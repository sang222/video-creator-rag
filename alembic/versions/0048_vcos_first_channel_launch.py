"""Add immutable first-channel launch policy and launch-run authority.

Revision ID: 0048_vcos_first_channel_launch
Revises: 0047_vcos_remove_shorts
Create Date: 2026-07-30 01:00:00

This revision also completes the non-production editorial candidate/preflight
authority required by the controlled-evidence launch runway.  Duration remains
referenced through the channel contract; it is not duplicated here.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0048_vcos_first_channel_launch"
down_revision: str | None = "0047_vcos_remove_shorts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    _create_editorial_migration_baseline()
    _complete_editorial_research_authority()
    _extend_strict_market_preflight()
    _create_launch_policy_versions()
    _create_launch_runs()
    _create_launch_scope_guards()
    _create_launch_policy_immutability_guard()


def downgrade() -> None:
    _fail_closed_if_launch_or_new_editorial_authority_exists()
    _drop_launch_policy_immutability_guard()
    _drop_launch_scope_guards()
    op.drop_table("launch_runs")
    op.drop_table("first_channel_launch_policy_versions")
    _remove_strict_market_preflight()
    _remove_editorial_research_extensions()
    op.drop_table("vcos_0048_editorial_migration_baseline")


def _create_editorial_migration_baseline() -> None:
    """Record exact 0047 rows that receive mechanical 0048 backfills."""

    op.create_table(
        "vcos_0048_editorial_migration_baseline",
        sa.Column("entity_kind", sa.String(length=40), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("baseline_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "entity_kind in "
            "('EDITORIAL_RESEARCH_RUN','EDITORIAL_IDEA_CANDIDATE',"
            "'IDEA_MARKET_PREFLIGHT')",
            name="ck_vcos_0048_editorial_baseline_kind",
        ),
        sa.CheckConstraint(
            "baseline_hash ~ '^[0-9a-f]{64}$'",
            name="ck_vcos_0048_editorial_baseline_hash",
        ),
        sa.PrimaryKeyConstraint("entity_kind", "entity_id"),
    )
    op.execute(
        """
        INSERT INTO vcos_0048_editorial_migration_baseline (
            entity_kind,
            entity_id,
            baseline_hash
        )
        SELECT
            'EDITORIAL_RESEARCH_RUN',
            run.id,
            encode(
                sha256(convert_to(to_jsonb(run)::text, 'UTF8')),
                'hex'
            )
        FROM editorial_research_runs AS run
        UNION ALL
        SELECT
            'EDITORIAL_IDEA_CANDIDATE',
            candidate.id,
            encode(
                sha256(convert_to(to_jsonb(candidate)::text, 'UTF8')),
                'hex'
            )
        FROM editorial_idea_candidates AS candidate
        UNION ALL
        SELECT
            'IDEA_MARKET_PREFLIGHT',
            preflight.id,
            encode(
                sha256(convert_to(to_jsonb(preflight)::text, 'UTF8')),
                'hex'
            )
        FROM idea_market_preflights AS preflight
        """
    )


def _complete_editorial_research_authority() -> None:
    op.add_column(
        "editorial_research_runs",
        sa.Column("channel_profile_version_id", UUID, nullable=True),
    )
    op.add_column(
        "editorial_research_runs",
        sa.Column("created_by_user_id", UUID, nullable=True),
    )
    op.execute(
        """
        UPDATE editorial_research_runs AS run
        SET channel_profile_version_id = policy.channel_profile_version_id
        FROM compiled_channel_policy_snapshots AS policy
        WHERE policy.id = run.policy_snapshot_id
          AND run.channel_profile_version_id IS NULL
        """
    )
    op.alter_column(
        "editorial_research_runs",
        "channel_profile_version_id",
        existing_type=UUID,
        nullable=False,
    )
    op.create_foreign_key(
        op.f(
            "fk_editorial_research_runs_channel_profile_version_id_"
            "channel_profile_versions"
        ),
        "editorial_research_runs",
        "channel_profile_versions",
        ["channel_profile_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_editorial_research_runs_created_by_user_id_users"),
        "editorial_research_runs",
        "users",
        ["created_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_editorial_research_runs_profile_id",
        "editorial_research_runs",
        ["channel_profile_version_id"],
    )

    for column in (
        sa.Column("suggested_series_plan_id", UUID, nullable=True),
        sa.Column(
            "budget_readiness",
            sa.String(length=40),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column(
            "rights_policy_state",
            sa.String(length=40),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column(
            "quality_state",
            sa.String(length=40),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column("experiment_phase", sa.String(length=40), nullable=True),
        sa.Column(
            "primary_variable_under_test",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "baseline_refs",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("comparison_group", sa.String(length=160), nullable=True),
        sa.Column("canonical_hash", sa.String(length=64), nullable=True),
        sa.Column("created_by_user_id", UUID, nullable=True),
    ):
        op.add_column("editorial_idea_candidates", column)
    op.execute(
        """
        UPDATE editorial_idea_candidates
        SET canonical_hash = encode(
            sha256(
                convert_to(
                    'editorial-idea-candidate:' || id::text,
                    'UTF8'
                )
            ),
            'hex'
        )
        WHERE canonical_hash IS NULL
        """
    )
    op.alter_column(
        "editorial_idea_candidates",
        "canonical_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "editorial_idea_candidates",
        "budget_readiness",
        existing_type=sa.String(length=40),
        server_default=None,
    )
    op.alter_column(
        "editorial_idea_candidates",
        "rights_policy_state",
        existing_type=sa.String(length=40),
        server_default=None,
    )
    op.alter_column(
        "editorial_idea_candidates",
        "quality_state",
        existing_type=sa.String(length=40),
        server_default=None,
    )
    op.alter_column(
        "editorial_idea_candidates",
        "baseline_refs",
        existing_type=JSONB,
        server_default=None,
    )
    op.create_foreign_key(
        op.f("fk_editorial_idea_candidates_suggested_series_plan_id_series_plans"),
        "editorial_idea_candidates",
        "series_plans",
        ["suggested_series_plan_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_editorial_idea_candidates_created_by_user_id_users"),
        "editorial_idea_candidates",
        "users",
        ["created_by_user_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_editorial_idea_candidates_canonical_hash",
        "editorial_idea_candidates",
        ["canonical_hash"],
    )
    op.create_check_constraint(
        "ck_editorial_idea_candidates_readiness",
        "editorial_idea_candidates",
        "budget_readiness in ('READY','BLOCKED','UNKNOWN') "
        "and rights_policy_state in ('PASS','BLOCK','UNKNOWN') "
        "and quality_state in ('PASS','BLOCK','UNKNOWN')",
    )
    op.create_check_constraint(
        "ck_editorial_idea_candidates_hash",
        "editorial_idea_candidates",
        "canonical_hash ~ '^[0-9a-f]{64}$'",
    )


def _extend_strict_market_preflight() -> None:
    for column in (
        sa.Column("niche_contract_digest_ref", sa.Text(), nullable=True),
        sa.Column(
            "niche_contract_digest_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("target_market_digest_ref", sa.Text(), nullable=True),
        sa.Column(
            "target_market_digest_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("editorial_slot_ref", sa.Text(), nullable=True),
        sa.Column("content_category_ref", sa.Text(), nullable=True),
        sa.Column("target_market", sa.String(length=2), nullable=True),
        sa.Column(
            "market_scope",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("market_fit_score", sa.Numeric(10, 4), nullable=True),
        sa.Column("market_fit_threshold", sa.Numeric(10, 4), nullable=True),
    ):
        op.add_column("idea_market_preflights", column)
    op.alter_column(
        "idea_market_preflights",
        "market_scope",
        existing_type=JSONB,
        server_default=None,
    )
    op.create_check_constraint(
        "ck_idea_market_preflights_authority_hashes",
        "idea_market_preflights",
        "(niche_contract_digest_hash is null or "
        "niche_contract_digest_hash ~ '^[0-9a-f]{64}$') and "
        "(target_market_digest_hash is null or "
        "target_market_digest_hash ~ '^[0-9a-f]{64}$')",
    )


def _create_launch_policy_versions() -> None:
    op.create_table(
        "first_channel_launch_policy_versions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("channel_profile_version_id", UUID, nullable=False),
        sa.Column("policy_snapshot_id", UUID, nullable=False),
        sa.Column("approved_initial_series_plan_ids", JSONB, nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("supersedes_policy_version_id", UUID, nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column(
            "launch_mode",
            sa.String(length=64),
            server_default=sa.text("'CONTROLLED_EVIDENCE_BUILDING'"),
            nullable=False,
        ),
        sa.Column(
            "duration_source",
            sa.String(length=48),
            server_default=sa.text("'CHANNEL_DURATION_CONTRACT'"),
            nullable=False,
        ),
        sa.Column("preparation_days_min", sa.Integer(), nullable=False),
        sa.Column("preparation_days_max", sa.Integer(), nullable=False),
        sa.Column("idea_candidates_target", sa.Integer(), nullable=False),
        sa.Column("preflight_pass_target", sa.Integer(), nullable=False),
        sa.Column("greenlight_target", sa.Integer(), nullable=False),
        sa.Column("public_ready_buffer_target", sa.Integer(), nullable=False),
        sa.Column("max_days_produced_ahead", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_productions", sa.Integer(), nullable=False),
        sa.Column("max_active_runs", sa.Integer(), nullable=False),
        sa.Column("initial_series_count", sa.Integer(), nullable=False),
        sa.Column("first_n_public_videos", sa.Integer(), nullable=False),
        sa.Column(
            "max_primary_variables_changed_per_video",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "auto_niche_pivot",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "auto_series_kill",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "auto_playbook_promotion",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "channel_promise_and_initial_series",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "pre_render_script_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "pre_render_package_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("final_video_decision", sa.String(length=40), nullable=False),
        sa.Column("public_publish", sa.String(length=32), nullable=False),
        sa.Column("commercial_model", sa.String(length=48), nullable=False),
        sa.Column(
            "affiliate_cta",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "sponsor_content",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("primary_cta", sa.String(length=40), nullable=False),
        sa.Column("target_long_form_per_week", sa.Integer(), nullable=False),
        sa.Column(
            "quality_fallback_long_form_per_week",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "minimum_publish_interval_hours",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("publish_weekdays", JSONB, nullable=False),
        sa.Column("publish_local_time", sa.String(length=5), nullable=False),
        sa.Column("render_lead_time_min_hours", sa.Integer(), nullable=False),
        sa.Column("render_lead_time_max_hours", sa.Integer(), nullable=False),
        sa.Column(
            "same_day_multi_publish",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=False),
        sa.Column("approved_by_user_id", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state in ('DRAFT','APPROVED','SUPERSEDED','ARCHIVED')",
            name="ck_launch_policy_state",
        ),
        sa.CheckConstraint(
            "launch_mode = 'CONTROLLED_EVIDENCE_BUILDING'",
            name="ck_launch_policy_mode",
        ),
        sa.CheckConstraint(
            "duration_source = 'CHANNEL_DURATION_CONTRACT'",
            name="ck_launch_policy_duration_source",
        ),
        sa.CheckConstraint(
            "preparation_days_min > 0 and "
            "preparation_days_max >= preparation_days_min and "
            "idea_candidates_target >= preflight_pass_target and "
            "preflight_pass_target >= greenlight_target and "
            "greenlight_target >= public_ready_buffer_target and "
            "public_ready_buffer_target > 0",
            name="ck_launch_policy_runway_targets",
        ),
        sa.CheckConstraint(
            "max_active_runs between 1 and 2 and "
            "initial_series_count between 1 and 2 and "
            "jsonb_typeof(approved_initial_series_plan_ids) = 'array' and "
            "jsonb_array_length(approved_initial_series_plan_ids) = "
            "initial_series_count",
            name="ck_launch_policy_series_limits",
        ),
        sa.CheckConstraint(
            "max_primary_variables_changed_per_video = 1 and "
            "not auto_niche_pivot and not auto_series_kill and "
            "not auto_playbook_promotion and not pre_render_script_review and "
            "not pre_render_package_review and not affiliate_cta and "
            "not sponsor_content and not same_day_multi_publish",
            name="ck_launch_policy_human_safety",
        ),
        sa.CheckConstraint(
            "target_long_form_per_week between 1 and 2 and "
            "quality_fallback_long_form_per_week between 1 and "
            "target_long_form_per_week and "
            "minimum_publish_interval_hours > 0 and "
            "render_lead_time_min_hours > 0 and "
            "render_lead_time_max_hours >= render_lead_time_min_hours and "
            "jsonb_typeof(publish_weekdays) = 'array' and "
            "jsonb_array_length(publish_weekdays) between 1 and "
            "target_long_form_per_week",
            name="ck_launch_policy_cadence",
        ),
        sa.CheckConstraint(
            "(state = 'APPROVED' and approved_by_user_id is not null and "
            "approved_at is not null) or state <> 'APPROVED'",
            name="ck_launch_policy_approval",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["channel_profile_version_id"],
            ["channel_profile_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["policy_snapshot_id"],
            ["compiled_channel_policy_snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_policy_version_id"],
            ["first_channel_launch_policy_versions.id"],
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "policy_version",
            name="uq_launch_policy_channel_version",
        ),
        sa.UniqueConstraint(
            "canonical_hash",
            name="uq_launch_policy_canonical_hash",
        ),
    )
    op.create_index(
        "ix_launch_policy_company",
        "first_channel_launch_policy_versions",
        ["company_id"],
    )
    op.create_index(
        "ix_launch_policy_channel",
        "first_channel_launch_policy_versions",
        ["channel_workspace_id"],
    )
    op.create_index(
        "uq_launch_policy_one_approved_channel",
        "first_channel_launch_policy_versions",
        ["channel_workspace_id"],
        unique=True,
        postgresql_where=sa.text("state = 'APPROVED'"),
    )


def _create_launch_runs() -> None:
    op.create_table(
        "launch_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("launch_policy_version_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("launch_key", sa.String(length=160), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'PREPARING'"),
            nullable=False,
        ),
        sa.Column("preparation_started_on", sa.Date(), nullable=False),
        sa.Column("launch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reason_codes",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", UUID, nullable=False),
        sa.Column("updated_by_user_id", UUID, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state in ('PREPARING','READY_TO_LAUNCH','ACTIVE','PAUSED',"
            "'COMPLETED','CANCELED')",
            name="ck_launch_runs_state",
        ),
        sa.ForeignKeyConstraint(
            ["launch_policy_version_id"],
            ["first_channel_launch_policy_versions.id"],
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "launch_key",
            name="uq_launch_runs_channel_key",
        ),
    )
    op.create_index(
        "ix_launch_runs_policy",
        "launch_runs",
        ["launch_policy_version_id"],
    )
    op.create_index(
        "ix_launch_runs_channel",
        "launch_runs",
        ["channel_workspace_id"],
    )
    op.create_index(
        "uq_launch_runs_one_open_channel",
        "launch_runs",
        ["channel_workspace_id"],
        unique=True,
        postgresql_where=sa.text(
            "state in ('PREPARING','READY_TO_LAUNCH','ACTIVE','PAUSED')"
        ),
    )


def _create_launch_scope_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_launch_policy_series_scope()
        RETURNS trigger AS $$
        DECLARE
            series_ref jsonb;
            series_ref_text text;
            series_ref_id uuid;
            seen_series_ids uuid[] := ARRAY[]::uuid[];
        BEGIN
            IF jsonb_typeof(NEW.approved_initial_series_plan_ids) <> 'array' THEN
                RAISE EXCEPTION
                    'launch policy initial series references must be an array';
            END IF;

            FOR series_ref IN
                SELECT value
                FROM jsonb_array_elements(
                    NEW.approved_initial_series_plan_ids
                )
            LOOP
                IF jsonb_typeof(series_ref) <> 'string' THEN
                    RAISE EXCEPTION
                        'launch policy initial series references must be UUID strings';
                END IF;
                series_ref_text := series_ref #>> '{}';
                IF series_ref_text !~*
                    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                    '[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN
                    RAISE EXCEPTION
                        'launch policy initial series reference is not a UUID: %',
                        series_ref_text;
                END IF;
                series_ref_id := series_ref_text::uuid;
                IF series_ref_id = ANY(seen_series_ids) THEN
                    RAISE EXCEPTION
                        'launch policy initial series references must be unique';
                END IF;
                seen_series_ids := array_append(
                    seen_series_ids,
                    series_ref_id
                );

                PERFORM 1
                FROM series_plans AS series
                WHERE series.id = series_ref_id
                  AND series.company_id = NEW.company_id
                  AND series.channel_workspace_id =
                      NEW.channel_workspace_id
                  AND series.channel_profile_version_id =
                      NEW.channel_profile_version_id
                  AND series.policy_snapshot_id = NEW.policy_snapshot_id
                  AND series.state = 'APPROVED'
                  AND jsonb_typeof(series.allowed_production_lanes) = 'array'
                  AND series.allowed_production_lanes @>
                      '["LONG_FORM"]'::jsonb
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'launch policy initial series % is not an approved '
                        'long-form plan in the exact policy scope',
                        series_ref_id;
                END IF;
            END LOOP;

            IF cardinality(seen_series_ids) <> NEW.initial_series_count THEN
                RAISE EXCEPTION
                    'launch policy initial series count does not match references';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_guard_launch_policy_series_scope
        AFTER INSERT OR UPDATE
        ON first_channel_launch_policy_versions
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION guard_launch_policy_series_scope()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_launch_run_policy_scope()
        RETURNS trigger AS $$
        BEGIN
            PERFORM 1
            FROM first_channel_launch_policy_versions AS policy
            WHERE policy.id = NEW.launch_policy_version_id
              AND policy.company_id = NEW.company_id
              AND policy.channel_workspace_id = NEW.channel_workspace_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'launch run policy, company, and channel scope mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_guard_launch_run_policy_scope
        AFTER INSERT OR UPDATE
        ON launch_runs
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION guard_launch_run_policy_scope()
        """
    )


