"""
Utility functions for data processing and formatting
"""
import json
from decimal import Decimal


class Utils:
    """Utility class for data transformation"""
    
    @staticmethod
    def decimal_to_float(obj):
        """Convert Decimal objects to float for JSON serialization."""
        if isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, list):
            return [Utils.decimal_to_float(item) for item in obj]
        elif isinstance(obj, dict):
            return {key: Utils.decimal_to_float(value) for key, value in obj.items()}
        return obj

    @staticmethod
    def build_json(columns, rows):
        """
        Build JSON from SQL query results with dimensions and measures classification.
        
        Args:
            columns: List of column descriptors from cursor.description
            rows: List of row tuples from query results
            
        Returns:
            Dict with dimensions, measures, and data
        """
        dimensions = []
        measures = []
        
        for column in columns:
            column_name = column[0]
            type_code = column[1] if len(column) > 1 else None
            
            # PostgreSQL type codes:
            # TEXT/VARCHAR/DATE types: 18, 25, 1043, 1042, 1082, 1083, 1114, 1184
            # NUMERIC types: 20, 21, 23, 700, 701, 1700
            if type_code in [18, 25, 1043, 1042, 1082, 1083, 1114, 1184]:
                dimensions.append(column_name)
            elif type_code in [20, 21, 23, 700, 701, 1700]:
                measures.append(column_name)
            else:
                # Default: treat as dimension if unknown
                dimensions.append(column_name)

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

