from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import set_committed_value

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.d2p1 import DailyToPackageRequest
from app.contracts.m5 import (
    ContextPackSnapshotCreate,
    EditorialCalendarSlotCreate,
    ProjectAdmissionDecisionCreate,
    RetrievalPlanSnapshotCreate,
)
from app.contracts.nich1 import (
    ChannelFitEvaluation,
    NicheAlignmentDossier,
    NicheDossierScope,
    NicheGateResult,
    nich1_stable_hash,
)
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.workflow import ApprovalDecisionCreate, ArtifactVersionCreate
from app.db.models import (
    Artifact,
    ArtifactVersion,
    ChannelDailyRun,
    ContextPackSnapshot,
    DailyIdeaDecision,
    EffectiveChannelRuntimeContextSnapshot,
    FirstScriptedVideoPackage,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    ProviderAttempt,
    RetrievalPlanSnapshot,
    ReviewTask,
    User,
    VideoProject,
    ChannelProfileVersion,
    CompiledChannelPolicySnapshot,
)
from app.main import create_app
from app.services.channel_profile import ChannelProfileService
from app.services.config_registry import ConfigRegistryService, content_hash
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.services.d2p1 import (
    FINAL_HUMAN_REVIEW_REASON,
    NICHE_GATE_KEYS,
    RECEIPT_ARTIFACT_TYPE,
    RECEIPT_SCHEMA_VERSION,
    RESEARCH_ASSIGNMENT_REASON,
    DailyToPackageOrchestrator,
)
from app.services.m5 import (
    DEFAULT_DAILY_CONTEXT_SOURCES,
    EditorialCalendarService,
    ProjectAdmissionService,
    ResourceResolverService,
    _hash_payload,
    _niche_evidence_ref,
)
from app.services.nich1 import (
    NicheAlignmentDossierBuilder,
    NicheContractDigestCompiler,
    channel_fit_threshold_from_compiled_policy,
)
from app.services.ofv0 import FormatIdentityContractService
from app.services.profile_compiler import ChannelProfileCompiler
from app.services.r3d1 import R3D1AdminService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.rbac import RBACService
from app.services.workflow import ApprovalService, ArtifactService
from app.services import ChannelWorkspaceService, CompanyService


class _OfflinePackageService:
    def __init__(
        self,
        session,
        *,
        failed_gate: str | None = None,
        tampered_digest_gate: str | None = None,
        create_provider_attempt: bool = False,
    ) -> None:
        self.session = session
        self.failed_gate = failed_gate
        self.tampered_digest_gate = tampered_digest_gate
        self.create_provider_attempt = create_provider_attempt
        self.calls = []

    def create(self, data):
        self.calls.append(data)
        project = self.session.get(VideoProject, data.video_project_id)
        effective = self.session.get(
            __import__(
                "app.db.models", fromlist=["EffectiveChannelRuntimeContextSnapshot"]
            ).EffectiveChannelRuntimeContextSnapshot,
            project.effective_context_snapshot_id,
        )
        frozen = project.audience_delivery_summary["d2p1_frozen_lineage"]
        digest_ref = frozen["niche_contract_digest_ref"]
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, project.policy_snapshot_id
        )
        package_id = uuid.uuid4()
        gate_results = {}
        for gate_key in NICHE_GATE_KEYS[1:]:
            status = "BLOCK" if gate_key == self.failed_gate else "PASS"
            subject = {
                "package_id": str(package_id),
                "project_id": str(project.id),
                "gate_key": gate_key,
            }
            stable = {
                "gate_key": gate_key,
                "verdict": status,
                "reason_codes": (
                    [] if status == "PASS" else ["SEMANTIC_ALIGNMENT_BLOCKED"]
                ),
                "checks": [
                    {
                        "check_key": f"offline_fixture.{gate_key}",
                        "verdict": status,
                        "reason_codes": (
                            []
                            if status == "PASS"
                            else ["SEMANTIC_ALIGNMENT_BLOCKED"]
                        ),
                        "details": {"offline_fixture": True},
                    }
                ],
                "niche_contract_digest_ref": digest_ref["ref"],
                "niche_contract_digest_hash": (
                    "f" * 64
                    if gate_key == self.tampered_digest_gate
                    else digest_ref["content_hash"]
                ),
                "subject_ref": (
                    f"first-scripted-video-package://{package_id}#"
                    f"{gate_key}"
                ),
                "subject_hash": content_hash(subject),
                "checked_policy_snapshot_ref": (
                    f"compiled-policy-snapshot://{snapshot.id}"
                ),
                "checked_policy_snapshot_hash": snapshot.content_hash,
                "evidence_refs": [],
                "human_review_required": False,
                "summary": f"Offline deterministic {gate_key} {status} result.",
            }
            gate_results[gate_key] = NicheGateResult.model_validate(
                {**stable, "content_hash": nich1_stable_hash(stable)}
            ).model_dump(mode="json")
        package = FirstScriptedVideoPackage(
            id=package_id,
            video_project_id=project.id,
            channel_id=project.channel_workspace_id,
            channel_profile_version_id=project.channel_profile_version_id,
            compiled_policy_snapshot_id=project.policy_snapshot_id,
            effective_context_snapshot_id=effective.id,
            effective_context_hash=effective.context_hash,
            provider_readiness_snapshot_id=None,
            package_status="READY_FOR_HUMAN_REVIEW",
            agent_run_refs=[],
            prompt_render_run_refs=[],
            prompt_audit_snapshot_refs=[],
            artifacts={
                "niche_gate_results": gate_results,
                "narration_script": {
                    "script": (
                        "One approved script anchors the workflow. Local stock motion shows operational context. "
                        "Native overlays keep generated visuals accurate and reviewable."
                    )
                },
                "visual_plan": {"authority": "OFFLINE_D2P_FIXTURE", "scene_count": 3},
                "provider_execution_plan": {"mode": "MR1_GATED", "provider_calls_allowed": False},
                "cost_estimate_snapshot": {"currency": "USD", "estimated_total": "0", "fixture": True},
            },
            limitations=["offline deterministic D2P1 fixture"],
            risk_limitations_summary={"no_media": True, "human_review_required": True},
            next_action="Human review required.",
        )
        self.session.add(package)
        if self.create_provider_attempt:
            self.session.add(
                ProviderAttempt(
                    provider_key="forbidden-d2p-test",
                    operation_key="forbidden_package_build",
                    target_type="first_scripted_video_package",
                    target_id=package.id,
                    attempt_number=1,
                    status="SUCCESS",
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    metadata_={"provider_call": True},
                )
            )
        self.session.flush()
        return SimpleNamespace(id=package.id)


