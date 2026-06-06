import time
import asyncio
from collections import Counter
from datetime import date

# Batasi LLM call paralel — sama dengan pola semaphore di summary_worker.py
_LLM_SEMAPHORE = asyncio.Semaphore(2)

from repositories.supabase_client import supabase
from services.summarizer.summarizer import client, MODELS, classify_gemini_error
from services.rps.rps_service import SEMESTER_START_DATE, get_meeting_week
from core.logger import logger


# ══════════════════════════════════════════════════════════════════════════════
# Konstanta Periode
# ══════════════════════════════════════════════════════════════════════════════

PERIOD_RANGES: dict[str, list[int]] = {
    "1-7":  [1, 2, 3, 4, 5, 6, 7],
    "9-15": [9, 10, 11, 12, 13, 14, 15],
    "1-15": [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15],
}

PERIOD_LABELS: dict[str, str] = {
    "1-7":  "Minggu 1–7 (Pra-UTS)",
    "9-15": "Minggu 9–15 (Pasca-UTS)",
    "1-15": "Minggu 1–15 (Keseluruhan)",
}


# ══════════════════════════════════════════════════════════════════════════════
# Helper Private
# ══════════════════════════════════════════════════════════════════════════════

def _derive_semester_label() -> str:
    """Derive label semester dari SEMESTER_START_DATE di env."""
    start = date.fromisoformat(SEMESTER_START_DATE)
    if 2 <= start.month <= 7:
        return f"Genap {start.year - 1}/{start.year}"
    return f"Ganjil {start.year}/{start.year + 1}"


def _majority_kesesuaian(values: list[str]) -> str:
    """
    Tentukan kesesuaian matkul dari list nilai per pertemuan.
    Gunakan worst-case: jika ada satu TIDAK SESUAI → keseluruhan Tidak Sesuai.
    """
    vals = [v.upper().strip() for v in values if v and v.strip() not in ("-", "")]
    if not vals:
        return "-"
    if any("TIDAK SESUAI" in v for v in vals):
        return "Tidak Sesuai"
    if any("SEBAGIAN" in v for v in vals):
        return "Sebagian Sesuai"
    return "Sesuai"


def _mode_activity(values: list[str]) -> str:
    """Kembalikan aktivitas yang paling sering muncul (modus)."""
    vals = [v.strip() for v in values if v and v.strip() not in ("-", "")]
    if not vals:
        return "-"
    return Counter(vals).most_common(1)[0][0]


def _format_aktivitas_str(row: dict) -> str:
    """Format string aktivitas untuk tabel PDF, misal: 'Ceramah 70% · Diskusi 30%'."""
    parts = []
    for label, key in [
        ("Ceramah",     "ceramah_pct"),
        ("Diskusi",     "diskusi_pct"),
        ("Tanya Jawab", "tanya_jawab_pct"),
        ("Diam",        "diam_pct"),
    ]:
        pct = int(row.get(key) or 0)
        if pct > 0:
            parts.append(f"{label} {pct}%")
    return " · ".join(parts) if parts else "-"


def _status_kinerja(matkul_list: list[dict]) -> str:
    """
    Tentukan status kinerja keseluruhan dosen berdasarkan semua matkul.
      Baik             : semua SESUAI + rata-rata tepat waktu ≥ 80%
      Cukup            : ada SEBAGIAN SESUAI atau rata-rata tepat waktu < 80%
      Perlu Perhatian  : ada TIDAK SESUAI
    """
    kesesuaian_list = [m["kesesuaian_rps"] for m in matkul_list]
    tepat_list      = [m["pct_tepat_waktu"] for m in matkul_list]
    avg_tepat       = sum(tepat_list) / len(tepat_list) if tepat_list else 0

    if any("Tidak Sesuai" in k for k in kesesuaian_list):
        return "Perlu Perhatian"
    if any("Sebagian" in k for k in kesesuaian_list) or avg_tepat < 80:
        return "Cukup"
    return "Baik"


# ══════════════════════════════════════════════════════════════════════════════
# LLM: Kesimpulan per Matkul
# ══════════════════════════════════════════════════════════════════════════════

