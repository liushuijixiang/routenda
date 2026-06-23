from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str
    name: str | None = None


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    ok: bool
    output: str
    raw: Any = None


@dataclass(frozen=True)
class AgentResponse:
    content: str
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
