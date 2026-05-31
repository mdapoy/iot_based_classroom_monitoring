from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from services.csv.csv_service import process_csv
from utils.file_validator import validate_csv
from api.v1.deps import require_admin

router = APIRouter(prefix="/upload", tags=["upload"])

@router.post("/csv")
async def upload_csv(
    file: UploadFile = File(...),
    user: dict = Depends(require_admin)
):
    validate_csv(file)

    file.file.seek(0)

    try:
        result = process_csv(file.file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result
