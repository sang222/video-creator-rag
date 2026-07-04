from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.contracts.r3d8 import (
    CostEstimateCreateRequest,
    HumanPaidRenderApprovalCreateRequest,
    HumanPaidRenderApprovalDecisionRequest,
    ProviderBoundaryPreflightRequest,
    ProviderIdempotencyKeyCreateRequest,
    ProviderJobCreateRequest,
    ProxyPreviewArtifactFlagCreateRequest,
    RenderRevisionCreateRequest,
)
from app.core.config import Settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    FirstScriptedVideoPackage,
    HumanUploadTask,
    MediaRenderJob,
    PaidProviderCallLedger,
    ProviderAttempt,
    R3D4GateBatchRun,
    RealSmokeRun,
)
from app.services.r3d8 import (
    CostEstimateService,
    HumanPaidRenderApprovalService,
    PaidAttemptLimitGate,
    PaidProviderBoundaryService,
    PexelsUsagePolicyGate,
    ProviderCharacterInputGate,
    ProviderIdempotencyService,
    ProviderJobService,
    ProviderVoiceInputGate,
    ProxyPreviewGate,
    RenderRevisionService,
    VisualSourceMixGate,
)
from tests.test_r3d2_effective_channel_runtime_context import (
    _category,
    _character_binding,
    _compile,
    _project,
    _scope,
)


def _configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        voice_provider="elevenlabs",
        ai_video_hero_provider="luma_api",
        cloud_final_assembly_renderer="creatomate_growth_10k",
        cloud_template_renderer="creatomate_growth_10k",
        free_visual_fallback_provider="pexels_api",
        elevenlabs_api_key="test-elevenlabs-key",
        elevenlabs_voice_id="test-voice-id",
        elevenlabs_model_id="test-voice-model",
        luma_api_key="test-luma-key",
        luma_hero_model="ray-2",
        luma_max_duration_seconds=8,
        luma_video_only=True,
        creatomate_api_key="test-creatomate-key",
        creatomate_template_id="tpl-test",
        creatomate_workspace_id="ws-test",
        pexels_api_key="test-pexels-key",
        provider_real_execution_enabled=False,
        elevenlabs_real_generation_enabled=False,
        luma_real_generation_enabled=False,
        creatomate_real_render_enabled=False,
        pexels_real_search_enabled=False,
        google_drive_real_archive_enabled=False,
        provider_real_readiness_probe_enabled=False,
    )


def _effective(db_session, scope, *, mode: str = "NO_CHARACTER", with_character: bool = False):
    category = _category(db_session, scope, mode=mode)
    refs = _character_binding(db_session, scope, category) if with_character else None
    project = _project(db_session, scope, category=category, binding=refs.binding if refs else None)
    effective = _compile(db_session, project)
    assert effective.compile_status == "PASS"
    return effective, category, refs


def _package(db_session, scope, effective) -> FirstScriptedVideoPackage:
    package = FirstScriptedVideoPackage(
        video_project_id=effective.video_project_id,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=effective.id,
        effective_context_hash=effective.context_hash,
        package_status="READY_FOR_HUMAN_REVIEW",
        artifacts={
            "narration_script": {"text": "A short script."},
            "visual_plan": {"backbone": "DIAGRAM"},
            "thumbnail_brief": {"title": "Concrete outcome"},
            "metadata_package": {"title": "Concrete outcome"},
            "rights_disclosure_review": {"source_manifest_required": True},
        },
        limitations=[],
        risk_limitations_summary={},
        next_action="Human review required.",
    )
    db_session.add(package)
    db_session.flush()
    return package


def _gate_batch(db_session, package, effective, *, status: str = "PASS") -> R3D4GateBatchRun:
    batch = R3D4GateBatchRun(
        package_id=package.id,
        video_project_id=effective.video_project_id,
        effective_context_snapshot_id=effective.id,
        context_hash=effective.context_hash,
        trigger_agent_key="r3d8-test",
        status=status,
        hard_block_count=1 if status == "BLOCK" else 0,
        review_required_count=0,
        gate_results_json=[],
        reducer_decision_json={"decision": status},
    )
    db_session.add(batch)
    db_session.flush()
    return batch


