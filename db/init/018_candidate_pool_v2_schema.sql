-- ============================================================
-- 018_candidate_pool_v2_schema.sql
-- IntelCrop Calabria
--
-- Scopo:
-- estendere la cache geometrica con metriche raw calcolate a batch
-- e costruire candidate_pool_v2.
--
-- candidate_pool_v2 NON è un reference seed.
-- È un pool regionale più inclusivo su cui applicare QC/score.
--
-- Criterio V2:
-- - geometria valida
-- - geometria non vuota
-- - area raw >= 0.5 ha
--
-- high_confidence_v2 rimane una feature, non una barriera unica.
-- ============================================================


ALTER TABLE olive_geometry_sensitivity_cache_v1
ADD COLUMN IF NOT EXISTS area_ha_raw double precision;

ALTER TABLE olive_geometry_sensitivity_cache_v1
ADD COLUMN IF NOT EXISTS perimeter_m_raw double precision;

ALTER TABLE olive_geometry_sensitivity_cache_v1
ADD COLUMN IF NOT EXISTS compactness_raw double precision;

ALTER TABLE olive_geometry_sensitivity_cache_v1
ADD COLUMN IF NOT EXISTS area_bin_raw text;

ALTER TABLE olive_geometry_sensitivity_cache_v1
ADD COLUMN IF NOT EXISTS candidate_pool_v2 boolean NOT NULL DEFAULT false;


CREATE INDEX IF NOT EXISTS idx_olive_geometry_sensitivity_cache_area_raw
ON olive_geometry_sensitivity_cache_v1(area_ha_raw);

CREATE INDEX IF NOT EXISTS idx_olive_geometry_sensitivity_cache_candidate_pool_v2
ON olive_geometry_sensitivity_cache_v1(candidate_pool_v2);


DROP VIEW IF EXISTS olive_candidate_pool_v2 CASCADE;


CREATE OR REPLACE VIEW olive_candidate_pool_v2 AS
SELECT
    c.*
FROM olive_geometry_sensitivity_cache_v1 c
WHERE c.geom_is_valid = true
  AND c.geom_is_empty = false
  AND c.area_ha_raw >= 0.5;


DROP VIEW IF EXISTS olive_candidate_pool_v2_summary CASCADE;


CREATE OR REPLACE VIEW olive_candidate_pool_v2_summary AS
SELECT
    COUNT(*) AS n_candidate_pool_v2,
    ROUND(SUM(area_ha_raw)::numeric, 2) AS total_area_ha_raw,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_already_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_added_vs_current,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference

FROM olive_candidate_pool_v2;


DROP VIEW IF EXISTS olive_candidate_pool_v2_by_zone CASCADE;


CREATE OR REPLACE VIEW olive_candidate_pool_v2_by_zone AS
SELECT
    spatial_validation_zone,

    COUNT(*) AS n_candidate_pool_v2,
    ROUND(SUM(area_ha_raw)::numeric, 2) AS total_area_ha_raw,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_already_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_added_vs_current,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference,

    ROUND(
        (
            COUNT(*) FILTER (WHERE current_high_confidence_v2 = true)::numeric
            / NULLIF(COUNT(*)::numeric, 0)
        ) * 100,
        2
    ) AS already_high_confidence_pct

FROM olive_candidate_pool_v2
GROUP BY spatial_validation_zone
ORDER BY spatial_validation_zone;


DROP VIEW IF EXISTS olive_candidate_pool_v2_area_bins CASCADE;


CREATE OR REPLACE VIEW olive_candidate_pool_v2_area_bins AS
SELECT
    spatial_validation_zone,
    area_bin_raw,

    COUNT(*) AS n_areas,
    ROUND(SUM(area_ha_raw)::numeric, 2) AS total_area_ha_raw,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_already_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_added_vs_current,

    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference

FROM olive_candidate_pool_v2
GROUP BY spatial_validation_zone, area_bin_raw
ORDER BY spatial_validation_zone, area_bin_raw;