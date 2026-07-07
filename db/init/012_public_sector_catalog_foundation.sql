CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ============================================================
-- 012_public_sector_catalog_foundation.sql
-- IntelCrop Calabria
--
-- Scopo:
-- - allineare il backend alla versione futura catalogo/PA/consorzio;
-- - introdurre ente_id, ruoli, territorio di competenza;
-- - versionare dati, modello e metodologia;
-- - mantenere separato il concetto di identity reference seed
--   dal concetto di baseline statistica per anomaly detection;
-- - predisporre export GIS standard e audit contrattuale.
--
-- Nota:
-- Questo file NON sostituisce baseline_v1 e strict_baseline_v1.
-- Aggiunge alias metodologici e struttura istituzionale.
-- ============================================================


-- ============================================================
-- 1. Enti / tenant istituzionali
-- ============================================================

CREATE TABLE IF NOT EXISTS public_entities (
    ente_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    ente_code text NOT NULL UNIQUE,
    ente_name text NOT NULL,

    ente_type text NOT NULL DEFAULT 'other'
        CHECK (
            ente_type IN (
                'regione',
                'provincia',
                'comune',
                'consorzio',
                'unione_comuni',
                'ente_ricerca',
                'azienda_pubblica',
                'other'
            )
        ),

    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'suspended', 'archived')),

    provisioning_mode text NOT NULL DEFAULT 'manual_contract'
        CHECK (
            provisioning_mode IN (
                'manual_contract',
                'spid_cie_ready',
                'internal_demo'
            )
        ),

    max_areas_per_batch integer NOT NULL DEFAULT 5
        CHECK (max_areas_per_batch > 0 AND max_areas_per_batch <= 50),

    notes text,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS public_entity_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    ente_id uuid NOT NULL REFERENCES public_entities(ente_id)
        ON DELETE CASCADE,

    auth_id text NOT NULL,

    role text NOT NULL
        CHECK (role IN ('analyst', 'referent', 'admin')),

    is_active boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (ente_id, auth_id)
);


CREATE TABLE IF NOT EXISTS public_entity_territories (
    territory_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    ente_id uuid NOT NULL REFERENCES public_entities(ente_id)
        ON DELETE CASCADE,

    territory_name text NOT NULL,
    territory_type text NOT NULL DEFAULT 'custom_polygon'
        CHECK (
            territory_type IN (
                'region',
                'province',
                'municipality_list',
                'custom_polygon'
            )
        ),

    source_note text,
    geom geometry(MultiPolygon, 4326) NOT NULL,

    is_active boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT public_entity_territories_geom_valid
        CHECK (ST_IsValid(geom))
);


CREATE INDEX IF NOT EXISTS idx_public_entity_users_auth
ON public_entity_users (auth_id)
WHERE is_active = true;


CREATE INDEX IF NOT EXISTS idx_public_entity_territories_geom
ON public_entity_territories
USING GIST (geom)
WHERE is_active = true;


-- ============================================================
-- 2. Versionamento fonti dati
-- ============================================================

CREATE TABLE IF NOT EXISTS data_layer_versions (
    layer_version text PRIMARY KEY,

    layer_name text NOT NULL,
    region text NOT NULL DEFAULT 'calabria',
    source_type text NOT NULL,

    source_dataset_name text NOT NULL,
    source_owner text NOT NULL,
    licensor text NOT NULL,

    license_name text NOT NULL,
    license_version text,
    license_url text,

    attribution_text text NOT NULL,
    endorsement_disclaimer text NOT NULL,

    acquisition_channel text NOT NULL DEFAULT 'official_request',
    acquisition_reference text,
    acquired_on date,
    source_publication_date date,

    is_active boolean NOT NULL DEFAULT true,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now()
);


