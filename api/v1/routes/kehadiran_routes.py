from fastapi import APIRouter, Query
from repositories.supabase_client import supabase
from datetime import date, timedelta, datetime
from collections import defaultdict
from typing import Optional

router = APIRouter(prefix="/kehadiran", tags=["Kehadiran"])


# =========================================================
# HELPER: ambil rec_session + relasi (dosen, jadwal_kuliah)
# =========================================================
def _fetch_sessions(
    dosen_kode: Optional[str] = None,
    kelas: Optional[str] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
):
    query = (
        supabase
        .table("rec_session")
        .select(
            "id, tanggal, kehadiran, keterlambatan, started_at, stopped_at, "
            "dosen:dosen_id ( id, kode_dosen, nama_lengkap ), "
            "jadwal:jadwal_id ( id, mata_kuliah, kode_mata_kuliah, kelas, "
            "jam_mulai, jam_selesai, ruangan )"
        )
    )

    if start:
        query = query.gte("tanggal", str(start))
    if end:
        query = query.lte("tanggal", str(end))

    rows = query.execute().data or []

    # filter dosen / kelas di Python (karena nested filter butuh foreignTable)
    if dosen_kode:
        rows = [r for r in rows if (r.get("dosen") or {}).get("kode_dosen") == dosen_kode]
    if kelas:
        rows = [r for r in rows if (r.get("jadwal") or {}).get("kelas") == kelas]

    return rows


# =========================================================
# 1. SUMMARY (untuk gauge mini & KPI box modal)
# =========================================================
@router.get("/summary")
def get_summary(
    dosen: Optional[str] = Query(None, description="kode_dosen filter"),
    kelas: Optional[str] = Query(None, description="kelas filter"),
    range_days: int = Query(30, description="rentang hari ke belakang"),
):
    end = date.today()
    start = end - timedelta(days=range_days)

    sessions = _fetch_sessions(dosen, kelas, start, end)

    total = len(sessions)
    tepat = sum(1 for s in sessions if s.get("kehadiran") == "tepat_waktu")
    terlambat = sum(1 for s in sessions if s.get("kehadiran") == "terlambat")

    # "tidak hadir" diestimasi: total rec_session yang kehadiran-nya NULL/lainnya
    tidak_hadir = total - tepat - terlambat

    pct = lambda x: round(x / total * 100, 1) if total else 0.0

    # Gauge "Kehadiran Dosen" = (tepat + terlambat) / total * 100 (rate kehadiran)
    gauge_value = pct(tepat + terlambat)

    return {
        "gauge_value": gauge_value,
        "tepat_waktu_pct": pct(tepat),
        "terlambat_pct": pct(terlambat),
        "tidak_hadir_pct": pct(tidak_hadir),
        "total_pertemuan": total,
        "range": {"start": str(start), "end": str(end)},
        "filters": {"dosen": dosen, "kelas": kelas},
    }


# =========================================================
# 2. TREND (line chart 7 / 30 hari)
# =========================================================
@router.get("/trend")
def get_trend(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    days: int = Query(7, description="berapa hari ke belakang"),
):
    end = date.today()
    start = end - timedelta(days=days - 1)

    sessions = _fetch_sessions(dosen, kelas, start, end)

    # group per tanggal
    bucket: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "hadir": 0})

    for s in sessions:
        t = s.get("tanggal")
        if not t:
            continue
        bucket[t]["total"] += 1
        if s.get("kehadiran") in ("tepat_waktu", "terlambat"):
            bucket[t]["hadir"] += 1

    HARI_ID = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]

    data = []
    for i in range(days):
        d = start + timedelta(days=i)
        key = str(d)
        b = bucket.get(key, {"total": 0, "hadir": 0})
        value = round(b["hadir"] / b["total"] * 100, 1) if b["total"] else 0.0

        data.append({
            "date": key,
            "day": HARI_ID[d.weekday()],
            "value": value,
            "total": b["total"],
        })

    return data


