from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.contracts.channel import ChannelWorkspaceCreate
from app.contracts.geo_market import (
    MARKET_GATE_STRICT_ORDER,
    DestinationBinding,
    IdeaMarketPreflightResult,
    MarketAlignmentDossier,
    MarketBoundPublishPackage,
    MarketFieldSuggestion,
    MarketGateKey,
    MarketGateResult,
    MarketPackageApproval,
    MarketPackageIntegrityResult,
    MarketReasonCode,
    MarketVerdict,
    MetadataMarketAlignmentInput,
    MinimalMarketChannelInit,
    PublishRiskMarketAlignment,
    ResearchJurisdictionInput,
    ScriptMarketAlignmentInput,
    TargetMarketDigest,
    TargetMarketDraftApproval,
    TargetMarketProfile,
    TargetMarketProfileDraft,
    ThumbnailMarketAlignmentInput,
    TopicMarketAlignmentInput,
    VisualMarketAlignmentInput,
    VoiceLocaleAlignmentInput,
    market_content_hash,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import ChannelWorkspace, VideoProject
from app.services.channel_workspace import ChannelWorkspaceService


TARGET_MARKET_METADATA_KEY = "target_market_governance"
DESTINATION_METADATA_KEY = "destination_governance"


def target_market_profile_ref(profile: TargetMarketProfile) -> str:
    return (
        f"channel://{profile.channel_key}/target-market-profile/"
        f"v{profile.profile_version}"
    )


def target_market_digest_ref(profile: TargetMarketProfile) -> str:
    return f"{target_market_profile_ref(profile)}/digest"


class TargetMarketDigestCompiler:
    VERSION = "geo1.target-market-digest-compiler.v1"

    def compile(self, profile: TargetMarketProfile) -> TargetMarketDigest:
        if not profile.approval_ref:
            raise ValidationFailureError("TARGET_MARKET_PROFILE_NOT_APPROVED")
        return TargetMarketDigest(
            profile_ref=target_market_profile_ref(profile),
            profile_hash=str(profile.content_hash),
            primary_market=profile.primary_market,
            acceptable_secondary_geos=profile.acceptable_secondary_geos,
            primary_locale=profile.primary_locale,
            content_language=profile.content_language,
            narration_locale=profile.narration_locale,
            primary_timezone=profile.primary_timezone,
            currency=profile.currency,
            units_policy=profile.units_policy,
            date_format=profile.date_format,
            spelling_system=profile.spelling_system,
            audience_market_context=profile.audience_market_context,
            workplace_context=profile.workplace_context,
            source_jurisdiction_policy=profile.source_jurisdiction_policy,
            preferred_source_jurisdictions=profile.preferred_source_jurisdictions,
            foreign_source_context_required=profile.foreign_source_context_required,
            prohibited_market_mismatches=profile.prohibited_market_mismatches,
            initial_publish_window_hypotheses=profile.initial_publish_window_hypotheses,
        )


class IdeaMarketPreflightEvaluator:
    VERSION = "geo1.idea-market-preflight-evaluator.v1"

    def evaluate(
        self,
        *,
        editorial_idea_candidate_ref: str,
        niche_contract_digest_ref: str,
        niche_contract_digest_hash: str,
        target_market_digest: TargetMarketDigest,
        editorial_slot_ref: str,
        content_category_ref: str,
        market_scope: list[str],
        criteria: dict[str, bool],
        evidence_refs: list[dict[str, Any]],
        threshold: float = 0.75,
    ) -> IdeaMarketPreflightResult:
        required = {
            "topic_demand_market_scope",
            "target_audience_fit",
            "terminology_fit",
            "tool_product_availability",
            "business_context_fit",
            "monetization_fit",
            "source_availability",
            "local_relevance",
        }
        normalized_scope = sorted({item.upper() for item in market_scope})
        missing_criteria = required - set(criteria)
        target_scoped = target_market_digest.primary_market in normalized_scope
        global_only = "GLOBAL" in normalized_scope and not target_scoped
        score = (
            sum(1 for key in required if criteria.get(key) is True) / len(required)
            if not missing_criteria
            else 0.0
        )
        reasons: list[MarketReasonCode] = []
        if not normalized_scope or global_only:
            reasons.append(MarketReasonCode.MARKET_DEMAND_SCOPE_MISSING)
        if score < threshold:
            reasons.append(MarketReasonCode.TOPIC_MARKET_DEMAND_WEAK)
        if missing_criteria or not evidence_refs or global_only:
            verdict = MarketVerdict.REVIEW_REQUIRED
        elif not target_scoped or score < max(0.5, threshold - 0.25):
            verdict = MarketVerdict.BLOCK
        elif score < threshold:
            verdict = MarketVerdict.REVIEW_REQUIRED
        else:
            verdict = MarketVerdict.PASS
        return IdeaMarketPreflightResult(
            editorial_idea_candidate_ref=editorial_idea_candidate_ref,
            niche_contract_digest_ref=niche_contract_digest_ref,
            niche_contract_digest_hash=niche_contract_digest_hash,
            target_market_digest_ref=target_market_digest_ref_from_digest(
                target_market_digest
            ),
            target_market_digest_hash=str(target_market_digest.content_hash),
            editorial_slot_ref=editorial_slot_ref,
            content_category_ref=content_category_ref,
            target_market=target_market_digest.primary_market,
            market_scope=normalized_scope,
            market_fit_score=round(score, 6),
            market_fit_threshold=threshold,
            decision=verdict,
            reason_codes=sorted(set(reasons), key=str),
            evidence_refs=evidence_refs,
            criteria={key: bool(criteria.get(key)) for key in sorted(required)},
        )


def target_market_digest_ref_from_digest(digest: TargetMarketDigest) -> str:
    return f"{digest.profile_ref}/digest"


class _MarketGate:
    gate_key: MarketGateKey

    def _result(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        subject: Any,
        subject_ref: str,
        verdict: MarketVerdict,
        reason_codes: list[MarketReasonCode],
        evidence_refs: list[dict[str, Any]] | None = None,
        measurements: dict[str, Any] | None = None,
        human_review_requirements: list[str] | None = None,
        exact_next_action: str | None = None,
    ) -> MarketGateResult:
        if digest.profile_hash != profile.content_hash:
            raise ValidationFailureError("TARGET_MARKET_PROFILE_STALE")
        return MarketGateResult(
            gate_key=self.gate_key,
            target_market_profile_ref=target_market_profile_ref(profile),
            target_market_profile_hash=str(profile.content_hash),
            target_market_digest_ref=target_market_digest_ref(profile),
            target_market_digest_hash=str(digest.content_hash),
            subject_ref=subject_ref,
            subject_hash=market_content_hash(subject),
            verdict=verdict,
            reason_codes=sorted(set(reason_codes), key=str),
            evidence_refs=evidence_refs or [],
            measurements=measurements or {},
            human_review_requirements=human_review_requirements or [],
            exact_next_action=exact_next_action,
        )


class TopicMarketAlignmentGate(_MarketGate):
    gate_key = MarketGateKey.TOPIC_MARKET_ALIGNMENT_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: TopicMarketAlignmentInput,
        subject_ref: str = "idea-market-preflight",
    ) -> MarketGateResult:
        preflight = data.preflight
        reasons = list(preflight.reason_codes)
        if preflight.target_market != profile.primary_market:
            reasons.append(MarketReasonCode.MARKET_DEMAND_SCOPE_MISSING)
            verdict = MarketVerdict.BLOCK
        else:
            verdict = preflight.decision
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=verdict,
            reason_codes=reasons,
            evidence_refs=preflight.evidence_refs,
            measurements={
                "market_fit_score": preflight.market_fit_score,
                "market_fit_threshold": preflight.market_fit_threshold,
                "market_scope": preflight.market_scope,
            },
            exact_next_action=(
                None
                if verdict == MarketVerdict.PASS
                else "Bổ sung bằng chứng nhu cầu trong đúng target market."
            ),
        )


