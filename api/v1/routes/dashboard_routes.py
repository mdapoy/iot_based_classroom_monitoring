from fastapi import APIRouter, Query
from repositories.supabase_client import supabase
from repositories.cache import get_all_jadwal
from core.logger import logger
from datetime import date, timedelta
from typing import Optional

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# =========================================================
# SUMMARY (untuk 4 KPI card di Dashboard)
# =========================================================
@router.get("/summary")
def get_summary(
    range_days: int = Query(180, description="rentang hari utk menghitung tepat_waktu_pct"),
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
):
    # ── total kelas (distinct kelas dari jadwal_kuliah) ──
    jadwal_rows = get_all_jadwal()

    if tahun_ajaran_id:
        jadwal_rows = [j for j in jadwal_rows if j.get("tahun_ajaran_id") == tahun_ajaran_id]
        logger.info(f"[DASHBOARD] summary filtered by tahun_ajaran_id={tahun_ajaran_id} → {len(jadwal_rows)} jadwal")

    total_kelas  = len({j["kelas"]            for j in jadwal_rows if j.get("kelas")})
    total_matkul = len({j["kode_mata_kuliah"] for j in jadwal_rows if j.get("kode_mata_kuliah")})

    # ── dosen tepat waktu % ──
    end = date.today()
    start = end - timedelta(days=range_days)

    sessions_q = (
        supabase.table("rec_session")
        .select("kehadiran")
        .gte("tanggal", str(start))
        .lte("tanggal", str(end))
    )
    if tahun_ajaran_id:
        jadwal_ids = [j["id"] for j in jadwal_rows if j.get("id")]
        if not jadwal_ids:
            # Tidak ada jadwal di TA ini → semua KPI nol
            return {
                "total_kelas":        total_kelas,
                "total_matkul":       total_matkul,
                "tepat_waktu_pct":    0.0,
                "total_sesi":         0,
                "aktivitas_dominan":  "-",
                "range": {"start": str(start), "end": str(end)},
            }
        sessions_q = sessions_q.in_("jadwal_id", jadwal_ids)

    sessions = sessions_q.execute().data or []

    total_sesi = len(sessions)
    tepat      = sum(1 for s in sessions if s.get("kehadiran") == "tepat_waktu")
    tepat_pct  = round(tepat / total_sesi * 100, 1) if total_sesi else 0.0

    # ── aktivitas dominan (dari activity_stats) ──
    activity_q = (
        supabase.table("activity_stats")
        .select("ceramah_sec, tanya_jawab_sec, diskusi_sec, diam_sec")
        .gte("tanggal", str(start))
        .lte("tanggal", str(end))
    )
    if tahun_ajaran_id:
        kode_matkul_set = [j["kode_mata_kuliah"] for j in jadwal_rows if j.get("kode_mata_kuliah")]
        if kode_matkul_set:
            activity_q = activity_q.in_("kode_matkul", kode_matkul_set)

    activity_rows = activity_q.execute().data or []

    agg = {
        "ceramah":     sum(r.get("ceramah_sec")     or 0 for r in activity_rows),
        "tanya_jawab": sum(r.get("tanya_jawab_sec") or 0 for r in activity_rows),
        "diskusi":     sum(r.get("diskusi_sec")     or 0 for r in activity_rows),
        "diam":        sum(r.get("diam_sec")        or 0 for r in activity_rows),
    }
    LABEL_MAP = {
        "ceramah":     "Ceramah",
        "tanya_jawab": "Tanya Jawab",
        "diskusi":     "Diskusi",
        "diam":        "Tidak Ada",
    }
    total_act = sum(agg.values())
    aktivitas_dominan = LABEL_MAP[max(agg, key=agg.get)] if total_act > 0 else "-"

    return {
        "total_kelas":        total_kelas,
        "total_matkul":       total_matkul,
        "tepat_waktu_pct":    tepat_pct,
        "total_sesi":         total_sesi,
        "aktivitas_dominan":  aktivitas_dominan,
        "range": {"start": str(start), "end": str(end)},
    }
