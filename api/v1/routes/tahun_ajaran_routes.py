from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from repositories.supabase_client import supabase
from repositories.cache import get_all_jadwal
from api.v1.deps import require_authenticated, require_admin
from core.logger import logger
from datetime import date
from typing import Optional, List

router = APIRouter(prefix="/tahun-ajaran", tags=["Tahun Ajaran"])


# =========================================================
# SCHEMA
# =========================================================
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


def _derive_active_tahun_semester(today: Optional[date] = None) -> tuple:
    """
    Tentukan tahun ajaran & semester AKTIF otomatis dari tanggal berjalan.
      • Genap  : Februari–Juli   (bulan 2–7)
      • Ganjil : Agustus–Januari (bulan 8–12 dan 1)

    Tahun ditulis 'YYYY/YYYY' (tahun mulai / berikutnya):
      • Genap          → mulai = tahun - 1   (Jun 2026 → '2025/2026' genap)
      • Ganjil Agu–Des → mulai = tahun       (Sep 2025 → '2025/2026' ganjil)
      • Ganjil Jan     → mulai = tahun - 1   (Jan 2026 → '2025/2026' ganjil)
    """
    today = today or date.today()
    y, m = today.year, today.month
    if 2 <= m <= 7:
        semester, start = "genap", y - 1
    elif m >= 8:
        semester, start = "ganjil", y
    else:  # m == 1
        semester, start = "ganjil", y - 1
    return f"{start}/{start + 1}", semester


def _mark_active(rows: list) -> list:
    """Set is_aktif tiap baris berdasarkan derivasi tanggal (bukan flag manual DB)."""
    tahun, semester = _derive_active_tahun_semester()
    for r in rows:
        r["is_aktif"] = (r.get("tahun") == tahun and r.get("semester") == semester)
    return rows


def _enrich_with_counts(rows: list) -> list:
    """
    Tambah jumlah jadwal & RPS per TA (untuk halaman manage TA).
    jadwal_count: dari cache get_all_jadwal() — 0 query DB.
    rps_count:    dari DB langsung — RPS tidak di-cache.
    """
    if not rows:
        return rows

    ids = [r["id"] for r in rows]

    all_jadwal = get_all_jadwal()
    jadwal_count: dict = {}
    for j in all_jadwal:
        ta_id = j.get("tahun_ajaran_id")
        if ta_id and ta_id in ids:
            jadwal_count[ta_id] = jadwal_count.get(ta_id, 0) + 1

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
# 1. LIST semua TA — is_aktif diturunkan dari tanggal, aktif paling atas
# =========================================================
@router.get("")
def list_tahun_ajaran(
    with_counts: Optional[str] = Query(None, description="Set 'true' untuk sertakan jumlah jadwal & RPS"),
    user: dict = Depends(require_authenticated),
):
    """Return list TA. is_aktif otomatis dari bulan berjalan. Pakai ?with_counts=true untuk jumlah jadwal & RPS."""
    try:
        res = (
            supabase.table("tahun_ajaran")
            .select("id, tahun, semester, is_aktif, created_at")
            .order("tahun", desc=True)
            .order("semester")
            .execute()
        )
        rows = res.data or []

        for r in rows:
            r["label"] = _format_label(r)
        _mark_active(rows)

        include_counts = (with_counts or "").strip().lower() in ("true", "1", "yes", "y")
        if include_counts:
            rows = _enrich_with_counts(rows)

        rows.sort(key=lambda r: (not r.get("is_aktif", False),))

        return {"status": "success", "data": rows}

    except Exception as e:
        logger.error(f"[TA] list error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 2. GET TA aktif — OTOMATIS dari bulan berjalan + auto-buat bila belum ada
# =========================================================
@router.get("/aktif")
def get_tahun_ajaran_aktif(user: dict = Depends(require_authenticated)):
    """TA aktif ditentukan OTOMATIS dari tanggal sekarang (Genap: Feb–Jul · Ganjil: Agu–Jan),
    bukan dari flag manual. Baris periode berjalan dibuat otomatis bila belum ada."""
    try:
        tahun, semester = _derive_active_tahun_semester()

        def _fetch_active():
            return (
                supabase.table("tahun_ajaran")
                .select("id, tahun, semester, is_aktif, created_at")
                .eq("tahun", tahun)
                .eq("semester", semester)
                .limit(1)
                .execute()
                .data
                or []
            )

        rows = _fetch_active()

        # Auto-buat TA periode berjalan kalau belum ada (2 semester sekaligus)
        if not rows:
            existing_sem = {
                r["semester"]
                for r in (
                    supabase.table("tahun_ajaran")
                    .select("semester")
                    .eq("tahun", tahun)
                    .execute()
                    .data
                    or []
                )
            }
            to_insert = [
                {"tahun": tahun, "semester": s}
                for s in ("ganjil", "genap")
                if s not in existing_sem
            ]
            if to_insert:
                supabase.table("tahun_ajaran").insert(to_insert).execute()
                logger.info(f"[TA] auto-created tahun={tahun} semester={[x['semester'] for x in to_insert]}")
            rows = _fetch_active()

        if not rows:
            return {"status": "success", "data": None}

        row = rows[0]

        # Sinkronkan flag is_aktif di DB agar endpoint lain yang masih membaca
        # flag tetap konsisten (mis. sisa kode lama).
        if not row.get("is_aktif"):
            supabase.table("tahun_ajaran").update({"is_aktif": False}).eq("is_aktif", True).execute()
            supabase.table("tahun_ajaran").update({"is_aktif": True}).eq("id", row["id"]).execute()

        row["is_aktif"] = True
        row["label"] = _format_label(row)
        return {"status": "success", "data": row}

    except Exception as e:
        logger.error(f"[TA] get aktif error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# 3. DELETE — tolak kalau TA periode berjalan / masih dipakai data
# =========================================================
@router.delete("/{ta_id}")
def delete_tahun_ajaran(
    ta_id: str,
    force: Optional[str] = Query(None, description="Set 'true' untuk paksa hapus"),
    user: dict = Depends(require_admin),
):
    """Hapus TA. Tolak kalau TA adalah periode berjalan atau masih ada jadwal/RPS terkait.
    Pakai ?force=true untuk paksa hapus (tahun_ajaran_id di tabel terkait jadi NULL)."""
    force_flag = (force or "").strip().lower() in ("true", "1", "yes", "y")
    try:
        ta_res = (
            supabase.table("tahun_ajaran")
            .select("id, tahun, semester")
            .eq("id", ta_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not ta_res:
            raise HTTPException(status_code=404, detail="Tahun ajaran tidak ditemukan")

        # Guard: tidak boleh hapus TA periode berjalan (aktif otomatis)
        akt_tahun, akt_sem = _derive_active_tahun_semester()
        if ta_res[0].get("tahun") == akt_tahun and ta_res[0].get("semester") == akt_sem:
            raise HTTPException(
                status_code=409,
                detail="Tidak bisa menghapus tahun ajaran yang sedang berjalan (aktif otomatis).",
            )

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
# 4. ASSIGN DATA LAMA — tag jadwal atau RPS ke TA tertentu
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
# 5. UNASSIGNED — list data yang belum punya TA
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
