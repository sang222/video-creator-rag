from __future__ import annotations

import runpy
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.operator_planning import (
    _as_operator_planning_http_error,
)
from app.contracts.geo_market import DestinationBinding
from app.contracts.m5 import SearchDemandEvidenceCreate
from app.contracts.operator_planning import (
    DailyShortPlanningLaunchRequest,
    LongFormPlanningLaunchRequest,
    OperatorPlanningPrepareRequest,
    OperatorPlanningStartRequest,
)
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.vcos_v2 import (
    AssignmentMode,
    ContentMode,
    ProductionLane,
)
from app.core.actor import authenticated_actor_context
from app.core.errors import ForbiddenError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import LLMRunSnapshot
from app.db.models.m5 import (
    ChannelDailyRun,
    ContextPackSnapshot,
    DailyIdeaDecision,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    RetrievalPlanSnapshot,
)
from app.db.models.ops import ProviderAttempt
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.workflow import Artifact, VideoProject
from app.main import create_app
from app.services.m5 import LLMWorkflowResult, SearchDemandEvidenceService
from app.services.operator_planning import OperatorPlanningService
from app.services.production_package import semantic_hash
from app.services.r3d1 import R3D1AdminService
from app.services.security_boundary import (
    permission_for_route,
    uncovered_protected_routes,
)
from app.services.v2_support_authority import LLMRouterV2SupportProducer


ROOT = Path(__file__).resolve().parents[1]
PHASE2 = runpy.run_path(str(ROOT / "tests/test_phase2_typed_admission.py"))
SUPPORT = runpy.run_path(str(ROOT / "tests/test_phase4_v2_support_authority.py"))
FakeTrustedProducer = SUPPORT["_FakeTrustedProducer"]


def _actor(authority: Any):
    return authenticated_actor_context(
        canonical_user_id=authority.operator.id,
        operator_user_id=authority.operator.id,
        actor_role="OPERATOR",
        permissions={
            "production.read",
            "production.start",
            "editorial.manage",
        },
    )


