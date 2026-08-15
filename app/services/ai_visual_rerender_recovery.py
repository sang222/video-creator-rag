"""Governed, append-only AI-only replacement of one reviewed native video.

This service is intentionally narrower than the normal production starter.  It
does not re-run editorial, package, MEDIA, TTS, or forced alignment.  It binds
one immutable historical FINAL_REVIEW_READY candidate to a distinct workflow,
a fresh visual budget, and one AI-only VISUAL command.  Repeated authorization
revalidates and returns the same lineage; it never creates a second provider
budget or a second replacement workflow.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.production_workflow import ProductionWorkflowStage
from app.contracts.vcos_v2 import ProductionLane
from app.core.actor import ActorContext, ActorType
from app.core.config import (
    Settings,
    VEO_DEFAULT_DURATION_SECONDS,
    VEO_DEFAULT_MODEL_ID,
    VEO_DEFAULT_OUTPUT_COUNT,
    VEO_DEFAULT_RESOLUTION,
)
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.ai_visual import (
    AIVisualProductionRun,
    AIVisualRerenderAuthority,
)
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.db.models.foundation import DomainEvent
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.v2_effect import (
    V2NarrationTimingRecoveryAuthority,
    V2NarrationTimingRecoveryReceipt,
    V2ProductionEffectLedger,
)
from app.db.models.voice_authority import CombinedReplacementBudgetAuthority
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.ai_visual_rerender_authority import (
    AI_VISUAL_POLICY_REF,
    active_ai_visual_policy_authority,
    resolve_governed_ai_visual_rerender_execution_authority,
    seal_ai_visual_rerender_authority_hash,
)
from app.services.config_registry import content_hash
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.combined_replacement_budget import (
    CombinedReplacementBudgetAuthorityService,
)
from app.services.production_workflow import (
    WORKFLOW_AGGREGATE_TYPE,
    WORKFLOW_CORRELATION_PREFIX,
    WORKFLOW_EVENT_TYPE,
    WORKFLOW_EVENT_VERSION,
    ProductionWorkflowCoordinator,
    command_id_for,
    handler_key_for,
    semantic_hash,
)
from app.services.production_package import ProductionPackageService
from app.services.v2_support_authority import V2FrozenSupportEnvelope
from app.workers.production_workflow import ProductionWorkflowWorker, WorkerRunResult


_CONTROLLED_RECOVERY_ACTOR_ID = uuid.UUID("6d196d74-7938-5c85-bc10-f25466616258")
_IDENTITY_NAMESPACE = uuid.UUID("03230f90-90d5-58c7-a355-91591d3237e1")
_RERENDER_REASON = "OPERATOR_REQUESTED_AI_ONLY_VISUAL_REPLACEMENT"
_REJECTED_VISUAL_POLICY = "NATIVE_EXPLANATORY_DIAGRAM"
_ALLOWED_REPLACEMENT_EVENT_STAGES = frozenset(
    {"VISUAL", "RENDER", "QC", "ARCHIVE", "FINALIZE"}
)
_SOURCE_MEDIA_RESULT_TYPE = "V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE"
_SOURCE_MEDIA_ADAPTER = "v2-elevenlabs-narration"
_SHA256 = frozenset("0123456789abcdef")


def _current_veo_unit_cost_usd() -> Decimal:
    return GoogleVeoModelPriceCatalog().estimate(
        model_id=VEO_DEFAULT_MODEL_ID,
        resolution=VEO_DEFAULT_RESOLUTION,
        duration_seconds=VEO_DEFAULT_DURATION_SECONDS,
        output_count=VEO_DEFAULT_OUTPUT_COUNT,
        hard_cap=Decimal("1000000"),
        approval_amount=Decimal("1000000"),
    ).estimated_amount


class _ExactEventWorker(Protocol):
    def run_exact_event(self, *, event_id: uuid.UUID) -> WorkerRunResult: ...


@dataclass(frozen=True, slots=True)
class AIVisualRerenderAuthorizationResult:
    authority_id: uuid.UUID
    authority_hash: str
    source_workflow_run_id: uuid.UUID
    replacement_workflow_run_id: uuid.UUID
    visual_production_run_id: uuid.UUID
    budget_reservation_id: uuid.UUID
    budget_reservation_ref: str
    visual_event_id: uuid.UUID
    workflow_state: str
    visual_run_state: str
    replayed: bool
    automatic_publish: bool = False


@dataclass(frozen=True, slots=True)
class AIVisualRerenderRunOnceResult:
    authority_id: uuid.UUID
    source_workflow_run_id: uuid.UUID
    replacement_workflow_run_id: uuid.UUID
    visual_production_run_id: uuid.UUID
    workflow_state: str
    visual_run_state: str
    event_id: uuid.UUID | None
    event_stage: str | None
    worker_result: WorkerRunResult | None
    automatic_publish: bool = False


@dataclass(frozen=True, slots=True)
class _SourceScope:
    candidate: FinalReviewCandidate
    final_media: FinalMediaRef
    source_workflow: ProductionWorkflowRun
    project: VideoProject
    timing_authority: V2NarrationTimingRecoveryAuthority
    timing_receipt: V2NarrationTimingRecoveryReceipt
    media_ledger: V2ProductionEffectLedger
    package_version: ArtifactVersion
    readiness_version: ArtifactVersion
    script_version: ArtifactVersion
    timed_words_version: ArtifactVersion
    caption_version: ArtifactVersion
    subtitle_qc_version: ArtifactVersion
    policy_snapshot: CompiledChannelPolicySnapshot
    channel_policy: ChannelScopedPolicy
    source_media_budget: MR1MonthlyBudgetReservation
    combined_budget_authority: CombinedReplacementBudgetAuthority
    timeline: dict[str, Any]
    timeline_relative_path: str
    audio_relative_path: str
    caption_relative_path: str
    timed_words_ref: str
    caption_artifact_ref: str
    subtitle_qc_ref: str


class AIVisualRerenderRecoveryService:
    """Authorize and advance exactly one governed AI visual replacement."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        now: Callable[[], datetime] = utc_now,
        workspace_root: Path | None = None,
        worker_factory: Callable[[], _ExactEventWorker] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.now = now
        repository_root = Path(__file__).resolve().parents[2]
        configured = os.getenv("VCOS_V2_PRODUCTION_ROOT")
        self.root = (
            workspace_root
            or (
                Path(configured)
                if configured
                else repository_root / "var/v2-production"
            )
        ).resolve()
        self._worker_factory = worker_factory or (
            lambda: ProductionWorkflowWorker(now=self.now)
        )

    def authorize(
        self,
        source_final_review_candidate_id: uuid.UUID,
        actor: ActorContext,
    ) -> AIVisualRerenderAuthorizationResult:
        """Create or return the sole append-only replacement authority."""

        self._require_controlled_actor(actor)
        self._lock_candidate_lineage(source_final_review_candidate_id)
        existing = self.session.scalar(
            select(AIVisualRerenderAuthority)
            .where(
                AIVisualRerenderAuthority.rejected_final_review_candidate_id
                == source_final_review_candidate_id
            )
            .with_for_update()
        )
        if existing is not None:
            result = self._replay_result(existing)
            self.session.commit()
            return result

        scope = self._resolve_source_scope(source_final_review_candidate_id)
        policy = active_ai_visual_policy_authority()
        visual_requirement = self._require_governed_visual_route_authority(scope)
        combined_partition = self._require_governed_combined_visual_partition(
            scope,
            image_count=visual_requirement[0],
            video_count=visual_requirement[1],
        )
        created_at = self.now()
        authority_id = self._stable_id(
            "authority", scope.candidate.id, scope.candidate.candidate_hash
        )
        replacement_workflow_id = self._stable_id(
            "workflow", scope.candidate.id, scope.candidate.candidate_hash
        )
        visual_run_id = self._stable_id(
            "visual-run", scope.candidate.id, scope.candidate.candidate_hash
        )

        replacement = self._new_replacement_workflow(
            scope=scope,
            policy=policy,
            authority_id=authority_id,
            workflow_id=replacement_workflow_id,
            visual_run_id=visual_run_id,
            created_at=created_at,
            actor=actor,
        )
        self.session.add(replacement)
        self.session.flush()

        # The pre-TTS aggregate reservation already occupies the Gemini/Veo
        # capacity.  A governed rerender is a zero-additional-occupancy child
        # of that reservation; creating a visual-run MR1 reservation here
        # would double-count the same replacement projection.
        budget = scope.source_media_budget
        budget_hash = scope.combined_budget_authority.content_hash
        if (
            budget.id != scope.combined_budget_authority.budget_reservation_id
            or budget.reservation_ref
            != scope.combined_budget_authority.budget_reservation_ref
            or budget.run_id != scope.source_workflow.id
            or combined_partition.get("aggregate_reservation_id") != str(budget.id)
            or combined_partition.get("aggregate_reservation_ref")
            != budget.reservation_ref
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_COMBINED_BUDGET_DRIFT")

        authority_values: dict[str, Any] = {
            "id": authority_id,
            "authorized_visual_production_run_id": visual_run_id,
            "source_workflow_run_id": scope.source_workflow.id,
            "replacement_workflow_run_id": replacement.id,
            "video_project_id": scope.project.id,
            "production_package_artifact_version_id": scope.package_version.id,
            "production_package_hash": scope.package_version.content_hash,
            "production_readiness_receipt_artifact_version_id": (
                scope.readiness_version.id
            ),
            "production_readiness_receipt_hash": scope.readiness_version.content_hash,
            "script_artifact_version_id": scope.script_version.id,
            "script_content_hash": scope.script_version.content_hash,
            "canonical_narration_hash": scope.timing_authority.approved_script_hash,
            "audio_ref": scope.audio_relative_path,
            "audio_checksum": scope.timing_authority.audio_checksum_sha256,
            "audio_duration_ms": scope.timing_authority.audio_duration_ms,
            "timed_words_artifact_version_id": scope.timed_words_version.id,
            "timed_words_hash": scope.timed_words_version.content_hash,
            "caption_artifact_version_id": scope.caption_version.id,
            "caption_hash": scope.caption_version.content_hash,
            "caption_checksum": str(
                (scope.media_ledger.effect_journal or {})["caption_checksum"]
            ),
            "subtitle_qc_artifact_version_id": scope.subtitle_qc_version.id,
            "subtitle_qc_hash": scope.subtitle_qc_version.content_hash,
            "rejected_final_media_ref_id": scope.final_media.id,
            "rejected_final_media_hash": scope.candidate.final_media_hash,
            "rejected_final_review_candidate_id": scope.candidate.id,
            "rejected_final_review_candidate_hash": scope.candidate.candidate_hash,
            "rejected_visual_policy": _REJECTED_VISUAL_POLICY,
            "production_visual_policy_version": policy["version"],
            "production_visual_policy_ref": policy["ref"],
            "production_visual_policy_hash": policy["hash"],
            "budget_reservation_id": budget.id,
            "budget_reservation_ref": budget.reservation_ref,
            "budget_authority_hash": budget_hash,
            "maximum_total_cost_usd": visual_requirement[2],
            "maximum_scene_count": visual_requirement[3],
            "maximum_image_submissions": visual_requirement[0],
            "maximum_video_submissions": visual_requirement[1],
            "maximum_tts_submissions": 0,
            "maximum_forced_alignment_submissions": 0,
            "narration_timing_recovery_authority_id": scope.timing_authority.id,
            "narration_timing_recovery_authority_hash": (
                scope.timing_authority.authority_hash
            ),
            "narration_timing_recovery_receipt_id": scope.timing_receipt.id,
            "narration_timing_recovery_receipt_hash": (
                scope.timing_receipt.receipt_hash
            ),
            "automatic_publish": False,
            "authorized_by_actor_type": actor.actor_type.value,
            "authorized_by_actor_id": actor.actor_id,
            "authorized_by_actor_role": actor.actor_role,
            "created_at": created_at,
        }
        authority = AIVisualRerenderAuthority(
            **authority_values,
            authority_hash=seal_ai_visual_rerender_authority_hash(authority_values),
        )
        self.session.add(authority)
        self.session.flush()

        visual_run = AIVisualProductionRun(
            id=visual_run_id,
            workflow_run_id=replacement.id,
            video_project_id=scope.project.id,
            rerender_authority_id=authority.id,
            execution_kind="GOVERNED_RERENDER",
            production_package_artifact_version_id=scope.package_version.id,
            production_package_hash=scope.package_version.content_hash,
            production_visual_policy_version=str(policy["version"]),
            production_visual_policy_ref=str(policy["ref"]),
            production_visual_policy_hash=str(policy["hash"]),
            source_timeline_ref=str(scope.source_workflow.canonical_media_timeline_ref),
            source_timeline_hash=str(
                scope.source_workflow.canonical_media_timeline_hash
            ),
            audio_ref=scope.audio_relative_path,
            audio_checksum=scope.timing_authority.audio_checksum_sha256,
            audio_duration_ms=scope.timing_authority.audio_duration_ms,
            timed_words_ref=scope.timed_words_ref,
            timed_words_hash=scope.timed_words_version.content_hash,
            caption_ref=scope.caption_relative_path,
            caption_hash=scope.caption_version.content_hash,
            caption_checksum=str(
                (scope.media_ledger.effect_journal or {})["caption_checksum"]
            ),
            subtitle_qc_ref=scope.subtitle_qc_ref,
            subtitle_qc_hash=scope.subtitle_qc_version.content_hash,
            budget_reservation_id=budget.id,
            budget_reservation_ref=budget.reservation_ref,
            budget_authority_hash=budget_hash,
            state="AUTHORIZED",
            current_phase="AUTHORIZE",
            projection_version=1,
            started_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        self.session.add(visual_run)
        self.session.flush()

        replacement.ai_visual_production_run_id = visual_run.id
        replacement.ai_visual_policy_ref = str(policy["ref"])
        replacement.ai_visual_policy_hash = str(policy["hash"])
        replacement.projection_version += 1
        replacement.last_progress_at = created_at
        coordinator = ProductionWorkflowCoordinator(self.session, now=self.now)
        event = coordinator._schedule_stage(
            replacement,
            ProductionWorkflowStage.VISUAL,
            # Stage delivery may reconcile a durable provider journal after a
            # software crash.  The scene effect itself still has one provider
            # submission and no provider retry; this is only the bounded local
            # workflow delivery/repair allowance.
            max_attempts=5,
        )
        self.session.flush()
        self._validate_fresh_lineage(
            authority=authority,
            visual_run=visual_run,
            replacement=replacement,
            visual_event=event,
        )
        self.session.commit()
        return AIVisualRerenderAuthorizationResult(
            authority_id=authority.id,
            authority_hash=authority.authority_hash,
            source_workflow_run_id=scope.source_workflow.id,
            replacement_workflow_run_id=replacement.id,
            visual_production_run_id=visual_run.id,
            budget_reservation_id=budget.id,
            budget_reservation_ref=budget.reservation_ref,
            visual_event_id=event.id,
            workflow_state=replacement.state,
            visual_run_state=visual_run.state,
            replayed=False,
        )

    def run_once(
        self,
        authority_id: uuid.UUID,
        actor: ActorContext,
    ) -> AIVisualRerenderRunOnceResult:
        """Execute at most one known event from only this replacement workflow."""

        self._require_controlled_actor(actor)
        if not actor.has_permission("production.workflow.execute"):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_CONTROLLED_SYSTEM_WORKER_REQUIRED"
            )
        authority = self.session.scalar(
            select(AIVisualRerenderAuthority)
            .where(AIVisualRerenderAuthority.id == authority_id)
            .with_for_update()
        )
        if authority is None:
            raise ValidationFailureError("AI_VISUAL_RERENDER_AUTHORITY_MISSING")
        resolved = resolve_governed_ai_visual_rerender_execution_authority(
            self.session,
            workflow_run_id=authority.replacement_workflow_run_id,
            required=True,
        )
        if resolved is None or resolved.authority.id != authority.id:
            raise ValidationFailureError("AI_VISUAL_RERENDER_AUTHORITY_DRIFT")
        self._require_no_media_event(resolved.replacement_workflow.id)

        event = self.session.scalar(
            select(DomainEvent)
            .where(
                DomainEvent.workflow_run_id == resolved.replacement_workflow.id,
                DomainEvent.delivered_at.is_(None),
                DomainEvent.published_at.is_(None),
                DomainEvent.dead_lettered_at.is_(None),
            )
            .order_by(DomainEvent.created_at.asc(), DomainEvent.id.asc())
            .limit(1)
        )
        if event is None:
            self.session.commit()
            return AIVisualRerenderRunOnceResult(
                authority_id=authority.id,
                source_workflow_run_id=resolved.source_workflow.id,
                replacement_workflow_run_id=resolved.replacement_workflow.id,
                visual_production_run_id=resolved.visual_run.id,
                workflow_state=resolved.replacement_workflow.state,
                visual_run_state=resolved.visual_run.state,
                event_id=None,
                event_stage=None,
                worker_result=None,
            )
        stage = self._validate_exact_replacement_event(
            event,
            workflow=resolved.replacement_workflow,
        )
        event_id = event.id
        self.session.commit()
        worker_result = self._worker_factory().run_exact_event(event_id=event_id)
        self.session.expire_all()
        refreshed = self.session.get(
            ProductionWorkflowRun, resolved.replacement_workflow.id
        )
        refreshed_visual = self.session.get(
            AIVisualProductionRun, resolved.visual_run.id
        )
        if refreshed is None or refreshed_visual is None:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_POST_EVENT_LINEAGE_MISSING"
            )
        self._require_no_media_event(refreshed.id)
        return AIVisualRerenderRunOnceResult(
            authority_id=authority.id,
            source_workflow_run_id=resolved.source_workflow.id,
            replacement_workflow_run_id=refreshed.id,
            visual_production_run_id=refreshed_visual.id,
            workflow_state=refreshed.state,
            visual_run_state=refreshed_visual.state,
            event_id=event_id,
            event_stage=stage,
            worker_result=worker_result,
        )

    def _resolve_source_scope(self, candidate_id: uuid.UUID) -> _SourceScope:
        candidate = self.session.get(FinalReviewCandidate, candidate_id)
        if candidate is None:
            raise ValidationFailureError("AI_VISUAL_RERENDER_SOURCE_CANDIDATE_MISSING")
        source = self.session.get(ProductionWorkflowRun, candidate.workflow_run_id)
        final_media = self.session.get(FinalMediaRef, candidate.final_media_ref_id)
        project = self.session.get(VideoProject, candidate.video_project_id)
        if (
            source is None
            or final_media is None
            or project is None
            or source.id == candidate_id
            or source.state != "FINAL_REVIEW_READY"
            or source.current_stage != "FINALIZE"
            or source.completed_at is None
            or source.video_project_id != candidate.video_project_id
            or source.company_id != candidate.company_id
            or source.channel_workspace_id != candidate.channel_workspace_id
            or source.production_package_artifact_version_id
            != candidate.production_package_artifact_version_id
            or source.production_package_hash != candidate.production_package_hash
            or source.production_readiness_receipt_artifact_version_id
            != candidate.production_readiness_receipt_artifact_version_id
            or source.production_readiness_receipt_hash
            != candidate.production_readiness_receipt_hash
            or source.final_review_candidate_id != candidate.id
            or source.final_review_candidate_hash != candidate.candidate_hash
            or source.final_media_ref_id != final_media.id
            or source.final_media_ref_hash != candidate.final_media_hash
            or final_media.video_project_id != candidate.video_project_id
            or final_media.production_package_artifact_version_id
            != candidate.production_package_artifact_version_id
            or final_media.production_package_hash != candidate.production_package_hash
            or final_media.checksum_sha256 != candidate.final_media_hash
            or candidate.archive_verification_state != "VERIFIED"
            or source.ai_visual_production_run_id is not None
            or candidate.ai_visual_production_run_id is not None
            or candidate.ai_visual_asset_manifest_hash is not None
            or candidate.ffmpeg_effect_plan_hash is not None
            or candidate.supersedes_final_review_candidate_id is not None
            or not candidate.native_render_plan_ref
            or not candidate.native_render_plan_hash
            or not source.canonical_media_timeline_ref
            or not source.canonical_media_timeline_hash
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_SOURCE_LINEAGE_INVALID")

        package_version = self._artifact_version(
            candidate.production_package_artifact_version_id,
            expected_hash=candidate.production_package_hash,
            artifact_type="production_package",
            project_id=project.id,
        )
        readiness_version = self._artifact_version(
            candidate.production_readiness_receipt_artifact_version_id,
            expected_hash=candidate.production_readiness_receipt_hash,
            artifact_type="production_readiness_receipt",
            project_id=project.id,
        )
        timing_rows = list(
            self.session.scalars(
                select(V2NarrationTimingRecoveryAuthority).where(
                    V2NarrationTimingRecoveryAuthority.workflow_run_id == source.id
                )
            ).all()
        )
        if len(timing_rows) != 1:
            raise ValidationFailureError("AI_VISUAL_RERENDER_TIMING_AUTHORITY_INVALID")
        timing = timing_rows[0]
        receipt = self.session.scalar(
            select(V2NarrationTimingRecoveryReceipt).where(
                V2NarrationTimingRecoveryReceipt.authority_id == timing.id
            )
        )
        media_rows = list(
            self.session.scalars(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == source.id,
                    V2ProductionEffectLedger.stage == "MEDIA",
                )
            ).all()
        )
        if len(media_rows) != 1 or receipt is None:
            raise ValidationFailureError("AI_VISUAL_RERENDER_TIMING_RECEIPT_INVALID")
        ledger = media_rows[0]
        journal = dict(ledger.effect_journal or {})
        if (
            timing.video_project_id != project.id
            or timing.production_package_artifact_version_id != package_version.id
            or timing.production_package_hash != package_version.content_hash
            or timing.max_tts_retries != 0
            or timing.max_forced_alignment_submissions != 1
            or receipt.workflow_run_id != source.id
            or receipt.media_effect_ledger_id != ledger.id
            or receipt.recovery_state != "VERIFIED"
            or receipt.provider_call_count != 1
            or receipt.tts_retry_count != 0
            or receipt.canonical_media_timeline_hash
            != source.canonical_media_timeline_hash
            or ledger.id != timing.media_effect_ledger_id
            or ledger.state != "VERIFIED"
            or ledger.adapter_key != _SOURCE_MEDIA_ADAPTER
            or ledger.effect_invocation_count != 1
            or ledger.result_type != _SOURCE_MEDIA_RESULT_TYPE
            or ledger.result_ref != source.canonical_media_timeline_ref
            or ledger.result_hash != source.canonical_media_timeline_hash
            or journal.get("state") != "VERIFIED"
            or journal.get("timeline_hash") != source.canonical_media_timeline_hash
            or journal.get("timing_recovery_authority_id") != str(timing.id)
            or journal.get("timing_recovery_authority_hash") != timing.authority_hash
            or journal.get("provider_call_count") != 2
            or journal.get("tts_provider_call_count") != 1
            or journal.get("tts_retry_count") != 0
            or journal.get("forced_alignment_provider_call_count") != 1
            or journal.get("audio_relative_path") != timing.audio_relative_path
            or journal.get("audio_checksum") != timing.audio_checksum_sha256
            or journal.get("subtitle_qc_state") != "PASS"
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_TIMING_LINEAGE_DRIFT")

        script_version = self._artifact_version(
            timing.script_artifact_version_id,
            expected_hash=timing.script_content_hash,
            project_id=project.id,
        )
        narration_text = str(
            (script_version.content or {}).get("narration_text") or ""
        ).strip()
        if (
            not narration_text
            or hashlib.sha256(narration_text.encode()).hexdigest()
            != timing.approved_script_hash
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_SCRIPT_AUTHORITY_DRIFT")

        timed_words_ref = self._required_journal_text(journal, "timed_words_ref")
        caption_artifact_ref = self._required_journal_text(journal, "caption_ref")
        subtitle_qc_ref = self._required_journal_text(journal, "subtitle_qc_ref")
        timed_words_version = self._sidecar_artifact(
            journal,
            id_key="timed_words_artifact_version_id",
            hash_key="timed_words_hash",
            ref=timed_words_ref,
            artifact_type="v2_timed_words",
            project_id=project.id,
        )
        caption_version = self._sidecar_artifact(
            journal,
            id_key="caption_artifact_version_id",
            hash_key="caption_artifact_hash",
            ref=caption_artifact_ref,
            artifact_type="v2_caption_srt",
            project_id=project.id,
        )
        subtitle_qc_version = self._sidecar_artifact(
            journal,
            id_key="subtitle_qc_artifact_version_id",
            hash_key="subtitle_qc_hash",
            ref=subtitle_qc_ref,
            artifact_type="v2_subtitle_qc",
            project_id=project.id,
        )

        timeline_relative = self._required_journal_text(
            journal, "timeline_relative_path"
        )
        audio_relative = self._required_journal_text(journal, "audio_relative_path")
        caption_relative = self._required_journal_text(journal, "caption_relative_path")
        timeline_path = self._root_file(timeline_relative)
        audio_path = self._root_file(audio_relative)
        caption_path = self._root_file(caption_relative)
        try:
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_TIMELINE_FILE_INVALID"
            ) from exc
        if (
            not isinstance(timeline, dict)
            or content_hash(timeline) != source.canonical_media_timeline_hash
            or self._sha256_file(timeline_path) != journal.get("timeline_file_checksum")
            or timeline.get("timeline_ref") != source.canonical_media_timeline_ref
            or str(timeline.get("workflow_run_id")) != str(source.id)
            or str(timeline.get("video_project_id")) != str(project.id)
            or timeline.get("production_package_hash") != package_version.content_hash
            or timeline.get("audio_checksum") != timing.audio_checksum_sha256
            or int(timeline.get("duration_ms") or 0) != timing.audio_duration_ms
            or timeline.get("timed_words_ref") != timed_words_ref
            or timeline.get("caption_ref") != caption_artifact_ref
            or timeline.get("caption_artifact_hash") != caption_version.content_hash
            or timeline.get("caption_checksum") != journal.get("caption_checksum")
            or timeline.get("subtitle_qc_ref") != subtitle_qc_ref
            or timeline.get("subtitle_qc_hash") != subtitle_qc_version.content_hash
            or timeline.get("subtitle_qc_state") != "PASS"
            or self._sha256_file(audio_path) != timing.audio_checksum_sha256
            or self._sha256_file(caption_path) != journal.get("caption_checksum")
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_SOURCE_MEDIA_DRIFT")

        policy_snapshot = self.session.get(
            CompiledChannelPolicySnapshot, project.policy_snapshot_id
        )
        try:
            channel_policy = ChannelScopedPolicy.model_validate(
                (policy_snapshot.compiled_payload or {}).get("channel_scoped_policy")
                if policy_snapshot is not None
                else None
            )
        except ValidationError as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_CHANNEL_BUDGET_POLICY_INVALID"
            ) from exc
        if (
            policy_snapshot is None
            or policy_snapshot.channel_workspace_id != project.channel_workspace_id
            or policy_snapshot.id != project.policy_snapshot_id
            or policy_snapshot.status not in {"active", "approved"}
            or channel_policy.policy_status != "APPROVED"
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_CHANNEL_POLICY_AUTHORITY_DRIFT"
            )
        package = ProductionPackageService(self.session).validate_for_readiness(
            package_version.id
        )
        support_ref = package.support_envelope_ref
        support_version = self.session.get(
            ArtifactVersion,
            support_ref.artifact_version_id if support_ref is not None else None,
        )
        try:
            envelope = V2FrozenSupportEnvelope.model_validate(
                support_version.content if support_version is not None else None
            )
        except (ValidationError, ValueError) as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_SOURCE_BUDGET_AUTHORITY_INVALID"
            ) from exc
        source_media_budget = self.session.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.run_id == source.id
            )
        )
        frozen = envelope.zero_cost_budget
        evidence = frozen.reservation_evidence or {}
        capacity = evidence.get("capacity_evidence") or {}
        frozen_media_ceiling = Decimal(frozen.authorized_cost_usd)
        policy_cap = Decimal(
            str(channel_policy.budget_policy.max_estimated_cost_per_video)
        )
        if (
            support_ref is None
            or support_version is None
            or support_version.content_hash != support_ref.content_hash
            or envelope.execution_mode != "REAL_LONG_FORM_PRODUCTION"
            or source_media_budget is None
            or source_media_budget.run_id != source.id
            or source_media_budget.video_project_id != project.id
            or frozen.reservation_ref != source_media_budget.reservation_ref
            or evidence.get("reservation_id") != str(source_media_budget.id)
            or evidence.get("run_id") != str(source.id)
            or evidence.get("project_id") != str(project.id)
            or evidence.get("request_hash") != source_media_budget.request_hash
            or capacity.get("content_hash")
            != (source_media_budget.capacity_evidence_json or {}).get("content_hash")
            or frozen_media_ceiling <= 0
            or frozen_media_ceiling > policy_cap
            or Decimal(source_media_budget.reserved_amount) != frozen_media_ceiling
            or source_media_budget.status
            not in {
                "RESERVED",
                "SUBMITTED",
                "SETTLED_ACTUAL",
                "SETTLED_CONSERVATIVE",
            }
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_SOURCE_BUDGET_AUTHORITY_DRIFT"
            )
        combined_authorities = list(
            self.session.scalars(
                select(CombinedReplacementBudgetAuthority).where(
                    CombinedReplacementBudgetAuthority.video_project_id == project.id,
                    CombinedReplacementBudgetAuthority.budget_reservation_id
                    == source_media_budget.id,
                    CombinedReplacementBudgetAuthority.budget_reservation_ref
                    == source_media_budget.reservation_ref,
                    CombinedReplacementBudgetAuthority.support_envelope_hash
                    == support_version.content_hash,
                    CombinedReplacementBudgetAuthority.route_budget_authority_hash
                    == frozen.content_hash,
                )
            ).all()
        )
        if len(combined_authorities) != 1:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_COMBINED_BUDGET_AUTHORITY_REQUIRED"
            )
        combined_budget_authority = combined_authorities[0]
        if (
            combined_budget_authority.state != "FROZEN"
            or Decimal(
                combined_budget_authority.combined_replacement_projected_cost_usd
            )
            != frozen_media_ceiling
            or combined_budget_authority.content_hash
            != CombinedReplacementBudgetAuthorityService.provider_execution_binding(
                combined_budget_authority
            ).get("authority_hash")
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_COMBINED_BUDGET_AUTHORITY_DRIFT"
            )
        return _SourceScope(
            candidate=candidate,
            final_media=final_media,
            source_workflow=source,
            project=project,
            timing_authority=timing,
            timing_receipt=receipt,
            media_ledger=ledger,
            package_version=package_version,
            readiness_version=readiness_version,
            script_version=script_version,
            timed_words_version=timed_words_version,
            caption_version=caption_version,
            subtitle_qc_version=subtitle_qc_version,
            policy_snapshot=policy_snapshot,
            channel_policy=channel_policy,
            source_media_budget=source_media_budget,
            combined_budget_authority=combined_budget_authority,
            timeline=timeline,
            timeline_relative_path=timeline_relative,
            audio_relative_path=audio_relative,
            caption_relative_path=caption_relative,
            timed_words_ref=timed_words_ref,
            caption_artifact_ref=caption_artifact_ref,
            subtitle_qc_ref=subtitle_qc_ref,
        )

    def _require_governed_visual_route_authority(
        self,
        scope: _SourceScope,
    ) -> tuple[int, int, Decimal, int]:
        """Compile exact owner requirements without creating paid authority."""

        from app.services.v2_ai_visual_stage import (
            V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD,
            compile_ai_visual_stage_planning,
        )

        provisional_run_id = self._stable_id(
            "visual-run", scope.candidate.id, scope.candidate.candidate_hash
        )
        # Reuse the exact pre-TTS owner envelope.  A governed rerender cannot
        # infer a 14-image/zero-video budget from the script or a deployment
        # constant: the active source combined authority is the only cost
        # ceiling it may consume.
        preflight = dict(
            (scope.combined_budget_authority.source_refs or {}).get(
                "ai_visual_preflight"
            )
            or {}
        )
        try:
            maximum_image_submissions = int(
                preflight["unique_ai_image_asset_slot_count"]
            )
            maximum_video_submissions = int(
                preflight["unique_ai_video_asset_slot_count"]
            )
            maximum_scene_count = len(
                list(preflight["visual_plan_compilation"]["scenes"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_COMBINED_VISUAL_PARTITION_REQUIRED"
            ) from exc
        if (
            maximum_image_submissions < 0
            or maximum_video_submissions < 0
            or maximum_image_submissions + maximum_video_submissions <= 0
            or maximum_scene_count <= 0
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_COMBINED_VISUAL_PARTITION_REQUIRED"
            )
        try:
            planning = compile_ai_visual_stage_planning(
                visual_run=SimpleNamespace(
                    id=provisional_run_id,
                    video_project_id=scope.project.id,
                    production_package_artifact_version_id=scope.package_version.id,
                    source_timeline_hash=(
                        scope.source_workflow.canonical_media_timeline_hash
                    ),
                ),
                timeline=scope.timeline,
                provider_readiness_ref="preflight://google-gemini-image+google-veo",
                budget_authority_ref="preflight://no-paid-authority",
                maximum_image_submissions=maximum_image_submissions,
                maximum_video_submissions=maximum_video_submissions,
            )
        except ValidationFailureError as exc:
            if str(exc) == "V2_AI_VISUAL_VIDEO_DURATION_AUTHORITY_INSUFFICIENT":
                raise ValidationFailureError(
                    "AI_VISUAL_RERENDER_VIDEO_DURATION_AUTHORITY_INSUFFICIENT"
                ) from exc
            raise
        image_count = planning.scene_plan.unique_ai_image_asset_slot_count
        video_count = planning.scene_plan.unique_ai_video_asset_slot_count
        visual_cost = (
            V2_AI_VISUAL_CONSERVATIVE_UNIT_COST_USD * image_count
            + _current_veo_unit_cost_usd() * video_count
        )
        video_seconds = video_count * 8
        budget = scope.channel_policy.budget_policy
        if (
            video_count > int(budget.max_veo_clips_per_video)
            or video_seconds > Decimal(str(budget.max_veo_seconds_per_video))
            or _current_veo_unit_cost_usd() * video_count
            > Decimal(str(budget.max_veo_cost_per_video))
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_VIDEO_DURATION_AUTHORITY_INSUFFICIENT"
            )
        if (
            len(planning.scene_plan.scenes) > maximum_scene_count
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_VISUAL_ROUTE_AUTHORITY_INSUFFICIENT"
            )
        return image_count, video_count, visual_cost, len(planning.scene_plan.scenes)

    def _require_governed_combined_visual_partition(
        self,
        scope: _SourceScope,
        *,
        image_count: int,
        video_count: int,
    ) -> dict[str, Any]:
        """Return the existing aggregate's zero-occupancy visual partition."""

        try:
            return (
                CombinedReplacementBudgetAuthorityService.governed_rerender_visual_partition(
                    authority=scope.combined_budget_authority,
                    reservation=scope.source_media_budget,
                    production_visual_policy_ref=AI_VISUAL_POLICY_REF,
                    production_visual_policy_hash=active_ai_visual_policy_authority()[
                        "hash"
                    ],
                    image_owner_count=image_count,
                    video_owner_count=video_count,
                )
            )
        except ValidationFailureError as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_COMBINED_VISUAL_PARTITION_REQUIRED"
            ) from exc

    def _new_replacement_workflow(
        self,
        *,
        scope: _SourceScope,
        policy: Mapping[str, Any],
        authority_id: uuid.UUID,
        workflow_id: uuid.UUID,
        visual_run_id: uuid.UUID,
        created_at: datetime,
        actor: ActorContext,
    ) -> ProductionWorkflowRun:
        source = scope.source_workflow
        identity = {
            "schema_version": "vcos.ai-visual-rerender-workflow-start.v1",
            "authority_id": str(authority_id),
            "source_candidate_id": str(scope.candidate.id),
            "source_candidate_hash": scope.candidate.candidate_hash,
            "source_workflow_run_id": str(source.id),
            "replacement_workflow_run_id": str(workflow_id),
            "visual_production_run_id": str(visual_run_id),
            "production_package_artifact_version_id": str(scope.package_version.id),
            "production_package_hash": scope.package_version.content_hash,
            "production_visual_policy_ref": policy["ref"],
            "production_visual_policy_hash": policy["hash"],
            "automatic_publish": False,
        }
        return ProductionWorkflowRun(
            id=workflow_id,
            company_id=source.company_id,
            channel_workspace_id=source.channel_workspace_id,
            video_project_id=source.video_project_id,
            uploaded_video_id=None,
            production_lane=source.production_lane,
            planning_source_type=source.planning_source_type,
            planning_source_id=source.planning_source_id,
            planning_source_hash=source.planning_source_hash,
            workflow_key=semantic_hash({**identity, "identity": "workflow-key"}),
            start_input_hash=semantic_hash(identity),
            state="VISUAL_PENDING",
            current_stage="VISUAL",
            state_reason_codes=[_RERENDER_REASON],
            projection_version=1,
            project_admission_decision_id=source.project_admission_decision_id,
            project_admission_decision_hash=source.project_admission_decision_hash,
            production_package_artifact_version_id=scope.package_version.id,
            production_package_hash=scope.package_version.content_hash,
            production_readiness_receipt_artifact_version_id=(
                scope.readiness_version.id
            ),
            production_readiness_receipt_hash=scope.readiness_version.content_hash,
            canonical_media_timeline_ref=source.canonical_media_timeline_ref,
            canonical_media_timeline_hash=source.canonical_media_timeline_hash,
            destination_binding_id=source.destination_binding_id,
            destination_binding_fingerprint=source.destination_binding_fingerprint,
            destination_binding=dict(source.destination_binding or {}),
            started_at=created_at,
            last_progress_at=created_at,
            metadata_={
                "schema_version": "vcos.ai-visual-rerender-workflow.v1",
                "rerender_authority_id": str(authority_id),
                "source_workflow_run_id": str(source.id),
                "source_final_review_candidate_id": str(scope.candidate.id),
                "visual_production_run_id": str(visual_run_id),
                "requested_by_actor_type": actor.actor_type.value,
                "requested_by_actor_id": str(actor.actor_id),
                "controlled_recovery_final_boundary": "FINAL_REVIEW_READY",
                "post_render_hold_requested": False,
                "post_render_hold_reason": None,
                "max_attempts": 5,
                "automatic_publish": False,
                "new_tts_calls_authorized": 0,
                "new_forced_alignment_calls_authorized": 0,
            },
            created_at=created_at,
            updated_at=created_at,
        )

    def _replay_result(
        self, authority: AIVisualRerenderAuthority
    ) -> AIVisualRerenderAuthorizationResult:
        scope = self._resolve_source_scope(authority.rejected_final_review_candidate_id)
        resolved = resolve_governed_ai_visual_rerender_execution_authority(
            self.session,
            workflow_run_id=authority.replacement_workflow_run_id,
            required=True,
        )
        if (
            resolved is None
            or resolved.authority.id != authority.id
            or resolved.source_workflow.id != scope.source_workflow.id
            or resolved.visual_run.id != authority.authorized_visual_production_run_id
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_REPLAY_LINEAGE_DRIFT")
        event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == resolved.replacement_workflow.id,
                DomainEvent.command_id
                == command_id_for(
                    resolved.replacement_workflow.id,
                    ProductionWorkflowStage.VISUAL,
                ),
            )
        )
        if event is None:
            raise ValidationFailureError("AI_VISUAL_RERENDER_VISUAL_EVENT_MISSING")
        self._validate_exact_replacement_event(
            event, workflow=resolved.replacement_workflow, require_pending=False
        )
        self._require_no_media_event(resolved.replacement_workflow.id)
        return AIVisualRerenderAuthorizationResult(
            authority_id=authority.id,
            authority_hash=authority.authority_hash,
            source_workflow_run_id=resolved.source_workflow.id,
            replacement_workflow_run_id=resolved.replacement_workflow.id,
            visual_production_run_id=resolved.visual_run.id,
            budget_reservation_id=resolved.budget.id,
            budget_reservation_ref=resolved.budget.reservation_ref,
            visual_event_id=event.id,
            workflow_state=resolved.replacement_workflow.state,
            visual_run_state=resolved.visual_run.state,
            replayed=True,
        )

    def _validate_fresh_lineage(
        self,
        *,
        authority: AIVisualRerenderAuthority,
        visual_run: AIVisualProductionRun,
        replacement: ProductionWorkflowRun,
        visual_event: DomainEvent,
    ) -> None:
        if (
            authority.authority_hash
            != seal_ai_visual_rerender_authority_hash(authority)
            or authority.source_workflow_run_id == replacement.id
            or authority.replacement_workflow_run_id != replacement.id
            or authority.authorized_visual_production_run_id != visual_run.id
            or visual_run.workflow_run_id != replacement.id
            or replacement.ai_visual_production_run_id != visual_run.id
            or replacement.ai_visual_policy_ref != AI_VISUAL_POLICY_REF
            or authority.maximum_tts_submissions != 0
            or authority.maximum_forced_alignment_submissions != 0
            or authority.automatic_publish is not False
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_FRESH_LINEAGE_DRIFT")
        self._validate_exact_replacement_event(visual_event, workflow=replacement)
        self._require_no_media_event(replacement.id)

    def _validate_exact_replacement_event(
        self,
        event: DomainEvent,
        *,
        workflow: ProductionWorkflowRun,
        require_pending: bool = True,
    ) -> str:
        payload = dict(event.payload or {})
        metadata = dict(event.metadata_ or {})
        retry_policy = metadata.get("retry_policy")
        stage = str(payload.get("stage") or "")
        event_is_pending = event.delivered_at is None and event.published_at is None
        receipt = (
            None
            if event_is_pending
            else self.session.scalar(
                select(WorkflowCommandReceipt).where(
                    WorkflowCommandReceipt.domain_event_id == event.id
                )
            )
        )
        try:
            stage_enum = ProductionWorkflowStage(stage)
        except ValueError as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_EVENT_STAGE_INVALID"
            ) from exc
        if (
            stage not in _ALLOWED_REPLACEMENT_EVENT_STAGES
            or event.event_type != WORKFLOW_EVENT_TYPE
            or event.event_version != WORKFLOW_EVENT_VERSION
            or event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or event.aggregate_id != workflow.id
            or event.company_id != workflow.company_id
            or event.channel_workspace_id != workflow.channel_workspace_id
            or event.workflow_run_id != workflow.id
            or event.correlation_id != f"{WORKFLOW_CORRELATION_PREFIX}:{workflow.id}"
            or payload.get("workflow_run_id") != str(workflow.id)
            or payload.get("production_lane") != workflow.production_lane
            or payload.get("handler_key")
            != handler_key_for(ProductionLane(workflow.production_lane), stage_enum)
            or (
                event_is_pending
                and payload.get("input_hash")
                != ProductionWorkflowCoordinator(
                    self.session, now=self.now
                )._stage_input_hash(workflow, stage_enum)
            )
            or event.command_id != command_id_for(workflow.id, stage_enum)
            or event.payload_hash != semantic_hash(payload)
            or event.max_attempts != 5
            or not 0 <= event.attempt_count <= event.max_attempts
            or metadata.get("schema_version") != "production-workflow-stage-event.v1"
            or metadata.get("stage") != stage
            or metadata.get("production_lane") != workflow.production_lane
            or not isinstance(retry_policy, dict)
            or retry_policy.get("policy_key") != "production-workflow-bounded-v1"
            or retry_policy.get("automatic_retry_allowed") is not True
            or retry_policy.get("policy_authorized_local_repair") is not True
            or retry_policy.get("max_attempts") != 5
            or retry_policy.get("provider_substitution_allowed") is not False
            or (require_pending and event.delivered_at is not None)
            or (require_pending and event.published_at is not None)
            or event.dead_lettered_at is not None
            or (
                not event_is_pending
                and (
                    receipt is None
                    or receipt.workflow_run_id != workflow.id
                    or receipt.command_id != event.command_id
                    or receipt.stage != stage
                    or receipt.handler_key != payload.get("handler_key")
                    or receipt.input_hash != payload.get("input_hash")
                )
            )
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_EVENT_AUTHORITY_DRIFT")
        return stage

    def _require_no_media_event(self, workflow_id: uuid.UUID) -> None:
        events = self.session.scalars(
            select(DomainEvent).where(DomainEvent.workflow_run_id == workflow_id)
        ).all()
        if any(
            event.command_id
            == command_id_for(workflow_id, ProductionWorkflowStage.MEDIA)
            or str((event.payload or {}).get("stage") or "") == "MEDIA"
            for event in events
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_MEDIA_EVENT_FORBIDDEN")

    def _artifact_version(
        self,
        version_id: uuid.UUID,
        *,
        expected_hash: str,
        project_id: uuid.UUID,
        artifact_type: str | None = None,
    ) -> ArtifactVersion:
        version = self.session.get(ArtifactVersion, version_id)
        artifact = self.session.get(Artifact, version.artifact_id) if version else None
        expected_lifecycle = (
            ("draft", "submitted")
            if artifact_type == "production_package"
            else ("approved", "approved")
        )
        if (
            version is None
            or artifact is None
            or version.content_hash != expected_hash
            or artifact.video_project_id != project_id
            or (artifact_type is not None and artifact.artifact_type != artifact_type)
            or artifact.current_version_id != version.id
            or (artifact.status, version.status) != expected_lifecycle
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_ARTIFACT_AUTHORITY_DRIFT")
        return version

    def _sidecar_artifact(
        self,
        journal: Mapping[str, Any],
        *,
        id_key: str,
        hash_key: str,
        ref: str,
        artifact_type: str,
        project_id: uuid.UUID,
    ) -> ArtifactVersion:
        try:
            version_id = uuid.UUID(str(journal[id_key]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_SIDECAR_IDENTITY_MISSING"
            ) from exc
        expected_hash = str(journal.get(hash_key) or "")
        if ref != f"artifact-version://{version_id}" or not self._is_hash(
            expected_hash
        ):
            raise ValidationFailureError("AI_VISUAL_RERENDER_SIDECAR_REF_DRIFT")
        return self._artifact_version(
            version_id,
            expected_hash=expected_hash,
            project_id=project_id,
            artifact_type=artifact_type,
        )

    def _root_file(self, value: str) -> Path:
        raw = Path(value)
        if raw.is_absolute() or ".." in raw.parts or "~" in raw.parts or not raw.parts:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_ROOT_RELATIVE_REF_REQUIRED"
            )
        cursor = self.root
        for part in raw.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ValidationFailureError(
                    "AI_VISUAL_RERENDER_SOURCE_SYMLINK_FORBIDDEN"
                )
        try:
            resolved = cursor.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_SOURCE_FILE_INVALID"
            ) from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ValidationFailureError("AI_VISUAL_RERENDER_SOURCE_FILE_INVALID")
        return resolved

    def _lock_candidate_lineage(self, candidate_id: uuid.UUID) -> None:
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 7902))"),
            {"key": f"ai-visual-rerender:{candidate_id}"},
        )

    @staticmethod
    def _require_controlled_actor(actor: ActorContext) -> None:
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or actor.actor_id != _CONTROLLED_RECOVERY_ACTOR_ID
            or actor.operator_user_id is not None
            or not actor.has_permission("production.start")
        ):
            raise ValidationFailureError(
                "AI_VISUAL_RERENDER_CONTROLLED_SYSTEM_WORKER_REQUIRED"
            )

    @staticmethod
    def _stable_id(
        kind: str, candidate_id: uuid.UUID, candidate_hash: str
    ) -> uuid.UUID:
        return uuid.uuid5(
            _IDENTITY_NAMESPACE, f"{kind}:{candidate_id}:{candidate_hash}"
        )

    @staticmethod
    def _required_journal_text(value: Mapping[str, Any], key: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result.strip():
            raise ValidationFailureError(
                f"AI_VISUAL_RERENDER_MEDIA_JOURNAL_FIELD_REQUIRED:{key}"
            )
        return result

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_hash(value: str) -> bool:
        return len(value) == 64 and set(value) <= _SHA256


__all__ = [
    "AIVisualRerenderAuthorizationResult",
    "AIVisualRerenderRecoveryService",
    "AIVisualRerenderRunOnceResult",
]
