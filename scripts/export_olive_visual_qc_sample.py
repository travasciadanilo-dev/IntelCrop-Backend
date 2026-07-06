import json
import os

import psycopg2
from dotenv import load_dotenv


load_dotenv()


SAMPLING_VERSION = "olive_visual_sample_v1"
QC_VERSION = "olive_pure_geom_qc_v2"
SAMPLES_PER_CLASS = 40


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "olive_visual_qc_sample_v1.geojson",
    )

    reset_sql = """
    DELETE FROM landcover_subtype_visual_qc_sample
    WHERE sampling_version = %(sampling_version)s;
    """

    insert_sql = """
    WITH candidates AS (
        SELECT
            id AS source_geometry_id,
            subtype_id,
            source_layer_version,
            qc_version,
            area_ha,
            compactness,
            n_points,
            n_parts,
            geom,
            CASE
                WHEN area_ha >= 0.50 AND area_ha < 1.00 THEN 'area_0_5_1_ha'
                WHEN area_ha >= 1.00 AND area_ha < 2.00 THEN 'area_1_2_ha'
                WHEN area_ha >= 2.00 AND area_ha < 5.00 THEN 'area_2_5_ha'
                WHEN area_ha >= 5.00 AND area_ha < 10.00 THEN 'area_5_10_ha'
                ELSE 'area_10_15_ha'
            END AS area_class
        FROM landcover_olive_pure_high_confidence_v2
        WHERE qc_version = %(qc_version)s
          AND area_ha >= 0.50
          AND area_ha <= 15.00
    ),
    ranked AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY area_class
                ORDER BY md5(source_geometry_id::text || %(sampling_version)s)
            ) AS sample_rank
        FROM candidates
    ),
    selected AS (
        SELECT *
        FROM ranked
        WHERE sample_rank <= %(samples_per_class)s
    )
    INSERT INTO landcover_subtype_visual_qc_sample (
        source_geometry_id,
        sampling_version,
        qc_version,
        subtype_id,
        source_layer_version,
        area_class,
        area_ha,
        compactness,
        n_points,
        n_parts,
        sample_rank
    )
    SELECT
        source_geometry_id,
        %(sampling_version)s,
        qc_version,
        subtype_id,
        source_layer_version,
        area_class,
        area_ha,
        compactness,
        n_points,
        n_parts,
        sample_rank
    FROM selected
    ON CONFLICT (source_geometry_id, sampling_version)
    DO NOTHING;
    """

    export_sql = """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'name', %(sampling_version)s,
        'features', json_agg(
            json_build_object(
                'type', 'Feature',
                'properties', json_build_object(
                    'source_geometry_id', s.source_geometry_id,
                    'sampling_version', s.sampling_version,
                    'visual_qc_version', 'olive_visual_qc_v1',
                    'subtype_id', s.subtype_id,
                    'source_layer_version', s.source_layer_version,
                    'qc_version', s.qc_version,
                    'area_class', s.area_class,
                    'area_ha', ROUND(s.area_ha::numeric, 4),
                    'compactness', ROUND(s.compactness::numeric, 4),
                    'n_points', s.n_points,
                    'n_parts', s.n_parts,
                    'sample_rank', s.sample_rank,
                    'visual_label', '',
                    'notes', ''
                ),
                'geometry', ST_AsGeoJSON(g.geom, 6)::json
            )
            ORDER BY s.area_class, s.sample_rank
        )
    )::text
    FROM landcover_subtype_visual_qc_sample s
    JOIN landcover_subtype_geometries g
      ON g.id = s.source_geometry_id
    WHERE s.sampling_version = %(sampling_version)s;
    """

    summary_sql = """
    SELECT
        area_class,
        COUNT(*) AS n_samples,
        ROUND(MIN(area_ha)::numeric, 4) AS min_area_ha,
        ROUND(MAX(area_ha)::numeric, 4) AS max_area_ha,
        ROUND(AVG(area_ha)::numeric, 4) AS avg_area_ha
    FROM landcover_subtype_visual_qc_sample
    WHERE sampling_version = %(sampling_version)s
    GROUP BY area_class
    ORDER BY area_class;
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(reset_sql, {"sampling_version": SAMPLING_VERSION})
            cur.execute(
                insert_sql,
                {
                    "sampling_version": SAMPLING_VERSION,
                    "qc_version": QC_VERSION,
                    "samples_per_class": SAMPLES_PER_CLASS,
                },
            )
            conn.commit()

            cur.execute(export_sql, {"sampling_version": SAMPLING_VERSION})
            geojson_text = cur.fetchone()[0]

            cur.execute(summary_sql, {"sampling_version": SAMPLING_VERSION})
            rows = cur.fetchall()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(geojson_text)

    print(f"GeoJSON campione creato: {output_path}")
    print("")
    print("Sample summary")
    print("--------------")
    for row in rows:
        print(
            f"{row[0]} | n={row[1]} | "
            f"min={row[2]} | max={row[3]} | avg={row[4]}"
        )


if __name__ == "__main__":
    main()