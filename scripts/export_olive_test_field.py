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
        "test_olive_pure_baseline_seed_v1_field.geojson",
    )

    query = """
    WITH src AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            qc_version,
            qc_class,
            visual_qc_version,
            visual_label,
            area_ha,
            compactness,
            geom
        FROM landcover_olive_pure_baseline_seed_v1
        WHERE area_ha BETWEEN 0.8 AND 3.0
        ORDER BY compactness DESC, area_ha ASC
        LIMIT 1
    ),
    point_inside AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            qc_version,
            qc_class,
            visual_qc_version,
            visual_label,
            area_ha AS source_area_ha,
            compactness,
            geom AS source_geom,
            ST_Transform(ST_PointOnSurface(geom), 3857) AS p3857
        FROM src
    ),
    square AS (
        SELECT
            id,
            subtype_id,
            source_layer_version,
            qc_version,
            qc_class,
            visual_qc_version,
            visual_label,
            source_area_ha,
            compactness,
            source_geom,
            ST_Transform(
                ST_MakeEnvelope(
                    ST_X(p3857) - 40,
                    ST_Y(p3857) - 40,
                    ST_X(p3857) + 40,
                    ST_Y(p3857) + 40,
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
            qc_version,
            qc_class,
            visual_qc_version,
            visual_label,
            source_area_ha,
            compactness,
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
        qc_version,
        qc_class,
        visual_qc_version,
        visual_label,
        source_area_ha,
        compactness,
        ST_Area(geom::geography) / 10000.0 AS test_area_ha,
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
        raise RuntimeError(
            "Nessuna geometria olive_like trovata in "
            "landcover_olive_pure_baseline_seed_v1."
        )

    (
        source_feature_id,
        subtype_id,
        source_layer_version,
        qc_version,
        qc_class,
        visual_qc_version,
        visual_label,
        source_area_ha,
        compactness,
        test_area_ha,
        n_points,
        geojson_text,
    ) = row

    geometry = json.loads(geojson_text)

    feature_collection = {
        "type": "FeatureCollection",
        "name": "test_olive_pure_baseline_seed_v1_field",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": "Test oliveto puro baseline seed v1",
                    "source": "landcover_olive_pure_baseline_seed_v1",
                    "source_feature_id": source_feature_id,
                    "subtype_expected": subtype_id,
                    "source_layer_version": source_layer_version,
                    "qc_version": qc_version,
                    "qc_class": qc_class,
                    "visual_qc_version": visual_qc_version,
                    "visual_label": visual_label,
                    "source_area_ha": round(float(source_area_ha), 4),
                    "compactness": round(float(compactness), 4),
                    "test_area_ha": round(float(test_area_ha), 4),
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
    print(f"Source: landcover_olive_pure_baseline_seed_v1")
    print(f"Subtype atteso: {subtype_id}")
    print(f"Layer version: {source_layer_version}")
    print(f"QC version: {qc_version}")
    print(f"QC class: {qc_class}")
    print(f"Visual QC version: {visual_qc_version}")
    print(f"Visual label: {visual_label}")
    print(f"Area sorgente: {float(source_area_ha):.4f} ha")
    print(f"Area test: {float(test_area_ha):.4f} ha")
    print(f"Punti geometria: {n_points}")


if __name__ == "__main__":
    main()