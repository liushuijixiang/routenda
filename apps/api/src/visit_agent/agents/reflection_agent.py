from __future__ import annotations

from collections.abc import Sequence

from visit_agent.agents.react_agent import ReactAgent
from visit_agent.core.message import AgentResponse, Message


class ReflectionAgent(ReactAgent):
    async def run(self, messages: Sequence[Message] | str) -> AgentResponse:
        draft = await super().run(messages)
        reflected = await self.llm.generate(
            [
                *draft.messages,
                Message(
                    "user",
                    "检查上一条回复是否遗漏用户目标、工具结果或下一步动作；只输出修订后的最终回复。",
                ),
            ],
            config=self.config,
        )
        return AgentResponse(reflected, [*draft.messages, Message("assistant", reflected)], draft.tool_calls)