INSERT INTO data_layer_versions (
    layer_version,
    layer_name,
    region,
    source_type,
    source_dataset_name,
    source_owner,
    licensor,
    license_name,
    license_version,
    license_url,
    attribution_text,
    endorsement_disclaimer,
    acquisition_channel,
    acquisition_reference,
    metadata
)
VALUES (
    'cut_calabria_v1',
    'CUT Calabria - aree olivicole operative',
    'calabria',
    'landcover_subtype',
    'Repertorio Cartografico Regione Calabria - CUT / oliveti',
    'Regione Calabria',
    'Regione Calabria',
    'Italian Open Data License',
    '2.0',
    'https://www.dati.gov.it/content/italian-open-data-license-v20',
    'Fonte dati territoriali: Regione Calabria - Repertorio Cartografico regionale. Elaborazione IntelCrop/Envioma su dati regionali.',
    'L’utilizzo dei dati non implica approvazione o validazione del servizio da parte della Regione Calabria.',
    'official_request',
    'Download/richiesta formale al Centro Cartografico Regionale; registrare data e versione del layer per ogni release.',
    jsonb_build_object(
        'forbidden_interpretation', ARRAY[
            'cultivar',
            'varieta_dedotta',
            'confine_catastale',
            'diagnosi_agronomica'
        ],
        'allowed_interpretation', ARRAY[
            'catalogo_territoriale',
            'tipologia_di_impianto',
            'monitoraggio_regionale',
            'priorita_di_ispezione'
        ]
    )
)
ON CONFLICT (layer_version) DO UPDATE
SET
    attribution_text = EXCLUDED.attribution_text,
    endorsement_disclaimer = EXCLUDED.endorsement_disclaimer,
    acquisition_reference = EXCLUDED.acquisition_reference,
    metadata = EXCLUDED.metadata;


CREATE OR REPLACE VIEW current_data_attribution_v1 AS
SELECT
    layer_version,
    attribution_text,
    endorsement_disclaimer,
    license_name,
    license_version,
    licensor,
    acquired_on,
    acquisition_reference
FROM data_layer_versions
WHERE is_active = true;


-- ============================================================
-- 3. Versionamento modello / seed / metodologia
-- ============================================================

CREATE TABLE IF NOT EXISTS model_versions (
    model_version text PRIMARY KEY,

    model_name text NOT NULL,
    model_family text NOT NULL,
    purpose text NOT NULL,

    source_layer_version text REFERENCES data_layer_versions(layer_version),

    status text NOT NULL DEFAULT 'experimental'
        CHECK (
            status IN (
                'experimental',
                'validated_seed',
                'operational',
                'archived'
            )
        ),

    training_sample_n integer,
    valid_label_n integer,

    tp numeric,
    fp numeric,
    fn numeric,
    tn numeric,

    precision_value numeric,
    recall_value numeric,
    specificity_value numeric,
    f1_score numeric,

    calibration_notes text,
    limitations text,

    created_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);


INSERT INTO model_versions (
    model_version,
    model_name,
    model_family,
    purpose,
    source_layer_version,
    status,
    training_sample_n,
    valid_label_n,
    tp,
    fp,
    fn,
    tn,
    precision_value,
    recall_value,
    specificity_value,
    f1_score,
    calibration_notes,
    limitations,
    metadata
)
VALUES
(
    'olive_identity_reference_seed_v1',
    'Olive identity reference seed v1',
    'rule_based_qc_seed',
    'Seed di riferimento visivo, geometrico, contestuale e spettrale per identità olivicola regionale.',
    'cut_calabria_v1',
    'validated_seed',
    199,
    198,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    'Deriva da landcover_olive_pure_baseline_v1. Usare come riferimento di identità, non come baseline statistica per anomalie.',
    'Non rappresenta tutte le aree olivicole regionali. Non identifica cultivar. Non è un confine catastale.',
    jsonb_build_object(
        'source_view', 'landcover_olive_pure_baseline_v1',
        'features', 70,
        'area_ha_approx', 322.70
    )
),
(
    'olive_identity_reference_strict_seed_v1',
    'Olive identity strict reference seed v1',
    'conservative_rule_based_qc_seed',
    'Seed conservativo ad alta precisione per calibrazione dello score regionale.',
    'cut_calabria_v1',
    'validated_seed',
    199,
    198,
    47,
    9,
    66,
    33,
    0.8393,
    0.4159,
    0.7857,
    0.5562,
    'Regola strict selezionata per massimizzare affidabilità e specificità. È un riferimento conservativo.',
    'Recall bassa: non va usato come classificatore completo. Le aree non strict non sono automaticamente negative.',
    jsonb_build_object(
        'source_view', 'landcover_olive_pure_baseline_strict_seed_v1',
        'features', 47,
        'area_ha_approx', 167.80
    )
)
ON CONFLICT (model_version) DO UPDATE
SET
    calibration_notes = EXCLUDED.calibration_notes,
    limitations = EXCLUDED.limitations,
    metadata = EXCLUDED.metadata;


