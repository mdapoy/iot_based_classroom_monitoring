from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from repositories.supabase_client import supabase
from core.logger import logger

# Strict — wajib ada token (dipakai untuk admin/write ops)
security = HTTPBearer()

# Optional — boleh ada token, boleh tidak (dipakai untuk FE read/report ops)
security_optional = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Validasi JWT token dari Supabase Auth.
    Ambil role user dari tabel profiles.
    Dipakai sebagai dependency di semua endpoint yang butuh login.
    """
    token = credentials.credentials

    try:
        # Validasi token ke Supabase Auth
        user_response = supabase.auth.get_user(token)
        user = user_response.user

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token tidak valid"
            )

        # Ambil role dari tabel profiles
        profile = supabase.table("profiles") \
            .select("role") \
            .eq("id", str(user.id)) \
            .single() \
            .execute()

        role = profile.data.get("role") if profile.data else None

        logger.info(f"[AUTH] user_id={user.id} role={role}")

        return {
            "id": str(user.id),
            "email": user.email,
            "role": role
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"[AUTH ERROR] {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak valid atau sudah kedaluwarsa"
        )


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Hanya user dengan role 'admin' yang boleh lewat."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya admin yang dapat mengakses endpoint ini"
        )
    return user


def require_authenticated(user: dict = Depends(get_current_user)) -> dict:
    """Semua user yang sudah login (admin atau dosen) boleh lewat."""
    return user


def optional_authenticated(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> dict:
    """
    Auth opsional — dipakai untuk endpoint yang dipanggil FE.
    FE tidak kirim token (auth ditangani Supabase ProtectedRoute di FE).
    Kalau ada token → divalidasi. Kalau tidak ada → akses tetap diizinkan.
    """
    if credentials is None:
        return {"id": None, "email": None, "role": None}

    try:
        user_response = supabase.auth.get_user(credentials.credentials)
        user = user_response.user

        if not user:
            return {"id": None, "email": None, "role": None}

        profile = (
            supabase.table("profiles")
            .select("role")
            .eq("id", str(user.id))
            .single()
            .execute()
        )
        role = profile.data.get("role") if profile.data else None

        return {"id": str(user.id), "email": user.email, "role": role}

    except Exception:
        # token invalid tapi kita tetap izinkan (FE tidak kirim token)
        return {"id": None, "email": None, "role": None}
