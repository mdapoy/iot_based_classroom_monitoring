from repositories.supabase_client import supabase
from services.storage.google_drive import get_file_from_gdrive_flexible
from services.report.report_service import get_existing_summary
from services.worker.worker_manager import start_stt_worker, start_summary_worker
from utils.metadata_parser import parse_filename
from core.logger import logger

TABLE = "reports"


async def generate_report(data: dict):
    try:
        logger.info(f"[START] Generate report | raw_data={data}")

        # CLEAN
        data = {k: str(v).strip() for k, v in data.items()}
        logger.info(f"[CLEAN] Data normalized | data={data}")

        # =========================
        # 1. CHECK EXISTING REPORT
        # =========================
        logger.info("[CACHE] Checking existing report...")

        existing = supabase.table(TABLE).select("*").match({
            "tanggal": data["tanggal"],
            "jam": data["jam"],
            "ruangan": data["ruangan"],
            "kode_matkul": data["kode_matkul"],
            "kode_dosen": data["kode_dosen"],
            "kelas": data["kelas"]
        }).execute()

        record = existing.data[0] if existing.data else None

        # =========================
        # 2. IF REPORT EXISTS
        # =========================
        if record:
            report_id = record["id"]
            logger.info(f"[CACHE] Found existing report | report_id={report_id}")

            # ---- cek summary ----
            existing_summary = get_existing_summary(report_id)
            if existing_summary:
                logger.info(f"[CACHE HIT] Summary found | file_path={existing_summary['file_path']}")

                public_url = supabase.storage \
                    .from_("summary") \
                    .get_public_url(existing_summary["file_path"])

                return {
                    "status": "success",
                    "source": "cache",
                    "url": public_url,
                    "report_id": report_id
                }

            # ---- cek transcript (dari audio_chunks) ----
            transcript_path = record.get("transcript_path")

            if transcript_path:
                logger.info(f"[CACHE HIT] Transcript exists | path={transcript_path}")

                # 🔒 kalau lagi summarizing
                if record["status"] == "summarizing":
                    logger.info("[SKIP] Already summarizing")
                    return {
                        "status": "processing",
                        "report_id": report_id
                    }

                # 🔥 trigger summary ulang
                supabase.table("reports").update({
                    "status": "transcribed",
                    "error_message": None
                }).eq("id", report_id).execute()

                await start_summary_worker()

                return {
                    "status": "processing_summary",
                    "report_id": report_id
                }

            # =========================
            # FALLBACK CEK audio_chunks
            # =========================
            chunks = supabase.table("audio_chunks") \
                .select("chunk_index, transcript, status") \
                .eq("report_id", report_id) \
                .order("chunk_index") \
                .execute()

            chunk_data = chunks.data or []

            if chunk_data:
                all_done = all(c["status"] == "done" for c in chunk_data)

                if all_done:
                    # 🔒 jangan ganggu kalau masih proses STT
                    if record["status"] in ["chunking", "processing"]:
                        logger.info("[SKIP] Still processing STT")
                        return {
                            "status": "processing",
                            "report_id": report_id
                        }

                    # 🔒 kalau lagi summarizing
                    if record["status"] == "summarizing":
                        logger.info("[SKIP] Already summarizing")
                        return {
                            "status": "processing",
                            "report_id": report_id
                        }

                    logger.info("[RECOVERY] Transcript complete → trigger summary worker")

                    supabase.table("reports").update({
                        "status": "transcribed",
                        "error_message": None
                    }).eq("id", report_id).execute()

                    return {
                        "status": "processing_summary",
                        "report_id": report_id
                    }

                else:
                    logger.info("[WAIT] Transcript not complete yet")

                    return {
                        "status": "processing",
                        "report_id": report_id
                    }

            # ---- belum ada transcript sama sekali ----
            logger.warning("[CACHE MISS] No transcript found, continue processing")

            return {
                "status": "processing",
                "report_id": report_id
            }

        # =========================
        # 3. IF REPORT NOT EXISTS
        # =========================
        logger.info("[CACHE] No existing report found")

        # NORMALIZE JAM
        jam = data["jam"][:5]
        logger.info(f"[NORMALIZE] Jam normalized | jam={jam}")

        base_filename = f"{data['tanggal']}_{jam}_{data['ruangan']}_{data['kode_matkul']}_{data['kode_dosen']}_{data['kelas']}"
        logger.info(f"[FILENAME] Generated base filename | base_filename={base_filename}")

        # DOWNLOAD AUDIO
        logger.info(f"[DOWNLOAD] Attempting to download audio | filename={base_filename}")

        audio_path, real_filename, used_folder_id = get_file_from_gdrive_flexible(base_filename)

        if not audio_path:
            logger.error(f"[DOWNLOAD FAILED] File not found | searched_folder={used_folder_id}")
            raise Exception("File tidak ditemukan di GDrive")

        logger.info(f"[DOWNLOAD SUCCESS] audio_path={audio_path} | real_filename={real_filename}")

        # PARSE METADATA
        meta = parse_filename(real_filename)
        logger.info(f"[PARSE] Metadata extracted | meta={meta}")

        # INSERT REPORT
        logger.info("[DB] Inserting new report...")
        insert_res = supabase.table(TABLE).insert({
            **meta,
            "status": "pending"
        }).execute()

        report_id = insert_res.data[0]["id"]
        logger.info(f"[DB SUCCESS] Report inserted | report_id={report_id}")

        # CHUNK AUDIO
        logger.info("[CHUNKING] Splitting audio...")
        from services.chunker import split_audio

        chunks = split_audio(audio_path)

        if not chunks:
            logger.error("[CHUNKING FAILED] No chunks generated")
            raise Exception("Gagal melakukan chunking audio")

        logger.info(f"[CHUNKING SUCCESS] Total chunks={len(chunks)}")

        # INSERT CHUNKS
        logger.info("[DB] Inserting audio chunks...")
        for i, chunk_path in enumerate(chunks):
            supabase.table("audio_chunks").insert({
                "report_id": report_id,
                "chunk_index": i,
                "chunk_path": chunk_path,
                "status": "pending"
            }).execute()

        logger.info("[DB SUCCESS] All chunks inserted")

        # UPDATE STATUS
        supabase.table("reports").update({
            "status": "chunking"
        }).eq("id", report_id).execute()

        logger.info(f"[DONE] Report processing started | report_id={report_id}")

        await start_stt_worker()
        await start_summary_worker()

        return {
            "status": "processing",
            "report_id": report_id
        }

    except Exception as e:
        logger.error(f"[ERROR] Generate report failed | error={str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }