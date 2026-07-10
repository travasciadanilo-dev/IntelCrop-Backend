from fastapi.testclient import TestClient

from main import app
from routers.areas import get_connection
from schemas import JobCreateResponse, JobStatusResponse


client = TestClient(app)


def delete_test_job(job_id: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM analysis_jobs_v1
                WHERE job_id = %s;
                """,
                (job_id,),
            )

        conn.commit()


def get_catalog_area_ids(limit: int = 2) -> list[str]:
    response = client.get(
        "/areas",
        params={
            "entity_id": "calabria_demo",
            "priority_only": "true",
            "limit": limit,
        },
    )

    assert response.status_code == 200

    data = response.json()
    area_ids = [item["area_id"] for item in data["items"]]

    assert len(area_ids) >= 1

    return area_ids


def test_job_create_response_accepts_queued_status():
    payload = {
        "job_id": "job_12345678",
        "status": "queued",
    }

    validated = JobCreateResponse.model_validate(payload)

    assert validated.job_id == "job_12345678"
    assert validated.status == "queued"


def test_job_status_response_accepts_processing_status():
    payload = {
        "job_id": "job_12345678",
        "status": "processing",
        "current_step": "Caricamento immagini Sentinel-2",
        "progress_pct": 20.0,
        "result": None,
        "error": None,
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.job_id == "job_12345678"
    assert validated.status == "processing"
    assert validated.current_step == "Caricamento immagini Sentinel-2"
    assert validated.progress_pct == 20.0
    assert validated.result is None
    assert validated.error is None


def test_job_status_response_accepts_done_status_without_result():
    payload = {
        "job_id": "job_12345678",
        "status": "done",
        "current_step": "Analisi completata",
        "progress_pct": 100.0,
        "result": None,
        "error": None,
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.status == "done"
    assert validated.progress_pct == 100.0


def test_job_status_response_accepts_error_status():
    payload = {
        "job_id": "job_12345678",
        "status": "error",
        "current_step": "Errore durante l'elaborazione",
        "progress_pct": None,
        "result": None,
        "error": {
            "code": "analysis_failed",
            "message": "Errore durante l'analisi.",
            "error_id": "abc12345",
        },
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.status == "error"
    assert validated.error is not None
    assert validated.error.code == "analysis_failed"
    assert validated.error.error_id == "abc12345"


def test_job_status_response_accepts_cancelled_status():
    payload = {
        "job_id": "job_12345678",
        "status": "cancelled",
        "current_step": "Analisi annullata",
        "progress_pct": None,
        "result": None,
        "error": None,
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.status == "cancelled"
    assert validated.current_step == "Analisi annullata"


def test_create_batch_job_contract():
    area_ids = get_catalog_area_ids(limit=2)

    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": area_ids,
            "analysis_profile": "catalog_screening_v1",
        },
    )

    assert response.status_code == 202

    data = response.json()

    assert data["status"] == "queued"
    assert data["job_id"].startswith("job_")

    delete_test_job(data["job_id"])


def test_created_batch_job_status_contract():
    area_ids = get_catalog_area_ids(limit=1)

    create_response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": area_ids,
        },
    )

    assert create_response.status_code == 202

    job_id = create_response.json()["job_id"]

    status_response = client.get(f"/jobs/{job_id}")

    assert status_response.status_code == 200

    data = status_response.json()

    assert data["job_id"] == job_id
    assert data["status"] == "queued"
    assert data["current_step"] == "Job registrato"
    assert data["progress_pct"] == 0.0
    assert data["result"] is None
    assert data["error"] is None

    delete_test_job(job_id)


def test_batch_job_requires_at_least_one_area():
    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [],
        },
    )

    assert response.status_code == 422


def test_batch_job_rejects_more_than_five_areas():
    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [
                "area-1",
                "area-2",
                "area-3",
                "area-4",
                "area-5",
                "area-6",
            ],
        },
    )

    assert response.status_code == 422


def test_batch_job_rejects_duplicate_areas():
    area_id = get_catalog_area_ids(limit=1)[0]

    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [area_id, area_id],
        },
    )

    assert response.status_code == 422
    assert "duplicati" in response.json()["detail"].lower()


def test_batch_job_rejects_unknown_entity():
    area_id = get_catalog_area_ids(limit=1)[0]

    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "wrong_entity",
            "area_ids": [area_id],
        },
    )

    assert response.status_code == 404
    assert "ente non trovato" in response.json()["detail"].lower()


def test_batch_job_rejects_area_outside_catalog():
    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": ["area-inesistente"],
        },
    )

    assert response.status_code == 404

    detail = response.json()["detail"]

    assert detail["missing_area_ids"] == ["area-inesistente"]


def test_batch_job_forbids_client_geometry():
    area_id = get_catalog_area_ids(limit=1)[0]

    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [area_id],
            "geometry": {
                "type": "Polygon",
                "coordinates": [],
            },
        },
    )

    assert response.status_code == 422


def test_batch_job_rejects_empty_analysis_profile():
    area_id = get_catalog_area_ids(limit=1)[0]

    response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [area_id],
            "analysis_profile": "   ",
        },
    )

    assert response.status_code == 422


def test_unknown_job_returns_404():
    response = client.get("/jobs/job_inesistente")

    assert response.status_code == 404
    assert "job non trovato" in response.json()["detail"].lower()
