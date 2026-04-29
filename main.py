from fastapi import FastAPI
from supabase import create_client
from dotenv import load_dotenv
import os
from fastapi.middleware.cors import CORSMiddleware
from api.v1.routes import csv_routes, monitoring_routes, record_routes, report_routes, scheduled_routes, stt_routes
from api.v1.routes import summary_routes

origins = ["*"]

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

# ini menggunakan lifespan untuk menjalankan fungsi load_schedule_from_db saat server start, sehingga scheduler akan langsung aktif tanpa perlu menunggu request pertama
# @asynccontextmanager
# async def lifespan(app: FastAPI):

#     # dijalankan saat server start
#     load_schedule_from_db()

#     yield

# app = FastAPI(lifespan=lifespan)

app = FastAPI()
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