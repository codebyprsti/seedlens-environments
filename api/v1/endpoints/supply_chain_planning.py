from decimal import Decimal
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from typing import List, Optional, Dict, Any
from collections import OrderedDict
import psycopg2
import psycopg2.extras
from core.db import get_db, get_connection, release_connection
from models.schemas.base import SupplyChainPlanningResponse

router = APIRouter()

@router.get("/supply-chain-planning/summary", response_model=List[Dict[str, Any]])
async def get_supply_chain_planning_summary(
        db: Session = Depends(get_db),
        season: Optional[str] = Query(None),
        crop: Optional[str] = Query(None),
        variety: Optional[str] = Query(None),
        village: Optional[str] = Query(None),
        grower: Optional[str] = Query(None),
        plan_revision_version: Optional[str] = Query(None),
        limit: int = Query(100),
        offset: int = Query(0)
):
    """
    Get supply chain planning summary with aggregated data
    """
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        base_query = """
        SELECT 
            season,
            crop,
            variety,
            village,
            grower,
            plan_revision_version,
            SUM(net_acres_current) as total_net_acres,
            AVG(productivity) as avg_productivity,
            SUM(production_allocation) as total_production_allocation,
            SUM(availability_actual) as total_availability_actual,
            SUM(adjusted_production_allocation) as total_adjusted_production_allocation,
            AVG(estimated_cost_per_kg) as avg_estimated_cost_per_kg,
            SUM(estimated_production_cost) as total_estimated_production_cost,
            COUNT(*) as record_count,

            -- NEW FIELDS
            SUM(actual_received_qty) as total_actual_received_qty,
            SUM(packaged_qty) as total_packaged_qty,
            AVG(actual_productivity) as avg_actual_productivity,
            SUM(amount) as total_amount
        FROM operations.supply_chain_planning
        WHERE 1=1
        """

        conditions = []
        params = []

        if season:
            conditions.append("season ILIKE %s")
            params.append(f"%{season}%")
        if crop:
            conditions.append("crop ILIKE %s")
            params.append(f"%{crop}%")
        if variety:
            conditions.append("variety ILIKE %s")
            params.append(f"%{variety}%")
        if village:
            conditions.append("village ILIKE %s")
            params.append(f"%{village}%")
        if grower:
            conditions.append("grower ILIKE %s")
            params.append(f"%{grower}%")
        if plan_revision_version:
            conditions.append("plan_revision_version = %s")
            params.append(plan_revision_version)

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        base_query += """
        GROUP BY season, crop, variety, village, grower, plan_revision_version
        ORDER BY season, crop, variety, village, grower
        LIMIT %s OFFSET %s
        """

        params.extend([limit, offset])

        cur.execute(base_query, params)
        rows = cur.fetchall()

        results = []
        for row in rows:
            result = {
                "season": row["season"],
                "crop": row["crop"],
                "variety": row["variety"],
                "village": row["village"],
                "grower": row["grower"],
                "plan_revision_version": row["plan_revision_version"],
                "total_net_acres": float(row["total_net_acres"] or 0),
                "avg_productivity": round(float(row["avg_productivity"] or 0), 2),
                "total_production_allocation": float(row["total_production_allocation"] or 0),
                "total_availability_actual": float(row["total_availability_actual"] or 0),
                "total_adjusted_production_allocation": float(row["total_adjusted_production_allocation"] or 0),
                "avg_estimated_cost_per_kg": round(float(row["avg_estimated_cost_per_kg"] or 0), 2),
                "total_estimated_production_cost": float(row["total_estimated_production_cost"] or 0),
                "record_count": int(row["record_count"]),

                # ✅ New fields
                "actual_received_qty": float(row["total_actual_received_qty"] or 0),
                "packaged_qty": float(row["total_packaged_qty"] or 0),
                "actual_productivity": round(float(row["avg_actual_productivity"] or 0), 2),
                "amount": float(row["total_amount"] or 0),
            }
            results.append(result)

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching supply chain planning summary: {str(e)}")
    finally:
        release_connection(conn)

