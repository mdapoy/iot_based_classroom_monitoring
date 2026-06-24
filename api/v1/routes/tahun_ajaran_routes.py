from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from repositories.supabase_client import supabase
from repositories.cache import get_all_jadwal
from api.v1.deps import require_authenticated, require_admin
from core.logger import logger
from typing import Optional, List
import re

router = APIRouter(prefix="/tahun-ajaran", tags=["Tahun Ajaran"])


# =========================================================
# SCHEMA
# =========================================================
TAHUN_REGEX = re.compile(r"^\d{4}/\d{4}$")


class TahunAjaranCreate(BaseModel):
    """Saat admin tambah TA baru → otomatis bikin 2 semester (ganjil+genap)."""
    tahun: str = Field(..., description="Format: '2025/2026'")

    @field_validator("tahun")
    @classmethod
    def validate_tahun(cls, v: str) -> str:
        v = v.strip()
        if not TAHUN_REGEX.match(v):
            raise ValueError("Format tahun harus 'YYYY/YYYY' (contoh: '2025/2026')")
        y1, y2 = map(int, v.split("/"))
        if y2 != y1 + 1:
            raise ValueError("Tahun kedua harus +1 dari yang pertama")
        return v


class TahunAjaranUpdate(BaseModel):
    """Update fields opsional."""
    is_aktif: Optional[bool] = None


class AssignTahunAjaran(BaseModel):
    """Untuk assign data lama (jadwal/RPS) ke TA tertentu."""
    tahun_ajaran_id: str = Field(..., description="UUID dari tahun_ajaran")
    ids: List[str] = Field(..., description="List ID jadwal/RPS yang mau di-assign")


# =========================================================
# HELPERS
# =========================================================
def _format_label(row: dict) -> str:
    """Return label seperti '2025/2026 Ganjil'."""
    return f"{row['tahun']} {row['semester'].capitalize()}"


def _enrich_with_counts(rows: list) -> list:
    """
    Tambah jumlah jadwal & RPS per TA (untuk halaman manage TA).
    jadwal_count: dari cache get_all_jadwal() — 0 query DB.
    rps_count:    dari DB langsung — RPS tidak di-cache.
    """
    if not rows:
        return rows

    ids = [r["id"] for r in rows]

    # ── Hitung jadwal per TA (dari cache, 0 query DB) ─────────────────────────
    all_jadwal = get_all_jadwal()
    jadwal_count: dict = {}
    for j in all_jadwal:
        ta_id = j.get("tahun_ajaran_id")
        if ta_id and ta_id in ids:
            jadwal_count[ta_id] = jadwal_count.get(ta_id, 0) + 1

    # ── Hitung RPS per TA (1 query DB, RPS tidak di-cache) ────────────────────
    rps_rows = (
        supabase.table("rps_pertemuan")
        .select("tahun_ajaran_id")
        .in_("tahun_ajaran_id", ids)
        .execute()
        .data
        or []
    )
    rps_count: dict = {}
    for r in rps_rows:
        ta_id = r.get("tahun_ajaran_id")
        if ta_id:
            rps_count[ta_id] = rps_count.get(ta_id, 0) + 1

    for r in rows:
        r["label"]        = _format_label(r)
        r["jadwal_count"] = jadwal_count.get(r["id"], 0)
        r["rps_count"]    = rps_count.get(r["id"], 0)

    return rows


