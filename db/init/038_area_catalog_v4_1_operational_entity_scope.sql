BEGIN;

CREATE OR REPLACE VIEW
area_catalog_v4_1_operational_entity_scope AS
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
JOIN area_catalog_v4_1_operational c
  ON c.geom && t.geom
 AND ST_Intersects(c.geom, t.geom)
WHERE e.entity_status = 'active';


COMMENT ON VIEW
area_catalog_v4_1_operational_entity_scope IS
'Entity-scoped internal v4.1 operational catalog including versioned regional spectral features. Intended for analysis-job snapshots, not direct public catalog exposure.';

COMMIT;
