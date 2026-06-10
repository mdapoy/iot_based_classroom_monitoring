import asyncio
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from repositories.supabase_client import supabase
from services.report.evaluation_analyzer import (
    check_prerequisites,
    build_eval_data,
    get_all_dosen_teknik_komputer,
    get_matkul_for_dosen,
    get_matkul_info,
    PERIOD_RANGES,
    PERIOD_LABELS,
)
from services.report.evaluation_service import generate_evaluation_pdf
from services.storage.storage_service import upload_evaluation, get_public_evaluation_url
from core.limiter import limiter
from core.logger import logger

router = APIRouter(prefix="/evaluation", tags=["Evaluasi Dosen"])

# ══════════════════════════════════════════════════════════════════════════════
# Konstanta & helper untuk tabel ringkasan kelas + performa dosen
# ══════════════════════════════════════════════════════════════════════════════

# Pertemuan yang tidak dihitung sebagai sesi mengajar (UTS ke-7, UAS ke-16)
EXCLUDED_PERTEMUAN = {7, 16}

# Bobot komponen % Performa Dosen (total 1.0)
PERFORMA_WEIGHTS = {
    "materi":    0.5,
    "waktu":     0.3,
    "kehadiran": 0.2,
}


def _materi_weight(nilai):
    """Konversi kesesuaian_materi → skor 0..100. Menerima float (baru) atau teks (lama)."""
    if nilai is None:
        return None
    try:
        return float(nilai)   # kesesuaian_pct langsung sebagai skor
    except (TypeError, ValueError):
        pass
    v = str(nilai).upper().strip()
    if "TIDAK SESUAI" in v:
        return 0.0
    if "SEBAGIAN" in v:
        return 50.0
    if "SESUAI" in v:
        return 100.0
    return None


