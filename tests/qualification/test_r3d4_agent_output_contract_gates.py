from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.db.models import AgentOutputValidationRun, R3D4GateRun, SchemaViolationLedger
from app.services.m12_2 import FirstScriptedVideoPackageService
from app.services.r3d4 import (
    AgentOutputContractRegistry,
    AgentOutputValidationService,
    ArtifactCanonicalizer,
    ArtifactConsistencyGate,
    CharacterConsistencyGate,
    CharacterRuntimeComplianceGate,
    DisclosureConsistencyGate,
    GATE_BLOCK,
    GATE_PASS,
    GATE_REVIEW,
    GateBatchResult,
    GateResult,
    MarketRuntimeComplianceGate,
    PackageStatusReducer,
    ProviderBoundaryGate,
    R3D4GateService,
    SRTTimingGate,
    ScriptDurationGate,
    UploadCopyTruthfulnessGate,
    VisualCoverageGate,
    VoiceProfileReadinessGate,
)


RUNTIME_REFS = {
    "effective_context_snapshot_id": "effective-snap-1",
    "compiled_policy_snapshot_id": "policy-snap-1",
    "channel_contract_hash": "channel-hash-1",
    "prompt_context_hash": "prompt-context-1",
    "relevant_contract_paths_used": ["market_locale_context.content_language"],
}


def _ctx(
    *,
    duration: int = 480,
    language: str = "vi",
    allowed_sources: list[str] | None = None,
    character_mode: str = "NO_CHARACTER",
    character_profile_id: str | None = None,
    voice_profile_id: str | None = None,
) -> Any:
    return SimpleNamespace(
        id=None,
        video_project_id=None,
        context_hash="ctx-hash",
        channel_contract_hash="channel-hash-1",
        compile_status="PASS",
        reason_codes_json=[],
        market_locale_context_json={"content_language": language},
        brand_voice_persona_context_json={"forbidden_style": ["hype bait"]},
        category_runtime_context_json={
            "default_format_policy": {
                "target_duration_seconds": duration,
                "min_seconds": duration,
                "max_seconds": int(duration * 1.2),
            }
        },
        character_identity_context_json={
            "character_policy_mode": character_mode,
            "character_profile_id": character_profile_id,
            "character_version_id": "char-v1" if character_profile_id else None,
            "character_image_branch_id": "branch-1" if character_profile_id else None,
            "reference_asset_pack_id": "pack-1" if character_profile_id else None,
        },
        visual_style_context_json={"allowed_visual_sources": allowed_sources or ["DIAGRAM", "CARD", "SCREENSHOT"]},
        voice_audio_context_json={"voice_profile_id": voice_profile_id},
        thumbnail_style_context_json={"text_overlay_language": language},
        metadata_seo_policy_context_json={"language": language},
        publish_timing_context_json={"manual_publish_only": True},
        source_rights_disclosure_context_json={"required_disclosure_blocks": ["AI_ASSISTED_DRAFT"]},
        monetization_cta_context_json={"unsupported_asset_offer_forbidden": True, "forbidden_cta_types": ["fake urgency"]},
        cost_provider_policy_context_json={},
        safety_forbidden_claims_context_json={},
    )


def _script(seconds: list[int], *, declared: int | None = None, language: str = "vi") -> dict[str, Any]:
    payload = {
        "language": language,
        "sentences": [
            {"sentence_id": f"S{index + 1}", "text": f"Sentence {index + 1}", "approx_seconds": seconds_value}
            for index, seconds_value in enumerate(seconds)
        ],
    }
    if declared is not None:
        payload["total_approx_seconds"] = declared
    return payload


def _batch(status: str, fail_codes: list[str] | None = None) -> GateBatchResult:
    result = GateResult(
        gate_key="test_gate",
        status=status,
        severity="CRITICAL" if status == GATE_BLOCK else "HIGH",
        measurements_json={},
        fail_codes=fail_codes or [],
        blocking_refs=[],
        checked_artifact_refs=[],
        checked_contract_paths=[],
        repair_hint=None,
        human_readable_summary="test",
    )
    return GateBatchResult(
        package_id=uuid.uuid4(),
        video_project_id=None,
        effective_context_snapshot_id=None,
        status=status,
        gate_results=[result],
        hard_block_count=1 if status == GATE_BLOCK else 0,
        review_required_count=1 if status == GATE_REVIEW else 0,
        context_hash="ctx-hash",
    )


