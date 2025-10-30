from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime


class ComparisonTypeOption(BaseModel):
    """Comparison type option model"""
    value: str = Field(..., description="Comparison type value")
    label: str = Field(..., description="Display label")
    description: str = Field(..., description="Description of the comparison type")
    metric_field: str = Field(..., description="Database field name for this metric")
    source_table: str = Field(..., description="Source table for this metric")


class ForecastStageOption(BaseModel):
    """Forecast stage option model"""
    stage: str = Field(..., description="Stage field name")
    label: str = Field(..., description="Display label for the stage")


class ComparisonOptionsResponse(BaseModel):
    """Response model for comparison options"""
    comparison_types: List[ComparisonTypeOption]
    forecast_stages: List[ForecastStageOption]


class FilterOptionsResponse(BaseModel):
    """Response model for filter options"""
    seasons: List[str] = Field(..., description="Available seasons")
    crops: List[str] = Field(..., description="Available crops")
    varieties: List[str] = Field(..., description="Available varieties")
    plan_versions: List[str] = Field(..., description="Available plan revision versions")


class ComparisonColumnMetadata(BaseModel):
    """Column metadata for comparison results"""
    db_column_name: str = Field(..., description="Database column name")
    display_name: str = Field(..., description="Display name for the column")
    type: str = Field(..., description="Data type")
    group_name: str = Field(..., description="Group name for organizing columns")
    is_visible: bool = Field(True, description="Whether column is visible")
    is_editable: bool = Field(False, description="Whether column is editable")


class ComparisonDataRow(BaseModel):
    """Single row of comparison data"""
    season: Optional[str] = Field(None, description="Season")
    crop: Optional[str] = Field(None, description="Crop")
    variety: Optional[str] = Field(None, description="Variety")
    comparison_type_1: str = Field(..., description="First comparison type")
    comparison_type_2: str = Field(..., description="Second comparison type")
    comparison_value_1: float = Field(0.0, description="First comparison value")
    comparison_value_2: float = Field(0.0, description="Second comparison value")
    variance: float = Field(0.0, description="Difference between values")
    percentage_difference: float = Field(0.0, description="Percentage difference")
    actual_value: float = Field(0.0, description="Actual received quantity")
    plan_value: float = Field(0.0, description="Planned production allocation")
    forecast_value_1: float = Field(0.0, description="First forecast stage value")
    forecast_value_2: float = Field(0.0, description="Second forecast stage value")
    total_records: int = Field(0, description="Number of records contributing to this row")


class ComparisonSummary(BaseModel):
    """Summary statistics for the comparison"""
    comparison_type_1: str = Field(..., description="First comparison type")
    comparison_type_2: str = Field(..., description="Second comparison type")
    forecast_stage_1: Optional[str] = Field(None, description="First forecast stage if applicable")
    forecast_stage_2: Optional[str] = Field(None, description="Second forecast stage if applicable")
    total_variance: float = Field(0.0, description="Total variance across all records")
    avg_percentage_difference: float = Field(0.0, description="Average percentage difference")


class ComparisonFilters(BaseModel):
    """Applied filters for the comparison"""
    season: Optional[str] = Field(None, description="Season filter")
    crop: Optional[str] = Field(None, description="Crop filter")
    variety: Optional[str] = Field(None, description="Variety filter")
    plan_revision_version: Optional[str] = Field(None, description="Plan revision version")
    group_by_variety: bool = Field(True, description="Whether results are grouped by variety")


class SeedForecastComparisonResponse(BaseModel):
    """Main response model for seed forecast comparison"""
    columns: List[ComparisonColumnMetadata] = Field(..., description="Column metadata")
    data: List[ComparisonDataRow] = Field(..., description="Comparison data rows")
    total_count: int = Field(..., description="Total number of records")
    limit: int = Field(..., description="Page size limit")
    offset: int = Field(..., description="Page offset")
    comparison_summary: ComparisonSummary = Field(..., description="Summary statistics")
    filters_applied: ComparisonFilters = Field(..., description="Applied filters")


class ComparisonSummaryStats(BaseModel):
    """Detailed summary statistics"""
    total_records: int = Field(0, description="Total number of records")
    total_value_1: float = Field(0.0, description="Total value for first comparison type")
    total_value_2: float = Field(0.0, description="Total value for second comparison type")
    avg_value_1: float = Field(0.0, description="Average value for first comparison type")
    avg_value_2: float = Field(0.0, description="Average value for second comparison type")
    total_variance: float = Field(0.0, description="Total variance")
    avg_percentage_difference: float = Field(0.0, description="Average percentage difference")
    comparison_type_1: str = Field(..., description="First comparison type")
    comparison_type_2: str = Field(..., description="Second comparison type")


