from fastapi import APIRouter, Body
from api.v1.endpoints import upload, category, full_data, category_filter_router, views, add_record



api_router = APIRouter()

api_router.include_router(add_record.router, tags=["Add Record"])
api_router.include_router(upload.router, tags=["Upload"])
api_router.include_router(category.router, tags=["Category"])
api_router.include_router(full_data.router, tags=["Full Data"])
api_router.include_router(category_filter_router.router, tags=["Category filters"])
api_router.include_router(views.router, tags=["Views"])

