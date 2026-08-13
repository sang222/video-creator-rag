from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from app.contracts.ai_visual_production import (
    AIVisualScenePlan,
    CompiledAIVideoPrompt,
    ai_visual_stable_hash,
    ai_visual_text_hash,
)
from app.core.config import Settings
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.native_render_plan import stable_hash
from app.services.v2_ai_visual_store import SQLAlchemyVeoEffectStore
from app.services.v2_veo_visual_provider import (
    FFmpegV2VeoMediaRuntime,
    GoogleGenAIVeoSDKClient,
    V2_VEO_STORE_DURABILITY,
    V2VeoDownloadedOutput,
    V2VeoDefinitiveProviderError,
    V2VeoEffectRecord,
    V2VeoExecutionAuthorization,
    V2VeoGenerationAuthority,
    V2VeoNormalizationReceipt,
    V2VeoOperationSnapshot,
    V2VeoOperationPersistenceError,
    V2VeoProviderBlocked,
    V2VeoProviderSubmission,
    V2VeoRetryPolicy,
    V2VeoVideoQCReceipt,
    V2VeoVisualProductionProvider,
    _evaluate_video_frames,
    build_v2_veo_provider_config_payload,
)


class MemoryVeoEffectStore:
    durability = V2_VEO_STORE_DURABILITY
    ready = True

    def __init__(self) -> None:
        self.records: dict[str, V2VeoEffectRecord] = {}
        self.events: list[str] = []

    def load_or_prepare(
        self,
        *,
        asset_effect_id: str,
        identity_hash: str,
        request_hash: str,
        authority: V2VeoGenerationAuthority,
        request_journal: Mapping[str, Any],
    ) -> V2VeoEffectRecord:
        self.events.append("LOAD_OR_PREPARE")
        existing = self.records.get(asset_effect_id)
        if existing is not None:
            return existing
        record = V2VeoEffectRecord(
            asset_effect_id=asset_effect_id,
            identity_hash=identity_hash,
            request_hash=request_hash,
            authority=authority.identity_payload,
            request_journal=dict(request_journal),
            prepared_at=datetime.fromisoformat(str(request_journal["prepared_at"])),
        )
        self.records[asset_effect_id] = record
        self.events.append("PREPARED_COMMITTED")
        return record

    def get(self, asset_effect_id: str) -> V2VeoEffectRecord | None:
        return self.records.get(asset_effect_id)

    def compare_and_set(
        self,
        *,
        asset_effect_id: str,
        expected_version: int,
        expected_states: frozenset[str],
        new_state: str,
        patch: Mapping[str, Any],
    ) -> V2VeoEffectRecord:
        current = self.records[asset_effect_id]
        if current.version != expected_version or current.state not in expected_states:
            raise RuntimeError("TEST_STORE_CAS_CONFLICT")
        updated = replace(
            current,
            state=new_state,
            version=current.version + 1,
            **dict(patch),
        )
        self.records[asset_effect_id] = updated
        self.events.append(f"{new_state}_COMMITTED")
        return updated


