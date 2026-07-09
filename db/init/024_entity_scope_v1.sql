CREATE TABLE IF NOT EXISTS app_entities_v1 (
    entity_id text PRIMARY KEY,
    entity_name text NOT NULL,
    entity_type text NOT NULL,
    entity_status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT app_entities_v1_entity_status_check
    CHECK (entity_status IN ('active', 'suspended', 'archived')),

    CONSTRAINT app_entities_v1_entity_type_check
    CHECK (entity_type IN ('public_administration', 'consortium', 'demo', 'internal'))
);

CREATE TABLE IF NOT EXISTS app_entity_territories_v1 (
    territory_id bigserial PRIMARY KEY,
    entity_id text NOT NULL REFERENCES app_entities_v1(entity_id) ON DELETE CASCADE,
    territory_name text NOT NULL,
    territory_scope_version text NOT NULL,
    territory_status text NOT NULL DEFAULT 'active',
    source_description text NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT app_entity_territories_v1_status_check
    CHECK (territory_status IN ('active', 'inactive', 'archived'))
);

CREATE INDEX IF NOT EXISTS app_entity_territories_v1_geom_gix
ON app_entity_territories_v1
USING GIST (geom);

CREATE INDEX IF NOT EXISTS app_entity_territories_v1_entity_idx
ON app_entity_territories_v1(entity_id);

INSERT INTO app_entities_v1 (
    entity_id,
    entity_name,
    entity_type,
    entity_status
)
VALUES (
    'calabria_demo',
    'Calabria demo entity',
    'demo',
    'active'
)
ON CONFLICT (entity_id) DO UPDATE
SET
    entity_name = EXCLUDED.entity_name,
    entity_type = EXCLUDED.entity_type,
    entity_status = EXCLUDED.entity_status,
    updated_at = now();

DELETE FROM app_entity_territories_v1
WHERE entity_id = 'calabria_demo'
  AND territory_scope_version = 'calabria_demo_full_catalog_extent_v1';

INSERT INTO app_entity_territories_v1 (
    entity_id,
    territory_name,
    territory_scope_version,
    territory_status,
    source_description,
    geom
)
SELECT
    'calabria_demo'::text AS entity_id,
    'Full diagnostic catalog extent'::text AS territory_name,
    'calabria_demo_full_catalog_extent_v1'::text AS territory_scope_version,
    'active'::text AS territory_status,
    'Demo territory generated from the bounding extent of area_catalog_v1_diagnostic. Replace with official entity boundaries for production.'::text AS source_description,
    ST_Multi(
        ST_SetSRID(
            ST_Extent(geom)::geometry,
            4326
        )
    )::geometry(MultiPolygon, 4326) AS geom
FROM area_catalog_v1_diagnostic;

DROP VIEW IF EXISTS area_catalog_v1_entity_scope;

CREATE OR REPLACE VIEW area_catalog_v1_entity_scope AS
SELECT
    e.entity_id,
    e.entity_name,
    e.entity_type,
    e.entity_status,

    t.territory_id,
    t.territory_name,
    t.territory_scope_version,
    t.territory_status,

    c.*

FROM app_entities_v1 e
JOIN app_entity_territories_v1 t
  ON t.entity_id = e.entity_id
 AND t.territory_status = 'active'
JOIN area_catalog_v1_diagnostic c
  ON c.geom && t.geom
 AND ST_Intersects(c.geom, t.geom)
WHERE e.entity_status = 'active';