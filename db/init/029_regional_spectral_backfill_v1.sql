BEGIN;

CREATE OR REPLACE VIEW regional_spectral_backfill_pending_v1 AS
SELECT
    p.area_id,
    p.source_geometry_id::uuid
        AS source_geometry_id,
    p.subtype_id,
    p.source_layer_version,
    p.spatial_validation_zone,
    p.current_high_confidence_v2,
    p.area_ha_raw,
    p.perimeter_m_raw,
    p.compactness_raw,
    p.n_points,
    p.n_parts
FROM olive_candidate_pool_v2 p
LEFT JOIN landcover_subtype_spectral_qc s
    ON s.source_geometry_id =
       p.source_geometry_id::uuid
   AND s.spectral_qc_version =
       'olive_spectral_qc_v1'
WHERE
    s.source_geometry_id IS NULL;

COMMENT ON VIEW regional_spectral_backfill_pending_v1 IS
'Dynamic view of olive_candidate_pool_v2 areas still missing complete spectral features for olive_spectral_qc_v1. Rows disappear after a successful upsert into landcover_subtype_spectral_qc.';

COMMIT;

