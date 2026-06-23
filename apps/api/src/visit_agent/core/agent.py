from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from visit_agent.core.config import AgentConfig
from visit_agent.core.llm import LLM
from visit_agent.core.message import AgentResponse, Message
from visit_agent.tools.registry import ToolRegistry


class Agent(ABC):
    def __init__(
        self,
        llm: LLM,
        tools: ToolRegistry | None = None,
        config: AgentConfig | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools or ToolRegistry()
        self.config = config or AgentConfig()

    @abstractmethod
    async def run(self, messages: Sequence[Message] | str) -> AgentResponse:
        pass

    def _messages(self, messages: Sequence[Message] | str) -> list[Message]:
        if isinstance(messages, str):
            return [
                Message("system", self.config.system_prompt),
                Message("user", messages),
            ]
        return list(messages)
