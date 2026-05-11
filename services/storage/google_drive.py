import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import time
from core.logger import logger

MAX_RETRIES = 3
RETRY_DELAY = 2  # detik

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials", "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "credentials", "token.json")

# FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
FOLDER_ID = "1zFqsMF4AEtpu-dKJnqXaFfQrevl3oTkh"

SCOPES = ['https://www.googleapis.com/auth/drive']


# 🔹 Centralized service builder
def get_drive_service():
    creds = get_credentials()
    return build('drive', 'v3', credentials=creds)


def get_credentials():
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                if os.path.exists(TOKEN_PATH):
                    os.remove(TOKEN_PATH)
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return creds


def upload_to_drive(file_path):
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        service = get_drive_service()

        file_metadata = {
            "name": os.path.basename(file_path),
            "parents": [FOLDER_ID]
        }

        media = MediaFileUpload(file_path)

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        return file.get("id")

    except Exception as e:
        print("UPLOAD ERROR:", e)
        return None


def find_file_by_name(filename: str):
    try:
        service = get_drive_service()

        query = f"name='{filename}' and '{FOLDER_ID}' in parents"

        results = service.files().list(
            q=query,
            fields="files(id, name)"
        ).execute()

        files = results.get('files', [])

        return files[0] if files else None

    except Exception as e:
        print("FIND ERROR:", e)
        return None


def download_file(file_id: str, filename: str, save_path="temp"):
    try:
        service = get_drive_service()

        os.makedirs(save_path, exist_ok=True)

        file_path = os.path.join(save_path, filename)

        request = service.files().get_media(fileId=file_id)

        with open(file_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

        return file_path

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None


def get_file_from_gdrive_flexible(base_filename: str):
    try:
        service = get_drive_service()

        query = f"name contains '{base_filename}' and '{FOLDER_ID}' in parents and trashed = false"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"[GDRIVE SEARCH] attempt={attempt} | filename={base_filename}")

                results = service.files().list(
                    q=query,
                    fields="files(id, name, parents)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()

                files = results.get("files", [])

                logger.info(f"[GDRIVE RESULT] total_found={len(files)}")

                if not files:
                    return None, None, None

                # 🔥 Handle multiple files
                if len(files) > 1:
                    logger.warning(
                        f"[GDRIVE WARNING] Multiple files found | filenames={[f['name'] for f in files]}"
                    )

                    # pilih yang paling mirip (exact match dulu)
                    exact_match = next(
                        (f for f in files if f["name"] == base_filename),
                        None
                    )

                    file = exact_match if exact_match else files[0]
                else:
                    file = files[0]

                file_id = file["id"]
                file_name = file["name"]
                parents = file.get("parents", [])

                folder_id = parents[0] if parents else None

                logger.info(
                    f"[GDRIVE SELECTED] file_name={file_name} | file_id={file_id} | folder_id={folder_id}"
                )

                # DOWNLOAD
                audio_path = download_file(file_id, file_name)

                if not audio_path:
                    logger.error("[GDRIVE DOWNLOAD FAILED] download_file returned None")
                    return None, None, folder_id

                logger.info(
                    f"[GDRIVE DOWNLOAD SUCCESS] file_name={file_name} | saved_path={audio_path}"
                )

                return audio_path, file_name, folder_id

            except Exception as inner_error:
                logger.error(
                    f"[GDRIVE ERROR] attempt={attempt} failed | error={str(inner_error)}",
                    exc_info=True
                )

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    raise

    except Exception as e:
        logger.error(f"[GDRIVE FATAL ERROR] {str(e)}", exc_info=True)
        return None, None, None