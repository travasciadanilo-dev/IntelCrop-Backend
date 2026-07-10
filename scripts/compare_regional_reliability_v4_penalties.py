import json
import os
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from scipy.special import expit, logit
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
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

FEATURES = GEOMETRY_FEATURES + SPECTRAL_FEATURES

MODEL_CONFIGS = {
    "combined_ridge": {
        "class_weight": None,
        "l1_ratio": 0.0,
    },
    "combined_lasso": {
        "class_weight": None,
        "l1_ratio": 1.0,
    },
}

PARAM_GRID = {
    "classifier__C": [
        0.1,
        0.3,
        1.0,
        3.0,
        10.0,
        30.0,
    ],
}

RANDOM_STATE = 42
THRESHOLD = 0.5
EPSILON = 1e-6
BOOTSTRAP_ITERATIONS = 2000


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
            f"Numero di righe inatteso: {len(df)} invece di 406."
        )

    if df["area_id"].duplicated().any():
        raise RuntimeError("Sono presenti area_id duplicati.")

    if df.isna().any().any():
        missing = df.isna().sum()
        missing = missing[missing > 0]

        raise RuntimeError(
            "Valori mancanti:\n"
            + missing.to_string()
        )

    class_counts = df["target_visual_v2"].value_counts().to_dict()

    if class_counts.get(1) != 319 or class_counts.get(0) != 87:
        raise RuntimeError(
            f"Distribuzione target inattesa: {class_counts}."
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

    for column in [
        "large_polygon_flag",
        "small_candidate_flag",
        "complex_boundary_flag",
    ]:
        result[column] = result[column].astype(int)

    return result


def build_pipeline(
    class_weight: str | None,
    l1_ratio: float,
) -> Pipeline:
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
                FEATURES,
            )
        ],
        remainder="drop",
    )

    classifier = LogisticRegression(
        solver="saga",
        l1_ratio=l1_ratio,
        class_weight=class_weight,
        max_iter=10000,
        random_state=RANDOM_STATE,
    )

    return Pipeline(
        steps=[
            ("preprocessing", preprocessing),
            ("classifier", classifier),
        ]
    )


def fit_inner_search(
    train_df: pd.DataFrame,
    class_weight: str | None,
    l1_ratio: float,
    inner_seed: int,
) -> GridSearchCV:
    inner_cv = StratifiedKFold(
        n_splits=5,
        shuffle=True,
        random_state=inner_seed,
    )

    search = GridSearchCV(
        estimator=build_pipeline(
            class_weight=class_weight,
            l1_ratio=l1_ratio,
        ),
        param_grid=PARAM_GRID,
        scoring="neg_brier_score",
        cv=inner_cv,
        n_jobs=-1,
        refit=True,
        error_score="raise",
    )

    search.fit(
        train_df[FEATURES],
        train_df["target_visual_v2"].to_numpy(),
    )

    return search


def classification_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, float]:
    probabilities = np.clip(
        probabilities,
        EPSILON,
        1.0 - EPSILON,
    )

    predictions = (
        probabilities >= THRESHOLD
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
        "precision": float(
            precision_score(
                y_true,
                predictions,
                zero_division=0,
            )
        ),
        "recall": float(
            recall_score(
                y_true,
                predictions,
                zero_division=0,
            )
        ),
        "specificity": float(specificity),
        "f1": float(
            f1_score(
                y_true,
                predictions,
                zero_division=0,
            )
        ),
        "accuracy": float(
            accuracy_score(y_true, predictions)
        ),
        "roc_auc": float(
            roc_auc_score(y_true, probabilities)
        ),
        "brier": float(
            brier_score_loss(y_true, probabilities)
        ),
        "log_loss": float(
            log_loss(
                y_true,
                probabilities,
                labels=[0, 1],
            )
        ),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
    }


def expected_calibration_error(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    n_bins: int = 10,
) -> float:
    probabilities = np.clip(
        probabilities,
        EPSILON,
        1.0 - EPSILON,
    )

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.digitize(
        probabilities,
        edges[1:-1],
        right=True,
    )

    ece = 0.0
    n = len(y_true)

    for bin_id in range(n_bins):
        mask = bin_ids == bin_id

        if not np.any(mask):
            continue

        observed = float(np.mean(y_true[mask]))
        predicted = float(np.mean(probabilities[mask]))
        weight = float(np.sum(mask) / n)

        ece += weight * abs(observed - predicted)

    return float(ece)


