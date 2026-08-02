from __future__ import annotations

import runpy
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.geo_market import DestinationBinding
from app.contracts.production_workflow import ProductionWorkflowProjectStart
from app.contracts.vcos_v2 import (
    AssignmentMode,
    LongFormPlanningRequest,
    ProductionLane,
)
from app.core.actor import authenticated_actor_context
from app.db.models.foundation import DomainEvent
from app.db.models.m5 import EditorialCalendarSlot, IdeaMarketPreflight
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.workflow import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ReviewTask,
    VideoProject,
)
from app.services.context_resolver import (
    EffectiveChannelRuntimeContextCompiler,
)
from app.services.production_workflow import ProductionWorkflowCoordinator
from app.services.v2_package_readiness import (
    CanonicalV2SupportCompiler,
    build_v2_package_readiness_gateway,
)
from app.services.v2_support_authority import (
    V2SupportAuthorityPrepareCommand,
    V2SupportAuthorityService,
)
from app.services.vcos_v2 import LongFormPlanningService
from app.workers.production_workflow import ProductionWorkflowWorker


ROOT = Path(__file__).resolve().parents[1]


def _approved_script() -> str:
    return " ".join(
        (
            f"Evidence statement {index} explains an approved mechanism "
            "using source-bound facts, measurable outcomes, and exact "
            "operator context."
        )
        for index in range(1, 61)
    )


def _configure_frozen_support_authority(
    session: Session,
    scope: object,
) -> None:
    binding = DestinationBinding(
        binding_version=1,
        channel_id=scope.channel.id,
        channel_key=scope.channel.key,
        platform="YOUTUBE",
        platform_account_ref="youtube-account://phase4-local",
        platform_channel_id="UC_PHASE4_LOCAL",
        channel_handle="@phase4-local",
        target_market_profile_ref="target-market-profile://phase4/v1",
        target_market_profile_hash="d" * 64,
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="VERIFIED",
        credential_ref="credential://phase4/local",
        verification_state="VERIFIED",
        verification_timestamp="2026-07-29T00:00:00+00:00",
        approval_ref="operator-approval://phase4/destination",
    ).model_dump(mode="json")
    scope.channel.metadata_ = {
        **(scope.channel.metadata_ or {}),
        "destination_governance": {
            "active_binding_ref": (f"destination-binding://{scope.channel.key}/v1"),
            "bindings": [binding],
        },
    }
    session.flush()


def _new_long_scope_with_approved_script(
    session: Session,
    base: object,
    *,
    include_frozen_script: bool = True,
) -> object:
    script = _approved_script()
    slot = EditorialCalendarSlot(
        company_id=base.company.id,
        channel_workspace_id=base.channel.id,
        policy_snapshot_id=base.policy.id,
        slot_date=date(2026, 7, 29),
        slot_type="CAMPAIGN",
        status="OPEN",
        schema_version="v2",
        production_lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
        category_id=base.project.category_id,
        production_goal="Compile exact approved evidence without padding",
        target_platforms=["YOUTUBE"],
        risk_level="LOW",
        operational_envelope={},
        created_by_user_id=base.operator.id,
    )
    session.add(slot)
    session.flush()
    evidence_blob = {
        "authority": "strict-preflight",
        "evidence_complete": True,
        "niche_contract_digest_hash": "a" * 64,
        "target_market_digest_hash": "b" * 64,
    }
    if include_frozen_script:
        evidence_blob["approved_script"] = script
    preflight = IdeaMarketPreflight(
        company_id=base.company.id,
        channel_workspace_id=base.channel.id,
        editorial_calendar_slot_id=slot.id,
        policy_fit_state="PASS",
        confidence_state="HIGH",
        evidence_blob=evidence_blob,
        niche_contract_digest_hash="a" * 64,
        target_market_digest_hash="b" * 64,
        reason_codes=["STRICT_EVIDENCE_COMPLETE"],
        decision="PASS",
    )
    session.add(preflight)
    session.flush()
    admission = LongFormPlanningService(session).admit(
        LongFormPlanningRequest(
            company_id=base.company.id,
            channel_workspace_id=base.channel.id,
            channel_profile_version_id=base.profile.id,
            policy_snapshot_id=base.policy.id,
            editorial_calendar_slot_id=slot.id,
            idea_market_preflight_id=preflight.id,
            assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
            title="Phase 4 support compiler",
            description=script,
            category_id=base.project.category_id,
            niche_gate_passed=True,
            market_gate_passed=True,
            evidence_refs=[
                {
                    "type": "idea_market_preflight",
                    "id": str(preflight.id),
                }
            ],
            duration_contract=base.duration,
            created_by_user_id=base.operator.id,
        )
    )
    assert admission.admitted_video_project_id is not None, admission.reason_codes
    project = session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    effective = EffectiveChannelRuntimeContextCompiler(session).ensure_for_project(
        project.id,
        editorial_calendar_slot_id=slot.id,
    )
    assert effective.compile_status == "PASS"
    # The active run is required while admission freezes its strategic lineage.
    # Pause it before this fixture exercises a workflow worker so an unrelated
    # cadence scan cannot consume one of the deterministic workflow events.
    base.pause_active_launch_run(session)
    return SimpleNamespace(
        company=base.company,
        operator=base.operator,
        channel=base.channel,
        profile=base.profile,
        policy=base.policy,
        duration=base.duration,
        admission=admission,
        project=project,
        effective=effective,
    )


