from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from core.db import get_db
from models.db_models import YieldInspectionView
from models.schemas.base import YieldSummaryResponse

router = APIRouter()

@router.get("/yield-summary", response_model=List[YieldSummaryResponse])
async def get_yield_summary(
    db: Session = Depends(get_db),
    crop_id: Optional[str] = Query(None),
    season_id: Optional[str] = Query(None),
    variety_id: Optional[str] = Query(None),
    village: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0)
):
    try:
        # Base selected columns (always included)
        base_columns = [
            YieldInspectionView.crop_id,
            YieldInspectionView.season_id,
            YieldInspectionView.variety_id,
            YieldInspectionView.crop_name,
            YieldInspectionView.season_name,
            YieldInspectionView.variety_name,
            func.sum(YieldInspectionView.physical_received_qty).label("total_received_qty"),
            func.sum(YieldInspectionView.packed_qty).label("total_packed_qty"),
            func.sum(YieldInspectionView.net_acres).label("total_net_acres"),
            func.sum(YieldInspectionView.stage_forecast1).label("forecast_1"),
            func.sum(YieldInspectionView.stage_forecast2).label("forecast_2")
        ]

        # Base group by fields (always included)
        group_by_fields = [
            YieldInspectionView.crop_id,
            YieldInspectionView.season_id,
            YieldInspectionView.variety_id,
            YieldInspectionView.crop_name,
            YieldInspectionView.season_name,
            YieldInspectionView.variety_name
        ]

        # Track what additional fields we're selecting
        include_village = False
        include_grower = False

        # Dynamic column addition based on query requirements
        # Add village if we have variety_id specified (for drilling down)
        if variety_id and not village:  # Show villages when filtering by variety
            base_columns.append(YieldInspectionView.village)
            group_by_fields.append(YieldInspectionView.village)
            include_village = True

        # Add grower_name if village filter is applied
        if village:  # Show growers when filtering by village
            base_columns.extend([
                YieldInspectionView.village,
                YieldInspectionView.grower_name
            ])
            group_by_fields.extend([
                YieldInspectionView.village,
                YieldInspectionView.grower_name
            ])
            include_village = True
            include_grower = True

        # Build query
        query = db.query(*base_columns)

        # Apply filters
        if crop_id:
            query = query.filter(YieldInspectionView.crop_id == crop_id)
        if season_id:
            query = query.filter(YieldInspectionView.season_id == season_id)
        if variety_id:
            query = query.filter(YieldInspectionView.variety_id == variety_id)
        if village:
            query = query.filter(YieldInspectionView.village.ilike(f"%{village}%"))

        # Group by and apply pagination
        query = query.group_by(*group_by_fields).offset(offset).limit(limit)

        rows = query.all()

        final_results = []

        for row in rows:
            # Safely handle potential None values from aggregation
            total_received_qty = float(row.total_received_qty) if row.total_received_qty is not None else 0.0
            total_packed_qty = float(row.total_packed_qty) if row.total_packed_qty is not None else 0.0
            total_net_acres = float(row.total_net_acres) if row.total_net_acres is not None else 0.0
            forecast_1 = float(row.forecast_1) if row.forecast_1 is not None else 0.0
            forecast_2 = float(row.forecast_2) if row.forecast_2 is not None else 0.0

            # Calculate productivity
            avg_productivity = (
                total_packed_qty / total_net_acres if total_net_acres > 0 else None
            )

            # Build result dictionary with required fields
            result = {
                "crop_name": row.crop_name,
                "season_name": row.season_name,
                "variety_name": row.variety_name,
                "village": None,  # Initialize with None
                "grower_name": None,  # Initialize with None
                "total_received_qty": round(total_received_qty, 2) if total_received_qty is not None else None,
                "total_packed_qty": round(total_packed_qty, 2) if total_packed_qty is not None else None,
                "avg_productivity": round(avg_productivity, 2) if avg_productivity is not None else None,
                "forecast_1": round(forecast_1, 2) if forecast_1 is not None else None,
                "forecast_2": round(forecast_2, 2) if forecast_2 is not None else None
            }

            # Add village if it was selected
            if include_village:
                result["village"] = getattr(row, "village", None)

            # Add grower_name if it was selected
            if include_grower:
                result["grower_name"] = getattr(row, "grower_name", None)

            # Create and validate the response
            try:
                response_item = YieldSummaryResponse(**result)
                final_results.append(response_item.model_dump(exclude_none=False))
            except Exception as validation_error:
                print(f"Validation error for row: {result}")
                print(f"Error: {validation_error}")
                continue

        return final_results

    except Exception as e:
        print(f"Full error: {str(e)}")  # Add for debugging
        raise HTTPException(status_code=500, detail=f"Error fetching yield summary: {str(e)}")
