import json
import os
from typing import Any, Dict, Optional

import psycopg2
from dotenv import load_dotenv


load_dotenv()


DEFAULT_SUBTYPE = "olive_generic_calabria"
DEFAULT_LAYER_VERSION = os.getenv("LANDCOVER_LAYER_VERSION", "cut_calabria_v1")

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
    """
    Accetta Geometry, Feature o FeatureCollection con una sola feature.
    Restituisce sempre una Geometry GeoJSON.
    """
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


def match_field_to_subtype(
    field_geojson: Dict[str, Any],
    *,
    database_url: Optional[str] = None,
    source_layer_version: str = DEFAULT_LAYER_VERSION,
) -> Dict[str, Any]:
    """
    Interseca il poligono del campo con i layer CUT caricati in PostGIS.

    Output:
    - subtype: tipologia di impianto assegnata
    - subtype_confidence: high / medium / low
    - subtype_layer_version: versione layer CUT
    - coverage_ratio: quota del campo coperta dal subtype assegnato
    - coverage_percent: stessa informazione in percentuale
    - matched_subtypes: dettaglio di tutte le intersezioni trovate

    Nota scientifica:
    questo NON deduce cultivar. Classifica solo la tipologia di impianto
    disponibile dal layer CUT: oliveto puro / consociato agrumi / consociato vite.
    """
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
            SUM(
                ST_Area(
                    ST_Intersection(g.geom, f.geom)::geography
                )
            ) AS overlap_m2,
            MAX(s.label_it) AS label_it
        FROM landcover_subtype_geometries g
        JOIN landcover_subtypes s
          ON s.id = g.subtype_id
        CROSS JOIN field_area f
        WHERE g.source_layer_version = %s
          AND g.geom && f.geom
          AND ST_Intersects(g.geom, f.geom)
        GROUP BY
            g.subtype_id,
            g.source_layer_version
    )
    SELECT
        i.subtype_id,
        i.label_it,
        i.source_layer_version,
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

    try:
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (geometry_json, source_layer_version))
                rows = cur.fetchall()

    except Exception as exc:
        raise LandcoverMatchingError(
            f"Errore durante il matching landcover_subtype: {exc}"
        ) from exc

    matched_subtypes = [
        {
            "subtype": row[0],
            "label_it": row[1],
            "source_layer_version": row[2],
            "overlap_m2": round(float(row[3] or 0), 2),
            "field_area_m2": round(float(row[4] or 0), 2),
            "coverage_ratio": round(float(row[5] or 0), 4),
            "coverage_percent": round(float(row[5] or 0) * 100, 2),
        }
        for row in rows
    ]

    if not matched_subtypes:
        return {
            "subtype": DEFAULT_SUBTYPE,
            "subtype_label_it": "Oliveto generico Calabria",
            "subtype_confidence": "low",
            "subtype_layer_version": source_layer_version,
            "coverage_ratio": 0.0,
            "coverage_percent": 0.0,
            "matched_subtypes": [],
            "note": (
                "Il campo non interseca i layer CUT olivicoli disponibili. "
                "Applicato fallback oliveto generico Calabria."
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
            "note": (
                "Intersezione presente ma copertura insufficiente per assegnare "
                "con robustezza una tipologia specifica. Applicato fallback generico."
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
        "note": (
            "Tipologia di impianto assegnata da intersezione spaziale CUT. "
            "Non rappresenta cultivar."
        ),
    }