# =========================================================
# 1. LIST semua TA — sort: aktif dulu, lalu tahun terbaru
# =========================================================
@router.get("")
def list_tahun_ajaran(
    with_counts: Optional[str] = Query(None, description="Set 'true' untuk sertakan jumlah jadwal & RPS"),
    user: dict = Depends(require_authenticated),
):
    """Return list TA. Pakai ?with_counts=true untuk include jumlah jadwal & RPS."""
    try:
        res = (
            supabase.table("tahun_ajaran")
            .select("id, tahun, semester, is_aktif, created_at")
            .order("tahun", desc=True)
            .order("semester")
            .execute()
        )
        rows = res.data or []

        # Tambah label string
        for r in rows:
            r["label"] = _format_label(r)

        # Parse query param manual (lebih toleran daripada bool FastAPI)
        include_counts = (with_counts or "").strip().lower() in ("true", "1", "yes", "y")
        if include_counts:
            rows = _enrich_with_counts(rows)

        # Sort: aktif paling atas
        rows.sort(key=lambda r: (not r.get("is_aktif", False),))

        return {"status": "success", "data": rows}

    except Exception as e:
        logger.error(f"[TA] list error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 2. GET TA yang sedang aktif
# =========================================================
@router.get("/aktif")
def get_tahun_ajaran_aktif(user: dict = Depends(require_authenticated)):
    try:
        res = (
            supabase.table("tahun_ajaran")
            .select("id, tahun, semester, is_aktif, created_at")
            .eq("is_aktif", True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return {"status": "success", "data": None}

        row = rows[0]
        row["label"] = _format_label(row)
        return {"status": "success", "data": row}

    except Exception as e:
        logger.error(f"[TA] get aktif error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 3. CREATE — otomatis bikin Ganjil + Genap
# =========================================================
@router.post("")
def create_tahun_ajaran(data: TahunAjaranCreate, user: dict = Depends(require_admin)):
    """Saat tambah TA baru → otomatis bikin 2 baris (Ganjil + Genap)."""
    try:
        # Cek apakah sudah ada
        existing = (
            supabase.table("tahun_ajaran")
            .select("id, semester")
            .eq("tahun", data.tahun)
            .execute()
            .data
            or []
        )
        existing_sem = {r["semester"] for r in existing}

        to_insert = []
        if "ganjil" not in existing_sem:
            to_insert.append({"tahun": data.tahun, "semester": "ganjil"})
        if "genap" not in existing_sem:
            to_insert.append({"tahun": data.tahun, "semester": "genap"})

        if not to_insert:
            raise HTTPException(
                status_code=409,
                detail=f"Tahun ajaran {data.tahun} sudah memiliki semester Ganjil dan Genap",
            )

        res = supabase.table("tahun_ajaran").insert(to_insert).execute()
        created = res.data or []
        for r in created:
            r["label"] = _format_label(r)

        logger.info(f"[TA] created tahun={data.tahun} semester={[r['semester'] for r in created]}")

        return {
            "status": "success",
            "message": f"Tahun ajaran {data.tahun} berhasil dibuat",
            "data": created,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TA] create error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 4. UPDATE — terutama untuk set aktif
# =========================================================
@router.patch("/{ta_id}")
def update_tahun_ajaran(ta_id: str, data: TahunAjaranUpdate, user: dict = Depends(require_admin)):
    try:
        # Validasi exists
        existing = (
            supabase.table("tahun_ajaran")
            .select("id")
            .eq("id", ta_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Tahun ajaran tidak ditemukan")

        # Jika set aktif → matikan dulu semua yang aktif
        if data.is_aktif is True:
            supabase.table("tahun_ajaran").update({"is_aktif": False}).eq(
                "is_aktif", True
            ).execute()

        # Update target
        update_payload = data.model_dump(exclude_none=True)
        if not update_payload:
            raise HTTPException(status_code=400, detail="Tidak ada field untuk diupdate")

        res = (
            supabase.table("tahun_ajaran")
            .update(update_payload)
            .eq("id", ta_id)
            .execute()
        )

        updated = res.data[0] if res.data else None
        if updated:
            updated["label"] = _format_label(updated)

        logger.info(f"[TA] updated id={ta_id} payload={update_payload}")

        return {
            "status": "success",
            "message": "Tahun ajaran berhasil diperbarui",
            "data": updated,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TA] update error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 5. DELETE — cek dulu apakah masih ada data terkait
# =========================================================
@router.delete("/{ta_id}")
def delete_tahun_ajaran(
    ta_id: str,
    force: Optional[str] = Query(None, description="Set 'true' untuk paksa hapus"),
    user: dict = Depends(require_admin),
):
    """Hapus TA. Default: tolak kalau masih ada jadwal/RPS terkait.
    Pakai ?force=true untuk paksa hapus (tahun_ajaran_id di tabel terkait jadi NULL)."""
    force_flag = (force or "").strip().lower() in ("true", "1", "yes", "y")
    try:
        # Cek exists + guard TA aktif
        ta_res = (
            supabase.table("tahun_ajaran")
            .select("id, is_aktif")
            .eq("id", ta_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not ta_res:
            raise HTTPException(status_code=404, detail="Tahun ajaran tidak ditemukan")
        if ta_res[0].get("is_aktif"):
            raise HTTPException(
                status_code=409,
                detail="Tidak bisa menghapus tahun ajaran yang sedang aktif. Aktifkan TA lain terlebih dahulu.",
            )

        # Cek dependent data
        jadwal_count = (
            supabase.table("jadwal_kuliah")
            .select("id", count="exact")
            .eq("tahun_ajaran_id", ta_id)
            .execute()
            .count
            or 0
        )
        rps_count = (
            supabase.table("rps_pertemuan")
            .select("id", count="exact")
            .eq("tahun_ajaran_id", ta_id)
            .execute()
            .count
            or 0
        )

        if (jadwal_count > 0 or rps_count > 0) and not force_flag:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"TA masih digunakan oleh {jadwal_count} jadwal dan {rps_count} RPS. "
                    "Pindahkan data terlebih dahulu atau gunakan ?force=true"
                ),
            )

        supabase.table("tahun_ajaran").delete().eq("id", ta_id).execute()

        logger.info(f"[TA] deleted id={ta_id} force={force_flag}")

        return {
            "status": "success",
            "message": "Tahun ajaran berhasil dihapus",
            "freed": {"jadwal": jadwal_count, "rps": rps_count},
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TA] delete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 6. ASSIGN DATA LAMA — tag jadwal atau RPS ke TA tertentu
# =========================================================
@router.post("/assign-jadwal")
def assign_jadwal_to_ta(data: AssignTahunAjaran, user: dict = Depends(require_admin)):
    """Tag list jadwal_kuliah ke tahun_ajaran tertentu."""
    return _assign_to_ta("jadwal_kuliah", data.tahun_ajaran_id, data.ids)


@router.post("/assign-rps")
def assign_rps_to_ta(data: AssignTahunAjaran, user: dict = Depends(require_admin)):
    """Tag list rps_pertemuan ke tahun_ajaran tertentu."""
    return _assign_to_ta("rps_pertemuan", data.tahun_ajaran_id, data.ids)


def _assign_to_ta(table: str, ta_id: str, ids: List[str]):
    """Helper: update tahun_ajaran_id pada banyak baris sekaligus."""
    try:
        # Validasi TA exists
        ta_exists = (
            supabase.table("tahun_ajaran")
            .select("id")
            .eq("id", ta_id)
            .limit(1)
            .execute()
            .data
        )
        if not ta_exists:
            raise HTTPException(status_code=404, detail="Tahun ajaran tidak ditemukan")

        if not ids:
            raise HTTPException(status_code=400, detail="Tidak ada ID yang dipilih")

        res = (
            supabase.table(table)
            .update({"tahun_ajaran_id": ta_id})
            .in_("id", ids)
            .execute()
        )

        affected = len(res.data) if res.data else 0

        logger.info(f"[TA] assigned {affected} {table} → ta_id={ta_id}")

        # Kalau jadwal di-assign, invalidate cache supaya data fresh
        if table == "jadwal_kuliah":
            from repositories.cache import invalidate_jadwal_cache
            invalidate_jadwal_cache()

        return {
            "status": "success",
            "message": f"{affected} data berhasil di-assign ke tahun ajaran",
            "affected": affected,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TA] assign {table} error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 7. UNASSIGNED — list data yang belum punya TA
# =========================================================
@router.get("/unassigned/jadwal")
def list_jadwal_unassigned(user: dict = Depends(require_authenticated)):
    """Daftar jadwal yang belum di-tag ke TA manapun (tahun_ajaran_id IS NULL)."""
    try:
        res = (
            supabase.table("jadwal_kuliah")
            .select("id, kode_mata_kuliah, mata_kuliah, kelas, dosen_utama, hari, jam_mulai, ruangan")
            .is_("tahun_ajaran_id", "null")
            .order("kode_mata_kuliah")
            .execute()
        )
        return {"status": "success", "data": res.data or []}

    except Exception as e:
        logger.error(f"[TA] unassigned jadwal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unassigned/rps")
def list_rps_unassigned(user: dict = Depends(require_authenticated)):
    """Daftar RPS yang belum di-tag ke TA manapun."""
    try:
        res = (
            supabase.table("rps_pertemuan")
            .select("id, kode_matkul, pertemuan_ke, materi_pembelajaran")
            .is_("tahun_ajaran_id", "null")
            .order("kode_matkul")
            .order("pertemuan_ke")
            .execute()
        )
        return {"status": "success", "data": res.data or []}

    except Exception as e:
        logger.error(f"[TA] unassigned rps error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
