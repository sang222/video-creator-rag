from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from app.contracts.asset_acquisition import AssetRequest, PexelsDownloadPlan
from app.contracts.google_veo import GoogleVeoExecutionGates
from app.core.config import Settings
from app.providers.google_veo import GoogleVeoAdapter
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.media_normalizer import MediaNormalizer
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.pa1r import (
    APPROVED_ELEVENLABS_MODELS,
    PA1RApprovalScope,
    PA1RCallLedger,
    PA1RExecutionGates,
    PEXELS_CLIENT_HEADERS,
    DrivePA1RArchive,
    ElevenLabsPA1RClient,
    GuardedProviderOperation,
    PexelsPA1RClient,
    archive_permits_cleanup,
    media_qc_permits_archive,
    pa1r_cost_evidence,
    provider_idempotency_key,
    _validate_pa1r_drive_path,
)
from app.services.provider_asset_manifests import build_ai_hero_request
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS
from app.services.production_archive import DriveArchiveFixtureVerifier, ProductionArchiveBuilder, ArchiveSource, ROLE_ARCHIVE_PATHS
from app.services.as1_rehearsal import _native_plan


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/as1"


class FakeTransport:
    def __init__(self):
        self.calls: list[dict] = []

    def json_request(self, method, url, *, headers, payload=None, timeout=30):
        self.calls.append({"kind": "json", "method": method, "url": url, "headers": headers, "payload": payload})
        if "/videos/search" in url:
            return json.loads((FIXTURES / "pexels_response.json").read_text()), {"X-Ratelimit-Remaining": "199"}
        if url.endswith("/user/subscription"):
            return {"character_count": 100, "character_limit": 10000}, {}
        if url.endswith("/voices"):
            return {"voices": [{"voice_id": "premade-1", "name": "Avery", "category": "premade"}]}, {}
        if url.endswith("/models"):
            return [{"model_id": "eleven_multilingual_v2"}], {}
        if "drive/v3/about" in url:
            return {"storageQuota": {"limit": "1000000", "usage": "100"}}, {}
        raise AssertionError(url)

    def bytes_request(self, method, url, *, headers, payload=None, timeout=60):
        self.calls.append({"kind": "bytes", "method": method, "url": url, "headers": headers, "payload": payload})
        return b"fake-mp3-one-output", {"request-id": "request-1"}



class FakeMediaDownloader:
    def __init__(self):
        self.calls = 0

    def download(self, *, plan, context):
        self.calls += 1
        assert context.download_url_hash == plan.download_url_hash
        destination = context.workspace_target_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((FIXTURES / "pexels_supporting_fixture.mp4").read_bytes())
        context.expire()
        return {
            "path": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": _sha(destination),
            "http_evidence": {"redirect_count": 0, "request_header_names": ["Accept", "User-Agent"]},
            "media_probe": {"container": "mp4", "width": 1920, "height": 1080},
        }


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gates(**changes) -> PA1RExecutionGates:
    payload = {name: True for name in PA1RExecutionGates.__dataclass_fields__}
    payload.update(changes)
    return PA1RExecutionGates(**payload)


