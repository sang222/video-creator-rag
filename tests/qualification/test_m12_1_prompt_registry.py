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
            "ai_hero_provider": "LUMA_API",
            "ai_hero_model_id": "luma_api_video_only",
            "ai_hero_allowed_durations_seconds": [4, 6, 8],
            "ai_hero_default_duration_seconds": 8,
            "ai_hero_audio": False,
            "renderer": "CREATOMATE_GROWTH_10K",
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

    moved_risk = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="TopicIdeaScoringAgent",
            raw_output={**valid.parsed_output, "agent_key": "TopicIdeaScoringAgent", "risk_level": "LOW"},
        )
    )
    assert moved_risk.status == "OK"
    assert "risk_level" not in moved_risk.parsed_output
    assert moved_risk.parsed_output["artifact"]["risk_assessment"]["risk_level"] == "LOW"
    assert moved_risk.repair_attempts[0]["repair_type"] == "move_top_level_risk_level_to_artifact"


def _topic_envelope(**overrides) -> dict:
    envelope = {
        "contract_version": "m12.1.0",
        "agent_key": "TopicIdeaScoringAgent",
        "status": "REVIEW_REQUIRED",
        "confidence_label": "LOW",
        "evidence_refs": [{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "int2"}],
        "limitations": ["20-hour claim requires human verification."],
        "next_action": "HUMAN_REVIEW_REQUIRED",
        "operator_summary_vi": "Topic needs review before publication.",
        "technical_appendix": {},
        "artifact": {"topic_score": {"score": "UNKNOWN"}},
    }
    envelope.update(overrides)
    return envelope


def test_topic_idea_scoring_extracts_base_envelope_from_prose_without_silent_success(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    I will explain first, which is invalid prose.
    Here is a small artifact example: {"topic":"bad intermediate object"}.
    {
      "contract_version": "m12.1.0",
      "agent_key": "TopicIdeaScoringAgent",
      "status": "REVIEW_REQUIRED",
      "confidence_label": "LOW",
      "evidence_refs": [{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "int2"}],
      "limitations": ["20-hour claim requires human verification."],
      "next_action": "HUMAN_REVIEW_REQUIRED",
      "operator_summary_vi": "Topic needs review before publication.",
      "technical_appendix": {},
      "artifact": {"topic_score": {"score": "UNKNOWN"}}
    }
    trailing prose is also invalid outside the extracted object.
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["agent_key"] == "TopicIdeaScoringAgent"
    assert repaired.repair_attempts == [
        {
            "repair_type": "extract_base_envelope_json_object",
            "semantic_change_allowed": False,
            "reason_codes": ["BASE_ENVELOPE_OBJECT_EXTRACTED_FROM_TEXT"],
        }
    ]


def test_topic_idea_scoring_repairs_fenced_json_only_when_content_is_valid_json(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """```json
    {
      "contract_version": "m12.1.0",
      "agent_key": "TopicIdeaScoringAgent",
      "status": "REVIEW_REQUIRED",
      "confidence_label": "LOW",
      "evidence_refs": [],
      "limitations": ["Needs evidence."],
      "next_action": "HUMAN_REVIEW_REQUIRED",
      "operator_summary_vi": "Topic needs review.",
      "technical_appendix": {},
      "artifact": {"topic_score": {"score": "UNKNOWN"}}
    }
    ```"""

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.repair_attempts == [{"repair_type": "strip_code_fence", "semantic_change_allowed": False}]


def test_topic_idea_scoring_malformed_json_remains_error(db_session) -> None:
    service = PromptRegistryService(db_session)

    result = service.validate_output(
        PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output='reasoning first {"contract_version": "m12.1.0", bad')
    )

    assert result.status == "ERROR"
    assert result.reason_codes == ["JSON_PARSE_FAILED"]
    assert result.parsed_output is None


def test_topic_idea_scoring_wraps_valid_artifact_only_output_with_audit(db_session) -> None:
    service = PromptRegistryService(db_session)

    repaired = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="TopicIdeaScoringAgent",
            raw_output={
                "topic_score": {"score": "UNKNOWN"},
                "risk_assessment": {"risk_level": "MEDIUM"},
                "recommendation": "REVIEW_REQUIRED",
            },
        )
    )

    assert repaired.status == "OK"
    assert repaired.parsed_output["agent_key"] == "TopicIdeaScoringAgent"
    assert repaired.parsed_output["artifact"]["topic_score"]["score"] == "UNKNOWN"
    assert repaired.parsed_output["operator_summary_vi"]
    assert repaired.repair_attempts == [
        {
            "repair_type": "wrap_topic_idea_artifact_in_base_envelope",
            "semantic_change_allowed": False,
            "reason_codes": ["TOPIC_IDEA_ARTIFACT_WRAPPED"],
        }
    ]


