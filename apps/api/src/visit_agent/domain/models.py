from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import asin, cos, radians, sin, sqrt
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


UTC = timezone.utc


def new_id() -> str:
    return str(uuid4())


class RequirementStatus(str, Enum):
    DRAFT = "DRAFT"
    NEED_MORE_INFORMATION = "NEED_MORE_INFORMATION"
    READY_TO_CONTACT = "READY_TO_CONTACT"
    CONTACTED = "CONTACTED"
    WAITING_REPLY = "WAITING_REPLY"
    CANDIDATES_RECEIVED = "CANDIDATES_RECEIVED"
    INTERNAL_APPROVAL = "INTERNAL_APPROVAL"
    TENTATIVE_HOLD = "TENTATIVE_HOLD"
    CONFIRMED = "CONFIRMED"
    RESCHEDULE_REQUESTED = "RESCHEDULE_REQUESTED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS = {
    RequirementStatus.DRAFT: {
        RequirementStatus.NEED_MORE_INFORMATION,
        RequirementStatus.READY_TO_CONTACT,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.NEED_MORE_INFORMATION: {
        RequirementStatus.READY_TO_CONTACT,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.READY_TO_CONTACT: {RequirementStatus.CONTACTED, RequirementStatus.CANCELLED},
    RequirementStatus.CONTACTED: {RequirementStatus.WAITING_REPLY, RequirementStatus.CANCELLED},
    RequirementStatus.WAITING_REPLY: {
        RequirementStatus.CANDIDATES_RECEIVED,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.CANDIDATES_RECEIVED: {
        RequirementStatus.INTERNAL_APPROVAL,
        RequirementStatus.RESCHEDULE_REQUESTED,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.INTERNAL_APPROVAL: {
        RequirementStatus.TENTATIVE_HOLD,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.TENTATIVE_HOLD: {
        RequirementStatus.CONFIRMED,
        RequirementStatus.RESCHEDULE_REQUESTED,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.CONFIRMED: {
        RequirementStatus.RESCHEDULE_REQUESTED,
        RequirementStatus.CANCELLATION_REQUESTED,
        RequirementStatus.COMPLETED,
    },
    RequirementStatus.RESCHEDULE_REQUESTED: {
        RequirementStatus.CANDIDATES_RECEIVED,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.CANCELLATION_REQUESTED: {RequirementStatus.CANCELLED},
    RequirementStatus.CANCELLED: {
        RequirementStatus.NEED_MORE_INFORMATION,
        RequirementStatus.READY_TO_CONTACT,
    },
}


def transition_status(current: RequirementStatus, target: RequirementStatus) -> RequirementStatus:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal transition {current.value}->{target.value}")
    return target


@dataclass
class Supplier:
    erp_id: str
    legal_name: str
    display_name: str
    aliases: list[str]
    status: str = "active"
    source_system: str = "mock"
    version: int = 1
    id: str = field(default_factory=new_id)


@dataclass
class SupplierSite:
    supplier_id: str
    name: str
    raw_address: str
    latitude: float
    longitude: float
    site_type: str = "factory"
    normalized_address: str = ""
    geocode_status: str = "verified"
    visitor_entrance: str = "main gate"
    parking_note: str = ""
    reception_hours: str = "09:00-17:00"
    verified_at: datetime | None = None
    is_temporary: bool = False
    id: str = field(default_factory=new_id)


@dataclass
class Contact:
    name: str
    emails: list[str]
    phones: list[str]
    language: str = "zh-CN"
    status: str = "active"
    last_verified_at: datetime | None = None
    id: str = field(default_factory=new_id)


@dataclass
class ContactAssignment:
    contact_id: str
    supplier_id: str
    site_id: str
    role: str
    business_scope: str = "visit"
    can_confirm_appointment: bool = True
    is_primary: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None


@dataclass
class MasterDataChangeRequest:
    entity_type: str
    entity_id: str
    original_value: dict[str, Any]
    proposed_value: dict[str, Any]
    source_message_id: str | None
    risk: str = "high"
    approval_status: str = "pending"
    id: str = field(default_factory=new_id)


@dataclass
class DataQualityIssue:
    entity_type: str
    entity_id: str
    issue_type: str
    detail: str
    status: str = "open"
    id: str = field(default_factory=new_id)


class VisitRequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    supplier_name: str | None = None
    supplier_id: str | None = None
    site_id: str | None = None
    purpose_category: str | None = None
    date_start: datetime | None = None
    date_end: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    priority: int = Field(default=3, ge=1, le=5)
    required_people: list[str] = Field(default_factory=list)
    origin: str | None = None
    destination: str | None = None
    return_deadline: datetime | None = None
    can_move_existing: bool = False

    def missing_slots(self) -> list[str]:
        missing: list[str] = []
        for name in (
            "supplier_id",
            "site_id",
            "date_start",
            "date_end",
            "duration_minutes",
            "origin",
        ):
            if getattr(self, name) in (None, "", []):
                missing.append(name)
        return missing

    @model_validator(mode="after")
    def validate_window(self) -> "VisitRequirementDraft":
        if self.date_start and self.date_end and self.date_start >= self.date_end:
            raise ValueError("date_start must be before date_end")
        return self


@dataclass
class VisitRequirement:
    draft: VisitRequirementDraft
    status: RequirementStatus = RequirementStatus.DRAFT
    version: int = 1
    locked_level: str = "none"
    id: str = field(default_factory=new_id)
    paused_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class RequirementRevision:
    requirement_id: str
    diff: dict[str, Any]
    source: str
    actor: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class AvailabilityWindow:
    requirement_id: str
    participant: str
    start: datetime
    end: datetime
    timezone_name: str = "Asia/Shanghai"
    source: str = "supplier"
    preference: int = 3
    id: str = field(default_factory=new_id)


@dataclass
class AvailabilityToken:
    requirement_id: str
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    submitted_at: datetime | None = None
    id: str = field(default_factory=new_id)

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return self.revoked_at is None and self.submitted_at is None and self.expires_at > now


@dataclass
class Appointment:
    requirement_id: str
    site_id: str
    start: datetime
    end: datetime
    participants: list[str]
    supplier_confirmation_status: str = "tentative"
    calendar_external_event_id: str | None = None
    status: str = "tentative"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class AppointmentVersion:
    appointment_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class CalendarBinding:
    appointment_id: str
    provider: str
    calendar_id: str
    external_event_id: str
    etag: str
    last_sync_at: datetime
    id: str = field(default_factory=new_id)


@dataclass
class ConversationThread:
    channel: str
    external_thread_id: str
    requirement_id: str | None
    requirement_version: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class Message:
    thread_id: str
    direction: str
    body: str
    send_status: str
    parsed_result: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class CalendarConflict:
    appointment_id: str
    binding_id: str | None
    local_snapshot: dict[str, Any]
    external_snapshot: dict[str, Any]
    reason: str
    status: str = "open"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class ApprovalRequest:
    action: str
    risk: str
    impact_preview: dict[str, Any]
    approver: str
    status: str = "pending"
    id: str = field(default_factory=new_id)


@dataclass
class AuditEvent:
    actor: str
    action: str
    entity: str
    entity_id: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    correlation_id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class OutboxJob:
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 3
    available_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    locked_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class HumanTask:
    kind: str
    entity_type: str
    entity_id: str
    title: str
    detail: str
    idempotency_key: str
    status: str = "open"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=new_id)


@dataclass
class ItineraryLeg:
    requirement_id: str
    from_label: str
    to_label: str
    start: datetime
    end: datetime
    travel_minutes: int
    buffer_minutes: int
    route_geometry: list[tuple[float, float]]
    id: str = field(default_factory=new_id)


@dataclass
class ItineraryPlan:
    objective: str
    solver: str
    legs: list[ItineraryLeg]
    total_travel_minutes: int
    total_wait_minutes: int
    unassigned: list[dict[str, str]]
    explanation_codes: list[str]
    total_buffer_minutes: int = 0
    changed_appointments: int = 0
    return_margin_minutes: int | None = None
    requirement_ids: list[str] = field(default_factory=list)
    variant: str = "recommended"
    status: str = "generated"
    accepted_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    alternative_plan_id: str | None = None
    id: str = field(default_factory=new_id)


def haversine_minutes(
    a: tuple[float, float], b: tuple[float, float], speed_kmh: float = 45.0
) -> int:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    aa = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    km = 2 * r * asin(sqrt(aa))
    return max(10, int((km / speed_kmh) * 60))


def day_window(day_offset: int, hour: int, minute: int = 0) -> datetime:
    local_zone = ZoneInfo("Asia/Shanghai")
    base = datetime(2026, 6, 25, tzinfo=local_zone) + timedelta(days=day_offset)
    return base.replace(hour=hour, minute=minute).astimezone(UTC)
