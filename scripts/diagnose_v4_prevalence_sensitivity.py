from __future__ import annotations

import math
import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv


load_dotenv()

MODEL_VERSION = (
    "regional_reliability_score_exp_v4_combined_ridge"
)

TRAINING_PREVALENCE = 319 / 406

TARGET_PREVALENCES = [
    0.30,
    0.40,
    0.50,
    0.60,
    0.70,
    TRAINING_PREVALENCE,
]


def logit(probability: float) -> float:
    return math.log(
        probability / (1.0 - probability)
    )


def sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)

    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def reliability_class(score: float) -> str:
    if score < 0.61:
        return "low"

    if score < 0.77:
        return "compatible"

    if score < 0.82:
        return "high"

    return "very_high"


def database_connection():
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


def main() -> None:
    conn = database_connection()

    try:
        frame = pd.read_sql_query(
            """
            SELECT
                area_id,
                spatial_validation_zone,
                experimental_reliability_score_v4
                    AS original_score
            FROM regional_reliability_scores_v4_diagnostic
            WHERE model_version = %s
            ORDER BY area_id;
            """,
            conn,
            params=(MODEL_VERSION,),
        )

    finally:
        conn.close()

    frame["original_logit"] = frame[
        "original_score"
    ].apply(logit)

    rows = []

    training_prior_logit = logit(
        TRAINING_PREVALENCE
    )

    for target_prevalence in TARGET_PREVALENCES:
        intercept_adjustment = (
            logit(target_prevalence)
            - training_prior_logit
        )

        scenario = frame.copy()

        scenario["adjusted_score"] = (
            scenario["original_logit"]
            + intercept_adjustment
        ).apply(sigmoid)

        scenario["adjusted_class"] = scenario[
            "adjusted_score"
        ].apply(reliability_class)

        groups = [
            ("all_calabria", scenario),
            *[
                (
                    zone,
                    scenario[
                        scenario[
                            "spatial_validation_zone"
                        ] == zone
                    ],
                )
                for zone in sorted(
                    scenario[
                        "spatial_validation_zone"
                    ].unique()
                )
            ],
        ]

        for population, subset in groups:
            class_counts = (
                subset["adjusted_class"]
                .value_counts()
            )

            rows.append(
                {
                    "target_prevalence":
                        target_prevalence,
                    "population": population,
                    "n": len(subset),
                    "mean_score": subset[
                        "adjusted_score"
                    ].mean(),
                    "median_score": subset[
                        "adjusted_score"
                    ].median(),
                    "low_n": int(
                        class_counts.get("low", 0)
                    ),
                    "compatible_n": int(
                        class_counts.get(
                            "compatible",
                            0,
                        )
                    ),
                    "high_n": int(
                        class_counts.get("high", 0)
                    ),
                    "very_high_n": int(
                        class_counts.get(
                            "very_high",
                            0,
                        )
                    ),
                    "very_high_percent": (
                        100.0
                        * class_counts.get(
                            "very_high",
                            0,
                        )
                        / len(subset)
                    ),
                    "intercept_adjustment":
                        intercept_adjustment,
                }
            )

    output = pd.DataFrame(rows)

    output.to_csv(
        "outputs/"
        "regional_reliability_v4_prevalence_sensitivity.csv",
        index=False,
    )

    print("Sensibilità v4 alla prevalenza")
    print("------------------------------")

    regional = output[
        output["population"] == "all_calabria"
    ]

    for row in regional.itertuples(index=False):
        print(
            f"prevalenza={row.target_prevalence:6.3f} "
            f"mean_score={row.mean_score:6.3f} "
            f"median={row.median_score:6.3f} "
            f"very_high={row.very_high_percent:6.2f}%"
        )

    print()
    print(
        "CSV salvato: "
        "outputs/"
        "regional_reliability_v4_prevalence_sensitivity.csv"
    )


if __name__ == "__main__":
    main()
