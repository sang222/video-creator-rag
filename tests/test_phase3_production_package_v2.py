from __future__ import annotations

import inspect
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.mr1 import (
    MR1StartCommand,
    MR1V2AutomatedAdmissionRequest,
)
from app.contracts.launch_cadence import (
    FirstChannelLaunchPolicyCreate,
    LaunchPolicyApproval,
    LaunchRunCreate,
    LaunchRunTransition,
)
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.profile import ChannelProfileInput
from app.contracts.production_package import (
    DURATION_CONTRACT_VERSION_V2,
    ExactContentRefV2,
    ProductionDurationContractV2,
    ProductionPackageContentV2,
    ProductionPackageCreateV2,
    ProductionPackageMateriality,
    ProductionPackageRevisionRequestV2,
    ProductionReadinessEvidenceV2,
    ProductionRevisionV2,
)
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.vcos_v2 import (
    AssignmentMode,
    DurationContractV2,
    LongFormPlanningRequest,
    ProductionLane,
)
from app.core.actor import authenticated_actor_context
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    FirstScriptedVideoPackage,
    GateRun,
    ReviewTask,
)
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.foundation import Company, User
from app.db.models.m5 import (
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
)
from app.db.models.launch_cadence import LaunchRun
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.workflow import VideoProject
from app.services.channel_profile import ChannelProfileService
from app.services.channel_workspace import ChannelWorkspaceService
from app.services.company import CompanyService
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.m12_2 import _expand_script_to_word_budget
from app.services.ofv0 import FormatIdentityContractService
from app.services.long_production import LongProductionOrchestrator
from app.services.launch_cadence import (
    FirstChannelLaunchPolicyService,
    LaunchRunService,
)
from app.services.m6 import LocalFixtureRendererService
from app.services.mr1_real_production import (
    MR1ProviderGateways,
    MR1RealProductionService,
)
from app.services.production_package import (
    ChannelDurationContractResolver,
    ProductionPackageService,
    ProductionReadinessService,
    REQUIRED_PRODUCTION_GATE_KEYS,
    strategic_lineage_from_record,
)
from app.services.profile_compiler import ChannelProfileCompiler
from app.services.r3d1 import R3D1AdminService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.rbac import RBACService
from app.services.vcos_v2 import LongFormPlanningService
from app.services.workflow import ArtifactService


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Phase3Scope:
    company: Company
    operator: User
    channel: ChannelWorkspace
    profile: ChannelProfileVersion
    policy: CompiledChannelPolicySnapshot
    duration: DurationContractV2
    admission: ProjectAdmissionDecision
    project: VideoProject
    effective: EffectiveChannelRuntimeContextSnapshot
    launch_run: LaunchRun

    def pause_active_launch_run(self, session: Session) -> LaunchRun:
        """Stop fixture-only cadence commands after V2 lineage is frozen."""

        run = session.get(LaunchRun, self.launch_run.id)
        assert run is not None
        if run.state != "ACTIVE":
            return run
        actor = authenticated_actor_context(
            canonical_user_id=self.operator.id,
            operator_user_id=self.operator.id,
            actor_role="COMPANY_ADMIN",
            permissions=RBACService(session).permissions_for_user(
                user_id=self.operator.id,
                company_id=self.company.id,
            ),
        )
        return LaunchRunService(session).transition(
            launch_run_id=run.id,
            data=LaunchRunTransition(
                target_state="PAUSED",
                reason_codes=["FIXTURE_LINEAGE_CAPTURED"],
            ),
            actor=actor,
        )


def _exact_duration(
    *,
    profile_id: uuid.UUID,
    policy_id: uuid.UUID,
    minimum_ms: int,
    target_ms: int,
    maximum_ms: int,
) -> DurationContractV2:
    digest = DurationContractV2.calculate_hash(
        minimum_duration_ms=minimum_ms,
        target_duration_ms=target_ms,
        maximum_duration_ms=maximum_ms,
        duration_contract_version=DURATION_CONTRACT_VERSION_V2,
        source_profile_version_id=profile_id,
        source_policy_snapshot_id=policy_id,
    )
    return DurationContractV2(
        minimum_duration_ms=minimum_ms,
        target_duration_ms=target_ms,
        maximum_duration_ms=maximum_ms,
        duration_contract_version=DURATION_CONTRACT_VERSION_V2,
        duration_contract_hash=digest,
        source_profile_version_id=profile_id,
        source_policy_snapshot_id=policy_id,
    )


