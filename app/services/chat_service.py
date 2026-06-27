"""
app/services/chat_service.py — Bluedart AI Chatbot v2
=======================================================
"""

import time, re, asyncio as _asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import httpx

from app.core.data_aggregator import (
    fetch_alerts_direct,
    BluedartAggregator,
    mongo_select, calc_halt_from_last_halt_time,
    GROUP_ID, STATUS_ON,
    fetch_vehicle_location,
    fetch_max_trips_vehicle,
    fetch_running_trips,
    dynamic_trip_query,
)
from app.core.intent_detector import IntentDetector, Intent
from app.core.openai_client import OpenAIClient, extract_mongo_query
from app.core.response_formatter import (
    format_response, format_stopped_vehicles, format_bulk_report,
    format_location_trips, format_alerts, format_delays, format_trip_status,
    calc_halt_mins, format_halt_duration, get_severity, format_max_trips,
)
from app.schemas.chat import ChatRequest, ChatResponse, ServiceCallInfo

MONGO_API = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"

# ─── Trigger keywords ─────────────────────────────────────────────────────────

STOPPED_TRIGGERS = [
    "stopped for more than","stopped more than","stopped vehicles",
    "halted more than","halt more than","maximum halt","max halt",
    "halt duration","halt time","vehicles stopped",
    "stopped for less","stopped less","halted less","halt less",
    "stopped for","halted for",
]
REPORT_TRIGGERS = [
    "download all","show all","list all","all trips",
    "for january","for february","for march","for april","for may",
    "last month","last week","last day",
    "north region","south region","east region","west region",
    "gps inactive","gps na","fixed e-lock","fixed lock",
    "portable e-lock","portable lock","icici device","3rd party",
    "atd not captured","ata not captured","atd missing","ata missing",
    "cancelled trips","forcefully closed","departure delayed","arrival delayed",
    "for route","all trips of","trips for vehicle","trips where","trips with",
]
DYNAMIC_FILTER_TRIGGERS = [
    "sfc trips","feeder trips","surface trips","air trips","lh trips","linehaul trips",
    "without portable","no portable lock","no portable",
    "fixed lock na","portable lock na",
    "fixed gps na","portable gps na",
    "axestrack trips","lynkit trips","kiasaint trips",
    "force close","forcefully closed trips",
    "north region trips","south region trips","east region trips","west region trips",
    "trips from","trips to",
]
ANALYTICS_TRIGGERS = [
    "highest number of trips","maximum trips","max trips","most trips",
    "vehicle with most","maximum distance","max distance",
    "covered maximum","sabse zyada trips","top vehicle",
]
RUNNING_TRIGGERS = [
    "running trips","active trips","current trips","abhi chal rahe",
    "live trips","trips running","trip_status 1",
]
TRANSSHIP_TRIGGERS = [
    "transshipment","trans shipment","transship","tranship",
    "kitne transship","how many transship","trans-shipment",
    "transshipment today","aaj transship","transshipment aaj",
    "gone trans shipment","trans shipment today",
]
CLOSE_TRIP_TRIGGERS = [
    "aaj kitne trips close","kitne trips close","trips close",
    "close trips today","trips closed today","how many trips closed",
    "aaj close hue","close hue trips","trip close today",
    "closed today","trips completed today","how many closed",
    "aaj kitne close","kitne close","trips aaj close",
]

# Context words — only EXPLICIT references to previous entity
CONTEXT_WORDS = [
    "this trip", "this vehicle", "this shipment",
    "same vehicle", "same trip",
    "that vehicle", "that trip", "that shipment",
    "for this trip", "for this vehicle",
    "about this trip", "on this trip", "on the trip",
    "for the trip", "for the vehicle",
    "will it arrive", "when will it", "will it reach",
    "kab aayega", "kab pahunchega",
    "yeh trip", "yeh vehicle",
    "usi trip", "usi vehicle",
    "is trip ka", "is vehicle ka",
    "is trip ki", "is vehicle ki",
]
FULL_LIST_TRIGGERS = [
    "full list","all vehicles","sab dikhao","complete list",
    "all records","poori list","sabhi","everything","show full","all data",
]
SEVERITY_MAP = {
    "critical": lambda m: m >= 1440,
    "high":     lambda m: 300 <= m < 1440,
    "medium":   lambda m: 180 <= m < 300,
    "low":      lambda m: m < 180,
}
ALERT_KEYWORDS = [
    "alert","trigger","koi trigger","s180","halt trigger",
    "unscheduled","speeding","voilation","violation","flag","qrt","threat",
    "alarm","warning","koi alert","any alert","triggered","pe koi","trigger hua",
]
DELAY_KEYWORDS = [
    "delay","late","der","delayed","hold","kyu ruka",
    "why stopped","delay report","kitna late","how late","delay reason","total_delay",
]
INVESTIGATE_KEYWORDS = [
    "investigate","delay aur alert","delay and alert",
    "delay aur trigger","alert aur delay","both delay","dono","delay and trigger",
]


# ─── Helper functions ─────────────────────────────────────────────────────────

def extract_hours(msg: str) -> float:
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:hour|hr|h)\b', msg.lower())
    if m: return float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:minute|min)\b', msg.lower())
    if m: return float(m.group(1)) / 60
    return 0.0

def is_less_than(msg: str) -> bool:
    return any(x in msg.lower() for x in ["less than","less","under","below","within"])

def wants_full_list(msg: str) -> bool:
    return any(kw in msg.lower() for kw in FULL_LIST_TRIGGERS)

def uses_context(msg: str) -> bool:
    return any(kw in msg.lower() for kw in CONTEXT_WORDS)

def should_use_history(msg: str, ids: dict) -> bool:
    """
    Use history IDs ONLY when user explicitly refers to a previous entity.
    Token-based — sirf CONTEXT_WORDS match hone par history se IDs lo.

    Rules:
      1. Explicit context token present ("this trip", "kab aayega" etc.) → always use
      2. Current message mein apne IDs hain → history mat use karo
      3. No IDs + no context token + bulk/filter query → history mat use karo
         (warna bulk reply ke HTML se shipment_no inject ho jaata hai)
    """
    msg_lower = msg.lower()

    # Rule 1: Explicit context word → hamesha history use karo
    if any(kw in msg_lower for kw in CONTEXT_WORDS):
        return True

    # Rule 2: Current message mein apne IDs hain → fresh query, no history
    has_ids = any(ids.get(k) for k in ["shipment_no", "vehicle_no", "imei"])
    if has_ids:
        return False

    # Rule 3: Bulk/filter type query → history se IDs inject nahi karni
    # (Axestrack trips, SFC trips jaise queries mein
    #  previous reply ka shipment_no context mein nahi lena)
    is_bulk_query = (
        any(kw in msg_lower for kw in DYNAMIC_FILTER_TRIGGERS) or
        any(kw in msg_lower for kw in REPORT_TRIGGERS) or
        any(kw in msg_lower for kw in RUNNING_TRIGGERS) or
        any(kw in msg_lower for kw in ANALYTICS_TRIGGERS) or
        any(kw in msg_lower for kw in TRANSSHIP_TRIGGERS) or
        any(kw in msg_lower for kw in CLOSE_TRIP_TRIGGERS)
    )
    if is_bulk_query:
        return False

    # Rule 4: Short follow-up with no IDs and no bulk trigger
    # → genuine follow-up, history use karo
    return len(msg.strip()) >= 5

def detect_severity(msg: str) -> Optional[str]:
    msg = msg.lower()
    if any(k in msg for k in ["critical","24h+",">24h"]): return "critical"
    if any(k in msg for k in ["high","5-24h","5h to"]):   return "high"
    if any(k in msg for k in ["medium","3-5h","3h to"]):  return "medium"
    if any(k in msg for k in ["low ","<3h","less than 3"]):return "low"
    return None

