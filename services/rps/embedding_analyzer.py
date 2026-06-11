"""Embedding-based subbab coverage analyzer.

Uses Gemini text-embedding-004 to measure cosine similarity between
RPS sub-topics and lecture transcript chunks. Returns a numeric coverage
percentage (0-100) and short LLM reasoning grounded in actual transcript
snippets.
"""

import math
import os
import re
import time

import httpx
from dotenv import load_dotenv

from core.logger import logger
from services.summarizer.summarizer import client, MODELS, classify_gemini_error, get_active_api_key

load_dotenv()

EMBED_MODEL = "gemini-embedding-001"   # text-embedding-004 renamed
MAX_SNIPPET = 300    # chars of best_chunk passed to LLM for reasoning
_EMBED_URL  = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{EMBED_MODEL}:embedContent?key={{key}}"
)


# ══════════════════════════════════════════════════════════════════════════════
# Parsing
# ══════════════════════════════════════════════════════════════════════════════

def parse_subbab(materi: str) -> list[str]:
    """
    Parse rps_pertemuan.materi_pembelajaran into a list of sub-topics.

    Handles comma/semicolon-separated lists with optional "Label: " prefix.
    Example inputs:
      "Email Forensik; Analisis header; Metadata; Tracing"
      "Topik: Hash function, Chain of custody, Akuisisi bukti"
    """
    if not materi or not materi.strip():
        return []

    # Strip optional leading label like "Topik: " or "Pertemuan 1: "
    text = re.sub(r'^[^:]{1,30}:\s*', '', materi.strip(), count=1)

    parts = re.split(r'[,;]', text)
    result = []
    for p in parts:
        s = p.strip()
        if s and len(s) > 2:
            result.append(s)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Embedding helpers
# ══════════════════════════════════════════════════════════════════════════════

