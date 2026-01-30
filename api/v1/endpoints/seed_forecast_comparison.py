from decimal import Decimal
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from typing import List, Optional, Dict, Any
import psycopg2
import psycopg2.extras
from core.db import get_db, get_connection, release_connection

router = APIRouter()


def safe_float_convert(value):
    """
    Safely convert any value to a JSON-compliant float
    Handles NaN, Infinity, and other edge cases
    """
    if value is None:
        return 0.0

    try:
        if isinstance(value, (int, float)):
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


def check_table_exists(conn, table_name: str, schema: str = "operations_demo") -> bool:
    """Check if a table exists in the specified schema"""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s AND table_name = %s
            )
        """, (schema, table_name))
        result = cur.fetchone()
        cur.close()
        return result[0] if result else False
    except:
        return False


def get_table_columns(conn, table_name: str, schema: str = "operations_demo") -> List[str]:
    """Get column names for a table"""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """, (schema, table_name))
        columns = [row[0] for row in cur.fetchall()]
        cur.close()
        return columns
    except:
        return []


@router.get("/seed-forecast/comparison-options")
def get_comparison_options():
    """
    Get available comparison types and their descriptions
    """
    return {
        "comparison_types": [
            {
                "value": "actual",
                "label": "Actual",
                "description": "Actual received quantities from supply chain planning",
                "metric_field": "actual_received_qty",
                "source_table": "supply_chain_planning"
            },
            {
                "value": "plan",
                "label": "Plan",
                "description": "Planned production allocation",
                "metric_field": "production_allocation",
                "source_table": "supply_chain_planning"
            },
            {
                "value": "forecast",
                "label": "Forecast",
                "description": "Forecasted values from seed forecast stages",
                "metric_field": "forecast_value",
                "source_table": "seed_forecast"
            }
        ],
        "forecast_stages": [
            {"stage": "stage_forecast1", "label": "Stage 1 Forecast"},
            {"stage": "stage_forecast2", "label": "Stage 2 Forecast"},
            {"stage": "stage_forecast3", "label": "Stage 3 Forecast"},
            {"stage": "stage_forecast4", "label": "Stage 4 Forecast"},
            {"stage": "stage_forecast5", "label": "Stage 5 Forecast"},
            {"stage": "stage_forecast6", "label": "Stage 6 Forecast"}
        ]
    }


@router.get("/seed-forecast/filter-options")
def get_filter_options(
        conn_param: Optional[str] = Query(None)
):
    """
    Get available filter options for seasons, crops, varieties, and plan versions
    Fixed with correct column names
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Check if lookup tables exist
        has_seasons_table = check_table_exists(conn, "seasons")
        has_crops_table = check_table_exists(conn, "crops")
        has_varieties_table = check_table_exists(conn, "varieties")
        has_seed_forecast_table = check_table_exists(conn, "seed_forecast")

        # Get available seasons
        seasons = []
        try:
            # Try from supply chain planning first
            cur.execute("""
                SELECT DISTINCT season FROM operations_demo.supply_chain_planning 
                WHERE season IS NOT NULL AND season != ''
                ORDER BY season
            """)
            seasons.extend([row['season'] for row in cur.fetchall()])

            # If seed forecast table and seasons table exist, get from there too
            if has_seed_forecast_table and has_seasons_table:
                cur.execute("""
                    SELECT DISTINCT s.season_name as season 
                    FROM operations_demo.seed_forecast sf
                    JOIN operations_demo.seasons s ON sf.season_id = s.season_id
                    WHERE s.season_name IS NOT NULL AND s.season_name != ''
                """)
                seasons.extend([row['season'] for row in cur.fetchall()])
        except Exception as e:
            print(f"Error getting seasons: {e}")

        # Get available crops
        crops = []
        try:
            cur.execute("""
                SELECT DISTINCT crop FROM operations_demo.supply_chain_planning 
                WHERE crop IS NOT NULL AND crop != ''
                ORDER BY crop
            """)
            crops.extend([row['crop'] for row in cur.fetchall()])

            if has_seed_forecast_table and has_crops_table:
                cur.execute("""
                    SELECT DISTINCT c.crop_name as crop 
                    FROM operations_demo.seed_forecast sf
                    JOIN operations_demo.crops c ON sf.crop_id = c.crop_id
                    WHERE c.crop_name IS NOT NULL AND c.crop_name != ''
                """)
                crops.extend([row['crop'] for row in cur.fetchall()])
        except Exception as e:
            print(f"Error getting crops: {e}")

        # Get available varieties
        varieties = []
        try:
            cur.execute("""
                SELECT DISTINCT variety FROM operations_demo.supply_chain_planning 
                WHERE variety IS NOT NULL AND variety != ''
                ORDER BY variety
            """)
            varieties.extend([row['variety'] for row in cur.fetchall()])

            if has_seed_forecast_table and has_varieties_table:
                cur.execute("""
                    SELECT DISTINCT v.variety_name as variety 
                    FROM operations_demo.seed_forecast sf
                    JOIN operations_demo.varieties v ON sf.variety_id = v.variety_id
                    WHERE v.variety_name IS NOT NULL AND v.variety_name != ''
                """)
                varieties.extend([row['variety'] for row in cur.fetchall()])
        except Exception as e:
            print(f"Error getting varieties: {e}")

        # Get available plan revision versions
        plan_versions = []
        try:
            cur.execute("""
                SELECT DISTINCT plan_revision_version 
                FROM operations_demo.supply_chain_planning 
                WHERE plan_revision_version IS NOT NULL AND plan_revision_version != ''
                ORDER BY plan_revision_version DESC
            """)
            plan_versions = [row['plan_revision_version'] for row in cur.fetchall()]
        except Exception as e:
            print(f"Error getting plan versions: {e}")

        # Remove duplicates and sort
        seasons = sorted(list(set(seasons)))
        crops = sorted(list(set(crops)))
        varieties = sorted(list(set(varieties)))

        # Return based on conn_param
        if conn_param == "season":
            return {"seasons": seasons}
        elif conn_param == "crop":
            return {"crops": crops}
        elif conn_param == "variety":
            return {"varieties": varieties}
        elif conn_param == "plan_versions":
            return {"plan_versions": plan_versions}
        else:
            return {
                "seasons": seasons,
                "crops": crops,
                "varieties": varieties,
                "plan_versions": plan_versions
            }

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching filter options: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)


@router.get("/seed-forecast/filtered-options")
def get_filtered_options(
        season_id: Optional[str] = Query(None),
        crop_id: Optional[str] = Query(None),
        plan_versions: Optional[str] = Query(None),
        db: Session = Depends(get_db)
):
    """
    Cascading dropdown for seed forecast comparison:
    1. If no params: return season list
    2. If season_id is present: return crop list for that season
    3. If crop_id is present: return plan_versions list for season + crop
    4. If plan_versions is present: return value_types options (actual/forecast)
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        result = {}

        # Check if lookup tables exist
        has_seasons_table = check_table_exists(conn, "seasons")
        has_crops_table = check_table_exists(conn, "crops")

        if not season_id and not crop_id and not plan_versions:
            # Step 1: Return all seasons
            if has_seasons_table:
                cur.execute("""
                    SELECT DISTINCT s.season_id, s.season_name
                    FROM operations_demo.seasons s
                    JOIN operations_demo.supply_chain_planning scp ON s.season_name = scp.season
                    WHERE s.season_name IS NOT NULL AND s.season_name != ''
                    ORDER BY s.season_name
                """)
                seasons = [{str(row['season_id']): row['season_name']} for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT DISTINCT season 
                    FROM operations_demo.supply_chain_planning 
                    WHERE season IS NOT NULL AND season != ''
                    ORDER BY season
                """)
                seasons = [row['season'] for row in cur.fetchall()]

            result["season_id"] = seasons

        elif season_id and not crop_id and not plan_versions:
            # Step 2: Return crops for given season
            if has_crops_table:
                cur.execute("""
                    SELECT DISTINCT c.crop_id, c.crop_name
                    FROM operations_demo.crops c
                    JOIN operations_demo.supply_chain_planning scp ON c.crop_name = scp.crop
                    WHERE scp.season ILIKE %s 
                    AND c.crop_name IS NOT NULL AND c.crop_name != ''
                    ORDER BY c.crop_name
                """, (f"%{season_id}%",))
                crops = [{str(row['crop_id']): row['crop_name']} for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT DISTINCT crop 
                    FROM operations_demo.supply_chain_planning 
                    WHERE season ILIKE %s 
                    AND crop IS NOT NULL AND crop != ''
                    ORDER BY crop
                """, (f"%{season_id}%",))
                crops = [row['crop'] for row in cur.fetchall()]

            result["crop_id"] = crops

        elif season_id and crop_id and not plan_versions:
            # Step 3: Return plan versions for season + crop
            # First, resolve season_name from operations_demo.seasons using season_id
            cur.execute("""
                SELECT season_name 
                FROM operations_demo.seasons 
                WHERE season_id = %s
            """, (season_id,))
            season_row = cur.fetchone()
            if not season_row:
                result["plan_versions"] = []
                return result
            season_name = season_row['season_name']
            
            # Second, resolve crop_name from operations_demo.crops using crop_id
            cur.execute("""
                SELECT crop_name 
                FROM operations_demo.crops 
                WHERE crop_id = %s
            """, (crop_id,))
            crop_row = cur.fetchone()
            if not crop_row:
                result["plan_versions"] = []
                return result
            crop_name = crop_row['crop_name']
            
            # Third, fetch DISTINCT plan_revision_version from operations_demo.supply_chain_planning
            # using exact match with UPPER(TRIM()) for case-insensitive and whitespace-tolerant matching
            cur.execute("""
                SELECT DISTINCT plan_revision_version 
                FROM operations_demo.supply_chain_planning 
                WHERE UPPER(TRIM(season)) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(crop)) = UPPER(TRIM(%s))
                  AND plan_revision_version IS NOT NULL 
                  AND plan_revision_version != ''
                ORDER BY plan_revision_version DESC
            """, (season_name, crop_name))
            plan_versions = [{row['plan_revision_version']: row['plan_revision_version']} for row in cur.fetchall()]
            result["plan_versions"] = plan_versions

        elif season_id and crop_id and plan_versions:
            # Step 4: Return value type options (actual/forecast)
            # These are static options, no database query needed
            result["value_types"] = [{"actual":"actual"}, {"forecast":"forecast"}]

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter combination")

        return result

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching filtered options: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)


