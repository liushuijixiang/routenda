from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from visit_agent.agent.graph import SlotFillingGraph
from visit_agent.agent.context import ContextBuilder
from visit_agent.agent.llm_gateway import LLMGateway
from visit_agent.agent.policy import Risk, classify_action
from visit_agent.agent.session_store import SessionStore
from visit_agent.agent.tools.registry import ToolRegistry, ToolSpec
from visit_agent.agent.tools.result import ToolResult
from visit_agent.application.tokens import AvailabilityTokenService
from visit_agent.config import settings
from visit_agent.application.status import transition_requirement
from visit_agent.domain.models import (
    ApprovalRequest,
    AuditEvent,
    ConversationThread,
    Message,
    RequirementRevision,
    RequirementStatus,
    VisitRequirement,
)
from visit_agent.infrastructure.adapters.communication import (
    SMTPAdapter,
    submit_public_availability,
)
from visit_agent.infrastructure.adapters.calendar import (
    FeishuCalendarAdapter,
    CalendarPort,
    MicrosoftGraphCalendarAdapter,
    MockCalendarAdapter,
)
from visit_agent.infrastructure.adapters.erp import (
    ERPNextAdapter,
    ERPPort,
    ExcelERPAdapter,
    MockERPAdapter,
)
from visit_agent.infrastructure.adapters.geo import (
    GeocoderPort,
    HaversineRouteMatrix,
    MockGeocoder,
    NominatimGeocoder,
    OSRMRouteMatrix,
    RouteMatrixPort,
)
from visit_agent.infrastructure.adapters.search import (
    DisabledSearchAdapter,
    SearchPort,
    SerperSearchAdapter,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository
from visit_agent.planning.solver import (
    DESTINATION_POINT,
    ORIGIN_POINT,
    ItinerarySolver,
    TravelTimes,
)


class VisitCoordinatorAgent:
    def __init__(self, repo: InMemoryRepository, llm: LLMGateway | None = None) -> None:
        self.repo = repo
        self.llm = llm or LLMGateway(
            settings.openai_api_key,
            settings.openai_base_url,
            settings.openai_model,
        )
        self.sessions = SessionStore(repo)
        self.context = ContextBuilder(repo)
        self.graph = SlotFillingGraph(repo, self.llm)
        self.tokens = AvailabilityTokenService(repo)
        if settings.erp_provider == "excel" and settings.erp_excel_path:
            self.erp: ERPPort = ExcelERPAdapter(repo, settings.erp_excel_path)
        elif (
            settings.erp_next_base_url
            and settings.erp_next_api_key
            and settings.erp_next_api_secret
        ):
            self.erp = ERPNextAdapter(
                settings.erp_next_base_url,
                settings.erp_next_api_key,
                settings.erp_next_api_secret,
            )
        else:
            self.erp = MockERPAdapter(repo)
        self.comm = SMTPAdapter(repo)
        if (
            settings.calendar_provider == "feishu"
            and settings.feishu_app_id
            and settings.feishu_app_secret
        ):
            self.calendar: CalendarPort = FeishuCalendarAdapter(
                settings.feishu_app_id,
                settings.feishu_app_secret,
                base_url=settings.feishu_base_url,
                calendar_id=settings.feishu_calendar_id,
            )
        elif (
            settings.microsoft_tenant_id
            and settings.microsoft_client_id
            and settings.microsoft_client_secret
        ):
            self.calendar = MicrosoftGraphCalendarAdapter(
                settings.microsoft_tenant_id,
                settings.microsoft_client_id,
                settings.microsoft_client_secret,
            )
        else:
            self.calendar = MockCalendarAdapter(repo.people_busy)
        self.geocoder: GeocoderPort = (
            NominatimGeocoder(settings.nominatim_base_url, settings.nominatim_user_agent)
            if settings.geocoding_provider == "nominatim"
            else MockGeocoder(
                {site.raw_address: (site.latitude, site.longitude) for site in repo.sites.values()}
            )
        )
        self.route_matrix: RouteMatrixPort = (
            OSRMRouteMatrix(settings.osrm_base_url)
            if settings.routing_provider == "osrm"
            else HaversineRouteMatrix()
        )
        self.search: SearchPort = (
            SerperSearchAdapter(settings.serper_api_key, settings.serper_url)
            if settings.search_provider == "serper"
            else DisabledSearchAdapter()
        )
        self.solver = ItinerarySolver(repo)
        self.tools = ToolRegistry(self._audit_tool_execution)
        self._register_tools()

    def _audit_tool_execution(self, spec: ToolSpec, result: ToolResult) -> None:
        self.repo.audit_event(
            AuditEvent(
                actor="agent",
                action=f"tool:{spec.name}",
                entity="ToolCall",
                entity_id=result.audit_id,
                after={
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "risk": spec.risk.value,
                    "read_write": spec.read_write,
                    "idempotent": spec.idempotent,
                },
            )
        )

    def _register_tools(self) -> None:
        class SearchSuppliersArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")
            query: str = Field(min_length=1, max_length=255)

        class GeneratePlanArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")
            requirement_ids: list[str] = Field(min_length=1)

        class EmptyArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")

        self.tools.register(
            "search_suppliers",
            "Search ERP suppliers",
            "read_supplier",
            "read",
            True,
            SearchSuppliersArgs,
            lambda query: ToolResult.success(
                [s for s in self.repo.suppliers.values() if query in s.display_name]
            ),
        )
        self.tools.register(
            "generate_plan",
            "Generate itinerary plan",
            "generate_plan",
            "read",
            True,
            GeneratePlanArgs,
            lambda requirement_ids: ToolResult.success(self.solver.plan(requirement_ids)),
        )
        self.tools.register(
            "forbid_negotiation",
            "Block negotiation topics",
            "negotiation",
            "write",
            True,
            EmptyArgs,
            lambda: ToolResult.failure("forbidden", "Agent cannot discuss negotiation topics"),
        )

    def intake(self, text: str) -> ToolResult:
        state = self.graph.receive_input(text)
        self.sessions.save(state)
        return ToolResult.success(
            {
                "session_id": state.session_id,
                "draft": state.draft,
                "missing_slots": state.missing_slots,
                "candidates": state.candidate_entities,
            }
        )

    def confirm_requirement(
        self,
        session_id: str,
        patch: dict[str, Any] | None = None,
    ) -> ToolResult:
        state = self.sessions.get(session_id)
        if not state:
            return ToolResult.failure("session_not_found", "No intake session exists")
        if patch:
            state = self.graph.merge_form(state, patch)
        if state.missing_slots:
            return ToolResult.failure("missing_slots", ",".join(state.missing_slots))
        req = VisitRequirement(draft=state.draft, status=RequirementStatus.READY_TO_CONTACT)
        self.repo.requirements[req.id] = req
        self.repo.revisions.append(
            RequirementRevision(
                requirement_id=req.id,
                diff={"before": None, "after": state.draft.model_dump()},
                source="agent_intake",
                actor="requester",
            )
        )
        self.repo.audit_event(
            AuditEvent(
                actor="requester",
                action="persist_requirement_revision",
                entity="VisitRequirement",
                entity_id=req.id,
                after={"status": req.status.value},
            )
        )
        return ToolResult.success(req)

    async def contact_supplier(
        self,
        requirement_id: str,
        approved: bool = False,
    ) -> ToolResult:
        req = self.repo.requirements[requirement_id]
        if req.deleted_at is not None:
            return ToolResult.failure("requirement_deleted", "Requirement is deleted")
        if req.paused_at is not None:
            return ToolResult.failure("requirement_paused", "Requirement is paused")
        risk = classify_action("send_external_message")
        if (
            settings.require_first_contact_approval
            and risk in {Risk.CONFIRM, Risk.HIGH}
            and not approved
        ):
            approval = ApprovalRequest(
                action="send_external_message",
                risk=risk.value,
                impact_preview={"requirement_id": requirement_id},
                approver="coordinator",
            )
            self.repo.approvals[approval.id] = approval
            self.repo.audit_event(
                AuditEvent(
                    actor="agent",
                    action="approval_requested:send_external_message",
                    entity="VisitRequirement",
                    entity_id=requirement_id,
                    after={"approval_id": approval.id},
                )
            )
            return ToolResult.success(
                {"approval": approval, "queued": False},
                "approval_required",
            )
        token = self.tokens.issue(requirement_id)
        result = await self.comm.send_candidate_email(
            requirement_id, "supplier@example.test", token=token
        )
        transition_requirement(
            self.repo,
            req,
            RequirementStatus.CONTACTED,
            actor="agent",
            reason="supplier_contact_queued",
        )
        transition_requirement(
            self.repo,
            req,
            RequirementStatus.WAITING_REPLY,
            actor="agent",
            reason="awaiting_supplier_availability",
        )
        self.repo.audit_event(
            AuditEvent(
                actor="agent",
                action="queue_supplier_email",
                entity="VisitRequirement",
                entity_id=requirement_id,
            )
        )
        return result

    def submit_availability(
        self,
        requirement_id: str,
        token: str | None = None,
        windows: list[dict[str, Any]] | None = None,
        alternative_windows: list[dict[str, Any]] | None = None,
        contact_name: str = "supplier",
        note: str = "",
        none_work: bool = False,
    ) -> ToolResult:
        if token:
            record = self.tokens.validate(token, requirement_id=requirement_id)
            if not record:
                return ToolResult.failure(
                    "invalid_or_expired_token", "Availability token is invalid, expired, or revoked"
                )
        req = self.repo.requirements[requirement_id]
        if req.draft.date_start is None:
            return ToolResult.failure("missing_date_start", "Requirement date_start is required")
        submitted_windows = windows
        if submitted_windows is None:
            start = req.draft.date_start + timedelta(hours=2)
            submitted_windows = [
                {
                    "start": start,
                    "end": start + timedelta(hours=3),
                    "timezone_name": "Asia/Shanghai",
                    "preference": 3,
                }
            ]
        created_windows = []
        for item in submitted_windows:
            created_windows.append(
                submit_public_availability(
                    self.repo,
                    requirement_id,
                    contact_name,
                    item["start"],
                    item["end"],
                    timezone_name=str(item.get("timezone_name", "Asia/Shanghai")),
                    preference=int(item.get("preference", 3)),
                )
            )
        for item in alternative_windows or []:
            created_windows.append(
                submit_public_availability(
                    self.repo,
                    requirement_id,
                    contact_name,
                    item["start"],
                    item["end"],
                    timezone_name=str(item.get("timezone_name", "Asia/Shanghai")),
                    preference=int(item.get("preference", 3)),
                    source="supplier_alternative",
                )
            )
        thread = self.repo.save_conversation(
            ConversationThread(
                channel="public_availability",
                external_thread_id=f"availability:{requirement_id}",
                requirement_id=requirement_id,
                requirement_version=req.version,
            )
        )
        self.repo.save_message(
            Message(
                thread_id=thread.id,
                direction="inbound",
                body=note,
                send_status="received",
                parsed_result={
                    "contact_name": contact_name,
                    "selected_window_ids": [item.id for item in created_windows],
                    "none_work": none_work,
                    "trusted_as_instruction": False,
                },
            )
        )
        if token:
            self.tokens.mark_submitted(token)
        if created_windows and req.status == RequirementStatus.WAITING_REPLY:
            transition_requirement(
                self.repo,
                req,
                RequirementStatus.CANDIDATES_RECEIVED,
                actor="supplier-public-page",
                reason="availability_submitted",
            )
        plan = self.solver.plan([requirement_id])
        self.repo.save_plan(plan)
        self.repo.audit_event(
            AuditEvent(
                actor="supplier-public-page",
                action="submit_availability",
                entity="VisitRequirement",
                entity_id=requirement_id,
                after={
                    "window_count": len(created_windows),
                    "none_work": none_work,
                    "replan_id": plan.id,
                },
            )
        )
        return ToolResult.success(
            {
                "windows": created_windows,
                "none_work": none_work,
                "replan": plan,
            }
        )

    def plan(self, requirement_ids: list[str]) -> ToolResult:
        plan = self.solver.plan(requirement_ids)
        self.repo.save_plan(plan)
        return ToolResult.success(plan)

    async def plan_with_routes(self, requirement_ids: list[str]) -> ToolResult:
        points = [ORIGIN_POINT]
        for requirement_id in requirement_ids:
            requirement = self.repo.requirements[requirement_id]
            site = self.repo.sites.get(requirement.draft.site_id or "")
            if site:
                point = (site.latitude, site.longitude)
                if point not in points:
                    points.append(point)
        if DESTINATION_POINT not in points:
            points.append(DESTINATION_POINT)
        matrix_result = await self.route_matrix.duration_minutes(points)
        travel_times = TravelTimes()
        provider = "haversine-estimate"
        if matrix_result.ok:
            provider = str(matrix_result.data.get("provider", provider))
            matrix = matrix_result.data.get("matrix", [])
            for left_index, left in enumerate(points):
                for right_index, right in enumerate(points):
                    value = matrix[left_index][right_index]
                    if value is not None:
                        travel_times.overrides[(left, right)] = int(value)
        plan = self.solver.plan(requirement_ids, travel_times=travel_times)
        plan.solver = f"{plan.solver}+{provider}"
        plan.explanation_codes.append(f"ROUTING_PROVIDER:{provider}")
        has_existing = any(
            appointment.requirement_id in requirement_ids and appointment.status != "cancelled"
            for appointment in self.repo.appointments.values()
        )
        if has_existing:
            alternative = self.solver.plan(
                requirement_ids,
                travel_times=travel_times,
                variant="minimal_change",
            )
            alternative.solver = f"{alternative.solver}+{provider}"
            alternative.explanation_codes.append(f"ROUTING_PROVIDER:{provider}")
            recommended_signature = [(leg.requirement_id, leg.start, leg.end) for leg in plan.legs]
            alternative_signature = [
                (leg.requirement_id, leg.start, leg.end) for leg in alternative.legs
            ]
            if alternative_signature != recommended_signature:
                self.repo.save_plan(alternative)
                plan.alternative_plan_id = alternative.id
        self.repo.save_plan(plan)
        return ToolResult.success(plan)

    def request_high_risk(
        self,
        action: str,
        requirement_id: str,
        preview: dict[str, Any],
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            action=action, risk="high", impact_preview=preview, approver="approver"
        )
        self.repo.approvals[approval.id] = approval
        self.repo.audit_event(
            AuditEvent(
                actor="agent",
                action=f"approval_requested:{action}",
                entity="VisitRequirement",
                entity_id=requirement_id,
                after=preview,
            )
        )
        return approval
