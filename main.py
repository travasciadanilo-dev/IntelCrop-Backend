"""
IntelCrop GEE API - main.py
============================================================
CHANGELOG v3 (rafforzamento rigore statistico) - VERSIONE FINALE
============================================================
Questo changelog documenta TUTTI gli interventi applicati.

I1  - MAHAL_ALPHA alzato da 0.85 a 0.975 (default)
I2  - Test di significativita' a livello di campo con correzione per
      autocorrelazione spaziale (n_eff stimata euristica)
I3  - Filtro spaziale (smoothing + cluster minimo)
I4  - Persistence direzionale (separata per positivo/negativo)
I5  - Covarianza robusta via reweighting iterativo
I6  - Shrinkage adattivo (approssimato) verso bersaglio diagonale
I7  - Soglia Hotelling T^2 corretta per campione finito
I8  - VDI con t-statistic standardizzato
I9  - Correzione per autocorrelazione temporale (n_eff)
I10 - Direction score continuo (somma di z-score)
I11 - Diagnostica di normalita' multivariata (informativa)

FIX AGGIUNTIVI (rispetto a v3 base):
F1  - Calibrazione del test binomiale I2 (one_sided_fraction=0.5)
F2  - Fallback silenzioso di hotelling_threshold con fattore di sicurezza
F3  - Mediana/MAD robuste con reweighting iterativo (z-score su pixel puliti)
F4  - Collegamento multivariateNormalityFlag alla confidenza
F5  - Consolidamento in analysisReliability (fonte unica di verita')
F6  - Validazione automatica dei risultati (validate_analysis_results)
F7  - Test di validazione statistica all'avvio (run_statistical_validations)
F8  - Protezione API: CORS ristretto + API Key opzionale
F9  - Patch field_level_significance con n_anomalous_eff
F10 - analysisStatus canonico per frontend
F11 - Integrazione python-dotenv per variabili d'ambiente
F12 - Configurazione API migliorata: ENV, limiti campo, rate limit
F13 - Security: rate limit, audit log, validazione area pre-GEE
F14 - Contratto dati tipizzato con Pydantic (FASE 1)
F15 - Aggiornamento a Pydantic v2 (ConfigDict)
F16 - CORS robusto con logging e headers espliciti
F17 - Debug esteso su validazione contratto (logging completo)
F18 - Validazione contratto NON bloccante (warning + flag)
============================================================
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from schemas import AnalysisResultContract
import ee
import json
import datetime
import math
import traceback
from statistics import median
from scipy.stats import chi2, f as f_dist, binomtest
import os
import time
import hashlib
import uuid
import numpy as np
from dotenv import load_dotenv

# F11: Carica variabili d'ambiente da .env
load_dotenv()

app = FastAPI(title="IntelCrop GEE API")

# ================================================================
# F8 + F16: CORS RISTRETTO CON LOGGING
# ================================================================
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

ALLOWED_ORIGINS_LIST = [
    origin.strip()
    for origin in ALLOWED_ORIGINS
    if origin.strip()
]

print("[INFO] CORS allowed origins:", ALLOWED_ORIGINS_LIST)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS_LIST,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-API-Key",
        "Accept",
        "Origin",
        "Authorization"
    ],
)

# ================================================================
# F8 + F12: CONFIGURAZIONE API
# ================================================================
ENV = os.getenv("ENV", "production")
INTELCROP_API_KEY = os.getenv("INTELCROP_API_KEY")

MAX_FIELD_AREA_HA = float(os.getenv("MAX_FIELD_AREA_HA", "100"))
MIN_FIELD_AREA_HA = float(os.getenv("MIN_FIELD_AREA_HA", "0.05"))
ANALYZE_RATE_LIMIT_PER_HOUR = int(os.getenv("ANALYZE_RATE_LIMIT_PER_HOUR", "30"))

# Profili di analisi e controlli condizionali
ANALYSIS_PROFILE = os.getenv("ANALYSIS_PROFILE", "operational_fast")

COMPUTE_FIELD_SIGNIFICANCE = os.getenv("COMPUTE_FIELD_SIGNIFICANCE", "false").lower() == "true"
COMPUTE_VDI = os.getenv("COMPUTE_VDI", "false").lower() == "true"

GENERATE_RESPONSE_MAP_LAYER = os.getenv("GENERATE_RESPONSE_MAP_LAYER", "true").lower() == "true"
GENERATE_INDEX_MAP_LAYERS = os.getenv("GENERATE_INDEX_MAP_LAYERS", "false").lower() == "true"
GENERATE_MAP_SNAPSHOTS = os.getenv("GENERATE_MAP_SNAPSHOTS", "false").lower() == "true"

DEBUG_ANALYSIS = os.getenv("DEBUG_ANALYSIS", "false").lower() == "true"
ANALYSIS_TIMING = os.getenv("ANALYSIS_TIMING", "true").lower() == "true"

# Step 3: Aggiungi configurazione per la validazione del contratto
CONTRACT_VALIDATION_MODE = os.getenv("CONTRACT_VALIDATION_MODE", "warn").lower()

# F13: Rate limit state
RATE_LIMIT_STATE = {}
RATE_LIMIT_WINDOW_SECONDS = 3600

# ================================================================
# F13: VERIFY API KEY (con hash)
# ================================================================

def verify_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
    """
    Protezione minima dell'API.
    In produzione INTELCROP_API_KEY è obbligatoria.
    Solo ENV=local_dev consente una modalità locale esplicita.
    """
    if not INTELCROP_API_KEY:
        if ENV == "local_dev":
            return "local_dev"

        raise HTTPException(
            status_code=500,
            detail="Configurazione API non valida."
        )

    if x_api_key != INTELCROP_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    return hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()[:16]


# Inizializza GEE
try:
    ee.Initialize()
    print("[INFO] Google Earth Engine inizializzato correttamente")
except Exception as e:
    print("[ERROR] Errore inizializzazione Google Earth Engine:", str(e))
    raise


class FieldRequest(BaseModel):
    geojson: Dict[str, Any]
    cloud_threshold: float = 0.40
    valid_pixel_threshold: float = 10.0
    mahal_alpha: float = 0.975
    min_cluster_pixels: int = 4
    apply_spatial_smoothing: bool = True
    covariance_shrinkage: float = 0.15
    robust_covariance_iterations: int = 2


@app.get("/")
def root():
    return {"status": "IntelCrop GEE API online"}


@app.get("/test-gee")
def test_gee():
    try:
        print("[DEBUG] Endpoint /test-gee chiamato")
        test = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')\
            .filterDate('2026-01-01', '2026-06-01')\
            .filterBounds(ee.Geometry.Point([15.0, 41.0]))\
            .size().getInfo()
        return {
            "status": "ok",
            "images_found": test,
            "message": f"Connessione GEE OK. Trovate {test} immagini Sentinel-2 per il punto di test (15.0, 41.0)"
        }
    except Exception as e:
        error_detail = traceback.format_exc()
        print("ERRORE COMPLETO test-gee:", error_detail)
        return {
            "status": "error",
            "detail": str(e),
            "traceback": error_detail,
            "message": "Errore di connessione a Google Earth Engine. Verifica autenticazione e progetto."
        }


# ================================================================
# F13: HELPER SICUREZZA E VALIDAZIONE
# ================================================================

def audit_log(event: str, **kwargs):
    """
    Audit log minimo.
    Non salva il GeoJSON completo, ma solo hash e metadati essenziali.
    """
    safe_payload = {
        "event": event,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        **kwargs
    }
    print("[AUDIT]", json.dumps(safe_payload, ensure_ascii=False))


def rate_limit_check(auth_id: str):
    """
    Rate limit semplice in memoria per API key / local_dev.
    È sufficiente per MVP locale/single instance.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    history = RATE_LIMIT_STATE.get(auth_id, [])
    history = [ts for ts in history if ts >= window_start]

    if len(history) >= ANALYZE_RATE_LIMIT_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="Limite orario di analisi raggiunto. Riprova più tardi."
        )

    history.append(now)
    RATE_LIMIT_STATE[auth_id] = history


def geojson_hash(geojson: Dict[str, Any]) -> str:
    """
    Hash stabile della geometria per audit log.
    """
    try:
        payload = json.dumps(geojson, sort_keys=True)
    except Exception:
        payload = str(geojson)

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _ring_area_m2(coords: List[List[float]]) -> float:
    """
    Area approssimata di un ring lon/lat in m² con proiezione equirettangolare locale.
    Sufficiente per validazione preliminare min/max prima di Earth Engine.
    """
    if not coords or len(coords) < 4:
        return 0.0

    mean_lat = sum(float(p[1]) for p in coords) / len(coords)
    lat_factor = 111320.0
    lon_factor = 111320.0 * math.cos(math.radians(mean_lat))

    points = [
        (float(p[0]) * lon_factor, float(p[1]) * lat_factor)
        for p in coords
        if len(p) >= 2
    ]

    if len(points) < 4:
        return 0.0

    area = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        area += x1 * y2 - x2 * y1

    return abs(area) / 2.0


def _polygon_area_m2(polygon_coords: List[Any]) -> float:
    """
    Area Polygon GeoJSON.
    Primo ring = esterno; ring successivi = eventuali buchi.
    """
    if not polygon_coords:
        return 0.0

    outer = _ring_area_m2(polygon_coords[0])
    holes = sum(_ring_area_m2(ring) for ring in polygon_coords[1:])
    return max(0.0, outer - holes)


def calculate_geojson_area_ha(geojson: Dict[str, Any]) -> float:
    """
    Calcola area approssimata in ettari per:
    - FeatureCollection
    - Feature
    - Polygon
    - MultiPolygon

    Serve solo per bloccare richieste fuori scala prima di GEE.
    """
    if not geojson:
        return 0.0

    geo_type = geojson.get("type")

    if geo_type == "FeatureCollection":
        return sum(
            calculate_geojson_area_ha(feature)
            for feature in geojson.get("features", [])
        )

    if geo_type == "Feature":
        return calculate_geojson_area_ha(geojson.get("geometry", {}))

    if geo_type == "Polygon":
        area_m2 = _polygon_area_m2(geojson.get("coordinates", []))
        return area_m2 / 10000.0

    if geo_type == "MultiPolygon":
        area_m2 = sum(
            _polygon_area_m2(poly)
            for poly in geojson.get("coordinates", [])
        )
        return area_m2 / 10000.0

    return 0.0


def validate_field_area_or_raise(area_ha: float):
    """
    Validazione area prima di qualunque chiamata Earth Engine.
    """
    if area_ha <= 0:
        raise HTTPException(
            status_code=422,
            detail="Area di studio non valida."
        )

    if area_ha < MIN_FIELD_AREA_HA:
        raise HTTPException(
            status_code=422,
            detail=f"Area troppo piccola. Superficie minima consentita: {MIN_FIELD_AREA_HA:.2f} ha."
        )

    if area_ha > MAX_FIELD_AREA_HA:
        raise HTTPException(
            status_code=422,
            detail=f"Area troppo grande. Superficie massima consentita: {MAX_FIELD_AREA_HA:.2f} ha."
        )


# ================================================================
# TEST DI VALIDAZIONE STATISTICA (F7)
# ================================================================

def run_statistical_validations():
    """
    Esegue test di validazione delle funzioni statistiche pure
    (senza dipendenza da GEE). Chiamata all'avvio dell'app.
    """
    print("=" * 70)
    print("AVVIO: Validazione funzioni statistiche")
    print("=" * 70)
    
    # Test 1: convergenza di hotelling_threshold
    print("\n[TEST 1] hotelling_threshold - convergenza a chi2...")
    p, alpha = 5, 0.975
    chi2_ref = chi2.ppf(alpha, p) ** 0.5
    
    for n in [10, 20, 50, 200, 5000]:
        th = hotelling_threshold(n, p, alpha)
        print(f"  n={n:>7} -> soglia={th:.4f}  (chi2 rif.={chi2_ref:.4f})")
    
    # Test 2: verifica che per n<=p sia conservativo
    th_small = hotelling_threshold(p, p, alpha)
    th_large = hotelling_threshold(1000, p, alpha)
    if th_small > th_large:
        print("  OK: soglia più alta per campione piccolo (conservativa)")
    else:
        print("  WARN: soglia per campione piccolo NON è più alta")
    
    print("\n[TEST 2] field_level_significance - test di calibrazione...")
    # Simula H0 con correzione spaziale
    np.random.seed(42)
    n_tests = 1000
    n_total = 100
    alpha = 0.975
    expected_rate = (1.0 - alpha) * 0.5
    
    significant_count = 0
    for _ in range(n_tests):
        n_anomalous = np.random.binomial(n_total, expected_rate)
        result = field_level_significance(
            n_anomalous, n_total, alpha, 
            field_area_ha=5.0, one_sided_fraction=0.5
        )
        if result.get('significant', False):
            significant_count += 1
    
    observed_rate = significant_count / n_tests
    print(f"  Tasso di falsi positivi osservato: {observed_rate:.3f} (atteso ~0.05)")
    if observed_rate < 0.10:
        print("  OK: test ben calibrato")
    else:
        print(f"  WARN: test potrebbe essere ottimistico ({observed_rate:.1%})")
    
    print("\n" + "=" * 70)
    print("VALIDAZIONE COMPLETATA")
    print("=" * 70)


