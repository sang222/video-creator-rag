from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.m12_2 import FirstScriptedVideoPackageRequest
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.workflow import VideoProjectCreate
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import (
    AgentContextPackSnapshot,
    CompiledChannelPolicySnapshot,
    HumanUploadTask,
    LLMRunSnapshot,
    MediaRenderJob,
    PromptAuditSnapshot,
    PromptRenderRun,
    ProviderAttempt,
    RealSmokeRun,
    VideoGenerationBoundary,
    VideoProject,
)
from app.main import create_app
from app.providers.ollama import OllamaLLMProvider
from app.services import R3D1AdminService, VideoProjectService
from app.services.m10_1 import LLMRouterService
from app.services.m12_2 import (
    FirstScriptedVideoPackageService,
    FULL_REHEARSAL_AGENT_CHAIN,
    _find_visual_source_values,
    _repair_visual_unknown_sentence_refs,
)
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler


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


def _project_with_effective_context(db_session, scope) -> VideoProject:
    category = R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"m122s-{uuid.uuid4().hex[:8]}",
            name="M12.2S Category",
            default_format_policy_json={"target_duration_seconds": 540, "structure": ["hook", "problem", "mechanism", "takeaway"]},
            default_visual_style_json={"style_note": "operator dashboard cards"},
            default_voice_style_json={"tone": "calm"},
            default_thumbnail_style_json={"style": "clear operator board"},
            visual_mode="DIAGRAM_FIRST",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )
    project_read = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id,
            title="M12.2S rehearsal project",
            description="Qualification fixture for R3D3 context pack.",
            created_by_user_id=scope.operator.id,
        )
    )
    project = db_session.get(VideoProject, project_read.id)
    snapshot = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    assert snapshot.compile_status == "PASS"
    return project


def _request(channel_id: uuid.UUID, *, video_project_id: uuid.UUID | None = None) -> FirstScriptedVideoPackageRequest:
    return FirstScriptedVideoPackageRequest(
        channel_id=channel_id,
        video_project_id=video_project_id,
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
        "evidence_refs": [{"type": "operator_research_pack", "id": "m12_2s"}],
        "limitations": ["Human review required before media generation."],
        "next_action": "Review package before media provider setup.",
        "operator_summary_vi": f"{agent_key} đã chạy bằng Ollama.",
        "technical_appendix": {"test_output": True},
        "artifact": artifact,
    }


def _long_script_sentences(count: int = 36) -> list[dict[str, Any]]:
    text = (
        "This qualification narration sentence keeps the package evidence bound, describes the manual review boundary, "
        "avoids provider execution, preserves channel contract references, explains operator safeguards, and remains long enough "
        "for deterministic duration validation without adding claims or media."
    )
    return [{"sentence_id": f"S{index}", "text": text, "approx_seconds": 15} for index in range(1, count + 1)]


def _underlong_script_sentences(count: int = 42) -> list[dict[str, Any]]:
    text = "This narration keeps review boundaries clear, preserves the hook promise, and avoids provider execution today."
    return [{"sentence_id": f"S{index}", "text": text, "approx_seconds": 4.7} for index in range(1, count + 1)]


