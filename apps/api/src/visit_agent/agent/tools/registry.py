from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from visit_agent.agent.policy import Risk, classify_action
from visit_agent.agent.tools.result import ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    action: str
    read_write: str
    risk: Risk
    idempotent: bool
    args_model: type[BaseModel]
    handler: Callable[..., ToolResult]


class ToolRegistry:
    def __init__(
        self,
        audit_execution: Callable[[ToolSpec, ToolResult], None] | None = None,
    ) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._audit_execution = audit_execution

    def register(
        self,
        name: str,
        description: str,
        action: str,
        read_write: str,
        idempotent: bool,
        args_model: type[BaseModel],
        handler: Callable[..., ToolResult],
    ) -> None:
        if read_write not in {"read", "write"}:
            raise ValueError("read_write must be read or write")
        self._tools[name] = ToolSpec(
            name,
            description,
            action,
            read_write,
            classify_action(action),
            idempotent,
            args_model,
            handler,
        )

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        approved: bool = False,
    ) -> ToolResult:
        spec = self.get(name)
        if spec.risk == Risk.FORBIDDEN:
            return self._finish(
                spec,
                ToolResult.failure("forbidden", f"Tool action is forbidden: {spec.action}"),
            )
        if spec.read_write == "write" and spec.risk in {Risk.CONFIRM, Risk.HIGH} and not approved:
            return self._finish(
                spec,
                ToolResult.failure(
                    "approval_required", f"Tool action requires approval: {spec.action}"
                ),
            )
        try:
            args = spec.args_model.model_validate(payload)
        except ValidationError as exc:
            return self._finish(spec, ToolResult.failure("validation_error", str(exc)))
        return self._finish(spec, spec.handler(**args.model_dump()))

    def _finish(self, spec: ToolSpec, result: ToolResult) -> ToolResult:
        if self._audit_execution:
            self._audit_execution(spec, result)
        return result
