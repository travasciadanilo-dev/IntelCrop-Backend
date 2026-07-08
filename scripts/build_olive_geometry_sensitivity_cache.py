import math
import os

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from pyproj import Transformer
from shapely import wkb
from shapely.ops import transform


load_dotenv()


FETCH_SIZE = 500
INSERT_SIZE = 500

TARGET_CRS_EPSG = 32633


def count_points(geom) -> int:
    if geom is None or geom.is_empty:
        return 0

    geom_type = geom.geom_type

    if geom_type == "Polygon":
        total = len(geom.exterior.coords)
        for interior in geom.interiors:
            total += len(interior.coords)
        return total

    if geom_type in {"MultiPolygon", "GeometryCollection"}:
        return sum(count_points(part) for part in geom.geoms)

    if hasattr(geom, "coords"):
        return len(geom.coords)

    if hasattr(geom, "geoms"):
        return sum(count_points(part) for part in geom.geoms)

    return 0


def count_parts(geom) -> int:
    if geom is None or geom.is_empty:
        return 0

    if hasattr(geom, "geoms"):
        return len(geom.geoms)

    return 1


def zone_from_lat(lat):
    if lat is None:
        return "unknown"

    if lat >= 39.5:
        return "north_calabria"

    if lat >= 38.6:
        return "central_calabria"

    return "south_calabria"


def n_points_bin(n_points):
    if n_points is None:
        return "unknown"
    if n_points <= 20:
        return "le_20"
    if n_points <= 50:
        return "21_50"
    if n_points <= 100:
        return "51_100"
    if n_points <= 250:
        return "101_250"
    return "gt_250"


def n_parts_bin(n_parts):
    if n_parts is None:
        return "unknown"
    if n_parts == 1:
        return "singlepart"
    if n_parts <= 3:
        return "multipart_2_3"
    if n_parts <= 10:
        return "multipart_4_10"
    return "multipart_gt_10"


def area_bin(area_ha):
    if area_ha is None:
        return "unknown"
    if area_ha < 0.15:
        return "lt_0_15_ha"
    if area_ha < 0.50:
        return "0_15_0_50_ha"
    if area_ha < 1.00:
        return "0_50_1_00_ha"
    if area_ha < 2.00:
        return "1_00_2_00_ha"
    if area_ha < 5.00:
        return "2_00_5_00_ha"
    return "ge_5_00_ha"


def looks_like_lonlat(geom):
    if geom is None or geom.is_empty:
        return False

    minx, miny, maxx, maxy = geom.bounds

    return (
        -180.0 <= minx <= 180.0
        and -180.0 <= maxx <= 180.0
        and -90.0 <= miny <= 90.0
        and -90.0 <= maxy <= 90.0
    )


def projected_geometry(geom, srid, transformer_4326_to_32633):
    if geom is None or geom.is_empty:
        return geom

    if srid == TARGET_CRS_EPSG:
        return geom

    if srid == 4326 or srid == 0 or looks_like_lonlat(geom):
        return transform(transformer_4326_to_32633.transform, geom)

    return geom


def compute_metric_geometry(geom, srid, transformer_4326_to_32633):
    if geom is None or geom.is_empty:
        return None, None, None

    metric_geom = projected_geometry(geom, srid, transformer_4326_to_32633)

    area_m2 = float(metric_geom.area)
    perimeter_m = float(metric_geom.length)

    area_ha = area_m2 / 10000.0

    if perimeter_m > 0:
        compactness = (4.0 * math.pi * area_m2) / (perimeter_m * perimeter_m)
    else:
        compactness = None

    return area_ha, perimeter_m, compactness


def flush_rows(cur, rows):
    if not rows:
        return

    execute_values(
        cur,
        """
        INSERT INTO olive_geometry_sensitivity_cache_v1 (
            area_id,
            source_geometry_id,
            subtype_id,
            source_layer_version,
            geom_is_valid,
            geom_is_empty,
            n_points,
            n_parts,
            approx_centroid_lat,
            spatial_validation_zone,
            n_points_bin,
            n_parts_bin,
            area_ha_raw,
            perimeter_m_raw,
            compactness_raw,
            area_bin_raw,
            candidate_pool_v2
        )
        VALUES %s
        ON CONFLICT (area_id) DO UPDATE
        SET
            source_geometry_id = EXCLUDED.source_geometry_id,
            subtype_id = EXCLUDED.subtype_id,
            source_layer_version = EXCLUDED.source_layer_version,
            geom_is_valid = EXCLUDED.geom_is_valid,
            geom_is_empty = EXCLUDED.geom_is_empty,
            n_points = EXCLUDED.n_points,
            n_parts = EXCLUDED.n_parts,
            approx_centroid_lat = EXCLUDED.approx_centroid_lat,
            spatial_validation_zone = EXCLUDED.spatial_validation_zone,
            n_points_bin = EXCLUDED.n_points_bin,
            n_parts_bin = EXCLUDED.n_parts_bin,
            area_ha_raw = EXCLUDED.area_ha_raw,
            perimeter_m_raw = EXCLUDED.perimeter_m_raw,
            compactness_raw = EXCLUDED.compactness_raw,
            area_bin_raw = EXCLUDED.area_bin_raw,
            candidate_pool_v2 = EXCLUDED.candidate_pool_v2;
        """,
        rows,
        page_size=INSERT_SIZE,
    )


