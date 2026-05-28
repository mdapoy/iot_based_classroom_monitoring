from fastapi import APIRouter, Query
from repositories.supabase_client import supabase
from datetime import date, timedelta
from typing import Optional
from collections import defaultdict
from calendar import monthrange

router = APIRouter(prefix="/aktivitas", tags=["Aktivitas Pembelajaran"])

# Bulan dalam Bahasa Indonesia (3 huruf, sesuai format frontend "Mei 2026")
BULAN_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
            "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

DAYS_ID = ["SEN", "SEL", "RAB", "KAM", "JUM", "SAB", "MIN"]


def _parse_month(label: str) -> tuple[int, int]:
    """Convert 'Mei 2026' → (2026, 5). Raise ValueError kalau invalid."""
    parts = label.strip().split(" ")
    if len(parts) != 2:
        raise ValueError(f"Invalid month format: {label}")
    bulan_str, year_str = parts
    return int(year_str), BULAN_ID.index(bulan_str) + 1


# =========================================================
# HELPER: ambil semua activity_stats di rentang waktu +
# enrich dengan info matkul/kelas/dosen dari jadwal_kuliah
# (join via kode_matkul karena jadwal_id sering NULL)
# =========================================================
def _fetch_stats(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
):
    q = supabase.table("activity_stats").select(
        "id, tanggal, kode_matkul, pertemuan_ke, "
        "dominant_activity, ceramah_pct, tanya_jawab_pct, diskusi_pct, diam_pct, "
        "ceramah_sec, tanya_jawab_sec, diskusi_sec, diam_sec, jadwal_id"
    )
    if start:
        q = q.gte("tanggal", str(start))
    if end:
        q = q.lte("tanggal", str(end))

    stats = q.execute().data or []

    # ── ambil jadwal_kuliah utk enrichment ──
    jadwal_rows = (
        supabase.table("jadwal_kuliah")
        .select("id, kode_mata_kuliah, mata_kuliah, kelas, dosen_utama")
        .execute()
        .data
        or []
    )

    # index: by id, by kode_matkul (list)
    by_id: dict = {j["id"]: j for j in jadwal_rows}
    by_kode: dict = defaultdict(list)
    for j in jadwal_rows:
        if j.get("kode_mata_kuliah"):
            by_kode[j["kode_mata_kuliah"]].append(j)

    enriched = []
    for s in stats:
        j = None
        if s.get("jadwal_id") and s["jadwal_id"] in by_id:
            j = by_id[s["jadwal_id"]]
        elif s.get("kode_matkul") in by_kode:
            j = by_kode[s["kode_matkul"]][0]

        s["mata_kuliah"]  = (j or {}).get("mata_kuliah")
        s["kelas"]        = (j or {}).get("kelas")
        s["dosen_utama"]  = (j or {}).get("dosen_utama")
        enriched.append(s)

    # ── filter ──
    if dosen:
        enriched = [s for s in enriched if s.get("dosen_utama") == dosen]
    if kelas:
        enriched = [s for s in enriched if s.get("kelas") == kelas]

    return enriched


