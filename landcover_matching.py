import json
import os
from typing import Any, Dict, Optional

import psycopg2
from dotenv import load_dotenv


load_dotenv()


DEFAULT_SUBTYPE = "olive_generic_calabria"
DEFAULT_LAYER_VERSION = os.getenv("LANDCOVER_LAYER_VERSION", "cut_calabria_v1")

DEFAULT_QC_VERSION = "olive_pure_geom_qc_v2"
DEFAULT_MATCHING_LAYER = "landcover_olive_pure_high_confidence_v2"

DEFAULT_BASELINE_VERSION = "olive_baseline_v1"
DEFAULT_BASELINE_LAYER = "landcover_olive_pure_baseline_v1"

DEFAULT_STRICT_BASELINE_VERSION = "olive_baseline_strict_seed_v1"
DEFAULT_STRICT_BASELINE_LAYER = "landcover_olive_pure_baseline_strict_seed_v1"

HIGH_CONFIDENCE_COVERAGE = float(
    os.getenv("LANDCOVER_HIGH_CONFIDENCE_COVERAGE", "0.75")
)

MEDIUM_CONFIDENCE_COVERAGE = float(
    os.getenv("LANDCOVER_MEDIUM_CONFIDENCE_COVERAGE", "0.50")
)


class LandcoverMatchingError(RuntimeError):
    pass


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise LandcoverMatchingError(
            "DATABASE_URL non configurato. Impostarlo in .env o .env.example."
        )

    return database_url


