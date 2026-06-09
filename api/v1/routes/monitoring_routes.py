from fastapi import APIRouter, Depends, Query
from services.storage.gdrive_video import list_videos, list_audios
from utils.metadata_parser import parse_filename_monitoring
from repositories.supabase_client import supabase
from repositories.cache import get_all_jadwal
from api.v1.deps import require_admin, require_authenticated, optional_authenticated
from core.logger import logger
from core.matkul_filter import is_matkul_blacklisted
from typing import Optional
import time
import os

MONITORING_FOLDER_ID = os.getenv("MONITORING_FOLDER_ID")

last_scan_time = 0

router = APIRouter(tags=["Monitoring"])


# =========================================================
# DERIVASI KEHADIRAN & AKTIVITAS (tidak hardcode)
# Sumber asli:
#   • kehadiran  → tabel rec_session   (link: jadwal_id + tanggal)
#   • aktivitas  → tabel activity_stats (link: kode_matkul + tanggal)
# =========================================================
KEHADIRAN_LABEL = {
    "tepat_waktu": "Tepat Waktu",
    "terlambat":   "Terlambat",
}

AKTIVITAS_LABEL = {
    "ceramah":     "Ceramah",
    "tanya_jawab": "Tanya Jawab",
    "diskusi":     "Diskusi",
    "diam":        "Tidak Ada",
}


def format_kehadiran(raw):
    """rec_session.kehadiran ('tepat_waktu') → 'Tepat Waktu'. None kalau kosong."""
    if not raw:
        return None
    return KEHADIRAN_LABEL.get(str(raw).lower(), str(raw))


def format_aktivitas(raw):
    """activity_stats.dominant_activity ('CERAMAH') → 'Ceramah'. None kalau kosong."""
    if not raw:
        return None
    return AKTIVITAS_LABEL.get(str(raw).lower(), str(raw).title())


def build_kehadiran_index():
    """Index kehadiran dari rec_session, key = (jadwal_id, tanggal).
    Order by id → baris terbaru menimpa yang lama."""
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
    return idx


def build_activity_index():
    """Index activity_stats, key = (kode_matkul, tanggal).
    Simpan seluruh baris agar breakdown persen bisa dipakai juga."""
    rows = (
        supabase.table("activity_stats")
        .select("kode_matkul, tanggal, dominant_activity, "
                "ceramah_pct, tanya_jawab_pct, diskusi_pct, diam_pct, pertemuan_ke")
        .order("id")
        .execute()
        .data or []
    )
    idx = {}
    for r in rows:
        if r.get("kode_matkul") and r.get("tanggal"):
            idx[(r["kode_matkul"], r["tanggal"])] = r
    return idx

@router.post("/monitoring/scan-drive")
def scan_drive(user: dict = Depends(optional_authenticated)):

    global last_scan_time

    if time.time() - last_scan_time < 60:
        return {
            "status": "skip",
            "new_data": False
        }

    last_scan_time = time.time()

    FOLDER_ID = MONITORING_FOLDER_ID

    # =========================
    # SCAN VIDEO & AUDIO
    # =========================
    videos = list_videos(FOLDER_ID)
    audios = list_audios(FOLDER_ID)

    inserted = []

    # =========================
    # AUDIO MAP
    # key = base filename
    # =========================
    audio_map = {}

    for audio in audios:
        base_name = audio["name"].rsplit(".", 1)[0]
        audio_map[base_name] = {
            "id": audio["id"],
            "name": audio["name"]
        }

    # =========================
    # PRE-FETCH (1 query) — set semua video_file_id yang sudah ada di DB
    # Menggantikan N query existence-check di dalam loop
    # =========================
    existing_res = supabase.table("monitoring").select("video_file_id").execute()
    existing_ids = {r["video_file_id"] for r in (existing_res.data or [])}

    # =========================
    # JADWAL INDEX (0 query) — dari cache, di-index per (kode_matkul, dosen, kelas)
    # Menggantikan N query find_jadwal() di dalam loop
    # =========================
    jadwal_list  = get_all_jadwal()
    jadwal_index = {
        (j["kode_mata_kuliah"], j["dosen_utama"], j["kelas"]): j
        for j in jadwal_list
        if (
            j.get("kode_mata_kuliah") and j.get("dosen_utama") and j.get("kelas")
            and not is_matkul_blacklisted(j.get("mata_kuliah", ""))
        )
    }

    # =========================
    # LOOP VIDEO
    # =========================
    skipped_parse  = []
    skipped_jadwal = []
    rows_to_insert = []   # kumpulkan dulu, batch insert setelah loop

    for video in videos:

        parsed = parse_filename_monitoring(video["name"])

        if not parsed:
            skipped_parse.append(video["name"])
            logger.warning(f"[SCAN] Gagal parse filename: {video['name']}")
            continue

        # Dict lookup O(1) — tidak ada query ke DB
        jadwal = jadwal_index.get((
            parsed["kode_matkul"],
            parsed["kode_dosen"],
            parsed["kelas"],
        ))

        if not jadwal:
            skipped_jadwal.append({
                "file":        video["name"],
                "kode_matkul": parsed["kode_matkul"],
                "kode_dosen":  parsed["kode_dosen"],
                "kelas":       parsed["kelas"],
            })
            logger.warning(
                f"[SCAN] Jadwal tidak ditemukan: "
                f"kode_matkul={parsed['kode_matkul']} "
                f"kode_dosen={parsed['kode_dosen']} "
                f"kelas={parsed['kelas']}"
            )
            continue

        # =========================
        # BASE FILENAME
        # =========================
        base_filename = video["name"].rsplit(".", 1)[0]

        # =========================
        # CHECK EXIST — set lookup O(1), tidak ada query ke DB
        # =========================
        if video["id"] in existing_ids:
            continue

        # =========================
        # FIND MATCH AUDIO
        # =========================
        audio_id = audio_map.get(base_filename, {}).get("id")

        # =========================
        # VIDEO URL
        # =========================
        video_url = f"https://drive.google.com/file/d/{video['id']}/preview"

        # =========================
        # KUMPULKAN UNTUK BATCH INSERT
        # CATATAN: kehadiran & aktivitas_dominan TIDAK diisi di sini.
        # Keduanya diturunkan saat dibaca dari rec_session / activity_stats,
        # sehingga selalu mencerminkan data terkini.
        # =========================
        rows_to_insert.append({
            "jadwal_id":      jadwal["id"],
            "tanggal":        str(parsed["tanggal"]),
            "video_url":      video_url,
            "video_file_id":  video["id"],
            "audio_file_id":  audio_id,
            "base_filename":  base_filename,
        })

        inserted.append(video["name"])

    # =========================
    # BATCH INSERT (1 query) — menggantikan N query INSERT di dalam loop
    # =========================
    if rows_to_insert:
        supabase.table("monitoring").insert(rows_to_insert).execute()

    logger.info(
        f"[SCAN] Selesai | inserted={len(inserted)} "
        f"skip_parse={len(skipped_parse)} "
        f"skip_jadwal={len(skipped_jadwal)}"
    )

    return {
        "status":           "scan selesai",
        "videos_found":     len(videos),
        "videos_matched":   inserted,
        "new_data":         len(inserted) > 0,
        "skipped_parse":    skipped_parse,
        "skipped_jadwal":   skipped_jadwal,
    }
    
