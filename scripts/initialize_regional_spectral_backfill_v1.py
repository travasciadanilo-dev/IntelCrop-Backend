import os
import subprocess
from collections import defaultdict
from datetime import date
from typing import Any

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato.")


RUN_VERSION = "regional_spectral_backfill_v1_20260710"
SPECTRAL_QC_VERSION = "olive_spectral_qc_v1"
SOURCE_POOL_VERSION = "olive_candidate_pool_v2"
SOURCE_PENDING_VIEW = "regional_spectral_backfill_pending_v1"

SATELLITE_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"

PERIOD_START = date(2023, 1, 1)
PERIOD_END = date(2026, 1, 1)

CLOUD_THRESHOLD = 40.0
ALGORITHM_VERSION = "regional_olive_spectral_features_v1"
BATCH_SIZE = 500

EXPECTED_PENDING_N = 39679

EXPECTED_ZONE_COUNTS = {
    "central_calabria": 19593,
    "north_calabria": 9563,
    "south_calabria": 10523,
}

ZONE_ORDER = {
    "central_calabria": 1,
    "north_calabria": 2,
    "south_calabria": 3,
}


def get_git_commit() -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--short=12",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value or None

    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
    ):
        return None


def load_pending_rows(
    cursor: Any,
) -> list[tuple]:
    cursor.execute(
        """
        SELECT
            area_id,
            source_geometry_id,
            subtype_id,
            source_layer_version,
            spatial_validation_zone,
            current_high_confidence_v2,
            area_ha_raw,
            perimeter_m_raw,
            compactness_raw,
            n_points,
            n_parts
        FROM regional_spectral_backfill_pending_v1
        ORDER BY
            CASE spatial_validation_zone
                WHEN 'central_calabria' THEN 1
                WHEN 'north_calabria' THEN 2
                WHEN 'south_calabria' THEN 3
                ELSE 99
            END,
            area_id;
        """
    )

    rows = cursor.fetchall()

    if len(rows) != EXPECTED_PENDING_N:
        raise RuntimeError(
            "Numero di aree pending inatteso: "
            f"attese {EXPECTED_PENDING_N}, "
            f"trovate {len(rows)}."
        )

    area_ids = [row[0] for row in rows]
    geometry_ids = [row[1] for row in rows]

    if len(set(area_ids)) != len(rows):
        raise RuntimeError(
            "Sono presenti area_id duplicati "
            "nella vista pending."
        )

    if len(set(geometry_ids)) != len(rows):
        raise RuntimeError(
            "Sono presenti source_geometry_id "
            "duplicati nella vista pending."
        )

    zone_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        zone = row[4]

        if zone not in EXPECTED_ZONE_COUNTS:
            raise RuntimeError(
                f"Zona geografica inattesa: {zone!r}."
            )

        zone_counts[zone] += 1

    if dict(zone_counts) != EXPECTED_ZONE_COUNTS:
        raise RuntimeError(
            "Conteggi zonali inattesi. "
            f"Attesi: {EXPECTED_ZONE_COUNTS}. "
            f"Trovati: {dict(zone_counts)}."
        )

    return rows


