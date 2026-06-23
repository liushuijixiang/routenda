# Adapters

Adapters implement provider-neutral ports and return `ToolResult` with a
stable error code, human-readable message, retryability, and optional audit ID.
Configured HTTP adapters use shared timeout, bounded retry, and circuit-breaker
logic. Deterministic mocks implement the same contracts for tests and demos.

## ERP

`MockERPAdapter` is the default. `ERPNextAdapter` implements Frappe token
authentication and `/api/resource` list/create/update operations for Supplier,
Contact, Address, and Visit Requirement records. `ERPNextFieldMap` centralizes
DocType and custom-field names so a deployment can change mappings without
changing workflow code. ERP remains authoritative for supplier master data;
discovered changes enter a `MasterDataChangeRequest` before write-back.

## Calendar

`MockCalendarAdapter` creates deterministic event IDs. `IcsCalendarAdapter`
imports busy intervals and exports appointments without a cloud account.
`MicrosoftGraphCalendarAdapter` implements client-credential OAuth token
caching, `getSchedule`, tentative event creation, confirmation/update/cancel,
and paginated delta synchronization with etags. External etag divergence
creates a `CalendarConflict` and approval instead of overwriting either side.

## Communication

Contact workflows first create an outbox record. The worker's SMTP delivery is
compatible with Mailpit locally and any configured SMTP relay. Reminder
interval, maximum count, quiet hours, and timezone are configurable. An inbound
parser extracts candidate dates, contact/address changes, and reschedule
signals into reviewable structured data; message text cannot invoke tools.

## Geocoding and routing

`MockGeocoder` and Haversine estimates are offline defaults. The Nominatim
adapter sends an identifying user agent, rate-limits requests, normalizes
confidence, and caches results. The OSRM adapter implements Table matrices and
Route geometry; provider/profile/rounded coordinates/date bucket form the
cache key. The route matrix is passed into CP-SAT rather than used only for
display.

## LLM

Without `OPENAI_API_KEY`, deterministic extraction rules are used. With a key,
the gateway calls an OpenAI-compatible `/chat/completions` endpoint using JSON
Schema structured output and validates the response as `VisitRequirementDraft`.
Malformed or unavailable responses receive bounded retries and then fall back
to rules. LLM output is data only and still passes domain validation and policy
checks.

See `integrations.md` for deployment variables and provider-specific notes.
