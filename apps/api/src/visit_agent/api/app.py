from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agent.policy import classify_action
from visit_agent.agent.tools.result import ToolResult
from visit_agent.api.schemas import (
    ApprovalDTO,
    CalendarSyncRequest,
    ConfirmRequirementRequest,
    CorrectMessageParseRequest,
    CsvContentRequest,
    HumanTaskDTO,
    IntakeRequest,
    IntakeResponse,
    IntegrationHealthDTO,
    PlanningRunRequest,
    PublicAvailabilitySubmitRequest,
    RequirementRevisionDTO,
    RescheduleAppointmentRequest,
    SearchRequest,
    SupplierDTO,
    SupplierSiteDTO,
    ToolResultDTO,
    UpdateRequirementRequest,
    VisitRequirementDraftDTO,
    VisitRequirementDTO,
)
from visit_agent.config import settings
from visit_agent.application.reconciliation import (
    create_supplier_change_requests,
    duplicate_supplier_candidates,
    export_suppliers_csv,
    preview_supplier_import,
)
from visit_agent.application.status import transition_requirement
from visit_agent.domain.models import (
    ALLOWED_TRANSITIONS,
    UTC,
    Appointment,
    AppointmentVersion,
    AuditEvent,
    CalendarBinding,
    CalendarConflict,
    ConversationThread,
    MasterDataChangeRequest,
    Message,
    RequirementRevision,
    RequirementStatus,
    VisitRequirement,
    VisitRequirementDraft,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo
from visit_agent.infrastructure.db.sqlalchemy_repository import SQLAlchemyRepository
from visit_agent.infrastructure.adapters.communication import parse_inbound_reply
from visit_agent.infrastructure.adapters.feishu import (
    FeishuOpenPlatformAdapter,
    extract_feishu_message_text,
    format_calendar_summary,
)


def encode_data(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable_encoder(asdict(cast(Any, value)))
    if isinstance(value, list):
        return [encode_data(item) for item in value]
    if isinstance(value, dict):
        return {key: encode_data(item) for key, item in value.items()}
    return jsonable_encoder(value)


def coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("expected ISO datetime")


def dto_result(result: ToolResult) -> ToolResultDTO:
    return ToolResultDTO(
        ok=result.ok,
        data=encode_data(result.data),
        error_code=result.error_code,
        message=result.message,
        retryable=result.retryable,
        audit_id=result.audit_id,
    )


ROLE_ORDER = {
    "requester": 1,
    "coordinator": 2,
    "approver": 3,
    "admin": 4,
}


def require_role(role: str, minimum: str) -> None:
    if ROLE_ORDER.get(role, 0) < ROLE_ORDER[minimum]:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden_role", "message": f"{minimum} role required"},
        )


def mask_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "***"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def mask_phone(value: str) -> str:
    if len(value) < 7:
        return "***"
    return f"{value[:3]}****{value[-4:]}"


def idem_key(route: str, idempotency_key: str | None) -> str | None:
    if not idempotency_key:
        return None
    return f"{route}:{idempotency_key}"


def cached_idempotent(repo: InMemoryRepository, key: str | None) -> Any | None:
    if key and key in repo.idempotency_records:
        return repo.idempotency_records[key]["response"]
    return None


def remember_idempotent(repo: InMemoryRepository, key: str | None, response: Any) -> Any:
    data = encode_data(response)
    if key:
        repo.save_idempotency_record(key, data)
    return data


def validate_requirement_references(
    repo: InMemoryRepository,
    draft: VisitRequirementDraft,
) -> None:
    if draft.supplier_id and draft.supplier_id not in repo.suppliers:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_not_found", "message": "Supplier does not exist"},
        )
    if draft.site_id:
        site = repo.sites.get(draft.site_id)
        if not site:
            raise HTTPException(
                status_code=404,
                detail={"code": "site_not_found", "message": "Site does not exist"},
            )
        if draft.supplier_id and site.supplier_id != draft.supplier_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "site_supplier_mismatch",
                    "message": "Site does not belong to the selected supplier",
                },
            )


def append_requirement_revision(
    repo: InMemoryRepository,
    requirement: VisitRequirement,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    source: str,
    actor: str,
) -> RequirementRevision:
    changed_fields = {
        field: {
            "before": encode_data(before.get(field)) if before else None,
            "after": encode_data(value),
        }
        for field, value in after.items()
        if before is None or before.get(field) != value
    }
    revision = RequirementRevision(
        requirement_id=requirement.id,
        diff={"version": requirement.version, "fields": changed_fields},
        source=source,
        actor=actor,
    )
    repo.revisions.append(revision)
    return revision


def apply_requirement_patch(
    repo: InMemoryRepository,
    requirement: VisitRequirement,
    patch: dict[str, Any],
    source: str,
    actor: str,
) -> VisitRequirement:
    before = requirement.draft.model_dump()
    merged = {**before, **patch}
    validated = VisitRequirementDraftDTO.model_validate(merged)
    updated_draft = validated.model_copy(deep=True)
    validate_requirement_references(repo, updated_draft)
    requirement.draft = updated_draft
    requirement.version += 1
    append_requirement_revision(
        repo,
        requirement,
        before,
        updated_draft.model_dump(),
        source,
        actor,
    )
    return requirement


def public_candidate_windows(requirement: VisitRequirement) -> list[dict[str, Any]]:
    start = requirement.draft.date_start
    end = requirement.draft.date_end
    if start is None or end is None:
        return []
    duration = timedelta(minutes=requirement.draft.duration_minutes or 90)
    anchors = [start + timedelta(hours=1), start + timedelta(days=1, hours=5)]
    candidates: list[dict[str, Any]] = []
    for anchor in anchors:
        candidate_end = anchor + duration
        if anchor >= start and candidate_end <= end:
            candidates.append(
                {
                    "start": anchor,
                    "end": candidate_end,
                    "timezone_name": "Asia/Shanghai",
                    "preference": 3,
                }
            )
    return candidates


