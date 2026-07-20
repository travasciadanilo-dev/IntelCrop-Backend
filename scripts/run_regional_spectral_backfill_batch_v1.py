import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import ee
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato.")


RUN_VERSION = os.getenv(
    "REGIONAL_SPECTRAL_BACKFILL_RUN_VERSION",
    "regional_spectral_backfill_v1_20260710",
)

SPECTRAL_QC_VERSION = "olive_spectral_qc_v1"

SOURCE_VIEW_LABEL = (
    "regional_spectral_backfill_v1_20260710"
)

GEE_SUB_BATCH_SIZE = int(
    os.getenv(
        "REGIONAL_SPECTRAL_GEE_SUB_BATCH_SIZE",
        "10",
    )
)

RETRY_FAILED_BATCHES = (
    os.getenv(
        "REGIONAL_SPECTRAL_RETRY_FAILED",
        "0",
    )
    == "1"
)

MAX_BATCHES_PER_RUN = int(
    os.getenv(
        "REGIONAL_SPECTRAL_MAX_BATCHES",
        "1",
    )
)

EE_REQUEST_TIMEOUT_SECONDS = int(
    os.getenv(
        "REGIONAL_SPECTRAL_EE_TIMEOUT_SECONDS",
        "300",
    )
)

EE_MAX_RETRIES = int(
    os.getenv(
        "REGIONAL_SPECTRAL_EE_MAX_RETRIES",
        "2",
    )
)


if GEE_SUB_BATCH_SIZE <= 0:
    raise RuntimeError(
        "REGIONAL_SPECTRAL_GEE_SUB_BATCH_SIZE "
        "deve essere maggiore di zero."
    )

if MAX_BATCHES_PER_RUN <= 0:
    raise RuntimeError(
        "REGIONAL_SPECTRAL_MAX_BATCHES "
        "deve essere maggiore di zero."
    )

if EE_REQUEST_TIMEOUT_SECONDS <= 0:
    raise RuntimeError(
        "REGIONAL_SPECTRAL_EE_TIMEOUT_SECONDS "
        "deve essere maggiore di zero."
    )

