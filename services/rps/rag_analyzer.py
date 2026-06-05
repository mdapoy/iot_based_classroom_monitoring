import time
from services.summarizer.summarizer import client, MODELS, classify_gemini_error
from core.logger import logger


def _format_curriculum(rps_semua: list[dict], pertemuan_ke: int) -> str:
    """
    Susun seluruh RPS menjadi teks kurikulum yang dapat dibaca LLM.
    Pertemuan target diberi tanda [TARGET] agar Gemini fokus di sana.
    """
    lines = []
    for row in rps_semua:
        p      = row.get("pertemuan_ke", "?")
        materi = row.get("materi_pembelajaran", "-")
        pengal = row.get("pengalaman_pembelajaran_mahasiswa", "-")
        tag    = " ← [TARGET: pertemuan ini]" if p == pertemuan_ke else ""
        lines.append(
            f"  Pertemuan {p:>2}{tag}\n"
            f"    Materi        : {materi}\n"
            f"    Pengalaman    : {pengal}"
        )
    return "\n".join(lines)


def _format_activity_summary(activity_summary: dict) -> str:
    """
    Format ringkasan aktivitas aktual untuk disisipkan ke dalam prompt RAG.
    """
    dominant = activity_summary.get("dominant", "-")
    lines = [
        f"  Aktivitas Dominan : {dominant}",
        f"  Ceramah           : {activity_summary.get('ceramah_pct', 0)}%"
        f"  ({activity_summary.get('ceramah_sec', 0):.0f}s)",
        f"  Tanya Jawab       : {activity_summary.get('tanya_jawab_pct', 0)}%"
        f"  ({activity_summary.get('tanya_jawab_sec', 0):.0f}s)",
        f"  Diskusi           : {activity_summary.get('diskusi_pct', 0)}%"
        f"  ({activity_summary.get('diskusi_sec', 0):.0f}s)",
        f"  Diam              : {activity_summary.get('diam_pct', 0)}%"
        f"  ({activity_summary.get('diam_sec', 0):.0f}s)",
    ]
    return "\n".join(lines)


