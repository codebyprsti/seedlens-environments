from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from core.db import get_db
from sqlalchemy import text, func
from models.db_models import CropRecord, SeasonRecord, VarietyRecord, SeasonCropInspectionBase, SupplyChainPlanning, LocationRecord

router = APIRouter()

@router.get("/seasons", response_model=Dict[str, Any])
async def get_all_seasons(db: Session = Depends(get_db)):
    """
    Always return all seasons — season_id and season_name.
    """
    try:
        season_data = db.query(SeasonRecord.season_id, SeasonRecord.season_name).all()
        result = {"id": [{s.season_id: s.season_name} for s in season_data]}
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching seasons: {str(e)}")




# from fastapi import APIRouter, Depends, HTTPException, Query
# from sqlalchemy.orm import Session
# from typing import Dict, Any, Optional
# from core.db import get_db
# from sqlalchemy import text
#
# router = APIRouter()

@router.get("/dropdown-options", response_model=Dict[str, Any])
async def get_dropdown_options(
    season_id: Optional[str] = Query(default=None),
    crop_id: Optional[str] = Query(default=None),
    variety_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    Cascading dropdown:
    1. If no params: return season_id list
    2. If season_id is present: return crop_id list
    3. If crop_id is present: return variety_id list
    4. If variety_id is present: return village list
    """
    try:
        result = {}

        if not season_id and not crop_id and not variety_id:
            # Step 1: Return all seasons
            season_data = db.query(SeasonRecord.season_id, SeasonRecord.season_name).all()
            result["season_id"] = [{s.season_id: s.season_name} for s in season_data]

        elif season_id and not crop_id:
            # Step 2: Return crops for given season
            crop_data = (
                db.query(CropRecord.crop_id, CropRecord.crop_name)
                .join(SeasonCropInspectionBase, CropRecord.crop_id == SeasonCropInspectionBase.crop_id)
                .filter(SeasonCropInspectionBase.season_id == season_id)
                .distinct()
                .all()
            )
            result["crop_id"] = [{c.crop_id: c.crop_name} for c in crop_data]

        elif season_id and crop_id and not variety_id:
            # Step 3: Return varieties for given season + crop
            variety_data = (
                db.query(VarietyRecord.variety_id, VarietyRecord.variety_name)
                .join(SeasonCropInspectionBase, VarietyRecord.variety_id == SeasonCropInspectionBase.variety_id)
                .filter(
                    SeasonCropInspectionBase.season_id == season_id,
                    SeasonCropInspectionBase.crop_id == crop_id
                )
                .distinct()
                .all()
            )
            # Add "ALL" as the first option
            variety_list = [{v.variety_id: v.variety_name} for v in variety_data]
            result["variety_id"] = [{"ALL": "ALL"}] + variety_list

        elif season_id and crop_id and variety_id:
            # Step 4: Return villages for season + crop + variety
            # Handle "ALL" for variety_id (case-insensitive)
            variety_normalized = str(variety_id).strip().upper() if variety_id else ""
            
            if variety_normalized == "ALL":
                # When variety = "ALL", get all villages for season + crop (no variety filter)
                village_data = (
                    db.query(SeasonCropInspectionBase.village)
                    .filter(
                        SeasonCropInspectionBase.season_id == season_id,
                        SeasonCropInspectionBase.crop_id == crop_id
                    )
                    .distinct()
                    .all()
                )
            else:
                # When specific variety is selected, get villages for that variety
                village_data = (
                    db.query(SeasonCropInspectionBase.village)
                    .filter(
                        SeasonCropInspectionBase.season_id == season_id,
                        SeasonCropInspectionBase.crop_id == crop_id,
                        SeasonCropInspectionBase.variety_id == variety_id
                    )
                    .distinct()
                    .all()
                )
            
            # Add "ALL" as the first option, followed by actual villages
            village_list = [v.village for v in village_data if v.village]
            result["village"] = ["ALL"] + village_list

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter combination.")

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching dropdown options: {str(e)}")


@router.get("/supply-chain-planning/dropdown", response_model=Dict[str, Any])
async def get_supply_chain_planning_dropdown_options(
        plan_revision_version: Optional[str] = Query(default=None),
        season: Optional[str] = Query(default=None),
        crop: Optional[str] = Query(default=None),
        state: Optional[str] = Query(default=None),
        variety: Optional[str] = Query(default=None),
        village: Optional[str] = Query(default=None),
        db: Session = Depends(get_db)
):
    try:
        result = {}

        base_query = """
            SELECT DISTINCT {column}
            FROM operations.supply_chain_vs_yield_view
            WHERE {conditions}
            AND {column} IS NOT NULL AND {column} != ''
            ORDER BY {column}
        """

        # Alternative query format for better debugging
        direct_query_template = """
            SELECT DISTINCT {column}
            FROM operations.supply_chain_vs_yield_view
            WHERE plan_revision_version = %(version)s 
            AND season = %(season)s 
            AND crop = %(crop)s
            {state_filter}
            AND {column} IS NOT NULL AND {column} != ''
            ORDER BY {column}
        """

        if not plan_revision_version and not season and not crop and not state and not variety and not village:
            query = base_query.format(column="plan_revision_version", conditions="1=1")
            result["plan_revision_version"] = [r[0] for r in db.execute(text(query)).fetchall()]

        elif plan_revision_version and not season:
            query = base_query.format(column="season", conditions="plan_revision_version = :version")
            result["season"] = [r[0] for r in db.execute(text(query), {"version": plan_revision_version}).fetchall()]

        elif plan_revision_version and season and not crop:
            query = base_query.format(column="crop", conditions="plan_revision_version = :version AND season = :season")
            result["crop"] = [r[0] for r in db.execute(text(query), {
                "version": plan_revision_version,
                "season": season
            }).fetchall()]

        elif plan_revision_version and season and crop and not state:
            query = base_query.format(
                column="state",
                conditions="plan_revision_version = :version AND season = :season AND crop = :crop"
            )
            state_list = [r[0] for r in db.execute(text(query), {
                "version": plan_revision_version,
                "season": season,
                "crop": crop
            }).fetchall()]
            # Add "All" as the first option
            result["state"] = ["All"] + state_list

        elif plan_revision_version and season and crop and state and not variety:
            # Handle "All" state - get varieties for all states (case-insensitive check)
            state_normalized = str(state).strip().lower() if state else ""
            
            if state_normalized == "all":
                # When state is "All" (case-insensitive), get all varieties across all states (no state filter)
                query = text("""
                    SELECT DISTINCT variety
                    FROM operations.supply_chain_vs_yield_view
                    WHERE plan_revision_version = :version 
                    AND season = :season 
                    AND crop = :crop
                    AND variety IS NOT NULL 
                    AND variety != ''
                    AND TRIM(variety) != ''
                    ORDER BY variety
                """)
                try:
                    rows = db.execute(query, {
                        "version": plan_revision_version,
                        "season": season,
                        "crop": crop
                    }).fetchall()
                    variety_list = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
                    # Remove duplicates while preserving order
                    variety_list = list(dict.fromkeys(variety_list))
                    # Filter out any empty strings
                    variety_list = [v for v in variety_list if v]
                except Exception as e:
                    # Log error for debugging
                    import logging
                    logging.error(f"Error fetching varieties when state=All: {str(e)}")
                    variety_list = []
            else:
                # When specific state is selected, get varieties for that state
                query = text("""
                    SELECT DISTINCT variety
                    FROM operations.supply_chain_vs_yield_view
                    WHERE plan_revision_version = :version 
                    AND season = :season 
                    AND crop = :crop
                    AND state = :state
                    AND variety IS NOT NULL 
                    AND variety != ''
                    AND TRIM(variety) != ''
                    ORDER BY variety
                """)
                try:
                    rows = db.execute(query, {
                "version": plan_revision_version,
                "season": season,
                "crop": crop,
                "state": state
                    }).fetchall()
                    variety_list = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
                    # Remove duplicates while preserving order
                    variety_list = list(dict.fromkeys(variety_list))
                    # Filter out any empty strings
                    variety_list = [v for v in variety_list if v]
                except Exception as e:
                    import logging
                    logging.error(f"Error fetching varieties for state {state}: {str(e)}")
                    variety_list = []
            
            # Always add "All" as the first option in variety dropdown, followed by actual varieties
            # Ensure we always return at least ["All"] even if no varieties found
            if variety_list:
                result["variety"] = ["All"] + variety_list
            else:
                # If no varieties found, still return ["All"] but log a warning
                import logging
                logging.warning(f"No varieties found for plan_revision_version={plan_revision_version}, season={season}, crop={crop}, state={state}")
                result["variety"] = ["All"]

        elif plan_revision_version and season and crop and state and variety and not village:
            # Handle "All" selections for state and variety (case-insensitive)
            state_normalized = str(state).strip().lower() if state else ""
            variety_normalized = str(variety).strip().lower() if variety else ""
            
            conditions_parts = ["plan_revision_version = :version", "season = :season", "crop = :crop"]
            params = {
                "version": plan_revision_version,
                "season": season,
                "crop": crop
            }
            
            # Only add state filter if it's not "All" (case-insensitive)
            if state_normalized != "all":
                conditions_parts.append("state = :state")
                params["state"] = state
            
            # Only add variety filter if it's not "All" (case-insensitive)
            if variety_normalized != "all":
                conditions_parts.append("variety = :variety")
                params["variety"] = variety
            
            conditions_str = " AND ".join(conditions_parts)
            query = base_query.format(
                column="village",
                conditions=conditions_str
            )
            result["village"] = [r[0] for r in db.execute(text(query), params).fetchall()]

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter combination.")

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching supply chain planning dropdown options: {str(e)}")

@router.get("/supply-chain-planning/versions", response_model=Dict[str, Any])
async def get_all_supply_chain_versions(db: Session = Depends(get_db)):
    """
    Always return all plan revision versions — similar to get_all_seasons
    """
    try:
        query = text("""
            SELECT DISTINCT plan_revision_version, 
                   COUNT(*) as record_count,
                   MAX(created_at) as latest_update
            FROM operations.supply_chain_planning 
            WHERE plan_revision_version IS NOT NULL 
            GROUP BY plan_revision_version
            ORDER BY plan_revision_version DESC
        """)
        version_data = db.execute(query).fetchall()

        result = {
            "versions": [
                {
                    "version": v[0],
                    "record_count": v[1],
                    "latest_update": str(v[2]) if v[2] else None
                }
                for v in version_data
            ]
        }
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching supply chain planning versions: {str(e)}")