def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    dot  = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts via Gemini REST v1beta embedContent.

    gemini-embedding-001 only supports the singular embedContent endpoint —
    batchEmbedContents is not available. We reuse a single httpx.Client for
    all texts to avoid TCP connection overhead per call.
    """
    if not texts:
        return []

    url = _EMBED_URL.format(key=get_active_api_key())
    embeddings = []

    with httpx.Client(timeout=60) as http:
        for text in texts:
            payload = {"content": {"parts": [{"text": text}]}}
            resp = http.post(url, json=payload)
            resp.raise_for_status()
            embeddings.append(resp.json()["embedding"]["values"])

    return embeddings


# ══════════════════════════════════════════════════════════════════════════════
# Coverage analysis
# ══════════════════════════════════════════════════════════════════════════════

def check_subbab_coverage(
    subbab_list: list[str],
    chunks: list[dict],
) -> dict:
    """
    Hitung skor kesesuaian materi per subbab menggunakan raw cosine similarity.

    Skor per subbab = max cosine similarity subbab vs semua chunk × 100 (0–100).
    kesesuaian_pct  = rata-rata skor semua subbab.

    Tidak ada threshold biner — nilai kontinu sehingga variasi antar pertemuan
    lebih terlihat jelas.

    Returns:
        {
          pct:         float|None,   # None jika embedding gagal
          total_count: int,
          detail:      [{subbab, score, max_sim, best_chunk}]
        }
    """
    if not subbab_list:
        return {"pct": None, "total_count": 0, "detail": []}

    chunk_texts = [c.get("transcript", "") for c in chunks]
    chunk_texts = [t for t in chunk_texts if t and t.strip()]

    if not chunk_texts:
        return {
            "pct":         0.0,
            "total_count": len(subbab_list),
            "detail": [
                {"subbab": s, "score": 0.0, "max_sim": 0.0, "best_chunk": ""}
                for s in subbab_list
            ],
        }

    all_texts = subbab_list + chunk_texts
    try:
        all_embeddings = _embed_texts(all_texts)
    except Exception as e:
        logger.error(f"[EMBED] batch embed gagal: {e}")
        return {"pct": None, "total_count": len(subbab_list), "detail": []}

    subbab_embs = all_embeddings[: len(subbab_list)]
    chunk_embs  = all_embeddings[len(subbab_list):]

    detail: list[dict] = []

    for subbab, s_emb in zip(subbab_list, subbab_embs):
        best_sim = 0.0
        best_idx = 0
        for j, c_emb in enumerate(chunk_embs):
            sim = _cosine_similarity(s_emb, c_emb)
            if sim > best_sim:
                best_sim = sim
                best_idx = j

        detail.append({
            "subbab":     subbab,
            "score":      round(best_sim * 100, 1),
            "max_sim":    round(best_sim, 4),
            "best_chunk": chunk_texts[best_idx][:MAX_SNIPPET] if chunk_texts else "",
        })

    pct = round(sum(d["score"] for d in detail) / len(detail), 1)
    logger.info(
        f"[EMBED COVERAGE] subbab={len(detail)} avg_score={pct}% "
        f"scores={[d['score'] for d in detail]}"
    )
    return {
        "pct":         pct,
        "total_count": len(subbab_list),
        "detail":      detail,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reasoning
# ══════════════════════════════════════════════════════════════════════════════

def generate_reasoning(
    coverage_result: dict,
    rps_materi: str,
    max_retries: int = 2,
) -> str:
    """
    Generate a short (2-3 sentence) explanation for the coverage score.

    LLM receives one snippet (~200 chars) per subbab as evidence —
    much cheaper than sending the full transcript.

    Returns reasoning string, or empty string on total failure.
    """
    detail = coverage_result.get("detail", [])
    if not detail:
        return ""

    pct   = coverage_result.get("pct", 0)
    total = coverage_result.get("total_count", 0)

    lines = []
    for d in detail:
        snippet = d["best_chunk"][:200].replace("\n", " ") if d["best_chunk"] else "-"
        lines.append(
            f"- {d['subbab']}: skor {d['score']:.0f}/100 (sim={d['max_sim']:.2f})\n"
            f"  Cuplikan transkrip: \"{snippet}\""
        )
    subbab_ctx = "\n".join(lines)

    prompt = (
        f"Anda adalah analis evaluasi pembelajaran. Tulis alasan singkat (2-3 kalimat) "
        f"mengapa rata-rata skor kesesuaian materi pertemuan ini adalah {pct:.0f}/100 "
        f"dari {total} subbab RPS.\n\n"
        f"Topik RPS: {rps_materi}\n\n"
        f"Skor per subbab (0–100, berdasarkan kemiripan semantik dengan transkrip kuliah):\n"
        f"{subbab_ctx}\n\n"
        f"Jelaskan subbab mana yang skornya tinggi (materi terbahas) dan yang rendah "
        f"(materi kurang/tidak terbahas). "
        f"Kembalikan HANYA teks alasan, tanpa judul atau poin."
    )

    for model_name in MODELS:
        delay = 2
        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt)
                text = resp.text.strip()
                logger.info(f"[EMBED REASONING] model={model_name} len={len(text)}")
                return text
            except Exception as e:
                etype = classify_gemini_error(str(e))
                if etype in ("high_demand", "rate_limit", "429_unknown") and attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                logger.warning(f"[EMBED REASONING] skip model={model_name} reason={etype}")
                break

    # Rule-based fallback
    high = [d["subbab"] for d in detail if d["score"] >= 60]
    low  = [d["subbab"] for d in detail if d["score"] < 60]
    parts = [f"Rata-rata skor kesesuaian materi {pct:.0f}/100 dari {total} subbab."]
    if high:
        parts.append(f"Subbab dengan skor tinggi: {', '.join(high)}.")
    if low:
        parts.append(f"Subbab dengan skor rendah: {', '.join(low)}.")
    return " ".join(parts)