# =========================================================
# 3. RANKING dosen
# =========================================================
@router.get("/ranking")
def get_ranking(
    kelas: Optional[str] = None,
    range_days: int = 30,
    limit: int = 10,
):
    end = date.today()
    start = end - timedelta(days=range_days)

    sessions = _fetch_sessions(None, kelas, start, end)

    agg: dict[int, dict] = {}

    for s in sessions:
        d = s.get("dosen") or {}
        did = d.get("id")
        if did is None:
            continue
        if did not in agg:
            agg[did] = {
                "id": did,
                "kode_dosen": d.get("kode_dosen"),
                "nama": (d.get("nama_lengkap") or "").strip(),
                "total": 0,
                "hadir": 0,
            }
        agg[did]["total"] += 1
        if s.get("kehadiran") in ("tepat_waktu", "terlambat"):
            agg[did]["hadir"] += 1

    def status_of(pct: float) -> str:
        if pct >= 95:
            return "Sangat Baik"
        if pct >= 85:
            return "Baik"
        if pct >= 75:
            return "Cukup"
        return "Warning"

    ranking = []
    for v in agg.values():
        pct = round(v["hadir"] / v["total"] * 100, 1) if v["total"] else 0.0
        ranking.append({
            "id": v["id"],
            "kode_dosen": v["kode_dosen"],
            "name": v["nama"],
            "attendance": pct,
            "status": status_of(pct),
            "total_pertemuan": v["total"],
        })

    ranking.sort(key=lambda x: x["attendance"], reverse=True)

    return ranking[:limit]


# =========================================================
# 4. DETAIL per Mata Kuliah
# =========================================================
@router.get("/per-matkul")
def get_per_matkul(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    range_days: int = 30,
):
    end = date.today()
    start = end - timedelta(days=range_days)

    sessions = _fetch_sessions(dosen, kelas, start, end)

    agg: dict[str, dict] = {}

    for s in sessions:
        j = s.get("jadwal") or {}
        d = s.get("dosen") or {}
        key = j.get("kode_mata_kuliah") or "UNKNOWN"

        if key not in agg:
            agg[key] = {
                "kode_matkul": key,
                "matkul": j.get("mata_kuliah"),
                "dosen": (d.get("nama_lengkap") or "").strip(),
                "kode_dosen": d.get("kode_dosen"),
                "kelas": j.get("kelas"),
                "total": 0,
                "tepat": 0,
                "terlambat": 0,
            }

        agg[key]["total"] += 1
        if s.get("kehadiran") == "tepat_waktu":
            agg[key]["tepat"] += 1
        elif s.get("kehadiran") == "terlambat":
            agg[key]["terlambat"] += 1

    out = []
    for v in agg.values():
        total = v["total"]
        tidak = total - v["tepat"] - v["terlambat"]
        out.append({
            "matkul": v["matkul"],
            "kode_matkul": v["kode_matkul"],
            "dosen": v["dosen"],
            "kode_dosen": v["kode_dosen"],
            "kelas": v["kelas"],
            "hadir": round(v["tepat"] / total * 100, 1) if total else 0,
            "terlambat": round(v["terlambat"] / total * 100, 1) if total else 0,
            "tidakHadir": round(tidak / total * 100, 1) if total else 0,
            "total_pertemuan": total,
        })

    out.sort(key=lambda x: x["hadir"], reverse=True)
    return out


# =========================================================
# 5. DASHBOARD ALL-IN-ONE (opsional, satu kali fetch)
# =========================================================
@router.get("/dashboard")
def get_dashboard(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    range_days: int = 30,
    trend_days: int = 7,
):
    return {
        "summary": get_summary(dosen, kelas, range_days),
        "trend": get_trend(dosen, kelas, trend_days),
        "ranking": get_ranking(kelas, range_days, 10),
        "per_matkul": get_per_matkul(dosen, kelas, range_days),
    }


# =========================================================
# 6. OPTIONS (untuk dropdown filter di modal)
# =========================================================
@router.get("/options")
def get_options():
    from repositories.cache import get_all_jadwal, get_all_dosen
    dosen = get_all_dosen()
    kelas = get_all_jadwal()

    return {
        "dosen": [{"kode": d["kode_dosen"], "nama": (d.get("nama_lengkap") or "").strip()} for d in dosen],
        "kelas": sorted({k["kelas"] for k in kelas if k.get("kelas")}),
    }
