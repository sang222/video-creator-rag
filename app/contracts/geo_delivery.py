from __future__ import annotations

import hashlib
import json
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"


def geo_delivery_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", exclude={"content_hash"})
    else:
        payload = value
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class _HashBoundModel(_StrictModel):
    content_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def bind_content_hash(self) -> "_HashBoundModel":
        expected = geo_delivery_hash(self)
        if self.content_hash is not None and self.content_hash != expected:
            raise ValueError("CONTENT_HASH_MISMATCH")
        object.__setattr__(self, "content_hash", expected)
        return self


class DeliveryVerdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class MarketDeliveryReasonCode(StrEnum):
    MARKET_LANGUAGE_MISMATCH = "MARKET_LANGUAGE_MISMATCH"
    VOICE_LOCALE_MISMATCH = "VOICE_LOCALE_MISMATCH"
    METADATA_LOCALE_MISMATCH = "METADATA_LOCALE_MISMATCH"
    CURRENCY_CONTEXT_MISMATCH = "CURRENCY_CONTEXT_MISMATCH"
    UNIT_SYSTEM_MISMATCH = "UNIT_SYSTEM_MISMATCH"
    SOURCE_JURISDICTION_MISMATCH = "SOURCE_JURISDICTION_MISMATCH"
    VISUAL_GEO_MISMATCH = "VISUAL_GEO_MISMATCH"
    PUBLISH_TIMEZONE_MISMATCH = "PUBLISH_TIMEZONE_MISMATCH"
    DESTINATION_MARKET_MISMATCH = "DESTINATION_MARKET_MISMATCH"
    LOCALIZATION_FEELS_TRANSLATED = "LOCALIZATION_FEELS_TRANSLATED"


class DestinationRuntimeContract(_HashBoundModel):
    schema_version: Literal["geo-delivery.destination-runtime-contract.v1"] = (
        "geo-delivery.destination-runtime-contract.v1"
    )
    destination_binding_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    platform: Literal["YOUTUBE", "TIKTOK"]
    platform_account_id: str | None = None
    platform_account_ref: str | None = None
    platform_channel_id: str | None = None
    handle: str | None = None
    account_country_region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    default_language: str = Field(pattern=r"^[a-z]{2,3}$")
    publish_mode: Literal["MANUAL"] = "MANUAL"
    requires_manual_publish: Literal[True] = True
    status: Literal[
        "DRAFT",
        "PENDING_VERIFICATION",
        "PENDING_PLATFORM_ID",
        "VERIFIED",
        "REVOKED",
    ]
    verified_at: AwareDatetime | None = None
    verification_method: str | None = None
    binding_fingerprint: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_publish_verification(self) -> "DestinationRuntimeContract":
        if self.status == "VERIFIED" and (
            not self.platform_channel_id
            or self.verified_at is None
            or not self.verification_method
        ):
            raise ValueError("DESTINATION_VERIFIED_EVIDENCE_INCOMPLETE")
        if self.status != "VERIFIED" and self.verified_at is not None:
            raise ValueError("DESTINATION_UNVERIFIED_TIMESTAMP_FORBIDDEN")
        return self


