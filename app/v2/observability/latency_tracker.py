from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class LatencyTracker:
    started_at: float = 0.0

    def start(self) -> None:
        self.started_at = time.perf_counter()

    def stop(self) -> float:
        if not self.started_at:
            return 0.0
        return round((time.perf_counter() - self.started_at) * 1000, 2)