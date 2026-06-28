from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts import ArtifactCreate, ArtifactVersionCreate, ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.m5 import (
    ChannelDailyRunCreate,
    DailyRunExecuteRequest,
    EditorialCalendarSlotCreate,
    IdeaMarketPreflightCreate,
    ProjectAdmissionDecisionCreate,
    SearchDemandEvidenceCreate,
)
from app.contracts.m6 import ProductionArtifactRunCreate
from app.contracts.ops import ProviderRegistryEntryCreate, QuotaAccountCreate
from app.contracts.workflow import VideoProjectCreate
from app.db.models import User, VideoProject
from app.services import (
    ArtifactService,
    ChannelDailyRunService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    EditorialCalendarService,
    GateDefinitionService,
    IdeaMarketPreflightService,
    ProductionArtifactRunService,
    ProjectAdmissionService,
    ProviderHealthService,
    ProviderRegistryService,
    QuotaService,
    RBACService,
    SearchDemandEvidenceService,
    VideoProjectService,
)

from .helpers.network_sentinel import install_network_sentinel
from .helpers.qualification_asserts import ROOT


@pytest.fixture(autouse=True)
def outbound_network_sentinel(monkeypatch):
    install_network_sentinel(monkeypatch)


class QualificationFactory:
    def __init__(self, session):
        self.session = session

    def seed_all(self) -> None:
        ConfigRegistryService(self.session).seed([ROOT / "config"])
        registry = ProviderRegistryService(self.session)
        if registry.get_entry("ollama") is None:
            registry.create_entry(
                data=ProviderRegistryEntryCreate(
                    provider_key="ollama",
                    provider_name="Ollama / LLMRouter",
                    provider_type="LLM",
                    capability_blob={"llm_router_lane_bound": True, "guarded_real_execution": True},
                    policy_fit_blob={"production_enabled_when_configured": True},
                    metadata={"readiness_provider_key": "ollama"},
                )
            )
        GateDefinitionService(self.session).seed_definitions()

    def user(self, *, role_key: str = "operator", company_id=None, email_prefix: str = "qual") -> User:
        user = User(
            email=f"{email_prefix}-{uuid.uuid4().hex[:10]}@example.com",
            display_name=email_prefix,
            status="active",
        )
        self.session.add(user)
        self.session.flush()
        if company_id is not None:
            RBACService(self.session).assign_role(user_id=user.id, role_key=role_key, company_id=company_id)
        return user

    def channel_scope(self, *, name: str = "Pre-M7") -> SimpleNamespace:
        self.seed_all()
        company = CompanyService(self.session).create_company(name=f"{name} Co")
        operator = self.user(role_key="operator", company_id=company.id, email_prefix="operator")
        admin = self.user(role_key="company_admin", company_id=company.id, email_prefix="admin")
        channel = ChannelWorkspaceService(self.session).create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(key=f"ch-{uuid.uuid4().hex[:8]}", name=f"{name} Channel"),
        )
        profile = ChannelProfileService(self.session).create_profile_version(
            channel_id=channel.id,
            data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
        )
        compiled = ChannelProfileCompiler(self.session).compile(
            profile_version_id=profile.id,
            correlation_id=f"pre-m7-compile-{uuid.uuid4().hex[:8]}",
        )
        snapshot = ChannelProfileService(self.session).activate_snapshot(snapshot_id=compiled.snapshot_id)
        return SimpleNamespace(
            company=company,
            channel=channel,
            profile=profile,
            snapshot=snapshot,
            operator=operator,
            admin=admin,
            compiled=compiled,
        )

    def m2_project(self) -> SimpleNamespace:
        scope = self.channel_scope(name="M2")
        project = VideoProjectService(self.session).create_project(
            data=VideoProjectCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                title="Pre-M7 exact-version workflow",
                description="Qualification fixture project",
                created_by_user_id=scope.operator.id,
            )
        )
        artifact = ArtifactService(self.session).create_artifact(
            data=ArtifactCreate(video_project_id=project.id, artifact_type="script", created_by_user_id=scope.operator.id)
        )
        version = ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content={"title": "v1", "lines": ["hello"]},
                created_by_user_id=scope.operator.id,
                external_entity_refs=[{"type": "brand", "id": "brand-1"}],
                packaging_metadata={"package": "draft"},
                media_qc_metadata={"ai_used": False},
                source_manifest={"rights_basis": "licensed"},
                evidence_refs=[{"type": "manual", "id": "ev-1"}],
                context_refs=[{"type": "context_pack_snapshot", "id": "ctx-1"}],
                claim_refs=[{"type": "claim", "id": "cl-1"}],
            )
        )
        return SimpleNamespace(**scope.__dict__, project=project, artifact=artifact, version=version)

    def m5_admitted_project(
        self,
        *,
        evidence_volume: int | None = 1200,
        mock_mode: str = "success",
        quota_limit: Decimal | None = None,
        provider_health_mode: str | None = None,
    ) -> SimpleNamespace:
        scope = self.channel_scope(name="M5")
        if provider_health_mode is not None:
            ProviderHealthService(self.session).check_provider(provider_key="mock_llm", mode=provider_health_mode)
        quota_account = None
        if quota_limit is not None:
            quota_account = QuotaService(self.session).create_account(
                data=QuotaAccountCreate(
                    provider_key="mock_llm",
                    quota_scope_type="CHANNEL",
                    quota_scope_id=scope.channel.id,
                    quota_window="DAILY",
                    quota_limit=quota_limit,
                    unit="REQUESTS",
                )
            )
        slot = EditorialCalendarService(self.session).create_slot(
            data=EditorialCalendarSlotCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                slot_date=date(2026, 6, 24),
                production_goal="Explain a budgeted VCOS workflow",
                target_platforms=["YOUTUBE"],
                content_pillar="education",
                format_hint="explainer",
                created_by_user_id=scope.operator.id,
            )
        )
        evidence = None
        if evidence_volume is not None:
            evidence = SearchDemandEvidenceService(self.session).create_evidence(
                data=SearchDemandEvidenceCreate(
                    company_id=scope.company.id,
                    channel_workspace_id=scope.channel.id,
                    evidence_source_type="MANUAL_RESEARCH",
                    query="budgeted video workflow",
                    platform="YOUTUBE",
                    search_volume_30d=evidence_volume,
                    relative_interest_index=Decimal("70"),
                    competition_index=Decimal("0.30"),
                    evidence_confidence="MEDIUM",
                )
            )
        daily_run = ChannelDailyRunService(self.session).create_run(
            data=ChannelDailyRunCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                editorial_calendar_slot_id=slot.id,
                run_date=slot.slot_date,
            )
        )
        executed = ChannelDailyRunService(self.session).execute_run(
            daily_run_id=daily_run.id,
            data=DailyRunExecuteRequest(
                mock_mode=mock_mode,
                quota_account_id=quota_account.id if quota_account else None,
                created_by_user_id=scope.operator.id,
            ),
        )
        if executed.status != "COMPLETED":
            return SimpleNamespace(
                **scope.__dict__,
                slot=slot,
                evidence=evidence,
                quota_account=quota_account,
                daily_run=executed,
                idea=None,
                preflight=None,
                admission=None,
                project=None,
            )
        from app.db.models import DailyIdeaDecision

        idea = self.session.get(DailyIdeaDecision, executed.daily_idea_decision_id)
        preflight = IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                channel_daily_run_id=executed.id,
                daily_idea_decision_id=idea.id,
                evidence_blob={"search_demand_evidence_ids": [str(evidence.id)] if evidence is not None else []},
                policy_fit_state="PASS",
            )
        )
        admission = ProjectAdmissionService(self.session).create_decision(
            data=ProjectAdmissionDecisionCreate(
                channel_daily_run_id=executed.id,
                daily_idea_decision_id=idea.id,
                idea_market_preflight_id=preflight.id,
                created_by_user_id=scope.operator.id,
            )
        )
        project = self.session.get(VideoProject, admission.admitted_video_project_id)
        return SimpleNamespace(
            **scope.__dict__,
            slot=slot,
            evidence=evidence,
            quota_account=quota_account,
            daily_run=executed,
            idea=idea,
            preflight=preflight,
            admission=admission,
            project=project,
        )

    def m6_full_flow(self, *, output_dir: Path | None = None, require_completed: bool = True) -> SimpleNamespace:
        flow = self.m5_admitted_project()
        run = ProductionArtifactRunService(self.session).create_run(
            data=ProductionArtifactRunCreate(
                video_project_id=flow.project.id,
                source_project_admission_decision_id=flow.admission.id,
            )
        )
        executed = ProductionArtifactRunService(self.session).execute_local_mock_flow(
            run_id=run.id,
            output_dir=output_dir,
        )
        if require_completed:
            assert executed.status == "COMPLETED"
        return SimpleNamespace(**flow.__dict__, production_run=executed)


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)