# =========================================================
# 1. SUMMARY (pie chart + legend %)
# =========================================================
@router.get("/summary")
def get_summary(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    range_days: int = Query(30, description="rentang hari ke belakang"),
):
    end = date.today()
    start = end - timedelta(days=range_days)

    rows = _fetch_stats(dosen, kelas, start, end)

    # weighted by detik agar lebih akurat dari rata-rata persen
    total_sec = sum(
        (r.get("ceramah_sec") or 0)
        + (r.get("tanya_jawab_sec") or 0)
        + (r.get("diskusi_sec") or 0)
        + (r.get("diam_sec") or 0)
        for r in rows
    )

    if total_sec > 0:
        agg_sec = {
            "ceramah":     sum(r.get("ceramah_sec") or 0 for r in rows),
            "tanya_jawab": sum(r.get("tanya_jawab_sec") or 0 for r in rows),
            "diskusi":     sum(r.get("diskusi_sec") or 0 for r in rows),
            "diam":        sum(r.get("diam_sec") or 0 for r in rows),
        }
        pct = {k: round(v / total_sec * 100, 1) for k, v in agg_sec.items()}
    else:
        pct = {"ceramah": 0.0, "tanya_jawab": 0.0, "diskusi": 0.0, "diam": 0.0}

    # dominant activity = key dengan pct tertinggi
    LABEL_MAP = {
        "ceramah":     "Ceramah",
        "tanya_jawab": "Tanya Jawab",
        "diskusi":     "Diskusi",
        "diam":        "Tidak Ada",
    }
    if total_sec > 0:
        dominant_key = max(pct, key=pct.get)
        dominant_label = LABEL_MAP[dominant_key]
        dominant_pct = pct[dominant_key]
    else:
        dominant_key = None
        dominant_label = "-"
        dominant_pct = 0.0

    return {
        "ceramah_pct":      pct["ceramah"],
        "tanya_jawab_pct":  pct["tanya_jawab"],
        "diskusi_pct":      pct["diskusi"],
        "diam_pct":         pct["diam"],
        "dominant_activity": dominant_label,
        "dominant_key":      dominant_key,
        "dominant_pct":      dominant_pct,
        "total_pertemuan":   len(rows),
        "range": {"start": str(start), "end": str(end)},
        "filters": {"dosen": dosen, "kelas": kelas},
    }


# =========================================================
# 2. PER-MATKUL (tabel detail modal)
# =========================================================
@router.get("/per-matkul")
def get_per_matkul(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    range_days: int = 30,
):
    end = date.today()
    start = end - timedelta(days=range_days)

    rows = _fetch_stats(dosen, kelas, start, end)

    agg: dict[str, dict] = {}
    for r in rows:
        key = r.get("kode_matkul") or "UNKNOWN"
        if key not in agg:
            agg[key] = {
                "kode_matkul": key,
                "matkul": r.get("mata_kuliah") or key,
                "kelas":  r.get("kelas"),
                "dosen":  r.get("dosen_utama"),
                "ceramah_sec": 0.0,
                "tanya_jawab_sec": 0.0,
                "diskusi_sec": 0.0,
                "diam_sec": 0.0,
                "total_pertemuan": 0,
            }

        agg[key]["ceramah_sec"]     += r.get("ceramah_sec") or 0
        agg[key]["tanya_jawab_sec"] += r.get("tanya_jawab_sec") or 0
        agg[key]["diskusi_sec"]     += r.get("diskusi_sec") or 0
        agg[key]["diam_sec"]        += r.get("diam_sec") or 0
        agg[key]["total_pertemuan"] += 1

    out = []
    for v in agg.values():
        total = v["ceramah_sec"] + v["tanya_jawab_sec"] + v["diskusi_sec"] + v["diam_sec"]
        if total > 0:
            ceramah   = round(v["ceramah_sec"]     / total * 100)
            tanya     = round(v["tanya_jawab_sec"] / total * 100)
            diskusi   = round(v["diskusi_sec"]     / total * 100)
            tidakAda  = round(v["diam_sec"]        / total * 100)
        else:
            ceramah = tanya = diskusi = tidakAda = 0

        out.append({
            "matkul": v["matkul"],
            "kode_matkul": v["kode_matkul"],
            "kelas": v["kelas"],
            "dosen": v["dosen"],
            "ceramah": ceramah,
            "tanya": tanya,
            "diskusi": diskusi,
            "tidakAda": tidakAda,
            "total_pertemuan": v["total_pertemuan"],
        })

    out.sort(key=lambda x: x["total_pertemuan"], reverse=True)
    return out


# =========================================================
# 3. DASHBOARD ALL-IN-ONE
# =========================================================
@router.get("/dashboard")
def get_dashboard(
    dosen: Optional[str] = None,
    kelas: Optional[str] = None,
    range_days: int = 30,
):
    return {
        "summary":    get_summary(dosen, kelas, range_days),
        "per_matkul": get_per_matkul(dosen, kelas, range_days),
    }


