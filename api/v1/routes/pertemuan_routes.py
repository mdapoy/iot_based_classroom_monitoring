"""
Pengaturan Pertemuan — BE untuk halaman konfigurasi minggu kuliah.

Endpoints:
  GET    /pertemuan/config               → status minggu berjalan + config aktif
  POST   /pertemuan/skip-dates           → tambah tanggal skip
  DELETE /pertemuan/skip-dates/{tanggal} → hapus tanggal skip
  POST   /pertemuan/upload-kalender      → upload PDF kalender → auto-extract config
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from api.v1.deps import optional_authenticated
from repositories.supabase_client import supabase
from services.rps.rps_service import get_active_config, invalidate_config_cache
from services.akademik.kalender_parser import parse_kalender_pdf
from core.logger import logger

router        = APIRouter(prefix="/pertemuan",        tags=["Pertemuan"])
router_legacy = APIRouter(prefix="/kalender-akademik", tags=["Pertemuan"])

_WIB = timezone(timedelta(hours=7))

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB


# ── Schema ────────────────────────────────────────────────────────────────────

class SkipDateBody(BaseModel):
    tanggal: str  # ISO "YYYY-MM-DD"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _get_active_ta() -> dict:
    """Return row tahun_ajaran yang is_aktif=True, atau raise 404."""
    rows = (
        supabase.table("tahun_ajaran")
        .select("id, tahun, semester, is_aktif")
        .eq("is_aktif", True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Tidak ada tahun ajaran yang aktif")
    return rows[0]


def _upsert_config(ta_id: str, payload: dict) -> dict:
    """
    Upsert semester_config untuk tahun_ajaran tertentu.
    Jika belum ada → INSERT, jika sudah ada → UPDATE.
    Return baris hasil upsert.
    """
    existing = (
        supabase.table("semester_config")
        .select("id, skip_dates, semester_start_date")
        .eq("tahun_ajaran_id", ta_id)
        .limit(1)
        .execute()
        .data
    )

    if existing:
        res = (
            supabase.table("semester_config")
            .update({**payload, "updated_at": "now()"})
            .eq("tahun_ajaran_id", ta_id)
            .execute()
        )
    else:
        res = (
            supabase.table("semester_config")
            .insert({"tahun_ajaran_id": ta_id, **payload})
            .execute()
        )

    invalidate_config_cache()
    return res.data[0] if res.data else {}


def _get_or_create_config(ta_id: str) -> dict:
    """Ambil semester_config; buat kosong jika belum ada."""
    rows = (
        supabase.table("semester_config")
        .select("id, semester_start_date, semester_end_date, skip_dates")
        .eq("tahun_ajaran_id", ta_id)
        .limit(1)
        .execute()
        .data
    )
    if rows:
        return rows[0]
    # Belum ada → buat dengan nilai kosong
    res = (
        supabase.table("semester_config")
        .insert({"tahun_ajaran_id": ta_id, "skip_dates": []})
        .execute()
    )
    return res.data[0] if res.data else {"skip_dates": []}


def _compute_current_week(start_str: str | None, skip_dates: list) -> int | None:
    """Hitung minggu berjalan berdasarkan hari ini."""
    if not start_str:
        return None
    try:
        today = datetime.now(_WIB).date()
        # Hari Minggu dianggap sudah masuk minggu berikutnya
        if today.weekday() == 6:
            today = today + timedelta(days=1)
        start = date.fromisoformat(start_str)
        skip_mondays = set()
        for s in skip_dates:
            try:
                d = date.fromisoformat(str(s))
                skip_mondays.add(_get_week_monday(d))
            except (ValueError, TypeError):
                pass

        start_monday = _get_week_monday(start)
        today_monday = _get_week_monday(today)

        if today_monday < start_monday:
            return None  # belum mulai

        week = 0
        cur  = start_monday
        while cur <= today_monday:
            if cur not in skip_mondays:
                week += 1
            cur += timedelta(weeks=1)
        return max(1, week)
    except Exception:
        return None


# ── 1. GET config semester aktif ─────────────────────────────────────────────

@router.get("/config")
def get_config(user: dict = Depends(optional_authenticated)):
    ta  = _get_active_ta()
    cfg = _get_or_create_config(ta["id"])

    start_str  = cfg.get("semester_start_date")
    end_str    = cfg.get("semester_end_date")
    skip_dates = cfg.get("skip_dates") or []

    # Normalisasi skip_dates ke Senin
    normalized_skip = []
    for s in skip_dates:
        try:
            d = date.fromisoformat(str(s))
            normalized_skip.append(_get_week_monday(d).isoformat())
        except (ValueError, TypeError):
            pass
    normalized_skip = sorted(set(normalized_skip))

    minggu_berjalan = _compute_current_week(start_str, normalized_skip)

    return {
        "tahun_ajaran_id":     ta["id"],
        "tahun_ajaran":        f"{ta['tahun']} {ta['semester'].capitalize()}",
        "semester_start_date": start_str,
        "semester_end_date":   end_str,
        "skip_dates":          normalized_skip,
        "minggu_berjalan":     minggu_berjalan,
    }


# ── 2. POST tambah skip date ──────────────────────────────────────────────────

@router.post("/skip-dates")
def add_skip_date(body: SkipDateBody, user: dict = Depends(optional_authenticated)):
    try:
        d = date.fromisoformat(body.tanggal)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal tidak valid (gunakan YYYY-MM-DD)")

    monday = _get_week_monday(d).isoformat()

    ta  = _get_active_ta()
    cfg = _get_or_create_config(ta["id"])

    skip = list(cfg.get("skip_dates") or [])
    # Normalisasi existing ke Senin sebelum cek duplikat
    skip_normalized = [_get_week_monday(date.fromisoformat(str(s))).isoformat() for s in skip]

    if monday in skip_normalized:
        raise HTTPException(status_code=409, detail=f"Minggu {monday} sudah ada di daftar skip")

    skip_normalized.append(monday)
    skip_normalized = sorted(set(skip_normalized))

    _upsert_config(ta["id"], {"skip_dates": skip_normalized})
    logger.info(f"[PERTEMUAN] Skip date ditambah: {monday} (ta={ta['id']})")

    return {
        "status":     "success",
        "message":    f"Minggu {monday} berhasil ditambahkan ke daftar skip",
        "skip_dates": skip_normalized,
    }


# ── 3. DELETE hapus skip date ─────────────────────────────────────────────────

@router.delete("/skip-dates/{tanggal}")
def delete_skip_date(tanggal: str, user: dict = Depends(optional_authenticated)):
    try:
        d = date.fromisoformat(tanggal)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal tidak valid (gunakan YYYY-MM-DD)")

    monday = _get_week_monday(d).isoformat()

    ta  = _get_active_ta()
    cfg = _get_or_create_config(ta["id"])

    skip = list(cfg.get("skip_dates") or [])
    skip_normalized = [_get_week_monday(date.fromisoformat(str(s))).isoformat() for s in skip]

    if monday not in skip_normalized:
        raise HTTPException(status_code=404, detail=f"Tanggal {monday} tidak ditemukan di daftar skip")

    skip_normalized.remove(monday)
    skip_normalized = sorted(skip_normalized)

    _upsert_config(ta["id"], {"skip_dates": skip_normalized})
    logger.info(f"[PERTEMUAN] Skip date dihapus: {monday} (ta={ta['id']})")

    return {
        "status":     "success",
        "message":    f"Minggu {monday} berhasil dihapus dari daftar skip",
        "skip_dates": skip_normalized,
    }


# ── 4. POST upload PDF kalender ───────────────────────────────────────────────

@router.post("/upload-kalender")
async def upload_kalender(
    file: UploadFile = File(...),
    user: dict = Depends(optional_authenticated),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Hanya file .pdf yang didukung")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="Ukuran file melebihi 10 MB")

    # Parse PDF
    try:
        parsed = parse_kalender_pdf(pdf_bytes)
    except Exception as e:
        logger.error(f"[PERTEMUAN] Gagal parse PDF kalender: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Gagal membaca PDF: {e}")

    if not parsed:
        raise HTTPException(
            status_code=422,
            detail="Tidak ada data semester yang ditemukan dalam PDF. "
                   "Pastikan format PDF adalah Kalender Akademik Universitas Telkom.",
        )

    # Ambil semua tahun_ajaran dari DB untuk mapping kode → id
    all_ta = (
        supabase.table("tahun_ajaran")
        .select("id, tahun, semester")
        .execute()
        .data or []
    )

    # Buat lookup: "2025-2" → ta_id
    def _kode_from_ta(ta_row: dict) -> str:
        year_first = int(ta_row["tahun"].split("/")[0])
        sem_num    = "1" if ta_row["semester"].lower() == "ganjil" else "2"
        return f"{year_first}-{sem_num}"

    ta_lookup: dict[str, str] = {_kode_from_ta(t): t["id"] for t in all_ta}

    # Auto-create tahun_ajaran yang belum ada di DB
    for sem in parsed:
        kode = sem["kode_semester"]
        if kode in ta_lookup:
            continue
        year_str, sem_num = kode.split("-")
        year      = int(year_str)
        tahun_str = f"{year}/{year + 1}"
        sem_label = "ganjil" if sem_num == "1" else "genap"
        res = (
            supabase.table("tahun_ajaran")
            .insert({"tahun": tahun_str, "semester": sem_label, "is_aktif": False})
            .execute()
        )
        if res.data:
            new_id = res.data[0]["id"]
            ta_lookup[kode] = new_id
            logger.info(f"[PERTEMUAN] Auto-create tahun_ajaran: {tahun_str} {sem_label} (id={new_id})")

    # Simpan config untuk SEMUA semester yang ada match di DB
    saved, skipped = [], []
    for sem in parsed:
        kode   = sem["kode_semester"]
        ta_id  = ta_lookup.get(kode)
        if not ta_id:
            skipped.append(kode)
            logger.warning(f"[PERTEMUAN] Gagal create tahun_ajaran untuk kode={kode}, dilewati")
            continue

        payload = {
            "semester_start_date": sem["semester_start_date"],
            "semester_end_date":   sem.get("semester_end_date"),
            "skip_dates":          sem["skip_dates"],
        }
        _upsert_config(ta_id, payload)
        saved.append(kode)
        logger.info(
            f"[PERTEMUAN] Disimpan | kode={kode} ta={ta_id} "
            f"start={sem['semester_start_date']} "
            f"end={sem.get('semester_end_date')} "
            f"skip={sem['skip_dates']}"
        )

    if not saved:
        available = [s["kode_semester"] for s in parsed]
        raise HTTPException(
            status_code=404,
            detail=(
                f"Tidak ada semester dari PDF yang cocok dengan data tahun ajaran di DB. "
                f"Semester di PDF: {available}"
            ),
        )

    # Tentukan semester aktif untuk response summary
    ta_aktif   = _get_active_ta()
    year_first = int(ta_aktif["tahun"].split("/")[0])
    sem_num    = "1" if ta_aktif["semester"].lower() == "ganjil" else "2"
    kode_aktif = f"{year_first}-{sem_num}"
    target     = next((s for s in parsed if s["kode_semester"] == kode_aktif), parsed[0])

    return {
        "status":              "success",
        "message":             f"Berhasil menyimpan {len(saved)} semester dari PDF",
        "semester_disimpan":   saved,
        "semester_dilewati":   skipped,
        "semester_aktif":      kode_aktif,
        "semester_start_date": target["semester_start_date"],
        "semester_end_date":   target.get("semester_end_date"),
        "skip_dates":          target["skip_dates"],
        "semua_semester_parsed": [s["kode_semester"] for s in parsed],
    }


# ── Legacy alias: /kalender-akademik/upload-pdf → sama dengan /pertemuan/upload-kalender

@router_legacy.post("/upload-pdf")
async def upload_kalender_legacy(
    file: UploadFile = File(...),
    user: dict = Depends(optional_authenticated),
):
    return await upload_kalender(file=file, user=user)
