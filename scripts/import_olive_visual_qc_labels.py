import json
import os

import psycopg2
from dotenv import load_dotenv


load_dotenv()


VALID_LABELS = {"olive_like", "uncertain", "not_olive_like"}


def first_available(props, fields, default=None):
    for field in fields:
        if field in props and props.get(field) not in (None, ""):
            return props.get(field)
    return default


def normalize_label(value):
    if value is None:
        return ""

    value = str(value).strip().lower()
    value = value.replace("-", "_").replace(" ", "_")

    mapping = {
        "olive": "olive_like",
        "olivo": "olive_like",
        "oliveto": "olive_like",
        "olive_like": "olive_like",

        "uncertain": "uncertain",
        "incerto": "uncertain",
        "dubbio": "uncertain",

        "not_olive": "not_olive_like",
        "not_olive_like": "not_olive_like",
        "non_olivo": "not_olive_like",
        "non_oliveto": "not_olive_like",
        "no": "not_olive_like",
    }

    return mapping.get(value, value)


def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL non configurato in .env")

    input_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "olive_visual_qc_sample_v1_reviewed.geojson",
    )

    if not os.path.exists(input_path):
        raise RuntimeError(f"File non trovato: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)

    features = geojson.get("features", [])

    records = []
    invalid_count = 0
    empty_count = 0
    detected_label_fields = set()

    for feature in features:
        props = feature.get("properties", {})

        source_geometry_id = first_available(
            props,
            ["source_geometry_id", "source_geo"],
        )

        sampling_version = first_available(
            props,
            ["sampling_version", "sampling_v"],
            "olive_visual_sample_v1",
        )

        visual_qc_version = first_available(
            props,
            ["visual_qc_version", "visual_qc_"],
            "olive_visual_qc_v1",
        )

        raw_label = first_available(
            props,
            ["visual_label", "visual_lab", "label", "qc_label"],
            "",
        )

        for field in ["visual_label", "visual_lab", "label", "qc_label"]:
            if field in props:
                detected_label_fields.add(field)

        visual_label = normalize_label(raw_label)
        notes = first_available(props, ["notes"], None)

        if not source_geometry_id:
            invalid_count += 1
            continue

        if not visual_label:
            empty_count += 1
            continue

        if visual_label not in VALID_LABELS:
            invalid_count += 1
            continue

        records.append(
            (
                source_geometry_id,
                sampling_version,
                visual_qc_version,
                visual_label,
                notes,
            )
        )

    print(f"Feature totali: {len(features)}")
    print(f"Etichette valide trovate: {len(records)}")
    print(f"Etichette vuote: {empty_count}")
    print(f"Record invalidi: {invalid_count}")
    print(f"Campi label rilevati: {sorted(detected_label_fields)}")

    if not records:
        raise RuntimeError(
            "Nessuna etichetta valida trovata. "
            "Usa olive_like / uncertain / not_olive_like."
        )

    upsert_sql = """
    INSERT INTO landcover_subtype_visual_qc (
        source_geometry_id,
        sampling_version,
        visual_qc_version,
        visual_label,
        reviewer,
        notes
    )
    VALUES (%s, %s, %s, %s, 'manual_review', %s)
    ON CONFLICT (source_geometry_id, visual_qc_version)
    DO UPDATE SET
        sampling_version = EXCLUDED.sampling_version,
        visual_label = EXCLUDED.visual_label,
        reviewer = EXCLUDED.reviewer,
        notes = EXCLUDED.notes,
        reviewed_at = now();
    """

    summary_sql = """
    SELECT
        visual_label,
        COUNT(*) AS n
    FROM landcover_subtype_visual_qc
    WHERE visual_qc_version = 'olive_visual_qc_v1'
    GROUP BY visual_label
    ORDER BY visual_label;
    """

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(upsert_sql, records)
            conn.commit()

            cur.execute(summary_sql)
            rows = cur.fetchall()

    print("")
    print(f"Etichette importate: {len(records)}")
    print("")
    print("Visual QC summary")
    print("-----------------")
    for row in rows:
        print(f"{row[0]} | n={row[1]}")


if __name__ == "__main__":
    main()