from __future__ import annotations

from collections.abc import Sequence
import logging
from typing import Any, Protocol

import httpx

from visit_agent.core.config import AgentConfig
from visit_agent.core.exceptions import LLMError
from visit_agent.core.message import Message


logger = logging.getLogger(__name__)


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
        self.client = client
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
        if self.client is None:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await self._post(client, payload)
        else:
            response = await self._post(self.client, payload)
        if response.status_code >= 400:
            raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
        content = self._content(response.json()).strip()
        if not content:
            raise LLMError("LLM returned empty content")
        return content

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
        return await client.post(
            self.chat_completions_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

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
        if not self._owns_client and self.client is not None:
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
            return (
                "我查到这些结果，先按可执行信息整理给你：\n"
                + "\n".join(tool_context)
                + "\n\n你可以继续补充时间、地点、参与人或约束，我会接着推进安排。"
            )
        if not last_user.strip():
            return "我在。你可以直接告诉我拜访对象、时间、地点或要查询的问题。"
        if len(last_user.strip()) <= 6:
            return "我在。你想安排拜访、查日历，还是让我帮你规划一段行程？"
        return (
            "我可以处理这个。为了推进安排，请补充：拜访对象、地点、日期时间、预计时长、"
            "参与人，以及是否有必须出发或返回的时间。"
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
            logger.warning("llm_fallback error=%s", self.last_error)
            return await self.fallback.generate(messages, tools=tools, config=config)
