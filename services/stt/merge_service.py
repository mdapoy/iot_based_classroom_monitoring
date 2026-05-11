from repositories.supabase_client import supabase
from services.storage.storage_service import upload_transcript
from services.cleanup_service import cleanup_chunks
from core.logger import logger


def merge_transcript(report_id: int):

    try:
        logger.info(f"[MERGE] Start | report_id={report_id}")

        # 🔍 ambil chunk
        res = supabase.table("audio_chunks") \
            .select("chunk_index, transcript, status") \
            .eq("report_id", report_id) \
            .order("chunk_index") \
            .execute()

        chunks = res.data or []

        if not chunks:
            raise Exception("No chunks found")

        # ❗ pastikan semua done
        not_done = [c for c in chunks if c["status"] != "done"]

        if not_done:
            raise Exception(f"{len(not_done)} chunks not finished")

        # ❗ pastikan semua ada transcript
        missing = [c for c in chunks if not c.get("transcript")]

        if missing:
            raise Exception(f"{len(missing)} chunks missing transcript")

        # 🛑 cek existing dulu
        existing = supabase.table("reports") \
            .select("transcript_path") \
            .eq("id", report_id) \
            .single() \
            .execute()

        if existing.data and existing.data.get("transcript_path"):

            logger.warning(
                f"[MERGE SKIP] Already exists | report_id={report_id}"
            )

            return existing.data["transcript_path"]

        # 🔗 merge transcript
        full_text = "\n".join([
            c["transcript"] for c in chunks
        ])

        logger.info(
            f"[MERGE DONE] text_length={len(full_text)}"
        )

        filename = f"report_{report_id}.txt"

        # 📦 upload transcript
        path = upload_transcript(filename, full_text)

        logger.info(
            f"[UPLOAD SUCCESS] transcript_path={path}"
        )

        # 📝 update report
        supabase.table("reports").update({
            "status": "transcribed",
            "transcript_path": path,
            "error_message": None
        }).eq("id", report_id).execute()

        logger.info(
            f"[REPORT UPDATED] report_id={report_id}"
        )

        # 🧹 cleanup local chunk files
        cleanup_chunks(report_id)

        logger.info(
            f"[MERGE SUCCESS] report_id={report_id}"
        )

        return full_text

    except Exception as e:

        logger.error(
            f"[MERGE ERROR] report_id={report_id} | {e}",
            exc_info=True
        )

        return None