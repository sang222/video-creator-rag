"""Authorize one bounded script-content repair and durable evidence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0065_script_content_repair"
down_revision = "0064_script_writer_normalization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_content_repair_authorization_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_qualification_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_qualification_runs.id"),
            nullable=False,
        ),
        sa.Column("source_script_hash", sa.String(length=64), nullable=False),
        sa.Column("source_result_receipts_hash", sa.String(length=64), nullable=False),
        sa.Column("source_terminal_settlement_hash", sa.String(length=64), nullable=False),
        sa.Column("script_assignment_hash", sa.String(length=64), nullable=False),
        sa.Column("factual_evidence_pack_hash", sa.String(length=64), nullable=False),
        sa.Column("memory_digest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("affected_section_ids", postgresql.JSONB(), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("repair_policy_version", sa.String(length=120), nullable=False),
        sa.Column("repair_type", sa.String(length=120), nullable=False),
        sa.Column("compensation", postgresql.JSONB(), nullable=False),
        sa.Column("authorization_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_qualification_run_id",
            name="uq_script_content_repair_authorization_source",
        ),
        sa.CheckConstraint(
            "source_script_hash ~ '^[0-9a-f]{64}$' and source_result_receipts_hash ~ '^[0-9a-f]{64}$' and source_terminal_settlement_hash ~ '^[0-9a-f]{64}$' and script_assignment_hash ~ '^[0-9a-f]{64}$' and factual_evidence_pack_hash ~ '^[0-9a-f]{64}$' and memory_digest_hash ~ '^[0-9a-f]{64}$' and runtime_contract_hash ~ '^[0-9a-f]{64}$' and authorization_hash ~ '^[0-9a-f]{64}$'",
            name="ck_script_content_repair_authorization_hashes",
        ),
        sa.CheckConstraint(
            "repair_type = 'SCRIPT_CONTENT_REPAIR'",
            name="ck_script_content_repair_authorization_type",
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_prevent_script_content_repair_authorization_mutation "
        "BEFORE UPDATE OR DELETE ON script_content_repair_authorization_receipts "
        "FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_script_content_repair_authorization_mutation "
        "ON script_content_repair_authorization_receipts"
    )
    op.drop_table("script_content_repair_authorization_receipts")
