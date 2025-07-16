from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from core.db import get_db
from models.db_models import SeasonCropInspectionBase
from models.schemas.base import SeasonCropInspectionResponse

router = APIRouter()

@router.get("/season-crop-inspection", response_model=List[SeasonCropInspectionResponse])
async def get_all_season_crop_inspection_data(
    db: Session = Depends(get_db)
):
    """
    Fetch fixed columns from season_crop_inspection_base without any filters.
    Returns up to 1000 records.
    """
    try:
        results = db.query(
            SeasonCropInspectionBase.crop_id,
            SeasonCropInspectionBase.season_id,
            SeasonCropInspectionBase.grower_id,
            SeasonCropInspectionBase.lot_id,
            SeasonCropInspectionBase.variety_id,
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
            SeasonCropInspectionBase.male_qty_in_kgs.label("male_qty"),      # match schema field
            SeasonCropInspectionBase.female_soaking_acre,
            SeasonCropInspectionBase.female_no_of_pkt,
            SeasonCropInspectionBase.female_qty_in_kgs.label("female_qty")   # match schema field
        ).limit(100).all()

        return [SeasonCropInspectionResponse(**row._asdict()) for row in results]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching data: {str(e)}")
