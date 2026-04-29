from fastapi import APIRouter
# from fastapi.responses import FileResponse
from services.report.report_orchestrator import generate_report
from services.report.report_service import get_existing_summary
from repositories.supabase_client import supabase
from models.report_schema import ReportRequest
from core.logger import logger
router = APIRouter(tags=["report"])

@router.post("/generate-report")
async def generate(data: ReportRequest):

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
    kelas: str = None
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
async def get_summary(report_id: int):
    
    summary = get_existing_summary(report_id)

    if not summary:
        return {
            "status": "error",
            "message": "Summary belum tersedia"
        }

    file_path = summary["file_path"]

    # 🔥 generate ulang public URL
    public_url = supabase.storage \
        .from_("summary") \
        .get_public_url(file_path)

    # 🧪 LOG PERBANDINGAN
    logger.info(f"[DB] file_path: {file_path}")
    logger.info(f"[DB] public_url (re-generated): {public_url}")

    return {
        "status": "success",
        "url": public_url
    }