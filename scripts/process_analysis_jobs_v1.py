from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from psycopg2.extras import Json, RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from routers.areas import get_connection, json_safe  # noqa: E402


LOGGER = logging.getLogger("analysis_jobs_worker_v1")

WORKER_VERSION = "analysis_jobs_worker_v1"
SUPPORTED_PROFILE = "catalog_screening_v1"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)

    return value


def claim_next_job() -> dict[str, Any] | None:
    """
    Acquisisce atomicamente un singolo job queued.

    FOR UPDATE SKIP LOCKED impedisce a due worker concorrenti
    di elaborare lo stesso job.
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                WITH next_job AS (
                    SELECT job_id
                    FROM analysis_jobs_v1
                    WHERE status = 'queued'
                    ORDER BY created_at, job_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE analysis_jobs_v1 AS jobs
                SET
                    status = 'processing',
                    current_step = 'Validazione snapshot catalogo',
                    progress_pct = 10.0,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                FROM next_job
                WHERE jobs.job_id = next_job.job_id
                RETURNING jobs.*;
                """
            )

            row = cur.fetchone()

        conn.commit()

    if not row:
        return None

    job = dict(row)

    for field in ("area_ids", "area_snapshot", "result", "error"):
        job[field] = decode_json_value(job.get(field))

    return {
        key: json_safe(value)
        for key, value in job.items()
    }


def update_job_progress(
    job_id: str,
    *,
    current_step: str,
    progress_pct: float,
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analysis_jobs_v1
                SET
                    current_step = %s,
                    progress_pct = %s,
                    updated_at = NOW()
                WHERE job_id = %s
                  AND status = 'processing';
                """,
                (
                    current_step,
                    progress_pct,
                    job_id,
                ),
            )

            if cur.rowcount != 1:
                raise RuntimeError(
                    f"Impossibile aggiornare il job processing: {job_id}"
                )

        conn.commit()


def build_catalog_screening_result(
    job: dict[str, Any],
) -> dict[str, Any]:
    area_ids = job.get("area_ids") or []
    areas = job.get("area_snapshot") or []

    if not isinstance(area_ids, list) or not area_ids:
        raise ValueError("area_ids mancante o non valido.")

    if not isinstance(areas, list) or not areas:
        raise ValueError("area_snapshot mancante o non valido.")

    snapshot_ids = {
        str(area.get("area_id"))
        for area in areas
        if area.get("area_id") is not None
    }

    missing_snapshot_ids = [
        str(area_id)
        for area_id in area_ids
        if str(area_id) not in snapshot_ids
    ]

    if missing_snapshot_ids:
        raise ValueError(
            "Snapshot incompleto per le aree: "
            + ", ".join(missing_snapshot_ids)
        )

    reliability_scores = [
        float(area["reliability_score"])
        for area in areas
        if area.get("reliability_score") is not None
    ]

    total_area_ha = sum(
        float(area.get("area_ha") or 0.0)
        for area in areas
    )

    priority_count = sum(
        1
        for area in areas
        if area.get("catalog_priority_candidate") is True
    )

    class_counts: dict[str, int] = {}

    for area in areas:
        class_name = str(
            area.get("reliability_class") or "unknown"
        )

        class_counts[class_name] = (
            class_counts.get(class_name, 0) + 1
        )

    mean_reliability_score = None

    if reliability_scores:
        mean_reliability_score = (
            sum(reliability_scores) / len(reliability_scores)
        )

    result_areas = []

    for area in areas:
        result_areas.append(
            {
                "area_id": area.get("area_id"),
                "area_ha": area.get("area_ha"),
                "reliability_score": area.get(
                    "reliability_score"
                ),
                "reliability_class": area.get(
                    "reliability_class"
                ),
                "priority_candidate": area.get(
                    "catalog_priority_candidate"
                ),
                "technical_subtype_id": area.get(
                    "technical_subtype_id"
                ),
                "spatial_validation_zone": area.get(
                    "spatial_validation_zone"
                ),
            }
        )

    return {
        "result_type": "catalog_screening_diagnostic_v1",
        "status": "completed",
        "job_id": job["job_id"],
        "entity_id": job["entity_id"],
        "analysis_profile": job["analysis_profile"],
        "worker_version": WORKER_VERSION,
        "catalog_version": job["catalog_version"],
        "model_version": job["model_version"],
        "generated_at": utc_now_iso(),
        "summary": {
            "selected_area_count": len(area_ids),
            "snapshot_area_count": len(areas),
            "total_area_ha": total_area_ha,
            "priority_area_count": priority_count,
            "mean_reliability_score": mean_reliability_score,
            "reliability_class_counts": class_counts,
        },
        "areas": result_areas,
        "limitations": [
            (
                "Risultato diagnostico basato sui metadati del "
                "catalogo; non include ancora elaborazioni "
                "satellitari."
            ),
            (
                "Il modello regionale di affidabilità "
                + (
                    "v4.1"
                    if job["catalog_version"]
                    == "area_catalog_v4_1_diagnostic"
                    else "v3"
                )
                + " è sperimentale e non costituisce validazione "
                "assoluta della coltura."
            ),
        ],
    }


def complete_job(
    job_id: str,
    result: dict[str, Any],
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analysis_jobs_v1
                SET
                    status = 'done',
                    current_step = 'Analisi completata',
                    progress_pct = 100.0,
                    result = %s,
                    error = NULL,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE job_id = %s
                  AND status = 'processing';
                """,
                (
                    Json(result),
                    job_id,
                ),
            )

            if cur.rowcount != 1:
                raise RuntimeError(
                    f"Impossibile completare il job: {job_id}"
                )

        conn.commit()


