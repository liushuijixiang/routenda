from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.infrastructure.adapters.feishu import (
    FeishuOpenPlatformAdapter,
    format_calendar_summary,
)


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
    """Conversation runtime that plans tool use and synthesizes user-facing replies."""

    def __init__(
        self,
        coordinator: VisitCoordinatorAgent,
        *,
        feishu_app_id: str = "",
        feishu_app_secret: str = "",
        feishu_base_url: str = "https://open.feishu.cn/open-apis",
        feishu_calendar_id: str = "primary",
    ) -> None:
        self.coordinator = coordinator
        self.feishu_app_id = feishu_app_id
        self.feishu_app_secret = feishu_app_secret
        self.feishu_base_url = feishu_base_url
        self.feishu_calendar_id = feishu_calendar_id

    async def run(self, text: str) -> AgentTurn:
        if not text.strip():
            return AgentTurn(text=text, reply="我收到了消息，但暂时只能处理文本。")

        planned = self._plan(text)
        calls: list[AgentToolCall] = []
        for tool_name in planned:
            calls.append(await self._execute(tool_name, text))
        return AgentTurn(text=text, tool_calls=calls, reply=self._synthesize(calls))

    def _plan(self, text: str) -> list[str]:
        tools = ["extract_visit_requirement"]
        lowered = text.lower()
        if any(keyword in text for keyword in ("供应商", "厂", "客户", "安科", "恒曜", "电子")):
            tools.append("search_suppliers")
        if any(keyword in lowered for keyword in ("日历", "日程", "calendar", "空闲", "忙闲")):
            tools.append("feishu_calendar")
        if any(keyword in text for keyword in ("搜索", "查一下", "资料", "新闻", "官网", "背景")):
            tools.append("web_search")
        if any(keyword in text for keyword in ("行程", "路线", "规划", "安排")):
            tools.append("generate_itinerary_plan")
        return tools

    async def _execute(self, tool_name: str, text: str) -> AgentToolCall:
        if tool_name == "extract_visit_requirement":
            return self._extract_visit_requirement(text)
        if tool_name == "search_suppliers":
            return self._search_suppliers(text)
        if tool_name == "feishu_calendar":
            return await self._feishu_calendar()
        if tool_name == "web_search":
            return await self._web_search(text)
        if tool_name == "generate_itinerary_plan":
            return self._generate_itinerary_plan()
        return AgentToolCall(tool_name, False, f"Unknown tool: {tool_name}")

    def _extract_visit_requirement(self, text: str) -> AgentToolCall:
        result = self.coordinator.intake(text)
        if not result.ok:
            return AgentToolCall(
                "extract_visit_requirement",
                False,
                result.message or result.error_code or "抽取失败",
                result,
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
        return AgentToolCall(
            "extract_visit_requirement",
            True,
            "；".join(parts) if parts else "这条消息不像完整拜访需求。",
            result.data,
        )

    def _search_suppliers(self, text: str) -> AgentToolCall:
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
            return AgentToolCall(
                "search_suppliers",
                True,
                "没有在本地 ERP/Excel 样例数据里匹配到供应商。",
            )
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
        return AgentToolCall("search_suppliers", True, "\n".join(lines), suppliers)

    async def _feishu_calendar(self) -> AgentToolCall:
        if not self.feishu_app_id or not self.feishu_app_secret:
            return AgentToolCall("feishu_calendar", False, "飞书凭据未配置。")
        feishu = FeishuOpenPlatformAdapter(
            self.feishu_app_id,
            self.feishu_app_secret,
            base_url=self.feishu_base_url,
        )
        try:
            events = await feishu.list_events(self.feishu_calendar_id)
            if events.ok:
                return AgentToolCall(
                    "feishu_calendar",
                    True,
                    format_calendar_summary(self.feishu_calendar_id, events.data),
                    events.data,
                )
            return AgentToolCall(
                "feishu_calendar",
                False,
                events.message or events.error_code or "飞书日历查询失败",
                events,
            )
        finally:
            await feishu.aclose()

    async def _web_search(self, text: str) -> AgentToolCall:
        result = await self.coordinator.search.search(text)
        if not result.ok:
            return AgentToolCall("web_search", False, result.message or result.error_code or "搜索失败")
        payload = result.data or {}
        organic = payload.get("organic", []) if isinstance(payload, dict) else []
        lines = []
        for item in organic[:3]:
            title = item.get("title", "未命名结果")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            lines.append(f"- {title}\n  {snippet}\n  {link}".rstrip())
        return AgentToolCall(
            "web_search",
            True,
            "\n".join(lines) if lines else "搜索没有返回可用结果。",
            payload,
        )

    def _generate_itinerary_plan(self) -> AgentToolCall:
        active = [
            requirement.id
            for requirement in self.coordinator.repo.requirements.values()
            if requirement.deleted_at is None
        ]
        if not active:
            return AgentToolCall(
                "generate_itinerary_plan",
                True,
                "当前还没有已确认的拜访需求可规划。",
            )
        result = self.coordinator.plan(active[:3])
        if not result.ok:
            return AgentToolCall(
                "generate_itinerary_plan",
                False,
                result.message or result.error_code or "规划失败",
                result,
            )
        plan = result.data
        return AgentToolCall(
            "generate_itinerary_plan",
            True,
            f"生成方案 {plan.id}，拜访段数 {len(plan.legs)}，未安排 {len(plan.unassigned)}。",
            plan,
        )

    @staticmethod
    def _synthesize(calls: list[AgentToolCall]) -> str:
        lines = ["我是 Routenda Agent，已按消息内容规划并调用工具："]
        for call in calls:
            status = "OK" if call.ok else "ERROR"
            lines.append(f"\n【{call.name} · {status}】\n{call.output}")
        lines.append(
            "\n下一步：如果这是新拜访需求，请补齐供应商、厂区、日期范围、拜访时长、参与人和出发/返回约束；我会继续生成可确认的需求草稿和行程方案。"
        )
        return "\n".join(lines)
