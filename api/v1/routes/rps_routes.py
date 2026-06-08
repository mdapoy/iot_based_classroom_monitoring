from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from repositories.supabase_client import supabase
from models.rps_schema import RPSRequest, RPSConfirmRequest
from api.v1.deps import optional_authenticated
from utils.file_validator import validate_csv
from core.logger import logger
from typing import Optional
import pandas as pd
import io

router = APIRouter(prefix="/rps", tags=["rps"])


@router.post("")
def create_or_update_rps(data: RPSRequest, user: dict = Depends(optional_authenticated)):
    """
    Simpan atau perbarui data RPS pertemuan ke tabel rps_pertemuan.
    Jika kombinasi kode_matkul + pertemuan_ke sudah ada, data akan diperbarui (upsert).
    """
    payload = {
        "kode_matkul": data.kodeMatkul.strip(),
        "pertemuan_ke": data.pertemuan,
        "materi_pembelajaran": data.materi.strip(),
        "pengalaman_pembelajaran_mahasiswa": data.pengalaman.strip(),
    }
    if data.tahun_ajaran_id:
        payload["tahun_ajaran_id"] = data.tahun_ajaran_id

    try:
        res = (
            supabase.table("rps_pertemuan")
            .upsert(payload, on_conflict="kode_matkul,pertemuan_ke")
            .execute()
        )

        if not res.data:
            raise HTTPException(status_code=500, detail="Gagal menyimpan data RPS")

        saved = res.data[0]
        logger.info(
            f"[RPS] Saved | kode_matkul={saved['kode_matkul']} "
            f"pertemuan_ke={saved['pertemuan_ke']}"
        )

        return {
            "status": "success",
            "message": f"RPS pertemuan ke-{data.pertemuan} untuk {data.kodeMatkul} berhasil disimpan.",
            "data": saved,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS] Error saving: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-csv")
async def upload_rps_csv(
    file: UploadFile = File(...),
    tahun_ajaran_id: Optional[str] = Form(None),
    user: dict = Depends(optional_authenticated),
):
    """
    Upload RPS dari file CSV sekaligus.
    Format kolom wajib:
      kode_matkul, pertemuan_ke, materi_pembelajaran,
      pengalaman_pembelajaran_mahasiswa
    Duplikat (kode_matkul + pertemuan_ke) di-upsert (update).
    """
    validate_csv(file)

    REQUIRED_COLUMNS = {
        "kode_matkul",
        "pertemuan_ke",
        "materi_pembelajaran",
        "pengalaman_pembelajaran_mahasiswa",
    }

    try:
        contents = await file.read()

        # Hitung total baris data sebelum parsing (untuk deteksi baris yang di-skip)
        raw_text     = contents.decode("utf-8", errors="replace")
        total_lines  = sum(1 for l in raw_text.splitlines()[1:] if l.strip())

        # on_bad_lines='skip' → baris yang field-nya tidak cocok (misal ada koma
        # di dalam nilai yang tidak di-quote) dilewati, tidak crash keseluruhan
        df = pd.read_csv(io.BytesIO(contents), on_bad_lines="skip")

        auto_skipped = total_lines - len(df)
        if auto_skipped > 0:
            logger.warning(
                f"[RPS CSV] {auto_skipped} baris di-skip otomatis "
                f"(format tidak valid — kemungkinan koma di dalam nilai field)"
            )

        # Normalize nama kolom
        df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Kolom tidak ditemukan dalam CSV: {', '.join(sorted(missing))}"
            )

        df = df[list(REQUIRED_COLUMNS)].copy()
        df = df.dropna(subset=["kode_matkul", "pertemuan_ke"])
        df["pertemuan_ke"] = df["pertemuan_ke"].astype(int)
        df = df.fillna("")

        rows = df.to_dict(orient="records")

        if not rows:
            raise HTTPException(status_code=400, detail="CSV tidak memiliki baris data")

        # ── Validasi tipe data per baris di Python (tanpa query ke DB) ──────
        validated    = []
        invalid_rows = []

        for row in rows:
            try:
                row["pertemuan_ke"] = int(row["pertemuan_ke"])
                validated.append(row)
            except (ValueError, TypeError):
                invalid_rows.append({
                    "kode_matkul":  row.get("kode_matkul"),
                    "pertemuan_ke": row.get("pertemuan_ke"),
                    "reason":       "pertemuan_ke bukan angka valid",
                })

        if not validated:
            raise HTTPException(
                status_code=400,
                detail="Tidak ada baris valid setelah validasi. Periksa kolom pertemuan_ke."
            )

        # Inject tahun_ajaran_id ke setiap baris jika disertakan
        if tahun_ajaran_id:
            for row in validated:
                row["tahun_ajaran_id"] = tahun_ajaran_id
            logger.info(f"[RPS CSV] tahun_ajaran_id={tahun_ajaran_id} akan di-set untuk semua baris")

        # ── Bulk upsert — 1 round-trip ke DB untuk semua baris valid ────────
        res = (
            supabase.table("rps_pertemuan")
            .upsert(validated, on_conflict="kode_matkul,pertemuan_ke")
            .execute()
        )
        count = len(res.data or [])

        logger.info(
            f"[RPS CSV] Uploaded | success={count} "
            f"invalid={len(invalid_rows)} auto_skipped={auto_skipped} "
            f"kode_matkul={df['kode_matkul'].unique().tolist()}"
        )

        # ── Susun laporan error ──────────────────────────────────────────────
        all_errors = invalid_rows.copy()
        if auto_skipped > 0:
            all_errors.append({
                "kode_matkul":  "-",
                "pertemuan_ke": "-",
                "reason": (
                    f"{auto_skipped} baris di-skip otomatis karena format tidak valid "
                    f"(kemungkinan koma di dalam nilai field yang tidak di-quote)"
                ),
            })

        total_skipped = len(invalid_rows) + auto_skipped

        return {
            "status":        "success",
            "message":       f"{count} baris RPS berhasil disimpan.",
            "rows_upserted": count,
            "skipped":       total_skipped,
            "errors":        all_errors,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS CSV] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-pdf")
