from __future__ import annotations

import uuid
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.contracts import (
    MARKET_GATE_STRICT_ORDER,
    MarketReasonCode,
    MarketVerdict,
    MetadataMarketAlignmentInput,
    ResearchJurisdictionInput,
    ScriptMarketAlignmentInput,
    TargetMarketProfile,
    ThumbnailMarketAlignmentInput,
    TopicMarketAlignmentInput,
    VisualMarketAlignmentInput,
    VoiceLocaleAlignmentInput,
)
from app.core.errors import ValidationFailureError
from app.services import (
    IdeaMarketPreflightEvaluator,
    MarketAlignmentDossierBuilder,
    MetadataMarketAlignmentGate,
    ResearchJurisdictionGate,
    ScriptMarketAlignmentGate,
    TargetMarketAlignmentGateRegistry,
    TargetMarketDigestCompiler,
    ThumbnailMarketAlignmentGate,
    TopicMarketAlignmentGate,
    VisualMarketAlignmentGate,
    VoiceLocaleAlignmentGate,
)


def _profile(**updates) -> TargetMarketProfile:
    payload = {
        "profile_version": 1,
        "channel_id": uuid.UUID("10000000-0000-0000-0000-000000000001"),
        "channel_key": "small-team-ai",
        "primary_market": "US",
        "primary_geo_cluster": ["US"],
        "acceptable_secondary_geos": ["CA", "GB", "AU"],
        "primary_locale": "en-US",
        "content_language": "en",
        "narration_locale": "en-US",
        "primary_timezone": "America/New_York",
        "spelling_system": "US",
        "currency": "USD",
        "units_policy": "US_WITH_METRIC_WHEN_RELEVANT",
        "date_format": "MMM D, YYYY",
        "title_locale": "en-US",
        "thumbnail_text_locale": "en-US",
        "caption_locales": ["en-US"],
        "audience_market_context": "US_SMALL_BUSINESS",
        "workplace_context": "US_SMALL_BUSINESS",
        "source_jurisdiction_policy": "TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED",
        "preferred_source_jurisdictions": ["US"],
        "foreign_source_context_required": True,
        "allowed_market_contexts": ["US", "CA", "GB", "AU"],
        "prohibited_market_mismatches": [
            "TRANSLATED_SOUNDING_ENGLISH",
            "NON_US_CURRENCY_WITHOUT_USD_EQUIVALENT",
            "FOREIGN_LEGAL_ASSUMPTION_WITHOUT_CONTEXT",
            "WRONG_VOICE_LOCALE",
            "WRONG_METADATA_LOCALE",
            "WRONG_THUMBNAIL_LOCALE",
        ],
        "initial_publish_window_hypotheses": [
            {
                "timezone": "America/New_York",
                "days": ["TUE", "THU"],
                "local_time": "10:00",
                "status": "HYPOTHESIS_ONLY",
            }
        ],
        "minimum_comparable_videos": 3,
        "video_geo_evaluation_window_days": 7,
        "channel_geo_review_window_days": 30,
        "account_country": None,
        "target_market": "US",
        "actual_viewer_geography_state": "UNMEASURED",
        "approval_ref": "operator-approval://geo1/small-team-ai/us/v1",
    }
    payload.update(updates)
    return TargetMarketProfile.model_validate(payload)


def _preflight(profile: TargetMarketProfile, *, scope=None, score_all=True):
    digest = TargetMarketDigestCompiler().compile(profile)
    criteria = {
        "topic_demand_market_scope": score_all,
        "target_audience_fit": True,
        "terminology_fit": True,
        "tool_product_availability": True,
        "business_context_fit": True,
        "monetization_fit": True,
        "source_availability": True,
        "local_relevance": True,
    }
    result = IdeaMarketPreflightEvaluator().evaluate(
        daily_idea_decision_ref="daily-idea://1",
        niche_contract_digest_ref="niche://1",
        niche_contract_digest_hash="1" * 64,
        target_market_digest=digest,
        editorial_slot_ref="slot://1",
        content_category_ref="category://1",
        market_scope=scope or ["US"],
        criteria=criteria,
        evidence_refs=[{"ref": "fixture://us-demand"}],
    )
    return digest, result


