from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.v2.models import DetectedIntent, EntityBundle, QueryPlan


@dataclass(slots=True)
class ConfidenceResult:
    score: float
    needs_clarification: bool
    rationale: List[str]
    clarification_question: str | None = None


class ConfidenceScorer:
    def score(self, intent: DetectedIntent, entities: EntityBundle, plan: QueryPlan) -> ConfidenceResult:
        score = intent.confidence
        rationale: List[str] = [f"intent={intent.primary_intent}:{intent.confidence:.2f}"]

        if not entities.is_empty():
            score += 0.15
            rationale.append("entities:present")
        else:
            score -= 0.10
            rationale.append("entities:missing")

        # Bulk/analytics queries with a date range are self-sufficient — no ID needed
        if entities.date_from:
            score += 0.25
            rationale.append("date_range:present")

        if intent.secondary_intents:
            score += min(0.10, 0.03 * len(intent.secondary_intents))
            rationale.append(f"secondary={len(intent.secondary_intents)}")

        if plan.action in {"count", "trend", "compare", "top_n", "list"}:
            score += 0.05
            rationale.append("analytics:structured")

        score = max(0.0, min(1.0, score))
        needs_clarification = score < 0.45 or plan.requires_clarification
        question = plan.clarification_question if needs_clarification else None
        return ConfidenceResult(score=round(score, 2), needs_clarification=needs_clarification, rationale=rationale, clarification_question=question)