def create_app(repo: InMemoryRepository | None = None) -> FastAPI:
    if repo is None and settings.database_url:
        repo = SQLAlchemyRepository(settings.database_url)
    else:
        repo = seed_demo(repo or InMemoryRepository())
    agent = VisitCoordinatorAgent(repo)
    app = FastAPI(title="Routenda API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Role"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "errors": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            detail = {
                "code": str(exc.detail.get("code", "http_error")),
                "message": str(exc.detail.get("message", "Request failed")),
                **{
                    key: value
                    for key, value in exc.detail.items()
                    if key not in {"code", "message"}
                },
            }
        else:
            detail = {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    app.state.repo = repo
    app.state.agent = agent

    @app.get("/api/v1/integrations/health", response_model=IntegrationHealthDTO)
    def integrations_health() -> dict[str, str]:
        erp_status = "mock"
        if settings.erp_provider == "excel" and settings.erp_excel_path:
            erp_status = f"excel:{settings.erp_excel_path}"
        elif (
            settings.erp_next_base_url
            and settings.erp_next_api_key
            and settings.erp_next_api_secret
        ):
            erp_status = "erpnext-configured"
        calendar_status = "mock"
        if (
            settings.calendar_provider == "feishu"
            and settings.feishu_app_id
            and settings.feishu_app_secret
        ):
            calendar_status = "feishu-configured"
        elif (
            settings.microsoft_tenant_id
            and settings.microsoft_client_id
            and settings.microsoft_client_secret
        ):
            calendar_status = "microsoft-graph-configured"
        return {
            "erp": erp_status,
            "calendar": calendar_status,
            "communication": f"smtp:{settings.smtp_host}:{settings.smtp_port}",
            "geocoding": (
                f"nominatim:{settings.nominatim_base_url}"
                if settings.geocoding_provider == "nominatim"
                else "mock"
            ),
            "routing": (
                f"osrm:{settings.osrm_base_url}"
                if settings.routing_provider == "osrm"
                else "haversine-estimate"
            ),
            "llm": "openai-compatible" if settings.openai_api_key else "rule-mode",
            "search": (
                f"serper:{settings.serper_url}"
                if settings.search_provider == "serper" and settings.serper_api_key
                else "disabled"
            ),
            "database": "sqlalchemy" if settings.database_url else "in-memory-demo",
        }

    @app.get("/api/v1/suppliers", response_model=list[SupplierDTO])
    def list_suppliers() -> list[SupplierDTO]:
        return [SupplierDTO.model_validate(item) for item in repo.suppliers.values()]

    @app.post("/api/v1/search")
    async def search(
        request: SearchRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        result = await agent.search.search(request.query)
        if not result.ok:
            raise HTTPException(
                status_code=502 if result.retryable else 400,
                detail={"code": result.error_code, "message": result.message},
            )
        return cast(dict[str, Any], encode_data(result.data))

    @app.get("/api/v1/reconciliation/suppliers/export")
    def export_supplier_reconciliation(
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> Response:
        require_role(x_role, "requester")
        return Response(
            content=export_suppliers_csv(repo),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="supplier-reconciliation.csv"'},
        )

    @app.post("/api/v1/reconciliation/suppliers/import-preview")
    def preview_supplier_reconciliation(
        request: CsvContentRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        return preview_supplier_import(repo, request.content)

    @app.post("/api/v1/reconciliation/suppliers/import-apply")
    def apply_supplier_reconciliation(
        request: CsvContentRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        key = idem_key("reconciliation:suppliers:apply", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        preview, created = create_supplier_change_requests(repo, request.content)
        if not preview["valid"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "csv_import_invalid",
                    "message": "CSV import contains errors",
                    "errors": preview["errors"],
                },
            )
        result = {
            "preview": preview,
            "change_requests": encode_data(created),
            "direct_updates": 0,
        }
        return cast(dict[str, Any], remember_idempotent(repo, key, result))

    @app.get("/api/v1/reconciliation/suppliers/duplicate-candidates")
    def supplier_duplicate_candidates(
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> list[dict[str, Any]]:
        require_role(x_role, "coordinator")
        return duplicate_supplier_candidates(repo)

    @app.get("/api/v1/suppliers/{supplier_id}/sites", response_model=list[SupplierSiteDTO])
    def list_supplier_sites(supplier_id: str) -> list[SupplierSiteDTO]:
        if supplier_id not in repo.suppliers:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_not_found", "message": "Supplier does not exist"},
            )
        return [
            SupplierSiteDTO.model_validate(item)
            for item in repo.sites.values()
            if item.supplier_id == supplier_id
        ]

    @app.get("/api/v1/suppliers/{supplier_id}/contacts")
    def list_supplier_contacts(
        supplier_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> list[dict[str, Any]]:
        if supplier_id not in repo.suppliers:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_not_found", "message": "Supplier does not exist"},
            )
        contact_ids = [
            item.contact_id for item in repo.assignments if item.supplier_id == supplier_id
        ]
        result = [cast(dict[str, Any], encode_data(repo.contacts[item])) for item in contact_ids]
        if ROLE_ORDER.get(x_role, 0) < ROLE_ORDER["coordinator"]:
            for contact in result:
                contact["emails"] = [mask_email(str(value)) for value in contact["emails"]]
                contact["phones"] = [mask_phone(str(value)) for value in contact["phones"]]
        return result

    @app.get("/api/v1/suppliers/{supplier_id}/timeline")
    def supplier_timeline(supplier_id: str) -> list[dict[str, Any]]:
        if supplier_id not in repo.suppliers:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_not_found", "message": "Supplier does not exist"},
            )
        requirement_ids = [
            item.id for item in repo.requirements.values() if item.draft.supplier_id == supplier_id
        ]
        return [
            encode_data(item)
            for item in repo.audit
            if item.entity_id in requirement_ids or item.entity_id == supplier_id
        ]

    @app.get("/api/v1/data-quality")
    def data_quality() -> list[dict[str, str]]:
        return cast(
            list[dict[str, str]],
            encode_data(list(repo.data_quality_issues.values())),
        )

    @app.get("/api/v1/requirements", response_model=list[VisitRequirementDTO])
    def list_requirements(include_deleted: bool = False) -> list[VisitRequirementDTO]:
        return [
            VisitRequirementDTO.model_validate(item)
            for item in repo.requirements.values()
            if include_deleted or item.deleted_at is None
        ]

    @app.post("/api/v1/requirements", response_model=VisitRequirementDTO)
    def create_requirement(
        request: VisitRequirementDraftDTO,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> VisitRequirementDTO:
        require_role(x_role, "requester")
        key = idem_key("requirements:create", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return VisitRequirementDTO.model_validate(cached)
        draft = request.model_copy(deep=True)
        validate_requirement_references(repo, draft)
        status = (
            RequirementStatus.NEED_MORE_INFORMATION
            if draft.missing_slots()
            else RequirementStatus.READY_TO_CONTACT
        )
        requirement = VisitRequirement(draft=draft, status=status)
        repo.requirements[requirement.id] = requirement
        append_requirement_revision(
            repo,
            requirement,
            None,
            draft.model_dump(),
            "structured_form",
            "requester",
        )
        repo.audit_event(
            AuditEvent(
                actor="requester",
                action="create_requirement",
                entity="VisitRequirement",
                entity_id=requirement.id,
                after={"status": requirement.status.value, "version": requirement.version},
            )
        )
        return VisitRequirementDTO.model_validate(remember_idempotent(repo, key, requirement))

    @app.get("/api/v1/requirements/{requirement_id}", response_model=VisitRequirementDTO)
    def get_requirement(requirement_id: str) -> VisitRequirementDTO:
        req = repo.requirements.get(requirement_id)
        if not req:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirement_not_found", "message": "Requirement does not exist"},
            )
        return VisitRequirementDTO.model_validate(req)

    @app.patch(
        "/api/v1/requirements/{requirement_id}",
        response_model=VisitRequirementDTO | ApprovalDTO,
    )
    def update_requirement(
        requirement_id: str,
        request: UpdateRequirementRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> VisitRequirementDTO | ApprovalDTO:
        require_role(x_role, "requester")
        key = idem_key(f"requirements:{requirement_id}:update", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            if cached.get("action"):
                return ApprovalDTO.model_validate(cached)
            return VisitRequirementDTO.model_validate(cached)
        requirement = repo.requirements.get(requirement_id)
        if not requirement:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "requirement_not_found",
                    "message": "Requirement does not exist",
                },
            )
        if requirement.deleted_at is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "requirement_deleted",
                    "message": "Deleted requirements cannot be modified",
                },
            )
        patch = request.patch.model_dump(exclude_unset=True)
        if not patch:
            return VisitRequirementDTO.model_validate(requirement)
        high_risk_fields = {
            "site_id",
            "date_start",
            "date_end",
            "duration_minutes",
            "required_people",
            "return_deadline",
        }
        if requirement.status == RequirementStatus.CONFIRMED and (high_risk_fields & patch.keys()):
            before = cast(dict[str, Any], encode_data(requirement.draft.model_dump()))
            after = {**before, **cast(dict[str, Any], encode_data(patch))}
            approval = agent.request_high_risk(
                "modify_confirmed_requirement",
                requirement.id,
                {
                    "requirement_id": requirement.id,
                    "before": before,
                    "after": after,
                    "patch": encode_data(patch),
                    "source": request.source,
                    "impact": agent.solver.impact_preview(requirement.id, patch),
                },
            )
            return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))
        before = cast(dict[str, Any], encode_data(requirement.draft.model_dump()))
        apply_requirement_patch(repo, requirement, patch, request.source, x_role)
        repo.audit_event(
            AuditEvent(
                actor=x_role,
                action="update_requirement",
                entity="VisitRequirement",
                entity_id=requirement.id,
                before=before,
                after={
                    "draft": encode_data(requirement.draft.model_dump()),
                    "version": requirement.version,
                },
            )
        )
        return VisitRequirementDTO.model_validate(remember_idempotent(repo, key, requirement))

    @app.delete(
        "/api/v1/requirements/{requirement_id}",
        response_model=VisitRequirementDTO | ApprovalDTO,
    )
    def delete_requirement(
        requirement_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> VisitRequirementDTO | ApprovalDTO:
        require_role(x_role, "requester")
        key = idem_key(f"requirements:{requirement_id}:delete", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            if cached.get("action"):
                return ApprovalDTO.model_validate(cached)
            return VisitRequirementDTO.model_validate(cached)
        requirement = repo.requirements.get(requirement_id)
        if not requirement:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "requirement_not_found",
                    "message": "Requirement does not exist",
                },
            )
        if requirement.status == RequirementStatus.CONFIRMED:
            appointment = next(
                (
                    item
                    for item in repo.appointments.values()
                    if item.requirement_id == requirement.id and item.status != "cancelled"
                ),
                None,
            )
            if appointment is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "active_appointment_not_found",
                        "message": "Confirmed requirement has no active appointment",
                    },
                )
            approval = agent.request_high_risk(
                "cancel_confirmed_appointment",
                requirement.id,
                {
                    "appointment_id": appointment.id,
                    "requirement_id": requirement.id,
                    "soft_delete": True,
                    "before": {"status": requirement.status.value, "deleted_at": None},
                    "after": {
                        "status": RequirementStatus.CANCELLATION_REQUESTED.value,
                        "deleted_at": "pending_approval",
                    },
                },
            )
            return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))
        if requirement.deleted_at is None:
            before_status = requirement.status
            requirement.deleted_at = datetime.now(UTC)
            if RequirementStatus.CANCELLED in ALLOWED_TRANSITIONS.get(requirement.status, set()):
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.CANCELLED,
                    actor=x_role,
                    reason="soft_delete",
                )
            requirement.version += 1
            append_requirement_revision(
                repo,
                requirement,
                {"deleted_at": None, "status": before_status.value},
                {
                    "deleted_at": requirement.deleted_at,
                    "status": requirement.status.value,
                },
                "soft_delete",
                x_role,
            )
            repo.audit_event(
                AuditEvent(
                    actor=x_role,
                    action="soft_delete_requirement",
                    entity="VisitRequirement",
                    entity_id=requirement.id,
                    before={"status": before_status.value, "deleted_at": None},
                    after={
                        "status": requirement.status.value,
                        "deleted_at": requirement.deleted_at.isoformat(),
                    },
                )
            )
        return VisitRequirementDTO.model_validate(remember_idempotent(repo, key, requirement))

    @app.get(
        "/api/v1/requirements/{requirement_id}/revisions",
        response_model=list[RequirementRevisionDTO],
    )
    def list_requirement_revisions(
        requirement_id: str,
    ) -> list[RequirementRevisionDTO]:
        if requirement_id not in repo.requirements:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirement_not_found", "message": "Requirement does not exist"},
            )
        return [
            RequirementRevisionDTO.model_validate(item)
            for item in repo.revisions
            if item.requirement_id == requirement_id
        ]

    @app.post(
        "/api/v1/requirements/{requirement_id}/pause",
        response_model=VisitRequirementDTO,
    )
    def pause_requirement(
        requirement_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> VisitRequirementDTO:
        require_role(x_role, "requester")
        key = idem_key(f"requirements:{requirement_id}:pause", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return VisitRequirementDTO.model_validate(cached)
        requirement = repo.requirements.get(requirement_id)
        if not requirement:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "requirement_not_found",
                    "message": "Requirement does not exist",
                },
            )
        if requirement.deleted_at is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "requirement_deleted",
                    "message": "Deleted requirements cannot be paused",
                },
            )
        if requirement.paused_at is None:
            requirement.paused_at = datetime.now(UTC)
            requirement.version += 1
            append_requirement_revision(
                repo,
                requirement,
                {"paused_at": None},
                {"paused_at": requirement.paused_at},
                "pause",
                x_role,
            )
            repo.audit_event(
                AuditEvent(
                    actor=x_role,
                    action="pause_requirement",
                    entity="VisitRequirement",
                    entity_id=requirement.id,
                    after={"paused_at": requirement.paused_at.isoformat()},
                )
            )
        return VisitRequirementDTO.model_validate(remember_idempotent(repo, key, requirement))

    @app.post(
        "/api/v1/requirements/{requirement_id}/contact",
        response_model=ToolResultDTO,
    )
    async def contact_requirement_supplier(
        requirement_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ToolResultDTO:
        require_role(x_role, "coordinator")
        key = idem_key(f"requirements:{requirement_id}:contact", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ToolResultDTO.model_validate(cached)
        if requirement_id not in repo.requirements:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "requirement_not_found",
                    "message": "Requirement does not exist",
                },
            )
        response = dto_result(await agent.contact_supplier(requirement_id))
        return ToolResultDTO.model_validate(remember_idempotent(repo, key, response))

    @app.post("/api/v1/requirements/{requirement_id}/cancel", response_model=ApprovalDTO)
    def cancel_requirement(
        requirement_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDTO:
        require_role(x_role, "approver")
        key = idem_key(f"requirements:{requirement_id}:cancel", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ApprovalDTO.model_validate(cached)
        req = repo.requirements.get(requirement_id)
        if not req:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirement_not_found", "message": "Requirement does not exist"},
            )
        appointment = next(
            (
                item
                for item in repo.appointments.values()
                if item.requirement_id == requirement_id and item.status != "cancelled"
            ),
            None,
        )
        if appointment is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "active_appointment_not_found",
                    "message": "No active appointment exists for this requirement",
                },
            )
        approval = agent.request_high_risk(
            "cancel_confirmed_appointment",
            requirement_id,
            {
                "appointment_id": appointment.id,
                "requirement_id": requirement_id,
                "before": {"status": req.status.value},
                "after": {"status": RequirementStatus.CANCELLED.value},
            },
        )
        return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))

    @app.post("/api/v1/requirements/{requirement_id}/resume", response_model=VisitRequirementDTO)
    def resume_requirement(
        requirement_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> VisitRequirementDTO:
        require_role(x_role, "requester")
        key = idem_key(f"requirements:{requirement_id}:resume", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return VisitRequirementDTO.model_validate(cached)
        req = repo.requirements.get(requirement_id)
        if not req:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirement_not_found", "message": "Requirement does not exist"},
            )
        if req.deleted_at is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "requirement_deleted",
                    "message": "Deleted requirements cannot be resumed",
                },
            )
        before = {"paused_at": req.paused_at, "status": req.status.value}
        changed = req.paused_at is not None or req.status == RequirementStatus.CANCELLED
        req.paused_at = None
        if req.status == RequirementStatus.CANCELLED:
            target = (
                RequirementStatus.NEED_MORE_INFORMATION
                if req.draft.missing_slots()
                else RequirementStatus.READY_TO_CONTACT
            )
            transition_requirement(
                repo,
                req,
                target,
                actor=x_role,
                reason="requirement_resumed",
            )
        if changed:
            req.version += 1
            append_requirement_revision(
                repo,
                req,
                before,
                {"paused_at": None, "status": req.status.value},
                "resume",
                x_role,
            )
        repo.audit_event(
            AuditEvent(
                actor="api",
                action="resume_requirement",
                entity="VisitRequirement",
                entity_id=requirement_id,
                after={"status": req.status.value, "paused_at": None},
            )
        )
        return VisitRequirementDTO.model_validate(remember_idempotent(repo, key, req))

    @app.post("/api/v1/requirements/{requirement_id}/impact-preview")
    def impact_preview(requirement_id: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        if requirement_id not in repo.requirements:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirement_not_found", "message": "Requirement does not exist"},
            )
        return agent.solver.impact_preview(requirement_id, patch or {})

    @app.post("/api/v1/agent/intake-sessions", response_model=IntakeResponse)
    def create_intake_session(
        request: IntakeRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> IntakeResponse:
        require_role(x_role, "requester")
        key = idem_key("agent:intake", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return IntakeResponse.model_validate(cached)
        result = agent.intake(request.text)
        if not result.ok:
            raise HTTPException(
                status_code=400, detail={"code": result.error_code, "message": result.message}
            )
        return IntakeResponse.model_validate(remember_idempotent(repo, key, result.data))

    @app.post("/api/v1/agent/confirm", response_model=VisitRequirementDTO)
    def confirm_requirement(
        request: ConfirmRequirementRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> VisitRequirementDTO:
        require_role(x_role, "requester")
        key = idem_key("agent:confirm", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return VisitRequirementDTO.model_validate(cached)
        result = agent.confirm_requirement(request.session_id, request.patch)
        if not result.ok:
            raise HTTPException(
                status_code=400, detail={"code": result.error_code, "message": result.message}
            )
        repo.audit_event(
            AuditEvent(
                actor="api",
                action="confirm_requirement",
                entity="VisitRequirement",
                entity_id=result.data.id,
                after={"idempotency_key": idempotency_key},
            )
        )
        return VisitRequirementDTO.model_validate(remember_idempotent(repo, key, result.data))

    @app.post("/api/v1/planning/run", response_model=ToolResultDTO)
    async def run_planning(
        request: PlanningRunRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ToolResultDTO:
        require_role(x_role, "requester")
        key = idem_key("planning:run", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ToolResultDTO.model_validate(cached)
        missing = [rid for rid in request.requirement_ids if rid not in repo.requirements]
        if missing:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirements_not_found", "message": ",".join(missing)},
            )
        response = dto_result(await agent.plan_with_routes(request.requirement_ids))
        return ToolResultDTO.model_validate(remember_idempotent(repo, key, response))

    @app.get("/api/v1/planning/{plan_id}/result")
    def planning_result(plan_id: str) -> dict[str, Any]:
        plan = repo.plans.get(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "plan_not_found", "message": "Itinerary plan does not exist"},
            )
        return cast(dict[str, Any], encode_data(plan))

    @app.post("/api/v1/planning/{plan_id}/accept")
    async def accept_plan(
        plan_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        key = idem_key(f"planning:{plan_id}:accept", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        plan = repo.plans.get(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "plan_not_found", "message": "Itinerary plan does not exist"},
            )
        if plan.status != "generated":
            raise HTTPException(
                status_code=409,
                detail={"code": "plan_not_acceptable", "message": "Plan is no longer generated"},
            )
        appointment_ids: list[str] = []
        for leg in plan.legs:
            requirement = repo.requirements[leg.requirement_id]
            if not requirement.draft.site_id:
                continue
            existing = next(
                (
                    item
                    for item in repo.appointments.values()
                    if item.requirement_id == requirement.id and item.status != "cancelled"
                ),
                None,
            )
            if existing:
                before = cast(dict[str, Any], encode_data(existing))
                existing.start = leg.start
                existing.end = leg.end
                existing.participants = requirement.draft.required_people
                repo.add_appointment_version(
                    AppointmentVersion(
                        appointment_id=existing.id,
                        before=before,
                        after=cast(dict[str, Any], encode_data(existing)),
                        reason="accepted_itinerary_plan",
                    )
                )
                appointment = existing
            else:
                appointment = Appointment(
                    requirement_id=requirement.id,
                    site_id=requirement.draft.site_id,
                    start=leg.start,
                    end=leg.end,
                    participants=requirement.draft.required_people,
                )
            hold = await agent.calendar.create_tentative_hold(appointment)
            if not hold.ok:
                appointment.status = "calendar_sync_failed"
                repo.save_appointment(appointment)
                raise HTTPException(
                    status_code=502,
                    detail={"code": hold.error_code, "message": hold.message},
                )
            appointment.calendar_external_event_id = str(hold.data.get("external_event_id", ""))
            repo.save_appointment(appointment)
            binding = next(
                (
                    item
                    for item in repo.calendar_bindings.values()
                    if item.appointment_id == appointment.id
                ),
                None,
            )
            if binding:
                binding.external_event_id = appointment.calendar_external_event_id
                binding.etag = str(hold.data.get("etag", binding.etag))
                binding.last_sync_at = datetime.now(UTC)
            else:
                binding = CalendarBinding(
                    appointment_id=appointment.id,
                    provider=type(agent.calendar).__name__,
                    calendar_id="primary",
                    external_event_id=appointment.calendar_external_event_id,
                    etag=str(hold.data.get("etag", "")),
                    last_sync_at=datetime.now(UTC),
                )
            repo.save_calendar_binding(binding)
            appointment_ids.append(appointment.id)
            if requirement.status == RequirementStatus.CANDIDATES_RECEIVED:
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.INTERNAL_APPROVAL,
                    actor=x_role,
                    reason="itinerary_plan_selected",
                )
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.TENTATIVE_HOLD,
                    actor=x_role,
                    reason="calendar_hold_created",
                )
        plan.status = "accepted"
        plan.accepted_at = datetime.now(UTC)
        repo.save_plan(plan)
        repo.audit_event(
            AuditEvent(
                actor="requester", action="accept_plan", entity="ItineraryPlan", entity_id=plan_id
            )
        )
        return cast(
            dict[str, Any],
            remember_idempotent(
                repo,
                key,
                {"id": plan_id, "status": "accepted", "appointment_ids": appointment_ids},
            ),
        )

    @app.get("/api/v1/public/availability/{token}")
    def get_public_availability(token: str) -> dict[str, Any]:
        record = agent.tokens.validate(token)
        if not record:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "invalid_or_expired_token",
                    "message": "Availability token is invalid, expired, or revoked",
                },
            )
        requirement = repo.requirements.get(record.requirement_id)
        if not requirement or requirement.deleted_at is not None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "requirement_not_found",
                    "message": "Requirement does not exist",
                },
            )
        supplier = repo.suppliers.get(requirement.draft.supplier_id or "")
        return {
            "requirement_id": requirement.id,
            "supplier_name": supplier.display_name if supplier else "供应商",
            "purpose_category": requirement.draft.purpose_category,
            "candidate_windows": encode_data(public_candidate_windows(requirement)),
            "expires_at": record.expires_at,
        }

    @app.post("/api/v1/public/availability/{token}/submit", response_model=ToolResultDTO)
    def submit_public_availability(
        token: str, request: PublicAvailabilitySubmitRequest
    ) -> ToolResultDTO:
        record = agent.tokens.validate(token)
        if not record:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "invalid_or_expired_token",
                    "message": "Availability token is invalid, expired, or revoked",
                },
            )
        if request.requirement_id and request.requirement_id != record.requirement_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "token_requirement_mismatch",
                    "message": "Token does not belong to this requirement",
                },
            )
        if record.requirement_id not in repo.requirements:
            raise HTTPException(
                status_code=404,
                detail={"code": "requirement_not_found", "message": "Requirement does not exist"},
            )
        result = agent.submit_availability(
            record.requirement_id,
            token=token,
            windows=[item.model_dump() for item in request.selected_windows],
            alternative_windows=[item.model_dump() for item in request.alternative_windows],
            contact_name=request.contact_name,
            note=request.note,
            none_work=request.none_work,
        )
        if not result.ok:
            raise HTTPException(
                status_code=403, detail={"code": result.error_code, "message": result.message}
            )
        return dto_result(result)

    @app.get("/api/v1/availability-polls")
    def availability_polls(requirement_id: str | None = None) -> list[dict[str, Any]]:
        windows = repo.availability
        if requirement_id:
            windows = [item for item in windows if item.requirement_id == requirement_id]
        return cast(list[dict[str, Any]], encode_data(windows))

    @app.get("/api/v1/conversations")
    def conversations(requirement_id: str | None = None) -> list[dict[str, Any]]:
        threads = repo.conversations.values()
        return cast(
            list[dict[str, Any]],
            encode_data(
                [
                    item
                    for item in threads
                    if requirement_id is None or item.requirement_id == requirement_id
                ]
            ),
        )

    @app.get("/api/v1/messages")
    def messages(requirement_id: str | None = None) -> list[dict[str, Any]]:
        thread_ids = {
            item.id
            for item in repo.conversations.values()
            if requirement_id is None or item.requirement_id == requirement_id
        }
        return cast(
            list[dict[str, Any]],
            encode_data([item for item in repo.messages.values() if item.thread_id in thread_ids]),
        )

    @app.post("/api/v1/inbound-webhook")
    def inbound_webhook(
        payload: dict[str, Any],
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        key = idem_key("inbound-webhook", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        requirement_id = cast(str | None, payload.get("requirement_id"))
        requirement = repo.requirements.get(requirement_id or "")
        external_thread_id = str(
            payload.get("external_thread_id", f"webhook:{requirement_id or 'unmatched'}")
        )
        thread = repo.save_conversation(
            ConversationThread(
                channel=str(payload.get("channel", "email")),
                external_thread_id=external_thread_id,
                requirement_id=requirement_id,
                requirement_version=requirement.version if requirement else 0,
            )
        )
        parsed_result = parse_inbound_reply(str(payload.get("body", "")))
        parsed_result["raw_payload"] = payload
        stored_message = repo.save_message(
            Message(
                thread_id=thread.id,
                direction="inbound",
                body=str(payload.get("body", "")),
                send_status="received",
                parsed_result=parsed_result,
            )
        )
        for change_type in ("contact_change", "address_change"):
            proposed = parsed_result.get(change_type)
            if proposed:
                change_id = f"{change_type}:{stored_message.id}"
                repo.save_master_data_change(
                    MasterDataChangeRequest(
                        id=change_id,
                        entity_type=change_type.removesuffix("_change"),
                        entity_id=requirement_id or "unmatched",
                        original_value={},
                        proposed_value={"value": proposed},
                        source_message_id=stored_message.id,
                    )
                )
        message = {
            **cast(dict[str, Any], encode_data(stored_message)),
            "trusted_as_instruction": False,
        }
        repo.audit_event(
            AuditEvent(
                actor="mail-adapter",
                action="inbound_webhook_parsed",
                entity="Message",
                entity_id=stored_message.id,
            )
        )
        return cast(dict[str, Any], remember_idempotent(repo, key, message))

    @app.post("/api/v1/feishu/events")
    async def feishu_events(payload: dict[str, Any]) -> dict[str, Any]:
        challenge = payload.get("challenge")
        if payload.get("type") == "url_verification" and challenge:
            token = str(payload.get("token", ""))
            if (
                settings.feishu_event_verification_token
                and token != settings.feishu_event_verification_token
            ):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "invalid_feishu_token", "message": "Feishu token mismatch"},
                )
            return {"challenge": challenge}

        if "encrypt" in payload:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "feishu_encrypted_events_not_enabled",
                    "message": "Configure Feishu to send unencrypted events or add decrypt support",
                },
            )

        header = cast(dict[str, Any], payload.get("header") or {})
        event_type = str(header.get("event_type") or payload.get("type") or "")
        event = cast(dict[str, Any], payload.get("event") or {})
        if event_type != "im.message.receive_v1":
            return {"ok": True, "ignored": event_type or "unknown"}

        text = extract_feishu_message_text(event)
        message = cast(dict[str, Any], event.get("message") or {})
        chat_id = str(message.get("chat_id") or "")
        if not text:
            reply = "我收到了消息，但暂时只能处理文本。"
        elif any(keyword in text.lower() for keyword in ("日历", "日程", "calendar")):
            feishu = FeishuOpenPlatformAdapter(
                settings.feishu_app_id,
                settings.feishu_app_secret,
                base_url=settings.feishu_base_url,
            )
            events = await feishu.list_events(settings.feishu_calendar_id)
            await feishu.aclose()
            if events.ok:
                reply = format_calendar_summary(settings.feishu_calendar_id, events.data)
            else:
                reply = f"飞书日历查询失败：{events.message or events.error_code}"
        else:
            intake = agent.intake(text)
            if intake.ok:
                missing = intake.data.get("missing_slots", [])
                if missing:
                    reply = "已收到拜访需求。还需要补充：" + "、".join(missing)
                else:
                    reply = "已解析拜访需求，可以在 Routenda 工作台继续确认。"
            else:
                reply = intake.message or "解析失败，请稍后重试。"

        sent: dict[str, Any] | None = None
        if chat_id and settings.feishu_app_id and settings.feishu_app_secret:
            feishu = FeishuOpenPlatformAdapter(
                settings.feishu_app_id,
                settings.feishu_app_secret,
                base_url=settings.feishu_base_url,
            )
            result = await feishu.send_text(chat_id, reply, receive_id_type="chat_id")
            await feishu.aclose()
            if not result.ok:
                raise HTTPException(
                    status_code=502,
                    detail={"code": result.error_code, "message": result.message},
                )
            sent = cast(dict[str, Any], encode_data(result.data))

        return {"ok": True, "event_type": event_type, "text": text, "reply": reply, "sent": sent}

    @app.get("/api/v1/feishu/calendar")
    async def feishu_calendar(x_role: str = Header(default="requester", alias="X-Role")) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise HTTPException(
                status_code=400,
                detail={"code": "missing_credentials", "message": "Feishu credentials are missing"},
            )
        feishu = FeishuOpenPlatformAdapter(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            base_url=settings.feishu_base_url,
        )
        calendars = await feishu.list_calendars()
        events = await feishu.list_events(settings.feishu_calendar_id)
        await feishu.aclose()
        if not calendars.ok:
            raise HTTPException(
                status_code=502,
                detail={"code": calendars.error_code, "message": calendars.message},
            )
        if not events.ok:
            raise HTTPException(
                status_code=502,
                detail={"code": events.error_code, "message": events.message},
            )
        return {
            "calendar_id": settings.feishu_calendar_id,
            "calendars": encode_data(calendars.data),
            "events": encode_data(events.data),
        }

    @app.patch("/api/v1/messages/{message_id}/parsed-result")
    def correct_message_parse(
        message_id: str,
        request: CorrectMessageParseRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        key = idem_key(f"messages:{message_id}:parsed-result", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        message = repo.messages.get(message_id)
        if message is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "message_not_found", "message": "Message does not exist"},
            )
        before = message.parsed_result
        message.parsed_result = {
            **request.parsed_result,
            "needs_human_review": False,
            "trusted_as_instruction": False,
            "corrected_by": x_role,
            "corrected_at": datetime.now(UTC),
        }
        repo.save_message(message)
        repo.audit_event(
            AuditEvent(
                actor=x_role,
                action="correct_message_parse",
                entity="Message",
                entity_id=message.id,
                before=before,
                after=message.parsed_result,
            )
        )
        return cast(
            dict[str, Any],
            remember_idempotent(repo, key, cast(dict[str, Any], encode_data(message))),
        )

    @app.post("/api/v1/appointments/{appointment_id}/confirm")
    def confirm_appointment(
        appointment_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDTO:
        require_role(x_role, "coordinator")
        key = idem_key(f"appointments:{appointment_id}:confirm", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ApprovalDTO.model_validate(cached)
        appointment = repo.appointments.get(appointment_id)
        if appointment is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "appointment_not_found", "message": "Appointment does not exist"},
            )
        approval = agent.request_high_risk(
            "final_confirm_appointment",
            appointment.requirement_id,
            {
                "appointment_id": appointment.id,
                "requirement_id": appointment.requirement_id,
                "start": appointment.start,
                "end": appointment.end,
            },
        )
        return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))

    @app.get("/api/v1/appointments")
    def list_appointments(requirement_id: str | None = None) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            encode_data(
                [
                    item
                    for item in repo.appointments.values()
                    if requirement_id is None or item.requirement_id == requirement_id
                ]
            ),
        )

    @app.get("/api/v1/planning")
    def list_plans(requirement_id: str | None = None) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            encode_data(
                [
                    item
                    for item in repo.plans.values()
                    if requirement_id is None or requirement_id in item.requirement_ids
                ]
            ),
        )

    @app.post("/api/v1/appointments/{appointment_id}/reschedule", response_model=ApprovalDTO)
    def reschedule_appointment(
        appointment_id: str,
        request: RescheduleAppointmentRequest,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDTO:
        require_role(x_role, "coordinator")
        key = idem_key(f"appointments:{appointment_id}:reschedule", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ApprovalDTO.model_validate(cached)
        appointment = repo.appointments.get(appointment_id)
        if appointment is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "appointment_not_found", "message": "Appointment does not exist"},
            )
        approval = agent.request_high_risk(
            "move_confirmed_appointment",
            appointment.requirement_id,
            {
                "appointment_id": appointment_id,
                "before": encode_data(appointment),
                "after": {"start": request.start, "end": request.end},
                "reason": request.reason,
            },
        )
        return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))

    @app.post("/api/v1/appointments/{appointment_id}/cancel", response_model=ApprovalDTO)
    def cancel_appointment(
        appointment_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDTO:
        require_role(x_role, "coordinator")
        key = idem_key(f"appointments:{appointment_id}:cancel", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ApprovalDTO.model_validate(cached)
        appointment = repo.appointments.get(appointment_id)
        if appointment is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "appointment_not_found", "message": "Appointment does not exist"},
            )
        approval = agent.request_high_risk(
            "cancel_confirmed_appointment",
            appointment.requirement_id,
            {"appointment_id": appointment_id},
        )
        return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))

    @app.post("/api/v1/calendars/sync")
    def calendar_sync(
        request: CalendarSyncRequest | None = None,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        require_role(x_role, "coordinator")
        key = idem_key("calendars:sync", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        created: list[CalendarConflict] = []
        for change in (request or CalendarSyncRequest()).external_changes:
            binding = repo.calendar_bindings.get(change.binding_id)
            if binding is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "calendar_binding_not_found",
                        "message": f"Calendar binding does not exist: {change.binding_id}",
                    },
                )
            if change.etag == binding.etag:
                binding.last_sync_at = datetime.now(UTC)
                repo.save_calendar_binding(binding)
                continue
            appointment = repo.appointments[binding.appointment_id]
            conflict = repo.save_calendar_conflict(
                CalendarConflict(
                    appointment_id=appointment.id,
                    binding_id=binding.id,
                    local_snapshot=cast(dict[str, Any], encode_data(appointment)),
                    external_snapshot={"etag": change.etag, **change.snapshot},
                    reason="external_event_changed_since_last_sync",
                )
            )
            created.append(conflict)
            agent.request_high_risk(
                "resolve_calendar_conflict",
                appointment.requirement_id,
                {
                    "conflict_id": conflict.id,
                    "appointment_id": appointment.id,
                    "overwrote_external_changes": False,
                },
            )
        response = {
            "provider": "mock",
            "conflicts": encode_data(created),
            "overwrote_external_changes": False,
        }
        return cast(dict[str, Any], remember_idempotent(repo, key, response))

    @app.get("/api/v1/calendars/conflicts")
    def calendar_conflicts() -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], encode_data(list(repo.calendar_conflicts.values())))

    @app.get("/api/v1/master-data-change-requests")
    def list_master_data_changes(
        status: str | None = None,
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> list[dict[str, Any]]:
        require_role(x_role, "coordinator")
        return cast(
            list[dict[str, Any]],
            encode_data(
                [
                    item
                    for item in repo.master_data_changes.values()
                    if status is None or item.approval_status == status
                ]
            ),
        )

    @app.post("/api/v1/master-data-change-requests/{change_id}/approve")
    async def approve_master_data_change(
        change_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        require_role(x_role, "approver")
        key = idem_key(f"master-data:{change_id}:approve", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        change = repo.master_data_changes.get(change_id)
        if change is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "master_data_change_not_found",
                    "message": "Master data change does not exist",
                },
            )
        if change.entity_type == "contact":
            result = await agent.erp.propose_or_update_contact(change.proposed_value)
        elif change.entity_type in {"address", "site"}:
            result = await agent.erp.propose_or_update_site(change.proposed_value)
        else:
            result = ToolResult.success({"change_request": change.proposed_value})
        if not result.ok:
            raise HTTPException(
                status_code=502,
                detail={"code": result.error_code, "message": result.message},
            )
        change.approval_status = "approved"
        repo.save_master_data_change(change)
        repo.audit_event(
            AuditEvent(
                actor="approver",
                action="approve_master_data_change",
                entity="MasterDataChangeRequest",
                entity_id=change_id,
            )
        )
        return cast(dict[str, Any], remember_idempotent(repo, key, change))

    @app.post("/api/v1/master-data-change-requests/{change_id}/reject")
    def reject_master_data_change(
        change_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        require_role(x_role, "approver")
        key = idem_key(f"master-data:{change_id}:reject", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return cast(dict[str, Any], cached)
        change = repo.master_data_changes.get(change_id)
        if change is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "master_data_change_not_found",
                    "message": "Master data change does not exist",
                },
            )
        change.approval_status = "rejected"
        repo.save_master_data_change(change)
        repo.audit_event(
            AuditEvent(
                actor="approver",
                action="reject_master_data_change",
                entity="MasterDataChangeRequest",
                entity_id=change_id,
            )
        )
        return cast(dict[str, Any], remember_idempotent(repo, key, change))

    @app.get("/api/v1/approvals", response_model=list[ApprovalDTO])
    def list_approvals() -> list[ApprovalDTO]:
        return [ApprovalDTO.model_validate(item) for item in repo.approvals.values()]

    @app.get("/api/v1/tasks", response_model=list[HumanTaskDTO])
    def list_human_tasks(
        status: str | None = None,
        x_role: str = Header(default="requester", alias="X-Role"),
    ) -> list[HumanTaskDTO]:
        require_role(x_role, "coordinator")
        tasks = repo.human_tasks.values()
        return [
            HumanTaskDTO.model_validate(task)
            for task in tasks
            if status is None or task.status == status
        ]

    @app.post("/api/v1/tasks/{task_id}/resolve", response_model=HumanTaskDTO)
    def resolve_human_task(
        task_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> HumanTaskDTO:
        require_role(x_role, "coordinator")
        key = idem_key(f"tasks:{task_id}:resolve", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return HumanTaskDTO.model_validate(cached)
        task = next((item for item in repo.human_tasks.values() if item.id == task_id), None)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found", "message": "Human task does not exist"},
            )
        task.status = "resolved"
        repo.update_human_task(task)
        repo.audit_event(
            AuditEvent(
                actor=x_role,
                action="resolve_human_task",
                entity="HumanTask",
                entity_id=task.id,
            )
        )
        return HumanTaskDTO.model_validate(remember_idempotent(repo, key, task))

    @app.post("/api/v1/approvals/{approval_id}/approve", response_model=ApprovalDTO)
    async def approve(
        approval_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDTO:
        require_role(x_role, "approver")
        key = idem_key(f"approvals:{approval_id}:approve", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ApprovalDTO.model_validate(cached)
        approval = repo.approvals.get(approval_id)
        if not approval:
            raise HTTPException(
                status_code=404,
                detail={"code": "approval_not_found", "message": "Approval does not exist"},
            )
        approval.status = "approved"
        if approval.action == "send_external_message":
            requirement_id = str(approval.impact_preview["requirement_id"])
            send_result = await agent.contact_supplier(
                requirement_id,
                approved=True,
            )
            if not send_result.ok:
                approval.status = "execution_failed"
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": send_result.error_code,
                        "message": send_result.message,
                    },
                )
        if approval.action == "modify_confirmed_requirement":
            requirement_id = str(approval.impact_preview["requirement_id"])
            requirement = repo.requirements.get(requirement_id)
            if not requirement:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "approval_target_missing",
                        "message": "Requirement for this approval no longer exists",
                    },
                )
            patch = cast(dict[str, Any], approval.impact_preview.get("patch", {}))
            source = str(approval.impact_preview.get("source", "approved_update"))
            apply_requirement_patch(
                repo,
                requirement,
                patch,
                source,
                x_role,
            )
            if requirement.status == RequirementStatus.CONFIRMED:
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.RESCHEDULE_REQUESTED,
                    actor=x_role,
                    reason="approved_requirement_change",
                )
            repo.audit_event(
                AuditEvent(
                    actor=x_role,
                    action="apply_approved_requirement_update",
                    entity="VisitRequirement",
                    entity_id=requirement.id,
                    before=cast(
                        dict[str, Any],
                        approval.impact_preview.get("before", {}),
                    ),
                    after={
                        "draft": encode_data(requirement.draft.model_dump()),
                        "status": requirement.status.value,
                        "version": requirement.version,
                    },
                )
            )
        if approval.action == "final_confirm_appointment":
            appointment_id = str(approval.impact_preview["appointment_id"])
            appointment = repo.appointments.get(appointment_id)
            if appointment is None:
                approval.status = "execution_failed"
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "approval_target_missing",
                        "message": "Appointment for this approval no longer exists",
                    },
                )
            before = cast(dict[str, Any], encode_data(appointment))
            confirmation = await agent.calendar.confirm_event(appointment)
            if not confirmation.ok:
                approval.status = "execution_failed"
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": confirmation.error_code,
                        "message": confirmation.message,
                    },
                )
            appointment.status = "confirmed"
            appointment.supplier_confirmation_status = "confirmed"
            repo.save_appointment(appointment)
            repo.add_appointment_version(
                AppointmentVersion(
                    appointment_id=appointment.id,
                    before=before,
                    after=cast(dict[str, Any], encode_data(appointment)),
                    reason="final_confirmation_approved",
                )
            )
            binding = next(
                (
                    item
                    for item in repo.calendar_bindings.values()
                    if item.appointment_id == appointment.id
                ),
                None,
            )
            if binding:
                binding.etag = str(confirmation.data.get("etag", binding.etag))
                binding.last_sync_at = datetime.now(UTC)
                repo.save_calendar_binding(binding)
            requirement = repo.requirements.get(appointment.requirement_id)
            if requirement and requirement.status == RequirementStatus.TENTATIVE_HOLD:
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.CONFIRMED,
                    actor=x_role,
                    reason="final_confirmation_approved",
                )
                erp_result = await agent.erp.update_visit_status(
                    requirement.id, RequirementStatus.CONFIRMED.value
                )
                if not erp_result.ok:
                    approval.status = "execution_failed"
                    raise HTTPException(
                        status_code=502,
                        detail={"code": erp_result.error_code, "message": erp_result.message},
                    )
        if approval.action == "move_confirmed_appointment":
            appointment_id = str(approval.impact_preview["appointment_id"])
            appointment = repo.appointments.get(appointment_id)
            if appointment is None:
                approval.status = "execution_failed"
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "approval_target_missing",
                        "message": "Appointment for this approval no longer exists",
                    },
                )
            after = cast(dict[str, Any], approval.impact_preview["after"])
            new_start = coerce_datetime(after["start"])
            new_end = coerce_datetime(after["end"])
            before = cast(dict[str, Any], encode_data(appointment))
            if appointment.calendar_external_event_id:
                updated = await agent.calendar.update_or_cancel_event(
                    appointment.calendar_external_event_id,
                    {"start": new_start, "end": new_end},
                )
                if not updated.ok:
                    approval.status = "execution_failed"
                    raise HTTPException(
                        status_code=502,
                        detail={"code": updated.error_code, "message": updated.message},
                    )
                binding = next(
                    (
                        item
                        for item in repo.calendar_bindings.values()
                        if item.appointment_id == appointment.id
                    ),
                    None,
                )
                if binding:
                    binding.etag = str(updated.data.get("etag", binding.etag))
                    binding.last_sync_at = datetime.now(UTC)
                    repo.save_calendar_binding(binding)
            appointment.start = new_start
            appointment.end = new_end
            appointment.status = "rescheduled"
            appointment.supplier_confirmation_status = "tentative"
            repo.save_appointment(appointment)
            repo.add_appointment_version(
                AppointmentVersion(
                    appointment_id=appointment.id,
                    before=before,
                    after=cast(dict[str, Any], encode_data(appointment)),
                    reason=str(approval.impact_preview.get("reason", "reschedule")),
                )
            )
            requirement = repo.requirements.get(appointment.requirement_id)
            if requirement and requirement.status == RequirementStatus.CONFIRMED:
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.RESCHEDULE_REQUESTED,
                    actor=x_role,
                    reason="appointment_rescheduled",
                )
        if approval.action == "cancel_confirmed_appointment":
            appointment_id = str(approval.impact_preview["appointment_id"])
            appointment = repo.appointments.get(appointment_id)
            if appointment is None:
                approval.status = "execution_failed"
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "approval_target_missing",
                        "message": "Appointment for this approval no longer exists",
                    },
                )
            before = cast(dict[str, Any], encode_data(appointment))
            if appointment.calendar_external_event_id:
                cancelled = await agent.calendar.update_or_cancel_event(
                    appointment.calendar_external_event_id, {"cancel": True}
                )
                if not cancelled.ok:
                    approval.status = "execution_failed"
                    raise HTTPException(
                        status_code=502,
                        detail={"code": cancelled.error_code, "message": cancelled.message},
                    )
            appointment.status = "cancelled"
            repo.save_appointment(appointment)
            repo.add_appointment_version(
                AppointmentVersion(
                    appointment_id=appointment.id,
                    before=before,
                    after=cast(dict[str, Any], encode_data(appointment)),
                    reason="cancellation_approved",
                )
            )
            requirement = repo.requirements.get(appointment.requirement_id)
            if requirement and requirement.status == RequirementStatus.CONFIRMED:
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.CANCELLATION_REQUESTED,
                    actor=x_role,
                    reason="cancellation_approved",
                )
                transition_requirement(
                    repo,
                    requirement,
                    RequirementStatus.CANCELLED,
                    actor=x_role,
                    reason="appointment_cancelled",
                )
                if bool(approval.impact_preview.get("soft_delete")):
                    requirement.deleted_at = datetime.now(UTC)
                    requirement.version += 1
                await agent.erp.update_visit_status(
                    requirement.id, RequirementStatus.CANCELLED.value
                )
        repo.audit_event(
            AuditEvent(
                actor="approver",
                action=f"approved:{approval.action}",
                entity="ApprovalRequest",
                entity_id=approval.id,
            )
        )
        return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))

    @app.post("/api/v1/approvals/{approval_id}/reject", response_model=ApprovalDTO)
    def reject(
        approval_id: str,
        x_role: str = Header(default="requester", alias="X-Role"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDTO:
        require_role(x_role, "approver")
        key = idem_key(f"approvals:{approval_id}:reject", idempotency_key)
        cached = cached_idempotent(repo, key)
        if cached:
            return ApprovalDTO.model_validate(cached)
        approval = repo.approvals.get(approval_id)
        if not approval:
            raise HTTPException(
                status_code=404,
                detail={"code": "approval_not_found", "message": "Approval does not exist"},
            )
        approval.status = "rejected"
        repo.audit_event(
            AuditEvent(
                actor="approver",
                action=f"rejected:{approval.action}",
                entity="ApprovalRequest",
                entity_id=approval.id,
            )
        )
        return ApprovalDTO.model_validate(remember_idempotent(repo, key, approval))

    @app.get("/api/v1/audit-events")
    def list_audit_events() -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], encode_data(repo.audit))

    @app.get("/api/v1/policy/{action}")
    def policy(action: str) -> dict[str, str]:
        return {"action": action, "risk": classify_action(action).value}

    return app


app = create_app()
