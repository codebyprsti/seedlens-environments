import json
from decimal import Decimal


class Utils:
  @staticmethod
  def decimal_to_float(obj):
    """Convert Decimal objects to float."""
    if isinstance(obj, Decimal):
      return float(obj)
    elif isinstance(obj, list):
      return [Utils.decimal_to_float(item) for item in obj]
    elif isinstance(obj, dict):
      return {key: Utils.decimal_to_float(value) for key, value in obj.items()}
    return obj

  @staticmethod
  def build_json(columns, rows):
    """Build JSON from SQL query results."""
    dimensions = []
    measures = []
    for column in columns:
      column_name = column[0]
      type_code = column[1]
      if type_code in [18, 25, 1043, 1042, 1082, 1083, 1114, 1184]:  # TEXT, VARCHAR, DATE, TIMESTAMP
        dimensions.append(column_name)
      elif type_code in [20, 21, 23, 700, 701, 1700]:  # INTEGER, FLOAT4 (REAL), NUMERIC
        measures.append(column_name)

    json_data = {"dimensions": dimensions, "measures": measures, "data": []}
    column_names = [desc[0] for desc in columns]
    for row in rows:
      data_row = {}
      for i, value in enumerate(row):
        if isinstance(value, str):
          value = value.strip()
        data_row[column_names[i]] = value
      json_data["data"].append(data_row)

    json_data = Utils.decimal_to_float(json_data)
    return json_data
