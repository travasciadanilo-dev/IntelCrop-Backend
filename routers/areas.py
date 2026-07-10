import json
import os
from decimal import Decimal
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from psycopg2.extras import RealDictCursor


load_dotenv()


router = APIRouter(prefix="/areas", tags=["areas"])


REGIONAL_CATALOG_VIEW = "area_catalog_v1_diagnostic"
ENTITY_CATALOG_VIEW = "area_catalog_v1_entity_scope"

ALLOWED_RELIABILITY_CLASSES = {
    "low",
    "compatible",
    "high",
    "very_high",
}

ALLOWED_ZONES = {
    "north_calabria",
    "central_calabria",
    "south_calabria",
}

ALLOWED_FORMATS = {
    "json",
    "geojson",
}


def get_connection():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise HTTPException(
            status_code=500,
            detail="DATABASE_URL non configurato.",
        )

    return psycopg2.connect(database_url)


def json_safe(value):
    if isinstance(value, Decimal):
        return float(value)

    return value


def get_catalog_view(entity_id: Optional[str]):
    if entity_id:
        return ENTITY_CATALOG_VIEW

    return REGIONAL_CATALOG_VIEW


def validate_entity(conn, entity_id: Optional[str]):
    if not entity_id:
        return None

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                entity_id,
                entity_name,
                entity_type,
                entity_status
            FROM app_entities_v1
            WHERE entity_id = %s;
            """,
            (entity_id,),
        )

        row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Ente non trovato: {entity_id}",
        )

    if row["entity_status"] != "active":
        raise HTTPException(
            status_code=403,
            detail=f"Ente non attivo: {entity_id}",
        )

    return dict(row)


def build_where_clause(
    entity_id: Optional[str],
    reliability_class: Optional[str],
    spatial_validation_zone: Optional[str],
    priority_only: bool,
    min_area_ha: Optional[float],
    max_area_ha: Optional[float],
    bbox: Optional[str],
):
    where = []
    params = []

    if entity_id:
        where.append("entity_id = %s")
        params.append(entity_id)

    if reliability_class:
        if reliability_class not in ALLOWED_RELIABILITY_CLASSES:
            raise HTTPException(
                status_code=400,
                detail=f"Classe affidabilità non valida: {reliability_class}",
            )

        where.append("reliability_class = %s")
        params.append(reliability_class)

    if spatial_validation_zone:
        if spatial_validation_zone not in ALLOWED_ZONES:
            raise HTTPException(
                status_code=400,
                detail=f"Zona non valida: {spatial_validation_zone}",
            )

        where.append("spatial_validation_zone = %s")
        params.append(spatial_validation_zone)

    if priority_only:
        where.append("catalog_priority_candidate IS TRUE")

    if min_area_ha is not None:
        where.append("area_ha >= %s")
        params.append(min_area_ha)

    if max_area_ha is not None:
        where.append("area_ha <= %s")
        params.append(max_area_ha)

    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = [
                float(x.strip())
                for x in bbox.split(",")
            ]
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="bbox deve essere nel formato minLon,minLat,maxLon,maxLat",
            ) from exc

        if min_lon >= max_lon or min_lat >= max_lat:
            raise HTTPException(
                status_code=400,
                detail="bbox non valida: min deve essere inferiore a max.",
            )

        where.append(
            """
            geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
            """
        )
        params.extend([min_lon, min_lat, max_lon, max_lat])

    if not where:
        return "", params

    return "WHERE " + " AND ".join(where), params


def fetch_count(conn, catalog_view, where_sql, params):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*)
            FROM {catalog_view}
            {where_sql};
            """,
            params,
        )

        return int(cur.fetchone()[0])