def _visual_scenes(count: int = 36) -> list[dict[str, Any]]:
    return [
        {"sentence_id": f"S{index}", "intended_visual_source": "DIAGRAM" if index % 2 else "CARD"}
        for index in range(1, count + 1)
    ]


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
            "duration_model": {
                "target_format": "long_form",
                "target_duration_seconds": 540,
                "allowed_duration_range_seconds": {"min": 486, "max": 594},
                "narration_words_target": 1260,
                "words_per_minute_assumption": 140,
            },
            "section_budgets": [
                {"section": "hook", "target_seconds": 60, "target_words": 140},
                {"section": "mechanism", "target_seconds": 390, "target_words": 910},
                {"section": "takeaway", "target_seconds": 90, "target_words": 210},
            ],
            "hook_spec": {
                "hook_type": "DIRECT",
                "first_3_seconds_script": "VCOS bắt đầu từ channel contract đã COMPLETE.",
                "first_3_seconds_visual": "Operator cockpit shows contract, Ollama, and provider boundary.",
                "promise_made": "Agent chạy qua Ollama nhưng không gọi provider media",
                "payoff_location": "S2",
                "clickbait_risk": "LOW",
                "visual_hook_relevance": "Visual shows the same Ollama and boundary flow.",
                "title_hook_alignment": "Title promises a rehearsal to media boundary.",
            },
        },
        "ScriptWriterAgent": {
            "hook_spec": {
                "hook_type": "DIRECT",
                "first_3_seconds_script": "VCOS bắt đầu từ channel contract đã COMPLETE.",
                "first_3_seconds_visual": "Operator cockpit shows contract, Ollama, and provider boundary.",
                "promise_made": "Agent chạy qua Ollama nhưng không gọi provider media",
                "payoff_location": "S2",
                "clickbait_risk": "LOW",
                "visual_hook_relevance": "Visual shows the same Ollama and boundary flow.",
                "title_hook_alignment": "Title promises a rehearsal to media boundary.",
            },
            "sentences": _long_script_sentences(),
            "total_approx_seconds": 540,
            "duration_self_check": {
                "actual_total_seconds": 540,
                "target_seconds": 540,
                "min_seconds": 486,
                "max_seconds": 594,
                "coverage_ratio": 1.003,
                "sentence_count": 36,
                "narration_word_count": 1264,
                "minimum_word_count": 1134,
            },
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
            "scenes": _visual_scenes(),
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
            "disclosure_notes": "Future generated media still needs source/provider manifest review.",
        },
        "GatekeeperSoftReviewAgent": {"result": gatekeeper_result, "findings": []},
        "UploadCardCopyAgent": {
            "title": "VCOS M12.2S",
            "description": "Paste-ready only. Disclosure: AI-assisted draft; future generated media still needs provider review.",
            "not_uploaded": True,
            "disclosure_refs": ["rights_disclosure_review"],
        },
        "ProviderReadinessSummaryAgent": {
            "providers": {
                "elevenlabs": "NEEDS_CREDENTIAL",
                "luma_api": "NOT_CONFIGURED_OPTIONAL",
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
    project = _project_with_effective_context(db_session, scope)
    router = FakeRouter(_outputs())

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    assert len(router.calls) == len(FULL_REHEARSAL_AGENT_CHAIN)
    assert {call["messages"][0]["role"] for call in router.calls} == {"system"}
    assert {call["messages"][1]["role"] for call in router.calls} == {"user"}
    assert all("previous_artifacts" not in call["messages"][1]["content"] for call in router.calls)
    assert all("channel_contract_json:" not in call["messages"][1]["content"] for call in router.calls)
    assert all("compiled_policy_snapshot_json:" not in call["messages"][1]["content"] for call in router.calls)
    assert len(package.prompt_render_run_refs) == len(FULL_REHEARSAL_AGENT_CHAIN)
    assert len(package.prompt_audit_snapshot_refs) >= len(FULL_REHEARSAL_AGENT_CHAIN)
    assert any(ref["agent_key"] == "ScriptRewriteAgent" and ref["route_status"] == "SKIPPED_SAFE" for ref in package.agent_run_refs)
    assert package.artifacts["visual_plan"]["scenes"][0]["intended_visual_source"] == "DIAGRAM"
    assert package.artifacts["thumbnail_brief"]["rendered"] is False
    assert package.artifacts["media_qc_explanation"]["status"] == "WAITING_MEDIA_GENERATION"
    assert package.artifacts["provider_plan_dry_validation"]["status"] == "REACHED"
    assert package.artifacts["provider_plan_dry_validation"]["will_execute"] is False
    assert package.artifacts["provider_plan_dry_validation"]["no_network_call_made"] is True
    assert package.artifacts["srt"]["artifact_type"] == "SRT_CAPTION_FILE"
    assert package.artifacts["srt"]["not_final_media"] is True
    assert package.artifacts["srt"]["not_publishable"] is True
    assert package.artifacts["srt"]["provider_calls_made"] is False
    assert package.artifacts["srt"]["upload_publish_made"] is False
    assert Path(package.artifacts["srt"]["local_path"]).exists()
    assert package.artifacts["srt"]["srt"].startswith("1\n00:00:00,000 --> ")
    assert package.artifacts["srt"]["caption_count"] == len(package.artifacts["srt"]["cues"])
    assert package.artifacts["srt"]["checksum_sha256"]
    assert package.artifacts["duration_model"]["read_only"] is True
    assert package.artifacts["duration_model"]["target_duration_seconds"] == 540.0
    assert sum(item["word_target"] for item in package.artifacts["script_word_budget"]["section_word_budgets"]) == 1260
    assert package.artifacts["script_word_budget"]["minimum_word_count"] == 1134
    assert package.artifacts["script_word_budget"]["maximum_word_count"] == 1386
    assert package.risk_limitations_summary["mock_fallback_used"] is False
    assert package.risk_limitations_summary["dry_run_success_used"] is False
    assert package.risk_limitations_summary["media_provider_calls_made"] is False
    assert package.risk_limitations_summary["upload_or_publish_calls_made"] is False
    assert db_session.query(AgentContextPackSnapshot).count() == len(FULL_REHEARSAL_AGENT_CHAIN)
    provider_pack = db_session.query(AgentContextPackSnapshot).filter(AgentContextPackSnapshot.agent_key == "ProviderReadinessSummaryAgent").one()
    assert "provider_readiness_digest" in provider_pack.context_pack_json["digests"]
    assert "script_digest" not in provider_pack.context_pack_json["digests"]
    media_pack = db_session.query(AgentContextPackSnapshot).filter(AgentContextPackSnapshot.agent_key == "MediaQCExplanationAgent").one()
    assert "package_summary_digest" in media_pack.context_pack_json["digests"]
    assert "script_digest" not in media_pack.context_pack_json["digests"]

    boundary = db_session.query(VideoGenerationBoundary).one()
    assert boundary.package_id == package.id
    assert boundary.boundary_status == "BLOCKED_PROVIDER_NOT_CONFIGURED"
    assert boundary.no_provider_calls_confirmed is True
    assert boundary.provider_readiness["elevenlabs"]["status"] in {"NEEDS_CREDENTIAL", "NOT_CONFIGURED"}
    assert boundary.provider_readiness["luma_api"]["required"] is False
    assert boundary.operator_summary_vi == (
        "Gói nội dung đã sẵn sàng tới bước tạo media, nhưng chưa thể generate video vì chưa cấu hình provider voice/render/AI hero."
    )
    assert "ElevenLabs" in boundary.next_action and "NativeFFmpeg" in boundary.next_action
    assert db_session.query(MediaRenderJob).count() == 0
    assert db_session.query(HumanUploadTask).count() == 0
    assert db_session.query(RealSmokeRun).count() == 0


def test_m12_2s_rights_disclosure_conditional_wording_repair_reaches_provider_boundary(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[5]["artifact"].pop("disclosure_notes", None)
    outputs[5]["artifact"]["description"] = "Paste-ready metadata, no upload. Content is generated using AI tools."
    outputs[8]["artifact"]["ai_disclosure_needed"] = True
    outputs[8]["artifact"]["disclosure_notes"] = "Future generated media still needs source/provider manifest review."

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    repair = package.artifacts["disclosure_wording_repair_attempt"]
    assert repair["attempted"] is True
    assert repair["repaired"] is True
    assert repair["semantic_change_allowed"] is False
    assert repair["reason_codes"] == ["AI_DISCLOSURE_CONDITIONAL_WORDING_MISSING"]
    assert "Future generated media" in package.artifacts["metadata_package"]["disclosure_notes"]
    assert "AI_DISCLOSURE_CONDITIONAL_WORDING_MISSING" not in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert package.artifacts["provider_plan_dry_validation"]["will_execute"] is False
    assert db_session.query(MediaRenderJob).count() == 0
    assert db_session.query(HumanUploadTask).count() == 0


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
    project = _project_with_effective_context(db_session, scope)
    request = _request(scope.channel.id, video_project_id=project.id).model_copy(update={"topic": None})
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(request)

    assert package.package_status == "BLOCKED"
    assert package.artifacts["topic"]["status"] == "NEEDS_TOPIC"
    assert router.calls == []


def test_m12_2s_real_ollama_disabled_returns_not_configured(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(real_ollama_agent_run_enabled=False, llm_real_execution_enabled=False),
        llm_router=router,
    ).rehearse_full(_request(scope.channel.id, video_project_id=project.id))

    assert package.package_status == "NOT_CONFIGURED"
    missing = package.artifacts["llm_readiness"]["missing_or_invalid_flags"]
    assert "VCOS_ENABLE_REAL_OLLAMA_AGENT_RUN" in missing
    assert "VCOS_LLM_REAL_EXECUTION_ENABLED" in missing
    assert router.calls == []


def test_m12_2s_llmrouter_real_path_creates_provider_and_llm_snapshots(db_session, qualification_factory, monkeypatch) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
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

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    assert db_session.query(ProviderAttempt).filter(ProviderAttempt.provider_key == "OLLAMA").count() == len(FULL_REHEARSAL_AGENT_CHAIN)
    assert db_session.query(LLMRunSnapshot).filter(LLMRunSnapshot.provider == "ollama").count() == len(FULL_REHEARSAL_AGENT_CHAIN)
    forbidden_attempts = db_session.query(ProviderAttempt).filter(
        ProviderAttempt.provider_key.in_(["ELEVENLABS", "LUMA_API", "PEXELS_API", "GOOGLE_DRIVE", "YOUTUBE"])
    ).all()
    assert forbidden_attempts == []


@pytest.mark.parametrize("gatekeeper_result, expected_status", [("BLOCK", "BLOCKED"), ("REVIEW_REQUIRED", "REVIEW_REQUIRED")])
def test_m12_2s_gatekeeper_stops_or_marks_review_required(db_session, qualification_factory, gatekeeper_result, expected_status) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(_outputs(gatekeeper_result=gatekeeper_result)),
    ).rehearse_full(_request(scope.channel.id, video_project_id=project.id))

    assert package.package_status == expected_status
    assert "upload_card_copy" not in package.artifacts
    assert db_session.query(HumanUploadTask).count() == 0


def test_m12_2s_provider_readiness_block_is_deferred_to_boundary(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    for output in outputs:
        if output["agent_key"] == "ProviderReadinessSummaryAgent":
            output["status"] = "BLOCK"
            output["artifact"] = {}
            output["next_action"] = "Configure ElevenLabs before media generation."

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(outputs),
    ).rehearse_full(_request(scope.channel.id, video_project_id=project.id))

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    assert package.artifacts["provider_readiness_summary_review"]["reason_codes"] == [
        "PROVIDER_GAP_DEFERRED_TO_VIDEO_GENERATION_BOUNDARY"
    ]
    assert package.artifacts["provider_readiness_summary"]["providers"]
    assert package.artifacts["media_qc_explanation"]["status"] == "WAITING_MEDIA_GENERATION"
    boundary = db_session.query(VideoGenerationBoundary).one()
    assert boundary.boundary_status == "BLOCKED_PROVIDER_NOT_CONFIGURED"
    assert "GATEKEEPER_BLOCK" not in boundary.blocked_reasons


def test_m12_2s_invalid_output_sets_review_required(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(_outputs(invalid_agent="ScriptWriterAgent")),
    ).rehearse_full(_request(scope.channel.id, video_project_id=project.id))

    assert package.package_status == "REVIEW_REQUIRED"
    assert "validation_result" in package.artifacts["narration_script"]
    assert len(package.prompt_render_run_refs) == 5
    assert db_session.query(PromptAuditSnapshot).count() >= 5


def test_m12_2s_topic_idea_missing_artifact_triggers_schema_retry(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    bad_topic = dict(outputs[1])
    bad_topic.pop("artifact")
    retry_topic = _envelope(
        "TopicIdeaScoringAgent",
        {"topic_score": {"score": "UNKNOWN"}, "risk_assessment": {"risk_level": "MEDIUM"}},
        status="REVIEW_REQUIRED",
    )
    router = FakeRouter([outputs[0], bad_topic, retry_topic, *outputs[2:]])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    retry_audit = package.artifacts["topic_idea_schema_retry_attempt"]
    assert retry_audit["attempted"] is True
    assert retry_audit["mock_or_canned_output_used"] is False
    assert retry_audit["reason_codes"] == ["TOPIC_IDEA_SCHEMA_RETRY_MISSING_ARTIFACT"]
    assert package.artifacts["topic_scores"]["topic_score"]["score"] == "UNKNOWN"
    assert package.artifacts["provider_plan_dry_validation"]["will_execute"] is False
    assert len(router.calls) == len(FULL_REHEARSAL_AGENT_CHAIN) + 1


def test_m12_2s_topic_idea_missing_artifact_retry_failure_blocks(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    bad_topic = dict(outputs[1])
    bad_topic.pop("artifact")
    retry_bad_topic = dict(bad_topic)
    router = FakeRouter([outputs[0], bad_topic, retry_bad_topic])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "REVIEW_REQUIRED"
    assert package.artifacts["topic_idea_schema_retry_attempt"]["attempted"] is True
    assert package.artifacts["topic_scores"]["validation_result"]["valid"] is False
    assert any("artifact" in error for error in package.artifacts["topic_scores"]["validation_result"]["errors"])
    assert "research_digest" not in package.artifacts
    assert "provider_plan_dry_validation" not in package.artifacts
    assert len(router.calls) == 3


def test_m12_2s_duration_gate_blocks_before_visual_or_provider_plan(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[4]["artifact"]["sentences"] = [
        {"sentence_id": "S1", "text": "Short opening.", "approx_seconds": 35},
        {"sentence_id": "S2", "text": "Short payoff.", "approx_seconds": 36},
    ]
    outputs[4]["artifact"]["total_approx_seconds"] = 71
    router = FakeRouter(outputs)

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "BLOCKED"
    assert "SCRIPT_DURATION_BELOW_MINIMUM" in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert "visual_plan" not in package.artifacts
    assert "provider_plan_dry_validation" not in package.artifacts
    assert [call["requested_task_type"] for call in router.calls][-1] == "long_form_script"


def test_m12_2s_overlong_script_triggers_bounded_duration_trim_repair(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    overlong_text = (
        "This overlong narration sentence repeats useful context, adds extra explanatory detail, "
        "and remains intentionally verbose for deterministic duration repair validation."
    )
    outputs[4]["artifact"]["sentences"] = [
        {"sentence_id": f"S{index}", "text": overlong_text, "approx_seconds": 9.0}
        for index in range(1, 96)
    ]
    outputs[6]["artifact"]["scenes"] = _visual_scenes(95)
    original_hook = dict(outputs[4]["artifact"]["hook_spec"])
    router = FakeRouter(outputs)

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    repair = package.artifacts["script_duration_repair_attempt"]
    assert repair["attempted"] is True
    assert repair["repaired"] is True
    assert repair["word_count_before"] > repair["maximum_word_count"]
    assert repair["word_count_after"] <= repair["maximum_word_count"]
    assert repair["hook_preserved"] is True
    assert repair["payoff_location_preserved"] is True
    assert package.artifacts["narration_script"]["hook_spec"] == original_hook
    assert "SCRIPT_DURATION_ABOVE_MAXIMUM" not in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert package.artifacts["provider_plan_dry_validation"]["will_execute"] is False
    assert db_session.query(MediaRenderJob).count() == 0
    assert db_session.query(HumanUploadTask).count() == 0


def test_m12_2s_underlong_script_triggers_bounded_duration_expansion(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[4]["artifact"]["sentences"] = _underlong_script_sentences()
    outputs[4]["artifact"]["total_approx_seconds"] = 276
    outputs[4]["artifact"]["duration_self_check"]["actual_total_seconds"] = 276
    outputs[4]["artifact"]["duration_self_check"]["narration_word_count"] = 420
    outputs[6]["artifact"]["scenes"] = _visual_scenes(42)
    original_hook = dict(outputs[4]["artifact"]["hook_spec"])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    repair = package.artifacts["script_duration_repair_attempt"]
    assert repair["attempted"] is True
    assert repair["repair_type"] == "bounded_script_duration_expand"
    assert repair["repaired"] is True
    assert repair["word_count_before"] < repair["minimum_word_count"]
    assert repair["minimum_word_count"] <= repair["word_count_after"] <= repair["maximum_word_count"]
    assert repair["hook_preserved"] is True
    assert repair["payoff_location_preserved"] is True
    assert repair["section_order_preserved"] is True
    assert package.artifacts["narration_script"]["hook_spec"] == original_hook
    assert "SCRIPT_DURATION_BELOW_MINIMUM" not in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert package.artifacts["provider_plan_dry_validation"]["will_execute"] is False
    assert db_session.query(MediaRenderJob).count() == 0
    assert db_session.query(HumanUploadTask).count() == 0


def test_m12_2s_duration_expansion_failure_keeps_package_blocked(db_session, qualification_factory, monkeypatch) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[4]["artifact"]["sentences"] = _underlong_script_sentences()
    outputs[4]["artifact"]["total_approx_seconds"] = 276
    monkeypatch.setattr("app.services.m12_2._expand_script_to_word_budget", lambda script, duration_model, budget: (script, []))

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "BLOCKED"
    repair = package.artifacts["script_duration_repair_attempt"]
    assert repair["attempted"] is True
    assert repair["repair_type"] == "bounded_script_duration_expand"
    assert repair["repaired"] is False
    assert "SCRIPT_DURATION_BELOW_MINIMUM" in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert "visual_plan" not in package.artifacts
    assert "provider_plan_dry_validation" not in package.artifacts


def test_m12_2s_duration_repair_failure_keeps_package_blocked(db_session, qualification_factory, monkeypatch) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    overlong_text = (
        "This overlong narration sentence repeats useful context, adds extra explanatory detail, "
        "and remains intentionally verbose for deterministic duration repair validation."
    )
    outputs[4]["artifact"]["sentences"] = [
        {"sentence_id": f"S{index}", "text": overlong_text, "approx_seconds": 9.0}
        for index in range(1, 96)
    ]
    monkeypatch.setattr("app.services.m12_2._trim_script_to_word_budget", lambda script, duration_model, budget: (script, []))

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "BLOCKED"
    assert package.artifacts["script_duration_repair_attempt"]["attempted"] is True
    assert package.artifacts["script_duration_repair_attempt"]["repaired"] is False
    assert "SCRIPT_DURATION_ABOVE_MAXIMUM" in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert "visual_plan" not in package.artifacts
    assert "provider_plan_dry_validation" not in package.artifacts


def test_m12_2s_missing_hook_fields_block_before_visual_or_provider_plan(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[4]["artifact"]["hook_spec"].pop("first_3_seconds_visual")
    router = FakeRouter(outputs)

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "BLOCKED"
    assert "HOOK_FIRST_3_SECONDS_VISUAL_MISSING" in package.artifacts["deterministic_gate_report"]["fail_codes"]
    assert "visual_plan" not in package.artifacts
    assert "provider_plan_dry_validation" not in package.artifacts


def test_m12_2s_visual_source_scan_ignores_evidence_source_type() -> None:
    artifact = {
        "evidence_refs": [{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "pack"}],
        "applied_context_refs": {"source_type": "FROZEN_DIGEST"},
        "scenes": [
            {"scene_id": "SCN01", "intended_visual_source": "DIAGRAM"},
            {"scene_id": "SCN02", "source_type": "CARD"},
        ],
    }

    assert _find_visual_source_values(artifact) == {"DIAGRAM", "CARD"}


def test_m12_2s_visual_unknown_sentence_ref_repair_is_bounded() -> None:
    script = {"sentences": [{"sentence_id": f"S{index}", "text": "ok", "approx_seconds": 1} for index in range(1, 4)]}
    visual = {
        "scenes": [
            {"scene_id": "SCN01", "sentence_ids": ["S1", "S2"], "intended_visual_source": "DIAGRAM"},
            {"scene_id": "SCN02", "sentence_ids": ["S3", "S4"], "intended_visual_source": "CARD"},
            {"scene_id": "SCN03", "sentence_ids": ["S5"], "intended_visual_source": "CARD"},
        ]
    }

    repaired, patches = _repair_visual_unknown_sentence_refs(visual, script)

    assert patches == [
        {
            "scene_id": "SCN02",
            "removed_unknown_sentence_refs": ["S4"],
            "kept_sentence_refs": ["S3"],
            "repair_action": "drop_unknown_sentence_refs",
        },
        {
            "scene_id": "SCN03",
            "removed_unknown_sentence_refs": ["S5"],
            "kept_sentence_refs": [],
            "repair_action": "drop_unanchored_visual_scene",
        },
    ]
    assert len(repaired["scenes"]) == 2
    assert repaired["scenes"][1]["sentence_ids"] == ["S3"]


def test_m12_2s_visual_repair_normalizes_covers_refs_and_candidate_source() -> None:
    script = {"sentences": [{"sentence_id": f"S{index}", "text": "ok", "approx_seconds": 1} for index in range(1, 4)]}
    visual = {
        "scenes": [
            {"scene_id": "SCN01", "sentence_range": ["S1"], "intended_visual_source": "LUMA_HERO_CANDIDATE_ONLY"},
            {"scene_id": "SCN02", "sentence_ids_covered": ["S2"], "intended_visual_source": "CARD"},
            {"scene_id": "SCN03", "narration_sentence_ids": ["S3"], "intended_visual_source": "CARD"},
        ]
    }

    repaired, patches = _repair_visual_unknown_sentence_refs(visual, script, allowed_sources={"DIAGRAM", "CARD"})

    assert repaired["scenes"][0]["sentence_ids"] == ["S1"]
    assert repaired["scenes"][0]["intended_visual_source"] == "DIAGRAM"
    assert repaired["scenes"][1]["sentence_ids"] == ["S2"]
    assert repaired["scenes"][1]["intended_visual_source"] == "CARD"
    assert repaired["scenes"][2]["sentence_ids"] == ["S3"]
    assert {patch["repair_action"] for patch in patches} == {
        "normalize_sentence_ids_covered",
        "normalize_narration_sentence_ids",
        "normalize_sentence_range",
        "normalize_disallowed_candidate_visual_source",
    }


def test_m12_2s_bounded_style_repair_can_remove_forbidden_style(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    effective.brand_voice_persona_context_json = {"forbidden_style": ["hype bait"]}
    db_session.flush()
    outputs = _outputs()
    outputs[4]["artifact"]["sentences"][0]["text"] = "This hype bait line still describes the COMPLETE channel contract."
    router = FakeRouter(outputs)

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "WAITING_PROVIDER_CONFIG"
    assert package.artifacts["script_style_repair_attempt"]["attempted"] is True
    assert package.artifacts["script_style_repair_attempt"]["sentence_patches"]
    repaired_text = package.artifacts["narration_script"]["sentences"][0]["text"].lower()
    assert "hype bait" not in repaired_text


def test_m12_2s_style_gate_still_blocks_if_repair_fails(db_session, qualification_factory, monkeypatch) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    effective.brand_voice_persona_context_json = {"forbidden_style": ["hype bait"]}
    db_session.flush()
    monkeypatch.setattr("app.services.m12_2._repair_forbidden_style_terms", lambda script, terms: (script, []))
    outputs = _outputs()
    outputs[4]["artifact"]["sentences"][0]["text"] = "This hype bait line still describes the COMPLETE channel contract."
    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "BLOCKED"
    assert package.artifacts["script_style_repair_attempt"]["attempted"] is True
    assert "SCRIPT_FORBIDDEN_STYLE_USED" in package.artifacts["deterministic_gate_report"]["fail_codes"]


def test_m12_2s_channel_authority_requires_decision(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[0]["artifact"] = {"reason": "Missing machine-readable decision."}
    router = FakeRouter(outputs)

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "REVIEW_REQUIRED"
    assert package.artifacts["admission_decision"]["reason_codes"] == ["REQUIRED_ARTIFACT_FIELDS_MISSING"]
    assert package.artifacts["admission_decision"]["missing_fields"] == ["decision"]
    assert len(router.calls) == 1
    assert db_session.query(MediaRenderJob).count() == 0


def test_m12_2s_thumbnail_and_media_qc_schema_guards(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    outputs = _outputs()
    outputs[7]["artifact"]["image_url"] = "https://example.invalid/rendered.png"

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(outputs)).rehearse_full(
        _request(scope.channel.id, video_project_id=project.id)
    )

    assert package.package_status == "REVIEW_REQUIRED"
    assert package.artifacts["thumbnail_brief_review"]["reason_codes"] == ["THUMBNAIL_RENDER_NOT_ALLOWED"]
    assert db_session.query(MediaRenderJob).count() == 0


def test_m12_2s_package_retrieval_agent_runs_and_boundary(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    project = _project_with_effective_context(db_session, scope)
    service = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter(_outputs()))
    package = service.rehearse_full(_request(scope.channel.id, video_project_id=project.id))

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
