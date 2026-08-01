"""Phase 2 typed planning, assignment, and atomic admission services.

The resolver in this module is pure and deterministic.  Database mutation is
kept in the service layer so the selected SeriesRun can be revalidated under a
row lock immediately before an episode is reserved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.vcos_v2 import (
    ASSIGNMENT_RESOLVER_VERSION,
    AssignmentCandidate,
    AssignmentMode,
    AssignmentReasonCode,
    AssignmentResolution,
    AssignmentResolverInput,
    ContentMode,
    DecisionReversibility,
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
    StrategicIntent,
    StrategicLineageV2,
)
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion, LaunchRun
from app.db.models.m5 import (
    EditorialCalendarSlot,
    EditorialIdeaCandidate,
    EditorialResearchRun,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
)
from app.db.models.m7 import UploadedVideo
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.production_start_readiness import (
    resolve_budget_authority,
)
from app.services.workflow import VideoProjectService


FaultHook = Callable[[str], None]


def _approved_launch_policy_for_channel(
    session: Session,
    *,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    lock: bool = False,
) -> FirstChannelLaunchPolicyVersion | None:
    """Return the channel-level launch authority when controlled launch applies."""

    statement = select(FirstChannelLaunchPolicyVersion).where(
        FirstChannelLaunchPolicyVersion.company_id == company_id,
        FirstChannelLaunchPolicyVersion.channel_workspace_id == channel_workspace_id,
        FirstChannelLaunchPolicyVersion.state == "APPROVED",
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _launch_policy_series_plan_ids(
    policy: FirstChannelLaunchPolicyVersion,
) -> set[str]:
    return {
        str(series_plan_id)
        for series_plan_id in policy.approved_initial_series_plan_ids or []
    }


def _launch_policy_allows_series_plan(
    policy: FirstChannelLaunchPolicyVersion | None,
    series_plan_id: uuid.UUID,
) -> bool:
    return policy is None or (
        str(series_plan_id) in _launch_policy_series_plan_ids(policy)
    )


def _launch_policy_active_series_violation(
    policy: FirstChannelLaunchPolicyVersion | None,
    series_plan_ids: Iterable[uuid.UUID],
) -> str | None:
    if policy is None:
        return None
    projected_series_plan_ids = list(series_plan_ids)
    if any(
        not _launch_policy_allows_series_plan(policy, series_plan_id)
        for series_plan_id in projected_series_plan_ids
    ):
        return "LAUNCH_ACTIVE_SERIES_OUTSIDE_INITIAL_POLICY"
    if len(projected_series_plan_ids) > policy.max_active_runs:
        return "LAUNCH_MAX_ACTIVE_SERIES_EXCEEDED"
    return None


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
    slot: EditorialCalendarSlot
    candidate: EditorialIdeaCandidate | None
    research_run: EditorialResearchRun | None
    preflight: IdeaMarketPreflight
    launch_policy: FirstChannelLaunchPolicyVersion
    launch_run: LaunchRun


class DeterministicAssignmentResolver:
    """Versioned assignment policy with an order-independent stable tie-break."""

    version = ASSIGNMENT_RESOLVER_VERSION

    def resolve(self, data: AssignmentResolverInput) -> AssignmentResolution:
        input_hash = self.input_hash(data)
        if not data.niche_gate_passed:
            raise AssignmentResolutionError("NICHE_GATE_NOT_PASS")
        if not data.market_gate_passed:
            raise AssignmentResolutionError("MARKET_GATE_NOT_PASS")

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
            raise AssignmentResolutionError(AssignmentReasonCode.NO_ELIGIBLE_SERIES)
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
            previous = self.session.get(SeriesPlan, data.supersedes_series_plan_id)
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
            **data.model_dump(exclude={"allowed_production_lanes"}, mode="python"),
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
            select(SeriesPlan).where(SeriesPlan.id == plan_id).with_for_update()
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
        if profile is None or profile.channel_workspace_id != data.channel_workspace_id:
            raise ValidationFailureError(
                "SeriesPlan profile does not belong to workspace"
            )
        policy = self.session.get(
            CompiledChannelPolicySnapshot, data.policy_snapshot_id
        )
        if (
            policy is None
            or policy.channel_workspace_id != data.channel_workspace_id
            or policy.channel_profile_version_id != data.channel_profile_version_id
        ):
            raise ValidationFailureError("SeriesPlan policy/profile binding is invalid")


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
            raise ValidationFailureError("SeriesRun requires an APPROVED SeriesPlan")
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
            launch_policy = _approved_launch_policy_for_channel(
                self.session,
                company_id=run.company_id,
                channel_workspace_id=run.channel_workspace_id,
                lock=True,
            )
            if launch_policy is not None:
                active_series_plan_ids = list(
                    self.session.scalars(
                        select(SeriesRun.series_plan_id)
                        .where(
                            SeriesRun.channel_workspace_id == run.channel_workspace_id,
                            SeriesRun.state == SeriesRunState.ACTIVE,
                            SeriesRun.id != run.id,
                        )
                        .with_for_update()
                    ).all()
                )
                projected_series_plan_ids = [
                    *active_series_plan_ids,
                    run.series_plan_id,
                ]
                violation = _launch_policy_active_series_violation(
                    launch_policy,
                    projected_series_plan_ids,
                )
                if violation is not None:
                    raise ValidationFailureError(violation)
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


class ProjectAdmissionV2Service:
    """Atomic, idempotent admission for the only active LONG_FORM lane."""

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
        del correlation_id
        context = self._load_context(data)
        data = self._resolve_server_lineage(data=data, context=context)
        existing = self._lock_source_and_existing(data)
        if existing is not None:
            return existing
        budget_gate_result = resolve_budget_authority(
            self.session,
            policy_snapshot_id=data.policy_snapshot_id,
            channel_workspace_id=data.channel_workspace_id,
        )
        if (
            data.budget_gate_result is not None
            and data.budget_gate_result != budget_gate_result
        ):
            raise ValidationFailureError(
                "LONG_FORM_ADMISSION_BUDGET_AUTHORITY_MISMATCH"
            )
        data = data.model_copy(update={"budget_gate_result": budget_gate_result})
        resolver_input = self._resolver_input(data, context)
        gate_reasons = self._gate_reasons(data, context)
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

    def _active_launch_authority(
        self,
        *,
        data: ProjectAdmissionV2Request,
    ) -> tuple[FirstChannelLaunchPolicyVersion, LaunchRun]:
        """Lock the one active launch authority before an admission can proceed."""

        launch_policy = _approved_launch_policy_for_channel(
            self.session,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            lock=True,
        )
        if (
            launch_policy is None
            or launch_policy.channel_profile_version_id
            != data.channel_profile_version_id
            or launch_policy.policy_snapshot_id != data.policy_snapshot_id
        ):
            raise ValidationFailureError("ACTIVE_LAUNCH_POLICY_REQUIRED")
        launch_run = self.session.scalar(
            select(LaunchRun)
            .where(
                LaunchRun.launch_policy_version_id == launch_policy.id,
                LaunchRun.company_id == data.company_id,
                LaunchRun.channel_workspace_id == data.channel_workspace_id,
                LaunchRun.state == "ACTIVE",
            )
            .with_for_update()
        )
        if launch_run is None:
            raise ValidationFailureError("ACTIVE_LAUNCH_RUN_REQUIRED")
        return launch_policy, launch_run

    @staticmethod
    def _launch_run_authority_hash(
        *,
        launch_policy: FirstChannelLaunchPolicyVersion,
        launch_run: LaunchRun,
    ) -> str:
        """Hash the active-run snapshot that an immutable admission binds."""

        return content_hash(
            {
                "launch_key": launch_run.launch_key,
                "launch_policy_hash": launch_policy.canonical_hash,
                "launch_policy_version_id": str(launch_policy.id),
                "launch_run_id": str(launch_run.id),
                "launch_started_at": (
                    launch_run.launch_started_at.isoformat()
                    if launch_run.launch_started_at is not None
                    else None
                ),
                "preparation_started_on": launch_run.preparation_started_on.isoformat(),
                "reason_codes": list(launch_run.reason_codes or []),
                "state": launch_run.state,
            }
        )

    @staticmethod
    def _audience_authority(
        *,
        context: _AdmissionContext,
    ) -> dict[str, Any]:
        """Extract the exact promise from the approved compiled contract."""

        payload = context.policy.compiled_payload or {}
        contract = payload.get("channel_contract_json")
        if not isinstance(contract, dict):
            raise ValidationFailureError("ADMISSION_AUDIENCE_CONTRACT_REQUIRED")
        identity = contract.get("channel_identity")
        target = contract.get("target_audience")
        market = contract.get("market_locale")
        if not isinstance(identity, dict) or not isinstance(target, dict):
            raise ValidationFailureError("ADMISSION_AUDIENCE_CONTRACT_REQUIRED")
        audience_promise = identity.get("brand_promise")
        primary_persona = target.get("primary_persona")
        if not isinstance(audience_promise, str) or not audience_promise.strip():
            raise ValidationFailureError("ADMISSION_AUDIENCE_PROMISE_REQUIRED")
        if not isinstance(primary_persona, str) or not primary_persona.strip():
            raise ValidationFailureError("ADMISSION_TARGET_AUDIENCE_REQUIRED")
        target_definition = {
            "audience_level": target.get("audience_level"),
            "audience_notes": target.get("audience_notes"),
            "desired_outcome": target.get("desired_outcome"),
            "market_locale": {
                "audience_locale": (
                    market.get("audience_locale") if isinstance(market, dict) else None
                ),
                "content_language": (
                    market.get("content_language") if isinstance(market, dict) else None
                ),
                "primary_market": (
                    market.get("primary_market") if isinstance(market, dict) else None
                ),
            },
            "pain_points": list(target.get("pain_points") or []),
            "primary_persona": primary_persona,
        }
        audience_promise_version = (
            f"channel-contract-snapshot-{context.policy.snapshot_version}"
        )
        audience_drift_guard_version = (
            f"channel-contract-drift-guard-{context.policy.snapshot_version}"
        )
        return {
            "audience_promise": audience_promise.strip(),
            "audience_promise_version": audience_promise_version,
            "audience_promise_hash": StrategicLineageV2.calculate_audience_promise_hash(
                audience_promise=audience_promise.strip(),
                audience_promise_version=audience_promise_version,
                target_audience_definition=target_definition,
                audience_drift_guard_version=audience_drift_guard_version,
            ),
            "target_audience_definition": target_definition,
            "audience_drift_guard_version": audience_drift_guard_version,
        }

    def _is_first_public_video(self, *, context: _AdmissionContext) -> bool:
        return (
            self.session.scalar(
                select(func.count(UploadedVideo.id)).where(
                    UploadedVideo.channel_workspace_id == context.workspace.id,
                    UploadedVideo.verification_status == "VERIFIED",
                )
            )
            or 0
        ) == 0

    def _resolve_server_lineage(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
    ) -> ProjectAdmissionV2Request:
        """Derive and reconcile immutable lineage; never persist caller claims.

        The request is only a claimed view.  Audience and launch values are
        rebuilt from the active policy/run and intent comes from the frozen
        candidate when it exists.  The first public video receives the bounded
        ACQUISITION default only if no candidate-specific intent overrides it.
        """

        lineage = self._audience_authority(context=context)
        launch_run_hash = self._launch_run_authority_hash(
            launch_policy=context.launch_policy,
            launch_run=context.launch_run,
        )
        lineage.update(
            {
                "active_launch_policy_version_id": context.launch_policy.id,
                "active_launch_policy_hash": context.launch_policy.canonical_hash,
                "active_launch_run_id": context.launch_run.id,
                "active_launch_run_hash": launch_run_hash,
            }
        )
        candidate = context.candidate
        candidate_has_lineage = bool(
            candidate is not None and candidate.audience_promise_hash
        )
        if candidate_has_lineage:
            assert candidate is not None
            candidate_lineage = {
                **lineage,
                "audience_promise": candidate.audience_promise,
                "audience_promise_version": candidate.audience_promise_version,
                "audience_promise_hash": candidate.audience_promise_hash,
                "target_audience_definition": candidate.target_audience_definition,
                "audience_drift_guard_version": candidate.audience_drift_guard_version,
                "strategic_intent": candidate.strategic_intent,
                "intent_success_criteria": candidate.intent_success_criteria,
                "intent_success_criteria_version": candidate.intent_success_criteria_version,
                "intent_success_criteria_hash": candidate.intent_success_criteria_hash,
                "experiment_hypothesis": candidate.experiment_hypothesis,
                "primary_variable_under_test": candidate.primary_variable_under_test,
                "decision_reversibility": candidate.decision_reversibility,
                "active_launch_policy_version_id": candidate.active_launch_policy_version_id,
                "active_launch_policy_hash": candidate.active_launch_policy_hash,
                "active_launch_run_id": candidate.active_launch_run_id,
                "active_launch_run_hash": candidate.active_launch_run_hash,
            }
            try:
                validated_candidate = StrategicLineageV2.model_validate(
                    candidate_lineage
                )
            except ValueError as exc:
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_STRATEGIC_LINEAGE_INVALID"
                ) from exc
            resolved = validated_candidate.model_dump(mode="python")
            for key in (
                "audience_promise",
                "audience_promise_version",
                "audience_promise_hash",
                "target_audience_definition",
                "audience_drift_guard_version",
                "active_launch_policy_version_id",
                "active_launch_policy_hash",
                "active_launch_run_id",
                "active_launch_run_hash",
            ):
                if resolved[key] != lineage[key]:
                    raise ValidationFailureError(
                        "EDITORIAL_CANDIDATE_STRATEGIC_AUTHORITY_MISMATCH"
                    )
            lineage = resolved
        else:
            if not self._is_first_public_video(context=context):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_STRATEGIC_LINEAGE_REQUIRED"
                )
            if candidate is not None and candidate.experiment_phase not in {
                None,
                "AUDIENCE_PROMISE",
            }:
                raise ValidationFailureError(
                    "FIRST_VIDEO_AUDIENCE_PROMISE_PHASE_REQUIRED"
                )
            strategic_intent = StrategicIntent.ACQUISITION
            primary_variable = "audience_promise_validation"
            experiment_hypothesis = (
                "The frozen channel promise is relevant to the approved target "
                "audience when demonstrated through one bounded long-form video: "
                f"{lineage['audience_promise']}"
            )
            criteria = {
                "criterion": "AUDIENCE_PROMISE_VALIDATION",
                "launch_policy_hash": context.launch_policy.canonical_hash,
                "measurement_scope": "FIRST_PUBLIC_VIDEO",
                "required_candidate_phase": "AUDIENCE_PROMISE",
            }
            criteria_version = "launch-audience-promise-v1"
            decision_reversibility = DecisionReversibility.TWO_WAY_DOOR
            lineage.update(
                {
                    "strategic_intent": strategic_intent,
                    "intent_success_criteria": criteria,
                    "intent_success_criteria_version": criteria_version,
                    "intent_success_criteria_hash": (
                        StrategicLineageV2.calculate_intent_success_criteria_hash(
                            strategic_intent=strategic_intent,
                            intent_success_criteria=criteria,
                            intent_success_criteria_version=criteria_version,
                            experiment_hypothesis=experiment_hypothesis,
                            primary_variable_under_test=primary_variable,
                            decision_reversibility=decision_reversibility,
                        )
                    ),
                    "experiment_hypothesis": experiment_hypothesis,
                    "primary_variable_under_test": primary_variable,
                    "decision_reversibility": decision_reversibility,
                }
            )
        try:
            resolved_lineage = StrategicLineageV2.model_validate(lineage)
        except ValueError as exc:
            raise ValidationFailureError("ADMISSION_STRATEGIC_LINEAGE_INVALID") from exc
        claimed = data.model_dump(
            mode="python", include=set(lineage), exclude_none=True
        )
        expected = resolved_lineage.model_dump(mode="python")
        if any(expected[key] != value for key, value in claimed.items()):
            raise ValidationFailureError("ADMISSION_STRATEGIC_LINEAGE_MISMATCH")
        return data.model_copy(update=expected)

    @staticmethod
    def _lineage_values(data: ProjectAdmissionV2Request) -> dict[str, Any]:
        """Serialize one identical immutable lineage into each downstream row."""

        return {
            "audience_promise": data.audience_promise,
            "audience_promise_version": data.audience_promise_version,
            "audience_promise_hash": data.audience_promise_hash,
            "target_audience_definition": data.target_audience_definition,
            "audience_drift_guard_version": data.audience_drift_guard_version,
            "strategic_intent": data.strategic_intent.value,
            "intent_success_criteria": data.intent_success_criteria,
            "intent_success_criteria_version": data.intent_success_criteria_version,
            "intent_success_criteria_hash": data.intent_success_criteria_hash,
            "experiment_hypothesis": data.experiment_hypothesis,
            "primary_variable_under_test": data.primary_variable_under_test,
            "decision_reversibility": data.decision_reversibility.value,
            "active_launch_policy_version_id": data.active_launch_policy_version_id,
            "active_launch_policy_hash": data.active_launch_policy_hash,
            "active_launch_run_id": data.active_launch_run_id,
            "active_launch_run_hash": data.active_launch_run_hash,
        }

    def _load_context(
        self,
        data: ProjectAdmissionV2Request,
    ) -> _AdmissionContext:
        if (
            data.production_lane != ProductionLane.LONG_FORM
            or data.planning_source_type != PlanningSourceType.LONG_FORM_PLAN
            or data.editorial_calendar_slot_id is None
            or data.idea_market_preflight_id is None
        ):
            raise ValidationFailureError("LONG_FORM_ADMISSION_SOURCE_REQUIRED")
        workspace = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        profile = self.session.get(
            ChannelProfileVersion,
            data.channel_profile_version_id,
        )
        policy = self.session.get(
            CompiledChannelPolicySnapshot,
            data.policy_snapshot_id,
        )
        if (
            workspace is None
            or workspace.company_id != data.company_id
            or profile is None
            or profile.channel_workspace_id != workspace.id
            or profile.status not in {"approved", "active"}
            or policy is None
            or policy.channel_workspace_id != workspace.id
            or policy.channel_profile_version_id != profile.id
            or policy.status != "active"
            or workspace.active_policy_snapshot_id != policy.id
        ):
            raise ValidationFailureError("LONG_FORM_ADMISSION_PROFILE_POLICY_MISMATCH")
        launch_policy, launch_run = self._active_launch_authority(data=data)
        from app.services.production_package import (
            ChannelDurationContractResolver,
        )

        authoritative_duration = ChannelDurationContractResolver(self.session).resolve(
            profile_version_id=profile.id,
            policy_snapshot_id=policy.id,
            production_lane=ProductionLane.LONG_FORM,
        )
        if authoritative_duration.model_dump(
            mode="json"
        ) != data.duration_contract.model_dump(mode="json"):
            raise ValidationFailureError("ADMISSION_DURATION_CONTRACT_MISMATCH")

        slot = self.session.get(
            EditorialCalendarSlot,
            data.editorial_calendar_slot_id,
        )
        if (
            slot is None
            or slot.company_id != data.company_id
            or slot.channel_workspace_id != data.channel_workspace_id
            or slot.policy_snapshot_id != data.policy_snapshot_id
            or slot.schema_version != "v2"
            or slot.production_lane != "LONG_FORM"
            or slot.assignment_mode != data.assignment_mode
            or slot.series_key is not None
            or (
                data.preferred_series_plan_id is not None
                and data.preferred_series_plan_id != slot.preferred_series_plan_id
            )
            or (
                data.preferred_series_run_id is not None
                and data.preferred_series_run_id != slot.preferred_series_run_id
            )
        ):
            raise ValidationFailureError("LONG_FORM_EDITORIAL_SLOT_MISMATCH")

        candidate = (
            self.session.get(
                EditorialIdeaCandidate,
                data.editorial_idea_candidate_id,
            )
            if data.editorial_idea_candidate_id is not None
            else None
        )
        research_run = (
            self.session.get(
                EditorialResearchRun,
                candidate.editorial_research_run_id,
            )
            if candidate is not None
            else None
        )
        if data.editorial_idea_candidate_id is not None and (
            candidate is None
            or research_run is None
            or candidate.company_id != data.company_id
            or candidate.channel_workspace_id != data.channel_workspace_id
            or candidate.policy_snapshot_id != data.policy_snapshot_id
            or candidate.proposed_title != data.title
            or candidate.stage
            not in {
                "GREENLIT",
                "SELECTED_FOR_SLOT",
                "IN_PRODUCTION",
                "FINAL_REVIEW_READY",
            }
            or research_run.channel_profile_version_id
            != data.channel_profile_version_id
        ):
            raise ValidationFailureError("LONG_FORM_EDITORIAL_CANDIDATE_MISMATCH")

        preflight = self.session.get(
            IdeaMarketPreflight,
            data.idea_market_preflight_id,
        )
        if (
            preflight is None
            or preflight.company_id != data.company_id
            or preflight.channel_workspace_id != data.channel_workspace_id
            or (
                candidate is None
                and preflight.editorial_calendar_slot_id is not None
                and preflight.editorial_calendar_slot_id != slot.id
            )
            or (
                candidate is not None
                and preflight.editorial_idea_candidate_id != candidate.id
            )
        ):
            raise ValidationFailureError("LONG_FORM_PREFLIGHT_SCOPE_MISMATCH")
        return _AdmissionContext(
            workspace=workspace,
            profile=profile,
            policy=policy,
            slot=slot,
            candidate=candidate,
            research_run=research_run,
            preflight=preflight,
            launch_policy=launch_policy,
            launch_run=launch_run,
        )

    @staticmethod
    def _gate_reasons(
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
    ) -> list[str]:
        reasons: list[str] = []
        preflight = context.preflight
        if preflight.policy_fit_state != "PASS" or not data.niche_gate_passed:
            reasons.append("NICHE_GATE_NOT_PASS")
        if preflight.decision != "PASS" or not data.market_gate_passed:
            reasons.append("MARKET_GATE_NOT_PASS")
        evidence = preflight.evidence_blob or {}
        niche_hash = preflight.niche_contract_digest_hash or evidence.get(
            "niche_contract_digest_hash"
        )
        market_hash = preflight.target_market_digest_hash or evidence.get(
            "target_market_digest_hash"
        )
        if not niche_hash:
            reasons.append("NICHE_CONTRACT_DIGEST_REQUIRED")
        if not market_hash:
            reasons.append("TARGET_MARKET_DIGEST_REQUIRED")
        if context.candidate is not None and (
            context.candidate.rights_policy_state != "PASS"
            or context.candidate.quality_state != "PASS"
        ):
            reasons.append("EDITORIAL_CANDIDATE_READINESS_NOT_PASS")
        if (
            not isinstance(data.budget_gate_result, dict)
            or data.budget_gate_result.get("decision") != "PASS"
        ):
            reasons.append("CHANNEL_BUDGET_GATE_NOT_PASS")
        return list(dict.fromkeys(reasons))

    def _resolver_input(
        self,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
    ) -> AssignmentResolverInput:
        gate_passed = not self._gate_reasons(data, context)
        return AssignmentResolverInput(
            production_lane=ProductionLane.LONG_FORM,
            assignment_mode=data.assignment_mode,
            preferred_series_plan_id=context.slot.preferred_series_plan_id,
            preferred_series_run_id=context.slot.preferred_series_run_id,
            candidates=self._candidates(data, context),
            niche_gate_passed=gate_passed,
            market_gate_passed=gate_passed,
            timely_niche_opportunity=data.timely_niche_opportunity,
            bridge_or_special=data.bridge_or_special,
        )

    def _candidates(
        self,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
    ) -> list[AssignmentCandidate]:
        launch_policy = _approved_launch_policy_for_channel(
            self.session,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
        )
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
            if isinstance(context.slot.operational_envelope, dict)
            else {}
        )
        candidates: list[AssignmentCandidate] = []
        for run, plan in rows:
            if not _launch_policy_allows_series_plan(launch_policy, plan.id):
                continue
            if plan.allowed_production_lanes != ["LONG_FORM"]:
                continue
            try:
                plan_state = SeriesPlanState(plan.state)
                run_state = SeriesRunState(run.state)
            except ValueError:
                continue
            run_key = str(run.id)
            candidates.append(
                AssignmentCandidate(
                    series_plan_id=plan.id,
                    series_run_id=run.id,
                    production_lane=ProductionLane.LONG_FORM,
                    plan_state=plan_state,
                    run_state=run_state,
                    next_episode_number=run.next_episode_number,
                    capacity=run.capacity,
                    reserved_episode_count=run.reserved_episode_count,
                    priority=run.priority,
                    coherence_score=self._mapping_int(
                        envelope,
                        "series_coherence_scores",
                        run_key,
                        100,
                    ),
                    schedule_eligible=self._schedule_eligible(
                        run=run,
                        slot=context.slot,
                    ),
                    mandatory_next_episode=run_key
                    in self._string_list(envelope.get("mandatory_series_run_ids")),
                    explicit_slot_priority=self._mapping_int(
                        envelope,
                        "series_slot_priorities",
                        run_key,
                        0,
                    ),
                    schedule_obligation=self._mapping_int(
                        envelope,
                        "series_schedule_obligations",
                        run_key,
                        0,
                    ),
                    recent_repetition_penalty=self._mapping_int(
                        envelope,
                        "series_repetition_penalties",
                        run_key,
                        0,
                    ),
                    niche_opportunity_value=self._mapping_int(
                        envelope,
                        "series_niche_opportunity_values",
                        run_key,
                        0,
                    ),
                    episode_role=(
                        data.episode_role
                        or (plan.episode_role_policy or {}).get("default_episode_role")
                    ),
                )
            )
        # If legacy/direct writes already oversubscribed the approved set,
        # there is no authoritative "first N" subset for admission to choose.
        if launch_policy is not None and (
            sum(
                candidate.run_state == SeriesRunState.ACTIVE for candidate in candidates
            )
            > launch_policy.max_active_runs
        ):
            return []
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
        return {str(item) for item in value} if isinstance(value, list) else set()

    @staticmethod
    def _schedule_eligible(
        *,
        run: SeriesRun,
        slot: EditorialCalendarSlot,
    ) -> bool:
        if (
            run.schedule_window_start is not None
            and slot.slot_date < run.schedule_window_start.date()
        ):
            return False
        if (
            run.schedule_window_end is not None
            and slot.slot_date > run.schedule_window_end.date()
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
            candidate = next(
                (
                    item
                    for item in resolver_input.candidates
                    if run is not None and item.series_run_id == run.id
                ),
                None,
            )
            invalid = (
                self._locked_run_reason(
                    data=data,
                    run=run,
                    candidate=candidate,
                )
                if run is not None
                else AssignmentReasonCode.SERIES_BINDING_INVALID
            )
            if invalid is not None:
                if data.assignment_mode == AssignmentMode.SERIES_REQUIRED:
                    return self._persist_block(
                        data=data,
                        context=context,
                        resolver_input=resolver_input,
                        reason_codes=[str(invalid)],
                        use_savepoint=False,
                    )
                fallback = self.resolver._standalone(
                    resolver_input,
                    input_hash=resolution.resolver_input_hash,
                    reason=invalid,
                )
                return self._create_admitted(
                    data=data,
                    context=context,
                    resolver_input=resolver_input,
                    resolution=fallback,
                )
            assert run is not None
            episode_number = run.next_episode_number
            run.next_episode_number += 1
            run.reserved_episode_count += 1
            self.session.flush()
            if self._fault_hook is not None:
                self._fault_hook("after_episode_reservation")
            return self._create_admitted(
                data=data,
                context=context,
                resolver_input=resolver_input,
                resolution=resolution.model_copy(
                    update={"episode_number": episode_number}
                ),
            )

    def _locked_run_reason(
        self,
        *,
        data: ProjectAdmissionV2Request,
        run: SeriesRun,
        candidate: AssignmentCandidate | None,
    ) -> AssignmentReasonCode | None:
        plan = self.session.get(SeriesPlan, run.series_plan_id)
        if (
            plan is None
            or plan.company_id != data.company_id
            or plan.channel_workspace_id != data.channel_workspace_id
            or plan.channel_profile_version_id != data.channel_profile_version_id
            or plan.policy_snapshot_id != data.policy_snapshot_id
            or plan.allowed_production_lanes != ["LONG_FORM"]
        ):
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        launch_policy = _approved_launch_policy_for_channel(
            self.session,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
        )
        if launch_policy is not None and not _launch_policy_allows_series_plan(
            launch_policy,
            plan.id,
        ):
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        if plan.state == SeriesPlanState.SUPERSEDED:
            return AssignmentReasonCode.SERIES_PLAN_SUPERSEDED
        if plan.state != SeriesPlanState.APPROVED:
            return AssignmentReasonCode.SERIES_BINDING_INVALID
        if run.state != SeriesRunState.ACTIVE:
            return AssignmentReasonCode.SERIES_RUN_NOT_ACTIVE
        if candidate is None or not candidate.schedule_eligible:
            return AssignmentReasonCode.SERIES_SCHEDULE_INELIGIBLE
        if candidate.coherence_score <= 0:
            return AssignmentReasonCode.SERIES_COHERENCE_FAILED
        if run.reserved_episode_count >= run.capacity:
            return AssignmentReasonCode.SERIES_CAPACITY_EXHAUSTED
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
            return self._create_admitted(
                data=data,
                context=context,
                resolver_input=resolver_input,
                resolution=resolution,
            )

    def _create_admitted(
        self,
        *,
        data: ProjectAdmissionV2Request,
        context: _AdmissionContext,
        resolver_input: AssignmentResolverInput,
        resolution: AssignmentResolution,
    ) -> ProjectAdmissionDecision:
        receipt_id = uuid.uuid4()
        project = VideoProjectService(self.session).create_project(
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
                project_type="vcos_v2_long_form",
                schema_version="v2",
                planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
                production_lane=ProductionLane.LONG_FORM,
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
                duration_contract=data.duration_contract,
                **self._lineage_values(data),
                render_eligible=True,
                created_by_user_id=data.created_by_user_id,
                audience_delivery_summary={
                    "active_launch_policy_hash": data.active_launch_policy_hash,
                    "active_launch_policy_version_id": str(
                        data.active_launch_policy_version_id
                    ),
                    "active_launch_run_hash": data.active_launch_run_hash,
                    "active_launch_run_id": str(data.active_launch_run_id),
                    "audience_promise_hash": data.audience_promise_hash,
                    "planning_source_type": "LONG_FORM_PLAN",
                    "strategic_intent": data.strategic_intent.value,
                    "editorial_calendar_slot_id": str(context.slot.id),
                    "editorial_idea_candidate_id": (
                        str(context.candidate.id)
                        if context.candidate is not None
                        else None
                    ),
                },
            ),
            correlation_id="vcos-v2-long-form-project-created",
            trusted_v2_admission=True,
        )
        decision_hash = content_hash(
            {
                "decision": "ADMIT",
                "request": data.model_dump(mode="json"),
                "resolution": resolution.model_dump(mode="json"),
            }
        )
        receipt = ProjectAdmissionDecision(
            id=receipt_id,
            schema_version="v2",
            editorial_research_run_id=(
                context.research_run.id if context.research_run is not None else None
            ),
            editorial_idea_candidate_id=(
                context.candidate.id if context.candidate is not None else None
            ),
            editorial_calendar_slot_id=context.slot.id,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            channel_profile_version_id=data.channel_profile_version_id,
            policy_snapshot_id=data.policy_snapshot_id,
            idea_market_preflight_id=context.preflight.id,
            planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
            production_lane=ProductionLane.LONG_FORM,
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
            resolver_version=resolution.resolver_version,
            resolver_input_hash=resolution.resolver_input_hash,
            decision_hash=decision_hash,
            assignment_input_ref=resolver_input.model_dump(mode="json"),
            duration_contract=data.duration_contract.model_dump(mode="json"),
            **self._lineage_values(data),
            budget_gate_result=data.budget_gate_result,
            readiness_gate_refs=self._readiness_refs(context),
            decision="ADMIT",
            reason_codes=[str(code) for code in resolution.reason_codes],
            evidence_refs=list(data.evidence_refs),
            admitted_video_project_id=project.id,
            created_artifact_refs=[],
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(receipt)
        context.slot.status = "ADMITTED"
        if context.candidate is not None:
            context.candidate.stage = "IN_PRODUCTION"
        self.session.flush()
        if project.project_admission_decision_id != receipt.id:
            raise ValidationFailureError(
                "PROJECT_ADMISSION_BIDIRECTIONAL_LINK_MISMATCH"
            )
        return receipt

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

        def create_receipt() -> ProjectAdmissionDecision:
            receipt = ProjectAdmissionDecision(
                schema_version="v2",
                editorial_research_run_id=(
                    context.research_run.id
                    if context.research_run is not None
                    else None
                ),
                editorial_idea_candidate_id=(
                    context.candidate.id if context.candidate is not None else None
                ),
                editorial_calendar_slot_id=context.slot.id,
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
                channel_profile_version_id=data.channel_profile_version_id,
                policy_snapshot_id=data.policy_snapshot_id,
                idea_market_preflight_id=context.preflight.id,
                planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
                production_lane=ProductionLane.LONG_FORM,
                content_mode=None,
                assignment_mode=data.assignment_mode,
                series_plan_id=data.preferred_series_plan_id,
                series_run_id=data.preferred_series_run_id,
                episode_number=None,
                episode_role=data.episode_role,
                standalone_reason_code=None,
                resolver_version=self.resolver.version,
                resolver_input_hash=resolver_input_hash,
                decision_hash=decision_hash,
                assignment_input_ref=resolver_input.model_dump(mode="json"),
                duration_contract=data.duration_contract.model_dump(mode="json"),
                **self._lineage_values(data),
                budget_gate_result=(
                    data.budget_gate_result
                    or {
                        "decision": "BLOCK",
                        "reason_codes": ["BUDGET_AUTHORITY_NOT_AVAILABLE"],
                        "deterministic": True,
                        "schema_version": "v2",
                    }
                ),
                readiness_gate_refs=self._readiness_refs(context),
                decision="BLOCK",
                reason_codes=reason_codes,
                evidence_refs=list(data.evidence_refs),
                admitted_video_project_id=None,
                created_artifact_refs=[],
                created_by_user_id=data.created_by_user_id,
            )
            self.session.add(receipt)
            self.session.flush()
            return receipt

        if not use_savepoint:
            return create_receipt()
        with self.session.begin_nested():
            return create_receipt()

    @staticmethod
    def _readiness_refs(
        context: _AdmissionContext,
    ) -> list[dict[str, Any]]:
        preflight = context.preflight
        evidence = preflight.evidence_blob or {}
        return [
            {
                "type": "idea_market_preflight",
                "id": str(preflight.id),
                "decision": preflight.decision,
                "policy_fit_state": preflight.policy_fit_state,
                "niche_contract_digest_hash": (
                    preflight.niche_contract_digest_hash
                    or evidence.get("niche_contract_digest_hash")
                ),
                "target_market_digest_hash": (
                    preflight.target_market_digest_hash
                    or evidence.get("target_market_digest_hash")
                ),
            }
        ]

    def _lock_source_and_existing(
        self,
        data: ProjectAdmissionV2Request,
    ) -> ProjectAdmissionDecision | None:
        self.session.scalar(
            select(EditorialCalendarSlot)
            .where(EditorialCalendarSlot.id == data.editorial_calendar_slot_id)
            .with_for_update()
        )
        if data.editorial_idea_candidate_id is not None:
            self.session.scalar(
                select(EditorialIdeaCandidate)
                .where(EditorialIdeaCandidate.id == data.editorial_idea_candidate_id)
                .with_for_update()
            )
            existing = self.session.scalar(
                select(ProjectAdmissionDecision).where(
                    ProjectAdmissionDecision.schema_version == "v2",
                    ProjectAdmissionDecision.editorial_idea_candidate_id
                    == data.editorial_idea_candidate_id,
                )
            )
            if existing is not None:
                return existing
        return self.session.scalar(
            select(ProjectAdmissionDecision).where(
                ProjectAdmissionDecision.schema_version == "v2",
                ProjectAdmissionDecision.editorial_calendar_slot_id
                == data.editorial_calendar_slot_id,
            )
        )


class LongFormPlanningService:
    def __init__(
        self,
        session: Session,
        *,
        admission_service: ProjectAdmissionV2Service | None = None,
    ):
        self.session = session
        self.admission_service = admission_service or ProjectAdmissionV2Service(session)

    def admit(
        self,
        data: LongFormPlanningRequest,
    ) -> ProjectAdmissionDecision:
        return self.admission_service.create_decision(
            data=ProjectAdmissionV2Request(
                planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
                channel_profile_version_id=data.channel_profile_version_id,
                policy_snapshot_id=data.policy_snapshot_id,
                editorial_calendar_slot_id=data.editorial_calendar_slot_id,
                editorial_idea_candidate_id=data.editorial_idea_candidate_id,
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
                budget_gate_result=data.budget_gate_result,
                duration_contract=data.duration_contract,
                audience_promise=data.audience_promise,
                audience_promise_version=data.audience_promise_version,
                audience_promise_hash=data.audience_promise_hash,
                target_audience_definition=data.target_audience_definition,
                audience_drift_guard_version=data.audience_drift_guard_version,
                strategic_intent=data.strategic_intent,
                intent_success_criteria=data.intent_success_criteria,
                intent_success_criteria_version=data.intent_success_criteria_version,
                intent_success_criteria_hash=data.intent_success_criteria_hash,
                experiment_hypothesis=data.experiment_hypothesis,
                primary_variable_under_test=data.primary_variable_under_test,
                decision_reversibility=data.decision_reversibility,
                active_launch_policy_version_id=data.active_launch_policy_version_id,
                active_launch_policy_hash=data.active_launch_policy_hash,
                active_launch_run_id=data.active_launch_run_id,
                active_launch_run_hash=data.active_launch_run_hash,
                created_by_user_id=data.created_by_user_id,
            )
        )


class LongFormPackageEligibilityService:
    def __init__(self, session: Session):
        self.session = session

    def require_eligible(
        self,
        video_project_id: uuid.UUID,
    ) -> VideoProject:
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.schema_version == "v2" and (
            project.production_lane != ProductionLane.LONG_FORM
            or project.planning_source_type != PlanningSourceType.LONG_FORM_PLAN
            or project.render_eligible is not True
        ):
            raise ValidationFailureError(
                "LONG_FORM package requires an admitted LONG_FORM project"
            )
        return project


class LegacySeriesReader:
    """Read-only archival classification without creating legacy authority."""

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
            return LegacySeriesClassification.V2_TYPED
        if legacy_series_key and legacy_series_key.strip():
            return LegacySeriesClassification.LEGACY_SERIES_BOUND
        return LegacySeriesClassification.UNRESOLVED_LEGACY

    def classify_project(
        self,
        video_project_id: uuid.UUID,
    ) -> LegacySeriesClassification:
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.schema_version == "v2":
            return LegacySeriesClassification.V2_TYPED
        admission = self.session.scalar(
            select(ProjectAdmissionDecision)
            .where(ProjectAdmissionDecision.admitted_video_project_id == project.id)
            .order_by(ProjectAdmissionDecision.created_at.asc())
        )
        legacy_key: str | None = None
        if admission is not None and admission.editorial_research_run_id is not None:
            research_run = self.session.get(
                EditorialResearchRun,
                admission.editorial_research_run_id,
            )
            slot = (
                self.session.get(
                    EditorialCalendarSlot,
                    research_run.editorial_calendar_slot_id,
                )
                if research_run is not None
                and research_run.editorial_calendar_slot_id is not None
                else None
            )
            legacy_key = slot.series_key if slot is not None else None
        return self.classify_values(
            schema_version="v1",
            series_plan_id=None,
            series_run_id=None,
            legacy_series_key=legacy_key,
        )
