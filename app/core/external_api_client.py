import httpx
import io
import zipfile
import json
import traceback
from datetime import datetime
from typing import Any, List, Optional

def normalize_api_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return record

    mapping = {
        "ShipmentMethod": "shipment_method",
        "Region": "region_code",
        "Source": "source_code",
        "BranchLocation": "source_code",
        "Destination": "destination_code",
        "RouteCode": "route_code",
        "RouteName": "route_name",
        "RouteId": "route_id",
        "FleetNo": "fleet_no",
        "ShipmentNo": "shipment_no",
        "RunCode": "run_code",
        "RunDate": "run_date",
        "VehicleNo": "vehicle_no",
        "State": "state",
        "BranchName": "branch_name",
        "Area": "area",
        "Driver": "driver_name",
        "DriverMobile": "driver_mobile",
        "Transporter": "transporter_name",
        "MobileATD": "mobile_atd",
        "MobileATA": "mobile_ata",
        "GPSATD": "gps_atd",
        "GPSATA": "gps_ata",
        "ApiATD": "api_atd",
        "ApiATA": "api_ata",
        "AHT": "aht",
        "STD": "schedule_departure",
        "ATD": "actual_source_departure_time",
        "DelayDeparture": "delay_departure",
        "STA": "schedule_arrival",
        "ATA": "actual_destination_arrival_time",
        "TTMapped": "tt_mapped",
        "TTTaken": "tt_taken",
        "DelayArrival": "delay_arrival",
        "DelayTT": "delay_tt",
        "TrackHistory1": "track_history_1",
        "Imei1": "imei_no",
        "TrackHistory2": "track_history_2",
        "Imei2": "imei_no2",
        "TrackHistory3": "track_history_3",
        "Imei3": "imei_no3",
        "ScheduleHalt": "schedule_halt",
        "ActualHalt": "halt_duration",
        "ATT": "att",
        "DistanceKm1": "distance_km",
        "DistanceKm2": "distance_km2",
        "DistanceKm3": "distance_km3",
        "GPSException1": "exception_common_backend",
        "GPSException2": "exception_common_backend_2",
        "GPSException3": "exception_common_backend_3",
        "TripStatus": "trip_status",
        "CloseBy": "close_by",
        "CloseDate": "close_date",
        "CloseByDevice": "close_by_device",
        "Bag": "bag",
        "Remarks": "close_remarks",
        "GPSVendorType1": "gps_vendor_name",
        "GPSVendorType2": "gps_vendor2",
        "GPSVendorType3": "gps_vendor3",
        "PortableLockVendor": "portable_lock_vendor",
        "Dashcam": "dashcam",
        "DashcamImei": "dashcam_imei",
        "GateOutTime": "gate_out_time",
        "GateInTime": "gate_in_time",
        "GpsAta": "gps_ata",
        "GpsAtd": "gps_atd",
        "BayNoIn": "bay_no_in",
        "BayNoOut": "bay_no_out",
        "ShipmentCountIn": "shipment_count_in",
        "ShipmentCountOut": "shipment_count_out",
        "WeightIn": "weight_in",
        "WeightOut": "weight_out",
        "CreateDate": "create_date",
        "PushTimeTrip": "push_time_trip",
        "PushStatus": "push_status",
        "ServerGPSReceivedIn": "server_gps_received_in",
        "ServerGPSProcessedIn": "server_gps_processed_in",
        "ServerGPSReceivedOut": "server_gps_received_out",
        "ServerGPSProcessedOut": "server_gps_processed_out",
        "PushTimeIn": "push_time_in",
        "PushTimeOut": "push_time_out",
        "PushTimeInStatus": "push_time_in_status",
        "PushTimeOutStatus": "push_time_out_status",
        "Id": "id",
        "RouteCategory": "route_category",
        "HighShipment": "high_shipment",
        "Detail": "detail",
    }

    normalized = {}
    for api_key, val in record.items():
        db_key = mapping.get(api_key, api_key)
        normalized[db_key] = val

    # Convert complex values to match expected types / formats
    if "trip_status" in normalized:
        status_val = normalized["trip_status"]
        if not isinstance(status_val, int):
            status_str = str(status_val).lower()
            if "close" in status_str or "complete" in status_str:
                normalized["trip_status"] = 0
            elif "cancel" in status_str:
                normalized["trip_status"] = 2
            else:
                normalized["trip_status"] = 1

    if "RouteCategory" in record:
        route_cat = str(record["RouteCategory"]).lower()
        normalized["trip_type"] = 2 if "intracity" in route_cat else 1

    for exc_key in ("exception_common_backend", "exception_common_backend_2", "exception_common_backend_3"):
        if exc_key in normalized and normalized[exc_key] == "GPS Active":
            normalized[exc_key] = ""

    # Source Name & Destination Name mapping fallback
    if "Source" in record:
        normalized["source_name"] = record["Source"]
    if "Destination" in record:
        normalized["destination_name"] = record["Destination"]

    return normalized

