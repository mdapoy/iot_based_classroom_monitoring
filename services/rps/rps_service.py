import os
import time
from datetime import date, timedelta
from dotenv import load_dotenv
from repositories.supabase_client import supabase
from core.logger import logger

load_dotenv()

# ── Env fallback (dipakai jika DB belum punya config) ────────────────────────
_ENV_START  = os.getenv("SEMESTER_START_DATE", "2026-02-23")
_ENV_SKIP   = [s.strip() for s in os.getenv("SKIP_WEEKS", "2026-03-16").split(",") if s.strip()]

# ── In-memory cache untuk semester config ────────────────────────────────────
_cfg_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 60  # detik


def _env_config() -> dict:
    """Fallback ke env vars jika DB tidak punya config untuk semester aktif."""
    return {
        "semester_start_date": _ENV_START,
        "skip_dates":          _ENV_SKIP,
    }


def _fetch_active_config() -> dict:
    """Query DB: ambil semester_config milik tahun_ajaran yang is_aktif=True."""
    try:
        ta = (
            supabase.table("tahun_ajaran")
            .select("id")
            .eq("is_aktif", True)
            .limit(1)
            .execute()
            .data
        )
        if not ta:
            return _env_config()

        cfg = (
            supabase.table("semester_config")
            .select("semester_start_date, skip_dates")
            .eq("tahun_ajaran_id", ta[0]["id"])
            .limit(1)
            .execute()
            .data
        )
        if not cfg or not cfg[0].get("semester_start_date"):
            return _env_config()

        return {
            "semester_start_date": cfg[0]["semester_start_date"],
            "skip_dates":          cfg[0].get("skip_dates") or [],
        }
    except Exception as e:
        logger.warning(f"[RPS] Gagal fetch semester_config dari DB, pakai env: {e}")
        return _env_config()


def get_active_config() -> dict:
    """Return config semester aktif dengan cache TTL 60 detik."""
    now = time.monotonic()
    if _cfg_cache["data"] and now - _cfg_cache["ts"] < _CACHE_TTL:
        return _cfg_cache["data"]

    data = _fetch_active_config()
    _cfg_cache["data"] = data
    _cfg_cache["ts"]   = now
    return data


def invalidate_config_cache() -> None:
    """Paksa refresh cache pada request berikutnya."""
    _cfg_cache["data"] = None
    _cfg_cache["ts"]   = 0.0


def _get_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_meeting_week(tanggal_str: str) -> int:
    """
    Hitung pertemuan ke-N berdasarkan tanggal rekaman.
    Baca semester_start_date dan skip_dates dari DB (semester aktif),
    fallback ke env vars jika belum dikonfigurasi.
    """
    cfg            = get_active_config()
    recording_date = date.fromisoformat(str(tanggal_str))
    semester_start = date.fromisoformat(cfg["semester_start_date"])

    # Normalisasi skip_dates ke set of Senin
    skip_mondays: set[date] = set()
    for s in cfg["skip_dates"]:
        try:
            d = date.fromisoformat(str(s))
            skip_mondays.add(_get_week_monday(d))
        except (ValueError, TypeError):
            logger.warning(f"[RPS] Format skip_date salah, abaikan: '{s}'")

    rec_monday   = _get_week_monday(recording_date)
    start_monday = _get_week_monday(semester_start)

    if rec_monday < start_monday:
        logger.warning(f"[RPS] Tanggal rekaman {tanggal_str} sebelum semester mulai")
        return 1

    pertemuan = 0
    current   = start_monday
    while current <= rec_monday:
        if current not in skip_mondays:
            pertemuan += 1
        current += timedelta(weeks=1)

    return max(1, pertemuan)


def get_rps_for_week(kode_matkul: str, pertemuan_ke: int) -> dict | None:
    """
    Ambil data RPS dari tabel rps_pertemuan untuk pertemuan tertentu.
    Return None jika tidak ditemukan.
    """
    try:
        res = (
            supabase.table("rps_pertemuan")
            .select("*")
            .eq("kode_matkul", kode_matkul)
            .eq("pertemuan_ke", pertemuan_ke)
            .single()
            .execute()
        )

        return res.data if res.data else None

    except Exception as e:
        logger.warning(
            f"[RPS] Not found | kode_matkul={kode_matkul} pertemuan={pertemuan_ke} | {e}"
        )
        return None


def get_all_rps_for_matkul(kode_matkul: str) -> list[dict]:
    """
    Ambil SEMUA pertemuan RPS untuk satu mata kuliah, urut berdasarkan pertemuan_ke.
    Digunakan untuk menyusun konteks kurikulum lengkap pada prompt RAG.
    Return list kosong jika tidak ditemukan.
    """
    try:
        res = (
            supabase.table("rps_pertemuan")
            .select("*")
            .eq("kode_matkul", kode_matkul)
            .order("pertemuan_ke")
            .execute()
        )
        return res.data or []

    except Exception as e:
        logger.warning(
            f"[RPS] get_all failed | kode_matkul={kode_matkul} | {e}"
        )
        return []


def get_nama_matkul(kode_matkul: str) -> str:
    """
    Ambil nama lengkap mata kuliah dari jadwal_kuliah berdasarkan kode_matkul.
    Menggunakan in-memory cache (TTL 10 menit) — tidak query DB langsung.
    Fallback ke kode_matkul jika tidak ditemukan.
    """
    try:
        from repositories.cache import get_all_jadwal
        jadwal = get_all_jadwal()
        match = next(
            (j for j in jadwal if j.get("kode_mata_kuliah") == kode_matkul),
            None,
        )
        if match:
            return match["mata_kuliah"]
        logger.warning(f"[RPS] nama_matkul not found | kode_matkul={kode_matkul}, fallback ke kode")
        return kode_matkul

    except Exception as e:
        logger.warning(f"[RPS] get_nama_matkul failed | kode_matkul={kode_matkul} | {e}")
        return kode_matkul
