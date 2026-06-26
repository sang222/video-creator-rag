import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from typer.testing import CliRunner

import app.services.m5 as m5_service
from app.cli.main import app as cli_app
from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.m5 import (
    ChannelDailyRunCreate,
    ChannelStatePackSnapshotCreate,
    ContextPackSnapshotCreate,
    DailyRunExecuteRequest,
    EditorialCalendarSlotCreate,
    IdeaMarketPreflightCreate,
    ProjectAdmissionDecisionCreate,
    RenderSpecDraft,
    RetrievalPlanSnapshotCreate,
    SearchDemandEvidenceCreate,
)
from app.contracts.ops import ProviderHealthCheckRequest, QuotaAccountCreate
from app.core.errors import ConflictError, ValidationFailureError
from app.db.models import (
    Artifact,
    AuditEvent,
    CostEvent,
    DailyIdeaDecision,
    DomainEvent,
    LLMRunSnapshot,
    ProviderAttempt,
    QuotaEvent,
    User,
    VideoProject,
)
from app.main import create_app
from app.services import (
    ChannelDailyRunService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelStatePackService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    EditorialCalendarService,
    IdeaMarketPreflightService,
    ProjectAdmissionService,
    ProviderHealthService,
    ProviderRegistryService,
    QuotaService,
    RBACService,
    ResourceResolverService,
    SearchDemandEvidenceService,
)

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

M5_TABLES = {
    "editorial_calendar_slots",
    "channel_daily_runs",
    "retrieval_plan_snapshots",
    "context_pack_snapshots",
    "channel_state_pack_snapshots",
    "search_demand_evidence",
    "search_intent_maps",
    "audience_target_packs",
    "idea_market_preflights",
    "daily_idea_decisions",
    "project_admission_decisions",
}

FORBIDDEN_M7_PLUS_FRAGMENTS = {
    "thumbnail_compositor",
    "tts_generation",
    "video_generation",
    "publish_upload",
    "analytics_semantic",
    "memory_promotion",
    "dashboard",
    "source_scrap",
    "source_parse",
    "opa_policy",
    "cedar_policy",
    "algorithm_agent",
    "growth_agent",
    "view_agent",
}


def _user(db_session, email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0], status="active")
    db_session.add(user)
    db_session.flush()
    return user


def _base(db_session):
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    ProviderRegistryService(db_session).seed_mock_providers()
    company = CompanyService(db_session).create_company(name="M5 Co")
    operator = _user(db_session, f"operator-{uuid.uuid4()}@example.com")
    RBACService(db_session).assign_role(user_id=operator.id, role_key="operator", company_id=company.id)
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key=f"m5-{uuid.uuid4().hex[:8]}", name="M5 Channel"),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiled = ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="m5-compile")
    snapshot = ChannelProfileService(db_session).activate_snapshot(snapshot_id=compiled.snapshot_id)
    return company, channel, snapshot, profile, operator


def _slot(db_session):
    company, channel, snapshot, profile, operator = _base(db_session)
    slot = EditorialCalendarService(db_session).create_slot(
        data=EditorialCalendarSlotCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            slot_date=date(2026, 6, 24),
            production_goal="Explain budgeted video workflows for founders",
            target_platforms=["YOUTUBE"],
            content_pillar="education",
            format_hint="explainer",
            created_by_user_id=operator.id,
        )
    )
    return company, channel, snapshot, profile, operator, slot


def _evidence(db_session, company, channel, *, volume: int | None = 800):
    return SearchDemandEvidenceService(db_session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            evidence_source_type="MOCK",
            query="budgeted video workflow",
            platform="YOUTUBE",
            search_volume_30d=volume,
            relative_interest_index=Decimal("70"),
            competition_index=Decimal("0.30"),
            evidence_confidence="MEDIUM",
        )
    )


