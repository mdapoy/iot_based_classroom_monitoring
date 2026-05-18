from pydantic import BaseModel


class RPSRequest(BaseModel):
    kodeMatkul: str
    pertemuan: int
    materi: str
    pengalaman: str
