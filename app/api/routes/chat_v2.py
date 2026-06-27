from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas.chat import ChatRequest, ChatResponse
from app.v2.service import get_v2_chat_service
from app.v2.exporters.trip_report_excel import EXPORT_DIR


router = APIRouter()


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Bluedart AI Chatbot V2",
    description="Deterministic hybrid AI query path with modular planning and execution.",
)
async def chat_v2(request: ChatRequest) -> ChatResponse:
    try:
        service = get_v2_chat_service()
        return await service.process(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error: {exc}")


@router.get(
    "/downloads/{filename}",
    summary="Download generated V2 export file",
)
async def download_v2_export(filename: str) -> FileResponse:
    if Path(filename).name != filename or not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = EXPORT_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