def test_topic_idea_scoring_completes_missing_operator_summary_with_audit(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = _topic_envelope()
    raw.pop("operator_summary_vi")

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["operator_summary_vi"] == "Chủ đề cần được người vận hành kiểm tra trước khi tiếp tục."
    assert repaired.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["operator_summary_vi"],
            "reason_codes": ["OPERATOR_SUMMARY_VI_COMPLETED"],
        }
    ]


def test_topic_idea_scoring_missing_artifact_does_not_pass_registry_validation(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = _topic_envelope()
    raw.pop("artifact")

    result = service.validate_output(PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output=raw))

    assert result.status == "REVIEW_REQUIRED"
    assert result.validation_result["valid"] is False
    assert any("artifact" in error for error in result.validation_result["errors"])


def test_topic_idea_scoring_rejects_unknown_artifact_only_shape(db_session) -> None:
    service = PromptRegistryService(db_session)

    result = service.validate_output(
        PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output={"topic": "bad intermediate object"})
    )

    assert result.status == "REVIEW_REQUIRED"
    assert result.validation_result["valid"] is False


def test_script_writer_repairs_stray_colon_before_timing_object(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "ScriptWriterAgent",
      "status": "OK",
      "confidence_label": "HIGH",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Review.",
      "operator_summary_vi": "OK.",
      "technical_appendix": {},
      "artifact": {
        "sentences": [
          {
            "sentence_id": "S1",
            "text": "Measure the time before and after automation": {
              "approx_seconds": 8.4
            }
          }
        ]
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ScriptWriterAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["artifact"]["sentences"][0]["text"] == "Measure the time before and after automation"
    assert repaired.parsed_output["artifact"]["sentences"][0]["approx_seconds"] == 8.4
    assert repaired.repair_attempts == [{"repair_type": "repair_stray_colon_object_property", "semantic_change_allowed": False}]


def test_publishing_metadata_repairs_smart_quote_json_string_delimiter(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "PublishingMetadataAgent",
      "status": "REVIEW_REQUIRED",
      "confidence_label": "LOW",
      "evidence_refs": [],
      "limitations": ["Channel contract remains frozen; no channel configuration changes are permitted.”],
      "next_action": "Human review.",
      "operator_summary_vi": "Cần human review.",
      "technical_appendix": {},
      "artifact": {"title": "How One Automation Can Save a Small Team 20 Hours Every Week"}
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="PublishingMetadataAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["limitations"] == [
        "Channel contract remains frozen; no channel configuration changes are permitted."
    ]
    assert repaired.repair_attempts == [{"repair_type": "repair_json_smart_quote_delimiters", "semantic_change_allowed": False}]


def test_rights_disclosure_repairs_contract_version_equals_typo(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version="12.1.0",
      "agent_key": "RightsDisclosureReviewer",
      "status": "OK",
      "confidence_label": "HIGH",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Rights check hop le cho rehearsal text-only.",
      "technical_appendix": {},
      "artifact": {
        "result": "PASS",
        "source_manifest_status": "NOT_REQUIRED_TEXT_ONLY",
        "ai_disclosure_needed": false,
        "rights_risk": "LOW",
        "disclosure_notes": "Future generated media needs source manifest review before upload."
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="RightsDisclosureReviewer", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["contract_version"] == "m12.1.0"
    assert repaired.parsed_output["agent_key"] == "RightsDisclosureReviewer"
    assert repaired.repair_attempts == [
        {"repair_type": "repair_contract_version_equals_typo", "semantic_change_allowed": False}
    ]


def test_rights_disclosure_repairs_bare_artifact_present_marker(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "RightsDisclosureReviewer",
      "status": "REVIEW_REQUIRED",
      "confidence_label": "HIGH",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Rights review hop le.",
      "technical_appendix": {},
      "artifact": {
        "artifact_present_and_valid",
        "source_manifest_status": "NOT_REQUIRED_TEXT_ONLY",
        "ai_disclosure_needed": false,
        "rights_risk": "LOW",
        "disclosure_notes": "Future media needs review."
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="RightsDisclosureReviewer", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["artifact"]["result"] == "PASS"
    assert repaired.repair_attempts == [
        {"repair_type": "repair_rights_artifact_present_marker", "semantic_change_allowed": False}
    ]


def test_visual_planning_repairs_string_replace_expression_literal(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "VisualPlanningAgent",
      "status": "OK",
      "confidence_label": "MEDIUM",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Visual plan hợp lệ.",
      "technical_appendix": {
        "context_pack_hash": "95775aa6e6cced3d2a1c3449b13b724c7a2c32656065b7821e663fc113dfee94".replace("ee94", "ee95")
      },
      "artifact": {
        "scenes": [{"sentence_id": "S1", "intended_visual_source": "DIAGRAM"}]
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="VisualPlanningAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["technical_appendix"]["context_pack_hash"].endswith("ee95")
    assert repaired.repair_attempts == [{"repair_type": "repair_json_string_replace_expression", "semantic_change_allowed": False}]


def test_visual_planning_repairs_artifact_compliance_chained_properties(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "VisualPlanningAgent",
      "status": "OK",
      "confidence_label": "MEDIUM",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Visual plan hop le.",
      "technical_appendix": {
        "artifact_compliance": {
          "required":"artifact.scenes":"present",
          "scene_source_field":"intended_visual_source":"present",
          "provider_backed_assets":"candidate-only":"confirmed"
        }
      },
      "artifact": {
        "scenes": [{"sentence_id": "S1", "intended_visual_source": "DIAGRAM"}]
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="VisualPlanningAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["technical_appendix"]["artifact_compliance"] == {
        "required_artifact_scenes": "present",
        "scene_source_field_intended_visual_source": "present",
        "provider_backed_assets_candidate_only": "confirmed",
    }
    assert repaired.repair_attempts == [
        {"repair_type": "repair_artifact_compliance_chained_properties", "semantic_change_allowed": False}
    ]


def test_visual_planning_repairs_generic_chained_string_properties(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "VisualPlanningAgent",
      "status": "OK",
      "confidence_label": "MEDIUM",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Visual plan hop le.",
      "technical_appendix": {
        "candidate_only_provider_options_considered": {
          "LUMA_HERO_CANDIDATE_ONLY": {
            "status":"not_selected_in_primary_plan_reason":"Allowed visual sources restrict this plan."
          }
        }
      },
      "artifact": {
        "scenes": [{"sentence_id": "S1", "intended_visual_source": "DIAGRAM"}]
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="VisualPlanningAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["technical_appendix"]["candidate_only_provider_options_considered"][
        "LUMA_HERO_CANDIDATE_ONLY"
    ]["status_not_selected_in_primary_plan_reason"] == "Allowed visual sources restrict this plan."
    assert repaired.repair_attempts == [
        {"repair_type": "repair_chained_string_properties", "semantic_change_allowed": False}
    ]


def test_upload_card_copy_repairs_missing_evidence_refs_array_close(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "UploadCardCopyAgent",
      "status": "OK",
      "confidence_label": "HIGH",
      "evidence_refs": [{"source_type": "OPERATOR_RESEARCH_PACK"}, {"provided": true}, "limitations": ["Manual upload only."],
      "next_action": "Continue.",
      "operator_summary_vi": "Upload copy hop le.",
      "technical_appendix": {},
      "artifact": {"title": "How One Automation Can Save a Small Team 20 Hours Every Week"}
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="UploadCardCopyAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["evidence_refs"] == [
        {"source_type": "OPERATOR_RESEARCH_PACK"},
        {"provided": True},
    ]
    assert repaired.parsed_output["limitations"] == ["Manual upload only."]
    assert repaired.repair_attempts == [
        {
            "repair_type": "repair_missing_evidence_refs_array_close_before_limitations",
            "semantic_change_allowed": False,
        }
    ]


def test_gatekeeper_repairs_unquoted_percent_number_value(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "GatekeeperSoftReviewAgent",
      "status": "OK",
      "confidence_label": "HIGH",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Gatekeeper hop le.",
      "technical_appendix": {"script_metrics": {"duration_variance": -18.13%, "within_allowed_range": true}},
      "artifact": {"risk_assessment": {"level": "LOW"}}
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="GatekeeperSoftReviewAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["technical_appendix"]["script_metrics"]["duration_variance"] == "-18.13%"
    assert repaired.repair_attempts == [
        {"repair_type": "repair_unquoted_percent_number_values", "semantic_change_allowed": False}
    ]


def test_gatekeeper_repairs_unclosed_array_string_before_delimiter(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "GatekeeperSoftReviewAgent",
      "status": "OK",
      "confidence_label": "HIGH",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "Gatekeeper hop le.",
      "technical_appendix": {
        "script_outline_check": {
          "key_findings": [
            "No fake result claims.",
            "Source_refs indicate operator research pack as basis - no external fabrication.
          ]
        }
      },
      "artifact": {"risk_assessment": {"level": "LOW"}}
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="GatekeeperSoftReviewAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["technical_appendix"]["script_outline_check"]["key_findings"][-1].endswith("fabrication.")
    assert repaired.repair_attempts == [
        {"repair_type": "repair_unclosed_string_before_json_delimiter", "semantic_change_allowed": False}
    ]


def test_provider_readiness_moves_nested_artifact_from_technical_appendix(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = {
        "contract_version": "m12.1.0",
        "agent_key": "ProviderReadinessSummaryAgent",
        "status": "OK",
        "confidence_label": "VERY_HIGH",
        "evidence_refs": [],
        "limitations": "Provider gaps are expected before purchase.",
        "next_action": "Review provider credentials.",
        "operator_summary_vi": "Provider readiness cần kiểm tra.",
        "technical_appendix": {
            "provider_ready_summary": {"elevenlabs": {"readiness_state": "BLOCKED"}},
            "artifact": {"providers": {"elevenlabs": {"status": "NEEDS_CREDENTIAL"}}},
        },
    }

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ProviderReadinessSummaryAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["confidence_label"] == "HIGH"
    assert repaired.parsed_output["artifact"]["providers"]["elevenlabs"]["status"] == "NEEDS_CREDENTIAL"
    assert "artifact" not in repaired.parsed_output["technical_appendix"]
    assert repaired.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["artifact", "confidence_label", "limitations", "technical_appendix"],
            "reason_codes": [
                "CONFIDENCE_VERY_HIGH_TO_HIGH_REPAIRED",
                "LIMITATIONS_STRING_LIST_REPAIRED",
                "PROVIDER_READINESS_ARTIFACT_MOVED_FROM_TECHNICAL_APPENDIX",
            ],
        }
    ]


def test_provider_readiness_completes_empty_summary_and_metadata_shapes(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = {
        "contract_version": "m12.1.0",
        "agent_key": "ProviderReadinessSummaryAgent",
        "status": "OK",
        "confidence_label": "MEDIUM",
        "evidence_refs": [],
        "limitations": [{"type": "PROVIDER_GAP", "providers": ["elevenlabs"]}],
        "next_action": ["Add ELEVENLABS_API_KEY.", "Re-run readiness."],
        "operator_summary_vi": "",
        "technical_appendix": {},
        "artifact": {
            "providers": {
                "elevenlabs": {"readiness_state": "BLOCKED"},
                "google-drive": {"readiness_state": "PASS"},
            }
        },
    }

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ProviderReadinessSummaryAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert "Provider cần cấu hình" in repaired.parsed_output["operator_summary_vi"]
    assert repaired.parsed_output["next_action"] == "Add ELEVENLABS_API_KEY.; Re-run readiness."
    assert repaired.parsed_output["limitations"] == ['{"providers":["elevenlabs"],"type":"PROVIDER_GAP"}']
    assert repaired.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["limitations", "next_action", "operator_summary_vi"],
            "reason_codes": [
                "LIMITATIONS_OBJECT_LIST_REPAIRED",
                "NEXT_ACTION_LIST_STRING_REPAIRED",
                "PROVIDER_READINESS_OPERATOR_SUMMARY_REPAIRED",
            ],
        }
    ]


def test_script_planning_repairs_duplicate_standalone_number_after_numeric_property(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "ScriptPlanningAgent",
      "status": "REVIEW_REQUIRED",
      "confidence_label": "MEDIUM",
      "evidence_refs": [],
      "limitations": ["Needs review."],
      "next_action": "HUMAN_REVIEW",
      "operator_summary_vi": "OK.",
      "technical_appendix": {},
      "artifact": {
        "section_budgets": [
          {
            "section_id": "automation_explained",
            "seconds": 90,
            "word_target": 210
            210
          }
        ]
      }
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ScriptPlanningAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["artifact"]["section_budgets"][0]["word_target"] == 210
    assert repaired.repair_attempts == [
        {
            "repair_type": "remove_duplicate_standalone_number_after_numeric_property",
            "semantic_change_allowed": False,
        }
    ]


def test_output_validation_repairs_single_missing_root_closing_delimiter(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "ResearchPackSummarizer",
      "status": "OK",
      "confidence_label": "MEDIUM",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "OK.",
      "technical_appendix": {},
      "artifact": {"summary": "Compact digest."}
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ResearchPackSummarizer", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["artifact"]["summary"] == "Compact digest."
    assert repaired.repair_attempts == [
        {"repair_type": "append_missing_json_closing_delimiters", "semantic_change_allowed": False}
    ]


def test_output_validation_repairs_embedded_agent_key_value(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = """
    {
      "contract_version": "m12.1.0",
      "agent_key": "ResearchPackSummarvinh: "ResearchPackSummarizer",
      "status": "OK",
      "confidence_label": "MEDIUM",
      "evidence_refs": [],
      "limitations": [],
      "next_action": "Continue.",
      "operator_summary_vi": "OK.",
      "technical_appendix": {},
      "artifact": {"summary": "Compact digest."}
    }
    """

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ResearchPackSummarizer", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.parsed_output["agent_key"] == "ResearchPackSummarizer"
    assert repaired.repair_attempts == [
        {"repair_type": "repair_embedded_agent_key_value", "semantic_change_allowed": False}
    ]


def test_topic_idea_scoring_metadata_shape_repairs_and_no_extra_allow(db_session) -> None:
    service = PromptRegistryService(db_session)
    for value in ("debug notes", ["debug notes"], None):
        repaired = service.validate_output(
            PromptOutputValidationRequest(agent_key="TopicIdeaScoringAgent", raw_output=_topic_envelope(technical_appendix=value))
        )
        assert repaired.status == "OK"
        assert isinstance(repaired.parsed_output["technical_appendix"], dict)
        assert repaired.repair_attempts[0]["repair_type"] == "normalize_envelope_metadata_shape"
        assert repaired.repair_attempts[0]["semantic_change_allowed"] is False

    schema = service.repository.load_schema("base_agent_envelope")
    assert schema["additionalProperties"] is False


def test_channel_authority_schema_shape_repair_is_bounded_and_strict(db_session) -> None:
    service = PromptRegistryService(db_session)
    raw = {
        "contract_version": "m12.1.0",
        "agent_key": "ChannelAuthorityAgent",
        "status": "REVIEW_REQUIRED",
        "confidence_label": "MEDIUM",
        "evidence_refs": [{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "int2"}],
        "limitations": ["20-hour claim needs human verification."],
        "next_action": "Review the claim before upload.",
        "operator_summary_vi": "Cần human review.",
        "technical_appendix": "debug notes from model",
        "artifact": {"decision": "REVIEW_REQUIRED", "reason": "Claim needs evidence."},
    }

    repaired = service.validate_output(PromptOutputValidationRequest(agent_key="ChannelAuthorityAgent", raw_output=raw))

    assert repaired.status == "OK"
    assert repaired.validation_result["valid"] is True
    assert repaired.parsed_output["technical_appendix"] == {"repaired_non_object_value": "debug notes from model"}
    assert repaired.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["technical_appendix"],
            "reason_codes": ["TECHNICAL_APPENDIX_OBJECT_REPAIRED"],
        }
    ]

    invalid_status = service.validate_output(
        PromptOutputValidationRequest(agent_key="ChannelAuthorityAgent", raw_output={**raw, "status": "ADMIT", "technical_appendix": {}})
    )
    assert invalid_status.status == "REVIEW_REQUIRED"
    assert "status is not allowed" in invalid_status.validation_result["errors"]

    invalid_artifact = service.validate_output(
        PromptOutputValidationRequest(agent_key="ChannelAuthorityAgent", raw_output={**raw, "artifact": [{"decision": "ADMIT"}]})
    )
    assert invalid_artifact.status == "REVIEW_REQUIRED"
    assert "artifact must be an object or null" in invalid_artifact.validation_result["errors"]
    assert invalid_artifact.repair_attempts[0]["repair_type"] == "normalize_envelope_metadata_shape"

    repaired_limitations = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="ChannelAuthorityAgent",
            raw_output={**raw, "technical_appendix": {}, "limitations": {"claim_review": ["Needs evidence."]}},
        )
    )
    assert repaired_limitations.status == "OK"
    assert repaired_limitations.parsed_output["limitations"] == ["claim_review: Needs evidence."]
    assert repaired_limitations.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["limitations"],
            "reason_codes": ["LIMITATIONS_OBJECT_LIST_REPAIRED"],
        }
    ]

    repaired_summary = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="ChannelAuthorityAgent",
            raw_output={**raw, "technical_appendix": {}, "operator_summary_vi": ""},
        )
    )
    assert repaired_summary.status == "OK"
    assert repaired_summary.parsed_output["operator_summary_vi"] == "ChannelAuthorityAgent cần review: Claim needs evidence."
    assert repaired_summary.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["operator_summary_vi"],
            "reason_codes": ["CHANNEL_AUTHORITY_OPERATOR_SUMMARY_REPAIRED"],
        }
    ]

    repaired_medium_high = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="ThumbnailBriefAgent",
            raw_output={**raw, "agent_key": "ThumbnailBriefAgent", "technical_appendix": {}, "confidence_label": "MEDIUM_HIGH"},
        )
    )
    assert repaired_medium_high.status == "OK"
    assert repaired_medium_high.parsed_output["confidence_label"] == "MEDIUM"
    assert repaired_medium_high.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["confidence_label"],
            "reason_codes": ["CONFIDENCE_MEDIUM_HIGH_TO_MEDIUM_REPAIRED"],
        }
    ]

    moved_moderate_risk = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="ChannelAuthorityAgent",
            raw_output={**raw, "technical_appendix": {}, "risk_level": "MODERATE"},
        )
    )
    assert moved_moderate_risk.status == "OK"
    assert "risk_level" not in moved_moderate_risk.parsed_output
    assert moved_moderate_risk.parsed_output["artifact"]["risk_assessment"]["risk_level"] == "MEDIUM"
    assert moved_moderate_risk.repair_attempts == [
        {
            "repair_type": "move_top_level_risk_level_to_artifact",
            "semantic_change_allowed": False,
            "fields": ["risk_level", "artifact.risk_assessment.risk_level"],
            "reason_codes": ["RISK_LEVEL_MODERATE_TO_MEDIUM_REPAIRED", "TOP_LEVEL_RISK_LEVEL_MOVED_TO_ARTIFACT"],
        }
    ]

    shared_repair = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="PublishingMetadataAgent",
            raw_output={
                **raw,
                "agent_key": "PublishingMetadataAgent",
                "status": "SUCCESS",
                "confidence_label": "UNKNOWN",
                "limitations": "Needs evidence.",
            },
        )
    )
    assert shared_repair.status == "OK"
    assert shared_repair.parsed_output["status"] == "OK"
    assert shared_repair.parsed_output["confidence_label"] == "LOW"
    assert shared_repair.parsed_output["limitations"] == ["Needs evidence."]
    assert shared_repair.parsed_output["technical_appendix"] == {"repaired_non_object_value": "debug notes from model"}
    assert shared_repair.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["confidence_label", "limitations", "status", "technical_appendix"],
            "reason_codes": [
                "CONFIDENCE_UNKNOWN_TO_LOW_REPAIRED",
                "LIMITATIONS_STRING_LIST_REPAIRED",
                "STATUS_SUCCESS_TO_OK_REPAIRED",
                "TECHNICAL_APPENDIX_OBJECT_REPAIRED",
            ],
        }
    ]

    complete_status = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="PublishingMetadataAgent",
            raw_output={**raw, "agent_key": "PublishingMetadataAgent", "status": "COMPLETE", "technical_appendix": {}},
        )
    )
    assert complete_status.status == "OK"
    assert complete_status.parsed_output["status"] == "OK"
    assert complete_status.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["status"],
            "reason_codes": ["STATUS_COMPLETE_TO_OK_REPAIRED"],
        }
    ]

    completed_status = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="PublishingMetadataAgent",
            raw_output={**raw, "agent_key": "PublishingMetadataAgent", "status": "COMPLETED", "technical_appendix": {}},
        )
    )
    assert completed_status.status == "OK"
    assert completed_status.parsed_output["status"] == "OK"
    assert completed_status.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["status"],
            "reason_codes": ["STATUS_COMPLETED_TO_OK_REPAIRED"],
        }
    ]

    ready_for_review_status = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="ThumbnailBriefAgent",
            raw_output={**raw, "agent_key": "ThumbnailBriefAgent", "status": "READY_FOR_HUMAN_REVIEW", "technical_appendix": {}},
        )
    )
    assert ready_for_review_status.status == "OK"
    assert ready_for_review_status.parsed_output["status"] == "OK"
    assert ready_for_review_status.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["status"],
            "reason_codes": ["STATUS_READY_FOR_HUMAN_REVIEW_TO_OK_REPAIRED"],
        }
    ]

    ready_status = service.validate_output(
        PromptOutputValidationRequest(
            agent_key="UploadCardCopyAgent",
            raw_output={**raw, "agent_key": "UploadCardCopyAgent", "status": "READY", "technical_appendix": {}},
        )
    )
    assert ready_status.status == "OK"
    assert ready_status.parsed_output["status"] == "OK"
    assert ready_status.repair_attempts == [
        {
            "repair_type": "normalize_envelope_metadata_shape",
            "semantic_change_allowed": False,
            "fields": ["status"],
            "reason_codes": ["STATUS_READY_TO_OK_REPAIRED"],
        }
    ]