class FailOperationCommitOnceStore(MemoryVeoEffectStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed_operation_commit = False

    def compare_and_set(self, **kwargs: Any) -> V2VeoEffectRecord:
        if (
            kwargs["new_state"] == "OPERATION_RECORDED"
            and not self.failed_operation_commit
        ):
            self.failed_operation_commit = True
            raise RuntimeError("simulated database commit outage")
        return super().compare_and_set(**kwargs)


class FakeVeoClient:
    retry_policy: Any = V2VeoRetryPolicy()

    def __init__(self) -> None:
        self.submit_count = 0
        self.poll_count = 0
        self.download_count = 0
        self.submit_error: Exception | None = None
        self.operation_id = "models/veo/operations/op-001"
        self.snapshot = V2VeoOperationSnapshot(
            provider_operation_id=self.operation_id,
            provider_status="PROCESSING",
            done=False,
            succeeded=False,
            output_available=False,
        )

    def submit_once(
        self, authority: V2VeoGenerationAuthority
    ) -> V2VeoProviderSubmission:
        self.submit_count += 1
        if self.submit_error is not None:
            raise self.submit_error
        return V2VeoProviderSubmission(
            provider_operation_id=self.operation_id,
            provider_status="SUBMITTED",
            provider_response_id="submit-response-001",
        )

    def poll_exact(self, provider_operation_id: str) -> V2VeoOperationSnapshot:
        self.poll_count += 1
        assert provider_operation_id == self.operation_id
        return self.snapshot

    def download_exact(self, provider_operation_id: str) -> V2VeoDownloadedOutput:
        self.download_count += 1
        assert provider_operation_id == self.operation_id
        return V2VeoDownloadedOutput(
            content=b"provider-video-content",
            provider_response_id="download-response-001",
        )


class FakeVeoMediaRuntime:
    def __init__(self, *, qc_passes: bool = True) -> None:
        self.qc_passes = qc_passes
        self.normalize_count = 0
        self.inspect_count = 0

    def readiness(self) -> Mapping[str, Any]:
        return {"ready": True, "blockers": []}

    def normalize_visual_only(
        self,
        *,
        source: Path,
        destination: Path,
        width: int,
        height: int,
        fps: int,
    ) -> V2VeoNormalizationReceipt:
        self.normalize_count += 1
        assert source.read_bytes() == b"provider-video-content"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"normalized-visual-only-content")
        output_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        return V2VeoNormalizationReceipt(
            input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            output_sha256=output_sha,
            output_size_bytes=destination.stat().st_size,
            width=width,
            height=height,
            fps=fps,
            input_audio_stream_count=1,
            output_audio_stream_count=0,
            contains_audio_stream=False,
            provider_audio_discarded=True,
            ffmpeg_argv_hash="f" * 64,
        )

    def inspect(
        self,
        *,
        asset: Path,
        expected_width: int,
        expected_height: int,
        expected_fps: int,
        expected_duration_seconds: float,
    ) -> V2VeoVideoQCReceipt:
        self.inspect_count += 1
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        checks = {
            "decode_valid": self.qc_passes,
            "video_stream_present": True,
            "duration_valid": True,
            "duration_seconds": expected_duration_seconds,
            "resolution_valid": True,
            "fps_valid": True,
            "provider_audio_discarded": True,
            "not_blank": True,
            "mostly_black_absent": True,
            "not_frozen_throughout": True,
            "sampled_frames_valid": True,
            "sampled_frame_sha256": [
                hashlib.sha256(f"frame-{index}".encode()).hexdigest()
                for index in range(5)
            ],
        }
        return V2VeoVideoQCReceipt(
            result="PASS" if self.qc_passes else "FAIL",
            checks=checks,
            reason_codes=() if self.qc_passes else ("V2_VEO_QC_DECODE_VALID",),
            asset_sha256=digest,
        )


def _authority() -> V2VeoGenerationAuthority:
    catalog = GoogleVeoModelPriceCatalog()
    prompt = (
        "One coherent abstract transformation: a stream of unstructured notes "
        "resolves into stable schema-bound fields, subtle dolly-in, no text, "
        "no people, ending on one validated tool-ready object."
    )
    return V2VeoGenerationAuthority(
        asset_effect_id="asset-effect-scene-003-veo",
        replacement_authority_id="replacement-authority-001",
        replacement_authority_hash=stable_hash({"replacement": 1}),
        visual_production_run_id="visual-production-run-001",
        scene_plan_snapshot_id="scene-plan-snapshot-001",
        style_bible_id="style-bible-001",
        workflow_run_id="workflow-run-001",
        project_id="project-001",
        production_package_artifact_version_id="package-version-001",
        production_package_hash=stable_hash({"package": 1}),
        asset_slot_id="asset-slot-scene-003",
        scene_id="scene-003",
        bound_scene_ids=("scene-003",),
        bound_scene_plan_hashes=(stable_hash({"scene": 3}),),
        primary_asset_owner_scene_id="scene-003",
        ordinal=3,
        route="AI_VIDEO",
        generation_mode="VEO_TEXT_TO_VIDEO",
        asset_acquisition_mode="GENERATED",
        production_visual_policy_version="vcos.production-visual-policy.ai-only.v1",
        production_visual_policy_ref="policy://ai-only/v1",
        production_visual_policy_hash=stable_hash({"visual_policy": "ai-only"}),
        model_id="veo-3.1-fast-generate-preview",
        provider_config_version="vcos.google-veo.production.v1",
        provider_config_hash=stable_hash(
            build_v2_veo_provider_config_payload(
                provider_config_version="vcos.google-veo.production.v1",
                model_id="veo-3.1-fast-generate-preview",
            )
        ),
        catalog_version=catalog.version,
        catalog_ref=catalog.ref,
        catalog_hash=stable_hash(catalog.payload),
        style_bible_ref="artifact-version://style-bible-001",
        style_bible_hash=stable_hash({"style": 1}),
        scene_plan_ref="artifact-version://scene-plan-003",
        scene_plan_hash=stable_hash({"scene": 3}),
        compiled_prompt_ref="artifact-version://compiled-video-prompt-003",
        compiled_prompt_hash=stable_hash({"compiled_prompt": 3}),
        compiled_prompt_content_hash=stable_hash({"compiled_prompt": 3}),
        prompt_compiler_version="veo-prompt-compiler/v2.0.0",
        prompt=prompt,
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        required_semantic_anchors=(
            "core_subject:structured evidence stream",
            "action_or_relation:raw fragments become verified fields",
            "environment:controlled technical environment",
            "visual_goal:show the transformation without text",
        ),
        negative_prompt=(
            "people, faces, human figure, text, logo, watermark, interface screenshot, "
            "presentation slide, three-box flowchart, generic AI robot"
        ),
        idempotency_key="v2-veo-scene-003-generation-001",
        budget_reservation_id="budget-reservation-001",
        budget_reservation_ref="budget-reservation://replacement-001",
        budget_authority_hash=stable_hash({"budget": 1}),
        cost_estimate_ref="cost-estimate://veo-scene-003",
        cost_estimate_hash=stable_hash({"estimate": "0.80"}),
        approval_ref="approval://replacement-visuals-001",
        approval_hash=stable_hash({"approval": 1}),
        estimated_cost_usd=Decimal("0.80"),
        maximum_approved_cost_usd=Decimal("0.80"),
    )


