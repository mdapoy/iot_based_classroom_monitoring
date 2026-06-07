"""
core/matkul_filter.py
─────────────────────
Blacklist mata kuliah yang dikecualikan dari monitoring & laporan evaluasi.

Matkul yang diblacklist:
  • Semua kelas Praktikum   — tidak ada rekaman kelas yang bisa dimonitor
  • Studium General         — bukan perkuliahan reguler

Cara kerja: keyword-based (case-insensitive) pada nama_matkul.
Tambahkan keyword baru ke BLACKLIST_KEYWORDS untuk memperluas filter.
"""

from core.logger import logger

# Keyword yang dicocokkan ke nama_matkul (lowercase, partial match)
BLACKLIST_KEYWORDS: list[str] = [
    "praktikum",
    "studium general",
    "stadium general",   # typo umum
]


def is_matkul_blacklisted(nama_matkul: str) -> bool:
    """
    Return True jika matkul harus dikecualikan dari monitoring & evaluasi.

    Contoh yang akan diblacklist:
      - "Praktikum Fisika 1"         → True  (keyword: praktikum)
      - "Praktikum Jaringan Komputer" → True  (keyword: praktikum)
      - "Studium General"             → True  (keyword: studium general)
      - "Fisika 2"                    → False
      - "Pemrograman Web"             → False
    """
    lower = (nama_matkul or "").lower()
    for kw in BLACKLIST_KEYWORDS:
        if kw in lower:
            return True
    return False


def filter_matkul_list(
    jadwal_list: list[dict],
    nama_field: str = "mata_kuliah",
) -> list[dict]:
    """
    Filter list jadwal / matkul — buang yang blacklisted.

    Args:
        jadwal_list : list of dict, masing-masing punya field nama_field
        nama_field  : nama kolom yang berisi nama mata kuliah (default: 'mata_kuliah')

    Returns:
        List yang sudah difilter, tanpa matkul blacklisted.
    """
    result = []
    for item in jadwal_list:
        nama = item.get(nama_field, "")
        if is_matkul_blacklisted(nama):
            logger.debug(f"[MATKUL_FILTER] Blacklisted: '{nama}'")
            continue
        result.append(item)
    return result
