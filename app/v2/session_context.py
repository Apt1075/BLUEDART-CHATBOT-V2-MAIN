from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class SessionContext:
    session_id: str
    history: List[Dict[str, str]] = field(default_factory=list)
    last_entities: Dict[str, Any] = field(default_factory=dict)
    last_intent: Optional[str] = None
    last_plan: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def remember_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        self.updated_at = datetime.utcnow()

    def merge_entities(self, entities: Dict[str, Any]) -> None:
        self.last_entities.update({k: v for k, v in entities.items() if v is not None})
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)