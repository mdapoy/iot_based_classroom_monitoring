import os
import httpx
from typing import Optional
from dotenv import load_dotenv
from core.logger import logger

load_dotenv()

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
CALLBACK_URL       = os.getenv("CALLBACK_URL", "")
AAI_API_BASE       = "https://api.assemblyai.com"

# Speech model selection
_model_str = os.getenv("ASSEMBLYAI_MODEL", "best").lower()
_SPEECH_MODELS_MAP = {
    "u3":        ["universal-3-pro", "universal-2"],
    "best":      ["universal-2"],
    "universal": ["universal-2"],
    "nano":      ["universal-2"],
}
SPEECH_MODELS_LIST = _SPEECH_MODELS_MAP.get(_model_str, ["universal-2"])

# Speaker diarization range (None = auto-detect by AssemblyAI)
_min_spk     = os.getenv("SPEAKERS_MIN")
_max_spk     = os.getenv("SPEAKERS_MAX")
SPEAKERS_MIN = int(_min_spk) if _min_spk else None
SPEAKERS_MAX = int(_max_spk) if _max_spk else None


def _aai_headers() -> dict:
    return {
        "authorization": ASSEMBLYAI_API_KEY,
        "content-type":  "application/json",
    }


def upload_file_to_aai(file_path: str) -> str:
    """
    Upload file audio ke AssemblyAI CDN.
    Return: audio_url (CDN link untuk dipakai saat submit)
    """
    if not ASSEMBLYAI_API_KEY:
        raise Exception("ASSEMBLYAI_API_KEY belum dikonfigurasi di .env")

    headers = {
        "authorization": ASSEMBLYAI_API_KEY,
        "content-type":  "application/octet-stream",
    }

    with open(file_path, "rb") as f:
        data = f.read()

    resp = httpx.post(
        f"{AAI_API_BASE}/v2/upload",
        headers=headers,
        content=data,
        timeout=120.0,
    )
    resp.raise_for_status()

    audio_url = resp.json()["upload_url"]
    logger.info(
        f"[AAI UPLOAD] {os.path.basename(file_path)} "
        f"({os.path.getsize(file_path) // 1024} KB) → uploaded"
    )
    return audio_url


def submit_transcript_aai(
    audio_url:     str,
    language_code: str           = "id",
    webhook_url:   Optional[str] = None,
) -> str:
    """
    Submit permintaan transkripsi ke AssemblyAI API.
    Menggunakan speech_models (plural array) + speaker_labels + speaker_options.
    Return: transcript_id
    """
    payload: dict = {
        "audio_url":      audio_url,
        "speech_models":  SPEECH_MODELS_LIST,
        "speaker_labels": True,
        "language_code":  language_code,
        "punctuate":      True,
        "format_text":    True,
    }

    if webhook_url:
        payload["webhook_url"] = webhook_url

    if SPEAKERS_MIN is not None or SPEAKERS_MAX is not None:
        speaker_opts: dict = {}
        if SPEAKERS_MIN is not None:
            speaker_opts["min_speakers_expected"] = SPEAKERS_MIN
        if SPEAKERS_MAX is not None:
            speaker_opts["max_speakers_expected"] = SPEAKERS_MAX
        payload["speaker_options"] = speaker_opts

    resp = httpx.post(
        f"{AAI_API_BASE}/v2/transcript",
        headers=_aai_headers(),
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()

    transcript_id = resp.json()["id"]
    logger.info(
        f"[AAI SUBMIT] transcript_id={transcript_id} "
        f"models={SPEECH_MODELS_LIST} lang={language_code} "
        f"webhook={'yes' if webhook_url else 'no'}"
    )
    return transcript_id


def fetch_transcript_aai(transcript_id: str) -> dict:
    """
    Ambil hasil transkripsi dari AssemblyAI via GET /v2/transcript/{id}.
    Return: raw JSON dict dari AssemblyAI.
    """
    resp = httpx.get(
        f"{AAI_API_BASE}/v2/transcript/{transcript_id}",
        headers=_aai_headers(),
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def extract_chunk_data(
    data:       dict,
    offset_sec: float,
    chunk_name: str,
) -> dict:
    """
    Parse raw AssemblyAI JSON response ke format chunk internal.
    Timestamp utterances di-offset sesuai posisi chunk dalam rekaman penuh.

    Return dict:
      chunk_name, status (done/failed), error, offset_sec,
      duration_sec, utterances[], model_used
    """
    if data.get("status") == "error":
        return {
            "chunk_name":   chunk_name,
            "status":       "failed",
            "error":        data.get("error", "unknown error"),
            "offset_sec":   offset_sec,
            "utterances":   [],
            "duration_sec": 0,
        }

    utterances = []
    for utt in (data.get("utterances") or []):
        words = [
            {
                "text":       w.get("text", ""),
                "start_sec":  round(w["start"] / 1000 + offset_sec, 3),
                "end_sec":    round(w["end"]   / 1000 + offset_sec, 3),
                "confidence": w.get("confidence"),
                "speaker":    w.get("speaker"),
            }
            for w in (utt.get("words") or [])
        ]
        utterances.append({
            "speaker":      utt.get("speaker"),
            "start_sec":    round(utt["start"] / 1000 + offset_sec, 3),
            "end_sec":      round(utt["end"]   / 1000 + offset_sec, 3),
            "duration_sec": round((utt["end"] - utt["start"]) / 1000, 3),
            "text":         utt.get("text", ""),
            "words":        words,
        })

    # audio_duration dari AAI API dalam milidetik
    audio_dur_ms = data.get("audio_duration") or 0

    return {
        "chunk_name":   chunk_name,
        "status":       "done",
        "error":        None,
        "offset_sec":   offset_sec,
        "duration_sec": audio_dur_ms / 1000,
        "utterances":   utterances,
        "model_used":   data.get("speech_model_used", "unknown"),
    }
