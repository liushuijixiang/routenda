from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult


class SearchTool(BaseTool):
    name = "search"
    description = "Search the web or configured search adapter."

    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider

    async def arun(
        self,
        args: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult.failure("missing_query", "缺少搜索关键词。")
        if self.provider is None:
            return ToolResult.failure("search_unavailable", "搜索服务未配置。")
        result = await self.provider.search(query)
        if not result.ok:
            return ToolResult.failure(result.error_code or "search_error", result.message or "搜索失败")
        payload = result.data or {}
        organic = payload.get("organic", []) if isinstance(payload, dict) else []
        lines: list[str] = []
        for item in organic[:3]:
            title = item.get("title", "未命名结果")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            lines.append(f"- {title}\n  {snippet}\n  {link}".rstrip())
        return ToolResult.success("\n".join(lines) if lines else "搜索没有返回可用结果。", payload)