@router.get("/supply-chain-planning-data/full_planning_data")
def get_supply_chain_planning_full_data(
        category_id: int = Query(100007, description="Category ID for supply chain planning")
):
    """
    Get full supply chain planning data with column metadata
    Following the same pattern as your existing entity API
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. Get category details
        cur.execute("SELECT category_name FROM operations.categories WHERE category_id = %s", (category_id,))
        cat_row = cur.fetchone()
        if not cat_row:
            raise HTTPException(status_code=404, detail="Invalid category_id")

        # For supply chain planning, we'll use the table name directly
        table_name = "supply_chain_planning"

        # 2. Check if table exists
        cur.execute("""
            SELECT to_regclass(%s)
        """, (f"operations.{table_name}",))
        if not cur.fetchone()[0]:
            raise HTTPException(status_code=404, detail=f"Table 'supply_chain_planning' does not exist")

        # 3. Get column metadata for supply chain planning with custom ordering
        cur.execute("""
            SELECT db_column_name, display_name, type, group_name, is_visible, is_editable,
                   CASE 
                       WHEN db_column_name = 'plan_revision_version' THEN 1
                       WHEN db_column_name = 'season' THEN 2
                       WHEN db_column_name = 'crop' THEN 3
                       WHEN db_column_name = 'state' THEN 4
                       WHEN db_column_name = 'variety' THEN 5
                       WHEN db_column_name = 'village' THEN 6
                       WHEN db_column_name = 'mandal' THEN 7
                       WHEN db_column_name = 'district' THEN 8
                       WHEN db_column_name = 'grower' THEN 9
                       WHEN db_column_name = 'longitude' THEN 10
                       WHEN db_column_name = 'latitude' THEN 11
                       WHEN db_column_name = 'net_acres_current' THEN 12
                       WHEN db_column_name = 'productivity' THEN 13
                       WHEN db_column_name = 'production_allocation' THEN 14
                       WHEN db_column_name = 'actual_net_acres' THEN 15
                       WHEN db_column_name = 'adjusted_production_allocation' THEN 16
                       WHEN db_column_name = 'estimated_cost_per_kg' THEN 17
                       WHEN db_column_name = 'estimated_production_cost' THEN 18
                       WHEN db_column_name = 'actual_received_qty' THEN 19
                       WHEN db_column_name = 'actual_packaged_qty' THEN 20
                       WHEN db_column_name = 'actual_productivity' THEN 21
                       WHEN db_column_name = 'actual_amount' THEN 22
                       WHEN db_column_name = 'created_at' THEN 23
                       WHEN db_column_name = 'updated_at' THEN 24
                       ELSE 999
                   END as custom_order
            FROM operations.column_metadata
            WHERE category_id = %s
            ORDER BY custom_order, column_id
        """, (category_id,))

        metadata = cur.fetchall()
        if metadata:
            columns = [dict(col) for col in metadata]
            visible_columns = [col["db_column_name"] for col in columns if col.get("is_visible", True)]
        else:
            # If no metadata exists, create default metadata
            default_columns = [
                {"db_column_name": "id", "display_name": "ID", "type": "integer", "group_name": "Identification",
                 "is_visible": True},
                {"db_column_name": "plan_revision_version", "display_name": "Plan Version", "type": "string",
                 "group_name": "Planning", "is_visible": True},
                {"db_column_name": "season", "display_name": "Season", "type": "string", "group_name": "Basic Info",
                 "is_visible": True},
                {"db_column_name": "crop", "display_name": "Crop", "type": "string", "group_name": "Basic Info",
                 "is_visible": True},
                {"db_column_name": "variety", "display_name": "Variety", "type": "string", "group_name": "Basic Info",
                 "is_visible": True},
                {"db_column_name": "village", "display_name": "Village", "type": "string", "group_name": "Location",
                 "is_visible": True},
                {"db_column_name": "grower", "display_name": "Grower", "type": "string", "group_name": "Stakeholder",
                 "is_visible": True},
                {"db_column_name": "net_acres_current", "display_name": "Net Acres", "type": "decimal",
                 "group_name": "Production", "is_visible": True},
                {"db_column_name": "productivity", "display_name": "Productivity", "type": "decimal",
                 "group_name": "Production", "is_visible": True},
                {"db_column_name": "production_allocation", "display_name": "Production Allocation", "type": "decimal",
                 "group_name": "Allocation", "is_visible": True},
                {"db_column_name": "availability_actual", "display_name": "Actual Availability", "type": "decimal",
                 "group_name": "Allocation", "is_visible": True},
                {"db_column_name": "adjusted_production_allocation", "display_name": "Adjusted Production",
                 "type": "decimal", "group_name": "Allocation", "is_visible": True},
                {"db_column_name": "estimated_cost_per_kg", "display_name": "Cost per Kg", "type": "decimal",
                 "group_name": "Cost", "is_visible": True},
                {"db_column_name": "estimated_production_cost", "display_name": "Total Production Cost",
                 "type": "decimal", "group_name": "Cost", "is_visible": True},
                {"db_column_name": "created_at", "display_name": "Created At", "type": "timestamp",
                 "group_name": "Audit", "is_visible": False},
                {"db_column_name": "updated_at", "display_name": "Updated At", "type": "timestamp",
                 "group_name": "Audit", "is_visible": False}
            ]
            columns = default_columns
            visible_columns = [col["db_column_name"] for col in default_columns if col["is_visible"]]

        # 4. Fetch data
        if visible_columns:
            column_list = ", ".join(visible_columns)
            cur.execute(
                f"""
                    SELECT 
                        plan_revision_version,
                        season,
                        crop,
                        variety,
                        village,
                        grower,
                        latitude,
                        longitude,
                        net_acres_current,
                        productivity,
                        production_allocation,
                        actual_net_acres,
                        adjusted_production_allocation,
                        estimated_cost_per_kg,
                        estimated_production_cost,
                        actual_received_qty,
                        actual_packaged_qty,
                        actual_productivity,
                        actual_amount
                    FROM operations.supply_chain_vs_yield_view 
                    WHERE category_id = %s 
                    ORDER BY plan_revision_version DESC
                """,
                (category_id,)
            )
            rows = cur.fetchall()

            # Convert rows to list of dictionaries and handle Decimal conversion
            processed_rows = []
            for row in rows:
                row_dict = dict(row)
                for key, value in row_dict.items():
                    if isinstance(value, Decimal):
                        if value.is_nan():
                            row_dict[key] = None
                        else:
                            row_dict[key] = float(value)
                    elif isinstance(value, float) and math.isnan(value):
                        row_dict[key] = None
                processed_rows.append(row_dict)
        else:
            processed_rows = []

        return {
            "columns": columns,
            "data": processed_rows,
            "total_records": len(processed_rows)
        }

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching supply chain planning data: {str(e)}")
    finally:
        release_connection(conn)


def safe_float_convert(value):
    """
    Safely convert any value to a JSON-compliant float
    Handles NaN, Infinity, and other edge cases
    """
    if value is None:
        return 0.0

    try:
        if isinstance(value, (int, float)):
            # Check for NaN or Infinity
            if math.isnan(value) or math.isinf(value):
                return 0.0
            return float(value)
        elif isinstance(value, Decimal):
            float_val = float(value)
            if math.isnan(float_val) or math.isinf(float_val):
                return 0.0
            return float_val
        elif isinstance(value, str):
            if value.strip() == '' or value.lower() in ['null', 'none', 'nan']:
                return 0.0
            float_val = float(value.replace(',', ''))
            if math.isnan(float_val) or math.isinf(float_val):
                return 0.0
            return float_val
        else:
            return 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


@router.get("/supply-chain-planning/detailed")
def get_supply_chain_planning_detailed(
        season: Optional[str] = Query(None, description="Required: Season to filter by"),
        crop: Optional[str] = Query(None, description="Optional: Crop (requires season)"),
        state: Optional[str] = Query(None, description="Optional: State (requires season and crop)"),
        variety: Optional[str] = Query(None, description="Optional: Variety (requires season, crop, and state)"),
        village: Optional[str] = Query(None, description="Optional: Village (requires season, crop, state, and variety)"),
        mandal: Optional[str] = Query(None, description="Optional: Mandal filter"),
        district: Optional[str] = Query(None, description="Optional: District filter"),
        plan_revision_version: Optional[str] = Query(None),
        include_empty_growers: bool = Query(True, description="Include records with empty grower field"),
        group_by_village: bool = Query(True, description="Group records by village and sum metrics"),
        limit: int = Query(5000),
        offset: int = Query(0),
        category_id: int = Query(100007, description="Category ID for supply chain planning")
):
    """
    Get detailed supply chain planning records with proper NaN handling and grouping
    Includes village coordinates from operations.locations and column metadata with location hierarchy
    """
    conn = None
    try:
        # Get database connection first
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Validate hierarchical filtering requirements
        # Allow plan_revision_version to be selected first, then season
        if not plan_revision_version:
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version is required as the first filter parameter"
            )
        
        if season and not plan_revision_version:
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version is required before season"
            )
        
        if crop and (not plan_revision_version or not season):
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version and season are required before crop"
            )
            
        if state and (not plan_revision_version or not season or not crop):
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version, season, and crop are required before state"
            )
            
        if variety and (not plan_revision_version or not season or not crop or not state):
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version, season, crop, and state are required before variety"
            )
            
        if village and (not plan_revision_version or not season or not crop or not state or not variety):
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version, season, crop, state, and variety are required before village"
            )

        # Validate that the provided parameters exist in the database
        if season:
            cur.execute("SELECT DISTINCT season FROM operations.supply_chain_vs_yield_view WHERE plan_revision_version = %s AND season = %s", (plan_revision_version, season))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid season '{season}' for plan_revision_version '{plan_revision_version}'. Please check available seasons."
                )
        
        if crop:
            cur.execute("SELECT DISTINCT crop FROM operations.supply_chain_vs_yield_view WHERE plan_revision_version = %s AND season = %s AND crop = %s", (plan_revision_version, season, crop))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid crop '{crop}' for season '{season}' and plan_revision_version '{plan_revision_version}'. Please check available crops."
                )
        
        if state:
            cur.execute("SELECT DISTINCT state FROM operations.supply_chain_vs_yield_view WHERE plan_revision_version = %s AND season = %s AND crop = %s AND state = %s", (plan_revision_version, season, crop, state))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid state '{state}' for season '{season}', crop '{crop}', and plan_revision_version '{plan_revision_version}'. Please check available states."
                )
        
        if variety:
            cur.execute("SELECT DISTINCT variety FROM operations.supply_chain_vs_yield_view WHERE plan_revision_version = %s AND season = %s AND crop = %s AND state = %s AND variety = %s", (plan_revision_version, season, crop, state, variety))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid variety '{variety}' for season '{season}', crop '{crop}', state '{state}', and plan_revision_version '{plan_revision_version}'. Please check available varieties."
                )
        
        if village:
            cur.execute("SELECT DISTINCT village FROM operations.supply_chain_vs_yield_view WHERE plan_revision_version = %s AND season = %s AND crop = %s AND state = %s AND variety = %s AND village = %s", (plan_revision_version, season, crop, state, variety, village))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid village '{village}' for the given filters. Please check available villages."
                )

        # Get column metadata for supply chain planning with custom ordering
        cur.execute("""
            SELECT db_column_name, display_name, type, group_name, is_visible, is_editable,
                   CASE 
                       WHEN db_column_name = 'plan_revision_version' THEN 1
                       WHEN db_column_name = 'season' THEN 2
                       WHEN db_column_name = 'crop' THEN 3
                       WHEN db_column_name = 'state' THEN 4
                       WHEN db_column_name = 'variety' THEN 5
                       WHEN db_column_name = 'village' THEN 6
                       WHEN db_column_name = 'mandal' THEN 7
                       WHEN db_column_name = 'district' THEN 8
                       WHEN db_column_name = 'grower' THEN 9
                       WHEN db_column_name = 'longitude' THEN 10
                       WHEN db_column_name = 'latitude' THEN 11
                       WHEN db_column_name = 'net_acres_current' THEN 12
                       WHEN db_column_name = 'productivity' THEN 13
                       WHEN db_column_name = 'production_allocation' THEN 14
                       WHEN db_column_name = 'actual_net_acres' THEN 15
                       WHEN db_column_name = 'adjusted_production_allocation' THEN 16
                       WHEN db_column_name = 'estimated_cost_per_kg' THEN 17
                       WHEN db_column_name = 'estimated_production_cost' THEN 18
                       WHEN db_column_name = 'actual_received_qty' THEN 19
                       WHEN db_column_name = 'actual_packaged_qty' THEN 20
                       WHEN db_column_name = 'actual_productivity' THEN 21
                       WHEN db_column_name = 'actual_amount' THEN 22
                       WHEN db_column_name = 'created_at' THEN 23
                       WHEN db_column_name = 'updated_at' THEN 24
                       ELSE 999
                   END as custom_order
            FROM operations.column_metadata
            WHERE category_id = %s
            ORDER BY custom_order, column_id
        """, (category_id,))

        metadata = cur.fetchall()
        if metadata:
            columns = [dict(col) for col in metadata]
            visible_columns = [col["db_column_name"] for col in columns if col.get("is_visible", True)]
        else:
            # Updated default metadata with location hierarchy
            default_columns = [
                {"db_column_name": "plan_revision_version", "display_name": "Plan Version", "type": "string",
                 "group_name": "Planning", "is_visible": True, "is_editable": False},
                {"db_column_name": "season", "display_name": "Season", "type": "string", "group_name": "Basic Info",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "crop", "display_name": "Crop", "type": "string", "group_name": "Basic Info",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "state", "display_name": "State", "type": "string", "group_name": "Location",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "variety", "display_name": "Variety", "type": "string", "group_name": "Basic Info",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "village", "display_name": "Village", "type": "string", "group_name": "Location",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "mandal", "display_name": "Mandal", "type": "string", "group_name": "Location",
                 "is_visible": False, "is_editable": False},  # NEW
                {"db_column_name": "district", "display_name": "District", "type": "string", "group_name": "Location",
                 "is_visible": False, "is_editable": False},  # NEW
                {"db_column_name": "grower", "display_name": "Grower", "type": "string", "group_name": "Stakeholder",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "longitude", "display_name": "Longitude", "type": "decimal",
                 "group_name": "Location", "is_visible": True, "is_editable": False},
                {"db_column_name": "latitude", "display_name": "Latitude", "type": "decimal", "group_name": "Location",
                 "is_visible": True, "is_editable": False},
                {"db_column_name": "net_acres_current", "display_name": "Net Acres", "type": "decimal",
                 "group_name": "Production", "is_visible": True, "is_editable": True},
                {"db_column_name": "productivity", "display_name": "Productivity", "type": "decimal",
                 "group_name": "Production", "is_visible": True, "is_editable": True},
                {"db_column_name": "production_allocation", "display_name": "Planned Production", "type": "decimal",
                 "group_name": "Production", "is_visible": True, "is_editable": True},
                {"db_column_name": "actual_net_acres", "display_name": "Actual Net Acres", "type": "decimal",
                 "group_name": "Actual", "is_visible": True, "is_editable": True},
                {"db_column_name": "adjusted_production_allocation", "display_name": "Adjusted Production",
                 "type": "decimal", "group_name": "Production", "is_visible": True, "is_editable": True},
                {"db_column_name": "estimated_cost_per_kg", "display_name": "Cost per Kg", "type": "decimal",
                 "group_name": "Cost", "is_visible": True, "is_editable": True},
                {"db_column_name": "estimated_production_cost", "display_name": "Total Production Cost",
                 "type": "decimal", "group_name": "Cost", "is_visible": True, "is_editable": True},
                {"db_column_name": "actual_received_qty", "display_name": "Actual Received Qty", "type": "decimal",
                 "group_name": "Actual", "is_visible": True, "is_editable": False},
                {"db_column_name": "actual_packaged_qty", "display_name": "Actual Packaged Qty", "type": "decimal",
                 "group_name": "Actual", "is_visible": True, "is_editable": False},
                {"db_column_name": "actual_productivity", "display_name": "Actual Productivity", "type": "decimal",
                 "group_name": "Actual", "is_visible": True, "is_editable": False},
                {"db_column_name": "actual_amount", "display_name": "Actual Amount", "type": "decimal",
                 "group_name": "Actual", "is_visible": True, "is_editable": False},
                {"db_column_name": "record_count", "display_name": "Record Count", "type": "integer",
                 "group_name": "Summary", "is_visible": True, "is_editable": False}
            ]
            columns = default_columns
            visible_columns = [col["db_column_name"] for col in default_columns if col["is_visible"]]

        # If group_by_village is false, always use individual records query
        if not group_by_village:
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                variety,
                village,
                state,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """
        elif not season:
            # Level 0: Show seasons for the plan_revision_version - individual records
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                variety,
                village,
                state,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """
        elif not crop:
            # Level 1: Show crops for the season - individual records
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                variety,
                village,
                state,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """
        elif not state:
            # Level 2: Show states for the season and crop - individual records
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                variety,
                village,
                state,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """
        elif not variety:
            # Level 3: Show varieties for the season, crop, and state - individual records
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                variety,
                village,
                state,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """
        elif not village:
            # Level 4: Show villages for the season, crop, state, and variety - individual records
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                variety,
                village,
                state,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """
        else:
            # Level 5: Show growers for all filters - individual records
            base_query = """
            SELECT  
                plan_revision_version,
                season,
                crop,
                state,
                variety,
                village,
                COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower,
                COALESCE(net_acres_current, 0.0) as net_acres_current,
                COALESCE(productivity, 0.0) as productivity,
                COALESCE(production_allocation, 0.0) as production_allocation,
                COALESCE(actual_net_acres, 0.0) as actual_net_acres,
                COALESCE(adjusted_production_allocation, 0.0) as adjusted_production_allocation,
                COALESCE(estimated_cost_per_kg, 0.0) as estimated_cost_per_kg,
                COALESCE(estimated_production_cost, 0.0) as estimated_production_cost,
                COALESCE(actual_received_qty, 0.0) as actual_received_qty,
                COALESCE(actual_packaged_qty, 0.0) as actual_packaged_qty,
                COALESCE(actual_productivity, 0.0) as actual_productivity,
                COALESCE(actual_amount, 0.0) as actual_amount,
                longitude,
                latitude,
                1 as record_count
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
            """

        conditions = []
        params = {}

        if plan_revision_version:
            conditions.append("plan_revision_version = %(version)s")
            params["version"] = plan_revision_version
        if season:
            conditions.append("season = %(season)s")
            params["season"] = season
        if crop:
            conditions.append("crop = %(crop)s")
            params["crop"] = crop
        if variety:
            conditions.append("variety ILIKE %(variety)s")
            params["variety"] = f"%{variety}%"
        if village:
            conditions.append("village ILIKE %(village)s")
            params["village"] = f"%{village}%"
        # NEW: Location hierarchy filters
        if mandal:
            conditions.append("mandal ILIKE %(mandal)s")
            params["mandal"] = f"%{mandal}%"
        if district:
            conditions.append("district ILIKE %(district)s")
            params["district"] = f"%{district}%"
        if state:
            conditions.append("state = %(state)s")
            params["state"] = state
        if not include_empty_growers and not group_by_village:
            conditions.append("grower IS NOT NULL AND grower != ''")

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        # All levels now use individual records - no GROUP BY needed
            base_query += """
        ORDER BY season, crop, state, variety, village, grower
            LIMIT %(limit)s OFFSET %(offset)s
            """

        params.update({"limit": limit, "offset": offset})

        cur.execute(base_query, params)
        rows = cur.fetchall()

        # Process rows directly since coordinates are included in the main query

        processed_rows = []
        for row in rows:
            row_dict = dict(row)

            # Handle grower field if it exists
            if 'grower_display' in row_dict:
                row_dict['grower'] = row_dict.pop('grower_display')

            # All levels now return individual record columns with guaranteed field order
            filtered_row = OrderedDict([
                ('plan_revision_version', row_dict.get('plan_revision_version', '')),
                ('season', row_dict.get('season', '')),
                ('crop', row_dict.get('crop', '')),
                ('state', row_dict.get('state', '')),
                ('variety', row_dict.get('variety', '')),
                ('village', row_dict.get('village', '')),
                ('grower', row_dict.get('grower', 'Not Assigned')),
                ('net_acres_current', safe_float_convert(row_dict.get('net_acres_current', 0.0))),
                ('productivity', safe_float_convert(row_dict.get('productivity', 0.0))),
                ('production_allocation', safe_float_convert(row_dict.get('production_allocation', 0.0))),
                ('actual_net_acres', safe_float_convert(row_dict.get('actual_net_acres', 0.0))),
                ('adjusted_production_allocation', safe_float_convert(row_dict.get('adjusted_production_allocation', 0.0))),
                ('estimated_cost_per_kg', safe_float_convert(row_dict.get('estimated_cost_per_kg', 0.0))),
                ('estimated_production_cost', safe_float_convert(row_dict.get('estimated_production_cost', 0.0))),
                ('actual_received_qty', safe_float_convert(row_dict.get('actual_received_qty', 0.0))),
                ('actual_packaged_qty', safe_float_convert(row_dict.get('actual_packaged_qty', 0.0))),
                ('actual_productivity', safe_float_convert(row_dict.get('actual_productivity', 0.0))),
                ('actual_amount', safe_float_convert(row_dict.get('actual_amount', 0.0))),
                ('longitude', safe_float_convert(row_dict.get('longitude', 0.0))),
                ('latitude', safe_float_convert(row_dict.get('latitude', 0.0))),
                ('record_count', safe_float_convert(row_dict.get('record_count', 1.0)))
            ])
            
            processed_rows.append(filtered_row)

        # All levels now use individual records - simple count
            count_query = "SELECT COUNT(*) FROM operations.supply_chain_vs_yield_view WHERE 1=1"

        count_conditions = []
        count_params = {}

        if plan_revision_version:
            count_conditions.append("plan_revision_version = %(version)s")
            count_params["version"] = plan_revision_version
        if season:
            count_conditions.append("season ILIKE %(season)s")
            count_params["season"] = f"%{season}%"
        if crop:
            count_conditions.append("crop ILIKE %(crop)s")
            count_params["crop"] = f"%{crop}%"
        if variety:
            count_conditions.append("variety ILIKE %(variety)s")
            count_params["variety"] = f"%{variety}%"
        if village:
            count_conditions.append("village ILIKE %(village)s")
            count_params["village"] = f"%{village}%"
        # NEW: Location hierarchy count filters
        if mandal:
            count_conditions.append("mandal ILIKE %(mandal)s")
            count_params["mandal"] = f"%{mandal}%"
        if district:
            count_conditions.append("district ILIKE %(district)s")
            count_params["district"] = f"%{district}%"
        if state:
            count_conditions.append("state ILIKE %(state)s")
            count_params["state"] = f"%{state}%"
        if not include_empty_growers and not group_by_village:
            count_conditions.append("grower IS NOT NULL AND grower != ''")

        if count_conditions:
            count_query += " AND " + " AND ".join(count_conditions)

        cur.execute(count_query, count_params)
        total_count = cur.fetchone()[0]

        # Removed unnecessary village dropdown logic to optimize response

        return {
            "columns": columns,
            "data": processed_rows,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "grouped": group_by_village
        }

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error fetching supply chain planning data: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        if conn:
            release_connection(conn)