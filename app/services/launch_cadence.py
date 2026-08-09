"""Controlled-evidence launch policy and deterministic long-form cadence."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.contracts.launch_cadence import (
    CadenceDecision,
    CadenceEvaluationCommand,
    CadenceEvaluationRead,
    CadenceEvaluationRequest,
    FirstChannelLaunchPolicyCreate,
    LaunchDashboardRead,
    LaunchPolicyApproval,
    LaunchRunCreate,
    LaunchRunRead,
    LaunchRunTransition,
    LaunchRunwayProjection,
    LongFormPublishSlotRead,
    RunwayCounts,
)
from app.contracts.m5 import (
    EditorialCalendarSlotCreate,
    IdeaMarketPreflightCreate,
)
from app.contracts.production_workflow import ProductionWorkflowProjectStart
from app.contracts.vcos_v2 import (
    AssignmentMode,
    LongFormPlanningRequest,
    ProductionLane,
)
from app.core.actor import ActorContext, ActorType, _system_worker_actor
from app.core.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailureError,
)
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import (
    CadenceEvaluationReceipt,
    FirstChannelLaunchPolicyVersion,
    LaunchRun,
    LongFormPublishSlot,
)
from app.db.models.m5 import (
    EditorialCalendarSlot,
    EditorialIdeaCandidate,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
)
from app.db.models.m7 import UploadedVideo
from app.db.models.ops import OpsIncident
from app.db.models.production_publish import (
    FinalReviewCandidate,
    FinalVideoDecision,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.workflow import VideoProject
from app.db.models.r3d1 import ContentCategory
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.script_qualification import (
    ScriptQualificationReceipt,
    ScriptQualificationRun,
    SeriesEpisodeReservation,
)
from app.services.cadence_events import (
    CADENCE_AGGREGATE_TYPE,
    CADENCE_EVALUATION_EVENT_TYPE,
)
from app.services.company_access import require_company_permission
from app.services.nich1 import (
    NicheContractCompilationError,
    NicheContractDigestCompiler,
)
from app.services.production_package import ChannelDurationContractResolver
from app.services.m5 import EditorialCalendarService, IdeaMarketPreflightService
from app.services.production_start_readiness import (
    resolve_budget_authority,
    resolve_provider_authority,
)
from app.services.production_workflow import ProductionWorkflowCoordinator
from app.services.r3d1 import CharacterBindingResolver
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.v2_support_authority import (
    V2SupportAuthorityPrepareCommand,
    V2SupportAuthorityService,
)
from app.services.vcos_v2 import LongFormPlanningService


_POLICY_TRANSITIONS = {
    "DRAFT": {"APPROVED", "ARCHIVED"},
    "APPROVED": {"SUPERSEDED", "ARCHIVED"},
    "SUPERSEDED": {"ARCHIVED"},
    "ARCHIVED": set(),
}
_RUN_TRANSITIONS = {
    "PREPARING": {"READY_TO_LAUNCH", "CANCELED"},
    "READY_TO_LAUNCH": {"ACTIVE", "PAUSED", "CANCELED"},
    "ACTIVE": {"PAUSED", "COMPLETED", "CANCELED"},
    "PAUSED": {"ACTIVE", "COMPLETED", "CANCELED"},
    "COMPLETED": set(),
    "CANCELED": set(),
}
_WEEKDAY = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}
_ACTIVE_WORKFLOW_STATES = {
    "PLANNING_PENDING",
    "PLANNING_RUNNING",
    "ASSIGNMENT_READY",
    "RESEARCH_PENDING",
    "RESEARCH_RUNNING",
    "PACKAGE_PENDING",
    "PACKAGE_RUNNING",
    "READY_FOR_PRODUCTION",
    "MEDIA_PENDING",
    "MEDIA_RUNNING",
    "RENDER_PENDING",
    "RENDER_RUNNING",
    "QC_PENDING",
    "QC_RUNNING",
    "ARCHIVE_PENDING",
    "ARCHIVE_RUNNING",
    "RETRY_SCHEDULED",
    "BLOCKED",
}


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cadence_support_budget_ceiling(budget_authority: dict[str, Any]) -> Decimal:
    """Use the evaluated per-video ceiling for frozen support authority."""

    try:
        value = Decimal(str(budget_authority["max_estimated_cost_per_video"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationFailureError(
            "CADENCE_SUPPORT_BUDGET_AUTHORITY_INVALID"
        ) from exc
    if value < 0 or value > Decimal("250"):
        raise ValidationFailureError("CADENCE_SUPPORT_BUDGET_AUTHORITY_INVALID")
    return value


class FirstChannelLaunchPolicyService:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        data: FirstChannelLaunchPolicyCreate,
        actor: ActorContext,
    ) -> FirstChannelLaunchPolicyVersion:
        require_company_permission(
            self.session,
            actor=actor,
            permission="channel.manage",
            company_id=data.company_id,
        )
        workspace = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        profile = self.session.get(
            ChannelProfileVersion, data.channel_profile_version_id
        )
        policy = self.session.get(
            CompiledChannelPolicySnapshot, data.policy_snapshot_id
        )
        if (
            workspace is None
            or workspace.company_id != data.company_id
            or profile is None
            or profile.channel_workspace_id != workspace.id
            or policy is None
            or policy.channel_workspace_id != workspace.id
            or policy.channel_profile_version_id != profile.id
            or profile.status != "active"
            or policy.status != "active"
            or workspace.active_policy_snapshot_id != policy.id
        ):
            raise ValidationFailureError("LAUNCH_POLICY_CHANNEL_AUTHORITY_MISMATCH")
        # Resolving is the proof that duration remains owned by the exact
        # ChannelProfile/CompiledPolicy lineage; no duration number is copied.
        ChannelDurationContractResolver(self.session).resolve(
            profile_version_id=profile.id,
            policy_snapshot_id=policy.id,
            production_lane=ProductionLane.LONG_FORM,
        )
        series = list(
            self.session.scalars(
                select(SeriesPlan).where(
                    SeriesPlan.id.in_(data.approved_initial_series_plan_ids)
                )
            ).all()
        )
        if len(series) != len(data.approved_initial_series_plan_ids):
            raise ValidationFailureError("LAUNCH_POLICY_SERIES_PLAN_MISSING")
        for item in series:
            if (
                item.company_id != data.company_id
                or item.channel_workspace_id != data.channel_workspace_id
                or item.channel_profile_version_id != data.channel_profile_version_id
                or item.policy_snapshot_id != data.policy_snapshot_id
                or item.state != "APPROVED"
                or item.allowed_production_lanes != ["LONG_FORM"]
            ):
                raise ValidationFailureError("LAUNCH_POLICY_SERIES_AUTHORITY_MISMATCH")
        if data.supersedes_policy_version_id is not None:
            prior = self.session.get(
                FirstChannelLaunchPolicyVersion,
                data.supersedes_policy_version_id,
            )
            if (
                prior is None
                or prior.channel_workspace_id != data.channel_workspace_id
                or prior.policy_version >= data.policy_version
            ):
                raise ValidationFailureError("LAUNCH_POLICY_SUPERSESSION_INVALID")
        payload = data.model_dump(mode="json")
        canonical_hash = _hash(payload)
        existing = self.session.scalar(
            select(FirstChannelLaunchPolicyVersion).where(
                FirstChannelLaunchPolicyVersion.canonical_hash == canonical_hash
            )
        )
        if existing is not None:
            return existing
        record_payload = data.model_dump()
        record_payload["approved_initial_series_plan_ids"] = [
            str(item) for item in data.approved_initial_series_plan_ids
        ]
        record = FirstChannelLaunchPolicyVersion(
            **record_payload,
            state="DRAFT",
            canonical_hash=canonical_hash,
            created_by_user_id=actor.actor_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def approve(
        self,
        *,
        policy_version_id: uuid.UUID,
        data: LaunchPolicyApproval,
        actor: ActorContext,
    ) -> FirstChannelLaunchPolicyVersion:
        record = self.session.scalar(
            select(FirstChannelLaunchPolicyVersion)
            .where(FirstChannelLaunchPolicyVersion.id == policy_version_id)
            .with_for_update()
        )
        if record is None:
            raise NotFoundError(f"launch policy not found: {policy_version_id}")
        require_company_permission(
            self.session,
            actor=actor,
            permission="channel.manage",
            company_id=record.company_id,
        )
        if record.state == "APPROVED":
            return record
        workspace = self.session.get(
            ChannelWorkspace,
            record.channel_workspace_id,
        )
        profile = self.session.get(
            ChannelProfileVersion,
            record.channel_profile_version_id,
        )
        compiled_policy = self.session.get(
            CompiledChannelPolicySnapshot,
            record.policy_snapshot_id,
        )
        if (
            workspace is None
            or profile is None
            or compiled_policy is None
            or workspace.active_policy_snapshot_id != compiled_policy.id
            or profile.status != "active"
            or compiled_policy.status != "active"
            or compiled_policy.channel_profile_version_id != profile.id
        ):
            raise ValidationFailureError(
                "LAUNCH_POLICY_CHANNEL_AUTHORITY_NO_LONGER_ACTIVE"
            )
        if "APPROVED" not in _POLICY_TRANSITIONS.get(record.state, set()):
            raise ConflictError(f"launch policy cannot be approved from {record.state}")
        if _hash(self._semantic_payload(record)) != record.canonical_hash:
            raise ValidationFailureError("LAUNCH_POLICY_IMMUTABILITY_HASH_MISMATCH")
        immutable_evidence = {_hash(item) for item in list(record.evidence_refs or [])}
        if not all(_hash(item) in immutable_evidence for item in data.evidence_refs):
            raise ValidationFailureError("LAUNCH_POLICY_APPROVAL_EVIDENCE_NOT_BOUND")
        current = self.session.scalar(
            select(FirstChannelLaunchPolicyVersion)
            .where(
                FirstChannelLaunchPolicyVersion.channel_workspace_id
                == record.channel_workspace_id,
                FirstChannelLaunchPolicyVersion.state == "APPROVED",
            )
            .with_for_update()
        )
        if current is not None and current.id != record.id:
            if record.supersedes_policy_version_id != current.id:
                raise ConflictError(
                    "approved launch policy requires explicit supersession"
                )
            current.state = "SUPERSEDED"
        record.state = "APPROVED"
        record.approved_by_user_id = actor.actor_id
        record.approved_at = utc_now()
        self.session.flush()
        return record

    def active_for_channel(
        self, channel_workspace_id: uuid.UUID
    ) -> FirstChannelLaunchPolicyVersion | None:
        return self.session.scalar(
            select(FirstChannelLaunchPolicyVersion).where(
                FirstChannelLaunchPolicyVersion.channel_workspace_id
                == channel_workspace_id,
                FirstChannelLaunchPolicyVersion.state == "APPROVED",
            )
        )

    @staticmethod
    def _semantic_payload(record: FirstChannelLaunchPolicyVersion) -> dict[str, Any]:
        ignored = {
            "id",
            "state",
            "canonical_hash",
            "created_by_user_id",
            "approved_by_user_id",
            "approved_at",
            "created_at",
            "updated_at",
        }
        return {
            column.name: getattr(record, column.name)
            for column in record.__table__.columns
            if column.name not in ignored
        }


class LaunchRunService:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        data: LaunchRunCreate,
        actor: ActorContext,
    ) -> LaunchRun:
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, data.launch_policy_version_id
        )
        if policy is None or policy.state != "APPROVED":
            raise ValidationFailureError("APPROVED_LAUNCH_POLICY_REQUIRED")
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.start",
            company_id=policy.company_id,
        )
        existing = self.session.scalar(
            select(LaunchRun).where(
                LaunchRun.channel_workspace_id == policy.channel_workspace_id,
                LaunchRun.launch_key == data.launch_key,
            )
        )
        if existing is not None:
            if existing.launch_policy_version_id != policy.id:
                raise ConflictError("LAUNCH_RUN_IDENTITY_CONFLICT")
            return existing
        record = LaunchRun(
            launch_policy_version_id=policy.id,
            company_id=policy.company_id,
            channel_workspace_id=policy.channel_workspace_id,
            launch_key=data.launch_key,
            state="PREPARING",
            preparation_started_on=data.preparation_started_on,
            reason_codes=["LAUNCH_PREPARATION_STARTED"],
            created_by_user_id=actor.actor_id,
            updated_by_user_id=actor.actor_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def transition(
        self,
        *,
        launch_run_id: uuid.UUID,
        data: LaunchRunTransition,
        actor: ActorContext,
    ) -> LaunchRun:
        run = self.session.scalar(
            select(LaunchRun).where(LaunchRun.id == launch_run_id).with_for_update()
        )
        if run is None:
            raise NotFoundError(f"launch run not found: {launch_run_id}")
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.start",
            company_id=run.company_id,
        )
        target = data.target_state.value
        if target == run.state:
            return run
        if target not in _RUN_TRANSITIONS[run.state]:
            raise ConflictError(f"invalid launch transition: {run.state}->{target}")
        now = utc_now()
        run.state = target
        run.reason_codes = data.reason_codes
        run.updated_by_user_id = actor.actor_id
        if target == "ACTIVE":
            policy = self.session.get(
                FirstChannelLaunchPolicyVersion, run.launch_policy_version_id
            )
            if policy is None or policy.state != "APPROVED":
                raise ValidationFailureError("APPROVED_LAUNCH_POLICY_REQUIRED")
            active_series_plan_ids = list(
                self.session.scalars(
                    select(SeriesRun.series_plan_id).where(
                        SeriesRun.channel_workspace_id == run.channel_workspace_id,
                        SeriesRun.state.in_(["ACTIVE", "SCHEDULED"]),
                    )
                ).all()
            )
            if len(active_series_plan_ids) > policy.max_active_runs:
                raise ValidationFailureError("LAUNCH_MAX_ACTIVE_SERIES_EXCEEDED")
            approved_initial_series = set(policy.approved_initial_series_plan_ids or [])
            if any(
                str(series_plan_id) not in approved_initial_series
                for series_plan_id in active_series_plan_ids
            ):
                raise ValidationFailureError(
                    "LAUNCH_ACTIVE_SERIES_OUTSIDE_INITIAL_POLICY"
                )
            run.launch_started_at = run.launch_started_at or now
            run.paused_at = None
        elif target == "PAUSED":
            run.paused_at = now
        elif target in {"COMPLETED", "CANCELED"}:
            run.completed_at = now
        self.session.flush()
        return run


class LaunchRunwayService:
    def __init__(self, session: Session):
        self.session = session

    def project(self, launch_run_id: uuid.UUID) -> LaunchRunwayProjection:
        run, policy = self._run_policy(launch_run_id)
        stage_counts = dict(
            self.session.execute(
                select(
                    EditorialIdeaCandidate.stage,
                    func.count(EditorialIdeaCandidate.id),
                )
                .where(
                    EditorialIdeaCandidate.channel_workspace_id
                    == run.channel_workspace_id,
                    EditorialIdeaCandidate.policy_snapshot_id
                    == policy.policy_snapshot_id,
                )
                .group_by(EditorialIdeaCandidate.stage)
            ).all()
        )
        published = int(
            self.session.scalar(
                select(func.count(UploadedVideo.id)).where(
                    UploadedVideo.channel_workspace_id == run.channel_workspace_id,
                    UploadedVideo.verification_status == "VERIFIED",
                )
            )
            or 0
        )
        upload_approved = int(
            self.session.scalar(
                select(func.count(FinalVideoDecision.id)).where(
                    FinalVideoDecision.channel_workspace_id == run.channel_workspace_id,
                    FinalVideoDecision.decision == "UPLOAD",
                )
            )
            or 0
        )
        final_ready = int(
            self.session.scalar(
                select(func.count(FinalReviewCandidate.id)).where(
                    FinalReviewCandidate.channel_workspace_id
                    == run.channel_workspace_id
                )
            )
            or 0
        )
        active = int(
            self.session.scalar(
                select(func.count(ProductionWorkflowRun.id)).where(
                    ProductionWorkflowRun.channel_workspace_id
                    == run.channel_workspace_id,
                    ProductionWorkflowRun.production_lane == "LONG_FORM",
                    ProductionWorkflowRun.state.in_(_ACTIVE_WORKFLOW_STATES),
                )
            )
            or 0
        )
        active_series = int(
            self.session.scalar(
                select(func.count(SeriesRun.id)).where(
                    SeriesRun.channel_workspace_id == run.channel_workspace_id,
                    SeriesRun.state.in_(["ACTIVE", "SCHEDULED"]),
                )
            )
            or 0
        )
        # ``IN_PRODUCTION`` is an editorial projection, not execution proof.
        # Count it as runway occupancy only while its canonical long-form
        # workflow is still nonterminal.  This keeps an auditable, terminal
        # zero-effect lineage from permanently satisfying the greenlit pool
        # after recovery without changing the historical candidate row.
        active_workflow_for_candidate = (
            select(ProductionWorkflowRun.id)
            .join(
                ProjectAdmissionDecision,
                ProjectAdmissionDecision.admitted_video_project_id
                == ProductionWorkflowRun.video_project_id,
            )
            .where(
                ProjectAdmissionDecision.editorial_idea_candidate_id
                == EditorialIdeaCandidate.id,
                ProductionWorkflowRun.channel_workspace_id
                == run.channel_workspace_id,
                ProductionWorkflowRun.production_lane == "LONG_FORM",
                ProductionWorkflowRun.state.in_(_ACTIVE_WORKFLOW_STATES),
            )
            .exists()
        )
        greenlit_occupancy = int(
            self.session.scalar(
                select(func.count(EditorialIdeaCandidate.id)).where(
                    EditorialIdeaCandidate.channel_workspace_id
                    == run.channel_workspace_id,
                    EditorialIdeaCandidate.policy_snapshot_id
                    == policy.policy_snapshot_id,
                    or_(
                        EditorialIdeaCandidate.stage.in_(
                            {
                                "GREENLIT",
                                "SELECTED_FOR_SLOT",
                                "FINAL_REVIEW_READY",
                                "PUBLISHED",
                            }
                        ),
                        and_(
                            EditorialIdeaCandidate.stage == "IN_PRODUCTION",
                            active_workflow_for_candidate,
                        ),
                    ),
                )
            )
            or 0
        )
        counts = RunwayCounts(
            idea_candidates=sum(stage_counts.values()),
            preflight_passed_candidates=sum(
                stage_counts.get(stage, 0)
                for stage in {
                    "PREFLIGHT_PASS",
                    "GREENLIT",
                    "SELECTED_FOR_SLOT",
                    "IN_PRODUCTION",
                    "FINAL_REVIEW_READY",
                    "PUBLISHED",
                }
            ),
            greenlit_candidates=greenlit_occupancy,
            in_production_videos=active,
            final_review_ready_videos=final_ready,
            upload_approved_videos=upload_approved,
            published_videos=published,
            rejected_or_expired_candidates=stage_counts.get("REJECTED", 0)
            + stage_counts.get("EXPIRED", 0),
        )
        return LaunchRunwayProjection(
            launch_run_id=run.id,
            launch_policy_version_id=policy.id,
            as_of=utc_now(),
            counts=counts,
            public_ready_buffer=self.public_ready_buffer(run.channel_workspace_id),
            active_series=active_series,
        )

    def public_ready_buffer(self, channel_workspace_id: uuid.UUID) -> int:
        do_not_upload = (
            select(FinalVideoDecision.id)
            .where(
                FinalVideoDecision.final_review_candidate_id == FinalReviewCandidate.id,
                FinalVideoDecision.decision == "DO_NOT_UPLOAD",
            )
            .exists()
        )
        verified_upload = (
            select(UploadedVideo.id)
            .where(
                UploadedVideo.video_project_id == FinalReviewCandidate.video_project_id,
                UploadedVideo.verification_status == "VERIFIED",
            )
            .exists()
        )
        return int(
            self.session.scalar(
                select(func.count(FinalReviewCandidate.id)).where(
                    FinalReviewCandidate.channel_workspace_id == channel_workspace_id,
                    FinalReviewCandidate.production_lane == "LONG_FORM",
                    FinalReviewCandidate.archive_verification_state == "VERIFIED",
                    ~do_not_upload,
                    ~verified_upload,
                )
            )
            or 0
        )

    def _run_policy(
        self, launch_run_id: uuid.UUID
    ) -> tuple[LaunchRun, FirstChannelLaunchPolicyVersion]:
        run = self.session.get(LaunchRun, launch_run_id)
        if run is None:
            raise NotFoundError(f"launch run not found: {launch_run_id}")
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, run.launch_policy_version_id
        )
        if policy is None:
            raise ValidationFailureError("LAUNCH_POLICY_MISSING")
        return run, policy


class LongFormCadenceService:
    def __init__(
        self,
        session: Session,
        *,
        now: Any = utc_now,
        provider_readiness_snapshot: Callable[[], Any] | None = None,
        support_authority_preparer: Callable[
            [uuid.UUID, uuid.UUID, uuid.UUID, Decimal], Any
        ]
        | None = None,
    ):
        self.session = session
        self.now = now
        self.provider_readiness_snapshot = provider_readiness_snapshot
        # This seam is intentionally narrow and exists only for isolated
        # tests.  The normal cadence path seals the same frozen support
        # envelope as OperatorPlanningService before it starts a workflow.
        self.support_authority_preparer = support_authority_preparer

    def ensure_slots(self, launch_run_id: uuid.UUID) -> list[LongFormPublishSlot]:
        run = self.session.scalar(
            select(LaunchRun).where(LaunchRun.id == launch_run_id).with_for_update()
        )
        if run is None:
            raise NotFoundError(f"launch run not found: {launch_run_id}")
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, run.launch_policy_version_id
        )
        if policy is None or policy.state != "APPROVED":
            raise ValidationFailureError("APPROVED_LAUNCH_POLICY_REQUIRED")
        try:
            zone = ZoneInfo(policy.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationFailureError("LAUNCH_POLICY_TIMEZONE_INVALID") from exc
        hour, minute = (int(part) for part in policy.publish_local_time.split(":"))
        now = self.now()
        local_today = now.astimezone(zone).date()
        horizon = local_today + timedelta(days=policy.max_days_produced_ahead)
        existing = list(
            self.session.scalars(
                select(LongFormPublishSlot)
                .where(LongFormPublishSlot.launch_run_id == run.id)
                .order_by(LongFormPublishSlot.intended_publish_at)
            ).all()
        )
        prior_times = [item.intended_publish_at for item in existing]
        prior_times.extend(
            self.session.scalars(
                select(UploadedVideo.published_at).where(
                    UploadedVideo.channel_workspace_id == run.channel_workspace_id,
                    UploadedVideo.verification_status == "VERIFIED",
                )
            ).all()
        )
        allowed = {_WEEKDAY[item] for item in policy.publish_weekdays}
        current = local_today
        while current <= horizon:
            if current.weekday() in allowed:
                intended = datetime.combine(
                    current, time(hour=hour, minute=minute), tzinfo=zone
                )
                intended_utc = intended.astimezone(now.tzinfo)
                if intended_utc > now and not any(
                    abs((intended_utc - prior).total_seconds())
                    < policy.minimum_publish_interval_hours * 3600
                    for prior in prior_times
                ):
                    slot = LongFormPublishSlot(
                        launch_run_id=run.id,
                        launch_policy_version_id=policy.id,
                        company_id=run.company_id,
                        channel_workspace_id=run.channel_workspace_id,
                        local_publish_date=current,
                        intended_publish_at=intended_utc,
                        target_start_window_open_at=intended_utc
                        - timedelta(hours=policy.render_lead_time_max_hours),
                        target_start_window_close_at=intended_utc
                        - timedelta(hours=policy.render_lead_time_min_hours),
                        state="OPEN",
                    )
                    self.session.add(slot)
                    self.session.flush()
                    existing.append(slot)
                    prior_times.append(intended_utc)
            current += timedelta(days=1)
        return sorted(existing, key=lambda item: item.intended_publish_at)

    def request_evaluation(
        self,
        *,
        launch_run_id: uuid.UUID,
        data: CadenceEvaluationRequest | None = None,
        actor: ActorContext,
    ) -> DomainEvent:
        """Put one deterministic cadence command on the existing durable outbox."""

        run = self.session.get(LaunchRun, launch_run_id)
        if run is None:
            raise NotFoundError(f"launch run not found: {launch_run_id}")
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, run.launch_policy_version_id
        )
        if policy is None:
            raise ValidationFailureError("LAUNCH_POLICY_MISSING")
        self._authorize_evaluation_request(run=run, actor=actor)
        now = self.now()
        evaluation_key = self._hour_window_key(policy=policy, now=now)
        identity = _hash(
            {
                "launch_run_id": str(run.id),
                "launch_policy_version_id": str(policy.id),
                "evaluation_key": evaluation_key,
            }
        )
        command_id = f"cadence:{identity}"
        existing = self.session.scalar(
            select(DomainEvent).where(DomainEvent.command_id == command_id)
        )
        if existing is not None:
            return existing
        payload = {
            "launch_run_id": str(run.id),
            "launch_policy_version_id": str(policy.id),
            "launch_policy_hash": policy.canonical_hash,
            "evaluation_key": evaluation_key,
        }
        event = DomainEvent(
            id=uuid.uuid5(uuid.NAMESPACE_URL, command_id),
            event_type=CADENCE_EVALUATION_EVENT_TYPE,
            event_version=1,
            aggregate_type=CADENCE_AGGREGATE_TYPE,
            aggregate_id=run.id,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            workflow_run_id=None,
            correlation_id=f"launch-cadence:{run.id}",
            command_id=command_id,
            payload_hash=_hash(payload),
            payload=payload,
            metadata_={
                "queue_name": "production-workflow",
                "retry_policy": {
                    "policy_key": "launch-cadence-bounded-v1",
                    "automatic_retry_allowed": True,
                    "max_attempts": 5,
                    "provider_substitution_allowed": False,
                },
            },
            attempt_count=0,
            max_attempts=5,
            next_attempt_at=now,
            occurred_at=now,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def evaluate(
        self,
        *,
        launch_run_id: uuid.UUID,
        data: CadenceEvaluationCommand,
        actor: ActorContext,
    ) -> CadenceEvaluationReceipt:
        run = self.session.scalar(
            select(LaunchRun).where(LaunchRun.id == launch_run_id).with_for_update()
        )
        if run is None:
            raise NotFoundError(f"launch run not found: {launch_run_id}")
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, run.launch_policy_version_id
        )
        if policy is None:
            raise ValidationFailureError("LAUNCH_POLICY_MISSING")
        self._authorize_worker_evaluation(actor=actor)
        now = self.now()
        window_key = data.evaluation_key
        existing = self.session.scalar(
            select(CadenceEvaluationReceipt).where(
                CadenceEvaluationReceipt.launch_run_id == run.id,
                CadenceEvaluationReceipt.evaluation_window_key == window_key,
            )
        )
        if existing is not None:
            return existing
        slots = self.ensure_slots(run.id)
        slot = next(
            (
                item
                for item in slots
                if item.state == "OPEN"
                and item.target_start_window_open_at
                <= now
                <= item.target_start_window_close_at
            ),
            None,
        )
        next_open = next(
            (
                item
                for item in slots
                if item.state == "OPEN" and item.intended_publish_at > now
            ),
            None,
        )
        projection = LaunchRunwayService(self.session).project(run.id)
        active_count = projection.counts.in_production_videos + self._active_qualification_count(run.id)
        budget_readiness = resolve_budget_authority(
            self.session,
            policy_snapshot_id=policy.policy_snapshot_id,
            channel_workspace_id=run.channel_workspace_id,
        )
        provider_readiness = resolve_provider_authority(
            self.session,
            policy_snapshot_id=policy.policy_snapshot_id,
            channel_workspace_id=run.channel_workspace_id,
            readiness_snapshot=(
                self.provider_readiness_snapshot()
                if self.provider_readiness_snapshot is not None
                else None
            ),
        )
        budget_provider_readiness = {
            "state": (
                "READY"
                if budget_readiness["state"] == "READY"
                and provider_readiness["state"] == "READY"
                else "BLOCKED"
            ),
            "budget": budget_readiness,
            "providers": provider_readiness,
        }
        strict_candidates = self._strict_candidates(run, policy)
        candidates = [
            item
            for item in strict_candidates
            if item.rights_policy_state == "PASS" and item.quality_state == "PASS"
        ]
        budget_blocked = budget_readiness["state"] != "READY"
        provider_blocked = provider_readiness["state"] != "READY"
        blocked_candidate_states = self._blocked_candidate_states(
            run=run,
            policy=policy,
        )
        rights_blocked = blocked_candidate_states["rights"]
        quality_blocked = blocked_candidate_states["quality"]
        incidents = list(
            self.session.scalars(
                select(OpsIncident.id).where(
                    OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
                    OpsIncident.severity.in_(["ERROR", "CRITICAL"]),
                    or_(
                        OpsIncident.metadata_["channel_workspace_id"].astext
                        == str(run.channel_workspace_id),
                        OpsIncident.workflow_run_id.in_(
                            select(ProductionWorkflowRun.id).where(
                                ProductionWorkflowRun.channel_workspace_id
                                == run.channel_workspace_id
                            )
                        ),
                    ),
                )
            ).all()
        )
        decision, reasons = self._decision(
            run=run,
            policy=policy,
            slot=slot,
            next_open=next_open,
            projection=projection,
            active_count=active_count,
            candidates=candidates,
            incidents=incidents,
            budget_blocked=budget_blocked,
            provider_blocked=provider_blocked,
            rights_blocked=rights_blocked,
            quality_blocked=quality_blocked,
        )
        selected = candidates[0] if decision == CadenceDecision.START_SCRIPT_QUALIFICATION else None
        input_payload = {
            "channel_workspace_id": str(run.channel_workspace_id),
            "launch_policy_version_id": str(policy.id),
            "launch_policy_hash": policy.canonical_hash,
            "launch_run_id": str(run.id),
            "evaluation_window_key": window_key,
            "evaluated_at_window": now.replace(minute=0, second=0, microsecond=0),
            "timezone": policy.timezone,
            "publish_slot_id": str(slot.id) if slot else None,
            "public_ready_buffer_count": projection.public_ready_buffer,
            "active_production_count": active_count,
            "eligible_greenlit_candidate_ids": [str(item.id) for item in candidates],
            "target_long_form_per_week": policy.target_long_form_per_week,
            "quality_fallback_long_form_per_week": (
                policy.quality_fallback_long_form_per_week
            ),
            "budget_provider_readiness": budget_provider_readiness,
            "blocking_incident_ids": [str(item) for item in incidents],
            "rights_policy_blocked": rights_blocked,
            "quality_blocked": quality_blocked,
        }
        input_hash = _hash(input_payload)
        admission_id: uuid.UUID | None = None
        project_id: uuid.UUID | None = None
        workflow_id: uuid.UUID | None = None
        qualification_run_id: uuid.UUID | None = None
        if selected is not None and slot is not None:
            # This is a lightweight, durable reservation only.  The candidate
            # remains historical GREENLIT; no admission, project, workflow or
            # media authority exists until the outbox-owned qualification PASS.
            from app.services.script_qualification import ScriptQualificationService

            qualification = ScriptQualificationService(self.session).reserve(
                candidate=selected, publish_slot_id=slot.id, launch_run_id=run.id,
            )
            qualification_run_id = qualification.id
            slot.state = "QUALIFICATION_RESERVED"
            slot.reserved_candidate_id = selected.id
            self.session.flush()
        decision_payload = {
            "input_hash": input_hash,
            "decision": decision.value,
            "reason_codes": reasons,
            "selected_candidate_id": str(selected.id) if selected else None,
            "project_admission_decision_id": str(admission_id)
            if admission_id
            else None,
            "admitted_video_project_id": str(project_id) if project_id else None,
            "production_workflow_run_id": str(workflow_id) if workflow_id else None,
            "script_qualification_run_id": str(qualification_run_id) if qualification_run_id else None,
        }
        receipt = CadenceEvaluationReceipt(
            launch_run_id=run.id,
            launch_policy_version_id=policy.id,
            publish_slot_id=slot.id if slot else None,
            selected_candidate_id=selected.id if selected else None,
            admitted_video_project_id=project_id,
            production_workflow_run_id=workflow_id,
            script_qualification_run_id=qualification_run_id,
            evaluated_at=now,
            evaluation_window_key=window_key,
            timezone=policy.timezone,
            public_ready_buffer_count=projection.public_ready_buffer,
            active_production_count=active_count,
            eligible_greenlit_candidate_ids=[str(item.id) for item in candidates],
            budget_provider_readiness=input_payload["budget_provider_readiness"],
            blocking_incident_ids=[str(item) for item in incidents],
            decision=decision.value,
            reason_codes=reasons,
            input_hash=input_hash,
            decision_hash=_hash(decision_payload),
        )
        self.session.add(receipt)
        self.session.flush()
        return receipt

    @staticmethod
    def _hour_window_key(
        *,
        policy: FirstChannelLaunchPolicyVersion,
        now: datetime,
    ) -> str:
        try:
            zone = ZoneInfo(policy.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationFailureError("LAUNCH_POLICY_TIMEZONE_INVALID") from exc
        local_hour = now.astimezone(zone).replace(minute=0, second=0, microsecond=0)
        return f"{policy.id}:{local_hour.isoformat()}"

    def _authorize_evaluation_request(
        self,
        *,
        run: LaunchRun,
        actor: ActorContext,
    ) -> None:
        if actor.actor_type == ActorType.HUMAN_USER:
            require_company_permission(
                self.session,
                actor=actor,
                permission="production.start",
                company_id=run.company_id,
            )
            return
        self._authorize_worker_evaluation(actor=actor)

    @staticmethod
    def _authorize_worker_evaluation(
        *,
        actor: ActorContext,
    ) -> None:
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or not actor.has_permission("production.start")
        ):
            raise ValidationFailureError("CADENCE_SYSTEM_WORKER_UNTRUSTED")

    @staticmethod
    def _decision(
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        slot: LongFormPublishSlot | None,
        next_open: LongFormPublishSlot | None,
        projection: LaunchRunwayProjection,
        active_count: int,
        candidates: list[EditorialIdeaCandidate],
        incidents: list[uuid.UUID],
        budget_blocked: bool,
        rights_blocked: bool,
        quality_blocked: bool,
        provider_blocked: bool = False,
    ) -> tuple[CadenceDecision, list[str]]:
        if policy.state != "APPROVED" or run.state != "ACTIVE":
            return CadenceDecision.WAIT_LAUNCH_NOT_ACTIVE, ["LAUNCH_NOT_ACTIVE"]
        if projection.public_ready_buffer >= policy.public_ready_buffer_target:
            return CadenceDecision.WAIT_BUFFER_FULL, ["PUBLIC_READY_BUFFER_TARGET_MET"]
        if active_count >= policy.max_concurrent_productions:
            return CadenceDecision.WAIT_ACTIVE_PRODUCTION, [
                "MAX_CONCURRENT_PRODUCTIONS_REACHED"
            ]
        if incidents:
            return CadenceDecision.WAIT_POLICY_OR_RIGHTS_BLOCKED, [
                "UNRESOLVED_POLICY_RIGHTS_OR_INCIDENT_BLOCK"
            ]
        if budget_blocked:
            return CadenceDecision.WAIT_BUDGET_BLOCKED, ["BUDGET_PROVIDER_BLOCKED"]
        if provider_blocked:
            return CadenceDecision.WAIT_PROVIDER_AUTHORITY, [
                "MANDATORY_REAL_PROVIDER_AUTHORITY_BLOCKED"
            ]
        if not candidates:
            if rights_blocked:
                return CadenceDecision.WAIT_POLICY_OR_RIGHTS_BLOCKED, [
                    "POLICY_OR_RIGHTS_BLOCKED"
                ]
            if quality_blocked:
                return CadenceDecision.WAIT_QUALITY_BLOCKED, [
                    "QUALITY_GATE_BLOCKED",
                    (
                        "QUALITY_FALLBACK_NOT_FORCED:"
                        f"{policy.quality_fallback_long_form_per_week}"
                    ),
                ]
            return CadenceDecision.WAIT_NO_ELIGIBLE_CANDIDATE, [
                "NO_STRICT_PREFLIGHT_GREENLIT_CANDIDATE"
            ]
        if slot is None:
            return CadenceDecision.WAIT_OUTSIDE_PRODUCTION_HORIZON, [
                "NO_SLOT_IN_TARGET_PRODUCTION_START_WINDOW"
                if next_open is not None
                else "NO_ELIGIBLE_PUBLISH_SLOT"
            ]
        return CadenceDecision.START_SCRIPT_QUALIFICATION, [
            "BUFFER_BELOW_TARGET",
            "STRICT_PREFLIGHT_CANDIDATE_SELECTED",
            "LONG_FORM_SLOT_ELIGIBLE",
            "SCRIPT_QUALIFICATION_RESERVED",
        ]

    def _strict_candidates(
        self,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
    ) -> list[EditorialIdeaCandidate]:
        passed_preflight = (
            select(IdeaMarketPreflight.id)
            .where(
                IdeaMarketPreflight.editorial_idea_candidate_id
                == EditorialIdeaCandidate.id,
                IdeaMarketPreflight.decision == "PASS",
                IdeaMarketPreflight.policy_fit_state == "PASS",
                IdeaMarketPreflight.niche_contract_digest_hash.is_not(None),
                IdeaMarketPreflight.target_market_digest_hash.is_not(None),
                IdeaMarketPreflight.evidence_blob["canonical_authority_verified"]
                .as_boolean()
                .is_(True),
            )
            .exists()
        )
        candidates = list(
            self.session.scalars(
                select(EditorialIdeaCandidate)
                .where(
                    EditorialIdeaCandidate.channel_workspace_id
                    == run.channel_workspace_id,
                    EditorialIdeaCandidate.policy_snapshot_id
                    == policy.policy_snapshot_id,
                    EditorialIdeaCandidate.stage == "GREENLIT",
                    passed_preflight,
                )
                .order_by(
                    EditorialIdeaCandidate.created_at,
                    EditorialIdeaCandidate.canonical_hash,
                    EditorialIdeaCandidate.id,
                )
            ).all()
        )
        approved_initial_series = set(policy.approved_initial_series_plan_ids or [])
        from app.services.editorial_specificity import EditorialSpecificityService
        from app.services.script_qualification import TopicDefinitionService

        specificity = EditorialSpecificityService(self.session)

        return [
            candidate
            for candidate in candidates
            if (
                candidate.suggested_series_plan_id is None
                or str(candidate.suggested_series_plan_id) in approved_initial_series
            )
            if _preflight_has_active_demand_authority(
                self.session,
                candidate_id=candidate.id,
            )
            and TopicDefinitionService(self.session).current_eligibility(candidate).eligible
            and specificity.current_pass(candidate)
        ]

    def _active_qualification_count(self, launch_run_id: uuid.UUID) -> int:
        """Count in-flight pre-admission work against cadence capacity."""

        from app.db.models.script_qualification import ScriptQualificationRun

        terminal = {
            "QUALIFIED",
            "BLOCKED_NON_REPAIRABLE",
            "BLOCKED_REPAIR_BUDGET_EXHAUSTED",
            "COOLDOWN",
            "SUPERSEDED",
        }
        return int(
            self.session.scalar(
                select(func.count(ScriptQualificationRun.id)).where(
                    ScriptQualificationRun.launch_run_id == launch_run_id,
                    ScriptQualificationRun.state.not_in(terminal),
                )
            )
            or 0
        )

    def _blocked_candidate_states(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
    ) -> dict[str, bool]:
        rows = self.session.execute(
            select(
                EditorialIdeaCandidate.rights_policy_state,
                EditorialIdeaCandidate.quality_state,
            ).where(
                EditorialIdeaCandidate.channel_workspace_id == run.channel_workspace_id,
                EditorialIdeaCandidate.policy_snapshot_id == policy.policy_snapshot_id,
                EditorialIdeaCandidate.stage.not_in(
                    ["PUBLISHED", "REJECTED", "EXPIRED"]
                ),
            )
        ).all()
        return {
            "rights": any(rights == "BLOCK" for rights, _quality in rows),
            "quality": any(quality == "BLOCK" for _rights, quality in rows),
        }

    def _start_selected_candidate(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        publish_slot: LongFormPublishSlot,
        policy: FirstChannelLaunchPolicyVersion,
        run: LaunchRun,
        budget_gate_result: dict[str, Any],
        script_qualification_run_id: uuid.UUID,
    ) -> tuple[ProjectAdmissionDecision, ProductionWorkflowRun]:
        # Qualification freezes content mode before the writer call.  Do not
        # re-run OPEN_MIX selection here and accidentally rewrite the script's
        # assignment at the project boundary.
        from app.db.models.script_qualification import ScriptQualificationRun
        from app.services.script_qualification import ScriptQualificationService

        qualification = self.session.get(ScriptQualificationRun, script_qualification_run_id)
        if qualification is None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RUN_NOT_FOUND")
        ScriptQualificationService(self.session).require_pass(
            qualification.id, candidate_id=candidate.id
        )
        frozen_assignment = ScriptQualificationService._validate_assignment_resolution(qualification)
        try:
            assignment_mode = AssignmentMode(frozen_assignment["assignment_mode"])
        except (KeyError, ValueError) as exc:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_ADMISSION_ASSIGNMENT_MISMATCH") from exc
        preferred_plan_id = (
            uuid.UUID(str(frozen_assignment["series_plan_id"]))
            if frozen_assignment.get("series_plan_id") else None
        )
        preferred_run_id = (
            uuid.UUID(str(frozen_assignment["series_run_id"]))
            if frozen_assignment.get("series_run_id") else None
        )
        preflight = next(
            (
                item
                for item in self.session.scalars(
                    select(IdeaMarketPreflight)
            .where(
                IdeaMarketPreflight.editorial_idea_candidate_id == candidate.id,
                IdeaMarketPreflight.decision == "PASS",
                IdeaMarketPreflight.policy_fit_state == "PASS",
            )
            .order_by(IdeaMarketPreflight.created_at.desc())
                ).all()
                if _preflight_demand_authority_valid(item)
            ),
            None,
        )
        if preflight is None:
            raise ValidationFailureError("CADENCE_PREFLIGHT_AUTHORITY_MISSING")
        if not _preflight_demand_authority_valid(preflight):
            raise ValidationFailureError("CADENCE_MARKET_DEMAND_AUTHORITY_MISSING")
        existing_admission = self.session.scalar(
            select(ProjectAdmissionDecision).where(
                ProjectAdmissionDecision.editorial_idea_candidate_id == candidate.id,
                ProjectAdmissionDecision.schema_version == "v2",
            )
        )
        if existing_admission is not None:
            if (
                existing_admission.decision != "ADMIT"
                or existing_admission.admitted_video_project_id is None
            ):
                raise ValidationFailureError("CADENCE_EXISTING_ADMISSION_BLOCKED")
            admission = existing_admission
        else:
            category = self._resolve_category(candidate, preflight)
            character = CharacterBindingResolver(self.session).resolve(
                category=category
            )
            if character.reason_codes:
                raise ValidationFailureError(
                    "CADENCE_CHARACTER_AUTHORITY_BLOCKED:"
                    + ",".join(character.reason_codes)
                )
            if preferred_plan_id is not None and str(preferred_plan_id) not in set(
                policy.approved_initial_series_plan_ids or []
            ):
                raise ValidationFailureError("CADENCE_SERIES_OUTSIDE_LAUNCH_POLICY")
            if (
                frozen_assignment["content_mode"] == "SERIES_EPISODE"
                and (preferred_plan_id is None or preferred_run_id is None)
            ):
                raise ValidationFailureError("SCRIPT_QUALIFICATION_ADMISSION_ASSIGNMENT_MISMATCH")
            # The PUBLISH slot is a new immutable authority, not a relabelled
            # research slot.  Create it through the normal editorial service
            # so NICH1's exact slot validation is persisted for the later
            # support/preflight gates.
            editorial_slot = EditorialCalendarService(self.session).create_slot(
                data=EditorialCalendarSlotCreate(
                    company_id=run.company_id,
                    channel_workspace_id=run.channel_workspace_id,
                    policy_snapshot_id=policy.policy_snapshot_id,
                    category_id=category.id,
                    slot_date=publish_slot.local_publish_date,
                    slot_type="PUBLISH",
                    status="OPEN",
                    schema_version="v2",
                    production_lane=ProductionLane.LONG_FORM,
                    assignment_mode=assignment_mode,
                    preferred_series_plan_id=preferred_plan_id,
                    preferred_series_run_id=preferred_run_id,
                    production_goal=candidate.proposed_title,
                    target_platforms=["YOUTUBE"],
                    content_pillar=candidate.proposed_pillar,
                    risk_level="LOW",
                    operational_envelope={
                        "launch_run_id": str(run.id),
                        "launch_policy_version_id": str(policy.id),
                        "long_form_publish_slot_id": str(publish_slot.id),
                        "editorial_idea_candidate_id": str(candidate.id),
                    },
                    created_by_user_id=run.updated_by_user_id,
                )
            )
            publish_preflight = self._create_publish_preflight(
                source_preflight=preflight,
                candidate=candidate,
                editorial_slot=editorial_slot,
            )
            duration = ChannelDurationContractResolver(self.session).resolve(
                profile_version_id=policy.channel_profile_version_id,
                policy_snapshot_id=policy.policy_snapshot_id,
                production_lane=ProductionLane.LONG_FORM,
            )
            admission = LongFormPlanningService(self.session).admit(
                LongFormPlanningRequest(
                    company_id=run.company_id,
                    channel_workspace_id=run.channel_workspace_id,
                    channel_profile_version_id=policy.channel_profile_version_id,
                    policy_snapshot_id=policy.policy_snapshot_id,
                    editorial_calendar_slot_id=editorial_slot.id,
                    editorial_idea_candidate_id=candidate.id,
                    idea_market_preflight_id=publish_preflight.id,
                    assignment_mode=assignment_mode,
                    title=candidate.proposed_title,
                    description=candidate.proposed_angle,
                    category_id=category.id,
                    character_binding_id=(
                        character.character_binding.id
                        if character.character_binding is not None
                        else None
                    ),
                    preferred_series_plan_id=preferred_plan_id,
                    preferred_series_run_id=preferred_run_id,
                    niche_gate_passed=True,
                    market_gate_passed=True,
                    evidence_refs=list(candidate.evidence_refs or []),
                    budget_gate_result=budget_gate_result,
                    script_qualification_run_id=script_qualification_run_id,
                    qualification_assignment_resolution=frozen_assignment,
                    duration_contract=duration,
                    created_by_user_id=run.updated_by_user_id,
                )
            )
            if (
                admission.decision != "ADMIT"
                or admission.admitted_video_project_id is None
            ):
                raise ValidationFailureError("CADENCE_ADMISSION_DID_NOT_ADMIT")

        if admission.admitted_video_project_id is None:
            raise ValidationFailureError("CADENCE_ADMISSION_PROJECT_MISSING")
        if admission.editorial_calendar_slot_id is None:
            raise ValidationFailureError("CADENCE_ADMISSION_SLOT_MISSING")

        self._bind_niche_governance(
            project_id=admission.admitted_video_project_id,
            editorial_slot_id=admission.editorial_calendar_slot_id,
        )

        # A workflow may never be enqueued against an admitted project whose
        # runtime authority has not been compiled and bound.  Previously the
        # cadence path skipped this step (unlike OperatorPlanningService), so
        # the first RESEARCH command could only fail with a stale/missing
        # effective-context error after a project had already been reserved.
        effective = EffectiveChannelRuntimeContextCompiler(
            self.session
        ).ensure_for_project(
            admission.admitted_video_project_id,
            editorial_calendar_slot_id=admission.editorial_calendar_slot_id,
        )
        if effective.compile_status != "PASS":
            raise ValidationFailureError("CADENCE_EFFECTIVE_CONTEXT_NOT_PASS")

        # The workflow id is the durable monthly-budget reservation key for
        # real execution.  Its initial event is still in this transaction: if
        # support sealing blocks, both the workflow and its event roll back.
        worker_actor = _system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        )
        coordinator = ProductionWorkflowCoordinator(self.session)
        start_system = getattr(coordinator, "start_from_project_system", None)
        if start_system is None:
            raise ValidationFailureError("CADENCE_SYSTEM_START_BOUNDARY_UNAVAILABLE")
        workflow_read = start_system(
            video_project_id=admission.admitted_video_project_id,
            company_id=run.company_id,
            data=ProductionWorkflowProjectStart(
                idempotency_key=(
                    f"cadence:{policy.id}:{publish_slot.id}:{candidate.id}"
                )
            ),
            actor=worker_actor,
        )
        workflow = self.session.get(ProductionWorkflowRun, workflow_read.id)
        if workflow is None:
            raise ValidationFailureError("CADENCE_WORKFLOW_START_MISSING")

        # Seal the immutable LLM-produced support envelope before that
        # transaction may dispatch its initial workflow command.  Cadence is
        # the only natural production entry point, so it always requests the
        # real-provider mode; the narrow callback remains test-only.
        max_budget_usd = _cadence_support_budget_ceiling(budget_gate_result)
        if self.support_authority_preparer is not None:
            self.support_authority_preparer(
                admission.admitted_video_project_id,
                admission.editorial_calendar_slot_id,
                run.updated_by_user_id,
                max_budget_usd,
            )
        else:
            V2SupportAuthorityService(self.session).prepare(
                V2SupportAuthorityPrepareCommand(
                    video_project_id=admission.admitted_video_project_id,
                    source_type="LONG_FORM_PLAN",
                    source_id=admission.editorial_calendar_slot_id,
                    actor_user_id=run.updated_by_user_id,
                    max_budget_usd=max_budget_usd,
                    idempotency_key=(
                        f"cadence-support:{policy.id}:{publish_slot.id}:{candidate.id}"
                    ),
                    execution_mode="REAL_LONG_FORM_PRODUCTION",
                    budget_reservation_run_id=workflow.id,
                    script_qualification_run_id=script_qualification_run_id,
                )
            )
        candidate.stage = "IN_PRODUCTION"
        publish_slot.state = "RESERVED"
        publish_slot.reserved_candidate_id = candidate.id
        publish_slot.admitted_video_project_id = admission.admitted_video_project_id
        self.session.flush()
        return admission, workflow

    def finalize_qualified_script_run(
        self,
        *,
        script_qualification_run_id: uuid.UUID,
        actor: ActorContext,
    ) -> tuple[ProjectAdmissionDecision, ProductionWorkflowRun]:
        """Cross the final-admission boundary after an immutable PASS only."""

        from app.db.models.script_qualification import ScriptQualificationRun
        from app.services.script_qualification import ScriptQualificationService

        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == script_qualification_run_id)
            .with_for_update()
        )
        if qualification is None:
            raise NotFoundError(f"script qualification run not found: {script_qualification_run_id}")
        ScriptQualificationService(self.session).require_pass(
            qualification.id, candidate_id=qualification.editorial_idea_candidate_id
        )
        if qualification.admitted_video_project_id is not None or qualification.production_workflow_run_id is not None:
            admission = self.session.scalar(select(ProjectAdmissionDecision).where(ProjectAdmissionDecision.admitted_video_project_id == qualification.admitted_video_project_id))
            workflow = self.session.get(ProductionWorkflowRun, qualification.production_workflow_run_id)
            if admission is None or workflow is None:
                raise ValidationFailureError("SCRIPT_QUALIFICATION_FINAL_ADMISSION_DRIFT")
            return admission, workflow
        candidate = self.session.get(EditorialIdeaCandidate, qualification.editorial_idea_candidate_id)
        slot = self.session.scalar(select(LongFormPublishSlot).where(LongFormPublishSlot.id == qualification.publish_slot_id).with_for_update())
        run = self.session.scalar(select(LaunchRun).where(LaunchRun.id == qualification.launch_run_id).with_for_update())
        if candidate is None or slot is None or run is None or candidate.stage != "GREENLIT" or slot.state != "QUALIFICATION_RESERVED" or slot.reserved_candidate_id != candidate.id:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_FINAL_ADMISSION_AUTHORITY_MISMATCH")
        policy = self.session.get(FirstChannelLaunchPolicyVersion, run.launch_policy_version_id)
        if policy is None:
            raise ValidationFailureError("LAUNCH_POLICY_MISSING")
        budget = resolve_budget_authority(self.session, policy_snapshot_id=policy.policy_snapshot_id, channel_workspace_id=run.channel_workspace_id)
        if budget.get("state") != "READY":
            raise ValidationFailureError("BUDGET_PROVIDER_BLOCKED")
        admission, workflow = self._start_selected_candidate(
            candidate=candidate, publish_slot=slot, policy=policy, run=run,
            budget_gate_result=budget, script_qualification_run_id=qualification.id,
        )
        qualification.admitted_video_project_id = admission.admitted_video_project_id
        qualification.production_workflow_run_id = workflow.id
        self.session.flush()
        return admission, workflow

    def _create_publish_preflight(
        self,
        *,
        source_preflight: IdeaMarketPreflight,
        candidate: EditorialIdeaCandidate,
        editorial_slot: EditorialCalendarSlot,
    ) -> IdeaMarketPreflight:
        """Re-evaluate evidence against the exact admitted PUBLISH slot.

        A research-slot preflight is useful selection input, but its NICH1
        digest and editorial-slot identity cannot authorize a later PUBLISH
        slot.  The strict preflight service reloads the persisted evidence and
        rechecks the new slot without manufacturing scores or copying a stale
        slot reference.
        """

        source_evidence = (
            source_preflight.evidence_blob
            if isinstance(source_preflight.evidence_blob, dict)
            else {}
        )
        claim_evidence_refs = list(
            source_evidence.get("claim_evidence_refs") or candidate.evidence_refs or []
        )
        market_demand_evidence_refs = list(
            source_evidence.get("market_demand_evidence_refs") or []
        )
        if not claim_evidence_refs:
            raise ValidationFailureError("CADENCE_PUBLISH_PREFLIGHT_CLAIM_EVIDENCE_MISSING")
        preflight = IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=editorial_slot.company_id,
                channel_workspace_id=editorial_slot.channel_workspace_id,
                editorial_calendar_slot_id=editorial_slot.id,
                editorial_research_run_id=candidate.editorial_research_run_id,
                editorial_idea_candidate_id=candidate.id,
                search_intent_map_id=source_preflight.search_intent_map_id,
                audience_target_pack_id=source_preflight.audience_target_pack_id,
                claim_evidence_refs=claim_evidence_refs,
                market_demand_evidence_refs=market_demand_evidence_refs,
            ),
            correlation_id=(
                f"cadence-publish-preflight:{editorial_slot.id}:{candidate.id}"
            ),
        )
        if preflight.decision != "PASS" or preflight.policy_fit_state != "PASS":
            raise ValidationFailureError("CADENCE_PUBLISH_PREFLIGHT_NOT_PASS")
        return preflight

    def _bind_niche_governance(
        self,
        *,
        project_id: uuid.UUID,
        editorial_slot_id: uuid.UUID,
    ) -> None:
        """Freeze the official NICH1 digest for a cadence-created PUBLISH slot.

        The normal research slot's preflight digest cannot be transplanted to a
        newly-created PUBLISH slot because the slot id, goal, and series
        binding are part of that digest.  Compile the digest from the exact
        project/slot authority instead, once and before effective context
        compilation.  This is a deterministic local authority write, not a
        caller-provided digest or a provider effect.
        """

        project = self.session.get(VideoProject, project_id)
        slot = self.session.get(EditorialCalendarSlot, editorial_slot_id)
        channel = (
            self.session.get(ChannelWorkspace, project.channel_workspace_id)
            if project is not None
            else None
        )
        profile = (
            self.session.get(ChannelProfileVersion, project.channel_profile_version_id)
            if project is not None and project.channel_profile_version_id is not None
            else None
        )
        policy = (
            self.session.get(CompiledChannelPolicySnapshot, project.policy_snapshot_id)
            if project is not None
            else None
        )
        category = (
            self.session.get(ContentCategory, project.category_id)
            if project is not None and project.category_id is not None
            else None
        )
        if any(
            item is None for item in (project, slot, channel, profile, policy, category)
        ):
            raise ValidationFailureError("CADENCE_NICHE_AUTHORITY_MISSING")
        assert project is not None and slot is not None
        assert channel is not None and profile is not None and policy is not None
        assert category is not None

        summary = dict(project.audience_delivery_summary or {})
        frozen = summary.get("niche_governance")
        if isinstance(frozen, dict) and frozen:
            return
        try:
            digest = NicheContractDigestCompiler().compile(
                channel=channel,
                profile_version=profile,
                policy_snapshot=policy,
                category=category,
                editorial_slot=slot,
            )
        except NicheContractCompilationError as exc:
            raise ValidationFailureError("CADENCE_NICHE_AUTHORITY_NOT_PASS") from exc
        digest_ref = {
            "type": "niche_contract_digest",
            "ref": digest.editorial_slot_ref + "#niche_contract_digest",
            "content_hash": digest.content_hash,
        }
        summary["niche_governance"] = {
            "channel_id": str(channel.id),
            "niche_contract_digest": digest.model_dump(mode="json"),
            "niche_contract_digest_ref": digest_ref,
            "topic": project.title,
        }
        project.audience_delivery_summary = summary
        self.session.flush()

    def _resolve_category(
        self,
        candidate: EditorialIdeaCandidate,
        preflight: IdeaMarketPreflight,
    ) -> ContentCategory:
        category_id: uuid.UUID | None = None
        if preflight.content_category_ref:
            try:
                category_id = uuid.UUID(
                    preflight.content_category_ref.rsplit("/", 1)[-1]
                )
            except ValueError:
                category_id = None
        statement = select(ContentCategory).where(
            ContentCategory.company_id == candidate.company_id,
            ContentCategory.channel_workspace_id == candidate.channel_workspace_id,
            ContentCategory.status == "ACTIVE",
        )
        categories = list(self.session.scalars(statement).all())
        if category_id is not None:
            categories = [item for item in categories if item.id == category_id]
        if len(categories) != 1:
            raise ValidationFailureError("CADENCE_CATEGORY_SCOPE_AMBIGUOUS")
        return categories[0]


def _preflight_demand_authority_valid(preflight: IdeaMarketPreflight) -> bool:
    """Reject legacy/mixed preflights from the current cadence path.

    Historical preflights remain readable, but only v3 evidence blobs with a
    real quantitative pass or an active first-launch exception are eligible to
    start the long-form cadence.
    """

    blob = preflight.evidence_blob or {}
    claim_refs = blob.get("claim_evidence_refs")
    demand_state = blob.get("demand_state")
    demand_type = blob.get("demand_authority_type")
    if not isinstance(claim_refs, list) or not claim_refs:
        return False
    if demand_state == "PASS" and demand_type == "QUANTITATIVE_DEMAND":
        refs = blob.get("market_demand_evidence_refs")
        return isinstance(refs, list) and bool(refs)
    if demand_state == "EXPERIMENT_AUTHORIZED" and demand_type == "FIRST_LAUNCH_EXPERIMENT":
        return preflight.demand_score is None and preflight.market_fit_score is None
    return False


def _preflight_has_active_demand_authority(
    session: Session,
    *,
    candidate_id: uuid.UUID,
) -> bool:
    preflights = session.scalars(
        select(IdeaMarketPreflight)
        .where(
            IdeaMarketPreflight.editorial_idea_candidate_id == candidate_id,
            IdeaMarketPreflight.decision == "PASS",
            IdeaMarketPreflight.policy_fit_state == "PASS",
        )
        .order_by(IdeaMarketPreflight.created_at.desc())
    ).all()
    return any(_preflight_demand_authority_valid(item) for item in preflights)


class LaunchDashboardService:
    def __init__(self, session: Session):
        self.session = session

    def read(self, launch_run_id: uuid.UUID) -> LaunchDashboardRead:
        run = self.session.get(LaunchRun, launch_run_id)
        if run is None:
            raise NotFoundError(f"launch run not found: {launch_run_id}")
        runway = LaunchRunwayService(self.session).project(run.id)
        slots = list(
            self.session.scalars(
                select(LongFormPublishSlot)
                .where(
                    LongFormPublishSlot.launch_run_id == run.id,
                    LongFormPublishSlot.state == "OPEN",
                    LongFormPublishSlot.intended_publish_at > utc_now(),
                )
                .order_by(LongFormPublishSlot.intended_publish_at)
            ).all()
        )
        next_slot = slots[0] if slots else None
        latest = self.session.scalar(
            select(CadenceEvaluationReceipt)
            .where(CadenceEvaluationReceipt.launch_run_id == run.id)
            .order_by(CadenceEvaluationReceipt.created_at.desc())
        )
        series = list(
            self.session.execute(
                select(
                    SeriesRun.id,
                    SeriesRun.series_plan_id,
                    SeriesRun.state,
                    SeriesRun.run_number,
                ).where(
                    SeriesRun.channel_workspace_id == run.channel_workspace_id,
                    SeriesRun.state.in_(["ACTIVE", "SCHEDULED"]),
                )
            ).mappings()
        )
        published = runway.counts.published_videos
        phase = (
            "AUDIENCE_PROMISE"
            if published < 3
            else "SERIES_PACKAGING"
            if published < 7
            else "ALLOCATION_PREPARATION"
            if published < 10
            else "STEADY_STATE"
        )
        blockers = (
            list(latest.reason_codes)
            if latest
            and latest.decision != CadenceDecision.START_LONG_FORM_PRODUCTION.value
            else []
        )
        next_action = (
            "Theo dõi sản xuất long-form đang chạy."
            if latest
            and latest.decision == CadenceDecision.START_LONG_FORM_PRODUCTION.value
            else "Khắc phục điều kiện cadence được nêu trong blockers."
            if blockers
            else "Đánh giá cadence khi vào cửa sổ sản xuất kế tiếp."
        )
        qualification_summary = self._qualification_summary(run)
        return LaunchDashboardRead(
            launch_run=LaunchRunRead.model_validate(run),
            launch_day=max(1, (date.today() - run.preparation_started_on).days + 1),
            runway=runway,
            active_series=[dict(item) for item in series],
            videos_published=published,
            next_publish_slot=LongFormPublishSlotRead.model_validate(next_slot)
            if next_slot
            else None,
            next_production_start_window={
                "opens_at": next_slot.target_start_window_open_at,
                "closes_at": next_slot.target_start_window_close_at,
            }
            if next_slot
            else None,
            latest_cadence_evaluation=CadenceEvaluationRead.model_validate(latest)
            if latest
            else None,
            current_experiment_phase=phase,
            blockers=blockers,
            next_action=next_action,
            qualification_summary=qualification_summary,
        )

    def _qualification_summary(self, run: LaunchRun) -> dict[str, Any]:
        """Expose recovery state through the existing launch dashboard."""

        qualifications = list(
            self.session.scalars(
                select(ScriptQualificationRun)
                .where(ScriptQualificationRun.launch_run_id == run.id)
                .order_by(ScriptQualificationRun.created_at.desc())
            ).all()
        )
        reservation_counts = dict(
            self.session.execute(
                select(
                    SeriesEpisodeReservation.state,
                    func.count(SeriesEpisodeReservation.id),
                )
                .join(
                    ScriptQualificationRun,
                    ScriptQualificationRun.id
                    == SeriesEpisodeReservation.script_qualification_run_id,
                )
                .where(ScriptQualificationRun.launch_run_id == run.id)
                .group_by(SeriesEpisodeReservation.state)
            ).all()
        )
        receipts = {
            item.script_qualification_run_id: item
            for item in self.session.scalars(
                select(ScriptQualificationReceipt).where(
                    ScriptQualificationReceipt.script_qualification_run_id.in_(
                        [item.id for item in qualifications] or [uuid.uuid4()]
                    )
                )
            ).all()
        }
        retry_counts = dict(
            self.session.execute(
                select(DomainEvent.aggregate_id, DomainEvent.attempt_count).where(
                    DomainEvent.aggregate_id.in_([item.id for item in qualifications] or [uuid.uuid4()]),
                    DomainEvent.event_type == "script_qualification.execute.v1",
                )
            ).all()
        )
        active_states = {
            "RESERVED",
            "WRITER_DISPATCHED",
            "SCRIPT_GENERATED",
            "STRUCTURAL_CHECKED",
            "CLAIM_INVENTORY_CHECKED",
            "GROUNDING_CHECKED",
            "VERIFIER_DISPATCHED",
            "EDITORIAL_CHECKED",
            "MEMORY_CHECKED",
        }
        active = [item for item in qualifications if item.state in active_states]
        blocked = [
            item
            for item in qualifications
            if item.state in {"BLOCKED_NON_REPAIRABLE", "BLOCKED_REPAIR_BUDGET_EXHAUSTED"}
        ]
        reconciliation = [
            item
            for item in blocked
            if "SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY"
            in ((item.failure_receipt or {}).get("reason_codes") or [])
        ]
        stuck_slots = list(
            self.session.scalars(
                select(LongFormPublishSlot).where(
                    LongFormPublishSlot.launch_run_id == run.id,
                    LongFormPublishSlot.state == "QUALIFICATION_RESERVED",
                    LongFormPublishSlot.target_start_window_open_at < utc_now(),
                )
            ).all()
        )

        def _row(item: ScriptQualificationRun) -> dict[str, Any]:
            receipt = receipts.get(item.id)
            return {
                "run_id": str(item.id),
                "state": item.state,
                "receipt_id": str(receipt.id) if receipt else None,
                "receipt_hash": receipt.content_hash if receipt else None,
                "finalization_retry_count": int(retry_counts.get(item.id, 0)),
                "next_operator_action": (
                    "Reconcile provider outcome with evidence."
                    if item in reconciliation
                    else "Wait for deterministic finalization retry."
                    if item.state == "QUALIFIED" and retry_counts.get(item.id, 0)
                    else "No operator action required."
                ),
            }

        return {
            "active_runs": [_row(item) for item in active],
            "blocked_runs": [_row(item) for item in blocked],
            "reconciliation_required_runs": [_row(item) for item in reconciliation],
            "stuck_slot_ids": [str(item.id) for item in stuck_slots],
            "reservation_counts": {
                state: int(reservation_counts.get(state, 0))
                for state in (
                    "RESERVED",
                    "CONSUMED",
                    "RELEASED",
                    "ABANDONED_AFTER_ADMISSION",
                )
            },
        }
