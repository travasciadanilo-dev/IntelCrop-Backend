import csv
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


load_dotenv()


VIEW_NAME = "olive_candidate_pool_v2_reliability_v3_diagnostic_v1"
OUT_PATH = Path("data/olive_reliability_v3_review_batches.csv")


BATCH_RULES = [
    ("very_high_top", "very_high", 40),
    ("high_top", "high", 40),
    ("compatible_top", "compatible", 40),
    ("compatible_borderline", "compatible", 40),
    ("low_borderline", "low", 40),
    ("low_bottom", "low", 40),
]


BASE_COLUMNS = [
    "area_id",
    "spatial_validation_zone",
    "candidate_origin",
    "experimental_reliability_score_v3",
    "experimental_reliability_class_v3",
    "experimental_reliability_label_v3",
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
    "reliability_model_version",
    "reliability_model_status",
]


def get_columns(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s;
            """,
            (VIEW_NAME,),
        )
        return {row[0] for row in cur.fetchall()}


def has_geom(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_name = 'geom'
            );
            """,
            (VIEW_NAME,),
        )
        return bool(cur.fetchone()[0])


def order_clause(batch_name):
    if batch_name in {"very_high_top", "high_top", "compatible_top"}:
        return "experimental_reliability_score_v3 DESC"

    if batch_name == "compatible_borderline":
        return "ABS(experimental_reliability_score_v3 - 0.7000) ASC"

    if batch_name == "low_borderline":
        return "ABS(experimental_reliability_score_v3 - 0.5000) ASC"

    if batch_name == "low_bottom":
        return "experimental_reliability_score_v3 ASC"

    return "experimental_reliability_score_v3 DESC"


def fetch_batch(conn, batch_name, reliability_class, limit_n, selected_columns, include_geom):
    select_parts = [
        "%s::text AS review_batch",
        *selected_columns,
    ]

    params = [batch_name, reliability_class, limit_n]

    if include_geom:
        select_parts.extend(
            [
                "ST_X(ST_PointOnSurface(geom)) AS review_lon",
                "ST_Y(ST_PointOnSurface(geom)) AS review_lat",
                "ST_AsGeoJSON(geom)::text AS geom_geojson",
            ]
        )

    sql = f"""
        SELECT
            {", ".join(select_parts)}
        FROM {VIEW_NAME}
        WHERE experimental_reliability_class_v3 = %s
        ORDER BY {order_clause(batch_name)}, area_id
        LIMIT %s;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with psycopg2.connect(database_url) as conn:
        columns = get_columns(conn)
        include_geom = has_geom(conn)

        selected_columns = [col for col in BASE_COLUMNS if col in columns]

        if "area_id" not in selected_columns:
            raise RuntimeError("area_id non trovato nella view diagnostica.")

        all_rows = []

        for batch_name, reliability_class, limit_n in BATCH_RULES:
            rows = fetch_batch(
                conn=conn,
                batch_name=batch_name,
                reliability_class=reliability_class,
                limit_n=limit_n,
                selected_columns=selected_columns,
                include_geom=include_geom,
            )
            all_rows.extend(rows)

        if not all_rows:
            raise RuntimeError("Nessuna riga esportata.")

        fieldnames = list(all_rows[0].keys())

        with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)

    print("")
    print("Export reliability v3 review batches")
    print("------------------------------------")
    print(f"view: {VIEW_NAME}")
    print(f"output: {OUT_PATH}")
    print(f"rows: {len(all_rows)}")
    print(f"geom_exported: {include_geom}")


if __name__ == "__main__":
    main()