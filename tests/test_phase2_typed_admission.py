from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, sessionmaker

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.vcos_v2 import (
    AssignmentCandidate,
    AssignmentMode,
    AssignmentReasonCode,
    AssignmentResolverInput,
    ContentMode,
    DurationContractV2,
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
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.foundation import Company, User
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    EditorialCalendarSlot,
    EditorialResearchRun,
    IdeaMarketPreflight,
)
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.workflow import VideoProject
from app.services.channel_profile import ChannelProfileService
from app.services.channel_workspace import ChannelWorkspaceService
from app.services.company import CompanyService
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.ofv0 import FormatIdentityContractService
from app.services.profile_compiler import ChannelProfileCompiler
from app.services.production_package import ChannelDurationContractResolver
from app.services.rbac import RBACService
from app.services.vcos_v2 import (
    DeterministicAssignmentResolver,
    LegacySeriesReader,
    LongFormPlanningService,
    ProjectAdmissionV2Service,
    SeriesPlanService,
    SeriesRunService,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Authority:
    company: Company
    channel: ChannelWorkspace
    profile: ChannelProfileVersion
    policy: CompiledChannelPolicySnapshot
    operator: User


def _authority(session: Session) -> _Authority:
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = CompanyService(session).create_company(
        name=f"Phase 2 {uuid.uuid4().hex[:8]}"
    )
    operator = User(
        email=f"phase2-{uuid.uuid4()}@example.com",
        display_name="Phase 2 Operator",
        status="active",
    )
    session.add(operator)
    session.flush()
    RBACService(session).assign_role(
        user_id=operator.id, role_key="operator", company_id=company.id
    )
    channel = ChannelWorkspaceService(session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            key="small-team-ai", name="Phase 2 Channel"
        ),
    )
    profile = ChannelProfileService(session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    format_contract = FormatIdentityContractService(session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile.id,
            created_by="phase2-trusted-fixture",
        )
    )
    FormatIdentityContractService(session).approve(
        format_contract.id,
        decided_by="phase2-trusted-fixture",
    )
    catalog = ConfigRegistryService(session).validate_catalog(
        ROOT / "config" / "channel_scoped_policy_catalog.yaml"
    )
    base_policy = ChannelScopedPolicy.model_validate(catalog.content["items"][0])
    scoped_policy = base_policy.model_copy(
        update={
            "policy_version": "phase2.trusted-fixture.v1",
            "format_identity_contract": base_policy.format_identity_contract.model_copy(
                update={"content_hash": format_contract.content_hash}
            ),
        }
    )
    profile_input = dict(profile.profile_input)
    profile_input["channel_policy"] = scoped_policy.model_dump(mode="json")
    profile.profile_input = profile_input
    profile.profile_input_hash = content_hash(profile_input)
    session.flush()
    compiled = ChannelProfileCompiler(session).compile(
        profile_version_id=profile.id,
        correlation_id="phase2-profile-compile",
    )
    profiles = ChannelProfileService(session)
    profiles.submit_for_approval(profile.id)
    profiles.approve_profile_version(
        profile_version_id=profile.id,
        approved_by=operator.id,
        approval_ref="operator-approval://phase2-trusted-fixture/v1",
    )
    policy = profiles.activate_snapshot(
        snapshot_id=compiled.snapshot_id
    )
    return _Authority(company, channel, profile, policy, operator)


def _slot(
    session: Session,
    authority: _Authority,
    *,
    lane: ProductionLane,
    assignment_mode: AssignmentMode,
    preferred_plan_id: uuid.UUID | None = None,
    preferred_run_id: uuid.UUID | None = None,
    legacy_series_key: str | None = None,
) -> EditorialCalendarSlot:
    slot = EditorialCalendarSlot(
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        policy_snapshot_id=authority.policy.id,
        slot_date=date(2026, 7, 28),
        slot_type="CAMPAIGN",
        status="OPEN",
        schema_version="v2",
        production_lane=lane,
        assignment_mode=assignment_mode,
        preferred_series_plan_id=preferred_plan_id,
        preferred_series_run_id=preferred_run_id,
        production_goal="Typed Phase 2 planning",
        target_platforms=["YOUTUBE"],
        series_key=legacy_series_key,
        risk_level="LOW",
        operational_envelope=(
            {
                "series_coherence_scores": {
                    str(preferred_run_id): 100,
                }
            }
            if preferred_run_id is not None
            else {}
        ),
        created_by_user_id=authority.operator.id,
    )
    session.add(slot)
    session.flush()
    return slot


