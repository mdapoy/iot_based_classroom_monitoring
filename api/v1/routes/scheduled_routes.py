from fastapi import APIRouter, Depends, HTTPException, Query
from repositories.cache import get_all_jadwal
from repositories.supabase_client import supabase
from api.v1.deps import require_authenticated
from core.logger import logger
from typing import Optional

router = APIRouter(prefix="/scheduled", tags=["scheduled"])


@router.get("/jadwal")
def get_jadwal(
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
    user: dict = Depends(require_authenticated),
):
    jadwal = get_all_jadwal()
    if tahun_ajaran_id:
        jadwal = [j for j in jadwal if j.get("tahun_ajaran_id") == tahun_ajaran_id]
        logger.info(f"[SCHEDULED] get_jadwal filtered tahun_ajaran_id={tahun_ajaran_id} → {len(jadwal)} rows")
    return jadwal


@router.delete("/jadwal/{jadwal_id}")
def delete_jadwal(jadwal_id: int, user: dict = Depends(require_authenticated)):
    """Hapus satu jadwal kuliah berdasarkan id."""
    try:
        existing = (
            supabase.table("jadwal_kuliah")
            .select("id")
            .eq("id", jadwal_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Jadwal tidak ditemukan")

        supabase.table("jadwal_kuliah").delete().eq("id", jadwal_id).execute()
        logger.info(f"[SCHEDULED] deleted jadwal id={jadwal_id}")
        return {"status": "success", "message": "Jadwal berhasil dihapus"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SCHEDULED] delete jadwal error: {e}", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail=(
                "Jadwal tidak bisa dihapus karena masih dipakai data lain "
                "(monitoring/rekaman/aktivitas). Hapus data terkait dahulu."
            ),
        )