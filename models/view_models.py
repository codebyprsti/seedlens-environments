from pydantic import BaseModel
from typing import Dict, Any, Optional


class ViewSaveRequest(BaseModel):
    user_id: str
    category_id: int
    view_name: str
    view_payload: Dict[str, Any]  # Complete view JSON

class ViewResponse(BaseModel):
    id: int
    user_id: str
    category_id: int
    view_name: str
    view_payload: Dict[str, Any]
