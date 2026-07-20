import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extras import Json


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato.")


MODEL_VERSION = (
    "regional_reliability_score_exp_v4_combined_ridge"
)

MODEL_NAME = (
    "Regional olive reliability score experimental v4 "
    "combined ridge"
)

MODEL_STATUS = "experimental_validated_not_promoted"

MODEL_DIR = Path("models") / MODEL_VERSION

METADATA_PATH = MODEL_DIR / "metadata.json"
COEFFICIENTS_PATH = MODEL_DIR / "coefficients.csv"

CALIBRATION_SUMMARY_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_calibration_summary.csv"
)

LOZO_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_lozo.csv"
)

AGGREGATED_PREDICTIONS_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_aggregated_predictions.csv"
)

THRESHOLDS = [
    {
        "class_code": "low",
        "class_label_it": "Bassa affidabilità",
        "min_score": 0.00,
        "max_score": 0.61,
        "class_rank": 1,
        "recommended_use": (
            "Area non prioritaria. Escludere dal catalogo operativo "
            "oppure sottoporre a verifica specifica se strategica."
        ),
    },
    {
        "class_code": "compatible",
        "class_label_it": "Compatibile",
        "min_score": 0.61,
        "max_score": 0.77,
        "class_rank": 2,
        "recommended_use": (
            "Area compatibile con l'identità olivicola attesa; "
            "utilizzabile per screening territoriale."
        ),
    },
    {
        "class_code": "high",
        "class_label_it": "Alta affidabilità",
        "min_score": 0.77,
        "max_score": 0.82,
        "class_rank": 3,
        "recommended_use": (
            "Area candidata ad alta affidabilità per catalogo "
            "operativo, mantenendo tracciabilità e limiti metodologici."
        ),
    },
    {
        "class_code": "very_high",
        "class_label_it": "Molto alta affidabilità",
        "min_score": 0.82,
        "max_score": 1.00,
        "class_rank": 4,
        "recommended_use": (
            "Area candidata prioritaria ad affidabilità molto alta. "
            "Non equivale a validazione catastale o agronomica."
        ),
    },
]


LIMITATIONS = (
    "Modello sperimentale addestrato su 406 aree sottoposte a "
    "revisione visuale, con 319 positivi e 87 negativi. "
    "Le probabilità rappresentano score di affidabilità operativa "
    "e non una validazione assoluta dell'uso del suolo. "
    "Le soglie derivano da repeated nested cross-validation e "
    "leave-one-zone-out. Il modello non è ancora validato su un "
    "campione esterno indipendente e non è promosso nel catalogo attivo."
)


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {path}")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [json_safe(item) for item in value]

    if isinstance(value, tuple):
        return [json_safe(item) for item in value]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return float(value)

    if isinstance(value, np.ndarray):
        return value.tolist()

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value


