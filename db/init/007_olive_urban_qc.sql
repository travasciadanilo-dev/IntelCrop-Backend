CREATE TABLE IF NOT EXISTS landcover_subtype_urban_qc (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_geometry_id UUID NOT NULL REFERENCES landcover_subtype_geometries(id) ON DELETE CASCADE,
    urban_qc_version TEXT NOT NULL,
    source_view TEXT NOT NULL,

    built_cover_percent DOUBLE PRECISION NULL,
    dynamic_built_mean DOUBLE PRECISION NULL,
    dynamic_built_p95 DOUBLE PRECISION NULL,

    artificial_flag TEXT NOT NULL CHECK (
        artificial_flag IN ('none', 'low', 'medium', 'high')
    ),

    usable_for_baseline_context BOOLEAN NOT NULL DEFAULT FALSE,
    exclusion_reason TEXT NULL,

    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_geometry_id, urban_qc_version)
);

CREATE INDEX IF NOT EXISTS idx_landcover_urban_qc_source_geometry
ON landcover_subtype_urban_qc (source_geometry_id);

CREATE INDEX IF NOT EXISTS idx_landcover_urban_qc_version
ON landcover_subtype_urban_qc (urban_qc_version);

CREATE INDEX IF NOT EXISTS idx_landcover_urban_qc_flag
ON landcover_subtype_urban_qc (artificial_flag);

CREATE INDEX IF NOT EXISTS idx_landcover_urban_qc_baseline
ON landcover_subtype_urban_qc (usable_for_baseline_context);


CREATE OR REPLACE VIEW landcover_olive_pure_baseline_seed_no_urban_v1 AS
SELECT
    s.*,
    u.urban_qc_version,
    u.built_cover_percent,
    u.dynamic_built_mean,
    u.dynamic_built_p95,
    u.artificial_flag,
    u.usable_for_baseline_context
FROM landcover_olive_pure_baseline_seed_v1 s
JOIN landcover_subtype_urban_qc u
  ON u.source_geometry_id = s.id
WHERE u.urban_qc_version = 'olive_urban_qc_v1'
  AND u.usable_for_baseline_context = TRUE;