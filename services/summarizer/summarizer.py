from google import genai
import os
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


def summarize_text(transcript: str, max_retries=3):

    logger.info(f"[SUMMARY] transcript_length={len(transcript)}")

    prompt = f"""
    Anda adalah seorang peringkas profesional yang bertugas untuk merangkum hasil dari rekaman suara di ruangan kuliah pada kelas Fisika 2.

    Aturan:
    1. Ringkasan harus menggunakan bahasa yang jelas dan mudah dipahami.
    2. Ringkasan harus mencakup poin-poin penting dari transkrip.
    3. Ringkasan harus dalam bentuk paragraf.
    4. Fokus pada konteks pembelajaran Fisika 2.
    5. jangan gunakan karakter seperti ■.

    Berikut transkrip:
    {transcript}
    """

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

                text = response.text.strip()

                logger.info(
                    f"[SUMMARY SUCCESS] "
                    f"model={model_name} "
                    f"elapsed={elapsed}s "
                    f"text_length={len(text)}"
                )

                return {
                    "success": True,
                    "result": text,
                    "model": model_name
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
        "success": False,
        "error": "Semua model Gemini gagal"
    }