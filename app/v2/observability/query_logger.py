from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List
from uuid import uuid4

from app.v2.observability.failed_query_tracker import FailedQueryTracker
from app.v2.observability.token_tracker import TokenTracker


@dataclass(slots=True)
class QueryLogEntry:
    trace_id: str
    session_id: str | None
    message: str
    intent: str
    status: str
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class QueryLogger:
    def __init__(self) -> None:
        self.token_tracker = TokenTracker()
        self.failed_tracker = FailedQueryTracker()
        self.entries: List[QueryLogEntry] = []

    def trace_id(self) -> str:
        return uuid4().hex

    def start(self, session_id: str | None, message: str, intent: str) -> QueryLogEntry:
        return QueryLogEntry(
            trace_id=self.trace_id(),
            session_id=session_id,
            message=message,
            intent=intent,
            status="started",
            tokens_in=self.token_tracker.estimate(message),
        )

    def finish(self, entry: QueryLogEntry, reply: str, metadata: Dict[str, Any] | None = None) -> QueryLogEntry:
        entry.status = "ok"
        entry.tokens_out = self.token_tracker.estimate(reply)
        entry.metadata.update(metadata or {})
        self.entries.append(entry)
        return entry

    def fail(self, entry: QueryLogEntry, error: str) -> QueryLogEntry:
        entry.status = "failed"
        entry.metadata["error"] = error
        self.entries.append(entry)
        self.failed_tracker.record(entry.intent, entry.message)
        return entry