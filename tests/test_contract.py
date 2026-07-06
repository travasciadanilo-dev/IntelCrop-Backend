from schemas import AnalysisResultContract, AnomalyThreshold


def test_analysis_result_contract_accepts_minimal_valid_payload():
    payload = {
        "totalArea": 0.38,
        "lastImageDate": "2026-07-04",
        "priorityAreas": [
            {
                "class": 1,
                "label": "Nessuna priorità",
                "color": "#1A7A3A",
                "area_ha": 0.38,
                "percent": 100.0
            }
        ],
        "analysisStatus": {
            "code": "no_priority",
            "label": "Nessuna priorità rilevata",
            "severity": "good",
            "color": "#1A7A3A",
            "description": "La Response Map non evidenzia aree prioritarie.",
            "recommended_action": "Continuare il monitoraggio."
        },
        "analysisReliability": {
            "level": "low",
            "label": "Bassa",
            "reasons": [
                "campione_pixel_insufficiente",
                "segnale_non_distinguibile_dal_rumore"
            ],
            "note": "Affidabilità statistica del segnale."
        },
        "fieldSignificance": {
            "applicable": False,
            "p_value": None,
            "significant": None,
            "expected_false_positive_rate": 0.0125,
            "observed_rate": 0.0,
            "n_anomalous_pixels": 0,
            "n_anomalous_effective_pixels": None,
            "n_total_pixels": 38,
            "n_effective_pixels": None,
            "spatial_independence_assumed": False,
            "note": "Significatività avanzata non calcolata."
        },
        "multivariateNormalityFlag": {
            "reliable": None,
            "flagged_components": [],
            "skewness": [],
            "excess_kurtosis": [],
            "note": "Normalità multivariata non calcolata."
        },
        "vdi": {
            "score": None,
            "class": None,
            "window_days": None,
            "r_squared": None,
            "confidence": "not_computed_fast_mode",
            "t_statistic": None,
            "n_observations": None,
            "n_effective": None,
            "lag1_autocorrelation": None,
            "note": "VDI non calcolato in modalità fast."
        },
        "vdiData": [],
        "trendData": [],
        "agronomicContext": {
            "ordinary_percent": 100.0,
            "high_performance_percent": 0.0,
            "priority_percent": 0.0,
            "priority_area_ha": 0.0,
            "emerging_percent": 0.0,
            "confirmed_percent": 0.0,
            "persistent_percent": 0.0,
            "confirmed_priority_percent": 0.0,
            "attention_level": "none",
            "vdi_class": None,
            "vdi_score": None
        },
        "anomalyThreshold": {
            "method": "hotelling_t2_corrected",
            "mahalanobis_threshold": 3.9498,
            "alpha": 0.975,
            "df": 5
        },
        "dataQuality": {
            "temporally_consistent": True,
            "last3_gap_days": 3,
            "last3_gap_warning_days": 35,
            "valid_observations": 54
        },
        "landcoverSubtype": {
            "subtype": "olive_pure",
            "subtype_label_it": "Oliveto puro",
            "subtype_confidence": "high",
            "subtype_layer_version": "cut_calabria_v1",
            "coverage_ratio": 1.0,
            "coverage_percent": 100.0,
            "matched_subtypes": [
                {
                    "subtype": "olive_pure",
                    "label_it": "Oliveto puro",
                    "source_layer_version": "cut_calabria_v1",
                    "overlap_m2": 40221.59,
                    "field_area_m2": 40221.6,
                    "coverage_ratio": 1.0,
                    "coverage_percent": 100.0
                }
            ],
            "landcover_qc_version": "olive_pure_geom_qc_v2",
            "landcover_qc_class": "high_confidence",
            "usable_for_baseline": True,
            "matching_layer": "landcover_olive_pure_high_confidence_v2",
            "note": "Tipologia di impianto assegnata da intersezione spaziale CUT. Non rappresenta cultivar."
        },
        "mapLayers": {},
        "mapSnapshots": {},
        "analysisProfile": {
            "profile": "operational_fast",
            "compute_trend_data": False,
            "compute_field_significance": False,
            "compute_normality_diagnostics": False,
            "compute_vdi": False,
            "generate_response_map_layer": True,
            "generate_index_map_layers": False,
            "generate_map_snapshots": False
        },
        "contractValidation": {
            "valid": True,
            "mode": "warn"
        }
    }

    validated = AnalysisResultContract.model_validate(payload)

    assert validated.totalArea == 0.38
    assert validated.analysisStatus.code == "no_priority"
    assert validated.priorityAreas[0].class_id == 1
    assert validated.agronomicContext.priority_percent == 0.0
    assert validated.analysisProfile.profile == "operational_fast"
    
    assert validated.landcoverSubtype.subtype == "olive_pure"
    assert validated.landcoverSubtype.subtype_confidence == "high"
    assert validated.landcoverSubtype.subtype_layer_version == "cut_calabria_v1"
    assert validated.landcoverSubtype.landcover_qc_version == "olive_pure_geom_qc_v2"
    assert validated.landcoverSubtype.landcover_qc_class == "high_confidence"
    assert validated.landcoverSubtype.usable_for_baseline is True
    assert validated.landcoverSubtype.matching_layer == "landcover_olive_pure_high_confidence_v2"


def test_anomaly_threshold_accepts_warning_only_payload():
    payload = {
        "method": "hotelling_t2_corrected",
        "note": "Campo con pochi pixel validi.",
        "warning": "Soglia calcolata con cautela."
    }

    validated = AnomalyThreshold.model_validate(payload)

    assert validated.method == "hotelling_t2_corrected"
    assert validated.mahalanobis_threshold is None
    assert validated.note == "Campo con pochi pixel validi."


def test_analysis_result_schema_is_exportable():
    schema = AnalysisResultContract.model_json_schema()

    assert schema["title"] == "AnalysisResultContract"
    assert "properties" in schema
    assert "totalArea" in schema["properties"]
    assert "priorityAreas" in schema["properties"]
    assert "analysisStatus" in schema["properties"]
    assert "analysisReliability" in schema["properties"]
    assert "fieldSignificance" in schema["properties"]