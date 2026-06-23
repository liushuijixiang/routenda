import unittest
import json
from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel

from visit_agent.agent.agent import VisitCoordinatorAgent
from visit_agent.application.status import transition_requirement
from visit_agent.agent.policy import Risk, classify_action
from visit_agent.domain.models import (
    Appointment,
    AvailabilityWindow,
    RequirementStatus,
    VisitRequirementDraft,
    day_window,
    transition_status,
)
from visit_agent.infrastructure.adapters.geo import BasicAddressNormalizer
from visit_agent.infrastructure.adapters.communication import parse_inbound_reply
from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo
from visit_agent.planning.solver import ORIGIN_POINT, ItinerarySolver, TravelTimes


class DomainTests(unittest.TestCase):
    def test_inbound_reply_parser_extracts_reviewable_business_signals(self):
        parsed = parse_inbound_reply(
            "需要改期，新联系人：张经理，新地址：苏州工业园 8 号，"
            "2026-06-25T09:00:00+08:00 到 2026-06-25T11:00:00+08:00"
        )

        self.assertTrue(parsed["reschedule_requested"])
        self.assertEqual(parsed["contact_change"], "张经理")
        self.assertEqual(parsed["address_change"], "苏州工业园 8 号")
        self.assertEqual(len(parsed["candidate_windows"]), 1)
        self.assertTrue(parsed["needs_human_review"])
        self.assertFalse(parsed["trusted_as_instruction"])

    def test_draft_validation_and_missing_slots(self):
        with self.assertRaises(ValueError):
            VisitRequirementDraft(
                supplier_id="s", date_start=day_window(0, 9), date_end=day_window(0, 8)
            )
        draft = VisitRequirementDraft(
            supplier_id="s", date_start=day_window(0, 9), date_end=day_window(0, 18)
        )
        self.assertIn("site_id", draft.missing_slots())

    def test_state_machine(self):
        self.assertEqual(
            transition_status(RequirementStatus.DRAFT, RequirementStatus.READY_TO_CONTACT),
            RequirementStatus.READY_TO_CONTACT,
        )
        with self.assertRaises(ValueError):
            transition_status(RequirementStatus.DRAFT, RequirementStatus.CONFIRMED)

    def test_checked_in_schema_matches_canonical_draft_fields(self):
        schema_path = Path(__file__).parents[3] / "schemas" / "visit-requirement.schema.json"
        checked_in = json.loads(schema_path.read_text(encoding="utf-8"))
        canonical = VisitRequirementDraft.model_json_schema()

        self.assertEqual(set(checked_in["properties"]), set(canonical["properties"]))
        self.assertFalse(checked_in["additionalProperties"])

    def test_each_requirement_transition_emits_audit_event(self):
        repo = seed_demo(InMemoryRepository())
        requirement = next(iter(repo.requirements.values()))
        requirement.status = RequirementStatus.DRAFT

        transition_requirement(
            repo,
            requirement,
            RequirementStatus.READY_TO_CONTACT,
            actor="test",
            reason="contract_test",
        )

        event = repo.audit[-1]
        self.assertEqual(event.action, "requirement_status_transition")
        self.assertEqual(event.before, {"status": "DRAFT"})
        self.assertEqual(
            event.after,
            {"status": "READY_TO_CONTACT", "reason": "contract_test"},
        )

    def test_policy_forbidden_negotiation(self):
        self.assertEqual(classify_action("negotiation"), Risk.FORBIDDEN)
        self.assertEqual(classify_action("cancel_confirmed_appointment"), Risk.HIGH)

    def test_tool_registry_validates_structured_args_and_blocks_forbidden_action(self):
        repo = seed_demo(InMemoryRepository())
        agent = VisitCoordinatorAgent(repo)

        invalid = agent.tools.execute("search_suppliers", {"query": ""})
        forbidden = agent.tools.execute("forbid_negotiation", {})
        valid = agent.tools.execute("search_suppliers", {"query": "苏州"})

        self.assertEqual(invalid.error_code, "validation_error")
        self.assertEqual(forbidden.error_code, "forbidden")
        self.assertTrue(valid.ok)
        self.assertTrue(all(issubclass(item.args_model, BaseModel) for item in agent.tools.list()))
        tool_events = [event for event in repo.audit if event.entity == "ToolCall"]
        self.assertEqual(len(tool_events), 3)
        self.assertTrue(all(event.after and "risk" in event.after for event in tool_events))

    def test_slot_filling(self):
        repo = seed_demo(InMemoryRepository())
        agent = VisitCoordinatorAgent(repo)
        result = agent.intake(
            "下周去苏州看 A、B 两家供应商，A 优先，王经理最好参加，周四 18 点前回上海。"
        )
        self.assertTrue(result.ok)
        self.assertIn("site_id", result.data["missing_slots"])

    def test_address_low_confidence(self):
        result = BasicAddressNormalizer().normalize("短")
        self.assertLess(result.data["confidence"], 0.5)

    def test_planning_feasible_and_unassigned(self):
        repo = seed_demo(InMemoryRepository())
        plan = ItinerarySolver(repo).plan(list(repo.requirements.keys()))
        self.assertGreaterEqual(len(plan.legs), 1)
        self.assertTrue(
            any(item["reason"] == "site_missing_or_unconfirmed" for item in plan.unassigned)
        )

    def test_return_deadline_infeasible(self):
        repo = seed_demo(InMemoryRepository())
        req = next(iter(repo.requirements.values()))
        req.draft.return_deadline = req.draft.date_start + timedelta(minutes=1)
        plan = ItinerarySolver(repo).plan([req.id])
        self.assertEqual(plan.unassigned[0]["reason"], "time_window_or_return_deadline_infeasible")

    def test_ortools_respects_internal_busy_time_and_reports_metrics(self):
        repo = seed_demo(InMemoryRepository())
        req = next(iter(repo.requirements.values()))
        repo.availability = [
            window for window in repo.availability if window.requirement_id != req.id
        ]
        repo.availability.append(
            AvailabilityWindow(
                req.id,
                "supplier",
                day_window(0, 10),
                day_window(0, 14),
            )
        )
        repo.people_busy["王经理"] = [(day_window(0, 10), day_window(0, 11))]

        plan = ItinerarySolver(repo).plan([req.id])

        self.assertEqual(plan.solver, "ortools")
        self.assertEqual(len(plan.legs), 1)
        self.assertGreaterEqual(plan.legs[0].start, day_window(0, 11))
        self.assertEqual(plan.total_buffer_minutes, 15)
        self.assertIsNotNone(plan.return_margin_minutes)
        self.assertGreaterEqual(plan.return_margin_minutes, 0)

    def test_ortools_hard_lock_preserves_window_start(self):
        repo = seed_demo(InMemoryRepository())
        req = next(iter(repo.requirements.values()))
        req.locked_level = "hard"
        repo.people_busy["王经理"] = []
        repo.availability = [
            window for window in repo.availability if window.requirement_id != req.id
        ]
        repo.availability.append(
            AvailabilityWindow(
                req.id,
                "supplier",
                day_window(0, 10),
                day_window(0, 14),
            )
        )

        plan = ItinerarySolver(repo).plan([req.id], lock_confirmed=True)

        self.assertEqual(len(plan.legs), 1)
        self.assertEqual(plan.legs[0].start, day_window(0, 10))
        self.assertIn("LOCK_CONFIRMED", plan.explanation_codes)

    def test_ortools_assigns_higher_priority_when_windows_conflict(self):
        repo = seed_demo(InMemoryRepository())
        candidates = [
            requirement
            for requirement in repo.requirements.values()
            if requirement.draft.site_id is not None
        ][:2]
        high, low = candidates
        high.draft.priority = 5
        low.draft.priority = 1
        high.draft.required_people = []
        low.draft.required_people = []
        repo.availability = [
            window for window in repo.availability if window.requirement_id not in {high.id, low.id}
        ]
        for requirement in (high, low):
            repo.availability.append(
                AvailabilityWindow(
                    requirement.id,
                    "supplier",
                    day_window(0, 10),
                    day_window(0, 12),
                )
            )

        plan = ItinerarySolver(repo).plan([high.id, low.id])

        self.assertEqual([leg.requirement_id for leg in plan.legs], [high.id])
        self.assertEqual(plan.unassigned[0]["requirement_id"], low.id)

    def test_outbox_idempotency(self):
        repo = InMemoryRepository()
        from visit_agent.domain.models import OutboxJob

        one = repo.add_outbox(OutboxJob("email", {}, "same"))
        two = repo.add_outbox(OutboxJob("email", {}, "same"))
        self.assertEqual(one.id, two.id)

    def test_solver_uses_route_provider_override(self):
        repo = seed_demo(InMemoryRepository())
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        site = repo.sites[requirement.draft.site_id]
        repo.availability = [
            AvailabilityWindow(
                requirement.id,
                "supplier",
                day_window(0, 10),
                day_window(0, 12),
            )
        ]
        routes = TravelTimes(overrides={(ORIGIN_POINT, (site.latitude, site.longitude)): 7})

        plan = ItinerarySolver(repo).plan([requirement.id], travel_times=routes)

        self.assertEqual(plan.legs[0].travel_minutes, 7)

    def test_minimal_change_variant_preserves_existing_appointment_time(self):
        repo = seed_demo(InMemoryRepository())
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        repo.availability = [
            AvailabilityWindow(
                requirement.id,
                "supplier",
                day_window(0, 10),
                day_window(0, 15),
            )
        ]
        existing = repo.save_appointment(
            Appointment(
                requirement_id=requirement.id,
                site_id=requirement.draft.site_id,
                start=day_window(0, 11),
                end=day_window(0, 12, 30),
                participants=requirement.draft.required_people,
            )
        )

        recommended = ItinerarySolver(repo).plan([requirement.id])
        minimal = ItinerarySolver(repo).plan([requirement.id], variant="minimal_change")

        self.assertNotEqual(recommended.legs[0].start, existing.start)
        self.assertEqual(minimal.legs[0].start, existing.start)
        self.assertEqual(minimal.variant, "minimal_change")
        self.assertEqual(minimal.changed_appointments, 0)

    def test_solver_enforces_local_working_hours(self):
        repo = seed_demo(InMemoryRepository())
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        requirement.draft.required_people = []
        repo.availability = [
            AvailabilityWindow(
                requirement.id,
                "supplier",
                day_window(0, 20),
                day_window(0, 22),
            )
        ]

        plan = ItinerarySolver(repo).plan([requirement.id])

        self.assertEqual(plan.legs, [])
        self.assertEqual(plan.unassigned[0]["reason"], "time_window_or_return_deadline_infeasible")

    def test_solver_prefers_higher_supplier_preference_when_feasible(self):
        repo = seed_demo(InMemoryRepository())
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        requirement.draft.required_people = []
        repo.availability = [
            AvailabilityWindow(
                requirement.id,
                "supplier",
                day_window(0, 10),
                day_window(0, 12),
                preference=1,
            ),
            AvailabilityWindow(
                requirement.id,
                "supplier",
                day_window(0, 13),
                day_window(0, 15),
                preference=5,
            ),
        ]

        plan = ItinerarySolver(repo).plan([requirement.id])

        self.assertEqual(plan.legs[0].start, day_window(0, 13))

    def test_low_confidence_site_requires_temporary_location_flag(self):
        repo = seed_demo(InMemoryRepository())
        requirement = next(
            item
            for item in repo.requirements.values()
            if item.draft.site_id
            and repo.sites[item.draft.site_id].geocode_status == "low_confidence"
        )
        site = repo.sites[requirement.draft.site_id]

        blocked = ItinerarySolver(repo).plan([requirement.id])
        site.is_temporary = True
        allowed = ItinerarySolver(repo).plan([requirement.id])

        self.assertEqual(blocked.unassigned[0]["reason"], "site_missing_or_unconfirmed")
        self.assertEqual(len(allowed.legs), 1)


if __name__ == "__main__":
    unittest.main()
