import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, Json
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


MODEL_VERSION = "regional_reliability_score_exp_v3"
MODEL_NAME = "Regional olive reliability score experimental v3"
MODEL_TYPE = "penalized_logistic_regression"
SAMPLE_VERSION = "olive_visual_review_sample_v2"
SOURCE_POOL_VERSION = "candidate_pool_v2_area_ge_0_5"
TRAINING_VIEW = "olive_visual_review_sample_v2_training_v1"

RANDOM_SEED = 42
N_REPEATS = 30
N_SPLITS = 5

NUMERIC_FEATURES = [
    "area_ha_raw",
    "log_area_ha_raw",
    "perimeter_m_raw",
    "log_perimeter_m_raw",
    "compactness_raw",
    "n_points",
    "log_n_points",
    "n_parts",
    "geometric_prior_rank",
]

BOOLEAN_FEATURES = [
    "current_high_confidence_v2",
    "identity_reference_match",
    "strict_reference_match",
    "large_polygon_flag",
    "small_candidate_flag",
    "complex_boundary_flag",
]

CATEGORICAL_FEATURES = [
    "spatial_validation_zone",
    "candidate_origin",
    "area_bin_raw",
    "n_points_bin",
    "n_parts_bin",
]

THRESHOLDS = [
    {
        "class_label": "low",
        "score_min": 0.00,
        "score_max": 0.50,
        "description": "Bassa affidabilità: area non prioritaria o da verificare solo se strategica.",
    },
    {
        "class_label": "compatible",
        "score_min": 0.50,
        "score_max": 0.70,
        "description": "Compatibile: area potenzialmente olivicola, utile per screening.",
    },
    {
        "class_label": "high",
        "score_min": 0.70,
        "score_max": 0.85,
        "description": "Alta affidabilità: area candidata per catalogo operativo.",
    },
    {
        "class_label": "very_high",
        "score_min": 0.85,
        "score_max": 1.00,
        "description": "Affidabilità molto alta: area candidata prioritaria.",
    },
]


def clean_number(value):
    if value is None:
        return None

    if isinstance(value, Decimal):
        return float(value)

    return value


def safe_float(value):
    value = clean_number(value)

    if value is None:
        return None

    value = float(value)

    if not math.isfinite(value):
        return None

    return value


def safe_log1p(value):
    value = safe_float(value)

    if value is None or value < 0:
        return None

    return math.log1p(value)


def bool_to_float(value):
    if value is None:
        return 0.0

    return 1.0 if bool(value) else 0.0


