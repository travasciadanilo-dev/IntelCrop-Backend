CREATE TABLE IF NOT EXISTS landcover_subtype_geometry_qc (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_geometry_id UUID NOT NULL REFERENCES landcover_subtype_geometries(id) ON DELETE CASCADE,
    subtype_id TEXT NOT NULL REFERENCES landcover_subtypes(id),
    source_layer_version TEXT NOT NULL,
    qc_version TEXT NOT NULL,

    area_ha DOUBLE PRECISION NOT NULL,
    perimeter_m DOUBLE PRECISION NOT NULL,
    compactness DOUBLE PRECISION NOT NULL,
    n_points INTEGER NOT NULL,
    n_parts INTEGER NOT NULL,
    is_valid BOOLEAN NOT NULL,

    area_flag TEXT NOT NULL,
    shape_flag TEXT NOT NULL,
    complexity_flag TEXT NOT NULL,
    multipart_flag TEXT NOT NULL,

    qc_score INTEGER NOT NULL,
    qc_class TEXT NOT NULL CHECK (
        qc_class IN ('high_confidence', 'medium_confidence', 'low_confidence', 'excluded')
    ),

    usable_for_matching BOOLEAN NOT NULL DEFAULT TRUE,
    usable_for_baseline BOOLEAN NOT NULL DEFAULT FALSE,

    exclusion_reason TEXT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_geometry_id, qc_version)
);

CREATE INDEX IF NOT EXISTS idx_landcover_qc_source_geometry_id
ON landcover_subtype_geometry_qc (source_geometry_id);

CREATE INDEX IF NOT EXISTS idx_landcover_qc_subtype
ON landcover_subtype_geometry_qc (subtype_id);

CREATE INDEX IF NOT EXISTS idx_landcover_qc_version
ON landcover_subtype_geometry_qc (qc_version);

CREATE INDEX IF NOT EXISTS idx_landcover_qc_class
ON landcover_subtype_geometry_qc (qc_class);

CREATE INDEX IF NOT EXISTS idx_landcover_qc_baseline
ON landcover_subtype_geometry_qc (usable_for_baseline);

CREATE OR REPLACE VIEW landcover_olive_pure_high_confidence_v1 AS
SELECT
    g.id,
    g.subtype_id,
    g.source_layer_version,
    g.source_file,
    g.source_feature_id,
    q.qc_version,
    q.area_ha,
    q.perimeter_m,
    q.compactness,
    q.n_points,
    q.n_parts,
    q.qc_score,
    q.qc_class,
    q.usable_for_matching,
    q.usable_for_baseline,
    g.geom
FROM landcover_subtype_geometries g
JOIN landcover_subtype_geometry_qc q
  ON q.source_geometry_id = g.id
WHERE g.subtype_id = 'olive_pure'
  AND q.qc_version = 'olive_pure_geom_qc_v1'
  AND q.qc_class = 'high_confidence'
  AND q.usable_for_baseline = TRUE;