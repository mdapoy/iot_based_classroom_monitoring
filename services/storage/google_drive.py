import os
import json
import time

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

from core.logger import logger

MAX_RETRIES = 3
RETRY_DELAY = 2  # detik

load_dotenv()

# =========================
# ENV
# =========================

FOLDER_ID = os.getenv("FOLDER_ID")

SCOPES = ['https://www.googleapis.com/auth/drive']

# 🔥 isi full JSON service account di .env
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")


# =========================
# GOOGLE DRIVE SERVICE
# =========================
def get_drive_service():
    try:
        if not GOOGLE_CREDENTIALS_JSON:
            raise Exception("GOOGLE_CREDENTIALS_JSON not found")

        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)

        creds = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES
        )

        return build("drive", "v3", credentials=creds)

    except Exception as e:
        logger.error(f"[GDRIVE AUTH ERROR] {e}", exc_info=True)
        raise


# =========================
# UPLOAD FILE
# =========================
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

        logger.info(f"[UPLOAD SUCCESS] file_id={file.get('id')}")

        return file.get("id")

    except Exception as e:
        logger.error(f"[UPLOAD ERROR] {e}", exc_info=True)
        return None


# =========================
# FIND FILE
# =========================
def find_file_by_name(filename: str):
    try:
        service = get_drive_service()

        query = (
            f"name='{filename}' "
            f"and '{FOLDER_ID}' in parents "
            f"and trashed=false"
        )

        results = service.files().list(
            q=query,
            fields="files(id, name)"
        ).execute()

        files = results.get('files', [])

        return files[0] if files else None

    except Exception as e:
        logger.error(f"[FIND ERROR] {e}", exc_info=True)
        return None


# =========================
# DOWNLOAD FILE
# =========================
def download_file(file_id: str, filename: str):
    try:
        service = get_drive_service()

        # download ke folder Downloads user
        downloads_folder = os.path.join(
            os.path.expanduser("~"),
            "Downloads"
        )

        os.makedirs(downloads_folder, exist_ok=True)

        file_path = os.path.join(downloads_folder, filename)

        request = service.files().get_media(fileId=file_id)

        with open(file_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)

            done = False

            while not done:
                status, done = downloader.next_chunk()

                if status:
                    progress = int(status.progress() * 100)

                    logger.info(f"[DOWNLOAD] {progress}%")

        logger.info(
            f"[DOWNLOAD SUCCESS] file_name={filename} | saved_to={file_path}"
        )

        return file_path

    except Exception as e:
        logger.error(f"[DOWNLOAD ERROR] {e}", exc_info=True)
        return None


# =========================
# FLEXIBLE SEARCH + DOWNLOAD
# =========================
def get_file_from_gdrive_flexible(base_filename: str):
    try:
        service = get_drive_service()

        query = (
            f"name contains '{base_filename}' "
            f"and '{FOLDER_ID}' in parents "
            f"and trashed = false"
        )

        for attempt in range(1, MAX_RETRIES + 1):

            try:
                logger.info(
                    f"[GDRIVE SEARCH] "
                    f"attempt={attempt} | "
                    f"filename={base_filename}"
                )

                results = service.files().list(
                    q=query,
                    fields="files(id, name, parents)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()

                files = results.get("files", [])

                logger.info(
                    f"[GDRIVE RESULT] total_found={len(files)}"
                )

                if not files:
                    return None, None, None

                # handle multiple files
                if len(files) > 1:

                    logger.warning(
                        f"[GDRIVE WARNING] Multiple files found | "
                        f"filenames={[f['name'] for f in files]}"
                    )

                    exact_match = next(
                        (
                            f for f in files
                            if f["name"] == base_filename
                        ),
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
                    f"[GDRIVE SELECTED] "
                    f"file_name={file_name} | "
                    f"file_id={file_id}"
                )

                # 🔥 DOWNLOAD
                downloaded_path = download_file(
                    file_id,
                    file_name
                )

                if not downloaded_path:

                    logger.error(
                        "[GDRIVE DOWNLOAD FAILED]"
                    )

                    return None, None, folder_id

                logger.info(
                    f"[GDRIVE DOWNLOAD SUCCESS] "
                    f"saved_path={downloaded_path}"
                )

                return downloaded_path, file_name, folder_id

            except Exception as inner_error:

                logger.error(
                    f"[GDRIVE ERROR] "
                    f"attempt={attempt} failed | "
                    f"error={str(inner_error)}",
                    exc_info=True
                )

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    raise

    except Exception as e:
        logger.error(
            f"[GDRIVE FATAL ERROR] {str(e)}",
            exc_info=True
        )

        return None, None, None