CREATE TABLE IF NOT EXISTS methodology_versions (
    methodology_version text PRIMARY KEY,

    title text NOT NULL,
    document_status text NOT NULL DEFAULT 'draft'
        CHECK (
            document_status IN (
                'draft',
                'reviewed',
                'released',
                'archived'
            )
        ),

    source_layer_version text REFERENCES data_layer_versions(layer_version),
    model_version text REFERENCES model_versions(model_version),

    methodology_date date NOT NULL DEFAULT CURRENT_DATE,

    summary text NOT NULL,
    limitations text NOT NULL,

    pdf_path text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now()
);


INSERT INTO methodology_versions (
    methodology_version,
    title,
    document_status,
    source_layer_version,
    model_version,
    summary,
    limitations,
    metadata
)
VALUES (
    'intellcrop_calabria_methodology_v1_draft',
    'IntelCrop Calabria - metodologia catalogo territoriale v1',
    'draft',
    'cut_calabria_v1',
    'olive_identity_reference_strict_seed_v1',
    'Metodologia per catalogo territoriale regionale basato su QC geometrico, visuale, contestuale, spettrale e successivo score di affidabilità regionale.',
    'Il sistema supporta monitoraggio e priorità di ispezione. Non identifica cultivar, non certifica confini catastali, non formula diagnosi fitosanitarie o causali.',
    jsonb_build_object(
        'required_sections', ARRAY[
            'fonte_dati_regionale',
            'licenza_e_attribuzione',
            'geometric_qc',
            'visual_qc',
            'urban_context_qc',
            'spectral_qc',
            'strict_seed',
            'regional_reliability_score',
            'validazione_spaziale',
            'limiti_interpretativi',
            'audit_trail'
        ]
    )
)
ON CONFLICT (methodology_version) DO UPDATE
SET
    summary = EXCLUDED.summary,
    limitations = EXCLUDED.limitations,
    metadata = EXCLUDED.metadata;


-- ============================================================
-- 4. Alias metodologici per evitare collisione "baseline"
-- ============================================================

CREATE OR REPLACE VIEW olive_identity_reference_seed_v1 AS
SELECT *
FROM landcover_olive_pure_baseline_v1;


CREATE OR REPLACE VIEW olive_identity_reference_strict_seed_v1 AS
SELECT *
FROM landcover_olive_pure_baseline_strict_seed_v1;


-- ============================================================
-- 5. Catalogo fondazione: NON è ancora area_catalog_scored_v1
-- ============================================================

