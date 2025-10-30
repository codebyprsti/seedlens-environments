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


def check_table_exists(conn, table_name: str, schema: str = "operations") -> bool:
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


def get_table_columns(conn, table_name: str, schema: str = "operations") -> List[str]:
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
                SELECT DISTINCT season FROM operations.supply_chain_planning 
                WHERE season IS NOT NULL AND season != ''
                ORDER BY season
            """)
            seasons.extend([row['season'] for row in cur.fetchall()])

            # If seed forecast table and seasons table exist, get from there too
            if has_seed_forecast_table and has_seasons_table:
                cur.execute("""
                    SELECT DISTINCT s.season_name as season 
                    FROM operations.seed_forecast sf
                    JOIN operations.seasons s ON sf.season_id = s.season_id
                    WHERE s.season_name IS NOT NULL AND s.season_name != ''
                """)
                seasons.extend([row['season'] for row in cur.fetchall()])
        except Exception as e:
            print(f"Error getting seasons: {e}")

        # Get available crops
        crops = []
        try:
            cur.execute("""
                SELECT DISTINCT crop FROM operations.supply_chain_planning 
                WHERE crop IS NOT NULL AND crop != ''
                ORDER BY crop
            """)
            crops.extend([row['crop'] for row in cur.fetchall()])

            if has_seed_forecast_table and has_crops_table:
                cur.execute("""
                    SELECT DISTINCT c.crop_name as crop 
                    FROM operations.seed_forecast sf
                    JOIN operations.crops c ON sf.crop_id = c.crop_id
                    WHERE c.crop_name IS NOT NULL AND c.crop_name != ''
                """)
                crops.extend([row['crop'] for row in cur.fetchall()])
        except Exception as e:
            print(f"Error getting crops: {e}")

        # Get available varieties
        varieties = []
        try:
            cur.execute("""
                SELECT DISTINCT variety FROM operations.supply_chain_planning 
                WHERE variety IS NOT NULL AND variety != ''
                ORDER BY variety
            """)
            varieties.extend([row['variety'] for row in cur.fetchall()])

            if has_seed_forecast_table and has_varieties_table:
                cur.execute("""
                    SELECT DISTINCT v.variety_name as variety 
                    FROM operations.seed_forecast sf
                    JOIN operations.varieties v ON sf.variety_id = v.variety_id
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
                FROM operations.supply_chain_planning 
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
                    FROM operations.seasons s
                    JOIN operations.supply_chain_planning scp ON s.season_name = scp.season
                    WHERE s.season_name IS NOT NULL AND s.season_name != ''
                    ORDER BY s.season_name
                """)
                seasons = [{str(row['season_id']): row['season_name']} for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT DISTINCT season 
                    FROM operations.supply_chain_planning 
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
                    FROM operations.crops c
                    JOIN operations.supply_chain_planning scp ON c.crop_name = scp.crop
                    WHERE scp.season ILIKE %s 
                    AND c.crop_name IS NOT NULL AND c.crop_name != ''
                    ORDER BY c.crop_name
                """, (f"%{season_id}%",))
                crops = [{str(row['crop_id']): row['crop_name']} for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT DISTINCT crop 
                    FROM operations.supply_chain_planning 
                    WHERE season ILIKE %s 
                    AND crop IS NOT NULL AND crop != ''
                    ORDER BY crop
                """, (f"%{season_id}%",))
                crops = [row['crop'] for row in cur.fetchall()]

            result["crop_id"] = crops

        elif season_id and crop_id and not plan_versions:
            # Step 3: Return plan versions for season + crop
            cur.execute("""
                SELECT DISTINCT plan_revision_version 
                FROM operations.supply_chain_planning 
                WHERE season ILIKE %s AND crop ILIKE %s
                AND plan_revision_version IS NOT NULL AND plan_revision_version != ''
                ORDER BY plan_revision_version DESC
            """, (f"%{season_id}%", f"%{crop_id}%"))
            plan_versions = [{row['plan_revision_version']:row['plan_revision_version']} for row in cur.fetchall()]
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
        plan_versions: Optional[str] = Query("V1.0-Maximized-Productivity", description="Plan revision version"),
        value_types: str = Query("actual", description="Value type: actual or forecast"),
        limit: int = Query(1000),
        offset: int = Query(0)
):
    """
    Compare plan values with actual or forecast values using yield_inspection_view
    FIXED: Added proper 2 decimal place rounding for all numeric values
    """
    if not season_id:
        season_id = "RABI_24_25"

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

        condition_str = ""
        if conditions:
            condition_str = " AND " + " AND ".join(conditions)

        # Build query parts
        query_parts = []

        # FIXED: Plan data CTE with ROUND to 2 decimal places
        plan_cte = f"""
        plan_data AS (
            SELECT 
                season,
                crop,
                variety,
                ROUND(SUM(COALESCE(production_allocation, 0))::NUMERIC, 2) as plan_value
            FROM operations.supply_chain_planning
            WHERE production_allocation IS NOT NULL{condition_str}
            GROUP BY season, crop, variety
        )"""
        query_parts.append(plan_cte)

        if value_types == "actual":
            # FIXED: Actual data CTE with ROUND to 2 decimal places
            actual_cte = f"""
            actual_data AS (
                SELECT 
                    season,
                    crop,
                    variety,
                    ROUND(SUM(COALESCE(actual_received_qty, 0))::NUMERIC, 2) as actual_value
                FROM operations.supply_chain_planning
                WHERE actual_received_qty IS NOT NULL{condition_str}
                GROUP BY season, crop, variety
            )"""
            query_parts.append(actual_cte)
        else:  # forecast
            # FIXED: Forecast data CTE with ROUND to 2 decimal places
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
                FROM operations.yield_inspection_view v
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

        # FIXED: Main SELECT with ROUND to 2 decimal places
        if value_types == "actual":
            main_select = """
            SELECT 
                COALESCE(p.season, a.season) as season,
                COALESCE(p.crop, a.crop) as crop,
                COALESCE(p.variety, a.variety) as variety,
                ROUND(COALESCE(p.plan_value, 0)::NUMERIC, 2) as plan_value,
                ROUND(COALESCE(a.actual_value, 0)::NUMERIC, 2) as actual_value
            FROM plan_data p
            FULL OUTER JOIN actual_data a ON (
                UPPER(TRIM(p.season)) = UPPER(TRIM(a.season)) AND 
                UPPER(TRIM(p.crop)) = UPPER(TRIM(a.crop)) AND 
                UPPER(TRIM(p.variety)) = UPPER(TRIM(a.variety))
            )
            WHERE (COALESCE(p.season, a.season) IS NOT NULL)
            ORDER BY season, crop, variety
            LIMIT %(limit)s OFFSET %(offset)s
            """
        else:  # forecast
            main_select = """
            SELECT 
                COALESCE(p.season, f.season) as season,
                COALESCE(p.crop, f.crop) as crop,
                COALESCE(p.variety, f.variety) as variety,
                ROUND(COALESCE(p.plan_value, 0)::NUMERIC, 2) as plan_value,
                ROUND(COALESCE(f.forecast_value, 0)::NUMERIC, 2) as forecast_value
            FROM plan_data p
            FULL OUTER JOIN forecast_data f ON (
                UPPER(TRIM(p.season)) = UPPER(TRIM(f.season)) AND 
                UPPER(TRIM(p.crop)) = UPPER(TRIM(f.crop)) AND 
                UPPER(TRIM(p.variety)) = UPPER(TRIM(f.variety))
            )
            WHERE (COALESCE(p.season, f.season) IS NOT NULL)
            ORDER BY season, crop, variety
            LIMIT %(limit)s OFFSET %(offset)s
            """

        # Execute query
        full_query = "WITH " + ",\n".join(query_parts) + "\n" + main_select
        params.update({"limit": limit, "offset": offset})

        cur.execute(full_query, params)
        rows = cur.fetchall()

        # FIXED: Process results with additional Python-side rounding for safety
        processed_rows = []
        for row in rows:
            row_dict = {
                'season': row['season'],
                'crop': row['crop'],
                'variety': row['variety'],
                'plan_value': round(safe_float_convert(row.get('plan_value', 0)), 2)  # Additional rounding
            }

            if value_types == "actual":
                row_dict['actual_value'] = round(safe_float_convert(row.get('actual_value', 0)), 2)
            else:
                row_dict['forecast_value'] = round(safe_float_convert(row.get('forecast_value', 0)), 2)

            processed_rows.append(row_dict)

        # Get available forecast seasons (fixed to return just strings, not arrays)
        available_seasons = []
        try:
            cur.execute("""
                SELECT DISTINCT season_name 
                FROM operations.yield_inspection_view 
                WHERE stage_forecast1 IS NOT NULL OR stage_forecast2 IS NOT NULL
                ORDER BY season_name
            """)
            available_seasons = [row[0] for row in cur.fetchall()]  # Extract strings, not arrays
        except:
            available_seasons = ["Error fetching seasons"]

        # Column metadata
        columns = [
            {"db_column_name": "season", "display_name": "Season", "type": "string", "is_visible": True},
            {"db_column_name": "crop", "display_name": "Crop", "type": "string", "is_visible": True},
            {"db_column_name": "variety", "display_name": "Variety", "type": "string", "is_visible": True},
            {"db_column_name": "plan_value", "display_name": "Plan Value", "type": "decimal", "is_visible": True}
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
                "available_forecast_seasons": available_seasons,  # Now returns clean strings
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
            FROM operations.supply_chain_planning scp
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


@router.get("/seed-forecast/state-variety-summary")
def get_state_variety_summary():
    """
    Get state-variety summary with planning vs actual values
    Uses latest season - 1 (24-25 if latest is 25-26) if available, otherwise uses latest season

    Response structure includes column metadata and data array
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Fixed query with proper CTE structure
        cur.execute("""
            WITH all_seasons AS (
                SELECT DISTINCT
                    season,
                    plan_revision_version,
                    CASE 
                        WHEN season ~ '\d{2}-\d{2}' THEN
                            CAST(SPLIT_PART(SUBSTRING(season FROM '\d{2}-\d{2}'), '-', 1) AS INTEGER)
                        ELSE 0
                    END as start_year
                FROM operations.supply_chain_vs_yield_view
                WHERE season IS NOT NULL 
                  AND season != ''
                  AND season ~ '\d{2}-\d{2}'  -- Only seasons with year pattern
            ),
            ranked_seasons AS (
                SELECT 
                    season,
                    plan_revision_version,
                    start_year,
                    ROW_NUMBER() OVER (ORDER BY plan_revision_version DESC, start_year DESC) as rank
                FROM all_seasons
            ),
            selected_seasons AS (
                SELECT season, plan_revision_version, rank FROM ranked_seasons WHERE rank = 2
                UNION ALL
                SELECT season, plan_revision_version, rank FROM ranked_seasons WHERE rank = 1
                  AND NOT EXISTS (SELECT 1 FROM ranked_seasons WHERE rank = 2)
            )
            SELECT season, plan_revision_version
            FROM selected_seasons
            ORDER BY rank
            LIMIT 1
        """)

        season_row = cur.fetchone()
        if not season_row:
            raise HTTPException(status_code=404, detail="No season data found in supply chain planning")

        selected_season = season_row['season']
        selected_version = season_row['plan_revision_version']

        # Main query to get state-variety combinations
        main_query = """
        SELECT 
            state,
            variety,
            SUM(COALESCE(production_allocation, 0)) as plan_value,
            SUM(COALESCE(actual_received_qty, 0)) as actual_value
        FROM operations.supply_chain_vs_yield_view
        WHERE season = %(season)s 
          AND plan_revision_version = %(version)s
          AND state IS NOT NULL AND state != ''
          AND variety IS NOT NULL AND variety != ''
          AND (production_allocation > 0 OR actual_received_qty > 0)
        GROUP BY state, variety
        ORDER BY state, variety
        """

        params = {
            "season": selected_season,
            "version": selected_version
        }

        cur.execute(main_query, params)
        rows = cur.fetchall()

        # Process the results
        processed_rows = []
        for row in rows:
            row_dict = {
                'state': row['state'],
                'variety': row['variety'],
                'plan_value': safe_float_convert(row['plan_value']),
                'actual_value': safe_float_convert(row['actual_value'])
            }
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
                "db_column_name": "actual_value",
                "display_name": "Actual Received Qty",
                "type": "decimal",
                "group_name": "Actual",
                "is_visible": True,
                "is_editable": False
            }
        ]

        return {
            "metadata": {
                "season_used": selected_season,
                "plan_version_used": selected_version,
                "total_records": len(processed_rows),
                "data_generated_at": "2025-09-09T16:06:00Z"
            },
            "columns": columns,
            "data": processed_rows
        }

    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error fetching state variety summary: {str(e)}")
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
                FROM operations.season_crop_yield_view
                WHERE season IS NOT NULL AND season != ''
                ORDER BY season DESC
            """)
            seasons_data = cur.fetchall()
            result["season_id"] = [{"id": str(row['season_id']), "name": row['season']} for row in seasons_data]

        elif season_id and not crop_id:
            # Step 2: Return crops for given season
            cur.execute("""
                SELECT DISTINCT crop, crop_id
                FROM operations.season_crop_yield_view
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
                FROM operations.season_crop_yield_view
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
                FROM operations.season_crop_yield_view
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
                FROM operations.season_crop_yield_view
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
                FROM operations.season_crop_yield_view
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
            FROM operations.season_crop_yield_view
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
            FROM operations.season_crop_yield_view
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


