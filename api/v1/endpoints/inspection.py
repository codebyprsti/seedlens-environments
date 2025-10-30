from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from core.db import get_db
from models.schemas.base import SeasonCropInspectionResponse

router = APIRouter()

from models.db_models import (
    SeasonCropInspectionBase,
    CropRecord,
    SeasonRecord,
    VarietyRecord,
    SeedForecast
)

@router.get("/season-crop-inspection", response_model=List[SeasonCropInspectionResponse])
async def get_all_season_crop_inspection_data(
    db: Session = Depends(get_db)
):
    """
    Fetch fixed columns from season_crop_inspection_base with related names.
    Returns up to 100 records.
    """
    try:
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
                SeasonCropInspectionBase.grower_name,
                SeasonCropInspectionBase.grower_gender,
                SeasonCropInspectionBase.fathers_name,
                SeasonCropInspectionBase.village,
                SeasonCropInspectionBase.mandal,
                SeasonCropInspectionBase.district,
                SeasonCropInspectionBase.state,
                SeasonCropInspectionBase.male_soaking_acre,
                SeasonCropInspectionBase.male_no_of_pkt,
                SeasonCropInspectionBase.male_qty_in_kgs.label("male_qty"),
                SeasonCropInspectionBase.female_soaking_acre,
                SeasonCropInspectionBase.female_no_of_pkt,
                SeasonCropInspectionBase.female_qty_in_kgs.label("female_qty"),
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