if not 0 <= EE_MAX_RETRIES < 100:
    raise RuntimeError(
        "REGIONAL_SPECTRAL_EE_MAX_RETRIES "
        "deve essere compreso tra 0 e 99."
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.build_olive_spectral_qc import (  # noqa: E402
    init_ee,
    reduce_spectral_metrics,
)


def chunks(
    values: list[Any],
    size: int,
) -> list[list[Any]]:
    return [
        values[index:index + size]
        for index in range(0, len(values), size)
    ]


# ================================================================
# FUNZIONE DI UTILITÀ PER ERRORI DI INFRASTRUTTURA
# ================================================================

def is_infrastructure_error(exc: Exception) -> bool:
    message = str(exc).lower()

    patterns = (
        "failed to resolve",
        "getaddrinfo failed",
        "name resolution",
        "max retries exceeded",
        "connection aborted",
        "connection reset",
        "connection refused",
        "temporary failure in name resolution",
        "earthengine.googleapis.com",
    )

    return any(
        pattern in message
        for pattern in patterns
    )


def get_run(
    cursor: Any,
) -> tuple[str, str]:
    cursor.execute(
        """
        SELECT
            run_id,
            status
        FROM regional_spectral_backfill_runs
        WHERE run_version = %s;
        """,
        (RUN_VERSION,),
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            f"Run non trovato: {RUN_VERSION}"
        )

    run_id, status = row

    if status not in (
        "ready",
        "running",
        "paused",
        "completed_with_failures",
    ):
        raise RuntimeError(
            "Stato run non processabile: "
            f"{status}"
        )

    return str(run_id), status


def claim_next_batch(
    conn: Any,
    run_id: str,
) -> dict[str, Any] | None:
    allowed_statuses = ["pending"]

    if RETRY_FAILED_BATCHES:
        allowed_statuses.append("failed")

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                batch_id,
                batch_number,
                spatial_validation_zone,
                expected_area_n,
                status,
                attempt_count
            FROM regional_spectral_backfill_batches
            WHERE
                run_id = %s
                AND status = ANY(%s)
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 1
                    WHEN 'failed' THEN 2
                    ELSE 9
                END,
                batch_number
            FOR UPDATE SKIP LOCKED
            LIMIT 1;
            """,
            (
                run_id,
                allowed_statuses,
            ),
        )

        row = cursor.fetchone()

        if row is None:
            conn.commit()
            return None

        (
            batch_id,
            batch_number,
            zone,
            expected_area_n,
            previous_status,
            attempt_count,
        ) = row

        cursor.execute(
            """
            UPDATE regional_spectral_backfill_batches
            SET
                status = 'running',
                attempt_count = attempt_count + 1,
                started_at = COALESCE(
                    started_at,
                    now()
                ),
                completed_at = NULL,
                error_message = NULL,
                updated_at = now()
            WHERE batch_id = %s;
            """,
            (batch_id,),
        )

        cursor.execute(
            """
            UPDATE regional_spectral_backfill_items
            SET
                status = 'running',
                attempt_count = attempt_count + 1,
                claimed_at = now(),
                completed_at = NULL,
                error_code = NULL,
                error_message = NULL,
                updated_at = now()
            WHERE
                batch_id = %s
                AND status IN (
                    'pending',
                    'failed'
                );
            """,
            (batch_id,),
        )

        cursor.execute(
            """
            UPDATE regional_spectral_backfill_runs
            SET
                status = 'running',
                started_at = COALESCE(
                    started_at,
                    now()
                ),
                updated_at = now()
            WHERE run_id = %s;
            """,
            (run_id,),
        )

    conn.commit()

    return {
        "batch_id": str(batch_id),
        "batch_number": int(batch_number),
        "zone": zone,
        "expected_area_n": int(expected_area_n),
        "previous_status": previous_status,
        "attempt_count": int(attempt_count) + 1,
    }


def load_batch_geometries(
    cursor: Any,
    batch_id: str,
) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            i.item_id,
            i.area_id,
            i.source_geometry_id,
            ST_AsGeoJSON(g.geom, 6)::text
                AS geometry_geojson
        FROM regional_spectral_backfill_items i
        JOIN landcover_subtype_geometries g
            ON g.id = i.source_geometry_id
        WHERE
            i.batch_id = %s
            AND i.status = 'running'
        ORDER BY i.area_id;
        """,
        (batch_id,),
    )

    rows = cursor.fetchall()

    return [
        {
            "item_id": str(row[0]),
            "area_id": row[1],
            "source_geometry_id": str(row[2]),
            "geometry_geojson": row[3],
        }
        for row in rows
    ]