def _user(db_session, *, prefix: str) -> User:
    user = User(
        email=f"{prefix}-{uuid.uuid4().hex[:10]}@example.com",
        display_name=prefix,
        status="active",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _d2p_scope(db_session) -> SimpleNamespace:
    ConfigRegistryService(db_session).seed(["config"])
    company = CompanyService(db_session).create_company(name="D2P1 Co")
    operator = _user(db_session, prefix="d2p-operator")
    admin = _user(db_session, prefix="d2p-admin")
    RBACService(db_session).assign_role(
        user_id=operator.id, role_key="operator", company_id=company.id
    )
    RBACService(db_session).assign_role(
        user_id=admin.id, role_key="company_admin", company_id=company.id
    )
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="small-team-ai", name="Small Team AI"),
    )

    compiler = ChannelProfileCompiler(db_session)
    profile_input, _ = compiler.profile_input_from_template("saas_digital_leverage")
    profile_input = profile_input.model_copy(
        update={
            "policies": {
                **profile_input.policies,
                "channel_contract": {
                    "channel_identity": {
                        "channel_name": "Small Team AI",
                        "channel_type": "YOUTUBE_CHANNEL",
                        "niche": "Practical AI automation for small professional teams",
                        "positioning": "Evidence-aware AI operations for lean teams",
                        "brand_promise": (
                            "Turn repeated work into bounded, auditable workflows"
                        ),
                        "primary_platform": "YouTube",
                        "series_plan": profile_input.series_plan,
                    },
                    "target_audience": {
                        "primary_persona": "small-team operators",
                        "audience_level": "semi_technical",
                        "pain_points": ["manual repetitive work"],
                        "desired_outcome": "reliable auditable automation",
                    },
                    "market_locale": {
                        "primary_market": "US",
                        "audience_locale": "en-US",
                        "content_language": "en",
                        "operator_language": "en",
                        "timezone": "America/New_York",
                    },
                    "editorial_strategy": {
                        "content_pillars": profile_input.content_pillars,
                        "allowed_topics": [
                            "AI automation workflow",
                            "small-team operations",
                        ],
                        "forbidden_topics": [
                            "crypto trading",
                            "medical guarantees",
                        ],
                    },
                    "format_policy": {
                        "long_form": {
                            "enabled": True,
                            "target_duration_minutes": {"min": 6, "max": 12},
                            "structure": [
                                "hook",
                                "problem",
                                "mechanism",
                                "result",
                                "takeaway",
                            ],
                            "chapters_required": True,
                        },
                        "shorts": {"enabled": False},
                    },
                    "voice_style": {
                        "narration_tone": "calm practical documentary explainer",
                        "pacing": "measured",
                        "allowed_style": ["evidence-aware"],
                        "forbidden_style": ["hype"],
                    },
                    "platform_strategy": {
                        "primary_platform": "YouTube",
                        "publish_mode": "human_handoff_only",
                        "auto_publish_allowed": False,
                        "studio_scraping_allowed": False,
                    },
                    "media_policy": {
                        "renderer": "NativeFFmpegRenderer",
                        "niche_visual_source_profile": "STOCK_ASSISTED",
                        "ai_hero_audio": False,
                    },
                    "rights_policy": {"source_manifest_required": True},
                    "learning_policy": {
                        "authority": "youtube_analytics_only",
                        "auto_promote_learning": False,
                        "config_mutation_by_agent_allowed": False,
                    },
                },
            }
        }
    )
    profile_v1 = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(
            profile_input=profile_input, created_by=operator.id
        ),
    )
    format_contract = FormatIdentityContractService(db_session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile_v1.id,
            created_by="ChannelAuthorityAgent",
        )
    )
    FormatIdentityContractService(db_session).approve(
        format_contract.id, decided_by="human-operator"
    )
    compiled_v1 = compiler.compile(
        profile_version_id=profile_v1.id,
        correlation_id="d2p1-profile-v1",
    )
    ChannelProfileService(db_session).approve_profile_version(
        profile_version_id=profile_v1.id,
        approved_by=admin.id,
        approval_ref="d2p1-offline-v1-fixture",
    )
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=compiled_v1.snapshot_id)
    activated_v2 = ChannelProfileService(db_session).approve_and_activate_ch1_flex_v2(
        channel_id=channel.id,
        approved_by=admin.id,
        approval_ref=(
            "operator-approval://ch1-flex-v2/small-team-ai/"
            "master-prompt-2026-07-19"
        ),
        correlation_id="d2p1-ch1-flex-v2",
    )
    profile_v2 = db_session.get(
        ChannelProfileVersion, uuid.UUID(activated_v2["channel_profile_version_id"])
    )
    snapshot = db_session.get(
        CompiledChannelPolicySnapshot,
        uuid.UUID(activated_v2["compiled_policy_snapshot_id"]),
    )

    pillar = "AI automation workflows"
    category = R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            category_key="workflow-automation",
            name="Workflow Automation",
            sub_niche="AI workflow automation for small teams",
            audience_segment="small-team operators",
            content_pillar=pillar,
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
        )
    )
    slot = EditorialCalendarService(db_session).create_slot(
        data=EditorialCalendarSlotCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            category_id=category.id,
            slot_date=date(2026, 7, 19),
            slot_type="DAILY",
            production_goal="Explain a reliable AI automation workflow for a small team",
            target_platforms=["YOUTUBE"],
            content_pillar=pillar,
            series_key="workflow_teardown",
            format_hint="screen-led explainer",
            created_by_user_id=operator.id,
        )
    )

    digest = NicheContractDigestCompiler().compile(
        channel=channel,
        profile_version=profile_v2,
        policy_snapshot=snapshot,
        category=category,
        editorial_slot=slot,
    )

    plan_payload = {
        "purpose": "DAILY_IDEA",
        "company_id": str(company.id),
        "channel_workspace_id": str(channel.id),
        "channel_profile_version_id": str(profile_v2.id),
        "policy_snapshot_id": str(snapshot.id),
        "editorial_calendar_slot_id": str(slot.id),
    }
    plan = RetrievalPlanSnapshot(
        purpose="DAILY_IDEA",
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile_v2.id,
        policy_snapshot_id=snapshot.id,
        video_project_id=None,
        editorial_calendar_slot_id=slot.id,
        allowed_sources=["channel_profile", "policy_snapshot", "editorial_slot"],
        excluded_sources=[],
        redaction_rules={},
        token_budget=4000,
        source_order=["channel_profile", "policy_snapshot", "editorial_slot"],
        plan_hash=content_hash(plan_payload),
        created_by_user_id=operator.id,
    )
    db_session.add(plan)
    db_session.flush()
    digest_json = digest.model_dump(mode="json")
    context_pack_id = uuid.uuid4()
    digest_ref = digest.editorial_slot_ref + "#niche_contract_digest"
    pack_content = {
        "niche_contract_digest": digest_json,
        "niche_contract_digest_ref": {
            "type": "niche_contract_digest",
            "ref": digest_ref,
            "content_hash": digest.content_hash,
        },
        "editorial_slot_digest": {
            "category_id": str(category.id),
            "content_pillar_key": pillar,
            "series_key": slot.series_key,
            "production_goal": slot.production_goal,
            "active_policy_snapshot_ref": f"compiled-policy-snapshot://{snapshot.id}",
            "active_policy_snapshot_hash": snapshot.content_hash,
        },
        "runtime_guard_digest": {
            "compiled_policy_snapshot_id": str(snapshot.id),
            "compiled_policy_snapshot_hash": snapshot.content_hash,
            "provider_calls_allowed": False,
            "direct_provider_sdk_allowed": False,
        },
        "agent_context_pack": {
            "agent_key": "DailyIdeaAgent",
            "digests": {"niche_contract_digest": digest_json},
        },
    }
    policy_refs = [
        {
            "type": "niche_contract_digest_authority",
            "compiled_policy_snapshot_id": str(snapshot.id),
            "content_hash": digest.content_hash,
        }
    ]
    context_pack_hash_payload = {
        "input_refs": [],
        "policy_refs": policy_refs,
        "evidence_refs": [{"type": "manual_research", "id": "d2p-evidence"}],
        "metric_refs": [],
        "memory_refs": [],
        "pack_content": pack_content,
    }
    context_pack = ContextPackSnapshot(
        id=context_pack_id,
        retrieval_plan_snapshot_id=plan.id,
        purpose="DAILY_IDEA",
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile_v2.id,
        policy_snapshot_id=snapshot.id,
        video_project_id=None,
        editorial_calendar_slot_id=slot.id,
        input_refs=context_pack_hash_payload["input_refs"],
        policy_refs=context_pack_hash_payload["policy_refs"],
        evidence_refs=context_pack_hash_payload["evidence_refs"],
        metric_refs=context_pack_hash_payload["metric_refs"],
        memory_refs=context_pack_hash_payload["memory_refs"],
        pack_content=pack_content,
        freshness_state="FRESH",
        confidence_level="HIGH",
        pack_hash=_hash_payload(context_pack_hash_payload),
        created_by_user_id=operator.id,
    )
    db_session.add(context_pack)
    db_session.flush()

    daily_run = ChannelDailyRun(
        company_id=company.id,
        channel_workspace_id=channel.id,
        policy_snapshot_id=snapshot.id,
        editorial_calendar_slot_id=slot.id,
        run_date=slot.slot_date,
        status="COMPLETED",
        run_mode="REAL_DISABLED",
        trigger_type="TEST",
        context_pack_snapshot_id=context_pack.id,
        reason_codes=[],
        metadata_={"offline_fixture": True},
    )
    db_session.add(daily_run)
    db_session.flush()
    decision = DailyIdeaDecision(
        channel_daily_run_id=daily_run.id,
        company_id=company.id,
        channel_workspace_id=channel.id,
        policy_snapshot_id=snapshot.id,
        context_pack_snapshot_id=context_pack.id,
        channel_state_pack_snapshot_id=None,
        llm_run_snapshot_id=None,
        decision_status="PROPOSED",
        proposed_title="AI automation workflow for a five-person support team",
        proposed_angle="Show the mechanism, handoff, and evidence boundary",
        proposed_format="screen-led explainer",
        proposed_pillar=pillar,
        proposed_series_key=slot.series_key,
        rationale={"authoritative_lineage": True},
        evidence_refs=[{"type": "manual_research", "id": "d2p-evidence"}],
        reason_codes=["IDEA_ADMITTED"],
        confidence_level="HIGH",
    )
    db_session.add(decision)
    db_session.flush()
    daily_run.daily_idea_decision_id = decision.id
    preflight_evidence_refs = [
        {"type": "manual_research", "id": "d2p-evidence"}
    ]
    subject_ref = DailyToPackageOrchestrator._decision_subject_ref(
        decision,
        evidence_refs=preflight_evidence_refs,
    )
    typed_idea_evidence = {
        "type": subject_ref["type"],
        "ref": subject_ref["ref"],
        "content_hash": subject_ref["content_hash"],
    }
    topic_gate_payload = {
        "gate_key": "topic_niche_alignment_gate",
        "verdict": "PASS",
        "reason_codes": [],
        "checks": [
            {
                "check_key": "offline_fixture.topic_alignment",
                "verdict": "PASS",
                "reason_codes": [],
                "details": {"offline_fixture": True},
            }
        ],
        "niche_contract_digest_ref": digest_ref,
        "niche_contract_digest_hash": digest.content_hash,
        "subject_ref": f"daily-idea-decision://{decision.id}",
        "subject_hash": subject_ref["content_hash"],
        "checked_policy_snapshot_ref": f"compiled-policy-snapshot://{snapshot.id}",
        "checked_policy_snapshot_hash": snapshot.content_hash,
        "evidence_refs": [typed_idea_evidence],
        "human_review_required": False,
        "summary": "Offline deterministic topic alignment passed.",
    }
    topic_gate = NicheGateResult.model_validate(
        {
            **topic_gate_payload,
            "content_hash": nich1_stable_hash(topic_gate_payload),
        }
    ).model_dump(mode="json")
    fit_evaluation = {
        "channel_fit_score": 0.92,
        "channel_fit_threshold": channel_fit_threshold_from_compiled_policy(snapshot),
        "channel_fit_result": "PASS",
        "policy_fit_state": "PASS",
        "reason_codes": [],
        "evidence_refs": [typed_idea_evidence],
        "required_gate_keys": ["topic_niche_alignment_gate"],
        "gate_result_hashes": {
            "topic_niche_alignment_gate": topic_gate["content_hash"],
        },
        "caller_policy_fit_state_ignored": "PASS",
    }
    fit_evaluation = ChannelFitEvaluation.model_validate(
        {
            **fit_evaluation,
            "content_hash": nich1_stable_hash(fit_evaluation),
        }
    ).model_dump(mode="json")
    pre_admission_dossier = NicheAlignmentDossierBuilder().build(
        digest=digest,
        digest_ref=digest_ref,
        gate_results=[NicheGateResult.model_validate(topic_gate)],
        channel_fit=ChannelFitEvaluation.model_validate(fit_evaluation),
        dossier_scope=NicheDossierScope.PRE_ADMISSION,
    ).model_dump(mode="json")
    preflight = IdeaMarketPreflight(
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_daily_run_id=daily_run.id,
        daily_idea_decision_id=decision.id,
        search_intent_map_id=None,
        audience_target_pack_id=None,
        demand_score=Decimal("0.85"),
        channel_fit_score=Decimal("0.92"),
        policy_fit_state="PASS",
        confidence_state="HIGH",
        evidence_blob={
            "channel_fit_evaluation": fit_evaluation,
            "channel_fit_gate": fit_evaluation,
            "channel_fit_result": "PASS",
            "topic_niche_alignment_gate": topic_gate,
            "niche_alignment_dossier": pre_admission_dossier,
            "evidence_refs": preflight_evidence_refs,
        },
        reason_codes=[],
        decision="PASS",
    )
    db_session.add(preflight)
    db_session.flush()
    return SimpleNamespace(
        company=company,
        operator=operator,
        admin=admin,
        channel=channel,
        profile=profile_v2,
        snapshot=snapshot,
        category=category,
        slot=slot,
        digest=digest,
        context_pack=context_pack,
        daily_run=daily_run,
        decision=decision,
        preflight=preflight,
    )


