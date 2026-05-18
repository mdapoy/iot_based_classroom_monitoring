from fastapi import APIRouter, Depends, HTTPException
from repositories.supabase_client import supabase
from models.rps_schema import RPSRequest
from api.v1.deps import optional_authenticated
from core.logger import logger

router = APIRouter(prefix="/rps", tags=["rps"])


@router.post("")
def create_or_update_rps(data: RPSRequest, user: dict = Depends(optional_authenticated)):
    """
    Simpan atau perbarui data RPS pertemuan ke tabel rps_pertemuan.
    Jika kombinasi kode_matkul + pertemuan_ke sudah ada, data akan diperbarui (upsert).
    """
    payload = {
        "kode_matkul": data.kodeMatkul.strip(),
        "pertemuan_ke": data.pertemuan,
        "materi_pembelajaran": data.materi.strip(),
        "pengalaman_pembelajaran_mahasiswa": data.pengalaman.strip(),
    }

    try:
        res = (
            supabase.table("rps_pertemuan")
            .upsert(payload, on_conflict="kode_matkul,pertemuan_ke")
            .execute()
        )

        if not res.data:
            raise HTTPException(status_code=500, detail="Gagal menyimpan data RPS")

        saved = res.data[0]
        logger.info(
            f"[RPS] Saved | kode_matkul={saved['kode_matkul']} "
            f"pertemuan_ke={saved['pertemuan_ke']}"
        )

        return {
            "status": "success",
            "message": f"RPS pertemuan ke-{data.pertemuan} untuk {data.kodeMatkul} berhasil disimpan.",
            "data": saved,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS] Error saving: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def get_rps_list(
    kode_matkul: str = None,
    user: dict = Depends(optional_authenticated),
):
    """
    Ambil daftar RPS. Bisa difilter berdasarkan kode_matkul.
    """
    try:
        query = (
            supabase.table("rps_pertemuan")
            .select("*")
            .order("kode_matkul")
            .order("pertemuan_ke")
        )

        if kode_matkul:
            query = query.eq("kode_matkul", kode_matkul.strip())

        res = query.execute()
        return {
            "status": "success",
            "data": res.data or [],
        }

    except Exception as e:
        logger.error(f"[RPS] Error fetching list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
