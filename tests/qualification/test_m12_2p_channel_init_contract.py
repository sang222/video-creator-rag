from __future__ import annotations

import copy
import uuid

import pytest

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.core.errors import ValidationFailureError
from app.db.models import CompiledChannelPolicySnapshot, User, VideoProject
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
)


def _contract(**overrides):
    base = {
        "channel_identity": {
            "channel_name": "VCOS VN",
            "channel_type": "YOUTUBE_CHANNEL",
            "niche": "AI workflow cho operator",
            "positioning": "Giải thích thực dụng, không hype",
            "brand_promise": "Giúp operator hiểu workflow và giới hạn bằng chứng.",
            "primary_platform": "YouTube",
            "secondary_platforms": ["Shorts"],
        },
        "target_audience": {
            "primary_persona": "Founder/operator bán kỹ thuật",
            "audience_level": "semi_technical",
            "pain_points": ["Thiếu quy trình sản xuất video có kiểm soát"],
            "desired_outcome": "Biết triển khai workflow an toàn",
            "audience_notes": "Ưa ví dụ thực tế.",
        },
        "market_locale": {
            "primary_market": "VN",
            "secondary_markets": ["US"],
            "audience_locale": "vi-VN",
            "content_language": "vi",
            "operator_language": "vi",
            "timezone": "Asia/Ho_Chi_Minh",
            "currency": "VND",
            "measurement_units": "metric",
            "date_format": "DD/MM/YYYY",
            "cultural_style": {"tone": "calm", "formality": "neutral", "humor": "light", "cta_style": "soft"},
            "market_examples_preference": "prefer",
            "regulatory_sensitivity": {
                "finance_claim_sensitivity": "high",
                "health_claim_sensitivity": "high",
                "disclosure_standard": "clear",
            },
        },
        "editorial_strategy": {
            "content_pillars": ["Workflow", "Policy", "Operator checklist"],
            "allowed_angles": ["practical explainer"],
            "forbidden_angles": ["guaranteed ROI"],
            "claim_style": ["measured", "evidence_backed", "no_exaggerated_roi"],
            "allowed_topics": ["AI workflow"],
            "forbidden_topics": ["fake traffic"],
        },
        "format_policy": {
            "long_form": {
                "enabled": True,
                "target_duration_minutes": {"min": 8, "max": 14},
                "structure": ["hook", "problem", "mechanism", "result", "takeaway"],
                "chapters_required": True,
            },
            "shorts": {
                "enabled": True,
                "target_duration_seconds": {"min": 30, "max": 45},
                "hard_max_seconds": 59,
                "captions_required": True,
                "shorts_per_long_form": 2,
            },
        },
        "voice_style": {
            "narration_tone": "practical_explainer",
            "pacing": "clear_short_sentences",
            "allowed_style": ["calm", "specific"],
            "forbidden_style": ["hype", "fearmongering", "aggressive_sales", "fake_urgency"],
        },
        "platform_strategy": {
            "primary_platform": "YouTube",
            "youtube_is_learning_authority": True,
            "secondary_platforms": ["Shorts"],
            "disabled_authorities": ["tiktok_analytics_learning", "facebook_analytics_learning"],
            "publish_mode": "human_handoff_only",
            "auto_publish_allowed": False,
            "studio_scraping_allowed": False,
        },
        "media_policy": {
            "voice_provider": "ElevenLabs",
            "ai_hero_provider": "Google Vertex Veo",
            "ai_hero_model_id": "veo-3.1-fast-generate-001",
            "ai_hero_allowed_durations_seconds": [4, 6, 8],
            "ai_hero_default_duration_seconds": 8,
            "ai_hero_audio": False,
            "ai_hero_allowed_use": ["hero_shot", "hard_to_find_visual"],
            "ai_hero_forbidden_use": ["data_diagram", "workflow_chart", "factual_evidence_visualization"],
            "renderer": "Creatomate Growth 10K",
            "storage_archive": "Google Drive",
            "drive_offload_enabled": True,
        },
        "rights_policy": {
            "source_manifest_required": True,
            "rights_evidence_required": True,
            "ai_disclosure_required_when_ai_media_used": True,
            "synthetic_media_warning_when_applicable": True,
            "music_policy": "approved_licensed_audio_library_safe_only",
            "reused_content_sensitivity": "medium",
        },
        "budget_policy": {
            "cost_sensitivity": "medium",
            "avoid_unnecessary_ai_hero": True,
            "prefer_reuse_safe_assets": True,
            "exact_cost_claim_requires_provider_snapshot": True,
        },
        "learning_policy": {
            "authority": "youtube_analytics_only",
            "min_evidence_required": "2 source refs for non-obvious claims",
            "auto_promote_learning": False,
            "config_mutation_by_agent_allowed": False,
            "weak_evidence_action": "summarize_limitations_only",
        },
        "forbidden_behavior": [
            "fake_traffic",
            "bot_engagement",
            "spam_reupload",
            "algorithm_manipulation",
            "platform_evasion",
            "ip_vps_tricks",
            "youtube_studio_scraping",
            "dashboard_scraping",
            "invented_metrics",
            "invented_sources",
            "invented_rights",
            "unsupported_local_claims",
        ],
    }
    return _deep_update(base, overrides)


