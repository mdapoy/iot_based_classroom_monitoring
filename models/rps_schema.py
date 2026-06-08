from pydantic import BaseModel
from typing import Optional, List


class RPSRequest(BaseModel):
    kodeMatkul: str
    pertemuan: int
    materi: str
    pengalaman: str
    tahun_ajaran_id: Optional[str] = None


class RPSRowItem(BaseModel):
    pertemuan_ke: int
    materi_pembelajaran: str
    pengalaman_pembelajaran_mahasiswa: str


class RPSConfirmRequest(BaseModel):
    """
    Body untuk POST /rps/confirm-insert.
    Berisi hasil ekstraksi PDF yang sudah diverifikasi user,
    siap di-upsert ke tabel rps_pertemuan.
    """
    kode_matkul:     str
    tahun_ajaran_id: Optional[str] = None   # jika None, backend auto-cari is_aktif
    rows:            List[RPSRowItem]
