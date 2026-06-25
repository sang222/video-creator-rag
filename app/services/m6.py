from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m6 import (
    AssetCandidateContract,
    AssetManifestContract,
    AssetRequirementContract,
    CaptionCueContract,
    CaptionTrackContract,
    ExportProfileContract,
    FileRefContract,
    LayerSpec,
    NarrationSegmentContract,
    ProductionArtifactRunCreate,
    RenderSceneSpec,
    RenderSpecContract,
    RenderVariantSpec,
    RightsEnvelopeContract,
    SceneManifestContract,
    SceneManifestSceneContract,
    SceneSourceDecisionContract,
    SceneSpecContract,
    ScriptDraftContract,
    ScriptSection,
    SourceManifestContract,
    VisualPlanContract,
    VoiceTimelineContract,
)
from app.contracts.ops import CostEventCreate
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AccessibilityQCReport as AccessibilityQCReportModel,
    Artifact,
    ArtifactVersion,
    AssetManifestSnapshot,
    CaptionTrackSnapshot,
    CostEvent,
    LLMRunSnapshot,
    MediaQCReport,
    MediaRenderJob,
    ProductionArtifactRun,
    ProjectAdmissionDecision,
    ProviderAttempt,
    RenderPackageSnapshot,
    RenderSpecSnapshot,
    SceneManifestSnapshot,
    SourceManifestSnapshot,
    User,
    VideoProject,
    VisualPlanSnapshot,
    VoiceTimelineSnapshot,
)
from app.providers.mock import MockLLMProvider
from app.services.audit import AuditService
from app.services.config_registry import content_hash
from app.services.domain_events import DomainEventBus
from app.services.ops import CostService, ProviderRegistryService
from app.services.workflow import ArtifactService


M5_REQUIRED_ARTIFACT_TYPES = ("creative_brief", "research_pack", "source_pack")
M6_ARTIFACT_TYPES = {
    "script",
    "narration_timeline",
    "caption_track",
    "visual_plan",
    "scene_manifest",
    "render_spec",
    "asset_manifest",
    "source_manifest",
    "render_package",
    "media_qc_report",
    "accessibility_qc_report",
}
SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_")


@dataclass(frozen=True)
class RenderResult:
    job: MediaRenderJob
    package: RenderPackageSnapshot | None


class ProductionArtifactRunService:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        data: ProductionArtifactRunCreate,
        correlation_id: str = "m6-production-run",
    ) -> ProductionArtifactRun:
        project = _require_project(self.session, data.video_project_id)
        _require_m5_project_inputs(self.session, project.id)
        if data.source_project_admission_decision_id is not None:
            admission = self.session.get(ProjectAdmissionDecision, data.source_project_admission_decision_id)
            if admission is None:
                raise NotFoundError(f"project admission decision not found: {data.source_project_admission_decision_id}")
            if admission.admitted_video_project_id != project.id:
                raise ValidationFailureError("admission decision does not reference this video project")
        if data.run_mode == "REAL_DISABLED":
            reason_codes = ["REAL_PROVIDER_BLOCKED"]
            status = "BLOCKED"
        else:
            reason_codes = []
            status = "PENDING"
        run = ProductionArtifactRun(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            policy_snapshot_id=project.policy_snapshot_id,
            source_project_admission_decision_id=data.source_project_admission_decision_id,
            run_mode=data.run_mode,
            status=status,
            reason_codes=reason_codes,
            metadata_=data.metadata,
        )
        self.session.add(run)
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="production_artifact_run.created",
            aggregate_type="production_artifact_run",
            aggregate_id=run.id,
            actor_id=None,
            target_type="production_artifact_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="MOCK_PROVIDER_ONLY" if data.run_mode != "REAL_DISABLED" else "REAL_PROVIDER_BLOCKED",
            payload={
                "video_project_id": str(project.id),
                "policy_snapshot_id": str(project.policy_snapshot_id),
                "run_mode": run.run_mode,
                "status": run.status,
            },
        )
        return run

    def get_run(self, run_id: uuid.UUID) -> ProductionArtifactRun | None:
        return self.session.get(ProductionArtifactRun, run_id)

    def require_run(self, run_id: uuid.UUID) -> ProductionArtifactRun:
        run = self.get_run(run_id)
        if run is None:
            raise NotFoundError(f"production artifact run not found: {run_id}")
        return run

    def execute_local_mock_flow(
        self,
        *,
        run_id: uuid.UUID,
        output_dir: Path | None = None,
        mock_mode: str = "success",
        correlation_id: str = "m6-production-execute",
    ) -> ProductionArtifactRun:
        run = self.require_run(run_id)
        if run.status == "BLOCKED" and run.run_mode == "REAL_DISABLED":
            return run
        run.status = "RUNNING"
        run.started_at = utc_now()
        run.reason_codes = ["MOCK_PROVIDER_ONLY"]
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="production_artifact_run.started",
            aggregate_type="production_artifact_run",
            aggregate_id=run.id,
            actor_id=None,
            target_type="production_artifact_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="MOCK_PROVIDER_ONLY",
            payload={"run_mode": run.run_mode, "video_project_id": str(run.video_project_id)},
        )
        try:
            script_version = ScriptNarrationService(self.session).create_script_artifact(
                run=run,
                mock_mode=mock_mode,
                correlation_id="m6-script-draft",
            )
            voice_snapshot = ScriptNarrationService(self.session).create_voice_timeline(
                run=run,
                script_version=script_version,
                correlation_id="m6-voice-timeline",
            )
            caption_snapshot = CaptionCompilerService(self.session).build_from_voice_timeline(
                run=run,
                voice_timeline_snapshot=voice_snapshot,
                correlation_id="m6-caption-track",
            )
            visual_snapshot = VisualPlanService(self.session).create_visual_plan(
                run=run,
                voice_timeline_snapshot=voice_snapshot,
                caption_track_snapshot=caption_snapshot,
                correlation_id="m6-visual-plan",
            )
            scene_snapshot = VisualPlanService(self.session).create_scene_manifest(
                run=run,
                visual_plan_snapshot=visual_snapshot,
                correlation_id="m6-scene-manifest",
            )
            asset_snapshot, source_snapshot = AssetPlanningService(self.session).create_asset_and_source_manifests(
                run=run,
                scene_manifest_snapshot=scene_snapshot,
                correlation_id="m6-asset-source-manifest",
            )
            render_spec_snapshot = RenderSpecCompilerService(self.session).compile_render_spec(
                run=run,
                voice_timeline_snapshot=voice_snapshot,
                caption_track_snapshot=caption_snapshot,
                visual_plan_snapshot=visual_snapshot,
                scene_manifest_snapshot=scene_snapshot,
                asset_manifest_snapshot=asset_snapshot,
                correlation_id="m6-render-spec",
            )
            accessibility_report = AccessibilityQCService(self.session).run_qc(
                caption_track_snapshot=caption_snapshot,
                render_package_snapshot=None,
                correlation_id="m6-accessibility-qc",
            )
            run.accessibility_qc_report_id = accessibility_report.id
            render_result = LocalFixtureRendererService(self.session).render_local_smoke(
                render_spec_snapshot_id=render_spec_snapshot.id,
                output_dir=output_dir,
                correlation_id="m6-local-video-smoke",
            )
            if render_result.package is None:
                media_report = MediaQCService(self.session).run_blocked_qc(
                    render_spec_snapshot=render_spec_snapshot,
                    render_job=render_result.job,
                    correlation_id="m6-media-qc",
                )
                run.media_qc_report_id = media_report.id
                run.status = "BLOCKED"
                run.completed_at = utc_now()
                run.reason_codes = _dedupe([*run.reason_codes, *render_result.job.reason_codes, *media_report.reason_codes])
                self.session.flush()
                _record_m6_event(
                    self.session,
                    event_type="production_artifact_run.failed",
                    aggregate_type="production_artifact_run",
                    aggregate_id=run.id,
                    actor_id=None,
                    target_type="production_artifact_run",
                    target_id=run.id,
                    company_id=run.company_id,
                    correlation_id=correlation_id,
                    reason_code=run.reason_codes[0],
                    payload={"status": run.status, "reason_codes": run.reason_codes},
                )
                return run
            media_report = MediaQCService(self.session).run_qc(
                render_package_snapshot=render_result.package,
                correlation_id="m6-media-qc",
            )
            accessibility_report = AccessibilityQCService(self.session).run_qc(
                caption_track_snapshot=caption_snapshot,
                render_package_snapshot=render_result.package,
                correlation_id="m6-accessibility-qc",
            )
            run.render_package_snapshot_id = render_result.package.id
            run.media_qc_report_id = media_report.id
            run.accessibility_qc_report_id = accessibility_report.id
            run.status = "COMPLETED" if media_report.qc_state == "PASS" and accessibility_report.qc_state == "PASS" else "REVIEW_REQUIRED"
            run.completed_at = utc_now()
            run.reason_codes = _dedupe([*run.reason_codes, *media_report.reason_codes, *accessibility_report.reason_codes])
            self.session.flush()
            _record_m6_event(
                self.session,
                event_type="production_artifact_run.completed",
                aggregate_type="production_artifact_run",
                aggregate_id=run.id,
                actor_id=None,
                target_type="production_artifact_run",
                target_id=run.id,
                company_id=run.company_id,
                correlation_id=correlation_id,
                reason_code=run.reason_codes[0] if run.reason_codes else "LOCAL_RENDER_COMPLETED",
                payload={"status": run.status, "reason_codes": run.reason_codes},
            )
            return run
        except (ValidationError, ValidationFailureError) as exc:
            run.status = "FAILED"
            run.completed_at = utc_now()
            run.reason_codes = _dedupe([*run.reason_codes, "BAD_ARTIFACT_REJECTED"])
            self.session.flush()
            _record_m6_event(
                self.session,
                event_type="production_artifact_run.failed",
                aggregate_type="production_artifact_run",
                aggregate_id=run.id,
                actor_id=None,
                target_type="production_artifact_run",
                target_id=run.id,
                company_id=run.company_id,
                correlation_id=correlation_id,
                reason_code="BAD_ARTIFACT_REJECTED",
                payload={"status": run.status, "error": str(exc), "reason_codes": run.reason_codes},
            )
            raise


