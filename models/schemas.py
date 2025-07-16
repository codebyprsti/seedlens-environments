from pydantic import BaseModel, constr

class UploadSchema(BaseModel):
    category_name: constr(strip_whitespace=True, min_length=1)
    category_desc: str
    column_types: str
