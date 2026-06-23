from contextlib import contextmanager
from datetime import datetime, timezone
from time import perf_counter
from collections.abc import Iterator

from visit_agent.domain.models import AuditEvent
from visit_agent.infrastructure.db.repository import InMemoryRepository


@contextmanager
def trace_tool(
    repo: InMemoryRepository,
    actor: str,
    tool_name: str,
    entity_id: str,
) -> Iterator[None]:
    start = perf_counter()
    try:
        yield
        status = "ok"
    except Exception:
        status = "error"
        raise
    finally:
        repo.audit_event(
            AuditEvent(
                actor=actor,
                action=f"tool:{tool_name}:{status}",
                entity="tool_call",
                entity_id=entity_id,
                after={
                    "elapsed_ms": int((perf_counter() - start) * 1000),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
