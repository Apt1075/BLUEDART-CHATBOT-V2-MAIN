"""
app/v2/entity_extraction/filter_metadata_service.py
=====================================================
Filter Metadata Service – Singleton, thread-safe, in-memory cached.

Responsibilities
----------------
1. Load all filter metadata from app/v2/filter.json at startup.
2. Support fuzzy matching (RapidFuzz token_sort_ratio).
3. Support alias / synonym matching.
4. Support typo correction.
5. Expose extract_filters(query: str) -> dict

Sections handled from filter.json
----------------------------------
  Region              → region_code
  Customer            → customer_code
  Route               → route_name
  RouteCategory       → route_category   (1=Intercity, 2=Intracity)
  RouteType           → shipment_method
  TripStatus          → trip_status      (0=Completed, 1=Active, 2=Cancelled)
  Vendor              → gps_vendor
  FixedGPSException   → fixed_gps_exception
  FixedELockException → fixed_elock_exception
  PortableELockException → portable_elock_exception
  ETADelay            → eta_delay_hrs
  SupervisorException → supervisor_exception
"""

from __future__ import annotations

import json
import logging
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# pyrefly: ignore [missing-import]
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & thresholds
# ---------------------------------------------------------------------------

_FILTER_JSON_PATH = Path(__file__).resolve().parents[2] / "v2" / "filter.json"

# Minimum fuzzy-match score (0–100) to accept a candidate
_FUZZY_THRESHOLD = 72

# For very short tokens (≤ 4 chars) skip fuzzy and only do exact / alias match
_SHORT_TOKEN_MIN_LEN = 5


# ---------------------------------------------------------------------------
# Alias / synonym tables
# (query-side → canonical filter value)
# ---------------------------------------------------------------------------

_REGION_ALIASES: Dict[str, str] = {
    # NORTH variants
    "north": "NORTH",
    "northern": "NORTH",
    "north region": "NORTH",
    "north zone": "NORTH",
    "north india": "NORTH",
    "uttar": "NORTH",

    # SOUTH variants
    "south": "SOUTH",
    "southern": "SOUTH",
    "south region": "SOUTH",
    "south zone": "SOUTH",
    "south india": "SOUTH",

    # EAST variants
    "east": "EAST",
    "eastern": "EAST",
    "east region": "EAST",
    "east zone": "EAST",
    "east india": "EAST",
    "puurb": "EAST",

    # WEST variants
    "west": "WEST",
    "western": "WEST",
    "west region": "WEST",
    "west zone": "WEST",
    "west india": "WEST",
    "west1": "WEST1",
    "west2": "WEST2",

    # SOUTH sub-regions
    "south1": "SOUTH1-HYD",
    "south1-hyd": "SOUTH1-HYD",
    "south1 hyd": "SOUTH1-HYD",
    "hyderabad region": "SOUTH1-HYD",
    "south2": "SOUTH2",
    "south1-maa": "SOUTH1-MAA",
    "south1 maa": "SOUTH1-MAA",
    "chennai region": "SOUTH1-MAA",

    # HO
    "ho": "HO",
    "head office": "HO",
    "headquarter": "HO",
}

_TRIP_STATUS_ALIASES: Dict[str, int] = {
    # Active / running
    "active": 1,
    "running": 1,
    "live": 1,
    "ongoing": 1,
    "in transit": 1,
    "in-transit": 1,
    "open": 1,
    "scheduled": 1,
    "schedule": 1,

    # Completed / closed
    "completed": 0,
    "complete": 0,
    "closed": 0,
    "close": 0,
    "done": 0,
    "finished": 0,
    "delivered": 0,
    "inactive": 0,

    # Cancelled
    "cancelled": 2,
    "canceled": 2,
    "cancel": 2,
}

_ROUTE_CATEGORY_ALIASES: Dict[str, int] = {
    "intercity": 1,
    "inter city": 1,
    "inter-city": 1,
    "lh": 1,
    "linehaul": 1,
    "line haul": 1,
    "line-haul": 1,

    "intracity": 2,
    "intra city": 2,
    "intra-city": 2,
    "local": 2,
    "city": 2,
    "same city": 2,
}

