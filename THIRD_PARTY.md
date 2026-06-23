# Third-Party Boundaries

## Runtime dependencies

- Backend: FastAPI, Pydantic, SQLAlchemy, Alembic, psycopg, HTTPX, LangGraph,
  OR-Tools, Uvicorn, Ruff, mypy, and pytest.
- Frontend: Next.js, React, RJSF, AJV, SurveyJS, MapLibre GL JS, Lucide React,
  TypeScript, Vitest, Playwright, and Prettier.
- Local infrastructure: PostgreSQL/PostGIS and Mailpit.
- Optional network services: ERPNext/Frappe, Microsoft Graph, Nominatim, OSRM,
  an OpenAI-compatible API, and an SMTP relay.

Dependency versions are resolved by `apps/api/uv.lock` and `pnpm-lock.yaml`.
Their upstream license files remain authoritative; deployments must review the
locked transitive dependency set and the terms of every configured network
service. Public Nominatim and public tile services also impose usage policies
that make them unsuitable for unrestricted production bulk traffic.

## Reference-only projects

Rasa, Twenty, Directus, OpenRefine, Timefold, VROOM, GraphHopper, Valhalla,
Rallly, Cal.com, and Hello-Agents informed architecture or UX evaluation only.
No source or assets from those projects are copied into this repository.

GPL, AGPL, SSPL/source-available, non-commercial Creative Commons, or other
reciprocal code must not be vendored or linked into the distributed product
without a separate legal and deployment review. The CSV reconciliation format
is compatible with external OpenRefine use but contains no OpenRefine code.
