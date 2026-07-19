from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.contracts.long_production import (
    LongFormRenderPackageStrictContract,
    LongProductionExecutionMode,
    ProductionRenderExecutionEnvelope,
    ResolvedMediaAsset,
    VisualSourceBinding,
)
from app.contracts.m10_2 import LongFormRenderPackageCreate
from app.contracts.visual_routing import SourceFallbackClass, VisualSourceRoute
from app.services.native_ffmpeg_renderer import NativeFFmpegRenderer
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import stable_hash
from tests.test_nr1_native_renderer_architecture import WORK, plan


def _envelope(manifest):
    payload = {
        "execution_mode": LongProductionExecutionMode.REAL_APPROVED_PRODUCTION,
        "project_ref": "video-project://fixture",
        "package_ref": "scripted-package://fixture",
        "plan_ref": manifest.source_plan_ref,
        "plan_hash": manifest.source_plan_hash,
        "production_eligible": True,
        "operator_approval_ref": "operator-approval://fixture/PASS",
        "provider_execution_plan_ref": "provider-plan://fixture",
        "cost_snapshot_ref": "cost-snapshot://fixture",
        "human_review_policy_ref": "human-review-policy://fixture",
        "archive_policy_ref": "archive-policy://fixture",
        "mr1_scoped_approval_ref": "mr1-approval://fixture/PASS",
        "idempotency_key": "fixture-production-envelope",
    }
    provisional = ProductionRenderExecutionEnvelope(
        **payload,
        authorization_hash="pending",
    )
    return provisional.model_copy(
        update={
            "authorization_hash": stable_hash(
                provisional.model_dump(mode="json", exclude={"authorization_hash"})
            )
        }
    )


def test_production_manifest_requires_exact_authorization_envelope_without_execution() -> None:
    production_plan = plan(
        production_eligible=True,
        purpose="REAL_APPROVED_PRODUCTION",
        created_at=datetime(2026, 7, 19, tzinfo=UTC),
    )
    manifest = NativeMotionCompiler().compile(production_plan)
    renderer = NativeFFmpegRenderer(WORK, smoke_enabled=False, production_enabled=False)
    with pytest.raises(PermissionError, match="PRODUCTION_RENDER_EXECUTION_ENVELOPE_REQUIRED"):
        renderer.authorize(manifest, purpose="REAL_APPROVED_PRODUCTION")
    authorized = renderer.authorize(
        manifest,
        purpose="REAL_APPROVED_PRODUCTION",
        execution_envelope=_envelope(manifest),
    )
    assert authorized["eligible"] is True
    assert authorized["production_eligible"] is True


def test_execution_modes_have_no_hidden_hybrid_and_manual_overrides_are_forbidden() -> None:
    assert {mode.value for mode in LongProductionExecutionMode} == {
        "OFFLINE_FIXTURE",
        "REAL_APPROVED_PRODUCTION",
    }
    from app.contracts.long_production import LongProductionRunRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LongProductionRunRequest.model_validate(
            {"execution_mode": "OFFLINE_FIXTURE", "topic": "forbidden override"}
        )


