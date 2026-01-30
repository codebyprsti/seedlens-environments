"""
DB-Driven Scheduler Service for One-Time Planning Execution

Handles scheduling of one-time planning jobs using APScheduler with DB persistence.
Jobs are stored in database and can survive app restarts.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import json
import pandas as pd
import pytz
import threading

logger = logging.getLogger(__name__)

# IST timezone
IST = pytz.timezone('Asia/Kolkata')
UTC = pytz.UTC

# Global scheduler instance with thread-safe initialization
_scheduler: Optional[BackgroundScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> BackgroundScheduler:
    """Get or create the global scheduler instance (thread-safe)."""
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            # Double-check pattern to avoid race conditions
            if _scheduler is None:
                _scheduler = BackgroundScheduler()
                _scheduler.start()
                logger.info("Scheduler started")
    return _scheduler


def _ensure_job_logs_table(db: Session):
    """Ensure planning_job_logs table exists with correct schema."""
    try:
        # Check if table exists
        check_table_query = text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'operations' 
                AND table_name = 'planning_job_logs'
            )
        """)
        table_exists = db.execute(check_table_query).scalar()
        
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
            db.execute(create_table_query)
            db.commit()
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
            result = db.execute(check_column_query).fetchone()
            if result and result[0] == 'timestamp without time zone':
                # Migrate columns to TIMESTAMPTZ
                logger.info("Migrating planning_job_logs table columns to TIMESTAMPTZ")
                migrate_query = text("""
                    ALTER TABLE operations.planning_job_logs 
                    ALTER COLUMN timestamp TYPE TIMESTAMPTZ USING timestamp AT TIME ZONE 'Asia/Kolkata',
                    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Asia/Kolkata'
                """)
                db.execute(migrate_query)
                db.commit()
                logger.info("Successfully migrated planning_job_logs table to TIMESTAMPTZ")
    except Exception as e:
        logger.error(f"Error ensuring planning_job_logs table: {str(e)}")
        db.rollback()
        # Don't raise - allow execution to continue even if logs table creation fails


