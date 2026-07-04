from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.models import FirstScriptedVideoPackage, ProviderAttempt, RealSmokeRun, VideoGenerationBoundary
from app.main import create_app
from app.services.m2 import (
    CreatomateRenderRequestBuilder,
    ElevenLabsVoiceAdapter,
    ElevenLabsVoiceRequestBuilder,
    LumaHeroVideoAdapter,
    LumaHeroVideoRequestBuilder,
    PexelsSearchRequestBuilder,
    PexelsVisualFallbackAdapter,
    ProviderBoundaryPreflight,
    ProviderCapabilityMatrix,
    ProviderCostEstimatePlaceholder,
    ProviderReadinessM2Service,
    validate_pexels_policy,
)
from app.services.m12_2 import FirstScriptedVideoPackageService
from tests.qualification.conftest import QualificationFactory
from tests.qualification.helpers.network_sentinel import install_network_sentinel


ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "voice_provider": "elevenlabs",
        "ai_video_hero_provider": "luma_api",
        "cloud_final_assembly_renderer": "creatomate_growth_10k",
        "cloud_template_renderer": "creatomate_growth_10k",
        "free_visual_fallback_provider": "pexels_api",
        "elevenlabs_api_key": None,
        "elevenlabs_voice_id": None,
        "elevenlabs_model_id": None,
        "luma_api_key": None,
        "luma_hero_model": None,
        "luma_default_duration_seconds": 8,
        "luma_max_duration_seconds": 8,
        "luma_video_only": True,
        "creatomate_api_key": None,
        "creatomate_template_id": None,
        "creatomate_workspace_id": None,
        "pexels_api_key": None,
        "pexels_attribution_required": True,
        "pexels_max_clips_per_long": 3,
        "pexels_max_runtime_pct_per_long": 20,
        "pexels_max_same_asset_reuse_per_30_days": 2,
        "google_drive_archive_enabled": False,
        "google_drive_root_folder_id": None,
        "provider_real_readiness_probe_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _by_provider(settings: Settings) -> dict[str, object]:
    return {item.provider_key: item for item in ProviderReadinessM2Service(settings).snapshot().providers}


def test_m2_empty_env_produces_exact_provider_blockers_without_crash() -> None:
    snapshot = ProviderReadinessM2Service(_settings()).snapshot()
    providers = {item.provider_key: item for item in snapshot.providers}

    assert snapshot.snapshot_state == "BLOCKED"
    assert providers["elevenlabs"].readiness_state == "NOT_CONFIGURED"
    assert providers["elevenlabs"].credential_status.state == "NEEDS_CREDENTIAL"
    assert "ELEVENLABS_API_KEY_MISSING" in providers["elevenlabs"].blocker_reason_codes
    assert providers["luma_api"].readiness_state == "NOT_CONFIGURED"
    assert "LUMA_API_KEY_MISSING" in providers["luma_api"].blocker_reason_codes
    assert providers["creatomate_growth_10k"].readiness_state == "NOT_CONFIGURED"
    assert "CREATOMATE_API_KEY_MISSING" in providers["creatomate_growth_10k"].blocker_reason_codes
    assert providers["pexels_api"].readiness_state == "NOT_CONFIGURED"
    assert "PEXELS_API_KEY_MISSING" in providers["pexels_api"].blocker_reason_codes
    assert providers["google_drive_archive"].readiness_state == "DISABLED"
    assert snapshot.no_network_calls_made is True


def test_m2_partial_env_reports_exact_missing_fields() -> None:
    eleven = _by_provider(_settings(elevenlabs_api_key="sk-eleven-test"))["elevenlabs"]
    assert eleven.credential_status.state == "CREDENTIAL_PRESENT"
    assert eleven.readiness_state == "NEEDS_VOICE"
    assert "ELEVENLABS_VOICE_ID_MISSING" in eleven.blocker_reason_codes
    assert "ELEVENLABS_MODEL_ID_MISSING" in eleven.blocker_reason_codes

    luma = _by_provider(_settings(luma_api_key="luma-test"))["luma_api"]
    assert luma.readiness_state == "NEEDS_MODEL"
    assert "LUMA_HERO_MODEL_MISSING" in luma.blocker_reason_codes

    creatomate = _by_provider(_settings(creatomate_api_key="creatomate-test"))["creatomate_growth_10k"]
    assert creatomate.readiness_state == "NEEDS_TEMPLATE"
    assert "CREATOMATE_TEMPLATE_ID_MISSING" in creatomate.blocker_reason_codes
    assert "CREATOMATE_WORKSPACE_ID_MISSING" in creatomate.blocker_reason_codes

    pexels = _by_provider(_settings())["pexels_api"]
    assert pexels.readiness_state == "NOT_CONFIGURED"
    assert pexels.missing_env_keys == ["PEXELS_API_KEY"]