def _scope(
    session: Session,
    *,
    minimum_ms: int = 240_000,
    target_ms: int = 300_000,
    maximum_ms: int = 420_000,
    duration_in_authority: bool = True,
) -> _Phase3Scope:
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = CompanyService(session).create_company(
        name=f"Phase 3 {uuid.uuid4().hex[:8]}"
    )
    operator = User(
        email=f"phase3-{uuid.uuid4()}@example.com",
        display_name="Phase 3 Operator",
        status="active",
    )
    session.add(operator)
    session.flush()
    RBACService(session).assign_role(
        user_id=operator.id,
        role_key="operator",
        company_id=company.id,
    )
    RBACService(session).assign_role(
        user_id=operator.id,
        role_key="company_admin",
        company_id=company.id,
    )
    channel = ChannelWorkspaceService(session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            # Phase 3's trusted production fixture must compile the active
            # channel-scoped budget/provider authority.  That authority is
            # intentionally declared only for the canonical long-form lane.
            key="small-team-ai",
            name="Phase 3 Channel",
        ),
    )
    profile = ChannelProfileService(session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(
            template_key="saas_digital_leverage",
            created_by=operator.id,
        ),
    )
    policy_id = uuid.uuid4()
    duration = _exact_duration(
        profile_id=profile.id,
        policy_id=policy_id,
        minimum_ms=minimum_ms,
        target_ms=target_ms,
        maximum_ms=maximum_ms,
    )
    profile_payload = deepcopy(profile.profile_input)
    format_strategy = profile_payload.setdefault("format_strategy", {})
    if duration_in_authority:
        duration_payload = duration.model_dump(mode="json")
        format_strategy["duration_contract"] = duration_payload
        format_strategy.setdefault("duration_contracts", {})[
            ProductionLane.LONG_FORM.value
        ] = duration_payload
    else:
        format_strategy.pop("duration_contract", None)
        format_strategy.pop("duration_contract_v2", None)
        format_strategy.setdefault("duration_contracts", {}).pop(
            ProductionLane.LONG_FORM.value,
            None,
        )
        format_strategy.setdefault("long_form", {}).pop(
            "duration_contract",
            None,
        )
    profile.profile_input = profile_payload
    profile.profile_input_hash = content_hash(profile_payload)
    session.flush()

    format_contract = FormatIdentityContractService(session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile.id,
            created_by="phase3-trusted-fixture",
        )
    )
    FormatIdentityContractService(session).approve(
        format_contract.id,
        decided_by="phase3-trusted-fixture",
    )

    compiler = ChannelProfileCompiler(session)
    parsed_profile = ChannelProfileInput.model_validate(profile_payload)
    catalogs = compiler.load_catalogs(parsed_profile.template_key)
    policy_payload, policy_hash = compiler.compile_from_input(
        profile_input=parsed_profile,
        template=catalogs.template,
        capability_matrix=catalogs.capability_matrix,
        compiler_policy=catalogs.compiler_policy,
        channel=channel,
        profile_input_hash_override=profile.profile_input_hash,
    )
    policy = CompiledChannelPolicySnapshot(
        id=policy_id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile.id,
        compile_run_id=None,
        snapshot_version=1,
        status="approved",
        compiler_version=catalogs.compiler_policy.compiler_version,
        capability_matrix_version=catalogs.capability_catalog.catalog_version,
        compiled_payload=policy_payload,
        content_hash=policy_hash,
        profile_input_hash=profile.profile_input_hash,
    )
    session.add(policy)
    session.flush()
    ChannelProfileService(session).activate_snapshot(snapshot_id=policy.id)
    actor = authenticated_actor_context(
        canonical_user_id=operator.id,
        operator_user_id=operator.id,
        actor_role="COMPANY_ADMIN",
        permissions=RBACService(session).permissions_for_user(
            user_id=operator.id,
            company_id=company.id,
        ),
    )
    launch_evidence = [{"type": "phase3_fixture", "ref": "launch-authority"}]
    launch_policy = FirstChannelLaunchPolicyService(session).create(
        data=FirstChannelLaunchPolicyCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=policy.id,
            approved_initial_series_plan_ids=[],
            evidence_refs=launch_evidence,
        ),
        actor=actor,
    )
    launch_policy = FirstChannelLaunchPolicyService(session).approve(
        policy_version_id=launch_policy.id,
        data=LaunchPolicyApproval(evidence_refs=launch_evidence),
        actor=actor,
    )
    launch_run = LaunchRunService(session).create(
        data=LaunchRunCreate(
            launch_policy_version_id=launch_policy.id,
            launch_key=f"phase3-{uuid.uuid4().hex[:12]}",
            preparation_started_on=date(2026, 7, 1),
        ),
        actor=actor,
    )
    LaunchRunService(session).transition(
        launch_run_id=launch_run.id,
        data=LaunchRunTransition(
            target_state="READY_TO_LAUNCH",
            reason_codes=["PHASE3_FIXTURE_READY"],
        ),
        actor=actor,
    )
    LaunchRunService(session).transition(
        launch_run_id=launch_run.id,
        data=LaunchRunTransition(
            target_state="ACTIVE",
            reason_codes=["PHASE3_FIXTURE_ACTIVE"],
        ),
        actor=actor,
    )
    category = R3D1AdminService(session).create_content_category(
        ContentCategoryCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            category_key=f"phase3-{uuid.uuid4().hex[:8]}",
            name="Phase 3 Category",
            sub_niche="research-backed operator education",
            audience_segment="creator operators",
            content_pillar="education",
            default_format_policy_json={"format": "explainer"},
            default_visual_style_json={"style_note": "clean diagrams"},
            default_voice_style_json={"tone": "calm"},
            default_thumbnail_style_json={"style": "clear text"},
            visual_mode="DIAGRAM_FIRST",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )

    slot = EditorialCalendarSlot(
        company_id=company.id,
        channel_workspace_id=channel.id,
        policy_snapshot_id=policy.id,
        slot_date=date(2026, 7, 28),
        slot_type="CAMPAIGN",
        status="OPEN",
        schema_version="v2",
        production_lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
        category_id=category.id,
        production_goal="Phase 3 canonical production package",
        target_platforms=["YOUTUBE"],
        risk_level="LOW",
        operational_envelope={},
        created_by_user_id=operator.id,
    )
    session.add(slot)
    session.flush()
    preflight = IdeaMarketPreflight(
        company_id=company.id,
        channel_workspace_id=channel.id,
        editorial_calendar_slot_id=slot.id,
        policy_fit_state="PASS",
        confidence_state="HIGH",
        evidence_blob={
            "authority": "phase3-test",
            "niche_contract_digest_hash": "a" * 64,
            "target_market_digest_hash": "b" * 64,
        },
        niche_contract_digest_hash="a" * 64,
        target_market_digest_hash="b" * 64,
        reason_codes=["SYSTEM_OK"],
        decision="PASS",
    )
    session.add(preflight)
    session.flush()
    admission = LongFormPlanningService(session).admit(
        LongFormPlanningRequest(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=policy.id,
            editorial_calendar_slot_id=slot.id,
            idea_market_preflight_id=preflight.id,
            assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
            title="Exact channel-scoped duration",
            description="Canonical Phase 3 package fixture",
            category_id=category.id,
            niche_gate_passed=True,
            market_gate_passed=True,
            duration_contract=duration,
            created_by_user_id=operator.id,
        )
    )
    assert admission.admitted_video_project_id is not None, admission.reason_codes
    project = session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    effective = EffectiveChannelRuntimeContextCompiler(session).ensure_for_project(
        project.id,
        editorial_calendar_slot_id=slot.id,
    )
    assert effective.compile_status == "PASS", effective.reason_codes_json
    return _Phase3Scope(
        company=company,
        operator=operator,
        channel=channel,
        profile=profile,
        policy=policy,
        duration=duration,
        admission=admission,
        project=project,
        effective=effective,
        launch_run=launch_run,
    )


