# State Machines

`VisitRequirement` transitions are validated by `ALLOWED_TRANSITIONS` in the
domain module. `transition_requirement` is the application entry point: it
applies one legal transition and writes one `requirement_status_transition`
audit event with actor and reason. Direct workflow assignment is prohibited.

| From | Allowed targets |
| --- | --- |
| `DRAFT` | `NEED_MORE_INFORMATION`, `READY_TO_CONTACT`, `CANCELLED` |
| `NEED_MORE_INFORMATION` | `READY_TO_CONTACT`, `CANCELLED` |
| `READY_TO_CONTACT` | `CONTACTED`, `CANCELLED` |
| `CONTACTED` | `WAITING_REPLY`, `CANCELLED` |
| `WAITING_REPLY` | `CANDIDATES_RECEIVED`, `CANCELLED` |
| `CANDIDATES_RECEIVED` | `INTERNAL_APPROVAL`, `RESCHEDULE_REQUESTED`, `CANCELLED` |
| `INTERNAL_APPROVAL` | `TENTATIVE_HOLD`, `CANCELLED` |
| `TENTATIVE_HOLD` | `CONFIRMED`, `RESCHEDULE_REQUESTED`, `CANCELLED` |
| `CONFIRMED` | `RESCHEDULE_REQUESTED`, `CANCELLATION_REQUESTED`, `COMPLETED` |
| `RESCHEDULE_REQUESTED` | `CANDIDATES_RECEIVED`, `CANCELLED` |
| `CANCELLATION_REQUESTED` | `CANCELLED` |
| `CANCELLED` | `NEED_MORE_INFORMATION`, `READY_TO_CONTACT` (explicit resume) |

`FAILED` and `COMPLETED` are terminal. Pause does not alter workflow status;
resume clears the pause marker, and an explicitly resumed cancelled draft
returns to the appropriate intake state.

## Approval states

Approval records move from `pending` to `approved`, `rejected`, or
`execution_failed`. Approval creation has no external side effect. Only the
approve endpoint executes first contact, confirmed-record changes, final
calendar confirmation, rescheduling, cancellation, or ERP master-data writes.
The executor rechecks that its target still exists. Idempotency records prevent
replaying an approval write.

Appointments separately track `tentative`, `confirmed`, `rescheduled`, and
`cancelled`; every mutation writes an `AppointmentVersion`. Calendar bindings
retain provider ID, calendar ID, etag, and last-sync time.
