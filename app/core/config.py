from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    OPENAI_API_KEY:     str   = "sk-your-key"
    OPENAI_MODEL:       str   = "gpt-4o-mini"
    OPENAI_MAX_TOKENS:  int   = 1500
    OPENAI_TEMPERATURE: float = 0.2
    ENABLE_V2_CHAT:     bool  = True
    ENABLE_LLM_FORMATTER: bool = False
    ENABLE_SEMANTIC_FALLBACK: bool = True
    SESSION_HISTORY_TURNS: int = 12
    QUERY_LOG_RETENTION_DAYS: int = 7
    SECRET_KEY:         str   = "change-in-production"
    JWT_ALGORITHM:      str   = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    SERVICE_TIMEOUT_SECONDS:     int = 15
    TRIP_SERVICE_URL:      str = "http://localhost:8001"
    DASHBOARD_SERVICE_URL: str = "http://localhost:8002"
    ALERT_SERVICE_URL:     str = "http://localhost:8003"
    VEHICLE_SERVICE_URL:   str = "http://localhost:8004"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
