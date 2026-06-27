from __future__ import annotations

import math
from typing import Any, Dict


class TokenTracker:
    def estimate(self, text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def estimate_payload(self, payload: Dict[str, Any]) -> int:
        return self.estimate(str(payload))