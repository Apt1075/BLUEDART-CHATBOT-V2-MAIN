"""
app/services/chat_service.py — Bluedart AI Chatbot v2
=======================================================
Clean rewrite using query_engine.py as single source of truth.

Flow for EVERY query:
  1. resolve_date_followup  → if user only sent a date, reconstruct full query
  2. is_live_query          → running/stopped/GPS = no date needed
  3. extract_filters + ids  → what filters + which IDs
  4. merge session context  → 10-turn memory
  5. has date?              → if no + not live + historical table → ask date
  6. build_conditions       → final MongoDB dict
  7. mongo_select           → fetch data
  8. format HTML            → return beautiful response
"""

import time, re, asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx

from app.core.data_aggregator import (
    BluedartAggregator, mongo_select, MONGO_TIMEOUT, GROUP_ID, STATUS_ON,
    fetch_alerts_direct, fetch_vehicle_location, fetch_max_trips_vehicle,
    fetch_running_trips, calc_halt_from_last_halt_time, flatten_lastdata,
)
from app.core.intent_detector import IntentDetector, Intent
from app.core.openai_client import OpenAIClient, extract_mongo_query
from app.core.response_formatter import (
    format_response, format_stopped_vehicles, format_bulk_report,
    format_location_trips, format_alerts, format_delays, format_trip_status,
    format_max_trips, calc_halt_mins, format_halt_duration, get_severity,
    get_gps_status, html_table, td, metric_box, section_title, links_row,
)
from app.core.query_engine import (
    T_TRIP, T_LIVE, T_TRIG_LOG, T_TRIG_DASH, T_DELAY,
    T_VEH_LAST, T_IMEI_LAST, T_STOPS, T_HALT,
    PROJECTIONS, SORT_FIELD, DATE_FIELD_MAP, LIVE_TABLES,
    is_live_query, parse_date, has_date,
    extract_filters, extract_ids, detect_table, build_conditions,
    date_clarification_html, resolve_date_followup, SESSION,
)
from app.core.cross_table_engine import (
    is_cross_table_query, run_cross_table_query,
)
from app.schemas.chat import ChatRequest, ChatResponse, ServiceCallInfo

MONGO_API = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"
LIMIT     = 20


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — Stopped vehicles (live, no date needed)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_hours(msg: str) -> float:
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:hour|hr|h)\b', msg.lower())
    if m: return float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:minute|min)\b', msg.lower())
    if m: return float(m.group(1)) / 60
    return 0.0

def _is_less_than(msg: str) -> bool:
    return any(x in msg.lower() for x in ["less than","less","under","below","within"])

def _detect_severity(msg: str) -> Optional[str]:
    ml = msg.lower()
    if any(k in ml for k in ["critical","24h+",">24h"]): return "critical"
    if any(k in ml for k in ["high","5-24h"]):            return "high"
    if any(k in ml for k in ["medium","3-5h"]):           return "medium"
    if any(k in ml for k in ["low ","<3h"]):              return "low"
    return None

