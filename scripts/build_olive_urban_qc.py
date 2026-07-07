import json
import os
import time
from typing import Dict, Iterable, List, Tuple

import ee
import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql


load_dotenv()


URBAN_QC_VERSION = "olive_urban_qc_v1"
SOURCE_VIEW = os.getenv(
    "OLIVE_URBAN_SOURCE_VIEW",
    "landcover_olive_pure_baseline_seed_v1",
)

BATCH_SIZE = int(os.getenv("OLIVE_URBAN_BATCH_SIZE", "25"))
LIMIT = int(os.getenv("OLIVE_URBAN_LIMIT", "0"))

DYNAMIC_WORLD_START = os.getenv("OLIVE_URBAN_DW_START", "2024-01-01")
DYNAMIC_WORLD_END = os.getenv("OLIVE_URBAN_DW_END", "2026-12-31")

ALLOWED_SOURCE_VIEWS = {
    "landcover_olive_pure_baseline_seed_v1",
    "landcover_olive_pure_high_confidence_v2",
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


def classify_artificial(
    built_cover_percent: float,
    dynamic_built_mean: float,
    dynamic_built_p95: float,
) -> Tuple[str, bool, str | None]:
    built_cover_percent = built_cover_percent or 0.0
    dynamic_built_mean = dynamic_built_mean or 0.0
    dynamic_built_p95 = dynamic_built_p95 or 0.0

    # Esclusione forte: presenza artificiale evidente
    if built_cover_percent >= 10.0 or dynamic_built_p95 >= 0.45:
        return "high", False, "high_artificial_surface_signal"

    # Esclusione prudenziale: segnale artificiale non trascurabile
    if built_cover_percent >= 3.0 or dynamic_built_p95 >= 0.20:
        return "medium", False, "medium_artificial_surface_signal"

    # Segnale debole: ammesso, ma tracciato
    if (
        built_cover_percent >= 1.0
        or dynamic_built_p95 >= 0.08
        or dynamic_built_mean >= 0.08
    ):
        return "low", True, "low_artificial_surface_signal"

    return "none", True, None


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


def reduce_urban_metrics(batch: List[Tuple[str, Dict]]) -> List[Dict]:
    features = []

    for source_geometry_id, geometry in batch:
        geom = ee.Geometry(geometry)
        features.append(
            ee.Feature(
                geom,
                {
                    "source_geometry_id": source_geometry_id,
                },
            )
        )

    fc = ee.FeatureCollection(features)

    worldcover = ee.Image("ESA/WorldCover/v200/2021")
    built_mask = worldcover.eq(50).rename("built")

    dynamic_world = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(DYNAMIC_WORLD_START, DYNAMIC_WORLD_END)
        .select("built")
    )

    dynamic_size = dynamic_world.size().getInfo()

    if dynamic_size > 0:
        dynamic_built = dynamic_world.median().rename("dynamic_built")
    else:
        dynamic_built = ee.Image.constant(0).rename("dynamic_built")

    image = ee.Image.cat([built_mask, dynamic_built])

    reducer = (
        ee.Reducer.mean()
        .combine(ee.Reducer.percentile([95]), sharedInputs=True)
    )

    reduced = image.reduceRegions(
        collection=fc,
        reducer=reducer,
        scale=10,
        tileScale=4,
    )

    info = reduced.getInfo()

    results = []

    for feature in info.get("features", []):
        props = feature.get("properties", {})

        source_geometry_id = props.get("source_geometry_id")

        built_mean = props.get("built_mean")
        dynamic_mean = props.get("dynamic_built_mean")
        dynamic_p95 = props.get("dynamic_built_p95")

        built_cover_percent = float(built_mean or 0) * 100.0
        dynamic_built_mean = float(dynamic_mean or 0)
        dynamic_built_p95 = float(dynamic_p95 or 0)

        artificial_flag, usable, reason = classify_artificial(
            built_cover_percent,
            dynamic_built_mean,
            dynamic_built_p95,
        )

        results.append(
            {
                "source_geometry_id": source_geometry_id,
                "built_cover_percent": round(built_cover_percent, 4),
                "dynamic_built_mean": round(dynamic_built_mean, 6),
                "dynamic_built_p95": round(dynamic_built_p95, 6),
                "artificial_flag": artificial_flag,
                "usable_for_baseline_context": usable,
                "exclusion_reason": reason,
            }
        )

    return results


