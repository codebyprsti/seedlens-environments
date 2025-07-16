from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from datetime import datetime
import psycopg2.extras

from models.filter_models import FilterItem, FilterCondition, DataType, ColumnInfo


class DynamicFilterService:
  """Service class for building dynamic SQL filters"""

  def __init__(self):
    self.condition_handlers = {
      FilterCondition.EQUALS: self._handle_equals,
      FilterCondition.GREATER_THAN: self._handle_greater_than,
      FilterCondition.LESS_THAN: self._handle_less_than,
      FilterCondition.CONTAINS: self._handle_contains,
      FilterCondition.BEFORE: self._handle_before,
      FilterCondition.AFTER: self._handle_after,
      FilterCondition.IS_TRUE: self._handle_is_true,
      FilterCondition.IS_FALSE: self._handle_is_false,
    }

  def _parse_date(self, date_str: str) -> str:
    """Parse date from DD-MM-YYYY format to YYYY-MM-DD"""
    try:
      parsed_date = datetime.strptime(date_str, "%d-%m-%Y")
      return parsed_date.strftime("%Y-%m-%d")
    except ValueError:
      raise ValueError(f"Invalid date format: {date_str}. Expected DD-MM-YYYY")

  def _handle_equals(self, field: str, value: Any, data_type: DataType) -> tuple:
    if data_type == DataType.DATE:
      parsed_date = self._parse_date(str(value))
      return f"{field} = %s", [parsed_date]
    return f"{field} = %s", [value]

  def _handle_greater_than(self, field: str, value: Any, data_type: DataType) -> tuple:
    return f"{field} > %s", [value]

  def _handle_less_than(self, field: str, value: Any, data_type: DataType) -> tuple:
    return f"{field} < %s", [value]

  def _handle_contains(self, field: str, value: Any, data_type: DataType) -> tuple:
    return f"{field} ILIKE %s", [f"%{value}%"]

  def _handle_before(self, field: str, value: Any, data_type: DataType) -> tuple:
    parsed_date = self._parse_date(str(value))
    return f"{field} < %s", [parsed_date]

  def _handle_after(self, field: str, value: Any, data_type: DataType) -> tuple:
    parsed_date = self._parse_date(str(value))
    return f"{field} > %s", [parsed_date]

  def _handle_is_true(self, field: str, value: Any, data_type: DataType) -> tuple:
    return f"{field} = TRUE", []

  def _handle_is_false(self, field: str, value: Any, data_type: DataType) -> tuple:
    return f"{field} = FALSE", []

  def _validate_filter(self, filter_item: FilterItem, column_info: ColumnInfo):
    """Validate filter condition against column data type"""
    data_type = column_info.data_type
    condition = filter_item.condition

    valid_conditions = {
      DataType.INTEGER: [FilterCondition.EQUALS, FilterCondition.GREATER_THAN,
                         FilterCondition.LESS_THAN],
      DataType.STRING: [FilterCondition.CONTAINS, FilterCondition.EQUALS],
      DataType.DATE: [FilterCondition.EQUALS, FilterCondition.BEFORE, FilterCondition.AFTER],
      DataType.TIMESTAMP: [FilterCondition.EQUALS, FilterCondition.BEFORE, FilterCondition.AFTER],
      DataType.BOOLEAN: [FilterCondition.IS_TRUE, FilterCondition.IS_FALSE],
    }

    if condition not in valid_conditions.get(data_type, []):
      raise HTTPException(
        status_code=400,
        detail=f"Invalid condition '{condition}' for data type '{data_type}' on field '{filter_item.field}'"
      )

  def get_column_metadata(self, conn, category_id: int) -> Dict[str, ColumnInfo]:
    """Fetch column metadata from database"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("""
            SELECT db_column_name, type
            FROM seedworks.column_metadata
            WHERE category_id = %s
        """, (category_id,))

    metadata = {}
    for row in cur.fetchall():
      metadata[row['db_column_name']] = ColumnInfo(
        name=row['db_column_name'],
        data_type=DataType(row['type'])
      )
    return metadata

  def build_filter_query(self, filters: List[FilterItem],
                         column_metadata: Dict[str, ColumnInfo]) -> tuple:
    """Build WHERE clause and parameters for SQL query"""
    if not filters:
      return "", []

    where_conditions = []
    parameters = []

    for filter_item in filters:
      if filter_item.field not in column_metadata:
        raise HTTPException(
          status_code=400,
          detail=f"Unknown field: {filter_item.field}"
        )

      column_info = column_metadata[filter_item.field]
      self._validate_filter(filter_item, column_info)

      handler = self.condition_handlers.get(filter_item.condition)
      if not handler:
        raise HTTPException(
          status_code=400,
          detail=f"Unsupported condition: {filter_item.condition}"
        )

      condition_sql, condition_params = handler(
        filter_item.field,
        filter_item.value,
        column_info.data_type
      )

      where_conditions.append(condition_sql)
      parameters.extend(condition_params)

    where_clause = " AND ".join(where_conditions)
    return where_clause, parameters

  def apply_filters(self, conn, schema: str, table: str, category_id: int,
                    filters: List[FilterItem], additional_columns: List[str] = None,
                      category_column: str = "category_id") -> List[Dict]:
    """Apply filters to a table and return filtered results"""
    # Get column metadata
    column_metadata = self.get_column_metadata(conn, category_id)

    if not column_metadata:
      raise HTTPException(status_code=400, detail="No column metadata found for this category")

    where_clause, parameters = self.build_filter_query(filters, column_metadata)

    columns = list(column_metadata.keys())
    if additional_columns:
      columns = list(set(columns + additional_columns))

    base_query = f'SELECT {", ".join(columns)} FROM {schema}.{table} WHERE {category_column} = %s'
    query_params = [category_id]

    if where_clause:
      base_query += f" AND {where_clause}"
      query_params.extend(parameters)

    # Add ORDER BY using table prefix (e.g., grower_records → grower_records_id)
    id_column = f"{table.replace('_records', '')}s_id"
    if id_column in columns:
      base_query += f' ORDER BY "{id_column}"'
    else:
      base_query += " ORDER BY 1"

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(base_query, query_params)

    return [dict(row) for row in cur.fetchall()]
