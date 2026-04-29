from services.storage.google_drive import get_drive_service


def list_videos(folder_id):
    service = get_drive_service()

    results = service.files().list(
        q=f"'{folder_id}' in parents and mimeType contains 'video/'",
        fields="files(id,name)"
    ).execute()

    return results.get("files", [])