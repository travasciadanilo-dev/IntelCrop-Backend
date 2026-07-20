BEGIN;

CREATE OR REPLACE VIEW area_catalog_v4_1_operational AS
SELECT
    c.*,

    f.subtype_id AS feature_subtype_id,
    f.source_layer_version,

    f.geom_is_valid,
    f.geom_is_empty,

    f.area_ha_raw,
    f.perimeter_m_raw,
    f.compactness_raw,
    f.approx_centroid_lat,

    f.spectral_qc_row_id,
    f.spectral_qc_version,
    f.spectral_source_view,

    f.n_observations,

    f.ndvi_median,
    f.ndvi_p25,
    f.ndvi_p75,
    f.ndvi_stddev,

    f.evi_median,
    f.evi_p25,
    f.evi_p75,
    f.evi_stddev,

    f.ndmi_median,
    f.ndmi_p25,
    f.ndmi_p75,
    f.ndmi_stddev,

    f.bsi_median,
    f.bsi_p25,
    f.bsi_p75,
    f.bsi_stddev,

    f.spectral_flag,
    f.usable_for_baseline_spectral,
    f.exclusion_reason,
    f.spectral_computed_at,
    f.spectral_status,
    f.has_complete_spectral_features,

    f.source_pool_version,
    f.feature_matrix_version,

    (
        c.source_geometry_id = f.source_geometry_id
    ) AS feature_source_geometry_match,

    (
        c.area_id = f.area_id
        AND c.source_geometry_id = f.source_geometry_id
    ) AS feature_link_valid

FROM area_catalog_v4_1_diagnostic c
JOIN area_feature_matrix_regional_v1 f
    ON f.area_id = c.area_id
   AND f.source_geometry_id = c.source_geometry_id;


CREATE OR REPLACE VIEW area_catalog_v4_1_operational_summary AS
SELECT
    COUNT(*) AS area_n,
    COUNT(DISTINCT area_id) AS distinct_area_n,
    COUNT(DISTINCT source_geometry_id)
        AS distinct_source_geometry_n,

    COUNT(*) FILTER (
        WHERE feature_link_valid
    ) AS valid_feature_link_n,

    COUNT(*) FILTER (
        WHERE NOT feature_link_valid
    ) AS invalid_feature_link_n,

    COUNT(*) FILTER (
        WHERE has_complete_spectral_features
    ) AS complete_spectral_feature_n,

    COUNT(*) FILTER (
        WHERE NOT has_complete_spectral_features
    ) AS incomplete_spectral_feature_n,

    COUNT(*) FILTER (
        WHERE usable_for_baseline_spectral
    ) AS spectral_usable_n,

    COUNT(*) FILTER (
        WHERE NOT usable_for_baseline_spectral
    ) AS spectral_not_usable_n,

    COUNT(*) FILTER (
        WHERE reliability_class = 'low'
    ) AS low_n,

    COUNT(*) FILTER (
        WHERE reliability_class = 'compatible'
    ) AS compatible_n,

    COUNT(*) FILTER (
        WHERE reliability_class = 'very_high'
    ) AS very_high_n,

    MIN(area_ha) AS min_area_ha,
    AVG(area_ha) AS mean_area_ha,
    MAX(area_ha) AS max_area_ha,

    MIN(catalog_version) AS catalog_version,
    MIN(catalog_status) AS catalog_status,
    MIN(reliability_model_version)
        AS reliability_model_version,
    MIN(feature_matrix_version)
        AS feature_matrix_version,
    MIN(spectral_qc_version)
        AS spectral_qc_version

FROM area_catalog_v4_1_operational;


COMMENT ON VIEW area_catalog_v4_1_operational IS
'Validated-not-promoted v4.1 operational catalog joining regional reliability classification with the versioned regional spectral feature matrix.';

COMMENT ON VIEW area_catalog_v4_1_operational_summary IS
'Integrity and coverage summary for area_catalog_v4_1_operational.';

COMMIT;
