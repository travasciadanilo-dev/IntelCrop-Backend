import argparse
import csv
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


load_dotenv()


DEFAULT_OUTPUT_GEOJSON = "data/olive_visual_review_sample_v2.geojson"
DEFAULT_OUTPUT_CSV = "data/olive_visual_review_sample_v2.csv"

DEFAULT_TOTAL_TARGET = 500
DEFAULT_RANDOM_SEED = 42

SAMPLE_VERSION = "olive_visual_review_sample_v2"
SOURCE_POOL_VERSION = "candidate_pool_v2_area_ge_0_5"


STRATA_CONFIG = [
    {
        "stratum": "north_added_unreviewed",
        "target": 160,
        "description": "Nord Calabria - nuove candidate non high-confidence",
        "predicate": lambda r: (
            r["spatial_validation_zone"] == "north_calabria"
            and not r["current_high_confidence_v2"]
            and r["visual_label"] is None
        ),
    },
    {
        "stratum": "north_high_confidence_unreviewed",
        "target": 50,
        "description": "Nord Calabria - aree high-confidence non revisionate",
        "predicate": lambda r: (
            r["spatial_validation_zone"] == "north_calabria"
            and r["current_high_confidence_v2"]
            and r["visual_label"] is None
        ),
    },
    {
        "stratum": "added_large_polygon_unreviewed",
        "target": 80,
        "description": "Poligoni grandi aggiunti dal candidate pool",
        "predicate": lambda r: (
            not r["current_high_confidence_v2"]
            and r["large_polygon_flag"]
            and r["visual_label"] is None
        ),
    },
    {
        "stratum": "added_small_candidate_unreviewed",
        "target": 60,
        "description": "Aree 0.5-1 ha aggiunte dal candidate pool",
        "predicate": lambda r: (
            not r["current_high_confidence_v2"]
            and r["small_candidate_flag"]
            and r["visual_label"] is None
        ),
    },
    {
        "stratum": "added_complex_boundary_unreviewed",
        "target": 60,
        "description": "Geometrie complesse aggiunte dal candidate pool",
        "predicate": lambda r: (
            not r["current_high_confidence_v2"]
            and r["complex_boundary_flag"]
            and r["visual_label"] is None
        ),
    },
    {
        "stratum": "central_south_added_unreviewed",
        "target": 60,
        "description": "Centro/Sud - nuove candidate non high-confidence",
        "predicate": lambda r: (
            r["spatial_validation_zone"] in {"central_calabria", "south_calabria"}
            and not r["current_high_confidence_v2"]
            and r["visual_label"] is None
        ),
    },
    {
        "stratum": "uncertain_recheck",
        "target": 30,
        "description": "Aree con precedente etichetta visuale incerta da ricontrollare",
        "predicate": lambda r: r["visual_label"] == "uncertain",
    },
]


def bool_value(value):
    if value is None:
        return None
    return bool(value)


def fetch_candidate_rows(cur):
    cur.execute(
        """
        SELECT
            area_id,
            source_geometry_id,
            subtype_id,
            source_layer_version,

            spatial_validation_zone,
            candidate_origin,

            area_ha_raw,
            perimeter_m_raw,
            compactness_raw,
            area_bin_raw,

            n_points,
            n_parts,
            n_points_bin,
            n_parts_bin,

            current_high_confidence_v2,
            identity_reference_match,
            strict_reference_match,

            large_polygon_flag,
            small_candidate_flag,
            complex_boundary_flag,
            geometric_prior_rank,

            visual_label,
            eval_class,
            eval_class_strict,

            review_priority_score,
            review_priority_reason
        FROM olive_candidate_pool_v2_review_priority
        ORDER BY area_id;
        """
    )

    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def sample_rows(rows, total_target, seed):
    rng = random.Random(seed)

    selected = []
    selected_ids = set()
    stratum_counts = {}

    for config in STRATA_CONFIG:
        pool = [row for row in rows if row["area_id"] not in selected_ids and config["predicate"](row)]

        rng.shuffle(pool)

        chosen = pool[: config["target"]]

        for row in chosen:
            row = dict(row)
            row["sample_stratum"] = config["stratum"]
            row["sample_stratum_description"] = config["description"]
            selected.append(row)
            selected_ids.add(row["area_id"])

        stratum_counts[config["stratum"]] = {
            "available": len(pool),
            "selected": len(chosen),
            "target": config["target"],
        }

    if len(selected) < total_target:
        remaining_pool = [
            row
            for row in rows
            if row["area_id"] not in selected_ids
            and row["visual_label"] is None
        ]

        rng.shuffle(remaining_pool)

        needed = total_target - len(selected)
        filler = remaining_pool[:needed]

        for row in filler:
            row = dict(row)
            row["sample_stratum"] = "balanced_random_fill"
            row["sample_stratum_description"] = "Riempimento casuale bilanciato dal candidate pool v2"
            selected.append(row)
            selected_ids.add(row["area_id"])

        stratum_counts["balanced_random_fill"] = {
            "available": len(remaining_pool),
            "selected": len(filler),
            "target": needed,
        }

    return selected[:total_target], stratum_counts


