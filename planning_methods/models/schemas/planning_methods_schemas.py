from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from decimal import Decimal

class ConstraintModel(BaseModel):
    """Model for planning constraints"""
    type: str = Field(..., description="Type of constraint (net_acres, budget)")
    basis: str = Field(..., description="Basis for calculation (carry_forward, weighted_average, custom)")
    value: Optional[float] = Field(None, description="Value for the constraint")
    formula: Optional[str] = Field(None, description="Custom formula for constraint calculation")

class RangeModel(BaseModel):
    """Model for range constraints"""
    min: Optional[float] = Field(None, description="Minimum allocation threshold")
    max: Optional[float] = Field(None, description="Maximum allocation threshold")

class PlanningMethodsRequest(BaseModel):
    """Request model for planning methods"""
    # Line of Business (LOB) - Progressive disclosure
    season: str = Field(..., description="Selected season")
    crop: str = Field(..., description="Selected crop")
    state: str = Field(..., description="Selected state")
    variety: str = Field(..., description="Selected variety (or 'All Varieties')")
    village: str = Field(..., description="Selected village")
    grower: str = Field(..., description="Selected grower (or 'All Growers')")
    
    # Planning level
    plan_level: int = Field(..., description="Level at which planning is done (1-6)")
    
    # Method type
    method_type: str = Field(..., description="Method type (minimization, maximization, min_max)")
    
    # Target measure
    target_measure: str = Field(..., description="Target measure (planned_allocation)")
    
    # Magnitude
    magnitude: float = Field(..., description="Total target quantity")
    
    # Criteria
    criteria: str = Field(..., description="Criteria based on method type")
    
    # Value fields
    value_fields: List[str] = Field(..., description="Selected value fields (productivity, cost)")
    
    # Constraints
    constraints: List[ConstraintModel] = Field(..., description="Planning constraints")
    
    # Range
    range_min: Optional[float] = Field(None, description="Minimum allocation range")
    range_max: Optional[float] = Field(None, description="Maximum allocation range")
    
    # Additional options
    include_all_varieties: bool = Field(False, description="Include all varieties in plan")
    multiple_varieties: List[str] = Field(default_factory=list, description="Multiple selected varieties")

class PlanningMethodsResponse(BaseModel):
    """Response model for planning methods"""
    plan_version: str = Field(..., description="Generated plan version")
    plan_data: List[Dict[str, Any]] = Field(..., description="Generated plan data")
    summary: Dict[str, Any] = Field(..., description="Plan summary statistics")
    constraints_applied: List[ConstraintModel] = Field(..., description="Applied constraints")
    method_used: str = Field(..., description="Method used for planning")
    
    # Additional metadata
    total_allocated_production: float = Field(..., description="Total allocated production")
    total_allocated_acres: float = Field(..., description="Total allocated acres")
    total_probable_cost: float = Field(..., description="Total probable cost")
    average_productivity: float = Field(..., description="Average productivity")
    average_cost_per_kg: float = Field(..., description="Average cost per kg")
    number_of_allocations: int = Field(..., description="Number of allocations")

class PlanVersionModel(BaseModel):
    """Model for plan version information"""
    plan_revision_version: str = Field(..., description="Plan version identifier")
    created_at: str = Field(..., description="Creation timestamp")
    record_count: int = Field(..., description="Number of records in plan")

class PlanUpdateRequest(BaseModel):
    """Request model for plan updates"""
    updates: List[Dict[str, Any]] = Field(..., description="List of updates to apply")
    reason: Optional[str] = Field(None, description="Reason for update")

class PlanUpdateResponse(BaseModel):
    """Response model for plan updates"""
    message: str = Field(..., description="Update status message")
    new_version: str = Field(..., description="New plan version")
    original_version: str = Field(..., description="Original plan version")