@router.get("/seed-forecast/comparison")
def get_seed_forecast_comparison(
    season_id: Optional[str] = Query(None, description="Filter by season"),
    crop_id: Optional[str] = Query(None, description="Filter by crop"),
    plan_versions: Optional[str] = Query("v1.0-Maximized-Productivity", description="Plan revision version"),
    value_types: str = Query("actual", description="Value type: actual or forecast"),
    limit_actuals_to_planned: bool = Query(
        False,
        description="If true, only include records where planned production_allocation > 0 (limits actuals to planned villages)"
    ),
    limit: int = Query(1000),
    offset: int = Query(0)
):
    """
    Compare plan values with actual or forecast values using yield_inspection_view
    FIXED: Added proper 2 decimal place rounding for all numeric values
    NEW: Added limit_actuals_to_planned checkbox to filter only planned villages
    """
    if not season_id:
        season_id = "RABI_25_26"

    valid_types = ["actual", "forecast"]
    if value_types not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid value type. Must be 'actual' or 'forecast'")

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Build filter conditions
        conditions = []
        params = {}

        conditions.append("season ILIKE %(season)s")
        params["season"] = f"%{season_id}%"

        if crop_id:
            conditions.append("crop ILIKE %(crop)s")
            params["crop"] = f"%{crop_id}%"

        if plan_versions:
            conditions.append("plan_revision_version = %(version)s")
            params["version"] = plan_versions

        # NEW: Limit actuals to planned villages (production_allocation > 0)
        if limit_actuals_to_planned:
            conditions.append("production_allocation > 0")

        condition_str = ""
        if conditions:
            condition_str = " AND " + " AND ".join(conditions)

        # Build query parts
        query_parts = []

        # Plan data CTE with ROUND to 2 decimal places
        plan_cte = f"""
        plan_data AS (
            SELECT 
                season,
                crop,
                variety,
                ROUND(SUM(COALESCE(production_allocation, 0))::NUMERIC, 2) as plan_value
            FROM operations_demo.supply_chain_planning
            WHERE production_allocation IS NOT NULL{condition_str}
            GROUP BY season, crop, variety
        )"""
        query_parts.append(plan_cte)

        # Target data CTE - aggregate target_kgs from operations_demo.targets
        # Match by plan_year, season_id, crop_id, state, variety_id
        # Join with lookup tables to get season, crop, variety names for matching
        # Aggregate across states and plan_years since output groups by season, crop, variety
        target_conditions = []
        target_conditions.append("t.target_kgs IS NOT NULL")
        target_conditions.append("s.season_name IS NOT NULL")
        target_conditions.append("s.season_name ILIKE %(season)s")
        if crop_id:
            target_conditions.append("c.crop_name ILIKE %(crop)s")
        
        target_where_clause = " AND ".join(target_conditions) if target_conditions else "1=1"
        
        target_cte = f"""
        target_data AS (
            SELECT 
                COALESCE(s.season_name, '') as season,
                COALESCE(c.crop_name, '') as crop,
                COALESCE(v.variety_name, '') as variety,
                ROUND(SUM(COALESCE(t.target_kgs, 0))::NUMERIC, 2) as target_value
            FROM operations_demo.targets t
            LEFT JOIN operations_demo.seasons s ON t.season_id = s.season_id
            LEFT JOIN operations_demo.crops c ON t.crop_id = c.crop_id
            LEFT JOIN operations_demo.varieties v ON t.variety_id = v.variety_id
            WHERE {target_where_clause}
            GROUP BY s.season_name, c.crop_name, v.variety_name
        )"""
        query_parts.append(target_cte)

        if value_types == "actual":
            # Actual data CTE with ROUND to 2 decimal places
            actual_cte = f"""
            actual_data AS (
                SELECT 
                    season,
                    crop,
                    variety,
                    ROUND(SUM(COALESCE(actual_received_qty, 0))::NUMERIC, 2) as actual_value
                FROM operations_demo.supply_chain_planning
                WHERE actual_received_qty IS NOT NULL{condition_str}
                GROUP BY season, crop, variety
            )"""
            query_parts.append(actual_cte)
        else:  # forecast
            # Forecast data CTE with ROUND to 2 decimal places
            forecast_cte = f"""
            forecast_data AS (
                SELECT 
                    v.season_name as season,
                    v.crop_name as crop,
                    v.variety_name as variety,
                    ROUND(
                        SUM(
                            CASE 
                                WHEN v.stage_forecast2 IS NOT NULL AND v.stage_forecast2 > 0 
                                    THEN v.stage_forecast2
                                WHEN v.stage_forecast1 IS NOT NULL AND v.stage_forecast1 > 0 
                                    THEN v.stage_forecast1
                                ELSE 0
                            END
                        )::NUMERIC, 2
                    ) as forecast_value
                FROM operations_demo.yield_inspection_view v
                WHERE (v.stage_forecast1 IS NOT NULL OR v.stage_forecast2 IS NOT NULL)
                    AND v.season_name ILIKE %(season)s
                    {f"AND v.crop_name ILIKE %(crop)s" if crop_id else ""}
                GROUP BY v.season_name, v.crop_name, v.variety_name
                HAVING ROUND(
                    SUM(
                        CASE 
                            WHEN v.stage_forecast2 IS NOT NULL AND v.stage_forecast2 > 0 
                                THEN v.stage_forecast2
                            WHEN v.stage_forecast1 IS NOT NULL AND v.stage_forecast1 > 0 
                                THEN v.stage_forecast1
                            ELSE 0
                        END
                    )::NUMERIC, 2
                ) > 0
            )"""
            query_parts.append(forecast_cte)

        # Main SELECT with ROUND to 2 decimal places
        if value_types == "actual":
            main_select = """
            SELECT 
                COALESCE(p.season, a.season, t.season) as season,
                COALESCE(p.crop, a.crop, t.crop) as crop,
                COALESCE(p.variety, a.variety, t.variety) as variety,
                ROUND(COALESCE(p.plan_value, 0)::NUMERIC, 2) as plan_value,
                ROUND(COALESCE(a.actual_value, 0)::NUMERIC, 2) as actual_value,
                ROUND(COALESCE(t.target_value, 0)::NUMERIC, 2) as target_value
            FROM plan_data p
            FULL OUTER JOIN actual_data a ON (
                UPPER(TRIM(p.season)) = UPPER(TRIM(a.season)) AND 
                UPPER(TRIM(p.crop)) = UPPER(TRIM(a.crop)) AND 
                UPPER(TRIM(p.variety)) = UPPER(TRIM(a.variety))
            )
            FULL OUTER JOIN target_data t ON (
                UPPER(TRIM(COALESCE(p.season, a.season))) = UPPER(TRIM(t.season)) AND 
                UPPER(TRIM(COALESCE(p.crop, a.crop))) = UPPER(TRIM(t.crop)) AND 
                UPPER(TRIM(COALESCE(p.variety, a.variety))) = UPPER(TRIM(t.variety))
            )
            WHERE (COALESCE(p.season, a.season, t.season) IS NOT NULL)
            ORDER BY season, crop, variety
            LIMIT %(limit)s OFFSET %(offset)s
            """
        else:  # forecast
            main_select = """
            SELECT 
                COALESCE(p.season, f.season, t.season) as season,
                COALESCE(p.crop, f.crop, t.crop) as crop,
                COALESCE(p.variety, f.variety, t.variety) as variety,
                ROUND(COALESCE(p.plan_value, 0)::NUMERIC, 2) as plan_value,
                ROUND(COALESCE(f.forecast_value, 0)::NUMERIC, 2) as forecast_value,
                ROUND(COALESCE(t.target_value, 0)::NUMERIC, 2) as target_value
            FROM plan_data p
            FULL OUTER JOIN forecast_data f ON (
                UPPER(TRIM(p.season)) = UPPER(TRIM(f.season)) AND 
                UPPER(TRIM(p.crop)) = UPPER(TRIM(f.crop)) AND 
                UPPER(TRIM(p.variety)) = UPPER(TRIM(f.variety))
            )
            FULL OUTER JOIN target_data t ON (
                UPPER(TRIM(COALESCE(p.season, f.season))) = UPPER(TRIM(t.season)) AND 
                UPPER(TRIM(COALESCE(p.crop, f.crop))) = UPPER(TRIM(t.crop)) AND 
                UPPER(TRIM(COALESCE(p.variety, f.variety))) = UPPER(TRIM(t.variety))
            )
            WHERE (COALESCE(p.season, f.season, t.season) IS NOT NULL)
            ORDER BY season, crop, variety
            LIMIT %(limit)s OFFSET %(offset)s
            """

        # Execute query
        full_query = "WITH " + ",\n".join(query_parts) + "\n" + main_select
        params.update({"limit": limit, "offset": offset})

        cur.execute(full_query, params)
        rows = cur.fetchall()

        # Process results with additional Python-side rounding for safety
        processed_rows = []
        for row in rows:
            row_dict = {
                'season': row['season'],
                'crop': row['crop'],
                'variety': row['variety'],
                'plan_value': round(safe_float_convert(row.get('plan_value', 0)), 2),
                'target_value': round(safe_float_convert(row.get('target_value', 0)), 2)
            }

            if value_types == "actual":
                row_dict['actual_value'] = round(safe_float_convert(row.get('actual_value', 0)), 2)
            else:
                row_dict['forecast_value'] = round(safe_float_convert(row.get('forecast_value', 0)), 2)

            processed_rows.append(row_dict)

        # Get available forecast seasons
        available_seasons = []
        try:
            cur.execute("""
                SELECT DISTINCT season_name 
                FROM operations_demo.yield_inspection_view 
                WHERE stage_forecast1 IS NOT NULL OR stage_forecast2 IS NOT NULL
                ORDER BY season_name
            """)
            available_seasons = [row[0] for row in cur.fetchall()]
        except:
            available_seasons = ["Error fetching seasons"]

        # Column metadata
        columns = [
            {"db_column_name": "season", "display_name": "Season", "type": "string", "is_visible": True},
            {"db_column_name": "crop", "display_name": "Crop", "type": "string", "is_visible": True},
            {"db_column_name": "variety", "display_name": "Variety", "type": "string", "is_visible": True},
            {"db_column_name": "plan_value", "display_name": "Plan Value", "type": "decimal", "is_visible": True},
            {"db_column_name": "target_value", "display_name": "Target Value", "type": "decimal", "is_visible": True}
        ]

        if value_types == "actual":
            columns.append({
                "db_column_name": "actual_value",
                "display_name": "Actual Value",
                "type": "decimal",
                "is_visible": True
            })
        else:
            columns.append({
                "db_column_name": "forecast_value",
                "display_name": "Latest Forecast Value",
                "type": "decimal",
                "is_visible": True
            })

        return {
            "metadata": {
                "season_used": season_id,
                "is_default_season": season_id == "RABI_24_25",
                "total_records": len(processed_rows),
                "data_type": value_types,
                "limit_actuals_to_planned": limit_actuals_to_planned,  # NEW: expose checkbox state
                "available_forecast_seasons": available_seasons,
                "data_source": "yield_inspection_view"
            },
            "columns": columns,
            "data": processed_rows
        }

    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error fetching comparison data: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)



