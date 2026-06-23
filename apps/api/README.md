# Routenda API

FastAPI service, LangGraph agent, planning model, provider adapters, Alembic migrations, and backend tests.

```bash
cd apps/api
uv sync
PYTHONPATH=src uv run pytest -q
uv run ruff check src tests ../../scripts
PYTHONPATH=src uv run mypy src
```

Key modules:

- `visit_agent.domain`: pure entities, validation, and state transitions.
- `visit_agent.agent`: intake graph, LLM gateway, policy checks, and tool registry.
- `visit_agent.planning`: OR-Tools CP-SAT itinerary solver.
- `visit_agent.infrastructure`: repositories and provider adapters.
- `visit_agent.api`: HTTP routes, DTOs, RBAC checks, idempotency, and error envelopes.
