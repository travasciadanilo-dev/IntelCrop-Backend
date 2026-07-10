import json
import os
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from sklearn.calibration import calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import (
    GridSearchCV,
    RepeatedStratifiedKFold,
    StratifiedKFold,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato.")


QUERY = """
SELECT
    area_id,
    spatial_validation_zone,
    target_visual_v2,

    area_ha_raw,
    perimeter_m_raw,
    compactness_raw,
    n_points,

    large_polygon_flag,
    small_candidate_flag,
    complex_boundary_flag,

    n_observations,

    ndvi_median,
    ndvi_p25,
    ndvi_p75,
    ndvi_stddev,

    evi_median,
    evi_p25,
    evi_p75,
    evi_stddev,

    ndmi_median,
    ndmi_p25,
    ndmi_p75,
    ndmi_stddev,

    bsi_median,
    bsi_p25,
    bsi_p75,
    bsi_stddev

FROM regional_feature_matrix_training_v2_combined
ORDER BY area_id;
"""


GEOMETRY_FEATURES = [
    "log_area_ha",
    "log_perimeter_m",
    "compactness_raw",
    "log_n_points",
    "large_polygon_flag",
    "small_candidate_flag",
    "complex_boundary_flag",
]

SPECTRAL_FEATURES = [
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

FEATURE_SETS = {
    "geometry": GEOMETRY_FEATURES,
    "spectral": SPECTRAL_FEATURES,
    "combined": GEOMETRY_FEATURES + SPECTRAL_FEATURES,
}


PARAM_GRID = {
    "classifier__C": [
        0.001,
        0.01,
        0.1,
        1.0,
        10.0,
        100.0,
    ],
    "classifier__l1_ratio": [
        0.0,
        0.25,
        0.5,
        0.75,
        1.0,
    ],
}


RANDOM_STATE = 42
THRESHOLD = 0.5


def load_data() -> pd.DataFrame:
    with psycopg2.connect(DATABASE_URL) as conn:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pandas only supports SQLAlchemy",
            )
            df = pd.read_sql_query(QUERY, conn)

    if len(df) != 406:
        raise RuntimeError(
            f"Numero di righe inatteso: {len(df)}."
        )

    if df["area_id"].duplicated().any():
        raise RuntimeError("area_id duplicati.")

    if df.isna().any().any():
        missing = df.isna().sum()
        missing = missing[missing > 0]

        raise RuntimeError(
            "Valori mancanti:\n"
            + missing.to_string()
        )

    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    result["log_area_ha"] = np.log1p(
        result["area_ha_raw"].clip(lower=0)
    )

    result["log_perimeter_m"] = np.log1p(
        result["perimeter_m_raw"].clip(lower=0)
    )

    result["log_n_points"] = np.log1p(
        result["n_points"].clip(lower=0)
    )

    result["log_n_observations"] = np.log1p(
        result["n_observations"].clip(lower=0)
    )

    for index_name in ["ndvi", "evi", "ndmi", "bsi"]:
        result[f"{index_name}_iqr"] = (
            result[f"{index_name}_p75"]
            - result[f"{index_name}_p25"]
        )

    boolean_columns = [
        "large_polygon_flag",
        "small_candidate_flag",
        "complex_boundary_flag",
    ]

    for column in boolean_columns:
        result[column] = result[column].astype(int)

    return result


def build_pipeline(features: list[str]) -> Pipeline:
    preprocessing = ColumnTransformer(
        transformers=[
            (
                "numeric",
                Pipeline(
                    steps=[
                        (
                            "imputer",
                            SimpleImputer(strategy="median"),
                        ),
                        (
                            "scaler",
                            StandardScaler(),
                        ),
                    ]
                ),
                features,
            )
        ],
        remainder="drop",
    )

    # MODIFICA: rimosso penalty="elasticnet" e aggiunto l1_ratio=0.5
    classifier = LogisticRegression(
        solver="saga",
        l1_ratio=0.5,
        class_weight="balanced",
        max_iter=10000,
        random_state=RANDOM_STATE,
    )

    return Pipeline(
        steps=[
            ("preprocessing", preprocessing),
            ("classifier", classifier),
        ]
    )


def calculate_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    threshold: float = THRESHOLD,
) -> dict[str, float]:
    predictions = (
        probabilities >= threshold
    ).astype(int)

    tn, fp, fn, tp = confusion_matrix(
        y_true,
        predictions,
        labels=[0, 1],
    ).ravel()

    specificity = (
        tn / (tn + fp)
        if (tn + fp) > 0
        else np.nan
    )

    return {
        "precision": precision_score(
            y_true,
            predictions,
            zero_division=0,
        ),
        "recall": recall_score(
            y_true,
            predictions,
            zero_division=0,
        ),
        "specificity": specificity,
        "f1": f1_score(
            y_true,
            predictions,
            zero_division=0,
        ),
        "accuracy": accuracy_score(
            y_true,
            predictions,
        ),
        "roc_auc": roc_auc_score(
            y_true,
            probabilities,
        ),
        "brier": brier_score_loss(
            y_true,
            probabilities,
        ),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
    }


