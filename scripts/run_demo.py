from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from visit_agent.agent.tools.result import ToolResult
from visit_agent.api.app import create_app
from visit_agent.application.outbox import OutboxWorker, ReminderPolicy
from visit_agent.domain.models import AvailabilityWindow, OutboxJob, RequirementStatus
from visit_agent.infrastructure.adapters.calendar import IcsCalendarAdapter
from visit_agent.infrastructure.db.repository import InMemoryRepository


class DemoDelivery:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, job: OutboxJob) -> ToolResult:
        self.sent.append(str(job.payload["to"]))
        return ToolResult.success({"to": job.payload["to"]}, "mailpit-compatible delivery")


def require_ok(response: object, label: str) -> dict:
    status_code = getattr(response, "status_code")
    if status_code >= 400:
        raise RuntimeError(f"{label} failed: {getattr(response, 'text')}")
    return getattr(response, "json")()


async def main() -> None:
    app = create_app(InMemoryRepository())
    client = TestClient(app)
    repo = app.state.repo

    print("1. 创建自然语言需求")
    intake = require_ok(
        client.post(
            "/api/v1/agent/intake-sessions",
            json={
                "text": "下周去苏州看 A、B 两家供应商，A 优先，王经理参加，"
                "厂区和时长待确认，周四 18 点前回上海。"
            },
        ),
        "intake",
    )

    print("2. Agent 识别缺失厂区和时长")
    print({"missing_slots": intake["missing_slots"], "candidates": intake["candidates"]})
    if "duration_minutes" not in intake["missing_slots"]:
        raise RuntimeError("demo intake did not identify missing duration")

    print("3. 用户通过结构化表单补齐并确认")
    supplier = next(iter(repo.suppliers.values()))
    site = next(item for item in repo.sites.values() if item.supplier_id == supplier.id)
    requirement = require_ok(
        client.post(
            "/api/v1/agent/confirm",
            headers={"Idempotency-Key": "demo-confirm"},
            json={
                "session_id": intake["session_id"],
                "patch": {
                    "supplier_id": supplier.id,
                    "site_id": site.id,
                    "duration_minutes": 90,
                    "origin": "上海虹桥酒店",
                },
            },
        ),
        "confirm requirement",
    )
    requirement_id = requirement["id"]
    print({"requirement_id": requirement_id, "status": requirement["status"]})

    print("4. 审批首次联络并通过 Outbox 发送候选时间邮件")
    contact = require_ok(
        client.post(
            f"/api/v1/requirements/{requirement_id}/contact",
            headers={"X-Role": "coordinator", "Idempotency-Key": "demo-contact"},
        ),
        "contact supplier",
    )
    approval_id = contact["data"]["approval"]["id"]
    require_ok(
        client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "demo-contact-approve"},
        ),
        "approve contact",
    )
    initial_job = next(iter(repo.outbox.values()))
    delivery = DemoDelivery()
    OutboxWorker(
        repo,
        delivery,
        ReminderPolicy(max_reminders=0),
    ).run_once(initial_job.available_at)
    print({"sent": delivery.sent, "outbox_status": initial_job.status})

    print("5. 供应商通过公开 token 页面提交候选时间")
    token = str(initial_job.payload["token"])
    poll = require_ok(client.get(f"/api/v1/public/availability/{token}"), "open availability poll")
    require_ok(
        client.post(
            f"/api/v1/public/availability/{token}/submit",
            json={
                "contact_name": "张经理",
                "note": "第一个时间可接待",
                "selected_windows": [poll["candidate_windows"][0]],
            },
        ),
        "submit availability",
    )
    print({"status": repo.requirements[requirement_id].status.value})

    print("6. OR-Tools 生成两天行程")
    planned = require_ok(
        client.post(
            "/api/v1/planning/run",
            json={"requirement_ids": [requirement_id]},
        ),
        "run planning",
    )["data"]
    if not planned["legs"]:
        raise RuntimeError("demo planner did not assign the confirmed requirement")
    print(
        {
            "plan_id": planned["id"],
            "solver": planned["solver"],
            "legs": len(planned["legs"]),
            "travel_minutes": planned["total_travel_minutes"],
        }
    )

    print("7. 接受推荐方案，审批最终确认，创建 Mock/ICS 事件并回写 Mock ERP")
    accepted = require_ok(
        client.post(
            f"/api/v1/planning/{planned['id']}/accept",
            headers={"X-Role": "coordinator", "Idempotency-Key": "demo-plan-accept"},
        ),
        "accept plan",
    )
    appointment_id = accepted["appointment_ids"][0]
    final_approval = require_ok(
        client.post(
            f"/api/v1/appointments/{appointment_id}/confirm",
            headers={"X-Role": "coordinator", "Idempotency-Key": "demo-final-confirm"},
        ),
        "request final confirmation",
    )
    require_ok(
        client.post(
            f"/api/v1/approvals/{final_approval['id']}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "demo-final-approve"},
        ),
        "approve final confirmation",
    )
    appointment = repo.appointments[appointment_id]
    ics = IcsCalendarAdapter({})
    await ics.create_tentative_hold(appointment)
    ics_text = ics.export_ics()
    print(
        {
            "appointment": appointment.status,
            "calendar_event": appointment.calendar_external_event_id,
            "ics_event": "BEGIN:VEVENT" in ics_text,
            "erp_status": repo.requirements[requirement_id].status.value,
        }
    )

    print("8. 修改需求并显示连锁影响预览")
    impact = require_ok(
        client.post(
            f"/api/v1/requirements/{requirement_id}/impact-preview",
            json={"date_start": planned["legs"][0]["start"]},
        ),
        "impact preview",
    )
    print(impact)

    draft = repo.requirements[requirement_id].draft
    if draft.date_start is None:
        raise RuntimeError("demo requirement lost its date range")
    repo.availability.append(
        AvailabilityWindow(
            requirement_id=requirement_id,
            participant="coordinator-adjustment",
            start=draft.date_start,
            end=draft.date_start + timedelta(hours=2),
            source="impact_preview_adjustment",
            preference=4,
        )
    )

    print("9. 基于现有预约生成推荐方案和最少改动备选")
    replanned = require_ok(
        client.post(
            "/api/v1/planning/run",
            json={"requirement_ids": [requirement_id]},
        ),
        "incremental replan",
    )["data"]
    if not replanned["alternative_plan_id"]:
        raise RuntimeError("demo replan did not produce a distinct minimal-change alternative")
    print(
        {
            "recommended": replanned["id"],
            "minimal_change": replanned["alternative_plan_id"],
            "changed_appointments": replanned["changed_appointments"],
        }
    )

    print("10. 取消已确认预约，经过强制审批并保留版本与审计")
    cancel = require_ok(
        client.post(
            f"/api/v1/appointments/{appointment_id}/cancel",
            headers={"X-Role": "coordinator", "Idempotency-Key": "demo-cancel"},
        ),
        "request cancellation",
    )
    require_ok(
        client.post(
            f"/api/v1/approvals/{cancel['id']}/approve",
            headers={"X-Role": "approver", "Idempotency-Key": "demo-cancel-approve"},
        ),
        "approve cancellation",
    )
    if repo.requirements[requirement_id].status != RequirementStatus.CANCELLED:
        raise RuntimeError("approved cancellation did not close the requirement")
    print(
        {
            "appointment": repo.appointments[appointment_id].status,
            "requirement": repo.requirements[requirement_id].status.value,
            "versions": len(repo.appointment_versions),
            "audit_events": len(repo.audit),
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