# FIXED: Enhanced safe_float_convert function with rounding
# def safe_float_convert(value, decimal_places=2):
#     """
#     Safely convert value to float and round to specified decimal places
#     """
#     try:
#         if value is None:
#             return 0.0
#         if isinstance(value, (int, float)):
#             return round(float(value), decimal_places)
#         if isinstance(value, str):
#             if value.strip() == '':
#                 return 0.0
#             return round(float(value.strip()), decimal_places)
#         return round(float(value), decimal_places)
#     except (ValueError, TypeError):
#         return 0.0


@router.get("/seed-forecast/comparison-summary")
def get_comparison_summary(
        season: Optional[str] = Query(None),
        crop: Optional[str] = Query(None),
        variety: Optional[str] = Query(None),
        plan_revision_version: Optional[str] = Query("v1.0"),
        comparison_type_1: str = Query("actual"),
        comparison_type_2: str = Query("plan")
):
    """
    Get high-level summary statistics for the comparison
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Get aggregated summary
        summary_query = """
        WITH comparison_data AS (
            SELECT 
                CASE WHEN %s = 'actual' THEN COALESCE(scp.actual_received_qty, 0)
                     WHEN %s = 'plan' THEN COALESCE(scp.production_allocation, 0)
                     ELSE 0 END as value_1,
                CASE WHEN %s = 'actual' THEN COALESCE(scp.actual_received_qty, 0)
                     WHEN %s = 'plan' THEN COALESCE(scp.production_allocation, 0)
                     ELSE 0 END as value_2
            FROM operations_demo.supply_chain_planning scp
            WHERE 1=1
        """

        conditions = []
        params = [comparison_type_1, comparison_type_1, comparison_type_2, comparison_type_2]

        if season:
            conditions.append("scp.season ILIKE %s")
            params.append(f"%{season}%")
        if crop:
            conditions.append("scp.crop ILIKE %s")
            params.append(f"%{crop}%")
        if variety:
            conditions.append("scp.variety ILIKE %s")
            params.append(f"%{variety}%")
        if plan_revision_version:
            conditions.append("scp.plan_revision_version = %s")
            params.append(plan_revision_version)

        if conditions:
            summary_query += " AND " + " AND ".join(conditions)

        summary_query += """
        )
        SELECT 
            COUNT(*) as total_records,
            SUM(value_1) as total_value_1,
            SUM(value_2) as total_value_2,
            AVG(value_1) as avg_value_1,
            AVG(value_2) as avg_value_2,
            SUM(value_1 - value_2) as total_variance,
            AVG(CASE WHEN value_2 != 0 THEN ((value_1 - value_2) / value_2) * 100 ELSE 0 END) as avg_percentage_diff
        FROM comparison_data
        """

        cur.execute(summary_query, params)
        summary = cur.fetchone()

        return {
            "summary": {
                "total_records": int(summary['total_records'] or 0),
                "total_value_1": safe_float_convert(summary['total_value_1']),
                "total_value_2": safe_float_convert(summary['total_value_2']),
                "avg_value_1": safe_float_convert(summary['avg_value_1']),
                "avg_value_2": safe_float_convert(summary['avg_value_2']),
                "total_variance": safe_float_convert(summary['total_variance']),
                "avg_percentage_difference": safe_float_convert(summary['avg_percentage_diff']),
                "comparison_type_1": comparison_type_1,
                "comparison_type_2": comparison_type_2
            }
        }

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching summary: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)


@router.get("/seed-forecast/state-variety-summary/dropdown")
def get_state_variety_summary_dropdown(
    plan_revision_version: Optional[str] = Query(None, description="Plan revision version filter (optional)"),
    season: Optional[str] = Query(None, description="Season filter (requires plan_revision_version)"),
    crop: Optional[str] = Query(None, description="Crop filter (requires season)"),
    state: Optional[str] = Query(None, description="State filter (requires crop)"),
    variety: Optional[str] = Query(None, description="Variety filter (requires state)")
):
    """
    Get dropdown options for state-variety-summary filters
    Cascading dropdown sequence: plan_revision_version → season → crop → state → variety → deviation_type
    Each dropdown includes "ALL" as the first option
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        result = {}

        # Step 1: Plan revision version dropdown (always show if not selected)
        if not plan_revision_version:
            cur.execute("""
                SELECT DISTINCT plan_revision_version
                FROM operations_demo.supply_chain_vs_yield_view
                WHERE plan_revision_version IS NOT NULL AND plan_revision_version != ''
                ORDER BY plan_revision_version DESC
            """)
            versions = [row['plan_revision_version'] for row in cur.fetchall()]
            result["plan_revision_version"] = versions

        # Step 2: Season dropdown (show if plan_revision_version is selected)
        if plan_revision_version and not season:
            query_params = {}
            query_conditions = []
            
            if plan_revision_version and plan_revision_version != "ALL":
                query_conditions.append("plan_revision_version = %(plan_revision_version)s")
                query_params["plan_revision_version"] = plan_revision_version
            
            where_clause = " AND " + " AND ".join(query_conditions) if query_conditions else ""
            
            cur.execute(f"""
                SELECT DISTINCT season
                FROM operations_demo.supply_chain_vs_yield_view
                WHERE 1=1 {where_clause}
                  AND season IS NOT NULL AND season != ''
                ORDER BY season DESC
            """, query_params)
            seasons = ["ALL"] + [row['season'] for row in cur.fetchall()]
            result["season"] = seasons

        # Step 3: Crop dropdown (show if season is selected)
        if season and not crop:
            query_params = {}
            query_conditions = []
            
            if plan_revision_version and plan_revision_version != "ALL":
                query_conditions.append("plan_revision_version = %(plan_revision_version)s")
                query_params["plan_revision_version"] = plan_revision_version
            if season and season != "ALL":
                query_conditions.append("season = %(season)s")
                query_params["season"] = season
            
            where_clause = " AND " + " AND ".join(query_conditions) if query_conditions else ""
            
            cur.execute(f"""
                SELECT DISTINCT crop
                FROM operations_demo.supply_chain_vs_yield_view
                WHERE 1=1 {where_clause}
                  AND crop IS NOT NULL AND crop != ''
                ORDER BY crop
            """, query_params)
            crops = ["ALL"] + [row['crop'] for row in cur.fetchall()]
            result["crop"] = crops

        # Step 4: State dropdown (show if crop is selected)
        if crop and not state:
            query_params = {}
            query_conditions = []
            
            if plan_revision_version and plan_revision_version != "ALL":
                query_conditions.append("plan_revision_version = %(plan_revision_version)s")
                query_params["plan_revision_version"] = plan_revision_version
            if season and season != "ALL":
                query_conditions.append("season = %(season)s")
                query_params["season"] = season
            if crop and crop != "ALL":
                query_conditions.append("crop = %(crop)s")
                query_params["crop"] = crop
            
            where_clause = " AND " + " AND ".join(query_conditions) if query_conditions else ""
            
            cur.execute(f"""
                SELECT DISTINCT state
                FROM operations_demo.supply_chain_vs_yield_view
                WHERE 1=1 {where_clause}
                  AND state IS NOT NULL AND state != ''
                ORDER BY state
            """, query_params)
            states = ["ALL"] + [row['state'] for row in cur.fetchall()]
            result["state"] = states

        # Step 5: Variety dropdown (show if state is selected)
        if state and not variety:
            query_params = {}
            query_conditions = []
            
            if plan_revision_version and plan_revision_version != "ALL":
                query_conditions.append("plan_revision_version = %(plan_revision_version)s")
                query_params["plan_revision_version"] = plan_revision_version
            if season and season != "ALL":
                query_conditions.append("season = %(season)s")
                query_params["season"] = season
            if crop and crop != "ALL":
                query_conditions.append("crop = %(crop)s")
                query_params["crop"] = crop
            if state and state != "ALL":
                query_conditions.append("state = %(state)s")
                query_params["state"] = state
            
            where_clause = " AND " + " AND ".join(query_conditions) if query_conditions else ""
            
            cur.execute(f"""
                SELECT DISTINCT variety
                FROM operations_demo.supply_chain_vs_yield_view
                WHERE 1=1 {where_clause}
                  AND variety IS NOT NULL AND variety != ''
                ORDER BY variety
            """, query_params)
            varieties = ["ALL"] + [row['variety'] for row in cur.fetchall()]
            result["variety"] = varieties

        # Step 6: Deviation_type dropdown (show if variety is selected)
        if variety:
            result["deviation_type"] = [
                "actual_vs_target",
                "plan_vs_target",
                "actual_vs_plan"
            ]

        return result

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching dropdown options: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)


