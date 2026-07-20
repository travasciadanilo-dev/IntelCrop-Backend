BEGIN;

DROP VIEW IF EXISTS landcover_olive_visual_training_v2_missing_spectral_v1;

CREATE VIEW landcover_olive_visual_training_v2_missing_spectral_v1 AS
SELECT DISTINCT
    g.id,
    g.subtype_id,
    g.source_layer_version,
    g.source_file,
    g.source_feature_id,
    g.geom,

    ST_Area(g.geom::geography) / 10000.0
        AS area_ha,

    t.sample_id,
    t.sample_version,
    t.area_id,
    t.spatial_validation_zone,
    t.binary_visual_label_v2,
    t.visual_label_v2

FROM olive_visual_review_sample_v2_training_v1 AS t

INNER JOIN landcover_subtype_geometries AS g
    ON g.id::text = t.source_geometry_id::text

LEFT JOIN landcover_subtype_spectral_qc AS s
    ON s.source_geometry_id = g.id
   AND s.spectral_qc_version = 'olive_spectral_qc_v1'

WHERE t.is_complete IS TRUE
  AND t.is_training_eligible IS TRUE
  AND t.binary_visual_label_v2 IN (0, 1)
  AND ST_IsValid(g.geom)
  AND NOT ST_IsEmpty(g.geom)
  AND s.source_geometry_id IS NULL;


COMMENT ON VIEW
landcover_olive_visual_training_v2_missing_spectral_v1 IS
'Geometrie del visual checking v2 training-eligible ancora prive di olive_spectral_qc_v1. Le etichette sono conservate soltanto per audit e controllo della copertura; non intervengono nel calcolo spettrale.';

COMMIT;