class MarketDeliveryEvidence(_StrictModel):
    policy_snapshot_id: uuid.UUID
    market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    target_market_profile_ref: str = Field(min_length=1)
    target_market_profile_hash: str = Field(pattern=SHA256_PATTERN)
    market_alignment_dossier_ref: str = Field(min_length=1)
    market_alignment_dossier_hash: str = Field(pattern=SHA256_PATTERN)
    creative_brief_ref: str = Field(min_length=1)
    research_pack_ref: str = Field(min_length=1)
    script_ref: str = Field(min_length=1)
    voice_manifest_ref: str = Field(min_length=1)
    visual_plan_ref: str = Field(min_length=1)
    metadata_package_ref: str = Field(min_length=1)
    caption_plan_ref: str = Field(min_length=1)
    caption_plan_state: Literal[
        "WAITING_FOR_FINAL_AUDIO_ALIGNMENT",
        "FINALIZED",
    ]
    caption_artifact_ref: str | None = None
    thumbnail_brief_ref: str = Field(min_length=1)
    publish_package_ref: str = Field(min_length=1)
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str = Field(pattern=SHA256_PATTERN)
    expected_market: str = Field(pattern=r"^[A-Z]{2}$")
    expected_content_language: str = Field(pattern=r"^[a-z]{2,3}$")
    expected_locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    expected_currency: str = Field(pattern=r"^[A-Z]{3}$")
    expected_unit_system: str = Field(min_length=1)
    expected_date_format: str = Field(min_length=1)
    expected_timezone: str = Field(min_length=1)
    preferred_source_jurisdictions: list[str] = Field(min_length=1)
    acceptable_visual_geos: list[str] = Field(min_length=1)
    script_locale: str = Field(min_length=2)
    voice_locale: str = Field(min_length=2)
    voice_content_language: str = Field(pattern=r"^[a-z]{2,3}$")
    metadata_locale: str = Field(min_length=2)
    metadata_original_language: str = Field(pattern=r"^[a-z]{2,3}$")
    caption_locales: list[str] = Field(min_length=1)
    currency_contexts: list[str] = Field(default_factory=list)
    unit_system: str = Field(min_length=1)
    date_format: str = Field(min_length=1)
    source_jurisdictions: list[str] = Field(min_length=1)
    local_examples_present: bool
    visual_geos: list[str] = Field(default_factory=list)
    ui_locales: list[str] = Field(default_factory=list)
    destination_market: str = Field(pattern=r"^[A-Z]{2}$")
    destination_status: str = Field(min_length=1)
    publish_timezone: str = Field(min_length=1)
    approved_publish_window: dict[str, Any] = Field(default_factory=dict)
    terminology_localized: bool = True
    translated_sounding_copy: bool = False

    @model_validator(mode="after")
    def validate_caption_authority(self) -> "MarketDeliveryEvidence":
        if self.caption_plan_state == "FINALIZED" and not self.caption_artifact_ref:
            raise ValueError("GEO_DELIVERY_FINAL_CAPTION_ARTIFACT_MISSING")
        if (
            self.caption_plan_state == "WAITING_FOR_FINAL_AUDIO_ALIGNMENT"
            and self.caption_artifact_ref is not None
        ):
            raise ValueError("GEO_DELIVERY_PENDING_CAPTION_ARTIFACT_FORBIDDEN")
        return self


class MarketDeliveryAlignmentResult(_HashBoundModel):
    schema_version: Literal["geo-delivery.market-alignment-result.v1"] = (
        "geo-delivery.market-alignment-result.v1"
    )
    policy_snapshot_id: uuid.UUID
    market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str = Field(pattern=SHA256_PATTERN)
    verdict: DeliveryVerdict
    reason_codes: list[MarketDeliveryReasonCode] = Field(default_factory=list)
    checks: dict[str, bool]
    evidence_refs: list[str] = Field(min_length=1)
    exact_next_action: str | None = None


class StrictMarketLineageEnvelope(_HashBoundModel):
    schema_version: Literal["geo-delivery.strict-market-lineage.v1"] = (
        "geo-delivery.strict-market-lineage.v1"
    )
    policy_snapshot_id: uuid.UUID
    approved_market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    target_market_profile_ref: str = Field(min_length=1)
    target_market_profile_hash: str = Field(pattern=SHA256_PATTERN)
    market_alignment_dossier_ref: str = Field(min_length=1)
    market_alignment_dossier_hash: str = Field(pattern=SHA256_PATTERN)
    destination_binding_id: uuid.UUID
    approved_destination_fingerprint: str = Field(pattern=SHA256_PATTERN)
    approved_platform: str = Field(min_length=1)
    approved_platform_channel_id: str | None = None
    approved_handle: str | None = None
    approved_package_hash: str = Field(pattern=SHA256_PATTERN)
    approved_publish_timezone: str = Field(min_length=1)
    approved_publish_window: dict[str, Any] = Field(min_length=1)
    approval_decision_id: uuid.UUID


class ActualPublishDestination(_StrictModel):
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str = Field(pattern=SHA256_PATTERN)
    destination_status: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    platform_channel_id: str = Field(min_length=1)
    external_video_id: str = Field(min_length=1)
    external_video_url: str = Field(min_length=1)
    published_at: AwareDatetime
    published_market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    published_package_hash: str = Field(pattern=SHA256_PATTERN)


