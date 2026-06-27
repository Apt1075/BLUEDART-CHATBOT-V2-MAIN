"""
app/core/response_formatter.py — Bluedart AI Chatbot v2
100% inline styles — no classes, no <style> tag.
Python 3.11 compatible — no backslash in f-strings.
All responses return HTML string — UI just uses innerHTML.
"""

from datetime import datetime
from typing import Any, Optional
from collections import defaultdict


def calc_halt_mins(halt_time_str: str) -> int:
    if not halt_time_str:
        return 0
    try:
        halt_dt = datetime.strptime(str(halt_time_str).strip(), "%Y-%m-%d %H:%M:%S")
        return max(0, int((datetime.now() - halt_dt).total_seconds() / 60))
    except Exception:
        return 0

def format_halt_duration(mins: int) -> str:
    if mins <= 0: return "N/A"
    return str(mins // 60) + "h " + str(mins % 60) + "m"

fmt_halt = format_halt_duration

def get_severity(mins: int) -> str:
    if mins <= 0:    return "-"
    if mins >= 1440: return "CRITICAL >24h"
    if mins >= 600:  return "HIGH 10-24h"
    if mins >= 300:  return "HIGH 5-10h"
    if mins >= 180:  return "MEDIUM 3-5h"
    return "LOW <3h"

severity = get_severity

def get_gps_status(exception: str) -> str:
    if exception == "GPS NA":          return "GPS NA"
    if exception == "No Connectivity": return "No Conn"
    if exception == "":                return "Active"
    return exception or "-"

gps_label = get_gps_status

def trunc(s: Any, n: int = 20) -> str:
    s = str(s or "")
    return s[:n] if len(s) > n else s


# ── Shared style strings ───────────────────────────────────────────────────

BD_BASE = "display:inline-flex;align-items:center;gap:3px;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600"
BD_DNG  = 'style="' + BD_BASE + ';background:#fef2f2;color:#dc2626"'
BD_WARN = 'style="' + BD_BASE + ';background:#fffbeb;color:#d97706"'
BD_SUCC = 'style="' + BD_BASE + ';background:#f0fdf4;color:#16a34a"'
BD_INFO = 'style="' + BD_BASE + ';background:#eff6ff;color:#2563eb"'
BD_GRAY = 'style="' + BD_BASE + ';background:#f3f4f6;color:#374151"'

S_CARD  = 'style="background:#fff;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;max-width:480px;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.07)"'
S_HDR   = 'style="padding:13px 16px 11px;display:flex;align-items:center;gap:10px;background:#1e3a5f"'
S_HICO  = 'style="width:36px;height:36px;border-radius:9px;background:rgba(255,255,255,.18);display:flex;align-items:center;justify-content:center;flex-shrink:0"'
S_HICO_I= 'style="font-size:19px;color:#fff"'
S_HTXT  = 'style="flex:1"'
S_HTTL  = 'style="font-size:15px;font-weight:600;color:#fff;line-height:1.2"'
S_HSUB  = 'style="font-size:11px;color:rgba(255,255,255,.6);margin-top:2px"'
S_HBDG  = 'style="display:flex;gap:6px;margin-left:auto;flex-shrink:0"'
S_ROW   = 'style="display:flex;align-items:center;padding:9px 14px;gap:10px;border-bottom:1px solid #f3f4f6"'
S_ROWL  = 'style="display:flex;align-items:center;padding:9px 14px;gap:10px"'
S_RICO  = 'style="font-size:15px;color:#9ca3af;width:18px;flex-shrink:0"'
S_RLBL  = 'style="font-size:12px;color:#6b7280;width:88px;flex-shrink:0"'
S_RVAL  = 'style="font-size:13px;color:#111827;font-weight:500;flex:1;line-height:1.4"'


def bd(text: str, sty: str) -> str:
    return '<span ' + sty + '>' + text + '</span>'

def bd_sev(sev: str, text: str) -> str:
    if "CRITICAL" in sev: return bd(text, BD_DNG)
    if "HIGH"     in sev: return bd(text, BD_WARN)
    if "MEDIUM"   in sev: return bd(text, BD_INFO)
    return bd(text, BD_SUCC)

def bd_gps(exc: str) -> str:
    lbl = get_gps_status(exc)
    if exc == "":        return bd(lbl, BD_SUCC)
    if exc == "GPS NA":  return bd(lbl, BD_DNG)
    return bd(lbl, BD_WARN)

def card_row(icon: str, label: str, value: str, last: bool = False) -> str:
    rs = S_ROWL if last else S_ROW
    return (
        '<div ' + rs + '>'
        '<i class="ti ' + icon + '" ' + S_RICO + '></i>'
        '<span ' + S_RLBL + '>' + label + '</span>'
        '<span ' + S_RVAL + '>' + value + '</span>'
        '</div>'
    )

def section_title(text: str) -> str:
    return '<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#6b7280;margin-bottom:12px">' + text + '</div>'

def metric_box(label: str, value: str, color: str = "#111827", sub: str = "") -> str:
    sub_html = '<div style="font-size:11px;color:#9ca3af;margin-top:2px">' + sub + '</div>' if sub else ""
    return (
        '<div style="background:#f9fafb;border-radius:11px;padding:12px 13px">'
        '<div style="font-size:11px;color:#6b7280;margin-bottom:3px">' + label + '</div>'
        '<div style="font-size:21px;font-weight:600;color:' + color + '">' + str(value) + '</div>'
        + sub_html +
        '</div>'
    )

def html_table(columns: list, rows_html: str) -> str:
    th_style = 'style="padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:#fff;white-space:nowrap;background:#1e3a5f"'
    headers  = "".join('<th ' + th_style + '>' + c + '</th>' for c in columns)
    return (
        '<div style="overflow-x:auto;border-radius:10px;border:1px solid #e5e7eb;margin-top:12px">'
        '<table style="width:100%;border-collapse:collapse;font-size:12px;min-width:500px">'
        '<thead><tr>' + headers + '</tr></thead>'
        '<tbody>' + rows_html + '</tbody>'
        '</table></div>'
    )

def td(val: str, bold: bool = False, color: str = "") -> str:
    fw = "font-weight:600;" if bold else ""
    cl = "color:" + color + ";" if color else ""
    return '<td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#374151;' + fw + cl + '">' + str(val) + '</td>'

def links_row(links: list) -> str:
    a_sty = 'style="color:#2563eb;text-decoration:none;font-size:12px"'
    items = '&nbsp;&nbsp;'.join('<a href="' + l["url"] + '" target="_blank" ' + a_sty + '>' + l["label"] + '</a>' for l in links)
    return '<div style="margin-top:10px;color:#6b7280">' + items + '</div>'

REPORT_LINKS = [
    {"label": "📊 Full Report",     "url": "https://cv18.secutrak.in/cv/specific/bluedart/Report/TripReport"},
    {"label": "⏱ Delay Dashboard", "url": "https://cv18.secutrak.in/cv/specific/bluedart/Delay-Dashboard"},
]


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 1: TRIP CARD
# ─────────────────────────────────────────────────────────────────────────────

def format_trip_status(data: dict) -> str:
    core = data.get("trip_core", {})
    trip = core.get("trip_detail", {})
    live = core.get("live_status", {})

    if isinstance(trip, list) and trip: trip = trip[0]
    if isinstance(live, list) and live: live = live[0]
    if not isinstance(trip, dict) or not trip:
        return "No trip data found. Please check the shipment number."

    halt_time = None
    halt_mins = 0
    if isinstance(live, dict):
        for key in ["last_halt_time1", "last_halt_time_current"]:
            ht = live.get(key)
            if ht:
                halt_time = ht; halt_mins = calc_halt_mins(ht); break

    gps_lat = gps_lng = gps_speed = gps_vendor_val = ""
    if isinstance(live, dict):
        ld = live.get("gps_current", live.get("last_data_current", {}))
        if isinstance(ld, dict):
            def f(v): return v[0] if isinstance(v, list) and v else v
            gps_lat        = str(f(ld.get("latitudeLR",  "")) or "")
            gps_lng        = str(f(ld.get("longitudeLR", "")) or "")
            gps_speed      = str(f(ld.get("speedLR",     "")) or "")
            gps_vendor_val = str(f(ld.get("io8LR",       "")) or trip.get("gps_vendor_name", ""))

    eta      = str(live.get("eta",                   "N/A") if isinstance(live, dict) else "N/A")
    eta_hrs  = str(live.get("eta_hrs",               "")    if isinstance(live, dict) else "")
    etd      = str(live.get("etd",                   "")    if isinstance(live, dict) else "")
    delay    = live.get("delay_hr",                  0)     if isinstance(live, dict) else 0
    v_status = str(live.get("vehicle_status_current","N/A") if isinstance(live, dict) else "N/A")
    address  = str(live.get("last_address_current",  "")    if isinstance(live, dict) else "")
    ts_raw   = trip.get("trip_status", 1)
    ts_txt   = {1: "ACTIVE", 0: "CLOSED", 2: "CANCELLED"}.get(ts_raw, "UNKNOWN")
    gps_exc  = trip.get("exception_common_backend", "")
    sev      = get_severity(halt_mins)

    ts_badge  = bd(ts_txt, BD_SUCC if ts_txt == "ACTIVE" else BD_GRAY)
    vs_icon   = "ti-alert-triangle" if v_status == "Stopped" else "ti-check"
    vs_sty    = BD_DNG if v_status == "Stopped" else BD_SUCC if v_status == "running" else BD_WARN
    vs_badge  = bd('<i class="ti ' + vs_icon + '"></i> ' + v_status, vs_sty)

    drv_html  = str(trip.get("driver_name","")) + ' <span style="font-weight:400;color:#9ca3af">' + str(trip.get("driver_mobile","")) + '</span>'
    eta_html  = eta[:16] + ' <span style="color:#9ca3af;font-size:11px">' + eta_hrs + '</span>'
    gps_html  = bd_gps(gps_exc) + ' <span style="color:#9ca3af;font-size:11px;margin-left:4px">' + gps_vendor_val + '</span>'
    src_dst   = trunc(str(trip.get("source_name","")),22) + " → " + trunc(str(trip.get("destination_name","")),22)

    rows  = card_row("ti-car",          "Vehicle",       str(trip.get("vehicle_no","")))
    rows += card_row("ti-user",         "Driver",        drv_html)
    rows += card_row("ti-route",        "Route",         trunc(str(trip.get("route_name","")),32))
    rows += card_row("ti-arrow-right",  "Source → Dest", src_dst)
    rows += card_row("ti-run",          "Method",        str(trip.get("shipment_method","")))
    rows += card_row("ti-building",     "Region/Fleet",  str(trip.get("region_code","")) + " &nbsp;·&nbsp; " + str(trip.get("fleet_no","")))

    if halt_mins > 0:
        rows += card_row("ti-clock-stop",   "Halt",     bd_sev(sev, fmt_halt(halt_mins) + " · " + sev))
    
    # Halt details from live status
    stopped_gt_2h = live.get("stopped_gt_2h", 0) if isinstance(live, dict) else 0
    stopped_gt_5h = live.get("stopped_gt_5h", 0) if isinstance(live, dict) else 0
    stopped_duration = str(live.get("stopped_duration", "") if isinstance(live, dict) else "")
    
    halt_info_parts = []
    if stopped_duration and stopped_duration != "0" and stopped_duration != "N/A":
        halt_info_parts.append(f"Duration: {stopped_duration}")
    if stopped_gt_2h:
        halt_info_parts.append("Stopped > 2h")
    if stopped_gt_5h:
        halt_info_parts.append("Stopped > 5h")
    if halt_info_parts:
        rows += card_row("ti-clock-stop", "Halt Details", " · ".join(halt_info_parts))

    if etd:
        rows += card_row("ti-calendar-clock", "ETD", etd[:16])

    rows += card_row("ti-calendar-clock","ETA",         eta_html)

    # Delay details from live status
    delaying_sta = str(live.get("delaying_sta", "") if isinstance(live, dict) else "")
    delay_hours_2_to_5h = live.get("delay_hours_2_to_5h", 0) if isinstance(live, dict) else 0
    critical_hours_gt_5h = live.get("critical_hours_gt_5h", 0) if isinstance(live, dict) else 0

    delay_info_parts = []
    if delay and int(delay) > 0:
        delay_info_parts.append(f"{int(delay)}h overdue")
    if delaying_sta:
        delay_info_parts.append(f"At: {delaying_sta}")
    if delay_hours_2_to_5h:
        delay_info_parts.append("Delay 2-5h")
    if critical_hours_gt_5h:
        delay_info_parts.append("Critical > 5h")
    if delay_info_parts:
        rows += card_row("ti-alert-circle", "Delay Details", bd(" · ".join(delay_info_parts), BD_DNG))
    if address:
        rows += card_row("ti-map-pin",      "Location", '<span style="font-size:12px">' + trunc(address,52) + '</span>')
    if gps_lat:
        rows += card_row("ti-location",     "GPS Coords", gps_lat + ", " + gps_lng + ' <span style="color:#9ca3af;font-size:11px">· ' + gps_speed + ' kmh</span>')
    rows += card_row("ti-satellite",    "GPS Status",    gps_html, last=True)

    shp = str(trip.get("shipment_no",""))
    tra = trunc(str(trip.get("transporter_name","") or "—"),30)
    rdt = str(trip.get("run_date",""))[:10]

    return (
        '<div ' + S_CARD + '>'
          '<div ' + S_HDR + '>'
            '<div ' + S_HICO + '><i class="ti ti-truck" ' + S_HICO_I + '></i></div>'
            '<div ' + S_HTXT + '>'
              '<div ' + S_HTTL + '>Trip ' + shp + '</div>'
              '<div ' + S_HSUB + '>' + tra + ' &nbsp;·&nbsp; Run: ' + rdt + '</div>'
            '</div>'
            '<div ' + S_HBDG + '>' + ts_badge + vs_badge + '</div>'
          '</div>'
          '<div>' + rows + '</div>'
        '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 1B: VEHICLE SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

def format_vehicle_snapshot(data: dict) -> str:
    vehicle = data.get("vehicle_gps", {})
    trip = data.get("trip_core", {}).get("trip_detail", {}) if isinstance(data.get("trip_core"), dict) else {}

    if isinstance(vehicle, list) and vehicle:
        vehicle = vehicle[0]
    if not isinstance(vehicle, dict):
        vehicle = {}

    def f(value: Any) -> Any:
        return value[0] if isinstance(value, list) and value else value

    gps = vehicle.get("gps_current", vehicle.get("last_data_current", {}))
    if not isinstance(gps, dict):
        gps = {}

    latitude = str(f(gps.get("latitudeLR", "")) or "-")
    longitude = str(f(gps.get("longitudeLR", "")) or "-")
    speed = str(f(gps.get("speedLR", "")) or "-")
    device_time = str(f(gps.get("deviceDatetimeLR", vehicle.get("device_time_current", ""))) or "-")[:16]
    server_time = str(f(gps.get("serverDatetimeLR", vehicle.get("update_time", vehicle.get("created_at", "")))) or "-")[:16]
    halt_time = str(f(gps.get("lastHaltTimeLR", "")) or "-")[:16]
    signal = str(f(gps.get("sigStrTLR", "")) or "-")
    voltage = str(f(gps.get("suplyVoltageLR", "")) or "-")
    fix = str(f(gps.get("fixLR", "")) or "-")
    imei = str(vehicle.get("imei_current") or trip.get("imei_no") or f(gps.get("imei", "")) or "-")
    imei_status = str(vehicle.get("imei_current_status") or "-")
    vehicle_no = str(vehicle.get("vehicle_number") or trip.get("vehicle_no") or "-")
    vehicle_id = str(vehicle.get("vehicle_id") or "-")
    location = str(f(gps.get("cellNameLR", vehicle.get("last_address", ""))) or "-")

    cards = (
        '<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px">'
        + metric_box("Vehicle", vehicle_no, "#111827", "ID " + vehicle_id)
        + metric_box("IMEI", imei, "#111827", imei_status)
        + metric_box("Last Update", server_time, "#111827", "Device " + device_time)
        + '</div>'
    )

    rows = (
        card_row("ti-map-pin", "Location", location)
        + card_row("ti-route", "Coords", latitude + ", " + longitude)
        + card_row("ti-dashboard", "Speed", speed + " kmh")
        + card_row("ti-battery", "Voltage", voltage + " V")
        + card_row("ti-signal", "Signal", signal)
        + card_row("ti-check", "GPS Fix", fix)
        + card_row("ti-clock", "Last Halt", halt_time, last=True)
    )

    if trip:
        trip_bits = []
        if trip.get("shipment_no"):
            trip_bits.append("Shipment " + str(trip.get("shipment_no")))
        if trip.get("route_name"):
            trip_bits.append("Route " + str(trip.get("route_name")))
        if trip.get("source_name") and trip.get("destination_name"):
            trip_bits.append(str(trip.get("source_name")) + " → " + str(trip.get("destination_name")))
        if trip_bits:
            rows = card_row("ti-truck", "Trip", " | ".join(trip_bits)) + rows

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:860px">'
        + section_title("Vehicle Snapshot")
        + cards
        + '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">'
        + rows
        + '</div>'
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 2: STOPPED VEHICLES
# ─────────────────────────────────────────────────────────────────────────────

def format_stopped_vehicles(data: dict, show_all: bool = False, severity_filter: Optional[str] = None) -> str:
    vehicles   = data.get("vehicles", [])
    query_info = data.get("query_info", {})

    SEVERITY_RANGES = {
        "critical": lambda m: m >= 1440,
        "high":     lambda m: 300 <= m < 1440,
        "medium":   lambda m: 180 <= m < 300,
        "low":      lambda m: m < 180,
    }
    if severity_filter and severity_filter in SEVERITY_RANGES:
        vehicles = [v for v in vehicles if SEVERITY_RANGES[severity_filter](v.get("halt_minutes",0))]

    total    = len(vehicles)
    critical = sum(1 for v in vehicles if v.get("halt_minutes",0) >= 1440)
    high     = sum(1 for v in vehicles if 300 <= v.get("halt_minutes",0) < 1440)
    medium   = sum(1 for v in vehicles if 180 <= v.get("halt_minutes",0) < 300)
    low      = sum(1 for v in vehicles if v.get("halt_minutes",0) < 180)
    threshold   = query_info.get("threshold","")
    filter_note = (" — " + severity_filter.upper() + " only") if severity_filter else ""

    metrics = (
        '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px">'
        + metric_box("Total stopped",  str(total),    "#111827", threshold)
        + metric_box("Critical >24h",  str(critical), "#dc2626", "immediate action")
        + metric_box("High 5-24h",     str(high),     "#d97706", "monitor closely")
        + metric_box("Medium 3-5h",    str(medium),   "#2563eb", "watch list")
        + '</div>'
    )

    show_v = vehicles if show_all else vehicles[:10]
    rows_html = ""
    for i, v in enumerate(show_v, 1):
        eta_s   = str(v.get("eta",""))[:10] if v.get("eta") else "N/A"
        h_mins  = v.get("halt_minutes", 0)
        sev_txt = str(v.get("severity","-"))
        sev_col = "#dc2626" if "CRITICAL" in sev_txt else "#d97706" if "HIGH" in sev_txt else "#2563eb" if "MEDIUM" in sev_txt else "#6b7280"
        bg      = "#fff" if i % 2 else "#f9fafb"
        rows_html += (
            '<tr style="background:' + bg + '">'
            + td(str(i))
            + td(str(v.get("shipment_no","")))
            + td(str(v.get("vehicle_no","")))
            + td(trunc(str(v.get("shipment_method","")),12))
            + td(str(v.get("halt_duration","N/A")), bold=True)
            + td(str(v.get("halt_since","N/A"))[:16])
            + td(trunc(str(v.get("last_address","-")),30))
            + td(sev_txt, color=sev_col)
            + td(eta_s)
            + '</tr>'
        )
    if not show_v:
        rows_html = '<tr><td colspan="9" style="text-align:center;padding:16px;color:#9ca3af">No vehicles found</td></tr>'

    table = html_table(["#","Shipment No","Vehicle No","Method","Halt Duration","Stopped Since","Location","Severity","ETA"], rows_html)

    actions = ""
    if not show_all and total > 10:
        btn_sty = 'style="padding:6px 14px;border-radius:8px;border:1px solid #d1d5db;background:#fff;font-size:12px;color:#1e3a5f;cursor:pointer;font-weight:500"'
        actions = (
            '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">'
            '<button ' + btn_sty + '>Show full list ↗</button>'
            '<button ' + btn_sty + '>Critical only ↗</button>'
            '<button ' + btn_sty + '>High severity ↗</button>'
            '</div>'
        )

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:860px">'
        + section_title("Stopped Vehicles" + filter_note)
        + metrics + table + actions
        + '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing ' + str(len(show_v)) + ' of ' + str(total) + ' · Halt from last_halt_time1/2/3</div>'
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 3: BULK REPORT + CHART
# ─────────────────────────────────────────────────────────────────────────────

def format_bulk_report(data: dict, query_msg: str = "") -> str:
    trips   = data.get("data", data.get("records", data.get("trips", [])))
    total   = data.get("total_found", data.get("total_records", len(trips) if isinstance(trips,list) else 0))
    filters = data.get("filters_applied", data.get("mongo_conditions", {}))

    parts = []
    if filters.get("region_code"):
        rc = filters["region_code"]
        # regex dict ho sakta hai — extract value
        if isinstance(rc, dict): rc = rc.get("$regex", str(rc))
        parts.append("Region: " + str(rc))
    if filters.get("trip_status") is not None:
        ts = filters["trip_status"]
        parts.append("Status: " + ("Active" if ts==1 else "Inactive" if ts==0 else "Cancelled"))
    if filters.get("exception_common_backend") is not None:
        exc_val = filters["exception_common_backend"]
        # regex dict ho sakta hai — string extract karo
        if isinstance(exc_val, dict): exc_val = exc_val.get("$regex", "")
        parts.append("GPS: " + get_gps_status(str(exc_val)))
    if filters.get("shipment_method") is not None:
        sm = filters["shipment_method"]
        if isinstance(sm, dict): sm = sm.get("$regex", str(sm))
        parts.append("Method: " + str(sm))
    if filters.get("gps_vendor_name") is not None:
        gv = filters["gps_vendor_name"]
        if isinstance(gv, dict):
            if "$regex" in gv: gv = gv["$regex"]
            elif "$nin" in gv: gv = "3rd Party"
            else: gv = str(gv)
        parts.append("Vendor: " + str(gv))
    if isinstance(filters.get("run_date"), dict):
        rd = filters["run_date"]
        parts.append("Date: " + str(rd.get("$gte",""))[:10] + " → " + str(rd.get("$lte",""))[:10])
    if filters.get("vehicle_no"):  parts.append("Vehicle: " + filters["vehicle_no"])
    if filters.get("source_code"): parts.append("Source: "  + filters["source_code"])
    filter_str = " | ".join(parts) if parts else "Last 30 days"

    msg_l        = query_msg.lower()
    show_atd_ata = "atd" in msg_l or "ata" in msg_l
    want_chart   = any(w in msg_l for w in ["chart","graph","analytics","trend","weekly","monthly","pie","bar"])
    show_trips   = trips[:20] if isinstance(trips, list) else []

    # ── Chart mode ─────────────────────────────────────────────────────────
    if want_chart and isinstance(trips, list) and trips:
        gps_active  = sum(1 for t in trips if t.get("exception_common_backend","") == "")
        gps_na      = sum(1 for t in trips if t.get("exception_common_backend","") == "GPS NA")
        gps_no_conn = sum(1 for t in trips if t.get("exception_common_backend","") == "No Connectivity")
        pct_a = int(gps_active  / max(total,1) * 100)
        pct_n = int(gps_na      / max(total,1) * 100)
        pct_c = int(gps_no_conn / max(total,1) * 100)

        weekly = defaultdict(lambda: {"active":0,"na":0,"no_conn":0})
        for t in trips:
            rd = str(t.get("run_date",""))[:10]
            try:
                dt = datetime.strptime(rd, "%Y-%m-%d")
                wk = "W" + str((dt.day-1)//7+1) + " " + dt.strftime("%b")
            except Exception:
                wk = "Other"
            exc = t.get("exception_common_backend","")
            if exc == "":           weekly[wk]["active"] += 1
            elif exc == "GPS NA":   weekly[wk]["na"] += 1
            else:                   weekly[wk]["no_conn"] += 1

        weeks  = list(weekly.keys())[:6]
        active = [weekly[w]["active"]  for w in weeks]
        na     = [weekly[w]["na"]      for w in weeks]
        nc     = [weekly[w]["no_conn"] for w in weeks]
        cid    = str(abs(id(data)) % 100000)

        chart_js = (
            "new Chart(el,{type:'bar',data:{labels:" + str(weeks) + ",datasets:["
            "{label:'GPS Active',data:" + str(active) + ",backgroundColor:'#22c55e',borderSkipped:false},"
            "{label:'GPS NA',data:" + str(na) + ",backgroundColor:'#ef4444',borderSkipped:false},"
            "{label:'No Conn',data:" + str(nc) + ",backgroundColor:'#f59e0b',borderSkipped:false}"
            "]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},"
            "scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,ticks:{callback:function(v){return v>999?(v/1000).toFixed(1)+'k':v}}}}}});"
        )
        dot = "display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle"
        metrics = (
            '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px">'
            + metric_box("Total trips",     str(total) + " trips",    "#111827", filter_str[:22])
            + metric_box("GPS active",      f"{gps_active:,}",        "#16a34a", str(pct_a) + "%")
            + metric_box("GPS NA",          f"{gps_na:,}",            "#dc2626", str(pct_n) + "%")
            + metric_box("No connectivity", f"{gps_no_conn:,}",       "#d97706", str(pct_c) + "%")
            + '</div>'
        )
        return (
            '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:600px">'
            + section_title("Bulk Report — " + filter_str)
            + metrics
            + '<div style="display:flex;gap:14px;margin-bottom:10px;font-size:12px;color:#6b7280">'
            + '<span><span style="' + dot + ';background:#22c55e"></span>GPS Active</span>'
            + '<span><span style="' + dot + ';background:#ef4444"></span>GPS NA</span>'
            + '<span><span style="' + dot + ';background:#f59e0b"></span>No Connectivity</span>'
            + '</div>'
            + '<div style="position:relative;width:100%;height:230px"><canvas id="bc' + cid + '">GPS Active:' + str(gps_active) + ', GPS NA:' + str(gps_na) + ', No Conn:' + str(gps_no_conn) + '</canvas></div>'
            + links_row(REPORT_LINKS)
            + '</div>'
            + '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>'
            + '<script>(function(){var el=document.getElementById("bc' + cid + '");if(el&&!el._init){el._init=true;' + chart_js + '}})()</script>'
        )

    # ── Table mode ──────────────────────────────────────────────────────────
    cols = ["#","Shipment No","Vehicle No","Driver","Route","Source","Destination","Run Date","GPS","Fixed Lock","Portable Lock"]
    if show_atd_ata: cols += ["ATD","ATA"]

    rows_html = ""
    for i, trip in enumerate(show_trips, 1):
        exc1 = get_gps_status(trip.get("exception_common_backend",""))
        exc2 = get_gps_status(trip.get("exception_common_backend_2",""))
        exc3 = get_gps_status(trip.get("exception_common_backend_3",""))
        gps_col1 = "#16a34a" if exc1=="Active" else "#dc2626" if exc1=="GPS NA" else "#d97706"
        bg = "#fff" if i % 2 else "#f9fafb"
        row_html = (
            '<tr style="background:' + bg + '">'
            + td(str(i))
            + td(str(trip.get("shipment_no","")))
            + td(str(trip.get("vehicle_no","")))
            + td(trunc(str(trip.get("driver_name","")),12))
            + td(trunc(str(trip.get("route_name","")),14))
            + td(trunc(str(trip.get("source_name","")),14))
            + td(trunc(str(trip.get("destination_name","")),14))
            + td(str(trip.get("run_date",""))[:16])
            + td(exc1, color=gps_col1)
            + td(exc2)
            + td(exc3)
        )
        if show_atd_ata:
            atd = "OK" if trip.get("actual_source_departure_time") else "Missing"
            ata = "OK" if trip.get("actual_destination_arrival_time") else "Missing"
            row_html += td(atd, color="#16a34a" if atd=="OK" else "#dc2626")
            row_html += td(ata, color="#16a34a" if ata=="OK" else "#dc2626")
        row_html += '</tr>'
        rows_html += row_html

    if not show_trips:
        rows_html = '<tr><td colspan="' + str(len(cols)) + '" style="text-align:center;padding:16px;color:#9ca3af">No trips found</td></tr>'

    table = html_table(cols, rows_html)

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:900px">'
        + section_title("Bulk Report: " + f"{total:,}" + " trips found")
        + '<div style="font-size:12px;color:#6b7280;margin-bottom:8px">Filters: ' + filter_str + '</div>'
        + table
        + '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing ' + str(len(show_trips)) + ' of ' + f"{total:,}" + ' (sorted by run_date desc)</div>'
        + links_row(REPORT_LINKS)
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 4: LOCATION TRIPS
# ─────────────────────────────────────────────────────────────────────────────

def format_location_trips(data: dict) -> str:
    if "error" in data or "info" in data:
        return '<div style="color:#6b7280;font-size:13px;padding:12px">' + data.get("user_message", data.get("error", data.get("info","No data"))) + '</div>'

    query_info = data.get("query_info", {})
    summary    = data.get("summary", {})
    trips      = data.get("trips", [])
    total      = data.get("_total", len(trips))
    source     = query_info.get("source_code","")
    status_txt = query_info.get("trip_status","ACTIVE")
    date_range = query_info.get("date_range","")

    gps_a = summary.get("gps_active",0)
    gps_n = summary.get("gps_na",0)
    gps_c = summary.get("gps_no_connectivity",0)

    rows_html = ""
    for i, trip in enumerate(trips[:20], 1):
        exc = trip.get("exception_common_backend","")
        gps = get_gps_status(exc)
        gps_col = "#16a34a" if exc=="" else "#dc2626" if exc=="GPS NA" else "#d97706"
        bg = "#fff" if i % 2 else "#f9fafb"
        rows_html += (
            '<tr style="background:' + bg + '">'
            + td(str(i))
            + td(str(trip.get("shipment_no","")))
            + td(str(trip.get("vehicle_no","")))
            + td(trunc(str(trip.get("driver_name","")),12))
            + td(trunc(str(trip.get("route_name","")),14))
            + td(str(trip.get("run_date",""))[:16])
            + td(trunc(str(trip.get("destination_name","")),18))
            + td(gps, color=gps_col)
            + td(str(trip.get("shipment_method","")))
            + '</tr>'
        )
    if not trips:
        rows_html = '<tr><td colspan="9" style="text-align:center;padding:16px;color:#9ca3af">No trips found</td></tr>'

    table = html_table(["#","Shipment No","Vehicle No","Driver","Route","Run Date","Destination","GPS Status","Method"], rows_html)

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:900px">'
        + section_title(str(total) + " " + status_txt + " trips from " + source)
        + '<div style="font-size:12px;color:#6b7280;margin-bottom:8px">Date: ' + date_range
        + ' &nbsp;|&nbsp; GPS Active: <span style="color:#16a34a;font-weight:600">' + str(gps_a) + '</span>'
        + ' &nbsp;|&nbsp; GPS NA: <span style="color:#dc2626;font-weight:600">' + str(gps_n) + '</span>'
        + ' &nbsp;|&nbsp; No Conn: <span style="color:#d97706;font-weight:600">' + str(gps_c) + '</span>'
        + '</div>'
        + table
        + '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing ' + str(min(20,len(trips))) + ' of ' + str(total) + '</div>'
        + ('&nbsp;<span style="font-size:12px;color:#2563eb">Reply full list for all records</span>' if total > 20 else "")
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 5: ALERTS
# ─────────────────────────────────────────────────────────────────────────────

def format_alerts(data: dict) -> str:
    logs = data.get("trigger_logs", data.get("alerts", []))
    if isinstance(logs, dict): logs = [logs]
    if not isinstance(logs, list): logs = []

    total    = len(logs)
    critical = sum(1 for a in logs if str(a.get("level","")) == "1")
    high     = sum(1 for a in logs if str(a.get("level","")) == "2")

    rows_html = ""
    for i, a in enumerate(logs[:20], 1):
        viol_mins = int(a.get("voilation_time",0) or 0)
        viol_dur  = str(viol_mins//60) + "h " + str(viol_mins%60) + "m" if viol_mins > 0 else "N/A"
        lvl       = str(a.get("level",""))
        lvl_col   = "#dc2626" if lvl=="1" else "#d97706" if lvl=="2" else "#6b7280"
        bg        = "#fff" if i % 2 else "#f9fafb"
        rows_html += (
            '<tr style="background:' + bg + '">'
            + td(str(i))
            + td(str(a.get("shipment_no","")))
            + td(str(a.get("vehicle_name",a.get("vehicle_no",""))))
            + td(str(a.get("alert_type","-")), bold=True)
            + td("L" + lvl, color=lvl_col)
            + td(viol_dur)
            + td(trunc(str(a.get("location","-")),28))
            + td(str(a.get("start_time",""))[:16])
            + '</tr>'
        )
    if not logs:
        rows_html = '<tr><td colspan="8" style="text-align:center;padding:16px;color:#9ca3af">No alerts found</td></tr>'

    table = html_table(["#","Shipment No","Vehicle No","Alert Type","Level","Violation Duration","Location","Start Time"], rows_html)

    metrics = (
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px">'
        + metric_box("Total alerts",  str(total),    "#111827")
        + metric_box("Critical (L1)", str(critical), "#dc2626", "immediate action")
        + metric_box("High (L2)",     str(high),     "#d97706", "monitor")
        + '</div>'
    )

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:900px">'
        + section_title("Alerts: " + str(total) + " found")
        + metrics + table
        + '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing ' + str(min(20,total)) + ' of ' + str(total) + '</div>'
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 6: DELAYS
# ─────────────────────────────────────────────────────────────────────────────

def format_delays(data: dict) -> str:
    delays     = data.get("delays", data.get("delay_data", []))
    total      = data.get("total_incidents", data.get("total_delay_incidents", len(delays) if isinstance(delays,list) else 0))
    total_mins = int(data.get("total_delay_mins", data.get("total_delay_minutes",0)) or 0)
    if isinstance(delays, dict): delays = [delays]
    if not isinstance(delays, list): delays = []

    rows_html = ""
    for i, d in enumerate(delays[:20], 1):
        reason = d.get("delay_reason_desc", d.get("delay_reason",""))
        mins   = int(d.get("total_delay_in_min") or 0)
        dur    = str(mins//60) + "h " + str(mins%60) + "m" if mins > 0 else "N/A"
        bg     = "#fff" if i % 2 else "#f9fafb"
        rows_html += (
            '<tr style="background:' + bg + '">'
            + td(str(i))
            + td(str(d.get("trip_id","")))
            + td(str(d.get("trip_vehicle_no",d.get("vehicle_no",""))))
            + td(trunc(str(d.get("driver_name","")),12))
            + td(trunc(str(d.get("route_name","")),14))
            + td(trunc(str(reason),22))
            + td(dur, bold=True, color="#d97706")
            + td(trunc(str(d.get("location_name","-")),20))
            + td(str(d.get("incident_date","")))
            + '</tr>'
        )
    if not delays:
        rows_html = '<tr><td colspan="9" style="text-align:center;padding:16px;color:#9ca3af">No delays found</td></tr>'

    table = html_table(["#","Shipment No","Vehicle No","Driver","Route","Delay Reason","Delay Duration","Location","Incident Date"], rows_html)

    total_dur = str(total_mins//60) + "h " + str(total_mins%60) + "m"
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:900px">'
        + section_title("Delays: " + str(total) + " incidents — Total: " + total_dur)
        + table
        + '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Showing ' + str(min(20,len(delays))) + ' of ' + str(total) + '</div>'
        + links_row([{"label":"⏱ Delay Dashboard","url":"https://cv18.secutrak.in/cv/specific/bluedart/Delay-Dashboard"}])
        + '</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def format_response(data: dict, query_type: str, query_msg: str = "",
                    show_all: bool = False, severity_filter: Optional[str] = None) -> str:
    if query_type == "STOPPED":    return format_stopped_vehicles(data, show_all=show_all, severity_filter=severity_filter)
    elif query_type == "TRIP":     return format_trip_status(data)
    elif query_type == "BULK":     return format_bulk_report(data, query_msg=query_msg)
    elif query_type == "LOCATION": return format_location_trips(data)
    elif query_type == "ALERT":    return format_alerts(data)
    elif query_type == "DELAY":    return format_delays(data)
    else:                          return ""


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 7: MAX TRIPS / ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

def format_max_trips(data: dict) -> str:
    if "error" in data:
        return '<div style="color:#dc2626;padding:12px;font-size:13px">' + data["error"] + '</div>'

    period    = data.get("period","Last month")
    total     = data.get("total_trips", 0)
    top_v     = data.get("top_vehicles", [])
    winner    = data.get("winner", {})
    w_vehicle = winner.get("vehicle_no","")
    w_count   = winner.get("trip_count", 0)

    # Top 5 table
    rows_html = ""
    for i, v in enumerate(top_v, 1):
        bg  = "#fff8f0" if i == 1 else ("#fff" if i % 2 else "#f9fafb")
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else str(i)
        rows_html += (
            '<tr style="background:' + bg + '">'
            + td(medal)
            + td(str(v.get("vehicle_no","")), bold=(i==1))
            + td(str(v.get("trip_count","")), bold=True, color="#1e3a5f")
            + '</tr>'
        )

    table = html_table(["Rank","Vehicle No","Trip Count"], rows_html)

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:520px">'
        + section_title("Vehicle with Maximum Trips — " + period)
        + '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px">'
        + metric_box("Winner Vehicle",  w_vehicle, "#1e3a5f")
        + metric_box("Trips Completed", str(w_count), "#16a34a", "last month")
        + '</div>'
        + table
        + '<div style="font-size:12px;color:#9ca3af;margin-top:8px">Period: ' + period + ' | Total trips analyzed: ' + str(f"{total:,}") + '</div>'
        + '</div>'
    )