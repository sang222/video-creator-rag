from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.routes.serializers_core import (
    _daily_idea_decision,
    _editorial_slot,
    _idea_market_preflight,
    _project_admission_decision,
)
from app.contracts.m5 import (
    DailyIdeaDecisionRead,
    EditorialCalendarSlotCreate,
    EditorialCalendarSlotRead,
    IdeaMarketPreflightCreate,
    IdeaMarketPreflightRead,
    ProjectAdmissionDecisionRead,
)
from app.contracts.nich1 import NicheGateVerdict
from app.contracts.vcos_v2 import AssignmentMode, ProductionLane
from app.core.errors import ValidationFailureError
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    EditorialCalendarSlot,
    SearchDemandEvidence,
    SeriesPlan,
    SeriesRun,
)
from app.services.m5 import (
    EditorialCalendarService,
    IdeaMarketPreflightService,
    _typed_slot_niche_authority,
)
from app.services.nich1 import EditorialSlotValidator


class _FakeSession:
    def __init__(self, rows: list[tuple[type[Any], Any]]) -> None:
        self.rows = {
            (model, row.id): row
            for model, row in rows
        }
        self.added: list[Any] = []

    def get(self, model: type[Any], row_id: uuid.UUID) -> Any | None:
        return self.rows.get((model, row_id))

    def add(self, row: Any) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        if hasattr(type(row), "created_at") and getattr(row, "created_at", None) is None:
            row.created_at = datetime.now(UTC)
        self.added.append(row)

    def flush(self) -> None:
        return None


def _strict_long_form_authority() -> SimpleNamespace:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    category_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    series_key = "workflow-audit"
    pillar = "Practical AI workflows"
    channel_contract = {
        "contract_status": "COMPLETE",
        "channel_identity": {
            "series_plan": [
                {
                    "key": series_key,
                    "content_pillar_key": pillar,
                }
            ]
        },
        "editorial_strategy": {
            "content_pillars": [pillar],
            "allowed_topics": ["AI workflow"],
            "forbidden_topics": ["medical guarantees"],
        },
    }
    compiled_payload = {
        "channel_contract_json": channel_contract,
        "channel_scoped_policy": {
            "policy_version": "small-team-ai.channel-policy.v2",
            "visual_source_policy_binding": {
                "schema_version": "ch1-flex.visual-source-policy-binding.v2"
            },
            "gate_policy": {"channel_fit_threshold": 0.8},
        },
    }
    channel = SimpleNamespace(
        id=channel_id,
        company_id=company_id,
        active_policy_snapshot_id=snapshot_id,
    )
    profile = SimpleNamespace(
        id=profile_id,
        channel_workspace_id=channel_id,
        status="active",
        profile_input={
            "series_plan": [
                {
                    "key": series_key,
                    "content_pillar_key": pillar,
                }
            ]
        },
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
        status="active",
        compiled_payload=compiled_payload,
        content_hash="a" * 64,
    )
    category = SimpleNamespace(
        id=category_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        category_key="workflow-audits",
        name="Workflow audits",
        sub_niche="AI workflow audits for lean operations",
        content_pillar=pillar,
        status="ACTIVE",
        allowed_topics_json=["workflow audit"],
        forbidden_topics_json=["medical guarantees"],
        content_hash="b" * 64,
    )
    plan = SimpleNamespace(
        id=plan_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
        policy_snapshot_id=snapshot_id,
        stable_series_key=series_key,
        allowed_production_lanes=["LONG_FORM"],
    )
    slot = SimpleNamespace(
        id=slot_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        policy_snapshot_id=snapshot_id,
        category_id=category_id,
        schema_version="v2",
        production_lane="LONG_FORM",
        assignment_mode="SERIES_PREFERRED",
        preferred_series_plan_id=plan_id,
        preferred_series_run_id=None,
        series_key=None,
        content_pillar=pillar,
        production_goal="Teach a small team to audit one AI workflow",
        format_hint="long-form explainer",
        operational_envelope={},
    )
    validation = EditorialSlotValidator().validate(
        channel=channel,
        profile_version=profile,
        policy_snapshot=snapshot,
        channel_contract=channel_contract,
        category=category,
        editorial_slot=_typed_slot_niche_authority(
            slot,
            preferred_plan=plan,
        ),
        strict_production=True,
    )
    assert validation.verdict == NicheGateVerdict.PASS
    slot.operational_envelope = {
        "nich1_slot_validation": validation.model_dump(mode="json")
    }
    evidence = SimpleNamespace(
        id=evidence_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        evidence_source_type="MANUAL_RESEARCH",
        query="AI workflow audit",
        platform="YOUTUBE",
        geo="US",
        search_volume_30d=5,
        relative_interest_index=Decimal("100"),
        competition_index=Decimal("0.10"),
        evidence_confidence="HIGH",
        captured_at=datetime.now(UTC),
    )
    session = _FakeSession(
        [
            (ChannelWorkspace, channel),
            (ChannelProfileVersion, profile),
            (CompiledChannelPolicySnapshot, snapshot),
            (ContentCategory, category),
            (EditorialCalendarSlot, slot),
            (SeriesPlan, plan),
            (SearchDemandEvidence, evidence),
        ]
    )
    return SimpleNamespace(
        company_id=company_id,
        channel_id=channel_id,
        profile_id=profile_id,
        snapshot_id=snapshot_id,
        slot=slot,
        plan=plan,
        evidence=evidence,
        session=session,
    )