def _deep_update(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_update(result[key], value)
        else:
            result[key] = value
    return result


def _scope(db_session, contract=None):
    company = CompanyService(db_session).create_company(name=f"M12.2P {uuid.uuid4().hex[:8]}")
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            key=f"m12-2p-{uuid.uuid4().hex[:8]}",
            name="M12.2P Channel",
            primary_language="vi",
            primary_region="VN",
            primary_timezone="Asia/Ho_Chi_Minh",
            target_market="VN",
            default_timezone="Asia/Ho_Chi_Minh",
            target_regions=["VN"],
            metadata={"operator_language": "vi", "m12_2p_channel_contract": contract or _contract()},
        ),
    )
    compiler = ChannelProfileCompiler(db_session)
    profile_input, _ = compiler.profile_input_from_template("saas_digital_leverage")
    contract_payload = contract or _contract()
    profile_input = profile_input.model_copy(
        update={
            "display_name": contract_payload["channel_identity"]["channel_name"],
            "target_market": contract_payload["market_locale"].get("primary_market") or "",
            "format_strategy": contract_payload["format_policy"],
            "voice_style": contract_payload["voice_style"],
            "platform_strategy": contract_payload["platform_strategy"],
            "content_pillars": contract_payload["editorial_strategy"]["content_pillars"],
            "policies": {"review": "human_review", "safety": "avoid unsupported claims", "channel_contract": contract_payload},
        }
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(profile_input=profile_input),
    )
    compiled = compiler.compile(profile_version_id=profile.id, correlation_id="m12-2p-test")
    snapshot = db_session.get(CompiledChannelPolicySnapshot, compiled.snapshot_id)
    return company, channel, profile, compiled, snapshot


def test_m12_2p_complete_contract_compiles_snapshot_and_activates(db_session) -> None:
    _, channel, profile, _, snapshot = _scope(db_session)

    contract = snapshot.compiled_payload["channel_contract_json"]

    assert profile.version == 1
    assert contract["contract_status"] == "COMPLETE"
    assert snapshot.compiled_payload["compiled_policy_snapshot_json"]["channel_contract_status"] == "COMPLETE"
    assert snapshot.compiled_payload["contract_status"] == "COMPLETE"
    assert contract["market_locale"]["primary_market"] == "VN"
    assert contract["platform_strategy"]["publish_mode"] == "human_handoff_only"
    assert contract["media_policy"]["renderer"] == "Creatomate Growth 10K"
    assert contract["learning_policy"]["authority"] == "youtube_analytics_only"

    activated = ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
    db_session.refresh(channel)
    assert activated.status == "active"
    assert channel.active_policy_snapshot_id == snapshot.id


@pytest.mark.parametrize(
    ("override", "missing_field", "unexpected_default"),
    [
        ({"market_locale": {"primary_market": None}}, "market_locale.primary_market", "US"),
        ({"market_locale": {"content_language": None}}, "market_locale.content_language", "en"),
        ({"target_audience": {"primary_persona": None}}, "target_audience.primary_persona", None),
    ],
)
def test_m12_2p_missing_required_contract_fields_are_partial_without_defaults(
    db_session,
    override,
    missing_field,
    unexpected_default,
) -> None:
    _, _, _, _, snapshot = _scope(db_session, _contract(**override))

    contract = snapshot.compiled_payload["channel_contract_json"]

    assert contract["contract_status"] == "PARTIAL"
    assert missing_field in contract["missing_fields"]
    if missing_field == "market_locale.primary_market":
        assert contract["market_locale"]["primary_market"] != unexpected_default
    if missing_field == "market_locale.content_language":
        assert contract["market_locale"]["content_language"] != unexpected_default
    with pytest.raises(ValidationFailureError):
        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)


