BEGIN;

DROP VIEW IF EXISTS regional_feature_matrix_training_v2_combined;
DROP VIEW IF EXISTS regional_feature_matrix_training_v2_spectral;
DROP VIEW IF EXISTS regional_feature_matrix_training_v2_geometry;

CREATE VIEW regional_feature_matrix_training_v2_geometry AS
SELECT
    sample_id,
    area_id,
    source_geometry_id,
    spatial_validation_zone,

    binary_visual_label_v2 AS target_visual_v2,

    area_ha_raw,
    perimeter_m_raw,
    compactness_raw,
    n_points,
    n_parts,
    approx_centroid_lat,

    large_polygon_flag,
    small_candidate_flag,
    complex_boundary_flag,

    feature_matrix_version

FROM regional_feature_matrix_visual_v2

WHERE binary_visual_label_v2 IN (0, 1)
  AND geom_is_valid IS TRUE
  AND geom_is_empty IS FALSE;


CREATE VIEW regional_feature_matrix_training_v2_spectral AS
SELECT
    sample_id,
    area_id,
    source_geometry_id,
    spatial_validation_zone,

    binary_visual_label_v2 AS target_visual_v2,

    n_observations,

    ndvi_median,
    ndvi_p25,
    ndvi_p75,
    ndvi_stddev,

    evi_median,
    evi_p25,
    evi_p75,
    evi_stddev,

    ndmi_median,
    ndmi_p25,
    ndmi_p75,
    ndmi_stddev,

    bsi_median,
    bsi_p25,
    bsi_p75,
    bsi_stddev,

    feature_matrix_version

FROM regional_feature_matrix_visual_v2

WHERE binary_visual_label_v2 IN (0, 1)
  AND n_observations > 0
  AND ndvi_median IS NOT NULL
  AND evi_median IS NOT NULL
  AND ndmi_median IS NOT NULL
  AND bsi_median IS NOT NULL;


CREATE VIEW regional_feature_matrix_training_v2_combined AS
SELECT
    sample_id,
    area_id,
    source_geometry_id,
    spatial_validation_zone,

    binary_visual_label_v2 AS target_visual_v2,

    area_ha_raw,
    perimeter_m_raw,
    compactness_raw,
    n_points,
    n_parts,
    approx_centroid_lat,

    large_polygon_flag,
    small_candidate_flag,
    complex_boundary_flag,

    n_observations,

    ndvi_median,
    ndvi_p25,
    ndvi_p75,
    ndvi_stddev,

    evi_median,
    evi_p25,
    evi_p75,
    evi_stddev,

    ndmi_median,
    ndmi_p25,
    ndmi_p75,
    ndmi_stddev,

    bsi_median,
    bsi_p25,
    bsi_p75,
    bsi_stddev,

    feature_matrix_version

FROM regional_feature_matrix_visual_v2

WHERE binary_visual_label_v2 IN (0, 1)
  AND geom_is_valid IS TRUE
  AND geom_is_empty IS FALSE
  AND n_observations > 0
  AND ndvi_median IS NOT NULL
  AND evi_median IS NOT NULL
  AND ndmi_median IS NOT NULL
  AND bsi_median IS NOT NULL;


COMMENT ON VIEW regional_feature_matrix_training_v2_geometry IS
'Training v2 geometry-only sui 406 campioni visuali. Zona conservata esclusivamente per validazione spaziale.';

COMMENT ON VIEW regional_feature_matrix_training_v2_spectral IS
'Training v2 spectral-only. Usa metriche spettrali grezze; spectral_flag e usable_for_baseline_spectral sono esclusi per evitare circolarità.';

COMMENT ON VIEW regional_feature_matrix_training_v2_combined IS
'Training v2 geometry plus spectral sugli stessi campioni, per confronto controllato con le configurazioni geometry-only e spectral-only.';

COMMIT;
