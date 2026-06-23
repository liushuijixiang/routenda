from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, create_engine, delete, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from visit_agent.domain.models import (
    ApprovalRequest,
    Appointment,
    AppointmentVersion,
    AuditEvent,
    AvailabilityToken,
    AvailabilityWindow,
    CalendarBinding,
    CalendarConflict,
    Contact,
    ContactAssignment,
    ConversationThread,
    DataQualityIssue,
    HumanTask,
    ItineraryLeg,
    ItineraryPlan,
    MasterDataChangeRequest,
    Message,
    OutboxJob,
    RequirementRevision,
    RequirementStatus,
    Supplier,
    SupplierSite,
    VisitRequirement,
    VisitRequirementDraft,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo
from visit_agent.infrastructure.db.sqlalchemy_models import (
    ApprovalRequestRow,
    AgentSessionRow,
    AppointmentRow,
    AppointmentVersionRow,
    AvailabilityTokenRow,
    AuditEventRow,
    AvailabilityWindowRow,
    Base,
    CalendarBindingRow,
    CalendarConflictRow,
    ContactAssignmentRow,
    ContactRow,
    ConversationThreadRow,
    DataQualityIssueRow,
    HumanTaskRow,
    IdempotencyRecordRow,
    ItineraryLegRow,
    ItineraryPlanRow,
    MessageRow,
    MasterDataChangeRequestRow,
    OutboxJobRow,
    RequirementRevisionRow,
    SupplierRow,
    SupplierSiteRow,
    VisitRequirementRow,
)


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class SQLAlchemyRepository(InMemoryRepository):
    """SQLAlchemy-backed repository for the modular-monolith runtime.

    The service layer still works with the same dataclasses as the in-memory
    repository. This class makes those dataclasses durable enough for local
    PostgreSQL/SQLite execution while preserving the deterministic repository
    interface used by application services and tests.
    """

    def __init__(self, database_url: str, seed_if_empty: bool = True) -> None:
        super().__init__()
        self.database_url = database_url
        self.engine: Engine = create_engine(database_url)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.load()
        if seed_if_empty and not self.suppliers:
            seed_demo(self)
            self.flush_all()

    def audit_event(self, event: AuditEvent) -> None:
        super().audit_event(event)
        self.flush_all()

    def append_worker_audit(self, event: AuditEvent) -> None:
        self.audit.append(event)
        with self.session_factory() as session:
            session.add(AuditEventRow(**audit_to_row(event)))
            session.commit()

    def add_outbox(self, job: OutboxJob) -> OutboxJob:
        existing = self.outbox.get(job.idempotency_key)
        if existing:
            return existing
        with self.session_factory() as session:
            persisted = session.scalar(
                select(OutboxJobRow).where(OutboxJobRow.idempotency_key == job.idempotency_key)
            )
            if persisted is not None:
                result = outbox_from_row(persisted)
                self.outbox[result.idempotency_key] = result
                return result
            session.add(OutboxJobRow(**outbox_to_row(job)))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                persisted = session.scalar(
                    select(OutboxJobRow).where(OutboxJobRow.idempotency_key == job.idempotency_key)
                )
                if persisted is None:
                    raise
                job = outbox_from_row(persisted)
        self.outbox[job.idempotency_key] = job
        return job

    def claim_due_outbox(
        self,
        now: datetime,
        limit: int,
        lock_timeout: timedelta = timedelta(minutes=5),
    ) -> list[OutboxJob]:
        stale_before = now - lock_timeout
        with self.session_factory() as session:
            statement = (
                select(OutboxJobRow)
                .where(
                    OutboxJobRow.available_at <= now,
                    or_(
                        OutboxJobRow.status.in_(("pending", "retry")),
                        and_(
                            OutboxJobRow.status == "processing",
                            OutboxJobRow.locked_at.is_not(None),
                            OutboxJobRow.locked_at <= stale_before,
                        ),
                    ),
                )
                .order_by(OutboxJobRow.available_at, OutboxJobRow.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = list(session.scalars(statement))
            for row in rows:
                row.status = "processing"
                row.locked_at = now
                row.attempts += 1
            session.commit()
            claimed = [outbox_from_row(row) for row in rows]
        for job in claimed:
            self.outbox[job.idempotency_key] = job
        return claimed

    def complete_outbox(self, job: OutboxJob, now: datetime) -> None:
        super().complete_outbox(job, now)
        self._persist_outbox_state(job)

    def defer_outbox(self, job: OutboxJob, available_at: datetime) -> None:
        super().defer_outbox(job, available_at)
        self._persist_outbox_state(job)

    def retry_outbox(self, job: OutboxJob, available_at: datetime, error: str) -> None:
        super().retry_outbox(job, available_at, error)
        self._persist_outbox_state(job)

    def fail_outbox(self, job: OutboxJob, now: datetime, error: str) -> None:
        super().fail_outbox(job, now, error)
        self._persist_outbox_state(job)

    def _persist_outbox_state(self, job: OutboxJob) -> None:
        with self.session_factory() as session:
            row = session.get(OutboxJobRow, job.id)
            if row is None:
                raise KeyError(f"outbox job disappeared: {job.id}")
            row.status = job.status
            row.attempts = job.attempts
            row.max_attempts = job.max_attempts
            row.available_at = job.available_at
            row.locked_at = job.locked_at
            row.completed_at = job.completed_at
            row.last_error = job.last_error
            session.commit()

    def add_human_task(self, task: HumanTask) -> HumanTask:
        existing = self.human_tasks.get(task.idempotency_key)
        if existing:
            return existing
        with self.session_factory() as session:
            row = session.scalar(
                select(HumanTaskRow).where(HumanTaskRow.idempotency_key == task.idempotency_key)
            )
            if row is None:
                row = HumanTaskRow(**human_task_to_row(task))
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    row = session.scalar(
                        select(HumanTaskRow).where(
                            HumanTaskRow.idempotency_key == task.idempotency_key
                        )
                    )
                    if row is None:
                        raise
            task = human_task_from_row(row)
        self.human_tasks[task.idempotency_key] = task
        return task

    def update_human_task(self, task: HumanTask) -> None:
        super().update_human_task(task)
        with self.session_factory() as session:
            row = session.get(HumanTaskRow, task.id)
            if row is None:
                raise KeyError(f"human task disappeared: {task.id}")
            row.status = task.status
            session.commit()

    def save_plan(self, plan: ItineraryPlan) -> ItineraryPlan:
        result = super().save_plan(plan)
        self.flush_all()
        return result

    def save_appointment(self, appointment: Appointment) -> Appointment:
        result = super().save_appointment(appointment)
        self.flush_all()
        return result

    def add_appointment_version(self, version: AppointmentVersion) -> AppointmentVersion:
        result = super().add_appointment_version(version)
        self.flush_all()
        return result

    def save_calendar_binding(self, binding: CalendarBinding) -> CalendarBinding:
        result = super().save_calendar_binding(binding)
        self.flush_all()
        return result

    def save_calendar_conflict(self, conflict: CalendarConflict) -> CalendarConflict:
        result = super().save_calendar_conflict(conflict)
        self.flush_all()
        return result

    def save_conversation(self, thread: ConversationThread) -> ConversationThread:
        existing = next(
            (
                item
                for item in self.conversations.values()
                if item.external_thread_id == thread.external_thread_id
            ),
            None,
        )
        if existing:
            return existing
        result = super().save_conversation(thread)
        self.flush_all()
        return result

    def save_message(self, message: Message) -> Message:
        result = super().save_message(message)
        self.flush_all()
        return result

    def save_master_data_change(self, change: MasterDataChangeRequest) -> MasterDataChangeRequest:
        result = super().save_master_data_change(change)
        self.flush_all()
        return result

    def save_data_quality_issue(self, issue: DataQualityIssue) -> DataQualityIssue:
        result = super().save_data_quality_issue(issue)
        self.flush_all()
        return result

    def save_agent_session(
        self,
        session_id: str,
        state: dict[str, Any],
    ) -> None:
        super().save_agent_session(session_id, state)
        self.flush_all()

    def save_idempotency_record(self, key: str, response: Any) -> None:
        super().save_idempotency_record(key, response)
        with self.session_factory() as session:
            session.merge(IdempotencyRecordRow(key=key, response=jsonable_encoder(response)))
            session.commit()

    def load(self) -> None:
        with self.session_factory() as session:
            self.suppliers = {
                row.id: supplier_from_row(row) for row in session.scalars(select(SupplierRow))
            }
            self.sites = {
                row.id: site_from_row(row) for row in session.scalars(select(SupplierSiteRow))
            }
            self.contacts = {
                row.id: contact_from_row(row) for row in session.scalars(select(ContactRow))
            }
            self.assignments = [
                assignment_from_row(row) for row in session.scalars(select(ContactAssignmentRow))
            ]
            self.requirements = {
                row.id: requirement_from_row(row)
                for row in session.scalars(select(VisitRequirementRow))
            }
            self.revisions = [
                revision_from_row(row) for row in session.scalars(select(RequirementRevisionRow))
            ]
            self.agent_sessions = {
                row.session_id: row.state for row in session.scalars(select(AgentSessionRow))
            }
            self.idempotency_records = {
                row.key: {"response": row.response}
                for row in session.scalars(select(IdempotencyRecordRow))
            }
            self.availability = [
                availability_from_row(row) for row in session.scalars(select(AvailabilityWindowRow))
            ]
            self.availability_tokens = {
                row.token_hash: token_from_row(row)
                for row in session.scalars(select(AvailabilityTokenRow))
            }
            self.approvals = {
                row.id: approval_from_row(row)
                for row in session.scalars(select(ApprovalRequestRow))
            }
            self.audit = [audit_from_row(row) for row in session.scalars(select(AuditEventRow))]
            self.outbox = {
                row.idempotency_key: outbox_from_row(row)
                for row in session.scalars(select(OutboxJobRow))
            }
            self.human_tasks = {
                row.idempotency_key: human_task_from_row(row)
                for row in session.scalars(select(HumanTaskRow))
            }
            leg_rows = list(session.scalars(select(ItineraryLegRow)))
            legs_by_plan: dict[str, list[ItineraryLeg]] = {}
            for row in leg_rows:
                legs_by_plan.setdefault(row.plan_id, []).append(itinerary_leg_from_row(row))
            self.plans = {
                row.id: itinerary_plan_from_row(row, legs_by_plan.get(row.id, []))
                for row in session.scalars(select(ItineraryPlanRow))
            }
            self.appointments = {
                row.id: appointment_from_row(row) for row in session.scalars(select(AppointmentRow))
            }
            self.appointment_versions = [
                appointment_version_from_row(row)
                for row in session.scalars(select(AppointmentVersionRow))
            ]
            self.calendar_bindings = {
                row.id: calendar_binding_from_row(row)
                for row in session.scalars(select(CalendarBindingRow))
            }
            self.calendar_conflicts = {
                row.id: calendar_conflict_from_row(row)
                for row in session.scalars(select(CalendarConflictRow))
            }
            self.conversations = {
                row.id: conversation_from_row(row)
                for row in session.scalars(select(ConversationThreadRow))
            }
            self.messages = {
                row.id: message_from_row(row) for row in session.scalars(select(MessageRow))
            }
            self.master_data_changes = {
                row.id: master_data_change_from_row(row)
                for row in session.scalars(select(MasterDataChangeRequestRow))
            }
            self.data_quality_issues = {
                row.id: data_quality_issue_from_row(row)
                for row in session.scalars(select(DataQualityIssueRow))
            }

    def flush_all(self) -> None:
        with self.session_factory() as session:
            clear_tables(session)
            session.add_all(
                SupplierRow(**supplier_to_row(item)) for item in self.suppliers.values()
            )
            session.add_all(SupplierSiteRow(**site_to_row(item)) for item in self.sites.values())
            session.add_all(ContactRow(**contact_to_row(item)) for item in self.contacts.values())
            session.add_all(
                ContactAssignmentRow(**assignment_to_row(item)) for item in self.assignments
            )
            session.add_all(
                VisitRequirementRow(**requirement_to_row(item))
                for item in self.requirements.values()
            )
            session.add_all(
                RequirementRevisionRow(**revision_to_row(item)) for item in self.revisions
            )
            session.add_all(
                AgentSessionRow(session_id=session_id, state=state)
                for session_id, state in self.agent_sessions.items()
            )
            session.add_all(
                IdempotencyRecordRow(key=key, response=jsonable_encoder(item["response"]))
                for key, item in self.idempotency_records.items()
            )
            session.add_all(
                AvailabilityWindowRow(**availability_to_row(item)) for item in self.availability
            )
            session.add_all(
                AvailabilityTokenRow(**token_to_row(item))
                for item in self.availability_tokens.values()
            )
            session.add_all(
                ApprovalRequestRow(**approval_to_row(item)) for item in self.approvals.values()
            )
            session.add_all(AuditEventRow(**audit_to_row(item)) for item in self.audit)
            session.add_all(OutboxJobRow(**outbox_to_row(item)) for item in self.outbox.values())
            session.add_all(
                HumanTaskRow(**human_task_to_row(item)) for item in self.human_tasks.values()
            )
            session.add_all(ItineraryPlanRow(**plan_to_row(item)) for item in self.plans.values())
            session.add_all(
                ItineraryLegRow(**leg_to_row(plan.id, leg))
                for plan in self.plans.values()
                for leg in plan.legs
            )
            session.add_all(
                AppointmentRow(**appointment_to_row(item)) for item in self.appointments.values()
            )
            session.add_all(
                AppointmentVersionRow(**appointment_version_to_row(item))
                for item in self.appointment_versions
            )
            session.add_all(
                CalendarBindingRow(**calendar_binding_to_row(item))
                for item in self.calendar_bindings.values()
            )
            session.add_all(
                CalendarConflictRow(**calendar_conflict_to_row(item))
                for item in self.calendar_conflicts.values()
            )
            session.add_all(
                ConversationThreadRow(**conversation_to_row(item))
                for item in self.conversations.values()
            )
            session.add_all(MessageRow(**message_to_row(item)) for item in self.messages.values())
            session.add_all(
                MasterDataChangeRequestRow(**master_data_change_to_row(item))
                for item in self.master_data_changes.values()
            )
            session.add_all(
                DataQualityIssueRow(**data_quality_issue_to_row(item))
                for item in self.data_quality_issues.values()
            )
            session.commit()

    def close(self) -> None:
        self.engine.dispose()


def clear_tables(session: Session) -> None:
    for row_type in (
        DataQualityIssueRow,
        MasterDataChangeRequestRow,
        MessageRow,
        ConversationThreadRow,
        CalendarConflictRow,
        CalendarBindingRow,
        AppointmentVersionRow,
        AppointmentRow,
        ItineraryLegRow,
        ItineraryPlanRow,
        HumanTaskRow,
        OutboxJobRow,
        AuditEventRow,
        ApprovalRequestRow,
        AvailabilityTokenRow,
        AvailabilityWindowRow,
        AgentSessionRow,
        IdempotencyRecordRow,
        RequirementRevisionRow,
        VisitRequirementRow,
        ContactAssignmentRow,
        ContactRow,
        SupplierSiteRow,
        SupplierRow,
    ):
        session.execute(delete(row_type))


def supplier_to_row(item: Supplier) -> dict[str, Any]:
    return {
        "id": item.id,
        "erp_id": item.erp_id,
        "legal_name": item.legal_name,
        "display_name": item.display_name,
        "aliases": item.aliases,
        "status": item.status,
        "source_system": item.source_system,
        "version": item.version,
    }


def supplier_from_row(row: SupplierRow) -> Supplier:
    return Supplier(
        id=row.id,
        erp_id=row.erp_id,
        legal_name=row.legal_name,
        display_name=row.display_name,
        aliases=row.aliases,
        status=row.status,
        source_system=row.source_system,
        version=row.version,
    )


def site_to_row(item: SupplierSite) -> dict[str, Any]:
    return {
        "id": item.id,
        "supplier_id": item.supplier_id,
        "site_type": item.site_type,
        "name": item.name,
        "raw_address": item.raw_address,
        "normalized_address": item.normalized_address,
        "latitude": item.latitude,
        "longitude": item.longitude,
        "geocode_status": item.geocode_status,
        "visitor_entrance": item.visitor_entrance,
        "parking_note": item.parking_note,
        "reception_hours": item.reception_hours,
        "verified_at": item.verified_at,
        "is_temporary": item.is_temporary,
    }


def site_from_row(row: SupplierSiteRow) -> SupplierSite:
    return SupplierSite(
        id=row.id,
        supplier_id=row.supplier_id,
        site_type=row.site_type,
        name=row.name,
        raw_address=row.raw_address,
        normalized_address=row.normalized_address,
        latitude=row.latitude,
        longitude=row.longitude,
        geocode_status=row.geocode_status,
        visitor_entrance=row.visitor_entrance,
        parking_note=row.parking_note,
        reception_hours=row.reception_hours,
        verified_at=row.verified_at,
        is_temporary=row.is_temporary,
    )


def contact_to_row(item: Contact) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "emails": item.emails,
        "phones": item.phones,
        "language": item.language,
        "status": item.status,
        "last_verified_at": item.last_verified_at,
    }


def contact_from_row(row: ContactRow) -> Contact:
    return Contact(
        id=row.id,
        name=row.name,
        emails=row.emails,
        phones=row.phones,
        language=row.language,
        status=row.status,
        last_verified_at=row.last_verified_at,
    )


def assignment_to_row(item: ContactAssignment) -> dict[str, Any]:
    return {
        "contact_id": item.contact_id,
        "supplier_id": item.supplier_id,
        "site_id": item.site_id,
        "role": item.role,
        "business_scope": item.business_scope,
        "can_confirm_appointment": item.can_confirm_appointment,
        "is_primary": item.is_primary,
        "valid_from": item.valid_from,
        "valid_to": item.valid_to,
    }


def assignment_from_row(row: ContactAssignmentRow) -> ContactAssignment:
    return ContactAssignment(
        contact_id=row.contact_id,
        supplier_id=row.supplier_id,
        site_id=row.site_id,
        role=row.role,
        business_scope=row.business_scope,
        can_confirm_appointment=row.can_confirm_appointment,
        is_primary=row.is_primary,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def draft_to_json(draft: VisitRequirementDraft) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(draft.model_dump()))


def draft_from_json(data: dict[str, Any]) -> VisitRequirementDraft:
    values = dict(data)
    for key in ("date_start", "date_end", "return_deadline"):
        values[key] = parse_dt(values.get(key))
    return VisitRequirementDraft(**values)


def requirement_to_row(item: VisitRequirement) -> dict[str, Any]:
    return {
        "id": item.id,
        "supplier_id": item.draft.supplier_id,
        "site_id": item.draft.site_id,
        "draft": draft_to_json(item.draft),
        "status": item.status,
        "version": item.version,
        "locked_level": item.locked_level,
        "paused_at": item.paused_at,
        "deleted_at": item.deleted_at,
    }


def requirement_from_row(row: VisitRequirementRow) -> VisitRequirement:
    return VisitRequirement(
        id=row.id,
        draft=draft_from_json(row.draft),
        status=row.status
        if isinstance(row.status, RequirementStatus)
        else RequirementStatus(row.status),
        version=row.version,
        locked_level=row.locked_level,
        paused_at=row.paused_at,
        deleted_at=row.deleted_at,
    )


def revision_to_row(item: RequirementRevision) -> dict[str, Any]:
    return {
        "id": item.id,
        "requirement_id": item.requirement_id,
        "diff": item.diff,
        "source": item.source,
        "actor": item.actor,
        "created_at": item.created_at,
    }


def revision_from_row(row: RequirementRevisionRow) -> RequirementRevision:
    return RequirementRevision(
        id=row.id,
        requirement_id=row.requirement_id,
        diff=row.diff,
        source=row.source,
        actor=row.actor,
        created_at=row.created_at,
    )


def availability_to_row(item: AvailabilityWindow) -> dict[str, Any]:
    return {
        "id": item.id,
        "requirement_id": item.requirement_id,
        "source": item.source,
        "participant": item.participant,
        "start": item.start,
        "end": item.end,
        "timezone_name": item.timezone_name,
        "preference": item.preference,
    }


def availability_from_row(row: AvailabilityWindowRow) -> AvailabilityWindow:
    return AvailabilityWindow(
        id=row.id,
        requirement_id=row.requirement_id,
        source=row.source,
        participant=row.participant,
        start=row.start,
        end=row.end,
        timezone_name=row.timezone_name,
        preference=row.preference,
    )


def token_to_row(item: AvailabilityToken) -> dict[str, Any]:
    return {
        "id": item.id,
        "requirement_id": item.requirement_id,
        "token_hash": item.token_hash,
        "expires_at": item.expires_at,
        "revoked_at": item.revoked_at,
        "submitted_at": item.submitted_at,
    }


def token_from_row(row: AvailabilityTokenRow) -> AvailabilityToken:
    return AvailabilityToken(
        id=row.id,
        requirement_id=row.requirement_id,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        submitted_at=row.submitted_at,
    )


def approval_to_row(item: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "action": item.action,
        "risk": item.risk,
        "impact_preview": item.impact_preview,
        "approver": item.approver,
        "status": item.status,
    }


def approval_from_row(row: ApprovalRequestRow) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        action=row.action,
        risk=row.risk,
        impact_preview=row.impact_preview,
        approver=row.approver,
        status=row.status,
    )


def audit_to_row(item: AuditEvent) -> dict[str, Any]:
    return {
        "actor": item.actor,
        "action": item.action,
        "entity": item.entity,
        "entity_id": item.entity_id,
        "before": jsonable_encoder(item.before),
        "after": jsonable_encoder(item.after),
        "correlation_id": item.correlation_id,
        "created_at": item.created_at,
    }


def audit_from_row(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        actor=row.actor,
        action=row.action,
        entity=row.entity,
        entity_id=row.entity_id,
        before=row.before,
        after=row.after,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
    )


def outbox_to_row(item: OutboxJob) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "payload": jsonable_encoder(item.payload),
        "idempotency_key": item.idempotency_key,
        "status": item.status,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "available_at": item.available_at,
        "locked_at": item.locked_at,
        "completed_at": item.completed_at,
        "last_error": item.last_error,
        "created_at": item.created_at,
    }