def _artifact_ref(
    session: Session,
    scope: _Phase3Scope,
    label: str,
    *,
    artifact_type: str,
    content: dict,
) -> ExactContentRefV2:
    artifacts = ArtifactService(session)
    artifact = artifacts.create_artifact(
        data=ArtifactCreate(
            video_project_id=scope.project.id,
            artifact_type=artifact_type,
            created_by_user_id=scope.operator.id,
        ),
        correlation_id=f"phase3-test-{label}-artifact",
    )
    version = artifacts.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            content=content,
            status="approved",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id=f"phase3-test-{label}-version",
    )
    artifact.status = "approved"
    session.flush()
    return ExactContentRefV2(
        type=label,
        ref=f"artifact://{artifact.id}",
        artifact_version_id=version.id,
        version=version.version_number,
        content_hash=version.content_hash,
    )


def _content(
    session: Session,
    scope: _Phase3Scope,
    *,
    evidence_updates: dict | None = None,
) -> ProductionPackageContentV2:
    evidence = {
        "research_evidence_complete": True,
        "niche_market_gates_pass": True,
        "assignment_integrity_pass": True,
        "editorial_depth_sufficient": True,
        "supported_claim_count": 5,
        "distinct_editorial_section_count": 4,
        "research_coverage_ratio": 0.9,
        "script_duration_ms": scope.duration.target_duration_ms,
        "anti_padding_pass": True,
        "padding_phrase_hits": 0,
        "repeated_sentence_ratio": 0.0,
        "script_gates_pass": True,
        "visual_thumbnail_metadata_gates_pass": True,
        "rights_disclosure_gates_pass": True,
        "provider_plan_valid": True,
        "budget_scope_valid": True,
        "package_integrity_inputs_complete": True,
        "unresolved_exception_types": [],
        "new_planning_cycle": False,
    }
    evidence.update(evidence_updates or {})
    claim_count = int(evidence["supported_claim_count"])
    section_count = int(evidence["distinct_editorial_section_count"])
    script_content = {
        "readiness_result": "PASS",
        "narration_text": (
            "Research-backed operators need exact evidence, exact duration, "
            "and deterministic production lineage."
        ),
        "estimated_duration_ms": int(evidence["script_duration_ms"]),
        "supported_claims": [
            {"claim_id": f"claim-{index + 1}", "evidence_ref": "research"}
            for index in range(claim_count)
        ],
        "sections": [
            {
                "section_id": f"section-{index + 1}",
                "sentences": [
                    {
                        "text": (
                            f"Unique research-backed editorial sentence {index + 1}."
                        )
                    }
                ],
            }
            for index in range(section_count)
        ],
        "research_coverage_ratio": float(evidence["research_coverage_ratio"]),
    }
    strategic_lineage = strategic_lineage_from_record(
        scope.project,
        invalid_reason_code="TEST_PROJECT_STRATEGIC_LINEAGE_INVALID",
    )
    assert strategic_lineage is not None
    return ProductionPackageContentV2(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        project_admission_decision_id=scope.admission.id,
        project_admission_decision_hash=scope.admission.decision_hash,
        channel_profile_version_id=scope.profile.id,
        channel_profile_hash=scope.profile.profile_input_hash,
        compiled_policy_snapshot_id=scope.policy.id,
        compiled_policy_snapshot_hash=scope.policy.content_hash,
        strategic_lineage=strategic_lineage,
        effective_context_ref=ExactContentRefV2(
            type="effective_context",
            ref=f"effective-context://{scope.effective.id}",
            id=scope.effective.id,
            content_hash=scope.effective.context_hash,
            version=1,
        ),
        production_lane=scope.admission.production_lane,
        assignment_mode=scope.admission.assignment_mode,
        content_mode=scope.admission.content_mode,
        series_plan_id=scope.admission.series_plan_id,
        series_run_id=scope.admission.series_run_id,
        episode_number=scope.admission.episode_number,
        episode_role=scope.admission.episode_role,
        standalone_reason_code=scope.admission.standalone_reason_code,
        duration_contract=ProductionDurationContractV2.model_validate(
            scope.duration.model_dump(mode="python")
        ),
        research_refs=[
            _artifact_ref(
                session,
                scope,
                "research",
                artifact_type="research_pack",
                content={
                    "readiness_result": "PASS",
                    "evidence_complete": True,
                    "evidence_refs": ["source-pack"],
                },
            )
        ],
        source_refs=[
            _artifact_ref(
                session,
                scope,
                "source",
                artifact_type="source_pack",
                content={
                    "readiness_result": "PASS",
                    "source_count": 1,
                    "sources": [{"ref": "source://phase3"}],
                },
            )
        ],
        niche_market_gate_refs=[
            _artifact_ref(
                session,
                scope,
                "niche_gate",
                artifact_type="niche_alignment_dossier",
                content={"result": "PASS"},
            ),
            _artifact_ref(
                session,
                scope,
                "market_gate",
                artifact_type="market_alignment_dossier",
                content={"result": "PASS"},
            ),
        ],
        script_ref=_artifact_ref(
            session,
            scope,
            "script",
            artifact_type="script",
            content=script_content,
        ),
        visual_plan_ref=_artifact_ref(
            session,
            scope,
            "visual_plan",
            artifact_type="visual_plan",
            content={"result": "PASS"},
        ),
        thumbnail_refs=[
            _artifact_ref(
                session,
                scope,
                "thumbnail",
                artifact_type="thumbnail_brief",
                content={"result": "PASS"},
            )
        ],
        metadata_ref=_artifact_ref(
            session,
            scope,
            "metadata",
            artifact_type="publishing_metadata_package",
            content={"result": "PASS"},
        ),
        rights_disclosure_refs=[
            _artifact_ref(
                session,
                scope,
                "rights",
                artifact_type="rights_disclosure_completeness_report",
                content={"result": "PASS"},
            )
        ],
        provider_execution_plan_ref=_artifact_ref(
            session,
            scope,
            "provider_plan",
            artifact_type="provider_execution_plan",
            content={"result": "PASS"},
        ),
        budget_scope_ref=_artifact_ref(
            session,
            scope,
            "budget_scope",
            artifact_type="cost_estimate_snapshot",
            content={"result": "PASS"},
        ),
        destination_binding_ref=_artifact_ref(
            session,
            scope,
            "destination",
            artifact_type="destination_binding",
            content={
                "result": "PASS",
                "publish_execution_allowed": True,
                "destination": {
                    "channel_workspace_id": str(scope.channel.id),
                    "platform": "YOUTUBE",
                    "platform_account_ref": "youtube-account://phase3-fixture",
                    "platform_channel_id": "channel-phase3-fixture",
                    "status": "VERIFIED",
                    "verified_at": utc_now().isoformat(),
                    "verification_method": "DETERMINISTIC_TEST_FIXTURE",
                },
            },
        ),
        readiness_evidence=ProductionReadinessEvidenceV2.model_validate(evidence),
    )


