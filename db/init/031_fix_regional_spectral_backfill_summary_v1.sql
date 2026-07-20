BEGIN;

DROP VIEW IF EXISTS
regional_spectral_backfill_run_summary_v1;

CREATE VIEW
regional_spectral_backfill_run_summary_v1 AS

WITH batch_summary AS (
    SELECT
        run_id,
        COUNT(*) AS batch_n,
        COUNT(*) FILTER (
            WHERE status = 'pending'
        ) AS pending_batch_n,
        COUNT(*) FILTER (
            WHERE status = 'running'
        ) AS running_batch_n,
        COUNT(*) FILTER (
            WHERE status = 'completed'
        ) AS completed_batch_n,
        COUNT(*) FILTER (
            WHERE status = 'failed'
        ) AS failed_batch_n,
        COUNT(*) FILTER (
            WHERE status = 'cancelled'
        ) AS cancelled_batch_n
    FROM regional_spectral_backfill_batches
    GROUP BY run_id
),

item_summary AS (
    SELECT
        run_id,
        COUNT(*) AS snapshot_area_n,
        COUNT(*) FILTER (
            WHERE status = 'pending'
        ) AS pending_area_n,
        COUNT(*) FILTER (
            WHERE status = 'running'
        ) AS running_area_n,
        COUNT(*) FILTER (
            WHERE status = 'completed'
        ) AS completed_area_n,
        COUNT(*) FILTER (
            WHERE status = 'failed'
        ) AS failed_area_n,
        COUNT(*) FILTER (
            WHERE status = 'cancelled'
        ) AS cancelled_area_n
    FROM regional_spectral_backfill_items
    GROUP BY run_id
)

SELECT
    r.run_id,
    r.run_version,
    r.spectral_qc_version,
    r.source_pool_version,
    r.source_pending_view,

    r.satellite_collection,
    r.period_start,
    r.period_end,
    r.cloud_threshold,

    r.algorithm_version,
    r.batch_size,
    r.status,

    r.initial_area_n,

    COALESCE(i.snapshot_area_n, 0)
        AS snapshot_area_n,

    COALESCE(i.pending_area_n, 0)
        AS pending_area_n,

    COALESCE(i.running_area_n, 0)
        AS running_area_n,

    COALESCE(i.completed_area_n, 0)
        AS completed_area_n,

    COALESCE(i.failed_area_n, 0)
        AS failed_area_n,

    COALESCE(i.cancelled_area_n, 0)
        AS cancelled_area_n,

    COALESCE(b.batch_n, 0)
        AS batch_n,

    COALESCE(b.pending_batch_n, 0)
        AS pending_batch_n,

    COALESCE(b.running_batch_n, 0)
        AS running_batch_n,

    COALESCE(b.completed_batch_n, 0)
        AS completed_batch_n,

    COALESCE(b.failed_batch_n, 0)
        AS failed_batch_n,

    COALESCE(b.cancelled_batch_n, 0)
        AS cancelled_batch_n,

    CASE
        WHEN COALESCE(i.snapshot_area_n, 0) = 0
            THEN 0::numeric
        ELSE ROUND(
            100.0
            * COALESCE(i.completed_area_n, 0)
            / i.snapshot_area_n,
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

LEFT JOIN batch_summary b
    ON b.run_id = r.run_id

LEFT JOIN item_summary i
    ON i.run_id = r.run_id;


COMMENT ON VIEW
regional_spectral_backfill_run_summary_v1 IS
'Accurate aggregated progress for each regional spectral backfill run. Batch and item counts are computed independently to avoid row multiplication.';

COMMIT;
