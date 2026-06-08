"""
PDF extractor untuk RPS Telkom University.
Menggunakan pdfplumber -- tanpa Gemini -- karena format template sudah standar.

Kolom yang diekstrak:
  kode_matkul, pertemuan_ke, materi_pembelajaran,
  pengalaman_pembelajaran_mahasiswa

Juga menyediakan helper get_active_tahun_ajaran_id() untuk mengisi
tahun_ajaran_id secara otomatis dari baris is_aktif=true.
"""
import re
import io
import pdfplumber
from repositories.supabase_client import supabase
from core.logger import logger


# ---- Text cleaning ----------------------------------------------------------

def _clean(val) -> str:
    """
    Bersihkan teks hasil ekstraksi PDF dari karakter artifact:
    - Unicode private-use area (U+E000-U+F8FF), font rendering issue
    - Prefix 'O ' (circle icon yang dirender sebagai huruf O)
    - Prefix 'M ' (artifact margin)
    - Karakter bullet diganti '; '
    - Newline diganti spasi
    - Whitespace berlebih di-compress
    """
    if not val:
        return ""
    text = str(val)
    # Hapus unicode private-use area (U+E000 - U+F8FF), font artifact dari PDF
    text = re.sub("[-]", "", text)
    # Ganti newline dengan spasi
    text = text.replace("\n", " ")
    # Hapus prefix 'O ' (PDF artifact circle icon)
    text = re.sub(r"^\s*O\s+", "", text)
    # Hapus prefix 'M ' (artifact)
    text = re.sub(r"^\s*M\s+", "", text)
    # Ganti bullet dengan '; ' untuk readability
    text = text.replace("•", "; ")
    # Compress multiple spaces/semicolons
    text = re.sub(r";\s*;", ";", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Hapus leading/trailing '; '
    text = text.strip("; ").strip()
    return text


# ---- Column swap detection --------------------------------------------------

_PENGALAMAN_MARKERS = re.compile(
    r"Kuliah:|Pemaparan|Diskusi:|Menit\]|direct learning|Final Ques",
    re.IGNORECASE,
)


def _is_pengalaman(text: str) -> bool:
    """True jika teks terlihat seperti kolom Pengalaman (bukan Materi)."""
    return bool(_PENGALAMAN_MARKERS.search(text))


# ---- Active tahun_ajaran ----------------------------------------------------

def get_active_tahun_ajaran_id() -> str | None:
    """
    Ambil UUID tahun_ajaran yang is_aktif = true.
    Return None jika tidak ditemukan (insert tetap bisa jalan, kolom nullable).
    """
    try:
        res = (
            supabase.table("tahun_ajaran")
            .select("id")
            .eq("is_aktif", True)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["id"]
    except Exception as e:
        logger.warning(f"[PDF_EXTRACTOR] Gagal ambil tahun_ajaran aktif: {e}")
    return None


# ---- Core extractor ---------------------------------------------------------

def extract_rps_from_pdf(file_bytes: bytes) -> dict:
    """
    Ekstrak data RPS dari bytes file PDF.

    Returns dict:
    {
        "kode_matkul": str | None,
        "nama_matkul": str | None,
        "tahun_ajaran_id": str | None,   # dari DB (is_aktif=true)
        "rows": [
            {
                "pertemuan_ke": int,
                "materi_pembelajaran": str,
                "pengalaman_pembelajaran_mahasiswa": str,
            },
            ...
        ],
        "warnings": [str],   # peringatan kualitas data (swap, dll.)
    }
    """
    kode_matkul = None
    nama_matkul = None
    rows        = []
    warnings    = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()

            for tbl in (tables or []):
                if not tbl:
                    continue

                # -- Deteksi tabel info matkul (3 kolom: Field | : | Value) --
                if len(tbl[0]) == 3:
                    for row in tbl:
                        if not row or not row[0]:
                            continue
                        label = str(row[0]).strip()
                        value = _clean(row[2]) if row[2] else ""

                        if label == "Matakuliah":
                            nama_matkul = value
                        elif "Kode Mata Kuliah" in label:
                            kode_matkul = value

                # -- Deteksi tabel utama RPS (>=8 kolom, header "Minggu...") --
                if len(tbl[0]) < 8:
                    continue

                col0_hdr = _clean(tbl[0][0]) if tbl[0][0] else ""
                col5_hdr = _clean(tbl[0][5]) if len(tbl[0]) > 5 and tbl[0][5] else ""

                is_rps_table = (
                    "Minggu" in col0_hdr
                    or "Pertemuan" in col0_hdr
                    or "Materi" in col5_hdr
                )
                if not is_rps_table:
                    continue

                # Lewati 2 baris header (header utama + sub-header)
                for row in tbl[2:]:
                    if len(row) < 8:
                        continue

                    minggu_raw = _clean(row[0]) if row[0] else ""

                    # Hanya proses baris dengan format "X-Y" (e.g. "1-1", "14-1")
                    m = re.match(r"^(\d+)-\d+$", minggu_raw)
                    if not m:
                        continue   # skip separator "CLO X CLO..." atau baris kosong

                    pertemuan_ke = int(m.group(1))
                    materi       = _clean(row[5]) if row[5] else ""
                    pengalaman   = _clean(row[7]) if row[7] else ""

                    # -- Deteksi & perbaiki swap kolom (PDF artifact) ----------
                    # Materi sejati TIDAK pernah mengandung "Kuliah:" atau "Pemaparan"
                    # (itu ciri khas kolom Pengalaman). Kondisi ini cukup untuk swap.
                    if re.search(r"Kuliah:|Pemaparan", materi, re.IGNORECASE):
                        warnings.append(
                            f"Pertemuan {pertemuan_ke}: kolom Materi dan Pengalaman "
                            f"tertukar di PDF -- diperbaiki otomatis."
                        )
                        materi, pengalaman = pengalaman, materi

                    rows.append({
                        "pertemuan_ke":                      pertemuan_ke,
                        "materi_pembelajaran":               materi,
                        "pengalaman_pembelajaran_mahasiswa": pengalaman,
                    })

    # Deduplikasi: jika satu pertemuan muncul lebih dari sekali, ambil yang pertama
    seen        = set()
    unique_rows = []
    for r in rows:
        pk = r["pertemuan_ke"]
        if pk not in seen:
            seen.add(pk)
            unique_rows.append(r)

    unique_rows.sort(key=lambda r: r["pertemuan_ke"])

    # Ambil tahun_ajaran_id aktif dari DB
    tahun_ajaran_id = get_active_tahun_ajaran_id()

    if not kode_matkul:
        warnings.append("kode_matkul tidak ditemukan di PDF. Harap isi manual.")
    if not unique_rows:
        warnings.append("Tidak ada baris pertemuan yang berhasil diekstrak.")

    logger.info(
        f"[PDF_EXTRACTOR] kode_matkul={kode_matkul} "
        f"rows={len(unique_rows)} warnings={len(warnings)}"
    )

    return {
        "kode_matkul":     kode_matkul,
        "nama_matkul":     nama_matkul,
        "tahun_ajaran_id": tahun_ajaran_id,
        "rows":            unique_rows,
        "warnings":        warnings,
    }
