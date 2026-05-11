# run_summary_worker.py
import asyncio
from services.report.summary_worker import run_summary_worker

asyncio.run(run_summary_worker())