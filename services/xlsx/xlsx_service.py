import time
import pandas as pd
from repositories.supabase_client import supabase
from repositories.cache import invalidate_jadwal_cache
from core.logger import logger
from typing import Optional

REQUIRED_COLUMNS = {"hari", "kode_mata_kuliah", "nama_mata_kuliah", "dosen", "kelas", "shift"}
BATCH_SIZE       = 500  # baris per request ke Supabase


def process_xlsx(file, tahun_ajaran_id: Optional[str] = None):

    # ── STEP 1: Parse Excel ───────────────────────────────────────────────────
    logger.info(f"[XLSX] [1/4] Membaca Excel...")
    t1 = time.time()

    df = pd.read_excel(file, engine="openpyxl")
    df.columns = df.columns.str.lower().str.replace(" ", "_")

    logger.info(f"[XLSX] [1/4] SELESAI | total_baris={len(df)} | waktu={time.time() - t1:.2f}s")

    # ── STEP 2: Validasi kolom ────────────────────────────────────────────────
    logger.info(f"[XLSX] [2/4] Validasi kolom...")
    t2 = time.time()

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Kolom tidak ditemukan dalam file: {', '.join(sorted(missing))}"
        )

    # Rename kolom Excel → nama kolom DB
    df = df.rename(columns={
        "nama_mata_kuliah": "mata_kuliah",
        "dosen":            "dosen_utama",
    })

    # Drop kolom yang tidak ada di DB
    df = df.drop(columns=["jenis"], errors="ignore")

    # Split kolom shift → jam_mulai, jam_selesai
    df[["jam_mulai", "jam_selesai"]] = df["shift"].str.split(" - ", expand=True)
    df = df.drop(columns=["shift"])
    df = df.fillna("")

    data = df.to_dict(orient="records")

    logger.info(f"[XLSX] [2/4] SELESAI | kolom OK | waktu={time.time() - t2:.2f}s")

    # ── STEP 3: Cek duplikat ──────────────────────────────────────────────────
    logger.info(f"[XLSX] [3/4] Cek duplikat ke DB...")
    t3 = time.time()

    existing_rows = (
        supabase.table("jadwal_kuliah")
        .select("hari, kode_mata_kuliah, dosen_utama, kelas, jam_mulai")
        .execute()
        .data
        or []
    )

    existing_keys = {
        (
            r.get("hari"),
            r.get("kode_mata_kuliah"),
            r.get("dosen_utama"),
            r.get("kelas"),
            r.get("jam_mulai"),
        )
        for r in existing_rows
    }

    new_data = [
        row for row in data
        if (
            row.get("hari"),
            row.get("kode_mata_kuliah"),
            row.get("dosen_utama"),
            row.get("kelas"),
            row.get("jam_mulai"),
        ) not in existing_keys
    ]

    logger.info(
        f"[XLSX] [3/4] SELESAI | "
        f"existing={len(existing_rows)} | "
        f"new={len(new_data)} | "
        f"skipped={len(data) - len(new_data)} | "
        f"waktu={time.time() - t3:.2f}s"
    )

    # ── STEP 4: Batch insert ke DB ───────────────────────────────────────────
    if new_data:
        if tahun_ajaran_id:
            for row in new_data:
                row["tahun_ajaran_id"] = tahun_ajaran_id

        batches      = [new_data[i:i + BATCH_SIZE] for i in range(0, len(new_data), BATCH_SIZE)]
        total_batch  = len(batches)
        total_inserted = 0

        logger.info(
            f"[XLSX] [4/4] Insert {len(new_data)} baris "
            f"dalam {total_batch} batch ({BATCH_SIZE}/batch)..."
        )
        t4 = time.time()

        for idx, batch in enumerate(batches, start=1):
            t_batch = time.time()
            supabase.table("jadwal_kuliah").insert(batch).execute()
            total_inserted += len(batch)
            logger.info(
                f"[XLSX] [4/4] Batch {idx:>3}/{total_batch} selesai | "
                f"rows={len(batch)} | waktu={time.time() - t_batch:.2f}s"
            )

        invalidate_jadwal_cache()
        logger.info(
            f"[XLSX] [4/4] SELESAI | "
            f"total_inserted={total_inserted} | waktu={time.time() - t4:.2f}s"
        )
    else:
        logger.info(f"[XLSX] [4/4] Tidak ada data baru — insert dilewati")

    return {
        "status":        "success",
        "rows_inserted": len(new_data),
        "rows_skipped":  len(data) - len(new_data),
    }