def get_or_create_run(
    cursor: Any,
    git_commit: str | None,
) -> tuple[str, bool]:
    cursor.execute(
        """
        SELECT
            run_id,
            initial_area_n
        FROM regional_spectral_backfill_runs
        WHERE run_version = %s;
        """,
        (RUN_VERSION,),
    )

    existing = cursor.fetchone()

    if existing:
        run_id, initial_area_n = existing

        if initial_area_n != EXPECTED_PENDING_N:
            raise RuntimeError(
                "Il run esistente ha un numero "
                "iniziale di aree inatteso: "
                f"{initial_area_n}."
            )

        return str(run_id), False

    cursor.execute(
        """
        INSERT INTO regional_spectral_backfill_runs (
            run_version,
            spectral_qc_version,
            source_pool_version,
            source_pending_view,
            satellite_collection,
            period_start,
            period_end,
            cloud_threshold,
            algorithm_version,
            batch_size,
            status,
            initial_area_n,
            pending_area_n,
            git_commit,
            created_by,
            metadata
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'created',
            %s,
            %s,
            %s,
            %s,
            jsonb_build_object(
                'candidate_pool_n', 40261,
                'existing_spectral_n', 582,
                'training_complete_n', 406,
                'zone_counts',
                jsonb_build_object(
                    'central_calabria', 19593,
                    'north_calabria', 9563,
                    'south_calabria', 10523
                ),
                'snapshot_strategy',
                'zone-separated deterministic batches'
            )
        )
        RETURNING run_id;
        """,
        (
            RUN_VERSION,
            SPECTRAL_QC_VERSION,
            SOURCE_POOL_VERSION,
            SOURCE_PENDING_VIEW,
            SATELLITE_COLLECTION,
            PERIOD_START,
            PERIOD_END,
            CLOUD_THRESHOLD,
            ALGORITHM_VERSION,
            BATCH_SIZE,
            EXPECTED_PENDING_N,
            EXPECTED_PENDING_N,
            git_commit,
            os.getenv("USERNAME")
            or os.getenv("USER")
            or "unknown",
        ),
    )

    run_id = cursor.fetchone()[0]

    return str(run_id), True


