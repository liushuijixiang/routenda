from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult
from visit_agent.tools.store import AgentSQLiteStore


class AgenticRLTool(BaseTool):
    name = "agentic_rl"
    description = "Record feedback and derive lightweight policy hints for agent improvement."

    def __init__(self, store: AgentSQLiteStore) -> None:
        self.store = store

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        operation = str(args.get("operation", "record")).lower()
        if operation in {"record", "feedback"}:
            task = str(args.get("task", "")).strip() or "chat"
            note = str(args.get("note", "")).strip()
            signal = float(args.get("signal", args.get("reward", 0.0)))
            feedback_id = self.store.add_feedback(
                task,
                signal,
                note,
                metadata={"source": args.get("source", "tool")},
            )
            return ToolResult.success(f"已记录 Agentic-RL 反馈：{feedback_id}", {"id": feedback_id})
        summary = self.store.feedback_summary(limit=int(args.get("limit", 20)))
        if summary["count"] == 0:
            return ToolResult.success("还没有可用于策略改进的反馈。", summary)
        hint = (
            f"近期反馈 {summary['count']} 条，平均信号 {summary['average_signal']:.2f}。"
            "低分任务需要减少模板化回复，优先澄清缺失字段并给出下一步。"
        )
        return ToolResult.success(hint, summary)