def _preflight(
    session: Session,
    authority: _Authority,
    *,
    editorial_calendar_slot_id: uuid.UUID | None = None,
    passed: bool = True,
) -> IdeaMarketPreflight:
    research_run = EditorialResearchRun(
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        channel_profile_version_id=authority.profile.id,
        policy_snapshot_id=authority.policy.id,
        editorial_calendar_slot_id=editorial_calendar_slot_id,
        run_date=date(2026, 7, 28),
        status="COMPLETED",
        trigger_type="TEST",
        candidate_count=1,
        reason_codes=["EDITORIAL_RESEARCH_COMPLETED"],
        metadata_={"provider_execution": "DISABLED"},
        created_by_user_id=authority.operator.id,
    )
    session.add(research_run)
    session.flush()
    candidate = EditorialIdeaCandidate(
        editorial_research_run_id=research_run.id,
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        policy_snapshot_id=authority.policy.id,
        stage="GREENLIT",
        proposed_title="Typed long-form planning entry",
        proposed_angle="A deterministic editorial candidate.",
        proposed_format="long-form explainer",
        proposed_pillar="education",
        rationale={"authority": "phase2-test"},
        evidence_refs=[{"type": "offline_fixture", "ref": "phase2-editorial"}],
        reason_codes=["DETERMINISTIC_GREENLIGHT"],
        confidence_level="HIGH",
        budget_readiness="READY",
        rights_policy_state="PASS",
        quality_state="PASS",
        experiment_phase="AUDIENCE_PROMISE",
        canonical_hash=(uuid.uuid4().hex * 2)[:64],
        created_by_user_id=authority.operator.id,
    )
    session.add(candidate)
    session.flush()
    preflight = IdeaMarketPreflight(
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        editorial_calendar_slot_id=editorial_calendar_slot_id,
        editorial_research_run_id=research_run.id,
        editorial_idea_candidate_id=candidate.id,
        policy_fit_state="PASS" if passed else "BLOCK",
        confidence_state="HIGH",
        niche_contract_digest_ref=f"niche-contract://{authority.channel.id}",
        niche_contract_digest_hash="a" * 64,
        target_market_digest_ref=f"target-market://{authority.channel.id}/US",
        target_market_digest_hash="b" * 64,
        editorial_slot_ref=f"editorial-slot://{editorial_calendar_slot_id}",
        target_market="US",
        market_scope=["US"],
        evidence_blob={"authority": "phase2-test"},
        reason_codes=["SYSTEM_OK"] if passed else ["NICHE_GATE_NOT_PASS"],
        decision="PASS" if passed else "BLOCK",
    )
    session.add(preflight)
    session.flush()
    return preflight


def _duration(
    authority: _Authority,
    *,
    production_lane: ProductionLane = ProductionLane.LONG_FORM,
) -> DurationContractV2:
    session = object_session(authority.profile)
    assert session is not None
    return ChannelDurationContractResolver(session).resolve(
        profile_version_id=authority.profile.id,
        policy_snapshot_id=authority.policy.id,
        production_lane=production_lane,
    )


def _series(
    session: Session,
    authority: _Authority,
    *,
    lane: ProductionLane = ProductionLane.LONG_FORM,
    active: bool = True,
    capacity: int = 10,
    priority: int = 0,
) -> tuple[SeriesPlan, SeriesRun]:
    plan_service = SeriesPlanService(session)
    plan = plan_service.create(
        SeriesPlanCreate(
            company_id=authority.company.id,
            channel_workspace_id=authority.channel.id,
            channel_profile_version_id=authority.profile.id,
            policy_snapshot_id=authority.policy.id,
            stable_series_key=f"series-{uuid.uuid4().hex[:8]}",
            display_name="Typed Series",
            editorial_promise="One coherent promise per episode",
            allowed_production_lanes=[lane],
            episode_role_policy={"default_episode_role": "CORE"},
            created_by_user_id=authority.operator.id,
        )
    )
    plan_service.transition(
        plan.id,
        SeriesPlanTransitionRequest(
            target_state=SeriesPlanState.APPROVED,
            actor_user_id=authority.operator.id,
            reason_codes=["SERIES_PLAN_APPROVED"],
            evidence_refs=[{"type": "operator_approval", "ref": "phase2-test"}],
        ),
    )
    run_service = SeriesRunService(session)
    run = run_service.create(
        SeriesRunCreate(
            series_plan_id=plan.id,
            run_key=f"run-{uuid.uuid4().hex[:8]}",
            run_number=1,
            capacity=capacity,
            priority=priority,
            created_by_user_id=authority.operator.id,
        )
    )
    run_service.transition(
        run.id,
        SeriesRunTransitionRequest(
            target_state=SeriesRunState.APPROVED,
            actor_user_id=authority.operator.id,
            reason_codes=["SERIES_RUN_APPROVED"],
        ),
    )
    if active:
        run_service.transition(
            run.id,
            SeriesRunTransitionRequest(
                target_state=SeriesRunState.ACTIVE,
                actor_user_id=authority.operator.id,
                reason_codes=["SERIES_RUN_ACTIVE"],
            ),
        )
    return plan, run


