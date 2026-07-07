CREATE OR REPLACE VIEW landcover_olive_pure_visual_training_v1 AS
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
  AND v.visual_label IN ('olive_like', 'not_olive_like', 'uncertain');


CREATE OR REPLACE VIEW landcover_olive_visual_training_eval_v1 AS
SELECT
    t.id,
    t.subtype_id,
    t.source_layer_version,
    t.qc_version,
    t.qc_class,
    t.visual_qc_version,
    t.visual_label,

    t.area_ha,
    t.compactness,
    t.n_points,
    t.n_parts,
    t.qc_score,

    u.urban_qc_version,
    u.artificial_flag,
    u.usable_for_baseline_context,
    u.built_cover_percent,
    u.dynamic_built_mean,
    u.dynamic_built_p95,

    sp.spectral_qc_version,
    sp.spectral_flag,
    sp.usable_for_baseline_spectral,
    sp.n_observations,
    sp.ndvi_median,
    sp.evi_median,
    sp.ndmi_median,
    sp.bsi_median,

    (
        u.usable_for_baseline_context = TRUE
        AND sp.usable_for_baseline_spectral = TRUE
    ) AS predicted_baseline_candidate,

    CASE
        WHEN t.visual_label = 'olive_like'
             AND u.usable_for_baseline_context = TRUE
             AND sp.usable_for_baseline_spectral = TRUE
        THEN 'true_positive'

        WHEN t.visual_label = 'olive_like'
             AND (
                u.usable_for_baseline_context = FALSE
                OR sp.usable_for_baseline_spectral = FALSE
             )
        THEN 'false_negative'

        WHEN t.visual_label IN ('not_olive_like', 'uncertain')
             AND u.usable_for_baseline_context = TRUE
             AND sp.usable_for_baseline_spectral = TRUE
        THEN 'false_positive'

        WHEN t.visual_label IN ('not_olive_like', 'uncertain')
             AND (
                u.usable_for_baseline_context = FALSE
                OR sp.usable_for_baseline_spectral = FALSE
             )
        THEN 'true_negative'

        ELSE 'unclassified'
    END AS eval_class,

    t.geom
FROM landcover_olive_pure_visual_training_v1 t
LEFT JOIN landcover_subtype_urban_qc u
  ON u.source_geometry_id = t.id
 AND u.urban_qc_version = 'olive_urban_qc_v1'
LEFT JOIN landcover_subtype_spectral_qc sp
  ON sp.source_geometry_id = t.id
 AND sp.spectral_qc_version = 'olive_spectral_qc_v1';