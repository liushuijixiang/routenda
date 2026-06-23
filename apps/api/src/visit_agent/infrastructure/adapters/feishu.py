from __future__ import annotations

from datetime import datetime, timedelta
import json
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx

from visit_agent.agent.tools.result import ToolResult
from visit_agent.domain.models import UTC
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker, resilient_tool_call


class FeishuOpenPlatformAdapter:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = "https://open.feishu.cn/open-apis",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=7.0, trust_env=False)
        self._owns_client = client is None
        self.breaker = CircuitBreaker("feishu-open-platform")
        self._tenant_token = ""
        self._token_expires_at = 0.0

    async def send_text(
        self,
        receive_id: str,
        text: str,
        *,
        receive_id_type: str = "chat_id",
    ) -> ToolResult:
        return await self._request(
            "send_text",
            "POST",
            f"/im/v1/messages?receive_id_type={quote(receive_id_type, safe='')}",
            payload={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )

    async def list_calendars(self) -> ToolResult:
        return await self._request("list_calendars", "GET", "/calendar/v4/calendars")

    async def list_events(
        self,
        calendar_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ToolResult:
        start = start or datetime.now(UTC) - timedelta(days=7)
        end = end or datetime.now(UTC) + timedelta(days=30)
        path = (
            f"/calendar/v4/calendars/{quote(calendar_id, safe='')}/events"
            f"?start_time={int(start.timestamp())}&end_time={int(end.timestamp())}"
        )
        return await self._request("list_events", "GET", path)

    async def _request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        async def call() -> ToolResult:
            token = await self._token()
            if not token.ok:
                return token
            response = await self.client.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._tenant_token}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code == 429 or response.status_code >= 500:
                return ToolResult.failure(
                    "feishu_unavailable",
                    f"Feishu returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                message = _feishu_error_message(response)
                return ToolResult.failure(
                    "feishu_rejected", f"Feishu returned HTTP {response.status_code}: {message}"
                )
            body = response.json()
            if body.get("code", 0) not in {0, None}:
                return ToolResult.failure("feishu_rejected", str(body.get("msg", "Feishu error")))
            return ToolResult.success(body.get("data", body))

        return await resilient_tool_call(
            f"feishu.{operation}", call, self.breaker, attempts=2, timeout_seconds=8
        )

    async def _token(self) -> ToolResult:
        if self._tenant_token and monotonic() < self._token_expires_at:
            return ToolResult.success({"cached": True})
        if not self.app_id or not self.app_secret:
            return ToolResult.failure(
                "missing_credentials", "Feishu credentials are not configured"
            )
        response = await self.client.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        if response.status_code >= 400:
            return ToolResult.failure(
                "feishu_auth_failed", f"Feishu auth HTTP {response.status_code}"
            )
        body = response.json()
        if body.get("code", 0) != 0:
            return ToolResult.failure("feishu_auth_failed", str(body.get("msg", "auth failed")))
        self._tenant_token = str(body["tenant_access_token"])
        self._token_expires_at = monotonic() + max(60, int(body.get("expire", 7200)) - 120)
        return ToolResult.success({"cached": False})

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def extract_feishu_message_text(event: dict[str, Any]) -> str:
    message = event.get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(parsed, dict):
            return str(parsed.get("text", content)).strip()
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    return ""


def format_calendar_summary(calendar_id: str, events_payload: Any) -> str:
    if not isinstance(events_payload, dict):
        return f"飞书日历 {calendar_id} 暂无可展示数据。"
    events = events_payload.get("items") or events_payload.get("events") or []
    if not events:
        return f"飞书日历 {calendar_id} 近期没有查询到日程。"

    lines = [f"飞书日历 {calendar_id} 近期日程："]
    for item in events[:5]:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") or item.get("title") or "未命名日程"
        start = item.get("start_time") or item.get("start") or {}
        if isinstance(start, dict):
            start_text = str(start.get("timestamp") or start.get("date") or start.get("date_time") or "")
        else:
            start_text = str(start)
        lines.append(f"- {summary} {start_text}".rstrip())
    if len(events) > 5:
        lines.append(f"... 还有 {len(events) - 5} 条")
    return "\n".join(lines)


def _feishu_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    msg = str(body.get("msg") or body.get("message") or "")
    violations = body.get("error", {}).get("permission_violations", {})
    if violations:
        scopes = [
            str(item.get("subject"))
            for item in violations
            if isinstance(item, dict) and item.get("subject")
        ]
        if scopes:
            return f"{msg}; missing scopes: {', '.join(scopes)}"
    return msg or str(body)[:500]