# ================================================================
# HELPER STATISTICI (puro Python)
# ================================================================

def effective_sample_size_spatial(n_total: int, field_area_ha: float) -> int:
    """
    F1: Stima euristica della dimensione campionaria effettiva per dati
    spazialmente correlati. Usa una regola pratica basata sulla densità
    di pixel e sull'area del campo.
    """
    if n_total is None or n_total <= 0:
        return n_total
    
    # Regola euristica: per campi piccoli la correlazione spaziale è più influente
    if field_area_ha is None:
        correction_factor = 0.35
    elif field_area_ha < 1:
        correction_factor = 0.15
    elif field_area_ha < 3:
        correction_factor = 0.25
    elif field_area_ha < 5:
        correction_factor = 0.35
    else:
        correction_factor = 0.50
    
    n_eff = max(10, int(n_total * correction_factor))
    return n_eff


def hotelling_threshold(n: float, p: int, alpha: float, field_area_ha: float = None) -> float:
    """
    I7 + F2: soglia sulla distanza di Mahalanobis corretta per covarianza
    stimata da campione finito di dimensione n.
    
    F2: Per campi piccoli (n <= p) usa un fattore di sicurezza basato su area.
    """
    p = int(p)
    if n is None or n <= p:
        # F2: campione troppo piccolo: usa chi2 con fattore di sicurezza
        base = float(chi2.ppf(alpha, p)) ** 0.5
        if field_area_ha is not None and field_area_ha < 1:
            safety_factor = 1.25
        elif field_area_ha is not None and field_area_ha < 3:
            safety_factor = 1.15
        else:
            safety_factor = 1.0
        return base * safety_factor
    
    try:
        f_val = float(f_dist.ppf(alpha, p, n - p))
        t2 = p * (n - 1) / (n - p) * f_val
        return t2 ** 0.5
    except ValueError:
        # Fallback sicuro
        base = float(chi2.ppf(alpha, p)) ** 0.5
        return base * 1.1


def field_level_significance(n_anomalous: int, n_total: int, alpha: float,
                              field_area_ha: float = None,
                              one_sided_fraction: float = 0.5) -> Dict[str, Any]:
    """
    I2 + F1 + F9: test binomiale a una coda su scala di campo.
    F1: Calibrazione corretta per anomalie bidirezionali (one_sided_fraction=0.5)
    F1: Correzione per autocorrelazione spaziale (n_eff stimata)
    F9: Patch per n_anomalous_eff quando n_eff < n_anomalous grezzo
    """
    expected_rate = (1.0 - alpha) * one_sided_fraction
    if n_total is None or n_total <= 0:
        return {
            "applicable": False,
            "p_value": None,
            "significant": None,
            "expected_false_positive_rate": expected_rate,
            "observed_rate": None,
            "note": "Numero di pixel totali non disponibile."
        }
    
    # F1: correzione per autocorrelazione spaziale
    if field_area_ha is not None:
        n_eff = effective_sample_size_spatial(n_total, field_area_ha)
    else:
        n_eff = n_total
    
    # F9: scala n_anomalous in proporzione a n_eff
    raw_observed_rate = n_anomalous / n_total if n_total > 0 else 0
    n_anomalous_eff = int(round(raw_observed_rate * n_eff))
    n_anomalous_eff = max(0, min(n_anomalous_eff, int(n_eff)))
    
    observed_rate = n_anomalous_eff / n_eff if n_eff > 0 else 0
    
    try:
        result = binomtest(
            int(n_anomalous_eff),
            int(n_eff),
            p=expected_rate,
            alternative='greater'
        )
        p_value = float(result.pvalue)
    except Exception:
        p_value = None
    
    significant = (p_value is not None) and (p_value < 0.05)
    
    return {
        "applicable": True,
        "p_value": round(p_value, 6) if p_value is not None else None,
        "significant": significant,
        "expected_false_positive_rate": round(expected_rate, 4),
        "observed_rate": round(observed_rate, 4),
        "n_anomalous_pixels": int(n_anomalous),
        "n_anomalous_effective_pixels": int(n_anomalous_eff),
        "n_total_pixels": int(n_total),
        "n_effective_pixels": int(n_eff),
        "spatial_independence_assumed": False,
        "note": (
            "Test binomiale a livello di campo con correzione per "
            "autocorrelazione spaziale (n_eff stimata euristica). "
            "Il p-value è più conservativo rispetto alla versione precedente."
        )
    }


def standardized_vdi_regression(xs: List[float], ys: List[float]) -> Dict[str, Any]:
    """
    I8 + I9: regressione lineare su giorni reali con:
      - pendenza standardizzata (t-statistic = slope / SE(slope))
      - correzione per autocorrelazione lag-1 (n_eff)
    """
    n = len(xs)
    if n < 2:
        return None

    meanX = sum(xs) / n
    meanY = sum(ys) / n

    ssXY = sum((x - meanX) * (y - meanY) for x, y in zip(xs, ys))
    ssXX = sum((x - meanX) ** 2 for x in xs)
    ssYY = sum((y - meanY) ** 2 for y in ys)

    if ssXX <= 0:
        return None

    slope = ssXY / ssXX
    r_squared = (ssXY ** 2) / (ssXX * ssYY) if ssYY > 0 else 0.0

    residuals = [y - (meanY + slope * (x - meanX)) for x, y in zip(xs, ys)]
    ssRes = sum(r ** 2 for r in residuals)

    lag1_autocorr = None
    n_eff = float(n)
    if n >= 4:
        num = sum(residuals[i] * residuals[i - 1] for i in range(1, n))
        den = sum(r ** 2 for r in residuals)
        if den > 0:
            lag1_autocorr = num / den
            r1_clipped = max(min(lag1_autocorr, 0.95), -0.95)
            n_eff = max(2.0, n * (1 - r1_clipped) / (1 + r1_clipped))

    if n_eff > 2 and ssXX > 0:
        residual_variance = ssRes / (n_eff - 2)
        se_slope = math.sqrt(max(residual_variance, 0.0) / ssXX)
        t_stat = slope / se_slope if se_slope > 0 else None
    else:
        se_slope = None
        t_stat = None

    return {
        "slope": slope,
        "r_squared": r_squared,
        "t_stat": t_stat,
        "se_slope": se_slope,
        "n_observations": n,
        "n_effective": round(n_eff, 2),
        "lag1_autocorrelation": round(lag1_autocorr, 4) if lag1_autocorr is not None else None,
    }


def classify_vdi_from_tstat(t_stat: Optional[float]) -> str:
    """
    I8: classi basate sulla pendenza standardizzata (t-statistic)
    """
    if t_stat is None:
        return "Insufficient statistical confidence"

    abs_t = abs(t_stat)

    if abs_t < 1.0:
        return "Stable"

    if t_stat < 0:
        if abs_t < 2.0:
            return "Slight Recovery"
        if abs_t < 3.0:
            return "Moderate Recovery"
        return "Strong Recovery"
    else:
        if abs_t < 2.0:
            return "Slight Divergence"
        if abs_t < 3.0:
            return "Moderate Divergence"
        return "Strong Divergence"


def confidence_from_tstat(t_stat: Optional[float], n_eff: Optional[float]) -> str:
    """
    I9: confidenza basata su t-stat e n_eff (corretto per autocorrelazione)
    """
    if t_stat is None or n_eff is None:
        return "not_applicable"

    abs_t = abs(t_stat)

    if n_eff < 4:
        return "low"

    if abs_t >= 2.5 and n_eff >= 5:
        return "high"
    elif abs_t >= 1.5:
        return "medium"
    else:
        return "low"


def skew_kurtosis_flag(skew_values: List[Optional[float]], kurt_values: List[Optional[float]]) -> Dict[str, Any]:
    """
    I11: diagnostica di normalita' multivariata (informativa)
    """
    flagged = []
    for i, (s, k) in enumerate(zip(skew_values, kurt_values)):
        if s is not None and abs(s) > 1.0:
            flagged.append(f"skew_index_{i}")
        if k is not None and abs(k) > 3.0:
            flagged.append(f"kurtosis_index_{i}")

    reliable = len(flagged) == 0

    return {
        "reliable": reliable,
        "flagged_components": flagged,
        "skewness": [round(s, 3) if s is not None else None for s in skew_values],
        "excess_kurtosis": [round(k, 3) if k is not None else None for k in kurt_values],
        "note": (
            "Diagnostica marginale (non un test multivariato formale di "
            "Mardia, non disponibile lato server GEE). Se reliable=false, "
            "l'assunzione di normalita' multivariata su cui si basa la "
            "soglia chi2/Hotelling e' meno solida per questa data/campo."
        )
    }


# ================================================================
# F6: VALIDAZIONE AUTOMATICA DEI RISULTATI
# ================================================================

