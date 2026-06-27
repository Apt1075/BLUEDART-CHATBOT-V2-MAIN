from __future__ import annotations

import asyncio
from typing import Dict

from app.core.config import settings
from app.v2.session_context import SessionContext


class ConversationManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, SessionContext] = {}
        self._lock = asyncio.Lock()

    async def get_context(self, session_id: str | None) -> SessionContext:
        key = session_id or "anonymous"
        async with self._lock:
            context = self._sessions.get(key)
            if context is None:
                context = SessionContext(session_id=key)
                self._sessions[key] = context
            return context

    async def record_user_message(self, session_id: str | None, message: str) -> SessionContext:
        context = await self.get_context(session_id)
        context.remember_turn("user", message)
        return context

    async def record_assistant_message(self, session_id: str | None, message: str) -> SessionContext:
        context = await self.get_context(session_id)
        context.remember_turn("assistant", message)
        return context

    async def update_context(self, session_id: str | None, intent: str, entities: Dict[str, str], plan: Dict[str, str]) -> SessionContext:
        context = await self.get_context(session_id)
        context.last_intent = intent
        context.merge_entities(entities)
        context.last_plan = plan
        if len(context.history) > settings.SESSION_HISTORY_TURNS * 2:
            context.history = context.history[-settings.SESSION_HISTORY_TURNS * 2 :]
        return context


_conversation_manager = ConversationManager()


def get_conversation_manager() -> ConversationManager:
    return _conversation_manager