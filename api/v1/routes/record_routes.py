from fastapi import APIRouter, Depends
from datetime import datetime
from fastapi import HTTPException
from services.recorder.video_recorder import start_video_recording, stop_video_recording
from services.scheduler.scheduler import schedule_recording
from api.v1.deps import require_admin
from fastapi import Query

router = APIRouter(prefix="/record", tags=["record"])

@router.post("/start-record")
# yang lama tanpa pengecekan apakah kamera sedang digunakan atau tidak, sehingga bisa menyebabkan error jika ada proses lain yang menggunakan kamera
# def start_record():

#     return start_video_recording()

def start_record(
    hari: str = Query(...),
    jam_mulai: str = Query(...),
    ruangan: str = Query(...),
    kode_mata_kuliah: str = Query(...),
    dosen_utama: str = Query(...),
    kelas: str = Query(...),
    user: dict = Depends(require_admin)
):
    date_now = datetime.now().strftime("%Y%m%d")

    nama_file = (
        f"{hari}_"
        f"{jam_mulai.replace(':','')}_"
        f"{ruangan}_"
        f"{kode_mata_kuliah}_"
        f"{dosen_utama}_"
        f"{kelas}_"
        f"{date_now}"
    )

    result = start_video_recording(nama_file)

    if result["status"] == "error":
        raise HTTPException(status_code=503, detail=result["message"])

    if result["status"] == "warning":
        raise HTTPException(status_code=424, detail=result["message"])

    return result

@router.post("/stop-record")
# yang lama tanpa pengecekan apakah ada recording yang aktif atau tidak, sehingga bisa menyebabkan error jika mencoba menghentikan proses ffmpeg yang tidak ada
# def stop_record():
#     return stop_video_recording()

def stop_record(user: dict = Depends(require_admin)):

    result = stop_video_recording()

    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])

    return result

@router.post("/schedule-record")
def schedule_record(start_time: str, stop_time: str, user: dict = Depends(require_admin)):

    start_datetime = datetime.fromisoformat(start_time)
    stop_datetime = datetime.fromisoformat(stop_time)

    return schedule_recording(start_datetime, stop_datetime)