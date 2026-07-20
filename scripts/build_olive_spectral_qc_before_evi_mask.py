import json
import os
import time
from typing import Dict, Iterable, List, Optional, Tuple

import ee
import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql


load_dotenv()


SPECTRAL_QC_VERSION = "olive_spectral_qc_v1"

SOURCE_VIEW = os.getenv(
    "OLIVE_SPECTRAL_SOURCE_VIEW",
    "landcover_olive_pure_baseline_seed_no_urban_v1",
)

BATCH_SIZE = int(os.getenv("OLIVE_SPECTRAL_BATCH_SIZE", "10"))
LIMIT = int(os.getenv("OLIVE_SPECTRAL_LIMIT", "0"))

S2_START = os.getenv("OLIVE_SPECTRAL_START", "2023-01-01")
S2_END = os.getenv("OLIVE_SPECTRAL_END", "2026-01-01")
MAX_CLOUD = float(os.getenv("OLIVE_SPECTRAL_MAX_CLOUD", "40"))

ALLOWED_SOURCE_VIEWS = {
    "landcover_olive_pure_baseline_seed_no_urban_v1",
    "landcover_olive_pure_baseline_seed_v1",
    "landcover_olive_pure_visual_training_v1",
    "landcover_olive_visual_training_v2_missing_spectral_v1",  # AGGIUNTA
    "landcover_olive_visual_training_v2_missing_spectral_v1",
}


def chunks(values: List[Tuple[str, Dict]], size: int) -> Iterable[List[Tuple[str, Dict]]]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


def init_ee():
    project = os.getenv("GEE_PROJECT")

    if project:
        try:
            ee.Initialize(project=project)
            print(f"[INFO] Earth Engine inizializzato con project={project}")
            return
        except Exception as exc:
            print(f"[WARN] EE project non utilizzabile: {project}")
            print(f"[WARN] Dettaglio: {str(exc)[:300]}")
            print("[INFO] Riprovo senza project esplicito...")

    try:
        ee.Initialize()
        print("[INFO] Earth Engine inizializzato senza project esplicito")
        return
    except Exception:
        print("[INFO] Autenticazione Earth Engine richiesta...")
        ee.Authenticate()
        ee.Initialize()
        print("[INFO] Earth Engine inizializzato dopo autenticazione")


def fetch_geometries(database_url: str) -> List[Tuple[str, Dict]]:
    if SOURCE_VIEW not in ALLOWED_SOURCE_VIEWS:
        raise RuntimeError(f"Vista sorgente non ammessa: {SOURCE_VIEW}")

    limit_clause = sql.SQL("")

    if LIMIT > 0:
        limit_clause = sql.SQL(" LIMIT {}").format(sql.Literal(LIMIT))

    query = sql.SQL("""
        SELECT
            id::text,
            ST_AsGeoJSON(geom, 6)::text
        FROM {source_view}
        ORDER BY id
    """).format(
        source_view=sql.Identifier(SOURCE_VIEW)
    ) + limit_clause

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

    return [(row[0], json.loads(row[1])) for row in rows]


def mask_s2_clouds(image):
    scl = image.select("SCL")

    valid = (
        scl.neq(0)   # no data
        .And(scl.neq(1))   # saturated / defective
        .And(scl.neq(3))   # cloud shadow
        .And(scl.neq(8))   # cloud medium probability
        .And(scl.neq(9))   # cloud high probability
        .And(scl.neq(10))  # thin cirrus
        .And(scl.neq(11))  # snow / ice
    )

    return image.updateMask(valid)


def add_indices(image):
    blue = image.select("B2").multiply(0.0001)
    red = image.select("B4").multiply(0.0001)
    nir = image.select("B8").multiply(0.0001)
    swir1 = image.select("B11").multiply(0.0001)

    ndvi = nir.subtract(red).divide(nir.add(red)).rename("NDVI")

    evi = (
        nir.subtract(red)
        .multiply(2.5)
        .divide(
            nir
            .add(red.multiply(6))
            .subtract(blue.multiply(7.5))
            .add(1)
        )
        .rename("EVI")
    )

    ndmi = nir.subtract(swir1).divide(nir.add(swir1)).rename("NDMI")

    bsi = (
        swir1.add(red)
        .subtract(nir.add(blue))
        .divide(swir1.add(red).add(nir).add(blue))
        .rename("BSI")
    )

    return image.addBands([ndvi, evi, ndmi, bsi])