def test_m2_capability_matrix_matches_locked_provider_stack() -> None:
    matrix = {entry.provider_key: entry for entry in ProviderCapabilityMatrix(_settings()).entries()}

    assert matrix["elevenlabs"].capabilities == ["VOICE_GENERATION"]
    assert matrix["luma_api"].limits["max_duration_seconds"] == 8
    assert "FINAL_ASSEMBLY_RENDER" in matrix["creatomate_growth_10k"].capabilities
    assert "THUMBNAIL_COMPOSITION" in matrix["creatomate_growth_10k"].capabilities
    assert "factual_evidence" in matrix["pexels_api"].blocked_roles
    assert matrix["google_drive_archive"].capabilities == ["ARCHIVE_STORAGE"]
    assert matrix["youtube_readonly"].capabilities == ["READ_ONLY_VERIFICATION_ANALYTICS"]
    assert all(entry.no_call_in_m2 for entry in matrix.values())


def test_m2_pexels_policy_blocks_roles_and_limits() -> None:
    settings = _settings()

    assert "PEXELS_USAGE_ROLE_BLOCKED" in validate_pexels_policy("factual_evidence", settings)
    assert "PEXELS_USAGE_ROLE_BLOCKED" in validate_pexels_policy("recurring_host_identity", settings)
    codes = validate_pexels_policy(
        "short_broll",
        settings,
        {"clips_per_long": 4, "runtime_pct_per_long": 21, "same_asset_reuse_per_30_days": 3},
    )
    assert {"PEXELS_MAX_CLIPS_EXCEEDED", "PEXELS_RUNTIME_PCT_EXCEEDED", "PEXELS_REUSE_LIMIT_EXCEEDED"}.issubset(set(codes))


def test_m2_request_builders_validate_required_fields_without_execution() -> None:
    settings = _settings()

    luma = LumaHeroVideoRequestBuilder(settings).build({"prompt": "Hero shot", "duration_seconds": 10})
    assert luma.is_valid is False
    assert "LUMA_DURATION_EXCEEDS_MAX" in luma.reason_codes
    assert luma.will_execute is False

    creatomate = CreatomateRenderRequestBuilder(settings).build({"modifications": {"title": "VCOS"}})
    assert creatomate.is_valid is False
    assert "CREATOMATE_TEMPLATE_ID_MISSING" in creatomate.reason_codes

    eleven = ElevenLabsVoiceRequestBuilder(settings).build({"text": "Narration"})
    assert eleven.is_valid is False
    assert "ELEVENLABS_VOICE_ID_MISSING" in eleven.reason_codes
    assert "ELEVENLABS_MODEL_ID_MISSING" in eleven.reason_codes

    pexels = PexelsSearchRequestBuilder(settings).build({"query": "operator dashboard", "usage_role": "factual_evidence"})
    assert pexels.is_valid is False
    assert "PEXELS_USAGE_ROLE_BLOCKED" in pexels.reason_codes
    assert pexels.no_network_call_made is True


def test_m2_preflight_blocks_paid_calls_and_unconfigured_providers() -> None:
    configured = _settings(
        elevenlabs_api_key="sk-eleven-test",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model_id="model-1",
    )
    result = ProviderBoundaryPreflight(configured).check(
        provider_key="elevenlabs",
        provider_capability="VOICE_GENERATION",
        payload={"text": "hello", "voice_id": "voice-1", "model_id": "model-1"},
        human_paid_approval=False,
        real_call_requested=True,
    )
    assert result.blocked is True
    assert "HUMAN_PAID_APPROVAL_MISSING" in result.reason_codes
    assert "PROVIDER_REAL_CALL_BLOCKED_IN_M2" in result.reason_codes
    assert "RENDER_REVISION_REQUIRED_R3D8" in result.reason_codes
    assert result.no_network_call_made is True

    missing = ProviderBoundaryPreflight(_settings()).check(
        provider_key="luma_api",
        provider_capability="AI_HERO_VIDEO",
        payload={"prompt": "hero", "duration_seconds": 8},
        human_paid_approval=True,
        real_call_requested=False,
    )
    assert missing.blocked is True
    assert "BLOCKED_PROVIDER_NOT_CONFIGURED" in missing.reason_codes


