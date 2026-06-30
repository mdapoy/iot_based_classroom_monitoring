# services/worker/download_worker.py
import asyncio
import os
import time
from repositories.supabase_client import supabase
from core.logger import logger


def _fmt_size(path: str) -> str:
    """Return ukuran file dalam format human-readable (KB / MB)."""
    try:
        size = os.path.getsize(path)
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / 1024:.1f} KB"
    except Exception:
        return "unknown size"


def _fmt_elapsed(seconds: float) -> str:
    """Return elapsed time dalam format m:ss atau s."""
    if seconds >= 60:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"


async def run_download_worker(report_id: int, search_prefix: str, search_suffix: str):
    """
    Background worker: download audio dari GDrive → split chunks → kick off STT + summary.
    Semua operasi blocking (I/O + ffmpeg) dijalankan di thread pool agar
    tidak memblokir event loop utama FastAPI.
    """
    from services.storage.google_drive import get_file_from_gdrive_flexible
    from services.chunker import split_audio, normalize_audio

    t_total = time.time()

    logger.info(
        f"[DOWNLOAD WORKER] ========== START ========== | report_id={report_id}"
    )
    logger.info(
        f"[DOWNLOAD WORKER] Target: prefix='{search_prefix}' suffix='{search_suffix}'"
    )

    try:
        # ── STEP 1: Search + Download dari GDrive ────────────────────
        logger.info(f"[DOWNLOAD WORKER] [1/5] Mencari file di GDrive...")
        t1 = time.time()

        audio_path, real_filename, used_folder_id = await asyncio.to_thread(
            get_file_from_gdrive_flexible, search_prefix, search_suffix
        )

        if not audio_path:
            raise Exception(
                f"File tidak ditemukan di GDrive | "
                f"prefix={search_prefix} suffix={search_suffix}"
            )

        elapsed1 = _fmt_elapsed(time.time() - t1)
        file_size = _fmt_size(audio_path)

        logger.info(
            f"[DOWNLOAD WORKER] [1/5] SELESAI | "
            f"file={real_filename} | size={file_size} | waktu={elapsed1}"
        )

        # ── STEP 1b: Update metadata dari real filename ───────────────
        meta = {}
        try:
            from utils.metadata_parser import parse_filename
            meta = parse_filename(real_filename)
            supabase.table("reports").update(meta).eq("id", report_id).execute()
            logger.info(f"[DOWNLOAD WORKER] Metadata di-update | ruangan={meta.get('ruangan')}")
        except Exception as meta_err:
            logger.warning(f"[DOWNLOAD WORKER] Gagal parse/update metadata: {meta_err}")

        # ── STEP 1c: Resolve monitoring_id ───────────────────────────
        try:
            from repositories.cache import get_all_jadwal
            jadwal = next(
                (j for j in get_all_jadwal()
                 if j.get("kode_mata_kuliah") == meta.get("kode_matkul")
                 and j.get("dosen_utama")     == meta.get("kode_dosen")
                 and j.get("kelas")           == meta.get("kelas")),
                None
            )
            if jadwal:
                mon_res = (
                    supabase.table("monitoring")
                    .select("id")
                    .eq("jadwal_id", jadwal["id"])
                    .eq("tanggal", meta.get("tanggal"))
                    .single()
                    .execute()
                )
                if mon_res.data:
                    supabase.table("reports").update(
                        {"monitoring_id": mon_res.data["id"]}
                    ).eq("id", report_id).execute()
                    logger.info(
                        f"[DOWNLOAD WORKER] monitoring_id di-set | "
                        f"monitoring_id={mon_res.data['id']}"
                    )
                else:
                    logger.warning(
                        f"[DOWNLOAD WORKER] monitoring entry tidak ditemukan | "
                        f"jadwal_id={jadwal['id']} tanggal={meta.get('tanggal')}"
                    )
            else:
                logger.warning(
                    f"[DOWNLOAD WORKER] jadwal tidak ditemukan untuk resolve monitoring_id | "
                    f"kode_matkul={meta.get('kode_matkul')} kode_dosen={meta.get('kode_dosen')} "
                    f"kelas={meta.get('kelas')}"
                )
        except Exception as mon_err:
            logger.warning(f"[DOWNLOAD WORKER] Gagal resolve monitoring_id: {mon_err}")

        # ── STEP 2: Normalisasi audio (loudnorm EBU R128) ────────────
        logger.info(f"[DOWNLOAD WORKER] [2/5] Normalisasi audio (loudnorm)...")
        t_norm = time.time()

        try:
            normalized_path = await asyncio.to_thread(normalize_audio, audio_path)
            audio_to_chunk  = normalized_path
            elapsed_norm    = _fmt_elapsed(time.time() - t_norm)
            logger.info(
                f"[DOWNLOAD WORKER] [2/5] SELESAI normalisasi | "
                f"output={os.path.basename(normalized_path)} | waktu={elapsed_norm}"
            )
        except Exception as norm_err:
            logger.warning(
                f"[DOWNLOAD WORKER] Normalisasi gagal, lanjut dengan audio asli | {norm_err}"
            )
            audio_to_chunk = audio_path

        # ── STEP 3: Update status DB ──────────────────────────────────
        supabase.table("reports").update(
            {"status": "chunking"}
        ).eq("id", report_id).execute()

        # ── STEP 4: Split audio dengan ffmpeg ────────────────────────
        logger.info(f"[DOWNLOAD WORKER] [3/5] Memulai chunking audio (ffmpeg)...")
        t2 = time.time()

        chunks = await asyncio.to_thread(split_audio, audio_to_chunk, report_id=report_id)

        if not chunks:
            raise Exception("Gagal melakukan chunking audio")

        elapsed2 = _fmt_elapsed(time.time() - t2)
        logger.info(
            f"[DOWNLOAD WORKER] [3/5] SELESAI | "
            f"total_chunks={len(chunks)} | waktu={elapsed2}"
        )

        # ── STEP 5: Insert chunks ke DB ───────────────────────────────
        logger.info(
            f"[DOWNLOAD WORKER] [4/5] Menyimpan {len(chunks)} chunk ke database..."
        )
        t3 = time.time()

        for i, chunk_path in enumerate(chunks):
            supabase.table("audio_chunks").insert({
                "report_id":   report_id,
                "chunk_index": i,
                "chunk_path":  chunk_path,
                "status":      "pending"
            }).execute()
            logger.info(
                f"[DOWNLOAD WORKER] [4/5] Chunk {i + 1}/{len(chunks)} tersimpan"
            )

        elapsed3 = _fmt_elapsed(time.time() - t3)
        logger.info(
            f"[DOWNLOAD WORKER] [4/5] SELESAI | waktu={elapsed3}"
        )

        # ── STEP 6: Kick off STT + Summary workers ────────────────────
        logger.info(f"[DOWNLOAD WORKER] [5/5] Menjalankan STT worker + Summary worker...")

        from services.worker.worker_manager import start_stt_worker
        await start_stt_worker()

        total_elapsed = _fmt_elapsed(time.time() - t_total)
        logger.info(
            f"[DOWNLOAD WORKER] ========== DONE ========== | "
            f"report_id={report_id} | total_waktu={total_elapsed}"
        )

    except Exception as e:
        total_elapsed = _fmt_elapsed(time.time() - t_total)
        logger.error(
            f"[DOWNLOAD WORKER] ========== FAILED ========== | "
            f"report_id={report_id} | error={str(e)} | waktu={total_elapsed}",
            exc_info=True
        )
        supabase.table("reports").update({
            "status":        "failed",
            "error_message": str(e)
        }).eq("id", report_id).execute()