def _long_request(
    authority: _Authority,
    slot: EditorialCalendarSlot,
    preflight: IdeaMarketPreflight,
    *,
    assignment_mode: AssignmentMode,
    preferred_plan_id: uuid.UUID | None = None,
    preferred_run_id: uuid.UUID | None = None,
) -> LongFormPlanningRequest:
    return LongFormPlanningRequest(
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        channel_profile_version_id=authority.profile.id,
        policy_snapshot_id=authority.policy.id,
        editorial_calendar_slot_id=slot.id,
        editorial_idea_candidate_id=preflight.editorial_idea_candidate_id,
        idea_market_preflight_id=preflight.id,
        assignment_mode=assignment_mode,
        preferred_series_plan_id=preferred_plan_id,
        preferred_series_run_id=preferred_run_id,
        title="Typed long-form planning entry",
        description="Typed long-form planning entry",
        niche_gate_passed=preflight.policy_fit_state == "PASS",
        market_gate_passed=preflight.decision == "PASS",
        evidence_refs=[{"type": "offline_fixture", "ref": "phase2-editorial"}],
        duration_contract=_duration(authority),
        created_by_user_id=authority.operator.id,
    )


def test_dedicated_long_form_entry_binds_editorial_research_candidate(
    db_session: Session,
) -> None:
    authority = _authority(db_session)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
    )
    preflight = _preflight(
        db_session,
        authority,
        editorial_calendar_slot_id=slot.id,
    )
    receipt = LongFormPlanningService(db_session).admit(
        _long_request(
            authority,
            slot,
            preflight,
            assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
        )
    )
    assert receipt.production_lane == ProductionLane.LONG_FORM
    assert receipt.editorial_research_run_id is not None
    assert receipt.editorial_idea_candidate_id == preflight.editorial_idea_candidate_id


def test_standalone_required_creates_true_standalone(
    db_session: Session,
) -> None:
    authority = _authority(db_session)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
    )
    receipt = LongFormPlanningService(db_session).admit(
        _long_request(
            authority,
            slot,
            _preflight(
                db_session,
                authority,
                editorial_calendar_slot_id=slot.id,
            ),
            assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
        )
    )
    project = db_session.get(VideoProject, receipt.admitted_video_project_id)
    assert project.content_mode == ContentMode.STANDALONE
    assert project.series_plan_id is None
    assert project.series_run_id is None
    assert project.episode_number is None
    assert (
        project.standalone_reason_code
        == AssignmentReasonCode.EXPLICIT_STANDALONE_REQUIRED
    )


def test_series_required_reserves_exact_episode(db_session: Session) -> None:
    authority = _authority(db_session)
    plan, run = _series(db_session, authority)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.SERIES_REQUIRED,
        preferred_plan_id=plan.id,
        preferred_run_id=run.id,
    )
    receipt = LongFormPlanningService(db_session).admit(
        _long_request(
            authority,
            slot,
            _preflight(
                db_session,
                authority,
                editorial_calendar_slot_id=slot.id,
            ),
            assignment_mode=AssignmentMode.SERIES_REQUIRED,
            preferred_plan_id=plan.id,
            preferred_run_id=run.id,
        )
    )
    assert receipt.decision == "ADMIT"
    assert receipt.content_mode == ContentMode.SERIES_EPISODE
    assert receipt.series_run_id == run.id
    assert receipt.episode_number == 1
    assert run.next_episode_number == 2
    assert run.reserved_episode_count == 1
    assert run.published_episode_count == 0


