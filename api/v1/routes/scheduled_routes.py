from fastapi import APIRouter, Depends
from repositories.cache import get_all_jadwal
from api.v1.deps import optional_authenticated

router = APIRouter(prefix="/scheduled", tags=["scheduled"])

@router.get("/jadwal")
def get_jadwal(user: dict = Depends(optional_authenticated)):
    return get_all_jadwal()