class ComparisonSummaryResponse(BaseModel):
    """Response model for comparison summary endpoint"""
    summary: ComparisonSummaryStats = Field(..., description="Summary statistics")


# Additional models for seed forecast data structure
class SeedForecastRecord(BaseModel):
    """Seed forecast record model"""
    id: int = Field(..., description="Record ID")
    grower_id: Optional[int] = Field(None, description="Grower ID")
    crop_id: Optional[int] = Field(None, description="Crop ID")
    lot_id: Optional[int] = Field(None, description="Lot ID")
    season_id: Optional[int] = Field(None, description="Season ID")
    variety_id: Optional[int] = Field(None, description="Variety ID")
    stage_forecast1: Optional[float] = Field(None, description="Stage 1 forecast value")
    stage_forecast2: Optional[float] = Field(None, description="Stage 2 forecast value")
    stage_forecast3: Optional[float] = Field(None, description="Stage 3 forecast value")
    stage_forecast4: Optional[float] = Field(None, description="Stage 4 forecast value")
    stage_forecast5: Optional[float] = Field(None, description="Stage 5 forecast value")
    stage_forecast6: Optional[float] = Field(None, description="Stage 6 forecast value")


class SupplyChainPlanningRecord(BaseModel):
    """Supply chain planning record model for comparison"""
    id: int = Field(..., description="Record ID")
    plan_revision_version: Optional[str] = Field(None, description="Plan revision version")
    season: Optional[str] = Field(None, description="Season")
    crop: Optional[str] = Field(None, description="Crop")
    variety: Optional[str] = Field(None, description="Variety")
    village: Optional[str] = Field(None, description="Village")
    grower: Optional[str] = Field(None, description="Grower")
    net_acres_current: Optional[float] = Field(None, description="Current net acres")
    productivity: Optional[float] = Field(None, description="Productivity")
    production_allocation: Optional[float] = Field(None, description="Production allocation")
    actual_net_acres: Optional[float] = Field(None, description="Actual net acres")
    adjusted_production_allocation: Optional[float] = Field(None, description="Adjusted production allocation")
    estimated_cost_per_kg: Optional[float] = Field(None, description="Estimated cost per kg")
    estimated_production_cost: Optional[float] = Field(None, description="Estimated production cost")
    actual_received_qty: Optional[float] = Field(None, description="Actual received quantity")
    actual_amount: Optional[float] = Field(None, description="Actual amount")
    actual_packaged_qty: Optional[float] = Field(None, description="Actual packaged quantity")
    actual_productivity: Optional[float] = Field(None, description="Actual productivity")
    created_at: Optional[datetime] = Field(None, description="Created timestamp")
    updated_at: Optional[datetime] = Field(None, description="Updated timestamp")


# Validation models for request parameters
class ComparisonRequest(BaseModel):
    """Request model for comparison parameters validation"""
    season: Optional[str] = Field(None, max_length=100, description="Season filter")
    crop: Optional[str] = Field(None, max_length=100, description="Crop filter")
    variety: Optional[str] = Field(None, max_length=100, description="Variety filter")
    plan_revision_version: str = Field("v1.0", max_length=50, description="Plan revision version")
    comparison_type_1: str = Field("actual", regex="^(actual|plan|forecast)$", description="First comparison type")
    comparison_type_2: str = Field("plan", regex="^(actual|plan|forecast)$", description="Second comparison type")
    forecast_stage_1: str = Field("stage_forecast1", regex="^stage_forecast[1-6]$", description="First forecast stage")
    forecast_stage_2: str = Field("stage_forecast2", regex="^stage_forecast[1-6]$", description="Second forecast stage")
    group_by_variety: bool = Field(True, description="Group results by variety")
    limit: int = Field(1000, ge=1, le=10000, description="Page size limit")
    offset: int = Field(0, ge=0, description="Page offset")

    class Config:
        schema_extra = {
            "example": {
                "season": "2024-25",
                "crop": "Tomato",
                "variety": "Hybrid-1",
                "plan_revision_version": "v1.0",
                "comparison_type_1": "actual",
                "comparison_type_2": "plan",
                "forecast_stage_1": "stage_forecast1",
                "forecast_stage_2": "stage_forecast2",
                "group_by_variety": True,
                "limit": 100,
                "offset": 0
            }
        }


# Error response models
class ErrorDetail(BaseModel):
    """Error detail model"""
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code")
    field: Optional[str] = Field(None, description="Field that caused the error")


class ErrorResponse(BaseModel):
    """Error response model"""
    error: str = Field(..., description="Error type")
    detail: str = Field(..., description="Error detail")
    errors: Optional[List[ErrorDetail]] = Field(None, description="Detailed error information")