-- ============================================================
-- 017_geometric_sensitivity_cache_schema.sql
-- IntelCrop Calabria
--
-- Scopo:
-- creare una tabella cache leggera per analisi di sensibilità
-- del filtro geometrico senza stressare PostGIS.
--
-- Le metriche geometriche raw vengono calcolate da Python a batch.
-- Questo file crea solo schema, indici e viste leggere.
-- ============================================================


DROP VIEW IF EXISTS olive_geometry_cache_candidate_pool_scenarios_v1 CASCADE;
DROP VIEW IF EXISTS olive_geometry_cache_by_complexity_v1 CASCADE;
DROP VIEW IF EXISTS olive_geometry_cache_by_zone_v1 CASCADE;
DROP VIEW IF EXISTS olive_geometry_cache_overview_v1 CASCADE;

DROP TABLE IF EXISTS olive_geometry_sensitivity_cache_v1 CASCADE;


CREATE TABLE olive_geometry_sensitivity_cache_v1 (
    area_id text PRIMARY KEY,
    source_geometry_id text,

    subtype_id text NOT NULL,
    source_layer_version text NOT NULL,

    geom_is_valid boolean,
    geom_is_empty boolean,

    n_points integer,
    n_parts integer,

    approx_centroid_lat double precision,
    spatial_validation_zone text,

    n_points_bin text,
    n_parts_bin text,

    current_high_confidence_v2 boolean NOT NULL DEFAULT false,

    high_confidence_area_ha double precision,
    high_confidence_compactness double precision,
    high_confidence_qc_score double precision,
    high_confidence_qc_class text,

    identity_reference_match boolean NOT NULL DEFAULT false,
    strict_reference_match boolean NOT NULL DEFAULT false,

    visual_label text,
    eval_class text,
    eval_class_strict text,

    binary_visual_label integer,

    created_at timestamptz NOT NULL DEFAULT now()
);


CREATE INDEX idx_olive_geometry_sensitivity_cache_zone
ON olive_geometry_sensitivity_cache_v1(spatial_validation_zone);

CREATE INDEX idx_olive_geometry_sensitivity_cache_high_conf
ON olive_geometry_sensitivity_cache_v1(current_high_confidence_v2);

CREATE INDEX idx_olive_geometry_sensitivity_cache_points_bin
ON olive_geometry_sensitivity_cache_v1(n_points_bin);

CREATE INDEX idx_olive_geometry_sensitivity_cache_parts_bin
ON olive_geometry_sensitivity_cache_v1(n_parts_bin);

CREATE INDEX idx_olive_geometry_sensitivity_cache_visual_label
ON olive_geometry_sensitivity_cache_v1(visual_label);


CREATE OR REPLACE VIEW olive_geometry_cache_overview_v1 AS
SELECT
    COUNT(*) AS n_raw_olive_pure,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_current_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_excluded_from_high_confidence,

    ROUND(
        (
            COUNT(*) FILTER (WHERE current_high_confidence_v2 = true)::numeric
            / NULLIF(COUNT(*)::numeric, 0)
        ) * 100,
        2
    ) AS retained_count_pct,

    ROUND(
        SUM(high_confidence_area_ha) FILTER (WHERE current_high_confidence_v2 = true)::numeric,
        2
    ) AS current_high_confidence_area_ha,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference

FROM olive_geometry_sensitivity_cache_v1;


CREATE OR REPLACE VIEW olive_geometry_cache_by_zone_v1 AS
SELECT
    spatial_validation_zone,

    COUNT(*) AS n_raw,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_current_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_excluded,

    ROUND(
        (
            COUNT(*) FILTER (WHERE current_high_confidence_v2 = true)::numeric
            / NULLIF(COUNT(*)::numeric, 0)
        ) * 100,
        2
    ) AS retained_count_pct,

    ROUND(
        SUM(high_confidence_area_ha) FILTER (WHERE current_high_confidence_v2 = true)::numeric,
        2
    ) AS current_high_confidence_area_ha,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference

FROM olive_geometry_sensitivity_cache_v1
GROUP BY spatial_validation_zone
ORDER BY spatial_validation_zone;


CREATE OR REPLACE VIEW olive_geometry_cache_by_complexity_v1 AS
SELECT
    spatial_validation_zone,
    n_points_bin,
    n_parts_bin,

    COUNT(*) AS n_raw,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_current_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_excluded,

    ROUND(
        (
            COUNT(*) FILTER (WHERE current_high_confidence_v2 = true)::numeric
            / NULLIF(COUNT(*)::numeric, 0)
        ) * 100,
        2
    ) AS retained_count_pct,

    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference

FROM olive_geometry_sensitivity_cache_v1
GROUP BY spatial_validation_zone, n_points_bin, n_parts_bin
ORDER BY spatial_validation_zone, n_points_bin, n_parts_bin;


CREATE OR REPLACE VIEW olive_geometry_cache_candidate_pool_scenarios_v1 AS
WITH scenarios AS (
    SELECT
        'current_high_confidence_v2' AS scenario_code,
        'Filtro geometrico attuale high-confidence v2' AS scenario_label,
        *
    FROM olive_geometry_sensitivity_cache_v1
    WHERE current_high_confidence_v2 = true

    UNION ALL

    SELECT
        'raw_all_olive_pure' AS scenario_code,
        'Tutte le geometrie olive_pure raw' AS scenario_label,
        *
    FROM olive_geometry_sensitivity_cache_v1

    UNION ALL

    SELECT
        'candidate_pool_singlepart_v1' AS scenario_code,
        'Candidate pool singlepart' AS scenario_label,
        *
    FROM olive_geometry_sensitivity_cache_v1
    WHERE n_parts = 1

    UNION ALL

    SELECT
        'candidate_pool_multipart_le_3_v1' AS scenario_code,
        'Candidate pool multipart <= 3' AS scenario_label,
        *
    FROM olive_geometry_sensitivity_cache_v1
    WHERE n_parts <= 3

    UNION ALL

    SELECT
        'candidate_pool_multipart_le_10_v1' AS scenario_code,
        'Candidate pool multipart <= 10' AS scenario_label,
        *
    FROM olive_geometry_sensitivity_cache_v1
    WHERE n_parts <= 10
)

SELECT
    scenario_code,
    scenario_label,
    spatial_validation_zone,

    COUNT(*) AS n_areas,

    COUNT(*) FILTER (WHERE current_high_confidence_v2 = true) AS n_already_high_confidence,
    COUNT(*) FILTER (WHERE current_high_confidence_v2 = false) AS n_added_vs_current,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference

FROM scenarios
GROUP BY scenario_code, scenario_label, spatial_validation_zone
ORDER BY scenario_code, spatial_validation_zone;