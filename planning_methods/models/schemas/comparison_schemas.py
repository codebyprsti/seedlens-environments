from pydantic import BaseModel
from typing import Optional, List, Literal
from enum import Enum


class ComparisonType(str, Enum):
    ACTUAL = "actual"
    PLAN = "plan"
    FORECAST = "forecast"


class ComparisonRequest(BaseModel):
    season: str
    crop: str
    variety: str
    plan_revision_version: Optional[str] = None
    comparison_type_1: ComparisonType
    comparison_type_2: ComparisonType

    class Config:
        use_enum_values = True


class ComparisonData(BaseModel):
    grower: str
    village: str
    type_1_value: Optional[float] = None
    type_2_value: Optional[float] = None
    variance: Optional[float] = None
    variance_percentage: Optional[float] = None


class ComparisonResponse(BaseModel):
    season: str
    crop: str
    variety: str
    comparison_type_1: str
    comparison_type_2: str
    plan_revision_version: Optional[str] = None
    total_type_1: float
    total_type_2: float
    total_variance: float
    total_variance_percentage: float
    data: List[ComparisonData]