def _active_category(session: Session, authority: Any):
    return R3D1AdminService(session).create_content_category(
        ContentCategoryCreate(
            company_id=authority.company.id,
            channel_workspace_id=authority.channel.id,
            category_key=f"planning-{uuid.uuid4().hex[:8]}",
            name="Danh mục launcher",
            sub_niche="typed v2 planning",
            audience_segment="operator",
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


def _configure_verified_destination(
    session: Session,
    authority: Any,
) -> str:
    binding_ref = f"destination-binding://{authority.channel.key}/v1"
    binding = DestinationBinding(
        binding_version=1,
        channel_id=authority.channel.id,
        channel_key=authority.channel.key,
        platform="YOUTUBE",
        platform_account_ref="youtube-account://planning-local",
        platform_channel_id="UC_PLANNING_LOCAL",
        channel_handle="@planning-local",
        target_market_profile_ref="target-market-profile://planning/v1",
        target_market_profile_hash="d" * 64,
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="VERIFIED",
        credential_ref="credential://planning/local",
        verification_state="VERIFIED",
        verification_timestamp=datetime(2026, 7, 29, tzinfo=UTC),
        approval_ref="operator-approval://planning/destination",
    )
    authority.channel.metadata_ = {
        **(authority.channel.metadata_ or {}),
        "destination_governance": {
            "active_binding_ref": binding_ref,
            "bindings": [binding.model_dump(mode="json")],
        },
    }
    session.flush()
    return binding_ref


def _persisted_evidence(session: Session, authority: Any):
    return SearchDemandEvidenceService(session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=authority.company.id,
            channel_workspace_id=authority.channel.id,
            evidence_source_type="MANUAL_RESEARCH",
            query="safe local video production workflow",
            platform="YOUTUBE",
            search_volume_30d=800,
            relative_interest_index=Decimal("70"),
            competition_index=Decimal("0.30"),
            evidence_confidence="HIGH",
        ),
        correlation_id="operator-planning-test-evidence",
    )


def _daily_source(
    session: Session,
    *,
    with_preflight: bool = True,
    with_evidence: bool = True,
):
    authority = PHASE2["_authority"](session)
    category = _active_category(session, authority)
    slot = PHASE2["_slot"](
        session,
        authority,
        lane=ProductionLane.DAILY_SHORT,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
    )
    slot.category_id = category.id
    slot.production_goal = "Ba bước kiểm tra nhanh"
    _configure_verified_destination(session, authority)
    evidence = _persisted_evidence(session, authority) if with_evidence else None
    daily_run = ChannelDailyRun(
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        policy_snapshot_id=authority.policy.id,
        editorial_calendar_slot_id=slot.id,
        run_date=slot.slot_date,
        status="COMPLETED",
        run_mode="REAL",
        trigger_type="MANUAL",
        reason_codes=["DAILY_RUN_COMPLETED"],
        metadata_={"authority_source": "test"},
        completed_at=utc_now(),
    )
    session.add(daily_run)
    session.flush()
    retrieval = RetrievalPlanSnapshot(
        purpose="DAILY_IDEA",
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        channel_profile_version_id=authority.profile.id,
        policy_snapshot_id=authority.policy.id,
        editorial_calendar_slot_id=slot.id,
        allowed_sources=["policy_snapshot"],
        excluded_sources=[],
        redaction_rules={},
        source_order=["policy_snapshot"],
        plan_hash=semantic_hash({"slot_id": str(slot.id)}),
        created_by_user_id=authority.operator.id,
    )
    session.add(retrieval)
    session.flush()
    context = ContextPackSnapshot(
        retrieval_plan_snapshot_id=retrieval.id,
        purpose="DAILY_IDEA",
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        channel_profile_version_id=authority.profile.id,
        policy_snapshot_id=authority.policy.id,
        editorial_calendar_slot_id=slot.id,
        input_refs=[],
        policy_refs=[],
        evidence_refs=(
            [{"type": "search_demand_evidence", "id": str(evidence.id)}]
            if evidence is not None
            else []
        ),
        metric_refs=[],
        memory_refs=[],
        pack_content={},
        freshness_state="FRESH",
        confidence_level="HIGH",
        pack_hash=semantic_hash({"retrieval_plan_id": str(retrieval.id)}),
        created_by_user_id=authority.operator.id,
    )
    session.add(context)
    session.flush()
    idea = DailyIdeaDecision(
        channel_daily_run_id=daily_run.id,
        company_id=authority.company.id,
        channel_workspace_id=authority.channel.id,
        policy_snapshot_id=authority.policy.id,
        context_pack_snapshot_id=context.id,
        schema_version="v2",
        production_lane=ProductionLane.DAILY_SHORT,
        proposed_content_mode=ContentMode.STANDALONE,
        assignment_input_ref={
            "editorial_calendar_slot_id": str(slot.id),
            "policy_snapshot_id": str(authority.policy.id),
            "production_lane": "DAILY_SHORT",
        },
        decision_status="PROPOSED",
        proposed_title="Ba bước kiểm tra nhanh",
        proposed_angle="Một hướng dẫn ngắn dựa trên evidence đã lưu.",
        proposed_format="explainer",
        proposed_pillar="education",
        proposed_series_key=None,
        rationale={"source": "trusted_daily_authority"},
        evidence_refs=context.evidence_refs,
        reason_codes=["DAILY_IDEA_PROPOSED"],
        confidence_level="HIGH",
    )
    session.add(idea)
    session.flush()
    daily_run.daily_idea_decision_id = idea.id
    preflight = None
    if with_preflight:
        preflight = IdeaMarketPreflight(
            company_id=authority.company.id,
            channel_workspace_id=authority.channel.id,
            editorial_calendar_slot_id=slot.id,
            channel_daily_run_id=daily_run.id,
            daily_idea_decision_id=idea.id,
            demand_score=Decimal("80"),
            channel_fit_score=Decimal("80"),
            policy_fit_state="PASS",
            confidence_state="HIGH",
            evidence_blob={
                "search_demand_evidence_ids": (
                    [str(evidence.id)] if evidence is not None else []
                ),
                "authority_source": "persisted-test-evidence",
            },
            reason_codes=["IDEA_ADMITTED"],
            decision="PASS",
        )
        session.add(preflight)
    session.flush()
    return authority, slot, daily_run, idea, preflight


def _long_source(
    session: Session,
    *,
    with_preflight: bool = True,
    with_evidence: bool = True,
):
    authority = PHASE2["_authority"](session)
    category = _active_category(session, authority)
    slot = PHASE2["_slot"](
        session,
        authority,
        lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
    )
    slot.category_id = category.id
    slot.production_goal = "Hướng dẫn vận hành video an toàn"
    _configure_verified_destination(session, authority)
    evidence = _persisted_evidence(session, authority) if with_evidence else None
    preflight = None
    if with_preflight:
        preflight = IdeaMarketPreflight(
            company_id=authority.company.id,
            channel_workspace_id=authority.channel.id,
            editorial_calendar_slot_id=slot.id,
            demand_score=Decimal("80"),
            channel_fit_score=Decimal("80"),
            policy_fit_state="PASS",
            confidence_state="HIGH",
            evidence_blob={
                "proposed_title": "Hướng dẫn vận hành video an toàn",
                "search_demand_evidence_ids": (
                    [str(evidence.id)] if evidence is not None else []
                ),
                "authority_source": "persisted-test-evidence",
            },
            reason_codes=["IDEA_ADMITTED"],
            decision="PASS",
        )
        session.add(preflight)
    session.flush()
    return authority, slot, preflight


class _FakeDailyLLMWorkflow:
    def __init__(self, session: Session):
        self.session = session
        self.calls = 0

    def run_authority(
        self,
        *,
        daily_run: ChannelDailyRun,
        context_pack: ContextPackSnapshot,
        correlation_id: str,
        **_: Any,
    ) -> LLMWorkflowResult:
        self.calls += 1
        proposal = {
            "proposed_title": "Daily Short từ lịch đã đóng băng",
            "proposed_angle": "Giải thích quy trình local bằng evidence persisted.",
            "proposed_format": "explainer",
            "proposed_pillar": "education",
            "proposed_series_key": None,
            "rationale": {"authority_source": "fake_llm_router_test"},
            "audience_problem": "Cần một quy trình dễ kiểm tra.",
            "search_intent_hypothesis": {"intent": "how-to"},
            "channel_fit_score": "80",
            "channel_fit_evidence": {"source": "compiled_policy"},
            "evidence_refs": list(context_pack.evidence_refs or []),
            "confidence": "HIGH",
        }
        input_payload = {
            "daily_run_id": str(daily_run.id),
            "context_pack_snapshot_id": str(context_pack.id),
        }
        llm_run = LLMRunSnapshot(
            run_type="DAILY_IDEA",
            provider="injected-test",
            model_name="injected-no-provider",
            provider_key="llm_router",
            model_key="injected-no-provider",
            run_mode="REAL",
            input_payload=input_payload,
            input_hash=semantic_hash(input_payload),
            output_payload=proposal,
            output_hash=semantic_hash(proposal),
            status="COMPLETED",
            estimated_cost=Decimal("0"),
            correlation_id=correlation_id,
            completed_at=utc_now(),
        )
        self.session.add(llm_run)
        self.session.flush()
        return LLMWorkflowResult(
            terminal_status="COMPLETED",
            reason_codes=["LLM_RUN_SNAPSHOT_CREATED"],
            llm_run=llm_run,
            proposal=proposal,
            provider_attempt=None,
            quota_event_id=None,
            cost_event_id=None,
            budget_gate_result={
                "decision": "PASS",
                "estimated_cost": "0",
            },
        )


def test_daily_one_action_seals_support_and_replays_atomically(
    db_session: Session,
) -> None:
    authority, slot, daily_run, idea, preflight = _daily_source(db_session)
    producer = FakeTrustedProducer()
    service = OperatorPlanningService(
        db_session,
        support_producer=producer,
    )
    actor = _actor(authority)

    catalog = service.catalog(actor=actor)
    option = next(
        item for item in catalog.daily_short_options if item.source_id == idea.id
    )
    assert option.source_type == "DAILY_IDEA"
    assert option.launchable is True
    assert option.status_label == "Sẵn sàng chuẩn bị và tạo dự án"
    assert option.technical_appendix["idea_market_preflight_id"] == preflight.id

    command = OperatorPlanningStartRequest(
        source_type="DAILY_IDEA",
        source_id=idea.id,
        idempotency_key="operator-daily-one-action",
    )
    first = service.prepare_and_launch(data=command, actor=actor)
    replay = service.prepare_and_launch(data=command, actor=actor)

    assert first.project_id == replay.project_id
    assert first.workflow_run_id == replay.workflow_run_id
    assert first.reused_admission is False
    assert replay.reused_admission is True
    assert replay.reused_workflow is True
    assert producer.calls == 1
    admission = db_session.get(ProjectAdmissionDecision, first.admission_id)
    project = db_session.get(VideoProject, first.project_id)
    assert admission is not None
    assert project is not None
    assert project.effective_context_snapshot_id is not None
    assert admission.daily_idea_decision_id == idea.id
    assert admission.editorial_calendar_slot_id == slot.id
    assert admission.channel_daily_run_id == daily_run.id
    assert admission.idea_market_preflight_id == preflight.id
    assert idea.decision_status == "PROPOSED"
    effective = db_session.get(
        EffectiveChannelRuntimeContextSnapshot,
        project.effective_context_snapshot_id,
    )
    assert effective is not None
    assert effective.compile_status == "PASS"
    assert first.technical_appendix["approved_script_word_count"] >= 24
    assert first.technical_appendix["media_provider_calls"] is False
    assert (
        db_session.scalar(
            select(func.count(ProductionWorkflowRun.id)).where(
                ProductionWorkflowRun.video_project_id == project.id
            )
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count(Artifact.id)).where(
                Artifact.artifact_type == "v2_frozen_support_envelope"
            )
        )
        == 1
    )
    assert db_session.scalar(select(func.count(ProviderAttempt.id))) == 0