def normalize_geojson_geometry(geojson: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(geojson, dict):
        raise LandcoverMatchingError("GeoJSON non valido: atteso oggetto JSON.")

    geojson_type = geojson.get("type")

    if geojson_type in {"Polygon", "MultiPolygon"}:
        return geojson

    if geojson_type == "Feature":
        geometry = geojson.get("geometry")
        if not geometry:
            raise LandcoverMatchingError("Feature GeoJSON senza geometry.")
        return normalize_geojson_geometry(geometry)

    if geojson_type == "FeatureCollection":
        features = geojson.get("features") or []
        if len(features) != 1:
            raise LandcoverMatchingError(
                "FeatureCollection non supportata: attesa una sola feature campo."
            )
        return normalize_geojson_geometry(features[0])

    raise LandcoverMatchingError(
        f"Tipo GeoJSON non supportato per matching landcover: {geojson_type}"
    )


def classify_subtype_confidence(coverage_ratio: float) -> str:
    if coverage_ratio >= HIGH_CONFIDENCE_COVERAGE:
        return "high"

    if coverage_ratio >= MEDIUM_CONFIDENCE_COVERAGE:
        return "medium"

    return "low"


def build_baseline_match_payload(
    row,
    *,
    baseline_version: str,
    baseline_layer: str,
    match_key: str,
) -> Optional[Dict[str, Any]]:
    if not row:
        return None

    coverage_ratio = round(float(row[18] or 0), 4)
    coverage_percent = round(coverage_ratio * 100, 2)
    is_match = coverage_ratio >= HIGH_CONFIDENCE_COVERAGE

    return {
        f"{match_key}_version": baseline_version,
        f"{match_key}_layer": baseline_layer,
        f"{match_key}_match": is_match,
        f"{match_key}_coverage_ratio": coverage_ratio,
        f"{match_key}_coverage_percent": coverage_percent,

        "subtype": row[0],
        "label_it": row[1],
        "source_layer_version": row[2],

        "landcover_qc_version": row[3],
        "landcover_qc_class": row[4],

        "visual_qc_version": row[5],
        "visual_label": row[6],

        "urban_qc_version": row[7],
        "artificial_flag": row[8],

        "spectral_qc_version": row[9],
        "spectral_flag": row[10],
        "spectral_n_observations": int(row[11] or 0),

        "ndvi_median": round(float(row[12]), 4) if row[12] is not None else None,
        "evi_median": round(float(row[13]), 4) if row[13] is not None else None,
        "ndmi_median": round(float(row[14]), 4) if row[14] is not None else None,
        "bsi_median": round(float(row[15]), 4) if row[15] is not None else None,

        "overlap_m2": round(float(row[16] or 0), 2),
        "field_area_m2": round(float(row[17] or 0), 2),
    }


def match_field_to_subtype(
    field_geojson: Dict[str, Any],
    *,
    database_url: Optional[str] = None,
    source_layer_version: str = DEFAULT_LAYER_VERSION,
) -> Dict[str, Any]:
    geometry = normalize_geojson_geometry(field_geojson)
    geometry_json = json.dumps(geometry)

    dsn = database_url or get_database_url()

    query = """
    WITH field_input AS (
        SELECT
            ST_Multi(
                ST_CollectionExtract(
                    ST_MakeValid(
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                    ),
                    3
                )
            )::geometry(MultiPolygon, 4326) AS geom
    ),
    field_area AS (
        SELECT
            geom,
            ST_Area(geom::geography) AS area_m2
        FROM field_input
    ),
    intersections AS (
        SELECT
            g.subtype_id,
            g.source_layer_version,
            g.qc_version,
            g.qc_class,
            g.usable_for_baseline,
            SUM(
                ST_Area(
                    ST_Intersection(g.geom, f.geom)::geography
                )
            ) AS overlap_m2,
            MAX(s.label_it) AS label_it
        FROM landcover_olive_pure_high_confidence_v2 g
        JOIN landcover_subtypes s
          ON s.id = g.subtype_id
        CROSS JOIN field_area f
        WHERE g.source_layer_version = %s
          AND g.geom && f.geom
          AND ST_Intersects(g.geom, f.geom)
        GROUP BY
            g.subtype_id,
            g.source_layer_version,
            g.qc_version,
            g.qc_class,
            g.usable_for_baseline
    )
    SELECT
        i.subtype_id,
        i.label_it,
        i.source_layer_version,
        i.qc_version,
        i.qc_class,
        i.usable_for_baseline,
        i.overlap_m2,
        f.area_m2,
        CASE
            WHEN f.area_m2 > 0 THEN i.overlap_m2 / f.area_m2
            ELSE 0
        END AS coverage_ratio
    FROM intersections i
    CROSS JOIN field_area f
    ORDER BY coverage_ratio DESC;
    """

    baseline_query_template = """
    WITH field_input AS (
        SELECT
            ST_Multi(
                ST_CollectionExtract(
                    ST_MakeValid(
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                    ),
                    3
                )
            )::geometry(MultiPolygon, 4326) AS geom
    ),
    field_area AS (
        SELECT
            geom,
            ST_Area(geom::geography) AS area_m2
        FROM field_input
    ),
    intersections AS (
        SELECT
            b.subtype_id,
            b.source_layer_version,
            b.qc_version,
            b.qc_class,
            b.visual_qc_version,
            b.visual_label,
            b.urban_qc_version,
            b.artificial_flag,
            b.spectral_qc_version,
            b.spectral_flag,
            b.n_observations,
            b.ndvi_median,
            b.evi_median,
            b.ndmi_median,
            b.bsi_median,
            SUM(
                ST_Area(
                    ST_Intersection(b.geom, f.geom)::geography
                )
            ) AS overlap_m2,
            MAX(s.label_it) AS label_it
        FROM {baseline_layer} b
        JOIN landcover_subtypes s
          ON s.id = b.subtype_id
        CROSS JOIN field_area f
        WHERE b.source_layer_version = %s
          AND b.geom && f.geom
          AND ST_Intersects(b.geom, f.geom)
        GROUP BY
            b.subtype_id,
            b.source_layer_version,
            b.qc_version,
            b.qc_class,
            b.visual_qc_version,
            b.visual_label,
            b.urban_qc_version,
            b.artificial_flag,
            b.spectral_qc_version,
            b.spectral_flag,
            b.n_observations,
            b.ndvi_median,
            b.evi_median,
            b.ndmi_median,
            b.bsi_median
    )
    SELECT
        i.subtype_id,
        i.label_it,
        i.source_layer_version,
        i.qc_version,
        i.qc_class,
        i.visual_qc_version,
        i.visual_label,
        i.urban_qc_version,
        i.artificial_flag,
        i.spectral_qc_version,
        i.spectral_flag,
        i.n_observations,
        i.ndvi_median,
        i.evi_median,
        i.ndmi_median,
        i.bsi_median,
        i.overlap_m2,
        f.area_m2,
        CASE
            WHEN f.area_m2 > 0 THEN i.overlap_m2 / f.area_m2
            ELSE 0
        END AS coverage_ratio
    FROM intersections i
    CROSS JOIN field_area f
    ORDER BY coverage_ratio DESC
    LIMIT 1;
    """

    baseline_query = baseline_query_template.format(
        baseline_layer=DEFAULT_BASELINE_LAYER
    )

    strict_baseline_query = baseline_query_template.format(
        baseline_layer=DEFAULT_STRICT_BASELINE_LAYER
    )

    try:
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (geometry_json, source_layer_version))
                rows = cur.fetchall()

                cur.execute(baseline_query, (geometry_json, source_layer_version))
                baseline_row = cur.fetchone()

                cur.execute(strict_baseline_query, (geometry_json, source_layer_version))
                strict_baseline_row = cur.fetchone()

    except Exception as exc:
        raise LandcoverMatchingError(
            f"Errore durante il matching landcover_subtype: {exc}"
        ) from exc

    matched_subtypes = [
        {
            "subtype": row[0],
            "label_it": row[1],
            "source_layer_version": row[2],
            "landcover_qc_version": row[3],
            "landcover_qc_class": row[4],
            "usable_for_baseline": bool(row[5]),
            "overlap_m2": round(float(row[6] or 0), 2),
            "field_area_m2": round(float(row[7] or 0), 2),
            "coverage_ratio": round(float(row[8] or 0), 4),
            "coverage_percent": round(float(row[8] or 0) * 100, 2),
        }
        for row in rows
    ]

    baseline_v1 = build_baseline_match_payload(
        baseline_row,
        baseline_version=DEFAULT_BASELINE_VERSION,
        baseline_layer=DEFAULT_BASELINE_LAYER,
        match_key="baseline_v1",
    )

    strict_baseline_v1 = build_baseline_match_payload(
        strict_baseline_row,
        baseline_version=DEFAULT_STRICT_BASELINE_VERSION,
        baseline_layer=DEFAULT_STRICT_BASELINE_LAYER,
        match_key="strict_baseline_v1",
    )

    baseline_v1_match = bool(
        baseline_v1 and baseline_v1.get("baseline_v1_match")
    )

    strict_baseline_v1_match = bool(
        strict_baseline_v1 and strict_baseline_v1.get("strict_baseline_v1_match")
    )

    common_baseline_payload = {
        "baseline_version": DEFAULT_BASELINE_VERSION,
        "baseline_layer": DEFAULT_BASELINE_LAYER,
        "baseline_v1_match": baseline_v1_match,
        "baseline_v1_coverage_ratio": baseline_v1.get("baseline_v1_coverage_ratio") if baseline_v1 else 0.0,
        "baseline_v1_coverage_percent": baseline_v1.get("baseline_v1_coverage_percent") if baseline_v1 else 0.0,
        "baseline_v1": baseline_v1,

        "strict_baseline_version": DEFAULT_STRICT_BASELINE_VERSION,
        "strict_baseline_layer": DEFAULT_STRICT_BASELINE_LAYER,
        "strict_baseline_v1_match": strict_baseline_v1_match,
        "strict_baseline_v1_coverage_ratio": strict_baseline_v1.get("strict_baseline_v1_coverage_ratio") if strict_baseline_v1 else 0.0,
        "strict_baseline_v1_coverage_percent": strict_baseline_v1.get("strict_baseline_v1_coverage_percent") if strict_baseline_v1 else 0.0,
        "strict_baseline_v1": strict_baseline_v1,
        "usable_for_strict_baseline": strict_baseline_v1_match,
    }

    if not matched_subtypes:
        return {
            "subtype": DEFAULT_SUBTYPE,
            "subtype_label_it": "Oliveto generico Calabria",
            "subtype_confidence": "low",
            "subtype_layer_version": source_layer_version,
            "coverage_ratio": 0.0,
            "coverage_percent": 0.0,
            "matched_subtypes": [],
            "landcover_qc_version": DEFAULT_QC_VERSION,
            "landcover_qc_class": None,
            "usable_for_baseline": False,
            "matching_layer": DEFAULT_MATCHING_LAYER,
            **common_baseline_payload,
            "note": (
                "Il campo non interseca aree olive_pure high-confidence QC v2. "
                "Non idoneo per baseline; applicato fallback descrittivo generico."
            ),
        }

    best = matched_subtypes[0]
    confidence = classify_subtype_confidence(best["coverage_ratio"])

    if confidence == "low":
        return {
            "subtype": DEFAULT_SUBTYPE,
            "subtype_label_it": "Oliveto generico Calabria",
            "subtype_confidence": "low",
            "subtype_layer_version": source_layer_version,
            "coverage_ratio": best["coverage_ratio"],
            "coverage_percent": best["coverage_percent"],
            "matched_subtypes": matched_subtypes,
            "landcover_qc_version": best.get("landcover_qc_version"),
            "landcover_qc_class": best.get("landcover_qc_class"),
            "usable_for_baseline": False,
            "matching_layer": DEFAULT_MATCHING_LAYER,
            **common_baseline_payload,
            "note": (
                "Intersezione con aree olive_pure high-confidence presente ma copertura "
                "insufficiente per assegnare robustamente il campo alla baseline."
            ),
        }

    return {
        "subtype": best["subtype"],
        "subtype_label_it": best["label_it"],
        "subtype_confidence": confidence,
        "subtype_layer_version": best["source_layer_version"],
        "coverage_ratio": best["coverage_ratio"],
        "coverage_percent": best["coverage_percent"],
        "matched_subtypes": matched_subtypes,
        "landcover_qc_version": best.get("landcover_qc_version"),
        "landcover_qc_class": best.get("landcover_qc_class"),
        "usable_for_baseline": bool(confidence == "high" and baseline_v1_match),
        "matching_layer": DEFAULT_MATCHING_LAYER,
        **common_baseline_payload,
        "note": (
            "Tipologia di impianto assegnata da intersezione spaziale con layer "
            "olive_pure high-confidence QC v2. Non rappresenta cultivar."
        ),
    }