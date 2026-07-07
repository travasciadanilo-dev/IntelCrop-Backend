import os
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv


load_dotenv()


MIN_TOTAL_TRAINING = 150
MIN_POSITIVE = 50
MIN_NEGATIVE = 30
MIN_REVIEWED_PER_ZONE = 10
MIN_STRICT_PER_ZONE = 5


def fetch_all(cur, query):
    cur.execute(query)
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def print_section(title):
    print("")
    print(title)
    print("-" * len(title))


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            summary = fetch_all(
                cur,
                """
                SELECT *
                FROM area_feature_matrix_summary_v1;
                """,
            )[0]

            training_by_label = fetch_all(
                cur,
                """
                SELECT
                    visual_label,
                    COUNT(*) AS n
                FROM area_feature_matrix_training_v1
                GROUP BY visual_label
                ORDER BY visual_label;
                """,
            )

            by_status = fetch_all(
                cur,
                """
                SELECT
                    training_reference_status,
                    COUNT(*) AS n_areas,
                    ROUND(SUM(area_ha)::numeric, 2) AS total_area_ha
                FROM area_feature_matrix_v1
                GROUP BY training_reference_status
                ORDER BY n_areas DESC;
                """,
            )

            by_zone = fetch_all(
                cur,
                """
                SELECT
                    spatial_validation_zone,
                    COUNT(*) AS n_areas,
                    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
                    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_olive_like,
                    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_not_olive_like,
                    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference,
                    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference
                FROM area_feature_matrix_v1
                GROUP BY spatial_validation_zone
                ORDER BY spatial_validation_zone;
                """,
            )

            feature_missing = fetch_all(
                cur,
                """
                SELECT
                    COUNT(*) AS n_total,
                    COUNT(*) FILTER (WHERE compactness IS NULL) AS compactness_missing,
                    COUNT(*) FILTER (WHERE qc_score IS NULL) AS qc_score_missing,
                    COUNT(*) FILTER (WHERE artificial_flag IS NULL) AS artificial_flag_missing,
                    COUNT(*) FILTER (WHERE spectral_flag IS NULL) AS spectral_flag_missing,
                    COUNT(*) FILTER (WHERE n_observations IS NULL) AS n_observations_missing,
                    COUNT(*) FILTER (WHERE ndvi_median IS NULL) AS ndvi_missing,
                    COUNT(*) FILTER (WHERE evi_median IS NULL) AS evi_missing,
                    COUNT(*) FILTER (WHERE ndmi_median IS NULL) AS ndmi_missing,
                    COUNT(*) FILTER (WHERE bsi_median IS NULL) AS bsi_missing
                FROM area_feature_matrix_v1;
                """,
            )[0]

    n_training = int(summary["n_visual_olive_like"] or 0) + int(summary["n_visual_not_olive_like"] or 0)
    n_positive = int(summary["n_visual_olive_like"] or 0)
    n_negative = int(summary["n_visual_not_olive_like"] or 0)
    n_uncertain = int(summary["n_visual_uncertain"] or 0)

    checks = []

    checks.append(
        (
            "training_total",
            n_training,
            MIN_TOTAL_TRAINING,
            n_training >= MIN_TOTAL_TRAINING,
            "numero totale di etichette valide olive_like/not_olive_like",
        )
    )

    checks.append(
        (
            "positive_labels",
            n_positive,
            MIN_POSITIVE,
            n_positive >= MIN_POSITIVE,
            "campione positivo olive_like",
        )
    )

    checks.append(
        (
            "negative_labels",
            n_negative,
            MIN_NEGATIVE,
            n_negative >= MIN_NEGATIVE,
            "campione negativo not_olive_like",
        )
    )

    zone_checks = []
    for row in by_zone:
        zone = row["spatial_validation_zone"]
        n_reviewed = int(row["n_visual_reviewed"] or 0)
        n_strict = int(row["n_strict_reference"] or 0)

        zone_checks.append(
            (
                f"{zone}_reviewed",
                n_reviewed,
                MIN_REVIEWED_PER_ZONE,
                n_reviewed >= MIN_REVIEWED_PER_ZONE,
            )
        )
        zone_checks.append(
            (
                f"{zone}_strict",
                n_strict,
                MIN_STRICT_PER_ZONE,
                n_strict >= MIN_STRICT_PER_ZONE,
            )
        )

    print_section("Area feature matrix summary")
    for key, value in summary.items():
        print(f"{key}: {value}")

    print_section("Training labels")
    for row in training_by_label:
        print(f"{row['visual_label']}: {row['n']}")

    print_section("Training reference status")
    for row in by_status:
        print(
            f"{row['training_reference_status']}: "
            f"{row['n_areas']} areas | {row['total_area_ha']} ha"
        )

    print_section("Spatial validation zones")
    for row in by_zone:
        print(
            f"{row['spatial_validation_zone']}: "
            f"areas={row['n_areas']} | "
            f"reviewed={row['n_visual_reviewed']} | "
            f"olive_like={row['n_olive_like']} | "
            f"not_olive_like={row['n_not_olive_like']} | "
            f"strict={row['n_strict_reference']} | "
            f"identity={row['n_identity_reference']}"
        )

    print_section("Missing feature diagnostics")
    n_total = int(feature_missing["n_total"] or 0)
    for key, value in feature_missing.items():
        if key == "n_total":
            print(f"{key}: {value}")
            continue

        n_missing = int(value or 0)
        pct = (n_missing / n_total * 100) if n_total else 0.0
        print(f"{key}: {n_missing} ({pct:.2f}%)")

    print_section("Readiness checks")
    all_pass = True

    for name, observed, required, passed, note in checks:
        status = "PASS" if passed else "FAIL"
        print(f"{status} | {name}: observed={observed} required>={required} | {note}")
        all_pass = all_pass and passed

    for name, observed, required, passed in zone_checks:
        status = "PASS" if passed else "WARN"
        print(f"{status} | {name}: observed={observed} required>={required}")

    print_section("Conclusion")

    if all_pass:
        print(
            "OK: matrice sufficiente per una prima calibrazione esplorativa "
            "del regional_reliability_score."
        )
        print(
            "Nota: se le zone spaziali hanno WARN, usare comunque leave-one-zone-out "
            "ma dichiarare il limite nella metodologia."
        )
    else:
        print(
            "ATTENZIONE: matrice non ancora robusta per dichiarare uno score regionale "
            "operativo. È possibile procedere solo con calibrazione esplorativa interna."
        )

    print("")
    print("Vincolo metodologico:")
    print("- strict_reference_match=true è un anchor ad alta precisione.")
    print("- strict_reference_match=false non significa area negativa.")
    print("- uncertain non va trattato come negativo.")


if __name__ == "__main__":
    main()