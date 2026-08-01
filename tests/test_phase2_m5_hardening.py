from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.api.routes.serializers_core import (
    _editorial_slot,
    _idea_market_preflight,
    _project_admission_decision,
)
from app.contracts.geo_market import TargetMarketProfile
from app.contracts.m5 import (
    EditorialCalendarSlotCreate,
    EditorialCalendarSlotRead,
    IdeaMarketPreflightCreate,
    IdeaMarketPreflightRead,
    ProjectAdmissionDecisionRead,
)
from app.contracts.nich1 import NicheGateVerdict, nich1_stable_hash
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
from app.services.geo_market import TargetMarketDigestCompiler
from app.services.m5 import (
    EditorialCalendarService,
    IdeaMarketPreflightService,
    _typed_slot_niche_authority,
)
from app.services.nich1 import EditorialSlotValidator


class _FakeSession:
    def __init__(self, rows: list[tuple[type[Any], Any]]) -> None:
        self.rows = {(model, row.id): row for model, row in rows}
        self.added: list[Any] = []

    def get(self, model: type[Any], row_id: uuid.UUID) -> Any | None:
        return self.rows.get((model, row_id))

    def add(self, row: Any) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        if (
            hasattr(type(row), "created_at")
            and getattr(row, "created_at", None) is None
        ):
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
    profile_input = {
        "template_key": "small_team_ai",
        "series_plan": [
            {
                "key": series_key,
                "content_pillar_key": pillar,
            }
        ],
        "media_style": {
            "niche_visual_source_profile": "STOCK_ASSISTED",
        },
        "voice_style": {
            "tone": "calm practical",
            "pacing": "measured",
        },
    }
    target_market_profile = TargetMarketProfile(
        profile_version=1,
        channel_id=channel_id,
        channel_key="small-team-ai",
        primary_market="US",
        primary_geo_cluster=["US"],
        acceptable_secondary_geos=["CA", "GB", "AU"],
        primary_locale="en-US",
        content_language="en",
        narration_locale="en-US",
        primary_timezone="America/New_York",
        spelling_system="US",
        currency="USD",
        units_policy="US_WITH_METRIC_WHEN_RELEVANT",
        date_format="MMM D, YYYY",
        title_locale="en-US",
        thumbnail_text_locale="en-US",
        caption_locales=["en-US"],
        audience_market_context="US_SMALL_BUSINESS",
        workplace_context="US_SMALL_BUSINESS",
        source_jurisdiction_policy=("TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED"),
        preferred_source_jurisdictions=["US"],
        foreign_source_context_required=True,
        allowed_market_contexts=["US", "CA", "GB", "AU"],
        prohibited_market_mismatches=[
            "TRANSLATED_SOUNDING_ENGLISH",
            "FOREIGN_LEGAL_ASSUMPTION_WITHOUT_CONTEXT",
            "WRONG_VOICE_LOCALE",
            "WRONG_METADATA_LOCALE",
            "WRONG_THUMBNAIL_LOCALE",
        ],
        initial_publish_window_hypotheses=[
            {
                "timezone": "America/New_York",
                "days": ["TUE", "THU"],
                "local_time": "10:00",
                "status": "HYPOTHESIS_ONLY",
            }
        ],
        minimum_comparable_videos=3,
        video_geo_evaluation_window_days=7,
        channel_geo_review_window_days=30,
        account_country=None,
        target_market="US",
        actual_viewer_geography_state="UNMEASURED",
        approval_ref="operator-approval://phase2-m5-hardening/us/v1",
    )
    target_market_digest = TargetMarketDigestCompiler().compile(target_market_profile)
    channel_contract = {
        "contract_status": "COMPLETE",
        "channel_identity": {
            "channel_key": "small-team-ai",
            "niche": "Practical AI for small professional teams",
            "positioning": "Evidence-aware AI operations for lean teams",
            "brand_promise": ("Turn repeated work into bounded, auditable workflows"),
            "series_plan": profile_input["series_plan"],
        },
        "target_audience": {
            "primary_persona": "small-team operators",
            "audience_segments": ["founders", "operations leads"],
            "pain_points": [
                "repetitive support work",
                "unclear automation risk",
            ],
            "desired_outcomes": [
                "save operator time responsibly",
                "adopt auditable workflows",
            ],
        },
        "market_locale": {
            "primary_market": "US",
            "content_language": "en",
            "audience_locale": "en-US",
        },
        "editorial_strategy": {
            "content_pillars": [
                pillar,
                "Small-team operating leverage",
            ],
            "allowed_topics": [
                "AI workflow",
                "small-team operations",
            ],
            "forbidden_topics": [
                "crypto trading",
                "medical guarantees",
            ],
        },
        "voice_style": {
            "narration_tone": "calm practical documentary",
            "pacing": "measured",
            "allowed_style": ["evidence-aware"],
            "forbidden_style": ["hype"],
        },
        "format_policy": {
            "primary_format": "long-form documentary/explainer",
            "target_runtime_minutes": {
                "minimum": 6,
                "maximum": 12,
            },
        },
        "media_policy": {
            "niche_visual_source_profile": "STOCK_ASSISTED",
        },
    }
    compiled_payload = {
        "channel_contract_json": channel_contract,
        "contract_status": "COMPLETE",
        "channel_scoped_policy": {
            "policy_version": "small-team-ai.channel-policy.v2",
            "channel_visual_strategy_profile": {
                "niche_visual_source_profile": "STOCK_ASSISTED",
            },
            "visual_source_policy_binding": {
                "schema_version": "ch1-flex.visual-source-policy-binding.v2",
                "niche_visual_source_profile": "STOCK_ASSISTED",
            },
            "gate_policy": {
                "niche_alignment_required": True,
                "channel_fit_threshold": 0.8,
            },
            "target_market_profile": target_market_profile.model_dump(mode="json"),
            "target_market_digest": target_market_digest.model_dump(mode="json"),
        },
    }
    channel = SimpleNamespace(
        id=channel_id,
        company_id=company_id,
        key="small-team-ai",
        active_policy_snapshot_id=snapshot_id,
    )
    profile = SimpleNamespace(
        id=profile_id,
        channel_workspace_id=channel_id,
        status="active",
        profile_input=profile_input,
        profile_input_hash=nich1_stable_hash(profile_input),
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
        status="active",
        compiled_payload=compiled_payload,
        content_hash=nich1_stable_hash(compiled_payload),
    )
    category_payload = {
        "id": str(category_id),
        "name": "Workflow audits",
        "sub_niche": "AI workflow audits for lean operations",
        "content_pillar": pillar,
        "allowed_topics": ["workflow audit", "support automation"],
        "forbidden_topics": ["enterprise ERP migration"],
    }
    category = SimpleNamespace(
        id=category_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        category_key="workflow-audits",
        name="Workflow audits",
        sub_niche="AI workflow audits for lean operations",
        audience_segment="small-team operators",
        content_pillar=pillar,
        status="ACTIVE",
        allowed_topics_json=["workflow audit", "support automation"],
        forbidden_topics_json=["enterprise ERP migration"],
        default_format_policy_json={"format": "explainer"},
        default_visual_style_json={
            "niche_visual_source_profile": "STOCK_ASSISTED",
        },
        content_hash=nich1_stable_hash(category_payload),
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
        content_pillar_id=None,
        content_pillar_key=pillar,
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
        target_market_profile=target_market_profile,
        target_market_digest=target_market_digest,
        session=session,
    )


