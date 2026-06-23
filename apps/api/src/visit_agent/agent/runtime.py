from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.agents.react_agent import ReactAgent
from visit_agent.config import settings
from visit_agent.core.config import AgentConfig
from visit_agent.core.llm import FallbackLLM, LLM, OpenAICompatibleLLM, ResilientLLM
from visit_agent.infrastructure.adapters.feishu import (
    FeishuOpenPlatformAdapter,
    format_calendar_summary,
)
from visit_agent.tools.base import BaseTool, ToolContext, ToolResult
from visit_agent.tools.builtin.calculator import CalculatorTool
from visit_agent.tools.builtin.search import SearchTool
from visit_agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class AgentToolCall:
    name: str
    ok: bool
    output: str
    raw: Any = None


@dataclass(frozen=True)
class AgentTurn:
    text: str
    reply: str
    tool_calls: list[AgentToolCall] = field(default_factory=list)


class AgentRuntime:
    """Compatibility runtime backed by the core Agent framework."""

    def __init__(
        self,
        coordinator: VisitCoordinatorAgent,
        *,
        feishu_app_id: str = "",
        feishu_app_secret: str = "",
        feishu_base_url: str = "https://open.feishu.cn/open-apis",
        feishu_calendar_id: str = "primary",
        llm: LLM | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.feishu_app_id = feishu_app_id
        self.feishu_app_secret = feishu_app_secret
        self.feishu_base_url = feishu_base_url
        self.feishu_calendar_id = feishu_calendar_id
        self.tools = self._build_tools()
        self.agent = ReactAgent(
            llm or self._default_llm(),
            self.tools,
            AgentConfig(),
        )

    async def run(self, text: str) -> AgentTurn:
        if not text.strip():
            return AgentTurn(text=text, reply="我收到了消息，但暂时只能处理文本。")
        response = await self.agent.run(text)
        return AgentTurn(
            text=text,
            reply=response.content,
            tool_calls=[
                AgentToolCall(call.name, call.ok, call.output, call.raw)
                for call in response.tool_calls
            ],
        )

    def _build_tools(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool(self.coordinator.search))
        registry.register(ExtractVisitRequirementTool(self.coordinator))
        registry.register(SearchSuppliersTool(self.coordinator))
        registry.register(GenerateItineraryPlanTool(self.coordinator))
        registry.register(
            FeishuCalendarTool(
                app_id=self.feishu_app_id,
                app_secret=self.feishu_app_secret,
                base_url=self.feishu_base_url,
                calendar_id=self.feishu_calendar_id,
            )
        )
        return registry

    @staticmethod
    def _default_llm() -> LLM:
        return ResilientLLM(
            OpenAICompatibleLLM(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                model=settings.openai_model,
            ),
            FallbackLLM(),
        )


class ExtractVisitRequirementTool(BaseTool):
    name = "extract_visit_requirement"
    description = "Extract structured business visit requirements from natural language."

    def __init__(self, coordinator: VisitCoordinatorAgent) -> None:
        self.coordinator = coordinator

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        text = str(args.get("text", "")).strip()
        if not text:
            return ToolResult.failure("missing_text", "缺少待抽取文本。")
        result = self.coordinator.intake(text)
        if not result.ok:
            return ToolResult.failure(
                result.error_code or "extract_failed",
                result.message or "抽取失败",
            )
        draft = result.data.get("draft")
        missing = result.data.get("missing_slots", [])
        parts: list[str] = []
        if draft and getattr(draft, "supplier_name", ""):
            parts.append(f"供应商={draft.supplier_name}")
        if draft and getattr(draft, "purpose_category", ""):
            parts.append(f"目的={draft.purpose_category}")
        if draft and getattr(draft, "duration_minutes", None):
            parts.append(f"时长={draft.duration_minutes}分钟")
        if missing:
            parts.append("待补充=" + "、".join(missing))
        return ToolResult.success(
            "；".join(parts) if parts else "这条消息不像完整拜访需求。",
            result.data,
        )


class SearchSuppliersTool(BaseTool):
    name = "search_suppliers"
    description = "Search supplier and site records from ERP or local Excel sample data."

    def __init__(self, coordinator: VisitCoordinatorAgent) -> None:
        self.coordinator = coordinator

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        text = str(args.get("text", args.get("query", ""))).strip()
        suppliers = [
            supplier
            for supplier in self.coordinator.repo.suppliers.values()
            if supplier.display_name in text
            or supplier.legal_name in text
            or any(alias and alias in text for alias in supplier.aliases)
        ]
        if not suppliers:
            result = self.coordinator.tools.execute("search_suppliers", {"query": text[:80]})
            suppliers = list(result.data or []) if result.ok else []
        if not suppliers:
            return ToolResult.success("没有在本地 ERP/Excel 样例数据里匹配到供应商。")
        lines: list[str] = []
        for supplier in suppliers[:3]:
            sites = [
                site
                for site in self.coordinator.repo.sites.values()
                if site.supplier_id == supplier.id
            ]
            site_text = "；".join(
                f"{site.name}，{site.raw_address}，接待时间 {site.reception_hours}"
                for site in sites[:2]
            )
            lines.append(f"- {supplier.display_name} ({supplier.erp_id})：{site_text or '暂无厂区'}")
        return ToolResult.success("\n".join(lines), suppliers)


class FeishuCalendarTool(BaseTool):
    name = "feishu_calendar"
    description = "Read the configured Feishu calendar events."

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str,
        calendar_id: str,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url
        self.calendar_id = calendar_id

    async def arun(
        self,
        args: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        if not self.app_id or not self.app_secret:
            return ToolResult.failure("missing_credentials", "飞书凭据未配置。")
        feishu = FeishuOpenPlatformAdapter(
            self.app_id,
            self.app_secret,
            base_url=self.base_url,
        )
        try:
            events = await feishu.list_events(self.calendar_id)
            if events.ok:
                return ToolResult.success(
                    format_calendar_summary(self.calendar_id, events.data),
                    events.data,
                )
            return ToolResult.failure(
                events.error_code or "feishu_calendar_failed",
                events.message or "飞书日历查询失败",
            )
        finally:
            await feishu.aclose()


class GenerateItineraryPlanTool(BaseTool):
    name = "generate_itinerary_plan"
    description = "Generate an itinerary plan for active visit requirements."

    def __init__(self, coordinator: VisitCoordinatorAgent) -> None:
        self.coordinator = coordinator

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        active = [
            requirement.id
            for requirement in self.coordinator.repo.requirements.values()
            if requirement.deleted_at is None
        ]
        if not active:
            return ToolResult.success("当前还没有已确认的拜访需求可规划。")
        result = self.coordinator.plan(active[:3])
        if not result.ok:
            return ToolResult.failure(
                result.error_code or "plan_failed",
                result.message or "规划失败",
            )
        plan = result.data
        return ToolResult.success(
            f"生成方案 {plan.id}，拜访段数 {len(plan.legs)}，未安排 {len(plan.unassigned)}。",
            plan,
        )