def _parse_rps_minutes_eval(pengalaman: Optional[str]):
    """Durasi harapan per pertemuan (menit) dari pengalaman_pembelajaran_mahasiswa."""
    if not pengalaman:
        return None
    m = re.search(r"(\d+)\s*[xX]\s*(\d+)", pengalaman)
    if m:
        return int(m.group(1)) * int(m.group(2))
    m2 = re.search(r"(\d+)\s*menit", pengalaman, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    return None


def _actual_minutes_from_stats(row: dict):
    """Durasi aktual mengajar (menit) dari baris activity_stats."""
    total_sec = sum(
        (row.get(k) or 0)
        for k in ("ceramah_sec", "tanya_jawab_sec", "diskusi_sec", "diam_sec")
    )
    return total_sec / 60.0

# Simpan referensi task agar tidak di-cancel oleh GC diam-diam
_background_tasks: set = set()


# ══════════════════════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════════════════════

class GenerateRequest(BaseModel):
    kode_dosen: str
    periode: str   # "1-7" | "9-15" | "1-15"
    tahun_ajaran_id: Optional[str] = None

    @field_validator("kode_dosen")
    @classmethod
    def validate_kode_dosen(cls, v: str) -> str:
        v = v.strip().upper()
        if not v or len(v) > 10 or not v.isalnum():
            raise ValueError("kode_dosen harus alfanumerik, maksimal 10 karakter")
        return v

    @field_validator("periode")
    @classmethod
    def validate_periode(cls, v: str) -> str:
        allowed = {"1-7", "9-15", "1-15"}
        if v not in allowed:
            raise ValueError(f"periode harus salah satu dari: {allowed}")
        return v


# ══════════════════════════════════════════════════════════════════════════════
# Background task — orkestrator utama
# ══════════════════════════════════════════════════════════════════════════════

async def _run_evaluation(
    eval_id: int,
    kode_dosen: str,
    periode: str,
    tahun_ajaran_id: Optional[str] = None,
) -> None:
    """
    Background async task:
      1. Agregasi data + LLM (build_eval_data)
      2. Generate PDF
      3. Upload ke Supabase Storage
      4. Update evaluation_reports → done
    """
    tmp_path = f"temp/eval_{eval_id}.pdf"

    try:
        # ── Tandai processing ─────────────────────────────────────────────────
        supabase.table("evaluation_reports").update({
            "status": "processing"
        }).eq("id", eval_id).execute()

        # ── Build data (DB queries + LLM calls) ───────────────────────────────
        logger.info(
            f"[EVAL ROUTE] Start build_eval_data eval_id={eval_id} "
            f"tahun_ajaran_id={tahun_ajaran_id}"
        )
        eval_data = await build_eval_data(kode_dosen, periode, tahun_ajaran_id=tahun_ajaran_id)

        # ── Generate PDF ──────────────────────────────────────────────────────
        os.makedirs("temp", exist_ok=True)
        generate_evaluation_pdf(eval_data, tmp_path)

        # ── Upload ke bucket "evaluasi" ───────────────────────────────────────
        safe_periode = periode.replace("-", "_")
        storage_path = f"eval_{kode_dosen}_{safe_periode}_{eval_id}.pdf"
        upload_evaluation(tmp_path, storage_path)

        # ── Update DB → done ──────────────────────────────────────────────────
        supabase.table("evaluation_reports").update({
            "status":        "done",
            "pdf_path":      storage_path,
            "error_message": None,
        }).eq("id", eval_id).execute()

        logger.info(
            f"[EVAL ROUTE] Done eval_id={eval_id} "
            f"kode_dosen={kode_dosen} periode={periode} "
            f"pdf={storage_path}"
        )

    except Exception as e:
        logger.error(f"[EVAL ROUTE] Error eval_id={eval_id}: {e}", exc_info=True)
        supabase.table("evaluation_reports").update({
            "status":        "failed",
            "error_message": str(e),
        }).eq("id", eval_id).execute()

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dosen")
def get_dosen_list():
    """
    Daftar semua dosen S1 Teknik Komputer.
    Digunakan untuk populate dropdown di frontend.
    """
    dosen_list = get_all_dosen_teknik_komputer()
    return {
        "status": "success",
        "data":   dosen_list,
        "total":  len(dosen_list),
    }


@router.get("/matkul-info")
def get_matkul_info_endpoint(
    kode_dosen: str = Query(..., description="Kode dosen, contoh: MFC"),
):
    """
    Daftar mata kuliah yang diampu dosen + ringkasan pertemuan tersedia semester ini.
    Digunakan FE untuk menampilkan overview sebelum user memilih periode & generate.
    """
    data = get_matkul_info(kode_dosen)
    return {
        "status": "success",
        "data":   data,
        "total":  len(data),
    }


@router.get("/periode-options")
def get_periode_options():
    """Daftar opsi periode yang tersedia."""
    return {
        "status": "success",
        "data": [
            {"value": k, "label": v}
            for k, v in PERIOD_LABELS.items()
        ],
    }


@router.get("/prerequisite-check")
def prerequisite_check(
    kode_dosen: str = Query(..., description="Kode dosen, contoh: MFC"),
    periode:    str = Query(..., description="Periode: 1-7 | 9-15 | 1-15"),
):
    """
    Cek apakah semua pertemuan dalam periode sudah punya laporan done.
    Gunakan ini sebelum tombol 'Generate' di-click untuk memberi feedback ke user.
    """
    if periode not in PERIOD_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Periode '{periode}' tidak valid. Pilihan: 1-7, 9-15, 1-15"
        )

    result = check_prerequisites(kode_dosen, periode)

    if result.get("error"):
        return {"status": "error", "message": result["error"]}

    return {
        "status":       "success",
        "can_generate": result["ok"],
        "can_partial":  result.get("can_partial", False),
        "missing":      result.get("missing", {}),
        "available":    result.get("available", {}),
        "message": (
            "Semua pertemuan sudah siap. Laporan evaluasi dapat dibuat."
            if result["ok"]
            else "Beberapa pertemuan belum memiliki laporan identifikasi yang selesai."
        ),
    }


