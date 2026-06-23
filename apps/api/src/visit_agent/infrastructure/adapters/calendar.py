from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from visit_agent.agent.tools.result import ToolResult
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker, resilient_tool_call


class CalendarPort(Protocol):
    async def query_busy(
        self,
        people: list[str],
        start: datetime,
        end: datetime,
    ) -> ToolResult: ...
    async def create_tentative_hold(self, appointment: object) -> ToolResult: ...
    async def confirm_event(self, appointment: object) -> ToolResult: ...
    async def update_or_cancel_event(
        self,
        event_id: str,
        payload: dict[str, Any],
    ) -> ToolResult: ...
    async def sync_external_changes(self) -> ToolResult: ...


class MockCalendarAdapter:
    def __init__(self, busy: dict[str, list[tuple[datetime, datetime]]]) -> None:
        self.busy = busy
        self.events: dict[str, dict[str, Any]] = {}

    async def query_busy(
        self,
        people: list[str],
        start: datetime,
        end: datetime,
    ) -> ToolResult:
        return ToolResult.success({p: self.busy.get(p, []) for p in people})

    async def create_tentative_hold(self, appointment: object) -> ToolResult:
        event_id = f"mock-event-{len(self.events) + 1}"
        self.events[event_id] = {"appointment": appointment, "status": "tentative", "etag": "v1"}
        return ToolResult.success({"external_event_id": event_id, "etag": "v1"})

    async def confirm_event(self, appointment: object) -> ToolResult:
        return ToolResult.success(
            {
                "external_event_id": getattr(appointment, "calendar_external_event_id", None),
                "status": "confirmed",
            }
        )

    async def update_or_cancel_event(
        self,
        event_id: str,
        payload: dict[str, Any],
    ) -> ToolResult:
        self.events[event_id] = payload
        return ToolResult.success({"external_event_id": event_id, "etag": "v2"})

    async def sync_external_changes(self) -> ToolResult:
        return ToolResult.success({"events": [], "conflicts": [], "delta_link": None})


class IcsCalendarAdapter(MockCalendarAdapter):
    def export_ics(self) -> str:
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Visit Agent//EN"]
        for event_id, item in self.events.items():
            appointment = item.get("appointment")
            start = getattr(appointment, "start", None)
            end = getattr(appointment, "end", None)
            if not start or not end:
                continue
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{event_id}",
                    f"DTSTART:{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                    f"DTEND:{end.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                    "SUMMARY:Supplier visit",
                    "END:VEVENT",
                ]
            )
        lines.extend(["END:VCALENDAR", ""])
        return "\r\n".join(lines)

    def import_ics(self, content: str) -> ToolResult:
        if "BEGIN:VCALENDAR" not in content or "END:VCALENDAR" not in content:
            return ToolResult.failure("invalid_ics", "ICS calendar envelope is missing")
        events = content.count("BEGIN:VEVENT")
        return ToolResult.success({"event_count": events, "raw": content})


