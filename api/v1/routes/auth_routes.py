from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from repositories.supabase_client import supabase

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(data: LoginRequest):
    """
    Login dengan email & password Supabase.

    Gunakan `access_token` dari response untuk mengisi field
    **HTTPBearer** di tombol 🔒 Authorize Swagger UI (/docs).

    Paste token-nya saja (tanpa kata 'Bearer').
    """
    try:
        response = supabase.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password
        })

        return {
            "access_token": response.session.access_token,
            "token_type": "bearer",
            "user": {
                "id": str(response.user.id),
                "email": response.user.email
            }
        }

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Email atau password salah"
        )
