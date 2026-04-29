from fileinput import filename

from repositories.supabase_client import supabase
from services.storage.storage_service import download_transcript
from services.storage.google_drive import get_file_from_gdrive_flexible
from services.stt.stt_service import send_to_stt_file
from services.summarizer.summarizer import summarize_text
from services.report.report_service import generate_pdf, get_existing_summary
from utils.metadata_parser import parse_filename
from core.logger import logger
from services.storage.storage_service import upload_summary
from services.report.report_service import insert_summary_record
from datetime import datetime
import asyncio
import os

TABLE = "reports"

async def generate_report(data: dict):

    try:
        logger.info(f"[START] Generate report: {data}")

        # ✅ CLEAN
        def clean(text):
            return str(text).strip()

        data = {k: clean(v) for k, v in data.items()}

        logger.debug(f"[DEBUG] Cleaned data: {data}")

        # 🔍 CHECK DB
        existing = supabase.table(TABLE).select("*").match({
            "tanggal": data["tanggal"],
            "jam": data["jam"],
            "ruangan": data["ruangan"],
            "kode_matkul": data["kode_matkul"],
            "kode_dosen": data["kode_dosen"],
            "kelas": data["kelas"]
        }).execute()

        record = existing.data[0] if existing.data else None

        # ✅ CEK SUMMARY DULU (NEW)
        if record:
            existing_summary = get_existing_summary(record["id"])

            if existing_summary:
                logger.info("[CACHE HIT] Summary already exists")

                file_path = existing_summary["file_path"]

                # 🚨 HANDLE LEGACY DATA
                if file_path.startswith("http"):
                    logger.warning("[LEGACY DATA] file_path already full URL")
                    public_url = file_path
                else:
                    public_url = supabase.storage \
                        .from_("summary") \
                        .get_public_url(file_path)

                logger.info(f"[CACHE RETURN] URL: {public_url}")

                return {
                    "status": "success",
                    "source": "cache",
                    "url": public_url,
                    "report_id": record["id"]
                }

        # 🧠 filename
        def normalize_jam(jam: str):
            return jam[:5]  # 08:30:00 → 08:30
        
        jam = normalize_jam(data["jam"])

        base_filename = f"{data['tanggal']}_{jam}_{data['ruangan']}_{data['kode_matkul']}_{data['kode_dosen']}_{data['kelas']}"
        logger.info(f"[INFO] Base filename: {base_filename}")

        # 🔁 BELUM ADA TRANSKRIP
        if not record or not record.get("transcription_done"):

            logger.info("[STEP] Downloading from Google Drive...")

            audio_path, real_filename = get_file_from_gdrive_flexible(base_filename)

            if not audio_path:
                raise Exception(f"File tidak ditemukan di GDrive: {base_filename}")

            meta = parse_filename(real_filename)

            # 🚀 SEND STT
            stt_result = send_to_stt_file(audio_path)

            if "error" in stt_result:
                raise Exception(stt_result["error"])

            task_id = stt_result["response"]["task_id"]
            logger.info(f"[INFO] Task ID: {task_id}")

            # 🗄️ INSERT AWAL
            insert_res = supabase.table(TABLE).insert({
                **meta,
                "task_id": task_id,
                "transcription_done": False,
                "file_path": None,
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            record_id = insert_res.data[0]["id"]

            # ⏳ POLLING DB
            transcript = None

            for i in range(60):
                logger.info(f"[POLL {i}] Checking DB...")

                check = supabase.table(TABLE).select("*").match({
                    "task_id": task_id
                }).execute()

                db_record = check.data[0] if check.data else None

                if db_record and db_record.get("transcription_done"):
                    transcript = download_transcript(db_record["file_path"])
                    logger.info("[SUCCESS] Transcript ready")
                    break

                await asyncio.sleep(3)

            if not transcript:
                raise Exception("STT timeout")

            os.remove(audio_path)

        else:
            logger.info("[STEP] Using cached transcript")
            transcript = download_transcript(record["file_path"])
        
        # ✨ SUMMARY (WITH RETRY + LOGGING)
        summary = None
        max_retry = 3

        for attempt in range(max_retry):
            try:
                logger.info(f"[SUMMARY] Attempt {attempt+1}/{max_retry}")

                summary = summarize_text(transcript)

                # log raw response (optional tapi bagus untuk debug)
                logger.debug(f"[SUMMARY RESPONSE] {summary}")

                if summary.get("success"):
                    logger.info("[SUMMARY SUCCESS]")
                    break

                error_msg = str(summary.get("error"))

                logger.warning(f"[SUMMARY FAILED] Attempt {attempt+1}: {error_msg}")

                # 🔁 Retry hanya untuk error tertentu
                if "503" in error_msg or "UNAVAILABLE" in error_msg:
                    wait_time = 2 ** attempt  # exponential backoff
                    logger.warning(f"[RETRY] Model busy, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    # langsung fail kalau bukan error server
                    raise Exception(error_msg)

            except Exception as e:
                logger.error(f"[SUMMARY EXCEPTION] Attempt {attempt+1}: {str(e)}")

                if attempt == max_retry - 1:
                    raise Exception(f"Gagal generate summary: {str(e)}")

                wait_time = 2 ** attempt
                logger.warning(f"[RETRY EXCEPTION] retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)


        # 🚨 FINAL CHECK
        if not summary or not summary.get("success"):
            raise Exception("Gagal generate summary setelah beberapa percobaan")
      
        # 🧾 REPORT ID
        report_id = record["id"] if record else record_id

        # 📄 PDF
        output_file = base_filename + ".pdf"
        generate_pdf(summary["result"], output_file)

        # 🛑 DOUBLE CHECK (ANTI RACE CONDITION)
        existing_summary = get_existing_summary(report_id)
        if existing_summary:
            logger.info("[SKIP] Summary already exists before upload")

            # hapus file lokal karena tidak jadi dipakai
            os.remove(output_file)

            file_path = existing_summary["file_path"]

            if file_path.startswith("http"):
                logger.warning("[LEGACY DATA] file_path already full URL")
                public_url = file_path
            else:
                public_url = supabase.storage \
                    .from_("summary") \
                    .get_public_url(file_path)

            return {
                "status": "success",
                "source": "cache",
                "report_id": report_id,
                "url": public_url
            }

        # 📦 UPLOAD SUMMARY
        filename = f"{data['tanggal']}/{output_file}"
        public_url = upload_summary(output_file, filename)

        logger.info(f"[UPLOAD] File path (storage): {filename}")
        logger.info(f"[UPLOAD] Public URL (generated): {public_url}")

        # 📝 INSERT SUMMARY
        insert_summary_record(report_id, filename)

        logger.info(f"[DB INSERT] Saved file_path to DB: {filename}")

        # 🧹 HAPUS FILE LOKAL
        os.remove(output_file)

        logger.info("[DONE] Report generated")

        return {
            "status": "success",
            "source": "generated",
            "report_id": report_id,
            "url": public_url
        }

    except Exception as e:
        logger.error(f"[ERROR] {str(e)}", exc_info=True)

        return {
            "status": "error",
            "message": str(e)
        }