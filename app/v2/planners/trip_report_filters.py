from __future__ import annotations

from typing import Any, Dict


TRIP_REPORT_DEFAULT_TRIP_TYPE = 1

TRIP_STATUS_FILTERS: Dict[str, int] = {
    "completed": 0,
    "closed": 0,
    "done": 0,
    "schedule": 1,
    "scheduled": 1,
    "active": 1,
    "running": 1,
    "cancelled": 2,
    "canceled": 2,
}

ROUTE_CATEGORY_FILTERS: Dict[str, int] = {
    "intercity": 1,
    "inter-city": 1,
    "inter city": 1,
    "network": 1,
    "intracity": 2,
    "intra-city": 2,
    "intra city": 2,
    "same city": 2,
}

SHIPMENT_METHOD_FILTERS: Dict[str, str] = {
    "air network": "AIR_Network",
    "air": "AIR_Network",
    "sfc network": "SFC_Network",
    "sfc": "SFC_Network",
    "speed truck": "Speed_Truck",
    "speed": "Speed_Truck",
    "feeder": "Feeder",
    "empty": "Empty",
    "delivery": "Delivery",
    "pick up and delivery": "Pick Up and Delivery",
    "pickup and delivery": "Pick Up and Delivery",
    "pick up": "Pick Up",
    "pickup": "Pick Up",
    "comm/bz-out": "Comm/BZ-Out",
    "comm/bz-in": "Comm/BZ-In",
    "comm/bz": "Comm/BZ",
    "customer delivery": "Customer Delivery",
}

REGION_FILTERS: Dict[str, str] = {
    "north": "NORTH",
    "south1-hyd": "SOUTH1-HYD",
    "south1 hyd": "SOUTH1-HYD",
    "east": "EAST",
    "west1": "WEST1",
    "south2": "SOUTH2",
    "west2": "WEST2",
    "south1-maa": "SOUTH1-MAA",
    "south1 maa": "SOUTH1-MAA",
    "ho": "HO",
    "south": "SOUTH",
    "west": "WEST",
}

DEVICE_EXCEPTION_FILTERS: Dict[str, Dict[str, Any]] = {
    "gps na": {"exception_common_backend": "GPS NA"},
    "gps active": {"exception_common_backend": "__gps_active__"},
    "gps no connectivity": {"exception_common_backend": "No Connectivity"},
    "no connectivity": {"exception_common_backend": "No Connectivity"},
    "fixed gps na": {"exception_common_backend_2": "GPS NA"},
    "fixed lock na": {"exception_common_backend_2": "GPS NA"},
    "fixed gps active": {"exception_common_backend_2": ""},
    "fixed lock active": {"exception_common_backend_2": ""},
    "fixed no connectivity": {"exception_common_backend_2": "No Connectivity"},
    "fixed elock exception of no connectivity": {"exception_common_backend_2": "No Connectivity"},
    "fixed lock exception of no connectivity": {"exception_common_backend_2": "No Connectivity"},
    "fixed lock no connectivity": {"exception_common_backend_2": "No Connectivity"},
    "portable gps na": {"exception_common_backend_3": "GPS NA"},
    "portable lock na": {"exception_common_backend_3": "GPS NA"},
    "portable gps active": {"exception_common_backend_3": ""},
    "portable lock active": {"exception_common_backend_3": ""},
    "portable no connectivity": {"exception_common_backend_3": "No Connectivity"},
    "portable elock exception of no connectivity": {"exception_common_backend_3": "No Connectivity"},
    "portable lock exception of no connectivity": {"exception_common_backend_3": "No Connectivity"},
    "portable lock no connectivity": {"exception_common_backend_3": "No Connectivity"},
}

VENDOR_FILTERS: Dict[str, Dict[str, Any]] = {
    "ilgic": {"gps_vendor_name": "ILGIC"},
    "third party": {"gps_vendor_name": "__3rdparty__"},
    "3rd party": {"gps_vendor_name": "__3rdparty__"},
}
