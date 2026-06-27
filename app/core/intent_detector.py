"""
app/core/intent_detector.py
============================
Bluedart-specific intent detection + ID extraction.

Detects:
  - Which intent (STATUS_CHECK, LOCATE, DELAY_QUERY, ALERT_QUERY etc.)
  - Which IDs are present in the message
    (shipment_no, vehicle_no, imei, m_trip_id, customer_id)

All ID patterns match Bluedart's real formats from secutrakdb.
"""

import re
from typing import Dict, Optional, Set


# ─────────────────────────────────────────────────────────────────────────────
# INTENT TYPES
# ─────────────────────────────────────────────────────────────────────────────

class Intent:
    STATUS_CHECK    = "STATUS_CHECK"     # shipment kahan hai, kya status
    LOCATE          = "LOCATE"           # live GPS, current location
    ETA_QUERY       = "ETA_QUERY"        # kab pahunchega, ETA
    DELAY_QUERY     = "DELAY_QUERY"      # delay kyu hua, kitna delay
    ALERT_QUERY     = "ALERT_QUERY"      # koi alert hai, S180, halt
    STOPS_QUERY     = "STOPS_QUERY"      # kitne stops, delivery status
    ISSUE_RESOLUTION = "ISSUE_RESOLUTION" # problem solve karo
    GPS_QUERY       = "GPS_QUERY"        # vehicle GPS, speed, location
    IMEI_QUERY      = "IMEI_QUERY"       # specific IMEI data
    CUSTOMER_QUERY  = "CUSTOMER_QUERY"   # customer ki routes
    ANALYTICS       = "ANALYTICS"        # count, summary, report
    GENERAL_QUERY   = "GENERAL_QUERY"    # fallback


# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD MAPS
# ─────────────────────────────────────────────────────────────────────────────

INTENT_KEYWORDS = {
    Intent.LOCATE: [
        "location", "kahan", "where", "gps", "live", "current position",
        "geocoord", "latitude", "longitude", "map", "coordinates",
        "abhi kahan", "right now"
    ],
    Intent.ETA_QUERY: [
        "eta", "kab pahunchega", "kab aayega", "when will", "arrival time",
        "estimated", "reach", "pahunche", "kitne time", "how long",
        "eta_hrs", "schedule arrival"
    ],
    Intent.DELAY_QUERY: [
        "delay", "late", "der", "delayed", "hold", "stuck",
        "total_delay", "delay_reason", "kyu ruka", "why stopped",
        "delay report", "kitna late", "how late"
    ],
    Intent.ALERT_QUERY: [
        "alert", "trigger", "S180", "halt", "unscheduled", "speeding",
        "voilation", "violation", "flag", "critical", "qrt", "threat",
        "alarm", "warning", "koi alert", "any alert"
    ],
    Intent.STOPS_QUERY: [
        "stop", "waypoint", "delivery point", "pod", "poa",
        "kitne stop", "how many stops", "sequence", "checkpoint",
        "location sequence", "delivered", "pending delivery"
    ],
    Intent.ISSUE_RESOLUTION: [
        "problem", "issue", "solve", "help", "fix", "check karo",
        "kya hua", "what happened", "investigate", "reason",
        "why", "kyu", "nahin pahuncha", "not delivered", "missing"
    ],
    Intent.GPS_QUERY: [
        "vehicle", "gaadi", "truck", "van", "speed", "ignition",
        "imei", "device", "Vehicle_wise", "lastdata", "gps data"
    ],
    Intent.IMEI_QUERY: [
        "imei", "device id", "tracker", "device data", "bluedart_lastdata"
    ],
    Intent.CUSTOMER_QUERY: [
        "customer", "customer_id", "client route", "customer route",
        "mapped route", "assigned route"
    ],
    Intent.ANALYTICS: [
        "kitne", "how many", "count", "total", "summary", "report",
        "list all", "sabhi", "all trips", "statistics", "stat"
    ],
    Intent.STATUS_CHECK: [
        "status", "kya hal", "update", "shipment", "trip",
        "consignment", "parcel", "package", "dispatch"
    ],
}

# Intent priority order (more specific first)
INTENT_PRIORITY = [
    Intent.ISSUE_RESOLUTION,
    Intent.ALERT_QUERY,
    Intent.DELAY_QUERY,
    Intent.STOPS_QUERY,
    Intent.LOCATE,
    Intent.ETA_QUERY,
    Intent.GPS_QUERY,
    Intent.IMEI_QUERY,
    Intent.CUSTOMER_QUERY,
    Intent.ANALYTICS,
    Intent.STATUS_CHECK,
    Intent.GENERAL_QUERY,
]


# ─────────────────────────────────────────────────────────────────────────────
# ID PATTERNS — Bluedart real formats from secutrakdb
# ─────────────────────────────────────────────────────────────────────────────

