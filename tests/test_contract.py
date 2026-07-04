import main


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
            "applicable": True,
            "p_value": 1.0,
            "significant": False,
            "expected_false_positive_rate": 0.0125,
            "observed_rate": 0.0,
            "n_anomalous_pixels": 0,
            "n_anomalous_effective_pixels": 0,
            "n_total_pixels": 38,
            "n_effective_pixels": 10,
            "spatial_independence_assumed": False,
            "note": "Test binomiale conservativo."
        },
        "multivariateNormalityFlag": {
            "reliable": False,
            "flagged_components": [
                "normalita_multivariata_non_verificata"
            ],
            "skewness": [],
            "excess_kurtosis": [],
            "note": "Normalità multivariata non pienamente verificata."
        },
        "vdi": {
            "score": None,
            "class": None,
            "window_days": None,
            "r_squared": None,
            "confidence": None,
            "t_statistic": None,
            "n_observations": None,
            "n_effective": None,
            "lag1_autocorrelation": None
        },
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
        "mapLayers": {},
        "mapSnapshots": {}
    }

    validated = main.AnalysisResultContract.model_validate(payload)

    assert validated.totalArea == 0.38
    assert validated.analysisStatus.code == "no_priority"
    assert validated.priorityAreas[0].class_id == 1
    assert validated.agronomicContext.priority_percent == 0.0


def test_anomaly_threshold_accepts_warning_only_payload():
    payload = {
        "method": "hotelling_t2_corrected",
        "note": "Campo con pochi pixel validi.",
        "warning": "Soglia calcolata con cautela."
    }

    validated = main.AnomalyThreshold.model_validate(payload)

    assert validated.method == "hotelling_t2_corrected"
    assert validated.mahalanobis_threshold is None
    assert validated.note == "Campo con pochi pixel validi."