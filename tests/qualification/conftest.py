from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.contracts import (
    ArtifactCreate,
    ArtifactVersionCreate,
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
)
from app.contracts.m5 import (
    EditorialCalendarSlotCreate,
    EditorialIdeaCandidateCreate,
    EditorialIdeaCandidateTransition,
    EditorialResearchRunCreate,
    IdeaMarketPreflightCreate,
    SearchDemandEvidenceCreate,
)
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.vcos_v2 import AssignmentMode, LongFormPlanningRequest
from app.core.actor import authenticated_actor_context
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.m6 import ProductionArtifactRunCreate
from app.contracts.ops import ProviderRegistryEntryCreate
from app.contracts.workflow import VideoProjectCreate
from app.db.models import (
    CaptionTrackSnapshot,
    User,
    VideoProject,
    VisualPlanSnapshot,
    VoiceTimelineSnapshot,
)
from app.services import (
    ArtifactService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    EditorialCalendarService,
    GateDefinitionService,
    IdeaMarketPreflightService,
    ProductionArtifactRunService,
    ProviderRegistryService,
    R3D1AdminService,
    RBACService,
    SearchDemandEvidenceService,
    VideoProjectService,
)
from app.services.editorial_research import EditorialResearchService
from app.services.production_package import ChannelDurationContractResolver
from app.services.ofv0 import FormatIdentityContractService
from app.services.vcos_v2 import LongFormPlanningService

ROOT = Path(__file__).resolve().parents[2]


