from __future__ import annotations

import hashlib
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values


load_dotenv()


MODEL_VERSION = "regional_reliability_score_exp_v4_combined_ridge"
MODEL_PATH = Path(
    "models"
) / MODEL_VERSION / "model.joblib"

EXPECTED_MODEL_SHA256 = (
    "ba183b5b7e543b9320eb5203c003934826124b29c9f3ded01447aacf70e8523e"
)

VIEW_VERSION = "olive_candidate_pool_v2"
SPECTRAL_QC_VERSION = "olive_spectral_qc_v1"

SCORE_TYPE = "experimental_regional_reliability_probability_v4"
MODEL_STATUS = "experimental_validated_not_promoted"

LIMITATIONS = (
    "Modello sperimentale addestrato su 406 aree sottoposte a revisione "
    "visiva interna. Non validato esternamente e non promosso come modello "
    "operativo. Le probabilità rappresentano la compatibilità con "
    "target_visual_v2 = 1."
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

SOURCE_SQL = """
SELECT
    p.area_id::text AS area_id,
    p.source_geometry_id::uuid AS source_geometry_id,
    p.spatial_validation_zone,

    p.area_ha_raw,
    p.perimeter_m_raw,
    p.compactness_raw,
    p.n_points,

    p.large_polygon_flag,
    p.small_candidate_flag,
    p.complex_boundary_flag,

    s.n_observations,

    s.ndvi_median,
    s.ndvi_p25,
    s.ndvi_p75,
    s.ndvi_stddev,

    s.evi_median,
    s.evi_p25,
    s.evi_p75,
    s.evi_stddev,

    s.ndmi_median,
    s.ndmi_p25,
    s.ndmi_p75,
    s.ndmi_stddev,

    s.bsi_median,
    s.bsi_p25,
    s.bsi_p75,
    s.bsi_stddev,

    s.spectral_flag,
    s.usable_for_baseline_spectral

FROM olive_candidate_pool_v2_review_priority p

JOIN landcover_subtype_spectral_qc s
    ON s.source_geometry_id = p.source_geometry_id::uuid
   AND s.spectral_qc_version = %s

ORDER BY p.area_id;
"""

UPSERT_SQL = """
INSERT INTO regional_reliability_scores_v4_diagnostic (
    area_id,
    source_geometry_id,
    spatial_validation_zone,

    model_version,
    score_type,
    view_version,
    spectral_qc_version,
    artifact_sha256,

    experimental_reliability_score_v4,
    experimental_reliability_class_v4,
    experimental_reliability_label_v4,

    model_status,
    limitations,
    feature_warning,

    spectral_flag,
    usable_for_baseline_spectral,

    log_area_ha,
    log_perimeter_m,
    compactness_raw,
    log_n_points,

    large_polygon_flag,
    small_candidate_flag,
    complex_boundary_flag,

    log_n_observations,

    ndvi_median,
    ndvi_iqr,
    ndvi_stddev,

    evi_median,
    evi_iqr,
    evi_stddev,

    ndmi_median,
    ndmi_iqr,
    ndmi_stddev,

    bsi_median,
    bsi_iqr,
    bsi_stddev,

    created_at
)
VALUES %s
ON CONFLICT (area_id, model_version)
DO UPDATE SET
    source_geometry_id =
        EXCLUDED.source_geometry_id,
    spatial_validation_zone =
        EXCLUDED.spatial_validation_zone,

    score_type =
        EXCLUDED.score_type,
    view_version =
        EXCLUDED.view_version,
    spectral_qc_version =
        EXCLUDED.spectral_qc_version,
    artifact_sha256 =
        EXCLUDED.artifact_sha256,

    experimental_reliability_score_v4 =
        EXCLUDED.experimental_reliability_score_v4,
    experimental_reliability_class_v4 =
        EXCLUDED.experimental_reliability_class_v4,
    experimental_reliability_label_v4 =
        EXCLUDED.experimental_reliability_label_v4,

    model_status =
        EXCLUDED.model_status,
    limitations =
        EXCLUDED.limitations,
    feature_warning =
        EXCLUDED.feature_warning,

    spectral_flag =
        EXCLUDED.spectral_flag,
    usable_for_baseline_spectral =
        EXCLUDED.usable_for_baseline_spectral,

    log_area_ha =
        EXCLUDED.log_area_ha,
    log_perimeter_m =
        EXCLUDED.log_perimeter_m,
    compactness_raw =
        EXCLUDED.compactness_raw,
    log_n_points =
        EXCLUDED.log_n_points,

    large_polygon_flag =
        EXCLUDED.large_polygon_flag,
    small_candidate_flag =
        EXCLUDED.small_candidate_flag,
    complex_boundary_flag =
        EXCLUDED.complex_boundary_flag,

    log_n_observations =
        EXCLUDED.log_n_observations,

    ndvi_median =
        EXCLUDED.ndvi_median,
    ndvi_iqr =
        EXCLUDED.ndvi_iqr,
    ndvi_stddev =
        EXCLUDED.ndvi_stddev,

    evi_median =
        EXCLUDED.evi_median,
    evi_iqr =
        EXCLUDED.evi_iqr,
    evi_stddev =
        EXCLUDED.evi_stddev,

    ndmi_median =
        EXCLUDED.ndmi_median,
    ndmi_iqr =
        EXCLUDED.ndmi_iqr,
    ndmi_stddev =
        EXCLUDED.ndmi_stddev,

    bsi_median =
        EXCLUDED.bsi_median,
    bsi_iqr =
        EXCLUDED.bsi_iqr,
    bsi_stddev =
        EXCLUDED.bsi_stddev,

    created_at = now();
"""


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        for block in iter(
            lambda: file_handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def load_source_dataframe(conn) -> pd.DataFrame:
    with conn.cursor() as cursor:
        cursor.execute(
            SOURCE_SQL,
            (SPECTRAL_QC_VERSION,),
        )

        columns = [
            description.name
            for description in cursor.description
        ]

        rows = cursor.fetchall()

    return pd.DataFrame(
        rows,
        columns=columns,
    )


def engineer_features(source: pd.DataFrame) -> pd.DataFrame:
    result = source.copy()

    positive_columns = [
        "area_ha_raw",
        "perimeter_m_raw",
        "n_points",
        "n_observations",
    ]

    for column in positive_columns:
        numeric = pd.to_numeric(
            result[column],
            errors="coerce",
        )

        if numeric.isna().any():
            raise RuntimeError(
                f"Valori mancanti o non numerici in {column}."
            )

        if (numeric < 0).any():
            raise RuntimeError(
                f"Valori negativi non validi in {column}."
            )

        result[column] = numeric

    result["log_area_ha"] = np.log1p(
        result["area_ha_raw"]
    )

    result["log_perimeter_m"] = np.log1p(
        result["perimeter_m_raw"]
    )

    result["log_n_points"] = np.log1p(
        result["n_points"]
    )

    result["log_n_observations"] = np.log1p(
        result["n_observations"]
    )

    for index_name in [
        "ndvi",
        "evi",
        "ndmi",
        "bsi",
    ]:
        result[f"{index_name}_iqr"] = (
            pd.to_numeric(
                result[f"{index_name}_p75"],
                errors="coerce",
            )
            - pd.to_numeric(
                result[f"{index_name}_p25"],
                errors="coerce",
            )
        )

    numeric_features = [
        "compactness_raw",
        "ndvi_median",
        "ndvi_stddev",
        "evi_median",
        "evi_stddev",
        "ndmi_median",
        "ndmi_stddev",
        "bsi_median",
        "bsi_stddev",
    ]

    for column in numeric_features:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce",
        )

    boolean_columns = [
        "large_polygon_flag",
        "small_candidate_flag",
        "complex_boundary_flag",
    ]

    for column in boolean_columns:
        if result[column].isna().any():
            raise RuntimeError(
                f"Flag mancante in {column}."
            )

        result[column] = (
            result[column]
            .astype(bool)
            .astype(int)
        )

    missing_counts = (
        result[FEATURES]
        .isna()
        .sum()
    )

    missing_counts = missing_counts[
        missing_counts > 0
    ]

    if not missing_counts.empty:
        raise RuntimeError(
            "Feature incomplete:\n"
            + missing_counts.to_string()
        )

    if not np.isfinite(
        result[FEATURES].to_numpy(
            dtype=float
        )
    ).all():
        raise RuntimeError(
            "Sono presenti feature non finite."
        )

    return result


def classify_probability(
    probability: float,
) -> tuple[str, str]:
    if probability < 0.61:
        return (
            "low",
            "Bassa compatibilità",
        )

    if probability < 0.77:
        return (
            "compatible",
            "Compatibile",
        )

    if probability < 0.82:
        return (
            "high",
            "Alta compatibilità",
        )

    return (
        "very_high",
        "Compatibilità molto alta",
    )


def build_feature_warning(
    spectral_flag: str,
    usable_for_baseline_spectral: bool,
) -> str | None:
    if (
        spectral_flag == "weak"
        or not usable_for_baseline_spectral
    ):
        return (
            "Qualità spettrale debole: risultato "
            "utilizzabile solo in modalità diagnostica "
            "e da sottoporre a verifica."
        )

    if spectral_flag == "moderate":
        return (
            "Qualità spettrale moderata: interpretare "
            "la probabilità con cautela."
        )

    return None


def verify_model(model) -> None:
    model_features = list(
        model.feature_names_in_
    )

    if model_features != FEATURES:
        raise RuntimeError(
            "Ordine delle feature del modello non valido.\n"
            f"Atteso: {FEATURES}\n"
            f"Trovato: {model_features}"
        )

    classes = [
        int(value)
        for value in model.classes_
    ]

    if classes != [0, 1]:
        raise RuntimeError(
            f"Classi del modello non valide: {classes}"
        )


def create_output_rows(
    data: pd.DataFrame,
    probabilities: np.ndarray,
    artifact_sha256: str,
) -> list[tuple]:
    output_rows: list[tuple] = []

    for row, probability in zip(
        data.itertuples(index=False),
        probabilities,
        strict=True,
    ):
        probability_value = float(probability)

        reliability_class, reliability_label = (
            classify_probability(
                probability_value
            )
        )

        warning = build_feature_warning(
            spectral_flag=str(
                row.spectral_flag
            ),
            usable_for_baseline_spectral=bool(
                row.usable_for_baseline_spectral
            ),
        )

        output_rows.append(
            (
                str(row.area_id),
                row.source_geometry_id,
                str(row.spatial_validation_zone),

                MODEL_VERSION,
                SCORE_TYPE,
                VIEW_VERSION,
                SPECTRAL_QC_VERSION,
                artifact_sha256,

                probability_value,
                reliability_class,
                reliability_label,

                MODEL_STATUS,
                LIMITATIONS,
                warning,

                str(row.spectral_flag),
                bool(
                    row.usable_for_baseline_spectral
                ),

                float(row.log_area_ha),
                float(row.log_perimeter_m),
                float(row.compactness_raw),
                float(row.log_n_points),

                int(row.large_polygon_flag),
                int(row.small_candidate_flag),
                int(row.complex_boundary_flag),

                float(row.log_n_observations),

                float(row.ndvi_median),
                float(row.ndvi_iqr),
                float(row.ndvi_stddev),

                float(row.evi_median),
                float(row.evi_iqr),
                float(row.evi_stddev),

                float(row.ndmi_median),
                float(row.ndmi_iqr),
                float(row.ndmi_stddev),

                float(row.bsi_median),
                float(row.bsi_iqr),
                float(row.bsi_stddev),

                None,
            )
        )

    return output_rows


def write_scores(
    conn,
    rows: list[tuple],
) -> None:
    template = """
    (
        %s, %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s,
        %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        COALESCE(%s, now())
    )
    """

    with conn.cursor() as cursor:
        execute_values(
            cursor,
            UPSERT_SQL,
            rows,
            template=template,
            page_size=1000,
        )


def print_summary(
    conn,
) -> None:
    summary_sql = """
    SELECT
        experimental_reliability_class_v4,
        COUNT(*) AS n,
        ROUND(
            AVG(
                experimental_reliability_score_v4
            )::numeric,
            4
        ) AS mean_score,
        ROUND(
            MIN(
                experimental_reliability_score_v4
            )::numeric,
            4
        ) AS min_score,
        ROUND(
            MAX(
                experimental_reliability_score_v4
            )::numeric,
            4
        ) AS max_score
    FROM regional_reliability_scores_v4_diagnostic
    WHERE model_version = %s
    GROUP BY experimental_reliability_class_v4
    ORDER BY
        CASE experimental_reliability_class_v4
            WHEN 'low' THEN 1
            WHEN 'compatible' THEN 2
            WHEN 'high' THEN 3
            WHEN 'very_high' THEN 4
        END;
    """

    with conn.cursor() as cursor:
        cursor.execute(
            summary_sql,
            (MODEL_VERSION,),
        )

        rows = cursor.fetchall()

    print()
    print("Distribuzione score v4")
    print("-----------------------")

    for (
        reliability_class,
        n,
        mean_score,
        min_score,
        max_score,
    ) in rows:
        print(
            f"{reliability_class:12s} "
            f"n={n:6d} "
            f"mean={mean_score} "
            f"min={min_score} "
            f"max={max_score}"
        )


def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Modello non trovato: {MODEL_PATH}"
        )

    artifact_sha256 = sha256_file(
        MODEL_PATH
    )

    if (
        artifact_sha256.lower()
        != EXPECTED_MODEL_SHA256.lower()
    ):
        raise RuntimeError(
            "Hash del modello non valido.\n"
            f"Atteso:  {EXPECTED_MODEL_SHA256}\n"
            f"Trovato: {artifact_sha256}"
        )

    model = joblib.load(
        MODEL_PATH
    )

    verify_model(model)

    conn = database_connection()

    try:
        source = load_source_dataframe(
            conn
        )

        print(
            f"Aree sorgente caricate: "
            f"{len(source)}"
        )

        if len(source) != 40261:
            raise RuntimeError(
                "Numero inatteso di aree sorgente: "
                f"{len(source)}. Attese: 40261."
            )

        if source["area_id"].duplicated().any():
            raise RuntimeError(
                "Sono presenti area_id duplicati."
            )

        engineered = engineer_features(
            source
        )

        probabilities = model.predict_proba(
            engineered[FEATURES]
        )[:, 1]

        if not np.isfinite(
            probabilities
        ).all():
            raise RuntimeError(
                "Probabilità non finite prodotte "
                "dal modello."
            )

        if (
            (probabilities < 0.0).any()
            or (probabilities > 1.0).any()
        ):
            raise RuntimeError(
                "Probabilità fuori dall'intervallo "
                "[0, 1]."
            )

        output_rows = create_output_rows(
            engineered,
            probabilities,
            artifact_sha256,
        )

        write_scores(
            conn,
            output_rows,
        )

        conn.commit()

        print(
            f"Righe v4 registrate: "
            f"{len(output_rows)}"
        )

        print(
            f"Artifact SHA-256: "
            f"{artifact_sha256}"
        )

        print_summary(
            conn
        )

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
