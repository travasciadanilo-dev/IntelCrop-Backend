import os

import pytest


if os.getenv("AREA_CATALOG_VERSION", "v3").strip().lower() != "v4_1":
    pytest.skip(
        "Suite areas v4.1: richiede AREA_CATALOG_VERSION=v4_1",
        allow_module_level=True,
    )


from fastapi.testclient import TestClient

from main import app
from routers.areas import (
    ACTIVE_CATALOG_STATUS,
    ACTIVE_CATALOG_VERSION,
    ACTIVE_FEATURE_MATRIX_VERSION,
    ACTIVE_MODEL_VERSION,
    ALLOWED_RELIABILITY_CLASSES,
    AREA_CATALOG_VERSION,
    ENTITY_CATALOG_VIEW,
    REGIONAL_CATALOG_VIEW,
)


client = TestClient(app)


def test_v4_1_configuration():
    assert AREA_CATALOG_VERSION == "v4_1"
    assert REGIONAL_CATALOG_VIEW == "area_catalog_v4_1_diagnostic"
    assert ENTITY_CATALOG_VIEW == "area_catalog_v4_1_entity_scope"
    assert ACTIVE_CATALOG_VERSION == "area_catalog_v4_1_diagnostic"
    assert ACTIVE_CATALOG_STATUS == "validated_not_promoted"
    assert (
        ACTIVE_FEATURE_MATRIX_VERSION
        == "area_feature_matrix_regional_v1"
    )
    assert (
        ACTIVE_MODEL_VERSION
        == "regional_reliability_score_exp_v4_combined_ridge"
    )
    assert ALLOWED_RELIABILITY_CLASSES == {
        "low",
        "compatible",
        "very_high",
    }


def test_v4_1_summary_contract():
    response = client.get("/areas/summary")

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v4_1_diagnostic"
    assert data["catalog_status"] == "validated_not_promoted"

    classes = {
        row["reliability_class"]: row["n"]
        for row in data["by_class"]
    }

    assert classes == {
        "low": 12714,
        "compatible": 9591,
        "very_high": 17956,
    }

    assert data["totals"]["n_total"] == 40261
    assert data["totals"]["n_priority_candidates"] == 17956


def test_v4_1_priority_contract():
    response = client.get(
        "/areas?priority_only=true&limit=5"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v4_1_diagnostic"
    assert data["catalog_status"] == "validated_not_promoted"
    assert data["total_matching"] == 17956
    assert len(data["items"]) == 5

    for item in data["items"]:
        assert item["reliability_class"] == "very_high"
        assert item["catalog_priority_candidate"] is True
        assert (
            item["reliability_model_version"]
            == "regional_reliability_score_exp_v4_combined_ridge"
        )


def test_v4_1_very_high_contract():
    response = client.get(
        "/areas?reliability_class=very_high&limit=5"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total_matching"] == 17956

    for item in data["items"]:
        assert item["reliability_class"] == "very_high"
        assert item["reliability_rank"] == 3


def test_v4_1_compatible_contract():
    response = client.get(
        "/areas?reliability_class=compatible&limit=5"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total_matching"] == 9591

    for item in data["items"]:
        assert item["reliability_class"] == "compatible"
        assert item["catalog_priority_candidate"] is False


def test_v4_1_high_is_rejected():
    response = client.get(
        "/areas?reliability_class=high&limit=5"
    )

    assert response.status_code == 400


def test_v4_1_geojson_contract():
    response = client.get(
        "/areas"
        "?priority_only=true"
        "&output_format=geojson"
        "&limit=5"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["type"] == "FeatureCollection"
    assert (
        data["metadata"]["catalog_view"]
        == "area_catalog_v4_1_diagnostic"
    )
    assert (
        data["metadata"]["catalog_status"]
        == "validated_not_promoted"
    )
    assert len(data["features"]) == 5

    for feature in data["features"]:
        assert feature["type"] == "Feature"
        assert (
            feature["properties"]["reliability_class"]
            == "very_high"
        )


def test_v4_1_metadata_contract():
    response = client.get("/areas/metadata")

    assert response.status_code == 200

    data = response.json()

    assert (
        data["catalog"]["catalog_view"]
        == "area_catalog_v4_1_diagnostic"
    )
    assert (
        data["catalog"]["catalog_version"]
        == "area_catalog_v4_1_diagnostic"
    )
    assert (
        data["catalog"]["catalog_status"]
        == "validated_not_promoted"
    )
    assert (
        data["catalog"]["feature_matrix_version"]
        == "area_feature_matrix_regional_v1"
    )
    assert (
        data["model"]["model_version"]
        == "regional_reliability_score_exp_v4_combined_ridge"
    )


    public_thresholds = data["thresholds"]

    assert [
        row["class_code"]
        for row in public_thresholds
    ] == [
        "low",
        "compatible",
        "very_high",
    ]

    threshold_by_class = {
        row["class_code"]: row
        for row in public_thresholds
    }

    assert threshold_by_class["low"]["min_score"] == 0.0
    assert threshold_by_class["low"]["max_score"] == 0.61
    assert threshold_by_class["low"]["class_rank"] == 1

    assert (
        threshold_by_class["compatible"]["min_score"]
        == 0.61
    )
    assert (
        threshold_by_class["compatible"]["max_score"]
        == 0.82
    )
    assert (
        threshold_by_class["compatible"]["class_rank"]
        == 2
    )

    assert (
        threshold_by_class["very_high"]["min_score"]
        == 0.82
    )
    assert (
        threshold_by_class["very_high"]["max_score"]
        == 1.0
    )
    assert (
        threshold_by_class["very_high"]["class_rank"]
        == 3
    )

    model_thresholds = data["model"]["metadata"]["thresholds"]

    assert set(model_thresholds) == {
        "low",
        "compatible",
        "very_high",
    }
    assert (
        model_thresholds["compatible"]["maximum_exclusive"]
        == 0.82
    )


def test_v4_1_entity_summary_contract():
    response = client.get(
        "/areas/summary?entity_id=calabria_demo"
    )

    assert response.status_code == 200

    data = response.json()

    assert (
        data["catalog_view"]
        == "area_catalog_v4_1_entity_scope"
    )
    assert data["catalog_status"] == "validated_not_promoted"
    assert data["entity"]["entity_id"] == "calabria_demo"


def test_v4_1_area_detail_contract():
    list_response = client.get(
        "/areas?priority_only=true&limit=1"
    )

    assert list_response.status_code == 200

    area_id = list_response.json()["items"][0]["area_id"]

    response = client.get(f"/areas/{area_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v4_1_diagnostic"
    assert data["catalog_status"] == "validated_not_promoted"
    assert data["area"]["area_id"] == area_id
    assert data["area"]["reliability_class"] == "very_high"