def fetch_geometries(cur, area_ids):
    geometries = {}

    if not area_ids:
        return geometries

    batch_size = 500

    for start in range(0, len(area_ids), batch_size):
        batch = area_ids[start : start + batch_size]

        cur.execute(
            """
            SELECT
                g.id::text AS area_id,
                ST_AsGeoJSON(g.geom)::json AS geometry_json,
                ST_X(ST_PointOnSurface(g.geom)) AS label_lon,
                ST_Y(ST_PointOnSurface(g.geom)) AS label_lat
            FROM landcover_subtype_geometries g
            WHERE g.id::text = ANY(%s);
            """,
            (batch,),
        )

        for area_id, geometry_json, label_lon, label_lat in cur.fetchall():
            geometries[area_id] = {
                "geometry": geometry_json,
                "label_lon": label_lon,
                "label_lat": label_lat,
            }

    return geometries


def write_geojson(rows, geometries, output_path, metadata):
    features = []

    for index, row in enumerate(rows, start=1):
        geom_info = geometries.get(row["area_id"])

        if not geom_info:
            continue

        properties = {
            "sample_id": f"OVR2_{index:04d}",
            "sample_version": SAMPLE_VERSION,
            "source_pool_version": SOURCE_POOL_VERSION,

            "area_id": row["area_id"],
            "source_geometry_id": str(row["source_geometry_id"]),

            "spatial_validation_zone": row["spatial_validation_zone"],
            "candidate_origin": row["candidate_origin"],

            "sample_stratum": row["sample_stratum"],
            "sample_stratum_description": row["sample_stratum_description"],

            "area_ha_raw": float(row["area_ha_raw"]) if row["area_ha_raw"] is not None else None,
            "perimeter_m_raw": float(row["perimeter_m_raw"]) if row["perimeter_m_raw"] is not None else None,
            "compactness_raw": float(row["compactness_raw"]) if row["compactness_raw"] is not None else None,
            "area_bin_raw": row["area_bin_raw"],

            "n_points": row["n_points"],
            "n_parts": row["n_parts"],
            "n_points_bin": row["n_points_bin"],
            "n_parts_bin": row["n_parts_bin"],

            "current_high_confidence_v2": bool_value(row["current_high_confidence_v2"]),
            "identity_reference_match": bool_value(row["identity_reference_match"]),
            "strict_reference_match": bool_value(row["strict_reference_match"]),

            "large_polygon_flag": bool_value(row["large_polygon_flag"]),
            "small_candidate_flag": bool_value(row["small_candidate_flag"]),
            "complex_boundary_flag": bool_value(row["complex_boundary_flag"]),
            "geometric_prior_rank": row["geometric_prior_rank"],

            "previous_visual_label": row["visual_label"],
            "previous_eval_class": row["eval_class"],
            "previous_eval_class_strict": row["eval_class_strict"],

            "review_priority_score": row["review_priority_score"],
            "review_priority_reason": row["review_priority_reason"],

            "label_lon": geom_info["label_lon"],
            "label_lat": geom_info["label_lat"],

            "visual_label_v2": "",
            "plantation_pattern_v2": "",
            "review_confidence_v2": "",
            "review_notes_v2": "",
        }

        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": geom_info["geometry"],
            }
        )

    geojson = {
        "type": "FeatureCollection",
        "name": SAMPLE_VERSION,
        "metadata": metadata,
        "features": features,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)


