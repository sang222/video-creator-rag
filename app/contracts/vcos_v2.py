"""Typed Phase 2 contracts for production lanes and series assignment.

This module is intentionally independent from the v1 M5 contracts.  In
particular, no v1 hash-bound payload is widened or re-serialized here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


VCOS_V2_SCHEMA_VERSION = "v2"
ASSIGNMENT_RESOLVER_VERSION = "vcos-assignment-resolver-v2.1"


class ProductionLane(StrEnum):
    LONG_FORM = "LONG_FORM"


class ContentMode(StrEnum):
    SERIES_EPISODE = "SERIES_EPISODE"
    STANDALONE = "STANDALONE"


class AssignmentMode(StrEnum):
    SERIES_REQUIRED = "SERIES_REQUIRED"
    SERIES_PREFERRED = "SERIES_PREFERRED"
    STANDALONE_REQUIRED = "STANDALONE_REQUIRED"
    OPEN_MIX = "OPEN_MIX"


class SeriesPlanState(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class SeriesRunState(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETION_PENDING = "COMPLETION_PENDING"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    ARCHIVED = "ARCHIVED"


class AssignmentReasonCode(StrEnum):
    EXPLICIT_SERIES_REQUIRED = "EXPLICIT_SERIES_REQUIRED"
    EXPLICIT_STANDALONE_REQUIRED = "EXPLICIT_STANDALONE_REQUIRED"
    MANDATORY_NEXT_EPISODE = "MANDATORY_NEXT_EPISODE"
    TIMELY_NICHE_OPPORTUNITY = "TIMELY_NICHE_OPPORTUNITY"
    NO_ELIGIBLE_SERIES = "NO_ELIGIBLE_SERIES"
    SERIES_CAPACITY_EXHAUSTED = "SERIES_CAPACITY_EXHAUSTED"
    SERIES_COHERENCE_FAILED = "SERIES_COHERENCE_FAILED"
    BRIDGE_OR_SPECIAL = "BRIDGE_OR_SPECIAL"
    SERIES_BINDING_INVALID = "SERIES_BINDING_INVALID"
    SERIES_PLAN_SUPERSEDED = "SERIES_PLAN_SUPERSEDED"
    SERIES_RUN_NOT_ACTIVE = "SERIES_RUN_NOT_ACTIVE"
    SERIES_SCHEDULE_INELIGIBLE = "SERIES_SCHEDULE_INELIGIBLE"
    SERIES_PREFERRED_SELECTED = "SERIES_PREFERRED_SELECTED"
    OPEN_MIX_SERIES_SELECTED = "OPEN_MIX_SERIES_SELECTED"


class LegacySeriesClassification(StrEnum):
    UNRESOLVED_LEGACY = "UNRESOLVED_LEGACY"
    LEGACY_SERIES_BOUND = "LEGACY_SERIES_BOUND"
    V2_TYPED = "V2_TYPED"


class PlanningSourceType(StrEnum):
    LONG_FORM_PLAN = "LONG_FORM_PLAN"


class DurationContractV2(BaseModel):
    """Frozen duration envelope shared by planning and production phases."""

    schema_version: Literal["v2"] = "v2"
    minimum_duration_ms: int = Field(gt=0)
    target_duration_ms: int = Field(gt=0)
    maximum_duration_ms: int = Field(gt=0)
    duration_contract_version: str = Field(min_length=1, max_length=80)
    duration_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_profile_version_id: uuid.UUID
    source_policy_snapshot_id: uuid.UUID

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_order_and_hash(self) -> Self:
        if not (
            self.minimum_duration_ms
            <= self.target_duration_ms
            <= self.maximum_duration_ms
        ):
            raise ValueError(
                "duration must satisfy minimum_duration_ms <= target_duration_ms "
                "<= maximum_duration_ms"
            )
        if self.duration_contract_hash != self.calculate_hash(
            minimum_duration_ms=self.minimum_duration_ms,
            target_duration_ms=self.target_duration_ms,
            maximum_duration_ms=self.maximum_duration_ms,
            duration_contract_version=self.duration_contract_version,
            source_profile_version_id=self.source_profile_version_id,
            source_policy_snapshot_id=self.source_policy_snapshot_id,
        ):
            raise ValueError("duration_contract_hash does not match frozen authority")
        return self

    @staticmethod
    def calculate_hash(
        *,
        minimum_duration_ms: int,
        target_duration_ms: int,
        maximum_duration_ms: int,
        duration_contract_version: str,
        source_profile_version_id: uuid.UUID,
        source_policy_snapshot_id: uuid.UUID,
    ) -> str:
        payload = {
            "duration_contract_version": duration_contract_version,
            "maximum_duration_ms": maximum_duration_ms,
            "minimum_duration_ms": minimum_duration_ms,
            "source_policy_snapshot_id": str(source_policy_snapshot_id),
            "source_profile_version_id": str(source_profile_version_id),
            "target_duration_ms": target_duration_ms,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def minimum_seconds(self) -> float:
        return self.minimum_duration_ms / 1000

    @property
    def target_seconds(self) -> float:
        return self.target_duration_ms / 1000

    @property
    def maximum_seconds(self) -> float:
        return self.maximum_duration_ms / 1000


class SeriesPlanCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    stable_series_key: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    allowed_production_lanes: list[ProductionLane] = Field(min_length=1)
    episode_role_policy: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: uuid.UUID
    version: int = Field(default=1, gt=0)
    supersedes_series_plan_id: uuid.UUID | None = None
    approval_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_lanes(self) -> Self:
        if len(set(self.allowed_production_lanes)) != len(
            self.allowed_production_lanes
        ):
            raise ValueError("allowed_production_lanes must not contain duplicates")
        if self.allowed_production_lanes != [ProductionLane.LONG_FORM]:
            raise ValueError("SeriesPlan supports LONG_FORM only")
        return self


class SeriesPlanRead(SeriesPlanCreate):
    id: uuid.UUID
    state: SeriesPlanState
    approved_by_user_id: uuid.UUID | None
    approved_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class SeriesPlanTransitionRequest(BaseModel):
    target_state: SeriesPlanState
    actor_user_id: uuid.UUID
    reason_codes: list[str] = Field(min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SeriesRunCreate(BaseModel):
    series_plan_id: uuid.UUID
    run_key: str = Field(min_length=1, max_length=160)
    run_number: int = Field(gt=0)
    capacity: int = Field(gt=0)
    first_episode_number: int = Field(default=1, gt=0)
    priority: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    schedule_window_start: AwareDatetime | None = None
    schedule_window_end: AwareDatetime | None = None
    created_by_user_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (
            self.schedule_window_start is not None
            and self.schedule_window_end is not None
            and self.schedule_window_end <= self.schedule_window_start
        ):
            raise ValueError("schedule_window_end must be after schedule_window_start")
        return self


class SeriesRunRead(SeriesRunCreate):
    id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    next_episode_number: int
    reserved_episode_count: int
    published_episode_count: int
    state: SeriesRunState
    state_reason_codes: list[str]
    approved_by_user_id: uuid.UUID | None
    approved_at: AwareDatetime | None
    activated_at: AwareDatetime | None
    completion_pending_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class SeriesRunTransitionRequest(BaseModel):
    target_state: SeriesRunState
    actor_user_id: uuid.UUID
    reason_codes: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class AssignmentCandidate(BaseModel):
    """Immutable resolver view; it contains no SQLAlchemy state."""

    series_plan_id: uuid.UUID
    series_run_id: uuid.UUID
    production_lane: ProductionLane
    plan_state: SeriesPlanState
    run_state: SeriesRunState
    next_episode_number: int = Field(gt=0)
    capacity: int = Field(gt=0)
    reserved_episode_count: int = Field(ge=0)
    priority: int = 0
    coherence_score: int = Field(default=0, ge=0, le=100)
    schedule_eligible: bool = True
    mandatory_next_episode: bool = False
    explicit_slot_priority: int = Field(default=0, ge=-100, le=100)
    schedule_obligation: int = Field(default=0, ge=0, le=100)
    recent_repetition_penalty: int = Field(default=0, ge=0, le=100)
    niche_opportunity_value: int = Field(default=0, ge=0, le=100)
    episode_role: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def has_capacity(self) -> bool:
        return self.reserved_episode_count < self.capacity


class AssignmentResolverInput(BaseModel):
    schema_version: Literal["v2"] = "v2"
    production_lane: ProductionLane
    assignment_mode: AssignmentMode
    preferred_series_plan_id: uuid.UUID | None = None
    preferred_series_run_id: uuid.UUID | None = None
    candidates: list[AssignmentCandidate] = Field(default_factory=list)
    niche_gate_passed: bool = False
    market_gate_passed: bool = False
    timely_niche_opportunity: bool = False
    bridge_or_special: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if (
            self.preferred_series_run_id is not None
            and self.preferred_series_plan_id is None
        ):
            raise ValueError(
                "preferred_series_run_id requires preferred_series_plan_id"
            )
        return self


class AssignmentResolution(BaseModel):
    schema_version: Literal["v2"] = "v2"
    resolver_version: str = ASSIGNMENT_RESOLVER_VERSION
    resolver_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_lane: ProductionLane
    assignment_mode: AssignmentMode
    content_mode: ContentMode
    series_plan_id: uuid.UUID | None = None
    series_run_id: uuid.UUID | None = None
    episode_number: int | None = Field(default=None, gt=0)
    episode_role: str | None = None
    standalone_reason_code: AssignmentReasonCode | None = None
    reason_codes: list[AssignmentReasonCode] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_assignment(self) -> Self:
        typed = (
            self.series_plan_id,
            self.series_run_id,
            self.episode_number,
        )
        if self.content_mode == ContentMode.SERIES_EPISODE:
            if any(value is None for value in typed):
                raise ValueError(
                    "SERIES_EPISODE requires series_plan_id, series_run_id, and episode_number"
                )
            if self.standalone_reason_code is not None:
                raise ValueError("SERIES_EPISODE cannot have standalone_reason_code")
        else:
            if (
                any(value is not None for value in typed)
                or self.episode_role is not None
            ):
                raise ValueError("STANDALONE cannot carry series or episode fields")
            if self.standalone_reason_code is None:
                raise ValueError("STANDALONE requires standalone_reason_code")
        return self

    @property
    def standalone_reason(self) -> AssignmentReasonCode | None:
        """Read-only compatibility view; v2 serialization uses the code name."""
        return self.standalone_reason_code


class ProjectAdmissionV2Request(BaseModel):
    schema_version: Literal["v2"] = "v2"
    planning_source_type: PlanningSourceType
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    editorial_calendar_slot_id: uuid.UUID | None = None
    editorial_idea_candidate_id: uuid.UUID | None = None
    idea_market_preflight_id: uuid.UUID | None = None
    production_lane: ProductionLane
    assignment_mode: AssignmentMode
    preferred_series_plan_id: uuid.UUID | None = None
    preferred_series_run_id: uuid.UUID | None = None
    raw_series_key: str | None = None
    title: str = Field(min_length=1)
    description: str | None = None
    category_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    episode_role: str | None = None
    standalone_reason_code: str | None = None
    duration_contract: DurationContractV2
    niche_gate_passed: bool = False
    market_gate_passed: bool = False
    timely_niche_opportunity: bool = False
    bridge_or_special: bool = False
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    budget_gate_result: dict[str, Any] | None = None
    created_by_user_id: uuid.UUID
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_source_and_lane(self) -> Self:
        if self.raw_series_key is not None:
            raise ValueError(
                "v2 admission forbids raw_series_key; use typed SeriesPlan/SeriesRun ids"
            )
        if self.planning_source_type != PlanningSourceType.LONG_FORM_PLAN:
            raise ValueError("long-form planning source is required")
        if self.production_lane != ProductionLane.LONG_FORM:
            raise ValueError("production lane must be LONG_FORM")
        if self.editorial_calendar_slot_id is None:
            raise ValueError("long-form planning requires editorial_calendar_slot_id")
        if (
            self.duration_contract.source_profile_version_id
            != self.channel_profile_version_id
            or self.duration_contract.source_policy_snapshot_id
            != self.policy_snapshot_id
        ):
            raise ValueError("duration contract must bind the admission profile/policy")
        return self


class ProjectAdmissionV2Read(BaseModel):
    id: uuid.UUID
    schema_version: Literal["v2"]
    decision: Literal["ADMIT", "BLOCK"]
    admitted_video_project_id: uuid.UUID | None
    planning_source_type: PlanningSourceType
    production_lane: ProductionLane
    content_mode: ContentMode | None
    assignment_mode: AssignmentMode
    series_plan_id: uuid.UUID | None
    series_run_id: uuid.UUID | None
    episode_number: int | None
    episode_role: str | None
    standalone_reason_code: str | None
    editorial_idea_candidate_id: uuid.UUID | None
    resolver_version: str
    resolver_input_hash: str
    decision_hash: str
    duration_contract: DurationContractV2
    reason_codes: list[str]
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LongFormPlanningRequest(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    editorial_calendar_slot_id: uuid.UUID
    editorial_idea_candidate_id: uuid.UUID | None = None
    idea_market_preflight_id: uuid.UUID
    assignment_mode: AssignmentMode
    title: str = Field(min_length=1)
    description: str | None = None
    category_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    preferred_series_plan_id: uuid.UUID | None = None
    preferred_series_run_id: uuid.UUID | None = None
    niche_gate_passed: bool = False
    market_gate_passed: bool = False
    timely_niche_opportunity: bool = False
    bridge_or_special: bool = False
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    budget_gate_result: dict[str, Any] | None = None
    duration_contract: DurationContractV2
    created_by_user_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class EditorialSlotV2Input(BaseModel):
    schema_version: Literal["v2"] = "v2"
    production_lane: ProductionLane
    assignment_mode: AssignmentMode
    preferred_series_plan_id: uuid.UUID | None = None
    preferred_series_run_id: uuid.UUID | None = None
    legacy_series_key: str | None = None
    slot_date: date | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def reject_v2_raw_series(self) -> Self:
        if self.legacy_series_key is not None:
            raise ValueError("v2 editorial slots cannot use legacy raw series_key")
        if (
            self.preferred_series_run_id is not None
            and self.preferred_series_plan_id is None
        ):
            raise ValueError(
                "preferred_series_run_id requires preferred_series_plan_id"
            )
        return self