class ResearchJurisdictionGate(_MarketGate):
    gate_key = MarketGateKey.RESEARCH_JURISDICTION_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: ResearchJurisdictionInput,
        subject_ref: str = "research-pack",
    ) -> MarketGateResult:
        reasons: list[MarketReasonCode] = []
        sources = {value.upper() for value in data.source_jurisdictions}
        foreign = any(value != profile.primary_market for value in sources)
        mismatched_specific_claim = bool(
            data.jurisdiction_specific_claim
            and data.claim_jurisdiction
            and data.claim_jurisdiction.upper() not in sources
        )
        if (
            data.presented_as_target_market_truth
            and data.legal_or_regulatory_claim
            and profile.primary_market not in sources
        ) or mismatched_specific_claim:
            reasons.append(MarketReasonCode.SOURCE_JURISDICTION_MISMATCH)
        if (
            foreign
            and profile.foreign_source_context_required
            and not data.foreign_source_context_disclosed
        ):
            reasons.append(MarketReasonCode.FOREIGN_CONTEXT_NOT_DISCLOSED)
        if data.evidence_sensitive_claim and not data.claim_jurisdiction:
            reasons.append(MarketReasonCode.SOURCE_JURISDICTION_MISMATCH)
        if data.currency and data.currency != profile.currency:
            reasons.append(MarketReasonCode.CURRENCY_MISMATCH)
        if data.units_policy and data.units_policy != profile.units_policy:
            reasons.append(MarketReasonCode.UNITS_POLICY_MISMATCH)
        if data.date_format and data.date_format != profile.date_format:
            reasons.append(MarketReasonCode.DATE_FORMAT_MISMATCH)
        verdict = MarketVerdict.BLOCK if reasons else MarketVerdict.PASS
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=verdict,
            reason_codes=reasons,
            evidence_refs=data.evidence_refs,
            measurements={
                "source_jurisdictions": sorted(sources),
                "claim_jurisdiction": data.claim_jurisdiction,
                "foreign_context_disclosed": data.foreign_source_context_disclosed,
            },
            exact_next_action=(
                None
                if verdict == MarketVerdict.PASS
                else "Sửa claim jurisdiction hoặc thêm foreign-source context rõ ràng."
            ),
        )


