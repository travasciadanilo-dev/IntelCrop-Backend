\set ON_ERROR_STOP on

BEGIN;

CREATE OR REPLACE VIEW area_feature_matrix_regional_v1 AS
SELECT
    p.area_id,
    p.source_geometry_id::uuid AS source_geometry_id,
    p.subtype_id,
    p.source_layer_version,
    p.spatial_validation_zone,

    p.geom_is_valid,
    p.geom_is_empty,

    p.area_ha_raw,
    p.perimeter_m_raw,
    p.compactness_raw,
    p.n_points,
    p.n_parts,
    p.approx_centroid_lat,
    p.area_bin_raw,
    p.n_points_bin,
    p.n_parts_bin,

    p.current_high_confidence_v2,
    p.identity_reference_match,
    p.strict_reference_match,

    qc.id AS spectral_qc_row_id,
    qc.spectral_qc_version,
    qc.source_view AS spectral_source_view,
    qc.n_observations,

    qc.ndvi_median,
    qc.ndvi_p25,
    qc.ndvi_p75,
    qc.ndvi_stddev,

    qc.evi_median,
    qc.evi_p25,
    qc.evi_p75,
    qc.evi_stddev,

    qc.ndmi_median,
    qc.ndmi_p25,
    qc.ndmi_p75,
    qc.ndmi_stddev,

    qc.bsi_median,
    qc.bsi_p25,
    qc.bsi_p75,
    qc.bsi_stddev,

    qc.spectral_flag,
    qc.usable_for_baseline_spectral,
    qc.exclusion_reason,
    qc.computed_at AS spectral_computed_at,

    CASE
        WHEN qc.id IS NULL THEN 'missing'
        WHEN qc.usable_for_baseline_spectral IS TRUE
            THEN 'usable'
        ELSE 'not_usable'
    END AS spectral_status,

    (
        qc.id IS NOT NULL
        AND qc.n_observations > 0
        AND qc.ndvi_median IS NOT NULL
        AND qc.evi_median IS NOT NULL
        AND qc.ndmi_median IS NOT NULL
        AND qc.bsi_median IS NOT NULL
    ) AS has_complete_spectral_features,

    'olive_candidate_pool_v2'::text
        AS source_pool_version,

    'area_feature_matrix_regional_v1'::text
        AS feature_matrix_version

FROM olive_candidate_pool_v2 p

LEFT JOIN landcover_subtype_spectral_qc qc
    ON qc.source_geometry_id::text = p.source_geometry_id
   AND qc.spectral_qc_version = 'olive_spectral_qc_v1'

WHERE
    p.candidate_pool_v2 IS TRUE
    AND p.geom_is_valid IS TRUE
    AND p.geom_is_empty IS FALSE;


CREATE OR REPLACE VIEW area_feature_matrix_regional_v1_summary AS
SELECT
    COUNT(*) AS area_n,

    COUNT(DISTINCT area_id)
        AS distinct_area_n,

    COUNT(DISTINCT source_geometry_id)
        AS distinct_geometry_n,

    COUNT(*) FILTER (
        WHERE spectral_qc_row_id IS NOT NULL
    ) AS spectral_matched_n,

    COUNT(*) FILTER (
        WHERE spectral_qc_row_id IS NULL
    ) AS spectral_missing_n,

    COUNT(*) FILTER (
        WHERE spectral_status = 'usable'
    ) AS spectral_usable_n,

    COUNT(*) FILTER (
        WHERE spectral_status = 'not_usable'
    ) AS spectral_not_usable_n,

    COUNT(*) FILTER (
        WHERE has_complete_spectral_features IS TRUE
    ) AS complete_spectral_feature_n,

    COUNT(*) FILTER (
        WHERE has_complete_spectral_features IS FALSE
    ) AS incomplete_spectral_feature_n,

    COUNT(*) FILTER (
        WHERE spatial_validation_zone = 'north_calabria'
    ) AS north_calabria_n,

    COUNT(*) FILTER (
        WHERE spatial_validation_zone = 'central_calabria'
    ) AS central_calabria_n,

    COUNT(*) FILTER (
        WHERE spatial_validation_zone = 'south_calabria'
    ) AS south_calabria_n,

    MIN(area_ha_raw) AS min_area_ha,
    AVG(area_ha_raw) AS mean_area_ha,
    MAX(area_ha_raw) AS max_area_ha,

    'olive_candidate_pool_v2'::text
        AS source_pool_version,

    'olive_spectral_qc_v1'::text
        AS spectral_qc_version,

    'area_feature_matrix_regional_v1'::text
        AS feature_matrix_version

FROM area_feature_matrix_regional_v1;


COMMENT ON VIEW area_feature_matrix_regional_v1 IS
'Regional operational feature matrix for the complete olive_candidate_pool_v2 catalog. One row per area, including geometric and olive_spectral_qc_v1 features.';

COMMENT ON VIEW area_feature_matrix_regional_v1_summary IS
'Coverage and integrity summary for area_feature_matrix_regional_v1.';

COMMIT;
