import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.sql import func as sql_func
from typing import List, Optional
from core.db import get_db
from models.db_models import YieldInspectionView, SeasonCropInspectionBase
from models.schemas.base import YieldSummaryResponse

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/yield-summary", response_model=List[YieldSummaryResponse])
async def get_yield_summary(
    db: Session = Depends(get_db),
    crop_id: Optional[str] = Query(None),
    season_id: Optional[str] = Query(None),
    variety_id: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    village: Optional[str] = Query(None),
    limit: int = Query(300),
    offset: int = Query(0)
):
    try:
        # Log incoming parameters for debugging
        logger.info(f"[yield-summary] Incoming params: season_id={season_id}, crop_id={crop_id}, "
                   f"variety_id={variety_id}, state={state}, village={village}, limit={limit}, offset={offset}")
        
        # Normalize "ALL" values for case-insensitive comparison
        variety_normalized = str(variety_id).strip().lower() if variety_id else ""
        village_normalized = str(village).strip().lower() if village else ""
        state_normalized = str(state).strip().lower() if state else ""
        
        # Determine grouping based on filter selections
        # Base selected columns (always included)
        base_columns = [
            YieldInspectionView.season_id,
            YieldInspectionView.season_name,
            YieldInspectionView.crop_id,
            YieldInspectionView.crop_name,
            func.sum(YieldInspectionView.physical_received_qty).label("total_received_qty"),
            func.sum(YieldInspectionView.packed_qty).label("total_packed_qty"),
            func.sum(YieldInspectionView.net_acres).label("total_net_acres"),
            func.sum(YieldInspectionView.stage_forecast1).label("forecast_1"),
            func.sum(YieldInspectionView.stage_forecast2).label("forecast_2")
        ]

        # Base group by fields (always season and crop)
        group_by_fields = [
            YieldInspectionView.season_id,
            YieldInspectionView.season_name,
            YieldInspectionView.crop_id,
            YieldInspectionView.crop_name
        ]

        # Track what additional fields we're selecting
        include_variety = False
        include_village = False
        include_grower = False
        include_state = False  # State only when variety is selected

        # Determine roll-up level based on filters
        # Rule 1: When only season is selected -> Group by season + crop (variety = NULL, state = NULL, village = NULL)
        if season_id and not crop_id and not variety_id:
            # Group by season + crop only, variety will be NULL, state will be NULL, village will be NULL
            include_variety = False
            include_village = False
            include_state = False
        
        # Rule 2: When crop is selected
        elif crop_id:
            # Always include variety when crop is selected
            base_columns.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name
            ])
            group_by_fields.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name
            ])
            include_variety = True
            include_state = True  # Include state when variety is selected
            
            # Rule 2a: When variety = "ALL" -> Villages should be NULL (no village-level grouping)
            if variety_normalized == "all":
                include_village = False
            # Rule 2b: When specific variety is selected -> Villages should be rolled up
            elif variety_id and variety_normalized != "all":
                # If specific village selected, include village and grower
                if village and village_normalized != "all":
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
                # If no village filter or village = "ALL", show villages rolled up
                else:
                    base_columns.append(YieldInspectionView.village)
                    group_by_fields.append(YieldInspectionView.village)
                    include_village = True
        
        # Rule 3: When variety = "ALL" and village = "ALL" -> Group by state (state-level grouping)
        elif variety_id and variety_normalized == "all" and village and village_normalized == "all":
            base_columns.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name,
                YieldInspectionView.village,
                func.max(YieldInspectionView.state).label("state")  # Direct state for grouping
            ])
            group_by_fields.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name,
                YieldInspectionView.village,
                YieldInspectionView.state  # Group by state for state-level grouping
            ])
            include_variety = True
            include_village = True
            include_state = True  # State-level grouping
        
        # Rule 4: When specific variety is selected -> Show village-level, state-level, and variety-level grouping
        elif variety_id and variety_normalized != "all":
            base_columns.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name
            ])
            group_by_fields.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name
            ])
            include_variety = True
            include_state = True  # Include state for state-level grouping
            
            # If specific village selected, include village and grower
            if village and village_normalized != "all":
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
            # If village = "ALL" or no village filter, show villages rolled up
            else:
                base_columns.append(YieldInspectionView.village)
                group_by_fields.append(YieldInspectionView.village)
                include_village = True
        
        # Default: Group by season + crop + variety (when no filters)
        else:
            base_columns.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name
            ])
            group_by_fields.extend([
                YieldInspectionView.variety_id,
                YieldInspectionView.variety_name
            ])
            include_variety = True
            include_village = False
            include_state = True  # Include state when variety is included
        
        # Add state column only when variety is selected
        # YieldInspectionView already has state column, so no join needed!
        if include_state:
            # Use MAX() to aggregate state (handles potential NULLs from outerjoin)
            # Try without trim first to see if that's causing the issue
            state_expr = func.max(YieldInspectionView.state).label("state")
            base_columns.insert(4, state_expr)
            # Add state to GROUP BY (using YieldInspectionView.state directly)
            group_by_fields.append(YieldInspectionView.state)

        # Build query - no join needed for state since YieldInspectionView has it
        query = db.query(*base_columns)
        
        # Apply filters (handle "ALL" values). Hierarchical dependency: State → District → Village.
        if season_id:
            query = query.filter(YieldInspectionView.season_id == season_id)
        if crop_id:
            query = query.filter(YieldInspectionView.crop_id == crop_id)
        if variety_id and variety_normalized != "all":
            query = query.filter(YieldInspectionView.variety_id == variety_id)
        elif variety_id and variety_normalized == "all":
            # When variety = "ALL", exclude NULL varieties to ensure no NULL varieties appear
            query = query.filter(YieldInspectionView.variety_id.isnot(None))
            query = query.filter(YieldInspectionView.variety_name.isnot(None))
            query = query.filter(YieldInspectionView.variety_name != '')
        # State filter (hierarchical: State → District → Village)
        if state and state_normalized != "all":
            query = query.filter(func.lower(func.trim(YieldInspectionView.state)) == state_normalized)
        if village and village_normalized != "all":
            query = query.filter(func.lower(func.trim(YieldInspectionView.village)) == village_normalized)
            # Ensure village belongs to selected state (hierarchical dependency)
            if state and state_normalized != "all":
                query = query.filter(func.lower(func.trim(YieldInspectionView.state)) == state_normalized)
        elif village and village_normalized == "all":
            # When village = "ALL", exclude NULL villages to ensure no NULL villages appear
            query = query.filter(YieldInspectionView.village.isnot(None))
            query = query.filter(YieldInspectionView.village != '')

        # Group by and apply pagination
        # State is already in group_by_fields if include_state is True
        query = query.group_by(*group_by_fields).offset(offset).limit(limit)

        # Log SQL query for debugging (before execution)
        logger.debug(f"[yield-summary] SQL query: {str(query.statement.compile(compile_kwargs={'literal_binds': True}))}")
        logger.info(f"[yield-summary] GROUP BY fields: {[str(f) for f in group_by_fields]}")

        rows = query.all()
        logger.info(f"[yield-summary] Query returned {len(rows)} rows")

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

            # Get and clean state value (trim whitespace) - only if state is included
            state_value = None
            if include_state:
                state_value = getattr(row, "state", None)
                if state_value and isinstance(state_value, str):
                    state_value = state_value.strip() or None
                elif state_value is None:
                    state_value = None

            # Build result dictionary with required fields
            result = {
                "crop_name": row.crop_name,
                "season_name": row.season_name,
                "variety_name": row.variety_name if include_variety else None,  # Set to NULL if not in grouping
                "village": None,  # Initialize with None
                "grower_name": None,  # Initialize with None
                "state": state_value,  # Include state when variety is selected
                "total_received_qty": round(total_received_qty, 2) if total_received_qty is not None else 0.0,
                "total_packed_qty": round(total_packed_qty, 2) if total_packed_qty is not None else 0.0,
                "avg_productivity": round(avg_productivity, 2) if avg_productivity is not None else None,
                "forecast_1": round(forecast_1, 2) if forecast_1 is not None else 0.0,
                "forecast_2": round(forecast_2, 2) if forecast_2 is not None else 0.0
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

        logger.info(f"[yield-summary] Returning {len(final_results)} results")
        return final_results

    except Exception as e:
        logger.exception(f"[yield-summary] Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching yield summary: {str(e)}")