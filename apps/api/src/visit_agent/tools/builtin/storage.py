from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult
from visit_agent.tools.store import AgentSQLiteStore


class StorageTool(BaseTool):
    name = "storage"
    description = "Persistent key-value storage for agent state and settings."

    def __init__(self, store: AgentSQLiteStore) -> None:
        self.store = store

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        operation = str(args.get("operation", "get")).lower()
        namespace = str(args.get("namespace", "agent"))
        key = str(args.get("key", "")).strip()
        if not key:
            return ToolResult.failure("missing_key", "缺少 storage key。")
        if operation == "set":
            self.store.set_value(namespace, key, args.get("value"))
            return ToolResult.success(f"已保存 {namespace}/{key}。")
        if operation == "get":
            value = self.store.get_value(namespace, key)
            if value is None:
                return ToolResult.success(f"{namespace}/{key} 暂无记录。")
            return ToolResult.success(f"{namespace}/{key} = {value}", value)
        return ToolResult.failure("unsupported_operation", f"不支持的 storage 操作：{operation}")