def test_agent_output_missing_applied_context_refs_blocks_package_critical(db_session) -> None:
    service = AgentOutputValidationService(db_session)

    result = service.validate(
        package_id=uuid.uuid4(),
        video_project_id=None,
        agent_key="ScriptWriterAgent",
        raw_output={"artifact": {"sentences": [{"sentence_id": "S1", "text": "x", "approx_seconds": 10}]}},
        parsed_output={"agent_key": "ScriptWriterAgent", "status": "OK", "artifact": {"sentences": [{"sentence_id": "S1", "text": "x", "approx_seconds": 10}]}},
        prompt_validation_result={"valid": True},
        runtime_context_refs={},
        prompt_render_run_id=None,
        agent_context_pack_snapshot_id=None,
    )

    assert result.status == "BLOCK"
    assert "APPLIED_CONTEXT_REFS_MISSING" in result.reason_codes
    assert db_session.query(SchemaViolationLedger).count() == 1


def test_canonicalizer_preserves_raw_hash_ref_and_does_not_invent_truth() -> None:
    contract = AgentOutputContractRegistry().resolve("ScriptWriterAgent")
    raw_output = {"artifact": {"sentences": [{"sentence_id": "S1", "text": "x", "approx_seconds": 10}]}}

    result = ArtifactCanonicalizer().canonicalize(
        contract=contract,
        raw_output=raw_output,
        parsed_output={"agent_key": "ScriptWriterAgent", "status": "OK", "artifact": raw_output["artifact"]},
        runtime_context_refs={},
        raw_output_ref="prompt-output:abc",
    )

    assert result.canonical_artifact["raw_output_ref"] == "prompt-output:abc"
    assert result.canonical_artifact["raw_output_hash"]
    assert result.canonical_artifact["sentences"] == raw_output["artifact"]["sentences"]
    assert "duration_policy" not in result.canonical_artifact


def test_schema_violation_ledger_records_missing_required_artifact_fields(db_session) -> None:
    service = AgentOutputValidationService(db_session)

    result = service.validate(
        package_id=uuid.uuid4(),
        video_project_id=None,
        agent_key="ScriptWriterAgent",
        raw_output={"artifact": {}},
        parsed_output={"agent_key": "ScriptWriterAgent", "status": "OK", "artifact": {}},
        prompt_validation_result={"valid": True},
        runtime_context_refs=RUNTIME_REFS,
        prompt_render_run_id=None,
        agent_context_pack_snapshot_id=None,
    )

    ledger = db_session.query(SchemaViolationLedger).one()
    assert result.status == "REVIEW_REQUIRED"
    assert "sentences" in ledger.missing_fields
    assert db_session.query(AgentOutputValidationRun).count() == 1


def test_unknown_empty_gatekeeper_result_becomes_review_required(db_session) -> None:
    service = FirstScriptedVideoPackageService(db_session)

    assert service._gatekeeper_result({"artifact": {"result": ""}}) == "REVIEW_REQUIRED"
    assert service._gatekeeper_result({"artifact": {"result": "maybe"}}) == "REVIEW_REQUIRED"


def test_gatekeeper_pass_cannot_override_deterministic_block() -> None:
    decision = PackageStatusReducer().resolve(
        current_status="READY_FOR_MEDIA_PROVIDERS",
        deterministic_batch=_batch(GATE_BLOCK, ["SCRIPT_DURATION_BELOW_MINIMUM"]),
        gatekeeper_result="PASS",
    )

    assert decision["package_status"] == "BLOCKED"


def test_required_gate_missing_blocks_and_persists(db_session) -> None:
    batch = R3D4GateService(db_session).run_batch(
        package_id=uuid.uuid4(),
        video_project_id=None,
        effective_context=_ctx(),
        artifacts={},
        gate_keys=["missing_required_gate"],
    )

    assert batch.status == "BLOCK"
    assert "REQUIRED_GATE_MISSING" in batch.fail_codes
    assert db_session.query(R3D4GateRun).filter(R3D4GateRun.gate_key == "missing_required_gate").count() == 1


def test_script_duration_gate_catches_short_long_form_script() -> None:
    result = ScriptDurationGate().run(artifacts={"narration_script": _script([88, 88], declared=176)}, effective_context=_ctx(duration=480))

    assert result.status == "BLOCK"
    assert "SCRIPT_DURATION_BELOW_MINIMUM" in result.fail_codes


def test_script_duration_gate_catches_declared_duration_mismatch() -> None:
    result = ScriptDurationGate().run(artifacts={"narration_script": _script([88, 88], declared=600)}, effective_context=_ctx(duration=176))

    assert result.status == "BLOCK"
    assert "SCRIPT_DECLARED_DURATION_MISMATCH" in result.fail_codes


