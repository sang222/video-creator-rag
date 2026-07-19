from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.creative_quality_canary import CreativeGateEvidence
from app.contracts.long_production import (
    FinalMediaCloseoutRequest,
    ForcedAlignmentRequest,
    LongFormRenderPackageStrictContract,
    LongProductionExecutionMode,
    LongProductionOrchestrationReceipt,
    LongProductionState,
    LongProductionStatusRead,
    MediaNormalizationItem,
    MediaNormalizationManifest,
    NarrationRequest,
    NarrationResult,
    ProductionRenderExecutionEnvelope,
    ResolvedMediaAsset,
    ReviewMediaCandidate,
    VisualSourceBinding,
)
from app.contracts.native_renderer import (
    AssetRequirement,
    CanvasSpec,
    NativeOverlayPlan,
    NativeRenderPlan,
    NativeRenderScene,
    ResolvedAssetRef,
    TextSafeRegion,
)
from app.contracts.temporal_authority import (
    EditorialSegmentInput,
    FinalNarrationAudio,
    TextSpan,
    VerifiedNarrationAlignment,
    VerifiedNarrationWord,
)
from app.contracts.visual_routing import (
    AuthoritativeOverlayContentKind,
    ExactTextNativeOverlayContract,
    SourceFallbackClass,
    VisualSourceRoute,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    Artifact,
    ArtifactVersion,
    CompiledChannelPolicySnapshot,
    FirstScriptedVideoPackage,
    VideoProject,
)
from app.services.caption_voice_quality import ReadableCaptionCompiler
from app.services.creative_media_qc import (
    CreativePerceptualMediaQC,
    TechnicalMediaQC,
)
from app.services.native_ffmpeg_renderer import (
    FFMPEG_FULL_DEFAULT,
    FFPROBE_FULL_DEFAULT,
    FFmpegCommandBuilder,
    NativeFFmpegRenderer,
)
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.temporal_authority import (
    CanonicalMediaTimelineCompiler,
    SpokenTextNormalizer,
    TemporalAuthorityGate,
)
from app.services.workflow import ArtifactService


ORCHESTRATOR_VERSION = "lpro1.long-production-orchestrator/1.0.0"
ORCHESTRATION_ARTIFACT_TYPE = "long_production_orchestration"
FIXTURE_PURPOSE = "LPRO1_OFFLINE_FIXTURE"
FIXTURE_SCRIPT = (
    "One approved script anchors the workflow. "
    "Local stock motion shows operational context. "
    "Native overlays keep generated visuals accurate and reviewable."
)


@dataclass(frozen=True)
class _PackageAuthority:
    project_id: str
    package_id: str
    company_id: str
    channel_id: str
    project_ref: str
    project_hash: str
    package_ref: str
    package_hash: str
    channel_profile_version_ref: str
    compiled_policy_snapshot_ref: str
    compiled_policy_snapshot_hash: str
    channel_contract_hash: str
    niche_contract_digest_ref: str
    niche_contract_digest_hash: str
    effective_context_ref: str
    effective_context_hash: str
    niche_alignment_dossier_ref: str
    niche_alignment_dossier_hash: str
    script_ref: str
    script_hash: str
    source_text: str
    visual_direction_contract_ref: str
    visual_direction_contract_hash: str
    provider_execution_plan_ref: str
    provider_execution_plan_hash: str
    cost_estimate_snapshot_ref: str
    cost_estimate_snapshot_hash: str
    native_render_policy_snapshot_ref: str
    native_render_policy_snapshot_hash: str
    approval_refs: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    part = path.with_suffix(path.suffix + ".part")
    part.write_text(payload, encoding="utf-8")
    os.replace(part, path)


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    process = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError(f"LPRO1_FFPROBE_FAILED:{path.name}")
    return json.loads(process.stdout)


def _hash_model(model: Any, hash_field: str = "content_hash") -> str:
    return stable_hash(model.model_dump(mode="json", exclude={hash_field}))


def strict_render_package_blockers(
    contract: LongFormRenderPackageStrictContract,
    *,
    plan: NativeRenderPlan | None = None,
) -> list[str]:
    blockers: list[str] = []
    if contract.content_hash != _hash_model(contract):
        blockers.append("STRICT_RENDER_PACKAGE_HASH_MISMATCH")
    audio = Path(contract.audio_asset_ref)
    if not audio.is_file() or audio.is_symlink():
        blockers.append("AUDIO_FILE_MISSING")
    elif _sha256_file(audio) != contract.audio_asset_hash:
        blockers.append("AUDIO_CHECKSUM_MISMATCH")
    for asset in contract.resolved_assets:
        path = Path(asset.local_file_ref)
        if not path.is_file() or path.is_symlink():
            blockers.append(f"ASSET_FILE_MISSING:{asset.scene_id}")
        elif _sha256_file(path) != asset.checksum_sha256:
            blockers.append(f"ASSET_CHECKSUM_MISMATCH:{asset.scene_id}")
        if not asset.provenance_refs or asset.rights_status not in {"CONFIRMED", "NOT_REQUIRED"}:
            blockers.append(f"ASSET_PROVENANCE_INCOMPLETE:{asset.scene_id}")
    if plan is None:
        blockers.append("NATIVE_RENDER_PLAN_MISSING")
    else:
        actual = canonical_plan_hash(plan)
        if actual != contract.native_render_plan_hash or plan.content_hash != actual:
            blockers.append("NATIVE_RENDER_PLAN_HASH_MISMATCH")
        if plan.temporal_authority_mode != "CANONICAL_STRICT":
            blockers.append("CANONICAL_TIMELINE_REQUIRED")
    return sorted(set(blockers))