def validate_analysis_results(results: Dict[str, Any], field_geojson: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    F6: Validazione automatica dei risultati dell'analisi.
    Restituisce warning se qualcosa non è statisticamente plausibile.
    """
    warnings = []
    
    # TEST 1: Somma delle percentuali delle classi
    total_pct = sum([a.get('percent', 0) for a in results.get('priorityAreas', [])])
    if abs(total_pct - 100) > 2:
        warnings.append({
            'type': 'sum_class_percent',
            'message': f'La somma delle percentuali delle classi è {total_pct:.1f}% (dovrebbe essere 100%)',
            'severity': 'medium'
        })
    
    # TEST 2: Superficie prioritaria eccessiva
    priority_pct = results.get('agronomicContext', {}).get('priority_percent', 0)
    if priority_pct > 50:
        warnings.append({
            'type': 'excessive_priority_area',
            'message': f'Le aree prioritarie coprono il {priority_pct:.1f}% del campo (soglia >50%)',
            'severity': 'high'
        })
    
    # TEST 3: Campo troppo piccolo
    total_area_ha = results.get('totalArea', 0)
    if total_area_ha < 0.5:
        warnings.append({
            'type': 'field_too_small',
            'message': f'Campo di {total_area_ha:.2f} ha (minimo raccomandato: 0.5 ha)',
            'severity': 'medium'
        })
    
    # TEST 4: Osservazioni insufficienti
    n_obs = len(results.get('trendData', []))
    if n_obs < 10:
        warnings.append({
            'type': 'insufficient_observations',
            'message': f'Solo {n_obs} osservazioni valide (minimo raccomandato: 10)',
            'severity': 'high' if n_obs < 5 else 'medium'
        })
    
    # TEST 5: Pixel insufficienti per covarianza
    n_pixels = results.get('dataQualityDiagnostic', {}).get('valid_pixels_for_covariance', 0)
    if n_pixels < 100:
        warnings.append({
            'type': 'insufficient_pixels',
            'message': f'Solo {n_pixels} pixel validi per la stima di covarianza (minimo: 100)',
            'severity': 'high' if n_pixels < 50 else 'medium'
        })
    
    # TEST 6: Disallineamento significatività-estensione
    field_sig = results.get('fieldSignificance', {})
    if field_sig.get('applicable') and field_sig.get('significant') is False:
        if priority_pct > 5:
            warnings.append({
                'type': 'field_significance_mismatch',
                'message': f'Priorità {priority_pct:.1f}% ma campo non significativo statisticamente',
                'severity': 'high'
            })
    
    # TEST 7: Coerenza VDI
    vdi = results.get('vdi', {})
    if vdi.get('confidence') == 'high' and vdi.get('n_effective', 0) < 5:
        warnings.append({
            'type': 'vdi_confidence_mismatch',
            'message': f'VDI confidenza "alta" con n_eff={vdi.get("n_effective")} (minimo: 5)',
            'severity': 'medium'
        })
    
    # TEST 8: VDI e significatività disallineati
    if vdi.get('class') in ['Strong Divergence', 'Moderate Divergence'] and field_sig.get('significant') is False:
        warnings.append({
            'type': 'vdi_field_sig_mismatch',
            'message': f'VDI "{vdi.get("class")}" ma campo non significativo statisticamente',
            'severity': 'high'
        })
    
    # TEST 9: Gap temporale tra acquisizioni
    data_quality = results.get('dataQuality', {})
    if data_quality.get('temporally_consistent') is False:
        gap = data_quality.get('last3_gap_days', 0)
        warnings.append({
            'type': 'temporal_gap',
            'message': f'Gap di {gap} giorni tra le ultime 3 acquisizioni (soglia: 35 giorni)',
            'severity': 'medium'
        })
    
    # TEST 10: Classe ordinaria mancante
    has_class_1 = any(a.get('class') == 1 for a in results.get('priorityAreas', []))
    if not has_class_1:
        warnings.append({
            'type': 'missing_reference_class',
            'message': 'Nessuna area classificata come "ordinaria" (classe 1)',
            'severity': 'high'
        })
    
    return warnings


def get_analysis_quality(warnings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    F6: Determina la qualità complessiva dell'analisi basata sui warning.
    """
    if not warnings:
        return {
            'level': 'excellent',
            'label': 'Eccellente',
            'description': 'Nessuna anomalia rilevata. I risultati sono statisticamente coerenti.'
        }
    
    has_high = any(w['severity'] == 'high' for w in warnings)
    has_medium = any(w['severity'] == 'medium' for w in warnings)
    n_warnings = len(warnings)
    
    if has_high:
        return {
            'level': 'critical',
            'label': 'Critica',
            'description': f'{n_warnings} problemi rilevati, di cui almeno uno grave. I risultati vanno interpretati con estrema cautela.'
        }
    elif has_medium:
        return {
            'level': 'caution',
            'label': 'Cautela',
            'description': f'{n_warnings} avvisi rilevati. I risultati sono statisticamente plausibili ma richiedono verifica.'
        }
    else:
        return {
            'level': 'good',
            'label': 'Buona',
            'description': 'Nessuna anomalia rilevata. I risultati sono statisticamente coerenti.'
        }


# ================================================================
# F10: ANALYSIS STATUS CANONICO
# ================================================================

def build_analysis_status(priority_pct: float,
                          priority_ha: float,
                          persistent_pct: float,
                          confirmed_priority_pct: float,
                          vdi_class: Optional[str],
                          reliability: Dict[str, Any]) -> Dict[str, Any]:
    """
    F10: Stato operativo canonico dell'analisi.
    Il frontend non deve ricalcolare lo stato: deve mostrare questo oggetto.
    """

    reliability_level = reliability.get("level", "low")

    if priority_pct == 0:
        return {
            "code": "no_priority",
            "label": "Nessuna priorità rilevata",
            "severity": "good",
            "color": "#1A7A3A",
            "description": (
                "La Response Map non evidenzia aree prioritarie di ispezione "
                "nella data analizzata."
            ),
            "recommended_action": (
                "Continuare il monitoraggio nelle prossime acquisizioni Sentinel-2. "
                "Non sono richieste verifiche mirate sulla base dell'analisi attuale."
            )
        }

    if reliability_level == "low":
        return {
            "code": "preliminary_signal",
            "label": "Segnale preliminare",
            "severity": "caution",
            "color": "#D97706",
            "description": (
                "Sono presenti aree evidenziate dalla Response Map, ma "
                "l'affidabilità statistica del segnale è bassa."
            ),
            "recommended_action": (
                "Interpretare il risultato con cautela e verificare il campo solo se "
                "le aree coincidono con evidenze agronomiche note o osservazioni dirette."
            )
        }

    if priority_pct >= 12 or persistent_pct >= 5 or vdi_class == "Strong Divergence":
        return {
            "code": "priority_inspection",
            "label": "Ispezione prioritaria",
            "severity": "critical",
            "color": "#DC2626",
            "description": (
                "Sono presenti aree prioritarie con estensione o persistenza rilevante."
            ),
            "recommended_action": (
                f"Verificare in campo le aree prioritarie, pari al "
                f"{priority_pct:.1f}% della superficie analizzata "
                f"({priority_ha:.2f} ha), dando precedenza alle zone persistenti."
            )
        }

    if priority_pct >= 5 or persistent_pct >= 2 or confirmed_priority_pct >= 5 or vdi_class == "Moderate Divergence":
        return {
            "code": "attention_required",
            "label": "Attenzione richiesta",
            "severity": "warning",
            "color": "#D97706",
            "description": (
                "Sono presenti aree con risposta vegetativa inferiore rispetto "
                "alle zone ordinarie del campo."
            ),
            "recommended_action": (
                f"Concentrare la verifica sulle aree prioritarie, pari al "
                f"{priority_pct:.1f}% della superficie analizzata "
                f"({priority_ha:.2f} ha), confrontandole con aree ordinarie limitrofe."
            )
        }

    return {
        "code": "limited_priority",
        "label": "Priorità limitata",
        "severity": "low",
        "color": "#1A7A3A",
        "description": (
            "Le aree prioritarie sono limitate e non mostrano un livello elevato "
            "di persistenza o conferma temporale."
        ),
        "recommended_action": (
            "Monitorare l'evoluzione nelle prossime acquisizioni e verificare in campo "
            "solo in presenza di evidenze agronomiche coerenti."
        )
    }


# ================================================================
# HELPER TIMING LOG
# ================================================================

def timing_log(request_id: str, label: str, start_time: float) -> float:
    """
    Log leggero dei tempi di esecuzione per individuare i colli di bottiglia.
    """
    if ANALYSIS_TIMING:
        elapsed = time.time() - start_time
        print(f"[TIMING] request_id={request_id} step='{label}' elapsed={elapsed:.2f}s")

    return time.time()


# ================================================================
# STEP 4: VALIDAZIONE CONTRATTO DATI
# ================================================================

def validate_analysis_result_contract_or_raise(
    result: Dict[str, Any],
    request_id: str,
    auth_id: str,
    geom_hash: str
) -> Dict[str, Any]:
    """
    Valida il contratto dati della risposta /analyze.

    CONTRACT_VALIDATION_MODE:
    - off: nessuna validazione
    - warn: logga warning ma non blocca /analyze
    - strict: blocca /analyze se il payload non rispetta il contratto
    """
    if CONTRACT_VALIDATION_MODE == "off":
        result["contractValidation"] = {
            "valid": None,
            "mode": "off"
        }
        return result

    try:
        AnalysisResultContract.model_validate(result)

        result["contractValidation"] = {
            "valid": True,
            "mode": CONTRACT_VALIDATION_MODE
        }

        return result

    except Exception as contract_error:
        contract_error_id = str(uuid.uuid4())[:8]

        print("=" * 80)
        print(f"[WARN] analysis_contract_validation_failed error_id={contract_error_id}")
        print(str(contract_error))
        print("[DEBUG] Result keys:", list(result.keys()) if isinstance(result, dict) else type(result))
        print("=" * 80)

        audit_log(
            "analysis_contract_validation_failed",
            request_id=request_id,
            auth_id=auth_id,
            geometry_hash=geom_hash,
            error_id=contract_error_id,
            mode=CONTRACT_VALIDATION_MODE
        )

        result["contractValidation"] = {
            "valid": False,
            "mode": CONTRACT_VALIDATION_MODE,
            "error_id": contract_error_id
        }

        if CONTRACT_VALIDATION_MODE == "strict":
            raise HTTPException(
                status_code=500,
                detail=f"Errore interno nella validazione del contratto dati. Codice errore: {contract_error_id}"
            )

        return result


# ================================================================
# ANALISI PRINCIPALE
# ================================================================

@app.post("/analyze")
def analyze_field(req: FieldRequest, auth_id: str = Depends(verify_api_key)):
    request_id = str(uuid.uuid4())[:8]
    stage_time = time.time()

    try:
        geojson = req.geojson
        geom_hash = geojson_hash(geojson)

        # F13: rate limit prima dell'elaborazione
        rate_limit_check(auth_id)

        # F13: validazione area PRIMA di qualunque chiamata Earth Engine
        input_area_ha = calculate_geojson_area_ha(geojson)
        validate_field_area_or_raise(input_area_ha)

        audit_log(
            "analyze_requested",
            request_id=request_id,
            auth_id=auth_id,
            geometry_hash=geom_hash,
            input_area_ha=round(input_area_ha, 4),
            analysis_profile=ANALYSIS_PROFILE
        )

        fieldGeom = ee.FeatureCollection(geojson).geometry()

        scale = 10
        cloudThreshold = req.cloud_threshold
        validPixelThreshold = req.valid_pixel_threshold
        mahalAlpha = req.mahal_alpha
        minClusterPixels = max(1, int(req.min_cluster_pixels))
        applySpatialSmoothing = req.apply_spatial_smoothing
        covShrinkage = min(max(req.covariance_shrinkage, 0.0), 0.9)
        robustCovIterations = max(0, int(req.robust_covariance_iterations))

        endDate = ee.Date(datetime.datetime.now().strftime('%Y-%m-%d'))
        startDate = ee.Date.fromYMD(ee.Number(endDate.get('year')), 1, 1)

        print(f"[INFO] Analisi avviata per area GeoJSON")
        print(f"[INFO] Profilo: {ANALYSIS_PROFILE}")
        print(f"[INFO] Periodo: {startDate.getInfo()} - {endDate.getInfo()}")
        print(f"[INFO] Soglie qualita' dato: cloud_threshold={cloudThreshold}, valid_pixel_threshold={validPixelThreshold}")
        print(f"[INFO] Parametri statistici: mahal_alpha={mahalAlpha}, min_cluster_pixels={minClusterPixels}, "
              f"spatial_smoothing={applySpatialSmoothing}, covariance_shrinkage={covShrinkage}, "
              f"robust_cov_iterations={robustCovIterations}")

        # ============================================================
        # CARICAMENTO E FILTRAGGIO DATI
        # ============================================================
        s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
              .filterBounds(fieldGeom)
              .filterDate(startDate, endDate)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 80)))

        csPlus = ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED')

        s2Clear = (s2
            .linkCollection(csPlus, ['cs_cdf'])
            .map(lambda img: img
                .updateMask(img.select('cs_cdf').gte(cloudThreshold))
                .divide(10000)
                .copyProperties(img, ['system:time_start', 'system:index'])
            ))

        def addIndices(img):
            b   = img.select('B2')
            r   = img.select('B4')
            re  = img.select('B5')
            re2 = img.select('B6')
            n   = img.select('B8')
            s1  = img.select('B11')
            s2b = img.select('B12')

            ndvi = n.subtract(r).divide(n.add(r)).rename('NDVI')
            evi  = (n.subtract(r).multiply(2.5)
                    .divide(n.add(r.multiply(6))
                    .subtract(b.multiply(7.5)).add(1))
                    .rename('EVI'))
            ndmi = n.subtract(s1).divide(n.add(s1)).rename('NDMI')
            ndre = n.subtract(re).divide(n.add(re)).rename('NDRE')
            msi  = s1.divide(n).rename('MSI')
            psri = r.subtract(b).divide(re2).rename('PSRI')
            nbr  = n.subtract(s2b).divide(n.add(s2b)).rename('NBR')
            osavi = n.subtract(r).divide(n.add(r).add(0.16)).rename('OSAVI')

            return img.addBands([ndvi, evi, ndmi, ndre, msi, psri, nbr, osavi])

        indexed = s2Clear.map(addIndices)

        def addDate(img):
            d = ee.Date(img.get('system:time_start')).format('YYYY-MM-dd')
            return img.set('date_string', d)

        withDate = indexed.map(addDate)

        # ============================================================
        # DAILY COMPOSITE
        # ============================================================
        uniqueDates = ee.List(withDate.aggregate_array('date_string')).distinct().sort()

        def makeDailyComposite(dateStr):
            dateStr = ee.String(dateStr)
            dayCollection = withDate.filter(ee.Filter.eq('date_string', dateStr))
            return (
                dayCollection.median()
                .set('date_string', dateStr)
                .set('system:time_start', ee.Date(dateStr).millis())
                .set('image_count_same_day', dayCollection.size())
            )

        daily = ee.ImageCollection(uniqueDates.map(makeDailyComposite)).sort('system:time_start')

        def addValidPercent(img):
            v = (ee.Number(img.select('NDVI').mask()
                .reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=fieldGeom, scale=scale, maxPixels=1e9
                ).get('NDVI')).multiply(100))
            return img.set('valid_percent', v)

        clean = (daily
            .map(addValidPercent)
            .filter(ee.Filter.gte('valid_percent', validPixelThreshold))
            .sort('system:time_start'))

        cleanSize = clean.size().getInfo()
        print(f"[INFO] Immagini trovate dopo filtri: {cleanSize}")
        stage_time = timing_log(request_id, "sentinel_images_filtered", stage_time)

        if cleanSize == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Nessuna immagine Sentinel-2 valida trovata per questo campo. "
                       f"Prova a disegnare un campo piu grande (almeno 1-2 ha), una zona diversa, "
                       f"oppure abbassa valid_pixel_threshold per il test di calibrazione."
            )

        # ============================================================
        # TREND DATA
        # ============================================================
        trendBands = ['NDVI', 'EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI', 'NBR', 'OSAVI']

        def makeTrendFeature(img):
            stats = (img.select(trendBands)
                .reduceRegion(reducer=ee.Reducer.median(), geometry=fieldGeom, scale=scale, maxPixels=1e9))
            return (ee.Feature(None, stats)
                .set('date', ee.Date(img.get('system:time_start')).format('YYYY-MM-dd')))

        trendFC = ee.FeatureCollection(clean.map(makeTrendFeature))

        try:
            trendData = trendFC.select(
                ['date', 'NDVI', 'EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI', 'NBR', 'OSAVI']
            ).getInfo()['features']
            trendData = [f['properties'] for f in trendData]
            print(f"[INFO] Trend data estratti: {len(trendData)} record")
        except Exception as e:
            print(f"[WARN] Errore estrazione trend: {str(e)}")
            trendData = []

        stage_time = timing_log(request_id, "trend_data_extracted", stage_time)

        def aggregate_rows_by_date(rows):
            grouped = {}
            for r in rows:
                d = r.get("date")
                if not d:
                    continue
                grouped.setdefault(d, []).append(r)

            aggregated = []
            for d, items in grouped.items():
                out = {"date": d}
                keys = set()
                for item in items:
                    keys.update(item.keys())
                keys.discard("date")
                for key in keys:
                    values = [item.get(key) for item in items if item.get(key) is not None]
                    if values:
                        try:
                            out[key] = median([float(v) for v in values])
                        except Exception:
                            out[key] = values[0]
                    else:
                        out[key] = None
                aggregated.append(out)
            return sorted(aggregated, key=lambda x: x["date"])

        trendData = aggregate_rows_by_date(trendData)

        # ============================================================
        # ULTIME 3 DATE PER MAHALANOBIS
        # ============================================================
        last3Dates = ee.List(clean.aggregate_array('date_string')).distinct().sort().reverse().slice(0, 3)

        last3DatesList = last3Dates.getInfo()
        LAST3_GAP_WARNING_DAYS = 35
        if len(last3DatesList) >= 2:
            parsed_last3 = [datetime.datetime.strptime(d, '%Y-%m-%d') for d in last3DatesList]
            last3GapDays = (max(parsed_last3) - min(parsed_last3)).days
        else:
            last3GapDays = 0
        last3TemporallyConsistent = last3GapDays <= LAST3_GAP_WARNING_DAYS
        print(f"[INFO] Gap temporale tra le 3 date piu' recenti: {last3GapDays} giorni (soglia: {LAST3_GAP_WARNING_DAYS})")

        def makeLast3DailyComposite(dateStr):
            dateStr = ee.String(dateStr)
            dayCollection = clean.filter(ee.Filter.eq('date_string', dateStr))
            return (
                dayCollection.median()
                .set('date_string', dateStr)
                .set('system:time_start', ee.Date(dateStr).millis())
                .set('image_count_same_day', dayCollection.size())
            )

        last3 = ee.ImageCollection(last3Dates.map(makeLast3DailyComposite)).sort('system:time_start')

        selectedIndices = ['EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI']
        MAHAL_DF = len(selectedIndices)

        GOOD_DIRECTION_POSITIVE = {
            'EVI': True, 'NDMI': True, 'NDRE': True, 'MSI': False, 'PSRI': False
        }

        # ============================================================
        # SOGLIE DI RIFERIMENTO
        # ============================================================
        MAHAL_CHI2_NOMINAL = float(chi2.ppf(mahalAlpha, MAHAL_DF)) ** 0.5
        MAHAL_PERMISSIVE_GATING = float(chi2.ppf(0.99, MAHAL_DF)) ** 0.5
        print(f"[INFO] Soglia chi2 nominale (df={MAHAL_DF}, alpha={mahalAlpha}): {MAHAL_CHI2_NOMINAL:.4f}")

        # ============================================================
        # FUNZIONI STATISTICHE GEE
        # ============================================================
        def robust_zScoreBands(img, mask=None):
            """
            F3: Versione robusta di zScoreBands con calcolo di mediana/MAD
            opzionalmente su pixel mascherati (esclusi quelli già identificati
            come anomalie nelle iterazioni precedenti).
            """
            zList = []
            for name in selectedIndices:
                band = img.select(name)
                
                if mask is not None:
                    band_masked = band.updateMask(mask)
                else:
                    band_masked = band
                
                med = ee.Number(band_masked.reduceRegion(
                    reducer=ee.Reducer.median(), geometry=fieldGeom, scale=scale, maxPixels=1e9
                ).get(name))
                mad = ee.Number(band_masked.subtract(med).abs().reduceRegion(
                    reducer=ee.Reducer.median(), geometry=fieldGeom, scale=scale, maxPixels=1e9
                ).get(name))
                mad = ee.Number(ee.Algorithms.If(mad.eq(0), 0.0001, mad))
                z = band.subtract(med).divide(mad.multiply(1.4826)).rename(name + '_z')
                zList.append(z)

            zImg = ee.Image.cat(zList)

            if applySpatialSmoothing:
                zImg = zImg.focalMean(radius=1, kernelType='square', units='pixels')

            return zImg

        def estimateCovariance(zImg, mask=None):
            """Covarianza centrata su reduceRegion, con maschera opzionale."""
            target = zImg.toArray()
            if mask is not None:
                target = target.updateMask(mask)
            arr = target.reduceRegion(
                reducer=ee.Reducer.centeredCovariance(),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).get('array')
            return ee.Array(arr)

        def shrinkCovariance(cov, df, shrinkageLambda):
            """
            I6: shrinkage verso bersaglio diagonale scalato sulla traccia.
            """
            traceVal = ee.Number(0)
            for i in range(df):
                traceVal = traceVal.add(ee.Number(cov.get([i, i])))
            targetScale = traceVal.divide(df)
            targetMatrix = ee.Array.identity(df).multiply(targetScale)
            return cov.multiply(1.0 - shrinkageLambda).add(targetMatrix.multiply(shrinkageLambda))

        def mahalanobisFromCov(zImg, invCov):
            x = zImg.toArray().toArray(1)
            return (x.arrayTranspose()
                    .matrixMultiply(ee.Image(invCov))
                    .matrixMultiply(x)
                    .arrayGet([0, 0]).sqrt()
                    .rename('Mahalanobis_Score'))

        def robustMahalV2(img):
            """
            I5 + F3: stima robusta della covarianza e degli z-score via
            reweighting iterativo.
            """
            img = ee.Image(img)
            
            # Prima iterazione: z-score su tutti i pixel
            zImg = robust_zScoreBands(img, mask=None)
            
            # Stima iniziale della covarianza
            cov = estimateCovariance(zImg)
            cov = shrinkCovariance(cov, MAHAL_DF, covShrinkage)
            
            cleanMask = None
            for iteration in range(robustCovIterations):
                invCovIter = cov.matrixInverse()
                mahalIter = mahalanobisFromCov(zImg, invCovIter)
                cleanMask = mahalIter.lt(MAHAL_PERMISSIVE_GATING)
                
                # F3: Ricalcola z-score SOLO sui pixel "puliti"
                zImg_clean = robust_zScoreBands(img, mask=cleanMask)
                
                # Ricalcola covarianza SOLO sui pixel "puliti"
                covIter = estimateCovariance(zImg_clean, mask=cleanMask)
                cov = shrinkCovariance(covIter, MAHAL_DF, covShrinkage)
                
                # Aggiorna zImg per i pixel puliti, mantieni i precedenti per gli altri
                zImg = zImg_clean.unmask(zImg)

            invCov = cov.matrixInverse()
            mahal = mahalanobisFromCov(zImg, invCov)

            # I11: diagnostica di normalita' marginale
            skewBands = []
            kurtBands = []
            for name in selectedIndices:
                z = zImg.select(name + '_z')
                m3 = z.pow(3).reduceRegion(reducer=ee.Reducer.mean(), geometry=fieldGeom, scale=scale, maxPixels=1e9).get(name + '_z')
                m4 = z.pow(4).reduceRegion(reducer=ee.Reducer.mean(), geometry=fieldGeom, scale=scale, maxPixels=1e9).get(name + '_z')
                skewBands.append(m3)
                kurtBands.append(m4)

            # Conteggio pixel validi
            validCount = ee.Number(zImg.select(0).mask().reduceRegion(
                reducer=ee.Reducer.sum(), geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).get(selectedIndices[0] + '_z'))

            return (img
                .addBands(mahal)
                .addBands(zImg)
                .set('valid_pixel_count', validCount)
                .set('skew_m3', skewBands)
                .set('kurt_m4', kurtBands)
                .copyProperties(img, ['system:time_start', 'date_string']))

        anomaly = last3.map(robustMahalV2)

        # ============================================================
        # SOGLIA HOTELLING CON CORREZIONE (I7 + F2)
        # ============================================================
        latestForCount = ee.Image(anomaly.sort('system:time_start', False).first())
        nValidPixels = latestForCount.get('valid_pixel_count').getInfo()
        
        # Calcola area per la correzione spaziale
        pixelArea = ee.Image.pixelArea()
        totalAreaTemp = ee.Number(pixelArea.reduceRegion(
            reducer=ee.Reducer.sum(), geometry=fieldGeom, scale=scale, maxPixels=1e9
        ).get('area'))
        totalAreaVal = totalAreaTemp.getInfo()
        field_area_ha = totalAreaVal / 10000
        
        MAHAL_THRESHOLD = hotelling_threshold(nValidPixels, MAHAL_DF, mahalAlpha, field_area_ha)
        print(f"[INFO] Pixel validi per covarianza: {nValidPixels}")
        print(f"[INFO] Soglia Mahalanobis (Hotelling T^2 corretta): {MAHAL_THRESHOLD:.4f}")
        stage_time = timing_log(request_id, "mahalanobis_threshold_computed", stage_time)

        # ============================================================
        # DIAGNOSTICA NORMALITA' MULTIVARIATA
        # ============================================================
        try:
            skewRaw = latestForCount.get('skew_m3').getInfo()
            kurtRaw = latestForCount.get('kurt_m4').getInfo()
            skewValues = [float(s) if s is not None else None for s in skewRaw]
            kurtValues = [float(k) - 3.0 if k is not None else None for k in kurtRaw]
        except Exception as e:
            print(f"[WARN] Errore calcolo diagnostica normalita': {str(e)}")
            skewValues = [None] * MAHAL_DF
            kurtValues = [None] * MAHAL_DF

        multivariateNormalityFlag = skew_kurtosis_flag(skewValues, kurtValues)
        print(f"[INFO] Diagnostica normalita' multivariata: reliable={multivariateNormalityFlag['reliable']}")

        # ============================================================
        # DIRECTION SCORES (I10) E PERSISTENCE (I4)
        # ============================================================
        def perDateDirectionScores(img):
            img = ee.Image(img)
            combined = ee.Image(0)
            for name in selectedIndices:
                z = img.select(name + '_z')
                if not GOOD_DIRECTION_POSITIVE[name]:
                    z = z.multiply(-1)
                combined = combined.add(z)
            combined = combined.rename('Direction_Combined_Z')

            positiveScore = combined.max(0).rename('Positive_Response_Score')
            negativeScore = combined.multiply(-1).max(0).rename('Negative_Response_Score')

            return (img
                .addBands([combined, positiveScore, negativeScore])
                .copyProperties(img, ['system:time_start', 'date_string']))

        withDirection = anomaly.map(perDateDirectionScores)

        def classifyPerDate(img):
            img = ee.Image(img)
            mahalScore = img.select('Mahalanobis_Score')
            posScore = img.select('Positive_Response_Score')
            negScore = img.select('Negative_Response_Score')

            aboveThreshold = mahalScore.gte(MAHAL_THRESHOLD)

            positiveCandidate_d = aboveThreshold.And(posScore.gt(negScore)).And(posScore.gt(0)).rename('positive_candidate_date')
            negativeCandidate_d = aboveThreshold.And(negScore.gt(posScore)).And(negScore.gt(0)).rename('negative_candidate_date')

            return (img
                .addBands([positiveCandidate_d, negativeCandidate_d])
                .copyProperties(img, ['system:time_start']))

        classified = withDirection.map(classifyPerDate)

        persistencePositive = classified.select('positive_candidate_date').sum().rename('Persistence_Positive')
        persistenceNegative = classified.select('negative_candidate_date').sum().rename('Persistence_Negative')

        latestAnomalyImage = ee.Image(classified.sort('system:time_start', False).first()).clip(fieldGeom)
        latestResponseImage = ee.Image(last3.sort('system:time_start', False).first()).clip(fieldGeom)

        currentScore = latestAnomalyImage.select('Mahalanobis_Score')
        positiveScore = latestAnomalyImage.select('Positive_Response_Score')
        negativeScore = latestAnomalyImage.select('Negative_Response_Score')

        # ============================================================
        # RESPONSE MAP
        # ============================================================
        negativeCandidate = (
            currentScore.gte(MAHAL_THRESHOLD)
            .And(negativeScore.gt(positiveScore))
            .And(negativeScore.gt(0))
        )

        positiveCandidate = (
            currentScore.gte(MAHAL_THRESHOLD)
            .And(positiveScore.gt(negativeScore))
            .And(positiveScore.gt(0))
        )

        def applyClusterFilter(candidateMask):
            connected = candidateMask.selfMask().connectedPixelCount(maxSize=256, eightConnected=True)
            keep = connected.gte(minClusterPixels)
            return candidateMask.And(keep.unmask(0))

        positiveCandidateFiltered = applyClusterFilter(positiveCandidate)
        negativeCandidateFiltered = applyClusterFilter(negativeCandidate)

        positiveReliable = positiveCandidateFiltered.And(persistencePositive.gte(2))

        priority = (ee.Image(1)
            .where(positiveReliable, 2)
            .where(negativeCandidateFiltered.And(persistenceNegative.eq(1)), 3)
            .where(negativeCandidateFiltered.And(persistenceNegative.eq(2)), 4)
            .where(negativeCandidateFiltered.And(persistenceNegative.eq(3)), 5)
            .rename('IntelCrop_Response_Map')
            .clip(fieldGeom))

        # ============================================================
        # STATISTICHE DI AREA
        # ============================================================
        directionMask = currentScore.gte(MAHAL_THRESHOLD).selfMask()

        positiveMean = positiveScore.updateMask(directionMask).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=fieldGeom, scale=scale, maxPixels=1e9
        ).get('Positive_Response_Score')

        negativeMean = negativeScore.updateMask(directionMask).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=fieldGeom, scale=scale, maxPixels=1e9
        ).get('Negative_Response_Score')

        positiveMeanVal = ee.Number(ee.Algorithms.If(positiveMean, positiveMean, 0)).getInfo()
        negativeMeanVal = ee.Number(ee.Algorithms.If(negativeMean, negativeMean, 0)).getInfo()

        # Direction summary
        if positiveMeanVal > 0 and positiveMeanVal > negativeMeanVal * 1.5:
            directionClass = "High Performance Zone"
            directionLabel = "High Performance Zone"
            directionDescription = (
                "Le aree individuate mostrano valori medi superiori "
                "rispetto al comportamento prevalente del campo per "
                "uno o più indicatori di vigore, stato idrico o attività vegetativa."
            )
        elif negativeMeanVal > 0 and negativeMeanVal > positiveMeanVal * 1.5:
            directionClass = "Low Performance Zone"
            directionLabel = "Low Performance Zone"
            directionDescription = (
                "Le aree individuate mostrano valori medi inferiori "
                "rispetto al comportamento prevalente del campo per "
                "uno o più indicatori di vigore, stato idrico o attività vegetativa."
            )
        else:
            directionClass = "Reference Zone"
            directionLabel = "Reference Zone"
            directionDescription = (
                "Le aree individuate presentano condizioni complessivamente "
                "in linea con il comportamento prevalente del campo."
            )

        # ============================================================
        # AREE PER CLASSE
        # ============================================================
        areaByClass = (pixelArea.addBands(priority).reduceRegion(
            reducer=ee.Reducer.sum().group(groupField=1, groupName='priority_class'),
            geometry=fieldGeom, scale=scale, maxPixels=1e9
        ))

        totalAreaVal = totalAreaTemp.getInfo()
        areaGroups = areaByClass.getInfo().get('groups', [])

        CLASS_LABELS = {
            1: 'Zona ordinaria', 2: 'Zona ad alta risposta', 3: 'Priorità emergente',
            4: 'Priorità confermata', 5: 'Priorità persistente',
        }
        CLASS_COLORS = {
            1: '#91cf60', 2: '#1a9850', 3: '#fee08b', 4: '#fc8d59', 5: '#d73027',
        }

        priorityAreas = []
        for g in sorted(areaGroups, key=lambda x: x['priority_class']):
            cls = int(g['priority_class'])
            area_m2 = g['sum']
            area_ha = round(area_m2 / 10000, 2)
            perc = round(area_m2 / totalAreaVal * 100, 1)
            priorityAreas.append({
                'class': cls, 'label': CLASS_LABELS.get(cls, str(cls)),
                'color': CLASS_COLORS.get(cls, '#888'), 'area_ha': area_ha, 'percent': perc,
            })

        # ============================================================
        # SIGNIFICATIVITA' A LIVELLO DI CAMPO (I2 + F1 + F9) - CONDIZIONALE
        # ============================================================
        totalPixelsApprox = int(round(totalAreaVal / (scale * scale)))
        anomalousAreaM2 = sum(g['sum'] for g in areaGroups if int(g['priority_class']) >= 3)
        anomalousPixelsApprox = int(round(anomalousAreaM2 / (scale * scale)))
        
        # Calcola il tasso di falsi positivi atteso per il placeholder
        expected_false_positive_rate = (1.0 - mahalAlpha) * 0.5

        if COMPUTE_FIELD_SIGNIFICANCE:
            fieldSignificance = field_level_significance(
                anomalousPixelsApprox, totalPixelsApprox, mahalAlpha,
                field_area_ha=field_area_ha, one_sided_fraction=0.5
            )
            print(f"[INFO] Significativita' a livello di campo: {fieldSignificance}")
        else:
            fieldSignificance = {
                "applicable": False,
                "p_value": None,
                "significant": None,
                "expected_false_positive_rate": round(expected_false_positive_rate, 4),
                "observed_rate": round(anomalousPixelsApprox / totalPixelsApprox, 4) if totalPixelsApprox else None,
                "n_anomalous_pixels": int(anomalousPixelsApprox) if anomalousPixelsApprox is not None else 0,
                "n_anomalous_effective_pixels": None,
                "n_total_pixels": int(totalPixelsApprox) if totalPixelsApprox is not None else 0,
                "n_effective_pixels": None,
                "spatial_independence_assumed": False,
                "note": "Test di significatività a livello di campo non calcolato in modalità operational_fast. La classificazione satellitare resta disponibile; la significatività statistica completa può essere calcolata in modalità scientific_full."
            }
            print("[INFO] Significativita' campo disattivata in operational_fast")

        stage_time = timing_log(request_id, "field_significance_step", stage_time)

        # ============================================================
        # AFFIDABILITA' CANONICA (F5)
        # ============================================================
        sampleAdequate = nValidPixels >= 10 * MAHAL_DF

        def compute_analysis_reliability(temporally_consistent, field_sig, normality_flag, sample_adequate):
            reasons = []
            if not temporally_consistent:
                reasons.append("gap_temporale_elevato")
            if not sample_adequate:
                reasons.append("campione_pixel_insufficiente")
            if field_sig.get("applicable") and field_sig.get("significant") is False:
                reasons.append("segnale_non_distinguibile_dal_rumore")
            if normality_flag and not normality_flag.get("reliable", True):
                reasons.append("normalita_multivariata_non_verificata")

            if not temporally_consistent or not sample_adequate:
                level = "low"
            elif len(reasons) == 0:
                level = "high"
            elif len(reasons) == 1:
                level = "medium"
            else:
                level = "low"

            label_map = {"high": "Alta", "medium": "Media", "low": "Bassa"}

            return {
                "level": level,
                "label": label_map[level],
                "reasons": reasons,
                "note": (
                    "Affidabilita' STATISTICA del segnale (coerenza interna "
                    "rispetto al rumore del campo stesso), non affidabilita' "
                    "AGRONOMICA. Non implica che il pattern rilevato "
                    "corrisponda a un problema reale in campo: e' uno "
                    "screening satellitare preliminare, non una diagnosi."
                )
            }

        analysisReliability = compute_analysis_reliability(
            last3TemporallyConsistent, fieldSignificance, multivariateNormalityFlag, sampleAdequate
        )
        print(f"[INFO] Affidabilita' canonica: {analysisReliability['level']}")

        # ============================================================
        # CLASS STATS
        # ============================================================
        statsBands = ['EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI']

        def safeRound(value, digits=4):
            try:
                if value is None:
                    return None
                return round(float(value), digits)
            except Exception:
                return None

        classStats = {}
        latestStatsImage = latestResponseImage.select(statsBands)

        for cls in [1, 2, 3, 4, 5]:
            classMask = priority.eq(cls).selfMask()
            classArea = pixelArea.updateMask(classMask).reduceRegion(
                reducer=ee.Reducer.sum(), geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).get('area')
            classAreaVal = ee.Number(ee.Algorithms.If(classArea, classArea, 0)).getInfo()

            if classAreaVal == 0:
                classStats[str(cls)] = {
                    "class": cls, "label": CLASS_LABELS.get(cls, str(cls)),
                    "area_ha": 0, "percent": 0, "indices": {}
                }
                continue

            reducer = (
                ee.Reducer.mean()
                .combine(ee.Reducer.median(), sharedInputs=True)
                .combine(ee.Reducer.stdDev(), sharedInputs=True)
                .combine(ee.Reducer.percentile([25, 75]), sharedInputs=True)
            )

            stats = latestStatsImage.updateMask(classMask).reduceRegion(
                reducer=reducer, geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).getInfo()

            classStats[str(cls)] = {
                "class": cls, "label": CLASS_LABELS.get(cls, str(cls)),
                "area_ha": round(classAreaVal / 10000, 2),
                "percent": round(classAreaVal / totalAreaVal * 100, 1),
                "indices": {}
            }

            for band in statsBands:
                classStats[str(cls)]["indices"][band] = {
                    "mean": safeRound(stats.get(f"{band}_mean")),
                    "median": safeRound(stats.get(f"{band}_median")),
                    "std": safeRound(stats.get(f"{band}_stdDev")),
                    "p25": safeRound(stats.get(f"{band}_p25")),
                    "p75": safeRound(stats.get(f"{band}_p75")),
                }

        referenceStats = classStats.get("1", {}).get("indices", {})

        for cls in ["2", "3", "4", "5"]:
            if cls not in classStats:
                continue
            classStats[cls].setdefault("indices", {})
            for band in statsBands:
                classStats[cls]["indices"].setdefault(band, {
                    "mean": None, "median": None, "std": None, "p25": None, "p75": None,
                })
                refMedian = referenceStats.get(band, {}).get("median")
                clsMedian = classStats[cls]["indices"][band].get("median")
                if refMedian is not None and clsMedian is not None:
                    classStats[cls]["indices"][band]["delta_ref"] = safeRound(clsMedian - refMedian)
                else:
                    classStats[cls]["indices"][band]["delta_ref"] = None

        # ============================================================
        # AGRONOMIC CONTEXT
        # ============================================================
        def getAreaPercent(class_id):
            for item in priorityAreas:
                if item["class"] == class_id:
                    return float(item.get("percent", 0))
            return 0.0

        def getAreaHa(class_id):
            for item in priorityAreas:
                if item["class"] == class_id:
                    return float(item.get("area_ha", 0))
            return 0.0

        ordinaryPct = getAreaPercent(1)
        highPerformancePct = getAreaPercent(2)
        emergingPct = getAreaPercent(3)
        confirmedPct = getAreaPercent(4)
        persistentPct = getAreaPercent(5)

        priorityPct = emergingPct + confirmedPct + persistentPct
        confirmedPriorityPct = confirmedPct + persistentPct
        priorityHa = getAreaHa(3) + getAreaHa(4) + getAreaHa(5)

        # ============================================================
        # AGRONOMIC DRIVERS
        # ============================================================
        def weighted_priority_delta(classStats, band):
            weighted_sum = 0
            weight_total = 0
            for cls in ["3", "4", "5"]:
                cls_stats = classStats.get(cls, {})
                area_pct = float(cls_stats.get("percent", 0) or 0)
                delta = cls_stats.get("indices", {}).get(band, {}).get("delta_ref")
                if delta is not None and area_pct > 0:
                    weighted_sum += float(delta) * area_pct
                    weight_total += area_pct
            if weight_total == 0:
                return None
            return round(weighted_sum / weight_total, 4)

        driverBands = ["EVI", "NDMI", "NDRE", "MSI", "PSRI"]
        driverDeltas = {band: weighted_priority_delta(classStats, band) for band in driverBands}

        def driver_strength(band, delta):
            if delta is None:
                return 0
            if band in ["EVI", "NDMI", "NDRE"]:
                return abs(delta) if delta < 0 else 0
            if band in ["MSI", "PSRI"]:
                return abs(delta) if delta > 0 else 0
            return 0

        rankedDrivers = sorted(
            [{"index": band, "delta_ref": driverDeltas.get(band),
              "strength": driver_strength(band, driverDeltas.get(band))} for band in driverBands],
            key=lambda x: x["strength"], reverse=True        )

        primaryDriver = rankedDrivers[0] if rankedDrivers else None
        secondaryDriver = rankedDrivers[1] if len(rankedDrivers) > 1 else None

        def interpret_driver(driver):
            if not driver or driver.get("strength", 0) == 0:
                return None
            band = driver["index"]
            if band == "EVI":
                return "vigore vegetativo inferiore rispetto alle zone ordinarie"
            if band == "NDMI":
                return "condizione idrica relativamente inferiore nelle zone prioritarie"
            if band == "NDRE":
                return "attività clorofilliana relativamente ridotta"
            if band == "MSI":
                return "maggiore segnale compatibile con stress idrico"
            if band == "PSRI":
                return "maggiore segnale compatibile con senescenza o stress fisiologico"
            return None

        def get_signal_confidence(priorityPct, confirmedPriorityPct, persistentPct, 
                                   temporally_consistent, field_sig, normality_flag=None):
            if not temporally_consistent:
                return {
                    "level": "low", "label": "Bassa",
                    "description": (
                        "Le ultime acquisizioni utilizzate per la mappa di priorità sono "
                        "distanziate nel tempo più del previsto (probabile copertura nuvolosa "
                        "intermedia). Il confronto tra queste date potrebbe non rappresentare "
                        "correttamente lo stato attuale del campo."
                    )
                }

            if field_sig.get("applicable") and field_sig.get("significant") is False:
                return {
                    "level": "low", "label": "Bassa",
                    "description": (
                        "La quota di superficie segnalata come prioritaria non è "
                        "statisticamente distinguibile dal tasso di falsi positivi atteso "
                        "per la soglia di significatività scelta. Trattare come segnale "
                        "preliminare."
                    )
                }

            normality_reliable = True if normality_flag is None else normality_flag.get("reliable", True)

            if priorityPct < 3:
                return {
                    "level": "low", "label": "Bassa",
                    "description": "Il segnale interessa una superficie limitata. L'interpretazione deve essere considerata preliminare."
                }

            if confirmedPriorityPct >= 5 or persistentPct >= 2:
                if not normality_reliable:
                    return {
                        "level": "medium", "label": "Media",
                        "description": (
                            "Il segnale è supportato da buona estensione spaziale e conferma "
                            "temporale, ma la diagnostica di normalità multivariata indica che "
                            "l'assunzione statistica sottostante è meno solida per questa data."
                        )
                    }
                return {
                    "level": "high", "label": "Alta",
                    "description": "Il segnale è supportato da una buona estensione spaziale e da conferma temporale nelle osservazioni recenti."
                }

            return {
                "level": "medium", "label": "Media",
                "description": "Il segnale è presente ma non pienamente consolidato. È consigliabile verificarne la stabilità nelle prossime acquisizioni."
            }

        def get_dominant_process(primary, secondary):
            indexes = [primary.get("index") if primary else None, secondary.get("index") if secondary else None]
            if "MSI" in indexes and "NDMI" in indexes:
                return {"code": "water_related", "label": "Segnale idrico",
                        "interpretation": "Il pattern osservato è compatibile con una differenza idrica localizzata rispetto alle zone ordinarie."}
            if "EVI" in indexes and "NDRE" in indexes:
                return {"code": "photosynthetic_related", "label": "Segnale fotosintetico",
                        "interpretation": "Il pattern osservato è compatibile con una riduzione relativa del vigore vegetativo o dell'attività clorofilliana."}
            if "PSRI" in indexes:
                return {"code": "senescence_related", "label": "Segnale fisiologico",
                        "interpretation": "Il pattern osservato è compatibile con un maggiore segnale di senescenza o stress fisiologico nelle zone prioritarie."}
            return {"code": "mixed_signal", "label": "Segnale misto",
                    "interpretation": "Il pattern osservato mostra differenze distribuite su più indici, senza un singolo processo dominante chiaramente prevalente."}

        signalConfidence = get_signal_confidence(
            priorityPct, confirmedPriorityPct, persistentPct, 
            last3TemporallyConsistent, fieldSignificance, multivariateNormalityFlag
        )
        dominantProcess = get_dominant_process(primaryDriver, secondaryDriver)

        agronomicDrivers = {
            "deltas": driverDeltas,
            "ranked": rankedDrivers,
            "primary": {
                "index": primaryDriver["index"] if primaryDriver else None,
                "delta_ref": primaryDriver["delta_ref"] if primaryDriver else None,
                "interpretation": interpret_driver(primaryDriver)
            },
            "secondary": {
                "index": secondaryDriver["index"] if secondaryDriver else None,
                "delta_ref": secondaryDriver["delta_ref"] if secondaryDriver else None,
                "interpretation": interpret_driver(secondaryDriver)
            },
            "confidence": signalConfidence,
            "dominant_process": dominantProcess
        }

        if persistentPct >= 5 or priorityPct >= 12:
            agronomicLevel = "elevata"
        elif confirmedPriorityPct >= 5 or priorityPct >= 5:
            agronomicLevel = "moderata"
        elif priorityPct > 0:
            agronomicLevel = "bassa"
        else:
            agronomicLevel = "ordinaria"

        agronomicContext = {
            "ordinary_percent": round(ordinaryPct, 1),
            "high_performance_percent": round(highPerformancePct, 1),
            "priority_percent": round(priorityPct, 1),
            "priority_area_ha": round(priorityHa, 2),
            "emerging_percent": round(emergingPct, 1),
            "confirmed_percent": round(confirmedPct, 1),
            "persistent_percent": round(persistentPct, 1),
            "confirmed_priority_percent": round(confirmedPriorityPct, 1),
            "attention_level": agronomicLevel,
            "vdi_class": None,
            "vdi_score": None,
        }

        comparisonContext = {
            "reference_available": ordinaryPct >= 1,
            "high_response_available": highPerformancePct >= 1,
            "priority_available": priorityPct >= 1,
            "priority_percent": round(priorityPct, 1),
            "high_response_percent": round(highPerformancePct, 1),
            "mode": (
                "reference_high_priority" if highPerformancePct >= 1 and priorityPct >= 1 else
                "reference_priority" if priorityPct >= 1 else
                "reference_high" if highPerformancePct >= 1 else
                "insufficient_priority_area"
            ),
            "message": (
                "Le zone prioritarie coprono meno dell'1% della superficie analizzata; il confronto temporale con le zone prioritarie non è sufficientemente robusto."
                if priorityPct < 1 else None
            )
        }

        # ============================================================
        # VDI - VEGETATION DIVERGENCE INDEX (CONDIZIONALE)
        # ============================================================
        if COMPUTE_VDI:
            latestDate = ee.Date(ee.Image(clean.sort('system:time_start', False).first()).get('system:time_start'))
            vesStart = latestDate.advance(-60, 'day')

            referenceMask = priority.eq(1).selfMask()
            priorityInspectionMask = priority.gte(3).selfMask()
            highResponseMask = priority.eq(2).selfMask()

            vdiBands = ['EVI', 'NDMI', 'NDRE', 'MSI', 'PSRI']
            vesCollection = clean.filterDate(vesStart, endDate)

            def safeDelta(a, b):
                return ee.Algorithms.If(
                    ee.Algorithms.IsEqual(a, None), None,
                    ee.Algorithms.If(ee.Algorithms.IsEqual(b, None), None, ee.Number(a).subtract(ee.Number(b)))
                )

            def makeVesFeature(img):
                img = ee.Image(img)
                referenceStats = img.select(vdiBands).updateMask(referenceMask).reduceRegion(
                    reducer=ee.Reducer.median(), geometry=fieldGeom, scale=scale, maxPixels=1e9)
                highResponseStats = img.select(vdiBands).updateMask(highResponseMask).reduceRegion(
                    reducer=ee.Reducer.median(), geometry=fieldGeom, scale=scale, maxPixels=1e9)
                priorityStats = img.select(vdiBands).updateMask(priorityInspectionMask).reduceRegion(
                    reducer=ee.Reducer.median(), geometry=fieldGeom, scale=scale, maxPixels=1e9)

                dEVI = safeDelta(priorityStats.get('EVI'), referenceStats.get('EVI'))
                dNDMI = safeDelta(priorityStats.get('NDMI'), referenceStats.get('NDMI'))
                dNDRE = safeDelta(priorityStats.get('NDRE'), referenceStats.get('NDRE'))
                dMSI = safeDelta(priorityStats.get('MSI'), referenceStats.get('MSI'))
                dPSRI = safeDelta(priorityStats.get('PSRI'), referenceStats.get('PSRI'))

                dHighEVI = safeDelta(highResponseStats.get('EVI'), referenceStats.get('EVI'))
                dHighNDMI = safeDelta(highResponseStats.get('NDMI'), referenceStats.get('NDMI'))
                dHighNDRE = safeDelta(highResponseStats.get('NDRE'), referenceStats.get('NDRE'))
                dHighMSI = safeDelta(highResponseStats.get('MSI'), referenceStats.get('MSI'))
                dHighPSRI = safeDelta(highResponseStats.get('PSRI'), referenceStats.get('PSRI'))

                return (ee.Feature(None)
                    .set('date', ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'))
                    .set('system:time_start', img.get('system:time_start'))
                    .set('priority_EVI', priorityStats.get('EVI'))
                    .set('priority_NDMI', priorityStats.get('NDMI'))
                    .set('priority_NDRE', priorityStats.get('NDRE'))
                    .set('priority_MSI', priorityStats.get('MSI'))
                    .set('priority_PSRI', priorityStats.get('PSRI'))
                    .set('high_EVI', highResponseStats.get('EVI'))
                    .set('high_NDMI', highResponseStats.get('NDMI'))
                    .set('high_NDRE', highResponseStats.get('NDRE'))
                    .set('high_MSI', highResponseStats.get('MSI'))
                    .set('high_PSRI', highResponseStats.get('PSRI'))
                    .set('reference_EVI', referenceStats.get('EVI'))
                    .set('reference_NDMI', referenceStats.get('NDMI'))
                    .set('reference_NDRE', referenceStats.get('NDRE'))
                    .set('reference_MSI', referenceStats.get('MSI'))
                    .set('reference_PSRI', referenceStats.get('PSRI'))
                    .set('delta_EVI', dEVI)
                    .set('delta_NDMI', dNDMI)
                    .set('delta_NDRE', dNDRE)
                    .set('delta_MSI', dMSI)
                    .set('delta_PSRI', dPSRI)
                    .set('delta_high_EVI', dHighEVI)
                    .set('delta_high_NDMI', dHighNDMI)
                    .set('delta_high_NDRE', dHighNDRE)
                    .set('delta_high_MSI', dHighMSI)
                    .set('delta_high_PSRI', dHighPSRI)
                )

            vesFC = ee.FeatureCollection(vesCollection.map(makeVesFeature))

            try:
                vesData = vesFC.select([
                    'date',
                    'priority_EVI', 'priority_NDMI', 'priority_NDRE', 'priority_MSI', 'priority_PSRI',
                    'high_EVI', 'high_NDMI', 'high_NDRE', 'high_MSI', 'high_PSRI',
                    'reference_EVI', 'reference_NDMI', 'reference_NDRE', 'reference_MSI', 'reference_PSRI',
                    'delta_EVI', 'delta_NDMI', 'delta_NDRE', 'delta_MSI', 'delta_PSRI',
                    'delta_high_EVI', 'delta_high_NDMI', 'delta_high_NDRE', 'delta_high_MSI', 'delta_high_PSRI'
                ]).getInfo()['features']
                vesData = [f['properties'] for f in vesData]
                vesData = aggregate_rows_by_date(vesData)
                print(f"[INFO] VDI data estratti dopo aggregazione giornaliera: {len(vesData)} record")

                vdiTimeSeries = []
                for r in vesData:
                    if r.get('delta_NDMI') is not None:
                        vdiTimeSeries.append({
                            'date': r.get('date'),
                            'vdi_proxy': round(float(r.get('delta_NDMI')), 6),
                            'delta_NDMI': round(float(r.get('delta_NDMI')), 6),
                            'delta_EVI': round(float(r.get('delta_EVI')), 6) if r.get('delta_EVI') is not None else None,
                            'delta_NDRE': round(float(r.get('delta_NDRE')), 6) if r.get('delta_NDRE') is not None else None,
                            'delta_MSI': round(float(r.get('delta_MSI')), 6) if r.get('delta_MSI') is not None else None,
                            'delta_PSRI': round(float(r.get('delta_PSRI')), 6) if r.get('delta_PSRI') is not None else None,
                        })
            except Exception as e:
                print(f"[WARN] Errore estrazione VDI: {str(e)}")
                vesData = []
                vdiTimeSeries = []

            # ============================================================
            # VDI STANDARDIZZATO (I8 + I9)
            # ============================================================
            MIN_VDI_OBSERVATIONS = 5

            if priorityPct < 1:
                vdiScore = None
                vdiClass = "Insufficient priority area"
                vdiRSquared = None
                vdiConfidence = "not_applicable"
                vdiTStat = None
                vdiNEff = None
                vdiLag1Autocorr = None
            else:
                validVdi = [r for r in vesData if r.get('delta_NDMI') is not None and r.get('date')]

                if len(validVdi) >= MIN_VDI_OBSERVATIONS:
                    validVdiSorted = sorted(validVdi, key=lambda r: r['date'])
                    firstDate = datetime.datetime.strptime(validVdiSorted[0]['date'], '%Y-%m-%d')
                    xs = [(datetime.datetime.strptime(r['date'], '%Y-%m-%d') - firstDate).days for r in validVdiSorted]
                    ys = [float(r['delta_NDMI']) for r in validVdiSorted]

                    reg = standardized_vdi_regression(xs, ys)

                    if reg is None:
                        vdiScore = None
                        vdiClass = "Insufficient priority area"
                        vdiConfidence = "not_applicable"
                        vdiRSquared = None
                        vdiTStat = None
                        vdiNEff = None
                        vdiLag1Autocorr = None
                    else:
                        vdiScore = reg["slope"]
                        vdiRSquared = round(reg["r_squared"], 3)
                        vdiTStat = round(reg["t_stat"], 3) if reg["t_stat"] is not None else None
                        vdiNEff = reg["n_effective"]
                        vdiLag1Autocorr = reg["lag1_autocorrelation"]
                        vdiClass = classify_vdi_from_tstat(reg["t_stat"])
                        vdiConfidence = confidence_from_tstat(reg["t_stat"], reg["n_effective"])
                else:
                    vdiScore = None
                    vdiClass = "Insufficient statistical confidence"
                    vdiRSquared = None
                    vdiConfidence = "not_applicable"
                    vdiTStat = None
                    vdiNEff = None
                    vdiLag1Autocorr = None

            vdiResult = {
                "score": round(vdiScore, 6) if vdiScore is not None else None,
                "class": vdiClass,
                "r_squared": vdiRSquared,
                "t_statistic": vdiTStat,
                "n_effective": vdiNEff,
                "lag1_autocorrelation": vdiLag1Autocorr,
                "confidence": vdiConfidence,
                "window_days": 60,
                "min_observations_required": MIN_VDI_OBSERVATIONS,
            }
            print(f"[INFO] VDI completato: {vdiClass}")
        else:
            vesData = []
            vdiTimeSeries = []
            vdiResult = {
                "score": None,
                "class": None,
                "window_days": None,
                "r_squared": None,
                "confidence": "not_computed_fast_mode",
                "t_statistic": None,
                "n_observations": None,
                "n_effective": None,
                "lag1_autocorrelation": None,
                "note": "VDI storico non calcolato in modalità operational_fast. Disponibile in modalità scientific_full."
            }
            print("[INFO] VDI disattivato in operational_fast")

        # Aggiorna il contesto agronomico con VDI
        agronomicContext["vdi_class"] = vdiResult.get("class")
        agronomicContext["vdi_score"] = vdiResult.get("score")

        stage_time = timing_log(request_id, "vdi_step", stage_time)

        # ============================================================
        # F10: ANALYSIS STATUS (dopo VDI)
        # ============================================================
        analysisStatus = build_analysis_status(
            priority_pct=priorityPct,
            priority_ha=priorityHa,
            persistent_pct=persistentPct,
            confirmed_priority_pct=confirmedPriorityPct,
            vdi_class=vdiResult.get("class"),
            reliability=analysisReliability
        )
        print(f"[INFO] Analysis status: {analysisStatus['code']} - {analysisStatus['label']}")

        lastDateStr = ''
        if trendData:
            dates = [r.get('date', '') for r in trendData if r.get('date')]
            if dates:
                lastDateStr = sorted(dates)[-1]

        # ============================================================
        # SOIL DIAGNOSTIC
        # ============================================================
        try:
            ndviOsaviDiff = (latestResponseImage.select('NDVI')
                .subtract(latestResponseImage.select('OSAVI')).abs().rename('NDVI_OSAVI_ABS_DIFF'))

            diffStats = ndviOsaviDiff.reduceRegion(
                reducer=ee.Reducer.mean().combine(ee.Reducer.percentile([90]), sharedInputs=True),
                geometry=fieldGeom, scale=scale, maxPixels=1e9
            ).getInfo()

            soilDiagnostic = {
                "mean_abs_diff_ndvi_osavi": safeRound(diffStats.get('NDVI_OSAVI_ABS_DIFF_mean')),
                "p90_abs_diff_ndvi_osavi": safeRound(diffStats.get('NDVI_OSAVI_ABS_DIFF_p90')),
                "note": (
                    "Indicatore preliminare di possibile interferenza del suolo esposto "
                    "(tipica negli oliveti a causa dello spazio inter-fila). Nessuna soglia "
                    "di esclusione o di peso e' ancora applicata: il valore va osservato su "
                    "piu' campi reali prima di decidere un criterio di filtro."
                )
            }
        except Exception as e:
            print(f"[WARN] Errore calcolo soil diagnostic: {str(e)}")
            soilDiagnostic = {
                "mean_abs_diff_ndvi_osavi": None,
                "p90_abs_diff_ndvi_osavi": None,
                "note": "Calcolo non riuscito per questa analisi."
            }

        # ============================================================
        # MAP LAYERS - PERFORMANCE OTTIMIZZATA
        # ============================================================
        mapLayers = {}

        if GENERATE_RESPONSE_MAP_LAYER:
            try:
                # Response Map layer
                priorityMapId = priority.getMapId({'min': 1, 'max': 5, 'palette': ['91cf60', '1a9850', 'fee08b', 'fc8d59', 'd73027']})
                mapLayers["priority"] = {
                    "name": "Response Map",
                    "type": "ee_tile",
                    "url": priorityMapId["tile_fetcher"].url_format,
                    "opacity": 0.75,
                    "group": "Priorità",
                    "legend": [
                        {"class": 1, "label": "Zona ordinaria", "color": "#91cf60"},
                        {"class": 2, "label": "Zona ad alta risposta", "color": "#1a9850"},
                        {"class": 3, "label": "Priorità emergente", "color": "#fee08b"},
                        {"class": 4, "label": "Priorità confermata", "color": "#fc8d59"},
                        {"class": 5, "label": "Priorità persistente", "color": "#d73027"},
                    ]
                }
                stage_time = timing_log(request_id, "response_map_layer_generated", stage_time)
            except Exception as e:
                print(f"[WARN] Errore generazione Response Map layer: {str(e)}")
        else:
            print("[INFO] Response Map layer disattivato")

        # Index map layers (opzionali)
        if GENERATE_INDEX_MAP_LAYERS:
            try:
                def getVisParams(image, band, palette):
                    stats = image.select(band).reduceRegion(
                        reducer=ee.Reducer.percentile([5, 95]), geometry=fieldGeom, scale=scale, maxPixels=1e9)
                    p5 = ee.Number(stats.get(f'{band}_p5'))
                    p95 = ee.Number(stats.get(f'{band}_p95'))
                    return {'min': p5.getInfo(), 'max': p95.getInfo(), 'palette': palette}

                latestImage = latestResponseImage

                eviPalette = ['8b0000', 'ff4500', 'ffd700', '7fff00', '006400']
                ndmiPalette = ['8b4513', 'd2b48c', 'ffffcc', '7fcdbb', '2c7fb8', '253494']
                ndrePalette = ['7f0000', 'd7301f', 'fc8d59', 'fee08b', '91cf60', '1a9850']
                ndviPalette = ['a50026', 'd73027', 'f46d43', 'fee08b', '66bd63', '1a9850', '006837']

                eviVis = getVisParams(latestImage, 'EVI', eviPalette)
                ndmiVis = getVisParams(latestImage, 'NDMI', ndmiPalette)
                ndreVis = getVisParams(latestImage, 'NDRE', ndrePalette)
                ndviVis = getVisParams(latestImage, 'NDVI', ndviPalette)

                eviMapId = latestImage.select('EVI').getMapId(eviVis)
                ndmiMapId = latestImage.select('NDMI').getMapId(ndmiVis)
                ndreMapId = latestImage.select('NDRE').getMapId(ndreVis)
                ndviMapId = latestImage.select('NDVI').getMapId(ndviVis)

                mapLayers.update({
                    "evi": {
                        "name": "EVI",
                        "type": "ee_tile",
                        "url": eviMapId["tile_fetcher"].url_format,
                        "opacity": 0.70,
                        "group": "Vigore vegetativo",
                        "min": eviVis["min"],
                        "max": eviVis["max"],
                        "palette": eviPalette,
                        "legendLabels": {
                            "low": "Basso vigore",
                            "high": "Alto vigore"
                        }
                    },
                    "ndmi": {
                        "name": "NDMI",
                        "type": "ee_tile",
                        "url": ndmiMapId["tile_fetcher"].url_format,
                        "opacity": 0.70,
                        "group": "Stato idrico",
                        "min": ndmiVis["min"],
                        "max": ndmiVis["max"],
                        "palette": ndmiPalette,
                        "legendLabels": {
                            "low": "Vegetazione più secca",
                            "high": "Vegetazione più umida"
                        }
                    },
                    "ndre": {
                        "name": "NDRE",
                        "type": "ee_tile",
                        "url": ndreMapId["tile_fetcher"].url_format,
                        "opacity": 0.70,
                        "group": "Attività clorofilliana",
                        "min": ndreVis["min"],
                        "max": ndreVis["max"],
                        "palette": ndrePalette,
                        "legendLabels": {
                            "low": "Bassa attività",
                            "high": "Alta attività"
                        }
                    },
                    "ndvi": {
                        "name": "NDVI",
                        "type": "ee_tile",
                        "url": ndviMapId["tile_fetcher"].url_format,
                        "opacity": 0.70,
                        "group": "Vigore vegetativo",
                        "min": ndviVis["min"],
                        "max": ndviVis["max"],
                        "palette": ndviPalette,
                        "legendLabels": {
                            "low": "Basso vigore",
                            "high": "Alto vigore"
                        }
                    }
                })
                stage_time = timing_log(request_id, "index_map_layers_generated", stage_time)
                print("[INFO] Map layers generati: Response Map, EVI, NDMI, NDRE, NDVI")
            except Exception as e:
                print(f"[WARN] Errore generazione index map layers: {str(e)}")
        else:
            print("[INFO] Index map layers disattivati")

        # ============================================================
        # MAP SNAPSHOTS PER REPORT
        # ============================================================
        mapSnapshots = {}

        if GENERATE_MAP_SNAPSHOTS:
            try:
                # Buffer dinamico per mostrare anche il contesto attorno al campo.
                fieldAreaForThumb = ee.Number(fieldGeom.area(1))
                thumbBuffer = fieldAreaForThumb.sqrt().multiply(0.90).max(100).min(500)
                thumbRegion = fieldGeom.buffer(thumbBuffer).bounds(1)

                thumbParamsBase = {
                    "region": thumbRegion,
                    "dimensions": "1400x850",
                    "format": "png"
                }

                # Bordo campo per evidenziare il perimetro nell'immagine report.
                fieldOutline = ee.Image().byte().paint(
                    featureCollection=ee.FeatureCollection([ee.Feature(fieldGeom)]),
                    color=1,
                    width=4
                ).visualize(
                    palette=["111827"],
                    forceRgbOutput=True
                )

                # Base satellitare NON clippata al campo.
                snapshotBaseImage = ee.Image(
                    s2
                    .filterBounds(thumbRegion)
                    .filterDate(latestDate.advance(-30, 'day'), latestDate.advance(1, 'day'))
                    .sort('CLOUDY_PIXEL_PERCENTAGE')
                    .first()
                ).divide(10000).clip(thumbRegion)

                trueColorVis = {
                    "bands": ["B4", "B3", "B2"],
                    "min": 0.02,
                    "max": 0.30,
                    "gamma": 1.15
                }

                satelliteBase = snapshotBaseImage.visualize(
                    **trueColorVis,
                    forceRgbOutput=True
                )

                # Overlay clippati al campo - solo se il layer è stato generato
                if GENERATE_RESPONSE_MAP_LAYER and "priority" in mapLayers:
                    priorityOverlay = priority.visualize(
                        min=1,
                        max=5,
                        palette=['91cf60', '1a9850', 'fee08b', 'fc8d59', 'd73027'],
                        opacity=0.62,
                        forceRgbOutput=True
                    )
                    mapSnapshots["priority"] = satelliteBase.blend(priorityOverlay).blend(fieldOutline).getThumbURL(thumbParamsBase)
                else:
                    mapSnapshots["priority"] = None

                if GENERATE_INDEX_MAP_LAYERS:
                    # Usa le variabili già calcolate
                    if 'eviVis' in locals():
                        eviOverlay = latestResponseImage.select('EVI').visualize(
                            **eviVis,
                            opacity=0.58,
                            forceRgbOutput=True
                        )
                        mapSnapshots["evi"] = satelliteBase.blend(eviOverlay).blend(fieldOutline).getThumbURL(thumbParamsBase)
                    else:
                        mapSnapshots["evi"] = None

                    if 'ndmiVis' in locals():
                        ndmiOverlay = latestResponseImage.select('NDMI').visualize(
                            **ndmiVis,
                            opacity=0.58,
                            forceRgbOutput=True
                        )
                        mapSnapshots["ndmi"] = satelliteBase.blend(ndmiOverlay).blend(fieldOutline).getThumbURL(thumbParamsBase)
                    else:
                        mapSnapshots["ndmi"] = None

                    if 'ndreVis' in locals():
                        ndreOverlay = latestResponseImage.select('NDRE').visualize(
                            **ndreVis,
                            opacity=0.58,
                            forceRgbOutput=True
                        )
                        mapSnapshots["ndre"] = satelliteBase.blend(ndreOverlay).blend(fieldOutline).getThumbURL(thumbParamsBase)
                    else:
                        mapSnapshots["ndre"] = None

                    if 'ndviVis' in locals():
                        ndviOverlay = latestResponseImage.select('NDVI').visualize(
                            **ndviVis,
                            opacity=0.58,
                            forceRgbOutput=True
                        )
                        mapSnapshots["ndvi"] = satelliteBase.blend(ndviOverlay).blend(fieldOutline).getThumbURL(thumbParamsBase)
                    else:
                        mapSnapshots["ndvi"] = None
                else:
                    mapSnapshots.update({
                        "evi": None,
                        "ndmi": None,
                        "ndre": None,
                        "ndvi": None
                    })

                print("[INFO] Map snapshots generati per report")
            except Exception as e:
                print(f"[WARN] Errore generazione map snapshots: {str(e)}")
                mapSnapshots = {}
        else:
            print("[INFO] Map snapshots disattivati per analisi veloce")
        
        stage_time = timing_log(request_id, "map_snapshots_step", stage_time)

        # ============================================================
        # COSTRUZIONE RISULTATO
        # ============================================================
        result = {
            "status": "ok",
            "lastImageDate": lastDateStr,
            "totalArea": round(totalAreaVal / 10000, 2),
            "priorityAreas": priorityAreas,
            "classStats": classStats,
            "agronomicDrivers": agronomicDrivers,
            "agronomicContext": agronomicContext,
            "comparisonContext": comparisonContext,
            "directionSummary": {
                "class": directionClass, "label": directionLabel, "description": directionDescription,
                "positive_score": round(float(positiveMeanVal), 4),
                "negative_score": round(float(negativeMeanVal), 4),
            },
            "vdi": vdiResult,
            "dataQuality": {
                "last3_gap_days": last3GapDays,
                "temporally_consistent": last3TemporallyConsistent,
                "gap_warning_threshold_days": LAST3_GAP_WARNING_DAYS,
            },
            "dataQualityDiagnostic": {
                "cloud_threshold_used": cloudThreshold,
                "valid_pixel_threshold_used": validPixelThreshold,
                "images_after_filter": cleanSize,
                "valid_pixels_for_covariance": nValidPixels,
            },
            "soilDiagnostic": soilDiagnostic,
            "anomalyThreshold": {
                "method": "hotelling_t2_corrected",
                "degrees_of_freedom": MAHAL_DF,
                "alpha": mahalAlpha,
                "n_valid_pixels": nValidPixels,
                "threshold_value": round(MAHAL_THRESHOLD, 4),
                "chi_square_nominal_threshold": round(MAHAL_CHI2_NOMINAL, 4),
                "note": (
                    "La soglia effettiva usata per classificare i pixel e' basata sulla "
                    "T^2 di Hotelling corretta per covarianza stimata da campione finito, "
                    "piu' conservativa del chi2 nominale quando il campo ha pochi pixel validi."
                )
            },
            "fieldSignificance": fieldSignificance,
            "multivariateNormalityFlag": multivariateNormalityFlag,
            "analysisReliability": analysisReliability,
            "analysisStatus": analysisStatus,  # F10
            "analysisProfile": {
                "profile": ANALYSIS_PROFILE,
                "compute_field_significance": COMPUTE_FIELD_SIGNIFICANCE,
                "compute_vdi": COMPUTE_VDI,
                "generate_response_map_layer": GENERATE_RESPONSE_MAP_LAYER,
                "generate_index_map_layers": GENERATE_INDEX_MAP_LAYERS,
                "generate_map_snapshots": GENERATE_MAP_SNAPSHOTS
            },
            "productDisclaimer": {
                "tier": "preliminary_satellite_screening",
                "short": "Screening satellitare preliminare — non sostituisce un sopralluogo agronomico.",
                "long": (
                    "Questa analisi individua pattern spettrali statisticamente anomali "
                    "rispetto al comportamento prevalente del campo. Non costituisce una "
                    "diagnosi agronomica e non è validata con ispezione a terra o rilievo "
                    "drone: va utilizzata per orientare un sopralluogo mirato, non per "
                    "decidere direttamente un'azione in campo (irrigazione, trattamento, "
                    "raccolta anticipata)."
                ),
                "must_display": True
            },
            "spatialFilterConfig": {
                "min_cluster_pixels": minClusterPixels,
                "spatial_smoothing_applied": applySpatialSmoothing,
            },
            "robustCovarianceConfig": {
                "shrinkage_intensity": covShrinkage,
                "reweighting_iterations": robustCovIterations,
            },
            "trendData": trendData,
            "vdiData": vesData if COMPUTE_VDI else [],
            "vdiTimeSeries": vdiTimeSeries if COMPUTE_VDI else [],
            "mapLayers": mapLayers,
            "mapSnapshots": mapSnapshots,
        }

        # ============================================================
        # F6: VALIDAZIONE AUTOMATICA
        # ============================================================
        validation_warnings = validate_analysis_results(result, geojson)
        analysis_quality = get_analysis_quality(validation_warnings)
        
        result['validation'] = {
            'warnings': validation_warnings,
            'quality': analysis_quality,
            'timestamp': datetime.datetime.now().isoformat()
        }

        # ============================================================
        # STEP 5: VALIDAZIONE CONTRATTO DATI (SOSTITUISCE IL BLOCCO MANUALE)
        # ============================================================
        result = validate_analysis_result_contract_or_raise(
            result=result,
            request_id=request_id,
            auth_id=auth_id,
            geom_hash=geom_hash
        )

        # ============================================================
        # F13: AUDIT LOG SUCCESSO
        # ============================================================
        audit_log(
            "analyze_completed",
            request_id=request_id,
            auth_id=auth_id,
            geometry_hash=geom_hash,
            total_area_ha=result.get("totalArea"),
            priority_percent=result.get("agronomicContext", {}).get("priority_percent"),
            analysis_status=result.get("analysisStatus", {}).get("code"),
            contract_valid=result.get("contractValidation", {}).get("valid"),
            analysis_profile=ANALYSIS_PROFILE
        )

        # DEBUG PRINT (solo se abilitato)
        if DEBUG_ANALYSIS:
            print("=" * 60)
            print("[DEBUG] RIEPILOGO ANALISI COMPLETATA")
            print("=" * 60)
            print(f"[DEBUG] trendData length: {len(trendData)}")
            print(f"[DEBUG] vdiData length: {len(vesData) if COMPUTE_VDI else 'disabled'}")
            print(f"[DEBUG] totalArea: {round(totalAreaVal / 10000, 2)} ha")
            print(f"[DEBUG] priorityAreas count: {len(priorityAreas)}")
            print(f"[DEBUG] mahal_threshold: {round(MAHAL_THRESHOLD, 4)}")
            print(f"[DEBUG] fieldSignificance: {fieldSignificance}")
            print(f"[DEBUG] analysisReliability: {analysisReliability}")
            print(f"[DEBUG] analysisStatus: {analysisStatus}")
            print(f"[DEBUG] validation_quality: {analysis_quality['level']}")
            print(f"[DEBUG] validation_warnings: {len(validation_warnings)}")
            print(f"[DEBUG] analysis_profile: {ANALYSIS_PROFILE}")
            print("=" * 60)

        print("[INFO] Analisi completata con successo")
        timing_log(request_id, "analysis_total_completed", stage_time)

        return result

    except HTTPException as e:
        # F13: audit log per errori HTTP (client)
        audit_log(
            "analyze_rejected",
            request_id=request_id,
            auth_id=auth_id,
            status_code=e.status_code,
            detail=str(e.detail)
        )
        raise e

    except Exception:
        # F13: audit log per errori interni (server)
        error_id = str(uuid.uuid4())[:8]
        error_detail = traceback.format_exc()

        print(f"[ERROR] analyze_failed request_id={request_id} error_id={error_id}")
        print(error_detail)

        audit_log(
            "analyze_failed",
            request_id=request_id,
            auth_id=auth_id,
            error_id=error_id
        )

        raise HTTPException(
            status_code=500,
            detail=f"Errore interno durante l'analisi. Codice errore: {error_id}"
        )


# ================================================================
# STEP 6: ENDPOINT SCHEMA CONTRATTO DATI
# ================================================================

@app.get("/schema/analysis-result")
def get_analysis_result_schema(auth_id: str = Depends(verify_api_key)):
    """
    Restituisce lo JSON Schema ufficiale del payload /analyze.
    Utile per frontend, test e documentazione tecnica.
    """
    return AnalysisResultContract.model_json_schema()


# ================================================================
# AVVIO APP - ESECUZIONE VALIDAZIONI (F7)
# ================================================================
if __name__ == "__main__":
    import uvicorn
    # Esegui validazioni prima di avviare il server
    run_statistical_validations()
    uvicorn.run(app, host="0.0.0.0", port=8000)
else:
    # Esegui validazioni quando il modulo viene importato
    try:
        run_statistical_validations()
    except Exception as e:
        print(f"[WARN] Validazione statistica non disponibile: {e}")