def fetch_rows(conn, catalog_view, where_sql, params, limit, offset, include_geometry, entity_scoped):
    geometry_sql = ""

    if include_geometry:
        geometry_sql = ", ST_AsGeoJSON(geom, 6)::text AS geometry_geojson"

    entity_sql = ""

    if entity_scoped:
        entity_sql = """
                entity_id,
                entity_name,
                entity_type,
                entity_status,
                territory_id,
                territory_name,
                territory_scope_version,
                territory_status,
        """

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT
                {entity_sql}

                area_id,
                region_code,
                region_label,
                technical_subtype_id,
                technical_subtype_label,
                area_type,
                area_type_label,

                spatial_validation_zone,
                candidate_origin,

                area_ha,
                area_bin_raw,
                n_points,
                n_parts,
                n_points_bin,
                n_parts_bin,

                current_high_confidence_v2,
                identity_reference_match,
                strict_reference_match,
                large_polygon_flag,
                small_candidate_flag,
                complex_boundary_flag,

                reliability_score,
                reliability_class,
                reliability_label,
                reliability_rank,
                catalog_priority_candidate,
                catalog_status_label,

                reliability_model_version,
                reliability_model_status,
                catalog_version,
                catalog_status,

                centroid_lon,
                centroid_lat,
                bbox_min_lon,
                bbox_min_lat,
                bbox_max_lon,
                bbox_max_lat

                {geometry_sql}

            FROM {catalog_view}
            {where_sql}
            ORDER BY reliability_rank DESC, reliability_score DESC, area_id
            LIMIT %s
            OFFSET %s;
            """,
            [*params, limit, offset],
        )

        return [dict(row) for row in cur.fetchall()]


def to_geojson(rows):
    features = []

    for row in rows:
        geometry_text = row.pop("geometry_geojson", None)

        if geometry_text:
            geometry = json.loads(geometry_text)
        else:
            geometry = None

        properties = {
            key: json_safe(value)
            for key, value in row.items()
        }

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": properties,
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


# ================================================================
# 1. /metadata
# ================================================================

@router.get("/metadata")
def areas_metadata(
    entity_id: Optional[str] = Query(
        default=None,
        description="ID ente per leggere metadata del catalogo scoped.",
    ),
):
    catalog_view = get_catalog_view(entity_id)

    where_sql, params = build_where_clause(
        entity_id=entity_id,
        reliability_class=None,
        spatial_validation_zone=None,
        priority_only=False,
        min_area_ha=None,
        max_area_ha=None,
        bbox=None,
    )

    with get_connection() as conn:
        entity = validate_entity(conn, entity_id)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS n_total,
                    COUNT(*) FILTER (
                        WHERE catalog_priority_candidate IS TRUE
                    ) AS n_priority_candidates,
                    COUNT(*) FILTER (
                        WHERE reliability_class = 'very_high'
                    ) AS n_very_high,
                    COUNT(*) FILTER (
                        WHERE reliability_class = 'high'
                    ) AS n_high,
                    COUNT(*) FILTER (
                        WHERE reliability_class = 'compatible'
                    ) AS n_compatible,
                    COUNT(*) FILTER (
                        WHERE reliability_class = 'low'
                    ) AS n_low,
                    MIN(reliability_score) AS min_reliability_score,
                    MAX(reliability_score) AS max_reliability_score,
                    AVG(reliability_score) AS mean_reliability_score
                FROM {catalog_view}
                {where_sql};
                """,
                params,
            )

            catalog_counts = dict(cur.fetchone())

            cur.execute(
                """
                SELECT *
                FROM regional_reliability_model_runs
                WHERE model_version = 'regional_reliability_score_exp_v3';
                """
            )

            model = cur.fetchone()

            if not model:
                raise HTTPException(
                    status_code=500,
                    detail="Metadata modello v3 non trovati nel registry.",
                )

            cur.execute(
                """
                SELECT *
                FROM regional_reliability_model_thresholds
                WHERE model_version = 'regional_reliability_score_exp_v3';
                """
            )

            thresholds = [dict(row) for row in cur.fetchall()]

            entity_territories = []

            if entity_id:
                cur.execute(
                    """
                    SELECT
                        territory_id,
                        territory_name,
                        territory_scope_version,
                        territory_status,
                        source_description
                    FROM app_entity_territories_v1
                    WHERE entity_id = %s
                    ORDER BY territory_id;
                    """,
                    (entity_id,),
                )

                entity_territories = [dict(row) for row in cur.fetchall()]

    return {
        "catalog": {
            "catalog_view": catalog_view,
            "catalog_version": "area_catalog_v1_diagnostic",
            "catalog_status": "diagnostic_not_final",
            "scope": "entity" if entity_id else "regional",
            "entity": entity,
            "entity_territories": entity_territories,
            "counts": {
                key: json_safe(value)
                for key, value in catalog_counts.items()
            },
        },
        "model": {
            key: json_safe(value)
            for key, value in dict(model).items()
        },
        "thresholds": [
            {
                key: json_safe(value)
                for key, value in row.items()
            }
            for row in thresholds
        ],
        "data_policy": {
            "source_attribution": "Regione Calabria - Repertorio Cartografico regionale, dataset derivati e rielaborati per finalit? diagnostiche.",
            "license_note": "Usare con attribuzione della fonte e senza implicare approvazione ufficiale del licenziante.",
            "production_note": "Per uso PA/consorzi sostituire il territorio demo con confini amministrativi o consortili ufficiali versionati.",
        },
    }


