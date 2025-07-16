from pydantic import BaseModel, Field
from datetime import datetime
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
    male_parent_seed_lot_no: Optional[str] = Field(None, max_length=50)
    female_parent_seed_lot_no: Optional[str] = Field(None, max_length=50)


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
class LocationRecordCreate(BaseSchema):
    location_id: str = Field(..., max_length=20, description="Village ID")
    village: str = Field(..., max_length=100, description="Village name")
    mandal: Optional[str] = Field(None, max_length=100, description="Mandal name")
    mandal_id: Optional[str] = Field(None, max_length=20, description="Mandal ID")
    district: Optional[str] = Field(None, max_length=100, description="District name")
    district_id: Optional[str] = Field(None, max_length=20, description="District ID")
    state: Optional[str] = Field(None, max_length=100, description="State name")
    category_id: int = Field(..., description="Associated category ID")


class LocationRecordResponse(BaseSchema):
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
class GrowerRecordCreate(BaseSchema):
    grower_id: str = Field(..., max_length=20, description="Unique grower identifier")
    grower_name: str = Field(..., max_length=100, description="Grower name")
    fathers_name: Optional[str] = Field(None, max_length=100)
    grower_gender: Optional[str] = Field(None, max_length=10)
    category_id: int = Field(..., description="Associated category ID")
    # location_id: Optional[str] = Field(None, max_length=20)


class GrowerRecordResponse(BaseSchema):
    grower_id: str
    grower_name: str
    fathers_name: Optional[str]
    gender: Optional[str]
    category_id: int
    # location_id: Optional[str]
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
    errors: List[str] = []


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
    season_id: str
    crop_id: str
    variety_id: str
    location_id: str
    grower_id: str
    organizer_id: Optional[str]
    lot_id: Optional[str]

    hybrid_id: Optional[str]
    organizer_name: Optional[str]
    grower_name: Optional[str]
    grower_gender: Optional[str]
    purchasing_document_number: Optional[str]
    fathers_name: Optional[str]
    village: Optional[str]
    mandal: Optional[str]
    taluka_id: Optional[str]
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