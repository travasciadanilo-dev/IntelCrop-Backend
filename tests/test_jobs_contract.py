from schemas import JobCreateResponse, JobStatusResponse


def test_job_create_response_accepts_queued_status():
    payload = {
        "job_id": "job_12345678",
        "status": "queued"
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
        "error": None
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.job_id == "job_12345678"
    assert validated.status == "processing"
    assert validated.current_step == "Caricamento immagini Sentinel-2"
    assert validated.progress_pct == 20.0
    assert validated.result is None
    assert validated.error is None


def test_job_status_response_accepts_done_status_without_result_during_contract_stage():
    payload = {
        "job_id": "job_12345678",
        "status": "done",
        "current_step": "Analisi completata",
        "progress_pct": 100.0,
        "result": None,
        "error": None
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
            "error_id": "abc12345"
        }
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.status == "error"
    assert validated.error.code == "analysis_failed"
    assert validated.error.error_id == "abc12345"


def test_job_status_response_accepts_cancelled_status():
    payload = {
        "job_id": "job_12345678",
        "status": "cancelled",
        "current_step": "Analisi annullata",
        "progress_pct": None,
        "result": None,
        "error": None
    }

    validated = JobStatusResponse.model_validate(payload)

    assert validated.status == "cancelled"
    assert validated.current_step == "Analisi annullata"