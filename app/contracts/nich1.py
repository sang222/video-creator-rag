from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


NICHE_CONTRACT_DIGEST_VERSION = "nich1.niche-contract-digest.v1"
NICHE_ALIGNMENT_DOSSIER_VERSION = "nich1.niche-alignment-dossier.v1"


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value
        return normalized.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"NICH1_NON_CANONICAL_HASH_VALUE:{type(value).__name__}")


def nich1_stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class NicheGateVerdict(StrEnum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCK = "BLOCK"


class NicheGateKey(StrEnum):
    TOPIC = "topic_niche_alignment_gate"
    SCRIPT = "script_niche_alignment_gate"
    VISUAL = "visual_niche_alignment_gate"
    THUMBNAIL = "thumbnail_niche_alignment_gate"
    METADATA = "metadata_niche_alignment_gate"


class NicheCriterion(StrEnum):
    NICHE_RELEVANCE = "NICHE_RELEVANCE"
    PILLAR_CATEGORY_FIT = "PILLAR_CATEGORY_FIT"
    AUDIENCE_FIT = "AUDIENCE_FIT"
    POSITIONING_FIT = "POSITIONING_FIT"
    BRAND_PROMISE_FIT = "BRAND_PROMISE_FIT"
    ALLOWED_TOPIC_COMPLIANCE = "ALLOWED_TOPIC_COMPLIANCE"
    SERIES_FIT = "SERIES_FIT"
    PRODUCTION_GOAL_FIT = "PRODUCTION_GOAL_FIT"
    TOPIC_FIDELITY = "TOPIC_FIDELITY"
    CLAIM_SCOPE_FIT = "CLAIM_SCOPE_FIT"
    VISUAL_LANGUAGE_FIT = "VISUAL_LANGUAGE_FIT"
    VISUAL_MEANING_FIDELITY = "VISUAL_MEANING_FIDELITY"
    THUMBNAIL_PROMISE_FIDELITY = "THUMBNAIL_PROMISE_FIDELITY"
    METADATA_TOPIC_FIDELITY = "METADATA_TOPIC_FIDELITY"
    CTA_FIT = "CTA_FIT"


class NicheReasonCode(StrEnum):
    # Authority and digest binding.
    NICHE_CONTRACT_DIGEST_MISSING = "NICHE_CONTRACT_DIGEST_MISSING"
    NICHE_CONTRACT_DIGEST_REF_MISMATCH = "NICHE_CONTRACT_DIGEST_REF_MISMATCH"
    NICHE_CONTRACT_DIGEST_HASH_MISMATCH = "NICHE_CONTRACT_DIGEST_HASH_MISMATCH"
    NICHE_CONTRACT_DIGEST_STALE = "NICHE_CONTRACT_DIGEST_STALE"
    NICHE_CONTRACT_REQUIRED_FIELD_MISSING = "NICHE_CONTRACT_REQUIRED_FIELD_MISSING"
    CHANNEL_CONTRACT_INCOMPLETE = "CHANNEL_CONTRACT_INCOMPLETE"
    CHANNEL_SCOPE_MISMATCH = "CHANNEL_SCOPE_MISMATCH"
    PROFILE_SCOPE_MISMATCH = "PROFILE_SCOPE_MISMATCH"
    PROFILE_NOT_ACTIVE_OR_APPROVED = "PROFILE_NOT_ACTIVE_OR_APPROVED"
    PROFILE_HASH_MISMATCH = "PROFILE_HASH_MISMATCH"
    POLICY_SCOPE_MISMATCH = "POLICY_SCOPE_MISMATCH"
    POLICY_SNAPSHOT_NOT_ACTIVE = "POLICY_SNAPSHOT_NOT_ACTIVE"
    POLICY_SNAPSHOT_HASH_MISMATCH = "POLICY_SNAPSHOT_HASH_MISMATCH"

    # Editorial slot and category binding.
    CATEGORY_BINDING_MISSING = "CATEGORY_BINDING_MISSING"
    CATEGORY_SCOPE_MISMATCH = "CATEGORY_SCOPE_MISMATCH"
    CATEGORY_NOT_ACTIVE = "CATEGORY_NOT_ACTIVE"
    CATEGORY_MISMATCH = "CATEGORY_MISMATCH"
    CATEGORY_SUB_NICHE_MISSING = "CATEGORY_SUB_NICHE_MISSING"
    CATEGORY_SUB_NICHE_MISMATCH = "CATEGORY_SUB_NICHE_MISMATCH"
    CONTENT_PILLAR_BINDING_MISSING = "CONTENT_PILLAR_BINDING_MISSING"
    CONTENT_PILLAR_NOT_IN_CHANNEL_CONTRACT = "CONTENT_PILLAR_NOT_IN_CHANNEL_CONTRACT"
    CATEGORY_PILLAR_MISMATCH = "CATEGORY_PILLAR_MISMATCH"
    SERIES_BINDING_MISSING = "SERIES_BINDING_MISSING"
    SERIES_NOT_ALLOWED = "SERIES_NOT_ALLOWED"
    SERIES_CATEGORY_MISMATCH = "SERIES_CATEGORY_MISMATCH"
    SERIES_PILLAR_MISMATCH = "SERIES_PILLAR_MISMATCH"
    PRODUCTION_GOAL_MISSING = "PRODUCTION_GOAL_MISSING"
    PRODUCTION_GOAL_UNSUPPORTED = "PRODUCTION_GOAL_UNSUPPORTED"
    SLOT_SCOPE_MISMATCH = "SLOT_SCOPE_MISMATCH"
    LEGACY_SLOT_STRICT_BINDING_REQUIRED = "LEGACY_SLOT_STRICT_BINDING_REQUIRED"

    # Topic/script semantic policy.
    FORBIDDEN_TOPIC_CONFLICT = "FORBIDDEN_TOPIC_CONFLICT"
    ALLOWED_TOPIC_MISMATCH = "ALLOWED_TOPIC_MISMATCH"
    ADJACENT_NICHE_CONFLICT = "ADJACENT_NICHE_CONFLICT"
    SEMANTIC_EVIDENCE_MISSING = "SEMANTIC_EVIDENCE_MISSING"
    SEMANTIC_ALIGNMENT_REVIEW_REQUIRED = "SEMANTIC_ALIGNMENT_REVIEW_REQUIRED"
    SEMANTIC_ALIGNMENT_BLOCKED = "SEMANTIC_ALIGNMENT_BLOCKED"
    APPROVED_TOPIC_DRIFT = "APPROVED_TOPIC_DRIFT"
    AUDIENCE_PAIN_NOT_SERVED = "AUDIENCE_PAIN_NOT_SERVED"
    AUDIENCE_OUTCOME_NOT_SERVED = "AUDIENCE_OUTCOME_NOT_SERVED"
    CLAIM_SCOPE_MISMATCH = "CLAIM_SCOPE_MISMATCH"
    ARTIFACT_BINDING_MISSING = "ARTIFACT_BINDING_MISSING"
    UPSTREAM_TOPIC_GATE_NOT_PASS = "UPSTREAM_TOPIC_GATE_NOT_PASS"

    # Visual policy.
    VISUAL_PLAN_MISSING = "VISUAL_PLAN_MISSING"
    VISUAL_DIRECTION_CHANNEL_MISMATCH = "VISUAL_DIRECTION_CHANNEL_MISMATCH"
    VISUAL_SOURCE_PROFILE_MISMATCH = "VISUAL_SOURCE_PROFILE_MISMATCH"
    SMALL_TEAM_AI_STOCK_ASSISTED_REQUIRED = "SMALL_TEAM_AI_STOCK_ASSISTED_REQUIRED"
    VISUAL_SCENE_DECISION_MISSING = "VISUAL_SCENE_DECISION_MISSING"
    MECHANISM_MEANING_REPLACED_BY_GENERIC_STOCK = "MECHANISM_MEANING_REPLACED_BY_GENERIC_STOCK"
    AI_IMAGE_EDITORIAL_JUSTIFICATION_MISSING = "AI_IMAGE_EDITORIAL_JUSTIFICATION_MISSING"
    AUTHORIZED_ASSET_REQUIRED_FOR_EVIDENCE = "AUTHORIZED_ASSET_REQUIRED_FOR_EVIDENCE"

    # Thumbnail/metadata policy.
    THUMBNAIL_TOPIC_PROMISE_MISMATCH = "THUMBNAIL_TOPIC_PROMISE_MISMATCH"
    THUMBNAIL_CLAIM_EVIDENCE_MISSING = "THUMBNAIL_CLAIM_EVIDENCE_MISSING"
    THUMBNAIL_MISLEADING_PRODUCT_UI = "THUMBNAIL_MISLEADING_PRODUCT_UI"
    METADATA_TOPIC_MISMATCH = "METADATA_TOPIC_MISMATCH"
    METADATA_CLAIM_EVIDENCE_MISSING = "METADATA_CLAIM_EVIDENCE_MISSING"

    # Channel fit and dossier completeness.
    CHANNEL_FIT_POLICY_THRESHOLD_MISSING = "CHANNEL_FIT_POLICY_THRESHOLD_MISSING"
    CHANNEL_FIT_BELOW_THRESHOLD = "CHANNEL_FIT_BELOW_THRESHOLD"
    CHANNEL_FIT_EVIDENCE_MISSING = "CHANNEL_FIT_EVIDENCE_MISSING"
    CHANNEL_FIT_GATE_BLOCKED = "CHANNEL_FIT_GATE_BLOCKED"
    CHANNEL_FIT_GATE_REVIEW_REQUIRED = "CHANNEL_FIT_GATE_REVIEW_REQUIRED"
    CALLER_POLICY_FIT_STATE_IGNORED = "CALLER_POLICY_FIT_STATE_IGNORED"
    MANDATORY_NICHE_GATE_EVIDENCE_MISSING = "MANDATORY_NICHE_GATE_EVIDENCE_MISSING"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class NicheDossierScope(StrEnum):
    PRE_ADMISSION = "PRE_ADMISSION"
    PRODUCTION_PACKAGE = "PRODUCTION_PACKAGE"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _HashBoundModel(_FrozenModel):
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_content_hash(self) -> "_HashBoundModel":
        expected = nich1_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("NICH1_CONTENT_HASH_MISMATCH")
        return self


class NicheEvidenceRef(_FrozenModel):
    type: str = Field(min_length=1, max_length=100)
    ref: str = Field(min_length=1, max_length=1000)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class NicheDigestRef(_FrozenModel):
    type: Literal["niche_contract_digest"] = "niche_contract_digest"
    ref: str = Field(min_length=1, max_length=1000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class NicheContractDigest(_HashBoundModel):
    digest_version: Literal["nich1.niche-contract-digest.v1"] = (
        NICHE_CONTRACT_DIGEST_VERSION
    )

    channel_id: uuid.UUID
    channel_key: str = Field(min_length=1, max_length=200)
    channel_contract_ref: str = Field(min_length=1, max_length=1000)
    channel_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_profile_version_ref: str = Field(min_length=1, max_length=1000)
    channel_profile_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_policy_snapshot_ref: str = Field(min_length=1, max_length=1000)
    compiled_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    primary_niche: str = Field(min_length=1, max_length=500)
    sub_niche: str = Field(min_length=1, max_length=500)
    positioning: str = Field(min_length=1, max_length=1000)
    brand_promise: str = Field(min_length=1, max_length=1000)

    primary_market: str = Field(min_length=1, max_length=100)
    content_language: str = Field(min_length=1, max_length=100)
    locale: str = Field(min_length=1, max_length=100)

    target_audience: str = Field(min_length=1, max_length=1000)
    audience_segments: list[str] = Field(min_length=1, max_length=32)
    audience_pain_points: list[str] = Field(min_length=1, max_length=64)
    audience_desired_outcomes: list[str] = Field(min_length=1, max_length=64)

    content_pillars: list[str] = Field(min_length=1, max_length=64)
    allowed_topics: list[str] = Field(default_factory=list, max_length=128)
    forbidden_topics: list[str] = Field(default_factory=list, max_length=128)

    category_id: uuid.UUID
    category_ref: str = Field(min_length=1, max_length=1000)
    category_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    category_name: str = Field(min_length=1, max_length=500)
    category_sub_niche: str = Field(min_length=1, max_length=500)
    category_allowed_topics: list[str] = Field(default_factory=list, max_length=128)
    category_forbidden_topics: list[str] = Field(default_factory=list, max_length=128)

    editorial_slot_id: uuid.UUID
    editorial_slot_ref: str = Field(min_length=1, max_length=1000)
    editorial_slot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_pillar_id: str | None = Field(default=None, max_length=500)
    content_pillar_key: str = Field(min_length=1, max_length=500)
    series_key: str = Field(min_length=1, max_length=500)
    production_goal: str = Field(min_length=1, max_length=2000)

    voice_tone_summary: str = Field(min_length=1, max_length=2000)
    format_summary: str = Field(min_length=1, max_length=2000)
    visual_source_profile: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_semantic_bindings(self) -> "NicheContractDigest":
        list_fields = (
            "audience_segments",
            "audience_pain_points",
            "audience_desired_outcomes",
            "content_pillars",
            "allowed_topics",
            "forbidden_topics",
            "category_allowed_topics",
            "category_forbidden_topics",
        )
        for field_name in list_fields:
            values = getattr(self, field_name)
            normalized = [value.strip().casefold() for value in values]
            if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
                raise ValueError(f"NICH1_DUPLICATE_OR_EMPTY_LIST_VALUE:{field_name}")
        pillars = {value.strip().casefold() for value in self.content_pillars}
        if self.content_pillar_key.strip().casefold() not in pillars:
            raise ValueError("NICH1_DIGEST_PILLAR_NOT_IN_CHANNEL_CONTRACT")
        allowed = {value.strip().casefold() for value in self.allowed_topics}
        forbidden = {value.strip().casefold() for value in self.forbidden_topics}
        if allowed & forbidden:
            raise ValueError("NICH1_CHANNEL_ALLOWED_FORBIDDEN_TOPIC_OVERLAP")
        category_allowed = {
            value.strip().casefold() for value in self.category_allowed_topics
        }
        category_forbidden = {
            value.strip().casefold() for value in self.category_forbidden_topics
        }
        if category_allowed & category_forbidden:
            raise ValueError("NICH1_CATEGORY_ALLOWED_FORBIDDEN_TOPIC_OVERLAP")
        return self

    def as_ref(self, ref: str | None = None) -> NicheDigestRef:
        return NicheDigestRef(
            ref=ref or self.editorial_slot_ref + "#niche_contract_digest",
            content_hash=self.content_hash,
        )


class EditorialSlotBinding(_HashBoundModel):
    slot_id: uuid.UUID
    slot_ref: str = Field(min_length=1, max_length=1000)
    company_id: uuid.UUID
    channel_id: uuid.UUID
    active_profile_version_ref: str = Field(min_length=1, max_length=1000)
    active_policy_snapshot_ref: str = Field(min_length=1, max_length=1000)
    active_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    category_id: uuid.UUID
    content_pillar_id: str | None = Field(default=None, max_length=500)
    content_pillar_key: str = Field(min_length=1, max_length=500)
    series_key: str = Field(min_length=1, max_length=500)
    production_goal: str = Field(min_length=1, max_length=2000)


class ContentCategoryBinding(_HashBoundModel):
    category_id: uuid.UUID
    category_ref: str = Field(min_length=1, max_length=1000)
    company_id: uuid.UUID
    channel_id: uuid.UUID
    status: str = Field(min_length=1, max_length=100)
    category_name: str = Field(min_length=1, max_length=500)
    sub_niche: str = Field(min_length=1, max_length=500)
    content_pillar_key: str = Field(min_length=1, max_length=500)
    allowed_topics: list[str] = Field(default_factory=list, max_length=128)
    forbidden_topics: list[str] = Field(default_factory=list, max_length=128)


class EditorialSlotValidationResult(_HashBoundModel):
    verdict: NicheGateVerdict
    production_eligible: bool
    legacy_readable: Literal[True] = True
    strict_production: bool
    reason_codes: list[NicheReasonCode] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    slot_binding: EditorialSlotBinding | None = None
    category_binding: ContentCategoryBinding | None = None

    @model_validator(mode="after")
    def validate_eligibility(self) -> "EditorialSlotValidationResult":
        if self.production_eligible != (self.verdict == NicheGateVerdict.PASS):
            raise ValueError("NICH1_SLOT_ELIGIBILITY_VERDICT_MISMATCH")
        if self.verdict != NicheGateVerdict.PASS and not self.reason_codes:
            raise ValueError("NICH1_SLOT_NON_PASS_REASON_REQUIRED")
        if self.production_eligible and (
            self.slot_binding is None or self.category_binding is None
        ):
            raise ValueError("NICH1_SLOT_PASS_BINDINGS_REQUIRED")
        return self


class NicheCriterionEvidence(_FrozenModel):
    criterion: NicheCriterion
    verdict: NicheGateVerdict
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=2000)
    reason_codes: list[NicheReasonCode] = Field(default_factory=list)
    evidence_refs: list[NicheEvidenceRef] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def require_non_pass_reason(self) -> "NicheCriterionEvidence":
        if self.verdict != NicheGateVerdict.PASS and not self.reason_codes:
            raise ValueError("NICH1_CRITERION_NON_PASS_REASON_REQUIRED")
        return self


class NicheGateCheck(_FrozenModel):
    check_key: str = Field(min_length=1, max_length=200)
    verdict: NicheGateVerdict
    reason_codes: list[NicheReasonCode] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class NicheGateResult(_HashBoundModel):
    gate_key: NicheGateKey
    verdict: NicheGateVerdict
    reason_codes: list[NicheReasonCode] = Field(default_factory=list)
    checks: list[NicheGateCheck] = Field(min_length=1, max_length=64)
    niche_contract_digest_ref: str | None = Field(default=None, max_length=1000)
    niche_contract_digest_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    subject_ref: str = Field(min_length=1, max_length=1000)
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_policy_snapshot_ref: str = Field(min_length=1, max_length=1000)
    checked_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[NicheEvidenceRef] = Field(default_factory=list, max_length=128)
    human_review_required: bool
    summary: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_result(self) -> "NicheGateResult":
        if self.verdict != NicheGateVerdict.PASS and not self.reason_codes:
            raise ValueError("NICH1_GATE_NON_PASS_REASON_REQUIRED")
        if self.human_review_required != (
            self.verdict == NicheGateVerdict.REVIEW_REQUIRED
        ):
            raise ValueError("NICH1_GATE_HUMAN_REVIEW_FLAG_MISMATCH")
        if len({check.check_key for check in self.checks}) != len(self.checks):
            raise ValueError("NICH1_DUPLICATE_GATE_CHECK")
        return self


class _NicheGateInput(_FrozenModel):
    niche_contract_digest: NicheContractDigest | None = None
    niche_contract_digest_ref: str | None = Field(default=None, max_length=1000)
    niche_contract_digest_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    active_policy_snapshot_ref: str = Field(min_length=1, max_length=1000)
    active_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_ref: str = Field(min_length=1, max_length=1000)
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_evidence: list[NicheCriterionEvidence] = Field(
        default_factory=list, max_length=32
    )
    evidence_refs: list[NicheEvidenceRef] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def unique_semantic_criteria(self) -> "_NicheGateInput":
        criteria = [item.criterion for item in self.semantic_evidence]
        if len(criteria) != len(set(criteria)):
            raise ValueError("NICH1_DUPLICATE_SEMANTIC_CRITERION")
        return self


class TopicNicheAlignmentInput(_NicheGateInput):
    channel_id: uuid.UUID
    slot_binding: EditorialSlotBinding | None = None
    category_binding: ContentCategoryBinding | None = None
    topic: str = Field(min_length=1, max_length=2000)
    angle: str | None = Field(default=None, max_length=4000)
    claim_scope: list[str] = Field(default_factory=list, max_length=64)
    adjacent_niche_conflict: bool = False


class ScriptNicheAlignmentInput(_NicheGateInput):
    daily_idea_ref: str = Field(min_length=1, max_length=1000)
    daily_idea_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    topic_gate_ref: str = Field(min_length=1, max_length=1000)
    topic_gate_result: NicheGateResult
    approved_topic: str = Field(min_length=1, max_length=2000)
    script_topic: str = Field(min_length=1, max_length=2000)
    script_text: str = Field(min_length=1, max_length=250_000)
    declared_primary_niche: str = Field(min_length=1, max_length=500)
    declared_sub_niche: str = Field(min_length=1, max_length=500)
    declared_category_id: uuid.UUID
    declared_content_pillar_key: str = Field(min_length=1, max_length=500)
    addressed_audience_pain_points: list[str] = Field(default_factory=list, max_length=64)
    addressed_audience_desired_outcomes: list[str] = Field(
        default_factory=list, max_length=64
    )
    claim_scope: list[str] = Field(default_factory=list, max_length=64)
    adjacent_niche_conflict: bool = False


class VisualNicheAlignmentInput(_NicheGateInput):
    visual_direction_contract: dict[str, Any]
    scene_visual_intents: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    visual_source_decisions: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    content_pillar_key: str = Field(min_length=1, max_length=500)
    category_id: uuid.UUID
    ai_image_editorial_justification_refs: dict[str, str] = Field(default_factory=dict)
    authorized_asset_evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


class ThumbnailNicheAlignmentInput(_NicheGateInput):
    approved_topic: str = Field(min_length=1, max_length=2000)
    thumbnail_promise: str = Field(min_length=1, max_length=2000)
    implied_niche: str = Field(min_length=1, max_length=500)
    visual_language: str = Field(min_length=1, max_length=2000)
    text_claims: list[str] = Field(default_factory=list, max_length=64)
    number_claims: list[str] = Field(default_factory=list, max_length=64)
    claim_evidence_refs: list[NicheEvidenceRef] = Field(default_factory=list, max_length=64)
    misleading_product_or_ui_representation: bool = False


class MetadataNicheAlignmentInput(_NicheGateInput):
    approved_topic: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1, max_length=20_000)
    keywords: list[str] = Field(default_factory=list, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=128)
    chapters: list[str] = Field(default_factory=list, max_length=128)
    summary_copy: str | None = Field(default=None, max_length=10_000)
    upload_card_copy: str | None = Field(default=None, max_length=10_000)
    cta: str | None = Field(default=None, max_length=4000)
    declared_category_id: uuid.UUID
    declared_content_pillar_key: str = Field(min_length=1, max_length=500)
    claim_scope: list[str] = Field(default_factory=list, max_length=64)
    claim_evidence_refs: list[NicheEvidenceRef] = Field(default_factory=list, max_length=64)
    adjacent_niche_conflict: bool = False


