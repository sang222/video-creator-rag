from __future__ import annotations

import copy
import uuid
from datetime import date
from types import SimpleNamespace

from app.contracts.geo_market import TargetMarketProfile
from app.contracts.launch_cadence import (
    FirstChannelLaunchPolicyCreate,
    LaunchPolicyApproval,
    LaunchRunCreate,
    LaunchRunTransition,
)
from app.contracts.m5 import (
    EditorialCalendarSlotCreate,
    EditorialIdeaCandidateCreate,
    EditorialResearchRunCreate,
    IdeaMarketPreflightCreate,
    SearchDemandEvidenceCreate,
)
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.vcos_v2 import AssignmentMode, ProductionLane
from app.core.actor import authenticated_actor_context
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.services.editorial_research import EditorialResearchService
from app.services.geo_market import TargetMarketDigestCompiler
from app.services.launch_cadence import FirstChannelLaunchPolicyService, LaunchRunService
from app.services.m5 import (
    EditorialCalendarService,
    IdeaMarketPreflightService,
    SearchDemandEvidenceService,
    _first_launch_experiment_authority,
)
from app.services.r3d1 import R3D1AdminService
from app.services.rbac import RBACService
from tests.qualification.conftest import QualificationFactory


def _admin_actor(session, scope):
    permissions = RBACService(session).permissions_for_user(
        user_id=scope.admin.id,
        company_id=scope.company.id,
    )
    return authenticated_actor_context(
        canonical_user_id=scope.admin.id,
        operator_user_id=scope.admin.id,
        actor_role="OWNER_ADMIN",
        permissions=permissions,
    )


def _operator_actor(session, scope):
    permissions = RBACService(session).permissions_for_user(
        user_id=scope.operator.id,
        company_id=scope.company.id,
    )
    return authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="PRODUCER",
        permissions=permissions,
    )


def _first_launch_case(session) -> SimpleNamespace:
    factory = QualificationFactory(session)
    scope = factory.channel_scope(name="First launch preflight", strict_long_form=True)
    admin = _admin_actor(session, scope)
    operator = _operator_actor(session, scope)
    evidence_refs = [{"type": "fixture", "ref": "qualification://first-launch"}]
    policies = FirstChannelLaunchPolicyService(session)
    policy = policies.create(
        data=FirstChannelLaunchPolicyCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            timezone="America/New_York",
            publish_weekdays=["TUESDAY"],
            publish_local_time="10:00",
            evidence_refs=evidence_refs,
        ),
        actor=admin,
    )
    policy = policies.approve(
        policy_version_id=policy.id,
        data=LaunchPolicyApproval(evidence_refs=evidence_refs),
        actor=admin,
    )
    runs = LaunchRunService(session)
    launch_run = runs.create(
        data=LaunchRunCreate(
            launch_policy_version_id=policy.id,
            launch_key=f"first-launch-{uuid.uuid4().hex}",
            preparation_started_on=date(2026, 8, 9),
        ),
        actor=admin,
    )
    runs.transition(
        launch_run_id=launch_run.id,
        data=LaunchRunTransition(
            target_state="READY_TO_LAUNCH", reason_codes=["FIXTURE_READY"]
        ),
        actor=admin,
    )
    launch_run = runs.transition(
        launch_run_id=launch_run.id,
        data=LaunchRunTransition(
            target_state="ACTIVE", reason_codes=["FIXTURE_ACTIVE"]
        ),
        actor=admin,
    )
    category = R3D1AdminService(session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"first-launch-{uuid.uuid4().hex[:8]}",
            name="First Launch Authority",
            sub_niche="small-team systems",
            audience_segment="small professional teams",
            content_pillar="AI automation workflows",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
        )
    )
    slot = EditorialCalendarService(session).create_slot(
        data=EditorialCalendarSlotCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id,
            slot_date=date(2026, 8, 9),
            slot_type="RESEARCH",
            schema_version="v2",
            production_lane=ProductionLane.LONG_FORM,
            assignment_mode=AssignmentMode.OPEN_MIX,
            production_goal="Explain one bounded AI workflow control decision",
            target_platforms=["YOUTUBE"],
            content_pillar="AI automation workflows",
            created_by_user_id=scope.operator.id,
        )
    )
    research = EditorialResearchService(session)
    research_run = research.create_run(
        data=EditorialResearchRunCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
            trigger_type="TEST",
            metadata={"provider_execution": "DISABLED"},
        ),
        actor=operator,
    )
    research.start_run(run_id=research_run.id, actor=operator)
    evidence = SearchDemandEvidenceService(session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            evidence_source_type="OFFICIAL_DOCUMENT",
            authority_purpose="CLAIM_SOURCE",
            source_ref="https://docs.example.test/ai/tool-selection",
            query="official tool selection guidance",
            platform="YOUTUBE",
            geo="US",
            evidence_confidence="HIGH",
            metadata={
                "editorial_fresh_evidence": {
                    "source_snapshot": {
                        "canonical_url": "https://docs.example.test/ai/tool-selection",
                        "content_hash": "a" * 64,
                        "title": "Tool-selection constraints",
                        "content_excerpt": "Use deterministic tool choice when a call must occur.",
                        "freshness_state": "FRESH",
                        "quality_decision": "PASS",
                        "source_class": "OFFICIAL_DOCUMENT",
                    },
                    "fetch_receipt": {
                        "status": "PASS",
                        "source_ref": "https://docs.example.test/ai/tool-selection",
                    },
                }
            },
        )
    )
    candidate = research.add_candidate(
        data=EditorialIdeaCandidateCreate(
            editorial_research_run_id=research_run.id,
            proposed_title="When Should an AI Workflow Call a Tool?",
            proposed_angle=(
                "Compare deterministic tool use, model-selected calls, and code "
                "execution as separate workflow control decisions."
            ),
            proposed_format="long-form explainer",
            proposed_pillar="AI automation workflows",
            rationale={
                "editorial_idea_proposal": {
                    "scope_exclusions": ["performance, ROI, or market claims"],
                    "evidence_bindings": [
                        {"quoted_text": "Do not make time-saving claims."}
                    ],
                    "viewer_value": "Give teams a safe tool-selection framework.",
                }
            },
            evidence_refs=[{"type": "search_demand_evidence", "id": str(evidence.id)}],
            confidence_level="HIGH",
            experiment_phase="AUDIENCE_PROMISE",
        ),
        actor=operator,
    )
    snapshot = session.get(CompiledChannelPolicySnapshot, scope.snapshot.id)
    assert snapshot is not None
    return SimpleNamespace(
        scope=scope,
        policy=policy,
        launch_run=launch_run,
        slot=slot,
        research_run=research_run,
        candidate=candidate,
        evidence=evidence,
        snapshot=snapshot,
        operator=operator,
    )