def _approve_research(
    db_session,
    scope,
    *,
    content_overrides: dict | None = None,
) -> ArtifactVersion:
    admission = db_session.scalars(
        select(ProjectAdmissionDecision).where(
            ProjectAdmissionDecision.daily_idea_decision_id == scope.decision.id
        )
    ).one()
    artifact = db_session.scalars(
        select(Artifact)
        .where(Artifact.video_project_id == admission.admitted_video_project_id)
        .where(Artifact.artifact_type == "research_pack")
    ).one()
    current = db_session.get(ArtifactVersion, artifact.current_version_id)
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    project_ref = DailyToPackageOrchestrator._project_ref(project)
    research_content = {
        "schema_version": "d2p1.approved-research-fixture.v1",
        "daily_idea_decision_id": str(scope.decision.id),
        "video_project_id": str(project.id),
        "video_project_ref": project_ref,
        "topic": scope.decision.proposed_title,
        "category_id": str(scope.category.id),
        "content_pillar_key": scope.slot.content_pillar,
        "niche_contract_digest_hash": scope.digest.content_hash,
        "findings": ["Small-team automation needs explicit human handoff and evidence."],
        "source_refs": [{"type": "manual_research", "id": "d2p-evidence"}],
    }
    research_content.update(content_overrides or {})
    version = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            parent_version_id=current.id,
            content=research_content,
            status="submitted",
            created_by_user_id=scope.operator.id,
            evidence_refs=[{"type": "manual_research", "id": "d2p-evidence"}],
        )
    )
    ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=version.id,
            target_artifact_version_id=version.id,
            decision="approved",
            decided_by_user_id=scope.admin.id,
            rationale="Offline exact-version D2P1 acceptance fixture.",
            evidence_basis={"source_refs": ["d2p-evidence"]},
        )
    )
    return version


