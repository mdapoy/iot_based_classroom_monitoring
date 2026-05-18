from fastapi import APIRouter, Depends
from services.summarizer.summarizer import summarize_text
from api.v1.deps import require_authenticated

router = APIRouter(tags=["summary"])

@router.post("/summary")
async def get_summary(transcript: str, user: dict = Depends(require_authenticated)):
    result = summarize_text(transcript)

    if not result["success"]:
        return {"error": result["error"]}

    return {
        "summary": result["result"]
    }