from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.m12_2 import FirstScriptedVideoPackageRequest
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.workflow import VideoProjectCreate
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import AgentContextPackSnapshot, VideoProject
from app.services import R3D1AdminService, VideoProjectService
from app.services.m12_2 import FirstScriptedVideoPackageService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.r3d3 import (
    AgentContextContractRegistry,
    AgentContextPackBuilder,
    ArtifactDigestBuilder,
    ContextPackShapeGate,
    DEFAULT_CONTRACTS,
    stable_hash,
)


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def route(self, **kwargs) -> LLMRouteResponse:
        self.calls.append(kwargs)
        output = {
            "contract_version": "m12.1.0",
            "agent_key": "ScriptWriterAgent",
            "status": "OK",
            "confidence_label": "HIGH",
            "evidence_refs": [],
            "limitations": [],
            "next_action": "Review.",
            "operator_summary_vi": "OK.",
            "technical_appendix": {},
            "artifact": {"sentences": []},
        }
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


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        production_prompt_activation_enabled=True,
        real_llm_package_run_enabled=True,
        real_ollama_agent_run_enabled=True,
        media_provider_calls_disabled=True,
        upload_and_publish_disabled=True,
        old_provider_smoke_disabled=True,
        llm_provider="ollama",
        llm_real_execution_enabled=True,
        llm_router_real_smoke=False,
    )


def _request(channel_id: uuid.UUID, *, video_project_id: uuid.UUID | None = None) -> FirstScriptedVideoPackageRequest:
    return FirstScriptedVideoPackageRequest(
        channel_id=channel_id,
        video_project_id=video_project_id,
        topic="R3D3 context pack",
        research_pack_text="Fact: VCOS uses frozen snapshots. Fact: No upload automation.",
        research_pack_ref="operator_research_pack:r3d3",
    )


def _project_with_effective_context(db_session, scope) -> VideoProject:
    category = R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"r3d3-{uuid.uuid4().hex[:8]}",
            name="R3D3 Category",
            default_format_policy_json={"target_duration_seconds": 420, "structure": ["hook", "proof", "takeaway"]},
            default_visual_style_json={"style_note": "compact diagrams"},
            default_voice_style_json={"tone": "calm"},
            default_thumbnail_style_json={"style": "clear"},
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
            title="R3D3 project",
            description="R3D3 fixture.",
            created_by_user_id=scope.operator.id,
        )
    )
    project = db_session.get(VideoProject, project_read.id)
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    assert effective.compile_status == "PASS"
    return project


def test_r3d3_blocks_package_when_effective_context_snapshot_missing(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D3 Missing Effective")
    router = FakeRouter()

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).create(_request(scope.channel.id))

    assert package.package_status == "BLOCKED"
    assert package.artifacts["effective_context"]["reason_codes"] == ["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"]
    assert package.prompt_render_run_refs == []
    assert router.calls == []