def write_csv(rows, geometries, output_path):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "sample_id",
        "sample_version",
        "source_pool_version",
        "area_id",
        "source_geometry_id",
        "spatial_validation_zone",
        "candidate_origin",
        "sample_stratum",
        "sample_stratum_description",
        "area_ha_raw",
        "area_bin_raw",
        "n_points",
        "n_parts",
        "n_points_bin",
        "n_parts_bin",
        "current_high_confidence_v2",
        "identity_reference_match",
        "strict_reference_match",
        "large_polygon_flag",
        "small_candidate_flag",
        "complex_boundary_flag",
        "previous_visual_label",
        "review_priority_score",
        "review_priority_reason",
        "label_lon",
        "label_lat",
        "visual_label_v2",
        "plantation_pattern_v2",
        "review_confidence_v2",
        "review_notes_v2",
    ]

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for index, row in enumerate(rows, start=1):
            geom_info = geometries.get(row["area_id"], {})

            writer.writerow(
                {
                    "sample_id": f"OVR2_{index:04d}",
                    "sample_version": SAMPLE_VERSION,
                    "source_pool_version": SOURCE_POOL_VERSION,
                    "area_id": row["area_id"],
                    "source_geometry_id": str(row["source_geometry_id"]),
                    "spatial_validation_zone": row["spatial_validation_zone"],
                    "candidate_origin": row["candidate_origin"],
                    "sample_stratum": row["sample_stratum"],
                    "sample_stratum_description": row["sample_stratum_description"],
                    "area_ha_raw": row["area_ha_raw"],
                    "area_bin_raw": row["area_bin_raw"],
                    "n_points": row["n_points"],
                    "n_parts": row["n_parts"],
                    "n_points_bin": row["n_points_bin"],
                    "n_parts_bin": row["n_parts_bin"],
                    "current_high_confidence_v2": row["current_high_confidence_v2"],
                    "identity_reference_match": row["identity_reference_match"],
                    "strict_reference_match": row["strict_reference_match"],
                    "large_polygon_flag": row["large_polygon_flag"],
                    "small_candidate_flag": row["small_candidate_flag"],
                    "complex_boundary_flag": row["complex_boundary_flag"],
                    "previous_visual_label": row["visual_label"],
                    "review_priority_score": row["review_priority_score"],
                    "review_priority_reason": row["review_priority_reason"],
                    "label_lon": geom_info.get("label_lon"),
                    "label_lat": geom_info.get("label_lat"),
                    "visual_label_v2": "",
                    "plantation_pattern_v2": "",
                    "review_confidence_v2": "",
                    "review_notes_v2": "",
                }
            )


def print_summary(rows, stratum_counts):
    print("")
    print("Visual review sample v2")
    print("-----------------------")
    print(f"selected_n: {len(rows)}")

    by_zone = {}
    by_stratum = {}

    for row in rows:
        zone = row["spatial_validation_zone"]
        stratum = row["sample_stratum"]

        by_zone[zone] = by_zone.get(zone, 0) + 1
        by_stratum[stratum] = by_stratum.get(stratum, 0) + 1

    print("")
    print("By zone")
    for zone, n in sorted(by_zone.items()):
        print(f"{zone}: {n}")

    print("")
    print("By stratum")
    for stratum, n in sorted(by_stratum.items()):
        counts = stratum_counts.get(stratum, {})
        available = counts.get("available")
        target = counts.get("target")
        print(f"{stratum}: selected={n} target={target} available={available}")


def main():
    parser = argparse.ArgumentParser(description="Export visual review sample v2.")
    parser.add_argument("--total-target", type=int, default=DEFAULT_TOTAL_TARGET)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--geojson", default=DEFAULT_OUTPUT_GEOJSON)
    parser.add_argument("--csv", default=DEFAULT_OUTPUT_CSV)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    created_at = datetime.now(timezone.utc).isoformat()

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            rows = fetch_candidate_rows(cur)
            selected, stratum_counts = sample_rows(rows, args.total_target, args.seed)
            geometries = fetch_geometries(cur, [row["area_id"] for row in selected])

    metadata = {
        "sample_version": SAMPLE_VERSION,
        "source_pool_version": SOURCE_POOL_VERSION,
        "created_at_utc": created_at,
        "total_target": args.total_target,
        "selected_n": len(selected),
        "random_seed": args.seed,
        "stratum_counts": stratum_counts,
        "label_schema": {
            "visual_label_v2": ["olive_like", "not_olive_like", "uncertain"],
            "plantation_pattern_v2": [
                "plantation_like",
                "mixed_or_sparse",
                "not_assessable",
            ],
            "review_confidence_v2": ["high", "medium", "low"],
        },
        "method_note": (
            "Campione stratificato da candidate_pool_v2. "
            "Oversampling su nord Calabria, nuove candidate non high-confidence, "
            "poligoni grandi, aree 0.5-1 ha, geometrie complesse e casi uncertain."
        ),
    }

    write_geojson(selected, geometries, args.geojson, metadata)
    write_csv(selected, geometries, args.csv)

    print_summary(selected, stratum_counts)

    print("")
    print(f"GeoJSON: {args.geojson}")
    print(f"CSV: {args.csv}")


if __name__ == "__main__":
    main()