BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;


CREATE TABLE IF NOT EXISTS regional_spectral_backfill_runs (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    run_version text NOT NULL,
    spectral_qc_version text NOT NULL,
    source_pool_version text NOT NULL,
    source_pending_view text NOT NULL,

    satellite_collection text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    cloud_threshold double precision NOT NULL,

    algorithm_version text NOT NULL,
    batch_size integer NOT NULL,

    status text NOT NULL DEFAULT 'created',

    initial_area_n integer NOT NULL,
    pending_area_n integer NOT NULL,
    running_area_n integer NOT NULL DEFAULT 0,
    completed_area_n integer NOT NULL DEFAULT 0,
    failed_area_n integer NOT NULL DEFAULT 0,

    git_commit text,
    created_by text,

    created_at timestamp with time zone
        NOT NULL DEFAULT now(),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone
        NOT NULL DEFAULT now(),

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT regional_spectral_backfill_runs_version_uk
        UNIQUE (run_version),

    CONSTRAINT regional_spectral_backfill_runs_status_ck
        CHECK (
            status IN (
                'created',
                'ready',
                'running',
                'paused',
                'completed',
                'completed_with_failures',
                'failed',
                'cancelled'
            )
        ),

    CONSTRAINT regional_spectral_backfill_runs_period_ck
        CHECK (period_end >= period_start),

    CONSTRAINT regional_spectral_backfill_runs_cloud_ck
        CHECK (
            cloud_threshold >= 0
            AND cloud_threshold <= 100
        ),

    CONSTRAINT regional_spectral_backfill_runs_batch_ck
        CHECK (batch_size > 0),

    CONSTRAINT regional_spectral_backfill_runs_counts_ck
        CHECK (
            initial_area_n >= 0
            AND pending_area_n >= 0
            AND running_area_n >= 0
            AND completed_area_n >= 0
            AND failed_area_n >= 0
        )
);


CREATE TABLE IF NOT EXISTS regional_spectral_backfill_batches (
    batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id uuid NOT NULL
        REFERENCES regional_spectral_backfill_runs(run_id)
        ON DELETE CASCADE,

    batch_number integer NOT NULL,

    spatial_validation_zone text,
    expected_area_n integer NOT NULL,

    status text NOT NULL DEFAULT 'pending',
    attempt_count integer NOT NULL DEFAULT 0,

    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone
        NOT NULL DEFAULT now(),

    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT regional_spectral_backfill_batches_run_number_uk
        UNIQUE (run_id, batch_number),

    CONSTRAINT regional_spectral_backfill_batches_status_ck
        CHECK (
            status IN (
                'pending',
                'running',
                'completed',
                'failed',
                'cancelled'
            )
        ),

    CONSTRAINT regional_spectral_backfill_batches_number_ck
        CHECK (batch_number > 0),

    CONSTRAINT regional_spectral_backfill_batches_expected_ck
        CHECK (expected_area_n > 0),

    CONSTRAINT regional_spectral_backfill_batches_attempt_ck
        CHECK (attempt_count >= 0)
);


CREATE TABLE IF NOT EXISTS regional_spectral_backfill_items (
    item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id uuid NOT NULL
        REFERENCES regional_spectral_backfill_runs(run_id)
        ON DELETE CASCADE,

    batch_id uuid NOT NULL
        REFERENCES regional_spectral_backfill_batches(batch_id)
        ON DELETE CASCADE,

    area_id text NOT NULL,
    source_geometry_id uuid NOT NULL,

    subtype_id text NOT NULL,
    source_layer_version text NOT NULL,
    spatial_validation_zone text NOT NULL,
    current_high_confidence_v2 boolean NOT NULL,

    area_ha_raw double precision NOT NULL,
    perimeter_m_raw double precision NOT NULL,
    compactness_raw double precision NOT NULL,
    n_points integer NOT NULL,
    n_parts integer NOT NULL,

    status text NOT NULL DEFAULT 'pending',
    attempt_count integer NOT NULL DEFAULT 0,

    claimed_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone
        NOT NULL DEFAULT now(),

    spectral_qc_row_id uuid,
    error_code text,
    error_message text,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT regional_spectral_backfill_items_run_area_uk
        UNIQUE (run_id, area_id),

    CONSTRAINT regional_spectral_backfill_items_run_geometry_uk
        UNIQUE (run_id, source_geometry_id),

    CONSTRAINT regional_spectral_backfill_items_status_ck
        CHECK (
            status IN (
                'pending',
                'running',
                'completed',
                'failed',
                'cancelled'
            )
        ),

    CONSTRAINT regional_spectral_backfill_items_attempt_ck
        CHECK (attempt_count >= 0),

    CONSTRAINT regional_spectral_backfill_items_area_ck
        CHECK (area_ha_raw >= 0.5),

    CONSTRAINT regional_spectral_backfill_items_perimeter_ck
        CHECK (perimeter_m_raw > 0),

    CONSTRAINT regional_spectral_backfill_items_compactness_ck
        CHECK (
            compactness_raw >= 0
            AND compactness_raw <= 1
        ),

    CONSTRAINT regional_spectral_backfill_items_points_ck
        CHECK (n_points > 0),

    CONSTRAINT regional_spectral_backfill_items_parts_ck
        CHECK (n_parts > 0)
);


