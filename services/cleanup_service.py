import os
from repositories.supabase_client import supabase
from core.logger import logger

def cleanup_chunks(report_id: int):
    try:
        logger.info(f"[CLEANUP] Start | report_id={report_id}")

        # ambil semua chunk
        res = supabase.table("audio_chunks") \
            .select("*") \
            .eq("report_id", report_id) \
            .execute()

        chunks = res.data or []

        for chunk in chunks:
            path = chunk.get("chunk_path")

            # hapus file fisik
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"[CLEANUP FILE] Deleted {path}")

        # hapus record DB
        supabase.table("audio_chunks") \
            .delete() \
            .eq("report_id", report_id) \
            .execute()

        logger.info(f"[CLEANUP DONE] report_id={report_id}")

    except Exception as e:
        logger.error(f"[CLEANUP ERROR] {str(e)}", exc_info=True)