CREATE OR REPLACE VIEW landcover_olive_visual_training_eval_strict_v1 AS
WITH scored AS (
    SELECT
        e.*,

        (
            COALESCE(e.built_cover_percent, 999) <= 2.0
            AND COALESCE(e.dynamic_built_mean, 999) <= 0.15
            AND COALESCE(e.dynamic_built_p95, 999) <= 0.20

            AND COALESCE(e.n_observations, 0) >= 80
            AND COALESCE(e.ndvi_median, -999) >= 0.32
            AND COALESCE(e.evi_median, -999) >= 0.16
            AND COALESCE(e.ndmi_median, -999) >= -0.20
            AND COALESCE(e.bsi_median, 999) <= 0.10

            AND COALESCE(e.area_ha, 0) >= 0.50
            AND COALESCE(e.area_ha, 999999) <= 15.00
            AND COALESCE(e.compactness, 0) >= 0.12
            AND COALESCE(e.n_points, 999999) <= 120
        ) AS predicted_auto_strict_candidate

    FROM landcover_olive_visual_training_eval_v1 e
)
SELECT
    scored.*,

    CASE
        WHEN visual_label = 'olive_like'
             AND predicted_auto_strict_candidate = TRUE
        THEN 'true_positive'

        WHEN visual_label = 'olive_like'
             AND predicted_auto_strict_candidate = FALSE
        THEN 'false_negative'

        WHEN visual_label = 'not_olive_like'
             AND predicted_auto_strict_candidate = TRUE
        THEN 'false_positive'

        WHEN visual_label = 'not_olive_like'
             AND predicted_auto_strict_candidate = FALSE
        THEN 'true_negative'

        WHEN visual_label = 'uncertain'
             AND predicted_auto_strict_candidate = TRUE
        THEN 'uncertain_pass'

        WHEN visual_label = 'uncertain'
             AND predicted_auto_strict_candidate = FALSE
        THEN 'uncertain_reject'

        ELSE 'unclassified'
    END AS eval_class_strict

FROM scored;


CREATE OR REPLACE VIEW landcover_olive_pure_baseline_strict_seed_v1 AS
SELECT
    b.*
FROM landcover_olive_pure_baseline_v1 b
JOIN landcover_olive_visual_training_eval_strict_v1 e
  ON e.id = b.id
WHERE e.visual_label = 'olive_like'
  AND e.predicted_auto_strict_candidate = TRUE;