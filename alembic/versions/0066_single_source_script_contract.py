"""Introduce V2 single-source script authority and scoped render holds."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0066_script_ssot"
down_revision = "0065_script_content_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_contract_replacement_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operator_authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaces_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_idea_candidates.id"), nullable=False),
        sa.Column("replaces_slot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("long_form_publish_slots.id"), nullable=False),
        sa.Column("replacement_reason", sa.String(length=160), nullable=False),
        sa.Column("source_topic_definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_topic_definitions.id"), nullable=False),
        sa.Column("source_preflight_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("idea_market_preflights.id"), nullable=False),
        sa.Column("source_evidence_pack_id", sa.String(length=160), nullable=False),
        sa.Column("source_memory_digest_id", sa.String(length=160), nullable=False),
        sa.Column("old_script_contract_version", sa.String(length=80), nullable=False),
        sa.Column("new_script_contract_version", sa.String(length=80), nullable=False),
        sa.Column("max_replacement_lineages", sa.Integer(), nullable=False),
        sa.Column("max_initial_writer_submissions", sa.Integer(), nullable=False),
        sa.Column("max_verifier_submissions", sa.Integer(), nullable=False),
        sa.Column("bounded_content_repair_policy_ref", sa.String(length=160), nullable=False),
        sa.Column("production_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qualification_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authority_hash", sa.String(length=64), nullable=False),
        sa.Column("replacement_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("editorial_idea_candidates.id")),
        sa.Column("replacement_slot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("long_form_publish_slots.id")),
        sa.Column("replacement_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("replaces_candidate_id", "new_script_contract_version", name="uq_script_contract_replacement_parent_version"),
        sa.UniqueConstraint("replacement_candidate_id", name="uq_script_contract_replacement_candidate"),
        sa.UniqueConstraint("replacement_slot_id", name="uq_script_contract_replacement_slot"),
        sa.CheckConstraint("replacement_reason = 'SCRIPT_CONTRACT_SINGLE_SOURCE_OF_TRUTH_MIGRATION'", name="ck_script_contract_replacement_reason"),
        sa.CheckConstraint("old_script_contract_version = 'V1_LEGACY' and new_script_contract_version = 'V2_SINGLE_SOURCE'", name="ck_script_contract_replacement_versions"),
        sa.CheckConstraint("max_replacement_lineages = 1 and max_initial_writer_submissions = 1 and max_verifier_submissions = 1", name="ck_script_contract_replacement_bounds"),
        sa.CheckConstraint("qualification_deadline <= production_window_end and authority_hash ~ '^[0-9a-f]{64}$'", name="ck_script_contract_replacement_authority_hash"),
    )
    op.create_index("ix_script_contract_replacement_parent", "script_contract_replacement_authorities", ["replaces_candidate_id"])

    op.add_column("editorial_idea_candidates", sa.Column("replaces_candidate_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_editorial_candidate_replaces", "editorial_idea_candidates", "editorial_idea_candidates", ["replaces_candidate_id"], ["id"])
    op.add_column("editorial_idea_candidates", sa.Column("replacement_authority_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_editorial_candidate_replacement_authority", "editorial_idea_candidates", "script_contract_replacement_authorities", ["replacement_authority_id"], ["id"])
    op.add_column("editorial_idea_candidates", sa.Column("replacement_reason", sa.String(length=160), nullable=True))
    op.add_column("editorial_idea_candidates", sa.Column("replacement_lineage_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_editorial_idea_candidates_replacement_lineage_key", "editorial_idea_candidates", ["replacement_lineage_key"])
    op.add_column("editorial_idea_candidates", sa.Column("script_contract_version", sa.String(length=80), nullable=False, server_default="V1_LEGACY"))
    op.create_check_constraint("ck_editorial_candidate_script_contract_version", "editorial_idea_candidates", "script_contract_version in ('V1_LEGACY','V2_SINGLE_SOURCE')")
    op.create_index("ix_editorial_candidate_replaces", "editorial_idea_candidates", ["replaces_candidate_id"])

    op.drop_constraint("uq_long_form_publish_slots_channel_time", "long_form_publish_slots", type_="unique")
    op.drop_constraint("uq_long_form_publish_slots_run_date", "long_form_publish_slots", type_="unique")
    op.add_column("long_form_publish_slots", sa.Column("replaces_slot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_long_form_slot_replaces", "long_form_publish_slots", "long_form_publish_slots", ["replaces_slot_id"], ["id"])
    op.add_column("long_form_publish_slots", sa.Column("replacement_authority_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_long_form_slot_replacement_authority", "long_form_publish_slots", "script_contract_replacement_authorities", ["replacement_authority_id"], ["id"])
    op.add_column("long_form_publish_slots", sa.Column("replacement_reason", sa.String(length=160), nullable=True))
    op.add_column("long_form_publish_slots", sa.Column("replacement_lineage_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_long_form_slots_replacement_lineage_key", "long_form_publish_slots", ["replacement_lineage_key"])
    op.create_index("ix_long_form_publish_slots_replaces", "long_form_publish_slots", ["replaces_slot_id"])
    op.create_index("uq_long_form_publish_slots_channel_time_primary", "long_form_publish_slots", ["channel_workspace_id", "intended_publish_at"], unique=True, postgresql_where=sa.text("replaces_slot_id is null"))
    op.create_index("uq_long_form_publish_slots_run_date_primary", "long_form_publish_slots", ["launch_run_id", "local_publish_date"], unique=True, postgresql_where=sa.text("replaces_slot_id is null"))

    op.add_column("script_qualification_runs", sa.Column("script_contract_version", sa.String(length=80), nullable=False, server_default="V1_LEGACY"))
    op.add_column("script_qualification_runs", sa.Column("canonical_compiler_version", sa.String(length=120), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("derived_canonical_script_hash", sa.String(length=64), nullable=True))
    op.add_column("script_qualification_runs", sa.Column("replacement_authority_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_script_qualification_replacement_authority", "script_qualification_runs", "script_contract_replacement_authorities", ["replacement_authority_id"], ["id"])
    op.create_check_constraint("ck_script_qualification_contract_version", "script_qualification_runs", "script_contract_version in ('V1_LEGACY','V2_SINGLE_SOURCE')")
    op.create_check_constraint("ck_script_qualification_derived_script_hash", "script_qualification_runs", "derived_canonical_script_hash is null or derived_canonical_script_hash ~ '^[0-9a-f]{64}$'")

    op.create_table(
        "canonical_script_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("script_qualification_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("script_qualification_runs.id"), nullable=False),
        sa.Column("script_contract_version", sa.String(length=80), nullable=False),
        sa.Column("compiler_version", sa.String(length=120), nullable=False),
        sa.Column("ordered_section_ids", postgresql.JSONB(), nullable=False),
        sa.Column("ordered_section_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("separator_policy", sa.String(length=120), nullable=False),
        sa.Column("normalization_policy", sa.String(length=160), nullable=False),
        sa.Column("section_set_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_script", sa.Text(), nullable=False),
        sa.Column("canonical_script_hash", sa.String(length=64), nullable=False),
        sa.Column("total_word_count", sa.Integer(), nullable=False),
        sa.Column("estimated_duration_ms", sa.Integer(), nullable=False),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("script_qualification_run_id", name="uq_canonical_script_artifact_run"),
        sa.CheckConstraint("script_contract_version = 'V2_SINGLE_SOURCE'", name="ck_canonical_script_artifact_contract"),
        sa.CheckConstraint("section_set_hash ~ '^[0-9a-f]{64}$' and canonical_script_hash ~ '^[0-9a-f]{64}$'", name="ck_canonical_script_artifact_hashes"),
        sa.CheckConstraint("total_word_count > 0 and estimated_duration_ms > 0", name="ck_canonical_script_artifact_measurements"),
    )
    op.create_index("ix_canonical_script_artifact_run", "canonical_script_artifacts", ["script_qualification_run_id"])
    op.create_index(
        "ix_canonical_script_artifact_section_set",
        "canonical_script_artifacts",
        ["script_contract_version", "compiler_version", "section_set_hash"],
    )
    op.add_column("script_qualification_runs", sa.Column("canonical_script_artifact_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_script_qualification_canonical_artifact", "script_qualification_runs", "canonical_script_artifacts", ["canonical_script_artifact_id"], ["id"])

    # Constraints are rendered through the repository naming convention. The
    # deployed 0044/0054 lineage therefore uses the concrete ``ck_*`` name,
    # even though its model declaration is named ``production_workflow_runs_state``.
    # Use literal DDL so Alembic does not apply that convention a second time.
    workflow_state_constraint = (
        "ck_production_workflow_runs_production_workflow_runs_state"
    )
    op.execute(
        "ALTER TABLE production_workflow_runs DROP CONSTRAINT IF EXISTS "
        f"{workflow_state_constraint}"
    )
    states = "'PLANNING_PENDING','PLANNING_RUNNING','ASSIGNMENT_READY','RESEARCH_PENDING','RESEARCH_RUNNING','PACKAGE_PENDING','PACKAGE_RUNNING','READY_FOR_PRODUCTION','MEDIA_PENDING','MEDIA_RUNNING','RENDER_PENDING','RENDER_RUNNING','QC_PENDING','QC_RUNNING','ARCHIVE_PENDING','ARCHIVE_RUNNING','PAUSED_AFTER_NATIVE_RENDER','FINAL_REVIEW_READY','BLOCKED','RETRY_SCHEDULED','CANCELED','FAILED_TERMINAL','DEAD_LETTERED','SUPERSEDED'"
    op.execute(
        "ALTER TABLE production_workflow_runs ADD CONSTRAINT "
        f"{workflow_state_constraint} CHECK (state in ({states}))"
    )
    op.create_table(
        "workflow_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_workflow_runs.id"), nullable=False),
        sa.Column("requested_reason", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_reason", sa.String(length=160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workflow_run_id", name="uq_workflow_hold_run"),
        sa.CheckConstraint("state in ('PENDING','ACTIVE','RELEASED')", name="ck_workflow_hold_state"),
        sa.CheckConstraint("(state = 'PENDING' and activated_at is null and released_at is null) or (state = 'ACTIVE' and activated_at is not null and released_at is null) or (state = 'RELEASED' and activated_at is not null and released_at is not null and release_reason is not null)", name="ck_workflow_hold_lifecycle"),
    )
    op.create_index("ix_workflow_holds_state", "workflow_holds", ["state", "updated_at"])

    for table, column in (("editorial_idea_candidates", "script_contract_version"), ("script_qualification_runs", "script_contract_version")):
        op.alter_column(table, column, server_default=None)
    op.execute("CREATE TRIGGER trg_prevent_script_contract_replacement_mutation BEFORE UPDATE OR DELETE ON script_contract_replacement_authorities FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update()")
    op.execute("CREATE TRIGGER trg_prevent_canonical_script_artifact_mutation BEFORE UPDATE OR DELETE ON canonical_script_artifacts FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update()")


def downgrade() -> None:
    raise RuntimeError("0066 is intentionally forward-only in production")
