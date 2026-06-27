from __future__ import annotations

from app.core.data_aggregator import BluedartAggregator
from app.v2.models import EntityBundle, ExecutionResult, QueryPlan


class CustomerExecutor:
    def __init__(self, aggregator: BluedartAggregator | None = None) -> None:
        self.aggregator = aggregator or BluedartAggregator()

    async def execute(self, plan: QueryPlan, entities: EntityBundle) -> ExecutionResult:
        if not entities.customer_id:
            return ExecutionResult(status="clarification", reply="Please provide a customer id.", query_type="CLARIFY")

        payload = await self.aggregator.get_customer_routes(entities.customer_id)
        return ExecutionResult(data=payload if isinstance(payload, dict) else {"routes": payload}, reply=str(payload), query_type="CUSTOMER", mongo_collection="courier_customer_route_bluedart", services_called=["courier_customer_route_bluedart"], metadata={"domain": plan.domain})