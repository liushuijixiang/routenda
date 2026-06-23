from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult
from visit_agent.tools.store import AgentSQLiteStore


class RAGTool(BaseTool):
    name = "rag"
    description = "Ingest and retrieve local knowledge snippets for retrieval-augmented answers."

    def __init__(self, store: AgentSQLiteStore) -> None:
        self.store = store

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        operation = str(args.get("operation", "search")).lower()
        if operation in {"ingest", "add", "index"}:
            text = str(args.get("text", "")).strip()
            if not text:
                return ToolResult.failure("missing_text", "缺少要入库的文档内容。")
            doc_id = self.store.add_document(
                text,
                source=str(args.get("source", "manual")),
                title=str(args.get("title", "")),
                metadata={"source": args.get("source", "manual")},
            )
            return ToolResult.success(f"已写入 RAG 文档：{doc_id}", {"id": doc_id})
        query = str(args.get("query", args.get("text", ""))).strip()
        if not query:
            return ToolResult.failure("missing_query", "缺少 RAG 检索 query。")
        hits = self.store.search_documents(query, limit=int(args.get("limit", 5)))
        if not hits:
            return ToolResult.success("没有检索到相关知识片段。", [])
        lines = [f"- {hit.text}" for hit in hits]
        return ToolResult.success("RAG 检索结果：\n" + "\n".join(lines), hits)