class ScriptNarrationService:
    def __init__(self, session: Session):
        self.session = session

    def create_script_artifact(
        self,
        *,
        run: ProductionArtifactRun,
        mock_mode: str = "success",
        correlation_id: str = "m6-script-draft",
    ) -> ArtifactVersion:
        project = _require_project(self.session, run.video_project_id)
        source_versions = _require_m5_project_inputs(self.session, project.id)
        response = MockLLMProvider().generate(prompt=f"m6_script:{project.id}", mode=mock_mode)
        cost_event = _record_mock_cost(self.session, project, provider_run_ref=f"production_artifact_run:{run.id}")
        if not response.ok:
            attempt = _record_provider_attempt(
                self.session,
                provider_key="mock_llm",
                operation_key="m6_script_draft",
                target_type="production_artifact_run",
                target_id=run.id,
                status=_provider_attempt_status(response),
                error_code=response.error_code,
                latency_ms=response.latency_ms,
                cost_event_id=cost_event.id,
                metadata={"mock": True, "mode": mock_mode},
                correlation_id="m6-provider-attempt",
                company_id=run.company_id,
            )
            _create_llm_run(
                self.session,
                run=run,
                status="FAILED",
                input_payload={"purpose": "SCRIPT_DRAFT", "video_project_id": str(project.id)},
                output_payload={"error_code": response.error_code, "provider_attempt_id": str(attempt.id)},
                cost_event_id=cost_event.id,
                correlation_id=correlation_id,
            )
            raise ValidationFailureError("mock LLM script draft failed")
        script_payload = self._build_script(project, source_versions)
        script_payload["script_hash"] = _hash_payload({**script_payload, "script_hash": None})
        script = ScriptDraftContract.model_validate(script_payload)
        llm_run = _create_llm_run(
            self.session,
            run=run,
            status="COMPLETED",
            input_payload={
                "purpose": "SCRIPT_DRAFT",
                "video_project_id": str(project.id),
                "source_artifact_version_ids": [str(item.id) for item in source_versions],
            },
            output_payload=script.model_dump(mode="json"),
            cost_event_id=cost_event.id,
            correlation_id=correlation_id,
        )
        _record_provider_attempt(
            self.session,
            provider_key="mock_llm",
            operation_key="m6_script_draft",
            target_type="production_artifact_run",
            target_id=run.id,
            status="SUCCESS",
            error_code=None,
            latency_ms=response.latency_ms,
            cost_event_id=cost_event.id,
            metadata={"mock": True, "mode": mock_mode, "llm_run_snapshot_id": str(llm_run.id)},
            correlation_id="m6-provider-attempt",
            company_id=run.company_id,
        )
        content = script.model_copy(update={"llm_run_snapshot_id": llm_run.id}).model_dump(mode="json")
        version = _create_artifact_version(
            self.session,
            project=project,
            artifact_type="script",
            content=content,
            created_by_user_id=project.created_by_user_id,
            evidence_refs=_collect_evidence_refs(source_versions),
            context_refs=[{"type": "production_artifact_run", "id": str(run.id)}],
            correlation_id="m6-script-artifact",
        )
        run.script_artifact_version_id = version.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="script_artifact.created",
            aggregate_type="artifact_version",
            aggregate_id=version.id,
            actor_id=project.created_by_user_id,
            target_type="artifact_version",
            target_id=version.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="VOICE_TIMELINE_CREATED",
            payload={"artifact_type": "script", "content_hash": version.content_hash},
        )
        return version

    def create_voice_timeline(
        self,
        *,
        run: ProductionArtifactRun,
        script_version: ArtifactVersion,
        correlation_id: str = "m6-voice-timeline",
    ) -> VoiceTimelineSnapshot:
        project = _require_project(self.session, run.video_project_id)
        script = ScriptDraftContract.model_validate(script_version.content)
        segments = [
            NarrationSegmentContract.model_validate({**segment.model_dump(mode="json"), "source_artifact_version_id": script_version.id})
            for segment in script.narration_segments
        ]
        payload = {
            "segments": [segment.model_dump(mode="json") for segment in segments],
            "total_duration_seconds": segments[-1].estimated_end_time,
            "timing_source": "ESTIMATED",
            "confidence_level": "MEDIUM",
        }
        payload["timeline_hash"] = _hash_payload({**payload, "timeline_hash": None})
        timeline = VoiceTimelineContract.model_validate(payload)
        snapshot = VoiceTimelineSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            script_artifact_version_id=script_version.id,
            policy_snapshot_id=run.policy_snapshot_id,
            timeline_blob=timeline.model_dump(mode="json"),
            total_duration_seconds=Decimal(str(timeline.total_duration_seconds)),
            timing_source=timeline.timing_source,
            confidence_level=timeline.confidence_level,
            timeline_hash=timeline.timeline_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        version = _create_artifact_version(
            self.session,
            project=project,
            artifact_type="narration_timeline",
            content=timeline.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[{"type": "production_artifact_run", "id": str(run.id)}],
            correlation_id="m6-voice-timeline-artifact",
        )
        run.voice_timeline_snapshot_id = snapshot.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="voice_timeline_snapshot.created",
            aggregate_type="voice_timeline_snapshot",
            aggregate_id=snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="voice_timeline_snapshot",
            target_id=snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="VOICE_TIMELINE_CREATED",
            payload={
                "timeline_hash": snapshot.timeline_hash,
                "total_duration_seconds": str(snapshot.total_duration_seconds),
                "artifact_version_id": str(version.id),
            },
        )
        return snapshot

    def _build_script(self, project: VideoProject, source_versions: list[ArtifactVersion]) -> dict[str, Any]:
        content_by_type = _content_by_artifact_type(self.session, source_versions)
        creative = content_by_type.get("creative_brief", {})
        research = content_by_type.get("research_pack", {})
        source = content_by_type.get("source_pack", {})
        title = str(creative.get("title") or project.title)
        angle = str(creative.get("angle") or project.description or "Explain the workflow clearly.")
        evidence_refs = list(research.get("evidence_refs") or source.get("source_refs") or [])
        section_texts = [
            ("hook", title),
            ("context", angle),
            ("mechanism", "Show how the admitted idea becomes script, timeline, scenes, render spec, and QC artifacts."),
            ("close", "End with a clear draft-ready package for human review."),
        ]
        sections = [
            ScriptSection(
                section_id=f"section_{index + 1:02d}",
                heading=heading,
                text=text,
                sequence_index=index,
            )
            for index, (heading, text) in enumerate(section_texts)
        ]
        segments: list[dict[str, Any]] = []
        cursor = 0.0
        for index, section in enumerate(sections):
            duration = max(2.0, round(len(section.text.split()) / 2.4, 3))
            end = round(cursor + duration, 3)
            segments.append(
                {
                    "narration_segment_id": f"nar_{index + 1:03d}",
                    "text": section.text,
                    "sequence_index": index,
                    "estimated_start_time": cursor,
                    "estimated_end_time": end,
                    "estimated_duration_seconds": round(end - cursor, 3),
                    "source_artifact_version_id": source_versions[0].id if source_versions else None,
                    "pronunciation_hints": {},
                    "evidence_refs": evidence_refs,
                    "safety_notes": ["mock draft; requires human review before production use"],
                }
            )
            cursor = end
        return {
            "script_id": f"script_{project.id}",
            "video_project_id": project.id,
            "title": title,
            "sections": [section.model_dump(mode="json") for section in sections],
            "narration_segments": segments,
            "source_artifact_version_ids": [version.id for version in source_versions],
            "llm_run_snapshot_id": None,
        }


