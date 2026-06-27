from __future__ import annotations

from app.core.data_aggregator import BluedartAggregator, fetch_vehicle_location
from app.core.response_formatter import format_trip_status, format_vehicle_snapshot
from app.v2.models import EntityBundle, ExecutionResult, QueryPlan


class GpsExecutor:
    def __init__(self, aggregator: BluedartAggregator | None = None) -> None:
        self.aggregator = aggregator or BluedartAggregator()

    async def execute(self, plan: QueryPlan, entities: EntityBundle) -> ExecutionResult:
        if entities.vehicle_no:
            payload = await fetch_vehicle_location(entities.vehicle_no)
            if payload.get("trip_core"):
                reply = format_trip_status(payload) + "<div style=\"height:10px\"></div>" + format_vehicle_snapshot(payload)
            else:
                reply = format_vehicle_snapshot(payload)
            return ExecutionResult(data=payload, reply=reply, query_type="LOCATION", mongo_collection="Vehicle_wise_lastdata", services_called=["Vehicle_wise_lastdata"], metadata={"domain": plan.domain})

        if entities.imei:
            payload = await self.aggregator.get_imei_lastdata(entities.imei)
            return ExecutionResult(data=payload, reply=f"IMEI data fetched for {entities.imei}.", query_type="IMEI", mongo_collection="bluedart_lastdata", services_called=["bluedart_lastdata"], metadata={"domain": plan.domain})

        return ExecutionResult(status="clarification", reply="Please provide a vehicle number or IMEI.", query_type="CLARIFY")