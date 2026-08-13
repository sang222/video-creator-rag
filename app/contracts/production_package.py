"""Canonical ProductionPackage v2 and automated readiness contracts.

The immutable database authority is an ``ArtifactVersion`` whose artifact type
is ``production_package``.  These contracts deliberately keep timestamps and
database-generated version identifiers outside the semantic package payload.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.vcos_v2 import (
    AssignmentMode,
    ContentMode,
    DurationContractV2,
    ProductionLane,
    StrategicLineageV2,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
PRODUCTION_PACKAGE_SCHEMA_V2 = "production.package.v2"
PRODUCTION_READINESS_SCHEMA_V2 = "production.readiness-receipt.v2"
DURATION_CONTRACT_VERSION_V2 = "channel-duration-contract.v2"


class ProductionPackageMateriality(StrEnum):
    NON_MATERIAL_TECHNICAL_REPAIR = "NON_MATERIAL_TECHNICAL_REPAIR"
    MATERIAL_EDITORIAL_CHANGE = "MATERIAL_EDITORIAL_CHANGE"
    MATERIAL_MARKET_OR_DESTINATION_CHANGE = "MATERIAL_MARKET_OR_DESTINATION_CHANGE"
    MATERIAL_PROVIDER_OR_COST_CHANGE = "MATERIAL_PROVIDER_OR_COST_CHANGE"
    MATERIAL_RIGHTS_OR_EVIDENCE_CHANGE = "MATERIAL_RIGHTS_OR_EVIDENCE_CHANGE"


class ExactContentRefV2(BaseModel):
    """Hash-bound reference to an exact immutable input."""

    type: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    id: uuid.UUID | None = None
    artifact_version_id: uuid.UUID | None = None
    version: int | str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionDurationContractV2(DurationContractV2):
    """Named Phase 3 view of the exact frozen Phase 2 duration authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionReadinessEvidenceV2(BaseModel):
    """Deterministic facts consumed by the generic GateRun engine."""

    research_evidence_complete: bool
    niche_market_gates_pass: bool
    assignment_integrity_pass: bool
    editorial_depth_sufficient: bool
    supported_claim_count: int = Field(ge=0)
    distinct_editorial_section_count: int = Field(ge=0)
    research_coverage_ratio: float = Field(ge=0, le=1)
    script_duration_ms: int = Field(gt=0)
    anti_padding_pass: bool
    padding_phrase_hits: int = Field(ge=0)
    repeated_sentence_ratio: float = Field(ge=0, le=1)
    script_gates_pass: bool
    visual_thumbnail_metadata_gates_pass: bool
    rights_disclosure_gates_pass: bool
    provider_plan_valid: bool
    budget_scope_valid: bool
    package_integrity_inputs_complete: bool = True
    unresolved_exception_types: list[
        Literal["RIGHTS", "EVIDENCE", "POLICY", "SECURITY"]
    ] = Field(default_factory=list)
    new_planning_cycle: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionRevisionV2(BaseModel):
    parent_package_artifact_version_id: uuid.UUID
    parent_package_hash: str = Field(pattern=SHA256_PATTERN)
    materiality: ProductionPackageMateriality
    affected_gate_keys: list[str] = Field(min_length=1)
    policy_authorized_repair: bool = False
    revision_reason_codes: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionPackageContentV2(BaseModel):
    """Timestamp-free semantic content of the canonical package artifact."""

    schema_version: Literal["production.package.v2"] = PRODUCTION_PACKAGE_SCHEMA_V2
    authority_classification: Literal["CANONICAL_V2_AUTHORITY"] = (
        "CANONICAL_V2_AUTHORITY"
    )
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    video_project_id: uuid.UUID
    project_admission_decision_id: uuid.UUID
    project_admission_decision_hash: str = Field(pattern=SHA256_PATTERN)
    channel_profile_version_id: uuid.UUID
    channel_profile_hash: str = Field(pattern=SHA256_PATTERN)
    compiled_policy_snapshot_id: uuid.UUID
    compiled_policy_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    # The first compatible v2 package version predates strategic lineage.  Keep
    # it optional for immutable historical reads; every current v2 project is
    # required by the package service to bind the exact project/admission copy.
    strategic_lineage: StrategicLineageV2 | None = None
    effective_context_ref: ExactContentRefV2
    production_lane: Literal[ProductionLane.LONG_FORM]
    assignment_mode: AssignmentMode
    content_mode: ContentMode
    series_plan_id: uuid.UUID | None = None
    series_run_id: uuid.UUID | None = None
    episode_number: int | None = Field(default=None, gt=0)
    episode_role: str | None = None
    standalone_reason_code: str | None = None
    duration_contract: ProductionDurationContractV2
    # Optional only so already-sealed Phase 3 package payloads remain readable.
    # The canonical Phase 4+ compiler always binds this exact authority.
    support_envelope_ref: ExactContentRefV2 | None = None
    production_visual_policy_version: str | None = None
    production_visual_policy_ref: str | None = None
    production_visual_policy_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    active_primary_visual_routes: list[str] = Field(default_factory=list)
    research_refs: list[ExactContentRefV2] = Field(min_length=1)
    source_refs: list[ExactContentRefV2] = Field(min_length=1)
    niche_market_gate_refs: list[ExactContentRefV2] = Field(min_length=1)
    script_ref: ExactContentRefV2
    visual_plan_ref: ExactContentRefV2
    thumbnail_refs: list[ExactContentRefV2] = Field(min_length=1)
    metadata_ref: ExactContentRefV2
    rights_disclosure_refs: list[ExactContentRefV2] = Field(min_length=1)
    provider_execution_plan_ref: ExactContentRefV2
    budget_scope_ref: ExactContentRefV2
    destination_binding_ref: ExactContentRefV2
    readiness_evidence: ProductionReadinessEvidenceV2
    revision: ProductionRevisionV2 | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_assignment(self) -> Self:
        visual_policy_values = (
            self.production_visual_policy_version,
            self.production_visual_policy_ref,
            self.production_visual_policy_hash,
        )
        if any(value is not None for value in visual_policy_values):
            if (
                self.production_visual_policy_version
                != "vcos.production-visual-policy.ai-only.v1"
                or self.production_visual_policy_ref
                != "config://production_visual_policy_catalog/2026-08-13/active-real-long-form-ai-only"
                or self.production_visual_policy_hash is None
                or self.active_primary_visual_routes != ["AI_IMAGE", "AI_VIDEO"]
            ):
                raise ValueError("PRODUCTION_PACKAGE_AI_VISUAL_POLICY_INVALID")
        elif self.active_primary_visual_routes:
            raise ValueError("PRODUCTION_PACKAGE_AI_VISUAL_POLICY_PARTIAL")
        if self.content_mode == ContentMode.SERIES_EPISODE:
            if (
                self.series_plan_id is None
                or self.series_run_id is None
                or self.episode_number is None
            ):
                raise ValueError(
                    "SERIES_EPISODE package requires exact plan, run, and episode"
                )
            if self.standalone_reason_code is not None:
                raise ValueError(
                    "SERIES_EPISODE package cannot have standalone_reason_code"
                )
        else:
            if any(
                value is not None
                for value in (
                    self.series_plan_id,
                    self.series_run_id,
                    self.episode_number,
                    self.episode_role,
                )
            ):
                raise ValueError("STANDALONE package cannot carry series bindings")
            if not self.standalone_reason_code:
                raise ValueError("STANDALONE package requires standalone_reason_code")
        artifact_refs = [
            *(
                [self.support_envelope_ref]
                if self.support_envelope_ref is not None
                else []
            ),
            *self.research_refs,
            *self.source_refs,
            *self.niche_market_gate_refs,
            self.script_ref,
            self.visual_plan_ref,
            *self.thumbnail_refs,
            self.metadata_ref,
            *self.rights_disclosure_refs,
            self.provider_execution_plan_ref,
            self.budget_scope_ref,
            self.destination_binding_ref,
        ]
        if any(ref.artifact_version_id is None for ref in artifact_refs):
            raise ValueError(
                "ProductionPackage v2 artifact refs require artifact_version_id"
            )
        return self