class CaptionCompilerService:
    def __init__(self, session: Session):
        self.session = session

    def build_from_voice_timeline(
        self,
        *,
        run: ProductionArtifactRun,
        voice_timeline_snapshot: VoiceTimelineSnapshot,
        language: str = "en",
        correlation_id: str = "m6-caption-track",
    ) -> CaptionTrackSnapshot:
        project = _require_project(self.session, run.video_project_id)
        timeline = VoiceTimelineContract.model_validate(voice_timeline_snapshot.timeline_blob)
        cues = [
            CaptionCueContract(
                caption_id=f"cap_{index + 1:03d}",
                narration_segment_id=segment.narration_segment_id,
                start_time=segment.estimated_start_time,
                end_time=segment.estimated_end_time,
                text=segment.text,
                line_count=min(2, max(1, (len(segment.text) // 42) + 1)),
                char_count=len(segment.text),
            )
            for index, segment in enumerate(timeline.segments)
        ]
        srt_text = export_srt(cues)
        payload = {
            "cues": [cue.model_dump(mode="json") for cue in cues],
            "format": "SRT",
            "language": language,
            "srt_text": srt_text,
        }
        payload["caption_hash"] = _hash_payload({**payload, "caption_hash": None})
        caption_track = CaptionTrackContract.model_validate(payload)
        self.validate_srt(caption_track.srt_text or "")
        snapshot = CaptionTrackSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            voice_timeline_snapshot_id=voice_timeline_snapshot.id,
            caption_blob=caption_track.model_dump(mode="json"),
            srt_text=caption_track.srt_text,
            language=caption_track.language,
            caption_hash=caption_track.caption_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        version = _create_artifact_version(
            self.session,
            project=project,
            artifact_type="caption_track",
            content=caption_track.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[{"type": "voice_timeline_snapshot", "id": str(voice_timeline_snapshot.id)}],
            correlation_id="m6-caption-track-artifact",
        )
        run.caption_track_snapshot_id = snapshot.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="caption_track_snapshot.created",
            aggregate_type="caption_track_snapshot",
            aggregate_id=snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="caption_track_snapshot",
            target_id=snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="CAPTION_TRACK_CREATED",
            payload={"caption_hash": snapshot.caption_hash, "artifact_version_id": str(version.id)},
        )
        return snapshot

    def validate_srt(self, srt_text: str) -> None:
        if not srt_text.strip():
            raise ValidationFailureError("SRT text is empty")
        blocks = [block for block in srt_text.strip().split("\n\n") if block.strip()]
        for index, block in enumerate(blocks, start=1):
            lines = block.splitlines()
            if len(lines) < 3:
                raise ValidationFailureError("invalid SRT block")
            if lines[0] != str(index):
                raise ValidationFailureError("SRT indices must be deterministic and sequential")
            if " --> " not in lines[1]:
                raise ValidationFailureError("SRT timing line is invalid")


class VisualPlanService:
    def __init__(self, session: Session):
        self.session = session

    def create_visual_plan(
        self,
        *,
        run: ProductionArtifactRun,
        voice_timeline_snapshot: VoiceTimelineSnapshot,
        caption_track_snapshot: CaptionTrackSnapshot,
        correlation_id: str = "m6-visual-plan",
    ) -> VisualPlanSnapshot:
        project = _require_project(self.session, run.video_project_id)
        timeline = VoiceTimelineContract.model_validate(voice_timeline_snapshot.timeline_blob)
        captions = CaptionTrackContract.model_validate(caption_track_snapshot.caption_blob)
        captions_by_narration: dict[str, list[str]] = {}
        for cue in captions.cues:
            captions_by_narration.setdefault(cue.narration_segment_id, []).append(cue.caption_id)
        scenes = [
            SceneSpecContract(
                scene_id=f"scene_{index + 1:03d}",
                sequence_index=index,
                start_time=segment.estimated_start_time,
                end_time=segment.estimated_end_time,
                narration_segment_id=segment.narration_segment_id,
                caption_ids=captions_by_narration.get(segment.narration_segment_id, []),
                narration_summary=_summary(segment.text),
                visual_intent=_visual_intent(segment.text),
                preferred_source=_preferred_source_for_text(segment.text),
                asset_requirements=[{"media_type": "GENERATED_PLACEHOLDER", "purpose": "local smoke"}],
                overlay_text=_overlay_text(segment.text),
                fallback_visual="local color fixture",
                evidence_refs=segment.evidence_refs,
                risk_notes=[],
            )
            for index, segment in enumerate(timeline.segments)
        ]
        payload = {
            "scenes": [scene.model_dump(mode="json") for scene in scenes],
            "total_duration_seconds": timeline.total_duration_seconds,
            "source_voice_timeline_snapshot_id": voice_timeline_snapshot.id,
        }
        payload["visual_plan_hash"] = _hash_payload({**payload, "visual_plan_hash": None})
        visual_plan = VisualPlanContract.model_validate(payload)
        snapshot = VisualPlanSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            voice_timeline_snapshot_id=voice_timeline_snapshot.id,
            caption_track_snapshot_id=caption_track_snapshot.id,
            visual_plan_blob=visual_plan.model_dump(mode="json"),
            visual_plan_hash=visual_plan.visual_plan_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        version = _create_artifact_version(
            self.session,
            project=project,
            artifact_type="visual_plan",
            content=visual_plan.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[
                {"type": "voice_timeline_snapshot", "id": str(voice_timeline_snapshot.id)},
                {"type": "caption_track_snapshot", "id": str(caption_track_snapshot.id)},
            ],
            correlation_id="m6-visual-plan-artifact",
        )
        run.visual_plan_snapshot_id = snapshot.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="visual_plan_snapshot.created",
            aggregate_type="visual_plan_snapshot",
            aggregate_id=snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="visual_plan_snapshot",
            target_id=snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="VISUAL_PLAN_CREATED",
            payload={"visual_plan_hash": snapshot.visual_plan_hash, "artifact_version_id": str(version.id)},
        )
        return snapshot

    def create_scene_manifest(
        self,
        *,
        run: ProductionArtifactRun,
        visual_plan_snapshot: VisualPlanSnapshot,
        correlation_id: str = "m6-scene-manifest",
    ) -> SceneManifestSnapshot:
        project = _require_project(self.session, run.video_project_id)
        visual_plan = VisualPlanContract.model_validate(visual_plan_snapshot.visual_plan_blob)
        manifest_scenes: list[SceneManifestSceneContract] = []
        for scene in visual_plan.scenes:
            scene_type = _scene_type_for_intent(scene.visual_intent)
            factual_risk = "HIGH" if scene.preferred_source == "SCREENSHOT_PLACEHOLDER" else "LOW"
            decision = SceneSourceDecisionService().decide(
                scene_type=scene_type,
                importance="MEDIUM",
                specificity="MEDIUM",
                factual_risk=factual_risk,
                need_realism="LOW",
                approved_asset_pool_match=False,
            )
            decision = decision.model_copy(update={"scene_id": scene.scene_id})
            manifest_scenes.append(
                SceneManifestSceneContract.model_validate(
                    {
                        **scene.model_dump(mode="json"),
                        "scene_type": scene_type,
                        "importance": "MEDIUM",
                        "specificity": "MEDIUM",
                        "factual_risk": factual_risk,
                        "need_realism": "LOW",
                        "expected_viewer_impact": "MEDIUM",
                        "deadline_sensitivity": "MEDIUM",
                        "max_cost_usd": decision.max_cost_usd,
                        "preferred_source_order": [decision.preferred_source, *decision.fallback_order],
                        "fallback_order": decision.fallback_order,
                        "requires_ai_disclosure_check": decision.requires_ai_disclosure_check,
                        "requires_rights_review": decision.rights_review_required,
                        "procurement_required": decision.procurement_required,
                        "procurement_priority": "MEDIUM" if decision.procurement_required else None,
                        "source_decision": decision.model_dump(mode="json"),
                    }
                )
            )
        payload = {
            "scenes": [scene.model_dump(mode="json") for scene in manifest_scenes],
            "visual_plan_snapshot_id": visual_plan_snapshot.id,
        }
        payload["scene_manifest_hash"] = _hash_payload({**payload, "scene_manifest_hash": None})
        manifest = SceneManifestContract.model_validate(payload)
        snapshot = SceneManifestSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            visual_plan_snapshot_id=visual_plan_snapshot.id,
            scene_manifest_blob=manifest.model_dump(mode="json"),
            scene_manifest_hash=manifest.scene_manifest_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        version = _create_artifact_version(
            self.session,
            project=project,
            artifact_type="scene_manifest",
            content=manifest.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[{"type": "visual_plan_snapshot", "id": str(visual_plan_snapshot.id)}],
            correlation_id="m6-scene-manifest-artifact",
        )
        run.scene_manifest_snapshot_id = snapshot.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="scene_manifest_snapshot.created",
            aggregate_type="scene_manifest_snapshot",
            aggregate_id=snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="scene_manifest_snapshot",
            target_id=snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="SCENE_MANIFEST_CREATED",
            payload={"scene_manifest_hash": snapshot.scene_manifest_hash, "artifact_version_id": str(version.id)},
        )
        return snapshot


