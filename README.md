# 🚚 Bluedart AI Chatbot v2 — INGEN Technology Pvt Ltd
**Made by @rpit Kumar | Clean Architecture | GPT-4o-mini | secutrakdb**

## Quick Start
```bash
cp .env.example .env
# Add OPENAI_API_KEY in .env
pip install -r requirements.txt
uvicorn main:app --reload --port 8002
# Open: http://localhost:8002/docs
```

## Project Structure
```
main.py                          ← FastAPI entry point
├── requirements.txt
├── .env.example                     ← Copy to .env, add API key
├── generate_halt_report.py          ← Standalone Excel report generator
└── app/
    ├── core/
    │   ├── config.py                ← Settings from .env
    │   ├── data_aggregator.py       ← MongoDB API calls (KEY FILE)
    │   ├── intent_detector.py       ← Intent + ID detection
    │   ├── openai_client.py         ← GPT-4o-mini client
    │   └── response_formatter.py   ← Common table format (NEW)
    ├── services/
    │   └── chat_service.py          ← Main pipeline (NEW - clean)
    ├── schemas/
    │   └── chat.py                  ← Request/Response models
    └── api/routes/
        └── chat.py                  ← POST /api/v1/chat
```

  V2 rollout notes and the phased migration plan live in [docs/v2_architecture.md](docs/v2_architecture.md).

## Common Response Format (ALL queries)
```
| S.No | Shipment No | Vehicle No | Driver | Route | Run Date | Destination | Halt | Halt Duration | Stopped Since | Location | Alerts |
```

## Query Types & Filters

| Query Type | Example | Filters |
|-----------|---------|---------|
| STOPPED | "Stopped vehicles > 3 hours" | critical/high/medium/low, full list |
| LOCATION | "Active trips from BHI last week" | source_code, active/inactive |
| BULK | "Download GPS NA trips January 2026" | region, GPS, lock, ATD/ATA, date |
| TRIP | "Where is shipment 11495287?" | shipment_no auto-extracted |
| ALERT | "Alerts for shipment 11460985" | alert level |
| DELAY | "Delay for trip 11455329" | trip delay reasons |

## API Payload
```json
{
  "message": "Your query here",
  "history": [],
  "session_id": "ops-001"
}
```

## Context (Multi-turn)
```json
Turn 2: {
  "message": "Show only critical ones",
  "history": [
    {"role":"user","content":"Stopped vehicles > 3 hours"},
    {"role":"assistant","content":"Total: 230..."}
  ]
}
```

## MongoDB Verification
Every response includes `mongo_query` — paste directly in mongosh:
```bash
use secutrakdb
# paste mongo_query from response
```