def outbox_from_row(row: OutboxJobRow) -> OutboxJob:
    return OutboxJob(
        id=row.id,
        kind=row.kind,
        payload=row.payload,
        idempotency_key=row.idempotency_key,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        available_at=row.available_at,
        locked_at=row.locked_at,
        completed_at=row.completed_at,
        last_error=row.last_error,
        created_at=row.created_at,
    )


def human_task_to_row(item: HumanTask) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "title": item.title,
        "detail": item.detail,
        "idempotency_key": item.idempotency_key,
        "status": item.status,
        "created_at": item.created_at,
    }


def human_task_from_row(row: HumanTaskRow) -> HumanTask:
    return HumanTask(
        id=row.id,
        kind=row.kind,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        title=row.title,
        detail=row.detail,
        idempotency_key=row.idempotency_key,
        status=row.status,
        created_at=row.created_at,
    )


def plan_to_row(item: ItineraryPlan) -> dict[str, Any]:
    return {
        "id": item.id,
        "requirement_ids": item.requirement_ids,
        "objective": item.objective,
        "solver": item.solver,
        "variant": item.variant,
        "status": item.status,
        "total_travel_minutes": item.total_travel_minutes,
        "total_wait_minutes": item.total_wait_minutes,
        "total_buffer_minutes": item.total_buffer_minutes,
        "changed_appointments": item.changed_appointments,
        "return_margin_minutes": item.return_margin_minutes,
        "unassigned": item.unassigned,
        "explanation_codes": item.explanation_codes,
        "accepted_at": item.accepted_at,
        "alternative_plan_id": item.alternative_plan_id,
        "created_at": item.created_at,
    }


