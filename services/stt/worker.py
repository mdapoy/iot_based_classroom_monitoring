# services/stt/worker.py
import asyncio
import os
from datetime import datetime, timedelta
from repositories.supabase_client import supabase
from services.stt.stt_service import send_to_stt_file
from core.logger import logger
import os
from dotenv import load_dotenv

load_dotenv()

semaphore = asyncio.Semaphore(5)

RETRY_DELAY_SECONDS = 30
MAX_RETRY_WINDOW_MINUTES = 10
IDLE_TIMEOUT = 300  # 5 menit


def is_retryable_error(error: str) -> bool:
    error = error.lower()

    retryable_keywords = [
        "timeout",
        "connection",
        "timed out",
        "temporarily unavailable",
        "max retries exceeded",
        "connection aborted"
    ]

    return any(k in error for k in retryable_keywords)


def should_retry(chunk) -> bool:
    """Tentukan apakah chunk failed boleh dicoba lagi"""
    updated_at = chunk.get("updated_at")

    if not updated_at:
        return True

    try:
        updated_time = datetime.fromisoformat(updated_at)
    except Exception:
        return True

    now = datetime.utcnow()

    # delay retry
    if now - updated_time < timedelta(seconds=RETRY_DELAY_SECONDS):
        return False

    # stop retry kalau sudah terlalu lama (biar nggak infinite)
    if now - updated_time > timedelta(minutes=MAX_RETRY_WINDOW_MINUTES):
        return False

    return True


async def process_chunk(chunk):
    async with semaphore:
        chunk_id = chunk["id"]
        path = chunk["chunk_path"]
        report_id = chunk["report_id"]

        try:
            logger.info(f"[WORKER] Processing chunk {chunk_id}")

            # 🔒 LOCK CHUNK
            updated = supabase.table("audio_chunks").update({
                "status": "processing"
            }).eq("id", chunk_id).eq("status", chunk["status"]).execute()

            if not updated.data:
                logger.warning(f"[WORKER] Chunk {chunk_id} already taken")
                return

            # 🔄 update report → processing
            supabase.table("reports").update({
                "status": "processing"
            }).eq("id", report_id).eq("status", "chunking").execute()

            # ❗ cek file
            if not os.path.exists(path):
                raise Exception(f"File not found: {path}")

            # 🚀 kirim ke STT
            res = send_to_stt_file(path)
            logger.info(f"[STT RESPONSE] chunk_id={chunk_id} response={res}")

            if not isinstance(res, dict):
                raise Exception(f"Invalid response format: {res}")

            if "error" in res:
                raise Exception(res["error"])
            
            logger.info(f"[SEND STT] chunk_id={chunk_id} path={path}")
            task_id = res.get("task_id")
            logger.info(f"[TASK ID] chunk_id={chunk_id} task_id={task_id}")

            if not task_id:
                raise Exception(f"No task_id from STT: {res}")

            # ✅ update task_id
            supabase.table("audio_chunks").update({
                "task_id": task_id
            }).eq("id", chunk_id).execute()

            logger.info(f"[STT TASK CREATED] chunk={chunk_id} task_id={task_id}")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[WORKER ERROR] Chunk {chunk_id}: {error_msg}")

            supabase.table("audio_chunks").update({
                "status": "failed",
                "error_message": error_msg
            }).eq("id", chunk_id).execute()


async def run_worker():
    logger.info("[WORKER] Started")

    idle_since = datetime.utcnow()

    while True:
        try:
            res = supabase.table("audio_chunks") \
                .select("*") \
                .in_("status", ["pending", "failed"]) \
                .limit(5) \
                .execute()

            chunks = res.data or []

            if chunks:
                idle_since = datetime.utcnow()

                logger.info(f"[WORKER] Found {len(chunks)} chunks")

                filtered_chunks = []

                for c in chunks:
                    if c["status"] == "pending":
                        filtered_chunks.append(c)
                    elif c["status"] == "failed" and should_retry(c):
                        filtered_chunks.append(c)

                if filtered_chunks:
                    await asyncio.gather(
                        *[process_chunk(c) for c in filtered_chunks]
                    )

            else:
                idle_time = (datetime.utcnow() - idle_since).total_seconds()

                if idle_time > IDLE_TIMEOUT:
                    logger.info("[WORKER] Idle timeout reached, stopping worker")
                    break

        except Exception as e:
            logger.error(f"[WORKER LOOP ERROR] {e}")

        await asyncio.sleep(2)

    logger.info("[WORKER] Stopped")
