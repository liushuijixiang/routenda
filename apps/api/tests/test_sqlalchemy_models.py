import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agent.session_store import SessionStore
from visit_agent.application.tokens import AvailabilityTokenService
from visit_agent.domain.models import (
    UTC,
    Appointment,
    AppointmentVersion,
    AuditEvent,
    CalendarBinding,
    CalendarConflict,
    ConversationThread,
    Message,
    MasterDataChangeRequest,
    OutboxJob,
    RequirementRevision,
)
from visit_agent.infrastructure.db.sqlalchemy_models import Base
from visit_agent.infrastructure.db.sqlalchemy_repository import SQLAlchemyRepository


class SQLAlchemyModelTests(unittest.TestCase):
    def test_metadata_creates_core_tables(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            tables = set(inspect(engine).get_table_names())
            self.assertIn("suppliers", tables)
            self.assertIn("visit_requirements", tables)
            self.assertIn("approval_requests", tables)
            self.assertIn("audit_events", tables)
            self.assertIn("outbox_jobs", tables)
            self.assertIn("human_tasks", tables)
            self.assertIn("itinerary_plans", tables)
            self.assertIn("itinerary_legs", tables)
            self.assertIn("conversation_threads", tables)
            self.assertIn("messages", tables)
            self.assertIn("appointments", tables)
            self.assertIn("appointment_versions", tables)
            self.assertIn("calendar_bindings", tables)
            self.assertIn("calendar_conflicts", tables)
            self.assertIn("master_data_change_requests", tables)
            self.assertIn("data_quality_issues", tables)
            self.assertIn("availability_tokens", tables)
            self.assertIn("requirement_revisions", tables)
            self.assertIn("agent_sessions", tables)
            self.assertIn("idempotency_records", tables)
        finally:
            engine.dispose()

    def test_sqlalchemy_repository_seeds_and_reloads_demo(self):
        with TemporaryDirectory() as tmp:
            database_url = f"sqlite+pysqlite:///{Path(tmp) / 'demo.db'}"
            repo = SQLAlchemyRepository(database_url)
            self.assertEqual(len(repo.suppliers), 8)
            self.assertEqual(len(repo.sites), 12)
            self.assertEqual(len(repo.contacts), 20)
            repo.audit_event(
                AuditEvent(
                    actor="test", action="persist_check", entity="repository", entity_id="demo"
                )
            )
            token = AvailabilityTokenService(repo).issue(next(iter(repo.requirements)))
            requirement = next(iter(repo.requirements.values()))
            requirement.paused_at = datetime.now(UTC)
            repo.revisions.append(
                RequirementRevision(
                    requirement_id=requirement.id,
                    diff={"fields": {"paused_at": {"before": None, "after": "now"}}},
                    source="test",
                    actor="test",
                )
            )
            intake = VisitCoordinatorAgent(repo).intake(
                "下周去苏州看 A 供应商，A 优先，王经理最好参加，周四 18 点前回上海。"
            )
            session_id = intake.data["session_id"]
            repo.save_idempotency_record("requirements:create:test", {"id": "stable-response"})
            repo.flush_all()
            job = repo.add_outbox(
                OutboxJob(
                    kind="email",
                    payload={"to": "supplier@example.test"},
                    idempotency_key="same-key",
                )
            )
            same = repo.add_outbox(
                OutboxJob(
                    kind="email",
                    payload={"to": "supplier@example.test"},
                    idempotency_key="same-key",
                )
            )
            self.assertEqual(job.id, same.id)
            plan = VisitCoordinatorAgent(repo).plan([requirement.id]).data
            self.assertIn(plan.id, repo.plans)
            leg = plan.legs[0]
            appointment = repo.save_appointment(
                Appointment(
                    requirement_id=requirement.id,
                    site_id=requirement.draft.site_id,
                    start=leg.start,
                    end=leg.end,
                    participants=requirement.draft.required_people,
                )
            )
            repo.add_appointment_version(
                AppointmentVersion(
                    appointment_id=appointment.id,
                    before={"status": "none"},
                    after={"status": "tentative"},
                    reason="test",
                )
            )
            binding = repo.save_calendar_binding(
                CalendarBinding(
                    appointment_id=appointment.id,
                    provider="mock",
                    calendar_id="primary",
                    external_event_id="event-1",
                    etag="v1",
                    last_sync_at=datetime.now(UTC),
                )
            )
            repo.save_calendar_conflict(
                CalendarConflict(
                    appointment_id=appointment.id,
                    binding_id=binding.id,
                    local_snapshot={"etag": "v1"},
                    external_snapshot={"etag": "v2"},
                    reason="external_change",
                )
            )
            thread = repo.save_conversation(
                ConversationThread(
                    channel="email",
                    external_thread_id="thread-1",
                    requirement_id=requirement.id,
                    requirement_version=requirement.version,
                )
            )
            repo.save_message(
                Message(
                    thread_id=thread.id,
                    direction="inbound",
                    body="available",
                    send_status="received",
                )
            )
            change = repo.save_master_data_change(
                MasterDataChangeRequest(
                    entity_type="supplier",
                    entity_id=requirement.draft.supplier_id,
                    original_value={"display_name": "old"},
                    proposed_value={"display_name": "new"},
                    source_message_id=None,
                )
            )

            reloaded = SQLAlchemyRepository(database_url, seed_if_empty=False)
            self.assertEqual(len(reloaded.suppliers), 8)
            self.assertTrue(any(event.action == "persist_check" for event in reloaded.audit))
            self.assertEqual(len(reloaded.outbox), 1)
            self.assertEqual(len(reloaded.availability_tokens), 1)
            self.assertNotIn(token, reloaded.availability_tokens)
            self.assertEqual(len(reloaded.revisions), 1)
            self.assertEqual(len(reloaded.plans), 1)
            self.assertEqual(len(reloaded.plans[plan.id].legs), len(plan.legs))
            self.assertEqual(len(reloaded.appointments), 1)
            self.assertEqual(len(reloaded.appointment_versions), 1)
            self.assertEqual(len(reloaded.calendar_bindings), 1)
            self.assertEqual(len(reloaded.calendar_conflicts), 1)
            self.assertEqual(len(reloaded.conversations), 1)
            self.assertEqual(len(reloaded.messages), 1)
            self.assertEqual(reloaded.master_data_changes[change.id].approval_status, "pending")
            self.assertEqual(len(reloaded.data_quality_issues), 2)
            self.assertIsNotNone(reloaded.requirements[requirement.id].paused_at)
            restored = SessionStore(reloaded).get(session_id)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.session_id, session_id)
            self.assertEqual(
                reloaded.idempotency_records["requirements:create:test"]["response"]["id"],
                "stable-response",
            )
            repo.close()
            reloaded.close()


if __name__ == "__main__":
    unittest.main()
