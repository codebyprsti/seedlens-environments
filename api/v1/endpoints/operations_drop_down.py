import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from core.db import get_db
from sqlalchemy import text, func
from models.db_models import CropRecord, SeasonRecord, VarietyRecord, SeasonCropInspectionBase, SupplyChainPlanning, LocationRecord, GrowerRecord

router = APIRouter()
logger = logging.getLogger(__name__)

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
    state: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    Cascading dropdown: State → District → Village hierarchy.
    1. If no params: return season_id list
    2. If season_id is present: return crop_id list
    3. If crop_id is present: return variety_id list
    4. If variety_id is present: return state list (filtered by variety_id)
    5. If variety_id + state are present: return village list (filtered by variety_id + state)
    """
    try:
        # Log incoming parameters for debugging
        logger.info(f"[dropdown-options] Incoming params: season_id={season_id}, crop_id={crop_id}, "
                   f"variety_id={variety_id}, state={state}")
        
        result = {}
        variety_normalized = str(variety_id).strip().upper() if variety_id else ""

        if not season_id and not crop_id and not variety_id and not state:
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

        elif season_id and crop_id and variety_id and not state:
            # Step 4: Return states filtered by variety_id (State → District → Village start)
            base_filter = [
                SeasonCropInspectionBase.season_id == season_id,
                SeasonCropInspectionBase.crop_id == crop_id,
                SeasonCropInspectionBase.state.isnot(None),
                SeasonCropInspectionBase.state != ""
            ]
            
            if variety_normalized != "ALL":
                base_filter.append(SeasonCropInspectionBase.variety_id == variety_id)
            
            state_data = (
                db.query(SeasonCropInspectionBase.state)
                .filter(*base_filter)
                .distinct()
                .all()
            )
            state_list = sorted(set(s.state for s in state_data if s.state))
            result["state"] = ["ALL"] + state_list
            logger.info(f"[dropdown-options] Returning {len(state_list)} states for variety_id={variety_id}")

        elif season_id and crop_id and variety_id and state:
            # Step 5: Return villages filtered by variety_id + state (State → District → Village)
            state_normalized = str(state).strip().upper() if state else ""
            base_filter = [
                SeasonCropInspectionBase.season_id == season_id,
                SeasonCropInspectionBase.crop_id == crop_id,
                SeasonCropInspectionBase.village.isnot(None),
                SeasonCropInspectionBase.village != ""
            ]
            
            if variety_normalized != "ALL":
                base_filter.append(SeasonCropInspectionBase.variety_id == variety_id)
            
            if state_normalized != "ALL":
                base_filter.append(func.lower(func.trim(SeasonCropInspectionBase.state)) == func.lower(func.trim(state)))
            
            village_data = (
                db.query(SeasonCropInspectionBase.village)
                .filter(*base_filter)
                .distinct()
                .all()
            )
            village_list = sorted(set(v.village for v in village_data if v.village))
            result["village"] = ["ALL"] + village_list

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter combination.")

        logger.info(f"[dropdown-options] Returning result with keys: {list(result.keys())}")
        return result

    except Exception as e:
        logger.exception(f"[dropdown-options] Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching dropdown options: {str(e)}")

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
