"""
repositories/cache.py
─────────────────────
In-memory cache untuk tabel statis yang sering dibaca.

Tabel yang di-cache:
  • jadwal_kuliah  — TTL 10 menit  (jadwal semester, sangat jarang berubah)
  • dosen          — TTL 30 menit  (data master dosen, paling statis)

Cara pakai:
  from repositories.cache import get_all_jadwal, get_all_dosen, invalidate_jadwal_cache

Invalidasi:
  Panggil invalidate_jadwal_cache() setelah upload Excel jadwal baru (xlsx_service.py)
  supaya request berikutnya mendapat data fresh dari DB.
"""

import threading
import time

from repositories.supabase_client import supabase
from core.logger import logger

# ── Lock global (satu per cache) ─────────────────────────────────────────────
_JADWAL_LOCK = threading.Lock()
_DOSEN_LOCK  = threading.Lock()

# ── State cache jadwal_kuliah ─────────────────────────────────────────────────
_jadwal_cache:      list[dict] | None = None
_jadwal_expires_at: float             = 0.0
JADWAL_TTL:         int               = 600   # 10 menit

# ── State cache dosen ─────────────────────────────────────────────────────────
_dosen_cache:      list[dict] | None = None
_dosen_expires_at: float             = 0.0
DOSEN_TTL:         int               = 1800  # 30 menit


# ═════════════════════════════════════════════════════════════════════════════
# jadwal_kuliah
# ═════════════════════════════════════════════════════════════════════════════

def get_all_jadwal() -> list[dict]:
    """
    Return semua row jadwal_kuliah (SELECT *).
    Cache TTL 10 menit. Thread-safe (double-checked locking).

    Caller tinggal filter in-memory:
        jadwal = get_all_jadwal()
        match  = next((j for j in jadwal if j["kode_mata_kuliah"] == kode), None)
    """
    global _jadwal_cache, _jadwal_expires_at

    now = time.monotonic()

    # Fast path — tidak perlu lock kalau cache masih fresh
    if _jadwal_cache is not None and now < _jadwal_expires_at:
        return _jadwal_cache

    with _JADWAL_LOCK:
        # Double-check: thread lain mungkin sudah refresh saat kita nunggu lock
        if _jadwal_cache is not None and now < _jadwal_expires_at:
            return _jadwal_cache

        logger.info("[CACHE] Refreshing jadwal_kuliah (SELECT *)")
        try:
            res = supabase.table("jadwal_kuliah").select("*").execute()
            _jadwal_cache      = res.data or []
            _jadwal_expires_at = time.monotonic() + JADWAL_TTL
            logger.info(f"[CACHE] jadwal_kuliah loaded: {len(_jadwal_cache)} rows, TTL={JADWAL_TTL}s")
        except Exception as e:
            logger.error(f"[CACHE] jadwal_kuliah refresh failed: {e}")
            # Kembalikan cache lama kalau ada, supaya sistem tidak crash total
            if _jadwal_cache is not None:
                logger.warning("[CACHE] Returning stale jadwal_kuliah cache")
                return _jadwal_cache
            raise

    return _jadwal_cache


def invalidate_jadwal_cache() -> None:
    """
    Paksa cache jadwal_kuliah dikosongkan.
    Request berikutnya akan fetch fresh dari DB.
    Panggil ini setelah insert/update ke tabel jadwal_kuliah (misal: upload Excel).
    """
    global _jadwal_cache, _jadwal_expires_at
    with _JADWAL_LOCK:
        _jadwal_cache      = None
        _jadwal_expires_at = 0.0
    logger.info("[CACHE] jadwal_kuliah cache invalidated")
    """
    Return semua dosen aktif (kode_dosen, nama_lengkap).
    Cache TTL 30 menit. Thread-safe.
    """
    global _dosen_cache, _dosen_expires_at

    now = time.monotonic()

    if _dosen_cache is not None and now < _dosen_expires_at:
        return _dosen_cache

    with _DOSEN_LOCK:
        if _dosen_cache is not None and now < _dosen_expires_at:
            return _dosen_cache

        logger.info("[CACHE] Refreshing dosen cache")
        try:
            res = (
                supabase.table("dosen")
                .select("kode_dosen, nama_lengkap")
                .eq("aktif", True)
                .execute()
            )
            _dosen_cache      = res.data or []
            _dosen_expires_at = time.monotonic() + DOSEN_TTL
            logger.info(f"[CACHE] dosen loaded: {len(_dosen_cache)} rows, TTL={DOSEN_TTL}s")
        except Exception as e:
            logger.error(f"[CACHE] dosen refresh failed: {e}")
            if _dosen_cache is not None:
                logger.warning("[CACHE] Returning stale dosen cache")
                return _dosen_cache
            raise

    return _dosen_cache


# ═════════════════════════════════════════════════════════════════════════════
# Monitoring index cache (kehadiran + activity)
# TTL 60 detik. Diinvalidasi saat laporan selesai diproses.
# ═════════════════════════════════════════════════════════════════════════════

