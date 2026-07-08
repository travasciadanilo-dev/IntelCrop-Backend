-- ============================================================
-- 021_remove_visual_review_confidence.sql
-- IntelCrop Calabria
--
-- Rimuove review_confidence_v2 dal workflow visual review v2.
--
-- Razionale:
-- - la label visuale primaria resta olive_like / not_olive_like / uncertain
-- - plantation_pattern_v2 resta una variabile fotointerpretativa
-- - l'affidabilità viene stimata dal modello, non assegnata soggettivamente
-- ============================================================


DROP VIEW IF EXISTS olive_visual_review_sample_v2_training_v1 CASCADE;
DROP VIEW IF EXISTS olive_visual_review_sample_v2_summary_v1 CASCADE;


ALTER TABLE olive_visual_review_sample_v2_labels
DROP CONSTRAINT IF EXISTS olive_visual_review_sample_v2_confidence_check;


ALTER TABLE olive_visual_review_sample_v2_labels
DROP COLUMN IF EXISTS review_confidence_v2;


UPDATE olive_visual_review_sample_v2_labels
SET
    is_complete = (
        visual_label_v2 IS NOT NULL
        AND plantation_pattern_v2 IS NOT NULL
    ),
    is_training_eligible = (
        visual_label_v2 IN ('olive_like', 'not_olive_like')
        AND plantation_pattern_v2 IS NOT NULL
    );


CREATE OR REPLACE VIEW olive_visual_review_sample_v2_summary_v1 AS
SELECT
    COUNT(*) AS n_samples,

    COUNT(*) FILTER (
        WHERE visual_label_v2 IS NOT NULL
          AND plantation_pattern_v2 IS NOT NULL
    ) AS n_complete,

    COUNT(*) FILTER (
        WHERE visual_label_v2 IS NULL
           OR plantation_pattern_v2 IS NULL
    ) AS n_incomplete,

    COUNT(*) FILTER (
        WHERE visual_label_v2 IN ('olive_like', 'not_olive_like')
          AND plantation_pattern_v2 IS NOT NULL
    ) AS n_training_eligible,

    COUNT(*) FILTER (WHERE visual_label_v2 = 'olive_like') AS n_olive_like,
    COUNT(*) FILTER (WHERE visual_label_v2 = 'not_olive_like') AS n_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label_v2 = 'uncertain') AS n_uncertain,

    COUNT(*) FILTER (WHERE plantation_pattern_v2 = 'plantation_like') AS n_plantation_like,
    COUNT(*) FILTER (WHERE plantation_pattern_v2 = 'mixed_or_sparse') AS n_mixed_or_sparse,
    COUNT(*) FILTER (WHERE plantation_pattern_v2 = 'not_assessable') AS n_not_assessable,

    COUNT(*) FILTER (WHERE spatial_validation_zone = 'north_calabria') AS n_north_calabria,
    COUNT(*) FILTER (WHERE spatial_validation_zone = 'central_calabria') AS n_central_calabria,
    COUNT(*) FILTER (WHERE spatial_validation_zone = 'south_calabria') AS n_south_calabria,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_current_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_added_candidates

FROM olive_visual_review_sample_v2_labels;


CREATE OR REPLACE VIEW olive_visual_review_sample_v2_training_v1 AS
SELECT
    l.*,

    CASE
        WHEN l.visual_label_v2 = 'olive_like' THEN 1
        WHEN l.visual_label_v2 = 'not_olive_like' THEN 0
        ELSE NULL
    END AS binary_visual_label_v2,

    CASE
        WHEN l.plantation_pattern_v2 = 'plantation_like' THEN 1
        WHEN l.plantation_pattern_v2 = 'mixed_or_sparse' THEN 0
        ELSE NULL
    END AS binary_plantation_pattern_v2

FROM olive_visual_review_sample_v2_labels l
WHERE l.visual_label_v2 IN ('olive_like', 'not_olive_like')
  AND l.plantation_pattern_v2 IN ('plantation_like', 'mixed_or_sparse', 'not_assessable');