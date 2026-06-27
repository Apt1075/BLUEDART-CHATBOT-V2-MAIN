"""
app/core/data_aggregator.py
============================
Bluedart — secutrakdb — 9 Collections
======================================

Collections handled:
  TRIP CORE:
    1. courier_trip_detail              → main trip table
    2. courier_trip_detail_customer     → waypoints / stops per trip

  LIVE TRACKING:
    3. trip_dashboard_live_status       → real-time ETA + GPS per trip
    4. Vehicle_wise_lastdata            → per-vehicle latest GPS snapshot
    5. bluedart_lastdata                → per-IMEI raw GPS feed

  ALERTS & TRIGGERS:
    6. logistic_trigger_log             → individual alert events
    7. bluedart_trigger_dashboard       → trip-level dashboard (alerts[] embedded)

  DELAYS:
    8. courier_route_delay              → delay incidents
    9. courier_route_delay_master       → lookup: reason_code → description (cached)

  CONFIG:
     courier_customer_route_bluedart   → customer to route mapping

RULES:
  - group_id = '0041' always applied (Bluedart tenant filter)
  - status   = 1      always applied (active records only)
  - No direct MongoDB connection — all data via your REST APIs
  - All API calls are concurrent (asyncio.gather)
  - nested arrays / objects flattened before sending to GPT
  - delay_reason_master cached at startup (small static table)
"""

import asyncio
import httpx
from typing import Any, Dict, List, Optional
from app.core.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GROUP_ID  = "0041"   # Bluedart tenant ID — every query uses this
STATUS_ON = 1        # active records only

# In-memory cache for delay master (small static lookup table)
_DELAY_REASON_CACHE: Dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: flatten last_data nested array structure
# ─────────────────────────────────────────────────────────────────────────────

