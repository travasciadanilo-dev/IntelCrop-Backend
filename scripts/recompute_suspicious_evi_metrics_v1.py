from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import ee
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from build_olive_spectral_qc import (
    init_ee,
    reduce_spectral_metrics,
)


load_dotenv()

SPECTRAL_QC_VERSION = "olive_spectral_qc_v1"
SOURCE_VIEW_LABEL = "regional_evi_mask_recompute_v1"

SUSPICIOUS_THRESHOLD = float(
    os.getenv(
        "EVI_RECOMPUTE_THRESHOLD",
        "0.5",
    )
)

SUB_BATCH_SIZE = int(
    os.getenv(
        "EVI_RECOMPUTE_SUB_BATCH_SIZE",
        "5",
    )
)

EE_TIMEOUT_SECONDS = int(
    os.getenv(
        "EVI_RECOMPUTE_EE_TIMEOUT_SECONDS",
        "180",
    )
)

EE_MAX_RETRIES = int(
    os.getenv(
        "EVI_RECOMPUTE_EE_MAX_RETRIES",
        "1",
    )
)

SLEEP_BETWEEN_BATCHES_SECONDS = float(
    os.getenv(
        "EVI_RECOMPUTE_SLEEP_SECONDS",
        "1",
    )
)


LOAD_SQL = """
SELECT
    p.area_id,
    p.source_geometry_id::uuid,
    ST_AsGeoJSON(g.geom, 6)::text AS geometry_geojson,
    s.id AS spectral_qc_row_id,
    s.evi_stddev AS previous_evi_stddev
FROM olive_candidate_pool_v2 p
JOIN landcover_subtype_geometries g
  ON g.id = p.source_geometry_id::uuid
JOIN landcover_subtype_spectral_qc s
  ON s.source_geometry_id = p.source_geometry_id::uuid
 AND s.spectral_qc_version = %s
WHERE s.evi_stddev > %s
ORDER BY s.evi_stddev DESC, p.area_id;
"""


UPSERT_SQL = """
INSERT INTO landcover_subtype_spectral_qc (
    source_geometry_id,
    spectral_qc_version,
    source_view,
    n_observations,

    ndvi_median,
    ndvi_p25,
    ndvi_p75,
    ndvi_stddev,

    evi_median,
    evi_p25,
    evi_p75,
    evi_stddev,

    ndmi_median,
    ndmi_p25,
    ndmi_p75,
    ndmi_stddev,

    bsi_median,
    bsi_p25,
    bsi_p75,
    bsi_stddev,

    spectral_flag,
    usable_for_baseline_spectral,
    exclusion_reason
)
VALUES %s
ON CONFLICT (
    source_geometry_id,
    spectral_qc_version
)
DO UPDATE SET
    source_view =
        EXCLUDED.source_view,

    n_observations =
        EXCLUDED.n_observations,

    ndvi_median =
        EXCLUDED.ndvi_median,
    ndvi_p25 =
        EXCLUDED.ndvi_p25,
    ndvi_p75 =
        EXCLUDED.ndvi_p75,
    ndvi_stddev =
        EXCLUDED.ndvi_stddev,

    evi_median =
        EXCLUDED.evi_median,
    evi_p25 =
        EXCLUDED.evi_p25,
    evi_p75 =
        EXCLUDED.evi_p75,
    evi_stddev =
        EXCLUDED.evi_stddev,

    ndmi_median =
        EXCLUDED.ndmi_median,
    ndmi_p25 =
        EXCLUDED.ndmi_p25,
    ndmi_p75 =
        EXCLUDED.ndmi_p75,
    ndmi_stddev =
        EXCLUDED.ndmi_stddev,

    bsi_median =
        EXCLUDED.bsi_median,
    bsi_p25 =
        EXCLUDED.bsi_p25,
    bsi_p75 =
        EXCLUDED.bsi_p75,
    bsi_stddev =
        EXCLUDED.bsi_stddev,

    spectral_flag =
        EXCLUDED.spectral_flag,

    usable_for_baseline_spectral =
        EXCLUDED.usable_for_baseline_spectral,

    exclusion_reason =
        EXCLUDED.exclusion_reason,

    computed_at = now();
"""


