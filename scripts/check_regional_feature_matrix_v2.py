import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


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
    n_parts,
    approx_centroid_lat,

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


def main() -> None:
    with psycopg2.connect(DATABASE_URL) as conn:
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

    numeric = df.select_dtypes(include=[np.number]).copy()
    feature_columns = [
        column
        for column in numeric.columns
        if column != "target_visual_v2"
    ]

    unique_counts = numeric[feature_columns].nunique(dropna=False)

    constant_features = unique_counts[
        unique_counts <= 1
    ].index.tolist()

    near_constant_features = []

    for column in feature_columns:
        frequencies = numeric[column].value_counts(
            normalize=True,
            dropna=False,
        )

        if not frequencies.empty and frequencies.iloc[0] >= 0.95:
            near_constant_features.append(column)

    correlations = (
        numeric[feature_columns]
        .corr(method="spearman")
    )

    pairs = []

    for i, first in enumerate(feature_columns):
        for second in feature_columns[i + 1:]:
            value = correlations.loc[first, second]

            if abs(value) >= 0.80:
                pairs.append(
                    {
                        "feature_1": first,
                        "feature_2": second,
                        "spearman_rho": value,
                        "abs_rho": abs(value),
                    }
                )

    pairs_df = pd.DataFrame(pairs)

    if not pairs_df.empty:
        pairs_df = pairs_df.sort_values(
            "abs_rho",
            ascending=False,
        )

    output_dir = Path("outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    correlations.to_csv(
        output_dir / "regional_feature_matrix_v2_spearman.csv"
    )

    pairs_df.to_csv(
        output_dir / "regional_feature_matrix_v2_high_correlations.csv",
        index=False,
    )

    summary = df.groupby(
        ["spatial_validation_zone", "target_visual_v2"],
        dropna=False,
    ).size().rename("n").reset_index()

    summary.to_csv(
        output_dir / "regional_feature_matrix_v2_zone_class_counts.csv",
        index=False,
    )

    print("Regional feature matrix v2 QC")
    print("--------------------------------")
    print(f"rows: {len(df)}")
    print(f"positive: {(df['target_visual_v2'] == 1).sum()}")
    print(f"negative: {(df['target_visual_v2'] == 0).sum()}")
    print(f"zones: {df['spatial_validation_zone'].nunique()}")
    print(f"numeric features: {len(feature_columns)}")
    print(f"|rho| >= 0.80 pairs: {len(pairs_df)}")
    print(f"constant features: {constant_features}")
    print(f"near-constant features: {near_constant_features}")

    if not pairs_df.empty:
        print()
        print("Top correlated pairs")
        print(
            pairs_df.head(20)[
                ["feature_1", "feature_2", "spearman_rho"]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()