class ScriptMarketAlignmentGate(_MarketGate):
    gate_key = MarketGateKey.SCRIPT_MARKET_ALIGNMENT_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: ScriptMarketAlignmentInput,
        subject_ref: str = "script-contract-digest",
    ) -> MarketGateResult:
        reasons: list[MarketReasonCode] = []
        if data.language_locale != profile.primary_locale:
            reasons.append(MarketReasonCode.SCRIPT_MARKET_CONTEXT_MISMATCH)
        if any(value != profile.currency for value in data.currencies):
            reasons.append(MarketReasonCode.CURRENCY_MISMATCH)
        if data.units_policy and data.units_policy != profile.units_policy:
            reasons.append(MarketReasonCode.UNITS_POLICY_MISMATCH)
        if data.date_format and data.date_format != profile.date_format:
            reasons.append(MarketReasonCode.DATE_FORMAT_MISMATCH)
        if (
            data.workplace_context
            and data.workplace_context != profile.workplace_context
        ):
            reasons.append(MarketReasonCode.SCRIPT_MARKET_CONTEXT_MISMATCH)
        if (
            data.audience_market_context
            and data.audience_market_context != profile.audience_market_context
        ):
            reasons.append(MarketReasonCode.SCRIPT_MARKET_CONTEXT_MISMATCH)
        if data.foreign_legal_assumption_without_context:
            reasons.append(MarketReasonCode.SCRIPT_MARKET_CONTEXT_MISMATCH)
        if data.translated_sounding_language_risk:
            reasons.append(MarketReasonCode.TRANSLATED_SOUNDING_LANGUAGE_RISK)
        hard = [
            reason
            for reason in reasons
            if reason != MarketReasonCode.TRANSLATED_SOUNDING_LANGUAGE_RISK
        ]
        verdict = (
            MarketVerdict.BLOCK
            if hard
            else MarketVerdict.REVIEW_REQUIRED
            if reasons
            else MarketVerdict.PASS
        )
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=verdict,
            reason_codes=reasons,
            measurements=data.model_dump(mode="json"),
        )


class VoiceLocaleAlignmentGate(_MarketGate):
    gate_key = MarketGateKey.VOICE_LOCALE_ALIGNMENT_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: VoiceLocaleAlignmentInput,
        subject_ref: str = "voice-contract",
    ) -> MarketGateResult:
        mismatch = bool(
            data.narration_locale != profile.narration_locale
            or data.voice_profile_locale != profile.narration_locale
            or data.content_language != profile.content_language
        )
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=MarketVerdict.BLOCK if mismatch else MarketVerdict.PASS,
            reason_codes=[MarketReasonCode.VOICE_LOCALE_MISMATCH] if mismatch else [],
            measurements=data.model_dump(mode="json"),
        )


class VisualMarketAlignmentGate(_MarketGate):
    gate_key = MarketGateKey.VISUAL_MARKET_ALIGNMENT_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: VisualMarketAlignmentInput,
        subject_ref: str = "visual-direction-contract",
    ) -> MarketGateResult:
        reasons: list[MarketReasonCode] = []
        allowed = {profile.primary_market, *profile.allowed_market_contexts}
        if data.market_contexts and any(
            item not in allowed for item in data.market_contexts
        ):
            reasons.append(MarketReasonCode.VISUAL_MARKET_CONTEXT_MISMATCH)
        if (
            data.actual_ui_or_product_jurisdiction
            and data.actual_ui_or_product_jurisdiction not in allowed
        ):
            reasons.append(MarketReasonCode.VISUAL_MARKET_CONTEXT_MISMATCH)
        if any(value != profile.currency for value in data.currencies):
            reasons.append(MarketReasonCode.CURRENCY_MISMATCH)
        if data.date_format and data.date_format != profile.date_format:
            reasons.append(MarketReasonCode.DATE_FORMAT_MISMATCH)
        if (
            data.workplace_context
            and data.workplace_context != profile.workplace_context
        ):
            reasons.append(MarketReasonCode.VISUAL_MARKET_CONTEXT_MISMATCH)
        if not data.evidence_authentic:
            reasons.append(MarketReasonCode.VISUAL_MARKET_CONTEXT_MISMATCH)
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=MarketVerdict.BLOCK if reasons else MarketVerdict.PASS,
            reason_codes=reasons,
            measurements=data.model_dump(mode="json"),
        )


class ThumbnailMarketAlignmentGate(_MarketGate):
    gate_key = MarketGateKey.THUMBNAIL_MARKET_ALIGNMENT_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: ThumbnailMarketAlignmentInput,
        subject_ref: str = "thumbnail-brief",
    ) -> MarketGateResult:
        reasons: list[MarketReasonCode] = []
        if (
            data.text_locale != profile.thumbnail_text_locale
            or data.foreign_market_bait
        ):
            reasons.append(MarketReasonCode.THUMBNAIL_LOCALE_MISMATCH)
        if any(value != profile.currency for value in data.currencies):
            reasons.append(MarketReasonCode.CURRENCY_MISMATCH)
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=MarketVerdict.BLOCK if reasons else MarketVerdict.PASS,
            reason_codes=reasons,
            measurements=data.model_dump(mode="json"),
        )