def test_request_rejects_caller_topic_override() -> None:
    with pytest.raises(ValidationError):
        DailyToPackageRequest.model_validate(
            {"daily_idea_decision_id": uuid.uuid4(), "topic": "caller override"}
        )


def test_m5_and_d2p_share_exact_daily_idea_subject_hash_contract() -> None:
    decision = SimpleNamespace(
        id=uuid.uuid4(),
        policy_snapshot_id=uuid.uuid4(),
        proposed_title="Small-team AI handoff",
        proposed_angle="Show the mechanism and evidence boundary",
        proposed_pillar="AI automation workflows",
        proposed_series_key="workflow_teardown",
        rationale={"authoritative_lineage": True},
    )
    evidence_refs = [{"type": "manual_research", "id": "canonical-evidence"}]

    m5_ref = _niche_evidence_ref(idea=decision, evidence_refs=evidence_refs)
    d2p_ref = DailyToPackageOrchestrator._decision_subject_ref(
        decision,
        evidence_refs=evidence_refs,
    )

    assert d2p_ref["type"] == m5_ref.type
    assert d2p_ref["ref"] == m5_ref.ref
    assert d2p_ref["content_hash"] == m5_ref.content_hash


def test_production_handoff_surface_is_read_only() -> None:
    operation = create_app().openapi()["paths"][
        "/daily-idea-decisions/{decision_id}/production-handoff"
    ]
    assert set(operation) == {"get"}