def test_agent_context_pack_uses_frozen_effective_snapshot_not_latest_channel_settings(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D3 Frozen")
    project = _project_with_effective_context(db_session, scope)
    effective = db_session.get(VideoProject, project.id).effective_context_snapshot_id
    snapshot = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    scope.channel.primary_language = "zz"
    scope.channel.target_market = "ZZ"
    db_session.flush()

    result = AgentContextPackBuilder(db_session).build(
        package_id=uuid.uuid4(),
        video_project_id=project.id,
        agent_key="ScriptWriterAgent",
        task_type="long_form_script",
        lane="long_context_text",
        effective_context_snapshot_id=effective,
        effective_context_hash=snapshot.context_hash,
        compiled_policy_snapshot_id=scope.snapshot.id,
        compiled_policy_snapshot_hash=scope.snapshot.content_hash,
        channel_contract_hash=snapshot.channel_contract_hash,
        artifacts={"script_outline": {"outline": ["hook"], "target_duration_seconds": 420}},
        evidence_refs=[{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d3"}],
        current_package_state={"research_pack_text": "Fact: frozen snapshot only.", "research_pack_ref": "r3d3"},
        runtime_guard_state={"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
    )

    assert result.status == "OK"
    assert result.context_pack["digests"]["script_contract_digest"]["payload"]["content_language"] != "zz"
    assert result.context_pack["latest_channel_settings_read"] is False


def test_context_pack_hash_stable_and_changes_when_artifact_digest_changes(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D3 Hash")
    project = _project_with_effective_context(db_session, scope)
    snapshot = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    package_id = uuid.uuid4()
    kwargs = {
        "package_id": package_id,
        "video_project_id": project.id,
        "agent_key": "ScriptWriterAgent",
        "task_type": "long_form_script",
        "lane": "long_context_text",
        "effective_context_snapshot_id": snapshot.id,
        "effective_context_hash": snapshot.context_hash,
        "compiled_policy_snapshot_id": scope.snapshot.id,
        "compiled_policy_snapshot_hash": scope.snapshot.content_hash,
        "channel_contract_hash": snapshot.channel_contract_hash,
        "evidence_refs": [{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d3"}],
        "current_package_state": {"research_pack_text": "Fact: stable hash.", "research_pack_ref": "r3d3"},
        "runtime_guard_state": {"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
    }
    first = AgentContextPackBuilder(db_session).build(artifacts={"script_outline": {"outline": ["hook"]}}, **kwargs)
    second = AgentContextPackBuilder(db_session).build(artifacts={"script_outline": {"outline": ["hook"]}}, **kwargs)
    changed = AgentContextPackBuilder(db_session).build(artifacts={"script_outline": {"outline": ["different"]}}, **kwargs)

    assert first.context_pack["context_pack_hash"] == second.context_pack["context_pack_hash"]
    assert first.context_pack["context_pack_hash"] != changed.context_pack["context_pack_hash"]


def test_artifact_digest_prioritizes_core_package_artifacts() -> None:
    artifacts = {
        "admission_decision": {"summary": "x" * 2000},
        "channel_contract_snapshot_ref": {"summary": "x" * 2000},
        "narration_script": {"sentences": [{"sentence_id": "S1", "text": "ok", "approx_seconds": 1}]},
        "metadata_package": {"title": "T", "description": "D"},
        "visual_plan": {"scenes": [{"sentence_ids": ["S1"], "intended_visual_source": "DIAGRAM"}]},
        "thumbnail_brief": {"variants": [{"concept": "C"}]},
        "rights_disclosure_review": {"result": "REVIEW_REQUIRED"},
    }

    refs = ArtifactDigestBuilder().build_many(package_id=uuid.uuid4(), artifacts=artifacts, max_refs=5)

    assert [ref["artifact_id"] for ref in refs] == [
        "narration_script",
        "metadata_package",
        "visual_plan",
        "thumbnail_brief",
        "rights_disclosure_review",
    ]


def test_gatekeeper_context_pack_honors_max_artifact_refs(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D3 Gatekeeper Artifact Ref Cap")
    project = _project_with_effective_context(db_session, scope)
    snapshot = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    artifacts = {
        "narration_script": {"sentences": [{"sentence_id": "S1", "text": "ok", "approx_seconds": 1}]},
        "script_outline": {"section_budgets": []},
        "metadata_package": {"title": "T", "description": "D"},
        "visual_plan": {"scenes": [{"sentence_ids": ["S1"], "intended_visual_source": "DIAGRAM"}]},
        "thumbnail_brief": {"variants": [{"concept": "C", "notes": "x" * 1200}]},
        "rights_disclosure_review": {"result": "REVIEW_REQUIRED", "disclosure_notes": "Future media review required."},
        "topic_scores": {"score": 80},
        "research_notes": {"facts": ["x" * 1000]},
        "deterministic_gate_report": {"status": "PASS", "fail_codes": []},
    }

    result = AgentContextPackBuilder(db_session).build(
        package_id=uuid.uuid4(),
        video_project_id=project.id,
        agent_key="GatekeeperSoftReviewAgent",
        task_type="policy_soft_review",
        lane="gatekeeper_soft_review",
        effective_context_snapshot_id=snapshot.id,
        effective_context_hash=snapshot.context_hash,
        compiled_policy_snapshot_id=scope.snapshot.id,
        compiled_policy_snapshot_hash=scope.snapshot.content_hash,
        channel_contract_hash=snapshot.channel_contract_hash,
        artifacts=artifacts,
        evidence_refs=[{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d3"}],
        current_package_state={"research_pack_text": "Fact: gatekeeper uses capped artifact refs.", "research_pack_ref": "r3d3"},
        runtime_guard_state={"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
    )

    assert result.status == "OK"
    assert len(result.context_pack["digests"]["artifact_digests"]) <= DEFAULT_CONTRACTS["GatekeeperSoftReviewAgent"].max_artifact_refs


def test_visual_context_pack_keeps_long_form_script_digest_within_budget(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D3 Long Script Digest")
    project = _project_with_effective_context(db_session, scope)
    snapshot = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    text = (
        "This long form fixture sentence preserves deterministic timing, source references, operator safeguards, "
        "and provider boundary language while avoiding raw media claims or upload promises."
    )
    sentences = [{"sentence_id": f"S{index}", "text": text, "approx_seconds": 15} for index in range(1, 49)]

    result = AgentContextPackBuilder(db_session).build(
        package_id=uuid.uuid4(),
        video_project_id=project.id,
        agent_key="VisualPlanningAgent",
        task_type="visual_plan",
        lane="visual_creative_review",
        effective_context_snapshot_id=snapshot.id,
        effective_context_hash=snapshot.context_hash,
        compiled_policy_snapshot_id=scope.snapshot.id,
        compiled_policy_snapshot_hash=scope.snapshot.content_hash,
        channel_contract_hash=snapshot.channel_contract_hash,
        artifacts={"narration_script": {"sentences": sentences}},
        evidence_refs=[{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d3"}],
        current_package_state={"research_pack_text": "Fact: visual planning uses compact script digest.", "research_pack_ref": "r3d3"},
        runtime_guard_state={"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
    )

    assert result.status == "OK"
    script_digest = result.context_pack["digests"]["script_sentence_digest"]["payload"]
    assert script_digest["sentence_count"] == 48
    assert len(script_digest["sentence_ids"]) == 48
    assert len(script_digest["timeline_slice"]) == 10
    assert script_digest["timeline_slice_omitted_count"] == 38


def test_prompt_budget_gate_blocks_when_required_context_cannot_fit(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D3 Budget")
    project = _project_with_effective_context(db_session, scope)
    snapshot = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    base = DEFAULT_CONTRACTS["ScriptWriterAgent"]
    tiny = replace(base, max_context_chars=120, content_hash=stable_hash({**base.to_dict(), "max_context_chars": 120}))
    registry = AgentContextContractRegistry({"ScriptWriterAgent": tiny})

    result = AgentContextPackBuilder(db_session, contract_registry=registry).build(
        package_id=uuid.uuid4(),
        video_project_id=project.id,
        agent_key="ScriptWriterAgent",
        task_type="long_form_script",
        lane="long_context_text",
        effective_context_snapshot_id=snapshot.id,
        effective_context_hash=snapshot.context_hash,
        compiled_policy_snapshot_id=scope.snapshot.id,
        compiled_policy_snapshot_hash=scope.snapshot.content_hash,
        channel_contract_hash=snapshot.channel_contract_hash,
        artifacts={"script_outline": {"outline": ["x" * 1000]}},
        evidence_refs=[{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d3"}],
        current_package_state={"research_pack_text": "Fact: compact.", "research_pack_ref": "r3d3"},
        runtime_guard_state={"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
    )

    assert result.status == "BLOCK"
    assert "CONTEXT_BUDGET_EXCEEDED" in result.reason_codes
    assert db_session.query(AgentContextPackSnapshot).count() == 1


def test_context_pack_shape_gate_blocks_forbidden_sections() -> None:
    contract = DEFAULT_CONTRACTS["ScriptWriterAgent"]
    pack = {
        "agent_context_contract": contract.to_dict(),
        "latest_channel_settings_read": False,
        "prompt_budget_metrics": {},
        "audit_refs": {"effective_context_snapshot_id": "x"},
        "digests": {section: {} for section in contract.required_context_sections},
    }
    pack["digests"]["full_previous_artifacts"] = {}

    result = ContextPackShapeGate().check(contract=contract, context_pack=pack)

    assert result.status == "BLOCK"
    assert "CONTEXT_PACK_SHAPE_INVALID" in result.reason_codes


def test_r3d3_source_guard_no_provider_vector_or_upload_paths() -> None:
    source = Path("app/services/r3d3.py").read_text(encoding="utf-8")
    forbidden = [
        "requests.",
        "httpx",
        "GoogleVertexVeoProvider",
        "YouTubeUpload",
        "GoogleDriveUploadService",
        "embedding",
        "vector_search",
    ]
    assert [token for token in forbidden if token in source] == []
