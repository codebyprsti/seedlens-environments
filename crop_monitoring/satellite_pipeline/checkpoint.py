"""
Checkpoint engine — stage-level resumable ingestion per file/satellite.
"""

from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    RAW_S2 = "raw_s2"
    RAW_S1 = "raw_s1"
    RAW_S3 = "raw_s3"
    STAC = "stac"
    HARMONIZE_S2 = "harmonize_s2"
    INDICES_S2 = "indices_s2"
    INDICES_S1 = "indices_s1"
    INDICES_S3 = "indices_s3"
    COMPLETE = "complete"


class CheckpointStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


SATELLITE_FOR_STAGE = {
    Stage.RAW_S2: "S2",
    Stage.RAW_S1: "S1",
    Stage.RAW_S3: "S3",
    Stage.STAC: "S2",
    Stage.HARMONIZE_S2: "S2",
    Stage.INDICES_S2: "S2",
    Stage.INDICES_S1: "S1",
    Stage.INDICES_S3: "S3",
    Stage.COMPLETE: "S2",
}


def _table_ok(db: Session) -> bool:
    row = db.execute(
        text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'operations' AND table_name = 'satellite_ingestion_checkpoint'
            LIMIT 1
        """)
    ).fetchone()
    return row is not None


def get_stage_status(
    db: Session,
    run_id: uuid.UUID,
    file_name: str,
    stage: Stage,
) -> Optional[str]:
    if not _table_ok(db):
        return None
    row = db.execute(
        text("""
            SELECT status FROM operations.satellite_ingestion_checkpoint
            WHERE run_id = CAST(:rid AS uuid) AND file_name = :fn AND stage = :stage
            LIMIT 1
        """),
        {"rid": str(run_id), "fn": file_name, "stage": stage.value},
    ).fetchone()
    return str(row[0]) if row else None


def should_skip_stage(
    db: Session,
    run_id: uuid.UUID,
    file_name: str,
    stage: Stage,
    *,
    resume: bool,
) -> bool:
    st = get_stage_status(db, run_id, file_name, stage)
    if st == CheckpointStatus.DONE.value:
        return True
    if st == CheckpointStatus.FAILED.value and resume:
        return False
    if st == CheckpointStatus.SKIPPED.value:
        return True
    return False


def upsert_checkpoint(
    db: Session,
    *,
    run_id: uuid.UUID,
    file_name: str,
    stage: Stage,
    status: CheckpointStatus,
    location_id: Optional[str] = None,
    raw_observation_id: Optional[int] = None,
    processed_row_id: Optional[int] = None,
    last_error: Optional[str] = None,
) -> None:
    if not _table_ok(db):
        return
    sat = SATELLITE_FOR_STAGE.get(stage, "S2")
    db.execute(
        text("""
            INSERT INTO operations.satellite_ingestion_checkpoint (
                run_id, file_name, location_id, satellite, stage, status,
                raw_observation_id, processed_row_id, last_error, updated_at
            ) VALUES (
                CAST(:rid AS uuid), :fn, :loc, :sat, :stage, :status,
                :raw_id, :proc_id, :err, NOW()
            )
            ON CONFLICT (run_id, file_name, satellite, stage)
            DO UPDATE SET
                status = EXCLUDED.status,
                location_id = COALESCE(EXCLUDED.location_id, operations.satellite_ingestion_checkpoint.location_id),
                raw_observation_id = COALESCE(EXCLUDED.raw_observation_id, operations.satellite_ingestion_checkpoint.raw_observation_id),
                processed_row_id = COALESCE(EXCLUDED.processed_row_id, operations.satellite_ingestion_checkpoint.processed_row_id),
                last_error = EXCLUDED.last_error,
                updated_at = NOW()
        """),
        {
            "rid": str(run_id),
            "fn": file_name,
            "loc": location_id,
            "sat": sat,
            "stage": stage.value,
            "status": status.value,
            "raw_id": raw_observation_id,
            "proc_id": processed_row_id,
            "err": (last_error or "")[:2000] if last_error else None,
        },
    )
