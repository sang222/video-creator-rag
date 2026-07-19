from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.d2p1 import (
    DailyToPackageReceiptContent,
    DailyToPackageRequest,
    DailyToPackageStatusRead,
)
from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.m12_2 import FirstScriptedVideoPackageRequest
from app.contracts.m5 import ProjectAdmissionDecisionCreate
from app.contracts.nich1 import (
    ChannelFitEvaluation,
    NicheAlignmentDossier,
    NicheContractDigest,
    NicheDossierScope,
    NicheGateResult,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate, ReviewTaskCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelDailyRun,
    ChannelProfileVersion,
    ChannelWorkspace,
    CloudMediaRef,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    ContextPackSnapshot,
    DailyIdeaDecision,
    EditorialCalendarSlot,
    EffectiveChannelRuntimeContextSnapshot,
    FinalMediaRef,
    FirstScriptedVideoPackage,
    HumanUploadTask,
    IdeaMarketPreflight,
    MediaRenderJob,
    MediaOffloadJob,
    PaidProviderCallLedger,
    ProjectAdmissionDecision,
    ProviderAttempt,
    ProviderJobSnapshot,
    R3D4GateRun,
    ReviewTask,
    UploadedVideo,
    VideoProject,
)
from app.services.config_registry import canonical_json, content_hash
from app.services.m12_2 import FirstScriptedVideoPackageService
from app.services.m5 import ProjectAdmissionService
from app.services.nich1 import (
    NicheAlignmentDossierBuilder,
    NicheContractDigestCompiler,
    channel_fit_threshold_from_compiled_policy,
)
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.workflow import ArtifactService, ReviewService


ORCHESTRATOR_VERSION = "d2p1.daily-to-package.v1"
PACKAGE_BUILDER_VERSION = "m12.2.authoritative-lineage.v1"
RECEIPT_SCHEMA_VERSION = "d2p1.daily-to-package-receipt.v1"
RECEIPT_ARTIFACT_TYPE = "idea_admission_lineage"
RESEARCH_ASSIGNMENT_REASON = "D2P1_RESEARCH_ASSIGNMENT"
FINAL_HUMAN_REVIEW_REASON = "D2P1_PACKAGE_REVIEW_REQUIRED"

CH1_FLEX_V2_CHANNEL_KEY = "small-team-ai"
CH1_FLEX_V2_POLICY_VERSION = "small-team-ai.channel-policy.v2"
CH1_FLEX_V2_APPROVAL_REF = (
    "operator-approval://ch1-flex-v2/"
    "small-team-ai/master-prompt-2026-07-19"
)
CH1_FLEX_V2_VISUAL_BINDING_SCHEMA = "ch1-flex.visual-source-policy-binding.v2"
CH1_MARKET_V3_POLICY_VERSION = "small-team-ai.channel-policy.v3"
CH1_MARKET_V3_APPROVAL_REF = (
    "operator-approval://ch1-market-v3/"
    "small-team-ai/master-prompt-2026-07-19"
)
COMPILED_POLICY_SCHEMA_VERSION = "m12.2p.channel_policy_snapshot.v1"
PRODUCTION_DOSSIER_BINDING_SCHEMA = "d2p1.production-niche-dossier-binding.v1"
NICHE_GATE_BINDING_SCHEMA = "d2p1.package-niche-gate-binding.v1"

NICHE_GATE_KEYS = (
    "topic_niche_alignment_gate",
    "script_niche_alignment_gate",
    "visual_niche_alignment_gate",
    "thumbnail_niche_alignment_gate",
    "metadata_niche_alignment_gate",
)

PROVIDER_EXECUTION_BOUNDARY_KEYS = (
    "provider_attempts",
    "provider_job_snapshots",
    "paid_provider_call_ledger",
)
MEDIA_EXECUTION_BOUNDARY_KEYS = (
    "media_render_jobs",
    "final_media_refs",
    "drive_media_offload_jobs",
    "drive_cloud_media_refs",
    "youtube_human_upload_tasks",
    "youtube_uploaded_videos",
)
EXECUTION_BOUNDARY_KEYS = (
    *PROVIDER_EXECUTION_BOUNDARY_KEYS,
    *MEDIA_EXECUTION_BOUNDARY_KEYS,
)


@dataclass(frozen=True)
class _Lineage:
    decision: DailyIdeaDecision
    daily_run: ChannelDailyRun
    slot: EditorialCalendarSlot
    context_pack: ContextPackSnapshot
    channel: ChannelWorkspace
    profile: ChannelProfileVersion
    policy_snapshot: CompiledChannelPolicySnapshot
    category: ContentCategory
    digest: dict[str, Any]
    digest_ref: dict[str, Any]
    preflight: IdeaMarketPreflight
    topic_gate: dict[str, Any]


@dataclass(frozen=True)
class _ResearchResolution:
    version: ArtifactVersion | None
    assignment: ReviewTask | None