def _execution() -> V2VeoExecutionAuthorization:
    return V2VeoExecutionAuthorization(
        provider_boundary_gate_passed=True,
        provider_real_execution_enabled=True,
        provider_production_execution_enabled=True,
        veo_real_generation_enabled=True,
        credential_configured=True,
        budget_reservation_active=True,
        cost_approval_active=True,
        paid_attempt_available=True,
        replacement_authority_active=True,
    )


def _runtime_settings() -> Settings:
    return Settings(
        _env_file=None,
        GEMINI_API_KEY="unit-test-not-a-real-secret",
        PROVIDER_REAL_EXECUTION_ENABLED=True,
        VCOS_PROVIDER_PRODUCTION_EXECUTION_ENABLED=True,
        VCOS_VEO_REAL_GENERATION_ENABLED=True,
        VEO_MODEL_ID="veo-3.1-fast-generate-preview",
        VEO_DEFAULT_DURATION_SECONDS=8,
        VEO_DEFAULT_RESOLUTION="720p",
        VEO_DEFAULT_ASPECT_RATIO="16:9",
        VEO_DEFAULT_OUTPUT_COUNT=1,
    )


def _service(
    tmp_path: Path,
    *,
    store: MemoryVeoEffectStore | None = None,
    client: FakeVeoClient | None = None,
    media: FakeVeoMediaRuntime | None = None,
) -> tuple[
    V2VeoVisualProductionProvider,
    MemoryVeoEffectStore,
    FakeVeoClient,
    FakeVeoMediaRuntime,
]:
    resolved_store = store or MemoryVeoEffectStore()
    resolved_client = client or FakeVeoClient()
    resolved_media = media or FakeVeoMediaRuntime()
    return (
        V2VeoVisualProductionProvider(
            store=resolved_store,
            client=resolved_client,
            media_runtime=resolved_media,
            workspace_root=tmp_path,
            settings=_runtime_settings(),
            adapter_registered=True,
        ),
        resolved_store,
        resolved_client,
        resolved_media,
    )


def test_submit_commits_prepared_and_submitting_before_one_provider_call(
    tmp_path: Path,
) -> None:
    service, store, client, _ = _service(tmp_path)
    authority = _authority()

    submitted = service.submit_once(authority=authority, execution=_execution())
    duplicate = service.submit_once(authority=authority, execution=_execution())

    assert submitted.state == duplicate.state == "OPERATION_RECORDED"
    assert submitted.provider_operation_id == client.operation_id
    assert submitted.generation_attempt_count == 1
    assert submitted.actual_cost_usd is None
    assert submitted.conservative_settlement_cost_usd == Decimal("0.80")
    assert submitted.cost_settlement_basis == "CONSERVATIVE_CATALOG_ESTIMATE_ACCEPTED"
    assert client.submit_count == 1
    assert store.events.index("PREPARED_COMMITTED") < store.events.index(
        "SUBMITTING_COMMITTED"
    )
    assert store.events.index("SUBMITTING_COMMITTED") < store.events.index(
        "OPERATION_RECORDED_COMMITTED"
    )
    assert submitted.request_journal["automatic_retry_attempts"] == 0
    assert submitted.request_journal["fallback_allowed"] is False
    assert submitted.request_journal["authority"]["scene_plan_hash"] == (
        authority.scene_plan_hash
    )
    assert submitted.request_journal["authority"]["style_bible_hash"] == (
        authority.style_bible_hash
    )
    assert submitted.request_journal["authority"]["cost_estimate_hash"] == (
        authority.cost_estimate_hash
    )


