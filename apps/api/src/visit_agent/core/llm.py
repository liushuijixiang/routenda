from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from visit_agent.core.config import AgentConfig
from visit_agent.core.exceptions import LLMError
from visit_agent.core.message import Message


class LLM(Protocol):
    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        config: AgentConfig | None = None,
    ) -> str:
        pass


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-5-mini",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_completions_url = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=20.0, trust_env=False)
        self._owns_client = client is None

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        config: AgentConfig | None = None,
    ) -> str:
        if not self.api_key:
            raise LLMError("LLM_API_KEY is not configured")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
                if message.role in {"system", "user", "assistant", "tool"}
            ],
            "temperature": config.temperature if config else 0.2,
            "max_tokens": config.max_tokens if config else 1200,
        }
        if tools:
            payload["tools"] = tools
        response = await self.client.post(
            self.chat_completions_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
        return self._content(response.json()).strip()

    @staticmethod
    def _content(payload: dict[str, Any]) -> str:
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
            )
        return str(content)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class FallbackLLM:
    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        config: AgentConfig | None = None,
    ) -> str:
        last_user = next((item.content for item in reversed(messages) if item.role == "user"), "")
        tool_context = [item.content for item in messages if item.role == "tool"]
        if tool_context:
            return "我是 Routenda Agent，已经调用工具处理了你的消息：\n" + "\n".join(
                tool_context
            )
        if not last_user.strip():
            return "我在。你可以直接告诉我拜访对象、时间、地点或要查询的问题。"
        return (
            "我是 Routenda Agent。我可以先和你确认需求，也可以调用供应商、日历、搜索、"
            "路线规划等工具继续分析。你刚才说："
            f"{last_user.strip()}"
        )


class ResilientLLM:
    def __init__(self, primary: LLM, fallback: LLM | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or FallbackLLM()
        self.last_error: str | None = None

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        config: AgentConfig | None = None,
    ) -> str:
        try:
            self.last_error = None
            return await self.primary.generate(messages, tools=tools, config=config)
        except Exception as exc:  # noqa: BLE001 - fallback keeps chat usable
            self.last_error = f"{type(exc).__name__}: {exc}"
            return await self.fallback.generate(messages, tools=tools, config=config)