def detect_location_query(msg: str):
    m = re.search(r'\bFROM\s+([A-Z]{2,5})\b', msg.upper())
    if m:
        code = m.group(1)
        exclude = {"THE","ALL","LAST","THIS","WEEK","MONTH","DAY","THOSE","NORTH","SOUTH","EAST","WEST"}
        if code not in exclude:
            return True, code
    return False, None

def msg_has_alert(msg: str) -> bool:
    return any(kw in msg.lower() for kw in ALERT_KEYWORDS)

def msg_has_delay(msg: str) -> bool:
    return any(kw in msg.lower() for kw in DELAY_KEYWORDS)

def msg_has_investigate(msg: str) -> bool:
    return any(kw in msg.lower() for kw in INVESTIGATE_KEYWORDS)

def extract_ids_from_history(history: list) -> dict:
    ids = {"shipment_no":None,"vehicle_no":None,"region_code":None,"date_from":None,"date_to":None}

    # ONLY user messages se IDs nikalo — AI HTML reply se nahi
    # Warna bulk table mein dikhaye gaye shipment_no agli query mein inject ho jaate hain
    user_text = " ".join(
        m.get("content","") for m in history if m.get("role") == "user"
    )
    all_text = " ".join(m.get("content","") for m in history)  # date range ke liye

    # 15-digit first, then 7-9 digit — sirf user messages mein
    m = re.search(r'\b(9\d{14})\b', user_text)
    if m:
        ids["shipment_no"] = m.group(1)
    else:
        m = re.search(r'\b(\d{7,9})\b', user_text)
        if m: ids["shipment_no"] = m.group(1)

    # Trip XXXXXXX pattern — sirf user messages mein
    m = re.search(r'Trip\s+(\d{7,15})', user_text)
    if m and not ids["shipment_no"]:
        ids["shipment_no"] = m.group(1)

    # Vehicle no — sirf user messages mein
    m = re.search(r'\b([A-Z]{2}\d{2}[A-Z]{1,3}\d{4})\b', user_text.upper())
    if m: ids["vehicle_no"] = m.group(1)

    # Region — user messages mein
    for region in ["NORTH","SOUTH","EAST","WEST","WEST2"]:
        if region.lower() in user_text.lower():
            ids["region_code"] = region; break

    # Date ranges — full history mein (context ke liye theek hai)
    now = datetime.now()
    if "january" in all_text.lower():
        ids["date_from"]="2026-01-01 00:00:00"; ids["date_to"]="2026-01-31 23:59:59"
    elif "last month" in all_text.lower():
        first=(now.replace(day=1)-timedelta(days=1)).replace(day=1)
        last=now.replace(day=1)-timedelta(days=1)
        ids["date_from"]=first.strftime("%Y-%m-%d 00:00:00")
        ids["date_to"]=last.strftime("%Y-%m-%d 23:59:59")
    elif "last week" in all_text.lower():
        ids["date_from"]=(now-timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
        ids["date_to"]=now.strftime("%Y-%m-%d 23:59:59")
    return ids



# ─── Date range parser ────────────────────────────────────────────────────────

def parse_date_range(msg: str) -> tuple:
    now = datetime.now()
    msg_l = msg.lower()
    MONTHS = {
        "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
        "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
        "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,
        "sep":9,"oct":10,"nov":11,"dec":12,
    }

    # 1. Specific day-month patterns (e.g. "12 may 2026", "15 may", "22 may")
    # Pattern A: Day Month Year
    pattern_a = r"\b(\d{1,2})[-/\s]+(" + "|".join(MONTHS.keys()) + r")\b(?:[-/\s]+(\d{2,4}))?\b"
    m_a = re.search(pattern_a, msg_l)
    if m_a:
        day = int(m_a.group(1))
        month_name = m_a.group(2)
        month_num = MONTHS[month_name]
        year_str = m_a.group(3)
        if year_str:
            year = int(year_str)
            if len(year_str) == 2:
                year += 2000
        else:
            year = now.year
            if month_num > now.month:
                year -= 1
        try:
            target_date = datetime(year, month_num, day)
            d_str = target_date.strftime("%Y-%m-%d")
            return (d_str + " 00:00:00", d_str + " 23:59:59")
        except ValueError:
            pass

    # Pattern B: Month Day Year
    pattern_b = r"\b(" + "|".join(MONTHS.keys()) + r")[-/\s]+(\d{1,2})(?:st|nd|rd|th)?\b(?:[-/\s]+(\d{2,4}))?\b"
    m_b = re.search(pattern_b, msg_l)
    if m_b:
        month_name = m_b.group(1)
        day = int(m_b.group(2))
        month_num = MONTHS[month_name]
        year_str = m_b.group(3)
        if year_str:
            year = int(year_str)
            if len(year_str) == 2:
                year += 2000
        else:
            year = now.year
            if month_num > now.month:
                year -= 1
        try:
            target_date = datetime(year, month_num, day)
            d_str = target_date.strftime("%Y-%m-%d")
            return (d_str + " 00:00:00", d_str + " 23:59:59")
        except ValueError:
            pass

    # 2. Look for Month only (full month range)
    for name, num in MONTHS.items():
        if re.search(r'\b' + re.escape(name) + r'\b', msg_l):
            import calendar
            year_match = re.search(r'\b(20\d{2}|\d{2})\b', msg_l)
            if year_match:
                year_val = int(year_match.group(1))
                if len(year_match.group(1)) == 2:
                    year = 2000 + year_val
                else:
                    year = year_val
            else:
                year = now.year
                if num > now.month: year -= 1
            last_day = calendar.monthrange(year, num)[1]
            return (f"{year}-{num:02d}-01 00:00:00", f"{year}-{num:02d}-{last_day} 23:59:59")

    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', msg_l)
    if m: return (m.group(0) + " 00:00:00", m.group(0) + " 23:59:59")

    m = re.search(r'last\s+(\d+)\s+days?', msg_l)
    if m:
        n = int(m.group(1))
        return ((now-timedelta(days=n)).strftime("%Y-%m-%d 00:00:00"), now.strftime("%Y-%m-%d %H:%M:%S"))

    if any(w in msg_l for w in ["yesterday","kal","kal ka"]):
        d = (now-timedelta(days=1)).strftime("%Y-%m-%d")
        return (d+" 00:00:00", d+" 23:59:59")

    if any(w in msg_l for w in ["last week","pichle hafte","week"]):
        return ((now-timedelta(days=7)).strftime("%Y-%m-%d 00:00:00"), now.strftime("%Y-%m-%d %H:%M:%S"))

    if any(w in msg_l for w in ["last month","pichle mahine"]):
        first=(now.replace(day=1)-timedelta(days=1)).replace(day=1)
        last=now.replace(day=1)-timedelta(days=1)
        return (first.strftime("%Y-%m-%d 00:00:00"), last.strftime("%Y-%m-%d 23:59:59"))

    if any(w in msg_l for w in ["this month","is mahine"]):
        return (now.strftime("%Y-%m-01 00:00:00"), now.strftime("%Y-%m-%d %H:%M:%S"))

    # Default: today
    return (now.strftime("%Y-%m-%d 00:00:00"), now.strftime("%Y-%m-%d %H:%M:%S"))


# ─── Fetch functions ──────────────────────────────────────────────────────────

async def fetch_stopped_vehicles(message: str) -> dict:
    now      = datetime.now()
    from_str = now.strftime("%Y-%m-%d 00:00:00")
    hours    = extract_hours(message)
    less     = is_less_than(message)
    threshold_m = hours * 60

    conditions = {
        "group_id": GROUP_ID, "status": STATUS_ON,
        "trip_status": 1, "vehicle_status_current": "Stopped",
        "update_time": {"$gte": from_str},
    }
    print(f"   [live_status] today stopped...", end=" ", flush=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0,read=60.0,write=15.0,pool=15.0)) as client:
        result = await mongo_select(client, "trip_dashboard_live_status", conditions)
    cnt = len(result) if isinstance(result, list) else 0
    print(f"OK ({cnt})")

    if not isinstance(result, list) or cnt == 0:
        return {
            "error": "No stopped vehicles today", "_hours": hours, "_less": less,
            "_mongo_hint": {"collection":"trip_dashboard_live_status","query":"..."}
        }

    vehicles = []
    for rec in result:
        candidates = []
        for i in ["1","2","3"]:
            st  = rec.get(f"vehicle_status{i}")
            lht = rec.get(f"last_halt_time{i}")
            if st == "Stopped" and lht:
                mins, dur = calc_halt_from_last_halt_time(lht)
                candidates.append({"mins":mins,"dur":dur,"halt_time":lht,
                                   "address":rec.get(f"last_address{i}","")})
        if not candidates and rec.get("last_halt_time_current"):
            mins, dur = calc_halt_from_last_halt_time(rec["last_halt_time_current"])
            candidates.append({"mins":mins,"dur":dur,"halt_time":rec["last_halt_time_current"],
                               "address":rec.get("last_address_current","")})
        if not candidates: continue
        best = max(candidates, key=lambda x: x["mins"])
        max_mins = best["mins"]
        if threshold_m > 0:
            if less and max_mins >= threshold_m: continue
            if not less and max_mins < threshold_m: continue

        gps = {}
        ld  = rec.get("last_data_current", {})
        if isinstance(ld, dict):
            def f(v): return v[0] if isinstance(v,list) and v else v
            gps = {"lat":f(ld.get("latitudeLR")),"lng":f(ld.get("longitudeLR")),
                   "speed":f(ld.get("speedLR")),"vendor":f(ld.get("io8LR"))}

        vehicles.append({
            "vehicle_no":      rec.get("vehicle_no"),
            "shipment_no":     rec.get("shipment_no"),
            "shipment_method": rec.get("shipment_method"),
            "halt_since":      best["halt_time"],
            "halt_duration":   best["dur"],
            "halt_minutes":    max_mins,
            "halt_hours":      round(max_mins/60,1),
            "severity":        get_severity(max_mins),
            "last_address":    best["address"] or rec.get("last_address_current",""),
            "eta":             rec.get("eta"),
            "delay_hr":        rec.get("delay_hr"),
            "gps":             gps,
        })

    vehicles.sort(key=lambda x: x["halt_minutes"], reverse=(not less))
    critical = sum(1 for v in vehicles if v["halt_minutes"] >= 1440)
    high     = sum(1 for v in vehicles if 300 <= v["halt_minutes"] < 1440)
    medium   = sum(1 for v in vehicles if 180 <= v["halt_minutes"] < 300)
    low      = sum(1 for v in vehicles if v["halt_minutes"] < 180)

    return {
        "query_info": {"filter":f"Stopped today","threshold":f"{'<' if less else '>'}{hours}h"},
        "summary": {"total":len(vehicles),"critical_gt_24h":critical,"high_5_24h":high,
                    "medium_3_5h":medium,"low_lt_3h":low},
        "vehicles": vehicles, "_hours": hours, "_less": less,
        "_mongo_hint": {
            "collection": "trip_dashboard_live_status",
            "query": f'db.trip_dashboard_live_status.find({{"group_id":"0041","status":1,"trip_status":1,"vehicle_status_current":"Stopped","update_time":{{"$gte":"{from_str}"}}}}).sort({{"update_time":-1}})'
        }
    }


