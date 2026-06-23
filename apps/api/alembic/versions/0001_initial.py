"""initial visit agent schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


requirement_status = sa.Enum(
    "DRAFT",
    "NEED_MORE_INFORMATION",
    "READY_TO_CONTACT",
    "CONTACTED",
    "WAITING_REPLY",
    "CANDIDATES_RECEIVED",
    "INTERNAL_APPROVAL",
    "TENTATIVE_HOLD",
    "CONFIRMED",
    "RESCHEDULE_REQUESTED",
    "CANCELLATION_REQUESTED",
    "CANCELLED",
    "COMPLETED",
    "FAILED",
    name="requirementstatus",
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("erp_id", sa.String(80), nullable=False, unique=True),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_suppliers_erp_id", "suppliers", ["erp_id"])
    op.create_index("ix_suppliers_display_name", "suppliers", ["display_name"])

    op.create_table(
        "supplier_sites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("site_type", sa.String(40), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("raw_address", sa.Text(), nullable=False),
        sa.Column("normalized_address", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("geocode_status", sa.String(40), nullable=False),
        sa.Column("visitor_entrance", sa.String(255), nullable=False),
        sa.Column("parking_note", sa.Text(), nullable=False),
        sa.Column("reception_hours", sa.String(120), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("is_temporary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("emails", sa.JSON(), nullable=False),
        sa.Column("phones", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "contact_assignments",
        sa.Column("contact_id", sa.String(36), sa.ForeignKey("contacts.id"), primary_key=True),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id"), primary_key=True),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("supplier_sites.id"), primary_key=True),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("business_scope", sa.String(120), nullable=False),
        sa.Column("can_confirm_appointment", sa.Boolean(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "visit_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id")),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("supplier_sites.id")),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("status", requirement_status, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("locked_level", sa.String(40), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_visit_requirements_status", "visit_requirements", ["status"])

    op.create_table("requirement_revisions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("requirement_id", sa.String(36), sa.ForeignKey("visit_requirements.id"), nullable=False), sa.Column("diff", sa.JSON(), nullable=False), sa.Column("source", sa.String(80), nullable=False), sa.Column("actor", sa.String(120), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("agent_sessions", sa.Column("session_id", sa.String(36), primary_key=True), sa.Column("state", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("idempotency_records", sa.Column("key", sa.String(255), primary_key=True), sa.Column("response", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("availability_windows", sa.Column("id", sa.String(36), primary_key=True), sa.Column("requirement_id", sa.String(36), sa.ForeignKey("visit_requirements.id"), nullable=False), sa.Column("source", sa.String(80), nullable=False), sa.Column("participant", sa.String(255), nullable=False), sa.Column("start", sa.DateTime(timezone=True), nullable=False), sa.Column("end", sa.DateTime(timezone=True), nullable=False), sa.Column("timezone_name", sa.String(80), nullable=False), sa.Column("preference", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("availability_tokens", sa.Column("id", sa.String(36), primary_key=True), sa.Column("requirement_id", sa.String(36), sa.ForeignKey("visit_requirements.id"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("submitted_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("appointments", sa.Column("id", sa.String(36), primary_key=True), sa.Column("requirement_id", sa.String(36), sa.ForeignKey("visit_requirements.id"), nullable=False), sa.Column("site_id", sa.String(36), sa.ForeignKey("supplier_sites.id"), nullable=False), sa.Column("start", sa.DateTime(timezone=True), nullable=False), sa.Column("end", sa.DateTime(timezone=True), nullable=False), sa.Column("participants", sa.JSON(), nullable=False), sa.Column("supplier_confirmation_status", sa.String(80), nullable=False), sa.Column("calendar_external_event_id", sa.String(255)), sa.Column("status", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("appointment_versions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("appointment_id", sa.String(36), sa.ForeignKey("appointments.id"), nullable=False), sa.Column("before", sa.JSON(), nullable=False), sa.Column("after", sa.JSON(), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("calendar_bindings", sa.Column("id", sa.String(36), primary_key=True), sa.Column("appointment_id", sa.String(36), sa.ForeignKey("appointments.id"), nullable=False), sa.Column("provider", sa.String(80), nullable=False), sa.Column("calendar_id", sa.String(255), nullable=False), sa.Column("external_event_id", sa.String(255), nullable=False), sa.Column("etag", sa.String(255), nullable=False), sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("calendar_conflicts", sa.Column("id", sa.String(36), primary_key=True), sa.Column("appointment_id", sa.String(36), sa.ForeignKey("appointments.id"), nullable=False), sa.Column("binding_id", sa.String(36), sa.ForeignKey("calendar_bindings.id")), sa.Column("local_snapshot", sa.JSON(), nullable=False), sa.Column("external_snapshot", sa.JSON(), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("conversation_threads", sa.Column("id", sa.String(36), primary_key=True), sa.Column("channel", sa.String(80), nullable=False), sa.Column("external_thread_id", sa.String(255), nullable=False, unique=True), sa.Column("requirement_id", sa.String(36), sa.ForeignKey("visit_requirements.id")), sa.Column("requirement_version", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("messages", sa.Column("id", sa.String(36), primary_key=True), sa.Column("thread_id", sa.String(36), sa.ForeignKey("conversation_threads.id"), nullable=False), sa.Column("direction", sa.String(40), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("send_status", sa.String(40), nullable=False), sa.Column("parsed_result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("itinerary_plans", sa.Column("id", sa.String(36), primary_key=True), sa.Column("requirement_ids", sa.JSON(), nullable=False), sa.Column("objective", sa.Text(), nullable=False), sa.Column("solver", sa.String(80), nullable=False), sa.Column("variant", sa.String(40), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("total_travel_minutes", sa.Integer(), nullable=False), sa.Column("total_wait_minutes", sa.Integer(), nullable=False), sa.Column("total_buffer_minutes", sa.Integer(), nullable=False), sa.Column("changed_appointments", sa.Integer(), nullable=False), sa.Column("return_margin_minutes", sa.Integer()), sa.Column("unassigned", sa.JSON(), nullable=False), sa.Column("explanation_codes", sa.JSON(), nullable=False), sa.Column("accepted_at", sa.DateTime(timezone=True)), sa.Column("alternative_plan_id", sa.String(36)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("itinerary_legs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("plan_id", sa.String(36), sa.ForeignKey("itinerary_plans.id"), nullable=False), sa.Column("requirement_id", sa.String(36), sa.ForeignKey("visit_requirements.id"), nullable=False), sa.Column("from_label", sa.String(255), nullable=False), sa.Column("to_label", sa.String(255), nullable=False), sa.Column("start", sa.DateTime(timezone=True), nullable=False), sa.Column("end", sa.DateTime(timezone=True), nullable=False), sa.Column("travel_minutes", sa.Integer(), nullable=False), sa.Column("buffer_minutes", sa.Integer(), nullable=False), sa.Column("route_geometry", sa.JSON(), nullable=False))
    op.create_table("approval_requests", sa.Column("id", sa.String(36), primary_key=True), sa.Column("action", sa.String(120), nullable=False), sa.Column("risk", sa.String(40), nullable=False), sa.Column("impact_preview", sa.JSON(), nullable=False), sa.Column("approver", sa.String(120), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("audit_events", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("actor", sa.String(120), nullable=False), sa.Column("action", sa.String(160), nullable=False), sa.Column("entity", sa.String(120), nullable=False), sa.Column("entity_id", sa.String(120), nullable=False), sa.Column("before", sa.JSON()), sa.Column("after", sa.JSON()), sa.Column("correlation_id", sa.String(36), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("outbox_jobs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("kind", sa.String(80), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True), sa.Column("status", sa.String(40), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("max_attempts", sa.Integer(), nullable=False), sa.Column("available_at", sa.DateTime(timezone=True), nullable=False), sa.Column("locked_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_index("ix_outbox_jobs_due", "outbox_jobs", ["status", "available_at"])
    op.create_table("human_tasks", sa.Column("id", sa.String(36), primary_key=True), sa.Column("kind", sa.String(80), nullable=False), sa.Column("entity_type", sa.String(80), nullable=False), sa.Column("entity_id", sa.String(80), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("detail", sa.Text(), nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True), sa.Column("status", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("master_data_change_requests", sa.Column("id", sa.String(36), primary_key=True), sa.Column("entity_type", sa.String(80), nullable=False), sa.Column("entity_id", sa.String(80), nullable=False), sa.Column("original_value", sa.JSON(), nullable=False), sa.Column("proposed_value", sa.JSON(), nullable=False), sa.Column("source_message_id", sa.String(36)), sa.Column("risk", sa.String(40), nullable=False), sa.Column("approval_status", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("data_quality_issues", sa.Column("id", sa.String(36), primary_key=True), sa.Column("entity_type", sa.String(80), nullable=False), sa.Column("entity_id", sa.String(80), nullable=False), sa.Column("issue_type", sa.String(80), nullable=False), sa.Column("detail", sa.Text(), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))


def downgrade() -> None:
    for table in [
        "data_quality_issues",
        "master_data_change_requests",
        "human_tasks",
        "outbox_jobs",
        "audit_events",
        "approval_requests",
        "itinerary_legs",
        "itinerary_plans",
        "messages",
        "conversation_threads",
        "calendar_conflicts",
        "calendar_bindings",
        "appointment_versions",
        "appointments",
        "availability_tokens",
        "availability_windows",
        "agent_sessions",
        "idempotency_records",
        "requirement_revisions",
        "visit_requirements",
        "contact_assignments",
        "contacts",
        "supplier_sites",
        "suppliers",
    ]:
        op.drop_table(table)
    requirement_status.drop(op.get_bind(), checkfirst=True)