def test_v2_long_form_preflight_ignores_forged_pass_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _strict_long_form_authority()
    monkeypatch.setattr("app.services.m5._record_m5_event", lambda *args, **kwargs: None)

    preflight = IdeaMarketPreflightService(authority.session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=authority.company_id,
            channel_workspace_id=authority.channel_id,
            editorial_calendar_slot_id=authority.slot.id,
            demand_score=Decimal("100"),
            channel_fit_score=Decimal("100"),
            policy_fit_state="PASS",
            evidence_blob={
                "search_led": False,
                "search_demand_evidence_ids": [str(authority.evidence.id)],
                "evidence_refs": [
                    {
                        "type": "caller_forgery",
                        "search_volume_30d": 1000000,
                    }
                ],
            },
        )
    )

    assert preflight.decision == "BLOCK"
    assert preflight.policy_fit_state == "PASS"
    assert preflight.demand_score == Decimal("0.5")
    assert preflight.channel_fit_score == Decimal("1")
    assert preflight.evidence_blob["authority_source"] == "PERSISTED_LONG_FORM_SLOT"
    assert preflight.evidence_blob["search_demand_evidence_ids"] == [
        str(authority.evidence.id)
    ]
    assert "caller_forgery" not in str(preflight.evidence_blob)
    assert (
        preflight.evidence_blob["slot_validation_hash"]
        == preflight.evidence_blob["slot_validation"]["content_hash"]
    )
    read = IdeaMarketPreflightRead.model_validate(
        _idea_market_preflight(preflight)
    )
    assert read.editorial_calendar_slot_id == authority.slot.id

    authority.evidence.search_volume_30d = 1000
    persisted_pass = IdeaMarketPreflightService(
        authority.session
    ).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=authority.company_id,
            channel_workspace_id=authority.channel_id,
            editorial_calendar_slot_id=authority.slot.id,
            demand_score=Decimal("0"),
            channel_fit_score=Decimal("0"),
            policy_fit_state="BLOCK",
            target_market="US",
            market_scope=["GLOBAL"],
            market_fit_score=Decimal("0"),
            market_fit_threshold=Decimal("1"),
            evidence_blob={
                "search_demand_evidence_ids": [str(authority.evidence.id)],
                "evidence_refs": [{"type": "caller_forgery"}],
            },
        )
    )
    assert persisted_pass.decision == "PASS"
    assert persisted_pass.policy_fit_state == "PASS"
    assert persisted_pass.demand_score == Decimal("100")

    with pytest.raises(
        ValidationFailureError,
        match="V2_LONG_FORM_PREFLIGHT_PERSISTED_DEMAND_REQUIRED",
    ):
        IdeaMarketPreflightService(authority.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=authority.company_id,
                channel_workspace_id=authority.channel_id,
                editorial_calendar_slot_id=authority.slot.id,
                demand_score=Decimal("100"),
                policy_fit_state="PASS",
                evidence_blob={
                    "evidence_refs": [
                        {
                            "type": "caller_forgery",
                            "search_volume_30d": 1000000,
                        }
                    ]
                },
            )
        )


def test_typed_slot_rejects_series_scope_and_run_plan_mismatch() -> None:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    run_id = uuid.uuid4()
    channel = SimpleNamespace(id=channel_id, company_id=company_id)
    snapshot = SimpleNamespace(
        id=snapshot_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
    )
    scoped_plan = SimpleNamespace(
        id=plan_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
        policy_snapshot_id=snapshot_id,
        stable_series_key="typed-series",
        allowed_production_lanes=["LONG_FORM"],
    )
    wrong_run = SimpleNamespace(
        id=run_id,
        series_plan_id=uuid.uuid4(),
        company_id=company_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
        policy_snapshot_id=snapshot_id,
    )
    session = _FakeSession(
        [
            (ChannelWorkspace, channel),
            (CompiledChannelPolicySnapshot, snapshot),
            (SeriesPlan, scoped_plan),
            (SeriesRun, wrong_run),
        ]
    )
    request = EditorialCalendarSlotCreate(
        company_id=company_id,
        channel_workspace_id=channel_id,
        policy_snapshot_id=snapshot_id,
        slot_date=date(2026, 7, 28),
        slot_type="CAMPAIGN",
        schema_version="v2",
        production_lane=ProductionLane.LONG_FORM,
        assignment_mode=AssignmentMode.SERIES_PREFERRED,
        preferred_series_plan_id=plan_id,
        preferred_series_run_id=run_id,
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_SLOT_SERIES_RUN_PLAN_MISMATCH",
    ):
        EditorialCalendarService(session).create_slot(data=request)

    scoped_plan.company_id = uuid.uuid4()
    with pytest.raises(
        ValidationFailureError,
        match="V2_SLOT_SERIES_PLAN_SCOPE_MISMATCH",
    ):
        EditorialCalendarService(session).create_slot(
            data=request.model_copy(update={"preferred_series_run_id": None})
        )
    assert session.added == []


