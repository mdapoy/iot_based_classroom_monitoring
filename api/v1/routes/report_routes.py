from fastapi import APIRouter, Depends
from services.report.report_orchestrator import generate_report
from services.report.report_service import get_existing_summary
from repositories.supabase_client import supabase
from models.report_schema import ReportRequest
from api.v1.deps import require_authenticated, optional_authenticated
from core.logger import logger

router = APIRouter(tags=["report"])

@router.post("/generate-report")
async def generate(data: ReportRequest, user: dict = Depends(optional_authenticated)):

    payload = {
        "tanggal": data.tanggal,
        "jam": data.jam,
        "ruangan": data.ruangan,
        "kode_matkul": data.matkul,
        "kode_dosen": data.dosen,
        "kelas": data.kelas
    }

    result = await generate_report(payload)
    return result

@router.get("/filters")
def get_filters(
    tanggal: str = None,
    matkul: str = None,
    dosen: str = None,
    ruangan: str = None,
    kelas: str = None,
    user: dict = Depends(optional_authenticated)
):

    # 🔍 QUERY BASE
    query = supabase.table("monitoring") \
        .select("tanggal, jadwal_kuliah!inner(*)")

    # 🎯 APPLY FILTER
    if tanggal:
        query = query.eq("tanggal", tanggal)

    if matkul:
        query = query.eq("jadwal_kuliah.kode_mata_kuliah", matkul)

    if dosen:
        query = query.eq("jadwal_kuliah.dosen_utama", dosen)

    if ruangan:
        query = query.eq("jadwal_kuliah.ruangan", ruangan)

    if kelas:
        query = query.eq("jadwal_kuliah.kelas", kelas)

    # 🚀 EXECUTE
    res = query.execute()
    data = res.data if res.data else []

    # 🔧 HELPER FUNCTION (AMAN)
    def safe_get_nested(key):
        result = set()
        for d in data:
            jadwal = d.get("jadwal_kuliah")
            if jadwal and jadwal.get(key):
                result.add(jadwal[key])
        return list(result)

    # 📦 RESPONSE
    return {
        "tanggal": list(set([
            d["tanggal"]
            for d in data
            if d.get("tanggal")
        ])),

        "matkul": safe_get_nested("kode_mata_kuliah"),
        "dosen": safe_get_nested("dosen_utama"),
        "ruangan": safe_get_nested("ruangan"),
        "kelas": safe_get_nested("kelas"),
        "jam": safe_get_nested("jam_mulai")
    }

@router.get("/summary/{report_id}")
async def get_summary(report_id: int, user: dict = Depends(optional_authenticated)):
    
    summary = get_existing_summary(report_id)

    if not summary:
        return {
            "status": "error",
            "message": "Summary belum tersedia"
        }

    file_path = summary["file_path"]

    # generate signed URL (private bucket, valid 1 jam)
    public_url = supabase.storage \
        .from_("summary") \
        .create_signed_url(file_path, 3600)["signedURL"]

    # 🧪 LOG PERBANDINGAN
    logger.info(f"[DB] file_path: {file_path}")
    logger.info(f"[DB] public_url (re-generated): {public_url}")

    return {
        "status": "success",
        "url": public_url
    }

@router.get("/report-status/{report_id}")
async def get_report_status(report_id: int, user: dict = Depends(optional_authenticated)):
    res = supabase.table("reports") \
        .select("status, error_message") \
        .eq("id", report_id) \
        .execute()
    
    if not res.data:
        return {
            "status": "error",
            "message": "Report tidak ditemukan"
        }
    
    return {
        "status": "success",
        "report_status": res.data[0]["status"],
        "error_message": res.data[0].get("error_message")
    }