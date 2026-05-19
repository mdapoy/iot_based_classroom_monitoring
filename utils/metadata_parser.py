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
    
# def parse_filename_monitoring(filename):

#     pattern = r"(\d{4}-\d{2}-\d{2})(\d{2}-\d{2})([A-Z0-9]+)([A-Z]+)([A-Z0-9\-]+)"

#     match = re.search(pattern, filename)

#     if not match:
#         return None

#     tanggal = datetime.strptime(match.group(1), "%Y-%m-%d").date()
#     jam = match.group(2).replace("-", ":")

#     kode_matkul = match.group(3)
#     kode_dosen = match.group(4)
#     kelas = match.group(5)  # 🔥 sekarang support "TK-47-GAB"

#     return {
#         "tanggal": tanggal,
#         "jam_mulai": jam,
#         "kode_matkul": kode_matkul,
#         "kode_dosen": kode_dosen,
#         "kelas": kelas
#     }

def parse_filename_monitoring(filename):
    """
    Parse nama file monitoring dari Google Drive.

    Format yang didukung (split by '_'):
      5 bagian : tanggal_jam_kode_matkul_kode_dosen_kelas
                 contoh: 2026-04-30_13-30_AZK1GAB3_MFC_TK-48-GAB1.mp4
      6 bagian : tanggal_jam_ruangan_kode_matkul_kode_dosen_kelas
                 contoh: 2026-04-30_13-30_TULT1212_AZK1GAB3_MFC_TK-48-GAB1.mp4
    """
    # hapus extension
    name = (
        filename
        .replace(".wav", "")
        .replace(".mp4", "")
        .replace(".mkv", "")
        .replace(".MOV", "")
        .replace(".mov", "")
    )

    parts = name.split("_")

    try:
        if len(parts) == 5:
            # tanggal _ jam _ kode_matkul _ kode_dosen _ kelas
            tanggal_str, jam, kode_matkul, kode_dosen, kelas = parts
        elif len(parts) == 6:
            # tanggal _ jam _ ruangan _ kode_matkul _ kode_dosen _ kelas
            tanggal_str, jam, _, kode_matkul, kode_dosen, kelas = parts
        else:
            return None

        tanggal = datetime.strptime(tanggal_str, "%Y-%m-%d").date()

        return {
            "tanggal":     tanggal,
            "jam_mulai":   jam.replace("-", ":"),
            "kode_matkul": kode_matkul,
            "kode_dosen":  kode_dosen,
            "kelas":       kelas,
        }

    except (ValueError, TypeError):
        return None