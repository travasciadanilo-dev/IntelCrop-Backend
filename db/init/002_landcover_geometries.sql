DROP TABLE IF EXISTS landcover_subtype_geometries CASCADE;

CREATE TABLE landcover_subtype_geometries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subtype_id TEXT NOT NULL REFERENCES landcover_subtypes(id),
    source_layer_version TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_feature_id BIGINT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    area_ha DOUBLE PRECISION NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_landcover_subtype_geometries_subtype
ON landcover_subtype_geometries (subtype_id);

CREATE INDEX idx_landcover_subtype_geometries_version
ON landcover_subtype_geometries (source_layer_version);