def _prepare_frozen_support_envelope(
    session: Session,
    scope: object,
    *,
    idempotency_key: str,
):
    support_module = runpy.run_path(
        str(ROOT / "tests/test_phase4_v2_support_authority.py")
    )
    producer = support_module["_FakeTrustedProducer"]()
    assert scope.admission.editorial_calendar_slot_id is not None
    result = V2SupportAuthorityService(
        session,
        producer=producer,
    ).prepare(
        V2SupportAuthorityPrepareCommand(
            video_project_id=scope.project.id,
            source_type="LONG_FORM_PLAN",
            source_id=scope.admission.editorial_calendar_slot_id,
            actor_user_id=scope.operator.id,
            idempotency_key=idempotency_key,
            max_budget_usd="0",
        )
    )
    assert producer.calls == 1
    return result


def test_default_support_compiler_progresses_new_v2_project_to_readiness(
    db_session: Session,
    engine,
) -> None:
    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    base = phase3["_scope"](db_session)
    scope = _new_long_scope_with_approved_script(db_session, base)
    _configure_frozen_support_authority(db_session, scope)
    prepared = _prepare_frozen_support_envelope(
        db_session,
        scope,
        idempotency_key="phase4-default-support-envelope",
    )
    assert (
        db_session.scalar(
            select(Artifact).where(
                Artifact.video_project_id == scope.project.id,
                Artifact.artifact_type == "production_package",
            )
        )
        is None
    )
    actor = authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="OWNER_ADMIN",
        permissions={
            "production.start",
            "production.cancel",
            "production.read",
            "ops.manage",
        },
    )
    started = ProductionWorkflowCoordinator(db_session).start_from_project(
        video_project_id=scope.project.id,
        company_id=scope.company.id,
        data=ProductionWorkflowProjectStart(
            idempotency_key="phase4-default-support-compiler"
        ),
        actor=actor,
    )
    db_session.commit()
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id="phase4-default-support-worker",
    )

    for index in range(6):
        result = worker.run_once()
        if result.status != "DELIVERED":
            with factory() as failed:
                event = failed.get(DomainEvent, result.event_id)
                failed_run = failed.get(ProductionWorkflowRun, started.id)
                diagnostic = (
                    (
                        event.last_error_code,
                        event.last_error_summary,
                        failed_run.current_stage if failed_run is not None else None,
                        (
                            failed_run.state_reason_codes
                            if failed_run is not None
                            else None
                        ),
                    )
                    if event is not None
                    else None
                )
            assert result.status == "DELIVERED", (index, diagnostic)

    with factory() as check:
        run = check.get(ProductionWorkflowRun, started.id)
        assert run is not None
        assert run.current_stage == "MEDIA"
        assert run.production_package_artifact_version_id is not None
        assert run.production_package_hash is not None
        assert run.production_readiness_receipt_artifact_version_id is not None
        assert run.production_readiness_receipt_hash is not None
        package_version = check.get(
            ArtifactVersion,
            run.production_package_artifact_version_id,
        )
        receipt_version = check.get(
            ArtifactVersion,
            run.production_readiness_receipt_artifact_version_id,
        )
        assert package_version is not None
        assert receipt_version is not None
        package_content = package_version.content
        provider_plan_version = check.get(
            ArtifactVersion,
            uuid.UUID(
                package_content["provider_execution_plan_ref"]["artifact_version_id"]
            ),
        )
        budget_scope_version = check.get(
            ArtifactVersion,
            uuid.UUID(package_content["budget_scope_ref"]["artifact_version_id"]),
        )
        assert provider_plan_version is not None
        assert budget_scope_version is not None
        provider_plan = provider_plan_version.content
        budget_scope = budget_scope_version.content
        assert provider_plan["execution_authorized"] is True
        assert provider_plan["paid_provider_calls"] is False
        assert budget_scope["budget_authorized"] is True
        assert all(
            operation["execution_authorized"] is True
            and operation["paid_provider_call"] is False
            and operation["max_cost_usd"] == "0"
            for operation in provider_plan["adapter_operations"].values()
        )
        assert all(
            authorization["authorized"] is True
            and authorization["paid_provider_call"] is False
            and authorization["max_cost_usd"] == "0"
            for authorization in budget_scope["operation_authorizations"].values()
        )
        assert (
            list(
                check.scalars(
                    select(ReviewTask).where(
                        ReviewTask.video_project_id == scope.project.id
                    )
                )
            )
            == []
        )
        assert (
            list(
                check.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id.in_(
                            [
                                package_version.id,
                                receipt_version.id,
                                provider_plan_version.id,
                                budget_scope_version.id,
                            ]
                        )
                    )
                )
            )
            == []
        )
        expected_envelope_ref = {
            "artifact_version_id": str(prepared.artifact_version_id),
            "content_hash": prepared.envelope_hash,
        }
        package_envelope_ref = package_version.content["support_envelope_ref"]
        receipt_envelope_ref = receipt_version.content["support_envelope_ref"]
        assert {
            "artifact_version_id": package_envelope_ref["artifact_version_id"],
            "content_hash": package_envelope_ref["content_hash"],
        } == expected_envelope_ref
        assert receipt_envelope_ref == package_envelope_ref
        support_types = set(
            check.scalars(
                select(Artifact.artifact_type).where(
                    Artifact.video_project_id == scope.project.id
                )
            )
        )
        assert {
            "research_pack",
            "source_pack",
            "script",
            "visual_plan",
            "provider_execution_plan",
            "cost_estimate_snapshot",
            "destination_binding",
            "v2_frozen_support_envelope",
            "production_package",
            "production_readiness_receipt",
        }.issubset(support_types)


