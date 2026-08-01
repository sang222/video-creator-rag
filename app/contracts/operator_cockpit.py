"""Read contracts for the Phase 6 operator production cockpit.

The cockpit deliberately exposes friendly, task-oriented projections. Raw
identifiers, hashes and receipts are kept in ``technical_appendix`` so a normal
operator never has to reason about internal workflow machinery.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


OperatorAction = Literal[
    "NONE",
    "START_PRODUCTION",
    "RESUME_PRODUCTION",
    "FINAL_REVIEW",
    "START_MANUAL_UPLOAD",
    "CONFIRM_MANUAL_UPLOAD",
    "CORRECT_CONFIRMATION",
    "RESOLVE_INCIDENT",
]
FinalVideoDecisionValue = Literal["UPLOAD", "DO_NOT_UPLOAD"]


class NextVideoRead(BaseModel):
    project_id: uuid.UUID
    workflow_run_id: uuid.UUID | None = None
    lane: Literal["LONG_FORM"]
    content_mode: str
    assignment_mode: str
    title: str
    topic: str | None = None
    series_title: str | None = None
    run_label: str | None = None
    episode_label: str | None = None
    standalone_reason: str | None = None
    why_selected: str
    production_state: str
    current_stage: str | None = None
    blocker: str | None = None
    next_action: str
    destination_label: str
    destination_handle: str | None = None
    estimated_cost: float | None = None
    actual_cost_so_far: float | None = None
    currency: str = "USD"
    provider_status: str
    render_status: str
    archive_status: str
    incident_status: str
    operator_action: OperatorAction = "NONE"
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class WorkflowStageProgressRead(BaseModel):
    stage: str
    state: str
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    retry_count: int = 0
    next_retry_at: AwareDatetime | None = None
    summary: str | None = None

    model_config = ConfigDict(extra="forbid")


class ProductionProgressRead(BaseModel):
    workflow_run_id: uuid.UUID
    project_id: uuid.UUID
    state: str
    active_stage: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    retry_count: int = 0
    next_retry_at: AwareDatetime | None = None
    lease_health: str = "UNKNOWN"
    provider_status: str = "NOT_STARTED"
    budget_status: str = "NOT_STARTED"
    estimated_cost: float | None = None
    reserved_cost: float | None = None
    settled_cost: float | None = None
    currency: str = "USD"
    render_status: str = "NOT_STARTED"
    render_progress_percent: int | None = None
    qc_status: str = "NOT_STARTED"
    archive_status: str = "NOT_STARTED"
    blocking_incident: str | None = None
    next_action: str
    operator_action: OperatorAction = "NONE"
    stages: list[WorkflowStageProgressRead] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class FinalReviewMediaRead(BaseModel):
    file_name: str
    player_url: str | None = None
    drive_web_view_url: str | None = None
    thumbnail_url: str | None = None
    captions_label: str | None = None
    checksum_sha256: str
    duration_seconds: float

    model_config = ConfigDict(extra="forbid")


class FinalReviewRead(BaseModel):
    candidate_id: uuid.UUID
    project_id: uuid.UUID
    workflow_run_id: uuid.UUID
    state: str
    title: str
    description: str
    lane: Literal["LONG_FORM"]
    content_mode: str
    audience_promise: str | None = None
    strategic_intent: str | None = None
    series_title: str | None = None
    run_label: str | None = None
    episode_label: str | None = None
    standalone_reason: str | None = None
    destination_label: str
    destination_handle: str | None = None
    media: FinalReviewMediaRead
    warnings: list[str] = Field(default_factory=list)
    rights_disclosure_summary: str
    auto_repair_summary: str
    archive_status: str
    decision: FinalVideoDecisionValue | None = None
    decision_recorded_at: AwareDatetime | None = None
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ManualPublishRead(BaseModel):
    task_id: uuid.UUID
    project_id: uuid.UUID
    final_review_candidate_id: uuid.UUID
    state: str
    exact_file_name: str
    drive_web_view_url: str | None = None
    verified_file_download_url: str | None = None
    reviewed_checksum_sha256: str
    target_platform: str
    destination_label: str
    destination_channel_id: str | None = None
    destination_handle: str | None = None
    platform_video_id: str | None = None
    platform_video_url: str | None = None
    actual_title: str | None = None
    actual_description: str | None = None
    actual_visibility: str | None = None
    actual_published_at: AwareDatetime | None = None
    actual_duration_seconds: float | None = None
    mismatch_state: str = "NOT_CHECKED"
    correction_state: str = "NOT_REQUIRED"
    uploaded_video_id: uuid.UUID | None = None
    uploaded_video_status: str = "NOT_RECORDED"
    analytics_ready: bool = False
    next_action: str
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProductionCockpitRead(BaseModel):
    generated_at: AwareDatetime
    next_video: NextVideoRead | None = None
    progress: ProductionProgressRead | None = None
    final_review: FinalReviewRead | None = None
    manual_publish: ManualPublishRead | None = None
    safety_notice: str = "VCOS không tự upload hoặc publish. Người vận hành thực hiện upload bên ngoài VCOS."
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
