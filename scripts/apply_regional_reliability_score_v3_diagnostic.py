import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, execute_values


load_dotenv()


MODEL_VERSION = "regional_reliability_score_exp_v3"
SOURCE_TABLE = "olive_candidate_pool_v2_review_priority"
SCORE_TABLE = "regional_reliability_scores_v3_diagnostic"
SCORE_VIEW = "olive_candidate_pool_v2_reliability_v3_diagnostic_v1"

SCORE_TYPE = "experimental_score_v3"
VIEW_VERSION = "olive_candidate_pool_v2_reliability_v3_diagnostic_v1"


LABELS_IT = {
    "low": "Bassa affidabilità diagnostica",
    "compatible": "Compatibile diagnostico",
    "high": "Alta affidabilità diagnostica",
    "very_high": "Affidabilità molto alta diagnostica",
}


def clean_number(value):
    if value is None:
        return None

    if isinstance(value, Decimal):
        return float(value)

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value):
        return None

    return value


def safe_log1p(value):
    value = clean_number(value)

    if value is None or value < 0:
        return None

    return math.log1p(value)


def bool_to_float(value):
    if value is None:
        return 0.0

    return 1.0 if bool(value) else 0.0


def stable_sigmoid(value):
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)

    z = math.exp(value)
    return z / (1.0 + z)


def first_existing(row, names):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]

    return None


def classify_score(score, thresholds):
    for threshold in thresholds:
        score_min = float(threshold["score_min"])
        score_max = float(threshold["score_max"])
        class_label = threshold["class_label"]

        if class_label == "very_high":
            if score >= score_min and score <= score_max:
                return class_label

        if score >= score_min and score < score_max:
            return class_label

    if score < 0.5:
        return "low"

    if score < 0.7:
        return "compatible"

    if score < 0.85:
        return "high"

    return "very_high"


def fetch_model_run(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                model_version,
                model_intercept,
                status,
                limitations,
                metadata
            FROM regional_reliability_model_runs
            WHERE model_version = %s;
            """,
            (MODEL_VERSION,),
        )

        row = cur.fetchone()

    if not row:
        raise RuntimeError(f"Model run non trovato: {MODEL_VERSION}")

    metadata = row["metadata"]

    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    if "feature_state" not in metadata:
        raise RuntimeError("metadata.feature_state non trovato nel model run.")

    if "thresholds" not in metadata:
        raise RuntimeError("metadata.thresholds non trovato nel model run.")

    return {
        "model_version": row["model_version"],
        "model_intercept": float(row["model_intercept"]),
        "status": row["status"],
        "limitations": row["limitations"],
        "metadata": metadata,
        "feature_state": metadata["feature_state"],
        "thresholds": metadata["thresholds"],
    }


def fetch_coefficients(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                feature_name,
                coefficient_value
            FROM regional_reliability_model_coefficients
            WHERE model_version = %s;
            """,
            (MODEL_VERSION,),
        )

        rows = cur.fetchall()

    if not rows:
        raise RuntimeError(f"Coefficienti non trovati per {MODEL_VERSION}")

    return {
        row["feature_name"]: float(row["coefficient_value"])
        for row in rows
    }


