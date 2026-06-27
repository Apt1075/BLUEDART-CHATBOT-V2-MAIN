from __future__ import annotations

from app.v2.models import EntityBundle
from app.v2.session_context import SessionContext


class EntityResolver:
    CONTEXT_WORDS = (
        "this trip",
        "this vehicle",
        "this shipment",
        "same vehicle",
        "same trip",
        "that vehicle",
        "that trip",
        "follow up",
        "same one",
        "previous one",
        "usi vehicle",
        "usi trip",
        "kab aayega",
        "kab pahunchega",
    )

    def resolve(self, entities: EntityBundle, session: SessionContext, normalized_message: str) -> EntityBundle:
        resolved = EntityBundle(**entities.to_dict())
        
        has_signal = self._has_context_signal(normalized_message)
        is_clarifying = False
        if session.last_plan and isinstance(session.last_plan, dict):
            if session.last_plan.get("requires_clarification"):
                is_clarifying = True

        if not has_signal and not is_clarifying:
            return resolved

        for key, value in session.last_entities.items():
            if key == "source":
                continue
            if getattr(resolved, key, None) is None and value:
                setattr(resolved, key, value)
                resolved.source[key] = "session"

        if resolved.context_reference is None:
            resolved.context_reference = session.last_intent or session.session_id
        return resolved

    def _has_context_signal(self, normalized_message: str) -> bool:
        message = normalized_message.lower()
        return any(token in message for token in self.CONTEXT_WORDS)