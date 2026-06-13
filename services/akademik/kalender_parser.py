"""
Parser untuk PDF Kalender Akademik Universitas Telkom.

Ekstrak per semester:
  - semester_start_date : tanggal mulai perkuliahan
  - skip_dates          : Senin dari minggu FS (tidak dihitung sbg pertemuan)

Format PDF yang didukung: layout tabel per bulan dengan baris
  "Minggu Kuliah N1 N2 FS N3 …" dan baris "Senin D1 D2 D3 …"
"""

import io
import re
from datetime import date, timedelta

import pdfplumber
from core.logger import logger

# ── Kamus bulan Indonesia → angka ───────────────────────────────────────────
_BULAN = {
    "JANUARI": 1,  "FEBRUARI": 2,  "MARET": 3,   "APRIL": 4,
    "MEI": 5,       "JUNI": 6,       "JULI": 7,    "AGUSTUS": 8,
    "SEPTEMBER": 9, "OKTOBER": 10,   "NOVEMBER": 11, "DESEMBER": 12,
}

# Regex: "SEPTEMBER 2025" / "FEBRUARI 2026" dll
_RE_BULAN_TAHUN = re.compile(
    r'\b(' + '|'.join(_BULAN) + r')\s+(\d{4})\b'
)

# Regex: "15 - … Minggu Perkuliahan 2025-1"
# Ellipsis bisa berupa … (u2026) atau "..."
_RE_MULAI = re.compile(
    r'(\d{1,2})\s*[-–]\s*[.…]+\s*Minggu\s+Perkuliahan\s+(\d{4})-(\d)',
    re.IGNORECASE,
)

# Regex: baris "Minggu Kuliah ..." yang mengandung "FS"
_RE_MK_FS = re.compile(r'Minggu\s+Kuliah\s+(.*)', re.IGNORECASE)

# Regex: baris "Senin D1 D2 D3 ..."
_RE_SENIN = re.compile(r'^Senin\s+([\d\s]+)', re.MULTILINE)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ── Parsing utama ─────────────────────────────────────────────────────────────

def parse_kalender_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Parse PDF kalender akademik.

    Return list dict per semester:
    {
        "kode_semester"      : "2025-2",
        "tahun"              : "2025/2026",
        "semester"           : "genap",
        "semester_start_date": "2026-02-23",
        "skip_dates"         : ["2026-01-05", "2026-03-16", ...],
    }
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]

    full_text = "\n".join(pages_text)

    semester_starts = _extract_semester_starts(full_text)
    fs_mondays      = _extract_fs_mondays(full_text)

    logger.info(
        f"[KALENDER] semester_starts={semester_starts} "
        f"fs_mondays={sorted(str(d) for d in fs_mondays)}"
    )

    result = []
    for kode, start_str in semester_starts.items():
        year_str, sem_num = kode.split("-")
        year     = int(year_str)
        tahun    = f"{year}/{year + 1}"
        semester = "ganjil" if sem_num == "1" else "genap"

        start_date = date.fromisoformat(start_str)
        end_date   = start_date + timedelta(weeks=22)

        # FS weeks yang jatuh dalam rentang semester ini
        skip = sorted(
            d.isoformat()
            for d in fs_mondays
            if start_date <= d <= end_date
        )

        result.append({
            "kode_semester":       kode,
            "tahun":               tahun,
            "semester":            semester,
            "semester_start_date": start_str,
            "skip_dates":          skip,
        })

    return result


# ── Helper: cari tanggal mulai perkuliahan per semester ──────────────────────

def _extract_semester_starts(text: str) -> dict[str, str]:
    """
    Return dict { "2025-1": "2025-09-15", "2025-2": "2026-02-23", … }
    """
    starts: dict[str, str] = {}

    # Pecah teks per blok bulan agar punya konteks tahun
    # Cari semua posisi header bulan
    headers = list(_RE_BULAN_TAHUN.finditer(text))

    for i, m in enumerate(headers):
        bulan_name = m.group(1)
        tahun      = int(m.group(2))
        bulan_num  = _BULAN[bulan_name]

        # Ambil teks dari header ini sampai header berikutnya
        start_pos = m.end()
        end_pos   = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        blok      = text[start_pos:end_pos]

        for mm in _RE_MULAI.finditer(blok):
            day       = int(mm.group(1))
            sem_year  = mm.group(2)   # "2025"
            sem_num   = mm.group(3)   # "1" atau "2"
            kode      = f"{sem_year}-{sem_num}"

            try:
                start_date = date(tahun, bulan_num, day)
                if kode not in starts:
                    starts[kode] = start_date.isoformat()
                    logger.info(
                        f"[KALENDER] Semester {kode} mulai "
                        f"{start_date} (dari blok {bulan_name} {tahun})"
                    )
            except ValueError:
                logger.warning(f"[KALENDER] Tanggal tidak valid: {tahun}-{bulan_num}-{day}")

    return starts


# ── Helper: cari Senin dari minggu FS ────────────────────────────────────────

def _extract_fs_mondays(text: str) -> set[date]:
    """
    Untuk setiap blok bulan yang punya baris "Minggu Kuliah … FS …",
    temukan tanggal Senin dari kolom FS.

    Algoritma:
      - "Minggu Kuliah 2 3 FS 4 5"  → token list = ['2','3','FS','4','5']
        FS ada di posisi 2 (0-indexed)
      - "Senin 2 9 16 23 30" → tanggal-tanggal Senin
        posisi 2 → Senin ke-3 = 16
      - Gabungkan dengan bulan/tahun saat ini → date(2026, 3, 16)
    """
    fs_mondays: set[date] = set()
    headers = list(_RE_BULAN_TAHUN.finditer(text))

    for i, m in enumerate(headers):
        bulan_name = m.group(1)
        tahun      = int(m.group(2))
        bulan_num  = _BULAN[bulan_name]

        start_pos = m.end()
        end_pos   = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        blok      = text[start_pos:end_pos]

        mk_match = _RE_MK_FS.search(blok)
        if not mk_match:
            continue

        mk_content = mk_match.group(1).strip()
        if "FS" not in mk_content.upper():
            continue

        # Tokenize: ambil angka dan "FS"
        tokens = re.findall(r'\d+|FS', mk_content, re.IGNORECASE)
        tokens = [t.upper() for t in tokens]

        fs_positions = [idx for idx, t in enumerate(tokens) if t == "FS"]

        # Ambil semua tanggal Senin dari blok ini
        senin_match = _RE_SENIN.search(blok)
        if not senin_match:
            continue

        senin_days = [int(d) for d in senin_match.group(1).split()]

        for pos in fs_positions:
            if pos < len(senin_days):
                day = senin_days[pos]
                try:
                    d = date(tahun, bulan_num, day)
                    # Pastikan ini memang Senin (konsistensi)
                    d = _monday(d)
                    fs_mondays.add(d)
                    logger.info(
                        f"[KALENDER] FS week: {d} "
                        f"(blok {bulan_name} {tahun}, pos={pos})"
                    )
                except ValueError:
                    logger.warning(
                        f"[KALENDER] Tanggal FS tidak valid: "
                        f"{tahun}-{bulan_num}-{day}"
                    )

    return fs_mondays