def test_default_pre_readiness_gateway_has_substantive_domain_compiler() -> None:
    gateway = build_v2_package_readiness_gateway()

    assert isinstance(gateway.support_producer, CanonicalV2SupportCompiler)
    assert gateway.package_builder is gateway.support_producer


def test_public_description_cannot_masquerade_as_frozen_approved_script(
    db_session: Session,
    engine,
) -> None:
    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    base = phase3["_scope"](db_session)
    scope = _new_long_scope_with_approved_script(
        db_session,
        base,
        include_frozen_script=False,
    )
    _configure_frozen_support_authority(db_session, scope)
    actor = authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="OWNER_ADMIN",
        permissions={
            "production.start",
            "production.cancel",
            "production.read",
            "ops.manage",
        },
    )
    started = ProductionWorkflowCoordinator(db_session).start_from_project(
        video_project_id=scope.project.id,
        company_id=scope.company.id,
        data=ProductionWorkflowProjectStart(
            idempotency_key="phase4-description-is-not-script"
        ),
        actor=actor,
    )
    db_session.commit()
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id="phase4-description-trust-boundary-worker",
    )

    assert worker.run_once().status == "DELIVERED"
    assert worker.run_once().status == "DELIVERED"
    assert worker.run_once().status == "DELIVERED"
    assert worker.run_once().status == "DEAD_LETTERED"

    with factory() as check:
        run = check.get(ProductionWorkflowRun, started.id)
        assert run is not None
        assert run.state == "BLOCKED"
        failed_event = check.scalar(
            select(DomainEvent)
            .where(
                DomainEvent.workflow_run_id == started.id,
                DomainEvent.dead_lettered_at.is_not(None),
            )
            .order_by(DomainEvent.created_at.desc())
        )
        assert failed_event is not None
        assert failed_event.last_error_code == "V2_FROZEN_SUPPORT_ENVELOPE_REQUIRED"
        support_count = check.scalar(
            select(Artifact)
            .where(
                Artifact.video_project_id == scope.project.id,
                Artifact.artifact_type.in_(
                    [
                        "research_pack",
                        "source_pack",
                        "script",
                        "visual_plan",
                        "production_package",
                    ]
                ),
            )
            .with_only_columns(Artifact.id)
            .limit(1)
        )
        assert support_count is None