def fetch_candidates(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT *
            FROM {SOURCE_TABLE}
            ORDER BY area_id;
        """)

        return [dict(row) for row in cur.fetchall()]


def derive_raw_features(row):
    area_ha_raw = clean_number(first_existing(row, ["area_ha_raw", "area_ha"]))
    perimeter_m_raw = clean_number(first_existing(row, ["perimeter_m_raw", "perimeter_m"]))
    n_points = clean_number(first_existing(row, ["n_points", "n_vertices"]))
    n_parts = clean_number(first_existing(row, ["n_parts"]))
    compactness_raw = clean_number(first_existing(row, ["compactness_raw", "compactness"]))
    geometric_prior_rank = clean_number(first_existing(row, ["geometric_prior_rank"]))

    return {
        "area_ha_raw": area_ha_raw,
        "log_area_ha_raw": safe_log1p(area_ha_raw),
        "perimeter_m_raw": perimeter_m_raw,
        "log_perimeter_m_raw": safe_log1p(perimeter_m_raw),
        "compactness_raw": compactness_raw,
        "n_points": n_points,
        "log_n_points": safe_log1p(n_points),
        "n_parts": n_parts,
        "geometric_prior_rank": geometric_prior_rank,

        "current_high_confidence_v2": first_existing(row, ["current_high_confidence_v2"]),
        "identity_reference_match": first_existing(row, ["identity_reference_match"]),
        "strict_reference_match": first_existing(row, ["strict_reference_match"]),
        "large_polygon_flag": first_existing(row, ["large_polygon_flag"]),
        "small_candidate_flag": first_existing(row, ["small_candidate_flag"]),
        "complex_boundary_flag": first_existing(row, ["complex_boundary_flag"]),

        "spatial_validation_zone": first_existing(row, ["spatial_validation_zone"]),
        "candidate_origin": first_existing(row, ["candidate_origin"]),
        "area_bin_raw": first_existing(row, ["area_bin_raw"]),
        "n_points_bin": first_existing(row, ["n_points_bin"]),
        "n_parts_bin": first_existing(row, ["n_parts_bin"]),
    }


def score_row(row, model_run, coefficients):
    state = model_run["feature_state"]
    raw = derive_raw_features(row)

    eta = model_run["model_intercept"]
    warnings = []

    for name in state["numeric_features"]:
        feature_name = f"num__{name}_z"
        coefficient = coefficients.get(feature_name)

        if coefficient is None:
            continue

        value = clean_number(raw.get(name))

        if value is None:
            value = float(state["numeric"][name]["median"])
            warnings.append(f"missing_numeric:{name}")

        mean = float(state["numeric"][name]["mean"])
        std = float(state["numeric"][name]["std"]) or 1.0

        eta += coefficient * ((value - mean) / std)

    for name in state["boolean_features"]:
        feature_name = f"bool__{name}"
        coefficient = coefficients.get(feature_name)

        if coefficient is None:
            continue

        eta += coefficient * bool_to_float(raw.get(name))

    for name in state["categorical_features"]:
        value = raw.get(name)

        if value is None:
            value = "missing"
            warnings.append(f"missing_category:{name}")
        else:
            value = str(value)

        feature_name = f"cat__{name}={value}"

        coefficient = coefficients.get(feature_name)

        if coefficient is not None:
            eta += coefficient
        else:
            known_values = set(state["categories"].get(name, []))
            if value not in known_values:
                warnings.append(f"unseen_category:{name}={value}")

    score = stable_sigmoid(eta)
    class_label = classify_score(score, model_run["thresholds"])

    return {
        "score": score,
        "class_label": class_label,
        "label_it": LABELS_IT.get(class_label, class_label),
        "warnings": sorted(set(warnings)),
    }


def ensure_output_objects(conn):
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCORE_TABLE} (
                area_id text NOT NULL,
                model_version text NOT NULL,
                score_type text NOT NULL,
                view_version text NOT NULL,

                experimental_reliability_score_v3 double precision NOT NULL,
                experimental_reliability_class_v3 text NOT NULL,
                experimental_reliability_label_v3 text NOT NULL,

                model_status text NOT NULL,
                limitations text NOT NULL,
                feature_warning text,

                created_at timestamptz NOT NULL DEFAULT now(),

                PRIMARY KEY (area_id, model_version)
            );
        """)

        cur.execute(f"""
            DELETE FROM {SCORE_TABLE}
            WHERE model_version = %s;
        """, (MODEL_VERSION,))

        cur.execute(f"""
            DROP VIEW IF EXISTS {SCORE_VIEW};

            CREATE VIEW {SCORE_VIEW} AS
            SELECT
                p.*,

                s.score_type,
                s.view_version,
                s.experimental_reliability_score_v3,
                s.experimental_reliability_class_v3,
                s.experimental_reliability_label_v3,
                s.model_version AS reliability_model_version,
                s.model_status AS reliability_model_status,
                s.limitations AS reliability_model_limitations,
                s.feature_warning AS reliability_feature_warning,
                s.created_at AS reliability_score_created_at

            FROM {SOURCE_TABLE} p
            JOIN {SCORE_TABLE} s
              ON s.area_id = p.area_id::text
             AND s.model_version = '{MODEL_VERSION}';
        """)

    conn.commit()