@router.get("/seed-forecast/state-variety-summary")
def get_state_variety_summary(
    plan_revision_version: Optional[str] = Query(None, description="Plan revision version filter (required)"),
    season: Optional[str] = Query(None, description="Season filter (requires plan_revision_version)"),
    crop: Optional[str] = Query(None, description="Crop filter (requires season)"),
    state: Optional[str] = Query(None, description="State filter (requires crop)"),
    variety: Optional[str] = Query(None, description="Variety filter (requires state)"),
    deviation_type: Optional[str] = Query(None, description="Deviation calculation type: actual_vs_target, plan_vs_target, or actual_vs_plan"),
    limit_actuals_to_planned: bool = Query(
        False,
        description="If true, only include records where planned production_allocation > 0 (limits actuals to planned villages)"
    )
):
    """
    Get state-variety summary with planning vs actual values
    Cascading filter flow: plan_revision_version → season → crop → state → variety → deviation_type
    All filters follow strict sequence - no hardcoded defaults
    Each filter includes "ALL" option from dropdown
    
    Response structure includes column metadata and data array
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Set defaults for deviation_type if not provided
        if not deviation_type:
            deviation_type = "actual_vs_target"

        # Validate deviation_type
        valid_deviation_types = ["actual_vs_target", "plan_vs_target", "actual_vs_plan"]
        if deviation_type not in valid_deviation_types:
            raise HTTPException(status_code=400, detail=f"Invalid deviation_type. Must be one of: {', '.join(valid_deviation_types)}")

        # If no filters are provided, load default metadata summary
        # Filters follow cascading sequence: plan_revision_version → season → crop → state → variety
        if not plan_revision_version and not season and not crop and not state and not variety:
            # Load default aggregated data grouped by state and variety
            # Build default query without filters
            where_conditions = [
                "state IS NOT NULL AND state != ''",
                "variety IS NOT NULL AND variety != ''",
                "(production_allocation > 0 OR actual_received_qty > 0)"
            ]
            
            if limit_actuals_to_planned:
                where_conditions.append("production_allocation > 0")
            
            where_clause = " AND ".join(where_conditions)
            
            # Build target_data CTE for default query
            target_conditions = ["t.target_kgs IS NOT NULL"]
            target_where_clause = " AND ".join(target_conditions)
            
            target_cte = f"""
            target_data AS (
                SELECT 
                    UPPER(TRIM(COALESCE(t.state, ''))) as state,
                    UPPER(TRIM(COALESCE(v.variety_name, ''))) as variety,
                    ROUND(SUM(COALESCE(t.target_kgs, 0))::NUMERIC, 2) as target_value
                FROM operations_demo.targets t
                LEFT JOIN operations_demo.seasons s ON t.season_id = s.season_id
                LEFT JOIN operations_demo.crops c ON t.crop_id = c.crop_id
                LEFT JOIN operations_demo.varieties v ON t.variety_id = v.variety_id
                WHERE {target_where_clause}
                    AND t.state IS NOT NULL 
                    AND t.state != ''
                    AND v.variety_name IS NOT NULL
                    AND v.variety_name != ''
                GROUP BY UPPER(TRIM(COALESCE(t.state, ''))), UPPER(TRIM(COALESCE(v.variety_name, '')))
            )"""
            
            # Build SELECT clause for default query
            select_parts = ["m.state", "m.variety"]
            select_parts.extend([
                "m.plan_value", 
                "m.actual_value", 
                "COALESCE(t.target_value, 0) as target_value",
                """CASE
                    WHEN %(deviation_type)s = 'actual_vs_target' AND COALESCE(t.target_value, 0) > 0
                        THEN ROUND(((m.actual_value - COALESCE(t.target_value, 0)) / COALESCE(t.target_value, 0)) * 100::NUMERIC, 2)
                    WHEN %(deviation_type)s = 'plan_vs_target' AND COALESCE(t.target_value, 0) > 0
                        THEN ROUND(((m.plan_value - COALESCE(t.target_value, 0)) / COALESCE(t.target_value, 0)) * 100::NUMERIC, 2)
                    WHEN %(deviation_type)s = 'actual_vs_plan' AND COALESCE(m.plan_value, 0) > 0
                        THEN ROUND(((m.actual_value - m.plan_value) / m.plan_value) * 100::NUMERIC, 2)
                    ELSE 0
                END as deviation_pct"""
            ])
            select_clause_final = ", ".join(select_parts)
            
            # Default query
            default_query = f"""
            WITH {target_cte},
            main_data AS (
                SELECT 
                    state,
                    variety,
                    SUM(COALESCE(production_allocation, 0)) as plan_value,
                    SUM(COALESCE(actual_received_qty, 0)) as actual_value
                FROM operations_demo.supply_chain_vs_yield_view
                WHERE {where_clause}
                GROUP BY state, variety
            )
            SELECT 
                {select_clause_final}
            FROM main_data m
            LEFT JOIN target_data t ON (
                UPPER(TRIM(m.state)) = t.state AND 
                UPPER(TRIM(m.variety)) = t.variety
            )
            ORDER BY m.state, m.variety
            LIMIT 1000
            """
            
            params = {"deviation_type": deviation_type}
            cur.execute(default_query, params)
            rows = cur.fetchall()
            
            # Process default results
            processed_rows = []
            for row in rows:
                deviation_pct_value = row.get('deviation_pct')
                if deviation_pct_value is not None:
                    deviation_pct_value = round(safe_float_convert(deviation_pct_value), 2)
                else:
                    deviation_pct_value = 0.0
                
                row_dict = {
                    'state': row['state'],
                    'variety': row['variety'],
                    'plan_value': safe_float_convert(row['plan_value']),
                    'actual_value': safe_float_convert(row['actual_value']),
                    'target_value': safe_float_convert(row.get('target_value', 0)),
                    'deviation_pct': deviation_pct_value
                }
                processed_rows.append(row_dict)
            
            # Build columns based on deviation_type (same logic as below)
            columns = [
                {
                    "db_column_name": "state",
                    "display_name": "State",
                    "type": "string",
                    "group_name": "Location",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "variety",
                    "display_name": "Variety",
                    "type": "string",
                    "group_name": "Basic Info",
                    "is_visible": True,
                    "is_editable": False
                }
            ]
            
            # Add columns based on deviation_type
            if deviation_type == "actual_vs_target":
                columns.extend([
                    {
                        "db_column_name": "actual_value",
                        "display_name": "Actual Received Qty",
                        "type": "decimal",
                        "group_name": "Actual",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "target_value",
                        "display_name": "Target Value",
                        "type": "decimal",
                        "group_name": "Target",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "plan_value",
                        "display_name": "Planned Production",
                        "type": "decimal",
                        "group_name": "Planning",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "deviation_pct",
                        "display_name": "Deviation (%)",
                        "type": "decimal",
                        "group_name": "Analysis",
                        "is_visible": True,
                        "is_editable": False
                    }
                ])
            elif deviation_type == "plan_vs_target":
                columns.extend([
                    {
                        "db_column_name": "plan_value",
                        "display_name": "Planned Production",
                        "type": "decimal",
                        "group_name": "Planning",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "target_value",
                        "display_name": "Target Value",
                        "type": "decimal",
                        "group_name": "Target",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "actual_value",
                        "display_name": "Actual Received Qty",
                        "type": "decimal",
                        "group_name": "Actual",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "deviation_pct",
                        "display_name": "Deviation (%)",
                        "type": "decimal",
                        "group_name": "Analysis",
                        "is_visible": True,
                        "is_editable": False
                    }
                ])
            else:  # actual_vs_plan
                columns.extend([
                    {
                        "db_column_name": "actual_value",
                        "display_name": "Actual Received Qty",
                        "type": "decimal",
                        "group_name": "Actual",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "plan_value",
                        "display_name": "Planned Production",
                        "type": "decimal",
                        "group_name": "Planning",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "target_value",
                        "display_name": "Target Value",
                        "type": "decimal",
                        "group_name": "Target",
                        "is_visible": True,
                        "is_editable": False
                    },
                    {
                        "db_column_name": "deviation_pct",
                        "display_name": "Deviation (%)",
                        "type": "decimal",
                        "group_name": "Analysis",
                        "is_visible": True,
                        "is_editable": False
                    }
                ])
            
            metadata = {
                "total_records": len(processed_rows),
                "limit_actuals_to_planned": limit_actuals_to_planned,
                "filters_applied": {
                    "plan_revision_version": None,
                    "season": None,
                    "crop": None,
                    "state": None,
                    "variety": None,
                    "deviation_type": deviation_type
                },
                "is_default_summary": True
            }
            
            return {
                "metadata": metadata,
                "columns": columns,
                "data": processed_rows
            }

        # Determine GROUP BY fields - always group by state and variety
        # Follows same flow as dropdown: season → crop → state → variety
        group_by_fields = ["state", "variety"]
        select_fields = ["state", "variety"]
        include_village = False

        # Build main query with dynamic WHERE and GROUP BY
        select_clause = ", ".join(select_fields)
        group_by_clause = ", ".join(group_by_fields)

        # Build WHERE conditions dynamically
        # Follow cascading filter flow: plan_revision_version → season → crop → state → variety
        # Handle "ALL" values from dropdown (means no filter applied for that field)
        where_conditions = [
            "state IS NOT NULL AND state != ''",
            "variety IS NOT NULL AND variety != ''",
            "(production_allocation > 0 OR actual_received_qty > 0)"
        ]
        
        # Add plan_revision_version filter (required, but can be "ALL")
        # Use UPPER(TRIM()) for case-insensitive and whitespace-tolerant matching
        if plan_revision_version and plan_revision_version != "ALL":
            where_conditions.append("UPPER(TRIM(plan_revision_version)) = UPPER(TRIM(%(version)s))")
        
        # Add season filter (required, but can be "ALL")
        # Use UPPER(TRIM()) for case-insensitive and whitespace-tolerant matching
        if season and season != "ALL":
            where_conditions.append("UPPER(TRIM(season)) = UPPER(TRIM(%(season)s))")
        
        # Add optional filters only if they are provided and not "ALL"
        # Use UPPER(TRIM()) for case-insensitive and whitespace-tolerant matching
        if crop and crop != "ALL":
            where_conditions.append("UPPER(TRIM(crop)) = UPPER(TRIM(%(crop)s))")
        if state and state != "ALL":
            where_conditions.append("UPPER(TRIM(state)) = UPPER(TRIM(%(state)s))")
        if variety and variety != "ALL":
            where_conditions.append("UPPER(TRIM(variety)) = UPPER(TRIM(%(variety)s))")
        
        # NEW: Limit actuals to planned villages (production_allocation > 0)
        if limit_actuals_to_planned:
            where_conditions.append("production_allocation > 0")
        
        where_clause = " AND ".join(where_conditions)
        
        # Build target_data CTE - aggregate target_kgs from operations_demo.targets
        # Always aggregate at state + variety level for the selected season/crop
        # Match by season_id and crop_id only (not state/variety filters)
        # Use UPPER(TRIM()) for state and variety to ensure exact matching with main_data
        target_conditions = []
        target_conditions.append("t.target_kgs IS NOT NULL")
        # Only filter by season if season is provided and not "ALL"
        # Use UPPER(TRIM()) for case-insensitive and whitespace-tolerant matching
        if season and season != "ALL":
            target_conditions.append("UPPER(TRIM(s.season_name)) = UPPER(TRIM(%(season)s))")
        # Only filter by crop if crop is provided and not "ALL"
        if crop and crop != "ALL":
            target_conditions.append("UPPER(TRIM(c.crop_name)) = UPPER(TRIM(%(crop)s))")
        
        target_where_clause = " AND ".join(target_conditions)
        
        target_cte = f"""
        target_data AS (
            SELECT 
                UPPER(TRIM(COALESCE(t.state, ''))) as state,
                UPPER(TRIM(COALESCE(v.variety_name, ''))) as variety,
                ROUND(SUM(COALESCE(t.target_kgs, 0))::NUMERIC, 2) as target_value
            FROM operations_demo.targets t
            LEFT JOIN operations_demo.seasons s ON t.season_id = s.season_id
            LEFT JOIN operations_demo.crops c ON t.crop_id = c.crop_id
            LEFT JOIN operations_demo.varieties v ON t.variety_id = v.variety_id
            WHERE {target_where_clause}
                AND t.state IS NOT NULL 
                AND t.state != ''
                AND v.variety_name IS NOT NULL
                AND v.variety_name != ''
            GROUP BY UPPER(TRIM(COALESCE(t.state, ''))), UPPER(TRIM(COALESCE(v.variety_name, '')))
        )"""
        
        # Build SELECT clause for final query
        # Note: Use original state/variety from main_data (not UPPER/TRIM) for display
        # Calculate deviation (%) based on deviation_type: ((base_value - reference_value) / reference_value) * 100
        select_parts = [f"m.{field}" for field in select_fields]
        select_parts.extend([
            "m.plan_value", 
            "m.actual_value", 
            "COALESCE(t.target_value, 0) as target_value",
            """CASE
                WHEN %(deviation_type)s = 'actual_vs_target' AND COALESCE(t.target_value, 0) > 0
                    THEN ROUND(((m.actual_value - COALESCE(t.target_value, 0)) / COALESCE(t.target_value, 0)) * 100::NUMERIC, 2)
                WHEN %(deviation_type)s = 'plan_vs_target' AND COALESCE(t.target_value, 0) > 0
                    THEN ROUND(((m.plan_value - COALESCE(t.target_value, 0)) / COALESCE(t.target_value, 0)) * 100::NUMERIC, 2)
                WHEN %(deviation_type)s = 'actual_vs_plan' AND COALESCE(m.plan_value, 0) > 0
                    THEN ROUND(((m.actual_value - m.plan_value) / m.plan_value) * 100::NUMERIC, 2)
                ELSE NULL
            END as deviation_pct"""
        ])
        select_clause_final = ", ".join(select_parts)
        
        # Main query using CTE with LEFT JOIN for target_data
        # JOIN uses UPPER(TRIM()) to ensure exact matching regardless of case/whitespace
        main_query = f"""
        WITH {target_cte},
        main_data AS (
            SELECT 
                {select_clause},
                SUM(COALESCE(production_allocation, 0)) as plan_value,
                SUM(COALESCE(actual_received_qty, 0)) as actual_value
            FROM operations_demo.supply_chain_vs_yield_view
            WHERE {where_clause}
            GROUP BY {group_by_clause}
        )
        SELECT 
            {select_clause_final}
        FROM main_data m
        LEFT JOIN target_data t ON (
            UPPER(TRIM(m.state)) = t.state AND 
            UPPER(TRIM(m.variety)) = t.variety
        )
        ORDER BY {", ".join([f"m.{field}" for field in group_by_fields])}
        """

        params = {
            "deviation_type": deviation_type
        }
        
        # Add filter params only if they are provided and not "ALL"
        if plan_revision_version and plan_revision_version != "ALL":
            params["version"] = plan_revision_version
        if season and season != "ALL":
            params["season"] = season
        if crop and crop != "ALL":
            params["crop"] = crop
        if state and state != "ALL":
            params["state"] = state
        if variety and variety != "ALL":
            params["variety"] = variety

        cur.execute(main_query, params)
        rows = cur.fetchall()

        # Process the results
        processed_rows = []
        for row in rows:
            # Handle deviation_pct: convert NULL to 0
            deviation_pct_value = row.get('deviation_pct')
            if deviation_pct_value is not None:
                deviation_pct_value = round(safe_float_convert(deviation_pct_value), 2)
            else:
                deviation_pct_value = 0.0
            
            row_dict = {
                'state': row['state'],
                'variety': row['variety'],
                'plan_value': safe_float_convert(row['plan_value']),
                'actual_value': safe_float_convert(row['actual_value']),
                'target_value': safe_float_convert(row.get('target_value', 0)),
                'deviation_pct': deviation_pct_value
            }
            # Only include village if it's in the group by
            if include_village:
                row_dict['village'] = row['village'] if row['village'] else None
            else:
                row_dict['village'] = None
            processed_rows.append(row_dict)

        # Column metadata
        columns = [
            {
                "db_column_name": "state",
                "display_name": "State",
                "type": "string",
                "group_name": "Location",
                "is_visible": True,
                "is_editable": False
            },
            {
                "db_column_name": "variety",
                "display_name": "Variety",
                "type": "string",
                "group_name": "Basic Info",
                "is_visible": True,
                "is_editable": False
            }
        ]
        
        # Add village column only when it's included in the results
        if include_village:
            columns.append({
                "db_column_name": "village",
                "display_name": "Village",
                "type": "string",
                "group_name": "Location",
                "is_visible": True,
                "is_editable": False
            })
        
        # Order columns based on deviation_type for heatmap
        # Heatmap fields should follow deviation type selection
        if deviation_type == "actual_vs_target":
            # For actual_vs_target: show actual_value, target_value, then plan_value
            columns.extend([
                {
                    "db_column_name": "actual_value",
                    "display_name": "Actual Received Qty",
                    "type": "decimal",
                    "group_name": "Actual",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "target_value",
                    "display_name": "Target Value",
                    "type": "decimal",
                    "group_name": "Target",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "plan_value",
                    "display_name": "Planned Production",
                    "type": "decimal",
                    "group_name": "Planning",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "deviation_pct",
                    "display_name": "Deviation (%)",
                    "type": "decimal",
                    "group_name": "Analysis",
                    "is_visible": True,
                    "is_editable": False
                }
            ])
        elif deviation_type == "plan_vs_target":
            # For plan_vs_target: show plan_value, target_value, then actual_value
            columns.extend([
                {
                    "db_column_name": "plan_value",
                    "display_name": "Planned Production",
                    "type": "decimal",
                    "group_name": "Planning",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "target_value",
                    "display_name": "Target Value",
                    "type": "decimal",
                    "group_name": "Target",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "actual_value",
                    "display_name": "Actual Received Qty",
                    "type": "decimal",
                    "group_name": "Actual",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "deviation_pct",
                    "display_name": "Deviation (%)",
                    "type": "decimal",
                    "group_name": "Analysis",
                    "is_visible": True,
                    "is_editable": False
                }
            ])
        else:  # actual_vs_plan
            # For actual_vs_plan: show actual_value, plan_value, then target_value
            columns.extend([
                {
                    "db_column_name": "actual_value",
                    "display_name": "Actual Received Qty",
                    "type": "decimal",
                    "group_name": "Actual",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "plan_value",
                    "display_name": "Planned Production",
                    "type": "decimal",
                    "group_name": "Planning",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "target_value",
                    "display_name": "Target Value",
                    "type": "decimal",
                    "group_name": "Target",
                    "is_visible": True,
                    "is_editable": False
                },
                {
                    "db_column_name": "deviation_pct",
                    "display_name": "Deviation (%)",
                    "type": "decimal",
                    "group_name": "Analysis",
                    "is_visible": True,
                    "is_editable": False
                }
            ])

        # Build metadata - only include actual values used in query, not hardcoded defaults
        metadata = {
            "total_records": len(processed_rows),
            "limit_actuals_to_planned": limit_actuals_to_planned,
            "filters_applied": {
                "plan_revision_version": plan_revision_version if plan_revision_version else None,
                "season": season if season else None,
                "crop": crop if crop else None,
                "state": state if state else None,
                "variety": variety if variety else None,
                "deviation_type": deviation_type
            }
        }
        
        return {
            "metadata": metadata,
            "columns": columns,
            "data": processed_rows
        }

    except HTTPException:
        raise
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        if conn:
            conn.rollback()
        import traceback
        error_detail = str(e) if str(e) else repr(e)
        raise HTTPException(status_code=500, detail=f"Error fetching state variety summary: {error_detail}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)

@router.get("/seed-forecast/tp-options")
def get_tp_filtered_options(
        season_id: Optional[str] = Query(None),
        crop_id: Optional[str] = Query(None),
        state: Optional[str] = Query(None),
        db: Session = Depends(get_db)
):
    """
    Cascading dropdown for TP aging analysis with consistent id/name format
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        result = {}

        if not season_id:
            # Step 1: Return all seasons
            cur.execute("""
                SELECT DISTINCT season, season_id
                FROM operations_demo.season_crop_yield_view
                WHERE season IS NOT NULL AND season != ''
                ORDER BY season DESC
            """)
            seasons_data = cur.fetchall()
            result["season_id"] = [{"id": str(row['season_id']), "name": row['season']} for row in seasons_data]

        elif season_id and not crop_id:
            # Step 2: Return crops for given season
            cur.execute("""
                SELECT DISTINCT crop, crop_id
                FROM operations_demo.season_crop_yield_view
                WHERE season_id = %s 
                AND crop IS NOT NULL AND crop != ''
                ORDER BY crop
            """, (season_id,))
            crops_data = cur.fetchall()
            result["crop_id"] = [{"id": str(row['crop_id']), "name": row['crop']} for row in crops_data]

        elif season_id and crop_id and not state:
            # Step 3: Return states for season + crop (removed "All States")
            cur.execute("""
                SELECT DISTINCT state
                FROM operations_demo.season_crop_yield_view
                WHERE season_id = %s AND crop_id = %s
                AND state IS NOT NULL AND state != ''
                ORDER BY state
            """, (season_id, crop_id))
            states_data = cur.fetchall()
            # Just return actual states, no "All States" option
            result["state"] = [{"id": row['state'], "name": row['state']} for row in states_data]

        elif season_id and crop_id and state:
            # Step 4: Return varieties for season + crop + state
            cur.execute("""
                SELECT DISTINCT variety_id, variety
                FROM operations_demo.season_crop_yield_view
                WHERE season_id = %s AND crop_id = %s AND state = %s
                AND variety IS NOT NULL AND variety != ''
                AND variety_id IS NOT NULL
                ORDER BY variety
            """, (season_id, crop_id, state))

            varieties_data = cur.fetchall()
            # Just return actual varieties, no "All Varieties" option
            result["variety_id"] = [{"id": str(row['variety_id']), "name": row['variety']} for row in varieties_data]

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter combination")

        return result

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching TP options: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)


