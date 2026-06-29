import json
import mimetypes
from repositories.supabase_client import supabase

BUCKET              = "transcripts"
BUCKET_SUMMARY      = "summary"
BUCKET_EVALUATION   = "evaluasi"
BUCKET_DIARIZATION  = "diarization"

# Signed URL berlaku 1 jam — cukup untuk sesi baca PDF di FE
_SIGNED_EXPIRES = 3600


def _signed_url(bucket: str, path: str) -> str:
    res = supabase.storage.from_(bucket).create_signed_url(path, _SIGNED_EXPIRES)
    return res["signedURL"]


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
        supabase.storage.from_(BUCKET_SUMMARY).upload(
            storage_path,
            f,
            {"content-type": mime_type or "application/octet-stream"}
        )
    return _signed_url(BUCKET_SUMMARY, storage_path)


def get_public_summary_url(filename: str) -> str:
    return _signed_url(BUCKET_SUMMARY, filename)


def upload_evaluation(local_path: str, storage_path: str) -> str:
    """Upload PDF laporan evaluasi ke bucket 'evaluasi'."""
    mime_type, _ = mimetypes.guess_type(local_path)
    with open(local_path, "rb") as f:
        supabase.storage.from_(BUCKET_EVALUATION).upload(
            storage_path,
            f,
            {"content-type": mime_type or "application/pdf"},
        )
    return _signed_url(BUCKET_EVALUATION, storage_path)


def get_public_evaluation_url(filename: str) -> str:
    return _signed_url(BUCKET_EVALUATION, filename)


def upload_diarization(report_id: int, utterances: list) -> str:
    path = f"{report_id}.json"
    supabase.storage.from_(BUCKET_DIARIZATION).upload(
        path,
        json.dumps(utterances, ensure_ascii=False).encode("utf-8"),
        {"content-type": "application/json"},
    )
    return path


def download_diarization(path: str) -> list:
    raw = supabase.storage.from_(BUCKET_DIARIZATION).download(path)
    return json.loads(raw.decode("utf-8"))