def verify_empty_run(
    cursor: Any,
    run_id: str,
) -> None:
    cursor.execute(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM regional_spectral_backfill_batches
                WHERE run_id = %s
            ) AS batch_n,
            (
                SELECT COUNT(*)
                FROM regional_spectral_backfill_items
                WHERE run_id = %s
            ) AS item_n;
        """,
        (run_id, run_id),
    )

    batch_n, item_n = cursor.fetchone()

    if batch_n == 0 and item_n == 0:
        return

    if batch_n > 0 and item_n > 0:
        raise RuntimeError(
            "Il run esiste ed è già popolato. "
            "Non viene modificato."
        )

    raise RuntimeError(
        "Run parzialmente popolato: "
        f"batches={batch_n}, items={item_n}. "
        "Richiesta verifica manuale."
    )


def build_batches(
    rows: list[tuple],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple]] = defaultdict(list)

    for row in rows:
        grouped[row[4]].append(row)

    batches: list[dict[str, Any]] = []
    batch_number = 1

    for zone in sorted(
        grouped,
        key=lambda value: ZONE_ORDER[value],
    ):
        zone_rows = grouped[zone]

        for start in range(
            0,
            len(zone_rows),
            BATCH_SIZE,
        ):
            chunk = zone_rows[
                start:start + BATCH_SIZE
            ]

            batches.append(
                {
                    "batch_number": batch_number,
                    "zone": zone,
                    "rows": chunk,
                }
            )

            batch_number += 1

    return batches


def insert_snapshot(
    cursor: Any,
    run_id: str,
    batches: list[dict[str, Any]],
) -> None:
    inserted_items = 0

    for batch in batches:
        cursor.execute(
            """
            INSERT INTO regional_spectral_backfill_batches (
                run_id,
                batch_number,
                spatial_validation_zone,
                expected_area_n,
                status,
                metadata
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                'pending',
                jsonb_build_object(
                    'batch_size_limit', %s,
                    'ordering', 'area_id'
                )
            )
            RETURNING batch_id;
            """,
            (
                run_id,
                batch["batch_number"],
                batch["zone"],
                len(batch["rows"]),
                BATCH_SIZE,
            ),
        )

        batch_id = cursor.fetchone()[0]

        values = [
            (
                run_id,
                batch_id,
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                "pending",
                0,
            )
            for row in batch["rows"]
        ]

        execute_values(
            cursor,
            """
            INSERT INTO regional_spectral_backfill_items (
                run_id,
                batch_id,
                area_id,
                source_geometry_id,
                subtype_id,
                source_layer_version,
                spatial_validation_zone,
                current_high_confidence_v2,
                area_ha_raw,
                perimeter_m_raw,
                compactness_raw,
                n_points,
                n_parts,
                status,
                attempt_count
            )
            VALUES %s
            """,
            values,
            page_size=500,
        )

        inserted_items += len(values)

    if inserted_items != EXPECTED_PENDING_N:
        raise RuntimeError(
            "Numero di item inseriti inatteso: "
            f"{inserted_items}."
        )

    cursor.execute(
        """
        UPDATE regional_spectral_backfill_runs
        SET
            status = 'ready',
            pending_area_n = %s,
            running_area_n = 0,
            completed_area_n = 0,
            failed_area_n = 0,
            updated_at = now()
        WHERE run_id = %s;
        """,
        (
            EXPECTED_PENDING_N,
            run_id,
        ),
    )


def verify_snapshot(
    cursor: Any,
    run_id: str,
) -> None:
    cursor.execute(
        """
        SELECT
            COUNT(*) AS batch_n,
            SUM(expected_area_n) AS expected_items,
            MIN(expected_area_n) AS min_batch_size,
            MAX(expected_area_n) AS max_batch_size
        FROM regional_spectral_backfill_batches
        WHERE run_id = %s;
        """,
        (run_id,),
    )

    (
        batch_n,
        expected_items,
        min_batch_size,
        max_batch_size,
    ) = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS item_n,
            COUNT(DISTINCT area_id)
                AS distinct_area_n,
            COUNT(DISTINCT source_geometry_id)
                AS distinct_geometry_n
        FROM regional_spectral_backfill_items
        WHERE run_id = %s;
        """,
        (run_id,),
    )

    (
        item_n,
        distinct_area_n,
        distinct_geometry_n,
    ) = cursor.fetchone()

    if expected_items != EXPECTED_PENDING_N:
        raise RuntimeError(
            "Somma expected_area_n inattesa: "
            f"{expected_items}."
        )

    if item_n != EXPECTED_PENDING_N:
        raise RuntimeError(
            f"Item inattesi: {item_n}."
        )

    if distinct_area_n != EXPECTED_PENDING_N:
        raise RuntimeError(
            "area_id distinti inattesi: "
            f"{distinct_area_n}."
        )

    if distinct_geometry_n != EXPECTED_PENDING_N:
        raise RuntimeError(
            "source_geometry_id distinti inattesi: "
            f"{distinct_geometry_n}."
        )

    print("Regional spectral backfill snapshot")
    print("-----------------------------------")
    print(f"run_id: {run_id}")
    print(f"run_version: {RUN_VERSION}")
    print("status: ready")
    print(f"batch_size_limit: {BATCH_SIZE}")
    print(f"batch_n: {batch_n}")
    print(f"snapshot_area_n: {item_n}")
    print(f"min_batch_size: {min_batch_size}")
    print(f"max_batch_size: {max_batch_size}")

    print()
    print("Expected batches by zone")

    cursor.execute(
        """
        SELECT
            spatial_validation_zone,
            COUNT(*) AS batch_n,
            SUM(expected_area_n) AS area_n,
            MIN(expected_area_n) AS min_batch_size,
            MAX(expected_area_n) AS max_batch_size
        FROM regional_spectral_backfill_batches
        WHERE run_id = %s
        GROUP BY spatial_validation_zone
        ORDER BY spatial_validation_zone;
        """,
        (run_id,),
    )

    for row in cursor.fetchall():
        print(
            f"{row[0]} | batches={row[1]} "
            f"| areas={row[2]} "
            f"| min={row[3]} "
            f"| max={row[4]}"
        )


def main() -> None:
    git_commit = get_git_commit()

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            pending_rows = load_pending_rows(cursor)

            run_id, created = get_or_create_run(
                cursor,
                git_commit,
            )

            if not created:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM regional_spectral_backfill_items
                    WHERE run_id = %s;
                    """,
                    (run_id,),
                )

                existing_item_n = cursor.fetchone()[0]

                if existing_item_n == EXPECTED_PENDING_N:
                    print(
                        "Run già inizializzato e coerente. "
                        "Nessuna modifica eseguita."
                    )
                    verify_snapshot(cursor, run_id)
                    return

            verify_empty_run(cursor, run_id)

            batches = build_batches(pending_rows)

            insert_snapshot(
                cursor=cursor,
                run_id=run_id,
                batches=batches,
            )

            verify_snapshot(cursor, run_id)


if __name__ == "__main__":
    main()
