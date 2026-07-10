from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)


INPUT_PATH = Path(
    "outputs/"
    "regional_reliability_v4_penalties_aggregated_predictions.csv"
)

OUTPUT_PATH = Path(
    "outputs/"
    "regional_reliability_v4_ridge_thresholds.csv"
)

MODEL_NAME = "combined_ridge"


def calculate_threshold_metrics(
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

    return {
        "threshold": threshold,
        "selected_n": int(predictions.sum()),
        "selected_percent": float(
            predictions.mean() * 100.0
        ),
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
        "npv": float(npv),
        "f1": float(
            f1_score(
                y_true,
                predictions,
                zero_division=0,
            )
        ),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
        "youden_j": float(
            recall_score(
                y_true,
                predictions,
                zero_division=0,
            )
            + specificity
            - 1.0
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

    if len(df) != 406:
        raise RuntimeError(
            f"Attese 406 righe Ridge, trovate {len(df)}."
        )

    if df["area_id"].duplicated().any():
        raise RuntimeError("area_id duplicati.")

    y_true = df["target"].to_numpy()
    probabilities = df["probability"].to_numpy()

    thresholds = np.round(
        np.arange(0.05, 0.951, 0.01),
        2,
    )

    rows = [
        calculate_threshold_metrics(
            y_true,
            probabilities,
            float(threshold),
        )
        for threshold in thresholds
    ]

    results = pd.DataFrame(rows)

    results.to_csv(
        OUTPUT_PATH,
        index=False,
    )

    best_youden = results.loc[
        results["youden_j"].idxmax()
    ]

    best_f1 = results.loc[
        results["f1"].idxmax()
    ]

    recall_90 = results[
        results["recall"] >= 0.90
    ].sort_values(
        ["specificity", "precision"],
        ascending=False,
    ).head(1)

    precision_90 = results[
        results["precision"] >= 0.90
    ].sort_values(
        ["recall", "specificity"],
        ascending=False,
    ).head(1)

    specificity_80 = results[
        results["specificity"] >= 0.80
    ].sort_values(
        ["recall", "precision"],
        ascending=False,
    ).head(1)

    print("Regional reliability v4 Ridge thresholds")
    print("----------------------------------------")

    print()
    print("Best Youden J")
    print(best_youden.to_string())

    print()
    print("Best F1")
    print(best_f1.to_string())

    print()
    print("Best threshold with recall >= 0.90")
    if recall_90.empty:
        print("Nessuna soglia.")
    else:
        print(recall_90.iloc[0].to_string())

    print()
    print("Best threshold with precision >= 0.90")
    if precision_90.empty:
        print("Nessuna soglia.")
    else:
        print(precision_90.iloc[0].to_string())

    print()
    print("Best threshold with specificity >= 0.80")
    if specificity_80.empty:
        print("Nessuna soglia.")
    else:
        print(specificity_80.iloc[0].to_string())

    print()
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
