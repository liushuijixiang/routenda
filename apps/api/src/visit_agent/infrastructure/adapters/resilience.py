from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

from visit_agent.agent.tools.result import ToolResult


T = TypeVar("T")


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    reset_seconds: float = 30.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if monotonic() - self.opened_at >= self.reset_seconds:
            self.failures = 0
            self.opened_at = None
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = monotonic()


async def resilient_tool_call(
    name: str,
    operation: Callable[[], Awaitable[ToolResult]],
    breaker: CircuitBreaker,
    *,
    attempts: int = 3,
    timeout_seconds: float = 5.0,
    backoff_seconds: float = 0.05,
) -> ToolResult:
    if not breaker.allow():
        return ToolResult.failure("circuit_open", f"{name} circuit is open", retryable=True)

    last: ToolResult | None = None
    for index in range(attempts):
        try:
            result = await asyncio.wait_for(operation(), timeout=timeout_seconds)
        except TimeoutError:
            result = ToolResult.failure("timeout", f"{name} timed out", retryable=True)
        except Exception as exc:
            result = ToolResult.failure("adapter_error", f"{name} failed: {exc}", retryable=True)

        if result.ok:
            breaker.record_success()
            return result

        last = result
        if not result.retryable:
            breaker.record_failure()
            return result
        if index < attempts - 1:
            await asyncio.sleep(backoff_seconds * (2**index))

    breaker.record_failure()
    return last or ToolResult.failure("adapter_error", f"{name} failed", retryable=True)
