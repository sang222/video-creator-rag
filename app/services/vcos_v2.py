"""Phase 2 typed planning, assignment, and atomic admission services.

The resolver in this module is pure and deterministic.  Database mutation is
kept in the service layer so the selected SeriesRun can be revalidated under a
row lock immediately before an episode is reserved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.vcos_v2 import (
    ASSIGNMENT_RESOLVER_VERSION,
    AssignmentCandidate,
    AssignmentMode,
    AssignmentReasonCode,
    AssignmentResolution,
    AssignmentResolverInput,
    ContentMode,
    DerivativeLineageInput,
    LegacySeriesClassification,
    LongFormPlanningRequest,
    PlanningSourceType,
    ProductionLane,
    ProjectAdmissionV2Request,
    SeriesPlanCreate,
    SeriesPlanState,
    SeriesPlanTransitionRequest,
    SeriesRunCreate,
    SeriesRunState,
    SeriesRunTransitionRequest,
)
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m5 import (
    ChannelDailyRun,
    DailyIdeaDecision,
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
)
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.workflow import VideoProjectService


FaultHook = Callable[[str], None]


class AssignmentResolutionError(ValidationFailureError):
    """Structured fail-closed resolver outcome used to persist a BLOCK receipt."""

    def __init__(self, *reason_codes: str | AssignmentReasonCode):
        normalized = [str(code) for code in reason_codes]
        super().__init__(", ".join(normalized))
        self.reason_codes = normalized


@dataclass(frozen=True)
class _AdmissionContext:
    workspace: ChannelWorkspace
    profile: ChannelProfileVersion
    policy: CompiledChannelPolicySnapshot
    slot: EditorialCalendarSlot | None
    daily_run: ChannelDailyRun | None
    daily_idea: DailyIdeaDecision | None
    preflight: IdeaMarketPreflight | None


class DeterministicAssignmentResolver:
    """Versioned assignment policy with an order-independent stable tie-break."""

    version = ASSIGNMENT_RESOLVER_VERSION

    def resolve(self, data: AssignmentResolverInput) -> AssignmentResolution:
        input_hash = self.input_hash(data)
        if not data.niche_gate_passed:
            raise AssignmentResolutionError("NICHE_GATE_NOT_PASS")
        if not data.market_gate_passed:
            raise AssignmentResolutionError("MARKET_GATE_NOT_PASS")

        if data.production_lane == ProductionLane.LONG_DERIVED_SHORT:
            return self._standalone(
                data,
                input_hash=input_hash,
                reason=AssignmentReasonCode.LONG_DERIVATIVE_AVAILABLE,
            )
        if data.assignment_mode == AssignmentMode.STANDALONE_REQUIRED:
            return self._standalone(
                data,
                input_hash=input_hash,
                reason=AssignmentReasonCode.EXPLICIT_STANDALONE_REQUIRED,
            )

        exact = self._exact_preferred(data)
        eligible = [
            candidate
            for candidate in data.candidates
            if self._ineligibility_reason(candidate) is None
        ]

        if data.assignment_mode == AssignmentMode.SERIES_REQUIRED:
            if (
                data.preferred_series_plan_id is None
                or data.preferred_series_run_id is None
                or exact is None
            ):
                raise AssignmentResolutionError(
                    AssignmentReasonCode.SERIES_BINDING_INVALID
                )
            invalid_reason = self._ineligibility_reason(exact)
            if invalid_reason is not None:
                raise AssignmentResolutionError(
                    AssignmentReasonCode.EXPLICIT_SERIES_REQUIRED,
                    invalid_reason,
                )
            return self._series(
                data,
                exact,
                input_hash=input_hash,
                primary_reason=AssignmentReasonCode.EXPLICIT_SERIES_REQUIRED,
            )

        if data.assignment_mode == AssignmentMode.SERIES_PREFERRED:
            selected = (
                exact
                if exact is not None and self._ineligibility_reason(exact) is None
                else self._winner(eligible)
            )
            if selected is not None and not data.bridge_or_special:
                return self._series(
                    data,
                    selected,
                    input_hash=input_hash,
                    primary_reason=(
                        AssignmentReasonCode.MANDATORY_NEXT_EPISODE
                        if selected.mandatory_next_episode
                        else AssignmentReasonCode.SERIES_PREFERRED_SELECTED
                    ),
                )
            reason = self._fallback_reason(data.candidates)
            if data.bridge_or_special:
                reason = AssignmentReasonCode.BRIDGE_OR_SPECIAL
            return self._standalone(data, input_hash=input_hash, reason=reason)

        # OPEN_MIX: mandatory obligations beat optional topical opportunities.
        mandatory = [item for item in eligible if item.mandatory_next_episode]
        if mandatory:
            return self._series(
                data,
                self._winner(mandatory),
                input_hash=input_hash,
                primary_reason=AssignmentReasonCode.MANDATORY_NEXT_EPISODE,
            )
        if data.bridge_or_special:
            return self._standalone(
                data,
                input_hash=input_hash,
                reason=AssignmentReasonCode.BRIDGE_OR_SPECIAL,
            )
        if data.timely_niche_opportunity:
            return self._standalone(
                data,
                input_hash=input_hash,
                reason=AssignmentReasonCode.TIMELY_NICHE_OPPORTUNITY,
            )
        winner = self._winner(eligible)
        if winner is not None:
            return self._series(
                data,
                winner,
                input_hash=input_hash,
                primary_reason=AssignmentReasonCode.OPEN_MIX_SERIES_SELECTED,
            )
        return self._standalone(
            data,
            input_hash=input_hash,
            reason=self._fallback_reason(data.candidates),
        )

    @staticmethod
    def input_hash(data: AssignmentResolverInput) -> str:
        payload = data.model_dump(mode="json")
        payload["candidates"] = sorted(
            payload["candidates"], key=lambda candidate: candidate["series_run_id"]
        )
        return content_hash(payload)

    @staticmethod
    def _exact_preferred(
        data: AssignmentResolverInput,
    ) -> AssignmentCandidate | None:
        for candidate in data.candidates:
            if (
                candidate.series_plan_id == data.preferred_series_plan_id
                and candidate.series_run_id == data.preferred_series_run_id
            ):
                return candidate
        return None

    @staticmethod
    def _ineligibility_reason(
        candidate: AssignmentCandidate,
    ) -> AssignmentReasonCode | None:
        if candidate.plan_state == SeriesPlanState.SUPERSEDED:
            return AssignmentReasonCode.SERIES_PLAN_SUPERSEDED
        if candidate.plan_state != SeriesPlanState.APPROVED:
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        if candidate.run_state != SeriesRunState.ACTIVE:
            return AssignmentReasonCode.SERIES_RUN_NOT_ACTIVE
        if not candidate.schedule_eligible:
            return AssignmentReasonCode.SERIES_SCHEDULE_INELIGIBLE
        if not candidate.has_capacity:
            return AssignmentReasonCode.SERIES_CAPACITY_EXHAUSTED
        if candidate.coherence_score <= 0:
            return AssignmentReasonCode.SERIES_COHERENCE_FAILED
        return None

    @staticmethod
    def _winner(
        candidates: Iterable[AssignmentCandidate],
    ) -> AssignmentCandidate | None:
        candidates = list(candidates)
        if not candidates:
            return None
        # UUID string is the final, stable ascending tie-break.  Every preceding
        # component is explicit and versioned by ASSIGNMENT_RESOLVER_VERSION.
        return sorted(
            candidates,
            key=lambda item: (
                -int(item.mandatory_next_episode),
                -item.explicit_slot_priority,
                -item.schedule_obligation,
                -item.priority,
                -item.coherence_score,
                -(item.capacity - item.reserved_episode_count),
                -item.niche_opportunity_value,
                item.recent_repetition_penalty,
                str(item.series_run_id),
            ),
        )[0]

    @staticmethod
    def _fallback_reason(
        candidates: Iterable[AssignmentCandidate],
    ) -> AssignmentReasonCode:
        reasons = [
            DeterministicAssignmentResolver._ineligibility_reason(item)
            for item in candidates
        ]
        for priority in (
            AssignmentReasonCode.SERIES_CAPACITY_EXHAUSTED,
            AssignmentReasonCode.SERIES_COHERENCE_FAILED,
            AssignmentReasonCode.SERIES_SCHEDULE_INELIGIBLE,
            AssignmentReasonCode.SERIES_PLAN_SUPERSEDED,
            AssignmentReasonCode.SERIES_RUN_NOT_ACTIVE,
            AssignmentReasonCode.SERIES_BINDING_INVALID,
        ):
            if priority in reasons:
                return priority
        return AssignmentReasonCode.NO_ELIGIBLE_SERIES

    def _series(
        self,
        data: AssignmentResolverInput,
        candidate: AssignmentCandidate | None,
        *,
        input_hash: str,
        primary_reason: AssignmentReasonCode,
    ) -> AssignmentResolution:
        if candidate is None:
            raise AssignmentResolutionError(
                AssignmentReasonCode.NO_ELIGIBLE_SERIES
            )
        reasons = [primary_reason]
        if candidate.mandatory_next_episode and primary_reason not in reasons:
            reasons.append(AssignmentReasonCode.MANDATORY_NEXT_EPISODE)
        return AssignmentResolution(
            resolver_version=self.version,
            resolver_input_hash=input_hash,
            production_lane=data.production_lane,
            assignment_mode=data.assignment_mode,
            content_mode=ContentMode.SERIES_EPISODE,
            series_plan_id=candidate.series_plan_id,
            series_run_id=candidate.series_run_id,
            episode_number=candidate.next_episode_number,
            episode_role=candidate.episode_role,
            reason_codes=reasons,
        )

    def _standalone(
        self,
        data: AssignmentResolverInput,
        *,
        input_hash: str,
        reason: AssignmentReasonCode,
    ) -> AssignmentResolution:
        return AssignmentResolution(
            resolver_version=self.version,
            resolver_input_hash=input_hash,
            production_lane=data.production_lane,
            assignment_mode=data.assignment_mode,
            content_mode=ContentMode.STANDALONE,
            standalone_reason_code=reason,
            reason_codes=[reason],
        )


class SeriesPlanService:
    _TRANSITIONS: dict[SeriesPlanState, frozenset[SeriesPlanState]] = {
        SeriesPlanState.DRAFT: frozenset(
            {SeriesPlanState.APPROVED, SeriesPlanState.ARCHIVED}
        ),
        SeriesPlanState.APPROVED: frozenset(
            {SeriesPlanState.SUPERSEDED, SeriesPlanState.ARCHIVED}
        ),
        SeriesPlanState.SUPERSEDED: frozenset({SeriesPlanState.ARCHIVED}),
        SeriesPlanState.ARCHIVED: frozenset(),
    }

    def __init__(self, session: Session):
        self.session = session

    def create(self, data: SeriesPlanCreate) -> SeriesPlan:
        self._validate_binding(data)
        if data.supersedes_series_plan_id is not None:
            previous = self.session.get(
                SeriesPlan, data.supersedes_series_plan_id
            )
            if previous is None:
                raise NotFoundError(
                    f"series plan not found: {data.supersedes_series_plan_id}"
                )
            if (
                previous.company_id != data.company_id
                or previous.channel_workspace_id != data.channel_workspace_id
                or previous.stable_series_key != data.stable_series_key
                or data.version != previous.version + 1
            ):
                raise ValidationFailureError(
                    "superseding plan must preserve scope/key and increment version"
                )
        plan = SeriesPlan(
            **data.model_dump(
                exclude={"allowed_production_lanes"}, mode="python"
            ),
            allowed_production_lanes=[
                str(lane) for lane in data.allowed_production_lanes
            ],
            state=SeriesPlanState.DRAFT,
            state_reason_codes=["SERIES_PLAN_DRAFT_CREATED"],
        )
        self.session.add(plan)
        self.session.flush()
        return plan

    def transition(
        self, plan_id: uuid.UUID, data: SeriesPlanTransitionRequest
    ) -> SeriesPlan:
        plan = self.session.scalar(
            select(SeriesPlan)
            .where(SeriesPlan.id == plan_id)
            .with_for_update()
        )
        if plan is None:
            raise NotFoundError(f"series plan not found: {plan_id}")
        current = SeriesPlanState(plan.state)
        if data.target_state not in self._TRANSITIONS[current]:
            raise ConflictError(
                f"invalid SeriesPlan transition {current} -> {data.target_state}"
            )
        if data.target_state == SeriesPlanState.APPROVED:
            if not data.evidence_refs:
                raise ValidationFailureError(
                    "SeriesPlan approval requires approval evidence"
                )
            if plan.supersedes_series_plan_id is not None:
                previous = self.session.scalar(
                    select(SeriesPlan)
                    .where(SeriesPlan.id == plan.supersedes_series_plan_id)
                    .with_for_update()
                )
                if previous is None or previous.state != SeriesPlanState.APPROVED:
                    raise ConflictError(
                        "superseded SeriesPlan must currently be APPROVED"
                    )
                previous.state = SeriesPlanState.SUPERSEDED
                previous.state_reason_codes = ["SERIES_PLAN_VERSION_SUPERSEDED"]
            plan.approved_by_user_id = data.actor_user_id
            plan.approved_at = utc_now()
            plan.approval_evidence_refs = list(data.evidence_refs)
        plan.state = data.target_state
        plan.state_reason_codes = list(data.reason_codes)
        self.session.flush()
        return plan

    def _validate_binding(self, data: SeriesPlanCreate) -> None:
        workspace = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        if workspace is None:
            raise NotFoundError(
                f"channel workspace not found: {data.channel_workspace_id}"
            )
        if workspace.company_id != data.company_id:
            raise ValidationFailureError(
                "SeriesPlan workspace does not belong to company"
            )
        profile = self.session.get(
            ChannelProfileVersion, data.channel_profile_version_id
        )
        if (
            profile is None
            or profile.channel_workspace_id != data.channel_workspace_id
        ):
            raise ValidationFailureError(
                "SeriesPlan profile does not belong to workspace"
            )
        policy = self.session.get(
            CompiledChannelPolicySnapshot, data.policy_snapshot_id
        )
        if (
            policy is None
            or policy.channel_workspace_id != data.channel_workspace_id
            or policy.channel_profile_version_id
            != data.channel_profile_version_id
        ):
            raise ValidationFailureError(
                "SeriesPlan policy/profile binding is invalid"
            )


class SeriesRunService:
    _TRANSITIONS: dict[SeriesRunState, frozenset[SeriesRunState]] = {
        SeriesRunState.PROPOSED: frozenset(
            {
                SeriesRunState.APPROVED,
                SeriesRunState.CANCELED,
                SeriesRunState.ARCHIVED,
            }
        ),
        SeriesRunState.APPROVED: frozenset(
            {
                SeriesRunState.SCHEDULED,
                SeriesRunState.ACTIVE,
                SeriesRunState.CANCELED,
                SeriesRunState.ARCHIVED,
            }
        ),
        SeriesRunState.SCHEDULED: frozenset(
            {
                SeriesRunState.ACTIVE,
                SeriesRunState.PAUSED,
                SeriesRunState.CANCELED,
                SeriesRunState.ARCHIVED,
            }
        ),
        SeriesRunState.ACTIVE: frozenset(
            {
                SeriesRunState.PAUSED,
                SeriesRunState.COMPLETION_PENDING,
                SeriesRunState.CANCELED,
            }
        ),
        SeriesRunState.PAUSED: frozenset(
            {
                SeriesRunState.ACTIVE,
                SeriesRunState.CANCELED,
                SeriesRunState.ARCHIVED,
            }
        ),
        SeriesRunState.COMPLETION_PENDING: frozenset(
            {
                SeriesRunState.ACTIVE,
                SeriesRunState.COMPLETED,
                SeriesRunState.CANCELED,
            }
        ),
        SeriesRunState.COMPLETED: frozenset({SeriesRunState.ARCHIVED}),
        SeriesRunState.CANCELED: frozenset({SeriesRunState.ARCHIVED}),
        SeriesRunState.ARCHIVED: frozenset(),
    }

    def __init__(self, session: Session):
        self.session = session

    def create(self, data: SeriesRunCreate) -> SeriesRun:
        plan = self.session.get(SeriesPlan, data.series_plan_id)
        if plan is None:
            raise NotFoundError(f"series plan not found: {data.series_plan_id}")
        if plan.state != SeriesPlanState.APPROVED:
            raise ValidationFailureError(
                "SeriesRun requires an APPROVED SeriesPlan"
            )
        run = SeriesRun(
            series_plan_id=plan.id,
            company_id=plan.company_id,
            channel_workspace_id=plan.channel_workspace_id,
            channel_profile_version_id=plan.channel_profile_version_id,
            policy_snapshot_id=plan.policy_snapshot_id,
            run_key=data.run_key,
            run_number=data.run_number,
            capacity=data.capacity,
            first_episode_number=data.first_episode_number,
            next_episode_number=data.first_episode_number,
            reserved_episode_count=0,
            published_episode_count=0,
            priority=data.priority,
            schedule_window_start=data.schedule_window_start,
            schedule_window_end=data.schedule_window_end,
            state=SeriesRunState.PROPOSED,
            state_reason_codes=["SERIES_RUN_PROPOSED"],
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def transition(
        self, run_id: uuid.UUID, data: SeriesRunTransitionRequest
    ) -> SeriesRun:
        run = self.session.scalar(
            select(SeriesRun).where(SeriesRun.id == run_id).with_for_update()
        )
        if run is None:
            raise NotFoundError(f"series run not found: {run_id}")
        current = SeriesRunState(run.state)
        if data.target_state not in self._TRANSITIONS[current]:
            raise ConflictError(
                f"invalid SeriesRun transition {current} -> {data.target_state}"
            )
        plan = self.session.get(SeriesPlan, run.series_plan_id)
        if data.target_state in {
            SeriesRunState.APPROVED,
            SeriesRunState.SCHEDULED,
            SeriesRunState.ACTIVE,
        } and (plan is None or plan.state != SeriesPlanState.APPROVED):
            raise ConflictError(
                "SeriesRun cannot advance without an APPROVED SeriesPlan"
            )
        now = utc_now()
        if data.target_state == SeriesRunState.APPROVED:
            run.approved_by_user_id = data.actor_user_id
            run.approved_at = now
        elif data.target_state == SeriesRunState.ACTIVE:
            if run.reserved_episode_count >= run.capacity:
                raise ConflictError("capacity-exhausted SeriesRun cannot activate")
            run.activated_at = now
        elif data.target_state == SeriesRunState.COMPLETION_PENDING:
            run.completion_pending_at = now
        elif data.target_state == SeriesRunState.COMPLETED:
            if run.published_episode_count < run.reserved_episode_count:
                raise ConflictError(
                    "SeriesRun cannot complete before reserved episodes are published"
                )
            run.completed_at = now
        run.state = data.target_state
        run.state_reason_codes = list(data.reason_codes)
        self.session.flush()
        return run


class DerivativeLineageValidator:
    def __init__(self, session: Session):
        self.session = session

    def validate(
        self,
        *,
        data: DerivativeLineageInput,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
    ) -> VideoProject:
        parent = self.session.get(VideoProject, data.parent_video_project_id)
        if parent is None:
            raise NotFoundError(
                f"parent video project not found: {data.parent_video_project_id}"
            )
        if (
            parent.company_id != company_id
            or parent.channel_workspace_id != channel_workspace_id
        ):
            raise ValidationFailureError(
                "derivative parent does not belong to admission scope"
            )
        if parent.status != "approved":
            # V2 replaces blanket pre-render human approval with the exact,
            # immutable automated readiness receipt.  Accept that authority
            # as the typed equivalent once the parent timeline is frozen.
            from app.services.production_package import (
                ProductionPackageService,
            )

            try:
                ProductionPackageService(
                    self.session
                ).require_ready_projection_authority(project_id=parent.id)
            except (NotFoundError, ValidationFailureError) as exc:
                raise ValidationFailureError(
                    "LONG_DERIVED_SHORT requires an approved or "
                    "READY_FOR_PRODUCTION parent"
                ) from exc
        if (
            parent.schema_version != "v2"
            or parent.production_lane != ProductionLane.LONG_FORM
            or parent.project_admission_decision_id is None
        ):
            raise ValidationFailureError(
                "LONG_DERIVED_SHORT requires an admitted typed LONG_FORM parent"
            )
        parent_admission = self.session.get(
            ProjectAdmissionDecision, parent.project_admission_decision_id
        )
        if (
            parent_admission is None
            or parent_admission.schema_version != "v2"
            or parent_admission.decision != "ADMIT"
            or parent_admission.production_lane != ProductionLane.LONG_FORM
            or parent_admission.admitted_video_project_id != parent.id
        ):
            raise ValidationFailureError(
                "parent project admission authority is missing or invalid"
            )
        parent_preflight = (
            self.session.get(
                IdeaMarketPreflight, parent_admission.idea_market_preflight_id
            )
            if parent_admission.idea_market_preflight_id is not None
            else None
        )
        if (
            parent_preflight is None
            or parent_preflight.policy_fit_state != "PASS"
            or parent_preflight.decision != "PASS"
        ):
            raise ValidationFailureError(
                "derivative parent lacks inherited PASS niche/market authority"
            )
        if (
            parent.canonical_timeline_ref != data.canonical_timeline_ref
            or parent.canonical_timeline_hash != data.canonical_timeline_hash
        ):
            raise ValidationFailureError(
                "derivative canonical timeline does not exactly match parent"
            )
        current_final_media = self.session.scalars(
            select(FinalMediaRef)
            .where(
                FinalMediaRef.video_project_id == parent.id,
                FinalMediaRef.company_id == company_id,
                FinalMediaRef.channel_workspace_id == channel_workspace_id,
            )
            .order_by(
                FinalMediaRef.created_at.desc(),
                FinalMediaRef.id.desc(),
            )
        ).first()
        if current_final_media is not None and (
            data.parent_final_media_ref_id != current_final_media.id
        ):
            raise ValidationFailureError(
                "parent_final_media_ref_id must bind current parent media"
            )
        if (
            data.parent_final_media_ref_id is not None
            and current_final_media is None
        ):
            raise ValidationFailureError(
                "parent_final_media_ref_id is not exact parent media"
            )
        return parent


class ProjectAdmissionV2Service:
    def __init__(
        self,
        session: Session,
        *,
        resolver: DeterministicAssignmentResolver | None = None,
        fault_hook: FaultHook | None = None,
    ):
        self.session = session
        self.resolver = resolver or DeterministicAssignmentResolver()
        self._fault_hook = fault_hook

    def create_decision(
        self,
        *,
        data: ProjectAdmissionV2Request,
        correlation_id: str = "vcos-v2-project-admission",
    ) -> ProjectAdmissionDecision:
        del correlation_id  # Event emission remains owned by existing services.
        data, context = self._load_context(data)
        existing = self._lock_source_and_existing(data)
        if existing is not None:
            return existing

        gate_reasons = self._gate_reasons(data, context)
        resolver_input = self._resolver_input(data, context)
        if gate_reasons:
            return self._persist_block(
                data=data,
                context=context,
                resolver_input=resolver_input,
                reason_codes=gate_reasons,
            )
        try:
            resolution = self.resolver.resolve(resolver_input)
        except AssignmentResolutionError as exc:
            return self._persist_block(
                data=data,
                context=context,
                resolver_input=resolver_input,
                reason_codes=exc.reason_codes,
            )
        if resolution.content_mode == ContentMode.SERIES_EPISODE:
            return self._admit_series(
                data=data,
                context=context,
                resolver_input=resolver_input,
                resolution=resolution,
            )
        return self._admit_standalone(
            data=data,
            context=context,
            resolver_input=resolver_input,
            resolution=resolution,
        )

    def _load_context(
        self, data: ProjectAdmissionV2Request
    ) -> tuple[ProjectAdmissionV2Request, _AdmissionContext]:
        workspace = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        if workspace is None:
            raise NotFoundError(
                f"channel workspace not found: {data.channel_workspace_id}"
            )
        if workspace.company_id != data.company_id:
            raise ValidationFailureError(
                "admission workspace does not belong to company"
            )
        profile = self.session.get(
            ChannelProfileVersion, data.channel_profile_version_id
        )
        if (
            profile is None
            or profile.channel_workspace_id != data.channel_workspace_id
        ):
            raise ValidationFailureError(
                "admission profile does not belong to workspace"
            )
        policy = self.session.get(
            CompiledChannelPolicySnapshot, data.policy_snapshot_id
        )
        if (
            policy is None
            or policy.channel_workspace_id != data.channel_workspace_id
            or policy.channel_profile_version_id
            != data.channel_profile_version_id
        ):
            raise ValidationFailureError(
                "admission profile/policy binding is invalid"
            )
        if (
            policy.status != "active"
            or workspace.active_policy_snapshot_id != policy.id
        ):
            raise ValidationFailureError(
                "admission policy snapshot must be active"
            )
        # The request may carry the frozen contract, but it is not authority.
        # Resolve the exact approved profile/policy values before any project,
        # episode reservation, or immutable admission receipt can be created.
        from app.services.production_package import (
            ChannelDurationContractResolver,
        )

        authoritative_duration = ChannelDurationContractResolver(
            self.session
        ).resolve(
            profile_version_id=profile.id,
            policy_snapshot_id=policy.id,
            production_lane=data.production_lane,
        )
        if (
            authoritative_duration.model_dump(mode="json")
            != data.duration_contract.model_dump(mode="json")
        ):
            raise ValidationFailureError(
                "ADMISSION_DURATION_CONTRACT_MISMATCH"
            )

        daily_run = (
            self.session.get(ChannelDailyRun, data.channel_daily_run_id)
            if data.channel_daily_run_id is not None
            else None
        )
        daily_idea = (
            self.session.get(DailyIdeaDecision, data.daily_idea_decision_id)
            if data.daily_idea_decision_id is not None
            else None
        )
        slot_id = data.editorial_calendar_slot_id
        if daily_run is not None:
            if (
                daily_run.company_id != data.company_id
                or daily_run.channel_workspace_id != data.channel_workspace_id
                or daily_run.policy_snapshot_id != data.policy_snapshot_id
            ):
                raise ValidationFailureError(
                    "daily run does not match admission scope"
                )
            authoritative_slot_id = daily_run.editorial_calendar_slot_id
            if authoritative_slot_id is None:
                raise ValidationFailureError(
                    "typed daily admission requires a frozen editorial slot"
                )
            if slot_id is not None and slot_id != authoritative_slot_id:
                raise ValidationFailureError(
                    "DAILY_ADMISSION_EDITORIAL_SLOT_MISMATCH"
                )
            slot_id = authoritative_slot_id
        if data.planning_source_type == PlanningSourceType.DAILY_IDEA:
            if daily_run is None or daily_idea is None:
                raise NotFoundError("daily admission source was not found")
            if (
                daily_idea.channel_daily_run_id != daily_run.id
                or daily_idea.company_id != data.company_id
                or daily_idea.channel_workspace_id != data.channel_workspace_id
                or daily_idea.policy_snapshot_id != data.policy_snapshot_id
            ):
                raise ValidationFailureError(
                    "daily idea does not match daily run/admission scope"
                )
            if (
                daily_idea.schema_version != "v2"
                or daily_idea.production_lane != ProductionLane.DAILY_SHORT
            ):
                raise ValidationFailureError(
                    "typed daily admission requires a frozen v2 DAILY_SHORT idea"
                )
            if (
                daily_run.daily_idea_decision_id is not None
                and daily_run.daily_idea_decision_id != daily_idea.id
            ):
                raise ValidationFailureError(
                    "DAILY_ADMISSION_FROZEN_IDEA_MISMATCH"
                )
            assignment_input = (
                daily_idea.assignment_input_ref
                if isinstance(daily_idea.assignment_input_ref, dict)
                else {}
            )
            frozen_slot_id = assignment_input.get(
                "editorial_calendar_slot_id",
                assignment_input.get("slot_id"),
            )
            if (
                frozen_slot_id is None
                or str(frozen_slot_id) != str(slot_id)
            ):
                raise ValidationFailureError(
                    "DAILY_IDEA_FROZEN_SLOT_MISMATCH"
                )
            if data.title != daily_idea.proposed_title:
                raise ValidationFailureError(
                    "DAILY_ADMISSION_FROZEN_TITLE_MISMATCH"
                )

        slot = (
            self.session.get(EditorialCalendarSlot, slot_id)
            if slot_id is not None
            else None
        )
        if slot_id is not None and slot is None:
            raise NotFoundError(f"editorial slot not found: {slot_id}")
        if slot is not None:
            if (
                slot.company_id != data.company_id
                or slot.channel_workspace_id != data.channel_workspace_id
                or slot.policy_snapshot_id != data.policy_snapshot_id
            ):
                raise ValidationFailureError(
                    "editorial slot does not match admission scope"
                )
            if slot.schema_version != "v2":
                raise ValidationFailureError(
                    "typed admission requires a v2 editorial slot"
                )
            if slot.series_key:
                raise ValidationFailureError(
                    "v2 slot raw series_key cannot be assignment authority"
                )
            if (
                slot.production_lane != data.production_lane
                or slot.assignment_mode != data.assignment_mode
            ):
                raise ValidationFailureError(
                    "request lane/assignment must match frozen editorial slot"
                )
            if (
                data.preferred_series_plan_id is not None
                and data.preferred_series_plan_id
                != slot.preferred_series_plan_id
            ) or (
                data.preferred_series_run_id is not None
                and data.preferred_series_run_id != slot.preferred_series_run_id
            ):
                raise ValidationFailureError(
                    "request series preference conflicts with frozen slot"
                )
            if (
                data.planning_source_type == PlanningSourceType.DAILY_IDEA
                and data.category_id is not None
                and data.category_id != slot.category_id
            ):
                raise ValidationFailureError(
                    "DAILY_ADMISSION_FROZEN_CATEGORY_MISMATCH"
                )
            data = data.model_copy(
                update={
                    "editorial_calendar_slot_id": slot.id,
                    "preferred_series_plan_id": slot.preferred_series_plan_id,
                    "preferred_series_run_id": slot.preferred_series_run_id,
                    "category_id": (
                        slot.category_id
                        if data.planning_source_type
                        == PlanningSourceType.DAILY_IDEA
                        else data.category_id
                    ),
                }
            )
        if (
            data.planning_source_type == PlanningSourceType.LONG_FORM_PLAN
            and slot is None
        ):
            raise ValidationFailureError(
                "LONG_FORM planning requires a persisted v2 editorial slot"
            )

        preflight = (
            self.session.get(IdeaMarketPreflight, data.idea_market_preflight_id)
            if data.idea_market_preflight_id is not None
            else None
        )
        if data.idea_market_preflight_id is not None and preflight is None:
            raise NotFoundError(
                f"idea market preflight not found: {data.idea_market_preflight_id}"
            )
        if preflight is not None and (
            preflight.company_id != data.company_id
            or preflight.channel_workspace_id != data.channel_workspace_id
        ):
            raise ValidationFailureError(
                "idea market preflight does not match admission scope"
            )
        if (
            preflight is not None
            and data.planning_source_type == PlanningSourceType.DAILY_IDEA
            and (
                preflight.channel_daily_run_id != data.channel_daily_run_id
                or preflight.daily_idea_decision_id
                != data.daily_idea_decision_id
                or preflight.editorial_calendar_slot_id != slot_id
            )
        ):
            raise ValidationFailureError(
                "IDEA_MARKET_PREFLIGHT_DAILY_SOURCE_MISMATCH"
            )
        if (
            preflight is not None
            and data.planning_source_type
            == PlanningSourceType.LONG_FORM_PLAN
            and (
                slot is None
                or preflight.editorial_calendar_slot_id != slot.id
                or preflight.channel_daily_run_id is not None
                or preflight.daily_idea_decision_id is not None
            )
        ):
            raise ValidationFailureError(
                "IDEA_MARKET_PREFLIGHT_EDITORIAL_SLOT_MISMATCH"
            )
        if data.derivative_lineage is not None:
            DerivativeLineageValidator(self.session).validate(
                data=data.derivative_lineage,
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
            )
        return data, _AdmissionContext(
            workspace=workspace,
            profile=profile,
            policy=policy,
            slot=slot,
            daily_run=daily_run,
            daily_idea=daily_idea,
            preflight=preflight,
        )

    @staticmethod
    def _gate_reasons(
        data: ProjectAdmissionV2Request, context: _AdmissionContext
    ) -> list[str]:
        if data.production_lane == ProductionLane.LONG_DERIVED_SHORT:
            return []
        if context.preflight is None:
            return ["IDEA_MARKET_PREFLIGHT_REQUIRED"]
        reasons: list[str] = []
        if context.preflight.policy_fit_state != "PASS":
            reasons.append("NICHE_GATE_NOT_PASS")
        if context.preflight.decision != "PASS":
            reasons.append("MARKET_GATE_NOT_PASS")
        return reasons

    def _resolver_input(
        self, data: ProjectAdmissionV2Request, context: _AdmissionContext
    ) -> AssignmentResolverInput:
        gate_passed = not self._gate_reasons(data, context)
        return AssignmentResolverInput(
            production_lane=data.production_lane,
            assignment_mode=data.assignment_mode,
            preferred_series_plan_id=data.preferred_series_plan_id,
            preferred_series_run_id=data.preferred_series_run_id,
            candidates=self._candidates(data, context),
            niche_gate_passed=gate_passed,
            market_gate_passed=gate_passed,
            timely_niche_opportunity=data.timely_niche_opportunity,
            bridge_or_special=data.bridge_or_special,
            parent_video_project_id=(
                data.derivative_lineage.parent_video_project_id
                if data.derivative_lineage is not None
                else None
            ),
        )

    def _candidates(
        self, data: ProjectAdmissionV2Request, context: _AdmissionContext
    ) -> list[AssignmentCandidate]:
        if data.production_lane == ProductionLane.LONG_DERIVED_SHORT:
            return []
        rows = self.session.execute(
            select(SeriesRun, SeriesPlan)
            .join(SeriesPlan, SeriesPlan.id == SeriesRun.series_plan_id)
            .where(
                SeriesPlan.company_id == data.company_id,
                SeriesPlan.channel_workspace_id == data.channel_workspace_id,
                SeriesPlan.channel_profile_version_id
                == data.channel_profile_version_id,
                SeriesPlan.policy_snapshot_id == data.policy_snapshot_id,
            )
            .order_by(SeriesRun.id)
            .with_for_update(of=SeriesRun)
            .execution_options(populate_existing=True)
        ).all()
        envelope = (
            context.slot.operational_envelope
            if context.slot is not None
            and isinstance(context.slot.operational_envelope, dict)
            else {}
        )
        candidates: list[AssignmentCandidate] = []
        for run, plan in rows:
            if str(data.production_lane) not in (plan.allowed_production_lanes or []):
                continue
            try:
                plan_state = SeriesPlanState(plan.state)
                run_state = SeriesRunState(run.state)
            except ValueError:
                continue
            run_id = str(run.id)
            candidates.append(
                AssignmentCandidate(
                    series_plan_id=plan.id,
                    series_run_id=run.id,
                    production_lane=data.production_lane,
                    plan_state=plan_state,
                    run_state=run_state,
                    next_episode_number=run.next_episode_number,
                    capacity=run.capacity,
                    reserved_episode_count=run.reserved_episode_count,
                    priority=run.priority,
                    coherence_score=self._mapping_int(
                        envelope, "series_coherence_scores", run_id, 0
                    ),
                    schedule_eligible=self._schedule_eligible(
                        run=run,
                        slot=context.slot,
                    ),
                    mandatory_next_episode=run_id
                    in self._string_list(envelope.get("mandatory_series_run_ids")),
                    explicit_slot_priority=self._mapping_int(
                        envelope, "series_slot_priorities", run_id, 0
                    ),
                    schedule_obligation=self._mapping_int(
                        envelope, "series_schedule_obligations", run_id, 0
                    ),
                    recent_repetition_penalty=self._mapping_int(
                        envelope, "series_repetition_penalties", run_id, 0
                    ),
                    niche_opportunity_value=self._mapping_int(
                        envelope, "series_niche_opportunity_values", run_id, 0
                    ),
                    derivative_parent_available=False,
                    episode_role=(
                        data.episode_role
                        or (plan.episode_role_policy or {}).get(
                            "default_episode_role"
                        )
                    ),
                )
            )
        return candidates

    @staticmethod
    def _mapping_int(
        source: dict[str, Any],
        key: str,
        item_key: str,
        default: int,
    ) -> int:
        values = source.get(key)
        if not isinstance(values, dict):
            return default
        value = values.get(item_key, default)
        return int(value) if isinstance(value, (int, float)) else default

    @staticmethod
    def _string_list(value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item) for item in value}

    @staticmethod
    def _schedule_eligible(
        *,
        run: SeriesRun,
        slot: EditorialCalendarSlot | None,
    ) -> bool:
        if slot is None:
            return (
                run.schedule_window_start is None
                and run.schedule_window_end is None
            )
        slot_date = slot.slot_date
        if (
            run.schedule_window_start is not None
            and slot_date < run.schedule_window_start.date()
        ):
            return False
        if (
            run.schedule_window_end is not None
            and slot_date > run.schedule_window_end.date()
        ):
            return False
        return True

    def _admit_series(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
        resolver_input: AssignmentResolverInput,
        resolution: AssignmentResolution,
    ) -> ProjectAdmissionDecision:
        with self.session.begin_nested():
            run = self.session.scalar(
                select(SeriesRun)
                .where(SeriesRun.id == resolution.series_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None:
                return self._persist_block(
                    data=data,
                    context=context,
                    resolver_input=resolver_input,
                    reason_codes=[
                        str(AssignmentReasonCode.SERIES_BINDING_INVALID)
                    ],
                    use_savepoint=False,
                )
            plan = self.session.get(SeriesPlan, run.series_plan_id)
            selected_candidate = next(
                (
                    candidate
                    for candidate in resolver_input.candidates
                    if candidate.series_run_id == run.id
                ),
                None,
            )
            invalid = self._locked_run_reason(
                data=data,
                plan=plan,
                run=run,
                candidate=selected_candidate,
            )
            if invalid is not None:
                if data.assignment_mode != AssignmentMode.SERIES_REQUIRED:
                    fallback = AssignmentResolution(
                        resolver_version=self.resolver.version,
                        resolver_input_hash=resolution.resolver_input_hash,
                        production_lane=data.production_lane,
                        assignment_mode=data.assignment_mode,
                        content_mode=ContentMode.STANDALONE,
                        standalone_reason_code=invalid,
                        reason_codes=[invalid],
                    )
                    receipt_id = uuid.uuid4()
                    project = self._create_project(
                        data=data,
                        resolution=fallback,
                        receipt_id=receipt_id,
                    )
                    return self._persist_admit(
                        data=data,
                        context=context,
                        resolver_input=resolver_input,
                        resolution=fallback,
                        project=project,
                        receipt_id=receipt_id,
                    )
                return self._persist_block(
                    data=data,
                    context=context,
                    resolver_input=resolver_input,
                    reason_codes=[str(invalid)],
                    use_savepoint=False,
                )
            episode_number = run.next_episode_number
            run.next_episode_number += 1
            run.reserved_episode_count += 1
            self.session.flush()
            if self._fault_hook is not None:
                self._fault_hook("after_episode_reservation")
            resolution = resolution.model_copy(
                update={"episode_number": episode_number}
            )
            receipt_id = uuid.uuid4()
            project = self._create_project(
                data=data,
                resolution=resolution,
                receipt_id=receipt_id,
            )
            receipt = self._persist_admit(
                data=data,
                context=context,
                resolver_input=resolver_input,
                resolution=resolution,
                project=project,
                receipt_id=receipt_id,
            )
            return receipt

    def _locked_run_reason(
        self,
        *,
        data: ProjectAdmissionV2Request,
        plan: SeriesPlan | None,
        run: SeriesRun,
        candidate: AssignmentCandidate | None,
    ) -> AssignmentReasonCode | None:
        if (
            plan is None
            or run.series_plan_id != plan.id
            or plan.id != data.preferred_series_plan_id
            and data.assignment_mode == AssignmentMode.SERIES_REQUIRED
            or plan.company_id != data.company_id
            or plan.channel_workspace_id != data.channel_workspace_id
            or plan.channel_profile_version_id
            != data.channel_profile_version_id
            or plan.policy_snapshot_id != data.policy_snapshot_id
        ):
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        if plan.state == SeriesPlanState.SUPERSEDED:
            return AssignmentReasonCode.SERIES_PLAN_SUPERSEDED
        if plan.state != SeriesPlanState.APPROVED:
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        if run.state != SeriesRunState.ACTIVE:
            return AssignmentReasonCode.SERIES_RUN_NOT_ACTIVE
        if candidate is None:
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        if not candidate.schedule_eligible:
            return AssignmentReasonCode.SERIES_SCHEDULE_INELIGIBLE
        if candidate.coherence_score <= 0:
            return AssignmentReasonCode.SERIES_COHERENCE_FAILED
        if run.reserved_episode_count >= run.capacity:
            return AssignmentReasonCode.SERIES_CAPACITY_EXHAUSTED
        if str(data.production_lane) not in (plan.allowed_production_lanes or []):
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        return None

    def _admit_standalone(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
        resolver_input: AssignmentResolverInput,
        resolution: AssignmentResolution,
    ) -> ProjectAdmissionDecision:
        with self.session.begin_nested():
            receipt_id = uuid.uuid4()
            project = self._create_project(
                data=data,
                resolution=resolution,
                receipt_id=receipt_id,
            )
            return self._persist_admit(
                data=data,
                context=context,
                resolver_input=resolver_input,
                resolution=resolution,
                project=project,
                receipt_id=receipt_id,
            )

    def _create_project(
        self,
        *,
        data: ProjectAdmissionV2Request,
        resolution: AssignmentResolution,
        receipt_id: uuid.UUID,
    ) -> VideoProject:
        lineage = data.derivative_lineage
        project_type = {
            PlanningSourceType.DAILY_IDEA: "vcos_v2_daily_short",
            PlanningSourceType.LONG_FORM_PLAN: "vcos_v2_long_form",
            PlanningSourceType.DERIVED_SHORT: "vcos_v2_long_derived_short",
        }[data.planning_source_type]
        return VideoProjectService(self.session).create_project(
            data=VideoProjectCreate(
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
                policy_snapshot_id=data.policy_snapshot_id,
                channel_profile_version_id=data.channel_profile_version_id,
                category_id=data.category_id,
                character_binding_id=data.character_binding_id,
                title=data.title,
                description=data.description,
                status="draft",
                project_type=project_type,
                schema_version="v2",
                planning_source_type=data.planning_source_type,
                production_lane=data.production_lane,
                content_mode=resolution.content_mode,
                assignment_mode=data.assignment_mode,
                series_plan_id=resolution.series_plan_id,
                series_run_id=resolution.series_run_id,
                episode_number=resolution.episode_number,
                episode_role=resolution.episode_role,
                standalone_reason_code=(
                    str(resolution.standalone_reason_code)
                    if resolution.standalone_reason_code is not None
                    else None
                ),
                project_admission_decision_id=receipt_id,
                parent_video_project_id=(
                    lineage.parent_video_project_id
                    if lineage is not None
                    else None
                ),
                parent_final_media_ref_id=(
                    lineage.parent_final_media_ref_id
                    if lineage is not None
                    else None
                ),
                canonical_timeline_ref=(
                    lineage.canonical_timeline_ref
                    if lineage is not None
                    else None
                ),
                canonical_timeline_hash=(
                    lineage.canonical_timeline_hash
                    if lineage is not None
                    else None
                ),
                duration_contract=data.duration_contract,
                render_eligible=data.production_lane
                != ProductionLane.LONG_DERIVED_SHORT,
                created_by_user_id=data.created_by_user_id,
                audience_delivery_summary={
                    "planning_source_type": str(data.planning_source_type),
                    "editorial_calendar_slot_id": (
                        str(data.editorial_calendar_slot_id)
                        if data.editorial_calendar_slot_id
                        else None
                    ),
                    "daily_idea_decision_id": (
                        str(data.daily_idea_decision_id)
                        if data.daily_idea_decision_id
                        else None
                    ),
                },
            ),
            correlation_id="vcos-v2-project-created-from-admission",
            trusted_v2_admission=True,
        )

    def _persist_admit(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
        resolver_input: AssignmentResolverInput,
        resolution: AssignmentResolution,
        project: VideoProject,
        receipt_id: uuid.UUID,
    ) -> ProjectAdmissionDecision:
        decision_hash = self._decision_hash(
            data=data, resolution=resolution, decision="ADMIT"
        )
        existing = self.session.scalar(
            select(ProjectAdmissionDecision).where(
                ProjectAdmissionDecision.decision_hash == decision_hash
            )
        )
        if existing is not None:
            raise ConflictError(
                "deterministic admission already exists with a different source lock"
            )
        lineage = data.derivative_lineage
        receipt = ProjectAdmissionDecision(
            id=receipt_id,
            schema_version="v2",
            channel_daily_run_id=data.channel_daily_run_id,
            daily_idea_decision_id=data.daily_idea_decision_id,
            editorial_calendar_slot_id=data.editorial_calendar_slot_id,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            channel_profile_version_id=data.channel_profile_version_id,
            policy_snapshot_id=data.policy_snapshot_id,
            idea_market_preflight_id=data.idea_market_preflight_id,
            planning_source_type=data.planning_source_type,
            production_lane=data.production_lane,
            content_mode=resolution.content_mode,
            assignment_mode=data.assignment_mode,
            series_plan_id=resolution.series_plan_id,
            series_run_id=resolution.series_run_id,
            episode_number=resolution.episode_number,
            episode_role=resolution.episode_role,
            standalone_reason_code=(
                str(resolution.standalone_reason_code)
                if resolution.standalone_reason_code is not None
                else None
            ),
            parent_video_project_id=(
                lineage.parent_video_project_id if lineage else None
            ),
            parent_final_media_ref_id=(
                lineage.parent_final_media_ref_id if lineage else None
            ),
            canonical_timeline_ref=(
                lineage.canonical_timeline_ref if lineage else None
            ),
            canonical_timeline_hash=(
                lineage.canonical_timeline_hash if lineage else None
            ),
            resolver_version=resolution.resolver_version,
            resolver_input_hash=resolution.resolver_input_hash,
            decision_hash=decision_hash,
            assignment_input_ref=resolver_input.model_dump(mode="json"),
            duration_contract=(
                data.duration_contract.model_dump(mode="json")
                if data.duration_contract is not None
                else None
            ),
            budget_gate_result={
                "decision": "PASS",
                "deterministic": True,
                "schema_version": "v2",
            },
            readiness_gate_refs=self._readiness_refs(
                data=data, context=context
            ),
            decision="ADMIT",
            reason_codes=[str(code) for code in resolution.reason_codes],
            evidence_refs=list(data.evidence_refs),
            admitted_video_project_id=project.id,
            created_artifact_refs=[],
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(receipt)
        self.session.flush()
        if project.project_admission_decision_id != receipt.id:
            raise ValidationFailureError(
                "PROJECT_ADMISSION_BIDIRECTIONAL_LINK_MISMATCH"
            )
        self._link_source(context=context, receipt=receipt)
        self.session.flush()
        return receipt

    def _readiness_refs(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
    ) -> list[dict[str, Any]]:
        if context.preflight is not None:
            return [
                {
                    "type": "idea_market_preflight",
                    "id": str(context.preflight.id),
                    "decision": context.preflight.decision,
                    "policy_fit_state": context.preflight.policy_fit_state,
                }
            ]
        if data.derivative_lineage is None:
            return []
        parent = self.session.get(
            VideoProject, data.derivative_lineage.parent_video_project_id
        )
        return [
            {
                "type": "inherited_parent_admission",
                "parent_video_project_id": str(
                    data.derivative_lineage.parent_video_project_id
                ),
                "parent_project_admission_decision_id": (
                    str(parent.project_admission_decision_id)
                    if parent is not None
                    and parent.project_admission_decision_id is not None
                    else None
                ),
                "niche_market_authority": "INHERITED_EXACT_PARENT_PASS",
            }
        ]

    def _persist_block(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
        resolver_input: AssignmentResolverInput,
        reason_codes: list[str],
        use_savepoint: bool = True,
    ) -> ProjectAdmissionDecision:
        resolver_input_hash = self.resolver.input_hash(resolver_input)
        decision_hash = content_hash(
            {
                "decision": "BLOCK",
                "reason_codes": reason_codes,
                "request": data.model_dump(mode="json"),
                "resolver_input_hash": resolver_input_hash,
                "resolver_version": self.resolver.version,
            }
        )
        existing = self.session.scalar(
            select(ProjectAdmissionDecision).where(
                ProjectAdmissionDecision.decision_hash == decision_hash
            )
        )
        if existing is not None:
            return existing

        def add_receipt() -> ProjectAdmissionDecision:
            receipt = ProjectAdmissionDecision(
                schema_version="v2",
                channel_daily_run_id=data.channel_daily_run_id,
                daily_idea_decision_id=data.daily_idea_decision_id,
                editorial_calendar_slot_id=data.editorial_calendar_slot_id,
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
                channel_profile_version_id=data.channel_profile_version_id,
                policy_snapshot_id=data.policy_snapshot_id,
                idea_market_preflight_id=data.idea_market_preflight_id,
                planning_source_type=data.planning_source_type,
                production_lane=data.production_lane,
                content_mode=None,
                assignment_mode=data.assignment_mode,
                series_plan_id=data.preferred_series_plan_id,
                series_run_id=data.preferred_series_run_id,
                episode_number=None,
                episode_role=data.episode_role,
                standalone_reason_code=None,
                parent_video_project_id=(
                    data.derivative_lineage.parent_video_project_id
                    if data.derivative_lineage
                    else None
                ),
                parent_final_media_ref_id=(
                    data.derivative_lineage.parent_final_media_ref_id
                    if data.derivative_lineage
                    else None
                ),
                canonical_timeline_ref=(
                    data.derivative_lineage.canonical_timeline_ref
                    if data.derivative_lineage
                    else None
                ),
                canonical_timeline_hash=(
                    data.derivative_lineage.canonical_timeline_hash
                    if data.derivative_lineage
                    else None
                ),
                resolver_version=self.resolver.version,
                resolver_input_hash=resolver_input_hash,
                decision_hash=decision_hash,
                assignment_input_ref=resolver_input.model_dump(mode="json"),
                duration_contract=(
                    data.duration_contract.model_dump(mode="json")
                    if data.duration_contract is not None
                    else None
                ),
                budget_gate_result={
                    "decision": "NOT_EVALUATED",
                    "deterministic": True,
                    "schema_version": "v2",
                },
                readiness_gate_refs=[],
                decision="BLOCK",
                reason_codes=reason_codes,
                evidence_refs=list(data.evidence_refs),
                admitted_video_project_id=None,
                created_artifact_refs=[],
                created_by_user_id=data.created_by_user_id,
            )
            self.session.add(receipt)
            self.session.flush()
            self._link_source(context=context, receipt=receipt)
            self.session.flush()
            return receipt

        if not use_savepoint:
            return add_receipt()
        with self.session.begin_nested():
            return add_receipt()

    @staticmethod
    def _decision_hash(
        *,
        data: ProjectAdmissionV2Request,
        resolution: AssignmentResolution,
        decision: str,
    ) -> str:
        return content_hash(
            {
                "decision": decision,
                "request": data.model_dump(mode="json"),
                "resolution": resolution.model_dump(mode="json"),
            }
        )

    @staticmethod
    def _link_source(
        *,
        context: _AdmissionContext,
        receipt: ProjectAdmissionDecision,
    ) -> None:
        if context.daily_run is not None:
            context.daily_run.project_admission_decision_id = receipt.id
        if context.slot is not None and receipt.decision == "ADMIT":
            context.slot.status = "ADMITTED"

    def _existing_source_receipt(
        self, data: ProjectAdmissionV2Request
    ) -> ProjectAdmissionDecision | None:
        if data.daily_idea_decision_id is not None:
            return self.session.scalars(
                select(ProjectAdmissionDecision)
                .where(
                    ProjectAdmissionDecision.schema_version == "v2",
                    ProjectAdmissionDecision.planning_source_type
                    == PlanningSourceType.DAILY_IDEA,
                    ProjectAdmissionDecision.daily_idea_decision_id
                    == data.daily_idea_decision_id,
                )
                .order_by(ProjectAdmissionDecision.created_at.asc())
            ).first()
        if (
            data.planning_source_type == PlanningSourceType.LONG_FORM_PLAN
            and data.editorial_calendar_slot_id is not None
        ):
            return self.session.scalars(
                select(ProjectAdmissionDecision)
                .where(
                    ProjectAdmissionDecision.schema_version == "v2",
                    ProjectAdmissionDecision.planning_source_type
                    == PlanningSourceType.LONG_FORM_PLAN,
                    ProjectAdmissionDecision.editorial_calendar_slot_id
                    == data.editorial_calendar_slot_id,
                )
                .order_by(ProjectAdmissionDecision.created_at.asc())
            ).first()
        return None

    def _lock_source_and_existing(
        self, data: ProjectAdmissionV2Request
    ) -> ProjectAdmissionDecision | None:
        """Serialize one immutable admission receipt per exact planning source."""

        if data.daily_idea_decision_id is not None:
            self.session.scalar(
                select(DailyIdeaDecision)
                .where(DailyIdeaDecision.id == data.daily_idea_decision_id)
                .with_for_update()
            )
        elif (
            data.planning_source_type == PlanningSourceType.LONG_FORM_PLAN
            and data.editorial_calendar_slot_id is not None
        ):
            self.session.scalar(
                select(EditorialCalendarSlot)
                .where(
                    EditorialCalendarSlot.id
                    == data.editorial_calendar_slot_id
                )
                .with_for_update()
            )
        return self._existing_source_receipt(data)


class LongFormPlanningService:
    """Dedicated LONG_FORM entry; it never creates a ChannelDailyRun."""

    def __init__(
        self,
        session: Session,
        *,
        admission_service: ProjectAdmissionV2Service | None = None,
    ):
        self.session = session
        self.admission_service = admission_service or ProjectAdmissionV2Service(
            session
        )

    def admit(
        self, data: LongFormPlanningRequest
    ) -> ProjectAdmissionDecision:
        return self.admission_service.create_decision(
            data=ProjectAdmissionV2Request(
                planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
                channel_profile_version_id=data.channel_profile_version_id,
                policy_snapshot_id=data.policy_snapshot_id,
                editorial_calendar_slot_id=data.editorial_calendar_slot_id,
                idea_market_preflight_id=data.idea_market_preflight_id,
                production_lane=ProductionLane.LONG_FORM,
                assignment_mode=data.assignment_mode,
                preferred_series_plan_id=data.preferred_series_plan_id,
                preferred_series_run_id=data.preferred_series_run_id,
                title=data.title,
                description=data.description,
                category_id=data.category_id,
                character_binding_id=data.character_binding_id,
                niche_gate_passed=data.niche_gate_passed,
                market_gate_passed=data.market_gate_passed,
                timely_niche_opportunity=data.timely_niche_opportunity,
                bridge_or_special=data.bridge_or_special,
                evidence_refs=data.evidence_refs,
                duration_contract=data.duration_contract,
                created_by_user_id=data.created_by_user_id,
            )
        )


class LongFormPackageEligibilityService:
    """Boundary guard used by package/render entry points for typed projects."""

    def __init__(self, session: Session):
        self.session = session

    def require_eligible(self, video_project_id: uuid.UUID) -> VideoProject:
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.schema_version == "v2" and (
            project.production_lane != ProductionLane.LONG_FORM
            or project.planning_source_type
            != PlanningSourceType.LONG_FORM_PLAN
            or project.render_eligible is not True
        ):
            raise ValidationFailureError(
                "LONG_FORM package requires an admitted v2 LONG_FORM project"
            )
        return project


class LegacySeriesReader:
    """Dual reader that classifies v1 truth without rewriting it."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def classify_values(
        *,
        schema_version: str | None,
        series_plan_id: uuid.UUID | None,
        series_run_id: uuid.UUID | None,
        legacy_series_key: str | None,
    ) -> LegacySeriesClassification:
        if schema_version == "v2":
            if series_plan_id is not None and series_run_id is not None:
                return LegacySeriesClassification.V2_TYPED
            return LegacySeriesClassification.V2_TYPED
        if legacy_series_key and legacy_series_key.strip():
            return LegacySeriesClassification.LEGACY_SERIES_BOUND
        return LegacySeriesClassification.UNRESOLVED_LEGACY

    def classify_project(
        self, video_project_id: uuid.UUID
    ) -> LegacySeriesClassification:
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.schema_version == "v2":
            return LegacySeriesClassification.V2_TYPED
        admission = self.session.scalars(
            select(ProjectAdmissionDecision)
            .where(
                ProjectAdmissionDecision.admitted_video_project_id == project.id
            )
            .order_by(ProjectAdmissionDecision.created_at.asc())
        ).first()
        if admission is None:
            return LegacySeriesClassification.UNRESOLVED_LEGACY
        if admission.schema_version == "v2":
            return LegacySeriesClassification.V2_TYPED
        legacy_key: str | None = None
        if admission.daily_idea_decision_id is not None:
            idea = self.session.get(
                DailyIdeaDecision, admission.daily_idea_decision_id
            )
            if idea is not None:
                legacy_key = idea.proposed_series_key
        if not legacy_key and admission.channel_daily_run_id is not None:
            run = self.session.get(
                ChannelDailyRun, admission.channel_daily_run_id
            )
            if run is not None and run.editorial_calendar_slot_id is not None:
                slot = self.session.get(
                    EditorialCalendarSlot, run.editorial_calendar_slot_id
                )
                if slot is not None:
                    legacy_key = slot.series_key
        return self.classify_values(
            schema_version="v1",
            series_plan_id=None,
            series_run_id=None,
            legacy_series_key=legacy_key,
        )