def test_status_exposes_earliest_blocked_admission_without_writes(db_session) -> None:
    scope = _d2p_scope(db_session)
    admission = ProjectAdmissionDecision(
        channel_daily_run_id=scope.daily_run.id,
        daily_idea_decision_id=scope.decision.id,
        idea_market_preflight_id=scope.preflight.id,
        budget_gate_result={"decision": "PASS"},
        readiness_gate_refs=[],
        decision="BLOCK",
        reason_codes=["NICH1_PREFLIGHT_SCOPE_MISMATCH", "IDEA_BLOCKED"],
        evidence_refs=[],
        admitted_video_project_id=None,
        created_artifact_refs=[],
        created_by_user_id=scope.operator.id,
    )
    db_session.add(admission)
    db_session.flush()
    artifact_count = db_session.scalar(select(func.count()).select_from(Artifact))
    admission_count = db_session.scalar(
        select(func.count()).select_from(ProjectAdmissionDecision)
    )

    status = DailyToPackageOrchestrator(db_session).status(scope.decision.id)

    assert status.current_state == "BLOCKED_POLICY"
    assert status.human_review_state == "BLOCKED"
    assert status.blockers == [
        "PROJECT_ADMISSION_NOT_ADMITTED:BLOCK:"
        "NICH1_PREFLIGHT_SCOPE_MISMATCH,IDEA_BLOCKED"
    ]
    assert "new DailyIdeaDecision/admission version" in status.exact_next_action
    assert db_session.scalar(select(func.count()).select_from(Artifact)) == artifact_count
    assert (
        db_session.scalar(select(func.count()).select_from(ProjectAdmissionDecision))
        == admission_count
    )


def test_resume_to_package_is_exact_version_idempotent_and_provider_free(db_session) -> None:
    scope = _d2p_scope(db_session)
    package_service = _OfflinePackageService(db_session)
    orchestrator = DailyToPackageOrchestrator(
        db_session, package_service=package_service
    )
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )

    first = orchestrator.run(request)
    assert first.current_state == "AWAITING_RESEARCH", first.model_dump(mode="json")
    db_session.refresh(scope.decision)
    assert scope.decision.decision_status == "PROPOSED"
    admission = db_session.scalar(
        select(ProjectAdmissionDecision).where(
            ProjectAdmissionDecision.daily_idea_decision_id == scope.decision.id
        )
    )
    assert admission is not None
    assert admission.decision == "ADMIT"
    assert first.research["assignment"] is not None
    first_receipt_version_id = first.receipt["artifact_version_id"]
    project_count = db_session.scalar(select(func.count()).select_from(VideoProject))
    assignment_count = db_session.scalar(
        select(func.count())
        .select_from(ReviewTask)
        .where(ReviewTask.review_reason_codes.contains([RESEARCH_ASSIGNMENT_REASON]))
    )

    second = orchestrator.run(request)
    assert second.current_state == "AWAITING_RESEARCH"
    assert second.receipt["artifact_version_id"] == first_receipt_version_id
    assert db_session.scalar(select(func.count()).select_from(VideoProject)) == project_count
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(ReviewTask.review_reason_codes.contains([RESEARCH_ASSIGNMENT_REASON]))
        )
        == assignment_count
    )
    assert package_service.calls == []

    research = _approve_research(db_session, scope)
    third = orchestrator.run(request)
    assert third.current_state == "PACKAGE_READY_FOR_HUMAN_REVIEW"
    assert third.human_review_state == "PENDING"
    assert third.research["pack"]["id"] == str(research.id)
    assert set(third.niche_gates) == set(NICHE_GATE_KEYS)
    assert all(item["status"] == "PASS" for item in third.niche_gates.values())
    assert all(
        item["niche_contract_digest_hash"] == scope.digest.content_hash
        and item["checked_policy_snapshot_hash"] == scope.snapshot.content_hash
        and item["package_id"] == third.package["id"]
        and item["content_hash"]
        == content_hash(
            {key: value for key, value in item.items() if key != "content_hash"}
        )
        for item in third.niche_gates.values()
    )
    assert third.provider_calls_made == 0
    assert third.media_calls_made == 0
    assert len(package_service.calls) == 1
    assert package_service.calls[0].topic == scope.decision.proposed_title
    assert package_service.calls[0].no_media is True
    assert package_service.calls[0].human_review_only is True
    persisted_package = db_session.get(
        FirstScriptedVideoPackage, uuid.UUID(third.package["id"])
    )
    dossier = NicheAlignmentDossier.model_validate(
        persisted_package.artifacts["niche_alignment_dossier"]
    )
    assert dossier.dossier_scope.value == "PRODUCTION_PACKAGE"
    assert dossier.overall_verdict.value == "PASS"
    assert {key.value for key in dossier.completed_gate_keys} == set(NICHE_GATE_KEYS)
    dossier_binding = persisted_package.artifacts[
        "d2p1_niche_alignment_dossier_binding"
    ]
    assert dossier_binding["dossier_content_hash"] == dossier.content_hash
    assert dossier_binding["package_id"] == str(persisted_package.id)
    assert dossier_binding["content_hash"] == content_hash(
        {
            key: value
            for key, value in dossier_binding.items()
            if key != "content_hash"
        }
    )
    execution_proof = persisted_package.artifacts["d2p1_authoritative_lineage"][
        "zero_execution_boundary"
    ]
    assert execution_proof["zero_execution_confirmed"] is True
    assert execution_proof["provider_calls_made"] == 0
    assert execution_proof["media_calls_made"] == 0
    assert set(execution_proof["record_deltas"]) == {
        "provider_attempts",
        "provider_job_snapshots",
        "paid_provider_call_ledger",
        "media_render_jobs",
        "final_media_refs",
        "drive_media_offload_jobs",
        "drive_cloud_media_refs",
        "youtube_human_upload_tasks",
        "youtube_uploaded_videos",
    }

    ready_receipt_id = third.receipt["artifact_version_id"]
    fourth = orchestrator.run(request)
    assert fourth.current_state == "PACKAGE_READY_FOR_HUMAN_REVIEW"
    assert fourth.receipt["artifact_version_id"] == ready_receipt_id
    assert len(package_service.calls) == 1
    assert db_session.scalar(select(func.count()).select_from(VideoProject)) == project_count
    assert db_session.scalar(select(func.count()).select_from(FirstScriptedVideoPackage)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(ReviewTask.review_reason_codes.contains([FINAL_HUMAN_REVIEW_REASON]))
        )
        == 1
    )
    receipt_artifacts = db_session.scalars(
        select(Artifact)
        .where(Artifact.artifact_type == RECEIPT_ARTIFACT_TYPE)
        .where(Artifact.video_project_id == uuid.UUID(third.project["id"]))
    ).all()
    assert sum(
        1
        for artifact in receipt_artifacts
        if (
            db_session.get(ArtifactVersion, artifact.current_version_id).content or {}
        ).get("schema_version")
        == RECEIPT_SCHEMA_VERSION
    ) == 1

    status = orchestrator.status(scope.decision.id)
    assert status.current_state == "PACKAGE_READY_FOR_HUMAN_REVIEW"
    assert status.receipt == fourth.receipt

    review = db_session.scalars(
        select(ReviewTask).where(
            ReviewTask.target_artifact_version_id == uuid.UUID(ready_receipt_id),
            ReviewTask.review_type == "final_human",
        )
    ).one()
    review.status = "completed"
    ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=uuid.UUID(ready_receipt_id),
            target_artifact_version_id=uuid.UUID(ready_receipt_id),
            decision="approved",
            decided_by_user_id=scope.admin.id,
            rationale="Exact D2P package receipt passed fixture human review.",
            evidence_basis={"review_task_id": str(review.id)},
        )
    )
    promoted = orchestrator.run(request)
    assert promoted.current_state == "READY_FOR_LONG_PRODUCTION"
    assert promoted.human_review_state == "PASS"
    assert promoted.package_human_review["reviewed_artifact_version_id"] == ready_receipt_id
    resumed = orchestrator.run(request)
    assert resumed.current_state == "READY_FOR_LONG_PRODUCTION"
    assert resumed.receipt["artifact_version_id"] == promoted.receipt["artifact_version_id"]


