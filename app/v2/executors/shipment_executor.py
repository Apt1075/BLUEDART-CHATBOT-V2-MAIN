from __future__ import annotations

import asyncio
from typing import Any, Dict

from app.core.data_aggregator import BluedartAggregator
from app.core.response_formatter import format_alerts, format_delays, format_trip_status
from app.v2.models import EntityBundle, ExecutionResult, QueryPlan


class ShipmentExecutor:
    def __init__(self, aggregator: BluedartAggregator | None = None) -> None:
        self.aggregator = aggregator or BluedartAggregator()

    async def execute(self, plan: QueryPlan, entities: EntityBundle, access_token: str | None = None) -> ExecutionResult:
        if not entities.shipment_no:
            return ExecutionResult(status="clarification", reply="Please provide a shipment number or vehicle number.", query_type="CLARIFY")

        from app.core.external_api_client import fetch_trip_report_api
        from datetime import datetime, timedelta
        date_from = entities.date_from[:10] if entities.date_from else (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        date_to = entities.date_to[:10] if entities.date_to else datetime.now().strftime("%Y-%m-%d")
        
        api_res = await fetch_trip_report_api(date_from, date_to, access_token, plan.filters)
        records = api_res.get("records", [])
        api_req_metadata = api_res.get("api_request")
        trip = {}
        if isinstance(records, list):
            for r in records:
                if str(r.get("shipment_no")) == str(entities.shipment_no) or str(r.get("trip_id")) == str(entities.shipment_no):
                    trip = r
                    break
        
        core = {"trip_detail": trip, "live_status": {}}
        tasks = {}
        if "delay" in plan.secondary_intents or plan.action == "delay":
            tasks["delays"] = self.aggregator.get_trip_delays(shipment_no=entities.shipment_no)
        if "alert" in plan.secondary_intents or plan.action == "alert":
            tasks["alerts"] = self.aggregator.get_trip_alerts(shipment_no=entities.shipment_no)
        if "stops" in plan.secondary_intents or plan.action == "stops":
            trip = core.get("trip_detail", {}) if isinstance(core, dict) else {}
            if isinstance(trip, list):
                trip = trip[0] if trip else {}
            m_trip_id = str(trip.get("m_trip_id") or "")
            if m_trip_id:
                tasks["stops"] = self.aggregator.get_trip_stops(m_trip_id=m_trip_id, vehicle_no=trip.get("vehicle_no"))

        extra: Dict[str, Any] = {}
        if tasks:
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for key, value in zip(tasks.keys(), results):
                extra[key] = value if not isinstance(value, Exception) else {"error": str(value)}

        reply_sections = [format_trip_status({"trip_core": core})]
        if extra.get("delays"):
            reply_sections.append(format_delays(extra["delays"]))
        if extra.get("alerts"):
            reply_sections.append(format_alerts(extra["alerts"]))

        return ExecutionResult(
            data={"trip_core": core, **extra},
            reply="".join(reply_sections),
            query_type="STATUS",
            mongo_collection="courier_trip_detail",
            services_called=["courier_trip_detail", "trip_dashboard_live_status"],
            metadata={"domain": plan.domain, "action": plan.action},
            api_request=api_req_metadata,
        )