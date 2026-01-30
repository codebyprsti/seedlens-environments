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
        # plan_revision_version is the first optional filter, then season
        # If plan_revision_version is provided, season becomes optional
        # If plan_revision_version is not provided, season is required as the first filter
        
        if not plan_revision_version and not season:
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version or season is required as the first filter parameter"
            )
        
        # If crop is provided, we need either plan_revision_version or season
        if crop and not plan_revision_version and not season:
            raise HTTPException(
                status_code=400,
                detail="plan_revision_version or season is required before crop"
            )
            
        # Allow "All" to be passed for state without requiring crop
        if state and state != "All":
            if not plan_revision_version and not season:
                raise HTTPException(
                    status_code=400,
                    detail="plan_revision_version or season is required before state"
                )
            if not crop:
                raise HTTPException(
                    status_code=400,
                    detail="crop is required before state"
                )
            
        if variety and variety != "All" and ((not plan_revision_version and not season) or not crop):
            if not plan_revision_version and not season:
                raise HTTPException(
                    status_code=400,
                    detail="plan_revision_version or season is required before variety"
                )
            if not crop:
                raise HTTPException(
                    status_code=400,
                    detail="crop is required before variety"
                )
            
        if village and ((not plan_revision_version and not season) or not crop or not state or not variety):
            missing = []
            if not plan_revision_version and not season:
                missing.append("plan_revision_version or season")
            if not crop:
                missing.append("crop")
            if not state:
                missing.append("state")
            if not variety:
                missing.append("variety")
            raise HTTPException(
                status_code=400,
                detail=f"{', '.join(missing)} are required before village"
            )

        # Normalize state and variety for case-insensitive comparison
        state_normalized = str(state).strip().lower() if state else ""
        variety_normalized = str(variety).strip().lower() if variety else ""

        # Validate that the provided parameters exist in the database
        # Build base query conditions
        base_conditions = []
        base_params = []
        
        if plan_revision_version:
            base_conditions.append("plan_revision_version = %s")
            base_params.append(plan_revision_version)
        
        if season:
            base_conditions.append("season = %s")
            base_params.append(season)
        
        if crop:
            base_conditions.append("crop = %s")
            base_params.append(crop)
        
        where_clause = " AND ".join(base_conditions) if base_conditions else "1=1"
        
        # Validate season
        if season:
            season_query = f"SELECT DISTINCT season FROM operations.supply_chain_vs_yield_view WHERE {where_clause}"
            cur.execute(season_query, tuple(base_params))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid season '{season}'. Please check available seasons."
                )
        
        # Validate crop
        if crop:
            crop_query = f"SELECT DISTINCT crop FROM operations.supply_chain_vs_yield_view WHERE {where_clause}"
            cur.execute(crop_query, tuple(base_params))
            if not cur.fetchone():
                filter_info = f"plan_revision_version '{plan_revision_version}'" if plan_revision_version else f"season '{season}'"
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid crop '{crop}' for {filter_info}. Please check available crops."
                )
        
        # Validate state only if it's not "All" (case-insensitive)
        if state and state_normalized != "all":
            state_query = f"SELECT DISTINCT state FROM operations.supply_chain_vs_yield_view WHERE {where_clause} AND state = %s"
            state_params = list(base_params) + [state]
            cur.execute(state_query, tuple(state_params))
            if not cur.fetchone():
                filter_info = f"plan_revision_version '{plan_revision_version}'" if plan_revision_version else f"season '{season}'"
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid state '{state}' for {filter_info} and crop '{crop}'. Please check available states."
                )
        
        # Validate variety only if it's not "All" (case-insensitive)
        if variety and variety_normalized != "all":
            # If state is "All", validate variety across all states
            if state_normalized == "all":
                variety_query = f"SELECT DISTINCT variety FROM operations.supply_chain_vs_yield_view WHERE {where_clause} AND variety = %s"
                variety_params = list(base_params) + [variety]
            else:
                variety_query = f"SELECT DISTINCT variety FROM operations.supply_chain_vs_yield_view WHERE {where_clause} AND state = %s AND variety = %s"
                variety_params = list(base_params) + [state, variety]
            
            cur.execute(variety_query, tuple(variety_params))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid variety '{variety}' for the given filters. Please check available varieties."
                )
        
        # Validate village - handle "All" for state and variety
        if village:
            # Build village validation query based on state/variety filters
            village_query = f"SELECT DISTINCT village FROM operations.supply_chain_vs_yield_view WHERE {where_clause}"
            village_params = list(base_params)
            
            # Only add state filter if it's not "All"
            if state and state_normalized != "all":
                village_query += " AND state = %s"
                village_params.append(state)
            
            # Only add variety filter if it's not "All"
            if variety and variety_normalized != "all":
                village_query += " AND variety = %s"
                village_params.append(variety)
            
            village_query += " AND village = %s"
            village_params.append(village)
            
            cur.execute(village_query, tuple(village_params))
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

        # Determine rollup level and build dynamic query with aggregation
        # Hierarchy: plan_revision_version (optional) → Season → Crop → State → Variety → Village
        
        # Base group by fields - always include season in results (even if not filtered)
        group_by_fields = []
        select_fields = []
        
        # Track what additional fields we're selecting
        include_season = False
        include_crop = False
        include_state = False
        include_variety = False
        include_village = False
        include_grower = False
        
        # Season is always included in results (to show all seasons when plan_revision_version is selected)
        select_fields.append("season")
        group_by_fields.append("season")
        include_season = True
        
        # Normalize state and variety for case-insensitive comparison
        state_normalized = str(state).strip().lower() if state else ""
        variety_normalized = str(variety).strip().lower() if variety else ""
        
        # Level 1: When season is selected, show crops (grouped)
        if not crop:
            # Show all available crops for the selected season
            select_fields.append("crop")
            group_by_fields.append("crop")
            include_crop = True
            # Don't include state, variety, or village when showing crops
            include_state = False
            include_variety = False
            include_village = False
            include_grower = False
        else:
            # Crop is selected, add to group by
            select_fields.append("crop")
            group_by_fields.append("crop")
            include_crop = True
            
            # Level 2: After selecting a crop, show states (grouped)
            if not state or state_normalized == "":
                # Show all available states for the selected crop
                select_fields.append("state")
                group_by_fields.append("state")
                include_state = True
                # Don't include variety or village when showing states
                include_variety = False
                include_village = False
                include_grower = False
            else:
                # State is selected, add to group by
                select_fields.append("state")
                group_by_fields.append("state")
                include_state = True
                
                # Level 3: State selection logic
                if state_normalized == "all":
                    # When state = "All", show all states + all varieties (grouped)
                    # State is already included, now include variety
                    select_fields.append("variety")
                    group_by_fields.append("variety")
                    include_variety = True
                    
                    # Check if variety is selected
                    if variety and variety_normalized != "all":
                        # Specific variety selected - include villages
                        if village and village.strip() and village.strip().upper() != "ALL":
                            # Specific village selected - include it with grower
                            select_fields.append("village")
                            select_fields.append("COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower")
                            group_by_fields.append("village")
                            group_by_fields.append("grower")
                            include_village = True
                            include_grower = True
                        else:
                            # Village not selected or "All" - show all villages grouped
                            select_fields.append("village")
                            group_by_fields.append("village")
                            include_village = True
                            include_grower = False
                    else:
                        # Variety not selected or "All" - show all varieties grouped, no villages
                        include_village = False
                        include_grower = False
                else:
                    # Specific state selected - show only varieties available under that state (grouped)
                    if not variety or variety_normalized == "":
                        # Show all varieties for the selected state
                        select_fields.append("variety")
                        group_by_fields.append("variety")
                        include_variety = True
                        # Don't include village when showing varieties
                        include_village = False
                        include_grower = False
                    else:
                        # Variety is selected
                        if variety_normalized == "all":
                            # When variety = "All", show all varieties + all villages (grouped)
                            select_fields.append("variety")
                            group_by_fields.append("variety")
                            include_variety = True
                            
                            # Include villages when variety is "All"
                            if village and village.strip() and village.strip().upper() != "ALL":
                                # Specific village selected - include it with grower
                                select_fields.append("village")
                                select_fields.append("COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower")
                                group_by_fields.append("village")
                                group_by_fields.append("grower")
                                include_village = True
                                include_grower = True
                            else:
                                # Village not selected or "All" - show all villages grouped
                                select_fields.append("village")
                                group_by_fields.append("village")
                                include_village = True
                                include_grower = False
                        else:
                            # Specific variety selected - show only villages under that variety (grouped)
                            select_fields.append("variety")
                            group_by_fields.append("variety")
                            include_variety = True
                            
                            if village and village.strip() and village.strip().upper() != "ALL":
                                # Specific village selected - include it with grower
                                select_fields.append("village")
                                select_fields.append("COALESCE(NULLIF(grower, ''), 'Not Assigned') as grower")
                                group_by_fields.append("village")
                                group_by_fields.append("grower")
                                include_village = True
                                include_grower = True
                            else:
                                # Village not selected or "All" - show all villages for the variety
                                select_fields.append("village")
                                group_by_fields.append("village")
                                include_village = True
                                include_grower = False
        
        # Add plan_revision_version to select if provided (optional filter)
        if plan_revision_version:
            select_fields.insert(0, "plan_revision_version")
            group_by_fields.insert(0, "plan_revision_version")
        
        # Build SELECT with aggregations
        select_clause = ", ".join(select_fields)
        
        # Add aggregated numeric fields
        aggregated_fields = """
            SUM(COALESCE(net_acres_current, 0.0)) as net_acres_current,
            AVG(COALESCE(productivity, 0.0)) as productivity,
            SUM(COALESCE(production_allocation, 0.0)) as production_allocation,
            SUM(COALESCE(actual_net_acres, 0.0)) as actual_net_acres,
            SUM(COALESCE(adjusted_production_allocation, 0.0)) as adjusted_production_allocation,
            AVG(COALESCE(estimated_cost_per_kg, 0.0)) as estimated_cost_per_kg,
            SUM(COALESCE(estimated_production_cost, 0.0)) as estimated_production_cost,
            SUM(COALESCE(actual_received_qty, 0.0)) as actual_received_qty,
            SUM(COALESCE(actual_packaged_qty, 0.0)) as actual_packaged_qty,
            AVG(COALESCE(actual_productivity, 0.0)) as actual_productivity,
            SUM(COALESCE(actual_amount, 0.0)) as actual_amount,
            COUNT(*) as record_count,
            AVG(longitude) as longitude,
            AVG(latitude) as latitude
        """
        
        # Build GROUP BY clause - handle grower field specially
        group_by_parts = []
        for field in group_by_fields:
            if field == "grower" and include_grower:
                # Use the same expression as in SELECT for grower
                group_by_parts.append("COALESCE(NULLIF(grower, ''), 'Not Assigned')")
            else:
                group_by_parts.append(field)
        group_by_clause = ", ".join(group_by_parts)
        
        base_query = f"""
            SELECT  
                {select_clause},
                {aggregated_fields}
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
        # Handle "All" selections for state and variety (case-insensitive)
        state_normalized = str(state).strip().lower() if state else ""
        variety_normalized = str(variety).strip().lower() if variety else ""
        
        if state and state_normalized != "all":
            conditions.append("state = %(state)s")
            params["state"] = state
        if variety and variety_normalized != "all":
            conditions.append("variety ILIKE %(variety)s")
            params["variety"] = f"%{variety}%"
        # Only filter by village if it's provided and not "ALL"
        if village and village.strip() and village.strip().upper() != "ALL":
            conditions.append("village ILIKE %(village)s")
            params["village"] = f"%{village}%"
        
        # When villages are included in grouping, filter out NULL villages to show only valid village data
        if include_village and not village:
            # Only filter NULL villages if we're grouping by village but not filtering by a specific village
            conditions.append("village IS NOT NULL AND village != '' AND TRIM(village) != ''")
        # NEW: Location hierarchy filters
        if mandal:
            conditions.append("mandal ILIKE %(mandal)s")
            params["mandal"] = f"%{mandal}%"
        if district:
            conditions.append("district ILIKE %(district)s")
            params["district"] = f"%{district}%"
        if not include_empty_growers and include_grower:
            conditions.append("grower IS NOT NULL AND grower != ''")

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        # Add GROUP BY clause
        # Build ORDER BY - use alias for grower field
        order_by_parts = []
        for field in group_by_fields:
            if field == "grower" and include_grower:
                order_by_parts.append("grower")  # Use alias from SELECT
            elif field != "grower" or not include_grower:
                order_by_parts.append(field)
        
        # Ensure we have fields to group by
        if not group_by_clause:
            raise HTTPException(status_code=400, detail="No grouping fields specified. Please select at least one filter.")
        
        # Build ORDER BY clause - use same fields as GROUP BY
        if order_by_parts:
            order_by_clause = ", ".join(order_by_parts)
        else:
            order_by_clause = group_by_clause  # Fallback to GROUP BY fields if order_by_parts is empty
        
        base_query += f"""
        GROUP BY {group_by_clause}
        ORDER BY {order_by_clause}
        LIMIT %(limit)s OFFSET %(offset)s
        """

        params.update({"limit": limit, "offset": offset})

        cur.execute(base_query, params)
        rows = cur.fetchall()

        # Process aggregated rows
        processed_rows = []
        for row in rows:
            row_dict = dict(row)

            # Build result dictionary with fields based on what was selected
            filtered_row = OrderedDict()
            
            # Include plan_revision_version only if it was in the query
            if plan_revision_version:
                filtered_row['plan_revision_version'] = row_dict.get('plan_revision_version', '')
            else:
                filtered_row['plan_revision_version'] = None
            
            # Add fields based on what was included in the query
            if include_season:
                filtered_row['season'] = row_dict.get('season', '')
            else:
                filtered_row['season'] = None
                
            if include_crop:
                filtered_row['crop'] = row_dict.get('crop', '')
            else:
                filtered_row['crop'] = None
                
            if include_state:
                filtered_row['state'] = row_dict.get('state', '')
            else:
                filtered_row['state'] = None
                
            # Handle variety field based on filter logic
            if include_variety:
                filtered_row['variety'] = row_dict.get('variety', '')
            else:
                # Set variety to NULL when not included in grouping
                filtered_row['variety'] = None
                
            # Handle village field based on filter logic
            if include_village:
                village_value = row_dict.get('village')
                # Handle None, empty string, or whitespace-only values
                if village_value is None or (isinstance(village_value, str) and village_value.strip() == ''):
                    filtered_row['village'] = None
                else:
                    filtered_row['village'] = str(village_value).strip()
            else:
                # Set village to NULL when not included in grouping
                filtered_row['village'] = None
                
            if include_grower:
                filtered_row['grower'] = row_dict.get('grower', 'Not Assigned')
            else:
                filtered_row['grower'] = None
            
            # Add aggregated numeric fields - round all to 2 decimal places
            filtered_row['net_acres_current'] = round(safe_float_convert(row_dict.get('net_acres_current', 0.0)), 2)
            filtered_row['productivity'] = round(safe_float_convert(row_dict.get('productivity', 0.0)), 2)
            filtered_row['production_allocation'] = round(safe_float_convert(row_dict.get('production_allocation', 0.0)), 2)
            filtered_row['actual_net_acres'] = round(safe_float_convert(row_dict.get('actual_net_acres', 0.0)), 2)
            filtered_row['adjusted_production_allocation'] = round(safe_float_convert(row_dict.get('adjusted_production_allocation', 0.0)), 2)
            filtered_row['estimated_cost_per_kg'] = round(safe_float_convert(row_dict.get('estimated_cost_per_kg', 0.0)), 2)
            filtered_row['estimated_production_cost'] = round(safe_float_convert(row_dict.get('estimated_production_cost', 0.0)), 2)
            filtered_row['actual_received_qty'] = round(safe_float_convert(row_dict.get('actual_received_qty', 0.0)), 2)
            filtered_row['actual_packaged_qty'] = round(safe_float_convert(row_dict.get('actual_packaged_qty', 0.0)), 2)
            filtered_row['actual_productivity'] = round(safe_float_convert(row_dict.get('actual_productivity', 0.0)), 2)
            filtered_row['actual_amount'] = round(safe_float_convert(row_dict.get('actual_amount', 0.0)), 2)
            filtered_row['longitude'] = round(safe_float_convert(row_dict.get('longitude', 0.0)), 2)
            filtered_row['latitude'] = round(safe_float_convert(row_dict.get('latitude', 0.0)), 2)
            filtered_row['record_count'] = int(round(safe_float_convert(row_dict.get('record_count', 1.0)), 0))  # record_count should be integer
            
            processed_rows.append(filtered_row)

        # Count query: count the aggregated groups
        # Filter out "grower" from count_group_by_fields if it's an expression
        count_group_by_fields = []
        for f in group_by_fields:
            if f == "grower" and include_grower:
                # Skip grower in count query as it's an expression
                continue
            elif "COALESCE" in str(f) or "as grower" in str(f):
                # Skip expressions
                continue
            else:
                count_group_by_fields.append(f)
        
        count_query = f"""
        SELECT COUNT(*) FROM (
            SELECT {", ".join(count_group_by_fields)}
            FROM operations.supply_chain_vs_yield_view
            WHERE 1=1
        """

        count_conditions = []
        count_params = {}

        if plan_revision_version:
            count_conditions.append("plan_revision_version = %(version)s")
            count_params["version"] = plan_revision_version
        if season:
            count_conditions.append("season = %(season)s")
            count_params["season"] = season
        if crop:
            count_conditions.append("crop = %(crop)s")
            count_params["crop"] = crop
        # Handle "All" selections for state and variety in count query (case-insensitive)
        state_normalized = str(state).strip().lower() if state else ""
        variety_normalized = str(variety).strip().lower() if variety else ""
        
        if state and state_normalized != "all":
            count_conditions.append("state = %(state)s")
            count_params["state"] = state
        if variety and variety_normalized != "all":
            count_conditions.append("variety ILIKE %(variety)s")
            count_params["variety"] = f"%{variety}%"
        if village and village.strip() and village.strip().upper() != "ALL":
            count_conditions.append("village ILIKE %(village)s")
            count_params["village"] = f"%{village}%"
        # Location hierarchy count filters
        if mandal:
            count_conditions.append("mandal ILIKE %(mandal)s")
            count_params["mandal"] = f"%{mandal}%"
        if district:
            count_conditions.append("district ILIKE %(district)s")
            count_params["district"] = f"%{district}%"
        if not include_empty_growers and include_grower:
            count_conditions.append("grower IS NOT NULL AND grower != ''")
        
        # When villages are included in grouping, filter out NULL/empty villages in count query too
        if include_village and not village:
            count_conditions.append("village IS NOT NULL AND village != '' AND TRIM(village) != ''")

        if count_conditions:
            count_query += " AND " + " AND ".join(count_conditions)

        count_query += f"""
        GROUP BY {", ".join(count_group_by_fields)}
        ) as grouped_results
        """

        cur.execute(count_query, count_params)
        total_count = cur.fetchone()[0]

        # Generate total row for all numeric/metric fields
        total_row = OrderedDict()
        
        if processed_rows:
            # Identify all numeric fields dynamically from the first row
            numeric_fields = []
            for key, value in processed_rows[0].items():
                # Check if the field is numeric (int or float) and not None
                if isinstance(value, (int, float)) and value is not None:
                    # Skip non-metric fields that shouldn't be summed (like record_count, IDs, etc.)
                    # Also skip fields that are averages (like productivity, estimated_cost_per_kg, actual_productivity)
                    # These should be calculated as weighted averages or excluded from totals
                    if key not in ['record_count']:
                        numeric_fields.append(key)
            
            # Calculate totals for each numeric field
            for field_name in numeric_fields:
                # Sum all values for this field across all rows
                total_value = sum(
                    safe_float_convert(row.get(field_name, 0.0)) 
                    for row in processed_rows 
                    if row.get(field_name) is not None
                )
                # Round to 2 decimal places for consistency
                total_row[f'total_{field_name}'] = round(total_value, 2)
        
        # Append total row to processed_rows if it has any totals
        if total_row:
            processed_rows.append(total_row)

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
        import traceback
        error_details = traceback.format_exc()
        print(f"Database error: {str(e)}")
        print(f"Traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        if conn:
            conn.rollback()
        import traceback
        error_details = traceback.format_exc()
        error_msg = str(e) if str(e) else repr(e)
        print(f"Error fetching supply chain planning data: {error_msg}")
        print(f"Traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error fetching supply chain planning data: {error_msg}")
    finally:
        if 'cur' in locals():
            cur.close()
        if conn:
            release_connection(conn)