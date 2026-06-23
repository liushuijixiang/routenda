# Planning Model

The planner persists an `ItineraryPlan` containing ordered legs, metrics,
unassigned reason codes, and an explanation. OR-Tools CP-SAT is the primary
solver; a labeled deterministic greedy fallback is available only when
OR-Tools cannot import.

## Variables and constraints

For each requirement the model creates an optional assignment variable and a
choice among feasible supplier availability windows. Start/end variables obey
the visit duration, requirement date range, supplier window, site reception
hours, and internal participant busy intervals. Pairwise ordering literals add
the selected route-matrix travel duration plus a configurable safety buffer.

Hard locks preserve an accepted appointment's start and site. Invalid or
unconfirmed sites are not schedulable. The final selected visit must leave
enough route time to reach the destination by `return_deadline`. Requirements
may be omitted rather than making the whole model infeasible.

## Objective

The lexicographic intent is encoded by weighted terms: maximize assigned
priority first, prefer higher-ranked supplier windows, penalize early/late
placement and unnecessary waiting, then reduce travel. Existing accepted legs
receive a stability preference so replanning changes as little as possible.
`can_move_existing=false` and hard locks prevent movement rather than merely
penalizing it.

## Outputs and replanning

Metrics include travel, waiting, safety buffer, and return margin in minutes.
Unassigned items use stable reason codes such as
`site_missing_or_unconfirmed`, `no_supplier_availability`, and
`time_window_or_return_deadline_infeasible`. Impact preview compares affected
fields and appointments before any confirmed change. Alternative generation
excludes the first solution and optimizes for minimum change, yielding a
distinct persisted `minimal_change` plan when feasible.

The current model is intended for small, single-coordinator regional visit
batches. Large fleets, parallel field teams, capacity constraints, and
multi-day vehicle routing would require a VRPTW model or dedicated routing
engine.
