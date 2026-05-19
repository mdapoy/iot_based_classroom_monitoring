from fastapi import APIRouter, Request, Depends
from services.stt.callback_handler import handle_assemblyai_callback
from api.v1.deps import require_authenticated
from core.logger import logger

router = APIRouter(tags=["Transcription"])


# ─────────────────────────────────────────────────────────────
# POST /callback/assemblyai
# Dipanggil oleh AssemblyAI setiap kali satu chunk selesai.
# TIDAK butuh auth — AssemblyAI tidak kirim header autentikasi.
# ─────────────────────────────────────────────────────────────
@router.post("/callback/assemblyai")
async def assemblyai_callback(request: Request):
    """
    Webhook dari AssemblyAI per chunk.

    Payload minimal dari AssemblyAI:
      { "transcript_id": "xxx", "status": "completed" | "error" }
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    transcript_id = body.get("transcript_id") or body.get("id")
    status        = body.get("status", "")

    logger.info(
        f"[WEBHOOK /assemblyai] transcript_id={transcript_id} status={status}"
    )

    if not transcript_id:
        return {"success": False, "message": "transcript_id tidak ada di payload"}

    # Panggil handler (sinkron, tapi cepat — hanya DB + 1 HTTP call ke AAI)
    handle_assemblyai_callback(transcript_id)

    return {"success": True, "transcript_id": transcript_id}
