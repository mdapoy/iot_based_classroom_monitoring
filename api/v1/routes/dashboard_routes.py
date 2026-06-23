from fastapi import APIRouter, Depends, Query
from repositories.supabase_client import supabase
from repositories.cache import get_all_jadwal
from api.v1.deps import require_authenticated
from core.logger import logger
from datetime import date, timedelta
from typing import Optional
import re

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# =========================================================
# SUMMARY (untuk 4 KPI card di Dashboard)
# =========================================================
@router.get("/summary")
def get_summary(
    range_days: int = Query(180, description="rentang hari utk menghitung tepat_waktu_pct"),
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
    user: dict = Depends(require_authenticated),
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


# =========================================================
# HELPER: durasi RPS & aktual
# =========================================================
def _parse_rps_minutes(pengalaman: Optional[str]):
    """Durasi harapan (menit) dari pengalaman_pembelajaran_mahasiswa RPS."""
    if not pengalaman:
        return None
    m = re.search(r"(\d+)\s*[xX]\s*(\d+)", pengalaman)
    if m:
        return int(m.group(1)) * int(m.group(2))
    m2 = re.search(r"(\d+)\s*menit", pengalaman, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    return None


def _actual_minutes_from_activity(stats: Optional[dict]):
    """Durasi aktual (menit) dari baris activity_stats."""
    if not stats:
        return None
    total_sec = sum(
        (stats.get(k) or 0)
        for k in ("ceramah_sec", "tanya_jawab_sec", "diskusi_sec", "diam_sec")
    )
    return round(total_sec / 60) if total_sec > 0 else None


def _actual_minutes_from_summary(summary: Optional[dict]):
    """Durasi aktual (menit) dari activity_summary jsonb (data laporan lama)."""
    if not summary or not isinstance(summary, dict):
        return None
    total_sec = sum(
        (summary.get(k) or 0)
        for k in ("ceramah_sec", "tanya_jawab_sec", "diskusi_sec", "diam_sec")
    )
    return round(total_sec / 60) if total_sec > 0 else None


# =========================================================
# DETEKSI ANOMALI
# Anomali = pertemuan yang:
#   • Durasi aktual < durasi RPS  → "Durasi Kurang"
#   • kesesuaian_materi TIDAK SESUAI → "Materi Tidak Sesuai"
#   • kesesuaian_materi SEBAGIAN SESUAI → "Materi Sebagian Sesuai"
# =========================================================
@router.get("/anomali")
def get_anomali(
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
    user: dict = Depends(require_authenticated),
):
    # ── reports yang sudah selesai ──────────────────────────────────────────
    reports = (
        supabase.table("reports")
        .select("id, tanggal, jam, ruangan, kode_matkul, kode_dosen, kelas, "
                "status_waktu, kesesuaian_materi, activity_summary")
        .eq("status", "done")
        .execute()
        .data or []
    )

    # ── activity_stats per report (untuk durasi aktual data baru) ───────────
    report_ids = [r["id"] for r in reports]
    stats_by_report: dict = {}
    if report_ids:
        for s in (
            supabase.table("activity_stats")
            .select("report_id, ceramah_sec, tanya_jawab_sec, diskusi_sec, diam_sec")
            .in_("report_id", report_ids)
            .execute()
            .data or []
        ):
            rid = s.get("report_id")
            if rid is not None and rid not in stats_by_report:
                stats_by_report[rid] = s

    # ── durasi harapan RPS per kode_matkul ──────────────────────────────────
    rps_minutes: dict = {}
    for r in (
        supabase.table("rps_pertemuan")
        .select("kode_matkul, pengalaman_pembelajaran_mahasiswa")
        .execute()
        .data or []
    ):
        kode = r.get("kode_matkul")
        if kode and kode not in rps_minutes:
            mnt = _parse_rps_minutes(r.get("pengalaman_pembelajaran_mahasiswa"))
            if mnt:
                rps_minutes[kode] = mnt

    # ── nama matkul + filter TA dari jadwal_kuliah ──────────────────────────
    jadwal = (
        supabase.table("jadwal_kuliah")
        .select("kode_mata_kuliah, mata_kuliah, tahun_ajaran_id")
        .execute()
        .data or []
    )
    nama_map: dict = {}
    allowed_kode: set = set()
    for j in jadwal:
        kode = j.get("kode_mata_kuliah")
        if not kode:
            continue
        nama_map.setdefault(kode, j.get("mata_kuliah") or kode)
        if tahun_ajaran_id and j.get("tahun_ajaran_id") == tahun_ajaran_id:
            allowed_kode.add(kode)

    out = []
    for r in reports:
        kode = r.get("kode_matkul")

        if tahun_ajaran_id and kode not in allowed_kode:
            continue

        # Durasi aktual: coba activity_stats dulu, fallback ke activity_summary jsonb
        actual = _actual_minutes_from_activity(stats_by_report.get(r["id"]))
        if actual is None:
            actual = _actual_minutes_from_summary(r.get("activity_summary"))

        expected = rps_minutes.get(kode)
        jenis = []

        if actual is not None and expected is not None and actual < expected:
            jenis.append("Durasi Kurang")

        materi = (r.get("kesesuaian_materi") or "").upper()
        if "TIDAK SESUAI" in materi:
            jenis.append("Materi Tidak Sesuai")
        elif "SEBAGIAN SESUAI" in materi:
            jenis.append("Materi Sebagian Sesuai")

        if not jenis:
            continue

        out.append({
            "report_id":         r["id"],
            "tanggal":           r.get("tanggal"),
            "jam":               r.get("jam"),
            "ruangan":           r.get("ruangan"),
            "kode_matkul":       kode,
            "matkul":            nama_map.get(kode, kode),
            "kelas":             r.get("kelas"),
            "kode_dosen":        r.get("kode_dosen"),
            "jenis":             jenis,
            "durasi_aktual":     actual,
            "durasi_rps":        expected,
            "kesesuaian_materi": r.get("kesesuaian_materi"),
            "status_waktu":      r.get("status_waktu"),
        })

    out.sort(key=lambda x: str(x["tanggal"] or ""), reverse=True)
    logger.info(f"[DASHBOARD] anomali: {len(out)} item (ta={tahun_ajaran_id})")
    return out