def table_columns(
    conn,
    table_name: str,
) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = %s
            ORDER BY ordinal_position;
            """,
            (table_name,),
        )

        rows = cur.fetchall()

    if not rows:
        raise RuntimeError(
            f"Tabella non trovata: {table_name}"
        )

    return {
        row[0]: {
            "data_type": row[1],
            "nullable": row[2] == "YES",
            "default": row[3],
        }
        for row in rows
    }


def insert_row(
    conn,
    table_name: str,
    values: dict[str, Any],
    conflict_columns: list[str],
) -> None:
    schema = table_columns(conn, table_name)

    filtered = {
        key: json_safe(value)
        for key, value in values.items()
        if key in schema
    }

    missing_required = []

    for column, definition in schema.items():
        if column == "id":
            continue

        if column in filtered:
            continue

        if definition["nullable"]:
            continue

        if definition["default"] is not None:
            continue

        missing_required.append(column)

    if missing_required:
        raise RuntimeError(
            f"{table_name}: colonne obbligatorie non valorizzate: "
            + ", ".join(missing_required)
        )

    columns = list(filtered.keys())

    if not columns:
        raise RuntimeError(
            f"Nessuna colonna compatibile per {table_name}."
        )

    assignments = [
        sql.SQL("{} = EXCLUDED.{}").format(
            sql.Identifier(column),
            sql.Identifier(column),
        )
        for column in columns
        if column not in conflict_columns
        and column not in {"id", "created_at"}
    ]

    query = sql.SQL(
        """
        INSERT INTO {table} ({columns})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_columns})
        DO UPDATE SET {assignments};
        """
    ).format(
        table=sql.Identifier(table_name),
        columns=sql.SQL(", ").join(
            sql.Identifier(column)
            for column in columns
        ),
        placeholders=sql.SQL(", ").join(
            sql.Placeholder()
            for _ in columns
        ),
        conflict_columns=sql.SQL(", ").join(
            sql.Identifier(column)
            for column in conflict_columns
        ),
        assignments=(
            sql.SQL(", ").join(assignments)
            if assignments
            else sql.SQL(
                "{} = EXCLUDED.{}"
            ).format(
                sql.Identifier(conflict_columns[0]),
                sql.Identifier(conflict_columns[0]),
            )
        ),
    )

    parameters = []

    for column in columns:
        value = filtered[column]

        if schema[column]["data_type"] in {
            "json",
            "jsonb",
        } and value is not None:
            parameters.append(Json(value))
        else:
            parameters.append(value)

    with conn.cursor() as cur:
        cur.execute(query, parameters)


def load_inputs():
    for path in [
        METADATA_PATH,
        COEFFICIENTS_PATH,
        CALIBRATION_SUMMARY_PATH,
        LOZO_PATH,
        AGGREGATED_PREDICTIONS_PATH,
    ]:
        require_file(path)

    metadata = json.loads(
        METADATA_PATH.read_text(encoding="utf-8")
    )

    coefficients = pd.read_csv(COEFFICIENTS_PATH)

    calibration_summary = pd.read_csv(
        CALIBRATION_SUMMARY_PATH
    )

    ridge_summary = calibration_summary[
        calibration_summary["model_name"]
        == "combined_ridge"
    ].copy()

    if len(ridge_summary) != 1:
        raise RuntimeError(
            "Riepilogo combined_ridge assente o duplicato."
        )

    lozo = pd.read_csv(LOZO_PATH)

    lozo = lozo[
        lozo["model_name"] == "combined_ridge"
    ].copy()

    if len(lozo) != 3:
        raise RuntimeError(
            f"Attesi 3 risultati LOZO, trovati {len(lozo)}."
        )

    predictions = pd.read_csv(
        AGGREGATED_PREDICTIONS_PATH
    )

    predictions = predictions[
        predictions["model_name"] == "combined_ridge"
    ].copy()

    if len(predictions) != 406:
        raise RuntimeError(
            f"Attese 406 predizioni, trovate {len(predictions)}."
        )

    if predictions["area_id"].duplicated().any():
        raise RuntimeError(
            "Predizioni aggregate con area_id duplicati."
        )

    return (
        metadata,
        coefficients,
        ridge_summary.iloc[0].to_dict(),
        lozo,
        predictions,
    )


def create_calibration_bins(
    predictions: pd.DataFrame,
) -> pd.DataFrame:
    result = predictions.copy()

    edges = np.linspace(0.0, 1.0, 11)

    result["bin_id"] = np.digitize(
        result["probability"],
        edges[1:-1],
        right=True,
    ) + 1

    rows = []

    for bin_id in range(1, 11):
        group = result[result["bin_id"] == bin_id]

        score_min = float(edges[bin_id - 1])
        score_max = float(edges[bin_id])

        if group.empty:
            mean_score = None
            observed_rate = None
            n_samples = 0
        else:
            mean_score = float(
                group["probability"].mean()
            )
            observed_rate = float(
                group["target"].mean()
            )
            n_samples = int(len(group))

        rows.append(
            {
                "bin_id": bin_id,
                "score_min": score_min,
                "score_max": score_max,
                "n_samples": n_samples,
                "mean_predicted_score": mean_score,
                "predicted_mean": mean_score,
                "mean_predicted_probability": mean_score,
                "observed_positive_rate": observed_rate,
                "observed_rate": observed_rate,
            }
        )

    return pd.DataFrame(rows)


def register_model_version(
    conn,
    metadata: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    values = {
        "model_version": MODEL_VERSION,
        "model_name": "regional_reliability_score",
        "model_family": (
            "standardized_logistic_regression_ridge"
        ),
        "purpose": (
            "Score regionale sperimentale di affidabilità "
            "per il catalogo territoriale olivicolo."
        ),
        "source_layer_version": (
            "cut_calabria_v1"
        ),
        "status": "experimental",
        "training_sample_n": 406,
        "valid_label_n": 406,
        "tp": int(summary["tp"]),
        "fp": int(summary["fp"]),
        "fn": int(summary["fn"]),
        "tn": int(summary["tn"]),
        "precision_value": float(
            summary["precision"]
        ),
        "recall_value": float(summary["recall"]),
        "specificity_value": float(
            summary["specificity"]
        ),
        "f1_score": float(summary["f1"]),
        "calibration_notes": (
            "Repeated nested stratified CV, confronto "
            "balanced/unweighted, confronto Ridge/Lasso, "
            "leave-one-zone-out e calibrazione OOF aggregata."
        ),
        "limitations": LIMITATIONS,
        "metadata": {
            **metadata,
            "registry_status": MODEL_STATUS,
            "official_validation_metrics_source": (
                "aggregated repeated nested CV predictions"
            ),
        },
    }

    insert_row(
        conn,
        "model_versions",
        values,
        ["model_version"],
    )


def register_model_run(
    conn,
    metadata: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    classifier = metadata["classifier"]

    values = {
        "model_version": MODEL_VERSION,
        "model_name": MODEL_NAME,
        "feature_matrix_version": (
            "regional_feature_matrix_training_v2_combined"
        ),
        "source_layer_version": (
            "cut_calabria_v1"
        ),
        "algorithm": (
            "standardized_logistic_regression"
        ),
        "penalty": "l2",
        "class_weight": "none",
        "training_n": 406,
        "positive_n": 319,
        "negative_n": 87,
        "uncertain_n": 94,
        "n_features": 20,
        "repeated_cv_folds": 5,
        "repeated_cv_repeats": 10,
        "bootstrap_iterations": 2000,
        "spatial_validation_strategy": (
            "leave_one_zone_out"
        ),
        "mean_precision": float(
            summary["precision"]
        ),
        "mean_recall": float(summary["recall"]),
        "mean_specificity": float(
            summary["specificity"]
        ),
        "mean_f1": float(summary["f1"]),
        "mean_accuracy": float(
            summary["accuracy"]
        ),
        "mean_roc_auc": float(
            summary["roc_auc"]
        ),
        "mean_brier_score": float(
            summary["brier"]
        ),
        "calibration_slope": float(
            summary["calibration_slope"]
        ),
        "calibration_intercept": float(
            summary["calibration_intercept"]
        ),
        "status": "experimental",
        "limitations": LIMITATIONS,
        "metadata": {
            **metadata,
            "registry_status": MODEL_STATUS,
            "log_loss": float(summary["log_loss"]),
            "ece_10_bins": float(
                summary["ece_10_bins"]
            ),
            "mean_probability_sd": float(
                summary["mean_probability_sd"]
            ),
            "selected_c": float(
                classifier["selected_c"]
            ),
            "artifact_sha256": metadata[
                "files"
            ]["model_sha256"],
        },
        "model_intercept": float(
            classifier["intercept"]
        ),
    }

    insert_row(
        conn,
        "regional_reliability_model_runs",
        values,
        ["model_version"],
    )


def register_coefficients(
    conn,
    coefficients: pd.DataFrame,
) -> None:
    required = {
        "feature",
        "coefficient_standardized",
        "absolute_coefficient",
        "odds_ratio_per_sd",
    }

    missing = required.difference(
        coefficients.columns
    )

    if missing:
        raise RuntimeError(
            "Colonne coefficienti mancanti: "
            + ", ".join(sorted(missing))
        )

    ordered = coefficients.sort_values(
        "absolute_coefficient",
        ascending=False,
    ).reset_index(drop=True)

    for index, row in ordered.iterrows():
        values = {
            "model_version": MODEL_VERSION,

            "feature_name": str(row["feature"]),
            "feature": str(row["feature"]),

            "coefficient_value": float(
                row["coefficient_standardized"]
            ),
            "coefficient": float(
                row["coefficient_standardized"]
            ),

            "absolute_coefficient": float(
                row["absolute_coefficient"]
            ),
            "abs_coefficient": float(
                row["absolute_coefficient"]
            ),

            "odds_ratio": float(
                row["odds_ratio_per_sd"]
            ),
            "odds_ratio_per_sd": float(
                row["odds_ratio_per_sd"]
            ),

            "importance_rank": index + 1,
            "feature_rank": index + 1,

            "feature_group": (
                "geometry"
                if str(row["feature"]) in {
                    "log_area_ha",
                    "log_perimeter_m",
                    "compactness_raw",
                    "log_n_points",
                    "large_polygon_flag",
                    "small_candidate_flag",
                    "complex_boundary_flag",
                }
                else "spectral"
            ),
        }

        insert_row(
            conn,
            "regional_reliability_model_coefficients",
            values,
            ["model_version", "feature_name"],
        )


def register_thresholds(conn) -> None:
    for threshold in THRESHOLDS:
        values = {
            "model_version": MODEL_VERSION,
            **threshold,
        }

        insert_row(
            conn,
            "regional_reliability_model_thresholds",
            values,
            ["model_version", "class_code"],
        )


def register_spatial_cv(
    conn,
    lozo: pd.DataFrame,
) -> None:
    for _, row in lozo.iterrows():
        values = {
            "model_version": MODEL_VERSION,
            "held_out_zone": str(
                row["held_out_zone"]
            ),
            "n_test": int(row["test_n"]),
            "test_n": int(row["test_n"]),
            "positive_n": int(row["positive_n"]),
            "negative_n": int(row["negative_n"]),

            "precision_value": float(
                row["precision"]
            ),
            "precision": float(row["precision"]),

            "recall_value": float(row["recall"]),
            "recall": float(row["recall"]),

            "specificity_value": float(
                row["specificity"]
            ),
            "specificity": float(
                row["specificity"]
            ),

            "f1_score": float(row["f1"]),
            "f1": float(row["f1"]),

            "accuracy_value": float(
                row["accuracy"]
            ),
            "accuracy": float(row["accuracy"]),

            "roc_auc": float(row["roc_auc"]),
            "brier_score": float(row["brier"]),

            "calibration_intercept": float(
                row["calibration_intercept"]
            ),
            "calibration_slope": float(
                row["calibration_slope"]
            ),

            "validation_strategy": (
                "leave_one_zone_out"
            ),
            "cv_strategy": "leave_one_zone_out",

            "metadata": {
                "best_c": float(row["best_c"]),
                "fixed_l1_ratio": float(
                    row["fixed_l1_ratio"]
                ),
                "ece_10_bins": float(
                    row["ece_10_bins"]
                ),
            },
        }

        insert_row(
            conn,
            "regional_reliability_spatial_cv_results",
            values,
            ["model_version", "held_out_zone"],
        )


def register_calibration_bins(
    conn,
    bins: pd.DataFrame,
) -> None:
    for _, row in bins.iterrows():
        values = {
            "model_version": MODEL_VERSION,
            **row.to_dict(),
        }

        insert_row(
            conn,
            "regional_reliability_calibration_bins",
            values,
            ["model_version", "bin_id"],
        )


def verify_registration(conn) -> None:
    queries = {
        "runs": (
            "regional_reliability_model_runs"
        ),
        "coefficients": (
            "regional_reliability_model_coefficients"
        ),
        "thresholds": (
            "regional_reliability_model_thresholds"
        ),
        "spatial_cv": (
            "regional_reliability_spatial_cv_results"
        ),
        "calibration_bins": (
            "regional_reliability_calibration_bins"
        ),
    }

    counts = {}

    with conn.cursor() as cur:
        for label, table_name in queries.items():
            query = sql.SQL(
                """
                SELECT COUNT(*)
                FROM {table}
                WHERE model_version = %s;
                """
            ).format(
                table=sql.Identifier(table_name)
            )

            cur.execute(query, (MODEL_VERSION,))
            counts[label] = int(cur.fetchone()[0])

    expected = {
        "runs": 1,
        "coefficients": 20,
        "thresholds": 4,
        "spatial_cv": 3,
        "calibration_bins": 10,
    }

    if counts != expected:
        raise RuntimeError(
            f"Conteggi registrazione non coerenti: "
            f"{counts}; attesi {expected}."
        )

    print("Registrazione verificata")
    print("------------------------")

    for key, value in counts.items():
        print(f"{key}: {value}")


def main() -> None:
    (
        metadata,
        coefficients,
        summary,
        lozo,
        predictions,
    ) = load_inputs()

    if metadata["model_version"] != MODEL_VERSION:
        raise RuntimeError(
            "model_version dei metadata non coerente."
        )

    bins = create_calibration_bins(predictions)

    with psycopg2.connect(DATABASE_URL) as conn:
        register_model_version(
            conn,
            metadata,
            summary,
        )

        register_model_run(
            conn,
            metadata,
            summary,
        )

        register_coefficients(
            conn,
            coefficients,
        )

        register_thresholds(conn)

        register_spatial_cv(
            conn,
            lozo,
        )

        register_calibration_bins(
            conn,
            bins,
        )

        verify_registration(conn)

    print()
    print(f"model_version: {MODEL_VERSION}")
    print(f"status: {MODEL_STATUS}")
    print(
        "Il catalogo attivo e il modello v3 "
        "non sono stati modificati."
    )


if __name__ == "__main__":
    main()
