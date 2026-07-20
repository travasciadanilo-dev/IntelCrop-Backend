import hashlib
import json
import os
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato.")


MODEL_VERSION = (
    "regional_reliability_score_exp_v4_combined_ridge"
)

MODEL_DIR = Path("models") / MODEL_VERSION

MODEL_PATH = MODEL_DIR / "model.joblib"
METADATA_PATH = MODEL_DIR / "metadata.json"

OUTPUT_PATH = Path(
    "outputs/regional_reliability_v4_final_training_predictions.csv"
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        for block in iter(
            lambda: file_handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


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

    for column in [
        "large_polygon_flag",
        "small_candidate_flag",
        "complex_boundary_flag",
    ]:
        result[column] = result[column].astype(int)

    if result[FEATURES].isna().any().any():
        raise RuntimeError(
            "Feature ingegnerizzate incomplete."
        )

    return result


def classify_probability(probability: float) -> str:
    if probability >= 0.82:
        return "very_high"

    if probability >= 0.77:
        return "high"

    if probability >= 0.61:
        return "compatible"

    return "low"


def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Modello non trovato: {MODEL_PATH}"
        )

    if not METADATA_PATH.exists():
        raise FileNotFoundError(
            f"Metadata non trovati: {METADATA_PATH}"
        )

    metadata = json.loads(
        METADATA_PATH.read_text(encoding="utf-8")
    )

    expected_hash = metadata[
        "files"
    ]["model_sha256"]

    actual_hash = sha256_file(MODEL_PATH)

    if actual_hash != expected_hash:
        raise RuntimeError(
            "Hash SHA256 del modello non coerente."
        )

    if metadata["model_version"] != MODEL_VERSION:
        raise RuntimeError(
            "model_version non coerente."
        )

    metadata_features = metadata[
        "features"
    ]["all"]

    if metadata_features != FEATURES:
        raise RuntimeError(
            "Ordine delle feature non coerente "
            "con metadata.json."
        )

    pipeline = joblib.load(MODEL_PATH)

    df = engineer_features(load_data())

    probabilities = pipeline.predict_proba(
        df[FEATURES]
    )[:, 1]

    if len(probabilities) != 406:
        raise RuntimeError(
            "Numero di probabilità inatteso."
        )

    if not np.isfinite(probabilities).all():
        raise RuntimeError(
            "Probabilità non finite."
        )

    if (
        (probabilities < 0.0).any()
        or (probabilities > 1.0).any()
    ):
        raise RuntimeError(
            "Probabilità fuori dall'intervallo [0, 1]."
        )

    output = df[
        [
            "area_id",
            "spatial_validation_zone",
            "target_visual_v2",
        ]
    ].copy()

    output["model_version"] = MODEL_VERSION
    output["probability"] = probabilities

    output["reliability_class"] = [
        classify_probability(float(value))
        for value in probabilities
    ]

    output.to_csv(
        OUTPUT_PATH,
        index=False,
    )

    class_counts = (
        output["reliability_class"]
        .value_counts()
        .reindex(
            [
                "low",
                "compatible",
                "high",
                "very_high",
            ],
            fill_value=0,
        )
    )

    print("Regional reliability v4 artifact verification")
    print("---------------------------------------------")
    print(f"model_version: {MODEL_VERSION}")
    print(f"rows: {len(output)}")
    print(f"features: {len(FEATURES)}")
    print(f"selected_C: {metadata['classifier']['selected_c']}")
    print(f"sha256_valid: {actual_hash == expected_hash}")
    print(
        f"probability_min: {probabilities.min():.6f}"
    )
    print(
        f"probability_mean: {probabilities.mean():.6f}"
    )
    print(
        f"probability_max: {probabilities.max():.6f}"
    )

    print()
    print("Full-fit training class distribution")
    print(class_counts.to_string())

    print()
    print(f"Output: {OUTPUT_PATH}")
    print()
    print(
        "Nota: queste sono predizioni in-sample "
        "del modello finale e non metriche di validazione."
    )


if __name__ == "__main__":
    main()
