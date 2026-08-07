"""Persist strict output contracts and immutable legacy writer normalization.

Revision ID: 0064_script_writer_normalization
Revises: 0063_background_script_qual
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0064_script_writer_normalization"
down_revision = "0063_background_script_qual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "script_qualification_background_attempts",
        sa.Column("response_schema_identifier", sa.String(length=160)),
    )
    op.add_column(
        "script_qualification_background_attempts",
        sa.Column("response_schema_hash", sa.String(length=64)),
    )
    op.add_column(
        "script_qualification_background_attempts",
        sa.Column("prompt_version", sa.String(length=120)),
    )
    op.create_table(
        "script_qualification_provider_response_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("script_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=False),
        sa.Column("background_attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_background_attempts.id"), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("provider_response_id", sa.String(length=200), nullable=False),
        sa.Column("provider_request_id", sa.String(length=200)),
        sa.Column("raw_provider_response", postgresql.JSONB(), nullable=False),
        sa.Column("raw_provider_response_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_output_content", sa.Text(), nullable=False),
        sa.Column("raw_output_hash", sa.String(length=64), nullable=False),
        sa.Column("usage", postgresql.JSONB()),
        sa.Column("response_schema_identifier", sa.String(length=160), nullable=False),
        sa.Column("response_schema_hash", sa.String(length=64)),
        sa.Column("prompt_version", sa.String(length=120), nullable=False),
        sa.Column("producer_input_hash", sa.String(length=64), nullable=False),
        sa.Column("accepted_typed_output_hash", sa.String(length=64)),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("background_attempt_id", name="uq_qualification_response_snapshot_attempt"),
        sa.UniqueConstraint("provider_response_id", name="uq_qualification_response_snapshot_provider_response"),
        sa.CheckConstraint("phase in ('WRITER','VERIFIER')", name="ck_qualification_response_snapshot_phase"),
        sa.CheckConstraint("raw_provider_response_hash ~ '^[0-9a-f]{64}$' and raw_output_hash ~ '^[0-9a-f]{64}$' and producer_input_hash ~ '^[0-9a-f]{64}$'", name="ck_qualification_response_snapshot_hashes"),
        sa.CheckConstraint("response_schema_hash is null or response_schema_hash ~ '^[0-9a-f]{64}$'", name="ck_qualification_response_snapshot_schema_hash"),
        sa.CheckConstraint("accepted_typed_output_hash is null or accepted_typed_output_hash ~ '^[0-9a-f]{64}$'", name="ck_qualification_response_snapshot_accepted_hash"),
    )
    op.create_index("ix_qualification_response_snapshot_run", "script_qualification_provider_response_snapshots", ["script_qualification_run_id"])
    op.create_table(
        "script_writer_output_normalization_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=False),
        sa.Column("source_background_attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_background_attempts.id"), nullable=False),
        sa.Column("source_provider_response_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_provider_response_snapshots.id"), nullable=False),
        sa.Column("source_provider_response_id", sa.String(length=200), nullable=False),
        sa.Column("source_provider_request_id", sa.String(length=200)),
        sa.Column("source_raw_output_hash", sa.String(length=64), nullable=False),
        sa.Column("source_schema_classification", sa.String(length=160), nullable=False),
        sa.Column("normalization_version", sa.String(length=120), nullable=False),
        sa.Column("field_mapping", postgresql.JSONB(), nullable=False),
        sa.Column("removed_wrapper_fields", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_payload", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("contract_schema_version", sa.String(length=120), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("receipt_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_qualification_run_id", name="uq_writer_normalization_source_run"),
        sa.UniqueConstraint("source_background_attempt_id", name="uq_writer_normalization_source_attempt"),
        sa.UniqueConstraint("source_provider_response_snapshot_id", name="uq_writer_normalization_source_snapshot"),
        sa.CheckConstraint("source_raw_output_hash ~ '^[0-9a-f]{64}$' and normalized_payload_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'", name="ck_writer_normalization_receipt_hashes"),
    )
    op.create_index("ix_writer_normalization_response", "script_writer_output_normalization_receipts", ["source_provider_response_id"])
    for table in (
        "script_qualification_provider_response_snapshots",
        "script_writer_output_normalization_receipts",
    ):
        op.execute(
            f"CREATE TRIGGER trg_prevent_{table}_mutation BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update()"
        )


def downgrade() -> None:
    for table in (
        "script_writer_output_normalization_receipts",
        "script_qualification_provider_response_snapshots",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_prevent_{table}_mutation ON {table}")
    op.drop_index("ix_writer_normalization_response", table_name="script_writer_output_normalization_receipts")
    op.drop_table("script_writer_output_normalization_receipts")
    op.drop_index("ix_qualification_response_snapshot_run", table_name="script_qualification_provider_response_snapshots")
    op.drop_table("script_qualification_provider_response_snapshots")
    op.drop_column("script_qualification_background_attempts", "prompt_version")
    op.drop_column("script_qualification_background_attempts", "response_schema_hash")
    op.drop_column("script_qualification_background_attempts", "response_schema_identifier")
