from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.contracts.launch_cadence import (
    CadenceDecision,
    CadenceEvaluationCommand,
    CadenceEvaluationRequest,
    FirstChannelLaunchPolicyCreate,
    LaunchPolicyApproval,
    LaunchRunCreate,
    LaunchRunTransition,
    LaunchRunwayProjection,
    RunwayCounts,
)
from app.contracts.m5 import (
    EditorialCalendarSlotCreate,
    EditorialIdeaCandidateCreate,
    EditorialIdeaCandidateTransition,
    EditorialResearchRunCreate,
    IdeaMarketPreflightCreate,
    SearchDemandEvidenceCreate,
)
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.vcos_v2 import (
    AssignmentMode,
    ProductionLane,
    SeriesPlanCreate,
    SeriesPlanState,
    SeriesPlanTransitionRequest,
    SeriesRunCreate,
    SeriesRunState,
    SeriesRunTransitionRequest,
)
from app.core.actor import _system_worker_actor, authenticated_actor_context
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    HumanUploadTask,
    MediaRenderJob,
    VideoProject,
)
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import (
    CadenceEvaluationReceipt,
    LongFormPublishSlot,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.production_workflow import WorkflowRecoveryReceipt
from app.db.models.ops import DeadLetterJob, OpsIncident, ProviderAttempt
from app.services.editorial_research import EditorialResearchService
from app.services.launch_cadence import (
    FirstChannelLaunchPolicyService,
    LaunchRunService,
    LongFormCadenceService,
)
from app.services.m5 import (
    EditorialCalendarService,
    IdeaMarketPreflightService,
    SearchDemandEvidenceService,
)
from app.services.stale_workflow_recovery import (
    STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
    StaleWorkflowRecoveryService,
)
from app.services.r3d1 import R3D1AdminService
from app.services.rbac import RBACService
from app.services.vcos_v2 import SeriesPlanService, SeriesRunService
from app.workers.production_workflow import ProductionWorkflowWorker
from tests.qualification.conftest import QualificationFactory


@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)


def _actor(session, scope, *, admin: bool = False):
    user = scope.admin if admin else scope.operator
    permissions = RBACService(session).permissions_for_user(
        user_id=user.id,
        company_id=scope.company.id,
    )
    return authenticated_actor_context(
        canonical_user_id=user.id,
        operator_user_id=user.id,
        actor_role="OWNER_ADMIN" if admin else "PRODUCER",
        permissions=permissions,
    )


def _ready_provider_snapshot():
    return SimpleNamespace(
        providers=[
            SimpleNamespace(
                provider_key=provider_key,
                readiness_state="READY_FOR_REAL_EXECUTION",
                blocker_reason_codes=[],
                no_call_was_made=True,
            )
            for provider_key in ("elevenlabs", "google_drive_archive")
        ],
        real_network_probe_enabled=False,
        no_network_calls_made=True,
    )


def _test_support_authority_preparer(*_args) -> None:
    """Keep cadence unit tests network-free; support sealing has its own suite."""


def _approved_series_plan(session, scope, index: int | str):
    service = SeriesPlanService(session)
    record = service.create(
        SeriesPlanCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            stable_series_key=f"launch-series-{index}",
            display_name=f"Launch Series {index}",
            editorial_promise=f"Evidence-led series promise {index}",
            allowed_production_lanes=[ProductionLane.LONG_FORM],
            created_by_user_id=scope.admin.id,
        )
    )
    service.transition(
        record.id,
        SeriesPlanTransitionRequest(
            target_state=SeriesPlanState.APPROVED,
            actor_user_id=scope.admin.id,
            reason_codes=["INITIAL_LAUNCH_SERIES_APPROVED"],
            evidence_refs=[
                {
                    "type": "channel_init",
                    "ref": f"qualification://series/{index}",
                }
            ],
        ),
    )
    return record


def _approved_series_plans(session, scope) -> list:
    return [_approved_series_plan(session, scope, index) for index in (1, 2)]


