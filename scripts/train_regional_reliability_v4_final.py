import hashlib
import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import psycopg2
import sklearn
from dotenv import load_dotenv
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV, RepeatedStratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato.")


MODEL_VERSION = (
    "regional_reliability_score_exp_v4_combined_ridge"
)

MODEL_STATUS = "experimental_validated_not_promoted"

TRAINING_DATASET_VERSION = (
    "regional_feature_matrix_training_v2_combined"
)

SPECTRAL_DATASET_VERSION = "olive_spectral_qc_v1"

RANDOM_STATE = 42

THRESHOLDS = {
    "screening": 0.61,
    "high": 0.77,
    "very_high": 0.82,
}

C_GRID = [
    0.1,
    0.3,
    1.0,
    3.0,
    10.0,
    30.0,
]

OUTPUT_DIR = Path("models") / MODEL_VERSION
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VALIDATION_SUMMARY_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_calibration_summary.csv"
)

LOZO_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_lozo.csv"
)

THRESHOLDS_LOZO_PATH = Path(
    "outputs/"
    "regional_reliability_v4_ridge_thresholds_lozo.csv"
)


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
            f"Attese 406 righe, trovate {len(df)}."
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

    class_counts = (
        df["target_visual_v2"]
        .value_counts()
        .to_dict()
    )

    if class_counts.get(1) != 319:
        raise RuntimeError(
            f"Positivi inattesi: {class_counts.get(1)}."
        )

    if class_counts.get(0) != 87:
        raise RuntimeError(
            f"Negativi inattesi: {class_counts.get(0)}."
        )

    if df["spatial_validation_zone"].nunique() != 3:
        raise RuntimeError(
            "Numero di zone geografiche inatteso."
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

    if result[FEATURES].isna().any().any():
        raise RuntimeError(
            "Feature ingegnerizzate con valori mancanti."
        )

    return result


def build_pipeline() -> Pipeline:
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
        l1_ratio=0.0,
        class_weight=None,
        max_iter=10000,
        random_state=RANDOM_STATE,
    )

    return Pipeline(
        steps=[
            ("preprocessing", preprocessing),
            ("classifier", classifier),
        ]
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        for block in iter(
            lambda: file_handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            json_safe(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            json_safe(item)
            for item in value
        ]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return float(value)

    if isinstance(value, np.ndarray):
        return value.tolist()

    if pd.isna(value):
        return None

    return value


def load_validation_outputs() -> dict[str, Any]:
    result: dict[str, Any] = {}

    if VALIDATION_SUMMARY_PATH.exists():
        validation = pd.read_csv(
            VALIDATION_SUMMARY_PATH
        )

        ridge = validation[
            validation["model_name"]
            == "combined_ridge"
        ]

        if len(ridge) == 1:
            result["repeated_cv_aggregated"] = (
                ridge.iloc[0].to_dict()
            )

    if LOZO_PATH.exists():
        lozo = pd.read_csv(LOZO_PATH)

        ridge_lozo = lozo[
            lozo["model_name"]
            == "combined_ridge"
        ]

        result["leave_one_zone_out"] = (
            ridge_lozo.to_dict(
                orient="records"
            )
        )

    if THRESHOLDS_LOZO_PATH.exists():
        threshold_lozo = pd.read_csv(
            THRESHOLDS_LOZO_PATH
        )

        result["threshold_validation_lozo"] = (
            threshold_lozo.to_dict(
                orient="records"
            )
        )

    return json_safe(result)


def extract_coefficients(
    fitted_pipeline: Pipeline,
) -> pd.DataFrame:
    classifier = fitted_pipeline.named_steps[
        "classifier"
    ]

    coefficients = classifier.coef_[0]

    if len(coefficients) != len(FEATURES):
        raise RuntimeError(
            "Numero coefficienti non coerente "
            "con il numero di feature."
        )

    result = pd.DataFrame(
        {
            "feature": FEATURES,
            "coefficient_standardized": coefficients,
            "absolute_coefficient": np.abs(
                coefficients
            ),
            "odds_ratio_per_sd": np.exp(
                coefficients
            ),
        }
    )

    return result.sort_values(
        "absolute_coefficient",
        ascending=False,
    )


def main() -> None:
    raw_df = load_data()
    df = engineer_features(raw_df)

    x = df[FEATURES]
    y = df["target_visual_v2"].to_numpy()

    cv = RepeatedStratifiedKFold(
        n_splits=5,
        n_repeats=10,
        random_state=RANDOM_STATE,
    )

    search = GridSearchCV(
        estimator=build_pipeline(),
        param_grid={
            "classifier__C": C_GRID,
        },
        scoring="neg_brier_score",
        cv=cv,
        n_jobs=-1,
        refit=True,
        return_train_score=False,
        error_score="raise",
    )

    search.fit(x, y)

    final_pipeline = search.best_estimator_
    best_c = float(
        search.best_params_["classifier__C"]
    )

    model_path = OUTPUT_DIR / "model.joblib"
    metadata_path = OUTPUT_DIR / "metadata.json"
    coefficients_path = (
        OUTPUT_DIR / "coefficients.csv"
    )
    cv_results_path = (
        OUTPUT_DIR / "hyperparameter_cv_results.csv"
    )
    training_manifest_path = (
        OUTPUT_DIR / "training_manifest.csv"
    )

    joblib.dump(
        final_pipeline,
        model_path,
        compress=3,
    )

    coefficients = extract_coefficients(
        final_pipeline
    )

    coefficients.to_csv(
        coefficients_path,
        index=False,
    )

    cv_results = pd.DataFrame(
        search.cv_results_
    )

    selected_cv_columns = [
        "param_classifier__C",
        "mean_test_score",
        "std_test_score",
        "rank_test_score",
        "mean_fit_time",
        "std_fit_time",
    ]

    cv_results = cv_results[
        selected_cv_columns
    ].copy()

    cv_results = cv_results.rename(
        columns={
            "param_classifier__C": "c",
            "mean_test_score": "mean_neg_brier",
            "std_test_score": "sd_neg_brier",
            "rank_test_score": "rank",
        }
    )

    cv_results["mean_brier"] = (
        -cv_results["mean_neg_brier"]
    )

    cv_results.sort_values(
        "rank"
    ).to_csv(
        cv_results_path,
        index=False,
    )

    training_manifest = raw_df[
        [
            "area_id",
            "spatial_validation_zone",
            "target_visual_v2",
        ]
    ].copy()

    training_manifest.to_csv(
        training_manifest_path,
        index=False,
    )

    trained_at = datetime.now(
        timezone.utc
    ).isoformat()

    classifier = final_pipeline.named_steps[
        "classifier"
    ]

    metadata = {
        "model_version": MODEL_VERSION,
        "model_status": MODEL_STATUS,
        "trained_at_utc": trained_at,
        "model_family": (
            "standardized_logistic_regression_ridge"
        ),
        "probability_target": (
            "probability of target_visual_v2 = 1"
        ),
        "training_dataset": {
            "view": TRAINING_DATASET_VERSION,
            "spectral_version": (
                SPECTRAL_DATASET_VERSION
            ),
            "n": int(len(df)),
            "positive_n": int(y.sum()),
            "negative_n": int(
                len(y) - y.sum()
            ),
            "zones": sorted(
                df[
                    "spatial_validation_zone"
                ].unique().tolist()
            ),
        },
        "features": {
            "geometry": GEOMETRY_FEATURES,
            "spectral": SPECTRAL_FEATURES,
            "all": FEATURES,
        },
        "feature_engineering": {
            "log_area_ha": "log1p(area_ha_raw)",
            "log_perimeter_m": (
                "log1p(perimeter_m_raw)"
            ),
            "log_n_points": "log1p(n_points)",
            "log_n_observations": (
                "log1p(n_observations)"
            ),
            "ndvi_iqr": "ndvi_p75 - ndvi_p25",
            "evi_iqr": "evi_p75 - evi_p25",
            "ndmi_iqr": "ndmi_p75 - ndmi_p25",
            "bsi_iqr": "bsi_p75 - bsi_p25",
        },
        "preprocessing": {
            "imputation": "median inside pipeline",
            "scaling": (
                "StandardScaler inside pipeline"
            ),
        },
        "classifier": {
            "solver": "saga",
            "penalty_equivalent": "ridge",
            "l1_ratio": 0.0,
            "class_weight": None,
            "selected_c": best_c,
            "intercept": float(
                classifier.intercept_[0]
            ),
            "max_iter": 10000,
        },
        "hyperparameter_selection": {
            "criterion": "negative Brier score",
            "cv": (
                "RepeatedStratifiedKFold "
                "5 folds x 10 repeats"
            ),
            "c_grid": C_GRID,
            "random_state": RANDOM_STATE,
            "best_mean_cv_brier": float(
                -search.best_score_
            ),
        },
        "thresholds": {
            "low": {
                "minimum": 0.0,
                "maximum_exclusive": (
                    THRESHOLDS["screening"]
                ),
            },
            "compatible": {
                "minimum": (
                    THRESHOLDS["screening"]
                ),
                "maximum_exclusive": (
                    THRESHOLDS["high"]
                ),
            },
            "high": {
                "minimum": THRESHOLDS["high"],
                "maximum_exclusive": (
                    THRESHOLDS["very_high"]
                ),
            },
            "very_high": {
                "minimum": (
                    THRESHOLDS["very_high"]
                ),
                "maximum_inclusive": 1.0,
            },
        },
        "validation": load_validation_outputs(),
        "software": {
            "python": (
                f"{os.sys.version_info.major}."
                f"{os.sys.version_info.minor}."
                f"{os.sys.version_info.micro}"
            ),
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "joblib": joblib.__version__,
        },
        "files": {
            "model": model_path.name,
            "coefficients": coefficients_path.name,
            "hyperparameter_cv_results": (
                cv_results_path.name
            ),
            "training_manifest": (
                training_manifest_path.name
            ),
        },
        "limitations": [
            (
                "Experimental model; not externally "
                "validated."
            ),
            (
                "Thresholds derived from internal "
                "OOF and leave-one-zone-out validation."
            ),
            (
                "Current training sample contains "
                "406 visually reviewed areas."
            ),
            (
                "Performance estimates must come "
                "from prior OOF/LOZO validation, "
                "not from the final full-data fit."
            ),
        ],
    }

    metadata = json_safe(metadata)

    metadata_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    artifact_hash = sha256_file(model_path)

    metadata["files"]["model_sha256"] = (
        artifact_hash
    )

    metadata_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("Regional reliability v4 final training")
    print("--------------------------------------")
    print(f"model_version: {MODEL_VERSION}")
    print(f"status: {MODEL_STATUS}")
    print(f"training_n: {len(df)}")
    print(f"positive_n: {int(y.sum())}")
    print(
        f"negative_n: {int(len(y) - y.sum())}"
    )
    print(f"selected_C: {best_c}")
    print(
        "selection_cv_brier: "
        f"{-search.best_score_:.6f}"
    )
    print(f"n_features: {len(FEATURES)}")
    print(f"model_path: {model_path}")
    print(f"metadata_path: {metadata_path}")
    print(
        f"model_sha256: {artifact_hash}"
    )

    print()
    print("Top standardized coefficients")
    print(
        coefficients.head(20).to_string(
            index=False
        )
    )


if __name__ == "__main__":
    main()
