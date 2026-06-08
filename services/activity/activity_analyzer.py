import json
import os
import re
import time
from dotenv import load_dotenv
from core.logger import logger

load_dotenv()

# Durasi tiap chunk dalam detik (harus sinkron dengan CHUNK_DURATION_SEC di .env)
CHUNK_DURATION_SEC  = int(os.getenv("CHUNK_DURATION_SEC", "300"))
SILENCE_THRESHOLD_S = int(os.getenv("SILENCE_THRESHOLD_MS", "3000")) / 1000  # detik
MERGE_SHORT_UTT_SEC = float(os.getenv("MERGE_SHORT_UTT_SEC", "1.5"))

# Reuse client & model list dari summarizer
try:
    from services.summarizer.summarizer import client as gemini_client, MODELS as GEMINI_MODELS
    _GEMINI_AVAILABLE = True
except ImportError:
    gemini_client = None
    GEMINI_MODELS  = []
    _GEMINI_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
# DIARIZATION HELPERS (pakai saat utterances tersedia dari AAI)
# ─────────────────────────────────────────────────────────────

def _merge_short_utterances(utterances: list, min_duration_sec: float) -> list:
    """
    Kurangi over-segmentation diarization dengan merge utterance pendek
    ke utterance sebelumnya. Idempotent — aman dipanggil berulang.
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


def _build_timeline(utterances: list, silence_threshold_sec: float) -> list:
    """
    Bangun timeline SPEECH + DIAM dari utterances yang sudah di-sort.
    DIAM = selisih antar-utterance >= silence_threshold_sec.
    """
    if not utterances:
        return []

    timeline = []
    for i, utt in enumerate(utterances):
        if i > 0:
            gap = utt["start_sec"] - utterances[i - 1]["end_sec"]
            if gap >= silence_threshold_sec:
                timeline.append({
                    "type":         "DIAM",
                    "speaker":      None,
                    "start_sec":    round(utterances[i - 1]["end_sec"], 3),
                    "end_sec":      round(utt["start_sec"], 3),
                    "duration_sec": round(gap, 3),
                    "text":         "",
                })

        timeline.append({
            "type":         "SPEECH",
            "speaker":      utt.get("speaker"),
            "start_sec":    utt["start_sec"],
            "end_sec":      utt["end_sec"],
            "duration_sec": utt["duration_sec"],
            "text":         utt.get("text", ""),
        })

    return timeline


def _merge_utterances_from_chunks(chunks: list) -> list:
    """
    Kumpulkan semua utterances dari semua chunk, urutkan berdasarkan start_sec,
    lalu terapkan merge utterance pendek satu kali lagi untuk handle batas chunk.
    """
    all_utterances = []
    for chunk in chunks:
        utts = chunk.get("utterances") or []
        all_utterances.extend(utts)

    # Urutkan berdasarkan timestamp global (sudah di-offset di callback_handler)
    all_utterances.sort(key=lambda u: u.get("start_sec", 0))

    # Satu pass lagi untuk handle utterance pendek di batas antar-chunk
    all_utterances = _merge_short_utterances(all_utterances, MERGE_SHORT_UTT_SEC)

    return all_utterances


def _format_windows_for_gemini(
    timeline: list,
    audio_duration_sec: float,
    window_size: int = CHUNK_DURATION_SEC,
) -> str:
    """
    Pecah timeline ke jendela 5 menit.
    Per jendela: hitung statistik bicara per speaker + contoh teks.
    Output: string ringkas untuk dikirim ke Gemini.
    """
    if not timeline:
        return "(tidak ada data timeline)"

    total_sec   = audio_duration_sec or (timeline[-1]["end_sec"] if timeline else 0)
    num_windows = max(1, int(total_sec / window_size) + (1 if total_sec % window_size else 0))

    def fmt(s: float) -> str:
        return f"{int(s // 60):02d}:{int(s % 60):02d}"

    lines = []
    for w in range(num_windows):
        w_start = w * window_size
        w_end   = min(w_start + window_size, total_sec)

        entries = [
            e for e in timeline
            if e["start_sec"] < w_end and e["end_sec"] > w_start
        ]
        speech = [e for e in entries if e["type"] == "SPEECH"]
        diams  = [e for e in entries if e["type"] == "DIAM"]

        # Waktu bicara per speaker
        spk_time: dict[str, float] = {}
        for e in speech:
            spk = e["speaker"] or "?"
            spk_time[spk] = spk_time.get(spk, 0) + max(
                0,
                min(e["end_sec"], w_end) - max(e["start_sec"], w_start),
            )

        total_diam = sum(
            max(0, min(e["end_sec"], w_end) - max(e["start_sec"], w_start))
            for e in diams
        )

        if spk_time:
            spk_str = ", ".join(
                f"Speaker {k}: {v:.0f}s"
                for k, v in sorted(spk_time.items(), key=lambda x: -x[1])
            )
        else:
            spk_str = "tidak ada speech"

        # Contoh teks 3 speech entry terpanjang
        longest = sorted(speech, key=lambda e: e["duration_sec"], reverse=True)[:3]
        samples = [
            f'  [{e.get("speaker", "?")}] "{(e["text"] or "")[:120].strip()}"'
            for e in longest
            if (e["text"] or "").strip()
        ]

        block = (
            f"[WINDOW {w + 1} | {fmt(w_start)}–{fmt(w_end)}]\n"
            f"  Bicara : {spk_str}\n"
            f"  Diam   : {total_diam:.0f}s"
        )
        if samples:
            block += "\n  Contoh :\n" + "\n".join(samples)

        lines.append(block)

    return "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CONTENT-BASED FORMATTER (fallback jika tidak ada diarization)
# ─────────────────────────────────────────────────────────────

def format_chunks_for_gemini(chunks: list) -> str:
    """
    Konversi list chunk ke teks berformat window per 5 menit.
    Digunakan sebagai fallback jika utterances tidak tersedia.

    Input chunks: [{"chunk_index": 0, "transcript": "..."}, ...]
    """
    if not chunks:
        return "(tidak ada data transkrip)"

    def fmt(sec: float) -> str:
        return f"{int(sec // 60):02d}:{int(sec % 60):02d}"

    lines = []
    for chunk in sorted(chunks, key=lambda c: c.get("chunk_index", 0)):
        idx   = chunk.get("chunk_index", 0)
        text  = (chunk.get("transcript") or "").strip()
        start = idx * CHUNK_DURATION_SEC
        end   = start + CHUNK_DURATION_SEC

        preview = text[:600] + ("..." if len(text) > 600 else "")
        lines.append(
            f"[WINDOW {idx + 1} | {fmt(start)}–{fmt(end)}]\n"
            f"  Teks: \"{preview}\"\n"
        )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# HITUNG RINGKASAN PER AKTIVITAS
# ─────────────────────────────────────────────────────────────

def compute_activity_summary(
    activity_timeline: list,
    total_duration_sec: float,
) -> dict:
    """
    Hitung total durasi dan persentase per jenis aktivitas
    dari activity_timeline yang dihasilkan Gemini.
    """
    totals: dict[str, float] = {
        "CERAMAH":    0.0,
        "TANYA_JAWAB": 0.0,
        "DISKUSI":    0.0,
        "DIAM":       0.0,
    }

    for seg in activity_timeline:
        act = seg.get("activity", "CERAMAH").upper()
        # start_sec/end_sec dari Gemini dalam satuan MENIT (Gemini membaca
        # label window "05:00" sebagai angka menit, bukan detik).
        # Konversi ke detik agar konsisten dengan total_duration_sec.
        dur_min = float(seg.get("end_sec", 0)) - float(seg.get("start_sec", 0))
        dur     = dur_min * 60  # menit → detik
        if act in totals:
            totals[act] += dur
        else:
            totals["CERAMAH"] += dur  # fallback

    base     = total_duration_sec or sum(totals.values()) or 1.0
    dominant = max(totals, key=lambda k: totals[k])

    return {
        "ceramah_sec":      round(totals["CERAMAH"], 1),
        "tanya_jawab_sec":  round(totals["TANYA_JAWAB"], 1),
        "diskusi_sec":      round(totals["DISKUSI"], 1),
        "diam_sec":         round(totals["DIAM"], 1),
        "ceramah_pct":      round(totals["CERAMAH"]     / base * 100, 1),
        "tanya_jawab_pct":  round(totals["TANYA_JAWAB"] / base * 100, 1),
        "diskusi_pct":      round(totals["DISKUSI"]     / base * 100, 1),
        "diam_pct":         round(totals["DIAM"]        / base * 100, 1),
        "dominant":         dominant,
        "activity_timeline": activity_timeline,
    }


# ─────────────────────────────────────────────────────────────
# JSON EXTRACTION HELPER
# ─────────────────────────────────────────────────────────────

def _extract_json_from_response(raw: str) -> str:
    """
    Ekstrak JSON dari response Gemini yang mungkin mengandung teks penjelasan
    sebelum atau sesudah blok JSON.

    Strategi (berurutan, berhenti di yang pertama berhasil):
      1. Cari JSON di dalam markdown code fence: ```json {...} ```
      2. Jika respons langsung diawali '{', gunakan apa adanya
      3. Cari kurung kurawal terluar { ... } di mana saja dalam teks
    """
    raw = raw.strip()

    # Strategi 1 — JSON di dalam ``` ... ``` (dengan atau tanpa label "json")
    code_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw)
    if code_match:
        return code_match.group(1).strip()

    # Strategi 2 — seluruh respons sudah JSON murni
    if raw.startswith("{"):
        return raw

    # Strategi 3 — JSON tertanam di tengah teks, ambil dari { pertama ke } terakhir
    brace_start = raw.find("{")
    brace_end   = raw.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        return raw[brace_start : brace_end + 1]

    # Tidak ada yang cocok — kembalikan raw agar json.loads() error dengan pesan jelas
    return raw


# ─────────────────────────────────────────────────────────────
# KLASIFIKASI AKTIVITAS VIA GEMINI
# ─────────────────────────────────────────────────────────────

def classify_activities(
    chunks: list,
    total_duration_sec: float,
    max_retries: int = 3,
) -> dict:
    """
    Klasifikasikan aktivitas pembelajaran per window 5 menit menggunakan Gemini.

    Dua mode input (dipilih otomatis):
      A. DIARIZATION  — jika chunks memiliki kolom 'utterances' (dari AssemblyAI)
                        → gunakan statistik bicara per speaker per window
      B. CONTENT-BASED — fallback jika tidak ada utterances
                        → klasifikasi dari pola konten teks saja

    Return dict:
      success (bool), ceramah_pct, tanya_jawab_pct, diskusi_pct,
      diam_pct, dominant, activity_timeline[], + _sec variants
    """
    if not _GEMINI_AVAILABLE or not gemini_client:
        return {"success": False, "error": "GEMINI_API_KEY tidak dikonfigurasi"}

    if not chunks:
        return {"success": False, "error": "Tidak ada data chunk transkrip"}

    total_min = round(total_duration_sec / 60, 1)

    # ── Pilih mode berdasarkan ketersediaan diarization data ─────────
    has_diarization = any(c.get("utterances") for c in chunks)

    if has_diarization:
        logger.info(
            f"[ACTIVITY] Mode DIARIZATION | chunks={len(chunks)}"
        )
        all_utterances = _merge_utterances_from_chunks(chunks)
        timeline       = _build_timeline(all_utterances, SILENCE_THRESHOLD_S)
        formatted      = _format_windows_for_gemini(timeline, total_duration_sec)
        mode_desc      = "diarization (speaker stats)"
    else:
        logger.info(
            f"[ACTIVITY] Mode CONTENT-BASED | chunks={len(chunks)}"
        )
        formatted = format_chunks_for_gemini(chunks)
        mode_desc = "content-based (plain text)"

    # ── Bangun prompt ─────────────────────────────────────────────────
    if has_diarization:
        data_label = "DATA PER WINDOW (speaker stats + contoh teks)"
        definitions = (
            "  • CERAMAH    : Didominasi 1 speaker (dosen) yang berbicara panjang. "
            "Speaker lain hampir tidak bicara.\n"
            "  • TANYA_JAWAB: Ada 2+ speaker bergantian, biasanya singkat. "
            "Pola Q&A antara dosen dan mahasiswa.\n"
            "  • DISKUSI    : 2+ speaker bicara dengan porsi lebih merata, "
            "percakapan terbuka.\n"
            "  • DIAM       : Diam total >= 3 detik, atau teks sangat pendek/kosong."
        )
    else:
        data_label = "DATA TRANSKRIP PER WINDOW"
        definitions = (
            "  • CERAMAH    : Teks berupa penjelasan panjang materi oleh pengajar, "
            "monolog, narasi konsep.\n"
            "  • TANYA_JAWAB: Teks mengandung pertanyaan dan jawaban singkat "
            "bolak-balik, interaksi dosen-mahasiswa.\n"
            "  • DISKUSI    : Teks berupa percakapan terbuka, banyak perspektif, "
            "tidak didominasi satu pihak.\n"
            "  • DIAM       : Teks sangat pendek, kosong, atau berisi kata-kata "
            "tidak bermakna (kuis/ujian tertulis/jeda)."
        )

    prompt = f"""Anda adalah analis rekaman kuliah. Rekaman berdurasi {total_duration_sec:.0f} detik (~{total_min} menit).

