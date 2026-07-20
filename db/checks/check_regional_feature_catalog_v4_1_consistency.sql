\set ON_ERROR_STOP on
\pset pager off

\echo ''
\echo 'Regional feature matrix and v4.1 catalog consistency'
\echo '----------------------------------------------------'

SELECT
    COUNT(*) AS feature_matrix_n,
    COUNT(*) FILTER (
        WHERE c.area_id IS NULL
    ) AS missing_catalog_n,
    COUNT(*) FILTER (
        WHERE r.area_id IS NULL
    ) AS missing_score_n
FROM area_feature_matrix_regional_v1 f
LEFT JOIN area_catalog_v4_1_diagnostic c
    ON c.area_id = f.area_id
LEFT JOIN regional_reliability_scores_v4_1_diagnostic r
    ON r.area_id = f.area_id;

SELECT
    COUNT(*) AS catalog_n,
    COUNT(f.area_id) AS matched_feature_n,
    COUNT(*) FILTER (
        WHERE f.area_id IS NULL
    ) AS missing_feature_n
FROM area_catalog_v4_1_diagnostic c
LEFT JOIN area_feature_matrix_regional_v1 f
    ON f.area_id = c.area_id;

SELECT
    c.reliability_class,
    COUNT(*) AS n_areas,
    ROUND(AVG(f.n_observations)::numeric, 2)
        AS mean_observations,
    ROUND(AVG(f.ndvi_median)::numeric, 4)
        AS mean_ndvi,
    ROUND(AVG(f.evi_median)::numeric, 4)
        AS mean_evi,
    ROUND(AVG(f.ndmi_median)::numeric, 4)
        AS mean_ndmi,
    ROUND(AVG(f.bsi_median)::numeric, 4)
        AS mean_bsi
FROM area_catalog_v4_1_diagnostic c
JOIN area_feature_matrix_regional_v1 f
    ON f.area_id = c.area_id
GROUP BY c.reliability_class
ORDER BY CASE c.reliability_class
    WHEN 'low' THEN 1
    WHEN 'compatible' THEN 2
    WHEN 'very_high' THEN 3
    ELSE 99
END;

DO $$
DECLARE
    v_feature_n bigint;
    v_catalog_n bigint;
    v_score_n bigint;
    v_missing_catalog_n bigint;
    v_missing_score_n bigint;
    v_missing_feature_n bigint;
BEGIN
    SELECT COUNT(*)
    INTO v_feature_n
    FROM area_feature_matrix_regional_v1;

    SELECT COUNT(*)
    INTO v_catalog_n
    FROM area_catalog_v4_1_diagnostic;

    SELECT COUNT(*)
    INTO v_score_n
    FROM regional_reliability_scores_v4_1_diagnostic;

    SELECT
        COUNT(*) FILTER (
            WHERE c.area_id IS NULL
        ),
        COUNT(*) FILTER (
            WHERE r.area_id IS NULL
        )
    INTO
        v_missing_catalog_n,
        v_missing_score_n
    FROM area_feature_matrix_regional_v1 f
    LEFT JOIN area_catalog_v4_1_diagnostic c
        ON c.area_id = f.area_id
    LEFT JOIN regional_reliability_scores_v4_1_diagnostic r
        ON r.area_id = f.area_id;

    SELECT COUNT(*) FILTER (
        WHERE f.area_id IS NULL
    )
    INTO v_missing_feature_n
    FROM area_catalog_v4_1_diagnostic c
    LEFT JOIN area_feature_matrix_regional_v1 f
        ON f.area_id = c.area_id;

    IF v_feature_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected feature matrix count: %',
            v_feature_n;
    END IF;

    IF v_catalog_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected catalog count: %',
            v_catalog_n;
    END IF;

    IF v_score_n <> 40261 THEN
        RAISE EXCEPTION
            'Unexpected score count: %',
            v_score_n;
    END IF;

    IF v_missing_catalog_n <> 0 THEN
        RAISE EXCEPTION
            'Feature rows missing catalog match: %',
            v_missing_catalog_n;
    END IF;

    IF v_missing_score_n <> 0 THEN
        RAISE EXCEPTION
            'Feature rows missing score match: %',
            v_missing_score_n;
    END IF;

    IF v_missing_feature_n <> 0 THEN
        RAISE EXCEPTION
            'Catalog rows missing feature match: %',
            v_missing_feature_n;
    END IF;
END
$$;

\echo 'REGIONAL_FEATURE_CATALOG_V4_1_CONSISTENCY_OK'
