from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from visit_agent.agent.tools.result import ToolResult
from visit_agent.application.outbox import OutboxWorker, ReminderPolicy
from visit_agent.domain.models import UTC, OutboxJob, RequirementStatus
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo
from visit_agent.infrastructure.db.sqlalchemy_repository import SQLAlchemyRepository


class RecordingDelivery:
    def __init__(self, results: list[ToolResult] | None = None) -> None:
        self.results = results or []
        self.jobs: list[OutboxJob] = []

    def send(self, job: OutboxJob) -> ToolResult:
        self.jobs.append(job)
        if self.results:
            return self.results.pop(0)
        return ToolResult.success(message="sent")


def waiting_repository() -> tuple[InMemoryRepository, str]:
    repo = seed_demo(InMemoryRepository())
    requirement = next(iter(repo.requirements.values()))
    requirement.status = RequirementStatus.WAITING_REPLY
    return repo, requirement.id


def candidate_job(requirement_id: str, available_at: datetime) -> OutboxJob:
    return OutboxJob(
        kind="email",
        payload={
            "to": "supplier@example.test",
            "token": "secret-token",
            "requirement_id": requirement_id,
            "message_type": "candidate_availability",
            "reminder_number": 0,
        },
        idempotency_key=f"candidate-email:{requirement_id}:supplier@example.test",
        available_at=available_at,
    )


class ReminderPolicyTests(unittest.TestCase):
    def test_quiet_hours_roll_forward_in_configured_timezone(self) -> None:
        policy = ReminderPolicy(
            quiet_start=time(20),
            quiet_end=time(8),
            timezone_name="Asia/Shanghai",
        )
        quiet_time = datetime(2026, 6, 22, 13, tzinfo=UTC)  # 21:00 in Shanghai

        self.assertTrue(policy.is_quiet(quiet_time))
        self.assertEqual(
            policy.next_allowed(quiet_time),
            datetime(2026, 6, 23, 0, tzinfo=UTC),
        )


class OutboxWorkerTests(unittest.TestCase):
    def test_reminders_stop_at_cap_and_create_one_human_task(self) -> None:
        repo, requirement_id = waiting_repository()
        start = datetime(2026, 6, 22, 2, tzinfo=UTC)
        policy = ReminderPolicy(
            interval=timedelta(hours=1),
            max_reminders=2,
            quiet_start=time(23),
            quiet_end=time(1),
            timezone_name="UTC",
        )
        delivery = RecordingDelivery()
        repo.add_outbox(candidate_job(requirement_id, start))
        worker = OutboxWorker(repo, delivery, policy)

        self.assertEqual(worker.run_once(start), 1)
        self.assertEqual(worker.run_once(start + timedelta(hours=1)), 1)
        self.assertEqual(worker.run_once(start + timedelta(hours=2)), 1)
        self.assertEqual(worker.run_once(start + timedelta(hours=3)), 0)

        self.assertEqual([job.payload["reminder_number"] for job in delivery.jobs], [0, 1, 2])
        self.assertEqual(len(repo.outbox), 3)
        self.assertEqual(len(repo.human_tasks), 1)
        task = next(iter(repo.human_tasks.values()))
        self.assertEqual(task.kind, "supplier_no_response")

    def test_queued_reminder_is_suppressed_after_supplier_reply(self) -> None:
        repo, requirement_id = waiting_repository()
        start = datetime(2026, 6, 22, 2, tzinfo=UTC)
        policy = ReminderPolicy(
            interval=timedelta(hours=1),
            quiet_start=time(23),
            quiet_end=time(1),
            timezone_name="UTC",
        )
        delivery = RecordingDelivery()
        repo.add_outbox(candidate_job(requirement_id, start))
        worker = OutboxWorker(repo, delivery, policy)

        worker.run_once(start)
        repo.requirements[requirement_id].status = RequirementStatus.CANDIDATES_RECEIVED
        worker.run_once(start + timedelta(hours=1))

        self.assertEqual(len(delivery.jobs), 1)
        reminder = next(job for job in repo.outbox.values() if job.payload["reminder_number"] == 1)
        self.assertEqual(reminder.status, "completed")
        self.assertEqual(len(repo.human_tasks), 0)

    def test_retry_is_finite_and_failure_creates_idempotent_task(self) -> None:
        repo, requirement_id = waiting_repository()
        start = datetime(2026, 6, 22, 2, tzinfo=UTC)
        delivery = RecordingDelivery(
            [
                ToolResult.failure("smtp", "offline", retryable=True),
                ToolResult.failure("smtp", "offline", retryable=True),
            ]
        )
        job = candidate_job(requirement_id, start)
        job.max_attempts = 2
        repo.add_outbox(job)
        policy = ReminderPolicy(
            quiet_start=time(23),
            quiet_end=time(1),
            timezone_name="UTC",
        )
        worker = OutboxWorker(repo, delivery, policy)

        worker.run_once(start)
        self.assertEqual(job.status, "retry")
        worker.run_once(start + timedelta(minutes=1))

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 2)
        self.assertEqual(len(repo.human_tasks), 1)

    def test_quiet_hour_claim_is_deferred_without_consuming_attempt(self) -> None:
        repo, requirement_id = waiting_repository()
        quiet = datetime(2026, 6, 22, 21, tzinfo=UTC)
        job = candidate_job(requirement_id, quiet)
        repo.add_outbox(job)
        worker = OutboxWorker(
            repo,
            RecordingDelivery(),
            ReminderPolicy(
                quiet_start=time(20),
                quiet_end=time(8),
                timezone_name="UTC",
            ),
        )

        worker.run_once(quiet)

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempts, 0)
        self.assertEqual(job.available_at, datetime(2026, 6, 23, 8, tzinfo=UTC))


class SQLClaimTests(unittest.TestCase):
    def test_second_repository_cannot_claim_processing_job(self) -> None:
        with TemporaryDirectory() as tmp:
            database_url = f"sqlite+pysqlite:///{Path(tmp) / 'outbox.db'}"
            first = SQLAlchemyRepository(database_url, seed_if_empty=False)
            now = datetime(2026, 6, 22, 2, tzinfo=UTC)
            first.add_outbox(candidate_job("requirement-1", now))
            second = SQLAlchemyRepository(database_url, seed_if_empty=False)

            claimed = first.claim_due_outbox(now, 1)
            duplicate_claim = second.claim_due_outbox(now, 1)

            self.assertEqual(len(claimed), 1)
            self.assertEqual(duplicate_claim, [])
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
