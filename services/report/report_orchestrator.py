from repositories.supabase_client import supabase
from services.report.report_service import get_existing_summary
from services.worker.worker_manager import start_summary_worker, start_download_worker
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

        # Tidak gunakan ruangan di cache-check karena formatnya bisa beda
        # antara request (KU3.04.17) dan nama file GDrive (KU3-0417).
        # Kombinasi tanggal + jam + matkul + dosen + kelas sudah unik.
        existing = supabase.table(TABLE).select("id, status, transcript_path").match({
            "tanggal":    data["tanggal"],
            "jam":        data["jam"],
            "kode_matkul": data["kode_matkul"],
            "kode_dosen": data["kode_dosen"],
            "kelas":      data["kelas"],
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
                    .create_signed_url(existing_summary["file_path"], 3600)["signedURL"]

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

        # NORMALIZE JAM — format GDrive menggunakan colon (13:30), bukan dash
        jam = data["jam"][:5]   # "13:30:00" → "13:30"
        logger.info(f"[NORMALIZE] Jam normalized | jam={jam}")

        # Search key dipecah 2 karena format ruangan bisa berbeda antara DB dan GDrive
        # (misal: DB simpan "KU3.04.17" tapi GDrive pakai "KU3-0417")
        # Strategi: cari file yang mengandung tanggal+jam DAN matkul+dosen+kelas
        search_prefix = f"{data['tanggal']}_{jam}"                                      # "2026-04-30_13:30"
        search_suffix = f"_{data['kode_matkul']}_{data['kode_dosen']}_{data['kelas']}"  # "_AZK1GAB3_MFC_TK-48-GAB1"
        base_filename = search_prefix + search_suffix   # untuk logging saja

        logger.info(f"[FILENAME] Generated base filename | base_filename={base_filename}")

        # INSERT REPORT dengan data dari request
        # Metadata lengkap (ruangan, dll) akan di-update oleh download worker
        # setelah mendapat nama file asli dari GDrive
        logger.info("[DB] Inserting new report...")
        insert_res = supabase.table(TABLE).insert({
            "tanggal":     data["tanggal"],
            "jam":         data["jam"],
            "kode_matkul": data["kode_matkul"],
            "kode_dosen":  data["kode_dosen"],
            "kelas":       data["kelas"],
            "ruangan":     data.get("ruangan", ""),
            "status":      "pending",
        }).execute()

        report_id = insert_res.data[0]["id"]
        logger.info(f"[DB SUCCESS] Report inserted | report_id={report_id}")

        # KICK OFF background download worker — return langsung ke client
        logger.info(f"[WORKER] Starting download worker | report_id={report_id}")
        await start_download_worker(report_id, search_prefix, search_suffix)

        logger.info(f"[DONE] Download worker started | report_id={report_id}")

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