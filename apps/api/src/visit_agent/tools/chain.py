from __future__ import annotations

from collections.abc import Iterable

from visit_agent.core.message import ToolCallRecord
from visit_agent.tools.async_executor import AsyncToolExecutor, ToolRequest
from visit_agent.tools.base import ToolContext


class ToolChain:
    def __init__(self, executor: AsyncToolExecutor, steps: Iterable[ToolRequest]) -> None:
        self.executor = executor
        self.steps = list(steps)

    async def run(self, context: ToolContext | None = None) -> list[ToolCallRecord]:
        records: list[ToolCallRecord] = []
        for step in self.steps:
            records.append(await self.executor.execute_one(step, context))
        return records
