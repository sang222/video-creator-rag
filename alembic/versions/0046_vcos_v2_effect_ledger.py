"""Add the crash-safe V2 post-readiness production effect ledger.

Revision ID: 0046_vcos_v2_effect_ledger
Revises: 0045_vcos_final_publish
Create Date: 2026-07-29 02:00:00

Each workflow stage owns one command-bound logical effect.  Its invocation
counter can move from zero to one only, and a VERIFIED row is immutable.
Downgrade is refused after any effect authority has been created.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0046_vcos_v2_effect_ledger"
down_revision: str | None = "0045_vcos_final_publish"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
_HISTORICAL_PROVIDER_TYPES = (
    "'WORKFLOW_ORCHESTRATOR','LLM_SCRIPT_ENGINE','API_NATIVE_TTS',"
    "'CAPTION_TIMELINE_ENGINE','AI_VIDEO_HERO_PROVIDER',"
    "'CLOUD_TEMPLATE_RENDERER_LIGHT','CLOUD_FINAL_ASSEMBLY_RENDERER',"
    "'MEDIA_STORAGE','MEDIA_QC_ENGINE','PUBLISH_PACKAGE_BUILDER',"
    "'API_NATIVE_STOCK_PROVIDER','FREE_FALLBACK_PROVIDER','MOCK_PROVIDER',"
    "'DEFERRED_MANUAL_LIBRARY'"
)
_V2_PROVIDER_TYPES = _HISTORICAL_PROVIDER_TYPES + ",'LOCAL_RENDERER_CAPABILITY'"
_HISTORICAL_STORAGE_PROVIDERS = "'GOOGLE_DRIVE'"
_V2_STORAGE_PROVIDERS = _HISTORICAL_STORAGE_PROVIDERS + ",'VCOS_LOCAL_ARCHIVE'"


def _jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    _widen_v2_provider_and_support_authority()
    op.create_table(
        "v2_production_effect_ledger",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=False),
        sa.Column(
            "production_package_artifact_version_id",
            UUID,
            nullable=False,
        ),
        sa.Column(
            "production_package_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("command_id", sa.String(length=160), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("adapter_key", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=40),
            server_default=sa.text("'PREPARED'"),
            nullable=False,
        ),
        sa.Column(
            "effect_invocation_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("result_type", sa.String(length=120), nullable=True),
        sa.Column("result_id", UUID, nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "result_payload",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "authority_refs",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "effect_journal",
            JSONB,
            server_default=_jsonb_object(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage in ('MEDIA','RENDER','QC','ARCHIVE')",
            name="ck_v2_production_effect_ledger_stage",
        ),
        sa.CheckConstraint(
            "state in ('PREPARED','EFFECT_STARTED','VERIFIED','FAILED_UNCERTAIN')",
            name="ck_v2_production_effect_ledger_state",
        ),
        sa.CheckConstraint(
            "production_package_hash ~ '^[0-9a-f]{64}$' "
            "and input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_production_effect_ledger_identity_hashes",
        ),
        sa.CheckConstraint(
            "result_hash is null or result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_v2_production_effect_ledger_result_hash",
        ),
        sa.CheckConstraint(
            "effect_invocation_count between 0 and 1",
            name="ck_v2_production_effect_ledger_invocation_count",
        ),
        sa.CheckConstraint(
            "(state = 'PREPARED' and effect_invocation_count = 0 "
            "and started_at is null and completed_at is null) or "
            "(state in ('EFFECT_STARTED','FAILED_UNCERTAIN') "
            "and effect_invocation_count = 1 and started_at is not null "
            "and completed_at is null) or "
            "(state = 'VERIFIED' and effect_invocation_count = 1 "
            "and started_at is not null and completed_at is not null "
            "and result_type is not null and result_hash is not null)",
            name="ck_v2_production_effect_ledger_state_evidence",
        ),
        sa.CheckConstraint(
            "completed_at is null or completed_at >= started_at",
            name="ck_v2_production_effect_ledger_completion_order",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["production_workflow_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["production_package_artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "command_id",
            name="uq_v2_production_effect_ledger_command_id",
        ),
        sa.UniqueConstraint(
            "workflow_run_id",
            "stage",
            name="uq_v2_production_effect_ledger_run_stage",
        ),
    )
    for index_name, columns in (
        (
            "ix_v2_production_effect_ledger_project",
            ["video_project_id"],
        ),
        (
            "ix_v2_production_effect_ledger_package",
            ["production_package_artifact_version_id"],
        ),
        (
            "ix_v2_production_effect_ledger_state",
            ["state", "updated_at"],
        ),
        (
            "ix_v2_production_effect_ledger_operation",
            ["adapter_key", "operation_id"],
        ),
    ):
        op.create_index(
            index_name,
            "v2_production_effect_ledger",
            columns,
        )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_verified_v2_effect_change()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.state = 'VERIFIED' THEN
                RAISE EXCEPTION 'verified V2 production effects are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_verified_v2_effect_change
        BEFORE UPDATE OR DELETE ON v2_production_effect_ledger
        FOR EACH ROW
        EXECUTE FUNCTION prevent_verified_v2_effect_change();
        """
    )


