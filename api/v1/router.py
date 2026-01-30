from fastapi import APIRouter, Body
from api.v1.endpoints import upload, category, full_data, category_filter_router, views, add_record, inspection, yield_summary, operations_drop_down, supply_chain_planning, seed_forecast_comparison, locations
from api.v1.endpoints.cumulative import routes as cumulative_routes
from api.v1.endpoints.agro_entities import seasons, crops, varieties



api_router = APIRouter()

api_router.include_router(add_record.router, tags=["Add Record"])
api_router.include_router(upload.router, tags=["Upload"])
api_router.include_router(category.router, tags=["Category"])
api_router.include_router(full_data.router, tags=["Full Data"])
api_router.include_router(category_filter_router.router, tags=["Category filters"])
api_router.include_router(views.router, tags=["Views"])
api_router.include_router(inspection.router, tags=["Inspection Data"])
api_router.include_router(yield_summary.router, tags=["yield summary Data"])
api_router.include_router(operations_drop_down.router, tags=["Drop-Down Options"])
api_router.include_router(supply_chain_planning.router, tags=["Supply Chain Data"])
api_router.include_router(seed_forecast_comparison.router, tags=["seed-forecast-comparison"])
api_router.include_router(locations.router, tags=["Locations"])
api_router.include_router(cumulative_routes.router, tags=["Cumulative Inspection Data"])
api_router.include_router(seasons.router, tags=["Agro Entities"])
api_router.include_router(crops.router, tags=["Agro Entities"])
api_router.include_router(varieties.router, tags=["Agro Entities"])






