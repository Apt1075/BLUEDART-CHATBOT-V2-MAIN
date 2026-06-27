"""
app/core/data_aggregator.py
============================
Bluedart — secutrakdb — 9 Collections
ALL queries use direct MongoDB REST API.
URL: http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery

Collections:
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
  - ALL data via direct MongoDB REST API (no microservice URLs)
  - All API calls are concurrent (asyncio.gather)
  - nested arrays / objects flattened before sending to GPT
  - delay_reason_master cached at startup (small static table)
"""

import asyncio
import httpx
import json as _json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from app.core.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GROUP_ID      = "0041"
STATUS_ON     = 1
MONGO_API_URL = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"
MONGO_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)

# In-memory cache for delay master (small static lookup table)
_DELAY_REASON_CACHE: Dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# CORE: Direct MongoDB REST API function
# ─────────────────────────────────────────────────────────────────────────────

async def mongo_select(
    client: httpx.AsyncClient,
    table: str,
    conditions: dict,
    fields: dict = None,
    sort: dict = None,
    limit: int = None,
) -> Any:
    """
    Direct MongoDB API call.
    Replaces all microservice client.get() calls.
    """
    fields  = fields or {}
    payload = {
        "conditions": _json.dumps(conditions),
        "fields":     _json.dumps(fields),
        "table":      table,
    }
    if sort:
        sort_str = ",".join(
            f"{k}:{'desc' if v == -1 else 'asc'}" for k, v in sort.items()
        )
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
            cnt  = len(data) if isinstance(data, list) else "doc"
            print(f"   [mongo] {table}: {cnt} records")
            return data
        print(f"   [mongo] ERROR {resp.status_code} on {table}")
        return {"error": f"HTTP {resp.status_code}", "table": table}
    except Exception as e:
        print(f"   [mongo] EXCEPTION on {table}: {e}")
        return {"error": str(e), "table": table}


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
        "ignition":        first(raw.get("io2LR")),
        "gps_vendor":      first(raw.get("io8LR")),
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
# HELPER: halt calculator
# ─────────────────────────────────────────────────────────────────────────────

def calc_halt_from_last_halt_time(halt_time_str: str):
    """Calculate halt: now - last_halt_time → (minutes, '2h 30m')"""
    if not halt_time_str:
        return 0, "N/A"
    try:
        halt_dt    = datetime.strptime(str(halt_time_str).strip(), "%Y-%m-%d %H:%M:%S")
        total_mins = max(0, int((datetime.now() - halt_dt).total_seconds() / 60))
        return total_mins, f"{total_mins//60}h {total_mins%60}m"
    except Exception:
        return 0, "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CLASS — All methods use direct MongoDB API
# ─────────────────────────────────────────────────────────────────────────────

