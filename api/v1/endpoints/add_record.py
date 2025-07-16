# api/v1/endpoints/add_record.py

from fastapi import APIRouter, Query, Body
from core.db import get_connection, release_connection
from services.dynamic_category_records import RecordService

router = APIRouter()
record_service = RecordService()

@router.post("/record/add")
def add_dynamic_record(category_id: int = Query(...), payload: dict = Body(...)):
    conn = get_connection()
    try:
        result = record_service.add_record(conn, category_id, payload)
        return {
            "message": "Record added successfully",
            "data": result
        }
    finally:
        release_connection(conn)