def test_long_prepare_replay_then_start_uses_same_frozen_authority(
    db_session: Session,
) -> None:
    authority, slot, preflight = _long_source(db_session)
    producer = FakeTrustedProducer()
    service = OperatorPlanningService(
        db_session,
        support_producer=producer,
    )
    actor = _actor(authority)
    prepare_command = OperatorPlanningPrepareRequest(
        source_type="LONG_FORM_PLAN",
        source_id=slot.id,
    )

    first = service.prepare_source(data=prepare_command, actor=actor)
    replay = service.prepare_source(data=prepare_command, actor=actor)
    launched = service.prepare_and_launch(
        data=OperatorPlanningStartRequest(
            source_type="LONG_FORM_PLAN",
            source_id=slot.id,
            idempotency_key="operator-long-one-action",
        ),
        actor=actor,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.support_artifact_version_id == first.support_artifact_version_id
    assert producer.calls == 1
    assert launched.project_id == first.project_id
    assert launched.reused_admission is True
    admission = db_session.get(ProjectAdmissionDecision, launched.admission_id)
    project = db_session.get(VideoProject, launched.project_id)
    assert admission is not None
    assert project is not None
    assert admission.editorial_calendar_slot_id == slot.id
    assert admission.idea_market_preflight_id == preflight.id
    assert project.title == "Hướng dẫn vận hành video an toàn"
    assert launched.technical_appendix["support_authority_artifact_id"] == (
        first.support_artifact_id
    )
    assert db_session.scalar(select(func.count(ProviderAttempt.id))) == 0


def test_daily_slot_normal_flow_materializes_trusted_v2_source(
    db_session: Session,
) -> None:
    authority = PHASE2["_authority"](db_session)
    category = _active_category(db_session, authority)
    slot = PHASE2["_slot"](
        db_session,
        authority,
        lane=ProductionLane.DAILY_SHORT,
        assignment_mode=AssignmentMode.STANDALONE_REQUIRED,
    )
    slot.category_id = category.id
    slot.production_goal = "Daily Short từ lịch đã đóng băng"
    _configure_verified_destination(db_session, authority)
    _persisted_evidence(db_session, authority)
    daily_llm = _FakeDailyLLMWorkflow(db_session)
    producer = FakeTrustedProducer()
    service = OperatorPlanningService(
        db_session,
        support_producer=producer,
        daily_llm_workflow=daily_llm,
    )
    actor = _actor(authority)
    option = next(
        item
        for item in service.catalog(actor=actor).daily_short_options
        if item.source_id == slot.id
    )

    assert option.source_type == "DAILY_SLOT"
    assert option.launchable is True
    result = service.prepare_and_launch(
        data=OperatorPlanningStartRequest(
            source_type="DAILY_SLOT",
            source_id=slot.id,
            idempotency_key="operator-daily-slot-one-action",
        ),
        actor=actor,
    )

    daily_run = db_session.scalars(
        select(ChannelDailyRun).where(
            ChannelDailyRun.editorial_calendar_slot_id == slot.id
        )
    ).one()
    idea = db_session.scalars(
        select(DailyIdeaDecision).where(
            DailyIdeaDecision.channel_daily_run_id == daily_run.id
        )
    ).one()
    preflight = db_session.scalars(
        select(IdeaMarketPreflight).where(
            IdeaMarketPreflight.daily_idea_decision_id == idea.id
        )
    ).one()
    admission = db_session.get(ProjectAdmissionDecision, result.admission_id)
    assert daily_llm.calls == 1
    assert producer.calls == 1
    assert daily_run.status == "COMPLETED"
    assert idea.schema_version == "v2"
    assert idea.production_lane == "DAILY_SHORT"
    assert idea.decision_status == "PROPOSED"
    assert preflight.decision == "PASS"
    assert preflight.policy_fit_state == "PASS"
    assert admission is not None
    assert admission.daily_idea_decision_id == idea.id
    assert result.technical_appendix["media_provider_calls"] is False
    assert db_session.scalar(select(func.count(ProviderAttempt.id))) == 0


def test_catalog_blocks_preflight_preparation_without_persisted_evidence(
    db_session: Session,
) -> None:
    authority, slot, _, idea, _ = _daily_source(
        db_session,
        with_preflight=False,
        with_evidence=False,
    )

    option = next(
        item
        for item in OperatorPlanningService(db_session)
        .catalog(actor=_actor(authority))
        .daily_short_options
        if item.source_id == idea.id
    )

    assert option.source_type == "DAILY_IDEA"
    assert option.launchable is False
    assert option.technical_appendix["reason_code"] == "SEARCH_DEMAND_EVIDENCE_MISSING"
    assert "persisted" in option.guidance
    assert slot.status == "OPEN"
    assert db_session.scalar(select(func.count(ProjectAdmissionDecision.id))) == 0


def test_prepare_rejects_cross_company_source_before_producer_call(
    db_session: Session,
) -> None:
    first_authority, _, _ = _long_source(db_session)
    second_authority, second_slot, _ = _long_source(db_session)
    producer = FakeTrustedProducer()

    with pytest.raises(ForbiddenError):
        OperatorPlanningService(
            db_session,
            support_producer=producer,
        ).prepare_source(
            data=OperatorPlanningPrepareRequest(
                source_type="LONG_FORM_PLAN",
                source_id=second_slot.id,
            ),
            actor=_actor(first_authority),
        )

    assert first_authority.company.id != second_authority.company.id
    assert producer.calls == 0
    assert db_session.scalar(select(func.count(ProjectAdmissionDecision.id))) == 0


def test_disabled_support_producer_blocks_before_workflow_with_retry_metadata(
    db_session: Session,
) -> None:
    authority, slot, _ = _long_source(db_session)
    service = OperatorPlanningService(
        db_session,
        support_producer=LLMRouterV2SupportProducer(
            db_session,
            enabled=False,
        ),
    )

    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_LLM_PRODUCER_DISABLED",
    ) as captured:
        service.prepare_and_launch(
            data=OperatorPlanningStartRequest(
                source_type="LONG_FORM_PLAN",
                source_id=slot.id,
                idempotency_key="disabled-support-producer",
            ),
            actor=_actor(authority),
        )

    mapped = _as_operator_planning_http_error(captured.value)
    assert mapped.status_code == 503
    assert mapped.detail == {
        "error_code": "V2_SUPPORT_LLM_PRODUCER_DISABLED",
        "classification": "BLOCK_EXTERNAL_FAILURE",
        "retry_eligible": False,
        "next_action": "CONFIGURE_LLM_ROUTER",
        "workflow_started": False,
        "fallback_used": False,
    }
    assert db_session.scalar(select(func.count(ProductionWorkflowRun.id))) == 0
    assert (
        db_session.scalar(
            select(func.count(Artifact.id)).where(
                Artifact.artifact_type == "v2_frozen_support_envelope"
            )
        )
        == 0
    )