class SceneSourceDecisionService:
    def decide(
        self,
        *,
        scene_type: str,
        importance: str,
        specificity: str,
        factual_risk: str,
        need_realism: str,
        approved_asset_pool_match: bool,
    ) -> SceneSourceDecisionContract:
        if factual_risk == "HIGH":
            preferred = "SCREENSHOT_PLACEHOLDER"
            fallback = ["MANUAL_PREMIUM_PLACEHOLDER", "LOCAL_FIXTURE"]
            source_class = "MANUAL_ASSET_LIBRARY"
            procurement = True
            rights_review = True
            ai_check = False
            reasons = ["SCENE_SOURCE_DECISION_CREATED", "LICENSE_EVIDENCE_REQUIRED"]
        elif scene_type in {"MECHANISM", "PROCESS", "DATA"}:
            preferred = "DIAGRAM_PLACEHOLDER"
            fallback = ["LOCAL_FIXTURE", "MOCK_MEDIA"]
            source_class = "LOCAL_RENDERER"
            procurement = False
            rights_review = False
            ai_check = False
            reasons = ["SCENE_SOURCE_DECISION_CREATED", "LOCAL_FIXTURE_ASSET_USED"]
        elif importance in {"HIGH", "CRITICAL"} and specificity == "HIGH" and need_realism == "HIGH":
            preferred = "AI_PLACEHOLDER"
            fallback = ["MANUAL_PREMIUM_PLACEHOLDER", "LOCAL_FIXTURE"]
            source_class = "API_NATIVE_PROVIDER"
            procurement = True
            rights_review = True
            ai_check = True
            reasons = ["SCENE_SOURCE_DECISION_CREATED", "BATCH_PROCUREMENT_REQUIRED"]
        elif approved_asset_pool_match:
            preferred = "APPROVED_ASSET_POOL"
            fallback = ["LOCAL_FIXTURE"]
            source_class = "APPROVED_ASSET_POOL"
            procurement = False
            rights_review = False
            ai_check = False
            reasons = ["SCENE_SOURCE_DECISION_CREATED", "APPROVED_ASSET_POOL_LOOKUP_PLACEHOLDER"]
        else:
            preferred = "LOCAL_FIXTURE"
            fallback = ["MOCK_MEDIA"]
            source_class = "LOCAL_RENDERER"
            procurement = False
            rights_review = False
            ai_check = False
            reasons = ["SCENE_SOURCE_DECISION_CREATED", "LOCAL_FIXTURE_ASSET_USED", "INTERNAL_TEST_ONLY_ASSET"]
        return SceneSourceDecisionContract(
            scene_id="deterministic_scene_source_decision",
            source_class=source_class,
            preferred_source=preferred,
            fallback_order=fallback,
            procurement_required=procurement,
            rights_review_required=rights_review,
            requires_ai_disclosure_check=ai_check,
            max_cost_usd=5.0 if procurement else 0.0,
            reason_codes=reasons,
        )


class AssetPlanningService:
    def __init__(self, session: Session):
        self.session = session

    def create_asset_and_source_manifests(
        self,
        *,
        run: ProductionArtifactRun,
        scene_manifest_snapshot: SceneManifestSnapshot,
        correlation_id: str = "m6-asset-source-manifest",
    ) -> tuple[AssetManifestSnapshot, SourceManifestSnapshot]:
        project = _require_project(self.session, run.video_project_id)
        scene_manifest = SceneManifestContract.model_validate(scene_manifest_snapshot.scene_manifest_blob)
        requirements: list[AssetRequirementContract] = []
        candidates: list[AssetCandidateContract] = []
        source_refs: list[dict[str, Any]] = []
        procurement_refs: list[dict[str, Any]] = []
        for scene in scene_manifest.scenes:
            requirement_id = f"req_{scene.scene_id}"
            source_class = scene.source_decision.source_class
            source_type = "LOCAL_FIXTURE"
            license_requirement = "INTERNAL_TEST_ONLY"
            status = "SATISFIED"
            if scene.preferred_source == "MANUAL_ENVATO_PLACEHOLDER":
                source_class = "MANUAL_ASSET_LIBRARY"
                source_type = "MANUAL_ENVATO_PLACEHOLDER"
                license_requirement = "LICENSE_REQUIRED"
                status = "WAITING_FOR_ASSET"
                procurement_refs.append({"scene_id": scene.scene_id, "source": "MANUAL_ENVATO_PLACEHOLDER"})
            requirement = AssetRequirementContract(
                requirement_id=requirement_id,
                scene_id=scene.scene_id,
                required_media_type="GENERATED_PLACEHOLDER",
                source_class=source_class,
                search_keywords=[scene.visual_intent],
                visual_description=scene.visual_intent,
                license_requirement=license_requirement,
                required_evidence=[] if license_requirement == "INTERNAL_TEST_ONLY" else ["manual_license_evidence"],
                fallback_allowed=True,
                procurement_required=scene.procurement_required,
                requirement_status=status,
            )
            candidate = AssetCandidateContract(
                asset_ref=f"{source_type.lower()}://{scene.scene_id}",
                requirement_id=requirement_id,
                source_type=source_type,
                rights_envelope=RightsEnvelopeContract(
                    license_state="INTERNAL_TEST_ONLY" if source_type == "LOCAL_FIXTURE" else "LICENSE_REQUIRED",
                    commercial_use_allowed=False if source_type == "LOCAL_FIXTURE" else None,
                    attribution_required=False if source_type == "LOCAL_FIXTURE" else None,
                    evidence_refs=[],
                    restrictions=["not for publish"] if source_type == "LOCAL_FIXTURE" else ["manual procurement required"],
                ),
                provenance_blob={
                    "created_by": "M6 AssetPlanningService",
                    "scene_id": scene.scene_id,
                    "preferred_source": scene.preferred_source,
                    "source_class": source_class,
                },
            )
            requirements.append(requirement)
            candidates.append(candidate)
            source_refs.append(
                {
                    "scene_id": scene.scene_id,
                    "source_class": source_class,
                    "preferred_source": scene.preferred_source,
                    "asset_ref": candidate.asset_ref,
                }
            )
        asset_payload = {
            "requirements": [item.model_dump(mode="json") for item in requirements],
            "candidates": [item.model_dump(mode="json") for item in candidates],
        }
        asset_payload["manifest_hash"] = _hash_payload({**asset_payload, "manifest_hash": None})
        asset_manifest = AssetManifestContract.model_validate(asset_payload)
        asset_snapshot = AssetManifestSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            scene_manifest_snapshot_id=scene_manifest_snapshot.id,
            asset_manifest_blob=asset_manifest.model_dump(mode="json"),
            asset_manifest_hash=asset_manifest.manifest_hash,
        )
        self.session.add(asset_snapshot)
        self.session.flush()
        source_payload = {
            "source_refs": source_refs,
            "asset_refs": [candidate.asset_ref for candidate in candidates],
            "generated_by": "M6 AssetPlanningService",
            "provider_classification_summary": _classification_summary(source_refs),
            "procurement_queue_refs": procurement_refs,
            "created_at": utc_now().isoformat(),
        }
        source_payload["manifest_hash"] = _hash_payload({**source_payload, "manifest_hash": None})
        source_manifest = SourceManifestContract.model_validate(source_payload)
        source_snapshot = SourceManifestSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            asset_manifest_snapshot_id=asset_snapshot.id,
            source_manifest_blob=source_manifest.model_dump(mode="json"),
            source_manifest_hash=source_manifest.manifest_hash,
        )
        self.session.add(source_snapshot)
        self.session.flush()
        _create_artifact_version(
            self.session,
            project=project,
            artifact_type="asset_manifest",
            content=asset_manifest.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[{"type": "scene_manifest_snapshot", "id": str(scene_manifest_snapshot.id)}],
            source_manifest=source_manifest.model_dump(mode="json"),
            correlation_id="m6-asset-manifest-artifact",
        )
        _create_artifact_version(
            self.session,
            project=project,
            artifact_type="source_manifest",
            content=source_manifest.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[{"type": "asset_manifest_snapshot", "id": str(asset_snapshot.id)}],
            source_manifest=source_manifest.model_dump(mode="json"),
            correlation_id="m6-source-manifest-artifact",
        )
        run.asset_manifest_snapshot_id = asset_snapshot.id
        run.source_manifest_snapshot_id = source_snapshot.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="asset_manifest_snapshot.created",
            aggregate_type="asset_manifest_snapshot",
            aggregate_id=asset_snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="asset_manifest_snapshot",
            target_id=asset_snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="ASSET_REQUIREMENT_CREATED",
            payload={"asset_manifest_hash": asset_snapshot.asset_manifest_hash},
        )
        _record_m6_event(
            self.session,
            event_type="source_manifest_snapshot.created",
            aggregate_type="source_manifest_snapshot",
            aggregate_id=source_snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="source_manifest_snapshot",
            target_id=source_snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="SOURCE_MANIFEST_CREATED",
            payload={"source_manifest_hash": source_snapshot.source_manifest_hash},
        )
        return asset_snapshot, source_snapshot


