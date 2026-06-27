from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List


def count_records(records: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for _ in records)


def top_n(records: List[Dict[str, Any]], field: str, n: int = 5) -> List[Dict[str, Any]]:
    counter = Counter(str(record.get(field, "")) for record in records if record.get(field))
    return [{field: key, "count": value} for key, value in counter.most_common(n)]


def group_by(records: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, int] = defaultdict(int)
    for record in records:
        grouped[str(record.get(field, "unknown"))] += 1
    return [{field: key, "count": count} for key, count in sorted(grouped.items(), key=lambda item: item[1], reverse=True)]


def kpi(records: List[Dict[str, Any]], numerator_field: str, denominator_field: str | None = None) -> Dict[str, Any]:
    numerator = sum(float(record.get(numerator_field, 0) or 0) for record in records)
    denominator = sum(float(record.get(denominator_field, 0) or 0) for record in records) if denominator_field else None
    ratio = round(numerator / denominator, 4) if denominator else None
    return {"numerator": numerator, "denominator": denominator, "ratio": ratio}