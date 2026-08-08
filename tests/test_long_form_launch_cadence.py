from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.contracts.geo_market import DestinationBinding
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
from app.contracts.production_workflow import ProductionWorkflowProjectStart
from app.contracts.production_package import (
    ProductionPackageContentV2,
    ProductionReadinessReceiptContentV2,
)
from app.contracts.vcos_v2 import (
    AssignmentResolverInput,
    AssignmentMode,
    ProductionLane,
    SeriesPlanCreate,
    SeriesPlanState,
    SeriesPlanTransitionRequest,
    SeriesRunCreate,
    SeriesRunState,
    SeriesRunTransitionRequest,
)
from app.contracts.ops import ProviderRegistryEntryCreate
from app.core.actor import _system_worker_actor, authenticated_actor_context
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    HumanUploadTask,
    MediaRenderJob,
    VideoProject,
)
from app.db.models.foundation import DomainEvent
from app.db.models.script_qualification import (
    SeriesEpisodeReservation,
    ScriptQualificationReceipt,
    ScriptQualificationRun,
)
from app.db.models.launch_cadence import (
    CadenceEvaluationReceipt,
    FirstChannelLaunchPolicyVersion,
    LaunchRun,
    LongFormPublishSlot,
)
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    EditorialResearchRun,
    ProjectAdmissionDecision,
    SearchDemandEvidence,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.production_workflow import WorkflowRecoveryReceipt
from app.db.models.vcos_v2 import SeriesRun
from app.db.models.ops import DeadLetterJob, OpsIncident, ProviderAttempt
from app.db.models.workflow import Artifact, ArtifactVersion
from app.services.editorial_research import EditorialResearchService
from app.services.editorial_runway_replenishment import (
    EditorialRunwayReplenishmentService,
)
from app.services.editorial_fresh_evidence import (
    FreshEvidenceCollector,
    FreshEvidenceSource,
)
from app.services.launch_cadence import (
    FirstChannelLaunchPolicyService,
    LaunchRunService,
    LaunchRunwayService,
    LongFormCadenceService,
)
from app.services.m5 import (
    EditorialCalendarService,
    IdeaMarketPreflightService,
    SearchDemandEvidenceService,
)
from app.services.production_workflow import ProductionWorkflowCoordinator
from app.services.stale_workflow_recovery import (
    STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
    StaleWorkflowRecoveryService,
)
from app.services.r3d1 import R3D1AdminService
from app.services.ops import ProviderRegistryService
from app.services.rbac import RBACService
from app.services.script_qualification import (
    ScriptQualificationService,
    TopicDefinitionService,
    span_hash,
)
from app.services.script_qualification_recovery import (
    ScriptQualificationRecoveryService,
)
from app.services.v2_support_authority import (
    V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
    V2FrozenSupportEnvelope,
)
from app.services.vcos_v2 import (
    AssignmentResolutionError,
    DeterministicAssignmentResolver,
    SeriesPlanService,
    SeriesRunService,
)
from app.services.config_registry import content_hash
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


class _DeterministicPassingQualificationProducer:
    """Two-call writer/verifier double for the real qualification service."""

    def __init__(self) -> None:
        self.writer_calls = 0
        self.verifier_calls = 0
        self.sentences: list[str] = []
        self.section_ids: list[str] = []
        self.writer_assignment_resolution: dict | None = None
        self.verifier_assignment_resolution: dict | None = None

    @staticmethod
    def _receipt(idempotency_key: str, lane_name: str) -> dict[str, str]:
        return {
            "status": "SUCCESS",
            "idempotency_key": idempotency_key,
            "lane_name": lane_name,
            "selected_model": "gpt-5.6-luna",
            "fallback_level": "PRIMARY",
            "route_attempt_id": str(uuid.uuid4()),
            "provider_attempt_id": str(uuid.uuid4()),
            "llm_run_snapshot_id": str(uuid.uuid4()),
        }

    def write(self, context, *, idempotency_key):
        self.writer_calls += 1
        self.writer_assignment_resolution = dict(context["assignment_resolution"])
        evidence_id = context["factual_evidence_pack"]["spans"][0]["evidence_span_id"]
        requirements = [
            item["requirement_id"]
            for item in context["script_assignment"]["required_requirement_units"]
        ]
        self.sentences = [
            f"The documented workflow fulfills {requirement} with exact source-bound evidence "
            + " ".join(f"detail{index}_{word}" for word in range(1, 151))
            + "."
            for index, requirement in enumerate(requirements, start=1)
        ]
        self.section_ids = [
            "hook" if index < 3 else "body" if index < 6 else "close"
            for index in range(len(self.sentences))
        ]
        return {
            "canonical_script": " ".join(self.sentences),
            "language": "en",
            "sections": [
                {"section_id": "hook", "heading": "Hook", "narration": " ".join(self.sentences[:3])},
                {"section_id": "body", "heading": "Body", "narration": " ".join(self.sentences[3:6])},
                {"section_id": "close", "heading": "Close", "narration": " ".join(self.sentences[6:])},
            ],
            "claims": [
                {"claim_id": f"writer-{index}", "claim_text": text, "evidence_span_ids": [evidence_id]}
                for index, text in enumerate(self.sentences, start=1)
            ],
        }, self._receipt(idempotency_key, "long_context_text")

    def verify(self, context, *, idempotency_key):
        self.verifier_calls += 1
        self.verifier_assignment_resolution = dict(context["assignment_resolution"])
        script = context["canonical_script"]
        spans = [
            {
                "text": text,
                "section_id": section_id,
            }
            for text, section_id in zip(self.sentences, self.section_ids, strict=True)
        ]
        evidence_id = context["factual_evidence_pack"]["spans"][0]["evidence_span_id"]
        requirements = [item["requirement_id"] for item in context["script_assignment"]["required_requirement_units"]]
        return {
            "material_claim_inventory": [
                {"observed_claim_id": f"observed-{index}", "span": span, "claim_type": "FACTUAL_ASSERTION", "materiality_state": "MATERIAL", "writer_declared_claim_id": f"writer-{index}", "factual_evidence_span_ids": [evidence_id], "semantic_relation": "ENTAILED", "assignment_requirement_ids": [requirements[index - 1]]}
                for index, span in enumerate(spans, start=1)
            ],
            "assignment_fulfillment_observations": [
                {"requirement_id": requirement, "status": "SUFFICIENT", "spans": [spans[index]], "evidence_span_ids": [evidence_id]}
                for index, requirement in enumerate(requirements)
            ],
            "section_purpose_observations": [
                {"section_id": "hook", "observed_primary_role": "HOOK", "fulfilled_requirement_ids": requirements[:3], "editorial_delta": "Defines the bounded documented subject.", "genericity_state": "SPECIFIC"},
                {"section_id": "body", "observed_primary_role": "MECHANISM", "fulfilled_requirement_ids": requirements[3:6], "editorial_delta": "Connects evidence to the operational choice.", "genericity_state": "SPECIFIC"},
                {"section_id": "close", "observed_primary_role": "CLOSING_INSIGHT", "fulfilled_requirement_ids": requirements[6:], "editorial_delta": "Converts the scope into a next verification step.", "genericity_state": "SPECIFIC"},
            ],
            "forbidden_scope_observations": [
                {"forbidden_scope_id": item["forbidden_scope_id"], "state": "ABSENT"}
                for item in context["script_assignment"]["forbidden_scope_units"]
            ],
        }, self._receipt(idempotency_key, "gatekeeper_soft_review")


def _configure_verified_destination(session, scope) -> None:
    """Install the immutable destination authority required by real support sealing."""

    binding = DestinationBinding(
        binding_version=1,
        channel_id=scope.channel.id,
        channel_key=scope.channel.key,
        platform="YOUTUBE",
        platform_account_ref="youtube-account://qualification-closeout",
        platform_channel_id="UC_QUALIFICATION_CLOSEOUT",
        channel_handle="@qualification-closeout",
        target_market_profile_ref="target-market-profile://qualification/v1",
        target_market_profile_hash="d" * 64,
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="VERIFIED",
        credential_ref="credential://qualification-closeout/destination",
        verification_state="VERIFIED",
        verification_timestamp="2026-08-03T00:00:00+00:00",
        approval_ref="operator-approval://qualification-closeout/destination",
    ).model_dump(mode="json")
    scope.channel.metadata_ = {
        **(scope.channel.metadata_ or {}),
        "destination_governance": {
            "active_binding_ref": (
                f"destination-binding://{scope.channel.key}/v1"
            ),
            "bindings": [binding],
        },
    }
    session.flush()