_SHIPMENT_METHOD_ALIASES: Dict[str, str] = {
    # AIR
    "air": "AIR_Network",
    "air network": "AIR_Network",
    "air freight": "AIR_Network",
    "air cargo": "AIR_Network",
    "flight": "AIR_Network",
    "by air": "AIR_Network",

    # SFC
    "sfc": "SFC_Network",
    "sfc network": "SFC_Network",
    "surface": "SFC_Network",
    "surface network": "SFC_Network",
    "road": "SFC_Network",
    "by road": "SFC_Network",

    # Speed Truck
    "speed truck": "Speed_Truck",
    "speed": "Speed_Truck",
    "express truck": "Speed_Truck",

    # Feeder
    "feeder": "Feeder",
    "feeder trip": "Feeder",

    # Empty
    "empty": "Empty",
    "empty run": "Empty",
    "empty trip": "Empty",

    # Delivery
    "delivery": "Delivery",
    "deliver": "Delivery",

    # Pick Up
    "pick up": "Pick Up",
    "pickup": "Pick Up",
    "pick-up": "Pick Up",

    # Pick Up and Delivery
    "pick up and delivery": "Pick Up and Delivery",
    "pickup and delivery": "Pick Up and Delivery",
    "pick up & delivery": "Pick Up and Delivery",

    # Comm/BZ
    "comm/bz-out": "Comm/BZ-Out",
    "comm/bz-in": "Comm/BZ-In",
    "comm bz out": "Comm/BZ-Out",
    "comm bz in": "Comm/BZ-In",
    "comm/bz": "Comm/BZ-Out",

    # Customer Delivery
    "customer delivery": "Customer Delivery",
    "cust delivery": "Customer Delivery",
}

_VENDOR_ALIASES: Dict[str, str] = {
    "ilgic": "ILGIC",
    "third party": "Third Party",
    "3rd party": "Third Party",
    "third-party": "Third Party",
    "external": "Third Party",
    "all vendors": "All",
    "all": "All",
    "axestrack": "ILGIC",           # logical alias in Bluedart context
    "axestrck": "ILGIC",
    "lynkit": "ILGIC",
    "kiasaint": "ILGIC",
}

_GPS_EXCEPTION_ALIASES: Dict[str, str] = {
    "gps na": "GPS NA",
    "gps is na": "GPS NA",
    "gps inactive": "GPS NA",
    "no gps": "GPS NA",
    "gps not working": "GPS NA",

    "gps active": "GPS Active",
    "gps working": "GPS Active",
    "gps connected": "GPS Active",

    "no connectivity": "No Connectivity",
    "gps no connectivity": "No Connectivity",
    "no connection": "No Connectivity",
    "not connected": "No Connectivity",

    "all": "All",
}

_FIXED_ELOCK_ALIASES: Dict[str, str] = {
    "fixed gps na": "GPS NA",
    "fixed lock na": "GPS NA",
    "fixed na": "GPS NA",
    "fixed elock na": "GPS NA",
    "fixed e-lock na": "GPS NA",

    "fixed gps active": "GPS Active",
    "fixed lock active": "GPS Active",
    "fixed elock active": "GPS Active",
    "fixed e-lock active": "GPS Active",

    "fixed no connectivity": "No Connectivity",
    "fixed lock no connectivity": "No Connectivity",
    "fixed elock no connectivity": "No Connectivity",

    "all": "All",
}

_PORTABLE_ELOCK_ALIASES: Dict[str, str] = {
    "portable gps na": "GPS NA",
    "portable lock na": "GPS NA",
    "portable na": "GPS NA",
    "portable elock na": "GPS NA",
    "portable e-lock na": "GPS NA",

    "portable gps active": "GPS Active",
    "portable lock active": "GPS Active",
    "portable elock active": "GPS Active",
    "portable e-lock active": "GPS Active",

    "portable no connectivity": "No Connectivity",
    "portable lock no connectivity": "No Connectivity",
    "portable elock no connectivity": "No Connectivity",

    "all": "All",
}

_SUPERVISOR_EXCEPTION_ALIASES: Dict[str, int] = {
    "vehicle outside master": 1,
    "route outside master": 2,
    "fleet outside master": 3,
    "delayed departure": 4,
    "departure delayed": 4,
    "delayed arrival": 5,
    "arrival delayed": 5,
    "tt delayed": 6,
    "transit time delayed": 6,
    "trip manual close": 7,
    "manual close": 7,
    "manually closed": 7,
    "trip cancelled": 8,
    "trip cancel": 8,
    "all exceptions": 0,
    "all": 0,
}

