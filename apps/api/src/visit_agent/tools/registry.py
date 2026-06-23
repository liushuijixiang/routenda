from __future__ import annotations

from typing import Any, List

from visit_agent.core.exceptions import ToolNotFoundError
from visit_agent.tools.base import BaseTool, ToolContext, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if not tool.name:
            raise ValueError("tool.name is required")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def list(self) -> list[BaseTool]:
        return list(self._tools.values())

    def schemas(self) -> List[dict[str, Any]]:
        return [tool.schema() for tool in self.list()]

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        return await self.get(name).arun(args, context)