def test_m2_cost_placeholder_never_returns_paid_zero_success_when_unconfigured() -> None:
    estimate = ProviderCostEstimatePlaceholder().for_provider("elevenlabs", provider_configured=False)

    assert estimate.status == "ESTIMATE_PENDING_PROVIDER_CONFIG"
    assert estimate.amount is None
    assert estimate.no_paid_zero_success is True


def test_m2_readiness_api_exposes_wiring_without_real_probe(monkeypatch) -> None:
    for key in [
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "ELEVENLABS_MODEL_ID",
        "LUMA_API_KEY",
        "LUMA_HERO_MODEL",
        "CREATOMATE_API_KEY",
        "CREATOMATE_TEMPLATE_ID",
        "CREATOMATE_WORKSPACE_ID",
        "PEXELS_API_KEY",
    ]:
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    monkeypatch.setenv("AI_VIDEO_HERO_PROVIDER", "luma_api")
    monkeypatch.setenv("CLOUD_FINAL_ASSEMBLY_RENDERER", "creatomate_growth_10k")
    monkeypatch.setenv("CLOUD_TEMPLATE_RENDERER", "creatomate_growth_10k")
    monkeypatch.setenv("FREE_VISUAL_FALLBACK_PROVIDER", "pexels_api")
    monkeypatch.setenv("PROVIDER_REAL_READINESS_PROBE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        response = TestClient(create_app()).get("/integrations/provider-wiring")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["snapshot_state"] == "BLOCKED"
    assert payload["real_network_probe_enabled"] is False
    assert payload["no_network_calls_made"] is True
    assert {item["provider_key"] for item in payload["providers"]} >= {"elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"}


def test_m2_m12_media_boundary_still_blocks_safely_with_empty_keys(db_session) -> None:
    fixture = QualificationFactory(db_session).channel_scope(name="M2Boundary")
    package = FirstScriptedVideoPackage(
        channel_id=fixture.channel.id,
        channel_profile_version_id=fixture.profile.id,
        compiled_policy_snapshot_id=fixture.snapshot.id,
        package_status="WAITING_PROVIDER_CONFIG",
        artifacts={
            "narration_script": {"sentences": [{"id": "S1", "text": "Safe package."}]},
            "visual_plan": {"scenes": [{"sentence_id": "S1", "visual_source": "DIAGRAM"}]},
            "thumbnail_brief": {"concept": "manual handoff"},
            "metadata_package": {"title": "Manual package"},
            "rights_disclosure_review": {"result": "OK"},
        },
        limitations=[],
        risk_limitations_summary={},
        next_action="Provider config required.",
    )
    db_session.add(package)
    db_session.flush()

    boundary = FirstScriptedVideoPackageService(db_session, settings=_settings())._create_generation_boundary(
        package=package,
        readiness_snapshot={"provider_summaries": []},
    )

    assert boundary.boundary_status == "BLOCKED_PROVIDER_NOT_CONFIGURED"
    assert boundary.no_provider_calls_confirmed is True
    assert boundary.provider_readiness["elevenlabs"]["status"] in {"NEEDS_CREDENTIAL", "NOT_CONFIGURED"}
    assert boundary.provider_readiness["creatomate_growth_10k"]["status"] in {"NEEDS_CREDENTIAL", "NOT_CONFIGURED"}
    assert boundary.provider_readiness["luma_api"]["required"] is False
    assert db_session.query(VideoGenerationBoundary).count() == 1
    assert db_session.query(ProviderAttempt).count() == 0
    assert db_session.query(RealSmokeRun).count() == 0


def test_m2_no_network_provider_media_upload_or_vector_calls(monkeypatch) -> None:
    install_network_sentinel(monkeypatch)
    settings = _settings()

    ProviderReadinessM2Service(settings).snapshot()
    ElevenLabsVoiceAdapter(settings).prepare({"text": "hello"})
    LumaHeroVideoAdapter(settings).prepare({"prompt": "hero", "duration_seconds": 8})
    PexelsVisualFallbackAdapter(settings).prepare({"query": "desk", "usage_role": "short_broll"})

    source = (ROOT / "app" / "services" / "m2.py").read_text(encoding="utf-8")
    forbidden_fragments = [
        "requests.",
        "httpx.",
        "urlopen",
        "upload_verified",
        "generate_video(",
        "synthesize(",
        "download(",
        "vector_search",
        "vector retrieval",
        "rag retrieval",
        "rag_query",
    ]
    lowered = source.lower()
    assert not any(fragment in lowered for fragment in forbidden_fragments)
    assert json.dumps(ProviderReadinessM2Service(settings).snapshot().model_dump(mode="json"))