def _approved_series_run(
    session,
    *,
    plan,
    actor_user_id: uuid.UUID,
    run_number: int = 1,
    priority: int = 0,
    activate: bool = True,
):
    service = SeriesRunService(session)
    run = service.create(
        SeriesRunCreate(
            series_plan_id=plan.id,
            run_key=f"{plan.stable_series_key}-run-{run_number}",
            run_number=run_number,
            capacity=10,
            priority=priority,
            created_by_user_id=actor_user_id,
        )
    )
    service.transition(
        run.id,
        SeriesRunTransitionRequest(
            target_state=SeriesRunState.APPROVED,
            actor_user_id=actor_user_id,
            reason_codes=["SERIES_RUN_APPROVED"],
        ),
    )
    if activate:
        service.transition(
            run.id,
            SeriesRunTransitionRequest(
                target_state=SeriesRunState.ACTIVE,
                actor_user_id=actor_user_id,
                reason_codes=["SERIES_RUN_ACTIVATED"],
            ),
        )
    return run


def _approved_launch_policy(
    session,
    scope,
    *,
    timezone_name: str = "UTC",
    weekdays: list[str] | None = None,
):
    actor = _actor(session, scope, admin=True)
    plans = _approved_series_plans(session, scope)
    evidence = [{"type": "channel_init", "ref": "qualification://launch"}]
    service = FirstChannelLaunchPolicyService(session)
    policy = service.create(
        data=FirstChannelLaunchPolicyCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            approved_initial_series_plan_ids=[item.id for item in plans],
            timezone=timezone_name,
            publish_weekdays=weekdays or ["TUESDAY", "SATURDAY"],
            publish_local_time="10:00",
            evidence_refs=evidence,
        ),
        actor=actor,
    )
    approved = service.approve(
        policy_version_id=policy.id,
        data=LaunchPolicyApproval(evidence_refs=evidence),
        actor=actor,
    )
    return approved, actor, plans


def _active_launch_run(session, policy, actor, *, started_on: date):
    service = LaunchRunService(session)
    run = service.create(
        data=LaunchRunCreate(
            launch_policy_version_id=policy.id,
            launch_key="qualification-launch",
            preparation_started_on=started_on,
        ),
        actor=actor,
    )
    service.transition(
        launch_run_id=run.id,
        data=LaunchRunTransition(
            target_state="READY_TO_LAUNCH",
            reason_codes=["RUNWAY_PREPARATION_COMPLETE"],
        ),
        actor=actor,
    )
    return service.transition(
        launch_run_id=run.id,
        data=LaunchRunTransition(
            target_state="ACTIVE",
            reason_codes=["CONTROLLED_LAUNCH_ACTIVE"],
        ),
        actor=actor,
    )