def test_daily_to_package_runtime_post_is_real_idempotent_application_wiring(db_session) -> None:
    scope = _d2p_scope(db_session)
    db_session.commit()
    client = create_app()
    from fastapi.testclient import TestClient

    with TestClient(client) as api:
        first = api.post(
            f"/daily-idea-decisions/{scope.decision.id}/production-handoff/run",
            json={},
        )
        second = api.post(
            f"/daily-idea-decisions/{scope.decision.id}/production-handoff/run",
            json={},
        )
        rejected_override = api.post(
            f"/daily-idea-decisions/{scope.decision.id}/production-handoff/run",
            json={"topic": "forbidden caller override"},
        )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["current_state"] == "AWAITING_RESEARCH"
    assert second.json()["receipt"]["artifact_version_id"] == first.json()["receipt"]["artifact_version_id"]
    assert rejected_override.status_code == 422


def test_failed_downstream_niche_gate_blocks_human_review(db_session) -> None:
    scope = _d2p_scope(db_session)
    initial = DailyToPackageOrchestrator(
        db_session, package_service=_OfflinePackageService(db_session)
    ).run(
        DailyToPackageRequest(
            daily_idea_decision_id=scope.decision.id,
            created_by_user_id=scope.operator.id,
        )
    )
    assert initial.current_state == "AWAITING_RESEARCH", initial.model_dump(mode="json")
    _approve_research(db_session, scope)
    blocked_service = _OfflinePackageService(
        db_session, failed_gate="metadata_niche_alignment_gate"
    )
    blocked = DailyToPackageOrchestrator(
        db_session, package_service=blocked_service
    ).run(
        DailyToPackageRequest(
            daily_idea_decision_id=scope.decision.id,
            created_by_user_id=scope.operator.id,
        )
    )
    assert blocked.current_state == "BLOCKED_POLICY"
    assert blocked.human_review_state == "BLOCKED"
    assert any("metadata_niche_alignment_gate" in item for item in blocked.blockers)
    blocked_package = db_session.get(
        FirstScriptedVideoPackage, uuid.UUID(blocked.package["id"])
    )
    blocked_dossier = NicheAlignmentDossier.model_validate(
        blocked_package.artifacts["niche_alignment_dossier"]
    )
    assert blocked_dossier.dossier_scope.value == "PRODUCTION_PACKAGE"
    assert blocked_dossier.overall_verdict.value == "BLOCK"
    assert {key.value for key in blocked_dossier.completed_gate_keys} == set(
        NICHE_GATE_KEYS
    )
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0