@router.post("/generate")
@limiter.limit("5/minute")   # max 5 generate per IP per menit (endpoint berat — panggil LLM)
async def generate_evaluation(request: Request, req: GenerateRequest):
    """
    Trigger generate laporan evaluasi dosen.

    Flow:
      1. Validasi periode
      2. Cek prerequisites (semua laporan identifikasi harus done)
      3. Cegah duplikat (pending/processing yang sama)
      4. Insert record evaluation_reports → pending
      5. Start background task
    """
    # ── 1. Validasi periode ────────────────────────────────────────────────────
    if req.periode not in PERIOD_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Periode '{req.periode}' tidak valid. Pilihan: 1-7, 9-15, 1-15"
        )

    # ── 2. Cek prerequisites ───────────────────────────────────────────────────
    prereq = check_prerequisites(req.kode_dosen, req.periode)

    if prereq.get("error"):
        raise HTTPException(status_code=422, detail=prereq["error"])

    # Block hanya kalau tidak ada pertemuan sama sekali yang tersedia
    if not prereq.get("can_partial", False):
        missing_info = {
            kode: f"Pertemuan {ptms}"
            for kode, ptms in prereq.get("missing", {}).items()
        }
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Belum ada pertemuan yang memiliki laporan identifikasi selesai "
                    "untuk periode ini. Harap generate laporan identifikasi terlebih dahulu."
                ),
                "missing": missing_info,
            },
        )

    # Jika sebagian tersedia (parsial) — lanjut generate dengan data yang ada
    if not prereq["ok"]:
        logger.warning(
            f"[EVAL ROUTE] Partial generate: dosen={req.kode_dosen} periode={req.periode} "
            f"available={prereq.get('available')} missing={prereq.get('missing')}"
        )

    # ── 3. Cegah duplikat ─────────────────────────────────────────────────────
    dup_res = (
        supabase.table("evaluation_reports")
        .select("id, status")
        .eq("kode_dosen", req.kode_dosen)
        .eq("periode", req.periode)
        .in_("status", ["pending", "processing"])
        .execute()
    )
    if dup_res.data:
        existing = dup_res.data[0]
        raise HTTPException(
            status_code=409,
            detail=(
                f"Laporan evaluasi untuk {req.kode_dosen} periode {req.periode} "
                f"sedang dalam proses (id={existing['id']}, status={existing['status']}). "
                f"Tunggu hingga selesai sebelum generate ulang."
            ),
        )

    # ── 4. Insert record evaluation_reports ───────────────────────────────────
    ins_res = (
        supabase.table("evaluation_reports")
        .insert({
            "kode_dosen": req.kode_dosen,
            "periode":    req.periode,
            "status":     "pending",
        })
        .execute()
    )
    if not ins_res.data:
        raise HTTPException(status_code=500, detail="Gagal membuat record evaluasi")

    eval_id = ins_res.data[0]["id"]

    # ── 5. Start background task ───────────────────────────────────────────────
    task = asyncio.create_task(
        _run_evaluation(eval_id, req.kode_dosen, req.periode, req.tahun_ajaran_id)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info(
        f"[EVAL ROUTE] Task started eval_id={eval_id} "
        f"kode_dosen={req.kode_dosen} periode={req.periode} "
        f"tahun_ajaran_id={req.tahun_ajaran_id}"
    )

    return {
        "status":   "success",
        "eval_id":  eval_id,
        "message":  (
            f"Proses generate laporan evaluasi dimulai. "
            f"Gunakan GET /evaluation/status/{eval_id} untuk memantau progress."
        ),
    }


@router.get("/status/{eval_id}")
def get_evaluation_status(eval_id: int):
    """Cek status proses generate laporan evaluasi."""
    res = (
        supabase.table("evaluation_reports")
        .select("id, kode_dosen, periode, status, pdf_path, error_message, created_at")
        .eq("id", eval_id)
        .single()
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=404, detail=f"Evaluasi id={eval_id} tidak ditemukan")

    row = res.data
    result = {
        "status":         "success",
        "eval_id":        row["id"],
        "kode_dosen":     row["kode_dosen"],
        "periode":        row["periode"],
        "process_status": row["status"],
        "created_at":     row.get("created_at"),
        "error_message":  row.get("error_message"),
        "pdf_url":        None,
    }

    # Generate public URL kalau sudah done
    if row.get("pdf_path") and row["status"] == "done":
        result["pdf_url"] = (
            get_public_evaluation_url(row["pdf_path"])
        )

    return result


@router.get("/list")
def list_evaluations(
    kode_dosen: Optional[str] = Query(None, description="Filter by kode_dosen"),
    periode:    Optional[str] = Query(None, description="Filter by periode"),
    limit:      int           = Query(20, ge=1, le=100),
):
    """
    Daftar laporan evaluasi yang pernah dibuat.
    Filter opsional: kode_dosen, periode.
    """
    q = (
        supabase.table("evaluation_reports")
        .select("id, kode_dosen, periode, status, pdf_path, error_message, created_at")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if kode_dosen:
        q = q.eq("kode_dosen", kode_dosen)
    if periode:
        q = q.eq("periode", periode)

    res = q.execute()
    rows = res.data or []

    # Enrich dengan public URL untuk yang sudah done
    for row in rows:
        if row.get("pdf_path") and row.get("status") == "done":
            row["pdf_url"] = (
                get_public_evaluation_url(row["pdf_path"])
            )
        else:
            row["pdf_url"] = None

    return {
        "status": "success",
        "data":   rows,
        "total":  len(rows),
    }


# ══════════════════════════════════════════════════════════════════════════════
# TABEL KELAS — ringkasan ketepatan materi & ketepatan waktu mengajar
# Dikelompokkan per (kode_matkul, kelas, kode_dosen)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/kelas-summary")
def get_kelas_summary(
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
):
    # ── laporan yang sudah selesai ───────────────────────────────────────────
    reports = (
        supabase.table("reports")
        .select("id, kode_matkul, kelas, kode_dosen, kesesuaian_materi, kesesuaian_pct")
        .eq("status", "done")
        .execute()
        .data or []
    )

    # ── activity_stats: pertemuan_ke + durasi aktual, di-index per report_id ─
    report_ids = [r["id"] for r in reports]
    stats_by_report: dict = {}
    if report_ids:
        for s in (
            supabase.table("activity_stats")
            .select("report_id, pertemuan_ke, ceramah_sec, tanya_jawab_sec, diskusi_sec, diam_sec")
            .in_("report_id", report_ids)
            .execute()
            .data or []
        ):
            rid = s.get("report_id")
            if rid is not None and rid not in stats_by_report:
                stats_by_report[rid] = s

    # ── durasi harapan RPS per kode_matkul ──────────────────────────────────
    rps_minutes: dict = {}
    for r in (
        supabase.table("rps_pertemuan")
        .select("kode_matkul, pengalaman_pembelajaran_mahasiswa")
        .execute()
        .data or []
    ):
        kode = r.get("kode_matkul")
        if kode and kode not in rps_minutes:
            mnt = _parse_rps_minutes_eval(r.get("pengalaman_pembelajaran_mahasiswa"))
            if mnt:
                rps_minutes[kode] = mnt

    # ── nama dosen ───────────────────────────────────────────────────────────
    nama_dosen_map = {
        d["kode_dosen"]: (d.get("nama_lengkap") or "").strip()
        for d in (
            supabase.table("dosen")
            .select("kode_dosen, nama_lengkap")
            .execute()
            .data or []
        )
        if d.get("kode_dosen")
    }

    # ── nama matkul + filter Tahun Ajaran dari jadwal_kuliah ────────────────
    jadwal = (
        supabase.table("jadwal_kuliah")
        .select("kode_mata_kuliah, mata_kuliah, tahun_ajaran_id")
        .execute()
        .data or []
    )
    nama_matkul_map: dict = {}
    allowed_kode: set = set()
    for j in jadwal:
        kode = j.get("kode_mata_kuliah")
        if not kode:
            continue
        nama_matkul_map.setdefault(kode, j.get("mata_kuliah") or kode)
        if tahun_ajaran_id and j.get("tahun_ajaran_id") == tahun_ajaran_id:
            allowed_kode.add(kode)

    # ── agregasi per (kode_matkul, kelas, kode_dosen) ───────────────────────
    groups: dict = {}
    for r in reports:
        kode  = r.get("kode_matkul")
        kelas = r.get("kelas")
        dosen = r.get("kode_dosen")

        if tahun_ajaran_id and kode not in allowed_kode:
            continue

        key = (kode, kelas, dosen)
        g = groups.setdefault(key, {
            "kode_matkul":   kode,
            "kelas":         kelas,
            "kode_dosen":    dosen,
            "total_done":    0,
            "materi_scores": [],
            "aktual_total":  0.0,
            "n_pertemuan":   0,
        })
        g["total_done"] += 1

        mw = _materi_weight(r.get("kesesuaian_pct") if r.get("kesesuaian_pct") is not None else r.get("kesesuaian_materi"))
        if mw is not None:
            g["materi_scores"].append(mw)

        stats = stats_by_report.get(r["id"])
        if stats:
            ptm = stats.get("pertemuan_ke")
            if ptm is not None and ptm not in EXCLUDED_PERTEMUAN:
                g["aktual_total"] += _actual_minutes_from_stats(stats)
                g["n_pertemuan"]  += 1

    # ── susun output ─────────────────────────────────────────────────────────
    out = []
    for g in groups.values():
        materi = g["materi_scores"]
        ketepatan_materi = round(sum(materi) / len(materi), 1) if materi else None

        expected_per = rps_minutes.get(g["kode_matkul"])
        n = g["n_pertemuan"]
        harapan_total = expected_per * n if (expected_per and n) else 0
        pct_waktu = (
            round(min(g["aktual_total"] / harapan_total * 100, 100.0), 1)
            if harapan_total > 0 else None
        )

        out.append({
            "kode_matkul":                    g["kode_matkul"],
            "nama_matkul":                    nama_matkul_map.get(g["kode_matkul"], g["kode_matkul"]),
            "kelas":                          g["kelas"],
            "kode_dosen":                     g["kode_dosen"],
            "nama_dosen":                     nama_dosen_map.get(g["kode_dosen"], g["kode_dosen"]),
            "ketepatan_materi":               ketepatan_materi,
            "ketepatan_waktu_mengajar":       pct_waktu,
            "total_pertemuan":                g["total_done"],
            "dinilai_materi":                 len(materi),
            "pertemuan_dihitung":             n,
            "durasi_aktual_total":            round(g["aktual_total"]),
            "durasi_harapan_total":           harapan_total,
            "durasi_harapan_per_pertemuan":   expected_per,
        })

    out.sort(key=lambda x: (str(x["kode_matkul"] or ""), str(x["kelas"] or "")))
    logger.info(f"[EVAL] kelas-summary: {len(out)} grup (ta={tahun_ajaran_id})")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# TABEL DOSEN — % Performa
# Performa = bobot-rata: Materi 50% · Waktu 30% · Kehadiran 20%
# Komponen yang tidak ada → bobot dinormalisasi ulang
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/dosen-performa")
def get_dosen_performa(
    tahun_ajaran_id: Optional[str] = Query(None, description="Filter berdasarkan tahun ajaran"),
):
    # ── reports done ─────────────────────────────────────────────────────────
    reports = (
        supabase.table("reports")
        .select("id, kode_matkul, kode_dosen, kesesuaian_materi, kesesuaian_pct")
        .eq("status", "done")
        .execute()
        .data or []
    )

    # ── activity_stats per report ────────────────────────────────────────────
    report_ids = [r["id"] for r in reports]
    stats_by_report: dict = {}
    if report_ids:
        for s in (
            supabase.table("activity_stats")
            .select("report_id, pertemuan_ke, ceramah_sec, tanya_jawab_sec, diskusi_sec, diam_sec")
            .in_("report_id", report_ids)
            .execute()
            .data or []
        ):
            rid = s.get("report_id")
            if rid is not None and rid not in stats_by_report:
                stats_by_report[rid] = s

    # ── durasi harapan RPS per kode_matkul ──────────────────────────────────
    rps_minutes: dict = {}
    for r in (
        supabase.table("rps_pertemuan")
        .select("kode_matkul, pengalaman_pembelajaran_mahasiswa")
        .execute()
        .data or []
    ):
        kode = r.get("kode_matkul")
        if kode and kode not in rps_minutes:
            mnt = _parse_rps_minutes_eval(r.get("pengalaman_pembelajaran_mahasiswa"))
            if mnt:
                rps_minutes[kode] = mnt

    # ── dosen: id→kode, kode→nama ────────────────────────────────────────────
    dosen_rows = (
        supabase.table("dosen")
        .select("id, kode_dosen, nama_lengkap")
        .execute()
        .data or []
    )
    kode_by_dosen_id = {d["id"]: d.get("kode_dosen") for d in dosen_rows if d.get("id") is not None}
    nama_dosen_map   = {
        d["kode_dosen"]: (d.get("nama_lengkap") or "").strip()
        for d in dosen_rows if d.get("kode_dosen")
    }

    # ── filter Tahun Ajaran ──────────────────────────────────────────────────
    allowed_kode: set   = set()
    allowed_jadwal: set = set()
    if tahun_ajaran_id:
        for j in (
            supabase.table("jadwal_kuliah")
            .select("id, kode_mata_kuliah, tahun_ajaran_id")
            .eq("tahun_ajaran_id", tahun_ajaran_id)
            .execute()
            .data or []
        ):
            if j.get("kode_mata_kuliah"):
                allowed_kode.add(j["kode_mata_kuliah"])
            if j.get("id") is not None:
                allowed_jadwal.add(j["id"])

    # ── agregasi per kode_dosen ──────────────────────────────────────────────
    agg: dict = {}

    def _slot(kode_dosen):
        return agg.setdefault(kode_dosen, {
            "materi_scores": [],
            "aktual_total":  0.0,
            "harapan_total": 0.0,
            "hadir_tepat":   0,
            "hadir_total":   0,
        })

    for r in reports:
        kode_dosen  = r.get("kode_dosen")
        kode_matkul = r.get("kode_matkul")
        if not kode_dosen:
            continue
        if tahun_ajaran_id and kode_matkul not in allowed_kode:
            continue

        g = _slot(kode_dosen)

        mw = _materi_weight(r.get("kesesuaian_pct") if r.get("kesesuaian_pct") is not None else r.get("kesesuaian_materi"))
        if mw is not None:
            g["materi_scores"].append(mw)

        stats = stats_by_report.get(r["id"])
        expected_per = rps_minutes.get(kode_matkul)
        if stats and expected_per:
            ptm = stats.get("pertemuan_ke")
            if ptm is not None and ptm not in EXCLUDED_PERTEMUAN:
                g["aktual_total"]  += _actual_minutes_from_stats(stats)
                g["harapan_total"] += expected_per

    # ── kehadiran dari rec_session ───────────────────────────────────────────
    for s in (
        supabase.table("rec_session")
        .select("dosen_id, jadwal_id, kehadiran")
        .execute()
        .data or []
    ):
        if tahun_ajaran_id and s.get("jadwal_id") not in allowed_jadwal:
            continue
        kehadiran = s.get("kehadiran")
        if not kehadiran:
            continue
        kode_dosen = kode_by_dosen_id.get(s.get("dosen_id"))
        if not kode_dosen:
            continue
        g = _slot(kode_dosen)
        g["hadir_total"] += 1
        if kehadiran == "tepat_waktu":
            g["hadir_tepat"] += 1

    # ── susun output + hitung performa ──────────────────────────────────────
    out = []
    for kode_dosen, g in agg.items():
        materi = g["materi_scores"]
        materi_pct = round(sum(materi) / len(materi), 1) if materi else None

        waktu_pct = (
            round(min(g["aktual_total"] / g["harapan_total"] * 100, 100.0), 1)
            if g["harapan_total"] > 0 else None
        )

        kehadiran_pct = (
            round(g["hadir_tepat"] / g["hadir_total"] * 100, 1)
            if g["hadir_total"] > 0 else None
        )

        # Bobot-rata dengan normalisasi ulang untuk komponen yang ada
        comp = [
            (PERFORMA_WEIGHTS["materi"],    materi_pct),
            (PERFORMA_WEIGHTS["waktu"],     waktu_pct),
            (PERFORMA_WEIGHTS["kehadiran"], kehadiran_pct),
        ]
        avail = [(w, v) for w, v in comp if v is not None]
        if avail:
            total_w = sum(w for w, _ in avail)
            performa = round(sum(w * v for w, v in avail) / total_w, 1)
        else:
            performa = None

        out.append({
            "kode_dosen":               kode_dosen,
            "nama_dosen":               nama_dosen_map.get(kode_dosen, kode_dosen),
            "performa":                 performa,
            "ketepatan_materi":         materi_pct,
            "ketepatan_waktu_mengajar": waktu_pct,
            "kehadiran":                kehadiran_pct,
        })

    out.sort(key=lambda x: (x["performa"] is None, -(x["performa"] or 0)))
    logger.info(f"[EVAL] dosen-performa: {len(out)} dosen (ta={tahun_ajaran_id})")
    return out
