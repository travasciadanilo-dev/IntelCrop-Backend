import json
import os
import sys

import psycopg2
from dotenv import load_dotenv


sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from landcover_matching import match_field_to_subtype


load_dotenv()


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ST_AsGeoJSON(geom)::text,
                    id::text,
                    area_ha,
                    spectral_flag
                FROM landcover_olive_pure_baseline_v1
                WHERE subtype_id = 'olive_pure'
                  AND source_layer_version = 'cut_calabria_v1'
                  AND qc_version = 'olive_pure_geom_qc_v2'
                  AND qc_class = 'high_confidence'
                  AND visual_label = 'olive_like'
                  AND artificial_flag IN ('none', 'low')
                  AND spectral_flag IN ('strong', 'moderate')
                ORDER BY
                    CASE spectral_flag
                        WHEN 'strong' THEN 1
                        WHEN 'moderate' THEN 2
                        ELSE 3
                    END,
                    area_ha ASC
                LIMIT 1;
                """
            )
            row = cur.fetchone()

    if not row:
        raise RuntimeError("Nessuna geometria trovata in landcover_olive_pure_baseline_v1.")

    geometry = json.loads(row[0])
    source_id = row[1]
    area_ha = float(row[2])
    spectral_flag = row[3]

    result = match_field_to_subtype(geometry)

    print("")
    print("Baseline v1 source")
    print("------------------")
    print(f"source_geometry_id: {source_id}")
    print(f"area_ha: {area_ha:.4f}")
    print(f"spectral_flag: {spectral_flag}")

    print("")
    print("Matching result")
    print("---------------")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    assert result["subtype"] == "olive_pure"
    assert result["subtype_confidence"] == "high"

    assert result["landcover_qc_version"] == "olive_pure_geom_qc_v2"
    assert result["landcover_qc_class"] == "high_confidence"

    assert result["baseline_version"] == "olive_baseline_v1"
    assert result["baseline_layer"] == "landcover_olive_pure_baseline_v1"
    assert result["baseline_v1_match"] is True
    assert result["baseline_v1_coverage_percent"] >= 75.0

    assert result["baseline_v1"] is not None
    assert result["baseline_v1"]["visual_label"] == "olive_like"
    assert result["baseline_v1"]["urban_qc_version"] == "olive_urban_qc_v1"
    assert result["baseline_v1"]["artificial_flag"] in {"none", "low"}
    assert result["baseline_v1"]["spectral_qc_version"] == "olive_spectral_qc_v1"
    assert result["baseline_v1"]["spectral_flag"] in {"strong", "moderate"}

    assert result["usable_for_baseline"] is True

    print("")
    print("OK: landcover matching baseline_v1 validato.")


if __name__ == "__main__":
    main()