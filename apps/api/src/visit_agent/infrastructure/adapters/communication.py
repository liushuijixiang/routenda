from datetime import datetime
import re
from typing import Any, Protocol

from visit_agent.agent.tools.result import ToolResult
from visit_agent.application.outbox import ReminderPolicy
from visit_agent.domain.models import AvailabilityWindow, OutboxJob
from visit_agent.infrastructure.db.repository import InMemoryRepository


class CommunicationPort(Protocol):
    async def send_candidate_email(
        self, requirement_id: str, to_email: str, token: str
    ) -> ToolResult: ...
    async def inbound_webhook(self, payload: dict[str, Any]) -> ToolResult: ...


class MockCommunicationAdapter:
    def __init__(
        self,
        repo: InMemoryRepository,
        reminder_policy: ReminderPolicy | None = None,
    ) -> None:
        self.repo = repo
        self.reminder_policy = reminder_policy or ReminderPolicy.from_settings()

    async def send_candidate_email(
        self, requirement_id: str, to_email: str, token: str
    ) -> ToolResult:
        job = self.repo.add_outbox(
            OutboxJob(
                kind="email",
                payload={
                    "to": to_email,
                    "token": token,
                    "requirement_id": requirement_id,
                    "message_type": "candidate_availability",
                    "reminder_number": 0,
                },
                idempotency_key=f"candidate-email:{requirement_id}:{to_email}",
                available_at=self.reminder_policy.first_send_at(),
            )
        )
        return ToolResult.success(job, "queued")

    async def inbound_webhook(self, payload: dict[str, Any]) -> ToolResult:
        return ToolResult.success({"parsed": payload, "trusted_as_instruction": False})


class SMTPAdapter(MockCommunicationAdapter):
    pass


ISO_DATETIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})")


def parse_inbound_reply(body: str) -> dict[str, Any]:
    normalized = body.strip()
    datetimes: list[str] = []
    for value in ISO_DATETIME_PATTERN.findall(normalized):
        candidate = value.replace("Z", "+00:00")
        try:
            datetimes.append(datetime.fromisoformat(candidate).isoformat())
        except ValueError:
            continue

    candidate_windows = [
        {"start": datetimes[index], "end": datetimes[index + 1]}
        for index in range(0, len(datetimes) - 1, 2)
    ]
    contact_match = re.search(
        r"(?:新联系人|联系人(?:改为|变更为))[:：]?\s*([^，。；;\n]+)", normalized
    )
    address_match = re.search(r"(?:新地址|地址(?:改为|变更为))[:：]?\s*([^，。；;\n]+)", normalized)
    rejected = any(word in normalized for word in ("都不行", "无法安排", "不能接待", "拒绝"))
    reschedule_requested = any(word in normalized for word in ("改期", "换个时间", "重新安排"))
    has_relative_time = bool(
        re.search(r"(?:周|星期)[一二三四五六日天](?:上午|下午|晚上)?", normalized)
    )
    return {
        "candidate_windows": candidate_windows,
        "relative_time_text": normalized if has_relative_time else None,
        "rejected": rejected,
        "reschedule_requested": reschedule_requested,
        "contact_change": contact_match.group(1).strip() if contact_match else None,
        "address_change": address_match.group(1).strip() if address_match else None,
        "needs_human_review": True,
        "trusted_as_instruction": False,
    }


def submit_public_availability(
    repo: InMemoryRepository,
    requirement_id: str,
    participant: str,
    start: datetime,
    end: datetime,
    timezone_name: str = "Asia/Shanghai",
    preference: int = 3,
    source: str = "public_token",
) -> AvailabilityWindow:
    window = AvailabilityWindow(
        requirement_id=requirement_id,
        participant=participant,
        start=start,
        end=end,
        timezone_name=timezone_name,
        preference=preference,
        source=source,
    )
    repo.availability.append(window)
    return window
