from repositories.supabase_client import supabase
from services.storage.storage_service import upload_transcript

def handle_stt_callback(task_id: str, text: str):
    file_name = f"{task_id}.txt"

    # upload
    path = upload_transcript(file_name, text)

    # update DB
    supabase.table("reports").update({
        "transcription_done": True,
        "file_path": path
    }).match({
        "task_id": task_id
    }).execute()

    return path