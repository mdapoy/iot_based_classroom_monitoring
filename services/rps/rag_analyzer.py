import time
from services.summarizer.summarizer import client, MODELS, classify_gemini_error
from core.logger import logger


def analyze_rps(transcript: str, rps: dict, pertemuan_ke: int, max_retries: int = 3) -> dict:
    """
    Bandingkan transcript dengan RPS pertemuan ke-N menggunakan Gemini API.

    RAG flow:
      Retrieval  → rps dict sudah diambil via get_rps_for_week()
      Augmented  → transcript + isi RPS digabung ke dalam satu prompt
      Generation → Gemini menghasilkan analisis kesesuaian

    Return dict:
      success (bool), kesesuaian, status_waktu, penjelasan  — atau error string
    """
    materi       = rps.get("materi_pembelajaran", "-")
    pengalaman   = rps.get("pengalaman_pembelajaran_mahasiswa", "-")

    prompt = f"""
Anda adalah analis pembelajaran yang mengevaluasi kesesuaian materi kuliah dengan RPS.

RPS Pertemuan ke-{pertemuan_ke}:
- Materi Pembelajaran                : {materi}
- Pengalaman Pembelajaran Mahasiswa  : {pengalaman}

Transkrip rekaman kuliah:
{transcript}

Tugas:
1. Apakah materi yang diajarkan SESUAI dengan RPS pertemuan ke-{pertemuan_ke}?
2. Apakah cakupan materi LEBIH CEPAT, TEPAT WAKTU, atau LEBIH LAMBAT dari RPS?
3. Penjelasan singkat 2-3 kalimat.

Jawab HANYA dalam format ini (tanpa teks lain):
KESESUAIAN: [SESUAI/SEBAGIAN SESUAI/TIDAK SESUAI]
STATUS: [LEBIH CEPAT/TEPAT WAKTU/LEBIH LAMBAT]
PENJELASAN: [2-3 kalimat penjelasan]
"""

    for model_name in MODELS:
        delay = 2

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )

                parsed = _parse_response(response.text.strip())
                logger.info(
                    f"[RAG] Success | model={model_name} "
                    f"kesesuaian={parsed.get('kesesuaian')}"
                )
                return {"success": True, **parsed}

            except Exception as e:
                error_type = classify_gemini_error(str(e))
                retryable = error_type in ["high_demand", "rate_limit", "429_unknown"]

                if retryable and attempt < max_retries - 1:
                    logger.warning(
                        f"[RAG RETRY] model={model_name} "
                        f"attempt={attempt + 1} sleep={delay}s"
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue

                logger.warning(
                    f"[RAG SKIP] model={model_name} reason={error_type}"
                )
                break  # try next model

    logger.error("[RAG FAILED] semua model gagal")
    return {"success": False, "error": "Analisis RPS gagal"}


def _parse_response(text: str) -> dict:
    """Parse output Gemini ke dict terstruktur."""
    result = {
        "kesesuaian": "-",
        "status_waktu": "-",
        "penjelasan": "-",
    }

    for line in text.split("\n"):
        if line.startswith("KESESUAIAN:"):
            result["kesesuaian"] = line.split(":", 1)[1].strip()
        elif line.startswith("STATUS:"):
            result["status_waktu"] = line.split(":", 1)[1].strip()
        elif line.startswith("PENJELASAN:"):
            result["penjelasan"] = line.split(":", 1)[1].strip()

    return result
