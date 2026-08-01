from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


def market_content_hash(value: Any) -> str:
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


class MarketVerdict(StrEnum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCK = "BLOCK"


class MarketReasonCode(StrEnum):
    TARGET_MARKET_PROFILE_MISSING = "TARGET_MARKET_PROFILE_MISSING"
    TARGET_MARKET_PROFILE_STALE = "TARGET_MARKET_PROFILE_STALE"
    MARKET_DEMAND_SCOPE_MISSING = "MARKET_DEMAND_SCOPE_MISSING"
    TOPIC_MARKET_DEMAND_WEAK = "TOPIC_MARKET_DEMAND_WEAK"
    SOURCE_JURISDICTION_MISMATCH = "SOURCE_JURISDICTION_MISMATCH"
    FOREIGN_CONTEXT_NOT_DISCLOSED = "FOREIGN_CONTEXT_NOT_DISCLOSED"
    SCRIPT_MARKET_CONTEXT_MISMATCH = "SCRIPT_MARKET_CONTEXT_MISMATCH"
    VOICE_LOCALE_MISMATCH = "VOICE_LOCALE_MISMATCH"
    VISUAL_MARKET_CONTEXT_MISMATCH = "VISUAL_MARKET_CONTEXT_MISMATCH"
    THUMBNAIL_LOCALE_MISMATCH = "THUMBNAIL_LOCALE_MISMATCH"
    LANGUAGE_METADATA_MISMATCH = "LANGUAGE_METADATA_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    UNITS_POLICY_MISMATCH = "UNITS_POLICY_MISMATCH"
    DATE_FORMAT_MISMATCH = "DATE_FORMAT_MISMATCH"
    TRANSLATED_SOUNDING_LANGUAGE_RISK = "TRANSLATED_SOUNDING_LANGUAGE_RISK"
    PRODUCT_AVAILABILITY_MISMATCH = "PRODUCT_AVAILABILITY_MISMATCH"
    DESTINATION_BINDING_MISSING = "DESTINATION_BINDING_MISSING"
    DESTINATION_BINDING_MISMATCH = "DESTINATION_BINDING_MISMATCH"
    DESTINATION_NOT_VERIFIED = "DESTINATION_NOT_VERIFIED"
    MARKET_ALIGNMENT_EVIDENCE_MISSING = "MARKET_ALIGNMENT_EVIDENCE_MISSING"
    MARKET_PACKAGE_APPROVAL_MISSING = "MARKET_PACKAGE_APPROVAL_MISSING"
    MARKET_PACKAGE_INTEGRITY_MISMATCH = "MARKET_PACKAGE_INTEGRITY_MISMATCH"
    MEDIA_FILE_MISSING = "MEDIA_FILE_MISSING"


class MarketGateKey(StrEnum):
    IDEA_MARKET_PREFLIGHT = "idea_market_preflight"
    TOPIC_MARKET_ALIGNMENT_GATE = "topic_market_alignment_gate"
    RESEARCH_JURISDICTION_GATE = "research_jurisdiction_gate"
    SCRIPT_MARKET_ALIGNMENT_GATE = "script_market_alignment_gate"
    VOICE_LOCALE_ALIGNMENT_GATE = "voice_locale_alignment_gate"
    VISUAL_MARKET_ALIGNMENT_GATE = "visual_market_alignment_gate"
    THUMBNAIL_MARKET_ALIGNMENT_GATE = "thumbnail_market_alignment_gate"
    METADATA_MARKET_ALIGNMENT_GATE = "metadata_market_alignment_gate"


MARKET_GATE_STRICT_ORDER: tuple[MarketGateKey, ...] = (
    MarketGateKey.IDEA_MARKET_PREFLIGHT,
    MarketGateKey.TOPIC_MARKET_ALIGNMENT_GATE,
    MarketGateKey.RESEARCH_JURISDICTION_GATE,
    MarketGateKey.SCRIPT_MARKET_ALIGNMENT_GATE,
    MarketGateKey.VOICE_LOCALE_ALIGNMENT_GATE,
    MarketGateKey.VISUAL_MARKET_ALIGNMENT_GATE,
    MarketGateKey.THUMBNAIL_MARKET_ALIGNMENT_GATE,
    MarketGateKey.METADATA_MARKET_ALIGNMENT_GATE,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class _HashBoundModel(_StrictModel):
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def bind_hash(self) -> "_HashBoundModel":
        expected = market_content_hash(self)
        if self.content_hash is not None and self.content_hash != expected:
            raise ValueError("CONTENT_HASH_MISMATCH")
        object.__setattr__(self, "content_hash", expected)
        return self


class MarketFieldSuggestion(_StrictModel):
    suggested_field: str = Field(min_length=1)
    suggested_value: Any
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    missing_information: list[str] = Field(default_factory=list)
    human_confirmation_required: bool = True


class TargetMarketSemanticFields(_StrictModel):
    primary_market: str = Field(pattern=r"^[A-Z]{2}$")
    primary_geo_cluster: list[str] = Field(min_length=1)
    acceptable_secondary_geos: list[str] = Field(default_factory=list)
    primary_locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    content_language: str = Field(pattern=r"^[a-z]{2,3}$")
    narration_locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    primary_timezone: str = Field(min_length=1)
    spelling_system: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    units_policy: str = Field(min_length=1)
    date_format: str = Field(min_length=1)
    title_locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    thumbnail_text_locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    caption_locales: list[str] = Field(min_length=1)
    audience_market_context: str = Field(min_length=1)
    workplace_context: str = Field(min_length=1)
    source_jurisdiction_policy: str = Field(min_length=1)
    preferred_source_jurisdictions: list[str] = Field(default_factory=list)
    foreign_source_context_required: bool = True
    allowed_market_contexts: list[str] = Field(default_factory=list)
    prohibited_market_mismatches: list[str] = Field(default_factory=list)
    initial_publish_window_hypotheses: list[dict[str, Any]] = Field(
        default_factory=list
    )
    minimum_comparable_videos: int = Field(ge=3)
    video_geo_evaluation_window_days: int = Field(ge=1)
    channel_geo_review_window_days: int = Field(ge=1)
    account_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    target_market: str = Field(pattern=r"^[A-Z]{2}$")
    actual_viewer_geography_state: Literal[
        "UNMEASURED", "MEASUREMENT_PENDING", "MEASURED"
    ] = "UNMEASURED"

    @model_validator(mode="after")
    def validate_market_truth(self) -> "TargetMarketSemanticFields":
        if self.target_market != self.primary_market:
            raise ValueError("TARGET_MARKET_PRIMARY_MARKET_MISMATCH")
        if self.primary_market not in self.primary_geo_cluster:
            raise ValueError("PRIMARY_MARKET_GEO_CLUSTER_MISSING")
        geos = [*self.primary_geo_cluster, *self.acceptable_secondary_geos]
        if any(len(geo) != 2 or geo.upper() != geo for geo in geos):
            raise ValueError("MARKET_GEO_CODE_INVALID")
        locales = [
            self.primary_locale,
            self.narration_locale,
            self.title_locale,
            self.thumbnail_text_locale,
            *self.caption_locales,
        ]
        if any(locale.lower() in {"eu", "europe"} for locale in locales):
            raise ValueError("EUROPE_IS_NOT_A_LOCALE")
        if any(
            not isinstance(locale, str) or len(locale) < 5 or "-" not in locale
            for locale in locales
        ):
            raise ValueError("LOCALE_INVALID")
        try:
            ZoneInfo(self.primary_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("PRIMARY_TIMEZONE_NOT_IANA") from exc
        if self.channel_geo_review_window_days < self.video_geo_evaluation_window_days:
            raise ValueError("CHANNEL_GEO_REVIEW_WINDOW_TOO_SHORT")
        return self


class TargetMarketProfileDraft(TargetMarketSemanticFields, _HashBoundModel):
    schema_version: Literal["geo2.target-market-profile-draft.v1"] = (
        "geo2.target-market-profile-draft.v1"
    )
    draft_id: uuid.UUID
    draft_version: int = Field(ge=1)
    channel_id: uuid.UUID
    channel_key: str = Field(min_length=1)
    channel_name: str = Field(min_length=1)
    channel_purpose: str = Field(min_length=1)
    target_audience_summary: str = Field(min_length=1)
    channel_market_type: Literal["MARKET_NATIVE", "GLOBAL_ENGLISH"]
    proposal_authority: Literal["AGENT_PROPOSAL_ONLY"] = "AGENT_PROPOSAL_ONLY"
    status: Literal[
        "DRAFT", "NEEDS_HUMAN_REVIEW", "SUBMITTED", "APPROVED", "REJECTED"
    ] = "NEEDS_HUMAN_REVIEW"
    suggestions: list[MarketFieldSuggestion] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    human_confirmation_required: bool = True


class TargetMarketProfile(TargetMarketSemanticFields, _HashBoundModel):
    schema_version: Literal["geo1.target-market-profile.v1"] = (
        "geo1.target-market-profile.v1"
    )
    profile_version: int = Field(ge=1)
    channel_id: uuid.UUID
    channel_key: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    approved_draft_ref: str | None = None


class InactiveTargetMarketProfileFixture(TargetMarketSemanticFields):
    key: str = Field(min_length=1)
    fixture_status: Literal["INACTIVE"] = "INACTIVE"
    channel_key: str = Field(min_length=1)
    approval_ref: None = None


class TargetMarketDigest(_HashBoundModel):
    schema_version: Literal["geo1.target-market-digest.v1"] = (
        "geo1.target-market-digest.v1"
    )
    profile_ref: str = Field(min_length=1)
    profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_market: str
    acceptable_secondary_geos: list[str]
    primary_locale: str
    content_language: str
    narration_locale: str
    primary_timezone: str
    currency: str
    units_policy: str
    date_format: str
    spelling_system: str
    audience_market_context: str
    workplace_context: str
    source_jurisdiction_policy: str
    preferred_source_jurisdictions: list[str]
    foreign_source_context_required: bool
    prohibited_market_mismatches: list[str]
    initial_publish_window_hypotheses: list[dict[str, Any]]


class IdeaMarketPreflightResult(_HashBoundModel):
    schema_version: Literal["geo1.idea-market-preflight.v1"] = (
        "geo1.idea-market-preflight.v1"
    )
    editorial_idea_candidate_ref: str
    niche_contract_digest_ref: str
    niche_contract_digest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_market_digest_ref: str
    target_market_digest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    editorial_slot_ref: str
    content_category_ref: str
    target_market: str
    market_scope: list[str]
    market_fit_score: float = Field(ge=0, le=1)
    market_fit_threshold: float = Field(ge=0, le=1)
    decision: MarketVerdict
    reason_codes: list[MarketReasonCode] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    criteria: dict[str, bool] = Field(default_factory=dict)


class ResearchJurisdictionInput(_StrictModel):
    target_market: str
    source_jurisdictions: list[str] = Field(min_length=1)
    claim_jurisdiction: str | None = None
    legal_or_regulatory_claim: bool = False
    jurisdiction_specific_claim: bool = False
    presented_as_target_market_truth: bool = False
    currency: str | None = None
    units_policy: str | None = None
    date_format: str | None = None
    foreign_source_context_disclosed: bool = False
    evidence_sensitive_claim: bool = False
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class TopicMarketAlignmentInput(_StrictModel):
    preflight: IdeaMarketPreflightResult


class ScriptMarketAlignmentInput(_StrictModel):
    language_locale: str
    currencies: list[str] = Field(default_factory=list)
    units_policy: str | None = None
    date_format: str | None = None
    workplace_context: str | None = None
    audience_market_context: str | None = None
    translated_sounding_language_risk: bool = False
    foreign_legal_assumption_without_context: bool = False


class VoiceLocaleAlignmentInput(_StrictModel):
    narration_locale: str
    content_language: str
    voice_profile_locale: str
    pronunciation_policy_ref: str | None = None


class VisualMarketAlignmentInput(_StrictModel):
    market_contexts: list[str] = Field(default_factory=list)
    actual_ui_or_product_jurisdiction: str | None = None
    currencies: list[str] = Field(default_factory=list)
    date_format: str | None = None
    workplace_context: str | None = None
    evidence_authentic: bool = True


class ThumbnailMarketAlignmentInput(_StrictModel):
    text_locale: str
    currencies: list[str] = Field(default_factory=list)
    market_promise: str | None = None
    foreign_market_bait: bool = False


class MetadataMarketAlignmentInput(_StrictModel):
    title_locale: str
    description_locale: str
    original_language: str
    caption_locales: list[str] = Field(default_factory=list)
    keywords_market_scope: list[str] = Field(default_factory=list)
    cta_market_scope: list[str] = Field(default_factory=list)
    product_available_in_target_market: bool = True


class MarketGateResult(_HashBoundModel):
    schema_version: Literal["geo1.market-gate-result.v1"] = "geo1.market-gate-result.v1"
    gate_key: MarketGateKey
    target_market_profile_ref: str
    target_market_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_market_digest_ref: str
    target_market_digest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_ref: str
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: MarketVerdict
    reason_codes: list[MarketReasonCode] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    measurements: dict[str, Any] = Field(default_factory=dict)
    human_review_requirements: list[str] = Field(default_factory=list)
    exact_next_action: str | None = None


class MarketAlignmentDossier(_HashBoundModel):
    schema_version: Literal["geo1.market-alignment-dossier.v1"] = (
        "geo1.market-alignment-dossier.v1"
    )
    target_market_profile_ref: str
    target_market_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_market_digest_ref: str
    target_market_digest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_profile_version_ref: str
    compiled_policy_snapshot_ref: str
    compiled_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_project_ref: str
    video_project_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    niche_alignment_dossier_ref: str
    niche_alignment_dossier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_results: list[MarketGateResult]
    overall_verdict: MarketVerdict
    reason_codes: list[MarketReasonCode] = Field(default_factory=list)
    human_review_requirements: list[str] = Field(default_factory=list)


class MinimalMarketChannelInit(_StrictModel):
    company_id: uuid.UUID
    channel_name: str = Field(min_length=1)
    channel_key: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    channel_purpose: str = Field(min_length=1)
    primary_market: str = Field(pattern=r"^[A-Z]{2}$")
    primary_language: str = Field(pattern=r"^[a-z]{2,3}$")
    primary_locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    target_audience_summary: str = Field(min_length=1)
    channel_market_type: Literal["MARKET_NATIVE", "GLOBAL_ENGLISH"]
    known_destination_channel: str | None = None
    account_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class TargetMarketDraftPatch(_StrictModel):
    expected_draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft: TargetMarketProfileDraft


class TargetMarketDraftApproval(_StrictModel):
    expected_draft_id: uuid.UUID
    expected_draft_version: int = Field(ge=1)
    expected_draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    decision: Literal["APPROVE", "REJECT"] = "APPROVE"
    decided_at: datetime | None = None


class DestinationBinding(_HashBoundModel):
    schema_version: Literal["geo2.destination-binding.v1"] = (
        "geo2.destination-binding.v1"
    )
    binding_version: int = Field(ge=1)
    channel_id: uuid.UUID
    channel_key: str
    platform: Literal["YOUTUBE", "TIKTOK"]
    platform_account_ref: str | None = None
    platform_channel_id: str | None = None
    channel_handle: str | None = None
    account_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    target_market_profile_ref: str
    target_market_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_market: str = Field(pattern=r"^[A-Z]{2}$")
    primary_market: str = Field(pattern=r"^[A-Z]{2}$")
    primary_locale: str
    original_language: str
    default_visibility: Literal["PRIVATE", "UNLISTED", "PUBLIC", "SCHEDULED"] = (
        "PRIVATE"
    )
    manual_publish_required: Literal[True] = True
    destination_status: Literal[
        "DRAFT", "PENDING_VERIFICATION", "PENDING_PLATFORM_ID", "VERIFIED", "REVOKED"
    ]
    credential_ref: str | None = None
    verification_state: Literal["NOT_VERIFIED", "PENDING", "VERIFIED", "FAILED"]
    verification_timestamp: datetime | None = None
    approval_ref: str

    @model_validator(mode="after")
    def validate_destination(self) -> "DestinationBinding":
        if self.target_market != self.primary_market:
            raise ValueError("DESTINATION_TARGET_MARKET_MISMATCH")
        if self.destination_status == "VERIFIED":
            if (
                self.verification_state != "VERIFIED"
                or self.verification_timestamp is None
                or not self.platform_channel_id
                or not self.credential_ref
            ):
                raise ValueError("DESTINATION_VERIFIED_EVIDENCE_INCOMPLETE")
        if (
            self.verification_state == "VERIFIED"
            and self.destination_status != "VERIFIED"
        ):
            raise ValueError("DESTINATION_STATUS_VERIFICATION_MISMATCH")
        return self


class PublishRiskMarketAlignment(_HashBoundModel):
    schema_version: Literal["geo2.publish-risk-market-alignment.v1"] = (
        "geo2.publish-risk-market-alignment.v1"
    )
    target_market_profile_ref: str
    target_market_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_market: str
    destination_binding_ref: str
    destination_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_language_match: bool
    narration_locale_match: bool
    title_locale_match: bool
    thumbnail_locale_match: bool
    caption_language_match: bool
    currency_units_match: bool
    cultural_context_match: bool
    source_jurisdiction_match: bool
    topic_market_demand_match: bool
    publish_window_status: Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
    localized_asset_requirements: list[str] = Field(default_factory=list)
    overall_decision: MarketVerdict
    reason_codes: list[MarketReasonCode] = Field(default_factory=list)


class MarketBoundPublishPackage(_HashBoundModel):
    schema_version: Literal["geo2.market-bound-publish-package.v1"] = (
        "geo2.market-bound-publish-package.v1"
    )
    package_id: str
    package_version: int = Field(ge=1)
    video_project_ref: str
    media_file_ref: str | None = None
    media_file_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    destination_binding_ref: str
    destination_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_market_profile_ref: str
    target_market_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_market: str
    primary_locale: str
    original_language: str
    caption_refs: list[dict[str, Any]] = Field(default_factory=list)
    localized_metadata_refs: list[dict[str, Any]] = Field(default_factory=list)
    thumbnail_refs: list[dict[str, Any]] = Field(default_factory=list)
    title: str
    description: str
    disclosures: list[str] = Field(default_factory=list)
    approved_publish_timezone: str
    approved_publish_window: dict[str, Any]
    market_alignment_dossier_ref: str
    market_alignment_dossier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    publish_risk_dossier_ref: str
    publish_risk_dossier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    technical_media_qc: Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
    creative_human_review: Literal["PASS", "PENDING", "REJECTED"]
    market_alignment_verdict: MarketVerdict
    publish_risk_verdict: MarketVerdict
    destination_status: str
    package_state: Literal[
        "DRAFT", "READY_FOR_APPROVAL", "MARKET_PACKAGE_FROZEN", "INVALIDATED", "BLOCKED"
    ] = "DRAFT"
    approved_package_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approval_ref: str | None = None


class MarketPackageApproval(_StrictModel):
    expected_package_id: str
    expected_package_version: int = Field(ge=1)
    expected_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_destination_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_market_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str
    approval_ref: str
    decided_at: datetime | None = None


class MarketPackageIntegrityResult(_StrictModel):
    verdict: MarketVerdict
    reason_codes: list[MarketReasonCode] = Field(default_factory=list)
    approved_package_hash: str | None = None
    current_package_hash: str
    exact_next_action: str