def enrich_cache(cur):
    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1
        SET
            current_high_confidence_v2 = false,
            identity_reference_match = false,
            strict_reference_match = false,
            high_confidence_area_ha = NULL,
            high_confidence_compactness = NULL,
            high_confidence_qc_score = NULL,
            high_confidence_qc_class = NULL,
            visual_label = NULL,
            eval_class = NULL,
            eval_class_strict = NULL,
            binary_visual_label = NULL;
        """
    )

    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1 c
        SET
            current_high_confidence_v2 = true,
            high_confidence_area_ha = h.area_ha,
            high_confidence_compactness = h.compactness,
            high_confidence_qc_score = h.qc_score,
            high_confidence_qc_class = h.qc_class
        FROM landcover_olive_pure_high_confidence_v2 h
        WHERE c.area_id = h.id::text;
        """
    )

    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1 c
        SET identity_reference_match = true
        FROM landcover_olive_pure_baseline_v1 b
        WHERE c.area_id = b.id::text;
        """
    )

    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1 c
        SET strict_reference_match = true
        FROM landcover_olive_pure_baseline_strict_seed_v1 s
        WHERE c.area_id = s.id::text;
        """
    )

    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1 c
        SET
            visual_label = v.visual_label,
            eval_class = v.eval_class,
            binary_visual_label =
                CASE
                    WHEN v.visual_label = 'olive_like' THEN 1
                    WHEN v.visual_label = 'not_olive_like' THEN 0
                    ELSE NULL
                END
        FROM landcover_olive_visual_training_eval_v1 v
        WHERE c.area_id = v.id::text;
        """
    )

    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1 c
        SET eval_class_strict = s.eval_class_strict
        FROM landcover_olive_visual_training_eval_strict_v1 s
        WHERE c.area_id = s.id::text;
        """
    )

    cur.execute(
        """
        UPDATE olive_geometry_sensitivity_cache_v1
        SET candidate_pool_v2 =
            CASE
                WHEN geom_is_valid = true
                 AND geom_is_empty = false
                 AND area_ha_raw >= 0.5
                THEN true
                ELSE false
            END;
        """
    )

    cur.execute("ANALYZE olive_geometry_sensitivity_cache_v1;")


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    transformer_4326_to_32633 = Transformer.from_crs(
        "EPSG:4326",
        f"EPSG:{TARGET_CRS_EPSG}",
        always_xy=True,
    )

    inserted = 0

    write_conn = psycopg2.connect(database_url)
    read_conn = psycopg2.connect(database_url)

    try:
        with write_conn.cursor() as write_cur:
            write_cur.execute("TRUNCATE olive_geometry_sensitivity_cache_v1;")
            write_conn.commit()

        with read_conn.cursor(name="olive_raw_geometry_cursor") as read_cur:
            read_cur.itersize = FETCH_SIZE
            read_cur.execute(
                """
                SELECT
                    id::text AS area_id,
                    id::text AS source_geometry_id,
                    subtype_id,
                    source_layer_version,
                    ST_SRID(geom) AS srid,
                    ST_AsBinary(geom) AS geom_wkb
                FROM landcover_subtype_geometries
                WHERE subtype_id = 'olive_pure'
                  AND source_layer_version = 'cut_calabria_v1'
                ORDER BY id;
                """
            )

            write_rows = []

            with write_conn.cursor() as write_cur:
                for (
                    area_id,
                    source_geometry_id,
                    subtype_id,
                    source_layer_version,
                    srid,
                    geom_wkb,
                ) in read_cur:
                    geom = wkb.loads(bytes(geom_wkb))

                    if geom.is_empty:
                        approx_lat = None
                    else:
                        minx, miny, maxx, maxy = geom.bounds
                        approx_lat = (miny + maxy) / 2.0

                    points = count_points(geom)
                    parts = count_parts(geom)

                    area_ha, perimeter_m, compactness = compute_metric_geometry(
                        geom,
                        int(srid or 0),
                        transformer_4326_to_32633,
                    )

                    is_candidate_pool_v2 = (
                        bool(geom.is_valid)
                        and not bool(geom.is_empty)
                        and area_ha is not None
                        and area_ha >= 0.5
                    )

                    write_rows.append(
                        (
                            area_id,
                            source_geometry_id,
                            subtype_id,
                            source_layer_version,
                            bool(geom.is_valid),
                            bool(geom.is_empty),
                            points,
                            parts,
                            approx_lat,
                            zone_from_lat(approx_lat),
                            n_points_bin(points),
                            n_parts_bin(parts),
                            area_ha,
                            perimeter_m,
                            compactness,
                            area_bin(area_ha),
                            is_candidate_pool_v2,
                        )
                    )

                    if len(write_rows) >= INSERT_SIZE:
                        flush_rows(write_cur, write_rows)
                        inserted += len(write_rows)
                        write_conn.commit()
                        print(f"Inserted {inserted} geometries")
                        write_rows = []

                if write_rows:
                    flush_rows(write_cur, write_rows)
                    inserted += len(write_rows)
                    write_conn.commit()
                    print(f"Inserted {inserted} geometries")

                print("")
                print("Enriching cache with QC, visual labels and references...")
                enrich_cache(write_cur)
                write_conn.commit()

    finally:
        read_conn.close()
        write_conn.close()

    print("")
    print("Cache completata")
    print(f"Totale geometrie inserite: {inserted}")


if __name__ == "__main__":
    main()