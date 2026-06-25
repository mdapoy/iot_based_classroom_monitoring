import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from dotenv import load_dotenv
from supabase import create_client

from core.limiter import limiter
from api.v1.routes import (
    auth_routes,
    xlsx_routes,
    monitoring_routes,
    report_routes,
    rps_routes,
    scheduled_routes,
    stt_routes,
    summary_routes,
    aktivitas_routes,
    dashboard_routes,
    kehadiran_routes,
    evaluation_routes,
    tahun_ajaran_routes,
    pertemuan_routes,
)

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
ENV = os.getenv("ENV", "dev")

supabase = create_client(url, key)


# ══════════════════════════════════════════════════════════════════════════════
# Middleware: Batasi ukuran body request
# Mencegah payload raksasa yang bisa membebani server (DDoS via large body)
# ══════════════════════════════════════════════════════════════════════════════

MAX_BODY_SIZE = int(os.getenv("MAX_BODY_SIZE_MB", "1")) * 1024 * 1024  # default 1 MB

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": (
                                f"Request body melebihi batas "
                                f"{MAX_BODY_SIZE // (1024 * 1024)} MB."
                            )
                        },
                    )
            except ValueError:
                pass   # content-length tidak valid, biarkan lanjut
        return await call_next(request)


# ══════════════════════════════════════════════════════════════════════════════
# Middleware: Security headers
# Mencegah clickjacking, MIME sniffing, dan reflective XSS
# Tidak menggantikan WAF, tapi mempersempit attack surface
# ══════════════════════════════════════════════════════════════════════════════

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
        return response


# ══════════════════════════════════════════════════════════════════════════════
# Lifespan
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[SYSTEM] ENV: {ENV}")
    print(f"[SYSTEM] MAX_BODY_SIZE: {MAX_BODY_SIZE // (1024 * 1024)} MB")
    yield
    print("[SYSTEM] Shutting down...")


# ══════════════════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if ENV == "dev" else None,
    redoc_url="/redoc" if ENV == "dev" else None,
    openapi_url="/openapi.json" if ENV == "dev" else None,
)

# ── 1. Rate limiter (DDoS) ────────────────────────────────────────────────────
# default_limits berlaku untuk SEMUA route kecuali yang punya @limiter.limit sendiri
# "200/minute" → max 200 request per IP per menit
# "1000/hour"  → max 1000 request per IP per jam
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── 2. Body size limit ────────────────────────────────────────────────────────
app.add_middleware(MaxBodySizeMiddleware)

# ── 3. Security headers ───────────────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)

# ── 4. CORS ───────────────────────────────────────────────────────────────────
# Di production ganti "*" dengan domain frontend yang spesifik
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_routes.router)
app.include_router(tahun_ajaran_routes.router)
app.include_router(xlsx_routes.router)
app.include_router(scheduled_routes.router)
app.include_router(stt_routes.router)
app.include_router(summary_routes.router)
app.include_router(report_routes.router)
app.include_router(rps_routes.router)
app.include_router(monitoring_routes.router)
app.include_router(aktivitas_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(kehadiran_routes.router)
app.include_router(evaluation_routes.router)
app.include_router(pertemuan_routes.router)
app.include_router(pertemuan_routes.router_legacy)
