from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from core.db import get_db
import re
import pandas as pd
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["planningmethods"])


def _extract_year_range(plan_revision_version: str, season_id: str) -> str:
    """
    Extract year range (YYYY-YYYY) from plan_revision_version or season_id.
    
    Args:
        plan_revision_version: Plan version string (e.g., "v1.0-Maximized-Productivity")
        season_id: Season ID (e.g., "RABI_24_25")
        
    Returns:
        Year range string in format "YYYY-YYYY" (e.g., "2024-2025")
    """
    # Try to extract from season_id first (e.g., "RABI_24_25" -> "2024-2025")
    season_match = re.search(r'(\d{2})[_-](\d{2})', season_id)
    if season_match:
        year1 = int(season_match.group(1))
        year2 = int(season_match.group(2))
        # Handle 2-digit years: assume 20xx format
        full_year1 = 2000 + year1 if year1 < 100 else year1
        full_year2 = 2000 + year2 if year2 < 100 else year2
        return f"{full_year1}-{full_year2}"
    
    # Try to extract from plan_revision_version (e.g., "v1.0-Maximized-Productivity-2024-2025")
    version_match = re.search(r'(\d{4})[_-](\d{4})', plan_revision_version)
    if version_match:
        return f"{version_match.group(1)}-{version_match.group(2)}"
    
    # Try to extract single year and construct range
    single_year_match = re.search(r'(\d{4})', plan_revision_version)
    if single_year_match:
        year = int(single_year_match.group(1))
        return f"{year}-{year + 1}"
    
    # Fallback: use current year
    from datetime import datetime
    current_year = datetime.now().year
    return f"{current_year}-{current_year + 1}"


