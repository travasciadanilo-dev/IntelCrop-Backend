import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

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
from sklearn.model_selection import RepeatedStratifiedKFold


load_dotenv()


NUMERIC_FEATURES = [
    "area_ha",
    "compactness",
    "n_points",
    "n_parts",
    "qc_score",
    "n_observations",
    "ndvi_median",
    "evi_median",
    "ndmi_median",
    "bsi_median",
]

BOOLEAN_FEATURES = [
    "geometric_usable_for_baseline",
    "context_component_pass",
    "spectral_component_pass",
]

CATEGORICAL_FEATURES = [
    "artificial_flag",
    "spectral_flag",
    "data_availability_class",
    "geometry_component_class",
]

MODEL_NAME = "regional_reliability_score"
FEATURE_MATRIX_VERSION = "area_feature_matrix_v1"
SOURCE_LAYER_VERSION = "cut_calabria_v1"


def clean_float(value, digits=6):
    if value is None:
        return None

    value = float(value)

    if not math.isfinite(value):
        return None

    return round(value, digits)


def parse_bool(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    value = str(value).strip().lower()

    if value in {"true", "t", "1", "yes", "y"}:
        return True

    if value in {"false", "f", "0", "no", "n"}:
        return False

    return None


def table_exists(cur, table_name):
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
        );
        """,
        (table_name,),
    )
    return bool(cur.fetchone()[0])


def fetch_training_rows(cur):
    cur.execute(
        """
        SELECT
            area_id,
            binary_visual_label,

            area_ha,
            compactness,
            n_points,
            n_parts,
            qc_score,

            geometric_usable_for_baseline,
            context_component_pass,
            spectral_component_pass,

            artificial_flag,
            spectral_flag,
            data_availability_class,
            geometry_component_class,

            n_observations,
            ndvi_median,
            evi_median,
            ndmi_median,
            bsi_median,

            spatial_validation_zone
        FROM area_feature_matrix_training_v1
        WHERE binary_visual_label IS NOT NULL
        ORDER BY area_id;
        """
    )

    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    if not rows:
        raise RuntimeError(
            "area_feature_matrix_training_v1 non contiene righe valide. "
            "Applicare prima 013_area_feature_matrix.sql."
        )

    return rows


def fetch_uncertain_n(cur):
    cur.execute(
        """
        SELECT COUNT(*)
        FROM area_feature_matrix_uncertain_v1;
        """
    )
    return int(cur.fetchone()[0] or 0)


def median_or_zero(values):
    valid = [float(v) for v in values if v is not None and math.isfinite(float(v))]

    if not valid:
        return 0.0

    return float(np.median(valid))


def fit_preprocessor(rows):
    numeric_medians = {}

    for feature in NUMERIC_FEATURES:
        numeric_medians[feature] = median_or_zero([row.get(feature) for row in rows])

    categories = {}
    for feature in CATEGORICAL_FEATURES:
        observed = set()

        for row in rows:
            value = row.get(feature)
            observed.add("missing" if value is None else str(value))

        observed.add("missing")
        categories[feature] = sorted(observed)

    raw_matrix, feature_names = build_raw_matrix(rows, numeric_medians, categories)

    means = raw_matrix.mean(axis=0)

    stds = raw_matrix.std(axis=0)
    stds[stds == 0] = 1.0

    return {
        "numeric_medians": numeric_medians,
        "categories": categories,
        "feature_names": feature_names,
        "means": means,
        "stds": stds,
    }


def build_raw_matrix(rows, numeric_medians, categories):
    matrix = []
    feature_names = None

    for row in rows:
        values = []
        names = []

        for feature in NUMERIC_FEATURES:
            raw_value = row.get(feature)
            is_missing = raw_value is None

            value = numeric_medians[feature] if is_missing else float(raw_value)

            values.append(value)
            names.append(feature)

            values.append(1.0 if is_missing else 0.0)
            names.append(f"{feature}__missing")

        for feature in BOOLEAN_FEATURES:
            bool_value = parse_bool(row.get(feature))

            if bool_value is None:
                values.append(0.0)
                values.append(1.0)
            else:
                values.append(1.0 if bool_value else 0.0)
                values.append(0.0)

            names.append(feature)
            names.append(f"{feature}__missing")

        for feature in CATEGORICAL_FEATURES:
            value = row.get(feature)
            value = "missing" if value is None else str(value)

            for category in categories[feature]:
                values.append(1.0 if value == category else 0.0)
                names.append(f"{feature}={category}")

        if feature_names is None:
            feature_names = names
        elif feature_names != names:
            raise RuntimeError("Schema feature non coerente durante la trasformazione.")

        matrix.append(values)

    return np.asarray(matrix, dtype=float), feature_names


def transform_rows(rows, preprocessor):
    raw_matrix, feature_names = build_raw_matrix(
        rows,
        preprocessor["numeric_medians"],
        preprocessor["categories"],
    )

    if feature_names != preprocessor["feature_names"]:
        raise RuntimeError("Le feature trasformate non coincidono con il preprocessor.")

    return (raw_matrix - preprocessor["means"]) / preprocessor["stds"]


def build_model(max_iter, c_value):
    return LogisticRegression(
        C=c_value,
        solver="liblinear",
        class_weight="balanced",
        max_iter=max_iter,
        random_state=42,
    )


def compute_metrics(y_true, y_prob, threshold=0.5):
    y_pred = (y_prob >= threshold).astype(int)

    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    accuracy = accuracy_score(y_true, y_pred)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    specificity = tn / (tn + fp) if (tn + fp) > 0 else None

    if len(set(y_true.tolist())) == 2:
        roc_auc = roc_auc_score(y_true, y_prob)
    else:
        roc_auc = None

    brier = brier_score_loss(y_true, y_prob)

    return {
        "precision": clean_float(precision),
        "recall": clean_float(recall),
        "specificity": clean_float(specificity),
        "f1": clean_float(f1),
        "accuracy": clean_float(accuracy),
        "roc_auc": clean_float(roc_auc),
        "brier": clean_float(brier),
    }


def mean_or_none(values):
    valid = [float(v) for v in values if v is not None and math.isfinite(float(v))]

    if not valid:
        return None

    return clean_float(np.mean(valid))


def bootstrap_ci(values, iterations=1000, seed=42):
    valid = np.asarray(
        [float(v) for v in values if v is not None and math.isfinite(float(v))],
        dtype=float,
    )

    if valid.size == 0:
        return None, None

    rng = np.random.default_rng(seed)
    boot_means = []

    for _ in range(iterations):
        sample = rng.choice(valid, size=valid.size, replace=True)
        boot_means.append(sample.mean())

    lower = np.percentile(boot_means, 2.5)
    upper = np.percentile(boot_means, 97.5)

    return clean_float(lower), clean_float(upper)


def run_repeated_cv(rows, y, args):
    positive_n = int(y.sum())
    negative_n = int(len(y) - positive_n)

    n_splits = min(args.cv_folds, positive_n, negative_n)

    if n_splits < 2:
        raise RuntimeError(
            "Campione non sufficiente per RepeatedStratifiedKFold: "
            f"positive_n={positive_n}, negative_n={negative_n}."
        )

    splitter = RepeatedStratifiedKFold(
        n_splits=n_splits,
        n_repeats=args.cv_repeats,
        random_state=args.seed,
    )

    metric_values = defaultdict(list)

    oof_score_sum = np.zeros(len(rows), dtype=float)
    oof_score_count = np.zeros(len(rows), dtype=float)

    indices = np.arange(len(rows))

    for train_idx, test_idx in splitter.split(indices, y):
        train_rows = [rows[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]

        y_train = y[train_idx]
        y_test = y[test_idx]

        preprocessor = fit_preprocessor(train_rows)
        x_train = transform_rows(train_rows, preprocessor)
        x_test = transform_rows(test_rows, preprocessor)

        model = build_model(args.max_iter, args.c_value)
        model.fit(x_train, y_train)

        y_prob = model.predict_proba(x_test)[:, 1]

        metrics = compute_metrics(y_test, y_prob, threshold=0.5)

        for key, value in metrics.items():
            metric_values[key].append(value)

        oof_score_sum[test_idx] += y_prob
        oof_score_count[test_idx] += 1

    if np.any(oof_score_count == 0):
        raise RuntimeError("OOF prediction incompleta: alcune righe non sono state validate.")

    oof_scores = oof_score_sum / oof_score_count

    return metric_values, oof_scores


def fit_calibration_line(y, scores):
    clipped = np.clip(scores, 1e-6, 1 - 1e-6)
    logits = np.log(clipped / (1 - clipped)).reshape(-1, 1)

    if len(set(y.tolist())) < 2:
        return None, None

    try:
        calibration_model = LogisticRegression(
            C=1_000_000,
            solver="lbfgs",
            max_iter=5000,
        )
        calibration_model.fit(logits, y)

        slope = calibration_model.coef_[0][0]
        intercept = calibration_model.intercept_[0]

        return clean_float(slope), clean_float(intercept)

    except Exception:
        return None, None


def derive_thresholds(scores, y):
    thresholds = [
        {
            "class_code": "low",
            "class_label_it": "Bassa affidabilità",
            "min_score": 0.0,
            "max_score": 0.50,
            "class_rank": 1,
            "recommended_use": (
                "Non usare come area di riferimento; richiede verifica o esclusione "
                "dal catalogo operativo."
            ),
        },
        {
            "class_code": "compatible",
            "class_label_it": "Compatibile",
            "min_score": 0.50,
            "max_score": 0.70,
            "class_rank": 2,
            "recommended_use": (
                "Usare solo per esplorazione interna o shortlist; non come riferimento "
                "high-confidence."
            ),
        },
        {
            "class_code": "high",
            "class_label_it": "Alta affidabilità",
            "min_score": 0.70,
            "max_score": 0.85,
            "class_rank": 3,
            "recommended_use": (
                "Usabile come area catalogo prioritaria, mantenendo tracciabilità "
                "e limiti metodologici."
            ),
        },
        {
            "class_code": "very_high",
            "class_label_it": "Molto alta affidabilità",
            "min_score": 0.85,
            "max_score": 1.0,
            "class_rank": 4,
            "recommended_use": (
                "Usabile come riferimento catalogo ad alta affidabilità per demo "
                "istituzionali e analisi batch."
            ),
        },
    ]

    notes = [
        "fixed_probability_thresholds_0.50_0.70_0.85",
        "thresholds_are_conservative_and_interpretable",
        "empirical_precision_must_be_checked_with_calibration_bins",
    ]

    return thresholds, notes


def calibration_bins(scores, y, n_bins=10):
    bins = []

    for bin_id in range(n_bins):
        score_min = bin_id / n_bins
        score_max = (bin_id + 1) / n_bins

        if bin_id == n_bins - 1:
            mask = (scores >= score_min) & (scores <= score_max)
        else:
            mask = (scores >= score_min) & (scores < score_max)

        n_samples = int(mask.sum())

        if n_samples == 0:
            mean_score = None
            observed_rate = None
        else:
            mean_score = clean_float(scores[mask].mean())
            observed_rate = clean_float(y[mask].mean())

        bins.append(
            {
                "bin_id": bin_id + 1,
                "score_min": clean_float(score_min),
                "score_max": clean_float(score_max),
                "n_samples": n_samples,
                "mean_predicted_score": mean_score,
                "observed_positive_rate": observed_rate,
            }
        )

    return bins


def run_spatial_cv(rows, y, args):
    zones = sorted({row["spatial_validation_zone"] for row in rows})
    results = []

    for zone in zones:
        test_idx = [i for i, row in enumerate(rows) if row["spatial_validation_zone"] == zone]
        train_idx = [i for i, row in enumerate(rows) if row["spatial_validation_zone"] != zone]

        y_test = y[test_idx]
        y_train = y[train_idx]

        positive_n = int(y_test.sum())
        negative_n = int(len(y_test) - positive_n)

        result = {
            "held_out_zone": zone,
            "n_test": int(len(y_test)),
            "positive_n": positive_n,
            "negative_n": negative_n,
            "precision": None,
            "recall": None,
            "specificity": None,
            "f1": None,
            "accuracy": None,
            "roc_auc": None,
            "brier": None,
        }

        if len(y_test) == 0 or len(set(y_test.tolist())) < 2 or len(set(y_train.tolist())) < 2:
            results.append(result)
            continue

        train_rows = [rows[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]

        preprocessor = fit_preprocessor(train_rows)
        x_train = transform_rows(train_rows, preprocessor)
        x_test = transform_rows(test_rows, preprocessor)

        model = build_model(args.max_iter, args.c_value)
        model.fit(x_train, y_train)

        y_prob = model.predict_proba(x_test)[:, 1]
        metrics = compute_metrics(y_test, y_prob, threshold=0.5)

        result.update(metrics)
        results.append(result)

    return results


def fit_final_model(rows, y, args):
    preprocessor = fit_preprocessor(rows)
    x = transform_rows(rows, preprocessor)

    model = build_model(args.max_iter, args.c_value)
    model.fit(x, y)

    scores = model.predict_proba(x)[:, 1]

    coefficients = []
    for feature_name, coefficient, mean, std in zip(
        preprocessor["feature_names"],
        model.coef_[0],
        preprocessor["means"],
        preprocessor["stds"],
    ):
        coefficients.append(
            {
                "feature_name": feature_name,
                "coefficient_value": clean_float(coefficient, digits=10),
                "feature_mean": clean_float(mean, digits=10),
                "feature_std": clean_float(std, digits=10),
            }
        )

    model_intercept = clean_float(model.intercept_[0], digits=10)

    return model, preprocessor, scores, coefficients, model_intercept


def save_results(
    conn,
    *,
    model_version,
    rows,
    y,
    uncertain_n,
    metric_values,
    oof_scores,
    thresholds,
    threshold_notes,
    calibration_bin_rows,
    spatial_cv_rows,
    coefficients,
    preprocessor,
    model_intercept,
    args,
):
    positive_n = int(y.sum())
    negative_n = int(len(y) - positive_n)

    mean_metrics = {
        "precision": mean_or_none(metric_values["precision"]),
        "recall": mean_or_none(metric_values["recall"]),
        "specificity": mean_or_none(metric_values["specificity"]),
        "f1": mean_or_none(metric_values["f1"]),
        "accuracy": mean_or_none(metric_values["accuracy"]),
        "roc_auc": mean_or_none(metric_values["roc_auc"]),
        "brier": mean_or_none(metric_values["brier"]),
    }

    ci = {}
    for metric in ["precision", "recall", "specificity", "f1", "accuracy", "roc_auc", "brier"]:
        lower, upper = bootstrap_ci(
            metric_values[metric],
            iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
        ci[metric] = {"lower": lower, "upper": upper}

    calibration_slope, calibration_intercept = fit_calibration_line(y, oof_scores)

    limitations = (
        "Modello sperimentale calibrato su campione visuale limitato. "
        "Le classi di affidabilità non identificano cultivar, non rappresentano confini catastali "
        "e non costituiscono diagnosi agronomica. "
        "Lo score stima la probabilità operativa che un'area del catalogo sia coerente "
        "con l'identità olivicola attesa, date le feature disponibili. "
        "Le aree non strict non sono negative per definizione. "
        "Le etichette uncertain sono escluse dal training."
    )

    metadata = {
        "feature_names": [item["feature_name"] for item in coefficients],
        "numeric_features": NUMERIC_FEATURES,
        "boolean_features": BOOLEAN_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_medians": {
            key: clean_float(value, digits=10)
            for key, value in preprocessor["numeric_medians"].items()
        },
        "categories": preprocessor["categories"],
        "threshold_notes": threshold_notes,
        "cv_threshold": 0.5,
        "model_c_value": args.c_value,
        "seed": args.seed,
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM regional_reliability_model_runs
            WHERE model_version = %s;
            """,
            (model_version,),
        )

        cur.execute(
            """
            INSERT INTO regional_reliability_model_runs (
                model_version,
                model_name,
                feature_matrix_version,
                source_layer_version,
                algorithm,
                penalty,
                class_weight,
                training_n,
                positive_n,
                negative_n,
                uncertain_n,
                n_features,
                model_intercept,
                repeated_cv_folds,
                repeated_cv_repeats,
                bootstrap_iterations,
                spatial_validation_strategy,
                mean_precision,
                mean_recall,
                mean_specificity,
                mean_f1,
                mean_accuracy,
                mean_roc_auc,
                mean_brier_score,
                precision_ci95_lower,
                precision_ci95_upper,
                recall_ci95_lower,
                recall_ci95_upper,
                specificity_ci95_lower,
                specificity_ci95_upper,
                f1_ci95_lower,
                f1_ci95_upper,
                accuracy_ci95_lower,
                accuracy_ci95_upper,
                roc_auc_ci95_lower,
                roc_auc_ci95_upper,
                brier_ci95_lower,
                brier_ci95_upper,
                calibration_slope,
                calibration_intercept,
                status,
                limitations,
                metadata
            )
            VALUES (
                %s, %s, %s, %s,
                'penalized_logistic_regression',
                'l2',
                'balanced',
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                'leave_one_zone_out',
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                'experimental',
                %s,
                %s::jsonb
            );
            """,
            (
                model_version,
                MODEL_NAME,
                FEATURE_MATRIX_VERSION,
                SOURCE_LAYER_VERSION,
                len(rows),
                positive_n,
                negative_n,
                uncertain_n,
                len(coefficients),
                model_intercept,
                args.cv_folds,
                args.cv_repeats,
                args.bootstrap_iterations,
                mean_metrics["precision"],
                mean_metrics["recall"],
                mean_metrics["specificity"],
                mean_metrics["f1"],
                mean_metrics["accuracy"],
                mean_metrics["roc_auc"],
                mean_metrics["brier"],
                ci["precision"]["lower"],
                ci["precision"]["upper"],
                ci["recall"]["lower"],
                ci["recall"]["upper"],
                ci["specificity"]["lower"],
                ci["specificity"]["upper"],
                ci["f1"]["lower"],
                ci["f1"]["upper"],
                ci["accuracy"]["lower"],
                ci["accuracy"]["upper"],
                ci["roc_auc"]["lower"],
                ci["roc_auc"]["upper"],
                ci["brier"]["lower"],
                ci["brier"]["upper"],
                calibration_slope,
                calibration_intercept,
                limitations,
                json.dumps(metadata),
            ),
        )

        coefficient_values = [
            (
                model_version,
                item["feature_name"],
                item["coefficient_value"],
                item["feature_mean"],
                item["feature_std"],
            )
            for item in coefficients
        ]

        execute_values(
            cur,
            """
            INSERT INTO regional_reliability_model_coefficients (
                model_version,
                feature_name,
                coefficient_value,
                feature_mean,
                feature_std
            )
            VALUES %s;
            """,
            coefficient_values,
        )

        threshold_values = [
            (
                model_version,
                item["class_code"],
                item["class_label_it"],
                item["min_score"],
                item["max_score"],
                item["class_rank"],
                item["recommended_use"],
            )
            for item in thresholds
        ]

        execute_values(
            cur,
            """
            INSERT INTO regional_reliability_model_thresholds (
                model_version,
                class_code,
                class_label_it,
                min_score,
                max_score,
                class_rank,
                recommended_use
            )
            VALUES %s;
            """,
            threshold_values,
        )

        spatial_values = [
            (
                model_version,
                item["held_out_zone"],
                item["n_test"],
                item["positive_n"],
                item["negative_n"],
                item["precision"],
                item["recall"],
                item["specificity"],
                item["f1"],
                item["accuracy"],
                item["roc_auc"],
                item["brier"],
            )
            for item in spatial_cv_rows
        ]

        execute_values(
            cur,
            """
            INSERT INTO regional_reliability_spatial_cv_results (
                model_version,
                held_out_zone,
                n_test,
                positive_n,
                negative_n,
                precision_value,
                recall_value,
                specificity_value,
                f1_score,
                accuracy_value,
                roc_auc,
                brier_score
            )
            VALUES %s;
            """,
            spatial_values,
        )

        bin_values = [
            (
                model_version,
                item["bin_id"],
                item["score_min"],
                item["score_max"],
                item["n_samples"],
                item["mean_predicted_score"],
                item["observed_positive_rate"],
            )
            for item in calibration_bin_rows
        ]

        execute_values(
            cur,
            """
            INSERT INTO regional_reliability_calibration_bins (
                model_version,
                bin_id,
                score_min,
                score_max,
                n_samples,
                mean_predicted_score,
                observed_positive_rate
            )
            VALUES %s;
            """,
            bin_values,
        )

        if table_exists(cur, "model_versions"):
            cur.execute(
                """
                INSERT INTO model_versions (
                    model_version,
                    model_name,
                    model_family,
                    purpose,
                    source_layer_version,
                    status,
                    training_sample_n,
                    valid_label_n,
                    precision_value,
                    recall_value,
                    specificity_value,
                    f1_score,
                    calibration_notes,
                    limitations,
                    metadata
                )
                VALUES (
                    %s,
                    %s,
                    'penalized_logistic_regression',
                    'Score regionale sperimentale di affidabilità per catalogo territoriale olivicolo.',
                    %s,
                    'experimental',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb
                )
                ON CONFLICT (model_version) DO UPDATE
                SET
                    precision_value = EXCLUDED.precision_value,
                    recall_value = EXCLUDED.recall_value,
                    specificity_value = EXCLUDED.specificity_value,
                    f1_score = EXCLUDED.f1_score,
                    calibration_notes = EXCLUDED.calibration_notes,
                    limitations = EXCLUDED.limitations,
                    metadata = EXCLUDED.metadata;
                """,
                (
                    model_version,
                    MODEL_NAME,
                    SOURCE_LAYER_VERSION,
                    len(rows),
                    len(rows),
                    mean_metrics["precision"],
                    mean_metrics["recall"],
                    mean_metrics["specificity"],
                    mean_metrics["f1"],
                    (
                        "Calibrazione sperimentale con Repeated Stratified CV, "
                        "bootstrap CI, leave-one-zone-out e calibration bins."
                    ),
                    limitations,
                    json.dumps(metadata),
                ),
            )

    conn.commit()

    return mean_metrics, ci, calibration_slope, calibration_intercept


