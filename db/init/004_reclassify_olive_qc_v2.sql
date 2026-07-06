DELETE FROM landcover_subtype_geometry_qc
WHERE qc_version = 'olive_pure_geom_qc_v2'
  AND subtype_id = 'olive_pure';

WITH base AS (
    SELECT
        source_geometry_id,
        subtype_id,
        source_layer_version,
        area_ha,
        perimeter_m,
        compactness,
        n_points,
        n_parts,
        is_valid
    FROM landcover_subtype_geometry_qc
    WHERE qc_version = 'olive_pure_geom_qc_v1'
      AND subtype_id = 'olive_pure'
),
classified AS (
    SELECT
        *,
        CASE
            WHEN area_ha < 0.50 THEN 'too_small'
            WHEN area_ha > 15.00 THEN 'too_large_for_baseline'
            ELSE 'ok'
        END AS area_flag,

        CASE
            WHEN compactness < 0.05 THEN 'very_irregular'
            WHEN compactness < 0.12 THEN 'irregular'
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
    FROM base
),
scored AS (
    SELECT
        *,
        (
            CASE WHEN is_valid THEN 25 ELSE 0 END +

            CASE
                WHEN area_flag = 'ok' THEN 25
                WHEN area_flag = 'too_large_for_baseline' THEN 5
                ELSE 0
            END +

            CASE
                WHEN shape_flag = 'ok' THEN 20
                WHEN shape_flag = 'irregular' THEN 8
                ELSE 0
            END +

            CASE
                WHEN complexity_flag = 'ok' THEN 15
                WHEN complexity_flag = 'complex' THEN 6
                ELSE 0
            END +

            CASE
                WHEN multipart_flag = 'ok' THEN 15
                WHEN multipart_flag = 'multipart' THEN 5
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
            WHEN area_flag = 'too_large_for_baseline' THEN 'area_above_baseline_threshold'
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
    'olive_pure_geom_qc_v2',
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
        WHEN qc_class = 'high_confidence' THEN TRUE
        ELSE FALSE
    END AS usable_for_baseline,

    exclusion_reason
FROM final;

CREATE OR REPLACE VIEW landcover_olive_pure_high_confidence_v2 AS
SELECT
    g.id,
    g.subtype_id,
    g.source_layer_version,
    g.source_file,
    g.source_feature_id,
    q.qc_version,
    q.area_ha,
    q.perimeter_m,
    q.compactness,
    q.n_points,
    q.n_parts,
    q.qc_score,
    q.qc_class,
    q.usable_for_matching,
    q.usable_for_baseline,
    g.geom
FROM landcover_subtype_geometries g
JOIN landcover_subtype_geometry_qc q
  ON q.source_geometry_id = g.id
WHERE g.subtype_id = 'olive_pure'
  AND q.qc_version = 'olive_pure_geom_qc_v2'
  AND q.qc_class = 'high_confidence'
  AND q.usable_for_baseline = TRUE;