def calibration_intercept_slope(
    y_true: np.ndarray,
    probabilities: np.ndarray,
) -> tuple[float, float]:
    probabilities = np.clip(
        probabilities,
        EPSILON,
        1.0 - EPSILON,
    )

    logits = logit(probabilities).reshape(-1, 1)

    calibration_model = LogisticRegression(
        solver="lbfgs",
        C=np.inf,
        max_iter=10000,
    )

    calibration_model.fit(logits, y_true)

    intercept = float(
        calibration_model.intercept_[0]
    )
    slope = float(
        calibration_model.coef_[0, 0]
    )

    return intercept, slope


def repeated_nested_cv(
    df: pd.DataFrame,
    model_name: str,
    class_weight: str | None,
    l1_ratio: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    y = df["target_visual_v2"].to_numpy()

    outer_cv = RepeatedStratifiedKFold(
        n_splits=5,
        n_repeats=10,
        random_state=RANDOM_STATE,
    )

    fold_rows = []
    prediction_rows = []

    for fold_id, (train_index, test_index) in enumerate(
        outer_cv.split(df, y),
        start=1,
    ):
        train_df = df.iloc[train_index].copy()
        test_df = df.iloc[test_index].copy()

        search = fit_inner_search(
            train_df=train_df,
            class_weight=class_weight,
            l1_ratio=l1_ratio,
            inner_seed=RANDOM_STATE + fold_id,
        )

        probabilities = search.predict_proba(
            test_df[FEATURES]
        )[:, 1]

        y_test = test_df[
            "target_visual_v2"
        ].to_numpy()

        metrics = classification_metrics(
            y_test,
            probabilities,
        )

        fold_rows.append(
            {
                "model_name": model_name,
                "fold_id": fold_id,
                "test_n": len(test_df),
                "positive_n": int(y_test.sum()),
                "negative_n": int(
                    len(y_test) - y_test.sum()
                ),
                "best_c": float(
                    search.best_params_[
                        "classifier__C"
                    ]
                ),
                "fixed_l1_ratio": float(l1_ratio),
                **metrics,
            }
        )

        for (_, sample), probability in zip(
            test_df.iterrows(),
            probabilities,
        ):
            prediction_rows.append(
                {
                    "model_name": model_name,
                    "fold_id": fold_id,
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
        pd.DataFrame(fold_rows),
        pd.DataFrame(prediction_rows),
    )


def aggregate_repeated_predictions(
    prediction_df: pd.DataFrame,
) -> pd.DataFrame:
    target_counts = prediction_df.groupby(
        "area_id"
    )["target"].nunique()

    if (target_counts != 1).any():
        raise RuntimeError(
            "Target incoerente tra ripetizioni CV."
        )

    aggregated = (
        prediction_df
        .groupby(
            [
                "model_name",
                "area_id",
                "spatial_validation_zone",
                "target",
            ],
            as_index=False,
        )
        .agg(
            probability=("probability", "mean"),
            probability_sd=("probability", "std"),
            n_predictions=("probability", "size"),
        )
    )

    if len(aggregated) != 406:
        raise RuntimeError(
            f"Predizioni aggregate inattese: {len(aggregated)}."
        )

    if not (
        aggregated["n_predictions"] == 10
    ).all():
        raise RuntimeError(
            "Ogni area deve avere esattamente 10 predizioni."
        )

    return aggregated


def evaluate_aggregated(
    aggregated: pd.DataFrame,
) -> dict[str, Any]:
    y_true = aggregated["target"].to_numpy()
    probabilities = aggregated[
        "probability"
    ].to_numpy()

    intercept, slope = calibration_intercept_slope(
        y_true,
        probabilities,
    )

    return {
        **classification_metrics(
            y_true,
            probabilities,
        ),
        "calibration_intercept": intercept,
        "calibration_slope": slope,
        "ece_10_bins": expected_calibration_error(
            y_true,
            probabilities,
            n_bins=10,
        ),
        "mean_probability_sd": float(
            aggregated["probability_sd"].mean()
        ),
    }


def leave_one_zone_out(
    df: pd.DataFrame,
    model_name: str,
    class_weight: str | None,
    l1_ratio: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    result_rows = []
    prediction_rows = []

    zones = sorted(
        df["spatial_validation_zone"].unique()
    )

    for zone_index, held_out_zone in enumerate(
        zones,
        start=1,
    ):
        train_df = df[
            df["spatial_validation_zone"]
            != held_out_zone
        ].copy()

        test_df = df[
            df["spatial_validation_zone"]
            == held_out_zone
        ].copy()

        search = fit_inner_search(
            train_df=train_df,
            class_weight=class_weight,
            l1_ratio=l1_ratio,
            inner_seed=RANDOM_STATE + 100 + zone_index,
        )

        probabilities = search.predict_proba(
            test_df[FEATURES]
        )[:, 1]

        y_test = test_df[
            "target_visual_v2"
        ].to_numpy()

        intercept, slope = calibration_intercept_slope(
            y_test,
            probabilities,
        )

        metrics = classification_metrics(
            y_test,
            probabilities,
        )

        result_rows.append(
            {
                "model_name": model_name,
                "held_out_zone": held_out_zone,
                "test_n": len(test_df),
                "positive_n": int(y_test.sum()),
                "negative_n": int(
                    len(y_test) - y_test.sum()
                ),
                "best_c": float(
                    search.best_params_[
                        "classifier__C"
                    ]
                ),
                "fixed_l1_ratio": float(l1_ratio),
                **metrics,
                "calibration_intercept": intercept,
                "calibration_slope": slope,
                "ece_10_bins": expected_calibration_error(
                    y_test,
                    probabilities,
                    n_bins=10,
                ),
            }
        )

        for (_, sample), probability in zip(
            test_df.iterrows(),
            probabilities,
        ):
            prediction_rows.append(
                {
                    "model_name": model_name,
                    "held_out_zone": held_out_zone,
                    "area_id": sample["area_id"],
                    "target": int(
                        sample["target_visual_v2"]
                    ),
                    "probability": float(probability),
                }
            )

    return (
        pd.DataFrame(result_rows),
        pd.DataFrame(prediction_rows),
    )


def paired_bootstrap_difference(
    balanced: pd.DataFrame,
    unweighted: pd.DataFrame,
) -> pd.DataFrame:
    merged = balanced[
        ["area_id", "target", "probability"]
    ].merge(
        unweighted[
            ["area_id", "target", "probability"]
        ],
        on=["area_id", "target"],
        suffixes=("_balanced", "_unweighted"),
        validate="one_to_one",
    )

    y = merged["target"].to_numpy()
    p_balanced = merged[
        "probability_balanced"
    ].to_numpy()
    p_unweighted = merged[
        "probability_unweighted"
    ].to_numpy()

    rng = np.random.default_rng(RANDOM_STATE)
    n = len(merged)

    rows = []

    for iteration in range(
        BOOTSTRAP_ITERATIONS
    ):
        indices = rng.integers(
            low=0,
            high=n,
            size=n,
        )

        y_boot = y[indices]

        if np.unique(y_boot).size < 2:
            continue

        pb = p_balanced[indices]
        pu = p_unweighted[indices]

        rows.append(
            {
                "iteration": iteration + 1,
                "brier_difference_balanced_minus_unweighted":
                    brier_score_loss(y_boot, pb)
                    - brier_score_loss(y_boot, pu),
                "auc_difference_balanced_minus_unweighted":
                    roc_auc_score(y_boot, pb)
                    - roc_auc_score(y_boot, pu),
                "log_loss_difference_balanced_minus_unweighted":
                    log_loss(
                        y_boot,
                        np.clip(
                            pb,
                            EPSILON,
                            1.0 - EPSILON,
                        ),
                    )
                    - log_loss(
                        y_boot,
                        np.clip(
                            pu,
                            EPSILON,
                            1.0 - EPSILON,
                        ),
                    ),
            }
        )

    return pd.DataFrame(rows)


def bootstrap_summary(
    bootstrap_df: pd.DataFrame,
) -> pd.DataFrame:
    rows = []

    metric_columns = [
        column
        for column in bootstrap_df.columns
        if column != "iteration"
    ]

    for column in metric_columns:
        values = bootstrap_df[column].dropna()

        rows.append(
            {
                "metric": column,
                "mean_difference": float(
                    values.mean()
                ),
                "median_difference": float(
                    values.median()
                ),
                "ci_2_5": float(
                    values.quantile(0.025)
                ),
                "ci_97_5": float(
                    values.quantile(0.975)
                ),
                "probability_difference_below_zero":
                    float((values < 0).mean()),
            }
        )

    return pd.DataFrame(rows)


def main() -> None:
    df = engineer_features(load_data())

    output_dir = Path("outputs")
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    fold_frames = []
    repeated_prediction_frames = []
    aggregated_frames = []
    evaluation_rows = []
    lozo_frames = []
    lozo_prediction_frames = []

    for model_name, config in (
        MODEL_CONFIGS.items()
    ):
        class_weight = config["class_weight"]
        l1_ratio = config["l1_ratio"]
        print()
        print(f"Modello: {model_name}")
        print("-" * 60)

        fold_df, repeated_predictions = (
            repeated_nested_cv(
                df=df,
                model_name=model_name,
                class_weight=class_weight,
                l1_ratio=l1_ratio,
            )
        )

        aggregated = aggregate_repeated_predictions(
            repeated_predictions
        )

        aggregate_metrics = evaluate_aggregated(
            aggregated
        )

        evaluation_rows.append(
            {
                "model_name": model_name,
                "n_areas": len(aggregated),
                **aggregate_metrics,
            }
        )

        lozo_df, lozo_predictions = (
            leave_one_zone_out(
                df=df,
                model_name=model_name,
                class_weight=class_weight,
                l1_ratio=l1_ratio,
            )
        )

        fold_frames.append(fold_df)
        repeated_prediction_frames.append(
            repeated_predictions
        )
        aggregated_frames.append(aggregated)
        lozo_frames.append(lozo_df)
        lozo_prediction_frames.append(
            lozo_predictions
        )

        print(
            json.dumps(
                evaluation_rows[-1],
                indent=2,
            )
        )

        print()
        print("Leave-one-zone-out")
        print(
            lozo_df[
                [
                    "held_out_zone",
                    "test_n",
                    "precision",
                    "recall",
                    "specificity",
                    "roc_auc",
                    "brier",
                    "calibration_intercept",
                    "calibration_slope",
                ]
            ].to_string(index=False)
        )

    folds_df = pd.concat(
        fold_frames,
        ignore_index=True,
    )

    repeated_predictions_df = pd.concat(
        repeated_prediction_frames,
        ignore_index=True,
    )

    aggregated_df = pd.concat(
        aggregated_frames,
        ignore_index=True,
    )

    evaluation_df = pd.DataFrame(
        evaluation_rows
    )

    lozo_df = pd.concat(
        lozo_frames,
        ignore_index=True,
    )

    lozo_predictions_df = pd.concat(
        lozo_prediction_frames,
        ignore_index=True,
    )

    ridge = aggregated_df[
        aggregated_df["model_name"]
        == "combined_ridge"
    ].copy()

    lasso = aggregated_df[
        aggregated_df["model_name"]
        == "combined_lasso"
    ].copy()

    bootstrap_df = paired_bootstrap_difference(
        balanced=ridge,
        unweighted=lasso,
    )

    bootstrap_summary_df = bootstrap_summary(
        bootstrap_df
    )

    folds_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_folds.csv",
        index=False,
    )

    repeated_predictions_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_repeated_predictions.csv",
        index=False,
    )

    aggregated_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_aggregated_predictions.csv",
        index=False,
    )

    evaluation_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_calibration_summary.csv",
        index=False,
    )

    lozo_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_lozo.csv",
        index=False,
    )

    lozo_predictions_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_lozo_predictions.csv",
        index=False,
    )

    bootstrap_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_bootstrap.csv",
        index=False,
    )

    bootstrap_summary_df.to_csv(
        output_dir
        / "regional_reliability_v4_penalties_bootstrap_summary.csv",
        index=False,
    )

    print()
    print("Confronto Ridge vs Lasso su OOF aggregate")
    print("--------------------------------------")

    print(
        evaluation_df[
            [
                "model_name",
                "precision",
                "recall",
                "specificity",
                "f1",
                "roc_auc",
                "brier",
                "log_loss",
                "calibration_intercept",
                "calibration_slope",
                "ece_10_bins",
            ]
        ].sort_values(
            "brier"
        ).to_string(index=False)
    )

    print()
    print("Bootstrap appaiato Ridge meno Lasso")
    print("-------------------")
    print(
        bootstrap_summary_df.to_string(
            index=False
        )
    )


if __name__ == "__main__":
    main()
