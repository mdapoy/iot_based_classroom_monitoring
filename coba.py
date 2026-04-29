from fastapi import FastAPI
from services.summarizer import summarize_text

app = FastAPI()


@app.post("/summary")
async def get_summary(transcript: str):
    result = summarize_text(transcript)

    if not result["success"]:
        return {"error": result["error"]}

    return {
        "summary": result["result"]
    }