class StrictMarketLineageResult(_HashBoundModel):
    schema_version: Literal["geo-delivery.strict-market-lineage-result.v1"] = (
        "geo-delivery.strict-market-lineage-result.v1"
    )
    verdict: DeliveryVerdict
    reason_codes: list[str] = Field(default_factory=list)
    approved_market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    published_market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    approved_destination_binding_id: uuid.UUID
    actual_destination_binding_id: uuid.UUID
    approved_destination_fingerprint: str = Field(pattern=SHA256_PATTERN)
    actual_destination_fingerprint: str = Field(pattern=SHA256_PATTERN)
    exact_next_action: str


class AnalyticsConfidenceState(StrEnum):
    TOO_EARLY = "TOO_EARLY"
    WEAK_SIGNAL = "WEAK_SIGNAL"
    DIRECTIONAL = "DIRECTIONAL"
    STABLE = "STABLE"
    ACTION_READY = "ACTION_READY"


class GeoAlignmentState(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    GEO_ON_TRACK = "GEO_ON_TRACK"
    GEO_DRIFT_DIRECTIONAL = "GEO_DRIFT_DIRECTIONAL"
    GEO_MISMATCH_STABLE = "GEO_MISMATCH_STABLE"
    ACTION_READY = "ACTION_READY"


class GeoWindow(StrEnum):
    H24 = "24H"
    H72 = "72H"
    D7 = "7D"
    D30 = "30D"


class MetricDataState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_DATA = "insufficient_data"


GeoMetric = dict[str, float] | None
GeoNestedMetric = dict[str, dict[str, float]] | None


class GeoAnalyticsInput(_StrictModel):
    analytics_snapshot_id: uuid.UUID
    uploaded_video_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    captured_at: AwareDatetime
    published_at: AwareDatetime
    observation_window: GeoWindow
    confidence_state: AnalyticsConfidenceState
    views_by_geo: GeoMetric = None
    watch_time_by_geo: GeoMetric = None
    average_view_duration_by_geo: GeoMetric = None
    subscribers_gained_by_geo: GeoMetric = None
    impressions_by_geo: GeoMetric = None
    estimated_monetized_playbacks_by_geo: GeoMetric = None
    revenue_by_geo: GeoMetric = None
    traffic_source_by_geo: GeoNestedMetric = None
    subtitle_audio_language_usage: GeoMetric = None
    unavailable_metrics: list[str] = Field(default_factory=list)
    processing_or_policy_incident: bool = False
    destination_enforcement_incident: bool = False

    @field_validator(
        "views_by_geo",
        "watch_time_by_geo",
        "average_view_duration_by_geo",
        "subscribers_gained_by_geo",
        "impressions_by_geo",
        "estimated_monetized_playbacks_by_geo",
        "revenue_by_geo",
        "subtitle_audio_language_usage",
    )
    @classmethod
    def validate_metric_map(cls, value: GeoMetric) -> GeoMetric:
        if value is not None and any(metric < 0 for metric in value.values()):
            raise ValueError("GEO_METRIC_NEGATIVE")
        return value


class GeoDistributionTracker(_HashBoundModel):
    schema_version: Literal["geo-delivery.geo-distribution-tracker.v1"] = (
        "geo-delivery.geo-distribution-tracker.v1"
    )
    uploaded_video_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    destination_binding_id: uuid.UUID
    destination_binding_fingerprint: str = Field(pattern=SHA256_PATTERN)
    policy_snapshot_id: uuid.UUID
    market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    target_market_profile_ref: str = Field(min_length=1)
    target_market_profile_hash: str = Field(pattern=SHA256_PATTERN)
    analytics_snapshot_id: uuid.UUID
    expected_primary_geos: list[str] = Field(min_length=1)
    acceptable_spillover_geos: list[str] = Field(default_factory=list)
    latest_window: GeoWindow
    latest_alignment_state: GeoAlignmentState
    latest_confidence_state: AnalyticsConfidenceState
    views_by_geo: GeoMetric = None
    watch_time_by_geo: GeoMetric = None
    average_view_duration_by_geo: GeoMetric = None
    subscribers_gained_by_geo: GeoMetric = None
    impressions_by_geo: GeoMetric = None
    estimated_monetized_playbacks_by_geo: GeoMetric = None
    revenue_by_geo: GeoMetric = None
    traffic_source_by_geo: GeoNestedMetric = None
    subtitle_audio_language_usage: GeoMetric = None
    metric_states: dict[str, MetricDataState]
    target_geo_share: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    action_allowed: bool = False


class ComparableVideoGeoSignal(_StrictModel):
    uploaded_video_id: uuid.UUID
    profile_family_ref: str = Field(min_length=1)
    policy_family_ref: str = Field(min_length=1)
    alignment_state: GeoAlignmentState
    confidence_state: AnalyticsConfidenceState
    drift_signature: str | None = None
    processing_or_policy_incident: bool = False
    destination_enforcement_incident: bool = False


class GeoDiagnosticResult(_HashBoundModel):
    schema_version: Literal["geo-delivery.geo-diagnostic-result.v1"] = (
        "geo-delivery.geo-diagnostic-result.v1"
    )
    video_reason_codes: list[str] = Field(default_factory=list)
    channel_reason_codes: list[str] = Field(default_factory=list)
    comparable_video_count: int = Field(ge=0)
    confidence_state: AnalyticsConfidenceState
    action_allowed: bool
    exact_next_action: str


class PlatformRevenueType(StrEnum):
    YOUTUBE_AD_FINALIZED = "YOUTUBE_AD_FINALIZED"
    YOUTUBE_SHORTS_FINALIZED = "YOUTUBE_SHORTS_FINALIZED"
    YOUTUBE_PREMIUM_FINALIZED = "YOUTUBE_PREMIUM_FINALIZED"
    TIKTOK_REWARDS_FINALIZED_IF_ELIGIBLE = "TIKTOK_REWARDS_FINALIZED_IF_ELIGIBLE"


class AdsOnlyMonetizationPolicy(_HashBoundModel):
    schema_version: Literal["geo-delivery.ads-only-policy.v1"] = (
        "geo-delivery.ads-only-policy.v1"
    )
    monetization_mode: Literal["PLATFORM_AD_REVENUE_ONLY"] = "PLATFORM_AD_REVENUE_ONLY"
    base_policy_snapshot_id: uuid.UUID
    base_policy_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    overlay_authority_ref: str = Field(min_length=1)
    allowed_revenue_types: list[PlatformRevenueType] = Field(min_length=4, max_length=4)
    affiliate_enabled: Literal[False] = False
    shopping_enabled: Literal[False] = False
    product_sales_enabled: Literal[False] = False
    service_sales_enabled: Literal[False] = False
    lead_generation_enabled: Literal[False] = False
    sponsorship_base_case_enabled: Literal[False] = False
    memberships_enabled: Literal[False] = False
    gifts_enabled: Literal[False] = False
    donations_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_allowed_set(self) -> "AdsOnlyMonetizationPolicy":
        if set(self.allowed_revenue_types) != set(PlatformRevenueType):
            raise ValueError("ADS_ONLY_ALLOWED_REVENUE_SET_INVALID")
        return self


class SelfFundingWindow(_StrictModel):
    window_key: str = Field(min_length=1)
    revenue_type: PlatformRevenueType
    revenue_amount: float = Field(ge=0)
    revenue_state: Literal["FINALIZED", "LOCKED", "PAID"]
    allocated_cost: float = Field(ge=0)
    raw_views: float | None = Field(default=None, ge=0)
    estimated_revenue: float | None = Field(default=None, ge=0)
    projected_revenue: float | None = Field(default=None, ge=0)


class SelfFundingResult(_HashBoundModel):
    schema_version: Literal["geo-delivery.self-funding-result.v1"] = (
        "geo-delivery.self-funding-result.v1"
    )
    verdict: DeliveryVerdict
    consecutive_qualifying_windows: int = Field(ge=0)
    qualifying_window_keys: list[str] = Field(default_factory=list)
    excluded_estimate_fields: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    exact_next_action: str


class GeoDeliveryAcceptanceGate(StrEnum):
    MARKET_LINEAGE = "GEO_DELIVERY_CLOSEOUT_MARKET_LINEAGE"
    DESTINATION_ENFORCEMENT = "GEO_DELIVERY_CLOSEOUT_DESTINATION_ENFORCEMENT"
    MARKET_ALIGNMENT = "GEO_DELIVERY_CLOSEOUT_MARKET_ALIGNMENT"
    DISTRIBUTION_TRACKER = "GEO_DISTRIBUTION_TRACKER"
    MATURITY_INTEGRATION = "GEO_MATURITY_INTEGRATION"
    DIAGNOSTIC_RULES = "GEO_DIAGNOSTIC_RULES"
    ADS_ONLY_MONETIZATION_POLICY = "ADS_ONLY_MONETIZATION_POLICY"


GEO_DELIVERY_ACCEPTANCE_GATES: tuple[GeoDeliveryAcceptanceGate, ...] = tuple(
    GeoDeliveryAcceptanceGate
)


class GeoDeliveryImmutableEvidenceRef(_StrictModel):
    evidence_type: Literal[
        "VERIFICATION_MANIFEST",
        "MACHINE_VERIFICATION_RUN",
        "ARTIFACT_VERSION",
    ]
    ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)


