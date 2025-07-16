from typing import List, Optional, Union, Annotated
from pydantic import BaseModel, Field, model_validator
from enum import Enum

class FilterCondition(str, Enum):
    EQUALS = "equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CONTAINS = "contains"
    BEFORE = "before"
    AFTER = "after"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"

class DataType(str, Enum):
    INTEGER = "integer"
    STRING = "text"
    DATE = "date"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"

class ColumnInfo(BaseModel):
    name: str
    data_type: DataType

class FilterItem(BaseModel):
    field: str
    condition: FilterCondition
    value: Optional[Union[str, int, bool]] = None

    @model_validator(mode="after")
    def validate_value_for_condition(self):
        if self.condition in [FilterCondition.IS_TRUE, FilterCondition.IS_FALSE]:
            return self
        if self.value is None:
            raise ValueError(f"Value is required for condition: {self.condition}")
        return self


class FilterRequest(BaseModel):
  filters: Annotated[List[FilterItem], Field(default_factory=list)]