def test_v2_serializers_preserve_typed_fields_and_nondaily_reads() -> None:
    now = datetime.now(UTC)
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    run_id = uuid.uuid4()
    slot = SimpleNamespace(
        id=uuid.uuid4(),
        company_id=company_id,
        channel_workspace_id=channel_id,
        policy_snapshot_id=policy_id,
        category_id=None,
        slot_date=date(2026, 7, 28),
        slot_type="CAMPAIGN",
        status="OPEN",
        schema_version="v2",
        production_lane="LONG_FORM",
        assignment_mode="SERIES_PREFERRED",
        preferred_series_plan_id=plan_id,
        preferred_series_run_id=run_id,
        production_goal="Typed long-form",
        target_platforms=["YOUTUBE"],
        content_pillar=None,
        series_key=None,
        format_hint="explainer",
        character_binding_policy_json=None,
        risk_level="LOW",
        operational_envelope={},
        created_by_user_id=None,
        created_at=now,
        updated_at=now,
    )
    slot_read = EditorialCalendarSlotRead.model_validate(_editorial_slot(slot))
    assert slot_read.production_lane == ProductionLane.LONG_FORM
    assert slot_read.preferred_series_run_id == run_id

    daily = SimpleNamespace(
        id=uuid.uuid4(),
        channel_daily_run_id=uuid.uuid4(),
        company_id=company_id,
        channel_workspace_id=channel_id,
        policy_snapshot_id=policy_id,
        context_pack_snapshot_id=uuid.uuid4(),
        channel_state_pack_snapshot_id=None,
        llm_run_snapshot_id=None,
        schema_version="v2",
        production_lane="DAILY_SHORT",
        proposed_content_mode="SERIES_EPISODE",
        assignment_input_ref={"slot_id": str(slot.id)},
        decision_status="PROPOSED",
        proposed_title="Typed daily idea",
        proposed_angle=None,
        proposed_format=None,
        proposed_pillar=None,
        proposed_series_key=None,
        rationale={},
        evidence_refs=[],
        reason_codes=[],
        confidence_level="HIGH",
        created_at=now,
    )
    daily_read = DailyIdeaDecisionRead.model_validate(
        _daily_idea_decision(daily)
    )
    assert daily_read.schema_version == "v2"
    assert daily_read.assignment_input_ref == {"slot_id": str(slot.id)}

    for source_type, lane in (
        ("LONG_FORM_PLAN", "LONG_FORM"),
        ("DERIVED_SHORT", "LONG_DERIVED_SHORT"),
    ):
        decision = SimpleNamespace(
            id=uuid.uuid4(),
            schema_version="v2",
            channel_daily_run_id=None,
            daily_idea_decision_id=None,
            editorial_calendar_slot_id=(
                slot.id if source_type == "LONG_FORM_PLAN" else None
            ),
            company_id=company_id,
            channel_workspace_id=channel_id,
            channel_profile_version_id=uuid.uuid4(),
            policy_snapshot_id=policy_id,
            idea_market_preflight_id=uuid.uuid4(),
            planning_source_type=source_type,
            production_lane=lane,
            content_mode="STANDALONE",
            assignment_mode="STANDALONE_REQUIRED",
            series_plan_id=None,
            series_run_id=None,
            episode_number=None,
            episode_role=None,
            standalone_reason_code="STANDALONE_REQUIRED",
            parent_video_project_id=(
                uuid.uuid4() if source_type == "DERIVED_SHORT" else None
            ),
            parent_final_media_ref_id=(
                uuid.uuid4() if source_type == "DERIVED_SHORT" else None
            ),
            canonical_timeline_ref=(
                "artifact-version://timeline"
                if source_type == "DERIVED_SHORT"
                else None
            ),
            canonical_timeline_hash=(
                "c" * 64 if source_type == "DERIVED_SHORT" else None
            ),
            resolver_version="vcos-assignment-resolver-v2.1",
            resolver_input_hash="d" * 64,
            decision_hash="e" * 64,
            assignment_input_ref={"source": source_type},
            duration_contract=None,
            budget_gate_result={"decision": "PASS"},
            readiness_gate_refs=[],
            decision="ADMIT",
            reason_codes=[],
            evidence_refs=[],
            admitted_video_project_id=uuid.uuid4(),
            created_artifact_refs=[],
            created_by_user_id=None,
            created_at=now,
        )
        serialized = _project_admission_decision(decision)
        read = ProjectAdmissionDecisionRead.model_validate(serialized)
        assert read.channel_daily_run_id is None
        assert read.daily_idea_decision_id is None
        assert read.planning_source_type == source_type
        assert read.assignment_input_ref == {"source": source_type}

    with pytest.raises(
        ValidationError,
        match="v1 ProjectAdmissionDecision read requires daily lineage",
    ):
        ProjectAdmissionDecisionRead.model_validate(
            {
                **serialized,
                "schema_version": "v1",
                "planning_source_type": None,
                "production_lane": None,
            }
        )
