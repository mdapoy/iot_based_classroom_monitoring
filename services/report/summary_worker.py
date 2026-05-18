import asyncio
import os
from datetime import datetime

from repositories.supabase_client import supabase
from services.storage.storage_service import download_transcript, upload_summary
from services.summarizer.summarizer import summarize_text
from services.report.report_service import generate_combined_pdf
from services.rps.rps_service import get_meeting_week, get_rps_for_week
from services.rps.rag_analyzer import analyze_rps
from core.logger import logger


# batasi concurrency
semaphore = asyncio.Semaphore(2)
IDLE_TIMEOUT = 300  # 5 menit


async def process_summary(report):
    async with semaphore:
        report_id = report["id"]
        local_path = None

        try:
            logger.info(f"[SUMMARY WORKER] Processing report {report_id}")

            # ── 1. Ambil transcript path (fresh dari DB) ─────────────
            fresh = (
                supabase.table("reports")
                .select("transcript_path")
                .eq("id", report_id)
                .single()
                .execute()
            )

            path = fresh.data.get("transcript_path")

            if not path:
                raise Exception("Transcript path not found")

            transcript = download_transcript(path)

            # ── 2. Hitung minggu pertemuan ───────────────────────────
            tanggal     = report.get("tanggal")
            kode_matkul = report.get("kode_matkul")

            if not tanggal or not kode_matkul:
                raise Exception(
                    "Field tanggal/kode_matkul tidak tersedia di report"
                )

            pertemuan_ke = get_meeting_week(str(tanggal))
            logger.info(f"[RAG] tanggal={tanggal} → pertemuan_ke={pertemuan_ke}")

            # ── 3. Fetch RPS pertemuan ini ───────────────────────────
            rps = get_rps_for_week(kode_matkul, pertemuan_ke)

            if not rps:
                raise Exception(
                    f"RPS pertemuan ke-{pertemuan_ke} untuk '{kode_matkul}' belum tersedia. "
                    f"Harap isi tabel rps_pertemuan terlebih dahulu."
                )

            # ── 4. Summarize + Analisis RPS secara PARALEL ───────────
            logger.info(f"[SUMMARY+RAG] Running parallel Gemini calls | report={report_id}")

            summary_result, analysis_raw = await asyncio.gather(
                asyncio.to_thread(summarize_text, transcript),
                asyncio.to_thread(analyze_rps, transcript, rps, pertemuan_ke),
            )

            # Validasi summary
            if not summary_result.get("success"):
                raise Exception(summary_result.get("error", "Summarize gagal"))

            summary_text_val = summary_result["result"]
            if isinstance(summary_text_val, dict):
                summary_text_val = (
                    summary_text_val.get("abstrak")
                    or summary_text_val.get("summary")
                    or ""
                )

            # Validasi analisis RPS
            if not analysis_raw.get("success"):
                raise Exception(analysis_raw.get("error", "Analisis RPS gagal"))

            # ── 5. Susun dict analisis lengkap untuk PDF ─────────────
            analysis = {
                "pertemuan_ke":  pertemuan_ke,
                "materi_pembelajaran": rps.get("materi_pembelajaran", "-"),
                "kesesuaian":    analysis_raw.get("kesesuaian", "-"),
                "status_waktu":  analysis_raw.get("status_waktu", "-"),
                "penjelasan":    analysis_raw.get("penjelasan", "-"),
            }

            # ── 6. Generate PDF 2 halaman ────────────────────────────
            os.makedirs("temp", exist_ok=True)
            local_path = f"temp/report_{report_id}.pdf"

            generate_combined_pdf(summary_text_val, analysis, local_path)

            # ── 7. Upload & update DB ────────────────────────────────
            storage_path = f"report_{report_id}.pdf"

            logger.info(f"[UPLOAD] uploading combined PDF → {storage_path}")
            upload_summary(local_path, storage_path)

            supabase.table("reports").update({
                "status": "done",
                "summary_path": storage_path,
                "error_message": None,
            }).eq("id", report_id).execute()

            logger.info(
                f"[SUMMARY DONE] Report {report_id} | "
                f"pertemuan={pertemuan_ke} kesesuaian={analysis['kesesuaian']}"
            )

        except Exception as e:
            logger.error(f"[SUMMARY ERROR] Report {report_id}: {e}", exc_info=True)

            supabase.table("reports").update({
                "status": "failed",
                "error_message": str(e),
            }).eq("id", report_id).execute()

        finally:
            if local_path and os.path.exists(local_path):
                os.remove(local_path)


async def run_summary_worker():
    logger.info("[SUMMARY WORKER] Started")

    idle_since = datetime.utcnow()

    while True:
        try:
            res = (
                supabase.table("reports")
                .select("*")
                .eq("status", "transcribed")
                .limit(3)
                .execute()
            )

            reports = res.data or []

            if reports:
                idle_since = datetime.utcnow()
                tasks = []

                for r in reports:
                    # optimistic lock
                    updated = (
                        supabase.table("reports")
                        .update({"status": "summarizing"})
                        .eq("id", r["id"])
                        .eq("status", "transcribed")
                        .execute()
                    )

                    if not updated.data:
                        continue  # another worker already grabbed it

                    tasks.append(process_summary(r))

                if tasks:
                    await asyncio.gather(*tasks)

            else:
                idle_time = (datetime.utcnow() - idle_since).total_seconds()

                if idle_time > IDLE_TIMEOUT:
                    logger.info(
                        "[SUMMARY WORKER] Idle timeout reached, stopping worker"
                    )
                    break

        except Exception as e:
            logger.error(f"[SUMMARY LOOP ERROR] {e}", exc_info=True)

        await asyncio.sleep(5)

    logger.info("[SUMMARY WORKER] Stopped")
