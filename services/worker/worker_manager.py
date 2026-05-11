import asyncio

from services.stt.worker import run_worker
from services.report.summary_worker import run_summary_worker

worker_task = None
summary_worker_task = None


async def start_stt_worker():
    global worker_task

    if worker_task and not worker_task.done():
        return

    print("[SYSTEM] Starting STT Worker...")
    worker_task = asyncio.create_task(run_worker())


async def start_summary_worker():
    global summary_worker_task

    if summary_worker_task and not summary_worker_task.done():
        return

    print("[SYSTEM] Starting Summary Worker...")
    summary_worker_task = asyncio.create_task(run_summary_worker())