from fastapi import APIRouter, HTTPException
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter()
svc    = ChatService()

@router.post("/chat", response_model=ChatResponse,
    summary="Bluedart AI Chatbot",
    description="""
Natural language queries for Bluedart logistics.

**Response always contains:**
- `reply`: Formatted table with columns: S.No | Shipment No | Vehicle No | Driver | Route | Run Date | Destination | Halt | Halt Duration | Stopped Since | Location | Alerts
- `mongo_query`: Paste in mongosh to verify
- `query_type`: STOPPED | LOCATION | BULK | TRIP | ALERT | DELAY | GENERAL
- `context_used`: true if history was referenced

**Filters in message:**
- Severity: 'critical', 'high', 'medium', 'low'
- Full list: 'full list', 'sab dikhao'
- Context: 'it', 'this trip', 'same vehicle', 'from those'
""")
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        return await svc.process(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
