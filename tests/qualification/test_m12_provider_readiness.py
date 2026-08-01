from __future__ import annotations

import uuid

from app.core.config import Settings
from app.db.models import LLMModelProfile, LLMRouterLane, LLMRouterProfile
from app.services.m12 import (
    PROVIDER_ORDER,
    REQUIRED_OPENAI_LANE_MODELS,
    OpenAIReadinessCheck,
    ProviderReadinessService,
    _openai_lane_evidence,
)


def test_readiness_provider_order_contains_only_current_integrations():
    assert PROVIDER_ORDER == (
        "openai",
        "youtube-public",
        "youtube-owner",
        "google-drive",
        "elevenlabs",
        "google_veo",
    )


def test_readiness_read_does_not_call_providers(db_session):
    result = ProviderReadinessService(
        db_session, Settings(llm_provider="openai", openai_api_key=None)
    ).readiness()
    assert result.technical_appendix["no_provider_calls_on_get"] is True
    keys = {item.provider_key for item in result.provider_summaries}
    assert {"openai", "elevenlabs", "google_veo"} <= keys


def test_openai_readiness_fails_closed_when_credential_is_missing(db_session):
    checks = OpenAIReadinessCheck(
        db_session, Settings(llm_provider="openai", openai_api_key=None)
    ).evaluate()

    credential = next(check for check in checks if check.check_type == "CREDENTIAL")
    assert credential.check_state == "BLOCKED"
    assert credential.reason_codes == ("OPENAI_CREDENTIAL_MISSING",)
    assert all(
        check.technical_appendix.get("provider_call_made") is False
        for check in checks
        if "provider_call_made" in check.technical_appendix
    )


def test_openai_lane_evidence_requires_exact_models_and_no_fallbacks():
    profile_id = uuid.uuid4()
    profile = LLMRouterProfile(
        profile_key="default",
        provider_key="OPENAI",
        base_url="https://api.openai.com/v1",
    )
    lanes = [
        LLMRouterLane(
            router_profile_id=profile_id,
            lane_name=lane_name,
            lane_description="test",
            allowed_task_types=["test"],
            primary_model=model_id,
            fallback_models=[],
            premium_model=None,
            emergency_model=None,
            backup_model=None,
            cost_tier="LOW",
            latency_tier="FAST",
            route_priority=index,
        )
        for index, (lane_name, model_id) in enumerate(
            REQUIRED_OPENAI_LANE_MODELS.items(), start=1
        )
    ]
    model_profiles = [
        LLMModelProfile(
            provider_key="OPENAI",
            model_id=model_id,
            model_role="router",
            lane_names=[lane_name],
        )
        for lane_name, model_id in REQUIRED_OPENAI_LANE_MODELS.items()
    ]

    assert _openai_lane_evidence(profile, lanes, model_profiles)["valid"] is True

    lanes[0].fallback_models = ["gpt-5.6-terra"]
    evidence = _openai_lane_evidence(profile, lanes, model_profiles)
    assert evidence["valid"] is False
    assert evidence["fallback_lanes"] == ["cheap_structured"]


def test_settings_have_no_generic_cloud_renderer_selection():
    fields = Settings.model_fields
    assert "cloud_final_assembly_renderer" not in fields
    assert "cloud_template_renderer" not in fields
    assert "cloud_final_renderer_provider" not in fields
