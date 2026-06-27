"""Bluedart AI Chatbot v2 — INGEN Technology | Made by Arpit"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# from app.api.routes import chat
from app.api.routes import chat
from app.api.routes import chat_v2
from app.core.config import settings
from app.core.data_aggregator import BluedartAggregator
import os

# Optional debugpy attach.
# Prevents crashes when the port is already in use (e.g., uvicorn --reload spawns workers).
if os.getenv("DEBUGPY_ENABLED", "0") == "1":
    import debugpy

    debugpy.listen(("0.0.0.0", int(os.getenv("DEBUGPY_PORT", "5678"))))

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Bluedart AI Chatbot v2 starting...")
    print(f"   Model  : {settings.OPENAI_MODEL}")
    agg = BluedartAggregator()
    await agg.load_delay_master_cache()
    print("   Status : ready ✓")
    yield
    print("🛑 Shutting down.")

app = FastAPI(title="Bluedart AI Chatbot v2 — INGEN Technology",
    description="Made By Arpit | GPT-4o-mini | secutrakdb | 9 MongoDB Collections",
    version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
if settings.ENABLE_V2_CHAT:
    app.include_router(chat_v2.router, prefix="/api/v2", tags=["Chat V2"])

@app.get("/", tags=["Health"])
async def root():
    return {"status":"running","version":"2.0.0","model":settings.OPENAI_MODEL,"docs":"/docs"}
@app.get("/health")
async def health():
    return {"status":"healthy"}
