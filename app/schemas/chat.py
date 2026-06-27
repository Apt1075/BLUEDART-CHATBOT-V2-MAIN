from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    role: str
    content: str

class ServiceCallInfo(BaseModel):
    service: str
    status: str
    data_keys: List[str] = Field(default_factory=list)

class DownloadFileInfo(BaseModel):
    filename: str
    url: str
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    row_count: int = 0

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[ChatMessage] = Field(default_factory=list)
    session_id: Optional[str] = None
    AccessToken: Optional[str] = None
    accessToken: Optional[str] = None
    token: Optional[str] = None

    def get_access_token(self) -> Optional[str]:
        return self.AccessToken or self.accessToken or self.token

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Where is shipment 11495287?",
                "history": [],
                "session_id": "ops-001"
            }
        }

class ChatResponse(BaseModel):
    reply: str
    intent: str
    query_type: str = ""
    extracted_ids: Dict[str, str] = Field(default_factory=dict)
    total_time_seconds: float = 0.0
    session_id: Optional[str] = None
    downloads: List[DownloadFileInfo] = Field(default_factory=list)
    api_request: Optional[Dict[str, Any]] = None
