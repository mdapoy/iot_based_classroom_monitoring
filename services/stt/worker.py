# services/stt/worker.py  (AssemblyAI mode)
import asyncio
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from repositories.supabase_client import supabase
from services.stt.stt_service import (
    upload_file_to_aai,
    submit_transcript_aai,
    CALLBACK_URL,
)
from core.logger import logger

load_dotenv()

CHUNK_DURATION_SEC = int(os.getenv("CHUNK_DURATION_SEC", "300"))
STT_LANGUAGE       = os.getenv("STT_LANGUAGE", "id")

semaphore    = asyncio.Semaphore(5)
IDLE_TIMEOUT = 300   # 5 menit
RETRY_DELAY  = 30    # detik sebelum retry chunk failed


def _build_webhook_url() -> str | None:
    """
    Susun URL webhook AssemblyAI dari CALLBACK_URL.
    Contoh: https://app.railway.app/callback/assemblyai

    Otomatis tambah https:// jika CALLBACK_URL tidak punya protokol.
    """
    if not CALLBACK_URL:
        return None
    base = CALLBACK_URL.rstrip("/")
    # Hapus suffix /callback lama jika ada
    if base.endswith("/callback"):
        base = base[: -len("/callback")]
    # Pastikan selalu pakai https://
    if not base.startswith("http://") and not base.startswith("https://"):
        base = f"https://{base}"
    return f"{base}/callback/assemblyai"


async def process_chunk(chunk: dict):
    async with semaphore:
        chunk_id  = chunk["id"]
        path      = chunk["chunk_path"]
        report_id = chunk["report_id"]
        chunk_idx = chunk.get("chunk_index", 0)

        try:
            logger.info(f"[WORKER] Processing chunk {chunk_id} (index={chunk_idx})")

            # ── Optimistic lock ──────────────────────────────────────
            now_iso = datetime.utcnow().isoformat()
            updated = (
                supabase.table("audio_chunks")
                .update({"status": "processing", "updated_at": now_iso})
                .eq("id", chunk_id)
                .eq("status", chunk["status"])
                .execute()
            )
            if not updated.data:
                logger.warning(f"[WORKER] Chunk {chunk_id} already taken")
                return

            # Tandai report sedang diproses
            supabase.table("reports").update({
                "status": "processing"
            }).eq("id", report_id).eq("status", "chunking").execute()

            if not os.path.exists(path):
                raise Exception(f"File not found: {path}")

            # ── Upload ke AssemblyAI CDN ─────────────────────────────
            audio_url = upload_file_to_aai(path)

            # ── Hitung offset dari chunk_index ───────────────────────
            offset_sec  = chunk_idx * CHUNK_DURATION_SEC
            webhook_url = _build_webhook_url()

            logger.info(
                f"[WORKER] chunk={chunk_id} idx={chunk_idx} "
                f"offset={offset_sec}s webhook={webhook_url}"
            )

            # ── Submit ke AssemblyAI (webhook mode) ──────────────────
            transcript_id = submit_transcript_aai(
                audio_url=audio_url,
                language_code=STT_LANGUAGE,
                webhook_url=webhook_url,
            )

            # Simpan transcript_id sebagai task_id
            supabase.table("audio_chunks").update({
                "task_id":    transcript_id,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", chunk_id).execute()

            logger.info(
                f"[STT SUBMITTED] chunk={chunk_id} idx={chunk_idx} "
                f"tid={transcript_id}"
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[WORKER ERROR] Chunk {chunk_id}: {error_msg}")
            supabase.table("audio_chunks").update({
                "status":        "failed",
                "error_message": error_msg,
                "updated_at":    datetime.utcnow().isoformat(),
            }).eq("id", chunk_id).execute()


def _should_retry(chunk: dict) -> bool:
    """Cek apakah chunk failed sudah cukup lama untuk dicoba lagi."""
    updated_at = chunk.get("updated_at")
    if not updated_at:
        return True

    try:
        t = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        # Normalisasi ke naive UTC
        if t.tzinfo is not None:
            t = t.astimezone(timezone.utc).replace(tzinfo=None)
        age = (datetime.utcnow() - t).total_seconds()
        return age >= RETRY_DELAY
    except Exception:
        return True


async def run_worker():
    logger.info("[WORKER] Started (AssemblyAI mode)")

    idle_since = datetime.utcnow()

    while True:
        try:
            res = (
                supabase.table("audio_chunks")
                .select("*")
                .in_("status", ["pending", "failed"])
                .limit(5)
                .execute()
            )
            chunks = res.data or []

            if chunks:
                idle_since = datetime.utcnow()
                logger.info(f"[WORKER] Found {len(chunks)} chunks")

                filtered = [
                    c for c in chunks
                    if c["status"] == "pending"
                    or (c["status"] == "failed" and _should_retry(c))
                ]

                if filtered:
                    await asyncio.gather(*[process_chunk(c) for c in filtered])

            else:
                idle_time = (datetime.utcnow() - idle_since).total_seconds()
                if idle_time > IDLE_TIMEOUT:
                    logger.info("[WORKER] Idle timeout reached, stopping worker")
                    break

        except Exception as e:
            logger.error(f"[WORKER LOOP ERROR] {e}")

        await asyncio.sleep(2)

    logger.info("[WORKER] Stopped")
