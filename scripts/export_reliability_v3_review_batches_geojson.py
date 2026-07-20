import csv
import json
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


load_dotenv()


CSV_PATH = Path("data/olive_reliability_v3_review_batches.csv")
OUT_PATH = Path("data/olive_reliability_v3_review_batches.geojson")

GEOMETRY_TABLE = "landcover_subtype_geometries"


def fetch_geometries(conn, area_ids):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT
                id::text AS area_id,
                ST_AsGeoJSON(geom)::json AS geometry
            FROM {GEOMETRY_TABLE}
            WHERE id::text = ANY(%s);
            """,
            (area_ids,),
        )

        return {
            row["area_id"]: row["geometry"]
            for row in cur.fetchall()
        }


def clean_properties(row):
    properties = {}

    for key, value in row.items():
        if key == "geom_geojson":
            continue

        if value == "":
            properties[key] = None
            continue

        if key in {
            "experimental_reliability_score_v3",
            "area_ha_raw",
        }:
            try:
                properties[key] = float(value)
            except ValueError:
                properties[key] = value
            continue

        if key in {"n_points", "n_parts"}:
            try:
                properties[key] = int(float(value))
            except ValueError:
                properties[key] = value
            continue

        if value in {"True", "False"}:
            properties[key] = value == "True"
            continue

        properties[key] = value

    return properties


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    if not CSV_PATH.exists():
        raise RuntimeError(f"CSV non trovato: {CSV_PATH}")

    with CSV_PATH.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise RuntimeError("CSV vuoto.")

    area_ids = sorted({row["area_id"] for row in rows})

    with psycopg2.connect(database_url) as conn:
        geometries = fetch_geometries(conn, area_ids)

    features = []
    missing = []

    for row in rows:
        area_id = row["area_id"]
        geometry = geometries.get(area_id)

        if geometry is None:
            missing.append(area_id)
            continue

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": clean_properties(row),
            }
        )

    geojson = {
        "type": "FeatureCollection",
        "name": "olive_reliability_v3_review_batches",
        "features": features,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    print("")
    print("Export reliability v3 review batches GeoJSON")
    print("-------------------------------------------")
    print(f"csv: {CSV_PATH}")
    print(f"geometry_table: {GEOMETRY_TABLE}")
    print(f"output: {OUT_PATH}")
    print(f"csv_rows: {len(rows)}")
    print(f"geojson_features: {len(features)}")
    print(f"missing_geometries: {len(missing)}")

    if missing:
        print("missing_area_ids:")
        for area_id in missing[:20]:
            print(area_id)


if __name__ == "__main__":
    main()