def test_m5_migration_tables_defaults_and_scope_guard(engine, db_session) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M5_TABLES <= tables
    assert not {table for table in tables for fragment in FORBIDDEN_M7_PLUS_FRAGMENTS if fragment in table}
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    assert slot.target_platforms == ["YOUTUBE"]
    assert slot.operational_envelope == {}
    assert daily_run.reason_codes == []
    assert daily_run.metadata_ == {}
    with engine.connect() as connection:
        revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
    assert revision == "0012_m10_1_router_derivatives"


def test_resource_resolver_enforces_scope_sources_and_deterministic_pack(db_session) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    service = ResourceResolverService(db_session)
    with pytest.raises(ValidationFailureError):
        service.create_retrieval_plan(
            data=RetrievalPlanSnapshotCreate(
                purpose="DAILY_IDEA",
                company_id=company.id,
                allowed_sources=["policy_snapshot"],
            )
        )
    with pytest.raises(ValidationFailureError):
        service.create_retrieval_plan(
            data=RetrievalPlanSnapshotCreate(
                purpose="DAILY_IDEA",
                company_id=company.id,
                channel_workspace_id=channel.id,
                policy_snapshot_id=snapshot.id,
                editorial_calendar_slot_id=slot.id,
                allowed_sources=["vector"],
            )
        )
    _evidence(db_session, company, channel)
    plan = service.create_retrieval_plan(
        data=RetrievalPlanSnapshotCreate(
            purpose="DAILY_IDEA",
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            allowed_sources=["channel_profile", "policy_snapshot", "editorial_slot", "search_demand_evidence"],
            source_order=["channel_profile", "policy_snapshot", "editorial_slot", "search_demand_evidence"],
            created_by_user_id=operator.id,
        )
    )
    pack = service.build_context_pack(data=ContextPackSnapshotCreate(retrieval_plan_snapshot_id=plan.id))
    pack_again = service.build_context_pack(data=ContextPackSnapshotCreate(retrieval_plan_snapshot_id=plan.id))
    assert pack.pack_hash == pack_again.pack_hash
    assert pack.memory_refs == []
    assert pack.metric_refs == []
    assert pack.pack_content["metric_truth"]["state"] == "UNKNOWN"
    assert pack.pack_content["scope"]["channel_workspace_id"] == str(channel.id)
    assert pack.evidence_refs[0]["type"] == "search_demand_evidence"
    serialized = json.dumps(pack.pack_content)
    assert "sk-" not in serialized
    with pytest.raises(ValidationFailureError):
        service.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan.id,
                memory_refs=[{"type": "company_memory", "id": "not-m5"}],
            )
        )


def test_channel_state_and_market_preflight_paths(db_session) -> None:
    company, channel, snapshot, _, _, slot = _slot(db_session)
    evidence = _evidence(db_session, company, channel, volume=900)
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    state_pack = ChannelStatePackService(db_session).build_snapshot(
        data=ChannelStatePackSnapshotCreate(
            channel_daily_run_id=daily_run.id,
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
        )
    )
    same_state = ChannelStatePackService(db_session).build_snapshot(
        data=ChannelStatePackSnapshotCreate(
            channel_daily_run_id=daily_run.id,
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
        )
    )
    assert state_pack.state_hash == same_state.state_hash
    assert state_pack.state_blob["analytics"]["state"] == "UNKNOWN"
    assert state_pack.evidence_summary["search_demand_evidence_count"] == 1
    passed = IdeaMarketPreflightService(db_session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_daily_run_id=daily_run.id,
            evidence_blob={"search_demand_evidence_ids": [str(evidence.id)]},
            policy_fit_state="PASS",
        )
    )
    review = IdeaMarketPreflightService(db_session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            evidence_blob={"search_led": True},
        )
    )
    non_search_led = IdeaMarketPreflightService(db_session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            evidence_blob={"search_led": False},
        )
    )
    assert passed.decision == "PASS"
    assert review.decision == "REVIEW_REQUIRED"
    assert non_search_led.decision == "PASS"
    assert "SEARCH_DEMAND_EVIDENCE_MISSING" in review.reason_codes