def to_float(value) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_float_prop(props: Dict, *names: str) -> Optional[float]:
    for name in names:
        value = props.get(name)
        if value is not None:
            return to_float(value)
    return None


def classify_spectral(metrics: Dict) -> Tuple[str, bool, Optional[str]]:
    n_obs = int(metrics.get("n_observations") or 0)

    ndvi = to_float(metrics.get("ndvi_median"))
    evi = to_float(metrics.get("evi_median"))
    ndmi = to_float(metrics.get("ndmi_median"))
    bsi = to_float(metrics.get("bsi_median"))

    if n_obs < 8:
        return "excluded", False, "insufficient_observations"

    if ndvi is None or evi is None or bsi is None:
        return "excluded", False, "missing_spectral_metrics"

    if ndmi is None:
        ndmi = 0.0

    if ndvi >= 0.35 and evi >= 0.18 and bsi <= 0.20:
        return "strong", True, None

    if ndvi >= 0.28 and evi >= 0.12 and bsi <= 0.32 and ndmi >= -0.35:
        return "moderate", True, "moderate_olive_spectral_signal"

    return "weak", False, "weak_or_inconsistent_vegetation_signal"


def empty_record(source_geometry_id: str, reason: str) -> Dict:
    return {
        "source_geometry_id": source_geometry_id,
        "n_observations": 0,

        "ndvi_median": None,
        "ndvi_p25": None,
        "ndvi_p75": None,
        "ndvi_stddev": None,

        "evi_median": None,
        "evi_p25": None,
        "evi_p75": None,
        "evi_stddev": None,

        "ndmi_median": None,
        "ndmi_p25": None,
        "ndmi_p75": None,
        "ndmi_stddev": None,

        "bsi_median": None,
        "bsi_p25": None,
        "bsi_p75": None,
        "bsi_stddev": None,

        "spectral_flag": "excluded",
        "usable_for_baseline_spectral": False,
        "exclusion_reason": reason,
    }


