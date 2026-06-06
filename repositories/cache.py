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


# ═════════════════════════════════════════════════════════════════════════════
# dosen
# ═════════════════════════════════════════════════════════════════════════════

def get_all_dosen() -> list[dict]:
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
