# Architecture

## Runtime

The system is a modular monolith with five Compose services: Next.js Web,
FastAPI API, an outbox worker using the same application package, PostgreSQL
with PostGIS enabled, and Mailpit. The API and worker share domain and
infrastructure code but run as separate processes. Demo scripts select the
in-memory repository and deterministic adapters so they need no external
accounts.

## Module boundaries

- `domain`: Pydantic draft schema, entities, state-transition table, and pure
  validation. It has no framework or provider dependency.
- `application`: use-case services for tokens, transitions, reconciliation,
  outbox delivery, and context construction.
- `agent`: LangGraph intake, bounded LLM extraction, policy classification,
  Pydantic tool contracts, and approval-aware orchestration.
- `planning`: OR-Tools CP-SAT model and route-time abstraction.
- `infrastructure`: repository implementations and provider adapters.
- `api`: HTTP DTOs, RBAC checks, idempotency replay, and structured errors.
- `apps/web`: operational UI consuming only `/api/v1`.

`VisitRequirementDraft` is the canonical structured contract. FastAPI DTOs
alias it and the checked-in JSON Schema mirrors its fields and constraints.
The RJSF create form and SurveyJS change workflows submit that same shape.

## Persistence and consistency

`SQLAlchemyRepository` loads durable rows into the repository abstraction and
writes entity changes through explicit save methods. Alembic creates all core
tables, indexes, enums, and the PostGIS extension when PostgreSQL is used.
Durable idempotency records prevent duplicate mutation execution across API
process restarts. Appointment versions, requirement revisions, messages,
calendar conflicts, approvals, tool calls, and each requirement status
transition remain auditable.

External delivery uses the transactional-outbox boundary. The worker claims
rows with `FOR UPDATE SKIP LOCKED`, records attempts, applies bounded retry and
quiet-hour rules, and creates a human task after terminal failure or reminder
exhaustion. External callbacks and calendar changes never become trusted Agent
instructions without parsing and review.

## Main workflow

1. LangGraph extracts a draft in rule mode or through an OpenAI-compatible
   structured-output request, then reports missing slots.
2. A user confirms or edits the draft; the API persists the requirement and a
   revision.
3. First supplier contact is approval-gated by default. Approval issues a
   hashed, expiring public token and queues the message in the outbox.
4. Supplier availability submission stores windows and the inbound message,
   consumes the token, transitions state, and triggers replanning.
5. CP-SAT produces persisted alternatives. Accepting one creates tentative
   calendar holds and bindings.
6. Final confirmation, rescheduling, cancellation, and confirmed-record edits
   require impact previews and approval before provider writes.
7. Calendar and ERP results update local bindings/status; failures retain an
   actionable error or human task rather than silently succeeding.

## Security boundary

Roles are ordered `requester < coordinator < approver < admin`. The default
role is least-privileged. Public tokens are random, stored only as SHA-256
hashes, expire after 72 hours, are revocable, and are single-use. Contact PII is
masked for requesters. CORS origins are explicit. Provider secrets are read
from environment variables and never returned by health endpoints.