def test_authority_binds_exact_compiled_scene_and_video_prompt() -> None:
    scene_body = {
        "schema_version": "vcos.ai-visual-scene-plan.v1",
        "scene_id": "scene-003",
        "ordinal": 3,
        "narration_unit_ids": ["nu003"],
        "information_unit_ids": ["iu003"],
        "actual_start_ms": 36060,
        "actual_end_ms": 44060,
        "presentation_start_ms": 36060,
        "presentation_end_ms": 44060,
        "scene_meaning": "Unstructured notes become schema-bound fields.",
        "visual_function": "PROCESS",
        "core_subject": "One stream of abstract notes",
        "secondary_subjects": ["Stable fields"],
        "action_or_relation": "The stream resolves into a validated object.",
        "environment": "Abstract technical workspace without UI",
        "visual_goal": "Show constrained transformation",
        "visual_style_direction": "Cinematic technical illustration",
        "composition_direction": "Left-to-right convergence",
        "camera_direction": "Subtle dolly-in",
        "continuity_constraints": ["Preserve blue amber palette"],
        "motion_need": "MOTION_BENEFICIAL",
        "production_route": "AI_VIDEO",
        "primary_asset_slot_id": "asset-slot-scene-003",
        "reuses_primary_asset_from_scene_id": None,
        "asset_reuse_semantic_reason": None,
        "prompt_brief": "One coherent transformation",
        "negative_constraints": ["no text"],
        "factual_risk": "LOW",
        "importance": "HERO",
        "transition_semantic_reason": "NEW_STEP",
        "style_bible_hash": stable_hash({"style": 1}),
        "planning_policy_hash": stable_hash({"policy": 1}),
    }
    scene = AIVisualScenePlan(
        **scene_body, content_hash=ai_visual_stable_hash(scene_body)
    )
    constraints = [
        "no presentation slide",
        "no PowerPoint",
        "no three-box flowchart",
        "no generic infographic card",
        "no text-heavy composition",
        "no fake dashboard",
        "no fake product UI",
        "no floating random labels",
        "no visible generated text",
        "no logo",
        "no watermark",
        "no people",
        "no face",
        "no human figure",
        "no text",
    ]
    prompt = _authority().prompt
    compiled_body = {
        "schema_version": "vcos.compiled-ai-video-prompt.v1",
        "scene_id": scene.scene_id,
        "scene_plan_hash": scene.content_hash,
        "style_bible_hash": scene.style_bible_hash,
        "prompt_compiler_version": "veo-prompt-compiler/v2.0.0",
        "aspect_ratio": "16:9",
        "target_duration_ms": 12_620,
        "provider_generation_duration_ms": 8_000,
        "intrinsic_motion_required": True,
        "provider_audio_usage_policy": "DISCARD",
        "prompt": prompt,
        "negative_constraints": constraints,
        "negative_prompt": ", ".join(constraints),
        "prompt_hash": ai_visual_text_hash(prompt),
        "provider_call_made": False,
    }
    compiled = CompiledAIVideoPrompt(
        **compiled_body, content_hash=ai_visual_stable_hash(compiled_body)
    )
    base = _authority()
    supplied = {
        field: getattr(base, field)
        for field in base.__dataclass_fields__
        if field
        not in {
            "scene_id",
            "asset_slot_id",
            "bound_scene_ids",
            "bound_scene_plan_hashes",
            "primary_asset_owner_scene_id",
            "ordinal",
            "route",
            "generation_mode",
            "asset_acquisition_mode",
            "style_bible_hash",
            "scene_plan_hash",
            "compiled_prompt_ref",
            "compiled_prompt_hash",
            "compiled_prompt_content_hash",
            "prompt_compiler_version",
            "prompt",
            "prompt_hash",
            "required_semantic_anchors",
            "negative_prompt",
            "duration_seconds",
            "aspect_ratio",
            "provider_audio_usage_policy",
        }
    }

    bound = V2VeoGenerationAuthority.from_compiled_visual_authority(
        scene_plan=scene,
        compiled_prompt=compiled,
        **supplied,
    )

    assert bound.scene_id == scene.scene_id
    assert bound.asset_slot_id == scene.primary_asset_slot_id
    assert bound.bound_scene_ids == (scene.scene_id,)
    assert bound.bound_scene_plan_hashes == (scene.content_hash,)
    assert bound.scene_plan_hash == scene.content_hash
    assert bound.style_bible_hash == scene.style_bible_hash
    assert bound.compiled_prompt_hash == compiled.content_hash
    assert bound.compiled_prompt_content_hash == compiled.content_hash
    assert bound.prompt_hash == compiled.prompt_hash
    assert bound.required_semantic_anchors == (
        f"core_subject:{scene.core_subject}",
        f"action_or_relation:{scene.action_or_relation}",
        f"environment:{scene.environment}",
        f"visual_goal:{scene.visual_goal}",
    )
    assert bound.duration_seconds == 8
    assert compiled.target_duration_ms == 12_620
    assert compiled.provider_generation_duration_ms == 8_000
    assert bound.provider_audio_usage_policy == "DISCARD"
    projection = bound.db_identity_projection
    assert projection["effect_identity_hash"] == bound.identity_hash
    assert projection["request_hash"] == bound.request_hash
    assert projection["generation_policy_hash"] == bound.generation_policy_hash
    assert projection["provider_key"] == "google_veo"
    assert projection["retry_allowed"] is False
    assert projection["fallback_allowed"] is False