async def _fetch_stopped(msg: str) -> dict:
    now     = datetime.now()
    from_dt = now.strftime("%Y-%m-%d 00:00:00")
    hours   = _extract_hours(msg)
    less    = _is_less_than(msg)
    thr_m   = hours * 60

    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        result = await mongo_select(client, T_LIVE,
            {"group_id": GROUP_ID, "status": STATUS_ON,
             "trip_status": 1, "vehicle_status_current": "Stopped",
             "update_time": {"$gte": from_dt}})

    if not isinstance(result, list) or not result:
        return {"error": "No stopped vehicles today", "_hours": hours, "_less": less}

    vehicles = []
    for rec in result:
        candidates = []
        for i in ["1","2","3"]:
            st  = rec.get(f"vehicle_status{i}")
            lht = rec.get(f"last_halt_time{i}")
            if st == "Stopped" and lht:
                mins, dur = calc_halt_from_last_halt_time(lht)
                candidates.append({"mins": mins, "dur": dur, "halt_time": lht,
                                    "address": rec.get(f"last_address{i}", "")})
        if not candidates and rec.get("last_halt_time_current"):
            mins, dur = calc_halt_from_last_halt_time(rec["last_halt_time_current"])
            candidates.append({"mins": mins, "dur": dur,
                                "halt_time": rec["last_halt_time_current"],
                                "address": rec.get("last_address_current", "")})
        if not candidates: continue
        best     = max(candidates, key=lambda x: x["mins"])
        max_mins = best["mins"]
        if thr_m > 0:
            if less and max_mins >= thr_m: continue
            if not less and max_mins < thr_m: continue

        gps = {}
        ld  = rec.get("last_data_current", {})
        if isinstance(ld, dict):
            def f(v): return v[0] if isinstance(v, list) and v else v
            gps = {"lat": f(ld.get("latitudeLR")), "lng": f(ld.get("longitudeLR")),
                   "speed": f(ld.get("speedLR")), "vendor": f(ld.get("io8LR"))}

        vehicles.append({
            "vehicle_no":      rec.get("vehicle_no"),
            "shipment_no":     rec.get("shipment_no"),
            "shipment_method": rec.get("shipment_method"),
            "halt_since":      best["halt_time"],
            "halt_duration":   best["dur"],
            "halt_minutes":    max_mins,
            "halt_hours":      round(max_mins / 60, 1),
            "severity":        get_severity(max_mins),
            "last_address":    best["address"] or rec.get("last_address_current", ""),
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
        "query_info": {"filter": "Stopped today",
                       "threshold": f"{'<' if less else '>'}{hours}h"},
        "summary": {"total": len(vehicles), "critical_gt_24h": critical,
                    "high_5_24h": high, "medium_3_5h": medium, "low_lt_3h": low},
        "vehicles": vehicles, "_hours": hours, "_less": less,
        "_mongo_hint": {
            "collection": T_LIVE,
            "query": f'db.{T_LIVE}.find({{"group_id":"0041","status":1,"trip_status":1,"vehicle_status_current":"Stopped","update_time":{{"$gte":"{from_dt}"}}}}).sort({{"update_time":-1}})'
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — Generic table fetch (uses query_engine)
# ─────────────────────────────────────────────────────────────────────────────

async def _generic_fetch(conditions: dict, table: str) -> List[dict]:
    proj      = PROJECTIONS.get(table, {})
    sort_fld  = SORT_FIELD.get(table)
    sort_dict = {sort_fld: -1} if sort_fld else None
    async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
        result = await mongo_select(client, table, conditions, proj, sort=sort_dict, limit=LIMIT)
    return result if isinstance(result, list) else []


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — Generic HTML table builder for any collection
# ─────────────────────────────────────────────────────────────────────────────

def _build_html_response(data: List[dict], table: str, conditions: dict,
                          filters_found: list, date_from: str = None,
                          date_to: str = None) -> str:
    count  = len(data)
    period = f"{date_from[:10]} → {date_to[:10]}" if date_from else "Live / Real-time"

    # Pick columns + row builder per table
    if table == T_TRIP:
        cols = ["#","Shipment No","Vehicle No","Driver","Route","Source","Destination",
                "Method","Run Date","Status","GPS","Fixed Lock","Portable Lock"]
        def row_fn(i, r):
            e1  = r.get("exception_common_backend","")
            e2  = r.get("exception_common_backend_2","")
            e3  = r.get("exception_common_backend_3","")
            ts  = r.get("trip_status", 1)
            ts_txt = {1:"Active",0:"Closed",2:"Cancelled"}.get(ts, str(ts))
            ts_col = {1:"#16a34a",0:"#6b7280",2:"#dc2626"}.get(ts,"#374151")
            g1c = "#16a34a" if e1=="" else "#dc2626" if e1=="GPS NA" else "#d97706"
            bg  = "#fff" if i%2 else "#f9fafb"
            return (bg, [
                str(i),
                str(r.get("shipment_no","—")),
                str(r.get("vehicle_no","—")),
                str(r.get("driver_name","—"))[:14],
                str(r.get("route_name","—"))[:16],
                str(r.get("source_name","—"))[:14],
                str(r.get("destination_name","—"))[:14],
                str(r.get("shipment_method","—")),
                str(r.get("run_date","—"))[:16],
                (ts_txt, ts_col),
                (get_gps_status(e1), g1c),
                get_gps_status(e2),
                get_gps_status(e3),
            ])

    elif table == T_LIVE:
        cols = ["#","Shipment No","Vehicle No","Status","Halted Since","Location","ETA","Delay"]
        def row_fn(i, r):
            vs  = r.get("vehicle_status_current","—")
            sc  = "#dc2626" if vs=="Stopped" else "#16a34a"
            bg  = "#fff" if i%2 else "#f9fafb"
            return (bg, [
                str(i),
                str(r.get("shipment_no","—")),
                str(r.get("vehicle_no","—")),
                (vs, sc),
                str(r.get("last_halt_time_current","—"))[:16],
                str(r.get("last_address_current","—"))[:30],
                str(r.get("eta","—"))[:16],
                str(r.get("delay_hr","—")),
            ])

    elif table == T_TRIG_LOG:
        cols = ["#","Shipment No","Vehicle","Alert Type","Level","Violation (min)","Location","Start","End"]
        def row_fn(i, r):
            lvl = str(r.get("level",""))
            lc  = "#dc2626" if lvl=="1" else "#d97706" if lvl=="2" else "#374151"
            bg  = "#fff" if i%2 else "#f9fafb"
            return (bg, [
                str(i),
                str(r.get("shipment_no","—")),
                str(r.get("vehicle_name","—")),
                str(r.get("alert_type","—")),
                (f"L{lvl}", lc),
                str(round(r.get("voilation_time",0) or 0, 1)),
                str(r.get("location","—"))[:28],
                str(r.get("start_time","—"))[:16],
                str(r.get("end_time","—"))[:16],
            ])

    elif table == T_DELAY:
        cols = ["#","Trip ID","Vehicle","Route","Location","Delay Reason","Incident Date","Delay(min)"]
        def row_fn(i, r):
            bg = "#fff" if i%2 else "#f9fafb"
            return (bg, [
                str(i),
                str(r.get("trip_id","—")),
                str(r.get("trip_vehicle_no", r.get("vehicle_no","—"))),
                str(r.get("route_name","—"))[:16],
                str(r.get("location_name","—"))[:18],
                str(r.get("delay_reason","—")),
                str(r.get("incident_date","—")),
                str(r.get("total_delay_in_min","—")),
            ])

    elif table == T_HALT:
        cols = ["#","Shipment No","Vehicle","Source","Destination","Method",
                "Halt Start","Halt End","Duration(min)","Location"]
        def row_fn(i, r):
            dur = r.get("duration", 0) or 0
            dc  = "#dc2626" if dur>=60 else "#d97706" if dur>=30 else "#16a34a"
            bg  = "#fff" if i%2 else "#f9fafb"
            return (bg, [
                str(i),
                str(r.get("shipment_no","—")),
                str(r.get("vehicle_name","—")),
                str(r.get("source_name","—"))[:12],
                str(r.get("destination_name","—"))[:12],
                str(r.get("shipment_method","—")),
                str(r.get("halt_start_time","—"))[:16],
                str(r.get("halt_end_time","—"))[:16],
                (str(round(dur,1)), dc),
                str(r.get("location","—"))[:28],
            ])

    elif table == T_STOPS:
        cols = ["#","Vehicle","Location","Seq","Scheduled Arrival","Scheduled Dep","POD","POA"]
        def row_fn(i, r):
            pod = r.get("pod_status",0)
            poa = r.get("poa_status",0)
            bg  = "#fff" if i%2 else "#f9fafb"
            return (bg, [
                str(i),
                str(r.get("vehicle_no_prm","—")),
                str(r.get("location_name","—"))[:22],
                str(r.get("location_sequence","—")),
                str(r.get("schedule_time_arrival","—"))[:16],
                str(r.get("schedule_time_departure","—"))[:16],
                ("✅ Done" if pod==1 else "⏳ Pending", "#16a34a" if pod==1 else "#d97706"),
                ("✅ Done" if poa==1 else "⏳ Pending", "#16a34a" if poa==1 else "#d97706"),
            ])

    else:
        # Fallback: show first 8 fields
        if not data:
            cols = ["No data"]; row_fn = lambda i,r: ("#fff", ["—"])
        else:
            fkeys = [k for k in list(data[0].keys())[:8] if not k.startswith("_")]
            cols  = ["#"] + [k.replace("_"," ").title() for k in fkeys]
            def row_fn(i, r):
                return ("#fff" if i%2 else "#f9fafb",
                        [str(i)] + [str(r.get(k,"—"))[:20] for k in fkeys])

    # Build rows HTML
    td_s = 'style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#374151;font-size:12px;white-space:nowrap"'
    rows_html = ""
    for i, rec in enumerate(data, 1):
        bg, cells = row_fn(i, rec)
        tds = ""
        for cell in cells:
            if isinstance(cell, tuple):
                txt, color = cell
                tds += f'<td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:{color};font-weight:500;font-size:12px;white-space:nowrap">{txt}</td>'
            else:
                tds += f'<td {td_s}>{cell}</td>'
        rows_html += f'<tr style="background:{bg}">{tds}</tr>'

    if not data:
        nc = len(cols)
        rows_html = f'<tr><td colspan="{nc}" style="text-align:center;padding:20px;color:#9ca3af">No records found for the applied filters</td></tr>'

    th_s = 'style="padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:#fff;background:#1e3a5f;white-space:nowrap"'
    h_html = "".join(f'<th {th_s}>{c}</th>' for c in cols)

    # Filter pills
    pills = "".join(
        f'<span style="display:inline-block;background:#eff6ff;border:1px solid #bfdbfe;'
        f'border-radius:20px;padding:2px 8px;font-size:10px;color:#1e40af;margin:2px">'
        f'{lbl}: <b>{val}</b></span>'
        for lbl, val in filters_found
    ) or "—"

    # Summary cards
    tbl_lbl = table.replace("_"," ").title()
    summary = (
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px">'
        + metric_box("Records found", str(count), "#1e3a5f", tbl_lbl)
        + metric_box("Period", period[:22], "#374151")
        + f'<div style="background:#f9fafb;border-radius:11px;padding:12px 14px">'
          f'<div style="font-size:11px;color:#6b7280;margin-bottom:3px">Filters applied</div>'
          f'<div style="font-size:11px;margin-top:4px">{pills}</div></div>'
        + '</div>'
    )

    table_html = (
        '<div style="overflow-x:auto;border-radius:10px;border:1px solid #e5e7eb">'
        '<table style="width:100%;border-collapse:collapse;min-width:600px">'
        f'<thead><tr>{h_html}</tr></thead><tbody>{rows_html}</tbody>'
        '</table></div>'
    )

    footer = (
        f'<div style="font-size:11px;color:#9ca3af;margin-top:8px">'
        f'Showing {count} records · {period} · {tbl_lbl}'
        f'</div>'
    )

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:1000px">'
        + section_title(f"{tbl_lbl} — {count} records")
        + summary + table_html + footer
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SERVICE
# ─────────────────────────────────────────────────────────────────────────────

class ChatService:
    def __init__(self):
        self.aggregator = BluedartAggregator()
        self.detector   = IntentDetector()
        self.ai         = OpenAIClient()

    async def process(self, request: ChatRequest) -> ChatResponse:
        start   = time.time()
        history = [m.dict() for m in (request.history or [])]
        sid     = request.session_id or "default"

        # ── Step 1: Resolve date-only follow-up ──────────────────────────────
        msg, was_reconstructed = resolve_date_followup(request.message, history)
        if was_reconstructed:
            print(f"   [ctx] Reconstructed: {msg}")
        msg_l = msg.lower()

        # ── Step 2: Intent + ID extraction ───────────────────────────────────
        intent = self.detector.classify_intent(msg)
        ids    = extract_ids(msg)
        raw_filters, filters_found = extract_filters(msg)

        # ── Step 3: Session context merge ────────────────────────────────────
        has_own_ids = any(ids.get(k) for k in ("shipment_no","vehicle_no","imei"))
        ctx_filters = SESSION.get_filters(sid)
        ctx_ids     = SESSION.get_ids(sid)
        ctx_date_f, ctx_date_t = SESSION.get_date(sid)

        # Merge: session filters + current (current overrides)
        merged_filters = {**ctx_filters, **{k:v for k,v in raw_filters.items() if not k.startswith("_")}}
        merged_ids     = {**ctx_ids, **{k:v for k,v in ids.items() if v}}

        # Save to session
        SESSION.update(sid, raw_filters, ids,
                       *parse_date(msg),
                       new_query_has_ids=has_own_ids)

        print(f"\n📨 [{sid}] {msg}")
        print(f"   intent={intent} filters={filters_found} ids={merged_ids}")

        t1         = time.time()
        query_type = "GENERAL"
        mongo_col  = ""
        mongo_q    = ""

        # ════════════════════════════════════════════════════════════════════
        # ROUTING — in priority order
        # ════════════════════════════════════════════════════════════════════

        # ── R1: Single shipment lookup ────────────────────────────────────────
        if merged_ids.get("shipment_no") and intent in (
            Intent.STATUS_CHECK, Intent.LOCATE, Intent.ETA_QUERY, Intent.GENERAL_QUERY
        ) and not any(k in msg_l for k in ["alert","trigger","delay","der","late","halt","stop","investigate"]):
            query_type = "TRIP"
            shp  = merged_ids["shipment_no"]
            data = await self.aggregator.get_trip_by_shipment(shp)
            reply = format_trip_status(data)
            mongo_col = T_TRIP
            mongo_q   = f'db.{T_TRIP}.findOne({{"shipment_no":"{shp}","group_id":"0041"}})'
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_TRIP, mongo_q, round(time.time()-t1,2), bool(ctx_filters))

        # ── R2: Stopped / halted vehicles (LIVE — no date) ────────────────────
        STOPPED_KWS = [
            "stopped for","stopped more","halted for","halted more","halt more",
            "max halt","halt duration","halt time","vehicles stopped","stopped vehicles",
            "stopped for less","halted less","stopped less","stopped for",
        ]
        if any(kw in msg_l for kw in STOPPED_KWS):
            query_type = "STOPPED"
            data       = await _fetch_stopped(msg)
            sev        = _detect_severity(msg)
            reply      = format_stopped_vehicles(data, show_all=("full list" in msg_l or "sab dikhao" in msg_l), severity_filter=sev)
            hint       = data.get("_mongo_hint", {})
            return self._resp(request, reply, intent, query_type, merged_ids,
                              hint.get("collection", T_LIVE), hint.get("query",""),
                              round(time.time()-t1,2), bool(ctx_filters))

        # ── R3: Alert query ───────────────────────────────────────────────────
        ALERT_KWS = ["alert","trigger","s180","unscheduled halt","speeding","violation",
                     "voilation","flag","qrt","alarm","warning","koi alert","any alert",
                     "triggered","pe koi","trigger hua"]
        if any(kw in msg_l for kw in ALERT_KWS) or intent == Intent.ALERT_QUERY:
            query_type = "ALERT"
            shp = merged_ids.get("shipment_no")
            veh = merged_ids.get("vehicle_no")

            # Build date-filtered alert query if date present
            date_from, date_to = parse_date(msg)
            if not date_from: date_from, date_to = ctx_date_f, ctx_date_t

            if shp or veh or date_from or filters_found:
                # Use generic engine for filtered alert queries
                extra = {k:v for k,v in raw_filters.items() if k in ("alert_type","level") and not k.startswith("_")}
                cond  = {"group_id": GROUP_ID, "status": STATUS_ON}
                if shp: cond["shipment_no"] = shp
                if veh: cond["vehicle_name"] = veh
                if date_from: cond["create_date"] = {"$gte": date_from, "$lte": date_to}
                cond.update(extra)
                data  = await _generic_fetch(cond, T_TRIG_LOG)
                total = len(data)
                crit  = sum(1 for a in data if str(a.get("level",""))=="1")
                high  = sum(1 for a in data if str(a.get("level",""))=="2")
                result_dict = {"trigger_logs": data, "summary": {"total": total, "critical_l1": crit, "high_l2": high}}
                reply = format_alerts(result_dict)
                return self._resp(request, reply, intent, query_type, merged_ids,
                                  T_TRIG_LOG, f"db.{T_TRIG_LOG}.find({cond}).sort({{create_date:-1}}).limit(20)",
                                  round(time.time()-t1,2), bool(ctx_filters))
            else:
                # No ID, no date, no specific filter → ask date
                reply = date_clarification_html(filters_found, msg)
                return self._resp(request, reply, intent, "GENERAL", merged_ids,
                                  T_TRIG_LOG, "", round(time.time()-t1,2), False)

        # ── R4: Delay query ───────────────────────────────────────────────────
        DELAY_KWS = ["delay","late","der","delayed","hold","kyu ruka","why stopped",
                     "delay report","kitna late","how late","delay reason","total_delay"]
        if any(kw in msg_l for kw in DELAY_KWS) or intent == Intent.DELAY_QUERY:
            query_type = "DELAY"
            shp  = merged_ids.get("shipment_no")
            data = await self.aggregator.get_trip_delays(shipment_no=shp)
            reply = format_delays(data)
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_DELAY, f'db.{T_DELAY}.find({{"trip_id":"{shp}","group_id":"0041"}}).sort({{entry_date:-1}})',
                              round(time.time()-t1,2), bool(ctx_filters))

        # ── R5: Vehicle locate (vehicle in msg, no shipment) ──────────────────
        if merged_ids.get("vehicle_no") and not merged_ids.get("shipment_no"):
            if not any(k in msg_l for k in ["alert","trigger","delay","der","late"]):
                query_type = "TRIP"
                veh  = merged_ids["vehicle_no"]
                data = await fetch_vehicle_location(veh)
                if "error" in data and "trip_core" not in data:
                    reply = data.get("error", f"No active trip for {veh}")
                else:
                    reply = format_trip_status(data)
                hint = data.get("_mongo_hint", {})
                return self._resp(request, reply, intent, query_type, merged_ids,
                                  hint.get("collection", T_TRIP), hint.get("query",""),
                                  round(time.time()-t1,2), bool(ctx_filters))

        # ── R6: Running trips (LIVE) ──────────────────────────────────────────
        RUNNING_KWS = ["running trip","active trip","current trip","abhi chal","chal raha",
                       "chal rahe","live trip","trips running","trip_status 1"]
        if any(kw in msg_l for kw in RUNNING_KWS):
            query_type = "BULK"
            data  = await fetch_running_trips(limit=LIMIT)
            reply = format_bulk_report(data, query_msg=msg)
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_TRIP, data.get("_mongo_hint",{}).get("query",""),
                              round(time.time()-t1,2), False)

        # ── R7: Analytics (max trips — auto last month) ───────────────────────
        ANALYTICS_KWS = ["max trips","most trips","highest trips","sabse zyada trips","top vehicle",
                         "maximum trips","vehicle with most"]
        if any(kw in msg_l for kw in ANALYTICS_KWS):
            query_type = "BULK"
            data  = await fetch_max_trips_vehicle()
            reply = format_max_trips(data)
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_TRIP, data.get("_mongo_hint",{}).get("query",""),
                              round(time.time()-t1,2), False)

        # ── R8: Transshipment ─────────────────────────────────────────────────
        TRANSSHIP_KWS = ["transshipment","trans shipment","transship","tranship"]
        if any(kw in msg_l for kw in TRANSSHIP_KWS):
            query_type = "BULK"
            date_from, date_to = parse_date(msg)
            if not date_from:
                date_from = datetime.now().strftime("%Y-%m-%d 00:00:00")
                date_to   = datetime.now().strftime("%Y-%m-%d 23:59:59")
            cond  = {"group_id": GROUP_ID, "status": STATUS_ON,
                     "transshipment_date": {"$gte": date_from, "$lte": date_to}}
            data  = await _generic_fetch(cond, T_TRIP)
            reply = _build_html_response(data, T_TRIP, cond, [("Type","Transshipment")], date_from, date_to)
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_TRIP, f"db.{T_TRIP}.find({cond}).limit({LIMIT})",
                              round(time.time()-t1,2), False)

        # ── R9: Delivery stops ────────────────────────────────────────────────
        STOPS_KWS = ["stop","waypoint","delivery point","pod","poa","kitne stop","how many stop"]
        if any(kw in msg_l for kw in STOPS_KWS) or intent == Intent.STOPS_QUERY:
            query_type = "GENERAL"
            shp = merged_ids.get("shipment_no")
            if not shp:
                reply = "Shipment number dijiye — stops dekhne ke liye. Example: 'stops for 11495287'"
            else:
                core = await self.aggregator.get_trip_by_shipment(shp)
                trip = core.get("trip_detail", {})
                if isinstance(trip, list): trip = trip[0] if trip else {}
                veh  = str(trip.get("vehicle_no","")) if isinstance(trip,dict) else ""
                stops_data = await self.aggregator.get_trip_stops(vehicle_no=veh)
                stops = stops_data.get("stops_detail",[])
                data  = stops
                reply = _build_html_response(data, T_STOPS, {}, [("Shipment No", shp)])
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_STOPS, "", round(time.time()-t1,2), bool(ctx_filters))

        # ── R10: Close trips today ────────────────────────────────────────────
        CLOSE_KWS = ["aaj kitne trips close","kitne trips close","trips close","close trips today",
                     "trips closed today","how many trips closed","aaj close","closed today","trips completed today"]
        if any(kw in msg_l for kw in CLOSE_KWS):
            query_type = "BULK"
            date_from, date_to = parse_date(msg)
            if not date_from:
                date_from = datetime.now().strftime("%Y-%m-%d 00:00:00")
                date_to   = datetime.now().strftime("%Y-%m-%d 23:59:59")
            cond  = {"group_id": GROUP_ID, "status": STATUS_ON, "trip_status": 0,
                     "run_date": {"$gte": date_from, "$lte": date_to}}
            data  = await _generic_fetch(cond, T_TRIP)
            reply = _build_html_response(data, T_TRIP, cond, [("Status","Closed")], date_from, date_to)
            return self._resp(request, reply, intent, query_type, merged_ids,
                              T_TRIP, f"db.{T_TRIP}.find({cond}).limit({LIMIT})",
                              round(time.time()-t1,2), False)

        # ── R11: GENERIC FILTER QUERY ─────────────────────────────────────────
        # Handles ALL combinations — single table OR cross-table join
        if merged_filters or filters_found:
            query_type = "BULK"

            # Is this a live query?
            live = is_live_query(msg)

            # Date logic
            date_from, date_to = parse_date(msg)
            if not date_from and not live:
                date_from, date_to = ctx_date_f, ctx_date_t

            # If date missing and not live → ask date
            if not date_from and not live:
                reply = date_clarification_html(filters_found, msg)
                return self._resp(request, reply, intent, "GENERAL", merged_ids,
                                  T_TRIP, "", round(time.time()-t1,2), False)

            # ── Cross-table join detection ────────────────────────────────────
            if is_cross_table_query(merged_filters, msg):
                # Build trip_detail conditions (removing alert/secondary fields)
                TRIP_ONLY_FIELDS = {
                    "shipment_method","region_code","trip_status","exception_common_backend",
                    "exception_common_backend_2","exception_common_backend_3","gps_vendor_name",
                    "imei_no2","imei_no3","imei_no_type","actual_source_departure_time",
                    "actual_destination_arrival_time","close_remarks","ata_source",
                    "is_fleet_master","trip_type","vehicle_no","source_code",
                }
                trip_only_filters = {k:v for k,v in merged_filters.items() if k in TRIP_ONLY_FIELDS}
                trip_cond = build_conditions(trip_only_filters, merged_ids, T_TRIP, date_from, date_to)
                # For live join: no date filter on trip_detail — get active trips only
                if live:
                    trip_cond["trip_status"] = 1
                    trip_cond.pop("run_date", None)
                print(f"   [cross-table] trip_cond={trip_cond}")
                reply = await run_cross_table_query(
                    trip_conditions = trip_cond,
                    raw_filters     = merged_filters,
                    filters_found   = filters_found,
                    msg             = msg,
                    date_from       = date_from,
                    date_to         = date_to,
                )
                return self._resp(request, reply, intent, query_type, merged_ids,
                                  "multi-table", "", round(time.time()-t1,2), bool(ctx_filters))

            # ── Single table query ────────────────────────────────────────────
            table   = detect_table(intent, raw_filters, merged_ids, msg)
            cond    = build_conditions(merged_filters, merged_ids, table, date_from, date_to)
            print(f"   [generic] table={table} cond={cond}")
            data    = await _generic_fetch(cond, table)
            reply   = _build_html_response(data, table, cond, filters_found, date_from, date_to)
            mongo_q = f"db.{table}.find({cond}).sort({{{SORT_FIELD.get(table,'_id')}:-1}}).limit({LIMIT})"
            return self._resp(request, reply, intent, query_type, merged_ids,
                              table, mongo_q, round(time.time()-t1,2), bool(ctx_filters))

        # ── R12: GPT fallback for everything else ─────────────────────────────
        data         = await self.aggregator.fetch_for_query(intent=intent, ids=merged_ids)
        fetch_time   = round(time.time()-t1, 2)
        direct_reply = format_response(data, query_type, query_msg=msg)

        if direct_reply:
            hint    = data.get("_mongo_hint", {})
            mongo_q = hint.get("query","")
            reply   = direct_reply
            ai_time = 0.0
        else:
            def _trim(d):
                out = {}
                for k,v in d.items():
                    if k.startswith("_"): continue
                    if isinstance(v,list) and len(v)>10: out[k+"_top10"]=v[:10]; out[k+"_total"]=len(v)
                    elif isinstance(v,list): out[k]=v
                    else: out[k]=v
                return out
            t2 = time.time()
            reply, mongo_col, mongo_q = await self.ai.analyze(
                user_query=msg, context_data=_trim(data), history=history,
                intent=intent, extracted_ids={k:v for k,v in merged_ids.items() if v},
            )
            ai_time = round(time.time()-t2, 2)

        total = round(time.time()-start, 2)
        print(f"   fetch:{fetch_time}s ai:{ai_time}s total:{total}s")
        services = [
            ServiceCallInfo(service=k,
                            status="error" if isinstance(v,dict) and "error" in v else "ok",
                            data_keys=list(v.keys()) if isinstance(v,dict) else [])
            for k,v in data.items() if not k.startswith("_")
        ]
        return ChatResponse(
            reply=reply, intent=intent, query_type=query_type,
            extracted_ids={k:v for k,v in merged_ids.items() if v},
            services_called=services,
            fetch_time_seconds=fetch_time, ai_time_seconds=ai_time,
            total_time_seconds=total,
            mongo_query=mongo_q, mongo_collection=mongo_col,
            context_used=bool(ctx_filters), session_id=request.session_id,
        )

    def _resp(self, req, reply, intent, query_type, ids, collection, mongo_q, ft, ctx_used):
        total = 0  # caller can set if needed
        return ChatResponse(
            reply=reply, intent=intent, query_type=query_type,
            extracted_ids={k:v for k,v in ids.items() if v},
            services_called=[ServiceCallInfo(service=collection, status="ok", data_keys=[])],
            fetch_time_seconds=ft, ai_time_seconds=0.0,
            total_time_seconds=ft,
            mongo_query=mongo_q, mongo_collection=collection,
            context_used=ctx_used, session_id=req.session_id,
        )