def test_target_market_profile_is_typed_versioned_and_digest_is_deterministic() -> None:
    profile = _profile()
    first = TargetMarketDigestCompiler().compile(profile)
    second = TargetMarketDigestCompiler().compile(profile)
    assert profile.profile_version == 1
    assert profile.target_market == "US"
    assert profile.account_country is None
    assert profile.actual_viewer_geography_state == "UNMEASURED"
    assert first == second
    assert first.content_hash == second.content_hash
    assert "prohibited_market_mismatches" in first.model_dump()
    assert "account_country" not in first.model_dump()


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"primary_locale": "EU"}, "string_pattern_mismatch"),
        ({"primary_timezone": "Eastern Time"}, "PRIMARY_TIMEZONE_NOT_IANA"),
        ({"target_market": "CA"}, "TARGET_MARKET_PRIMARY_MARKET_MISMATCH"),
        ({"actual_viewer_geography_state": "US"}, "literal_error"),
    ],
)
def test_profile_rejects_invalid_or_conflated_market_truth(updates, match) -> None:
    with pytest.raises(ValidationError, match=match):
        _profile(**updates)


def test_global_demand_does_not_substitute_us_market_demand() -> None:
    _digest, result = _preflight(_profile(), scope=["GLOBAL"])
    assert result.decision == MarketVerdict.REVIEW_REQUIRED
    assert MarketReasonCode.MARKET_DEMAND_SCOPE_MISSING in result.reason_codes


def test_us_topic_and_market_native_components_pass() -> None:
    profile = _profile()
    digest, preflight = _preflight(profile)
    assert preflight.decision == MarketVerdict.PASS
    topic = TopicMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=TopicMarketAlignmentInput(preflight=preflight),
    )
    research = ResearchJurisdictionGate().evaluate(
        profile=profile,
        digest=digest,
        data=ResearchJurisdictionInput(
            target_market="US",
            source_jurisdictions=["US"],
            claim_jurisdiction="US",
            jurisdiction_specific_claim=True,
            currency="USD",
            units_policy="US_WITH_METRIC_WHEN_RELEVANT",
            date_format="MMM D, YYYY",
            evidence_refs=[{"ref": "fixture://us-source"}],
        ),
    )
    script = ScriptMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=ScriptMarketAlignmentInput(
            language_locale="en-US",
            currencies=["USD"],
            units_policy="US_WITH_METRIC_WHEN_RELEVANT",
            date_format="MMM D, YYYY",
            workplace_context="US_SMALL_BUSINESS",
            audience_market_context="US_SMALL_BUSINESS",
        ),
    )
    voice = VoiceLocaleAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=VoiceLocaleAlignmentInput(
            narration_locale="en-US",
            content_language="en",
            voice_profile_locale="en-US",
        ),
    )
    visual = VisualMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=VisualMarketAlignmentInput(
            market_contexts=["US"],
            actual_ui_or_product_jurisdiction="US",
            currencies=["USD"],
            date_format="MMM D, YYYY",
            workplace_context="US_SMALL_BUSINESS",
        ),
    )
    thumbnail = ThumbnailMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=ThumbnailMarketAlignmentInput(text_locale="en-US", currencies=["USD"]),
    )
    metadata = MetadataMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=MetadataMarketAlignmentInput(
            title_locale="en-US",
            description_locale="en-US",
            original_language="en",
            caption_locales=["en-US"],
            keywords_market_scope=["US"],
            cta_market_scope=["US"],
        ),
    )
    components = [topic, research, script, voice, visual, thumbnail, metadata]
    assert all(item.verdict == MarketVerdict.PASS for item in components)
    dossier = MarketAlignmentDossierBuilder().build(
        profile=profile,
        digest=digest,
        channel_profile_version_ref="profile://v2",
        compiled_policy_snapshot_ref="snapshot://v2",
        compiled_policy_snapshot_hash="2" * 64,
        video_project_ref="project://1",
        video_project_hash="3" * 64,
        niche_alignment_dossier_ref="niche-dossier://1",
        niche_alignment_dossier_hash="4" * 64,
        component_results=components,
    )
    assert dossier.overall_verdict == MarketVerdict.PASS


