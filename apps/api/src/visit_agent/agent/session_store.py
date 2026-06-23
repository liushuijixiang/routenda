from datetime import datetime
from typing import Any, cast

from fastapi.encoders import jsonable_encoder

from visit_agent.agent.state import AgentState
from visit_agent.domain.models import VisitRequirementDraft
from visit_agent.infrastructure.db.repository import InMemoryRepository


class SessionStore:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo

    def save(self, state: AgentState) -> None:
        payload = cast(
            dict[str, Any],
            jsonable_encoder(
                {
                    "session_id": state.session_id,
                    "draft": state.draft.model_dump(),
                    "missing_slots": state.missing_slots,
                    "candidate_entities": state.candidate_entities,
                    "confirmed": state.confirmed,
                }
            ),
        )
        self.repo.save_agent_session(state.session_id, payload)

    def get(self, session_id: str) -> AgentState | None:
        payload = self.repo.get_agent_session(session_id)
        if not payload:
            return None
        draft_values = dict(cast(dict[str, Any], payload["draft"]))
        for field_name in ("date_start", "date_end", "return_deadline"):
            value = draft_values.get(field_name)
            if isinstance(value, str):
                draft_values[field_name] = datetime.fromisoformat(value)
        return AgentState(
            session_id=str(payload["session_id"]),
            draft=VisitRequirementDraft(**draft_values),
            missing_slots=list(cast(list[str], payload.get("missing_slots", []))),
            candidate_entities=cast(
                dict[str, list[dict[str, Any]]],
                payload.get("candidate_entities", {}),
            ),
            confirmed=bool(payload.get("confirmed", False)),
        )
