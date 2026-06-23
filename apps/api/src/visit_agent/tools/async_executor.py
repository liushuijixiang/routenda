from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from visit_agent.core.message import ToolCallRecord
from visit_agent.tools.base import ToolContext
from visit_agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolRequest:
    name: str
    args: dict[str, Any]


class AsyncToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute_one(
        self,
        request: ToolRequest,
        context: ToolContext | None = None,
    ) -> ToolCallRecord:
        result = await self.registry.execute(request.name, request.args, context)
        return ToolCallRecord(
            name=request.name,
            args=request.args,
            ok=result.ok,
            output=result.output,
            raw=result.data if result.ok else result.error,
        )

    async def execute_many(
        self,
        requests: Iterable[ToolRequest],
        context: ToolContext | None = None,
    ) -> list[ToolCallRecord]:
        return await asyncio.gather(
            *(self.execute_one(request, context) for request in requests)
        )