class RenderSpecCompilerService:
    def __init__(self, session: Session):
        self.session = session

    def compile_render_spec(
        self,
        *,
        run: ProductionArtifactRun,
        voice_timeline_snapshot: VoiceTimelineSnapshot,
        caption_track_snapshot: CaptionTrackSnapshot,
        visual_plan_snapshot: VisualPlanSnapshot,
        scene_manifest_snapshot: SceneManifestSnapshot,
        asset_manifest_snapshot: AssetManifestSnapshot,
        correlation_id: str = "m6-render-spec",
    ) -> RenderSpecSnapshot:
        project = _require_project(self.session, run.video_project_id)
        voice_timeline = VoiceTimelineContract.model_validate(voice_timeline_snapshot.timeline_blob)
        visual_plan = VisualPlanContract.model_validate(visual_plan_snapshot.visual_plan_blob)
        asset_manifest = AssetManifestContract.model_validate(asset_manifest_snapshot.asset_manifest_blob)
        candidates_by_scene = {
            req.scene_id: candidate.asset_ref
            for req in asset_manifest.requirements
            for candidate in asset_manifest.candidates
            if candidate.requirement_id == req.requirement_id
        }
        scenes = [
            RenderSceneSpec(
                scene_id=scene.scene_id,
                start_time=scene.start_time,
                end_time=scene.end_time,
                narration_segment_id=scene.narration_segment_id,
                visual_asset_ref=candidates_by_scene.get(scene.scene_id),
                layer_specs=[
                    LayerSpec(
                        layer_id=f"layer_{scene.scene_id}_visual",
                        layer_type="VIDEO",
                        asset_ref=candidates_by_scene.get(scene.scene_id),
                        z_index=0,
                        metadata={"placeholder": True},
                    ),
                    LayerSpec(
                        layer_id=f"layer_{scene.scene_id}_caption",
                        layer_type="CAPTION",
                        z_index=10,
                        metadata={"caption_ids": scene.caption_ids},
                    ),
                ],
                overlay_text=scene.overlay_text,
            )
            for scene in visual_plan.scenes
        ]
        variant = RenderVariantSpec(
            variant_id="default_16x9",
            platform="YOUTUBE",
            surface="LONG_FORM",
            aspect_ratio="16:9",
            resolution_width=1280,
            resolution_height=720,
            fps=30,
            crop_strategy="LETTERBOX",
            caption_placement={"placement_key": "lower_third", "vertical_anchor": "BOTTOM", "max_lines": 2, "safe_area_aware": True},
            safe_area_profile={"profile_key": "youtube_16x9_default", "top_pct": 0.08, "bottom_pct": 0.12, "left_pct": 0.06, "right_pct": 0.06},
            overlay_scale=1.0,
            export_filename=f"{project.id}_default_16x9.mp4",
            variant_status="READY",
        )
        export_profile = ExportProfileContract(
            aspect_ratio="16:9",
            resolution_width=1280,
            resolution_height=720,
            fps=30,
            container="mp4",
            codec="h264",
        )
        payload = {
            "render_spec_id": f"render_spec_{run.id}",
            "video_project_id": project.id,
            "voice_timeline_snapshot_id": voice_timeline_snapshot.id,
            "visual_plan_snapshot_id": visual_plan_snapshot.id,
            "caption_track_snapshot_id": caption_track_snapshot.id,
            "asset_manifest_snapshot_id": asset_manifest_snapshot.id,
            "scene_manifest_snapshot_id": scene_manifest_snapshot.id,
            "scenes": [scene.model_dump(mode="json") for scene in scenes],
            "render_variants": [variant.model_dump(mode="json")],
            "audio_tracks": [
                {
                    "track_id": "mock_silent_audio",
                    "track_type": "SILENT",
                    "source_ref": "local_fixture://silent_audio",
                    "duration_seconds": voice_timeline.total_duration_seconds,
                    "metadata": {"explicit_silent_audio": True},
                }
            ],
            "caption_track_ref": f"caption_track_snapshot:{caption_track_snapshot.id}",
            "default_export_profile": export_profile.model_dump(mode="json"),
            "render_intent": "LOCAL_SMOKE",
            "total_duration_seconds": voice_timeline.total_duration_seconds,
        }
        payload["render_spec_hash"] = _hash_payload({**payload, "render_spec_hash": None})
        render_spec = RenderSpecContract.model_validate(payload)
        snapshot = RenderSpecSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=project.id,
            voice_timeline_snapshot_id=voice_timeline_snapshot.id,
            visual_plan_snapshot_id=visual_plan_snapshot.id,
            caption_track_snapshot_id=caption_track_snapshot.id,
            asset_manifest_snapshot_id=asset_manifest_snapshot.id,
            scene_manifest_snapshot_id=scene_manifest_snapshot.id,
            render_spec_blob=render_spec.model_dump(mode="json"),
            render_spec_hash=render_spec.render_spec_hash,
            validation_state="PASS",
            reason_codes=["RENDER_SPEC_CREATED", "RENDER_VARIANT_CREATED", "VOICE_AS_MASTER_CONTRACT_REQUIRED"],
        )
        self.session.add(snapshot)
        self.session.flush()
        version = _create_artifact_version(
            self.session,
            project=project,
            artifact_type="render_spec",
            content=render_spec.model_dump(mode="json"),
            created_by_user_id=project.created_by_user_id,
            context_refs=[
                {"type": "voice_timeline_snapshot", "id": str(voice_timeline_snapshot.id)},
                {"type": "visual_plan_snapshot", "id": str(visual_plan_snapshot.id)},
                {"type": "asset_manifest_snapshot", "id": str(asset_manifest_snapshot.id)},
            ],
            correlation_id="m6-render-spec-artifact",
        )
        run.render_spec_snapshot_id = snapshot.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="render_spec_snapshot.created",
            aggregate_type="render_spec_snapshot",
            aggregate_id=snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="render_spec_snapshot",
            target_id=snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="RENDER_SPEC_CREATED",
            payload={"render_spec_hash": snapshot.render_spec_hash, "artifact_version_id": str(version.id)},
        )
        _record_m6_event(
            self.session,
            event_type="render_variant.created",
            aggregate_type="render_spec_snapshot",
            aggregate_id=snapshot.id,
            actor_id=project.created_by_user_id,
            target_type="render_spec_snapshot",
            target_id=snapshot.id,
            company_id=project.company_id,
            correlation_id=correlation_id,
            reason_code="RENDER_VARIANT_CREATED",
            payload={"variant_id": variant.variant_id, "aspect_ratio": variant.aspect_ratio, "resolution": "1280x720"},
        )
        return snapshot

    def validate_snapshot(self, render_spec_snapshot_id: uuid.UUID) -> RenderSpecContract:
        snapshot = self.session.get(RenderSpecSnapshot, render_spec_snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"render spec snapshot not found: {render_spec_snapshot_id}")
        return RenderSpecContract.model_validate(snapshot.render_spec_blob)


