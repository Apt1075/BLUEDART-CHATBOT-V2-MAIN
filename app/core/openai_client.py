import json
from typing import Any, Dict, List, Optional, Tuple
from openai import AsyncOpenAI
from app.core.config import settings

SYSTEM_PROMPT = """You are an expert AI assistant for Bluedart logistics (INGEN Technology).
Database: secutrakdb (MongoDB). group_id is always "0041".

Collections:
  courier_trip_detail          → trips (GPS=exception_common_backend, FixedLock=_2, PortableLock=_3)
  trip_dashboard_live_status   → live GPS, ETA, halt (last_halt_time1/2/3, vehicle_status1/2/3)
  logistic_trigger_log         → alerts (S180, UNSCHEDULED_HALT)
  bluedart_trigger_dashboard   → trip alert dashboard (alerts[] array)
  courier_route_delay          → delay incidents
  courier_route_delay_master   → delay reason lookup
  courier_trip_detail_customer → trip stops
  Vehicle_wise_lastdata        → vehicle GPS snapshots
  bluedart_lastdata            → IMEI-level GPS

FIELD GUIDE:
  exception_common_backend   = "" Active | "GPS NA" No GPS | "No Connectivity" Offline
  exception_common_backend_2 = Fixed Lock status
  exception_common_backend_3 = Portable Lock status
  trip_status: 1=active 0=closed 2=cancelled
  vehicle_status1/2/3: "Stopped" | "running" | "InActive"
  last_halt_time1/2/3: halt start → now() - last_halt_time = duration
  ATD = actual_source_departure_time | ATA = actual_destination_arrival_time

  trip_dashboard_live_status fields for delay/halt/ETA/ETD:
    - eta: Expected Time of Arrival (date/time string)
    - etd: Expected Time of Departure
    - eta_hrs: ETA hours
    - eta_lt_2h: 1 if ETA is < 2h, else 0
    - stopped_gt_2h: 1 if stopped > 2 hours, else 0
    - stopped_gt_5h: 1 if stopped > 5 hours, else 0
    - stopped_duration: halt duration formatted as 'HH:MM:SS'
    - delay_hr: delay hours
    - delaying_sta: station where delay is occurring
    - delay_trip_gt_60s: 1 if delay is > 60 seconds, else 0
    - delay_hours_2_to_5h: 1 if delay is 2-5h, else 0
    - critical_hours_gt_5h: 1 if critical delay is > 5 hours, else 0

MULTI-TURN: Remember entities from conversation history.
If user says "it","this trip","this vehicle" — use previously mentioned IDs.

ALWAYS end response with:
---MONGO_QUERY---
collection: <collection_name>
query: db.<collection>.find({"group_id":"0041",<filter>},{<projection>}).limit(10)
---END_MONGO_QUERY---"""


def extract_mongo_query(text: str):
    if "---MONGO_QUERY---" not in text:
        return text, "", ""
    parts = text.split("---MONGO_QUERY---")
    clean = parts[0].strip()
    block = parts[1].split("---END_MONGO_QUERY---")[0].strip() if len(parts) > 1 else ""
    collection = ""
    query = ""
    for line in block.split("\n"):
        if line.startswith("collection:"):
            collection = line.replace("collection:","").strip()
        elif line.startswith("query:"):
            query = line.replace("query:","").strip()
        elif query:
            query += "\n" + line
    return clean, collection, query


class OpenAIClient:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model  = settings.OPENAI_MODEL

    async def analyze(
        self,
        user_query: str,
        context_data: Dict[str, Any],
        history: List[Dict] = None,
        intent_instruction: str = "",
        intent: str = "GENERAL_QUERY",
        extracted_ids: Dict = None,
    ) -> Tuple[str, str, str]:
        history       = history or []
        extracted_ids = extracted_ids or {}

        context_summary = ""
        if history:
            context_summary = f"\nCONVERSATION HISTORY ({len(history)//2} turns):\n"
            for msg in history[-6:]:
                role    = "User" if msg["role"] == "user" else "AI"
                content = msg["content"][:200] + "..." if len(msg["content"]) > 200 else msg["content"]
                context_summary += f"  {role}: {content}\n"

        ids_str = ""
        if extracted_ids:
            ids_str = "IDs: " + ", ".join(f"{k}={v}" for k,v in extracted_ids.items() if v) + "\n"

        service_lines = []
        for key, val in context_data.items():
            if key in ("_meta","_mongo_hint"): continue
            if isinstance(val,dict) and "error" in val:
                service_lines.append(f"  ✗ {key}: {val.get('error','error')}")
            else:
                cnt = len(val) if isinstance(val,list) else "ok"
                service_lines.append(f"  ✓ {key}: {cnt}")

        meta = context_data.get("_meta",{})
        meta_str = ""
        if meta:
            lines = [f"  {k}: {v}" for k,v in meta.items() if v is not None]
            if lines: meta_str = "TRIP SUMMARY:\n" + "\n".join(lines) + "\n\n"

        ctx_clean    = {k:v for k,v in context_data.items() if k not in ("_meta","_mongo_hint")}
        context_json = json.dumps(ctx_clean, indent=2, default=str)

        user_msg = f"""USER QUERY: {user_query}

INTENT: {intent}
INSTRUCTION: {intent_instruction}
{ids_str}{context_summary}
SERVICES:
{chr(10).join(service_lines)}

{meta_str}DATA:
{context_json}

Answer query. End with:
---MONGO_QUERY---
collection: <name>
query: db.<collection>.find(...)
---END_MONGO_QUERY---"""

        messages = [
            {"role":"system","content":SYSTEM_PROMPT},
            *history[-8:],
            {"role":"user","content":user_msg},
        ]

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=settings.OPENAI_MAX_TOKENS,
            temperature=settings.OPENAI_TEMPERATURE,
        )

        full_text = response.choices[0].message.content
        return extract_mongo_query(full_text)
