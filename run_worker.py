# run_worker.py
import asyncio
from services.stt.worker import run_worker

asyncio.run(run_worker())