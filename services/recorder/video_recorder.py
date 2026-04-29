import subprocess
import os
import time
import threading
from datetime import datetime
from dotenv import load_dotenv
from services.storage.google_drive import upload_to_drive #perubahan nama import dari utilities ke services
from repositories.supabase_client import supabase

load_dotenv()

rtsp_url = os.getenv("RTSP_URL")

process = None
current_file = None
start_time = None
frame_received = False #baru, untuk cek apakah frame sudah diterima dari kamera, agar tidak langsung berhenti saat start video recorder karena belum ada frame yang diterima



def log(message):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {message}")


# def start_video_recording():

#     global process, current_file, start_time

#     start_time = datetime.now()

#     current_file = f"record_{start_time.strftime('%Y%m%d_%H%M%S')}.mp4"

#     cmd = [
#         "ffmpeg",
#         "-rtsp_transport", "tcp",
#         "-i", rtsp_url,
#         "-c", "copy",
#         current_file
#     ]

#     process = subprocess.Popen(
#         cmd,
#         stdin=subprocess.PIPE
#     )

#     log(f"Recording started: {current_file}")

#     return {
#         "status": "recording started",
#         "file": current_file
#     }

def start_video_recording(nama_file = None): #baru, dengan pengecekan kamera dan monitoring ffmpeg
    print("Start recording:", nama_file)
    global process, current_file, start_time, frame_received

    if process and process.poll() is None:
        return {"status": "error", "message": "recording already running"}

    log("Checking camera connection...")

    ok, msg = check_camera(rtsp_url)

    if not ok:
        log(f"Camera error: {msg}")
        return {
            "status": "error",
            "message": f"Camera not reachable: {msg}"
        }

    start_time = datetime.now()

    # jika nama_file tidak diberikan baru
    if not nama_file:
        nama_file = f"record_{start_time.strftime('%Y%m%d_%H%M%S')}"

    current_file = f"{nama_file}.mp4"

    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-c", "copy",
        "-f", "mp4",
        current_file
    ]

    try:

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        frame_received = False

        thread = threading.Thread(
            target=monitor_ffmpeg,
            daemon=True
        )

        thread.start()

        time.sleep(5)

        # cek apakah ffmpeg langsung mati
        if process.poll() is not None:

            error = process.stderr.read().decode()

            log("FFMPEG failed to start")
            log(error)

            process = None

            return {
                "status": "error",
                "message": "ffmpeg failed to start",
                "ffmpeg_error": error
            }

        # cek apakah frame masuk
        if not frame_received:

            log("WARNING: ffmpeg started but no frames received")

            return {
                "status": "warning",
                "message": "ffmpeg started but no video frames detected"
            }

        log(f"Recording started: {current_file}")

        return {
            "status": "success",
            "message": "recording started",
            "file": current_file
        }

    except Exception as e:

        log(f"Exception starting ffmpeg: {e}")

        return {
            "status": "error",
            "message": str(e)
        }

# def stop_video_recording():
#     global process, current_file, start_time

#     if process:

#         process.stdin.write(b"q")
#         process.stdin.flush()
#         process.wait()

#         end_time = datetime.now()
#         duration = end_time - start_time

#         log(f"Recording stopped")
#         log(f"Recording duration: {duration}")

#         file_path = current_file

#         log("Uploading to Google Drive...")

#         file_id = upload_to_drive(file_path)

#         log("Upload finished")

#         os.remove(file_path)

#         process = None
#         current_file = None
#         start_time = None

#         return {
#             "status": "recording stopped",
#             "duration": str(duration),
#             "google_drive_file_id": file_id
#         }

#     return {"status": "no active recording"}

def stop_video_recording():#baru, dengan pengecekan apakah ada recording yang aktif sebelum mencoba menghentikan proses ffmpeg

    global process, current_file, start_time

    if not process:
        return {"status": "error", "message": "no active recording"}
    
    if process.poll() is not None:

        log("FFmpeg process already stopped")

        process = None

        return {
            "status": "error",
            "message": "recording process already stopped"
        }

    process.stdin.write(b"q")
    process.stdin.flush()

    process.wait()

    end_time = datetime.now()
    duration = end_time - start_time

    log("Recording stopped")
    log(f"Recording duration: {duration}")

    file_path = current_file

    log("Uploading to Google Drive...")

    file_id = upload_to_drive(file_path)

    log("Upload finished")

    os.remove(file_path)

    process = None
    current_file = None
    start_time = None

    return {
        "status": "success",
        "message": "recording stopped",
        "duration": str(duration),
        "google_drive_file_id": file_id
    }

def check_camera(rtsp_url):

    cmd = [
        "ffprobe",
        "-v", "error",
        "-rtsp_transport", "tcp",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        rtsp_url
    ]

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        )

        if result.returncode != 0:
            return False, result.stderr.decode()

        return True, "camera reachable"

    except subprocess.TimeoutExpired:
        return False, "RTSP connection timeout"

    except Exception as e:
        return False, str(e)
    
def monitor_ffmpeg():

    global process
    global frame_received

    while True:

        if process is None:
            break

        line = process.stderr.readline()

        if not line:
            break

        text = line.decode("utf-8", errors="ignore")

        log(text.strip())

        if "frame=" in text:
            frame_received = True