def insert_scores(conn, rows):
    values = [
        (
            row["area_id"],
            row["model_version"],
            row["score_type"],
            row["view_version"],
            row["score"],
            row["class_label"],
            row["label_it"],
            row["model_status"],
            row["limitations"],
            row["feature_warning"],
        )
        for row in rows
    ]

    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO {SCORE_TABLE} (
                area_id,
                model_version,
                score_type,
                view_version,
                experimental_reliability_score_v3,
                experimental_reliability_class_v3,
                experimental_reliability_label_v3,
                model_status,
                limitations,
                feature_warning
            )
            VALUES %s;
            """,
            values,
            page_size=1000,
        )

    conn.commit()


def print_summary(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT
                experimental_reliability_class_v3 AS reliability_class,
                COUNT(*) AS n,
                ROUND(AVG(experimental_reliability_score_v3)::numeric, 4) AS mean_score,
                ROUND(MIN(experimental_reliability_score_v3)::numeric, 4) AS min_score,
                ROUND(MAX(experimental_reliability_score_v3)::numeric, 4) AS max_score
            FROM {SCORE_TABLE}
            WHERE model_version = %s
            GROUP BY experimental_reliability_class_v3
            ORDER BY
                CASE experimental_reliability_class_v3
                    WHEN 'low' THEN 1
                    WHEN 'compatible' THEN 2
                    WHEN 'high' THEN 3
                    WHEN 'very_high' THEN 4
                    ELSE 9
                END;
        """, (MODEL_VERSION,))

        class_rows = cur.fetchall()

        cur.execute(f"""
            SELECT
                COALESCE(spatial_validation_zone, 'missing') AS spatial_validation_zone,
                experimental_reliability_class_v3 AS reliability_class,
                COUNT(*) AS n
            FROM {SCORE_VIEW}
            GROUP BY spatial_validation_zone, experimental_reliability_class_v3
            ORDER BY spatial_validation_zone, reliability_class;
        """)

        zone_rows = cur.fetchall()

        cur.execute(f"""
            SELECT
                COALESCE(candidate_origin, 'missing') AS candidate_origin,
                experimental_reliability_class_v3 AS reliability_class,
                COUNT(*) AS n
            FROM {SCORE_VIEW}
            GROUP BY candidate_origin, experimental_reliability_class_v3
            ORDER BY candidate_origin, reliability_class;
        """)

        origin_rows = cur.fetchall()

        cur.execute(f"""
            SELECT
                COUNT(*) AS n_rows,
                COUNT(*) FILTER (WHERE feature_warning IS NOT NULL) AS n_with_warning
            FROM {SCORE_TABLE}
            WHERE model_version = %s;
        """, (MODEL_VERSION,))

        warning_row = cur.fetchone()

    print("")
    print("Regional reliability score v3 diagnostic application")
    print("----------------------------------------------------")
    print(f"model_version: {MODEL_VERSION}")
    print(f"source_table: {SOURCE_TABLE}")
    print(f"score_table: {SCORE_TABLE}")
    print(f"score_view: {SCORE_VIEW}")

    print("")
    print("By reliability class")
    for row in class_rows:
        print(
            f"{row['reliability_class']}: "
            f"n={row['n']} "
            f"mean={row['mean_score']} "
            f"min={row['min_score']} "
            f"max={row['max_score']}"
        )

    print("")
    print("By zone")
    for row in zone_rows:
        print(
            f"{row['spatial_validation_zone']} | "
            f"{row['reliability_class']}: {row['n']}"
        )

    print("")
    print("By candidate origin")
    for row in origin_rows:
        print(
            f"{row['candidate_origin']} | "
            f"{row['reliability_class']}: {row['n']}"
        )

    print("")
    print(
        f"feature_warnings: "
        f"{warning_row['n_with_warning']} / {warning_row['n_rows']}"
    )


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    with psycopg2.connect(database_url) as conn:
        model_run = fetch_model_run(conn)
        coefficients = fetch_coefficients(conn)
        candidates = fetch_candidates(conn)

        if not candidates:
            raise RuntimeError(f"Nessuna candidata trovata in {SOURCE_TABLE}")

        ensure_output_objects(conn)

        scored_rows = []

        for row in candidates:
            scored = score_row(row, model_run, coefficients)

            scored_rows.append(
                {
                    "area_id": str(row["area_id"]),
                    "model_version": MODEL_VERSION,
                    "score_type": SCORE_TYPE,
                    "view_version": VIEW_VERSION,
                    "score": scored["score"],
                    "class_label": scored["class_label"],
                    "label_it": scored["label_it"],
                    "model_status": model_run["status"],
                    "limitations": model_run["limitations"],
                    "feature_warning": ";".join(scored["warnings"]) if scored["warnings"] else None,
                }
            )

        insert_scores(conn, scored_rows)
        print_summary(conn)


if __name__ == "__main__":
    main()