def reduce_spectral_metrics(batch: List[Tuple[str, Dict]]) -> List[Dict]:
    features = []

    for source_geometry_id, geometry in batch:
        feature = ee.Feature(
            ee.Geometry(geometry),
            {
                "source_geometry_id": source_geometry_id,
            },
        )
        features.append(feature)

    fc = ee.FeatureCollection(features)

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(S2_START, S2_END)
        .filterBounds(fc.geometry())
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", MAX_CLOUD))
        .map(mask_s2_clouds)
        .map(add_indices)
        .select(["NDVI", "EVI", "NDMI", "BSI"])
    )

    n_observations = int(collection.size().getInfo())

    if n_observations == 0:
        return [
            empty_record(source_geometry_id, "no_sentinel2_observations")
            for source_geometry_id, _ in batch
        ]

    median = collection.median()
    percentiles = collection.reduce(ee.Reducer.percentile([25, 75]))
    stddev = collection.reduce(ee.Reducer.stdDev())
    valid_count = collection.select("NDVI").count().rename("NOBS")

    image = ee.Image.cat([median, percentiles, stddev, valid_count])

    reduced = image.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        scale=10,
        tileScale=4,
    )

    info = reduced.getInfo()

    results = []

    for feature in info.get("features", []):
        props = feature.get("properties", {})

        source_geometry_id = props.get("source_geometry_id")

        n_obs_feature = get_float_prop(props, "NOBS", "NOBS_mean")

        if n_obs_feature is None:
            n_obs_feature = 0

        metrics = {
            "source_geometry_id": source_geometry_id,
            "n_observations": int(round(n_obs_feature)),

            "ndvi_median": get_float_prop(props, "NDVI", "NDVI_mean"),
            "ndvi_p25": get_float_prop(props, "NDVI_p25", "NDVI_p25_mean"),
            "ndvi_p75": get_float_prop(props, "NDVI_p75", "NDVI_p75_mean"),
            "ndvi_stddev": get_float_prop(props, "NDVI_stdDev", "NDVI_stdDev_mean"),

            "evi_median": get_float_prop(props, "EVI", "EVI_mean"),
            "evi_p25": get_float_prop(props, "EVI_p25", "EVI_p25_mean"),
            "evi_p75": get_float_prop(props, "EVI_p75", "EVI_p75_mean"),
            "evi_stddev": get_float_prop(props, "EVI_stdDev", "EVI_stdDev_mean"),

            "ndmi_median": get_float_prop(props, "NDMI", "NDMI_mean"),
            "ndmi_p25": get_float_prop(props, "NDMI_p25", "NDMI_p25_mean"),
            "ndmi_p75": get_float_prop(props, "NDMI_p75", "NDMI_p75_mean"),
            "ndmi_stddev": get_float_prop(props, "NDMI_stdDev", "NDMI_stdDev_mean"),

            "bsi_median": get_float_prop(props, "BSI", "BSI_mean"),
            "bsi_p25": get_float_prop(props, "BSI_p25", "BSI_p25_mean"),
            "bsi_p75": get_float_prop(props, "BSI_p75", "BSI_p75_mean"),
            "bsi_stddev": get_float_prop(props, "BSI_stdDev", "BSI_stdDev_mean"),
        }

        spectral_flag, usable, reason = classify_spectral(metrics)

        metrics.update(
            {
                "spectral_flag": spectral_flag,
                "usable_for_baseline_spectral": usable,
                "exclusion_reason": reason,
            }
        )

        results.append(metrics)

    returned_ids = {
        record["source_geometry_id"]
        for record in results
        if record.get("source_geometry_id")
    }

    for source_geometry_id, _ in batch:
        if source_geometry_id not in returned_ids:
            results.append(
                empty_record(
                    source_geometry_id,
                    "missing_reduce_region_result",
                )
            )

    return results


def upsert_results(database_url: str, records: List[Dict]):
    if not records:
        return

    query = """
    INSERT INTO landcover_subtype_spectral_qc (
        source_geometry_id,
        spectral_qc_version,
        source_view,
        n_observations,

        ndvi_median,
        ndvi_p25,
        ndvi_p75,
        ndvi_stddev,

        evi_median,
        evi_p25,
        evi_p75,
        evi_stddev,

        ndmi_median,
        ndmi_p25,
        ndmi_p75,
        ndmi_stddev,

        bsi_median,
        bsi_p25,
        bsi_p75,
        bsi_stddev,

        spectral_flag,
        usable_for_baseline_spectral,
        exclusion_reason
    )
    VALUES (
        %(source_geometry_id)s,
        %(spectral_qc_version)s,
        %(source_view)s,
        %(n_observations)s,

        %(ndvi_median)s,
        %(ndvi_p25)s,
        %(ndvi_p75)s,
        %(ndvi_stddev)s,

        %(evi_median)s,
        %(evi_p25)s,
        %(evi_p75)s,
        %(evi_stddev)s,

        %(ndmi_median)s,
        %(ndmi_p25)s,
        %(ndmi_p75)s,
        %(ndmi_stddev)s,

        %(bsi_median)s,
        %(bsi_p25)s,
        %(bsi_p75)s,
        %(bsi_stddev)s,

        %(spectral_flag)s,
        %(usable_for_baseline_spectral)s,
        %(exclusion_reason)s
    )
    ON CONFLICT (source_geometry_id, spectral_qc_version)
    DO UPDATE SET
        source_view = EXCLUDED.source_view,
        n_observations = EXCLUDED.n_observations,

        ndvi_median = EXCLUDED.ndvi_median,
        ndvi_p25 = EXCLUDED.ndvi_p25,
        ndvi_p75 = EXCLUDED.ndvi_p75,
        ndvi_stddev = EXCLUDED.ndvi_stddev,

        evi_median = EXCLUDED.evi_median,
        evi_p25 = EXCLUDED.evi_p25,
        evi_p75 = EXCLUDED.evi_p75,
        evi_stddev = EXCLUDED.evi_stddev,

        ndmi_median = EXCLUDED.ndmi_median,
        ndmi_p25 = EXCLUDED.ndmi_p25,
        ndmi_p75 = EXCLUDED.ndmi_p75,
        ndmi_stddev = EXCLUDED.ndmi_stddev,

        bsi_median = EXCLUDED.bsi_median,
        bsi_p25 = EXCLUDED.bsi_p25,
        bsi_p75 = EXCLUDED.bsi_p75,
        bsi_stddev = EXCLUDED.bsi_stddev,

        spectral_flag = EXCLUDED.spectral_flag,
        usable_for_baseline_spectral = EXCLUDED.usable_for_baseline_spectral,
        exclusion_reason = EXCLUDED.exclusion_reason,
        computed_at = now();
    """

    payload = [
        {
            **record,
            "spectral_qc_version": SPECTRAL_QC_VERSION,
            "source_view": SOURCE_VIEW,
        }
        for record in records
    ]

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(query, payload)
            conn.commit()


