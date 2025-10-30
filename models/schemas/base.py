from pydantic import BaseModel, validator, Field, field_validator
from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey, Index
from datetime import datetime, date
from typing import Optional, List, Dict, Any



class BaseSchema(BaseModel):
    class Config:
        from_attributes = True
        populate_by_name = True


# models/schemas/category_schemas.py
class CategoryRecordCreate(BaseSchema):
    category_name: str = Field(..., max_length=100, description="Category name")
    description: Optional[str] = Field(None, max_length=500, description="Category description")


class CategoryRecordResponse(BaseSchema):
    category_id: int
    category_name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


# models/schemas/crop_schemas.py
class CropRecordCreate(BaseSchema):
    crop_id: str = Field(..., max_length=20, description="Unique crop identifier")
    crop_name: str = Field(..., max_length=100, description="Name of the crop")
    category_id: int = Field(..., description="Associated category ID")


class CropRecordResponse(BaseSchema):
    crop_id: str
    crop_name: str
    category_id: int
    created_at: datetime
    updated_at: datetime


# models/schemas/variety_schemas.py
class VarietyRecordCreate(BaseSchema):
    variety_id: str = Field(..., max_length=20, description="Unique variety identifier")
    variety_name: str = Field(..., max_length=100, description="Variety name from HSP Code")
    crop_id: str = Field(..., max_length=20, description="Associated crop ID")
    category_id: int = Field(..., description="Associated category ID")


class VarietyRecordResponse(BaseSchema):
    variety_id: str
    variety_name: str
    crop_id: str
    category_id: int
    male_seed_lot_no: Optional[str]
    female_seed_lot_no: Optional[str]
    created_at: datetime
    updated_at: datetime


# models/schemas/location_schemas.py
class LocationRecordCreate(BaseModel):
    location_id: str = Field(..., max_length=20, description="Customed Village ID")
    source_location_id: Optional[str] = Field(None, max_length=20, description="Village ID")
    village: str = Field(..., max_length=100, description="Village name")
    mandal: Optional[str] = Field(None, max_length=100, description="Mandal name")
    mandal_id: Optional[str] = Field(None, max_length=20, description="Mandal ID")
    district: Optional[str] = Field(None, max_length=100, description="District name")
    district_id: Optional[str] = Field(None, max_length=20, description="District ID")
    state: Optional[str] = Field(None, max_length=100, description="State name")
    category_id: int = Field(..., description="Associated category ID")

class SupplyChainPlanningResponse(BaseModel):
    id: Optional[int] = None
    plan_revision_version: Optional[str] = None
    season: Optional[str] = None
    crop: Optional[str] = None
    variety: Optional[str] = None
    village: Optional[str] = None
    grower: Optional[str] = None
    net_acres_current: Optional[float] = None
    productivity: Optional[float] = None
    production_allocation: Optional[float] = None
    availability_actual: Optional[float] = None
    adjusted_production_allocation: Optional[float] = None
    estimated_cost_per_kg: Optional[float] = None
    estimated_production_cost: Optional[float] = None
    category_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LocationRecordResponse(BaseSchema):
    source_location_id: str
    location_id: str
    village: str
    mandal_id: int
    district_id: int
    mandal: Optional[str]
    district: Optional[str]
    state: Optional[str]
    category_id: int
    created_at: datetime
    updated_at: datetime


# models/schemas/grower_schemas.py
class GrowerRecordCreate(BaseModel):
    grower_id: str = Field(..., max_length=20, description="Unique grower identifier")
    source_grower_id: Optional[str] = Field(None, max_length=20, description="Unique grower")
    grower_name: str = Field(..., max_length=100, description="Grower name")
    fathers_name: Optional[str] = Field(None, max_length=100)
    grower_gender: Optional[str] = Field(None, max_length=10)
    category_id: int = Field(..., description="Associated category ID")
    # location_id: Optional[str] = Field(None, max_length=20)


class GrowerRecordResponse(BaseSchema):
    grower_id: str
    source_grower_id: str
    grower_name: str
    fathers_name: Optional[str]
    gender: Optional[str]
    category_id: int
    created_at: datetime
    updated_at: datetime


# models/schemas/organizer_schemas.py
class OrganizerRecordCreate(BaseSchema):
    organizer_id: str = Field(..., max_length=20, description="Unique organizer identifier")
    organizer_name: str = Field(..., max_length=100, description="Organizer name")
    category_id: int = Field(..., description="Associated category ID")
    # production_plant: str = Field(..., max_length=100, description="Production Plant")


class OrganizerRecordResponse(BaseSchema):
    organizer_id: str
    organizer_name: str
    category_id: int
    # production_plant: str
    created_at: datetime
    updated_at: datetime


