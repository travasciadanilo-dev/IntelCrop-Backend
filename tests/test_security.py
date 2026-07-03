import os
import pytest
from fastapi import HTTPException

# Modalità test: nessuna API key e ambiente produzione
os.environ["ENV"] = "production"
os.environ.pop("INTELCROP_API_KEY", None)

import main


def test_verify_api_key_rejects_missing_key_in_production():
    main.ENV = "production"
    main.INTELCROP_API_KEY = None

    with pytest.raises(HTTPException) as exc:
        main.verify_api_key(x_api_key=None)

    assert exc.value.status_code == 500
    assert exc.value.detail == "Configurazione API non valida."


def test_verify_api_key_rejects_wrong_key():
    main.ENV = "production"
    main.INTELCROP_API_KEY = "correct-key"

    with pytest.raises(HTTPException) as exc:
        main.verify_api_key(x_api_key="wrong-key")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unauthorized"


def test_verify_api_key_accepts_correct_key():
    main.ENV = "production"
    main.INTELCROP_API_KEY = "correct-key"

    auth_id = main.verify_api_key(x_api_key="correct-key")

    assert isinstance(auth_id, str)
    assert len(auth_id) == 16


def test_calculate_geojson_area_valid_polygon():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [15.0000, 41.0000],
                        [15.0030, 41.0000],
                        [15.0030, 41.0030],
                        [15.0000, 41.0030],
                        [15.0000, 41.0000],
                    ]]
                }
            }
        ]
    }

    area_ha = main.calculate_geojson_area_ha(geojson)

    assert area_ha > 0
    assert area_ha < 20


def test_validate_field_area_rejects_too_large():
    main.MAX_FIELD_AREA_HA = 100
    main.MIN_FIELD_AREA_HA = 0.05

    with pytest.raises(HTTPException) as exc:
        main.validate_field_area_or_raise(1000)

    assert exc.value.status_code == 422
    assert "Area troppo grande" in exc.value.detail


def test_validate_field_area_rejects_invalid_area():
    with pytest.raises(HTTPException) as exc:
        main.validate_field_area_or_raise(0)

    assert exc.value.status_code == 422
    assert "Area di studio non valida" in exc.value.detail