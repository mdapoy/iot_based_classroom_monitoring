from repositories.supabase_client import supabase


def find_jadwal(kode_matkul, kode_dosen, kelas):

    res = (
        supabase
        .table("jadwal_kuliah")
        .select("*")
        .eq("kode_mata_kuliah", kode_matkul)
        .eq("dosen_utama", kode_dosen)
        .eq("kelas", kelas)
        .execute()
    )

    if len(res.data) == 0:
        return None

    return res.data[0]