def test_v2_long_form_preflight_ignores_forged_pass_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _strict_long_form_authority()
    monkeypatch.setattr(
        "app.services.m5._record_m5_event", lambda *args, **kwargs: None
    )

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
    read = IdeaMarketPreflightRead.model_validate(_idea_market_preflight(preflight))
    assert read.editorial_calendar_slot_id == authority.slot.id

    authority.evidence.search_volume_30d = 1000
    persisted_pass = IdeaMarketPreflightService(authority.session).create_preflight(
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


def test_v2_serializers_preserve_long_form_typed_fields() -> None:
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

    decision = SimpleNamespace(
        id=uuid.uuid4(),
        schema_version="v2",
        editorial_research_run_id=uuid.uuid4(),
        editorial_idea_candidate_id=uuid.uuid4(),
        editorial_calendar_slot_id=slot.id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=uuid.uuid4(),
        policy_snapshot_id=policy_id,
        idea_market_preflight_id=uuid.uuid4(),
        planning_source_type="LONG_FORM_PLAN",
        production_lane="LONG_FORM",
        content_mode="STANDALONE",
        assignment_mode="STANDALONE_REQUIRED",
        series_plan_id=None,
        series_run_id=None,
        episode_number=None,
        episode_role=None,
        standalone_reason_code="STANDALONE_REQUIRED",
        resolver_version="vcos-assignment-resolver-v2.1",
        resolver_input_hash="d" * 64,
        decision_hash="e" * 64,
        assignment_input_ref={"source": "LONG_FORM_PLAN"},
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
    assert read.editorial_research_run_id == decision.editorial_research_run_id
    assert read.editorial_idea_candidate_id == decision.editorial_idea_candidate_id
    assert read.planning_source_type == "LONG_FORM_PLAN"
    assert read.production_lane == ProductionLane.LONG_FORM
    assert read.assignment_input_ref == {"source": "LONG_FORM_PLAN"}
