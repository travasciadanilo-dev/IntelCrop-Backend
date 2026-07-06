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
                FROM landcover_subtype_geometries
                WHERE subtype_id = 'olive_pure'
                  AND source_layer_version = 'cut_calabria_v1'
                LIMIT 1;
                """
            )
            row = cur.fetchone()

    if not row:
        raise RuntimeError("Nessuna geometria olive_pure trovata nel DB.")

    geometry = json.loads(row[0])

    result = match_field_to_subtype(geometry)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()