class ProductionPackageCreateV2(BaseModel):
    content: ProductionPackageContentV2
    created_by_user_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class ProductionPackageReadV2(BaseModel):
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    version_number: int = Field(gt=0)
    parent_version_id: uuid.UUID | None
    canonical_hash: str = Field(pattern=SHA256_PATTERN)
    readiness_state: Literal["BUILT", "READY_FOR_PRODUCTION", "BLOCKED"]
    content: ProductionPackageContentV2

    model_config = ConfigDict(extra="forbid", frozen=True)


class GateRunBindingV2(BaseModel):
    gate_run_id: uuid.UUID
    gate_definition_version_id: uuid.UUID
    gate_key: str = Field(min_length=1)
    result: Literal["PASS"]
    input_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    gate_run_hash: str = Field(pattern=SHA256_PATTERN)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionReadinessReceiptContentV2(BaseModel):
    schema_version: Literal["production.readiness-receipt.v2"] = (
        PRODUCTION_READINESS_SCHEMA_V2
    )
    readiness_state: Literal["READY_FOR_PRODUCTION"] = "READY_FOR_PRODUCTION"
    production_package_artifact_version_id: uuid.UUID
    production_package_version: int = Field(gt=0)
    production_package_hash: str = Field(pattern=SHA256_PATTERN)
    # Mirrors the package binding for direct, hash-stable readiness authority.
    support_envelope_ref: ExactContentRefV2 | None = None
    project_admission_decision_id: uuid.UUID
    project_admission_decision_hash: str = Field(pattern=SHA256_PATTERN)
    channel_profile_version_id: uuid.UUID
    channel_profile_hash: str = Field(pattern=SHA256_PATTERN)
    compiled_policy_snapshot_id: uuid.UUID
    compiled_policy_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    # Mirrors the package's immutable audience/intent/launch authority.
    strategic_lineage: StrategicLineageV2 | None = None
    duration_contract_hash: str = Field(pattern=SHA256_PATTERN)
    required_gate_runs: list[GateRunBindingV2] = Field(min_length=1)
    research_evidence_refs: list[ExactContentRefV2] = Field(min_length=1)
    rights_evidence_refs: list[ExactContentRefV2] = Field(min_length=1)
    provider_execution_plan_hash: str = Field(pattern=SHA256_PATTERN)
    budget_scope_hash: str = Field(pattern=SHA256_PATTERN)
    readiness_evaluator_version: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionReadinessReceiptReadV2(BaseModel):
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    version_number: int = Field(gt=0)
    receipt_hash: str = Field(pattern=SHA256_PATTERN)
    content: ProductionReadinessReceiptContentV2

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionReadinessEvaluationV2(BaseModel):
    status: Literal["READY_FOR_PRODUCTION", "BLOCKED"]
    package: ProductionPackageReadV2
    gate_run_ids: list[uuid.UUID]
    blocker_reason_codes: list[str] = Field(default_factory=list)
    receipt: ProductionReadinessReceiptReadV2 | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionPackageRevisionRequestV2(BaseModel):
    package_artifact_version_id: uuid.UUID
    materiality: ProductionPackageMateriality
    affected_gate_keys: list[str] = Field(min_length=1)
    revision_reason_codes: list[str] = Field(min_length=1)
    policy_authorized_repair: bool = False
    new_planning_cycle: bool = False
    content_updates: dict[str, Any] = Field(min_length=1)
    created_by_user_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")