def _greenlit_candidate(session, scope, actor):
    category = R3D1AdminService(session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"cadence-{uuid.uuid4().hex[:8]}",
            name="Cadence Long-form",
            sub_niche="small-team systems",
            audience_segment="small professional teams",
            content_pillar="AI automation workflows",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
        )
    )
    research_slot = EditorialCalendarService(session).create_slot(
        data=EditorialCalendarSlotCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id,
            slot_date=date(2026, 8, 3),
            slot_type="RESEARCH",
            schema_version="v2",
            production_lane=ProductionLane.LONG_FORM,
            assignment_mode=AssignmentMode.OPEN_MIX,
            production_goal="Audit one bounded automation",
            target_platforms=["YOUTUBE"],
            content_pillar="AI automation workflows",
            created_by_user_id=scope.operator.id,
        )
    )
    research = EditorialResearchService(session)
    run = research.create_run(
        data=EditorialResearchRunCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            editorial_calendar_slot_id=research_slot.id,
            run_date=research_slot.slot_date,
            trigger_type="TEST",
            metadata={"provider_execution": "DISABLED"},
        ),
        actor=actor,
    )
    research.start_run(run_id=run.id, actor=actor)
    evidence = SearchDemandEvidenceService(session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            evidence_source_type="MANUAL_RESEARCH",
            query="small team automation audit",
            platform="YOUTUBE",
            geo="US",
            search_volume_30d=1200,
            relative_interest_index=Decimal("70"),
            competition_index=Decimal("0.30"),
            evidence_confidence="MEDIUM",
        )
    )
    candidate = research.add_candidate(
        data=EditorialIdeaCandidateCreate(
            editorial_research_run_id=run.id,
            proposed_title="How a Small Team Audits One Automation",
            proposed_angle="A bounded evidence-led operating walkthrough.",
            proposed_format="long-form explainer",
            proposed_pillar="AI automation workflows",
            evidence_refs=[{"type": "search_demand_evidence", "id": str(evidence.id)}],
            confidence_level="MEDIUM",
            budget_readiness="READY",
            rights_policy_state="PASS",
            quality_state="PASS",
            experiment_phase="AUDIENCE_PROMISE",
        ),
        actor=actor,
    )
    assert candidate.budget_readiness == "UNKNOWN"
    assert candidate.rights_policy_state == "UNKNOWN"
    assert candidate.quality_state == "UNKNOWN"
    preflight = IdeaMarketPreflightService(session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            editorial_calendar_slot_id=research_slot.id,
            editorial_research_run_id=run.id,
            editorial_idea_candidate_id=candidate.id,
            demand_score=Decimal("60"),
            channel_fit_score=Decimal("0.90"),
            policy_fit_state="PASS",
            niche_contract_digest_ref=f"niche-contract://{scope.channel.id}",
            niche_contract_digest_hash="a" * 64,
            target_market_digest_ref=f"target-market://{scope.channel.id}/US",
            target_market_digest_hash="b" * 64,
            editorial_slot_ref=f"editorial-slot://{research_slot.id}",
            content_category_ref=str(category.id),
            target_market="US",
            market_scope=["US"],
            market_fit_score=Decimal("0.90"),
            market_fit_threshold=Decimal("0.60"),
            evidence_blob={"search_demand_evidence_ids": [str(evidence.id)]},
        )
    )
    research.transition_candidate(
        candidate_id=candidate.id,
        data=EditorialIdeaCandidateTransition(
            target_stage="PREFLIGHT_PASS",
            idea_market_preflight_id=preflight.id,
            reason_codes=["STRICT_LONG_FORM_PREFLIGHT_PASS"],
        ),
        actor=actor,
    )
    assert candidate.budget_readiness == "UNKNOWN"
    assert candidate.rights_policy_state == "PASS"
    assert candidate.quality_state == "PASS"
    assert candidate.evidence_refs == [
        {
            "type": "search_demand_evidence",
            "id": str(evidence.id),
            "evidence_source_type": "MANUAL_RESEARCH",
            "query": "small team automation audit",
            "platform": "YOUTUBE",
            "geo": "US",
            "search_volume_30d": 1200,
            "relative_interest_index": "70",
            "competition_index": "0.30",
            "confidence": "MEDIUM",
            "captured_at": evidence.captured_at.isoformat(),
        }
    ]
    research.transition_candidate(
        candidate_id=candidate.id,
        data=EditorialIdeaCandidateTransition(
            target_stage="GREENLIT",
            idea_market_preflight_id=preflight.id,
            reason_codes=["DETERMINISTIC_GREENLIGHT"],
        ),
        actor=actor,
    )
    research.complete_run(run_id=run.id, actor=actor)
    return run, candidate, preflight