def upsert_results(database_url: str, records: List[Dict]):
    if not records:
        return

    query = """
    INSERT INTO landcover_subtype_urban_qc (
        source_geometry_id,
        urban_qc_version,
        source_view,
        built_cover_percent,
        dynamic_built_mean,
        dynamic_built_p95,
        artificial_flag,
        usable_for_baseline_context,
        exclusion_reason
    )
    VALUES (
        %(source_geometry_id)s,
        %(urban_qc_version)s,
        %(source_view)s,
        %(built_cover_percent)s,
        %(dynamic_built_mean)s,
        %(dynamic_built_p95)s,
        %(artificial_flag)s,
        %(usable_for_baseline_context)s,
        %(exclusion_reason)s
    )
    ON CONFLICT (source_geometry_id, urban_qc_version)
    DO UPDATE SET
        source_view = EXCLUDED.source_view,
        built_cover_percent = EXCLUDED.built_cover_percent,
        dynamic_built_mean = EXCLUDED.dynamic_built_mean,
        dynamic_built_p95 = EXCLUDED.dynamic_built_p95,
        artificial_flag = EXCLUDED.artificial_flag,
        usable_for_baseline_context = EXCLUDED.usable_for_baseline_context,
        exclusion_reason = EXCLUDED.exclusion_reason,
        computed_at = now();
    """

    payload = [
        {
            **record,
            "urban_qc_version": URBAN_QC_VERSION,
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
        artificial_flag,
        COUNT(*) AS n,
        ROUND(AVG(built_cover_percent)::numeric, 4) AS avg_built_cover_percent,
        ROUND(AVG(dynamic_built_mean)::numeric, 6) AS avg_dynamic_built_mean,
        ROUND(AVG(dynamic_built_p95)::numeric, 6) AS avg_dynamic_built_p95
    FROM landcover_subtype_urban_qc
    WHERE urban_qc_version = %(version)s
      AND source_view = %(source_view)s
    GROUP BY artificial_flag
    ORDER BY
        CASE artificial_flag
            WHEN 'none' THEN 1
            WHEN 'low' THEN 2
            WHEN 'medium' THEN 3
            WHEN 'high' THEN 4
        END;
    """

    usable_sql = """
    SELECT
        COUNT(*) AS usable_features
    FROM landcover_subtype_urban_qc
    WHERE urban_qc_version = %(version)s
      AND source_view = %(source_view)s
      AND usable_for_baseline_context = TRUE;
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                summary_sql,
                {
                    "version": URBAN_QC_VERSION,
                    "source_view": SOURCE_VIEW,
                },
            )
            rows = cur.fetchall()

            print("")
            print("Urban QC summary")
            print("----------------")
            for row in rows:
                print(
                    f"{row[0]} | n={row[1]} | "
                    f"built%={row[2]} | "
                    f"dw_mean={row[3]} | "
                    f"dw_p95={row[4]}"
                )

            cur.execute(
                usable_sql,
                {
                    "version": URBAN_QC_VERSION,
                    "source_view": SOURCE_VIEW,
                },
            )
            usable = cur.fetchone()[0]

            print("")
            print(f"Usable for baseline context: {usable}")


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    init_ee()

    features = fetch_geometries(database_url)

    print(f"Source view: {SOURCE_VIEW}")
    print(f"Urban QC version: {URBAN_QC_VERSION}")
    print(f"Features da processare: {len(features)}")
    print(f"Batch size: {BATCH_SIZE}")

    processed = 0

    for idx, batch in enumerate(chunks(features, BATCH_SIZE), start=1):
        results = reduce_urban_metrics(batch)
        upsert_results(database_url, results)

        processed += len(batch)

        print(f"Batch {idx} completato | {processed}/{len(features)}")

        time.sleep(0.5)

    print_summary(database_url)


if __name__ == "__main__":
    main()