class LongFormRenderPackageToNativeRenderPlanAdapter:
    """The single package-to-existing-NativeRenderPlan bridge."""

    def adapt(
        self,
        *,
        authority: _PackageAuthority,
        timeline: Any,
        normalized_assets: list[ResolvedMediaAsset],
        decisions: list[VisualSourceBinding],
        audio_path: Path,
        audio_hash: str,
    ) -> NativeRenderPlan:
        by_scene = {item.scene_id: item for item in normalized_assets}
        decisions_by_scene = {item.scene_id: item for item in decisions}
        scenes: list[NativeRenderScene] = []
        for segment in timeline.segments:
            asset = by_scene.get(segment.segment_id)
            decision = decisions_by_scene.get(segment.segment_id)
            if asset is None or decision is None:
                raise ValueError("LPRO1_SCENE_VISUAL_BINDING_MISSING")
            overlay_plan = None
            exact_text = decision.preferred_route == VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY
            text_regions = [
                TextSafeRegion(
                    id=f"{segment.segment_id}-native-overlay",
                    x=0.08,
                    y=0.08,
                    width=0.48,
                    height=0.12,
                    purpose="NATIVE_EXACT_TEXT",
                    minimum_contrast_requirement=4.5,
                    alignment="LEFT",
                )
            ] if exact_text else []
            if exact_text:
                exact_payload = {
                    "scene_id": segment.segment_id,
                    "source_decision_ref": decision.decision_ref,
                    "source_decision_hash": decision.decision_hash,
                    "preferred_source_route": decision.preferred_route,
                    "exact_text_required": True,
                    "exact_number_required": False,
                    "forbidden_generated_text": True,
                    "forbidden_generated_logo": True,
                    "forbidden_generated_fake_ui": True,
                    "native_overlay_required": True,
                    "authoritative_content_kinds": [AuthoritativeOverlayContentKind.HEADLINE],
                    "authoritative_content_refs": ["fixture-content://generated-scene/native-headline"],
                }
                exact_contract = ExactTextNativeOverlayContract(
                    **exact_payload,
                    content_hash=stable_hash(exact_payload),
                )
                overlay_payload = {
                    "plan_id": f"lpro1-overlay-{segment.segment_id}",
                    "scene_id": segment.segment_id,
                    "source_decision_ref": decision.decision_ref,
                    "source_decision_hash": decision.decision_hash,
                    "preferred_source_route": decision.preferred_route,
                    "exact_text_contract": exact_contract,
                    "text_safe_regions": text_regions,
                    "reserved_overlay_regions": [],
                    "overlay_content_refs": exact_contract.authoritative_content_refs,
                    "native_overlay_required": True,
                }
                overlay_plan = NativeOverlayPlan(
                    **overlay_payload,
                    content_hash=stable_hash(
                        {
                            **overlay_payload,
                            "exact_text_contract": exact_contract.model_dump(mode="json"),
                            "text_safe_regions": [item.model_dump(mode="json") for item in text_regions],
                        }
                    ),
                )
            treatment = {
                VisualSourceRoute.NATIVE_DIAGRAM: "DIAGRAM",
                VisualSourceRoute.PEXELS_VIDEO: "STOCK_VIDEO",
                VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY: "STATIC_COMPOSITION",
            }[decision.preferred_route]
            scenes.append(
                NativeRenderScene(
                    scene_id=segment.segment_id,
                    source_segment_ids=[segment.segment_id],
                    narration_start_ms=segment.scene_start_ms,
                    narration_end_ms=segment.scene_end_ms,
                    duration_ms=segment.target_scene_duration_ms,
                    visual_treatment=treatment,
                    layout_type="FULL_FRAME_WITH_NATIVE_CAPTION_SAFE_AREA",
                    asset_requirements=[AssetRequirement(key=asset.asset_id)],
                    resolved_asset_refs=[
                        ResolvedAssetRef(
                            key=asset.asset_id,
                            path=asset.local_file_ref,
                            checksum=asset.checksum_sha256,
                        )
                    ],
                    animation_type="HOLD_STATIC",
                    transition_in="CUT",
                    transition_out="CUT",
                    originality_role="MECHANISM_EXPLANATION",
                    provider_intent="FIXTURE_RESOLVED_NO_NETWORK",
                    visual_routing_mode="VSR1_STRICT",
                    source_decision_ref=decision.decision_ref,
                    source_decision_hash=decision.decision_hash,
                    preferred_source_route=decision.preferred_route,
                    exact_text_required=exact_text,
                    exact_number_required=False,
                    forbidden_generated_text=True,
                    forbidden_generated_logo=True,
                    forbidden_generated_fake_ui=True,
                    text_safe_regions=text_regions,
                    reserved_overlay_regions=[],
                    eligibility_gate_refs=decision.eligibility_gate_refs,
                    native_overlay_required=exact_text,
                    native_overlay_plan=overlay_plan,
                )
            )
        metrics = timeline.qc_metrics
        creative = {
            name: {"result": "PASS"}
            for name in (
                "NarrationPacingGate",
                "CaptionCompilationGate",
                "CaptionLayoutGate",
                "CaptionSafeAreaGate",
                "CaptionAudioSyncGate",
                "CaptionCoverageGate",
                "TimelineDriftGate",
                "SceneSemanticMatchGate",
                "VisualContinuityGate",
                "AssetAdjacencyGate",
                "FinalDurationConsistencyGate",
            )
        }
        body = {
            "plan_id": f"native-render-plan:{authority.package_id}",
            "plan_version": 1,
            "package_id": authority.package_id,
            "video_project_id": authority.project_id,
            "company_id": authority.company_id,
            "channel_id": authority.channel_id,
            "channel_profile_version_id": authority.channel_profile_version_ref,
            "effective_context_snapshot_id": authority.effective_context_ref,
            "effective_context_hash": authority.effective_context_hash,
            "format_identity_contract_ref": authority.visual_direction_contract_ref,
            "format_identity_contract_hash": authority.visual_direction_contract_hash,
            "format_identity_status": "APPROVED",
            "episode_originality_manifest_ref": f"fixture-originality://{authority.package_id}",
            "episode_originality_manifest_hash": stable_hash({"package": authority.package_hash, "original": True}),
            "final_originality_gate": "PASS",
            "claim_evidence_ledger_refs": [f"fixture-claims://{authority.package_id}"],
            "script_ref": authority.script_ref,
            "script_hash": authority.script_hash,
            "srt_ref": metrics["caption_compilation_ref"],
            "srt_hash": metrics["caption_compilation_hash"],
            "audio_timeline_ref": f"canonical-timeline:{timeline.timeline_hash}",
            "temporal_authority_mode": "CANONICAL_STRICT",
            "canonical_media_timeline_ref": f"canonical-timeline:{timeline.timeline_hash}",
            "canonical_media_timeline_hash": timeline.timeline_hash,
            "canonical_audio_asset_ref": str(audio_path),
            "canonical_caption_compilation_ref": metrics["caption_compilation_ref"],
            "canonical_caption_compilation_hash": metrics["caption_compilation_hash"],
            "canonical_caption_render_payload_hash": metrics["caption_render_payload_hash"],
            "scene_timing_source": "CANONICAL_MEDIA_TIMELINE",
            "caption_timing_source": "CANONICAL_MEDIA_TIMELINE",
            "parallel_timing_inputs": [],
            "visual_plan_ref": authority.visual_direction_contract_ref,
            "visual_plan_hash": authority.visual_direction_contract_hash,
            "visual_direction_contract_ref": authority.visual_direction_contract_ref,
            "visual_direction_contract_hash": authority.visual_direction_contract_hash,
            "creative_gate_results": creative,
            "canvas_spec": CanvasSpec(width=1920, height=1080, fps=30),
            "scenes": scenes,
            "global_motion_policy": {"motion_pack": "NativeMotionPack_v1"},
            "caption_policy": {"authority": "CANONICAL_MEDIA_TIMELINE"},
            "audio_policy": {
                "narration_asset_ref": str(audio_path),
                "narration_asset_hash": audio_hash,
                "sample_rate": 48000,
                "channels": 2,
            },
            "output_profiles": ["YT_LONG_1080P30_SDR_H264_VT"],
            "character_policy_mode": "NO_CHARACTER",
            "purpose": FIXTURE_PURPOSE,
            "production_eligible": False,
            "status": "APPROVED",
            "created_at": datetime(2026, 7, 19, tzinfo=UTC),
            "created_by": "LPRO1_OFFLINE_FIXTURE_AUTHORITY",
        }
        plan = NativeRenderPlan(**body)
        plan.content_hash = canonical_plan_hash(plan)
        return plan


class FinalMediaCloseoutService:
    @staticmethod
    def validate(data: FinalMediaCloseoutRequest) -> dict[str, Any]:
        blockers: list[str] = []
        candidate = data.review_candidate
        if not candidate.production_eligible:
            blockers.append("FINAL_MEDIA_PRODUCTION_ELIGIBILITY_REQUIRED")
        if data.human_review_decision != "PASS" or not data.human_review_receipt_ref:
            blockers.append("FINAL_MEDIA_HUMAN_REVIEW_PASS_REQUIRED")
        if data.reviewed_hash != candidate.output_sha256:
            blockers.append("FINAL_MEDIA_REVIEWED_HASH_MISMATCH")
        if data.technical_qc_result != "PASS":
            blockers.append("FINAL_MEDIA_TECHNICAL_QC_PASS_REQUIRED")
        if data.creative_review_result != "ACCEPTED":
            blockers.append("FINAL_MEDIA_CREATIVE_REVIEW_ACCEPTANCE_REQUIRED")
        if data.archive_required and data.archive_verification_result != "PASS":
            blockers.append("FINAL_MEDIA_ARCHIVE_VERIFICATION_REQUIRED")
        if not data.package_lineage_valid or data.legacy_incomplete_package:
            blockers.append("FINAL_MEDIA_STRICT_PACKAGE_LINEAGE_REQUIRED")
        if not data.provenance_complete or not data.rights_disclosure_resolved:
            blockers.append("FINAL_MEDIA_PROVENANCE_RIGHTS_INCOMPLETE")
        if not data.file_ref or not data.file_checksum:
            blockers.append("FINAL_MEDIA_FILE_REF_CHECKSUM_REQUIRED")
        elif data.file_ref.startswith("archive://"):
            pass
        else:
            path = Path(data.file_ref)
            if not path.is_file() or path.is_symlink():
                blockers.append("FINAL_MEDIA_FILE_MISSING")
            elif _sha256_file(path) != data.file_checksum:
                blockers.append("FINAL_MEDIA_FILE_CHECKSUM_MISMATCH")
        if data.file_checksum and data.file_checksum != candidate.output_sha256:
            blockers.append("FINAL_MEDIA_CANDIDATE_CHECKSUM_MISMATCH")
        if blockers:
            raise ValidationFailureError(";".join(sorted(set(blockers))))
        return {
            "result": "PASS",
            "eligible_for_final_media_registration": True,
            "file_ref": data.file_ref,
            "file_checksum": data.file_checksum,
            "review_candidate_ref": candidate.candidate_id,
        }