# ================================================================
# 2. /export
# ================================================================

@router.get("/export")
def export_areas(
    entity_id: Optional[str] = Query(
        default=None,
        description="ID ente per esportare solo il territorio di competenza.",
    ),
    reliability_class: Optional[str] = Query(
        default=None,
        description="Filtro classe: low, compatible, high, very_high.",
    ),
    spatial_validation_zone: Optional[str] = Query(
        default=None,
        description="Filtro zona: north_calabria, central_calabria, south_calabria.",
    ),
    priority_only: bool = Query(
        default=True,
        description="Se true esporta solo high e very_high diagnostiche.",
    ),
    min_area_ha: Optional[float] = Query(
        default=None,
        ge=0,
        description="Area minima in ettari.",
    ),
    max_area_ha: Optional[float] = Query(
        default=None,
        ge=0,
        description="Area massima in ettari.",
    ),
    bbox: Optional[str] = Query(
        default=None,
        description="Bounding box WGS84: minLon,minLat,maxLon,maxLat.",
    ),
    output_format: str = Query(
        default="geojson",
        pattern="^geojson$",
        description="Formato export. Per ora supportato: geojson.",
    ),
    limit: int = Query(
        default=5000,
        ge=1,
        le=50000,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
):
    if output_format != "geojson":
        raise HTTPException(
            status_code=400,
            detail="Formato export non supportato. Usa output_format=geojson.",
        )

    if (
        min_area_ha is not None
        and max_area_ha is not None
        and min_area_ha > max_area_ha
    ):
        raise HTTPException(
            status_code=400,
            detail="min_area_ha non pu? essere maggiore di max_area_ha.",
        )

    catalog_view = get_catalog_view(entity_id)
    entity_scoped = entity_id is not None

    where_sql, params = build_where_clause(
        entity_id=entity_id,
        reliability_class=reliability_class,
        spatial_validation_zone=spatial_validation_zone,
        priority_only=priority_only,
        min_area_ha=min_area_ha,
        max_area_ha=max_area_ha,
        bbox=bbox,
    )

    with get_connection() as conn:
        entity = validate_entity(conn, entity_id)

        total = fetch_count(
            conn=conn,
            catalog_view=catalog_view,
            where_sql=where_sql,
            params=params,
        )

        rows = fetch_rows(
            conn=conn,
            catalog_view=catalog_view,
            where_sql=where_sql,
            params=params,
            limit=limit,
            offset=offset,
            include_geometry=True,
            entity_scoped=entity_scoped,
        )

    geojson = to_geojson(rows)

    geojson["metadata"] = {
        "catalog_view": catalog_view,
        "catalog_status": "diagnostic_not_final",
        "entity": entity,
        "export_format": "geojson",
        "total_matching": total,
        "exported_features": len(geojson["features"]),
        "limit": limit,
        "offset": offset,
        "priority_only": priority_only,
        "reliability_class": reliability_class,
        "spatial_validation_zone": spatial_validation_zone,
    }

    return geojson


# ================================================================
# 3. /summary
# ================================================================

@router.get("/summary")
def area_summary(
    entity_id: Optional[str] = Query(
        default=None,
        description="ID ente per filtrare il riepilogo sul territorio di competenza.",
    ),
):
    catalog_view = get_catalog_view(entity_id)

    where_sql, params = build_where_clause(
        entity_id=entity_id,
        reliability_class=None,
        spatial_validation_zone=None,
        priority_only=False,
        min_area_ha=None,
        max_area_ha=None,
        bbox=None,
    )

    with get_connection() as conn:
        entity = validate_entity(conn, entity_id)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    reliability_class,
                    COUNT(*) AS n,
                    ROUND(AVG(reliability_score)::numeric, 4) AS mean_score,
                    ROUND(MIN(reliability_score)::numeric, 4) AS min_score,
                    ROUND(MAX(reliability_score)::numeric, 4) AS max_score
                FROM {catalog_view}
                {where_sql}
                GROUP BY reliability_class
                ORDER BY
                    CASE reliability_class
                        WHEN 'low' THEN 1
                        WHEN 'compatible' THEN 2
                        WHEN 'high' THEN 3
                        WHEN 'very_high' THEN 4
                        ELSE 9
                    END;
                """,
                params,
            )

            by_class = [dict(row) for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    spatial_validation_zone,
                    reliability_class,
                    COUNT(*) AS n
                FROM {catalog_view}
                {where_sql}
                GROUP BY spatial_validation_zone, reliability_class
                ORDER BY spatial_validation_zone, reliability_class;
                """,
                params,
            )

            by_zone = [dict(row) for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS n_total,
                    COUNT(*) FILTER (
                        WHERE catalog_priority_candidate IS TRUE
                    ) AS n_priority_candidates
                FROM {catalog_view}
                {where_sql};
                """,
                params,
            )

            totals = dict(cur.fetchone())

    return {
        "catalog_view": catalog_view,
        "catalog_status": "diagnostic_not_final",
        "entity": entity,
        "totals": {
            key: json_safe(value)
            for key, value in totals.items()
        },
        "by_class": [
            {
                key: json_safe(value)
                for key, value in row.items()
            }
            for row in by_class
        ],
        "by_zone": [
            {
                key: json_safe(value)
                for key, value in row.items()
            }
            for row in by_zone
        ],
    }


# ================================================================
# 4. / (lista aree)
# ================================================================

@router.get("")
def list_areas(
    entity_id: Optional[str] = Query(
        default=None,
        description="ID ente per filtrare il catalogo sul territorio di competenza.",
    ),
    reliability_class: Optional[str] = Query(
        default=None,
        description="Filtro classe: low, compatible, high, very_high.",
    ),
    spatial_validation_zone: Optional[str] = Query(
        default=None,
        description="Filtro zona: north_calabria, central_calabria, south_calabria.",
    ),
    priority_only: bool = Query(
        default=False,
        description="Se true restituisce solo high e very_high diagnostiche.",
    ),
    min_area_ha: Optional[float] = Query(
        default=None,
        ge=0,
        description="Area minima in ettari.",
    ),
    max_area_ha: Optional[float] = Query(
        default=None,
        ge=0,
        description="Area massima in ettari.",
    ),
    bbox: Optional[str] = Query(
        default=None,
        description="Bounding box WGS84: minLon,minLat,maxLon,maxLat.",
    ),
    include_geometry: bool = Query(
        default=False,
        description="Include geometria GeoJSON nella risposta.",
    ),
    output_format: str = Query(
        default="json",
        pattern="^(json|geojson)$",
        description="Formato risposta: json o geojson.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
):
    if output_format not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato non valido: {output_format}",
        )

    if output_format == "geojson":
        include_geometry = True

    if (
        min_area_ha is not None
        and max_area_ha is not None
        and min_area_ha > max_area_ha
    ):
        raise HTTPException(
            status_code=400,
            detail="min_area_ha non può essere maggiore di max_area_ha.",
        )

    catalog_view = get_catalog_view(entity_id)
    entity_scoped = entity_id is not None

    where_sql, params = build_where_clause(
        entity_id=entity_id,
        reliability_class=reliability_class,
        spatial_validation_zone=spatial_validation_zone,
        priority_only=priority_only,
        min_area_ha=min_area_ha,
        max_area_ha=max_area_ha,
        bbox=bbox,
    )

    with get_connection() as conn:
        entity = validate_entity(conn, entity_id)

        total = fetch_count(
            conn=conn,
            catalog_view=catalog_view,
            where_sql=where_sql,
            params=params,
        )

        rows = fetch_rows(
            conn=conn,
            catalog_view=catalog_view,
            where_sql=where_sql,
            params=params,
            limit=limit,
            offset=offset,
            include_geometry=include_geometry,
            entity_scoped=entity_scoped,
        )

    if output_format == "geojson":
        geojson = to_geojson(rows)
        geojson["metadata"] = {
            "catalog_view": catalog_view,
            "catalog_status": "diagnostic_not_final",
            "entity": entity,
            "total_matching": total,
            "limit": limit,
            "offset": offset,
        }
        return geojson

    return {
        "catalog_view": catalog_view,
        "catalog_status": "diagnostic_not_final",
        "entity": entity,
        "total_matching": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                key: json_safe(value)
                for key, value in row.items()
                if key != "geometry_geojson"
            }
            for row in rows
        ],
    }


# ================================================================
# 5. /{area_id} (dettaglio area - ULTIMA)
# ================================================================

@router.get("/{area_id}")
def get_area_detail(
    area_id: str,
    entity_id: Optional[str] = Query(
        default=None,
        description="ID ente per verificare la disponibilità dell'area nel territorio di competenza.",
    ),
    include_geometry: bool = Query(
        default=True,
        description="Include la geometria GeoJSON dell'area.",
    ),
):
    """
    Recupera il dettaglio di un'area specifica dal catalogo.
    
    Args:
        area_id: ID univoco dell'area nel catalogo
        entity_id: (Opzionale) ID ente per filtrare sul territorio di competenza
        include_geometry: Se True, include la geometria GeoJSON
    
    Returns:
        Dettaglio dell'area con tutte le proprietà catalogate
    """
    catalog_view = get_catalog_view(entity_id)
    entity_scoped = entity_id is not None

    where = ["area_id = %s"]
    params = [area_id]

    if entity_id:
        where.append("entity_id = %s")
        params.append(entity_id)

    where_sql = "WHERE " + " AND ".join(where)

    with get_connection() as conn:
        entity = validate_entity(conn, entity_id)

        rows = fetch_rows(
            conn=conn,
            catalog_view=catalog_view,
            where_sql=where_sql,
            params=params,
            limit=1,
            offset=0,
            include_geometry=include_geometry,
            entity_scoped=entity_scoped,
        )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Area non trovata o non disponibile per il territorio richiesto: {area_id}",
        )

    row = rows[0]
    geometry_text = row.pop("geometry_geojson", None)

    geometry = json.loads(geometry_text) if geometry_text else None

    return {
        "catalog_view": catalog_view,
        "catalog_status": "diagnostic_not_final",
        "entity": entity,
        "area": {
            key: json_safe(value)
            for key, value in row.items()
        },
        "geometry": geometry,
    }