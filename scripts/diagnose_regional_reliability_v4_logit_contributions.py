from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


load_dotenv()

MODEL_VERSION = (
    "regional_reliability_score_exp_v4_combined_ridge"
)

MODEL_PATH = Path(
    "models"
) / MODEL_VERSION / "model.joblib"

OUTPUT_PATH = Path(
    "outputs"
) / "regional_reliability_v4_logit_contributions.csv"

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


def read_dataframe(
    conn,
    sql: str,
    params: tuple[Any, ...],
) -> pd.DataFrame:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)

        columns = [
            description.name
            for description in cursor.description
        ]

        rows = cursor.fetchall()

    return pd.DataFrame(
        rows,
        columns=columns,
    )


def clean_feature_name(
    feature_name: str,
) -> str:
    if "__" in feature_name:
        return feature_name.split("__", 1)[1]

    return feature_name


def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Modello non trovato: {MODEL_PATH}"
        )

    model = joblib.load(MODEL_PATH)

    if not hasattr(model, "named_steps"):
        raise RuntimeError(
            "L'artefatto non è una sklearn Pipeline."
        )

    if "preprocessing" not in model.named_steps:
        raise RuntimeError(
            "Step 'preprocessing' non trovato."
        )

    if "classifier" not in model.named_steps:
        raise RuntimeError(
            "Step 'classifier' non trovato."
        )

    classifier = model.named_steps["classifier"]

    if classifier.coef_.shape[0] != 1:
        raise RuntimeError(
            "Sono supportati soltanto modelli binari."
        )

    return model


def transformed_feature_names(
    preprocessing,
    transformed_n: int,
) -> list[str]:
    try:
        names = list(
            preprocessing.get_feature_names_out()
        )
    except Exception:
        names = FEATURES.copy()

    if len(names) != transformed_n:
        raise RuntimeError(
            "Numero di feature trasformate non coerente: "
            f"nomi={len(names)}, matrice={transformed_n}."
        )

    return [
        clean_feature_name(str(name))
        for name in names
    ]


def transform_features(
    model,
    frame: pd.DataFrame,
) -> tuple[np.ndarray, list[str], np.ndarray, float]:
    preprocessing = model.named_steps[
        "preprocessing"
    ]

    classifier = model.named_steps[
        "classifier"
    ]

    x = frame[FEATURES].copy()

    for feature in FEATURES:
        x[feature] = pd.to_numeric(
            x[feature],
            errors="coerce",
        )

    transformed = preprocessing.transform(x)

    if hasattr(transformed, "toarray"):
        transformed = transformed.toarray()

    transformed = np.asarray(
        transformed,
        dtype=float,
    )

    names = transformed_feature_names(
        preprocessing,
        transformed.shape[1],
    )

    coefficients = np.asarray(
        classifier.coef_[0],
        dtype=float,
    )

    if transformed.shape[1] != coefficients.shape[0]:
        raise RuntimeError(
            "Coefficienti e matrice trasformata "
            "non hanno la stessa dimensione."
        )

    intercept = float(
        classifier.intercept_[0]
    )

    return (
        transformed,
        names,
        coefficients,
        intercept,
    )


def aggregate_contributions(
    population_name: str,
    transformed: np.ndarray,
    feature_names: list[str],
    coefficients: np.ndarray,
    intercept: float,
    scores: pd.Series,
    classes: pd.Series,
) -> pd.DataFrame:
    contributions = (
        transformed
        * coefficients.reshape(1, -1)
    )

    mean_contribution = contributions.mean(
        axis=0
    )

    median_contribution = np.median(
        contributions,
        axis=0,
    )

    mean_abs_contribution = np.abs(
        contributions
    ).mean(axis=0)

    rows = []

    for index, feature_name in enumerate(
        feature_names
    ):
        rows.append(
            {
                "population": population_name,
                "feature": feature_name,
                "coefficient": coefficients[index],
                "mean_contribution": (
                    mean_contribution[index]
                ),
                "median_contribution": (
                    median_contribution[index]
                ),
                "mean_absolute_contribution": (
                    mean_abs_contribution[index]
                ),
                "intercept": intercept,
                "population_n": len(scores),
                "mean_score": float(
                    pd.to_numeric(
                        scores,
                        errors="coerce",
                    ).mean()
                ),
                "very_high_percent": float(
                    100.0
                    * (
                        classes.astype(str)
                        == "very_high"
                    ).mean()
                ),
            }
        )

    return pd.DataFrame(rows)


