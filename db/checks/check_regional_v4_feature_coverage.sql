\set ON_ERROR_STOP on
\pset pager off

\echo ''
\echo '============================================================'
\echo '1. POSSIBILI SORGENTI DEL POOL REGIONALE'
\echo '============================================================'

SELECT
    c.table_schema,
    c.table_name,
    CASE
        WHEN t.table_name IS NOT NULL THEN 'table'
        WHEN v.table_name IS NOT NULL THEN 'view'
        WHEN m.matviewname IS NOT NULL THEN 'materialized_view'
        ELSE 'other'
    END AS object_type,
    COUNT(*) FILTER (
        WHERE c.column_name = 'area_id'
    ) AS has_area_id,
    COUNT(*) FILTER (
        WHERE c.column_name IN (
            'source_geometry_id',
            'geometry_id'
        )
    ) AS has_source_geometry_id,
    COUNT(*) FILTER (
        WHERE c.column_name IN (
            'geom',
            'geometry'
        )
    ) AS has_geometry,
    COUNT(*) FILTER (
        WHERE c.column_name = 'n_observations'
    ) AS has_spectral_observations,
    COUNT(*) FILTER (
        WHERE c.column_name = 'model_version'
    ) AS has_model_version
FROM information_schema.columns c
LEFT JOIN information_schema.tables t
    ON t.table_schema = c.table_schema
   AND t.table_name = c.table_name
LEFT JOIN information_schema.views v
    ON v.table_schema = c.table_schema
   AND v.table_name = c.table_name
LEFT JOIN pg_matviews m
    ON m.schemaname = c.table_schema
   AND m.matviewname = c.table_name
WHERE
    c.table_schema = 'public'
    AND (
        c.table_name ILIKE '%regional%'
        OR c.table_name ILIKE '%catalog%'
        OR c.table_name ILIKE '%feature_matrix%'
        OR c.table_name ILIKE '%high_confidence%'
        OR c.table_name ILIKE '%candidate%'
        OR c.table_name ILIKE '%spectral%'
    )
GROUP BY
    c.table_schema,
    c.table_name,
    t.table_name,
    v.table_name,
    m.matviewname
HAVING
    COUNT(*) FILTER (
        WHERE c.column_name = 'area_id'
    ) > 0
    OR
    COUNT(*) FILTER (
        WHERE c.column_name IN (
            'source_geometry_id',
            'geometry_id'
        )
    ) > 0
ORDER BY
    c.table_name;


\echo ''
\echo '============================================================'
\echo '2. COLONNE DELLE MATRICI REGIONALI ESISTENTI'
\echo '============================================================'

SELECT
    table_name,
    ordinal_position,
    column_name,
    data_type
FROM information_schema.columns
WHERE
    table_schema = 'public'
    AND (
        table_name ILIKE '%regional_feature_matrix%'
        OR table_name ILIKE '%area_feature_matrix%'
        OR table_name ILIKE '%catalog_scored%'
        OR table_name ILIKE '%candidate_pool%'
    )
ORDER BY
    table_name,
    ordinal_position;


\echo ''
\echo '============================================================'
\echo '3. OGGETTI CONTENENTI LE FEATURE GREZZE DEL MODELLO V4'
\echo '============================================================'

WITH required_columns(column_name) AS (
    VALUES
        ('area_id'),
        ('area_ha_raw'),
        ('perimeter_m_raw'),
        ('compactness_raw'),
        ('n_points'),
        ('large_polygon_flag'),
        ('small_candidate_flag'),
        ('complex_boundary_flag'),
        ('n_observations'),
        ('ndvi_median'),
        ('ndvi_p25'),
        ('ndvi_p75'),
        ('ndvi_stddev'),
        ('evi_median'),
        ('evi_p25'),
        ('evi_p75'),
        ('evi_stddev'),
        ('ndmi_median'),
        ('ndmi_p25'),
        ('ndmi_p75'),
        ('ndmi_stddev'),
        ('bsi_median'),
        ('bsi_p25'),
        ('bsi_p75'),
        ('bsi_stddev')
),
object_columns AS (
    SELECT
        table_schema,
        table_name,
        column_name
    FROM information_schema.columns
    WHERE table_schema = 'public'
),
coverage AS (
    SELECT
        oc.table_schema,
        oc.table_name,
        COUNT(DISTINCT oc.column_name)
            FILTER (
                WHERE oc.column_name IN (
                    SELECT column_name
                    FROM required_columns
                )
            ) AS available_required_columns
    FROM object_columns oc
    GROUP BY
        oc.table_schema,
        oc.table_name
)
SELECT
    table_schema,
    table_name,
    available_required_columns,
    25 - available_required_columns
        AS missing_required_columns
