-- ============================================================
-- 013_area_feature_matrix.sql
-- IntelCrop Calabria
--
-- Scopo:
-- Costruire la matrice regionale delle feature per tutte le aree
-- geometricamente high-confidence del catalogo operativo.
--
-- Questa view NON valida tutte le aree.
-- Serve come base per calibrare un regional_reliability_score.
--
-- Input principali:
-- - landcover_olive_pure_high_confidence_v2
-- - landcover_subtypes
-- - landcover_olive_visual_training_eval_v1
-- - landcover_olive_visual_training_eval_strict_v1
-- - landcover_olive_pure_baseline_v1
-- - landcover_olive_pure_baseline_strict_seed_v1
--
-- Nota metodologica:
-- strict_reference_match = true identifica un seed conservativo.
-- strict_reference_match = false NON significa area non olivicola.
-- ============================================================


DROP VIEW IF EXISTS area_feature_matrix_v1;


CREATE OR REPLACE VIEW area_feature_matrix_v1 AS
WITH base AS (
    SELECT
        h.id::text AS area_id,
        h.id AS source_geometry_id,

        'calabria'::text AS region_code,
        'Calabria'::text AS region_label,

        h.subtype_id AS technical_subtype_id,
        s.label_it AS technical_subtype_label,

        CASE
            WHEN h.subtype_id IN ('olive_pure', 'olive_citrus', 'olive_vine')
                THEN 'permanent_tree_crop'
            ELSE 'agricultural_area'
        END AS area_type,

        CASE
            WHEN h.subtype_id IN ('olive_pure', 'olive_citrus', 'olive_vine')
                THEN 'Coltura arborea permanente'
            ELSE 'Area agricola'
        END AS area_type_label,

        h.source_layer_version,

        h.qc_version AS geometric_qc_version,
        h.qc_class AS geometric_qc_class,
        h.usable_for_baseline AS geometric_usable_for_baseline,

        h.area_ha,
        h.compactness,
        h.n_points,
        h.n_parts,
        h.qc_score,

        ST_PointOnSurface(h.geom) AS label_point_geom,
        ST_X(ST_PointOnSurface(h.geom)) AS centroid_lon,
        ST_Y(ST_PointOnSurface(h.geom)) AS centroid_lat,

        ST_XMin(ST_Envelope(h.geom)) AS bbox_min_lon,
        ST_YMin(ST_Envelope(h.geom)) AS bbox_min_lat,
        ST_XMax(ST_Envelope(h.geom)) AS bbox_max_lon,
        ST_YMax(ST_Envelope(h.geom)) AS bbox_max_lat,

        h.geom

    FROM landcover_olive_pure_high_confidence_v2 h
    JOIN landcover_subtypes s
      ON s.id = h.subtype_id
    WHERE h.source_layer_version = 'cut_calabria_v1'
      AND h.qc_version = 'olive_pure_geom_qc_v2'
      AND h.qc_class = 'high_confidence'
),

visual_eval AS (
    SELECT
        id::text AS area_id,
        visual_qc_version,
        visual_label,
        eval_class
    FROM landcover_olive_visual_training_eval_v1
),

strict_eval AS (
    SELECT
        id::text AS area_id,
        eval_class_strict
    FROM landcover_olive_visual_training_eval_strict_v1
),

identity_reference AS (
    SELECT
        id::text AS area_id,

        visual_qc_version,
        visual_label,

        urban_qc_version,
        artificial_flag,

        spectral_qc_version,
        spectral_flag,
        n_observations,

        ndvi_median,
        evi_median,
        ndmi_median,
        bsi_median

    FROM landcover_olive_pure_baseline_v1
),

strict_reference AS (
    SELECT
        id::text AS area_id,

        visual_qc_version,
        visual_label,

        urban_qc_version,
        artificial_flag,

        spectral_qc_version,
        spectral_flag,
        n_observations,

        ndvi_median,
        evi_median,
        ndmi_median,
        bsi_median

    FROM landcover_olive_pure_baseline_strict_seed_v1
),

