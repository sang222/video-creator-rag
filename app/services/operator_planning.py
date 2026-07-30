"""Authenticated, fail-closed launcher for existing frozen v2 planning sources."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.operator_planning import (
    DailyShortPlanningLaunchRequest,
    LongFormPlanningLaunchRequest,
    OperatorPlanningCatalogRead,
    OperatorPlanningLaunchRead,
    OperatorPlanningOptionRead,
    OperatorPlanningPrepareRead,
    OperatorPlanningPrepareRequest,
    OperatorPlanningStartRequest,
    PlanningSourceKind,
)
from app.contracts.m5 import (
    ChannelDailyRunCreate,
    ChannelStatePackSnapshotCreate,
    ContextPackSnapshotCreate,
    DailyIdeaDecisionCreate,
    IdeaMarketPreflightCreate,
    RetrievalPlanSnapshotCreate,
)
from app.contracts.geo_market import DestinationBinding
from app.contracts.production_workflow import ProductionWorkflowProjectStart
from app.contracts.vcos_v2 import (
    AssignmentMode,
    DurationContractV2,
    LongFormPlanningRequest,
    PlanningSourceType,
    ProductionLane,
    ProjectAdmissionV2Request,
)
from app.core.actor import ActorContext, ActorType
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.foundation import Company, UserRole
from app.db.models.m5 import (
    ChannelDailyRun,
    DailyIdeaDecision,
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    SearchDemandEvidence,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.workflow import VideoProject
from app.services.production_package import ChannelDurationContractResolver
from app.services.production_workflow import ProductionWorkflowCoordinator
from app.services.m5 import (
    DEFAULT_DAILY_CONTEXT_SOURCES,
    ChannelAuthorityService,
    ChannelDailyRunService,
    ChannelStatePackService,
    IdeaMarketPreflightService,
    ResourceResolverService,
)
from app.services.r3d1 import (
    CategoryScopeResolver,
    ChannelRuntimeAuthorityService,
    CharacterBindingResolver,
    ProjectScopeAdmissionGuard,
)
from app.services.rbac import RBACService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.v2_support_authority import (
    TrustedV2SupportProducer,
    V2SupportAuthorityPrepareCommand,
    V2SupportAuthorityResult,
    V2SupportAuthorityService,
)
from app.services.vcos_v2 import (
    LongFormPlanningService,
    ProjectAdmissionV2Service,
)


_SOURCE_LIMIT = 200
_ELIGIBLE_DAILY_STATES = {"PROPOSED", "ADMITTED"}
_NEW_DAILY_RUN_STATES = {"PENDING", "RUNNING", "COMPLETED"}
_NEW_SLOT_STATES = {"OPEN", "ASSIGNED"}
_EXISTING_SLOT_STATES = {*_NEW_SLOT_STATES, "ADMITTED"}


@dataclass(frozen=True, slots=True)
class _ResolvedAuthority:
    workspace: ChannelWorkspace
    profile: ChannelProfileVersion
    policy: CompiledChannelPolicySnapshot
    slot: EditorialCalendarSlot
    preflight: IdeaMarketPreflight
    duration: DurationContractV2
    category_id: uuid.UUID
    character_binding_id: uuid.UUID | None
    existing_admission: ProjectAdmissionDecision | None
    source_title: str
    source_description: str | None
    destination_binding_ref: str
    destination_binding_hash: str


@dataclass(frozen=True, slots=True)
class _PreparedPlanning:
    requested_source_type: PlanningSourceKind
    requested_source_id: uuid.UUID
    lane: ProductionLane
    source_type: PlanningSourceType
    source_id: uuid.UUID
    authority: _ResolvedAuthority
    admission: ProjectAdmissionDecision
    effective_context: EffectiveChannelRuntimeContextSnapshot
    support_authority: V2SupportAuthorityResult
    reused_admission: bool


class _PlanningBlocked(ValidationFailureError):
    def __init__(self, code: str, guidance: str):
        super().__init__(code)
        self.code = code
        self.guidance = guidance


class OperatorPlanningService:
    """Resolve trusted v2 authority and start the durable workflow atomically.

    A frozen Daily slot may be materialized into its typed daily run and idea.
    Missing preflights are created only from persisted evidence. The service
    then creates or reuses admission, project, Effective Context, and the
    canonical trusted support-authority envelope before workflow start.
    """

    def __init__(
        self,
        session: Session,
        *,
        support_producer: TrustedV2SupportProducer | None = None,
        daily_llm_workflow: Any | None = None,
    ):
        self.session = session
        self.support_producer = support_producer
        self.daily_llm_workflow = daily_llm_workflow

    def catalog(
        self,
        *,
        actor: ActorContext,
    ) -> OperatorPlanningCatalogRead:
        accessible_company_ids = self._accessible_company_ids(
            actor=actor,
            permission="production.read",
        )

        daily_statement = (
            select(DailyIdeaDecision)
            .where(
                DailyIdeaDecision.schema_version == "v2",
                DailyIdeaDecision.production_lane == ProductionLane.DAILY_SHORT,
            )
            .order_by(DailyIdeaDecision.created_at.desc(), DailyIdeaDecision.id)
            .limit(_SOURCE_LIMIT)
        )
        daily_slot_statement = (
            select(EditorialCalendarSlot)
            .where(
                EditorialCalendarSlot.schema_version == "v2",
                EditorialCalendarSlot.production_lane == ProductionLane.DAILY_SHORT,
            )
            .order_by(
                EditorialCalendarSlot.slot_date.desc(),
                EditorialCalendarSlot.id,
            )
            .limit(_SOURCE_LIMIT)
        )
        long_statement = (
            select(EditorialCalendarSlot)
            .where(
                EditorialCalendarSlot.schema_version == "v2",
                EditorialCalendarSlot.production_lane == ProductionLane.LONG_FORM,
            )
            .order_by(EditorialCalendarSlot.slot_date.desc(), EditorialCalendarSlot.id)
            .limit(_SOURCE_LIMIT)
        )
        if accessible_company_ids is not None:
            if not accessible_company_ids:
                return OperatorPlanningCatalogRead(
                    generated_at=utc_now(),
                    technical_appendix={
                        "accessible_company_count": 0,
                        "source_limit_per_lane": _SOURCE_LIMIT,
                        "read_model_only": True,
                        "catalog_provider_calls": False,
                        "mr1_execution": False,
                        "automatic_publish": False,
                    },
                )
            daily_statement = daily_statement.where(
                DailyIdeaDecision.company_id.in_(accessible_company_ids)
            )
            daily_slot_statement = daily_slot_statement.where(
                EditorialCalendarSlot.company_id.in_(accessible_company_ids)
            )
            long_statement = long_statement.where(
                EditorialCalendarSlot.company_id.in_(accessible_company_ids)
            )

        daily_ideas = list(self.session.scalars(daily_statement).all())
        represented_daily_slot_ids = {
            run.editorial_calendar_slot_id
            for idea in daily_ideas
            if (run := self.session.get(ChannelDailyRun, idea.channel_daily_run_id))
            is not None
            and run.editorial_calendar_slot_id is not None
        }
        daily_options = [self._daily_option(idea) for idea in daily_ideas]
        daily_options.extend(
            self._daily_slot_option(slot)
            for slot in self.session.scalars(daily_slot_statement).all()
            if slot.id not in represented_daily_slot_ids
        )
        long_options = [
            self._long_option(slot)
            for slot in self.session.scalars(long_statement).all()
        ]
        return OperatorPlanningCatalogRead(
            generated_at=utc_now(),
            daily_short_options=daily_options,
            long_form_options=long_options,
            technical_appendix={
                "accessible_company_count": (
                    "GLOBAL"
                    if accessible_company_ids is None
                    else len(accessible_company_ids)
                ),
                "source_limit_per_lane": _SOURCE_LIMIT,
                "read_model_only": True,
                "catalog_provider_calls": False,
                "mr1_execution": False,
                "automatic_publish": False,
            },
        )

    def prepare_source(
        self,
        *,
        data: OperatorPlanningPrepareRequest,
        actor: ActorContext,
    ) -> OperatorPlanningPrepareRead:
        prepared = self._prepare_source(
            source_type=data.source_type,
            source_id=data.source_id,
            max_budget_usd=data.max_budget_usd,
            actor=actor,
        )
        return self._prepare_read(prepared)

    def prepare_and_launch(
        self,
        *,
        data: OperatorPlanningStartRequest,
        actor: ActorContext,
    ) -> OperatorPlanningLaunchRead:
        prepared = self._prepare_source(
            source_type=data.source_type,
            source_id=data.source_id,
            max_budget_usd=data.max_budget_usd,
            actor=actor,
        )
        return self._start_admitted_project(
            lane=prepared.lane,
            title=prepared.authority.source_title,
            admission=prepared.admission,
            authority=prepared.authority,
            effective=prepared.effective_context,
            support=prepared.support_authority,
            actor=actor,
            company_id=prepared.authority.workspace.company_id,
            max_attempts=data.max_attempts,
            idempotency_key=data.idempotency_key,
            reused_admission=prepared.reused_admission,
        )

    def launch_daily_short(
        self,
        *,
        data: DailyShortPlanningLaunchRequest,
        actor: ActorContext,
    ) -> OperatorPlanningLaunchRead:
        idempotency_key = self._command_idempotency_key(
            provided=data.idempotency_key,
            source_type=PlanningSourceType.DAILY_IDEA,
            source_id=data.daily_idea_decision_id,
        )
        prepared = self._prepare_source(
            source_type=PlanningSourceType.DAILY_IDEA,
            source_id=data.daily_idea_decision_id,
            max_budget_usd=data.max_budget_usd,
            actor=actor,
        )
        return self._start_admitted_project(
            lane=ProductionLane.DAILY_SHORT,
            title=prepared.authority.source_title,
            admission=prepared.admission,
            authority=prepared.authority,
            effective=prepared.effective_context,
            support=prepared.support_authority,
            actor=actor,
            company_id=prepared.authority.workspace.company_id,
            max_attempts=data.max_attempts,
            idempotency_key=idempotency_key,
            reused_admission=prepared.reused_admission,
        )

    def launch_long_form(
        self,
        *,
        data: LongFormPlanningLaunchRequest,
        actor: ActorContext,
    ) -> OperatorPlanningLaunchRead:
        idempotency_key = self._command_idempotency_key(
            provided=data.idempotency_key,
            source_type=PlanningSourceType.LONG_FORM_PLAN,
            source_id=data.editorial_calendar_slot_id,
        )
        prepared = self._prepare_source(
            source_type=PlanningSourceType.LONG_FORM_PLAN,
            source_id=data.editorial_calendar_slot_id,
            max_budget_usd=data.max_budget_usd,
            actor=actor,
        )
        return self._start_admitted_project(
            lane=ProductionLane.LONG_FORM,
            title=prepared.authority.source_title,
            admission=prepared.admission,
            authority=prepared.authority,
            effective=prepared.effective_context,
            support=prepared.support_authority,
            actor=actor,
            company_id=prepared.authority.workspace.company_id,
            max_attempts=data.max_attempts,
            idempotency_key=idempotency_key,
            reused_admission=prepared.reused_admission,
        )

    def _prepare_source(
        self,
        *,
        source_type: PlanningSourceKind | PlanningSourceType,
        source_id: uuid.UUID,
        max_budget_usd: Decimal,
        actor: ActorContext,
    ) -> _PreparedPlanning:
        requested_source_type = (
            source_type.value
            if isinstance(source_type, PlanningSourceType)
            else source_type
        )
        requested_source_id = source_id
        if requested_source_type == "DAILY_SLOT":
            slot = self.session.scalar(
                select(EditorialCalendarSlot)
                .where(EditorialCalendarSlot.id == source_id)
                .with_for_update()
            )
            if slot is None:
                raise NotFoundError("frozen v2 Daily Short slot not found")
            self._require_company_permission(
                actor=actor,
                permission="production.start",
                company_id=slot.company_id,
            )
            idea = self._materialize_daily_source(
                slot=slot,
                actor=actor,
            )
            source_type = PlanningSourceType.DAILY_IDEA
            source_id = idea.id
        elif requested_source_type == PlanningSourceType.DAILY_IDEA.value:
            source_type = PlanningSourceType.DAILY_IDEA
            idea = self.session.scalar(
                select(DailyIdeaDecision)
                .where(DailyIdeaDecision.id == source_id)
                .with_for_update()
            )
            if idea is None:
                raise NotFoundError("frozen v2 Daily Short source not found")
            self._require_company_permission(
                actor=actor,
                permission="production.start",
                company_id=idea.company_id,
            )
        elif requested_source_type == PlanningSourceType.LONG_FORM_PLAN.value:
            source_type = PlanningSourceType.LONG_FORM_PLAN
        else:
            raise ValidationFailureError("PLANNING_SOURCE_TYPE_UNSUPPORTED")

        if source_type == PlanningSourceType.DAILY_IDEA:
            self._active_authority(
                company_id=idea.company_id,
                channel_workspace_id=idea.channel_workspace_id,
                policy_snapshot_id=idea.policy_snapshot_id,
                lane=ProductionLane.DAILY_SHORT,
            )
            self._ensure_daily_preflight(idea)
            authority = self._resolve_daily(idea)
            admission, reused = self._admit_daily(
                idea=idea,
                authority=authority,
                actor=actor,
            )
            lane = ProductionLane.DAILY_SHORT
        else:
            slot = self.session.scalar(
                select(EditorialCalendarSlot)
                .where(EditorialCalendarSlot.id == source_id)
                .with_for_update()
            )
            if slot is None:
                raise NotFoundError("frozen v2 Long-form slot not found")
            self._require_company_permission(
                actor=actor,
                permission="production.start",
                company_id=slot.company_id,
            )
            self._active_authority(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                policy_snapshot_id=slot.policy_snapshot_id,
                lane=ProductionLane.LONG_FORM,
            )
            self._ensure_long_preflight(slot)
            authority = self._resolve_long(
                slot,
                requested_preflight_id=None,
            )
            admission, reused = self._admit_long(
                slot=slot,
                authority=authority,
                actor=actor,
            )
            lane = ProductionLane.LONG_FORM

        if admission.decision != "ADMIT" or admission.admitted_video_project_id is None:
            raise ValidationFailureError(
                "PLANNING_SOURCE_DID_NOT_PRODUCE_ADMITTED_V2_PROJECT"
            )
        effective = EffectiveChannelRuntimeContextCompiler(
            self.session
        ).ensure_for_project(
            admission.admitted_video_project_id,
            editorial_calendar_slot_id=authority.slot.id,
        )
        if effective.compile_status != "PASS":
            self._block(
                "EFFECTIVE_CONTEXT_NOT_PASS",
                "Runtime context của dự án chưa PASS; hãy xử lý các lý do chặn trước.",
            )
        support = V2SupportAuthorityService(self.session).prepare(
            V2SupportAuthorityPrepareCommand(
                video_project_id=admission.admitted_video_project_id,
                source_type=source_type.value,
                source_id=source_id,
                actor_user_id=actor.actor_id,
                max_budget_usd=max_budget_usd,
                idempotency_key=(
                    f"operator-planning-support:{source_type.value}:{source_id}"
                ),
            ),
            producer=self.support_producer,
        )
        self._verify_support_authority(
            result=support,
            source_type=source_type,
            source_id=source_id,
            preflight_id=authority.preflight.id,
        )
        return _PreparedPlanning(
            requested_source_type=requested_source_type,
            requested_source_id=requested_source_id,
            lane=lane,
            source_type=source_type,
            source_id=source_id,
            authority=authority,
            admission=admission,
            effective_context=effective,
            support_authority=support,
            reused_admission=reused,
        )

    def _materialize_daily_source(
        self,
        *,
        slot: EditorialCalendarSlot,
        actor: ActorContext,
    ) -> DailyIdeaDecision:
        existing = self.session.scalars(
            select(DailyIdeaDecision)
            .join(
                ChannelDailyRun,
                ChannelDailyRun.id == DailyIdeaDecision.channel_daily_run_id,
            )
            .where(
                ChannelDailyRun.editorial_calendar_slot_id == slot.id,
                DailyIdeaDecision.company_id == slot.company_id,
                DailyIdeaDecision.channel_workspace_id == slot.channel_workspace_id,
                DailyIdeaDecision.policy_snapshot_id == slot.policy_snapshot_id,
                DailyIdeaDecision.schema_version == "v2",
                DailyIdeaDecision.production_lane == ProductionLane.DAILY_SHORT,
                DailyIdeaDecision.decision_status.in_(_ELIGIBLE_DAILY_STATES),
            )
            .order_by(
                DailyIdeaDecision.created_at.asc(),
                DailyIdeaDecision.id,
            )
            .with_for_update()
        ).first()
        existing_admission = (
            self._existing_admission(
                source_type=PlanningSourceType.DAILY_IDEA,
                source_id=existing.id,
            )
            if existing is not None
            else None
        )
        self._require_slot(
            slot=slot,
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            policy_snapshot_id=slot.policy_snapshot_id,
            lane=ProductionLane.DAILY_SHORT,
            existing=existing_admission,
        )
        if existing is not None:
            return existing

        daily_run = ChannelDailyRunService(self.session).create_run(
            data=ChannelDailyRunCreate(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                policy_snapshot_id=slot.policy_snapshot_id,
                editorial_calendar_slot_id=slot.id,
                run_date=slot.slot_date,
                status="PENDING",
                run_mode="REAL",
                trigger_type="MANUAL",
                metadata={
                    "authority_source": "operator_planning_daily_slot",
                    "schema_version": "v2",
                },
            ),
            correlation_id="operator-planning-daily-run-create",
        )
        daily_run.status = "RUNNING"
        daily_run.started_at = utc_now()
        resolver = ResourceResolverService(self.session)
        plan = resolver.create_retrieval_plan(
            data=RetrievalPlanSnapshotCreate(
                purpose="DAILY_IDEA",
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                policy_snapshot_id=slot.policy_snapshot_id,
                editorial_calendar_slot_id=slot.id,
                allowed_sources=DEFAULT_DAILY_CONTEXT_SOURCES,
                source_order=DEFAULT_DAILY_CONTEXT_SOURCES,
                created_by_user_id=actor.actor_id,
            ),
            correlation_id="operator-planning-daily-retrieval-plan",
        )
        context = resolver.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan.id,
                freshness_state="UNKNOWN",
                confidence_level="UNKNOWN",
                created_by_user_id=actor.actor_id,
            ),
            correlation_id="operator-planning-daily-context-pack",
        )
        state = ChannelStatePackService(self.session).build_snapshot(
            data=ChannelStatePackSnapshotCreate(
                channel_daily_run_id=daily_run.id,
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                policy_snapshot_id=slot.policy_snapshot_id,
                context_pack_snapshot_id=context.id,
            ),
            correlation_id="operator-planning-daily-state-pack",
        )
        idea = ChannelAuthorityService(
            self.session,
            llm_workflow=self.daily_llm_workflow,
        ).create_decision(
            data=DailyIdeaDecisionCreate(
                channel_daily_run_id=daily_run.id,
                context_pack_snapshot_id=context.id,
                channel_state_pack_snapshot_id=state.id,
                schema_version="v2",
                production_lane=ProductionLane.DAILY_SHORT,
                assignment_input_ref={
                    "editorial_calendar_slot_id": str(slot.id),
                    "policy_snapshot_id": str(slot.policy_snapshot_id),
                    "production_lane": "DAILY_SHORT",
                    "assignment_mode": slot.assignment_mode,
                    "preferred_series_plan_id": (
                        str(slot.preferred_series_plan_id)
                        if slot.preferred_series_plan_id is not None
                        else None
                    ),
                    "preferred_series_run_id": (
                        str(slot.preferred_series_run_id)
                        if slot.preferred_series_run_id is not None
                        else None
                    ),
                },
                provider_key="llm_router",
                estimated_cost=Decimal("0"),
            ),
            correlation_id="operator-planning-daily-authority",
        )
        daily_run.context_pack_snapshot_id = context.id
        daily_run.channel_state_pack_snapshot_id = state.id
        daily_run.daily_idea_decision_id = idea.id
        daily_run.status = "COMPLETED"
        daily_run.completed_at = utc_now()
        daily_run.reason_codes = [
            "DAILY_RUN_COMPLETED",
            *list(idea.reason_codes or []),
        ]
        self.session.flush()
        return idea

    def _ensure_daily_preflight(
        self,
        idea: DailyIdeaDecision,
    ) -> IdeaMarketPreflight:
        daily_run = self.session.get(ChannelDailyRun, idea.channel_daily_run_id)
        if daily_run is None or daily_run.editorial_calendar_slot_id is None:
            raise ValidationFailureError("DAILY_RUN_SOURCE_MISMATCH")
        existing = self.session.scalars(
            select(IdeaMarketPreflight)
            .where(
                IdeaMarketPreflight.company_id == idea.company_id,
                IdeaMarketPreflight.channel_workspace_id == idea.channel_workspace_id,
                IdeaMarketPreflight.editorial_calendar_slot_id
                == daily_run.editorial_calendar_slot_id,
                IdeaMarketPreflight.channel_daily_run_id == daily_run.id,
                IdeaMarketPreflight.daily_idea_decision_id == idea.id,
                IdeaMarketPreflight.policy_fit_state == "PASS",
                IdeaMarketPreflight.decision == "PASS",
            )
            .order_by(
                IdeaMarketPreflight.created_at.desc(),
                IdeaMarketPreflight.id,
            )
        ).first()
        if existing is not None:
            return existing
        evidence_ids = self._persisted_search_evidence_ids(
            company_id=idea.company_id,
            channel_workspace_id=idea.channel_workspace_id,
        )
        return IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=idea.company_id,
                channel_workspace_id=idea.channel_workspace_id,
                editorial_calendar_slot_id=(daily_run.editorial_calendar_slot_id),
                channel_daily_run_id=daily_run.id,
                daily_idea_decision_id=idea.id,
                # The source is already bound to the channel's active compiled
                # policy; this is server-derived authority, never caller input.
                policy_fit_state="PASS",
                evidence_blob={
                    "search_demand_evidence_ids": [str(item) for item in evidence_ids],
                    "authority_source": ("PERSISTED_OPERATOR_PLANNING_DAILY_SOURCE"),
                },
            ),
            correlation_id="operator-planning-daily-preflight",
        )

    def _ensure_long_preflight(
        self,
        slot: EditorialCalendarSlot,
    ) -> IdeaMarketPreflight:
        existing = self.session.scalars(
            select(IdeaMarketPreflight)
            .where(
                IdeaMarketPreflight.company_id == slot.company_id,
                IdeaMarketPreflight.channel_workspace_id == slot.channel_workspace_id,
                IdeaMarketPreflight.editorial_calendar_slot_id == slot.id,
                IdeaMarketPreflight.channel_daily_run_id.is_(None),
                IdeaMarketPreflight.daily_idea_decision_id.is_(None),
                IdeaMarketPreflight.policy_fit_state == "PASS",
                IdeaMarketPreflight.decision == "PASS",
            )
            .order_by(
                IdeaMarketPreflight.created_at.desc(),
                IdeaMarketPreflight.id,
            )
        ).first()
        if existing is not None:
            return existing
        evidence_ids = self._persisted_search_evidence_ids(
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
        )
        return IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                editorial_calendar_slot_id=slot.id,
                # The source is already bound to the channel's active compiled
                # policy; this is server-derived authority, never caller input.
                policy_fit_state="PASS",
                evidence_blob={
                    "search_demand_evidence_ids": [str(item) for item in evidence_ids],
                    "authority_source": ("PERSISTED_OPERATOR_PLANNING_LONG_SOURCE"),
                },
            ),
            correlation_id="operator-planning-long-preflight",
        )

    def _persisted_search_evidence_ids(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        return list(
            self.session.scalars(
                select(SearchDemandEvidence.id)
                .where(
                    SearchDemandEvidence.company_id == company_id,
                    SearchDemandEvidence.channel_workspace_id == channel_workspace_id,
                )
                .order_by(
                    SearchDemandEvidence.created_at.desc(),
                    SearchDemandEvidence.id,
                )
                .limit(20)
            ).all()
        )

    def _admit_daily(
        self,
        *,
        idea: DailyIdeaDecision,
        authority: _ResolvedAuthority,
        actor: ActorContext,
    ) -> tuple[ProjectAdmissionDecision, bool]:
        admission = authority.existing_admission
        if admission is not None:
            return admission, True
        daily_run = self.session.get(ChannelDailyRun, idea.channel_daily_run_id)
        assert daily_run is not None
        admission = ProjectAdmissionV2Service(self.session).create_decision(
            data=ProjectAdmissionV2Request(
                planning_source_type=PlanningSourceType.DAILY_IDEA,
                company_id=idea.company_id,
                channel_workspace_id=idea.channel_workspace_id,
                channel_profile_version_id=authority.profile.id,
                policy_snapshot_id=authority.policy.id,
                editorial_calendar_slot_id=authority.slot.id,
                channel_daily_run_id=daily_run.id,
                daily_idea_decision_id=idea.id,
                idea_market_preflight_id=authority.preflight.id,
                production_lane=ProductionLane.DAILY_SHORT,
                assignment_mode=AssignmentMode(authority.slot.assignment_mode),
                preferred_series_plan_id=(authority.slot.preferred_series_plan_id),
                preferred_series_run_id=(authority.slot.preferred_series_run_id),
                title=authority.source_title,
                description=authority.source_description,
                category_id=authority.category_id,
                character_binding_id=authority.character_binding_id,
                duration_contract=authority.duration,
                niche_gate_passed=True,
                market_gate_passed=True,
                timely_niche_opportunity=self._slot_flag(
                    authority.slot, "timely_niche_opportunity"
                ),
                bridge_or_special=self._slot_flag(authority.slot, "bridge_or_special"),
                evidence_refs=list(idea.evidence_refs or []),
                created_by_user_id=actor.actor_id,
            )
        )
        return admission, False

    def _admit_long(
        self,
        *,
        slot: EditorialCalendarSlot,
        authority: _ResolvedAuthority,
        actor: ActorContext,
    ) -> tuple[ProjectAdmissionDecision, bool]:
        admission = authority.existing_admission
        if admission is not None:
            return admission, True
        admission = LongFormPlanningService(self.session).admit(
            LongFormPlanningRequest(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                channel_profile_version_id=authority.profile.id,
                policy_snapshot_id=authority.policy.id,
                editorial_calendar_slot_id=slot.id,
                idea_market_preflight_id=authority.preflight.id,
                assignment_mode=AssignmentMode(slot.assignment_mode),
                title=authority.source_title,
                description=authority.source_description,
                category_id=authority.category_id,
                character_binding_id=authority.character_binding_id,
                preferred_series_plan_id=slot.preferred_series_plan_id,
                preferred_series_run_id=slot.preferred_series_run_id,
                niche_gate_passed=True,
                market_gate_passed=True,
                timely_niche_opportunity=self._slot_flag(
                    slot, "timely_niche_opportunity"
                ),
                bridge_or_special=self._slot_flag(slot, "bridge_or_special"),
                duration_contract=authority.duration,
                created_by_user_id=actor.actor_id,
            )
        )
        return admission, False

    def _prepare_read(
        self,
        prepared: _PreparedPlanning,
    ) -> OperatorPlanningPrepareRead:
        result = prepared.support_authority
        return OperatorPlanningPrepareRead(
            source_type=prepared.requested_source_type,
            source_id=prepared.requested_source_id,
            lane=prepared.lane.value,
            title=prepared.authority.source_title,
            admission_id=prepared.admission.id,
            project_id=prepared.admission.admitted_video_project_id,
            support_artifact_id=result.artifact_id,
            support_artifact_version_id=result.artifact_version_id,
            envelope_hash=result.envelope_hash,
            status=result.status,
            replayed=result.replayed,
            approved_script_hash=result.approved_script_hash,
            approved_script_word_count=result.approved_script_word_count,
            exact_source_refs=[
                item.model_dump(mode="json") for item in result.exact_source_refs
            ],
            reason_codes=result.reason_codes,
            next_action=(
                "Support authority đã được đóng băng. Có thể bắt đầu workflow; "
                "không cần duyệt nội dung trước render."
            ),
            technical_appendix={
                "profile_version_id": prepared.authority.profile.id,
                "policy_snapshot_id": prepared.authority.policy.id,
                "resolved_planning_source_type": (prepared.source_type.value),
                "resolved_planning_source_id": prepared.source_id,
                "idea_market_preflight_id": prepared.authority.preflight.id,
                "duration_contract_hash": (
                    prepared.authority.duration.duration_contract_hash
                ),
                "destination_binding_ref": (prepared.authority.destination_binding_ref),
                "destination_binding_hash": (
                    prepared.authority.destination_binding_hash
                ),
                "effective_context_snapshot_id": (prepared.effective_context.id),
                "effective_context_hash": (prepared.effective_context.context_hash),
                "media_provider_calls": False,
                "mr1_execution": False,
                "automatic_publish": False,
            },
        )

    @staticmethod
    def _verify_support_authority(
        *,
        result: V2SupportAuthorityResult,
        source_type: PlanningSourceType,
        source_id: uuid.UUID,
        preflight_id: uuid.UUID,
    ) -> None:
        source_kind = (
            "FROZEN_DAILY_IDEA"
            if source_type == PlanningSourceType.DAILY_IDEA
            else "FROZEN_EDITORIAL_SLOT"
        )
        source_ref_matches = any(
            ref.source_kind == source_kind and ref.id == source_id
            for ref in result.exact_source_refs
        )
        preflight_ref_matches = any(
            ref.source_kind == "TYPED_MARKET_PREFLIGHT" and ref.id == preflight_id
            for ref in result.exact_source_refs
        )
        hashes = (result.envelope_hash, result.approved_script_hash)
        if (
            result.status != "APPROVED"
            or result.approved_script_word_count < 24
            or any(
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in hashes
            )
            or not source_ref_matches
            or not preflight_ref_matches
        ):
            raise ValidationFailureError("V2_SUPPORT_AUTHORITY_RESULT_INVALID")

    @staticmethod
    def _command_idempotency_key(
        *,
        provided: str | None,
        source_type: PlanningSourceType,
        source_id: uuid.UUID,
    ) -> str:
        if provided:
            return provided
        return f"operator-planning:{source_type.value}:{source_id}"

    def _daily_option(self, idea: DailyIdeaDecision) -> OperatorPlanningOptionRead:
        workspace = self.session.get(ChannelWorkspace, idea.channel_workspace_id)
        run = self.session.get(ChannelDailyRun, idea.channel_daily_run_id)
        slot = (
            self.session.get(EditorialCalendarSlot, run.editorial_calendar_slot_id)
            if run is not None and run.editorial_calendar_slot_id is not None
            else None
        )
        try:
            authority = self._resolve_daily(idea)
            return self._ready_option(
                source_id=idea.id,
                source_type="DAILY_IDEA",
                lane=ProductionLane.DAILY_SHORT,
                title=authority.source_title,
                workspace=authority.workspace,
                slot=authority.slot,
                duration=authority.duration,
                admission=authority.existing_admission,
                preflight=authority.preflight,
                destination_binding_ref=(authority.destination_binding_ref),
                destination_binding_hash=(authority.destination_binding_hash),
            )
        except _PlanningBlocked as exc:
            if exc.code == "DAILY_PREFLIGHT_NOT_PASS" and slot is not None:
                return self._slot_preparation_option(
                    slot=slot,
                    source_type="DAILY_IDEA",
                    source_id=idea.id,
                    lane=ProductionLane.DAILY_SHORT,
                )
            return self._blocked_option(
                source_id=idea.id,
                source_type="DAILY_IDEA",
                lane=ProductionLane.DAILY_SHORT,
                title=idea.proposed_title,
                workspace=workspace,
                slot=slot,
                code=exc.code,
                guidance=exc.guidance,
            )

    def _daily_slot_option(
        self,
        slot: EditorialCalendarSlot,
    ) -> OperatorPlanningOptionRead:
        return self._slot_preparation_option(
            slot=slot,
            source_type="DAILY_SLOT",
            lane=ProductionLane.DAILY_SHORT,
        )

    def _long_option(self, slot: EditorialCalendarSlot) -> OperatorPlanningOptionRead:
        workspace = self.session.get(ChannelWorkspace, slot.channel_workspace_id)
        try:
            authority = self._resolve_long(slot)
            return self._ready_option(
                source_id=slot.id,
                source_type="LONG_FORM_PLAN",
                lane=ProductionLane.LONG_FORM,
                title=authority.source_title,
                workspace=authority.workspace,
                slot=slot,
                duration=authority.duration,
                admission=authority.existing_admission,
                preflight=authority.preflight,
                destination_binding_ref=(authority.destination_binding_ref),
                destination_binding_hash=(authority.destination_binding_hash),
            )
        except _PlanningBlocked as exc:
            if exc.code == "LONG_FORM_PREFLIGHT_NOT_PASS":
                return self._slot_preparation_option(
                    slot=slot,
                    source_type="LONG_FORM_PLAN",
                    lane=ProductionLane.LONG_FORM,
                )
            return self._blocked_option(
                source_id=slot.id,
                source_type="LONG_FORM_PLAN",
                lane=ProductionLane.LONG_FORM,
                title=slot.production_goal
                or f"Video dài ngày {slot.slot_date.isoformat()}",
                workspace=workspace,
                slot=slot,
                code=exc.code,
                guidance=exc.guidance,
            )

    def _slot_preparation_option(
        self,
        *,
        slot: EditorialCalendarSlot,
        source_type: PlanningSourceKind,
        source_id: uuid.UUID | None = None,
        lane: ProductionLane,
    ) -> OperatorPlanningOptionRead:
        option_source_id = source_id or slot.id
        workspace = self.session.get(ChannelWorkspace, slot.channel_workspace_id)
        try:
            self._require_slot(
                slot=slot,
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                policy_snapshot_id=slot.policy_snapshot_id,
                lane=lane,
                existing=None,
            )
            authority = self._active_authority(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                policy_snapshot_id=slot.policy_snapshot_id,
                lane=lane,
            )
            if not self._persisted_search_evidence_ids(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
            ):
                self._block(
                    "SEARCH_DEMAND_EVIDENCE_MISSING",
                    "Kênh chưa có search-demand evidence persisted để backend chạy strict preflight.",
                )
            if slot.category_id is None:
                self._block(
                    "CATEGORY_SCOPE_MISSING",
                    "Lịch nội dung chưa khóa danh mục nội dung.",
                )
            category = CategoryScopeResolver(self.session).resolve_explicit(
                company_id=slot.company_id,
                channel_workspace_id=slot.channel_workspace_id,
                category_id=slot.category_id,
                source="editorial_calendar_slot",
            )
            if not category.ok or category.category is None:
                self._block(
                    "CATEGORY_SCOPE_BLOCKED",
                    self._scope_guidance(category.reason_codes),
                )
            binding = CharacterBindingResolver(self.session).resolve(
                category=category.category,
                explicit_character_binding_id=(self._slot_character_binding_id(slot)),
            )
            if not binding.ok:
                self._block(
                    "CHARACTER_BINDING_BLOCKED",
                    self._scope_guidance(binding.reason_codes),
                )
            self._require_series_assignment(slot, authority.profile, authority.policy)
            destination_ref, destination_hash = self._verified_destination_authority(
                authority.workspace
            )
            return self._ready_option(
                source_id=option_source_id,
                source_type=source_type,
                lane=lane,
                title=(
                    slot.production_goal
                    or (
                        "Daily Short"
                        if lane == ProductionLane.DAILY_SHORT
                        else "Video dài"
                    )
                ),
                workspace=authority.workspace,
                slot=slot,
                duration=authority.duration,
                admission=None,
                preflight=None,
                destination_binding_ref=destination_ref,
                destination_binding_hash=destination_hash,
            )
        except _PlanningBlocked as exc:
            return self._blocked_option(
                source_id=option_source_id,
                source_type=source_type,
                lane=lane,
                title=slot.production_goal
                or (
                    "Daily Short" if lane == ProductionLane.DAILY_SHORT else "Video dài"
                ),
                workspace=workspace,
                slot=slot,
                code=exc.code,
                guidance=exc.guidance,
            )

    def _resolve_daily(self, idea: DailyIdeaDecision) -> _ResolvedAuthority:
        if (
            idea.schema_version != "v2"
            or idea.production_lane != ProductionLane.DAILY_SHORT
            or idea.decision_status not in _ELIGIBLE_DAILY_STATES
        ):
            self._block(
                "DAILY_IDEA_NOT_FROZEN_ELIGIBLE",
                "Ý tưởng chưa phải proposal/frozen v2 Daily Short đủ điều kiện.",
            )
        daily_run = self.session.get(ChannelDailyRun, idea.channel_daily_run_id)
        if (
            daily_run is None
            or daily_run.company_id != idea.company_id
            or daily_run.channel_workspace_id != idea.channel_workspace_id
            or daily_run.policy_snapshot_id != idea.policy_snapshot_id
            or daily_run.daily_idea_decision_id not in {None, idea.id}
            or daily_run.editorial_calendar_slot_id is None
        ):
            self._block(
                "DAILY_RUN_SOURCE_MISMATCH",
                "Daily run không còn khớp chính xác với ý tưởng đã đóng băng.",
            )
        existing = self._existing_admission(
            source_type=PlanningSourceType.DAILY_IDEA,
            source_id=idea.id,
        )
        if existing is None and daily_run.status not in _NEW_DAILY_RUN_STATES:
            self._block(
                "DAILY_RUN_NOT_ELIGIBLE",
                "Daily run đã bị chặn, hủy hoặc thất bại; cần tạo nguồn kế hoạch mới.",
            )
        slot = self.session.get(
            EditorialCalendarSlot, daily_run.editorial_calendar_slot_id
        )
        self._require_slot(
            slot=slot,
            company_id=idea.company_id,
            channel_workspace_id=idea.channel_workspace_id,
            policy_snapshot_id=idea.policy_snapshot_id,
            lane=ProductionLane.DAILY_SHORT,
            existing=existing,
        )
        assert slot is not None
        frozen_slot_id = self._frozen_daily_slot_id(idea)
        if frozen_slot_id != slot.id:
            self._block(
                "DAILY_IDEA_FROZEN_SLOT_MISMATCH",
                "Ý tưởng không còn trỏ đúng lịch nội dung đã đóng băng.",
            )
        authority = self._active_authority(
            company_id=idea.company_id,
            channel_workspace_id=idea.channel_workspace_id,
            policy_snapshot_id=idea.policy_snapshot_id,
            lane=ProductionLane.DAILY_SHORT,
        )
        preflight = self._daily_preflight(
            idea=idea,
            daily_run=daily_run,
            slot=slot,
            existing=existing,
        )
        if slot.category_id is None:
            self._block(
                "CATEGORY_SCOPE_MISSING",
                "Lịch nội dung chưa khóa danh mục nội dung; hãy bổ sung trước khi chạy.",
            )
        scope = ProjectScopeAdmissionGuard(self.session).evaluate_for_daily_run(
            daily_run,
            explicit_category_id=slot.category_id,
        )
        if not scope.ok or scope.category_id is None:
            self._block(
                "DAILY_RUNTIME_SCOPE_BLOCKED",
                self._scope_guidance(scope.reason_codes),
            )
        self._require_series_assignment(slot, authority.profile, authority.policy)
        destination_binding_ref, destination_binding_hash = (
            self._verified_destination_authority(authority.workspace)
        )
        resolved = _ResolvedAuthority(
            workspace=authority.workspace,
            profile=authority.profile,
            policy=authority.policy,
            slot=slot,
            preflight=preflight,
            duration=authority.duration,
            category_id=scope.category_id,
            character_binding_id=scope.character_binding_id,
            existing_admission=existing,
            source_title=idea.proposed_title,
            source_description=idea.proposed_angle,
            destination_binding_ref=destination_binding_ref,
            destination_binding_hash=destination_binding_hash,
        )
        self._require_existing_authority(
            authority=resolved,
            lane=ProductionLane.DAILY_SHORT,
            source_id=idea.id,
        )
        return resolved

    def _resolve_long(
        self,
        slot: EditorialCalendarSlot,
        *,
        requested_preflight_id: uuid.UUID | None = None,
    ) -> _ResolvedAuthority:
        existing = self._existing_admission(
            source_type=PlanningSourceType.LONG_FORM_PLAN,
            source_id=slot.id,
        )
        self._require_slot(
            slot=slot,
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            policy_snapshot_id=slot.policy_snapshot_id,
            lane=ProductionLane.LONG_FORM,
            existing=existing,
        )
        authority = self._active_authority(
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            policy_snapshot_id=slot.policy_snapshot_id,
            lane=ProductionLane.LONG_FORM,
        )
        preflight = self._long_preflight(
            slot=slot,
            existing=existing,
            requested_preflight_id=requested_preflight_id,
        )
        if slot.category_id is None:
            self._block(
                "CATEGORY_SCOPE_MISSING",
                "Lịch video dài chưa khóa danh mục nội dung; hãy bổ sung trước khi chạy.",
            )
        channel_authority = ChannelRuntimeAuthorityService(self.session).resolve(
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            policy_snapshot_id=slot.policy_snapshot_id,
        )
        if not channel_authority.ok:
            self._block(
                "CHANNEL_RUNTIME_AUTHORITY_BLOCKED",
                self._scope_guidance(channel_authority.reason_codes),
            )
        category = CategoryScopeResolver(self.session).resolve_explicit(
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            category_id=slot.category_id,
            source="editorial_calendar_slot",
        )
        if not category.ok or category.category is None:
            self._block(
                "CATEGORY_SCOPE_BLOCKED",
                self._scope_guidance(category.reason_codes),
            )
        binding = CharacterBindingResolver(self.session).resolve(
            category=category.category,
            explicit_character_binding_id=self._slot_character_binding_id(slot),
        )
        if not binding.ok:
            self._block(
                "CHARACTER_BINDING_BLOCKED",
                self._scope_guidance(binding.reason_codes),
            )
        self._require_series_assignment(slot, authority.profile, authority.policy)
        source_title = self._long_source_title(
            slot=slot,
            preflight=preflight,
        )
        destination_binding_ref, destination_binding_hash = (
            self._verified_destination_authority(authority.workspace)
        )
        resolved = _ResolvedAuthority(
            workspace=authority.workspace,
            profile=authority.profile,
            policy=authority.policy,
            slot=slot,
            preflight=preflight,
            duration=authority.duration,
            category_id=category.category.id,
            character_binding_id=(
                binding.character_binding.id
                if binding.character_binding is not None
                else None
            ),
            existing_admission=existing,
            source_title=source_title,
            source_description=slot.production_goal,
            destination_binding_ref=destination_binding_ref,
            destination_binding_hash=destination_binding_hash,
        )
        self._require_existing_authority(
            authority=resolved,
            lane=ProductionLane.LONG_FORM,
            source_id=slot.id,
        )
        return resolved

    @dataclass(frozen=True, slots=True)
    class _ActiveAuthority:
        workspace: ChannelWorkspace
        profile: ChannelProfileVersion
        policy: CompiledChannelPolicySnapshot
        duration: DurationContractV2

    def _active_authority(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID,
        lane: ProductionLane,
    ) -> _ActiveAuthority:
        workspace = self.session.get(ChannelWorkspace, channel_workspace_id)
        policy = self.session.get(CompiledChannelPolicySnapshot, policy_snapshot_id)
        if (
            workspace is None
            or workspace.company_id != company_id
            or workspace.active_policy_snapshot_id != policy_snapshot_id
            or policy is None
            or policy.channel_workspace_id != channel_workspace_id
            or policy.status != "active"
        ):
            self._block(
                "ACTIVE_POLICY_SOURCE_MISMATCH",
                "Nguồn kế hoạch không còn dùng policy đang active của kênh.",
            )
        profile = self.session.get(
            ChannelProfileVersion, policy.channel_profile_version_id
        )
        if (
            profile is None
            or profile.channel_workspace_id != workspace.id
            or profile.status not in {"approved", "active"}
        ):
            self._block(
                "ACTIVE_PROFILE_SOURCE_MISMATCH",
                "Profile đã duyệt của nguồn kế hoạch không còn hợp lệ.",
            )
        try:
            duration = ChannelDurationContractResolver(self.session).resolve(
                profile_version_id=profile.id,
                policy_snapshot_id=policy.id,
                production_lane=lane,
            )
        except ValidationFailureError:
            self._block(
                "DURATION_CONTRACT_NOT_RESOLVABLE",
                "Kênh chưa có duration contract v2 khớp giữa profile và policy.",
            )
        return self._ActiveAuthority(
            workspace=workspace,
            profile=profile,
            policy=policy,
            duration=duration,
        )

    def _daily_preflight(
        self,
        *,
        idea: DailyIdeaDecision,
        daily_run: ChannelDailyRun,
        slot: EditorialCalendarSlot,
        existing: ProjectAdmissionDecision | None,
    ) -> IdeaMarketPreflight:
        preferred_id = (
            existing.idea_market_preflight_id if existing is not None else None
        )
        statement = select(IdeaMarketPreflight).where(
            IdeaMarketPreflight.company_id == idea.company_id,
            IdeaMarketPreflight.channel_workspace_id == idea.channel_workspace_id,
            IdeaMarketPreflight.editorial_calendar_slot_id == slot.id,
            IdeaMarketPreflight.channel_daily_run_id == daily_run.id,
            IdeaMarketPreflight.daily_idea_decision_id == idea.id,
        )
        preflight = (
            self.session.get(IdeaMarketPreflight, preferred_id)
            if preferred_id is not None
            else self.session.scalars(
                statement.order_by(
                    IdeaMarketPreflight.created_at.desc(),
                    IdeaMarketPreflight.id,
                )
            ).first()
        )
        if (
            preflight is None
            or preflight.company_id != idea.company_id
            or preflight.channel_workspace_id != idea.channel_workspace_id
            or preflight.editorial_calendar_slot_id != slot.id
            or preflight.channel_daily_run_id != daily_run.id
            or preflight.daily_idea_decision_id != idea.id
            or preflight.policy_fit_state != "PASS"
            or preflight.decision != "PASS"
        ):
            self._block(
                "DAILY_PREFLIGHT_NOT_PASS",
                "Ý tưởng chưa có preflight PASS khớp chính xác với Daily run và lịch.",
            )
        return preflight

    def _long_preflight(
        self,
        *,
        slot: EditorialCalendarSlot,
        existing: ProjectAdmissionDecision | None,
        requested_preflight_id: uuid.UUID | None,
    ) -> IdeaMarketPreflight:
        preferred_id = requested_preflight_id or (
            existing.idea_market_preflight_id if existing is not None else None
        )
        if preferred_id is not None:
            preflight = self.session.get(IdeaMarketPreflight, preferred_id)
        else:
            preflight = self.session.scalars(
                select(IdeaMarketPreflight)
                .where(
                    IdeaMarketPreflight.company_id == slot.company_id,
                    IdeaMarketPreflight.channel_workspace_id
                    == slot.channel_workspace_id,
                    IdeaMarketPreflight.editorial_calendar_slot_id == slot.id,
                    IdeaMarketPreflight.channel_daily_run_id.is_(None),
                    IdeaMarketPreflight.daily_idea_decision_id.is_(None),
                    IdeaMarketPreflight.policy_fit_state == "PASS",
                    IdeaMarketPreflight.decision == "PASS",
                )
                .order_by(
                    IdeaMarketPreflight.created_at.desc(),
                    IdeaMarketPreflight.id,
                )
            ).first()
        if (
            preflight is None
            or preflight.company_id != slot.company_id
            or preflight.channel_workspace_id != slot.channel_workspace_id
            or preflight.editorial_calendar_slot_id != slot.id
            or preflight.channel_daily_run_id is not None
            or preflight.daily_idea_decision_id is not None
            or preflight.policy_fit_state != "PASS"
            or preflight.decision != "PASS"
        ):
            self._block(
                "LONG_FORM_PREFLIGHT_NOT_PASS",
                "Lịch video dài chưa có preflight PASS khớp chính xác.",
            )
        if existing is not None and existing.idea_market_preflight_id != preflight.id:
            self._block(
                "LONG_FORM_PREFLIGHT_IMMUTABLE_MISMATCH",
                "Admission hiện có đã khóa một preflight khác; cần tạo source version mới.",
            )
        return preflight

    def _long_source_title(
        self,
        *,
        slot: EditorialCalendarSlot,
        preflight: IdeaMarketPreflight,
    ) -> str:
        evidence = (
            preflight.evidence_blob if isinstance(preflight.evidence_blob, dict) else {}
        )
        raw_title = next(
            (
                value
                for key in ("proposed_title", "title")
                if isinstance((value := evidence.get(key)), str) and value.strip()
            ),
            slot.production_goal,
        )
        title = raw_title.strip() if isinstance(raw_title, str) else ""
        if not title:
            self._block(
                "LONG_FORM_FROZEN_TITLE_REQUIRED",
                "PASS preflight hoặc lịch video dài chưa khóa tiêu đề.",
            )
        if len(title) > 500:
            self._block(
                "LONG_FORM_FROZEN_TITLE_INVALID",
                "Tiêu đề đã đóng băng vượt quá 500 ký tự; cần source version mới.",
            )
        return title

    def _verified_destination_authority(
        self,
        workspace: ChannelWorkspace,
    ) -> tuple[str, str]:
        metadata = workspace.metadata_ if isinstance(workspace.metadata_, dict) else {}
        governance = metadata.get("destination_governance")
        bindings = governance.get("bindings") if isinstance(governance, dict) else None
        active_ref = (
            str(governance.get("active_binding_ref") or "")
            if isinstance(governance, dict)
            else ""
        )
        active = (
            next(
                (
                    item
                    for item in bindings
                    if isinstance(item, dict)
                    and active_ref
                    == (
                        f"destination-binding://{workspace.key}/"
                        f"v{item.get('binding_version')}"
                    )
                ),
                None,
            )
            if isinstance(bindings, list) and active_ref
            else None
        )
        try:
            validated = (
                DestinationBinding.model_validate(active)
                if isinstance(active, dict)
                else None
            )
        except Exception:
            validated = None
        expected_ref = (
            f"destination-binding://{workspace.key}/v{validated.binding_version}"
            if validated is not None
            else ""
        )
        if (
            validated is None
            or active_ref != expected_ref
            or validated.channel_id != workspace.id
            or validated.channel_key != workspace.key
            or validated.destination_status != "VERIFIED"
            or validated.verification_state != "VERIFIED"
            or not validated.platform_channel_id
            or not validated.platform_account_ref
            or not validated.content_hash
            or active.get("content_hash") != validated.content_hash
        ):
            self._block(
                "VERIFIED_DESTINATION_REQUIRED",
                "Kênh chưa có destination binding active đã VERIFIED; hãy hoàn tất xác minh trong Channel Profile.",
            )
        return active_ref, validated.content_hash

    def _require_slot(
        self,
        *,
        slot: EditorialCalendarSlot | None,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID,
        lane: ProductionLane,
        existing: ProjectAdmissionDecision | None,
    ) -> None:
        allowed_states = (
            _EXISTING_SLOT_STATES if existing is not None else _NEW_SLOT_STATES
        )
        if (
            slot is None
            or slot.company_id != company_id
            or slot.channel_workspace_id != channel_workspace_id
            or slot.policy_snapshot_id != policy_snapshot_id
            or slot.schema_version != "v2"
            or slot.production_lane != lane
            or slot.assignment_mode not in {item.value for item in AssignmentMode}
            or slot.status not in allowed_states
            or slot.series_key is not None
        ):
            self._block(
                "EDITORIAL_SLOT_NOT_FROZEN_ELIGIBLE",
                "Lịch nội dung typed v2 không còn đủ điều kiện hoặc chứa series key cũ.",
            )

    def _require_series_assignment(
        self,
        slot: EditorialCalendarSlot,
        profile: ChannelProfileVersion,
        policy: CompiledChannelPolicySnapshot,
    ) -> None:
        if slot.assignment_mode != AssignmentMode.SERIES_REQUIRED:
            return
        if (
            slot.preferred_series_plan_id is None
            or slot.preferred_series_run_id is None
        ):
            self._block(
                "SERIES_REQUIRED_BINDING_MISSING",
                "Lịch yêu cầu series nhưng chưa khóa đúng kế hoạch và đợt chạy.",
            )
        plan = self.session.get(SeriesPlan, slot.preferred_series_plan_id)
        run = self.session.get(SeriesRun, slot.preferred_series_run_id)
        envelope = (
            slot.operational_envelope
            if isinstance(slot.operational_envelope, dict)
            else {}
        )
        coherence = envelope.get("series_coherence_scores")
        coherence_score = (
            coherence.get(str(run.id), 0)
            if isinstance(coherence, dict) and run is not None
            else 0
        )
        schedule_ok = (
            run is not None
            and (
                run.schedule_window_start is None
                or slot.slot_date >= run.schedule_window_start.date()
            )
            and (
                run.schedule_window_end is None
                or slot.slot_date <= run.schedule_window_end.date()
            )
        )
        if (
            plan is None
            or run is None
            or run.series_plan_id != plan.id
            or plan.company_id != slot.company_id
            or plan.channel_workspace_id != slot.channel_workspace_id
            or plan.channel_profile_version_id != profile.id
            or plan.policy_snapshot_id != policy.id
            or plan.state != "APPROVED"
            or run.state != "ACTIVE"
            or run.reserved_episode_count >= run.capacity
            or not schedule_ok
            or not isinstance(coherence_score, (int, float))
            or coherence_score <= 0
            or slot.production_lane not in (plan.allowed_production_lanes or [])
        ):
            self._block(
                "SERIES_REQUIRED_BINDING_NOT_ELIGIBLE",
                "Series bắt buộc chưa active, đã hết chỗ hoặc không khớp lịch hiện tại.",
            )

    def _require_existing_authority(
        self,
        *,
        authority: _ResolvedAuthority,
        lane: ProductionLane,
        source_id: uuid.UUID,
    ) -> None:
        admission = authority.existing_admission
        if admission is None:
            return
        source_matches = (
            admission.daily_idea_decision_id == source_id
            if lane == ProductionLane.DAILY_SHORT
            else admission.editorial_calendar_slot_id == source_id
        )
        project = self._existing_project(admission)
        effective = (
            self.session.get(
                EffectiveChannelRuntimeContextSnapshot,
                project.effective_context_snapshot_id,
            )
            if project is not None and project.effective_context_snapshot_id is not None
            else None
        )
        exact_duration = authority.duration.model_dump(mode="json")
        if (
            admission.schema_version != "v2"
            or admission.decision != "ADMIT"
            or not source_matches
            or admission.company_id != authority.workspace.company_id
            or admission.channel_workspace_id != authority.workspace.id
            or admission.channel_profile_version_id != authority.profile.id
            or admission.policy_snapshot_id != authority.policy.id
            or admission.idea_market_preflight_id != authority.preflight.id
            or admission.production_lane != lane
            or admission.assignment_mode != authority.slot.assignment_mode
            or admission.duration_contract != exact_duration
            or project is None
            or project.project_admission_decision_id != admission.id
            or project.company_id != admission.company_id
            or project.channel_workspace_id != admission.channel_workspace_id
            or project.channel_profile_version_id
            != admission.channel_profile_version_id
            or project.policy_snapshot_id != admission.policy_snapshot_id
            or project.production_lane != admission.production_lane
            or project.planning_source_type != admission.planning_source_type
            or project.category_id != authority.category_id
            or project.character_binding_id != authority.character_binding_id
            or project.duration_contract != exact_duration
            or project.title != authority.source_title
            or project.description != authority.source_description
            or (
                effective is not None
                and (
                    effective.video_project_id != project.id
                    or effective.compile_status != "PASS"
                )
            )
        ):
            self._block(
                "EXISTING_ADMISSION_AUTHORITY_MISMATCH",
                "Admission hiện có không còn khớp source/profile/policy/duration; cần source version mới.",
            )

    def _start_admitted_project(
        self,
        *,
        lane: ProductionLane,
        title: str,
        admission: ProjectAdmissionDecision,
        authority: _ResolvedAuthority,
        effective: EffectiveChannelRuntimeContextSnapshot,
        support: V2SupportAuthorityResult,
        actor: ActorContext,
        company_id: uuid.UUID,
        max_attempts: int,
        idempotency_key: str | None,
        reused_admission: bool,
    ) -> OperatorPlanningLaunchRead:
        if admission.decision != "ADMIT" or admission.admitted_video_project_id is None:
            raise ValidationFailureError(
                "PLANNING_SOURCE_DID_NOT_PRODUCE_ADMITTED_V2_PROJECT"
            )
        exact = _ResolvedAuthority(
            workspace=authority.workspace,
            profile=authority.profile,
            policy=authority.policy,
            slot=authority.slot,
            preflight=authority.preflight,
            duration=authority.duration,
            category_id=authority.category_id,
            character_binding_id=authority.character_binding_id,
            existing_admission=admission,
            source_title=authority.source_title,
            source_description=authority.source_description,
            destination_binding_ref=authority.destination_binding_ref,
            destination_binding_hash=authority.destination_binding_hash,
        )
        self._require_existing_authority(
            authority=exact,
            lane=lane,
            source_id=(
                admission.daily_idea_decision_id
                if lane == ProductionLane.DAILY_SHORT
                else admission.editorial_calendar_slot_id
            )
            or uuid.UUID(int=0),
        )
        self._verify_support_authority(
            result=support,
            source_type=PlanningSourceType(admission.planning_source_type),
            source_id=(
                admission.daily_idea_decision_id
                if lane == ProductionLane.DAILY_SHORT
                else admission.editorial_calendar_slot_id
            )
            or uuid.UUID(int=0),
            preflight_id=authority.preflight.id,
        )
        existing_workflow = self.session.scalars(
            select(ProductionWorkflowRun)
            .where(
                ProductionWorkflowRun.video_project_id
                == admission.admitted_video_project_id
            )
            .order_by(ProductionWorkflowRun.created_at.asc())
        ).first()
        workflow = ProductionWorkflowCoordinator(self.session).start_from_project(
            video_project_id=admission.admitted_video_project_id,
            company_id=company_id,
            data=ProductionWorkflowProjectStart(
                max_attempts=max_attempts,
                idempotency_key=idempotency_key,
            ),
            actor=actor,
        )
        state = (
            workflow.state.value
            if hasattr(workflow.state, "value")
            else str(workflow.state)
        )
        return OperatorPlanningLaunchRead(
            lane=lane.value,
            title=title,
            admission_id=admission.id,
            project_id=admission.admitted_video_project_id,
            workflow_run_id=workflow.id,
            workflow_state=state,
            reused_admission=reused_admission,
            reused_workflow=existing_workflow is not None,
            next_action=(
                "Workflow đã được xếp lịch bền vững. Mở dự án để theo dõi; "
                "hệ thống không tự publish/upload."
            ),
            technical_appendix={
                "planning_source_type": admission.planning_source_type,
                "planning_source_id": (
                    admission.daily_idea_decision_id
                    if lane == ProductionLane.DAILY_SHORT
                    else admission.editorial_calendar_slot_id
                ),
                "profile_version_id": authority.profile.id,
                "policy_snapshot_id": authority.policy.id,
                "duration_contract_hash": (authority.duration.duration_contract_hash),
                "support_authority_artifact_id": support.artifact_id,
                "support_authority_artifact_version_id": (support.artifact_version_id),
                "support_authority_envelope_hash": support.envelope_hash,
                "support_authority_replayed": support.replayed,
                "approved_script_hash": support.approved_script_hash,
                "approved_script_word_count": (support.approved_script_word_count),
                "destination_binding_ref": (authority.destination_binding_ref),
                "destination_binding_hash": (authority.destination_binding_hash),
                "effective_context_snapshot_id": effective.id,
                "effective_context_hash": effective.context_hash,
                "media_provider_calls": False,
                "mr1_execution": False,
                "automatic_publish": False,
            },
        )

    def _ready_option(
        self,
        *,
        source_id: uuid.UUID,
        source_type: PlanningSourceKind,
        lane: ProductionLane,
        title: str,
        workspace: ChannelWorkspace,
        slot: EditorialCalendarSlot,
        duration: DurationContractV2,
        admission: ProjectAdmissionDecision | None,
        preflight: IdeaMarketPreflight | None,
        destination_binding_ref: str,
        destination_binding_hash: str,
    ) -> OperatorPlanningOptionRead:
        project = self._existing_project(admission)
        workflow = (
            self.session.scalars(
                select(ProductionWorkflowRun)
                .where(ProductionWorkflowRun.video_project_id == project.id)
                .order_by(ProductionWorkflowRun.created_at.asc())
            ).first()
            if project is not None
            else None
        )
        state = (
            "WORKFLOW_STARTED"
            if workflow is not None
            else "ALREADY_ADMITTED"
            if admission is not None
            else "READY"
        )
        return OperatorPlanningOptionRead(
            source_id=source_id,
            source_type=source_type,
            lane=lane.value,
            title=title,
            company_label=self._company_label(workspace.company_id),
            channel_label=workspace.name,
            slot_label=self._slot_label(slot),
            assignment_label=self._assignment_label(slot.assignment_mode),
            duration_label=self._duration_label(duration),
            state=state,
            status_label={
                "READY": "Sẵn sàng chuẩn bị và tạo dự án",
                "ALREADY_ADMITTED": "Dự án sẵn sàng chuẩn bị",
                "WORKFLOW_STARTED": "Đã xếp lịch sản xuất",
            }[state],
            launchable=True,
            guidance=(
                "Mở dự án đang chạy để theo dõi."
                if workflow is not None
                else "Đóng băng support authority rồi dùng lại dự án typed v2."
                if admission is not None
                else "Đóng băng support authority, tạo dự án typed v2 và bắt đầu workflow trong một thao tác."
            ),
            project_id=project.id if project is not None else None,
            workflow_run_id=workflow.id if workflow is not None else None,
            technical_appendix={
                "source_id": source_id,
                "editorial_calendar_slot_id": slot.id,
                "idea_market_preflight_id": (
                    preflight.id if preflight is not None else None
                ),
                "project_admission_decision_id": (
                    admission.id if admission is not None else None
                ),
                "assignment_mode": slot.assignment_mode,
                "duration_contract_hash": duration.duration_contract_hash,
                "support_authority_preparation_required": workflow is None,
                "destination_binding_ref": destination_binding_ref,
                "destination_binding_hash": destination_binding_hash,
            },
        )

    def _blocked_option(
        self,
        *,
        source_id: uuid.UUID,
        source_type: PlanningSourceKind,
        lane: ProductionLane,
        title: str,
        workspace: ChannelWorkspace | None,
        slot: EditorialCalendarSlot | None,
        code: str,
        guidance: str,
    ) -> OperatorPlanningOptionRead:
        return OperatorPlanningOptionRead(
            source_id=source_id,
            source_type=source_type,
            lane=lane.value,
            title=title,
            company_label=(
                self._company_label(workspace.company_id)
                if workspace is not None
                else "Công ty không còn tồn tại"
            ),
            channel_label=workspace.name
            if workspace is not None
            else "Kênh không còn tồn tại",
            slot_label=self._slot_label(slot),
            assignment_label=(
                self._assignment_label(slot.assignment_mode)
                if slot is not None
                else "Chưa xác định"
            ),
            state="BLOCKED",
            status_label="Chưa đủ điều kiện",
            launchable=False,
            guidance=guidance,
            technical_appendix={
                "source_id": source_id,
                "reason_code": code,
                "editorial_calendar_slot_id": slot.id if slot is not None else None,
            },
        )

    def _existing_admission(
        self,
        *,
        source_type: PlanningSourceType,
        source_id: uuid.UUID,
    ) -> ProjectAdmissionDecision | None:
        source_filter = (
            ProjectAdmissionDecision.daily_idea_decision_id == source_id
            if source_type == PlanningSourceType.DAILY_IDEA
            else ProjectAdmissionDecision.editorial_calendar_slot_id == source_id
        )
        return self.session.scalars(
            select(ProjectAdmissionDecision)
            .where(
                ProjectAdmissionDecision.schema_version == "v2",
                ProjectAdmissionDecision.planning_source_type == source_type,
                source_filter,
            )
            .order_by(ProjectAdmissionDecision.created_at.asc())
        ).first()

    def _existing_project(
        self, admission: ProjectAdmissionDecision | None
    ) -> VideoProject | None:
        if admission is None or admission.admitted_video_project_id is None:
            return None
        return self.session.get(VideoProject, admission.admitted_video_project_id)

    @staticmethod
    def _frozen_daily_slot_id(
        idea: DailyIdeaDecision,
    ) -> uuid.UUID | None:
        payload = (
            idea.assignment_input_ref
            if isinstance(idea.assignment_input_ref, dict)
            else {}
        )
        raw = payload.get("editorial_calendar_slot_id", payload.get("slot_id"))
        if raw in (None, ""):
            return None
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _slot_character_binding_id(
        slot: EditorialCalendarSlot,
    ) -> uuid.UUID | None:
        payload = (
            slot.character_binding_policy_json
            if isinstance(slot.character_binding_policy_json, dict)
            else {}
        )
        raw = payload.get("character_binding_id")
        if raw in (None, ""):
            return None
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError, AttributeError) as exc:
            raise _PlanningBlocked(
                "CHARACTER_BINDING_ID_INVALID",
                "Lịch nội dung chứa character binding không hợp lệ.",
            ) from exc

    @staticmethod
    def _slot_flag(slot: EditorialCalendarSlot, key: str) -> bool:
        envelope = (
            slot.operational_envelope
            if isinstance(slot.operational_envelope, dict)
            else {}
        )
        return envelope.get(key) is True

    @staticmethod
    def _slot_label(slot: EditorialCalendarSlot | None) -> str:
        if slot is None:
            return "Chưa xác định lịch"
        lane_label = (
            "Short hằng ngày"
            if slot.production_lane == ProductionLane.DAILY_SHORT
            else "Video dài"
        )
        return f"{lane_label} · {slot.slot_date.strftime('%d/%m/%Y')}"

    @staticmethod
    def _assignment_label(value: str | None) -> str:
        return {
            "SERIES_REQUIRED": "Bắt buộc theo series",
            "SERIES_PREFERRED": "Ưu tiên theo series",
            "STANDALONE_REQUIRED": "Video độc lập",
            "OPEN_MIX": "Linh hoạt series/độc lập",
        }.get(value or "", "Chưa xác định")

    @staticmethod
    def _duration_label(duration: DurationContractV2) -> str:
        minimum = duration.minimum_duration_ms / 1000
        target = duration.target_duration_ms / 1000
        maximum = duration.maximum_duration_ms / 1000
        return f"Mục tiêu {target:g}s · khoảng {minimum:g}–{maximum:g}s"

    @staticmethod
    def _scope_guidance(reason_codes: list[str]) -> str:
        reasons = set(reason_codes)
        if "CHANNEL_CONTRACT_NOT_COMPLETE" in reasons:
            return "Hồ sơ kênh chưa hoàn chỉnh; hãy duyệt và activate policy trước."
        if "CATEGORY_SCOPE_MISSING" in reasons:
            return "Chưa khóa danh mục nội dung cho lịch này."
        if "CATEGORY_NOT_ACTIVE" in reasons:
            return "Danh mục nội dung chưa active."
        if "CHARACTER_REQUIRED_BUT_MISSING" in reasons:
            return "Danh mục yêu cầu nhân vật nhưng chưa có binding đủ điều kiện."
        if reasons & {
            "CHARACTER_BINDING_NOT_ACTIVE",
            "CHARACTER_ASSET_PACK_MISSING",
            "CHARACTER_VOICE_PROFILE_MISSING",
        }:
            return "Character binding chưa đủ trạng thái, quyền hoặc voice/asset cần thiết."
        return "Nguồn kế hoạch chưa đủ authority vận hành để khởi động an toàn."

    @staticmethod
    def _block(code: str, guidance: str) -> None:
        raise _PlanningBlocked(code, guidance)

    def _require_company_permission(
        self,
        *,
        actor: ActorContext,
        permission: str,
        company_id: uuid.UUID,
    ) -> None:
        if (
            actor.actor_type != ActorType.HUMAN_USER
            or actor.operator_user_id is None
            or not actor.has_permission(permission)
            or not RBACService(self.session).user_has_permission(
                user_id=actor.actor_id,
                permission=permission,
                company_id=company_id,
            )
        ):
            raise ForbiddenError(f"missing permission: {permission}")

    def _accessible_company_ids(
        self,
        *,
        actor: ActorContext,
        permission: str,
    ) -> set[uuid.UUID] | None:
        """Return None for a trusted global role, otherwise exact company scope."""

        if (
            actor.actor_type != ActorType.HUMAN_USER
            or actor.operator_user_id is None
            or not actor.has_permission(permission)
        ):
            raise ForbiddenError(f"missing permission: {permission}")
        rbac = RBACService(self.session)
        global_permissions = rbac.permissions_for_user(
            user_id=actor.actor_id,
            company_id=None,
        )
        if "*" in global_permissions or permission in global_permissions:
            return None
        assigned_company_ids = set(
            self.session.scalars(
                select(UserRole.company_id).where(
                    UserRole.user_id == actor.actor_id,
                    UserRole.company_id.is_not(None),
                )
            ).all()
        )
        return {
            company_id
            for company_id in assigned_company_ids
            if company_id is not None
            and rbac.user_has_permission(
                user_id=actor.actor_id,
                permission=permission,
                company_id=company_id,
            )
        }

    def _company_label(self, company_id: uuid.UUID) -> str:
        company = self.session.get(Company, company_id)
        return company.name if company is not None else "Công ty không còn tồn tại"
