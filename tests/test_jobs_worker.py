import os
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app
from routers.areas import get_connection
from scripts.process_analysis_jobs_v1 import run_once


client = TestClient(app)


def get_test_area_id() -> str:
    response = client.get(
        "/areas",
        params={
            "entity_id": "calabria_demo",
            "priority_only": "true",
            "limit": 1,
        },
    )

    assert response.status_code == 200

    return response.json()["items"][0]["area_id"]


def delete_job(job_id: str) -> None:
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


def test_worker_completes_queued_job():
    area_id = get_test_area_id()

    create_response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [area_id],
        },
    )

    assert create_response.status_code == 202

    job_id = create_response.json()["job_id"]

    try:
        processed = run_once()

        assert processed is True

        status_response = client.get(f"/jobs/{job_id}")

        assert status_response.status_code == 200

        data = status_response.json()

        assert data["status"] == "done"
        assert data["current_step"] == "Analisi completata"
        assert data["progress_pct"] == 100.0
        assert data["error"] is None
        assert data["result"] is not None

        result = data["result"]

        assert (
            result["result_type"]
            == "catalog_screening_diagnostic_v1"
        )
        assert result["job_id"] == job_id
        assert result["summary"]["selected_area_count"] == 1
        assert len(result["areas"]) == 1

        catalog_version = os.getenv(
            "AREA_CATALOG_VERSION",
            "v3",
        ).strip().lower()

        if catalog_version == "v4_1":
            expected_catalog = (
                "area_catalog_v4_1_diagnostic"
            )
            expected_model = (
                "regional_reliability_score_exp_"
                "v4_combined_ridge"
            )
            expected_limitation_version = "v4.1"
            expected_feature_matrix = (
                "area_feature_matrix_regional_v1"
            )
        else:
            expected_catalog = (
                "area_catalog_v1_diagnostic"
            )
            expected_model = (
                "regional_reliability_score_exp_v3"
            )
            expected_limitation_version = "v3"
            expected_feature_matrix = None

        assert (
            result["catalog_version"]
            == expected_catalog
        )
        assert (
            result["model_version"]
            == expected_model
        )
        assert (
            result["feature_matrix_version"]
            == expected_feature_matrix
        )

        if catalog_version == "v4_1":
            assert result["spectral_summary"] is not None

            spectral_summary = result[
                "spectral_summary"
            ]

            assert (
                spectral_summary["selected_area_count"]
                == 1
            )
            assert (
                spectral_summary["complete_feature_count"]
                == 1
            )
            assert (
                spectral_summary["mean_observations"]
                is not None
            )
            assert set(
                spectral_summary["mean_indices"]
            ) == {
                "ndvi_median",
                "evi_median",
                "ndmi_median",
                "bsi_median",
            }

            result_area = result["areas"][0]

            assert result_area["spectral_quality"]
            assert result_area["spectral_indices"]
            assert (
                result_area["spectral_quality"][
                    "complete_features"
                ]
                is True
            )
        else:
            assert result["spectral_summary"] is None
            assert (
                result["areas"][0]["spectral_quality"]
                is None
            )
            assert (
                result["areas"][0]["spectral_indices"]
                is None
            )

        limitations_text = " ".join(
            result["limitations"]
        )

        assert (
            f"affidabilit\u00e0 {expected_limitation_version}"
            in limitations_text
        )
        assert (
            "\u00e8 sperimentale"
            in limitations_text
        )
    finally:
        delete_job(job_id)


def test_worker_returns_false_without_queued_jobs():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analysis_jobs_v1
                SET status = 'cancelled'
                WHERE status = 'queued';
                """
            )

        conn.commit()

    assert run_once() is False


def test_worker_marks_unsupported_profile_as_error():
    area_id = get_test_area_id()

    create_response = client.post(
        "/jobs/batch",
        json={
            "entity_id": "calabria_demo",
            "area_ids": [area_id],
            "analysis_profile": (
                "unsupported_profile_"
                + uuid4().hex[:8]
            ),
        },
    )

    assert create_response.status_code == 202

    job_id = create_response.json()["job_id"]

    try:
        processed = run_once()

        assert processed is False

        status_response = client.get(f"/jobs/{job_id}")

        assert status_response.status_code == 200

        data = status_response.json()

        assert data["status"] == "error"
        assert data["result"] is None
        assert data["error"] is not None
        assert data["error"]["code"] == "analysis_failed"
        assert data["error"]["error_id"]
    finally:
        delete_job(job_id)
