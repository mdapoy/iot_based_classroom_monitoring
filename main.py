from fastapi import FastAPI
from utilities.recorder import start_recording, stop_recording

app = FastAPI()

@app.post("/start-record")
def start_record():
    return start_recording()

@app.post("/stop-record")
def stop_record():
    return stop_recording()