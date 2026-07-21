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


def safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


def build_spectral_summary(
    areas: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not any(
        area.get("feature_matrix_version")
        for area in areas
    ):
        return None

    index_fields = (
        "ndvi_median",
        "evi_median",
        "ndmi_median",
        "bsi_median",
    )

    index_means: dict[str, float | None] = {}

    for field in index_fields:
        values = [
            value
            for area in areas
            if (
                value := safe_float(area.get(field))
            ) is not None
        ]

        index_means[field] = mean_or_none(values)

    observation_values = [
        value
        for area in areas
        if (
            value := safe_float(
                area.get("n_observations")
            )
        ) is not None
    ]

    usable_count = sum(
        1
        for area in areas
        if area.get("usable_for_baseline_spectral") is True
    )

    complete_count = sum(
        1
        for area in areas
        if area.get(
            "has_complete_spectral_features"
        ) is True
    )

    spectral_status_counts: dict[str, int] = {}

    for area in areas:
        status = str(
            area.get("spectral_status") or "unknown"
        )

        spectral_status_counts[status] = (
            spectral_status_counts.get(status, 0) + 1
        )

    return {
        "source": "regional_snapshot_features",
        "selected_area_count": len(areas),
        "complete_feature_count": complete_count,
        "usable_baseline_count": usable_count,
        "not_usable_baseline_count": (
            len(areas) - usable_count
        ),
        "mean_observations": mean_or_none(
            observation_values
        ),
        "mean_indices": index_means,
        "spectral_status_counts": spectral_status_counts,
        "interpretation_scope": (
            "Descrizione delle feature spettrali regionali "
            "precalcolate; non rappresenta una diagnosi "
            "agronomica assoluta o un confronto temporale "
            "del singolo appezzamento."
        ),
    }


def build_relative_comparison(
    areas: list[dict[str, Any]],
) -> tuple[
    dict[str, Any] | None,
    dict[str, dict[str, Any]],
]:
    """
    Confronta descrittivamente le aree selezionate nello stesso job.

    I risultati indicano soltanto la posizione relativa dei valori
    osservati. Non esprimono qualit?, salute o priorit? agronomica.
    """
    if not any(
        area.get("feature_matrix_version")
        for area in areas
    ):
        return None, {}

    complete_areas = [
        area
        for area in areas
        if (
            area.get("has_complete_spectral_features")
            is True
            and area.get("area_id") is not None
        )
    ]

    base_result = {
        "comparison_scope": "selected_job_areas_only",
        "minimum_required_areas": 2,
        "comparable_area_count": len(complete_areas),
        "position_definition": (
            "0.0 indica il valore minimo e 1.0 il valore "
            "massimo osservato tra le aree selezionate. "
            "La posizione non rappresenta qualit? agronomica."
        ),
    }

    if len(complete_areas) < 2:
        return {
            **base_result,
            "status": "insufficient_areas",
            "indices": {},
        }, {}

    index_fields = (
        "ndvi_median",
        "evi_median",
        "ndmi_median",
        "bsi_median",
    )

    comparison_indices: dict[str, dict[str, Any]] = {}
    area_positions: dict[str, dict[str, Any]] = {
        str(area["area_id"]): {}
        for area in complete_areas
    }

    for field in index_fields:
        values_by_area = {
            str(area["area_id"]): value
            for area in complete_areas
            if (
                value := safe_float(area.get(field))
            ) is not None
        }

        if len(values_by_area) < 2:
            continue

        values = list(values_by_area.values())
        minimum = min(values)
        maximum = max(values)
        spread = maximum - minimum
        unique_desc = sorted(
            set(values),
            reverse=True,
        )

        comparison_indices[field] = {
            "compared_area_count": len(values_by_area),
            "minimum": minimum,
            "maximum": maximum,
            "spread": spread,
            "interpretation": (
                "Confronto numerico interno al job; valori "
                "pi? alti o pi? bassi non sono classificati "
                "automaticamente come migliori o peggiori."
            ),
        }

        for area_id, value in values_by_area.items():
            if spread == 0:
                relative_position = 0.5
            else:
                relative_position = (
                    value - minimum
                ) / spread

            area_positions[area_id][field] = {
                "raw_value": value,
                "rank_desc": (
                    unique_desc.index(value) + 1
                ),
                "compared_area_count": len(
                    values_by_area
                ),
                "relative_position_0_1": (
                    relative_position
                ),
            }

    return {
        **base_result,
        "status": (
            "available"
            if comparison_indices
            else "insufficient_data"
        ),
        "indices": comparison_indices,
    }, area_positions


def build_operational_summary(
    job: dict[str, Any],
    areas: list[dict[str, Any]],
    spectral_summary: dict[str, Any] | None,
    relative_comparison: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Costruisce un riepilogo descrittivo del job v4.1.

    Non genera un indice composito e non interpreta gli indici
    spettrali come diagnosi agronomica assoluta.
    """
    feature_matrix_version = job.get(
        "feature_matrix_version"
    )

    if not feature_matrix_version:
        return None

    selected_area_count = len(areas)

    reliability_class_counts: dict[str, int] = {}

    reliability_scores = []

    for area in areas:
        class_name = str(
            area.get("reliability_class") or "unknown"
        )

        reliability_class_counts[class_name] = (
            reliability_class_counts.get(
                class_name,
                0,
            )
            + 1
        )

        score = safe_float(
            area.get("reliability_score")
        )

        if score is not None:
            reliability_scores.append(score)

    mean_reliability_score = mean_or_none(
        reliability_scores
    )

    complete_feature_count = 0
    usable_baseline_count = 0
    not_usable_baseline_count = 0
    spectral_status_counts: dict[str, int] = {}

    if spectral_summary is not None:
        complete_feature_count = int(
            spectral_summary.get(
                "complete_feature_count",
                0,
            )
        )
        usable_baseline_count = int(
            spectral_summary.get(
                "usable_baseline_count",
                0,
            )
        )
        not_usable_baseline_count = int(
            spectral_summary.get(
                "not_usable_baseline_count",
                0,
            )
        )
        spectral_status_counts = dict(
            spectral_summary.get(
                "spectral_status_counts",
                {},
            )
        )

    comparison_status = "not_available"
    comparable_area_count = 0
    available_indices: list[str] = []

    if relative_comparison is not None:
        comparison_status = str(
            relative_comparison.get(
                "status",
                "not_available",
            )
        )
        comparable_area_count = int(
            relative_comparison.get(
                "comparable_area_count",
                0,
            )
        )
        available_indices = sorted(
            relative_comparison.get(
                "indices",
                {}
            ).keys()
        )

    messages = [
        (
            f"Il job comprende {selected_area_count} "
            "aree selezionate."
        ),
        (
            f"Le feature spettrali complete sono disponibili "
            f"per {complete_feature_count} aree."
        ),
        (
            f"Le feature utilizzabili come baseline regionale "
            f"sono disponibili per {usable_baseline_count} aree."
        ),
    ]

    if comparison_status == "available":
        messages.append(
            (
                "Il confronto spettrale relativo e disponibile "
                f"per {comparable_area_count} aree e "
                f"{len(available_indices)} indici."
            )
        )
    elif comparison_status == "insufficient_areas":
        messages.append(
            (
                "Il confronto relativo non e disponibile: "
                "sono necessarie almeno due aree con feature "
                "spettrali complete."
            )
        )
    else:
        messages.append(
            (
                "Il confronto relativo non e disponibile per "
                "insufficienza dei dati confrontabili."
            )
        )

    return {
        "status": "available",
        "scope": "selected_job_areas",
        "catalog_reliability": {
            "model_version": job.get(
                "model_version"
            ),
            "mean_score": mean_reliability_score,
            "class_counts": reliability_class_counts,
            "interpretation": (
                "La probabilita di affidabilita descrive la "
                "compatibilita del candidato con il catalogo "
                "olivicolo regionale; non descrive lo stato "
                "vegetativo della coltura."
            ),
        },
        "spectral_availability": {
            "feature_matrix_version": (
                feature_matrix_version
            ),
            "selected_area_count": selected_area_count,
            "complete_feature_count": (
                complete_feature_count
            ),
            "usable_baseline_count": (
                usable_baseline_count
            ),
            "not_usable_baseline_count": (
                not_usable_baseline_count
            ),
            "status_counts": spectral_status_counts,
        },
        "relative_comparison": {
            "status": comparison_status,
            "comparable_area_count": (
                comparable_area_count
            ),
            "available_indices": available_indices,
            "interpretation": (
                "Le posizioni sono relative esclusivamente alle "
                "aree incluse nel job e non identificano "
                "automaticamente condizioni migliori o peggiori."
            ),
        },
        "messages": messages,
    }


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

    relative_comparison, relative_positions = (
        build_relative_comparison(areas)
    )

    result_areas = []

    for area in areas:
        area_result = {
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

        if area.get("feature_matrix_version"):
            area_id = str(area.get("area_id"))

            area_result["spectral_quality"] = {
                "spectral_status": area.get(
                    "spectral_status"
                ),
                "spectral_flag": area.get(
                    "spectral_flag"
                ),
                "n_observations": area.get(
                    "n_observations"
                ),
                "usable_for_baseline": area.get(
                    "usable_for_baseline_spectral"
                ),
                "complete_features": area.get(
                    "has_complete_spectral_features"
                ),
                "exclusion_reason": area.get(
                    "exclusion_reason"
                ),
            }

            area_result["spectral_indices"] = {
                "ndvi_median": area.get("ndvi_median"),
                "evi_median": area.get("evi_median"),
                "ndmi_median": area.get("ndmi_median"),
                "bsi_median": area.get("bsi_median"),
            }

            if area_id in relative_positions:
                area_result["relative_position"] = (
                    relative_positions[area_id]
                )

        result_areas.append(area_result)

    spectral_summary = build_spectral_summary(areas)

    operational_summary = build_operational_summary(
        job=job,
        areas=areas,
        spectral_summary=spectral_summary,
        relative_comparison=relative_comparison,
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
        "feature_matrix_version": job.get(
            "feature_matrix_version"
        ),
        "generated_at": utc_now_iso(),
        "summary": {
            "selected_area_count": len(area_ids),
            "snapshot_area_count": len(areas),
            "total_area_ha": total_area_ha,
            "priority_area_count": priority_count,
            "mean_reliability_score": mean_reliability_score,
            "reliability_class_counts": class_counts,
        },
        **(
            {"spectral_summary": spectral_summary}
            if spectral_summary is not None
            else {}
        ),
        **(
            {
                "relative_comparison": (
                    relative_comparison
                )
            }
            if relative_comparison is not None
            else {}
        ),
        **(
            {
                "operational_summary": (
                    operational_summary
                )
            }
            if operational_summary is not None
            else {}
        ),
        "areas": result_areas,
        "limitations": [
            (
                "Il risultato usa metadati del catalogo e, per "
                "v4.1, feature spettrali regionali precalcolate. "
                "Non esegue una nuova elaborazione satellitare "
                "temporale sul singolo appezzamento."
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