def _create_package(
    session: Session,
    scope: _Phase3Scope,
    *,
    evidence_updates: dict | None = None,
):
    return ProductionPackageService(
        session,
        allow_legacy_envelope_free_write=True,
    ).create_package(
        ProductionPackageCreateV2(
            content=_content(
                session,
                scope,
                evidence_updates=evidence_updates,
            ),
            created_by_user_id=scope.operator.id,
        )
    )


def test_channel_scoped_duration_is_exact_and_missing_authority_blocks(
    db_session: Session,
) -> None:
    first = _scope(
        db_session,
        minimum_ms=180_000,
        target_ms=240_000,
        maximum_ms=300_000,
    )
    second = _scope(
        db_session,
        minimum_ms=480_000,
        target_ms=540_000,
        maximum_ms=660_000,
    )
    resolver = ChannelDurationContractResolver(db_session)
    first_contract = resolver.resolve(
        profile_version_id=first.profile.id,
        policy_snapshot_id=first.policy.id,
    )
    second_contract = resolver.resolve(
        profile_version_id=second.profile.id,
        policy_snapshot_id=second.policy.id,
    )
    assert first_contract.model_dump(mode="json") == first.duration.model_dump(
        mode="json"
    )
    assert second_contract.model_dump(mode="json") == second.duration.model_dump(
        mode="json"
    )
    assert (
        first_contract.duration_contract_hash != second_contract.duration_contract_hash
    )

    with pytest.raises(ValidationFailureError, match="DURATION_CONTRACT_MISSING"):
        _scope(db_session, duration_in_authority=False)


