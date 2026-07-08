import argparse
import csv
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values


load_dotenv()


DEFAULT_CSV = "data/olive_visual_review_sample_v2.csv"

VALID_VISUAL_LABELS = {"olive_like", "not_olive_like", "uncertain"}
VALID_PLANTATION_PATTERNS = {"plantation_like", "mixed_or_sparse", "not_assessable"}
VALID_REVIEW_CONFIDENCE = {"high", "medium", "low"}


REQUIRED_COLUMNS = [
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


def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    return value


def parse_bool(value):
    value = clean_text(value)

    if value is None:
        return None

    value = value.lower()

    if value in {"true", "t", "1", "yes", "y"}:
        return True

    if value in {"false", "f", "0", "no", "n"}:
        return False

    raise ValueError(f"Valore boolean non valido: {value}")


def parse_int(value):
    value = clean_text(value)

    if value is None:
        return None

    return int(float(value))


def parse_float(value):
    value = clean_text(value)

    if value is None:
        return None

    return float(value)


def validate_choice(row, column, valid_values, row_number):
    value = clean_text(row.get(column))

    if value is None:
        return None

    value = value.lower()

    if value not in valid_values:
        raise ValueError(
            f"Riga {row_number}: valore non valido per {column}: '{value}'. "
            f"Valori ammessi: {sorted(valid_values)}"
        )

    return value


def is_complete(visual_label, plantation_pattern, review_confidence):
    return (
        visual_label is not None
        and plantation_pattern is not None
        and review_confidence is not None
    )


def is_training_eligible(visual_label, review_confidence):
    return (
        visual_label in {"olive_like", "not_olive_like"}
        and review_confidence in {"high", "medium"}
    )


def read_csv_rows(path, strict=False):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        missing = [col for col in REQUIRED_COLUMNS if col not in fieldnames]

        if missing:
            raise RuntimeError(f"Colonne mancanti nel CSV: {missing}")

        parsed_rows = []

        for row_number, row in enumerate(reader, start=2):
            visual_label = validate_choice(
                row,
                "visual_label_v2",
                VALID_VISUAL_LABELS,
                row_number,
            )
            plantation_pattern = validate_choice(
                row,
                "plantation_pattern_v2",
                VALID_PLANTATION_PATTERNS,
                row_number,
            )
            review_confidence = validate_choice(
                row,
                "review_confidence_v2",
                VALID_REVIEW_CONFIDENCE,
                row_number,
            )

            complete = is_complete(visual_label, plantation_pattern, review_confidence)

            if strict and not complete:
                raise RuntimeError(
                    f"Riga {row_number}: revisione incompleta. "
                    "Compilare visual_label_v2, plantation_pattern_v2 e review_confidence_v2."
                )

            parsed_rows.append(
                {
                    "sample_id": clean_text(row["sample_id"]),
                    "sample_version": clean_text(row["sample_version"]),
                    "source_pool_version": clean_text(row["source_pool_version"]),
                    "area_id": clean_text(row["area_id"]),
                    "source_geometry_id": clean_text(row["source_geometry_id"]),
                    "spatial_validation_zone": clean_text(row["spatial_validation_zone"]),
                    "candidate_origin": clean_text(row["candidate_origin"]),
                    "sample_stratum": clean_text(row["sample_stratum"]),
                    "sample_stratum_description": clean_text(row["sample_stratum_description"]),
                    "area_ha_raw": parse_float(row["area_ha_raw"]),
                    "area_bin_raw": clean_text(row["area_bin_raw"]),
                    "n_points": parse_int(row["n_points"]),
                    "n_parts": parse_int(row["n_parts"]),
                    "n_points_bin": clean_text(row["n_points_bin"]),
                    "n_parts_bin": clean_text(row["n_parts_bin"]),
                    "current_high_confidence_v2": parse_bool(row["current_high_confidence_v2"]),
                    "identity_reference_match": parse_bool(row["identity_reference_match"]),
                    "strict_reference_match": parse_bool(row["strict_reference_match"]),
                    "large_polygon_flag": parse_bool(row["large_polygon_flag"]),
                    "small_candidate_flag": parse_bool(row["small_candidate_flag"]),
                    "complex_boundary_flag": parse_bool(row["complex_boundary_flag"]),
                    "previous_visual_label": clean_text(row["previous_visual_label"]),
                    "review_priority_score": parse_int(row["review_priority_score"]),
                    "review_priority_reason": clean_text(row["review_priority_reason"]),
                    "label_lon": parse_float(row["label_lon"]),
                    "label_lat": parse_float(row["label_lat"]),
                    "visual_label_v2": visual_label,
                    "plantation_pattern_v2": plantation_pattern,
                    "review_confidence_v2": review_confidence,
                    "review_notes_v2": clean_text(row["review_notes_v2"]),
                    "is_complete": complete,
                    "is_training_eligible": is_training_eligible(
                        visual_label,
                        review_confidence,
                    ),
                }
            )

    return parsed_rows


def upsert_rows(conn, rows, source_file):
    values = [
        (
            row["sample_id"],
            row["sample_version"],
            row["source_pool_version"],
            row["area_id"],
            row["source_geometry_id"],
            row["spatial_validation_zone"],
            row["candidate_origin"],
            row["sample_stratum"],
            row["sample_stratum_description"],
            row["area_ha_raw"],
            row["area_bin_raw"],
            row["n_points"],
            row["n_parts"],
            row["n_points_bin"],
            row["n_parts_bin"],
            row["current_high_confidence_v2"],
            row["identity_reference_match"],
            row["strict_reference_match"],
            row["large_polygon_flag"],
            row["small_candidate_flag"],
            row["complex_boundary_flag"],
            row["previous_visual_label"],
            row["review_priority_score"],
            row["review_priority_reason"],
            row["label_lon"],
            row["label_lat"],
            row["visual_label_v2"],
            row["plantation_pattern_v2"],
            row["review_confidence_v2"],
            row["review_notes_v2"],
            row["is_complete"],
            row["is_training_eligible"],
            source_file,
        )
        for row in rows
    ]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO olive_visual_review_sample_v2_labels (
                sample_id,
                sample_version,
                source_pool_version,
                area_id,
                source_geometry_id,
                spatial_validation_zone,
                candidate_origin,
                sample_stratum,
                sample_stratum_description,
                area_ha_raw,
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
                previous_visual_label,
                review_priority_score,
                review_priority_reason,
                label_lon,
                label_lat,
                visual_label_v2,
                plantation_pattern_v2,
                review_confidence_v2,
                review_notes_v2,
                is_complete,
                is_training_eligible,
                source_file
            )
            VALUES %s
            ON CONFLICT (sample_id) DO UPDATE
            SET
                sample_version = EXCLUDED.sample_version,
                source_pool_version = EXCLUDED.source_pool_version,
                area_id = EXCLUDED.area_id,
                source_geometry_id = EXCLUDED.source_geometry_id,
                spatial_validation_zone = EXCLUDED.spatial_validation_zone,
                candidate_origin = EXCLUDED.candidate_origin,
                sample_stratum = EXCLUDED.sample_stratum,
                sample_stratum_description = EXCLUDED.sample_stratum_description,
                area_ha_raw = EXCLUDED.area_ha_raw,
                area_bin_raw = EXCLUDED.area_bin_raw,
                n_points = EXCLUDED.n_points,
                n_parts = EXCLUDED.n_parts,
                n_points_bin = EXCLUDED.n_points_bin,
                n_parts_bin = EXCLUDED.n_parts_bin,
                current_high_confidence_v2 = EXCLUDED.current_high_confidence_v2,
                identity_reference_match = EXCLUDED.identity_reference_match,
                strict_reference_match = EXCLUDED.strict_reference_match,
                large_polygon_flag = EXCLUDED.large_polygon_flag,
                small_candidate_flag = EXCLUDED.small_candidate_flag,
                complex_boundary_flag = EXCLUDED.complex_boundary_flag,
                previous_visual_label = EXCLUDED.previous_visual_label,
                review_priority_score = EXCLUDED.review_priority_score,
                review_priority_reason = EXCLUDED.review_priority_reason,
                label_lon = EXCLUDED.label_lon,
                label_lat = EXCLUDED.label_lat,
                visual_label_v2 = EXCLUDED.visual_label_v2,
                plantation_pattern_v2 = EXCLUDED.plantation_pattern_v2,
                review_confidence_v2 = EXCLUDED.review_confidence_v2,
                review_notes_v2 = EXCLUDED.review_notes_v2,
                is_complete = EXCLUDED.is_complete,
                is_training_eligible = EXCLUDED.is_training_eligible,
                source_file = EXCLUDED.source_file,
                imported_at = now();
            """,
            values,
            page_size=500,
        )

    conn.commit()


def fetch_summary(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM olive_visual_review_sample_v2_summary_v1;
            """
        )
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, cur.fetchone()))