def _drop_launch_scope_guards() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_launch_run_policy_scope ON launch_runs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_launch_policy_series_scope "
        "ON first_channel_launch_policy_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_launch_run_policy_scope()")
    op.execute("DROP FUNCTION IF EXISTS guard_launch_policy_series_scope()")


def _create_launch_policy_immutability_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_launch_policy_version_change()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'launch policy versions are immutable after insert';
            END IF;

            IF OLD.state = 'DRAFT' AND NEW.state = 'APPROVED' THEN
                IF NEW.approved_by_user_id IS NULL OR NEW.approved_at IS NULL THEN
                    RAISE EXCEPTION
                        'policy approval requires actor and timestamp';
                END IF;
                IF (
                    to_jsonb(NEW) -
                        ARRAY[
                            'state',
                            'approved_by_user_id',
                            'approved_at',
                            'updated_at'
                        ]
                    IS DISTINCT FROM
                    to_jsonb(OLD) -
                        ARRAY[
                            'state',
                            'approved_by_user_id',
                            'approved_at',
                            'updated_at'
                        ]
                ) THEN
                    RAISE EXCEPTION
                        'policy semantics cannot change during approval';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.state = 'APPROVED'
               AND NEW.state IN ('SUPERSEDED','ARCHIVED') THEN
                IF (
                    to_jsonb(NEW) - ARRAY['state','updated_at']
                    IS DISTINCT FROM
                    to_jsonb(OLD) - ARRAY['state','updated_at']
                ) THEN
                    RAISE EXCEPTION
                        'approved policy semantics and approval evidence '
                        'are immutable';
                END IF;
                RETURN NEW;
            END IF;

            RAISE EXCEPTION
                'launch policy update rejected: only DRAFT->APPROVED and '
                'APPROVED->SUPERSEDED/ARCHIVED are allowed';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_launch_policy_version_change
        BEFORE UPDATE OR DELETE ON first_channel_launch_policy_versions
        FOR EACH ROW
        EXECUTE FUNCTION guard_launch_policy_version_change()
        """
    )


def _drop_launch_policy_immutability_guard() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_launch_policy_version_change "
        "ON first_channel_launch_policy_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_launch_policy_version_change()")


def _fail_closed_if_launch_or_new_editorial_authority_exists() -> None:
    message = (
        "0048 downgrade refused: launch or post-removal editorial authority exists"
    )
    predicate = (
        "EXISTS (SELECT 1 FROM first_channel_launch_policy_versions) "
        "OR EXISTS (SELECT 1 FROM launch_runs) "
        "OR EXISTS ("
        "SELECT 1 "
        "FROM editorial_research_runs AS run "
        "LEFT JOIN vcos_0048_editorial_migration_baseline AS baseline "
        "ON baseline.entity_kind = 'EDITORIAL_RESEARCH_RUN' "
        "AND baseline.entity_id = run.id "
        "LEFT JOIN compiled_channel_policy_snapshots AS policy "
        "ON policy.id = run.policy_snapshot_id "
        "WHERE baseline.entity_id IS NULL "
        "OR baseline.baseline_hash IS DISTINCT FROM encode("
        "sha256(convert_to(("
        "to_jsonb(run) - ARRAY["
        "'channel_profile_version_id','created_by_user_id'"
        "]"
        ")::text, 'UTF8')), 'hex') "
        "OR run.channel_profile_version_id IS DISTINCT FROM "
        "policy.channel_profile_version_id "
        "OR run.created_by_user_id IS NOT NULL"
        ") "
        "OR EXISTS ("
        "SELECT 1 "
        "FROM vcos_0048_editorial_migration_baseline AS baseline "
        "LEFT JOIN editorial_research_runs AS run "
        "ON run.id = baseline.entity_id "
        "WHERE baseline.entity_kind = 'EDITORIAL_RESEARCH_RUN' "
        "AND run.id IS NULL"
        ") "
        "OR EXISTS ("
        "SELECT 1 "
        "FROM editorial_idea_candidates AS candidate "
        "LEFT JOIN vcos_0048_editorial_migration_baseline AS baseline "
        "ON baseline.entity_kind = 'EDITORIAL_IDEA_CANDIDATE' "
        "AND baseline.entity_id = candidate.id "
        "WHERE baseline.entity_id IS NULL "
        "OR baseline.baseline_hash IS DISTINCT FROM encode("
        "sha256(convert_to(("
        "to_jsonb(candidate) - ARRAY["
        "'suggested_series_plan_id','budget_readiness',"
        "'rights_policy_state','quality_state','experiment_phase',"
        "'primary_variable_under_test','baseline_refs',"
        "'comparison_group','canonical_hash','created_by_user_id'"
        "]"
        ")::text, 'UTF8')), 'hex') "
        "OR candidate.suggested_series_plan_id IS NOT NULL "
        "OR candidate.budget_readiness <> 'UNKNOWN' "
        "OR candidate.rights_policy_state <> 'UNKNOWN' "
        "OR candidate.quality_state <> 'UNKNOWN' "
        "OR candidate.experiment_phase IS NOT NULL "
        "OR candidate.primary_variable_under_test IS NOT NULL "
        "OR candidate.baseline_refs <> '[]'::jsonb "
        "OR candidate.comparison_group IS NOT NULL "
        "OR candidate.canonical_hash <> encode("
        "sha256(convert_to("
        "'editorial-idea-candidate:' || candidate.id::text, "
        "'UTF8')), 'hex') "
        "OR candidate.created_by_user_id IS NOT NULL"
        ") "
        "OR EXISTS ("
        "SELECT 1 "
        "FROM vcos_0048_editorial_migration_baseline AS baseline "
        "LEFT JOIN editorial_idea_candidates AS candidate "
        "ON candidate.id = baseline.entity_id "
        "WHERE baseline.entity_kind = 'EDITORIAL_IDEA_CANDIDATE' "
        "AND candidate.id IS NULL"
        ") "
        "OR EXISTS ("
        "SELECT 1 "
        "FROM idea_market_preflights AS preflight "
        "LEFT JOIN vcos_0048_editorial_migration_baseline AS baseline "
        "ON baseline.entity_kind = 'IDEA_MARKET_PREFLIGHT' "
        "AND baseline.entity_id = preflight.id "
        "WHERE baseline.entity_id IS NULL "
        "OR baseline.baseline_hash IS DISTINCT FROM encode("
        "sha256(convert_to(("
        "to_jsonb(preflight) - ARRAY["
        "'niche_contract_digest_ref','niche_contract_digest_hash',"
        "'target_market_digest_ref','target_market_digest_hash',"
        "'editorial_slot_ref','content_category_ref','target_market',"
        "'market_scope','market_fit_score','market_fit_threshold'"
        "]"
        ")::text, 'UTF8')), 'hex') "
        "OR preflight.niche_contract_digest_ref IS NOT NULL "
        "OR preflight.niche_contract_digest_hash IS NOT NULL "
        "OR preflight.target_market_digest_ref IS NOT NULL "
        "OR preflight.target_market_digest_hash IS NOT NULL "
        "OR preflight.editorial_slot_ref IS NOT NULL "
        "OR preflight.content_category_ref IS NOT NULL "
        "OR preflight.target_market IS NOT NULL "
        "OR preflight.market_scope <> '[]'::jsonb "
        "OR preflight.market_fit_score IS NOT NULL "
        "OR preflight.market_fit_threshold IS NOT NULL"
        ") "
        "OR EXISTS ("
        "SELECT 1 "
        "FROM vcos_0048_editorial_migration_baseline AS baseline "
        "LEFT JOIN idea_market_preflights AS preflight "
        "ON preflight.id = baseline.entity_id "
        "WHERE baseline.entity_kind = 'IDEA_MARKET_PREFLIGHT' "
        "AND preflight.id IS NULL"
        ")"
    )
    _raise_if(predicate, message)


def _remove_strict_market_preflight() -> None:
    op.drop_constraint(
        "ck_idea_market_preflights_authority_hashes",
        "idea_market_preflights",
        type_="check",
    )
    for column_name in (
        "market_fit_threshold",
        "market_fit_score",
        "market_scope",
        "target_market",
        "content_category_ref",
        "editorial_slot_ref",
        "target_market_digest_hash",
        "target_market_digest_ref",
        "niche_contract_digest_hash",
        "niche_contract_digest_ref",
    ):
        op.drop_column("idea_market_preflights", column_name)


def _remove_editorial_research_extensions() -> None:
    op.drop_constraint(
        "ck_editorial_idea_candidates_hash",
        "editorial_idea_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_editorial_idea_candidates_readiness",
        "editorial_idea_candidates",
        type_="check",
    )
    op.drop_constraint(
        "uq_editorial_idea_candidates_canonical_hash",
        "editorial_idea_candidates",
        type_="unique",
    )
    for constraint_name in (
        "fk_editorial_idea_candidates_suggested_series_plan_id_series_plans",
        "fk_editorial_idea_candidates_created_by_user_id_users",
    ):
        op.drop_constraint(
            op.f(constraint_name),
            "editorial_idea_candidates",
            type_="foreignkey",
        )
    for column_name in (
        "created_by_user_id",
        "canonical_hash",
        "comparison_group",
        "baseline_refs",
        "primary_variable_under_test",
        "experiment_phase",
        "quality_state",
        "rights_policy_state",
        "budget_readiness",
        "suggested_series_plan_id",
    ):
        op.drop_column("editorial_idea_candidates", column_name)

    op.drop_index(
        "ix_editorial_research_runs_profile_id",
        table_name="editorial_research_runs",
    )
    for constraint_name in (
        "fk_editorial_research_runs_created_by_user_id_users",
        "fk_editorial_research_runs_channel_profile_version_id_"
        "channel_profile_versions",
    ):
        op.drop_constraint(
            op.f(constraint_name),
            "editorial_research_runs",
            type_="foreignkey",
        )
    op.drop_column("editorial_research_runs", "created_by_user_id")
    op.drop_column(
        "editorial_research_runs",
        "channel_profile_version_id",
    )


def _raise_if(predicate: str, message: str) -> None:
    safe_message = message.replace("'", "''")
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {predicate} THEN
                        RAISE EXCEPTION '{safe_message}';
                    END IF;
                END;
                $$;
                """
            )
        )
        return
    connection = op.get_bind()
    if bool(connection.execute(sa.text(f"SELECT {predicate}")).scalar()):
        raise RuntimeError(message)