joined AS (
    SELECT
        b.area_id,
        b.source_geometry_id,

        b.region_code,
        b.region_label,

        b.technical_subtype_id,
        b.technical_subtype_label,
        b.area_type,
        b.area_type_label,

        b.source_layer_version,

        b.geometric_qc_version,
        b.geometric_qc_class,
        b.geometric_usable_for_baseline,

        b.area_ha,
        b.compactness,
        b.n_points,
        b.n_parts,
        b.qc_score,

        ve.visual_qc_version AS visual_eval_version,
        ve.visual_label AS visual_eval_label,
        ve.eval_class AS visual_eval_class,

        se.eval_class_strict AS strict_eval_class,

        ir.area_id IS NOT NULL AS identity_reference_match,
        sr.area_id IS NOT NULL AS strict_reference_match,

        COALESCE(sr.visual_qc_version, ir.visual_qc_version, ve.visual_qc_version) AS visual_qc_version,
        COALESCE(sr.visual_label, ir.visual_label, ve.visual_label) AS visual_label,

        COALESCE(sr.urban_qc_version, ir.urban_qc_version) AS urban_qc_version,
        COALESCE(sr.artificial_flag, ir.artificial_flag) AS artificial_flag,

        COALESCE(sr.spectral_qc_version, ir.spectral_qc_version) AS spectral_qc_version,
        COALESCE(sr.spectral_flag, ir.spectral_flag) AS spectral_flag,
        COALESCE(sr.n_observations, ir.n_observations) AS n_observations,

        COALESCE(sr.ndvi_median, ir.ndvi_median) AS ndvi_median,
        COALESCE(sr.evi_median, ir.evi_median) AS evi_median,
        COALESCE(sr.ndmi_median, ir.ndmi_median) AS ndmi_median,
        COALESCE(sr.bsi_median, ir.bsi_median) AS bsi_median,

        CASE
            WHEN sr.area_id IS NOT NULL THEN 'strict_reference'
            WHEN ir.area_id IS NOT NULL THEN 'identity_reference'
            WHEN ve.visual_label IS NOT NULL THEN 'visual_reviewed'
            ELSE 'unreviewed_candidate'
        END AS training_reference_status,

        CASE
            WHEN ve.visual_label = 'olive_like' THEN 1
            WHEN ve.visual_label = 'not_olive_like' THEN 0
            ELSE NULL
        END AS binary_visual_label,

        CASE
            WHEN ve.visual_label = 'uncertain' THEN true
            ELSE false
        END AS is_uncertain_visual_label,

        CASE
            WHEN sr.area_id IS NOT NULL THEN true
            ELSE false
        END AS is_high_precision_anchor,

        CASE
            WHEN ir.area_id IS NOT NULL THEN true
            ELSE false
        END AS is_identity_reference_seed,

        CASE
            WHEN COALESCE(sr.artificial_flag, ir.artificial_flag) IN ('none', 'low') THEN true
            WHEN COALESCE(sr.artificial_flag, ir.artificial_flag) IN ('medium', 'high') THEN false
            ELSE NULL
        END AS context_component_pass,

        CASE
            WHEN COALESCE(sr.spectral_flag, ir.spectral_flag) IN ('strong', 'moderate') THEN true
            WHEN COALESCE(sr.spectral_flag, ir.spectral_flag) = 'weak' THEN false
            ELSE NULL
        END AS spectral_component_pass,

        CASE
            WHEN COALESCE(sr.n_observations, ir.n_observations) IS NULL THEN NULL
            WHEN COALESCE(sr.n_observations, ir.n_observations) >= 120 THEN 'high'
            WHEN COALESCE(sr.n_observations, ir.n_observations) >= 80 THEN 'medium'
            ELSE 'low'
        END AS data_availability_class,

        CASE
            WHEN b.qc_score >= 95 THEN 'excellent'
            WHEN b.qc_score >= 90 THEN 'good'
            ELSE 'minimum'
        END AS geometry_component_class,

        CASE
            WHEN b.centroid_lat >= 39.5 THEN 'north_calabria'
            WHEN b.centroid_lat >= 38.6 THEN 'central_calabria'
            ELSE 'south_calabria'
        END AS spatial_validation_zone,

        b.centroid_lon,
        b.centroid_lat,

        b.bbox_min_lon,
        b.bbox_min_lat,
        b.bbox_max_lon,
        b.bbox_max_lat,

        b.label_point_geom,
        b.geom

    FROM base b
    LEFT JOIN visual_eval ve
      ON ve.area_id = b.area_id
    LEFT JOIN strict_eval se
      ON se.area_id = b.area_id
    LEFT JOIN identity_reference ir
      ON ir.area_id = b.area_id
    LEFT JOIN strict_reference sr
      ON sr.area_id = b.area_id
)

