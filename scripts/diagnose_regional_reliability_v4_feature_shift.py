from __future__ import annotations

import os

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


load_dotenv()

MODEL_VERSION = (
    "regional_reliability_score_exp_v4_combined_ridge"
)

FEATURES = [
    "log_area_ha",
    "log_perimeter_m",
    "compactness_raw",
    "log_n_points",
    "large_polygon_flag",
    "small_candidate_flag",
    "complex_boundary_flag",
    "log_n_observations",
    "ndvi_median",
    "ndvi_iqr",
    "ndvi_stddev",
    "evi_median",
    "evi_iqr",
    "evi_stddev",
    "ndmi_median",
    "ndmi_iqr",
    "ndmi_stddev",
    "bsi_median",
    "bsi_iqr",
    "bsi_stddev",
]


def connect():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(
        host=(
            os.getenv("POSTGRES_HOST")
            or os.getenv("DB_HOST")
            or "localhost"
        ),
        port=int(
            os.getenv("POSTGRES_PORT")
            or os.getenv("DB_PORT")
            or "5432"
        ),
        dbname=(
            os.getenv("POSTGRES_DB")
            or os.getenv("DB_NAME")
            or "intellcrop"
        ),
        user=(
            os.getenv("POSTGRES_USER")
            or os.getenv("DB_USER")
            or "intellcrop"
        ),
        password=(
            os.getenv("POSTGRES_PASSWORD")
            or os.getenv("DB_PASSWORD")
        ),
    )


def read_dataframe(conn, sql, params=None):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)

        columns = [
            description.name
            for description in cursor.description
        ]

        return pd.DataFrame(
            cursor.fetchall(),
            columns=columns,
        )


def standardized_difference(
    reference: pd.Series,
    comparison: pd.Series,
) -> float:
    reference = pd.to_numeric(
        reference,
        errors="coerce",
    ).dropna()

    comparison = pd.to_numeric(
        comparison,
        errors="coerce",
    ).dropna()

    reference_sd = float(
        reference.std(ddof=1)
    )

    if (
        not np.isfinite(reference_sd)
        or reference_sd == 0.0
    ):
        return float("nan")

    return float(
        (
            comparison.mean()
            - reference.mean()
        )
        / reference_sd
    )


def shift_level(value: float) -> str:
    if not np.isfinite(value):
        return "not_available"

    absolute = abs(value)

    if absolute < 0.25:
        return "minimal"

    if absolute < 0.50:
        return "moderate"

    if absolute < 1.00:
        return "high"

    return "very_high"


def main():
    feature_sql = ",\n".join(FEATURES)

    regional_sql = f"""
    SELECT
        area_id,
        spatial_validation_zone,
        experimental_reliability_score_v4,
        experimental_reliability_class_v4,
        {feature_sql}
    FROM regional_reliability_scores_v4_diagnostic
    WHERE model_version = %s
    ORDER BY area_id;
    """

    training_sql = f"""
    SELECT
        t.area_id::text AS area_id,
        t.spatial_validation_zone,
        t.target_visual_v2,
        s.experimental_reliability_score_v4,
        s.experimental_reliability_class_v4,
        {", ".join("s." + feature for feature in FEATURES)}
    FROM regional_feature_matrix_training_v2_combined t
    JOIN regional_reliability_scores_v4_diagnostic s
      ON s.area_id = t.area_id::text
     AND s.model_version = %s
    ORDER BY t.area_id;
    """

    conn = connect()

    try:
        regional = read_dataframe(
            conn,
            regional_sql,
            (MODEL_VERSION,),
        )

        training = read_dataframe(
            conn,
            training_sql,
            (MODEL_VERSION,),
        )

    finally:
        conn.close()

    rows = []

    populations = [
        ("all_calabria", regional),
        *[
            (
                zone,
                regional[
                    regional[
                        "spatial_validation_zone"
                    ] == zone
                ],
            )
            for zone in sorted(
                regional[
                    "spatial_validation_zone"
                ].unique()
            )
        ],
    ]

    for population_name, population in populations:
        for feature in FEATURES:
            training_values = pd.to_numeric(
                training[feature],
                errors="coerce",
            )

            population_values = pd.to_numeric(
                population[feature],
                errors="coerce",
            )

            standardized_shift = (
                standardized_difference(
                    training_values,
                    population_values,
                )
            )

            rows.append(
                {
                    "population": population_name,
                    "feature": feature,
                    "training_mean": (
                        training_values.mean()
                    ),
                    "population_mean": (
                        population_values.mean()
                    ),
                    "training_sd": (
                        training_values.std(ddof=1)
                    ),
                    "standardized_shift": (
                        standardized_shift
                    ),
                    "absolute_shift": abs(
                        standardized_shift
                    ),
                    "shift_level": shift_level(
                        standardized_shift
                    ),
                }
            )

    diagnostics = pd.DataFrame(rows)

    diagnostics = diagnostics.sort_values(
        [
            "population",
            "absolute_shift",
        ],
        ascending=[True, False],
    )

    output_path = (
        "outputs/"
        "regional_reliability_v4_feature_shift.csv"
    )

    os.makedirs(
        "outputs",
        exist_ok=True,
    )

    diagnostics.to_csv(
        output_path,
        index=False,
    )

    print(
        "Feature-shift diagnostica v4"
    )
    print(
        "----------------------------"
    )

    for population_name in diagnostics[
        "population"
    ].unique():
        subset = diagnostics[
            diagnostics["population"]
            == population_name
        ].head(10)

        print()
        print(population_name)

        for row in subset.itertuples(
            index=False
        ):
            print(
                f"{row.feature:24s} "
                f"shift={row.standardized_shift:8.3f} "
                f"level={row.shift_level}"
            )

    print()
    print(
        f"CSV salvato: {output_path}"
    )


if __name__ == "__main__":
    main()
