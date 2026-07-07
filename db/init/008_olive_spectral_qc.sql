CREATE TABLE IF NOT EXISTS landcover_subtype_spectral_qc (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_geometry_id UUID NOT NULL REFERENCES landcover_subtype_geometries(id) ON DELETE CASCADE,
    spectral_qc_version TEXT NOT NULL,
    source_view TEXT NOT NULL,

    n_observations INTEGER NOT NULL,

    ndvi_median DOUBLE PRECISION NULL,
    evi_median DOUBLE PRECISION NULL,
    ndmi_median DOUBLE PRECISION NULL,
    bsi_median DOUBLE PRECISION NULL,

    ndvi_p25 DOUBLE PRECISION NULL,
    ndvi_p75 DOUBLE PRECISION NULL,
    evi_p25 DOUBLE PRECISION NULL,
    evi_p75 DOUBLE PRECISION NULL,
    ndmi_p25 DOUBLE PRECISION NULL,
    ndmi_p75 DOUBLE PRECISION NULL,
    bsi_p25 DOUBLE PRECISION NULL,
    bsi_p75 DOUBLE PRECISION NULL,

    ndvi_stddev DOUBLE PRECISION NULL,
    evi_stddev DOUBLE PRECISION NULL,
    ndmi_stddev DOUBLE PRECISION NULL,
    bsi_stddev DOUBLE PRECISION NULL,

    spectral_flag TEXT NOT NULL CHECK (
        spectral_flag IN ('strong', 'moderate', 'weak', 'excluded')
    ),

    usable_for_baseline_spectral BOOLEAN NOT NULL DEFAULT FALSE,
    exclusion_reason TEXT NULL,

    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (source_geometry_id, spectral_qc_version)
);

CREATE INDEX IF NOT EXISTS idx_landcover_spectral_qc_source_geometry
ON landcover_subtype_spectral_qc (source_geometry_id);

CREATE INDEX IF NOT EXISTS idx_landcover_spectral_qc_version
ON landcover_subtype_spectral_qc (spectral_qc_version);

CREATE INDEX IF NOT EXISTS idx_landcover_spectral_qc_flag
ON landcover_subtype_spectral_qc (spectral_flag);

CREATE INDEX IF NOT EXISTS idx_landcover_spectral_qc_baseline
ON landcover_subtype_spectral_qc (usable_for_baseline_spectral);


CREATE OR REPLACE VIEW landcover_olive_pure_baseline_v1 AS
SELECT
    s.*,

    sp.spectral_qc_version,
    sp.n_observations,

    sp.ndvi_median,
    sp.evi_median,
    sp.ndmi_median,
    sp.bsi_median,

    sp.ndvi_p25,
    sp.ndvi_p75,
    sp.evi_p25,
    sp.evi_p75,
    sp.ndmi_p25,
    sp.ndmi_p75,
    sp.bsi_p25,
    sp.bsi_p75,

    sp.ndvi_stddev,
    sp.evi_stddev,
    sp.ndmi_stddev,
    sp.bsi_stddev,

    sp.spectral_flag,
    sp.usable_for_baseline_spectral
FROM landcover_olive_pure_baseline_seed_no_urban_v1 s
JOIN landcover_subtype_spectral_qc sp
  ON sp.source_geometry_id = s.id
WHERE sp.spectral_qc_version = 'olive_spectral_qc_v1'
  AND sp.usable_for_baseline_spectral = TRUE;