class ChannelFitEvaluation(_HashBoundModel):
    channel_fit_score: float = Field(ge=0.0, le=1.0)
    channel_fit_threshold: float = Field(ge=0.0, le=1.0)
    channel_fit_result: NicheGateVerdict
    policy_fit_state: NicheGateVerdict
    reason_codes: list[NicheReasonCode] = Field(default_factory=list)
    evidence_refs: list[NicheEvidenceRef] = Field(default_factory=list, max_length=128)
    required_gate_keys: list[NicheGateKey] = Field(min_length=1)
    gate_result_hashes: dict[NicheGateKey, str] = Field(default_factory=dict)
    caller_policy_fit_state_ignored: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_derived_fit(self) -> "ChannelFitEvaluation":
        if self.policy_fit_state != self.channel_fit_result:
            raise ValueError("NICH1_POLICY_FIT_MUST_BE_DERIVED_FROM_CHANNEL_FIT")
        if (
            self.channel_fit_score < self.channel_fit_threshold
            and self.channel_fit_result == NicheGateVerdict.PASS
        ):
            raise ValueError("NICH1_CHANNEL_FIT_BELOW_THRESHOLD_CANNOT_PASS")
        if self.channel_fit_result == NicheGateVerdict.PASS and not self.evidence_refs:
            raise ValueError("NICH1_CHANNEL_FIT_PASS_EVIDENCE_REQUIRED")
        if self.channel_fit_result != NicheGateVerdict.PASS and not self.reason_codes:
            raise ValueError("NICH1_CHANNEL_FIT_NON_PASS_REASON_REQUIRED")
        if set(self.required_gate_keys) - set(self.gate_result_hashes):
            if self.channel_fit_result != NicheGateVerdict.BLOCK:
                raise ValueError("NICH1_MISSING_GATE_EVIDENCE_MUST_BLOCK")
        return self


