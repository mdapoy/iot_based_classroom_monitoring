import asyncio
from repositories.supabase_client import supabase
from services.storage.storage_service import download_transcript, upload_summary
from services.summarizer.summarizer import summarize_text
from services.report.report_service import generate_pdf
from core.logger import logger
import os
from datetime import datetime
import asyncio


# batasi concurrency
semaphore = asyncio.Semaphore(2)
IDLE_TIMEOUT = 300  # 5 menit


async def process_summary(report):
    async with semaphore:
        report_id = report["id"]
        local_path = None  # 🔥 penting (biar gak crash di finally)

        try:
            logger.info(f"[SUMMARY WORKER] Processing report {report_id}")

            # ambil transcript path
            fresh = supabase.table("reports") \
                .select("transcript_path") \
                .eq("id", report_id) \
                .single() \
                .execute()

            path = fresh.data.get("transcript_path")

            if not path:
                raise Exception("Transcript path not found")

            transcript = download_transcript(path)

            # retry summarize
            max_retry = 3
            result = None

            for attempt in range(max_retry):
                result = await asyncio.to_thread(summarize_text, transcript)

                if result.get("success"):
                    break

                await asyncio.sleep(2 ** attempt)

            if not result or not result.get("success"):
                raise Exception(result.get("error"))

            summary = result["result"]

            # format summary
            if isinstance(summary, dict):
                summary_text = summary.get("abstrak") or summary.get("summary") or ""
            else:
                summary_text = summary

            # ✅ clean (tanpa nested folder)
            os.makedirs("temp", exist_ok=True)

            local_path = f"temp/report_{report_id}.pdf"

            # generate PDF
            generate_pdf(summary_text, local_path)

            # 🔥 upload TANPA folder (sesuai request kamu)
            storage_path = f"report_{report_id}.pdf"

            logger.info(f"[UPLOAD] uploading summary → {storage_path}")
            upload_summary(local_path, storage_path)

            # update DB
            supabase.table("reports").update({
                "status": "done",
                "summary_path": storage_path,
                "error_message": None
            }).eq("id", report_id).execute()

            logger.info(f"[SUMMARY DONE] Report {report_id}")

        except Exception as e:
            logger.error(f"[SUMMARY ERROR] Report {report_id}: {e}", exc_info=True)

            supabase.table("reports").update({
                "status": "failed",
                "error_message": str(e)
            }).eq("id", report_id).execute()

        finally:
            # 🔥 aman (gak error walau local_path belum dibuat)
            if local_path and os.path.exists(local_path):
                os.remove(local_path)

async def run_summary_worker():
    logger.info("[SUMMARY WORKER] Started")

    idle_since = datetime.utcnow()

    while True:
        try:
            res = supabase.table("reports") \
                .select("*") \
                .eq("status", "transcribed") \
                .limit(3) \
                .execute()

            reports = res.data or []

            # ✅ ADA REPORT
            if reports:
                idle_since = datetime.utcnow()

                tasks = []

                for r in reports:
                    # 🔒 LOCK
                    updated = supabase.table("reports").update({
                        "status": "summarizing"
                    }).eq("id", r["id"]).eq("status", "transcribed").execute()

                    # kalau gagal → skip
                    if not updated.data:
                        continue

                    tasks.append(process_summary(r))

                if tasks:
                    await asyncio.gather(*tasks)

            # ❌ TIDAK ADA REPORT
            else:
                idle_time = (
                    datetime.utcnow() - idle_since
                ).total_seconds()

                if idle_time > IDLE_TIMEOUT:
                    logger.info(
                        "[SUMMARY WORKER] Idle timeout reached, stopping worker"
                    )
                    break

        except Exception as e:
            logger.error(
                f"[SUMMARY LOOP ERROR] {e}",
                exc_info=True
            )

        await asyncio.sleep(5)

    logger.info("[SUMMARY WORKER] Stopped")