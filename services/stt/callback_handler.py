from repositories.supabase_client import supabase
from core.logger import logger
from services.stt.merge_service import merge_transcript


def handle_stt_callback(task_id: str, text: str):
    try:
        logger.info(f"[CALLBACK] Received | task_id={task_id}")

        logger.info(f"[CALLBACK RAW DATA] task_id={task_id} text_length={len(text) if text else 0}")

        if not text or not text.strip():
            logger.warning(f"[CALLBACK] Empty transcript | task_id={task_id}")
            return

        # 1. cari chunk
        logger.info(f"[DB LOOKUP] searching task_id={task_id}")
        chunk_res = supabase.table("audio_chunks") \
            .select("*") \
            .eq("task_id", task_id) \
            .execute()

        if not chunk_res.data:
            logger.warning(f"[CALLBACK] Chunk not found | task_id={task_id}")
            return

        chunk = chunk_res.data[0]
        chunk_id = chunk["id"]
        report_id = chunk["report_id"]

        # 2. idempotent
        if chunk["status"] == "done":
            return

        # 3. update chunk
        supabase.table("audio_chunks").update({
            "status": "done",
            "transcript": text
        }).eq("id", chunk_id).execute()

        # 4. cek sisa
        remaining = supabase.table("audio_chunks") \
            .select("id") \
            .eq("report_id", report_id) \
            .neq("status", "done") \
            .execute()

        if remaining.data:
            return

        # 🔒 LOCK REPORT (PAKAI STATUS VALID)
        lock = supabase.table("reports").update({
            "status": "processing"
        }).eq("id", report_id).in_("status", ["chunking", "processing"]).execute()

        if not lock.data:
            logger.info(f"[LOCK FAIL] report already taken | {report_id}")
            return

        logger.info(f"[MERGE TRIGGER] report_id={report_id}")

        # 5. merge transcript
        result = merge_transcript(report_id)

        if not result:
            logger.error(f"[MERGE FAILED] report_id={report_id}")
            supabase.table("reports").update({
                "status": "failed"
            }).eq("id", report_id).execute()
            return

        # merge_transcript akan set → transcribed
        logger.info(f"[TRANSCRIBED] report_id={report_id}")

    except Exception as e:
        logger.error(f"[CALLBACK ERROR] {e}", exc_info=True)