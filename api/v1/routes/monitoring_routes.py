from fastapi import APIRouter, Depends, Query
from services.storage.gdrive_video import list_videos, list_audios
from utils.metadata_parser import parse_filename_monitoring
from repositories.supabase_client import supabase
from repositories.cache import get_all_jadwal
from api.v1.deps import require_admin, require_authenticated, optional_authenticated
from core.logger import logger
from typing import Optional
import time
import os

MONITORING_FOLDER_ID = os.getenv("MONITORING_FOLDER_ID")

last_scan_time = 0

router = APIRouter(tags=["Monitoring"])

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
        if j.get("kode_mata_kuliah") and j.get("dosen_utama") and j.get("kelas")
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
        # =========================
        rows_to_insert.append({
            "jadwal_id":      jadwal["id"],
            "tanggal":        str(parsed["tanggal"]),
            "kehadiran":      "Tepat Waktu",
            "aktivitas_dominan": "Ceramah",
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

    data = []

    for item in monitoring.data:

        j = item.get("jadwal_kuliah") or {}

        data.append({
            "id": item["id"],
            "tanggal": item["tanggal"],
            "jam": f"{j.get('jam_mulai', '')} - {j.get('jam_selesai', '')}",
            "ruangan": j.get("ruangan", ""),
            "matkul": j.get("mata_kuliah", ""),
            "kode": j.get("kode_mata_kuliah", ""),
            "kodeDosen": j.get("dosen_utama", ""),
            "kehadiran": item["kehadiran"],
            "aktivitas": item["aktivitas_dominan"],
            "kelas": j.get("kelas", ""),
            "video_url": item.get("video_url"),
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

    return response.data