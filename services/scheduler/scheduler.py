from repositories.cache import get_all_jadwal


def find_jadwal(kode_matkul, kode_dosen, kelas):
    """
    Cari jadwal berdasarkan kode_matkul, kode_dosen, dan kelas.
    Menggunakan in-memory cache (TTL 10 menit) — tidak query DB langsung.
    """
    jadwal = get_all_jadwal()
    return next(
        (
            j for j in jadwal
            if j.get("kode_mata_kuliah") == kode_matkul
            and j.get("dosen_utama") == kode_dosen
            and j.get("kelas") == kelas
        ),
        None,
    )