@router.get("/dropdown", response_model=Dict[str, Any])
async def get_planning_methods_dropdown(
    plan_revision_version: Optional[str] = Query(default=None),
    season_id: Optional[str] = Query(default=None),
    crop_id: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    variety_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    Cascading dropdown for Planning Methods → Display screen.
    Hierarchy: plan_revision_version → season → crop → state → variety
    
    1. No params → return distinct plan_revision_version (with "All" option)
    2. plan_revision_version → return distinct season_id + season_name (NO "All" option)
    3. plan_revision_version + season_id → return distinct crop_id + crop_name (NO "All" option)
    4. plan_revision_version + season_id + crop_id → return distinct state from operations.targets (with "All" option)
    5. plan_revision_version + season_id + crop_id + state → return distinct variety_id + variety_name from supply_chain_planning (NO targets)
    6. plan_revision_version + season_id + crop_id + state + variety_id → return ONLY selected variety's target from operations.targets as single object
    """
    try:
        result = {}

        if not plan_revision_version and not season_id and not crop_id and not state:
            # Step 1: Return static plan_revision_version list (response-level change only)
            result["plan_revision_version"] = [
                {"v3.0-Min-Max": "v3.0-Min-Max"},
                {"v2.0-Minimized-Cost": "v2.0-Minimized-Cost"},
                {"v1.0-Maximized-Productivity": "v1.0-Maximized-Productivity"}
            ]

        elif plan_revision_version and not season_id:
            # Step 2: Return distinct season_id + season_name for given plan_revision_version
            # Handle "All" for plan_revision_version (case-insensitive)
            # Match season (text) with season_name to get season_id
            plan_version_normalized = str(plan_revision_version).strip().lower() if plan_revision_version else ""
            
            if plan_version_normalized == "all":
                query = text("""
                    SELECT DISTINCT s.season_id, s.season_name
                    FROM operations.supply_chain_planning scp
                    INNER JOIN operations.seasons s ON TRIM(scp.season) = TRIM(s.season_name)
                    WHERE scp.season IS NOT NULL 
                    AND scp.season != ''
                    AND TRIM(scp.season) != ''
                    AND s.season_id IS NOT NULL 
                    AND s.season_name IS NOT NULL 
                    AND s.season_name != ''
                    AND TRIM(s.season_name) != ''
                    ORDER BY s.season_name
                """)
                rows = db.execute(query).fetchall()
            else:
                query = text("""
                    SELECT DISTINCT s.season_id, s.season_name
                    FROM operations.supply_chain_planning scp
                    INNER JOIN operations.seasons s ON TRIM(scp.season) = TRIM(s.season_name)
                    WHERE scp.plan_revision_version = :version
                    AND scp.season IS NOT NULL 
                    AND scp.season != ''
                    AND TRIM(scp.season) != ''
                    AND s.season_id IS NOT NULL 
                    AND s.season_name IS NOT NULL 
                    AND s.season_name != ''
                    AND TRIM(s.season_name) != ''
                    ORDER BY s.season_name
                """)
                rows = db.execute(query, {"version": plan_revision_version}).fetchall()
            
            season_list = [{str(r[0]): str(r[1])} for r in rows if r[0] and r[1]]
            # No "All" option for season
            result["season_id"] = season_list

        elif plan_revision_version and season_id and not crop_id:
            # Step 3: Return distinct crop_id + crop_name for given plan_revision_version + season_id
            # Match season (text) with season_name to filter, then match crop (text) with crop_name to get crop_id
            plan_version_normalized = str(plan_revision_version).strip().lower() if plan_revision_version else ""
            
            conditions = []
            params = {}
            
            if plan_version_normalized != "all":
                conditions.append("scp.plan_revision_version = :version")
                params["version"] = plan_revision_version
            
            # Season is required (no "All" option)
            conditions.append("s.season_id = :season_id")
            params["season_id"] = season_id
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query = text(f"""
                SELECT DISTINCT c.crop_id, c.crop_name
                FROM operations.supply_chain_planning scp
                INNER JOIN operations.seasons s ON TRIM(scp.season) = TRIM(s.season_name)
                INNER JOIN operations.crops c ON TRIM(scp.crop) = TRIM(c.crop_name)
                WHERE {where_clause}
                AND scp.crop IS NOT NULL 
                AND scp.crop != ''
                AND TRIM(scp.crop) != ''
                AND c.crop_id IS NOT NULL 
                AND c.crop_name IS NOT NULL 
                AND c.crop_name != ''
                AND TRIM(c.crop_name) != ''
                ORDER BY c.crop_name
            """)
            rows = db.execute(query, params).fetchall()
            
            crop_list = [{str(r[0]): str(r[1])} for r in rows if r[0] and r[1]]
            # No "All" option for crop
            result["crop_id"] = crop_list

        elif plan_revision_version and season_id and crop_id and not state:
            # Step 4: Return distinct state from operations.targets filtered by season_id + crop_id
            # Extract plan_year range (YYYY-YYYY) from plan_revision_version or season_id
            plan_year_range = _extract_year_range(plan_revision_version, season_id)
            
            conditions = []
            params = {}
            
            # Filter by plan_year_range (VARCHAR format: 'YYYY-YYYY')
            conditions.append("t.plan_year = :plan_year_range")
            params["plan_year_range"] = plan_year_range
            
            # Season and crop are required (no "All" option)
            conditions.append("t.season_id = :season_id")
            params["season_id"] = season_id
            
            conditions.append("t.crop_id = :crop_id")
            params["crop_id"] = crop_id
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query = text(f"""
                SELECT DISTINCT t.state
                FROM operations.targets t
                WHERE {where_clause}
                AND t.state IS NOT NULL 
                AND t.state != ''
                AND TRIM(t.state) != ''
                AND t.target_kgs IS NOT NULL
                AND t.target_kgs > 0
                ORDER BY t.state
            """)
            rows = db.execute(query, params).fetchall()
            
            state_list = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
            # Add "All" as the first option
            result["state"] = ["All"] + state_list

        elif plan_revision_version and season_id and crop_id and state and variety_id:
            # Step 6: Return target(s) from operations.targets table
            # Handle "All" for both state and variety_id
            variety_normalized = str(variety_id).strip().lower() if variety_id else ""
            state_normalized = str(state).strip().lower() if state else ""
            
            # Extract plan_year range (YYYY-YYYY) from plan_revision_version or season_id
            plan_year_range = _extract_year_range(plan_revision_version, season_id)
            
            conditions = []
            params = {}
            
            # Filter by plan_year_range (VARCHAR format: 'YYYY-YYYY')
            conditions.append("t.plan_year = :plan_year_range")
            params["plan_year_range"] = plan_year_range
            
            # Season and crop are required (no "All" option)
            conditions.append("t.season_id = :season_id")
            params["season_id"] = season_id
            
            conditions.append("t.crop_id = :crop_id")
            params["crop_id"] = crop_id
            
            # Filter by state if not "All"
            if state_normalized != "all":
                conditions.append("t.state = :state")
                params["state"] = state
            
            # Filter by variety_id if not "All"
            if variety_normalized != "all":
                conditions.append("t.variety_id = :variety_id")
                params["variety_id"] = variety_id
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query = text(f"""
                SELECT 
                    t.state,
                    t.variety_id,
                    v.variety_name,
                    t.target_kgs as quantity_kgs
                FROM operations.targets t
                INNER JOIN operations.varieties v ON t.variety_id = v.variety_id
                WHERE {where_clause}
                AND t.state IS NOT NULL 
                AND t.state != ''
                AND t.variety_id IS NOT NULL 
                AND t.target_kgs IS NOT NULL 
                AND t.target_kgs > 0
                ORDER BY t.state, v.variety_name
            """)
            rows = db.execute(query, params).fetchall()
            
            if not rows:
                raise HTTPException(
                    status_code=404,
                    detail=f"No target found for the specified filters (state: {state}, variety_id: {variety_id})"
                )
            
            # Always return targets as a list (response-level change only)
            targets_list = []
            for row in rows:
                state_name = str(row[0]).strip() if row[0] else None
                variety_id_val = str(row[1]).strip() if row[1] else None
                variety_name = str(row[2]).strip() if row[2] else None
                quantity_kgs = float(row[3]) if row[3] else 0.0
                quantity_mt = quantity_kgs / 1000.0
                
                targets_list.append({
                    "state": state_name,
                    "variety_id": variety_id_val,
                    "variety_name": variety_name,
                    "quantity_kgs": quantity_kgs,
                    "quantity_mt": quantity_mt,
                    "editable": True  # Add editable flag
                })
            result["targets"] = targets_list

        elif plan_revision_version and season_id and crop_id and state:
            # Step 5: Return distinct variety_id + variety_name from operations.targets table
            # Do NOT return targets when only state is selected
            state_normalized = str(state).strip().lower() if state else ""
            
            # Extract plan_year range (YYYY-YYYY) from plan_revision_version or season_id
            plan_year_range = _extract_year_range(plan_revision_version, season_id)
            
            conditions = []
            params = {}
            
            # Filter by plan_year_range (VARCHAR format: 'YYYY-YYYY')
            conditions.append("t.plan_year = :plan_year_range")
            params["plan_year_range"] = plan_year_range
            
            # Season and crop are required (no "All" option)
            conditions.append("t.season_id = :season_id")
            params["season_id"] = season_id
            
            conditions.append("t.crop_id = :crop_id")
            params["crop_id"] = crop_id
            
            # Filter by state if not "All"
            if state_normalized != "all":
                conditions.append("t.state = :state")
                params["state"] = state
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query = text(f"""
                SELECT DISTINCT 
                    t.variety_id,
                    v.variety_name
                FROM operations.targets t
                INNER JOIN operations.varieties v ON t.variety_id = v.variety_id
                WHERE {where_clause}
                AND t.variety_id IS NOT NULL 
                AND t.variety_id != ''
                AND v.variety_name IS NOT NULL 
                AND v.variety_name != ''
                AND t.target_kgs IS NOT NULL 
                AND t.target_kgs > 0
                ORDER BY v.variety_name
            """)
            rows = db.execute(query, params).fetchall()
            
            variety_list = [{str(r[0]): str(r[1])} for r in rows if r[0] and r[1]]
            
            # Add "All" as the first option for varieties
            # Do NOT return targets - only return variety list
            result["variety_id"] = [{"All": "All"}] + variety_list

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter balanced_optimization.")

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching planning methods dropdown options: {str(e)}")


# Request/Response Models
class PlanningMethodRequest(BaseModel):
    methods: str  # "Maximization", "Minimization", "Min-Max"
    criteria: str  # "Productivity", "Cost", etc.
    plan_revision_version: str
    season_id: str
    crop_id: str
    state: Optional[str] = None
    variety_id: Optional[str] = None
    min_allocation: Optional[float] = None
    max_allocation: Optional[float] = None


# New request models for the updated payload structure
class TargetItem(BaseModel):
    """Individual target item in the targets array. Supports multiple states and varieties."""
    state: str
    variety_id: str
    quantity: float  # Quantity in Kgs


class CriteriaItem(BaseModel):
    id: str  # "PRODUCTIVITY", "COST", etc.
    min_allocation: float
    max_allocation: float


class ConstraintItem(BaseModel):
    id: str  # "TARGET_BUDGET", "TARGET_COST", etc.
    value: float




@router.get("/methods", response_model=Dict[str, Any])
async def get_planning_methods(
    method_algorithm: Optional[str] = Query(default=None, description="Optional: Get specific method with criteria")
):
    """
    Get available planning method algorithms.
    - No method param → return list of all methods
    - method param provided → return that method with its criteria
    """
    all_methods = [
        {
            "id": "Maximization",
            "name": "Maximization",
            "description": "Maximize productivity by allocating to highest productivity locations first",
            "service_file": "supply_chain_planning_service.py"
        },
        {
            "id": "Minimization",
            "name": "Minimization",
            "description": "Minimize cost by allocating to lowest cost locations first",
            "service_file": "minimization_planning_service.py"
        },
        {
            "id": "Min-Max",
            "name": "Min-Max",
            "description": "balanced_optimization of productivity and cost optimization using linear programming",
            "service_file": "Linear_programming.py"
        }
    ]
    
    default_constraints = [
    {
        "id": "Target_Budget",
        "label": "Target Budget",
        "value": 0
    }
]
    
    # If no method specified, return all methods in new format
    if not method_algorithm:
        return {
            "method_algorithm": [
                {"minimization": "Minimization"},
                {"maximization": "Maximization"},
                {"min_max": "Min Max"}
            ]
        }
    
    # If method specified, return that method with its criteria in new format
    method_lower = method_algorithm.strip().lower()
    
    if method_lower == "maximization":
        return {
            "method_algorithm": "maximization",
            "criteria": [
                {
                    "id": "productivity",
                    "label": "Productivity",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": True
                },
                {
                    "id": "cost",
                    "label": "Cost",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": False
                }
            ],
            "constraints": default_constraints
        }
    elif method_lower == "minimization":
        return {
            "method_algorithm": "minimization",
            "criteria": [
                {
                    "id": "cost",
                    "label": "Cost",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": True
                },
                {
                    "id": "productivity",
                    "label": "Productivity",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": False
                }
            ],
            "constraints": default_constraints
        }
    elif method_lower in ["min-max", "min_max", "minmax"]:
        return {
            "method_algorithm": "min_max",
            "criteria": [
                {
                    "id": "productivity",
                    "label": "Productivity",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": False
                },
                {
                    "id": "cost",
                    "label": "Cost",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": False
                },
                {
                    "id": "balanced_optimization",
                    "label": "Balanced_optimization",
                    "min_allocation": 0,
                    "max_allocation": 0,
                    "editable": True
                }
            ],
            "constraints": default_constraints
        }
    else:
        raise HTTPException(status_code=400, detail=f"Invalid method: {method_algorithm}. Must be Maximization, Minimization, or Min-Max")


def _persist_plan_allocation(
    result_df: pd.DataFrame,
    plan_revision_version: str,
    season_id: str,
    crop_id: str,
    db: Session
) -> int:
    """
    Persist raw allocation data to operations.plan_allocation table.
    One row per allocated village/state/variety.
    
    Returns:
        Number of rows inserted
    """
    if not isinstance(result_df, pd.DataFrame) or result_df.empty:
        return 0
    
    try:
        # Get season and crop names
        season_name = season_id
        crop_name = crop_id
        
        season_query = text("""
            SELECT season_name 
            FROM operations.seasons 
            WHERE season_id = :season_id
            LIMIT 1
        """)
        season_result = db.execute(season_query, {"season_id": season_id}).fetchone()
        if season_result and season_result[0]:
            season_name = str(season_result[0]).strip()
        
        crop_query = text("""
            SELECT crop_name 
            FROM operations.crops 
            WHERE crop_id = :crop_id
            LIMIT 1
        """)
        crop_result = db.execute(crop_query, {"crop_id": crop_id}).fetchone()
        if crop_result and crop_result[0]:
            crop_name = str(crop_result[0]).strip()
        
        # Check if table exists and create if not
        try:
            check_table_query = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'operations' 
                    AND table_name = 'plan_allocation'
                )
            """)
            table_exists = db.execute(check_table_query).scalar()
            
            if not table_exists:
                create_table_query = text("""
                    CREATE TABLE operations.plan_allocation (
                        id BIGSERIAL PRIMARY KEY,
                        plan_revision_version VARCHAR(100),
                        season_id VARCHAR(50),
                        season VARCHAR(50),
                        crop_id VARCHAR(50),
                        crop VARCHAR(100),
                        state VARCHAR(100),
                        village VARCHAR(100),
                        variety VARCHAR(100),
                        allocated_acres NUMERIC(10, 2),
                        planned_production NUMERIC(10, 2),
                        estimated_cost_per_kg NUMERIC(10, 2),
                        estimated_production_cost NUMERIC(12, 2),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute(create_table_query)
                db.commit()
                logger.info("Created operations.plan_allocation table")
        except Exception as e:
            logger.warning(f"Error checking/creating plan_allocation table: {str(e)}")
            db.rollback()
            # Continue - table might already exist
        
        # Delete existing records for this plan version to avoid duplicates
        delete_query = text("""
            DELETE FROM operations.plan_allocation 
            WHERE plan_revision_version = :plan_revision_version
            AND season_id = :season_id
            AND crop_id = :crop_id
        """)
        db.execute(delete_query, {
            "plan_revision_version": plan_revision_version,
            "season_id": season_id,
            "crop_id": crop_id
        })
        
        # Prepare data for insertion using bulk insert for better performance
        rows_to_insert = []
        for _, row in result_df.iterrows():
            # Get values from DataFrame
            state = str(row.get('state', '')) if pd.notna(row.get('state')) else None
            village = str(row.get('village', '')) if pd.notna(row.get('village')) else None
            variety = str(row.get('variety', '')) if pd.notna(row.get('variety')) else None
            allocated_acres = float(row.get('allocated_acres', 0)) if pd.notna(row.get('allocated_acres')) else 0.0
            planned_production = float(row.get('planned_production', 0)) if pd.notna(row.get('planned_production')) else 0.0
            estimated_cost_per_kg = float(row.get('estimated_cost_per_kg', 0)) if pd.notna(row.get('estimated_cost_per_kg')) else 0.0
            estimated_production_cost = float(row.get('estimated_production_cost', 0)) if pd.notna(row.get('estimated_production_cost')) else 0.0
            
            # Skip rows with zero allocation
            if allocated_acres <= 0 and planned_production <= 0:
                continue
            
            rows_to_insert.append({
                "plan_revision_version": plan_revision_version,
                "season_id": season_id,
                "season": season_name,
                "crop_id": crop_id,
                "crop": crop_name,
                "state": state,
                "village": village,
                "variety": variety,
                "allocated_acres": allocated_acres,
                "planned_production": planned_production,
                "estimated_cost_per_kg": estimated_cost_per_kg,
                "estimated_production_cost": estimated_production_cost
            })
        
        # Insert rows one by one (SQLAlchemy text() doesn't support bulk insert directly)
        if rows_to_insert:
            insert_query = text("""
                INSERT INTO operations.plan_allocation (
                    plan_revision_version, season_id, season, crop_id, crop,
                    state, village, variety, allocated_acres, planned_production,
                    estimated_cost_per_kg, estimated_production_cost
                ) VALUES (
                    :plan_revision_version, :season_id, :season, :crop_id, :crop,
                    :state, :village, :variety, :allocated_acres, :planned_production,
                    :estimated_cost_per_kg, :estimated_production_cost
                )
            """)
            
            for row_data in rows_to_insert:
                db.execute(insert_query, row_data)
            rows_inserted = len(rows_to_insert)
        else:
            rows_inserted = 0
            logger.warning("No rows to insert into operations.plan_allocation (all had zero allocation)")
        
        db.commit()
        logger.info(f"Persisted {rows_inserted} rows to operations.plan_allocation table")
        return rows_inserted
        
    except Exception as e:
        db.rollback()
        error_msg = str(e)
        logger.error(f"Error persisting plan allocation data: {error_msg}")
        logger.exception(e)  # Log full traceback
        print(f"ERROR persisting allocation: {error_msg}")
        raise Exception(f"Failed to persist allocation data: {error_msg}") from e


def _persist_statistics_summary(
    statistics_summary: List[Dict[str, Any]],
    plan_revision_version: str,
    season_id: str,
    crop_id: str,
    method_algorithm: str,
    db: Session
) -> int:
    """
    Persist statistics summary to operations.plan_statistics table.
    One row per state/variety balanced_optimization.
    
    Returns:
        Number of rows inserted
    """
    if not statistics_summary:
        return 0
    
    try:
        # Get season and crop names
        season_name = season_id
        crop_name = crop_id
        
        season_query = text("""
            SELECT season_name 
            FROM operations.seasons 
            WHERE season_id = :season_id
            LIMIT 1
        """)
        season_result = db.execute(season_query, {"season_id": season_id}).fetchone()
        if season_result and season_result[0]:
            season_name = str(season_result[0]).strip()
        
        crop_query = text("""
            SELECT crop_name 
            FROM operations.crops 
            WHERE crop_id = :crop_id
            LIMIT 1
        """)
        crop_result = db.execute(crop_query, {"crop_id": crop_id}).fetchone()
        if crop_result and crop_result[0]:
            crop_name = str(crop_result[0]).strip()
        
        # Check if table exists and create if not (match actual schema with crop_name)
        try:
            check_table_query = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'operations' 
                    AND table_name = 'plan_statistics'
                )
            """)
            table_exists = db.execute(check_table_query).scalar()
            
            if not table_exists:
                create_table_query = text("""
                    CREATE TABLE operations.plan_statistics (
                        id BIGSERIAL PRIMARY KEY,
                        plan_revision_version VARCHAR(100),
                        season_id VARCHAR(50),
                        season VARCHAR(50),
                        crop_id VARCHAR(50),
                        crop_name VARCHAR(100),
                        method_algorithm VARCHAR(50),
                        state VARCHAR(100),
                        variety VARCHAR(100),
                        target_kgs NUMERIC(14, 3),
                        target_mt NUMERIC(12, 2),
                        allocated_kgs NUMERIC(14, 3),
                        allocated_mt NUMERIC(12, 2),
                        allocated_acres NUMERIC(10, 2),
                        total_cost NUMERIC(12, 2),
                        count_of_allocated_villages INTEGER,
                        count_of_unallocated_villages INTEGER,
                        total_villages INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute(create_table_query)
                db.commit()
                logger.info("Created operations.plan_statistics table")
            else:
                # Table exists - check if crop_name column exists, add if not
                check_column_query = text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_schema = 'operations' 
                        AND table_name = 'plan_statistics'
                        AND column_name = 'crop_name'
                    )
                """)
                has_crop_name = db.execute(check_column_query).scalar()
                if not has_crop_name:
                    # Try to add crop_name column if it doesn't exist
                    try:
                        alter_table_query = text("""
                            ALTER TABLE operations.plan_statistics 
                            ADD COLUMN IF NOT EXISTS crop_name VARCHAR(100)
                        """)
                        db.execute(alter_table_query)
                        db.commit()
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error checking/creating plan_statistics table: {str(e)}")
            db.rollback()
            # Continue - table might already exist
        
        # Delete existing records for this plan version to avoid duplicates
        delete_query = text("""
            DELETE FROM operations.plan_statistics 
            WHERE plan_revision_version = :plan_revision_version
            AND season_id = :season_id
            AND crop_id = :crop_id
            AND method_algorithm = :method_algorithm
        """)
        db.execute(delete_query, {
            "plan_revision_version": plan_revision_version,
            "season_id": season_id,
            "crop_id": crop_id,
            "method_algorithm": method_algorithm
        })
        
        # Prepare data for insertion
        rows_inserted = 0
        for stat in statistics_summary:
            # Use crop_name column (as per actual schema)
            insert_query = text("""
                INSERT INTO operations.plan_statistics (
                    plan_revision_version, season_id, season, crop_id, crop_name, method_algorithm,
                    state, variety, target_kgs, target_mt, allocated_kgs, allocated_mt,
                    allocated_acres, total_cost, count_of_allocated_villages,
                    count_of_unallocated_villages, total_villages
                ) VALUES (
                    :plan_revision_version, :season_id, :season, :crop_id, :crop_name, :method_algorithm,
                    :state, :variety, :target_kgs, :target_mt, :allocated_kgs, :allocated_mt,
                    :allocated_acres, :total_cost, :count_of_allocated_villages,
                    :count_of_unallocated_villages, :total_villages
                )
            """)
            
            db.execute(insert_query, {
                "plan_revision_version": plan_revision_version,
                "season_id": season_id,
                "season": season_name,
                "crop_id": crop_id,
                "crop_name": crop_name,
                "method_algorithm": method_algorithm,
                "state": stat.get("state", ""),
                "variety": stat.get("variety", ""),
                "target_kgs": float(stat.get("target_kgs", 0)),
                "target_mt": float(stat.get("target_mt", 0)),
                "allocated_kgs": float(stat.get("allocated_kgs", 0)),
                "allocated_mt": float(stat.get("allocated_mt", 0)),
                "allocated_acres": float(stat.get("allocated_acres", 0)),
                "total_cost": float(stat.get("total_cost", 0)),
                "count_of_allocated_villages": int(stat.get("count_of_allocated_villages", 0)),
                "count_of_unallocated_villages": int(stat.get("count_of_unallocated_villages", 0)),
                "total_villages": int(stat.get("total_villages", 0))
            })
            rows_inserted += 1
        
        db.commit()
        logger.info(f"Persisted {rows_inserted} rows to operations.plan_statistics table")
        return rows_inserted
        
    except Exception as e:
        db.rollback()
        error_msg = str(e)
        logger.error(f"Error persisting statistics summary: {error_msg}")
        logger.exception(e)  # Log full traceback
        print(f"ERROR persisting statistics: {error_msg}")
        raise Exception(f"Failed to persist statistics: {error_msg}") from e


