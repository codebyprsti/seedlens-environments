from typing import List, Dict, Any
from fastapi import HTTPException
import psycopg2.extras

from services.filter_service import DynamicFilterService
from models.filter_models import DataType


class CategoryService:
    """Generic service for handling category-based filtering"""

    def __init__(self):
        self.filter_service = DynamicFilterService()

    def get_category_info(self, conn, category_id: int) -> Dict[str, Any]:
        """Resolve table name from category"""
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT category_name FROM seedworks.category WHERE category_id = %s", (category_id,))
        cat_row = cur.fetchone()

        if not cat_row:
            raise HTTPException(status_code=404, detail="Invalid category_id")

        table_name = f"{cat_row['category_name'].lower().replace(' ', '_').rstrip('s')}_records"

        return {
            "category_name": cat_row["category_name"],
            "table_name": table_name
        }

    def get_filter_metadata(self, conn, category_id: int) -> List[Dict]:
        """Return fields and supported filters for a category"""
        column_metadata = self.filter_service.get_column_metadata(conn, category_id)

        supported_conditions = {
            DataType.INTEGER: ["equals", "greater_than", "less_than"],
            DataType.STRING: ["contains", "equals"],
            DataType.DATE: ["equals", "before", "after"],
            DataType.TIMESTAMP: ["equals", "before", "after"],
            DataType.BOOLEAN: ["is_true", "is_false"],
        }

        return [
            {
                "field": col.name,
                "data_type": col.data_type,
                "supported_conditions": supported_conditions.get(col.data_type, [])
            }
            for col in column_metadata.values()
        ]
