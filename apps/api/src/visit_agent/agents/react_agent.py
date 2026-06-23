from __future__ import annotations

import json
import re
from collections.abc import Sequence

from visit_agent.core.agent import Agent
from visit_agent.core.message import AgentResponse, Message, ToolCallRecord
from visit_agent.tools.async_executor import AsyncToolExecutor, ToolRequest
from visit_agent.tools.base import ToolContext


class ReactAgent(Agent):
    async def run(self, messages: Sequence[Message] | str) -> AgentResponse:
        history = self._messages(messages)
        first = await self.llm.generate(history, tools=self.tools.schemas(), config=self.config)
        requests = self._tool_requests(first, history)
        calls: list[ToolCallRecord] = []
        if requests:
            calls = await AsyncToolExecutor(self.tools).execute_many(requests, ToolContext())
            tool_messages = [
                Message("tool", f"{call.name}: {call.output}", name=call.name) for call in calls
            ]
            final = await self.llm.generate(
                [
                    *history,
                    Message("assistant", first),
                    *tool_messages,
                    Message("user", "请基于工具结果给出最终回复。"),
                ],
                config=self.config,
            )
        else:
            final = first
        return AgentResponse(
            content=final,
            messages=[*history, Message("assistant", final)],
            tool_calls=calls,
        )

    def _tool_requests(self, llm_text: str, messages: Sequence[Message]) -> list[ToolRequest]:
        explicit = self._parse_explicit_tool_calls(llm_text)
        if explicit:
            return explicit
        user_text = next((message.content for message in reversed(messages) if message.role == "user"), "")
        return self._heuristic_tool_requests(user_text)

    @staticmethod
    def _parse_explicit_tool_calls(text: str) -> list[ToolRequest]:
        requests: list[ToolRequest] = []
        for match in re.finditer(r"```tool\s*(.*?)```", text, flags=re.DOTALL):
            try:
                payload = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    raw_args = item.get("args")
                    args: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}
                    requests.append(ToolRequest(item["name"], dict(args)))
        return requests

    def _heuristic_tool_requests(self, text: str) -> list[ToolRequest]:
        lowered = text.lower()
        requests: list[ToolRequest] = []
        names = {tool.name for tool in self.tools.list()}
        if "calculator" in names and re.fullmatch(r"[\d\s+\-*/().%^]+", text.strip()):
            requests.append(ToolRequest("calculator", {"expression": text.strip().replace("^", "**")}))
        if "extract_visit_requirement" in names and self._looks_like_visit(text):
            requests.append(ToolRequest("extract_visit_requirement", {"text": text}))
        if "search_suppliers" in names and any(
            keyword in text for keyword in ("供应商", "厂", "客户", "安科", "恒曜", "电子")
        ):
            requests.append(ToolRequest("search_suppliers", {"text": text}))
        if "feishu_calendar" in names and any(
            keyword in lowered for keyword in ("日历", "日程", "calendar", "空闲", "忙闲")
        ):
            requests.append(ToolRequest("feishu_calendar", {}))
        if "search" in names and any(
            keyword in text for keyword in ("搜索", "查一下", "资料", "新闻", "官网", "背景")
        ):
            requests.append(ToolRequest("search", {"query": text}))
        if "memory" in names and any(keyword in text for keyword in ("记住", "记一下", "记忆")):
            requests.append(ToolRequest("memory", {"operation": "remember", "text": text}))
        if "rag" in names and any(keyword in text for keyword in ("知识库", "资料库", "RAG", "rag")):
            requests.append(ToolRequest("rag", {"operation": "search", "query": text}))
        if "context_engineering" in names and any(
            keyword in text for keyword in ("上下文", "context", "连续对话")
        ):
            requests.append(ToolRequest("context_engineering", {"query": text}))
        if "communication_protocol" in names and any(
            keyword in text for keyword in ("MCP", "A2A", "ANP", "协议", "通讯")
        ):
            requests.append(ToolRequest("communication_protocol", {"payload": {"text": text}}))
        if "agentic_rl" in names and any(
            keyword in text for keyword in ("反馈", "奖励", "reward", "RL", "rl")
        ):
            requests.append(ToolRequest("agentic_rl", {"operation": "summary"}))
        if "generate_itinerary_plan" in names and any(
            keyword in text for keyword in ("行程", "路线", "规划", "安排")
        ):
            requests.append(ToolRequest("generate_itinerary_plan", {}))
        return requests

    @staticmethod
    def _looks_like_visit(text: str) -> bool:
        return any(
            keyword in text
            for keyword in (
                "拜访",
                "去",
                "到",
                "供应商",
                "客户",
                "厂",
                "行程",
                "路线",
                "日程",
                "安排",
            )
        )
