# from googleapiclient.discovery import build
# from googleapiclient.http import MediaFileUpload
# from googleapiclient.errors import HttpError
# from google_auth_oauthlib.flow import InstalledAppFlow
# from google.auth.transport.requests import Request
# from google.oauth2.credentials import Credentials

# import os
# from dotenv import load_dotenv

# load_dotenv()

# BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
# TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

# FOLDER_ID = "1zFqsMF4AEtpu-dKJnqXaFfQrevl3oTkh"

# SCOPES = ['https://www.googleapis.com/auth/drive.file']


# def upload_to_drive(file_path):

#     try:

#         if not os.path.exists(file_path):
#             raise FileNotFoundError(f"File tidak ditemukan: {file_path}")

#         creds = None

#         if os.path.exists(TOKEN_FILE):
#             creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

#         if not creds or not creds.valid:
#             if creds and creds.expired and creds.refresh_token:
#                 creds.refresh(Request())
#             else:
#                 flow = InstalledAppFlow.from_client_secrets_file(
#                     CREDENTIALS_FILE, SCOPES
#                 )
#                 creds = flow.run_local_server(port=0)

#             with open(TOKEN_FILE, "w") as token:
#                 token.write(creds.to_json())

#         service = build('drive', 'v3', credentials=creds)

#         file_metadata = {
#             "name": os.path.basename(file_path),
#             "parents": [FOLDER_ID]
#         }

#         media = MediaFileUpload(file_path)

#         file = service.files().create(
#             body=file_metadata,
#             media_body=media,
#             fields="id"
#         ).execute()

#         print("Upload berhasil!")
#         print("File ID:", file.get("id"))

#         return file.get("id")

#     except HttpError as e:
#         print("GOOGLE DRIVE API ERROR:", e)

#     except Exception as e:
#         print("ERROR:", e)

#     return None

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

import os
from datetime import datetime
from dotenv import load_dotenv
import time

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

FOLDER_ID = "1zFqsMF4AEtpu-dKJnqXaFfQrevl3oTkh"
SCOPES = ['https://www.googleapis.com/auth/drive.file']


def log(msg: str):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {msg}")


def upload_to_drive(file_path):
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File tidak ditemukan: {file_path}")

        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError("credentials.json tidak ditemukan")

        creds = None

        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log("Refreshing expired token...")
                creds.refresh(Request())
            else:
                log("Authenticating with Google OAuth...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())

        service = build("drive", "v3", credentials=creds)

        file_metadata = {
            "name": os.path.basename(file_path),
            "parents": [FOLDER_ID]
        }

        media = MediaFileUpload(file_path, resumable=True, chunksize=1024*1024)

        request = service.files().create(body=file_metadata, media_body=media, fields="id")

        log(f"Start uploading file: {os.path.basename(file_path)}")

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                percent = int(status.progress() * 100)
                log(f"Uploading... {percent}%")

        log(f"Upload selesai! File ID: {response.get('id')}")
        return response.get('id')

    except HttpError as e:
        log(f"Google Drive API Error: {e}")

    except Exception as e:
        log(f"Upload Error: {e}")

    return None
