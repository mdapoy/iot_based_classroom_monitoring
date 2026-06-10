"""Embedding-based subbab coverage analyzer.

Uses Gemini text-embedding-004 to measure cosine similarity between
RPS sub-topics and lecture transcript chunks. Returns a numeric coverage
percentage (0-100) and short LLM reasoning grounded in actual transcript
snippets.
"""

import math
import re
import time

from core.logger import logger
from services.summarizer.summarizer import client, MODELS, classify_gemini_error

EMBED_MODEL  = "text-embedding-004"
THRESHOLD    = 0.75   # cosine similarity minimum to count as "covered"
MAX_SNIPPET  = 300    # chars of best_chunk passed to LLM for reasoning


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
    """Batch-embed texts using Gemini text-embedding-004."""
    if not texts:
        return []
    result = client.models.embed_content(model=EMBED_MODEL, contents=texts)
    return [e.values for e in result.embeddings]


# ══════════════════════════════════════════════════════════════════════════════
# Coverage analysis
# ══════════════════════════════════════════════════════════════════════════════

def check_subbab_coverage(
    subbab_list: list[str],
    chunks: list[dict],
    threshold: float = THRESHOLD,
) -> dict:
    """
    Determine which RPS sub-topics were covered in the lecture.

    All subbab + chunk transcripts are embedded in one batch API call.
    For each subbab, max cosine similarity across all chunks is computed.
    A subbab is "covered" if max_sim >= threshold.

    Args:
        subbab_list: from parse_subbab()
        chunks:      list of audio_chunk rows — each must have 'transcript' key
        threshold:   cosine similarity cutoff (default 0.75)

    Returns:
        {
          pct: float|None,   # None if embedding failed
          covered_count: int,
          total_count: int,
          detail: [{subbab, covered, max_sim, best_chunk}]
        }
    """
    if not subbab_list:
        return {"pct": None, "covered_count": 0, "total_count": 0, "detail": []}

    chunk_texts = [c.get("transcript", "") for c in chunks]
    chunk_texts = [t for t in chunk_texts if t and t.strip()]

    if not chunk_texts:
        return {
            "pct":           0.0,
            "covered_count": 0,
            "total_count":   len(subbab_list),
            "detail": [
                {"subbab": s, "covered": False, "max_sim": 0.0, "best_chunk": ""}
                for s in subbab_list
            ],
        }

    # Single batch: subbab first, then chunk transcripts
    all_texts = subbab_list + chunk_texts
    try:
        all_embeddings = _embed_texts(all_texts)
    except Exception as e:
        logger.error(f"[EMBED] batch embed gagal: {e}")
        return {"pct": None, "covered_count": 0, "total_count": len(subbab_list), "detail": []}

    subbab_embs = all_embeddings[: len(subbab_list)]
    chunk_embs  = all_embeddings[len(subbab_list):]

    covered_count = 0
    detail: list[dict] = []

    for subbab, s_emb in zip(subbab_list, subbab_embs):
        best_sim = 0.0
        best_idx = 0
        for j, c_emb in enumerate(chunk_embs):
            sim = _cosine_similarity(s_emb, c_emb)
            if sim > best_sim:
                best_sim = sim
                best_idx = j

        covered = best_sim >= threshold
        if covered:
            covered_count += 1

        detail.append({
            "subbab":     subbab,
            "covered":    covered,
            "max_sim":    round(best_sim, 4),
            "best_chunk": chunk_texts[best_idx][:MAX_SNIPPET] if chunk_texts else "",
        })

    pct = round(covered_count / len(subbab_list) * 100, 1)
    logger.info(
        f"[EMBED COVERAGE] covered={covered_count}/{len(subbab_list)} "
        f"({pct}%) threshold={threshold}"
    )
    return {
        "pct":           pct,
        "covered_count": covered_count,
        "total_count":   len(subbab_list),
        "detail":        detail,
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
    n_ok  = coverage_result.get("covered_count", 0)

    lines = []
    for d in detail:
        status  = "terbahas" if d["covered"] else "tidak terbahas"
        snippet = d["best_chunk"][:200].replace("\n", " ") if d["best_chunk"] else "-"
        lines.append(
            f"- {d['subbab']}: {status} (sim={d['max_sim']:.2f})\n"
            f"  Cuplikan: \"{snippet}\""
        )
    subbab_ctx = "\n".join(lines)

    prompt = (
        f"Anda adalah analis evaluasi pembelajaran. Tulis alasan singkat (2-3 kalimat) "
        f"mengapa nilai kesesuaian materi pertemuan ini adalah {pct:.0f}% "
        f"({n_ok}/{total} subbab terbahas).\n\n"
        f"Topik RPS: {rps_materi}\n\n"
        f"Hasil analisis per subbab:\n{subbab_ctx}\n\n"
        f"Sebutkan subbab yang terbahas dan yang tidak berdasarkan data di atas. "
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
    covered_names   = [d["subbab"] for d in detail if d["covered"]]
    not_covered     = [d["subbab"] for d in detail if not d["covered"]]
    parts = [f"Nilai kesesuaian {pct:.0f}% ({n_ok}/{total} subbab terbahas)."]
    if covered_names:
        parts.append(f"Subbab yang terbahas: {', '.join(covered_names)}.")
    if not_covered:
        parts.append(f"Subbab yang tidak terbahas: {', '.join(not_covered)}.")
    return " ".join(parts)