def itinerary_plan_from_row(
    row: ItineraryPlanRow,
    legs: list[ItineraryLeg],
) -> ItineraryPlan:
    return ItineraryPlan(
        id=row.id,
        requirement_ids=row.requirement_ids,
        objective=row.objective,
        solver=row.solver,
        variant=row.variant,
        status=row.status,
        legs=sorted(legs, key=lambda item: item.start),
        total_travel_minutes=row.total_travel_minutes,
        total_wait_minutes=row.total_wait_minutes,
        total_buffer_minutes=row.total_buffer_minutes,
        changed_appointments=row.changed_appointments,
        return_margin_minutes=row.return_margin_minutes,
        unassigned=row.unassigned,
        explanation_codes=row.explanation_codes,
        accepted_at=row.accepted_at,
        alternative_plan_id=row.alternative_plan_id,
        created_at=row.created_at,
    )


def leg_to_row(plan_id: str, item: ItineraryLeg) -> dict[str, Any]:
    return {
        "id": item.id,
        "plan_id": plan_id,
        "requirement_id": item.requirement_id,
        "from_label": item.from_label,
        "to_label": item.to_label,
        "start": item.start,
        "end": item.end,
        "travel_minutes": item.travel_minutes,
        "buffer_minutes": item.buffer_minutes,
        "route_geometry": jsonable_encoder(item.route_geometry),
    }