# Signals around each filter group – used to constrain fuzzy search scope
_REGION_SIGNALS = (
    "region", "zone", "area", "north", "south", "east", "west",
    "northern", "southern", "eastern", "western",
)
_CUSTOMER_SIGNALS = (
    "customer", "client", "account", "party", "cust",
)
_ROUTE_SIGNALS = (
    "route", "path", "leg", "sector",
)
_SHIPMENT_METHOD_SIGNALS = (
    "air", "sfc", "surface", "feeder", "speed", "delivery",
    "pick up", "pickup", "empty", "comm", "linehaul", "lh",
)
_TRIP_STATUS_SIGNALS = (
    "active", "running", "closed", "completed", "cancelled",
    "live", "open", "done", "scheduled", "cancel",
)
_VENDOR_SIGNALS = (
    "vendor", "gps vendor", "axestrack", "lynkit", "kiasaint",
    "ilgic", "third party", "3rd party",
)
_GPS_EXCEPTION_SIGNALS = (
    "gps na", "gps active", "gps inactive", "no connectivity",
    "gps status",
)
_FIXED_ELOCK_SIGNALS = (
    "fixed lock", "fixed gps", "fixed elock", "fixed e-lock",
)
_PORTABLE_ELOCK_SIGNALS = (
    "portable lock", "portable gps", "portable elock", "portable e-lock",
)


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class FilterMetadataService:
    """
    Singleton service that loads filter.json once and exposes
    `extract_filters(query) -> dict` for downstream pipeline use.
    """

    _instance: Optional["FilterMetadataService"] = None
    _lock = threading.Lock()

    # ------------------------------------------------------------------
    # Singleton constructor
    # ------------------------------------------------------------------

    def __new__(cls) -> "FilterMetadataService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        with self.__class__._lock:
            if self._initialized:
                return
            self._load()
            self._initialized = True

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Parse filter.json and build in-memory lookup structures."""
        path = _FILTER_JSON_PATH
        logger.info("[FilterMetadataService] Loading from %s", path)

        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)

        master: Dict[str, Any] = raw["Filter"]["Master"]

        # ── raw sets ──────────────────────────────────────────────────
        self._regions: Dict[str, str] = master.get("Region", {})          # code → display
        self._customers: Dict[str, str] = master.get("Customer", {})       # code → code
        self._routes: Dict[str, str] = master.get("Route", {})             # name → name
        self._route_categories: Dict[str, str] = master.get("RouteCategory", {})
        self._route_types: Dict[str, Any] = master.get("RouteType", {})
        self._trip_statuses: Dict[str, str] = master.get("TripStatus", {})
        self._vendors: Dict[str, str] = master.get("Vendor", {})
        self._fixed_gps_exc: Dict[str, str] = master.get("FixedGPSException", {})
        self._fixed_elock_exc: Dict[str, str] = master.get("FixedELockException", {})
        self._portable_elock_exc: Dict[str, str] = master.get("PortableELockException", {})
        self._eta_delays: Dict[str, str] = master.get("ETADelay", {})
        self._supervisor_exc: Dict[str, str] = master.get("SupervisorException", {})

        # ── normalised lists for fuzzy search ─────────────────────────
        # Customer codes are short (≤ 4 chars) — exact-only, stored uppercase
        self._customer_codes: List[str] = [c.upper() for c in self._customers]

        # Route names can be fuzzy matched (they can be long compound codes)
        self._route_names: List[str] = list(self._routes.keys())

        # Region display values for fuzzy (e.g. "NORTH(NORTH)")
        self._region_codes: List[str] = list(self._regions.keys())

        logger.info(
            "[FilterMetadataService] Loaded: %d regions, %d customers, %d routes",
            len(self._regions),
            len(self._customers),
            len(self._routes),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_filters(self, query: str) -> Dict[str, Any]:
        """
        Extract structured filters from a natural-language query.

        Returns a dict with zero or more of these keys:
          region_code, customer_code, route_name, route_category,
          shipment_method, trip_status, gps_vendor, fixed_gps_exception,
          fixed_elock_exception, portable_elock_exception,
          eta_delay_hrs, supervisor_exception
        """
        q = query.strip()
        q_lower = q.lower()
        result: Dict[str, Any] = {}

        # Order matters: more specific first
        self._extract_customer(q_lower, result)
        self._extract_region(q_lower, result)
        self._extract_shipment_method(q_lower, result)
        self._extract_trip_status(q_lower, result)
        self._extract_route_category(q_lower, result)
        self._extract_vendor(q_lower, result)
        self._extract_gps_exception(q_lower, result)
        self._extract_fixed_elock(q_lower, result)
        self._extract_portable_elock(q_lower, result)
        self._extract_supervisor_exception(q_lower, result)
        self._extract_route(q_lower, result)

        return result

    # ------------------------------------------------------------------
    # Per-filter extractors
    # ------------------------------------------------------------------

    def _extract_region(self, text: str, out: Dict[str, Any]) -> None:
        if "region_code" in out:
            return

        # 1. Alias table (longest match wins)
        match = self._alias_match(text, _REGION_ALIASES)
        if match:
            out["region_code"] = match
            return

        # 2. Direct code match (e.g. "NORTH", "WEST2")
        for code in self._region_codes:
            if re.search(r"\b" + re.escape(code.lower()) + r"\b", text):
                out["region_code"] = code
                return

        # 3. Fuzzy – only when a region signal is present
        if not any(sig in text for sig in _REGION_SIGNALS):
            return

        candidate = self._fuzzy_match(text, self._region_codes)
        if candidate:
            out["region_code"] = candidate

    # Tokens that look like customer codes but are actually domain keywords
    _CUSTOMER_CODE_BLACKLIST = frozenset({
        "ALL", "AIR", "SFC", "LH", "GPS", "ATA", "ATD",
        "HO", "AND", "YES", "FOR", "THE",
    })

    def _extract_customer(self, text: str, out: Dict[str, Any]) -> None:
        if "customer_code" in out:
            return

        # Customer signal must be present to avoid false positives on
        # 3-letter route codes or vehicle numbers
        has_signal = any(sig in text for sig in _CUSTOMER_SIGNALS)

        # 1. Word-boundary exact match on any customer code
        #    Only fire when a customer signal word is also present
        #    (prevents domain-keyword tokens like AIR / ALL from matching)
        if has_signal:
            for code in self._customer_codes:
                if code in self._CUSTOMER_CODE_BLACKLIST:
                    continue
                if re.search(r"\b" + re.escape(code.lower()) + r"\b", text.lower()):
                    out["customer_code"] = code
                    return

        # 2. Fuzzy only when "customer" keyword is present
        if not has_signal:
            return

        # Extract the token(s) near the customer signal
        token = self._extract_token_near(text, _CUSTOMER_SIGNALS, max_len=6)
        if token and len(token) >= 2 and token.upper() not in self._CUSTOMER_CODE_BLACKLIST:
            candidate = self._fuzzy_match_code(token.upper(), self._customer_codes)
            if candidate and candidate not in self._CUSTOMER_CODE_BLACKLIST:
                out["customer_code"] = candidate

    def _extract_shipment_method(self, text: str, out: Dict[str, Any]) -> None:
        if "shipment_method" in out:
            return

        if not any(sig in text for sig in _SHIPMENT_METHOD_SIGNALS):
            return

        match = self._alias_match(text, _SHIPMENT_METHOD_ALIASES)
        if match:
            out["shipment_method"] = match

    def _extract_trip_status(self, text: str, out: Dict[str, Any]) -> None:
        if "trip_status" in out:
            return

        match = self._alias_match(text, _TRIP_STATUS_ALIASES)
        if match is not None:
            out["trip_status"] = match

    def _extract_route_category(self, text: str, out: Dict[str, Any]) -> None:
        if "route_category" in out:
            return

        match = self._alias_match(text, _ROUTE_CATEGORY_ALIASES)
        if match is not None:
            out["route_category"] = match

    def _extract_vendor(self, text: str, out: Dict[str, Any]) -> None:
        if "gps_vendor" in out:
            return

        if not any(sig in text for sig in _VENDOR_SIGNALS):
            return

        match = self._alias_match(text, _VENDOR_ALIASES)
        if match:
            out["gps_vendor"] = match

    def _extract_gps_exception(self, text: str, out: Dict[str, Any]) -> None:
        if "gps_exception" in out:
            return

        if not any(sig in text for sig in _GPS_EXCEPTION_SIGNALS):
            return

        match = self._alias_match(text, _GPS_EXCEPTION_ALIASES)
        if match:
            out["gps_exception"] = match

    def _extract_fixed_elock(self, text: str, out: Dict[str, Any]) -> None:
        if "fixed_elock_exception" in out:
            return

        if not any(sig in text for sig in _FIXED_ELOCK_SIGNALS):
            return

        match = self._alias_match(text, _FIXED_ELOCK_ALIASES)
        if match:
            out["fixed_elock_exception"] = match

    def _extract_portable_elock(self, text: str, out: Dict[str, Any]) -> None:
        if "portable_elock_exception" in out:
            return

        if not any(sig in text for sig in _PORTABLE_ELOCK_SIGNALS):
            return

        match = self._alias_match(text, _PORTABLE_ELOCK_ALIASES)
        if match:
            out["portable_elock_exception"] = match

    # Phrases that must appear in text to trigger supervisor exception extraction
    _SUPERVISOR_SIGNALS = (
        "supervisor", "exception", "vehicle outside", "route outside",
        "fleet outside", "delayed departure", "delayed arrival",
        "departure delayed", "arrival delayed", "tt delayed",
        "transit time delayed", "manual close", "manually closed",
        "trip manual", "trip cancel",
    )

    def _extract_supervisor_exception(self, text: str, out: Dict[str, Any]) -> None:
        if "supervisor_exception" in out:
            return

        # Only try when an explicit exception-related signal is present
        # (avoids matching 'all' in generic queries like 'all sfc network trips')
        if not any(sig in text for sig in self._SUPERVISOR_SIGNALS):
            return

        match = self._alias_match(text, _SUPERVISOR_EXCEPTION_ALIASES)
        if match is not None:
            out["supervisor_exception"] = match

    def _extract_route(self, text: str, out: Dict[str, Any]) -> None:
        """Extract a route code from the query using exact/fuzzy match.

        Route codes in Bluedart look like 'XNA', 'XNA-BVL', 'XND' etc.
        We only fuzzy-match when a route signal is present OR the token
        pattern looks like a compound route code.
        """
        if "route_name" in out:
            return

        has_signal = any(sig in text for sig in _ROUTE_SIGNALS)

        # Exact word match against all route names (they are ALL-CAPS codes)
        for rn in self._route_names:
            if re.search(r"\b" + re.escape(rn.lower()) + r"\b", text):
                out["route_name"] = rn
                return

        if not has_signal:
            return

        # Fuzzy – extract the token right after "route"
        token = self._extract_token_near(text, ("route",), max_len=25)
        if token and len(token) >= 2:
            candidate, score, _ = process.extractOne(
                token.upper(),
                self._route_names,
                scorer=fuzz.token_sort_ratio,
            ) or (None, 0, None)
            if candidate and score >= _FUZZY_THRESHOLD:
                out["route_name"] = candidate

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _alias_match(text: str, table: Dict[str, Any]) -> Optional[Any]:
        """Longest-phrase-first lookup in an alias table."""
        sorted_keys = sorted(table.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if re.search(r"\b" + re.escape(key) + r"\b", text):
                return table[key]
        return None

    @staticmethod
    def _fuzzy_match(text: str, choices: List[str], threshold: int = _FUZZY_THRESHOLD) -> Optional[str]:
        """Return the best fuzzy match from `choices` against `text`."""
        if not choices:
            return None
        result = process.extractOne(
            text,
            choices,
            scorer=fuzz.token_sort_ratio,
        )
        if result and result[1] >= threshold:
            return result[0]
        return None

    @staticmethod
    def _fuzzy_match_code(token: str, codes: List[str], threshold: int = 85) -> Optional[str]:
        """Exact/near-exact match for short uppercase codes (customer, route)."""
        token_upper = token.upper()
        # exact
        if token_upper in codes:
            return token_upper
        # one-character typo via rapidfuzz
        result = process.extractOne(
            token_upper,
            codes,
            scorer=fuzz.ratio,
        )
        if result and result[1] >= threshold:
            return result[0]
        return None

    @staticmethod
    def _extract_token_near(text: str, signals: Tuple[str, ...], max_len: int = 10) -> Optional[str]:
        """
        Extract the first word token that appears right after any signal word.
        e.g. text="show shipment for ndj customer", signals=("customer",)
             → scans for tokens before/after "customer" → returns "NDJ"
        """
        for sig in signals:
            # Token after signal
            m = re.search(
                r"\b" + re.escape(sig) + r"\b\s+([a-z0-9_\-]{1," + str(max_len) + r"})",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                return m.group(1)
            # Token before signal
            m = re.search(
                r"\b([a-z0-9_\-]{1," + str(max_len) + r"})\s+" + re.escape(sig) + r"\b",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                return m.group(1)
        return None


# ---------------------------------------------------------------------------
# Module-level accessor (use this everywhere)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_filter_metadata_service() -> FilterMetadataService:
    """Return the singleton FilterMetadataService (cached after first call)."""
    return FilterMetadataService()
