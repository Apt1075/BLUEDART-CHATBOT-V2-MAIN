from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Tuple


class SemanticMatcher:
    INTENT_PHRASES: Dict[str, List[str]] = {
        "shipment": ["shipment", "consignment", "parcel", "trip status", "where is", "status"],
        "gps": ["location", "gps", "live position", "current location", "vehicle location"],
        "delay": ["delay", "late", "stuck", "halt", "why stopped"],
        "alert": ["alert", "violation", "alarm", "trigger", "warning"],
        "analytics": ["count", "trend", "compare", "top", "summary", "kpi", "group by"],
        "customer": ["customer", "route mapping", "assigned route"],
        "stops": ["stop", "waypoint", "delivery point", "pod"],
        "imei": ["imei", "device", "tracker"],
    }

    def score(self, text: str, intent: str) -> float:
        phrases = self.INTENT_PHRASES.get(intent, [])
        if not phrases:
            return 0.0
        text_l = text.lower()
        tokens = set(re.findall(r"[a-z0-9_]+", text_l))
        phrase_hits = sum(1 for phrase in phrases if phrase in text_l)
        token_hits = sum(1 for phrase in phrases if any(part in tokens for part in phrase.split()))
        similarity = max(SequenceMatcher(None, text_l, phrase).ratio() for phrase in phrases)
        return min(1.0, (phrase_hits * 0.35) + (token_hits * 0.15) + (similarity * 0.5))

    def rank(self, text: str, intents: Iterable[str]) -> List[Tuple[str, float]]:
        scored = [(intent, self.score(text, intent)) for intent in intents]
        return sorted(scored, key=lambda item: item[1], reverse=True)