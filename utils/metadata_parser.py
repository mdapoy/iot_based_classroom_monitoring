import re
from datetime import datetime

def parse_filename(filename: str):
    try:
        name = filename.replace(".wav", "").replace(".mp4", "")
        parts = name.split("_")

        if len(parts) != 6:
            raise ValueError("Format filename tidak sesuai")

        return {
            "tanggal": parts[0],
            "jam": parts[1],
            "ruangan": parts[2],
            "kode_matkul": parts[3],
            "kode_dosen": parts[4],
            "kelas": parts[5],
        }

    except Exception as e:
        raise ValueError(f"Gagal parsing filename: {str(e)}")
    
def parse_filename_monitoring(filename):

    pattern = r"(\d{4}-\d{2}-\d{2})(\d{2}-\d{2})([A-Z0-9]+)([A-Z]+)([A-Z0-9\-]+)"

    match = re.search(pattern, filename)

    if not match:
        return None

    tanggal = datetime.strptime(match.group(1), "%Y-%m-%d").date()
    jam = match.group(2).replace("-", ":")

    kode_matkul = match.group(3)
    kode_dosen = match.group(4)
    kelas = match.group(5)  # 🔥 sekarang support "TK-47-GAB"

    return {
        "tanggal": tanggal,
        "jam_mulai": jam,
        "kode_matkul": kode_matkul,
        "kode_dosen": kode_dosen,
        "kelas": kelas
    }