def _normalize_state_for_matching(state_name: str) -> str:
    """
    Normalize state name for matching, handling common variations.
    Matches the behavior of SupplyChainPlanner.normalize_state_name() to ensure
    district-level states (Karimnagar, Warangal) are normalized to Telangana.
    """
    if not state_name:
        return ''
    normalized = str(state_name).strip().lower()
    # Map common variations - MUST include district-to-state mappings
    state_mapping = {
        'chhattisgarh': 'chattissgarh',
        'chattisgarh': 'chattissgarh',
        'chattissgarh': 'chattissgarh',
        'odisha': 'odisha',
        'orissa': 'odisha',
        'karnataka': 'karnataka',
        'warangal': 'telangana',  # Warangal is a district in Telangana
        'karimnagar': 'telangana',  # Karimnagar is a district in Telangana
        'telangana': 'telangana'
    }
    return state_mapping.get(normalized, normalized)


def _generate_statistics_summary(
    result_df: pd.DataFrame,
    targets_list: List[Dict[str, Any]],
    db: Session = None
) -> List[Dict[str, Any]]:
    """
    Generate statistics summary grouped by state and variety.
    
    Returns:
        List of statistics dictionaries with columns:
        state, variety, target_kgs, target_mt, allocated_kgs, allocated_mt,
        allocated_acres, total_cost, count_of_allocated_villages,
        count_of_unallocated_villages, total_villages
    """
    # Create a mapping of state+variety_id to target quantities
    # Use normalized states for consistent matching
    target_map = {}
    variety_id_to_name = {}
    for target in targets_list:
        state = target.get('state', '')
        variety_id = target.get('variety_id', '')
        quantity_kgs = float(target.get('quantity', 0))
        # Normalize state for consistent matching
        normalized_state = _normalize_state_for_matching(state)
        key = (normalized_state.lower(), variety_id)
        target_map[key] = {
            'quantity_kgs': quantity_kgs,
            'state': state,  # Keep original state for display
            'variety_id': variety_id
        }
        variety_id_to_name[variety_id] = variety_id  # Default to variety_id if name not found
    
    # Get variety names from database if db session provided
    if db:
        try:
            variety_query = text("""
                SELECT variety_id, variety_name
                FROM operations.varieties
            """)
            variety_results = db.execute(variety_query).fetchall()
            for row in variety_results:
                vid = str(row[0]).strip() if row[0] else ''
                vname = str(row[1]).strip() if row[1] else ''
                if vid and vname:
                    variety_id_to_name[vid] = vname
        except Exception as e:
            logger.warning(f"Could not fetch variety names from database: {str(e)}")
    
    # Get variety names from result_df if available
    if isinstance(result_df, pd.DataFrame) and not result_df.empty:
        if 'variety' in result_df.columns and 'variety_id' in result_df.columns:
            for _, row in result_df.iterrows():
                variety_name = str(row.get('variety', '')) if pd.notna(row.get('variety')) else ''
                variety_id_val = str(row.get('variety_id', '')) if pd.notna(row.get('variety_id')) else ''
                if variety_name and variety_id_val:
                    variety_id_to_name[variety_id_val] = variety_name
    
    # Create a set of valid state+variety pairs from targets_list for filtering
    # Normalize states and match by variety_id or variety name
    valid_target_pairs = set()
    for target in targets_list:
        target_state = target.get('state', '').strip()
        target_variety_id = target.get('variety_id', '').strip()
        # Normalize state (handle districts like Karimnagar/Warangal -> Telangana)
        normalized_state = _normalize_state_for_matching(target_state)
        # Store both variety_id and potential variety name for matching
        target_variety_name = variety_id_to_name.get(target_variety_id, target_variety_id)
        valid_target_pairs.add((normalized_state.lower(), target_variety_id.lower()))
        if target_variety_name and target_variety_name != target_variety_id:
            valid_target_pairs.add((normalized_state.lower(), target_variety_name.strip().lower()))
    
    # Group by state and variety
    statistics = []
    processed_keys = set()
    
    # Process results from result_df if available
    if isinstance(result_df, pd.DataFrame) and not result_df.empty and 'state' in result_df.columns and 'variety' in result_df.columns:
        grouped = result_df.groupby(['state', 'variety'], dropna=False)
        
        for (state, variety), group_df in grouped:
            state_str = str(state) if pd.notna(state) else ''
            variety_str = str(variety) if pd.notna(variety) else ''
            
            # Normalize state for matching
            normalized_state_str = _normalize_state_for_matching(state_str)
            
            # Check if this state+variety pair is in targets_list
            # Match by normalized state and variety (by name or ID)
            is_valid_pair = False
            matched_target_key = None
            
            # Try to match by variety name
            variety_normalized = variety_str.strip().lower()
            if (normalized_state_str.lower(), variety_normalized) in valid_target_pairs:
                is_valid_pair = True
                matched_target_key = (normalized_state_str.lower(), variety_normalized)
            
            # Also try to match by variety_id if available in result_df
            if not is_valid_pair and 'variety_id' in group_df.columns:
                variety_id_series = group_df['variety_id'].dropna().unique()
                for vid in variety_id_series:
                    vid_str = str(vid).strip().lower()
                    if (normalized_state_str.lower(), vid_str) in valid_target_pairs:
                        is_valid_pair = True
                        matched_target_key = (normalized_state_str.lower(), vid_str)
                        break
            
            # Skip if this state+variety pair is not in targets_list
            if not is_valid_pair:
                logger.debug(
                    f"Skipping statistics for state='{state_str}', variety='{variety_str}' "
                    f"(not in request.targets)"
                )
                continue
            
            # Calculate allocated metrics
            # Use planned_production instead of adjusted_production_allocation for API display
            allocated_kgs = float(group_df['planned_production'].sum()) if 'planned_production' in group_df.columns else 0.0
            allocated_mt = allocated_kgs / 1000.0
            allocated_acres = float(group_df['allocated_acres'].sum()) if 'allocated_acres' in group_df.columns else 0.0
            total_cost = float(group_df['estimated_production_cost'].sum()) if 'estimated_production_cost' in group_df.columns else 0.0
            
            # Count allocated villages (unique villages with positive allocation)
            # Filter rows with positive allocation first
            allocated_mask = (
                (group_df.get('allocated_acres', pd.Series([0])) > 0) |
                (group_df.get('planned_production', pd.Series([0])) > 0)
            )
            allocated_rows = group_df[allocated_mask]
            
            # Count unique villages (not rows) - if village column exists, use it; otherwise count rows
            if 'village' in group_df.columns:
                # Count unique villages with allocations
                allocated_villages = allocated_rows['village'].dropna()
                allocated_villages = allocated_villages[allocated_villages != '']
                count_allocated_villages = int(allocated_villages.nunique()) if len(allocated_villages) > 0 else 0
                
                # Count total unique villages matching state+variety (ALL villages, not just available ones)
                # This matches the original planner behavior where all matching villages are counted
                total_villages_series = group_df['village'].dropna()
                total_villages_series = total_villages_series[total_villages_series != '']
                total_villages = int(total_villages_series.nunique()) if len(total_villages_series) > 0 else 0
            else:
                # Fallback: count rows if village column doesn't exist
                count_allocated_villages = int(allocated_mask.sum())
                total_villages = len(group_df)
            
            count_unallocated_villages = total_villages - count_allocated_villages
            
            # Get target from targets_list (try to match by state and variety)
            target_kgs = 0.0
            matched_key = None
            
            # Try to find matching target by normalized state and variety
            # Since we've already filtered to valid pairs, this should always find a match
            for (target_state_normalized, target_variety_id), target_info in target_map.items():
                if target_state_normalized == normalized_state_str.lower():
                    # Try to match variety by checking if variety_id matches or if variety name matches
                    variety_match = False
                    variety_name_from_id = variety_id_to_name.get(target_variety_id, target_variety_id)
                    
                    # Match if variety name from result matches variety name from database/variety_id
                    if variety_name_from_id.strip().lower() == variety_str.strip().lower():
                        variety_match = True
                    elif target_variety_id.strip().lower() in variety_str.strip().lower():
                        variety_match = True
                    
                    if variety_match:
                        target_kgs = target_info['quantity_kgs']
                        matched_key = (target_state_normalized, target_variety_id)
                        break
            
            # Use variety name from database if available
            final_variety_name = variety_str
            if matched_key and matched_key[1] in variety_id_to_name:
                final_variety_name = variety_id_to_name[matched_key[1]]
            
            # Count total allocation records (rows) for verification against database
            count_allocation_records = len(allocated_rows)
            total_records = len(group_df)
            
            # Log allocation counts for verification against database
            logger.info(
                f"Allocation counts for state='{state_str}', variety='{final_variety_name}': "
                f"allocated_villages={count_allocated_villages}, total_villages={total_villages}, "
                f"allocation_records={count_allocation_records}, total_records={total_records}"
            )
            
            statistics.append({
                "state": state_str,
                "variety": final_variety_name,
                "target_kgs": target_kgs,
                "target_mt": target_kgs / 1000.0,
                "allocated_kgs": allocated_kgs,
                "allocated_mt": allocated_mt,
                "allocated_acres": allocated_acres,
                "total_cost": total_cost,
                "count_of_allocated_villages": count_allocated_villages,
                "count_of_unallocated_villages": count_unallocated_villages,
                "total_villages": total_villages,
                "count_of_allocation_records": count_allocation_records  # For verification against DB
            })
            
            # Track this state+variety pair as processed (use normalized state for consistency)
            # This ensures we don't add duplicate zero-allocation rows later
            # Track by variety name (always available)
            processed_state_variety_key = (normalized_state_str.lower(), final_variety_name.strip().lower())
            processed_keys.add(processed_state_variety_key)
            
            # Also track by variety_id if available (for better matching)
            if matched_key:
                processed_keys.add(matched_key)  # This is (normalized_state, variety_id)
            
            # Also track by variety_id from result_df if available
            if 'variety_id' in group_df.columns:
                variety_id_series = group_df['variety_id'].dropna().unique()
                for vid in variety_id_series:
                    vid_str = str(vid).strip().lower()
                    processed_keys.add((normalized_state_str.lower(), vid_str))
    
    # Also include targets that had zero allocation (not found in results)
    # But ONLY if they don't already exist in statistics
    for target in targets_list:
        state = target.get('state', '')
        variety_id = target.get('variety_id', '')
        quantity_kgs = float(target.get('quantity', 0))
        
        # Normalize state for consistent matching
        normalized_target_state = _normalize_state_for_matching(state)
        target_variety_name = variety_id_to_name.get(variety_id, variety_id)
        
        # Create keys for checking (use normalized state)
        key_by_id = (normalized_target_state.lower(), variety_id.lower())
        key_by_name = (normalized_target_state.lower(), target_variety_name.strip().lower())
        
        # Skip if already processed (check both variety_id and variety name)
        if key_by_id in processed_keys or key_by_name in processed_keys:
            continue
        
        # Double-check: verify this state+variety pair doesn't exist in statistics
        # Use normalized states and exact variety matching
        found = False
        for stat in statistics:
            stat_state_normalized = _normalize_state_for_matching(stat.get('state', ''))
            stat_variety = stat.get('variety', '').strip().lower()
            
            # Match by normalized state
            if stat_state_normalized.lower() == normalized_target_state.lower():
                # Check if variety matches (exact match preferred, fallback to substring)
                # Exact match by variety name
                if stat_variety == target_variety_name.strip().lower():
                    found = True
                    break
                # Match by variety_id (if variety_id appears in stat variety name)
                elif variety_id.strip().lower() in stat_variety:
                    found = True
                    break
                # Match if stat variety name appears in target variety name (handles USRH-04 vs USRH-4)
                elif stat_variety in target_variety_name.strip().lower() or target_variety_name.strip().lower() in stat_variety:
                    found = True
                    break
        
        # Only add zero-allocation row if this state+variety pair doesn't exist
        if not found:
            # Get variety name if available
            variety_name = variety_id_to_name.get(variety_id, variety_id)
            
            # Log reason for zero allocation
            logger.warning(
                f"Zero allocation for target: state={state}, variety_id={variety_id}, variety={variety_name}, "
                f"target={quantity_kgs} kgs. Reason: No villages found or no allocation generated."
            )
            
            statistics.append({
                "state": state,
                "variety": variety_name,
                "target_kgs": quantity_kgs,
                "target_mt": quantity_kgs / 1000.0,
                "allocated_kgs": 0.0,
                "allocated_mt": 0.0,
                "allocated_acres": 0.0,
                "total_cost": 0.0,
                "count_of_allocated_villages": 0,
                "count_of_unallocated_villages": 0,
                "total_villages": 0,
                "allocation_reason": "No villages found or no allocation generated"
            })
    
    # Log summary of statistics
    total_targets = len(targets_list)
    targets_with_allocation = sum(1 for s in statistics if s.get('allocated_kgs', 0) > 0)
    targets_with_zero_allocation = total_targets - targets_with_allocation
    
    logger.info(
        f"Statistics summary: {total_targets} total targets, "
        f"{targets_with_allocation} with allocation, "
        f"{targets_with_zero_allocation} with zero allocation"
    )
    
    return statistics