class DimensionOptionsResponse(BaseModel):
    """Response model for dimension options"""
    dimension_types: Dict[int, str] = Field(..., description="Available dimension types")
    method_types: Dict[str, str] = Field(..., description="Available method types")
    constraint_types: Dict[str, str] = Field(..., description="Available constraint types")
    basis_selections: Dict[str, str] = Field(..., description="Available basis selections")
    options: Dict[str, List[str]] = Field(..., description="Available options for each dimension")

class ProgressiveOptionsResponse(BaseModel):
    """Response model for progressive options"""
    level: int = Field(..., description="Current selection level")
    options: List[str] = Field(..., description="Available options for current level")

class PlanSummaryModel(BaseModel):
    """Model for plan summary statistics"""
    total_allocated_production: float = Field(..., description="Total allocated production")
    total_allocated_acres: float = Field(..., description="Total allocated acres")
    total_probable_cost: float = Field(..., description="Total probable cost")
    average_productivity: float = Field(..., description="Average productivity")
    average_cost_per_kg: float = Field(..., description="Average cost per kg")
    number_of_allocations: int = Field(..., description="Number of allocations")

class PlanningDataModel(BaseModel):
    """Model for individual planning data record"""
    plan_revision_version: str = Field(..., description="Plan version")
    season: str = Field(..., description="Season")
    crop: str = Field(..., description="Crop")
    variety: str = Field(..., description="Variety")
    village: str = Field(..., description="Village")
    state: str = Field(..., description="State")
    grower: str = Field(..., description="Grower")
    
    # Y0 data (current year)
    y0_net_acres: float = Field(..., description="Y0 net acres")
    y0_avg_productivity: float = Field(..., description="Y0 average productivity")
    
    # Planning data
    production_allocation: float = Field(..., description="Production allocation")
    planned_allocation: float = Field(..., description="Planned allocation")
    available_net_acres: float = Field(..., description="Available net acres")
    allocated_acres: float = Field(..., description="Allocated acres")
    adjusted_production_allocation: float = Field(..., description="Adjusted production allocation")
    
    # Cost data
    estimated_cost_per_kg: float = Field(..., description="Estimated cost per kg")
    estimated_production_cost: float = Field(..., description="Estimated production cost")
    probable_cost: float = Field(..., description="Probable cost")
    
    # Y1 data (next year)
    y1_received_qty: float = Field(..., description="Y1 received quantity")
    y1_packed_qty: float = Field(..., description="Y1 packed quantity")
    y1_net_acres: float = Field(..., description="Y1 net acres")
    y1_productivity: float = Field(..., description="Y1 productivity")
    y1_amount: float = Field(..., description="Y1 amount")
    
    # Location data
    longitude: float = Field(..., description="Longitude")
    latitude: float = Field(..., description="Latitude")
    
    # Metadata
    hybrid_type: Optional[str] = Field(None, description="Hybrid type")
    years_available: Optional[int] = Field(None, description="Years of data available")
    data_source: Optional[str] = Field(None, description="Data source")
    is_planned_village: Optional[bool] = Field(None, description="Is planned village")

class ConstraintBasedOutputModel(BaseModel):
    """Model for constraint-based output columns"""
    # Y0 data (current year)
    y0_net_acres: float = Field(..., description="A - Y0 Net Acres (carried forward from previous season)")
    
    # Algorithm outputs
    productivity: float = Field(..., description="B - Productivity (from algorithm)")
    planned_allocation: float = Field(..., description="C - Planned Allocation (from algorithm)")
    available_net_acres: float = Field(..., description="D - Available Net Acres (constraint-based)")
    adjusted_production_allocation: float = Field(..., description="E - Adjusted Production Allocation")
    cost_per_kg: float = Field(..., description="F - Cost per kg (from algorithm)")
    estimated_production_cost: float = Field(..., description="G - Estimated Production Cost")
    
    # Manual adjustment support
    is_manually_adjusted: bool = Field(False, description="Whether manually adjusted via Excel")
    adjustment_reason: Optional[str] = Field(None, description="Reason for manual adjustment")
    adjustment_timestamp: Optional[str] = Field(None, description="Timestamp of adjustment")