def print_summary(database_url: str):
    summary_sql = """
    SELECT
        spectral_flag,
        usable_for_baseline_spectral,
        COUNT(*) AS n,
        ROUND(AVG(n_observations)::numeric, 2) AS avg_n_obs,
        ROUND(AVG(ndvi_median)::numeric, 4) AS avg_ndvi,
        ROUND(AVG(evi_median)::numeric, 4) AS avg_evi,
        ROUND(AVG(ndmi_median)::numeric, 4) AS avg_ndmi,
        ROUND(AVG(bsi_median)::numeric, 4) AS avg_bsi
    FROM landcover_subtype_spectral_qc
    WHERE spectral_qc_version = %(version)s
      AND source_view = %(source_view)s
    GROUP BY spectral_flag, usable_for_baseline_spectral
    ORDER BY
        CASE spectral_flag
            WHEN 'strong' THEN 1
            WHEN 'moderate' THEN 2
            WHEN 'weak' THEN 3
            WHEN 'excluded' THEN 4
        END;
    """

    baseline_sql = """
    SELECT
        COUNT(*) AS baseline_features
    FROM landcover_olive_pure_baseline_v1;
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                summary_sql,
                {
                    "version": SPECTRAL_QC_VERSION,
                    "source_view": SOURCE_VIEW,
                },
            )
            rows = cur.fetchall()

            print("")
            print("Spectral QC summary")
            print("-------------------")

            for row in rows:
                print(
                    f"{row[0]} | usable={row[1]} | n={row[2]} | "
                    f"obs={row[3]} | ndvi={row[4]} | evi={row[5]} | "
                    f"ndmi={row[6]} | bsi={row[7]}"
                )

            cur.execute(baseline_sql)
            baseline = cur.fetchone()[0]

            print("")
            print(f"Baseline v1 features: {baseline}")


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    init_ee()

    features = fetch_geometries(database_url)

    print(f"Source view: {SOURCE_VIEW}")
    print(f"Spectral QC version: {SPECTRAL_QC_VERSION}")
    print(f"Features da processare: {len(features)}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Periodo Sentinel-2: {S2_START} - {S2_END}")
    print(f"Cloud max: {MAX_CLOUD}")

    processed = 0

    for idx, batch in enumerate(chunks(features, BATCH_SIZE), start=1):
        results = reduce_spectral_metrics(batch)
        upsert_results(database_url, results)

        processed += len(batch)

        print(f"Batch {idx} completato | {processed}/{len(features)}")

        time.sleep(0.5)

    print_summary(database_url)


if __name__ == "__main__":
    main()