def test_series_required_blocks_inactive_run(db_session: Session) -> None:
    authority = _authority(db_session)
    plan, run = _series(db_session, authority, active=False)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.SERIES_REQUIRED,
        preferred_plan_id=plan.id,
        preferred_run_id=run.id,
    )
    receipt = LongFormPlanningService(db_session).admit(
        _long_request(
            authority,
            slot,
            _preflight(
                db_session,
                authority,
                editorial_calendar_slot_id=slot.id,
            ),
            assignment_mode=AssignmentMode.SERIES_REQUIRED,
            preferred_plan_id=plan.id,
            preferred_run_id=run.id,
        )
    )
    assert receipt.decision == "BLOCK"
    assert AssignmentReasonCode.SERIES_RUN_NOT_ACTIVE in receipt.reason_codes
    assert receipt.admitted_video_project_id is None


def test_series_preferred_falls_back_to_standalone(
    db_session: Session,
) -> None:
    authority = _authority(db_session)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.SERIES_PREFERRED,
    )
    receipt = LongFormPlanningService(db_session).admit(
        _long_request(
            authority,
            slot,
            _preflight(
                db_session,
                authority,
                editorial_calendar_slot_id=slot.id,
            ),
            assignment_mode=AssignmentMode.SERIES_PREFERRED,
        )
    )
    assert receipt.decision == "ADMIT"
    assert receipt.content_mode == ContentMode.STANDALONE
    assert receipt.standalone_reason_code == AssignmentReasonCode.NO_ELIGIBLE_SERIES


def test_open_mix_is_order_independent_with_stable_tie_break() -> None:
    low_id = uuid.UUID(int=1)
    high_id = uuid.UUID(int=2)

    def candidate(run_id: uuid.UUID) -> AssignmentCandidate:
        return AssignmentCandidate(
            series_plan_id=uuid.UUID(int=100 + run_id.int),
            series_run_id=run_id,
            production_lane=ProductionLane.LONG_FORM,
            plan_state=SeriesPlanState.APPROVED,
            run_state=SeriesRunState.ACTIVE,
            next_episode_number=1,
            capacity=10,
            reserved_episode_count=0,
            coherence_score=100,
        )

    resolver = DeterministicAssignmentResolver()
    first = resolver.resolve(
        AssignmentResolverInput(
            production_lane=ProductionLane.LONG_FORM,
            assignment_mode=AssignmentMode.OPEN_MIX,
            candidates=[candidate(high_id), candidate(low_id)],
            niche_gate_passed=True,
            market_gate_passed=True,
        )
    )
    second = resolver.resolve(
        AssignmentResolverInput(
            production_lane=ProductionLane.LONG_FORM,
            assignment_mode=AssignmentMode.OPEN_MIX,
            candidates=[candidate(low_id), candidate(high_id)],
            niche_gate_passed=True,
            market_gate_passed=True,
        )
    )
    assert first.series_run_id == low_id
    assert second.series_run_id == low_id
    assert first.resolver_input_hash == second.resolver_input_hash


def test_niche_market_gate_blocks_standalone(db_session: Session) -> None:
    authority = _authority(db_session)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
    )
    receipt = LongFormPlanningService(db_session).admit(
        _long_request(
            authority,
            slot,
            _preflight(
                db_session,
                authority,
                editorial_calendar_slot_id=slot.id,
                passed=False,
            ),
            assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
        )
    )
    assert receipt.decision == "BLOCK"
    assert "NICHE_GATE_NOT_PASS" in receipt.reason_codes
    assert receipt.admitted_video_project_id is None


def test_concurrent_admissions_reserve_distinct_episodes(
    db_session: Session, engine
) -> None:
    authority = _authority(db_session)
    plan, run = _series(db_session, authority, capacity=2)
    requests: list[LongFormPlanningRequest] = []
    for _ in range(2):
        slot = _slot(
            db_session,
            authority,
            lane=ProductionLane.LONG_FORM,
            assignment_mode=AssignmentMode.SERIES_REQUIRED,
            preferred_plan_id=plan.id,
            preferred_run_id=run.id,
        )
        requests.append(
            _long_request(
                authority,
                slot,
                _preflight(
                    db_session,
                    authority,
                    editorial_calendar_slot_id=slot.id,
                ),
                assignment_mode=AssignmentMode.SERIES_REQUIRED,
                preferred_plan_id=plan.id,
                preferred_run_id=run.id,
            )
        )
    db_session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def admit(request: LongFormPlanningRequest) -> int:
        with factory() as worker:
            receipt = LongFormPlanningService(worker).admit(request)
            episode = receipt.episode_number
            worker.commit()
            assert episode is not None
            return episode

    with ThreadPoolExecutor(max_workers=2) as pool:
        episodes = list(pool.map(admit, requests))
    assert sorted(episodes) == [1, 2]
    db_session.expire_all()
    refreshed = db_session.get(SeriesRun, run.id)
    assert refreshed.reserved_episode_count == 2
    assert refreshed.published_episode_count == 0