def test_ambiguous_submit_blocks_forever_without_exact_operation_id(
    tmp_path: Path,
) -> None:
    client = FakeVeoClient()
    client.submit_error = TimeoutError("ambiguous transport close")
    service, store, _, _ = _service(tmp_path, client=client)
    authority = _authority()

    with pytest.raises(V2VeoProviderBlocked) as first:
        service.submit_once(authority=authority, execution=_execution())
    with pytest.raises(V2VeoProviderBlocked) as replay:
        service.submit_once(authority=authority, execution=_execution())

    record = store.get(authority.asset_effect_id)
    assert record is not None
    assert record.state == "FAILED_UNCERTAIN"
    assert record.generation_attempt_count == 1
    assert record.actual_cost_usd is None
    assert record.conservative_settlement_cost_usd == Decimal("0.80")
    assert record.cost_settlement_basis == "CONSERVATIVE_CATALOG_ESTIMATE_UNCERTAIN"
    assert client.submit_count == 1
    assert "V2_VEO_SUBMIT_OUTCOME_UNCERTAIN" in first.value.reason_codes
    assert "V2_VEO_EXACT_OPERATION_ID_REQUIRED" in replay.value.reason_codes


def test_definitive_provider_rejection_records_zero_cost_and_no_retry(
    tmp_path: Path,
) -> None:
    client = FakeVeoClient()
    client.submit_error = V2VeoDefinitiveProviderError(
        "V2_VEO_PROVIDER_REQUEST_REJECTED"
    )
    service, _, _, _ = _service(tmp_path, client=client)
    authority = _authority()

    failed = service.submit_once(authority=authority, execution=_execution())
    replay = service.submit_once(authority=authority, execution=_execution())

    assert failed.state == replay.state == "FAILED_DEFINITIVE"
    assert failed.actual_cost_usd == Decimal("0")
    assert failed.conservative_settlement_cost_usd is None
    assert failed.cost_settlement_basis == "DEFINITIVE_REJECTION_NO_OPERATION"
    assert client.submit_count == 1


def test_operation_commit_failure_recovers_exact_id_without_resubmit(
    tmp_path: Path,
) -> None:
    store = FailOperationCommitOnceStore()
    client = FakeVeoClient()
    service, _, _, _ = _service(tmp_path, store=store, client=client)
    authority = _authority()

    with pytest.raises(V2VeoOperationPersistenceError) as failure:
        service.submit_once(authority=authority, execution=_execution())
    assert failure.value.provider_operation_id == client.operation_id
    client.snapshot = V2VeoOperationSnapshot(
        provider_operation_id=client.operation_id,
        provider_status="PROCESSING",
        done=False,
        succeeded=False,
        output_available=False,
    )

    recovered = service.reconcile_exact_operation(
        authority=authority,
        provider_operation_id=failure.value.provider_operation_id,
        recovery_authority_ref="provider-response-recovery://incident-002",
        recovery_authority_hash=stable_hash({"incident": 2}),
    )

    assert recovered.state == "POLLING"
    assert recovered.provider_operation_id == client.operation_id
    assert recovered.actual_cost_usd is None
    assert recovered.conservative_settlement_cost_usd == Decimal("0.80")
    assert client.submit_count == 1
    assert client.poll_count == 1


