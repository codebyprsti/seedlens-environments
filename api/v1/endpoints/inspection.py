import time
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import literal, text
from typing import List, Optional
from core.db import get_db
from models.schemas.base import (
    SeasonCropInspectionResponse,
    PlanVsYieldInspectionRow,
)
from models.db_models import (
    SeasonCropInspectionBase,
    CropRecord,
    SeasonRecord,
    VarietyRecord,
    SeedForecast,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Inline plan-vs-yield logic (no view required). supply_chain_planning joined on names (season, crop, variety, village).
_PLAN_VS_YIELD_INSPECTION_SQL = text("""
WITH sub AS (
    SELECT
        y.season_id,
        y.crop_id,
        y.variety_id,
        y.lot_id,
        y.location_id,
        s.season_name,
        c.crop_name,
        v.variety_name,
        l.village,
        l.mandal,
        l.district,
        l.state,
        l.latitude,
        l.longitude,
        sc.plan_revision_version,
        sc.net_acres_current::double precision AS planned_net_acres,
        sc.productivity::double precision AS planned_productivity,
        sc.production_allocation::double precision AS planned_production_allocation,
        sc.actual_net_acres::double precision AS sc_actual_net_acres,
        sc.adjusted_production_allocation::double precision AS adjusted_production_allocation,
        sc.estimated_cost_per_kg::double precision AS estimated_cost_per_kg,
        sc.estimated_production_cost::double precision AS estimated_production_cost,
        y.net_tp_acres::double precision AS actual_tp_acres,
        y.net_acreage_area::double precision AS actual_net_acres,
        y.sum_of_received_qty::double precision AS actual_received_qty,
        y.packed_qty::double precision AS actual_packed_qty,
        y.productivity_of_packed_seed::double precision AS actual_productivity,
        y.amount_inr::double precision AS actual_amount,
        sf.stage_forecast1::double precision AS stage_forecast1,
        sf.stage_forecast2::double precision AS stage_forecast2,
        sf.stage_forecast3::double precision AS stage_forecast3,
        sf.stage_forecast4::double precision AS stage_forecast4,
        sf.stage_forecast5::double precision AS stage_forecast5,
        sf.stage_forecast6::double precision AS stage_forecast6
    FROM operations.season_crop_yield y
    LEFT JOIN operations.seasons s ON s.season_id = y.season_id
    LEFT JOIN operations.crops c ON c.crop_id = y.crop_id
    LEFT JOIN operations.varieties v ON v.variety_id = y.variety_id
    LEFT JOIN operations.locations l ON l.location_id = y.location_id
    LEFT JOIN operations.supply_chain_planning sc
        ON LOWER(TRIM(sc.season)) = LOWER(TRIM(s.season_name))
        AND LOWER(TRIM(sc.crop)) = LOWER(TRIM(c.crop_name))
        AND LOWER(TRIM(sc.variety)) = LOWER(TRIM(v.variety_name))
        AND LOWER(TRIM(COALESCE(sc.village, ''))) = LOWER(TRIM(COALESCE(l.village, '')))
    LEFT JOIN operations.seed_forecast sf
        ON sf.season_id = y.season_id AND sf.crop_id = y.crop_id
        AND sf.variety_id = y.variety_id AND sf.lot_id = y.lot_id
)
SELECT * FROM sub
WHERE 1=1
  AND (:season_id IS NULL OR lower(trim(season_id)) = lower(trim(:season_id)))
  AND (:crop_id IS NULL OR lower(trim(crop_id)) = lower(trim(:crop_id)))
  AND (:variety_id IS NULL OR lower(trim(variety_id)) = lower(trim(:variety_id)))
  AND (:location_id IS NULL OR lower(trim(location_id)) = lower(trim(:location_id)))
  -- Dropdown hierarchy: State → District → Village (filters cascade when provided)
  AND (:state IS NULL OR lower(trim(state)) = lower(trim(:state)))
  AND (:district IS NULL OR lower(trim(district)) = lower(trim(:district)))
  AND (:village IS NULL OR lower(trim(village)) = lower(trim(:village)))
ORDER BY season_id, crop_id, variety_id, lot_id
LIMIT :limit OFFSET :offset
""")


@router.get("/yield/inspection", response_model=List[SeasonCropInspectionResponse])
async def get_all_season_crop_inspection_data(
    db: Session = Depends(get_db)
):
    """
    Fetch fixed columns from season_crop_inspection_base with related names.
    Returns up to 100 records. Table columns are unchanged; response labels only:
    growers_name -> grower_name, father_name -> fathers_name, taluka_mandal -> mandal.
    """
    try:
        # Response uses grower_name (and fathers_name, mandal); table stays growers_name, father_name, taluka_mandal
        results = (
            db.query(
                SeasonCropInspectionBase.crop_id,
                CropRecord.crop_name,
                SeasonCropInspectionBase.season_id,
                SeasonRecord.season_name,
                SeasonCropInspectionBase.grower_id,
                SeasonCropInspectionBase.lot_id,
                SeasonCropInspectionBase.variety_id,
                VarietyRecord.variety_name,
                SeasonCropInspectionBase.organizer_name,
                SeasonCropInspectionBase.growers_name.label("grower_name"),
                literal(None).label("grower_gender"),
                SeasonCropInspectionBase.father_name.label("fathers_name"),
                SeasonCropInspectionBase.village,
                SeasonCropInspectionBase.taluka_mandal.label("mandal"),
                SeasonCropInspectionBase.district,
                SeasonCropInspectionBase.state,
                SeedForecast.stage_forecast1,
                SeedForecast.stage_forecast2
            )
            .join(CropRecord, CropRecord.crop_id == SeasonCropInspectionBase.crop_id)
            .join(SeasonRecord, SeasonRecord.season_id == SeasonCropInspectionBase.season_id)
            .join(VarietyRecord, VarietyRecord.variety_id == SeasonCropInspectionBase.variety_id)
            .outerjoin(SeedForecast, SeedForecast.lot_id == SeasonCropInspectionBase.lot_id)
            .all()
        )
        return [SeasonCropInspectionResponse(**row._asdict()) for row in results]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching data: {str(e)}")


def _row_to_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


@router.get("/season-crop-inspection", response_model=List[PlanVsYieldInspectionRow])
async def get_yield_inspection_from_view(
    db: Session = Depends(get_db),
    season_id: Optional[str] = Query(None, description="Filter by season_id (optional)"),
    crop_id: Optional[str] = Query(None, description="Filter by crop_id (optional)"),
    variety_id: Optional[str] = Query(None, description="Filter by variety_id (optional)"),
    location_id: Optional[str] = Query(None, description="Filter by location_id (optional)"),
    village: Optional[str] = Query(None, description="Filter by village (optional)"),
    district: Optional[str] = Query(None, description="Filter by district (optional)"),
    state: Optional[str] = Query(None, description="Filter by state (optional)"),
    limit: int = Query(500, ge=1, le=5000, description="Rows per page (default 500)"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Returns only the rows (list of lot-level plan vs yield data).
    No required params — call with no args to get latest data.
    """
    params = {
        "season_id": season_id,
        "crop_id": crop_id,
        "variety_id": variety_id,
        "location_id": location_id,
        "village": village,
        "district": district,
        "state": state,
        "limit": limit,
        "offset": offset,
    }
    try:
        result = db.execute(_PLAN_VS_YIELD_INSPECTION_SQL, params)
        rows_result = result.fetchall()
        keys = list(result.keys())
        logger.info(f"[yield/inspection] returned={len(rows_result)}")

        rows = []
        for r in rows_result:
            d = dict(zip(keys, r))
            rows.append(PlanVsYieldInspectionRow(
                season_id=d.get("season_id"),
                crop_id=d.get("crop_id"),
                variety_id=d.get("variety_id"),
                lot_id=d.get("lot_id"),
                location_id=d.get("location_id"),
                season_name=d.get("season_name"),
                crop_name=d.get("crop_name"),
                variety_name=d.get("variety_name"),
                village=d.get("village"),
                mandal=d.get("mandal"),
                district=d.get("district"),
                state=d.get("state"),
                latitude=_row_to_float(d.get("latitude")),
                longitude=_row_to_float(d.get("longitude")),
                plan_revision_version=d.get("plan_revision_version"),
                planned_net_acres=_row_to_float(d.get("planned_net_acres")),
                planned_productivity=_row_to_float(d.get("planned_productivity")),
                planned_production_allocation=_row_to_float(d.get("planned_production_allocation")),
                actual_net_acres=_row_to_float(d.get("actual_net_acres")),
                actual_tp_acres=_row_to_float(d.get("actual_tp_acres")),
                actual_received_qty=_row_to_float(d.get("actual_received_qty")),
                actual_packed_qty=_row_to_float(d.get("actual_packed_qty")),
                actual_productivity=_row_to_float(d.get("actual_productivity")),
                actual_amount=_row_to_float(d.get("actual_amount")),
                adjusted_production_allocation=_row_to_float(d.get("adjusted_production_allocation")),
                estimated_cost_per_kg=_row_to_float(d.get("estimated_cost_per_kg")),
                estimated_production_cost=_row_to_float(d.get("estimated_production_cost")),
                stage_forecast1=_row_to_float(d.get("stage_forecast1")),
                stage_forecast2=_row_to_float(d.get("stage_forecast2")),
                stage_forecast3=_row_to_float(d.get("stage_forecast3")),
                stage_forecast4=_row_to_float(d.get("stage_forecast4")),
                stage_forecast5=_row_to_float(d.get("stage_forecast5")),
                stage_forecast6=_row_to_float(d.get("stage_forecast6")),
            ))
        return rows
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=f"Error fetching yield inspection: {str(e)}")
