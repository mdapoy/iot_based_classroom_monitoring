import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*ArbitraryType.*")

from fastapi import FastAPI
from supabase import create_client
from dotenv import load_dotenv
import os
import asyncio
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api.v1.routes import (
    csv_routes,
    monitoring_routes,
    record_routes,
    report_routes,
    scheduled_routes,
    stt_routes,
    summary_routes
)

from services.stt.worker import run_worker
from services.report.summary_worker import run_summary_worker

origins = ["*"]

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

ENV = os.getenv("ENV", "dev")

# ✅ LIFESPAN 
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[SYSTEM] ENV: {ENV}")
    yield
    print("[SYSTEM] Shutting down...")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(csv_routes.router)
app.include_router(record_routes.router)
app.include_router(scheduled_routes.router)
app.include_router(stt_routes.router)
app.include_router(summary_routes.router)
app.include_router(report_routes.router)
app.include_router(monitoring_routes.router)