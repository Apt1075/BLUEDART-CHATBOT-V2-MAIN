from __future__ import annotations

from collections import Counter
from typing import Dict, List


class FailedQueryTracker:
    def __init__(self) -> None:
        self._intents = Counter()
        self._queries = Counter()

    def record(self, intent: str, message: str) -> None:
        self._intents[intent] += 1
        self._queries[message[:120].lower()] += 1

    def top_intents(self, limit: int = 10) -> List[Dict[str, int]]:
        return [{"intent": intent, "count": count} for intent, count in self._intents.most_common(limit)]