from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any, Dict

from app.core.config import settings
from app.core.openai_client import OpenAIClient
from app.schemas.chat import ChatRequest, ChatResponse, ServiceCallInfo
from app.v2.confidence import ConfidenceResult, ConfidenceScorer
from app.v2.entity_extraction.imei_extractor import ImeiExtractor
from app.v2.entity_extraction.date_extractor import DateExtractor
from app.v2.entity_extraction.shipment_extractor import ShipmentExtractor
from app.v2.entity_extraction.trip_id_extractor import TripIdExtractor
from app.v2.entity_extraction.vehicle_extractor import VehicleExtractor
from app.v2.executors.analytics_executor import AnalyticsExecutor
from app.v2.executors.customer_executor import CustomerExecutor
from app.v2.executors.gps_executor import GpsExecutor
from app.v2.executors.shipment_executor import ShipmentExecutor
from app.v2.memory.conversation_manager import get_conversation_manager
from app.v2.models import EntityBundle
from app.v2.normalizer import QueryNormalizer
from app.v2.observability.latency_tracker import LatencyTracker
from app.v2.observability.query_logger import QueryLogger
from app.v2.planners.query_planner import QueryPlanner
from app.v2.query_understanding.classifier import QueryClassifier
from app.v2.response_builder import ResponseBuilder
from app.v2.resolver import EntityResolver