def _ensure_job_tables(db: Session):
    """Ensure planning_jobs and planning_job_logs tables exist with correct schema.
    
    Note: This function may commit/rollback transactions. It should be called
    before starting a transaction that needs to be atomic.
    """
    try:
        # Check if planning_jobs table exists
        check_table_query = text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'operations' 
                AND table_name = 'planning_jobs'
            )
        """)
        table_exists = db.execute(check_table_query).scalar()
        
        if not table_exists:
            # Create table with TIMESTAMPTZ
            create_table_query = text("""
                CREATE TABLE operations.planning_jobs (
                    job_id VARCHAR(255) PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    schedule_time TIMESTAMPTZ NOT NULL,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    error_message TEXT,
                    request_data JSONB NOT NULL,
                    statistics_summary JSONB,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute(create_table_query)
            db.commit()
            logger.info("Created planning_jobs table with TIMESTAMPTZ columns")
        else:
            # Check if columns need to be migrated to TIMESTAMPTZ
            check_column_query = text("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'operations' 
                AND table_name = 'planning_jobs' 
                AND column_name = 'schedule_time'
            """)
            result = db.execute(check_column_query).fetchone()
            if result and result[0] == 'timestamp without time zone':
                # Migrate columns to TIMESTAMPTZ
                logger.info("Migrating planning_jobs table columns to TIMESTAMPTZ")
                migrate_query = text("""
                    ALTER TABLE operations.planning_jobs 
                    ALTER COLUMN schedule_time TYPE TIMESTAMPTZ USING schedule_time AT TIME ZONE 'Asia/Kolkata',
                    ALTER COLUMN started_at TYPE TIMESTAMPTZ USING started_at AT TIME ZONE 'Asia/Kolkata',
                    ALTER COLUMN completed_at TYPE TIMESTAMPTZ USING completed_at AT TIME ZONE 'Asia/Kolkata',
                    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Asia/Kolkata',
                    ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'Asia/Kolkata'
                """)
                db.execute(migrate_query)
                db.commit()
                logger.info("Successfully migrated planning_jobs table to TIMESTAMPTZ")
        
        # Ensure logs table exists (this may also commit)
        _ensure_job_logs_table(db)
        
    except Exception as e:
        logger.error(f"Error ensuring planning_jobs table: {str(e)}", exc_info=True)
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.error(f"Error during rollback in _ensure_job_tables: {str(rollback_error)}")
        raise


def _verify_job_persisted(db: Session, job_id: str) -> bool:
    """
    Verify that a job was successfully persisted to the database.
    
    Args:
        db: Database session
        job_id: Job identifier to verify
    
    Returns:
        True if job exists in database, False otherwise
    """
    try:
        verify_query = text("""
            SELECT job_id, status, created_at
            FROM operations.planning_jobs
            WHERE job_id = :job_id
        """)
        result = db.execute(verify_query, {"job_id": job_id}).fetchone()
        if result:
            logger.debug(f"Verified job {job_id} exists in database with status {result[1]}")
            return True
        else:
            logger.warning(f"Job {job_id} not found in database after commit")
            return False
    except Exception as e:
        logger.error(f"Error verifying job {job_id} persistence: {str(e)}", exc_info=True)
        return False


def schedule_one_time_job(
    db: Session,
    job_id: str,
    user_id: str,
    schedule_time: datetime,
    request_data: Dict[str, Any]
) -> bool:
    """
    Schedule a one-time job by storing it in the database.
    
    This function ensures atomic persistence of jobs:
    1. Ensures tables exist (may commit separately)
    2. Inserts/updates job in database
    3. Commits transaction
    4. Verifies job was persisted
    5. Schedules job in APScheduler (non-blocking)
    
    Args:
        db: Database session (will be committed, not closed)
        job_id: Unique identifier for the job
        user_id: User identifier
        schedule_time: When to execute the job (must be in the future)
        request_data: Request data for the job
    
    Returns:
        True if scheduled successfully, False otherwise
    
    Raises:
        ValueError: If scheduling fails (with detailed error message)
    """
    try:
        # Ensure tables exist (this may commit separately, but that's OK)
        _ensure_job_tables(db)
        
        # Ensure schedule_time is timezone-aware in IST
        if schedule_time.tzinfo is None:
            schedule_time = IST.localize(schedule_time)
        else:
            # Convert to IST if it's in a different timezone
            schedule_time = schedule_time.astimezone(IST)
        
        # Validate schedule_time is in the future (allow up to 1 minute in the past for clock skew)
        # Compare in IST
        now_ist = datetime.now(IST)
        time_diff = (schedule_time - now_ist).total_seconds()
        if time_diff < -60:
            logger.error(f"Schedule time {schedule_time} is too far in the past")
            return False
        
        # Store job in database with 'pending' status (store as IST timezone-aware)
        # Use explicit transaction handling to ensure atomicity
        try:
            insert_query = text("""
                INSERT INTO operations.planning_jobs 
                (job_id, user_id, status, schedule_time, request_data, created_at, updated_at)
                VALUES (:job_id, :user_id, 'pending', :schedule_time, CAST(:request_data AS JSONB), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (job_id) DO UPDATE SET
                    status = 'pending',
                    schedule_time = :schedule_time,
                    request_data = CAST(:request_data AS JSONB),
                    updated_at = CURRENT_TIMESTAMP
            """)

            db.execute(insert_query, {
                "job_id": job_id,
                "user_id": user_id,
                "schedule_time": schedule_time,
                "request_data": json.dumps(request_data)
            })
            db.flush()  # Flush to ensure data is available for verification within transaction
            
            # Commit the transaction
            db.commit()
            logger.info(f"Job {job_id} committed to database")
            
            # Verify the job was persisted after commit (using a new query to ensure we see committed data)
            # Use a fresh query to verify persistence across transaction boundaries
            if not _verify_job_persisted(db, job_id):
                logger.error(f"CRITICAL: Job {job_id} was not found in database after commit!")
                # This is a serious issue - the commit may have failed silently
                # Try to re-insert or raise an error
                raise ValueError(f"Job {job_id} was not persisted to database after commit. This may indicate a transaction isolation or concurrency issue.")
            
            # Schedule the job in APScheduler AFTER successful DB commit
            # APScheduler expects UTC, so convert IST to UTC
            scheduler = get_scheduler()
            
            try:
                # Check if job already exists in scheduler
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)
                    logger.info(f"Removed existing scheduler job {job_id}")
                
                # Convert IST to UTC for APScheduler
                schedule_time_utc = schedule_time.astimezone(UTC)
                
                # Schedule the job (APScheduler uses UTC internally)
                scheduler.add_job(
                    func=_execute_scheduled_job,
                    trigger=DateTrigger(run_date=schedule_time_utc),
                    id=job_id,
                    args=[job_id],
                    replace_existing=True
                )
                
                logger.info(f"Job {job_id} scheduled in APScheduler for {schedule_time} IST ({schedule_time_utc} UTC)")
            except Exception as scheduler_error:
                # If scheduler fails, log but don't fail the entire operation
                # The job is already in DB and can be rescheduled on restart
                logger.error(f"Failed to schedule job {job_id} in APScheduler (job is in DB): {str(scheduler_error)}", exc_info=True)
                # Don't raise - job is persisted and will be loaded on restart
            
            logger.info(f"Job {job_id} successfully scheduled for {schedule_time} IST ({schedule_time_utc} UTC) and stored in database")
            return True
        except Exception as db_error:
            # Rollback on any DB error
            db.rollback()
            logger.error(f"Database error scheduling job {job_id}: {str(db_error)}", exc_info=True)
            raise
    except Exception as e:
        error_msg = f"Error scheduling job {job_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        db.rollback()
        # Store error message for better error reporting
        # Re-raise as ValueError so endpoint can catch and provide better error message
        raise ValueError(error_msg) from e


def _execute_scheduled_job(job_id: str):
    """
    Execute a scheduled job by reading from database and running the planning logic.
    This function is called by APScheduler at the scheduled time.
    """
    from core.db import get_db
    from planning_methods.plan_execution import PlanExecutionOrchestrator
    from planning_methods.user_logging_service import UserPlanningLogger
    from planning_methods.endpoints import _generate_statistics_summary
    
    db = next(get_db())
    user_logger = None
    try:
        # Read job from database
        select_query = text("""
            SELECT user_id, request_data, status
            FROM operations.planning_jobs
            WHERE job_id = :job_id
        """)
        result = db.execute(select_query, {"job_id": job_id}).fetchone()
        
        if not result:
            logger.error(f"Job {job_id} not found in database")
            return
        
        user_id, request_data_json, status = result
        
        # Check if already processed
        if status in ['running', 'completed', 'failed']:
            logger.warning(f"Job {job_id} already in status {status}, skipping execution")
            return
        
        # Update status to 'queued' (job picked up by scheduler, about to start)
        update_queued_query = text("""
            UPDATE operations.planning_jobs
            SET status = 'queued',
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id AND status = 'pending'
        """)
        db.execute(update_queued_query, {"job_id": job_id})
        db.commit()
        
        # Initialize user logger early for queued state logging
        user_logger = UserPlanningLogger(user_id, job_id, db)
        user_logger.log_progress("Job queued and ready to execute", {"status": "queued"})
        
        # Parse request data
        request_data = json.loads(request_data_json) if isinstance(request_data_json, str) else request_data_json
        
        # Update status to 'running'
        update_status_query = text("""
            UPDATE operations.planning_jobs
            SET status = 'running',
                started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id
        """)
        db.execute(update_status_query, {"job_id": job_id})
        db.commit()
        
        # Log job start (user_logger already initialized above)
        user_logger.log_job_start()
        user_logger.log_progress("Job execution started", {"status": "running"})
        user_logger.log_input(request_data)
        
        # Convert to targets list
        targets_list = [
            {"state": t["state"], "variety_id": t["variety_id"], "quantity": t["quantity"]}
            for t in request_data.get("targets", [])
        ]
        
        user_logger.log_target_summary(targets_list)
        user_logger.log_progress("Targets validated and prepared", {"targets_count": len(targets_list)})
        
        # Convert criteria and constraints
        criteria_list = [
            {"id": c["id"], "min_allocation": c["min_allocation"], "max_allocation": c["max_allocation"]}
            for c in (request_data.get("criteria") or [])
        ]
        constraints_list = [
            {"id": c["id"], "value": c["value"]}
            for c in (request_data.get("constraints") or [])
        ]
        
        user_logger.log_progress("Initializing planning orchestrator", {
            "method_algorithm": request_data["method_algorithm"],
            "criteria_count": len(criteria_list),
            "constraints_count": len(constraints_list)
        })
        
        # Execute planning with progress callbacks
        orchestrator = PlanExecutionOrchestrator(db)
        user_logger.log_progress("Starting plan execution", {"stage": "execution_start"})
        
        result_df = orchestrator.execute_plan(
            plan_revision_version=request_data["plan_revision_version"],
            season_id=request_data["season_id"],
            crop_id=request_data["crop_id"],
            targets=targets_list,
            method_algorithm=request_data["method_algorithm"],
            criteria=criteria_list,
            constraints=constraints_list,
            progress_callback=lambda stage, message, data: user_logger.log_progress(message, {**data, "stage": stage}) if user_logger else None
        )
        
        user_logger.log_progress("Plan execution completed", {"stage": "execution_complete", "result_rows": len(result_df) if isinstance(result_df, pd.DataFrame) else 0})
        
        # Validate result
        if not isinstance(result_df, pd.DataFrame):
            result_df = pd.DataFrame()
        
        # Log eligible villages
        user_logger.log_eligible_villages(result_df)
        user_logger.log_progress("Eligible villages identified", {"stage": "village_analysis"})
        
        # Log allocated villages
        user_logger.log_allocated_villages(result_df)
        user_logger.log_progress("Village allocation completed", {"stage": "allocation_complete"})
        
        # Generate statistics (only statistics_summary, not full result_df)
        user_logger.log_progress("Generating statistics summary", {"stage": "statistics_generation"})
        statistics_summary = _generate_statistics_summary(result_df, targets_list, db=db)
        user_logger.log_progress("Statistics summary generated", {"stage": "statistics_complete", "statistics_count": len(statistics_summary) if isinstance(statistics_summary, list) else 0})
        
        # Persist allocation data to operations.plan_allocation (using same logic as /execute)
        # This must happen after plan execution completes, before updating job status
        allocation_persisted = False
        rows_persisted = 0
        if not result_df.empty:
            try:
                user_logger.log_progress("Persisting allocation data to operations.plan_allocation", {"stage": "persistence_allocation"})
                from planning_methods.endpoints import _persist_plan_allocation
                rows_persisted = _persist_plan_allocation(
                    result_df=result_df,
                    plan_revision_version=request_data["plan_revision_version"],
                    season_id=request_data["season_id"],
                    crop_id=request_data["crop_id"],
                    db=db
                )
                allocation_persisted = True
                user_logger.log_progress(f"Persisted {rows_persisted} allocation rows to operations.plan_allocation", {"stage": "persistence_allocation_complete", "rows_persisted": rows_persisted})
                logger.info(f"Successfully persisted {rows_persisted} rows to operations.plan_allocation for job {job_id}")
            except Exception as persist_error:
                logger.error(f"Error persisting allocation data for job {job_id}: {str(persist_error)}", exc_info=True)
                user_logger.log_error(f"Failed to persist allocation data: {str(persist_error)}", {"error_type": type(persist_error).__name__})
                # Continue execution - persistence failure should not block job completion
        else:
            logger.info(f"No allocation data to persist for job {job_id} (result_df is empty)")
            user_logger.log_progress("No allocation data to persist (empty result)", {"stage": "persistence_allocation_skipped"})
        
        # Persist aggregated statistics to operations.plan_statistics (using same logic as /execute)
        # This must always happen, even if allocation is empty (statistics include zero allocations)
        stats_persisted = False
        stats_count = 0
        try:
            user_logger.log_progress("Persisting statistics to operations.plan_statistics", {"stage": "persistence_statistics"})
            from planning_methods.endpoints import _persist_statistics_summary
            stats_count = _persist_statistics_summary(
                statistics_summary=statistics_summary,
                plan_revision_version=request_data["plan_revision_version"],
                season_id=request_data["season_id"],
                crop_id=request_data["crop_id"],
                method_algorithm=request_data["method_algorithm"].strip().lower(),
                db=db
            )
            stats_persisted = True
            user_logger.log_progress(f"Persisted {stats_count} statistics rows to operations.plan_statistics", {"stage": "persistence_statistics_complete", "stats_count": stats_count})
            logger.info(f"Successfully persisted {stats_count} statistics rows to operations.plan_statistics for job {job_id}")
        except Exception as persist_error:
            logger.error(f"Error persisting statistics for job {job_id}: {str(persist_error)}", exc_info=True)
            user_logger.log_error(f"Failed to persist statistics: {str(persist_error)}", {"error_type": type(persist_error).__name__})
            # Continue execution - persistence failure should not block job completion
        
        # Log persistence status
        if allocation_persisted and stats_persisted:
            logger.info(f"✓ All persistence operations completed successfully for job {job_id}")
            user_logger.log_progress("All persistence operations completed", {"stage": "persistence_complete", "allocation_rows": rows_persisted, "statistics_rows": stats_count})
        else:
            failed_ops = []
            if not result_df.empty and not allocation_persisted:
                failed_ops.append("plan_allocation")
            if not stats_persisted:
                failed_ops.append("plan_statistics")
            if failed_ops:
                logger.warning(f"⚠ Persistence failed for {', '.join(failed_ops)} in job {job_id}. Check logs above for details.")
                user_logger.log_progress(f"Persistence incomplete: {', '.join(failed_ops)} failed", {"stage": "persistence_partial", "failed_operations": failed_ops})
        
        # Log execution duration (needed for execution_summary)
        user_logger.log_execution_duration()
        
        # Update job status to 'completed' and store statistics_summary
        user_logger.log_progress("Finalizing job completion", {"stage": "finalization"})
        update_complete_query = text("""
            UPDATE operations.planning_jobs
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                statistics_summary = CAST(:statistics_summary AS JSONB),
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id
        """)
        db.execute(update_complete_query, {
            "job_id": job_id,
            "statistics_summary": json.dumps(statistics_summary)
        })
        db.commit()
        
        user_logger.log_progress("Job completed successfully", {"status": "completed", "stage": "complete"})
        logger.info(f"Job {job_id} completed successfully")
        
    except Exception as e:
        error_msg = f"Planning execution failed: {str(e)}"
        logger.error(f"Job {job_id} failed: {error_msg}", exc_info=True)
        
        # Log error if logger is available
        try:
            if user_logger:
                user_logger.log_progress("Job execution failed", {"status": "failed", "stage": "error", "error": str(e)})
                user_logger.log_error(error_msg, {"error_type": type(e).__name__})
                user_logger.log_execution_duration()
        except Exception as log_error:
            logger.warning(f"Error logging failure for job {job_id}: {str(log_error)}")
        
        # Update job status to 'failed'
        try:
            update_failed_query = text("""
                UPDATE operations.planning_jobs
                SET status = 'failed',
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = :error_message,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = :job_id
            """)
            db.execute(update_failed_query, {
                "job_id": job_id,
                "error_message": error_msg
            })
            db.commit()
        except Exception as update_error:
            logger.error(f"Error updating job status to failed: {str(update_error)}")
            db.rollback()
    finally:
        db.close()


def _build_execution_summary(all_logs: List[Dict[str, Any]], job_status: str) -> Optional[Dict[str, Any]]:
    """
    Build execution_summary for completed jobs (status + execution duration).
    
    Args:
        all_logs: All logs from database
        job_status: Job status (should be "completed" or "failed")
        
    Returns:
        Execution summary dict with status and duration, or None
    """
    if job_status not in ['completed', 'failed']:
        return None
    
    # Find execution_duration log
    duration_logs = [log for log in all_logs if log.get("event") == "execution_duration"]
    if not duration_logs:
        return None
    
    duration_log = duration_logs[-1]  # Get most recent duration log
    duration_data = duration_log.get("data", {})
    
    return {
        "status": job_status,
        "duration_seconds": duration_data.get("duration_seconds"),
        "duration_formatted": duration_data.get("duration_formatted", "")
    }


def _filter_logs_by_status(all_logs: List[Dict[str, Any]], job_status: str) -> List[Dict[str, Any]]:
    """
    Filter logs based on job status for UI-friendly API response.
    
    Filters at response level only - all logs remain in database for audit/debugging.
    
    - For pending/scheduled/queued/running: Return only the latest progress log
    - For completed: Return only completion-related logs (success message + execution_duration)
      Excludes: job_start, input, routing, target_summary, and other historical/debug logs
    - For failed: Return error logs + execution_duration (excludes historical logs)
    
    Args:
        all_logs: All logs from database (preserved for audit)
        job_status: Current job status
        
    Returns:
        Filtered list of logs optimized for UI display
    """
    if not all_logs:
        return []
    
    # Events to exclude from completed/failed responses (historical/debug logs)
    excluded_events = {'job_start', 'input', 'target_summary', 'eligible_villages', 
                      'allocated_villages', 'progress'}  # progress excluded unless completion-related
    
    # For pending/scheduled/queued/running: return only latest progress log
    if job_status in ['pending', 'scheduled', 'queued', 'running']:
        # Find the latest progress log
        progress_logs = [log for log in all_logs if log.get("event") == "progress"]
        if progress_logs:
            # Return the most recent progress log
            return [progress_logs[-1]]
        # If no progress log, return the most recent log of any type
        return [all_logs[-1]] if all_logs else []
    
    # For completed: return only completion-related logs (exclude historical/debug logs)
    # Note: execution_duration is now in execution_summary, not in logs
    elif job_status == 'completed':
        summary_logs = []
        
        # Include final progress log with "complete" status or completion message
        # Only include progress logs that indicate completion
        final_progress = [log for log in all_logs 
                         if log.get("event") == "progress" 
                         and (log.get("data", {}).get("status") == "completed" 
                              or "completed" in log.get("message", "").lower()
                              or "successfully" in log.get("message", "").lower())]
        if final_progress:
            # Get the most recent completion progress log
            summary_logs.append(final_progress[-1])
        
        # Sort by timestamp to maintain order
        summary_logs.sort(key=lambda x: x.get("timestamp", ""))
        return summary_logs if summary_logs else []
    
    # For failed: return error logs only (execution_duration is in execution_summary)
    elif job_status == 'failed':
        error_logs = []
        # Include error logs
        error_events = [log for log in all_logs if log.get("event") == "error"]
        error_logs.extend(error_events)
        
        # Note: execution_duration is now in execution_summary, not in logs
        
        # Sort by timestamp
        error_logs.sort(key=lambda x: x.get("timestamp", ""))
        return error_logs if error_logs else []
    
    # For unknown status: return all logs (fallback)
    return all_logs


def get_job_status_from_db(db: Session, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Get job status from database only (no in-memory state).
    
    Args:
        db: Database session
        job_id: Job identifier
    
    Returns:
        Job status dictionary or None if not found
    """
    try:
        _ensure_job_tables(db)
        
        select_query = text("""
            SELECT 
                job_id,
                user_id,
                status,
                schedule_time,
                started_at,
                completed_at,
                error_message,
                statistics_summary,
                created_at,
                updated_at
            FROM operations.planning_jobs
            WHERE job_id = :job_id
        """)
        result = db.execute(select_query, {"job_id": job_id}).fetchone()
        
        if not result:
            return None
        
        # Convert timestamps to IST for API response
        def convert_to_ist(dt, assume_ist=False):
            """Convert datetime to IST for API response.
            
            Args:
                dt: Datetime to convert
                assume_ist: If True and dt is naive, assume it's IST. Otherwise assume UTC.
            """
            if dt is None:
                return None
            # If datetime is naive, assume timezone based on assume_ist flag
            if dt.tzinfo is None:
                if assume_ist:
                    dt = IST.localize(dt)
                else:
                    dt = UTC.localize(dt)
            # Convert to IST
            return dt.astimezone(IST)
        
        # Convert to dictionary, converting all timestamps to IST
        # schedule_time is stored as IST, so if naive, assume IST
        schedule_time_ist = convert_to_ist(result[3], assume_ist=True)
        # Other timestamps (started_at, completed_at, created_at, updated_at) are from CURRENT_TIMESTAMP
        # which is typically UTC, so if naive, assume UTC
        started_at_ist = convert_to_ist(result[4], assume_ist=False)
        completed_at_ist = convert_to_ist(result[5], assume_ist=False)
        created_at_ist = convert_to_ist(result[8], assume_ist=False)
        updated_at_ist = convert_to_ist(result[9], assume_ist=False)
        
        # Parse statistics_summary safely - JSONB is already parsed by PostgreSQL/SQLAlchemy
        statistics_summary = None
        if result[7]:
            try:
                # JSONB columns are already parsed by SQLAlchemy, so check type first
                if isinstance(result[7], (dict, list)):
                    statistics_summary = result[7]
                elif isinstance(result[7], str):
                    # If it's a string, try to parse it
                    statistics_summary = json.loads(result[7])
                else:
                    # Fallback: try to convert to dict/list
                    statistics_summary = result[7]
            except (json.JSONDecodeError, TypeError, ValueError) as parse_error:
                logger.warning(f"Error parsing statistics_summary for job {job_id}: {str(parse_error)}")
                statistics_summary = None
        
        # Map internal "pending" status to "scheduled" for UI (response-layer only)
        internal_status = result[2] if result[2] else "unknown"
        display_status = "scheduled" if internal_status == "pending" else internal_status
        
        # Build job_status with defensive handling for all fields
        job_status = {
            "job_id": result[0] if result[0] else None,
            "user_id": result[1] if result[1] else None,
            "status": display_status,  # Use mapped status for UI
            "schedule_time": schedule_time_ist.isoformat() if schedule_time_ist else None,
            "started_at": started_at_ist.isoformat() if started_at_ist else None,
            "completed_at": completed_at_ist.isoformat() if completed_at_ist else None,
            "error_message": result[6] if result[6] else None,
            "statistics_summary": statistics_summary,
            "created_at": created_at_ist.isoformat() if created_at_ist else None,
            "updated_at": updated_at_ist.isoformat() if updated_at_ist else None,
            "logs": []  # Initialize logs as empty list, will be populated below
        }
        
        # Add logs from planning_job_logs table (safely handle missing table)
        try:
            # Ensure logs table exists before querying
            _ensure_job_logs_table(db)
            
            logs_query = text("""
                SELECT timestamp, level, event, message, data
                FROM operations.planning_job_logs
                WHERE job_id = :job_id
                ORDER BY timestamp ASC
            """)
            logs_result = db.execute(logs_query, {"job_id": job_id}).fetchall()
            
            all_logs = []
            for row in logs_result:
                try:
                    # Convert log timestamp to IST
                    # Log timestamps are stored as IST (from user_logging_service), so if naive, assume IST
                    log_timestamp_ist = convert_to_ist(row[0], assume_ist=True) if row[0] else None
                    
                    # Parse log data safely - JSONB is already parsed by PostgreSQL/SQLAlchemy
                    log_data = {}
                    if row[4]:
                        try:
                            if isinstance(row[4], dict):
                                log_data = row[4]
                            elif isinstance(row[4], str):
                                log_data = json.loads(row[4])
                            else:
                                log_data = row[4] if isinstance(row[4], (dict, list)) else {}
                        except (json.JSONDecodeError, TypeError, ValueError):
                            log_data = {}
                    
                    all_logs.append({
                        "timestamp": log_timestamp_ist.isoformat() if log_timestamp_ist else None,
                        "level": row[1] if row[1] else "INFO",
                        "event": row[2] if row[2] else None,
                        "message": row[3] if row[3] else None,
                        "data": log_data
                    })
                except Exception as row_error:
                    # Skip invalid log rows but continue processing
                    logger.warning(f"Error processing log row for job {job_id}: {str(row_error)}")
                    continue
            
            # Filter logs based on job status (filter at response level, don't delete from DB)
            # Use internal_status for filtering (not display_status)
            filtered_logs = _filter_logs_by_status(all_logs, internal_status)
            job_status["logs"] = filtered_logs
            
            # For completed/failed jobs, create execution_summary from duration log
            if display_status in ["completed", "failed"]:
                execution_summary = _build_execution_summary(all_logs, internal_status)
                if execution_summary:
                    job_status["execution_summary"] = execution_summary
        except Exception as logs_error:
            # Log error but don't fail the entire request - logs are optional
            logger.warning(f"Error retrieving logs for job {job_id}: {str(logs_error)}")
            job_status["logs"] = []  # Ensure logs is always a list
        
        return job_status
        
    except Exception as e:
        logger.error(f"Error getting job status {job_id}: {str(e)}", exc_info=True)
        
        # Try to get at least basic job info even if there's an error
        # This prevents "Job not found" errors due to parsing issues
        try:
            # Retry with minimal query to get basic job info
            minimal_query = text("""
                SELECT job_id, user_id, status, schedule_time, started_at, completed_at, 
                       error_message, statistics_summary, created_at, updated_at
                FROM operations.planning_jobs
                WHERE job_id = :job_id
            """)
            minimal_result = db.execute(minimal_query, {"job_id": job_id}).fetchone()
            
            if minimal_result:
                # Build minimal job_status with safe defaults
                try:
                    schedule_time_ist = convert_to_ist(minimal_result[3], assume_ist=True) if minimal_result[3] else None
                    started_at_ist = convert_to_ist(minimal_result[4], assume_ist=False) if minimal_result[4] else None
                    completed_at_ist = convert_to_ist(minimal_result[5], assume_ist=False) if minimal_result[5] else None
                    created_at_ist = convert_to_ist(minimal_result[8], assume_ist=False) if minimal_result[8] else None
                    updated_at_ist = convert_to_ist(minimal_result[9], assume_ist=False) if minimal_result[9] else None
                except Exception as time_error:
                    logger.warning(f"Error converting timestamps for job {job_id}: {str(time_error)}")
                    schedule_time_ist = started_at_ist = completed_at_ist = created_at_ist = updated_at_ist = None
                
                # Parse statistics_summary safely
                statistics_summary = None
                if minimal_result[7]:
                    try:
                        if isinstance(minimal_result[7], (dict, list)):
                            statistics_summary = minimal_result[7]
                        elif isinstance(minimal_result[7], str):
                            statistics_summary = json.loads(minimal_result[7])
                    except Exception:
                        statistics_summary = None
                
                # Map internal "pending" status to "scheduled" for UI
                minimal_internal_status = minimal_result[2] or "unknown"
                minimal_display_status = "scheduled" if minimal_internal_status == "pending" else minimal_internal_status
                
                # Return minimal job_status - always return something if job exists
                return {
                    "job_id": minimal_result[0] or job_id,
                    "user_id": minimal_result[1] or None,
                    "status": minimal_display_status,  # Use mapped status
                    "schedule_time": schedule_time_ist.isoformat() if schedule_time_ist else None,
                    "started_at": started_at_ist.isoformat() if started_at_ist else None,
                    "completed_at": completed_at_ist.isoformat() if completed_at_ist else None,
                    "error_message": minimal_result[6] or None,
                    "statistics_summary": statistics_summary,
                    "created_at": created_at_ist.isoformat() if created_at_ist else None,
                    "updated_at": updated_at_ist.isoformat() if updated_at_ist else None,
                    "logs": []  # Empty logs on error
                }
        except Exception as retry_error:
            logger.error(f"Failed to retrieve minimal job info for {job_id}: {str(retry_error)}")
        
        # Only return None if job truly doesn't exist, not due to parsing errors
        return None


def load_pending_jobs(db: Session):
    """
    Load pending jobs from database and reschedule them.
    This should be called on application startup to make the scheduler restart-safe.
    
    Args:
        db: Database session
    """
    try:
        _ensure_job_tables(db)
        
        # Get all pending or queued jobs with future schedule_time
        select_query = text("""
            SELECT job_id, schedule_time
            FROM operations.planning_jobs
            WHERE status IN ('pending', 'queued')
            AND schedule_time > CURRENT_TIMESTAMP
        """)
        results = db.execute(select_query).fetchall()
        
        scheduler = get_scheduler()
        rescheduled_count = 0
        
        for row in results:
            job_id, schedule_time = row
            try:
                # Ensure schedule_time is timezone-aware in IST
                if schedule_time.tzinfo is None:
                    # If naive, assume it's IST (from DB)
                    schedule_time = IST.localize(schedule_time)
                else:
                    # Convert to IST if it's in a different timezone
                    schedule_time = schedule_time.astimezone(IST)
                
                # Convert IST to UTC for APScheduler
                schedule_time_utc = schedule_time.astimezone(UTC)
                
                # Reschedule the job (APScheduler uses UTC internally)
                scheduler.add_job(
                    func=_execute_scheduled_job,
                    trigger=DateTrigger(run_date=schedule_time_utc),
                    id=job_id,
                    args=[job_id],
                    replace_existing=True
                )
                rescheduled_count += 1
                logger.info(f"Rescheduled pending job {job_id} for {schedule_time} IST ({schedule_time_utc} UTC)")
            except Exception as e:
                logger.error(f"Error rescheduling job {job_id}: {str(e)}")
        
        logger.info(f"Rescheduled {rescheduled_count} pending jobs on startup")
        
    except Exception as e:
        logger.error(f"Error loading pending jobs: {str(e)}")


def initialize_scheduler(db: Session):
    """
    Initialize the scheduler and load pending jobs.
    Call this on application startup.
    
    Ensures both planning_jobs and planning_job_logs tables exist.
    
    Args:
        db: Database session
    """
    try:
        # Ensure all required tables exist (including logs table)
        _ensure_job_tables(db)
        logger.info("Scheduler tables verified/created successfully")
    except Exception as e:
        logger.error(f"Error ensuring scheduler tables: {str(e)}")
        # Continue anyway - tables will be created on first use
    
    get_scheduler()  # Start scheduler
    load_pending_jobs(db)  # Load pending jobs from DB