def upsert_spectral_records(
    cursor: Any,
    records: list[dict[str, Any]],
) -> dict[str, str]:
    if not records:
        return {}

    values = [
        (
            record["source_geometry_id"],
            SPECTRAL_QC_VERSION,
            SOURCE_VIEW_LABEL,
            record["n_observations"],

            record["ndvi_median"],
            record["ndvi_p25"],
            record["ndvi_p75"],
            record["ndvi_stddev"],

            record["evi_median"],
            record["evi_p25"],
            record["evi_p75"],
            record["evi_stddev"],

            record["ndmi_median"],
            record["ndmi_p25"],
            record["ndmi_p75"],
            record["ndmi_stddev"],

            record["bsi_median"],
            record["bsi_p25"],
            record["bsi_p75"],
            record["bsi_stddev"],

            record["spectral_flag"],
            record[
                "usable_for_baseline_spectral"
            ],
            record["exclusion_reason"],
        )
        for record in records
    ]

    execute_values(
        cursor,
        """
        INSERT INTO landcover_subtype_spectral_qc (
            source_geometry_id,
            spectral_qc_version,
            source_view,
            n_observations,

            ndvi_median,
            ndvi_p25,
            ndvi_p75,
            ndvi_stddev,

            evi_median,
            evi_p25,
            evi_p75,
            evi_stddev,

            ndmi_median,
            ndmi_p25,
            ndmi_p75,
            ndmi_stddev,

            bsi_median,
            bsi_p25,
            bsi_p75,
            bsi_stddev,

            spectral_flag,
            usable_for_baseline_spectral,
            exclusion_reason
        )
        VALUES %s

        ON CONFLICT (
            source_geometry_id,
            spectral_qc_version
        )
        DO UPDATE SET
            source_view =
                EXCLUDED.source_view,

            n_observations =
                EXCLUDED.n_observations,

            ndvi_median =
                EXCLUDED.ndvi_median,
            ndvi_p25 =
                EXCLUDED.ndvi_p25,
            ndvi_p75 =
                EXCLUDED.ndvi_p75,
            ndvi_stddev =
                EXCLUDED.ndvi_stddev,

            evi_median =
                EXCLUDED.evi_median,
            evi_p25 =
                EXCLUDED.evi_p25,
            evi_p75 =
                EXCLUDED.evi_p75,
            evi_stddev =
                EXCLUDED.evi_stddev,

            ndmi_median =
                EXCLUDED.ndmi_median,
            ndmi_p25 =
                EXCLUDED.ndmi_p25,
            ndmi_p75 =
                EXCLUDED.ndmi_p75,
            ndmi_stddev =
                EXCLUDED.ndmi_stddev,

            bsi_median =
                EXCLUDED.bsi_median,
            bsi_p25 =
                EXCLUDED.bsi_p25,
            bsi_p75 =
                EXCLUDED.bsi_p75,
            bsi_stddev =
                EXCLUDED.bsi_stddev,

            spectral_flag =
                EXCLUDED.spectral_flag,

            usable_for_baseline_spectral =
                EXCLUDED.usable_for_baseline_spectral,

            exclusion_reason =
                EXCLUDED.exclusion_reason,

            computed_at = now();
        """,
        values,
        page_size=100,
    )

    geometry_ids = [
        record["source_geometry_id"]
        for record in records
    ]

    cursor.execute(
        """
        SELECT
            id,
            source_geometry_id
        FROM landcover_subtype_spectral_qc
        WHERE
            spectral_qc_version = %s
            AND source_geometry_id =
                ANY(%s::uuid[]);
        """,
        (
            SPECTRAL_QC_VERSION,
            geometry_ids,
        ),
    )

    return {
        str(source_geometry_id): str(row_id)
        for row_id, source_geometry_id
        in cursor.fetchall()
    }


# ================================================================
# FUNZIONE MODIFICATA: mark_items_completed CON run_id
# ================================================================

def mark_items_completed(
    cursor: Any,
    run_id: str,
    spectral_ids: dict[str, str],
) -> None:
    if not spectral_ids:
        return

    values = [
        (
            spectral_row_id,
            source_geometry_id,
            run_id,
        )
        for source_geometry_id, spectral_row_id
        in spectral_ids.items()
    ]

    execute_values(
        cursor,
        """
        UPDATE regional_spectral_backfill_items AS i
        SET
            status = 'completed',
            spectral_qc_row_id =
                data.spectral_row_id::uuid,
            completed_at = now(),
            updated_at = now(),
            error_code = NULL,
            error_message = NULL
        FROM (
            VALUES %s
        ) AS data(
            spectral_row_id,
            source_geometry_id,
            run_id
        )
        WHERE
            i.source_geometry_id =
                data.source_geometry_id::uuid
            AND i.run_id =
                data.run_id::uuid;
        """,
        values,
        template="(%s, %s, %s)",
        page_size=100,
    )


def mark_items_failed(
    cursor: Any,
    source_geometry_ids: list[str],
    error_code: str,
    error_message: str,
) -> None:
    if not source_geometry_ids:
        return

    cursor.execute(
        """
        UPDATE regional_spectral_backfill_items
        SET
            status = 'failed',
            error_code = %s,
            error_message = %s,
            updated_at = now()
        WHERE
            run_id = (
                SELECT run_id
                FROM regional_spectral_backfill_runs
                WHERE run_version = %s
            )
            AND source_geometry_id =
                ANY(%s::uuid[])
            AND status = 'running';
        """,
        (
            error_code,
            error_message[:4000],
            RUN_VERSION,
            source_geometry_ids,
        ),
    )


