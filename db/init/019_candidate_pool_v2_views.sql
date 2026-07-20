-- ============================================================
-- 019_candidate_pool_v2_views.sql
-- IntelCrop Calabria
--
-- Scopo:
-- organizzare il candidate_pool_v2 in livelli operativi puliti:
-- 1. screening pool
-- 2. analysis-ready pool preliminare
-- 3. priority review pool per nuovo campionamento visuale
--
-- Nessuna di queste view modifica strict/reference seed.
-- ============================================================


DROP VIEW IF EXISTS olive_candidate_pool_v2_screening CASCADE;
DROP VIEW IF EXISTS olive_candidate_pool_v2_analysis_ready_prelim CASCADE;
DROP VIEW IF EXISTS olive_candidate_pool_v2_review_priority CASCADE;
DROP VIEW IF EXISTS olive_candidate_pool_v2_operational_summary CASCADE;


CREATE OR REPLACE VIEW olive_candidate_pool_v2_screening AS
SELECT
    c.*,

    CASE
        WHEN c.current_high_confidence_v2 = true THEN 'current_high_confidence'
        ELSE 'added_candidate'
    END AS candidate_origin,

    CASE
        WHEN c.area_ha_raw >= 5.0 THEN true
        ELSE false
    END AS large_polygon_flag,

    CASE
        WHEN c.area_ha_raw >= 0.5 AND c.area_ha_raw < 1.0 THEN true
        ELSE false
    END AS small_candidate_flag,

    CASE
        WHEN c.n_points > 250 THEN true
        ELSE false
    END AS complex_boundary_flag,

    CASE
        WHEN c.current_high_confidence_v2 = true THEN 2
        ELSE 1
    END AS geometric_prior_rank

FROM olive_geometry_sensitivity_cache_v1 c
WHERE c.geom_is_valid = true
  AND c.geom_is_empty = false
  AND c.area_ha_raw >= 0.5;


CREATE OR REPLACE VIEW olive_candidate_pool_v2_analysis_ready_prelim AS
SELECT
    *
FROM olive_candidate_pool_v2_screening
WHERE area_ha_raw >= 0.5
  AND (
        current_high_confidence_v2 = true
        OR identity_reference_match = true
        OR strict_reference_match = true
        OR visual_label = 'olive_like'
      );


CREATE OR REPLACE VIEW olive_candidate_pool_v2_review_priority AS
SELECT
    *,

    CASE
        WHEN spatial_validation_zone = 'north_calabria'
         AND current_high_confidence_v2 = false
         AND area_ha_raw >= 0.5
            THEN 100

        WHEN current_high_confidence_v2 = false
         AND area_ha_raw >= 5.0
            THEN 90

        WHEN current_high_confidence_v2 = false
         AND area_ha_raw >= 0.5
         AND area_ha_raw < 1.0
            THEN 80

        WHEN current_high_confidence_v2 = false
         AND n_points > 250
            THEN 70

        WHEN visual_label = 'uncertain'
            THEN 60

        ELSE 10
    END AS review_priority_score,

    CASE
        WHEN spatial_validation_zone = 'north_calabria'
         AND current_high_confidence_v2 = false
            THEN 'Nord Calabria - nuova area candidate non high-confidence'

        WHEN current_high_confidence_v2 = false
         AND area_ha_raw >= 5.0
            THEN 'Poligono grande aggiunto da candidate pool'

        WHEN current_high_confidence_v2 = false
         AND area_ha_raw >= 0.5
         AND area_ha_raw < 1.0
            THEN 'Area piccola ma sopra soglia minima'

        WHEN current_high_confidence_v2 = false
         AND n_points > 250
            THEN 'Geometria complessa aggiunta da candidate pool'

        WHEN visual_label = 'uncertain'
            THEN 'Etichetta visuale incerta da ricontrollare'

        ELSE 'Priorità ordinaria'
    END AS review_priority_reason

FROM olive_candidate_pool_v2_screening;


CREATE OR REPLACE VIEW olive_candidate_pool_v2_operational_summary AS
SELECT
    spatial_validation_zone,

    COUNT(*) AS n_screening_pool,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_current_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_added_candidates,

    COUNT(*) FILTER (WHERE area_ha_raw >= 0.5 AND area_ha_raw < 1.0) AS n_0_5_1_ha,
    COUNT(*) FILTER (WHERE area_ha_raw >= 1.0 AND area_ha_raw < 2.0) AS n_1_2_ha,
    COUNT(*) FILTER (WHERE area_ha_raw >= 2.0 AND area_ha_raw < 5.0) AS n_2_5_ha,
    COUNT(*) FILTER (WHERE area_ha_raw >= 5.0) AS n_ge_5_ha,

    COUNT(*) FILTER (WHERE large_polygon_flag = true) AS n_large_polygon,
    COUNT(*) FILTER (WHERE small_candidate_flag = true) AS n_small_candidate,
    COUNT(*) FILTER (WHERE complex_boundary_flag = true) AS n_complex_boundary,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference,

    ROUND(SUM(area_ha_raw)::numeric, 2) AS total_area_ha_raw

FROM olive_candidate_pool_v2_screening
GROUP BY spatial_validation_zone
ORDER BY spatial_validation_zone;