CREATE OR REPLACE VIEW area_catalog_foundation_v1 AS
SELECT
    g.id::text AS area_id,

    g.subtype_id AS technical_subtype_id,

    CASE
        WHEN g.subtype_id = 'olive_pure'
            THEN 'Coltura arborea permanente'
        ELSE 'Area agricola'
    END AS public_area_type,

    CASE
        WHEN g.subtype_id = 'olive_pure'
            THEN 'Oliveto da catalogo territoriale regionale'
        ELSE 'Area agricola da catalogo territoriale regionale'
    END AS technical_description,

    g.source_layer_version,
    g.qc_version AS geometric_qc_version,
    g.qc_class AS geometric_qc_class,

    g.area_ha,
    g.compactness,
    g.n_points,
    g.qc_score,

    EXISTS (
        SELECT 1
        FROM olive_identity_reference_seed_v1 b
        WHERE b.id = g.id
    ) AS identity_reference_match,

    EXISTS (
        SELECT 1
        FROM olive_identity_reference_strict_seed_v1 s
        WHERE s.id = g.id
    ) AS strict_reference_match,

    CASE
        WHEN EXISTS (
            SELECT 1
            FROM olive_identity_reference_strict_seed_v1 s
            WHERE s.id = g.id
        )
            THEN 'Alta affidabilità'
        WHEN EXISTS (
            SELECT 1
            FROM olive_identity_reference_seed_v1 b
            WHERE b.id = g.id
        )
            THEN 'Compatibile'
        ELSE 'Da classificare'
    END AS preliminary_catalog_label,

    CASE
        WHEN EXISTS (
            SELECT 1
            FROM olive_identity_reference_strict_seed_v1 s
            WHERE s.id = g.id
        )
            THEN 'strict_reference'
        WHEN EXISTS (
            SELECT 1
            FROM olive_identity_reference_seed_v1 b
            WHERE b.id = g.id
        )
            THEN 'identity_reference'
        ELSE 'available_unscored'
    END AS preliminary_catalog_status,

    FALSE AS is_regionally_scored,

    NULL::numeric AS regional_reliability_score,
    NULL::text AS regional_reliability_class,
    NULL::text AS regional_reliability_model_version,

    ST_PointOnSurface(g.geom) AS label_point_geom,
    ST_X(ST_PointOnSurface(g.geom)) AS centroid_lon,
    ST_Y(ST_PointOnSurface(g.geom)) AS centroid_lat,

    g.geom

FROM landcover_olive_pure_high_confidence_v2 g
WHERE g.source_layer_version = 'cut_calabria_v1'
  AND g.qc_version = 'olive_pure_geom_qc_v2'
  AND g.qc_class = 'high_confidence';


CREATE INDEX IF NOT EXISTS idx_landcover_high_conf_v2_geom
ON landcover_olive_pure_high_confidence_v2
USING GIST (geom);


-- ============================================================
-- 6. Catalogo filtrato per territorio ente
--    Da usare nei futuri GET /areas e /areas/{area_id}
-- ============================================================

CREATE OR REPLACE VIEW public_entity_area_catalog_v1 AS
SELECT
    e.ente_id,
    e.ente_code,
    e.ente_name,
    e.ente_type,

    c.area_id,
    c.technical_subtype_id,
    c.public_area_type,
    c.technical_description,

    c.source_layer_version,
    c.geometric_qc_version,
    c.geometric_qc_class,

    c.area_ha,
    c.compactness,
    c.n_points,
    c.qc_score,

    c.identity_reference_match,
    c.strict_reference_match,
    c.preliminary_catalog_label,
    c.preliminary_catalog_status,

    c.is_regionally_scored,
    c.regional_reliability_score,
    c.regional_reliability_class,
    c.regional_reliability_model_version,

    ROUND(
        (
            ST_Area(
                ST_Intersection(c.geom, t.geom)::geography
            )
            / NULLIF(ST_Area(c.geom::geography), 0)
        )::numeric * 100,
        2
    ) AS territory_overlap_percent,

    c.centroid_lon,
    c.centroid_lat,
    c.label_point_geom,
    c.geom

FROM public_entities e
JOIN public_entity_territories t
  ON t.ente_id = e.ente_id
 AND t.is_active = true
JOIN area_catalog_foundation_v1 c
  ON c.geom && t.geom
 AND ST_Intersects(c.geom, t.geom)
