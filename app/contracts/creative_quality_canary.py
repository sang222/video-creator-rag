from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CQR1_RUN_ID = "pa1r-cqr1-20260714-paid-canary-001"
CQR1_PURPOSE = "CQR1_CONTROLLED_PAID_CANARY"

CreativeGateDecision = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]


class CreativeGateEvidence(BaseModel):
    gate_name: str = Field(min_length=1)
    result: CreativeGateDecision
    reason_codes: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class FinalDurationEvidence(BaseModel):
    canonical_timeline_duration_ms: int = Field(gt=0)
    final_narration_duration_ms: int = Field(gt=0)
    final_mp4_duration_ms: int = Field(gt=0)
    final_caption_end_ms: int = Field(gt=0)
    final_scene_end_ms: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class FinalDurationConsistencyResult(CreativeGateEvidence):
    gate_name: Literal["FinalDurationConsistencyGate"] = "FinalDurationConsistencyGate"


class TechnicalMediaQCReport(BaseModel):
    run_id: str = Field(min_length=1)
    result: Literal["PASS", "FAIL"]
    checks: dict[str, Any]
    required_checks: list[str] = Field(min_length=1)
    reason_codes: list[str] = Field(default_factory=list)
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CreativePerceptualMediaQCReport(BaseModel):
    run_id: str = Field(min_length=1)
    result: CreativeGateDecision
    gate_results: list[CreativeGateEvidence]
    required_gates: list[str] = Field(min_length=1)
    missing_gates: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    technical_media_qc_implies_creative_pass: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


HUMAN_WATCHABILITY_DIMENSIONS = (
    "VOICE_NATURALNESS",
    "VOICE_PACE_COMFORT",
    "CAPTION_READABILITY",
    "CAPTION_SYNC_TRUST",
    "SCENE_RELEVANCE",
    "VISUAL_CONTINUITY",
    "TRANSITION_QUALITY",
    "OVERALL_WATCHABILITY_AI_SLOP",
)

HUMAN_CRITICAL_REASON_CODES = (
    "HUMAN_VOICE_RUSHED",
    "HUMAN_VOICE_UNNATURAL",
    "HUMAN_CAPTION_DOMINANT",
    "HUMAN_CAPTION_UNREADABLE",
    "HUMAN_SYNC_DISTRACTING",
    "HUMAN_SCENE_IRRELEVANT",
    "HUMAN_VISUAL_DISCONTINUITY",
    "HUMAN_TRANSITION_JOLT",
    "HUMAN_AI_SLOP",
)


class PendingHumanDimension(BaseModel):
    dimension: str = Field(min_length=1)
    score: None = None
    notes: str = ""

    model_config = ConfigDict(extra="forbid")


class TimestampedReviewIssue(BaseModel):
    timestamp_ms: int = Field(ge=0)
    reason_code: str = Field(min_length=1)
    notes: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class HumanWatchabilityReviewPacket(BaseModel):
    run_id: str = Field(min_length=1)
    review_state: Literal["PENDING"] = "PENDING"
    final_mp4_path: str = Field(min_length=1)
    contact_sheet_path: str = Field(min_length=1)
    before_after_packet_ref: str = Field(min_length=1)
    drive_archive_receipt_ref: str | None = None
    dimensions: list[PendingHumanDimension] = Field(min_length=8, max_length=8)
    timestamped_issues: list[TimestampedReviewIssue] = Field(default_factory=list)
    critical_reason_code_checklist: dict[str, Literal[False]]
    uninterrupted_full_watch_1x_completed: Literal[False] = False
    optional_flagged_spot_check_speed: float = Field(gt=0, le=1)
    pass_total_minimum: int = Field(gt=0, le=40)
    pass_dimension_minimum: int = Field(ge=1, le=5)
    repair_total_range: tuple[int, int]
    critical_issue_overrides_average: Literal[True] = True
    no_publish_statement: str = Field(min_length=1)
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def pending_packet_is_complete(self) -> "HumanWatchabilityReviewPacket":
        dimensions = [item.dimension for item in self.dimensions]
        if set(dimensions) != set(HUMAN_WATCHABILITY_DIMENSIONS) or len(dimensions) != len(set(dimensions)):
            raise ValueError("HUMAN_WATCHABILITY_DIMENSIONS_INVALID")
        if set(self.critical_reason_code_checklist) != set(HUMAN_CRITICAL_REASON_CODES):
            raise ValueError("HUMAN_CRITICAL_REASON_CODE_CHECKLIST_INVALID")
        if self.repair_total_range[0] > self.repair_total_range[1]:
            raise ValueError("HUMAN_WATCHABILITY_REPAIR_RANGE_INVALID")
        if self.repair_total_range[1] >= self.pass_total_minimum:
            raise ValueError("HUMAN_WATCHABILITY_PASS_RANGE_OVERLAP")
        return self


