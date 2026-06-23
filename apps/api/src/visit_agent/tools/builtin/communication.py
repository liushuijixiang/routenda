from __future__ import annotations

from typing import Any

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult


class CommunicationProtocolTool(BaseTool):
    name = "communication_protocol"
    description = "Create simple MCP/A2A/ANP-style envelopes for agent communication."

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        protocol = str(args.get("protocol", "mcp")).lower()
        target = str(args.get("target", "agent"))
        intent = str(args.get("intent", "message"))
        payload = args.get("payload", {})
        if protocol not in {"mcp", "a2a", "anp"}:
            return ToolResult.failure("unsupported_protocol", f"暂不支持协议：{protocol}")
        envelope = {
            "protocol": protocol,
            "target": target,
            "intent": intent,
            "payload": payload,
        }
        return ToolResult.success(
            f"已生成 {protocol.upper()} 通讯信封：target={target}, intent={intent}",
            envelope,
        )