CREATE INDEX IF NOT EXISTS
    regional_spectral_backfill_batches_run_status_idx
ON regional_spectral_backfill_batches (
    run_id,
    status,
    batch_number
);


CREATE INDEX IF NOT EXISTS
    regional_spectral_backfill_items_run_status_idx
ON regional_spectral_backfill_items (
    run_id,
    status,
    batch_id
);


CREATE INDEX IF NOT EXISTS
    regional_spectral_backfill_items_geometry_idx
ON regional_spectral_backfill_items (
    source_geometry_id
);


CREATE INDEX IF NOT EXISTS
    regional_spectral_backfill_items_zone_status_idx
ON regional_spectral_backfill_items (
    spatial_validation_zone,
    status
);


CREATE OR REPLACE VIEW
regional_spectral_backfill_run_summary_v1 AS
SELECT
    r.run_id,
    r.run_version,
    r.spectral_qc_version,
    r.source_pool_version,
    r.satellite_collection,
    r.period_start,
    r.period_end,
    r.cloud_threshold,
    r.algorithm_version,
    r.batch_size,
    r.status,

    r.initial_area_n,

    COUNT(i.item_id) AS snapshot_area_n,

    COUNT(i.item_id) FILTER (
        WHERE i.status = 'pending'
    ) AS pending_area_n,

    COUNT(i.item_id) FILTER (
        WHERE i.status = 'running'
    ) AS running_area_n,

    COUNT(i.item_id) FILTER (
        WHERE i.status = 'completed'
    ) AS completed_area_n,

    COUNT(i.item_id) FILTER (
        WHERE i.status = 'failed'
    ) AS failed_area_n,

    COUNT(DISTINCT b.batch_id) AS batch_n,

    COUNT(DISTINCT b.batch_id) FILTER (
        WHERE b.status = 'completed'
    ) AS completed_batch_n,

    CASE
        WHEN COUNT(i.item_id) = 0 THEN 0
        ELSE ROUND(
            100.0
            * COUNT(i.item_id) FILTER (
                WHERE i.status = 'completed'
            )
            / COUNT(i.item_id),
            2
        )
    END AS progress_percent,

    r.created_at,
    r.started_at,
    r.completed_at,
    r.updated_at,
    r.git_commit,
    r.created_by,
    r.metadata

FROM regional_spectral_backfill_runs r

LEFT JOIN regional_spectral_backfill_batches b
    ON b.run_id = r.run_id

LEFT JOIN regional_spectral_backfill_items i
    ON i.run_id = r.run_id

GROUP BY
    r.run_id;


COMMENT ON TABLE regional_spectral_backfill_runs IS
'Versioned registry of regional Sentinel-2 spectral backfill executions. Each run freezes its input population and processing configuration.';

COMMENT ON TABLE regional_spectral_backfill_batches IS
'Versioned processing batches belonging to one regional spectral backfill run.';

COMMENT ON TABLE regional_spectral_backfill_items IS
'Frozen per-area processing queue for a regional spectral backfill run. Items remain auditable even after the dynamic pending view changes.';

COMMENT ON VIEW regional_spectral_backfill_run_summary_v1 IS
'Aggregated progress and status for each regional spectral backfill run.';


COMMIT;
