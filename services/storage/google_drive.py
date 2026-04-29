# from googleapiclient.discovery import build
# from googleapiclient.http import MediaFileUpload
# from googleapiclient.errors import HttpError
# from google_auth_oauthlib.flow import InstalledAppFlow
# from google.auth.transport.requests import Request
# from google.oauth2.credentials import Credentials
# import os
# from dotenv import load_dotenv
# from googleapiclient.http import MediaIoBaseDownload
# import io

# load_dotenv()

# BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials", "credentials.json")
# TOKEN_PATH = os.path.join(BASE_DIR, "credentials", "token.json")

# FOLDER_ID = "1zFqsMF4AEtpu-dKJnqXaFfQrevl3oTkh"

# SCOPES = ['https://www.googleapis.com/auth/drive']


# def get_credentials():

#     creds = None

#     if os.path.exists(TOKEN_FILE):
#         creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

#     # jika token tidak valid
#     if not creds or not creds.valid:

#         if creds and creds.expired and creds.refresh_token:
#             try:
#                 print("Refreshing expired token...")
#                 creds.refresh(Request())

#             except Exception as e:
#                 print("Refresh token gagal:", e)

#                 # hapus token lama
#                 if os.path.exists(TOKEN_FILE):
#                     os.remove(TOKEN_FILE)

#                 creds = None

#         # jika tidak ada creds / refresh gagal
#         if not creds:
#             print("Login ulang Google OAuth...")
#             flow = InstalledAppFlow.from_client_secrets_file(
#                 CREDENTIALS_FILE, SCOPES
#             )
#             creds = flow.run_local_server(port=0)

#         # simpan token baru
#         with open(TOKEN_FILE, "w") as token:
#             token.write(creds.to_json())

#     return creds

# def get_credentials():
#     creds = None

#     if os.path.exists(TOKEN_PATH):
#         creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

#     if not creds or not creds.valid:

#         if creds and creds.expired and creds.refresh_token:
#             try:
#                 print("Refreshing expired token...")
#                 creds.refresh(Request())
#             except Exception as e:
#                 print("Refresh token gagal:", e)

#                 if os.path.exists(TOKEN_PATH):
#                     os.remove(TOKEN_PATH)

#                 creds = None

#         if not creds:
#             print("Login ulang Google OAuth...")
#             flow = InstalledAppFlow.from_client_secrets_file(
#                 CREDENTIALS_PATH, SCOPES
#             )
#             creds = flow.run_local_server(port=0)

#         with open(TOKEN_PATH, "w") as token:
#             token.write(creds.to_json())

#     return creds


# def upload_to_drive(file_path):

#     try:

#         if not os.path.exists(file_path):
#             raise FileNotFoundError(f"File tidak ditemukan: {file_path}")

#         creds = get_credentials()

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

# def find_file_by_name(filename: str):
#     try:
#         creds = get_credentials()
#         service = build('drive', 'v3', credentials=creds)

#         query = f"name='{filename}' and '{FOLDER_ID}' in parents"

#         results = service.files().list(
#             q=query,
#             spaces='drive',
#             fields="files(id, name)"
#         ).execute()

#         files = results.get('files', [])

#         if not files:
#             return None

#         return files[0]  # ambil yang pertama

#     except Exception as e:
#         print("Error find file:", e)
#         return None
    
# def download_file(file_id: str, filename: str, save_path: str = "temp"):
#     try:
#         creds = get_credentials()
#         service = build('drive', 'v3', credentials=creds)

#         if not os.path.exists(save_path):
#             os.makedirs(save_path)

#         file_path = os.path.join(save_path, filename)

#         request = service.files().get_media(fileId=file_id)

#         with open(file_path, "wb") as f:
#             downloader = MediaIoBaseDownload(f, request)

#             done = False
#             while not done:
#                 status, done = downloader.next_chunk()

#         print(f"Download selesai: {file_path}")
#         return file_path

#     except Exception as e:
#         print("Error download file:", e)
#         return None

# def get_file_from_gdrive_flexible(base_filename: str):
#     creds = get_credentials()
#     service = build('drive', 'v3', credentials=creds)

#     query = f"name contains '{base_filename}'"

#     print(f"\n[DEBUG] QUERY: {query}")

#     results = service.files().list(
#         q=query,
#         fields="files(id, name, parents)",
#         supportsAllDrives=True,
#         includeItemsFromAllDrives=True
#     ).execute()

#     files = results.get("files", [])

#     if not files:
#         print("\n[DEBUG] File tidak ditemukan")
#         return None, None

#     print("\n[DEBUG] File ditemukan:")
#     for f in files:
#         print(f["name"], f.get("parents"))

#     file = files[0]

#     return download_file(file["id"], file["name"]), file["name"]

import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials", "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "credentials", "token.json")

FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")

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

        query = f"name contains '{base_filename}'"

        results = service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        files = results.get("files", [])

        if not files:
            return None, None

        file = files[0]

        return download_file(file["id"], file["name"]), file["name"]

    except Exception as e:
        print("FLEX SEARCH ERROR:", e)
        return None, None