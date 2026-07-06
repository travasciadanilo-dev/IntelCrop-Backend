import json
import os

import psycopg2
from dotenv import load_dotenv


load_dotenv()


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "test_olive_pure_field.geojson",
    )

    query = """
    WITH src AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            geom
        FROM landcover_subtype_geometries
        WHERE subtype_id = 'olive_pure'
          AND source_layer_version = 'cut_calabria_v1'
        LIMIT 1
    ),
    point_inside AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            geom AS source_geom,
            ST_Transform(ST_PointOnSurface(geom), 3857) AS p3857
        FROM src
    ),
    square AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            source_geom,
            ST_Transform(
                ST_MakeEnvelope(
                    ST_X(p3857) - 45,
                    ST_Y(p3857) - 45,
                    ST_X(p3857) + 45,
                    ST_Y(p3857) + 45,
                    3857
                ),
                4326
            ) AS square_geom
        FROM point_inside
    ),
    clipped AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            ST_Multi(
                ST_CollectionExtract(
                    ST_MakeValid(
                        ST_Intersection(source_geom, square_geom)
                    ),
                    3
                )
            )::geometry(MultiPolygon, 4326) AS geom
        FROM square
    )
    SELECT
        id,
        subtype_id,
        source_layer_version,
        ST_Area(geom::geography) / 10000.0 AS area_ha,
        ST_NPoints(geom) AS n_points,
        ST_AsGeoJSON(geom, 6)::text AS geojson
    FROM clipped
    WHERE geom IS NOT NULL
      AND NOT ST_IsEmpty(geom);
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()

    if not row:
        raise RuntimeError("Nessuna geometria olive_pure valida trovata.")

    (
        source_feature_id,
        subtype_id,
        source_layer_version,
        area_ha,
        n_points,
        geojson_text,
    ) = row

    geometry = json.loads(geojson_text)

    feature_collection = {
        "type": "FeatureCollection",
        "name": "test_olive_pure_field",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": "Test oliveto puro Calabria",
                    "source": "PostGIS landcover_subtype_geometries",
                    "source_feature_id": source_feature_id,
                    "subtype_expected": subtype_id,
                    "source_layer_version": source_layer_version,
                    "area_ha": round(float(area_ha), 4),
                    "n_points": int(n_points),
                },
                "geometry": geometry,
            }
        ],
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(feature_collection, f, ensure_ascii=False, indent=2)

    print(f"GeoJSON creato: {output_path}")
    print(f"Subtype atteso: {subtype_id}")
    print(f"Layer version: {source_layer_version}")
    print(f"Area: {float(area_ha):.4f} ha")
    print(f"Punti geometria: {n_points}")


if __name__ == "__main__":
    main()