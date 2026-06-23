from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult
from visit_agent.tools.store import AgentSQLiteStore


class ContextEngineeringTool(BaseTool):
    name = "context_engineering"
    description = "Build compact task context from query, memory, RAG knowledge and constraints."

    def __init__(self, store: AgentSQLiteStore) -> None:
        self.store = store

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        query = str(args.get("query", args.get("text", ""))).strip()
        scope = str(args.get("scope") or context.conversation_id if context else "global")
        if not query:
            return ToolResult.failure("missing_query", "缺少上下文构建 query。")
        memories = self.store.search_memories(query, scope=scope, limit=3)
        docs = self.store.search_documents(query, limit=3)
        sections = [f"当前任务：{query}"]
        if memories:
            sections.append("相关记忆：\n" + "\n".join(f"- {item.text}" for item in memories))
        if docs:
            sections.append("相关知识：\n" + "\n".join(f"- {item.text}" for item in docs))
        sections.append("上下文策略：优先使用用户明确事实；缺失关键信息时只问最少澄清问题。")
        context_text = "\n\n".join(sections)
        return ToolResult.success(context_text, {"memories": memories, "documents": docs})
