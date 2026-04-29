from repositories.supabase_client import supabase
import mimetypes

BUCKET = "transcripts"
BUCKET_SUMMARY = "summary"

def upload_transcript(filename: str, content: str):
    supabase.storage.from_(BUCKET).upload(
        filename,
        content.encode("utf-8"),
        {"content-type": "text/plain"}
    )

    return filename

def download_transcript(path: str):
    res = supabase.storage.from_(BUCKET).download(path)
    return res.decode("utf-8")

def upload_summary(local_path, storage_path):
    mime_type, _ = mimetypes.guess_type(local_path)

    with open(local_path, "rb") as f:
        supabase.storage.from_("summary").upload(
            storage_path,
            f,
            {
                "content-type": mime_type or "application/octet-stream"
            }
        )

    return supabase.storage.from_("summary").get_public_url(storage_path)

def get_public_summary_url(filename: str):
    return supabase.storage.from_(BUCKET_SUMMARY).get_public_url(filename)