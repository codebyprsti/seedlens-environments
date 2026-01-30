"""
User-Level Logging Service for Planning Execution

Generates user-level logs for planning jobs including:
- Job start
- Input parameters
- Target summary
- Eligible villages
- Allocated villages
- Execution duration
- Errors
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import pandas as pd
import time
import pytz

logger = logging.getLogger(__name__)

# IST timezone
IST = pytz.timezone('Asia/Kolkata')


class UserPlanningLogger:
    """User-level logger for planning execution."""
    
    def __init__(self, user_id: str, job_id: str, db: Session):
        self.user_id = user_id
        self.job_id = job_id
        self.db = db
        self.start_time = None
        self.logs: List[Dict[str, Any]] = []
    
    def log_job_start(self, schedule_time: Optional[datetime] = None):
        """Log job start."""
        self.start_time = time.time()
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "INFO",
            "event": "job_start",
            "message": f"Planning job started for user {self.user_id}",
            "schedule_time": schedule_time.isoformat() if schedule_time else None
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.info(f"[User {self.user_id}] Job {self.job_id} started")
    
    def log_input(self, request_data: Dict[str, Any]):
        """Log input parameters."""
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "INFO",
            "event": "input",
            "message": "Planning input parameters",
            "data": {
                "plan_revision_version": request_data.get("plan_revision_version"),
                "season_id": request_data.get("season_id"),
                "crop_id": request_data.get("crop_id"),
                "method_algorithm": request_data.get("method_algorithm"),
                "targets_count": len(request_data.get("targets", [])),
                "criteria_count": len(request_data.get("criteria", [])),
                "constraints_count": len(request_data.get("constraints", []))
            }
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.info(f"[User {self.user_id}] Input logged: {log_entry['data']}")
    
    def log_target_summary(self, targets_list: List[Dict[str, Any]]):
        """Log target summary."""
        total_quantity = sum(t.get("quantity", 0) for t in targets_list)
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "INFO",
            "event": "target_summary",
            "message": f"Processing {len(targets_list)} target(s)",
            "data": {
                "targets_count": len(targets_list),
                "total_quantity_kgs": total_quantity,
                "total_quantity_mt": total_quantity / 1000.0,
                "targets": [
                    {
                        "state": t.get("state"),
                        "variety_id": t.get("variety_id"),
                        "quantity_kgs": t.get("quantity"),
                        "quantity_mt": t.get("quantity", 0) / 1000.0
                    }
                    for t in targets_list
                ]
            }
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.info(f"[User {self.user_id}] Target summary: {len(targets_list)} targets, {total_quantity:,.0f} kgs total")
    
    def log_eligible_villages(self, result_df: pd.DataFrame):
        """Log eligible villages count."""
        if result_df.empty:
            eligible_count = 0
            unique_villages = []
        else:
            if 'village' in result_df.columns:
                unique_villages = result_df['village'].dropna().unique().tolist()
                eligible_count = len(unique_villages)
            else:
                eligible_count = len(result_df)
                unique_villages = []
        
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "INFO",
            "event": "eligible_villages",
            "message": f"Found {eligible_count} eligible village(s)",
            "data": {
                "eligible_villages_count": eligible_count,
                "total_rows": len(result_df)
            }
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.info(f"[User {self.user_id}] Eligible villages: {eligible_count}")
    
    def log_allocated_villages(self, result_df: pd.DataFrame):
        """Log allocated villages count."""
        if result_df.empty:
            allocated_count = 0
            allocated_villages = []
        else:
            # Count villages with positive allocation
            if 'village' in result_df.columns:
                allocated_mask = (
                    (result_df.get('allocated_acres', pd.Series([0])) > 0) |
                    (result_df.get('planned_production', pd.Series([0])) > 0)
                )
                allocated_df = result_df[allocated_mask]
                allocated_villages = allocated_df['village'].dropna().unique().tolist()
                allocated_count = len(allocated_villages)
            else:
                allocated_mask = (
                    (result_df.get('allocated_acres', pd.Series([0])) > 0) |
                    (result_df.get('planned_production', pd.Series([0])) > 0)
                )
                allocated_count = int(allocated_mask.sum())
                allocated_villages = []
        
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "INFO",
            "event": "allocated_villages",
            "message": f"Allocated to {allocated_count} village(s)",
            "data": {
                "allocated_villages_count": allocated_count,
                "total_eligible": len(result_df) if not result_df.empty else 0
            }
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.info(f"[User {self.user_id}] Allocated villages: {allocated_count}")
    
    def log_execution_duration(self):
        """Log execution duration."""
        if self.start_time:
            duration = time.time() - self.start_time
            now_ist = datetime.now(IST)
            log_entry = {
                "timestamp": now_ist.isoformat(),
                "level": "INFO",
                "event": "execution_duration",
                "message": f"Execution completed in {duration:.2f} seconds",
                "data": {
                    "duration_seconds": round(duration, 2),
                    "duration_formatted": f"{duration:.2f}s"
                }
            }
            self.logs.append(log_entry)
            self._persist_log(log_entry)
            logger.info(f"[User {self.user_id}] Execution duration: {duration:.2f}s")
    
    def log_error(self, error_message: str, error_details: Optional[Dict[str, Any]] = None):
        """Log error."""
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "ERROR",
            "event": "error",
            "message": error_message,
            "data": error_details or {}
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.error(f"[User {self.user_id}] Error: {error_message}")
    
    def _persist_log(self, log_entry: Dict[str, Any]):
        """Persist log entry to database."""
        try:
            # Check if table exists
            check_table_query = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'operations' 
                    AND table_name = 'planning_job_logs'
                )
            """)
            table_exists = self.db.execute(check_table_query).scalar()
            
            if not table_exists:
                # Create table with TIMESTAMPTZ
                create_table_query = text("""
                    CREATE TABLE operations.planning_job_logs (
                        id SERIAL PRIMARY KEY,
                        job_id VARCHAR(255) NOT NULL,
                        user_id VARCHAR(255) NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        level VARCHAR(50) NOT NULL,
                        event VARCHAR(100) NOT NULL,
                        message TEXT,
                        data JSONB,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                self.db.execute(create_table_query)
                self.db.commit()
                logger.info("Created planning_job_logs table with TIMESTAMPTZ columns")
            else:
                # Check if columns need to be migrated to TIMESTAMPTZ
                check_column_query = text("""
                    SELECT data_type 
                    FROM information_schema.columns 
                    WHERE table_schema = 'operations' 
                    AND table_name = 'planning_job_logs' 
                    AND column_name = 'timestamp'
                """)
                result = self.db.execute(check_column_query).fetchone()
                if result and result[0] == 'timestamp without time zone':
                    # Migrate columns to TIMESTAMPTZ
                    logger.info("Migrating planning_job_logs table columns to TIMESTAMPTZ")
                    migrate_query = text("""
                        ALTER TABLE operations.planning_job_logs 
                        ALTER COLUMN timestamp TYPE TIMESTAMPTZ USING timestamp AT TIME ZONE 'Asia/Kolkata',
                        ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Asia/Kolkata'
                    """)
                    self.db.execute(migrate_query)
                    self.db.commit()
                    logger.info("Successfully migrated planning_job_logs table to TIMESTAMPTZ")
            
            # Insert log entry
            insert_query = text("""
                INSERT INTO operations.planning_job_logs 
                (job_id, user_id, timestamp, level, event, message, data)
                VALUES (:job_id, :user_id, :timestamp, :level, :event, :message, CAST(:data AS JSONB))
            """)
            
            import json
            # Parse timestamp - handle both with and without timezone
            timestamp_str = log_entry["timestamp"]
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str.replace('Z', '+00:00')
            try:
                timestamp_dt = datetime.fromisoformat(timestamp_str)
                # Ensure it's timezone-aware in IST
                if timestamp_dt.tzinfo is None:
                    timestamp_dt = IST.localize(timestamp_dt)
                else:
                    timestamp_dt = timestamp_dt.astimezone(IST)
            except ValueError:
                # Fallback to current time in IST if parsing fails
                timestamp_dt = datetime.now(IST)
            
            self.db.execute(insert_query, {
                "job_id": self.job_id,
                "user_id": self.user_id,
                "timestamp": timestamp_dt,
                "level": log_entry["level"],
                "event": log_entry["event"],
                "message": log_entry.get("message", ""),
                "data": json.dumps(log_entry.get("data", {}))
            })
            self.db.commit()
        except Exception as e:
            logger.error(f"Error persisting log entry: {str(e)}")
            self.db.rollback()
    
    def log_progress(self, message: str, data: Optional[Dict[str, Any]] = None):
        """Log progress/heartbeat during execution."""
        now_ist = datetime.now(IST)
        log_entry = {
            "timestamp": now_ist.isoformat(),
            "level": "INFO",
            "event": "progress",
            "message": message,
            "data": data or {}
        }
        self.logs.append(log_entry)
        self._persist_log(log_entry)
        logger.info(f"[User {self.user_id}] Progress: {message}")
    
    def get_all_logs(self) -> List[Dict[str, Any]]:
        """Get all logs for this job."""
        return self.logs

