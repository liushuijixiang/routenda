from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str = ""
    retryable: bool = False
    audit_id: str = ""

    @classmethod
    def success(cls, data: Any = None, message: str = "") -> "ToolResult":
        return cls(ok=True, data=data, message=message, audit_id=str(uuid4()))

    @classmethod
    def failure(cls, code: str, message: str, retryable: bool = False) -> "ToolResult":
        return cls(
            ok=False, error_code=code, message=message, retryable=retryable, audit_id=str(uuid4())
        )
