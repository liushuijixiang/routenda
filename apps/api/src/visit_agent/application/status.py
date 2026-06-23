from __future__ import annotations

from visit_agent.domain.models import (
    AuditEvent,
    RequirementStatus,
    VisitRequirement,
    transition_status,
)


def transition_requirement(
    repo: object,
    requirement: VisitRequirement,
    target: RequirementStatus,
    *,
    actor: str,
    reason: str,
) -> RequirementStatus:
    """Apply and durably audit one legal requirement state transition."""
    before = requirement.status
    requirement.status = transition_status(before, target)
    repo.audit_event(  # type: ignore[attr-defined]
        AuditEvent(
            actor=actor,
            action="requirement_status_transition",
            entity="VisitRequirement",
            entity_id=requirement.id,
            before={"status": before.value},
            after={"status": target.value, "reason": reason},
        )
    )
    return target