def fetch_by_zone(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                spatial_validation_zone,
                COUNT(*) AS n,
                COUNT(*) FILTER (WHERE is_complete = true) AS n_complete,
                COUNT(*) FILTER (WHERE is_training_eligible = true) AS n_training,
                COUNT(*) FILTER (WHERE visual_label_v2 = 'olive_like') AS n_olive_like,
                COUNT(*) FILTER (WHERE visual_label_v2 = 'not_olive_like') AS n_not_olive_like,
                COUNT(*) FILTER (WHERE visual_label_v2 = 'uncertain') AS n_uncertain
            FROM olive_visual_review_sample_v2_labels
            GROUP BY spatial_validation_zone
            ORDER BY spatial_validation_zone;
            """
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def print_summary(rows, summary, by_zone):
    print("")
    print("Import visual review sample v2")
    print("------------------------------")
    print(f"rows_read: {len(rows)}")
    print(f"rows_complete: {sum(1 for r in rows if r['is_complete'])}")
    print(f"rows_training_eligible: {sum(1 for r in rows if r['is_training_eligible'])}")

    print("")
    print("DB summary")
    for key, value in summary.items():
        print(f"{key}: {value}")

    print("")
    print("By zone")
    for row in by_zone:
        print(
            f"{row['spatial_validation_zone']}: "
            f"n={row['n']} complete={row['n_complete']} "
            f"training={row['n_training']} olive_like={row['n_olive_like']} "
            f"not_olive_like={row['n_not_olive_like']} uncertain={row['n_uncertain']}"
        )

    if summary["n_training_eligible"] == 0:
        print("")
        print("Nota: nessuna riga training-eligible. È normale se il CSV non è ancora revisionato.")


def main():
    parser = argparse.ArgumentParser(description="Import visual review sample v2 labels.")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.csv)

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV non trovato: {csv_path}")

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    rows = read_csv_rows(csv_path, strict=args.strict)

    with psycopg2.connect(database_url) as conn:
        upsert_rows(conn, rows, str(csv_path))
        summary = fetch_summary(conn)
        by_zone = fetch_by_zone(conn)

    print_summary(rows, summary, by_zone)


if __name__ == "__main__":
    main()