def flatten_lastdata(raw: Any) -> Dict:
    """
    MongoDB stores GPS data as:
        { "latitudeLR": ["21.077769"], "speedLR": ["0"], ... }

    GPT needs clean flat dict:
        { "latitude": "21.077769", "speed": "0", ... }
    """
    if not raw or not isinstance(raw, dict):
        return {}

    def first(val):
        if isinstance(val, list):
            return val[0] if val else None
        return val

    return {
        "latitude":        first(raw.get("latitudeLR")),
        "longitude":       first(raw.get("longitudeLR")),
        "speed_kmh":       first(raw.get("speedLR")),
        "device_time":     first(raw.get("deviceDatetimeLR")),
        "server_time":     first(raw.get("serverDatetimeLR")),
        "message_type":    first(raw.get("messageTypeLR")),
        "last_halt_time":  first(raw.get("lastHaltTimeLR")),
        "day_max_speed":   first(raw.get("dayMaxSpeedLR")),
        "supply_voltage":  first(raw.get("suplyVoltageLR")),
        "signal_strength": first(raw.get("sigStrTLR")),
        "gps_fix":         first(raw.get("fixLR")),
        "firmware":        first(raw.get("versionLR")),
        "ignition":        first(raw.get("io2LR")),   # io2 = ignition on most devices
        "gps_vendor":      first(raw.get("io8LR")),   # io8 often has vendor name
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: summarize alerts[] embedded array
# ─────────────────────────────────────────────────────────────────────────────

def summarize_alerts(alerts: List[Dict]) -> Dict:
    """
    bluedart_trigger_dashboard.alerts[] can be very large.
    Summarize so GPT gets meaningful data without token overflow.
    """
    if not alerts:
        return {"total": 0, "types": [], "critical": [], "recent_3": []}

    # Critical = level 1
    critical = [
        {
            "alert_type":     a.get("alert_type"),
            "location":       a.get("location"),
            "geocoord":       a.get("geocoord"),
            "voilation_time": a.get("voilation_time"),
            "start_time":     a.get("start_time"),
            "end_time":       a.get("end_time"),
        }
        for a in alerts if str(a.get("level", "")) == "1"
    ]

    # Most recent 3 alerts
    recent = sorted(alerts, key=lambda x: x.get("create_date", ""), reverse=True)[:3]
    recent_clean = [
        {
            "alert_type":     a.get("alert_type"),
            "location":       a.get("location"),
            "voilation_time": a.get("voilation_time"),
            "create_date":    a.get("create_date"),
        }
        for a in recent
    ]

    return {
        "total":          len(alerts),
        "unique_types":   list({a.get("alert_type") for a in alerts if a.get("alert_type")}),
        "critical_count": len(critical),
        "critical":       critical[:5],
        "recent_3":       recent_clean,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────────────────────

class BluedartAggregator:
    """
    Fetches Bluedart logistics data from all 9 MongoDB collections
    via your existing REST APIs. No direct DB connection.
    """

    def __init__(self):
        self.timeout = httpx.Timeout(settings.SERVICE_TIMEOUT_SECONDS)

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 1 + 3: courier_trip_detail + trip_dashboard_live_status
    # Entry point: shipment_no
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_by_shipment(self, shipment_no: str) -> Dict:
        """
        Primary lookup for any shipment query.
        Hits both trip detail and live dashboard concurrently.

        MongoDB query equivalent:
          courier_trip_detail.find({ shipment_no, group_id:'0041', status:1 })
          trip_dashboard_live_status.find({ shipment_no, group_id:'0041', status:1, trip_status:1 })
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            trip_resp, live_resp = await asyncio.gather(
                client.get(
                    f"{settings.TRIP_SERVICE_URL}/trips",
                    params={
                        "shipment_no": shipment_no,
                        "group_id":    GROUP_ID,
                        "status":      STATUS_ON,
                    }
                ),
                client.get(
                    f"{settings.DASHBOARD_SERVICE_URL}/live-status",
                    params={
                        "shipment_no": shipment_no,
                        "group_id":    GROUP_ID,
                        "status":      STATUS_ON,
                        "trip_status": STATUS_ON,
                    }
                ),
                return_exceptions=True
            )

        trip = self._json(trip_resp, "courier_trip_detail")
        live = self._json(live_resp, "trip_dashboard_live_status")

        # Flatten nested GPS arrays in live status
        if isinstance(live, dict):
            if "last_data_current" in live:
                live["gps_current"] = flatten_lastdata(live.pop("last_data_current"))
            # Remove raw imei1/2/3 data blobs — keep only current
            for k in ["last_data1", "last_data2", "last_data3"]:
                live.pop(k, None)

        return {
            "trip_detail": trip,
            "live_status": live,
        }

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 2: courier_trip_detail_customer
    # Entry point: m_trip_id
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_stops(self, m_trip_id: str) -> Dict:
        """
        Ordered delivery stops / waypoints for a trip.

        MongoDB query equivalent:
          courier_trip_detail_customer.find({ m_trip_id, group_id:'0041', status:1 })
                                      .sort({ location_sequence: 1 })

        Key fields: location_sequence, pod_status (0=pending,1=done),
                    poa_status, schedule_time_arrival, location_name
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{settings.TRIP_SERVICE_URL}/trips/{m_trip_id}/stops",
                params={
                    "group_id": GROUP_ID,
                    "status":   STATUS_ON,
                    "sort":     "location_sequence:asc",
                }
            )
        stops = self._json(resp, "courier_trip_detail_customer")

        if isinstance(stops, list):
            return {
                "total_stops":   len(stops),
                "completed_pod": sum(1 for s in stops if s.get("pod_status") == 1),
                "pending_stops": sum(1 for s in stops if s.get("pod_status") == 0),
                "stops_detail":  stops,
            }
        return stops

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 6 + 7: logistic_trigger_log + bluedart_trigger_dashboard
    # Entry point: m_trip_id + shipment_no
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_alerts(self, m_trip_id: str, shipment_no: str) -> Dict:
        """
        All alerts for a trip from both alert collections (concurrent).

        Collection 6 — logistic_trigger_log:
          Individual alert events: S180, UNSCHEDULED_HALT, SPEEDING etc.
          Fields: alert_type, voilation_time, geocoord, primary_info{}

        Collection 7 — bluedart_trigger_dashboard:
          Per-trip dashboard with embedded alerts[] array.
          Fields: flag_critical, qrt_assigned, alerts[].alert_type
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            log_resp, dash_resp = await asyncio.gather(
                client.get(
                    f"{settings.ALERT_SERVICE_URL}/trigger-logs",
                    params={
                        "m_trip_id": m_trip_id,
                        "group_id":  GROUP_ID,
                        "status":    STATUS_ON,
                        "sort":      "create_date:desc",
                        "limit":     20,
                    }
                ),
                client.get(
                    f"{settings.ALERT_SERVICE_URL}/trigger-dashboard",
                    params={
                        "m_trip_id": m_trip_id,
                        "group_id":  GROUP_ID,
                        "status":    STATUS_ON,
                    }
                ),
                return_exceptions=True
            )

        trigger_logs  = self._json(log_resp,  "logistic_trigger_log")
        trigger_dash  = self._json(dash_resp, "bluedart_trigger_dashboard")

        # Summarize embedded alerts[] before sending to GPT
        if isinstance(trigger_dash, dict) and "alerts" in trigger_dash:
            trigger_dash["alerts_summary"] = summarize_alerts(
                trigger_dash.pop("alerts", [])
            )

        return {
            "trigger_logs":      trigger_logs,
            "trigger_dashboard": trigger_dash,
        }

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 8 + 9: courier_route_delay + courier_route_delay_master
    # Entry point: m_trip_id
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_delays(self, m_trip_id: str) -> Dict:
        """
        Delay incidents for a trip, reason codes joined from cached master.

        Collection 8 — courier_route_delay:
          Fields: delay_reason(code), delay_seq, incident_date,
                  total_delay_in_min, enroute_code, location_name

        Collection 9 — courier_route_delay_master (cached in memory):
          Fields: c_reason_code → creason_desc
          Joined locally — no extra API call per query.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{settings.TRIP_SERVICE_URL}/trips/{m_trip_id}/delays",
                params={
                    "group_id": GROUP_ID,
                    "status":   STATUS_ON,
                    "sort":     "entry_date:desc",
                }
            )
        delays = self._json(resp, "courier_route_delay")

        if isinstance(delays, list):
            total_mins = 0
            for d in delays:
                # Join reason code → description from cache
                code = str(d.get("delay_reason", ""))
                d["delay_reason_desc"] = _DELAY_REASON_CACHE.get(
                    code, f"Code {code} (not in master)"
                )
                try:
                    total_mins += int(d.get("total_delay_in_min") or 0)
                except (ValueError, TypeError):
                    pass

            return {
                "total_delay_incidents": len(delays),
                "total_delay_minutes":   total_mins,
                "total_delay_hours":     round(total_mins / 60, 1),
                "delays":                delays,
            }
        return delays

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 4: Vehicle_wise_lastdata
    # Entry point: vehicle_no
    # ──────────────────────────────────────────────────────────────────────

    async def get_vehicle_lastdata(self, vehicle_no: str) -> Dict:
        """
        Latest GPS snapshot for a vehicle across all its IMEIs.

        MongoDB query equivalent:
          Vehicle_wise_lastdata.find({ vehicle_number, status:1 })

        Key structure: imeis{ imei1{ lastdata{} }, imei2{...} }
        Flattens nested imeis{} for GPT readability.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{settings.VEHICLE_SERVICE_URL}/vehicles/{vehicle_no}/lastdata",
                params={"status": STATUS_ON}
            )
        doc = self._json(resp, "Vehicle_wise_lastdata")

        if isinstance(doc, dict):
            # Flatten primary GPS
            doc["gps_current"] = flatten_lastdata(doc.pop("last_data_current", {}))

            # Flatten each IMEI's lastdata
            raw_imeis = doc.pop("imeis", {})
            doc["imeis_summary"] = {
                imei_key: {
                    "imei":        idata.get("imei"),
                    "status":      idata.get("imei_status"),
                    "device_time": idata.get("device_time"),
                    "gps":         flatten_lastdata(idata.get("lastdata", {})),
                }
                for imei_key, idata in raw_imeis.items()
                if isinstance(idata, dict)
            }

        return doc

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 5: bluedart_lastdata
    # Entry point: imei
    # ──────────────────────────────────────────────────────────────────────

    async def get_imei_lastdata(self, imei: str) -> Dict:
        """
        Raw GPS feed for a specific IMEI device.
        Note: this collection has NO m_trip_id — standalone IMEI data.

        MongoDB query equivalent:
          bluedart_lastdata.find({ imei, group_id:'0041' })
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{settings.VEHICLE_SERVICE_URL}/lastdata/imei",
                params={
                    "imei":     imei,
                    "group_id": GROUP_ID,
                }
            )
        doc = self._json(resp, "bluedart_lastdata")

        if isinstance(doc, dict) and "last_data1" in doc:
            doc["gps_parsed"] = flatten_lastdata(doc.pop("last_data1"))

        return doc

    # ──────────────────────────────────────────────────────────────────────
    # CONFIG: courier_customer_route_bluedart
    # Entry point: customer_id
    # ──────────────────────────────────────────────────────────────────────

    async def get_customer_routes(self, customer_id: str) -> Dict:
        """
        Routes configured for a customer.
        MongoDB query equivalent:
          courier_customer_route_bluedart.find({ customer_id, group_id:'0041', status:1 })
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{settings.TRIP_SERVICE_URL}/customer-routes",
                params={
                    "customer_id": customer_id,
                    "group_id":    GROUP_ID,
                    "status":      STATUS_ON,
                }
            )
        return self._json(resp, "courier_customer_route_bluedart")

    # ──────────────────────────────────────────────────────────────────────
    # STARTUP: cache delay master
    # ──────────────────────────────────────────────────────────────────────

    async def load_delay_master_cache(self) -> None:
        """
        Call ONCE at app startup via FastAPI lifespan event.
        Loads courier_route_delay_master (small static lookup) into memory.
        Avoids a DB call for every delay reason join.
        """
        global _DELAY_REASON_CACHE
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{settings.TRIP_SERVICE_URL}/delay-reasons",
                    params={"group_id": GROUP_ID, "status": STATUS_ON}
                )
            data = resp.json() if resp.status_code == 200 else []
            _DELAY_REASON_CACHE = {
                str(item["c_reason_code"]): item["creason_desc"]
                for item in data
                if "c_reason_code" in item and "creason_desc" in item
            }
            print(f"   Delay master cached: {len(_DELAY_REASON_CACHE)} reason codes loaded")
        except Exception as e:
            print(f"   WARNING: Delay master cache failed: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # SMART FETCH — main entry point called by chat_service
    # ──────────────────────────────────────────────────────────────────────

    async def fetch_for_query(
        self,
        intent: str,
        ids: Dict[str, Optional[str]],
    ) -> Dict[str, Any]:
        """
        Decides which of the 9 collections to hit based on intent + IDs.
        All selected API calls run concurrently via asyncio.gather.

        Args:
            intent:  from IntentDetector (STATUS_CHECK, LOCATE, DELAY_QUERY etc.)
            ids:     extracted from user message
                     {
                       shipment_no, m_trip_id, vehicle_no,
                       imei, customer_id
                     }
        Returns:
            Merged cleaned dict — ready for GPT-4o-mini
        """
        shipment_no = ids.get("shipment_no")
        m_trip_id   = ids.get("m_trip_id")
        vehicle_no  = ids.get("vehicle_no")
        imei        = ids.get("imei")
        customer_id = ids.get("customer_id")

        tasks: Dict[str, Any] = {}

        # Trip core — almost always needed
        if shipment_no:
            tasks["trip_core"] = self.get_trip_by_shipment(shipment_no)

        # Intent-based collection selection
        if intent in ("DELAY_QUERY", "ISSUE_RESOLUTION") and m_trip_id:
            tasks["delays"] = self.get_trip_delays(m_trip_id)

        if intent in ("ALERT_QUERY", "ISSUE_RESOLUTION") and m_trip_id and shipment_no:
            tasks["alerts"] = self.get_trip_alerts(m_trip_id, shipment_no)

        if intent == "STOPS_QUERY" and m_trip_id:
            tasks["stops"] = self.get_trip_stops(m_trip_id)

        if intent in ("LOCATE", "GPS_QUERY") and vehicle_no:
            tasks["vehicle_gps"] = self.get_vehicle_lastdata(vehicle_no)

        if intent == "IMEI_QUERY" and imei:
            tasks["imei_gps"] = self.get_imei_lastdata(imei)

        if intent == "CUSTOMER_QUERY" and customer_id:
            tasks["customer_routes"] = self.get_customer_routes(customer_id)

        # Full context for issue resolution
        if intent == "ISSUE_RESOLUTION" and m_trip_id:
            if "stops" not in tasks:
                tasks["stops"] = self.get_trip_stops(m_trip_id)

        # No tasks — give helpful message
        if not tasks:
            return {
                "note": (
                    "Koi valid ID nahi mila query mein. "
                    "Shipment number (jaise 11464086) ya vehicle number provide karein."
                )
            }

        # Run all concurrently
        keys    = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        merged: Dict[str, Any] = {}
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                merged[key] = {"error": str(result), "status": "unavailable"}
            else:
                merged[key] = result

        # Extract useful meta from trip_core for GPT summary
        merged["_meta"] = self._build_meta(merged)
        return merged

    def _build_meta(self, merged: Dict) -> Dict:
        """Build a clean summary dict from trip_core for GPT context."""
        meta = {}
        core = merged.get("trip_core", {})

        trip = core.get("trip_detail", {}) if isinstance(core, dict) else {}
        live = core.get("live_status",  {}) if isinstance(core, dict) else {}

        if isinstance(trip, dict):
            meta.update({
                "shipment_no":      trip.get("shipment_no"),
                "vehicle_no":       trip.get("vehicle_no"),
                "driver_name":      trip.get("driver_name"),
                "driver_mobile":    trip.get("driver_mobile"),
                "route_name":       trip.get("route_name"),
                "source":           trip.get("source_name"),
                "destination":      trip.get("destination_name"),
                "shipment_method":  trip.get("shipment_method"),
                "run_date":         trip.get("run_date"),
                "trip_status":      trip.get("trip_status"),
                "gps_vendor":       trip.get("gps_vendor_name"),
                "transporter":      trip.get("transporter_name"),
            })

        if isinstance(live, dict):
            meta.update({
                "eta":              live.get("eta"),
                "eta_hrs":          live.get("eta_hrs"),
                "delay_hours":      live.get("delay_hr"),
                "stopped_gt_2h":    live.get("stopped_gt_2h"),
                "vehicle_status":   live.get("vehicle_status_current"),
                "last_address":     live.get("last_address_current"),
                "last_halt_time":   live.get("last_halt_time_current"),
                "stopped_duration": live.get("stopped_duration"),
                "fixed_locks":      live.get("fixed_lock"),
                "portable_locks":   live.get("portable_lock"),
            })

        return meta

    def _json(self, response: Any, label: str) -> Any:
        """Safely parse HTTP response JSON."""
        try:
            if isinstance(response, Exception):
                return {"error": str(response), "collection": label}
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}", "collection": label}
        except Exception as e:
            return {"error": str(e), "collection": label}


# ─── Standalone mongo_select function ────────────────────────────────────────
import json as _json

MONGO_API_URL = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"

async def mongo_select(client, table: str, conditions: dict, fields: dict = None, sort: dict = None, limit: int = None):
    """Direct MongoDB API call with optional sort and limit."""
    import json as _json
    fields = fields or {}
    payload = {
        "conditions": _json.dumps(conditions),
        "fields":     _json.dumps(fields),
        "table":      table,
    }
    if sort:
        # API expects "field:desc" format not JSON
        sort_str = ",".join(f"{k}:{'desc' if v == -1 else 'asc'}" for k, v in sort.items())
        payload["sort"] = sort_str
    if limit:
        payload["limit"] = str(limit)

    try:
        resp = await client.post(
            MONGO_API_URL, data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            print(f"   [mongo_select] {table}: status={resp.status_code} count={len(data) if isinstance(data,list) else type(data)}")
            return data
        print(f"   [mongo_select] ERROR: {resp.status_code} {resp.text[:200]}")
        return {"error": f"HTTP {resp.status_code}", "table": table}
    except Exception as e:
        return {"error": str(e), "table": table}

def calc_halt_from_last_halt_time(halt_time_str: str):
    """Calculate halt: now - last_halt_time"""
    from datetime import datetime as _dt
    if not halt_time_str:
        return 0, "N/A"
    try:
        halt_dt    = _dt.strptime(str(halt_time_str).strip(), "%Y-%m-%d %H:%M:%S")
        total_mins = max(0, int((_dt.now() - halt_dt).total_seconds() / 60))
        return total_mins, f"{total_mins//60}h {total_mins%60}m"
    except Exception:
        return 0, "N/A"


async def fetch_alerts_direct(vehicle_no: str = None, shipment_no: str = None, m_trip_id: str = None) -> dict:
    """
    Fetch alerts directly from MongoDB API.
    Works with vehicle_no, shipment_no, or m_trip_id.
    """
    import httpx, json as _json
    conditions = {"group_id": GROUP_ID, "status": STATUS_ON}

    if vehicle_no:
        conditions["vehicle_name"] = vehicle_no
    elif shipment_no:
        conditions["shipment_no"] = shipment_no

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)) as client:
        result = await mongo_select(client, "logistic_trigger_log", conditions,
            {"alert_type":1,"voilation_time":1,"location":1,"geocoord":1,
             "start_time":1,"end_time":1,"level":1,"shipment_no":1,
             "vehicle_name":1,"create_date":1,"mail_sent":1},
            sort={"create_date": -1}, limit=20)

    logs = result if isinstance(result, list) else []
    total    = len(logs)
    critical = sum(1 for a in logs if str(a.get("level","")) == "1")
    high     = sum(1 for a in logs if str(a.get("level","")) == "2")

    return {
        "trigger_logs": logs,
        "summary": {
            "total": total,
            "critical_l1": critical,
            "high_l2": high,
        },
        "_mongo_hint": {
            "collection": "logistic_trigger_log",
            "query": f'db.logistic_trigger_log.find({conditions},{{"alert_type":1,"voilation_time":1,"location":1,"level":1,"start_time":1}}).sort({{"create_date":-1}}).limit(20)'
        }
    }