def test_transaction_failure_releases_episode_reservation(
    db_session: Session,
) -> None:
    authority = _authority(db_session)
    plan, run = _series(db_session, authority)
    slot = _slot(
        db_session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.SERIES_REQUIRED,
        preferred_plan_id=plan.id,
        preferred_run_id=run.id,
    )
    request = _long_request(
        authority,
        slot,
        _preflight(
            db_session,
            authority,
            editorial_calendar_slot_id=slot.id,
        ),
        assignment_mode=AssignmentMode.SERIES_REQUIRED,
        preferred_plan_id=plan.id,
        preferred_run_id=run.id,
    )

    def fail_after_reservation(stage: str) -> None:
        assert stage == "after_episode_reservation"
        raise RuntimeError("injected post-reservation failure")

    service = ProjectAdmissionV2Service(db_session, fault_hook=fail_after_reservation)
    with pytest.raises(RuntimeError, match="injected"):
        service.create_decision(
            data=ProjectAdmissionV2Request(
                planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
                company_id=request.company_id,
                channel_workspace_id=request.channel_workspace_id,
                channel_profile_version_id=request.channel_profile_version_id,
                policy_snapshot_id=request.policy_snapshot_id,
                editorial_calendar_slot_id=request.editorial_calendar_slot_id,
                editorial_idea_candidate_id=request.editorial_idea_candidate_id,
                idea_market_preflight_id=request.idea_market_preflight_id,
                production_lane=ProductionLane.LONG_FORM,
                assignment_mode=request.assignment_mode,
                preferred_series_plan_id=request.preferred_series_plan_id,
                preferred_series_run_id=request.preferred_series_run_id,
                title=request.title,
                niche_gate_passed=request.niche_gate_passed,
                market_gate_passed=request.market_gate_passed,
                evidence_refs=request.evidence_refs,
                duration_contract=request.duration_contract,
                created_by_user_id=request.created_by_user_id,
            )
        )
    db_session.expire_all()
    refreshed = db_session.get(SeriesRun, run.id)
    assert refreshed.next_episode_number == 1
    assert refreshed.reserved_episode_count == 0
    assert db_session.scalar(select(VideoProject.id)) is None


def test_legacy_series_dual_reader_classification() -> None:
    assert (
        LegacySeriesReader.classify_values(
            schema_version="v1",
            series_plan_id=None,
            series_run_id=None,
            legacy_series_key=None,
        )
        == LegacySeriesClassification.UNRESOLVED_LEGACY
    )
    assert (
        LegacySeriesReader.classify_values(
            schema_version="v1",
            series_plan_id=None,
            series_run_id=None,
            legacy_series_key="legacy-series",
        )
        == LegacySeriesClassification.LEGACY_SERIES_BOUND
    )


def test_v2_raw_series_key_only_is_rejected() -> None:
    profile_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    digest = DurationContractV2.calculate_hash(
        minimum_duration_ms=60_000,
        target_duration_ms=600_000,
        maximum_duration_ms=1_200_000,
        duration_contract_version="test-v2",
        source_profile_version_id=profile_id,
        source_policy_snapshot_id=policy_id,
    )
    with pytest.raises(ValidationError, match="raw_series_key"):
        ProjectAdmissionV2Request(
            planning_source_type=PlanningSourceType.LONG_FORM_PLAN,
            company_id=uuid.uuid4(),
            channel_workspace_id=uuid.uuid4(),
            channel_profile_version_id=profile_id,
            policy_snapshot_id=policy_id,
            editorial_calendar_slot_id=uuid.uuid4(),
            production_lane=ProductionLane.LONG_FORM,
            assignment_mode=AssignmentMode.SERIES_REQUIRED,
            raw_series_key="not-authority",
            title="Blocked raw series",
            duration_contract=DurationContractV2(
                minimum_duration_ms=60_000,
                target_duration_ms=600_000,
                maximum_duration_ms=1_200_000,
                duration_contract_version="test-v2",
                duration_contract_hash=digest,
                source_profile_version_id=profile_id,
                source_policy_snapshot_id=policy_id,
            ),
            created_by_user_id=uuid.uuid4(),
        )