class FeishuCalendarAdapter:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = "https://open.feishu.cn/open-apis",
        calendar_id: str = "primary",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.calendar_id = calendar_id
        self.client = client or httpx.AsyncClient(timeout=7.0, trust_env=False)
        self._owns_client = client is None
        self.breaker = CircuitBreaker("feishu")
        self._tenant_token = ""
        self._token_expires_at = 0.0

    async def query_busy(
        self,
        people: list[str],
        start: datetime,
        end: datetime,
    ) -> ToolResult:
        return await self._request(
            "query_busy",
            "POST",
            "/calendar/v4/freebusy/list",
            payload={
                "time_min": self._iso(start),
                "time_max": self._iso(end),
                "user_ids": people,
            },
        )

    async def create_tentative_hold(self, appointment: object) -> ToolResult:
        result = await self._request(
            "create_tentative_hold",
            "POST",
            f"/calendar/v4/calendars/{quote(self.calendar_id, safe='')}/events",
            payload=self._event_payload(appointment, "Supplier visit (tentative)", "tentative"),
        )
        if result.ok:
            event = result.data.get("event", result.data)
            result.data = {
                "external_event_id": event.get("event_id") or event.get("id", ""),
                "etag": event.get("etag", ""),
                "status": "tentative",
            }
        return result

    async def confirm_event(self, appointment: object) -> ToolResult:
        event_id = getattr(appointment, "calendar_external_event_id", "")
        if not event_id:
            return ToolResult.failure(
                "missing_external_event", "Appointment has no Feishu event ID"
            )
        result = await self._request(
            "confirm_event",
            "PATCH",
            f"/calendar/v4/calendars/{quote(self.calendar_id, safe='')}/events/{quote(event_id, safe='')}",
            payload={"summary": "Supplier visit (confirmed)", "status": "confirmed"},
        )
        if result.ok:
            result.data = {"external_event_id": event_id, "etag": "", "status": "confirmed"}
        return result

    async def update_or_cancel_event(
        self,
        event_id: str,
        payload: dict[str, Any],
    ) -> ToolResult:
        cancel = bool(payload.get("cancel"))
        method = "DELETE" if cancel else "PATCH"
        return await self._request(
            "cancel_event" if cancel else "update_event",
            method,
            f"/calendar/v4/calendars/{quote(self.calendar_id, safe='')}/events/{quote(event_id, safe='')}",
            payload=None if cancel else payload,
        )

    async def sync_external_changes(self) -> ToolResult:
        return ToolResult.success({"events": [], "conflicts": [], "delta_link": None})

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
                return ToolResult.failure(
                    "feishu_rejected", f"Feishu returned HTTP {response.status_code}"
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

    @staticmethod
    def _event_payload(appointment: object, summary: str, status: str) -> dict[str, Any]:
        start = getattr(appointment, "start")
        end = getattr(appointment, "end")
        return {
            "summary": summary,
            "status": status,
            "start_time": {"timestamp": str(int(start.timestamp()))},
            "end_time": {"timestamp": str(int(end.timestamp()))},
        }

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class MicrosoftGraphCalendarAdapter:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        client: httpx.AsyncClient | None = None,
        graph_base_url: str = "https://graph.microsoft.com/v1.0",
        login_base_url: str = "https://login.microsoftonline.com",
        calendar_user: str = "me",
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.graph_base_url = graph_base_url.rstrip("/")
        self.login_base_url = login_base_url.rstrip("/")
        self.calendar_user = calendar_user
        self.breaker = CircuitBreaker("microsoft-graph")
        self.client = client or httpx.AsyncClient(timeout=7.0, trust_env=False)
        self._owns_client = client is None
        self._access_token = ""
        self._token_expires_at = 0.0
        self.delta_link: str | None = None

    async def query_busy(
        self,
        people: list[str],
        start: datetime,
        end: datetime,
    ) -> ToolResult:
        result = await self._request(
            "query_busy",
            "POST",
            f"{self._user_path()}/calendar/getSchedule",
            payload={
                "schedules": people,
                "startTime": self._graph_datetime(start),
                "endTime": self._graph_datetime(end),
                "availabilityViewInterval": 15,
            },
        )
        if result.ok:
            result.data = {
                item.get("scheduleId", ""): [
                    {
                        "start": period["start"]["dateTime"],
                        "end": period["end"]["dateTime"],
                        "status": period.get("status"),
                    }
                    for period in item.get("scheduleItems", [])
                ]
                for item in result.data.get("value", [])
            }
        return result

    async def create_tentative_hold(self, appointment: object) -> ToolResult:
        start = getattr(appointment, "start")
        end = getattr(appointment, "end")
        result = await self._request(
            "create_tentative_hold",
            "POST",
            f"{self._user_path()}/events",
            payload={
                "subject": "Supplier visit (tentative)",
                "start": self._graph_datetime(start),
                "end": self._graph_datetime(end),
                "showAs": "tentative",
                "isReminderOn": True,
                "transactionId": getattr(appointment, "id", None),
                "attendees": [
                    {
                        "emailAddress": {"address": person, "name": person},
                        "type": "required",
                    }
                    for person in getattr(appointment, "participants", [])
                    if "@" in person
                ],
            },
        )
        if result.ok:
            result.data = {
                "external_event_id": result.data["id"],
                "etag": result.data.get("@odata.etag", ""),
                "status": "tentative",
            }
        return result

    async def confirm_event(self, appointment: object) -> ToolResult:
        event_id = getattr(appointment, "calendar_external_event_id", None)
        if not event_id:
            return ToolResult.failure("missing_external_event", "Appointment has no Graph event ID")
        result = await self._request(
            "confirm_event",
            "PATCH",
            f"{self._user_path()}/events/{quote(event_id, safe='')}",
            payload={"showAs": "busy", "subject": "Supplier visit (confirmed)"},
        )
        if result.ok:
            result.data = {
                "external_event_id": event_id,
                "etag": result.data.get("@odata.etag", ""),
                "status": "confirmed",
            }
        return result

    async def update_or_cancel_event(
        self,
        event_id: str,
        payload: dict[str, Any],
    ) -> ToolResult:
        event_payload = dict(payload)
        cancel = bool(event_payload.pop("cancel", False))
        if isinstance(event_payload.get("start"), datetime):
            event_payload["start"] = self._graph_datetime(event_payload["start"])
        if isinstance(event_payload.get("end"), datetime):
            event_payload["end"] = self._graph_datetime(event_payload["end"])
        method = "DELETE" if cancel else "PATCH"
        result = await self._request(
            "cancel_event" if cancel else "update_event",
            method,
            f"{self._user_path()}/events/{quote(event_id, safe='')}",
            payload=None if cancel else event_payload,
        )
        if result.ok:
            result.data = {
                "external_event_id": event_id,
                "etag": result.data.get("@odata.etag", "") if result.data else "",
                "status": "cancelled" if cancel else "updated",
            }
        return result

    async def sync_external_changes(self) -> ToolResult:
        now = datetime.now(UTC)
        if self.delta_link:
            path = self.delta_link
        else:
            start = (now - timedelta(days=30)).isoformat()
            end = (now + timedelta(days=180)).isoformat()
            path = (
                f"{self._user_path()}/calendarView/delta"
                f"?startDateTime={quote(start)}&endDateTime={quote(end)}"
            )
        result = await self._request("sync_external_changes", "GET", path)
        if not result.ok:
            return result
        events = list(result.data.get("value", []))
        next_link = result.data.get("@odata.nextLink")
        while next_link:
            page = await self._request("sync_external_changes_page", "GET", next_link)
            if not page.ok:
                return page
            events.extend(page.data.get("value", []))
            result = page
            next_link = page.data.get("@odata.nextLink")
        self.delta_link = result.data.get("@odata.deltaLink", self.delta_link)
        return ToolResult.success({"events": events, "delta_link": self.delta_link})

    async def _request(
        self,
        operation: str,
        method: str,
        path_or_url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        async def call() -> ToolResult:
            token = await self._token()
            if not token.ok:
                return token
            url = (
                path_or_url
                if path_or_url.startswith("http")
                else f"{self.graph_base_url}{path_or_url}"
            )
            response = await self.client.request(
                method,
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token.data}", "Accept": "application/json"},
            )
            if response.status_code == 429 or response.status_code >= 500:
                return ToolResult.failure(
                    "calendar_unavailable",
                    f"Microsoft Graph returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                return ToolResult.failure(
                    "calendar_rejected", f"Microsoft Graph returned HTTP {response.status_code}"
                )
            if response.status_code == 204 or not response.content:
                return ToolResult.success({})
            return ToolResult.success(response.json())

        return await resilient_tool_call(
            f"graph.{operation}", call, self.breaker, attempts=2, timeout_seconds=8
        )

    async def _token(self) -> ToolResult:
        if not self.tenant_id or not self.client_id or not self.client_secret:
            return ToolResult.failure(
                "missing_credentials", "Microsoft Graph credentials are not configured"
            )
        if self._access_token and monotonic() < self._token_expires_at - 60:
            return ToolResult.success(self._access_token)
        response = await self.client.post(
            f"{self.login_base_url}/{quote(self.tenant_id, safe='')}/oauth2/v2.0/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if response.status_code >= 400:
            return ToolResult.failure(
                "calendar_auth_failed",
                f"Microsoft identity platform returned HTTP {response.status_code}",
                retryable=response.status_code >= 500,
            )
        payload = response.json()
        self._access_token = str(payload["access_token"])
        self._token_expires_at = monotonic() + int(payload.get("expires_in", 3600))
        return ToolResult.success(self._access_token)

    @staticmethod
    def _graph_datetime(value: datetime) -> dict[str, str]:
        return {
            "dateTime": value.astimezone(UTC).replace(tzinfo=None).isoformat(),
            "timeZone": "UTC",
        }

    def _user_path(self) -> str:
        if self.calendar_user == "me":
            return "/me"
        return f"/users/{quote(self.calendar_user, safe='')}"

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
