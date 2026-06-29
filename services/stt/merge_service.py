from repositories.supabase_client import supabase
from services.storage.storage_service import upload_transcript, upload_diarization
from core.logger import logger


def merge_transcript(report_id: int):

    try:
        logger.info(f"[MERGE] Start | report_id={report_id}")

        # Ambil chunk beserta utterances
        res = supabase.table("audio_chunks") \
            .select("chunk_index, transcript, utterances, status") \
            .eq("report_id", report_id) \
            .order("chunk_index") \
            .execute()

        chunks = res.data or []

        if not chunks:
            raise Exception("No chunks found")

        not_done = [c for c in chunks if c["status"] != "done"]
        if not_done:
            raise Exception(f"{len(not_done)} chunks not finished")

        missing = [c for c in chunks if not c.get("transcript")]
        if missing:
            raise Exception(f"{len(missing)} chunks missing transcript")

        # Cek existing
        existing = supabase.table("reports") \
            .select("transcript_path") \
            .eq("id", report_id) \
            .single() \
            .execute()

        if existing.data and existing.data.get("transcript_path"):
            logger.warning(f"[MERGE SKIP] Already exists | report_id={report_id}")
            return existing.data["transcript_path"]

        # Gabungkan teks
        full_text = "\n".join([c["transcript"] for c in chunks])
        logger.info(f"[MERGE] text_length={len(full_text)}")

        # Gabungkan utterances dari semua chunk (urut chunk_index)
        all_utterances = []
        for c in chunks:
            all_utterances.extend(c.get("utterances") or [])
        logger.info(f"[MERGE] total_utterances={len(all_utterances)}")

        # Upload transcript ke bucket transcripts
        transcript_path = upload_transcript(f"report_{report_id}.txt", full_text)
        logger.info(f"[MERGE] transcript uploaded → {transcript_path}")

        # Upload utterances ke bucket diarization
        utterances_path = upload_diarization(report_id, all_utterances)
        logger.info(f"[MERGE] diarization uploaded → {utterances_path}")

        # Update report
        supabase.table("reports").update({
            "status":           "transcribed",
            "transcript_path":  transcript_path,
            "utterances_path":  utterances_path,
            "error_message":    None,
        }).eq("id", report_id).execute()

        logger.info(f"[MERGE SUCCESS] report_id={report_id}")
        return full_text

    except Exception as e:
        logger.error(f"[MERGE ERROR] report_id={report_id} | {e}", exc_info=True)
        return None