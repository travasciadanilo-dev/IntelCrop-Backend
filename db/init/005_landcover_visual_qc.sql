CREATE TABLE IF NOT EXISTS landcover_subtype_visual_qc_sample (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_geometry_id UUID NOT NULL REFERENCES landcover_subtype_geometries(id) ON DELETE CASCADE,
    sampling_version TEXT NOT NULL,
    qc_version TEXT NOT NULL,

    subtype_id TEXT NOT NULL,
    source_layer_version TEXT NOT NULL,

    area_class TEXT NOT NULL,
    area_ha DOUBLE PRECISION NOT NULL,
    compactness DOUBLE PRECISION NOT NULL,
    n_points INTEGER NOT NULL,
    n_parts INTEGER NOT NULL,

    sample_rank INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_geometry_id, sampling_version)
);

CREATE INDEX IF NOT EXISTS idx_visual_qc_sample_version
ON landcover_subtype_visual_qc_sample (sampling_version);

CREATE INDEX IF NOT EXISTS idx_visual_qc_sample_source_geometry
ON landcover_subtype_visual_qc_sample (source_geometry_id);


CREATE TABLE IF NOT EXISTS landcover_subtype_visual_qc (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_geometry_id UUID NOT NULL REFERENCES landcover_subtype_geometries(id) ON DELETE CASCADE,
    sampling_version TEXT NOT NULL,
    visual_qc_version TEXT NOT NULL,

    visual_label TEXT NOT NULL CHECK (
        visual_label IN ('olive_like', 'uncertain', 'not_olive_like')
    ),

    reviewer TEXT NOT NULL DEFAULT 'manual_review',
    notes TEXT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_geometry_id, visual_qc_version)
);

CREATE INDEX IF NOT EXISTS idx_visual_qc_version
ON landcover_subtype_visual_qc (visual_qc_version);

CREATE INDEX IF NOT EXISTS idx_visual_qc_label
ON landcover_subtype_visual_qc (visual_label);

CREATE INDEX IF NOT EXISTS idx_visual_qc_source_geometry
ON landcover_subtype_visual_qc (source_geometry_id);


CREATE OR REPLACE VIEW landcover_olive_pure_visual_olive_like_v1 AS
SELECT
    g.id,
    g.subtype_id,
    g.source_layer_version,
    g.source_file,
    g.source_feature_id,

    q.qc_version,
    q.qc_class,
    q.area_ha,
    q.compactness,
    q.n_points,
    q.n_parts,

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