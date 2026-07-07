import json
import os

import psycopg2
from dotenv import load_dotenv


load_dotenv()


OUTPUT_FILENAME = "test_olive_pure_strict_baseline_v1_field.geojson"


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        OUTPUT_FILENAME,
    )

    query = """
    WITH src AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            qc_version,
            qc_class,
            visual_qc_version,
            visual_label,
            urban_qc_version,
            artificial_flag,
            spectral_qc_version,
            spectral_flag,
            area_ha,
            compactness,
            n_observations,
            ndvi_median,
            evi_median,
            ndmi_median,
            bsi_median,
            geom
        FROM landcover_olive_pure_baseline_strict_seed_v1
        WHERE area_ha BETWEEN 0.8 AND 4.0
        ORDER BY
            CASE spectral_flag
                WHEN 'strong' THEN 1
                WHEN 'moderate' THEN 2
                ELSE 3
            END,
            compactness DESC,
            area_ha ASC
        LIMIT 1
    ),
    point_inside AS (
        SELECT
            *,
            ST_Transform(ST_PointOnSurface(geom), 3857) AS p3857
        FROM src
    ),
    square AS (
        SELECT
            *,
            ST_Transform(
                ST_MakeEnvelope(
                    ST_X(p3857) - 40,
                    ST_Y(p3857) - 40,
                    ST_X(p3857) + 40,
                    ST_Y(p3857) + 40,
                    3857
                ),
                4326
            ) AS square_geom
        FROM point_inside
    ),
    clipped AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            qc_version,
            qc_class,
            visual_qc_version,
            visual_label,
            urban_qc_version,
            artificial_flag,
            spectral_qc_version,
            spectral_flag,
            area_ha AS source_area_ha,
            compactness,
            n_observations,
            ndvi_median,
            evi_median,
            ndmi_median,
            bsi_median,
            ST_Multi(
                ST_CollectionExtract(
                    ST_MakeValid(
                        ST_Intersection(geom, square_geom)
                    ),
                    3
                )
            )::geometry(MultiPolygon, 4326) AS geom
        FROM square
    )
    SELECT
        id,
        subtype_id,
        source_layer_version,
        qc_version,
        qc_class,
        visual_qc_version,
        visual_label,
        urban_qc_version,
        artificial_flag,
        spectral_qc_version,
        spectral_flag,
        source_area_ha,
        compactness,
        n_observations,
        ndvi_median,
        evi_median,
        ndmi_median,
        bsi_median,
        ST_Area(geom::geography) / 10000.0 AS test_area_ha,
        ST_NPoints(geom) AS n_points,
        ST_AsGeoJSON(geom, 6)::text AS geojson
    FROM clipped
    WHERE geom IS NOT NULL
      AND NOT ST_IsEmpty(geom);
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()

    if not row:
        raise RuntimeError("Nessuna geometria trovata in landcover_olive_pure_baseline_strict_seed_v1.")

    (
        source_feature_id,
        subtype_id,
        source_layer_version,
        qc_version,
        qc_class,
        visual_qc_version,
        visual_label,
        urban_qc_version,
        artificial_flag,
        spectral_qc_version,
        spectral_flag,
        source_area_ha,
        compactness,
        n_observations,
        ndvi_median,
        evi_median,
        ndmi_median,
        bsi_median,
        test_area_ha,
        n_points,
        geojson_text,
    ) = row

    feature_collection = {
        "type": "FeatureCollection",
        "name": "test_olive_pure_strict_baseline_v1_field",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": "Test oliveto puro strict baseline v1",
                    "source": "landcover_olive_pure_baseline_strict_seed_v1",
                    "source_feature_id": source_feature_id,
                    "subtype_expected": subtype_id,
                    "source_layer_version": source_layer_version,
                    "qc_version": qc_version,
                    "qc_class": qc_class,
                    "visual_qc_version": visual_qc_version,
                    "visual_label": visual_label,
                    "urban_qc_version": urban_qc_version,
                    "artificial_flag": artificial_flag,
                    "spectral_qc_version": spectral_qc_version,
                    "spectral_flag": spectral_flag,
                    "source_area_ha": round(float(source_area_ha), 4),
                    "compactness": round(float(compactness), 4),
                    "n_observations": int(n_observations),
                    "ndvi_median": round(float(ndvi_median), 4),
                    "evi_median": round(float(evi_median), 4),
                    "ndmi_median": round(float(ndmi_median), 4),
                    "bsi_median": round(float(bsi_median), 4),
                    "test_area_ha": round(float(test_area_ha), 4),
                    "n_points": int(n_points),
                },
                "geometry": json.loads(geojson_text),
            }
        ],
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(feature_collection, f, ensure_ascii=False, indent=2)

    print(f"GeoJSON creato: {output_path}")
    print("Source: landcover_olive_pure_baseline_strict_seed_v1")
    print(f"Subtype: {subtype_id}")
    print(f"QC: {qc_version} | {qc_class}")
    print(f"Visual: {visual_qc_version} | {visual_label}")
    print(f"Urban: {urban_qc_version} | {artificial_flag}")
    print(f"Spectral: {spectral_qc_version} | {spectral_flag}")
    print(f"N observations: {n_observations}")
    print(f"NDVI: {float(ndvi_median):.4f}")
    print(f"EVI: {float(evi_median):.4f}")
    print(f"NDMI: {float(ndmi_median):.4f}")
    print(f"BSI: {float(bsi_median):.4f}")
    print(f"Area test: {float(test_area_ha):.4f} ha")


if __name__ == "__main__":
    main()