def _stage(provider_key: str, provider_stage: str, cost: str | None = "1.00", **extra) -> dict:
    item = {"provider_key": provider_key, "provider_stage": provider_stage, **extra}
    if cost is not None:
        item["estimated_cost"] = cost
    return item


def _revision(db_session, scope, effective, *, provider_plan: dict, gate_status: str = "PASS"):
    package = _package(db_session, scope, effective)
    _gate_batch(db_session, package, effective, status=gate_status)
    return RenderRevisionService(db_session).create(
        RenderRevisionCreateRequest(
            package_id=package.id,
            provider_plan_json=provider_plan,
            created_by="r3d8-test",
        )
    )


def _approve(db_session, revision, *, stages: list[str], max_cost: str = "10.00", expires_at=None):
    approval_service = HumanPaidRenderApprovalService(db_session)
    approval = approval_service.create_pending(
        HumanPaidRenderApprovalCreateRequest(
            render_revision_id=revision.id,
            max_approved_cost=Decimal(max_cost),
            approved_provider_stages_json=stages,
            rationale="Operator approves validation boundary fixture.",
            expires_at=expires_at,
        )
    )
    return approval_service.approve(
        approval.id,
        HumanPaidRenderApprovalDecisionRequest(
            approved_by="operator",
            max_approved_cost=Decimal(max_cost),
            approved_provider_stages_json=stages,
            expires_at=expires_at,
        ),
    )


def _estimate(db_session, revision, settings=None):
    return CostEstimateService(db_session, settings or _configured_settings()).create(
        CostEstimateCreateRequest(render_revision_id=revision.id)
    )


def _count(db_session, model) -> int:
    return int(db_session.scalar(select(func.count()).select_from(model)) or 0)


