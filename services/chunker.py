# services/chunker.py

import os
import subprocess
from dotenv import load_dotenv
from core.logger import logger

load_dotenv()

CHUNK_DURATION_SEC = int(os.getenv("CHUNK_DURATION_SEC", "300"))


def normalize_audio(file_path: str) -> str:
    """
    Normalisasi loudness audio menggunakan ffmpeg loudnorm (EBU R128).
    Target: -16 LUFS (standar speech). Returns path file hasil normalisasi.
    File normalized ditulis ke direktori yang sama dengan suffix _normalized.
    """
    base, ext = os.path.splitext(file_path)
    out_path = f"{base}_normalized{ext}"

    command = [
        "ffmpeg", "-y",
        "-i", file_path,
        "-filter:a", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "48000",
        out_path,
    ]

    logger.info(f"[NORMALIZE] Mulai normalisasi: {os.path.basename(file_path)}")
    subprocess.run(command, check=True, capture_output=True)
    logger.info(f"[NORMALIZE] Selesai -> {os.path.basename(out_path)}")
    return out_path


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