def itinerary_leg_from_row(row: ItineraryLegRow) -> ItineraryLeg:
    return ItineraryLeg(
        id=row.id,
        requirement_id=row.requirement_id,
        from_label=row.from_label,
        to_label=row.to_label,
        start=row.start,
        end=row.end,
        travel_minutes=row.travel_minutes,
        buffer_minutes=row.buffer_minutes,
        route_geometry=[(float(point[0]), float(point[1])) for point in row.route_geometry],
    )


def appointment_to_row(item: Appointment) -> dict[str, Any]:
    return {
        "id": item.id,
        "requirement_id": item.requirement_id,
        "site_id": item.site_id,
        "start": item.start,
        "end": item.end,
        "participants": item.participants,
        "supplier_confirmation_status": item.supplier_confirmation_status,
        "calendar_external_event_id": item.calendar_external_event_id,
        "status": item.status,
        "created_at": item.created_at,
    }


def appointment_from_row(row: AppointmentRow) -> Appointment:
    return Appointment(
        id=row.id,
        requirement_id=row.requirement_id,
        site_id=row.site_id,
        start=row.start,
        end=row.end,
        participants=row.participants,
        supplier_confirmation_status=row.supplier_confirmation_status,
        calendar_external_event_id=row.calendar_external_event_id,
        status=row.status,
        created_at=row.created_at,
    )


