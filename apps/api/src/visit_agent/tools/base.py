from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    user_id: str = ""
    conversation_id: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    data: Any = None
    error: str | None = None

    @classmethod
    def success(cls, output: str, data: Any = None) -> ToolResult:
        return cls(True, output, data)

    @classmethod
    def failure(cls, error: str, output: str) -> ToolResult:
        return cls(False, output, None, error)


class BaseTool:
    name: str = ""
    description: str = ""

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
        }

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        raise NotImplementedError

    async def arun(
        self,
        args: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        return await asyncio.to_thread(self.run, args, context)
