# services/chunker.py

import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

CHUNK_DURATION_SEC = int(os.getenv("CHUNK_DURATION_SEC", "300"))


def split_audio(file_path, chunk_duration=None, report_id=None):
    if chunk_duration is None:
        chunk_duration = CHUNK_DURATION_SEC


    output_dir = f"chunks/{report_id}" if report_id else "chunks"
    os.makedirs(output_dir, exist_ok=True)

    output_pattern = os.path.join(output_dir, "chunk_%03d.wav")

    command = [
        "ffmpeg",
        "-i", file_path,
        "-f", "segment",
        "-segment_time", str(chunk_duration),
        "-c", "copy",
        output_pattern
    ]

    subprocess.run(command, check=True)

    return sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.endswith(".wav")
    ])