def test_canonical_hash_and_automated_readiness_receipt_are_exact(
    db_session: Session,
) -> None:
    scope = _scope(db_session)
    before_approvals = db_session.scalar(
        select(func.count()).select_from(ApprovalDecision)
    )
    package_service = ProductionPackageService(
        db_session,
        allow_legacy_envelope_free_write=True,
    )
    package_request = ProductionPackageCreateV2(
        content=_content(db_session, scope),
        created_by_user_id=scope.operator.id,
    )
    package = package_service.create_package(package_request)
    same = package_service.create_package(package_request)
    assert same.artifact_version_id == package.artifact_version_id
    assert same.canonical_hash == package.canonical_hash
    assert package.content.duration_contract.model_dump(
        mode="json"
    ) == scope.duration.model_dump(mode="json")

    evaluation = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert evaluation.status == "READY_FOR_PRODUCTION"
    assert evaluation.receipt is not None
    assert len(evaluation.gate_run_ids) == len(REQUIRED_PRODUCTION_GATE_KEYS)
    assert db_session.scalar(
        select(func.count())
        .select_from(GateRun)
        .where(GateRun.target_id == package.artifact_version_id)
    ) == len(REQUIRED_PRODUCTION_GATE_KEYS)
    assert db_session.scalar(select(func.count()).select_from(ReviewTask)) == 0
    assert (
        db_session.scalar(select(func.count()).select_from(ApprovalDecision))
        == before_approvals
    )

    receipt = evaluation.receipt.content
    assert receipt.production_package_artifact_version_id == package.artifact_version_id
    assert receipt.production_package_hash == package.canonical_hash
    assert receipt.project_admission_decision_hash == scope.admission.decision_hash
    assert receipt.channel_profile_hash == scope.profile.profile_input_hash
    assert receipt.compiled_policy_snapshot_hash == scope.policy.content_hash
    assert receipt.duration_contract_hash == scope.duration.duration_contract_hash
    assert {item.gate_key for item in receipt.required_gate_runs} == set(
        REQUIRED_PRODUCTION_GATE_KEYS
    )
    assert all(item.gate_run_hash for item in receipt.required_gate_runs)


def test_new_v2_package_write_requires_frozen_support_envelope(
    db_session: Session,
) -> None:
    scope = _scope(db_session)
    request = ProductionPackageCreateV2(
        content=_content(db_session, scope),
        created_by_user_id=scope.operator.id,
    )

    with pytest.raises(
        ValidationFailureError,
        match="PRODUCTION_PACKAGE_SUPPORT_ENVELOPE_REQUIRED",
    ):
        ProductionPackageService(db_session).create_package(request)


