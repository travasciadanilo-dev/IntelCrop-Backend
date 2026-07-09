import argparse
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


load_dotenv()


VIEW_NAME = "area_catalog_v1_diagnostic"
DEFAULT_PRIORITY_OUTPUT = Path("data/area_catalog_v1_diagnostic_priority.geojson")
DEFAULT_FULL_OUTPUT = Path("data/area_catalog_v1_diagnostic_full.geojson")


PROPERTY_COLUMNS = [
    "area_id",
    "region_code",
    "region_label",
    "technical_subtype_id",
    "technical_subtype_label",
    "area_type",
    "area_type_label",
    "spatial_validation_zone",
    "candidate_origin",
    "area_ha",
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
    "reliability_score",
    "reliability_class",
    "reliability_label",
    "reliability_rank",
    "catalog_priority_candidate",
    "catalog_status_label",
    "reliability_model_version",
    "reliability_model_status",
    "catalog_version",
    "catalog_status",
    "centroid_lon",
    "centroid_lat",
    "bbox_min_lon",
    "bbox_min_lat",
    "bbox_max_lon",
    "bbox_max_lat",
]


def json_safe(value):
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return value


def build_query(priority_only):
    where_clause = ""

    if priority_only:
        where_clause = "WHERE catalog_priority_candidate IS TRUE"

    columns_sql = ",\n                ".join(PROPERTY_COLUMNS)

    return f"""
        SELECT
            {columns_sql},
            ST_AsGeoJSON(geom, 6)::text AS geometry_geojson
        FROM {VIEW_NAME}
        {where_clause}
        ORDER BY reliability_rank DESC, reliability_score DESC, area_id;
    """


def export_geojson(conn, output_path, priority_only):
    sql = build_query(priority_only)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    n = 0

    with conn.cursor(name="catalog_export_cursor", cursor_factory=RealDictCursor) as cur:
        cur.itersize = 1000
        cur.execute(sql)

        with output_path.open("w", encoding="utf-8") as f:
            f.write('{"type":"FeatureCollection","name":"')
            f.write(output_path.stem)
            f.write('","features":[')

            first = True

            for row in cur:
                geometry = json.loads(row.pop("geometry_geojson"))

                properties = {
                    key: json_safe(row.get(key))
                    for key in PROPERTY_COLUMNS
                }

                feature = {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties,
                }

                if not first:
                    f.write(",")

                json.dump(feature, f, ensure_ascii=False)
                first = False
                n += 1

            f.write("]}")

    return n


def main():
    parser = argparse.ArgumentParser(
        description="Export area_catalog_v1_diagnostic as GeoJSON."
    )

    parser.add_argument(
        "--full",
        action="store_true",
        help="Export all catalog rows, not only high/very_high diagnostic priority candidates.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output GeoJSON path.",
    )

    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    priority_only = not args.full

    if args.output:
        output_path = args.output
    elif priority_only:
        output_path = DEFAULT_PRIORITY_OUTPUT
    else:
        output_path = DEFAULT_FULL_OUTPUT

    with psycopg2.connect(database_url) as conn:
        n = export_geojson(
            conn=conn,
            output_path=output_path,
            priority_only=priority_only,
        )

    print("")
    print("Export area catalog v1 diagnostic GeoJSON")
    print("----------------------------------------")
    print(f"view: {VIEW_NAME}")
    print(f"output: {output_path}")
    print(f"priority_only: {priority_only}")
    print(f"features: {n}")


if __name__ == "__main__":
    main()