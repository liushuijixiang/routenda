import asyncio
import unittest

from visit_agent.agent.tools.result import ToolResult
from visit_agent.infrastructure.adapters.geo import OSRMRouteMatrix
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker, resilient_tool_call


class ResilienceTests(unittest.TestCase):
    def test_retry_recovers_after_transient_failure(self):
        async def run():
            calls = 0

            async def operation():
                nonlocal calls
                calls += 1
                if calls == 1:
                    return ToolResult.failure("temporary", "temporary failure", retryable=True)
                return ToolResult.success({"calls": calls})

            breaker = CircuitBreaker("test", failure_threshold=2)
            result = await resilient_tool_call(
                "test.operation", operation, breaker, attempts=2, timeout_seconds=1
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.data["calls"], 2)
            self.assertTrue(breaker.allow())

        asyncio.run(run())

    def test_circuit_opens_after_failures(self):
        async def run():
            async def operation():
                return ToolResult.failure("temporary", "temporary failure", retryable=True)

            breaker = CircuitBreaker("test", failure_threshold=1, reset_seconds=60)
            first = await resilient_tool_call(
                "test.operation", operation, breaker, attempts=1, timeout_seconds=1
            )
            second = await resilient_tool_call(
                "test.operation", operation, breaker, attempts=1, timeout_seconds=1
            )
            self.assertFalse(first.ok)
            self.assertFalse(second.ok)
            self.assertEqual(second.error_code, "circuit_open")

        asyncio.run(run())

    def test_timeout_is_retryable(self):
        async def run():
            async def operation():
                await asyncio.sleep(0.05)
                return ToolResult.success()

            breaker = CircuitBreaker("test", failure_threshold=2)
            result = await resilient_tool_call(
                "test.timeout", operation, breaker, attempts=1, timeout_seconds=0.001
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "timeout")
            self.assertTrue(result.retryable)

        asyncio.run(run())

    def test_osrm_adapter_uses_resilience_contract(self):
        async def run():
            adapter = OSRMRouteMatrix("")
            first = await adapter.duration_minutes([(31.2, 121.3), (31.3, 120.6)])
            second = await adapter.duration_minutes([(31.2, 121.3), (31.3, 120.6)])
            self.assertFalse(first.ok)
            self.assertEqual(first.error_code, "missing_base_url")
            self.assertFalse(second.ok)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