_KEHADIRAN_IDX_LOCK = threading.Lock()
_kehadiran_idx_cache:      dict | None = None
_kehadiran_idx_expires_at: float       = 0.0

_ACTIVITY_IDX_LOCK = threading.Lock()
_activity_idx_cache:      dict | None = None
_activity_idx_expires_at: float       = 0.0

MONITORING_IDX_TTL: int = 60  # detik


def get_kehadiran_index() -> dict:
    """
    Index kehadiran dari rec_session, key = (jadwal_id, tanggal).
    Cache TTL 60 detik. Diinvalidasi oleh invalidate_monitoring_cache().
    """
    global _kehadiran_idx_cache, _kehadiran_idx_expires_at

    now = time.monotonic()
    if _kehadiran_idx_cache is not None and now < _kehadiran_idx_expires_at:
        return _kehadiran_idx_cache

    with _KEHADIRAN_IDX_LOCK:
        if _kehadiran_idx_cache is not None and now < _kehadiran_idx_expires_at:
            return _kehadiran_idx_cache

        logger.info("[CACHE] Refreshing kehadiran index (rec_session)")
        try:
            rows = (
                supabase.table("rec_session")
                .select("jadwal_id, tanggal, kehadiran")
                .order("id")
                .execute()
                .data or []
            )
            idx = {}
            for r in rows:
                if r.get("jadwal_id") and r.get("tanggal"):
                    idx[(r["jadwal_id"], r["tanggal"])] = r.get("kehadiran")
            _kehadiran_idx_cache      = idx
            _kehadiran_idx_expires_at = time.monotonic() + MONITORING_IDX_TTL
            logger.info(f"[CACHE] kehadiran index loaded: {len(idx)} entries")
        except Exception as e:
            logger.error(f"[CACHE] kehadiran index refresh failed: {e}")
            if _kehadiran_idx_cache is not None:
                logger.warning("[CACHE] Returning stale kehadiran index")
                return _kehadiran_idx_cache
            raise

    return _kehadiran_idx_cache


def get_activity_index() -> dict:
    """
    Index activity_stats, key = monitoring_id.
    Chain: monitoring.id → reports.monitoring_id → activity_stats.report_id.
    Cache TTL 60 detik. Diinvalidasi oleh invalidate_monitoring_cache().
    """
    global _activity_idx_cache, _activity_idx_expires_at

    now = time.monotonic()
    if _activity_idx_cache is not None and now < _activity_idx_expires_at:
        return _activity_idx_cache

    with _ACTIVITY_IDX_LOCK:
        if _activity_idx_cache is not None and now < _activity_idx_expires_at:
            return _activity_idx_cache

        logger.info("[CACHE] Refreshing activity index (reports → activity_stats)")
        try:
            rpt_rows = (
                supabase.table("reports")
                .select("id, monitoring_id")
                .not_.is_("monitoring_id", "null")
                .execute()
                .data or []
            )
            if not rpt_rows:
                _activity_idx_cache      = {}
                _activity_idx_expires_at = time.monotonic() + MONITORING_IDX_TTL
                return _activity_idx_cache

            report_to_mon = {r["id"]: r["monitoring_id"] for r in rpt_rows}
            rows = (
                supabase.table("activity_stats")
                .select("report_id, dominant_activity, ceramah_pct, tanya_jawab_pct, "
                        "diskusi_pct, diam_pct, pertemuan_ke")
                .in_("report_id", list(report_to_mon.keys()))
                .order("id")
                .execute()
                .data or []
            )
            idx = {}
            for r in rows:
                mon_id = report_to_mon.get(r["report_id"])
                if mon_id:
                    idx[mon_id] = r
            _activity_idx_cache      = idx
            _activity_idx_expires_at = time.monotonic() + MONITORING_IDX_TTL
            logger.info(f"[CACHE] activity index loaded: {len(idx)} entries")
        except Exception as e:
            logger.error(f"[CACHE] activity index refresh failed: {e}")
            if _activity_idx_cache is not None:
                logger.warning("[CACHE] Returning stale activity index")
                return _activity_idx_cache
            raise

    return _activity_idx_cache


def invalidate_monitoring_cache() -> None:
    """
    Reset cache kehadiran dan activity index.
    Dipanggil setelah laporan selesai diproses agar halaman monitoring
    langsung menampilkan data terbaru tanpa menunggu TTL habis.
    """
    global _kehadiran_idx_cache, _kehadiran_idx_expires_at
    global _activity_idx_cache,  _activity_idx_expires_at

    with _KEHADIRAN_IDX_LOCK:
        _kehadiran_idx_cache      = None
        _kehadiran_idx_expires_at = 0.0

    with _ACTIVITY_IDX_LOCK:
        _activity_idx_cache      = None
        _activity_idx_expires_at = 0.0

    logger.info("[CACHE] monitoring index cache invalidated")