def refresh_run_counts(
    cursor: Any,
    run_id: str,
) -> None:
    cursor.execute(
        """
        WITH item_counts AS (
            SELECT
                COUNT(*) FILTER (
                    WHERE status = 'pending'
                ) AS pending_n,

                COUNT(*) FILTER (
                    WHERE status = 'running'
                ) AS running_n,

                COUNT(*) FILTER (
                    WHERE status = 'completed'
                ) AS completed_n,

                COUNT(*) FILTER (
                    WHERE status = 'failed'
                ) AS failed_n
            FROM regional_spectral_backfill_items
            WHERE run_id = %s
        )
        UPDATE regional_spectral_backfill_runs r
        SET
            pending_area_n =
                item_counts.pending_n,

            running_area_n =
                item_counts.running_n,

            completed_area_n =
                item_counts.completed_n,

            failed_area_n =
                item_counts.failed_n,

            updated_at = now()
        FROM item_counts
        WHERE r.run_id = %s;
        """,
        (
            run_id,
            run_id,
        ),
    )


def finalize_batch(
    cursor: Any,
    run_id: str,
    batch_id: str,
) -> tuple[str, int, int]:
    cursor.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE status = 'completed'
            ) AS completed_n,

            COUNT(*) FILTER (
                WHERE status = 'failed'
            ) AS failed_n,

            COUNT(*) FILTER (
                WHERE status IN (
                    'pending',
                    'running'
                )
            ) AS open_n
        FROM regional_spectral_backfill_items
        WHERE batch_id = %s;
        """,
        (batch_id,),
    )

    completed_n, failed_n, open_n = cursor.fetchone()

    if open_n > 0:
        batch_status = "failed"
        error_message = (
            f"Batch incompleto: {open_n} item "
            "ancora pending/running."
        )

    elif failed_n > 0:
        batch_status = "failed"
        error_message = (
            f"{failed_n} item non completati."
        )

    else:
        batch_status = "completed"
        error_message = None

    cursor.execute(
        """
        UPDATE regional_spectral_backfill_batches
        SET
            status = %s,
            completed_at = CASE
                WHEN %s IN (
                    'completed',
                    'failed'
                )
                THEN now()
                ELSE completed_at
            END,
            error_message = %s,
            updated_at = now()
        WHERE batch_id = %s;
        """,
        (
            batch_status,
            batch_status,
            error_message,
            batch_id,
        ),
    )

    refresh_run_counts(
        cursor=cursor,
        run_id=run_id,
    )

    return (
        batch_status,
        int(completed_n),
        int(failed_n),
    )


def finalize_run_if_finished(
    cursor: Any,
    run_id: str,
) -> str:
    cursor.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE status = 'pending'
            ) AS pending_batch_n,

            COUNT(*) FILTER (
                WHERE status = 'running'
            ) AS running_batch_n,

            COUNT(*) FILTER (
                WHERE status = 'failed'
            ) AS failed_batch_n
        FROM regional_spectral_backfill_batches
        WHERE run_id = %s;
        """,
        (run_id,),
    )

    (
        pending_batch_n,
        running_batch_n,
        failed_batch_n,
    ) = cursor.fetchone()

    if pending_batch_n > 0 or running_batch_n > 0:
        status = "running"
        completed_at_sql = "NULL"

    elif failed_batch_n > 0:
        status = "completed_with_failures"
        completed_at_sql = "now()"

    else:
        status = "completed"
        completed_at_sql = "now()"

    cursor.execute(
        f"""
        UPDATE regional_spectral_backfill_runs
        SET
            status = %s,
            completed_at = {completed_at_sql},
            updated_at = now()
        WHERE run_id = %s;
        """,
        (
            status,
            run_id,
        ),
    )

    return status


