from fastapi import APIRouter, Body, Query
from typing import List
from models.view_models import ViewSaveRequest, ViewResponse
from services.view_service import ViewService

router = APIRouter(prefix="/views", tags=["Views"])
view_service = ViewService()


@router.post("/save", response_model=ViewResponse)
def save_user_view(view_request: ViewSaveRequest = Body(...)):
    """Save or update a user’s view configuration"""
    return view_service.save_user_view(view_request)


@router.get("/", response_model=List[ViewResponse])
def get_user_views(user_id: str = Query(...)):
    """Get all saved views for a user"""
    return view_service.get_user_views(user_id)
