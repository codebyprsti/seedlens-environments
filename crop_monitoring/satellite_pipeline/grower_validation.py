"""
Validation reports for grower linkage and satellite schema hygiene.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from crop_monitoring.satellite_pipeline.harvest_field_mapping import MappingReport

logger = logging.getLogger(__name__)


def write_grower_validation_reports(
    db: Session,
    report: MappingReport,
    output_dir: Path,
    *,
    prefix: str = "grower",
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    null_gid = output_dir / f"{prefix}_null_grower_id_in_satellite.csv"
    with null_gid.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table", "location_id", "internal_id", "grower_name", "observation_date"])
        for table in ("sentinel1_indices", "sentinel2_indices", "sentinel3_indices"):
            if not _table_exists(db, table):
                continue
            rows = db.execute(
                text(f"""
                    SELECT location_id, internal_id, grower_name, observation_date
                    FROM operations.{table}
                    WHERE grower_name IS NOT NULL AND TRIM(grower_name) <> ''
                      AND (grower_id IS NULL OR TRIM(grower_id) = '')
                    ORDER BY location_id, observation_date
                    LIMIT 5000
                """)
            ).fetchall()
            for r in rows:
                w.writerow([table, r[0], r[1], r[2], r[3]])
    paths["null_grower_id"] = null_gid

    dup_growers = output_dir / f"{prefix}_duplicate_grower_names.csv"
    rows = db.execute(
        text("""
            SELECT LOWER(REGEXP_REPLACE(TRIM(grower_name), '\\s+', ' ', 'g')) AS norm,
                   COUNT(*) AS cnt,
                   array_agg(grower_id ORDER BY grower_id) AS ids
            FROM operations.growers
            WHERE grower_name IS NOT NULL AND TRIM(grower_name) <> ''
            GROUP BY 1
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
            LIMIT 500
        """)
    ).fetchall()
    with dup_growers.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["normalized_name", "count", "grower_ids"])
        for r in rows:
            w.writerow([r[0], r[1], r[2]])
    paths["duplicate_growers"] = dup_growers

    unmatched = output_dir / f"{prefix}_unmatched_field_mappings.csv"
    with unmatched.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["internal_id", "location_id", "grower_name", "grower_id", "notes"])
        for m in sorted(report.mappings.values(), key=lambda x: x.internal_id):
            if not m.grower_name or not m.grower_id:
                w.writerow([m.internal_id, m.location_id, m.grower_name or "", m.grower_id or "", m.validation_notes or ""])
    paths["unmatched_mappings"] = unmatched

    new_growers = output_dir / f"{prefix}_newly_created_grower_ids.csv"
    if new_growers.exists():
        paths["new_growers"] = new_growers

    dup_loc = output_dir / f"{prefix}_duplicate_location_internal.csv"
    with dup_loc.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["location_id", "internal_ids"])
        seen: dict[str, list[str]] = {}
        for m in report.mappings.values():
            seen.setdefault(m.location_id, []).append(m.internal_id)
        for loc, iids in sorted(seen.items()):
            if len(iids) > 1:
                w.writerow([loc, ";".join(iids)])
    paths["duplicate_location"] = dup_loc

    removed_cols = output_dir / f"{prefix}_schema_columns_removed.txt"
    removed_cols.write_text(
        "Dropped columns (if existed): crop_indices_id (all sentinel tables), valid_pixel_fraction (S2)\n"
        "Added: satellite_source, cloud_coverage, orbit_direction, processing_level\n"
        "Unique index: (location_id, season_id, acquisition_date, satellite_source)\n",
        encoding="utf-8",
    )
    paths["schema"] = removed_cols

    logger.info("Grower validation reports written to %s", output_dir)
    return paths


def _table_exists(db: Session, table: str) -> bool:
    row = db.execute(
        text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'operations' AND table_name = :t LIMIT 1
        """),
        {"t": table},
    ).fetchone()
    return row is not None


def export_new_grower_ids(db: Session, grower_ids: list[str], path: Path) -> None:
    if not grower_ids:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grower_id", "grower_name"])
        for gid in grower_ids:
            row = db.execute(
                text("SELECT grower_name FROM operations.growers WHERE grower_id = :g"),
                {"g": gid},
            ).fetchone()
            w.writerow([gid, row[0] if row else ""])