@router.get("/monitoring")
def get_monitoring(
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
    user: dict = Depends(optional_authenticated),
):
    if tahun_ajaran_id:
        # !inner → hanya monitoring yang punya jadwal, lalu filter TA-nya
        monitoring = (
            supabase.table("monitoring")
            .select("*, jadwal_kuliah!inner(*)")
            .eq("jadwal_kuliah.tahun_ajaran_id", tahun_ajaran_id)
            .execute()
        )
        logger.info(f"[MONITORING] get_monitoring filtered by tahun_ajaran_id={tahun_ajaran_id}")
    else:
        monitoring = supabase.table("monitoring").select("*, jadwal_kuliah(*)").execute()

    # Index sumber data asli (masing-masing 1 query) untuk derivasi non-hardcode
    kehadiran_idx = build_kehadiran_index()
    activity_idx  = build_activity_index()

    data = []

    for item in monitoring.data:

        j = item.get("jadwal_kuliah") or {}
        nama_matkul = j.get("mata_kuliah", "")

        # Sembunyikan matkul yang diblacklist (praktikum, studium general)
        if is_matkul_blacklisted(nama_matkul):
            continue

        # ── Kehadiran: dari rec_session via (jadwal_id, tanggal) ──
        kehadiran_raw = kehadiran_idx.get((item.get("jadwal_id"), item.get("tanggal")))

        # ── Aktivitas: dari activity_stats via (kode_matkul, tanggal) ──
        act = activity_idx.get((j.get("kode_mata_kuliah"), item.get("tanggal")))
        aktivitas_raw = act.get("dominant_activity") if act else None

        data.append({
            "id":            item["id"],
            "tanggal":       item["tanggal"],
            "jam":           f"{j.get('jam_mulai', '')} - {j.get('jam_selesai', '')}",
            "ruangan":       j.get("ruangan", ""),
            "matkul":        nama_matkul,
            "kode":          j.get("kode_mata_kuliah", ""),
            "kodeDosen":     j.get("dosen_utama", ""),
            "kehadiran":     format_kehadiran(kehadiran_raw) or "-",
            "aktivitas":     format_aktivitas(aktivitas_raw) or "-",
            "kelas":         j.get("kelas", ""),
            "video_url":     item.get("video_url"),
            "audio_file_id": item.get("audio_file_id"),
            "base_filename": item.get("base_filename"),
        })

    return data

@router.get("/{monitoring_id}")
def get_monitoring_detail(monitoring_id: int, user: dict = Depends(optional_authenticated)):

    response = (
        supabase.table("monitoring")
        .select("*, jadwal_kuliah(*)")
        .eq("id", monitoring_id)
        .single()
        .execute()
    )

    m = response.data
    if not m:
        return m

    j = m.get("jadwal_kuliah") or {}

    # ── Kehadiran: dari rec_session (jadwal_id + tanggal) ──
    keh = (
        supabase.table("rec_session")
        .select("kehadiran")
        .eq("jadwal_id", m.get("jadwal_id"))
        .eq("tanggal", m.get("tanggal"))
        .order("id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    kehadiran_raw = keh[0]["kehadiran"] if keh else None
    m["kehadiran"] = format_kehadiran(kehadiran_raw) or "-"

    # ── Aktivitas: dari activity_stats (kode_matkul + tanggal) ──
    act = (
        supabase.table("activity_stats")
        .select("dominant_activity, ceramah_pct, tanya_jawab_pct, "
                "diskusi_pct, diam_pct, pertemuan_ke")
        .eq("kode_matkul", j.get("kode_mata_kuliah"))
        .eq("tanggal", m.get("tanggal"))
        .order("id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if act:
        stats = act[0]
        m["activity_stats"]    = stats
        m["aktivitas_dominan"] = format_aktivitas(stats.get("dominant_activity")) or "-"
        m["pertemuan_ke"]      = stats.get("pertemuan_ke")
    else:
        m["activity_stats"]    = None
        m["aktivitas_dominan"] = "-"

    return m