class DailyToPackageOrchestrator:
    """Provider-free bridge from an admitted daily decision to M12.2.

    Creative inputs are resolved from immutable lineage.  The public request has
    no topic/category/policy fields by design, so callers cannot replace the
    admitted topic while moving the decision into production.
    """

    def __init__(
        self,
        session: Session,
        *,
        package_service: Any | None = None,
    ) -> None:
        self.session = session
        self.package_service = package_service or FirstScriptedVideoPackageService(session)

    def run(self, data: DailyToPackageRequest) -> DailyToPackageStatusRead:
        boundary_before = self._execution_boundary_counts()
        provider_before, media_before = self._execution_totals(boundary_before)
        decision = self.session.scalar(
            select(DailyIdeaDecision)
            .where(DailyIdeaDecision.id == data.daily_idea_decision_id)
            .with_for_update()
        )
        if decision is None:
            raise NotFoundError(f"daily idea decision not found: {data.daily_idea_decision_id}")

        try:
            lineage = self._resolve_lineage(decision)
        except ValidationFailureError as exc:
            return self._ephemeral_blocked(decision.id, str(exc))

        actor_id = data.created_by_user_id or lineage.slot.created_by_user_id
        if actor_id is None:
            return self._ephemeral_blocked(
                decision.id,
                "D2P1_ACTOR_REQUIRED",
                next_action="Provide created_by_user_id with project and artifact workflow rights.",
            )

        admission = self._resolve_or_admit(lineage=lineage, actor_id=actor_id)
        if admission.decision != "ADMIT" or admission.admitted_video_project_id is None:
            return self._ephemeral_blocked(
                decision.id,
                (
                    f"PROJECT_ADMISSION_NOT_ADMITTED:{admission.decision}:"
                    f"{','.join(admission.reason_codes or [])}"
                ),
                next_action="Resolve the M5 admission gate and create a new admissible decision version.",
            )
        project = self.session.get(VideoProject, admission.admitted_video_project_id)
        if project is None:
            raise NotFoundError(f"admitted project not found: {admission.admitted_video_project_id}")

        receipt_values = self._receipt_values(
            lineage=lineage,
            admission=admission,
            project=project,
        )
        try:
            self._freeze_project_lineage(
                project=project,
                lineage=lineage,
                receipt_values=receipt_values,
            )
            receipt_values["project_ref"] = self._project_ref(project)
            prior_receipt = self._current_receipt(project.id)
            if prior_receipt is None:
                _, receipt_version = self._persist_receipt(
                    project=project,
                    actor_id=actor_id,
                    values={
                        **receipt_values,
                        "state": "PROJECT_ADMITTED",
                        "last_successful_state": "PROJECT_ADMITTED",
                        "exact_next_action": "Compile or resolve the frozen Effective Context.",
                    },
                )
            else:
                self._validate_receipt_lineage(prior_receipt[1], receipt_values)
                promoted = self._promote_reviewed_handoff(
                    project=project,
                    actor_id=actor_id,
                    receipt_version=prior_receipt[1],
                )
                if promoted is not None:
                    return promoted
        except ValidationFailureError as exc:
            receipt_values["project_ref"] = self._project_ref(project)
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker=str(exc),
                last_successful_state="PROJECT_ADMITTED",
                next_action="Create a corrected immutable admission lineage; do not follow latest policy state.",
            )

        try:
            effective = EffectiveChannelRuntimeContextCompiler(self.session).ensure_for_project(
                project.id,
                editorial_calendar_slot_id=lineage.slot.id,
            )
            self._validate_effective_context(effective=effective, project=project, lineage=lineage)
        except (NotFoundError, ValidationFailureError) as exc:
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker=str(exc),
                last_successful_state="PROJECT_ADMITTED",
                next_action="Create a new corrected policy/digest/project version; do not mutate frozen lineage.",
            )

        receipt_values["effective_context_ref"] = self._effective_ref(effective)
        prior_receipt = self._current_receipt(project.id)
        if prior_receipt is None or self._receipt_progress_rank(prior_receipt[1]) < 2:
            _, receipt_version = self._persist_receipt(
                project=project,
                actor_id=actor_id,
                values={
                    **receipt_values,
                    "state": "EFFECTIVE_CONTEXT_READY",
                    "last_successful_state": "EFFECTIVE_CONTEXT_READY",
                    "exact_next_action": "Resolve an exact approved ResearchPack version.",
                },
            )

        try:
            research = self._resolve_research(
                project=project,
                lineage=lineage,
                actor_id=actor_id,
                explicit_version_id=data.approved_research_artifact_version_id,
            )
        except ValidationFailureError as exc:
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker=str(exc),
                last_successful_state="EFFECTIVE_CONTEXT_READY",
                next_action="Approve an exact current ResearchPack/SourcePack version bound to this project.",
            )
        if research.assignment is not None:
            receipt_values["research_assignment_ref"] = self._research_assignment_ref(research.assignment)
        if research.version is None:
            _, receipt_version = self._persist_receipt(
                project=project,
                actor_id=actor_id,
                values={
                    **receipt_values,
                    "state": "AWAITING_RESEARCH",
                    "last_successful_state": "EFFECTIVE_CONTEXT_READY",
                    "human_review_state": "NOT_READY",
                    "exact_next_action": (
                        "Approve the exact current ResearchPack or SourcePack ArtifactVersion bound to this project, "
                        "then rerun D2P1 with the same DailyIdeaDecision ID."
                    ),
                },
            )
            return self._status_from_receipt(receipt_version)

        receipt_values["research_pack_ref"] = self._artifact_version_ref(research.version)
        fingerprint = self._idempotency_fingerprint(
            lineage=lineage,
            admission=admission,
            project=project,
            research_version=research.version,
        )
        receipt_values["idempotency_fingerprint"] = fingerprint

        current_progress = self._current_receipt(project.id)
        same_fingerprint = bool(
            current_progress is not None
            and (current_progress[1].content or {}).get("idempotency_fingerprint") == fingerprint
        )
        if not same_fingerprint or self._receipt_progress_rank(current_progress[1]) < 3:
            _, receipt_version = self._persist_receipt(
                project=project,
                actor_id=actor_id,
                values={
                    **receipt_values,
                    "state": "RESEARCH_READY",
                    "last_successful_state": "RESEARCH_READY",
                    "exact_next_action": "Build the scripted package from frozen lineage and approved research.",
                },
            )
        current_progress = self._current_receipt(project.id)
        same_fingerprint = bool(
            current_progress is not None
            and (current_progress[1].content or {}).get("idempotency_fingerprint") == fingerprint
        )
        if not same_fingerprint or self._receipt_progress_rank(current_progress[1]) < 4:
            _, receipt_version = self._persist_receipt(
                project=project,
                actor_id=actor_id,
                values={
                    **receipt_values,
                    "state": "PACKAGE_BUILDING",
                    "last_successful_state": "PACKAGE_BUILDING",
                    "exact_next_action": "Run M12.2 with no_media=true and validate every mandatory niche gate.",
                },
            )

        package = self._find_package(project_id=project.id, fingerprint=fingerprint)
        if package is None:
            try:
                package = self._build_package(
                    project=project,
                    lineage=lineage,
                    effective=effective,
                    research_version=research.version,
                    fingerprint=fingerprint,
                )
            except Exception as exc:
                if not self.session.is_active:
                    raise
                self._accumulate_execution_delta(
                    values=receipt_values,
                    provider_before=provider_before,
                    media_before=media_before,
                )
                if self._has_execution_violation(receipt_values):
                    return self._persisted_blocked(
                        project=project,
                        actor_id=actor_id,
                        values=receipt_values,
                        blocker="D2P1_FORBIDDEN_PROVIDER_OR_MEDIA_EXECUTION",
                        last_successful_state="PACKAGE_BUILDING",
                        next_action="Investigate the execution boundary; D2P1 must finish before provider/media work.",
                    )
                return self._persisted_technical_failure(
                    project=project,
                    actor_id=actor_id,
                    values=receipt_values,
                    blocker=f"PACKAGE_BUILD_FAILED:{type(exc).__name__}",
                )

        lineage_core = {
            "schema_version": "d2p1.authoritative-package-lineage.v1",
            "idempotency_fingerprint": fingerprint,
            "daily_idea_decision_ref": receipt_values["daily_idea_decision_ref"],
            "project_ref": receipt_values["project_ref"],
            "effective_context_ref": receipt_values["effective_context_ref"],
            "niche_contract_digest_ref": receipt_values["niche_contract_digest_ref"],
            "editorial_slot_ref": receipt_values["editorial_slot_ref"],
            "research_pack_ref": receipt_values["research_pack_ref"],
            "no_media": True,
            "human_review_only": True,
        }
        existing_lineage = (package.artifacts or {}).get("d2p1_authoritative_lineage")
        if existing_lineage is None:
            lineage_payload = {
                **lineage_core,
                "zero_execution_boundary": self._execution_boundary_proof(
                    boundary_before
                ),
            }
        else:
            lineage_payload = _dict(existing_lineage)
        if (
            any(lineage_payload.get(key) != value for key, value in lineage_core.items())
            or not self._valid_execution_boundary_proof(
                _dict(lineage_payload.get("zero_execution_boundary"))
            )
        ):
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker="SCRIPTED_PACKAGE_LINEAGE_MISMATCH",
                last_successful_state="PACKAGE_BUILDING",
                next_action="Create a new explicit package version bound to the current frozen lineage.",
            )
        package.artifacts = {
            **(package.artifacts or {}),
            "d2p1_authoritative_lineage": lineage_payload,
        }
        self.session.flush()
        receipt_values["scripted_package_ref"] = self._package_ref(package)

        self._accumulate_execution_delta(
            values=receipt_values,
            provider_before=provider_before,
            media_before=media_before,
        )
        if self._has_execution_violation(receipt_values):
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker="D2P1_FORBIDDEN_PROVIDER_OR_MEDIA_EXECUTION",
                last_successful_state="PACKAGE_BUILDING",
                next_action="Investigate the execution boundary; D2P1 must finish before provider/media work.",
            )

        try:
            gate_refs = self._resolve_niche_gates(lineage=lineage, package=package)
        except ValidationFailureError as exc:
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker=str(exc),
                last_successful_state="PACKAGE_BUILDING",
                next_action="Repair and persist all five typed, hash-bound niche gate results for this exact package lineage.",
            )
        self.session.flush()
        receipt_values["niche_gate_refs"] = gate_refs
        receipt_values["scripted_package_ref"] = self._package_ref(package)
        missing_or_failed = [
            key
            for key in NICHE_GATE_KEYS
            if not any(
                ref.get("gate_key") == key and _gate_status(ref) == "PASS"
                for ref in gate_refs
            )
        ]
        if missing_or_failed:
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker=f"MANDATORY_NICHE_GATE_NOT_PASS:{','.join(missing_or_failed)}",
                last_successful_state="PACKAGE_BUILDING",
                next_action="Repair the failed semantic niche gate and create a new explicit artifact/package version.",
            )
        if package.package_status != "READY_FOR_HUMAN_REVIEW":
            return self._persisted_blocked(
                project=project,
                actor_id=actor_id,
                values=receipt_values,
                blocker=f"SCRIPTED_PACKAGE_NOT_READY:{package.package_status}",
                last_successful_state="PACKAGE_BUILDING",
                next_action=package.next_action or "Resolve M12.2 package blockers and rerun D2P1.",
            )

        _, receipt_version = self._persist_receipt(
            project=project,
            actor_id=actor_id,
            values={
                **receipt_values,
                "state": "PACKAGE_READY_FOR_HUMAN_REVIEW",
                "last_successful_state": "PACKAGE_READY_FOR_HUMAN_REVIEW",
                "human_review_state": "PENDING",
                "blockers": [],
                "exact_next_action": "Complete the explicit final human review; do not auto-render or auto-publish.",
            },
        )
        self._ensure_final_human_review(
            project=project,
            receipt_version=receipt_version,
            actor_id=actor_id,
        )
        return self._status_from_receipt(receipt_version)

    def status(self, daily_idea_decision_id: uuid.UUID) -> DailyToPackageStatusRead:
        """Read the durable handoff state without compiling, calling, or creating."""

        decision = self.session.get(DailyIdeaDecision, daily_idea_decision_id)
        if decision is None:
            raise NotFoundError(f"daily idea decision not found: {daily_idea_decision_id}")
        admission = self.session.scalars(
            select(ProjectAdmissionDecision)
            .where(ProjectAdmissionDecision.daily_idea_decision_id == decision.id)
            .order_by(ProjectAdmissionDecision.created_at.asc())
        ).first()
        project = (
            self.session.get(VideoProject, admission.admitted_video_project_id)
            if admission is not None and admission.admitted_video_project_id is not None
            else None
        )
        if project is not None:
            receipt = self._current_receipt(project.id)
            if receipt is not None and admission is not None and admission.decision == "ADMIT":
                return self._status_from_receipt(receipt[1])

        if admission is not None and admission.decision != "ADMIT":
            blocker = (
                f"PROJECT_ADMISSION_NOT_ADMITTED:{admission.decision}:"
                f"{','.join(admission.reason_codes or [])}"
            )
            return DailyToPackageStatusRead(
                daily_idea_decision_id=decision.id,
                current_state="BLOCKED_POLICY",
                project=self._project_ref(project) if project is not None else None,
                research={"state": "NOT_RESOLVED"},
                package=None,
                niche_gates={},
                blockers=[blocker],
                exact_next_action=(
                    "Resolve the frozen M5 admission blockers and create a new "
                    "DailyIdeaDecision/admission version; do not mutate this receipt."
                ),
                human_review_state="BLOCKED",
                provider_calls_made=0,
                media_calls_made=0,
            )

        if admission is None:
            try:
                lineage = self._resolve_lineage(decision)
            except ValidationFailureError as exc:
                return self._ephemeral_blocked(
                    decision.id,
                    str(exc),
                    next_action=(
                        "Repair the frozen D2P1 entry authority by creating an explicit "
                        "new version; the read-only status endpoint will not mutate it."
                    ),
                )
            if lineage.slot.created_by_user_id is None:
                return self._ephemeral_blocked(
                    decision.id,
                    "D2P1_ACTOR_REQUIRED",
                    next_action=(
                        "Provide created_by_user_id with project and artifact workflow "
                        "rights when running D2P1."
                    ),
                )

        effective = (
            self.session.get(EffectiveChannelRuntimeContextSnapshot, project.effective_context_snapshot_id)
            if project is not None and project.effective_context_snapshot_id is not None
            else None
        )
        state = "PROJECT_ADMITTED" if project is not None else (
            "DAILY_DECISION_ACCEPTED"
            if decision.decision_status in {"PROPOSED", "ADMITTED"}
            else "BLOCKED_POLICY"
        )
        blockers = [] if state != "BLOCKED_POLICY" else [f"DAILY_DECISION_NOT_ADMITTED:{decision.decision_status}"]
        return DailyToPackageStatusRead(
            daily_idea_decision_id=decision.id,
            current_state=state,
            project=self._project_ref(project) if project is not None else None,
            effective_context=self._effective_ref(effective) if effective is not None else None,
            research={"state": "NOT_RESOLVED"},
            package=None,
            niche_gates={},
            blockers=blockers,
            exact_next_action=(
                "Run D2P1 with this admitted DailyIdeaDecision ID."
                if not blockers
                else "Create a new admitted DailyIdeaDecision version after policy gates pass."
            ),
            human_review_state="NOT_READY",
            provider_calls_made=0,
            media_calls_made=0,
        )

    def _resolve_lineage(self, decision: DailyIdeaDecision) -> _Lineage:
        # PROPOSED is the normal output of DailyIdeaAgent.  D2P1 owns the call
        # into M5 project admission, which is what transitions the decision to
        # ADMITTED after preflight and Effective Context pass.  Requiring
        # ADMITTED here would create a circular precondition and bypass the
        # bridge this orchestrator exists to provide.
        if decision.decision_status not in {"PROPOSED", "ADMITTED"}:
            raise ValidationFailureError(
                f"DAILY_DECISION_NOT_ADMISSIBLE:{decision.decision_status}"
            )
        daily_run = self.session.get(ChannelDailyRun, decision.channel_daily_run_id)
        if daily_run is None:
            raise ValidationFailureError("DAILY_RUN_MISSING")
        if daily_run.daily_idea_decision_id not in (None, decision.id):
            raise ValidationFailureError("DAILY_RUN_DECISION_LINEAGE_MISMATCH")
        if daily_run.editorial_calendar_slot_id is None:
            raise ValidationFailureError("EDITORIAL_SLOT_MISSING")
        slot = self.session.get(EditorialCalendarSlot, daily_run.editorial_calendar_slot_id)
        if slot is None:
            raise ValidationFailureError("EDITORIAL_SLOT_MISSING")
        missing_slot_fields = [
            key
            for key, value in {
                "category_id": slot.category_id,
                "content_pillar": slot.content_pillar,
                "series_key": slot.series_key,
                "production_goal": slot.production_goal,
            }.items()
            if value is None or (isinstance(value, str) and not value.strip())
        ]
        if missing_slot_fields:
            raise ValidationFailureError(f"EDITORIAL_SLOT_STRICT_FIELDS_MISSING:{','.join(missing_slot_fields)}")

        category = self.session.get(ContentCategory, slot.category_id)
        if category is None:
            raise ValidationFailureError("CONTENT_CATEGORY_MISSING")
        if category.channel_workspace_id != decision.channel_workspace_id or category.company_id != decision.company_id:
            raise ValidationFailureError("CONTENT_CATEGORY_SCOPE_MISMATCH")
        if category.content_pillar and category.content_pillar != slot.content_pillar:
            raise ValidationFailureError("EDITORIAL_PILLAR_CATEGORY_MISMATCH")

        channel = self.session.get(ChannelWorkspace, decision.channel_workspace_id)
        policy_snapshot = self.session.get(CompiledChannelPolicySnapshot, decision.policy_snapshot_id)
        if channel is None or policy_snapshot is None:
            raise ValidationFailureError("ACTIVE_CHANNEL_POLICY_MISSING")
        existing_admission = self.session.scalars(
            select(ProjectAdmissionDecision)
            .where(ProjectAdmissionDecision.daily_idea_decision_id == decision.id)
            .where(ProjectAdmissionDecision.decision == "ADMIT")
            .order_by(ProjectAdmissionDecision.created_at.asc())
        ).first()
        admitted_project = (
            self.session.get(
                VideoProject, existing_admission.admitted_video_project_id
            )
            if existing_admission is not None
            and existing_admission.admitted_video_project_id is not None
            else None
        )
        historical_resume = admitted_project is not None
        if channel.company_id != decision.company_id or policy_snapshot.channel_workspace_id != channel.id:
            raise ValidationFailureError("STALE_OR_WRONG_CHANNEL_POLICY_SNAPSHOT")
        if historical_resume:
            if (
                admitted_project.company_id != decision.company_id
                or admitted_project.channel_workspace_id != channel.id
                or admitted_project.policy_snapshot_id != policy_snapshot.id
                or admitted_project.channel_profile_version_id
                != policy_snapshot.channel_profile_version_id
                or policy_snapshot.status not in {"active", "approved"}
            ):
                raise ValidationFailureError(
                    "ADMITTED_PROJECT_POLICY_LINEAGE_MISMATCH"
                )
        elif (
            channel.active_policy_snapshot_id != policy_snapshot.id
            or policy_snapshot.status != "active"
        ):
            raise ValidationFailureError("STALE_OR_WRONG_CHANNEL_POLICY_SNAPSHOT")
        profile = self.session.get(ChannelProfileVersion, policy_snapshot.channel_profile_version_id)
        if profile is None or profile.channel_workspace_id != channel.id:
            raise ValidationFailureError("CHANNEL_PROFILE_SCOPE_MISMATCH")
        self._validate_exact_ch1_flex_v2_policy(
            channel=channel,
            profile=profile,
            policy_snapshot=policy_snapshot,
            historical_resume=historical_resume,
        )

        context_pack = self.session.get(ContextPackSnapshot, decision.context_pack_snapshot_id)
        if context_pack is None:
            raise ValidationFailureError("DAILY_CONTEXT_PACK_MISSING")
        if (
            context_pack.channel_workspace_id != channel.id
            or context_pack.company_id != decision.company_id
            or context_pack.channel_profile_version_id != profile.id
            or context_pack.policy_snapshot_id != policy_snapshot.id
            or context_pack.editorial_calendar_slot_id != slot.id
        ):
            raise ValidationFailureError("DAILY_CONTEXT_PACK_LINEAGE_MISMATCH")
        digest, digest_ref = self._resolve_digest(context_pack)
        self._validate_digest(
            digest=digest,
            digest_ref=digest_ref,
            channel=channel,
            profile=profile,
            policy_snapshot=policy_snapshot,
            slot=slot,
            category=category,
            historical_resume=historical_resume,
        )

        preflight = (
            self.session.get(
                IdeaMarketPreflight,
                existing_admission.idea_market_preflight_id,
            )
            if existing_admission is not None
            and existing_admission.idea_market_preflight_id is not None
            else self.session.scalars(
                select(IdeaMarketPreflight)
                .where(IdeaMarketPreflight.daily_idea_decision_id == decision.id)
                .order_by(IdeaMarketPreflight.created_at.asc())
            ).first()
        )
        if preflight is None:
            raise ValidationFailureError("IDEA_MARKET_PREFLIGHT_MISSING")
        if (
            preflight.channel_daily_run_id != daily_run.id
            or preflight.daily_idea_decision_id != decision.id
            or preflight.channel_workspace_id != channel.id
            or preflight.company_id != decision.company_id
        ):
            raise ValidationFailureError("IDEA_MARKET_PREFLIGHT_SCOPE_MISMATCH")
        if preflight.decision != "PASS" or preflight.policy_fit_state != "PASS":
            raise ValidationFailureError(f"CHANNEL_FIT_NOT_PASS:{preflight.decision}")
        if preflight.channel_fit_score is None:
            raise ValidationFailureError("CHANNEL_FIT_SCORE_MISSING")
        evidence = preflight.evidence_blob or {}
        channel_fit_evaluation = _dict(evidence.get("channel_fit_evaluation"))
        channel_fit_gate = _dict(evidence.get("channel_fit_gate"))
        channel_fit_result = _first_nonempty(
            evidence.get("channel_fit_result"),
            channel_fit_evaluation.get("channel_fit_result"),
            channel_fit_gate.get("channel_fit_result"),
            channel_fit_gate.get("result"),
            channel_fit_gate.get("status"),
        )
        if str(channel_fit_result).upper() != "PASS":
            raise ValidationFailureError("CHANNEL_FIT_RESULT_NOT_PASS")
        topic_gate = self._gate_from_value(evidence, "topic_niche_alignment_gate")
        if topic_gate is None:
            topic_gate = self._gate_from_value(decision.rationale or {}, "topic_niche_alignment_gate")
        if topic_gate is None or _gate_status(topic_gate) != "PASS":
            raise ValidationFailureError("TOPIC_NICHE_ALIGNMENT_GATE_NOT_PASS")
        self._validate_preflight_gate_bindings(
            decision=decision,
            preflight=preflight,
            topic_gate=topic_gate,
            channel_fit_gate=channel_fit_gate or channel_fit_evaluation,
            digest_ref=digest_ref,
            policy_snapshot=policy_snapshot,
        )

        return _Lineage(
            decision=decision,
            daily_run=daily_run,
            slot=slot,
            context_pack=context_pack,
            channel=channel,
            profile=profile,
            policy_snapshot=policy_snapshot,
            category=category,
            digest=digest,
            digest_ref=digest_ref,
            preflight=preflight,
            topic_gate={
                **topic_gate,
                "gate_key": "topic_niche_alignment_gate",
            },
        )

    @staticmethod
    def _validate_exact_ch1_flex_v2_policy(
        *,
        channel: ChannelWorkspace,
        profile: ChannelProfileVersion,
        policy_snapshot: CompiledChannelPolicySnapshot,
        historical_resume: bool,
    ) -> None:
        """Accept only the exact activated CH1-FLEX v2 contract for this bridge."""

        payload = policy_snapshot.compiled_payload or {}
        scoped = _dict(payload.get("channel_scoped_policy"))
        compiled_schema = _dict(payload.get("compiled_policy_snapshot_json")).get(
            "schema_version"
        )
        visual_binding = _dict(scoped.get("visual_source_policy_binding"))
        compiled_gate_policy = _dict(payload.get("gate_policy"))
        threshold_authority = _dict(
            compiled_gate_policy.get("channel_fit_threshold_authority")
        )
        provider_policy_ref = _dict(
            _dict(payload.get("snapshot_refs")).get("provider_usage_policy")
        )
        provider_ref_value = provider_policy_ref.get("ref")
        expected_threshold_ref = (
            str(provider_ref_value) + "#pexels.semantic_fit_threshold"
            if provider_ref_value
            else None
        )
        try:
            typed_scoped = ChannelScopedPolicy.model_validate(scoped)
            channel_fit_threshold = channel_fit_threshold_from_compiled_policy(
                policy_snapshot
            )
        except Exception:
            typed_scoped = None
            channel_fit_threshold = None
        common_exact = (
            typed_scoped is not None
            and typed_scoped.visual_source_policy_binding is not None
            and typed_scoped.provider_usage_policy.google_gemini_image is not None
            and channel_fit_threshold
            == typed_scoped.provider_usage_policy.pexels.semantic_fit_threshold
            and threshold_authority.get("ref")
            == expected_threshold_ref
            and threshold_authority.get("version") == provider_policy_ref.get("version")
            and threshold_authority.get("content_hash")
            == provider_policy_ref.get("content_hash")
            and threshold_authority.get("derivation")
            == "REUSE_APPROVED_SEMANTIC_FIT_THRESHOLD"
            and channel.key == CH1_FLEX_V2_CHANNEL_KEY
            and profile.status
            in ({"active", "approved"} if historical_resume else {"active"})
            and scoped.get("channel_key") == CH1_FLEX_V2_CHANNEL_KEY
            and scoped.get("policy_status") == "APPROVED"
            and compiled_schema == COMPILED_POLICY_SCHEMA_VERSION
            and visual_binding.get("schema_version")
            == CH1_FLEX_V2_VISUAL_BINDING_SCHEMA
            and visual_binding.get("niche_visual_source_profile")
            == "STOCK_ASSISTED"
            and visual_binding.get("one_source_decision_per_scene") is True
            and visual_binding.get("auto_pexels_to_ai_failover") is False
        )
        exact_v2 = (
            profile.version == 2
            and scoped.get("policy_version") == CH1_FLEX_V2_POLICY_VERSION
            and scoped.get("approval_ref") == CH1_FLEX_V2_APPROVAL_REF
        )
        exact_v3 = (
            profile.version == 3
            and scoped.get("policy_version") == CH1_MARKET_V3_POLICY_VERSION
            and scoped.get("approval_ref") == CH1_MARKET_V3_APPROVAL_REF
            and typed_scoped is not None
            and typed_scoped.target_market_profile is not None
            and typed_scoped.target_market_digest is not None
            and typed_scoped.market_alignment_policy is not None
            and typed_scoped.destination_binding_policy is not None
            and typed_scoped.market_package_freeze_policy is not None
            and typed_scoped.publish_timing_localization_policy is not None
            and typed_scoped.geo_evaluation_policy is not None
            and typed_scoped.target_market_profile.primary_market == "US"
            and typed_scoped.target_market_profile.primary_locale == "en-US"
            and typed_scoped.destination_binding_policy.destination.manual_publish_required
            is True
        )
        exact = common_exact and (exact_v2 or exact_v3)
        if not exact:
            raise ValidationFailureError(
                "CH1_FLEX_V2_EXACT_POLICY_SCHEMA_REQUIRED"
            )

    @staticmethod
    def _validate_preflight_gate_bindings(
        *,
        decision: DailyIdeaDecision,
        preflight: IdeaMarketPreflight,
        topic_gate: dict[str, Any],
        channel_fit_gate: dict[str, Any],
        digest_ref: dict[str, Any],
        policy_snapshot: CompiledChannelPolicySnapshot,
    ) -> None:
        if topic_gate.get("niche_contract_digest_hash") != digest_ref.get("content_hash"):
            raise ValidationFailureError("TOPIC_NICHE_GATE_DIGEST_HASH_MISMATCH")
        if topic_gate.get("checked_policy_snapshot_hash") != policy_snapshot.content_hash:
            raise ValidationFailureError("TOPIC_NICHE_GATE_POLICY_HASH_MISMATCH")
        if not _ref_matches(topic_gate.get("subject_ref"), decision.id):
            raise ValidationFailureError("TOPIC_NICHE_GATE_SUBJECT_MISMATCH")
        raw_preflight_evidence_refs = _dict(preflight.evidence_blob).get(
            "evidence_refs"
        )
        preflight_evidence_refs = (
            [item for item in raw_preflight_evidence_refs if isinstance(item, dict)]
            if isinstance(raw_preflight_evidence_refs, list)
            else []
        )
        if (
            decision.decision_status == "PROPOSED"
            and topic_gate.get("subject_hash")
            != DailyToPackageOrchestrator._decision_subject_ref(
                decision,
                evidence_refs=preflight_evidence_refs,
            ).get("content_hash")
        ):
            raise ValidationFailureError("TOPIC_NICHE_GATE_SUBJECT_HASH_MISMATCH")
        topic_hash = topic_gate.get("content_hash")
        if not isinstance(topic_hash, str) or topic_hash != content_hash(
            {key: value for key, value in topic_gate.items() if key != "content_hash"}
        ):
            raise ValidationFailureError("TOPIC_NICHE_GATE_CONTENT_HASH_MISMATCH")

        fit_hash = channel_fit_gate.get("content_hash")
        if not isinstance(fit_hash, str) or fit_hash != content_hash(
            {key: value for key, value in channel_fit_gate.items() if key != "content_hash"}
        ):
            raise ValidationFailureError("CHANNEL_FIT_GATE_CONTENT_HASH_MISMATCH")
        try:
            persisted_score = float(preflight.channel_fit_score)
            gate_score = float(channel_fit_gate["channel_fit_score"])
            gate_threshold = float(channel_fit_gate["channel_fit_threshold"])
        except (KeyError, TypeError, ValueError):
            raise ValidationFailureError("CHANNEL_FIT_GATE_SCORE_OR_THRESHOLD_MISSING") from None
        try:
            expected_threshold = channel_fit_threshold_from_compiled_policy(
                policy_snapshot
            )
        except Exception:
            raise ValidationFailureError(
                "CHANNEL_FIT_GATE_THRESHOLD_AUTHORITY_MISSING"
            ) from None
        if gate_threshold != expected_threshold:
            raise ValidationFailureError("CHANNEL_FIT_GATE_THRESHOLD_MISMATCH")
        if persisted_score != gate_score or gate_score < gate_threshold:
            raise ValidationFailureError("CHANNEL_FIT_GATE_SCORE_MISMATCH_OR_BELOW_THRESHOLD")
        gate_hashes = _dict(channel_fit_gate.get("gate_result_hashes"))
        if gate_hashes.get("topic_niche_alignment_gate") != topic_hash:
            raise ValidationFailureError("CHANNEL_FIT_GATE_TOPIC_EVIDENCE_HASH_MISMATCH")

    def _resolve_digest(self, context_pack: ContextPackSnapshot) -> tuple[dict[str, Any], dict[str, Any]]:
        pack_content = context_pack.pack_content or {}
        raw = pack_content.get("niche_contract_digest")
        if not isinstance(raw, dict):
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_MISSING")
        digest = raw
        for key in ("content", "digest", "digest_content"):
            if isinstance(raw.get(key), dict):
                digest = raw[key]
                break
        claimed_hash = digest.get("content_hash") or raw.get("content_hash")
        if not isinstance(claimed_hash, str) or len(claimed_hash) != 64:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_HASH_MISSING")
        computed = content_hash({key: value for key, value in digest.items() if key != "content_hash"})
        if claimed_hash != computed:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_HASH_MISMATCH")
        separate_ref = (
            pack_content.get("niche_contract_digest_ref")
            if isinstance(pack_content.get("niche_contract_digest_ref"), dict)
            else None
        )
        raw_ref = raw.get("ref") if isinstance(raw.get("ref"), dict) else None
        if separate_ref is not None and separate_ref.get("content_hash") != claimed_hash:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_REF_HASH_MISMATCH")
        digest_ref = {
            "type": "niche_contract_digest",
            "ref": (
                separate_ref.get("ref")
                if separate_ref is not None
                else raw_ref.get("ref")
                if raw_ref is not None
                else raw.get("ref") if isinstance(raw.get("ref"), str)
                else f"context-pack://{context_pack.id}#niche_contract_digest"
            ),
            "content_hash": claimed_hash,
        }
        return digest, digest_ref

    @staticmethod
    def _validate_digest(
        *,
        digest: dict[str, Any],
        digest_ref: dict[str, Any],
        channel: ChannelWorkspace,
        profile: ChannelProfileVersion,
        policy_snapshot: CompiledChannelPolicySnapshot,
        slot: EditorialCalendarSlot,
        category: ContentCategory,
        historical_resume: bool = False,
    ) -> None:
        required_nonempty_fields = (
            "primary_niche",
            "positioning",
            "brand_promise",
            "target_audience",
            "content_pillars",
            "content_pillar_key",
            "series_key",
            "production_goal",
            "visual_source_profile",
        )
        missing = [key for key in required_nonempty_fields if digest.get(key) in (None, "", [])]
        missing.extend(key for key in ("allowed_topics", "forbidden_topics") if key not in digest)
        if missing:
            raise ValidationFailureError(f"NICHE_CONTRACT_DIGEST_FIELDS_MISSING:{','.join(missing)}")
        checks = (
            ("channel", digest.get("channel_id"), channel.id),
            ("profile", digest.get("channel_profile_version_ref"), profile.id),
            ("snapshot", digest.get("compiled_policy_snapshot_ref"), policy_snapshot.id),
            ("category", digest.get("category_id"), category.id),
        )
        for label, actual, expected in checks:
            if not _ref_matches(actual, expected):
                raise ValidationFailureError(f"NICHE_CONTRACT_DIGEST_{label.upper()}_MISMATCH")
        digest_snapshot_hash = _first_nonempty(
            digest.get("compiled_policy_snapshot_hash"),
            digest.get("compiled_policy_snapshot_content_hash"),
        )
        if digest_snapshot_hash != policy_snapshot.content_hash:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_POLICY_HASH_MISMATCH")
        if digest.get("channel_profile_version_hash") != profile.profile_input_hash:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_PROFILE_HASH_MISMATCH")
        channel_contract = (policy_snapshot.compiled_payload or {}).get("channel_contract_json")
        expected_contract_hash = content_hash(channel_contract) if isinstance(channel_contract, dict) else None
        if not expected_contract_hash or digest.get("channel_contract_hash") != expected_contract_hash:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_CHANNEL_CONTRACT_HASH_MISMATCH")
        if digest.get("category_hash") != category.content_hash:
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_CATEGORY_HASH_MISMATCH")
        if not _ref_matches(digest.get("editorial_slot_id"), slot.id) or not _ref_matches(
            digest.get("editorial_slot_ref"), slot.id
        ):
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_EDITORIAL_SLOT_MISMATCH")
        if str(digest.get("content_pillar_key")) != str(slot.content_pillar):
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_PILLAR_MISMATCH")
        if str(digest.get("series_key")) != str(slot.series_key):
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_SERIES_MISMATCH")
        if str(digest.get("production_goal")) != str(slot.production_goal):
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_GOAL_MISMATCH")
        if digest_ref.get("content_hash") != digest.get("content_hash"):
            raise ValidationFailureError("NICHE_CONTRACT_DIGEST_REF_HASH_MISMATCH")
        try:
            provided_digest = NicheContractDigest.model_validate(digest)
            authoritative_digest = NicheContractDigestCompiler().compile(
                channel=channel,
                profile_version=profile,
                policy_snapshot=policy_snapshot,
                category=category,
                editorial_slot=slot,
                allow_historical_approved=historical_resume,
            )
        except Exception as exc:
            raise ValidationFailureError(
                "NICHE_CONTRACT_DIGEST_AUTHORITY_RECOMPILE_FAILED"
            ) from exc
        if provided_digest.model_dump(mode="json") != authoritative_digest.model_dump(
            mode="json"
        ):
            raise ValidationFailureError(
                "NICHE_CONTRACT_DIGEST_AUTHORITY_CONTENT_MISMATCH"
            )

    def _resolve_or_admit(self, *, lineage: _Lineage, actor_id: uuid.UUID) -> ProjectAdmissionDecision:
        existing = self.session.scalars(
            select(ProjectAdmissionDecision)
            .where(ProjectAdmissionDecision.daily_idea_decision_id == lineage.decision.id)
            .where(ProjectAdmissionDecision.decision == "ADMIT")
            .order_by(ProjectAdmissionDecision.created_at.asc())
        ).first()
        if existing is not None:
            return existing
        return ProjectAdmissionService(self.session).create_decision(
            data=ProjectAdmissionDecisionCreate(
                channel_daily_run_id=lineage.daily_run.id,
                daily_idea_decision_id=lineage.decision.id,
                idea_market_preflight_id=lineage.preflight.id,
                category_id=lineage.category.id,
                created_by_user_id=actor_id,
            ),
            correlation_id=f"d2p1-admission-{lineage.decision.id}",
        )

    def _freeze_project_lineage(
        self,
        *,
        project: VideoProject,
        lineage: _Lineage,
        receipt_values: dict[str, Any],
    ) -> None:
        if (
            project.company_id != lineage.decision.company_id
            or project.channel_workspace_id != lineage.channel.id
            or project.policy_snapshot_id != lineage.policy_snapshot.id
            or project.channel_profile_version_id != lineage.profile.id
            or project.category_id != lineage.category.id
        ):
            raise ValidationFailureError("ADMITTED_PROJECT_FROZEN_SCOPE_MISMATCH")
        frozen = {
            "schema_version": "d2p1.project-frozen-lineage.v1",
            "channel_id": str(lineage.channel.id),
            "channel_profile_version_ref": {
                "type": "channel_profile_version",
                "id": str(lineage.profile.id),
                "version": lineage.profile.version,
                "content_hash": lineage.profile.profile_input_hash,
            },
            "compiled_policy_snapshot_ref": {
                "type": "compiled_policy_snapshot",
                "id": str(lineage.policy_snapshot.id),
                "content_hash": lineage.policy_snapshot.content_hash,
            },
            "channel_contract_ref": lineage.digest.get("channel_contract_ref"),
            "channel_contract_hash": lineage.digest.get("channel_contract_hash"),
            "niche_contract_digest_ref": receipt_values["niche_contract_digest_ref"],
            "daily_idea_decision_ref": receipt_values["daily_idea_decision_ref"],
            "editorial_slot_ref": receipt_values["editorial_slot_ref"],
            "category_id": str(lineage.category.id),
            "content_pillar": lineage.slot.content_pillar,
            "series_key": lineage.slot.series_key,
            "production_goal": lineage.slot.production_goal,
            "topic": lineage.decision.proposed_title,
            "angle": lineage.decision.proposed_angle,
        }
        m5_frozen = _dict(
            (project.audience_delivery_summary or {}).get("niche_governance")
        )
        m5_decision_ref = _dict(m5_frozen.get("daily_idea_decision_ref"))
        if (
            not m5_frozen
            or m5_decision_ref.get("id")
            != receipt_values["daily_idea_decision_ref"].get("id")
            or m5_decision_ref.get("content_hash")
            != receipt_values["daily_idea_decision_ref"].get("content_hash")
            or _dict(m5_frozen.get("niche_contract_digest_ref")).get(
                "content_hash"
            )
            != lineage.digest_ref.get("content_hash")
            or m5_frozen.get("compiled_policy_snapshot_hash")
            != lineage.policy_snapshot.content_hash
        ):
            raise ValidationFailureError("M5_FROZEN_NICHE_LINEAGE_MISMATCH")
        existing = (project.audience_delivery_summary or {}).get("d2p1_frozen_lineage")
        if existing not in (None, frozen):
            raise ValidationFailureError("ADMITTED_PROJECT_FROZEN_LINEAGE_MISMATCH")
        if existing is None:
            project.audience_delivery_summary = {
                **(project.audience_delivery_summary or {}),
                "d2p1_frozen_lineage": frozen,
            }
            self.session.flush()

    @staticmethod
    def _validate_effective_context(
        *,
        effective: EffectiveChannelRuntimeContextSnapshot,
        project: VideoProject,
        lineage: _Lineage,
    ) -> None:
        if effective.compile_status != "PASS":
            raise ValidationFailureError(f"EFFECTIVE_CONTEXT_NOT_PASS:{effective.compile_status}")
        if (
            effective.video_project_id != project.id
            or effective.company_id != project.company_id
            or effective.channel_workspace_id != project.channel_workspace_id
            or effective.channel_profile_version_id != lineage.profile.id
            or effective.compiled_policy_snapshot_id != lineage.policy_snapshot.id
            or effective.content_category_id != lineage.category.id
        ):
            raise ValidationFailureError("EFFECTIVE_CONTEXT_FROZEN_SCOPE_MISMATCH")
        digest_contract_hash = lineage.digest.get("channel_contract_hash")
        if digest_contract_hash and effective.channel_contract_hash != digest_contract_hash:
            raise ValidationFailureError("EFFECTIVE_CONTEXT_NICHE_CONTRACT_MISMATCH")

    def _resolve_research(
        self,
        *,
        project: VideoProject,
        lineage: _Lineage,
        actor_id: uuid.UUID,
        explicit_version_id: uuid.UUID | None,
    ) -> _ResearchResolution:
        candidates: list[ArtifactVersion] = []
        prior_assignment = self.session.scalars(
            select(ReviewTask)
            .where(ReviewTask.video_project_id == project.id)
            .where(ReviewTask.review_type == "evidence")
            .where(ReviewTask.review_reason_codes.contains([RESEARCH_ASSIGNMENT_REASON]))
            .order_by(ReviewTask.created_at.asc())
        ).first()
        if explicit_version_id is not None:
            version = self.session.get(ArtifactVersion, explicit_version_id)
            if version is None:
                raise ValidationFailureError("APPROVED_RESEARCH_VERSION_MISSING")
            artifact = self.session.get(Artifact, version.artifact_id)
            if artifact is None or artifact.video_project_id != project.id:
                raise ValidationFailureError("RESEARCH_PROJECT_LINEAGE_MISMATCH")
            if artifact.current_version_id != version.id:
                raise ValidationFailureError("RESEARCH_VERSION_IS_STALE")
            if artifact.artifact_type not in {"research_pack", "source_pack"}:
                raise ValidationFailureError("RESEARCH_ARTIFACT_TYPE_INVALID")
            candidates.append(version)
        else:
            artifacts = self.session.scalars(
                select(Artifact)
                .where(Artifact.video_project_id == project.id)
                .where(Artifact.artifact_type.in_(["research_pack", "source_pack"]))
                .order_by(Artifact.created_at.asc())
            ).all()
            preferred = sorted(artifacts, key=lambda item: item.artifact_type != "research_pack")
            candidates.extend(
                version
                for artifact in preferred
                if artifact.current_version_id is not None
                and (version := self.session.get(ArtifactVersion, artifact.current_version_id)) is not None
            )

        approved_but_invalid: ValidationFailureError | None = None
        for version in candidates:
            if self._is_exact_approved(version):
                try:
                    self._validate_research_content_bindings(
                        version=version,
                        project=project,
                        lineage=lineage,
                    )
                except ValidationFailureError as exc:
                    approved_but_invalid = exc
                    if explicit_version_id is not None:
                        raise
                    continue
                return _ResearchResolution(version=version, assignment=prior_assignment)
        if explicit_version_id is not None:
            raise ValidationFailureError("RESEARCH_VERSION_NOT_APPROVED")
        if approved_but_invalid is not None:
            raise approved_but_invalid

        target = candidates[0] if candidates else None
        if target is None:
            raise ValidationFailureError("M5_RESEARCH_ARTIFACT_MISSING")
        assignment = self.session.scalars(
            select(ReviewTask)
            .where(ReviewTask.video_project_id == project.id)
            .where(ReviewTask.target_artifact_version_id == target.id)
            .where(ReviewTask.review_type == "evidence")
            .where(ReviewTask.review_reason_codes.contains([RESEARCH_ASSIGNMENT_REASON]))
            .order_by(ReviewTask.created_at.asc())
        ).first()
        if assignment is None:
            assignment = ReviewService(self.session).create_review_task(
                data=ReviewTaskCreate(
                    video_project_id=project.id,
                    target_type="artifact_version",
                    target_id=target.id,
                    target_artifact_version_id=target.id,
                    review_type="evidence",
                    requested_by_user_id=actor_id,
                    review_reason_codes=[RESEARCH_ASSIGNMENT_REASON],
                    evidence_required=True,
                    evidence_refs=[
                        self._lineage_decision_ref(lineage),
                        self._project_ref(project),
                        lineage.digest_ref,
                        self._slot_ref(lineage.slot),
                    ],
                    review_scope=(
                        f"topic={lineage.decision.proposed_title}; category={lineage.category.id}; "
                        f"pillar={lineage.slot.content_pillar}; series={lineage.slot.series_key}"
                    ),
                    context_pack_ref=f"context-pack://{lineage.context_pack.id}#{lineage.context_pack.pack_hash}",
                ),
                correlation_id=f"d2p1-research-assignment-{lineage.decision.id}",
            )
        return _ResearchResolution(version=None, assignment=assignment)

    def _is_exact_approved(self, version: ArtifactVersion) -> bool:
        artifact = self.session.get(Artifact, version.artifact_id)
        if artifact is None or artifact.current_version_id != version.id:
            return False
        approval = self.session.scalars(
            select(ApprovalDecision)
            .where(ApprovalDecision.target_type == "artifact_version")
            .where(ApprovalDecision.target_id == version.id)
            .where(ApprovalDecision.target_artifact_version_id == version.id)
            .where(ApprovalDecision.decision == "approved")
            .order_by(ApprovalDecision.created_at.desc())
        ).first()
        return approval is not None

    def _validate_research_content_bindings(
        self,
        *,
        version: ArtifactVersion,
        project: VideoProject,
        lineage: _Lineage,
    ) -> None:
        """Require approved research truth to name every frozen authority key."""

        content = _dict(version.content)
        project_binding = _dict(content.get("video_project_ref"))
        expected_project_ref = self._project_ref(project)
        exact_checks = {
            "content_hash": version.content_hash == content_hash(content),
            "decision": _ref_matches(
                content.get("daily_idea_decision_id"), lineage.decision.id
            ),
            "topic": content.get("topic") == lineage.decision.proposed_title,
            "category": _ref_matches(content.get("category_id"), lineage.category.id),
            "pillar": str(content.get("content_pillar_key") or "")
            == str(lineage.slot.content_pillar),
            "digest": content.get("niche_contract_digest_hash")
            == lineage.digest_ref.get("content_hash"),
            "project": _ref_matches(content.get("video_project_id"), project.id),
            "project_ref_id": _ref_matches(project_binding.get("id"), project.id),
            "project_ref_hash": project_binding.get("content_hash")
            == expected_project_ref.get("content_hash"),
        }
        failed = [key for key, passed in exact_checks.items() if not passed]
        if failed:
            raise ValidationFailureError(
                f"RESEARCH_CONTENT_FROZEN_LINEAGE_MISMATCH:{','.join(failed)}"
            )

    def _build_package(
        self,
        *,
        project: VideoProject,
        lineage: _Lineage,
        effective: EffectiveChannelRuntimeContextSnapshot,
        research_version: ArtifactVersion,
        fingerprint: str,
    ) -> FirstScriptedVideoPackage:
        request = FirstScriptedVideoPackageRequest(
            channel_id=lineage.channel.id,
            topic=lineage.decision.proposed_title,
            research_pack_text=canonical_json(research_version.content or {}),
            research_pack_ref=f"artifact-version://{research_version.id}#{research_version.content_hash}",
            video_project_id=project.id,
            no_media=True,
            human_review_only=True,
        )
        result = self.package_service.create(request)
        package_id = _uuid_or_none(result.get("id")) if isinstance(result, dict) else _uuid_or_none(getattr(result, "id", None))
        package = self.session.get(FirstScriptedVideoPackage, package_id) if package_id else None
        if package is None:
            raise ValidationFailureError("M12_2_PACKAGE_NOT_PERSISTED")
        if (
            package.video_project_id != project.id
            or package.channel_id != lineage.channel.id
            or package.channel_profile_version_id != lineage.profile.id
            or package.compiled_policy_snapshot_id != lineage.policy_snapshot.id
            or package.effective_context_snapshot_id != effective.id
            or package.effective_context_hash != effective.context_hash
        ):
            raise ValidationFailureError("M12_2_PACKAGE_AUTHORITATIVE_LINEAGE_MISMATCH")
        return package

    def _resolve_niche_gates(
        self,
        *,
        lineage: _Lineage,
        package: FirstScriptedVideoPackage,
    ) -> list[dict[str, Any]]:
        package_lineage = _dict(
            (package.artifacts or {}).get("d2p1_authoritative_lineage")
        )
        package_lineage_hash = content_hash(_json_dict(package_lineage))
        results = [
            self._validate_typed_niche_gate_result(
                raw=lineage.topic_gate,
                gate_key="topic_niche_alignment_gate",
                lineage=lineage,
            )
        ]
        for gate_key in NICHE_GATE_KEYS[1:]:
            embedded = self._gate_from_value(package.artifacts or {}, gate_key)
            if embedded is None:
                continue
            result = self._validate_typed_niche_gate_result(
                raw=embedded,
                gate_key=gate_key,
                lineage=lineage,
            )
            persisted = self.session.scalars(
                select(R3D4GateRun)
                .where(R3D4GateRun.package_id == package.id)
                .where(R3D4GateRun.gate_key == gate_key)
                .order_by(R3D4GateRun.created_at.desc())
            ).first()
            if persisted is not None:
                measured_source_hash = _dict(persisted.measurements_json).get(
                    "content_hash"
                )
                if (
                    _gate_status({"status": persisted.status})
                    != result.verdict.value
                    or measured_source_hash not in (None, result.content_hash)
                ):
                    raise ValidationFailureError(
                        f"R3D4_NICHE_GATE_ATTESTATION_MISMATCH:{gate_key}"
                    )
            results.append(result)

        resolved_keys = {result.gate_key.value for result in results}
        missing_keys = [key for key in NICHE_GATE_KEYS if key not in resolved_keys]
        if missing_keys:
            raise ValidationFailureError(
                f"MANDATORY_TYPED_NICHE_GATE_RESULT_MISSING:{','.join(missing_keys)}"
            )

        try:
            digest = NicheContractDigest.model_validate(lineage.digest)
            preflight_evidence = _dict(lineage.preflight.evidence_blob)
            fit_raw = _dict(preflight_evidence.get("channel_fit_gate")) or _dict(
                preflight_evidence.get("channel_fit_evaluation")
            )
            channel_fit = ChannelFitEvaluation.model_validate(fit_raw)
            dossier = NicheAlignmentDossierBuilder().build(
                digest=digest,
                digest_ref=str(lineage.digest_ref["ref"]),
                gate_results=results,
                channel_fit=channel_fit,
                dossier_scope=NicheDossierScope.PRODUCTION_PACKAGE,
                human_review_requirements=(
                    "Complete the explicit D2P1 final human package review.",
                ),
            )
        except Exception as exc:
            raise ValidationFailureError(
                "PRODUCTION_NICHE_DOSSIER_TYPED_BINDING_INVALID"
            ) from exc
        self._persist_production_niche_dossier(
            package=package,
            lineage=lineage,
            dossier=dossier,
            package_lineage_hash=package_lineage_hash,
        )
        refs = [
            self._package_gate_binding_ref(
                package=package,
                lineage=lineage,
                dossier=dossier,
                result=result,
                package_lineage_hash=package_lineage_hash,
            )
            for result in results
        ]
        self._validate_package_gate_binding_refs(
            refs=refs,
            package=package,
            lineage=lineage,
            dossier=dossier,
            package_lineage_hash=package_lineage_hash,
        )
        return refs

    @staticmethod
    def _validate_typed_niche_gate_result(
        *,
        raw: dict[str, Any],
        gate_key: str,
        lineage: _Lineage,
    ) -> NicheGateResult:
        try:
            result = NicheGateResult.model_validate(raw)
        except Exception as exc:
            raise ValidationFailureError(
                f"NICHE_GATE_TYPED_HASH_BOUND_RESULT_REQUIRED:{gate_key}"
            ) from exc
        if result.gate_key.value != gate_key:
            raise ValidationFailureError(f"NICHE_GATE_KEY_MISMATCH:{gate_key}")
        if result.niche_contract_digest_hash != lineage.digest_ref.get(
            "content_hash"
        ):
            raise ValidationFailureError(
                f"NICHE_GATE_DIGEST_BINDING_MISMATCH:{gate_key}"
            )
        if result.checked_policy_snapshot_hash != lineage.policy_snapshot.content_hash:
            raise ValidationFailureError(
                f"NICHE_GATE_POLICY_BINDING_MISMATCH:{gate_key}"
            )
        return result

    @staticmethod
    def _package_gate_binding_ref(
        *,
        package: FirstScriptedVideoPackage,
        lineage: _Lineage,
        dossier: NicheAlignmentDossier,
        result: NicheGateResult,
        package_lineage_hash: str,
    ) -> dict[str, Any]:
        stable = {
            "schema_version": NICHE_GATE_BINDING_SCHEMA,
            "type": "package_niche_gate_binding",
            "gate_key": result.gate_key.value,
            "status": result.verdict.value,
            "source_gate_result_hash": result.content_hash,
            "source_subject_ref": result.subject_ref,
            "source_subject_hash": result.subject_hash,
            "niche_contract_digest_hash": lineage.digest_ref["content_hash"],
            "checked_policy_snapshot_hash": lineage.policy_snapshot.content_hash,
            "package_id": str(package.id),
            "package_lineage_hash": package_lineage_hash,
            "production_dossier_hash": dossier.content_hash,
        }
        return {**stable, "content_hash": content_hash(_json_dict(stable))}

    @staticmethod
    def _persist_production_niche_dossier(
        *,
        package: FirstScriptedVideoPackage,
        lineage: _Lineage,
        dossier: NicheAlignmentDossier,
        package_lineage_hash: str,
    ) -> None:
        dossier_json = dossier.model_dump(mode="json")
        binding_stable = {
            "schema_version": PRODUCTION_DOSSIER_BINDING_SCHEMA,
            "type": "production_niche_alignment_dossier",
            "ref": f"first-scripted-video-package://{package.id}#niche_alignment_dossier",
            "package_id": str(package.id),
            "package_lineage_hash": package_lineage_hash,
            "niche_contract_digest_hash": lineage.digest_ref["content_hash"],
            "checked_policy_snapshot_hash": lineage.policy_snapshot.content_hash,
            "dossier_scope": dossier.dossier_scope.value,
            "dossier_content_hash": dossier.content_hash,
            "gate_result_hashes": {
                result.gate_key.value: result.content_hash
                for result in (
                    dossier.topic_result,
                    dossier.script_result,
                    dossier.visual_result,
                    dossier.thumbnail_result,
                    dossier.metadata_result,
                )
                if result is not None
            },
        }
        binding = {
            **binding_stable,
            "content_hash": content_hash(_json_dict(binding_stable)),
        }
        artifacts = package.artifacts or {}
        existing_dossier = artifacts.get("niche_alignment_dossier")
        existing_binding = artifacts.get("d2p1_niche_alignment_dossier_binding")
        existing_is_production = (
            isinstance(existing_dossier, dict)
            and existing_dossier.get("dossier_scope") == "PRODUCTION_PACKAGE"
        )
        if (existing_is_production and existing_dossier != dossier_json) or (
            existing_binding not in (None, binding)
        ):
            raise ValidationFailureError(
                "PRODUCTION_NICHE_DOSSIER_LINEAGE_MISMATCH"
            )
        package.artifacts = {
            **artifacts,
            "niche_alignment_dossier": dossier_json,
            "d2p1_niche_alignment_dossier_binding": binding,
        }

    @staticmethod
    def _validate_package_gate_binding_refs(
        *,
        refs: list[dict[str, Any]],
        package: FirstScriptedVideoPackage,
        lineage: _Lineage,
        dossier: NicheAlignmentDossier,
        package_lineage_hash: str,
    ) -> None:
        dossier_results = {
            result.gate_key.value: result
            for result in (
                dossier.topic_result,
                dossier.script_result,
                dossier.visual_result,
                dossier.thumbnail_result,
                dossier.metadata_result,
            )
            if result is not None
        }
        if set(dossier_results) != set(NICHE_GATE_KEYS) or {
            str(ref.get("gate_key") or "") for ref in refs
        } != set(NICHE_GATE_KEYS):
            raise ValidationFailureError(
                "PACKAGE_NICHE_GATE_BINDING_SET_INCOMPLETE"
            )
        for ref in refs:
            claimed_hash = ref.get("content_hash")
            if claimed_hash != content_hash(
                _json_dict({key: value for key, value in ref.items() if key != "content_hash"})
            ):
                raise ValidationFailureError("PACKAGE_NICHE_GATE_BINDING_HASH_MISMATCH")
            gate_key = str(ref.get("gate_key") or "")
            dossier_result = dossier_results.get(gate_key)
            if (
                dossier_result is None
                or ref.get("schema_version") != NICHE_GATE_BINDING_SCHEMA
                or ref.get("package_id") != str(package.id)
                or ref.get("package_lineage_hash") != package_lineage_hash
                or ref.get("niche_contract_digest_hash")
                != lineage.digest_ref.get("content_hash")
                or ref.get("checked_policy_snapshot_hash")
                != lineage.policy_snapshot.content_hash
                or ref.get("production_dossier_hash") != dossier.content_hash
                or ref.get("source_gate_result_hash")
                != dossier_result.content_hash
                or ref.get("source_subject_ref") != dossier_result.subject_ref
                or ref.get("source_subject_hash") != dossier_result.subject_hash
                or ref.get("status") != dossier_result.verdict.value
            ):
                raise ValidationFailureError(
                    f"PACKAGE_NICHE_GATE_LINEAGE_MISMATCH:{gate_key}"
                )

    def _find_package(self, *, project_id: uuid.UUID, fingerprint: str) -> FirstScriptedVideoPackage | None:
        packages = self.session.scalars(
            select(FirstScriptedVideoPackage)
            .where(FirstScriptedVideoPackage.video_project_id == project_id)
            .order_by(FirstScriptedVideoPackage.created_at.asc())
        ).all()
        for package in packages:
            lineage = (package.artifacts or {}).get("d2p1_authoritative_lineage")
            if isinstance(lineage, dict) and lineage.get("idempotency_fingerprint") == fingerprint:
                return package
        return None

    def _receipt_values(
        self,
        *,
        lineage: _Lineage,
        admission: ProjectAdmissionDecision,
        project: VideoProject,
    ) -> dict[str, Any]:
        current = self._current_receipt(project.id)
        current_content = current[1].content or {} if current is not None else {}
        return {
            "orchestrator_version": ORCHESTRATOR_VERSION,
            "daily_idea_decision_ref": self._lineage_decision_ref(lineage),
            "project_ref": self._project_ref(project),
            "admission_receipt_ref": self._admission_ref(admission),
            "effective_context_ref": None,
            "niche_contract_digest_ref": lineage.digest_ref,
            "editorial_slot_ref": self._slot_ref(lineage.slot),
            "research_assignment_ref": None,
            "research_pack_ref": None,
            "scripted_package_ref": None,
            "package_human_review_ref": current_content.get("package_human_review_ref"),
            "niche_gate_refs": [],
            "human_review_state": "NOT_READY",
            # These are lineage-cumulative counters.  A later rerun is never
            # allowed to replace a prior non-zero proof with a fresh zero.
            "provider_calls_made": int(
                current_content.get("provider_calls_made") or 0
            ),
            "media_calls_made": int(current_content.get("media_calls_made") or 0),
            "idempotency_fingerprint": None,
            "blockers": [],
        }

    def _persist_receipt(
        self,
        *,
        project: VideoProject,
        actor_id: uuid.UUID,
        values: dict[str, Any],
    ) -> tuple[Artifact, ArtifactVersion]:
        current = self._current_receipt(project.id)
        monotonic_values = dict(values)
        if current is not None:
            previous = current[1].content or {}
            monotonic_values["provider_calls_made"] = max(
                int(previous.get("provider_calls_made") or 0),
                int(monotonic_values.get("provider_calls_made") or 0),
            )
            monotonic_values["media_calls_made"] = max(
                int(previous.get("media_calls_made") or 0),
                int(monotonic_values.get("media_calls_made") or 0),
            )
        payload = DailyToPackageReceiptContent.model_validate(
            {"schema_version": RECEIPT_SCHEMA_VERSION, **monotonic_values}
        ).model_dump(mode="json")
        artifact_service = ArtifactService(self.session)
        if current is None:
            artifact = artifact_service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project.id,
                    artifact_type=RECEIPT_ARTIFACT_TYPE,
                    created_by_user_id=actor_id,
                ),
                correlation_id=f"d2p1-receipt-{project.id}",
            )
            current_version = None
        else:
            artifact, current_version = current
            if current_version.content == payload:
                return artifact, current_version
        version = artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=current_version.id if current_version is not None else None,
                content=payload,
                status=(
                    "approved"
                    if payload["state"] in {"PACKAGE_HUMAN_REVIEW_PASSED", "READY_FOR_LONG_PRODUCTION"}
                    else "submitted"
                    if payload["state"] == "PACKAGE_READY_FOR_HUMAN_REVIEW"
                    else "draft"
                ),
                created_by_user_id=actor_id,
                evidence_refs=[
                    payload["daily_idea_decision_ref"],
                    payload["niche_contract_digest_ref"],
                    *payload["niche_gate_refs"],
                ],
                context_refs=[
                    ref
                    for ref in (
                        payload["effective_context_ref"],
                        payload["editorial_slot_ref"],
                    )
                    if isinstance(ref, dict)
                ],
            ),
            correlation_id=f"d2p1-receipt-version-{project.id}",
        )
        if payload["state"] == "PACKAGE_READY_FOR_HUMAN_REVIEW":
            artifact.status = "in_review"
            self.session.flush()
        elif payload["state"] == "READY_FOR_LONG_PRODUCTION":
            artifact.status = "approved"
            self.session.flush()
        return artifact, version

    def _current_receipt(self, project_id: uuid.UUID) -> tuple[Artifact, ArtifactVersion] | None:
        artifacts = self.session.scalars(
            select(Artifact)
            .where(Artifact.video_project_id == project_id)
            .where(Artifact.artifact_type == RECEIPT_ARTIFACT_TYPE)
            .order_by(Artifact.created_at.asc())
        ).all()
        for artifact in artifacts:
            if artifact.current_version_id is None:
                continue
            version = self.session.get(ArtifactVersion, artifact.current_version_id)
            if version is not None and (version.content or {}).get("schema_version") == RECEIPT_SCHEMA_VERSION:
                return artifact, version
        return None

    @staticmethod
    def _validate_receipt_lineage(
        receipt_version: ArtifactVersion,
        expected_values: dict[str, Any],
    ) -> None:
        content = receipt_version.content or {}
        for key in (
            "daily_idea_decision_ref",
            "project_ref",
            "admission_receipt_ref",
            "niche_contract_digest_ref",
            "editorial_slot_ref",
        ):
            actual = content.get(key)
            expected = expected_values.get(key)
            if not isinstance(actual, dict) or not isinstance(expected, dict):
                raise ValidationFailureError(f"D2P1_RECEIPT_LINEAGE_MISSING:{key}")
            if actual.get("content_hash") != expected.get("content_hash"):
                raise ValidationFailureError(f"D2P1_RECEIPT_STALE_LINEAGE:{key}")

    @staticmethod
    def _receipt_progress_rank(receipt_version: ArtifactVersion) -> int:
        content = receipt_version.content or {}
        state = str(content.get("state") or "")
        if state in {"BLOCKED_POLICY", "FAILED_TECHNICAL"}:
            state = str(content.get("last_successful_state") or "")
        return {
            "DAILY_DECISION_ACCEPTED": 0,
            "PROJECT_ADMITTED": 1,
            "EFFECTIVE_CONTEXT_READY": 2,
            "AWAITING_RESEARCH": 2,
            "RESEARCH_READY": 3,
            "PACKAGE_BUILDING": 4,
            "PACKAGE_READY_FOR_HUMAN_REVIEW": 5,
            "PACKAGE_HUMAN_REVIEW_PASSED": 6,
            "READY_FOR_LONG_PRODUCTION": 7,
        }.get(state, 0)

    def _promote_reviewed_handoff(
        self,
        *,
        project: VideoProject,
        actor_id: uuid.UUID,
        receipt_version: ArtifactVersion,
    ) -> DailyToPackageStatusRead | None:
        """Advance only from an exact completed human review and approval."""

        content = DailyToPackageReceiptContent.model_validate(receipt_version.content)
        if content.state == "READY_FOR_LONG_PRODUCTION":
            return self._status_from_receipt(receipt_version)
        if content.state != "PACKAGE_READY_FOR_HUMAN_REVIEW":
            return None
        review = self.session.scalars(
            select(ReviewTask)
            .where(ReviewTask.video_project_id == project.id)
            .where(ReviewTask.target_artifact_version_id == receipt_version.id)
            .where(ReviewTask.review_type == "final_human")
            .where(ReviewTask.review_reason_codes.contains([FINAL_HUMAN_REVIEW_REASON]))
            .order_by(ReviewTask.created_at.asc())
        ).first()
        approval = self.session.scalars(
            select(ApprovalDecision)
            .where(ApprovalDecision.target_type == "artifact_version")
            .where(ApprovalDecision.target_id == receipt_version.id)
            .where(ApprovalDecision.target_artifact_version_id == receipt_version.id)
            .where(ApprovalDecision.decision == "approved")
            .order_by(ApprovalDecision.created_at.asc())
        ).first()
        if review is None or review.status != "completed" or approval is None:
            return None
        review_ref = {
            "type": "human_review_receipt",
            "review_task_id": str(review.id),
            "approval_decision_id": str(approval.id),
            "reviewed_artifact_version_id": str(receipt_version.id),
            "reviewed_artifact_hash": receipt_version.content_hash,
            "decision": "PASS",
        }
        stable = {key: value for key, value in review_ref.items() if key != "content_hash"}
        review_ref["content_hash"] = content_hash(_json_dict(stable))
        base = content.model_dump(mode="json")
        _, passed = self._persist_receipt(
            project=project,
            actor_id=actor_id,
            values={
                **base,
                "state": "PACKAGE_HUMAN_REVIEW_PASSED",
                "last_successful_state": "PACKAGE_HUMAN_REVIEW_PASSED",
                "package_human_review_ref": review_ref,
                "human_review_state": "PASS",
                "blockers": [],
                "exact_next_action": "Select an explicit long-production execution mode; no media has been created.",
            },
        )
        _, ready = self._persist_receipt(
            project=project,
            actor_id=actor_id,
            values={
                **passed.content,
                "state": "READY_FOR_LONG_PRODUCTION",
                "last_successful_state": "READY_FOR_LONG_PRODUCTION",
                "human_review_state": "PASS",
                "exact_next_action": "Run the controlled long-production trigger in OFFLINE_FIXTURE or with an MR1 envelope.",
            },
        )
        return self._status_from_receipt(ready)

    def _ensure_final_human_review(
        self,
        *,
        project: VideoProject,
        receipt_version: ArtifactVersion,
        actor_id: uuid.UUID,
    ) -> ReviewTask:
        existing = self.session.scalars(
            select(ReviewTask)
            .where(ReviewTask.video_project_id == project.id)
            .where(ReviewTask.target_artifact_version_id == receipt_version.id)
            .where(ReviewTask.review_type == "final_human")
            .where(ReviewTask.review_reason_codes.contains([FINAL_HUMAN_REVIEW_REASON]))
            .order_by(ReviewTask.created_at.asc())
        ).first()
        if existing is not None:
            return existing
        return ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=project.id,
                target_type="artifact_version",
                target_id=receipt_version.id,
                target_artifact_version_id=receipt_version.id,
                review_type="final_human",
                requested_by_user_id=actor_id,
                review_reason_codes=[FINAL_HUMAN_REVIEW_REASON],
                evidence_required=True,
                evidence_refs=[
                    {"type": "daily_to_package_receipt", "artifact_version_id": str(receipt_version.id)},
                ],
                review_scope="D2P1 production-valid scripted package boundary; media/render/publish remain forbidden.",
            ),
            correlation_id=f"d2p1-final-human-review-{project.id}",
        )

    def _persisted_blocked(
        self,
        *,
        project: VideoProject,
        actor_id: uuid.UUID,
        values: dict[str, Any],
        blocker: str,
        last_successful_state: str,
        next_action: str,
    ) -> DailyToPackageStatusRead:
        _, version = self._persist_receipt(
            project=project,
            actor_id=actor_id,
            values={
                **values,
                "state": "BLOCKED_POLICY",
                "last_successful_state": last_successful_state,
                "human_review_state": "BLOCKED",
                "blockers": [blocker],
                "exact_next_action": next_action,
            },
        )
        return self._status_from_receipt(version)

    def _persisted_technical_failure(
        self,
        *,
        project: VideoProject,
        actor_id: uuid.UUID,
        values: dict[str, Any],
        blocker: str,
    ) -> DailyToPackageStatusRead:
        _, version = self._persist_receipt(
            project=project,
            actor_id=actor_id,
            values={
                **values,
                "state": "FAILED_TECHNICAL",
                "last_successful_state": "PACKAGE_BUILDING",
                "human_review_state": "NOT_READY",
                "blockers": [blocker],
                "exact_next_action": "Retry the same D2P1 lineage after repairing the technical package failure.",
            },
        )
        return self._status_from_receipt(version)

    @staticmethod
    def _ephemeral_blocked(
        decision_id: uuid.UUID,
        blocker: str,
        *,
        next_action: str = "Create a new policy-valid DailyIdeaDecision version and rerun D2P1.",
    ) -> DailyToPackageStatusRead:
        return DailyToPackageStatusRead(
            daily_idea_decision_id=decision_id,
            current_state="BLOCKED_POLICY",
            research={"state": "NOT_RESOLVED"},
            niche_gates={},
            blockers=[blocker],
            exact_next_action=next_action,
            human_review_state="BLOCKED",
            provider_calls_made=0,
            media_calls_made=0,
        )

    def _status_from_receipt(self, version: ArtifactVersion) -> DailyToPackageStatusRead:
        content = DailyToPackageReceiptContent.model_validate(version.content)
        gate_map = {
            str(ref.get("gate_key")): ref
            for ref in content.niche_gate_refs
            if isinstance(ref, dict) and ref.get("gate_key")
        }
        return DailyToPackageStatusRead(
            daily_idea_decision_id=uuid.UUID(str(content.daily_idea_decision_ref["id"])),
            current_state=content.state,
            project=content.project_ref,
            effective_context=content.effective_context_ref,
            research={
                "state": "APPROVED" if content.research_pack_ref else "AWAITING_APPROVAL",
                "assignment": content.research_assignment_ref,
                "pack": content.research_pack_ref,
            },
            package=content.scripted_package_ref,
            package_human_review=content.package_human_review_ref,
            niche_gates=gate_map,
            blockers=content.blockers,
            exact_next_action=content.exact_next_action,
            human_review_state=content.human_review_state,
            idempotency_fingerprint=content.idempotency_fingerprint,
            receipt={
                "type": "daily_to_package_receipt",
                "artifact_version_id": str(version.id),
                "content_hash": version.content_hash,
                "version_number": version.version_number,
            },
            provider_calls_made=content.provider_calls_made,
            media_calls_made=content.media_calls_made,
        )

    def _idempotency_fingerprint(
        self,
        *,
        lineage: _Lineage,
        admission: ProjectAdmissionDecision,
        project: VideoProject,
        research_version: ArtifactVersion,
    ) -> str:
        return content_hash(
            {
                "daily_idea_decision_hash": self._lineage_decision_ref(lineage)["content_hash"],
                "project_admission_hash": self._admission_ref(admission)["content_hash"],
                "project_hash": self._project_ref(project)["content_hash"],
                "channel_profile_version_id": str(lineage.profile.id),
                "channel_profile_input_hash": lineage.profile.profile_input_hash,
                "compiled_policy_snapshot_id": str(lineage.policy_snapshot.id),
                "compiled_policy_snapshot_hash": lineage.policy_snapshot.content_hash,
                "niche_contract_digest_hash": lineage.digest_ref["content_hash"],
                "editorial_slot_hash": self._slot_ref(lineage.slot)["content_hash"],
                "research_pack_hash": research_version.content_hash,
                "package_builder_version": PACKAGE_BUILDER_VERSION,
            }
        )

    @staticmethod
    def _lineage_decision_ref(lineage: _Lineage) -> dict[str, Any]:
        return {
            "type": "daily_idea_decision",
            "id": str(lineage.decision.id),
            "ref": lineage.topic_gate.get("subject_ref"),
            "content_hash": lineage.topic_gate.get("subject_hash"),
        }

    @staticmethod
    def _decision_subject_ref(
        decision: DailyIdeaDecision,
        *,
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stable = {
            "id": str(decision.id),
            "policy_snapshot_id": str(decision.policy_snapshot_id),
            "title": decision.proposed_title,
            "angle": decision.proposed_angle,
            "pillar": decision.proposed_pillar,
            "series_key": decision.proposed_series_key,
            "rationale": decision.rationale,
            "evidence_refs": evidence_refs,
        }
        return {
            "type": "daily_idea_decision",
            "id": str(decision.id),
            "ref": f"daily-idea-decision://{decision.id}",
            "content_hash": content_hash(_json_dict(stable)),
        }

    @staticmethod
    def _decision_ref(decision: DailyIdeaDecision) -> dict[str, Any]:
        stable = {
            "channel_daily_run_id": str(decision.channel_daily_run_id),
            "company_id": str(decision.company_id),
            "channel_workspace_id": str(decision.channel_workspace_id),
            "policy_snapshot_id": str(decision.policy_snapshot_id),
            "context_pack_snapshot_id": str(decision.context_pack_snapshot_id),
            "llm_run_snapshot_id": str(decision.llm_run_snapshot_id) if decision.llm_run_snapshot_id else None,
            "decision_status": decision.decision_status,
            "proposed_title": decision.proposed_title,
            "proposed_angle": decision.proposed_angle,
            "proposed_format": decision.proposed_format,
            "proposed_pillar": decision.proposed_pillar,
            "proposed_series_key": decision.proposed_series_key,
            "rationale": decision.rationale,
            "evidence_refs": decision.evidence_refs,
            "reason_codes": decision.reason_codes,
            "confidence_level": decision.confidence_level,
        }
        return {
            "type": "daily_idea_decision",
            "id": str(decision.id),
            "content_hash": content_hash(_json_dict(stable)),
        }

    @staticmethod
    def _slot_ref(slot: EditorialCalendarSlot) -> dict[str, Any]:
        stable = {
            "company_id": str(slot.company_id),
            "channel_workspace_id": str(slot.channel_workspace_id),
            "policy_snapshot_id": str(slot.policy_snapshot_id),
            "category_id": str(slot.category_id) if slot.category_id else None,
            "slot_date": slot.slot_date.isoformat(),
            "slot_type": slot.slot_type,
            "production_goal": slot.production_goal,
            "target_platforms": slot.target_platforms,
            "content_pillar": slot.content_pillar,
            "series_key": slot.series_key,
            "format_hint": slot.format_hint,
            "risk_level": slot.risk_level,
            "operational_envelope": slot.operational_envelope,
        }
        return {
            "type": "editorial_calendar_slot",
            "id": str(slot.id),
            "content_hash": content_hash(_json_dict(stable)),
        }

    @staticmethod
    def _admission_ref(admission: ProjectAdmissionDecision) -> dict[str, Any]:
        stable = {
            "channel_daily_run_id": str(admission.channel_daily_run_id),
            "daily_idea_decision_id": str(admission.daily_idea_decision_id),
            "idea_market_preflight_id": str(admission.idea_market_preflight_id) if admission.idea_market_preflight_id else None,
            "budget_gate_result": admission.budget_gate_result,
            "readiness_gate_refs": admission.readiness_gate_refs,
            "decision": admission.decision,
            "reason_codes": admission.reason_codes,
            "evidence_refs": admission.evidence_refs,
            "admitted_video_project_id": str(admission.admitted_video_project_id) if admission.admitted_video_project_id else None,
            "created_artifact_refs": admission.created_artifact_refs,
        }
        return {
            "type": "project_admission_decision",
            "id": str(admission.id),
            "content_hash": content_hash(_json_dict(stable)),
        }

    @staticmethod
    def _project_ref(project: VideoProject) -> dict[str, Any]:
        stable = {
            "company_id": str(project.company_id),
            "channel_workspace_id": str(project.channel_workspace_id),
            "policy_snapshot_id": str(project.policy_snapshot_id),
            "channel_profile_version_id": str(project.channel_profile_version_id) if project.channel_profile_version_id else None,
            "category_id": str(project.category_id) if project.category_id else None,
            "channel_contract_content_hash": project.channel_contract_content_hash,
            "title": project.title,
            "description": project.description,
            "project_type": project.project_type,
            "d2p1_frozen_lineage": (project.audience_delivery_summary or {}).get("d2p1_frozen_lineage"),
        }
        return {
            "type": "video_project",
            "id": str(project.id),
            "content_hash": content_hash(_json_dict(stable)),
        }

    @staticmethod
    def _effective_ref(effective: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
        return {
            "type": "effective_channel_runtime_context_snapshot",
            "id": str(effective.id),
            "content_hash": effective.context_hash,
            "status": effective.compile_status,
        }

    def _artifact_version_ref(self, version: ArtifactVersion) -> dict[str, Any]:
        artifact = self.session.get(Artifact, version.artifact_id)
        return {
            "type": "artifact_version",
            "id": str(version.id),
            "artifact_id": str(version.artifact_id),
            "artifact_type": artifact.artifact_type if artifact is not None else None,
            "version_number": version.version_number,
            "content_hash": version.content_hash,
        }

    @staticmethod
    def _research_assignment_ref(task: ReviewTask) -> dict[str, Any]:
        stable = {
            "video_project_id": str(task.video_project_id),
            "target_id": str(task.target_id),
            "target_artifact_version_id": str(task.target_artifact_version_id) if task.target_artifact_version_id else None,
            "review_type": task.review_type,
            "review_reason_codes": task.review_reason_codes,
            "evidence_refs": task.evidence_refs,
            "review_scope": task.review_scope,
            "context_pack_ref": task.context_pack_ref,
        }
        return {
            "type": "review_task",
            "id": str(task.id),
            "status": task.status,
            "content_hash": content_hash(_json_dict(stable)),
        }

    @staticmethod
    def _package_ref(package: FirstScriptedVideoPackage) -> dict[str, Any]:
        stable = {
            "video_project_id": str(package.video_project_id) if package.video_project_id else None,
            "channel_id": str(package.channel_id),
            "channel_profile_version_id": str(package.channel_profile_version_id) if package.channel_profile_version_id else None,
            "compiled_policy_snapshot_id": str(package.compiled_policy_snapshot_id) if package.compiled_policy_snapshot_id else None,
            "effective_context_snapshot_id": str(package.effective_context_snapshot_id) if package.effective_context_snapshot_id else None,
            "effective_context_hash": package.effective_context_hash,
            "package_status": package.package_status,
            "agent_run_refs": package.agent_run_refs,
            "prompt_render_run_refs": package.prompt_render_run_refs,
            "prompt_audit_snapshot_refs": package.prompt_audit_snapshot_refs,
            "artifacts": package.artifacts,
            "limitations": package.limitations,
            "risk_limitations_summary": package.risk_limitations_summary,
            "next_action": package.next_action,
        }
        return {
            "type": "first_scripted_video_package",
            "id": str(package.id),
            "status": package.package_status,
            "content_hash": content_hash(_json_dict(stable)),
        }

    def _gate_from_value(self, value: Any, gate_key: str) -> dict[str, Any] | None:
        if isinstance(value, dict):
            direct = value.get(gate_key)
            if isinstance(direct, dict):
                return direct
            if value.get("gate_key") == gate_key:
                return value
            for item in value.values():
                found = self._gate_from_value(item, gate_key)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = self._gate_from_value(item, gate_key)
                if found is not None:
                    return found
        return None

    def _execution_boundary_counts(self) -> dict[str, int]:
        models = {
            "provider_attempts": ProviderAttempt,
            "provider_job_snapshots": ProviderJobSnapshot,
            "paid_provider_call_ledger": PaidProviderCallLedger,
            "media_render_jobs": MediaRenderJob,
            "final_media_refs": FinalMediaRef,
            "drive_media_offload_jobs": MediaOffloadJob,
            "drive_cloud_media_refs": CloudMediaRef,
            "youtube_human_upload_tasks": HumanUploadTask,
            "youtube_uploaded_videos": UploadedVideo,
        }
        return {
            key: int(self.session.scalar(select(func.count()).select_from(model)) or 0)
            for key, model in models.items()
        }

    @staticmethod
    def _execution_totals(counts: dict[str, int]) -> tuple[int, int]:
        provider = sum(
            int(counts.get(key) or 0)
            for key in PROVIDER_EXECUTION_BOUNDARY_KEYS
        )
        media = sum(
            int(counts.get(key) or 0)
            for key in MEDIA_EXECUTION_BOUNDARY_KEYS
        )
        return provider, media

    def _execution_counts(self) -> tuple[int, int]:
        return self._execution_totals(self._execution_boundary_counts())

    def _execution_boundary_proof(
        self, before: dict[str, int]
    ) -> dict[str, Any]:
        after = self._execution_boundary_counts()
        deltas = {
            key: max(0, int(after.get(key) or 0) - int(before.get(key) or 0))
            for key in before
        }
        provider_total, media_total = self._execution_totals(deltas)
        stable = {
            "schema_version": "d2p1.zero-execution-boundary.v1",
            "monitored_provider_boundaries": [
                "Pexels",
                "Google Gemini Image",
                "Google Veo",
                "ElevenLabs",
                "provider job snapshots",
                "paid provider call ledger",
            ],
            "monitored_media_boundaries": [
                "FFmpeg/media render jobs",
                "FinalMediaRef",
                "Google Drive offload/cloud media refs",
                "YouTube upload tasks/uploaded videos",
            ],
            "record_deltas": deltas,
            "provider_calls_made": provider_total,
            "media_calls_made": media_total,
            "zero_execution_confirmed": provider_total == 0 and media_total == 0,
        }
        return {**stable, "content_hash": content_hash(_json_dict(stable))}

    @staticmethod
    def _valid_execution_boundary_proof(proof: dict[str, Any]) -> bool:
        claimed = proof.get("content_hash")
        if claimed != content_hash(
            _json_dict({key: value for key, value in proof.items() if key != "content_hash"})
        ):
            return False
        deltas = _dict(proof.get("record_deltas"))
        if set(deltas) != set(EXECUTION_BOUNDARY_KEYS) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in deltas.values()
        ):
            return False
        provider_total, media_total = DailyToPackageOrchestrator._execution_totals(
            deltas
        )
        return bool(
            proof.get("schema_version") == "d2p1.zero-execution-boundary.v1"
            and int(proof.get("provider_calls_made") or 0) == provider_total
            and int(proof.get("media_calls_made") or 0) == media_total
            and proof.get("zero_execution_confirmed")
            is (provider_total == 0 and media_total == 0)
        )

    def _accumulate_execution_delta(
        self,
        *,
        values: dict[str, Any],
        provider_before: int,
        media_before: int,
    ) -> None:
        provider_after, media_after = self._execution_counts()
        values["provider_calls_made"] = int(
            values.get("provider_calls_made") or 0
        ) + max(0, provider_after - provider_before)
        values["media_calls_made"] = int(values.get("media_calls_made") or 0) + max(
            0, media_after - media_before
        )

    @staticmethod
    def _has_execution_violation(values: dict[str, Any]) -> bool:
        return bool(
            int(values.get("provider_calls_made") or 0)
            or int(values.get("media_calls_made") or 0)
        )


def _gate_status(value: dict[str, Any]) -> str:
    return str(
        _first_nonempty(
            value.get("status"),
            value.get("result"),
            value.get("decision"),
            value.get("verdict"),
        )
        or "MISSING"
    ).upper()


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ref_matches(value: Any, expected: uuid.UUID) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_ref_matches(item, expected) for item in value.values())
    return str(expected) in str(value)


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _json_dict(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize model-ish values before sending them to canonical hashing."""

    def normalize(item: Any) -> Any:
        if isinstance(item, uuid.UUID):
            return str(item)
        if isinstance(item, Decimal):
            return str(item)
        if hasattr(item, "isoformat") and callable(item.isoformat):
            return item.isoformat()
        if isinstance(item, dict):
            return {str(key): normalize(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [normalize(nested) for nested in item]
        return item

    return normalize(value)