def _stock_request() -> AssetRequest:
    payload = {
        "request_id": "pa1r-stock",
        "scene_id": "scene-stock",
        "source_segment_ids": ["segment-stock"],
        "purpose": "SUPPORT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": "guarded media workflow team reviewing video operations",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 6,
        "maximum_duration_seconds": 12,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["SUPPORTING_STOCK", "NATIVE_VISUAL"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


@pytest.mark.parametrize(
    "change",
    [
        {"approval_present": False},
        {"credential_ready": False},
        {"billing_quota_ready": False},
        {"global_kill_switch_open": False},
    ],
)
def test_preflight_blockers_make_zero_provider_calls(tmp_path, change):
    calls = []
    ledger = PA1RCallLedger(tmp_path / "ledger.json")
    ledger.plan("voice", provider="elevenlabs", operation="tts", paid=True, idempotency_key="idem")
    result = GuardedProviderOperation(ledger).run("voice", gates=_gates(**change), operation=lambda: calls.append(1))
    assert result["status"] == "BLOCKED"
    assert result["provider_call_made"] is False
    assert calls == []


def test_pexels_uses_authorization_header_and_exactly_one_download(tmp_path):
    transport = FakeTransport()
    media = FakeMediaDownloader()
    client = PexelsPA1RClient(transport, media)
    evidence, candidate, context = client.search_select_once(
        api_key="secret-not-persisted",
        request=_stock_request(),
        workspace_directory=tmp_path,
    )
    plan = PexelsDownloadPlan(**evidence["download_plan"])
    assert "video_files" not in candidate
    receipt = client.download_once(plan=plan, execution_context=context, request_id="pa1r-stock")
    assert client.search_flow_count == 1 and client.selected_download_count == 1
    assert transport.calls[0]["headers"] == {
        "Authorization": "secret-not-persisted",
        **PEXELS_CLIENT_HEADERS,
    }
    assert "/v1/videos/search?" in transport.calls[0]["url"] and "api_key" not in transport.calls[0]["url"].lower()
    assert media.calls == 1 and len(transport.calls) == 1
    assert receipt.sha256 and receipt.provider_call_made is True
    assert "secret-not-persisted" not in json.dumps(evidence)
    with pytest.raises(RuntimeError, match="DOWNLOAD_LIMIT"):
        client.download_once(plan=plan, execution_context=context, request_id="second")


def test_pexels_http_failure_persists_only_redacted_diagnostics(tmp_path):
    secret = "pexels-secret-must-not-persist"

    class RejectingTransport(FakeTransport):
        def json_request(self, method, url, *, headers, payload=None, timeout=30):
            self.calls.append({"kind": "json", "method": method, "url": url, "headers": headers})
            response_headers = {
                "Content-Type": "application/json",
                "X-Request-Id": "request-safe",
                "CF-Ray": "ray-safe",
                "Set-Cookie": "must-not-persist",
            }
            raise urllib.error.HTTPError(
                url,
                403,
                "Forbidden",
                response_headers,
                io.BytesIO(f'{{"error":"denied", "ref":"{secret}"}}'.encode()),
            )

    ledger = PA1RCallLedger(tmp_path / "ledger.json")
    ledger.plan("pexels", provider="pexels_api", operation="search", paid=False, idempotency_key="idem")
    client = PexelsPA1RClient(RejectingTransport())
    with pytest.raises(RuntimeError, match="PEXELS_HTTP_403"):
        GuardedProviderOperation(ledger).run(
            "pexels",
            gates=_gates(),
            operation=lambda: client.search_select_once(
                api_key=secret,
                request=_stock_request(),
                workspace_directory=tmp_path,
            ),
        )
    persisted = json.loads(ledger.path.read_text())
    evidence = persisted["entries"]["pexels"]["evidence"]["provider_http_error"]
    serialized = json.dumps(persisted)
    assert evidence["http_status"] == 403
    assert evidence["response_headers"]["x-request-id"] == "request-safe"
    assert "set-cookie" not in evidence["response_headers"]
    assert secret not in serialized and "must-not-persist" not in serialized


def test_elevenlabs_one_bounded_existing_voice_generation(tmp_path):
    transport = FakeTransport()
    client = ElevenLabsPA1RClient(transport)
    readiness = client.readiness(api_key="secret", required_characters=200)
    result = client.generate_once(
        api_key="secret",
        voice_id=readiness["voice_id"],
        model_id=readiness["model_id"],
        text="A bounded non-production smoke narration.",
        destination=tmp_path / "narration.mp3",
    )
    assert readiness["credits_available"] is True
    assert readiness["voice_category"] == "premade" and readiness["model_id"] in APPROVED_ELEVENLABS_MODELS
    assert client.generation_count == 1 and result["generation_count"] == 1
    assert result["production_eligible"] is False and result["not_publishable"] is True
    with pytest.raises(RuntimeError, match="GENERATION_LIMIT"):
        client.generate_once(api_key="secret", voice_id="premade-1", model_id="eleven_multilingual_v2", text="x", destination=tmp_path / "two.mp3")


class FakeVeo:
    def __init__(self):
        self.submits = 0
        self.polls = 0

    def submit(self, request):
        self.submits += 1
        return {"operation_id": "op-one", "status": "SUBMITTED"}

    def get_operation(self, provider_operation_id):
        self.polls += 1
        return {"status": "SUCCEEDED", "output_url": "https://invalid.example/output?volatile=1"}


def test_veo_one_submit_polling_and_duplicate_are_idempotent():
    settings = Settings(
        _env_file=None,
        VCOS_AI_VIDEO_HERO_PROVIDER="google_veo",
        VEO_MODEL_ID="veo-3.1-fast-generate-preview",
        VEO_DEFAULT_DURATION_SECONDS=8,
        VEO_DEFAULT_RESOLUTION="720p",
        VEO_DEFAULT_ASPECT_RATIO="16:9",
        VEO_DEFAULT_OUTPUT_COUNT=1,
    )
    generic = build_ai_hero_request(
        _stock_request().model_copy(update={"request_id": "hero", "scene_id": "hero-scene", "purpose": "METAPHOR", "requested_role": "AI_HERO", "projected_cost_class": "MEDIUM"}),
        package_id="pkg",
        project_id="project",
        channel_id="channel",
        prompt_text="Abstract guarded luminous flow, no people, no logos",
        provider_resolution_policy_ref="policy://pa1r",
    )
    fake = FakeVeo()
    adapter = GoogleVeoAdapter(settings, fixture_client=fake)
    request = adapter.build_generation_request(
        generic,
        cost_catalog_ref=GoogleVeoModelPriceCatalog().ref,
        approval_ref="approval://pa1r",
        approval_scope="PA1R_ONE_AI_HERO_CLIP",
        idempotency_key="idem-one",
    )
    gates = GoogleVeoExecutionGates(**{name: True for name in GoogleVeoExecutionGates.model_fields if name != "approved_production_execution_scope"})
    first = adapter.submit_generation(request, gates=gates, fixture_only=True)
    duplicate = adapter.submit_generation(request, gates=gates, fixture_only=True)
    completed = adapter.poll_operation(first, max_polls=2, fixture_only=True)
    assert fake.submits == 1 and duplicate.provider_operation_id == first.provider_operation_id
    assert fake.polls == 1 and completed.generation_attempts_consumed == 0
    assert completed.normalized_status == "SUCCEEDED" and completed.output_reference.startswith("volatile://")


def test_failure_cannot_be_rendered_as_provider_pass_and_no_retry(tmp_path):
    ledger = PA1RCallLedger(tmp_path / "ledger.json")
    ledger.plan("veo", provider="google_veo", operation="generate", paid=True, idempotency_key="idem")
    boundary = GuardedProviderOperation(ledger)
    with pytest.raises(RuntimeError, match="provider failed"):
        boundary.run("veo", gates=_gates(), operation=lambda: (_ for _ in ()).throw(RuntimeError("provider failed")))
    assert ledger.entries["veo"]["status"] == "FAILED" and ledger.entries["veo"]["attempt_count"] == 1
    duplicate = boundary.run("veo", gates=_gates(), operation=lambda: {"unexpected": True})
    assert duplicate["status"] == "BLOCKED" and duplicate["provider_call_made"] is False


def test_no_external_ai_video_fallback_and_provider_audio_is_removed(tmp_path):
    manifest = MediaNormalizer().compile_video_plan(
        input_asset_ref="veo",
        input_asset_hash="hash",
        input_path=tmp_path / "veo.mp4",
        output_path=tmp_path / "veo-muted.mp4",
        width=1920,
        height=1080,
        trim_end_seconds=6,
        provider_audio_present=True,
    )
    assert CANONICAL_PROVIDER_KEYS == ("elevenlabs", "google_veo", "pexels_api")
    assert "-an" in manifest.sanitized_ffmpeg_argv_plan
    assert manifest.normalization_profile["provider_audio_discarded"] is True
    assert manifest.normalization_profile["narration_authority"] == "ELEVENLABS"
    assert manifest.expected_output_shape["contains_audio_stream"] is False


def test_normalization_and_native_compile_accept_only_resolved_smoke_assets(tmp_path):
    plan = _native_plan(project_id="pa1r-project", package_id="pa1r-package")
    captions = tmp_path / "captions.srt"
    captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nSmoke\n")
    plan.srt_ref = str(captions)
    plan.srt_hash = _sha(captions)
    plan.content_hash = canonical_plan_hash(plan)
    compiled = NativeMotionCompiler().compile(plan, allow_resolved_provider_assets=True)
    assert compiled.production_eligible is False
    assert compiled.unresolved_inputs == []
    assert any(scene["visual_treatment"] == "STOCK_VIDEO" for scene in compiled.compiled_scenes)
    assert any(scene["visual_treatment"] == "AI_HERO_VIDEO" for scene in compiled.compiled_scenes)


def test_media_qc_gate_and_archive_mismatch_block_cleanup(tmp_path):
    sources = []
    for role, archive_path in ROLE_ARCHIVE_PATHS.items():
        path = tmp_path / role.lower()
        path.write_text(role)
        sources.append(ArchiveSource(role, path, archive_path))
    manifest = ProductionArchiveBuilder().build(manifest_id="manifest", project_id="project", package_id="package", sources=sources)
    metadata = [{"archive_path": item.expected_archive_path, "size_bytes": item.size_bytes, "sha256": item.sha256} for item in manifest.files]
    metadata[0]["sha256"] = "mismatch"
    receipt = DriveArchiveFixtureVerifier().verify(
        manifest=manifest,
        configured_root_folder_id_reference="configured://root",
        root_relative_folder_path="smoke_tests/2026-07-12/pa1r/run",
        fixture_files=metadata,
    )
    assert media_qc_permits_archive("FAIL") is False and media_qc_permits_archive("PASS") is True
    assert receipt.archive_state == "FAILED" and archive_permits_cleanup(receipt) is False


def test_drive_path_is_root_relative_and_nested_root_is_rejected():
    _validate_pa1r_drive_path("smoke_tests/2026-07-12/pa1r/run-1")
    with pytest.raises(ValueError):
        _validate_pa1r_drive_path("VCOS/smoke_tests/2026-07-12/pa1r/run-1")
    with pytest.raises(ValueError):
        _validate_pa1r_drive_path("/smoke_tests/2026-07-12/pa1r/run-1")


def test_cost_approval_idempotency_and_no_publish_scope():
    settings = Settings(_env_file=None, VCOS_ELEVENLABS_MONTHLY_CAP_USD="22", VCOS_ELEVENLABS_MONTHLY_CREDIT_CAP="121000")
    cost = pa1r_cost_evidence(settings)
    approval = PA1RApprovalScope().evidence()
    idem1 = provider_idempotency_key("run", "google_veo", "generate", {"model": "fast"})
    idem2 = provider_idempotency_key("run", "google_veo", "generate", {"model": "fast"})
    assert cost["estimated_total"] < cost["hard_cap"] == 3.0
    assert approval["youtube_allowed"] is False and approval["production_promotion_allowed"] is False
    assert approval["automatic_retry_allowed"] is False and approval["max_veo_generations"] == 1
    assert idem1 == idem2


def test_approval_evidence_can_be_bound_to_one_fresh_run():
    approval = PA1RApprovalScope(
        approval_ref="operator-chat-pa1r-approval://pa1r-fresh-run",
        approved_at="2026-07-13T23:30:00+07:00",
    ).evidence()
    assert approval["approval_ref"].endswith("pa1r-fresh-run")
    assert approval["approved_at"] == "2026-07-13T23:30:00+07:00"
    assert approval["max_veo_generations"] == 1


def test_pa1r_source_has_no_forbidden_publishing_or_frozen_context_mutation():
    source = (ROOT / "app/services/pa1r.py").read_text()
    forbidden_models = ("FinalMediaRef", "HumanUploadTask", "UploadedVideo", "ChannelProfileVersion", "EffectiveChannelRuntimeContextSnapshot", "FormatIdentityContract", "LearningToMemoryPromotionRun")
    assert all(name not in source for name in forbidden_models)
    assert "youtube.com/upload" not in source.lower()
    retired = tuple("".join(parts) for parts in (("crea", "tomate"), ("lu", "ma"), ("run", "way"), ("kli", "ng"), ("so", "ra")))
    assert all(name not in source.lower() for name in retired)


def test_drive_quota_probe_is_read_only_and_secret_free():
    transport = FakeTransport()
    archive = object.__new__(DrivePA1RArchive)
    result = DrivePA1RArchive.quota_readiness(archive, access_token="secret", transport=transport)
    assert result["quota_available"] is True and result["readiness_probe_only"] is True
    assert transport.calls[0]["method"] == "GET"
    assert "secret" not in json.dumps(result)
