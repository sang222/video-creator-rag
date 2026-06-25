from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.contracts.m5 import ContextPackSnapshotCreate, RetrievalPlanSnapshotCreate, SearchDemandEvidenceCreate
from app.core.errors import ValidationFailureError
from app.db.models import Artifact, ContextPackSnapshot, CostEvent, DailyIdeaDecision, LLMRunSnapshot, ProviderAttempt, VideoProject
from app.services import ResourceResolverService

from .helpers.lineage_asserts import assert_m5_project_lineage
from .helpers.qualification_asserts import assert_no_secret_payload


def test_m5_daily_context_admission_lineage_and_artifact_boundary(db_session, qualification_factory) -> None:
    flow = qualification_factory.m5_admitted_project()
    assert flow.daily_run.status == "COMPLETED"
    assert flow.preflight.decision == "PASS"
    assert flow.admission.decision == "ADMIT"
    assert_m5_project_lineage(db_session, flow)
    pack = db_session.get(ContextPackSnapshot, flow.idea.context_pack_snapshot_id)
    assert pack.pack_content["metric_truth"]["state"] == "UNKNOWN"
    assert pack.memory_refs == []
    assert_no_secret_payload(pack.pack_content)
    llm_run = db_session.get(LLMRunSnapshot, flow.idea.llm_run_snapshot_id)
    assert llm_run.provider_key == "mock_llm"
    assert llm_run.run_mode == "MOCK"
    assert db_session.scalar(select(CostEvent).where(CostEvent.provider_key == "mock_llm").limit(1)) is not None
    assert db_session.scalar(select(ProviderAttempt).where(ProviderAttempt.provider_key == "mock_llm").limit(1)) is not None
    artifact_types = {
        artifact.artifact_type
        for artifact in db_session.scalars(select(Artifact).where(Artifact.video_project_id == flow.project.id)).all()
    }
    assert artifact_types == {"creative_brief", "research_pack", "source_pack"}
    assert not {"script", "render_spec", "render_package", "publish"} & artifact_types


def test_m5_resource_resolver_rejects_disallowed_memory_vector_scraping_and_secrets(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="M5 source guard")
    service = ResourceResolverService(db_session)
    with pytest.raises(ValidationFailureError):
        service.create_retrieval_plan(
            data=RetrievalPlanSnapshotCreate(
                purpose="DAILY_IDEA",
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                allowed_sources=["vector"],
            )
        )
    with pytest.raises(ValidationError):
        SearchDemandEvidenceCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            evidence_source_type="AUTOSUGGEST",
            query="unsafe",
        )
    plan = service.create_retrieval_plan(
        data=RetrievalPlanSnapshotCreate(
            purpose="DAILY_IDEA",
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            allowed_sources=["channel_profile", "policy_snapshot"],
        )
    )
    with pytest.raises(ValidationFailureError):
        service.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan.id,
                memory_refs=[{"type": "company_memory", "id": "bad"}],
            )
        )
    with pytest.raises(ValidationFailureError):
        service.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan.id,
                pack_content={"secret_token": "sk-raw"},
            )
        )


def test_m5_malformed_quota_and_provider_health_blocks_stop_admission(db_session, qualification_factory) -> None:
    quota_blocked = qualification_factory.m5_admitted_project(quota_limit=Decimal("0"))
    assert quota_blocked.daily_run.status == "BLOCKED"
    assert "PROVIDER_QUOTA_BLOCKED" in quota_blocked.daily_run.reason_codes
    assert db_session.query(VideoProject).count() == 0

    failed = qualification_factory.m5_admitted_project(mock_mode="malformed")
    assert failed.daily_run.status == "FAILED"
    assert "LLM_OUTPUT_MALFORMED" in failed.daily_run.reason_codes
    assert db_session.query(DailyIdeaDecision).count() == 0
    assert db_session.query(VideoProject).count() == 0

    provider_blocked = qualification_factory.m5_admitted_project(provider_health_mode="unavailable")
    assert provider_blocked.daily_run.status == "BLOCKED"
    assert "PROVIDER_HEALTH_BLOCKED" in provider_blocked.daily_run.reason_codes
    assert db_session.query(VideoProject).count() == 0
