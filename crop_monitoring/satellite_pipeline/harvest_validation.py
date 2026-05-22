"""
Validation report for harvest KML batch (duplicates, geometry, unmatched growers).
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from crop_monitoring.satellite_pipeline.harvest_field_mapping import (
    FieldMapping,
    MappingReport,
    validate_kml_geometry,
)

logger = logging.getLogger(__name__)


def run_full_validation(
    report: MappingReport,
    kml_dir: Path,
) -> MappingReport:
    """Augment mapping report with geometry checks and duplicate location_ids."""
    loc_to_internals: dict[str, list[str]] = defaultdict(list)
    for iid, m in report.mappings.items():
        loc_to_internals[m.location_id].append(iid)

    for loc, iids in loc_to_internals.items():
        if len(iids) > 1:
            report.errors.append(f"duplicate_location_id {loc}: {iids}")

    for iid, m in report.mappings.items():
        if not m.kml_path:
            continue
        ok, err = validate_kml_geometry(Path(m.kml_path))
        if not ok:
            report.errors.append(f"invalid_geometry {iid}: {err}")
            m.validation_status = "error"
            m.validation_notes = (m.validation_notes or "") + f"; geometry:{err}"

    missing_grower = [iid for iid, m in report.mappings.items() if not m.grower_name]
    for iid in missing_grower:
        report.warnings.append(f"missing_grower_name: {iid}")

    return report


def write_validation_reports(
    report: MappingReport,
    output_dir: Path,
    *,
    prefix: str = "harvest",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"{prefix}_validation.log"
    unmatched_path = output_dir / f"{prefix}_unmatched.csv"

    lines = []
    lines.append(f"errors={len(report.errors)} warnings={len(report.warnings)}")
    lines.extend(f"ERROR: {e}" for e in report.errors)
    lines.extend(f"WARN: {w}" for w in report.warnings)
    lines.extend(f"DUP_INTERNAL: {d}" for d in report.duplicate_internal_ids)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with unmatched_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "internal_id",
                "location_id",
                "file_name",
                "grower_name",
                "grower_id",
                "validation_status",
                "validation_notes",
            ]
        )
        for m in sorted(report.mappings.values(), key=lambda x: x.internal_id):
            if m.validation_status != "ok" or not m.grower_name:
                w.writerow(
                    [
                        m.internal_id,
                        m.location_id,
                        m.file_name,
                        m.grower_name or "",
                        m.grower_id or "",
                        m.validation_status,
                        m.validation_notes or "",
                    ]
                )

    logger.info("Wrote %s and %s", log_path, unmatched_path)
    return log_path, unmatched_path