class MetadataMarketAlignmentGate(_MarketGate):
    gate_key = MarketGateKey.METADATA_MARKET_ALIGNMENT_GATE

    def evaluate(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        data: MetadataMarketAlignmentInput,
        subject_ref: str = "metadata-package",
    ) -> MarketGateResult:
        reasons: list[MarketReasonCode] = []
        if (
            data.title_locale != profile.title_locale
            or data.description_locale != profile.primary_locale
            or data.original_language != profile.content_language
            or not set(profile.caption_locales).issubset(data.caption_locales)
        ):
            reasons.append(MarketReasonCode.LANGUAGE_METADATA_MISMATCH)
        if not data.product_available_in_target_market:
            reasons.append(MarketReasonCode.PRODUCT_AVAILABILITY_MISMATCH)
        return self._result(
            profile=profile,
            digest=digest,
            subject=data,
            subject_ref=subject_ref,
            verdict=MarketVerdict.BLOCK if reasons else MarketVerdict.PASS,
            reason_codes=reasons,
            measurements=data.model_dump(mode="json"),
        )


class TargetMarketAlignmentGateRegistry:
    def __init__(self) -> None:
        self._gates: dict[MarketGateKey, Any] = {
            MarketGateKey.TOPIC_MARKET_ALIGNMENT_GATE: TopicMarketAlignmentGate(),
            MarketGateKey.RESEARCH_JURISDICTION_GATE: ResearchJurisdictionGate(),
            MarketGateKey.SCRIPT_MARKET_ALIGNMENT_GATE: ScriptMarketAlignmentGate(),
            MarketGateKey.VOICE_LOCALE_ALIGNMENT_GATE: VoiceLocaleAlignmentGate(),
            MarketGateKey.VISUAL_MARKET_ALIGNMENT_GATE: VisualMarketAlignmentGate(),
            MarketGateKey.THUMBNAIL_MARKET_ALIGNMENT_GATE: ThumbnailMarketAlignmentGate(),
            MarketGateKey.METADATA_MARKET_ALIGNMENT_GATE: MetadataMarketAlignmentGate(),
        }

    @property
    def strict_order(self) -> tuple[MarketGateKey, ...]:
        return MARKET_GATE_STRICT_ORDER

    @property
    def registered_keys(self) -> tuple[MarketGateKey, ...]:
        return tuple(key for key in MARKET_GATE_STRICT_ORDER if key in self._gates)

    def get(self, key: MarketGateKey) -> Any:
        if key == MarketGateKey.IDEA_MARKET_PREFLIGHT:
            return IdeaMarketPreflightEvaluator()
        try:
            return self._gates[key]
        except KeyError as exc:
            raise ValidationFailureError(f"MARKET_GATE_NOT_REGISTERED:{key}") from exc


class MarketAlignmentDossierBuilder:
    def build(
        self,
        *,
        profile: TargetMarketProfile,
        digest: TargetMarketDigest,
        channel_profile_version_ref: str,
        compiled_policy_snapshot_ref: str,
        compiled_policy_snapshot_hash: str,
        video_project_ref: str,
        video_project_hash: str,
        niche_alignment_dossier_ref: str,
        niche_alignment_dossier_hash: str,
        component_results: list[MarketGateResult],
    ) -> MarketAlignmentDossier:
        expected = set(MARKET_GATE_STRICT_ORDER[1:])
        actual = {result.gate_key for result in component_results}
        missing = expected - actual
        reasons = [
            reason for result in component_results for reason in result.reason_codes
        ]
        human = [
            item
            for result in component_results
            for item in result.human_review_requirements
        ]
        if missing:
            reasons.append(MarketReasonCode.MARKET_ALIGNMENT_EVIDENCE_MISSING)
            overall = MarketVerdict.BLOCK
        elif any(result.verdict == MarketVerdict.BLOCK for result in component_results):
            overall = MarketVerdict.BLOCK
        elif any(
            result.verdict == MarketVerdict.REVIEW_REQUIRED
            for result in component_results
        ):
            overall = MarketVerdict.REVIEW_REQUIRED
        else:
            overall = MarketVerdict.PASS
        return MarketAlignmentDossier(
            target_market_profile_ref=target_market_profile_ref(profile),
            target_market_profile_hash=str(profile.content_hash),
            target_market_digest_ref=target_market_digest_ref(profile),
            target_market_digest_hash=str(digest.content_hash),
            channel_profile_version_ref=channel_profile_version_ref,
            compiled_policy_snapshot_ref=compiled_policy_snapshot_ref,
            compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
            video_project_ref=video_project_ref,
            video_project_hash=video_project_hash,
            niche_alignment_dossier_ref=niche_alignment_dossier_ref,
            niche_alignment_dossier_hash=niche_alignment_dossier_hash,
            component_results=component_results,
            overall_verdict=overall,
            reason_codes=sorted(set(reasons), key=str),
            human_review_requirements=sorted(set(human)),
        )