def test_daily_run_mock_success_and_budgeted_project_admission_e2e(db_session) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    evidence = _evidence(db_session, company, channel, volume=1200)
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    executed = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=daily_run.id,
        data=DailyRunExecuteRequest(created_by_user_id=operator.id),
    )
    assert executed.status == "COMPLETED"
    assert executed.context_pack_snapshot_id is not None
    assert executed.channel_state_pack_snapshot_id is not None
    assert executed.daily_idea_decision_id is not None
    idea = db_session.get(DailyIdeaDecision, executed.daily_idea_decision_id)
    assert idea is not None
    assert idea.llm_run_snapshot_id is not None
    assert idea.decision_status == "PROPOSED"
    preflight = IdeaMarketPreflightService(db_session).create_preflight(
        data=IdeaMarketPreflightCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_daily_run_id=executed.id,
            daily_idea_decision_id=idea.id,
            evidence_blob={"search_demand_evidence_ids": [str(evidence.id)]},
            policy_fit_state="PASS",
        )
    )
    admission = ProjectAdmissionService(db_session).create_decision(
        data=ProjectAdmissionDecisionCreate(
            channel_daily_run_id=executed.id,
            daily_idea_decision_id=idea.id,
            idea_market_preflight_id=preflight.id,
            created_by_user_id=operator.id,
        )
    )
    assert admission.decision == "ADMIT"
    assert admission.admitted_video_project_id is not None
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    assert project is not None
    assert project.policy_snapshot_id == snapshot.id
    artifact_types = {
        artifact.artifact_type
        for artifact in db_session.scalars(select(Artifact).where(Artifact.video_project_id == project.id)).all()
    }
    assert artifact_types == {"creative_brief", "research_pack", "source_pack"}
    assert "script" not in artifact_types
    assert db_session.query(LLMRunSnapshot).count() == 1
    assert db_session.query(ProviderAttempt).filter_by(provider_key="mock_llm").count() == 1
    assert db_session.query(CostEvent).count() == 1
    event_types = {event.event_type for event in db_session.scalars(select(DomainEvent)).all()}
    audit_types = {event.event_type for event in db_session.scalars(select(AuditEvent)).all()}
    assert "daily_idea_decision.created" in event_types
    assert "project_admission_decision.admitted" in event_types
    assert "initial_artifact.created_from_daily_run" in audit_types


def test_daily_run_quota_and_malformed_mock_fail_safely(db_session) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    quota = QuotaService(db_session).create_account(
        data=QuotaAccountCreate(
            provider_key="mock_llm",
            quota_scope_type="CHANNEL",
            quota_scope_id=channel.id,
            quota_window="DAILY",
            quota_limit=Decimal("0"),
            unit="REQUESTS",
        )
    )
    quota_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    blocked = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=quota_run.id,
        data=DailyRunExecuteRequest(quota_account_id=quota.id, created_by_user_id=operator.id),
    )
    assert blocked.status == "BLOCKED"
    assert "PROVIDER_QUOTA_BLOCKED" in blocked.reason_codes
    assert db_session.query(VideoProject).count() == 0
    assert db_session.scalars(select(QuotaEvent).where(QuotaEvent.event_type == "REJECT")).one().reason_code == "QUOTA_EXHAUSTED"
    assert db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.status == "QUOTA_REJECTED")).one()

    malformed_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    failed = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=malformed_run.id,
        data=DailyRunExecuteRequest(mock_mode="malformed", created_by_user_id=operator.id),
    )
    assert failed.status == "FAILED"
    assert "LLM_OUTPUT_MALFORMED" in failed.reason_codes
    assert "LLM_SCHEMA_VALIDATION_FAILED" in failed.reason_codes
    assert db_session.query(VideoProject).count() == 0
    assert db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.error_code == "MALFORMED_OUTPUT")).one()
    with pytest.raises(ConflictError):
        ChannelDailyRunService(db_session).execute_run(
            daily_run_id=malformed_run.id,
            data=DailyRunExecuteRequest(),
        )


