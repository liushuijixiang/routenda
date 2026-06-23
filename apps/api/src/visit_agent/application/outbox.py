from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.message import EmailMessage
import smtplib
from typing import Protocol
from zoneinfo import ZoneInfo

from visit_agent.agent.tools.result import ToolResult
from visit_agent.config import Settings, settings
from visit_agent.domain.models import (
    UTC,
    AuditEvent,
    ConversationThread,
    HumanTask,
    Message,
    OutboxJob,
    RequirementStatus,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository


def _parse_clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid clock value: {value}") from exc


@dataclass(frozen=True)
class ReminderPolicy:
    first_send_delay: timedelta = timedelta(0)
    interval: timedelta = timedelta(hours=24)
    max_reminders: int = 2
    quiet_start: time = time(20, 0)
    quiet_end: time = time(8, 0)
    timezone_name: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        if self.first_send_delay < timedelta(0):
            raise ValueError("first_send_delay cannot be negative")
        if self.interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if self.max_reminders < 0:
            raise ValueError("max_reminders cannot be negative")
        ZoneInfo(self.timezone_name)

    @classmethod
    def from_settings(cls, config: Settings = settings) -> ReminderPolicy:
        return cls(
            first_send_delay=timedelta(minutes=config.reminder_first_send_delay_minutes),
            interval=timedelta(hours=config.reminder_interval_hours),
            max_reminders=config.reminder_max_count,
            quiet_start=_parse_clock(config.reminder_quiet_start),
            quiet_end=_parse_clock(config.reminder_quiet_end),
            timezone_name=config.reminder_timezone,
        )

    def is_quiet(self, value: datetime) -> bool:
        local_clock = value.astimezone(ZoneInfo(self.timezone_name)).time().replace(tzinfo=None)
        if self.quiet_start == self.quiet_end:
            return False
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= local_clock < self.quiet_end
        return local_clock >= self.quiet_start or local_clock < self.quiet_end

    def next_allowed(self, value: datetime) -> datetime:
        if not self.is_quiet(value):
            return value
        zone = ZoneInfo(self.timezone_name)
        local = value.astimezone(zone)
        end_date = local.date()
        if (
            self.quiet_start > self.quiet_end
            and local.time().replace(tzinfo=None) >= self.quiet_start
        ):
            end_date += timedelta(days=1)
        allowed = datetime.combine(end_date, self.quiet_end, tzinfo=zone)
        return allowed.astimezone(UTC)

    def first_send_at(self, now: datetime | None = None) -> datetime:
        return self.next_allowed((now or datetime.now(UTC)) + self.first_send_delay)

    def next_reminder_at(self, now: datetime) -> datetime:
        return self.next_allowed(now + self.interval)


class DeliveryPort(Protocol):
    def send(self, job: OutboxJob) -> ToolResult: ...


class SMTPEmailSender:
    def __init__(self, host: str, port: int, from_address: str) -> None:
        self.host = host
        self.port = port
        self.from_address = from_address

    def send(self, job: OutboxJob) -> ToolResult:
        if job.kind != "email":
            return ToolResult.failure("unsupported_outbox_kind", f"Unsupported kind: {job.kind}")
        message = EmailMessage()
        reminder_number = int(job.payload.get("reminder_number", 0))
        message["From"] = self.from_address
        message["To"] = str(job.payload["to"])
        message["Subject"] = "拜访时间确认" if reminder_number == 0 else "提醒：请确认拜访时间"
        message.set_content(
            f"请通过以下安全链接选择可用时间：\n/public/availability/{job.payload['token']}\n"
        )
        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            return ToolResult.failure("smtp_delivery_failed", str(exc), retryable=True)
        return ToolResult.success({"to": job.payload["to"]}, "sent")


class OutboxWorker:
    def __init__(
        self,
        repo: InMemoryRepository,
        delivery: DeliveryPort,
        policy: ReminderPolicy | None = None,
        batch_size: int = 20,
    ) -> None:
        self.repo = repo
        self.delivery = delivery
        self.policy = policy or ReminderPolicy.from_settings()
        self.batch_size = batch_size

    def run_once(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        jobs = self.repo.claim_due_outbox(current, self.batch_size)
        for job in jobs:
            self._process(job, current)
        return len(jobs)

    def _process(self, job: OutboxJob, now: datetime) -> None:
        if self.policy.is_quiet(now):
            self.repo.defer_outbox(job, self.policy.next_allowed(now))
            return
        if self._is_supplier_follow_up(job) and not self._still_waiting(job):
            self.repo.complete_outbox(job, now)
            self._audit(job, "suppress_supplier_reminder", {"reason": "reply_received"})
            return

        result = self.delivery.send(job)
        if result.ok:
            self.repo.complete_outbox(job, now)
            self._record_outbound_message(job)
            self._audit(job, "deliver_outbox_job", {"attempts": job.attempts})
            self._schedule_next_action(job, now)
            return

        error = result.message or result.error_code or "delivery failed"
        if result.retryable and job.attempts < job.max_attempts:
            retry_at = now + timedelta(minutes=2 ** (job.attempts - 1))
            self.repo.retry_outbox(job, self.policy.next_allowed(retry_at), error)
            self._audit(job, "retry_outbox_job", {"error": error, "attempts": job.attempts})
            return
        self.repo.fail_outbox(job, now, error)
        self._create_task(job, "delivery_failed", f"消息发送失败：{error}")
        self._audit(job, "fail_outbox_job", {"error": error, "attempts": job.attempts})

    @staticmethod
    def _is_supplier_follow_up(job: OutboxJob) -> bool:
        return (
            job.payload.get("message_type") == "candidate_availability"
            and int(job.payload.get("reminder_number", 0)) > 0
        )

    def _still_waiting(self, job: OutboxJob) -> bool:
        requirement = self.repo.requirements.get(str(job.payload.get("requirement_id", "")))
        return bool(
            requirement
            and requirement.status == RequirementStatus.WAITING_REPLY
            and requirement.paused_at is None
            and requirement.deleted_at is None
        )

    def _schedule_next_action(self, job: OutboxJob, now: datetime) -> None:
        if job.payload.get("message_type") != "candidate_availability":
            return
        reminder_number = int(job.payload.get("reminder_number", 0))
        if not self._still_waiting(job):
            return
        if reminder_number >= self.policy.max_reminders:
            self._create_task(
                job,
                "supplier_no_response",
                f"已发送 {self.policy.max_reminders} 次提醒，需人工跟进供应商。",
            )
            return
        next_number = reminder_number + 1
        payload = {**job.payload, "reminder_number": next_number}
        self.repo.add_outbox(
            OutboxJob(
                kind=job.kind,
                payload=payload,
                idempotency_key=f"{job.idempotency_key}:reminder:{next_number}",
                available_at=self.policy.next_reminder_at(now),
                max_attempts=job.max_attempts,
            )
        )

    def _create_task(self, job: OutboxJob, kind: str, detail: str) -> None:
        requirement_id = str(job.payload.get("requirement_id", "unknown"))
        self.repo.add_human_task(
            HumanTask(
                kind=kind,
                entity_type="VisitRequirement",
                entity_id=requirement_id,
                title="供应商联络需要人工处理",
                detail=detail,
                idempotency_key=f"{kind}:{requirement_id}",
            )
        )

    def _record_outbound_message(self, job: OutboxJob) -> None:
        requirement_id = str(job.payload.get("requirement_id", ""))
        requirement = self.repo.requirements.get(requirement_id)
        thread = self.repo.save_conversation(
            ConversationThread(
                channel="email",
                external_thread_id=f"email:{requirement_id}:{job.payload.get('to', '')}",
                requirement_id=requirement_id or None,
                requirement_version=requirement.version if requirement else 0,
            )
        )
        self.repo.save_message(
            Message(
                thread_id=thread.id,
                direction="outbound",
                body=f"candidate_availability reminder={job.payload.get('reminder_number', 0)}",
                send_status="sent",
                parsed_result={"outbox_job_id": job.id},
            )
        )

    def _audit(self, job: OutboxJob, action: str, after: dict[str, object]) -> None:
        self.repo.append_worker_audit(
            AuditEvent(
                actor="outbox-worker",
                action=action,
                entity="OutboxJob",
                entity_id=job.id,
                after=after,
            )
        )
