import asyncio
import os
from datetime import datetime

from repositories.supabase_client import supabase
from services.storage.storage_service import download_transcript, upload_summary
from services.summarizer.summarizer import summarize_text
from services.report.report_service import generate_combined_pdf
from services.cleanup_service import cleanup_chunks
from services.rps.rps_service import (
    get_meeting_week,
    get_rps_for_week,
    get_all_rps_for_matkul,
    get_nama_matkul,
)
from services.rps.rag_analyzer import analyze_rps
from services.activity.activity_analyzer import classify_activities
from core.logger import logger


# batasi concurrency
semaphore    = asyncio.Semaphore(2)
IDLE_TIMEOUT = 300  # 5 menit


def _fetch_chunks(report_id: int) -> tuple[list[dict], float]:
    """
    Ambil semua chunk transkrip dari DB untuk satu report.
    Return (chunks, total_duration_sec).

    Jika AssemblyAI dipakai, setiap chunk juga memiliki kolom 'utterances' JSONB
    yang digunakan oleh classify_activities() untuk mode diarization.
    """
    from services.activity.activity_analyzer import CHUNK_DURATION_SEC

    res = (
        supabase.table("audio_chunks")
        .select("chunk_index, transcript, utterances")   # utterances = diarization data
        .eq("report_id", report_id)
        .eq("status", "done")
        .order("chunk_index")
        .execute()
    )
    chunks = res.data or []
    total_duration_sec = len(chunks) * CHUNK_DURATION_SEC
    return chunks, float(total_duration_sec)


def _insert_activity_stats(report: dict, report_id: int, pertemuan_ke: int, activity_result: dict):
    """
    Simpan statistik aktivitas ke tabel activity_stats.
    Kaitkan ke jadwal_kuliah melalui monitoring_id yang ada di reports.
    """
    try:
        monitoring_id = report.get("monitoring_id")
        jadwal_id     = None

        # Ambil jadwal_id dari tabel monitoring
        if monitoring_id:
            mon_res = (
                supabase.table("monitoring")
                .select("jadwal_id")
                .eq("id", monitoring_id)
                .single()
                .execute()
            )
            if mon_res.data:
                jadwal_id = mon_res.data.get("jadwal_id")

        payload = {
            "report_id":           report_id,
            "monitoring_id":       monitoring_id,
            "jadwal_id":           jadwal_id,
            "pertemuan_ke":        pertemuan_ke,
            "tanggal":             str(report.get("tanggal")) if report.get("tanggal") else None,
            "kode_matkul":         report.get("kode_matkul"),
            "dominant_activity":   activity_result.get("dominant"),
            "ceramah_pct":         activity_result.get("ceramah_pct"),
            "tanya_jawab_pct":     activity_result.get("tanya_jawab_pct"),
            "diskusi_pct":         activity_result.get("diskusi_pct"),
            "diam_pct":            activity_result.get("diam_pct"),
            "ceramah_sec":         activity_result.get("ceramah_sec"),
            "tanya_jawab_sec":     activity_result.get("tanya_jawab_sec"),
            "diskusi_sec":         activity_result.get("diskusi_sec"),
            "diam_sec":            activity_result.get("diam_sec"),
            "activity_timeline":   activity_result.get("activity_timeline", []),
        }

        supabase.table("activity_stats").insert(payload).execute()
        logger.info(
            f"[ACTIVITY STATS] Saved | report={report_id} "
            f"pertemuan={pertemuan_ke} jadwal_id={jadwal_id} "
            f"dominant={activity_result.get('dominant')}"
        )

    except Exception as e:
        # Soft-fail: jangan hentikan pipeline karena insert stats gagal
        logger.warning(f"[ACTIVITY STATS] Insert failed (non-fatal): {e}")


