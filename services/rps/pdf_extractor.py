"""
PDF extractor untuk RPS Telkom University.
Menggunakan pdfplumber -- tanpa Gemini -- karena format template standar.

Mendukung 2 format template:
  V1 — Format RTOS (tabel 9 col, header "Minggu dan Pertemuan", nomor "X-Y")
  V2 — Format Kimia (tabel 16-col + 10-col, nomor plain integer)

Kolom yang diekstrak:
  kode_matkul, pertemuan_ke, materi_pembelajaran,
  pengalaman_pembelajaran_mahasiswa
"""
import re
import io
import pdfplumber
from repositories.supabase_client import supabase
from core.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _clean(val) -> str:
    """
    Bersihkan teks hasil ekstraksi PDF:
    - Unicode private-use area (U+E000-U+F8FF) → hapus
    - Newline → spasi
    - Prefix 'O ' / 'M ' (PDF artifact) → hapus
    - Bullet '•' → '; '
    - Whitespace berlebih → compress
    """
    if not val:
        return ""
    text = str(val)
    # Hapus unicode private-use area U+E000-U+F8FF (font artifact)
    text = re.sub(r'[-]', '', text)
    text = text.replace("\n", " ")
    text = re.sub(r"^\s*O\s+", "", text)
    text = re.sub(r"^\s*M\s+", "", text)
    text = text.replace("•", "; ")
    text = re.sub(r";\s*;", ";", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("; ").strip()
    return text


def _dedup_sort(rows: list[dict]) -> list[dict]:
    """Deduplikasi berdasarkan pertemuan_ke (ambil pertama), lalu sort."""
    seen, out = set(), []
    for r in rows:
        pk = r["pertemuan_ke"]
        if pk not in seen:
            seen.add(pk)
            out.append(r)
    out.sort(key=lambda r: r["pertemuan_ke"])
    return out


def get_active_tahun_ajaran_id() -> str | None:
    """Ambil UUID tahun_ajaran is_aktif=true dari DB."""
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


# ─────────────────────────────────────────────────────────────────────────────
# PARSER V1 — Format RTOS
# Template: tabel 9 kolom, header "Minggu dan Pertemuan", nomor "X-Y"
# ─────────────────────────────────────────────────────────────────────────────

def _extract_v1(pdf_bytes: bytes) -> tuple[str | None, str | None, list, list]:
    """
    Parser V1 untuk format template RTOS.

    Ciri-ciri:
    - Tabel info terpisah 3-kolom: [Field, ':', Value]
    - 'Kode Mata Kuliah' sebagai label
    - Tabel RPS ≥8 kolom dengan header 'Minggu dan Pertemuan' di col 0
    - Nomor pertemuan: 'X-Y' (e.g. '1-1', '14-1')
    - Materi di col 5, Pengalaman Tatap Muka di col 7

    Returns: (kode_matkul, nama_matkul, rows, warnings)
    """
    kode_matkul = None
    nama_matkul = None
    rows        = []
    warnings    = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for tbl in (page.extract_tables() or []):
                if not tbl:
                    continue

                # ── Info table: 3 kolom [Field | ':' | Value] ──────────────
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

                # ── RPS table: ≥8 kolom, header "Minggu..." di col 0 ───────
                if len(tbl[0]) < 8:
                    continue

                col0_hdr = _clean(tbl[0][0]) if tbl[0][0] else ""
                col5_hdr = _clean(tbl[0][5]) if len(tbl[0]) > 5 and tbl[0][5] else ""

                if not ("Minggu" in col0_hdr or "Pertemuan" in col0_hdr or "Materi" in col5_hdr):
                    continue

                for row in tbl[2:]:  # skip 2 baris header
                    if len(row) < 8:
                        continue
                    minggu_raw = _clean(row[0]) if row[0] else ""
                    m = re.match(r"^(\d+)-\d+$", minggu_raw)
                    if not m:
                        continue
                    pertemuan_ke = int(m.group(1))
                    materi       = _clean(row[5]) if row[5] else ""
                    pengalaman   = _clean(row[7]) if row[7] else ""

                    # Deteksi & perbaiki swap kolom (PDF artifact)
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

    return kode_matkul, nama_matkul, _dedup_sort(rows), warnings


# ─────────────────────────────────────────────────────────────────────────────
# PARSER V2 — Format Kimia
# Template: tabel 16-col (Hal. 1) + tabel 10-col (Hal. 2+), nomor plain int
# ─────────────────────────────────────────────────────────────────────────────

def _extract_aktivitas_belajar(text: str) -> str:
    """
    Dari kolom 'Metode & Aktivitas Pembelajaran' (V2), ambil hanya
    bagian 'Aktivitas Belajar:' karena itulah pengalaman_pembelajaran_mahasiswa.

    Contoh input:
      "Metode: Discovery Learning... Aktivitas Belajar: Kuliah, Diskusi [BT=3x60']"
    Output:
      "Kuliah, Diskusi"
    """
    m = re.search(r"Aktivitas Belajar\s*:\s*(.+?)(?:\[BT\s*=|\Z)", text, re.IGNORECASE | re.DOTALL)
    if m:
        result = m.group(1).strip()
        # Hapus trailing BT/BM reference jika masih ada
        result = re.sub(r"\[BT\s*=.*", "", result, flags=re.DOTALL).strip()
        result = result.rstrip(",; ").strip()
        return result
    return text


def _extract_v2(pdf_bytes: bytes) -> tuple[str | None, str | None, list, list]:
    """
    Parser V2 untuk format template Kimia.

    Ciri-ciri:
    - Halaman 1: tabel besar 16-kolom
        * Header info: row dengan 'Kode MK' → row berikutnya punya nilainya
        * Header RPS: row dengan 'MINGGU KE-', 'MATERI', 'METODE & AKTIVITAS'
        * Data rows: col 0 = plain integer (1, 2, 3)
    - Halaman 2+: tabel 10-kolom tanpa header, langsung data
        * Col 0 = plain integer, col 5 = Materi, col 6 = Metode+Aktivitas

    Returns: (kode_matkul, nama_matkul, rows, warnings)
    """
    kode_matkul = None
    nama_matkul = None
    rows        = []
    warnings    = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for tbl in (page.extract_tables() or []):
                if not tbl or len(tbl[0]) < 8:
                    continue

                n_cols = len(tbl[0])

                # ── TABEL BESAR (≥12 kolom) — Halaman 1 ─────────────────────
                if n_cols >= 12:
                    # Cari kode_matkul dan nama_matkul
                    for row_idx, row in enumerate(tbl):
                        kode_col = None
                        nama_col = None
                        for ci, cell in enumerate(row):
                            if cell and "Kode MK" in str(cell):
                                kode_col = ci
                            if cell and "Nama MK" in str(cell):
                                nama_col = ci
                        if kode_col is not None and row_idx + 1 < len(tbl):
                            next_row = tbl[row_idx + 1]
                            if kode_col < len(next_row) and next_row[kode_col]:
                                kode_matkul = _clean(next_row[kode_col])
                            if nama_col is not None and nama_col < len(next_row) and next_row[nama_col]:
                                nama_matkul = _clean(next_row[nama_col])
                            break

                    # Cari header row RPS dan kolom Materi / Aktivitas
                    materi_col     = None
                    aktivitas_col  = None
                    header_row_idx = None

                    for row_idx, row in enumerate(tbl):
                        for ci, cell in enumerate(row):
                            if cell and re.search(r"^MATERI$", str(cell).strip(), re.IGNORECASE):
                                materi_col = ci
                            if cell and re.search(r"METODE|AKTIVITAS", str(cell), re.IGNORECASE):
                                aktivitas_col = ci
                        if materi_col is not None and aktivitas_col is not None:
                            header_row_idx = row_idx
                            break

                    if materi_col is None or header_row_idx is None:
                        continue

                    # Proses data rows setelah header
                    for row in tbl[header_row_idx + 1:]:
                        pertemuan_raw = _clean(row[0]) if row[0] else ""
                        m = re.match(r"^(\d+)$", pertemuan_raw)
                        if not m:
                            continue
                        pertemuan_ke = int(m.group(1))
                        materi       = _clean(row[materi_col]) if materi_col < len(row) and row[materi_col] else ""
                        pengalaman   = ""
                        if aktivitas_col is not None and aktivitas_col < len(row) and row[aktivitas_col]:
                            pengalaman = _extract_aktivitas_belajar(_clean(row[aktivitas_col]))

                        rows.append({
                            "pertemuan_ke":                      pertemuan_ke,
                            "materi_pembelajaran":               materi,
                            "pengalaman_pembelajaran_mahasiswa": pengalaman,
                        })

                # ── TABEL MEDIUM (8-11 kolom) — Halaman 2+ ──────────────────
                elif 8 <= n_cols <= 11:
                    # Cek apakah col 0 baris pertama adalah plain integer
                    first_val = _clean(tbl[0][0]) if tbl[0][0] else ""
                    if not re.match(r"^\d+$", first_val):
                        continue  # bukan tabel pertemuan V2

                    for row in tbl:
                        pertemuan_raw = _clean(row[0]) if row[0] else ""
                        m = re.match(r"^(\d+)$", pertemuan_raw)
                        if not m:
                            continue
                        pertemuan_ke = int(m.group(1))
                        materi       = _clean(row[5]) if len(row) > 5 and row[5] else ""
                        pengalaman   = ""
                        if len(row) > 6 and row[6]:
                            pengalaman = _extract_aktivitas_belajar(_clean(row[6]))

                        rows.append({
                            "pertemuan_ke":                      pertemuan_ke,
                            "materi_pembelajaran":               materi,
                            "pengalaman_pembelajaran_mahasiswa": pengalaman,
                        })

    return kode_matkul, nama_matkul, _dedup_sort(rows), warnings


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT — coba V1 dulu, fallback ke V2
# ─────────────────────────────────────────────────────────────────────────────

def extract_rps_from_pdf(file_bytes: bytes) -> dict:
    """
    Ekstrak data RPS dari bytes file PDF.
    Mencoba parser V1 (format RTOS) terlebih dahulu.
    Jika tidak menghasilkan baris, otomatis beralih ke parser V2 (format Kimia).

    Returns dict:
    {
        "kode_matkul":     str | None,
        "nama_matkul":     str | None,
        "tahun_ajaran_id": str | None,
        "parser_version":  "v1" | "v2",
        "rows": [
            {
                "pertemuan_ke": int,
                "materi_pembelajaran": str,
                "pengalaman_pembelajaran_mahasiswa": str,
            },
            ...
        ],
        "warnings": [str],
    }
    """
    warnings     = []
    parser_used  = "v1"

    # ── Coba V1 ──────────────────────────────────────────────────────────────
    kode_matkul, nama_matkul, rows, w1 = _extract_v1(file_bytes)
    warnings.extend(w1)

    # ── Fallback ke V2 jika V1 tidak menemukan baris ─────────────────────────
    if not rows:
        logger.info("[PDF_EXTRACTOR] V1 tidak menemukan baris, mencoba V2...")
        kode_matkul, nama_matkul, rows, w2 = _extract_v2(file_bytes)
        warnings.extend(w2)
        parser_used = "v2"
        if rows:
            warnings.insert(0, "Format PDF dikenali sebagai template V2 (Kimia).")

    # ── Validasi hasil ────────────────────────────────────────────────────────
    if not kode_matkul:
        warnings.append("kode_matkul tidak ditemukan di PDF. Harap isi manual.")
    if not rows:
        warnings.append(
            "Tidak ada baris pertemuan yang berhasil diekstrak. "
            "Pastikan file adalah RPS format Telkom University (template V1 atau V2)."
        )

    tahun_ajaran_id = get_active_tahun_ajaran_id()

    logger.info(
        f"[PDF_EXTRACTOR] parser={parser_used} kode_matkul={kode_matkul} "
        f"rows={len(rows)} warnings={len(warnings)}"
    )

    return {
        "kode_matkul":     kode_matkul,
        "nama_matkul":     nama_matkul,
        "tahun_ajaran_id": tahun_ajaran_id,
        "parser_version":  parser_used,
        "rows":            rows,
        "warnings":        warnings,
    }
