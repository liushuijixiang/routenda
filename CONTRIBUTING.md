# Contributing

Thanks for working on Routenda. Keep changes small, tested, and aligned with the existing module boundaries.

## Development

```bash
cp .env.example .env.local
make setup
make lint
make test
```

Use mock adapters for normal development. Add real provider credentials only to `.env.local` or your shell environment.

## Pull Requests

- Describe the user-facing behavior change.
- Include tests for domain logic, adapters, API routes, or UI behavior when touched.
- Keep generated files, caches, local secrets, and provider data out of the commit.
- Update docs when adding an integration, configuration key, route, or workflow state.

## Code Style

- Backend: Ruff, strict mypy, Pydantic v2, SQLAlchemy 2.
- Frontend: TypeScript, Prettier, Next.js App Router.
- Integrations: isolate provider behavior behind adapters and return structured `ToolResult` values for external calls.
