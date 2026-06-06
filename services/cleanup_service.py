import os
from repositories.supabase_client import supabase
from core.logger import logger

def cleanup_chunks(report_id: int):
    try:
        logger.info(f"[CLEANUP] Start | report_id={report_id}")

        # ambil semua chunk
        res = supabase.table("audio_chunks") \
            .select("id, chunk_path") \
            .eq("report_id", report_id) \
            .execute()

        chunks = res.data or []

        chunk_dir = None  # track direktori chunk untuk dihapus setelah semua file selesai

        for chunk in chunks:
            path = chunk.get("chunk_path")

            # hapus file fisik
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"[CLEANUP FILE] Deleted {path}")

                # simpan direktori dari path chunk pertama yang valid
                if chunk_dir is None:
                    chunk_dir = os.path.dirname(path)

        # hapus subdir khusus report (bukan root "chunks/") jika sudah kosong
        if chunk_dir and chunk_dir != "chunks" and os.path.isdir(chunk_dir):
            try:
                os.rmdir(chunk_dir)
                logger.debug(f"[CLEANUP DIR] Removed dir {chunk_dir}")
            except OSError:
                # direktori tidak kosong atau sudah dihapus, abaikan
                pass

        # hapus record DB
        supabase.table("audio_chunks") \
            .delete() \
            .eq("report_id", report_id) \
            .execute()

        logger.info(f"[CLEANUP DONE] report_id={report_id}")

    except Exception as e:
        logger.error(f"[CLEANUP ERROR] {str(e)}", exc_info=True)