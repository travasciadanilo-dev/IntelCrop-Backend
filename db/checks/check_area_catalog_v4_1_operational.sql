\set ON_ERROR_STOP on
\pset pager off

\echo ''
\echo 'Area catalog v4.1 operational integrity'
\echo '---------------------------------------'

SELECT *
FROM area_catalog_v4_1_operational_summary;

SELECT
    reliability_class,
    COUNT(*) AS area_n,
    ROUND(AVG(reliability_score)::numeric, 4)
        AS mean_reliability_score,
    ROUND(AVG(ndvi_median)::numeric, 4)
        AS mean_ndvi,
    ROUND(AVG(evi_median)::numeric, 4)
        AS mean_evi,
    ROUND(AVG(ndmi_median)::numeric, 4)
        AS mean_ndmi,
    ROUND(AVG(bsi_median)::numeric, 4)
        AS mean_bsi
FROM area_catalog_v4_1_operational
GROUP BY reliability_class
ORDER BY CASE reliability_class
    WHEN 'low' THEN 1
    WHEN 'compatible' THEN 2
    WHEN 'very_high' THEN 3
    ELSE 99
END;

DO $$
DECLARE
    v_area_n bigint;
    v_distinct_area_n bigint;
    v_distinct_geometry_n bigint;
    v_invalid_link_n bigint;
    v_incomplete_spectral_n bigint;
    v_catalog_version text;
    v_catalog_status text;
    v_model_version text;
    v_feature_matrix_version text;
BEGIN
    SELECT
        area_n,
        distinct_area_n,
        distinct_source_geometry_n,
        invalid_feature_link_n,
        incomplete_spectral_feature_n,
        catalog_version,
        catalog_status,
        reliability_model_version,
        feature_matrix_version
    INTO
        v_area_n,
        v_distinct_area_n,
        v_distinct_geometry_n,
        v_invalid_link_n,
        v_incomplete_spectral_n,
        v_catalog_version,
        v_catalog_status,
        v_model_version,
        v_feature_matrix_version
    FROM area_catalog_v4_1_operational_summary;

    IF v_area_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected operational area count: %',
            v_area_n;
    END IF;

    IF v_distinct_area_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected distinct area count: %',
            v_distinct_area_n;
    END IF;

    IF v_distinct_geometry_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected distinct geometry count: %',
            v_distinct_geometry_n;
    END IF;

    IF v_invalid_link_n <> 0 THEN
        RAISE EXCEPTION
            'Invalid feature links: %',
            v_invalid_link_n;
    END IF;

    IF v_incomplete_spectral_n <> 0 THEN
        RAISE EXCEPTION
            'Incomplete spectral feature rows: %',
            v_incomplete_spectral_n;
    END IF;

    IF v_catalog_version
        <> 'area_catalog_v4_1_diagnostic'
    THEN
        RAISE EXCEPTION
            'Unexpected catalog version: %',
            v_catalog_version;
    END IF;

    IF v_catalog_status
        <> 'validated_not_promoted'
    THEN
        RAISE EXCEPTION
            'Unexpected catalog status: %',
            v_catalog_status;
    END IF;

    IF v_model_version
        <> 'regional_reliability_score_exp_v4_combined_ridge'
    THEN
        RAISE EXCEPTION
            'Unexpected model version: %',
            v_model_version;
    END IF;

    IF v_feature_matrix_version
        <> 'area_feature_matrix_regional_v1'
    THEN
        RAISE EXCEPTION
            'Unexpected feature matrix version: %',
            v_feature_matrix_version;
    END IF;
END
$$;

\echo 'AREA_CATALOG_V4_1_OPERATIONAL_OK'