def test_srt_timing_gate_catches_overlap_and_bad_numbering() -> None:
    srt = "2\n00:00:00,000 --> 00:00:04,000\nA\n\n3\n00:00:03,000 --> 00:00:05,000\nB\n"

    result = SRTTimingGate().run(artifacts={"srt": {"content": srt}, "narration_script": _script([5])})

    assert result.status == "BLOCK"
    assert "SRT_NUMBERING_NOT_SEQUENTIAL" in result.fail_codes
    assert "SRT_TIMING_OVERLAP" in result.fail_codes


def test_draft_srt_cannot_be_treated_as_final() -> None:
    srt = "1\n00:00:00,000 --> 00:00:05,000\nA\n"

    result = SRTTimingGate().run(
        artifacts={"srt": {"content": srt, "lifecycle_state": "DRAFT_SCRIPT_TIMING", "final": True}, "narration_script": _script([5])}
    )

    assert result.status == "BLOCK"
    assert "DRAFT_SRT_MARKED_FINAL" in result.fail_codes


def test_visual_coverage_gate_catches_missing_sentence_ids() -> None:
    result = VisualCoverageGate().run(
        artifacts={
            "narration_script": _script([5, 5]),
            "visual_plan": {"scenes": [{"sentence_id": "S1", "intended_visual_source": "DIAGRAM"}]},
        },
        effective_context=_ctx(),
    )

    assert result.status == "BLOCK"
    assert "VISUAL_COVERAGE_MISSING_SENTENCE_IDS" in result.fail_codes


def test_visual_coverage_gate_catches_unknown_sentence_refs() -> None:
    result = VisualCoverageGate().run(
        artifacts={
            "narration_script": _script([5]),
            "visual_plan": {"scenes": [{"sentence_id": "S9", "intended_visual_source": "DIAGRAM"}]},
        },
        effective_context=_ctx(),
    )

    assert result.status == "BLOCK"
    assert "VISUAL_PLAN_UNKNOWN_SENTENCE_REFS" in result.fail_codes


def test_visual_coverage_gate_catches_large_duration_mismatch() -> None:
    result = VisualCoverageGate().run(
        artifacts={
            "narration_script": _script([100, 100]),
            "visual_plan": {
                "scenes": [
                    {"sentence_id": "S1", "intended_visual_source": "DIAGRAM", "duration_seconds": 10},
                    {"sentence_id": "S2", "intended_visual_source": "CARD", "duration_seconds": 10},
                ]
            },
        },
        effective_context=_ctx(),
    )

    assert result.status == "BLOCK"
    assert "VISUAL_DURATION_LARGE_MISMATCH" in result.fail_codes


def test_visual_coverage_gate_rejects_disallowed_source_type() -> None:
    result = VisualCoverageGate().run(
        artifacts={
            "narration_script": _script([5]),
            "visual_plan": {"scenes": [{"sentence_id": "S1", "intended_visual_source": "VEO_HERO_CANDIDATE_ONLY"}]},
        },
        effective_context=_ctx(allowed_sources=["DIAGRAM", "CARD"]),
    )

    assert result.status == "BLOCK"
    assert "VISUAL_SOURCE_DISALLOWED_BY_CONTRACT" in result.fail_codes


def test_artifact_consistency_gate_catches_duration_mismatch() -> None:
    result = ArtifactConsistencyGate().run(
        artifacts={"narration_script": _script([100, 100]), "metadata_package": {"duration_seconds": 600}}
    )

    assert result.status == "BLOCK"
    assert "SCRIPT_METADATA_DURATION_MISMATCH" in result.fail_codes


def test_disclosure_consistency_gate_catches_ai_media_wording_mismatch() -> None:
    result = DisclosureConsistencyGate().run(
        artifacts={
            "metadata_package": {"description": "AI-generated video is included."},
            "rights_disclosure_review": {"ai_disclosure_needed": True},
        },
        effective_context=_ctx(),
    )

    assert result.status == "REVIEW_REQUIRED"
    assert "AI_MEDIA_DISCLOSURE_FALSE_PRESENT_TENSE" in result.fail_codes


def test_upload_copy_truthfulness_gate_catches_unsupported_freebie_cta() -> None:
    result = UploadCopyTruthfulnessGate().run(
        artifacts={"upload_card_copy": {"description": "Download the free checklist and product demo now."}}
    )

    assert result.status == "BLOCK"
    assert "UNSUPPORTED_CTA_OR_FREEBIE_CLAIM" in result.fail_codes


