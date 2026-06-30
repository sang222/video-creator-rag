import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


SlotType = Literal["DAILY", "WEEKLY", "CAMPAIGN", "EVERGREEN", "EXPERIMENT", "MANUAL"]
SlotStatus = Literal["OPEN", "ASSIGNED", "ADMITTED", "SKIPPED", "CANCELLED"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
DailyRunStatus = Literal["PENDING", "RUNNING", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"]
DailyRunMode = Literal["REAL", "REAL_DISABLED", "NOT_CONFIGURED", "HUMAN_REVIEW_ONLY", "BLOCKED"]
DailyRunTriggerType = Literal["MANUAL", "SCHEDULED", "TEST"]
ContextPackPurpose = Literal["DAILY_IDEA", "PROJECT_ADMISSION", "AUTHORITY_REVIEW", "SEARCH_DEMAND", "TEST"]
FreshnessState = Literal["FRESH", "STALE", "UNKNOWN", "NOT_REQUIRED"]
ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
SearchDemandSourceType = Literal[
    "OFFICIAL_MANUAL",
    "PAID_TOOL_CSV",
    "GOOGLE_TRENDS_CSV",
    "YOUTUBE_ANALYTICS",
    "TIKTOK_CREATOR_SEARCH_INSIGHTS_MANUAL",
    "INTERNAL_ANALYTICS",
    "MANUAL_RESEARCH",
]
SearchPlatform = Literal["YOUTUBE", "TIKTOK", "FACEBOOK", "INSTAGRAM", "GOOGLE", "GENERIC"]
PolicyFitState = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "UNKNOWN"]
IdeaDecisionStatus = Literal["PROPOSED", "ADMITTED", "REVIEW_REQUIRED", "BLOCKED", "REJECTED", "SKIPPED"]
IdeaMarketDecision = Literal["PASS", "REVIEW_REQUIRED", "BLOCK", "SKIPPED"]
AdmissionDecision = Literal["ADMIT", "REVIEW_REQUIRED", "BLOCK", "SKIP"]
TimecodeValue = int | float | str


class EditorialCalendarSlotCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    category_id: uuid.UUID | None = None
    slot_date: date
    slot_type: SlotType = "DAILY"
    status: SlotStatus = "OPEN"
    production_goal: str | None = None
    target_platforms: list[str] = Field(default_factory=list)
    content_pillar: str | None = None
    series_key: str | None = None
    format_hint: str | None = None
    character_binding_policy_json: dict[str, Any] | None = None
    risk_level: RiskLevel = "UNKNOWN"
    operational_envelope: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class EditorialCalendarSlotRead(EditorialCalendarSlotCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ChannelDailyRunCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    editorial_calendar_slot_id: uuid.UUID | None = None
    run_date: date
    status: DailyRunStatus = "PENDING"
    run_mode: DailyRunMode = "REAL_DISABLED"
    trigger_type: DailyRunTriggerType = "MANUAL"
    reason_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ChannelDailyRunRead(ChannelDailyRunCreate):
    id: uuid.UUID
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    context_pack_snapshot_id: uuid.UUID | None
    channel_state_pack_snapshot_id: uuid.UUID | None
    daily_idea_decision_id: uuid.UUID | None
    project_admission_decision_id: uuid.UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RetrievalPlanSnapshotCreate(BaseModel):
    purpose: ContextPackPurpose
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None = None
    channel_profile_version_id: uuid.UUID | None = None
    policy_snapshot_id: uuid.UUID | None = None
    video_project_id: uuid.UUID | None = None
    editorial_calendar_slot_id: uuid.UUID | None = None
    allowed_sources: list[str] = Field(default_factory=list)
    excluded_sources: list[str] = Field(default_factory=list)
    redaction_rules: dict[str, Any] = Field(default_factory=dict)
    token_budget: int | None = None
    source_order: list[str] = Field(default_factory=list)
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class RetrievalPlanSnapshotRead(RetrievalPlanSnapshotCreate):
    id: uuid.UUID
    plan_hash: str
    created_at: AwareDatetime


class ContextPackSnapshotCreate(BaseModel):
    retrieval_plan_snapshot_id: uuid.UUID
    input_refs: list[dict[str, Any]] = Field(default_factory=list)
    policy_refs: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    metric_refs: list[dict[str, Any]] = Field(default_factory=list)
    memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    pack_content: dict[str, Any] = Field(default_factory=dict)
    freshness_state: FreshnessState = "UNKNOWN"
    confidence_level: ConfidenceLevel = "UNKNOWN"
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ContextPackSnapshotRead(BaseModel):
    id: uuid.UUID
    retrieval_plan_snapshot_id: uuid.UUID
    purpose: ContextPackPurpose
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID | None
    channel_profile_version_id: uuid.UUID | None
    policy_snapshot_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    editorial_calendar_slot_id: uuid.UUID | None
    input_refs: list[dict[str, Any]]
    policy_refs: list[dict[str, Any]]
    evidence_refs: list[dict[str, Any]]
    metric_refs: list[dict[str, Any]]
    memory_refs: list[dict[str, Any]]
    pack_content: dict[str, Any]
    freshness_state: FreshnessState
    confidence_level: ConfidenceLevel
    pack_hash: str
    created_by_user_id: uuid.UUID | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ChannelStatePackSnapshotCreate(BaseModel):
    channel_daily_run_id: uuid.UUID | None = None
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    context_pack_snapshot_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ChannelStatePackSnapshotRead(ChannelStatePackSnapshotCreate):
    id: uuid.UUID
    state_blob: dict[str, Any]
    active_project_refs: list[dict[str, Any]]
    pending_review_refs: list[dict[str, Any]]
    readiness_summary: dict[str, Any]
    provider_health_summary: dict[str, Any]
    quota_summary: dict[str, Any]
    evidence_summary: dict[str, Any]
    freshness_state: FreshnessState
    confidence_level: ConfidenceLevel
    state_hash: str
    created_at: AwareDatetime


class SearchDemandEvidenceCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    evidence_source_type: SearchDemandSourceType
    source_ref: str | None = None
    query: str = Field(min_length=1)
    platform: SearchPlatform = "GENERIC"
    geo: str | None = None
    language: str | None = None
    lookback_window_days: int | None = None
    search_volume_30d: int | None = None
    relative_interest_index: Decimal | None = None
    competition_index: Decimal | None = None
    trending_velocity: Decimal | None = None
    evidence_confidence: ConfidenceLevel = "UNKNOWN"
    captured_at: AwareDatetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class SearchDemandEvidenceRead(SearchDemandEvidenceCreate):
    id: uuid.UUID
    captured_at: AwareDatetime
    created_at: AwareDatetime


class SearchIntentMapCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_daily_run_id: uuid.UUID | None = None
    daily_idea_decision_id: uuid.UUID | None = None
    primary_search_intent: str = Field(min_length=1)
    secondary_search_intents: list[str] = Field(default_factory=list)
    keyword_cluster: list[str] = Field(default_factory=list)
    audience_problem: str | None = None
    audience_language: str | None = None
    target_geo: str | None = None
    source_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    demand_confidence: ConfidenceLevel = "UNKNOWN"
    competition_notes: str | None = None
    content_gap_notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class SearchIntentMapRead(SearchIntentMapCreate):
    id: uuid.UUID
    created_at: AwareDatetime


class AudienceTargetPackCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_daily_run_id: uuid.UUID | None = None
    daily_idea_decision_id: uuid.UUID | None = None
    target_audience: str = Field(min_length=1)
    audience_problem: str = Field(min_length=1)
    audience_language: str | None = None
    target_geo: str | None = None
    platform_surface_hypothesis: list[str] = Field(default_factory=list)
    audience_rationale: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = "UNKNOWN"

    model_config = ConfigDict(extra="forbid")


class AudienceTargetPackRead(AudienceTargetPackCreate):
    id: uuid.UUID
    created_at: AwareDatetime


class IdeaMarketPreflightCreate(BaseModel):
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    channel_daily_run_id: uuid.UUID | None = None
    daily_idea_decision_id: uuid.UUID | None = None
    search_intent_map_id: uuid.UUID | None = None
    audience_target_pack_id: uuid.UUID | None = None
    demand_score: Decimal | None = None
    channel_fit_score: Decimal | None = None
    policy_fit_state: PolicyFitState = "UNKNOWN"
    evidence_blob: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class IdeaMarketPreflightRead(IdeaMarketPreflightCreate):
    id: uuid.UUID
    confidence_state: ConfidenceLevel
    reason_codes: list[str]
    decision: IdeaMarketDecision
    created_at: AwareDatetime


class DailyIdeaDecisionCreate(BaseModel):
    channel_daily_run_id: uuid.UUID
    context_pack_snapshot_id: uuid.UUID
    channel_state_pack_snapshot_id: uuid.UUID | None = None
    provider_key: str = "llm_router"
    quota_account_id: uuid.UUID | None = None
    budget_policy_key: str | None = None
    estimated_cost: Decimal = Decimal("0")

    model_config = ConfigDict(extra="forbid")


class DailyIdeaDecisionRead(BaseModel):
    id: uuid.UUID
    channel_daily_run_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    context_pack_snapshot_id: uuid.UUID
    channel_state_pack_snapshot_id: uuid.UUID | None
    llm_run_snapshot_id: uuid.UUID | None
    decision_status: IdeaDecisionStatus
    proposed_title: str
    proposed_angle: str | None
    proposed_format: str | None
    proposed_pillar: str | None
    proposed_series_key: str | None
    rationale: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    reason_codes: list[str]
    confidence_level: ConfidenceLevel
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class DailyRunExecuteRequest(BaseModel):
    provider_key: str = "llm_router"
    quota_account_id: uuid.UUID | None = None
    budget_policy_key: str | None = None
    estimated_cost: Decimal = Decimal("0")
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ProjectAdmissionDecisionCreate(BaseModel):
    channel_daily_run_id: uuid.UUID
    daily_idea_decision_id: uuid.UUID
    idea_market_preflight_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    character_binding_id: uuid.UUID | None = None
    budget_policy_key: str | None = None
    quota_account_id: uuid.UUID | None = None
    estimated_cost: Decimal = Decimal("0")
    created_by_user_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class ProjectAdmissionDecisionRead(ProjectAdmissionDecisionCreate):
    id: uuid.UUID
    budget_gate_result: dict[str, Any]
    readiness_gate_refs: list[dict[str, Any]]
    decision: AdmissionDecision
    reason_codes: list[str]
    evidence_refs: list[dict[str, Any]]
    admitted_video_project_id: uuid.UUID | None
    created_artifact_refs: list[dict[str, Any]]
    created_at: AwareDatetime


class MockAuthorityProposal(BaseModel):
    proposed_title: str = Field(min_length=1)
    proposed_angle: str | None = None
    proposed_format: str | None = None
    proposed_pillar: str | None = None
    proposed_series_key: str | None = None
    audience_problem: str = Field(min_length=1)
    search_intent_hypothesis: dict[str, Any]
    rationale: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    confidence: ConfidenceLevel
    idea_source_refs: list[dict[str, Any]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", strict=True)


class CreativeBriefDraft(BaseModel):
    title: str = Field(min_length=1)
    angle: str | None = None
    format: str | None = None
    pillar: str | None = None
    series_key: str | None = None
    rationale: dict[str, Any]
    status: Literal["draft"] = "draft"

    model_config = ConfigDict(extra="forbid", strict=True)


class ResearchPackDraft(BaseModel):
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    context_pack_snapshot_id: str = Field(min_length=1)
    numeric_truth: Literal["SQL_OR_UNKNOWN"]
    status: Literal["draft"] = "draft"

    model_config = ConfigDict(extra="forbid", strict=True)


class SourcePackDraft(BaseModel):
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    context_pack_snapshot_id: str = Field(min_length=1)
    status: Literal["draft"] = "draft"

    model_config = ConfigDict(extra="forbid", strict=True)


class SceneSpec(BaseModel):
    scene_id: str = Field(min_length=1)
    start_time: TimecodeValue
    end_time: TimecodeValue
    narration_segment_id: str = Field(min_length=1)
    caption_or_narration_ref: str = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    preferred_source: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("narration_segment_id", mode="before")
    @classmethod
    def _narration_segment_required(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("RENDER_SPEC_MISSING_NARRATION_SEGMENT")
        return value

    @model_validator(mode="after")
    def _validate_timing(self):
        if _time_to_seconds(self.end_time) <= _time_to_seconds(self.start_time):
            raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
        return self


class RenderSpecDraft(BaseModel):
    voice_as_master: bool
    narration_timeline_ref: str = Field(min_length=1)
    scenes: list[SceneSpec] = Field(min_length=1)
    allow_scene_overlap: bool = False
    allow_scene_gaps: bool = False
    status: Literal["contract_only_for_m6"] = "contract_only_for_m6"

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _validate_voice_timeline_contract(self):
        if self.voice_as_master is not True:
            raise ValueError("VOICE_AS_MASTER_CONTRACT_REQUIRED")
        ordered = sorted(self.scenes, key=lambda scene: _time_to_seconds(scene.start_time))
        for previous, current in zip(ordered, ordered[1:]):
            previous_end = _time_to_seconds(previous.end_time)
            current_start = _time_to_seconds(current.start_time)
            if current_start < previous_end and not self.allow_scene_overlap:
                raise ValueError("RENDER_SPEC_SCENE_OVERLAP")
            if current_start > previous_end and not self.allow_scene_gaps:
                raise ValueError("VOICE_AS_MASTER_CONTRACT_REQUIRED")
        return self


def _time_to_seconds(value: TimecodeValue) -> float:
    if isinstance(value, bool):
        raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
    if isinstance(value, int | float):
        if value < 0:
            raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
        return float(value)
    if not isinstance(value, str):
        raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
    text = value.strip()
    parts = text.split(":")
    if not text or len(parts) > 3 or any(part == "" for part in parts):
        raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID") from exc
    if any(part < 0 for part in values):
        raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
    if len(values) == 1:
        return values[0]
    if values[-1] >= 60 or (len(values) == 3 and values[-2] >= 60):
        raise ValueError("RENDER_SPEC_SCENE_TIMING_INVALID")
    if len(values) == 2:
        minutes, seconds = values
        return minutes * 60 + seconds
    hours, minutes, seconds = values
    return hours * 3600 + minutes * 60 + seconds