def test_render_revision_create_hash_stable_and_supersedes_previous(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    package = _package(db_session, scope, effective)
    _gate_batch(db_session, package, effective)
    provider_plan = {"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]}
    service = RenderRevisionService(db_session)

    first = service.create(RenderRevisionCreateRequest(package_id=package.id, provider_plan_json=provider_plan))
    second = service.create(RenderRevisionCreateRequest(package_id=package.id, provider_plan_json=provider_plan))
    db_session.refresh(first)

    assert first.revision_status == "SUPERSEDED"
    assert second.revision_no == 2
    assert second.revision_status == "READY_FOR_COST_ESTIMATE"
    assert second.render_plan_hash == first.render_plan_hash


def test_cost_estimate_empty_provider_plan_pending_and_pexels_manifest_required(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    empty_revision = _revision(db_session, scope, effective, provider_plan={"provider_stages": []})

    empty_estimate = CostEstimateService(db_session, _configured_settings()).create(
        CostEstimateCreateRequest(render_revision_id=empty_revision.id)
    )

    assert empty_estimate.estimate_status == "ESTIMATE_PENDING_PROVIDER_CONFIG"
    assert empty_estimate.estimated_total_cost is None
    assert "PROVIDER_PLAN_EMPTY" in empty_estimate.blocker_reason_codes_json

    pexels_revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={"provider_stages": [_stage("pexels_api", "PEXELS_SEARCH", None, usage_role="short_broll")]},
    )
    pexels_estimate = CostEstimateService(db_session, _configured_settings()).create(
        CostEstimateCreateRequest(render_revision_id=pexels_revision.id)
    )

    assert pexels_estimate.estimated_pexels_cost == Decimal("0")
    assert pexels_estimate.estimate_status == "BLOCKED"
    assert "PEXELS_ATTRIBUTION_USAGE_MANIFEST_REQUIRED" in pexels_estimate.blocker_reason_codes_json


def test_human_paid_approval_required_and_rejected_revoked_expired_block(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]},
    )
    estimate = _estimate(db_session, revision)
    boundary = PaidProviderBoundaryService(db_session, _configured_settings())
    request = ProviderBoundaryPreflightRequest(
        render_revision_id=revision.id,
        provider_key="creatomate_growth_10k",
        provider_stage="FINAL_ASSEMBLY_RENDER",
        request_payload_json={"template_id": "tpl-test"},
        cost_estimate_snapshot_id=estimate.id,
    )

    missing = boundary.preflight(request)
    assert missing.status == "WAITING_HUMAN_PAID_APPROVAL"
    assert "HUMAN_PAID_APPROVAL_MISSING" in missing.reason_codes

    approval_service = HumanPaidRenderApprovalService(db_session)
    rejected = approval_service.create_pending(HumanPaidRenderApprovalCreateRequest(render_revision_id=revision.id))
    approval_service.reject(rejected.id)
    rejected_decision = boundary.preflight(request.model_copy(update={"human_approval_id": rejected.id}))
    assert "HUMAN_PAID_APPROVAL_REJECTED" in rejected_decision.reason_codes

    revoked = approval_service.create_pending(HumanPaidRenderApprovalCreateRequest(render_revision_id=revision.id))
    approval_service.revoke(revoked.id)
    revoked_decision = boundary.preflight(request.model_copy(update={"human_approval_id": revoked.id}))
    assert "HUMAN_PAID_APPROVAL_REVOKED" in revoked_decision.reason_codes

    expired = _approve(db_session, revision, stages=["FINAL_ASSEMBLY_RENDER"], expires_at=utc_now() - timedelta(minutes=1))
    expired_decision = boundary.preflight(request.model_copy(update={"human_approval_id": expired.id}))
    assert expired_decision.status == "WAITING_HUMAN_PAID_APPROVAL"
    assert "HUMAN_PAID_APPROVAL_EXPIRED" in expired_decision.reason_codes