# models/schemas/inspection_schemas.py
class InspectionRecordCreate(BaseSchema):
    season_id: str = Field(..., max_length=20)
    crop_id: str = Field(..., max_length=20)
    variety_id: str = Field(..., max_length=20)
    grower_id: str = Field(..., max_length=20)
    location_id: str = Field(..., max_length=20)
    organizer_id: Optional[str] = Field(None, max_length=20)
    lot_id: Optional[str] = Field(None, max_length=50)
    category_id: int = Field(..., description="Associated category ID")

    # First Inspection fields
    first_inspection_date: Optional[datetime] = None
    first_inspection_stage: Optional[str] = Field(None, max_length=50)
    first_inspection_result: Optional[str] = Field(None, max_length=20)
    first_inspection_remarks: Optional[str] = None
    first_inspection_area_acres: Optional[float] = None
    first_inspection_plant_population: Optional[int] = None
    first_inspection_roguing_done: Optional[bool] = None
    first_inspection_isolation_distance: Optional[float] = None

    # Second Inspection fields
    second_inspection_date: Optional[datetime] = None
    second_inspection_stage: Optional[str] = Field(None, max_length=50)
    second_inspection_result: Optional[str] = Field(None, max_length=20)
    second_inspection_remarks: Optional[str] = None
    second_inspection_area_acres: Optional[float] = None
    second_inspection_plant_population: Optional[int] = None
    second_inspection_roguing_done: Optional[bool] = None
    second_inspection_isolation_distance: Optional[float] = None

    # Third Inspection fields
    third_inspection_date: Optional[datetime] = None
    third_inspection_stage: Optional[str] = Field(None, max_length=50)
    third_inspection_result: Optional[str] = Field(None, max_length=20)
    third_inspection_remarks: Optional[str] = None
    third_inspection_area_acres: Optional[float] = None
    third_inspection_plant_population: Optional[int] = None
    third_inspection_roguing_done: Optional[bool] = None
    third_inspection_isolation_distance: Optional[float] = None

    # Harvest and Yield fields
    harvest_date: Optional[datetime] = None
    expected_yield_kg: Optional[float] = None
    actual_yield_kg: Optional[float] = None
    moisture_content: Optional[float] = None
    purity_percentage: Optional[float] = None
    germination_percentage: Optional[float] = None

    # Status fields
    overall_status: Optional[str] = Field(None, max_length=20)
    final_recommendation: Optional[str] = Field(None, max_length=50)
    inspector_name: Optional[str] = Field(None, max_length=100)
    inspection_agency: Optional[str] = Field(None, max_length=100)


class InspectionRecordResponse(BaseSchema):
    id: int
    season_id: str
    crop_id: str
    variety_id: str
    grower_id: str
    location_id: str
    organizer_id: Optional[str]
    lot_id: Optional[str]
    category_id: int
    overall_status: Optional[str]
    final_recommendation: Optional[str]
    created_at: datetime
    updated_at: datetime


# models/schemas/metadata_schemas.py
class ColumnMetadataCreate(BaseSchema):
    db_column_name: str = Field(..., max_length=100, description="Database column name")
    display_name: str = Field(..., max_length=100, description="Display name for UI")
    type: str = Field(..., max_length=50, description="Data type")
    group_name: Optional[str] = Field(None, max_length=100, description="Group name for categorization")
    is_visible: bool = Field(True, description="Whether column is visible in UI")
    category_id: int = Field(..., description="Associated category ID")


class ColumnMetadataResponse(BaseSchema):
    id: int
    db_column_name: str
    display_name: str
    type: str
    group_name: Optional[str]
    is_visible: bool
    category_id: int
    created_at: datetime
    updated_at: datetime


# models/schemas/upload_schemas.py
class UploadResponse(BaseSchema):
    success: bool
    message: str
    inserted_records: Dict[str, int]
    total_rows_processed: int
    # errors: List[str] = []


class ExcelValidationError(BaseSchema):
    row: int
    column: str
    error: str
    value: Any


class ValidationReport(BaseSchema):
    valid_rows: int
    invalid_rows: int
    errors: List[ExcelValidationError]
    warnings: List[str]


class SeasonCropInspectionBaseCreate(BaseSchema):
    season_id: Optional[str] = None
    crop_id:Optional[str] = None
    variety_id: Optional[str] = None
    location_id: Optional[str] = None
    grower_id: Optional[str] = None
    organizer_id: Optional[str] = None
    lot_id: Optional[str]
    production_code: Optional[str] = None
    production_location: Optional[str] = None
    hybrid_id: Optional[str]
    organizer_name: Optional[str]
    grower_name: Optional[str]
    grower_gender: Optional[str]
    purchasing_document_number: Optional[float]
    fathers_name: Optional[str]
    village: Optional[str]
    mandal: Optional[str]
    mandal_id: Optional[str]
    district: Optional[str]
    district_id: Optional[str]
    state: Optional[str]

    male_parent_seed_lot_no: Optional[str]
    male_soaking_acre: Optional[float]
    male_no_of_pkt: Optional[float]
    male_qty_in_kgs: Optional[float]

    female_parent_seed_lot_no: Optional[str]
    female_soaking_acre: Optional[float]
    female_no_of_pkt: Optional[float]
    female_qty_in_kgs: Optional[float]

    production_officer: Optional[str]
    tfa_name: Optional[str]
    production_plant: Optional[str]