WHERE e.status = 'active';


-- ============================================================
-- 7. Audit trail contrattuale / istituzionale
-- ============================================================

CREATE TABLE IF NOT EXISTS analysis_execution_registry (
    execution_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    request_id text UNIQUE,

    ente_id uuid REFERENCES public_entities(ente_id),
    auth_id text,

    analysis_scope text NOT NULL DEFAULT 'catalog_batch'
        CHECK (
            analysis_scope IN (
                'single_catalog_area',
                'catalog_batch',
                'legacy_field_geometry'
            )
        ),

    area_ids text[] NOT NULL DEFAULT ARRAY[]::text[],

    analysis_profile text,

    source_layer_version text REFERENCES data_layer_versions(layer_version),
    model_version text REFERENCES model_versions(model_version),
    methodology_version text REFERENCES methodology_versions(methodology_version),

    satellite_collection text,
    satellite_period_start date,
    satellite_period_end date,

    status text NOT NULL DEFAULT 'created'
        CHECK (
            status IN (
                'created',
                'running',
                'completed',
                'failed',
                'cancelled'
            )
        ),

    contract_valid boolean,

    priority_percent numeric,
    total_area_ha numeric,

    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);


CREATE INDEX IF NOT EXISTS idx_analysis_execution_registry_ente
ON analysis_execution_registry (ente_id, created_at DESC);


CREATE INDEX IF NOT EXISTS idx_analysis_execution_registry_request
ON analysis_execution_registry (request_id);


-- Aggiunta non distruttiva se nel progetto esistono già tabelle fields/analyses/jobs.
ALTER TABLE IF EXISTS fields
ADD COLUMN IF NOT EXISTS ente_id uuid REFERENCES public_entities(ente_id);

ALTER TABLE IF EXISTS analyses
ADD COLUMN IF NOT EXISTS ente_id uuid REFERENCES public_entities(ente_id);

ALTER TABLE IF EXISTS analysis_jobs
ADD COLUMN IF NOT EXISTS ente_id uuid REFERENCES public_entities(ente_id);

ALTER TABLE IF EXISTS fields
ADD COLUMN IF NOT EXISTS source_layer_version text REFERENCES data_layer_versions(layer_version);

ALTER TABLE IF EXISTS analyses
ADD COLUMN IF NOT EXISTS source_layer_version text REFERENCES data_layer_versions(layer_version);

ALTER TABLE IF EXISTS analysis_jobs
ADD COLUMN IF NOT EXISTS source_layer_version text REFERENCES data_layer_versions(layer_version);


-- ============================================================
-- 8. Feedback minimo per calibrazione futura
-- ============================================================

CREATE TABLE IF NOT EXISTS batch_analysis_feedback (
    feedback_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    execution_id uuid REFERENCES analysis_execution_registry(execution_id)
        ON DELETE SET NULL,

    ente_id uuid REFERENCES public_entities(ente_id),
    auth_id text,

    area_id text,

    feedback_value text NOT NULL
        CHECK (feedback_value IN ('yes', 'no', 'unknown')),

    feedback_note text,

    created_at timestamptz NOT NULL DEFAULT now()
);


CREATE INDEX IF NOT EXISTS idx_batch_analysis_feedback_area
ON batch_analysis_feedback (area_id, created_at DESC);


-- ============================================================
-- 9. Predisposizione export GIS / WMS / WFS
-- ============================================================

CREATE TABLE IF NOT EXISTS gis_export_layers (
    export_layer_id text PRIMARY KEY,

    layer_view_name text NOT NULL,
    public_layer_name text NOT NULL,

    supported_formats text[] NOT NULL DEFAULT ARRAY['geojson']::text[],
    requires_ente_scope boolean NOT NULL DEFAULT true,

    source_layer_version text REFERENCES data_layer_versions(layer_version),
    model_version text REFERENCES model_versions(model_version),
    methodology_version text REFERENCES methodology_versions(methodology_version),

    attribution_text text NOT NULL,
    disclaimer_text text NOT NULL,

    is_active boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);