# =========================================================
# 4. TREND (LineChart Tren Aktivitas Pembelajaran)
# =========================================================
@router.get("/months")
def get_available_months():
    """Daftar bulan yang punya data activity_stats, urut terbaru ke lama."""
    rows = supabase.table("activity_stats").select("tanggal").execute().data or []

    month_set: set[tuple[int, int]] = set()
    for r in rows:
        t = r.get("tanggal")
        if not t:
            continue
        d = date.fromisoformat(t)
        month_set.add((d.year, d.month))

    sorted_months = sorted(month_set, reverse=True)
    return [f"{BULAN_ID[m - 1]} {y}" for y, m in sorted_months]


@router.get("/trend")
def get_trend(
    month: str = Query(..., description="Format: 'Mei 2026'"),
):
    """Agregat aktivitas pembelajaran per hari (SEN–SAB) untuk bulan tertentu.
    Persentase dihitung weighted by detik (lebih akurat saat banyak pertemuan)."""
    try:
        year, m = _parse_month(month)
    except (ValueError, IndexError):
        return {"month": month, "days": []}

    start = date(year, m, 1)
    end   = date(year, m, monthrange(year, m)[1])

    rows = (
        supabase.table("activity_stats")
        .select("tanggal, ceramah_sec, tanya_jawab_sec, diskusi_sec, diam_sec")
        .gte("tanggal", str(start))
        .lte("tanggal", str(end))
        .execute()
        .data
        or []
    )

    # bucket per hari (SEN–SAB, tanpa MIN biar konsisten dgn UI lama)
    bucket = {d: {"c": 0.0, "t": 0.0, "ds": 0.0, "dm": 0.0} for d in DAYS_ID}

    for r in rows:
        t = r.get("tanggal")
        if not t:
            continue
        d = date.fromisoformat(t)
        key = DAYS_ID[d.weekday()]
        bucket[key]["c"]  += r.get("ceramah_sec")     or 0
        bucket[key]["t"]  += r.get("tanya_jawab_sec") or 0
        bucket[key]["ds"] += r.get("diskusi_sec")     or 0
        bucket[key]["dm"] += r.get("diam_sec")        or 0

    days_out = []
    for d in DAYS_ID[:6]:   # SEN s/d SAB
        b = bucket[d]
        total = b["c"] + b["t"] + b["ds"] + b["dm"]
        if total > 0:
            days_out.append({
                "name":       d,
                "ceramah":    round(b["c"]  / total * 100),
                "tanyaJawab": round(b["t"]  / total * 100),
                "diskusi":    round(b["ds"] / total * 100),
                "tidakAda":   round(b["dm"] / total * 100),
            })
        else:
            days_out.append({
                "name": d, "ceramah": 0, "tanyaJawab": 0, "diskusi": 0, "tidakAda": 0,
            })

    return {"month": month, "days": days_out}


# =========================================================
# 5. OPTIONS (dropdown filter)
# =========================================================
@router.get("/options")
def get_options():
    # Untuk aktivitas pakai dosen_utama (kode_dosen string di jadwal_kuliah)
    jadwal = (
        supabase.table("jadwal_kuliah")
        .select("kelas, dosen_utama")
        .execute()
        .data
        or []
    )

    dosen_set = sorted({j["dosen_utama"] for j in jadwal if j.get("dosen_utama")})
    kelas_set = sorted({j["kelas"]       for j in jadwal if j.get("kelas")})

    # enrich dosen dengan nama_lengkap dari tabel dosen
    dosen_master = (
        supabase.table("dosen")
        .select("kode_dosen, nama_lengkap")
        .execute()
        .data
        or []
    )
    nama_map = {d["kode_dosen"]: (d.get("nama_lengkap") or "").strip() for d in dosen_master}

    return {
        "dosen": [{"kode": k, "nama": nama_map.get(k, k)} for k in dosen_set],
        "kelas": kelas_set,
    }
