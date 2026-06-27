from __future__ import annotations

import httpx
import json
from typing import Any, Dict, List

from app.core.data_aggregator import GROUP_ID, MONGO_TIMEOUT, STATUS_ON, mongo_select
from app.core.response_formatter import html_table, metric_box, td
from app.v2.analytics.comparisons import compare_counts
from app.v2.analytics.aggregations import group_by, top_n
from app.v2.analytics.trend_analysis import trend_series
from app.v2.exporters.trip_report_excel import create_trip_report_excel
from app.v2.models import EntityBundle, ExecutionResult, QueryPlan


class AnalyticsExecutor:
    def __init__(self) -> None:
        self.default_collection = "courier_trip_detail"

    async def execute(self, plan: QueryPlan, entities: EntityBundle, access_token: str | None = None) -> ExecutionResult:
        table = self._resolve_table(plan)
        date_field = "entry_date" if table == "courier_route_delay" else "run_date"
        conditions = {"group_id": GROUP_ID, "status": STATUS_ON}
        self._apply_filters(conditions, plan, entities, date_field)

        action = plan.action
        sort = self._sort_for_plan(plan)

        # For count queries, we only need the total — fetch with a high limit but minimal projection
        if action == "count":
            limit = 10000
            fields = {"_id": 1}  # minimal projection for speed
        else:
            limit = int(plan.filters.get("limit") or 10000)
            fields = self._projection_for(plan)

        api_req_metadata = None
        if table in ("courier_trip_detail", "courier_trip_detail_customer"):
            from app.core.external_api_client import fetch_trip_report_api
            date_cond = conditions.get(date_field, {})
            date_from = date_cond.get("$gte") if isinstance(date_cond, dict) else None
            date_to = date_cond.get("$lte") if isinstance(date_cond, dict) else None
            api_res = await fetch_trip_report_api(date_from, date_to, access_token, plan.filters)
            records = api_res.get("records", [])
            api_req_metadata = api_res.get("api_request")
            
            # Apply memory filtering for shipment_no/trip_id if filtered
            shipment_no_filter = conditions.get("shipment_no")
            if shipment_no_filter and isinstance(records, list):
                records = [r for r in records if str(r.get("shipment_no")) == str(shipment_no_filter) or str(r.get("trip_id")) == str(shipment_no_filter)]
        else:
            async with httpx.AsyncClient(timeout=MONGO_TIMEOUT) as client:
                records = await mongo_select(client, table, conditions, fields, sort=sort, limit=limit)
        records = records if isinstance(records, list) else []

        if action == "list":
            # List always produces an xlsx download link
            download = self._force_export(records, plan, table)
            data = {"rows": records, "count": len(records), "collection": table}
            if download:
                data["downloads"] = [download]
                reply = self._download_link_reply(download, len(records), plan)
            else:
                reply = self._list_reply(records, plan)
        elif action == "trend":
            series = trend_series(records, date_field)
            reply = self._trend_reply(series, plan)
            data = {"series": series, "count": len(records), "collection": table}
        elif action == "compare":
            half = len(records) // 2
            comparison = compare_counts(half, len(records) - half)
            reply = self._comparison_reply(comparison, plan)
            data = {"comparison": comparison, "count": len(records), "collection": table}
        elif action == "top_n":
            group_field = plan.filters.get("group_by") or "vehicle_no"
            rows = top_n(records, group_field, plan.filters.get("top_n") or 5)
            reply = self._group_reply(rows, group_field, plan)
            data = {"rows": rows, "count": len(records), "collection": table}
        elif action == "group_by":
            group_field = plan.filters.get("group_by") or "route_name"
            rows = group_by(records, group_field)
            reply = self._group_reply(rows, group_field, plan)
            data = {"rows": rows, "count": len(records), "collection": table}
        else:
            # Default: count
            reply = self._count_reply(len(records), plan)
            data = {"count": len(records), "collection": table}

        return ExecutionResult(
            data=data,
            reply=reply,
            query_type="ANALYTICS",
            mongo_query=self._mongo_query(table, conditions, fields, sort, limit),
            mongo_collection=table,
            services_called=[table],
            metadata={"domain": plan.domain, "action": plan.action},
            api_request=api_req_metadata,
        )

    def _resolve_table(self, plan: QueryPlan) -> str:
        metric = plan.filters.get("metric", "trip")
        if metric in {"delay"}:
            return "courier_route_delay"
        return self.default_collection

    def _projection_for(self, plan: QueryPlan) -> Dict[str, int]:
        return {
            "shipment_no": 1,
            "trip_id": 1,
            "vehicle_no": 1,
            "trip_type": 1,
            "shipment_method": 1,
            "route_name": 1,
            "route_code": 1,
            "source_code": 1,
            "destination_code": 1,
            "source_name": 1,
            "destination_name": 1,
            "fleet_no": 1,
            "transporter_name": 1,
            "region_code": 1,
            "trip_status": 1,
            "gps_vendor_name": 1,
            "ata_source": 1,
            "actual_source_departure_time": 1,
            "actual_destination_arrival_time": 1,
            "exception_common_backend": 1,
            "exception_common_backend_2": 1,
            "exception_common_backend_3": 1,
            "imei_no_type": 1,
            "imei_no_type2": 1,
            "imei_no_type3": 1,
            "close_remarks": 1,
            "distance_km": 1,
            "halt_duration": 1,
            "run_date": 1,
            "delay_reason": 1,
            "total_delay_in_min": 1,
            "driver_name": 1,
            "driver_mobile": 1,
            "schedule_departure": 1,
            "schedule_arrival": 1,
            "close_by": 1,
            "close_date": 1,
            "gps_vendor2": 1,
            "gps_vendor3": 1,
            "imei_no": 1,
            "imei_no2": 1,
            "imei_no3": 1,
            "route_id": 1,
            "run_code": 1,
        }

    def _export_if_requested(self, records: List[Dict[str, Any]], plan: QueryPlan, table: str) -> Dict[str, Any] | None:
        """Legacy — kept for backward compat. Use _force_export for list action."""
        if table != self.default_collection:
            return None
        if plan.filters.get("export") != "excel":
            return None
        return create_trip_report_excel(records, stem="trip_report")

    def _force_export(self, records: List[Dict[str, Any]], plan: QueryPlan, table: str) -> Dict[str, Any] | None:
        """Always generate xlsx for list action regardless of export flag."""
        if table != self.default_collection:
            return None
        return create_trip_report_excel(records, stem="trip_report")

    def _apply_filters(self, conditions: Dict[str, Any], plan: QueryPlan, entities: EntityBundle, date_field: str) -> None:
        trip_id = plan.filters.get("trip_id") or entities.trip_id or entities.shipment_no
        if trip_id:
            conditions["shipment_no"] = trip_id

        date_from = plan.filters.get("date_from") or entities.date_from
        date_to = plan.filters.get("date_to") or entities.date_to or date_from
        if date_from:
            conditions[date_field] = {"$gte": date_from, "$lte": date_to}

        # ────────────────────────────────────────────────────────────────
        # GPS VENDOR FILTERS
        # ────────────────────────────────────────────────────────────────
        if plan.filters.get("gps_vendor_wheelseye"):
            conditions["$or"] = [
                {"gps_vendor2": "wheelseye"},
                {"gps_vendor3": "wheelseye"},
            ]
        elif plan.filters.get("gps_vendor_3rdparty"):
            conditions["gps_vendor_name"] = {"$nin": ["Secutrak", "Secutrak_TP"]}
            conditions["gps_vendor2"] = {"$nin": ["Secutrak", "Secutrak_TP"]}
            conditions["gps_vendor3"] = {"$nin": ["Secutrak", "Secutrak_TP"]}
        elif plan.filters.get("gps_vendor_any"):
            conditions["$or"] = [
                {"gps_vendor_name": {"$ne": ""}},
                {"gps_vendor2": {"$ne": ""}},
                {"gps_vendor3": {"$ne": ""}},
            ]

        # ────────────────────────────────────────────────────────────────
        # DEVICE / IMEI FILTERS (NA = device not present)
        # ────────────────────────────────────────────────────────────────
        if plan.filters.get("imei_no") == "":
            conditions["imei_no"] = ""
        if plan.filters.get("imei_no2") == "":
            conditions["imei_no2"] = ""
        if plan.filters.get("imei_no3") == "":
            conditions["imei_no3"] = ""

        for field in ("imei_no_type", "imei_no_type2", "imei_no_type3"):
            if field in plan.filters:
                conditions[field] = self._normalize_filter_value(field, plan.filters[field])

        for field in ("actual_source_departure_time", "actual_destination_arrival_time"):
            value = plan.filters.get(field)
            if value == "__empty__":
                conditions[field] = {"$in": ["", None]}
            elif value is not None:
                conditions[field] = self._normalize_filter_value(field, value)

        if plan.filters.get("close_remarks") == "__regex_supervisor__":
            conditions["close_remarks"] = {"$regex": "supervisor", "$options": "i"}

        if plan.filters.get("ata_source") in {"GPS", "MANUAL", "API"}:
            conditions["ata_source"] = plan.filters["ata_source"]

        for field in ("fleet_no", "transporter_name", "route_name", "route_code", "source_code", "destination_code", "source_name", "destination_name", "distance_km", "halt_duration"):
            value = plan.filters.get(field)
            if value is not None:
                conditions[field] = self._normalize_filter_value(field, value)

        # ────────────────────────────────────────────────────────────────
        # ROUTE CODE SEARCH  ("HKI route" → source_code OR destination_code OR route_name)
        # ────────────────────────────────────────────────────────────────
        route_code_search = plan.filters.get("route_code_search")
        if route_code_search:
            code = str(route_code_search).upper()
            or_clause = [
                {"source_code": code},
                {"destination_code": code},
                {"route_name": {"$regex": code, "$options": "i"}},
            ]
            if "$or" in conditions:
                # Merge into existing $and to avoid overwriting other $or clauses
                conditions["$and"] = conditions.get("$and", []) + [{"$or": or_clause}]
            else:
                conditions["$or"] = or_clause

        # ────────────────────────────────────────────────────────────────
        # ALL DEVICES INACTIVE / NA (complex $or logic)
        # ────────────────────────────────────────────────────────────────
        if plan.filters.get("all_devices_no_connectivity"):
            if "$or" not in conditions:
                conditions["$or"] = []
            conditions["$or"].extend([
                {"exception_common_backend": "No Connectivity"},
                {"exception_common_backend_2": "No Connectivity"},
                {"exception_common_backend_3": "No Connectivity"},
            ])
        elif plan.filters.get("all_devices_na"):
            if "$or" not in conditions:
                conditions["$or"] = []
            conditions["$or"].extend([
                {"exception_common_backend": "NA"},
                {"exception_common_backend_2": "NA"},
                {"exception_common_backend_3": "NA"},
            ])

        # ────────────────────────────────────────────────────────────────
        # STANDARD FIELD FILTERS
        # ────────────────────────────────────────────────────────────────
        for field in (
            "vehicle_no",
            "source_code",
            "destination_code",
            "region_code",
            "shipment_method",
            "gps_vendor_name",
            "route_name",
            "route_code",
            "source_name",
            "destination_name",
            "trip_type",
            "trip_status",
            "exception_common_backend",
            "exception_common_backend_2",
            "exception_common_backend_3",
            "actual_source_departure_time",
            "actual_destination_arrival_time",
            "ata_source",
            "close_remarks",
            "fleet_no",
            "transporter_name",
            "distance_km",
            "halt_duration",
        ):
            value = plan.filters.get(field)
            if value is None:
                continue
            # route_name is already covered by the $or in route_code_search
            if field == "route_name" and plan.filters.get("route_code_search"):
                continue
            conditions[field] = self._normalize_filter_value(field, value)

    def _normalize_filter_value(self, field: str, value: Any) -> Any:
        if field == "region_code" and isinstance(value, str):
            return value.upper()
        if field == "trip_status":
            return int(value)
        if field == "trip_type":
            return int(value) if isinstance(value, str) else value
        if field in {"exception_common_backend", "exception_common_backend_2", "exception_common_backend_3"}:
            if isinstance(value, str):
                if value == "__gps_active__":
                    return {"$nin": ["GPS NA", "No Connectivity", "NA"]}
                if value == "__regex_supervisor__":
                    return {"$regex": "supervisor", "$options": "i"}
                return value
            if value == "__gps_active__":
                return {"$nin": ["GPS NA", "No Connectivity", "NA"]}
            if value == "__3rdparty__":
                return {"$nin": ["", "Axestrack_bluedart", "Kiasaint_bluedart", "Lynkit_Bluedart"]}
            return value
        if field in {"actual_source_departure_time", "actual_destination_arrival_time"}:
            if value == "__empty__":
                return {"$in": ["", None]}
            return value
        if field in {"imei_no_type", "imei_no_type2", "imei_no_type3"}:
            return int(value) if isinstance(value, str) and value.isdigit() else value
        if field == "close_remarks" and value == "__regex_supervisor__":
            return {"$regex": "supervisor", "$options": "i"}
        if field == "shipment_method":
            return {"$regex": str(value), "$options": "i"}
        if field in {"distance_km", "halt_duration"} and isinstance(value, (int, float)):
            return value
        if field in {"source_code", "destination_code"}:
            return str(value).upper()
        if field in {"gps_vendor_name", "route_name", "route_code", "source_name", "destination_name"}:
            return {"$regex": str(value), "$options": "i"}
        if field in {"fleet_no", "transporter_name", "ata_source"}:
            return str(value)
        if field == "vehicle_no":
            return str(value).upper()
        return value

    def _mongo_query(
        self,
        table: str,
        conditions: Dict[str, Any],
        fields: Dict[str, int],
        sort: Dict[str, int] | None,
        limit: int,
    ) -> str:
        query = f"db.{table}.find({json.dumps(conditions)}, {json.dumps(fields)})"
        if sort:
            query += f".sort({json.dumps(sort)})"
        return f"{query}.limit({limit})"

    def _sort_for_plan(self, plan: QueryPlan) -> Dict[str, int] | None:
        sort_by = plan.filters.get("sort_by")
        if not sort_by:
            return {"run_date": -1}
        order = -1 if str(plan.filters.get("sort_order", "desc")).lower() == "desc" else 1
        return {str(sort_by): order}

    def _count_reply(self, count: int, plan: QueryPlan) -> str:
        metric = plan.filters.get("metric", "trip")
        date_from = plan.filters.get("date_from", "")
        date_to = plan.filters.get("date_to", "")
        if date_from and date_to:
            label = f"{date_from[:10]} to {date_to[:10]}"
        elif date_from:
            label = f"from {date_from[:10]}"
        else:
            label = f"total {metric}s"
        return metric_box("Total Trips", str(count), "#111827", label)

    def _download_link_reply(self, download: Dict[str, Any], count: int, plan: QueryPlan) -> str:
        url = download.get("url", "")
        filename = download.get("filename", "trip_report.xlsx")
        row_count = download.get("row_count", count)
        return (
            f'<div style="padding:12px;background:#f0f9ff;border:1px solid #0ea5e9;border-radius:8px;">'
            f'<p style="margin:0 0 8px 0;font-weight:600;color:#0369a1;">'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" '
            f'style="vertical-align:-3px;margin-right:6px" viewBox="0 0 16 16">'
            f'<path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/>'
            f'<path d="M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z"/>'
            f'</svg>Trip Report Ready</p>'
            f'<p style="margin:0 0 10px 0;color:#555;font-size:13px;">{row_count} records found</p>'
            f'<a href="{url}" download="{filename}" '
            f'style="display:inline-block;padding:8px 18px;background:#0ea5e9;color:#fff;'
            f'border-radius:6px;text-decoration:none;font-weight:600;font-size:13px;">'
            f'Download {filename}</a></div>'
        )

    def _list_reply(self, rows: List[Dict[str, Any]], plan: QueryPlan) -> str:
        """Fallback when xlsx cannot be generated (e.g. non-trip table)."""
        headers = ["#", "Trip ID", "Vehicle", "Route", "Source", "Destination", "Run Date"]
        body = "".join(
            "<tr>"
            + td(str(index + 1))
            + td(str(row.get("shipment_no", "-")), bold=True)
            + td(str(row.get("vehicle_no", "-")))
            + td(str(row.get("route_name", "-")))
            + td(str(row.get("source_name", "-")))
            + td(str(row.get("destination_name", "-")))
            + td(str(row.get("run_date", "-"))[:16])
            + "</tr>"
            for index, row in enumerate(rows[:50])
        )
        if not body:
            return metric_box("Trips", "0", "#111827", "No records found")
        return html_table(headers, body)

    def _group_reply(self, rows: List[Dict[str, Any]], field: str, plan: QueryPlan) -> str:
        header = ["#", field, "count"]
        rows_html = "".join(
            "<tr>"
            + td(str(index + 1))
            + td(str(row.get(field, row.get("vehicle_no", "-"))), bold=True)
            + td(str(row.get("count", 0)))
            + "</tr>"
            for index, row in enumerate(rows[:10])
        )
        return html_table(header, rows_html)

    def _trend_reply(self, series: List[Dict[str, Any]], plan: QueryPlan) -> str:
        rows_html = "".join("<tr>" + td(item["period"]) + td(str(item["count"]), bold=True) + "</tr>" for item in series)
        return html_table(["period", "count"], rows_html)

    def _comparison_reply(self, comparison: Dict[str, Any], plan: QueryPlan) -> str:
        rows_html = "<tr>" + td("left") + td(str(comparison["left"]), bold=True) + "</tr>"
        rows_html += "<tr>" + td("right") + td(str(comparison["right"]), bold=True) + "</tr>"
        rows_html += "<tr>" + td("delta") + td(str(comparison["delta"]), bold=True) + "</tr>"
        return html_table(["metric", "value"], rows_html)