def test_authority_types_and_revision_entrypoints_are_fail_closed(
    db_session: Session,
) -> None:
    scope = _scope(db_session)
    artifacts = ArtifactService(db_session)
    with pytest.raises(
        ValidationFailureError,
        match="AUTHORITY_ARTIFACT_DOMAIN_SERVICE_REQUIRED",
    ):
        artifacts.create_artifact(
            data=ArtifactCreate(
                video_project_id=scope.project.id,
                artifact_type="production_package",
                status="approved",
                created_by_user_id=scope.operator.id,
            )
        )

    service = ProductionPackageService(
        db_session,
        allow_legacy_envelope_free_write=True,
    )
    content = _content(db_session, scope)
    forged_initial_revision = content.model_copy(
        update={
            "revision": ProductionRevisionV2(
                parent_package_artifact_version_id=uuid.uuid4(),
                parent_package_hash="0" * 64,
                materiality=(
                    ProductionPackageMateriality.NON_MATERIAL_TECHNICAL_REPAIR
                ),
                affected_gate_keys=["production_visual_metadata_gate"],
                policy_authorized_repair=True,
                revision_reason_codes=["FORGED_INITIAL_PARENT"],
            )
        }
    )
    with pytest.raises(
        ValidationFailureError,
        match="PRODUCTION_PACKAGE_INITIAL_REVISION_FORBIDDEN",
    ):
        service.create_package(
            ProductionPackageCreateV2(
                content=forged_initial_revision,
                created_by_user_id=scope.operator.id,
            )
        )

    original = service.create_package(
        ProductionPackageCreateV2(
            content=content,
            created_by_user_id=scope.operator.id,
        )
    )
    embedded_revision = original.content.model_copy(
        update={
            "revision": ProductionRevisionV2(
                parent_package_artifact_version_id=original.artifact_version_id,
                parent_package_hash=original.canonical_hash,
                materiality=(
                    ProductionPackageMateriality.NON_MATERIAL_TECHNICAL_REPAIR
                ),
                affected_gate_keys=["production_visual_metadata_gate"],
                policy_authorized_repair=True,
                revision_reason_codes=["DIRECT_REVISION_BYPASS"],
            )
        }
    )
    with pytest.raises(
        ValidationFailureError,
        match="PRODUCTION_PACKAGE_REVISION_SERVICE_REQUIRED",
    ):
        service.create_package(
            ProductionPackageCreateV2(
                content=embedded_revision,
                created_by_user_id=scope.operator.id,
            )
        )
    with pytest.raises(
        ValidationFailureError,
        match="AUTHORITY_ARTIFACT_DOMAIN_SERVICE_REQUIRED",
    ):
        artifacts.create_artifact(
            data=ArtifactCreate(
                video_project_id=scope.project.id,
                artifact_type="production_readiness_receipt",
                status="approved",
                created_by_user_id=scope.operator.id,
            )
        )


def test_invalid_exact_refs_cannot_poison_canonical_package_slot(
    db_session: Session,
) -> None:
    scope = _scope(db_session)
    content = _content(db_session, scope)
    invalid_script_ref = content.script_ref.model_copy(
        update={"content_hash": "f" * 64}
    )

    with pytest.raises(
        ValidationFailureError,
        match="PRODUCTION_PACKAGE_ARTIFACT_REF_MISMATCH:script_ref",
    ):
        ProductionPackageService(
            db_session,
            allow_legacy_envelope_free_write=True,
        ).create_package(
            ProductionPackageCreateV2(
                content=content.model_copy(update={"script_ref": invalid_script_ref}),
                created_by_user_id=scope.operator.id,
            )
        )

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.video_project_id == scope.project.id)
            .where(Artifact.artifact_type == "production_package")
        )
        == 0
    )


def test_public_support_artifact_cannot_self_attest_readiness(
    db_session: Session,
) -> None:
    scope = _scope(db_session)
    artifacts = ArtifactService(db_session)
    with pytest.raises(
        ValidationFailureError,
        match="V2_READINESS_ARTIFACT_DOMAIN_SERVICE_REQUIRED",
    ):
        artifacts.create_artifact(
            data=ArtifactCreate(
                video_project_id=scope.project.id,
                artifact_type="niche_alignment_dossier",
                status="approved",
                created_by_user_id=scope.operator.id,
            ),
            public_write=True,
        )
    assert db_session.scalar(select(func.count()).select_from(GateRun)) == 0