def fail_job(
    job_id: str,
    exc: Exception,
) -> None:
    error_id = uuid4().hex[:12]

    error_payload = {
        "code": "analysis_failed",
        "message": str(exc),
        "error_id": error_id,
        "worker_version": WORKER_VERSION,
        "failed_at": utc_now_iso(),
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analysis_jobs_v1
                SET
                    status = 'error',
                    current_step = 'Errore durante elaborazione',
                    progress_pct = NULL,
                    result = NULL,
                    error = %s,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE job_id = %s
                  AND status = 'processing';
                """,
                (
                    Json(error_payload),
                    job_id,
                ),
            )

        conn.commit()

    LOGGER.exception(
        "Job %s fallito | error_id=%s",
        job_id,
        error_id,
    )


def process_job(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])

    if job.get("analysis_profile") != SUPPORTED_PROFILE:
        raise ValueError(
            "Profilo di analisi non supportato: "
            f"{job.get('analysis_profile')}"
        )

    update_job_progress(
        job_id,
        current_step="Preparazione aree del catalogo",
        progress_pct=35.0,
    )

    result = build_catalog_screening_result(job)

    update_job_progress(
        job_id,
        current_step="Finalizzazione risultato diagnostico",
        progress_pct=80.0,
    )

    complete_job(
        job_id=job_id,
        result=result,
    )


def run_once() -> bool:
    job = claim_next_job()

    if not job:
        LOGGER.info("Nessun job queued disponibile.")
        return False

    job_id = str(job["job_id"])

    LOGGER.info(
        "Job acquisito: %s | entity=%s | aree=%s",
        job_id,
        job.get("entity_id"),
        len(job.get("area_ids") or []),
    )

    try:
        process_job(job)
    except Exception as exc:
        fail_job(job_id, exc)
        return False

    LOGGER.info("Job completato: %s", job_id)
    return True


def run_loop(max_jobs: int) -> int:
    processed = 0

    while processed < max_jobs:
        completed = run_once()

        if not completed:
            break

        processed += 1

    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Elabora job persistenti del catalogo IntelCrop."
        )
    )

    parser.add_argument(
        "--max-jobs",
        type=int,
        default=1,
        help="Numero massimo di job da processare.",
    )

    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = parse_args()

    if args.max_jobs < 1:
        raise ValueError("--max-jobs deve essere almeno 1.")

    processed = run_loop(args.max_jobs)

    LOGGER.info("Job completati in questa esecuzione: %s", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