class NicheAlignmentDossier(_HashBoundModel):
    dossier_version: Literal["nich1.niche-alignment-dossier.v1"] = (
        NICHE_ALIGNMENT_DOSSIER_VERSION
    )
    dossier_scope: NicheDossierScope
    channel_id: uuid.UUID
    channel_key: str = Field(min_length=1, max_length=200)
    channel_contract_ref: str = Field(min_length=1, max_length=1000)
    channel_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_profile_version_ref: str = Field(min_length=1, max_length=1000)
    compiled_policy_snapshot_ref: str = Field(min_length=1, max_length=1000)
    compiled_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    niche_contract_digest_ref: str = Field(min_length=1, max_length=1000)
    niche_contract_digest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    editorial_slot_ref: str = Field(min_length=1, max_length=1000)
    category_ref: str = Field(min_length=1, max_length=1000)
    content_pillar_id: str | None = Field(default=None, max_length=500)
    content_pillar_key: str = Field(min_length=1, max_length=500)
    series_key: str = Field(min_length=1, max_length=500)

    topic_result: NicheGateResult | None = None
    script_result: NicheGateResult | None = None
    visual_result: NicheGateResult | None = None
    thumbnail_result: NicheGateResult | None = None
    metadata_result: NicheGateResult | None = None

    channel_fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    channel_fit_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    channel_fit_result: NicheGateVerdict | None = None
    completed_gate_keys: list[NicheGateKey] = Field(default_factory=list)
    missing_gate_keys: list[NicheGateKey] = Field(default_factory=list)
    reason_codes: list[NicheReasonCode] = Field(default_factory=list)
    human_review_requirements: list[str] = Field(default_factory=list, max_length=64)
    overall_verdict: NicheGateVerdict

    @model_validator(mode="after")
    def validate_dossier_verdict(self) -> "NicheAlignmentDossier":
        results = [
            result
            for result in (
                self.topic_result,
                self.script_result,
                self.visual_result,
                self.thumbnail_result,
                self.metadata_result,
            )
            if result is not None
        ]
        if any(result.verdict == NicheGateVerdict.BLOCK for result in results):
            if self.overall_verdict != NicheGateVerdict.BLOCK:
                raise ValueError("NICH1_DOSSIER_BLOCKED_COMPONENT_CANNOT_PASS")
        if (
            self.dossier_scope == NicheDossierScope.PRODUCTION_PACKAGE
            and self.missing_gate_keys
            and self.overall_verdict != NicheGateVerdict.BLOCK
        ):
            raise ValueError("NICH1_PRODUCTION_DOSSIER_MISSING_GATE_MUST_BLOCK")
        if (
            self.channel_fit_result == NicheGateVerdict.BLOCK
            and self.overall_verdict != NicheGateVerdict.BLOCK
        ):
            raise ValueError("NICH1_DOSSIER_CHANNEL_FIT_BLOCK_CANNOT_PASS")
        return self


