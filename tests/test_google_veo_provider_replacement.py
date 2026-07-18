from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts.asset_acquisition import AIHeroAssetRequest, CompiledAssetRequestPlan
from app.contracts.google_veo import GoogleVeoExecutionGates, GoogleVeoGenerationRequest
from app.core.config import Settings
from app.main import create_app
from app.providers.google_veo import GoogleVeoAdapter, GoogleVeoUnavailableRouter
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.google_veo_rehearsal import GoogleVeoLocalFixtureRehearsal
from app.services.native_render_plan import stable_hash
from app.services.production_archive import ROLE_ARCHIVE_PATHS
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MP4 = ROOT / "tests/fixtures/as1/pexels_supporting_fixture.mp4"


class FakeClient:
    def __init__(self, states=None):
        self.submit_count = 0
        self.poll_count = 0
        self.states = list(states or [{"status": "PROCESSING"}, {"status": "SUCCEEDED", "output_url": "https://example.invalid/out.mp4?token=secret"}])

    def submit(self, request):
        self.submit_count += 1
        return {"operation_id": "fixture-op", "status": "SUBMITTED"}

    def get_operation(self, provider_operation_id):
        self.poll_count += 1
        return self.states[min(self.poll_count - 1, len(self.states) - 1)]


def settings(**changes) -> Settings:
    base = {
        "VCOS_AI_VIDEO_HERO_PROVIDER": "google_veo",
        "VEO_MODEL_ID": "veo-3.1-fast-generate-preview",
        "VEO_DEFAULT_DURATION_SECONDS": 8,
        "VEO_DEFAULT_RESOLUTION": "720p",
        "VEO_DEFAULT_ASPECT_RATIO": "16:9",
        "VEO_DEFAULT_OUTPUT_COUNT": 1,
        "VCOS_VEO_REAL_GENERATION_ENABLED": False,
        "VCOS_PA1R_VEO_SMOKE_ENABLED": False,
    }
    base.update(changes)
    return Settings(_env_file=None, **base)


def generic_request(**changes) -> AIHeroAssetRequest:
    prompt = "Abstract workflow becomes a calm luminous system, no characters or logos"
    payload = {
        "request_id": "ai-hero-1",
        "package_id": "package-1",
        "project_id": "project-1",
        "channel_id": "small-team-ai",
        "scene_id": "scene-1",
        "source_segment_ids": ["segment-1"],
        "visual_intent": "workflow transformation metaphor",
        "hero_reason": "METAPHOR",
        "prompt_text": prompt,
        "prompt_hash": stable_hash(prompt),
        "prompt_safety_status": "PASS",
        "required_duration_seconds": 8,
        "preferred_resolution": "720p",
        "required_aspect_ratio": "16:9",
        "character_policy_mode": "NO_CHARACTER",
        "projected_cost_class": "MEDIUM",
        "human_approval_required": True,
        "provider_resolution_policy_ref": "policy://small-team-ai/strategy-b",
    }
    payload.update(changes)
    return AIHeroAssetRequest(**payload, request_hash=stable_hash(payload))


def request(adapter: GoogleVeoAdapter | None = None, **generic_changes) -> GoogleVeoGenerationRequest:
    adapter = adapter or GoogleVeoAdapter(settings())
    return adapter.build_generation_request(
        generic_request(**generic_changes),
        cost_catalog_ref=GoogleVeoModelPriceCatalog().ref,
        approval_ref="approval://fixture",
        approval_scope="PA1R_ONE_AI_HERO_CLIP",
        idempotency_key="idem-fixture",
    )


def gates(**changes) -> GoogleVeoExecutionGates:
    payload = {
        "provider_boundary_gate_passed": True,
        "human_paid_render_approval_passed": True,
        "cost_estimate_snapshot_passed": True,
        "channel_monthly_budget_gate_passed": True,
        "paid_attempt_limit_gate_passed": True,
        "provider_idempotency_key_valid": True,
        "global_kill_switch_open": True,
        "provider_kill_switch_open": True,
    }
    payload.update(changes)
    return GoogleVeoExecutionGates(**payload)


def test_canonical_stack_and_native_render_authority_are_frozen():
    assert CANONICAL_PROVIDER_KEYS == (
        "elevenlabs",
        "google_veo",
        "google_gemini_image",
        "pexels_api",
    )
    assert "native_ffmpeg_renderer" not in CANONICAL_PROVIDER_KEYS
    assert Path("app/services/native_ffmpeg_renderer.py").is_file()


