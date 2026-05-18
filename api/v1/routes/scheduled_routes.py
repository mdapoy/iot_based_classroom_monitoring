from fastapi import APIRouter, Depends
from repositories.supabase_client import supabase
from api.v1.deps import optional_authenticated

router = APIRouter(prefix="/scheduled", tags=["scheduled"])

@router.get("/jadwal")
def get_jadwal(user: dict = Depends(optional_authenticated)):
    response = supabase.table("jadwal_kuliah").select("*").execute()

    return response.data