# Scheduling endpoints
class PlanningScheduleRequest(BaseModel):
    """Request model for scheduling one-time planning execution."""
    user_id: str
    schedule_time: str  # ISO format datetime string
    plan_revision_version: str
    season_id: str
    crop_id: str
    targets: List[TargetItem]
    method_algorithm: str
    criteria: Optional[List[CriteriaItem]] = None
    constraints: Optional[List[ConstraintItem]] = None


@router.post("/schedule", response_model=Dict[str, Any])
async def schedule_planning_execution(
    request: PlanningScheduleRequest,
    db: Session = Depends(get_db)
):
    """
    Schedule a one-time planning execution at the specified time.
    
    This is the primary execution endpoint for UI integration.
    Use /job-status/{job_id} to track execution progress and results.
    
    Accepts planning payload:
    - user_id: User identifier for logging
    - schedule_time: When to execute (ISO format datetime string in IST)
    - plan_revision_version: Plan version
    - season_id: Season identifier
    - crop_id: Crop identifier
    - targets: List of target items (state, variety_id, quantity)
    - method_algorithm: Planning method (Maximization, Minimization, Min-Max)
    - criteria: Optional criteria configuration
    - constraints: Optional constraints
    
    Returns:
    - job_id: Unique identifier for the scheduled job
    - schedule_time: When the job will execute (IST timezone)
    - status: "scheduled" (mapped from internal "pending" status)
    """
    try:
        from planning_methods.scheduler_service import schedule_one_time_job
        from datetime import datetime
        import uuid
        
        # Parse schedule_time as IST (Asia/Kolkata)
        try:
            import pytz
            ist = pytz.timezone('Asia/Kolkata')
            
            # Parse the datetime string (assume it's in IST if no timezone specified)
            dt = datetime.fromisoformat(request.schedule_time.replace('Z', '+00:00'))
            
            # If datetime is naive, assume it's IST
            if dt.tzinfo is None:
                schedule_time = ist.localize(dt)
            else:
                # If timezone-aware, convert to IST
                schedule_time = dt.astimezone(ist)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid schedule_time format. Use ISO format (e.g., '2024-01-01T12:00:00')"
            )
        
        # Generate unique job ID
        job_id = f"planning_{request.user_id}_{uuid.uuid4().hex[:8]}"
        
        # Prepare request data for execution
        request_data = {
            "plan_revision_version": request.plan_revision_version,
            "season_id": request.season_id,
            "crop_id": request.crop_id,
            "targets": [{"state": t.state, "variety_id": t.variety_id, "quantity": t.quantity} for t in request.targets],
            "method_algorithm": request.method_algorithm,
            "criteria": [{"id": c.id, "min_allocation": c.min_allocation, "max_allocation": c.max_allocation} for c in (request.criteria or [])],
            "constraints": [{"id": c.id, "value": c.value} for c in (request.constraints or [])]
        }
        
        # Schedule the job (stores in DB and schedules in APScheduler)
        try:
            success = schedule_one_time_job(
                db=db,
                job_id=job_id,
                user_id=request.user_id,
                schedule_time=schedule_time,
                request_data=request_data
            )
            
            if not success:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to schedule job"
                )
        except ValueError as e:
            # Re-raise ValueError from schedule_one_time_job with more context
            raise HTTPException(
                status_code=500,
                detail=f"Failed to schedule job: {str(e)}"
            )
        
        logger.info(f"Scheduled planning job {job_id} for user {request.user_id} at {schedule_time} IST")
        
        return {
            "success": True,
            "job_id": job_id,
            "user_id": request.user_id,
            "schedule_time": schedule_time.isoformat(),
            "status": "scheduled",  # Map internal "pending" to "scheduled" for UI
            "message": f"Planning execution scheduled for {schedule_time.isoformat()} IST"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error scheduling planning execution: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error scheduling planning execution: {str(e)}"
        )