def test_ai_hero_domain_contracts_are_provider_neutral():
    assert set(CompiledAssetRequestPlan.model_fields) >= {
        "native_request_count",
        "supporting_stock_request_count",
        "ai_hero_request_count",
        "unresolved_request_count",
    }
    assert not any("veo" in name.lower() for name in AIHeroAssetRequest.model_fields)


def test_default_request_is_deterministic_fast_720p_eight_seconds_output_one():
    adapter = GoogleVeoAdapter(settings())
    first = request(adapter)
    second = request(adapter)
    assert first == second
    assert (first.model_id, first.duration_seconds, first.resolution, first.aspect_ratio, first.output_count) == (
        "veo-3.1-fast-generate-preview",
        8,
        "720p",
        "16:9",
        1,
    )


@pytest.mark.parametrize("model_id", ["veo-2.0-generate-001", "veo-3.0-generate-001", "veo-3.0-fast-generate-001"])
def test_deprecated_models_reject(model_id):
    with pytest.raises(ValueError):
        settings(VEO_MODEL_ID=model_id)


def test_duration_resolution_aspect_output_and_character_guards_reject():
    base = request().model_dump()
    for change, code in [
        ({"duration_seconds": 6}, "VEO_DURATION_NOT_APPROVED"),
        ({"resolution": "480p"}, "VEO_RESOLUTION_NOT_SUPPORTED"),
        ({"aspect_ratio": "1:1"}, "literal_error"),
        ({"output_count": 2}, "VEO_PA1R_OUTPUT_COUNT_MUST_EQUAL_ONE"),
        ({"character_policy_mode": "CHARACTER_ALLOWED"}, "VEO_NO_CHARACTER_POLICY_CONFLICT"),
        ({"human_likeness_requested": True}, "VEO_NO_CHARACTER_POLICY_CONFLICT"),
        ({"hero_reason": "FILLER"}, "VEO_HERO_REASON_NOT_APPROVED"),
    ]:
        with pytest.raises(ValueError, match=code):
            GoogleVeoGenerationRequest(**(base | change))


@pytest.mark.parametrize(
    "gate_change",
    [
        {"human_paid_render_approval_passed": False},
        {"cost_estimate_snapshot_passed": False},
        {"channel_monthly_budget_gate_passed": False},
        {"global_kill_switch_open": False},
        {"provider_kill_switch_open": False},
    ],
)
def test_missing_approval_cost_budget_or_kill_switch_causes_no_submit(gate_change):
    fake = FakeClient()
    adapter = GoogleVeoAdapter(settings(), fixture_client=fake)
    receipt = adapter.submit_generation(request(adapter), gates=gates(**gate_change), fixture_only=True)
    assert receipt.provider_call_made is False and receipt.submit_attempt_no == 0 and fake.submit_count == 0


def test_execution_flags_default_false_and_secret_is_redacted():
    configured = GoogleVeoAdapter(settings(GEMINI_API_KEY="fixture-secret")).validate_configuration()
    assert configured["credential_configured"] is True
    assert configured["credential_value_redacted"] is True
    assert configured["execution_enabled"] is False and configured["smoke_enabled"] is False
    assert "fixture-secret" not in json.dumps(configured)