def test_launch_policy_is_exact_channel_scoped_and_hash_bound(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(name="Launch Policy")
    policy, _, plans = _approved_launch_policy(db_session, scope)

    assert policy.state == "APPROVED"
    assert policy.channel_profile_version_id == scope.profile.id
    assert policy.policy_snapshot_id == scope.snapshot.id
    assert policy.approved_initial_series_plan_ids == [str(item.id) for item in plans]
    assert policy.duration_source == "CHANNEL_DURATION_CONTRACT"
    assert len(policy.canonical_hash) == 64
    assert policy.idea_candidates_target == 12
    assert policy.preflight_pass_target == 8
    assert policy.greenlight_target == 6
    assert policy.public_ready_buffer_target == 3
    assert policy.auto_niche_pivot is False
    assert policy.auto_series_kill is False
    assert policy.auto_playbook_promotion is False
    assert policy.pre_render_script_review is False
    assert policy.pre_render_package_review is False
    assert policy.public_publish == "MANUAL_ONLY"


def test_editorial_research_candidate_has_no_production_side_effect(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Research Only", strict_long_form=True
    )
    actor = _actor(db_session, scope)
    before = {
        "projects": db_session.scalar(select(func.count()).select_from(VideoProject)),
        "renders": db_session.scalar(select(func.count()).select_from(MediaRenderJob)),
    }
    _, candidate, _ = _greenlit_candidate(db_session, scope, actor)

    assert candidate.stage == "GREENLIT"
    assert (
        db_session.scalar(select(func.count()).select_from(VideoProject))
        == before["projects"]
    )
    assert (
        db_session.scalar(select(func.count()).select_from(MediaRenderJob))
        == before["renders"]
    )


def test_tuesday_slots_use_policy_timezone_and_minimum_interval(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(name="Cadence Slots")
    policy, actor, _ = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY", "SATURDAY"]
    )
    run = _active_launch_run(db_session, policy, actor, started_on=date(2026, 7, 20))
    fixed_now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    slots = LongFormCadenceService(db_session, now=lambda: fixed_now).ensure_slots(
        run.id
    )

    assert slots
    assert all(
        item.intended_publish_at.astimezone(timezone.utc).weekday() in {1, 5}
        for item in slots
    )
    assert all(
        (right.intended_publish_at - left.intended_publish_at).total_seconds()
        >= policy.minimum_publish_interval_hours * 3600
        for left, right in zip(slots, slots[1:])
    )
    assert len({item.local_publish_date for item in slots}) == len(slots)


def test_buffer_below_target_starts_exactly_one_long_form_workflow(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Cadence Start", strict_long_form=True
    )
    policy, admin_actor, _ = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    run = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    producer_actor = _actor(db_session, scope)
    _, candidate, _ = _greenlit_candidate(db_session, scope, producer_actor)
    fixed_now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    service = LongFormCadenceService(
        db_session,
        now=lambda: fixed_now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    first = service.evaluate(
        launch_run_id=run.id,
        data=CadenceEvaluationCommand(evaluation_key="start-window-1"),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )
    replay = service.evaluate(
        launch_run_id=run.id,
        data=CadenceEvaluationCommand(evaluation_key="start-window-1"),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )

    assert first.id == replay.id
    assert first.decision == CadenceDecision.START_LONG_FORM_PRODUCTION
    assert first.selected_candidate_id == candidate.id
    assert first.admitted_video_project_id is not None
    assert first.production_workflow_run_id is not None
    project = db_session.get(VideoProject, first.admitted_video_project_id)
    workflow = db_session.get(ProductionWorkflowRun, first.production_workflow_run_id)
    assert project.production_lane == "LONG_FORM"
    assert project.planning_source_type == "LONG_FORM_PLAN"
    assert workflow.production_lane == "LONG_FORM"
    assert (
        db_session.scalar(
            select(func.count(ProductionWorkflowRun.id)).where(
                ProductionWorkflowRun.channel_workspace_id == scope.channel.id
            )
        )
        == 1
    )
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def _stale_zero_effect_workflow(db_session, qualification_factory):
    scope = qualification_factory.channel_scope(
        name="Stale workflow recovery", strict_long_form=True
    )
    policy, admin_actor, _ = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    launch_run = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    producer_actor = _actor(db_session, scope)
    _greenlit_candidate(db_session, scope, producer_actor)
    receipt = LongFormCadenceService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    ).evaluate(
        launch_run_id=launch_run.id,
        data=CadenceEvaluationCommand(evaluation_key="stale-zero-effect-start"),
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    workflow = db_session.get(ProductionWorkflowRun, receipt.production_workflow_run_id)
    project = db_session.get(VideoProject, receipt.admitted_video_project_id)
    origin_event = db_session.scalar(
        select(DomainEvent)
        .where(DomainEvent.workflow_run_id == workflow.id)
        .order_by(DomainEvent.occurred_at)
        .limit(1)
    )
    assert workflow is not None and project is not None and origin_event is not None
    project.effective_context_snapshot_id = None
    workflow.state = "DEAD_LETTERED"
    workflow.current_stage = "RESEARCH"
    workflow.state_reason_codes = ["STAGE_RETRY_EXHAUSTED"]
    dead_letter = DeadLetterJob(
        queue_name="production-workflow",
        job_type="production.workflow.stage",
        target_type="production_workflow_run",
        target_id=workflow.id,
        domain_event_id=origin_event.id,
        workflow_run_id=workflow.id,
        command_id=origin_event.command_id,
        fail_count=5,
        replay_state="REPLAYABLE",
        retry_eligible=True,
        reason_code="STAGE_RETRY_EXHAUSTED",
        next_action="Historical fixture retry.",
    )
    incident = OpsIncident(
        incident_type="STAGE_RETRY_EXHAUSTED",
        severity="ERROR",
        state="OPEN",
        project_id=project.id,
        workflow_run_id=workflow.id,
        stage="RESEARCH",
        domain_event_id=origin_event.id,
        command_id=origin_event.command_id,
        retry_eligible=True,
        learning_excluded=True,
        operator_visible_blocker="Historical research retry exhaustion.",
        reason_codes=["STAGE_RETRY_EXHAUSTED"],
        next_action="Do not replay until recovery is classified.",
    )
    db_session.add_all([dead_letter, incident])
    db_session.flush()
    return workflow, dead_letter, incident, origin_event


def test_zero_effect_stale_dead_letter_auto_supersedes_once(
    db_session, qualification_factory
) -> None:
    workflow, dead_letter, incident, origin_event = _stale_zero_effect_workflow(
        db_session, qualification_factory
    )

    recovery = StaleWorkflowRecoveryService(db_session)
    assert recovery.enqueue_due() == 1
    event = db_session.scalar(
        select(DomainEvent).where(
            DomainEvent.event_type == STALE_WORKFLOW_RECOVERY_EVENT_TYPE
        )
    )
    assert event is not None
    receipt = recovery.execute_event(
        event=event,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    db_session.flush()

    assert receipt.workflow_run_id == workflow.id
    assert receipt.dead_letter_job_id == dead_letter.id
    assert receipt.incident_id == incident.id
    assert receipt.proof["effect_invocation_count"] == 0
    assert receipt.proof["provider_attempt_count"] == 0
    assert receipt.proof["budget_reservation_count"] == 0
    assert receipt.proof["budget_settlement_count"] == 0
    assert workflow.state == "SUPERSEDED"
    assert dead_letter.replay_state == "DISCARDED"
    assert dead_letter.retry_eligible is False
    assert incident.state == "RESOLVED"
    assert incident.resolution_evidence["workflow_recovery_receipt_id"] == str(
        receipt.id
    )
    assert origin_event.id == dead_letter.domain_event_id
    assert (
        recovery.execute_event(
            event=event,
            actor=_system_worker_actor(
                "vcos-durable-worker", permissions={"production.start"}
            ),
        ).id
        == receipt.id
    )
    assert recovery.enqueue_due() == 0
    assert db_session.scalar(select(func.count(WorkflowRecoveryReceipt.id))) == 1
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def test_effectful_stale_dead_letter_is_never_auto_superseded(
    db_session, qualification_factory
) -> None:
    workflow, _dead_letter, _incident, _origin_event = _stale_zero_effect_workflow(
        db_session, qualification_factory
    )
    db_session.add(
        ProviderAttempt(
            provider_key="elevenlabs",
            operation_key="final_narration",
            target_type="production_workflow_run",
            target_id=workflow.id,
            attempt_number=1,
            status="SUCCESS",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
    )
    db_session.flush()

    assert StaleWorkflowRecoveryService(db_session).enqueue_due() == 0
    assert workflow.state == "DEAD_LETTERED"
    assert (
        db_session.scalar(
            select(func.count(WorkflowRecoveryReceipt.id)).where(
                WorkflowRecoveryReceipt.workflow_run_id == workflow.id
            )
        )
        == 0
    )


def test_series_run_activation_enforces_launch_allowlist_and_maximum(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(name="Launch Series Guard")
    policy, actor, plans = _approved_launch_policy(db_session, scope)
    _active_launch_run(db_session, policy, actor, started_on=date(2026, 7, 20))
    outsider_plan = _approved_series_plan(db_session, scope, "outside")
    outsider_run = _approved_series_run(
        db_session,
        plan=outsider_plan,
        actor_user_id=scope.admin.id,
        activate=False,
    )

    with pytest.raises(
        ValidationFailureError,
        match="LAUNCH_ACTIVE_SERIES_OUTSIDE_INITIAL_POLICY",
    ):
        SeriesRunService(db_session).transition(
            outsider_run.id,
            SeriesRunTransitionRequest(
                target_state=SeriesRunState.ACTIVE,
                actor_user_id=scope.admin.id,
                reason_codes=["OUTSIDE_SERIES_ACTIVATION_ATTEMPT"],
            ),
        )

    assert outsider_run.state == SeriesRunState.APPROVED
    assert outsider_run.activated_at is None

    _approved_series_run(
        db_session,
        plan=plans[0],
        actor_user_id=scope.admin.id,
    )
    _approved_series_run(
        db_session,
        plan=plans[1],
        actor_user_id=scope.admin.id,
    )
    third_allowed_run = _approved_series_run(
        db_session,
        plan=plans[0],
        actor_user_id=scope.admin.id,
        run_number=2,
        activate=False,
    )

    with pytest.raises(
        ValidationFailureError,
        match="LAUNCH_MAX_ACTIVE_SERIES_EXCEEDED",
    ):
        SeriesRunService(db_session).transition(
            third_allowed_run.id,
            SeriesRunTransitionRequest(
                target_state=SeriesRunState.ACTIVE,
                actor_user_id=scope.admin.id,
                reason_codes=["THIRD_ACTIVE_RUN_ATTEMPT"],
            ),
        )

    assert third_allowed_run.state == SeriesRunState.APPROVED
    assert third_allowed_run.activated_at is None


def test_open_mix_admission_filters_series_outside_launch_policy(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Launch Admission Guard", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(db_session, scope)
    launch_run = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    allowed_run = _approved_series_run(
        db_session,
        plan=plans[0],
        actor_user_id=scope.admin.id,
        priority=-100,
    )
    outsider_plan = _approved_series_plan(db_session, scope, "admission-outside")
    outsider_run = _approved_series_run(
        db_session,
        plan=outsider_plan,
        actor_user_id=scope.admin.id,
        priority=1_000_000,
        activate=False,
    )
    # Simulate a pre-existing/corrupt active row so admission must defend its
    # own authority boundary even after activation is hardened.
    outsider_run.state = SeriesRunState.ACTIVE
    outsider_run.activated_at = datetime.now(timezone.utc)
    db_session.flush()

    producer_actor = _actor(db_session, scope)
    _greenlit_candidate(db_session, scope, producer_actor)
    receipt = LongFormCadenceService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    ).evaluate(
        launch_run_id=launch_run.id,
        data=CadenceEvaluationCommand(evaluation_key="open-mix-launch-guard"),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )

    assert receipt.admitted_video_project_id is not None
    project = db_session.get(VideoProject, receipt.admitted_video_project_id)
    assert receipt.decision == CadenceDecision.START_LONG_FORM_PRODUCTION
    assert project.content_mode == "SERIES_EPISODE"
    assert project.series_plan_id == plans[0].id
    assert project.series_run_id == allowed_run.id
    assert project.series_plan_id != outsider_plan.id
    assert allowed_run.reserved_episode_count == 1
    assert outsider_run.reserved_episode_count == 0


def test_cadence_wait_matrix_never_forces_bad_input() -> None:
    run = SimpleNamespace(state="ACTIVE")
    policy = SimpleNamespace(
        state="APPROVED",
        public_ready_buffer_target=3,
        max_concurrent_productions=1,
        quality_fallback_long_form_per_week=1,
    )

    def projection(buffer: int) -> LaunchRunwayProjection:
        return LaunchRunwayProjection(
            launch_run_id=uuid.uuid4(),
            launch_policy_version_id=uuid.uuid4(),
            as_of=datetime.now(timezone.utc),
            counts=RunwayCounts(),
            public_ready_buffer=buffer,
            active_series=0,
        )

    decision, _ = LongFormCadenceService._decision(
        run=run,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(3),
        active_count=0,
        candidates=[object()],
        incidents=[],
        budget_blocked=False,
        rights_blocked=False,
        quality_blocked=False,
    )
    assert decision == CadenceDecision.WAIT_BUFFER_FULL

    decision, _ = LongFormCadenceService._decision(
        run=run,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(0),
        active_count=0,
        candidates=[],
        incidents=[],
        budget_blocked=True,
        rights_blocked=False,
        quality_blocked=False,
    )
    assert decision == CadenceDecision.WAIT_BUDGET_BLOCKED

    decision, reasons = LongFormCadenceService._decision(
        run=run,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(0),
        active_count=0,
        candidates=[object()],
        incidents=[],
        budget_blocked=False,
        rights_blocked=False,
        quality_blocked=False,
        provider_blocked=True,
    )
    assert decision == CadenceDecision.WAIT_PROVIDER_AUTHORITY
    assert reasons == ["MANDATORY_REAL_PROVIDER_AUTHORITY_BLOCKED"]

    decision, _ = LongFormCadenceService._decision(
        run=run,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(0),
        active_count=0,
        candidates=[],
        incidents=[],
        budget_blocked=False,
        rights_blocked=True,
        quality_blocked=False,
    )
    assert decision == CadenceDecision.WAIT_POLICY_OR_RIGHTS_BLOCKED

    decision, _ = LongFormCadenceService._decision(
        run=run,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(0),
        active_count=0,
        candidates=[object()],
        incidents=[],
        budget_blocked=True,
        rights_blocked=False,
        quality_blocked=False,
    )
    assert decision == CadenceDecision.WAIT_BUDGET_BLOCKED

    decision, _ = LongFormCadenceService._decision(
        run=run,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(0),
        active_count=0,
        candidates=[],
        incidents=[],
        budget_blocked=False,
        rights_blocked=False,
        quality_blocked=True,
    )
    assert decision == CadenceDecision.WAIT_QUALITY_BLOCKED

    paused = SimpleNamespace(state="PAUSED")
    decision, _ = LongFormCadenceService._decision(
        run=paused,
        policy=policy,
        slot=object(),
        next_open=None,
        projection=projection(0),
        active_count=0,
        candidates=[object()],
        incidents=[],
        budget_blocked=False,
        rights_blocked=False,
        quality_blocked=False,
    )
    assert decision == CadenceDecision.WAIT_LAUNCH_NOT_ACTIVE


def test_cadence_worker_owns_automatic_scan_and_direct_evaluation() -> None:
    run_once_source = inspect.getsource(ProductionWorkflowWorker.run_once)
    scan_source = inspect.getsource(
        ProductionWorkflowWorker._enqueue_due_cadence_evaluations
    )
    assert "_enqueue_due_cadence_evaluations()" in run_once_source
    assert 'LaunchRun.state == "ACTIVE"' in scan_source
    assert "with_for_update(skip_locked=True)" in scan_source
    assert "request_evaluation(" in scan_source

    human = authenticated_actor_context(
        canonical_user_id=uuid.uuid4(),
        operator_user_id=uuid.uuid4(),
        actor_role="PRODUCER",
        permissions={"production.start"},
    )
    with pytest.raises(
        ValidationFailureError,
        match="CADENCE_SYSTEM_WORKER_UNTRUSTED",
    ):
        LongFormCadenceService._authorize_worker_evaluation(actor=human)


def test_cadence_outbox_request_and_receipt_are_idempotent(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(name="Cadence Outbox")
    policy, actor, _ = _approved_launch_policy(db_session, scope)
    run = _active_launch_run(db_session, policy, actor, started_on=date(2026, 7, 20))
    service = LongFormCadenceService(db_session)
    request = CadenceEvaluationRequest()
    first = service.request_evaluation(launch_run_id=run.id, data=request, actor=actor)
    second = service.request_evaluation(launch_run_id=run.id, data=request, actor=actor)

    assert first.id == second.id
    assert first.command_id == second.command_id
    assert first.workflow_run_id is None
    assert first.attempt_count == 0
    assert (
        db_session.scalar(
            select(func.count(CadenceEvaluationReceipt.id)).where(
                CadenceEvaluationReceipt.launch_run_id == run.id
            )
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count(LongFormPublishSlot.id)).where(
                LongFormPublishSlot.launch_run_id == run.id
            )
        )
        == 0
    )
