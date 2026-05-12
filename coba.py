import os
import json
import tempfile

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import uvicorn
import io

load_dotenv()

app = FastAPI()

# =========================
# CONFIG
# =========================

SCOPES = ["https://www.googleapis.com/auth/drive"]

# =========================
# GOOGLE DRIVE SERVICE
# =========================
def get_drive_service():
    try:
        credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

        if not credentials_json:
            raise Exception("GOOGLE_CREDENTIALS_JSON not found")

        credentials_info = json.loads(credentials_json)

        creds = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES
        )

        service = build("drive", "v3", credentials=creds)

        return service

    except Exception as e:
        raise Exception(f"Google Drive Auth Error: {str(e)}")


# =========================
# DEBUG ENV
# =========================
@app.get("/check-env")
def check_env():
    return {
        "google_credentials_exists": os.getenv("GOOGLE_CREDENTIALS_JSON") is not None,
        "folder_id_exists": os.getenv("FOLDER_ID") is not None,
    }


# =========================
# LIST FILES IN FOLDER
# =========================
@app.get("/list-files")
def list_files():
    try:
        folder_id = os.getenv("FOLDER_ID")

        if not folder_id:
            raise Exception("FOLDER_ID not found")

        service = get_drive_service()

        query = f"'{folder_id}' in parents and trashed = false"

        results = service.files().list(
            q=query,
            fields="files(id, name)"
        ).execute()

        files = results.get("files", [])

        return {
            "total_files": len(files),
            "files": files
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================
# DOWNLOAD FILE BY NAME
# =========================
@app.get("/download")
def download_file_by_name(filename: str):
    try:
        folder_id = os.getenv("FOLDER_ID")

        if not folder_id:
            raise Exception("FOLDER_ID not found")

        service = get_drive_service()

        # cari file berdasarkan nama
        query = (
            f"name='{filename}' "
            f"and '{folder_id}' in parents "
            f"and trashed=false"
        )

        results = service.files().list(
            q=query,
            fields="files(id, name)"
        ).execute()

        files = results.get("files", [])

        if not files:
            raise Exception(f"File '{filename}' not found")

        file = files[0]

        file_id = file["id"]
        file_name = file["name"]

        # buat folder download
        os.makedirs("downloads", exist_ok=True)

        save_path = os.path.join("downloads", file_name)

        request = service.files().get_media(fileId=file_id)

        with open(save_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)

            done = False

            while not done:
                status, done = downloader.next_chunk()

                if status:
                    print(f"Download {int(status.progress() * 100)}%")

        return {
            "status": "success",
            "file_name": file_name,
            "saved_to": save_path
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================
# ROOT
# =========================
@app.get("/")
def root():
    return {
        "message": "Google Drive Service Account Test"
    }


# =========================
# RUN LOCAL
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )