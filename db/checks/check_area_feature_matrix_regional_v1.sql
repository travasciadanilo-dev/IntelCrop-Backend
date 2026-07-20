\set ON_ERROR_STOP on
\pset pager off

\echo ''
\echo 'Area feature matrix regional v1 integrity'
\echo '-----------------------------------------'

SELECT
    COUNT(*) AS row_n,
    COUNT(DISTINCT area_id) AS distinct_area_n,
    COUNT(DISTINCT source_geometry_id) AS distinct_geometry_n,
    COUNT(*) - COUNT(DISTINCT area_id) AS duplicate_area_n,
    COUNT(*) FILTER (
        WHERE spectral_qc_row_id IS NULL
    ) AS missing_spectral_link_n,
    COUNT(*) FILTER (
        WHERE has_complete_spectral_features IS FALSE
    ) AS incomplete_spectral_feature_n
FROM area_feature_matrix_regional_v1;

SELECT *
FROM area_feature_matrix_regional_v1_summary;

DO $$
DECLARE
    v_row_n bigint;
    v_distinct_area_n bigint;
    v_distinct_geometry_n bigint;
    v_missing_spectral_n bigint;
    v_incomplete_spectral_n bigint;
BEGIN
    SELECT
        COUNT(*),
        COUNT(DISTINCT area_id),
        COUNT(DISTINCT source_geometry_id),
        COUNT(*) FILTER (
            WHERE spectral_qc_row_id IS NULL
        ),
        COUNT(*) FILTER (
            WHERE has_complete_spectral_features IS FALSE
        )
    INTO
        v_row_n,
        v_distinct_area_n,
        v_distinct_geometry_n,
        v_missing_spectral_n,
        v_incomplete_spectral_n
    FROM area_feature_matrix_regional_v1;

    IF v_row_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected row count: %',
            v_row_n;
    END IF;

    IF v_distinct_area_n <> v_row_n THEN
        RAISE EXCEPTION
            'Duplicate area_id detected';
    END IF;

    IF v_distinct_geometry_n <> v_row_n THEN
        RAISE EXCEPTION
            'Duplicate source_geometry_id detected';
    END IF;

    IF v_missing_spectral_n <> 0 THEN
        RAISE EXCEPTION
            'Missing spectral links: %',
            v_missing_spectral_n;
    END IF;

    IF v_incomplete_spectral_n <> 0 THEN
        RAISE EXCEPTION
            'Incomplete spectral features: %',
            v_incomplete_spectral_n;
    END IF;
END
$$;

\echo 'AREA_FEATURE_MATRIX_REGIONAL_V1_OK'