def test_negative_market_mismatches_block_or_surface_review() -> None:
    profile = _profile()
    digest, _ = _preflight(profile)
    uk_tax = ResearchJurisdictionGate().evaluate(
        profile=profile,
        digest=digest,
        data=ResearchJurisdictionInput(
            target_market="US",
            source_jurisdictions=["GB"],
            claim_jurisdiction="US",
            legal_or_regulatory_claim=True,
            jurisdiction_specific_claim=True,
            presented_as_target_market_truth=True,
            foreign_source_context_disclosed=False,
            evidence_sensitive_claim=True,
        ),
    )
    assert uk_tax.verdict == MarketVerdict.BLOCK
    assert MarketReasonCode.SOURCE_JURISDICTION_MISMATCH in uk_tax.reason_codes
    assert MarketReasonCode.FOREIGN_CONTEXT_NOT_DISCLOSED in uk_tax.reason_codes

    script = ScriptMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=ScriptMarketAlignmentInput(
            language_locale="en-US",
            currencies=["VND"],
            translated_sounding_language_risk=True,
        ),
    )
    assert script.verdict == MarketVerdict.BLOCK
    assert MarketReasonCode.CURRENCY_MISMATCH in script.reason_codes
    assert MarketReasonCode.TRANSLATED_SOUNDING_LANGUAGE_RISK in script.reason_codes

    wrong_voice = VoiceLocaleAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=VoiceLocaleAlignmentInput(
            narration_locale="en-GB", content_language="en", voice_profile_locale="en-GB"
        ),
    )
    assert wrong_voice.verdict == MarketVerdict.BLOCK

    german_thumbnail = ThumbnailMarketAlignmentGate().evaluate(
        profile=profile,
        digest=digest,
        data=ThumbnailMarketAlignmentInput(text_locale="de-DE"),
    )
    assert german_thumbnail.verdict == MarketVerdict.BLOCK


def test_foreign_source_with_explicit_context_is_allowed() -> None:
    profile = _profile()
    digest = TargetMarketDigestCompiler().compile(profile)
    result = ResearchJurisdictionGate().evaluate(
        profile=profile,
        digest=digest,
        data=ResearchJurisdictionInput(
            target_market="US",
            source_jurisdictions=["GB"],
            claim_jurisdiction="GB",
            legal_or_regulatory_claim=True,
            jurisdiction_specific_claim=True,
            presented_as_target_market_truth=False,
            foreign_source_context_disclosed=True,
            evidence_sensitive_claim=True,
        ),
    )
    assert result.verdict == MarketVerdict.PASS


def test_stale_profile_and_missing_component_evidence_fail_closed() -> None:
    profile = _profile()
    stale = _profile(profile_version=2, approval_ref="operator://v2")
    digest = TargetMarketDigestCompiler().compile(stale)
    with pytest.raises(ValidationFailureError, match="TARGET_MARKET_PROFILE_STALE"):
        ScriptMarketAlignmentGate().evaluate(
            profile=profile,
            digest=digest,
            data=ScriptMarketAlignmentInput(language_locale="en-US"),
        )

    current_digest = TargetMarketDigestCompiler().compile(profile)
    dossier = MarketAlignmentDossierBuilder().build(
        profile=profile,
        digest=current_digest,
        channel_profile_version_ref="profile://v2",
        compiled_policy_snapshot_ref="snapshot://v2",
        compiled_policy_snapshot_hash="2" * 64,
        video_project_ref="project://1",
        video_project_hash="3" * 64,
        niche_alignment_dossier_ref="niche-dossier://1",
        niche_alignment_dossier_hash="4" * 64,
        component_results=[],
    )
    assert dossier.overall_verdict == MarketVerdict.BLOCK
    assert MarketReasonCode.MARKET_ALIGNMENT_EVIDENCE_MISSING in dossier.reason_codes


def test_market_gate_registry_has_strict_order_and_no_provider_surface() -> None:
    registry = TargetMarketAlignmentGateRegistry()
    assert registry.strict_order == MARKET_GATE_STRICT_ORDER
    assert len(registry.registered_keys) == len(MARKET_GATE_STRICT_ORDER) - 1
    assert registry.get(MARKET_GATE_STRICT_ORDER[0]).VERSION.startswith("geo1")
    source = deepcopy(registry.__dict__)
    assert "provider" not in repr(source).lower()
