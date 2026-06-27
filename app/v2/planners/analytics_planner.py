from __future__ import annotations

import re
from typing import Any, Dict

from app.v2.models import EntityBundle, QueryPlan
from app.v2.planners.trip_report_filters import (
    DEVICE_EXCEPTION_FILTERS,
    REGION_FILTERS,
    ROUTE_CATEGORY_FILTERS,
    SHIPMENT_METHOD_FILTERS,
    TRIP_REPORT_DEFAULT_TRIP_TYPE,
    TRIP_STATUS_FILTERS,
    VENDOR_FILTERS,
)


class AnalyticsPlanner:
    FILTER_KEYWORDS: Dict[str, Dict[str, Any]] = {
        **{keyword: {"shipment_method": value} for keyword, value in SHIPMENT_METHOD_FILTERS.items()},
        **{keyword: {"region_code": value} for keyword, value in REGION_FILTERS.items()},
        **{f"{keyword} region": {"region_code": value} for keyword, value in REGION_FILTERS.items()},
        **{keyword: {"trip_type": value} for keyword, value in ROUTE_CATEGORY_FILTERS.items()},
        **{keyword: {"trip_status": value} for keyword, value in TRIP_STATUS_FILTERS.items()},
        **DEVICE_EXCEPTION_FILTERS,
        **VENDOR_FILTERS,
        "cancelled": {"trip_status": 2},
        "closed trips": {"trip_status": 0},
        "active only": {"trip_status": 1},
        "active trips": {"trip_status": 1},
        "axestrack": {"gps_vendor_name": "Axestrack_bluedart"},
        "kiasaint": {"gps_vendor_name": "Kiasaint_bluedart"},
        "lynkit": {"gps_vendor_name": "Lynkit_Bluedart"},
        "wheelseye": {"gps_vendor_name": "wheelseye_bluedart"},
        "secutrak": {"gps_vendor_name": "Secutrak"},
        "icici": {"gps_vendor_name": "ICICI"},
        "3rd party": {"gps_vendor_3rdparty": True},
        "third party": {"gps_vendor_3rdparty": True},
        "without portable lock": {"imei_no3": "__empty__"},
        "with portable lock": {"imei_no3": "__exists__"},
        "without fixed lock": {"imei_no2": "__empty__"},
        "with fixed lock": {"imei_no2": "__exists__"},
    }

    def plan(self, message: str, entities: EntityBundle, confidence: float, secondary_intents: list[str]) -> QueryPlan:
        text = message.lower()
        action = "count"
        if any(token in text for token in ("trend", "over time", "monthly", "daily")):
            action = "trend"
        elif any(token in text for token in ("compare", "vs", "versus")):
            action = "compare"
        elif any(token in text for token in ("top", "highest", "max", "maximum")):
            action = "top_n"
        elif any(token in text for token in ("group by", "breakdown", "by route", "by vehicle")):
            action = "group_by"

        filters: Dict[str, Any] = {
            "metric": self._detect_metric(text),
            "group_by": self._detect_group_by(text),
            "period": self._detect_period(text),
            "top_n": self._detect_top_n(text),
        }
        if entities.date_from:
            filters["date_from"] = entities.date_from
        if entities.date_to:
            filters["date_to"] = entities.date_to

        filters.update(self._extract_filter_conditions(text, entities))
        self._apply_trip_report_defaults(text, filters)
        if self._is_download_request(text) and filters.get("metric") == "trip":
            filters["export"] = "excel"
            filters.setdefault("limit", 10000)

        return QueryPlan(
            domain="analytics",
            action=action,
            filters={k: v for k, v in filters.items() if v is not None},
            entities=entities.to_dict(),
            confidence=confidence,
            secondary_intents=secondary_intents,
            preferred_executor="analytics_executor",
            trace=["analytics_planner"],
        )

    def _detect_metric(self, text: str) -> str:
        if "delay" in text:
            return "delay"
        if "alert" in text:
            return "alert"
        if "trip" in text or "shipment" in text:
            return "trip"
        return "count"

    def _detect_group_by(self, text: str) -> str | None:
        if "vehicle" in text:
            return "vehicle_no"
        if "route" in text:
            return "route_name"
        if "customer" in text:
            return "customer_id"
        return None

    def _detect_period(self, text: str) -> str | None:
        if "today" in text:
            return "today"
        if "week" in text:
            return "week"
        if "month" in text:
            return "month"
        return None

    def _detect_top_n(self, text: str) -> int | None:
        match = re.search(r"top\s+(\d+)", text)
        return int(match.group(1)) if match else None

    def _extract_filter_conditions(self, text: str, entities: EntityBundle) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}

        device_state_query = any(term in text for term in ["gps", "fixed", "portable"]) and any(
            term in text for term in ["inactive", "active", "na", "no connectivity"]
        )

        if not device_state_query:
            if re.search(r"\bcancelled\b|\bcanceled\b", text):
                filters["trip_status"] = TRIP_STATUS_FILTERS["cancelled"]
            elif re.search(r"\bclosed trip(s)?\b|\bcompleted trip(s)?\b|\binactive trip(s)?\b", text):
                filters["trip_status"] = TRIP_STATUS_FILTERS["completed"]
            elif re.search(r"\bactive trip(s)?\b|\bactive only\b", text):
                filters["trip_status"] = TRIP_STATUS_FILTERS["active"]

        trip_identifier = entities.trip_id or entities.shipment_no
        if trip_identifier:
            filters["trip_id"] = trip_identifier

        if entities.vehicle_no:
            filters["vehicle_no"] = entities.vehicle_no

        source_code = self._extract_code(text, r"\bfrom\s+([a-z0-9]{2,5})\b")
        if source_code:
            filters["source_code"] = source_code.upper()

        destination_code = self._extract_code(text, r"\bto\s+([a-z0-9]{2,5})\b")
        if destination_code:
            filters["destination_code"] = destination_code.upper()

        temp_text = text[:]
        for keyword, mapping in sorted(self.FILTER_KEYWORDS.items(), key=lambda item: len(item[0]), reverse=True):
            if keyword in set(TRIP_STATUS_FILTERS) | {"closed trips", "active only", "active trips"}:
                continue
            if not re.search(r"\b" + re.escape(keyword) + r"\b", temp_text):
                continue
            for field, value in mapping.items():
                if field in filters:
                    continue
                filters[field] = value
            # Replace matched keyword with spaces to prevent shorter sub-phrases from matching
            temp_text = re.sub(r"\b" + re.escape(keyword) + r"\b", " " * len(keyword), temp_text)

        return filters

    def _apply_trip_report_defaults(self, text: str, filters: Dict[str, Any]) -> None:
        pass

    def _looks_like_trip_report_query(self, text: str, filters: Dict[str, Any]) -> bool:
        report_terms = ("download", "export", "report", "show", "list", "count", "how many")
        return bool(filters.get("trip_status") is not None or any(term in text for term in report_terms))

    def _is_download_request(self, text: str) -> bool:
        return any(term in text for term in ("download", "export", "excel", "xlsx"))

    def _extract_code(self, text: str, pattern: str) -> str | None:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(1)
            temporal_words = {"last", "this", "today", "week", "month", "year"}
            if value.lower() in temporal_words:
                continue

            # Check if this match is part of a date (e.g. followed by -MM or /MM)
            span = match.span(1)
            remaining = text[span[1]:]
            if re.match(r'^[-/]\d+', remaining):
                continue

            return value

        return None
