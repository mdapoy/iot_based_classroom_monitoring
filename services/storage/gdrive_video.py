from services.storage.google_drive import get_drive_service
from core.logger import logger

service = get_drive_service()


def _list_files_in_folder(folder_id: str, mime_filter: str) -> list[dict]:
    """
    Ambil semua file di folder_id yang cocok dengan mime_filter.
    - Hanya file langsung di dalam folder_id (in parents), tidak rekursif
    - Tidak termasuk file yang sudah di-trash
    - Handle pagination agar semua file terambil (>100)
    """
    results = []
    page_token = None
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType contains '{mime_filter}' "
        f"and trashed = false"
    )

    while True:
        params = {
            "q":      query,
            "fields": "nextPageToken, files(id, name)",
            "pageSize": 1000,
            "supportsAllDrives":        True,
            "includeItemsFromAllDrives": True,
        }
        if page_token:
            params["pageToken"] = page_token

        response = service.files().list(**params).execute()
        results.extend(response.get("files", []))

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    logger.info(
        f"[GDRIVE LIST] folder={folder_id} mime={mime_filter} "
        f"total={len(results)}"
    )
    return results


def list_videos(folder_id: str) -> list[dict]:
    return _list_files_in_folder(folder_id, "video/")


def list_audios(folder_id: str) -> list[dict]:
    return _list_files_in_folder(folder_id, "audio/")