class LongProductionOrchestrator:
    def __init__(
        self,
        session: Session | None,
        *,
        workspace_root: Path,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ) -> None:
        self.session = session
        self.workspace_root = workspace_root.resolve()
        self.ffmpeg = ffmpeg or (FFMPEG_FULL_DEFAULT if Path(FFMPEG_FULL_DEFAULT).is_file() else shutil.which("ffmpeg"))
        self.ffprobe = ffprobe or (FFPROBE_FULL_DEFAULT if Path(FFPROBE_FULL_DEFAULT).is_file() else shutil.which("ffprobe"))
        if not self.ffmpeg or not self.ffprobe:
            raise RuntimeError("LPRO1_FFMPEG_RUNTIME_UNAVAILABLE")

    def run(
        self,
        *,
        project_id: uuid.UUID,
        package_id: uuid.UUID | None = None,
        execution_mode: LongProductionExecutionMode = LongProductionExecutionMode.OFFLINE_FIXTURE,
        execution_envelope: ProductionRenderExecutionEnvelope | None = None,
    ) -> LongProductionOrchestrationReceipt:
        if self.session is None:
            raise RuntimeError("LPRO1_DB_SESSION_REQUIRED")
        authority, actor_id = self._authority_from_db(project_id, package_id)
        if execution_mode == LongProductionExecutionMode.REAL_APPROVED_PRODUCTION:
            if execution_envelope is None:
                raise ValidationFailureError("LPRO1_MR1_EXECUTION_ENVELOPE_REQUIRED")
            if execution_envelope.project_ref != authority.project_ref or execution_envelope.package_ref != authority.package_ref:
                raise ValidationFailureError("LPRO1_PRODUCTION_ENVELOPE_LINEAGE_MISMATCH")
            raise ValidationFailureError("LPRO1_REAL_PROVIDER_EXECUTION_REMAINS_MR1_ON_HOLD")
        receipt = self._run_authority(authority)
        self._persist_receipt(project_id=project_id, actor_id=actor_id, receipt=receipt)
        return receipt

    def run_fixture(self) -> LongProductionOrchestrationReceipt:
        return self._run_authority(self._fixture_authority())

    def _run_authority(self, authority: _PackageAuthority) -> LongProductionOrchestrationReceipt:
        lineage_seed = {
            "package_hash": authority.package_hash,
            "project_hash": authority.project_hash,
            "profile": authority.channel_profile_version_ref,
            "policy": authority.compiled_policy_snapshot_hash,
            "effective_context": authority.effective_context_hash,
            "niche_dossier": authority.niche_alignment_dossier_hash,
            "provider_plan": authority.provider_execution_plan_hash,
            "renderer_policy": authority.native_render_policy_snapshot_hash,
            "orchestrator_version": ORCHESTRATOR_VERSION,
        }
        lineage_fingerprint = stable_hash(lineage_seed)
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, lineage_fingerprint))
        root = self.workspace_root / "runs" / run_id
        root.mkdir(parents=True, exist_ok=True)
        completed_path = root / "orchestration_receipt.json"
        if completed_path.is_file() and not completed_path.is_symlink():
            prior = LongProductionOrchestrationReceipt.model_validate_json(
                completed_path.read_text(encoding="utf-8")
            )
            candidate_path = Path(str((root / "review_media_candidate.json")))
            if candidate_path.is_file():
                candidate = ReviewMediaCandidate.model_validate_json(
                    candidate_path.read_text(encoding="utf-8")
                )
                output = Path(candidate.output_file_ref)
                if output.is_file() and _sha256_file(output) == candidate.output_sha256:
                    return prior

        transitions = [LongProductionState.PACKAGE_ACCEPTED.value, LongProductionState.AWAITING_NARRATION.value]
        normalized = SpokenTextNormalizer().normalize(
            script_revision_id=f"script:{authority.script_hash}",
            source_text=authority.source_text,
        )
        duration_ms = len(normalized.spoken_tokens) * 600
        audio_dir = root / "narration"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / "fixture-narration.wav"
        self._materialize_audio(audio_path, duration_ms)
        audio_hash = _sha256_file(audio_path)
        narration_request_payload = {
            "request_id": f"narration-request:{run_id}",
            "scripted_package_ref": authority.package_ref,
            "script_hash": authority.script_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "voice_policy_ref": "fixture-policy://voice/calm-practical-v1",
            "pacing_policy_ref": "fixture-policy://pacing/measured-v1",
            "provider_execution_plan_ref": authority.provider_execution_plan_ref,
            "idempotency_key": stable_hash({"run": run_id, "stage": "narration"}),
            "execution_mode": LongProductionExecutionMode.OFFLINE_FIXTURE,
        }
        narration_request = NarrationRequest(
            **narration_request_payload,
            content_hash=stable_hash(narration_request_payload),
        )
        narration_result_payload = {
            "request_id": narration_request.request_id,
            "provider_key": "LOCAL_DETERMINISTIC_NARRATION_FIXTURE",
            "provider_manifest_ref": str(audio_dir / "provider-manifest.json"),
            "audio_asset_ref": str(audio_path),
            "audio_sha256": audio_hash,
            "duration_ms": duration_ms,
            "sample_rate": 48000,
            "channels": 2,
            "fixture_only": True,
            "provider_call_made": False,
        }
        narration_result = NarrationResult(
            **narration_result_payload,
            content_hash=stable_hash(narration_result_payload),
        )
        _write_json(audio_dir / "narration-request.json", narration_request.model_dump(mode="json"))
        _write_json(audio_dir / "narration-result.json", narration_result.model_dump(mode="json"))
        _write_json(
            audio_dir / "provider-manifest.json",
            {
                "provider": "LOCAL_DETERMINISTIC_NARRATION_FIXTURE",
                "network_call_made": False,
                "audio_sha256": audio_hash,
            },
        )
        transitions.extend([LongProductionState.NARRATION_READY.value, LongProductionState.AWAITING_ALIGNMENT.value])

        alignment_request_payload = {
            "request_id": f"alignment-request:{run_id}",
            "narration_request_id": narration_request.request_id,
            "audio_asset_ref": str(audio_path),
            "audio_sha256": audio_hash,
            "script_hash": authority.script_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "strict_token_coverage": 1.0,
            "estimated_timing_fallback_allowed": False,
            "idempotency_key": stable_hash({"run": run_id, "stage": "alignment"}),
        }
        alignment_request = ForcedAlignmentRequest(
            **alignment_request_payload,
            content_hash=stable_hash(alignment_request_payload),
        )
        words = [
            VerifiedNarrationWord(
                word_id=f"verified-{index + 1:04d}",
                text=token.text,
                start_ms=index * 600,
                end_ms=(index + 1) * 600,
                source_spoken_token_ids=[token.token_id],
                provider_start_ms=index * 600,
                provider_end_ms=(index + 1) * 600,
                forced_start_ms=index * 600,
                forced_end_ms=(index + 1) * 600,
                confidence=1.0,
                reason_codes=["FIXTURE_PROVIDER_TIMING", "FIXTURE_FORCED_ALIGNMENT_VERIFIED"],
            )
            for index, token in enumerate(normalized.spoken_tokens)
        ]
        alignment_payload = {
            "spoken_text_hash": normalized.spoken_text_hash,
            "audio_asset_ref": str(audio_path),
            "audio_duration_ms": duration_ms,
            "verified_words": [item.model_dump(mode="json") for item in words],
            "provider_seed_ref": f"fixture-provider-seed:{narration_request.content_hash}",
            "forced_alignment_ref": f"fixture-forced-alignment:{alignment_request.content_hash}",
            "token_coverage": 1.0,
            "missing_tokens": [],
            "extra_tokens": [],
            "normalization_only_differences": [],
            "timing_conflicts": [],
            "alignment_confidence": 1.0,
            "reconciliation_reason_codes": ["FIXTURE_PROVIDER_AND_FORCED_ALIGNMENT_RECONCILED"],
            "verification_status": "PASS",
        }
        alignment = VerifiedNarrationAlignment(
            **alignment_payload,
            content_hash=stable_hash(alignment_payload),
        )
        alignment_dir = root / "alignment"
        _write_json(alignment_dir / "forced-alignment-request.json", alignment_request.model_dump(mode="json"))
        _write_json(
            alignment_dir / "fixture-forced-alignment-result.json",
            {
                "request_id": alignment_request.request_id,
                "provider": "LOCAL_DETERMINISTIC_ALIGNMENT_FIXTURE",
                "words": [item.model_dump(mode="json") for item in words],
                "network_call_made": False,
                "token_coverage": 1.0,
            },
        )
        _write_json(alignment_dir / "verified-narration-alignment.json", alignment.model_dump(mode="json"))
        transitions.append(LongProductionState.ALIGNMENT_READY.value)

        token_groups = self._three_token_groups(normalized.spoken_tokens)
        segments = []
        for index, tokens in enumerate(token_groups):
            source_start = min(span.start for token in tokens for span in token.source_spans)
            source_end = max(span.end for token in tokens for span in token.source_spans)
            segments.append(
                EditorialSegmentInput(
                    segment_id=f"scene-{index + 1}",
                    editorial_span=TextSpan(start=source_start, end=source_end),
                    spoken_token_ids=[token.token_id for token in tokens],
                    motion_intent=("NATIVE_DIAGRAM" if index == 0 else "STOCK_MOTION" if index == 1 else "GENERATED_STILL_NATIVE_OVERLAY"),
                    source_provenance=[{"type": "approved_script", "ref": authority.script_ref}],
                )
            )
        timeline = CanonicalMediaTimelineCompiler().compile(
            project_id=authority.project_id,
            package_id=authority.package_id,
            channel_id=authority.channel_id,
            script_revision_id=normalized.script_revision_id,
            spoken_text_revision_id=normalized.content_hash,
            tts_request_id=narration_request.request_id,
            normalized=normalized,
            alignment=alignment,
            segments=segments,
        )
        captioned = ReadableCaptionCompiler().compile(
            normalized=normalized,
            alignment=alignment,
            timeline=timeline,
            policy=self._caption_policy(),
            aspect_ratio="16:9",
        )
        timeline = captioned.timeline
        final_audio_payload = {
            "audio_asset_ref": str(audio_path),
            "duration_ms": duration_ms,
            "is_final": True,
        }
        final_audio = FinalNarrationAudio(
            **final_audio_payload,
            content_hash=stable_hash(final_audio_payload),
        )
        temporal_gate = TemporalAuthorityGate().evaluate(
            normalized=normalized,
            final_audio=final_audio,
            alignment=alignment,
            timeline=timeline,
        )
        if temporal_gate.gate_status != "PASS":
            raise RuntimeError("LPRO1_TEMPORAL_AUTHORITY_FAILED")
        timeline_dir = root / "timeline"
        _write_json(timeline_dir / "canonical-media-timeline.json", timeline.model_dump(mode="json"))
        _write_json(timeline_dir / "compiled-caption-track.json", captioned.track.model_dump(mode="json"))
        _write_json(timeline_dir / "temporal-authority-gate.json", temporal_gate.model_dump(mode="json"))
        transitions.extend([LongProductionState.CANONICAL_TIMELINE_READY.value, LongProductionState.AWAITING_ASSETS.value])

        decisions = self._visual_decisions(run_id)
        assets, normalization = self._materialize_and_normalize_assets(
            root=root,
            timeline=timeline,
            decisions=decisions,
        )
        transitions.extend([LongProductionState.ASSETS_READY.value, LongProductionState.NATIVE_RENDER_PLAN_READY.value])
        plan = LongFormRenderPackageToNativeRenderPlanAdapter().adapt(
            authority=authority,
            timeline=timeline,
            normalized_assets=assets,
            decisions=decisions,
            audio_path=audio_path,
            audio_hash=audio_hash,
        )
        plan_dir = root / "render-plan"
        _write_json(plan_dir / "native-render-plan.json", plan.model_dump(mode="json"))
        manifest = NativeMotionCompiler().compile(
            plan,
            allow_resolved_provider_assets=True,
            canonical_timeline=timeline,
        )
        _write_json(plan_dir / "compiled-native-render-manifest.json", manifest.model_dump(mode="json"))
        transitions.append(LongProductionState.RENDERING.value)
        command = FFmpegCommandBuilder(
            self.workspace_root,
            ffmpeg=str(self.ffmpeg),
            ffprobe=str(self.ffprobe),
        ).build_lpro1_fixture(
            manifest,
            run_key=run_id,
            audio_path=audio_path,
        )
        receipt, native_qc = NativeFFmpegRenderer(
            self.workspace_root,
            smoke_enabled=True,
            production_enabled=False,
        ).execute(manifest, command, purpose=FIXTURE_PURPOSE)
        transitions.append(LongProductionState.RENDERED_AWAITING_TECHNICAL_QC.value)
        measured = native_qc.checks
        technical_checks = {
            "decode": measured.get("full_decode") is True,
            "codec_container": measured.get("codec_container_matches_expected") is True,
            "stream_integrity": measured.get("stream_integrity") is True and measured.get("av_drift_within_limit") is True,
            "dimensions": measured.get("dimensions_match_expected") is True,
            "fps": measured.get("fps_matches_expected") is True,
            "audio_format": measured.get("audio_format_matches_expected") is True,
            "duration": measured.get("duration_matches_expected") is True,
            "fast_start": measured.get("fast_start") is True,
            "checksum": measured.get("checksum_sha256") == receipt.output_checksum,
            "black_output": measured.get("black_output_absent") is True,
            "caption_presence": measured.get("caption_likely_present") is True,
            "scene_coverage": measured.get("timeline_coverage") is True,
        }
        technical = TechnicalMediaQC().evaluate(
            run_id=run_id,
            checks=technical_checks,
            required_checks=technical_checks.keys(),
        )
        if technical.result != "PASS":
            raise RuntimeError("LPRO1_TECHNICAL_MEDIA_QC_FAILED")
        transitions.append(LongProductionState.TECHNICAL_QC_PASSED.value)
        creative_gates = []
        for name in CreativePerceptualMediaQC.required_gates:
            result = "REVIEW_REQUIRED" if name == "VisualContinuityGate" else "PASS"
            payload = {
                "gate_name": name,
                "result": result,
                "reason_codes": ["LPRO1_FIXTURE_HUMAN_WATCH_REQUIRED"] if result == "REVIEW_REQUIRED" else [],
                "metrics": {
                    "fixture_evidence": True,
                    "scene_semantic_match": "PASS",
                    "caption_readability": "PASS",
                    "voice_pacing_fixture": "PASS",
                    "overlay_readability": "PASS",
                    "stock_mechanism_appropriateness": "PASS",
                    "generated_image_native_overlay_appropriateness": "PASS",
                    "transition_quality": "REVIEW_REQUIRED" if result == "REVIEW_REQUIRED" else "PASS",
                    "overall_watchability": "HUMAN_REVIEW_REQUIRED",
                },
                "evidence_refs": [receipt.output_path],
            }
            creative_gates.append(
                CreativeGateEvidence(**payload, content_hash=stable_hash(payload))
            )
        creative = CreativePerceptualMediaQC().aggregate(
            run_id=run_id,
            gate_results=creative_gates,
        )
        if creative.result != "REVIEW_REQUIRED":
            raise RuntimeError("LPRO1_CREATIVE_REVIEW_BOUNDARY_INVALID")
        transitions.append(LongProductionState.CREATIVE_REVIEW_REQUIRED.value)
        qc_dir = root / "qc"
        _write_json(qc_dir / "technical-media-qc.json", technical.model_dump(mode="json"))
        _write_json(qc_dir / "creative-perceptual-media-qc.json", creative.model_dump(mode="json"))
        candidate_payload = {
            "candidate_id": f"review-media-candidate:{run_id}",
            "project_ref": authority.project_ref,
            "package_ref": authority.package_ref,
            "plan_ref": plan.plan_id,
            "output_file_ref": receipt.output_path,
            "output_sha256": receipt.output_checksum,
            "technical_media_qc_ref": str(qc_dir / "technical-media-qc.json"),
            "technical_media_qc_hash": technical.content_hash,
            "creative_media_qc_ref": str(qc_dir / "creative-perceptual-media-qc.json"),
            "creative_media_qc_hash": creative.content_hash,
            "production_eligible": False,
            "not_publishable": True,
            "human_review_status": "PENDING",
        }
        candidate = ReviewMediaCandidate(
            **candidate_payload,
            content_hash=stable_hash(candidate_payload),
        )
        _write_json(root / "review_media_candidate.json", candidate.model_dump(mode="json"))
        transitions.append(LongProductionState.READY_FOR_HUMAN_REVIEW.value)

        asset_usage_payload = {
            "assets": [item.model_dump(mode="json") for item in assets],
            "visual_decisions": [item.model_dump(mode="json") for item in decisions],
        }
        asset_usage_hash = stable_hash(asset_usage_payload)
        strict_payload = {
            "scripted_package_ref": authority.package_ref,
            "scripted_package_hash": authority.package_hash,
            "project_ref": authority.project_ref,
            "project_hash": authority.project_hash,
            "channel_profile_version_ref": authority.channel_profile_version_ref,
            "compiled_policy_snapshot_ref": authority.compiled_policy_snapshot_ref,
            "compiled_policy_snapshot_hash": authority.compiled_policy_snapshot_hash,
            "channel_contract_hash": authority.channel_contract_hash,
            "niche_contract_digest_ref": authority.niche_contract_digest_ref,
            "niche_contract_digest_hash": authority.niche_contract_digest_hash,
            "effective_context_ref": authority.effective_context_ref,
            "effective_context_hash": authority.effective_context_hash,
            "niche_alignment_dossier_ref": authority.niche_alignment_dossier_ref,
            "niche_alignment_dossier_hash": authority.niche_alignment_dossier_hash,
            "narration_request_ref": str(audio_dir / "narration-request.json"),
            "narration_result_ref": str(audio_dir / "narration-result.json"),
            "audio_asset_ref": str(audio_path),
            "audio_asset_hash": audio_hash,
            "verified_alignment_ref": str(alignment_dir / "verified-narration-alignment.json"),
            "verified_alignment_hash": alignment.content_hash,
            "verified_alignment_status": "PASS",
            "canonical_timeline_ref": str(timeline_dir / "canonical-media-timeline.json"),
            "canonical_timeline_hash": timeline.timeline_hash,
            "caption_track_ref": str(timeline_dir / "compiled-caption-track.json"),
            "caption_track_hash": captioned.track.content_hash,
            "visual_direction_contract_ref": authority.visual_direction_contract_ref,
            "visual_direction_contract_hash": authority.visual_direction_contract_hash,
            "visual_source_decisions": decisions,
            "resolved_assets": assets,
            "asset_usage_manifest_ref": str(root / "assets" / "asset-usage-manifest.json"),
            "asset_usage_manifest_hash": asset_usage_hash,
            "media_normalization_manifest_ref": str(root / "assets" / "media-normalization-manifest.json"),
            "media_normalization_manifest_hash": normalization.content_hash,
            "native_render_policy_snapshot_ref": authority.native_render_policy_snapshot_ref,
            "native_render_policy_snapshot_hash": authority.native_render_policy_snapshot_hash,
            "native_render_plan_ref": plan.plan_id,
            "native_render_plan_hash": plan.content_hash,
            "renderer_eligibility": "PASS",
            "provider_execution_plan_ref": authority.provider_execution_plan_ref,
            "provider_execution_plan_hash": authority.provider_execution_plan_hash,
            "cost_estimate_snapshot_ref": authority.cost_estimate_snapshot_ref,
            "cost_estimate_snapshot_hash": authority.cost_estimate_snapshot_hash,
            "approval_refs": list(authority.approval_refs),
            "idempotency_refs": [lineage_fingerprint, command.run_key, command.command_hash],
            "target_duration_seconds": duration_ms / 1000.0,
        }
        strict_contract = LongFormRenderPackageStrictContract(
            **strict_payload,
            content_hash=stable_hash(
                LongFormRenderPackageStrictContract(
                    **strict_payload,
                    content_hash="pending",
                ).model_dump(mode="json", exclude={"content_hash"})
            ),
        )
        package_blockers = strict_render_package_blockers(strict_contract, plan=plan)
        if package_blockers:
            raise RuntimeError("LPRO1_STRICT_RENDER_PACKAGE_FAILED:" + ",".join(package_blockers))
        _write_json(root / "strict-long-form-render-package.json", strict_contract.model_dump(mode="json"))
        _write_json(root / "assets" / "asset-usage-manifest.json", asset_usage_payload)

        final_fingerprint = stable_hash(
            {
                **lineage_seed,
                "timeline": timeline.timeline_hash,
                "assets": [item.checksum_sha256 for item in assets],
                "renderer_policy": authority.native_render_policy_snapshot_hash,
                "plan": plan.content_hash,
            }
        )
        receipt_payload = {
            "orchestrator_version": ORCHESTRATOR_VERSION,
            "run_id": run_id,
            "execution_mode": LongProductionExecutionMode.OFFLINE_FIXTURE,
            "current_state": LongProductionState.READY_FOR_HUMAN_REVIEW,
            "package_ref": authority.package_ref,
            "project_ref": authority.project_ref,
            "lineage_refs": lineage_seed,
            "narration_refs": {
                "request": str(audio_dir / "narration-request.json"),
                "result": str(audio_dir / "narration-result.json"),
                "audio": str(audio_path),
            },
            "alignment_refs": {
                "request": str(alignment_dir / "forced-alignment-request.json"),
                "verified": str(alignment_dir / "verified-narration-alignment.json"),
            },
            "canonical_timeline_ref": str(timeline_dir / "canonical-media-timeline.json"),
            "asset_resolution_refs": [item.local_file_ref for item in assets],
            "normalization_ref": str(root / "assets" / "media-normalization-manifest.json"),
            "native_render_plan_ref": str(plan_dir / "native-render-plan.json"),
            "native_motion_compiler_ref": str(plan_dir / "compiled-native-render-manifest.json"),
            "ffmpeg_receipt_ref": str(Path(command.working_directory) / "execution_receipt.json"),
            "technical_media_qc_ref": str(qc_dir / "technical-media-qc.json"),
            "creative_media_qc_ref": str(qc_dir / "creative-perceptual-media-qc.json"),
            "review_media_candidate_ref": str(root / "review_media_candidate.json"),
            "final_media_ref": None,
            "provider_calls": 0,
            "render_attempts": 1,
            "state_transitions": transitions,
            "idempotency_fingerprint": final_fingerprint,
            "blockers": [],
            "exact_next_action": "A human reviews the exact candidate MP4 hash; no FinalMediaRef or upload is authorized.",
        }
        orchestration = LongProductionOrchestrationReceipt(
            **receipt_payload,
            content_hash=stable_hash(receipt_payload),
        )
        _write_json(completed_path, orchestration.model_dump(mode="json"))
        return orchestration

    def _materialize_audio(self, path: Path, duration_ms: int) -> None:
        if path.is_file() and not path.is_symlink():
            probe = _probe(path, str(self.ffprobe))
            audio = next(item for item in probe["streams"] if item.get("codec_type") == "audio")
            if int(audio.get("sample_rate") or 0) == 48000 and int(audio.get("channels") or 0) == 2:
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part.wav")
        process = subprocess.run(
            [
                str(self.ffmpeg),
                "-hide_banner",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=330:sample_rate=48000:duration={duration_ms / 1000.0:.6f}",
                "-af",
                "volume=0.18,tremolo=f=3.5:d=0.55",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(part),
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError("LPRO1_FIXTURE_NARRATION_MATERIALIZATION_FAILED")
        os.replace(part, path)

    def _materialize_and_normalize_assets(
        self,
        *,
        root: Path,
        timeline: Any,
        decisions: list[VisualSourceBinding],
    ) -> tuple[list[ResolvedMediaAsset], MediaNormalizationManifest]:
        source_dir = root / "assets" / "source"
        normalized_dir = root / "assets" / "normalized"
        source_dir.mkdir(parents=True, exist_ok=True)
        normalized_dir.mkdir(parents=True, exist_ok=True)
        font = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
        if not font.is_file():
            font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        if not font.is_file():
            raise FileNotFoundError("LPRO1_NATIVE_FONT_NOT_FOUND")
        results: list[ResolvedMediaAsset] = []
        normalized_items: list[MediaNormalizationItem] = []
        for index, (segment, decision) in enumerate(zip(timeline.segments, decisions, strict=True)):
            duration = segment.target_scene_duration_ms / 1000.0
            if index == 0:
                source = source_dir / "native-diagram-source.mp4"
                source_command = [
                    str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x071827:s=1280x720:r=24:d={duration:.6f}", "-vf",
                    f"drawbox=x=90:y=210:w=260:h=150:color=0x2563eb:t=fill,drawbox=x=510:y=210:w=260:h=150:color=0x14b8a6:t=fill,drawbox=x=930:y=210:w=260:h=150:color=0xf59e0b:t=fill,drawtext=fontfile='{font}':text='SCRIPT':fontcolor=white:fontsize=38:x=145:y=260,drawtext=fontfile='{font}':text='TIMELINE':fontcolor=white:fontsize=34:x=550:y=260,drawtext=fontfile='{font}':text='REVIEW':fontcolor=white:fontsize=38:x=985:y=260,drawbox=x=350:y=275:w=160:h=12:color=white:t=fill,drawbox=x=770:y=275:w=160:h=12:color=white:t=fill",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
                ]
            elif index == 1:
                source = source_dir / "stock-like-source.mp4"
                source_command = [
                    str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=1280x720:rate=24:duration={duration:.6f}", "-vf",
                    "hue=s=0.75,eq=contrast=1.08:brightness=-0.04", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
                ]
            else:
                source = source_dir / "generated-like-source.png"
                source_command = [
                    str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                    "color=c=0x1d1538:s=1280x720", "-vf",
                    "drawbox=x=80:y=80:w=1120:h=560:color=0x312e81:t=fill,drawbox=x=180:y=180:w=360:h=360:color=0x7c3aed:t=fill,drawbox=x=740:y=150:w=300:h=420:color=0x0ea5e9:t=fill,drawbox=x=520:y=330:w=260:h=60:color=white:t=fill",
                    "-frames:v", "1", "-update", "1", str(source),
                ]
            if not source.is_file():
                process = subprocess.run(source_command, capture_output=True, text=True)
                if process.returncode != 0:
                    raise RuntimeError(f"LPRO1_SOURCE_ASSET_MATERIALIZATION_FAILED:{index + 1}")
            normalized_path = normalized_dir / f"scene-{index + 1}-normalized.mp4"
            if not normalized_path.is_file():
                input_args = ["-loop", "1"] if source.suffix == ".png" else []
                filtergraph = (
                    "scale=1920:1080:force_original_aspect_ratio=increase,"
                    "crop=1920:1080,setsar=1,fps=30,format=yuv420p"
                )
                process = subprocess.run(
                    [
                        str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", *input_args,
                        "-i", str(source), "-vf", filtergraph, "-an", "-t", f"{duration:.6f}",
                        "-r", "30", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                        str(normalized_path),
                    ],
                    capture_output=True,
                    text=True,
                )
                if process.returncode != 0:
                    raise RuntimeError(f"LPRO1_MEDIA_NORMALIZATION_FAILED:{index + 1}")
            source_probe = _probe(source, str(self.ffprobe))
            normalized_probe = _probe(normalized_path, str(self.ffprobe))
            video = next(item for item in normalized_probe["streams"] if item.get("codec_type") == "video")
            source_hash = _sha256_file(source)
            normalized_hash = _sha256_file(normalized_path)
            asset_id = f"lpro1-asset-scene-{index + 1}"
            results.append(
                ResolvedMediaAsset(
                    asset_id=asset_id,
                    scene_id=segment.segment_id,
                    source_decision_ref=decision.decision_ref,
                    source_decision_hash=decision.decision_hash,
                    actual_route=decision.preferred_route,
                    local_file_ref=str(normalized_path),
                    checksum_sha256=normalized_hash,
                    width=int(video["width"]),
                    height=int(video["height"]),
                    duration_ms=segment.target_scene_duration_ms,
                    rights_status="CONFIRMED" if index == 1 else "NOT_REQUIRED",
                    provenance_refs=[f"fixture-provenance://scene-{index + 1}", str(source)],
                    normalization_state="NORMALIZED",
                    scene_usage_ref=f"canonical-timeline:{timeline.timeline_hash}#{segment.segment_id}",
                )
            )
            normalized_items.append(
                MediaNormalizationItem(
                    asset_id=asset_id,
                    source_ref=str(source),
                    source_checksum=source_hash,
                    normalized_ref=str(normalized_path),
                    normalized_checksum=normalized_hash,
                    byte_probe={
                        "source": source_probe,
                        "normalized": normalized_probe,
                        "codec": video.get("codec_name"),
                        "pixel_format": video.get("pix_fmt"),
                        "width": video.get("width"),
                        "height": video.get("height"),
                        "fps": video.get("avg_frame_rate"),
                    },
                    state="PASS",
                )
            )
        manifest_payload = {
            "manifest_id": f"media-normalization:{timeline.timeline_hash}",
            "items": [item.model_dump(mode="json") for item in normalized_items],
            "target_video": {
                "codec": "h264",
                "container": "mp4",
                "fps": 30,
                "pixel_format": "yuv420p",
                "resolution": "1920x1080",
                "aspect_ratio": "16:9",
                "color": "bt709",
            },
            "target_audio": {"sample_rate": 48000, "channels": 2},
            "actual_byte_probe_required": True,
            "result": "PASS",
        }
        manifest = MediaNormalizationManifest(
            **manifest_payload,
            content_hash=stable_hash(manifest_payload),
        )
        _write_json(root / "assets" / "media-normalization-manifest.json", manifest.model_dump(mode="json"))
        return results, manifest

    def _authority_from_db(
        self,
        project_id: uuid.UUID,
        package_id: uuid.UUID | None,
    ) -> tuple[_PackageAuthority, uuid.UUID]:
        assert self.session is not None
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {project_id}")
        query = select(FirstScriptedVideoPackage).where(
            FirstScriptedVideoPackage.video_project_id == project.id
        )
        if package_id is not None:
            query = query.where(FirstScriptedVideoPackage.id == package_id)
        package = self.session.scalars(query.order_by(FirstScriptedVideoPackage.created_at.desc())).first()
        if package is None:
            raise ValidationFailureError("LPRO1_SCRIPTED_PACKAGE_NOT_FOUND")
        receipt_artifact = self.session.scalars(
            select(Artifact)
            .where(Artifact.video_project_id == project.id)
            .where(Artifact.artifact_type == "idea_admission_lineage")
        ).first()
        receipt_version = (
            self.session.get(ArtifactVersion, receipt_artifact.current_version_id)
            if receipt_artifact is not None and receipt_artifact.current_version_id
            else None
        )
        receipt = receipt_version.content if receipt_version is not None else {}
        if receipt.get("state") != "READY_FOR_LONG_PRODUCTION" or receipt.get("human_review_state") != "PASS":
            raise ValidationFailureError("LPRO1_PACKAGE_HUMAN_REVIEW_BOUNDARY_NOT_PASSED")
        package_ref = receipt.get("scripted_package_ref") or {}
        if package_ref.get("id") != str(package.id):
            raise ValidationFailureError("LPRO1_SCRIPTED_PACKAGE_RECEIPT_MISMATCH")
        if (
            package.channel_id != project.channel_workspace_id
            or package.channel_profile_version_id != project.channel_profile_version_id
            or package.compiled_policy_snapshot_id != project.policy_snapshot_id
            or package.effective_context_snapshot_id != project.effective_context_snapshot_id
            or package.effective_context_hash != (receipt.get("effective_context_ref") or {}).get("content_hash")
        ):
            raise ValidationFailureError("LPRO1_FROZEN_PACKAGE_LINEAGE_MISMATCH")
        dossier = (package.artifacts or {}).get("niche_alignment_dossier") or {}
        if str(dossier.get("overall_verdict")) != "PASS" or not dossier.get("content_hash"):
            raise ValidationFailureError("LPRO1_NICHE_ALIGNMENT_DOSSIER_PASS_REQUIRED")
        snapshot = self.session.get(CompiledChannelPolicySnapshot, project.policy_snapshot_id)
        if snapshot is None:
            raise ValidationFailureError("LPRO1_COMPILED_POLICY_SNAPSHOT_NOT_FOUND")
        source_text = self._extract_script_text((package.artifacts or {}).get("narration_script"))
        if not source_text:
            raise ValidationFailureError("LPRO1_STRICT_SCRIPT_ARTIFACT_MISSING")
        artifacts = package.artifacts or {}
        provider_plan = artifacts.get("provider_execution_plan")
        cost = artifacts.get("cost_estimate_snapshot")
        if not isinstance(provider_plan, dict) or not isinstance(cost, dict):
            raise ValidationFailureError("LPRO1_PROVIDER_COST_EVIDENCE_MISSING")
        digest = (receipt.get("niche_contract_digest_ref") or {})
        return (
            _PackageAuthority(
                project_id=str(project.id),
                package_id=str(package.id),
                company_id=str(project.company_id),
                channel_id=str(project.channel_workspace_id),
                project_ref=f"video-project://{project.id}",
                project_hash=stable_hash({"id": str(project.id), "profile": str(project.channel_profile_version_id), "policy": str(project.policy_snapshot_id), "effective": str(project.effective_context_snapshot_id)}),
                package_ref=f"first-scripted-video-package://{package.id}",
                package_hash=str(package_ref["content_hash"]),
                channel_profile_version_ref=f"channel-profile-version://{package.channel_profile_version_id}",
                compiled_policy_snapshot_ref=f"compiled-policy-snapshot://{snapshot.id}",
                compiled_policy_snapshot_hash=snapshot.content_hash,
                channel_contract_hash=project.channel_contract_content_hash or stable_hash({"snapshot": snapshot.content_hash, "channel": str(project.channel_workspace_id)}),
                niche_contract_digest_ref=str(digest.get("ref")),
                niche_contract_digest_hash=str(digest.get("content_hash")),
                effective_context_ref=f"effective-context://{package.effective_context_snapshot_id}",
                effective_context_hash=str(package.effective_context_hash),
                niche_alignment_dossier_ref=f"first-scripted-video-package://{package.id}#niche_alignment_dossier",
                niche_alignment_dossier_hash=str(dossier["content_hash"]),
                script_ref=f"first-scripted-video-package://{package.id}#narration_script",
                script_hash=stable_hash(artifacts["narration_script"]),
                source_text=source_text,
                visual_direction_contract_ref=f"first-scripted-video-package://{package.id}#visual_plan",
                visual_direction_contract_hash=stable_hash(artifacts.get("visual_plan") or {}),
                provider_execution_plan_ref=f"first-scripted-video-package://{package.id}#provider_execution_plan",
                provider_execution_plan_hash=stable_hash(provider_plan),
                cost_estimate_snapshot_ref=f"first-scripted-video-package://{package.id}#cost_estimate_snapshot",
                cost_estimate_snapshot_hash=stable_hash(cost),
                native_render_policy_snapshot_ref=project.native_render_policy_snapshot_ref or f"compiled-policy-snapshot://{snapshot.id}#native-render",
                native_render_policy_snapshot_hash=project.native_render_policy_snapshot_hash or stable_hash(snapshot.compiled_payload.get("native_render_policy") or snapshot.content_hash),
                approval_refs=(str((receipt.get("package_human_review_ref") or {}).get("approval_decision_id")),),
            ),
            project.created_by_user_id,
        )

    def _persist_receipt(
        self,
        *,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        receipt: LongProductionOrchestrationReceipt,
    ) -> ArtifactVersion:
        assert self.session is not None
        artifact = self.session.scalars(
            select(Artifact)
            .where(Artifact.video_project_id == project_id)
            .where(Artifact.artifact_type == ORCHESTRATION_ARTIFACT_TYPE)
        ).first()
        service = ArtifactService(self.session)
        if artifact is None:
            artifact = service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project_id,
                    artifact_type=ORCHESTRATION_ARTIFACT_TYPE,
                    status="in_review",
                    created_by_user_id=actor_id,
                ),
                correlation_id=f"lpro1-orchestration-{project_id}",
            )
        if artifact.current_version_id:
            current = self.session.get(ArtifactVersion, artifact.current_version_id)
            if current is not None and current.content == receipt.model_dump(mode="json"):
                return current
        return service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=artifact.current_version_id,
                content=receipt.model_dump(mode="json"),
                status="submitted",
                created_by_user_id=actor_id,
                source_manifest={"output_is_mp4": True, "final_media_ref_created": False},
                evidence_refs=[{"type": "review_media_candidate", "ref": receipt.review_media_candidate_ref}],
            ),
            correlation_id=f"lpro1-orchestration-version-{project_id}",
        )

    @staticmethod
    def status(session: Session, project_id: uuid.UUID) -> LongProductionStatusRead:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {project_id}")
        artifact = session.scalars(
            select(Artifact)
            .where(Artifact.video_project_id == project_id)
            .where(Artifact.artifact_type == ORCHESTRATION_ARTIFACT_TYPE)
        ).first()
        receipt = None
        if artifact is not None and artifact.current_version_id:
            version = session.get(ArtifactVersion, artifact.current_version_id)
            if version is not None:
                receipt = LongProductionOrchestrationReceipt.model_validate(version.content)
        state = receipt.current_state if receipt else LongProductionState.PACKAGE_ACCEPTED
        transitions = set(receipt.state_transitions if receipt else [])
        return LongProductionStatusRead(
            project_id=str(project_id),
            current_state=state,
            package_readiness="PASS" if receipt else "AWAITING_CONTROLLED_RUN",
            narration_status="READY" if LongProductionState.NARRATION_READY.value in transitions else "NOT_STARTED",
            alignment_status="PASS" if LongProductionState.ALIGNMENT_READY.value in transitions else "NOT_STARTED",
            timeline_status="READY" if LongProductionState.CANONICAL_TIMELINE_READY.value in transitions else "NOT_STARTED",
            asset_status="READY" if LongProductionState.ASSETS_READY.value in transitions else "NOT_STARTED",
            render_plan_status="READY" if LongProductionState.NATIVE_RENDER_PLAN_READY.value in transitions else "NOT_STARTED",
            render_status="MP4_CREATED" if receipt and receipt.ffmpeg_receipt_ref else "NOT_STARTED",
            technical_qc_status="PASS" if receipt and receipt.technical_media_qc_ref else "NOT_STARTED",
            creative_qc_status="REVIEW_REQUIRED" if receipt and receipt.creative_media_qc_ref else "NOT_STARTED",
            human_review_status="PENDING" if state == LongProductionState.READY_FOR_HUMAN_REVIEW else "NOT_READY",
            archive_status="NOT_STARTED",
            final_media_ref_status="NOT_CREATED" if not receipt or not receipt.final_media_ref else "REGISTERED",
            blockers=list(receipt.blockers if receipt else []),
            exact_next_action=(receipt.exact_next_action if receipt else "Run the controlled long-production trigger after D2P1 human review PASS."),
            receipt=receipt,
        )

    def _fixture_authority(self) -> _PackageAuthority:
        package_hash = stable_hash({"fixture": "approved-scripted-package", "script": FIXTURE_SCRIPT})
        return _PackageAuthority(
            project_id="lpro1-fixture-project",
            package_id="lpro1-approved-scripted-package",
            company_id="lpro1-fixture-company",
            channel_id="small-team-ai-fixture",
            project_ref="fixture://video-project/lpro1",
            project_hash=stable_hash({"fixture_project": "lpro1"}),
            package_ref="fixture://scripted-package/lpro1-approved",
            package_hash=package_hash,
            channel_profile_version_ref="fixture://channel-profile/v2",
            compiled_policy_snapshot_ref="fixture://compiled-policy/v2",
            compiled_policy_snapshot_hash=stable_hash({"policy": "v2", "frozen": True}),
            channel_contract_hash=stable_hash({"channel_contract": "v2"}),
            niche_contract_digest_ref="fixture://niche-contract-digest/small-team-ai",
            niche_contract_digest_hash=stable_hash({"niche": "small-team-ai"}),
            effective_context_ref="fixture://effective-context/lpro1",
            effective_context_hash=stable_hash({"effective_context": "PASS"}),
            niche_alignment_dossier_ref="fixture://niche-alignment-dossier/lpro1",
            niche_alignment_dossier_hash=stable_hash({"all_five_niche_gates": "PASS"}),
            script_ref="fixture://approved-script/lpro1",
            script_hash=stable_hash(FIXTURE_SCRIPT),
            source_text=FIXTURE_SCRIPT,
            visual_direction_contract_ref="fixture://visual-direction-contract/lpro1",
            visual_direction_contract_hash=stable_hash({"visual_direction": "approved"}),
            provider_execution_plan_ref="fixture://provider-execution-plan/lpro1",
            provider_execution_plan_hash=stable_hash({"providers": [], "fixture": True}),
            cost_estimate_snapshot_ref="fixture://cost-estimate/lpro1",
            cost_estimate_snapshot_hash=stable_hash({"currency": "USD", "estimated": 0}),
            native_render_policy_snapshot_ref="fixture://native-render-policy/lpro1",
            native_render_policy_snapshot_hash=stable_hash({"profile": "YT_LONG_1080P30"}),
            approval_refs=("fixture-approval://scripted-package-human-pass",),
        )

    @staticmethod
    def _extract_script_text(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("narration_text", "full_script", "script", "text", "content"):
                result = LongProductionOrchestrator._extract_script_text(value.get(key))
                if result:
                    return result
            sentences = value.get("sentences") or value.get("segments")
            if isinstance(sentences, list):
                parts = [LongProductionOrchestrator._extract_script_text(item) for item in sentences]
                joined = " ".join(item for item in parts if item)
                return joined or None
        return None

    @staticmethod
    def _three_token_groups(tokens: list[Any]) -> list[list[Any]]:
        if len(tokens) < 6:
            raise ValueError("LPRO1_FIXTURE_SCRIPT_TOO_SHORT")
        first = len(tokens) // 3
        second = (2 * len(tokens)) // 3
        return [tokens[:first], tokens[first:second], tokens[second:]]

    @staticmethod
    def _visual_decisions(run_id: str) -> list[VisualSourceBinding]:
        specs = (
            (VisualSourceRoute.NATIVE_DIAGRAM, SourceFallbackClass.NATIVE_ONLY),
            (VisualSourceRoute.PEXELS_VIDEO, SourceFallbackClass.PEXELS_ONLY),
            (VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY, SourceFallbackClass.AI_IMAGE_PRIMARY),
        )
        values = []
        for index, (route, fallback) in enumerate(specs, start=1):
            ref = f"fixture-visual-source-decision://{run_id}/scene-{index}"
            values.append(
                VisualSourceBinding(
                    scene_id=f"scene-{index}",
                    decision_ref=ref,
                    decision_hash=stable_hash({"ref": ref, "route": route.value, "fallback": fallback.value}),
                    preferred_route=route,
                    fallback_class=fallback,
                    routing_reason_codes=[f"LPRO1_FIXTURE_{route.value}"],
                    eligibility_gate_refs=[f"fixture-eligibility-gate://scene-{index}/PASS"],
                )
            )
        return values

    @staticmethod
    def _caption_policy() -> dict[str, Any]:
        return {
            "policy_ref": "fixture-policy://caption-style/lpro1-v1",
            "policy_version": "lpro1-caption-style-v1",
            "longform_16_9": {
                "font_scale_pass": [0.044, 0.050], "font_scale_review": [0.040, 0.054],
                "block_outside": [0.040, 0.054], "max_chars_per_line_pass": 42,
                "max_chars_per_line_review": 46, "max_chars_per_line_block": 46,
                "max_block_width_pass": 0.68, "max_block_width_review": 0.74,
                "max_block_width_block": 0.74, "bottom_safe_margin_pass": 0.08,
                "bottom_safe_margin_review_min": 0.05,
            },
            "shorts_9_16": {
                "font_scale_pass": [0.046, 0.054], "font_scale_review": [0.042, 0.058],
                "block_outside": [0.042, 0.058], "max_chars_per_line_pass": 32,
                "max_chars_per_line_review": 36, "max_chars_per_line_block": 36,
                "max_block_width_pass": 0.84, "max_block_width_review": 0.88,
                "max_block_width_block": 0.88, "bottom_safe_margin_pass": 0.12,
                "bottom_safe_margin_review_min": 0.08,
            },
            "global": {
                "max_lines_per_cue": 2,
                "cue_duration_seconds": {"pass": [1.0, 6.0], "review": [0.8, 7.0], "block_outside": [0.8, 7.0]},
                "reading_speed_cps": {"pass_average_max": 15, "review_average_max": 17.5, "block_average_above": 17.5, "pass_p95_max": 17, "review_p95_max": 20, "block_any_above": 20},
            },
            "font_family": "Arial",
            "outline_ratio": 0.055,
            "shadow_ratio": 0.025,
        }


def run_lpro1_fixture_rehearsal(workspace_root: Path) -> LongProductionOrchestrationReceipt:
    return LongProductionOrchestrator(None, workspace_root=workspace_root).run_fixture()