class V2ChatService:
    def __init__(self) -> None:
        self.normalizer = QueryNormalizer()
        self.classifier = QueryClassifier()
        self.shipment_extractor = ShipmentExtractor()
        self.vehicle_extractor = VehicleExtractor()
        self.imei_extractor = ImeiExtractor()
        self.date_extractor = DateExtractor()
        self.trip_id_extractor = TripIdExtractor()
        self.resolver = EntityResolver()
        self.scorer = ConfidenceScorer()
        self.planner = QueryPlanner()
        self.shipment_executor = ShipmentExecutor()
        self.gps_executor = GpsExecutor()
        self.analytics_executor = AnalyticsExecutor()
        self.customer_executor = CustomerExecutor()
        self.response_builder = ResponseBuilder()
        self.conversation_manager = get_conversation_manager()
        self.query_logger = QueryLogger()
        self.llm_client = OpenAIClient()

    async def process(self, request: ChatRequest) -> ChatResponse:
        tracker = LatencyTracker()
        tracker.start()
        log_entry = self.query_logger.start(request.session_id, request.message, "pending")

        session = await self.conversation_manager.get_context(request.session_id)
        await self.conversation_manager.record_user_message(request.session_id, request.message)

        normalized_text, normalization_notes = self.normalizer.normalize(request.message)

        # Check for greeting message
        clean_msg = normalized_text.strip().lower().rstrip("?.,!")
        greetings = {"hi", "hello", "hey", "hola", "greetings", "good morning", "good afternoon", "good evening"}
        if clean_msg in greetings:
            reply = "Welcome to SecureTrack AI assistance! How can I help you today? You can ask me about shipment/trip statuses, vehicle tracking, halts, and delays."
            await self.conversation_manager.record_assistant_message(request.session_id, reply)
            self.query_logger.finish(log_entry, reply, {"greeting": True})
            return ChatResponse(
                reply=reply,
                intent="general",
                query_type="GENERAL",
                extracted_ids={},
                total_time_seconds=tracker.stop() / 1000,
                session_id=request.session_id,
                downloads=[],
            )

        detected_intent = self.classifier.classify(normalized_text)
        entities = self._extract_entities(normalized_text)
        resolved_entities = self.resolver.resolve(entities, session, normalized_text)

        provisional_plan = self.planner.build_plan(request.message, detected_intent, resolved_entities, self._neutral_confidence(), session.to_dict())
        confidence_result = self.scorer.score(detected_intent, resolved_entities, provisional_plan)
        plan = self.planner.build_plan(request.message, detected_intent, resolved_entities, confidence_result, session.to_dict())

        if plan.requires_clarification and plan.clarification_question:
            reply = plan.clarification_question
            await self.conversation_manager.update_context(request.session_id, detected_intent.primary_intent, self._entity_dict(resolved_entities), plan.to_dict())
            await self.conversation_manager.record_assistant_message(request.session_id, reply)
            self.query_logger.finish(log_entry, reply, {"clarification": True})
            return ChatResponse(
                reply=reply,
                intent=detected_intent.primary_intent,
                query_type="CLARIFY",
                extracted_ids=self._entity_dict(resolved_entities),
                services_called=[],
                total_time_seconds=tracker.stop() / 1000,
                context_used=bool(session.history),
                session_id=request.session_id,
                confidence=confidence_result.score,
                clarification_required=True,
                clarification_question=plan.clarification_question,
                understanding={"normalized": normalized_text, "notes": normalization_notes, "classification": asdict(detected_intent)},
                query_plan=plan.to_dict(),
                trace_id=log_entry.trace_id,
                downloads=[],
            )

        execution_result = await self._execute_plan(plan, resolved_entities, request.get_access_token())
        reply = self.response_builder.build(execution_result, plan)

        is_external = plan.domain in ("analytics", "shipment")
        if (settings.ENABLE_LLM_FORMATTER or is_external) and execution_result.data:
            reply = await self._format_with_llm(request.message, plan, resolved_entities, execution_result.data, reply)

        elapsed = tracker.stop() / 1000
        await self.conversation_manager.update_context(request.session_id, detected_intent.primary_intent, self._entity_dict(resolved_entities), plan.to_dict())
        await self.conversation_manager.record_assistant_message(request.session_id, reply)
        self.query_logger.finish(log_entry, reply, {"domain": plan.domain, "action": plan.action})

        return ChatResponse(
            reply=reply,
            intent=detected_intent.primary_intent,
            query_type=execution_result.query_type or plan.action.upper(),
            extracted_ids=self._entity_dict(resolved_entities),
            total_time_seconds=elapsed,
            session_id=request.session_id,
            downloads=execution_result.data.get("downloads", []),
            api_request=execution_result.api_request,
        )

    async def _execute_plan(self, plan, entities, access_token: str | None = None):
        if plan.domain == "analytics":
            return await self.analytics_executor.execute(plan, entities, access_token)
        if plan.domain == "gps":
            return await self.gps_executor.execute(plan, entities)
        if plan.domain == "customer":
            return await self.customer_executor.execute(plan, entities)
        return await self.shipment_executor.execute(plan, entities, access_token)

    async def _format_with_llm(self, message: str, plan, entities, data: Dict[str, Any], current_reply: str) -> str:
        try:
            summary, _, _ = await self.llm_client.analyze(
                user_query=message,
                context_data=data,
                history=[],
                intent_instruction=f"Summarize domain={plan.domain} action={plan.action} in one concise reply.",
                intent=plan.domain.upper(),
                extracted_ids=self._entity_dict(entities),
            )
            return summary or current_reply
        except Exception:
            return current_reply

    def _extract_entities(self, message: str) -> EntityBundle:
        date_range = self.date_extractor.extract(message)
        return EntityBundle(
            shipment_no=self.shipment_extractor.extract(message),
            vehicle_no=self.vehicle_extractor.extract(message),
            imei=self.imei_extractor.extract(message),
            trip_id=self.trip_id_extractor.extract(message),
            date_from=date_range.date_from,
            date_to=date_range.date_to,
        )

    def _entity_dict(self, entities: EntityBundle) -> Dict[str, Any]:
        return {k: v for k, v in entities.to_dict().items() if v and k != "source"}

    def _neutral_confidence(self) -> ConfidenceResult:
        return ConfidenceResult(score=0.0, needs_clarification=False, rationale=[])


@lru_cache(maxsize=1)
def get_v2_chat_service() -> V2ChatService:
    return V2ChatService()
