from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, cast

from visit_agent.domain.models import (
    ApprovalRequest,
    Appointment,
    AppointmentVersion,
    AuditEvent,
    AvailabilityToken,
    AvailabilityWindow,
    CalendarBinding,
    CalendarConflict,
    Contact,
    ContactAssignment,
    ConversationThread,
    DataQualityIssue,
    HumanTask,
    ItineraryPlan,
    MasterDataChangeRequest,
    Message,
    OutboxJob,
    RequirementRevision,
    Supplier,
    SupplierSite,
    VisitRequirement,
    VisitRequirementDraft,
    day_window,
)


class InMemoryRepository:
    def __init__(self) -> None:
        self.suppliers: dict[str, Supplier] = {}
        self.sites: dict[str, SupplierSite] = {}
        self.contacts: dict[str, Contact] = {}
        self.assignments: list[ContactAssignment] = []
        self.requirements: dict[str, VisitRequirement] = {}
        self.revisions: list[RequirementRevision] = []
        self.availability: list[AvailabilityWindow] = []
        self.availability_tokens: dict[str, AvailabilityToken] = {}
        self.approvals: dict[str, ApprovalRequest] = {}
        self.audit: list[AuditEvent] = []
        self.outbox: dict[str, OutboxJob] = {}
        self.human_tasks: dict[str, HumanTask] = {}
        self.people_busy: dict[str, list[tuple[datetime, datetime]]] = {}
        self.master_data_changes: dict[str, MasterDataChangeRequest] = {}
        self.data_quality_issues: dict[str, DataQualityIssue] = {}
        self.plans: dict[str, ItineraryPlan] = {}
        self.appointments: dict[str, Appointment] = {}
        self.appointment_versions: list[AppointmentVersion] = []
        self.calendar_bindings: dict[str, CalendarBinding] = {}
        self.calendar_conflicts: dict[str, CalendarConflict] = {}
        self.conversations: dict[str, ConversationThread] = {}
        self.messages: dict[str, Message] = {}
        self.agent_sessions: dict[str, dict[str, Any]] = {}
        self.idempotency_records: dict[str, dict[str, Any]] = {}

    def audit_event(self, event: AuditEvent) -> None:
        self.audit.append(event)

    def append_worker_audit(self, event: AuditEvent) -> None:
        self.audit.append(event)

    def add_outbox(self, job: OutboxJob) -> OutboxJob:
        existing = self.outbox.get(job.idempotency_key)
        if existing:
            return existing
        self.outbox[job.idempotency_key] = job
        return job

    def claim_due_outbox(
        self,
        now: datetime,
        limit: int,
        lock_timeout: timedelta = timedelta(minutes=5),
    ) -> list[OutboxJob]:
        stale_before = now - lock_timeout
        candidates = [
            job
            for job in self.outbox.values()
            if job.available_at <= now
            and (
                job.status in {"pending", "retry"}
                or (
                    job.status == "processing"
                    and job.locked_at is not None
                    and job.locked_at <= stale_before
                )
            )
        ]
        claimed = sorted(candidates, key=lambda item: (item.available_at, item.created_at))[:limit]
        for job in claimed:
            job.status = "processing"
            job.locked_at = now
            job.attempts += 1
        return claimed

    def complete_outbox(self, job: OutboxJob, now: datetime) -> None:
        job.status = "completed"
        job.completed_at = now
        job.locked_at = None
        job.last_error = None

    def defer_outbox(self, job: OutboxJob, available_at: datetime) -> None:
        job.status = "pending"
        job.available_at = available_at
        job.locked_at = None
        job.attempts = max(0, job.attempts - 1)

    def retry_outbox(self, job: OutboxJob, available_at: datetime, error: str) -> None:
        job.status = "retry"
        job.available_at = available_at
        job.locked_at = None
        job.last_error = error

    def fail_outbox(self, job: OutboxJob, now: datetime, error: str) -> None:
        job.status = "failed"
        job.completed_at = now
        job.locked_at = None
        job.last_error = error

    def add_human_task(self, task: HumanTask) -> HumanTask:
        existing = self.human_tasks.get(task.idempotency_key)
        if existing:
            return existing
        self.human_tasks[task.idempotency_key] = task
        return task

    def save_plan(self, plan: ItineraryPlan) -> ItineraryPlan:
        self.plans[plan.id] = plan
        return plan

    def save_appointment(self, appointment: Appointment) -> Appointment:
        self.appointments[appointment.id] = appointment
        return appointment

    def add_appointment_version(self, version: AppointmentVersion) -> AppointmentVersion:
        self.appointment_versions.append(version)
        return version

    def save_calendar_binding(self, binding: CalendarBinding) -> CalendarBinding:
        self.calendar_bindings[binding.id] = binding
        return binding

    def save_calendar_conflict(self, conflict: CalendarConflict) -> CalendarConflict:
        self.calendar_conflicts[conflict.id] = conflict
        return conflict

    def save_conversation(self, thread: ConversationThread) -> ConversationThread:
        existing = next(
            (
                item
                for item in self.conversations.values()
                if item.external_thread_id == thread.external_thread_id
            ),
            None,
        )
        if existing:
            return existing
        self.conversations[thread.id] = thread
        return thread

    def save_message(self, message: Message) -> Message:
        self.messages[message.id] = message
        return message

    def save_master_data_change(self, change: MasterDataChangeRequest) -> MasterDataChangeRequest:
        self.master_data_changes[change.id] = change
        return change

    def save_data_quality_issue(self, issue: DataQualityIssue) -> DataQualityIssue:
        self.data_quality_issues[issue.id] = issue
        return issue

    def update_human_task(self, task: HumanTask) -> None:
        self.human_tasks[task.idempotency_key] = task

    def save_agent_session(
        self,
        session_id: str,
        state: dict[str, Any],
    ) -> None:
        self.agent_sessions[session_id] = state

    def get_agent_session(self, session_id: str) -> dict[str, Any] | None:
        return self.agent_sessions.get(session_id)

    def save_idempotency_record(self, key: str, response: Any) -> None:
        self.idempotency_records[key] = {"response": response}

    def snapshot_counts(self) -> dict[str, int]:
        return {
            "suppliers": len(self.suppliers),
            "sites": len(self.sites),
            "contacts": len(self.contacts),
            "requirements": len(self.requirements),
            "requirement_revisions": len(self.revisions),
            "availability": len(self.availability),
            "availability_tokens": len(self.availability_tokens),
            "audit_events": len(self.audit),
            "outbox_jobs": len(self.outbox),
            "human_tasks": len(self.human_tasks),
            "itinerary_plans": len(self.plans),
            "appointments": len(self.appointments),
            "conversations": len(self.conversations),
            "messages": len(self.messages),
            "agent_sessions": len(self.agent_sessions),
            "master_data_changes": len(self.master_data_changes),
            "data_quality_issues": len(self.data_quality_issues),
            "idempotency_records": len(self.idempotency_records),
        }


