CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS landcover_subtypes (
    id TEXT PRIMARY KEY,
    label_it TEXT NOT NULL,
    crop TEXT NOT NULL,
    region TEXT NOT NULL,
    source_layer_version TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO landcover_subtypes (
    id,
    label_it,
    crop,
    region,
    source_layer_version,
    active
)
VALUES
    ('olive_pure', 'Oliveto puro', 'olive', 'calabria', 'cut_calabria_v1', TRUE),
    ('olive_citrus', 'Oliveto consociato con agrumi', 'olive', 'calabria', 'cut_calabria_v1', FALSE),
    ('olive_vine', 'Oliveto consociato con vite', 'olive', 'calabria', 'cut_calabria_v1', FALSE),
    ('olive_generic_calabria', 'Oliveto generico Calabria', 'olive', 'calabria', 'fallback_v1', TRUE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS fields (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cliente_id TEXT NOT NULL DEFAULT 'local_dev',
    geom geometry(MultiPolygon, 4326) NOT NULL,
    area_ha DOUBLE PRECISION NOT NULL CHECK (area_ha > 0),
    landcover_subtype TEXT NOT NULL DEFAULT 'olive_generic_calabria'
        REFERENCES landcover_subtypes(id),
    subtype_confidence TEXT NOT NULL DEFAULT 'low'
        CHECK (subtype_confidence IN ('high', 'medium', 'low', 'unknown')),
    subtype_layer_version TEXT NOT NULL DEFAULT 'fallback_v1',
    cultivar_dichiarata TEXT NULL,
    data_registrazione TIMESTAMPTZ NOT NULL DEFAULT now(),
    canale_notifica TEXT NULL,
    attivo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_fields_geom
ON fields
USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_fields_cliente_id
ON fields (cliente_id);

CREATE INDEX IF NOT EXISTS idx_fields_subtype
ON fields (landcover_subtype);

CREATE TABLE IF NOT EXISTS field_covariates (
    field_id UUID PRIMARY KEY REFERENCES fields(id) ON DELETE CASCADE,
    elevazione_media DOUBLE PRECISION NULL,
    pendenza_media DOUBLE PRECISION NULL,
    tri DOUBLE PRECISION NULL,
    tpi DOUBLE PRECISION NULL,
    awc_suolo DOUBLE PRECISION NULL,
    tessitura_suolo TEXT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    field_id UUID REFERENCES fields(id) ON DELETE SET NULL,
    job_id TEXT NULL,
    cliente_id TEXT NOT NULL DEFAULT 'local_dev',
    data_acquisizione_s2 DATE NULL,
    data_acquisizione_s1 DATE NULL,
    indici_json JSONB NULL,
    esito_confronto_a JSONB NULL,
    esito_confronto_b JSONB NULL,
    bilancio_idrico_stimato JSONB NULL,
    gdd_cumulato_stagione DOUBLE PRECISION NULL,
    baseline_version TEXT NULL,
    analysis_result_json JSONB NULL,
    analysis_status TEXT NULL,
    priority_percent DOUBLE PRECISION NULL,
    total_area_ha DOUBLE PRECISION NULL,
    contract_valid BOOLEAN NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analyses_field_id
ON analyses (field_id);

CREATE INDEX IF NOT EXISTS idx_analyses_cliente_id
ON analyses (cliente_id);

CREATE INDEX IF NOT EXISTS idx_analyses_created_at
ON analyses (created_at DESC);

CREATE TABLE IF NOT EXISTS landcover_baseline_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subtype_id TEXT NOT NULL REFERENCES landcover_subtypes(id),
    finestra_temporale TEXT NOT NULL,
    indice TEXT NOT NULL,
    mediana DOUBLE PRECISION NOT NULL,
    mad DOUBLE PRECISION NOT NULL,
    matrice_covarianza_json JSONB NULL,
    n_pixel BIGINT NOT NULL CHECK (n_pixel >= 0),
    baseline_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subtype_id, finestra_temporale, indice, baseline_version)
);

CREATE INDEX IF NOT EXISTS idx_baseline_subtype
ON landcover_baseline_stats (subtype_id);

CREATE INDEX IF NOT EXISTS idx_baseline_version
ON landcover_baseline_stats (baseline_version);

CREATE TABLE IF NOT EXISTS alert_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    field_id UUID NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
    analysis_id UUID REFERENCES analyses(id) ON DELETE SET NULL,
    severita_precedente TEXT NULL,
    severita_nuova TEXT NOT NULL,
    inviato_at TIMESTAMPTZ NULL,
    canale TEXT NULL,
    esito_riscontrato TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alert_log_field_id
ON alert_log (field_id);

CREATE INDEX IF NOT EXISTS idx_alert_log_created_at
ON alert_log (created_at DESC);

CREATE TABLE IF NOT EXISTS weather_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cella_id TEXT NOT NULL,
    data DATE NOT NULL,
    precipitazione_mm DOUBLE PRECISION NULL,
    et0_mm DOUBLE PRECISION NULL,
    temp_media DOUBLE PRECISION NULL,
    source_version TEXT NOT NULL DEFAULT 'v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cella_id, data, source_version)
);

CREATE INDEX IF NOT EXISTS idx_weather_cache_cell_date
ON weather_cache (cella_id, data);