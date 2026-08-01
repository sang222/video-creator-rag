"""Pure Phase 1 standalone/bootstrap source-policy checks."""

from __future__ import annotations

from copy import deepcopy
import json
import uuid

import pytest
from pydantic import ValidationError

from app.contracts.launch_cadence import FirstChannelLaunchPolicyCreate
from app.contracts.profile import ChannelProfileInput
from app.services.channel_contract import (
    CONTRACT_COMPLETE,
    CONTRACT_CONTRADICTORY,
    build_channel_contract,
)
from app.services.runtime_bootstrap import Phase1RuntimeBootstrapService


def _launch_policy_kwargs() -> dict:
    return {
        "company_id": uuid.UUID(int=1),
        "channel_workspace_id": uuid.UUID(int=2),
        "channel_profile_version_id": uuid.UUID(int=3),
        "policy_snapshot_id": uuid.UUID(int=4),
        "evidence_refs": [{"type": "operator_approval", "ref": "test://phase1-launch"}],
    }


def _stale_profile_payload() -> dict:
    return {
        "template_key": "saas_digital_leverage",
        "template_version": "1.0.0",
        "display_name": "Small Team AI",
        "target_market": "US",
        "audience_segment": "professional_dense",
        "monetization_model": {
            "primary": "mixed",
            "channels": ["adsense", "saas_affiliate"],
        },
        "format_strategy": {
            "long_form_minutes": "6-12",
            "shorts": {"enabled": True},
        },
        "risk_tolerance": "low_to_medium",
        "media_style": {"renderer": "CapCut"},
        "voice_style": {"tone": "professional calm documentary explainer"},
        "evidence_requirement": {"claims": "practical software claims"},
        "platform_strategy": {
            "primary": "TikTok",
            "auto_publish_allowed": True,
        },
        "human_review_strictness": "strict",
        "content_pillars": ["AI automation workflows"],
        "series_plan": [
            {
                "key": "legacy-short-plan",
                "format": "long_form_and_shorts",
            }
        ],
        "initial_content_runway": [
            {"title": "Automation workflow", "format": "long_form"}
        ],
        "policies": {
            "channel_contract": {
                "channel_identity": {
                    "channel_name": "Small Team AI",
                    "niche": "Practical AI workflows for small teams",
                    "primary_platform": "TikTok",
                    "secondary_platforms": ["Shorts"],
                },
                "target_audience": {"primary_persona": "small-team operators"},
                "market_locale": {
                    "primary_market": "US",
                    "audience_locale": "en-US",
                    "content_language": "en",
                    "operator_language": "vi",
                    "timezone": "America/New_York",
                },
                "editorial_strategy": {"content_pillars": ["AI automation workflows"]},
                "format_policy": {
                    "long_form": {"enabled": True},
                    "shorts": {"enabled": True},
                },
                "voice_style": {"narration_tone": "practical explainer"},
                "platform_strategy": {
                    "primary_platform": "TikTok",
                    "publish_mode": "automatic",
                    "auto_publish_allowed": True,
                },
                "media_policy": {
                    "voice_provider": "ElevenLabs",
                    "renderer": "CapCut",
                },
            }
        },
    }


def test_profile_input_accepts_an_explicit_zero_series_authority() -> None:
    profile = ChannelProfileInput.model_validate(
        _stale_profile_payload() | {"series_plan": []}
    )

    assert profile.series_plan == []


def test_launch_policy_derives_zero_or_supplied_series_count() -> None:
    standalone = FirstChannelLaunchPolicyCreate(
        **_launch_policy_kwargs(),
        approved_initial_series_plan_ids=[],
    )
    planned = FirstChannelLaunchPolicyCreate(
        **_launch_policy_kwargs(),
        approved_initial_series_plan_ids=[uuid.UUID(int=5), uuid.UUID(int=6)],
    )

    assert standalone.initial_series_count == 0
    assert planned.initial_series_count == 2

    with pytest.raises(ValidationError, match="initial_series_count must match"):
        FirstChannelLaunchPolicyCreate(
            **_launch_policy_kwargs(),
            approved_initial_series_plan_ids=[],
            initial_series_count=1,
        )


def test_sanitization_is_pure_and_replaces_retired_launch_authority() -> None:
    stale = _stale_profile_payload()
    original = deepcopy(stale)
    stale_contract = build_channel_contract(profile_input=stale)

    assert stale_contract["contract_status"] == CONTRACT_CONTRADICTORY
    assert set(stale_contract["contradiction_reasons"]) >= {
        "CHANNEL_CONTRACT_SHORTS_AUTHORITY_FORBIDDEN",
        "CHANNEL_CONTRACT_PRIMARY_PLATFORM_MUST_BE_YOUTUBE",
        "CHANNEL_CONTRACT_AUTO_PUBLISH_FORBIDDEN",
        "CHANNEL_CONTRACT_RENDERER_MUST_BE_NATIVE_FFMPEG",
    }

    clean = Phase1RuntimeBootstrapService.sanitize_profile_input(stale)
    repeated = Phase1RuntimeBootstrapService.sanitize_profile_input(clean)
    contract = build_channel_contract(profile_input=clean.model_dump(mode="json"))

    assert stale == original
    assert clean.model_dump(mode="json") == repeated.model_dump(mode="json")
    assert clean.series_plan == []
    assert clean.target_market == "US"
    assert clean.platform_strategy == {
        "primary": "youtube_long_form",
        "primary_platform": "YouTube",
        "publish_mode": "human_handoff_only",
        "auto_publish_allowed": False,
        "studio_scraping_allowed": False,
        "secondary_platforms": [],
    }
    assert clean.monetization_model == {
        "primary": "platform_ad_revenue",
        "channels": ["adsense"],
        "affiliate_cta": False,
        "sponsor_content": False,
    }
    assert "shorts" not in json.dumps(clean.model_dump(mode="json")).lower()
    assert contract["contract_status"] == CONTRACT_COMPLETE
    assert contract["channel_identity"]["primary_platform"] == "YouTube"
    assert contract["platform_strategy"]["publish_mode"] == "human_handoff_only"
    assert contract["media_policy"]["renderer"] == "NativeFFmpegRenderer"