def appointment_version_to_row(item: AppointmentVersion) -> dict[str, Any]:
    return {
        "id": item.id,
        "appointment_id": item.appointment_id,
        "before": jsonable_encoder(item.before),
        "after": jsonable_encoder(item.after),
        "reason": item.reason,
        "created_at": item.created_at,
    }


def appointment_version_from_row(row: AppointmentVersionRow) -> AppointmentVersion:
    return AppointmentVersion(
        id=row.id,
        appointment_id=row.appointment_id,
        before=row.before,
        after=row.after,
        reason=row.reason,
        created_at=row.created_at,
    )


def calendar_binding_to_row(item: CalendarBinding) -> dict[str, Any]:
    return {
        "id": item.id,
        "appointment_id": item.appointment_id,
        "provider": item.provider,
        "calendar_id": item.calendar_id,
        "external_event_id": item.external_event_id,
        "etag": item.etag,
        "last_sync_at": item.last_sync_at,
    }


def calendar_binding_from_row(row: CalendarBindingRow) -> CalendarBinding:
    return CalendarBinding(
        id=row.id,
        appointment_id=row.appointment_id,
        provider=row.provider,
        calendar_id=row.calendar_id,
        external_event_id=row.external_event_id,
        etag=row.etag,
        last_sync_at=row.last_sync_at,
    )