def test_v2_long_production_resolves_only_current_ready_package(
    db_session: Session,
    tmp_path: Path,
) -> None:
    scope = _scope(db_session)
    package = _create_package(db_session, scope)
    evaluation = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert evaluation.status == "READY_FOR_PRODUCTION"
    orchestrator = LongProductionOrchestrator(
        db_session,
        workspace_root=tmp_path,
        ffmpeg="/bin/true",
        ffprobe="/bin/true",
    )
    authority, actor_id = orchestrator._authority_from_db(
        scope.project.id,
        None,
    )
    assert authority.production_package_schema_version == "v2"
    assert authority.package_id == str(package.artifact_version_id)
    assert actor_id == scope.operator.id

    legacy = FirstScriptedVideoPackage(
        video_project_id=scope.project.id,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.policy.id,
        effective_context_snapshot_id=scope.effective.id,
        effective_context_hash=scope.effective.context_hash,
        package_status="READY_FOR_HUMAN_REVIEW",
        agent_run_refs=[],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts={},
        limitations=[],
        risk_limitations_summary={},
    )
    db_session.add(legacy)
    db_session.flush()
    with pytest.raises(
        ValidationFailureError,
        match="PRODUCTION_PACKAGE_V2_PROJECTION_VERSION_MISMATCH",
    ):
        orchestrator._authority_from_db(scope.project.id, legacy.id)


def test_mr1_v2_admission_accepts_automated_receipt_validation_only(
    db_session: Session,
    tmp_path: Path,
) -> None:
    scope = _scope(
        db_session,
        minimum_ms=480_000,
        target_ms=540_000,
        maximum_ms=660_000,
    )
    package = _create_package(db_session, scope)
    evaluation = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert evaluation.status == "READY_FOR_PRODUCTION"
    before_approvals = db_session.scalar(
        select(func.count()).select_from(ApprovalDecision)
    )
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path,
    )
    validation = service.validate_v2_automated_admission(
        MR1V2AutomatedAdmissionRequest(
            project_id=scope.project.id,
            production_package_artifact_version_id=(package.artifact_version_id),
        )
    )
    assert validation.status == "VALIDATED"
    assert validation.legacy_approval_required is False
    assert validation.execution_authorized is False
    assert validation.duration_contract.model_dump(
        mode="json"
    ) == scope.duration.model_dump(mode="json")
    bounds = service._approved_duration_bounds(
        {"duration_contract": validation.duration_contract.model_dump(mode="json")}
    )
    assert bounds["minimum_duration_ms"] == 480_000
    assert bounds["maximum_duration_ms"] == 660_000
    assert bounds["limit_source"] == "CHANNEL_DURATION_CONTRACT_V2"

    with pytest.raises(
        ValidationFailureError,
        match="MR1_V2_REAL_EXECUTION_DISABLED",
    ):
        service.start(
            MR1StartCommand(
                approval_id=uuid.uuid4(),
                approval_content_hash="0" * 64,
                project_id=scope.project.id,
                package_artifact_version_id=package.artifact_version_id,
            ),
            gateways=MR1ProviderGateways(
                narration=None,
                alignment=None,
                pexels=None,
                drive=None,
            ),
        )
    assert (
        db_session.scalar(select(func.count()).select_from(ApprovalDecision))
        == before_approvals
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.video_project_id == scope.project.id)
            .where(Artifact.artifact_type == "mr1_execution_run")
        )
        == 0
    )


@pytest.mark.parametrize(
    ("updates", "reason_code"),
    [
        (
            {"unresolved_exception_types": ["RIGHTS"]},
            "RIGHTS_EXCEPTION_BLOCKED",
        ),
        (
            {"unresolved_exception_types": ["EVIDENCE"]},
            "EVIDENCE_EXCEPTION_BLOCKED",
        ),
        (
            {
                "editorial_depth_sufficient": False,
                "supported_claim_count": 1,
                "distinct_editorial_section_count": 1,
                "research_coverage_ratio": 0.2,
            },
            "BLOCK_INSUFFICIENT_EDITORIAL_DEPTH",
        ),
        (
            {"script_duration_ms": 60_000},
            "SCRIPT_DURATION_OUTSIDE_CHANNEL_CONTRACT",
        ),
    ],
)
def test_readiness_exceptions_depth_and_duration_fail_closed(
    db_session: Session,
    updates: dict,
    reason_code: str,
) -> None:
    scope = _scope(db_session)
    package = _create_package(db_session, scope, evidence_updates=updates)
    evaluation = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert evaluation.status == "BLOCKED"
    assert evaluation.receipt is None
    assert reason_code in evaluation.blocker_reason_codes
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.video_project_id == scope.project.id)
            .where(Artifact.artifact_type == "production_readiness_receipt")
        )
        == 0
    )