def _strict_payload(tmp_path):
    audio = tmp_path / "narration.wav"
    asset = tmp_path / "asset.mp4"
    audio.write_bytes(b"audio-fixture")
    asset.write_bytes(b"asset-fixture")
    decision = VisualSourceBinding(
        scene_id="scene-1",
        decision_ref="visual-source-decision://scene-1",
        decision_hash="decision-hash",
        preferred_route=VisualSourceRoute.NATIVE_DIAGRAM,
        fallback_class=SourceFallbackClass.NATIVE_ONLY,
        routing_reason_codes=["FIXTURE"],
        eligibility_gate_refs=["gate://fixture/PASS"],
    )
    resolved = ResolvedMediaAsset(
        asset_id="asset-1",
        scene_id="scene-1",
        source_decision_ref=decision.decision_ref,
        source_decision_hash=decision.decision_hash,
        actual_route=decision.preferred_route,
        local_file_ref=str(asset),
        checksum_sha256="asset-checksum",
        width=1920,
        height=1080,
        duration_ms=1000,
        rights_status="NOT_REQUIRED",
        provenance_refs=["fixture://local-generated"],
        normalization_state="NORMALIZED",
        scene_usage_ref="scene-usage://scene-1",
    )
    values = {
        "scripted_package_ref": "package://fixture",
        "scripted_package_hash": "package-hash",
        "project_ref": "project://fixture",
        "project_hash": "project-hash",
        "channel_profile_version_ref": "profile://v2",
        "compiled_policy_snapshot_ref": "policy://v2",
        "compiled_policy_snapshot_hash": "policy-hash",
        "channel_contract_hash": "channel-contract-hash",
        "niche_contract_digest_ref": "niche-digest://fixture",
        "niche_contract_digest_hash": "niche-digest-hash",
        "effective_context_ref": "effective-context://fixture",
        "effective_context_hash": "effective-context-hash",
        "niche_alignment_dossier_ref": "niche-dossier://fixture",
        "niche_alignment_dossier_hash": "niche-dossier-hash",
        "narration_request_ref": "narration-request://fixture",
        "narration_result_ref": "narration-result://fixture",
        "audio_asset_ref": str(audio),
        "audio_asset_hash": "audio-hash",
        "verified_alignment_ref": "alignment://fixture",
        "verified_alignment_hash": "alignment-hash",
        "canonical_timeline_ref": "timeline://fixture",
        "canonical_timeline_hash": "timeline-hash",
        "caption_track_ref": "caption://fixture",
        "caption_track_hash": "caption-hash",
        "visual_direction_contract_ref": "visual-direction://fixture",
        "visual_direction_contract_hash": "visual-direction-hash",
        "visual_source_decisions": [decision],
        "resolved_assets": [resolved],
        "asset_usage_manifest_ref": "asset-usage://fixture",
        "asset_usage_manifest_hash": "asset-usage-hash",
        "media_normalization_manifest_ref": "normalization://fixture",
        "media_normalization_manifest_hash": "normalization-hash",
        "native_render_policy_snapshot_ref": "native-render-policy://fixture",
        "native_render_policy_snapshot_hash": "native-render-policy-hash",
        "native_render_plan_ref": "native-render-plan://fixture",
        "native_render_plan_hash": "native-render-plan-hash",
        "provider_execution_plan_ref": "provider-plan://fixture",
        "provider_execution_plan_hash": "provider-plan-hash",
        "cost_estimate_snapshot_ref": "cost://fixture",
        "cost_estimate_snapshot_hash": "cost-hash",
        "approval_refs": ["approval://fixture"],
        "idempotency_refs": ["idempotency://fixture"],
        "target_duration_seconds": 1.0,
        "content_hash": "strict-contract-hash",
    }
    return values


@pytest.mark.parametrize(
    "missing",
    [
        "audio_asset_ref",
        "verified_alignment_ref",
        "canonical_timeline_ref",
        "caption_track_ref",
        "visual_source_decisions",
        "resolved_assets",
        "provider_execution_plan_ref",
        "cost_estimate_snapshot_ref",
        "approval_refs",
    ],
)
def test_strict_render_contract_rejects_every_missing_authority(tmp_path, missing) -> None:
    values = _strict_payload(tmp_path)
    values.pop(missing)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LongFormRenderPackageStrictContract.model_validate(values)


def test_legacy_optional_package_is_readable_but_not_strict_render_ready() -> None:
    legacy = LongFormRenderPackageCreate()
    assert legacy.strict_production is False
    with pytest.raises(ValueError, match="STRICT_RENDER_PACKAGE_CONTRACT_REQUIRED"):
        LongFormRenderPackageCreate(strict_production=True)