def fit_inner_search(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    features: list[str],
) -> GridSearchCV:
    inner_cv = StratifiedKFold(
        n_splits=5,
        shuffle=True,
        random_state=RANDOM_STATE,
    )

    search = GridSearchCV(
        estimator=build_pipeline(features),
        param_grid=PARAM_GRID,
        scoring="neg_brier_score",
        cv=inner_cv,
        n_jobs=-1,
        refit=True,
        error_score="raise",
    )

    search.fit(
        x_train[features],
        y_train,
    )

    return search


def repeated_cv(
    df: pd.DataFrame,
    feature_set_name: str,
    features: list[str],
) -> tuple[dict[str, Any], pd.DataFrame]:
    y = df["target_visual_v2"].to_numpy()

    outer_cv = RepeatedStratifiedKFold(
        n_splits=5,
        n_repeats=10,
        random_state=RANDOM_STATE,
    )

    predictions = []
    fold_rows = []

    for fold_id, (train_index, test_index) in enumerate(
        outer_cv.split(df, y),
        start=1,
    ):
        train_df = df.iloc[train_index]
        test_df = df.iloc[test_index]

        y_train = y[train_index]
        y_test = y[test_index]

        search = fit_inner_search(
            train_df,
            y_train,
            features,
        )

        probabilities = search.predict_proba(
            test_df[features]
        )[:, 1]

        metrics = calculate_metrics(
            y_test,
            probabilities,
        )

        fold_rows.append(
            {
                "evaluation": "repeated_cv",
                "feature_set": feature_set_name,
                "fold_id": fold_id,
                "test_zone": None,
                "test_n": len(test_index),
                "positive_n": int(y_test.sum()),
                "negative_n": int(
                    len(y_test) - y_test.sum()
                ),
                "best_c": search.best_params_[
                    "classifier__C"
                ],
                "best_l1_ratio": search.best_params_[
                    "classifier__l1_ratio"
                ],
                **metrics,
            }
        )

        for local_index, probability in zip(
            test_index,
            probabilities,
        ):
            predictions.append(
                {
                    "evaluation": "repeated_cv",
                    "feature_set": feature_set_name,
                    "fold_id": fold_id,
                    "area_id": df.iloc[
                        local_index
                    ]["area_id"],
                    "spatial_validation_zone": df.iloc[
                        local_index
                    ]["spatial_validation_zone"],
                    "target": int(
                        df.iloc[
                            local_index
                        ]["target_visual_v2"]
                    ),
                    "probability": float(probability),
                }
            )

    fold_df = pd.DataFrame(fold_rows)
    prediction_df = pd.DataFrame(predictions)

    summary = {
        "evaluation": "repeated_cv",
        "feature_set": feature_set_name,
        "n_outer_folds": len(fold_df),
    }

    metric_names = [
        "precision",
        "recall",
        "specificity",
        "f1",
        "accuracy",
        "roc_auc",
        "brier",
    ]

    for metric in metric_names:
        summary[f"{metric}_mean"] = float(
            fold_df[metric].mean()
        )
        summary[f"{metric}_sd"] = float(
            fold_df[metric].std(ddof=1)
        )

    return summary, prediction_df