def test_channel_authority_prompt_forbids_bad_envelope_shape(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("ChannelAuthorityAgent")

    assert "technical_appendix must always be an object" in bundle.system_prompt
    assert "limitations must be a list of strings" in bundle.system_prompt
    assert "operator_summary_vi must be a non-empty Vietnamese sentence" in bundle.system_prompt
    assert "Use MEDIUM, not MODERATE" in bundle.system_prompt
    assert "Do not output top-level risk_level" in bundle.system_prompt
    assert "artifact must be an object, never an array or string" in bundle.system_prompt
    assert "Do not put a status key inside artifact" in bundle.system_prompt
    assert "Never omit required top-level fields" in bundle.system_prompt
    assert "Minimal valid REVIEW_REQUIRED shape" in bundle.system_prompt


def test_topic_idea_prompt_requires_full_base_envelope_shape(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("TopicIdeaScoringAgent")

    assert "Return JSON only" in bundle.system_prompt
    assert "Never omit required top-level fields" in bundle.system_prompt
    assert "operator_summary_vi must be a non-empty Vietnamese sentence" in bundle.system_prompt
    assert '"artifact":{"topic_score":{"score":"UNKNOWN"}' in bundle.system_prompt
    assert "Do not output top-level risk_level" in bundle.system_prompt


def test_publishing_metadata_prompt_forbids_smart_quote_json_delimiters(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("PublishingMetadataAgent")

    assert "Return only strict JSON as one complete BaseEnvelope object" in bundle.system_prompt
    assert "Use plain ASCII double quotes" in bundle.system_prompt
    assert "Do not use smart quotes" in bundle.system_prompt
    assert '"agent_key":"PublishingMetadataAgent"' in bundle.system_prompt
    assert '"manual_publishing_copy":""' in bundle.system_prompt


def test_thumbnail_prompt_forbids_package_status_as_envelope_status(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("ThumbnailBriefAgent")

    assert "Return only strict JSON as one complete BaseEnvelope object" in bundle.system_prompt
    assert "Use only allowed top-level status enum values" in bundle.system_prompt
    assert "Do not use package/workflow statuses such as READY_FOR_HUMAN_REVIEW" in bundle.system_prompt


def test_visual_planning_prompt_forbids_json_expressions(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("VisualPlanningAgent")

    assert "Return only strict JSON as one complete BaseEnvelope object" in bundle.system_prompt
    assert "Use only JSON literals" in bundle.system_prompt
    assert "Never write expressions" in bundle.system_prompt
    assert ".replace" in bundle.system_prompt
    assert "Never write chained key/value fragments" in bundle.system_prompt
    assert "artifact must include `scenes`" in bundle.system_prompt


def test_rights_and_gatekeeper_prompts_defer_text_only_media_manifest_gaps(db_session) -> None:
    service = PromptRegistryService(db_session)
    rights = service.repository.load_bundle("RightsDisclosureReviewer")
    gatekeeper = service.repository.load_bundle("GatekeeperSoftReviewAgent")

    assert "Return only strict JSON as one complete BaseEnvelope object" in gatekeeper.system_prompt
    assert "write percentages as strings" in gatekeeper.system_prompt
    assert "M12.2S text-only rehearsal" in rights.system_prompt
    assert "do not mark the text package HIGH risk" in rights.system_prompt
    assert "source_manifest_status=NOT_REQUIRED_TEXT_ONLY" in rights.system_prompt
    assert "future generated media will need provider/source manifest review" in rights.system_prompt
    assert 'never put bare marker strings such as `"artifact_present_and_valid"`' in rights.system_prompt
    assert "scenario claim such as \"can save up to 20 hours\" may PASS provider dry preview" in gatekeeper.system_prompt
    assert "requires human verification before publish" in gatekeeper.system_prompt
    assert "Do not treat that scenario framing as publish approval" in gatekeeper.system_prompt
    assert 'top-level status must be "OK" and artifact.result must be "PASS"' in gatekeeper.system_prompt
    assert 'artifact must include a result field' in gatekeeper.system_prompt


def test_provider_readiness_prompt_requires_top_level_artifact(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("ProviderReadinessSummaryAgent")

    assert "Return only strict JSON as one complete BaseEnvelope object" in bundle.system_prompt
    assert "never VERY_HIGH" in bundle.system_prompt
    assert "top-level artifact.providers" in bundle.system_prompt
    assert "Do not put artifact inside technical_appendix" in bundle.system_prompt


def test_media_qc_prompt_requires_artifact_status_key(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("MediaQCExplanationAgent")

    assert 'put `"status": "NOT_AVAILABLE"` or `"status": "WAITING_MEDIA_GENERATION"` inside the artifact object' in bundle.system_prompt
    assert 'do not use `"artifact.status"`' in bundle.system_prompt
    assert "`artifact_status`" in bundle.system_prompt
    assert "Do not return PASS, QC_PASS, or equivalent when no media file exists" in bundle.system_prompt


def test_upload_card_copy_prompt_requires_strict_base_envelope(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("UploadCardCopyAgent")

    assert "Return only strict JSON as one complete BaseEnvelope object" in bundle.system_prompt
    assert "evidence_refs must be a closed array before limitations begins" in bundle.system_prompt
    assert "technical_appendix must be an object" in bundle.system_prompt


def test_script_writer_prompt_forbids_non_json_literal_values(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("ScriptWriterAgent")

    assert "Return only strict JSON literals" in bundle.system_prompt
    assert "never an expression such as 60 / 140" in bundle.system_prompt


def test_research_pack_summarizer_prompt_keeps_artifact_compact(db_session) -> None:
    service = PromptRegistryService(db_session)
    bundle = service.repository.load_bundle("ResearchPackSummarizer")

    assert "Return only strict JSON as one complete BaseEnvelope object" in bundle.system_prompt
    assert 'agent_key must be exactly "ResearchPackSummarizer"' in bundle.system_prompt
    assert "Do not copy full provider readiness maps" in bundle.system_prompt
    assert "summarize them as a small digest" in bundle.system_prompt


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
