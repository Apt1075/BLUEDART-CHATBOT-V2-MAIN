from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from app.v2.models import DetectedIntent
from app.v2.semantic_matcher import SemanticMatcher


class QueryClassifier:
    INTENT_TERMS: Dict[str, List[str]] = {
        "shipment": ["shipment", "consignment", "parcel", "trip", "status"],
        "gps": ["location", "gps", "current position", "live location", "vehicle"],
        "delay": ["delay", "late", "stuck", "halt", "why"],
        "alert": ["alert", "violation", "warning", "trigger", "s180"],
        "analytics": ["count", "summary", "report", "trend", "compare", "top", "kpi"],
        "customer": ["customer", "route mapping", "customer route"],
        "stops": ["stop", "waypoint", "delivery point", "pod"],
        "imei": ["imei", "device", "tracker"],
        "general": ["help", "what", "show", "tell"],
    }

    def __init__(self, matcher: SemanticMatcher | None = None) -> None:
        self.matcher = matcher or SemanticMatcher()

    def classify(self, message: str) -> DetectedIntent:
        text = message.lower()
        scores: Dict[str, float] = defaultdict(float)
        matched_terms: Dict[str, List[str]] = defaultdict(list)

        for intent, terms in self.INTENT_TERMS.items():
            for term in terms:
                if term in text:
                    scores[intent] += 0.28 if len(term) > 4 else 0.18
                    matched_terms[intent].append(term)
            semantic = self.matcher.score(text, intent)
            scores[intent] += semantic * 0.55

        if not scores:
            return DetectedIntent(primary_intent="general", confidence=0.0)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        primary_intent, raw_score = ranked[0]
        secondary = [intent for intent, score in ranked[1:] if score >= max(0.35, raw_score * 0.65)][:3]
        confidence = max(0.0, min(1.0, raw_score))
        return DetectedIntent(
            primary_intent=primary_intent,
            secondary_intents=secondary,
            confidence=round(confidence, 2),
            matched_terms=dict(matched_terms),
        )