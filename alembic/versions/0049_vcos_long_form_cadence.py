"""Add deterministic long-form publish slots and cadence receipts.

Revision ID: 0049_vcos_long_form_cadence
Revises: 0048_vcos_first_channel_launch
Create Date: 2026-07-30 02:00:00

Slots express publication intent only.  Cadence receipts are immutable
decisions and never constitute upload or publish authority.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0049_vcos_long_form_cadence"
down_revision: str | None = "0048_vcos_first_channel_launch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
LEGACY_PUBLISH_TIMING_SOURCE_CHECK = (
    "source in ('CHANNEL_CONFIG','HUMAN_OVERRIDE','ANALYTICS_OBSERVED_LATER')"
)
LAUNCH_POLICY_PUBLISH_TIMING_SOURCE_CHECK = (
    f"({LEGACY_PUBLISH_TIMING_SOURCE_CHECK} or "
    "source ~ '^LP:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$')"
)


def upgrade() -> None:
    _allow_launch_policy_publish_timing_sources()
    _create_long_form_publish_slots()
    _create_cadence_evaluation_receipts()
    _create_cadence_scope_guards()
    _create_cadence_receipt_immutability_guard()


def downgrade() -> None:
    _fail_closed_if_cadence_authority_exists()
    _drop_cadence_receipt_immutability_guard()
    _drop_cadence_scope_guards()
    op.drop_table("cadence_evaluation_receipts")
    op.drop_table("long_form_publish_slots")
    _restore_legacy_publish_timing_sources()


def _allow_launch_policy_publish_timing_sources() -> None:
    op.drop_constraint(
        "ck_publish_timing_suggestions_source",
        "publish_timing_suggestions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_publish_timing_suggestions_source",
        "publish_timing_suggestions",
        LAUNCH_POLICY_PUBLISH_TIMING_SOURCE_CHECK,
    )


def _restore_legacy_publish_timing_sources() -> None:
    op.drop_constraint(
        "ck_publish_timing_suggestions_source",
        "publish_timing_suggestions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_publish_timing_suggestions_source",
        "publish_timing_suggestions",
        LEGACY_PUBLISH_TIMING_SOURCE_CHECK,
    )


def _create_long_form_publish_slots() -> None:
    op.create_table(
        "long_form_publish_slots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("launch_run_id", UUID, nullable=False),
        sa.Column("launch_policy_version_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("local_publish_date", sa.Date(), nullable=False),
        sa.Column(
            "intended_publish_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "target_start_window_open_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "target_start_window_close_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=24),
            server_default=sa.text("'OPEN'"),
            nullable=False,
        ),
        sa.Column("reserved_candidate_id", UUID, nullable=True),
        sa.Column("admitted_video_project_id", UUID, nullable=True),
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
            "state in ('OPEN','RESERVED','FULFILLED','SKIPPED','CANCELED')",
            name="ck_long_form_publish_slots_state",
        ),
        sa.CheckConstraint(
            "target_start_window_open_at <= target_start_window_close_at "
            "and target_start_window_close_at < intended_publish_at",
            name="ck_long_form_publish_slots_window",
        ),
        sa.ForeignKeyConstraint(["launch_run_id"], ["launch_runs.id"]),
        sa.ForeignKeyConstraint(
            ["launch_policy_version_id"],
            ["first_channel_launch_policy_versions.id"],
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reserved_candidate_id"],
            ["editorial_idea_candidates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["admitted_video_project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_workspace_id",
            "intended_publish_at",
            name="uq_long_form_publish_slots_channel_time",
        ),
        sa.UniqueConstraint(
            "launch_run_id",
            "local_publish_date",
            name="uq_long_form_publish_slots_run_date",
        ),
    )
    op.create_index(
        "ix_long_form_publish_slots_run",
        "long_form_publish_slots",
        ["launch_run_id"],
    )
    op.create_index(
        "ix_long_form_publish_slots_intended",
        "long_form_publish_slots",
        ["intended_publish_at"],
    )


def _create_cadence_evaluation_receipts() -> None:
    op.create_table(
        "cadence_evaluation_receipts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("launch_run_id", UUID, nullable=False),
        sa.Column("launch_policy_version_id", UUID, nullable=False),
        sa.Column("publish_slot_id", UUID, nullable=True),
        sa.Column("selected_candidate_id", UUID, nullable=True),
        sa.Column("admitted_video_project_id", UUID, nullable=True),
        sa.Column("production_workflow_run_id", UUID, nullable=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "evaluation_window_key",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column(
            "public_ready_buffer_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("active_production_count", sa.Integer(), nullable=False),
        sa.Column(
            "eligible_greenlit_candidate_ids",
            JSONB,
            nullable=False,
        ),
        sa.Column("budget_provider_readiness", JSONB, nullable=False),
        sa.Column("blocking_incident_ids", JSONB, nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "public_ready_buffer_count >= 0 and active_production_count >= 0",
            name="ck_cadence_receipts_counts",
        ),
        sa.CheckConstraint(
            "decision in ("
            "'START_LONG_FORM_PRODUCTION','WAIT_BUFFER_FULL',"
            "'WAIT_NO_ELIGIBLE_CANDIDATE','WAIT_ACTIVE_PRODUCTION',"
            "'WAIT_OUTSIDE_PRODUCTION_HORIZON','WAIT_BUDGET_BLOCKED',"
            "'WAIT_POLICY_OR_RIGHTS_BLOCKED','WAIT_QUALITY_BLOCKED',"
            "'WAIT_LAUNCH_NOT_ACTIVE')",
            name="ck_cadence_receipts_decision",
        ),
        sa.ForeignKeyConstraint(["launch_run_id"], ["launch_runs.id"]),
        sa.ForeignKeyConstraint(
            ["launch_policy_version_id"],
            ["first_channel_launch_policy_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["publish_slot_id"],
            ["long_form_publish_slots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["selected_candidate_id"],
            ["editorial_idea_candidates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["admitted_video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["production_workflow_run_id"],
            ["production_workflow_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "launch_run_id",
            "evaluation_window_key",
            name="uq_cadence_receipts_run_window",
        ),
        sa.UniqueConstraint(
            "input_hash",
            name="uq_cadence_receipts_input_hash",
        ),
    )
    op.create_index(
        "ix_cadence_receipts_run",
        "cadence_evaluation_receipts",
        ["launch_run_id"],
    )
    op.create_index(
        "ix_cadence_receipts_evaluated",
        "cadence_evaluation_receipts",
        ["evaluated_at"],
    )


def _create_cadence_scope_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_long_form_publish_slot_scope()
        RETURNS trigger AS $$
        DECLARE
            scoped_policy_snapshot_id uuid;
        BEGIN
            SELECT policy.policy_snapshot_id
            INTO scoped_policy_snapshot_id
            FROM launch_runs AS run
            JOIN first_channel_launch_policy_versions AS policy
              ON policy.id = run.launch_policy_version_id
            WHERE run.id = NEW.launch_run_id
              AND policy.id = NEW.launch_policy_version_id
              AND run.company_id = NEW.company_id
              AND run.channel_workspace_id = NEW.channel_workspace_id
              AND policy.company_id = NEW.company_id
              AND policy.channel_workspace_id = NEW.channel_workspace_id
            FOR KEY SHARE OF run, policy;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'long-form slot run, policy, company, and channel scope mismatch';
            END IF;

            IF NEW.reserved_candidate_id IS NOT NULL THEN
                PERFORM 1
                FROM editorial_idea_candidates AS candidate
                WHERE candidate.id = NEW.reserved_candidate_id
                  AND candidate.company_id = NEW.company_id
                  AND candidate.channel_workspace_id =
                      NEW.channel_workspace_id
                  AND candidate.policy_snapshot_id =
                      scoped_policy_snapshot_id
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'long-form slot reserved candidate is outside launch scope';
                END IF;
            END IF;

            IF NEW.admitted_video_project_id IS NOT NULL THEN
                PERFORM 1
                FROM video_projects AS project
                WHERE project.id = NEW.admitted_video_project_id
                  AND project.company_id = NEW.company_id
                  AND project.channel_workspace_id =
                      NEW.channel_workspace_id
                  AND project.policy_snapshot_id =
                      scoped_policy_snapshot_id
                  AND project.schema_version = 'v2'
                  AND project.production_lane = 'LONG_FORM'
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'long-form slot admitted project is outside launch scope';
                END IF;
            END IF;

            IF NEW.state = 'RESERVED'
               AND (
                   NEW.reserved_candidate_id IS NULL
                   OR NEW.admitted_video_project_id IS NULL
               )
            THEN
                RAISE EXCEPTION
                    'reserved long-form slot requires candidate and project';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_guard_long_form_publish_slot_scope
        AFTER INSERT OR UPDATE
        ON long_form_publish_slots
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION guard_long_form_publish_slot_scope()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_cadence_evaluation_receipt_scope()
        RETURNS trigger AS $$
        DECLARE
            scoped_company_id uuid;
            scoped_channel_workspace_id uuid;
            scoped_policy_snapshot_id uuid;
            candidate_ref jsonb;
            candidate_ref_text text;
            candidate_ref_id uuid;
            seen_candidate_ids uuid[] := ARRAY[]::uuid[];
        BEGIN
            SELECT
                run.company_id,
                run.channel_workspace_id,
                policy.policy_snapshot_id
            INTO
                scoped_company_id,
                scoped_channel_workspace_id,
                scoped_policy_snapshot_id
            FROM launch_runs AS run
            JOIN first_channel_launch_policy_versions AS policy
              ON policy.id = run.launch_policy_version_id
            WHERE run.id = NEW.launch_run_id
              AND policy.id = NEW.launch_policy_version_id
              AND run.company_id = policy.company_id
              AND run.channel_workspace_id = policy.channel_workspace_id
            FOR KEY SHARE OF run, policy;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'cadence receipt run and launch policy scope mismatch';
            END IF;

            IF NEW.publish_slot_id IS NOT NULL THEN
                PERFORM 1
                FROM long_form_publish_slots AS slot
                WHERE slot.id = NEW.publish_slot_id
                  AND slot.launch_run_id = NEW.launch_run_id
                  AND slot.launch_policy_version_id =
                      NEW.launch_policy_version_id
                  AND slot.company_id = scoped_company_id
                  AND slot.channel_workspace_id =
                      scoped_channel_workspace_id
                  AND (
                      slot.reserved_candidate_id IS NULL
                      OR slot.reserved_candidate_id =
                          NEW.selected_candidate_id
                  )
                  AND (
                      slot.admitted_video_project_id IS NULL
                      OR slot.admitted_video_project_id =
                          NEW.admitted_video_project_id
                  )
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'cadence receipt publish slot is outside launch scope';
                END IF;
            END IF;

            IF NEW.selected_candidate_id IS NOT NULL THEN
                PERFORM 1
                FROM editorial_idea_candidates AS candidate
                WHERE candidate.id = NEW.selected_candidate_id
                  AND candidate.company_id = scoped_company_id
                  AND candidate.channel_workspace_id =
                      scoped_channel_workspace_id
                  AND candidate.policy_snapshot_id =
                      scoped_policy_snapshot_id
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'cadence receipt selected candidate is outside launch scope';
                END IF;
            END IF;

            IF NEW.admitted_video_project_id IS NOT NULL THEN
                PERFORM 1
                FROM video_projects AS project
                WHERE project.id = NEW.admitted_video_project_id
                  AND project.company_id = scoped_company_id
                  AND project.channel_workspace_id =
                      scoped_channel_workspace_id
                  AND project.policy_snapshot_id =
                      scoped_policy_snapshot_id
                  AND project.schema_version = 'v2'
                  AND project.production_lane = 'LONG_FORM'
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'cadence receipt admitted project is outside launch scope';
                END IF;
            END IF;

            IF NEW.production_workflow_run_id IS NOT NULL THEN
                PERFORM 1
                FROM production_workflow_runs AS workflow
                WHERE workflow.id = NEW.production_workflow_run_id
                  AND workflow.company_id = scoped_company_id
                  AND workflow.channel_workspace_id =
                      scoped_channel_workspace_id
                  AND workflow.production_lane = 'LONG_FORM'
                  AND NEW.admitted_video_project_id IS NOT NULL
                  AND workflow.video_project_id =
                      NEW.admitted_video_project_id
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'cadence receipt workflow is outside launch project scope';
                END IF;
            END IF;

            IF jsonb_typeof(NEW.eligible_greenlit_candidate_ids) <> 'array' THEN
                RAISE EXCEPTION
                    'cadence eligible candidate references must be an array';
            END IF;
            FOR candidate_ref IN
                SELECT value
                FROM jsonb_array_elements(
                    NEW.eligible_greenlit_candidate_ids
                )
            LOOP
                IF jsonb_typeof(candidate_ref) <> 'string' THEN
                    RAISE EXCEPTION
                        'cadence eligible candidate references must be UUID strings';
                END IF;
                candidate_ref_text := candidate_ref #>> '{}';
                IF candidate_ref_text !~*
                    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                    '[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN
                    RAISE EXCEPTION
                        'cadence eligible candidate reference is not a UUID: %',
                        candidate_ref_text;
                END IF;
                candidate_ref_id := candidate_ref_text::uuid;
                IF candidate_ref_id = ANY(seen_candidate_ids) THEN
                    RAISE EXCEPTION
                        'cadence eligible candidate references must be unique';
                END IF;
                seen_candidate_ids := array_append(
                    seen_candidate_ids,
                    candidate_ref_id
                );
                PERFORM 1
                FROM editorial_idea_candidates AS candidate
                WHERE candidate.id = candidate_ref_id
                  AND candidate.company_id = scoped_company_id
                  AND candidate.channel_workspace_id =
                      scoped_channel_workspace_id
                  AND candidate.policy_snapshot_id =
                      scoped_policy_snapshot_id
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'cadence eligible candidate % is outside launch scope',
                        candidate_ref_id;
                END IF;
            END LOOP;

            IF NEW.decision = 'START_LONG_FORM_PRODUCTION' AND (
                NEW.publish_slot_id IS NULL
                OR NEW.selected_candidate_id IS NULL
                OR NEW.admitted_video_project_id IS NULL
                OR NEW.production_workflow_run_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'start cadence receipt requires slot, candidate, project, and workflow';
            END IF;
            IF NEW.decision <> 'START_LONG_FORM_PRODUCTION' AND (
                NEW.selected_candidate_id IS NOT NULL
                OR NEW.admitted_video_project_id IS NOT NULL
                OR NEW.production_workflow_run_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'wait cadence receipt cannot claim production authority';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_guard_cadence_receipt_scope
        AFTER INSERT OR UPDATE
        ON cadence_evaluation_receipts
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION guard_cadence_evaluation_receipt_scope()
        """
    )