async def upload_rps_pdf_direct(
    file: UploadFile = File(...),
    tahun_ajaran_id: Optional[str] = Form(None),
    user: dict = Depends(optional_authenticated),
):
    """
    Upload PDF RPS → ekstrak → langsung simpan ke DB dalam satu langkah.
    Endpoint ini dipanggil oleh frontend Input RPS (tanpa preview step).

    - tahun_ajaran_id opsional: jika tidak dikirim, backend auto-ambil is_aktif=true.
    - Response format sama dengan /upload-csv agar kompatibel dengan frontend.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File harus berformat PDF")

    try:
        from services.rps.pdf_extractor import extract_rps_from_pdf, get_active_tahun_ajaran_id

        file_bytes = await file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="File PDF kosong")

        # Ekstrak data dari PDF
        result = extract_rps_from_pdf(file_bytes)

        if not result["rows"]:
            raise HTTPException(
                status_code=422,
                detail="Tidak ada data pertemuan yang berhasil diekstrak. "
                       "Pastikan file adalah RPS format Telkom University."
            )

        # Resolve tahun_ajaran_id: dari form → dari DB aktif
        ta_id = tahun_ajaran_id or result.get("tahun_ajaran_id") or get_active_tahun_ajaran_id()

        kode_matkul = result.get("kode_matkul", "").strip()
        if not kode_matkul:
            raise HTTPException(
                status_code=422,
                detail="kode_matkul tidak ditemukan di PDF. Gunakan endpoint /rps/extract-pdf untuk input manual."
            )

        # Susun payload DB
        payload = []
        for row in result["rows"]:
            item = {
                "kode_matkul":                      kode_matkul,
                "pertemuan_ke":                     row["pertemuan_ke"],
                "materi_pembelajaran":              row["materi_pembelajaran"].strip(),
                "pengalaman_pembelajaran_mahasiswa": row["pengalaman_pembelajaran_mahasiswa"].strip(),
            }
            if ta_id:
                item["tahun_ajaran_id"] = ta_id
            payload.append(item)

        # Bulk upsert
        res = (
            supabase.table("rps_pertemuan")
            .upsert(payload, on_conflict="kode_matkul,pertemuan_ke")
            .execute()
        )
        count = len(res.data or [])

        logger.info(
            f"[RPS PDF UPLOAD] kode_matkul={kode_matkul} rows={count} "
            f"warnings={len(result['warnings'])} tahun_ajaran_id={ta_id}"
        )

        return {
            "status":        "success",
            "message":       f"{count} baris RPS berhasil disimpan untuk {kode_matkul}.",
            "rows_upserted": count,
            "kode_matkul":   kode_matkul,
            "nama_matkul":   result.get("nama_matkul"),
            "tahun_ajaran_id": ta_id,
            "skipped":       0,
            "errors":        result["warnings"],  # warnings ditampilkan sebagai info
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS PDF UPLOAD] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-pdf")
async def extract_rps_pdf(
    file: UploadFile = File(...),
    user: dict = Depends(optional_authenticated),
):
    """
    Upload file PDF RPS (format Telkom University) dan ekstrak datanya.
    Tidak menyimpan apa pun ke database — hanya mengembalikan preview.

    Response berisi:
      - kode_matkul   : deteksi otomatis dari halaman info
      - nama_matkul   : deteksi otomatis
      - tahun_ajaran_id: UUID tahun ajaran aktif (is_aktif=true)
      - rows          : list pertemuan (pertemuan_ke, materi, pengalaman)
      - warnings      : daftar peringatan kualitas data (jika ada)

    Langkah selanjutnya: kirim hasil ini ke POST /rps/confirm-insert
    setelah user memverifikasi.
    """
    # Validasi tipe file
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File harus berformat PDF")

    try:
        from services.rps.pdf_extractor import extract_rps_from_pdf

        file_bytes = await file.read()

        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="File PDF kosong")

        result = extract_rps_from_pdf(file_bytes)

        if not result["rows"]:
            raise HTTPException(
                status_code=422,
                detail="Tidak ada data pertemuan yang berhasil diekstrak. "
                       "Pastikan file adalah RPS format Telkom University."
            )

        logger.info(
            f"[RPS PDF] Extracted | file={file.filename} "
            f"kode_matkul={result['kode_matkul']} rows={len(result['rows'])}"
        )

        return {
            "status":   "success",
            "message":  f"{len(result['rows'])} pertemuan berhasil diekstrak. "
                        f"Periksa data lalu kirim ke /rps/confirm-insert untuk menyimpan.",
            "preview":  result,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS PDF] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-insert")
def confirm_insert_rps(
    data: RPSConfirmRequest,
    user: dict = Depends(optional_authenticated),
):
    """
    Simpan hasil ekstraksi PDF ke tabel rps_pertemuan (bulk upsert).
    Dipanggil setelah user memverifikasi preview dari /rps/extract-pdf.

    - Jika tahun_ajaran_id tidak dikirim, backend auto-ambil dari is_aktif=true.
    - Menggunakan upsert on_conflict(kode_matkul, pertemuan_ke) → aman dijalankan ulang.
    """
    if not data.rows:
        raise HTTPException(status_code=400, detail="Tidak ada baris data yang dikirim")

    try:
        # Resolve tahun_ajaran_id
        tahun_ajaran_id = data.tahun_ajaran_id
        if not tahun_ajaran_id:
            from services.rps.pdf_extractor import get_active_tahun_ajaran_id
            tahun_ajaran_id = get_active_tahun_ajaran_id()

        # Susun payload DB
        payload = []
        for row in data.rows:
            item = {
                "kode_matkul":                      data.kode_matkul.strip(),
                "pertemuan_ke":                     row.pertemuan_ke,
                "materi_pembelajaran":              row.materi_pembelajaran.strip(),
                "pengalaman_pembelajaran_mahasiswa": row.pengalaman_pembelajaran_mahasiswa.strip(),
            }
            if tahun_ajaran_id:
                item["tahun_ajaran_id"] = tahun_ajaran_id
            payload.append(item)

        res = (
            supabase.table("rps_pertemuan")
            .upsert(payload, on_conflict="kode_matkul,pertemuan_ke")
            .execute()
        )

        count = len(res.data or [])

        logger.info(
            f"[RPS PDF] Inserted | kode_matkul={data.kode_matkul} "
            f"rows={count} tahun_ajaran_id={tahun_ajaran_id}"
        )

        return {
            "status":        "success",
            "message":       f"{count} baris RPS berhasil disimpan untuk {data.kode_matkul}.",
            "rows_upserted": count,
            "kode_matkul":   data.kode_matkul,
            "tahun_ajaran_id": tahun_ajaran_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS PDF] Confirm insert error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def get_rps_list(
    kode_matkul: Optional[str] = None,
    tahun_ajaran_id: Optional[str] = None,
    user: dict = Depends(optional_authenticated),
):
    """
    Ambil daftar RPS. Bisa difilter berdasarkan kode_matkul dan/atau tahun_ajaran_id.
    """
    try:
        query = (
            supabase.table("rps_pertemuan")
            .select("*")
            .order("kode_matkul")
            .order("pertemuan_ke")
        )

        if kode_matkul:
            query = query.eq("kode_matkul", kode_matkul.strip())
        if tahun_ajaran_id:
            query = query.eq("tahun_ajaran_id", tahun_ajaran_id)
            logger.info(f"[RPS] get_rps_list filtered by tahun_ajaran_id={tahun_ajaran_id}")

        res = query.execute()
        return {
            "status": "success",
            "data": res.data or [],
        }

    except Exception as e:
        logger.error(f"[RPS] Error fetching list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
