from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.m12_2 import FirstScriptedVideoPackageRequest
from app.core.config import Settings
from app.core.errors import ValidationFailureError
from app.db.models import FirstScriptedVideoPackage, MediaRenderJob, PromptRenderRun, RealSmokeRun
from app.main import create_app
from app.services.m12_2 import (
    FirstScriptedVideoPackageService,
    PACKAGE_AGENT_CHAIN,
    verify_m12_2_required_tags,
)


class CompletedTags:
    stdout = "m12-1-prompt-registry-contracts\n"


class FakeRouter:
    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

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
            provider_attempt_id=None,
            llm_run_snapshot_id=uuid.uuid4(),
            reason_codes=["TEST_LLM_ROUTE"],
        )


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "production_prompt_activation_enabled": True,
        "real_llm_package_run_enabled": True,
        "media_provider_calls_disabled": True,
        "upload_and_publish_disabled": True,
        "old_provider_smoke_disabled": True,
        "llm_provider": "ollama",
        "llm_real_execution_enabled": True,
        "llm_router_real_smoke": False,
    }
    base.update(overrides)
    return Settings(**base)


def _complete_scope(qualification_factory):
    scope = qualification_factory.channel_scope(name="M12.2")
    scope.channel.primary_language = "vi"
    scope.channel.primary_region = "VN"
    scope.channel.primary_timezone = "Asia/Ho_Chi_Minh"
    scope.channel.target_regions = ["VN"]
    scope.channel.metadata_ = {"operator_language": "vi"}
    return scope


def _request(channel_id: uuid.UUID) -> FirstScriptedVideoPackageRequest:
    return FirstScriptedVideoPackageRequest(
        channel_id=channel_id,
        topic="Cách dựng workflow sản xuất video không dùng mock fallback",
        research_pack_text="Source notes: VCOS uses prompt registry, channel contract, human review, and no upload in M12.2.",
        research_pack_ref="operator_research_pack:m12_2",
    )


def _envelope(agent_key: str, artifact: dict, *, status: str = "OK") -> dict:
    return {
        "contract_version": "m12.1.0",
        "agent_key": agent_key,
        "status": status,
        "confidence_label": "HIGH",
        "risk_level": "LOW",
        "evidence_refs": [{"type": "operator_research_pack", "id": "m12_2"}],
        "limitations": ["Human review required."],
        "next_action": "Human review required.",
        "operator_summary_vi": f"{agent_key} hoàn tất.",
        "technical_appendix": {"test_output": True},
        "artifact": artifact,
    }


def _outputs(*, gatekeeper_result: str = "PASS", invalid_agent: str | None = None) -> list[dict]:
    artifacts = {
        "ChannelAuthorityAgent": {"decision": "ADMIT", "reason": "Fits channel contract."},
        "TopicIdeaScoringAgent": {"scores": [{"topic": "VCOS M12.2", "score": 86}]},
        "ResearchPackSummarizer": {
            "summary": "M12.2 activates production prompts for a human-review package.",
            "source_notes": ["operator research pack"],
            "evidence_refs": [{"id": "m12_2"}],
        },
        "ScriptPlanningAgent": {"outline": ["hook", "problem", "mechanism", "result", "takeaway"]},
        "ScriptWriterAgent": {
            "sentences": [
                {"sentence_id": "S1", "text": "M12.2 starts with a real channel contract.", "approx_seconds": 4},
                {"sentence_id": "S2", "text": "The package stops at human review.", "approx_seconds": 4},
            ]
        },
        "PublishingMetadataAgent": {
            "title": "VCOS M12.2: Production Prompt Activation",
            "description": "Human-review package only.",
            "chapters": [{"time": "00:00", "title": "Hook"}],
            "tags": ["VCOS"],
            "pinned_comment": "Review before publishing.",
            "disclosure_notes": ["AI-assisted script draft."],
        },
        "VisualPlanningAgent": {
            "scenes": [
                {"sentence_id": "S1", "intended_visual_source": "DIAGRAM"},
                {"sentence_id": "S2", "intended_visual_source": "CARD"},
            ],
            "media_provider_calls": "NONE",
        },
        "UploadCardCopyAgent": {"title": "VCOS M12.2", "description": "Paste-ready copy.", "not_uploaded": True},
        "GatekeeperSoftReviewAgent": {"result": gatekeeper_result, "findings": []},
    }
    outputs = []
    for step in PACKAGE_AGENT_CHAIN:
        envelope = _envelope(step.agent_key, artifacts[step.agent_key])
        if invalid_agent == step.agent_key:
            envelope["unexpected"] = True
        outputs.append(envelope)
    return outputs


def test_m12_2_preflight_tags_required(monkeypatch) -> None:
    monkeypatch.setattr("app.services.m12_2.subprocess.run", lambda *args, **kwargs: CompletedTags())

    result = verify_m12_2_required_tags(Path("/tmp"))

    assert result["status"] == "BLOCKED"
    assert result["missing_tags"] == ["m12-1r-mock-dryrun-purge"]