class LocalFixtureRendererService:
    def __init__(self, session: Session):
        self.session = session

    def render_local_smoke(
        self,
        *,
        render_spec_snapshot_id: uuid.UUID,
        output_dir: Path | None = None,
        correlation_id: str = "m6-local-video-smoke",
    ) -> RenderResult:
        snapshot = self.session.get(RenderSpecSnapshot, render_spec_snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"render spec snapshot not found: {render_spec_snapshot_id}")
        if snapshot.validation_state != "PASS":
            raise ValidationFailureError("RenderSpec must validate before render job is created")
        render_spec = RenderSpecContract.model_validate(snapshot.render_spec_blob)
        run = self.session.get(ProductionArtifactRun, snapshot.production_artifact_run_id)
        job = MediaRenderJob(
            production_artifact_run_id=snapshot.production_artifact_run_id,
            video_project_id=snapshot.video_project_id,
            render_spec_snapshot_id=snapshot.id,
            render_variant_id=render_spec.render_variants[0].variant_id,
            renderer_key="LOCAL_FFMPEG",
            status="RUNNING",
            started_at=utc_now(),
            reason_codes=["LOCAL_RENDER_STARTED"],
            metadata_={"render_spec_hash": snapshot.render_spec_hash},
        )
        self.session.add(job)
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="media_render_job.created",
            aggregate_type="media_render_job",
            aggregate_id=job.id,
            actor_id=None,
            target_type="media_render_job",
            target_id=job.id,
            company_id=run.company_id if run else None,
            correlation_id=correlation_id,
            reason_code="LOCAL_RENDER_STARTED",
            payload={"render_spec_snapshot_id": str(snapshot.id), "renderer_key": job.renderer_key},
        )
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            reason = "FFMPEG_UNAVAILABLE" if ffmpeg is None else "FFPROBE_UNAVAILABLE"
            job.status = "BLOCKED"
            job.completed_at = utc_now()
            job.error_code = reason
            job.error_message_redacted = "local media dependency unavailable"
            job.reason_codes = _dedupe([*job.reason_codes, reason, "LOCAL_RENDER_FAILED"])
            self.session.flush()
            _record_m6_event(
                self.session,
                event_type="local_video_smoke.failed",
                aggregate_type="media_render_job",
                aggregate_id=job.id,
                actor_id=None,
                target_type="media_render_job",
                target_id=job.id,
                company_id=run.company_id if run else None,
                correlation_id=correlation_id,
                reason_code=reason,
                payload={"status": job.status, "reason_codes": job.reason_codes},
            )
            return RenderResult(job=job, package=None)
        output_root = output_dir or Path("var/generated/m6")
        output_root.mkdir(parents=True, exist_ok=True)
        stem = f"{snapshot.production_artifact_run_id}_{render_spec.render_variants[0].variant_id}"
        video_path = output_root / f"{stem}.mp4"
        srt_path = output_root / f"{stem}.srt"
        manifest_path = output_root / f"{stem}.manifest.json"
        caption_snapshot = self.session.get(CaptionTrackSnapshot, snapshot.caption_track_snapshot_id)
        if caption_snapshot is None:
            raise NotFoundError(f"caption track snapshot not found: {snapshot.caption_track_snapshot_id}")
        srt_path.write_text(caption_snapshot.srt_text or "", encoding="utf-8")
        variant = render_spec.render_variants[0]
        duration = max(0.1, render_spec.total_duration_seconds)
        command = [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x203040:s={variant.resolution_width}x{variant.resolution_height}:r={variant.fps}:d={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration:.3f}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(video_path),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            job.status = "FAILED"
            job.completed_at = utc_now()
            job.error_code = "LOCAL_RENDER_FAILED"
            job.error_message_redacted = _redact(completed.stderr)
            job.reason_codes = _dedupe([*job.reason_codes, "LOCAL_RENDER_FAILED"])
            self.session.flush()
            _record_m6_event(
                self.session,
                event_type="media_render_job.failed",
                aggregate_type="media_render_job",
                aggregate_id=job.id,
                actor_id=None,
                target_type="media_render_job",
                target_id=job.id,
                company_id=run.company_id if run else None,
                correlation_id=correlation_id,
                reason_code="LOCAL_RENDER_FAILED",
                payload={"status": job.status, "reason_codes": job.reason_codes},
            )
            return RenderResult(job=job, package=None)
        probed_duration = _ffprobe_duration(ffprobe, video_path)
        final_video_ref = _file_ref(
            video_path,
            mime_type="video/mp4",
            duration_seconds=probed_duration,
            width=variant.resolution_width,
            height=variant.resolution_height,
            created_by="LocalFixtureRendererService",
            source_type="LOCAL_FIXTURE",
        )
        caption_ref = _file_ref(
            srt_path,
            mime_type="application/x-subrip",
            duration_seconds=render_spec.total_duration_seconds,
            created_by="CaptionCompilerService",
            source_type="LOCAL_FIXTURE",
        )
        manifest = {
            "render_spec_snapshot_id": str(snapshot.id),
            "render_spec_hash": snapshot.render_spec_hash,
            "final_video_ref": final_video_ref,
            "caption_ref": caption_ref,
            "variant_id": variant.variant_id,
        }
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        manifest_ref = _file_ref(
            manifest_path,
            mime_type="application/json",
            created_by="LocalFixtureRendererService",
            source_type="LOCAL_FIXTURE",
        )
        package = RenderPackageSnapshot(
            production_artifact_run_id=snapshot.production_artifact_run_id,
            video_project_id=snapshot.video_project_id,
            media_render_job_id=job.id,
            render_spec_snapshot_id=snapshot.id,
            final_video_ref=final_video_ref,
            caption_ref=caption_ref,
            manifest_ref=manifest_ref,
            file_manifest={"files": [final_video_ref, caption_ref, manifest_ref]},
            checksum_manifest={
                "final_video": final_video_ref["checksum"],
                "caption": caption_ref["checksum"],
                "manifest": manifest_ref["checksum"],
            },
            duration_seconds=Decimal(str(probed_duration)),
            variant_outputs=[
                {
                    "variant_id": variant.variant_id,
                    "file_ref": final_video_ref,
                    "status": "RENDERED",
                }
            ],
            package_state="CREATED",
        )
        self.session.add(package)
        self.session.flush()
        _create_artifact_version(
            self.session,
            project=_require_project(self.session, snapshot.video_project_id),
            artifact_type="render_package",
            content={
                "render_package_snapshot_id": str(package.id),
                "render_spec_snapshot_id": str(snapshot.id),
                "file_manifest": package.file_manifest,
                "checksum_manifest": package.checksum_manifest,
                "duration_seconds": str(package.duration_seconds),
                "package_state": package.package_state,
            },
            created_by_user_id=_require_project(self.session, snapshot.video_project_id).created_by_user_id,
            packaging_metadata=package.file_manifest,
            media_qc_metadata={"package_state": package.package_state},
            correlation_id="m6-render-package-artifact",
        )
        job.status = "COMPLETED"
        job.completed_at = utc_now()
        job.output_ref = final_video_ref
        job.reason_codes = _dedupe([*job.reason_codes, "LOCAL_RENDER_COMPLETED", "FINAL_VIDEO_CREATED", "GENERATED_MEDIA_NOT_STAGED"])
        if run is not None:
            run.render_package_snapshot_id = package.id
        self.session.flush()
        _record_m6_event(
            self.session,
            event_type="media_render_job.completed",
            aggregate_type="media_render_job",
            aggregate_id=job.id,
            actor_id=None,
            target_type="media_render_job",
            target_id=job.id,
            company_id=run.company_id if run else None,
            correlation_id=correlation_id,
            reason_code="LOCAL_RENDER_COMPLETED",
            payload={"output_checksum": final_video_ref["checksum"], "duration_seconds": probed_duration},
        )
        _record_m6_event(
            self.session,
            event_type="render_package_snapshot.created",
            aggregate_type="render_package_snapshot",
            aggregate_id=package.id,
            actor_id=None,
            target_type="render_package_snapshot",
            target_id=package.id,
            company_id=run.company_id if run else None,
            correlation_id=correlation_id,
            reason_code="FINAL_VIDEO_CREATED",
            payload={"file_manifest_count": len(package.file_manifest.get("files", [])), "duration_seconds": probed_duration},
        )
        _record_m6_event(
            self.session,
            event_type="local_video_smoke.completed",
            aggregate_type="media_render_job",
            aggregate_id=job.id,
            actor_id=None,
            target_type="media_render_job",
            target_id=job.id,
            company_id=run.company_id if run else None,
            correlation_id=correlation_id,
            reason_code="FINAL_VIDEO_CREATED",
            payload={"final_video_ref": final_video_ref},
        )
        return RenderResult(job=job, package=package)

    def get_job(self, render_job_id: uuid.UUID) -> MediaRenderJob | None:
        return self.session.get(MediaRenderJob, render_job_id)

    def get_package(self, render_package_id: uuid.UUID) -> RenderPackageSnapshot | None:
        return self.session.get(RenderPackageSnapshot, render_package_id)


class MediaQCService:
    def __init__(self, session: Session):
        self.session = session

    def run_blocked_qc(
        self,
        *,
        render_spec_snapshot: RenderSpecSnapshot,
        render_job: MediaRenderJob,
        correlation_id: str = "m6-media-qc",
    ) -> MediaQCReport:
        report = MediaQCReport(
            production_artifact_run_id=render_spec_snapshot.production_artifact_run_id,
            video_project_id=render_spec_snapshot.video_project_id,
            render_package_snapshot_id=None,
            render_spec_snapshot_id=render_spec_snapshot.id,
            qc_state="BLOCK",
            duration_check={"state": "BLOCK", "reason": "no render package"},
            scene_coverage_check={"state": "PASS", "source": "render_spec"},
            caption_alignment_check={"state": "PASS", "source": "render_spec"},
            audio_presence_check={"state": "PASS", "explicit_silent_audio": True},
            file_integrity_check={"state": "BLOCK", "reason_codes": render_job.reason_codes},
            manifest_check={"state": "BLOCK", "reason": "missing package manifest"},
            variant_check={"state": "PASS", "source": "render_spec"},
            reason_codes=_dedupe(["MEDIA_QC_BLOCKED", *render_job.reason_codes]),
        )
        self.session.add(report)
        self.session.flush()
        _record_qc_artifact(self.session, report)
        _record_m6_event(
            self.session,
            event_type="media_qc_report.created",
            aggregate_type="media_qc_report",
            aggregate_id=report.id,
            actor_id=None,
            target_type="media_qc_report",
            target_id=report.id,
            company_id=_project_company(self.session, report.video_project_id),
            correlation_id=correlation_id,
            reason_code="MEDIA_QC_BLOCKED",
            payload={"qc_state": report.qc_state, "reason_codes": report.reason_codes},
        )
        return report

    def run_qc(
        self,
        *,
        render_package_snapshot: RenderPackageSnapshot,
        correlation_id: str = "m6-media-qc",
    ) -> MediaQCReport:
        render_spec_snapshot = self.session.get(RenderSpecSnapshot, render_package_snapshot.render_spec_snapshot_id)
        if render_spec_snapshot is None:
            raise NotFoundError("render spec snapshot not found for render package")
        render_spec = RenderSpecContract.model_validate(render_spec_snapshot.render_spec_blob)
        final_ref = render_package_snapshot.final_video_ref or {}
        file_path = Path(final_ref.get("file_path", ""))
        exists = file_path.exists()
        size_ok = exists and file_path.stat().st_size > 0
        checksum_ok = bool(final_ref.get("checksum")) and (not exists or _sha256_file(file_path) == final_ref.get("checksum"))
        duration = float(render_package_snapshot.duration_seconds or 0)
        duration_ok = abs(duration - render_spec.total_duration_seconds) <= 0.75
        manifest_ok = bool(render_package_snapshot.file_manifest) and bool(render_package_snapshot.checksum_manifest)
        state = "PASS" if all([exists, size_ok, checksum_ok, duration_ok, manifest_ok]) else "BLOCK"
        reasons = ["MEDIA_QC_PASSED"] if state == "PASS" else ["MEDIA_QC_BLOCKED"]
        if not duration_ok:
            reasons.append("DURATION_MISMATCH")
        if not checksum_ok or not manifest_ok:
            reasons.append("MANIFEST_CHECKSUM_MISSING")
        report = MediaQCReport(
            production_artifact_run_id=render_package_snapshot.production_artifact_run_id,
            video_project_id=render_package_snapshot.video_project_id,
            render_package_snapshot_id=render_package_snapshot.id,
            render_spec_snapshot_id=render_spec_snapshot.id,
            qc_state=state,
            duration_check={"state": "PASS" if duration_ok else "BLOCK", "expected": render_spec.total_duration_seconds, "actual": duration},
            scene_coverage_check={"state": "PASS", "scene_count": len(render_spec.scenes)},
            caption_alignment_check={"state": "PASS", "caption_track_ref": render_spec.caption_track_ref},
            audio_presence_check={"state": "PASS", "explicit_silent_audio": True},
            file_integrity_check={"state": "PASS" if exists and size_ok and checksum_ok else "BLOCK", "exists": exists, "size_ok": size_ok, "checksum_ok": checksum_ok},
            manifest_check={"state": "PASS" if manifest_ok else "BLOCK"},
            variant_check={"state": "PASS", "variant_count": len(render_spec.render_variants)},
            reason_codes=_dedupe(reasons),
        )
        self.session.add(report)
        self.session.flush()
        render_package_snapshot.package_state = "QC_PASSED" if state == "PASS" else "QC_BLOCKED"
        self.session.flush()
        _record_qc_artifact(self.session, report)
        _record_m6_event(
            self.session,
            event_type="media_qc_report.created",
            aggregate_type="media_qc_report",
            aggregate_id=report.id,
            actor_id=None,
            target_type="media_qc_report",
            target_id=report.id,
            company_id=_project_company(self.session, report.video_project_id),
            correlation_id=correlation_id,
            reason_code=report.reason_codes[0],
            payload={"qc_state": report.qc_state, "reason_codes": report.reason_codes},
        )
        return report


class AccessibilityQCService:
    def __init__(self, session: Session):
        self.session = session

    def run_qc(
        self,
        *,
        caption_track_snapshot: CaptionTrackSnapshot,
        render_package_snapshot: RenderPackageSnapshot | None = None,
        correlation_id: str = "m6-accessibility-qc",
    ) -> AccessibilityQCReportModel:
        caption_track = CaptionTrackContract.model_validate(caption_track_snapshot.caption_blob)
        captions_present = bool(caption_track.cues) and bool(caption_track.srt_text)
        readability_ok = all((cue.char_count or len(cue.text)) <= 180 and (cue.line_count or 1) <= 2 for cue in caption_track.cues)
        state = "PASS" if captions_present and readability_ok else "REVIEW_REQUIRED"
        reasons = ["ACCESSIBILITY_QC_PASSED"] if state == "PASS" else ["ACCESSIBILITY_QC_REVIEW_REQUIRED"]
        report = AccessibilityQCReportModel(
            production_artifact_run_id=caption_track_snapshot.production_artifact_run_id,
            video_project_id=caption_track_snapshot.video_project_id,
            caption_track_snapshot_id=caption_track_snapshot.id,
            render_package_snapshot_id=render_package_snapshot.id if render_package_snapshot else None,
            qc_state=state,
            caption_presence_check={"state": "PASS" if captions_present else "BLOCK", "cue_count": len(caption_track.cues)},
            caption_readability_check={"state": "PASS" if readability_ok else "REVIEW_REQUIRED"},
            safe_area_check={"state": "PASS", "placeholder": True},
            flashing_risk_check={"state": "PASS", "placeholder": True, "aesthetics_scored": False},
            disclosure_placement_check={"state": "PASS", "placeholder": True},
            pronunciation_check={"state": "PASS", "placeholder": True},
            reason_codes=reasons,
        )
        self.session.add(report)
        self.session.flush()
        _record_accessibility_artifact(self.session, report)
        _record_m6_event(
            self.session,
            event_type="accessibility_qc_report.created",
            aggregate_type="accessibility_qc_report",
            aggregate_id=report.id,
            actor_id=None,
            target_type="accessibility_qc_report",
            target_id=report.id,
            company_id=_project_company(self.session, report.video_project_id),
            correlation_id=correlation_id,
            reason_code=report.reason_codes[0],
            payload={"qc_state": report.qc_state, "reason_codes": report.reason_codes},
        )
        return report


def export_srt(cues: list[CaptionCueContract]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(f"{index}\n{_srt_timestamp(cue.start_time)} --> {_srt_timestamp(cue.end_time)}\n{cue.text}")
    return "\n\n".join(blocks) + "\n"


def _srt_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _require_project(session: Session, project_id: uuid.UUID) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"project not found: {project_id}")
    return project


def _require_m5_project_inputs(session: Session, project_id: uuid.UUID) -> list[ArtifactVersion]:
    versions: list[ArtifactVersion] = []
    for artifact_type in M5_REQUIRED_ARTIFACT_TYPES:
        artifact = session.scalars(
            select(Artifact).where(Artifact.video_project_id == project_id, Artifact.artifact_type == artifact_type)
        ).one_or_none()
        if artifact is None or artifact.current_version_id is None:
            raise ValidationFailureError(f"M6 requires M5 {artifact_type} draft artifact")
        version = session.get(ArtifactVersion, artifact.current_version_id)
        if version is None:
            raise ValidationFailureError(f"M6 requires current version for {artifact_type}")
        versions.append(version)
    return versions


def _content_by_artifact_type(session: Session, versions: list[ArtifactVersion]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for version in versions:
        artifact = session.get(Artifact, version.artifact_id)
        if artifact is not None:
            result[artifact.artifact_type] = version.content
    return result


def _create_artifact_version(
    session: Session,
    *,
    project: VideoProject,
    artifact_type: str,
    content: dict[str, Any],
    created_by_user_id: uuid.UUID,
    evidence_refs: list[dict[str, Any]] | None = None,
    context_refs: list[dict[str, Any]] | None = None,
    packaging_metadata: dict[str, Any] | None = None,
    media_qc_metadata: dict[str, Any] | None = None,
    source_manifest: dict[str, Any] | None = None,
    correlation_id: str,
) -> ArtifactVersion:
    if artifact_type not in M6_ARTIFACT_TYPES and artifact_type not in M5_REQUIRED_ARTIFACT_TYPES:
        raise ValidationFailureError(f"artifact type is not allowed in M6: {artifact_type}")
    artifact = session.scalars(
        select(Artifact).where(Artifact.video_project_id == project.id, Artifact.artifact_type == artifact_type)
    ).one_or_none()
    artifact_service = ArtifactService(session)
    if artifact is None:
        artifact = artifact_service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project.id,
                artifact_type=artifact_type,
                created_by_user_id=created_by_user_id,
            ),
            correlation_id=correlation_id,
        )
    max_version = session.scalar(select(func.max(ArtifactVersion.version_number)).where(ArtifactVersion.artifact_id == artifact.id)) or 0
    version = artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            parent_version_id=artifact.current_version_id if max_version else None,
            content=_jsonable(content),
            created_by_user_id=created_by_user_id,
            evidence_refs=evidence_refs or [],
            context_refs=context_refs or [],
            packaging_metadata=packaging_metadata or {},
            media_qc_metadata=media_qc_metadata or {},
            source_manifest=source_manifest or {},
        ),
        correlation_id=correlation_id,
    )
    return version