class MarketResearchRouter(Protocol):
    def propose(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class OfflineMarketResearchRouter:
    """Deterministic GEO2 fixture router; it performs no provider or network call."""

    provider_calls = 0

    def propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        market = str(payload["primary_market"])
        if market == "US":
            return {
                "secondary_geos": ["CA", "GB", "AU"],
                "narration_locale": "en-US",
                "timezone": "America/New_York",
                "currency": "USD",
                "units_policy": "US_WITH_METRIC_WHEN_RELEVANT",
                "spelling_system": "US",
                "date_format": "MMM D, YYYY",
                "audience_market_context": "US_SMALL_BUSINESS",
                "workplace_context": "US_SMALL_BUSINESS",
                "preferred_source_jurisdictions": ["US"],
                "source_jurisdiction_policy": "TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED",
                "prohibited_market_mismatches": [
                    "TRANSLATED_SOUNDING_ENGLISH",
                    "NON_US_CURRENCY_WITHOUT_USD_EQUIVALENT",
                    "FOREIGN_LEGAL_ASSUMPTION_WITHOUT_CONTEXT",
                    "WRONG_VOICE_LOCALE",
                    "WRONG_METADATA_LOCALE",
                    "WRONG_THUMBNAIL_LOCALE",
                ],
                "publish_window_hypotheses": [
                    {
                        "timezone": "America/New_York",
                        "days": ["TUE", "THU"],
                        "local_time": "10:00",
                        "status": "HYPOTHESIS_ONLY",
                    }
                ],
                "market_terminology_notes": ["small business", "team", "workflow"],
                "confidence": 0.9,
            }
        return {
            "secondary_geos": [],
            "narration_locale": payload["primary_locale"],
            "timezone": "UTC",
            "currency": "USD",
            "units_policy": "EXPLICIT_CONTEXT_REQUIRED",
            "spelling_system": "LOCALE_NATIVE",
            "date_format": "YYYY-MM-DD",
            "audience_market_context": "HUMAN_CONFIRMATION_REQUIRED",
            "workplace_context": "HUMAN_CONFIRMATION_REQUIRED",
            "preferred_source_jurisdictions": [market],
            "source_jurisdiction_policy": "TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED",
            "prohibited_market_mismatches": [],
            "publish_window_hypotheses": [],
            "market_terminology_notes": [],
            "confidence": 0.4,
        }


class MarketChannelGovernanceService:
    def __init__(
        self,
        session: Session,
        *,
        router: MarketResearchRouter | None = None,
    ) -> None:
        self.session = session
        self.router = router or OfflineMarketResearchRouter()

    def create_minimal_channel(
        self, data: MinimalMarketChannelInit
    ) -> ChannelWorkspace:
        metadata = {
            TARGET_MARKET_METADATA_KEY: {
                "minimal_input": data.model_dump(mode="json"),
                "drafts": [],
                "profiles": [],
                "digests": [],
                "approvals": [],
                "active_profile_ref": None,
                "state": "RESEARCH_DRAFT_REQUIRED",
            },
            DESTINATION_METADATA_KEY: {"bindings": [], "active_binding_ref": None},
            "organic_geo_truth": {
                "per_video_target_country_supported": False,
                "guaranteed_country_delivery": False,
                "account_country_is_target_market": False,
                "actual_viewer_geography_state": "UNMEASURED",
            },
        }
        return ChannelWorkspaceService(self.session).create_channel(
            company_id=data.company_id,
            data=ChannelWorkspaceCreate(
                key=data.channel_key,
                name=data.channel_name,
                primary_language=data.primary_language,
                primary_region=data.primary_market,
                primary_timezone="UTC",
                target_market=data.primary_market,
                default_timezone="UTC",
                metadata=metadata,
            ),
            correlation_id="geo2-minimal-channel-init",
        )

    def run_market_research_draft(
        self, channel_id: uuid.UUID
    ) -> TargetMarketProfileDraft:
        channel = self._channel(channel_id)
        governance = self._market_metadata(channel)
        for item in reversed(governance["drafts"]):
            if item.get("status") in {"DRAFT", "NEEDS_HUMAN_REVIEW", "SUBMITTED"}:
                return TargetMarketProfileDraft.model_validate(item)
        minimal = governance.get("minimal_input") or {}
        if not minimal:
            raise ValidationFailureError("MINIMAL_CHANNEL_INIT_REQUIRED")
        proposed = self.router.propose(minimal)
        confidence = float(proposed.get("confidence", 0.5))
        evidence = [
            {
                "ref": "offline-fixture://geo2/market-research/us-small-team-ai/v1",
                "kind": "OFFLINE_POLICY_FIXTURE",
            }
        ]
        suggestion_values = {
            "acceptable_secondary_geos": proposed.get("secondary_geos", []),
            "narration_locale": proposed["narration_locale"],
            "primary_timezone": proposed["timezone"],
            "currency": proposed["currency"],
            "units_policy": proposed["units_policy"],
            "spelling_system": proposed["spelling_system"],
            "date_format": proposed["date_format"],
            "audience_market_context": proposed["audience_market_context"],
            "workplace_context": proposed["workplace_context"],
            "source_jurisdiction_policy": proposed["source_jurisdiction_policy"],
            "prohibited_market_mismatches": proposed["prohibited_market_mismatches"],
            "initial_publish_window_hypotheses": proposed["publish_window_hypotheses"],
            "market_terminology_notes": proposed.get("market_terminology_notes", []),
        }
        suggestions = [
            MarketFieldSuggestion(
                suggested_field=field,
                suggested_value=value,
                confidence=confidence,
                evidence_refs=evidence,
                rationale=f"Offline market policy suggestion for {minimal['primary_market']}.",
                missing_information=[]
                if confidence >= 0.8
                else ["operator_confirmation"],
            )
            for field, value in suggestion_values.items()
        ]
        market = minimal["primary_market"]
        locale = minimal["primary_locale"]
        profile_draft = TargetMarketProfileDraft(
            draft_id=uuid.uuid4(),
            draft_version=len(governance["drafts"]) + 1,
            channel_id=channel.id,
            channel_key=channel.key,
            channel_name=channel.name,
            channel_purpose=minimal["channel_purpose"],
            target_audience_summary=minimal["target_audience_summary"],
            channel_market_type=minimal["channel_market_type"],
            primary_market=market,
            primary_geo_cluster=[market],
            acceptable_secondary_geos=proposed.get("secondary_geos", []),
            primary_locale=locale,
            content_language=minimal["primary_language"],
            narration_locale=proposed["narration_locale"],
            primary_timezone=proposed["timezone"],
            spelling_system=proposed["spelling_system"],
            currency=proposed["currency"],
            units_policy=proposed["units_policy"],
            date_format=proposed["date_format"],
            title_locale=locale,
            thumbnail_text_locale=locale,
            caption_locales=[locale],
            audience_market_context=proposed["audience_market_context"],
            workplace_context=proposed["workplace_context"],
            source_jurisdiction_policy=proposed["source_jurisdiction_policy"],
            preferred_source_jurisdictions=proposed["preferred_source_jurisdictions"],
            foreign_source_context_required=True,
            allowed_market_contexts=[market, *proposed.get("secondary_geos", [])],
            prohibited_market_mismatches=proposed["prohibited_market_mismatches"],
            initial_publish_window_hypotheses=proposed["publish_window_hypotheses"],
            minimum_comparable_videos=3,
            video_geo_evaluation_window_days=7,
            channel_geo_review_window_days=30,
            account_country=minimal.get("account_country"),
            target_market=market,
            actual_viewer_geography_state="UNMEASURED",
            suggestions=suggestions,
            missing_information=[] if confidence >= 0.8 else ["operator_confirmation"],
        )
        governance["drafts"].append(profile_draft.model_dump(mode="json"))
        governance["state"] = "DRAFT_NEEDS_HUMAN_REVIEW"
        self._store_market_metadata(channel, governance)
        return profile_draft

    def get_market_draft(self, channel_id: uuid.UUID) -> TargetMarketProfileDraft:
        channel = self._channel(channel_id)
        drafts = self._market_metadata(channel)["drafts"]
        if not drafts:
            raise NotFoundError(f"target market draft not found: {channel_id}")
        return TargetMarketProfileDraft.model_validate(drafts[-1])

    def update_market_draft(
        self,
        channel_id: uuid.UUID,
        *,
        expected_hash: str,
        draft: TargetMarketProfileDraft,
    ) -> TargetMarketProfileDraft:
        channel = self._channel(channel_id)
        governance = self._market_metadata(channel)
        current = self.get_market_draft(channel_id)
        if expected_hash != current.content_hash:
            raise ValidationFailureError("TARGET_MARKET_DRAFT_HASH_CONFLICT")
        if draft.draft_id != current.draft_id or draft.channel_id != channel.id:
            raise ValidationFailureError("TARGET_MARKET_DRAFT_EXACT_TARGET_MISMATCH")
        if current.status in {"APPROVED", "REJECTED"}:
            raise ValidationFailureError("TARGET_MARKET_DRAFT_IMMUTABLE")
        payload = draft.model_dump(mode="json", exclude={"content_hash"})
        payload["status"] = "NEEDS_HUMAN_REVIEW"
        payload["human_confirmation_required"] = True
        updated = TargetMarketProfileDraft.model_validate(payload)
        governance["drafts"][-1] = updated.model_dump(mode="json")
        self._store_market_metadata(channel, governance)
        return updated

    def approve_market_draft(
        self,
        channel_id: uuid.UUID,
        approval: TargetMarketDraftApproval,
    ) -> TargetMarketProfile | None:
        channel = self._channel(channel_id)
        governance = self._market_metadata(channel)
        draft = self.get_market_draft(channel_id)
        if (
            draft.draft_id != approval.expected_draft_id
            or draft.draft_version != approval.expected_draft_version
            or draft.content_hash != approval.expected_draft_hash
        ):
            raise ValidationFailureError("TARGET_MARKET_DRAFT_APPROVAL_TARGET_MISMATCH")
        decided_at = approval.decided_at or datetime.now(UTC)
        if approval.decision == "REJECT":
            raw = draft.model_dump(mode="json", exclude={"content_hash"})
            raw["status"] = "REJECTED"
            governance["drafts"][-1] = TargetMarketProfileDraft.model_validate(
                raw
            ).model_dump(mode="json")
            governance["approvals"].append(
                {
                    **approval.model_dump(mode="json"),
                    "decided_at": decided_at.isoformat(),
                }
            )
            governance["state"] = "DRAFT_REJECTED"
            self._store_market_metadata(channel, governance)
            return None
        profile = TargetMarketProfile(
            **draft.model_dump(
                mode="python",
                include=set(TargetMarketProfile.model_fields)
                - {
                    "schema_version",
                    "profile_version",
                    "approval_ref",
                    "approved_draft_ref",
                    "content_hash",
                },
            ),
            profile_version=len(governance["profiles"]) + 1,
            approval_ref=approval.approval_ref,
            approved_draft_ref=f"target-market-draft://{draft.draft_id}/v{draft.draft_version}",
        )
        digest = TargetMarketDigestCompiler().compile(profile)
        approved_raw = draft.model_dump(mode="json", exclude={"content_hash"})
        approved_raw["status"] = "APPROVED"
        governance["drafts"][-1] = TargetMarketProfileDraft.model_validate(
            approved_raw
        ).model_dump(mode="json")
        governance["profiles"].append(profile.model_dump(mode="json"))
        governance["digests"].append(digest.model_dump(mode="json"))
        governance["approvals"].append(
            {
                **approval.model_dump(mode="json"),
                "decided_at": decided_at.isoformat(),
                "approved_profile_ref": target_market_profile_ref(profile),
                "approved_profile_hash": profile.content_hash,
            }
        )
        governance["state"] = "APPROVED_NOT_ACTIVE"
        self._store_market_metadata(channel, governance)
        return profile

    def target_market_preview(self, channel_id: uuid.UUID) -> dict[str, Any]:
        channel = self._channel(channel_id)
        governance = self._market_metadata(channel)
        profile_raw = governance["profiles"][-1] if governance["profiles"] else None
        digest_raw = governance["digests"][-1] if governance["digests"] else None
        draft_raw = governance["drafts"][-1] if governance["drafts"] else None
        profile = (
            TargetMarketProfile.model_validate(profile_raw) if profile_raw else None
        )
        digest = TargetMarketDigest.model_validate(digest_raw) if digest_raw else None
        blockers = []
        if profile is None:
            blockers.append(MarketReasonCode.TARGET_MARKET_PROFILE_MISSING.value)
        if digest is None:
            blockers.append(MarketReasonCode.MARKET_ALIGNMENT_EVIDENCE_MISSING.value)
        return {
            "channel_id": str(channel.id),
            "state": governance["state"],
            "draft": draft_raw,
            "profile": profile.model_dump(mode="json") if profile else None,
            "digest": digest.model_dump(mode="json") if digest else None,
            "target_market": profile.primary_market
            if profile
            else channel.target_market,
            "primary_locale": profile.primary_locale if profile else None,
            "component_gate_states": {},
            "reason_codes": blockers,
            "blockers": blockers,
            "exact_next_action": (
                "Compile profile v3 only after exact operator approval."
                if not blockers
                else "Run, review and approve the target market draft."
            ),
            "organic_target_country_supported": False,
        }

    def save_destination_binding(
        self, channel_id: uuid.UUID, binding: DestinationBinding
    ) -> DestinationBinding:
        channel = self._channel(channel_id)
        if binding.channel_id != channel.id or binding.channel_key != channel.key:
            raise ValidationFailureError("DESTINATION_BINDING_CHANNEL_MISMATCH")
        governance = self._market_metadata(channel)
        profiles = [
            TargetMarketProfile.model_validate(item) for item in governance["profiles"]
        ]
        profile = next(
            (
                item
                for item in profiles
                if target_market_profile_ref(item) == binding.target_market_profile_ref
                and item.content_hash == binding.target_market_profile_hash
            ),
            None,
        )
        if profile is None:
            raise ValidationFailureError("DESTINATION_TARGET_MARKET_PROFILE_MISMATCH")
        if (
            binding.primary_market != profile.primary_market
            or binding.primary_locale != profile.primary_locale
            or binding.original_language != profile.content_language
        ):
            raise ValidationFailureError("DESTINATION_MARKET_POLICY_MISMATCH")
        metadata = deepcopy(channel.metadata_ or {})
        destination = deepcopy(
            metadata.get(DESTINATION_METADATA_KEY)
            or {"bindings": [], "active_binding_ref": None}
        )
        current = destination["bindings"][-1] if destination["bindings"] else None
        if current and binding.binding_version <= int(current["binding_version"]):
            raise ValidationFailureError("DESTINATION_BINDING_VERSION_NOT_FORWARD")
        destination["bindings"].append(binding.model_dump(mode="json"))
        metadata[DESTINATION_METADATA_KEY] = destination
        channel.metadata_ = metadata
        self.session.flush()
        return binding

    def latest_destination_binding(
        self, channel_id: uuid.UUID
    ) -> DestinationBinding | None:
        channel = self._channel(channel_id)
        destination = (channel.metadata_ or {}).get(DESTINATION_METADATA_KEY) or {}
        rows = destination.get("bindings") or []
        return DestinationBinding.model_validate(rows[-1]) if rows else None

    def _channel(self, channel_id: uuid.UUID) -> ChannelWorkspace:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        return channel

    @staticmethod
    def _market_metadata(channel: ChannelWorkspace) -> dict[str, Any]:
        metadata = deepcopy(channel.metadata_ or {})
        governance = deepcopy(
            metadata.get(TARGET_MARKET_METADATA_KEY)
            or {
                "minimal_input": {},
                "drafts": [],
                "profiles": [],
                "digests": [],
                "approvals": [],
                "active_profile_ref": None,
                "state": "MISSING",
            }
        )
        for key in ("drafts", "profiles", "digests", "approvals"):
            governance.setdefault(key, [])
        return governance

    def _store_market_metadata(
        self, channel: ChannelWorkspace, governance: dict[str, Any]
    ) -> None:
        metadata = deepcopy(channel.metadata_ or {})
        metadata[TARGET_MARKET_METADATA_KEY] = governance
        channel.metadata_ = metadata
        self.session.flush()


def freeze_project_market_lineage(
    project: VideoProject,
    *,
    profile: TargetMarketProfile,
    digest: TargetMarketDigest,
) -> None:
    frozen = {
        "target_market_profile_ref": target_market_profile_ref(profile),
        "target_market_profile_version": profile.profile_version,
        "target_market_profile_hash": profile.content_hash,
        "target_market_digest_ref": target_market_digest_ref(profile),
        "target_market_digest_hash": digest.content_hash,
        "primary_market": profile.primary_market,
        "primary_locale": profile.primary_locale,
        "narration_locale": profile.narration_locale,
        "primary_timezone": profile.primary_timezone,
        "actual_viewer_geography_state": "UNMEASURED",
    }
    summary = deepcopy(project.audience_delivery_summary or {})
    existing = summary.get("target_market_freeze")
    if existing is not None and existing != frozen:
        raise ValidationFailureError("PROJECT_TARGET_MARKET_FREEZE_IMMUTABLE")
    summary["target_market_freeze"] = frozen
    project.audience_delivery_summary = summary


class MarketPackageFreezeService:
    @staticmethod
    def package_hash(package: MarketBoundPublishPackage) -> str:
        payload = package.model_dump(
            mode="json",
            exclude={
                "content_hash",
                "approved_package_hash",
                "approval_ref",
                "package_state",
            },
        )
        return market_content_hash(payload)

    def freeze(
        self,
        *,
        package: MarketBoundPublishPackage,
        approval: MarketPackageApproval,
        destination: DestinationBinding,
        dossier: MarketAlignmentDossier,
        publish_risk: PublishRiskMarketAlignment,
    ) -> MarketBoundPublishPackage:
        current_hash = self.package_hash(package)
        if (
            approval.expected_package_id != package.package_id
            or approval.expected_package_version != package.package_version
            or approval.expected_package_hash != current_hash
            or approval.expected_destination_binding_hash != destination.content_hash
            or approval.expected_market_profile_hash
            != package.target_market_profile_hash
        ):
            raise ValidationFailureError("MARKET_PACKAGE_APPROVAL_TARGET_MISMATCH")
        reasons: list[MarketReasonCode] = []
        if not package.media_file_ref or not package.media_file_hash:
            reasons.append(MarketReasonCode.MEDIA_FILE_MISSING)
        if (
            package.technical_media_qc != "PASS"
            or package.creative_human_review != "PASS"
        ):
            reasons.append(MarketReasonCode.MARKET_PACKAGE_APPROVAL_MISSING)
        if dossier.overall_verdict != MarketVerdict.PASS:
            reasons.append(MarketReasonCode.MARKET_ALIGNMENT_EVIDENCE_MISSING)
        if publish_risk.overall_decision != MarketVerdict.PASS:
            reasons.append(MarketReasonCode.MARKET_ALIGNMENT_EVIDENCE_MISSING)
        if destination.destination_status != "VERIFIED":
            reasons.append(MarketReasonCode.DESTINATION_NOT_VERIFIED)
        if (
            package.destination_binding_hash != destination.content_hash
            or package.market_alignment_dossier_hash != dossier.content_hash
            or package.publish_risk_dossier_hash != publish_risk.content_hash
        ):
            reasons.append(MarketReasonCode.MARKET_PACKAGE_INTEGRITY_MISMATCH)
        if reasons:
            raise ValidationFailureError(
                "MARKET_PACKAGE_FREEZE_BLOCKED:"
                + ",".join(sorted({reason.value for reason in reasons}))
            )
        raw = package.model_dump(
            mode="json",
            exclude={
                "content_hash",
                "approved_package_hash",
                "approval_ref",
                "package_state",
            },
        )
        raw.update(
            {
                "package_state": "MARKET_PACKAGE_FROZEN",
                "approved_package_hash": current_hash,
                "approval_ref": approval.approval_ref,
            }
        )
        return MarketBoundPublishPackage.model_validate(raw)

    def verify_integrity(
        self,
        *,
        package: MarketBoundPublishPackage,
        destination: DestinationBinding,
        current_market_profile_hash: str,
        schedule_within_tolerance: bool = True,
        credential_still_valid: bool = True,
    ) -> MarketPackageIntegrityResult:
        current_hash = self.package_hash(package)
        reasons: list[MarketReasonCode] = []
        if package.approved_package_hash != current_hash:
            reasons.append(MarketReasonCode.MARKET_PACKAGE_INTEGRITY_MISMATCH)
        if package.destination_binding_hash != destination.content_hash:
            reasons.append(MarketReasonCode.DESTINATION_BINDING_MISMATCH)
        if package.target_market_profile_hash != current_market_profile_hash:
            reasons.append(MarketReasonCode.TARGET_MARKET_PROFILE_STALE)
        if not schedule_within_tolerance or not credential_still_valid:
            reasons.append(MarketReasonCode.MARKET_PACKAGE_INTEGRITY_MISMATCH)
        return MarketPackageIntegrityResult(
            verdict=MarketVerdict.BLOCK if reasons else MarketVerdict.PASS,
            reason_codes=sorted(set(reasons), key=str),
            approved_package_hash=package.approved_package_hash,
            current_package_hash=current_hash,
            exact_next_action=(
                "Create a new package version and obtain a new exact-target approval."
                if reasons
                else "Proceed with manual publish handoff only."
            ),
        )