def _authority_result(session, case):
    scoped = case.snapshot.compiled_payload["channel_scoped_policy"]
    target_digest = TargetMarketDigestCompiler().compile(
        TargetMarketProfile.model_validate(scoped["target_market_profile"])
    )
    return _first_launch_experiment_authority(
        session,
        candidate=case.candidate,
        slot=case.slot,
        snapshot=case.snapshot,
        target_digest=target_digest,
        claim_evidence_refs=[{"id": str(case.evidence.id)}],
    )


def test_scoreless_first_launch_preflight_passes_with_server_lineage(db_session) -> None:
    case = _first_launch_case(db_session)

    preflight = IdeaMarketPreflightService(db_session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=case.scope.company.id,
            channel_workspace_id=case.scope.channel.id,
            editorial_calendar_slot_id=case.slot.id,
            editorial_research_run_id=case.research_run.id,
            editorial_idea_candidate_id=case.candidate.id,
            policy_fit_state="PASS",
            claim_evidence_refs=[{"id": str(case.evidence.id)}],
            market_demand_evidence_refs=[],
        )
    )

    assert preflight.decision == "PASS"
    assert "FIRST_LAUNCH_EXPERIMENT_AUTHORIZED" in preflight.reason_codes
    assert preflight.demand_score is None
    assert preflight.evidence_blob["demand_authority_type"] == "FIRST_LAUNCH_EXPERIMENT"
    assert preflight.evidence_blob["demand_state"] == "EXPERIMENT_AUTHORIZED"
    assert preflight.evidence_blob["market_demand_evidence_refs"] == []
    assert case.candidate.experiment_phase == "AUDIENCE_PROMISE"
    assert case.candidate.strategic_intent == "ACQUISITION"
    assert case.candidate.primary_variable_under_test == "audience_promise_validation"
    assert case.candidate.active_launch_policy_version_id == case.policy.id
    assert case.candidate.active_launch_run_id == case.launch_run.id
    assert _authority_result(db_session, case)["authorized"] is True


def test_first_launch_authority_rejects_each_invalid_binding(db_session) -> None:
    case = _first_launch_case(db_session)
    candidate = case.candidate

    originals = {
        "active_launch_policy_hash": candidate.active_launch_policy_hash,
        "active_launch_run_id": candidate.active_launch_run_id,
        "audience_promise": candidate.audience_promise,
        "target_audience_definition": copy.deepcopy(candidate.target_audience_definition),
        "proposed_angle": candidate.proposed_angle,
        "experiment_phase": candidate.experiment_phase,
        "strategic_intent": candidate.strategic_intent,
        "first_n_public_videos": case.policy.first_n_public_videos,
    }
    candidate.active_launch_policy_hash = "0" * 64
    assert _authority_result(db_session, case)["authorized"] is False
    candidate.active_launch_policy_hash = originals["active_launch_policy_hash"]

    candidate.active_launch_run_id = uuid.uuid4()
    assert _authority_result(db_session, case)["authorized"] is False
    candidate.active_launch_run_id = originals["active_launch_run_id"]

    candidate.audience_promise = "A different promise"
    assert _authority_result(db_session, case)["authorized"] is False
    candidate.audience_promise = originals["audience_promise"]

    candidate.target_audience_definition = copy.deepcopy(
        originals["target_audience_definition"]
    )
    candidate.target_audience_definition["market_locale"]["primary_market"] = "CA"
    assert _authority_result(db_session, case)["authorized"] is False
    candidate.target_audience_definition = originals["target_audience_definition"]

    case.policy.first_n_public_videos = 0
    assert _authority_result(db_session, case)["authorized"] is False
    case.policy.first_n_public_videos = originals["first_n_public_videos"]

    candidate.proposed_angle = "This tool has a guaranteed ROI."
    assert _authority_result(db_session, case)["authorized"] is False
    candidate.proposed_angle = originals["proposed_angle"]

    candidate.experiment_phase = "STEADY_STATE"
    candidate.strategic_intent = "AUTHORITY"
    assert _authority_result(db_session, case)["authorized"] is False
