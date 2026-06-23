from typing import Any

from visit_agent.infrastructure.db.repository import InMemoryRepository


class ContextBuilder:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo

    def for_requirement(self, requirement_id: str) -> dict[str, Any]:
        req = self.repo.requirements[requirement_id]
        supplier = self.repo.suppliers.get(req.draft.supplier_id or "")
        site = self.repo.sites.get(req.draft.site_id or "")
        windows = [w for w in self.repo.availability if w.requirement_id == requirement_id]
        thread_ids = {
            thread.id
            for thread in self.repo.conversations.values()
            if thread.requirement_id == requirement_id
        }
        messages = [
            message for message in self.repo.messages.values() if message.thread_id in thread_ids
        ]
        appointments = [
            item
            for item in self.repo.appointments.values()
            if item.requirement_id == requirement_id
        ]
        return {
            "requirement": req,
            "supplier": supplier,
            "site": site,
            "availability": windows[:5],
            "people_busy": {
                person: self.repo.people_busy.get(person, [])[:10]
                for person in req.draft.required_people
            },
            "messages": messages[-10:],
            "appointments": appointments,
        }
