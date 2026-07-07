import itertools
import os
from typing import Dict, List

import psycopg2
from dotenv import load_dotenv


load_dotenv()


def safe_float(value, default=None):
    if value is None:
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_bool(value):
    return bool(value)


def safe_div(num, den):
    if den == 0:
        return None
    return num / den


def fetch_rows(database_url: str) -> List[Dict]:
    query = """
    SELECT
        visual_label,
        area_ha,
        compactness,
        n_points,
        qc_score,

        built_cover_percent,
        dynamic_built_mean,
        dynamic_built_p95,

        n_observations,
        ndvi_median,
        evi_median,
        ndmi_median,
        bsi_median,

        predicted_baseline_candidate
    FROM landcover_olive_visual_training_eval_v1
    WHERE visual_label IN ('olive_like', 'not_olive_like', 'uncertain');
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

    result = []

    for row in rows:
        result.append(
            {
                "visual_label": row[0],
                "area_ha": safe_float(row[1]),
                "compactness": safe_float(row[2]),
                "n_points": int(row[3] or 0),
                "qc_score": safe_float(row[4]),

                "built_cover_percent": safe_float(row[5], 0.0),
                "dynamic_built_mean": safe_float(row[6], 0.0),
                "dynamic_built_p95": safe_float(row[7], 0.0),

                "n_observations": int(row[8] or 0),
                "ndvi_median": safe_float(row[9]),
                "evi_median": safe_float(row[10]),
                "ndmi_median": safe_float(row[11]),
                "bsi_median": safe_float(row[12]),

                "predicted_baseline_candidate": safe_bool(row[13]),
            }
        )

    return result


def evaluate_predictions(rows: List[Dict], predictions: List[bool]) -> Dict:
    tp = fp = fn = tn = 0
    uncertain_pass = 0
    uncertain_total = 0

    for row, pred in zip(rows, predictions):
        label = row["visual_label"]

        if label == "uncertain":
            uncertain_total += 1
            if pred:
                uncertain_pass += 1
            continue

        if label == "olive_like" and pred:
            tp += 1
        elif label == "olive_like" and not pred:
            fn += 1
        elif label == "not_olive_like" and pred:
            fp += 1
        elif label == "not_olive_like" and not pred:
            tn += 1

    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    specificity = safe_div(tn, tn + fp)
    accuracy = safe_div(tp + tn, tp + fp + fn + tn)

    if precision is None or recall is None or precision + recall == 0:
        f1 = None
    else:
        f1 = (2 * precision * recall) / (precision + recall)

    uncertain_pass_rate = safe_div(uncertain_pass, uncertain_total)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision or 0.0,
        "recall": recall or 0.0,
        "specificity": specificity or 0.0,
        "accuracy": accuracy or 0.0,
        "f1": f1 or 0.0,
        "uncertain_pass": uncertain_pass,
        "uncertain_total": uncertain_total,
        "uncertain_pass_rate": uncertain_pass_rate or 0.0,
    }


def predict_rule(row: Dict, params: Dict) -> bool:
    ndvi = row["ndvi_median"]
    evi = row["evi_median"]
    ndmi = row["ndmi_median"]
    bsi = row["bsi_median"]

    if ndvi is None or evi is None or ndmi is None or bsi is None:
        return False

    if row["n_observations"] < params["min_obs"]:
        return False

    urban_ok = (
        row["built_cover_percent"] <= params["built_max"]
        and row["dynamic_built_mean"] <= params["dw_mean_max"]
        and row["dynamic_built_p95"] <= params["dw_p95_max"]
    )

    spectral_ok = (
        ndvi >= params["ndvi_min"]
        and evi >= params["evi_min"]
        and ndmi >= params["ndmi_min"]
        and bsi <= params["bsi_max"]
    )

    geometry_ok = (
        row["area_ha"] is not None
        and row["compactness"] is not None
        and row["area_ha"] >= params["area_min"]
        and row["area_ha"] <= params["area_max"]
        and row["compactness"] >= params["compactness_min"]
        and row["n_points"] <= params["n_points_max"]
    )

    return urban_ok and spectral_ok and geometry_ok


def format_metric(value):
    return f"{value:.4f}"


def print_metrics(title: str, metrics: Dict):
    print("")
    print(title)
    print("-" * len(title))
    print(
        f"TP={metrics['tp']} FP={metrics['fp']} "
        f"FN={metrics['fn']} TN={metrics['tn']} "
        f"precision={format_metric(metrics['precision'])} "
        f"recall={format_metric(metrics['recall'])} "
        f"specificity={format_metric(metrics['specificity'])} "
        f"f1={format_metric(metrics['f1'])} "
        f"accuracy={format_metric(metrics['accuracy'])} "
        f"uncertain_pass={metrics['uncertain_pass']}/{metrics['uncertain_total']} "
        f"uncertain_rate={format_metric(metrics['uncertain_pass_rate'])}"
    )


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    rows = fetch_rows(database_url)

    print(f"Record valutati: {len(rows)}")
    print(f"olive_like: {sum(1 for r in rows if r['visual_label'] == 'olive_like')}")
    print(f"not_olive_like: {sum(1 for r in rows if r['visual_label'] == 'not_olive_like')}")
    print(f"uncertain: {sum(1 for r in rows if r['visual_label'] == 'uncertain')}")

    current_predictions = [
        bool(row["predicted_baseline_candidate"])
        for row in rows
    ]

    current_metrics = evaluate_predictions(rows, current_predictions)
    print_metrics("Current rule excluding uncertain from metrics", current_metrics)

    grid = {
        "built_max": [0.25, 0.50, 1.00, 2.00, 3.00, 5.00],
        "dw_mean_max": [0.04, 0.06, 0.08, 0.10, 0.15],
        "dw_p95_max": [0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30],
        "ndvi_min": [0.28, 0.32, 0.35, 0.40, 0.45, 0.50],
        "evi_min": [0.12, 0.16, 0.18, 0.20, 0.24, 0.28, 0.32],
        "ndmi_min": [-0.35, -0.20, -0.10, 0.00, 0.05],
        "bsi_max": [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.32],
        "min_obs": [50, 80, 100],
        "area_min": [0.50],
        "area_max": [15.00],
        "compactness_min": [0.12, 0.18, 0.24],
        "n_points_max": [120, 160, 180],
    }

    keys = list(grid.keys())
    candidates = []

    total = 0

    for values in itertools.product(*(grid[key] for key in keys)):
        params = dict(zip(keys, values))

        predictions = [
            predict_rule(row, params)
            for row in rows
        ]

        metrics = evaluate_predictions(rows, predictions)

        total += 1

        if metrics["tp"] == 0:
            continue

        # Baseline: priorità a precisione e controllo falsi positivi.
        score = (
            metrics["precision"] * 0.45
            + metrics["specificity"] * 0.25
            + metrics["f1"] * 0.20
            + metrics["recall"] * 0.10
            - metrics["uncertain_pass_rate"] * 0.15
        )

        candidate = {
            **params,
            **metrics,
            "score": score,
        }

        candidates.append(candidate)

    print("")
    print(f"Combinazioni testate: {total}")
    print(f"Candidati validi: {len(candidates)}")

    conservative = [
        c for c in candidates
        if c["precision"] >= 0.85
        and c["recall"] >= 0.35
        and c["specificity"] >= 0.70
    ]

    if not conservative:
        conservative = [
            c for c in candidates
            if c["precision"] >= 0.80
            and c["recall"] >= 0.35
            and c["specificity"] >= 0.60
        ]

    conservative = sorted(
        conservative,
        key=lambda c: (
            c["score"],
            c["precision"],
            c["specificity"],
            c["f1"],
            -c["uncertain_pass_rate"],
        ),
        reverse=True,
    )

    print("")
    print("TOP conservative rules")
    print("----------------------")

    for i, c in enumerate(conservative[:20], start=1):
        print(
            f"{i:02d} | score={c['score']:.4f} "
            f"TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} "
            f"precision={c['precision']:.4f} recall={c['recall']:.4f} "
            f"specificity={c['specificity']:.4f} f1={c['f1']:.4f} "
            f"uncertain_rate={c['uncertain_pass_rate']:.4f} "
            f"| built<={c['built_max']} dw_mean<={c['dw_mean_max']} "
            f"dw_p95<={c['dw_p95_max']} ndvi>={c['ndvi_min']} "
            f"evi>={c['evi_min']} ndmi>={c['ndmi_min']} "
            f"bsi<={c['bsi_max']} min_obs>={c['min_obs']} "
            f"compactness>={c['compactness_min']} n_points<={c['n_points_max']}"
        )

    precision_sorted = sorted(
        candidates,
        key=lambda c: (
            c["precision"],
            c["recall"],
            c["specificity"],
            c["f1"],
            -c["uncertain_pass_rate"],
        ),
        reverse=True,
    )

    print("")
    print("TOP precision rules")
    print("-------------------")

    for i, c in enumerate(precision_sorted[:20], start=1):
        print(
            f"{i:02d} | score={c['score']:.4f} "
            f"TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} "
            f"precision={c['precision']:.4f} recall={c['recall']:.4f} "
            f"specificity={c['specificity']:.4f} f1={c['f1']:.4f} "
            f"uncertain_rate={c['uncertain_pass_rate']:.4f} "
            f"| built<={c['built_max']} dw_mean<={c['dw_mean_max']} "
            f"dw_p95<={c['dw_p95_max']} ndvi>={c['ndvi_min']} "
            f"evi>={c['evi_min']} ndmi>={c['ndmi_min']} "
            f"bsi<={c['bsi_max']} min_obs>={c['min_obs']} "
            f"compactness>={c['compactness_min']} n_points<={c['n_points_max']}"
        )


if __name__ == "__main__":
    main()