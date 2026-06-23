import asyncio
import unittest

import httpx

from visit_agent.api.app import create_app
from visit_agent.domain.models import (
    Appointment,
    AvailabilityWindow,
    HumanTask,
    RequirementStatus,
    day_window,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository


class LocalASGIClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, url: str, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(run())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


class FastAPITests(unittest.TestCase):
    def setUp(self):
        self.client = LocalASGIClient(create_app(InMemoryRepository()))

    def create_demo_appointment(self) -> Appointment:
        repo = self.client.app.state.repo
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        return repo.save_appointment(
            Appointment(
                requirement_id=requirement.id,
                site_id=requirement.draft.site_id,
                start=day_window(0, 10),
                end=day_window(0, 11, 30),
                participants=requirement.draft.required_people,
                status="confirmed",
            )
        )

    def test_openapi_and_health(self):
        openapi = self.client.get("/openapi.json")
        self.assertEqual(openapi.status_code, 200)
        self.assertIn("/api/v1/agent/intake-sessions", openapi.json()["paths"])
        health = self.client.get("/api/v1/integrations/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["llm"], "rule-mode")
        cors = self.client.get("/api/v1/suppliers", headers={"Origin": "http://127.0.0.1:3000"})
        self.assertEqual(cors.headers["access-control-allow-origin"], "http://127.0.0.1:3000")

    def test_intake_confirm_plan_public_availability(self):
        intake = self.client.post(
            "/api/v1/agent/intake-sessions",
            json={"text": "下周去苏州看 A 供应商，A 优先，王经理最好参加，周四 18 点前回上海。"},
        )
        self.assertEqual(intake.status_code, 200)
        session_id = intake.json()["session_id"]
        supplier = self.client.get("/api/v1/suppliers").json()[0]
        site = self.client.get(f"/api/v1/suppliers/{supplier['id']}/sites").json()[0]
        confirmed = self.client.post(
            "/api/v1/agent/confirm",
            headers={"Idempotency-Key": "test-confirm-1"},
            json={
                "session_id": session_id,
                "patch": {
                    "supplier_id": supplier["id"],
                    "site_id": site["id"],
                    "duration_minutes": 90,
                    "origin": "上海虹桥酒店",
                },
            },
        )
        self.assertEqual(confirmed.status_code, 200)
        requirement_id = confirmed.json()["id"]
        app = self.client.app
        token = app.state.agent.tokens.issue(requirement_id)
        poll = self.client.get(f"/api/v1/public/availability/{token}")
        self.assertEqual(poll.status_code, 200)
        candidate = poll.json()["candidate_windows"][0]
        availability = self.client.post(
            f"/api/v1/public/availability/{token}/submit",
            json={
                "contact_name": "张经理",
                "note": "第一个时间可以",
                "selected_windows": [candidate],
            },
        )
        self.assertEqual(availability.status_code, 200)
        self.assertEqual(len(availability.json()["data"]["windows"]), 1)
        self.assertIn("replan", availability.json()["data"])
        plan = self.client.post("/api/v1/planning/run", json={"requirement_ids": [requirement_id]})
        self.assertEqual(plan.status_code, 200)
        self.assertTrue(plan.json()["ok"])

    def test_session_and_planning_writes_are_idempotent(self):
        headers = {"Idempotency-Key": "same-intake"}
        first = self.client.post(
            "/api/v1/agent/intake-sessions",
            headers=headers,
            json={"text": "下周去苏州看 A 供应商"},
        )
        replay = self.client.post(
            "/api/v1/agent/intake-sessions",
            headers=headers,
            json={"text": "this different payload must not create another session"},
        )
        self.assertEqual(first.json()["session_id"], replay.json()["session_id"])

        requirement_id = self.client.get("/api/v1/requirements").json()[0]["id"]
        plan_headers = {"Idempotency-Key": "same-plan"}
        first_plan = self.client.post(
            "/api/v1/planning/run",
            headers=plan_headers,
            json={"requirement_ids": [requirement_id]},
        )
        replay_plan = self.client.post(
            "/api/v1/planning/run",
            headers=plan_headers,
            json={"requirement_ids": [requirement_id]},
        )
        self.assertEqual(first_plan.json()["data"]["id"], replay_plan.json()["data"]["id"])

    def test_policy_endpoint_blocks_negotiation(self):
        response = self.client.get("/api/v1/policy/negotiation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["risk"], "forbidden")

    def test_contact_pii_is_masked_for_requester_and_visible_to_coordinator(self):
        supplier_id = self.client.get("/api/v1/suppliers").json()[0]["id"]
        masked = self.client.get(f"/api/v1/suppliers/{supplier_id}/contacts")
        visible = self.client.get(
            f"/api/v1/suppliers/{supplier_id}/contacts",
            headers={"X-Role": "coordinator"},
        )

        self.assertIn("***@", masked.json()[0]["emails"][0])
        self.assertIn("****", masked.json()[0]["phones"][0])
        self.assertNotIn("***", visible.json()[0]["emails"][0])

    def test_validation_errors_use_structured_envelope(self):
        response = self.client.post(
            "/api/v1/requirements",
            json={"duration_minutes": -1, "priority": 99},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "validation_error")
        self.assertTrue(response.json()["detail"]["errors"])
        missing = self.client.get("/api/v1/not-a-route")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["code"], "http_error")

    def test_required_route_inventory_and_demo_handlers(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        required = [
            "/api/v1/requirements/{requirement_id}/cancel",
            "/api/v1/requirements/{requirement_id}/resume",
            "/api/v1/requirements/{requirement_id}/contact",
            "/api/v1/requirements/{requirement_id}/impact-preview",
            "/api/v1/suppliers/{supplier_id}/contacts",
            "/api/v1/suppliers/{supplier_id}/timeline",
            "/api/v1/data-quality",
            "/api/v1/master-data-change-requests/{change_id}/approve",
            "/api/v1/master-data-change-requests/{change_id}/reject",
            "/api/v1/availability-polls",
            "/api/v1/conversations",
            "/api/v1/messages",
            "/api/v1/inbound-webhook",
            "/api/v1/planning/{plan_id}/result",
            "/api/v1/planning/{plan_id}/accept",
            "/api/v1/appointments/{appointment_id}/confirm",
            "/api/v1/appointments/{appointment_id}/reschedule",
            "/api/v1/appointments/{appointment_id}/cancel",
            "/api/v1/calendars/sync",
            "/api/v1/calendars/conflicts",
        ]
        for path in required:
            self.assertIn(path, paths)

        quality = self.client.get("/api/v1/data-quality")
        self.assertEqual(quality.status_code, 200)
        self.assertTrue(quality.json())
        inbound = self.client.post("/api/v1/inbound-webhook", json={"body": "周四上午可以"})
        self.assertEqual(inbound.status_code, 200)
        self.assertFalse(inbound.json()["trusted_as_instruction"])
        self.assertEqual(inbound.json()["parsed_result"]["relative_time_text"], "周四上午可以")
        corrected = self.client.patch(
            f"/api/v1/messages/{inbound.json()['id']}/parsed-result",
            headers={"X-Role": "coordinator", "Idempotency-Key": "correct-inbound"},
            json={"parsed_result": {"candidate_windows": [], "rejected": False}},
        )
        self.assertEqual(corrected.status_code, 200)
        self.assertFalse(corrected.json()["parsed_result"]["needs_human_review"])
        appointment = self.create_demo_appointment()
        approval = self.client.post(
            f"/api/v1/appointments/{appointment.id}/cancel",
            headers={"X-Role": "coordinator"},
        )
        self.assertEqual(approval.status_code, 200)
        self.assertEqual(approval.json()["risk"], "high")

    def test_idempotency_key_reuses_confirm_response(self):
        intake = self.client.post(
            "/api/v1/agent/intake-sessions",
            json={"text": "下周去苏州看 A 供应商，A 优先，王经理最好参加，周四 18 点前回上海。"},
        )
        session_id = intake.json()["session_id"]
        supplier = self.client.get("/api/v1/suppliers").json()[0]
        site = self.client.get(f"/api/v1/suppliers/{supplier['id']}/sites").json()[0]
        payload = {
            "session_id": session_id,
            "patch": {
                "supplier_id": supplier["id"],
                "site_id": site["id"],
                "duration_minutes": 90,
                "origin": "上海虹桥酒店",
            },
        }
        headers = {"Idempotency-Key": "same-confirm-key", "X-Role": "requester"}
        first = self.client.post("/api/v1/agent/confirm", headers=headers, json=payload)
        second = self.client.post("/api/v1/agent/confirm", headers=headers, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_rbac_rejects_high_risk_write_for_requester(self):
        appointment = self.create_demo_appointment()
        blocked = self.client.post(
            f"/api/v1/appointments/{appointment.id}/cancel",
            headers={"X-Role": "requester", "Idempotency-Key": "blocked-cancel"},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"]["code"], "forbidden_role")

        allowed = self.client.post(
            f"/api/v1/appointments/{appointment.id}/cancel",
            headers={"X-Role": "approver", "Idempotency-Key": "allowed-cancel"},
        )
        replay = self.client.post(
            f"/api/v1/appointments/{appointment.id}/cancel",
            headers={"X-Role": "approver", "Idempotency-Key": "allowed-cancel"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(allowed.json()["id"], replay.json()["id"])

    def test_calendar_sync_requires_coordinator(self):
        blocked = self.client.post("/api/v1/calendars/sync", headers={"X-Role": "requester"})
        self.assertEqual(blocked.status_code, 403)
        allowed = self.client.post("/api/v1/calendars/sync", headers={"X-Role": "coordinator"})
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(allowed.json()["overwrote_external_changes"])

    def test_public_availability_rejects_invalid_token(self):
        requirement_id = self.client.get("/api/v1/requirements").json()[0]["id"]
        response = self.client.post(
            "/api/v1/public/availability/not-a-real-token/submit",
            json={
                "requirement_id": requirement_id,
                "contact_name": "张经理",
                "none_work": True,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "invalid_or_expired_token")

    def test_public_availability_accepts_none_work_and_alternative(self):
        requirement_id = self.client.get("/api/v1/requirements").json()[0]["id"]
        app = self.client.app
        token = app.state.agent.tokens.issue(requirement_id)
        response = self.client.post(
            f"/api/v1/public/availability/{token}/submit",
            json={
                "contact_name": "李女士",
                "note": "候选都不合适，建议周五下午",
                "none_work": True,
                "alternative_windows": [
                    {
                        "start": "2026-06-26T14:00:00Z",
                        "end": "2026-06-26T16:00:00Z",
                        "timezone_name": "Asia/Shanghai",
                        "preference": 5,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["none_work"])
        self.assertEqual(
            response.json()["data"]["windows"][0]["source"],
            "supplier_alternative",
        )
        message = list(app.state.repo.messages.values())[-1]
        self.assertFalse(message.parsed_result["trusted_as_instruction"])
        self.assertEqual(message.parsed_result["contact_name"], "李女士")

    def test_requirement_crud_revisions_pause_resume_and_soft_delete(self):
        supplier = self.client.get("/api/v1/suppliers").json()[0]
        site = self.client.get(f"/api/v1/suppliers/{supplier['id']}/sites").json()[0]
        payload = {
            "supplier_name": supplier["display_name"],
            "supplier_id": supplier["id"],
            "site_id": site["id"],
            "purpose_category": "质量复盘",
            "date_start": "2026-06-25T09:00:00Z",
            "date_end": "2026-06-25T17:00:00Z",
            "duration_minutes": 90,
            "priority": 4,
            "required_people": ["王经理"],
            "origin": "上海虹桥酒店",
            "destination": "上海虹桥机场",
            "return_deadline": "2026-06-25T18:00:00Z",
            "can_move_existing": False,
        }
        created = self.client.post(
            "/api/v1/requirements",
            headers={"X-Role": "requester", "Idempotency-Key": "create-lifecycle"},
            json=payload,
        )
        self.assertEqual(created.status_code, 200)
        requirement_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "READY_TO_CONTACT")

        updated = self.client.patch(
            f"/api/v1/requirements/{requirement_id}",
            headers={"X-Role": "requester", "Idempotency-Key": "update-lifecycle"},
            json={"patch": {"priority": 5, "purpose_category": "现场质量复盘"}},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["version"], 2)
        self.assertEqual(updated.json()["draft"]["priority"], 5)

        paused = self.client.post(
            f"/api/v1/requirements/{requirement_id}/pause",
            headers={"X-Role": "requester", "Idempotency-Key": "pause-lifecycle"},
        )
        self.assertEqual(paused.status_code, 200)
        self.assertIsNotNone(paused.json()["paused_at"])

        resumed = self.client.post(
            f"/api/v1/requirements/{requirement_id}/resume",
            headers={"X-Role": "requester", "Idempotency-Key": "resume-lifecycle"},
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertIsNone(resumed.json()["paused_at"])

        deleted = self.client.delete(
            f"/api/v1/requirements/{requirement_id}",
            headers={"X-Role": "requester", "Idempotency-Key": "delete-lifecycle"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["status"], "CANCELLED")
        self.assertIsNotNone(deleted.json()["deleted_at"])

        visible_ids = {item["id"] for item in self.client.get("/api/v1/requirements").json()}
        self.assertNotIn(requirement_id, visible_ids)
        all_ids = {
            item["id"]
            for item in self.client.get("/api/v1/requirements?include_deleted=true").json()
        }
        self.assertIn(requirement_id, all_ids)

        revisions = self.client.get(f"/api/v1/requirements/{requirement_id}/revisions")
        self.assertEqual(revisions.status_code, 200)
        self.assertEqual(len(revisions.json()), 5)
        self.assertIn("fields", revisions.json()[-1]["diff"])

    def test_confirmed_requirement_update_waits_for_approval(self):
        requirement_id = self.client.get("/api/v1/requirements").json()[0]["id"]
        repo = self.client.app.state.repo
        requirement = repo.requirements[requirement_id]
        requirement.status = RequirementStatus.CONFIRMED
        original_start = requirement.draft.date_start

        proposed = self.client.patch(
            f"/api/v1/requirements/{requirement_id}",
            headers={"X-Role": "requester", "Idempotency-Key": "confirmed-update"},
            json={
                "patch": {"date_start": "2026-06-25T11:00:00Z"},
                "source": "reschedule_wizard",
            },
        )
        self.assertEqual(proposed.status_code, 200)
        self.assertEqual(proposed.json()["action"], "modify_confirmed_requirement")
        self.assertEqual(requirement.draft.date_start, original_start)

        approved = self.client.post(
            f"/api/v1/approvals/{proposed.json()['id']}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "approve-update"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(requirement.status, RequirementStatus.RESCHEDULE_REQUESTED)
        self.assertEqual(requirement.draft.date_start.hour, 11)

    def test_first_supplier_contact_waits_for_approval_before_outbox(self):
        requirement_id = self.client.get("/api/v1/requirements").json()[0]["id"]
        repo = self.client.app.state.repo
        requirement = repo.requirements[requirement_id]
        requirement.status = RequirementStatus.READY_TO_CONTACT

        proposed = self.client.post(
            f"/api/v1/requirements/{requirement_id}/contact",
            headers={"X-Role": "coordinator", "Idempotency-Key": "contact-first"},
        )
        self.assertEqual(proposed.status_code, 200)
        self.assertEqual(proposed.json()["message"], "approval_required")
        self.assertFalse(proposed.json()["data"]["queued"])
        self.assertEqual(len(repo.outbox), 0)

        approval_id = proposed.json()["data"]["approval"]["id"]
        approved = self.client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "approve-contact"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(len(repo.outbox), 1)
        self.assertEqual(requirement.status, RequirementStatus.WAITING_REPLY)

    def test_stored_plan_acceptance_and_final_confirmation_create_appointment_binding(self):
        repo = self.client.app.state.repo
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        requirement.status = RequirementStatus.CANDIDATES_RECEIVED

        planned = self.client.post(
            "/api/v1/planning/run",
            headers={"X-Role": "requester"},
            json={"requirement_ids": [requirement.id]},
        )
        self.assertEqual(planned.status_code, 200)
        plan_id = planned.json()["data"]["id"]
        result = self.client.get(f"/api/v1/planning/{plan_id}/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["requirement_ids"], [requirement.id])

        accepted = self.client.post(
            f"/api/v1/planning/{plan_id}/accept",
            headers={"X-Role": "coordinator", "Idempotency-Key": "accept-stored-plan"},
        )
        self.assertEqual(accepted.status_code, 200)
        appointment_id = accepted.json()["appointment_ids"][0]
        self.assertEqual(requirement.status, RequirementStatus.TENTATIVE_HOLD)

        proposed = self.client.post(
            f"/api/v1/appointments/{appointment_id}/confirm",
            headers={"X-Role": "approver", "Idempotency-Key": "confirm-tentative"},
        )
        self.assertEqual(proposed.status_code, 200)
        approved = self.client.post(
            f"/api/v1/approvals/{proposed.json()['id']}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "approve-final-confirm"},
        )

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(repo.appointments[appointment_id].status, "confirmed")
        self.assertEqual(requirement.status, RequirementStatus.CONFIRMED)
        self.assertEqual(len(repo.calendar_bindings), 1)
        binding = next(iter(repo.calendar_bindings.values()))
        reschedule = self.client.post(
            f"/api/v1/appointments/{appointment_id}/reschedule",
            headers={"X-Role": "coordinator", "Idempotency-Key": "move-appointment"},
            json={
                "start": "2026-06-25T13:00:00Z",
                "end": "2026-06-25T14:30:00Z",
                "reason": "供应商调整接待时间",
            },
        )
        moved = self.client.post(
            f"/api/v1/approvals/{reschedule.json()['id']}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "approve-move"},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(repo.appointments[appointment_id].start.hour, 13)
        self.assertEqual(requirement.status, RequirementStatus.RESCHEDULE_REQUESTED)
        self.assertEqual(len(repo.appointment_versions), 2)
        synced = self.client.post(
            "/api/v1/calendars/sync",
            headers={"X-Role": "coordinator"},
            json={
                "external_changes": [
                    {
                        "binding_id": binding.id,
                        "etag": "v3",
                        "snapshot": {"start": "2026-06-25T13:00:00Z"},
                    }
                ]
            },
        )
        self.assertEqual(synced.status_code, 200)
        self.assertFalse(synced.json()["overwrote_external_changes"])
        self.assertEqual(len(repo.calendar_conflicts), 1)
        self.assertTrue(
            any(item.action == "resolve_calendar_conflict" for item in repo.approvals.values())
        )

    def test_planning_exposes_distinct_minimal_change_alternative(self):
        repo = self.client.app.state.repo
        requirement = next(
            item for item in repo.requirements.values() if item.draft.site_id is not None
        )
        repo.availability = [
            item for item in repo.availability if item.requirement_id != requirement.id
        ]
        repo.availability.append(
            AvailabilityWindow(
                requirement.id,
                "supplier",
                day_window(0, 10),
                day_window(0, 15),
            )
        )
        repo.save_appointment(
            Appointment(
                requirement_id=requirement.id,
                site_id=requirement.draft.site_id,
                start=day_window(0, 11),
                end=day_window(0, 12, 30),
                participants=requirement.draft.required_people,
            )
        )

        response = self.client.post(
            "/api/v1/planning/run", json={"requirement_ids": [requirement.id]}
        )

        self.assertEqual(response.status_code, 200)
        alternative_id = response.json()["data"]["alternative_plan_id"]
        self.assertIsNotNone(alternative_id)
        self.assertEqual(repo.plans[alternative_id].variant, "minimal_change")
        self.assertEqual(repo.plans[alternative_id].legs[0].start, day_window(0, 11))

    def test_approved_cancellation_versions_appointment_and_closes_requirement(self):
        repo = self.client.app.state.repo
        appointment = self.create_demo_appointment()
        requirement = repo.requirements[appointment.requirement_id]
        requirement.status = RequirementStatus.CONFIRMED

        proposed = self.client.post(
            f"/api/v1/appointments/{appointment.id}/cancel",
            headers={"X-Role": "coordinator", "Idempotency-Key": "cancel-real"},
        )
        approved = self.client.post(
            f"/api/v1/approvals/{proposed.json()['id']}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "approve-cancel-real"},
        )

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(appointment.status, "cancelled")
        self.assertEqual(requirement.status, RequirementStatus.CANCELLED)
        self.assertEqual(repo.appointment_versions[-1].reason, "cancellation_approved")

    def test_coordinator_can_list_and_resolve_human_task_idempotently(self):
        repo = self.client.app.state.repo
        task = repo.add_human_task(
            HumanTask(
                kind="supplier_no_response",
                entity_type="VisitRequirement",
                entity_id="requirement-1",
                title="人工跟进",
                detail="提醒次数已达上限",
                idempotency_key="supplier_no_response:requirement-1",
            )
        )

        forbidden = self.client.get("/api/v1/tasks", headers={"X-Role": "requester"})
        listed = self.client.get("/api/v1/tasks?status=open", headers={"X-Role": "coordinator"})
        resolved = self.client.post(
            f"/api/v1/tasks/{task.id}/resolve",
            headers={"X-Role": "coordinator", "Idempotency-Key": "resolve-task-1"},
        )
        repeated = self.client.post(
            f"/api/v1/tasks/{task.id}/resolve",
            headers={"X-Role": "coordinator", "Idempotency-Key": "resolve-task-1"},
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(resolved.json()["status"], "resolved")
        self.assertEqual(repeated.json(), resolved.json())

    def test_supplier_csv_preview_and_apply_create_approval_bound_change(self):
        exported = self.client.get(
            "/api/v1/reconciliation/suppliers/export", headers={"X-Role": "requester"}
        )
        self.assertEqual(exported.status_code, 200)
        supplier = self.client.get("/api/v1/suppliers").json()[0]
        corrected = exported.text.replace(supplier["display_name"], "已核对供应商", 1)

        preview = self.client.post(
            "/api/v1/reconciliation/suppliers/import-preview",
            headers={"X-Role": "coordinator"},
            json={"content": corrected},
        )
        applied = self.client.post(
            "/api/v1/reconciliation/suppliers/import-apply",
            headers={"X-Role": "coordinator", "Idempotency-Key": "supplier-csv-1"},
            json={"content": corrected},
        )

        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.json()["valid"])
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()["direct_updates"], 0)
        self.assertEqual(len(applied.json()["change_requests"]), 1)
        self.assertEqual(
            self.client.get(
                "/api/v1/master-data-change-requests?status=pending",
                headers={"X-Role": "coordinator"},
            ).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
