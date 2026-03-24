# import subprocess
# import os
# import time
# from datetime import datetime
# from dotenv import load_dotenv
# from utilities.google_drive import upload_to_drive

# load_dotenv()

# rtsp_url = os.getenv("RTSP_URL")

# process = None
# current_file = None


# def start_recording():
#     global process, current_file

#     current_file = f"record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"

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

#     return {"status": "recording started", "file": current_file}


# def stop_recording():
#     global process, current_file

#     if process:

#         process.stdin.write(b"q")
#         process.stdin.flush()
#         process.wait()

#         file_path = current_file

#         file_id = upload_to_drive(file_path)

#         os.remove(file_path)

#         process = None
#         current_file = None

#         return {
#             "status": "recording stopped",
#             "google_drive_file_id": file_id
#         }

#     return {"status": "no active recording"}


import subprocess
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from utilities.google_drive import upload_to_drive

load_dotenv()

rtsp_url = os.getenv("RTSP_URL")

process = None
current_file = None
start_time = None


def log(message):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {message}")


def start_recording():
    global process, current_file, start_time

    start_time = datetime.now()

    current_file = f"record_{start_time.strftime('%Y%m%d_%H%M%S')}.mp4"

    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-c", "copy",
        current_file
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE
    )

    log(f"Recording started: {current_file}")

    return {
        "status": "recording started",
        "file": current_file
    }


def stop_recording():
    global process, current_file, start_time

    if process:

        process.stdin.write(b"q")
        process.stdin.flush()
        process.wait()

        end_time = datetime.now()
        duration = end_time - start_time

        log(f"Recording stopped")
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
            "status": "recording stopped",
            "duration": str(duration),
            "google_drive_file_id": file_id
        }

    return {"status": "no active recording"}