def main() -> None:
    feature_select = ",\n".join(
        f"s.{feature}"
        for feature in FEATURES
    )

    regional_sql = f"""
    SELECT
        s.area_id,
        s.spatial_validation_zone,
        s.experimental_reliability_score_v4,
        s.experimental_reliability_class_v4,
        {feature_select}
    FROM regional_reliability_scores_v4_diagnostic s
    WHERE s.model_version = %s
    ORDER BY s.area_id;
    """

    training_sql = f"""
    SELECT
        s.area_id,
        s.spatial_validation_zone,
        s.experimental_reliability_score_v4,
        s.experimental_reliability_class_v4,
        t.target_visual_v2,
        {feature_select}
    FROM regional_feature_matrix_training_v2_combined t
    JOIN regional_reliability_scores_v4_diagnostic s
      ON s.area_id = t.area_id::text
     AND s.model_version = %s
    ORDER BY s.area_id;
    """

    conn = database_connection()

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

    if len(regional) != 40261:
        raise RuntimeError(
            "Numero regionale inatteso: "
            f"{len(regional)}."
        )

    if len(training) != 406:
        raise RuntimeError(
            "Numero training inatteso: "
            f"{len(training)}."
        )

    model = load_model()

    populations = [
        ("training_all", training),
        (
            "training_negative",
            training[
                training["target_visual_v2"] == 0
            ],
        ),
        (
            "training_positive",
            training[
                training["target_visual_v2"] == 1
            ],
        ),
        ("all_calabria", regional),
    ]

    for zone in sorted(
        regional[
            "spatial_validation_zone"
        ].dropna().unique()
    ):
        populations.append(
            (
                str(zone),
                regional[
                    regional[
                        "spatial_validation_zone"
                    ] == zone
                ],
            )
        )

    outputs = []

    for population_name, frame in populations:
        (
            transformed,
            feature_names,
            coefficients,
            intercept,
        ) = transform_features(
            model,
            frame,
        )

        # --- BLOCCO AGGIUNTO (verifica e stampa logit) ---
        contributions = (
            transformed
            * coefficients.reshape(1, -1)
        )

        total_feature_contribution = (
            contributions.sum(axis=1)
        )

        total_logit = (
            intercept
            + total_feature_contribution
        )

        reconstructed_probability = (
            1.0
            / (
                1.0
                + np.exp(-total_logit)
            )
        )

        stored_probability = pd.to_numeric(
            frame[
                "experimental_reliability_score_v4"
            ],
            errors="coerce",
        ).to_numpy(dtype=float)

        maximum_probability_difference = float(
            np.max(
                np.abs(
                    reconstructed_probability
                    - stored_probability
                )
            )
        )

        if maximum_probability_difference > 1e-8:
            raise RuntimeError(
                "Ricostruzione delle probabilità "
                "non coerente per "
                f"{population_name}: "
                f"{maximum_probability_difference}"
            )

        print(
            f"{population_name:20s} | "
            f"intercept={intercept:.4f} | "
            f"mean_feature_sum="
            f"{total_feature_contribution.mean():.4f} | "
            f"mean_logit={total_logit.mean():.4f} | "
            f"logit_p25={np.quantile(total_logit, 0.25):.4f} | "
            f"logit_p50={np.quantile(total_logit, 0.50):.4f} | "
            f"logit_p75={np.quantile(total_logit, 0.75):.4f}"
        )
        # ------------------------------------------------

        output = aggregate_contributions(
            population_name=population_name,
            transformed=transformed,
            feature_names=feature_names,
            coefficients=coefficients,
            intercept=intercept,
            scores=frame[
                "experimental_reliability_score_v4"
            ],
            classes=frame[
                "experimental_reliability_class_v4"
            ],
        )

        outputs.append(output)

    diagnostics = pd.concat(
        outputs,
        ignore_index=True,
    )

    training_reference = (
        diagnostics[
            diagnostics["population"]
            == "training_all"
        ][
            [
                "feature",
                "mean_contribution",
            ]
        ]
        .rename(
            columns={
                "mean_contribution":
                    "training_mean_contribution"
            }
        )
    )

    diagnostics = diagnostics.merge(
        training_reference,
        on="feature",
        how="left",
    )

    diagnostics[
        "contribution_shift_vs_training"
    ] = (
        diagnostics["mean_contribution"]
        - diagnostics[
            "training_mean_contribution"
        ]
    )

    diagnostics[
        "absolute_contribution_shift"
    ] = diagnostics[
        "contribution_shift_vs_training"
    ].abs()

    diagnostics = diagnostics.sort_values(
        [
            "population",
            "absolute_contribution_shift",
        ],
        ascending=[True, False],
    )

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    diagnostics.to_csv(
        OUTPUT_PATH,
        index=False,
    )

    # --- SOSTITUZIONE DEL BLOCCO FINALE DI STAMPA ---
    # Riepilogo logit complessivo (sostituisce la vecchia stampa per popolazione)
    print()
    print("Riepilogo logit complessivo")
    print("---------------------------")

    for population_name, frame in populations:
        (
            transformed,
            feature_names,
            coefficients,
            intercept,
        ) = transform_features(
            model,
            frame,
        )

        contribution_sum = (
            transformed
            * coefficients.reshape(1, -1)
        ).sum(axis=1)

        logits = intercept + contribution_sum

        probabilities = (
            1.0
            / (
                1.0
                + np.exp(-logits)
            )
        )

        stored_scores = pd.to_numeric(
            frame[
                "experimental_reliability_score_v4"
            ],
            errors="coerce",
        ).to_numpy(dtype=float)

        max_difference = np.max(
            np.abs(
                probabilities
                - stored_scores
            )
        )

        print(
            f"{population_name:20s} "
            f"n={len(frame):5d} "
            f"intercept={intercept:7.4f} "
            f"features={contribution_sum.mean():7.4f} "
            f"logit_mean={logits.mean():7.4f} "
            f"logit_p50={np.median(logits):7.4f} "
            f"score_p50={np.median(probabilities):7.4f} "
            f"check={max_difference:.2e}"
        )
    # --------------------------------------------------

    print()
    print(f"CSV salvato: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()