def get_table_columns(conn, table_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s;
            """,
            (table_name,),
        )
        return {row[0] for row in cur.fetchall()}


def scalar(value):
    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, Decimal):
        return float(value)

    return value


def insert_row(conn, table_name, row):
    columns = get_table_columns(conn, table_name)

    filtered = {
        key: scalar(value)
        for key, value in row.items()
        if key in columns
    }

    if not filtered:
        raise RuntimeError(f"Nessuna colonna compatibile per {table_name}")

    col_names = list(filtered.keys())
    placeholders = []
    values = []

    for key in col_names:
        value = filtered[key]

        if isinstance(value, (dict, list)):
            placeholders.append("%s")
            values.append(Json(value))
        else:
            placeholders.append("%s")
            values.append(value)

    sql = f"""
        INSERT INTO {table_name} ({", ".join(col_names)})
        VALUES ({", ".join(placeholders)});
    """

    with conn.cursor() as cur:
        cur.execute(sql, values)


def delete_existing_model(conn):
    tables = [
        "regional_reliability_calibration_bins",
        "regional_reliability_spatial_cv_results",
        "regional_reliability_model_thresholds",
        "regional_reliability_model_coefficients",
        "regional_reliability_model_runs",
    ]

    with conn.cursor() as cur:
        for table in tables:
            cur.execute(
                f"""
                DELETE FROM {table}
                WHERE model_version = %s;
                """,
                (MODEL_VERSION,),
            )

    conn.commit()


def fetch_training_rows(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                l.sample_id,
                l.area_id,

                l.spatial_validation_zone,
                l.candidate_origin,
                l.area_ha_raw,
                l.area_bin_raw,

                l.n_points,
                l.n_parts,
                l.n_points_bin,
                l.n_parts_bin,

                l.current_high_confidence_v2,
                l.identity_reference_match,
                l.strict_reference_match,

                l.large_polygon_flag,
                l.small_candidate_flag,
                l.complex_boundary_flag,

                l.visual_label_v2,
                l.plantation_pattern_v2,
                l.binary_visual_label_v2,

                p.perimeter_m_raw,
                p.compactness_raw,
                p.geometric_prior_rank

            FROM olive_visual_review_sample_v2_training_v1 l
            LEFT JOIN olive_candidate_pool_v2_review_priority p
                ON p.area_id = l.area_id
            WHERE l.visual_label_v2 IN ('olive_like', 'not_olive_like')
            ORDER BY l.sample_id;
            """
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_sample_summary(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT *
            FROM olive_visual_review_sample_v2_summary_v1;
            """
        )
        return dict(cur.fetchone())


def fit_feature_state(rows):
    numeric_values = {name: [] for name in NUMERIC_FEATURES}
    categories = {name: set() for name in CATEGORICAL_FEATURES}

    for row in rows:
        row_features = derive_raw_features(row)

        for name in NUMERIC_FEATURES:
            value = safe_float(row_features.get(name))

            if value is not None:
                numeric_values[name].append(value)

        for name in CATEGORICAL_FEATURES:
            value = row_features.get(name)

            if value is None:
                value = "missing"

            categories[name].add(str(value))

    numeric_state = {}

    for name, values in numeric_values.items():
        if values:
            arr = np.array(values, dtype=float)
            median = float(np.median(arr))
            mean = float(np.mean(arr))
            std = float(np.std(arr))

            if std == 0 or not math.isfinite(std):
                std = 1.0
        else:
            median = 0.0
            mean = 0.0
            std = 1.0

        numeric_state[name] = {
            "median": median,
            "mean": mean,
            "std": std,
        }

    category_state = {
        name: sorted(values)
        for name, values in categories.items()
    }

    return {
        "numeric": numeric_state,
        "categories": category_state,
        "numeric_features": NUMERIC_FEATURES,
        "boolean_features": BOOLEAN_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
    }


def derive_raw_features(row):
    area_ha_raw = safe_float(row.get("area_ha_raw"))
    perimeter_m_raw = safe_float(row.get("perimeter_m_raw"))
    n_points = safe_float(row.get("n_points"))

    features = {
        "area_ha_raw": area_ha_raw,
        "log_area_ha_raw": safe_log1p(area_ha_raw),
        "perimeter_m_raw": perimeter_m_raw,
        "log_perimeter_m_raw": safe_log1p(perimeter_m_raw),
        "compactness_raw": safe_float(row.get("compactness_raw")),
        "n_points": n_points,
        "log_n_points": safe_log1p(n_points),
        "n_parts": safe_float(row.get("n_parts")),
        "geometric_prior_rank": safe_float(row.get("geometric_prior_rank")),
        "current_high_confidence_v2": row.get("current_high_confidence_v2"),
        "identity_reference_match": row.get("identity_reference_match"),
        "strict_reference_match": row.get("strict_reference_match"),
        "large_polygon_flag": row.get("large_polygon_flag"),
        "small_candidate_flag": row.get("small_candidate_flag"),
        "complex_boundary_flag": row.get("complex_boundary_flag"),
        "spatial_validation_zone": row.get("spatial_validation_zone"),
        "candidate_origin": row.get("candidate_origin"),
        "area_bin_raw": row.get("area_bin_raw"),
        "n_points_bin": row.get("n_points_bin"),
        "n_parts_bin": row.get("n_parts_bin"),
    }

    return features


def transform_rows(rows, state):
    feature_names = []

    for name in NUMERIC_FEATURES:
        feature_names.append(f"num__{name}_z")

    for name in BOOLEAN_FEATURES:
        feature_names.append(f"bool__{name}")

    for name in CATEGORICAL_FEATURES:
        for category in state["categories"][name]:
            feature_names.append(f"cat__{name}={category}")

    X = np.zeros((len(rows), len(feature_names)), dtype=float)

    feature_index = {name: idx for idx, name in enumerate(feature_names)}

    for row_idx, row in enumerate(rows):
        raw = derive_raw_features(row)

        for name in NUMERIC_FEATURES:
            value = safe_float(raw.get(name))

            if value is None:
                value = state["numeric"][name]["median"]

            mean = state["numeric"][name]["mean"]
            std = state["numeric"][name]["std"]

            X[row_idx, feature_index[f"num__{name}_z"]] = (value - mean) / std

        for name in BOOLEAN_FEATURES:
            X[row_idx, feature_index[f"bool__{name}"]] = bool_to_float(raw.get(name))

        for name in CATEGORICAL_FEATURES:
            value = raw.get(name)

            if value is None:
                value = "missing"

            key = f"cat__{name}={value}"

            if key in feature_index:
                X[row_idx, feature_index[key]] = 1.0

    return X, feature_names


def y_from_rows(rows):
    return np.array([int(row["binary_visual_label_v2"]) for row in rows], dtype=int)


def make_model():
    return LogisticRegression(
        C=0.75,
        class_weight="balanced",
        solver="lbfgs",
        max_iter=10000,
        random_state=RANDOM_SEED,
    )


def compute_metrics(y_true, y_prob, threshold=0.5):
    y_pred = (np.array(y_prob) >= threshold).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()

    specificity = tn / (tn + fp) if (tn + fp) else 0.0

    try:
        auc = roc_auc_score(y_true, y_prob)
    except ValueError:
        auc = None

    return {
        "n": int(len(y_true)),
        "positive_n": int(np.sum(y_true == 1)),
        "negative_n": int(np.sum(y_true == 0)),
        "tp": int(tp),
        "fp": int(fp),
        "tn": int(tn),
        "fn": int(fn),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "specificity": float(specificity),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "roc_auc": float(auc) if auc is not None else None,
        "brier": float(brier_score_loss(y_true, y_prob)),
    }


def compute_calibration(y_true, y_prob):
    eps = 1e-6
    y_prob = np.clip(np.array(y_prob), eps, 1 - eps)
    logits = np.log(y_prob / (1 - y_prob)).reshape(-1, 1)

    if len(set(y_true)) < 2:
        return None, None

    model = LogisticRegression(
        C=np.inf,
        solver="lbfgs",
        max_iter=10000,
    )
    model.fit(logits, y_true)

    return float(model.coef_[0][0]), float(model.intercept_[0])


def repeated_cv_predictions(rows):
    y = y_from_rows(rows)

    min_class_n = min(Counter(y).values())
    n_splits = min(N_SPLITS, min_class_n)

    if n_splits < 2:
        raise RuntimeError("Non ci sono abbastanza osservazioni per la cross-validation.")

    cv = RepeatedStratifiedKFold(
        n_splits=n_splits,
        n_repeats=N_REPEATS,
        random_state=RANDOM_SEED,
    )

    prob_sum = np.zeros(len(rows), dtype=float)
    prob_count = np.zeros(len(rows), dtype=float)

    indices = np.arange(len(rows))

    for train_idx, test_idx in cv.split(indices, y):
        train_rows = [rows[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]

        state = fit_feature_state(train_rows)
        X_train, _ = transform_rows(train_rows, state)
        X_test, _ = transform_rows(test_rows, state)

        y_train = y[train_idx]

        model = make_model()
        model.fit(X_train, y_train)

        y_prob = model.predict_proba(X_test)[:, 1]

        prob_sum[test_idx] += y_prob
        prob_count[test_idx] += 1

    return prob_sum / np.maximum(prob_count, 1)


def leave_one_zone_out(rows):
    results = []

    zones = sorted({row["spatial_validation_zone"] for row in rows})

    for zone in zones:
        train_rows = [row for row in rows if row["spatial_validation_zone"] != zone]
        test_rows = [row for row in rows if row["spatial_validation_zone"] == zone]

        if not train_rows or not test_rows:
            continue

        y_train = y_from_rows(train_rows)
        y_test = y_from_rows(test_rows)

        if len(set(y_train)) < 2 or len(set(y_test)) < 2:
            continue

        state = fit_feature_state(train_rows)
        X_train, _ = transform_rows(train_rows, state)
        X_test, _ = transform_rows(test_rows, state)

        model = make_model()
        model.fit(X_train, y_train)

        y_prob = model.predict_proba(X_test)[:, 1]
        metrics = compute_metrics(y_test, y_prob)

        metrics.update(
            {
                "model_version": MODEL_VERSION,

                "held_out_zone": zone,
                "spatial_validation_zone": zone,
                "validation_zone": zone,
                "zone": zone,

                "n_train": len(train_rows),
                "training_n": len(train_rows),

                "n_test": len(test_rows),
                "test_n": len(test_rows),

                "precision_value": metrics["precision"],
                "recall_value": metrics["recall"],
                "specificity_value": metrics["specificity"],
                "accuracy_value": metrics["accuracy"],
                "brier_score": metrics["brier"],

                "validation_strategy": "leave_one_zone_out",
                "cv_strategy": "leave_one_zone_out",
            }
        )

        results.append(metrics)

    return results


def make_calibration_bins(y_true, y_prob, n_bins=10):
    bins = []

    y_prob = np.array(y_prob)
    y_true = np.array(y_true)

    for i in range(n_bins):
        score_min = i / n_bins
        score_max = (i + 1) / n_bins

        if i == n_bins - 1:
            mask = (y_prob >= score_min) & (y_prob <= score_max)
        else:
            mask = (y_prob >= score_min) & (y_prob < score_max)

        n = int(mask.sum())

        if n > 0:
            observed_rate = float(y_true[mask].mean())
            predicted_mean = float(y_prob[mask].mean())
        else:
            observed_rate = None
            predicted_mean = None

        bins.append(
            {
                "model_version": MODEL_VERSION,
                "bin_id": i + 1,
                "bin_index": i + 1,
                "score_min": score_min,
                "score_max": score_max,
                "n": n,
                "sample_n": n,
                "n_samples": n,
                "observed_rate": observed_rate,
                "predicted_mean": predicted_mean,
                "mean_predicted_probability": predicted_mean,

                "mean_predicted_score": predicted_mean,
                "observed_positive_rate": observed_rate,
            }
        )

    return bins


def save_model_results(
    conn,
    rows,
    full_state,
    feature_names,
    model,
    cv_prob,
    cv_metrics,
    calibration_slope,
    calibration_intercept,
    sample_summary,
    lozo_results,
    calibration_bins,
):
    y = y_from_rows(rows)

    positive_n = int(np.sum(y == 1))
    negative_n = int(np.sum(y == 0))
    uncertain_n = int(sample_summary.get("n_uncertain", 0))

    metadata = {
        "model_version": MODEL_VERSION,
        "model_name": MODEL_NAME,
        "model_type": MODEL_TYPE,
        "sample_version": SAMPLE_VERSION,
        "source_pool_version": SOURCE_POOL_VERSION,
        "training_view": TRAINING_VIEW,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_definition": {
            "olive_like": 1,
            "not_olive_like": 0,
            "uncertain": "excluded",
        },
        "excluded_features": [
            "sample_stratum",
            "review_priority_score",
            "review_priority_reason",
            "previous_visual_label",
            "plantation_pattern_v2",
            "review_confidence_v2",
        ],
        "feature_state": full_state,
        "thresholds": THRESHOLDS,
        "cv": {
            "method": "RepeatedStratifiedKFold",
            "n_splits": N_SPLITS,
            "n_repeats": N_REPEATS,
            "random_seed": RANDOM_SEED,
            "probability_aggregation": "mean probability across repeats",
        },
        "sample_summary": sample_summary,
    }

    run_row = {
        "model_version": MODEL_VERSION,
        "model_name": MODEL_NAME,
        "model_label": MODEL_NAME,
        "model_type": MODEL_TYPE,
        "model_family": MODEL_TYPE,
        "sample_version": SAMPLE_VERSION,
        "source_pool_version": SOURCE_POOL_VERSION,
        "training_view": TRAINING_VIEW,
        "training_n": len(rows),
        "positive_n": positive_n,
        "negative_n": negative_n,
        "uncertain_n": uncertain_n,
        "n_features": len(feature_names),
        "model_intercept": float(model.intercept_[0]),
        "precision": cv_metrics["precision"],
        "recall": cv_metrics["recall"],
        "specificity": cv_metrics["specificity"],
        "f1": cv_metrics["f1"],
        "f1_score": cv_metrics["f1"],
        "accuracy": cv_metrics["accuracy"],
        "roc_auc": cv_metrics["roc_auc"],
        "brier": cv_metrics["brier"],
        "brier_score": cv_metrics["brier"],

        "mean_precision": cv_metrics["precision"],
        "mean_recall": cv_metrics["recall"],
        "mean_specificity": cv_metrics["specificity"],
        "mean_f1": cv_metrics["f1"],
        "mean_accuracy": cv_metrics["accuracy"],
        "mean_roc_auc": cv_metrics["roc_auc"],
        "mean_brier_score": cv_metrics["brier"],
        "calibration_slope": calibration_slope,
        "calibration_intercept": calibration_intercept,
        "model_status": "experimental",
        "notes": (
            "V3 calibrato su visual review sample v2. "
            "Il campo plantation_pattern_v2 è mantenuto per audit ma non usato come predittore principale."
        ),
        "status": "experimental",
        "limitations": (
            "Modello sperimentale calibrato su visual review sample v2. "
            "Il campione ? stratificato e sovracampiona nord Calabria e aree added_candidate; "
            "le probabilit? devono essere interpretate come score di affidabilit? operativa, "
            "non come validazione assoluta delle aree. "
            "Il campo plantation_pattern_v2 ? usato per audit, non come predittore principale. "
            "Le aree uncertain sono escluse dal training."
        ),
        "metadata": metadata,
    }

    insert_row(conn, "regional_reliability_model_runs", run_row)

    coefs = list(zip(feature_names, model.coef_[0]))
    total_abs = sum(abs(coef) for _, coef in coefs) or 1.0

    for rank, (feature_name, coefficient) in enumerate(
        sorted(coefs, key=lambda item: abs(item[1]), reverse=True),
        start=1,
    ):
        abs_coef = abs(float(coefficient))
        importance = 100.0 * abs_coef / total_abs

        coef_value = float(coefficient)

        coef_row = {
            "model_version": MODEL_VERSION,
            "feature_name": feature_name,
            "feature": feature_name,
            "variable_name": feature_name,

            "coefficient_value": coef_value,
            "coefficient": coef_value,
            "coef": coef_value,

            "odds_ratio": float(math.exp(max(min(coef_value, 20), -20))),
            "abs_coefficient": abs_coef,

            "normalized_importance": importance,
            "importance_pct": importance,
            "importance_score": importance,

            "importance_rank": rank,
            "feature_rank": rank,
        }

        insert_row(conn, "regional_reliability_model_coefficients", coef_row)

    threshold_labels_it = {
        "low": "Bassa affidabilit?",
        "compatible": "Compatibile",
        "high": "Alta affidabilit?",
        "very_high": "Affidabilit? molto alta",
    }

    for class_rank, threshold in enumerate(THRESHOLDS, start=1):
        class_code = threshold["class_label"]

        threshold_row = {
            "model_version": MODEL_VERSION,

            "class_code": class_code,
            "class_label_it": threshold_labels_it.get(class_code, class_code),

            "min_score": threshold["score_min"],
            "max_score": threshold["score_max"],
            "class_rank": class_rank,

            "recommended_use": threshold["description"],

            "class_label": class_code,
            "reliability_class": class_code,
            "reliability_label": class_code,

            "score_min": threshold["score_min"],
            "score_max": threshold["score_max"],
            "threshold_min": threshold["score_min"],
            "threshold_max": threshold["score_max"],

            "description": threshold["description"],
            "class_description": threshold["description"],
            "operational_meaning": threshold["description"],
            "recommended_action": threshold["description"],
            "intended_use": threshold["description"],
        }

        insert_row(conn, "regional_reliability_model_thresholds", threshold_row)

    for result in lozo_results:
        insert_row(conn, "regional_reliability_spatial_cv_results", result)

    for bin_row in calibration_bins:
        insert_row(conn, "regional_reliability_calibration_bins", bin_row)

    conn.commit()


def print_results(rows, metrics, calibration_slope, calibration_intercept, lozo_results, calibration_bins):
    y = y_from_rows(rows)

    print("")
    print("Regional reliability score v3")
    print("-----------------------------")
    print(f"model_version: {MODEL_VERSION}")
    print(f"training_n: {len(rows)}")
    print(f"positive_n: {int(np.sum(y == 1))}")
    print(f"negative_n: {int(np.sum(y == 0))}")

    print("")
    print("Repeated stratified CV")
    for key in [
        "precision",
        "recall",
        "specificity",
        "f1",
        "accuracy",
        "roc_auc",
        "brier",
    ]:
        print(f"{key}: {metrics[key]}")

    print("")
    print(f"calibration_slope: {calibration_slope}")
    print(f"calibration_intercept: {calibration_intercept}")

    print("")
    print("Leave-one-zone-out")
    for result in lozo_results:
        print(
            f"{result['spatial_validation_zone']}: "
            f"test_n={result['test_n']} "
            f"pos={result['positive_n']} neg={result['negative_n']} "
            f"precision={result['precision']:.4f} "
            f"recall={result['recall']:.4f} "
            f"specificity={result['specificity']:.4f} "
            f"auc={result['roc_auc']:.4f} "
            f"brier={result['brier']:.4f}"
        )

    print("")
    print("Calibration bins")
    for bin_row in calibration_bins:
        print(
            f"{bin_row['bin_id']}: "
            f"{bin_row['score_min']:.1f}-{bin_row['score_max']:.1f} "
            f"n={bin_row['n']} "
            f"pred={bin_row['predicted_mean']} "
            f"obs={bin_row['observed_rate']}"
        )


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    with psycopg2.connect(database_url) as conn:
        rows = fetch_training_rows(conn)
        sample_summary = fetch_sample_summary(conn)

        if len(rows) < 50:
            raise RuntimeError("Training sample troppo piccolo per modello v3.")

        y = y_from_rows(rows)

        if len(set(y)) < 2:
            raise RuntimeError("Il training sample deve contenere positivi e negativi.")

        cv_prob = repeated_cv_predictions(rows)
        cv_metrics = compute_metrics(y, cv_prob)

        calibration_slope, calibration_intercept = compute_calibration(y, cv_prob)

        full_state = fit_feature_state(rows)
        X, feature_names = transform_rows(rows, full_state)

        model = make_model()
        model.fit(X, y)

        lozo_results = leave_one_zone_out(rows)
        calibration_bins = make_calibration_bins(y, cv_prob)

        delete_existing_model(conn)

        save_model_results(
            conn=conn,
            rows=rows,
            full_state=full_state,
            feature_names=feature_names,
            model=model,
            cv_prob=cv_prob,
            cv_metrics=cv_metrics,
            calibration_slope=calibration_slope,
            calibration_intercept=calibration_intercept,
            sample_summary=sample_summary,
            lozo_results=lozo_results,
            calibration_bins=calibration_bins,
        )

        print_results(
            rows=rows,
            metrics=cv_metrics,
            calibration_slope=calibration_slope,
            calibration_intercept=calibration_intercept,
            lozo_results=lozo_results,
            calibration_bins=calibration_bins,
        )


if __name__ == "__main__":
    main()