def _drop_cadence_scope_guards() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_cadence_receipt_scope "
        "ON cadence_evaluation_receipts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_long_form_publish_slot_scope "
        "ON long_form_publish_slots"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_cadence_evaluation_receipt_scope()")
    op.execute("DROP FUNCTION IF EXISTS guard_long_form_publish_slot_scope()")


def _create_cadence_receipt_immutability_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_cadence_receipt_change()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'cadence evaluation receipts are immutable after insert';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_cadence_receipt_change
        BEFORE UPDATE OR DELETE ON cadence_evaluation_receipts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_cadence_receipt_change()
        """
    )


def _drop_cadence_receipt_immutability_guard() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_cadence_receipt_change "
        "ON cadence_evaluation_receipts"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_cadence_receipt_change()")


def _fail_closed_if_cadence_authority_exists() -> None:
    message = (
        "0049 downgrade refused: long-form cadence slots or immutable "
        "evaluation receipts or launch-policy timing suggestions exist"
    )
    predicate = (
        "EXISTS (SELECT 1 FROM cadence_evaluation_receipts) "
        "OR EXISTS (SELECT 1 FROM long_form_publish_slots) "
        "OR EXISTS ("
        "SELECT 1 FROM publish_timing_suggestions WHERE source LIKE 'LP:%'"
        ")"
    )
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