def _generate_kesimpulan_matkul(matkul_data: dict, max_retries: int = 3) -> str:
    nama    = matkul_data.get("nama_matkul", "-")
    kode    = matkul_data.get("kode_matkul", "-")
    total   = matkul_data.get("total_pertemuan", 0)
    tepat   = matkul_data.get("pct_tepat_waktu", 0.0)
    kes_rps = matkul_data.get("kesesuaian_rps", "-")
    metode  = matkul_data.get("metode_dominan", "-")

    pertemuan_lines = [
        f"  Pertemuan {p['pertemuan_ke']:>2}: {str(p['topik'])[:50]}"
        f" | Kesesuaian: {p['kesesuaian_materi']}"
        f" | Status: {p['status_waktu']}"
        for p in matkul_data.get("pertemuan", [])
    ]
    tabel_str = "\n".join(pertemuan_lines) or "  (tidak ada data)"

    prompt = f"""Anda adalah analis kinerja mengajar dosen universitas. Tulis SATU paragraf kesimpulan \
(3-4 kalimat) dalam Bahasa Indonesia mengenai pelaksanaan mata kuliah berikut:

Mata Kuliah               : {nama} ({kode})
Total Pertemuan Dianalisis: {total}
Ketepatan Waktu           : {tepat:.1f}%
Kesesuaian RPS            : {kes_rps}
Metode Pembelajaran Dominan: {metode}

Ringkasan per pertemuan:
{tabel_str}

Tulis paragraf yang objektif dan padat. Fokus pada kesesuaian materi, ketepatan waktu, \
dan metode yang digunakan. Tidak menggunakan poin-poin atau judul.
Kembalikan HANYA teks paragraf."""

    for model_name in MODELS:
        delay = 2
        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt)
                text = resp.text.strip()
                logger.info(f"[EVAL KESIMPULAN] matkul={kode} model={model_name}")
                return text
            except Exception as e:
                etype = classify_gemini_error(str(e))
                if etype in ("high_demand", "rate_limit", "429_unknown") and attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                logger.warning(f"[EVAL KESIMPULAN] skip model={model_name} reason={etype}")
                break

    # Fallback rule-based
    return (
        f"Mata kuliah {nama} telah dilaksanakan selama {total} pertemuan "
        f"dengan ketepatan waktu {tepat:.1f}% dan kesesuaian RPS {kes_rps}. "
        f"Metode pembelajaran yang dominan digunakan adalah {metode}."
    )


# ══════════════════════════════════════════════════════════════════════════════
# LLM: Rekomendasi Global
# ══════════════════════════════════════════════════════════════════════════════