def test_exact_ch1_flex_v2_schema_is_required_before_initial_admission(
    db_session,
) -> None:
    scope = _d2p_scope(db_session)
    tampered = deepcopy(scope.snapshot.compiled_payload)
    tampered["channel_scoped_policy"]["policy_version"] = (
        "small-team-ai.channel-policy.v2-lookalike"
    )
    # Model a corrupt row loaded from storage without attempting to update the
    # production-immutable snapshot itself.
    set_committed_value(scope.snapshot, "compiled_payload", tampered)

    blocked = DailyToPackageOrchestrator(
        db_session, package_service=_OfflinePackageService(db_session)
    ).run(
        DailyToPackageRequest(
            daily_idea_decision_id=scope.decision.id,
            created_by_user_id=scope.operator.id,
        )
    )

    assert blocked.current_state == "BLOCKED_POLICY"
    assert blocked.blockers == ["CH1_FLEX_V2_EXACT_POLICY_SCHEMA_REQUIRED"]
    assert db_session.scalar(select(func.count()).select_from(VideoProject)) == 0
    counts_before_status = (
        db_session.scalar(select(func.count()).select_from(VideoProject)),
        db_session.scalar(select(func.count()).select_from(ProjectAdmissionDecision)),
        db_session.scalar(select(func.count()).select_from(ArtifactVersion)),
    )
    status = DailyToPackageOrchestrator(db_session).status(scope.decision.id)
    assert status.current_state == "BLOCKED_POLICY"
    assert status.blockers == ["CH1_FLEX_V2_EXACT_POLICY_SCHEMA_REQUIRED"]
    assert (
        db_session.scalar(select(func.count()).select_from(VideoProject)),
        db_session.scalar(select(func.count()).select_from(ProjectAdmissionDecision)),
        db_session.scalar(select(func.count()).select_from(ArtifactVersion)),
    ) == counts_before_status


def test_effective_context_block_rolls_back_candidate_project_and_is_idempotent(
    db_session,
    monkeypatch,
) -> None:
    scope = _d2p_scope(db_session)

    def _blocked_compile(self, *, project, editorial_calendar_slot=None):
        return SimpleNamespace(compile_status="BLOCK")

    monkeypatch.setattr(
        EffectiveChannelRuntimeContextCompiler,
        "compile_for_project",
        _blocked_compile,
    )
    counts_before = {
        "projects": db_session.scalar(select(func.count()).select_from(VideoProject)),
        "artifacts": db_session.scalar(select(func.count()).select_from(Artifact)),
        "effective": db_session.scalar(
            select(func.count()).select_from(EffectiveChannelRuntimeContextSnapshot)
        ),
    }
    request = ProjectAdmissionDecisionCreate(
        channel_daily_run_id=scope.daily_run.id,
        daily_idea_decision_id=scope.decision.id,
        idea_market_preflight_id=scope.preflight.id,
        category_id=scope.category.id,
        created_by_user_id=scope.operator.id,
    )

    first = ProjectAdmissionService(db_session).create_decision(data=request)
    second = ProjectAdmissionService(db_session).create_decision(data=request)

    assert first.id == second.id
    assert first.decision == "BLOCK"
    assert first.admitted_video_project_id is None
    assert first.created_artifact_refs == []
    assert "EFFECTIVE_CONTEXT_NOT_PASS" in first.reason_codes
    assert "CANDIDATE_PROJECT_ROLLED_BACK" in first.reason_codes
    assert scope.decision.decision_status == "PROPOSED"
    assert {
        "projects": db_session.scalar(select(func.count()).select_from(VideoProject)),
        "artifacts": db_session.scalar(select(func.count()).select_from(Artifact)),
        "effective": db_session.scalar(
            select(func.count()).select_from(EffectiveChannelRuntimeContextSnapshot)
        ),
    } == counts_before


def test_approved_research_must_bind_all_frozen_lineage_dimensions(db_session) -> None:
    scope = _d2p_scope(db_session)
    orchestrator = DailyToPackageOrchestrator(
        db_session, package_service=_OfflinePackageService(db_session)
    )
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )
    assert orchestrator.run(request).current_state == "AWAITING_RESEARCH"
    _approve_research(
        db_session,
        scope,
        content_overrides={"niche_contract_digest_hash": "0" * 64},
    )

    blocked = orchestrator.run(request)

    assert blocked.current_state == "BLOCKED_POLICY"
    assert any(
        "RESEARCH_CONTENT_FROZEN_LINEAGE_MISMATCH:digest" in blocker
        for blocker in blocked.blockers
    )


def test_downstream_gate_digest_binding_mismatch_blocks_dossier(db_session) -> None:
    scope = _d2p_scope(db_session)
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )
    assert DailyToPackageOrchestrator(
        db_session, package_service=_OfflinePackageService(db_session)
    ).run(request).current_state == "AWAITING_RESEARCH"
    _approve_research(db_session, scope)

    blocked = DailyToPackageOrchestrator(
        db_session,
        package_service=_OfflinePackageService(
            db_session,
            tampered_digest_gate="visual_niche_alignment_gate",
        ),
    ).run(request)

    assert blocked.current_state == "BLOCKED_POLICY"
    assert any(
        "NICHE_GATE_DIGEST_BINDING_MISMATCH:visual_niche_alignment_gate"
        in blocker
        for blocker in blocked.blockers
    )


def test_d2p_rejects_self_hashed_semantic_digest_substitution(db_session) -> None:
    scope = _d2p_scope(db_session)
    forged = scope.digest.model_dump(mode="json", exclude={"content_hash"})
    forged["primary_niche"] = "Consumer crypto speculation"
    forged["content_hash"] = nich1_stable_hash(forged)
    digest_ref = {
        **scope.context_pack.pack_content["niche_contract_digest_ref"],
        "content_hash": forged["content_hash"],
    }

    with pytest.raises(
        ValidationFailureError,
        match="NICHE_CONTRACT_DIGEST_AUTHORITY_CONTENT_MISMATCH",
    ):
        DailyToPackageOrchestrator._validate_digest(
            digest=forged,
            digest_ref=digest_ref,
            channel=scope.channel,
            profile=scope.profile,
            policy_snapshot=scope.snapshot,
            slot=scope.slot,
            category=scope.category,
        )


