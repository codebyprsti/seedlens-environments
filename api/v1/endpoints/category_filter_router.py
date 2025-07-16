from fastapi import APIRouter, Query, Body
from models.filter_models import FilterRequest
from services.category_service import CategoryService
from core.db import get_connection, release_connection

router = APIRouter()
category_service = CategoryService()

@router.post("/filtered_data")
def get_filtered_data(
    category_id: int = Query(...),
    filter_request: FilterRequest = Body(...)
):
    conn = get_connection()
    try:
        # Dynamically resolve table name for category
        category_info = category_service.get_category_info(conn, category_id)

        # Apply filters
        filtered_data = category_service.filter_service.apply_filters(
            conn=conn,
            schema="seedworks",
            table=category_info["table_name"],
            category_id=category_id,
            filters=filter_request.filters,
            additional_columns=[],
            category_column="category_id"
        )

        return {
            "category_id": category_id,
            "category_name": category_info["category_name"],
            "total_records": len(filtered_data),
            "data": filtered_data,
            "applied_filters": [
                {
                    "field": f.field,
                    "condition": f.condition,
                    "value": f.value
                } for f in filter_request.filters
            ]
        }
    finally:
        release_connection(conn)