@router.get("/seed-forecast/tp-aging")
def get_tp_aging_metrics(
        season_id: Optional[str] = Query(None, description="Season identifier (optional)"),
        crop_id: Optional[str] = Query(None, description="Crop identifier (optional)"),
        state: Optional[str] = Query(None, description="State filter (optional)"),
        variety_id: Optional[str] = Query(None, description="Specific variety ID (optional)"),
        group_by_village: bool = Query(False, description="Group metrics by village"),
        limit: int = Query(1000),
        offset: int = Query(0)
):
    """
    Get TP aging metrics - returns all varieties data by default (aggregated across states)
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Build filter conditions dynamically
        conditions = []
        params = []

        # Add season filter only if provided
        if season_id:
            conditions.append("season_id = %s")
            params.append(season_id)

        # Add crop filter only if provided
        if crop_id:
            conditions.append("crop_id = %s")
            params.append(crop_id)

        # Add state filter only if provided (removed "all" check)
        if state:
            conditions.append("state = %s")
            params.append(state)

        # Add variety filter only if provided
        if variety_id:
            conditions.append("variety_id = %s")
            params.append(variety_id)

        # Base WHERE conditions for data quality
        base_conditions = [
            "female_soaking_date IS NOT NULL",
            "female_tp_date IS NOT NULL",
            "female_tp_date > female_soaking_date",
            "variety IS NOT NULL",
            "variety != ''",
            "state IS NOT NULL",
            "state != ''"
        ]

        # Combine all conditions
        all_conditions = base_conditions + conditions
        condition_str = " AND ".join(all_conditions)

        # Base query logic - Updated to return 2 decimal places
        if group_by_village:
            # When grouping by village, include state info
            base_query = f"""
                SELECT 
                    state,
                    village,
                    variety_id,
                    variety,
                    season,
                    crop,
                    DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) AS tp_days,
                    CASE
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 1 AND 20 THEN 'TP 1-20'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 21 AND 25 THEN 'TP 21-25'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 26 AND 30 THEN 'TP 26-30'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 31 AND 40 THEN 'TP 31-40'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 41 AND 50 THEN 'TP 41-50'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 51 AND 60 THEN 'TP 51-60'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) > 60 THEN 'TP Above 60'
                        ELSE 'Unknown'
                    END AS tp_window,
                    ROUND(SUM(COALESCE(net_acres, 0))::numeric, 2) as acres,
                    ROUND(SUM(COALESCE(packed_qt, 0))::numeric, 2) as packed_quantity,
                    CASE 
                        WHEN SUM(COALESCE(net_acres, 0)) > 0 
                        THEN ROUND((SUM(COALESCE(packed_qt, 0)) / SUM(COALESCE(net_acres, 0)))::numeric, 2)
                        ELSE 0.00 
                    END as productivity
                FROM operations_demo.season_crop_yield_view
                WHERE {condition_str}
                GROUP BY 
                    state, village, variety_id, variety, season, crop,
                    DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp),
                    CASE
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 1 AND 20 THEN 'TP 1-20'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 21 AND 25 THEN 'TP 21-25'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 26 AND 30 THEN 'TP 26-30'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 31 AND 40 THEN 'TP 31-40'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 41 AND 50 THEN 'TP 41-50'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 51 AND 60 THEN 'TP 51-60'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) > 60 THEN 'TP Above 60'
                        ELSE 'Unknown'
                    END
                ORDER BY state, village, variety, tp_days
            """
        else:
            # Default: Group by variety only (aggregated across all states unless state is specified)
            base_query = f"""
                SELECT 
                    variety_id,
                    variety,
                    season,
                    crop,
                    DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) AS tp_days,
                    CASE
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 1 AND 20 THEN 'TP 1-20'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 21 AND 25 THEN 'TP 21-25'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 26 AND 30 THEN 'TP 26-30'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 31 AND 40 THEN 'TP 31-40'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 41 AND 50 THEN 'TP 41-50'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 51 AND 60 THEN 'TP 51-60'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) > 60 THEN 'TP Above 60'
                        ELSE 'Unknown'
                    END AS tp_window,
                    ROUND(SUM(COALESCE(net_acres, 0))::numeric, 2) as acres,
                    ROUND(SUM(COALESCE(packed_qt, 0))::numeric, 2) as packed_quantity,
                    CASE 
                        WHEN SUM(COALESCE(net_acres, 0)) > 0 
                        THEN ROUND((SUM(COALESCE(packed_qt, 0)) / SUM(COALESCE(net_acres, 0)))::numeric, 2)
                        ELSE 0.00 
                    END as productivity
                FROM operations_demo.season_crop_yield_view
                WHERE {condition_str}
                GROUP BY 
                    variety_id, variety, season, crop,
                    DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp),
                    CASE
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 1 AND 20 THEN 'TP 1-20'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 21 AND 25 THEN 'TP 21-25'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 26 AND 30 THEN 'TP 26-30'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 31 AND 40 THEN 'TP 31-40'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 41 AND 50 THEN 'TP 41-50'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) BETWEEN 51 AND 60 THEN 'TP 51-60'
                        WHEN DATE_PART('day', female_tp_date::timestamp - female_soaking_date::timestamp) > 60 THEN 'TP Above 60'
                        ELSE 'Unknown'
                    END
                ORDER BY variety, tp_days
            """

        # Add pagination
        base_query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(base_query, params)
        rows = cur.fetchall()

        # If no data found
        if not rows:
            return {
                "metadata": {
                    "season": season_id or "All seasons",
                    "crop": crop_id or "All crops",
                    "state": state or "All states (aggregated)",
                    "variety": variety_id or "All varieties",
                    "total_records": 0,
                    "message": "No TP aging data found for the selected filters"
                },
                "data": []
            }

        # Process results - Updated to round ALL numeric fields to 2 decimal places
        processed_data = []
        varieties_dict = {}

        for row in rows:
            if group_by_village:
                # When grouping by village, include state info
                key = f"{row['state']} - {row['village']} - {row['variety']}"
                variety_display = f"{row['village']} - {row['variety']}"
                variety_id_key = row['variety_id']
                state_info = row['state']
            else:
                # Default: Group by variety only
                key = row['variety_id']
                variety_display = row['variety']
                variety_id_key = row['variety_id']
                # State info depends on whether state filter was applied
                state_info = state if state else "Aggregated across states"

            if key not in varieties_dict:
                varieties_dict[key] = {
                    "variety_id": variety_id_key,
                    "variety": variety_display,
                    "state": state_info,
                    "season": row['season'] if 'season' in row else "Multiple seasons",
                    "crop": row['crop'] if 'crop' in row else "Multiple crops",
                    "TP Windows": {},
                    "total_acres": 0.00,
                    "total_packed_quantity": 0.00
                }

            # Add TP window data (aggregate if same TP window exists)
            tp_window = row['tp_window']
            if tp_window in varieties_dict[key]["TP Windows"]:
                # Aggregate existing data with rounding
                varieties_dict[key]["TP Windows"][tp_window]["acres"] = round(
                    varieties_dict[key]["TP Windows"][tp_window]["acres"] + float(row['acres']), 2
                )
                varieties_dict[key]["TP Windows"][tp_window]["packed_quantity"] = round(
                    varieties_dict[key]["TP Windows"][tp_window]["packed_quantity"] + float(row['packed_quantity']), 2
                )
                # Recalculate productivity with rounding
                total_acres = varieties_dict[key]["TP Windows"][tp_window]["acres"]
                total_packed = varieties_dict[key]["TP Windows"][tp_window]["packed_quantity"]
                varieties_dict[key]["TP Windows"][tp_window]["productivity"] = (
                    round(total_packed / total_acres, 2) if total_acres > 0 else 0.00
                )
            else:
                # New TP window - round all numeric fields
                varieties_dict[key]["TP Windows"][tp_window] = {
                    "acres": round(float(row['acres']), 2),
                    "packed_quantity": round(float(row['packed_quantity']), 2),
                    "productivity": round(float(row['productivity']), 2)
                }

            # Accumulate totals with rounding
            varieties_dict[key]["total_acres"] = round(
                varieties_dict[key]["total_acres"] + float(row['acres']), 2
            )
            varieties_dict[key]["total_packed_quantity"] = round(
                varieties_dict[key]["total_packed_quantity"] + float(row['packed_quantity']), 2
            )

        # Calculate overall productivity with rounding
        for variety_data in varieties_dict.values():
            if variety_data["total_acres"] > 0:
                variety_data["overall_productivity"] = round(
                    variety_data["total_packed_quantity"] / variety_data["total_acres"], 2
                )
            else:
                variety_data["overall_productivity"] = 0.00

        processed_data = list(varieties_dict.values())

        # Get total count for pagination
        count_query = f"""
            SELECT COUNT(DISTINCT variety_id) as total
            FROM operations_demo.season_crop_yield_view
            WHERE {condition_str}
        """
        cur.execute(count_query, params[:-2])  # Remove limit and offset
        total_count = cur.fetchone()['total']

        # Summary statistics
        total_varieties = len(set(item['variety_id'] for item in processed_data))

        return {
            "metadata": {
                "season": season_id or "All seasons",
                "crop": crop_id or "All crops",
                "state": state or "Aggregated across all states",
                "variety": variety_id or f"All varieties ({total_varieties} found)",
                "total_records": total_count,
                "returned_records": len(processed_data),
                "group_by_village": group_by_village,
                "summary": {
                    "varieties_count": total_varieties,
                    "aggregation": f"Data for {state}" if state else "Data aggregated across all states"
                }
            },
            "data": processed_data
        }

    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error fetching TP aging metrics: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)




@router.get("/seed-forecast/tp-aging-summary")
def get_tp_aging_summary(
        season_id: str = Query(...),
        crop_id: str = Query(...),
        state: Optional[str] = Query(None),
        variety_id: Optional[str] = Query(None)
):
    """
    Get aggregated TP aging summary with proper TP calculation
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        conditions = ["season_id = %s", "crop_id = %s"]
        params = [season_id, crop_id]

        if state and state != "all":
            conditions.append("state = %s")
            params.append(state)

        if variety_id and variety_id != "all":
            conditions.append("variety_id = %s")
            params.append(variety_id)

        condition_str = " AND ".join(conditions)

        # Summary query with proper TP calculation
        summary_query = f"""
            SELECT 
                'Grand Total' as variety,
                SUM(COALESCE(net_acres, 0)) as total_acres,
                SUM(COALESCE(packed_qt, 0)) as total_packed_quantity,
                CASE 
                    WHEN SUM(COALESCE(net_acres, 0)) > 0 
                    THEN ROUND((SUM(COALESCE(packed_qt, 0)) / SUM(COALESCE(net_acres, 0)))::numeric, 0)
                    ELSE 0 
                END as overall_productivity
            FROM operations_demo.season_crop_yield_view
            WHERE {condition_str}
              AND female_soaking_date IS NOT NULL
              AND female_tp_date IS NOT NULL
              AND female_tp_date > female_soaking_date
        """

        cur.execute(summary_query, params)
        summary = cur.fetchone()

        return {
            "variety": summary['variety'],
            "total_acres": float(summary['total_acres']),
            "total_packed_quantity": float(summary['total_packed_quantity']),
            "overall_productivity": int(summary['overall_productivity'])
        }

    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching TP aging summary: {str(e)}")
    finally:
        if 'cur' in locals():
            cur.close()
        release_connection(conn)


