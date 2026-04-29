from pydantic import BaseModel

class ReportRequest(BaseModel):
    tanggal: str
    jam: str
    ruangan: str
    matkul: str
    dosen: str
    kelas: str