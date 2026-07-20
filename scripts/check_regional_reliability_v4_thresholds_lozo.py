from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix


INPUT_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_lozo_predictions.csv"
)

OUTPUT_PATH = Path(
    "outputs/"
    "regional_reliability_v4_ridge_thresholds_lozo.csv"
)

MODEL_NAME = "combined_ridge"

THRESHOLDS = {
    "screening": 0.61,
    "high": 0.77,
    "very_high": 0.82,
}


def calculate_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> dict:
    predictions = (
        probabilities >= threshold
    ).astype(int)

    tn, fp, fn, tp = confusion_matrix(
        y_true,
        predictions,
        labels=[0, 1],
    ).ravel()

    precision = (
        tp / (tp + fp)
        if (tp + fp) > 0
        else np.nan
    )

    recall = (
        tp / (tp + fn)
        if (tp + fn) > 0
        else np.nan
    )

    specificity = (
        tn / (tn + fp)
        if (tn + fp) > 0
        else np.nan
    )

    npv = (
        tn / (tn + fn)
        if (tn + fn) > 0
        else np.nan
    )

    f1 = (
        2.0 * precision * recall
        / (precision + recall)
        if (
            np.isfinite(precision)
            and np.isfinite(recall)
            and (precision + recall) > 0
        )
        else np.nan
    )

    return {
        "selected_n": int(predictions.sum()),
        "selected_percent": float(
            predictions.mean() * 100.0
        ),
        "precision": float(precision),
        "recall": float(recall),
        "specificity": float(specificity),
        "npv": float(npv),
        "f1": float(f1),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
        "youden_j": float(
            recall + specificity - 1.0
        ),
    }


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"File non trovato: {INPUT_PATH}"
        )

    df = pd.read_csv(INPUT_PATH)

    df = df[
        df["model_name"] == MODEL_NAME
    ].copy()

    required_columns = {
        "model_name",
        "held_out_zone",
        "area_id",
        "target",
        "probability",
    }

    missing_columns = required_columns.difference(
        df.columns
    )

    if missing_columns:
        raise RuntimeError(
            "Colonne mancanti: "
            + ", ".join(sorted(missing_columns))
        )

    if len(df) != 406:
        raise RuntimeError(
            f"Attese 406 predizioni LOZO Ridge, "
            f"trovate {len(df)}."
        )

    if df["area_id"].duplicated().any():
        raise RuntimeError(
            "Ogni area deve comparire una sola volta "
            "nelle predizioni LOZO."
        )

    rows = []

    groups = [
        ("all_calabria", df),
        *[
            (zone, group.copy())
            for zone, group in df.groupby(
                "held_out_zone"
            )
        ],
    ]

    for zone, group in groups:
        y_true = group["target"].to_numpy(
            dtype=int
        )

        probabilities = group[
            "probability"
        ].to_numpy(dtype=float)

        for level, threshold in THRESHOLDS.items():
            metrics = calculate_metrics(
                y_true=y_true,
                probabilities=probabilities,
                threshold=threshold,
            )

            rows.append(
                {
                    "validation": "leave_one_zone_out",
                    "zone": zone,
                    "level": level,
                    "threshold": threshold,
                    "n": len(group),
                    "positive_n": int(y_true.sum()),
                    "negative_n": int(
                        len(y_true) - y_true.sum()
                    ),
                    **metrics,
                }
            )

    results = pd.DataFrame(rows)

    results.to_csv(
        OUTPUT_PATH,
        index=False,
    )

    print(
        results[
            [
                "zone",
                "level",
                "threshold",
                "n",
                "selected_n",
                "precision",
                "recall",
                "specificity",
                "f1",
                "fp",
                "fn",
                "youden_j",
            ]
        ].to_string(index=False)
    )

    print()
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