def _support_envelope_for_project(session, project_id: uuid.UUID):
    artifact = session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == project_id,
            Artifact.artifact_type == V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
        )
    )
    assert artifact is not None
    version = session.get(ArtifactVersion, artifact.current_version_id)
    assert version is not None
    return artifact, version, V2FrozenSupportEnvelope.model_validate(version.content)


def _qualification_gate(envelope: V2FrozenSupportEnvelope) -> dict:
    gate = next(
        (
            item
            for item in envelope.gate_receipts
            if item.get("gate_key") == "script_qualification"
        ),
        None,
    )
    assert gate is not None
    return gate


def _assert_no_pre_readiness_provider_effects(
    session, *, workflow_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Routes and reservations are allowed; provider/render effects are not."""

    assert (
        session.scalar(
            select(func.count(ProviderAttempt.id)).where(
                ProviderAttempt.target_id.in_([workflow_id, project_id])
            )
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count(MediaRenderJob.id)).where(
                MediaRenderJob.video_project_id == project_id
            )
        )
        == 0
    )


def _run_real_worker_to_readiness(engine, workflow_id: uuid.UUID) -> None:
    """Run only the normal worker composition through the readiness boundary."""

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id=f"script-qualification-closeout-{workflow_id}",
    )
    for index in range(10):
        result = worker.run_once()
        if result.status != "DELIVERED":
            with factory() as failed:
                event = failed.get(DomainEvent, result.event_id)
                workflow = failed.get(ProductionWorkflowRun, workflow_id)
                diagnostic = (
                    event.last_error_code if event is not None else None,
                    event.last_error_summary if event is not None else None,
                    workflow.current_stage if workflow is not None else None,
                    workflow.state_reason_codes if workflow is not None else None,
                )
            raise AssertionError((index, result, diagnostic))
        with factory() as check:
            workflow = check.get(ProductionWorkflowRun, workflow_id)
            assert workflow is not None
            if workflow.state == "READY_FOR_PRODUCTION":
                assert workflow.current_stage == "MEDIA"
                return
    raise AssertionError("workflow did not reach READY_FOR_PRODUCTION")


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
    include_initial_series: bool = True,
):
    actor = _actor(session, scope, admin=True)
    plans = _approved_series_plans(session, scope) if include_initial_series else []
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


def test_runway_replenishment_is_single_durable_block_without_fresh_evidence(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Runway Replenishment", strict_long_form=True
    )
    policy, actor, _plans = _approved_launch_policy(
        db_session,
        scope,
        include_initial_series=False,
    )
    R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key="runway-replenishment",
            name="Runway Replenishment",
            sub_niche="small-team systems",
            audience_segment="small professional teams",
            content_pillar="AI automation workflows",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
        )
    )
    _active_launch_run(
        db_session,
        policy,
        actor,
        started_on=date(2026, 8, 3),
    )
    worker_actor = _system_worker_actor(
        "vcos-durable-worker",
        permissions={"editorial.manage", "production.start"},
    )
    def now() -> datetime:
        return datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
    service = EditorialRunwayReplenishmentService(db_session, now=now)

    first = service.reconcile_active_launches(actor=worker_actor)
    second = service.reconcile_active_launches(actor=worker_actor)

    assert len(first) == 1
    assert first[0].status == "BLOCKED"
    assert "SOURCE_PROVIDER_MISSING" in first[0].reason_codes
    assert "RUNWAY_REPLENISHMENT_SERIES_AUTHORITY_UNAVAILABLE" not in first[0].reason_codes
    assert len(second) == 1
    assert second[0].status == "COOLDOWN"
    assert second[0].editorial_research_run_id == first[0].editorial_research_run_id
    research_runs = list(
        db_session.scalars(
            select(EditorialResearchRun).where(
                EditorialResearchRun.channel_workspace_id == scope.channel.id
            )
        ).all()
    )
    assert len(research_runs) == 1
    research = research_runs[0]
    assert research.status == "BLOCKED"
    assert research.candidate_count == 0
    mode = research.metadata_["runway_replenishment"]["mode_decision"]
    assert mode["content_mode"] == "STANDALONE"
    assert mode["standalone_authority"]["launch_initial_series_count"] == 0
    # Context may be unavailable for a deliberately incomplete fixture; the
    # durable block and absence of downstream production effects are invariant.
    assert db_session.scalar(
        select(func.count(VideoProject.id)).where(
            VideoProject.channel_workspace_id == scope.channel.id
        )
    ) == 0


def test_series_required_still_fails_closed_without_typed_binding() -> None:
    resolver = DeterministicAssignmentResolver()

    with pytest.raises(AssignmentResolutionError, match="SERIES_BINDING_INVALID"):
        resolver.resolve(
            AssignmentResolverInput(
                production_lane=ProductionLane.LONG_FORM,
                assignment_mode=AssignmentMode.SERIES_REQUIRED,
                niche_gate_passed=True,
                market_gate_passed=True,
            )
        )


def test_open_mix_runway_naturally_resolves_active_series_intent(
    db_session, qualification_factory
) -> None:
    """The production runway feeds typed active series to OPEN_MIX."""

    scope = qualification_factory.channel_scope(
        name="Natural series runway", strict_long_form=True
    )
    policy, actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key="natural-series-runway",
            name="Natural Series Runway",
            sub_niche="small-team systems",
            audience_segment="small professional teams",
            content_pillar="AI automation workflows",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
        )
    )
    launch = _active_launch_run(
        db_session, policy, actor, started_on=date(2026, 8, 3)
    )
    service = EditorialRunwayReplenishmentService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 14, tzinfo=timezone.utc),
    )
    slot, blocker = service._create_research_slot(
        run=launch, policy=policy, run_date=date(2026, 8, 3)
    )
    assert blocker is None and slot is not None
    mode = service._resolve_mode(
        run=launch, policy=policy, editorial_calendar_slot=slot
    )
    assert mode.content_mode == "SERIES_EPISODE"
    assert mode.series_binding is not None
    assert mode.series_binding["series_plan_id"] == str(plans[0].id)
    assert mode.series_binding["series_run_id"] == str(series_run.id)
    assert "episode_number" not in mode.series_binding


class _DeterministicOfficialDocsProvider:
    def __init__(self, source: FreshEvidenceSource, *, provider_key: str = "official-docs-test"):
        self.source = source
        self.provider_key = provider_key
        self.calls = 0

    def collect(self, *, research_question, maximum_sources, timeout_seconds):
        del research_question, timeout_seconds
        self.calls += 1
        return [self.source][:maximum_sources]


def test_fresh_evidence_collector_persists_hash_bound_source_snapshot(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Fresh editorial evidence", strict_long_form=True
    )
    policy = {
        "policy_snapshot_id": str(scope.snapshot.id),
        "policy_snapshot_hash": scope.snapshot.content_hash,
        "network_access_allowed": True,
        "allowed_source_classes": ["OFFICIAL_DOCUMENTATION"],
        "allowed_domains": ["docs.example.test"],
        "maximum_sources_per_run": 2,
        "timeout_seconds": 10,
        "freshness_days": 14,
    }
    ProviderRegistryService(db_session).create_entry(
        data=ProviderRegistryEntryCreate(
            provider_key="official-docs-test",
            provider_name="Deterministic official docs",
            provider_type="OTHER",
            capability_blob={"editorial_evidence_collection": True},
            policy_fit_blob={"editorial_evidence_authority": policy},
        )
    )
    now = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
    collector = FreshEvidenceCollector(db_session, now=lambda: now)
    authority = collector.inspect_authority(
        policy_snapshot_id=str(scope.snapshot.id),
        policy_snapshot_hash=scope.snapshot.content_hash,
    )
    assert authority.ready, authority
    provider = _DeterministicOfficialDocsProvider(
        FreshEvidenceSource(
            source_ref="https://docs.example.test/automation/approval-workflows",
            title="Approval workflow guide",
            publisher="Example Docs",
            source_class="OFFICIAL_DOCUMENTATION",
            retrieved_content="A bounded source extract describing approval workflow setup.",
            retrieved_at=now,
            query="small team approval workflow automation",
        )
    )
    result = collector.collect(
        authority=authority,
        provider=provider,
        company_id=str(scope.company.id),
        channel_workspace_id=str(scope.channel.id),
        editorial_research_run_id=str(uuid.uuid4()),
        context_pack_snapshot_id=str(uuid.uuid4()),
        research_question="Find a current official workflow source within the approved pillar.",
    )

    assert result.ok
    assert provider.calls == 1
    assert result.receipt is not None
    assert result.receipt["source_pack"]["content_hash"]
    assert result.receipt["research_pack"]["content_hash"]
    evidence_id = uuid.UUID(result.evidence_ids[0])
    evidence = db_session.get(SearchDemandEvidence, evidence_id)
    assert evidence is not None
    assert evidence.evidence_source_type == "OFFICIAL_DOCUMENT"
    snapshot = evidence.metadata_["editorial_fresh_evidence"]["source_snapshot"]
    assert snapshot["freshness_state"] == "FRESH"
    assert snapshot["quality_decision"] == "PASS"
    assert snapshot["content_hash"] == content_hash(
        {
            "source_ref": provider.source.source_ref,
            "title": provider.source.title,
            "publisher": provider.source.publisher,
            "source_class": provider.source.source_class,
            "retrieved_content": provider.source.retrieved_content,
            "retrieved_at": provider.source.retrieved_at.isoformat(),
        }
    )


def test_fresh_evidence_collector_rejects_stale_snapshot(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Rejected editorial evidence", strict_long_form=True
    )
    now = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
    policy = {
        "policy_snapshot_id": str(scope.snapshot.id),
        "policy_snapshot_hash": scope.snapshot.content_hash,
        "network_access_allowed": True,
        "allowed_source_classes": ["OFFICIAL_DOCUMENTATION"],
        "allowed_domains": ["docs.example.test"],
        "maximum_sources_per_run": 1,
        "timeout_seconds": 10,
        "freshness_days": 7,
    }
    ProviderRegistryService(db_session).create_entry(
        data=ProviderRegistryEntryCreate(
            provider_key="official-docs-stale-test",
            provider_name="Rejected official docs",
            provider_type="OTHER",
            capability_blob={"editorial_evidence_collection": True},
            policy_fit_blob={"editorial_evidence_authority": policy},
        )
    )
    collector = FreshEvidenceCollector(db_session, now=lambda: now)
    authority = collector.inspect_authority(
        policy_snapshot_id=str(scope.snapshot.id),
        policy_snapshot_hash=scope.snapshot.content_hash,
    )
    assert authority.ready, authority
    provider = _DeterministicOfficialDocsProvider(
        FreshEvidenceSource(
            source_ref="https://docs.example.test/obsolete",
            title="Obsolete guide",
            publisher="Example Docs",
            source_class="OFFICIAL_DOCUMENTATION",
            retrieved_content="A source extract that must not be admitted.",
            retrieved_at=now - timedelta(days=30),
            query="obsolete workflow guide",
        ),
        provider_key="official-docs-stale-test",
    )
    result = collector.collect(
        authority=authority,
        provider=provider,
        company_id=str(scope.company.id),
        channel_workspace_id=str(scope.channel.id),
        editorial_research_run_id=str(uuid.uuid4()),
        context_pack_snapshot_id=str(uuid.uuid4()),
        research_question="A question that must not admit stale content.",
    )

    assert not result.ok
    assert "SOURCE_FRESHNESS_FAILED" in result.authority.reason_codes
    assert db_session.scalar(
        select(func.count(SearchDemandEvidence.id)).where(
            SearchDemandEvidence.channel_workspace_id == scope.channel.id
        )
    ) == 0


def test_fresh_evidence_collector_rejects_non_https_snapshot(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Invalid URL editorial evidence", strict_long_form=True
    )
    now = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
    policy = {
        "policy_snapshot_id": str(scope.snapshot.id),
        "policy_snapshot_hash": scope.snapshot.content_hash,
        "network_access_allowed": True,
        "allowed_source_classes": ["OFFICIAL_DOCUMENTATION"],
        "allowed_domains": ["docs.example.test"],
        "maximum_sources_per_run": 1,
        "timeout_seconds": 10,
        "freshness_days": 7,
    }
    ProviderRegistryService(db_session).create_entry(
        data=ProviderRegistryEntryCreate(
            provider_key="official-docs-invalid-url-test",
            provider_name="Invalid URL official docs",
            provider_type="OTHER",
            capability_blob={"editorial_evidence_collection": True},
            policy_fit_blob={"editorial_evidence_authority": policy},
        )
    )
    collector = FreshEvidenceCollector(db_session, now=lambda: now)
    authority = collector.inspect_authority(
        policy_snapshot_id=str(scope.snapshot.id),
        policy_snapshot_hash=scope.snapshot.content_hash,
    )
    assert authority.ready, authority
    provider = _DeterministicOfficialDocsProvider(
        FreshEvidenceSource(
            source_ref="http://docs.example.test/no-tls",
            title="Invalid source",
            publisher="Example Docs",
            source_class="OFFICIAL_DOCUMENTATION",
            retrieved_content="A source extract that must not be admitted.",
            retrieved_at=now,
            query="invalid source reference",
        ),
        provider_key="official-docs-invalid-url-test",
    )
    result = collector.collect(
        authority=authority,
        provider=provider,
        company_id=str(scope.company.id),
        channel_workspace_id=str(scope.channel.id),
        editorial_research_run_id=str(uuid.uuid4()),
        context_pack_snapshot_id=str(uuid.uuid4()),
        research_question="A question that must not admit an insecure URL.",
    )

    assert not result.ok
    assert "SOURCE_SNAPSHOT_INVALID" in result.authority.reason_codes


def _greenlit_candidate(session, scope, actor, *, topic_variant: str | None = None):
    suffix = f" {topic_variant}" if topic_variant else ""
    source_slug = f"audit-{topic_variant.lower().replace(' ', '-')}" if topic_variant else "audit"
    source_url = f"https://docs.example.test/automation/{source_slug}"
    source_title = f"How a Small Team Audits One Automation{suffix}"
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
    claim_evidence = SearchDemandEvidenceService(session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            evidence_source_type="OFFICIAL_DOCUMENT",
            authority_purpose="CLAIM_SOURCE",
            source_ref=source_url,
            query="small team automation audit",
            platform="YOUTUBE",
            geo="US",
            evidence_confidence="HIGH",
            metadata={
                "editorial_fresh_evidence": {
                    "source_snapshot": {
                        "canonical_url": source_url,
                        "content_hash": "c" * 64,
                        "title": source_title,
                        "content_excerpt": (
                            "The official automation-audit document defines a bounded "
                            "audit workflow for a small team."
                        ),
                        "freshness_state": "FRESH",
                        "quality_decision": "PASS",
                        "source_class": "OFFICIAL_DOCUMENT",
                    },
                    "fetch_receipt": {
                        "status": "PASS",
                        "source_ref": source_url,
                    },
                }
            },
        )
    )
    evidence = SearchDemandEvidenceService(session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            evidence_source_type="GOOGLE_TRENDS_CSV",
            authority_purpose="MARKET_DEMAND",
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
            proposed_title=source_title,
            proposed_angle="A bounded evidence-led operating walkthrough.",
            proposed_format="long-form explainer",
            proposed_pillar="AI automation workflows",
            evidence_refs=[{"type": "search_demand_evidence", "id": str(claim_evidence.id)}],
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
            claim_evidence_refs=[{"id": str(claim_evidence.id)}],
            market_demand_evidence_refs=[{"id": str(evidence.id)}],
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
            "id": str(claim_evidence.id),
            "evidence_source_type": "OFFICIAL_DOCUMENT",
            "authority_purpose": "CLAIM_SOURCE",
            "query": "small team automation audit",
            "platform": "YOUTUBE",
            "geo": "US",
            "search_volume_30d": None,
            "relative_interest_index": None,
            "competition_index": None,
            "confidence": "HIGH",
            "captured_at": claim_evidence.captured_at.isoformat(),
        }
    ]
    # The production transition now requires a current topic gate before it
    # can derive and persist novelty authority at GREENLIT.
    _bind_current_topic_authority(session, candidate)
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


def _bind_current_topic_authority(session, candidate) -> None:
    """Give a test candidate one current, topic-capable factual snapshot."""

    existing = TopicDefinitionService(session).current_eligibility(candidate)
    if existing.eligible:
        return
    evidence_id = uuid.UUID(str(candidate.evidence_refs[0]["id"]))
    evidence = session.get(SearchDemandEvidence, evidence_id)
    assert evidence is not None
    topic = TopicDefinitionService(session).create_from_topic_capable_evidence(
        candidate=candidate
    )
    receipt = TopicDefinitionService(session).evaluate(topic)
    assert receipt.current_production_eligibility is True


def _bind_series_topic_authority(session, candidate, *, plan, run) -> None:
    """Create the exact series topic authority used by reservation tests."""

    evidence_id = uuid.UUID(str(candidate.evidence_refs[0]["id"]))
    evidence = session.get(SearchDemandEvidence, evidence_id)
    assert evidence is not None
    snapshot = (evidence.metadata_ or {})["editorial_fresh_evidence"]["source_snapshot"]
    excerpt = str(snapshot["content_excerpt"])
    subject = candidate.proposed_title
    topic = TopicDefinitionService(session).create(
        candidate=candidate,
        fields={
            "subject_type": "OFFICIAL_DOCUMENTED_PRODUCT_OR_FEATURE",
            "subject_name": subject,
            "subject_canonical_id": f"official-document:{evidence.id}",
            "subject_evidence_refs": [{"id": str(evidence.id)}],
            "subject_evidence_spans": [
                {"evidence_id": str(evidence.id), "text": excerpt, "span_hash": span_hash(excerpt)}
            ],
            "target_audience": "small professional teams",
            "audience_problem": "need a bounded evidence-first workflow",
            "content_pillar": candidate.proposed_pillar,
            "production_goal": candidate.proposed_title,
            "scope_inclusions": ["Documented workflow only"],
            "exclusions": ["Unsupported claims"],
            "central_question_or_thesis": f"What does {subject} establish for a small team?",
            "learning_outcome": "Viewers can identify the documented workflow boundary.",
            "viewer_value": "A concrete next step grounded in the source.",
            "content_mode": "SERIES_EPISODE",
            "channel_contract_ref": {"policy_snapshot_id": str(candidate.policy_snapshot_id)},
            "source_classification_refs": [{"source_classification": "TOPIC_CAPABLE"}],
            "series_binding": {
                "series_ref": str(plan.id),
                "run_ref": str(run.id),
                "episode_number": run.next_episode_number,
                "episode_role": "WORKFLOW_DEEP_DIVE",
                "episode_delta": "Advances the series with the next exact documented workflow.",
                "learning_outcome": "Viewers can identify the documented workflow boundary.",
            },
            "standalone_self_containment_required": False,
        },
    )
    receipt = TopicDefinitionService(session).evaluate(topic)
    assert receipt.current_production_eligibility is True


def _fixture_qualification_pass(session, run_id: uuid.UUID) -> ScriptQualificationRun:
    """Seed a previously verified PASS when a test needs a later-stage fixture."""

    qualification = session.get(ScriptQualificationRun, run_id)
    assert qualification is not None
    qualification.state = "QUALIFIED"
    content = {
        "schema_version": "script-qualification-receipt.v3",
        "run_id": str(qualification.id),
        "result": "PASS",
        "script_hash": "a" * 64,
        "assignment_hash": qualification.script_assignment_hash,
        "evidence_pack_hash": qualification.factual_evidence_pack_hash,
        "runtime_contract": qualification.runtime_contract,
        "runtime_contract_hash": qualification.runtime_contract_hash,
        "assignment_resolution": qualification.assignment_resolution,
        "assignment_resolution_hash": qualification.assignment_resolution_hash,
        "memory_digest_hash": qualification.memory_digest_hash,
        "receipts": {
            "structural": {"status": "PASS", "script_hash": "a" * 64, "runtime_contract_hash": qualification.runtime_contract_hash},
            "inventory": {"status": "PASS"},
            "grounding": {
                "status": "PASS",
                "assignment_hash": qualification.script_assignment_hash,
                "evidence_pack_hash": qualification.factual_evidence_pack_hash,
            },
            "fulfillment": {"status": "PASS", "research_coverage_ratio": 1.0},
            "memory": {"status": "PASS_EMPTY", "memory_digest_hash": qualification.memory_digest_hash},
        },
        "qualified_script": {"canonical_script": "placeholder", "language": "en", "sections": [{"section_id": "one", "heading": "One", "narration": "placeholder"}], "claims": []},
        "factual_evidence_pack": qualification.factual_evidence_pack,
        "memory_digest": qualification.memory_digest,
        "producer_provenance": {},
    }
    session.add(
        ScriptQualificationReceipt(
            script_qualification_run_id=qualification.id,
            result="PASS",
            script_hash="a" * 64,
            script_assignment_hash=qualification.script_assignment_hash,
            factual_evidence_pack_hash=qualification.factual_evidence_pack_hash,
            content=content,
            content_hash=content_hash(content),
        )
    )
    session.flush()
    return qualification


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


def test_buffer_below_target_reserves_exactly_one_script_qualification_before_admission(
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
    _bind_current_topic_authority(db_session, candidate)
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
    assert first.decision == CadenceDecision.START_SCRIPT_QUALIFICATION
    assert first.selected_candidate_id == candidate.id
    assert first.script_qualification_run_id is not None
    assert first.admitted_video_project_id is None
    assert first.production_workflow_run_id is None
    qualification = db_session.get(
        ScriptQualificationRun, first.script_qualification_run_id
    )
    assert qualification is not None
    assert qualification.state == "RESERVED"
    assert qualification.editorial_idea_candidate_id == candidate.id
    assert qualification.writer_attempt_key.endswith(":writer")
    assert qualification.verifier_attempt_key.endswith(":verifier")
    pending = TopicDefinitionService(db_session).current_eligibility(candidate)
    assert pending.eligible is False
    assert pending.primary_reason_code == "SCRIPT_QUALIFICATION_PENDING"
    slot = db_session.get(LongFormPublishSlot, first.publish_slot_id)
    assert slot is not None
    assert slot.state == "QUALIFICATION_RESERVED"
    assert slot.reserved_candidate_id == candidate.id
    assert slot.admitted_video_project_id is None
    runway = LaunchRunwayService(db_session).project(run.id)
    assert runway.counts.in_production_videos == 0
    assert runway.counts.greenlit_candidates == 1
    assert (
        db_session.scalar(
            select(func.count(ProductionWorkflowRun.id)).where(
                ProductionWorkflowRun.channel_workspace_id == scope.channel.id
            )
        )
        == 0
    )
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 0


def test_series_episode_reservation_is_pre_writer_idempotent_and_released_on_supersession(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Series Qualification Reservation", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_series_topic_authority(
        db_session, candidate, plan=plans[0], run=series_run
    )
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    receipt = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="series-reservation"),
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    assert receipt.script_qualification_run_id is not None
    qualification = db_session.get(
        ScriptQualificationRun, receipt.script_qualification_run_id
    )
    assert qualification is not None
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    assert reservation is not None
    assert reservation.state == "RESERVED"
    assert reservation.series_plan_id == plans[0].id
    assert reservation.series_run_id == series_run.id
    assert reservation.episode_number == 1
    assert qualification.episode_reservation_active is True
    assert series_run.next_episode_number == 2
    assert series_run.reserved_episode_count == 1

    same = ScriptQualificationService(db_session).reserve(
        candidate=candidate,
        publish_slot_id=qualification.publish_slot_id,
        launch_run_id=launch.id,
    )
    assert same.id == qualification.id
    assert db_session.scalar(select(func.count()).select_from(SeriesEpisodeReservation)) == 1

    ScriptQualificationService(db_session).supersede(qualification.id)
    assert reservation.state == "RELEASED"
    assert qualification.episode_reservation_active is False
    assert series_run.reserved_episode_count == 0
    assert series_run.next_episode_number == 2
    assert db_session.scalar(select(func.count()).select_from(VideoProject)) == 0


def test_structural_block_settles_series_slot_and_releases_capacity_once(
    db_session, qualification_factory
) -> None:
    """A deterministic failure closes the slot without reopening its episode."""

    scope = qualification_factory.channel_scope(
        name="Series terminal qualification settlement", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_series_topic_authority(
        db_session, candidate, plan=plans[0], run=series_run
    )
    qualification_start = LongFormCadenceService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    ).evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="structural-settlement"),
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    assert qualification_start.script_qualification_run_id is not None
    qualification = db_session.get(
        ScriptQualificationRun, qualification_start.script_qualification_run_id
    )
    assert qualification is not None

    class _StructuralBlockProducer(_DeterministicPassingQualificationProducer):
        def write(self, context, *, idempotency_key):
            output, receipt = super().write(context, idempotency_key=idempotency_key)
            output["language"] = "vi"
            return output, receipt

    producer = _StructuralBlockProducer()
    result = ScriptQualificationService(db_session, producer=producer).execute(
        qualification.id
    )
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    slot = db_session.get(LongFormPublishSlot, qualification.publish_slot_id)
    assert result.state == "BLOCKED_NON_REPAIRABLE"
    assert producer.writer_calls == 1 and producer.verifier_calls == 0
    assert reservation is not None and reservation.state == "RELEASED"
    assert series_run.reserved_episode_count == 0
    assert slot is not None and slot.state == "CANCELED"
    assert candidate.stage == "REJECTED"
    assert result.terminal_settlement_receipt is not None
    receipt = result.terminal_settlement_receipt
    assert receipt["capacity_released"] is True
    assert receipt["content_hash"] == content_hash(
        {key: value for key, value in receipt.items() if key != "content_hash"}
    )
    repeated = ScriptQualificationRecoveryService(
        db_session
    ).settle_deterministic_block(
        result, reason_code="SCRIPT_QUALIFICATION_BLOCKED"
    )
    assert repeated == receipt
    assert series_run.reserved_episode_count == 0
    assert db_session.scalar(select(func.count()).select_from(VideoProject)) == 0


def test_series_reservation_allocates_sequential_episodes_from_stale_topic_intent(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Sequential series episode allocation", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    actor = _actor(db_session, scope)
    _, first_candidate, _ = _greenlit_candidate(db_session, scope, actor)
    _, second_candidate, _ = _greenlit_candidate(
        db_session, scope, actor, topic_variant="Second Workflow"
    )
    # Both TopicDefinitions are intentionally created while next_episode is 1.
    _bind_series_topic_authority(
        db_session, first_candidate, plan=plans[0], run=series_run
    )
    _bind_series_topic_authority(
        db_session, second_candidate, plan=plans[0], run=series_run
    )
    slots = LongFormCadenceService(db_session).ensure_slots(launch.id)
    assert len(slots) >= 2

    first = ScriptQualificationService(db_session).reserve(
        candidate=first_candidate,
        publish_slot_id=slots[0].id,
        launch_run_id=launch.id,
    )
    second = ScriptQualificationService(db_session).reserve(
        candidate=second_candidate,
        publish_slot_id=slots[1].id,
        launch_run_id=launch.id,
    )
    assert first.assignment_resolution["episode_number"] == 1
    assert second.assignment_resolution["episode_number"] == 2
    assert first.writer_attempt_key != second.writer_attempt_key
    assert series_run.next_episode_number == 3
    assert series_run.reserved_episode_count == 2


def test_unknown_series_writer_outcome_keeps_episode_reserved(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Unknown Series Qualification Outcome", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_series_topic_authority(
        db_session, candidate, plan=plans[0], run=series_run
    )
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    receipt = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    ).evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="unknown-series-writer"),
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    assert receipt.script_qualification_run_id is not None
    qualification = db_session.get(
        ScriptQualificationRun, receipt.script_qualification_run_id
    )
    assert qualification is not None
    qualification.state = "WRITER_DISPATCHED"

    class _NeverRetryProducer:
        def write(self, *_args, **_kwargs):
            raise AssertionError("unknown outcome must not call writer again")

        def verify(self, *_args, **_kwargs):
            raise AssertionError("unknown outcome must not call verifier")

    result = ScriptQualificationService(
        db_session, producer=_NeverRetryProducer()
    ).execute(qualification.id)
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    assert result.state == "BLOCKED_NON_REPAIRABLE"
    assert reservation is not None and reservation.state == "RESERVED"
    assert qualification.episode_reservation_active is True
    assert series_run.reserved_episode_count == 1
    slot = db_session.get(LongFormPublishSlot, qualification.publish_slot_id)
    terminal_receipt = result.terminal_settlement_receipt
    assert slot is not None and slot.state == "QUALIFICATION_RECONCILIATION_REQUIRED"
    assert terminal_receipt is not None
    assert terminal_receipt["capacity_released"] is False
    assert terminal_receipt["content_hash"] == content_hash(
        {key: value for key, value in terminal_receipt.items() if key != "content_hash"}
    )

    actor = _actor(db_session, scope, admin=True)
    reconciliation = ScriptQualificationRecoveryService(
        db_session
    ).reconcile_provider_outcome(
        run_id=qualification.id,
        decision="NO_PROVIDER_EFFECT_CONFIRMED",
        evidence_refs=[{"ref": "provider-audit://writer/no-effect"}],
        reason_code="PROVIDER_AUDIT_CONFIRMED_NO_EFFECT",
        actor=actor,
    )
    assert reconciliation["outcome"] == "SUPERSEDED_AND_CAPACITY_RELEASED"
    assert reconciliation["content_hash"] == content_hash(
        {key: value for key, value in reconciliation.items() if key != "content_hash"}
    )
    assert qualification.state == "SUPERSEDED"
    assert reservation.state == "RELEASED"
    assert series_run.reserved_episode_count == 0
    assert slot.state == "CANCELED"
    assert ScriptQualificationRecoveryService(db_session).reconcile_provider_outcome(
        run_id=qualification.id,
        decision="NO_PROVIDER_EFFECT_CONFIRMED",
        evidence_refs=[{"ref": "provider-audit://writer/no-effect"}],
        reason_code="PROVIDER_AUDIT_CONFIRMED_NO_EFFECT",
        actor=actor,
    ) == reconciliation
    assert series_run.reserved_episode_count == 0


def test_series_qualification_pass_consumes_the_exact_reserved_episode_once(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Series Qualification Finalization", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_series_topic_authority(
        db_session, candidate, plan=plans[0], run=series_run
    )
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    start = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="series-finalization"),
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    assert start.script_qualification_run_id is not None
    producer = _DeterministicPassingQualificationProducer()
    qualification = ScriptQualificationService(
        db_session, producer=producer, now=lambda: now
    ).execute(start.script_qualification_run_id)
    assert qualification.state == "QUALIFIED"
    assert producer.writer_calls == 1
    assert producer.verifier_calls == 1

    admission, workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    replay_admission, replay_workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    assert reservation is not None
    assert reservation.state == "CONSUMED"
    assert reservation.consumed_admission_decision_id == admission.id
    assert project is not None
    assert project.series_plan_id == reservation.series_plan_id
    assert project.series_run_id == reservation.series_run_id
    assert project.episode_number == reservation.episode_number
    assert admission.series_plan_id == reservation.series_plan_id
    assert admission.series_run_id == reservation.series_run_id
    assert admission.episode_number == reservation.episode_number
    assert replay_admission.id == admission.id
    assert replay_workflow.id == workflow.id
    assert series_run.reserved_episode_count == 1


def test_standalone_qualification_to_production_readiness_uses_only_two_llm_calls(
    db_session, engine, qualification_factory
) -> None:
    """Exercise the active cadence and worker composition through readiness."""

    scope = qualification_factory.channel_scope(
        name="Standalone qualification production closeout", strict_long_form=True
    )
    _configure_verified_destination(db_session, scope)
    policy, admin_actor, _ = _approved_launch_policy(
        db_session,
        scope,
        timezone_name="UTC",
        weekdays=["TUESDAY"],
        include_initial_series=False,
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_current_topic_authority(db_session, candidate)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
    )

    reservation = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="standalone-production-ready"),
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    assert reservation.script_qualification_run_id is not None
    qualification = db_session.get(
        ScriptQualificationRun, reservation.script_qualification_run_id
    )
    assert qualification is not None
    assert qualification.state == "RESERVED"
    assert candidate.stage == "GREENLIT"
    assert qualification.assignment_resolution["content_mode"] == "STANDALONE"
    assert qualification.runtime_contract_hash
    assert (
        db_session.scalar(
            select(func.count(VideoProject.id)).where(
                VideoProject.channel_workspace_id == scope.channel.id
            )
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count(SeriesEpisodeReservation.id)).where(
                SeriesEpisodeReservation.script_qualification_run_id == qualification.id
            )
        )
        == 0
    )

    producer = _DeterministicPassingQualificationProducer()
    qualification = ScriptQualificationService(
        db_session, producer=producer, now=lambda: now
    ).execute(qualification.id)
    receipt = ScriptQualificationService(db_session).require_pass(
        qualification.id, candidate_id=candidate.id
    )
    assert qualification.state == "QUALIFIED"
    assert producer.writer_calls == producer.verifier_calls == 1
    assert producer.writer_assignment_resolution == qualification.assignment_resolution
    assert producer.verifier_assignment_resolution == qualification.assignment_resolution
    assert receipt.result == "PASS"
    assert receipt.content["memory_digest"]["status"] == "EMPTY_SAFE_DIGEST"
    assert receipt.content["runtime_contract_hash"] == qualification.runtime_contract_hash
    assert (
        receipt.content["assignment_resolution_hash"]
        == qualification.assignment_resolution_hash
    )

    admission, workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    assert project.content_mode == "STANDALONE"
    assert project.series_plan_id is None
    assert project.series_run_id is None
    assert project.episode_number is None
    assert admission.content_mode == "STANDALONE"
    assert producer.writer_calls == producer.verifier_calls == 1
    effective = db_session.get(
        EffectiveChannelRuntimeContextSnapshot, project.effective_context_snapshot_id
    )
    assert effective is not None and effective.compile_status == "PASS"

    _support_artifact, support_version, envelope = _support_envelope_for_project(
        db_session, project.id
    )
    qualification_gate = _qualification_gate(envelope)
    assert envelope.execution_mode == "REAL_LONG_FORM_PRODUCTION"
    assert envelope.project_ref.id == project.id
    assert envelope.admission_ref.id == admission.id
    assert qualification_gate["runtime_contract_hash"] == qualification.runtime_contract_hash
    assert (
        qualification_gate["assignment_resolution_hash"]
        == qualification.assignment_resolution_hash
    )
    assert qualification_gate["receipt_hash"] == receipt.content_hash
    _assert_no_pre_readiness_provider_effects(
        db_session, workflow_id=workflow.id, project_id=project.id
    )

    db_session.commit()
    _run_real_worker_to_readiness(engine, workflow.id)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as check:
        ready_workflow = check.get(ProductionWorkflowRun, workflow.id)
        assert ready_workflow is not None
        assert ready_workflow.state == "READY_FOR_PRODUCTION"
        package_version = check.get(
            ArtifactVersion, ready_workflow.production_package_artifact_version_id
        )
        readiness_version = check.get(
            ArtifactVersion,
            ready_workflow.production_readiness_receipt_artifact_version_id,
        )
        assert package_version is not None and readiness_version is not None
        package = ProductionPackageContentV2.model_validate(package_version.content)
        readiness = ProductionReadinessReceiptContentV2.model_validate(
            readiness_version.content
        )
        assert package.content_mode == "STANDALONE"
        assert package.series_plan_id is None and package.series_run_id is None
        assert package.support_envelope_ref is not None
        assert package.support_envelope_ref.artifact_version_id == support_version.id
        assert package.support_envelope_ref.content_hash == support_version.content_hash
        assert package.readiness_evidence.editorial_depth_sufficient is True
        assert package.readiness_evidence.research_coverage_ratio == receipt.content[
            "receipts"
        ]["fulfillment"]["research_coverage_ratio"]
        assert readiness.readiness_state == "READY_FOR_PRODUCTION"
        assert readiness.production_package_artifact_version_id == package_version.id
        assert readiness.production_package_hash == package_version.content_hash
        assert readiness_version.content_hash == ready_workflow.production_readiness_receipt_hash
        _assert_no_pre_readiness_provider_effects(
            check, workflow_id=workflow.id, project_id=project.id
        )


def test_series_qualification_to_production_readiness_preserves_reserved_lineage(
    db_session, engine, qualification_factory
) -> None:
    """The one reserved series episode remains exact through package/readiness."""

    scope = qualification_factory.channel_scope(
        name="Series qualification production closeout", strict_long_form=True
    )
    _configure_verified_destination(db_session, scope)
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    series_run = _approved_series_run(
        db_session, plan=plans[0], actor_user_id=scope.admin.id
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_series_topic_authority(
        db_session, candidate, plan=plans[0], run=series_run
    )
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
    )

    start = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="series-production-ready"),
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    assert start.script_qualification_run_id is not None
    qualification = db_session.get(ScriptQualificationRun, start.script_qualification_run_id)
    assert qualification is not None and qualification.state == "RESERVED"
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    assert reservation is not None and reservation.state == "RESERVED"
    frozen = qualification.assignment_resolution
    assert candidate.stage == "GREENLIT"
    assert frozen["content_mode"] == "SERIES_EPISODE"
    assert frozen["series_plan_id"] == str(plans[0].id)
    assert frozen["series_run_id"] == str(series_run.id)
    assert frozen["episode_number"] == reservation.episode_number == 1
    assert frozen["episode_role"] == reservation.episode_role
    assert frozen["episode_delta"] == reservation.episode_delta
    assert series_run.next_episode_number == 2
    assert (
        db_session.scalar(
            select(func.count(VideoProject.id)).where(
                VideoProject.channel_workspace_id == scope.channel.id
            )
        )
        == 0
    )

    producer = _DeterministicPassingQualificationProducer()
    qualification = ScriptQualificationService(
        db_session, producer=producer, now=lambda: now
    ).execute(qualification.id)
    receipt = ScriptQualificationService(db_session).require_pass(
        qualification.id, candidate_id=candidate.id
    )
    assert qualification.state == "QUALIFIED"
    assert producer.writer_calls == producer.verifier_calls == 1
    assert producer.writer_assignment_resolution == frozen
    assert producer.verifier_assignment_resolution == frozen
    assert producer.verifier_assignment_resolution["episode_delta"] == reservation.episode_delta
    assert receipt.content["memory_digest"]["status"] == "EMPTY_SAFE_DIGEST"
    assert reservation.state == "RESERVED"

    admission, workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    replay_admission, replay_workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    assert replay_admission.id == admission.id
    assert replay_workflow.id == workflow.id
    assert producer.writer_calls == producer.verifier_calls == 1
    assert reservation.state == "CONSUMED"
    assert reservation.consumed_admission_decision_id == admission.id
    assert qualification.episode_reservation_active is False
    for record in (admission, project):
        assert record.content_mode == "SERIES_EPISODE"
        assert record.series_plan_id == reservation.series_plan_id
        assert record.series_run_id == reservation.series_run_id
        assert record.episode_number == reservation.episode_number
        assert record.episode_role == reservation.episode_role

    _support_artifact, support_version, envelope = _support_envelope_for_project(
        db_session, project.id
    )
    qualification_gate = _qualification_gate(envelope)
    assert envelope.project_ref.id == project.id
    assert envelope.admission_ref.id == admission.id
    assert envelope.admission_ref.content_hash == admission.decision_hash
    assert qualification_gate["runtime_contract_hash"] == qualification.runtime_contract_hash
    assert (
        qualification_gate["assignment_resolution_hash"]
        == qualification.assignment_resolution_hash
    )
    assert qualification_gate["receipt_hash"] == receipt.content_hash
    _assert_no_pre_readiness_provider_effects(
        db_session, workflow_id=workflow.id, project_id=project.id
    )

    db_session.commit()
    _run_real_worker_to_readiness(engine, workflow.id)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as check:
        ready_workflow = check.get(ProductionWorkflowRun, workflow.id)
        assert ready_workflow is not None
        assert ready_workflow.state == "READY_FOR_PRODUCTION"
        package_version = check.get(
            ArtifactVersion, ready_workflow.production_package_artifact_version_id
        )
        readiness_version = check.get(
            ArtifactVersion,
            ready_workflow.production_readiness_receipt_artifact_version_id,
        )
        assert package_version is not None and readiness_version is not None
        package = ProductionPackageContentV2.model_validate(package_version.content)
        readiness = ProductionReadinessReceiptContentV2.model_validate(
            readiness_version.content
        )
        assert package.content_mode == "SERIES_EPISODE"
        assert package.series_plan_id == reservation.series_plan_id
        assert package.series_run_id == reservation.series_run_id
        assert package.episode_number == reservation.episode_number
        assert package.episode_role == reservation.episode_role
        assert package.support_envelope_ref is not None
        assert package.support_envelope_ref.artifact_version_id == support_version.id
        assert package.support_envelope_ref.content_hash == support_version.content_hash
        assert package.readiness_evidence.editorial_depth_sufficient is True
        assert package.readiness_evidence.research_coverage_ratio == receipt.content[
            "receipts"
        ]["fulfillment"]["research_coverage_ratio"]
        assert readiness.readiness_state == "READY_FOR_PRODUCTION"
        assert readiness.production_package_hash == package_version.content_hash
        assert readiness_version.content_hash == ready_workflow.production_readiness_receipt_hash
        _assert_no_pre_readiness_provider_effects(
            check, workflow_id=workflow.id, project_id=project.id
        )


def test_qualification_pass_is_the_only_path_to_cadence_admission(
    db_session, qualification_factory
) -> None:
    scope = qualification_factory.channel_scope(
        name="Cadence Qualification Finalization", strict_long_form=True
    )
    policy, admin_actor, _ = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    run = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_current_topic_authority(db_session, candidate)
    fixed_now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    service = LongFormCadenceService(
        db_session,
        now=lambda: fixed_now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    receipt = service.evaluate(
        launch_run_id=run.id,
        data=CadenceEvaluationCommand(evaluation_key="qualification-finalization"),
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    assert receipt.script_qualification_run_id is not None

    class _PassingProducer:
        sentences: list[str] = []
        section_ids: list[str] = []

        @staticmethod
        def _receipt(*, idempotency_key: str, lane_name: str) -> dict[str, str]:
            return {
                "status": "SUCCESS",
                "idempotency_key": idempotency_key,
                "lane_name": lane_name,
                "selected_model": "gpt-5.6-luna",
                "fallback_level": "PRIMARY",
                "route_attempt_id": str(uuid.uuid4()),
                "provider_attempt_id": str(uuid.uuid4()),
                "llm_run_snapshot_id": str(uuid.uuid4()),
            }

        def write(self, context, *, idempotency_key):
            evidence_id = context["factual_evidence_pack"]["spans"][0]["evidence_span_id"]
            requirements = [
                item["requirement_id"]
                for item in context["script_assignment"]["required_requirement_units"]
            ]
            self.sentences = [
                (
                    f"The documented audit workflow fulfills the {requirement} obligation with its exact evidence boundary "
                    + " ".join(f"detail{index}_{word}" for word in range(1, 151))
                    + "."
                )
                for index, requirement in enumerate(requirements, start=1)
            ]
            self.section_ids = [
                "hook" if index < 3 else "body" if index < 6 else "close"
                for index in range(len(self.sentences))
            ]
            return (
                {
                    "canonical_script": " ".join(self.sentences),
                    "language": "en",
                    "sections": [
                        {"section_id": "hook", "heading": "Hook", "narration": " ".join(self.sentences[:3])},
                        {"section_id": "body", "heading": "Body", "narration": " ".join(self.sentences[3:6])},
                        {"section_id": "close", "heading": "Close", "narration": " ".join(self.sentences[6:])},
                    ],
                    "claims": [
                        {"claim_id": f"writer-{index}", "claim_text": text, "evidence_span_ids": [evidence_id]}
                        for index, text in enumerate(self.sentences, start=1)
                    ],
                },
                    self._receipt(idempotency_key=idempotency_key, lane_name="long_context_text"),
            )

        def verify(self, context, *, idempotency_key):
            script = context["canonical_script"]
            spans = []
            for index, (text, section_id) in enumerate(zip(self.sentences, self.section_ids, strict=True), start=1):
                spans.append({
                    "text": text,
                    "section_id": section_id,
                })
            evidence_id = context["factual_evidence_pack"]["spans"][0]["evidence_span_id"]
            requirements = [
                item["requirement_id"]
                for item in context["script_assignment"]["required_requirement_units"]
            ]
            return (
                {
                    "material_claim_inventory": [
                        {
                            "observed_claim_id": f"observed-{index}",
                            "span": span,
                            "claim_type": "FACTUAL_ASSERTION",
                            "materiality_state": "MATERIAL",
                            "writer_declared_claim_id": f"writer-{index}",
                            "factual_evidence_span_ids": [evidence_id],
                            "semantic_relation": "ENTAILED",
                            "assignment_requirement_ids": [requirements[index - 1]],
                        }
                        for index, span in enumerate(spans, start=1)
                    ],
                    "assignment_fulfillment_observations": [
                        {"requirement_id": requirement, "status": "SUFFICIENT", "spans": [spans[index]], "evidence_span_ids": [evidence_id]}
                        for index, requirement in enumerate(requirements)
                    ],
                        "section_purpose_observations": [
                        {"section_id": "hook", "observed_primary_role": "HOOK", "fulfilled_requirement_ids": requirements[:3], "editorial_delta": "Establishes the bounded subject, angle, and question.", "genericity_state": "SPECIFIC"},
                        {"section_id": "body", "observed_primary_role": "MECHANISM", "fulfilled_requirement_ids": requirements[3:6], "editorial_delta": "Connects scope to the audience decision.", "genericity_state": "SPECIFIC"},
                            {"section_id": "close", "observed_primary_role": "CLOSING_INSIGHT", "fulfilled_requirement_ids": requirements[6:], "editorial_delta": "Turns the evidence boundary into a standalone next step.", "genericity_state": "SPECIFIC"},
                        ],
                        "forbidden_scope_observations": [
                            {"forbidden_scope_id": item["forbidden_scope_id"], "state": "ABSENT"}
                            for item in context["script_assignment"]["forbidden_scope_units"]
                        ],
                },
                    self._receipt(idempotency_key=idempotency_key, lane_name="gatekeeper_soft_review"),
            )

    qualification = ScriptQualificationService(
        db_session, producer=_PassingProducer(), now=lambda: fixed_now
    ).execute(receipt.script_qualification_run_id)
    assert qualification.state == "QUALIFIED"
    qualification_receipt = db_session.scalar(
        select(ScriptQualificationReceipt).where(
            ScriptQualificationReceipt.script_qualification_run_id == qualification.id
        )
    )
    assert qualification_receipt is not None
    assert qualification_receipt.result == "PASS"
    provenance = qualification_receipt.content["producer_provenance"]
    assert provenance["writer"]["producer_input_hash"]
    assert provenance["writer"]["producer_output_hash"]
    assert provenance["writer"]["prompt_version"] == qualification.writer_prompt_version
    assert provenance["writer"]["route_attempt_id"]
    assert provenance["verifier"]["producer_input_hash"]
    assert qualification_receipt.content["memory_digest"]["status"] == "EMPTY_SAFE_DIGEST"

    admission, workflow = service.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor("vcos-durable-worker", permissions={"production.start"}),
    )
    assert admission.admitted_video_project_id is not None
    assert workflow.video_project_id == admission.admitted_video_project_id
    assert candidate.stage == "IN_PRODUCTION"
    assert qualification.admitted_video_project_id == admission.admitted_video_project_id
    slot = db_session.get(LongFormPublishSlot, receipt.publish_slot_id)
    assert slot is not None
    assert slot.state == "RESERVED"
    assert slot.admitted_video_project_id == admission.admitted_video_project_id


def test_runway_excludes_terminal_in_production_orphan_but_cadence_cannot_reselect_it(
    db_session, qualification_factory
) -> None:
    workflow, _dead_letter, _incident, _origin_event = _stale_zero_effect_workflow(
        db_session, qualification_factory
    )
    workflow.state = "SUPERSEDED"
    db_session.flush()

    launch_run = db_session.scalar(
        select(LaunchRun).where(
            LaunchRun.channel_workspace_id == workflow.channel_workspace_id
        )
    )
    admission = db_session.get(
        ProjectAdmissionDecision, workflow.project_admission_decision_id
    )
    candidate = db_session.get(
        EditorialIdeaCandidate,
        admission.editorial_idea_candidate_id if admission is not None else None,
    )
    assert launch_run is not None
    assert candidate is not None
    assert candidate.stage == "IN_PRODUCTION"

    runway = LaunchRunwayService(db_session).project(launch_run.id)
    assert runway.counts.in_production_videos == 0
    assert runway.counts.greenlit_candidates == 0
    policy = db_session.get(
        FirstChannelLaunchPolicyVersion, launch_run.launch_policy_version_id
    )
    assert policy is not None
    assert LongFormCadenceService(db_session)._strict_candidates(launch_run, policy) == []


def test_workflow_start_blocks_missing_effective_context_before_research_dispatch(
    db_session, qualification_factory
) -> None:
    workflow, _dead_letter, _incident, _origin_event = _stale_zero_effect_workflow(
        db_session, qualification_factory
    )

    with pytest.raises(
        ValidationFailureError, match="WORKFLOW_EFFECTIVE_CONTEXT_NOT_PASS"
    ):
        ProductionWorkflowCoordinator(db_session).start_from_project_system(
            video_project_id=workflow.video_project_id,
            company_id=workflow.company_id,
            data=ProductionWorkflowProjectStart(
                idempotency_key="missing-effective-context-must-not-dispatch"
            ),
            actor=_system_worker_actor(
                "vcos-durable-worker", permissions={"production.start"}
            ),
        )


def _stale_zero_effect_workflow(db_session, qualification_factory, *, series: bool = False):
    scope = qualification_factory.channel_scope(
        name="Stale workflow recovery", strict_long_form=True
    )
    policy, admin_actor, plans = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    launch_run = _active_launch_run(
        db_session, policy, admin_actor, started_on=date(2026, 7, 20)
    )
    producer_actor = _actor(db_session, scope)
    _, candidate, _ = _greenlit_candidate(db_session, scope, producer_actor)
    if series:
        series_run = _approved_series_run(
            db_session, plan=plans[0], actor_user_id=scope.admin.id
        )
        _bind_series_topic_authority(
            db_session, candidate, plan=plans[0], run=series_run
        )
    else:
        _bind_current_topic_authority(db_session, candidate)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    receipt = cadence.evaluate(
        launch_run_id=launch_run.id,
        data=CadenceEvaluationCommand(evaluation_key="stale-zero-effect-start"),
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    assert receipt.script_qualification_run_id is not None
    qualification = _fixture_qualification_pass(
        db_session, receipt.script_qualification_run_id
    )
    admission, workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    origin_event = db_session.scalar(
        select(DomainEvent)
        .where(DomainEvent.workflow_run_id == workflow.id)
        .order_by(DomainEvent.occurred_at)
        .limit(1)
    )
    assert workflow is not None and project is not None and origin_event is not None
    project.effective_context_snapshot_id = None
    workflow.project_admission_decision_id = project.project_admission_decision_id
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
    admission = db_session.get(
        ProjectAdmissionDecision, workflow.project_admission_decision_id
    )
    assert admission is not None
    candidate = db_session.get(
        EditorialIdeaCandidate,
        admission.editorial_idea_candidate_id,
    )
    publish_slot = db_session.scalar(
        select(LongFormPublishSlot).where(
            LongFormPublishSlot.admitted_video_project_id == workflow.video_project_id
        )
    )
    assert candidate is not None
    assert candidate.stage == "REJECTED"
    assert "ZERO_EFFECT_ADMISSION_LINEAGE_CLOSED" in candidate.reason_codes
    assert publish_slot is not None
    assert publish_slot.state == "CANCELED"
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


def test_zero_effect_stale_series_workflow_abandons_capacity_once(
    db_session, qualification_factory
) -> None:
    workflow, dead_letter, _incident, _origin_event = _stale_zero_effect_workflow(
        db_session, qualification_factory, series=True
    )
    qualification = db_session.scalar(
        select(ScriptQualificationRun).where(
            ScriptQualificationRun.production_workflow_run_id == workflow.id
        )
    )
    assert qualification is not None
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    assert reservation is not None and reservation.state == "CONSUMED"
    series_run = db_session.get(SeriesRun, reservation.series_run_id)
    assert series_run is not None and series_run.reserved_episode_count == 1

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
    assert receipt.dead_letter_job_id == dead_letter.id
    assert reservation.state == "ABANDONED_AFTER_ADMISSION"
    assert reservation.abandoned_reason_code == (
        "ZERO_EFFECT_WORKFLOW_ABANDONED_AFTER_ADMISSION"
    )
    assert series_run.reserved_episode_count == 0
    assert recovery.execute_event(
        event=event,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    ).id == receipt.id
    assert series_run.reserved_episode_count == 0


def test_effectful_stale_dead_letter_is_never_auto_superseded(
    db_session, qualification_factory
) -> None:
    workflow, _dead_letter, _incident, _origin_event = _stale_zero_effect_workflow(
        db_session, qualification_factory, series=True
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
    qualification = db_session.scalar(
        select(ScriptQualificationRun).where(
            ScriptQualificationRun.production_workflow_run_id == workflow.id
        )
    )
    assert qualification is not None
    reservation = db_session.scalar(
        select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification.id
        )
    )
    assert reservation is not None and reservation.state == "CONSUMED"
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
    _, candidate, _ = _greenlit_candidate(db_session, scope, producer_actor)
    _bind_current_topic_authority(db_session, candidate)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    receipt = cadence.evaluate(
        launch_run_id=launch_run.id,
        data=CadenceEvaluationCommand(evaluation_key="open-mix-launch-guard"),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )

    assert receipt.admitted_video_project_id is None
    assert receipt.decision == CadenceDecision.START_SCRIPT_QUALIFICATION
    assert receipt.script_qualification_run_id is not None
    qualification = _fixture_qualification_pass(
        db_session, receipt.script_qualification_run_id
    )
    admission, workflow = cadence.finalize_qualified_script_run(
        script_qualification_run_id=qualification.id,
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    assert workflow.video_project_id == project.id
    # The standalone TopicDefinition is frozen before writer dispatch, so a
    # later OPEN_MIX decision cannot convert it into either series run.
    assert project.content_mode == "STANDALONE"
    assert project.series_plan_id is None
    assert project.series_run_id is None
    assert allowed_run.reserved_episode_count == 0
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
