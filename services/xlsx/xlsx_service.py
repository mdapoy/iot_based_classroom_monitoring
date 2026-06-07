import pandas as pd
from repositories.supabase_client import supabase
from repositories.cache import invalidate_jadwal_cache
from typing import Optional

REQUIRED_COLUMNS = {"hari", "kode_mata_kuliah", "nama_mata_kuliah", "dosen", "kelas", "shift"}


def process_xlsx(file, tahun_ajaran_id: Optional[str] = None):

    df = pd.read_excel(file, engine="openpyxl")

    # Normalize nama kolom: lowercase + spasi → underscore
    df.columns = df.columns.str.lower().str.replace(" ", "_")

    # Validasi kolom wajib
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

    # Cek duplikat sebelum INSERT
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

    if new_data:
        if tahun_ajaran_id:
            for row in new_data:
                row["tahun_ajaran_id"] = tahun_ajaran_id
        supabase.table("jadwal_kuliah").insert(new_data).execute()
        invalidate_jadwal_cache()   # data jadwal berubah — paksa refresh cache

    return {
        "status":        "success",
        "rows_inserted": len(new_data),
        "rows_skipped":  len(data) - len(new_data),
    }
