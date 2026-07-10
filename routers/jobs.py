from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from psycopg2.extras import RealDictCursor

from routers.areas import get_connection, validate_entity
from schemas import (
    BatchJobCreateRequest,
    JobCreateResponse,
    JobStatusResponse,
)


router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
)


# Registro temporaneo per la fase contrattuale.
# Sarà sostituito da persistenza DB + worker nella fase operativa.
_JOB_STORE: dict[str, dict[str, Any]] = {}
_JOB_STORE_LOCK = Lock()


def _validate_area_ids(area_ids: list[str]) -> list[str]:
    cleaned = [
        str(area_id).strip()
        for area_id in area_ids
        if str(area_id).strip()
    ]

    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail="È necessario selezionare almeno un'area.",
        )

    if len(cleaned) > 5:
        raise HTTPException(
            status_code=422,
            detail="È possibile analizzare al massimo 5 aree per job.",
        )

    if len(cleaned) != len(set(cleaned)):
        raise HTTPException(
            status_code=422,
            detail="La richiesta contiene area_id duplicati.",
        )

    return cleaned


def _fetch_entity_catalog_areas(
    conn,
    entity_id: str,
    area_ids: list[str],
) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                area_id::text AS area_id,
                entity_id,
                technical_subtype_id,
                area_type,
                reliability_score,
                reliability_class,
                catalog_priority_candidate,
                area_ha_raw,
                reliability_model_version,
                catalog_version
            FROM area_catalog_v1_entity_scope
            WHERE entity_id = %s
              AND area_id::text = ANY(%s::text[])
            ORDER BY area_id::text;
            """,
            (entity_id, area_ids),
        )

        return [dict(row) for row in cur.fetchall()]


@router.post(
    "/batch",
    response_model=JobCreateResponse,
    status_code=202,
)
def create_batch_job(
    payload: BatchJobCreateRequest,
):
    entity_id = payload.entity_id.strip()

    if not entity_id:
        raise HTTPException(
            status_code=422,
            detail="entity_id è obbligatorio.",
        )

    area_ids = _validate_area_ids(payload.area_ids)

    with get_connection() as conn:
        validate_entity(conn, entity_id)

        catalog_areas = _fetch_entity_catalog_areas(
            conn=conn,
            entity_id=entity_id,
            area_ids=area_ids,
        )

    found_ids = {
        str(area["area_id"])
        for area in catalog_areas
    }

    missing_ids = [
        area_id
        for area_id in area_ids
        if area_id not in found_ids
    ]

    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail={
                "message": (
                    "Una o più aree non esistono oppure non appartengono "
                    "al territorio dell'ente."
                ),
                "missing_area_ids": missing_ids,
            },
        )

    job_id = f"job_{uuid4().hex}"

    job_record = {
        "job_id": job_id,
        "status": "queued",
        "current_step": "Job registrato",
        "progress_pct": 0.0,
        "result": None,
        "error": None,
        "entity_id": entity_id,
        "area_ids": area_ids,
        "areas": catalog_areas,
        "analysis_profile": payload.analysis_profile,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with _JOB_STORE_LOCK:
        _JOB_STORE[job_id] = job_record

    return JobCreateResponse(
        job_id=job_id,
        status="queued",
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
)
def get_job_status(
    job_id: str,
):
    with _JOB_STORE_LOCK:
        job = _JOB_STORE.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"Job non trovato: {job_id}",
        )

    return JobStatusResponse.model_validate(job)