class GeoDeliveryVerificationNodeOutcome(_StrictModel):
    node_id: str = Field(min_length=1)
    outcome: Literal["passed", "failed", "skipped"]


class GeoDeliveryVerificationRun(_HashBoundModel):
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    run_kind: Literal["PYTEST", "STATIC_CHECK"]
    command: list[str] = Field(min_length=1)
    exit_code: int
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    output_hash: str = Field(pattern=SHA256_PATTERN)
    verdict: DeliveryVerdict
    node_outcomes: list[GeoDeliveryVerificationNodeOutcome] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_machine_result(self) -> "GeoDeliveryVerificationRun":
        node_ids = [item.node_id for item in self.node_outcomes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("GEO_VERIFICATION_NODE_ID_DUPLICATE")
        if self.verdict == DeliveryVerdict.PASS and (
            self.exit_code != 0 or self.failed != 0
        ):
            raise ValueError("GEO_VERIFICATION_RUN_FALSE_PASS")
        if self.run_kind == "PYTEST":
            if self.verdict == DeliveryVerdict.PASS and not self.node_outcomes:
                raise ValueError("GEO_VERIFICATION_PYTEST_NODE_OUTCOMES_MISSING")
            counts = {
                outcome: sum(item.outcome == outcome for item in self.node_outcomes)
                for outcome in ("passed", "failed", "skipped")
            }
            if (
                counts["passed"] != self.passed
                or counts["failed"] != self.failed
                or counts["skipped"] != self.skipped
            ):
                raise ValueError("GEO_VERIFICATION_PYTEST_COUNTS_MISMATCH")
        elif self.node_outcomes:
            raise ValueError("GEO_VERIFICATION_STATIC_NODE_OUTCOMES_FORBIDDEN")
        return self


class GeoDeliveryVerificationGateResult(_HashBoundModel):
    gate: GeoDeliveryAcceptanceGate
    verdict: DeliveryVerdict
    checks: dict[str, bool] = Field(min_length=1)
    verification_run_ids: list[str] = Field(min_length=1)
    required_node_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pass_checks(self) -> "GeoDeliveryVerificationGateResult":
        if len(self.verification_run_ids) != len(set(self.verification_run_ids)):
            raise ValueError("GEO_VERIFICATION_GATE_RUN_REF_DUPLICATE")
        if len(self.required_node_ids) != len(set(self.required_node_ids)):
            raise ValueError("GEO_VERIFICATION_GATE_REQUIRED_NODE_DUPLICATE")
        if self.verdict == DeliveryVerdict.PASS and not all(self.checks.values()):
            raise ValueError("GEO_VERIFICATION_GATE_FALSE_PASS")
        return self


class GeoDeliveryVerificationManifest(_HashBoundModel):
    schema_version: Literal["geo-delivery.verification-manifest.v1"] = (
        "geo-delivery.verification-manifest.v1"
    )
    producer: Literal["VCOS_MACHINE_VERIFICATION_RUNNER"]
    generated_at: AwareDatetime
    workspace_hash: str = Field(pattern=SHA256_PATTERN)
    repository_revision: str = Field(pattern=r"^workspace-sha256:[0-9a-f]{64}$")
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    policy_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    source_package_artifact_version_id: uuid.UUID
    source_package_content_hash: str = Field(pattern=SHA256_PATTERN)
    verification_runs: list[GeoDeliveryVerificationRun] = Field(min_length=1)
    gate_results: list[GeoDeliveryVerificationGateResult] = Field(
        min_length=7,
        max_length=7,
    )

    @model_validator(mode="after")
    def validate_complete_gate_run_graph(self) -> "GeoDeliveryVerificationManifest":
        if self.repository_revision != f"workspace-sha256:{self.workspace_hash}":
            raise ValueError("GEO_VERIFICATION_REPOSITORY_REVISION_MISMATCH")
        run_ids = [item.run_id for item in self.verification_runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("GEO_VERIFICATION_RUN_ID_DUPLICATE")
        gate_keys = [item.gate for item in self.gate_results]
        if len(gate_keys) != len(set(gate_keys)) or set(gate_keys) != set(
            GEO_DELIVERY_ACCEPTANCE_GATES
        ):
            raise ValueError("GEO_VERIFICATION_GATE_SET_INCOMPLETE")
        known_runs = set(run_ids)
        by_run_id = {item.run_id: item for item in self.verification_runs}
        for gate in self.gate_results:
            if not set(gate.verification_run_ids).issubset(known_runs):
                raise ValueError("GEO_VERIFICATION_GATE_RUN_REF_INVALID")
            if gate.verdict == DeliveryVerdict.PASS and any(
                by_run_id[run_id].verdict != DeliveryVerdict.PASS
                for run_id in gate.verification_run_ids
            ):
                raise ValueError("GEO_VERIFICATION_GATE_RUN_NOT_PASSING")
            referenced_outcomes = {
                outcome.node_id: outcome.outcome
                for run_id in gate.verification_run_ids
                for outcome in by_run_id[run_id].node_outcomes
            }
            if gate.verdict == DeliveryVerdict.PASS and any(
                referenced_outcomes.get(node_id) != "passed"
                for node_id in gate.required_node_ids
            ):
                raise ValueError("GEO_VERIFICATION_REQUIRED_NODE_NOT_PASSING")
        return self


class GeoDeliveryVerificationReceiptRunEvidence(_StrictModel):
    run_id: str = Field(min_length=1)
    run_kind: Literal["PYTEST", "STATIC_CHECK"]
    command: list[str] = Field(min_length=1)
    exit_code: int
    output_hash: str = Field(pattern=SHA256_PATTERN)
    verdict: DeliveryVerdict


class GeoDeliveryVerificationReceipt(_HashBoundModel):
    schema_version: Literal["geo-delivery.verification-receipt.v1"] = (
        "geo-delivery.verification-receipt.v1"
    )
    producer: Literal["VCOS_MACHINE_VERIFICATION_RUNNER"]
    manifest: GeoDeliveryVerificationManifest
    run_evidence: list[GeoDeliveryVerificationReceiptRunEvidence] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_exact_run_evidence(self) -> "GeoDeliveryVerificationReceipt":
        expected = {
            (
                item.run_id,
                item.run_kind,
                tuple(item.command),
                item.exit_code,
                item.output_hash,
                item.verdict,
            )
            for item in self.manifest.verification_runs
        }
        actual = {
            (
                item.run_id,
                item.run_kind,
                tuple(item.command),
                item.exit_code,
                item.output_hash,
                item.verdict,
            )
            for item in self.run_evidence
        }
        if len(actual) != len(self.run_evidence) or actual != expected:
            raise ValueError("GEO_VERIFICATION_RECEIPT_RUN_EVIDENCE_MISMATCH")
        return self


class GeoDeliveryAcceptanceGateResult(_HashBoundModel):
    gate: GeoDeliveryAcceptanceGate
    verdict: DeliveryVerdict
    checks: dict[str, bool] = Field(min_length=1)
    evidence_refs: list[GeoDeliveryImmutableEvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_truthful_pass(self) -> "GeoDeliveryAcceptanceGateResult":
        if self.verdict == DeliveryVerdict.PASS and not all(self.checks.values()):
            raise ValueError("GEO_ACCEPTANCE_GATE_FALSE_PASS")
        refs = [(item.ref, item.content_hash) for item in self.evidence_refs]
        if len(refs) != len(set(refs)):
            raise ValueError("GEO_ACCEPTANCE_EVIDENCE_REF_DUPLICATE")
        return self


class GeoDeliveryAcceptanceEvidenceSet(_HashBoundModel):
    results: list[GeoDeliveryAcceptanceGateResult] = Field(
        min_length=7,
        max_length=7,
    )

    @model_validator(mode="after")
    def validate_complete_gate_set(self) -> "GeoDeliveryAcceptanceEvidenceSet":
        gates = [item.gate for item in self.results]
        if len(gates) != len(set(gates)) or set(gates) != set(
            GEO_DELIVERY_ACCEPTANCE_GATES
        ):
            raise ValueError("GEO_ACCEPTANCE_GATE_SET_INCOMPLETE")
        return self


class GeoDeliveryAcceptanceVerdicts(_StrictModel):
    GEO_DELIVERY_CLOSEOUT_MARKET_LINEAGE: Literal["PASS"]
    GEO_DELIVERY_CLOSEOUT_DESTINATION_ENFORCEMENT: Literal["PASS"]
    GEO_DELIVERY_CLOSEOUT_MARKET_ALIGNMENT: Literal["PASS"]
    GEO_DISTRIBUTION_TRACKER: Literal["PASS"]
    GEO_MATURITY_INTEGRATION: Literal["PASS"]
    GEO_DIAGNOSTIC_RULES: Literal["PASS"]
    ADS_ONLY_MONETIZATION_POLICY: Literal["PASS"]


class GeoDeliveryArtifactRef(_StrictModel):
    artifact_type: Literal[
        "effective_ads_only_monetization_policy",
        "geo_market_delivery_closeout_evidence",
    ]
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    version_number: int = Field(ge=1)
    ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)


class GeoDeliveryNoExecutionProof(_HashBoundModel):
    schema_version: Literal["geo-delivery.no-execution-proof.v1"] = (
        "geo-delivery.no-execution-proof.v1"
    )
    before_counts: dict[str, int] = Field(min_length=1)
    after_counts: dict[str, int] = Field(min_length=1)
    deltas: dict[str, int] = Field(min_length=1)
    all_deltas_zero: Literal[True]

    @model_validator(mode="after")
    def validate_measured_zero_delta(self) -> "GeoDeliveryNoExecutionProof":
        if (
            set(self.before_counts) != set(self.after_counts)
            or set(self.before_counts) != set(self.deltas)
            or any(value < 0 for value in self.before_counts.values())
            or any(value < 0 for value in self.after_counts.values())
            or self.deltas
            != {
                key: self.after_counts[key] - value
                for key, value in self.before_counts.items()
            }
            or any(self.deltas.values())
        ):
            raise ValueError("GEO_DELIVERY_NO_EXECUTION_PROOF_INVALID")
        return self


class EffectiveAdsOnlyPolicyArtifact(_HashBoundModel):
    schema_version: Literal["geo-delivery.effective-ads-only-policy-artifact.v1"] = (
        "geo-delivery.effective-ads-only-policy-artifact.v1"
    )
    artifact_state: Literal["SUBMITTED"] = "SUBMITTED"
    immutable: Literal[True] = True
    base_policy_snapshot_id: uuid.UUID
    base_policy_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    effective_market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    policy: AdsOnlyMonetizationPolicy


class GeoMarketDeliveryCloseoutEvidence(_HashBoundModel):
    schema_version: Literal["geo-delivery.closeout-evidence.v1"] = (
        "geo-delivery.closeout-evidence.v1"
    )
    artifact_state: Literal["SUBMITTED"] = "SUBMITTED"
    immutable: Literal[True] = True
    base_policy_snapshot_id: uuid.UUID
    base_policy_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    effective_market_policy_hash: str = Field(pattern=SHA256_PATTERN)
    source_approval_decision_id: uuid.UUID
    source_approval_scope: Literal["PKG1_MARKET_REVISION_PACKAGE_PLANNING"]
    human_review_receipt_artifact_version_id: uuid.UUID
    human_review_receipt_content_hash: str = Field(pattern=SHA256_PATTERN)
    verification_receipt_artifact_version_id: uuid.UUID
    verification_receipt_content_hash: str = Field(pattern=SHA256_PATTERN)
    no_execution_proof: GeoDeliveryNoExecutionProof
    effective_ads_only_policy_ref: GeoDeliveryArtifactRef
    destination_runtime: DestinationRuntimeContract
    market_alignment_result: MarketDeliveryAlignmentResult
    acceptance_verdicts: GeoDeliveryAcceptanceVerdicts
    acceptance_evidence: GeoDeliveryAcceptanceEvidenceSet
    verification_manifest: GeoDeliveryVerificationManifest
    implementation_versions: dict[str, str] = Field(min_length=5)
    verification_refs: list[str] = Field(min_length=1)
    destination_status: str = Field(min_length=1)
    upload_ready: bool
    publish_execution_ready: bool

    @model_validator(mode="after")
    def enforce_truthful_publish_boundary(self) -> "GeoMarketDeliveryCloseoutEvidence":
        verified = self.destination_runtime.status == "VERIFIED"
        if not verified and (self.upload_ready or self.publish_execution_ready):
            raise ValueError("GEO_CLOSEOUT_PUBLISH_BOUNDARY_MISMATCH")
        if self.destination_status != self.destination_runtime.status:
            raise ValueError("GEO_CLOSEOUT_DESTINATION_STATUS_MISMATCH")
        if self.market_alignment_result.verdict != DeliveryVerdict.PASS:
            raise ValueError("GEO_CLOSEOUT_MARKET_ALIGNMENT_NOT_PASSING")
        verdicts = self.acceptance_verdicts.model_dump(mode="json")
        evidence = {
            item.gate.value: item.verdict.value
            for item in self.acceptance_evidence.results
        }
        if (
            set(evidence) != {gate.value for gate in GEO_DELIVERY_ACCEPTANCE_GATES}
            or set(evidence.values()) != {"PASS"}
            or verdicts != evidence
        ):
            raise ValueError("GEO_CLOSEOUT_ACCEPTANCE_EVIDENCE_MISMATCH")
        runs = {
            item.run_id: item for item in self.verification_manifest.verification_runs
        }
        manifest_gates = {
            item.gate: item for item in self.verification_manifest.gate_results
        }
        acceptance_gates = {
            item.gate: item for item in self.acceptance_evidence.results
        }
        for gate in GEO_DELIVERY_ACCEPTANCE_GATES:
            manifest_gate = manifest_gates[gate]
            acceptance_gate = acceptance_gates[gate]
            expected_refs = {
                (
                    "VERIFICATION_MANIFEST",
                    f"verification-manifest://"
                    f"{self.verification_manifest.content_hash}#{gate.value}",
                    self.verification_manifest.content_hash,
                ),
                *{
                    (
                        "MACHINE_VERIFICATION_RUN",
                        f"verification-run://"
                        f"{self.verification_manifest.content_hash}/{run_id}",
                        runs[run_id].content_hash,
                    )
                    for run_id in manifest_gate.verification_run_ids
                },
            }
            actual_refs = {
                (item.evidence_type, item.ref, item.content_hash)
                for item in acceptance_gate.evidence_refs
            }
            if (
                acceptance_gate.verdict != manifest_gate.verdict
                or acceptance_gate.checks != manifest_gate.checks
                or actual_refs != expected_refs
            ):
                raise ValueError("GEO_CLOSEOUT_MANIFEST_EVIDENCE_MISMATCH")
        expected_verification_refs = sorted(
            {
                f"{item.ref}|sha256:{item.content_hash}"
                for result in self.acceptance_evidence.results
                for item in result.evidence_refs
            }
        )
        if sorted(self.verification_refs) != expected_verification_refs:
            raise ValueError("GEO_CLOSEOUT_VERIFICATION_REFS_MISMATCH")
        return self