DEFINISI AKTIVITAS:
{definitions}

{data_label} (5 menit per window):
{formatted}

INSTRUKSI:
1. Analisis setiap window berdasarkan data di atas.
2. Beri 1 label aktivitas per window.
3. Gabungkan window berurutan dengan label SAMA menjadi 1 segmen.
4. Tambahkan deskripsi singkat (10-20 kata) yang menjelaskan konteks aktivitas.
5. Gunakan start_sec dan end_sec dalam satuan detik (bukan menit).

KELUARKAN HANYA JSON valid berikut (tanpa markdown, tanpa komentar):
{{
  "activity_timeline": [
    {{"start_sec": <float>, "end_sec": <float>, "activity": "CERAMAH|TANYA_JAWAB|DISKUSI|DIAM", "description": "<string>"}},
    ...
  ]
}}"""

    last_error = None

    for model_name in GEMINI_MODELS:
        delay = 4

        for attempt in range(max_retries):
            try:
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                raw = response.text.strip()

                # Log awal respons untuk debugging (maks 300 karakter)
                logger.info(
                    f"[ACTIVITY RAW] model={model_name} "
                    f"len={len(raw)} preview={raw[:300]!r}"
                )

                # Ekstrak JSON dari response (tahan terhadap teks penjelasan
                # sebelum/sesudah blok JSON yang kadang dikirim Gemini)
                json_str = _extract_json_from_response(raw)

                try:
                    parsed = json.loads(json_str)
                except json.JSONDecodeError as je:
                    raise ValueError(
                        f"JSON tidak valid setelah ekstraksi: {je} | "
                        f"json_str={json_str[:200]!r}"
                    )

                activity_timeline = parsed.get("activity_timeline", [])

                if not activity_timeline:
                    logger.warning(
                        f"[ACTIVITY] activity_timeline kosong | "
                        f"raw={raw[:300]!r}"
                    )
                    raise ValueError("activity_timeline kosong dari Gemini")

                summary = compute_activity_summary(activity_timeline, total_duration_sec)

                logger.info(
                    f"[ACTIVITY] Klasifikasi selesai | model={model_name} "
                    f"mode={mode_desc} "
                    f"segments={len(activity_timeline)} dominant={summary['dominant']}"
                )

                return {"success": True, **summary}

            except Exception as e:
                last_error = str(e)
                err_low    = last_error.lower()
                retryable  = any(k in err_low for k in [
                    "overloaded", "rate limit", "too many requests",
                    "resource exhausted", "503", "429"
                ])

                if retryable and attempt < max_retries - 1:
                    logger.warning(
                        f"[ACTIVITY RETRY] model={model_name} "
                        f"attempt={attempt + 1} sleep={delay}s"
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue

                logger.warning(
                    f"[ACTIVITY SKIP] model={model_name} | {last_error[:120]}"
                )
                break

    logger.error(f"[ACTIVITY FAILED] semua model gagal | {last_error}")
    return {"success": False, "error": f"Semua model Gemini gagal: {last_error}"}
