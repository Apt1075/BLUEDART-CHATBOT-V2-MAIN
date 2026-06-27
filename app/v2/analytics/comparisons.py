from __future__ import annotations

from typing import Any, Dict


def compare_counts(left: int, right: int) -> Dict[str, Any]:
    delta = left - right
    pct = round((delta / right) * 100, 2) if right else None
    return {"left": left, "right": right, "delta": delta, "pct_change": pct}