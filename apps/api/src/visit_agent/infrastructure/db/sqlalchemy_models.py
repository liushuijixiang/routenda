from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from visit_agent.domain.models import RequirementStatus


def uuid_text() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupplierRow(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    erp_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), index=True)
    source_system: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)

    sites: Mapped[list["SupplierSiteRow"]] = relationship(back_populates="supplier")


class SupplierSiteRow(Base, TimestampMixin):
    __tablename__ = "supplier_sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    site_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    raw_address: Mapped[str] = mapped_column(Text)
    normalized_address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    geocode_status: Mapped[str] = mapped_column(String(40), index=True)
    visitor_entrance: Mapped[str] = mapped_column(String(255), default="")
    parking_note: Mapped[str] = mapped_column(Text, default="")
    reception_hours: Mapped[str] = mapped_column(String(120), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False)

    supplier: Mapped[SupplierRow] = relationship(back_populates="sites")


class ContactRow(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    name: Mapped[str] = mapped_column(String(255), index=True)
    emails: Mapped[list[str]] = mapped_column(JSON, default=list)
    phones: Mapped[list[str]] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(40), default="zh-CN")
    status: Mapped[str] = mapped_column(String(40), index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactAssignmentRow(Base):
    __tablename__ = "contact_assignments"

    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_sites.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(80))
    business_scope: Mapped[str] = mapped_column(String(120), default="visit")
    can_confirm_appointment: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VisitRequirementRow(Base, TimestampMixin):
    __tablename__ = "visit_requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_sites.id"), index=True)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[RequirementStatus] = mapped_column(Enum(RequirementStatus), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    locked_level: Mapped[str] = mapped_column(String(40), default="none")
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequirementRevisionRow(Base, TimestampMixin):
    __tablename__ = "requirement_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("visit_requirements.id"), index=True)
    diff: Mapped[dict[str, Any]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(120))


class AgentSessionRow(Base, TimestampMixin):
    __tablename__ = "agent_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)


class IdempotencyRecordRow(Base, TimestampMixin):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    response: Mapped[Any] = mapped_column(JSON, nullable=False)


class AvailabilityWindowRow(Base, TimestampMixin):
    __tablename__ = "availability_windows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("visit_requirements.id"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    participant: Mapped[str] = mapped_column(String(255))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone_name: Mapped[str] = mapped_column(String(80))
    preference: Mapped[int] = mapped_column(Integer, default=3)


class AvailabilityTokenRow(Base, TimestampMixin):
    __tablename__ = "availability_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("visit_requirements.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppointmentRow(Base, TimestampMixin):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("visit_requirements.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_sites.id"))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    participants: Mapped[list[str]] = mapped_column(JSON, default=list)
    supplier_confirmation_status: Mapped[str] = mapped_column(String(80))
    calendar_external_event_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40), index=True)


class AppointmentVersionRow(Base):
    __tablename__ = "appointment_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), index=True)
    before: Mapped[dict[str, Any]] = mapped_column(JSON)
    after: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalendarBindingRow(Base, TimestampMixin):
    __tablename__ = "calendar_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    calendar_id: Mapped[str] = mapped_column(String(255))
    external_event_id: Mapped[str] = mapped_column(String(255), index=True)
    etag: Mapped[str] = mapped_column(String(255))
    last_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CalendarConflictRow(Base):
    __tablename__ = "calendar_conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), index=True)
    binding_id: Mapped[str | None] = mapped_column(ForeignKey("calendar_bindings.id"))
    local_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    external_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationThreadRow(Base):
    __tablename__ = "conversation_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    channel: Mapped[str] = mapped_column(String(80), index=True)
    external_thread_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    requirement_id: Mapped[str | None] = mapped_column(ForeignKey("visit_requirements.id"))
    requirement_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    thread_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"), index=True)
    direction: Mapped[str] = mapped_column(String(40), index=True)
    body: Mapped[str] = mapped_column(Text)
    send_status: Mapped[str] = mapped_column(String(40), index=True)
    parsed_result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItineraryPlanRow(Base):
    __tablename__ = "itinerary_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    requirement_ids: Mapped[list[str]] = mapped_column(JSON)
    objective: Mapped[str] = mapped_column(Text)
    solver: Mapped[str] = mapped_column(String(80))
    variant: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    total_travel_minutes: Mapped[int] = mapped_column(Integer)
    total_wait_minutes: Mapped[int] = mapped_column(Integer)
    total_buffer_minutes: Mapped[int] = mapped_column(Integer)
    changed_appointments: Mapped[int] = mapped_column(Integer)
    return_margin_minutes: Mapped[int | None] = mapped_column(Integer)
    unassigned: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    explanation_codes: Mapped[list[str]] = mapped_column(JSON)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alternative_plan_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItineraryLegRow(Base):
    __tablename__ = "itinerary_legs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    plan_id: Mapped[str] = mapped_column(ForeignKey("itinerary_plans.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("visit_requirements.id"), index=True)
    from_label: Mapped[str] = mapped_column(String(255))
    to_label: Mapped[str] = mapped_column(String(255))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    travel_minutes: Mapped[int] = mapped_column(Integer)
    buffer_minutes: Mapped[int] = mapped_column(Integer)
    route_geometry: Mapped[list[list[float]]] = mapped_column(JSON)


class MasterDataChangeRequestRow(Base, TimestampMixin):
    __tablename__ = "master_data_change_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    original_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    risk: Mapped[str] = mapped_column(String(40), index=True)
    approval_status: Mapped[str] = mapped_column(String(40), index=True)


class DataQualityIssueRow(Base, TimestampMixin):
    __tablename__ = "data_quality_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    issue_type: Mapped[str] = mapped_column(String(80), index=True)
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)


class ApprovalRequestRow(Base, TimestampMixin):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    action: Mapped[str] = mapped_column(String(120), index=True)
    risk: Mapped[str] = mapped_column(String(40), index=True)
    impact_preview: Mapped[dict[str, Any]] = mapped_column(JSON)
    approver: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(160), index=True)
    entity: Mapped[str] = mapped_column(String(120), index=True)
    entity_id: Mapped[str] = mapped_column(String(120), index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class OutboxJobRow(Base, TimestampMixin):
    __tablename__ = "outbox_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class HumanTaskRow(Base, TimestampMixin):
    __tablename__ = "human_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_text)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
