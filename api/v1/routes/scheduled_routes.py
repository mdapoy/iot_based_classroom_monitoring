from fastapi import APIRouter, Depends, Query
from repositories.cache import get_all_jadwal
from api.v1.deps import optional_authenticated
from core.logger import logger
from typing import Optional

router = APIRouter(prefix="/scheduled", tags=["scheduled"])


@router.get("/jadwal")
def get_jadwal(
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
    user: dict = Depends(optional_authenticated),
):
    jadwal = get_all_jadwal()
    if tahun_ajaran_id:
        jadwal = [j for j in jadwal if j.get("tahun_ajaran_id") == tahun_ajaran_id]
        logger.info(f"[SCHEDULED] get_jadwal filtered tahun_ajaran_id={tahun_ajaran_id} → {len(jadwal)} rows")
    return jadwal