INSERT INTO gis_export_layers (
    export_layer_id,
    layer_view_name,
    public_layer_name,
    supported_formats,
    requires_ente_scope,
    source_layer_version,
    model_version,
    methodology_version,
    attribution_text,
    disclaimer_text,
    metadata
)
VALUES (
    'area_catalog_foundation_v1',
    'public_entity_area_catalog_v1',
    'IntelCrop Calabria - catalogo territoriale aree disponibili',
    ARRAY['geojson', 'shp', 'wms', 'wfs']::text[],
    true,
    'cut_calabria_v1',
    'olive_identity_reference_strict_seed_v1',
    'intellcrop_calabria_methodology_v1_draft',
    'Fonte dati territoriali: Regione Calabria - Repertorio Cartografico regionale. Elaborazione IntelCrop/Envioma su dati regionali.',
    'Il layer è un catalogo territoriale operativo e non rappresenta confini catastali, cultivar o diagnosi agronomiche. L’utilizzo non implica approvazione da parte della Regione Calabria.',
    jsonb_build_object(
        'planned_api_endpoint', '/areas/export?format=geojson|shp',
        'planned_ogc_services', ARRAY['WMS', 'WFS'],
        'tenant_filter_required', true
    )
)
ON CONFLICT (export_layer_id) DO UPDATE
SET
    supported_formats = EXCLUDED.supported_formats,
    attribution_text = EXCLUDED.attribution_text,
    disclaimer_text = EXCLUDED.disclaimer_text,
    metadata = EXCLUDED.metadata;


CREATE OR REPLACE VIEW area_catalog_export_metadata_v1 AS
SELECT
    gel.export_layer_id,
    gel.layer_view_name,
    gel.public_layer_name,
    gel.supported_formats,
    gel.requires_ente_scope,
    gel.attribution_text,
    gel.disclaimer_text,
    d.license_name,
    d.license_version,
    d.licensor,
    d.acquisition_reference,
    m.model_name,
    m.model_version,
    mv.title AS methodology_title,
    mv.methodology_version
FROM gis_export_layers gel
LEFT JOIN data_layer_versions d
  ON d.layer_version = gel.source_layer_version
LEFT JOIN model_versions m
  ON m.model_version = gel.model_version
LEFT JOIN methodology_versions mv
  ON mv.methodology_version = gel.methodology_version
WHERE gel.is_active = true;


-- ============================================================
-- 10. View controllo stato
-- ============================================================

CREATE OR REPLACE VIEW public_sector_catalog_readiness_v1 AS
SELECT
    (SELECT COUNT(*) FROM public_entities) AS n_entities,
    (SELECT COUNT(*) FROM public_entities WHERE status = 'active') AS n_active_entities,
    (SELECT COUNT(*) FROM public_entity_territories WHERE is_active = true) AS n_active_territories,

    (SELECT COUNT(*) FROM area_catalog_foundation_v1) AS n_catalog_foundation_areas,

    (
        SELECT COUNT(*)
        FROM area_catalog_foundation_v1
        WHERE strict_reference_match = true
    ) AS n_strict_reference_areas,

    (
        SELECT COUNT(*)
        FROM area_catalog_foundation_v1
        WHERE identity_reference_match = true
    ) AS n_identity_reference_areas,

    (
        SELECT COUNT(*)
        FROM data_layer_versions
        WHERE is_active = true
    ) AS n_active_data_versions,

    (
        SELECT COUNT(*)
        FROM model_versions
        WHERE status IN ('validated_seed', 'operational')
    ) AS n_validated_model_versions,

    (
        SELECT COUNT(*)
        FROM gis_export_layers
        WHERE is_active = true
    ) AS n_active_export_layers;