class QualificationFactory:
    def __init__(self, session):
        self.session = session

    def seed_all(self) -> None:
        ConfigRegistryService(self.session).seed([ROOT / "config"])
        registry = ProviderRegistryService(self.session)
        if registry.get_entry("openai") is None:
            registry.create_entry(
                data=ProviderRegistryEntryCreate(
                    provider_key="openai",
                    provider_name="OpenAI Responses Router",
                    provider_type="LLM",
                    capability_blob={
                        "llm_router_lane_bound": True,
                        "guarded_real_execution": True,
                    },
                    policy_fit_blob={"production_enabled_when_configured": True},
                    metadata={"readiness_provider_key": "openai"},
                )
            )
        GateDefinitionService(self.session).seed_definitions()

    def user(
        self, *, role_key: str = "operator", company_id=None, email_prefix: str = "qual"
    ) -> User:
        user = User(
            email=f"{email_prefix}-{uuid.uuid4().hex[:10]}@example.com",
            display_name=email_prefix,
            status="active",
        )
        self.session.add(user)
        self.session.flush()
        if company_id is not None:
            RBACService(self.session).assign_role(
                user_id=user.id, role_key=role_key, company_id=company_id
            )
        return user

    def channel_scope(
        self,
        *,
        name: str = "Pre-M7",
        strict_long_form: bool = False,
    ) -> SimpleNamespace:
        self.seed_all()
        company = CompanyService(self.session).create_company(name=f"{name} Co")
        operator = self.user(
            role_key="operator", company_id=company.id, email_prefix="operator"
        )
        admin = self.user(
            role_key="company_admin", company_id=company.id, email_prefix="admin"
        )
        channel = ChannelWorkspaceService(self.session).create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(
                key=(
                    "small-team-ai"
                    if strict_long_form
                    else f"ch-{uuid.uuid4().hex[:8]}"
                ),
                name=f"{name} Channel",
            ),
        )
        profile = ChannelProfileService(self.session).create_profile_version(
            channel_id=channel.id,
            data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
        )
        if strict_long_form:
            format_contract = FormatIdentityContractService(self.session).draft(
                FormatIdentityContractDraftRequest(
                    channel_id=channel.id,
                    channel_profile_version_id=profile.id,
                    created_by="QualificationFactory",
                )
            )
            FormatIdentityContractService(self.session).approve(
                format_contract.id,
                decided_by="qualification-operator",
            )
        compiled = ChannelProfileCompiler(self.session).compile(
            profile_version_id=profile.id,
            correlation_id=f"pre-m7-compile-{uuid.uuid4().hex[:8]}",
        )
        profile_service = ChannelProfileService(self.session)
        if strict_long_form:
            profile_service.submit_for_approval(profile.id)
            profile_service.approve_profile_version(
                profile_version_id=profile.id,
                approved_by=admin.id,
                approval_ref="operator-approval://ch1-flex/small-team-ai/profile-v1",
            )
        snapshot = profile_service.activate_snapshot(
            snapshot_id=compiled.snapshot_id
        )
        return SimpleNamespace(
            company=company,
            channel=channel,
            profile=profile,
            snapshot=snapshot,
            operator=operator,
            admin=admin,
            compiled=compiled,
        )

    def m2_project(self, *, scope_name: str = "M2") -> SimpleNamespace:
        scope = self.channel_scope(name=scope_name)
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
            data=ArtifactCreate(
                video_project_id=project.id,
                artifact_type="script",
                created_by_user_id=scope.operator.id,
            )
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
        return SimpleNamespace(
            **scope.__dict__, project=project, artifact=artifact, version=version
        )

    def m5_admitted_project(
        self,
        *,
        evidence_volume: int | None = 1200,
        mock_mode: str = "success",
        quota_limit: Decimal | None = None,
        provider_health_mode: str | None = None,
    ) -> SimpleNamespace:
        """Build the canonical research-candidate-preflight long-form source."""

        scope = self.channel_scope(name="M5", strict_long_form=True)
        permissions = RBACService(self.session).permissions_for_user(
            user_id=scope.operator.id,
            company_id=scope.company.id,
        )
        actor = authenticated_actor_context(
            canonical_user_id=scope.operator.id,
            operator_user_id=scope.operator.id,
            actor_role="PRODUCER",
            permissions=permissions,
        )
        category = R3D1AdminService(self.session).create_content_category(
            ContentCategoryCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                category_key=f"default-{uuid.uuid4().hex[:8]}",
                name="Default Long-form Category",
                sub_niche="small-team automation",
                audience_segment="small professional teams",
                content_pillar="AI automation workflows",
                character_policy_mode="NO_CHARACTER",
                status="ACTIVE",
            )
        )
        slot = EditorialCalendarService(self.session).create_slot(
            data=EditorialCalendarSlotCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                category_id=category.id,
                slot_date=date(2026, 6, 24),
                slot_type="RESEARCH",
                schema_version="v2",
                production_lane="LONG_FORM",
                assignment_mode=AssignmentMode.OPEN_MIX,
                production_goal="Explain a budgeted VCOS workflow",
                target_platforms=["YOUTUBE"],
                content_pillar="AI automation workflows",
                format_hint="long-form explainer",
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
                    geo="US",
                    search_volume_30d=evidence_volume,
                    relative_interest_index=Decimal("70"),
                    competition_index=Decimal("0.30"),
                    evidence_confidence="MEDIUM",
                )
            )
        research = EditorialResearchService(self.session)
        research_run = research.create_run(
            data=EditorialResearchRunCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                channel_profile_version_id=scope.profile.id,
                policy_snapshot_id=scope.snapshot.id,
                editorial_calendar_slot_id=slot.id,
                run_date=slot.slot_date,
                trigger_type="TEST",
                metadata={"provider_execution": "DISABLED"},
            ),
            actor=actor,
        )
        research.start_run(run_id=research_run.id, actor=actor)
        blocked_readiness = (
            (quota_limit is not None and quota_limit <= 0)
            or provider_health_mode == "unavailable"
            or mock_mode != "success"
        )
        candidate = research.add_candidate(
            data=EditorialIdeaCandidateCreate(
                editorial_research_run_id=research_run.id,
                proposed_title="How a Small Team Can Audit One Automation",
                proposed_angle="Evidence-aware long-form operating walkthrough.",
                proposed_format="long-form explainer",
                proposed_pillar="AI automation workflows",
                evidence_refs=[
                    {
                        "type": "search_demand_evidence",
                        "id": str(evidence.id) if evidence else "missing",
                    }
                ],
                confidence_level="MEDIUM",
                budget_readiness="BLOCKED" if blocked_readiness else "READY",
                rights_policy_state="PASS",
                quality_state="BLOCK" if mock_mode != "success" else "PASS",
                experiment_phase="AUDIENCE_PROMISE",
            ),
            actor=actor,
        )
        preflight = IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                editorial_calendar_slot_id=slot.id,
                editorial_research_run_id=research_run.id,
                editorial_idea_candidate_id=candidate.id,
                demand_score=Decimal("60") if evidence else None,
                channel_fit_score=Decimal("0.90"),
                policy_fit_state="PASS",
                niche_contract_digest_ref=f"niche-contract://{scope.channel.id}",
                niche_contract_digest_hash="a" * 64,
                target_market_digest_ref=f"target-market://{scope.channel.id}/US",
                target_market_digest_hash="b" * 64,
                editorial_slot_ref=f"editorial-slot://{slot.id}",
                content_category_ref=str(category.id),
                target_market="US",
                market_scope=["US"],
                market_fit_score=Decimal("0.90"),
                market_fit_threshold=Decimal("0.60"),
                evidence_blob={
                    "search_demand_evidence_ids": [str(evidence.id)]
                    if evidence is not None
                    else []
                },
            )
        )
        if preflight.decision != "PASS" or blocked_readiness:
            if preflight.decision == "PASS":
                research.transition_candidate(
                    candidate_id=candidate.id,
                    data=EditorialIdeaCandidateTransition(
                        target_stage="PREFLIGHT_PASS",
                        idea_market_preflight_id=preflight.id,
                        reason_codes=["STRICT_LONG_FORM_PREFLIGHT_PASS"],
                    ),
                    actor=actor,
                )
            research.complete_run(run_id=research_run.id, actor=actor)
            return SimpleNamespace(
                **scope.__dict__,
                actor=actor,
                category=category,
                slot=slot,
                evidence=evidence,
                quota_account=None,
                research_run=research_run,
                candidate=candidate,
                idea=candidate,
                preflight=preflight,
                admission=None,
                project=None,
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
        research.transition_candidate(
            candidate_id=candidate.id,
            data=EditorialIdeaCandidateTransition(
                target_stage="GREENLIT",
                idea_market_preflight_id=preflight.id,
                reason_codes=["DETERMINISTIC_GREENLIGHT"],
            ),
            actor=actor,
        )
        duration = ChannelDurationContractResolver(self.session).resolve(
            profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            production_lane="LONG_FORM",
        )
        admission = LongFormPlanningService(self.session).admit(
            LongFormPlanningRequest(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                channel_profile_version_id=scope.profile.id,
                policy_snapshot_id=scope.snapshot.id,
                editorial_calendar_slot_id=slot.id,
                editorial_idea_candidate_id=candidate.id,
                idea_market_preflight_id=preflight.id,
                assignment_mode=AssignmentMode.OPEN_MIX,
                title=candidate.proposed_title,
                description=candidate.proposed_angle,
                category_id=category.id,
                niche_gate_passed=True,
                market_gate_passed=True,
                evidence_refs=list(candidate.evidence_refs),
                duration_contract=duration,
                created_by_user_id=scope.operator.id,
            )
        )
        project = self.session.get(VideoProject, admission.admitted_video_project_id)
        assert project is not None
        self._seed_m5_project_artifacts(project=project, actor_user_id=scope.operator.id)
        research.complete_run(run_id=research_run.id, actor=actor)
        return SimpleNamespace(
            **scope.__dict__,
            actor=actor,
            category=category,
            slot=slot,
            evidence=evidence,
            quota_account=None,
            research_run=research_run,
            candidate=candidate,
            idea=candidate,
            preflight=preflight,
            admission=admission,
            project=project,
        )

    def _seed_m5_project_artifacts(self, *, project, actor_user_id) -> None:
        """Provide the admitted project's canonical M5 inputs for M6 tests."""

        artifacts = ArtifactService(self.session)
        for artifact_type in ("creative_brief", "research_pack", "source_pack"):
            artifact = artifacts.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project.id,
                    artifact_type=artifact_type,
                    created_by_user_id=actor_user_id,
                )
            )
            artifacts.create_artifact_version(
                data=ArtifactVersionCreate(
                    artifact_id=artifact.id,
                    content={
                        "artifact_type": artifact_type,
                        "project_id": str(project.id),
                        "source": "qualification-admitted-long-form",
                    },
                    created_by_user_id=actor_user_id,
                    external_entity_refs=[],
                    packaging_metadata={},
                    media_qc_metadata={},
                    source_manifest={"rights_basis": "qualification"},
                    evidence_refs=[],
                    context_refs=[],
                )
            )

    def m6_full_flow(
        self, *, output_dir: Path | None = None, require_completed: bool = True
    ) -> SimpleNamespace:
        flow = self.m5_admitted_project()
        run = ProductionArtifactRunService(self.session).create_run(
            data=ProductionArtifactRunCreate(
                video_project_id=flow.project.id,
                source_project_admission_decision_id=flow.admission.id,
            )
        )
        # Test-only, database-backed long-form timeline input. Provider
        # execution remains disabled in this qualification factory.
        segments = [
            {
                "narration_segment_id": f"segment-{index}",
                "sequence_index": index - 1,
                "text": text,
                "estimated_start_time": float((index - 1) * 8),
                "estimated_end_time": float(index * 8),
            }
            for index, text in enumerate(
                (
                    "How small teams can make a reliable workflow.",
                    "Why canonical timing keeps every production stage aligned.",
                    "A review boundary protects the final publishing decision.",
                ),
                start=1,
            )
        ]
        voice = VoiceTimelineSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=flow.project.id,
            script_artifact_version_id=None,
            policy_snapshot_id=flow.project.policy_snapshot_id,
            timeline_blob={"segments": segments},
            total_duration_seconds=Decimal("24"),
            timing_source="ESTIMATED",
            confidence_level="HIGH",
            timeline_hash="qualification-voice-timeline-hash",
        )
        self.session.add(voice)
        self.session.flush()
        captions = CaptionTrackSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=flow.project.id,
            voice_timeline_snapshot_id=voice.id,
            caption_blob={
                "cues": [
                    {
                        "caption_id": f"caption-{index}",
                        "narration_segment_id": segment["narration_segment_id"],
                        "text": segment["text"],
                    }
                    for index, segment in enumerate(segments, start=1)
                ]
            },
            srt_text=None,
            language="en",
            caption_hash="qualification-caption-track-hash",
        )
        self.session.add(captions)
        self.session.flush()
        visual = VisualPlanSnapshot(
            production_artifact_run_id=run.id,
            video_project_id=flow.project.id,
            voice_timeline_snapshot_id=voice.id,
            caption_track_snapshot_id=captions.id,
            visual_plan_blob={
                "scenes": [
                    {
                        "scene_id": f"scene-{index}",
                        "narration_segment_id": segment["narration_segment_id"],
                    }
                    for index, segment in enumerate(segments, start=1)
                ]
            },
            visual_plan_hash="qualification-visual-plan-hash",
        )
        self.session.add(visual)
        # Materialize the generated snapshot identifier before binding it into
        # the production-run lineage.  SQLAlchemy column defaults are applied
        # on flush, so assigning ``visual.id`` before this point silently
        # persisted a NULL run pointer even though the snapshot itself existed.
        self.session.flush()
        run.voice_timeline_snapshot_id = voice.id
        run.caption_track_snapshot_id = captions.id
        run.visual_plan_snapshot_id = visual.id
        run.status = "COMPLETED"
        run.reason_codes = ["QUALIFICATION_LONG_FORM_INPUT_READY"]
        self.session.flush()
        executed = run
        if require_completed:
            assert executed.status == "COMPLETED"
        return SimpleNamespace(**flow.__dict__, production_run=executed)


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)