def test_provider_boundary_gate_blocks_provider_attempts_when_disabled() -> None:
    readiness = {
        "provider_summaries": [
            {"provider_key": "elevenlabs", "readiness_state": "PASS"},
            {"provider_key": "creatomate", "readiness_state": "PASS"},
        ]
    }

    result = ProviderBoundaryGate().run(
        artifacts={"provider_attempts": [{"provider_key": "ELEVENLABS"}]},
        provider_readiness_state=readiness,
    )

    assert result.status == "BLOCK"
    assert "FORBIDDEN_PROVIDER_OR_UPLOAD_ATTEMPT" in result.fail_codes


def test_provider_boundary_gate_passes_without_upload_publish_or_media_attempts() -> None:
    readiness = {
        "provider_summaries": [
            {"provider_key": "elevenlabs", "readiness_state": "PASS"},
            {"provider_key": "creatomate", "readiness_state": "PASS"},
        ]
    }

    result = ProviderBoundaryGate().run(artifacts={}, provider_readiness_state=readiness)

    assert result.status == "PASS"
    assert result.fail_codes == []


def test_no_character_category_blocks_character_refs() -> None:
    result = CharacterRuntimeComplianceGate().run(
        artifacts={"visual_plan": {"character_refs": ["char-1"]}},
        effective_context=_ctx(character_mode="NO_CHARACTER"),
    )

    assert result.status == "BLOCK"
    assert "NO_CHARACTER_FORBIDS_CHARACTER_REFS" in result.fail_codes


def test_required_character_category_blocks_missing_binding() -> None:
    result = CharacterRuntimeComplianceGate().run(artifacts={}, effective_context=_ctx(character_mode="REQUIRED_CHARACTER"))

    assert result.status == "BLOCK"
    assert "REQUIRED_CHARACTER_BINDING_MISSING" in result.fail_codes


def test_character_refs_must_match_frozen_effective_context() -> None:
    result = CharacterConsistencyGate().run(
        artifacts={"thumbnail_brief": {"character_refs": ["char-2"]}},
        effective_context=_ctx(character_mode="REQUIRED_CHARACTER", character_profile_id="char-1"),
    )

    assert result.status == "BLOCK"
    assert "CHARACTER_REFS_DO_NOT_MATCH_FROZEN_CONTEXT" in result.fail_codes


def test_voice_profile_missing_blocks_when_character_requires_voice() -> None:
    result = VoiceProfileReadinessGate().run(
        artifacts={},
        effective_context=_ctx(character_mode="REQUIRED_CHARACTER", character_profile_id="char-1", voice_profile_id=None),
    )

    assert result.status == "BLOCK"
    assert "VOICE_PROFILE_REQUIRED_MISSING" in result.fail_codes


def test_market_locale_mismatch_causes_review_required() -> None:
    result = MarketRuntimeComplianceGate().run(
        artifacts={"narration_script": _script([10], language="en"), "metadata_package": {"language": "vi"}},
        effective_context=_ctx(language="vi"),
    )

    assert result.status == "REVIEW_REQUIRED"
    assert "MARKET_LOCALE_LANGUAGE_MISMATCH" in result.fail_codes


def test_package_with_deterministic_block_cannot_be_media_ready() -> None:
    decision = PackageStatusReducer().resolve(
        current_status="READY_FOR_MEDIA_PROVIDERS",
        deterministic_batch=_batch(GATE_BLOCK, ["VISUAL_COVERAGE_MISSING_SENTENCE_IDS"]),
        gatekeeper_result="PASS",
    )

    assert decision["package_status"] != "READY_FOR_MEDIA_PROVIDERS"
    assert decision["package_status"] == "BLOCKED"


def test_r3d4_adds_no_provider_media_or_upload_calls() -> None:
    source = (Path("app/services/r3d4.py").read_text(encoding="utf-8") + Path("app/services/m12_2.py").read_text(encoding="utf-8"))
    forbidden = ["GoogleDriveUploadService(", "YouTubeUpload", "ElevenLabsProvider(", "CreatomateProvider(", "VeoProvider("]

    assert [token for token in forbidden if token in source] == []


def test_r3d4_adds_no_vector_rag_or_memory_retrieval() -> None:
    source = (Path("app/services/r3d4.py").read_text(encoding="utf-8") + Path("app/services/m12_2.py").read_text(encoding="utf-8")).lower()
    forbidden = ["similarity_search", "vectorstore", "memory_lake", "embedding_model", "controlled memory"]

    assert [token for token in forbidden if token in source] == []
