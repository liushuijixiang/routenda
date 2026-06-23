from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult
from visit_agent.tools.store import AgentSQLiteStore


class MemoryTool(BaseTool):
    name = "memory"
    description = "Read and write long-term user or conversation memory."

    def __init__(self, store: AgentSQLiteStore) -> None:
        self.store = store

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        operation = str(args.get("operation", "search")).lower()
        scope = str(args.get("scope") or context.conversation_id if context else "global")
        if operation in {"remember", "add", "write"}:
            text = str(args.get("text", "")).strip()
            if not text:
                return ToolResult.failure("missing_text", "缺少要记住的内容。")
            memory_id = self.store.add_memory(
                text,
                scope=scope,
                importance=float(args.get("importance", 0.5)),
                metadata={"source": args.get("source", "tool")},
            )
            return ToolResult.success(f"已写入长期记忆：{memory_id}", {"id": memory_id})
        query = str(args.get("query", args.get("text", ""))).strip()
        if not query:
            return ToolResult.failure("missing_query", "缺少记忆检索 query。")
        hits = self.store.search_memories(query, scope=scope, limit=int(args.get("limit", 5)))
        if not hits:
            return ToolResult.success("没有检索到相关长期记忆。", [])
        lines = [f"- {hit.text}" for hit in hits]
        return ToolResult.success("相关长期记忆：\n" + "\n".join(lines), hits)
