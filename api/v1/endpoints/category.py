from fastapi import APIRouter, Query, HTTPException
from services.dynamic_category_records import list_all_categories, get_records_for_category

router = APIRouter()

@router.get("/list_categories")
def list_categories():
    return list_all_categories()

@router.get("/get_records_by_category")
def get_records_by_category(
    category_id: int = Query(...),
    page: int = 1,
    page_size: int = 25
):
    return get_records_for_category(category_id, page, page_size)
