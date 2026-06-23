# Routenda

AI-powered Appointment & Visit Coordinator.

Routenda is an open-source Alpha product for coordinating business visits across requesters, coordinators, suppliers, calendars, and route planning. It turns an informal visit request into a structured requirement, collects supplier availability, proposes feasible itineraries, and keeps approvals, messages, calendar writes, and ERP updates auditable.

The product name comes from route + agenda. The code still uses the internal Python package name `visit_agent` to keep imports stable during the Alpha phase.

## What It Does

- Intake: extracts structured visit requirements from natural-language requests, with rule mode available when no LLM key is configured.
- Supplier coordination: queues first-contact messages, issues single-use public availability links, parses replies, and creates human tasks when automation should stop.
- Planning: uses OR-Tools CP-SAT with availability windows, required participants, travel buffers, return deadlines, locked appointments, and route-time providers.
- Approvals: gates first external contact and high-risk changes such as confirmed reschedules or cancellations.
- Integrations: supports ERPNext, Microsoft Graph Calendar, Feishu Calendar, SMTP/Mailpit, Nominatim, OSRM, Serper, and an Excel/CSV ERP substitute for early pilots.
- Web UI: includes requirement creation, supplier directory, itinerary review, approval queue, public availability submission, and integration health pages.

## Status

Routenda is Alpha software. It is suitable for local evaluation, integration spikes, and controlled pilots. Production deployment still needs real identity integration, hardened authorization, provider-specific field mapping, and operational monitoring.

## Quick Start

Prerequisites:

- Python 3.12+
- `uv`
- Node.js and `pnpm`
- Docker with either `docker compose` or `docker-compose`

```bash
cp .env.example .env.local
make setup
make lint
make test
make demo
make dev
```

Local services from `make dev`:

- Web: `http://localhost:3000`
- API: `http://localhost:8000`
- Mailpit: `http://localhost:8025`

The default demo path uses in-memory data and mock adapters, so no external account is required.

## Configuration

Use `.env.local` for local secrets. Do not commit it.

Common integration switches:

- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`: OpenAI-compatible LLM extraction.
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`: alternate OpenAI-compatible chat-completions endpoint.
- `ERP_PROVIDER=excel`, `ERP_EXCEL_PATH=data/erpnext-suppliers.csv`: local ERPNext-style CSV or simple XLSX substitute.
- `ERP_NEXT_BASE_URL`, `ERP_NEXT_API_KEY`, `ERP_NEXT_API_SECRET`: ERPNext REST adapter.
- `CALENDAR_PROVIDER=feishu`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`: Feishu Calendar adapter.
- `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`: Microsoft Graph Calendar adapter.
- `SEARCH_PROVIDER=serper`, `SERPER_API_KEY`, `SERPER_URL`: Serper search adapter.
- `ROUTING_PROVIDER=osrm`, `OSRM_BASE_URL`: OSRM routing and matrix provider.
- `GEOCODING_PROVIDER=nominatim`, `NOMINATIM_BASE_URL`, `NOMINATIM_USER_AGENT`: Nominatim geocoding provider.
- `DATABASE_URL`: SQLAlchemy repository, for example PostgreSQL/PostGIS in Compose.

See [docs/integrations.md](docs/integrations.md) for provider details.

## Repository Layout

- [apps/api](apps/api): FastAPI API, LangGraph agent, planning model, adapters, tests, and Alembic migrations.
- [apps/web](apps/web): Next.js operational UI.
- [data](data): sample supplier/site/contact data for the Excel ERP substitute.
- [docs](docs): architecture, integration, planning, state-machine, and adapter notes.
- [schemas](schemas): JSON Schema and supplier update survey schemas.
- [scripts](scripts): demo and seed scripts.

## Quality Gates

```bash
make lint
make test
```

Backend checks run Ruff, strict mypy, and pytest. Frontend checks run TypeScript, Prettier, Vitest, smoke tests, and Playwright.

## License

Routenda is released under the MIT License. See [LICENSE](LICENSE).
