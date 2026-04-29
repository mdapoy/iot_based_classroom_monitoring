from google import genai
import os
import time
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def summarize_text(transcript: str, max_retries=5):
    prompt = f"""
    Anda adalah seorang peringkas profesional yang bertugas untuk merangkum hasil transkrip.
    Aturan:
    1. Ringkasan harus menggunakan bahasa yang jelas dan mudah dipahami.
    2. Ringkasan harus mencakup poin-poin penting dari transkrip.
    3. Pastikan ringkasan dalam bentuk paragraf, bukan dalam bentuk poin-poin.
    Berikut transkrip:
    {transcript}
    """

    delay = 2  # detik awal

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )

            return {
                "success": True,
                "result": response.text
            }

        except Exception as e:
            error_msg = str(e).lower()

            # 🔁 HANDLE HIGH DEMAND / RATE LIMIT
            if any(keyword in error_msg for keyword in [
                "429", "quota", "rate", "overloaded", "busy"
            ]):
                print(f"[RETRY {attempt+1}] Model busy, retry in {delay}s...")
                time.sleep(delay)
                delay *= 2  # exponential backoff
                continue

            # ❌ ERROR LAIN → LANGSUNG FAIL
            return {
                "success": False,
                "error": str(e)
            }

    # 🚨 FALLBACK JIKA SEMUA RETRY GAGAL
    return {
        "success": False,
        "error": "Model sedang sibuk, silakan coba lagi nanti"
    }