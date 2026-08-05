"""Add current topic eligibility and durable pre-admission script qualification.

Revision ID: 0058_editorial_script_qual
Revises: 0057_learning_governance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0058_editorial_script_qual"
down_revision: str | None = "0057_learning_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("editorial_idea_candidates", sa.Column("parent_candidate_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("editorial_idea_candidates", sa.Column("topic_repair_depth", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key("fk_editorial_candidate_parent", "editorial_idea_candidates", "editorial_idea_candidates", ["parent_candidate_id"], ["id"])
    op.create_index("ix_editorial_candidate_parent", "editorial_idea_candidates", ["parent_candidate_id"])
    op.create_check_constraint("ck_editorial_candidate_topic_repair_depth", "editorial_idea_candidates", "topic_repair_depth between 0 and 2")

    op.create_table(
        "editorial_topic_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("editorial_idea_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_idea_candidates.id"), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel_workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_workspaces.id"), nullable=False),
        sa.Column("policy_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False),
        sa.Column("topic_definition_version", sa.Integer(), nullable=False),
        sa.Column("topic_definition_hash", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(80), nullable=False), sa.Column("subject_name", sa.Text(), nullable=False), sa.Column("subject_canonical_id", sa.String(300), nullable=False),
        sa.Column("subject_evidence_refs", postgresql.JSONB(), nullable=False), sa.Column("subject_evidence_spans", postgresql.JSONB(), nullable=False),
        sa.Column("target_audience", sa.Text(), nullable=False), sa.Column("audience_problem", sa.Text(), nullable=False), sa.Column("content_pillar", sa.Text(), nullable=False), sa.Column("production_goal", sa.Text(), nullable=False),
        sa.Column("scope_inclusions", postgresql.JSONB(), nullable=False), sa.Column("exclusions", postgresql.JSONB(), nullable=False),
        sa.Column("central_question_or_thesis", sa.Text(), nullable=False), sa.Column("learning_outcome", sa.Text(), nullable=False), sa.Column("viewer_value", sa.Text(), nullable=False),
        sa.Column("content_mode", sa.String(32), nullable=False), sa.Column("channel_contract_ref", postgresql.JSONB(), nullable=False), sa.Column("source_classification_refs", postgresql.JSONB(), nullable=False),
        sa.Column("series_binding", postgresql.JSONB(), nullable=True), sa.Column("standalone_self_containment_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("parent_topic_definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_topic_definitions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("editorial_idea_candidate_id", "topic_definition_version", name="uq_topic_definition_candidate_version"), sa.UniqueConstraint("topic_definition_hash", name="uq_topic_definition_hash"),
        sa.CheckConstraint("topic_definition_version > 0", name="ck_topic_definition_version"), sa.CheckConstraint("content_mode in ('STANDALONE','SERIES_EPISODE')", name="ck_topic_definition_content_mode"), sa.CheckConstraint("topic_definition_hash ~ '^[0-9a-f]{64}$'", name="ck_topic_definition_hash"),
    )
    op.create_index("ix_topic_definition_candidate", "editorial_topic_definitions", ["editorial_idea_candidate_id"])
    op.create_index("ix_topic_definition_channel", "editorial_topic_definitions", ["channel_workspace_id"])
    op.create_table(
        "editorial_topic_definition_gate_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("editorial_topic_definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_topic_definitions.id"), nullable=False),
        sa.Column("editorial_idea_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_idea_candidates.id"), nullable=False),
        sa.Column("gate_version", sa.String(120), nullable=False), sa.Column("state", sa.String(16), nullable=False), sa.Column("current_production_eligibility", sa.Boolean(), nullable=False), sa.Column("primary_reason_code", sa.String(160), nullable=True), sa.Column("reason_codes", postgresql.JSONB(), nullable=False), sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("receipt_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("editorial_topic_definition_id", "gate_version", name="uq_topic_gate_definition_version"), sa.CheckConstraint("state in ('PASS','BLOCK')", name="ck_topic_gate_state"), sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'", name="ck_topic_gate_hashes"),
    )
    op.create_index("ix_topic_gate_candidate", "editorial_topic_definition_gate_receipts", ["editorial_idea_candidate_id"])
    op.create_table(
        "script_qualification_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("editorial_idea_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_idea_candidates.id"), nullable=False), sa.Column("publish_slot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("long_form_publish_slots.id"), nullable=False), sa.Column("launch_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("launch_runs.id"), nullable=False), sa.Column("topic_definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_topic_definitions.id"), nullable=False),
        sa.Column("topic_definition_hash", sa.String(64), nullable=False), sa.Column("script_assignment", postgresql.JSONB(), nullable=False), sa.Column("script_assignment_hash", sa.String(64), nullable=False), sa.Column("factual_evidence_pack", postgresql.JSONB(), nullable=False), sa.Column("factual_evidence_pack_hash", sa.String(64), nullable=False), sa.Column("memory_digest", postgresql.JSONB(), nullable=False), sa.Column("memory_digest_hash", sa.String(64), nullable=False), sa.Column("writer_prompt_version", sa.String(120), nullable=False), sa.Column("verifier_prompt_version", sa.String(120), nullable=False), sa.Column("gate_policy_version", sa.String(120), nullable=False), sa.Column("model", sa.String(160), nullable=False), sa.Column("logical_attempt_number", sa.Integer(), server_default="1", nullable=False), sa.Column("logical_identity_hash", sa.String(64), nullable=False), sa.Column("state", sa.String(48), server_default="RESERVED", nullable=False), sa.Column("writer_attempt_key", sa.String(200), nullable=False), sa.Column("verifier_attempt_key", sa.String(200), nullable=False),
        sa.Column("writer_receipt", postgresql.JSONB(), nullable=True), sa.Column("verifier_receipt", postgresql.JSONB(), nullable=True), sa.Column("script_payload", postgresql.JSONB(), nullable=True), sa.Column("result_receipts", postgresql.JSONB(), nullable=True), sa.Column("failure_receipt", postgresql.JSONB(), nullable=True), sa.Column("repair_attempts", sa.Integer(), server_default="0", nullable=False), sa.Column("reserved_cost_usd", sa.Numeric(18,6), server_default="0", nullable=False), sa.Column("consumed_cost_usd", sa.Numeric(18,6), server_default="0", nullable=False), sa.Column("admitted_video_project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_projects.id"), nullable=True), sa.Column("production_workflow_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_workflow_runs.id"), nullable=True), sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("logical_identity_hash", name="uq_script_qualification_logical_identity"), sa.UniqueConstraint("publish_slot_id", name="uq_script_qualification_slot"), sa.CheckConstraint("logical_attempt_number > 0 and repair_attempts between 0 and 1", name="ck_script_qualification_attempts"), sa.CheckConstraint("state in ('RESERVED','WRITER_DISPATCHED','SCRIPT_GENERATED','STRUCTURAL_CHECKED','CLAIM_INVENTORY_CHECKED','GROUNDING_CHECKED','VERIFIER_DISPATCHED','EDITORIAL_CHECKED','MEMORY_CHECKED','REPAIRABLE_BLOCK','REPAIR_DISPATCHED','REVERIFYING','QUALIFIED','BLOCKED_NON_REPAIRABLE','BLOCKED_REPAIR_BUDGET_EXHAUSTED','COOLDOWN','SUPERSEDED')", name="ck_script_qualification_state"), sa.CheckConstraint("topic_definition_hash ~ '^[0-9a-f]{64}$' and script_assignment_hash ~ '^[0-9a-f]{64}$' and factual_evidence_pack_hash ~ '^[0-9a-f]{64}$' and memory_digest_hash ~ '^[0-9a-f]{64}$' and logical_identity_hash ~ '^[0-9a-f]{64}$'", name="ck_script_qualification_hashes"),
    )
    op.create_index("ix_script_qualification_candidate", "script_qualification_runs", ["editorial_idea_candidate_id"])
    op.create_index("ix_script_qualification_state", "script_qualification_runs", ["state"])
    op.create_table(
        "script_qualification_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("script_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=False), sa.Column("result", sa.String(16), nullable=False), sa.Column("script_hash", sa.String(64), nullable=False), sa.Column("script_assignment_hash", sa.String(64), nullable=False), sa.Column("factual_evidence_pack_hash", sa.String(64), nullable=False), sa.Column("content", postgresql.JSONB(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("script_qualification_run_id", name="uq_script_qualification_receipt_run"), sa.CheckConstraint("result in ('PASS','BLOCK')", name="ck_script_qualification_receipt_result"), sa.CheckConstraint("script_hash ~ '^[0-9a-f]{64}$' and script_assignment_hash ~ '^[0-9a-f]{64}$' and factual_evidence_pack_hash ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'", name="ck_script_qualification_receipt_hashes"),
    )
    op.drop_constraint("ck_cadence_receipts_decision", "cadence_evaluation_receipts", type_="check")
    op.create_check_constraint("ck_cadence_receipts_decision", "cadence_evaluation_receipts", "decision in ('START_LONG_FORM_PRODUCTION','START_SCRIPT_QUALIFICATION','WAIT_BUFFER_FULL','WAIT_NO_ELIGIBLE_CANDIDATE','WAIT_ACTIVE_PRODUCTION','WAIT_OUTSIDE_PRODUCTION_HORIZON','WAIT_BUDGET_BLOCKED','WAIT_PROVIDER_AUTHORITY','WAIT_POLICY_OR_RIGHTS_BLOCKED','WAIT_QUALITY_BLOCKED','WAIT_LAUNCH_NOT_ACTIVE')")
    op.add_column("cadence_evaluation_receipts", sa.Column("script_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=True))
    op.drop_constraint("ck_long_form_publish_slots_state", "long_form_publish_slots", type_="check")
    op.create_check_constraint(
        "ck_long_form_publish_slots_state",
        "long_form_publish_slots",
        "state in ('OPEN','QUALIFICATION_RESERVED','RESERVED','FULFILLED','SKIPPED','CANCELED')",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_long_form_publish_slot_scope()
        RETURNS trigger AS $$
        DECLARE
            scoped_policy_snapshot_id uuid;
        BEGIN
            SELECT policy.policy_snapshot_id INTO scoped_policy_snapshot_id
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
                RAISE EXCEPTION 'long-form slot run, policy, company, and channel scope mismatch';
            END IF;
            IF NEW.reserved_candidate_id IS NOT NULL THEN
                PERFORM 1 FROM editorial_idea_candidates AS candidate
                WHERE candidate.id = NEW.reserved_candidate_id
                  AND candidate.company_id = NEW.company_id
                  AND candidate.channel_workspace_id = NEW.channel_workspace_id
                  AND candidate.policy_snapshot_id = scoped_policy_snapshot_id
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'long-form slot reserved candidate is outside launch scope';
                END IF;
            END IF;
            IF NEW.admitted_video_project_id IS NOT NULL THEN
                PERFORM 1 FROM video_projects AS project
                WHERE project.id = NEW.admitted_video_project_id
                  AND project.company_id = NEW.company_id
                  AND project.channel_workspace_id = NEW.channel_workspace_id
                  AND project.policy_snapshot_id = scoped_policy_snapshot_id
                  AND project.schema_version = 'v2'
                  AND project.production_lane = 'LONG_FORM'
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'long-form slot admitted project is outside launch scope';
                END IF;
            END IF;
            IF NEW.state = 'QUALIFICATION_RESERVED'
               AND (NEW.reserved_candidate_id IS NULL OR NEW.admitted_video_project_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'qualification-reserved slot requires candidate and forbids project';
            END IF;
            IF NEW.state = 'RESERVED'
               AND (NEW.reserved_candidate_id IS NULL OR NEW.admitted_video_project_id IS NULL)
            THEN
                RAISE EXCEPTION 'reserved long-form slot requires candidate and project';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
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
            SELECT run.company_id, run.channel_workspace_id, policy.policy_snapshot_id
              INTO scoped_company_id, scoped_channel_workspace_id, scoped_policy_snapshot_id
              FROM launch_runs AS run
              JOIN first_channel_launch_policy_versions AS policy
                ON policy.id = run.launch_policy_version_id
             WHERE run.id = NEW.launch_run_id
               AND policy.id = NEW.launch_policy_version_id
               AND run.company_id = policy.company_id
               AND run.channel_workspace_id = policy.channel_workspace_id
             FOR KEY SHARE OF run, policy;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'cadence receipt run and launch policy scope mismatch';
            END IF;
            IF NEW.publish_slot_id IS NOT NULL THEN
                PERFORM 1 FROM long_form_publish_slots AS slot
                 WHERE slot.id = NEW.publish_slot_id
                   AND slot.launch_run_id = NEW.launch_run_id
                   AND slot.launch_policy_version_id = NEW.launch_policy_version_id
                   AND slot.company_id = scoped_company_id
                   AND slot.channel_workspace_id = scoped_channel_workspace_id
                   AND (slot.reserved_candidate_id IS NULL OR slot.reserved_candidate_id = NEW.selected_candidate_id)
                   AND (slot.admitted_video_project_id IS NULL OR slot.admitted_video_project_id = NEW.admitted_video_project_id)
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'cadence receipt publish slot is outside launch scope';
                END IF;
            END IF;
            IF NEW.selected_candidate_id IS NOT NULL THEN
                PERFORM 1 FROM editorial_idea_candidates AS candidate
                 WHERE candidate.id = NEW.selected_candidate_id
                   AND candidate.company_id = scoped_company_id
                   AND candidate.channel_workspace_id = scoped_channel_workspace_id
                   AND candidate.policy_snapshot_id = scoped_policy_snapshot_id
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'cadence receipt selected candidate is outside launch scope';
                END IF;
            END IF;
            IF NEW.admitted_video_project_id IS NOT NULL THEN
                PERFORM 1 FROM video_projects AS project
                 WHERE project.id = NEW.admitted_video_project_id
                   AND project.company_id = scoped_company_id
                   AND project.channel_workspace_id = scoped_channel_workspace_id
                   AND project.policy_snapshot_id = scoped_policy_snapshot_id
                   AND project.schema_version = 'v2'
                   AND project.production_lane = 'LONG_FORM'
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'cadence receipt admitted project is outside launch scope';
                END IF;
            END IF;
            IF NEW.production_workflow_run_id IS NOT NULL THEN
                PERFORM 1 FROM production_workflow_runs AS workflow
                 WHERE workflow.id = NEW.production_workflow_run_id
                   AND workflow.company_id = scoped_company_id
                   AND workflow.channel_workspace_id = scoped_channel_workspace_id
                   AND workflow.production_lane = 'LONG_FORM'
                   AND NEW.admitted_video_project_id IS NOT NULL
                   AND workflow.video_project_id = NEW.admitted_video_project_id
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'cadence receipt workflow is outside launch project scope';
                END IF;
            END IF;
            IF NEW.script_qualification_run_id IS NOT NULL THEN
                PERFORM 1 FROM script_qualification_runs AS qualification
                 WHERE qualification.id = NEW.script_qualification_run_id
                   AND qualification.launch_run_id = NEW.launch_run_id
                   AND qualification.publish_slot_id = NEW.publish_slot_id
                   AND qualification.editorial_idea_candidate_id = NEW.selected_candidate_id
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'cadence receipt qualification is outside selected candidate scope';
                END IF;
            END IF;
            IF jsonb_typeof(NEW.eligible_greenlit_candidate_ids) <> 'array' THEN
                RAISE EXCEPTION 'cadence eligible candidate references must be an array';
            END IF;
            FOR candidate_ref IN SELECT value FROM jsonb_array_elements(NEW.eligible_greenlit_candidate_ids) LOOP
                IF jsonb_typeof(candidate_ref) <> 'string' THEN
                    RAISE EXCEPTION 'cadence eligible candidate references must be UUID strings';
                END IF;
                candidate_ref_text := candidate_ref #>> '{}';
                IF candidate_ref_text !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                    RAISE EXCEPTION 'cadence eligible candidate reference is not a UUID: %', candidate_ref_text;
                END IF;
                candidate_ref_id := candidate_ref_text::uuid;
                IF candidate_ref_id = ANY(seen_candidate_ids) THEN
                    RAISE EXCEPTION 'cadence eligible candidate references must be unique';
                END IF;
                seen_candidate_ids := array_append(seen_candidate_ids, candidate_ref_id);
                PERFORM 1 FROM editorial_idea_candidates AS candidate
                 WHERE candidate.id = candidate_ref_id
                   AND candidate.company_id = scoped_company_id
                   AND candidate.channel_workspace_id = scoped_channel_workspace_id
                   AND candidate.policy_snapshot_id = scoped_policy_snapshot_id
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'cadence eligible candidate % is outside launch scope', candidate_ref_id;
                END IF;
            END LOOP;
            IF NEW.decision = 'START_LONG_FORM_PRODUCTION'
               AND (NEW.publish_slot_id IS NULL OR NEW.selected_candidate_id IS NULL OR NEW.admitted_video_project_id IS NULL OR NEW.production_workflow_run_id IS NULL)
            THEN
                RAISE EXCEPTION 'start cadence receipt requires slot, candidate, project, and workflow';
            END IF;
            IF NEW.decision = 'START_SCRIPT_QUALIFICATION'
               AND (NEW.publish_slot_id IS NULL OR NEW.selected_candidate_id IS NULL OR NEW.script_qualification_run_id IS NULL OR NEW.admitted_video_project_id IS NOT NULL OR NEW.production_workflow_run_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'qualification cadence receipt requires slot, candidate, and qualification only';
            END IF;
            IF NEW.decision NOT IN ('START_LONG_FORM_PRODUCTION', 'START_SCRIPT_QUALIFICATION')
               AND (NEW.selected_candidate_id IS NOT NULL OR NEW.admitted_video_project_id IS NOT NULL OR NEW.production_workflow_run_id IS NOT NULL OR NEW.script_qualification_run_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'wait cadence receipt cannot claim production authority';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    if bool(connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM long_form_publish_slots WHERE state = 'QUALIFICATION_RESERVED')")).scalar()):
        raise RuntimeError("0058 downgrade refused: qualification-reserved publish slots exist")
    op.drop_constraint("ck_long_form_publish_slots_state", "long_form_publish_slots", type_="check")
    op.create_check_constraint(
        "ck_long_form_publish_slots_state",
        "long_form_publish_slots",
        "state in ('OPEN','RESERVED','FULFILLED','SKIPPED','CANCELED')",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_long_form_publish_slot_scope()
        RETURNS trigger AS $$
        DECLARE
            scoped_policy_snapshot_id uuid;
        BEGIN
            SELECT policy.policy_snapshot_id INTO scoped_policy_snapshot_id
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
                RAISE EXCEPTION 'long-form slot run, policy, company, and channel scope mismatch';
            END IF;
            IF NEW.reserved_candidate_id IS NOT NULL THEN
                PERFORM 1 FROM editorial_idea_candidates AS candidate
                WHERE candidate.id = NEW.reserved_candidate_id
                  AND candidate.company_id = NEW.company_id
                  AND candidate.channel_workspace_id = NEW.channel_workspace_id
                  AND candidate.policy_snapshot_id = scoped_policy_snapshot_id
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'long-form slot reserved candidate is outside launch scope';
                END IF;
            END IF;
            IF NEW.admitted_video_project_id IS NOT NULL THEN
                PERFORM 1 FROM video_projects AS project
                WHERE project.id = NEW.admitted_video_project_id
                  AND project.company_id = NEW.company_id
                  AND project.channel_workspace_id = NEW.channel_workspace_id
                  AND project.policy_snapshot_id = scoped_policy_snapshot_id
                  AND project.schema_version = 'v2'
                  AND project.production_lane = 'LONG_FORM'
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'long-form slot admitted project is outside launch scope';
                END IF;
            END IF;
            IF NEW.state = 'RESERVED'
               AND (NEW.reserved_candidate_id IS NULL OR NEW.admitted_video_project_id IS NULL)
            THEN
                RAISE EXCEPTION 'reserved long-form slot requires candidate and project';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.drop_column("cadence_evaluation_receipts", "script_qualification_run_id")
    op.drop_constraint("ck_cadence_receipts_decision", "cadence_evaluation_receipts", type_="check")
    op.create_check_constraint("ck_cadence_receipts_decision", "cadence_evaluation_receipts", "decision in ('START_LONG_FORM_PRODUCTION','WAIT_BUFFER_FULL','WAIT_NO_ELIGIBLE_CANDIDATE','WAIT_ACTIVE_PRODUCTION','WAIT_OUTSIDE_PRODUCTION_HORIZON','WAIT_BUDGET_BLOCKED','WAIT_PROVIDER_AUTHORITY','WAIT_POLICY_OR_RIGHTS_BLOCKED','WAIT_QUALITY_BLOCKED','WAIT_LAUNCH_NOT_ACTIVE')")
    op.drop_table("script_qualification_receipts")
    op.drop_index("ix_script_qualification_state", table_name="script_qualification_runs")
    op.drop_index("ix_script_qualification_candidate", table_name="script_qualification_runs")
    op.drop_table("script_qualification_runs")
    op.drop_index("ix_topic_gate_candidate", table_name="editorial_topic_definition_gate_receipts")
    op.drop_table("editorial_topic_definition_gate_receipts")
    op.drop_index("ix_topic_definition_channel", table_name="editorial_topic_definitions")
    op.drop_index("ix_topic_definition_candidate", table_name="editorial_topic_definitions")
    op.drop_table("editorial_topic_definitions")
    op.drop_constraint("ck_editorial_candidate_topic_repair_depth", "editorial_idea_candidates", type_="check")
    op.drop_index("ix_editorial_candidate_parent", table_name="editorial_idea_candidates")
    op.drop_constraint("fk_editorial_candidate_parent", "editorial_idea_candidates", type_="foreignkey")
    op.drop_column("editorial_idea_candidates", "topic_repair_depth")
    op.drop_column("editorial_idea_candidates", "parent_candidate_id")
