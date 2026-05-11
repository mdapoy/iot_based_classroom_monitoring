import requests
import os
from dotenv import load_dotenv
from core.logger import logger

load_dotenv()

API_KEY = os.getenv("API_KEY_STT")
CALLBACK_URL = os.getenv("CALLBACK_URL")

API_URL = "https://telkom-ai-dag.api.apilogy.id/Speech_To_Text_Callback/0.0.2/stt_inference"

async def send_to_stt(file):
    try:
        files = {
            "file": (file.filename, await file.read(), file.content_type)
        }

        data = {
            "callback_url": CALLBACK_URL
        }

        headers = {
            "x-api-key": API_KEY
        }

        response = requests.post(
            API_URL,
            headers=headers,
            files=files,
            data=data
        )

        return {
            "status_code": response.status_code,
            "response": response.json()
        }

    except Exception as e:
        return {"error": str(e)}

def check_stt_result(task_id: str):
    url = f"https://telkom-ai-dag.api.apilogy.id/Speech_To_Text_Callback/0.0.2/checkresult/{task_id}"

    headers = {
        "x-api-key": API_KEY
    }

    response = requests.get(url, headers=headers)

    return {
        "status_code": response.status_code,
        "response": response.json()
    }

def send_to_stt_file(file_path: str):
    try:
        logger.info(f"[SEND STT] START file={file_path}")

        # 🔥 VALIDASI FILE
        if not os.path.exists(file_path):
            logger.error(f"[SEND STT] File not found: {file_path}")
            return {"error": "file not found"}

        file_size = os.path.getsize(file_path)
        logger.info(f"[SEND STT] file_size={file_size} bytes")

        with open(file_path, "rb") as f:
            files = {
                "file": (os.path.basename(file_path), f, "audio/wav")
            }

            # 🔥 VALIDASI CALLBACK URL
            logger.info(f"[SEND STT] callback_url={CALLBACK_URL}")

            if not CALLBACK_URL:
                logger.error("[SEND STT] CALLBACK_URL is EMPTY ❌")
                return {"error": "callback_url is empty"}

            data = {
                "callback_url": CALLBACK_URL
            }

            headers = {
                "x-api-key": API_KEY
            }

            # 🔥 LOG DETAIL REQUEST
            logger.info(f"[SEND STT] headers={headers}")
            logger.info(f"[SEND STT] data={data}")
            logger.info(f"[SEND STT] sending request to {API_URL}")

            response = requests.post(
                API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=120  # 🔥 biar gak silent hang
            )

            # 🔥 STATUS RESPONSE
            logger.info(f"[SEND STT] status_code={response.status_code}")

            # 🔥 RAW TEXT RESPONSE (penting banget kalau json error)
            logger.info(f"[SEND STT] raw_response_text={response.text}")

            try:
                res = response.json()
            except Exception:
                logger.error("[SEND STT] Failed to parse JSON response")
                return {"error": "invalid json response", "raw": response.text}

            # 🔥 FINAL RESPONSE
            logger.info(f"[SEND STT] parsed_response={res}")

            task_id = res.get("task_id")

            if not task_id:
                logger.error(f"[SEND STT] task_id NOT FOUND in response ❌")

            else:
                logger.info(f"[SEND STT] SUCCESS task_id={task_id} ✅")

            return {
                "task_id": task_id,
                "raw": res
            }

    except Exception as e:
        logger.error(f"[SEND STT ERROR] {str(e)}", exc_info=True)
        return {"error": str(e)}