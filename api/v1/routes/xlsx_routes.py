from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from services.xlsx.xlsx_service import process_xlsx
from utils.file_validator import validate_xlsx
from api.v1.deps import optional_authenticated
from typing import Optional

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/xlsx")
async def upload_xlsx(
    file: UploadFile = File(...),
    tahun_ajaran_id: Optional[str] = Form(None),
    user: dict = Depends(optional_authenticated),
):
    validate_xlsx(file)

    file.file.seek(0)

    try:
        result = process_xlsx(file.file, tahun_ajaran_id=tahun_ajaran_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result