def _collect_evidence_refs(versions: list[ArtifactVersion]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for version in versions:
        refs.extend(version.evidence_refs)
        content_refs = version.content.get("evidence_refs") or version.content.get("source_refs") or []
        if isinstance(content_refs, list):
            refs.extend(content_refs)
    return refs


def _record_mock_cost(session: Session, project: VideoProject, *, provider_run_ref: str) -> CostEvent:
    ProviderRegistryService(session).require_entry("mock_llm")
    return CostService(session).record_event(
        data=CostEventCreate(
            provider_key="mock_llm",
            cost_scope_type="PROJECT",
            cost_scope_id=project.id,
            amount=Decimal("0"),
            cost_type="ESTIMATED",
            unit_count=Decimal("1"),
            unit_type="REQUESTS",
            provider_run_ref=provider_run_ref,
            metadata={"mock": True, "operation_key": "m6_script_draft"},
        ),
        correlation_id="m6-llm-cost-event",
    )


def _create_llm_run(
    session: Session,
    *,
    run: ProductionArtifactRun,
    status: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any] | None,
    cost_event_id: uuid.UUID | None,
    correlation_id: str,
) -> LLMRunSnapshot:
    payload = {
        **input_payload,
        "production_artifact_run_id": str(run.id),
        "policy_snapshot_id": str(run.policy_snapshot_id),
        "provider_key": "mock_llm",
    }
    llm_run = LLMRunSnapshot(
        run_type="M6_SCRIPT_DRAFT",
        provider="mock",
        model_name="mock-llm",
        provider_key="mock_llm",
        model_key="mock-llm",
        run_mode="MOCK",
        prompt_template_key="m6_script_draft",
        prompt_template_version="1.0.0",
        input_payload=_jsonable(payload),
        input_hash=_hash_payload(payload),
        output_payload=_jsonable(output_payload) if output_payload is not None else None,
        output_hash=_hash_payload(output_payload) if output_payload is not None else None,
        status=status,
        estimated_cost=Decimal("0"),
        token_estimate=Decimal("0"),
        quota_event_id=None,
        cost_event_id=cost_event_id,
        cost_payload={"estimated_cost": "0", "currency": "USD", "mock": True},
        correlation_id=correlation_id,
        completed_at=utc_now(),
    )
    session.add(llm_run)
    session.flush()
    _record_m6_event(
        session,
        event_type="llm_run_snapshot.created_mock",
        aggregate_type="llm_run_snapshot",
        aggregate_id=llm_run.id,
        actor_id=None,
        target_type="llm_run_snapshot",
        target_id=llm_run.id,
        company_id=run.company_id,
        correlation_id=correlation_id,
        reason_code="LLM_RUN_SNAPSHOT_CREATED",
        payload={"run_type": llm_run.run_type, "provider_key": llm_run.provider_key, "status": llm_run.status},
    )
    return llm_run


