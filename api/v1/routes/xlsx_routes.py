import time
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from services.xlsx.xlsx_service import process_xlsx
from utils.file_validator import validate_xlsx
from api.v1.deps import optional_authenticated
from core.logger import logger
from typing import Optional

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/xlsx")
async def upload_xlsx(
    file: UploadFile = File(...),
    tahun_ajaran_id: Optional[str] = Form(None),
    user: dict = Depends(optional_authenticated),
):
    t_start = time.time()

    # Ambil ukuran file dari Content-Length atau baca langsung
    size_bytes = 0
    file.file.seek(0, 2)
    size_bytes = file.file.tell()
    file.file.seek(0)
    size_kb = size_bytes / 1024

    logger.info(f"[XLSX] ========== START ==========")
    logger.info(f"[XLSX] File: {file.filename} | Size: {size_kb:.1f} KB | TA: {tahun_ajaran_id}")

    validate_xlsx(file)
    file.file.seek(0)

    try:
        result = process_xlsx(file.file, tahun_ajaran_id=tahun_ajaran_id)
    except ValueError as e:
        elapsed = time.time() - t_start
        logger.error(f"[XLSX] ========== FAILED ========== | error={str(e)} | waktu={elapsed:.2f}s")
        raise HTTPException(status_code=400, detail=str(e))

    elapsed = time.time() - t_start
    logger.info(
        f"[XLSX] ========== DONE ========== | "
        f"inserted={result.get('rows_inserted')} | "
        f"skipped={result.get('rows_skipped')} | "
        f"total_waktu={elapsed:.2f}s"
    )

    return result
