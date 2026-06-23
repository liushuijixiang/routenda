from __future__ import annotations

import ast
import operator
from typing import Any, Callable

from visit_agent.tools.base import BaseTool, ToolContext, ToolResult


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Safely evaluate simple arithmetic expressions."

    _operators: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    _unary: dict[type[ast.AST], Callable[[Any], Any]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        expression = str(args.get("expression", "")).strip()
        if not expression:
            return ToolResult.failure("missing_expression", "缺少计算表达式。")
        try:
            value = self._eval(ast.parse(expression, mode="eval").body)
        except Exception as exc:  # noqa: BLE001 - user-facing tool error
            return ToolResult.failure("calculator_error", f"计算失败：{exc}")
        return ToolResult.success(f"{expression} = {value}", value)

    def _eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return node.value
        if isinstance(node, ast.BinOp):
            op = self._operators.get(type(node.op))
            if op is None:
                raise ValueError("unsupported operator")
            return op(self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.UnaryOp):
            unary_op = self._unary.get(type(node.op))
            if unary_op is None:
                raise ValueError("unsupported unary operator")
            return unary_op(self._eval(node.operand))
        raise ValueError("unsupported expression")
