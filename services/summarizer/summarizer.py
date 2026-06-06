from google import genai
import os
import re
import json
import time
from dotenv import load_dotenv
from core.logger import logger

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite"
]

def classify_gemini_error(error: str):
    error = error.lower()

    # quota / token
    if any(k in error for k in [
        "quota exceeded",
        "resource exhausted",
        "billing",
        "daily limit",
        "token limit"
    ]):
        return "quota"

    # rate limit
    if any(k in error for k in [
        "rate limit",
        "too many requests"
    ]):
        return "rate_limit"

    # high demand
    if any(k in error for k in [
        "overloaded",
        "busy",
        "unavailable",
        "service unavailable"
    ]):
        return "high_demand"

    # context terlalu besar
    if any(k in error for k in [
        "input too large",
        "context length",
        "maximum context"
    ]):
        return "context_limit"

    # generic 429
    if "429" in error:
        return "429_unknown"

    return "unknown"


def summarize_text(transcript: str, nama_matkul: str = "mata kuliah", max_retries=3):

    logger.info(f"[SUMMARY] transcript_length={len(transcript)} nama_matkul={nama_matkul}")

    prompt = f"""Anda adalah analis rekaman kuliah {nama_matkul}. Tugas Anda adalah menggambarkan kegiatan yang terjadi di kelas berdasarkan transkrip.

Hasilkan DUA bagian:

1. RINGKASAN: Terdiri dari kalimat pembuka (1 kalimat) yang menggambarkan kegiatan umum perkuliahan, diikuti tepat 6 poin yang menggambarkan jalannya kelas:
   - Poin 1: Topik utama dan cara dosen memperkenalkan atau membuka materi
   - Poin 2: Konsep atau rumus kunci yang disampaikan dosen
   - Poin 3: Metode penyampaian yang digunakan (ceramah, diskusi, tanya jawab, atau kombinasi)
   - Poin 4: Interaksi atau pertanyaan yang muncul di kelas (jika tidak ada, tuliskan kondisi kelas)
   - Poin 5: Poin penting atau hal yang ditekankan oleh dosen
   - Poin 6: Arahan, penutup, atau catatan akhir pertemuan dari dosen (jika tidak ada, tuliskan kesimpulan umum)

2. DETAIL: Satu paragraf panjang yang menjelaskan seluruh kegiatan dan materi secara rinci dan mengalir.

Kembalikan HANYA dalam format JSON berikut (tanpa teks lain di luar JSON):
{{
  "ringkasan": {{
    "pembuka": "Kalimat pembuka yang menggambarkan kegiatan umum perkuliahan.",
    "poin": ["poin 1", "poin 2", "poin 3", "poin 4", "poin 5", "poin 6"]
  }},
  "detail": "paragraf detail lengkap..."
}}

ATURAN WAJIB:
- PEMBUKA: 1 kalimat singkat yang menggambarkan kegiatan umum perkuliahan.
- POIN: array of string, tepat 6 elemen, masing-masing 1 kalimat singkat dan padat.
- Setiap poin harus mencerminkan kegiatan nyata di kelas berdasarkan transkrip.
- DETAIL: paragraf mengalir tanpa poin-poin, tanpa penomoran, tanpa simbol markdown (**, *, #, -).
- Jika perlu menyebut urutan di detail, gunakan: "pertama", "selanjutnya", "kemudian", "terakhir".
- Bahasa Indonesia yang jelas dan mudah dipahami.
- Fokus pada konteks pembelajaran {nama_matkul}.

Berikut transkrip:
{transcript}"""

    for model_name in MODELS:

        logger.info(f"[MODEL] Trying model={model_name}")

        delay = 2

        for attempt in range(max_retries):

            try:
                start = time.time()

                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )

                elapsed = round(time.time() - start, 2)
                text    = response.text.strip()

                # ── Parse JSON response ───────────────────────────────
                # ringkasan = {"pembuka": str, "poin": list[str]}
                ringkasan = {"pembuka": "", "poin": []}
                detail    = ""
                try:
                    json_match = re.search(r'\{.*\}', text, re.DOTALL)
                    if json_match:
                        parsed   = json.loads(json_match.group())
                        raw_ring = parsed.get("ringkasan", {})

                        if isinstance(raw_ring, dict):
                            pembuka = str(raw_ring.get("pembuka", "")).strip()
                            poin    = raw_ring.get("poin", [])
                            poin    = [str(p).strip() for p in poin[:6] if str(p).strip()]
                            ringkasan = {"pembuka": pembuka, "poin": poin}

                        elif isinstance(raw_ring, list):
                            # Fallback: model kembalikan array langsung
                            poin      = [str(p).strip() for p in raw_ring[:6] if str(p).strip()]
                            ringkasan = {"pembuka": "", "poin": poin}

                        elif isinstance(raw_ring, str) and raw_ring.strip():
                            ringkasan = {"pembuka": raw_ring.strip(), "poin": []}

                        detail = parsed.get("detail", "").strip()

                    if not detail:
                        detail = text
                except Exception:
                    detail = text

                logger.info(
                    f"[SUMMARY SUCCESS] "
                    f"model={model_name} "
                    f"elapsed={elapsed}s "
                    f"ringkasan_poin={len(ringkasan.get('poin', []))} "
                    f"detail_len={len(detail)}"
                )

                return {
                    "success":   True,
                    "ringkasan": ringkasan,   # list[str], maks 8 poin
                    "detail":    detail,      # str paragraf
                    "model":     model_name,
                }

            except Exception as e:

                error_text = str(e)
                error_type = classify_gemini_error(error_text)

                logger.error(
                    f"[SUMMARY ERROR] "
                    f"model={model_name} "
                    f"retry={attempt+1}/{max_retries} "
                    f"type={error_type} "
                    f"error={error_text}"
                )

                retryable = error_type in [
                    "high_demand",
                    "rate_limit",
                    "429_unknown"
                ]

                if retryable:

                    logger.warning(
                        f"[SUMMARY RETRY] "
                        f"model={model_name} "
                        f"reason={error_type} "
                        f"sleep={delay}s"
                    )

                    time.sleep(delay)
                    delay *= 2
                    continue

                # ❌ jangan retry kalau quota/context
                logger.warning(
                    f"[MODEL SKIP] "
                    f"model={model_name} "
                    f"reason={error_type}"
                )

                break

        logger.warning(f"[FALLBACK] move_to_next_model")

    logger.error("[SUMMARY FAILED] all models failed")

    return {
        "success":  False,
        "ringkasan": "",
        "detail":    "",
        "error":     "Semua model Gemini gagal"
    }