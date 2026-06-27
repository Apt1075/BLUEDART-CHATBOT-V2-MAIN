from __future__ import annotations

import re
import os
import json

CUSTOMER_CODES = set()
try:
    # Resolve path to app/v2/filter.json relative to this file
    _filter_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "filter.json")
    if os.path.exists(_filter_path):
        with open(_filter_path, "r") as f:
            _filter_data = json.load(f)
            _cust_dict = _filter_data.get("Filter", {}).get("Master", {}).get("Customer", {})
            CUSTOMER_CODES = {k.upper() for k in _cust_dict.keys()}
except Exception as e:
    print("[query-planner] Failed to load customer codes:", e)

from typing import Any, Dict

from app.v2.confidence import ConfidenceResult
from app.v2.models import DetectedIntent, EntityBundle, QueryPlan
from app.v2.planners.analytics_planner import AnalyticsPlanner
from app.v2.planners.trip_report_filters import (
    ROUTE_CATEGORY_FILTERS,
    SHIPMENT_METHOD_FILTERS,
    TRIP_REPORT_DEFAULT_TRIP_TYPE,
    TRIP_STATUS_FILTERS,
)


class QueryPlanner:
    BULK_QUERY_TERMS = (
        "download",
        "download all",
        "export",
        "show trips",
        "show",
        "list trips",
        "find trips",
        "filter trips",
        "trip report",
        "trip list",
        "trip history",
        "trip data",
        "all trips",
        "report",
        "filter",
        "search",
        "view",
        "how many",
        "count",
        "number of",
        # ── total-trip signals ───────────────────────────────────────────
        "total trips",
        "total trip",
        "total count",
        "total delay",
        "total alert",
        "total",
        # ── day-wise / month-wise signals ────────────────────────────────
        "day wise",
        "daywise",
        "day-wise",
        "month wise",
        "monthwise",
    )

    # Words that mean the user wants a COUNT (number only)
    COUNT_TERMS = (
        "total",
        "count",
        "how many",
        "number of",
        "how much",
        "all",
        "kitne",
        "kul",
        "total count",
        "total trips",
    )

    # Words that mean the user wants a LIST (triggers xlsx download)
    LIST_TERMS = (
        "list",
        "show list",
        "give list",
        "get list",
        "detail",
        "details",
        "show details",
        "show all details",
        "trip report",
        "trip list",
        "trip history",
        "trip data",
        "download",
        "export",
        "excel",
        "xlsx",
    )

    FILTER_QUERY_TERMS = (
        "trip id",
        "trip no",
        "trip number",
        "trip status",
        "trip type",
        "feeder type",
        "region",
        "origin",
        "destination",
        "route",
        "fleet",
        "transporter",
        "location",
        "gps vendor",
        "gps",
        "fixed gps",
        "fixed elock",
        "portable elock",
        "fixed lock",
        "portable lock",
        "atd",
        "ata",
        "halt",
        "distance",
        "run date",
        "this week",
        "this month",
        "this year",
        "today",
        "yesterday",
        "last week",
        "last month",
        # ── month names ─────────────────────────────────────────────────
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug",
        "sep", "oct", "nov", "dec",
    )

    def __init__(self) -> None:
        self.analytics_planner = AnalyticsPlanner()

    def build_plan(
        self,
        message: str,
        intent: DetectedIntent,
        entities: EntityBundle,
        confidence: ConfidenceResult,
        session_context: Dict[str, Any] | None = None,
    ) -> QueryPlan:
        if self._looks_like_bulk_trip_query(message, entities, session_context):
            return self._plan_bulk_trip_query(message, entities, confidence, intent.secondary_intents, session_context)

        if intent.primary_intent == "analytics":
            return self.analytics_planner.plan(message, entities, confidence.score, intent.secondary_intents)

        if intent.primary_intent in {"gps", "imei"}:
            domain = "gps"
            action = "track"
            executor = "gps_executor"
        elif intent.primary_intent == "customer":
            domain = "customer"
            action = "lookup"
            executor = "customer_executor"
        else:
            domain = "shipment"
            if intent.primary_intent == "stops":
                action = "stops"
            elif intent.primary_intent in {"delay", "alert"}:
                action = intent.primary_intent
            else:
                action = "status"
            executor = "shipment_executor"

        filters: Dict[str, Any] = {}
        if entities.date_from:
            filters["date_from"] = entities.date_from
        if entities.date_to:
            filters["date_to"] = entities.date_to

        clarification_question = None
        requires_clarification = confidence.needs_clarification
        if requires_clarification and entities.is_empty():
            clarification_question = "Please share a shipment number, vehicle number, IMEI, or customer ID so I can route the query deterministically."

        return QueryPlan(
            domain=domain,
            action=action,
            filters=filters,
            entities=entities.to_dict(),
            confidence=confidence.score,
            secondary_intents=intent.secondary_intents,
            requires_clarification=requires_clarification,
            clarification_question=clarification_question,
            preferred_executor=executor,
            trace=["query_planner"],
        )

    def _looks_like_bulk_trip_query(
        self,
        message: str,
        entities: EntityBundle,
        session_context: Dict[str, Any] | None = None,
    ) -> bool:
        # ── Continuation of a pending bulk-trip clarification ──────────────
        # If the bot already asked for a date range / trip-id and the user
        # has now supplied one, treat this reply as the continuation.
        if session_context:
            last_plan = session_context.get("last_plan") or {}
            if (
                isinstance(last_plan, dict)
                and last_plan.get("requires_clarification")
                and last_plan.get("domain") == "analytics"
                and last_plan.get("action") in ("list", "count")
                and (entities.date_from or entities.trip_id or entities.shipment_no)
            ):
                return True

        text = message.lower()
        has_filter_signal = any(term in text for term in self.FILTER_QUERY_TERMS)
        has_bulk_signal = any(term in text for term in self.BULK_QUERY_TERMS) or has_filter_signal
        has_trip_identifier = bool(entities.trip_id or entities.shipment_no)
        has_other_direct_identifier = any(
            getattr(entities, field, None)
            for field in ("vehicle_no", "imei", "customer_id", "route_id", "m_trip_id")
        )

        if has_trip_identifier:
            return has_bulk_signal

        return has_bulk_signal and not has_other_direct_identifier

    def _plan_bulk_trip_query(
        self,
        message: str,
        entities: EntityBundle,
        confidence: ConfidenceResult,
        secondary_intents: list[str],
        session_context: Dict[str, Any] | None = None,
    ) -> QueryPlan:
        text = message.lower()
        filters: Dict[str, Any] = {"metric": "trip", "mode": "list"}
        trip_identifier = entities.trip_id or entities.shipment_no
        if trip_identifier:
            filters["trip_id"] = trip_identifier

        filters.update(self.analytics_planner._extract_filter_conditions(text, entities))

        # Check if the user is answering a previous origin/destination clarification question
        is_answering_clarification = False
        clarified_code = None
        if session_context:
            last_plan = session_context.get("last_plan") or {}
            last_question = last_plan.get("clarification_question") or ""
            match = re.search(r"do you want\s+([a-z0-9]+)\s+as\s+origin\s+or\s+destination", last_question, re.IGNORECASE)
            if match:
                clarified_code = match.group(1).upper()
                if "origin" in text:
                    filters["source_code"] = clarified_code
                    is_answering_clarification = True
                elif "destination" in text:
                    filters["destination_code"] = clarified_code
                    is_answering_clarification = True

        if entities.date_from:
            filters["date_from"] = entities.date_from
        if entities.date_to:
            filters["date_to"] = entities.date_to
        
        trip_filters = self._extract_trip_filters(message)

        pending_filters = self._pending_filters(session_context)
        for key, value in pending_filters.items():
            if key in {"metric", "mode", "date_from", "date_to"}:
                continue
            filters.setdefault(key, value)
        filters.update(trip_filters)
        # ── Determine action: list request is explicit, otherwise default to count ──
        is_list_request = any(term in text for term in self.LIST_TERMS)
        is_count_request = any(term in text for term in self.COUNT_TERMS)

        if is_list_request:
            action = "list"
            # List action always produces an xlsx download
            filters["export"] = "excel"
            filters.setdefault("limit", 10000)
        else:
            # Default to count even if no explicit count keyword (e.g. "May month trips")
            action = "count"

        # A bulk query only needs clarification when there is no date AND no trip ID
        requires_clarification = not trip_identifier and not entities.date_from
        clarification_question = None

        if not filters.get("source_code") and not filters.get("destination_code") and not is_answering_clarification:
            words = [w.upper() for w in re.findall(r"\b[a-zA-Z0-9]{3,5}\b", text)]
            stopwords = {"TRIP", "TRIPS", "TOTAL", "LIST", "SHOW", "FIND", "VIEW", "DATE", "WEEK", "JUNE", "JULY", "WENT", "FROM", "TO", "SFC", "GPS", "LOCK"}
            ambiguous_codes = [w for w in words if w in CUSTOMER_CODES and w not in stopwords]
            if ambiguous_codes:
                ambiguous_code = ambiguous_codes[0]
                requires_clarification = True
                clarification_question = f"Do you want {ambiguous_code} as origin or destination?"

        if requires_clarification and not clarification_question:
            clarification_question = (
                "Please share a date range first, or say this week, this month, or this year. "
                "If you already have a trip ID, send that and I will use it directly."
            )

        return QueryPlan(
            domain="analytics",
            action=action,
            filters={k: v for k, v in filters.items() if v is not None},
            entities=entities.to_dict(),
            confidence=confidence.score,
            secondary_intents=secondary_intents,
            requires_clarification=requires_clarification,
            clarification_question=clarification_question,
            preferred_executor="analytics_executor",
            trace=["query_planner", "bulk_trip_query"],
        )

    def _is_download_request(self, text: str) -> bool:
        return any(term in text for term in ("download", "export", "excel", "xlsx"))

    def _pending_filters(self, session_context: Dict[str, Any] | None) -> Dict[str, Any]:
        if not session_context:
            return {}

        last_plan = session_context.get("last_plan") or {}
        if not isinstance(last_plan, dict):
            return {}
        if not last_plan.get("requires_clarification"):
            return {}
        if last_plan.get("domain") != "analytics" or last_plan.get("action") != "list":
            return {}

        filters = last_plan.get("filters") or {}
        return filters if isinstance(filters, dict) else {}

    def _extract_trip_filters(self, message: str) -> Dict[str, Any]:
        """
        Extract trip-specific filters from message: trip_status, trip_type, shipment_method,
        GPS/IMEI, GPS vendor, and exception/connectivity status.
        """
        text = message.lower()
        trip_filters: Dict[str, Any] = {}

        # ───────────────────────────────────────────────────────────────────
        # TRIP TYPE: intracity (2) vs intercity (1)
        # ───────────────────────────────────────────────────────────────────
        for keyword, value in sorted(ROUTE_CATEGORY_FILTERS.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(r"\b" + re.escape(keyword) + r"\b", text):
                trip_filters["trip_type"] = value
                break

        # ───────────────────────────────────────────────────────────────────
        # TRIP STATUS: running/active (1) vs closed/completed (0) vs cancelled (2)
        # ───────────────────────────────────────────────────────────────────
        device_state_query = any(
            phrase in text
            for phrase in ["gps inactive", "gps na", "gps active", "fixed lock", "fixed e-lock", "portable lock", "portable e-lock"]
        )

        if re.search(r"\brunning trip(s)?\b|\bactive trip(s)?\b|\blive trip(s)?\b|\bopen trip\b|\bin transit\b", text):
            trip_filters["trip_status"] = TRIP_STATUS_FILTERS["running"]
        elif not device_state_query and re.search(r"\binactive trip(s)?\b|\bclosed trip(s)?\b|\bcompleted trip(s)?\b|\bdone trip(s)?\b|\bcompleted\b|\bdone\b", text):
            trip_filters["trip_status"] = TRIP_STATUS_FILTERS["completed"]
        elif any(w in text for w in ["cancelled", "canceled"]):
            trip_filters["trip_status"] = TRIP_STATUS_FILTERS["cancelled"]

        # ───────────────────────────────────────────────────────────────────
        # SHIPMENT METHOD
        # ───────────────────────────────────────────────────────────────────
        for keyword, value in sorted(SHIPMENT_METHOD_FILTERS.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(r"\b" + re.escape(keyword) + r"\b", text):
                trip_filters["shipment_method"] = value
                break

        # ───────────────────────────────────────────────────────────────────
        # GPS VENDOR
        # ───────────────────────────────────────────────────────────────────
        vendor_filters = self._extract_gps_vendor_filters(text)
        trip_filters.update(vendor_filters)

        # ───────────────────────────────────────────────────────────────────
        # GPS / IMEI / E-LOCK (Primary, Fixed, Portable)
        # ───────────────────────────────────────────────────────────────────
        device_filters = self._extract_device_filters(text)
        trip_filters.update(device_filters)

        # ───────────────────────────────────────────────────────────────────
        # ROUTE / PARTY / LOCATION / STATUS DETAILS
        # ───────────────────────────────────────────────────────────────────

        # Words that must never be treated as route codes
        _ROUTE_CODE_STOPWORDS = {
            "total", "trips", "trip", "all", "route", "count",
            "show", "list", "in", "for", "the", "of", "by",
            "shipments", "shipment", "data", "report", "detail",
            "may", "june", "july", "august",
            "september", "october", "november", "december",
            "january", "february", "march", "april", "name",
        }

        # Check for explicit hyphenated route code (e.g. PAW-PAX-RAW)
        hyphenated_route = None
        hyphenated_match = re.search(r"\b(?=[a-z0-9-]*[a-z])([a-z0-9]{2,6}(?:-[a-z0-9]{2,6})+)\b", text)
        if hyphenated_match:
            candidate = hyphenated_match.group(1).upper()
            parts = candidate.split("-")
            if not any(part.lower() in _ROUTE_CODE_STOPWORDS for part in parts):
                hyphenated_route = candidate

        # Prefer hyphenated route code if present, otherwise fall back to route name patterns
        route_name = hyphenated_route
        if not route_name:
            # Pattern 1: code AFTER 'route'  →  "route HKI"  or  "route name HKI"
            route_name_after = self._extract_named_value(
                text,
                [r"\broute\s+(?:name\s+)?([a-z0-9][a-z0-9\-_/]*)\b"],
            )
            # Pattern 2: code BEFORE 'route' →  "HKI route"
            # Require 2-8 alphanumeric chars; stop at common non-code words via stopword check
            route_name_before = self._extract_named_value(
                text,
                [r"\b([a-z0-9]{2,8})\s+route\b"],
            )

            # Prefer the one that is not a stopword
            for candidate in (route_name_after, route_name_before):
                if candidate and candidate.lower() not in _ROUTE_CODE_STOPWORDS:
                    route_name = candidate.upper()
                    break

        if route_name:
            if "-" in route_name:
                trip_filters["route_code"] = route_name
            else:
                trip_filters["route_name"] = route_name
                # Also search by source_code / destination_code so we catch
                # legs where route_name doesn't match but source/dest do
                # (only when no explicit from/to codes were given)
                if "source_code" not in trip_filters and "destination_code" not in trip_filters:
                    trip_filters["route_code_search"] = route_name  # handled in executor

        fleet_no = self._extract_named_value(text, [r"\bfleet\s+(?:no\s+)?([a-z0-9][a-z0-9\-_/]*)\b"])
        if fleet_no:
            trip_filters["fleet_no"] = fleet_no.upper()

        transporter = self._extract_named_value(
            text,
            [r"\btransporter\s+(?:name\s+)?(?:is\s+|of\s+|for\s+|belonging\s+to\s+)?([a-z0-9][a-z0-9\-_/]*)\b"],
        )
        if transporter:
            trip_filters["transporter_name"] = transporter

        location_code = self._extract_named_value(text, [r"\bfrom\s+([a-z0-9]{2,8})\s+location\b", r"\blocation\s+([a-z0-9]{2,8})\b"])
        if location_code and "source_code" not in trip_filters:
            trip_filters["source_code"] = location_code.upper()

        destination_code = self._extract_named_value(text, [r"\bdestination\s+(?:is\s+|to\s+|for\s+)?([a-z0-9][a-z0-9\-_/]*)\b"])
        if destination_code:
            trip_filters["destination_code"] = destination_code.upper()

        if any(w in text for w in ["forcefully closed", "force closed", "supervisor close"]):
            trip_filters["close_remarks"] = "__regex_supervisor__"

        if "captured through gps" in text:
            trip_filters["ata_source"] = "GPS"
        elif "captured manually" in text:
            trip_filters["ata_source"] = "MANUAL"
        elif "captured through api" in text:
            trip_filters["ata_source"] = "API"

        if "atd" in text and any(w in text for w in ["not captured", "missing", "was not"]):
            trip_filters["actual_source_departure_time"] = "__empty__"
        if "ata" in text and any(w in text for w in ["not captured", "missing", "was not"]):
            trip_filters["actual_destination_arrival_time"] = "__empty__"

        if any(w in text for w in ["maximum halt", "max halt", "halt duration"]):
            trip_filters["sort_by"] = "halt_duration"
            trip_filters["sort_order"] = "desc"
            trip_filters.setdefault("limit", 1)
        elif any(w in text for w in ["maximum distance", "max distance", "covered maximum distance"]):
            trip_filters["sort_by"] = "distance_km"
            trip_filters["sort_order"] = "desc"
            trip_filters.setdefault("limit", 1)
        elif any(w in text for w in ["highest number of trips", "maximum trips", "max trips", "most trips", "vehicle with most"]):
            trip_filters["top_n"] = 1

        return trip_filters

    def _extract_gps_vendor_filters(self, text: str) -> Dict[str, Any]:
        """Extract GPS vendor filters: primary/2nd/3rd, 3rd party, wheelseye."""
        filters: Dict[str, Any] = {}

        if "wheelseye" in text:
            filters["gps_vendor_wheelseye"] = True
        elif any(w in text for w in ["3rd party", "third party", "third-party"]):
            filters["gps_vendor_3rdparty"] = True
        elif "vendor" in text or "gps vendor" in text:
            filters["gps_vendor_any"] = True

        return filters

    def _extract_device_filters(self, text: str) -> Dict[str, Any]:
        """
        Extract GPS/IMEI/E-Lock filters for imei_no, imei_no2, imei_no3 and their exceptions.
        """
        filters: Dict[str, Any] = {}

        device_specs = [
            {
                "field": "imei_no_type",
                "value": 1,
                "exception_field": "exception_common_backend",
                "mention_terms": ["gps"],
                "active_terms": ["gps active", "gps working", "gps connected"],
                "inactive_terms": ["gps inactive", "gps no connectivity", "gps not working", "gps offline"],
                "na_terms": ["gps na", "gps status is na", "gps is na", "gps status na", "gps status: na"],
            },
            {
                "field": "imei_no_type2",
                "value": 12,
                "exception_field": "exception_common_backend_2",
                "mention_terms": ["fixed e-lock", "fixed elock", "fixed lock", "fixed"],
                "active_terms": ["fixed active", "fixed working", "fixed connected", "fixed lock active"],
                "inactive_terms": ["fixed inactive", "fixed no connectivity", "fixed not working", "fixed lock inactive"],
                "na_terms": ["fixed na", "fixed lock na", "fixed gps na", "fixed lock gps na"],
            },
            {
                "field": "imei_no_type3",
                "value": 13,
                "exception_field": "exception_common_backend_3",
                "mention_terms": ["portable e-lock", "portable elock", "portable lock", "portable"],
                "active_terms": ["portable active", "portable working", "portable connected", "portable lock active"],
                "inactive_terms": ["portable inactive", "portable no connectivity", "portable not working", "portable lock inactive"],
                "na_terms": ["portable na", "portable lock na", "portable gps na", "portable lock gps na"],
            },
        ]

        for spec in device_specs:
            if not self._mentions_device(text, spec):
                continue

            filters[spec["field"]] = spec["value"]
            state = self._detect_device_state(text, spec)
            if state == "active":
                filters[spec["exception_field"]] = ""
            elif state == "inactive":
                filters[spec["exception_field"]] = "No Connectivity"
            elif state == "na":
                filters[spec["exception_field"]] = "NA"

        if "all devices inactive" in text or "all devices no connectivity" in text:
            filters["all_devices_no_connectivity"] = True
        elif "all devices na" in text:
            filters["all_devices_na"] = True

        return filters

    def _mentions_device(self, text: str, spec: Dict[str, Any]) -> bool:
        return any(term in text for term in spec["mention_terms"])

    def _detect_device_state(self, text: str, spec: Dict[str, Any]) -> str | None:
        if any(term in text for term in spec["na_terms"]):
            return "na"
        if any(term in text for term in spec["inactive_terms"]):
            return "inactive"
        if any(term in text for term in spec["active_terms"]):
            return "active"
        return None

    def _extract_named_value(self, text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None
