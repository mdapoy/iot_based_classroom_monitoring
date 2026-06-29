from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from repositories.cache import get_all_jadwal, invalidate_jadwal_cache
from repositories.supabase_client import supabase
from api.v1.deps import require_authenticated
from core.logger import logger
from typing import Optional
import re

router = APIRouter(prefix="/scheduled", tags=["scheduled"])

# Hari valid (uppercase, sesuai data jadwal_kuliah & grid frontend)
HARI_VALID = {"SENIN", "SELASA", "RABU", "KAMIS", "JUMAT", "SABTU"}

# Format jam HH:MM atau HH:MM:SS
_JAM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")

class JadwalManualCreate(BaseModel):
    hari: str
    kode_mata_kuliah: str
    mata_kuliah: str
    kelas: str
    dosen_utama: str
    ruangan: str
    jam_mulai: str
    jam_selesai: str
    tahun_ajaran_id: Optional[str] = None

    @field_validator("hari")
    @classmethod
    def _v_hari(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if v not in HARI_VALID:
            raise ValueError(f"hari harus salah satu dari: {', '.join(sorted(HARI_VALID))}")
        return v

    @field_validator(
        "kode_mata_kuliah", "mata_kuliah", "kelas", "dosen_utama", "ruangan"
    )
    @classmethod
    def _v_not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Field wajib diisi")
        return v

    @field_validator("jam_mulai", "jam_selesai")
    @classmethod
    def _v_jam(cls, v: str) -> str:
        v = (v or "").strip()
        if not _JAM_RE.match(v):
            raise ValueError("Format jam harus HH:MM (contoh: 13:30)")
        return v[:5]  # normalisasi ke HH:MM

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

@router.post("/jadwal")
def create_jadwal(data: JadwalManualCreate, user: dict = Depends(require_authenticated)):
    """Tambah satu jadwal kuliah secara manual (tanpa upload Excel)."""
    if data.jam_selesai <= data.jam_mulai:
        raise HTTPException(
            status_code=400,
            detail="Jam selesai harus lebih besar dari jam mulai",
        )

    payload = {
        "hari":             data.hari,
        "kode_mata_kuliah": data.kode_mata_kuliah,
        "mata_kuliah":      data.mata_kuliah,
        "kelas":            data.kelas,
        "dosen_utama":      data.dosen_utama,
        "ruangan":          data.ruangan,
        "jam_mulai":        data.jam_mulai,
        "jam_selesai":      data.jam_selesai,
    }
    if data.tahun_ajaran_id:
        payload["tahun_ajaran_id"] = data.tahun_ajaran_id

    # ── Cek duplikat (kunci sama dengan upload Excel) ──────────────────────────
    dup = (
        supabase.table("jadwal_kuliah")
        .select("id")
        .eq("hari", payload["hari"])
        .eq("mata_kuliah", payload["mata_kuliah"])
        .eq("ruangan", payload["ruangan"])
        .eq("kelas", payload["kelas"])
        .eq("jam_mulai", payload["jam_mulai"])
        .eq("jam_selesai", payload["jam_selesai"])
        .limit(1)
        .execute()
        .data
        or []
    )
    if dup:
        raise HTTPException(
            status_code=409,
            detail="Jadwal dengan kombinasi hari, mata kuliah, ruangan, kelas, dan jam yang sama sudah ada.",
        )

    try:
        res = supabase.table("jadwal_kuliah").insert(payload).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Gagal menyimpan jadwal")

        # Refresh cache jadwal agar jadwal baru langsung muncul (tanpa nunggu TTL)
        invalidate_jadwal_cache()

        saved = res.data[0]
        logger.info(
            f"[SCHEDULED] created jadwal id={saved.get('id')} "
            f"matkul={payload['kode_mata_kuliah']} kelas={payload['kelas']}"
        )
        return {
            "status": "success",
            "message": "Jadwal berhasil ditambahkan",
            "data": saved,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SCHEDULED] create jadwal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan jadwal: {e}")

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
        invalidate_jadwal_cache()
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
