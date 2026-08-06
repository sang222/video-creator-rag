"""Channel-scoped launch and long-form cadence authorities."""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class LaunchPolicyState(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class LaunchRunState(StrEnum):
    PREPARING = "PREPARING"
    READY_TO_LAUNCH = "READY_TO_LAUNCH"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


class CadenceDecision(StrEnum):
    START_LONG_FORM_PRODUCTION = "START_LONG_FORM_PRODUCTION"
    START_SCRIPT_QUALIFICATION = "START_SCRIPT_QUALIFICATION"
    WAIT_BUFFER_FULL = "WAIT_BUFFER_FULL"
    WAIT_NO_ELIGIBLE_CANDIDATE = "WAIT_NO_ELIGIBLE_CANDIDATE"
    WAIT_ACTIVE_PRODUCTION = "WAIT_ACTIVE_PRODUCTION"
    WAIT_OUTSIDE_PRODUCTION_HORIZON = "WAIT_OUTSIDE_PRODUCTION_HORIZON"
    WAIT_BUDGET_BLOCKED = "WAIT_BUDGET_BLOCKED"
    WAIT_PROVIDER_AUTHORITY = "WAIT_PROVIDER_AUTHORITY"
    WAIT_POLICY_OR_RIGHTS_BLOCKED = "WAIT_POLICY_OR_RIGHTS_BLOCKED"
    WAIT_QUALITY_BLOCKED = "WAIT_QUALITY_BLOCKED"
    WAIT_LAUNCH_NOT_ACTIVE = "WAIT_LAUNCH_NOT_ACTIVE"


class FirstChannelLaunchPolicyCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    # Zero approved plans is valid when the launch deliberately uses the
    # deterministic STANDALONE fallback.  The database constraint is updated
    # separately with the next schema migration.
    approved_initial_series_plan_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=2
    )
    policy_version: int = Field(default=1, gt=0)
    supersedes_policy_version_id: uuid.UUID | None = None

    launch_mode: Literal["CONTROLLED_EVIDENCE_BUILDING"] = (
        "CONTROLLED_EVIDENCE_BUILDING"
    )
    duration_source: Literal["CHANNEL_DURATION_CONTRACT"] = "CHANNEL_DURATION_CONTRACT"

    preparation_days_min: int = Field(default=14, ge=1)
    preparation_days_max: int = Field(default=21, ge=1)
    idea_candidates_target: int = Field(default=12, ge=1)
    preflight_pass_target: int = Field(default=8, ge=1)
    greenlight_target: int = Field(default=6, ge=1)
    public_ready_buffer_target: int = Field(default=3, ge=1)
    max_days_produced_ahead: int = Field(default=14, ge=1)
    max_concurrent_productions: int = Field(default=1, ge=1)

    max_active_runs: int = Field(default=2, ge=1, le=2)
    initial_series_count: int = Field(default=0, ge=0, le=2)

    first_n_public_videos: int = Field(default=10, ge=1)
    max_primary_variables_changed_per_video: int = Field(default=1, ge=1, le=1)
    auto_niche_pivot: Literal[False] = False
    auto_series_kill: Literal[False] = False
    auto_playbook_promotion: Literal[False] = False

    channel_promise_and_initial_series: Literal["CHANNEL_INIT_ONLY"] = (
        "CHANNEL_INIT_ONLY"
    )
    pre_render_script_review: Literal[False] = False
    pre_render_package_review: Literal[False] = False
    final_video_decision: Literal["UPLOAD_OR_DO_NOT_UPLOAD"] = "UPLOAD_OR_DO_NOT_UPLOAD"
    public_publish: Literal["MANUAL_ONLY"] = "MANUAL_ONLY"

    commercial_model: Literal["PLATFORM_AD_REVENUE_ONLY"] = "PLATFORM_AD_REVENUE_ONLY"
    affiliate_cta: Literal[False] = False
    sponsor_content: Literal[False] = False
    primary_cta: Literal["NEXT_VIDEO_OR_SUBSCRIBE"] = "NEXT_VIDEO_OR_SUBSCRIBE"

    target_long_form_per_week: int = Field(default=2, ge=1, le=2)
    quality_fallback_long_form_per_week: int = Field(default=1, ge=1, le=2)
    minimum_publish_interval_hours: int = Field(default=72, ge=1)
    publish_weekdays: list[
        Literal[
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY",
        ]
    ] = Field(default_factory=lambda: ["TUESDAY", "SATURDAY"], min_length=1)
    publish_local_time: str = Field(
        default="10:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    render_lead_time_min_hours: int = Field(default=24, ge=1)
    render_lead_time_max_hours: int = Field(default=48, ge=1)
    same_day_multi_publish: Literal[False] = False
    timezone: str = Field(default="America/New_York", min_length=1, max_length=80)

    evidence_refs: list[dict[str, Any]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def default_initial_series_count(cls, value: Any) -> Any:
        """Derive the omitted count without hiding an explicit mismatch."""

        if not isinstance(value, dict) or "initial_series_count" in value:
            return value
        normalized = dict(value)
        normalized["initial_series_count"] = len(
            normalized.get("approved_initial_series_plan_ids") or []
        )
        return normalized

    @model_validator(mode="after")
    def validate_operating_envelope(self) -> "FirstChannelLaunchPolicyCreate":
        if self.preparation_days_min > self.preparation_days_max:
            raise ValueError("preparation_days_min cannot exceed preparation_days_max")
        if not (
            self.idea_candidates_target
            >= self.preflight_pass_target
            >= self.greenlight_target
            >= self.public_ready_buffer_target
        ):
            raise ValueError("runway targets must be monotonically non-increasing")
        if self.initial_series_count != len(self.approved_initial_series_plan_ids):
            raise ValueError(
                "initial_series_count must match approved_initial_series_plan_ids"
            )
        if len(set(self.approved_initial_series_plan_ids)) != len(
            self.approved_initial_series_plan_ids
        ):
            raise ValueError("initial series plan ids must be unique")
        if self.quality_fallback_long_form_per_week > self.target_long_form_per_week:
            raise ValueError("quality fallback cannot exceed target cadence")
        if self.render_lead_time_min_hours > self.render_lead_time_max_hours:
            raise ValueError("render lead-time minimum cannot exceed maximum")
        if len(set(self.publish_weekdays)) != len(self.publish_weekdays):
            raise ValueError("publish weekdays must be unique")
        if len(self.publish_weekdays) > self.target_long_form_per_week:
            raise ValueError("publish weekdays cannot exceed target long-form cadence")
        return self


class FirstChannelLaunchPolicyRead(FirstChannelLaunchPolicyCreate):
    id: uuid.UUID
    state: LaunchPolicyState
    canonical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    approved_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LaunchPolicyApproval(BaseModel):
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class LaunchRunCreate(BaseModel):
    launch_policy_version_id: uuid.UUID
    launch_key: str = Field(min_length=1, max_length=160)
    preparation_started_on: date

    model_config = ConfigDict(extra="forbid")


class LaunchRunTransition(BaseModel):
    target_state: LaunchRunState
    reason_codes: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class LaunchRunRead(BaseModel):
    id: uuid.UUID
    launch_policy_version_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    launch_key: str
    state: LaunchRunState
    preparation_started_on: date
    launch_started_at: AwareDatetime | None
    paused_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    reason_codes: list[str]
    created_by_user_id: uuid.UUID
    updated_by_user_id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RunwayCounts(BaseModel):
    idea_candidates: int = 0
    preflight_passed_candidates: int = 0
    greenlit_candidates: int = 0
    in_production_videos: int = 0
    final_review_ready_videos: int = 0
    upload_approved_videos: int = 0
    published_videos: int = 0
    rejected_or_expired_candidates: int = 0


class LaunchRunwayProjection(BaseModel):
    launch_run_id: uuid.UUID
    launch_policy_version_id: uuid.UUID
    as_of: AwareDatetime
    counts: RunwayCounts
    public_ready_buffer: int
    active_series: int


class LongFormPublishSlotRead(BaseModel):
    id: uuid.UUID
    launch_run_id: uuid.UUID
    launch_policy_version_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    local_publish_date: date
    intended_publish_at: AwareDatetime
    target_start_window_open_at: AwareDatetime
    target_start_window_close_at: AwareDatetime
    state: Literal[
        "OPEN",
        "QUALIFICATION_RESERVED",
        "QUALIFICATION_RECONCILIATION_REQUIRED",
        "RESERVED",
        "FULFILLED",
        "SKIPPED",
        "CANCELED",
    ]
    reserved_candidate_id: uuid.UUID | None
    admitted_video_project_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CadenceEvaluationRequest(BaseModel):
    """External trigger; the server owns the evaluation window identity."""

    model_config = ConfigDict(extra="forbid")


class CadenceEvaluationCommand(BaseModel):
    """Trusted outbox payload consumed only by the durable worker."""

    evaluation_key: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")


class CadenceEvaluationOutboxRead(BaseModel):
    event_id: uuid.UUID
    launch_run_id: uuid.UUID
    command_id: str
    evaluation_key: str
    status: Literal["QUEUED", "DELIVERED", "DEAD_LETTERED"]


class CadenceEvaluationRead(BaseModel):
    id: uuid.UUID
    launch_run_id: uuid.UUID
    launch_policy_version_id: uuid.UUID
    publish_slot_id: uuid.UUID | None
    selected_candidate_id: uuid.UUID | None
    admitted_video_project_id: uuid.UUID | None
    production_workflow_run_id: uuid.UUID | None
    script_qualification_run_id: uuid.UUID | None
    evaluated_at: AwareDatetime
    evaluation_window_key: str
    timezone: str
    public_ready_buffer_count: int
    active_production_count: int
    eligible_greenlit_candidate_ids: list[uuid.UUID]
    budget_provider_readiness: dict[str, Any]
    blocking_incident_ids: list[uuid.UUID]
    decision: CadenceDecision
    reason_codes: list[str]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LaunchDashboardRead(BaseModel):
    launch_run: LaunchRunRead
    launch_day: int
    runway: LaunchRunwayProjection
    active_series: list[dict[str, Any]]
    videos_published: int
    next_publish_slot: LongFormPublishSlotRead | None
    next_production_start_window: dict[str, AwareDatetime] | None
    latest_cadence_evaluation: CadenceEvaluationRead | None
    current_experiment_phase: str
    watch_hours: None = None
    ypp_progress: None = None
    blockers: list[str]
    next_action: str
    qualification_summary: dict[str, Any] = Field(default_factory=dict)
