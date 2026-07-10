from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from psycopg2.extras import Json, RealDictCursor

from routers.areas import get_connection, json_safe, validate_entity
from schemas import (
    BatchJobCreateRequest,
    JobCreateResponse,
    JobStatusResponse,
)


router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
)


def validate_area_ids(area_ids: list[str]) -> list[str]:
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


def fetch_entity_catalog_areas(
    conn,
    entity_id: str,
    area_ids: list[str],
) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                to_jsonb(area_row) - 'geom' AS area_snapshot
            FROM area_catalog_v1_entity_scope AS area_row
            WHERE entity_id = %s
              AND area_id::text = ANY(%s::text[])
            ORDER BY area_id::text;
            """,
            (entity_id, area_ids),
        )

        rows = cur.fetchall()

    catalog_areas = []

    for row in rows:
        snapshot = row["area_snapshot"]

        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)

        catalog_areas.append(
            {
                key: json_safe(value)
                for key, value in snapshot.items()
            }
        )

    return catalog_areas


def insert_job(
    conn,
    *,
    job_id: str,
    entity_id: str,
    area_ids: list[str],
    catalog_areas: list[dict[str, Any]],
    analysis_profile: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analysis_jobs_v1 (
                job_id,
                entity_id,
                status,
                current_step,
                progress_pct,
                analysis_profile,
                area_ids,
                area_snapshot,
                result,
                error,
                request_version,
                catalog_version,
                model_version
            )
            VALUES (
                %s,
                %s,
                'queued',
                'Job registrato',
                0.0,
                %s,
                %s,
                %s,
                NULL,
                NULL,
                'catalog_batch_job_v1',
                'area_catalog_v1_diagnostic',
                'regional_reliability_score_exp_v3'
            );
            """,
            (
                job_id,
                entity_id,
                analysis_profile,
                Json(area_ids),
                Json(catalog_areas),
            ),
        )


def fetch_job(
    conn,
    job_id: str,
) -> dict[str, Any] | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                job_id,
                status,
                current_step,
                progress_pct,
                result,
                error
            FROM analysis_jobs_v1
            WHERE job_id = %s;
            """,
            (job_id,),
        )

        row = cur.fetchone()

    if not row:
        return None

    result = dict(row)

    for field in ("result", "error"):
        value = result.get(field)

        if isinstance(value, str):
            result[field] = json.loads(value)

    return {
        key: json_safe(value)
        for key, value in result.items()
    }


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

    area_ids = validate_area_ids(payload.area_ids)

    analysis_profile = payload.analysis_profile.strip()

    if not analysis_profile:
        raise HTTPException(
            status_code=422,
            detail="analysis_profile non può essere vuoto.",
        )

    with get_connection() as conn:
        validate_entity(conn, entity_id)

        catalog_areas = fetch_entity_catalog_areas(
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
                        "Una o più aree non esistono oppure non "
                        "appartengono al territorio dell'ente."
                    ),
                    "missing_area_ids": missing_ids,
                },
            )

        job_id = f"job_{uuid4().hex}"

        insert_job(
            conn=conn,
            job_id=job_id,
            entity_id=entity_id,
            area_ids=area_ids,
            catalog_areas=catalog_areas,
            analysis_profile=analysis_profile,
        )

        conn.commit()

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
    with get_connection() as conn:
        job = fetch_job(
            conn=conn,
            job_id=job_id,
        )

    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"Job non trovato: {job_id}",
        )

    return JobStatusResponse.model_validate(job)
