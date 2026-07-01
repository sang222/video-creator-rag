from __future__ import annotations

from sqlalchemy import func, select

from app.contracts import PromptOutputValidationRequest, PromptRenderRequest
from app.db.models import (
    AgentPromptProfile,
    PromptAuditSnapshot,
    PromptEvaluationRun,
    PromptRenderRun,
    PromptTemplateRecord,
    ProviderAttempt,
)
from app.providers.base import ProviderResponse
from app.providers.ollama import OllamaChatRequest, OllamaLLMProvider
from app.services import LLMRouterService, PromptRegistryService
from app.services.m12_1 import REQUIRED_AGENT_KEYS


class SequenceProvider:
    provider_key = "OLLAMA"

    def __init__(self, responses: list[ProviderResponse]):
        self.responses = responses
        self.calls: list[OllamaChatRequest] = []

    def chat(self, *, request: OllamaChatRequest) -> ProviderResponse:
        self.calls.append(request)
        return self.responses.pop(0)


def _complete_channel_contract() -> dict:
    return {
        "channel_identity": {
            "channel_name": "VCOS Test",
            "channel_type": "YOUTUBE_CHANNEL",
            "niche": "operator workflows",
            "positioning": "practical",
            "brand_promise": "clear production workflows",
            "platform_targets": ["YOUTUBE"],
            "series_plan": [{"name": "Ops"}],
        },
        "target_audience": {
            "primary_persona": "Vietnamese solo operator",
            "audience_level": "intermediate",
            "pain_points": ["time"],
            "desired_outcome": "ship safely",
        },
        "market_locale": {
            "primary_market": "VN",
            "secondary_markets": [],
            "audience_locale": "vi-VN",
            "content_language": "vi",
            "operator_language": "vi",
            "timezone": "Asia/Ho_Chi_Minh",
            "currency": "VND",
            "measurement_units": "metric",
            "date_format": "DD/MM/YYYY",
            "cultural_style": "clear and practical",
            "market_examples_preference": "Vietnam-first",
            "regulatory_sensitivity": "normal",
            "market_locale_context_status": "KNOWN",
        },
        "editorial_strategy": {
            "content_pillars": ["workflow"],
            "allowed_angles": ["practical"],
            "forbidden_angles": ["hype"],
            "claim_style": "evidence-aware",
            "allowed_topics": ["operations"],
            "forbidden_topics": ["fake engagement"],
        },
        "format_policy": {"long_form": {"enabled": True}, "shorts": {"enabled": True}},
        "voice_style": {"narration_tone": "calm"},
        "platform_strategy": {
            "primary_platform": "YOUTUBE",
            "youtube_is_learning_authority": True,
            "secondary_platforms": [],
            "disabled_authorities": ["TIKTOK", "FACEBOOK"],
            "publish_mode": "MANUAL",
            "auto_publish_allowed": False,
            "studio_scraping_allowed": False,
        },
        "media_policy": {
            "voice_provider": "ELEVENLABS",
            "ai_hero_provider": "GOOGLE_VERTEX_VEO",
            "ai_hero_model_id": "veo-3.1-fast-generate-001",
            "ai_hero_allowed_durations_seconds": [4, 6, 8],
            "ai_hero_default_duration_seconds": 8,
            "ai_hero_audio": False,
            "renderer": "CREATOMATE_GROWTH_10K_LIGHT_ONLY",
            "storage_archive": "GOOGLE_DRIVE",
        },
        "rights_policy": {"rights_evidence_required": True, "ai_disclosure_required_when_ai_media_used": True},
        "budget_policy": {"monthly_budget_usd": 250, "avoid_unnecessary_ai_hero": True},
        "learning_policy": {"authority": "YOUTUBE", "auto_promote_learning": False},
        "forbidden_behavior": ["fake_traffic", "bot_engagement", "dashboard_scraping"],
        "contract_status": "COMPLETE",
    }


def test_prompt_registry_syncs_all_required_agents_and_hashes(db_session) -> None:
    summary = PromptRegistryService(db_session).sync_repo_registry()

    assert summary.template_count == len(REQUIRED_AGENT_KEYS)
    assert set(summary.agent_keys) == set(REQUIRED_AGENT_KEYS)
    assert db_session.scalar(select(func.count()).select_from(PromptTemplateRecord)) == len(REQUIRED_AGENT_KEYS)
    assert db_session.scalar(select(func.count()).select_from(AgentPromptProfile)) == len(REQUIRED_AGENT_KEYS)
    assert summary.prompt_hashes == PromptRegistryService(db_session).sync_repo_registry().prompt_hashes
    profile = db_session.scalars(select(AgentPromptProfile).where(AgentPromptProfile.agent_key == "VisualPlanningAgent")).one()
    assert profile.default_router_lane == "visual_creative_review"
    assert "common_channel_contract" in " ".join(profile.safety_policy_refs)


