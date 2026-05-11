from fastapi import APIRouter
from services.storage.gdrive_video import list_videos, list_audios
from utils.metadata_parser import parse_filename_monitoring
from services.scheduler.scheduler import find_jadwal
from repositories.supabase_client import supabase
import time

last_scan_time = 0

router = APIRouter(tags=["Monitoring"])

@router.post("/monitoring/scan-drive")
def scan_drive():

    global last_scan_time

    if time.time() - last_scan_time < 60:
        return {
            "status": "skip",
            "new_data": False
        }

    last_scan_time = time.time()

    FOLDER_ID = "1lF7NDuXOYUwBLNtfYpaOAvxWA44AqIJ-"

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
    # LOOP VIDEO
    # =========================
    for video in videos:

        parsed = parse_filename_monitoring(video["name"])

        if not parsed:
            continue

        jadwal = find_jadwal(
            parsed["kode_matkul"],
            parsed["kode_dosen"],
            parsed["kelas"]
        )

        if not jadwal:
            continue

        # =========================
        # BASE FILENAME
        # =========================
        base_filename = video["name"].rsplit(".", 1)[0]

        # =========================
        # FIND MATCH AUDIO
        # =========================
        matched_audio = audio_map.get(base_filename)

        audio_id = None

        if matched_audio:
            audio_id = matched_audio["id"]

        # =========================
        # VIDEO URL
        # =========================
        video_url = f"https://drive.google.com/file/d/{video['id']}/preview"

        # =========================
        # CHECK EXIST
        # =========================
        exist = (
            supabase
            .table("monitoring")
            .select("*")
            .eq("video_file_id", video["id"])
            .execute()
        )

        if len(exist.data) > 0:
            continue

        # =========================
        # INSERT
        # =========================
        supabase.table("monitoring").insert({

            "jadwal_id": jadwal["id"],
            "tanggal": str(parsed["tanggal"]),

            "kehadiran": "Tepat Waktu",
            "aktivitas_dominan": "Ceramah",

            "video_url": video_url,
            "video_file_id": video["id"],

            "audio_file_id": audio_id,

            "base_filename": base_filename

        }).execute()

        inserted.append(video["name"])

    return {
        "status": "scan selesai",
        "videos_matched": inserted,
        "new_data": len(inserted) > 0
    }
    
@router.get("/monitoring")
def get_monitoring():

    monitoring = supabase.table("monitoring").select("*").execute()

    data = []

    for item in monitoring.data:

        jadwal = (
            supabase
            .table("jadwal_kuliah")
            .select("*")
            .eq("id", item["jadwal_id"])
            .single()
            .execute()
        )

        j = jadwal.data

        data.append({
            "id": item["id"],
            "tanggal": item["tanggal"],
            "jam": f"{j['jam_mulai']} - {j['jam_selesai']}",
            "ruangan": j["ruangan"],
            "matkul": j["mata_kuliah"],
            "kode": j["kode_mata_kuliah"],
            "kodeDosen": j["dosen_utama"],
            "kehadiran": item["kehadiran"],
            "aktivitas": item["aktivitas_dominan"],
            "kelas": j["kelas"],
        })

    return data

@router.get("/{monitoring_id}")
def get_monitoring_detail(monitoring_id: int):

    response = (
        supabase.table("monitoring")
        .select("*, jadwal_kuliah(*)")
        .eq("id", monitoring_id)
        .single()
        .execute()
    )

    return response.data