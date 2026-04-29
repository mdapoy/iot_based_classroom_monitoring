from fastapi import APIRouter
from repositories.supabase_client import supabase

router = APIRouter(prefix="/scheduled", tags=["scheduled"])
@router.get("/jadwal")
def get_jadwal():
    response = supabase.table("jadwal_kuliah").select("*").execute()

    return response.data