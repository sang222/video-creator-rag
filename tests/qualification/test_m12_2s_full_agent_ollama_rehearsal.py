from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.m12_2 import FirstScriptedVideoPackageRequest
from app.core.config import Settings
from app.db.models import (
    CompiledChannelPolicySnapshot,
    HumanUploadTask,
    LLMRunSnapshot,
    MediaRenderJob,
    PromptAuditSnapshot,
    PromptRenderRun,
    ProviderAttempt,
    RealSmokeRun,
    VideoGenerationBoundary,
)
from app.main import create_app
from app.providers.ollama import OllamaLLMProvider
from app.services.m10_1 import LLMRouterService
from app.services.m12_2 import FirstScriptedVideoPackageService, FULL_REHEARSAL_AGENT_CHAIN


class FakeRouter:
    def __init__(self, outputs: list[dict[str, Any]]):
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def route(self, **kwargs) -> LLMRouteResponse:
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        return LLMRouteResponse(
            status="SUCCESS",
            lane_name=kwargs["lane_name"],
            selected_model="test-router-model",
            fallback_level="PRIMARY",
            content=json.dumps(output),
            structured_output=output,
            route_attempt_id=uuid.uuid4(),
            provider_attempt_id=uuid.uuid4(),
            llm_run_snapshot_id=uuid.uuid4(),
            reason_codes=["TEST_LLM_ROUTE"],
        )


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "production_prompt_activation_enabled": True,
        "real_llm_package_run_enabled": True,
        "real_ollama_agent_run_enabled": True,
        "media_provider_calls_disabled": True,
        "upload_and_publish_disabled": True,
        "old_provider_smoke_disabled": True,
        "llm_provider": "ollama",
        "llm_real_execution_enabled": True,
        "llm_router_real_smoke": False,
        "elevenlabs_api_key": None,
        "elevenlabs_plan": None,
        "creatomate_api_key": None,
        "creatomate_plan": None,
        "ai_hero_provider": None,
        "veo_real_execution_enabled": False,
        "veo_real_smoke": False,
    }
    base.update(overrides)
    return Settings(**base)


def _complete_scope(qualification_factory):
    scope = qualification_factory.channel_scope(name="M12.2S")
    scope.channel.primary_language = "vi"
    scope.channel.primary_region = "VN"
    scope.channel.primary_timezone = "Asia/Ho_Chi_Minh"
    scope.channel.target_regions = ["VN"]
    scope.channel.metadata_ = {"operator_language": "vi"}
    return scope


def _request(channel_id: uuid.UUID) -> FirstScriptedVideoPackageRequest:
    return FirstScriptedVideoPackageRequest(
        channel_id=channel_id,
        topic="Cách kiểm soát agent video AI không gọi provider media khi chưa cấu hình",
        research_pack_text=(
            "Operator note: VCOS đã có channel contract COMPLETE, prompt registry, LLMRouter Ollama, "
            "manual publish handoff, và provider media chưa được cấu hình."
        ),
        research_pack_ref="operator_research_pack:m12_2s",
        target_video_type="long_form",
        package_title_seed="VCOS M12.2S rehearsal",
    )


def _envelope(agent_key: str, artifact: dict[str, Any], *, status: str = "OK") -> dict[str, Any]:
    return {
        "contract_version": "m12.1.0",
        "agent_key": agent_key,
        "status": status,
        "confidence_label": "HIGH",
        "risk_level": "LOW",
        "evidence_refs": [{"type": "operator_research_pack", "id": "m12_2s"}],
        "limitations": ["Human review required before media generation."],
        "next_action": "Review package before media provider setup.",
        "operator_summary_vi": f"{agent_key} đã chạy bằng Ollama.",
        "technical_appendix": {"test_output": True},
        "artifact": artifact,
    }


