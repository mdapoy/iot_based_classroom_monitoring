from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from repositories.supabase_client import supabase
from models.rps_schema import RPSRequest
from api.v1.deps import optional_authenticated
from utils.file_validator import validate_csv
from core.logger import logger
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
        df = pd.read_csv(io.BytesIO(contents))

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

        # Proses per-baris agar baris yang gagal dilaporkan
        # tanpa menggagalkan seluruh upload
        successful = []
        skipped_rows = []

        for row in rows:
            try:
                res = (
                    supabase.table("rps_pertemuan")
                    .upsert(row, on_conflict="kode_matkul,pertemuan_ke")
                    .execute()
                )
                if res.data:
                    successful.append(row)
                else:
                    skipped_rows.append({
                        "kode_matkul":  row.get("kode_matkul"),
                        "pertemuan_ke": row.get("pertemuan_ke"),
                        "reason":       "Upsert tidak mengembalikan data",
                    })
            except Exception as row_err:
                skipped_rows.append({
                    "kode_matkul":  row.get("kode_matkul"),
                    "pertemuan_ke": row.get("pertemuan_ke"),
                    "reason":       str(row_err),
                })

        count = len(successful)

        logger.info(
            f"[RPS CSV] Uploaded | success={count} skipped={len(skipped_rows)} "
            f"kode_matkul={df['kode_matkul'].unique().tolist()}"
        )

        return {
            "status":        "success",
            "message":       f"{count} baris RPS berhasil disimpan.",
            "rows_upserted": count,
            "skipped":       len(skipped_rows),
            "errors":        skipped_rows,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RPS CSV] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def get_rps_list(
    kode_matkul: str = None,
    user: dict = Depends(optional_authenticated),
):
    """
    Ambil daftar RPS. Bisa difilter berdasarkan kode_matkul.
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

        res = query.execute()
        return {
            "status": "success",
            "data": res.data or [],
        }

    except Exception as e:
        logger.error(f"[RPS] Error fetching list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