ID_PATTERNS = {
    # shipment_no: 8-digit numeric (e.g. 11464086) OR 15-digit (e.g. 900001057416549)
    "shipment_no": r"\b(9\d{14}|\d{7,9})\b",

    # vehicle_no: Indian registration format (e.g. KL24G3501, HR55AJ9358, DL01MB4349)
    "vehicle_no": r"\b([A-Z]{2}\d{2}[A-Z]{1,2}\d{4})\b",

    # imei: 15-digit IMEI number
    "imei": r"\b(\d{15})\b",

    # customer_id: 5-digit numeric (e.g. 45624)
    "customer_id": r"\b(customer[_\s]?id[:\s]+(\d{4,6})|\b(\d{5})\b)",

    # route_id: 6-7 digit numeric (e.g. 974722, 1118293)
    "route_id": r"\b(\d{6,7})\b",
}

# GPT prompt guidance per intent
INTENT_INSTRUCTIONS = {
    Intent.STATUS_CHECK:     "Give current trip status clearly. Include source, destination, current location, trip_status code meaning.",
    Intent.LOCATE:           "Focus on GPS coordinates, last known location address, speed, and when GPS was last updated.",
    Intent.ETA_QUERY:        "State ETA clearly with date and time. Mention delay_hr if any. State confidence based on vehicle status.",
    Intent.DELAY_QUERY:      "List all delay incidents with reason descriptions. Sum total delay minutes. Identify if delay is ongoing.",
    Intent.ALERT_QUERY:      "List all active alerts by type. Highlight critical (level 1) alerts first. Include location and duration of violation.",
    Intent.STOPS_QUERY:      "Show all stops in sequence. Indicate completed (pod_status=1) vs pending. Mention schedule vs actual times.",
    Intent.ISSUE_RESOLUTION: "Analyze all available data to identify root cause. Give specific actionable recommendation.",
    Intent.GPS_QUERY:        "Show current GPS data: coordinates, speed, ignition state, signal strength, last update time.",
    Intent.IMEI_QUERY:       "Show raw device data for the IMEI: coordinates, speed, firmware version, last communication time.",
    Intent.CUSTOMER_QUERY:   "List routes mapped to this customer with route_id and status.",
    Intent.ANALYTICS:        "Provide count/summary with numbers. Compare where relevant.",
    Intent.GENERAL_QUERY:    "Provide comprehensive answer using all available data.",
}


# ─────────────────────────────────────────────────────────────────────────────
# DETECTOR CLASS
# ─────────────────────────────────────────────────────────────────────────────

class IntentDetector:

    def classify_intent(self, message: str) -> str:
        """Detect the primary intent from user message."""
        msg = message.lower()

        scores: Dict[str, int] = {}
        for intent, keywords in INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in msg)
            if score > 0:
                scores[intent] = score

        if not scores:
            return Intent.GENERAL_QUERY

        # Return highest scoring intent, respecting priority for ties
        for intent in INTENT_PRIORITY:
            if intent in scores:
                max_score = max(scores.values())
                if scores[intent] >= max_score:
                    return intent

        return max(scores, key=scores.get)

    def extract_ids(self, message: str) -> Dict[str, Optional[str]]:
        """
        Extract all Bluedart IDs from user message.

        Returns dict with None for IDs not found:
        {
          "shipment_no":  "11464086" or None,
          "vehicle_no":   "KL24G3501" or None,
          "imei":         "860906047378283" or None,
          "customer_id":  "45624" or None,
          "route_id":     "974722" or None,
          "m_trip_id":    None (can't extract ObjectId from natural language)
        }
        """
        extracted: Dict[str, Optional[str]] = {
            "shipment_no": None,
            "vehicle_no":  None,
            "imei":        None,
            "customer_id": None,
            "route_id":    None,
            "m_trip_id":   None,   # ObjectId — fetched from DB, not user input
        }

        msg_upper = message.upper()

        # Vehicle number — Indian registration (e.g. HR55AJ9358)
        vehicle_match = re.search(
            r"\b([A-Z]{2}\d{2}[A-Z]{1,3}\d{4})\b", msg_upper
        )
        if vehicle_match:
            extracted["vehicle_no"] = vehicle_match.group(1)

        # IMEI — 15 digits
        imei_match = re.search(r"\b(\d{15})\b", message)
        if imei_match:
            extracted["imei"] = imei_match.group(1)

        # Shipment number — 8-digit OR 15-digit starting with 9
        # Try 15-digit first (more specific)
        shp_long = re.search(r"\b(9\d{14})\b", message)
        if shp_long:
            extracted["shipment_no"] = shp_long.group(1)
        else:
            # 7-9 digit (but not already matched as IMEI)
            shp_short = re.search(r"\b(\d{7,9})\b", message)
            if shp_short and shp_short.group(1) != extracted.get("imei"):
                extracted["shipment_no"] = shp_short.group(1)

        # Customer ID — 5 digit numeric (only if keyword present)
        if any(kw in message.lower() for kw in ["customer", "client", "cust"]):
            cust_match = re.search(r"\b(\d{5})\b", message)
            if cust_match:
                extracted["customer_id"] = cust_match.group(1)

        # Route ID — 6-7 digit numeric
        if any(kw in message.lower() for kw in ["route", "route_id"]):
            route_match = re.search(r"\b(\d{6,7})\b", message)
            if route_match:
                extracted["route_id"] = route_match.group(1)

        return extracted

    def get_prompt_instruction(self, intent: str) -> str:
        """Returns GPT instruction string for this intent."""
        return INTENT_INSTRUCTIONS.get(intent, INTENT_INSTRUCTIONS[Intent.GENERAL_QUERY])
