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
# CONTENT-RICH FALLBACK (dipakai saat diarization cuma 1 speaker unik)
# ─────────────────────────────────────────────────────────────

def _build_turns_from_utterances(utterances: list, gap_threshold_sec: float = 2.0) -> list:
    """
    Pecah tiap utterance jadi "turn" (segmen bicara berkelanjutan) di titik
    manapun jeda antar-kata >= gap_threshold_sec, memakai data 'words'.

    Tujuannya: utterance yang di-diarize sebagai "1 speaker ngomong ratusan
    detik nonstop" mungkin sebenarnya menyembunyikan jeda/interupsi singkat
    dari orang lain yang tidak sempat ke-split jadi utterance terpisah oleh
    AssemblyAI. Jeda antar-kata yang panjang adalah sinyal potensi itu — jadi
    turn hasil pecahan ini punya durasi terukur (bukan sekadar ditandai di
    teks), sehingga bisa dianalisis dengan threshold durasi yang sama seperti
    Mode A (CERAMAH/DISKUSI_TANYA_JAWAB).

    Kalau utterance tidak punya 'words' (mis. file *_utterances_only.json
    yang sudah di-strip) atau cuma 1 kata: seluruh utterance jadi 1 turn
    (degradasi graceful).

    Return list turn terurut start_sec, bentuknya kompatibel dengan
    _build_timeline(): {"speaker", "start_sec", "end_sec", "duration_sec", "text"}.
    """
    turns = []
    for u in utterances:
        words = u.get("words") or []
        if len(words) < 2:
            turns.append({
                "speaker":      u.get("speaker"),
                "start_sec":    u["start_sec"],
                "end_sec":      u["end_sec"],
                "duration_sec": round(u["end_sec"] - u["start_sec"], 3),
                "text":         (u.get("text") or "").strip(),
            })
            continue

        seg_start_idx = 0
        for i in range(1, len(words)):
            gap = words[i]["start_sec"] - words[i - 1]["end_sec"]
            if gap >= gap_threshold_sec:
                seg_words = words[seg_start_idx:i]
                turns.append({
                    "speaker":      u.get("speaker"),
                    "start_sec":    seg_words[0]["start_sec"],
                    "end_sec":      seg_words[-1]["end_sec"],
                    "duration_sec": round(seg_words[-1]["end_sec"] - seg_words[0]["start_sec"], 3),
                    "text":         " ".join(w.get("text", "") for w in seg_words).strip(),
                })
                seg_start_idx = i

        seg_words = words[seg_start_idx:]
        turns.append({
            "speaker":      u.get("speaker"),
            "start_sec":    seg_words[0]["start_sec"],
            "end_sec":      seg_words[-1]["end_sec"],
            "duration_sec": round(seg_words[-1]["end_sec"] - seg_words[0]["start_sec"], 3),
            "text":         " ".join(w.get("text", "") for w in seg_words).strip(),
        })

    turns.sort(key=lambda t: t["start_sec"])
    return turns