async def process_summary(report):
    async with semaphore:
        report_id  = report["id"]
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

            if not fresh.data:
                raise Exception(f"Report {report_id} tidak ditemukan di DB")
            path = fresh.data.get("transcript_path")
            if not path:
                raise Exception("Transcript path not found")

            transcript = download_transcript(path)

            # ── 2. Hitung pertemuan_ke dari tanggal ──────────────────
            tanggal     = report.get("tanggal")
            kode_matkul = report.get("kode_matkul")

            if not tanggal or not kode_matkul:
                raise Exception(
                    "Field tanggal/kode_matkul tidak tersedia di report"
                )

            pertemuan_ke = get_meeting_week(str(tanggal))
            logger.info(f"[RAG] tanggal={tanggal} → pertemuan_ke={pertemuan_ke}")

            # ── 3. Fetch SEMUA RPS untuk matkul ini ─────────────────
            rps_semua = get_all_rps_for_matkul(kode_matkul)

            if not rps_semua:
                raise Exception(
                    f"Tidak ada data RPS untuk '{kode_matkul}'. "
                    f"Harap isi tabel rps_pertemuan terlebih dahulu."
                )

            pertemuan_numbers = [r.get("pertemuan_ke") for r in rps_semua]
            if pertemuan_ke not in pertemuan_numbers:
                raise Exception(
                    f"RPS pertemuan ke-{pertemuan_ke} untuk '{kode_matkul}' belum tersedia. "
                    f"Pertemuan yang ada: {sorted(pertemuan_numbers)}"
                )

            rps_target = next(
                (r for r in rps_semua if r.get("pertemuan_ke") == pertemuan_ke), {}
            )

            # ── 4. Fetch nama lengkap mata kuliah ────────────────────
            nama_matkul = get_nama_matkul(kode_matkul)

            # ── 5. Fetch chunks untuk klasifikasi aktivitas ──────────
            chunks, total_duration_sec = _fetch_chunks(report_id)

            logger.info(
                f"[SUMMARY WORKER] "
                f"matkul={nama_matkul} pertemuan={pertemuan_ke} "
                f"chunks={len(chunks)} duration={total_duration_sec:.0f}s"
            )

            # ── 6. RONDE 1 — Summarize + Klasifikasi Aktivitas (paralel)
            logger.info(f"[RONDE 1] summarize + classify_activities | report={report_id}")

            summary_result, activity_result = await asyncio.gather(
                asyncio.to_thread(summarize_text, transcript, nama_matkul),
                asyncio.to_thread(classify_activities, chunks, total_duration_sec),
            )

            # Validasi summary
            if not summary_result.get("success"):
                raise Exception(summary_result.get("error", "Summarize gagal"))

            _ring         = summary_result.get("ringkasan", {})
            ringkasan_val = _ring if isinstance(_ring, dict) else {"pembuka": "", "poin": []}
            detail_val    = summary_result.get("detail",    "")

            # Fallback: kalau detail kosong coba ambil dari key lama
            if not detail_val:
                detail_val = (
                    summary_result.get("result")
                    or summary_result.get("abstrak")
                    or summary_result.get("summary")
                    or ""
                )

            # Aktivitas — soft fail (tidak stop pipeline)
            if not activity_result.get("success"):
                logger.warning(
                    f"[ACTIVITY WARN] Klasifikasi gagal (dilanjutkan tanpa aktivitas): "
                    f"{activity_result.get('error')}"
                )
                activity_result = None

            # ── 7. RONDE 2 — Analisis RPS + Metode (menggunakan activity_summary)
            logger.info(f"[RONDE 2] analyze_rps | report={report_id}")

            analysis_raw = await asyncio.to_thread(
                analyze_rps,
                transcript,
                rps_semua,
                pertemuan_ke,
                activity_result,   # None jika aktivitas gagal
            )

            if not analysis_raw.get("success"):
                raise Exception(analysis_raw.get("error", "Analisis RPS gagal"))

            # ── 8. Susun dict analisis lengkap untuk PDF ─────────────
            analysis = {
                "pertemuan_ke":         pertemuan_ke,
                "materi_pembelajaran":  rps_target.get("materi_pembelajaran", "-"),
                "pengalaman_rps":       rps_target.get("pengalaman_pembelajaran_mahasiswa", "-"),
                "kesesuaian":           analysis_raw.get("kesesuaian", "-"),
                "pertemuan_terdeteksi": analysis_raw.get("pertemuan_terdeteksi", "-"),
                "status_waktu":         analysis_raw.get("status_waktu", "-"),
                "penjelasan":           analysis_raw.get("penjelasan", "-"),
                "catatan":              analysis_raw.get("catatan", "-"),
                "metode_dominan":       analysis_raw.get("metode_dominan", "-"),
                "kesesuaian_metode":    analysis_raw.get("kesesuaian_metode", "-"),
                "penjelasan_metode":    analysis_raw.get("penjelasan_metode", "-"),
                # aktivitas summary (untuk bar di PDF)
                "activity":             activity_result,
            }

            # ── 9. Generate PDF 2 halaman ────────────────────────────
            os.makedirs("temp", exist_ok=True)
            local_path = f"temp/report_{report_id}.pdf"

            generate_combined_pdf(
                detail_val,
                analysis,
                local_path,
                nama_matkul=nama_matkul,
                pertemuan_ke=pertemuan_ke,
                kode_kelas=str(report.get("kelas")        or ""),
                tanggal=str(report.get("tanggal")         or ""),
                kode_dosen=str(report.get("kode_dosen")   or ""),
                kode_matkul=str(report.get("kode_matkul") or ""),
                ringkasan=ringkasan_val,
                catatan=analysis.get("catatan", "-"),
            )

            # ── 10. Upload PDF ────────────────────────────────────────
            storage_path = f"report_{report_id}.pdf"
            logger.info(f"[UPLOAD] uploading combined PDF → {storage_path}")
            upload_summary(local_path, storage_path)

            # ── 11. Update DB ─────────────────────────────────────────
            update_payload: dict = {
                "status":         "done",
                "summary_path":   storage_path,
                "error_message":  None,
            }

            # Simpan hasil analisis RAG ke kolom baru (dibutuhkan laporan evaluasi)
            update_payload["kesesuaian_materi"] = analysis_raw.get("kesesuaian",        "-")
            update_payload["status_waktu"]      = analysis_raw.get("status_waktu",      "-")
            update_payload["kesesuaian_metode"] = analysis_raw.get("kesesuaian_metode", "-")

            if activity_result:
                # Simpan ringkasan aktivitas (tanpa activity_timeline panjang)
                update_payload["activity_summary"] = {
                    "dominant":         activity_result.get("dominant"),
                    "ceramah_pct":      activity_result.get("ceramah_pct"),
                    "tanya_jawab_pct":  activity_result.get("tanya_jawab_pct"),
                    "diskusi_pct":      activity_result.get("diskusi_pct"),
                    "diam_pct":         activity_result.get("diam_pct"),
                    "ceramah_sec":      activity_result.get("ceramah_sec"),
                    "tanya_jawab_sec":  activity_result.get("tanya_jawab_sec"),
                    "diskusi_sec":      activity_result.get("diskusi_sec"),
                    "diam_sec":         activity_result.get("diam_sec"),
                    "activity_timeline": activity_result.get("activity_timeline", []),
                }

            supabase.table("reports").update(update_payload).eq("id", report_id).execute()

            # ── 12. Simpan ke activity_stats ──────────────────────────
            if activity_result:
                _insert_activity_stats(report, report_id, pertemuan_ke, activity_result)

            logger.info(
                f"[SUMMARY DONE] Report {report_id} | "
                f"target=pertemuan-{pertemuan_ke} "
                f"terdeteksi=pertemuan-{analysis['pertemuan_terdeteksi']} "
                f"status={analysis['status_waktu']} "
                f"kesesuaian={analysis['kesesuaian']} "
                f"kesesuaian_metode={analysis['kesesuaian_metode']} "
                f"dominant={analysis['metode_dominan']}"
            )

        except Exception as e:
            logger.error(f"[SUMMARY ERROR] Report {report_id}: {e}", exc_info=True)

            supabase.table("reports").update({
                "status":        "failed",
                "error_message": str(e),
            }).eq("id", report_id).execute()

        finally:
            if local_path and os.path.exists(local_path):
                os.remove(local_path)

            # Hapus audio_chunks dari DB setelah semua data sudah dipakai
            # (transcript sudah di-upload, utterances sudah dipakai classify_activities)
            try:
                cleanup_chunks(report_id)
            except Exception as ce:
                logger.warning(f"[CLEANUP WARN] report={report_id}: {ce}")


async def run_summary_worker():
    logger.info("[SUMMARY WORKER] Started")

    idle_since = datetime.utcnow()

    while True:
        try:
            res = (
                supabase.table("reports")
                .select("id, tanggal, kode_matkul, kelas, kode_dosen, monitoring_id, status")
                .eq("status", "transcribed")
                .limit(3)
                .execute()
            )

            reports = res.data or []

            if reports:
                idle_since = datetime.utcnow()
                tasks      = []

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
                        continue

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
