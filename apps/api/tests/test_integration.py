import asyncio
import unittest

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.domain.models import RequirementStatus, transition_status
from visit_agent.infrastructure.adapters.calendar import MockCalendarAdapter
from visit_agent.infrastructure.adapters.erp import MockERPAdapter
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


class IntegrationTests(unittest.TestCase):
    def test_create_to_confirm_flow(self):
        async def run():
            repo = seed_demo(InMemoryRepository())
            agent = VisitCoordinatorAgent(repo)
            intake = agent.intake(
                "下周去苏州看 A 供应商，A 优先，王经理最好参加，周四 18 点前回上海。"
            )
            supplier = next(iter(repo.suppliers.values()))
            site = next(s for s in repo.sites.values() if s.supplier_id == supplier.id)
            confirmed = agent.confirm_requirement(
                intake.data["session_id"],
                {
                    "supplier_id": supplier.id,
                    "site_id": site.id,
                    "duration_minutes": 90,
                    "origin": "上海虹桥酒店",
                },
            )
            self.assertTrue(confirmed.ok)
            await agent.contact_supplier(confirmed.data.id, approved=True)
            token = next(iter(repo.outbox.values())).payload["token"]
            agent.submit_availability(confirmed.data.id, token=token)
            plan = agent.plan([confirmed.data.id]).data
            self.assertGreaterEqual(len(plan.legs), 1)

        asyncio.run(run())

    def test_calendar_and_erp_contracts(self):
        async def run():
            repo = seed_demo(InMemoryRepository())
            erp = MockERPAdapter(repo)
            cal = MockCalendarAdapter(repo.people_busy)
            supplier = next(iter(repo.suppliers.values()))
            self.assertTrue((await erp.list_sites(supplier.id)).ok)
            self.assertTrue((await cal.query_busy(["王经理"], None, None)).ok)

        asyncio.run(run())

    def test_reschedule_preview_and_approval(self):
        repo = seed_demo(InMemoryRepository())
        agent = VisitCoordinatorAgent(repo)
        req = next(iter(repo.requirements.values()))
        preview = agent.solver.impact_preview(req.id, {"site_id": "new"})
        approval = agent.request_high_risk("move_confirmed_appointment", req.id, preview)
        self.assertEqual(approval.status, "pending")
        self.assertTrue(repo.audit)

    def test_external_conflict_does_not_overwrite(self):
        async def run():
            cal = MockCalendarAdapter({})
            result = await cal.sync_external_changes()
            self.assertEqual(result.data["conflicts"], [])

        asyncio.run(run())

    def test_cancel_confirmed_requires_approval(self):
        repo = seed_demo(InMemoryRepository())
        agent = VisitCoordinatorAgent(repo)
        req = next(iter(repo.requirements.values()))
        req.status = RequirementStatus.READY_TO_CONTACT
        req.status = transition_status(req.status, RequirementStatus.CONTACTED)
        approval = agent.request_high_risk(
            "cancel_confirmed_appointment", req.id, {"after": "CANCELLATION_REQUESTED"}
        )
        self.assertEqual(approval.risk, "high")


if __name__ == "__main__":
    unittest.main()