def process_batch(
    conn: Any,
    run_id: str,
    batch: dict[str, Any],
) -> None:
    batch_id = batch["batch_id"]

    with conn.cursor() as cursor:
        items = load_batch_geometries(
            cursor=cursor,
            batch_id=batch_id,
        )

    # MODIFICA: validazione più flessibile
    if not items:
        raise RuntimeError(
            "Il batch non contiene item processabili."
        )

    if len(items) > batch["expected_area_n"]:
        raise RuntimeError(
            "Numero di geometrie superiore alla "
            "dimensione prevista del batch: "
            f"massimo {batch['expected_area_n']}, "
            f"trovate {len(items)}."
        )

    print()
    print(
        f"Batch {batch['batch_number']} "
        f"| zona={batch['zone']} "
        f"| aree={len(items)} "
        f"| tentativo={batch['attempt_count']}"
    )

    item_chunks = chunks(
        items,
        GEE_SUB_BATCH_SIZE,
    )

    for chunk_number, item_chunk in enumerate(
        item_chunks,
        start=1,
    ):
        geometry_batch = []

        for item in item_chunk:
            geometry_batch.append(
                (
                    item["source_geometry_id"],
                    json.loads(
                        item["geometry_geojson"]
                    ),
                )
            )

        source_geometry_ids = [
            item["source_geometry_id"]
            for item in item_chunk
        ]

        try:
            records = reduce_spectral_metrics(
                geometry_batch
            )

            returned_ids = {
                record["source_geometry_id"]
                for record in records
            }

            missing_ids = sorted(
                set(source_geometry_ids)
                - returned_ids
            )

            if missing_ids:
                raise RuntimeError(
                    "Earth Engine non ha restituito "
                    f"{len(missing_ids)} geometrie."
                )

            with conn.cursor() as cursor:
                spectral_ids = (
                    upsert_spectral_records(
                        cursor=cursor,
                        records=records,
                    )
                )

                unresolved_ids = sorted(
                    set(source_geometry_ids)
                    - set(spectral_ids)
                )

                if unresolved_ids:
                    raise RuntimeError(
                        "Upsert non verificato per "
                        f"{len(unresolved_ids)} geometrie."
                    )

                mark_items_completed(
                    cursor=cursor,
                    run_id=run_id,
                    spectral_ids=spectral_ids,
                )

                refresh_run_counts(
                    cursor=cursor,
                    run_id=run_id,
                )

            conn.commit()

            print(
                f"  sotto-batch "
                f"{chunk_number}/{len(item_chunks)} "
                f"completato | "
                f"{len(records)} aree"
            )

        # ============================================================
        # EXCEPT MODIFICATO CON GESTIONE INFRASTRUTTURA
        # ============================================================
        except Exception as exc:
            conn.rollback()

            error_message = (
                f"{type(exc).__name__}: {exc}"
            )

            if is_infrastructure_error(exc):
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE regional_spectral_backfill_items
                        SET
                            status = 'pending',
                            claimed_at = NULL,
                            error_code = NULL,
                            error_message = NULL,
                            updated_at = now()
                        WHERE
                            batch_id = %s
                            AND status = 'running';
                        """,
                        (batch_id,),
                    )

                    cursor.execute(
                        """
                        UPDATE regional_spectral_backfill_batches
                        SET
                            status = 'pending',
                            completed_at = NULL,
                            error_message = %s,
                            updated_at = now()
                        WHERE batch_id = %s;
                        """,
                        (
                            (
                                "Infrastructure interruption: "
                                + error_message
                            )[:4000],
                            batch_id,
                        ),
                    )

                    refresh_run_counts(
                        cursor=cursor,
                        run_id=run_id,
                    )

                conn.commit()

                raise RuntimeError(
                    "Earth Engine non raggiungibile. "
                    "Batch sospeso senza marcare gli "
                    "item rimanenti come falliti."
                ) from exc

            with conn.cursor() as cursor:
                mark_items_failed(
                    cursor=cursor,
                    source_geometry_ids=(
                        source_geometry_ids
                    ),
                    error_code=(
                        "spectral_request_timeout"
                        if "timed out"
                        in str(exc).lower()
                        or "deadline"
                        in str(exc).lower()
                        else
                        "spectral_sub_batch_failed"
                    ),
                    error_message=error_message,
                )

                refresh_run_counts(
                    cursor=cursor,
                    run_id=run_id,
                )

            conn.commit()

            print(
                f"  sotto-batch "
                f"{chunk_number}/{len(item_chunks)} "
                f"fallito | {error_message}"
            )


def main() -> None:
    init_ee()

    ee.data.setDeadline(
        EE_REQUEST_TIMEOUT_SECONDS * 1000
    )

    ee.data.setMaxRetries(
        EE_MAX_RETRIES
    )

    processed_batches = 0

    with psycopg2.connect(DATABASE_URL) as conn:
        run_id, initial_status = get_run(
            conn.cursor()
        )

        print("Regional spectral backfill worker")
        print("--------------------------------")
        print(f"run_version: {RUN_VERSION}")
        print(f"initial_status: {initial_status}")
        print(
            "gee_sub_batch_size: "
            f"{GEE_SUB_BATCH_SIZE}"
        )
        print(
            "max_batches_per_run: "
            f"{MAX_BATCHES_PER_RUN}"
        )
        print(
            "retry_failed_batches: "
            f"{RETRY_FAILED_BATCHES}"
        )
        print(
            "ee_request_timeout_seconds: "
            f"{EE_REQUEST_TIMEOUT_SECONDS}"
        )
        print(
            "ee_max_retries: "
            f"{EE_MAX_RETRIES}"
        )

        while processed_batches < MAX_BATCHES_PER_RUN:
            batch = claim_next_batch(
                conn=conn,
                run_id=run_id,
            )

            if batch is None:
                print(
                    "Nessun altro batch processabile."
                )
                break

            try:
                process_batch(
                    conn=conn,
                    run_id=run_id,
                    batch=batch,
                )

            # ============================================================
            # EXCEPT MODIFICATO CON GESTIONE INFRASTRUTTURA
            # ============================================================
            except Exception as exc:
                if is_infrastructure_error(exc) or (
                    "earth engine non raggiungibile"
                    in str(exc).lower()
                ):
                    conn.rollback()

                    print(
                        "Worker interrotto per errore "
                        "di rete o DNS. Gli item non "
                        "completati restano pending."
                    )

                    break

                conn.rollback()

                error_message = (
                    f"{type(exc).__name__}: {exc}"
                )

                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE
                            regional_spectral_backfill_items
                        SET
                            status = 'failed',
                            error_code =
                                'batch_worker_failed',
                            error_message = %s,
                            updated_at = now()
                        WHERE
                            batch_id = %s
                            AND status = 'running';
                        """,
                        (
                            error_message[:4000],
                            batch["batch_id"],
                        ),
                    )

                    cursor.execute(
                        """
                        UPDATE
                            regional_spectral_backfill_batches
                        SET
                            status = 'failed',
                            completed_at = now(),
                            error_message = %s,
                            updated_at = now()
                        WHERE batch_id = %s;
                        """,
                        (
                            error_message[:4000],
                            batch["batch_id"],
                        ),
                    )

                    refresh_run_counts(
                        cursor=cursor,
                        run_id=run_id,
                    )

                conn.commit()

                print(
                    "Batch fallito: "
                    f"{error_message}"
                )

                traceback.print_exc()

            with conn.cursor() as cursor:
                (
                    batch_status,
                    completed_n,
                    failed_n,
                ) = finalize_batch(
                    cursor=cursor,
                    run_id=run_id,
                    batch_id=batch["batch_id"],
                )

                run_status = finalize_run_if_finished(
                    cursor=cursor,
                    run_id=run_id,
                )

            conn.commit()

            print(
                f"Batch {batch['batch_number']} "
                f"terminato | "
                f"status={batch_status} "
                f"| completed={completed_n} "
                f"| failed={failed_n}"
            )

            print(
                f"Stato run: {run_status}"
            )

            processed_batches += 1

    print()
    print(
        f"Batch processati in questa esecuzione: "
        f"{processed_batches}"
    )


if __name__ == "__main__":
    main()