"""Add durable Responses Background state for script qualification.

Revision ID: 0063_background_script_qual
Revises: 0062_series_reservation_width
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0063_background_script_qual"
down_revision = "0062_series_reservation_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_script_qualification_slot", "script_qualification_runs", type_="unique")
    op.add_column("script_qualification_runs", sa.Column("supersedes_qualification_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_script_qualification_supersedes", "script_qualification_runs", "script_qualification_runs", ["supersedes_qualification_run_id"], ["id"])
    op.add_column("script_qualification_runs", sa.Column("recovery_key", sa.String(length=300), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("recovery_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("logical_deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_script_qualification_recovery_key", "script_qualification_runs", ["recovery_key"])
    op.create_unique_constraint("uq_script_qualification_slot_attempt", "script_qualification_runs", ["publish_slot_id", "logical_attempt_number"])
    op.drop_constraint("ck_script_qualification_state", "script_qualification_runs", type_="check")
    op.create_check_constraint("ck_script_qualification_state", "script_qualification_runs", "state in ('RESERVED','RECOVERY_AUTHORIZED','WRITER_SUBMIT_PENDING','WRITER_BACKGROUND_SUBMITTED','WRITER_QUEUED','WRITER_IN_PROGRESS','WRITER_DISPATCHED','SCRIPT_GENERATED','STRUCTURAL_CHECKED','CLAIM_INVENTORY_CHECKED','GROUNDING_CHECKED','VERIFIER_SUBMIT_PENDING','VERIFIER_BACKGROUND_SUBMITTED','VERIFIER_QUEUED','VERIFIER_IN_PROGRESS','VERIFIER_DISPATCHED','EDITORIAL_CHECKED','MEMORY_CHECKED','REPAIRABLE_BLOCK','REPAIR_DISPATCHED','REVERIFYING','QUALIFIED','BLOCKED_NON_REPAIRABLE','BLOCKED_REPAIR_BUDGET_EXHAUSTED','COOLDOWN','SUPERSEDED')")
    op.create_table(
        "script_qualification_background_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("script_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False), sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False), sa.Column("lane", sa.String(length=160), nullable=False), sa.Column("task", sa.String(length=160), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False), sa.Column("immutable_input_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("client_correlation_id", sa.String(length=300), nullable=False), sa.Column("provider_response_id", sa.String(length=200)), sa.Column("provider_request_id", sa.String(length=200)),
        sa.Column("background_status", sa.String(length=48), nullable=False), sa.Column("provider_outcome", sa.String(length=80)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)), sa.Column("last_polled_at", sa.DateTime(timezone=True)), sa.Column("next_poll_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("logical_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("poll_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("submission_attempt_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_network_error", sa.Text()), sa.Column("output_hash", sa.String(length=64)), sa.Column("usage", postgresql.JSONB()), sa.Column("actual_cost_usd", sa.Numeric(18, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("script_qualification_run_id", "phase", name="uq_qualification_background_phase"), sa.UniqueConstraint("client_correlation_id", name="uq_qualification_background_correlation"), sa.UniqueConstraint("provider_response_id", name="uq_qualification_background_response"),
        sa.CheckConstraint("phase in ('WRITER','VERIFIER')", name="ck_qualification_background_phase"), sa.CheckConstraint("background_status in ('SUBMIT_PENDING','SUBMITTED','QUEUED','IN_PROGRESS','COMPLETED','FAILED','CANCELLED','INCOMPLETE','DEADLINE_EXCEEDED','SUBMISSION_OUTCOME_UNKNOWN')", name="ck_qualification_background_status"), sa.CheckConstraint("poll_count >= 0 and submission_attempt_count between 0 and 1", name="ck_qualification_background_counts"), sa.CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_qualification_background_input_fingerprint"),
    )
    op.create_index("ix_qualification_background_due", "script_qualification_background_attempts", ["background_status", "next_poll_at"])
    op.create_table("script_qualification_provider_reclassification_receipts", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("original_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=False), sa.Column("original_route_attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_route_attempts.id")), sa.Column("receipt", postgresql.JSONB(), nullable=False), sa.Column("receipt_hash", sa.String(length=64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("original_qualification_run_id", name="uq_qualification_reclassification_original"))


def downgrade() -> None:
    op.drop_table("script_qualification_provider_reclassification_receipts")
    op.drop_index("ix_qualification_background_due", table_name="script_qualification_background_attempts")
    op.drop_table("script_qualification_background_attempts")
    op.drop_constraint("ck_script_qualification_state", "script_qualification_runs", type_="check")
    op.create_check_constraint("ck_script_qualification_state", "script_qualification_runs", "state in ('RESERVED','WRITER_DISPATCHED','SCRIPT_GENERATED','STRUCTURAL_CHECKED','CLAIM_INVENTORY_CHECKED','GROUNDING_CHECKED','VERIFIER_DISPATCHED','EDITORIAL_CHECKED','MEMORY_CHECKED','REPAIRABLE_BLOCK','REPAIR_DISPATCHED','REVERIFYING','QUALIFIED','BLOCKED_NON_REPAIRABLE','BLOCKED_REPAIR_BUDGET_EXHAUSTED','COOLDOWN','SUPERSEDED')")
    op.drop_constraint("uq_script_qualification_slot_attempt", "script_qualification_runs", type_="unique")
    op.drop_constraint("uq_script_qualification_recovery_key", "script_qualification_runs", type_="unique")
    op.drop_constraint("fk_script_qualification_supersedes", "script_qualification_runs", type_="foreignkey")
    op.drop_column("script_qualification_runs", "logical_deadline_at")
    op.drop_column("script_qualification_runs", "recovery_requested_at")
    op.drop_column("script_qualification_runs", "recovery_key")
    op.drop_column("script_qualification_runs", "supersedes_qualification_run_id")
    op.create_unique_constraint("uq_script_qualification_slot", "script_qualification_runs", ["publish_slot_id"])