def _record_provider_attempt(
    session: Session,
    *,
    provider_key: str,
    operation_key: str,
    target_type: str,
    target_id: uuid.UUID,
    status: str,
    error_code: str | None,
    latency_ms: int | None,
    cost_event_id: uuid.UUID | None,
    metadata: dict[str, Any],
    correlation_id: str,
    company_id: uuid.UUID | None,
) -> ProviderAttempt:
    attempt = ProviderAttempt(
        provider_key=provider_key,
        operation_key=operation_key,
        target_type=target_type,
        target_id=target_id,
        attempt_number=1,
        status=status,
        error_code=error_code,
        error_message_redacted="redacted provider error" if error_code else None,
        started_at=utc_now(),
        finished_at=utc_now(),
        latency_ms=latency_ms,
        cost_event_id=cost_event_id,
        quota_event_id=None,
        metadata_=metadata,
    )
    session.add(attempt)
    session.flush()
    _record_m6_event(
        session,
        event_type="provider_attempt.created",
        aggregate_type="provider_attempt",
        aggregate_id=attempt.id,
        actor_id=None,
        target_type="provider_attempt",
        target_id=attempt.id,
        company_id=company_id,
        correlation_id=correlation_id,
        reason_code="MOCK_PROVIDER_ONLY" if status == "SUCCESS" else "BAD_ARTIFACT_REJECTED",
        payload={
            "provider_key": attempt.provider_key,
            "operation_key": attempt.operation_key,
            "status": attempt.status,
            "target_type": attempt.target_type,
            "target_id": str(attempt.target_id) if attempt.target_id else None,
        },
    )
    return attempt


def _provider_attempt_status(response: Any) -> str:
    if response.ok:
        return "SUCCESS"
    if response.error_code == "PROVIDER_QUOTA_EXCEEDED":
        return "QUOTA_REJECTED"
    if response.error_code == "CIRCUIT_BREAKER_OPEN":
        return "CIRCUIT_OPEN"
    if response.retryable:
        return "RETRYABLE_FAILURE"
    return "NON_RETRYABLE_FAILURE"


def _record_qc_artifact(session: Session, report: MediaQCReport) -> None:
    project = _require_project(session, report.video_project_id)
    _create_artifact_version(
        session,
        project=project,
        artifact_type="media_qc_report",
        content={
            "media_qc_report_id": str(report.id),
            "qc_state": report.qc_state,
            "reason_codes": report.reason_codes,
            "duration_check": report.duration_check,
            "file_integrity_check": report.file_integrity_check,
            "manifest_check": report.manifest_check,
        },
        created_by_user_id=project.created_by_user_id,
        media_qc_metadata={"qc_state": report.qc_state, "reason_codes": report.reason_codes},
        correlation_id="m6-media-qc-artifact",
    )


def _record_accessibility_artifact(session: Session, report: AccessibilityQCReportModel) -> None:
    project = _require_project(session, report.video_project_id)
    _create_artifact_version(
        session,
        project=project,
        artifact_type="accessibility_qc_report",
        content={
            "accessibility_qc_report_id": str(report.id),
            "qc_state": report.qc_state,
            "reason_codes": report.reason_codes,
            "caption_presence_check": report.caption_presence_check,
            "caption_readability_check": report.caption_readability_check,
        },
        created_by_user_id=project.created_by_user_id,
        media_qc_metadata={"qc_state": report.qc_state, "reason_codes": report.reason_codes},
        correlation_id="m6-accessibility-qc-artifact",
    )


def _classification_summary(source_refs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for ref in source_refs:
        key = str(ref.get("source_class"))
        counts[key] = counts.get(key, 0) + 1
    return {"source_class_counts": counts, "real_provider_calls": 0, "envato_api_calls": 0}


def _summary(text: str) -> str:
    words = text.split()
    return " ".join(words[:14]) if len(words) > 14 else text


def _visual_intent(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("workflow", "system", "process", "timeline")):
        return "diagram of the production process"
    if any(word in lowered for word in ("data", "metric", "evidence")):
        return "simple data card placeholder"
    return "generic local fixture b-roll placeholder"


def _preferred_source_for_text(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("workflow", "system", "process", "timeline", "data")):
        return "DIAGRAM_PLACEHOLDER"
    return "LOCAL_FIXTURE"


def _scene_type_for_intent(intent: str) -> str:
    lowered = intent.lower()
    if "data" in lowered:
        return "DATA"
    if "diagram" in lowered or "process" in lowered:
        return "PROCESS"
    return "GENERIC_BROLL"


def _overlay_text(text: str) -> str:
    words = text.split()
    return " ".join(words[:6])


def _file_ref(
    path: Path,
    *,
    mime_type: str,
    created_by: str,
    source_type: str,
    duration_seconds: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    ref = FileRefContract(
        file_path=str(path),
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        checksum=_sha256_file(path),
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        created_by=created_by,
        source_type=source_type,
        license_state="INTERNAL_TEST_ONLY",
        provenance_blob={"created_at": utc_now().isoformat(), "local_only": True},
    )
    return ref.model_dump(mode="json")


def _ffprobe_duration(ffprobe: str, path: Path) -> float:
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValidationFailureError("ffprobe could not read generated MP4")
    payload = json.loads(completed.stdout)
    return float(payload["format"]["duration"])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_payload(value: Any) -> str:
    jsonable = _jsonable(value)
    if not isinstance(jsonable, dict):
        jsonable = {"value": jsonable}
    return content_hash(jsonable)


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _record_m6_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
) -> None:
    safe_payload = _jsonable(payload)
    _ensure_no_secret_payload(safe_payload)
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload=safe_payload,
        ),
        company_id=company_id,
    )
    AuditService(session).append(
        AuditEnvelope(
            action=event_type,
            actor_type="system" if actor_id is None else "user",
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            correlation_id=correlation_id,
            reason_code=reason_code,
            payload=safe_payload,
        ),
        company_id=company_id,
    )


def _ensure_no_secret_payload(value: Any) -> None:
    for key, item in _walk_items(value):
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS) and normalized != "secret_ref":
            raise ValidationFailureError(f"secret-like payload key is not allowed: {key}")
        if isinstance(item, str) and any(marker in item for marker in RAW_SECRET_MARKERS):
            raise ValidationFailureError("raw secret-like value is not allowed")


def _walk_items(value: Any):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield str(child_key), child_value
            yield from _walk_items(child_value)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_items(item)


def _project_company(session: Session, project_id: uuid.UUID) -> uuid.UUID | None:
    project = session.get(VideoProject, project_id)
    return project.company_id if project else None


def _redact(value: str) -> str:
    return value[:400].replace("\n", " ")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
