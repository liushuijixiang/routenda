from typing import Any, TypedDict, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from visit_agent.agent.llm_gateway import LLMGateway
from visit_agent.agent.state import AgentState
from visit_agent.domain.models import VisitRequirementDraft
from visit_agent.infrastructure.db.repository import InMemoryRepository


class IntakeGraphState(TypedDict, total=False):
    text: str
    session_id: str
    draft: VisitRequirementDraft
    supplier_candidates: list[dict[str, Any]]
    missing_slots: list[str]


class SlotFillingGraph:
    def __init__(self, repo: InMemoryRepository, llm: LLMGateway) -> None:
        self.repo = repo
        self.llm = llm
        self.compiled_graph = self._build_graph()

    def receive_input(self, text: str) -> AgentState:
        result = cast(IntakeGraphState, self.compiled_graph.invoke({"text": text}))
        candidates = result["supplier_candidates"]
        return AgentState(
            session_id=result["session_id"],
            draft=result["draft"],
            missing_slots=result["missing_slots"],
            candidate_entities={"suppliers": candidates},
        )

    def merge_form(self, state: AgentState, patch: dict[str, Any]) -> AgentState:
        data = state.draft.model_dump()
        data.update(patch)
        state.draft = VisitRequirementDraft.model_validate(data)
        state.missing_slots = state.draft.missing_slots()
        return state

    def _build_graph(self) -> Any:
        workflow = StateGraph(IntakeGraphState)
        workflow.add_node("receive_input", self._receive_input)
        workflow.add_node("extract_structured_fields", self._extract_structured_fields)
        workflow.add_node("resolve_supplier_and_people", self._resolve_supplier_and_people)
        workflow.add_node("validate_with_pydantic", self._validate_draft)
        workflow.add_node("compute_missing_slots", self._compute_missing_slots)
        workflow.add_edge(START, "receive_input")
        workflow.add_edge("receive_input", "extract_structured_fields")
        workflow.add_edge("extract_structured_fields", "resolve_supplier_and_people")
        workflow.add_edge("resolve_supplier_and_people", "validate_with_pydantic")
        workflow.add_edge("validate_with_pydantic", "compute_missing_slots")
        workflow.add_edge("compute_missing_slots", END)
        return workflow.compile()

    def _receive_input(self, state: IntakeGraphState) -> IntakeGraphState:
        return {"session_id": str(uuid4()), "text": state["text"]}

    def _extract_structured_fields(
        self,
        state: IntakeGraphState,
    ) -> IntakeGraphState:
        return {"draft": self.llm.extract_visit_draft(state["text"])}

    def _resolve_supplier_and_people(
        self,
        state: IntakeGraphState,
    ) -> IntakeGraphState:
        draft = state["draft"]
        candidates = self._resolve_supplier_candidates(draft.supplier_name)
        if len(candidates) == 1:
            draft.supplier_id = candidates[0]["id"]
            site = next(
                (
                    site
                    for site in self.repo.sites.values()
                    if site.supplier_id == draft.supplier_id
                ),
                None,
            )
            draft.site_id = site.id if site else None
        return {"draft": draft, "supplier_candidates": candidates}

    def _validate_draft(self, state: IntakeGraphState) -> IntakeGraphState:
        draft = VisitRequirementDraft.model_validate(state["draft"])
        return {"draft": draft}

    def _compute_missing_slots(self, state: IntakeGraphState) -> IntakeGraphState:
        return {"missing_slots": state["draft"].missing_slots()}

    def _resolve_supplier_candidates(self, name: str | None) -> list[dict[str, Any]]:
        if not name:
            return []
        result: list[dict[str, Any]] = []
        for supplier in self.repo.suppliers.values():
            haystack = [supplier.display_name, supplier.legal_name, *supplier.aliases]
            if any(part in item or item in name for item in haystack for part in name.split("、")):
                result.append({"id": supplier.id, "display_name": supplier.display_name})
        return result[:5]
