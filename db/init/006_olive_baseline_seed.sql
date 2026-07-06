CREATE OR REPLACE VIEW landcover_olive_pure_baseline_seed_v1 AS
SELECT
    g.id,
    g.subtype_id,
    g.source_layer_version,
    g.source_file,
    g.source_feature_id,

    q.qc_version,
    q.qc_class,
    q.area_ha,
    q.perimeter_m,
    q.compactness,
    q.n_points,
    q.n_parts,
    q.qc_score,

    v.visual_qc_version,
    v.visual_label,
    v.reviewer,
    v.reviewed_at,
    v.notes,

    g.geom
FROM landcover_subtype_geometries g
JOIN landcover_subtype_geometry_qc q
  ON q.source_geometry_id = g.id
JOIN landcover_subtype_visual_qc v
  ON v.source_geometry_id = g.id
WHERE g.subtype_id = 'olive_pure'
  AND q.qc_version = 'olive_pure_geom_qc_v2'
  AND q.qc_class = 'high_confidence'
  AND v.visual_qc_version = 'olive_visual_qc_v1'
  AND v.visual_label = 'olive_like';