def leave_one_zone_out(
    df: pd.DataFrame,
    feature_set_name: str,
    features: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    predictions = []

    zones = sorted(
        df["spatial_validation_zone"].unique()
    )

    for zone in zones:
        train_df = df[
            df["spatial_validation_zone"] != zone
        ].copy()

        test_df = df[
            df["spatial_validation_zone"] == zone
        ].copy()

        y_train = train_df[
            "target_visual_v2"
        ].to_numpy()

        y_test = test_df[
            "target_visual_v2"
        ].to_numpy()

        search = fit_inner_search(
            train_df,
            y_train,
            features,
        )

        probabilities = search.predict_proba(
            test_df[features]
        )[:, 1]

        metrics = calculate_metrics(
            y_test,
            probabilities,
        )

        rows.append(
            {
                "evaluation": "leave_one_zone_out",
                "feature_set": feature_set_name,
                "held_out_zone": zone,
                "test_n": len(test_df),
                "positive_n": int(y_test.sum()),
                "negative_n": int(
                    len(y_test) - y_test.sum()
                ),
                "best_c": search.best_params_[
                    "classifier__C"
                ],
                "best_l1_ratio": search.best_params_[
                    "classifier__l1_ratio"
                ],
                **metrics,
            }
        )

        for (_, sample), probability in zip(
            test_df.iterrows(),
            probabilities,
        ):
            predictions.append(
                {
                    "evaluation": "leave_one_zone_out",
                    "feature_set": feature_set_name,
                    "held_out_zone": zone,
                    "area_id": sample["area_id"],
                    "spatial_validation_zone": sample[
                        "spatial_validation_zone"
                    ],
                    "target": int(
                        sample["target_visual_v2"]
                    ),
                    "probability": float(probability),
                }
            )

    return (
        pd.DataFrame(rows),
        pd.DataFrame(predictions),
    )


def main() -> None:
    df = engineer_features(load_data())

    output_dir = Path("outputs")
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    repeated_summaries = []
    all_predictions = []
    all_zone_results = []

    for feature_set_name, features in FEATURE_SETS.items():
        print()
        print(f"Feature set: {feature_set_name}")
        print("-" * 50)

        repeated_summary, repeated_predictions = (
            repeated_cv(
                df,
                feature_set_name,
                features,
            )
        )

        zone_results, zone_predictions = (
            leave_one_zone_out(
                df,
                feature_set_name,
                features,
            )
        )

        repeated_summaries.append(
            repeated_summary
        )

        all_predictions.extend(
            [
                repeated_predictions,
                zone_predictions,
            ]
        )

        all_zone_results.append(
            zone_results
        )

        print(
            json.dumps(
                repeated_summary,
                indent=2,
            )
        )

        print()
        print("Leave-one-zone-out")
        print(
            zone_results[
                [
                    "held_out_zone",
                    "test_n",
                    "precision",
                    "recall",
                    "specificity",
                    "f1",
                    "roc_auc",
                    "brier",
                ]
            ].to_string(index=False)
        )

    repeated_summary_df = pd.DataFrame(
        repeated_summaries
    )

    zone_results_df = pd.concat(
        all_zone_results,
        ignore_index=True,
    )

    predictions_df = pd.concat(
        all_predictions,
        ignore_index=True,
    )

    repeated_summary_df.to_csv(
        output_dir
        / "regional_reliability_v4_repeated_cv_summary.csv",
        index=False,
    )

    zone_results_df.to_csv(
        output_dir
        / "regional_reliability_v4_leave_one_zone_out.csv",
        index=False,
    )

    predictions_df.to_csv(
        output_dir
        / "regional_reliability_v4_predictions.csv",
        index=False,
    )

    print()
    print("Confronto finale")
    print("-----------------")

    print(
        repeated_summary_df[
            [
                "feature_set",
                "precision_mean",
                "recall_mean",
                "specificity_mean",
                "f1_mean",
                "roc_auc_mean",
                "brier_mean",
            ]
        ].sort_values(
            "brier_mean"
        ).to_string(index=False)
    )


if __name__ == "__main__":
    main()