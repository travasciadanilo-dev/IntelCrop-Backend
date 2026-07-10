BEGIN;

DROP VIEW IF EXISTS regional_feature_matrix_training_v2_combined;
DROP VIEW IF EXISTS regional_feature_matrix_training_v2_spectral;
DROP VIEW IF EXISTS regional_feature_matrix_training_v2_geometry;
DROP VIEW IF EXISTS regional_feature_matrix_training_v2_core;
DROP VIEW IF EXISTS regional_feature_matrix_spectral_diagnostic_v2;
DROP VIEW IF EXISTS regional_feature_matrix_visual_v2_summary;
DROP VIEW IF EXISTS regional_feature_matrix_visual_v2;

CREATE VIEW regional_feature_matrix_visual_v2 AS
SELECT
    -- Identità stabile
    t.sample_id,
    t.sample_version,
    t.source_pool_version,
    t.area_id,
    t.source_geometry_id::uuid AS source_geometry_id,

    -- Stratificazione territoriale
    t.spatial_validation_zone,
    t.candidate_origin,
    t.sample_stratum,
    t.sample_stratum_description,

    -- Target visuale v2
    t.visual_label_v2,
    t.binary_visual_label_v2,
    t.plantation_pattern_v2,
    t.binary_plantation_pattern_v2,
    t.is_complete,
    t.is_training_eligible,

    -- Variabili geometriche continue
    p.area_ha_raw,
    p.perimeter_m_raw,
    p.compactness_raw,
    p.n_points,
    p.n_parts,
    p.approx_centroid_lat,

    -- Variabili geometriche categoriali
    p.area_bin_raw,
    p.n_points_bin,
    p.n_parts_bin,

    -- Indicatori geometrici elementari
    p.geom_is_valid,
    p.geom_is_empty,
    p.large_polygon_flag,
    p.small_candidate_flag,
    p.complex_boundary_flag,

    -- Indicatori di origine e controllo
    p.current_high_confidence_v2,
    p.identity_reference_match,
    p.strict_reference_match,

    -- Feature spettrali conservate solo come diagnostica
    s.spectral_qc_version,
    s.n_observations,
    s.ndvi_median,
    s.evi_median,
    s.ndmi_median,
    s.bsi_median,
    s.ndvi_p25,
    s.ndvi_p75,
    s.evi_p25,
    s.evi_p75,
    s.ndmi_p25,
    s.ndmi_p75,
    s.bsi_p25,
    s.bsi_p75,
    s.ndvi_stddev,
    s.evi_stddev,
    s.ndmi_stddev,
    s.bsi_stddev,
    s.spectral_flag,
    s.usable_for_baseline_spectral,

    -- Indicatori espliciti di disponibilità
    (s.source_geometry_id IS NOT NULL)
        AS has_spectral_data,

    COALESCE(
        s.usable_for_baseline_spectral,
        FALSE
    ) AS spectral_usable,

    CASE
        WHEN s.source_geometry_id IS NULL
            THEN 'missing'
        WHEN s.usable_for_baseline_spectral IS TRUE
            THEN 'usable'
        ELSE 'not_usable'
    END AS spectral_availability_class,

    -- Audit
    t.source_file AS visual_source_file,
    t.imported_at AS visual_imported_at,
    s.computed_at AS spectral_computed_at,

    -- Versione della matrice
    'regional_feature_matrix_visual_v2'
        ::text AS feature_matrix_version

FROM olive_visual_review_sample_v2_training_v1 AS t

INNER JOIN olive_candidate_pool_v2_reliability_v3_diagnostic_v1 AS p
    ON p.area_id::text = t.area_id::text
   AND p.source_geometry_id::text = t.source_geometry_id::text

LEFT JOIN landcover_subtype_spectral_qc AS s
    ON s.source_geometry_id::text = t.source_geometry_id::text

WHERE t.is_complete IS TRUE
  AND t.is_training_eligible IS TRUE
  AND t.binary_visual_label_v2 IN (0, 1);


CREATE VIEW regional_feature_matrix_training_v2_core AS
SELECT
    sample_id,
    sample_version,
    source_pool_version,
    area_id,
    source_geometry_id,

    spatial_validation_zone,
    candidate_origin,
    sample_stratum,

    binary_visual_label_v2 AS target_visual_v2,

    area_ha_raw,
    perimeter_m_raw,
    compactness_raw,
    n_points,
    n_parts,
    approx_centroid_lat,

    area_bin_raw,
    n_points_bin,
    n_parts_bin,

    large_polygon_flag,
    small_candidate_flag,
    complex_boundary_flag,

    geom_is_valid,
    geom_is_empty,

    feature_matrix_version

FROM regional_feature_matrix_visual_v2

WHERE is_training_eligible IS TRUE
  AND binary_visual_label_v2 IN (0, 1)
  AND geom_is_valid IS TRUE
  AND geom_is_empty IS FALSE;


CREATE VIEW regional_feature_matrix_spectral_diagnostic_v2 AS
SELECT
    *

FROM regional_feature_matrix_visual_v2

WHERE spectral_usable IS TRUE;


CREATE VIEW regional_feature_matrix_visual_v2_summary AS
SELECT
    COUNT(*) AS n_training_total,

    COUNT(*) FILTER (
        WHERE binary_visual_label_v2 = 1
    ) AS n_positive,

    COUNT(*) FILTER (
        WHERE binary_visual_label_v2 = 0
    ) AS n_negative,

    COUNT(*) FILTER (
        WHERE spectral_usable IS TRUE
    ) AS n_spectral_usable,

    COUNT(*) FILTER (
        WHERE spectral_usable IS FALSE
    ) AS n_spectral_missing_or_rejected,

    ROUND(
        (
            100.0
            * COUNT(*) FILTER (
                WHERE spectral_usable IS TRUE
            )
            / NULLIF(COUNT(*), 0)
        )::numeric,
        2
    ) AS spectral_coverage_pct,

    COUNT(DISTINCT spatial_validation_zone)
        AS n_validation_zones,

    MIN(area_ha_raw) AS min_area_ha,
    AVG(area_ha_raw) AS mean_area_ha,
    MAX(area_ha_raw) AS max_area_ha,

    'regional_feature_matrix_visual_v2'
        ::text AS feature_matrix_version

FROM regional_feature_matrix_visual_v2;


COMMENT ON VIEW regional_feature_matrix_visual_v2 IS
'Feature matrix basata sul secondo visual checking. Include 406 campioni training-eligible e conserva le feature spettrali solo come diagnostica di disponibilità.';

COMMENT ON VIEW regional_feature_matrix_training_v2_core IS
'Training matrix core v2 priva di output del modello v3, score di priorità e feature spettrali incomplete.';

COMMENT ON VIEW regional_feature_matrix_spectral_diagnostic_v2 IS
'Sottoinsieme diagnostico con dati spettrali utilizzabili. Non deve essere usato come training regionale finché la copertura non è bilanciata.';

COMMIT;