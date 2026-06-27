"""
app/core/response_formatter.py
================================
COMMON RESPONSE TABLE FORMAT for all query types.

Standard columns:
S.No | Shipment No | Vehicle No | Driver | Route | Run Date | Destination | Halt | Halt Duration | Stopped Since | Location | Alerts

Filters per query type:
- TRIP/STATUS    → source, destination, route, run_date, trip_status
- STOPPED/HALT   → halt_duration, stopped_since, location, severity
- ALERTS         → alert_type, level, violation_time
- BULK REPORT    → gps_status, fixed_lock, portable_lock, atd, ata
- LOCATION       → source_code, gps_status, method
"""

from datetime import datetime
from typing import Dict, List, Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# HALT CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

def calc_halt_mins(halt_time_str: str) -> int:
    """Calculate halt minutes from lastHaltTime to now."""
    if not halt_time_str:
        return 0
    try:
        halt_dt = datetime.strptime(str(halt_time_str).strip(), "%Y-%m-%d %H:%M:%S")
        return max(0, int((datetime.now() - halt_dt).total_seconds() / 60))
    except Exception:
        return 0


def format_halt_duration(mins: int) -> str:
    if mins <= 0:
        return "N/A"
    return f"{mins//60}h {mins%60}m"


def get_severity(mins: int) -> str:
    if mins <= 0:      return "-"
    if mins >= 1440:   return "CRITICAL >24h"
    if mins >= 600:    return "HIGH 10-24h"
    if mins >= 300:    return "HIGH 5-10h"
    if mins >= 180:    return "MEDIUM 3-5h"
    return "LOW <3h"


def get_gps_status(exception: str) -> str:
    if exception == "GPS NA":           return "GPS NA"
    if exception == "No Connectivity":  return "No Conn"
    if exception == "":                 return "Active"
    return exception or "-"


# ─────────────────────────────────────────────────────────────────────────────
# ROW BUILDER — common row for all query types
# ─────────────────────────────────────────────────────────────────────────────

def build_row(
    sno: int,
    shipment_no: str = "",
    vehicle_no: str = "",
    driver: str = "",
    route: str = "",
    run_date: str = "",
    destination: str = "",
    halt: str = "-",
    halt_duration: str = "N/A",
    stopped_since: str = "N/A",
    location: str = "-",
    alerts: str = "-",
) -> str:
    """Build one table row with all standard columns."""
    return (
        f"| {sno} "
        f"| {shipment_no} "
        f"| {vehicle_no} "
        f"| {driver[:15] if driver else '-'} "
        f"| {route[:20] if route else '-'} "
        f"| {run_date[:16] if run_date else '-'} "
        f"| {destination[:20] if destination else '-'} "
        f"| {halt} "
        f"| {halt_duration} "
        f"| {stopped_since[:16] if stopped_since and stopped_since != 'N/A' else 'N/A'} "
        f"| {location[:35] if location else '-'} "
        f"| {alerts} |"
    )


TABLE_HEADER = (
    "| S.No | Shipment No | Vehicle No | Driver | Route | Run Date | Destination "
    "| Halt | Halt Duration | Stopped Since | Location | Alerts |"
)
TABLE_DIVIDER = (
    "|------|-------------|------------|--------|-------|----------|-------------|"
    "------|---------------|---------------|--------------------------------------|--------|"
)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 1: STOPPED VEHICLES
# Source: trip_dashboard_live_status
# Filters: severity (critical/high/medium/low), threshold hours
# ─────────────────────────────────────────────────────────────────────────────

