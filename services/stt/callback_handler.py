import os
from dotenv import load_dotenv
from repositories.supabase_client import supabase
from services.stt.stt_service import fetch_transcript_aai, extract_chunk_data
from services.stt.merge_service import merge_transcript
from services.stt.worker import mark_chunk_failed
from core.logger import logger

load_dotenv()

CHUNK_DURATION_SEC  = int(os.getenv("CHUNK_DURATION_SEC", "300"))
MERGE_SHORT_UTT_SEC = float(os.getenv("MERGE_SHORT_UTT_SEC", "1.5"))


def _merge_short_utterances(utterances: list, min_duration_sec: float) -> list:
    """
    Kurangi over-segmentation diarization dengan menggabungkan utterance
    yang terlalu pendek ke utterance sebelumnya.

    Aturan merge:
    - Utterance dengan duration < min_duration_sec digabung ke utterance sebelumnya
    - Hanya merge jika jarak ke utterance sebelumnya < 2 detik (bukan jeda panjang)
    """
    if not utterances or min_duration_sec <= 0:
        return utterances

    merged = []
    for utt in utterances:
        if (
            merged
            and utt["duration_sec"] < min_duration_sec
            and (utt["start_sec"] - merged[-1]["end_sec"]) < 2.0
        ):
            prev = merged[-1]
            combined = (prev["text"] or "").rstrip() + " " + (utt["text"] or "").lstrip()
            prev["text"]         = combined.strip()
            prev["end_sec"]      = utt["end_sec"]
            prev["duration_sec"] = round(prev["end_sec"] - prev["start_sec"], 3)
            if "words" in prev and "words" in utt:
                prev["words"] = prev["words"] + utt["words"]
        else:
            merged.append(dict(utt))

    return merged


def handle_assemblyai_callback(transcript_id: str) -> bool:
    """
    Dipanggil saat webhook dari AssemblyAI tiba.

    Alur:
      1. Cari chunk di DB berdasarkan task_id = transcript_id
      2. Fetch hasil lengkap dari AssemblyAI API
      3. Ekstrak utterances (dengan offset dari chunk_index)
      4. Simpan plain text + utterances JSONB ke audio_chunks
      5. Jika semua chunk selesai → trigger merge_transcript
    """
    try:
        logger.info(f"[AAI CALLBACK] Received | transcript_id={transcript_id}")

        # ── 1. Cari chunk ────────────────────────────────────────────
        chunk_res = (
            supabase.table("audio_chunks")
            .select("id, report_id, chunk_index, status, retry_count")
            .eq("task_id", transcript_id)
            .execute()
        )

        if not chunk_res.data:
            logger.warning(f"[AAI CALLBACK] Chunk not found | tid={transcript_id}")
            return False

        chunk     = chunk_res.data[0]
        chunk_id  = chunk["id"]
        report_id = chunk["report_id"]
        chunk_idx = chunk.get("chunk_index", 0)

        # Idempotent — skip jika sudah done
        if chunk["status"] == "done":
            logger.info(f"[AAI CALLBACK] Chunk {chunk_id} already done, skip")
            return False

        # ── 2. Offset dari chunk_index ───────────────────────────────
        offset_sec = chunk_idx * CHUNK_DURATION_SEC
        chunk_name = f"chunk_{chunk_idx:03d}.wav"

        # ── 3. Fetch hasil dari AssemblyAI ───────────────────────────
        data       = fetch_transcript_aai(transcript_id)
        chunk_data = extract_chunk_data(data, offset_sec, chunk_name)

        logger.info(
            f"[AAI CALLBACK] chunk={chunk_id} idx={chunk_idx} "
            f"utterances={len(chunk_data['utterances'])} "
            f"model={chunk_data.get('model_used', '?')} "
            f"duration={chunk_data['duration_sec']:.1f}s"
        )

        # ── 4. Handle error dari AssemblyAI ─────────────────────────
        if chunk_data["status"] == "failed":
            mark_chunk_failed(
                chunk_id, report_id, chunk.get("retry_count", 0),
                chunk_data.get("error", "AssemblyAI error"),
            )
            logger.error(
                f"[AAI CALLBACK] chunk={chunk_id} FAILED: {chunk_data.get('error')}"
            )
            return False

        # ── 5. Merge short utterances per chunk ──────────────────────
        utterances        = chunk_data["utterances"]
        merged_utterances = _merge_short_utterances(utterances, MERGE_SHORT_UTT_SEC)

        before = len(utterances)
        after  = len(merged_utterances)
        if before != after:
            logger.info(
                f"[AAI CALLBACK] Short-utt merge chunk {chunk_id}: "
                f"{before} → {after} utterances"
            )

        # Plain text (untuk transcript field & backward compat)
        plain_text = " ".join(
            u["text"] for u in merged_utterances if u.get("text")
        ).strip()

        # ── 6. Simpan ke DB ──────────────────────────────────────────
        supabase.table("audio_chunks").update({
            "status":     "done",
            "transcript": plain_text,
            "utterances": merged_utterances,   # JSONB — sudah di-offset
        }).eq("id", chunk_id).execute()

        logger.info(
            f"[AAI CALLBACK] chunk={chunk_id} saved | "
            f"text_len={len(plain_text)} chars | "
            f"utterances={len(merged_utterances)}"
        )

        # ── 7. Cek apakah semua chunk selesai ────────────────────────
        remaining = (
            supabase.table("audio_chunks")
            .select("id")
            .eq("report_id", report_id)
            .neq("status", "done")
            .execute()
        )

        if remaining.data:
            logger.info(
                f"[AAI CALLBACK] {len(remaining.data)} chunk(s) still pending "
                f"for report {report_id}"
            )
            return False

        # ── 8. Lock report → trigger merge ──────────────────────────
        lock = (
            supabase.table("reports")
            .update({"status": "processing"})
            .eq("id", report_id)
            .in_("status", ["chunking", "processing"])
            .execute()
        )

        if not lock.data:
            logger.info(
                f"[AAI CALLBACK] report {report_id} already locked, skip merge"
            )
            return False

        logger.info(
            f"[AAI CALLBACK] All chunks done → merge report {report_id}"
        )

        result = merge_transcript(report_id)

        if not result:
            logger.error(f"[AAI CALLBACK] Merge failed for report {report_id}")
            supabase.table("reports").update({
                "status": "failed"
            }).eq("id", report_id).execute()
            return False

        logger.info(f"[AAI CALLBACK] Merge done for report {report_id}")
        return True

    except Exception as e:
        logger.error(
            f"[AAI CALLBACK ERROR] tid={transcript_id}: {e}", exc_info=True
        )
        return False
