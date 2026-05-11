# services/chunker.py

import os
import subprocess

def split_audio(file_path, chunk_duration=600):

    output_dir = "chunks"
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
    ])