class BluedartAggregator:
    """
    Fetches Bluedart logistics data from all 9 MongoDB collections
    via direct MongoDB REST API.
    URL: http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery
    """

    def __init__(self):
        self.timeout = MONGO_TIMEOUT

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 1 + 3: courier_trip_detail + trip_dashboard_live_status
    # Entry point: shipment_no
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_by_shipment(self, shipment_no: str) -> Dict:
        """
        Primary lookup for any shipment query.
        Hits both trip detail and live dashboard concurrently.

        MongoDB equivalent:
          courier_trip_detail.find({ shipment_no, group_id:'0041', status:1 })
          trip_dashboard_live_status.find({ shipment_no, group_id:'0041', status:1 })
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            trip, live = await asyncio.gather(
                mongo_select(client, "courier_trip_detail",
                    {"group_id": GROUP_ID, "status": STATUS_ON, "shipment_no": shipment_no},
                    {"shipment_no":1,"vehicle_no":1,"driver_name":1,"driver_mobile":1,
                     "source_name":1,"source_code":1,"destination_name":1,"destination_code":1,
                     "route_name":1,"route_code":1,"run_date":1,"trip_status":1,"status":1,
                     "shipment_method":1,"gps_vendor_name":1,"transporter_name":1,"fleet_no":1,
                     "region_code":1,"imei_no":1,"imei_no2":1,"imei_no3":1,
                     "exception_common_backend":1,"exception_common_backend_2":1,
                     "exception_common_backend_3":1,"schedule_departure":1,"schedule_arrival":1,
                     "actual_source_departure_time":1,"actual_destination_arrival_time":1,
                     "fixed_lock":1,"portable_lock":1}
                ),
                mongo_select(client, "trip_dashboard_live_status",
                    {"group_id": GROUP_ID, "status": STATUS_ON, "shipment_no": shipment_no},
                    {"vehicle_no":1,"shipment_no":1,"eta":1,"eta_hrs":1,"etd":1,"delay_hr":1,
                     "vehicle_status_current":1,"last_halt_time_current":1,"last_address_current":1,
                     "last_halt_time1":1,"vehicle_status1":1,"last_address1":1,
                     "last_halt_time2":1,"vehicle_status2":1,
                     "last_halt_time3":1,"vehicle_status3":1,
                     "stopped_gt_2h":1,"stopped_gt_5h":1,"stopped_duration":1,
                     "delay_hours_2_to_5h":1,"critical_hours_gt_5h":1,"delaying_sta":1,
                     "delay_trip_gt_60s":1,"eta_lt_2h":1,
                     "fixed_lock":1,"portable_lock":1,
                     "on_time_trip":1,"route_distance":1,"last_data_current":1}
                ),
                return_exceptions=True
            )

        # Handle list response
        if isinstance(trip, list): trip = trip[0] if trip else {}
        if isinstance(live, list): live = live[0] if live else {}

        # Flatten GPS arrays in live status
        if isinstance(live, dict):
            if "last_data_current" in live:
                live["gps_current"] = flatten_lastdata(live.pop("last_data_current", {}))
            for k in ["last_data1", "last_data2", "last_data3"]:
                live.pop(k, None)

        return {"trip_detail": trip, "live_status": live}

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 2: courier_trip_detail_customer
    # Entry point: m_trip_id
    # ──────────────────────────────────────────────────────────────────────
    async def get_trip_stops(self, m_trip_id: str = None,
        vehicle_no: str = None, run_date: str = None) -> Dict:
        conditions = {"group_id": GROUP_ID, "status": STATUS_ON}
        if vehicle_no:
            conditions["vehicle_no_prm"] = vehicle_no
        else:
            return {"note":"No vehicle","stops_detail":[],"total_stops":0,"completed_pod":0,"pending_stops":0}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            stops = await mongo_select(client, "courier_trip_detail_customer",
                conditions,
                {"location_name":1,"location_code":1,"location_sequence":1,
                "sequence_no":1,"pod_status":1,"poa_status":1,
                "schedule_time_arrival":1,"schedule_time_departure":1,
                "halt_duration":1,"run_date_prm":1},
                limit=100
            )

        if not isinstance(stops, list) or not stops:
            return {"note":"No stops found","stops_detail":[],"total_stops":0,"completed_pod":0,"pending_stops":0}

        # Python mein sort latest first
        stops.sort(key=lambda x: str(x.get("run_date_prm","") or ""), reverse=True)
        latest_date = str(stops[0].get("run_date_prm",""))[:10]

        # Latest trip ke stops only
        latest = [s for s in stops if str(s.get("run_date_prm","")).startswith(latest_date)]

        # Safe sort by sequence
        def safe_seq(x):
            try:
                return int(x.get("location_sequence") or x.get("sequence_no") or 0)
            except (ValueError, TypeError):
                return 0

        latest.sort(key=safe_seq)

        return {
            "total_stops":   len(latest),
            "completed_pod": sum(1 for s in latest if s.get("pod_status")==1),
            "pending_stops": sum(1 for s in latest if s.get("pod_status")==0),
            "stops_detail":  latest,
            "trip_date":     latest_date,
        }
    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 6 + 7: logistic_trigger_log + bluedart_trigger_dashboard
    # Entry point: shipment_no, vehicle_no, or m_trip_id
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_alerts(self, m_trip_id: str = None, shipment_no: str = None,
                               vehicle_no: str = None) -> Dict:
        """
        All alerts from logistic_trigger_log + bluedart_trigger_dashboard.
        MongoDB:
          logistic_trigger_log.find({shipment_no/vehicle_name}).sort({create_date:-1}).limit(20)
          bluedart_trigger_dashboard.find({shipment_no})
        """
        cond_log  = {"group_id": GROUP_ID, "status": STATUS_ON}
        cond_dash = {"group_id": GROUP_ID, "status": STATUS_ON}

        if shipment_no:
            cond_log["shipment_no"]  = shipment_no
            cond_dash["shipment_no"] = shipment_no
        elif vehicle_no:
            cond_log["vehicle_name"] = vehicle_no
        elif m_trip_id:
            cond_log["m_trip_id"]  = m_trip_id
            cond_dash["m_trip_id"] = m_trip_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            logs, dash = await asyncio.gather(
                mongo_select(client, "logistic_trigger_log", cond_log,
                    {"alert_type":1,"voilation_time":1,"location":1,"geocoord":1,
                     "start_time":1,"end_time":1,"level":1,"shipment_no":1,
                     "vehicle_name":1,"create_date":1,"mail_sent":1,"vid":1},
                    sort={"create_date": -1}, limit=20
                ),
                mongo_select(client, "bluedart_trigger_dashboard", cond_dash,
                    {"shipment_no":1,"flag_critical":1,"qrt_assigned":1,
                     "threats":1,"priority":1,"trip_status":1,"update_time":1}
                ),
                return_exceptions=True
            )

        # Process logs
        if not isinstance(logs, list): logs = []
        total    = len(logs)
        critical = sum(1 for a in logs if str(a.get("level","")) == "1")
        high     = sum(1 for a in logs if str(a.get("level","")) == "2")

        # Process dashboard
        dash_info = {}
        if isinstance(dash, list) and dash:
            dash_info = dash[0]
            if "alerts" in dash_info:
                dash_info["alerts_summary"] = summarize_alerts(dash_info.pop("alerts", []))
        elif isinstance(dash, dict) and "error" not in dash:
            dash_info = dash
            if "alerts" in dash_info:
                dash_info["alerts_summary"] = summarize_alerts(dash_info.pop("alerts", []))

        return {
            "trigger_logs":      logs,
            "trigger_dashboard": dash_info,
            "summary":           {"total": total, "critical_l1": critical, "high_l2": high},
        }

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 8 + 9: courier_route_delay + courier_route_delay_master
    # Entry point: shipment_no or m_trip_id
    # ──────────────────────────────────────────────────────────────────────

    async def get_trip_delays(self, m_trip_id: str = None, shipment_no: str = None) -> Dict:
        """
        Delay incidents with reason descriptions joined from cached master.
        MongoDB: courier_route_delay.find({trip_id/m_trip_id}).sort({entry_date:-1})
        """
        conditions = {"group_id": GROUP_ID, "status": STATUS_ON}
        if shipment_no:
            conditions["trip_id"] = shipment_no
        elif m_trip_id:
            conditions["m_trip_id"] = m_trip_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            delays = await mongo_select(client, "courier_route_delay", conditions,
                {"trip_id":1,"delay_reason":1,"delay_seq":1,"total_delay_in_min":1,
                 "location_name":1,"incident_date":1,"incident_time":1,"entry_date":1,
                 "enroute_code":1,"driver_name":1,"driver_mobile":1,"vehicle_no":1,
                 "route_name":1,"source_name":1,"destination_name":1,"remarks":1,
                 "trip_vehicle_no":1,"fleet_no":1},
                sort={"entry_date": -1}, limit=20
            )

        if isinstance(delays, list):
            total_mins = 0
            for d in delays:
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
        return {"note": "No delays found", "delays": []}

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 4: Vehicle_wise_lastdata
    # Entry point: vehicle_no
    # ──────────────────────────────────────────────────────────────────────

    async def get_vehicle_lastdata(self, vehicle_no: str) -> Dict:
        """
        Latest GPS snapshot for a vehicle.
        MongoDB: Vehicle_wise_lastdata.find({vehicle_no, status:1})
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            result = await mongo_select(client, "Vehicle_wise_lastdata",
                {"group_id": GROUP_ID, "status": STATUS_ON, "vehicle_no": vehicle_no},
                {"vehicle_no":1,"last_data_current":1,"vehicle_status":1,
                 "last_address":1,"last_halt_time":1,"imei_no":1}
            )

        doc = result[0] if isinstance(result, list) and result else result
        if isinstance(doc, dict):
            doc["gps_current"] = flatten_lastdata(doc.pop("last_data_current", {}))
            raw_imeis = doc.pop("imeis", {})
            if raw_imeis and isinstance(raw_imeis, dict):
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
        return doc if doc else {"error": f"No GPS data for vehicle {vehicle_no}"}

    # ──────────────────────────────────────────────────────────────────────
    # COLLECTION 5: bluedart_lastdata
    # Entry point: imei
    # ──────────────────────────────────────────────────────────────────────

    async def get_imei_lastdata(self, imei: str) -> Dict:
        """
        Raw GPS feed for a specific IMEI device.
        MongoDB: bluedart_lastdata.find({imei, group_id:'0041'})
        Note: No m_trip_id in this collection — standalone IMEI data.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            result = await mongo_select(client, "bluedart_lastdata",
                {"group_id": GROUP_ID, "imei": imei},
                {"imei":1,"last_data1":1,"vehicle_no":1,"sts":1}
            )

        doc = result[0] if isinstance(result, list) and result else result
        if isinstance(doc, dict) and "last_data1" in doc:
            doc["gps_parsed"] = flatten_lastdata(doc.pop("last_data1"))
        return doc if doc else {"error": f"No IMEI data for {imei}"}

    # ──────────────────────────────────────────────────────────────────────
    # CONFIG: courier_customer_route_bluedart
    # Entry point: customer_id
    # ──────────────────────────────────────────────────────────────────────

    async def get_customer_routes(self, customer_id: str) -> Dict:
        """
        Routes configured for a customer.
        MongoDB: courier_customer_route_bluedart.find({customer_id, group_id, status})
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            result = await mongo_select(client, "courier_customer_route_bluedart",
                {"group_id": GROUP_ID, "status": STATUS_ON, "customer_id": customer_id},
                {"customer_id":1,"route_id":1,"route_name":1,"route_code":1}
            )
        return result if result else {"note": f"No routes for customer {customer_id}"}

    # ──────────────────────────────────────────────────────────────────────
    # STARTUP: cache delay master from MongoDB directly
    # ──────────────────────────────────────────────────────────────────────

    async def load_delay_master_cache(self) -> None:
        """
        Call ONCE at app startup.
        Loads courier_route_delay_master into memory.
        Avoids a DB call for every delay reason join.
        """
        global _DELAY_REASON_CACHE
        try:
            async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
                data = await mongo_select(client, "courier_route_delay_master",
                    {"group_id": GROUP_ID, "status": STATUS_ON}, {}
                )
            if isinstance(data, list):
                _DELAY_REASON_CACHE = {
                    str(item["c_reason_code"]): item["creason_desc"]
                    for item in data
                    if "c_reason_code" in item and "creason_desc" in item
                }
                print(f"   Delay master cached: {len(_DELAY_REASON_CACHE)} reason codes")
            else:
                print(f"   WARNING: Delay master cache failed: {data}")
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
        Routes to correct collections based on intent + IDs.
        All selected API calls run concurrently via asyncio.gather.
        """
        from app.core.intent_detector import Intent

        shipment_no = ids.get("shipment_no")
        vehicle_no  = ids.get("vehicle_no")
        imei        = ids.get("imei")
        customer_id = ids.get("customer_id")

        tasks: Dict[str, Any] = {}

        # ── Shipment-based queries ────────────────────────────────────────
        if shipment_no:
            tasks["trip_core"] = self.get_trip_by_shipment(shipment_no)

        if shipment_no and intent in ("DELAY_QUERY", "ISSUE_RESOLUTION"):
            tasks["delays"] = self.get_trip_delays(shipment_no=shipment_no)

        if shipment_no and intent in ("ALERT_QUERY", "ISSUE_RESOLUTION"):
            tasks["alerts"] = self.get_trip_alerts(shipment_no=shipment_no)

        if shipment_no and intent == "STOPS_QUERY":
            # Get m_trip_id first then fetch stops
            core = await self.get_trip_by_shipment(shipment_no)
            trip = core.get("trip_detail", {})
            if isinstance(trip, list): trip = trip[0] if trip else {}
            m_id = str(trip.get("m_trip_id","")) if isinstance(trip,dict) else ""
            return {
                "trip_core":  core,
                "trip_stops": await self.get_trip_stops(m_id) if m_id else {"note":"m_trip_id not found"},
            }

        # ── Vehicle-based queries ─────────────────────────────────────────
        if vehicle_no and intent in ("LOCATE", "GPS_QUERY"):
            tasks["vehicle_gps"] = self.get_vehicle_lastdata(vehicle_no)
            # Also get active trip for this vehicle
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                trip_info = await mongo_select(client, "courier_trip_detail",
                    {"group_id":GROUP_ID,"status":STATUS_ON,"vehicle_no":vehicle_no,"trip_status":1},
                    {"shipment_no":1,"vehicle_no":1,"driver_name":1,"route_name":1,
                     "source_name":1,"destination_name":1,"run_date":1,"trip_status":1},
                    sort={"run_date":-1}, limit=1
                )
            merged_result = {"vehicle_gps": await tasks["vehicle_gps"]}
            merged_result["active_trip"] = trip_info[0] if isinstance(trip_info,list) and trip_info else {}
            merged_result["_meta"] = {}
            return merged_result

        if vehicle_no and intent == "ALERT_QUERY":
            result = await self.get_trip_alerts(vehicle_no=vehicle_no)
            return {"alerts": result,
                    "_mongo_hint": {
                        "collection": "logistic_trigger_log",
                        "query": f'db.logistic_trigger_log.find({{"vehicle_name":"{vehicle_no}","group_id":"0041","status":1}}).sort({{"create_date":-1}}).limit(20)'
                    }}

        # ── IMEI query ────────────────────────────────────────────────────
        if imei and intent == "IMEI_QUERY":
            tasks["imei_gps"] = self.get_imei_lastdata(imei)

        # ── Customer query ────────────────────────────────────────────────
        if customer_id and intent == "CUSTOMER_QUERY":
            tasks["customer_routes"] = self.get_customer_routes(customer_id)

        # ── ISSUE_RESOLUTION — fetch everything ───────────────────────────
        if intent == "ISSUE_RESOLUTION" and shipment_no:
            if "trip_core" not in tasks:
                tasks["trip_core"] = self.get_trip_by_shipment(shipment_no)

        # ── No tasks — return helpful note ────────────────────────────────
        if not tasks:
            return {
                "note": (
                    "Please provide a shipment number or vehicle number. "
                    "Example: 'Where is shipment 11495287?' or 'Alerts for vehicle HR55AJ9358'"
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

        merged["_meta"] = self._build_meta(merged)
        return merged

    # ─────────────────────────────────────────────────────────────────────
    # META builder
    # ─────────────────────────────────────────────────────────────────────

    def _build_meta(self, merged: Dict) -> Dict:
        """Extract key fields for GPT quick summary."""
        meta = {}
        core = merged.get("trip_core", {})
        trip = core.get("trip_detail", {}) if isinstance(core, dict) else {}
        live = core.get("live_status",  {}) if isinstance(core, dict) else {}
        if isinstance(trip, list): trip = trip[0] if trip else {}
        if isinstance(live, list): live = live[0] if live else {}

        if isinstance(trip, dict):
            meta.update({
                "shipment_no":     trip.get("shipment_no"),
                "vehicle_no":      trip.get("vehicle_no"),
                "driver_name":     trip.get("driver_name"),
                "driver_mobile":   trip.get("driver_mobile"),
                "route_name":      trip.get("route_name"),
                "source":          trip.get("source_name"),
                "destination":     trip.get("destination_name"),
                "shipment_method": trip.get("shipment_method"),
                "run_date":        trip.get("run_date"),
                "trip_status":     trip.get("trip_status"),
                "gps_vendor":      trip.get("gps_vendor_name"),
                "transporter":     trip.get("transporter_name"),
            })

        if isinstance(live, dict):
            meta.update({
                "eta":              live.get("eta"),
                "eta_hrs":          live.get("eta_hrs"),
                "etd":              live.get("etd"),
                "eta_lt_2h":        live.get("eta_lt_2h"),
                "delay_hours":      live.get("delay_hr"),
                "delaying_sta":     live.get("delaying_sta"),
                "delay_trip_gt_60s": live.get("delay_trip_gt_60s"),
                "delay_hours_2_to_5h": live.get("delay_hours_2_to_5h"),
                "critical_hours_gt_5h": live.get("critical_hours_gt_5h"),
                "stopped_gt_2h":    live.get("stopped_gt_2h"),
                "stopped_gt_5h":    live.get("stopped_gt_5h"),
                "stopped_duration": live.get("stopped_duration"),
                "vehicle_status":   live.get("vehicle_status_current"),
                "last_address":     live.get("last_address_current"),
                "last_halt_time":   live.get("last_halt_time_current"),
                "fixed_locks":      live.get("fixed_lock"),
                "portable_locks":   live.get("portable_lock"),
            })
        return meta


# ─────────────────────────────────────────────────────────────────────────────
# Standalone functions — used directly by chat_service.py
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_alerts_direct(
    vehicle_no: str = None,
    shipment_no: str = None,
    m_trip_id: str = None,
) -> dict:
    """
    Direct alert fetch by vehicle_no or shipment_no.
    Used by chat_service for ALERT_QUERY bypass.
    """
    conditions = {"group_id": GROUP_ID}
    if vehicle_no:    conditions["vehicle_name"] = vehicle_no
    elif shipment_no: conditions["shipment_no"]  = shipment_no
    elif m_trip_id:   conditions["m_trip_id"]    = m_trip_id

    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        logs = await mongo_select(client, "logistic_trigger_log", conditions,
            {"alert_type":1,"voilation_time":1,"location":1,"geocoord":1,
             "start_time":1,"end_time":1,"level":1,"shipment_no":1,
             "vehicle_name":1,"create_date":1,"mail_sent":1},
            sort={"create_date": -1}, limit=20
        )

    logs     = logs if isinstance(logs, list) else []
    total    = len(logs)
    critical = sum(1 for a in logs if str(a.get("level","")) == "1")
    high     = sum(1 for a in logs if str(a.get("level","")) == "2")

    return {
        "trigger_logs": logs,
        "summary":      {"total": total, "critical_l1": critical, "high_l2": high},
        "_mongo_hint":  {
            "collection": "logistic_trigger_log",
            "query": (
                'db.logistic_trigger_log.find('
                + _json.dumps(conditions)
                + ',{"alert_type":1,"level":1,"voilation_time":1,"location":1,"start_time":1})'
                '.sort({"create_date":-1}).limit(20)'
            )
        }
    }

# ─────────────────────────────────────────────────────────────────────────────
# ADD THESE FUNCTIONS at the END of your existing data_aggregator.py
# (after the existing fetch_alerts_direct function)
# ─────────────────────────────────────────────────────────────────────────────

def _last_month_range():
    """Returns (date_from, date_to) for last calendar month."""
    now   = datetime.now()
    first = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    last  = now.replace(day=1) - timedelta(days=1)
    return first.strftime("%Y-%m-%d 00:00:00"), last.strftime("%Y-%m-%d 23:59:59")


async def fetch_vehicle_location(vehicle_no: str) -> dict:
    """
    Fetch vehicle GPS + active trip + live status concurrently.
    Used for: 'Where is vehicle HR55AJ9358?'
    Returns trip_core structure so format_trip_status can render card.
    """
    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        gps_result, trip_result, live_result = await asyncio.gather(
            mongo_select(client, "Vehicle_wise_lastdata",
                {"group_id": GROUP_ID, "status": STATUS_ON, "vehicle_no": vehicle_no},
                {"vehicle_no":1,"last_data_current":1,"vehicle_status":1,
                 "last_address":1,"last_halt_time":1,"imei_no":1}
            ),
            mongo_select(client, "courier_trip_detail",
                {"group_id": GROUP_ID, "status": STATUS_ON,
                 "vehicle_no": vehicle_no, "trip_status": 1},
                {"shipment_no":1,"vehicle_no":1,"driver_name":1,"driver_mobile":1,
                 "route_name":1,"source_name":1,"destination_name":1,
                 "run_date":1,"trip_status":1,"shipment_method":1,
                 "exception_common_backend":1,"transporter_name":1,"fleet_no":1,
                 "region_code":1,"gps_vendor_name":1,"imei_no":1},
                sort={"run_date": -1}, limit=1
            ),
            mongo_select(client, "trip_dashboard_live_status",
                {"group_id": GROUP_ID, "status": STATUS_ON, "vehicle_no": vehicle_no},
                {"vehicle_no":1,"shipment_no":1,"eta":1,"eta_hrs":1,"etd":1,"delay_hr":1,
                 "vehicle_status_current":1,"last_halt_time_current":1,
                 "last_address_current":1,"last_halt_time1":1,"vehicle_status1":1,
                 "stopped_gt_2h":1,"stopped_gt_5h":1,"stopped_duration":1,
                 "delay_hours_2_to_5h":1,"critical_hours_gt_5h":1,"delaying_sta":1,
                 "delay_trip_gt_60s":1,"eta_lt_2h":1,
                 "last_data_current":1},
                sort={"update_time": -1}, limit=1
            ),
            return_exceptions=True
        )

    # GPS data
    gps_doc = gps_result[0] if isinstance(gps_result, list) and gps_result else {}
    if isinstance(gps_doc, dict) and "last_data_current" in gps_doc:
        gps_doc["gps_current"] = flatten_lastdata(gps_doc.pop("last_data_current", {}))

    # Active trip
    trip_doc = trip_result[0] if isinstance(trip_result, list) and trip_result else {}

    # Live status
    live_doc = live_result[0] if isinstance(live_result, list) and live_result else {}
    if isinstance(live_doc, dict) and "last_data_current" in live_doc:
        live_doc["gps_current"] = flatten_lastdata(live_doc.pop("last_data_current", {}))

    if not isinstance(trip_doc, dict) or not trip_doc:
        # No active trip — return GPS only with error
        return {
            "error":       f"No active trip found for vehicle {vehicle_no}",
            "vehicle_gps": gps_doc,
            "_mongo_hint": {
                "collection": "Vehicle_wise_lastdata",
                "query":      f'db.Vehicle_wise_lastdata.findOne({{"vehicle_no":"{vehicle_no}","group_id":"0041","status":1}})'
            }
        }

    # Return trip_core structure — same as shipment query
    # format_trip_status() will render as card
    return {
        "trip_core": {
            "trip_detail": trip_doc,
            "live_status": live_doc,
        },
        "vehicle_gps": gps_doc,
        "_mongo_hint": {
            "collection": "courier_trip_detail",
            "query":      f'db.courier_trip_detail.findOne({{"vehicle_no":"{vehicle_no}","group_id":"0041","status":1,"trip_status":1}},{{"shipment_no":1,"route_name":1,"source_name":1,"destination_name":1,"driver_name":1}})'
        }
    }


async def fetch_max_trips_vehicle(date_from: str = None, date_to: str = None) -> dict:
    """
    Vehicle with highest number of trips in given period.
    MongoDB REST API doesn't support aggregation, so we:
    1. Fetch all trips for the period (only vehicle_no field)
    2. Count in Python using Counter
    """
    if not date_from or not date_to:
        date_from, date_to = _last_month_range()

    conditions = {
        "group_id": GROUP_ID,
        "status":   STATUS_ON,
        "run_date": {"$gte": date_from, "$lte": date_to}
    }

    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        trips = await mongo_select(client, "courier_trip_detail", conditions,
            {"vehicle_no":1,"shipment_no":1,"run_date":1,"route_name":1,
             "source_name":1,"destination_name":1,"shipment_method":1,"driver_name":1},
            sort={"run_date": -1}
        )

    if not isinstance(trips, list) or not trips:
        return {"error": "No trips found for the period", "date_from": date_from, "date_to": date_to}

    from collections import Counter
    vehicle_counts = Counter(t.get("vehicle_no","") for t in trips if t.get("vehicle_no"))
    top5 = vehicle_counts.most_common(5)

    top_vehicle = top5[0][0] if top5 else ""
    top_count   = top5[0][1] if top5 else 0
    sample_trips = [t for t in trips if t.get("vehicle_no") == top_vehicle][:5]

    return {
        "query_type":   "MAX_TRIPS",
        "period":       date_from[:10] + " to " + date_to[:10],
        "total_trips":  len(trips),
        "top_vehicles": [{"vehicle_no": v, "trip_count": c} for v, c in top5],
        "winner": {
            "vehicle_no":   top_vehicle,
            "trip_count":   top_count,
            "sample_trips": sample_trips,
        },
        "_mongo_hint": {
            "collection": "courier_trip_detail",
            "query":      'db.courier_trip_detail.aggregate([{"$match":{"group_id":"0041","status":1,"run_date":{"$gte":"' + date_from + '","$lte":"' + date_to + '"}}},{"$group":{"_id":"$vehicle_no","trip_count":{"$sum":1}}},{"$sort":{"trip_count":-1}},{"$limit":5}])'
        }
    }


async def fetch_running_trips(limit: int = 20) -> dict:
    """
    All currently running/active trips.
    courier_trip_detail: group_id=0041, trip_status=1 (active)
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d 00:00:00")

    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        trips = await mongo_select(client, "courier_trip_detail",
            {"group_id": GROUP_ID, "status": STATUS_ON, "trip_status": 1},
            {"shipment_no":1,"vehicle_no":1,"driver_name":1,"driver_mobile":1,
             "route_name":1,"source_name":1,"destination_name":1,"run_date":1,
             "shipment_method":1,"region_code":1,"fleet_no":1,
             "exception_common_backend":1,"exception_common_backend_2":1,
             "exception_common_backend_3":1,"transporter_name":1,
             "schedule_departure":1,"schedule_arrival":1},
            sort={"run_date": -1}, limit=limit
        )

    if not isinstance(trips, list):
        return {"error": "Could not fetch running trips", "trips": []}

    total = len(trips)
    gps_active  = sum(1 for t in trips if t.get("exception_common_backend","") == "")
    gps_na      = sum(1 for t in trips if t.get("exception_common_backend","") == "GPS NA")
    gps_no_conn = sum(1 for t in trips if t.get("exception_common_backend","") == "No Connectivity")

    return {
        "query_type":  "RUNNING_TRIPS",
        "total_shown": total,
        "summary": {
            "gps_active":         gps_active,
            "gps_na":             gps_na,
            "gps_no_connectivity":gps_no_conn,
        },
        "trips":       trips,
        "filters_applied": {
            "trip_status": 1,
            "group_id":    GROUP_ID,
        },
        "_mongo_hint": {
            "collection": "courier_trip_detail",
            "query":      'db.courier_trip_detail.find({"group_id":"0041","status":1,"trip_status":1},{"shipment_no":1,"vehicle_no":1,"driver_name":1,"route_name":1,"source_name":1,"destination_name":1,"run_date":1,"exception_common_backend":1}).sort({"run_date":-1}).limit(20)'
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# DYNAMIC FILTER — courier_trip_detail ke important keys se auto conditions
# ─────────────────────────────────────────────────────────────────────────────

# Har field ke possible user words → DB value mapping
FIELD_VALUE_MAP = {
    "shipment_method": {
        "feeder":    "Feeder",
        "sfc":       "SFC",
        "surface":   "Surface",
        "air":       "Air",
        "road":      "Road",
        "lh":        "LH",
        "linehaul":  "LH",
    },
    "region_code": {
        "north":  "NORTH",
        "south":  "SOUTH",
        "east":   "EAST",
        "west":   "WEST",
        "west2":  "WEST2",
    },
    "exception_common_backend": {          # main GPS (imei_no_type)
        "gps na":          "GPS NA",
        "gps_na":          "GPS NA",
        "no connectivity": "No Connectivity",
        "offline":         "No Connectivity",
        "active":          "__gps_active__",   # special: $nin GPS NA/No Conn
        "gps active":      "__gps_active__",
    },
    "exception_common_backend_2": {        # fixed lock GPS (imei_no_type2)
        "fixed lock na":        "GPS NA",
        "fixed gps na":         "GPS NA",
        "fixed active":         "",
        "fixed lock active":    "",
        "fixed no connectivity":"No Connectivity",
    },
    "exception_common_backend_3": {        # portable lock GPS (imei_no_type3)
        "portable lock na":        "GPS NA",
        "portable gps na":         "GPS NA",
        "portable active":         "",
        "portable lock active":    "",
        "portable no connectivity":"No Connectivity",
        "no portable":             None,   # portable lock exist hi nahi karta
        "without portable":        None,
    },
    "gps_vendor_name": {
        "axestrack": "Axestrack_bluedart",
        "kiasaint":  "Kiasaint_bluedart",
        "lynkit":    "Lynkit_Bluedart",
        "icici":     "ICICI",
        "3rd party": "__3rdparty__",       # special: $nin handle
    },
    "imei_no_type": {
        "axestrack": "Axestrack_bluedart",
        "kiasaint":  "Kiasaint_bluedart",
        "lynkit":    "Lynkit_Bluedart",
        "icici":     "ICICI",
        "3rd party": "__3rdparty__",
    },
    "trip_type": {
        "transship":     "Transshipment",
        "transshipment": "Transshipment",
        "normal":        "Normal",
        "direct":        "Direct",
    },
    "close_by": {
        "supervisor":  "supervisor",
        "system":      "system",
        "forcefully":  "supervisor",
        "force close": "supervisor",
    },
}

# User keyword → which DB field it maps to
KEYWORD_TO_FIELD = {
    # shipment_method
    "feeder":    "shipment_method",
    "sfc":       "shipment_method",
    "surface":   "shipment_method",
    "air":       "shipment_method",
    "linehaul":  "shipment_method",
    "lh":        "shipment_method",
    # region
    "north":     "region_code",
    "south":     "region_code",
    "east":      "region_code",
    "west":      "region_code",
    "west2":     "region_code",
    # main GPS
    "gps na":          "exception_common_backend",
    "gps_na":          "exception_common_backend",
    "no connectivity": "exception_common_backend",
    "offline":         "exception_common_backend",
    # fixed lock GPS
    "fixed lock na":     "exception_common_backend_2",
    "fixed gps na":      "exception_common_backend_2",
    "fixed active":      "exception_common_backend_2",
    # portable lock GPS
    "portable lock na":  "exception_common_backend_3",
    "portable gps na":   "exception_common_backend_3",
    "portable active":   "exception_common_backend_3",
    "no portable":       "exception_common_backend_3",
    "without portable":  "exception_common_backend_3",
    # IMEI / vendor — gps_vendor_name primary field (imei_no_type bhi cover hoga regex se)
    "axestrack":  "gps_vendor_name",
    "kiasaint":   "gps_vendor_name",
    "icici":      "gps_vendor_name",
    "lynkit":     "gps_vendor_name",
    "3rd party":  "gps_vendor_name",
    # trip type
    "transship":     "trip_type",
    "transshipment": "trip_type",
    "normal":        "trip_type",
    # close_by
    "forcefully":  "close_by",
    "force close": "close_by",
    "supervisor":  "close_by",
}


def build_dynamic_conditions(user_msg: str, date_from: str, date_to: str) -> dict:
    """
    User ki natural language query se MongoDB conditions auto-build karta hai.
    courier_trip_detail ke important keys cover kiye hain.

    Example:
        "SFC trips without portable lock north region"
        → { shipment_method: "SFC", imei_no3: {$in:[None,""]}, region_code: "NORTH" }
    """
    import re as _re
    msg = user_msg.lower()

    conditions: dict = {
        "group_id": GROUP_ID,
        "status":   STATUS_ON,
        "run_date": {"$gte": date_from, "$lte": date_to},
    }

    # Multi-word keywords pehle scan karo (longest match first)
    all_keywords = sorted(KEYWORD_TO_FIELD.keys(), key=len, reverse=True)

    for kw in all_keywords:
        if kw not in msg:
            continue

        field     = KEYWORD_TO_FIELD[kw]
        value_map = FIELD_VALUE_MAP.get(field, {})
        value     = value_map.get(kw)

        # "no portable" / "without portable" → imei_no3 field blank/null hona chahiye
        if value is None and field == "exception_common_backend_3":
            conditions["imei_no3"] = {"$in": [None, ""]}
            continue

        # GPS active → NOT IN (GPS NA, No Connectivity)
        # "" exact match nahi karta jab field null/missing ho
        if value == "__gps_active__":
            if field not in conditions:
                conditions[field] = {"$nin": ["GPS NA", "No Connectivity"]}
            continue

        # 3rd party vendor → known vendors ke alawa sab
        if value == "__3rdparty__":
            conditions["gps_vendor_name"] = {
                "$nin": ["", "Axestrack_bluedart", "Kiasaint_bluedart", "Lynkit_Bluedart"]
            }
            continue

        if value is not None:
            # Ek field already set hai to overwrite mat karo (pehla match wins)
            if field not in conditions:
                # exception fields exact match rakhte hain ("GPS NA", "", "No Connectivity")
                # baaki string fields regex se match karo — DB mein "SFC_Network" ho
                # aur user "SFC" likhe tab bhi match hoga
                if field in ("exception_common_backend",
                             "exception_common_backend_2",
                             "exception_common_backend_3"):
                    conditions[field] = value                      # exact match
                else:
                    conditions[field] = {
                        "$regex":   value,
                        "$options": "i",                           # case-insensitive
                    }

    # ── Vehicle number (Indian format: HR55AJ9358) ─────────────────────────────
    veh = _re.search(r'\b([A-Z]{2}\d{2}[A-Z]{1,3}\d{4})\b', user_msg.upper())
    if veh:
        conditions["vehicle_no"] = veh.group(1)

    # ── Source code (FROM DEL, FROM BOM) ──────────────────────────────────────
    src = _re.search(r'\bfrom\s+([A-Z]{2,5})\b', user_msg.upper())
    if src:
        exclude = {"THE", "ALL", "LAST", "THIS", "WEEK", "MONTH", "DAY",
                   "NORTH", "SOUTH", "EAST", "WEST", "THOSE"}
        code = src.group(1)
        if code not in exclude:
            conditions["source_code"] = code

    # ── Destination code (TO DEL, TO BOM) ─────────────────────────────────────
    dst = _re.search(r'\bto\s+([A-Z]{2,5})\b', user_msg.upper())
    if dst:
        exclude = {"THE", "ALL", "NORTH", "SOUTH", "EAST", "WEST"}
        code = dst.group(1)
        if code not in exclude:
            conditions["destination_code"] = code

    # ── Fleet no ──────────────────────────────────────────────────────────────
    fleet = _re.search(r'\bfleet[_\s]?no?[:\s]+(\w+)\b', msg)
    if fleet:
        conditions["fleet_no"] = fleet.group(1)

    # ── Route code ────────────────────────────────────────────────────────────
    route = _re.search(r'\broute[_\s]?code[:\s]+([A-Z0-9]+)\b', user_msg.upper())
    if route:
        conditions["route_code"] = route.group(1)

    # ── Cancelled trips ───────────────────────────────────────────────────────
    if any(w in msg for w in ["cancelled", "canceled"]):
        conditions["trip_status"] = 2

    # ── Close remarks (forcefully closed) ────────────────────────────────────
    if any(w in msg for w in ["forcefully closed", "force closed"]):
        conditions["close_remarks"] = {"$regex": "supervisor", "$options": "i"}

    print(f"   [dynamic_conditions] {conditions}")
    return conditions


async def dynamic_trip_query(user_msg: str, date_from: str, date_to: str) -> dict:
    """
    User query + date range → courier_trip_detail se filtered trips.
    build_dynamic_conditions() se auto conditions ban jaati hain.

    Usage (chat_service se):
        data = await dynamic_trip_query(msg, date_from, date_to)
    """
    conditions = build_dynamic_conditions(user_msg, date_from, date_to)

    fields = {
        "shipment_no":                1,
        "vehicle_no":                 1,
        "run_date":                   1,
        "shipment_method":            1,
        "region_code":                1,
        "trip_type":                  1,
        "source_code":                1,
        "source_name":                1,
        "destination_code":           1,
        "destination_name":           1,
        "route_code":                 1,
        "route_name":                 1,
        "driver_name":                1,
        "driver_mobile":              1,
        "transporter_name":           1,
        "gps_vendor_name":            1,
        "gps_vendor2":                1,
        "gps_vendor3":                1,
        "fleet_no":                   1,
        "close_by":                   1,
        "close_date":                 1,
        "imei_no_type":               1,
        "imei_no_type2":              1,
        "imei_no_type3":              1,
        "exception_common_backend":   1,
        "exception_common_backend_2": 1,
        "exception_common_backend_3": 1,
        "trip_status":                1,
        "actual_source_departure_time":     1,
        "actual_destination_arrival_time":  1,
    }

    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        trips = await mongo_select(
            client,
            "courier_trip_detail",
            conditions,
            fields,
            sort={"run_date": -1},
            limit=50,
        )

    count = len(trips) if isinstance(trips, list) else 0
    print(f"   [dynamic_trip_query] {count} records found")

    # ── Summary counts (useful for reply header) ───────────────────────────────
    method_counts: dict = {}
    gps_counts = {"GPS_Active": 0, "GPS_NA": 0, "No_Connectivity": 0}

    if isinstance(trips, list):
        for t in trips:
            m = t.get("shipment_method") or "Unknown"
            method_counts[m] = method_counts.get(m, 0) + 1

            exc = t.get("exception_common_backend", "")
            if exc == "":
                gps_counts["GPS_Active"] += 1
            elif exc == "GPS NA":
                gps_counts["GPS_NA"] += 1
            else:
                gps_counts["No_Connectivity"] += 1

    return {
        "total_found":      count,
        "filters_applied":  conditions,
        "period":           f"{date_from[:10]} to {date_to[:10]}",
        "shipment_methods": method_counts,
        "gps_summary":      gps_counts,
        "data":             trips if isinstance(trips, list) else [],
        "_mongo_hint": {
            "collection": "courier_trip_detail",
            "query": (
                'db.courier_trip_detail.find('
                + _json.dumps(conditions)
                + ',{"shipment_no":1,"vehicle_no":1,"shipment_method":1,'
                '"exception_common_backend":1,"run_date":1})'
                '.sort({"run_date":-1}).limit(50)'
            ),
        },
    }