def print_summary(
    *,
    model_version,
    y,
    uncertain_n,
    mean_metrics,
    ci,
    thresholds,
    spatial_cv_rows,
    calibration_slope,
    calibration_intercept,
    coefficients,
):
    positive_n = int(y.sum())
    negative_n = int(len(y) - positive_n)

    print("")
    print("Regional reliability calibration")
    print("--------------------------------")
    print(f"model_version: {model_version}")
    print(f"training_n: {len(y)}")
    print(f"positive_n: {positive_n}")
    print(f"negative_n: {negative_n}")
    print(f"uncertain_n_excluded: {uncertain_n}")
    print(f"n_features: {len(coefficients)}")

    print("")
    print("Repeated stratified CV")
    print("----------------------")
    for metric in ["precision", "recall", "specificity", "f1", "accuracy", "roc_auc", "brier"]:
        print(
            f"{metric}: {mean_metrics[metric]} "
            f"[{ci[metric]['lower']}, {ci[metric]['upper']}]"
        )

    print("")
    print("Calibration")
    print("-----------")
    print(f"calibration_slope: {calibration_slope}")
    print(f"calibration_intercept: {calibration_intercept}")

    print("")
    print("Thresholds")
    print("----------")
    for item in thresholds:
        print(
            f"{item['class_code']}: "
            f"{item['min_score']} - {item['max_score']} | "
            f"{item['class_label_it']}"
        )

    print("")
    print("Leave-one-zone-out")
    print("------------------")
    for row in spatial_cv_rows:
        print(
            f"{row['held_out_zone']}: "
            f"n={row['n_test']} pos={row['positive_n']} neg={row['negative_n']} | "
            f"precision={row['precision']} recall={row['recall']} "
            f"specificity={row['specificity']} auc={row['roc_auc']}"
        )

    print("")
    print("Nota metodologica")
    print("-----------------")
    print("Status salvato: experimental.")
    print("Non usare ancora come modello operativo finché non verifichiamo metriche, zone e calibrazione.")


