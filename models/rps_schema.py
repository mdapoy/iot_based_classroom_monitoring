from pydantic import BaseModel
from typing import Optional


class RPSRequest(BaseModel):
    kodeMatkul: str
    pertemuan: int
    materi: str
    pengalaman: str
    tahun_ajaran_id: Optional[str] = None
