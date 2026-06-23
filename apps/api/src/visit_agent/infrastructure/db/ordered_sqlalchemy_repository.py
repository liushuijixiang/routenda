from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from visit_agent.domain.models import AuditEvent, DataQualityIssue
from visit_agent.infrastructure.db import sqlalchemy_models as models
from visit_agent.infrastructure.db import sqlalchemy_repository as legacy
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


class OrderedSQLAlchemyRepository(legacy.SQLAlchemyRepository):
    """SQLAlchemy repository with deterministic seed and FK-safe flush ordering."""

    def __init__(self, database_url: str, seed_if_empty: bool = True) -> None:
        InMemoryRepository.__init__(self)
        self.database_url = database_url
        self._seeding = False
        self.engine = create_engine(database_url)

        if database_url.startswith("sqlite"):

            def enable_sqlite_foreign_keys(
                dbapi_connection: Any,
                _connection_record: Any,
            ) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(self.engine, "connect", enable_sqlite_foreign_keys)

        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        models.Base.metadata.create_all(self.engine)
        self.load()

        if seed_if_empty and not self.suppliers:
            self._seeding = True
            try:
                seed_demo(self)
            finally:
                self._seeding = False
            self.flush_all()

    def audit_event(self, audit_event: AuditEvent) -> None:
        if self._seeding:
            InMemoryRepository.audit_event(self, audit_event)
            return
        super().audit_event(audit_event)

    def save_data_quality_issue(self, issue: DataQualityIssue) -> DataQualityIssue:
        if self._seeding:
            return InMemoryRepository.save_data_quality_issue(self, issue)
        return super().save_data_quality_issue(issue)

    @staticmethod
    def _add_and_flush(session: Session, rows: Iterable[Any]) -> None:
        items = list(rows)
        if items:
            session.add_all(items)
        session.flush()

    def flush_all(self) -> None:
        """Replace the snapshot while respecting every foreign-key dependency."""
        with self.session_factory() as session:
            legacy.clear_tables(session)
            session.flush()

            self._add_and_flush(
                session,
                (
                    models.SupplierRow(**legacy.supplier_to_row(item))
                    for item in self.suppliers.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.SupplierSiteRow(**legacy.site_to_row(item))
                    for item in self.sites.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.ContactRow(**legacy.contact_to_row(item))
                    for item in self.contacts.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.ContactAssignmentRow(**legacy.assignment_to_row(item))
                    for item in self.assignments
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.VisitRequirementRow(**legacy.requirement_to_row(item))
                    for item in self.requirements.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.RequirementRevisionRow(**legacy.revision_to_row(item))
                    for item in self.revisions
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.AgentSessionRow(session_id=session_id, state=state)
                    for session_id, state in self.agent_sessions.items()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.IdempotencyRecordRow(
                        key=key,
                        response=jsonable_encoder(item["response"]),
                    )
                    for key, item in self.idempotency_records.items()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.AvailabilityWindowRow(**legacy.availability_to_row(item))
                    for item in self.availability
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.AvailabilityTokenRow(**legacy.token_to_row(item))
                    for item in self.availability_tokens.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.ApprovalRequestRow(**legacy.approval_to_row(item))
                    for item in self.approvals.values()
                ),
            )
            self._add_and_flush(
                session,
                (models.AuditEventRow(**legacy.audit_to_row(item)) for item in self.audit),
            )
            self._add_and_flush(
                session,
                (
                    models.OutboxJobRow(**legacy.outbox_to_row(item))
                    for item in self.outbox.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.HumanTaskRow(**legacy.human_task_to_row(item))
                    for item in self.human_tasks.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.ItineraryPlanRow(**legacy.plan_to_row(item))
                    for item in self.plans.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.ItineraryLegRow(**legacy.leg_to_row(plan.id, leg))
                    for plan in self.plans.values()
                    for leg in plan.legs
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.AppointmentRow(**legacy.appointment_to_row(item))
                    for item in self.appointments.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.AppointmentVersionRow(**legacy.appointment_version_to_row(item))
                    for item in self.appointment_versions
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.CalendarBindingRow(**legacy.calendar_binding_to_row(item))
                    for item in self.calendar_bindings.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.CalendarConflictRow(**legacy.calendar_conflict_to_row(item))
                    for item in self.calendar_conflicts.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.ConversationThreadRow(**legacy.conversation_to_row(item))
                    for item in self.conversations.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.MessageRow(**legacy.message_to_row(item))
                    for item in self.messages.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.MasterDataChangeRequestRow(
                        **legacy.master_data_change_to_row(item)
                    )
                    for item in self.master_data_changes.values()
                ),
            )
            self._add_and_flush(
                session,
                (
                    models.DataQualityIssueRow(**legacy.data_quality_issue_to_row(item))
                    for item in self.data_quality_issues.values()
                ),
            )
            session.commit()
