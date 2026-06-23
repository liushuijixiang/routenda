from __future__ import annotations

from collections.abc import Sequence

from visit_agent.agents.react_agent import ReactAgent
from visit_agent.core.message import AgentResponse, Message


class PlanSolveAgent(ReactAgent):
    async def run(self, messages: Sequence[Message] | str) -> AgentResponse:
        history = self._messages(messages)
        plan = await self.llm.generate(
            [
                *history,
                Message("user", "先给出内部执行计划，必要时选择工具；计划要短。"),
            ],
            tools=self.tools.schemas(),
            config=self.config,
        )
        return await super().run([*history, Message("assistant", plan)])