class CQR1OfflineQualificationEvidence(BaseModel):
    cqr1b_c_offline_fixtures_passed: bool = False
    golden_media_tests_passed: bool = False
    negative_tests_passed: bool = False
    technical_media_qc_fixture_passed: bool = False
    creative_media_qc_fixture_passed: bool = False
    alembic_one_head_passed: bool = False
    compileall_passed: bool = False
    focused_regressions_passed: bool = False
    git_diff_check_passed: bool = False
    historical_evidence_immutable: bool = False

    model_config = ConfigDict(extra="forbid")

    @property
    def all_passed(self) -> bool:
        return all(self.model_dump().values())


class CQR1ProviderReadinessEvidence(BaseModel):
    pexels_api_key_configured: bool = False
    elevenlabs_api_key_configured: bool = False
    elevenlabs_voice_id_configured: bool = False
    elevenlabs_model_id_configured: bool = False
    elevenlabs_tts_access_confirmed: bool = False
    elevenlabs_voices_read_confirmed: bool = False
    elevenlabs_models_read_confirmed: bool = False
    elevenlabs_forced_alignment_permission_confirmed: bool | Literal["unknown"] = "unknown"
    google_veo_api_key_configured: bool = False
    google_veo_model_accessible: bool = False
    drive_oauth_connected: bool = False
    drive_archive_root_configured: bool = False
    secret_values_exposed: Literal[False] = False
    provider_probe_count: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")

    @property
    def all_passed(self) -> bool:
        payload = self.model_dump(exclude={"secret_values_exposed", "provider_probe_count"})
        return all(value is True for value in payload.values())


class CQR1CanaryApprovalScope(BaseModel):
    run_id: str = CQR1_RUN_ID
    purpose: str = CQR1_PURPOSE
    maximum_pexels_search_flows: int = Field(default=1, ge=0, le=1)
    maximum_pexels_downloads: int = Field(default=1, ge=0, le=1)
    maximum_elevenlabs_tts_generations: int = Field(default=1, ge=0, le=1)
    maximum_elevenlabs_forced_alignment_calls: int = Field(default=1, ge=0, le=1)
    maximum_google_veo_submits: int = Field(default=1, ge=0, le=1)
    maximum_google_veo_outputs: int = Field(default=1, ge=0, le=1)
    maximum_drive_archive_attempts: int = Field(default=1, ge=0, le=1)
    total_hard_cost_cap_usd: Decimal = Field(default=Decimal("3.00"), gt=0, le=Decimal("3.00"))
    automatic_provider_retry: Literal[False] = False
    external_provider_fallback: Literal[False] = False
    youtube_allowed: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    approval_ref: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def exact_scope(self) -> "CQR1CanaryApprovalScope":
        if self.run_id != CQR1_RUN_ID or self.purpose != CQR1_PURPOSE:
            raise ValueError("CQR1_APPROVAL_SCOPE_MISMATCH")
        limits = (
            self.maximum_pexels_search_flows,
            self.maximum_pexels_downloads,
            self.maximum_elevenlabs_tts_generations,
            self.maximum_elevenlabs_forced_alignment_calls,
            self.maximum_google_veo_submits,
            self.maximum_google_veo_outputs,
            self.maximum_drive_archive_attempts,
        )
        if any(value != 1 for value in limits):
            raise ValueError("CQR1_APPROVAL_ONE_SHOT_LIMIT_MISMATCH")
        return self


class CQR1ProviderCallLedgerEntry(BaseModel):
    operation_key: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    paid: bool
    status: Literal["PLANNED", "EXECUTING", "SUCCEEDED", "FAILED"] = "PLANNED"
    attempt_count: int = Field(default=0, ge=0, le=1)
    max_attempts: Literal[1] = 1
    output_count: int = Field(default=0, ge=0, le=1)
    idempotency_key_hash: str = Field(min_length=1)
    provider_call_made: bool = False
    safe_evidence: dict[str, Any] = Field(default_factory=dict)
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True

    model_config = ConfigDict(extra="forbid")


class CQR1PaidCanaryPreflightResult(BaseModel):
    run_id: str = CQR1_RUN_ID
    status: Literal["PASS", "BLOCKED"]
    blocker_reason_codes: list[str] = Field(default_factory=list)
    exact_next_action: str = Field(min_length=1)
    offline_gate_passed: bool
    provider_readiness_passed: bool
    ledger_fresh: bool
    provider_call_count: int = Field(ge=0)
    provider_execution_allowed: bool = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def fail_closed(self) -> "CQR1PaidCanaryPreflightResult":
        if self.status == "PASS":
            if self.blocker_reason_codes or not self.provider_execution_allowed:
                raise ValueError("CQR1_PREFLIGHT_PASS_INCONSISTENT")
        elif self.provider_execution_allowed:
            raise ValueError("CQR1_PREFLIGHT_BLOCKED_EXECUTION_FORBIDDEN")
        return self