def test_exact_operation_recovery_polls_without_resubmitting(tmp_path: Path) -> None:
    client = FakeVeoClient()
    client.submit_error = TimeoutError("ambiguous transport close")
    service, _, _, _ = _service(tmp_path, client=client)
    authority = _authority()
    with pytest.raises(V2VeoProviderBlocked):
        service.submit_once(authority=authority, execution=_execution())
    client.submit_error = None
    client.snapshot = V2VeoOperationSnapshot(
        provider_operation_id=client.operation_id,
        provider_status="SUCCEEDED",
        done=True,
        succeeded=True,
        output_available=True,
    )

    recovered = service.reconcile_exact_operation(
        authority=authority,
        provider_operation_id=client.operation_id,
        recovery_authority_ref="provider-console-recovery://incident-001",
        recovery_authority_hash=stable_hash({"incident": 1}),
    )

    assert recovered.state == "RESPONSE_CAPTURED"
    assert recovered.provider_operation_id == client.operation_id
    assert client.submit_count == 1
    assert client.poll_count == 1
    assert any(
        item["event"] == "OPERATION_IDENTITY_RECOVERED"
        for item in recovered.response_journals
    )


def test_materialize_strips_audio_and_requires_actual_asset_qc(tmp_path: Path) -> None:
    service, store, client, media = _service(tmp_path)
    authority = _authority()
    service.submit_once(authority=authority, execution=_execution())
    client.snapshot = V2VeoOperationSnapshot(
        provider_operation_id=client.operation_id,
        provider_status="SUCCEEDED",
        done=True,
        succeeded=True,
        output_available=True,
    )
    service.poll_once(authority=authority)

    verified = service.materialize(authority=authority)
    duplicate = service.materialize(authority=authority)

    assert verified.state == duplicate.state == "VERIFIED"
    assert verified.production_eligible is True
    assert verified.actual_cost_usd is None
    assert verified.conservative_settlement_cost_usd == Decimal("0.80")
    assert verified.cost_settlement_basis == "CONSERVATIVE_CATALOG_ESTIMATE_ACCEPTED"
    assert verified.normalization_receipt["contains_audio_stream"] is False
    assert verified.normalization_receipt["provider_audio_discarded"] is True
    assert verified.qc_receipt["result"] == "PASS"
    assert verified.qc_receipt["provider_provenance_valid"] is True
    assert verified.qc_receipt["scene_binding_valid"] is True
    assert verified.normalization_receipt["normalization_hash"]
    projection = verified.db_state_projection
    assert projection["state"] == "VERIFIED"
    assert projection["provider_call_count"] == 1
    assert projection["provider_operation_id"] == client.operation_id
    assert projection["provider_response_id"] == "download-response-001"
    assert projection["output_ref"] == verified.normalized_output_ref
    assert projection["output_checksum"] == verified.normalized_output_sha256
    assert projection["output_content_type"] == "video/mp4"
    assert projection["output_width"] == 1280
    assert projection["output_height"] == 720
    assert projection["output_duration_ms"] == 8_000
    assert projection["output_fps"] == Decimal(24)
    assert projection["output_audio_stream_count"] == 0
    assert projection["retry_allowed"] is False
    assert projection["fallback_allowed"] is False
    assert client.download_count == 1
    assert media.normalize_count == 1
    assert media.inspect_count == 1
    assert store.get(authority.asset_effect_id) == verified


def test_qc_failure_is_terminal_and_never_falls_back(tmp_path: Path) -> None:
    media = FakeVeoMediaRuntime(qc_passes=False)
    service, _, client, _ = _service(tmp_path, media=media)
    authority = _authority()
    service.submit_once(authority=authority, execution=_execution())
    client.snapshot = V2VeoOperationSnapshot(
        provider_operation_id=client.operation_id,
        provider_status="SUCCEEDED",
        done=True,
        succeeded=True,
        output_available=True,
    )
    service.poll_once(authority=authority)

    failed = service.materialize(authority=authority)
    with pytest.raises(V2VeoProviderBlocked):
        service.materialize(authority=authority)

    assert failed.state == "BLOCKED"
    assert failed.production_eligible is False
    assert failed.last_error_code == "V2_VEO_QC_DECODE_VALID"
    assert client.submit_count == 1
    assert client.download_count == 1


