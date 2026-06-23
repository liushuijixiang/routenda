from __future__ import annotations

from collections.abc import Sequence

from visit_agent.core.agent import Agent
from visit_agent.core.message import AgentResponse, Message


class SimpleAgent(Agent):
    async def run(self, messages: Sequence[Message] | str) -> AgentResponse:
        history = self._messages(messages)
        content = await self.llm.generate(history, config=self.config)
        return AgentResponse(
            content=content,
            messages=[*history, Message("assistant", content)],
        )