def _outputs(*, gatekeeper_result: str = "PASS", invalid_agent: str | None = None) -> list[dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {
        "ChannelAuthorityAgent": {"decision": "ADMIT", "reason": "Fits COMPLETE channel contract."},
        "TopicIdeaScoringAgent": {"score": 88, "risk": "LOW", "cost": "LOW"},
        "ResearchPackSummarizer": {
            "facts": ["VCOS routes agent calls through LLMRouter."],
            "assumptions": ["Media providers are not configured."],
            "open_questions": ["Operator must choose paid providers later."],
            "conflicts": [],
            "evidence_refs": [{"id": "m12_2s"}],
        },
        "ScriptPlanningAgent": {
            "hook": "Agent đã chạy thật nhưng dừng đúng chỗ.",
            "problem": "Provider media chưa cấu hình.",
            "mechanism": "LLMRouter + prompt snapshots + boundary.",
            "result": "Text package ready for review.",
            "takeaway": "Không fake media QC.",
        },
        "ScriptWriterAgent": {
            "sentences": [
                {"sentence_id": "S1", "text": "VCOS bắt đầu từ channel contract đã COMPLETE.", "approx_seconds": 5},
                {"sentence_id": "S2", "text": "Agent chạy qua Ollama nhưng không gọi provider media.", "approx_seconds": 5},
            ]
        },
        "PublishingMetadataAgent": {
            "title": "VCOS M12.2S: rehearsal tới media boundary",
            "description": "Paste-ready metadata, no upload.",
            "chapters": [{"time": "00:00", "title": "Hook"}],
            "tags": ["VCOS", "Ollama"],
            "pinned_comment": "Review trước khi cấu hình provider.",
            "disclosure_notes": ["AI-assisted draft."],
        },
        "VisualPlanningAgent": {
            "scenes": [
                {"sentence_id": "S1", "intended_visual_source": "DIAGRAM"},
                {"sentence_id": "S2", "intended_visual_source": "CARD"},
                {"sentence_id": "S2", "intended_visual_source": "VEO_HERO_CANDIDATE_ONLY"},
            ],
            "media_provider_calls": "NONE",
        },
        "ThumbnailBriefAgent": {
            "variants": [{"concept": "Boundary stop", "text": "Dừng đúng chỗ", "style": "clear operator board"}],
            "rendered": False,
        },
        "RightsDisclosureReviewer": {
            "result": "REVIEW_REQUIRED",
            "source_manifest_status": "OPERATOR_NOTES_ONLY",
            "ai_disclosure_needed": True,
            "rights_risk": "MEDIUM",
        },
        "GatekeeperSoftReviewAgent": {"result": gatekeeper_result, "findings": []},
        "UploadCardCopyAgent": {"title": "VCOS M12.2S", "description": "Paste-ready only.", "not_uploaded": True},
        "ProviderReadinessSummaryAgent": {
            "providers": {
                "elevenlabs": "NEEDS_CREDENTIAL",
                "creatomate": "NEEDS_CREDENTIAL",
                "veo": "NOT_CONFIGURED_OPTIONAL",
            }
        },
        "MediaQCExplanationAgent": {
            "status": "WAITING_MEDIA_GENERATION",
            "reason": "No media file exists in M12.2S.",
            "fake_qc_pass": False,
        },
    }
    outputs: list[dict[str, Any]] = []
    for step in FULL_REHEARSAL_AGENT_CHAIN:
        envelope = _envelope(step.agent_key, artifacts[step.agent_key])
        if invalid_agent == step.agent_key:
            envelope["unexpected"] = True
        outputs.append(envelope)
    return outputs


def test_m12_2s_complete_contract_runs_full_rehearsal_to_provider_boundary(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    router = FakeRouter(_outputs())

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(_request(scope.channel.id))

    assert package.package_status == "READY_FOR_MEDIA_PROVIDERS"
    assert len(router.calls) == len(FULL_REHEARSAL_AGENT_CHAIN)
    assert {call["messages"][0]["role"] for call in router.calls} == {"system"}
    assert {call["messages"][1]["role"] for call in router.calls} == {"user"}
    assert len(package.prompt_render_run_refs) == len(FULL_REHEARSAL_AGENT_CHAIN)
    assert len(package.prompt_audit_snapshot_refs) >= len(FULL_REHEARSAL_AGENT_CHAIN)
    assert any(ref["agent_key"] == "ScriptRewriteAgent" and ref["route_status"] == "SKIPPED_SAFE" for ref in package.agent_run_refs)
    assert package.artifacts["visual_plan"]["scenes"][0]["intended_visual_source"] == "DIAGRAM"
    assert package.artifacts["thumbnail_brief"]["rendered"] is False
    assert package.artifacts["media_qc_explanation"]["status"] == "WAITING_MEDIA_GENERATION"
    assert package.risk_limitations_summary["mock_fallback_used"] is False
    assert package.risk_limitations_summary["dry_run_success_used"] is False
    assert package.risk_limitations_summary["media_provider_calls_made"] is False
    assert package.risk_limitations_summary["upload_or_publish_calls_made"] is False

    boundary = db_session.query(VideoGenerationBoundary).one()
    assert boundary.package_id == package.id
    assert boundary.boundary_status == "BLOCKED_PROVIDER_NOT_CONFIGURED"
    assert boundary.no_provider_calls_confirmed is True
    assert boundary.provider_readiness["elevenlabs"]["status"] in {"NEEDS_CREDENTIAL", "NOT_CONFIGURED"}
    assert boundary.provider_readiness["creatomate"]["status"] in {"NEEDS_CREDENTIAL", "NOT_CONFIGURED"}
    assert boundary.provider_readiness["veo"]["required"] is False
    assert boundary.operator_summary_vi == (
        "Gói nội dung đã sẵn sàng tới bước tạo media, nhưng chưa thể generate video vì chưa cấu hình provider voice/render/AI hero."
    )
    assert boundary.next_action == "Cấu hình Creatomate và ElevenLabs trước; Veo là optional cho hero shot."
    assert db_session.query(MediaRenderJob).count() == 0
    assert db_session.query(HumanUploadTask).count() == 0
    assert db_session.query(RealSmokeRun).count() == 0


def test_m12_2s_partial_contract_blocks_before_llm(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    payload = dict(scope.snapshot.compiled_payload)
    contract = dict(payload["channel_contract_json"])
    contract["contract_status"] = "PARTIAL"
    payload["channel_contract_json"] = contract
    partial_snapshot = CompiledChannelPolicySnapshot(
        channel_workspace_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compile_run_id=None,
        snapshot_version=2,
        status="active",
        compiler_version="m12.2s-test",
        capability_matrix_version="test",
        compiled_payload=payload,
        content_hash=f"partial-{uuid.uuid4().hex}",
        profile_input_hash=scope.profile.profile_input_hash,
    )
    db_session.add(partial_snapshot)
    db_session.flush()
    scope.channel.active_policy_snapshot_id = partial_snapshot.id
    db_session.flush()
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(_request(scope.channel.id))

    assert package.package_status == "BLOCKED"
    assert package.artifacts["channel_contract_review"]["reason_codes"] == ["CHANNEL_CONTRACT_INCOMPLETE"]
    assert package.prompt_render_run_refs == []
    assert router.calls == []


def test_m12_2s_missing_topic_blocks_before_llm(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    request = _request(scope.channel.id).model_copy(update={"topic": None})
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(request)

    assert package.package_status == "BLOCKED"
    assert package.artifacts["topic"]["status"] == "NEEDS_TOPIC"
    assert router.calls == []


def test_m12_2s_real_ollama_disabled_returns_not_configured(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(real_ollama_agent_run_enabled=False, llm_real_execution_enabled=False),
        llm_router=router,
    ).rehearse_full(_request(scope.channel.id))

    assert package.package_status == "NOT_CONFIGURED"
    missing = package.artifacts["llm_readiness"]["missing_or_invalid_flags"]
    assert "VCOS_ENABLE_REAL_OLLAMA_AGENT_RUN" in missing
    assert "VCOS_LLM_REAL_EXECUTION_ENABLED" in missing
    assert router.calls == []


def test_m12_2s_llmrouter_real_path_creates_provider_and_llm_snapshots(db_session, qualification_factory, monkeypatch) -> None:
    scope = _complete_scope(qualification_factory)
    outputs = _outputs()

    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("VCOS_LLM_PROVIDER", "ollama")

    def transport(method: str, url: str, payload: dict[str, Any] | None, timeout_seconds: int) -> tuple[int, dict[str, Any]]:
        assert method == "POST"
        assert url.endswith("/api/chat")
        assert payload is not None
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        output = outputs.pop(0)
        return 200, {
            "model": payload["model"],
            "message": {"content": json.dumps(output)},
            "prompt_eval_count": 12,
            "eval_count": 34,
            "total_duration": 3_000_000,
        }

    provider = OllamaLLMProvider(base_url="http://ollama.test", transport=transport)
    router = LLMRouterService(db_session, provider=provider)

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(_request(scope.channel.id))

    assert package.package_status == "READY_FOR_MEDIA_PROVIDERS"
    assert db_session.query(ProviderAttempt).filter(ProviderAttempt.provider_key == "OLLAMA").count() == len(FULL_REHEARSAL_AGENT_CHAIN)
    assert db_session.query(LLMRunSnapshot).filter(LLMRunSnapshot.provider == "ollama").count() == len(FULL_REHEARSAL_AGENT_CHAIN)
    forbidden_attempts = db_session.query(ProviderAttempt).filter(ProviderAttempt.provider_key.in_(["ELEVENLABS", "CREATOMATE", "GOOGLE_VERTEX_VEO", "GOOGLE_DRIVE", "YOUTUBE"])).all()
    assert forbidden_attempts == []


@pytest.mark.parametrize("gatekeeper_result, expected_status", [("BLOCK", "BLOCKED"), ("REVIEW_REQUIRED", "REVIEW_REQUIRED")])
def test_m12_2s_gatekeeper_stops_or_marks_review_required(db_session, qualification_factory, gatekeeper_result, expected_status) -> None:
    scope = _complete_scope(qualification_factory)

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(_outputs(gatekeeper_result=gatekeeper_result)),
    ).rehearse_full(_request(scope.channel.id))

    assert package.package_status == expected_status
    assert "upload_card_copy" not in package.artifacts
    assert db_session.query(HumanUploadTask).count() == 0


def test_m12_2s_invalid_output_sets_review_required(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(_outputs(invalid_agent="ScriptWriterAgent")),
    ).rehearse_full(_request(scope.channel.id))

    assert package.package_status == "REVIEW_REQUIRED"
    assert "validation_result" in package.artifacts["narration_script"]
    assert len(package.prompt_render_run_refs) == 5
    assert db_session.query(PromptAuditSnapshot).count() >= 5


def test_m12_2s_thumbnail_and_media_qc_schema_guards(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    outputs = _outputs()
    outputs[7]["artifact"]["image_url"] = "https://example.invalid/rendered.png"

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(_request(scope.channel.id))

    assert package.package_status == "REVIEW_REQUIRED"
    assert package.artifacts["thumbnail_brief_review"]["reason_codes"] == ["THUMBNAIL_RENDER_NOT_ALLOWED"]
    assert db_session.query(MediaRenderJob).count() == 0


def test_m12_2s_package_retrieval_agent_runs_and_boundary(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    service = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(_outputs()))
    package = service.rehearse_full(_request(scope.channel.id))

    retrieved = service.get(package.id)
    agent_runs = service.agent_runs(package.id)
    boundary = service.generation_boundary(package.id)

    assert retrieved.artifacts["video_generation_boundary_ref"] == str(boundary.id)
    assert agent_runs.package_id == package.id
    assert len(agent_runs.agent_runs) == len(FULL_REHEARSAL_AGENT_CHAIN) + 1
    assert agent_runs.provider_attempt_refs
    assert agent_runs.llm_run_snapshot_refs
    assert boundary.boundary_status == "BLOCKED_PROVIDER_NOT_CONFIGURED"


def test_m12_2s_api_routes_exist_and_no_old_provider_smoke_path(db_session) -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    assert "/video-packages/rehearse-full" in paths
    assert "/video-packages/{package_id}" in paths
    assert "/video-packages/{package_id}/agent-runs" in paths
    assert "/video-packages/{package_id}/generation-boundary" in paths
    source = Path("app/services/m12_2.py").read_text(encoding="utf-8")
    forbidden = ["app.providers.mock", "RealSmokeOrchestratorService", "GoogleDriveUploadService", "YouTubeUpload"]
    assert [token for token in forbidden if token in source] == []
    assert db_session.query(PromptRenderRun).count() == 0
