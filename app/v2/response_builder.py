from __future__ import annotations

from app.v2.models import ExecutionResult, QueryPlan


class ResponseBuilder:
    def build(self, result: ExecutionResult, plan: QueryPlan, llm_text: str | None = None) -> str:
        if llm_text:
            return llm_text
        if result.reply:
            return result.reply
        return str(result.data or {})