def test_provider_idempotency_key_stable_and_changes_by_revision_or_request(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    plan = {"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]}
    revision = _revision(db_session, scope, effective, provider_plan=plan)
    service = ProviderIdempotencyService(db_session)
    payload = {"template_id": "tpl-test", "modifications": {"title": "A"}}

    first = service.get_or_create(
        ProviderIdempotencyKeyCreateRequest(
            render_revision_id=revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            request_payload_json=payload,
        )
    )
    second = service.get_or_create(
        ProviderIdempotencyKeyCreateRequest(
            render_revision_id=revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            request_payload_json=payload,
        )
    )
    changed_request = service.get_or_create(
        ProviderIdempotencyKeyCreateRequest(
            render_revision_id=revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            request_payload_json={**payload, "modifications": {"title": "B"}},
        )
    )
    new_revision = _revision(db_session, scope, effective, provider_plan=plan)
    changed_revision = service.get_or_create(
        ProviderIdempotencyKeyCreateRequest(
            render_revision_id=new_revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            request_payload_json=payload,
        )
    )

    assert first.id == second.id
    assert first.idempotency_key == second.idempotency_key
    assert changed_request.idempotency_key != first.idempotency_key
    assert changed_revision.idempotency_key != first.idempotency_key


def test_paid_attempt_limit_first_attempt_only_and_new_revision_resets(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    plan = {"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]}
    revision = _revision(db_session, scope, effective, provider_plan=plan)
    gate = PaidAttemptLimitGate(db_session)

    first = gate.record_attempt(
        render_revision_id=revision.id,
        provider_key="creatomate_growth_10k",
        provider_stage="FINAL_ASSEMBLY_RENDER",
    )
    first_status = first.status
    first_attempt_count = first.attempt_count
    second = gate.record_attempt(
        render_revision_id=revision.id,
        provider_key="creatomate_growth_10k",
        provider_stage="FINAL_ASSEMBLY_RENDER",
    )
    fresh_revision = _revision(db_session, scope, effective, provider_plan=plan)
    _estimate(db_session, fresh_revision)
    _approve(db_session, fresh_revision, stages=["FINAL_ASSEMBLY_RENDER"])
    fresh = gate.record_attempt(
        render_revision_id=fresh_revision.id,
        provider_key="creatomate_growth_10k",
        provider_stage="FINAL_ASSEMBLY_RENDER",
    )

    assert first_status == "PASS"
    assert first_attempt_count == 1
    assert second.status == "BLOCKED"
    assert "PAID_ATTEMPT_LIMIT_EXCEEDED" in second.reason_codes_json
    assert fresh.status == "PASS"
    assert fresh.attempt_count == 1


def test_deterministic_gate_block_blocks_boundary_and_records_ledger(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]},
        gate_status="BLOCK",
    )
    estimate = _estimate(db_session, revision)
    approval = _approve(db_session, revision, stages=["FINAL_ASSEMBLY_RENDER"])

    decision = PaidProviderBoundaryService(db_session, _configured_settings()).preflight(
        ProviderBoundaryPreflightRequest(
            render_revision_id=revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            request_payload_json={"template_id": "tpl-test"},
            cost_estimate_snapshot_id=estimate.id,
            human_approval_id=approval.id,
        )
    )
    ledger = db_session.get(PaidProviderCallLedger, decision.ledger_id)

    assert decision.status == "BLOCKED_DETERMINISTIC_GATE"
    assert decision.call_status == "BLOCKED"
    assert "DETERMINISTIC_GATE_BATCH_BLOCK" in decision.reason_codes
    assert ledger is not None
    assert ledger.call_type == "VALIDATION_ONLY"
    assert ledger.call_status == "BLOCKED"


def test_voice_gate_missing_profile_and_invalid_consent_blocks(db_session) -> None:
    scope = _scope(db_session)
    no_voice_effective, _, _ = _effective(db_session, scope)
    revision = _revision(
        db_session,
        scope,
        no_voice_effective,
        provider_plan={"provider_stages": [_stage("elevenlabs", "VOICE_GENERATION", "1.00")]},
    )
    estimate = _estimate(db_session, revision)
    approval = _approve(db_session, revision, stages=["VOICE_GENERATION"])

    decision = PaidProviderBoundaryService(db_session, _configured_settings()).preflight(
        ProviderBoundaryPreflightRequest(
            render_revision_id=revision.id,
            provider_key="elevenlabs",
            provider_stage="VOICE_GENERATION",
            request_payload_json={"text": "hello"},
            cost_estimate_snapshot_id=estimate.id,
            human_approval_id=approval.id,
        )
    )
    assert decision.status == "BLOCKED_VOICE_INPUT"
    assert "VOICE_PROFILE_REQUIRED" in decision.reason_codes

    char_effective, _, refs = _effective(db_session, scope, mode="REQUIRED_CHARACTER", with_character=True)
    refs.voice.consent_status = "MISSING"
    refs.voice.commercial_use_status = "UNKNOWN"
    db_session.flush()
    gate = ProviderVoiceInputGate(db_session).check(
        effective=char_effective,
        provider_key="elevenlabs",
        provider_stage="VOICE_GENERATION",
        request_payload={"voice_profile_id": str(refs.voice.id)},
    )

    assert not gate.passed
    assert "VOICE_CONSENT_NOT_VALID" in gate.reason_codes
    assert "VOICE_COMMERCIAL_USE_NOT_ALLOWED" in gate.reason_codes


def test_character_input_gate_and_luma_duration_boundary(db_session) -> None:
    scope = _scope(db_session)
    no_char_effective, _, _ = _effective(db_session, scope)
    no_character = ProviderCharacterInputGate().check(
        effective=no_char_effective,
        provider_key="luma_api",
        provider_stage="AI_HERO_VIDEO",
        request_payload={"requires_character": True},
    )
    assert not no_character.passed
    assert "NO_CHARACTER_BLOCKS_CHARACTER_PROVIDER_INPUT" in no_character.reason_codes

    char_effective, _, _ = _effective(db_session, scope, mode="REQUIRED_CHARACTER", with_character=True)
    char_effective.reference_asset_pack_id = None
    db_session.flush()
    missing_ref = ProviderCharacterInputGate().check(
        effective=char_effective,
        provider_key="luma_api",
        provider_stage="AI_HERO_VIDEO",
        request_payload={"requires_character": True},
    )
    assert not missing_ref.passed
    assert "REFERENCE_ASSET_PACK_REQUIRED" in missing_ref.reason_codes

    revision = _revision(
        db_session,
        scope,
        no_char_effective,
        provider_plan={"provider_stages": [_stage("luma_api", "AI_HERO_VIDEO", "2.00")]},
    )
    duration = VisualSourceMixGate(_configured_settings()).check(
        revision=revision,
        provider_key="luma_api",
        provider_stage="AI_HERO_VIDEO",
        request_payload={"duration_seconds": 12},
    )
    assert not duration.passed
    assert "LUMA_DURATION_EXCEEDS_8_SECONDS" in duration.reason_codes


def test_pexels_policy_visual_mix_and_proxy_preview_gate(db_session) -> None:
    settings = _configured_settings()
    pexels = PexelsUsagePolicyGate(settings)

    factual = pexels.check(request_payload={"usage_role": "factual_evidence", "attribution_manifest_ref": "manifest"})
    recurring = pexels.check(request_payload={"usage_role": "recurring_host_identity", "attribution_manifest_ref": "manifest"})
    over_limit = pexels.check(
        request_payload={
            "usage_role": "short_broll",
            "attribution_manifest_ref": "manifest",
            "usage_metrics": {
                "clips_per_long": settings.pexels_max_clips_per_long + 1,
                "runtime_pct_per_long": settings.pexels_max_runtime_pct_per_long + 1,
                "same_asset_reuse_per_30_days": settings.pexels_max_same_asset_reuse_per_30_days + 1,
            },
        }
    )

    assert "PEXELS_CANNOT_BE_EVIDENCE_OR_RECURRING_CHARACTER_SOURCE" in factual.reason_codes
    assert "PEXELS_USAGE_ROLE_BLOCKED" in recurring.reason_codes
    assert {
        "PEXELS_MAX_CLIPS_EXCEEDED",
        "PEXELS_RUNTIME_PCT_EXCEEDED",
        "PEXELS_REUSE_LIMIT_EXCEEDED",
    }.issubset(set(over_limit.reason_codes))

    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={"provider_stages": [_stage("pexels_api", "PEXELS_SEARCH", None)]},
    )
    visual_mix = VisualSourceMixGate(settings).check(revision=revision, request_payload={"visual_backbone": "PEXELS"})
    assert not visual_mix.passed
    assert "PEXELS_CANNOT_BE_CORE_VISUAL_BACKBONE" in visual_mix.reason_codes

    flag = ProxyPreviewGate(db_session).flag(
        ProxyPreviewArtifactFlagCreateRequest(
            artifact_ref="preview://draft-card",
            video_project_id=effective.video_project_id,
            package_id=revision.package_id,
            source_type="PROXY_PREVIEW",
        )
    )
    assert flag.preview_only and flag.not_final_media and flag.not_publishable
    proxy_check = ProxyPreviewGate(db_session).check_not_final_media(artifact_ref="preview://draft-card")
    assert not proxy_check.passed
    assert "PROXY_PREVIEW_ARTIFACT_NOT_PUBLISHABLE" in proxy_check.reason_codes

    try:
        ProxyPreviewGate(db_session).flag(
            ProxyPreviewArtifactFlagCreateRequest(
                artifact_ref="preview://bad",
                video_project_id=effective.video_project_id,
                package_id=revision.package_id,
                source_type="PROXY_PREVIEW",
                not_publishable=False,
            )
        )
    except ValidationFailureError as exc:
        assert "PROXY_PREVIEW_FLAGS_MUST_BE_NON_PUBLISHABLE" in str(exc)
    else:
        raise AssertionError("proxy preview artifact was allowed to become publishable")


def test_provider_job_timeout_maps_to_resume_required(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]},
    )
    service = ProviderJobService(db_session)
    job = service.create_not_submitted(
        ProviderJobCreateRequest(
            render_revision_id=revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            provider_request_json={"template_id": "tpl-test"},
        )
    )

    timeout = service.mark_timeout_resume_required(job.id)

    assert timeout.job_status == "RESUME_REQUIRED"
    assert timeout.last_error_code == "PROVIDER_JOB_TIMED_OUT"
    assert _count(db_session, ProviderAttempt) == 0