def seed_demo(repo: InMemoryRepository) -> InMemoryRepository:
    names = [
        "苏州安科",
        "昆山博远",
        "无锡长鸣",
        "常州德信",
        "上海恩泽",
        "嘉兴飞联",
        "南通冠华",
        "太仓恒曜",
    ]
    coords = [
        (31.30, 120.62),
        (31.38, 120.98),
        (31.57, 120.30),
        (31.81, 119.97),
        (31.23, 121.47),
        (30.75, 120.76),
        (31.98, 120.89),
        (31.45, 121.10),
    ]
    for idx, name in enumerate(names):
        supplier = Supplier(
            erp_id=f"SUP-{idx + 1:03d}",
            legal_name=f"{name}制造有限公司",
            display_name=name,
            aliases=[name[-2:], f"{name}供应商"],
        )
        repo.suppliers[supplier.id] = supplier
        site_count = 2 if idx < 4 else 1
        for site_idx in range(site_count):
            lat, lon = coords[idx]
            site = SupplierSite(
                supplier_id=supplier.id,
                name=f"{name}{'一厂' if site_idx == 0 else '二厂'}",
                raw_address=f"{name}工业园 {site_idx + 1} 号",
                normalized_address=f"江苏/上海周边/{name}工业园{site_idx + 1}号",
                latitude=lat + site_idx * 0.03,
                longitude=lon + site_idx * 0.03,
                parking_note="访客车位需提前登记",
            )
            if idx == 2 and site_idx == 0:
                site.geocode_status = "low_confidence"
            repo.sites[site.id] = site
            if site.geocode_status != "verified":
                repo.save_data_quality_issue(
                    DataQualityIssue(
                        entity_type="SupplierSite",
                        entity_id=site.id,
                        issue_type="address_low_confidence",
                        detail=site.raw_address,
                    )
                )
        for cidx in range(2 if idx < 4 else 3):
            contact = Contact(
                name=f"{name}联系人{cidx + 1}",
                emails=[f"contact{idx}{cidx}@example.test"],
                phones=[f"1380000{idx:02d}{cidx:02d}"],
            )
            if idx == 3 and cidx == 0:
                contact.status = "suspected_left"
            repo.contacts[contact.id] = contact
            if contact.status != "active":
                repo.save_data_quality_issue(
                    DataQualityIssue(
                        entity_type="Contact",
                        entity_id=contact.id,
                        issue_type="contact_status",
                        detail=contact.status,
                    )
                )
            first_site = next(s for s in repo.sites.values() if s.supplier_id == supplier.id)
            repo.assignments.append(
                ContactAssignment(
                    contact.id, supplier.id, first_site.id, role="sales", is_primary=cidx == 0
                )
            )

    supplier_list = list(repo.suppliers.values())
    for idx in range(5):
        supplier = supplier_list[idx]
        site = next(s for s in repo.sites.values() if s.supplier_id == supplier.id)
        draft = VisitRequirementDraft(
            supplier_name=supplier.display_name,
            supplier_id=supplier.id,
            site_id=site.id if idx != 1 else None,
            purpose_category="质量沟通",
            date_start=day_window(0, 9),
            date_end=day_window(1, 18),
            duration_minutes=90 if idx != 2 else None,
            priority=5 if idx == 0 else 3,
            required_people=["王经理"] if idx in (0, 1) else ["李工程师"],
            origin="上海虹桥酒店",
            destination="上海虹桥机场",
            return_deadline=day_window(1, 10) + timedelta(hours=8),
        )
        req = VisitRequirement(draft=draft)
        repo.requirements[req.id] = req
        repo.availability.append(
            AvailabilityWindow(
                req.id, "supplier", day_window(idx % 2, 10 + idx), day_window(idx % 2, 12 + idx)
            )
        )

    repo.people_busy = {
        "王经理": [(day_window(0, 13), day_window(0, 15))],
        "李工程师": [(day_window(1, 9), day_window(1, 10))],
        "赵采购": [(day_window(0, 16), day_window(0, 17))],
    }
    repo.audit_event(
        AuditEvent(
            actor="system",
            action="seed_demo",
            entity="repository",
            entity_id="demo",
            after=repo.snapshot_counts(),
        )
    )
    return repo


def as_public_dict(obj: object) -> dict[str, Any]:
    return asdict(cast(Any, obj))
    (ConversationThread,)