def test_gemini_developer_api_omits_enterprise_only_generate_audio_parameter(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.calls = []

        def generate_videos(self, **kwargs):
            self.calls.append(kwargs)
            assert kwargs["config"].generate_audio is None
            assert kwargs["config"].person_generation == "allow_all"
            return type("Operation", (), {"name": "operations/developer-api-fixture"})()

    models = FakeModels()
    fake_client = type("Client", (), {"models": models})()
    adapter = GoogleVeoAdapter(settings())
    monkeypatch.setattr(adapter, "_official_client", lambda: fake_client)

    operation_id = adapter._submit_with_official_sdk(request(adapter))

    assert operation_id == "operations/developer-api-fixture"
    assert len(models.calls) == 1
    assert request(adapter).generate_audio_expected is True
    assert request(adapter).provider_audio_usage_policy == "DISCARD"
    assert request(adapter).character_policy_mode == "NO_CHARACTER"
    assert all(
        token in request(adapter).negative_prompt
        for token in ("people", "person", "face", "human figure", "presenter", "speaker", "human likeness")
    )
    transport = adapter.transport_config_evidence(request(adapter))
    assert transport["generate_audio_parameter_sent"] is False
    assert transport["generate_audio_value"] is None
    assert transport["person_generation_sent"] == "allow_all"
    assert transport["domain_character_policy"] == "NO_CHARACTER"


def test_duplicate_submit_and_polling_do_not_consume_paid_attempt():
    fake = FakeClient()
    adapter = GoogleVeoAdapter(settings(), fixture_client=fake)
    compiled = request(adapter)
    first = adapter.submit_generation(compiled, gates=gates(), fixture_only=True)
    duplicate = adapter.submit_generation(compiled, gates=gates(), fixture_only=True)
    completed = adapter.poll_operation(first, fixture_only=True)
    assert fake.submit_count == 1 and duplicate.provider_operation_id == first.provider_operation_id
    assert completed.generation_attempts_consumed == 0 and fake.poll_count == 2


def test_timeout_or_failure_never_auto_resubmits_or_uses_another_provider():
    fake = FakeClient(states=[{"status": "PROCESSING"}])
    adapter = GoogleVeoAdapter(settings(), fixture_client=fake)
    submitted = adapter.submit_generation(request(adapter), gates=gates(), fixture_only=True)
    processing = adapter.poll_operation(submitted, max_polls=2, fixture_only=True)
    assert processing.normalized_status == "PROCESSING"
    assert fake.submit_count == 1 and processing.generation_attempts_consumed == 0
    assert not hasattr(adapter, "fallback_provider")


@pytest.mark.parametrize(
    "behavior,decision",
    [
        ("NATIVE_VISUAL_OR_REVIEW", "NATIVE_VISUAL_REQUIRED"),
        ("REVIEW_REQUIRED", "REVIEW_REQUIRED"),
        ("BLOCK", "BLOCK"),
    ],
)
def test_unavailable_routing_uses_native_review_or_block_without_external_fallback(behavior, decision):
    result = GoogleVeoUnavailableRouter().route(
        original_ai_hero_intent_ref="ai-hero-1",
        unavailable_reason="AI_HERO_UNAVAILABLE",
        frozen_policy_behavior=behavior,
        cost_avoided_usd=Decimal("0.80"),
    )
    assert result.decision == decision
    assert result.external_provider_attempted is False and result.external_provider_fallback_used is False


def test_versioned_cost_catalog_estimates_point_eight_and_actual_is_null():
    estimate = GoogleVeoModelPriceCatalog().estimate(
        model_id="veo-3.1-fast-generate-preview",
        resolution="720p",
        duration_seconds=8,
        output_count=1,
        hard_cap=Decimal("1.00"),
        approval_amount=Decimal("1.00"),
    )
    assert estimate.price_catalog_version == "2026-07-12"
    assert estimate.estimated_amount == Decimal("0.80") and estimate.actual_amount is None


def test_fixture_rehearsal_covers_audio_discard_provenance_archive_and_no_execution(tmp_path):
    result = GoogleVeoLocalFixtureRehearsal().run(workspace_root=tmp_path, fixture_mp4=FIXTURE_MP4)
    assert result["verdict"] == "PASS" and result["transport"] == "LOCAL_FIXTURE_ONLY"
    assert result["provider_call_made"] is False and result["actual_cost_usd"] is None
    assert result["duplicate_submit_prevented"] is True and result["generation_attempts_consumed"] == 0
    assert result["provider_audio_present"] is True and result["provider_audio_discarded"] is True
    assert result["normalized_contains_audio_stream"] is False and result["archive_roles_complete"] is True
    manifests = tmp_path / "hpr1-google-veo-fixture/manifests"
    normalize = json.loads((manifests / "media_normalization_manifest.json").read_text())
    assert "-an" in normalize["sanitized_ffmpeg_argv_plan"]
    provenance = json.loads((manifests / "google_veo_provenance_manifest.json").read_text())
    assert provenance["synthetic_media_disclosure_required"] is True
    assert provenance["output_reference"].startswith("volatile://") and "token=" not in json.dumps(provenance)
    archive = json.loads((manifests / "production_archive_manifest.json").read_text())
    assert {item["logical_role"] for item in archive["files"]} == set(ROLE_ARCHIVE_PATHS)
    assert all(result[key] is False for key in (
        "final_media_ref_created",
        "human_upload_task_created",
        "provider_job_snapshot_submitted",
        "paid_provider_call_ledger_executed",
        "channel_or_frozen_context_mutated",
    ))


def test_no_generation_action_endpoint_and_openapi_is_clean():
    schema = create_app().openapi()
    assert not any("veo" in path.lower() and any(method in item for method in ("post", "put", "patch", "delete")) for path, item in schema["paths"].items())
    removed_token = bytes((108, 117, 109, 97)).decode()
    assert removed_token not in json.dumps(schema).lower()