def test_default_flags_false_allow_validation_only_without_provider_media_upload_calls(db_session) -> None:
    scope = _scope(db_session)
    effective, _, refs = _effective(db_session, scope, mode="REQUIRED_CHARACTER", with_character=True)
    revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={"provider_stages": [_stage("elevenlabs", "VOICE_GENERATION", "1.00")]},
    )
    estimate = _estimate(db_session, revision)
    approval = _approve(db_session, revision, stages=["VOICE_GENERATION"])

    decision = PaidProviderBoundaryService(db_session, _configured_settings()).preflight(
        ProviderBoundaryPreflightRequest(
            render_revision_id=revision.id,
            provider_key="elevenlabs",
            provider_stage="VOICE_GENERATION",
            call_type="SUBMIT",
            request_payload_json={"text": "hello", "voice_profile_id": str(refs.voice.id)},
            cost_estimate_snapshot_id=estimate.id,
            human_approval_id=approval.id,
            real_call_requested=True,
        )
    )

    assert decision.allowed is True
    assert decision.will_execute is False
    assert decision.no_network_call_made is True
    assert decision.status == "ALLOWED_NOT_EXECUTED"
    assert decision.call_status == "ALLOWED_NOT_EXECUTED"
    assert "PROVIDER_REAL_EXECUTION_DISABLED" in decision.reason_codes
    assert _count(db_session, ProviderAttempt) == 0
    assert _count(db_session, RealSmokeRun) == 0
    assert _count(db_session, MediaRenderJob) == 0
    assert _count(db_session, HumanUploadTask) == 0
    assert _count(db_session, PaidProviderCallLedger) == 1


def test_r3d8_source_guards_no_real_provider_upload_or_youtube_execution() -> None:
    settings_defaults = Settings.model_fields
    assert settings_defaults["provider_real_execution_enabled"].default is False
    assert settings_defaults["elevenlabs_real_generation_enabled"].default is False
    assert settings_defaults["luma_real_generation_enabled"].default is False
    assert settings_defaults["creatomate_real_render_enabled"].default is False
    assert settings_defaults["pexels_real_search_enabled"].default is False
    assert settings_defaults["google_drive_real_archive_enabled"].default is False

    source = Path("app/services/r3d8.py").read_text(encoding="utf-8")
    forbidden = [
        "ElevenLabsVoiceAdapter(",
        "LumaHeroVideoAdapter(",
        "CreatomateFinalRendererAdapter(",
        "PexelsVisualFallbackAdapter(",
        "DriveArchiveAdapter(",
        "requests.",
        "httpx.",
        "youtube.videos().insert",
        "YouTubeUpload",
        "ProviderAttempt(",
        "MediaRenderJob(",
        "HumanUploadTask(",
        "ChannelProfileVersion(",
    ]
    for marker in forbidden:
        assert marker not in source