NICHE_GATE_STRICT_ORDER: tuple[NicheGateKey, ...] = (
    NicheGateKey.TOPIC,
    NicheGateKey.SCRIPT,
    NicheGateKey.VISUAL,
    NicheGateKey.THUMBNAIL,
    NicheGateKey.METADATA,
)


__all__ = [
    "ChannelFitEvaluation",
    "ContentCategoryBinding",
    "EditorialSlotBinding",
    "EditorialSlotValidationResult",
    "MetadataNicheAlignmentInput",
    "NICHE_ALIGNMENT_DOSSIER_VERSION",
    "NICHE_CONTRACT_DIGEST_VERSION",
    "NICHE_GATE_STRICT_ORDER",
    "NicheAlignmentDossier",
    "NicheContractDigest",
    "NicheCriterion",
    "NicheCriterionEvidence",
    "NicheDigestRef",
    "NicheDossierScope",
    "NicheEvidenceRef",
    "NicheGateCheck",
    "NicheGateKey",
    "NicheGateResult",
    "NicheGateVerdict",
    "NicheReasonCode",
    "ScriptNicheAlignmentInput",
    "ThumbnailNicheAlignmentInput",
    "TopicNicheAlignmentInput",
    "VisualNicheAlignmentInput",
    "nich1_stable_hash",
]
