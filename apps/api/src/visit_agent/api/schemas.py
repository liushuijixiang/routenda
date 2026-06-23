from __future__ import annotations

from datetime import datetime
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from visit_agent.domain.models import (
    RequirementStatus,
    VisitRequirementDraft,
)


VisitRequirementDraftDTO: TypeAlias = VisitRequirementDraft


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResultDTO(BaseModel):
    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str = ""
    retryable: bool = False
    audit_id: str = ""


class SupplierDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    erp_id: str
    legal_name: str
    display_name: str
    aliases: list[str]
    status: str
    source_system: str
    version: int


class SupplierSiteDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    supplier_id: str
    site_type: str
    name: str
    raw_address: str
    normalized_address: str
    latitude: float
    longitude: float
    geocode_status: str
    visitor_entrance: str
    parking_note: str
    reception_hours: str
    is_temporary: bool


class VisitRequirementDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    draft: VisitRequirementDraftDTO
    status: RequirementStatus
    version: int
    locked_level: str
    paused_at: datetime | None = None
    deleted_at: datetime | None = None


class VisitRequirementDraftPatchDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str | None = None
    supplier_id: str | None = None
    site_id: str | None = None
    purpose_category: str | None = None
    date_start: datetime | None = None
    date_end: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    priority: int | None = Field(default=None, ge=1, le=5)
    required_people: list[str] | None = None
    origin: str | None = None
    destination: str | None = None
    return_deadline: datetime | None = None
    can_move_existing: bool | None = None


class UpdateRequirementRequest(BaseModel):
    patch: VisitRequirementDraftPatchDTO
    source: str = Field(default="api", min_length=1, max_length=80)


class RequirementRevisionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    requirement_id: str
    diff: dict[str, Any]
    source: str
    actor: str
    created_at: datetime


class IntakeRequest(BaseModel):
    text: str = Field(min_length=1)


class IntakeResponse(BaseModel):
    session_id: str
    draft: VisitRequirementDraftDTO
    missing_slots: list[str]
    candidates: dict[str, list[dict[str, Any]]]


class ConfirmRequirementRequest(BaseModel):
    session_id: str
    patch: dict[str, Any] = Field(default_factory=dict)


class PlanningRunRequest(BaseModel):
    requirement_ids: list[str] = Field(min_length=1)


class AvailabilityWindowSelectionDTO(BaseModel):
    start: datetime
    end: datetime
    timezone_name: str = Field(default="Asia/Shanghai", min_length=1)
    preference: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def validate_window(self) -> "AvailabilityWindowSelectionDTO":
        if self.start >= self.end:
            raise ValueError("availability start must be before end")
        return self


class PublicAvailabilitySubmitRequest(BaseModel):
    requirement_id: str | None = None
    contact_name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)
    selected_windows: list[AvailabilityWindowSelectionDTO] = Field(default_factory=list)
    none_work: bool = False
    alternative_windows: list[AvailabilityWindowSelectionDTO] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection(self) -> "PublicAvailabilitySubmitRequest":
        if not self.selected_windows and not self.none_work:
            raise ValueError("select at least one window or mark none_work")
        return self


class ApprovalDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    risk: str
    impact_preview: dict[str, Any]
    approver: str
    status: str


class IntegrationHealthDTO(BaseModel):
    erp: str
    calendar: str
    communication: str
    geocoding: str
    routing: str
    llm: str
    search: str
    database: str


class HumanTaskDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    entity_type: str
    entity_id: str
    title: str
    detail: str
    idempotency_key: str
    status: str
    created_at: datetime


class CalendarExternalChangeDTO(BaseModel):
    binding_id: str
    etag: str = Field(min_length=1)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class CalendarSyncRequest(BaseModel):
    external_changes: list[CalendarExternalChangeDTO] = Field(default_factory=list)


class CorrectMessageParseRequest(BaseModel):
    parsed_result: dict[str, Any]


class CsvContentRequest(BaseModel):
    content: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)


class RescheduleAppointmentRequest(BaseModel):
    start: datetime
    end: datetime
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_window(self) -> "RescheduleAppointmentRequest":
        if self.start >= self.end:
            raise ValueError("appointment start must be before end")
        return self
