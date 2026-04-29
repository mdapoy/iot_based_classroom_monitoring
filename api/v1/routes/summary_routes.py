from fastapi import APIRouter
from services.summarizer.summarizer import summarize_text

router = APIRouter(tags=["summary"])

@router.post("/summary")
async def get_summary(transcript: str):
    result = summarize_text(transcript)

    if not result["success"]:
        return {"error": result["error"]}

    return {
        "summary": result["result"]
    }