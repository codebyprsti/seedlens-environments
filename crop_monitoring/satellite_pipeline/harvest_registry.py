"""
Persist harvest field mappings to operations.harvest_field_registry (DB cache).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from crop_monitoring.satellite_pipeline.harvest_field_mapping import FieldMapping, MappingReport

logger = logging.getLogger(__name__)


def registry_table_exists(db: Session) -> bool:
    row = db.execute(
        text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'operations' AND table_name = 'harvest_field_registry'
            LIMIT 1
        """)
    ).fetchone()
    return row is not None


def upsert_harvest_registry(db: Session, mapping: FieldMapping) -> None:
    if not registry_table_exists(db):
        return
    db.execute(
        text("""
            INSERT INTO operations.harvest_field_registry (
                internal_id, location_id, season_id, file_name, legacy_file_name,
                grower_name, grower_id, kml_path, state_code,
                validation_status, validation_notes, updated_at
            ) VALUES (
                :internal_id, :location_id, :season_id, :file_name, :legacy_file_name,
                :grower_name, :grower_id, :kml_path, :state_code,
                :validation_status, :validation_notes, NOW()
            )
            ON CONFLICT (internal_id) DO UPDATE SET
                location_id = EXCLUDED.location_id,
                season_id = EXCLUDED.season_id,
                file_name = EXCLUDED.file_name,
                legacy_file_name = EXCLUDED.legacy_file_name,
                grower_name = COALESCE(EXCLUDED.grower_name, operations.harvest_field_registry.grower_name),
                grower_id = COALESCE(EXCLUDED.grower_id, operations.harvest_field_registry.grower_id),
                kml_path = EXCLUDED.kml_path,
                validation_status = EXCLUDED.validation_status,
                validation_notes = EXCLUDED.validation_notes,
                updated_at = NOW()
        """),
        {
            "internal_id": mapping.internal_id,
            "location_id": mapping.location_id,
            "season_id": mapping.season_id,
            "file_name": mapping.file_name,
            "legacy_file_name": mapping.legacy_file_name,
            "grower_name": mapping.grower_name,
            "grower_id": mapping.grower_id,
            "kml_path": mapping.kml_path,
            "state_code": mapping.state_code,
            "validation_status": mapping.validation_status,
            "validation_notes": mapping.validation_notes,
        },
    )


def load_registry_by_internal_id(db: Session, internal_id: str) -> Optional[dict]:
    if not registry_table_exists(db):
        return None
    row = db.execute(
        text("""
            SELECT internal_id, location_id, season_id, file_name, legacy_file_name,
                   grower_name, grower_id
            FROM operations.harvest_field_registry
            WHERE internal_id = :iid
            LIMIT 1
        """),
        {"iid": internal_id.upper()},
    ).mappings().fetchone()
    return dict(row) if row else None


def persist_mapping_report(db: Session, report: MappingReport) -> int:
    n = 0
    for m in report.mappings.values():
        upsert_harvest_registry(db, m)
        n += 1
    return n
