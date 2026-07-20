-- ============================================================
-- 020_visual_review_sample_v2_schema.sql
-- IntelCrop Calabria
--
-- Scopo:
-- salvare le revisioni visuali del sample v2.
--
-- Le label importate non aggiornano automaticamente il modello.
-- Verranno usate per calibrare regional_reliability_score_v3.
-- ============================================================


DROP VIEW IF EXISTS olive_visual_review_sample_v2_training_v1 CASCADE;
DROP VIEW IF EXISTS olive_visual_review_sample_v2_summary_v1 CASCADE;

CREATE TABLE IF NOT EXISTS olive_visual_review_sample_v2_labels (
    sample_id text PRIMARY KEY,

    sample_version text NOT NULL DEFAULT 'olive_visual_review_sample_v2',
    source_pool_version text NOT NULL DEFAULT 'candidate_pool_v2_area_ge_0_5',

    area_id text NOT NULL,
    source_geometry_id text,

    spatial_validation_zone text,
    candidate_origin text,

    sample_stratum text,
    sample_stratum_description text,

    area_ha_raw double precision,
    area_bin_raw text,

    n_points integer,
    n_parts integer,
    n_points_bin text,
    n_parts_bin text,

    current_high_confidence_v2 boolean,
    identity_reference_match boolean,
    strict_reference_match boolean,

    large_polygon_flag boolean,
    small_candidate_flag boolean,
    complex_boundary_flag boolean,

    previous_visual_label text,

    review_priority_score integer,
    review_priority_reason text,

    label_lon double precision,
    label_lat double precision,

    visual_label_v2 text,
    plantation_pattern_v2 text,
    review_confidence_v2 text,
    review_notes_v2 text,

    is_complete boolean NOT NULL DEFAULT false,
    is_training_eligible boolean NOT NULL DEFAULT false,

    source_file text,
    imported_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT olive_visual_review_sample_v2_visual_label_check
        CHECK (
            visual_label_v2 IS NULL
            OR visual_label_v2 IN ('olive_like', 'not_olive_like', 'uncertain')
        ),

    CONSTRAINT olive_visual_review_sample_v2_pattern_check
        CHECK (
            plantation_pattern_v2 IS NULL
            OR plantation_pattern_v2 IN ('plantation_like', 'mixed_or_sparse', 'not_assessable')
        ),

    CONSTRAINT olive_visual_review_sample_v2_confidence_check
        CHECK (
            review_confidence_v2 IS NULL
            OR review_confidence_v2 IN ('high', 'medium', 'low')
        )
);


CREATE INDEX IF NOT EXISTS idx_olive_visual_review_sample_v2_area_id
ON olive_visual_review_sample_v2_labels(area_id);

CREATE INDEX IF NOT EXISTS idx_olive_visual_review_sample_v2_zone
ON olive_visual_review_sample_v2_labels(spatial_validation_zone);

CREATE INDEX IF NOT EXISTS idx_olive_visual_review_sample_v2_training
ON olive_visual_review_sample_v2_labels(is_training_eligible);

CREATE INDEX IF NOT EXISTS idx_olive_visual_review_sample_v2_visual_label
ON olive_visual_review_sample_v2_labels(visual_label_v2);


CREATE OR REPLACE VIEW olive_visual_review_sample_v2_summary_v1 AS
SELECT
    COUNT(*) AS n_samples,

    COUNT(*) FILTER (WHERE is_complete = true) AS n_complete,
    COUNT(*) FILTER (WHERE is_complete = false) AS n_incomplete,

    COUNT(*) FILTER (WHERE is_training_eligible = true) AS n_training_eligible,

    COUNT(*) FILTER (WHERE visual_label_v2 = 'olive_like') AS n_olive_like,
    COUNT(*) FILTER (WHERE visual_label_v2 = 'not_olive_like') AS n_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label_v2 = 'uncertain') AS n_uncertain,

    COUNT(*) FILTER (WHERE plantation_pattern_v2 = 'plantation_like') AS n_plantation_like,
    COUNT(*) FILTER (WHERE plantation_pattern_v2 = 'mixed_or_sparse') AS n_mixed_or_sparse,
    COUNT(*) FILTER (WHERE plantation_pattern_v2 = 'not_assessable') AS n_not_assessable,

    COUNT(*) FILTER (WHERE review_confidence_v2 = 'high') AS n_confidence_high,
    COUNT(*) FILTER (WHERE review_confidence_v2 = 'medium') AS n_confidence_medium,
    COUNT(*) FILTER (WHERE review_confidence_v2 = 'low') AS n_confidence_low,

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
    END AS binary_visual_label_v2

FROM olive_visual_review_sample_v2_labels l
WHERE l.is_training_eligible = true
  AND l.visual_label_v2 IN ('olive_like', 'not_olive_like')
  AND l.review_confidence_v2 IN ('high', 'medium');