def test_failed_support_producer_maps_to_explicit_manual_retry() -> None:
    mapped = _as_operator_planning_http_error(
        ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_FAILED")
    )

    assert mapped.status_code == 503
    assert mapped.detail == {
        "error_code": "V2_SUPPORT_LLM_PRODUCER_FAILED",
        "classification": "BLOCK_EXTERNAL_FAILURE",
        "retry_eligible": True,
        "next_action": "RETRY_WHEN_LLM_ROUTER_HEALTHY",
        "workflow_started": False,
        "fallback_used": False,
    }


def test_public_contracts_are_id_only_and_routes_are_authenticated() -> None:
    malicious_overrides = {
        "company_id": str(uuid.uuid4()),
        "approved_script": "caller supplied content",
        "evidence_blob": {"decision": "PASS"},
        "policy_snapshot_id": str(uuid.uuid4()),
        "idea_market_preflight_id": str(uuid.uuid4()),
        "title": "caller supplied title",
    }
    for field, value in malicious_overrides.items():
        with pytest.raises(ValidationError):
            OperatorPlanningPrepareRequest.model_validate(
                {
                    "source_type": "DAILY_IDEA",
                    "source_id": str(uuid.uuid4()),
                    field: value,
                }
            )
        with pytest.raises(ValidationError):
            OperatorPlanningStartRequest.model_validate(
                {
                    "source_type": "LONG_FORM_PLAN",
                    "source_id": str(uuid.uuid4()),
                    "idempotency_key": "safe-command",
                    field: value,
                }
            )
    with pytest.raises(ValidationError):
        DailyShortPlanningLaunchRequest.model_validate(
            {
                "daily_idea_decision_id": str(uuid.uuid4()),
                "approved_script": "caller supplied content",
            }
        )
    with pytest.raises(ValidationError):
        LongFormPlanningLaunchRequest.model_validate(
            {
                "editorial_calendar_slot_id": str(uuid.uuid4()),
                "idea_market_preflight_id": str(uuid.uuid4()),
            }
        )

    application = create_app()
    assert uncovered_protected_routes(application) == []
    assert (
        permission_for_route("GET", "/operator-planning/catalog") == "production.read"
    )
    for path in (
        "/operator-planning/prepare",
        "/operator-planning/launch",
        "/operator-planning/daily-short/launch",
        "/operator-planning/long-form/launch",
    ):
        assert permission_for_route("POST", path) == "production.start"

    anonymous = TestClient(application)
    assert anonymous.get("/operator-planning/catalog").status_code == 401
    assert (
        anonymous.post(
            "/operator-planning/prepare",
            json={
                "source_type": "DAILY_IDEA",
                "source_id": str(uuid.uuid4()),
            },
        ).status_code
        == 401
    )
    assert (
        anonymous.post(
            "/operator-planning/launch",
            json={
                "source_type": "LONG_FORM_PLAN",
                "source_id": str(uuid.uuid4()),
                "idempotency_key": "anonymous-command",
            },
        ).status_code
        == 401
    )