def format_stopped_vehicles(
    data: dict,
    show_all: bool = False,
    severity_filter: Optional[str] = None,
) -> str:
    summary   = data.get("summary", {})
    vehicles  = data.get("vehicles", [])
    query_info = data.get("query_info", {})

    # Apply severity filter if requested
    SEVERITY_RANGES = {
        "critical": lambda m: m >= 1440,
        "high":     lambda m: 300 <= m < 1440,
        "medium":   lambda m: 180 <= m < 300,
        "low":      lambda m: m < 180,
    }
    if severity_filter and severity_filter in SEVERITY_RANGES:
        fn       = SEVERITY_RANGES[severity_filter]
        vehicles = [v for v in vehicles if fn(v.get("halt_minutes", 0))]

    total    = len(vehicles)
    critical = sum(1 for v in vehicles if v.get("halt_minutes", 0) >= 1440)
    high     = sum(1 for v in vehicles if 300 <= v.get("halt_minutes", 0) < 1440)
    medium   = sum(1 for v in vehicles if 180 <= v.get("halt_minutes", 0) < 300)
    low      = sum(1 for v in vehicles if v.get("halt_minutes", 0) < 180)

    threshold = query_info.get("threshold", "")
    filter_note = f" (filtered: {severity_filter.upper()})" if severity_filter else ""

    lines = [
        f"**Stopped Vehicles Report{filter_note}**",
        f"Total: **{total}** | Critical(>24h): {critical} | High(5-24h): {high} | Medium(3-5h): {medium} | Low(<3h): {low}",
        f"Filter: {threshold} | Calculated from: last_halt_time1/2/3",
        "",
        TABLE_HEADER,
        TABLE_DIVIDER,
    ]

    show_vehicles = vehicles if show_all else vehicles[:10]
    for i, v in enumerate(show_vehicles, 1):
        lines.append(build_row(
            sno          = i,
            shipment_no  = str(v.get("shipment_no", "")),
            vehicle_no   = str(v.get("vehicle_no", "")),
            driver       = "-",
            route        = str(v.get("shipment_method", "")),
            run_date     = "-",
            destination  = "-",
            halt         = get_severity(None if v.get("halt_minutes",0) <= 0 else "Stopped"),
            halt_duration = v.get("halt_duration", "N/A"),
            stopped_since = str(v.get("halt_since", "N/A")),
            location     = str(v.get("last_address", "-"))[:35],
            alerts       = f"ETA:{v.get('eta','N/A')[:10] if v.get('eta') else 'N/A'}",
        ))

    if not show_vehicles:
        lines.append("| — | No vehicles found | — | — | — | — | — | — | — | — | — | — |")

    lines.append(f"\nShowing {len(show_vehicles)} of {total}.")
    if not show_all and total > 10:
        lines.append("Reply **'full list'** for all records | **'critical'** / **'high'** / **'medium'** / **'low'** to filter.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 2: TRIP STATUS / SHIPMENT
# Source: courier_trip_detail + trip_dashboard_live_status
# ─────────────────────────────────────────────────────────────────────────────

def format_trip_status(data: dict) -> str:
    """For STATUS_CHECK, LOCATE, ETA_QUERY — single shipment detail."""
    core = data.get("trip_core", {})
    trip = core.get("trip_detail", {})
    live = core.get("live_status", {})
    meta = data.get("_meta", {})

    if isinstance(trip, list) and trip: trip = trip[0]
    if isinstance(live, list) and live: live = live[0]

    if not isinstance(trip, dict) or not trip:
        return "No data found. Check shipment number."

    # Halt from live status
    halt_time = None
    halt_mins = 0
    if isinstance(live, dict):
        for key in ["last_halt_time1", "last_halt_time_current"]:
            ht = live.get(key)
            if ht:
                halt_time = ht
                halt_mins = calc_halt_mins(ht)
                break

    gps = live.get("gps_current", {}) if isinstance(live, dict) else {}
    location = (live.get("last_address_current") or gps.get("latitude", "")) if isinstance(live, dict) else "-"

    lines = [
        f"**Trip Status: {trip.get('shipment_no','')}**",
        f"Status: {'ACTIVE' if trip.get('trip_status')==1 else 'CLOSED'} | Method: {trip.get('shipment_method','')} | Region: {trip.get('region_code','')}",
        "",
        TABLE_HEADER,
        TABLE_DIVIDER,
        build_row(
            sno          = 1,
            shipment_no  = str(trip.get("shipment_no", "")),
            vehicle_no   = str(trip.get("vehicle_no", "")),
            driver       = f"{trip.get('driver_name','')} {trip.get('driver_mobile','')}",
            route        = str(trip.get("route_name", ""))[:20],
            run_date     = str(trip.get("run_date", ""))[:16],
            destination  = str(trip.get("destination_name", ""))[:20],
            halt         = get_severity(halt_mins) if halt_mins > 0 else "-",
            halt_duration = format_halt_duration(halt_mins),
            stopped_since = str(halt_time) if halt_time else "N/A",
            location     = str(location)[:35],
            alerts       = f"ETA:{live.get('eta','N/A')[:10] if isinstance(live,dict) and live.get('eta') else 'N/A'}",
        )
    ]

    # Additional details
    if isinstance(live, dict) and live:
        lines.append("")
        etd = live.get("etd", "")
        etd_str = f" | ETD={etd}" if etd else ""
        
        stopped_gt_2h = live.get("stopped_gt_2h", 0)
        stopped_gt_5h = live.get("stopped_gt_5h", 0)
        stopped_duration = live.get("stopped_duration", "")
        halt_details = []
        if stopped_duration and stopped_duration != "0" and stopped_duration != "N/A":
            halt_details.append(f"Duration: {stopped_duration}")
        if stopped_gt_2h:
            halt_details.append("Stopped > 2h")
        if stopped_gt_5h:
            halt_details.append("Stopped > 5h")
        halt_details_str = f" | Halt Details: {' · '.join(halt_details)}" if halt_details else ""

        delay_hr = live.get("delay_hr", 0)
        delaying_sta = live.get("delaying_sta", "")
        delay_hours_2_to_5h = live.get("delay_hours_2_to_5h", 0)
        critical_hours_gt_5h = live.get("critical_hours_gt_5h", 0)
        delay_details = []
        if delay_hr:
            delay_details.append(f"{delay_hr}h delay")
        if delaying_sta:
            delay_details.append(f"At: {delaying_sta}")
        if delay_hours_2_to_5h:
            delay_details.append("Delay 2-5h")
        if critical_hours_gt_5h:
            delay_details.append("Critical > 5h")
        delay_details_str = f" | Delay Details: {' · '.join(delay_details)}" if delay_details else ""

        lines.append(f"**Live Details:** ETA={live.get('eta','N/A')}{etd_str} | Vehicle={live.get('vehicle_status_current','N/A')}{halt_details_str}{delay_details_str}")
        if gps:
            lines.append(f"**GPS:** Lat={gps.get('latitude','-')} Lng={gps.get('longitude','-')} Speed={gps.get('speed_kmh','-')}kmh")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 3: BULK REPORT
# Source: courier_trip_detail
# Filters: region, gps_status, fixed_lock, portable_lock, atd, ata, date
# ─────────────────────────────────────────────────────────────────────────────

def format_bulk_report(data: dict, query_msg: str = "") -> str:
    """For DOWNLOAD_REPORT — bulk trips with GPS/lock status."""
    trips  = data.get("data", data.get("records", data.get("trips", [])))
    total  = data.get("total_found", data.get("total_records", len(trips) if isinstance(trips, list) else 0))
    filters = data.get("filters_applied", data.get("mongo_conditions", {}))

    # Filter summary
    filter_parts = []
    if filters.get("region_code"): filter_parts.append(f"Region: {filters['region_code']}")
    if filters.get("trip_status") is not None: filter_parts.append(f"Status: {'Active' if filters['trip_status']==1 else 'Inactive'}")
    if filters.get("exception_common_backend"): filter_parts.append(f"GPS: {filters['exception_common_backend']}")
    if filters.get("run_date"): filter_parts.append(f"Date: {filters['run_date'].get('$gte','')[:10]} to {filters['run_date'].get('$lte','')[:10]}")
    filter_str = " | ".join(filter_parts) if filter_parts else "All trips"

    lines = [
        f"**Bulk Report: {total} trips found**",
        f"Filters: {filter_str}",
        "",
        TABLE_HEADER,
        TABLE_DIVIDER,
    ]

    show_trips = trips[:10] if isinstance(trips, list) else []
    for i, trip in enumerate(show_trips, 1):
        exc1 = get_gps_status(trip.get("exception_common_backend", ""))
        exc2 = get_gps_status(trip.get("exception_common_backend_2", ""))
        exc3 = get_gps_status(trip.get("exception_common_backend_3", ""))
        alert_str = f"GPS:{exc1} FL:{exc2} PL:{exc3}"

        # ATD/ATA check
        atd = trip.get("actual_source_departure_time", "")
        ata = trip.get("actual_destination_arrival_time", "")
        if "atd" in query_msg.lower() or "ata" in query_msg.lower():
            alert_str = f"ATD:{'OK' if atd else 'Missing'} ATA:{'OK' if ata else 'Missing'}"

        lines.append(build_row(
            sno          = i,
            shipment_no  = str(trip.get("shipment_no", "")),
            vehicle_no   = str(trip.get("vehicle_no", "")),
            driver       = str(trip.get("driver_name", "")),
            route        = str(trip.get("route_name", ""))[:20],
            run_date     = str(trip.get("run_date", ""))[:16],
            destination  = str(trip.get("destination_name", ""))[:20],
            halt         = "-",
            halt_duration = "N/A",
            stopped_since = "N/A",
            location     = str(trip.get("source_name", ""))[:35],
            alerts       = alert_str,
        ))

    if not show_trips:
        lines.append("| — | No trips found | — | — | — | — | — | — | — | — | — | — |")

    lines.append(f"\nShowing {len(show_trips)} of {total}.")
    if total > 10:
        lines.append("Reply **'full list'** for all records.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 4: LOCATION TRIPS
# Source: courier_trip_detail filtered by source_code
# ─────────────────────────────────────────────────────────────────────────────

def format_location_trips(data: dict) -> str:
    """For trips FROM a specific location (source_code filter)."""
    if "error" in data or "info" in data:
        return f"**Location Query:** {data.get('user_message', data.get('error', data.get('info', 'No data')))}"

    query_info = data.get("query_info", {})
    summary    = data.get("summary", {})
    trips      = data.get("trips", [])
    total      = data.get("_total", len(trips))
    source     = query_info.get("source_code", "")
    status_txt = query_info.get("trip_status", "ACTIVE")

    lines = [
        f"**{total} {status_txt} trips from {source}**",
        f"GPS Active: {summary.get('gps_active',0)} | GPS NA: {summary.get('gps_na',0)} | No Connectivity: {summary.get('gps_no_connectivity',0)}",
        "",
        TABLE_HEADER,
        TABLE_DIVIDER,
    ]

    for i, trip in enumerate(trips[:10], 1):
        exc = trip.get("exception_common_backend", "")
        lines.append(build_row(
            sno          = i,
            shipment_no  = str(trip.get("shipment_no", "")),
            vehicle_no   = str(trip.get("vehicle_no", "")),
            driver       = str(trip.get("driver_name", "")),
            route        = str(trip.get("route_name", ""))[:20],
            run_date     = str(trip.get("run_date", ""))[:16],
            destination  = str(trip.get("destination_name", ""))[:20],
            halt         = "-",
            halt_duration = "N/A",
            stopped_since = "N/A",
            location     = str(trip.get("source_name", ""))[:35],
            alerts       = get_gps_status(exc),
        ))

    if not trips:
        lines.append("| — | No trips found | — | — | — | — | — | — | — | — | — | — |")

    lines.append(f"\nShowing {min(10,len(trips))} of {total}.")
    if total > 10:
        lines.append("Reply **'full list'** for all records.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 5: ALERTS
# Source: logistic_trigger_log + bluedart_trigger_dashboard
# ─────────────────────────────────────────────────────────────────────────────

def format_alerts(data: dict) -> str:
    """For ALERT_QUERY — show alert list."""
    logs  = data.get("trigger_logs", data.get("alerts", []))
    dash  = data.get("trigger_dashboard", {})

    if isinstance(logs, dict): logs = [logs]
    if not isinstance(logs, list): logs = []

    total    = len(logs)
    critical = sum(1 for a in logs if str(a.get("level","")) == "1")
    high     = sum(1 for a in logs if str(a.get("level","")) == "2")

    lines = [
        f"**Alerts: {total} found** | Critical(L1): {critical} | High(L2): {high}",
        "",
        TABLE_HEADER,
        TABLE_DIVIDER,
    ]

    for i, alert in enumerate(logs[:10], 1):
        lines.append(build_row(
            sno          = i,
            shipment_no  = str(alert.get("shipment_no", "")),
            vehicle_no   = str(alert.get("vehicle_name", "")),
            driver       = str(alert.get("primary_info", {}).get("driver_name", "") if isinstance(alert.get("primary_info"),dict) else ""),
            route        = str(alert.get("primary_info", {}).get("route_name", "") if isinstance(alert.get("primary_info"),dict) else "")[:20],
            run_date     = str(alert.get("run_date", ""))[:16],
            destination  = str(alert.get("primary_info", {}).get("destination_code", "") if isinstance(alert.get("primary_info"),dict) else ""),
            halt         = f"L{alert.get('level','-')}",
            halt_duration = f"{int(alert.get('voilation_time',0)//60)}h {int(alert.get('voilation_time',0)%60)}m" if alert.get("voilation_time") else "N/A",
            stopped_since = str(alert.get("start_time", "N/A"))[:16],
            location     = str(alert.get("location", "-"))[:35],
            alerts       = str(alert.get("alert_type", "-")),
        ))

    if not logs:
        lines.append("| — | No alerts found | — | — | — | — | — | — | — | — | — | — |")

    if isinstance(dash, dict) and dash.get("alerts_summary"):
        s = dash["alerts_summary"]
        lines.append(f"\n**Dashboard:** Total={s.get('total',0)} | Critical={s.get('critical_count',0)} | Types={','.join(s.get('unique_types',[]))}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTER 6: DELAY REPORT
# Source: courier_route_delay
# ─────────────────────────────────────────────────────────────────────────────

def format_delays(data: dict) -> str:
    """For DELAY_QUERY — show delay incidents."""
    delays = data.get("delays", data.get("delay_data", []))
    total  = data.get("total_incidents", len(delays) if isinstance(delays, list) else 0)
    total_mins = data.get("total_delay_mins", 0)

    if isinstance(delays, dict): delays = [delays]
    if not isinstance(delays, list): delays = []

    lines = [
        f"**Delays: {total} incidents** | Total delay: {total_mins//60}h {total_mins%60}m",
        "",
        TABLE_HEADER,
        TABLE_DIVIDER,
    ]

    for i, d in enumerate(delays[:10], 1):
        lines.append(build_row(
            sno          = i,
            shipment_no  = str(d.get("trip_id", "")),
            vehicle_no   = str(d.get("trip_vehicle_no", d.get("vehicle_no",""))),
            driver       = str(d.get("driver_name", "")),
            route        = str(d.get("route_name", ""))[:20],
            run_date     = str(d.get("incident_date", ""))[:16],
            destination  = str(d.get("destination_name", ""))[:20],
            halt         = str(d.get("delay_reason_desc", d.get("delay_reason",""))),
            halt_duration = f"{int(d.get('total_delay_in_min',0) or 0)//60}h {int(d.get('total_delay_in_min',0) or 0)%60}m",
            stopped_since = f"{d.get('incident_date','')} {d.get('incident_time','')}",
            location     = str(d.get("location_name", "-"))[:35],
            alerts       = str(d.get("enroute_code", "-")),
        ))

    if not delays:
        lines.append("| — | No delays found | — | — | — | — | — | — | — | — | — | — |")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FORMATTER — routes to correct formatter based on query type
# ─────────────────────────────────────────────────────────────────────────────

def format_response(
    data: dict,
    query_type: str,
    query_msg: str = "",
    show_all: bool = False,
    severity_filter: Optional[str] = None,
) -> str:
    """
    Route to correct formatter based on query type.
    query_type: STOPPED | TRIP | BULK | LOCATION | ALERT | DELAY | GENERAL
    """
    if query_type == "STOPPED":
        return format_stopped_vehicles(data, show_all=show_all, severity_filter=severity_filter)
    elif query_type == "TRIP":
        return format_trip_status(data)
    elif query_type == "BULK":
        return format_bulk_report(data, query_msg=query_msg)
    elif query_type == "LOCATION":
        return format_location_trips(data)
    elif query_type == "ALERT":
        return format_alerts(data)
    elif query_type == "DELAY":
        return format_delays(data)
    else:
        # General — let GPT handle but add table if data has trips
        return ""  # empty = GPT will handle