def database_connection():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(
        host=(
            os.getenv("POSTGRES_HOST")
            or os.getenv("DB_HOST")
            or "localhost"
        ),
        port=int(
            os.getenv("POSTGRES_PORT")
            or os.getenv("DB_PORT")
            or "5432"
        ),
        dbname=(
            os.getenv("POSTGRES_DB")
            or os.getenv("DB_NAME")
            or "intellcrop"
        ),
        user=(
            os.getenv("POSTGRES_USER")
            or os.getenv("DB_USER")
            or "intellcrop"
        ),
        password=(
            os.getenv("POSTGRES_PASSWORD")
            or os.getenv("DB_PASSWORD")
        ),
    )


def chunks(
    values: list[Any],
    size: int,
) -> list[list[Any]]:
    return [
        values[index:index + size]
        for index in range(0, len(values), size)
    ]


def is_infrastructure_error(
    exception: Exception,
) -> bool:
    message = str(exception).lower()

    indicators = (
        "failed to resolve",
        "getaddrinfo failed",
        "name resolution",
        "temporary failure in name resolution",
        "connection reset",
        "connection aborted",
        "connection refused",
        "remote end closed connection",
        "timed out",
        "timeout",
        "deadline exceeded",
        "network is unreachable",
    )

    return any(
        indicator in message
        for indicator in indicators
    )


