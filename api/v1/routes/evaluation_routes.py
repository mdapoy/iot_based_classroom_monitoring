import asyncio
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from repositories.supabase_client import supabase
from services.report.evaluation_analyzer import (
    check_prerequisites,
    build_eval_data,
    get_all_dosen_teknik_komputer,
    get_matkul_for_dosen,
    PERIOD_RANGES,
    PERIOD_LABELS,
)
from services.report.evaluation_service import generate_evaluation_pdf
from services.storage.storage_service import upload_evaluation, get_public_evaluation_url
from core.logger import logger

router = APIRouter(prefix="/evaluation", tags=["Evaluasi Dosen"])


# ══════════════════════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════════════════════

class GenerateRequest(BaseModel):
    kode_dosen: str
    periode: str   # "1-7" | "9-15" | "1-15"


# ══════════════════════════════════════════════════════════════════════════════
# Background task — orkestrator utama
# ══════════════════════════════════════════════════════════════════════════════

async def _run_evaluation(eval_id: int, kode_dosen: str, periode: str) -> None:
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
        logger.info(f"[EVAL ROUTE] Start build_eval_data eval_id={eval_id}")
        eval_data = await build_eval_data(kode_dosen, periode)

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
        "status":    "success",
        "can_generate": result["ok"],
        "missing":   result.get("missing", {}),
        "message": (
            "Semua pertemuan sudah siap. Laporan evaluasi dapat dibuat."
            if result["ok"]
            else "Beberapa pertemuan belum memiliki laporan identifikasi yang selesai."
        ),
    }


@router.post("/generate")
async def generate_evaluation(req: GenerateRequest):
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

    if not prereq["ok"]:
        missing_info = {
            kode: f"Pertemuan {missing}"
            for kode, missing in prereq["missing"].items()
        }
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Tidak semua pertemuan memiliki laporan identifikasi yang selesai. "
                    "Harap generate ulang laporan yang kurang terlebih dahulu."
                ),
                "missing": missing_info,
            },
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
    asyncio.create_task(_run_evaluation(eval_id, req.kode_dosen, req.periode))

    logger.info(
        f"[EVAL ROUTE] Task started eval_id={eval_id} "
        f"kode_dosen={req.kode_dosen} periode={req.periode}"
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