def test_readiness_rejects_client_without_attested_no_retry(tmp_path: Path) -> None:
    client = FakeVeoClient()
    client.retry_policy = SimpleNamespace(
        total_transport_attempts=2, automatic_retry_attempts=1
    )
    service, _, _, _ = _service(tmp_path, client=client)

    readiness = service.readiness(authority=_authority(), execution=_execution())

    assert readiness.ready is False
    assert "V2_VEO_AUTOMATIC_RETRY_FORBIDDEN" in readiness.blocker_reason_codes
    with pytest.raises(V2VeoProviderBlocked):
        service.submit_once(authority=_authority(), execution=_execution())
    assert client.submit_count == 0


def test_readiness_requires_bound_runtime_settings_and_registered_adapter(
    tmp_path: Path,
) -> None:
    service = V2VeoVisualProductionProvider(
        store=MemoryVeoEffectStore(),
        client=FakeVeoClient(),
        media_runtime=FakeVeoMediaRuntime(),
        workspace_root=tmp_path,
    )

    readiness = service.readiness(authority=_authority(), execution=_execution())

    assert readiness.ready is False
    assert "V2_VEO_RUNTIME_SETTINGS_REQUIRED" in readiness.blocker_reason_codes
    assert "V2_VEO_PRODUCTION_ADAPTER_NOT_REGISTERED" in (
        readiness.blocker_reason_codes
    )


def test_official_sdk_wrapper_requires_one_total_http_attempt() -> None:
    def sdk(attempts: int):
        return SimpleNamespace(
            _api_client=SimpleNamespace(
                _http_options=SimpleNamespace(
                    retry_options=SimpleNamespace(attempts=attempts)
                )
            )
        )

    assert GoogleGenAIVeoSDKClient(sdk(1)).retry_policy.automatic_retry_attempts == 0
    with pytest.raises(ValueError, match="NO_RETRY_ATTESTATION"):
        GoogleGenAIVeoSDKClient(sdk(2))

    projection = GoogleGenAIVeoSDKClient.readiness_projection()
    assert projection.ready is True
    assert projection.sdk_available is True
    assert projection.no_retry_attested is True


def test_authority_rejects_unsealed_provider_transport_config() -> None:
    with pytest.raises(ValueError, match="PROVIDER_CONFIG_HASH_MISMATCH"):
        replace(_authority(), provider_config_hash="0" * 64)


def test_sqlalchemy_store_round_trips_typed_record_evidence() -> None:
    effect_id = str(uuid.uuid4())
    authority = replace(_authority(), asset_effect_id=effect_id)
    prepared_at = datetime.fromisoformat("2026-08-14T08:00:00+00:00")
    submitted_at = datetime.fromisoformat("2026-08-14T08:00:01+00:00")
    completed_at = datetime.fromisoformat("2026-08-14T08:00:02+00:00")
    record = V2VeoEffectRecord(
        asset_effect_id=effect_id,
        identity_hash=authority.identity_hash,
        request_hash=authority.request_hash,
        authority=authority.identity_payload,
        request_journal=authority.request_journal(prepared_at),
        state="FAILED_UNCERTAIN",
        version=3,
        generation_attempt_count=1,
        prepared_at=prepared_at,
        submitted_at=submitted_at,
        completed_at=completed_at,
        actual_cost_usd=None,
        conservative_settlement_cost_usd=Decimal("0.80"),
        cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_UNCERTAIN",
        last_error_code="V2_VEO_SUBMIT_OUTCOME_UNCERTAIN",
    )
    evidence = {
        **SQLAlchemyVeoEffectStore._record_payload(record),
        "record_hash": SQLAlchemyVeoEffectStore._record_hash(record),
        "technical_qc": None,
    }
    row = SimpleNamespace(
        id=uuid.UUID(effect_id),
        effect_identity_hash=authority.identity_hash,
        request_hash=authority.request_hash,
        state=record.state,
        revision=record.version,
        provider_call_count=record.generation_attempt_count,
        qc_evidence=evidence,
    )

    restored = SQLAlchemyVeoEffectStore._record_from_row(row)

    assert restored == record
    assert isinstance(restored.prepared_at, datetime)
    assert restored.actual_cost_usd is None
    assert restored.conservative_settlement_cost_usd == Decimal("0.80")


