from fastapi import APIRouter, UploadFile, File, Depends
from services.stt.stt_service import send_to_stt, check_stt_result
from repositories.supabase_client import supabase
from services.storage.storage_service import upload_transcript
from services.stt.callback_handler import handle_stt_callback
from api.v1.deps import require_authenticated

router = APIRouter(tags=["Transcription"])

TABLE = "reports"

@router.post("/test-stt")
async def test_stt(
    file: UploadFile = File(...),
    user: dict = Depends(require_authenticated)
):
    return await send_to_stt(file)

# ⚠️ /callback sengaja TIDAK pakai auth — dipanggil langsung oleh Telkom AI STT service
@router.post("/callback")
async def callback(data: dict):
    task_id = data.get("_id")
    text = data.get("data", {}).get("all_text")

    if not task_id or not text:
        return {"status": "invalid payload"}

    handle_stt_callback(task_id, text)

    return {"status": "ok"}


@router.get("/check/{task_id}")
def check_result(task_id: str, user: dict = Depends(require_authenticated)):
    return check_stt_result(task_id)