def analyze_rps(
    transcript: str,
    rps_semua: list[dict],
    pertemuan_ke: int,
    activity_summary: dict | None = None,
    max_retries: int = 3,
) -> dict:
    """
    Bandingkan transcript dengan RPS menggunakan Gemini API.

    RAG flow:
      Retrieval  → rps_semua: seluruh baris rps_pertemuan untuk satu matkul
      Augmented  → transcript + kurikulum lengkap + aktivitas aktual (opsional)
      Generation → Gemini menentukan kesesuaian materi, status waktu,
                   kesesuaian metode pembelajaran, dan penjelasan

    Parameter:
      activity_summary: hasil dari classify_activities() — jika dikirim,
                        Gemini juga menganalisis kesesuaian metode pembelajaran
                        dengan kolom pengalaman_pembelajaran_mahasiswa di RPS.

    Return dict:
      success, kesesuaian, pertemuan_terdeteksi, status_waktu, penjelasan,
      metode_dominan, kesesuaian_metode, penjelasan_metode
    """
    if not rps_semua:
        return {"success": False, "error": "Daftar RPS kosong"}

    kurikulum_str = _format_curriculum(rps_semua, pertemuan_ke)
    total         = len(rps_semua)

    # Ambil pengalaman pembelajaran target untuk ditampilkan eksplisit
    rps_target   = next((r for r in rps_semua if r.get("pertemuan_ke") == pertemuan_ke), {})
    pengalaman   = rps_target.get("pengalaman_pembelajaran_mahasiswa", "-")

    # ── Bagian aktivitas (opsional) ──────────────────────────────
    if activity_summary and activity_summary.get("dominant"):
        activity_str = _format_activity_summary(activity_summary)
        activity_section = f"""
=== DATA AKTIVITAS AKTUAL REKAMAN ===
{activity_str}

"""
        metode_task = f"""
5. Perhatikan DATA AKTIVITAS AKTUAL di atas dan kolom Pengalaman Pembelajaran
   pada RPS pertemuan ke-{pertemuan_ke} berikut:
   "{pengalaman}"
   Apakah metode pembelajaran yang terjadi SESUAI dengan yang direncanakan RPS?
   Bandingkan aktivitas dominan aktual dengan metode yang tertulis di RPS.

"""
        metode_format = (
            "METODE_DOMINAN: [CERAMAH/TANYA_JAWAB/DISKUSI/DIAM]\n"
            "KESESUAIAN_METODE: [SESUAI/SEBAGIAN SESUAI/TIDAK SESUAI]\n"
            "PENJELASAN_METODE: [2-3 kalimat penjelasan kesesuaian metode]"
        )
    else:
        activity_section = ""
        metode_task      = ""
        metode_format    = (
            "METODE_DOMINAN: -\n"
            "KESESUAIAN_METODE: -\n"
            "PENJELASAN_METODE: -"
        )

    prompt = f"""Anda adalah analis pembelajaran yang mengevaluasi rekaman kuliah berdasarkan Rencana Pembelajaran Semester (RPS).

=== KURIKULUM LENGKAP ({total} pertemuan) ===
{kurikulum_str}

=== TRANSKRIP REKAMAN (Pertemuan ke-{pertemuan_ke}) ===
{transcript}
{activity_section}
=== TUGAS ANALISIS ===
1. Tentukan pertemuan mana dalam kurikulum yang paling cocok dengan isi transkrip di atas.
2. Bandingkan dengan pertemuan TARGET (ke-{pertemuan_ke}):
   - Jika konten transkrip cocok dengan pertemuan SETELAH ke-{pertemuan_ke} → materi LEBIH CEPAT dari jadwal
   - Jika konten transkrip cocok tepat dengan pertemuan ke-{pertemuan_ke}   → TEPAT WAKTU
   - Jika konten transkrip cocok dengan pertemuan SEBELUM ke-{pertemuan_ke} → materi LEBIH LAMBAT dari jadwal
3. Nilai kesesuaian isi transkrip dengan RPS pertemuan ke-{pertemuan_ke}:
   - SESUAI          : topik utama sama persis
   - SEBAGIAN SESUAI : ada irisan topik, tapi tidak lengkap atau ada topik di luar RPS
   - TIDAK SESUAI    : topik transkrip tidak berkaitan dengan RPS pertemuan ke-{pertemuan_ke}
4. Tulis penjelasan singkat 2-3 kalimat yang menjelaskan temuan poin 1-3.
5. Periksa apakah transkrip JUGA membahas materi dari pertemuan LAIN selain target ke-{pertemuan_ke}:
   - Jika ada materi dari pertemuan sebelumnya → sebutkan "pertemuan ke-X (sebelumnya): [topik]"
   - Jika ada materi dari pertemuan mendatang  → sebutkan "pertemuan ke-X (mendatang): [topik]"
   - Jika ada materi yang tidak ada di RPS manapun → sebutkan "di luar RPS: [topik]"
   - Jika tidak ada materi tambahan di luar target → tuliskan "TIDAK ADA"
{metode_task}
Jawab HANYA dalam format berikut (tanpa teks lain, tanpa markdown):
KESESUAIAN: [SESUAI/SEBAGIAN SESUAI/TIDAK SESUAI]
PERTEMUAN_TERDETEKSI: [nomor pertemuan yang paling cocok dengan isi transkrip]
STATUS: [LEBIH CEPAT/TEPAT WAKTU/LEBIH LAMBAT]
PENJELASAN: [2-3 kalimat penjelasan materi]
CATATAN: [TIDAK ADA / deskripsi singkat materi tambahan di luar target pertemuan]
{metode_format}
"""

    for model_name in MODELS:
        delay = 2

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )

                parsed = _parse_response(response.text.strip())
                logger.info(
                    f"[RAG] Success | model={model_name} "
                    f"kesesuaian={parsed.get('kesesuaian')} "
                    f"terdeteksi=pertemuan-{parsed.get('pertemuan_terdeteksi')} "
                    f"status={parsed.get('status_waktu')} "
                    f"kesesuaian_metode={parsed.get('kesesuaian_metode')}"
                )
                return {"success": True, **parsed}

            except Exception as e:
                error_type = classify_gemini_error(str(e))
                retryable  = error_type in ["high_demand", "rate_limit", "429_unknown"]

                if retryable and attempt < max_retries - 1:
                    logger.warning(
                        f"[RAG RETRY] model={model_name} "
                        f"attempt={attempt + 1} sleep={delay}s"
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue

                logger.warning(
                    f"[RAG SKIP] model={model_name} reason={error_type}"
                )
                break

    logger.error("[RAG FAILED] semua model gagal")
    return {"success": False, "error": "Analisis RPS gagal"}


def _parse_response(text: str) -> dict:
    """Parse output Gemini ke dict terstruktur."""
    result = {
        "kesesuaian":           "-",
        "pertemuan_terdeteksi": "-",
        "status_waktu":         "-",
        "penjelasan":           "-",
        "catatan":              "-",
        "metode_dominan":       "-",
        "kesesuaian_metode":    "-",
        "penjelasan_metode":    "-",
    }

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("KESESUAIAN:") and not line.startswith("KESESUAIAN_METODE:"):
            result["kesesuaian"] = line.split(":", 1)[1].strip()
        elif line.startswith("PERTEMUAN_TERDETEKSI:"):
            result["pertemuan_terdeteksi"] = line.split(":", 1)[1].strip()
        elif line.startswith("STATUS:"):
            result["status_waktu"] = line.split(":", 1)[1].strip()
        elif line.startswith("PENJELASAN:") and not line.startswith("PENJELASAN_METODE:"):
            result["penjelasan"] = line.split(":", 1)[1].strip()
        elif line.startswith("CATATAN:"):
            result["catatan"] = line.split(":", 1)[1].strip()
        elif line.startswith("METODE_DOMINAN:"):
            result["metode_dominan"] = line.split(":", 1)[1].strip()
        elif line.startswith("KESESUAIAN_METODE:"):
            result["kesesuaian_metode"] = line.split(":", 1)[1].strip()
        elif line.startswith("PENJELASAN_METODE:"):
            result["penjelasan_metode"] = line.split(":", 1)[1].strip()

    return result