@router.get("/job-status/{job_id}", response_model=Dict[str, Any])
async def get_job_status(
    job_id: str,
    db: Session = Depends(get_db)
):
    """
    Get status of a scheduled planning job from database (UI-OPTIMIZED).
    
    Returns status-aware filtered logs optimized for UI display:
    - For pending/queued/running: Latest progress log only
    - For completed: Completion message + execution_duration only
    - For failed: Error logs + execution_duration only
    
    Historical/debug logs (job_start, input, routing, etc.) are excluded from response
    but remain in database for audit purposes.
    
    Returns:
    - job_id: Job identifier
    - status: "scheduled" (mapped from "pending"), "queued", "running", "completed", or "failed"
    - schedule_time: When job was scheduled (IST timezone)
    - started_at: When job started (if running/completed/failed, IST timezone)
    - completed_at: When job completed (if completed/failed, IST timezone)
    - error_message: Error details (if failed)
    - statistics_summary: Statistics summary (if completed)
    - execution_summary: Status + execution duration (if completed or failed)
    - logs: Status-aware filtered logs (not full history)
      - For scheduled/queued/running: Latest progress log only
      - For completed: Completion message only (duration in execution_summary)
      - For failed: Error logs only (duration in execution_summary)
    """
    try:
        from planning_methods.scheduler_service import get_job_status_from_db
        
        # Get job status from database only (no in-memory state)
        status = get_job_status_from_db(db, job_id)
        
        if not status:
            # Only return 404 if job truly doesn't exist, not due to parsing errors
            # Double-check by querying job_id directly
            try:
                check_query = text("""
                    SELECT job_id FROM operations.planning_jobs WHERE job_id = :job_id
                """)
                exists = db.execute(check_query, {"job_id": job_id}).fetchone()
                if not exists:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Job {job_id} not found"
                    )
                else:
                    # Job exists but parsing failed - return error with job_id
                    logger.error(f"Job {job_id} exists but status retrieval failed")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error retrieving job status for {job_id}. Job exists but status data could not be parsed."
                    )
            except HTTPException:
                raise
            except Exception as check_error:
                logger.error(f"Error checking job existence {job_id}: {str(check_error)}")
                raise HTTPException(
                    status_code=404,
                    detail=f"Job {job_id} not found"
                )
        
        return status
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status {job_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error getting job status: {str(e)}"
        )

