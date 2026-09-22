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
    state: Optional[str] = Query(default=None),
    variety_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    Cascading dropdown: season → crop → state → variety → village
    """
    try:
        logger.info(f"[dropdown-options] Incoming params: season_id={season_id}, crop_id={crop_id}, "
                   f"state={state}, variety_id={variety_id}")
        
        result = {}
        state_normalized = str(state).strip().lower() if state else ""
        variety_normalized = str(variety_id).strip().upper() if variety_id else ""

        if not season_id and not crop_id and not state and not variety_id:
            season_data = db.query(SeasonRecord.season_id, SeasonRecord.season_name).all()
            result["season_id"] = [{s.season_id: s.season_name} for s in season_data]

        elif season_id and not crop_id:
            crop_data = (
                db.query(CropRecord.crop_id, CropRecord.crop_name)
                .join(SeasonCropInspectionBase, CropRecord.crop_id == SeasonCropInspectionBase.crop_id)
                .filter(SeasonCropInspectionBase.season_id == season_id)
                .distinct()
                .all()
            )
            result["crop_id"] = [{c.crop_id: c.crop_name} for c in crop_data]

        elif season_id and crop_id and not state:
            state_data = (
                db.query(SeasonCropInspectionBase.state)
                .filter(
                    SeasonCropInspectionBase.season_id == season_id,
                    SeasonCropInspectionBase.crop_id == crop_id,
                    SeasonCropInspectionBase.state.isnot(None),
                    SeasonCropInspectionBase.state != ""
                )
                .distinct()
                .all()
            )
            state_list = sorted(set(s.state for s in state_data if s.state))
            result["state"] = ["ALL"] + state_list

        elif season_id and crop_id and state and not variety_id:
            base_filter = [
                SeasonCropInspectionBase.season_id == season_id,
                SeasonCropInspectionBase.crop_id == crop_id,
                SeasonCropInspectionBase.variety_id.isnot(None),
                VarietyRecord.variety_name.isnot(None),
                VarietyRecord.variety_name != ""
            ]
            if state_normalized != "all":
                base_filter.append(func.lower(func.trim(SeasonCropInspectionBase.state)) == state_normalized)
            variety_data = (
                db.query(VarietyRecord.variety_id, VarietyRecord.variety_name)
                .join(SeasonCropInspectionBase, VarietyRecord.variety_id == SeasonCropInspectionBase.variety_id)
                .filter(*base_filter)
                .distinct()
                .all()
            )
            variety_list = [{v.variety_id: v.variety_name} for v in variety_data]
            result["variety_id"] = [{"ALL": "ALL"}] + variety_list

        elif season_id and crop_id and state and variety_id:
            base_filter = [
                SeasonCropInspectionBase.season_id == season_id,
                SeasonCropInspectionBase.crop_id == crop_id,
                SeasonCropInspectionBase.village.isnot(None),
                SeasonCropInspectionBase.village != ""
            ]
            if state_normalized != "all":
                base_filter.append(func.lower(func.trim(SeasonCropInspectionBase.state)) == state_normalized)
            if variety_normalized != "ALL":
                base_filter.append(SeasonCropInspectionBase.variety_id == variety_id)
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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[dropdown-options] Error: {str(e)}")
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
    """
    Cascading dropdown: plan_revision_version → season → crop → state → variety → village.
    ALL is valid for state, variety, and village and does not filter that field.
    """
    try:
        def normalize_param(value, param_name=None):
            if not value:
                return None
            value_str = str(value).strip().lower()
            placeholders = ["state", "variety", "village", "season", "crop", "none", "null", ""]
            if value_str in placeholders:
                logger.info(f"[supply-chain-planning-dropdown] Normalizing {param_name}='{value}' to None")
                return None
            return value

        season = normalize_param(season, "season")
        crop = normalize_param(crop, "crop")
        state = normalize_param(state, "state")
        variety = normalize_param(variety, "variety")
        village = normalize_param(village, "village")

        result = {}
        state_normalized = str(state).strip().lower() if state else ""
        variety_normalized = str(variety).strip().lower() if variety else ""

        base_query = """
            SELECT DISTINCT {column}
            FROM operations_demo.supply_chain_vs_yield_view
            WHERE {conditions}
            AND {column} IS NOT NULL AND {column} != ''
            ORDER BY {column}
        """

        if not plan_revision_version and not season and not crop and not state and not variety and not village:
            query = base_query.format(column="plan_revision_version", conditions="1=1")
            result["plan_revision_version"] = [r[0] for r in db.execute(text(query)).fetchall()]

        elif plan_revision_version and not season:
            query = base_query.format(
                column="season",
                conditions="TRIM(LOWER(plan_revision_version)) = TRIM(LOWER(:version))"
            )
            result["season"] = [r[0] for r in db.execute(text(query), {"version": plan_revision_version}).fetchall()]

        elif plan_revision_version and season and not crop:
            query = base_query.format(
                column="crop",
                conditions="TRIM(LOWER(plan_revision_version)) = TRIM(LOWER(:version)) AND TRIM(LOWER(season)) = TRIM(LOWER(:season))"
            )
            result["crop"] = [r[0] for r in db.execute(text(query), {
                "version": plan_revision_version,
                "season": season
            }).fetchall()]

        elif plan_revision_version and season and crop and not state:
            query = base_query.format(
                column="state",
                conditions="TRIM(LOWER(plan_revision_version)) = TRIM(LOWER(:version)) AND TRIM(LOWER(season)) = TRIM(LOWER(:season)) AND TRIM(LOWER(crop)) = TRIM(LOWER(:crop))"
            )
            state_list = [r[0] for r in db.execute(text(query), {
                "version": plan_revision_version,
                "season": season,
                "crop": crop
            }).fetchall()]
            result["state"] = ["ALL"] + state_list

        elif plan_revision_version and season and crop and state and not variety:
            conditions = (
                "TRIM(LOWER(plan_revision_version)) = TRIM(LOWER(:version)) "
                "AND TRIM(LOWER(season)) = TRIM(LOWER(:season)) "
                "AND TRIM(LOWER(crop)) = TRIM(LOWER(:crop))"
            )
            params = {"version": plan_revision_version, "season": season, "crop": crop}
            if state_normalized != "all":
                conditions += " AND TRIM(LOWER(state)) = TRIM(LOWER(:state))"
                params["state"] = state
            query = base_query.format(column="variety", conditions=conditions)
            variety_list = [r[0] for r in db.execute(text(query), params).fetchall()]
            result["variety"] = ["ALL"] + variety_list

        elif plan_revision_version and season and crop and state and variety and not village:
            conditions_parts = [
                "TRIM(LOWER(plan_revision_version)) = TRIM(LOWER(:version))",
                "TRIM(LOWER(season)) = TRIM(LOWER(:season))",
                "TRIM(LOWER(crop)) = TRIM(LOWER(:crop))",
            ]
            params = {"version": plan_revision_version, "season": season, "crop": crop}
            if state_normalized != "all":
                conditions_parts.append("TRIM(LOWER(state)) = TRIM(LOWER(:state))")
                params["state"] = state
            if variety_normalized != "all":
                conditions_parts.append("TRIM(LOWER(variety)) = TRIM(LOWER(:variety))")
                params["variety"] = variety
            query = base_query.format(column="village", conditions=" AND ".join(conditions_parts))
            village_list = [r[0] for r in db.execute(text(query), params).fetchall()]
            result["village"] = ["ALL"] + village_list

        elif plan_revision_version and season and crop and state and variety and village:
            # Last cascade step — nothing further to return.
            result = {}

        else:
            raise HTTPException(status_code=400, detail="Invalid parameter combination.")

        logger.info(f"[supply-chain-planning-dropdown] Returning keys: {list(result.keys())}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[supply-chain-planning-dropdown] Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching supply chain planning dropdown options: {str(e)}"
        )


@router.get("/supply-chain-planning/versions", response_model=Dict[str, Any])
async def get_all_supply_chain_versions(db: Session = Depends(get_db)):
    """
    Always return all plan revision versions — similar to get_all_seasons
    """
    try:
        query = text("""
            SELECT DISTINCT plan_revision_version, 
                   COUNT(*) as record_count,
                   NULL as latest_update
            FROM operations_demo.supply_chain_vs_yield_view 
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
