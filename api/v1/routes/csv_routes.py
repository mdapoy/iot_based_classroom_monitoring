from fastapi import APIRouter, UploadFile, File, Depends
from services.csv.csv_service import process_csv
from utils.file_validator import validate_csv
from api.v1.deps import require_admin

router = APIRouter(prefix="/upload", tags=["upload"])

# yang baru dengan penambahan file.file.seek(0) untuk memastikan pointer berada di awal file sebelum diproses
@router.post("/csv")
async def upload_csv(
    file: UploadFile = File(...),
    user: dict = Depends(require_admin)
):
    validate_csv(file)

    file.file.seek(0)

    result = process_csv(file.file)

    return result
