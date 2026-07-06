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
                SELECT ST_AsGeoJSON(geom)::text
                FROM landcover_olive_pure_high_confidence_v2
                WHERE subtype_id = 'olive_pure'
                  AND source_layer_version = 'cut_calabria_v1'
                  AND qc_version = 'olive_pure_geom_qc_v2'
                  AND qc_class = 'high_confidence'
                ORDER BY area_ha ASC
                LIMIT 1;
                """
            )
            row = cur.fetchone()

    if not row:
        raise RuntimeError("Nessuna geometria olive_pure high-confidence v2 trovata.")

    geometry = json.loads(row[0])
    result = match_field_to_subtype(geometry)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    assert result["subtype"] == "olive_pure"
    assert result["subtype_confidence"] == "high"
    assert result["landcover_qc_version"] == "olive_pure_geom_qc_v2"
    assert result["landcover_qc_class"] == "high_confidence"
    assert result["usable_for_baseline"] is True


if __name__ == "__main__":
    main()