def test_provider_zero_proof_is_cumulative_and_cannot_be_erased_by_rerun(
    db_session,
) -> None:
    scope = _d2p_scope(db_session)
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )
    package_service = _OfflinePackageService(
        db_session, create_provider_attempt=True
    )
    orchestrator = DailyToPackageOrchestrator(
        db_session, package_service=package_service
    )
    assert orchestrator.run(request).current_state == "AWAITING_RESEARCH"
    _approve_research(db_session, scope)

    first_block = orchestrator.run(request)
    assert first_block.current_state == "BLOCKED_POLICY"
    assert first_block.provider_calls_made == 1
    assert len(package_service.calls) == 1
    violating_package = db_session.get(
        FirstScriptedVideoPackage, uuid.UUID(first_block.package["id"])
    )
    proof = violating_package.artifacts["d2p1_authoritative_lineage"][
        "zero_execution_boundary"
    ]
    assert proof["zero_execution_confirmed"] is False
    assert proof["record_deltas"]["provider_attempts"] == 1

    package_service.create_provider_attempt = False
    rerun = orchestrator.run(request)
    assert rerun.current_state == "BLOCKED_POLICY"
    assert rerun.provider_calls_made == 1
    assert rerun.blockers == ["D2P1_FORBIDDEN_PROVIDER_OR_MEDIA_EXECUTION"]
    assert len(package_service.calls) == 1


def test_admitted_project_resumes_against_frozen_v2_when_latest_policy_changes(
    db_session,
) -> None:
    scope = _d2p_scope(db_session)
    package_service = _OfflinePackageService(db_session)
    orchestrator = DailyToPackageOrchestrator(
        db_session, package_service=package_service
    )
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )
    assert orchestrator.run(request).current_state == "AWAITING_RESEARCH"

    previous_snapshot = db_session.scalars(
        select(CompiledChannelPolicySnapshot)
        .where(CompiledChannelPolicySnapshot.channel_workspace_id == scope.channel.id)
        .where(CompiledChannelPolicySnapshot.id != scope.snapshot.id)
        .order_by(CompiledChannelPolicySnapshot.created_at.asc())
    ).first()
    previous_profile = db_session.get(
        ChannelProfileVersion, previous_snapshot.channel_profile_version_id
    )
    scope.snapshot.status = "approved"
    scope.profile.status = "approved"
    previous_snapshot.status = "active"
    previous_profile.status = "active"
    scope.channel.active_policy_snapshot_id = previous_snapshot.id
    db_session.flush()
    _approve_research(db_session, scope)

    resumed = orchestrator.run(request)

    assert (
        resumed.current_state == "PACKAGE_READY_FOR_HUMAN_REVIEW"
    ), resumed.model_dump(mode="json")
    frozen_project = db_session.get(VideoProject, uuid.UUID(resumed.project["id"]))
    assert frozen_project is not None
    assert frozen_project.policy_snapshot_id == scope.snapshot.id
    frozen_package = db_session.get(
        FirstScriptedVideoPackage, uuid.UUID(resumed.package["id"])
    )
    assert frozen_package is not None
    assert frozen_package.compiled_policy_snapshot_id == scope.snapshot.id


def test_tampered_m5_frozen_niche_lineage_blocks_resume(db_session) -> None:
    scope = _d2p_scope(db_session)
    orchestrator = DailyToPackageOrchestrator(
        db_session, package_service=_OfflinePackageService(db_session)
    )
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )
    first = orchestrator.run(request)
    assert first.current_state == "AWAITING_RESEARCH"
    project = db_session.get(VideoProject, uuid.UUID(first.project["id"]))
    summary = deepcopy(project.audience_delivery_summary)
    summary["niche_governance"]["niche_contract_digest_ref"][
        "content_hash"
    ] = "0" * 64
    project.audience_delivery_summary = summary
    db_session.flush()

    blocked = orchestrator.run(request)

    assert blocked.current_state == "BLOCKED_POLICY"
    assert blocked.blockers == ["M5_FROZEN_NICHE_LINEAGE_MISMATCH"]


def test_m5_daily_context_compiles_bounded_authoritative_niche_content(db_session) -> None:
    scope = _d2p_scope(db_session)
    resolver = ResourceResolverService(db_session)
    plan = resolver.create_retrieval_plan(
        data=RetrievalPlanSnapshotCreate(
            purpose="DAILY_IDEA",
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            editorial_calendar_slot_id=scope.slot.id,
            allowed_sources=DEFAULT_DAILY_CONTEXT_SOURCES,
            source_order=DEFAULT_DAILY_CONTEXT_SOURCES,
            created_by_user_id=scope.operator.id,
        )
    )
    pack = resolver.build_context_pack(
        data=ContextPackSnapshotCreate(
            retrieval_plan_snapshot_id=plan.id,
            freshness_state="FRESH",
            confidence_level="HIGH",
            created_by_user_id=scope.operator.id,
        )
    )
    digest = pack.pack_content["niche_contract_digest"]
    assert digest["primary_niche"]
    assert digest["target_audience"]
    assert digest["content_pillar_key"] == scope.slot.content_pillar
    assert digest["series_key"] == scope.slot.series_key
    assert digest["production_goal"] == scope.slot.production_goal
    assert pack.pack_content["niche_contract_digest_ref"]["content_hash"] == digest["content_hash"]
    assert pack.pack_content["prompt_budget_report"]["budget_status"] == "OK"
    agent_pack = pack.pack_content["agent_context_pack"]
    assert set(agent_pack["digests"]) >= {
        "niche_contract_digest",
        "editorial_slot_digest",
        "runtime_guard_digest",
        "evidence_digest",
        "common_skill_digest",
    }