FROM coverage
WHERE available_required_columns > 0
ORDER BY
    available_required_columns DESC,
    table_name;


\echo ''
\echo '============================================================'
\echo '4. DETTAGLIO COLONNE MANCANTI PER OGGETTO'
\echo '============================================================'

WITH required_columns(column_name) AS (
    VALUES
        ('area_id'),
        ('area_ha_raw'),
        ('perimeter_m_raw'),
        ('compactness_raw'),
        ('n_points'),
        ('large_polygon_flag'),
        ('small_candidate_flag'),
        ('complex_boundary_flag'),
        ('n_observations'),
        ('ndvi_median'),
        ('ndvi_p25'),
        ('ndvi_p75'),
        ('ndvi_stddev'),
        ('evi_median'),
        ('evi_p25'),
        ('evi_p75'),
        ('evi_stddev'),
        ('ndmi_median'),
        ('ndmi_p25'),
        ('ndmi_p75'),
        ('ndmi_stddev'),
        ('bsi_median'),
        ('bsi_p25'),
        ('bsi_p75'),
        ('bsi_stddev')
),
candidate_objects AS (
    SELECT
        table_schema,
        table_name,
        COUNT(*) FILTER (
            WHERE column_name IN (
                SELECT column_name
                FROM required_columns
            )
        ) AS available_n
    FROM information_schema.columns
    WHERE table_schema = 'public'
    GROUP BY
        table_schema,
        table_name
    HAVING
        COUNT(*) FILTER (
            WHERE column_name IN (
                SELECT column_name
                FROM required_columns
            )
        ) >= 5
)
SELECT
    o.table_schema,
    o.table_name,
    o.available_n,
    STRING_AGG(
        r.column_name,
        ', '
        ORDER BY r.column_name
    ) AS missing_columns
FROM candidate_objects o
CROSS JOIN required_columns r
LEFT JOIN information_schema.columns c
    ON c.table_schema = o.table_schema
   AND c.table_name = o.table_name
   AND c.column_name = r.column_name
WHERE c.column_name IS NULL
GROUP BY
    o.table_schema,
    o.table_name,
    o.available_n
ORDER BY
    o.available_n DESC,
    o.table_name;


\echo ''
\echo '============================================================'
\echo '5. VERSIONI SPETTRALI DISPONIBILI'
\echo '============================================================'

SELECT
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE
    table_schema = 'public'
    AND (
        table_name ILIKE '%spectral%'
        OR table_name ILIKE '%feature_matrix%'
    )
    AND (
        column_name ILIKE '%version%'
        OR column_name IN (
            'area_id',
            'source_geometry_id',
            'n_observations',
            'ndvi_median',
            'evi_median',
            'ndmi_median',
            'bsi_median',
            'created_at',
            'updated_at'
        )
    )
ORDER BY
    table_name,
    ordinal_position;


\echo ''
\echo '============================================================'
\echo '6. DEFINIZIONI DELLE VISTE REGIONALI PRINCIPALI'
\echo '============================================================'

SELECT
    schemaname,
    viewname,
    definition
FROM pg_views
WHERE
    schemaname = 'public'
    AND (
        viewname ILIKE '%regional_feature_matrix%'
        OR viewname ILIKE '%area_feature_matrix%'
        OR viewname ILIKE '%catalog_scored%'
        OR viewname ILIKE '%candidate_pool%'
    )
ORDER BY viewname;
