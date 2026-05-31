from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from services.xlsx.xlsx_service import process_xlsx
from utils.file_validator import validate_xlsx
from api.v1.deps import require_admin

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/xlsx")
async def upload_xlsx(
    file: UploadFile = File(...),
    user: dict = Depends(require_admin)
):
    validate_xlsx(file)

    file.file.seek(0)

    try:
        result = process_xlsx(file.file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result