def test_authority_blocks_when_context_has_no_allowed_idea_source(db_session) -> None:
    company, channel, snapshot, _, operator = _base(db_session)
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            run_date=date(2026, 6, 24),
        )
    )
    blocked = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=daily_run.id,
        data=DailyRunExecuteRequest(created_by_user_id=operator.id),
    )
    assert blocked.status == "BLOCKED"
    assert "AUTHORITY_CONTEXT_INSUFFICIENT" in blocked.reason_codes
    assert "AUTHORITY_IDEA_SOURCE_MISSING" in blocked.reason_codes
    assert blocked.context_pack_snapshot_id is not None
    assert blocked.channel_state_pack_snapshot_id is not None
    assert db_session.query(DailyIdeaDecision).count() == 0
    assert db_session.query(LLMRunSnapshot).count() == 0
    assert db_session.query(ProviderAttempt).count() == 0
    assert db_session.query(CostEvent).count() == 0
    assert db_session.query(VideoProject).count() == 0


def test_m5_rejects_real_provider_key_before_any_provider_attempt(db_session) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    blocked = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=daily_run.id,
        data=DailyRunExecuteRequest(provider_key="openai", created_by_user_id=operator.id),
    )
    assert blocked.status == "BLOCKED"
    assert "PROVIDER_HEALTH_BLOCKED" in blocked.reason_codes
    assert db_session.query(ProviderAttempt).count() == 0
    assert db_session.query(CostEvent).count() == 0
    assert db_session.query(VideoProject).count() == 0


def test_authority_schema_validation_failure_records_failed_attempt(db_session, monkeypatch) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    _evidence(db_session, company, channel)
    original = m5_service._proposal_from_context

    def malformed_proposal(context_pack, state_pack):
        proposal = original(context_pack, state_pack)
        proposal.pop("proposed_title")
        return proposal

    monkeypatch.setattr(m5_service, "_proposal_from_context", malformed_proposal)
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    failed = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=daily_run.id,
        data=DailyRunExecuteRequest(created_by_user_id=operator.id),
    )
    assert failed.status == "FAILED"
    assert "LLM_SCHEMA_VALIDATION_FAILED" in failed.reason_codes
    assert db_session.query(DailyIdeaDecision).count() == 0
    attempt = db_session.scalars(
        select(ProviderAttempt).where(ProviderAttempt.error_code == "LLM_SCHEMA_VALIDATION_FAILED")
    ).one()
    assert attempt.status == "NON_RETRYABLE_FAILURE"
    llm_run = db_session.scalars(select(LLMRunSnapshot)).one()
    assert llm_run.status == "FAILED"
    assert llm_run.provider_key == "mock_llm"
    assert llm_run.model_key == "mock-llm"
    assert llm_run.run_mode == "MOCK"
    assert db_session.query(CostEvent).count() == 1
    assert db_session.query(VideoProject).count() == 0


