from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_areas_summary_contract():
    response = client.get("/areas/summary")

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v1_diagnostic"
    assert data["catalog_status"] == "diagnostic_not_final"

    assert data["totals"]["n_total"] == 40261
    assert data["totals"]["n_priority_candidates"] == 2706

    classes = {
        row["reliability_class"]: row["n"]
        for row in data["by_class"]
    }

    assert classes["low"] == 26766
    assert classes["compatible"] == 10789
    assert classes["high"] == 1664
    assert classes["very_high"] == 1042


def test_areas_priority_contract():
    response = client.get("/areas?priority_only=true&limit=5")

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v1_diagnostic"
    assert data["catalog_status"] == "diagnostic_not_final"
    assert data["total_matching"] == 2706
    assert data["limit"] == 5
    assert data["offset"] == 0
    assert len(data["items"]) == 5

    for item in data["items"]:
        assert item["catalog_priority_candidate"] is True
        assert item["reliability_class"] in {"high", "very_high"}
        assert item["reliability_model_version"] == "regional_reliability_score_exp_v3"
        assert item["reliability_model_status"] == "experimental"
        assert item["catalog_version"] == "area_catalog_v1_diagnostic"
        assert item["catalog_status"] == "diagnostic_not_final"


def test_areas_very_high_contract():
    response = client.get("/areas?reliability_class=very_high&limit=5")

    assert response.status_code == 200

    data = response.json()

    assert data["total_matching"] == 1042
    assert len(data["items"]) == 5

    for item in data["items"]:
        assert item["reliability_class"] == "very_high"
        assert item["reliability_rank"] == 4


def test_areas_geojson_contract():
    response = client.get(
        "/areas?priority_only=true&output_format=geojson&limit=5"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["type"] == "FeatureCollection"
    assert data["metadata"]["catalog_view"] == "area_catalog_v1_diagnostic"
    assert data["metadata"]["catalog_status"] == "diagnostic_not_final"
    assert data["metadata"]["total_matching"] == 2706
    assert data["metadata"]["limit"] == 5
    assert data["metadata"]["offset"] == 0

    assert len(data["features"]) == 5

    for feature in data["features"]:
        assert feature["type"] == "Feature"
        assert feature["geometry"] is not None
        assert feature["properties"]["catalog_priority_candidate"] is True
        assert feature["properties"]["reliability_class"] in {"high", "very_high"}


def test_areas_invalid_reliability_class():
    response = client.get("/areas?reliability_class=wrong_class")

    assert response.status_code == 400


def test_areas_invalid_bbox():
    response = client.get("/areas?bbox=16,39,15,38")

    assert response.status_code == 400

def test_areas_entity_summary_contract():
    response = client.get("/areas/summary?entity_id=calabria_demo")

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v1_entity_scope"
    assert data["catalog_status"] == "diagnostic_not_final"
    assert data["entity"]["entity_id"] == "calabria_demo"
    assert data["entity"]["entity_status"] == "active"

    assert data["totals"]["n_total"] == 40261
    assert data["totals"]["n_priority_candidates"] == 2706


def test_areas_entity_priority_contract():
    response = client.get(
        "/areas?entity_id=calabria_demo&priority_only=true&limit=5"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["catalog_view"] == "area_catalog_v1_entity_scope"
    assert data["catalog_status"] == "diagnostic_not_final"
    assert data["entity"]["entity_id"] == "calabria_demo"
    assert data["total_matching"] == 2706
    assert len(data["items"]) == 5

    for item in data["items"]:
        assert item["entity_id"] == "calabria_demo"
        assert item["entity_status"] == "active"
        assert item["catalog_priority_candidate"] is True
        assert item["reliability_class"] in {"high", "very_high"}


def test_areas_entity_not_found():
    response = client.get("/areas?entity_id=wrong_entity&limit=5")

    assert response.status_code == 404