class YieldRecordBase(BaseModel):
    # Make all fields optional with proper defaults
    grower_id: Optional[str] = ""
    crop_id: Optional[str] = ""
    lot_id: Optional[str] = ""
    season_id: Optional[str] = ""
    variety_id: Optional[str] = ""

    physical_received_qty_as_per_sap: Optional[float] = None
    m1_soaking_date: Optional[date] = None
    m1_soaking_slab: Optional[str] = None
    female_soaking_date: Optional[date] = None
    female_tp_date: Optional[date] = None

    sowing_acres: Optional[float] = None
    net_tp_acres: Optional[float] = None
    net_acerage_area: Optional[float] = None
    final_harvestable_area: Optional[float] = None

    sum_of_received_raw_qty: Optional[float] = None
    qty: Optional[float] = None
    rate_per_kg: Optional[float] = None
    amount_inr: Optional[float] = None

    productivity_of_packed_seed: Optional[float] = None
    packed_qt: Optional[float] = None
    productvity: Optional[float] = None

    slab: Optional[str] = None
    pos_done_b: Optional[str] = None
    production_manager: Optional[str] = None
    production_plant: Optional[str] = None
    production_location: Optional[str] = None
    production_co: Optional[str] = None

    po_soaking_acres: Optional[float] = None
    purchase_order: Optional[str] = None
    planting_list_soaking_acres: Optional[float] = None
    net_acres: Optional[float] = None

    tp_days: Optional[int] = None
    tp_days_slab: Optional[str] = None

    class Config:
        # Allow extra fields to be ignored
        extra = "ignore"
        # Convert string 'nan' to None
        validate_assignment = True


class YieldRecordUpdate(BaseModel):
    physical_received_qty_as_per_sap: Optional[float]
    packed_qt: Optional[float]
    productvit: Optional[float]
    slab: Optional[str]
    pos_done_b: Optional[str]
    production_manager: Optional[str]
    production_plant: Optional[str]
    production_location: Optional[str]
    production_co: Optional[str]
    po_soaking_acres: Optional[float]
    purchase_order: Optional[str]
    planting_list_soaking_acres: Optional[float]
    net_acres: Optional[float]
    tp_days: Optional[int]
    tp_days_slab: Optional[str]

class YieldRecordResponse(YieldRecordBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

    @field_validator('production_co', mode='before')
    def validate_production_co(cls, v):
        if v is None:
            return v
        return str(v)

class SeasonCropInspectionBaseOut(BaseModel):
    crop_id: int
    season_id: int
    grower_id: int
    lot_id: int
    variety_id: int
    organizer_name: str
    grower_name: str
    grower_gender: str
    fathers_name: str
    village: str
    mandal: str
    district: str
    state: str
    male_soaking_acre: float
    male_no_of_pkt: int
    male_qty: float
    female_soaking_acre: float
    female_no_of_pkt: int
    female_qty: float

class SeasonCropInspectionResponse(BaseModel):
    crop_name: Optional[str]
    season_name: Optional[str]
    variety_name: Optional[str]
    organizer_name: Optional[str]
    grower_name: Optional[str]
    grower_gender: Optional[str]
    fathers_name: Optional[str]
    village: Optional[str]
    mandal: Optional[str]
    district: Optional[str]
    stage_forecast1:Optional[float]
    stage_forecast2:Optional[float]

    class Config:
        orm_mode = True

class YieldSummaryResponse(BaseModel):
    season_name: Optional[str]
    crop_name: Optional[str]
    variety_name: Optional[str]
    village: Optional[str] = None
    grower_name: Optional[str] = None
    total_received_qty: float
    total_packed_qty: float
    avg_productivity: Optional[float]
    forecast_1: Optional[float]
    forecast_2: Optional[float]

class SeedForecastBase(BaseModel):
    grower_id: str
    crop_id: str
    lot_id: str
    season_id: Optional[str]
    variety_id: Optional[str]
    stage_forecast1: Optional[float]
    stage_forecast2: Optional[float]
    stage_forecast3: Optional[float]
    stage_forecast4: Optional[float]
    stage_forecast5: Optional[float]
    stage_forecast6: Optional[float]

    class Config:
        orm_mode = True




# class DynamicYieldRecordSchema(BaseModel):
#     grower_id: str
#     crop_id: str
#     lot_id: str
#     season_id: str
#     variety_id: str
#     yield_data: Optional[dict] = Field(default_factory=dict)
#
#     class Config:
#         orm_mode = True