def map_exception_value(val: Any) -> str:
    if val is None:
        return None
    val_str = str(val).lower()
    if val_str == "" or "active" in val_str:
        return "3"
    if "na" in val_str:
        return "2"
    if "no connectivity" in val_str or "offline" in val_str:
        return "4"
    if "all" in val_str:
        return "1"
    if val_str in ("1", "2", "3", "4"):
        return val_str
    return str(val)

async def fetch_trip_report_api(
    date_from: Optional[str],
    date_to: Optional[str],
    access_token: Optional[str],
    filters: Optional[dict] = None
) -> dict:
    # Resolve dates
    # Input date is like "2026-06-23 00:00:00" -> extract "2026-06-23"
    resolved_from = date_from[:10] if date_from else datetime.now().strftime("%Y-%m-%d")
    resolved_to = date_to[:10] if date_to else datetime.now().strftime("%Y-%m-%d")
    resolved_token = access_token or "f2cN7PAlgzR31hY0LoZW247"  # fallback to user's example

    print(f"[external-api] Calling bdTripReport with DateFrom={resolved_from}, DateTo={resolved_to}, AccessToken={resolved_token}")

    url = "https://apinode2.secutrak.in/dev-app-itraceit/bdTripReport"
    payload = {
        "DateFrom": resolved_from,
        "DateTo": resolved_to,
        "ReportType": "1",
        "AccessToken": resolved_token
    }

    if filters:
        # Map our internal filter keys to the external API's PascalCase filter keys
        # 1. Region
        region = filters.get("region_code")
        if region:
            payload["Region"] = str(region).upper()

        # 2. Customer
        customer = filters.get("customer") or filters.get("customer_id")
        if customer:
            payload["Customer"] = str(customer)

        # 3. Route
        route = filters.get("route_code") or filters.get("route_name")
        if route:
            payload["Route"] = str(route)

        # 4. RouteCategory (Intercity vs Intracity)
        # trip_type 1 is Intercity, 2 is Intracity
        trip_type = filters.get("trip_type") or filters.get("route_category")
        if trip_type:
            if str(trip_type) in ("1", "Intercity"):
                payload["RouteCategory"] = "1"
            elif str(trip_type) in ("2", "Intracity"):
                payload["RouteCategory"] = "2"

        # 5. RouteType
        route_type = filters.get("route_type") or filters.get("shipment_method")
        if route_type:
            payload["RouteType"] = str(route_type)

        # 6. ETADelay / delay reasons
        eta_delay = filters.get("delay_reason") or filters.get("eta_delay")
        if eta_delay:
            payload["ETADelay"] = str(eta_delay)

        # 7. SupervisorException
        super_exc = filters.get("supervisor_exception") or filters.get("close_remarks")
        if super_exc:
            payload["SupervisorException"] = str(super_exc)

        # 8. TripStatus (Active/Running/Schedule -> "1", Closed/Completed -> "0", Cancelled -> "2")
        trip_status = filters.get("trip_status")
        if trip_status is not None:
            if str(trip_status) in ("0", "Completed", "Close", "Closed"):
                payload["TripStatus"] = "0"
            elif str(trip_status) in ("1", "Active", "Open", "Running", "Schedule"):
                payload["TripStatus"] = "1"
            elif str(trip_status) in ("2", "Cancelled"):
                payload["TripStatus"] = "2"

        # 9. Vendor
        if filters.get("gps_vendor_3rdparty") or filters.get("vendor_3rdparty"):
            payload["Vendor"] = "3"
        else:
            vendor = filters.get("gps_vendor_name") or filters.get("gps_vendor2") or filters.get("gps_vendor3") or filters.get("vendor")
            if vendor:
                vendor_str = str(vendor).lower()
                if "third" in vendor_str or "3rd" in vendor_str:
                    payload["Vendor"] = "3"
                elif "ilgic" in vendor_str:
                    payload["Vendor"] = "2"
                elif "all" in vendor_str:
                    payload["Vendor"] = "1"
                else:
                    payload["Vendor"] = str(vendor)

        # 10. FixedGPSException
        gps_exc = filters.get("exception_common_backend") or filters.get("fixed_gps_exception")
        if gps_exc:
            payload["FixedGPSException"] = map_exception_value(gps_exc)

        # 11. FixedELockException
        fixed_lock = filters.get("exception_common_backend_2") or filters.get("fixed_elock_exception")
        if fixed_lock:
            payload["FixedELockException"] = map_exception_value(fixed_lock)

        # 12. PortableELockException
        port_lock = filters.get("exception_common_backend_3") or filters.get("portable_elock_exception")
        if port_lock:
            payload["PortableELockException"] = map_exception_value(port_lock)

        # 13. Origin / Source
        origin = filters.get("source_code") or filters.get("origin")
        if origin:
            payload["Origin"] = str(origin).upper()

        # 14. Destination
        dest = filters.get("destination_code") or filters.get("destination")
        if dest:
            payload["Destination"] = str(dest).upper()

    api_request_info = {
        "url": url,
        "method": "POST",
        "payload": payload
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, data=payload)
            if resp.status_code in (200, 201):
                data = None
                # Try unzipping first if response looks like a ZIP file
                if resp.content.startswith(b'PK\x03\x04'):
                    try:
                        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                            file_list = [f for f in z.namelist() if not f.endswith('/')]
                            if file_list:
                                first_file = file_list[0]
                                with z.open(first_file) as f:
                                    data = json.loads(f.read().decode('utf-8'))
                            else:
                                print("[external-api] Zip file has no files inside.")
                    except Exception as zip_err:
                        print(f"[external-api] Failed to unzip or parse zip content: {zip_err}")
                        traceback.print_exc()

                # Fallback to direct JSON parsing if not zipped or zip parsing failed
                if data is None:
                    try:
                        data = resp.json()
                    except Exception as json_err:
                        print(f"[external-api] Failed to parse JSON response: {json_err}. Response preview: {resp.text[:500]}")
                        traceback.print_exc()
                        return {"records": [], "api_request": api_request_info}

                # Print the JSON response in the terminal
                print("[external-api] JSON Response data:")
                print(json.dumps(data, indent=2, default=str))

                # Handle response structures
                records = []
                if isinstance(data, list):
                    records = [normalize_api_record(r) for r in data]
                elif isinstance(data, dict):
                    raw_records = []
                    if isinstance(data.get("data"), list):
                        raw_records = data["data"]
                    elif isinstance(data.get("results"), list):
                        raw_records = data["results"]
                    elif isinstance(data.get("Report"), list):
                        raw_records = data["Report"]
                    elif isinstance(data.get("report"), list):
                        raw_records = data["report"]
                    else:
                        raw_records = [data]
                    records = [normalize_api_record(r) for r in raw_records]
                return {"records": records, "api_request": api_request_info}
            else:
                print(f"[external-api] Error status={resp.status_code} response={resp.text[:200]}")
                return {"records": [], "api_request": api_request_info}
    except Exception as e:
        print(f"[external-api] Exception occurred: {e}")
        traceback.print_exc()
        return {"records": [], "api_request": api_request_info}