def calendar_conflict_to_row(item: CalendarConflict) -> dict[str, Any]:
    return {
        "id": item.id,
        "appointment_id": item.appointment_id,
        "binding_id": item.binding_id,
        "local_snapshot": jsonable_encoder(item.local_snapshot),
        "external_snapshot": jsonable_encoder(item.external_snapshot),
        "reason": item.reason,
        "status": item.status,
        "created_at": item.created_at,
    }


def calendar_conflict_from_row(row: CalendarConflictRow) -> CalendarConflict:
    return CalendarConflict(
        id=row.id,
        appointment_id=row.appointment_id,
        binding_id=row.binding_id,
        local_snapshot=row.local_snapshot,
        external_snapshot=row.external_snapshot,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
    )


def conversation_to_row(item: ConversationThread) -> dict[str, Any]:
    return {
        "id": item.id,
        "channel": item.channel,
        "external_thread_id": item.external_thread_id,
        "requirement_id": item.requirement_id,
        "requirement_version": item.requirement_version,
        "created_at": item.created_at,
    }


def conversation_from_row(row: ConversationThreadRow) -> ConversationThread:
    return ConversationThread(
        id=row.id,
        channel=row.channel,
        external_thread_id=row.external_thread_id,
        requirement_id=row.requirement_id,
        requirement_version=row.requirement_version,
        created_at=row.created_at,
    )


