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
    LongFormPlanningLaunchRequest,
    OperatorPlanningPrepareRequest,
    OperatorPlanningStartRequest,
)
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.vcos_v2 import (
    AssignmentMode,
    ProductionLane,
)
from app.core.actor import authenticated_actor_context
from app.core.errors import ForbiddenError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.m5 import (
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
)
from app.db.models.ops import ProviderAttempt
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.workflow import Artifact, VideoProject
from app.main import create_app
from app.services.m5 import SearchDemandEvidenceService
from app.services.operator_planning import OperatorPlanningService
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
            channel_fit_score=Decimal("0.80"),
            policy_fit_state="PASS",
            niche_contract_digest_ref=f"niche-contract://{authority.channel.id}",
            niche_contract_digest_hash="a" * 64,
            target_market_digest_ref=f"target-market://{authority.channel.id}/US",
            target_market_digest_hash="b" * 64,
            editorial_slot_ref=f"editorial-slot://{slot.id}",
            content_category_ref=str(category.id),
            target_market="US",
            market_scope=["US"],
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
                    "source_type": "LONG_FORM_PLAN",
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
        "/operator-planning/long-form/launch",
    ):
        assert permission_for_route("POST", path) == "production.start"

    anonymous = TestClient(application)
    assert anonymous.get("/operator-planning/catalog").status_code == 401
    assert (
        anonymous.post(
            "/operator-planning/prepare",
            json={
                "source_type": "LONG_FORM_PLAN",
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