def test_render_spec_voice_as_master_contract_validation() -> None:
    valid_payload = {
        "voice_as_master": True,
        "narration_timeline_ref": "mock://voice-timeline/v1",
        "scenes": [
            {
                "scene_id": "scene-001",
                "start_time": 0,
                "end_time": 4.5,
                "narration_segment_id": "narration-001",
                "caption_or_narration_ref": "caption-001",
                "visual_intent": "Show the workflow problem.",
                "preferred_source": "placeholder",
            },
            {
                "scene_id": "scene-002",
                "start_time": 4.5,
                "end_time": "00:09",
                "narration_segment_id": "narration-002",
                "caption_or_narration_ref": "caption-002",
                "visual_intent": "Show the planned fix.",
                "preferred_source": "placeholder",
            },
        ],
    }
    spec = RenderSpecDraft.model_validate(valid_payload)
    assert spec.status == "contract_only_for_m6"

    overlap_payload = {**valid_payload, "scenes": [{**valid_payload["scenes"][0]}, {**valid_payload["scenes"][1], "start_time": 4}]}
    with pytest.raises(ValidationError, match="RENDER_SPEC_SCENE_OVERLAP"):
        RenderSpecDraft.model_validate(overlap_payload)

    bad_timing_payload = {**valid_payload, "scenes": [{**valid_payload["scenes"][0], "end_time": 0}]}
    with pytest.raises(ValidationError, match="RENDER_SPEC_SCENE_TIMING_INVALID"):
        RenderSpecDraft.model_validate(bad_timing_payload)

    missing_narration_payload = {
        **valid_payload,
        "scenes": [{**valid_payload["scenes"][0], "narration_segment_id": ""}],
    }
    with pytest.raises(ValidationError, match="RENDER_SPEC_MISSING_NARRATION_SEGMENT"):
        RenderSpecDraft.model_validate(missing_narration_payload)

    voice_not_master_payload = {**valid_payload, "voice_as_master": False}
    with pytest.raises(ValidationError, match="VOICE_AS_MASTER_CONTRACT_REQUIRED"):
        RenderSpecDraft.model_validate(voice_not_master_payload)


def test_m5_mock_only_has_no_external_provider_or_network_calls() -> None:
    m5_source = (ROOT / "app/services/m5.py").read_text()
    provider_source = (ROOT / "app/providers/mock.py").read_text()
    combined = f"{m5_source}\n{provider_source}".lower()
    for forbidden in (
        "from openai",
        "import openai",
        "from anthropic",
        "import anthropic",
        "from ollama",
        "import ollama",
        "requests.post",
        "requests.get",
        "httpx.",
        "aiohttp.",
    ):
        assert forbidden not in combined
    assert "mockllmprovider" in combined


def test_provider_unhealthy_blocks_or_review_required(db_session) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    ProviderHealthService(db_session).check_provider(provider_key="mock_llm", mode="unavailable")
    daily_run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            editorial_calendar_slot_id=slot.id,
            run_date=slot.slot_date,
        )
    )
    blocked = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=daily_run.id,
        data=DailyRunExecuteRequest(created_by_user_id=operator.id),
    )
    assert blocked.status == "BLOCKED"
    assert "PROVIDER_HEALTH_BLOCKED" in blocked.reason_codes
    assert db_session.query(VideoProject).count() == 0


def test_m5_api_and_cli_smoke(db_session) -> None:
    company, channel, snapshot, _, operator, slot = _slot(db_session)
    db_session.commit()
    client = TestClient(create_app())
    evidence = client.post(
        "/search-demand-evidence",
        json={
            "company_id": str(company.id),
            "channel_workspace_id": str(channel.id),
            "evidence_source_type": "MOCK",
            "query": "api m5 smoke",
            "platform": "YOUTUBE",
            "search_volume_30d": 500,
            "evidence_confidence": "MEDIUM",
        },
    )
    assert evidence.status_code == 200, evidence.text
    daily = client.post(
        "/channel-daily-runs",
        json={
            "company_id": str(company.id),
            "channel_workspace_id": str(channel.id),
            "policy_snapshot_id": str(snapshot.id),
            "editorial_calendar_slot_id": str(slot.id),
            "run_date": "2026-06-24",
        },
    )
    assert daily.status_code == 200, daily.text
    executed = client.post(f"/channel-daily-runs/{daily.json()['id']}/execute", json={})
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "COMPLETED"
    inspected = runner.invoke(cli_app, ["daily", "inspect", "--daily-run-id", executed.json()["id"]])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["status"] == "COMPLETED"
