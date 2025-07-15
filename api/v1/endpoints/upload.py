from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from core.db import SessionLocal
from services import record_service, inspection_service
import pandas as pd

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/upload/inspection-excel/")
def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Invalid file type")

    df = pd.read_excel(file.file, header=[0, 1])
    df.columns = [' '.join(col).strip() for col in df.columns.values]

    record_service.load_and_insert_foundations(file.file, db)
    inspection_service.insert_inspection_data(df, db)

    return {"message": "Upload successful", "rows": len(df)}