SELECT
    *
FROM joined;


-- ============================================================
-- Metadata view
-- ============================================================

DROP VIEW IF EXISTS area_feature_matrix_summary_v1;


CREATE OR REPLACE VIEW area_feature_matrix_summary_v1 AS
SELECT
    COUNT(*) AS n_areas,
    ROUND(SUM(area_ha)::numeric, 2) AS total_area_ha,

    COUNT(*) FILTER (WHERE visual_label IS NOT NULL) AS n_visual_reviewed,
    COUNT(*) FILTER (WHERE visual_label = 'olive_like') AS n_visual_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'not_olive_like') AS n_visual_not_olive_like,
    COUNT(*) FILTER (WHERE visual_label = 'uncertain') AS n_visual_uncertain,

    COUNT(*) FILTER (WHERE identity_reference_match = true) AS n_identity_reference_seed,
    COUNT(*) FILTER (WHERE strict_reference_match = true) AS n_strict_reference_seed,

    COUNT(*) FILTER (WHERE artificial_flag IN ('none', 'low')) AS n_context_pass,
    COUNT(*) FILTER (WHERE artificial_flag IN ('medium', 'high')) AS n_context_reject,
    COUNT(*) FILTER (WHERE artificial_flag IS NULL) AS n_context_missing,

    COUNT(*) FILTER (WHERE spectral_flag IN ('strong', 'moderate')) AS n_spectral_pass,
    COUNT(*) FILTER (WHERE spectral_flag = 'weak') AS n_spectral_reject,
    COUNT(*) FILTER (WHERE spectral_flag IS NULL) AS n_spectral_missing,

    COUNT(*) FILTER (WHERE spatial_validation_zone = 'north_calabria') AS n_north_calabria,
    COUNT(*) FILTER (WHERE spatial_validation_zone = 'central_calabria') AS n_central_calabria,
    COUNT(*) FILTER (WHERE spatial_validation_zone = 'south_calabria') AS n_south_calabria
FROM area_feature_matrix_v1;


-- ============================================================
-- Training/calibration subset
-- ============================================================

DROP VIEW IF EXISTS area_feature_matrix_training_v1;


CREATE OR REPLACE VIEW area_feature_matrix_training_v1 AS
SELECT
    *
FROM area_feature_matrix_v1
WHERE visual_label IN ('olive_like', 'not_olive_like')
  AND binary_visual_label IS NOT NULL;


-- ============================================================
-- Uncertain subset: da valutare separatamente
-- ============================================================

DROP VIEW IF EXISTS area_feature_matrix_uncertain_v1;


CREATE OR REPLACE VIEW area_feature_matrix_uncertain_v1 AS
SELECT
    *
FROM area_feature_matrix_v1
WHERE visual_label = 'uncertain';


-- ============================================================
-- Strict anchor subset
-- ============================================================

DROP VIEW IF EXISTS area_feature_matrix_strict_anchor_v1;


CREATE OR REPLACE VIEW area_feature_matrix_strict_anchor_v1 AS
SELECT
    *
FROM area_feature_matrix_v1
WHERE strict_reference_match = true;