def test_sqlalchemy_store_persists_exact_content_addressed_journal(
    tmp_path: Path,
) -> None:
    authority = replace(_authority(), asset_effect_id=str(uuid.uuid4()))
    journal = authority.request_journal(
        datetime.fromisoformat("2026-08-14T08:00:00+00:00")
    )

    first_ref = SQLAlchemyVeoEffectStore._persist_journal_static(
        workspace_root=tmp_path,
        asset_effect_id=authority.asset_effect_id,
        kind="request",
        payload=journal,
        hash_key="journal_hash",
    )
    second_ref = SQLAlchemyVeoEffectStore._persist_journal_static(
        workspace_root=tmp_path,
        asset_effect_id=authority.asset_effect_id,
        kind="request",
        payload=journal,
        hash_key="journal_hash",
    )

    destination = tmp_path / first_ref
    assert first_ref == second_ref
    assert destination.is_file()
    assert authority.asset_effect_id not in first_ref
    assert destination.name == f"request-{journal['journal_hash']}.json"
    assert json.loads(destination.read_text(encoding="utf-8")) == journal

    tampered = {**journal, "fallback_allowed": True}
    with pytest.raises(ValueError, match="JOURNAL_HASH_INVALID"):
        SQLAlchemyVeoEffectStore._persist_journal_static(
            workspace_root=tmp_path,
            asset_effect_id=authority.asset_effect_id,
            kind="request",
            payload=tampered,
            hash_key="journal_hash",
        )


def test_ffmpeg_normalization_command_removes_audio_and_verifies_probe(
    tmp_path: Path,
) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    ffmpeg.write_text("fake", encoding="utf-8")
    ffprobe.write_text("fake", encoding="utf-8")
    ffmpeg.chmod(0o755)
    ffprobe.chmod(0o755)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    destination = tmp_path / "normalized.mp4"
    commands: list[list[str]] = []
    probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
            }
        ],
        "format": {"duration": "8.0"},
    }

    def runner(argv: list[str], **kwargs: Any):
        commands.append(argv)
        if argv[0] == str(ffprobe):
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps(probe), stderr=""
            )
        Path(argv[-1]).write_bytes(b"normalized-video")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runtime = FFmpegV2VeoMediaRuntime(
        ffmpeg=str(ffmpeg), ffprobe=str(ffprobe), runner=runner
    )
    receipt = runtime.normalize_visual_only(
        source=source,
        destination=destination,
        width=1280,
        height=720,
        fps=30,
    )

    normalize_argv = next(command for command in commands if "-an" in command)
    assert "-an" in normalize_argv
    assert "-c:a" not in normalize_argv
    assert receipt.contains_audio_stream is False
    assert receipt.provider_audio_discarded is True


def test_frame_qc_rejects_blank_and_frozen_video() -> None:
    frozen = bytes([40]) * (96 * 54)
    frozen_result = _evaluate_video_frames([frozen] * 5)
    moving = [
        bytes((index + offset) % 256 for offset in range(96 * 54))
        for index in (0, 8, 16, 24, 32)
    ]
    moving_result = _evaluate_video_frames(moving)

    assert frozen_result["not_blank"] is False
    assert frozen_result["not_frozen_throughout"] is False
    assert moving_result["not_blank"] is True
    assert moving_result["mostly_black_absent"] is True
    assert moving_result["not_frozen_throughout"] is True


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg runtime is unavailable",
)
def test_real_ffmpeg_runtime_removes_audio_and_passes_video_qc(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-with-audio.mp4"
    normalized = tmp_path / "visual-only.mp4"
    subprocess.run(
        [
            str(shutil.which("ffmpeg")),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    runtime = FFmpegV2VeoMediaRuntime()

    normalization = runtime.normalize_visual_only(
        source=source,
        destination=normalized,
        width=320,
        height=180,
        fps=30,
    )
    qc = runtime.inspect(
        asset=normalized,
        expected_width=320,
        expected_height=180,
        expected_fps=30,
        expected_duration_seconds=1.0,
    )

    assert normalization.input_audio_stream_count == 1
    assert normalization.output_audio_stream_count == 0
    assert normalization.provider_audio_discarded is True
    assert qc.result == "PASS"
    assert qc.checks["audio_stream_count"] == 0
    assert qc.checks["not_blank"] is True
    assert qc.checks["not_frozen_throughout"] is True