def test_materiality_revision_hashes_and_automated_reruns(
    db_session: Session,
) -> None:
    scope = _scope(db_session)
    service = ProductionPackageService(
        db_session,
        allow_legacy_envelope_free_write=True,
    )
    original = _create_package(db_session, scope)
    ready = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=original.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert ready.status == "READY_FOR_PRODUCTION"

    old_metadata_version = db_session.get(
        ArtifactVersion,
        original.content.metadata_ref.artifact_version_id,
    )
    assert old_metadata_version is not None
    repaired_metadata_version = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=old_metadata_version.artifact_id,
            parent_version_id=old_metadata_version.id,
            content=old_metadata_version.content,
            status="approved",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id="phase3-non-material-metadata-rebind",
        trusted_authority_write=True,
    )
    repaired = service.revise_package(
        ProductionPackageRevisionRequestV2(
            package_artifact_version_id=original.artifact_version_id,
            materiality=ProductionPackageMateriality.NON_MATERIAL_TECHNICAL_REPAIR,
            affected_gate_keys=["production_visual_metadata_gate"],
            revision_reason_codes=["METADATA_TECHNICAL_REPAIR"],
            policy_authorized_repair=True,
            content_updates={
                "metadata_ref": {
                    **original.content.metadata_ref.model_dump(mode="json"),
                    "artifact_version_id": str(repaired_metadata_version.id),
                    "version": repaired_metadata_version.version_number,
                    "content_hash": repaired_metadata_version.content_hash,
                }
            },
            created_by_user_id=scope.operator.id,
        )
    )
    assert repaired.parent_version_id == original.artifact_version_id
    assert repaired.canonical_hash != original.canonical_hash
    rerun = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=repaired.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert rerun.status == "READY_FOR_PRODUCTION"
    assert len(rerun.gate_run_ids) == len(REQUIRED_PRODUCTION_GATE_KEYS)
    with pytest.raises(
        ValidationFailureError,
        match="PRODUCTION_PACKAGE_REVISION_PARENT_NOT_CURRENT",
    ):
        service.revise_package(
            ProductionPackageRevisionRequestV2(
                package_artifact_version_id=original.artifact_version_id,
                materiality=(
                    ProductionPackageMateriality.NON_MATERIAL_TECHNICAL_REPAIR
                ),
                affected_gate_keys=["production_visual_metadata_gate"],
                revision_reason_codes=["STALE_PARENT_RETRY"],
                policy_authorized_repair=True,
                content_updates={
                    "metadata_ref": repaired.content.metadata_ref.model_dump(
                        mode="json"
                    )
                },
                created_by_user_id=scope.operator.id,
            )
        )

    old_script_version = db_session.get(
        ArtifactVersion,
        repaired.content.script_ref.artifact_version_id,
    )
    assert old_script_version is not None
    changed_script_version = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=old_script_version.artifact_id,
            parent_version_id=old_script_version.id,
            content={
                **old_script_version.content,
                "editorial_change": "material research-backed rewrite",
            },
            status="approved",
            created_by_user_id=scope.operator.id,
        ),
        correlation_id="phase3-material-script-revision",
        trusted_authority_write=True,
    )
    material = service.revise_package(
        ProductionPackageRevisionRequestV2(
            package_artifact_version_id=repaired.artifact_version_id,
            materiality=ProductionPackageMateriality.MATERIAL_EDITORIAL_CHANGE,
            affected_gate_keys=["production_script_integrity_gate"],
            revision_reason_codes=["SCRIPT_EDITORIAL_CHANGE"],
            policy_authorized_repair=False,
            new_planning_cycle=True,
            content_updates={
                "script_ref": {
                    **repaired.content.script_ref.model_dump(mode="json"),
                    "artifact_version_id": str(changed_script_version.id),
                    "version": changed_script_version.version_number,
                    "content_hash": changed_script_version.content_hash,
                }
            },
            created_by_user_id=scope.operator.id,
        )
    )
    assert material.canonical_hash != repaired.canonical_hash
    blocked = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=material.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert blocked.status == "BLOCKED"
    assert "MATERIAL_CHANGE_REQUIRES_NEW_PLANNING_CYCLE" in blocked.blocker_reason_codes


def test_padding_is_neutralized_without_fabricating_depth() -> None:
    script = {
        "hook_spec": {"promise_made": "Research-backed promise"},
        "sentences": [
            {"sentence_id": "s1", "text": "Only supported depth is allowed."}
        ],
    }
    repaired, patches = _expand_script_to_word_budget(
        script,
        {"words_per_minute_assumption": 140},
        {
            "minimum_word_count": 400,
            "target_word_count": 500,
            "maximum_word_count": 600,
        },
    )
    assert repaired == script
    assert repaired is not script
    assert patches == []


def test_v2_local_renderer_checks_readiness_before_side_effects() -> None:
    source = inspect.getsource(LocalFixtureRendererService.render_local_smoke)
    readiness = source.index("require_ready_projection_authority")
    job_create = source.index("job = MediaRenderJob")
    ffmpeg_execute = source.index("subprocess.run")

    assert readiness < job_create < ffmpeg_execute