def _format_windows_turns_for_gemini(
    timeline: list,
    audio_duration_sec: float,
    window_size: int = CHUNK_DURATION_SEC,
) -> str:
    """
    Fallback ketika diarization cuma mendeteksi <=1 speaker unik (statistik
    per-speaker tidak reliable). Timeline di sini dibangun dari "turn" (lihat
    _build_turns_from_utterances), bukan dari speaker asli.

    Per window: turn TERPANJANG = kandidat durasi CERAMAH (1 blok bicara
    panjang tanpa jeda berarti). Total turn LAIN (bukan yang terpanjang) =
    kandidat durasi DISKUSI_TANYA_JAWAB (kumpulan bicara singkat/interupsi).
    Sama seperti Mode A, tapi menggantikan "waktu per speaker" dengan
    "durasi per turn" karena cuma ada 1 speaker nominal.
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

        def clipped_dur(e):
            return max(0, min(e["end_sec"], w_end) - max(e["start_sec"], w_start))

        speech_sorted = sorted(speech, key=clipped_dur, reverse=True)

        if speech_sorted:
            longest_dur = clipped_dur(speech_sorted[0])
            other_dur   = sum(clipped_dur(e) for e in speech_sorted[1:])
        else:
            longest_dur = 0.0
            other_dur   = 0.0

        total_diam = sum(clipped_dur(e) for e in diams)

        samples = [
            f'  [{fmt(e["start_sec"])}] "{(e["text"] or "")[:120].strip()}"'
            for e in speech_sorted[:3]
            if (e["text"] or "").strip()
        ]

        block = (
            f"[WINDOW {w + 1} | {fmt(w_start)}–{fmt(w_end)}]\n"
            f"  Turn terpanjang (kandidat CERAMAH)              : {longest_dur:.0f}s\n"
            f"  Total turn lain (kandidat DISKUSI/TANYA_JAWAB)  : {other_dur:.0f}s\n"
            f"  Diam                                            : {total_diam:.0f}s"
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
        # Gemini mengembalikan start_sec/end_sec dalam DETIK sesuai instruksi prompt.
        dur = float(seg.get("end_sec", 0)) - float(seg.get("start_sec", 0))
        if act in totals:
            totals[act] += dur
        else:
            totals["CERAMAH"] += dur  # fallback

    # Jika timeline Gemini tidak menutupi seluruh durasi (terpotong / truncated),
    # tambahkan sisa waktu ke aktivitas dominan saat ini atau CERAMAH sebagai fallback.
    covered  = sum(totals.values())
    gap      = (total_duration_sec or 0) - covered
    if gap > 5:  # toleransi 5 detik untuk rounding
        dominant_so_far = max(totals, key=lambda k: totals[k]) if covered > 0 else "CERAMAH"
        totals[dominant_so_far] += gap
        logger.warning(
            f"[ACTIVITY] Timeline gap {gap:.0f}s tidak ter-cover — "
            f"dialokasikan ke {dominant_so_far}"
        )

    base     = total_duration_sec or sum(totals.values()) or 1.0

    # Dominant dari 4 kategori raw; DISKUSI dan TANYA_JAWAB digabung jadi satu label
    _raw_dom = max(totals, key=lambda k: totals[k])
    if _raw_dom in ("DISKUSI", "TANYA_JAWAB"):
        dominant = "DISKUSI & TANYA JAWAB"
    elif _raw_dom == "DIAM":
        dominant = "DIAM"
    else:
        dominant = "CERAMAH"

    int_sec = totals["DISKUSI"] + totals["TANYA_JAWAB"]

    return {
        "ceramah_sec":      round(totals["CERAMAH"], 1),
        "tanya_jawab_sec":  round(totals["TANYA_JAWAB"], 1),
        "diskusi_sec":      round(totals["DISKUSI"], 1),
        "diam_sec":         round(totals["DIAM"], 1),
        "interaktif_sec":   round(int_sec, 1),
        "ceramah_pct":      round(totals["CERAMAH"]     / base * 100, 1),
        "tanya_jawab_pct":  round(totals["TANYA_JAWAB"] / base * 100, 1),
        "diskusi_pct":      round(totals["DISKUSI"]     / base * 100, 1),
        "diam_pct":         round(totals["DIAM"]        / base * 100, 1),
        "interaktif_pct":   round(int_sec / base * 100, 1),
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
    window_sec: int | None = None,
    ceramah_threshold_sec: int | None = None,
    diskusi_tj_threshold_sec: int | None = None,
    use_content_fallback: bool = False,
) -> dict:
    """
    Klasifikasikan aktivitas pembelajaran per window menggunakan Gemini.

    Tiga mode input (dipilih otomatis):
      A. DIARIZATION     — jika chunks memiliki kolom 'utterances' dengan
                           >=2 speaker unik → gunakan statistik bicara per
                           speaker per window
      A2. CONTENT-RICH FALLBACK — jika 'utterances' ada tapi cuma <=1 speaker
                           unik (diarization gagal membedakan speaker) DAN
                           use_content_fallback=True → pecah tiap utterance
                           jadi "turn" berdasarkan jeda antar-kata >= 2 detik
                           (data 'words'), lalu pakai durasi turn terpanjang
                           (kandidat CERAMAH) vs total turn lain (kandidat
                           DISKUSI_TANYA_JAWAB) per window — threshold sama
                           persis dengan Mode A, cuma sumber datanya beda
      B. CONTENT-BASED   — fallback jika tidak ada 'utterances' sama sekali
                           → klasifikasi dari pola konten teks saja (selalu
                           window CHUNK_DURATION_SEC, tidak terpengaruh window_sec)

    window_sec / ceramah_threshold_sec / diskusi_tj_threshold_sec /
    use_content_fallback bersifat opsional dan HANYA dipakai oleh script
    evaluasi (evaluation/f1_evaluator.py, evaluation/annotation_helper.py)
    untuk eksperimen. Pemanggil produksi (summary_worker.py) tidak mengisi
    parameter ini sehingga behavior tetap seperti semula (window
    CHUNK_DURATION_SEC, prompt "label paling dominan", tidak ada fallback
    content-rich walau diarization cuma 1 speaker).

    Return dict:
      success (bool), ceramah_pct, tanya_jawab_pct, diskusi_pct,
      diam_pct, dominant, activity_timeline[], + _sec variants
    """
    if not _GEMINI_AVAILABLE or not gemini_client:
        return {"success": False, "error": "GEMINI_API_KEY tidak dikonfigurasi"}

    if not chunks:
        return {"success": False, "error": "Tidak ada data chunk transkrip"}

    total_min = round(total_duration_sec / 60, 1)

    # ── Parameter eksperimen (default None → behavior produksi lama) ──
    use_threshold_prompt = window_sec is not None
    eff_window_sec       = window_sec or CHUNK_DURATION_SEC
    eff_ceramah_th       = ceramah_threshold_sec or 120
    eff_diskusi_th       = diskusi_tj_threshold_sec or 60

    # ── Pilih mode berdasarkan ketersediaan & kualitas diarization data ──
    has_diarization      = any(c.get("utterances") for c in chunks)
    all_utterances       = _merge_utterances_from_chunks(chunks) if has_diarization else []
    unique_speakers      = {u.get("speaker") for u in all_utterances if u.get("speaker")}
    diarization_degenerate = (
        has_diarization and use_content_fallback and len(unique_speakers) <= 1
    )

    if diarization_degenerate:
        logger.info(
            f"[ACTIVITY] Mode CONTENT-RICH FALLBACK (turn-duration) | "
            f"chunks={len(chunks)} speakers_unik={len(unique_speakers)}"
        )
        turns     = _build_turns_from_utterances(all_utterances, gap_threshold_sec=2.0)
        timeline  = _build_timeline(turns, SILENCE_THRESHOLD_S)
        formatted = _format_windows_turns_for_gemini(timeline, total_duration_sec, window_size=eff_window_sec)
        mode_desc = "content-rich fallback (turn-duration, diarization degenerate)"
    elif has_diarization:
        logger.info(
            f"[ACTIVITY] Mode DIARIZATION | chunks={len(chunks)}"
        )
        timeline  = _build_timeline(all_utterances, SILENCE_THRESHOLD_S)
        formatted = _format_windows_for_gemini(timeline, total_duration_sec, window_size=eff_window_sec)
        mode_desc = "diarization (speaker stats)"
    else:
        logger.info(
            f"[ACTIVITY] Mode CONTENT-BASED | chunks={len(chunks)}"
        )
        formatted = format_chunks_for_gemini(chunks)
        mode_desc = "content-based (plain text)"

    # Mode content-based selalu window CHUNK_DURATION_SEC (format_chunks_for_gemini
    # tidak menerima window_size), jadi window_min & instruksi threshold HANYA
    # relevan untuk mode diarization/content-rich fallback — konsisten dengan
    # docstring di atas.
    window_min = round((eff_window_sec if has_diarization else CHUNK_DURATION_SEC) / 60, 1)

    # ── Bangun prompt ─────────────────────────────────────────────────
    # DIAM di mode threshold-based (eval only) didefinisikan sebagai PROPORSI
    # window yang diam (>=60%, sama seperti DIAM_THRESHOLD di
    # evaluation/annotation_helper.py) — bukan cuma "ada 1 jeda >=3 detik"
    # yang terlalu longgar untuk klasifikasi window 3 menit penuh. Rule lama
    # ("Diam total >= 3 detik") dipertahankan untuk mode produksi (tidak
    # threshold-based) supaya behavior produksi tidak berubah.
    use_threshold_diam = diarization_degenerate or use_threshold_prompt
    if use_threshold_diam:
        diam_sec_th = round(0.6 * eff_window_sec)
        diam_def = (
            f"  • DIAM       : Total waktu diam DALAM window >= 60% dari durasi "
            f"window (~{diam_sec_th} detik dari {window_min} menit) — bukan cuma "
            f"karena ada satu jeda >=3 detik di suatu titik."
        )
    else:
        diam_def = "  • DIAM       : Diam total >= 3 detik, atau teks sangat pendek/kosong."

    if diarization_degenerate:
        data_label = "DATA PER WINDOW (turn stats + contoh teks, diarization speaker tidak reliable)"
        definitions = (
            "  • CERAMAH    : Didominasi 1 turn bicara panjang tanpa jeda berarti. "
            "Turn lain (kalau ada) sangat singkat.\n"
            "  • TANYA_JAWAB: Ada beberapa turn singkat bergantian dengan turn "
            "lain, pola tanya-jawab.\n"
            "  • DISKUSI    : Beberapa turn dengan porsi lebih merata/terbuka.\n"
            f"{diam_def}\n\n"
            "  Catatan: karena diarization cuma mendeteksi 1 speaker unik "
            "(tidak reliable), data di bawah dipecah jadi \"turn\" berdasarkan "
            "jeda antar-kata >= 2 detik — bukan berdasarkan speaker asli."
        )
    elif has_diarization:
        data_label = "DATA PER WINDOW (speaker stats + contoh teks)"
        definitions = (
            "  • CERAMAH    : Didominasi 1 speaker (dosen) yang berbicara panjang. "
            "Speaker lain hampir tidak bicara.\n"
            "  • TANYA_JAWAB: Ada 2+ speaker bergantian, biasanya singkat. "
            "Pola Q&A antara dosen dan mahasiswa.\n"
            "  • DISKUSI    : 2+ speaker bicara dengan porsi lebih merata, "
            "percakapan terbuka.\n"
            f"{diam_def}"
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

    if diarization_degenerate or (use_threshold_prompt and has_diarization):
        # Reuse instruksi threshold yang sama persis untuk mode diarization normal
        # DAN content-rich fallback (turn-duration) — cuma beda sumber datanya
        # ("waktu per speaker" vs "durasi per turn"), threshold-nya sama.
        instruksi_label = f"""2. Tentukan label window dengan aturan berikut (bukan sekadar yang paling dominan):
   - CERAMAH jika estimasi durasi pola ceramah (1 speaker/turn mendominasi) > {eff_ceramah_th} detik dalam window tersebut.
   - DISKUSI atau TANYA_JAWAB jika estimasi durasi pola diskusi/tanya-jawab (2+ speaker/turn bergantian) > {eff_diskusi_th} detik dalam window tersebut. Pilih salah satu sesuai definisi masing-masing di atas.
   - DIAM sesuai definisi DIAM di atas (tidak berubah).
   - Jika TIDAK ADA kategori di atas yang memenuhi ambang batasnya, pilih kategori dengan estimasi durasi TERBESAR di window tersebut."""
    else:
        instruksi_label = "2. Beri 1 label aktivitas per window."

    prompt = f"""Anda adalah analis rekaman kuliah. Rekaman berdurasi {total_duration_sec:.0f} detik (~{total_min} menit).

DEFINISI AKTIVITAS:
{definitions}

{data_label} ({window_min} menit per window):
{formatted}

INSTRUKSI:
1. Analisis setiap window berdasarkan data di atas.
{instruksi_label}
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
