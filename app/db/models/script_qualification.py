"""Durable editorial-topic and script-qualification authorities.

These rows intentionally sit before ``ProjectAdmissionDecision``.  A
historical GREENLIT candidate remains immutable; its *current* eligibility is
represented by a versioned topic-gate receipt and, later, a qualification
receipt.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, utc_updated_at, uuid_pk


class EditorialTopicDefinition(Base):
    __tablename__ = "editorial_topic_definitions"

    id: Mapped[uuid.UUID] = uuid_pk()
    editorial_idea_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    channel_workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False
    )
    policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compiled_channel_policy_snapshots.id"), nullable=False
    )
    topic_definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_name: Mapped[str] = mapped_column(Text, nullable=False)
    subject_canonical_id: Mapped[str] = mapped_column(String(300), nullable=False)
    subject_evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    subject_evidence_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    audience_problem: Mapped[str] = mapped_column(Text, nullable=False)
    content_pillar: Mapped[str] = mapped_column(Text, nullable=False)
    production_goal: Mapped[str] = mapped_column(Text, nullable=False)
    scope_inclusions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    exclusions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    central_question_or_thesis: Mapped[str] = mapped_column(Text, nullable=False)
    learning_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    viewer_value: Mapped[str] = mapped_column(Text, nullable=False)
    content_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_contract_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_classification_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    series_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    standalone_self_containment_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parent_topic_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_topic_definitions.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("editorial_idea_candidate_id", "topic_definition_version", name="uq_topic_definition_candidate_version"),
        UniqueConstraint("topic_definition_hash", name="uq_topic_definition_hash"),
        CheckConstraint("topic_definition_version > 0", name="ck_topic_definition_version"),
        CheckConstraint("content_mode in ('STANDALONE','SERIES_EPISODE')", name="ck_topic_definition_content_mode"),
        CheckConstraint("topic_definition_hash ~ '^[0-9a-f]{64}$'", name="ck_topic_definition_hash"),
        Index("ix_topic_definition_candidate", "editorial_idea_candidate_id"),
        Index("ix_topic_definition_channel", "channel_workspace_id"),
    )


class EditorialTopicDefinitionGateReceipt(Base):
    __tablename__ = "editorial_topic_definition_gate_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    editorial_topic_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_topic_definitions.id"), nullable=False
    )
    editorial_idea_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id"), nullable=False
    )
    gate_version: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    current_production_eligibility: Mapped[bool] = mapped_column(Boolean, nullable=False)
    primary_reason_code: Mapped[str | None] = mapped_column(String(160))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("editorial_topic_definition_id", "gate_version", name="uq_topic_gate_definition_version"),
        CheckConstraint("state in ('PASS','BLOCK')", name="ck_topic_gate_state"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'", name="ck_topic_gate_hashes"),
        Index("ix_topic_gate_candidate", "editorial_idea_candidate_id"),
    )


class ScriptQualificationRun(Base):
    __tablename__ = "script_qualification_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    editorial_idea_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id"), nullable=False
    )
    publish_slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("long_form_publish_slots.id"), nullable=False
    )
    launch_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("launch_runs.id"), nullable=False
    )
    topic_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_topic_definitions.id"), nullable=False
    )
    topic_definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    script_assignment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    script_assignment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    factual_evidence_pack: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    factual_evidence_pack_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_digest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    memory_digest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nullable only for immutable historical rows.  Current production
    # eligibility is enforced by ScriptQualificationService.require_pass.
    runtime_contract: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    runtime_contract_hash: Mapped[str | None] = mapped_column(String(64))
    assignment_resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assignment_resolution_hash: Mapped[str | None] = mapped_column(String(64))
    # V1 remains immutable/audit-only. New execution binds V2 explicitly.
    script_contract_version: Mapped[str] = mapped_column(
        String(80), nullable=False, default="V1_LEGACY"
    )
    canonical_compiler_version: Mapped[str | None] = mapped_column(String(120))
    canonical_script_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_script_artifacts.id")
    )
    derived_canonical_script_hash: Mapped[str | None] = mapped_column(String(64))
    replacement_authority_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_contract_replacement_authorities.id")
    )
    episode_reservation_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    writer_prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    verifier_prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    gate_policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    logical_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    logical_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_qualification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id")
    )
    recovery_key: Mapped[str | None] = mapped_column(String(300), unique=True)
    recovery_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logical_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(48), nullable=False, default="RESERVED")
    writer_attempt_key: Mapped[str] = mapped_column(String(200), nullable=False)
    verifier_attempt_key: Mapped[str] = mapped_column(String(200), nullable=False)
    writer_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verifier_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    script_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_receipts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # A terminal qualification settlement is a separate, durable authority
    # from the writer/verifier result.  It records what happened to the
    # cadence slot and any series capacity, so a blocked run cannot silently
    # leave a future publish slot reserved forever.
    terminal_settlement_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Provider-outcome recovery may be revisited as new evidence arrives.
    # Entries are appended only and each carries its own content hash.
    provider_outcome_reconciliation_receipts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    repair_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    consumed_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    admitted_video_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("video_projects.id"))
    production_workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_workflow_runs.id"))
    cooldown_until: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("logical_identity_hash", name="uq_script_qualification_logical_identity"),
        UniqueConstraint("publish_slot_id", "logical_attempt_number", name="uq_script_qualification_slot_attempt"),
        CheckConstraint("logical_attempt_number > 0 and repair_attempts between 0 and 1", name="ck_script_qualification_attempts"),
        CheckConstraint("state in ('RESERVED','RECOVERY_AUTHORIZED','WRITER_SUBMIT_PENDING','WRITER_BACKGROUND_SUBMITTED','WRITER_QUEUED','WRITER_IN_PROGRESS','WRITER_DISPATCHED','SCRIPT_GENERATED','STRUCTURAL_CHECKED','CLAIM_INVENTORY_CHECKED','GROUNDING_CHECKED','VERIFIER_SUBMIT_PENDING','VERIFIER_BACKGROUND_SUBMITTED','VERIFIER_QUEUED','VERIFIER_IN_PROGRESS','VERIFIER_DISPATCHED','EDITORIAL_CHECKED','MEMORY_CHECKED','REPAIRABLE_BLOCK','REPAIR_DISPATCHED','REVERIFYING','QUALIFIED','BLOCKED_NON_REPAIRABLE','BLOCKED_REPAIR_BUDGET_EXHAUSTED','COOLDOWN','SUPERSEDED')", name="ck_script_qualification_state"),
        CheckConstraint("topic_definition_hash ~ '^[0-9a-f]{64}$' and script_assignment_hash ~ '^[0-9a-f]{64}$' and factual_evidence_pack_hash ~ '^[0-9a-f]{64}$' and memory_digest_hash ~ '^[0-9a-f]{64}$' and logical_identity_hash ~ '^[0-9a-f]{64}$'", name="ck_script_qualification_hashes"),
        CheckConstraint("runtime_contract_hash is null or runtime_contract_hash ~ '^[0-9a-f]{64}$'", name="ck_script_qualification_runtime_contract_hash"),
        CheckConstraint("assignment_resolution_hash is null or assignment_resolution_hash ~ '^[0-9a-f]{64}$'", name="ck_script_qualification_assignment_resolution_hash"),
        CheckConstraint(
            "script_contract_version in ('V1_LEGACY','V2_SINGLE_SOURCE')",
            name="ck_script_qualification_contract_version",
        ),
        CheckConstraint(
            "derived_canonical_script_hash is null or derived_canonical_script_hash ~ '^[0-9a-f]{64}$'",
            name="ck_script_qualification_derived_script_hash",
        ),
        Index("ix_script_qualification_candidate", "editorial_idea_candidate_id"),
        Index("ix_script_qualification_state", "state"),
    )


class CanonicalScriptArtifact(Base):
    """Immutable compiler output for a V2 section set."""

    __tablename__ = "canonical_script_artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    script_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_qualification_runs.id"),
        nullable=False,
    )
    script_contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(120), nullable=False)
    ordered_section_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    ordered_section_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    separator_policy: Mapped[str] = mapped_column(String(120), nullable=False)
    normalization_policy: Mapped[str] = mapped_column(String(160), nullable=False)
    section_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_script: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    total_word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    compiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "script_qualification_run_id",
            name="uq_canonical_script_artifact_run",
        ),
        Index(
            "ix_canonical_script_artifact_section_set",
            "script_contract_version",
            "compiler_version",
            "section_set_hash",
        ),
        CheckConstraint(
            "script_contract_version = 'V2_SINGLE_SOURCE'",
            name="ck_canonical_script_artifact_contract",
        ),
        CheckConstraint(
            "section_set_hash ~ '^[0-9a-f]{64}$' and canonical_script_hash ~ '^[0-9a-f]{64}$'",
            name="ck_canonical_script_artifact_hashes",
        ),
        CheckConstraint(
            "total_word_count > 0 and estimated_duration_ms > 0",
            name="ck_canonical_script_artifact_measurements",
        ),
        Index("ix_canonical_script_artifact_run", "script_qualification_run_id"),
    )


class ScriptContractReplacementAuthority(Base):
    """One explicit operator-authorized replacement of a terminal lineage."""

    __tablename__ = "script_contract_replacement_authorities"

    id: Mapped[uuid.UUID] = uuid_pk()
    operator_authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    replaces_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id"), nullable=False
    )
    replaces_slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("long_form_publish_slots.id"), nullable=False
    )
    replacement_reason: Mapped[str] = mapped_column(String(160), nullable=False)
    source_topic_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_topic_definitions.id"), nullable=False
    )
    source_preflight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("idea_market_preflights.id"), nullable=False
    )
    source_evidence_pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_memory_digest_id: Mapped[str] = mapped_column(String(160), nullable=False)
    old_script_contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    new_script_contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    max_replacement_lineages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_initial_writer_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_verifier_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    bounded_content_repair_policy_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    production_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qualification_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authority_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_idea_candidates.id")
    )
    replacement_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("long_form_publish_slots.id")
    )
    replacement_qualification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id")
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint(
            "replaces_candidate_id",
            "new_script_contract_version",
            name="uq_script_contract_replacement_parent_version",
        ),
        UniqueConstraint(
            "replacement_candidate_id",
            name="uq_script_contract_replacement_candidate",
        ),
        UniqueConstraint(
            "replacement_slot_id",
            name="uq_script_contract_replacement_slot",
        ),
        CheckConstraint(
            "replacement_reason = 'SCRIPT_CONTRACT_SINGLE_SOURCE_OF_TRUTH_MIGRATION'",
            name="ck_script_contract_replacement_reason",
        ),
        CheckConstraint(
            "old_script_contract_version = 'V1_LEGACY' and new_script_contract_version = 'V2_SINGLE_SOURCE'",
            name="ck_script_contract_replacement_versions",
        ),
        CheckConstraint(
            "max_replacement_lineages = 1 and max_initial_writer_submissions = 1 and max_verifier_submissions = 1",
            name="ck_script_contract_replacement_bounds",
        ),
        CheckConstraint(
            "qualification_deadline <= production_window_end and authority_hash ~ '^[0-9a-f]{64}$'",
            name="ck_script_contract_replacement_authority_hash",
        ),
        Index("ix_script_contract_replacement_parent", "replaces_candidate_id"),
    )


class SeriesEpisodeReservation(Base):
    """Durable, pre-admission ownership of one exact SeriesRun episode.

    A ``SeriesRun`` continues to own its capacity and episode sequence.  This
    authority merely records which pre-admission qualification owns the exact
    episode that has already been allocated from that sequence.  It is the
    bridge between a frozen qualification assignment and final admission.
    """

    __tablename__ = "series_episode_reservations"

    id: Mapped[uuid.UUID] = uuid_pk()
    script_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_qualification_runs.id"),
        nullable=False,
    )
    series_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_plans.id"), nullable=False
    )
    series_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series_runs.id"), nullable=False
    )
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_role: Mapped[str] = mapped_column(String(120), nullable=False)
    episode_delta: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_resolution_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reservation_authority_version: Mapped[str] = mapped_column(
        String(120), nullable=False
    )
    state: Mapped[str] = mapped_column(String(48), nullable=False, default="RESERVED")
    released_reason_code: Mapped[str | None] = mapped_column(String(160))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_admission_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_admission_decisions.id")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_reason_code: Mapped[str | None] = mapped_column(String(160))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint(
            "script_qualification_run_id",
            name="uq_series_episode_reservations_qualification",
        ),
        # Episode numbers are never silently recycled.  A released authority
        # frees capacity, but preserves its audit identity and prevents a
        # different qualification from inheriting a possibly dispatched one.
        UniqueConstraint(
            "series_run_id",
            "episode_number",
            name="uq_series_episode_reservations_run_episode",
        ),
        CheckConstraint(
            "state in ('RESERVED','RELEASED','CONSUMED','ABANDONED_AFTER_ADMISSION')",
            name="ck_series_episode_reservations_state",
        ),
        CheckConstraint(
            "episode_number > 0",
            name="ck_series_episode_reservations_episode_number",
        ),
        CheckConstraint(
            "assignment_resolution_hash ~ '^[0-9a-f]{64}$'",
            name="ck_series_episode_reservations_resolution_hash",
        ),
        CheckConstraint(
            "(state = 'RESERVED' and released_at is null and consumed_at is null "
            "and consumed_admission_decision_id is null and abandoned_at is null) or "
            "(state = 'RELEASED' and released_at is not null and consumed_at is null "
            "and consumed_admission_decision_id is null and abandoned_at is null) or "
            "(state = 'CONSUMED' and consumed_at is not null "
            "and consumed_admission_decision_id is not null and abandoned_at is null) or "
            "(state = 'ABANDONED_AFTER_ADMISSION' and consumed_at is not null "
            "and consumed_admission_decision_id is not null and abandoned_at is not null)",
            name="ck_series_episode_reservations_lifecycle",
        ),
        Index("ix_series_episode_reservations_series_run", "series_run_id"),
        Index(
            "ix_series_episode_reservations_qualification",
            "script_qualification_run_id",
        ),
    )


class ScriptQualificationReceipt(Base):
    __tablename__ = "script_qualification_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    script_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id"), nullable=False
    )
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    script_assignment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    factual_evidence_pack_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        UniqueConstraint("script_qualification_run_id", name="uq_script_qualification_receipt_run"),
        CheckConstraint("result in ('PASS','BLOCK')", name="ck_script_qualification_receipt_result"),
        CheckConstraint("script_hash ~ '^[0-9a-f]{64}$' and script_assignment_hash ~ '^[0-9a-f]{64}$' and factual_evidence_pack_hash ~ '^[0-9a-f]{64}$' and content_hash ~ '^[0-9a-f]{64}$'", name="ck_script_qualification_receipt_hashes"),
    )


class ScriptQualificationBackgroundAttempt(Base):
    """Durable OpenAI Background lifecycle for one qualification phase."""

    __tablename__ = "script_qualification_background_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    script_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    lane: Mapped[str] = mapped_column(String(160), nullable=False)
    task: Mapped[str] = mapped_column(String(160), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    immutable_input_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    client_correlation_id: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    background_status: Mapped[str] = mapped_column(String(48), nullable=False, default="SUBMIT_PENDING")
    provider_outcome: Mapped[str | None] = mapped_column(String(80))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logical_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submission_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_network_error: Mapped[str | None] = mapped_column(Text)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    # These fields bind every future Responses call to the exact persisted
    # Structured Outputs contract. Historical attempts legitimately remain
    # null: their producer contract is recorded in an append-only snapshot.
    response_schema_identifier: Mapped[str | None] = mapped_column(String(160))
    response_schema_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()

    __table_args__ = (
        UniqueConstraint("script_qualification_run_id", "phase", name="uq_qualification_background_phase"),
        CheckConstraint("phase in ('WRITER','VERIFIER')", name="ck_qualification_background_phase"),
        CheckConstraint("background_status in ('SUBMIT_PENDING','SUBMITTED','QUEUED','IN_PROGRESS','COMPLETED','FAILED','CANCELLED','INCOMPLETE','DEADLINE_EXCEEDED','SUBMISSION_OUTCOME_UNKNOWN')", name="ck_qualification_background_status"),
        CheckConstraint("poll_count >= 0 and submission_attempt_count between 0 and 1", name="ck_qualification_background_counts"),
        CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_qualification_background_input_fingerprint"),
        Index("ix_qualification_background_due", "background_status", "next_poll_at"),
    )


class ScriptQualificationProviderResponseSnapshot(Base):
    """Immutable provider-response evidence for a completed Background poll."""

    __tablename__ = "script_qualification_provider_response_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    script_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id"), nullable=False
    )
    background_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_qualification_background_attempts.id"),
        nullable=False,
        unique=True,
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_response_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    raw_provider_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_provider_response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_output_content: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_schema_identifier: Mapped[str] = mapped_column(String(160), nullable=False)
    response_schema_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    producer_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_typed_output_hash: Mapped[str | None] = mapped_column(String(64))
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint("phase in ('WRITER','VERIFIER')", name="ck_qualification_response_snapshot_phase"),
        CheckConstraint(
            "raw_provider_response_hash ~ '^[0-9a-f]{64}$' and raw_output_hash ~ '^[0-9a-f]{64}$' and producer_input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_qualification_response_snapshot_hashes",
        ),
        CheckConstraint(
            "response_schema_hash is null or response_schema_hash ~ '^[0-9a-f]{64}$'",
            name="ck_qualification_response_snapshot_schema_hash",
        ),
        CheckConstraint(
            "accepted_typed_output_hash is null or accepted_typed_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_qualification_response_snapshot_accepted_hash",
        ),
        UniqueConstraint("provider_response_id", name="uq_qualification_response_snapshot_provider_response"),
        Index("ix_qualification_response_snapshot_run", "script_qualification_run_id"),
    )


class ScriptWriterOutputNormalizationReceipt(Base):
    """Append-only authority that maps one known legacy writer shape exactly."""

    __tablename__ = "script_writer_output_normalization_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id"), nullable=False, unique=True
    )
    source_background_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_background_attempts.id"), nullable=False, unique=True
    )
    source_provider_response_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_provider_response_snapshots.id"), nullable=False, unique=True
    )
    source_provider_response_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_provider_request_id: Mapped[str | None] = mapped_column(String(200))
    source_raw_output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_schema_classification: Mapped[str] = mapped_column(String(160), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(120), nullable=False)
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    removed_wrapper_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "source_raw_output_hash ~ '^[0-9a-f]{64}$' and normalized_payload_hash ~ '^[0-9a-f]{64}$' and receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_writer_normalization_receipt_hashes",
        ),
        Index("ix_writer_normalization_response", "source_provider_response_id"),
    )


class ScriptContentRepairAuthorizationReceipt(Base):
    """Immutable authority for one bounded, same-lineage content repair."""

    __tablename__ = "script_content_repair_authorization_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_qualification_runs.id"),
        nullable=False,
        unique=True,
    )
    source_script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_result_receipts_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_terminal_settlement_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    script_assignment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    factual_evidence_pack_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    memory_digest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_section_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    repair_policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    repair_type: Mapped[str] = mapped_column(String(120), nullable=False)
    compensation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    authorization_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        CheckConstraint(
            "source_script_hash ~ '^[0-9a-f]{64}$' and source_result_receipts_hash ~ '^[0-9a-f]{64}$' and source_terminal_settlement_hash ~ '^[0-9a-f]{64}$' and script_assignment_hash ~ '^[0-9a-f]{64}$' and factual_evidence_pack_hash ~ '^[0-9a-f]{64}$' and memory_digest_hash ~ '^[0-9a-f]{64}$' and runtime_contract_hash ~ '^[0-9a-f]{64}$' and authorization_hash ~ '^[0-9a-f]{64}$'",
            name="ck_script_content_repair_authorization_hashes",
        ),
        CheckConstraint(
            "repair_type = 'SCRIPT_CONTENT_REPAIR'",
            name="ck_script_content_repair_authorization_type",
        ),
    )


class ScriptQualificationProviderReclassificationReceipt(Base):
    __tablename__ = "script_qualification_provider_reclassification_receipts"

    id: Mapped[uuid.UUID] = uuid_pk()
    original_qualification_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_qualification_runs.id"), nullable=False, unique=True
    )
    original_route_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_route_attempts.id")
    )
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