def test_missing_channel_contract_returns_review_required_and_persists_audit(db_session) -> None:
    result = PromptRegistryService(db_session).render_prompt(
        PromptRenderRequest(agent_key="ScriptWriterAgent", task_payload={"topic": "safe workflow"})
    )

    assert result.status == "REVIEW_REQUIRED"
    assert result.rendered_messages == []
    assert result.blocking_output is not None
    assert result.blocking_output.next_action == "Bổ sung hoặc compile lại ChannelProfileVersion trước khi render prompt."
    assert db_session.get(PromptRenderRun, result.prompt_render_run_id).validation_status == "REVIEW_REQUIRED"
    assert db_session.get(PromptAuditSnapshot, result.prompt_audit_snapshot_id).validation_result["status"] == "REVIEW_REQUIRED"


def test_render_binds_frozen_channel_contract_messages_and_eval_cases(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="M12.1 Prompt")
    contract = _complete_channel_contract()
    service = PromptRegistryService(db_session)

    result = service.render_prompt(
        PromptRenderRequest(
            agent_key="PublishingMetadataAgent",
            task_payload={"video_title": "VCOS prompt registry"},
            evidence_refs=[{"type": "manual", "id": "ev-1"}],
            artifact_refs=[{"type": "VideoProject", "id": "vp-1"}],
            channel_profile_version_id=scope.profile.id,
            compiled_policy_snapshot_id=scope.snapshot.id,
            channel_contract_json=contract,
            compiled_policy_snapshot_json=scope.snapshot.compiled_payload,
            market_locale_context_json=contract["market_locale"],
        )
    )

    assert result.status == "OK"
    assert [message.role for message in result.rendered_messages] == ["system", "user"]
    assert "common_channel_contract" in result.rendered_messages[0].content
    assert "PublishingMetadataAgent" in result.rendered_messages[0].content
    assert str(scope.profile.id) in result.rendered_messages[1].content
    assert "channel_contract_ref_json" in result.rendered_messages[1].content
    assert "channel_contract_json:" not in result.rendered_messages[1].content
    run = db_session.get(PromptRenderRun, result.prompt_render_run_id)
    assert run.channel_profile_version_id == scope.profile.id
    assert run.compiled_policy_snapshot_id == scope.snapshot.id
    assert run.channel_contract_json["contract_status"] == "COMPLETE"

    eval_runs = service.run_evaluation_cases()
    assert len(eval_runs) >= 2
    assert db_session.scalar(select(func.count()).select_from(PromptEvaluationRun)) >= 2
    assert {run.run_state for run in eval_runs} <= {"PASS", "SKIPPED"}


def test_output_validation_repairs_syntax_only_and_rejects_unknown_fields(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """```json
    {
      "contract_version": "m12.1.0",
      "agent_key": "PublishingMetadataAgent",
      "status": "OK",
      "confidence_label": "HIGH",
      "risk_level": "LOW",
      "evidence_refs": [],
      "limitations": [],
      "next_action": null,
      "operator_summary_vi": "Đã kiểm tra.",
      "technical_appendix": {},
      "artifact": {}
    }
    ```"""
    valid = service.validate_output(PromptOutputValidationRequest(agent_key="PublishingMetadataAgent", raw_output=raw))
    assert valid.status == "OK"
    assert valid.repair_attempts[0]["repair_type"] == "strip_code_fence"

    invalid = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="PublishingMetadataAgent",
            raw_output={**valid.parsed_output, "unexpected": True},
        )
    )
    assert invalid.status == "REVIEW_REQUIRED"
    assert "Unknown fields" in invalid.validation_result["errors"][0]

    repaired_key = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="PublishingMetadataAgent",
            raw_output={**valid.parsed_output, "agent_key": "publishing_metadata_agent.production@1.0.0"},
        )
    )
    assert repaired_key.status == "OK"
    assert repaired_key.parsed_output["agent_key"] == "PublishingMetadataAgent"
    assert repaired_key.repair_attempts[0]["repair_type"] == "normalize_envelope_agent_key"

    repaired_null = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="PublishingMetadataAgent",
            raw_output={**valid.parsed_output, "risk_level": "null"},
        )
    )
    assert repaired_null.status == "OK"
    assert repaired_null.parsed_output["risk_level"] is None


def test_ollama_and_router_transmit_system_user_messages(db_session, monkeypatch) -> None:
    payload = OllamaLLMProvider().build_chat_payload(
        request=OllamaChatRequest(
            model="gpt-oss:20b-cloud",
            messages=[
                {"role": "system", "content": "system contract"},
                {"role": "user", "content": "task payload"},
            ],
            response_format="json",
        )
    )
    assert payload["messages"] == [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "task payload"},
    ]
    assert payload["format"] == "json"

    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("VCOS_LLM_PROVIDER", "ollama")
    provider = SequenceProvider(
        [
            ProviderResponse(
                ok=True,
                output={"content": '{"ok":true}', "json": {"ok": True}, "usage": {"prompt_eval_count": 2}},
                latency_ms=2,
            )
        ]
    )
    result = LLMRouterService(db_session, provider=provider).route(
        lane_name="cheap_structured",
        messages=[
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "task payload"},
        ],
        requested_task_type="json_schema_output",
        response_format="json",
    )
    assert result.status == "SUCCESS"
    assert provider.calls[0].messages[0]["role"] == "system"
    attempt = db_session.scalars(select(ProviderAttempt)).one()
    assert attempt.metadata_["router_lane"] == "cheap_structured"
    assert attempt.metadata_["validation_outcome"] == "VCOS_VALIDATION_PENDING"
