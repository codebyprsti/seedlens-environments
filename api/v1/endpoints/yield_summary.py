from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from core.db import get_db
from models.db_models import YieldInspectionView  # your SQLAlchemy mapped view
from models.schemas.base import YieldSummaryResponse  # create this schema

router = APIRouter()

@router.get("/yield-summary", response_model=List[YieldSummaryResponse])
async def get_yield_summary(
    db: Session = Depends(get_db),
    crop_id: Optional[str] = Query(None),
    season_id: Optional[str] = Query(None),
    variety_id: Optional[str] = Query(None),
    village: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """
    Aggregates yield data grouped by crop, season, variety, and village.
    """
    try:
        query = db.query(
            YieldInspectionView.crop_id,
            YieldInspectionView.season_id,
            YieldInspectionView.variety_id,
            YieldInspectionView.village,
            func.sum(YieldInspectionView.physical_received_qty).label("total_received_qty"),
            func.sum(YieldInspectionView.packed_qty).label("total_packed_qty"),
            func.avg(YieldInspectionView.productivity).label("avg_productivity")
        ).group_by(
            YieldInspectionView.crop_id,
            YieldInspectionView.season_id,
            YieldInspectionView.variety_id,
            YieldInspectionView.village
        )

        # Optional filters
        if crop_id:
            query = query.filter(YieldInspectionView.crop_id == crop_id)
        if season_id:
            query = query.filter(YieldInspectionView.season_id == season_id)
        if variety_id:
            query = query.filter(YieldInspectionView.variety_id == variety_id)
        if village:
            query = query.filter(YieldInspectionView.village.ilike(f"%{village}%"))

        query = query.offset(offset).limit(limit)

        rows = query.all()

        results = []
        for row in rows:
            results.append(YieldSummaryResponse(
                crop_id=row.crop_id,
                season_id=row.season_id,
                variety_id=row.variety_id,
                village=row.village,
                total_received_qty=row.total_received_qty,
                total_packed_qty=row.total_packed_qty,
                avg_productivity=row.avg_productivity
            ))

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching yield summary: {str(e)}")
