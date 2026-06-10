"""
method_analyzer.py
------------------
Hitung skor kesesuaian metode pembelajaran (0-100) berdasarkan overlap
antara aktivitas yang direncanakan di RPS dengan aktivitas aktual dari rekaman.

Formula:
    expected_per = 100 / N          (porsi ideal per aktivitas yang direncanakan)
    score_i      = min(actual_pct_i / expected_per, 1.0)
    method_score = mean(score_i) × 100

Contoh:
    RPS "Kuliah dan diskusi"  → expected = [CERAMAH, DISKUSI], N=2, expected_per=50%
    Aktual: ceramah=100%, diskusi=0%
    score_CERAMAH = min(100/50, 1.0) = 1.0
    score_DISKUSI = min(0/50,   1.0) = 0.0
    method_score  = (1.0+0.0)/2 × 100 = 50%
"""

from core.logger import logger

# ── Keyword → activity mapping ────────────────────────────────────────────────
_KEYWORD_MAP: dict[str, list[str]] = {
    "CERAMAH": [
        "kuliah", "ceramah", "lecture", "presentasi", "paparan",
        "penjelasan dosen", "penyampaian materi",
    ],
    "DISKUSI": [
        "diskusi", "discussion", "kelompok", "group",
        "kolaborasi", "peer", "berdiskusi",
    ],
    "TANYA_JAWAB": [
        "tanya jawab", "tanya-jawab", "question", "kuis", "quiz",
        "latihan soal", "soal", "evaluasi", "latihan",
    ],
}

# activity key → field di activity_result
_PCT_FIELD: dict[str, str] = {
    "CERAMAH":    "ceramah_pct",
    "DISKUSI":    "diskusi_pct",
    "TANYA_JAWAB": "tanya_jawab_pct",
}


# ── Public API ────────────────────────────────────────────────────────────────

def parse_expected_activities(pengalaman_rps: str) -> list[str]:
    """
    Parse teks pengalaman_pembelajaran_mahasiswa → list aktivitas yang direncanakan.
    Return subset dari ["CERAMAH", "DISKUSI", "TANYA_JAWAB"].
    """
    if not pengalaman_rps or not pengalaman_rps.strip():
        return []

    text = pengalaman_rps.lower()
    found: list[str] = []
    for activity, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw in text:
                found.append(activity)
                break

    return found


def compute_method_score(
    pengalaman_rps: str,
    activity_result: dict | None,
) -> float | None:
    """
    Hitung skor kesesuaian metode (0–100).

    Return None jika:
    - tidak ada aktivitas terdeteksi dari teks RPS
    - activity_result kosong/None
    """
    if not activity_result:
        return None

    expected = parse_expected_activities(pengalaman_rps)
    if not expected:
        logger.warning(
            f"[METHOD SCORE] Tidak ada aktivitas terdeteksi dari RPS: "
            f"{str(pengalaman_rps)[:80]!r}"
        )
        return None

    n            = len(expected)
    expected_per = 100.0 / n

    scores: list[float] = []
    for activity in expected:
        field     = _PCT_FIELD.get(activity)
        if field is None:
            continue
        actual_pct = float(activity_result.get(field) or 0)
        score_i    = min(actual_pct / expected_per, 1.0)
        scores.append(score_i)

    if not scores:
        return None

    method_score = round(sum(scores) / len(scores) * 100, 1)
    logger.info(
        f"[METHOD SCORE] expected={expected} "
        f"scores={[round(s*100,1) for s in scores]} "
        f"method_score={method_score}%"
    )
    return method_score