def _generate_rekomendasi_global(eval_data: dict, max_retries: int = 3) -> str:
    nama_dosen     = eval_data["dosen"].get("nama_lengkap", "-")
    status_kinerja = eval_data["ringkasan"]["status_kinerja"]
    semester_label = eval_data["semester_label"]

    matkul_lines = [
        f"  - {m['nama_matkul']} ({m['kode_matkul']}): "
        f"Kesesuaian={m['kesesuaian_rps']}, "
        f"Tepat Waktu={m['pct_tepat_waktu']:.1f}%, "
        f"Metode Dominan={m['metode_dominan']}"
        for m in eval_data["ringkasan"]["per_matkul"]
    ]
    matkul_str = "\n".join(matkul_lines)

    prompt = f"""Anda adalah analis kinerja mengajar dosen universitas. Tulis kesimpulan dan rekomendasi \
untuk laporan evaluasi dosen berikut dalam Bahasa Indonesia yang formal:

Dosen         : {nama_dosen}
Periode       : {semester_label}
Status Kinerja: {status_kinerja}

Ringkasan per mata kuliah:
{matkul_str}

Tulis DUA paragraf:
1. Paragraf pertama: kesimpulan umum kinerja mengajar dosen (3-4 kalimat)
2. Paragraf kedua: rekomendasi konkret (2-3 kalimat, dimulai dengan kata "Rekomendasi:")

Tidak menggunakan poin-poin atau judul. Kembalikan HANYA dua paragraf tersebut."""

    for model_name in MODELS:
        delay = 2
        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt)
                text = resp.text.strip()
                logger.info(f"[EVAL REKOMENDASI] dosen={nama_dosen} model={model_name}")
                return text
            except Exception as e:
                etype = classify_gemini_error(str(e))
                if etype in ("high_demand", "rate_limit", "429_unknown") and attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                logger.warning(f"[EVAL REKOMENDASI] skip model={model_name} reason={etype}")
                break

    # Fallback rule-based
    return (
        f"Berdasarkan analisis rekaman perkuliahan periode {semester_label}, "
        f"dosen {nama_dosen} menunjukkan kinerja mengajar dengan status {status_kinerja}. "
        f"Secara keseluruhan pelaksanaan perkuliahan telah berlangsung sesuai rencana.\n\n"
        f"Rekomendasi: Pertahankan konsistensi dalam penyampaian materi dan lakukan "
        f"evaluasi berkala terhadap metode pembelajaran yang digunakan."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def get_dosen_info(kode_dosen: str) -> dict:
    """Ambil data dosen dari tabel dosen berdasarkan kode_dosen."""
    try:
        res = (
            supabase.table("dosen")
            .select("kode_dosen, nama_lengkap, program_studi, nip")
            .eq("kode_dosen", kode_dosen)
            .single()
            .execute()
        )
        return res.data or {}
    except Exception as e:
        logger.warning(f"[EVAL] get_dosen_info gagal kode={kode_dosen}: {e}")
        return {}


def get_matkul_for_dosen(kode_dosen: str) -> list[dict]:
    """
    Ambil semua mata kuliah yang diampu dosen ini (dari jadwal_kuliah).
    Menggunakan in-memory cache (TTL 10 menit) — filter in-memory per dosen.
    Return list of {kode_matkul, nama_matkul}, sudah deduplikasi per kode.
    """
    try:
        from repositories.cache import get_all_jadwal
        jadwal = get_all_jadwal()
        seen: dict[str, str] = {}
        for j in jadwal:
            if j.get("dosen_utama") == kode_dosen:
                kode = j.get("kode_mata_kuliah")
                if kode and kode not in seen:
                    seen[kode] = j.get("mata_kuliah", kode)
        return [{"kode_matkul": k, "nama_matkul": v} for k, v in seen.items()]
    except Exception as e:
        logger.warning(f"[EVAL] get_matkul_for_dosen gagal kode={kode_dosen}: {e}")
        return []


def get_all_dosen_teknik_komputer() -> list[dict]:
    """
    Ambil semua dosen S1 Teknik Komputer dari tabel dosen.
    Digunakan untuk populate dropdown di frontend.
    """
    try:
        res = (
            supabase.table("dosen")
            .select("kode_dosen, nama_lengkap, program_studi")
            .eq("program_studi", "S1 Teknik Komputer")
            .eq("aktif", True)
            .order("nama_lengkap")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.warning(f"[EVAL] get_all_dosen_teknik_komputer gagal: {e}")
        return []


def check_prerequisites(kode_dosen: str, periode: str) -> dict:
    """
    Cek apakah semua pertemuan dalam periode sudah punya laporan done
    untuk setiap mata kuliah yang diampu dosen.

    Return:
        { "ok": True }
        { "ok": False, "missing": { "kode_matkul": [pertemuan_ke yg kurang] }, "error": "..." }
    """
    if periode not in PERIOD_RANGES:
        return {"ok": False, "missing": {}, "error": f"Periode '{periode}' tidak valid. Pilihan: 1-7, 9-15, 1-15"}

    expected_set = set(PERIOD_RANGES[periode])
    matkul_list  = get_matkul_for_dosen(kode_dosen)

    if not matkul_list:
        return {
            "ok":      False,
            "missing": {},
            "error":   f"Dosen '{kode_dosen}' tidak mengampu mata kuliah apapun semester ini",
        }

    # Ambil semua laporan done dosen ini dari awal semester
    try:
        reports_res = (
            supabase.table("reports")
            .select("id, kode_matkul, tanggal")
            .eq("kode_dosen", kode_dosen)
            .eq("status", "done")
            .gte("tanggal", SEMESTER_START_DATE)
            .execute()
        )
        all_reports = reports_res.data or []
    except Exception as e:
        return {"ok": False, "missing": {}, "error": f"Gagal query reports: {e}"}

    report_ids = [r["id"] for r in all_reports]

    # Ambil pertemuan_ke dari activity_stats
    ptm_by_report: dict[int, int] = {}
    if report_ids:
        try:
            act_res = (
                supabase.table("activity_stats")
                .select("report_id, pertemuan_ke")
                .in_("report_id", report_ids)
                .execute()
            )
            for row in (act_res.data or []):
                ptm_by_report[row["report_id"]] = row["pertemuan_ke"]
        except Exception as e:
            logger.warning(f"[EVAL] activity_stats query gagal: {e}")

    # Fallback: hitung pertemuan_ke dari tanggal kalau activity_stats kosong
    for r in all_reports:
        rid = r["id"]
        if rid not in ptm_by_report and r.get("tanggal"):
            try:
                ptm_by_report[rid] = get_meeting_week(str(r["tanggal"]))
            except Exception:
                pass

    # Kelompokkan pertemuan yang sudah ada per matkul
    done_ptm: dict[str, set[int]] = {}
    for r in all_reports:
        kode = r.get("kode_matkul")
        ptm  = ptm_by_report.get(r["id"])
        if kode and ptm and ptm in expected_set:
            done_ptm.setdefault(kode, set()).add(ptm)

    # Cek yang kurang per matkul
    missing: dict[str, list[int]] = {}
    for m in matkul_list:
        kode = m["kode_matkul"]
        have = done_ptm.get(kode, set())
        lack = sorted(expected_set - have)
        if lack:
            missing[kode] = lack

    return {"ok": len(missing) == 0, "missing": missing}


async def build_eval_data(kode_dosen: str, periode: str) -> dict:
    """
    Bangun seluruh data laporan evaluasi:
      - Agregasi KPI per matkul dari DB
      - LLM: kesimpulan per matkul (paralel)
      - LLM: rekomendasi global

    Return dict lengkap siap dipakai evaluation_service.generate_evaluation_pdf().
    """
    expected_set = set(PERIOD_RANGES[periode])

    # ── 1. Dosen info ─────────────────────────────────────────────────────────
    dosen_info = get_dosen_info(kode_dosen)
    if not dosen_info:
        raise ValueError(f"Data dosen '{kode_dosen}' tidak ditemukan di tabel dosen")

    # ── 2. Matkul yang diampu ─────────────────────────────────────────────────
    matkul_list = get_matkul_for_dosen(kode_dosen)
    if not matkul_list:
        raise ValueError(f"Dosen '{kode_dosen}' tidak mengampu mata kuliah apapun")

    # ── 3. Semua laporan done semester ini ────────────────────────────────────
    reports_res = (
        supabase.table("reports")
        .select("id, kode_matkul, tanggal, kesesuaian_materi, status_waktu, kesesuaian_metode")
        .eq("kode_dosen", kode_dosen)
        .eq("status", "done")
        .gte("tanggal", SEMESTER_START_DATE)
        .execute()
    )
    all_reports = reports_res.data or []
    report_ids  = [r["id"] for r in all_reports]

    logger.info(f"[EVAL] kode_dosen={kode_dosen} periode={periode} total_reports={len(all_reports)}")

    # ── 4. Activity stats ─────────────────────────────────────────────────────
    act_by_report: dict[int, dict] = {}
    if report_ids:
        act_res = (
            supabase.table("activity_stats")
            .select(
                "report_id, pertemuan_ke, dominant_activity, "
                "ceramah_pct, diskusi_pct, tanya_jawab_pct, diam_pct, "
                "ceramah_sec, diskusi_sec, tanya_jawab_sec, diam_sec"
            )
            .in_("report_id", report_ids)
            .execute()
        )
        for row in (act_res.data or []):
            act_by_report[row["report_id"]] = row

    # ── 5. RPS pertemuan (topik) ──────────────────────────────────────────────
    kode_list = [m["kode_matkul"] for m in matkul_list]
    rps_map: dict[tuple, str] = {}
    if kode_list:
        rps_res = (
            supabase.table("rps_pertemuan")
            .select("kode_matkul, pertemuan_ke, materi_pembelajaran")
            .in_("kode_matkul", kode_list)
            .execute()
        )
        for row in (rps_res.data or []):
            rps_map[(row["kode_matkul"], row["pertemuan_ke"])] = row.get("materi_pembelajaran", "-")

    # ── 6. Lookup pertemuan_ke per report ─────────────────────────────────────
    ptm_by_report: dict[int, int] = {
        rid: act["pertemuan_ke"] for rid, act in act_by_report.items()
    }
    # Fallback dari tanggal
    for r in all_reports:
        rid = r["id"]
        if rid not in ptm_by_report and r.get("tanggal"):
            try:
                ptm_by_report[rid] = get_meeting_week(str(r["tanggal"]))
            except Exception:
                pass

    # ── 7. Build detail per matkul ────────────────────────────────────────────
    detail_matkul: list[dict] = []

    for m in matkul_list:
        kode = m["kode_matkul"]
        nama = m["nama_matkul"]

        # Filter laporan matkul ini dalam periode
        matkul_reports = sorted(
            [r for r in all_reports
             if r.get("kode_matkul") == kode
             and ptm_by_report.get(r["id"]) in expected_set],
            key=lambda r: ptm_by_report.get(r["id"], 0)
        )

        pertemuan_data: list[dict] = []
        for r in matkul_reports:
            rid = r["id"]
            ptm = ptm_by_report.get(rid, 0)
            act = act_by_report.get(rid, {})

            total_sec = sum(int(act.get(k) or 0) for k in (
                "ceramah_sec", "diskusi_sec", "tanya_jawab_sec", "diam_sec"
            ))

            pertemuan_data.append({
                "pertemuan_ke":      ptm,
                "topik":             rps_map.get((kode, ptm), "-"),
                "kesesuaian_materi": r.get("kesesuaian_materi") or "-",
                "durasi_menit":      round(total_sec / 60),
                "status_waktu":      r.get("status_waktu")      or "-",
                "aktivitas_str":     _format_aktivitas_str(act),
                "kesesuaian_metode": r.get("kesesuaian_metode") or "-",
            })

        # KPI per matkul
        total_ptm  = len(pertemuan_data)
        n_tepat    = sum(1 for p in pertemuan_data if "TEPAT" in (p["status_waktu"] or "").upper())
        pct_tepat  = round(n_tepat / total_ptm * 100, 1) if total_ptm else 0.0
        kes_rps    = _majority_kesesuaian([p["kesesuaian_materi"] for p in pertemuan_data])
        metode_dom = _mode_activity([
            act_by_report.get(r["id"], {}).get("dominant_activity", "-")
            for r in matkul_reports
        ])

        detail_matkul.append({
            "kode_matkul":     kode,
            "nama_matkul":     nama,
            "total_pertemuan": total_ptm,
            "pct_tepat_waktu": pct_tepat,
            "kesesuaian_rps":  kes_rps,
            "metode_dominan":  metode_dom,
            "pertemuan":       pertemuan_data,
            "kesimpulan":      "",   # diisi LLM di bawah
        })

    # ── 8. Ringkasan keseluruhan ──────────────────────────────────────────────
    total_matkul    = len(detail_matkul)
    total_pertemuan = sum(m["total_pertemuan"] for m in detail_matkul)
    status_kinerja  = _status_kinerja(detail_matkul)

    eval_data = {
        "dosen":          dosen_info,
        "periode":        periode,
        "periode_label":  PERIOD_LABELS.get(periode, periode),
        "semester_label": _derive_semester_label(),
        "ringkasan": {
            "total_matkul":    total_matkul,
            "total_pertemuan": total_pertemuan,
            "status_kinerja":  status_kinerja,
            "per_matkul": [
                {
                    "kode_matkul":     m["kode_matkul"],
                    "nama_matkul":     m["nama_matkul"],
                    "total_pertemuan": m["total_pertemuan"],
                    "pct_tepat_waktu": m["pct_tepat_waktu"],
                    "kesesuaian_rps":  m["kesesuaian_rps"],
                    "metode_dominan":  m["metode_dominan"],
                }
                for m in detail_matkul
            ],
        },
        "detail_matkul": detail_matkul,
        "rekomendasi":   "",   # diisi LLM di bawah
    }

    # ── 9. LLM: kesimpulan per matkul (paralel, dibatasi semaphore) ──────────────
    logger.info(f"[EVAL] LLM kesimpulan {len(detail_matkul)} matkul (paralel, max 2)...")

    async def _limited_kesimpulan(matkul_data: dict) -> str:
        async with _LLM_SEMAPHORE:
            return await asyncio.to_thread(_generate_kesimpulan_matkul, matkul_data)

    kes_results = await asyncio.gather(*[
        _limited_kesimpulan(m) for m in detail_matkul
    ])
    for m, kes in zip(detail_matkul, kes_results):
        m["kesimpulan"] = kes

    # ── 10. LLM: rekomendasi global ───────────────────────────────────────────
    logger.info(f"[EVAL] LLM rekomendasi global...")
    eval_data["rekomendasi"] = await asyncio.to_thread(
        _generate_rekomendasi_global, eval_data
    )

    logger.info(
        f"[EVAL] build_eval_data selesai | "
        f"dosen={kode_dosen} matkul={total_matkul} pertemuan={total_pertemuan}"
    )

    return eval_data
