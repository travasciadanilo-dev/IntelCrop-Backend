CREATE TABLE IF NOT EXISTS analysis_jobs_v1 (
    job_id TEXT PRIMARY KEY,

    entity_id TEXT NOT NULL
        REFERENCES app_entities_v1(entity_id),

    status TEXT NOT NULL
        CHECK (
            status IN (
                'queued',
                'processing',
                'done',
                'error',
                'cancelled'
            )
        ),

    current_step TEXT,
    progress_pct DOUBLE PRECISION
        CHECK (
            progress_pct IS NULL
            OR (
                progress_pct >= 0
                AND progress_pct <= 100
            )
        ),

    analysis_profile TEXT NOT NULL
        DEFAULT 'catalog_screening_v1',

    area_ids JSONB NOT NULL,
    area_snapshot JSONB NOT NULL,

    result JSONB,
    error JSONB,

    request_version TEXT NOT NULL
        DEFAULT 'catalog_batch_job_v1',

    catalog_version TEXT NOT NULL
        DEFAULT 'area_catalog_v1_diagnostic',

    model_version TEXT NOT NULL
        DEFAULT 'regional_reliability_score_exp_v3',

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    CONSTRAINT analysis_jobs_v1_area_ids_array
        CHECK (jsonb_typeof(area_ids) = 'array'),

    CONSTRAINT analysis_jobs_v1_area_snapshot_array
        CHECK (jsonb_typeof(area_snapshot) = 'array'),

    CONSTRAINT analysis_jobs_v1_area_count
        CHECK (
            jsonb_array_length(area_ids) BETWEEN 1 AND 5
        )
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_v1_entity_created
    ON analysis_jobs_v1 (
        entity_id,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_v1_status_created
    ON analysis_jobs_v1 (
        status,
        created_at
    );

COMMENT ON TABLE analysis_jobs_v1 IS
'Persistent analysis jobs created from entity-scoped catalog areas.';

COMMENT ON COLUMN analysis_jobs_v1.area_snapshot IS
'Immutable catalog-area metadata snapshot captured when the job is created.';

COMMENT ON COLUMN analysis_jobs_v1.request_version IS
'Version of the API request contract used to create the job.';