def message_to_row(item: Message) -> dict[str, Any]:
    return {
        "id": item.id,
        "thread_id": item.thread_id,
        "direction": item.direction,
        "body": item.body,
        "send_status": item.send_status,
        "parsed_result": jsonable_encoder(item.parsed_result),
        "created_at": item.created_at,
    }


def message_from_row(row: MessageRow) -> Message:
    return Message(
        id=row.id,
        thread_id=row.thread_id,
        direction=row.direction,
        body=row.body,
        send_status=row.send_status,
        parsed_result=row.parsed_result,
        created_at=row.created_at,
    )


def master_data_change_to_row(item: MasterDataChangeRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "original_value": jsonable_encoder(item.original_value),
        "proposed_value": jsonable_encoder(item.proposed_value),
        "source_message_id": item.source_message_id,
        "risk": item.risk,
        "approval_status": item.approval_status,
    }


def master_data_change_from_row(
    row: MasterDataChangeRequestRow,
) -> MasterDataChangeRequest:
    return MasterDataChangeRequest(
        id=row.id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        original_value=row.original_value,
        proposed_value=row.proposed_value,
        source_message_id=row.source_message_id,
        risk=row.risk,
        approval_status=row.approval_status,
    )


def data_quality_issue_to_row(item: DataQualityIssue) -> dict[str, Any]:
    return {
        "id": item.id,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "issue_type": item.issue_type,
        "detail": item.detail,
        "status": item.status,
    }


def data_quality_issue_from_row(row: DataQualityIssueRow) -> DataQualityIssue:
    return DataQualityIssue(
        id=row.id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        issue_type=row.issue_type,
        detail=row.detail,
        status=row.status,
    )