async def fetch_location_trips(source_code: str, trip_status: int = 1, date_range: str = "last_week") -> dict:
    now = datetime.now()
    if date_range == "last_week":
        date_from = (now-timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
        date_to   = now.strftime("%Y-%m-%d 23:59:59")
    elif date_range == "last_month":
        first=(now.replace(day=1)-timedelta(days=1)).replace(day=1)
        last=now.replace(day=1)-timedelta(days=1)
        date_from = first.strftime("%Y-%m-%d 00:00:00")
        date_to   = last.strftime("%Y-%m-%d 23:59:59")
    else:
        date_from = now.strftime("%Y-%m-%d 00:00:00")
        date_to   = now.strftime("%Y-%m-%d 23:59:59")

    conditions = {
        "group_id": GROUP_ID, "status": STATUS_ON,
        "trip_status": trip_status, "source_code": source_code.upper(),
        "run_date": {"$gte": date_from, "$lte": date_to}
    }
    projection = {
        "shipment_no":1,"vehicle_no":1,"driver_name":1,"source_name":1,
        "source_code":1,"destination_name":1,"destination_code":1,
        "route_name":1,"run_date":1,"trip_status":1,"shipment_method":1,
        "exception_common_backend":1,"exception_common_backend_2":1,
        "exception_common_backend_3":1,"schedule_departure":1,"fleet_no":1,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        result = await mongo_select(client, "courier_trip_detail", conditions, projection)

    count = len(result) if isinstance(result, list) else 0
    if count == 0:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            check = await mongo_select(client, "courier_trip_detail",
                {"group_id":GROUP_ID,"status":STATUS_ON,"source_code":source_code.upper()},{})
        check_count = len(check) if isinstance(check, list) else 0
        if check_count == 0:
            return {"error":f"Location '{source_code}' not found",
                    "user_message":f"'{source_code.upper()}' location DB mein exist nahi karta."}
        latest = check[0].get("run_date","") if isinstance(check,list) and check else ""
        return {"info":"Location exists but no trips","_total":0,
                "user_message":f"'{source_code.upper()}' mein {date_range} koi trip nahi. Latest: {latest[:10]}."}

    gps_active  = sum(1 for t in result if isinstance(t,dict) and t.get("exception_common_backend","")=="")
    gps_na      = sum(1 for t in result if isinstance(t,dict) and t.get("exception_common_backend","")=="GPS NA")
    gps_no_conn = sum(1 for t in result if isinstance(t,dict) and t.get("exception_common_backend","")=="No Connectivity")

    return {
        "query_info": {"source_code":source_code.upper(),"trip_status":"ACTIVE" if trip_status==1 else "INACTIVE",
                       "date_range":f"{date_from[:10]} to {date_to[:10]}","collection":"courier_trip_detail"},
        "summary": {"total_trips":count,"gps_active":gps_active,"gps_na":gps_na,"gps_no_connectivity":gps_no_conn},
        "trips": result if isinstance(result,list) else [],
        "_total": count,
        "_mongo_hint": {"collection":"courier_trip_detail","query":f'db.courier_trip_detail.find({{"group_id":"0041","status":1,"trip_status":{trip_status},"source_code":"{source_code.upper()}","run_date":{{"$gte":"{date_from}","$lte":"{date_to}"}}}}).sort({{"run_date":-1}}).limit(50)'}
    }


async def fetch_bulk_report(message: str) -> dict:
    msg = message.lower()
    now = datetime.now()
    conditions = {"group_id":GROUP_ID,"status":STATUS_ON}

    if any(x in msg for x in ["inactive trips","closed trips","inactive only"]):   conditions["trip_status"] = 0
    elif any(x in msg for x in ["active trips","active only"]) and "gps" not in msg: conditions["trip_status"] = 1
    elif any(x in msg for x in ["cancelled trips","cancelled"]):                   conditions["trip_status"] = 2

    for region in ["NORTH","SOUTH","EAST","WEST","WEST2"]:
        if region.lower() in msg:
            conditions["region_code"] = region; break

    if "january" in msg:    conditions["run_date"] = {"$gte":"2026-01-01 00:00:00","$lte":"2026-01-31 23:59:59"}
    elif "february" in msg: conditions["run_date"] = {"$gte":"2026-02-01 00:00:00","$lte":"2026-02-28 23:59:59"}
    elif "march" in msg:    conditions["run_date"] = {"$gte":"2026-03-01 00:00:00","$lte":"2026-03-31 23:59:59"}
    elif "april" in msg:    conditions["run_date"] = {"$gte":"2026-04-01 00:00:00","$lte":"2026-04-30 23:59:59"}
    elif "may" in msg:      conditions["run_date"] = {"$gte":"2026-05-01 00:00:00","$lte":"2026-05-31 23:59:59"}
    elif "last month" in msg:
        first=(now.replace(day=1)-timedelta(days=1)).replace(day=1)
        last=now.replace(day=1)-timedelta(days=1)
        conditions["run_date"] = {"$gte":first.strftime("%Y-%m-%d 00:00:00"),"$lte":last.strftime("%Y-%m-%d 23:59:59")}
    elif "last week" in msg:
        conditions["run_date"] = {"$gte":(now-timedelta(days=7)).strftime("%Y-%m-%d 00:00:00"),"$lte":now.strftime("%Y-%m-%d 23:59:59")}

    if "run_date" not in conditions:
        conditions["run_date"] = {"$gte":(now-timedelta(days=30)).strftime("%Y-%m-%d 00:00:00"),"$lte":now.strftime("%Y-%m-%d 23:59:59")}
        print("   [auto-date] Last 30 days applied")

    if any(x in msg for x in ["gps active","gps is active","gps_active"]):
        # "" exact match nahi karta jab field null/missing ho
        # isliye NOT IN GPS NA / No Connectivity use karo
        conditions["exception_common_backend"] = {"$nin": ["GPS NA", "No Connectivity"]}
    elif any(x in msg for x in ["gps inactive","gps na","gps is na","gps_na"]):
        conditions["exception_common_backend"] = "GPS NA"
    elif "no connectivity" in msg:
        conditions["exception_common_backend"] = "No Connectivity"

    if any(x in msg for x in ["fixed e-lock","fixed elock","fixed lock"]):
        if any(x in msg for x in ["inactive","gps na","na"]): conditions["exception_common_backend_2"] = {"$in":["GPS NA","No Connectivity"]}
        elif "active" in msg: conditions["exception_common_backend_2"] = ""
        else: conditions["imei_no2"] = {"$exists":True,"$ne":""}

    if any(x in msg for x in ["portable e-lock","portable elock","portable lock"]):
        if any(x in msg for x in ["inactive","gps na","na"]): conditions["exception_common_backend_3"] = {"$in":["GPS NA","No Connectivity"]}
        elif "active" in msg: conditions["exception_common_backend_3"] = ""
        else: conditions["imei_no3"] = {"$exists":True,"$ne":""}

    if "atd" in msg and any(x in msg for x in ["not captured","missing","was not"]):
        conditions["actual_source_departure_time"] = {"$in":["",None]}
    if "ata" in msg and any(x in msg for x in ["not captured","missing","was not"]):
        conditions["actual_destination_arrival_time"] = {"$in":["",None]}

    if "cancelled" in msg: conditions["trip_status"] = 2
    if any(x in msg for x in ["forcefully closed","force closed","supervisor close"]):
        conditions["close_remarks"] = {"$regex":"supervisor","$options":"i"}

    vm = re.search(r'\b([A-Z]{2}\d{2}[A-Z]{1,3}\d{4})\b', message.upper())
    if vm: conditions["vehicle_no"] = vm.group(1)

    if "icici" in msg:
        conditions["gps_vendor_name"] = {"$regex":"icici","$options":"i"}
    elif any(x in msg for x in ["3rd party","third party"]):
        conditions["gps_vendor_name"] = {"$nin":["","Axestrack_bluedart","Kiasaint_bluedart","Lynkit_Bluedart"]}

    if "captured through gps" in msg:    conditions["ata_source"] = "GPS"
    elif "captured manually" in msg:     conditions["ata_source"] = "MANUAL"
    elif "captured through api" in msg:  conditions["ata_source"] = "API"

    print(f"   BULK CONDITIONS: {conditions}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0,read=180.0,write=15.0,pool=15.0)) as client:
        result = await mongo_select(client,"courier_trip_detail",conditions,
            {"shipment_no":1,"vehicle_no":1,"driver_name":1,"source_name":1,
             "destination_name":1,"route_name":1,"run_date":1,"trip_status":1,
             "shipment_method":1,"exception_common_backend":1,
             "exception_common_backend_2":1,"exception_common_backend_3":1,
             "actual_source_departure_time":1,"actual_destination_arrival_time":1},
            sort={"run_date":-1},limit=20)

    count = len(result) if isinstance(result,list) else 0
    print(f"   BULK RESULT: {count} records")
    return {
        "total_found":count,"filters_applied":conditions,
        "data":result if isinstance(result,list) else [],
        "full_report_url":"https://cv18.secutrak.in/cv/specific/bluedart/Report/TripReport",
        "delay_report_url":"https://cv18.secutrak.in/cv/specific/bluedart/Delay-Dashboard",
        "_mongo_hint":{"collection":"courier_trip_detail","query":f"db.courier_trip_detail.find({conditions}).sort({{run_date:-1}}).limit(20)"}
    }


async def fetch_transshipment(msg: str = "today") -> dict:
    date_from, date_to = parse_date_range(msg)
    conditions = {"group_id":GROUP_ID,"status":STATUS_ON,"transshipment_date":{"$gte":date_from,"$lte":date_to}}
    print(f"   [transship] {date_from} to {date_to}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0,read=60.0,write=15.0,pool=15.0)) as client:
        result = await mongo_select(client,"courier_trip_detail",conditions,
            {"shipment_no":1,"vehicle_no":1,"driver_name":1,"route_name":1,
             "source_name":1,"destination_name":1,"run_date":1,"trip_status":1,
             "shipment_method":1,"transshipment_id":1,"transshipment_date":1,
             "region_code":1,"exception_common_backend":1},
            sort={"transshipment_date":-1},limit=20)
    count = len(result) if isinstance(result,list) else 0
    print(f"   [transship] {count} records")
    return {
        "total_found":count,"period":date_from[:10]+" to "+date_to[:10],
        "filters_applied":{"transshipment_date":{"$gte":date_from,"$lte":date_to}},
        "data":result if isinstance(result,list) else [],
        "_mongo_hint":{"collection":"courier_trip_detail",
            "query":'db.courier_trip_detail.find({"group_id":"0041","status":1,"transshipment_date":{"$gte":"'+date_from+'","$lte":"'+date_to+'"}}).sort({"transshipment_date":-1}).limit(20)'}
    }


async def fetch_trips_by_status(msg: str = "today", trip_status: int = 0) -> dict:
    date_from, date_to = parse_date_range(msg)
    status_labels = {0:"CLOSED",1:"ACTIVE",2:"CANCELLED"}
    status_label  = status_labels.get(trip_status,str(trip_status))
    conditions = {"group_id":GROUP_ID,"status":STATUS_ON,"trip_status":trip_status,
                  "run_date":{"$gte":date_from,"$lte":date_to}}
    print(f"   [trips_{status_label}] {date_from} to {date_to}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0,read=60.0,write=15.0,pool=15.0)) as client:
        result = await mongo_select(client,"courier_trip_detail",conditions,
            {"shipment_no":1,"vehicle_no":1,"driver_name":1,"route_name":1,
             "source_name":1,"destination_name":1,"run_date":1,"trip_status":1,
             "shipment_method":1,"region_code":1,"fleet_no":1,
             "exception_common_backend":1,"actual_destination_arrival_time":1},
            sort={"run_date":-1},limit=20)
    count = len(result) if isinstance(result,list) else 0
    print(f"   [trips_{status_label}] {count} records")
    return {
        "trip_status_label":status_label,"total_found":count,
        "period":date_from[:10]+" to "+date_to[:10],
        "filters_applied":{"trip_status":trip_status,"run_date":{"$gte":date_from,"$lte":date_to}},
        "data":result if isinstance(result,list) else [],
        "_mongo_hint":{"collection":"courier_trip_detail",
            "query":'db.courier_trip_detail.find({"group_id":"0041","status":1,"trip_status":'+str(trip_status)+',"run_date":{"$gte":"'+date_from+'","$lte":"'+date_to+'"}}).sort({"run_date":-1}).limit(20)'}
    }


# ─── HTML builders ────────────────────────────────────────────────────────────

def _build_transship_html(data: dict) -> str:
    count  = data.get("total_found",0)
    period = data.get("period","")
    trips  = data.get("data",[])
    th_s   = 'style="padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:#fff;background:#1e3a5f;white-space:nowrap"'
    td_s   = 'style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#374151;font-size:12px"'
    cols   = ["#","Shipment No","Vehicle No","Driver","Route","Source","Destination","Run Date","Transship ID","Transship Date","GPS"]
    h_html = "".join('<th '+th_s+'>'+c+'</th>' for c in cols)
    r_html = ""
    for i,t in enumerate(trips[:20],1):
        bg=("#fff" if i%2 else "#f9fafb"); exc=t.get("exception_common_backend","")
        gps="Active" if exc=="" else ("GPS NA" if exc=="GPS NA" else "No Conn")
        gps_col="#16a34a" if exc=="" else ("#dc2626" if exc=="GPS NA" else "#d97706")
        r_html += (
            '<tr style="background:'+bg+'">'
            +'<td '+td_s+'>'+str(i)+'</td>'
            +'<td '+td_s+'>'+str(t.get("shipment_no",""))+'</td>'
            +'<td '+td_s+'>'+str(t.get("vehicle_no",""))+'</td>'
            +'<td '+td_s+'>'+str(t.get("driver_name",""))[:12]+'</td>'
            +'<td '+td_s+'>'+str(t.get("route_name",""))[:14]+'</td>'
            +'<td '+td_s+'>'+str(t.get("source_name",""))[:14]+'</td>'
            +'<td '+td_s+'>'+str(t.get("destination_name",""))[:14]+'</td>'
            +'<td '+td_s+'>'+str(t.get("run_date",""))[:16]+'</td>'
            +'<td '+td_s+'>'+str(t.get("transshipment_id","—"))+'</td>'
            +'<td '+td_s+'>'+str(t.get("transshipment_date",""))[:16]+'</td>'
            +'<td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:'+gps_col+';font-weight:500;font-size:12px">'+gps+'</td>'
            +'</tr>'
        )
    if not trips:
        r_html='<tr><td colspan="11" style="text-align:center;padding:16px;color:#9ca3af">No transshipments found</td></tr>'
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:960px">'
        '<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#6b7280;margin-bottom:12px">Transshipments — '+period+'</div>'
        '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px">'
        '<div style="background:#f9fafb;border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Total Transshipments</div><div style="font-size:28px;font-weight:600;color:#1e3a5f">'+str(count)+'</div></div>'
        '<div style="background:#f9fafb;border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Period</div><div style="font-size:13px;font-weight:500;color:#111827;margin-top:4px">'+period+'</div></div>'
        '</div>'
        '<div style="overflow-x:auto;border-radius:10px;border:1px solid #e5e7eb">'
        '<table style="width:100%;border-collapse:collapse;min-width:700px">'
        '<thead><tr>'+h_html+'</tr></thead><tbody>'+r_html+'</tbody>'
        '</table></div>'
        '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing '+str(min(20,len(trips)))+' of '+str(count)+' · '+period+'</div>'
        '</div>'
    )


def _build_close_trips_html(data: dict) -> str:
    count  = data.get("total_found",0)
    period = data.get("period","")
    trips  = data.get("data",[])
    label  = data.get("trip_status_label","CLOSED")
    th_s   = 'style="padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:#fff;background:#1e3a5f;white-space:nowrap"'
    td_s   = 'style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#374151;font-size:12px"'
    cols   = ["#","Shipment No","Vehicle No","Driver","Route","Source","Destination","Run Date","Method","ATA","GPS"]
    h_html = "".join('<th '+th_s+'>'+c+'</th>' for c in cols)
    r_html = ""
    for i,t in enumerate(trips[:20],1):
        bg=("#fff" if i%2 else "#f9fafb"); exc=t.get("exception_common_backend","")
        gps="Active" if exc=="" else ("GPS NA" if exc=="GPS NA" else "No Conn")
        gps_col="#16a34a" if exc=="" else ("#dc2626" if exc=="GPS NA" else "#d97706")
        ata=str(t.get("actual_destination_arrival_time","") or "—")[:16]
        r_html += (
            '<tr style="background:'+bg+'">'
            +'<td '+td_s+'>'+str(i)+'</td>'
            +'<td '+td_s+'>'+str(t.get("shipment_no",""))+'</td>'
            +'<td '+td_s+'>'+str(t.get("vehicle_no",""))+'</td>'
            +'<td '+td_s+'>'+str(t.get("driver_name",""))[:12]+'</td>'
            +'<td '+td_s+'>'+str(t.get("route_name",""))[:14]+'</td>'
            +'<td '+td_s+'>'+str(t.get("source_name",""))[:14]+'</td>'
            +'<td '+td_s+'>'+str(t.get("destination_name",""))[:14]+'</td>'
            +'<td '+td_s+'>'+str(t.get("run_date",""))[:16]+'</td>'
            +'<td '+td_s+'>'+str(t.get("shipment_method",""))+'</td>'
            +'<td '+td_s+'>'+ata+'</td>'
            +'<td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:'+gps_col+';font-weight:500;font-size:12px">'+gps+'</td>'
            +'</tr>'
        )
    if not trips:
        r_html='<tr><td colspan="11" style="text-align:center;padding:16px;color:#9ca3af">No trips found</td></tr>'
    bg_color="#f0fdf4" if label=="CLOSED" else "#fef2f2" if label=="CANCELLED" else "#eff6ff"
    val_color="#16a34a" if label=="CLOSED" else "#dc2626" if label=="CANCELLED" else "#2563eb"
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:960px">'
        '<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#6b7280;margin-bottom:12px">'+label+' Trips — '+period+'</div>'
        '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px">'
        '<div style="background:'+bg_color+';border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Total '+label+' Trips</div><div style="font-size:28px;font-weight:600;color:'+val_color+'">'+str(count)+'</div><div style="font-size:11px;color:#9ca3af;margin-top:2px">trip_status = '+label+'</div></div>'
        '<div style="background:#f9fafb;border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Period</div><div style="font-size:13px;font-weight:500;color:#111827;margin-top:4px">'+period+'</div></div>'
        '</div>'
        '<div style="overflow-x:auto;border-radius:10px;border:1px solid #e5e7eb">'
        '<table style="width:100%;border-collapse:collapse;min-width:700px">'
        '<thead><tr>'+h_html+'</tr></thead><tbody>'+r_html+'</tbody>'
        '</table></div>'
        '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing '+str(min(20,len(trips)))+' of '+str(count)+' · '+period+'</div>'
        '</div>'
    )


# ─── GPT Context Trimmer ──────────────────────────────────────────────────────

def trim_for_gpt(data: dict, max_items: int = 10) -> dict:
    result = {}
    for k,v in data.items():
        if k in ("_meta","_mongo_hint","_hours","_less","_total"): continue
        if k == "vehicles" and isinstance(v,list):
            result["vehicles_top10"]=v[:max_items]; result["vehicles_total"]=len(v)
        elif k in ("data","trips","records") and isinstance(v,list):
            result[k+"_top10"]=v[:max_items]; result[k+"_total"]=len(v)
        elif isinstance(v,dict) and len(str(v))>1000:
            result[k]={dk:dv for dk,dv in v.items() if len(str(dv))<200}
        elif isinstance(v,list) and len(v)>15: result[k]=v[:15]
        else: result[k]=v
    return result


# ─── Main Service ─────────────────────────────────────────────────────────────

class ChatService:
    def __init__(self):
        self.aggregator = BluedartAggregator()
        self.detector   = IntentDetector()
        self.ai         = OpenAIClient()

    async def process(self, request: ChatRequest) -> ChatResponse:
        start     = time.time()
        msg       = request.message
        msg_lower = msg.lower()
        history   = [m.dict() for m in (request.history or [])]

        intent = self.detector.classify_intent(msg)
        ids    = self.detector.extract_ids(msg)

        # ── Context: merge IDs from history ───────────────────────────────────
        context_used = False
        if history:
            if should_use_history(msg, ids):
                history_ids  = extract_ids_from_history(history)
                context_used = True
                for k,v in history_ids.items():
                    if not ids.get(k) and v:
                        ids[k] = v
                print(f"   ctx_ids: shp={ids.get('shipment_no')} veh={ids.get('vehicle_no')}")

        # ── Current message IDs (for routing decisions) ───────────────────────
        cur_shp = re.search(r'\b(9\d{14}|\d{7,9})\b', msg)
        cur_veh = re.search(r'\b([A-Z]{2}\d{2}[A-Z]{1,3}\d{4})\b', msg.upper())

        # ── Detect query modes ────────────────────────────────────────────────
        is_full      = wants_full_list(msg)
        severity     = detect_severity(msg)
        is_stopped   = any(kw in msg_lower for kw in STOPPED_TRIGGERS)
        is_loc, loc  = detect_location_query(msg)
        if ids.get("shipment_no"): is_loc = False

        has_alert    = msg_has_alert(msg)
        has_delay    = msg_has_delay(msg)
        has_investig = msg_has_investigate(msg)

        is_report = (
            any(kw in msg_lower for kw in REPORT_TRIGGERS) and
            not ids.get("shipment_no") and not is_stopped and
            not is_loc and not has_alert and not has_delay
        )

        is_dynamic_filter = (
            any(kw in msg_lower for kw in DYNAMIC_FILTER_TRIGGERS) and
            not ids.get("shipment_no") and
            not ids.get("vehicle_no") and      # specific vehicle diya ho to dynamic filter mat chalao
            not is_stopped and not is_loc and
            not has_alert and not has_delay and not has_investig
        )

        # Vehicle locate: ONLY if vehicle in CURRENT message, NO shipment anywhere, NO alert/delay keywords
        is_vehicle_locate = (
            bool(cur_veh) and
            not bool(cur_shp) and
            not ids.get("shipment_no") and
            not is_stopped and not is_loc and
            not has_alert and not has_delay and not has_investig and
            intent not in (Intent.ALERT_QUERY, Intent.DELAY_QUERY, Intent.ISSUE_RESOLUTION)
        )

        print(f"\n📨 {msg}")
        print(f"   intent={intent} ctx={context_used} alert={has_alert} delay={has_delay} invest={has_investig} veh_locate={is_vehicle_locate}")

        t1 = time.time()
        query_type       = "GENERAL"
        mongo_collection = ""
        mongo_query      = ""
        data             = {}

        # ── 1. STOPPED VEHICLES ───────────────────────────────────────────────
        if is_stopped or (is_full and context_used and not is_loc):
            query_type = "STOPPED"
            query_msg  = msg if is_stopped else next(
                (m["content"] for m in reversed(history) if any(kw in m.get("content","").lower() for kw in STOPPED_TRIGGERS)), msg)
            data = await fetch_stopped_vehicles(query_msg)
            hint = data.get("_mongo_hint",{})
            mongo_collection = hint.get("collection","trip_dashboard_live_status")
            mongo_query      = hint.get("query","")
            sev  = severity or (detect_severity(" ".join(m.get("content","") for m in history)) if context_used else None)
            reply = format_stopped_vehicles(data, show_all=is_full, severity_filter=sev)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="trip_dashboard_live_status",status="ok",data_keys=["vehicles","summary"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=mongo_query, mongo_collection=mongo_collection,
                context_used=context_used, session_id=request.session_id,
            )

        # ── 2. LOCATION QUERY ─────────────────────────────────────────────────
        elif is_loc and loc:
            query_type = "LOCATION"
            trip_status_filter = 0 if any(w in msg_lower for w in ["inactive","closed"]) else 1
            date_range_filter  = "last_month" if "last month" in msg_lower else "last_week"
            data = await fetch_location_trips(loc, trip_status_filter, date_range_filter)
            hint = data.get("_mongo_hint",{})
            mongo_collection = hint.get("collection","courier_trip_detail")
            mongo_query      = hint.get("query","")
            reply = format_location_trips(data)
            if is_full and "trips" in data:
                all_trips = data["trips"]
                from app.core.response_formatter import TABLE_HEADER, TABLE_DIVIDER, build_row, get_gps_status
                lines = [f"**All {data.get('_total',0)} trips from {loc}**", TABLE_HEADER, TABLE_DIVIDER]
                for i,t in enumerate(all_trips,1):
                    exc = t.get("exception_common_backend","")
                    lines.append(build_row(i,t.get("shipment_no",""),t.get("vehicle_no",""),
                        t.get("driver_name",""),(t.get("route_name") or "")[:20],
                        (t.get("run_date") or "")[:16],(t.get("destination_name") or "")[:20],
                        "-","N/A","N/A",(t.get("source_name") or "")[:35],get_gps_status(exc)))
                lines.append(f"\nAll {len(all_trips)} records shown.")
                reply = "\n".join(lines)
            ft = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["trips"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=mongo_query, mongo_collection=mongo_collection,
                context_used=context_used, session_id=request.session_id,
            )

        # ── 3. INVESTIGATE: delay + alert dono ───────────────────────────────
        elif has_investig:
            query_type = "GENERAL"
            shp = ids.get("shipment_no")
            veh = ids.get("vehicle_no")
            print(f"   mode: INVESTIGATE shp={shp} veh={veh}")
            delays_data, alerts_data = await _asyncio.gather(
                self.aggregator.get_trip_delays(shipment_no=shp),
                fetch_alerts_direct(shipment_no=shp) if shp else fetch_alerts_direct(vehicle_no=veh),
                # fetch_alerts_direct(shipment_no=shp, vehicle_no=veh),
            )
            reply = format_delays(delays_data) + "\n\n" + format_alerts(alerts_data)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[
                    ServiceCallInfo(service="courier_route_delay",status="ok",data_keys=["delays"]),
                    ServiceCallInfo(service="logistic_trigger_log",status="ok",data_keys=["trigger_logs"]),
                ],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query="", mongo_collection="",
                context_used=context_used, session_id=request.session_id,
            )

        # ── 4. ALERT QUERY ────────────────────────────────────────────────────
        elif has_alert:
            query_type = "ALERT"
            veh = ids.get("vehicle_no")
            shp = ids.get("shipment_no")
            print(f"   mode: ALERT veh={veh} shp={shp}")
            data  = await fetch_alerts_direct(vehicle_no=veh, shipment_no=shp)
            if shp:
                data = await fetch_alerts_direct(shipment_no=shp)   # shipment priority
            else:
                data = await fetch_alerts_direct(vehicle_no=veh)
            hint  = data.get("_mongo_hint",{})
            mongo_collection = hint.get("collection","logistic_trigger_log")
            mongo_query      = hint.get("query","")
            reply = format_alerts(data)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="logistic_trigger_log",status="ok",data_keys=["trigger_logs","summary"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=mongo_query, mongo_collection=mongo_collection,
                context_used=context_used, session_id=request.session_id,
            )

        # ── 5. DELAY QUERY ────────────────────────────────────────────────────
        elif has_delay:
            query_type = "DELAY"
            shp = ids.get("shipment_no")
            print(f"   mode: DELAY shp={shp}")
            data  = await self.aggregator.get_trip_delays(shipment_no=shp)
            reply = format_delays(data)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="courier_route_delay",status="ok",data_keys=["delays"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=f'db.courier_route_delay.find({{"trip_id":"{shp}","group_id":"0041","status":1}}).sort({{"entry_date":-1}}).limit(20)',
                mongo_collection="courier_route_delay",
                context_used=context_used, session_id=request.session_id,
            )

        # ── 6. VEHICLE LOCATE ─────────────────────────────────────────────────
        elif is_vehicle_locate:
            query_type = "TRIP"
            veh = ids.get("vehicle_no")
            print(f"   mode: VEHICLE LOCATE {veh}")
            data = await fetch_vehicle_location(veh)
            hint = data.get("_mongo_hint",{})
            mongo_collection = hint.get("collection","courier_trip_detail")
            mongo_query      = hint.get("query","")
            if "error" in data and "trip_core" not in data:
                reply = data.get("error","No active trip found for "+str(veh))
            else:
                reply = format_trip_status(data)
            ft = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["trip_detail","live_status"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=mongo_query, mongo_collection=mongo_collection,
                context_used=context_used, session_id=request.session_id,
            )

        # ── 7. MAX TRIPS ANALYTICS ────────────────────────────────────────────
        elif any(kw in msg_lower for kw in ANALYTICS_TRIGGERS):
            query_type = "BULK"
            print("   mode: MAX TRIPS ANALYTICS")
            data  = await fetch_max_trips_vehicle()
            reply = format_max_trips(data)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["top_vehicles"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=data.get("_mongo_hint",{}).get("query",""),
                mongo_collection="courier_trip_detail",
                context_used=context_used, session_id=request.session_id,
            )

        # ── 8. RUNNING TRIPS ──────────────────────────────────────────────────
        elif any(kw in msg_lower for kw in RUNNING_TRIGGERS):
            query_type = "BULK"
            print("   mode: RUNNING TRIPS")
            data  = await fetch_running_trips(limit=20)
            reply = format_bulk_report(data, query_msg=msg)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["trips"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=data.get("_mongo_hint",{}).get("query",""),
                mongo_collection="courier_trip_detail",
                context_used=context_used, session_id=request.session_id,
            )

        # ── 9. TRANSSHIPMENT ──────────────────────────────────────────────────
        elif any(kw in msg_lower for kw in TRANSSHIP_TRIGGERS):
            query_type = "BULK"
            print("   mode: TRANSSHIPMENT")
            data  = await fetch_transshipment(msg)
            reply = _build_transship_html(data)
            hint  = data.get("_mongo_hint",{})
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["transshipment_id","transshipment_date"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=hint.get("query",""), mongo_collection="courier_trip_detail",
                context_used=context_used, session_id=request.session_id,
            )

        # ── 10. CLOSE TRIPS ───────────────────────────────────────────────────
        elif any(kw in msg_lower for kw in CLOSE_TRIP_TRIGGERS):
            query_type = "BULK"
            print("   mode: CLOSE TRIPS")
            data  = await fetch_trips_by_status(msg, 0)
            reply = _build_close_trips_html(data)
            hint  = data.get("_mongo_hint",{})
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["data","total_found"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=hint.get("query",""), mongo_collection="courier_trip_detail",
                context_used=context_used, session_id=request.session_id,
            )

        # ── 11. DYNAMIC FILTER QUERY ──────────────────────────────────────────
        elif is_dynamic_filter:
            query_type = "BULK"
            print("   mode: DYNAMIC FILTER")
            date_from, date_to = parse_date_range(msg)
            data  = await dynamic_trip_query(msg, date_from, date_to)
            hint  = data.get("_mongo_hint", {})
            mongo_collection = hint.get("collection", "courier_trip_detail")
            mongo_query      = hint.get("query", "")
            reply = format_bulk_report(data, query_msg=msg)
            ft    = round(time.time() - t1, 2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k: v for k, v in ids.items() if v},
                services_called=[ServiceCallInfo(
                    service="courier_trip_detail", status="ok",
                    data_keys=["data", "total_found", "shipment_methods", "gps_summary"],
                )],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time() - start, 2),
                mongo_query=mongo_query, mongo_collection=mongo_collection,
                context_used=context_used, session_id=request.session_id,
            )

        # ── 12. BULK REPORT ───────────────────────────────────────────────────
        elif is_report:
            query_type = "BULK"
            print("   mode: BULK REPORT")
            data = await fetch_bulk_report(msg)
            hint = data.get("_mongo_hint",{})
            mongo_collection = hint.get("collection","courier_trip_detail")
            mongo_query      = hint.get("query","")

            if is_full:
                all_data = data.get("data",[])
                from app.core.response_formatter import TABLE_HEADER, TABLE_DIVIDER, build_row, get_gps_status
                lines = [f"**All {data.get('total_found',0)} trips**", TABLE_HEADER, TABLE_DIVIDER]
                for i,t in enumerate(all_data,1):
                    exc = t.get("exception_common_backend","")
                    lines.append(build_row(i,t.get("shipment_no",""),t.get("vehicle_no",""),
                        t.get("driver_name",""),(t.get("route_name") or "")[:20],
                        (t.get("run_date") or "")[:16],(t.get("destination_name") or "")[:20],
                        "-","N/A","N/A",(t.get("source_name") or "")[:35],f"GPS:{get_gps_status(exc)}"))
                lines.append(f"\nAll {len(all_data)} records shown.")
                reply = "\n".join(lines)
                ft = round(time.time()-t1,2)
                return ChatResponse(
                    reply=reply, intent=intent, query_type=query_type,
                    extracted_ids={k:v for k,v in ids.items() if v},
                    services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["data"])],
                    fetch_time_seconds=ft, ai_time_seconds=0.0,
                    total_time_seconds=round(time.time()-start,2),
                    mongo_query=mongo_query, mongo_collection=mongo_collection,
                    context_used=context_used, session_id=request.session_id,
                )

            reply = format_bulk_report(data, query_msg=msg)
            ft    = round(time.time()-t1,2)
            return ChatResponse(
                reply=reply, intent=intent, query_type=query_type,
                extracted_ids={k:v for k,v in ids.items() if v},
                services_called=[ServiceCallInfo(service="courier_trip_detail",status="ok",data_keys=["data"])],
                fetch_time_seconds=ft, ai_time_seconds=0.0,
                total_time_seconds=round(time.time()-start,2),
                mongo_query=mongo_query, mongo_collection=mongo_collection,
                context_used=context_used, session_id=request.session_id,
            )

        # ── 12. SPECIFIC QUERIES → GPT ────────────────────────────────────────
        else:
            if intent == Intent.ALERT_QUERY:
                query_type = "ALERT"
                # data  = await fetch_alerts_direct(vehicle_no=ids.get("vehicle_no"), shipment_no=ids.get("shipment_no"))
                shp = ids.get("shipment_no")
                veh = ids.get("vehicle_no")
                if shp:
                    data = await fetch_alerts_direct(shipment_no=shp)
                else:
                    data = await fetch_alerts_direct(vehicle_no=veh)
                reply = format_alerts(data)
                ft    = round(time.time()-t1,2)
                return ChatResponse(
                    reply=reply, intent=intent, query_type=query_type,
                    extracted_ids={k:v for k,v in ids.items() if v},
                    services_called=[ServiceCallInfo(service="logistic_trigger_log",status="ok",data_keys=["trigger_logs"])],
                    fetch_time_seconds=ft, ai_time_seconds=0.0,
                    total_time_seconds=round(time.time()-start,2),
                    mongo_query="", mongo_collection="logistic_trigger_log",
                    context_used=context_used, session_id=request.session_id,
                )

            elif intent == Intent.DELAY_QUERY:
                query_type = "DELAY"
                shp  = ids.get("shipment_no")
                data = await self.aggregator.get_trip_delays(shipment_no=shp)
                reply = format_delays(data)
                ft    = round(time.time()-t1,2)
                return ChatResponse(
                    reply=reply, intent=intent, query_type=query_type,
                    extracted_ids={k:v for k,v in ids.items() if v},
                    services_called=[ServiceCallInfo(service="courier_route_delay",status="ok",data_keys=["delays"])],
                    fetch_time_seconds=ft, ai_time_seconds=0.0,
                    total_time_seconds=round(time.time()-start,2),
                    mongo_query="", mongo_collection="courier_route_delay",
                    context_used=context_used, session_id=request.session_id,
                )

            elif intent == Intent.STOPS_QUERY or any(
                kw in msg_lower for kw in
                ["delivery stops","waypoint","kitne stop","pod","poa",
                 "stop details","delivery points","show stops","stops for"]
            ):
                query_type = "GENERAL"
                shp = ids.get("shipment_no")
                print(f"   mode: STOPS shp={shp}")
                if not shp:
                    reply = "Please provide a shipment number to show delivery stops."
                    ft    = round(time.time()-t1,2)
                    return ChatResponse(reply=reply, intent=intent, query_type=query_type,
                        extracted_ids={k:v for k,v in ids.items() if v}, services_called=[],
                        fetch_time_seconds=ft, ai_time_seconds=0.0,
                        total_time_seconds=round(time.time()-start,2),
                        mongo_query="", mongo_collection="",
                        context_used=context_used, session_id=request.session_id)

                core = await self.aggregator.get_trip_by_shipment(shp)
                trip = core.get("trip_detail", {})
                if isinstance(trip, list): trip = trip[0] if trip else {}

                vehicle_no = str(trip.get("vehicle_no", "")) if isinstance(trip, dict) else ""
                run_date   = str(trip.get("run_date",   "")) if isinstance(trip, dict) else ""

                print(f"   stops: vehicle={vehicle_no} run_date={run_date[:10]}")

                if not vehicle_no:
                    reply = f"Vehicle info not found for shipment {shp}."
                    ft = round(time.time()-t1, 2)
                    return ChatResponse(
                        reply=reply, intent=intent, query_type=query_type,
                        extracted_ids={k:v for k,v in ids.items() if v}, services_called=[],
                        fetch_time_seconds=ft, ai_time_seconds=0.0,
                        total_time_seconds=round(time.time()-start,2),
                        mongo_query="", mongo_collection="",
                        context_used=context_used, session_id=request.session_id,
                    )

                # stops_data = await self.aggregator.get_trip_stops(
                #     vehicle_no=vehicle_no,
                #     run_date=run_date,
                # )
                stops_data = await self.aggregator.get_trip_stops(
                    vehicle_no = vehicle_no,
                )
                stops      = stops_data.get("stops_detail",[])
                total_s    = stops_data.get("total_stops",0)
                completed  = stops_data.get("completed_pod",0)
                pending    = stops_data.get("pending_stops",0)

                th_s  = 'style="padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:#fff;background:#1e3a5f;white-space:nowrap"'
                td_s  = 'style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#374151;font-size:12px"'
                cols  = ["Seq","Location","Scheduled Arrival","Scheduled Departure","POD Status","POA Status"]
                h_html= "".join('<th '+th_s+'>'+c+'</th>' for c in cols)
                r_html= ""
                for s in stops:
                    pod=s.get("pod_status",0); pod_txt="✅ Done" if pod==1 else "⏳ Pending"; pod_col="#16a34a" if pod==1 else "#d97706"
                    poa=s.get("poa_status",0); poa_txt="✅ Done" if poa==1 else "⏳ Pending"
                    r_html += (
                        '<tr>'
                        '<td '+td_s+'>'+str(s.get("location_sequence",""))+'</td>'
                        '<td '+td_s+'>'+str(s.get("location_name",""))+'</td>'
                        '<td '+td_s+'>'+str(s.get("schedule_time_arrival",""))[:16]+'</td>'
                        '<td '+td_s+'>'+str(s.get("schedule_time_departure",""))[:16]+'</td>'
                        '<td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:'+pod_col+';font-size:12px;font-weight:500">'+pod_txt+'</td>'
                        '<td '+td_s+'>'+poa_txt+'</td>'
                        '</tr>'
                    )
                if not stops:
                    r_html='<tr><td colspan="6" style="text-align:center;padding:16px;color:#9ca3af">No delivery stops found</td></tr>'

                reply = (
                    '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:800px">'
                    '<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#6b7280;margin-bottom:12px">Delivery Stops — Shipment '+str(shp)+'</div>'
                    '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px">'
                    '<div style="background:#f9fafb;border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Total Stops</div><div style="font-size:24px;font-weight:600;color:#111827">'+str(total_s)+'</div></div>'
                    '<div style="background:#f0fdf4;border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Completed</div><div style="font-size:24px;font-weight:600;color:#16a34a">'+str(completed)+'</div></div>'
                    '<div style="background:#fffbeb;border-radius:11px;padding:12px 14px"><div style="font-size:11px;color:#6b7280;margin-bottom:3px">Pending</div><div style="font-size:24px;font-weight:600;color:#d97706">'+str(pending)+'</div></div>'
                    '</div>'
                    '<div style="overflow-x:auto;border-radius:10px;border:1px solid #e5e7eb">'
                    '<table style="width:100%;border-collapse:collapse;min-width:500px">'
                    '<thead><tr>'+h_html+'</tr></thead><tbody>'+r_html+'</tbody>'
                    '</table></div></div>'
                )
                ft = round(time.time()-t1,2)
                return ChatResponse(
                    reply=reply, intent=intent, query_type=query_type,
                    extracted_ids={k:v for k,v in ids.items() if v},
                    services_called=[ServiceCallInfo(service="courier_trip_detail_customer",status="ok",data_keys=["stops_detail"])],
                    fetch_time_seconds=ft, ai_time_seconds=0.0,
                    total_time_seconds=round(time.time()-start,2),
                    # mongo_query=f'db.courier_trip_detail_customer.find({{"vehicle_no_prm":"{vehicle_no}","run_date_prm":{"$gte":"{run_date[:10]} 00:00:00","$lte":"{run_date[:10]} 23:59:59"},"group_id":"0041","status":1}}).sort({{"location_sequence":1}})',
                    mongo_collection="courier_trip_detail_customer",
                    context_used=context_used, session_id=request.session_id,
                )

            elif intent in (Intent.STATUS_CHECK, Intent.LOCATE, Intent.ETA_QUERY):
                query_type = "TRIP"
                data = await self.aggregator.fetch_for_query(intent=intent, ids=ids)
            else:
                query_type = "GENERAL"
                data = await self.aggregator.fetch_for_query(intent=intent, ids=ids)

        fetch_time   = round(time.time()-t1,2)
        direct_reply = format_response(data, query_type, query_msg=msg)

        if direct_reply:
            hint        = data.get("_mongo_hint",{})
            mongo_query = hint.get("query","")
            reply       = direct_reply
            ai_time     = 0.0
        else:
            gpt_data = trim_for_gpt(data)
            t2       = time.time()
            full_text_reply, collection, ai_mongo = await self.ai.analyze(
                user_query=msg, context_data=gpt_data, history=history,
                intent=intent, extracted_ids={k:v for k,v in ids.items() if v},
            )
            ai_time          = round(time.time()-t2,2)
            reply            = full_text_reply
            mongo_collection = collection
            mongo_query      = ai_mongo

        total    = round(time.time()-start,2)
        print(f"   fetch:{fetch_time}s ai:{ai_time}s total:{total}s")

        services = [
            ServiceCallInfo(service=k,
                           status="error" if isinstance(v,dict) and "error" in v else "ok",
                           data_keys=list(v.keys()) if isinstance(v,dict) else [])
            for k,v in data.items()
            if k not in ("_meta","_mongo_hint","_hours","_less","_total")
        ]

        return ChatResponse(
            reply=reply, intent=intent, query_type=query_type,
            extracted_ids={k:v for k,v in ids.items() if v},
            services_called=services,
            fetch_time_seconds=fetch_time, ai_time_seconds=ai_time,
            total_time_seconds=total,
            mongo_query=mongo_query, mongo_collection=mongo_collection,
            context_used=context_used, session_id=request.session_id,
        )