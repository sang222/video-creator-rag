"""Cut VCOS over to the OpenAI Luna/Terra-only LLM runtime.

Revision ID: 0051_openai_luna_terra_cutover
Revises: 0050_vcos_long_form_analytics
Create Date: 2026-08-01 10:00:00
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0051_openai_luna_terra_cutover"
down_revision: str | None = "0050_vcos_long_form_analytics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _drop_matching_checks(table_name: str, fragment: str) -> None:
    """Drop the live check name, including convention-prefixed legacy names."""

    if context.is_offline_mode():
        escaped_fragment = fragment.replace("'", "''")
        op.execute(
            sa.text(
                f"""
                DO $$
                DECLARE constraint_name text;
                BEGIN
                    FOR constraint_name IN
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = to_regclass('{table_name}')
                          AND contype = 'c'
                          AND pg_get_constraintdef(oid) ILIKE '%{escaped_fragment}%'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE "{table_name}" DROP CONSTRAINT %I',
                            constraint_name
                        );
                    END LOOP;
                END;
                $$;
                """
            )
        )
        return
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "select conname from pg_constraint "
            "where conrelid = to_regclass(:table_name) and contype = 'c' "
            "and pg_get_constraintdef(oid) ilike :fragment"
        ),
        {"table_name": table_name, "fragment": f"%{fragment}%"},
    ).scalars()
    for constraint_name in rows:
        op.execute(f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{constraint_name}"')


def upgrade() -> None:
    # A zero-series policy is the explicit, deterministic first-channel
    # STANDALONE branch; it is not a missing series authority.
    _drop_matching_checks(
        "first_channel_launch_policy_versions", "initial_series_count"
    )
    op.create_check_constraint(
        "ck_launch_policy_series_limits",
        "first_channel_launch_policy_versions",
        "max_active_runs between 1 and 2 and "
        "initial_series_count between 0 and 2 and "
        "jsonb_typeof(approved_initial_series_plan_ids) = 'array' and "
        "jsonb_array_length(approved_initial_series_plan_ids) = initial_series_count",
    )

    # OLLAMA was a configuration authority rather than an immutable run
    # artifact.  Remove it before opening the OpenAI-only constraint.
    _drop_matching_checks("llm_router_profiles", "provider_key")
    _drop_matching_checks("llm_model_profiles", "provider_key")
    op.execute(
        "UPDATE llm_router_profiles "
        "SET provider_key = 'OPENAI', base_url = 'https://api.openai.com/v1'"
    )
    op.execute("DELETE FROM llm_model_profiles")
    op.execute(
        "UPDATE llm_router_lanes "
        "SET fallback_models = '[]'::jsonb, premium_model = null, "
        "emergency_model = null, backup_model = null, "
        "requires_human_approval_for_premium = false"
    )
    op.execute(
        "UPDATE provider_registry_entries SET status = 'DISABLED' "
        "WHERE lower(provider_key) like '%ollama%'"
    )
    op.create_check_constraint(
        "ck_llm_router_profiles_openai_provider",
        "llm_router_profiles",
        "provider_key = 'OPENAI'",
    )
    op.create_check_constraint(
        "ck_llm_model_profiles_openai_provider",
        "llm_model_profiles",
        "provider_key = 'OPENAI'",
    )

    op.add_column(
        "llm_router_lanes",
        sa.Column(
            "reasoning_effort",
            sa.String(length=40),
            nullable=False,
            server_default="low",
        ),
    )
    op.add_column(
        "llm_model_profiles",
        sa.Column(
            "capability_blob",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "llm_model_profiles",
        sa.Column("pricing_snapshot_version", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "llm_route_attempts",
        sa.Column("reasoning_effort", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "llm_route_attempts",
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "llm_route_attempts",
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
    )
    op.create_index(
        "ix_llm_route_attempts_provider_request_id",
        "llm_route_attempts",
        ["provider_request_id"],
        unique=False,
    )

    op.create_table(
        "openai_pricing_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("snapshot_version", sa.String(length=160), nullable=False),
        sa.Column("provider_key", sa.String(length=80), nullable=False),
        sa.Column("service_tier", sa.String(length=40), nullable=False),
        sa.Column("pricing_blob", JSONB, nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("approved_by_user_id", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_version", name="uq_openai_pricing_snapshots_version"
        ),
        sa.CheckConstraint(
            "provider_key = 'OPENAI'", name="ck_openai_pricing_snapshots_provider"
        ),
        sa.CheckConstraint(
            "service_tier = 'standard'", name="ck_openai_pricing_snapshots_tier"
        ),
        sa.CheckConstraint(
            "status in ('DRAFT','APPROVED','SUPERSEDED')",
            name="ck_openai_pricing_snapshots_status",
        ),
        sa.CheckConstraint(
            "canonical_hash ~ '^[0-9a-f]{64}$'",
            name="ck_openai_pricing_snapshots_hash",
        ),
    )
    op.create_table(
        "openai_cutover_receipts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("provider_registry_entry_id", UUID, nullable=False),
        sa.Column("pricing_snapshot_id", UUID, nullable=False),
        sa.Column("budget_policy_id", UUID, nullable=False),
        sa.Column("credential_reference_id", UUID, nullable=False),
        sa.Column("lane_mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_registry_entry_id"], ["provider_registry_entries.id"]
        ),
        sa.ForeignKeyConstraint(
            ["pricing_snapshot_id"], ["openai_pricing_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(["budget_policy_id"], ["budget_policies.id"]),
        sa.ForeignKeyConstraint(
            ["credential_reference_id"], ["credential_references.id"]
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lane_mapping_hash", name="uq_openai_cutover_receipts_lanes"
        ),
        sa.CheckConstraint(
            "status in ('DRAFT','READY','CANARY_PASSED','BLOCKED')",
            name="ck_openai_cutover_receipts_status",
        ),
        sa.CheckConstraint(
            "lane_mapping_hash ~ '^[0-9a-f]{64}$' and canonical_hash ~ '^[0-9a-f]{64}$'",
            name="ck_openai_cutover_receipts_hashes",
        ),
    )
    op.create_index(
        "ix_openai_cutover_receipts_status", "openai_cutover_receipts", ["status"]
    )

    op.create_table(
        "openai_canary_artifacts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("cutover_receipt_id", UUID, nullable=False),
        sa.Column("artifact_key", sa.String(length=160), nullable=False),
        sa.Column("lane_name", sa.String(length=160), nullable=False),
        sa.Column("model_id", sa.String(length=160), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=40), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("llm_route_attempt_id", UUID, nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        sa.Column("repair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cutover_receipt_id"], ["openai_cutover_receipts.id"]),
        sa.ForeignKeyConstraint(["llm_route_attempt_id"], ["llm_route_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cutover_receipt_id",
            "artifact_key",
            name="uq_openai_canary_receipt_artifact",
        ),
        sa.CheckConstraint(
            "model_id in ('gpt-5.6-luna','gpt-5.6-terra')",
            name="ck_openai_canary_artifacts_model",
        ),
        sa.CheckConstraint(
            "reasoning_effort in ('none','low','medium','high')",
            name="ck_openai_canary_artifacts_reasoning",
        ),
        sa.CheckConstraint(
            "status in ('PENDING','SUCCESS','FAILED','SKIPPED')",
            name="ck_openai_canary_artifacts_status",
        ),
        sa.CheckConstraint(
            "repair_count >= 0", name="ck_openai_canary_artifacts_repair_count"
        ),
    )
    op.create_index(
        "ix_openai_canary_artifacts_status", "openai_canary_artifacts", ["status"]
    )


def downgrade() -> None:
    # A cutover receipt or canary is authoritative execution evidence.  Never
    # silently discard it to resurrect the retired provider configuration.
    if context.is_offline_mode():
        op.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM openai_cutover_receipts) THEN
                        RAISE EXCEPTION
                            'DOWNGRADE_BLOCKED_OPENAI_CUTOVER_RECEIPTS_EXIST';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM first_channel_launch_policy_versions
                        WHERE initial_series_count = 0
                    ) THEN
                        RAISE EXCEPTION
                            'DOWNGRADE_BLOCKED_STANDALONE_LAUNCH_POLICY_EXISTS';
                    END IF;
                END;
                $$;
                """
            )
        )
    else:
        bind = op.get_bind()
        if bind.execute(
            sa.text("select count(*) from openai_cutover_receipts")
        ).scalar_one():
            raise RuntimeError("DOWNGRADE_BLOCKED_OPENAI_CUTOVER_RECEIPTS_EXIST")
        if bind.execute(
            sa.text(
                "select count(*) from first_channel_launch_policy_versions "
                "where initial_series_count = 0"
            )
        ).scalar_one():
            raise RuntimeError("DOWNGRADE_BLOCKED_STANDALONE_LAUNCH_POLICY_EXISTS")
    op.drop_index(
        "ix_openai_canary_artifacts_status", table_name="openai_canary_artifacts"
    )
    op.drop_table("openai_canary_artifacts")
    op.drop_index(
        "ix_openai_cutover_receipts_status", table_name="openai_cutover_receipts"
    )
    op.drop_table("openai_cutover_receipts")
    op.drop_table("openai_pricing_snapshots")
    op.drop_index(
        "ix_llm_route_attempts_provider_request_id", table_name="llm_route_attempts"
    )
    op.drop_column("llm_route_attempts", "actual_cost_usd")
    op.drop_column("llm_route_attempts", "provider_request_id")
    op.drop_column("llm_route_attempts", "reasoning_effort")
    op.drop_column("llm_model_profiles", "pricing_snapshot_version")
    op.drop_column("llm_model_profiles", "capability_blob")
    op.drop_column("llm_router_lanes", "reasoning_effort")
    _drop_matching_checks("llm_model_profiles", "provider_key")
    _drop_matching_checks("llm_router_profiles", "provider_key")
    op.execute("UPDATE llm_model_profiles SET provider_key = 'OLLAMA'")
    op.execute("UPDATE llm_router_profiles SET provider_key = 'OLLAMA'")
    op.create_check_constraint(
        "ck_llm_router_profiles_provider_key",
        "llm_router_profiles",
        "provider_key = 'OLLAMA'",
    )
    op.create_check_constraint(
        "ck_llm_model_profiles_provider_key",
        "llm_model_profiles",
        "provider_key = 'OLLAMA'",
    )
    _drop_matching_checks(
        "first_channel_launch_policy_versions", "initial_series_count"
    )
    op.create_check_constraint(
        "ck_launch_policy_series_limits",
        "first_channel_launch_policy_versions",
        "max_active_runs between 1 and 2 and "
        "initial_series_count between 1 and 2 and "
        "jsonb_typeof(approved_initial_series_plan_ids) = 'array' and "
        "jsonb_array_length(approved_initial_series_plan_ids) = initial_series_count",
    )