def create_backup(conn) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS
                evi_suspicious_recompute_backup_v1
            (
                backup_id bigserial PRIMARY KEY,
                recompute_version text NOT NULL,
                backed_up_at timestamptz NOT NULL
                    DEFAULT now(),

                spectral_qc_row_id uuid NOT NULL,
                source_geometry_id uuid NOT NULL,
                spectral_qc_version text NOT NULL,
                source_view text,

                n_observations integer,

                ndvi_median double precision,
                ndvi_p25 double precision,
                ndvi_p75 double precision,
                ndvi_stddev double precision,

                evi_median double precision,
                evi_p25 double precision,
                evi_p75 double precision,
                evi_stddev double precision,

                ndmi_median double precision,
                ndmi_p25 double precision,
                ndmi_p75 double precision,
                ndmi_stddev double precision,

                bsi_median double precision,
                bsi_p25 double precision,
                bsi_p75 double precision,
                bsi_stddev double precision,

                spectral_flag text,
                usable_for_baseline_spectral boolean,
                exclusion_reason text,
                computed_at timestamptz,

                UNIQUE (
                    recompute_version,
                    spectral_qc_row_id
                )
            );
            """
        )

        cursor.execute(
            """
            INSERT INTO
                evi_suspicious_recompute_backup_v1
            (
                recompute_version,
                spectral_qc_row_id,
                source_geometry_id,
                spectral_qc_version,
                source_view,

                n_observations,

                ndvi_median,
                ndvi_p25,
                ndvi_p75,
                ndvi_stddev,

                evi_median,
                evi_p25,
                evi_p75,
                evi_stddev,

                ndmi_median,
                ndmi_p25,
                ndmi_p75,
                ndmi_stddev,

                bsi_median,
                bsi_p25,
                bsi_p75,
                bsi_stddev,

                spectral_flag,
                usable_for_baseline_spectral,
                exclusion_reason,
                computed_at
            )
            SELECT
                %s,
                s.id,
                s.source_geometry_id,
                s.spectral_qc_version,
                s.source_view,

                s.n_observations,

                s.ndvi_median,
                s.ndvi_p25,
                s.ndvi_p75,
                s.ndvi_stddev,

                s.evi_median,
                s.evi_p25,
                s.evi_p75,
                s.evi_stddev,

                s.ndmi_median,
                s.ndmi_p25,
                s.ndmi_p75,
                s.ndmi_stddev,

                s.bsi_median,
                s.bsi_p25,
                s.bsi_p75,
                s.bsi_stddev,

                s.spectral_flag,
                s.usable_for_baseline_spectral,
                s.exclusion_reason,
                s.computed_at

            FROM landcover_subtype_spectral_qc s

            WHERE
                s.spectral_qc_version = %s
                AND s.evi_stddev > %s

            ON CONFLICT (
                recompute_version,
                spectral_qc_row_id
            )
            DO NOTHING;
            """,
            (
                SOURCE_VIEW_LABEL,
                SPECTRAL_QC_VERSION,
                SUSPICIOUS_THRESHOLD,
            ),
        )

        inserted_n = cursor.rowcount

    conn.commit()
    return inserted_n


def load_items(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            LOAD_SQL,
            (
                SPECTRAL_QC_VERSION,
                SUSPICIOUS_THRESHOLD,
            ),
        )

        rows = cursor.fetchall()

    return [
        {
            "area_id": str(row[0]),
            "source_geometry_id": str(row[1]),
            "geometry_geojson": row[2],
            "spectral_qc_row_id": str(row[3]),
            "previous_evi_stddev": float(row[4]),
        }
        for row in rows
    ]


def validate_records(
    requested_ids: list[str],
    records: list[dict[str, Any]],
) -> None:
    returned_ids = {
        str(record["source_geometry_id"])
        for record in records
    }

    missing_ids = sorted(
        set(requested_ids) - returned_ids
    )

    unexpected_ids = sorted(
        returned_ids - set(requested_ids)
    )

    if missing_ids:
        raise RuntimeError(
            "Earth Engine non ha restituito "
            f"{len(missing_ids)} geometrie."
        )

    if unexpected_ids:
        raise RuntimeError(
            "Earth Engine ha restituito geometrie "
            "non richieste."
        )

    for record in records:
        source_geometry_id = str(
            record["source_geometry_id"]
        )

        evi_values = [
            record.get("evi_median"),
            record.get("evi_p25"),
            record.get("evi_p75"),
            record.get("evi_stddev"),
        ]

        for value in evi_values:
            if value is None:
                continue

            numeric_value = float(value)

            if not (
                float("-inf")
                < numeric_value
                < float("inf")
            ):
                raise RuntimeError(
                    "Valore EVI non finito per "
                    f"{source_geometry_id}."
                )

        evi_stddev = record.get(
            "evi_stddev"
        )

        if (
            evi_stddev is not None
            and float(evi_stddev) > 1.0
        ):
            raise RuntimeError(
                "Ricalcolo ancora implausibile per "
                f"{source_geometry_id}: "
                f"evi_stddev={evi_stddev}"
            )


def upsert_records(
    conn,
    records: list[dict[str, Any]],
) -> None:
    values = [
        (
            record["source_geometry_id"],
            SPECTRAL_QC_VERSION,
            SOURCE_VIEW_LABEL,
            record["n_observations"],

            record["ndvi_median"],
            record["ndvi_p25"],
            record["ndvi_p75"],
            record["ndvi_stddev"],

            record["evi_median"],
            record["evi_p25"],
            record["evi_p75"],
            record["evi_stddev"],

            record["ndmi_median"],
            record["ndmi_p25"],
            record["ndmi_p75"],
            record["ndmi_stddev"],

            record["bsi_median"],
            record["bsi_p25"],
            record["bsi_p75"],
            record["bsi_stddev"],

            record["spectral_flag"],
            record[
                "usable_for_baseline_spectral"
            ],
            record["exclusion_reason"],
        )
        for record in records
    ]

    with conn.cursor() as cursor:
        execute_values(
            cursor,
            UPSERT_SQL,
            values,
            page_size=100,
        )

    conn.commit()


def export_audit_csv(conn) -> Path:
    output_directory = Path("outputs")
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        output_directory
        / "evi_recompute_audit_v1.csv"
    )

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                p.area_id,
                p.source_geometry_id,
                p.spatial_validation_zone,

                b.evi_stddev
                    AS previous_evi_stddev,

                s.evi_stddev
                    AS recomputed_evi_stddev,

                s.evi_median,
                s.evi_p25,
                s.evi_p75,

                s.n_observations,
                s.spectral_flag,
                s.usable_for_baseline_spectral,
                s.source_view,
                s.computed_at

            FROM
                evi_suspicious_recompute_backup_v1 b

            JOIN olive_candidate_pool_v2 p
              ON p.source_geometry_id::uuid =
                  b.source_geometry_id

            JOIN landcover_subtype_spectral_qc s
              ON s.id =
                  b.spectral_qc_row_id

            WHERE
                b.recompute_version = %s

            ORDER BY
                b.evi_stddev DESC,
                p.area_id;
            """,
            (SOURCE_VIEW_LABEL,),
        )

        columns = [
            description.name
            for description in cursor.description
        ]

        rows = cursor.fetchall()

    import csv

    with output_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(columns)
        writer.writerows(rows)

    return output_path