def main():
    parser = argparse.ArgumentParser(
        description="Calibra regional_reliability_score sperimentale."
    )

    parser.add_argument(
        "--model-version",
        default=None,
        help="Versione modello. Se omessa usa timestamp UTC.",
    )

    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--cv-repeats", type=int, default=20)
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--max-iter", type=int, default=5000)
    parser.add_argument("--c-value", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    if args.model_version:
        model_version = args.model_version
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        model_version = f"regional_reliability_score_exp_{ts}"

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            rows = fetch_training_rows(cur)
            uncertain_n = fetch_uncertain_n(cur)

        y = np.asarray([int(row["binary_visual_label"]) for row in rows], dtype=int)

        metric_values, oof_scores = run_repeated_cv(rows, y, args)

        thresholds, threshold_notes = derive_thresholds(oof_scores, y)
        calibration_bin_rows = calibration_bins(oof_scores, y, n_bins=10)
        spatial_cv_rows = run_spatial_cv(rows, y, args)

        _, preprocessor, _, coefficients, model_intercept = fit_final_model(rows, y, args)

        (
            mean_metrics,
            ci,
            calibration_slope,
            calibration_intercept,
        ) = save_results(
            conn,
            model_version=model_version,
            rows=rows,
            y=y,
            uncertain_n=uncertain_n,
            metric_values=metric_values,
            oof_scores=oof_scores,
            thresholds=thresholds,
            threshold_notes=threshold_notes,
            calibration_bin_rows=calibration_bin_rows,
            spatial_cv_rows=spatial_cv_rows,
            coefficients=coefficients,
            preprocessor=preprocessor,
            model_intercept=model_intercept,
            args=args,
        )

    print_summary(
        model_version=model_version,
        y=y,
        uncertain_n=uncertain_n,
        mean_metrics=mean_metrics,
        ci=ci,
        thresholds=thresholds,
        spatial_cv_rows=spatial_cv_rows,
        calibration_slope=calibration_slope,
        calibration_intercept=calibration_intercept,
        coefficients=coefficients,
    )


if __name__ == "__main__":
    main()