def test_m12_2p_rejects_legacy_provider_budget_inputs(db_session) -> None:
    company = CompanyService(db_session).create_company(name="Budget Reject")
    with pytest.raises(ValidationFailureError):
        ChannelWorkspaceService(db_session).create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(
                key="budget-reject",
                name="Budget Reject",
                metadata={"tts_character_budget": 1000},
            ),
        )
    with pytest.raises(ValidationFailureError):
        ChannelWorkspaceService(db_session).create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(
                key="budget-reject-2",
                name="Budget Reject 2",
                metadata={"m12_2p_channel_contract": {"ai_hero_budget_usd": 12}},
            ),
        )


def test_m12_2p_contradictory_auto_publish_blocks_activation(db_session) -> None:
    _, _, _, _, snapshot = _scope(
        db_session,
        _contract(platform_strategy={"auto_publish_allowed": True}),
    )

    contract = snapshot.compiled_payload["channel_contract_json"]

    assert contract["contract_status"] == "CONTRADICTORY"
    assert any("auto_publish_allowed" in reason for reason in contract["contradiction_reasons"])
    with pytest.raises(ValidationFailureError):
        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)


def test_m12_2p_contract_hash_changes_when_market_locale_changes(db_session) -> None:
    _, _, _, first, first_snapshot = _scope(db_session, _contract())
    _, _, _, second, second_snapshot = _scope(
        db_session,
        _contract(market_locale={"primary_market": "US", "audience_locale": "en-US", "content_language": "en", "currency": "USD", "timezone": "America/New_York"}),
    )

    assert first.content_hash == first_snapshot.content_hash
    assert second.content_hash == second_snapshot.content_hash
    assert first.content_hash != second.content_hash
    assert second_snapshot.compiled_payload["channel_contract_json"]["market_locale"]["primary_market"] == "US"


def test_m12_2p_existing_video_project_snapshot_is_not_mutated(db_session) -> None:
    company, channel, _, _, first_snapshot = _scope(db_session, _contract())
    activated = ChannelProfileService(db_session).activate_snapshot(snapshot_id=first_snapshot.id)
    user = User(email=f"m12-2p-{uuid.uuid4().hex[:8]}@example.com", display_name="M12.2P", status="active")
    db_session.add(user)
    db_session.flush()
    project = VideoProject(
        company_id=company.id,
        channel_workspace_id=channel.id,
        policy_snapshot_id=activated.id,
        title="Frozen project",
        description="Project keeps original snapshot.",
        created_by_user_id=user.id,
    )
    db_session.add(project)
    db_session.flush()
    before = copy.deepcopy(first_snapshot.compiled_payload)

    compiler = ChannelProfileCompiler(db_session)
    profile_input, _ = compiler.profile_input_from_template("saas_digital_leverage")
    new_contract = _contract(market_locale={"primary_market": "US", "audience_locale": "en-US", "content_language": "en", "currency": "USD", "timezone": "America/New_York"})
    profile_input = profile_input.model_copy(
        update={
            "display_name": "New market",
            "target_market": "US",
            "format_strategy": new_contract["format_policy"],
            "voice_style": new_contract["voice_style"],
            "platform_strategy": new_contract["platform_strategy"],
            "content_pillars": new_contract["editorial_strategy"]["content_pillars"],
            "policies": {"channel_contract": new_contract},
        }
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(profile_input=profile_input),
    )
    second = compiler.compile(profile_version_id=profile.id, correlation_id="m12-2p-new-snapshot")
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=second.snapshot_id)

    db_session.refresh(first_snapshot)
    db_session.refresh(project)
    assert first_snapshot.compiled_payload == before
    assert project.policy_snapshot_id == activated.id


def test_m12_2p_channel_contract_has_no_provider_budget_numbers(db_session) -> None:
    _, _, _, _, snapshot = _scope(db_session)
    contract = snapshot.compiled_payload["channel_contract_json"]

    assert "monthly_budget_usd" not in str(contract)
    assert "tts_character_budget" not in str(contract)
    assert "ai_hero_budget_usd" not in str(contract)
    assert set(contract["budget_policy"]) == {
        "cost_sensitivity",
        "avoid_unnecessary_ai_hero",
        "prefer_reuse_safe_assets",
        "exact_cost_claim_requires_provider_snapshot",
    }
