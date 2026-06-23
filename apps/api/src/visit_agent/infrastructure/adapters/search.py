from __future__ import annotations

from typing import Any, Protocol

import httpx

from visit_agent.agent.tools.result import ToolResult
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker, resilient_tool_call


class SearchPort(Protocol):
    async def search(self, query: str) -> ToolResult: ...


class DisabledSearchAdapter:
    async def search(self, query: str) -> ToolResult:
        return ToolResult.failure("search_disabled", "Search provider is not configured")


class SerperSearchAdapter:
    def __init__(
        self,
        api_key: str,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.url = url
        self.client = client or httpx.AsyncClient(timeout=8.0, trust_env=False)
        self._owns_client = client is None
        self.breaker = CircuitBreaker("serper")

    async def search(self, query: str) -> ToolResult:
        async def call() -> ToolResult:
            if not self.api_key:
                return ToolResult.failure("missing_credentials", "Serper API key is not configured")
            response = await self.client.post(
                self.url,
                json={"q": query},
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            )
            if response.status_code == 429 or response.status_code >= 500:
                return ToolResult.failure(
                    "search_unavailable",
                    f"Serper returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                return ToolResult.failure(
                    "search_rejected", f"Serper returned HTTP {response.status_code}"
                )
            payload: dict[str, Any] = response.json()
            return ToolResult.success(
                {
                    "organic": payload.get("organic", []),
                    "knowledge_graph": payload.get("knowledgeGraph"),
                    "answer_box": payload.get("answerBox"),
                }
            )

        return await resilient_tool_call("serper.search", call, self.breaker, attempts=2)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
