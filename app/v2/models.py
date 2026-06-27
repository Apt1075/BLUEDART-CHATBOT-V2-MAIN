from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class DetectedIntent:
    primary_intent: str
    secondary_intents: List[str] = field(default_factory=list)
    confidence: float = 0.0
    matched_terms: Dict[str, List[str]] = field(default_factory=dict)


@dataclass(slots=True)
class EntityBundle:
    shipment_no: Optional[str] = None
    vehicle_no: Optional[str] = None
    imei: Optional[str] = None
    customer_id: Optional[str] = None
    route_id: Optional[str] = None
    m_trip_id: Optional[str] = None
    trip_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    context_reference: Optional[str] = None
    source: Dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            getattr(self, field_name)
            for field_name in ("shipment_no", "vehicle_no", "imei", "customer_id", "route_id", "m_trip_id", "trip_id")
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QueryPlan:
    domain: str
    action: str
    filters: Dict[str, Any] = field(default_factory=dict)
    entities: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    secondary_intents: List[str] = field(default_factory=list)
    requires_clarification: bool = False
    clarification_question: Optional[str] = None
    execution_mode: str = "deterministic"
    preferred_executor: Optional[str] = None
    trace: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutionResult:
    data: Dict[str, Any] = field(default_factory=dict)
    reply: str = ""
    query_type: str = ""
    mongo_query: Optional[str] = None
    mongo_collection: Optional[str] = None
    services_called: List[str] = field(default_factory=list)
    context_used: bool = False
    status: str = "ok"
    metadata: Dict[str, Any] = field(default_factory=dict)
    api_request: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)