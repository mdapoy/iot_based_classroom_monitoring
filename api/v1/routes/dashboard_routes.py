from fastapi import APIRouter, Query
from repositories.supabase_client import supabase
from datetime import date, timedelta
from typing import Optional

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# =========================================================
# SUMMARY (untuk 4 KPI card di Dashboard)
# =========================================================
@router.get("/summary")
def get_summary(
    range_days: int = Query(30, description="rentang hari utk menghitung tepat_waktu_pct"),
):
    # ── total kelas (distinct kelas dari jadwal_kuliah) ──
    jadwal_rows = (
        supabase.table("jadwal_kuliah")
        .select("kelas, kode_mata_kuliah")
        .execute()
        .data
        or []
    )

    total_kelas  = len({j["kelas"]            for j in jadwal_rows if j.get("kelas")})
    total_matkul = len({j["kode_mata_kuliah"] for j in jadwal_rows if j.get("kode_mata_kuliah")})

    # ── dosen tepat waktu % ──
    end = date.today()
    start = end - timedelta(days=range_days)

    sessions = (
        supabase.table("rec_session")
        .select("kehadiran")
        .gte("tanggal", str(start))
        .lte("tanggal", str(end))
        .execute()
        .data
        or []
    )

    total_sesi = len(sessions)
    tepat      = sum(1 for s in sessions if s.get("kehadiran") == "tepat_waktu")
    tepat_pct  = round(tepat / total_sesi * 100, 1) if total_sesi else 0.0

    return {
        "total_kelas":     total_kelas,
        "total_matkul":    total_matkul,
        "tepat_waktu_pct": tepat_pct,
        "total_sesi":      total_sesi,
        "range": {"start": str(start), "end": str(end)},
    }