def print_summary(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS recomputed_n,

                COUNT(*) FILTER (
                    WHERE evi_stddev > 0.5
                ) AS remaining_above_0_5,

                COUNT(*) FILTER (
                    WHERE evi_stddev > 1.0
                ) AS remaining_above_1,

                ROUND(
                    MIN(evi_stddev)::numeric,
                    6
                ) AS minimum,

                ROUND(
                    AVG(evi_stddev)::numeric,
                    6
                ) AS mean_value,

                ROUND(
                    MAX(evi_stddev)::numeric,
                    6
                ) AS maximum

            FROM landcover_subtype_spectral_qc

            WHERE
                spectral_qc_version = %s
                AND source_view = %s;
            """,
            (
                SPECTRAL_QC_VERSION,
                SOURCE_VIEW_LABEL,
            ),
        )

        row = cursor.fetchone()

    print()
    print("Riepilogo ricalcolo EVI")
    print("-----------------------")
    print(f"ricalcolate:          {row[0]}")
    print(f"ancora sopra 0.5:    {row[1]}")
    print(f"ancora sopra 1.0:    {row[2]}")
    print(f"min evi_stddev:      {row[3]}")
    print(f"media evi_stddev:    {row[4]}")
    print(f"max evi_stddev:      {row[5]}")


def main() -> None:
    if SUB_BATCH_SIZE < 1:
        raise RuntimeError(
            "EVI_RECOMPUTE_SUB_BATCH_SIZE deve "
            "essere almeno 1."
        )

    init_ee()

    ee.data.setDeadline(
        EE_TIMEOUT_SECONDS * 1000
    )

    ee.data.setMaxRetries(
        EE_MAX_RETRIES
    )

    conn = database_connection()

    try:
        backup_inserted_n = create_backup(
            conn
        )

        items = load_items(
            conn
        )

        print("Ricalcolo mirato EVI v1")
        print("-----------------------")
        print(
            f"backup nuove righe: "
            f"{backup_inserted_n}"
        )
        print(
            f"geometrie da ricalcolare: "
            f"{len(items)}"
        )
        print(
            f"sotto-batch: {SUB_BATCH_SIZE}"
        )

        if not items:
            print(
                "Nessuna geometria sospetta "
                "da ricalcolare."
            )
            return

        item_chunks = chunks(
            items,
            SUB_BATCH_SIZE,
        )

        completed_n = 0

        for chunk_number, item_chunk in enumerate(
            item_chunks,
            start=1,
        ):
            geometry_batch = [
                (
                    item["source_geometry_id"],
                    json.loads(
                        item["geometry_geojson"]
                    ),
                )
                for item in item_chunk
            ]

            requested_ids = [
                item["source_geometry_id"]
                for item in item_chunk
            ]

            try:
                records = reduce_spectral_metrics(
                    geometry_batch
                )

                validate_records(
                    requested_ids=requested_ids,
                    records=records,
                )

                upsert_records(
                    conn=conn,
                    records=records,
                )

            except Exception as exception:
                conn.rollback()

                if is_infrastructure_error(
                    exception
                ):
                    print(
                        "Interruzione di rete o DNS. "
                        "Le geometrie non elaborate "
                        "restano invariate."
                    )
                    print(
                        f"Errore: "
                        f"{type(exception).__name__}: "
                        f"{exception}"
                    )
                    break

                raise

            completed_n += len(records)

            print(
                f"sotto-batch "
                f"{chunk_number}/"
                f"{len(item_chunks)} "
                f"completato | "
                f"{len(records)} aree"
            )

            if (
                SLEEP_BETWEEN_BATCHES_SECONDS > 0
                and chunk_number
                < len(item_chunks)
            ):
                time.sleep(
                    SLEEP_BETWEEN_BATCHES_SECONDS
                )

        print()
        print(
            f"Geometrie aggiornate in questa "
            f"esecuzione: {completed_n}"
        )

        print_summary(
            conn
        )

        audit_path = export_audit_csv(
            conn
        )

        print(
            f"Audit CSV: {audit_path}"
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
