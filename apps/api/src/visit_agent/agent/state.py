from dataclasses import dataclass, field
from typing import Any

from visit_agent.domain.models import VisitRequirementDraft


@dataclass
class AgentState:
    session_id: str
    draft: VisitRequirementDraft = field(default_factory=VisitRequirementDraft)
    missing_slots: list[str] = field(default_factory=list)
    candidate_entities: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    confirmed: bool = False
