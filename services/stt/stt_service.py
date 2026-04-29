import requests
import os
from dotenv import load_dotenv

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
        with open(file_path, "rb") as f:
            files = {
                "file": (os.path.basename(file_path), f, "audio/wav")
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