import os
from typing import List

import psycopg2
from dotenv import load_dotenv


load_dotenv()


QC_VERSION = "olive_pure_geom_qc_v2"
BATCH_SIZE = int(os.getenv("OLIVE_QC_BATCH_SIZE", "500"))


def chunks(values: List[str], size: int):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    fetch_ids_sql = """
    SELECT id::text
    FROM landcover_subtype_geometries
    WHERE subtype_id = 'olive_pure'
      AND source_layer_version = 'cut_calabria_v1'
    ORDER BY id;
    """

    delete_sql = """
    DELETE FROM landcover_subtype_geometry_qc
    WHERE qc_version = %(qc_version)s
      AND subtype_id = 'olive_pure';
    """

    insert_batch_sql = """
    WITH base AS (
        SELECT
            id AS source_geometry_id,
            subtype_id,
            source_layer_version,
            geom,
            ST_IsValid(geom) AS is_valid,
            ST_NPoints(geom) AS n_points,
            ST_NumGeometries(geom) AS n_parts,
            ST_Area(geom::geography) AS area_m2,
            ST_Perimeter(geom::geography) AS perimeter_m
        FROM landcover_subtype_geometries
        WHERE id = ANY(%(ids)s::uuid[])
          AND subtype_id = 'olive_pure'
          AND source_layer_version = 'cut_calabria_v1'
    ),
    metrics AS (
        SELECT
            source_geometry_id,
            subtype_id,
            source_layer_version,
            area_m2 / 10000.0 AS area_ha,
            perimeter_m,
            CASE
                WHEN perimeter_m > 0 THEN
                    (4.0 * pi() * area_m2) / (perimeter_m ^ 2)
                ELSE 0
            END AS compactness,
            n_points,
            n_parts,
            is_valid
        FROM base
    ),
    classified AS (
        SELECT
            *,
            CASE
                WHEN area_ha < 0.30 THEN 'too_small'
                WHEN area_ha < 0.50 THEN 'small'
                WHEN area_ha > 15.00 THEN 'too_large_for_baseline'
                ELSE 'ok'
            END AS area_flag,

            CASE
                WHEN compactness < 0.05 THEN 'very_irregular'
                WHEN compactness < 0.10 THEN 'irregular'
                ELSE 'ok'
            END AS shape_flag,

            CASE
                WHEN n_points > 300 THEN 'too_complex'
                WHEN n_points > 180 THEN 'complex'
                ELSE 'ok'
            END AS complexity_flag,

            CASE
                WHEN n_parts > 3 THEN 'too_fragmented'
                WHEN n_parts > 1 THEN 'multipart'
                ELSE 'ok'
            END AS multipart_flag
        FROM metrics
    ),
    scored AS (
        SELECT
            *,
            (
                CASE WHEN is_valid THEN 25 ELSE 0 END +
                CASE
                    WHEN area_flag = 'ok' THEN 25
                    WHEN area_flag = 'too_large_for_baseline' THEN 10
                    ELSE 0
                END +
                CASE
                    WHEN shape_flag = 'ok' THEN 20
                    WHEN shape_flag = 'irregular' THEN 10
                    ELSE 0
                END +
                CASE
                    WHEN complexity_flag = 'ok' THEN 15
                    WHEN complexity_flag = 'complex' THEN 8
                    ELSE 0
                END +
                CASE
                    WHEN multipart_flag = 'ok' THEN 15
                    WHEN multipart_flag = 'multipart' THEN 8
                    ELSE 0
                END
            ) AS qc_score
        FROM classified
    ),
    final AS (
        SELECT
            *,
            CASE
                WHEN NOT is_valid THEN 'excluded'
                WHEN area_flag = 'too_small' THEN 'excluded'

                WHEN area_flag = 'ok'
                  AND shape_flag = 'ok'
                  AND complexity_flag = 'ok'
                  AND multipart_flag = 'ok'
                  AND qc_score >= 90
                THEN 'high_confidence'

                WHEN qc_score >= 70 THEN 'medium_confidence'
                WHEN qc_score >= 50 THEN 'low_confidence'
                ELSE 'excluded'
            END AS qc_class,

            CASE
                WHEN NOT is_valid THEN 'invalid_geometry'
                WHEN area_flag = 'too_small' THEN 'area_below_minimum_threshold'
                WHEN shape_flag = 'very_irregular' THEN 'very_irregular_geometry'
                WHEN complexity_flag = 'too_complex' THEN 'excessive_geometry_complexity'
                WHEN multipart_flag = 'too_fragmented' THEN 'excessive_fragmentation'
                ELSE NULL
            END AS exclusion_reason
        FROM scored
    )
    INSERT INTO landcover_subtype_geometry_qc (
        source_geometry_id,
        subtype_id,
        source_layer_version,
        qc_version,
        area_ha,
        perimeter_m,
        compactness,
        n_points,
        n_parts,
        is_valid,
        area_flag,
        shape_flag,
        complexity_flag,
        multipart_flag,
        qc_score,
        qc_class,
        usable_for_matching,
        usable_for_baseline,
        exclusion_reason
    )
    SELECT
        source_geometry_id,
        subtype_id,
        source_layer_version,
        %(qc_version)s,
        area_ha,
        perimeter_m,
        compactness,
        n_points,
        n_parts,
        is_valid,
        area_flag,
        shape_flag,
        complexity_flag,
        multipart_flag,
        qc_score,
        qc_class,

        CASE
            WHEN qc_class IN ('high_confidence', 'medium_confidence') THEN TRUE
            ELSE FALSE
        END AS usable_for_matching,

        CASE
            WHEN qc_class = 'high_confidence'
              AND area_flag = 'ok'
              AND shape_flag = 'ok'
              AND complexity_flag = 'ok'
              AND multipart_flag = 'ok'
            THEN TRUE
            ELSE FALSE
        END AS usable_for_baseline,

        exclusion_reason
    FROM final
    ON CONFLICT (source_geometry_id, qc_version)
    DO UPDATE SET
        area_ha = EXCLUDED.area_ha,
        perimeter_m = EXCLUDED.perimeter_m,
        compactness = EXCLUDED.compactness,
        n_points = EXCLUDED.n_points,
        n_parts = EXCLUDED.n_parts,
        is_valid = EXCLUDED.is_valid,
        area_flag = EXCLUDED.area_flag,
        shape_flag = EXCLUDED.shape_flag,
        complexity_flag = EXCLUDED.complexity_flag,
        multipart_flag = EXCLUDED.multipart_flag,
        qc_score = EXCLUDED.qc_score,
        qc_class = EXCLUDED.qc_class,
        usable_for_matching = EXCLUDED.usable_for_matching,
        usable_for_baseline = EXCLUDED.usable_for_baseline,
        exclusion_reason = EXCLUDED.exclusion_reason,
        computed_at = now();
    """

    summary_sql = """
    SELECT
        qc_class,
        COUNT(*) AS n_features,
        ROUND(SUM(area_ha)::numeric, 2) AS total_area_ha,
        ROUND(MIN(area_ha)::numeric, 4) AS min_area_ha,
        ROUND(MAX(area_ha)::numeric, 2) AS max_area_ha,
        ROUND(AVG(qc_score)::numeric, 2) AS avg_qc_score
    FROM landcover_subtype_geometry_qc
    WHERE qc_version = %(qc_version)s
      AND subtype_id = 'olive_pure'
    GROUP BY qc_class
    ORDER BY
        CASE qc_class
            WHEN 'high_confidence' THEN 1
            WHEN 'medium_confidence' THEN 2
            WHEN 'low_confidence' THEN 3
            WHEN 'excluded' THEN 4
            ELSE 5
        END;
    """

    baseline_sql = """
    SELECT
        COUNT(*) AS baseline_features,
        ROUND(SUM(area_ha)::numeric, 2) AS baseline_area_ha
    FROM landcover_subtype_geometry_qc
    WHERE qc_version = %(qc_version)s
      AND subtype_id = 'olive_pure'
      AND usable_for_baseline = TRUE;
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(fetch_ids_sql)
            ids = [row[0] for row in cur.fetchall()]

        print(f"Geometrie olive_pure da processare: {len(ids)}")
        print(f"Batch size: {BATCH_SIZE}")

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(delete_sql, {"qc_version": QC_VERSION})
            conn.commit()

    processed = 0

    for batch_index, batch_ids in enumerate(chunks(ids, BATCH_SIZE), start=1):
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    insert_batch_sql,
                    {
                        "ids": batch_ids,
                        "qc_version": QC_VERSION,
                    },
                )
                conn.commit()

        processed += len(batch_ids)

        print(
            f"Batch {batch_index} completato | "
            f"processate {processed}/{len(ids)}"
        )

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(summary_sql, {"qc_version": QC_VERSION})
            rows = cur.fetchall()

            print("")
            print("QC summary")
            print("----------")
            for row in rows:
                print(
                    f"{row[0]} | n={row[1]} | area_ha={row[2]} | "
                    f"min={row[3]} | max={row[4]} | avg_score={row[5]}"
                )

            cur.execute(baseline_sql, {"qc_version": QC_VERSION})
            baseline = cur.fetchone()

            print("")
            print("Baseline candidates")
            print("-------------------")
            print(f"features={baseline[0]} | area_ha={baseline[1]}")


if __name__ == "__main__":
    main()