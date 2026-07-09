DROP VIEW IF EXISTS area_catalog_v1_diagnostic;

CREATE OR REPLACE VIEW area_catalog_v1_diagnostic AS
WITH catalog AS (
    SELECT
        d.area_id::text AS area_id,
        g.id AS source_geometry_id,

        'calabria'::text AS region_code,
        'Calabria'::text AS region_label,

        g.subtype_id AS technical_subtype_id,
        COALESCE(s.label_it, g.subtype_id) AS technical_subtype_label,

        CASE
            WHEN g.subtype_id IN ('olive_pure', 'olive_citrus', 'olive_vine')
            THEN 'permanent_tree_crop'
            ELSE 'agricultural_area'
        END AS area_type,

        CASE
            WHEN g.subtype_id IN ('olive_pure', 'olive_citrus', 'olive_vine')
            THEN 'Coltura arborea permanente'
            ELSE 'Area agricola'
        END AS area_type_label,

        d.spatial_validation_zone,
        d.candidate_origin,

        d.area_ha_raw AS area_ha,
        d.area_bin_raw,
        d.n_points,
        d.n_parts,
        d.n_points_bin,
        d.n_parts_bin,

        d.current_high_confidence_v2,
        d.identity_reference_match,
        d.strict_reference_match,

        d.large_polygon_flag,
        d.small_candidate_flag,
        d.complex_boundary_flag,

        d.experimental_reliability_score_v3 AS reliability_score,
        d.experimental_reliability_class_v3 AS reliability_class,
        d.experimental_reliability_label_v3 AS reliability_label,

        CASE
            WHEN d.experimental_reliability_class_v3 = 'very_high' THEN 4
            WHEN d.experimental_reliability_class_v3 = 'high' THEN 3
            WHEN d.experimental_reliability_class_v3 = 'compatible' THEN 2
            WHEN d.experimental_reliability_class_v3 = 'low' THEN 1
            ELSE 0
        END AS reliability_rank,

        CASE
            WHEN d.experimental_reliability_class_v3 IN ('very_high', 'high')
            THEN true
            ELSE false
        END AS catalog_priority_candidate,

        CASE
            WHEN d.experimental_reliability_class_v3 = 'very_high'
            THEN 'Prioritaria diagnostica'
            WHEN d.experimental_reliability_class_v3 = 'high'
            THEN 'Alta priorità diagnostica'
            WHEN d.experimental_reliability_class_v3 = 'compatible'
            THEN 'Compatibile diagnostica'
            ELSE 'Bassa priorità diagnostica'
        END AS catalog_status_label,

        d.reliability_model_version,
        d.reliability_model_status,
        d.reliability_model_limitations,
        d.reliability_score_created_at,

        'area_catalog_v1_diagnostic'::text AS catalog_version,
        'diagnostic_not_final'::text AS catalog_status,

        ST_X(ST_PointOnSurface(g.geom)) AS centroid_lon,
        ST_Y(ST_PointOnSurface(g.geom)) AS centroid_lat,

        ST_XMin(ST_Envelope(g.geom)) AS bbox_min_lon,
        ST_YMin(ST_Envelope(g.geom)) AS bbox_min_lat,
        ST_XMax(ST_Envelope(g.geom)) AS bbox_max_lon,
        ST_YMax(ST_Envelope(g.geom)) AS bbox_max_lat,

        g.geom

    FROM olive_candidate_pool_v2_reliability_v3_diagnostic_v1 d
    JOIN landcover_subtype_geometries g
      ON g.id::text = d.area_id::text
    LEFT JOIN landcover_subtypes s
      ON s.id = g.subtype_id
)
SELECT *
FROM catalog;