def test_m12_2_missing_channel_returns_needs_channel_init(db_session) -> None:
    service = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=FakeRouter([]))

    with pytest.raises(ValidationFailureError) as exc:
        service.create(_request(uuid.uuid4()))

    assert "BLOCKED: NEEDS_CHANNEL_INIT" in str(exc.value)


def test_m12_2_missing_channel_contract_blocks_before_llm(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="M12.2 Contract Missing")
    scope.channel.active_policy_snapshot_id = None
    db_session.flush()
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).create(_request(scope.channel.id))

    assert package.package_status == "BLOCKED"
    assert package.next_action == "Bổ sung hoặc compile lại ChannelProfileVersion trước khi chạy video package production."
    assert package.prompt_render_run_refs == []
    assert router.calls == []


def test_m12_2_activation_flag_false_blocks(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(production_prompt_activation_enabled=False),
        llm_router=FakeRouter([]),
    ).create(_request(scope.channel.id))

    assert package.package_status == "BLOCKED"
    assert "VCOS_ENABLE_PRODUCTION_PROMPT_ACTIVATION" in package.artifacts["runtime_mode"]["missing_or_invalid_flags"]


def test_m12_2_missing_llm_readiness_returns_not_configured_no_fallback(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    router = FakeRouter([])

    package = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(real_llm_package_run_enabled=False, llm_real_execution_enabled=False),
        llm_router=router,
    ).create(_request(scope.channel.id))

    assert package.package_status == "NOT_CONFIGURED"
    assert package.artifacts["llm_readiness"]["reason_codes"] == ["LLM_PROVIDER_NOT_CONFIGURED"]
    assert package.risk_limitations_summary["mock_fallback_used"] is False
    assert router.calls == []


def test_m12_2_first_package_uses_prompt_registry_and_reaches_human_review(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    router = FakeRouter(_outputs())

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).create(_request(scope.channel.id))

    assert package.package_status == "READY_FOR_HUMAN_REVIEW"
    assert len(router.calls) == len(PACKAGE_AGENT_CHAIN)
    assert len(package.agent_run_refs) == len(PACKAGE_AGENT_CHAIN)
    assert len(package.prompt_render_run_refs) == len(PACKAGE_AGENT_CHAIN)
    assert package.prompt_audit_snapshot_refs
    assert package.provider_readiness_snapshot_id is not None
    assert package.artifacts["channel_contract_snapshot_ref"]["compiled_policy_snapshot_id"] == str(scope.snapshot.id)
    assert package.artifacts["admission_decision"]["decision"] == "ADMIT"
    assert package.artifacts["narration_script"]["sentences"][0]["sentence_id"] == "S1"
    assert package.artifacts["upload_card_copy"]["not_uploaded"] is True
    assert package.artifacts["human_review_checklist"]["final_statement"].startswith("Human final approval required")
    assert package.risk_limitations_summary["media_provider_calls_made"] is False
    assert package.risk_limitations_summary["upload_or_publish_calls_made"] is False
    assert db_session.query(RealSmokeRun).count() == 0
    assert db_session.query(MediaRenderJob).count() == 0

    first_run = db_session.get(PromptRenderRun, package.prompt_render_run_refs[0])
    assert first_run.rendered_messages[0]["role"] == "system"
    assert first_run.rendered_messages[1]["role"] == "user"
    assert first_run.prompt_hash
    assert first_run.prompt_context_hash
    assert first_run.channel_contract_json["contract_status"] == "COMPLETE"
    assert first_run.compiled_policy_snapshot_id == scope.snapshot.id
    assert {call["lane_name"] for call in router.calls} == {step.router_lane for step in PACKAGE_AGENT_CHAIN}


def test_m12_2_invalid_agent_output_stops_for_review(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    router = FakeRouter(_outputs(invalid_agent="ScriptWriterAgent"))

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).create(_request(scope.channel.id))

    assert package.package_status == "REVIEW_REQUIRED"
    assert "validation_result" in package.artifacts["narration_script"]
    assert len(router.calls) == 5


def test_m12_2_gatekeeper_result_controls_final_status(db_session, qualification_factory) -> None:
    scope = _complete_scope(qualification_factory)
    review = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(_outputs(gatekeeper_result="REVIEW_REQUIRED")),
    ).create(_request(scope.channel.id))
    blocked = FirstScriptedVideoPackageService(
        db_session,
        settings=_settings(),
        llm_router=FakeRouter(_outputs(gatekeeper_result="BLOCK")),
    ).create(_request(scope.channel.id))

    assert review.package_status == "REVIEW_REQUIRED"
    assert blocked.package_status == "BLOCKED"


def test_m12_2_api_routes_exist_and_no_forbidden_package_paths(db_session) -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    assert "/video-packages/first-scripted" in paths
    assert "/video-packages/{package_id}" in paths
    assert "/video-packages/{package_id}/review" in paths
    source = Path("app/services/m12_2.py").read_text(encoding="utf-8")
    forbidden = [
        "app.providers.mock",
        "RealSmokeOrchestratorService",
        "GoogleVertexVeoProvider",
        "CreatomateRender",
        "YouTubeUpload",
        "GoogleDriveUploadService",
    ]
    assert [token for token in forbidden if token in source] == []
    assert db_session.query(FirstScriptedVideoPackage).count() == 0