def downgrade() -> None:
    _fail_closed_if_effect_authority_exists()
    _fail_closed_if_v2_provider_or_support_authority_exists()
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_verified_v2_effect_change "
        "ON v2_production_effect_ledger"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_verified_v2_effect_change()")
    op.drop_table("v2_production_effect_ledger")
    _restore_historical_provider_and_support_authority()


def _widen_v2_provider_and_support_authority() -> None:
    for table_name, constraint_name in (
        ("media_provider_role_profiles", "ck_media_provider_roles_type"),
        (
            "provider_capability_matrix_entries",
            "ck_provider_capability_entries_type",
        ),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(
            constraint_name,
            table_name,
            f"provider_type in ({_V2_PROVIDER_TYPES})",
        )
    op.drop_constraint(
        "ck_cloud_media_refs_storage_provider",
        "cloud_media_refs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cloud_media_refs_storage_provider",
        "cloud_media_refs",
        f"storage_provider in ({_V2_STORAGE_PROVIDERS})",
    )
    op.drop_index(
        "uq_artifacts_v2_authority_per_project",
        table_name="artifacts",
    )
    op.create_index(
        "uq_artifacts_v2_authority_per_project",
        "artifacts",
        ["video_project_id", "artifact_type"],
        unique=True,
        postgresql_where=sa.text(
            "artifact_type in "
            "('production_package','production_readiness_receipt',"
            "'v2_frozen_support_envelope')"
        ),
    )


def _restore_historical_provider_and_support_authority() -> None:
    op.drop_index(
        "uq_artifacts_v2_authority_per_project",
        table_name="artifacts",
    )
    op.create_index(
        "uq_artifacts_v2_authority_per_project",
        "artifacts",
        ["video_project_id", "artifact_type"],
        unique=True,
        postgresql_where=sa.text(
            "artifact_type in ('production_package','production_readiness_receipt')"
        ),
    )
    for table_name, constraint_name in (
        ("media_provider_role_profiles", "ck_media_provider_roles_type"),
        (
            "provider_capability_matrix_entries",
            "ck_provider_capability_entries_type",
        ),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(
            constraint_name,
            table_name,
            f"provider_type in ({_HISTORICAL_PROVIDER_TYPES})",
        )
    op.drop_constraint(
        "ck_cloud_media_refs_storage_provider",
        "cloud_media_refs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cloud_media_refs_storage_provider",
        "cloud_media_refs",
        f"storage_provider in ({_HISTORICAL_STORAGE_PROVIDERS})",
    )


def _fail_closed_if_effect_authority_exists() -> None:
    message = "0046 downgrade refused: authoritative V2 production effect rows exist"
    predicate = "EXISTS (SELECT 1 FROM v2_production_effect_ledger)"
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {predicate} THEN
                        RAISE EXCEPTION '{message}';
                    END IF;
                END
                $$;
                """
            )
        )
        return
    if op.get_bind().execute(sa.text(f"SELECT {predicate}")).scalar_one():
        raise RuntimeError(message)


def _fail_closed_if_v2_provider_or_support_authority_exists() -> None:
    message = (
        "0046 downgrade refused: LOCAL_RENDERER_CAPABILITY or frozen V2 "
        "support authority or VCOS_LOCAL_ARCHIVE cloud archive rows exist"
    )
    predicate = (
        "EXISTS (SELECT 1 FROM media_provider_role_profiles "
        "WHERE provider_type = 'LOCAL_RENDERER_CAPABILITY') "
        "OR EXISTS (SELECT 1 FROM provider_capability_matrix_entries "
        "WHERE provider_type = 'LOCAL_RENDERER_CAPABILITY') "
        "OR EXISTS (SELECT 1 FROM artifacts "
        "WHERE artifact_type = 'v2_frozen_support_envelope') "
        "OR EXISTS (SELECT 1 FROM cloud_media_refs "
        "WHERE storage_provider = 'VCOS_LOCAL_ARCHIVE')"
    )
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {predicate} THEN
                        RAISE EXCEPTION '{message}';
                    END IF;
                END
                $$;
                """
            )
        )
        return
    if op.get_bind().execute(sa.text(f"SELECT {predicate}")).scalar_one():
        raise RuntimeError(message)
