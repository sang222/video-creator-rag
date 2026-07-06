from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts.m12_2r import BackfillUploadedVideoRequest
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.workflow import VideoProjectCreate
from app.core.time import utc_now
from app.db.models import (
    AgentContextPackSnapshot,
    ChannelMemoryItem,
    CostEstimateSnapshot,
    FailureTraceReport,
    FinalMediaRef,
    FirstScriptedVideoPackage,
    HumanPaidRenderApproval,
    LearningCandidate,
    LearningEvidenceBundle,
    LearningReviewQueueItem,
    MemoryFacet,
    MemoryInfluenceManifest,
    MemoryReviewQueueItem,
    PaidAttemptLimitRecord,
    PaidProviderCallLedger,
    PostPublishHealthRun,
    ProxyPreviewArtifactFlag,
    QualityDeltaAttribution,
    R3D4GateBatchRun,
    R3D4GateRun,
    RecoveryProposal,
    RenderRevision,
    UploadedVideoMetricsSummary,
    UploadedVideo,
    VectorRetrievalManifest,
    VideoProject,
)
from app.main import create_app
from app.services import (
    EffectiveChannelRuntimeContextCompiler,
    PackagingHandoffReadService,
    PublishHandoffLedgerService,
    R3D1AdminService,
    VideoProjectService,
)
from app.services.r3d8 import stable_hash
from app.services.r3d9 import (
    ChannelRuntimeTraceService,
    DiagnosticOpsService,
    MemoryInfluenceOpsService,
    MemoryOpsReadModelService,
    PackageOpsSummaryService,
    ProviderCostOpsService,
    QualityDeltaOpsService,
    RetrievalOpsTraceService,
    RuntimeDashboardService,
    UploadedVideoOpsService,
)
from tests.qualification.conftest import QualificationFactory


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _artifacts() -> dict:
    return {
        "hook_spec": {
            "hook_type": "DIRECT",
            "first_3_seconds_script": "VCOS prepares a manual-only handoff.",
            "first_3_seconds_visual": "Operator cockpit card",
            "promise_made": "VCOS stops before provider calls",
            "payoff_location": "S2",
            "clickbait_risk": "LOW",
        },
        "narration_script": {
            "sentences": [
                {"sentence_id": "S1", "text": "VCOS prepares copy for a human operator."},
                {"sentence_id": "S2", "text": "VCOS stops before provider calls and upload."},
            ]
        },
        "metadata_package": {
            "title": "VCOS manual publish handoff",
            "description": "Copy into YouTube manually after final human review. VCOS does not upload or publish.",
            "subtitle_refs": [{"ref": "subtitle:draft", "lifecycle_state": "DRAFT"}],
            "disclosure_notes": ["AI-assisted draft, human final approval required."],
            "language": "vi",
            "locale": "vi-VN",
        },
        "thumbnail_brief": {
            "concept": "Operator cockpit",
            "text_overlay": "Manual only",
            "main_subject": "VCOS dashboard",
            "composition": "Clear card",
            "mobile_readability_notes": "Short text.",
        },
        "upload_card_copy": {
            "title": "VCOS manual publish handoff",
            "description": "Copy into YouTube manually after final human review. VCOS does not upload or publish.",
            "checklist_items": ["Copy title", "Copy description", "Upload subtitles"],
        },
    }


def _fixture(db_session, qualification_factory):
    scope = qualification_factory.channel_scope(name="R3D9")
    scope.channel.status = "active"
    scope.channel.primary_timezone = "Asia/Ho_Chi_Minh"
    category = R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"r3d9-{uuid.uuid4().hex[:8]}",
            name="R3D9 Ops",
            default_thumbnail_style_json={"style": "high contrast"},
            default_visual_style_json={"style": "operator cards"},
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )
    project_read = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id,
            title="R3D9 runtime trace project",
            description="R3D9 fixture",
            created_by_user_id=scope.operator.id,
        )
    )
    project = db_session.get(VideoProject, project_read.id)
    assert project is not None
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    effective.publish_timing_context_json = {
        "channel_timezone": "Asia/Ho_Chi_Minh",
        "manual_publish_only": True,
        "configured_publish_window": {"windows": [{"day": "MONDAY", "start": "09:00", "end": "11:00"}]},
        "source_contract_paths": ["publish_timing"],
    }
    effective.voice_audio_context_json = {"voice_profile_id": None, "language": "vi"}
    effective.thumbnail_style_context_json = {"style": "high contrast"}
    effective.cost_provider_policy_context_json = {"provider_real_execution_enabled": False, "budget_cap": "manual-only"}
    db_session.flush()

    package = FirstScriptedVideoPackage(
        video_project_id=project.id,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=effective.id,
        effective_context_hash=effective.context_hash,
        package_status="READY_FOR_HUMAN_REVIEW",
        agent_run_refs=[],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts=_artifacts(),
        limitations=["Human final approval required."],
        risk_limitations_summary={"provider_calls": False, "upload_calls": False},
        next_action="Human final approval required.",
    )
    db_session.add(package)
    db_session.flush()
    handoff = PackagingHandoffReadService(db_session).build(package.id)

    final_media = FinalMediaRef(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project.id,
        uploaded_video_id=None,
        media_type="LONG_FORM_FINAL",
        file_ref=f"fixture://final-media/{package.id}.mp4",
        provider_key=None,
        provider_type=None,
    )
    db_session.add(final_media)
    db_session.flush()

    task = PublishHandoffLedgerService(db_session).create_upload_task_from_package(package.id)
    backfill = PublishHandoffLedgerService(db_session).backfill_uploaded_video(
        task_id=task.id,
        data=BackfillUploadedVideoRequest(
            youtube_url_or_video_id="abcDEF12345",
            actual_title="VCOS manual publish handoff",
            operator_note="manual paste-back",
        ),
    )
    uploaded = db_session.get(UploadedVideo, backfill.uploaded_video.id)
    assert uploaded is not None

    batch = R3D4GateBatchRun(
        package_id=package.id,
        video_project_id=project.id,
        effective_context_snapshot_id=effective.id,
        context_hash=effective.context_hash,
        trigger_agent_key="r3d9-test",
        status="BLOCK",
        hard_block_count=1,
        review_required_count=0,
        gate_results_json=[{"gate_key": "ManualPublishOnlyGate", "status": "PASS"}],
        reducer_decision_json={"decision": "BLOCK"},
    )
    db_session.add(batch)
    db_session.flush()
    gate = R3D4GateRun(
        gate_batch_run_id=batch.id,
        package_id=package.id,
        video_project_id=project.id,
        effective_context_snapshot_id=effective.id,
        gate_key="TitlePromiseGate",
        status="BLOCK",
        severity="HARD_RULE",
        measurements_json={},
        fail_codes=["TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM"],
        blocking_refs=[],
        checked_artifact_refs=[{"artifact_key": "metadata_package"}],
        checked_contract_paths=["metadata_seo_policy_context"],
        evidence_refs=[],
        repair_hint="Rewrite title.",
        human_readable_summary="Title needs review.",
    )
    db_session.add(gate)
    pack = AgentContextPackSnapshot(
        package_id=package.id,
        video_project_id=project.id,
        agent_key="ScriptWriterAgent",
        task_type="script",
        lane="long_context_text",
        context_pack_version="r3d9-test",
        builder_version="r3d9-test",
        agent_context_contract_hash="contract-hash",
        effective_context_snapshot_id=effective.id,
        effective_context_hash=effective.context_hash,
        channel_contract_hash=effective.channel_contract_hash,
        compiled_policy_snapshot_id=scope.snapshot.id,
        compiled_policy_snapshot_hash=scope.snapshot.content_hash,
        context_pack_hash="context-pack-hash",
        prompt_context_hash="prompt-context-hash",
        runtime_guard_digest_hash="runtime-guard-hash",
        budget_report_json={"prompt_budget": 1234, "used": 456},
        omitted_items_json=[],
        largest_context_contributors_json=[{"key": "contract", "chars": 100}],
        agent_context_contract_json={},
        context_pack_json={},
        shape_gate_result_json={"status": "PASS"},
    )
    db_session.add(pack)
    metrics = UploadedVideoMetricsSummary(
        uploaded_video_id=uploaded.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project.id,
        platform="YOUTUBE",
        platform_video_id=uploaded.platform_video_id,
        metrics_summary={"views": 0, "click_through_rate": 0.01},
        availability_summary={},
        freshness_state="FRESH",
        confidence_level="MEDIUM",
        monitoring_state="READY_FOR_ANALYTICS",
        latest_captured_at=utc_now(),
    )
    db_session.add(metrics)
    health = PostPublishHealthRun(
        uploaded_video_id=uploaded.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project.id,
        policy_snapshot_id=scope.snapshot.id,
        platform="YOUTUBE",
        platform_video_id=uploaded.platform_video_id,
        observation_window="T_PLUS_1H",
        run_state="COMPLETED",
        health_state="INSUFFICIENT_DATA",
        severity="INFO",
        confidence_level="LOW",
        reason_codes=["ANALYTICS_NOT_MATURE"],
        operator_summary="Analytics too early.",
    )
    db_session.add(health)
    db_session.flush()
    failure = FailureTraceReport(
        post_publish_health_run_id=health.id,
        uploaded_video_id=uploaded.id,
        video_project_id=project.id,
        platform="YOUTUBE",
        platform_video_id=uploaded.platform_video_id,
        observation_window="T_PLUS_1H",
        primary_status="INSUFFICIENT_DATA",
        primary_suspected_cause="ANALYTICS_NOT_MATURE",
        confidence_level="LOW",
        severity="INFO",
        evidence_plain_text=["Wait for maturity."],
        operator_summary="Too early to diagnose.",
    )
    db_session.add(failure)
    db_session.flush()
    recovery = RecoveryProposal(
        failure_trace_report_id=failure.id,
        uploaded_video_id=uploaded.id,
        video_project_id=project.id,
        proposal_type="REVIEW_TITLE_THUMBNAIL",
        proposal_state="PROPOSED",
        operator_summary="Review title later.",
        recommended_actions=["Wait analytics maturity"],
        evidence_refs=[{"failure_trace_report_id": str(failure.id)}],
        risk_level="LOW",
    )
    db_session.add(recovery)

    candidate = LearningCandidate(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project.id,
        uploaded_video_id=uploaded.id,
        candidate_type="PACKAGING_PATTERN",
        candidate_state="READY_FOR_HUMAN_REVIEW",
        operator_summary="Candidate needs review.",
        friendly_status="Needs review.",
        candidate_summary="Do not learn yet.",
        suggested_learning="Wait for mature analytics before learning.",
        recommended_scope="CHANNEL",
        confidence_label="LOW",
        risk_level="LOW",
    )
    db_session.add(candidate)
    db_session.flush()
    evidence = LearningEvidenceBundle(
        learning_candidate_id=candidate.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        evidence_summary="Too early diagnostic.",
    )
    db_session.add(evidence)
    db_session.flush()
    candidate.evidence_bundle_id = evidence.id
    learning_queue = LearningReviewQueueItem(
        learning_candidate_id=candidate.id,
        evidence_bundle_id=evidence.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project.id,
        uploaded_video_id=uploaded.id,
        queue_state="READY_FOR_HUMAN_REVIEW",
        priority="NORMAL",
        operator_summary="Learning candidate pending.",
        friendly_status="Pending.",
        evidence_summary="Too early.",
        recommended_scope="CHANNEL",
        confidence_label="LOW",
        risk_level="LOW",
        next_action="Review learning candidate.",
        approval_actions_allowed=["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE"],
    )
    db_session.add(learning_queue)

    memory = ChannelMemoryItem(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        content_category_id=category.id,
        memory_type="PACKAGING_PATTERN",
        source_type="LEARNING_CANDIDATE",
        source_ref=str(candidate.id),
        source_content_hash="source-hash",
        summary="Unsafe pending memory.",
        approval_status="REVIEW_REQUIRED",
        rights_status="UNKNOWN",
        prompt_safety_state="UNKNOWN",
        reuse_scope="CHANNEL",
        freshness_state="FRESH",
        created_from_learning_candidate_id=candidate.id,
        content_hash="memory-hash",
    )
    db_session.add(memory)
    db_session.flush()
    facet = MemoryFacet(
        memory_item_id=memory.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        content_category_id=category.id,
        facet_type="PACKAGING_PATTERN",
        facet_text="Raw memory text should not appear in retrieval debug.",
        facet_text_hash="facet-hash",
        scope_json={"channel_id": str(scope.channel.id)},
        allowed_use_cases_json=["script"],
        forbidden_use_cases_json=[],
        prompt_safety_state="UNKNOWN",
        embedding_eligible=False,
    )
    db_session.add(facet)
    db_session.flush()
    memory_queue = MemoryReviewQueueItem(memory_item_id=memory.id, queue_status="PENDING", reason_codes_json=["MEMORY_REVIEW_REQUIRED"])
    db_session.add(memory_queue)

    retrieval = VectorRetrievalManifest(
        video_project_id=project.id,
        package_id=package.id,
        effective_context_snapshot_id=effective.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        content_category_id=category.id,
        agent_key="ScriptWriterAgent",
        use_case="script",
        query_text_hash=stable_hash({"q": "ops"}),
        sql_filter_json={"approval_status": "APPROVED"},
        candidate_count_before_vector=1,
        candidate_count_after_policy=0,
        selected_memory_facet_refs_json=[{"memory_item_id": str(memory.id), "memory_facet_id": str(facet.id), "facet_text": facet.facet_text}],
        blocked_refs_json=[{"memory_item_id": str(memory.id), "memory_facet_id": str(facet.id), "reason_codes": ["MEMORY_NOT_APPROVED"], "facet_text": facet.facet_text}],
        rejected_refs_json=[],
        retrieval_hash="retrieval-hash",
        digest_hash="digest-hash",
    )
    db_session.add(retrieval)
    db_session.flush()
    influence = MemoryInfluenceManifest(
        video_project_id=project.id,
        package_id=package.id,
        effective_context_snapshot_id=effective.id,
        agent_key="ScriptWriterAgent",
        retrieval_manifest_id=retrieval.id,
        memory_facet_ids_used_json=[str(facet.id)],
        memory_item_ids_used_json=[str(memory.id)],
        digest_hash="digest-hash",
        prompt_context_hash="prompt-context-hash",
        applied_as_json={"context_pack_section": "memory_digest"},
        ignored_memory_refs_json=[],
        blocked_memory_refs_json=[{"memory_item_id": str(memory.id), "reason_codes": ["MEMORY_NOT_APPROVED"], "facet_text": facet.facet_text}],
        scope_status="BLOCK",
    )
    db_session.add(influence)
    db_session.flush()
    quality = QualityDeltaAttribution(
        source_memory_influence_manifest_id=influence.id,
        source_video_project_id=project.id,
        target_uploaded_video_id=uploaded.id,
        target_video_project_id=project.id,
        effective_context_snapshot_id=effective.id,
        category_id=category.id,
        expected_metric_family="CTR",
        expected_improvement_direction="HIGHER",
        baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
        observed_snapshot_ref={"maturity_state": "IMMATURE"},
        attribution_window="T_PLUS_24H",
        confidence_result="TOO_EARLY",
        confidence_delta=0,
        reason_codes_json=["ANALYTICS_NOT_MATURE"],
    )
    db_session.add(quality)

    revision = RenderRevision(
        video_project_id=project.id,
        package_id=package.id,
        effective_context_snapshot_id=effective.id,
        revision_no=1,
        revision_status="BLOCKED",
        source_artifact_refs_json=[{"package_id": str(package.id)}],
        gate_batch_refs_json=[{"gate_batch_run_id": str(batch.id), "status": "BLOCK"}],
        render_plan_hash="render-plan-hash",
        provider_plan_json={"provider_stages": [{"provider_key": "creatomate_growth_10k", "provider_stage": "FINAL_ASSEMBLY_RENDER"}]},
        created_by="r3d9-test",
    )
    db_session.add(revision)
    db_session.flush()
    estimate = CostEstimateSnapshot(
        render_revision_id=revision.id,
        video_project_id=project.id,
        package_id=package.id,
        estimate_status="ESTIMATE_PENDING_PROVIDER_CONFIG",
        currency="USD",
        estimated_pexels_cost=Decimal("0"),
        provider_estimates_json={},
        blocker_reason_codes_json=["CREATOMATE_PROVIDER_NOT_CONFIGURED"],
        content_hash="cost-hash",
    )
    approval = HumanPaidRenderApproval(
        render_revision_id=revision.id,
        approval_status="PENDING",
        max_approved_cost=Decimal("10.00"),
        approved_provider_stages_json=["FINAL_ASSEMBLY_RENDER"],
        rationale="Pending human approval.",
    )
    attempt = PaidAttemptLimitRecord(
        render_revision_id=revision.id,
        provider_key="creatomate_growth_10k",
        provider_stage="FINAL_ASSEMBLY_RENDER",
        attempt_count=1,
        max_attempts=1,
        status="BLOCKED",
        reason_codes_json=["PAID_ATTEMPT_LIMIT_EXCEEDED"],
    )
    ledger = PaidProviderCallLedger(
        render_revision_id=revision.id,
        provider_key="creatomate_growth_10k",
        provider_stage="FINAL_ASSEMBLY_RENDER",
        call_type="VALIDATION_ONLY",
        call_status="BLOCKED",
        cost_estimate_snapshot_id=None,
        request_fingerprint="fingerprint",
        reason_codes_json=["PROVIDER_REAL_EXECUTION_DISABLED"],
    )
    proxy = ProxyPreviewArtifactFlag(
        artifact_ref="proxy:preview",
        video_project_id=project.id,
        package_id=package.id,
        preview_only=True,
        not_final_media=True,
        not_publishable=True,
        source_type="TEST",
    )
    db_session.add_all([estimate, approval, attempt, ledger, proxy])
    db_session.flush()
    return {
        "scope": scope,
        "project": project,
        "effective": effective,
        "package": package,
        "handoff": handoff,
        "uploaded": uploaded,
        "retrieval": retrieval,
        "influence": influence,
        "quality": quality,
    }


def test_r3d9_runtime_dashboard_read_models_and_safe_boundaries(db_session, qualification_factory) -> None:
    fx = _fixture(db_session, qualification_factory)

    command = RuntimeDashboardService(db_session).command_center()
    assert command.active_channels
    assert command.packages_waiting_review
    assert command.upload_tasks_waiting_human
    assert command.uploaded_videos_waiting_verification_or_analytics
    assert command.diagnostics_needing_review
    assert command.recovery_proposals_needing_action
    assert command.learning_candidates_needing_review
    assert command.memory_approvals_needing_review
    assert command.provider_cost_blockers
    assert command.gate_failures
    assert all(card.next_action.next_action_code for card in command.packages_waiting_review + command.provider_cost_blockers)

    trace = ChannelRuntimeTraceService(db_session).for_project(fx["project"].id)
    fx["scope"].channel.primary_timezone = "Pacific/Honolulu"
    db_session.flush()
    trace_after_mutation = ChannelRuntimeTraceService(db_session).for_project(fx["project"].id)
    assert trace.effective_context_snapshot_id == fx["effective"].id
    assert trace.channel_contract_hash == fx["effective"].channel_contract_hash
    assert trace.latest_mutable_settings_used is False
    assert trace_after_mutation.publish_timing_policy["channel_timezone"] == "Asia/Ho_Chi_Minh"

    package = PackageOpsSummaryService(db_session).build(fx["package"].id)
    assert package.manual_publish_handoff["manual_only_warning_vi"]
    assert package.title_description_subtitles_disclosure["title"] == "VCOS manual publish handoff"
    assert package.r3d4_deterministic_gate_results[0]["gate_key"] == "TitlePromiseGate"
    assert package.prompt_budget_summary[0]["budget_report"]["prompt_budget"] == 1234

    uploaded = UploadedVideoOpsService(db_session).build(fx["uploaded"].id)
    assert uploaded.backfill_history
    assert uploaded.analytics_maturity == "TOO_EARLY"
    assert uploaded.no_youtube_studio_scraping is True

    diagnostics = DiagnosticOpsService(db_session).queue()
    assert diagnostics.items[0]["data_maturity"] == "TOO_EARLY"
    assert diagnostics.items[0]["action_ready"] is False

    memory = MemoryOpsReadModelService(db_session).queue()
    assert memory.items[0]["prompt_eligible"] is False
    assert "MEMORY_NOT_APPROVED" in memory.items[0]["prompt_eligibility_blockers"]

    retrieval = RetrievalOpsTraceService(db_session).build(fx["retrieval"].id)
    assert retrieval.raw_memory_hidden is True
    assert "facet_text" not in str(retrieval.model_dump())

    influence = MemoryInfluenceOpsService(db_session).build(fx["influence"].id)
    assert influence.retrieval_manifest_id == fx["retrieval"].id
    assert influence.memory_facets_used[0]["raw_memory_text_hidden"] is True

    quality = QualityDeltaOpsService(db_session).build(fx["quality"].id)
    assert quality.result == "TOO_EARLY"
    assert quality.next_action.next_action_code == "WAIT_ANALYTICS_MATURITY"

    provider = ProviderCostOpsService(db_session).build(fx["package"].id)
    assert provider.will_execute is False
    assert provider.cost_estimates[0]["estimate_status"] == "ESTIMATE_PENDING_PROVIDER_CONFIG"
    assert "CREATOMATE_PROVIDER_NOT_CONFIGURED" in provider.next_action.blocking_reason_codes


def test_r3d9_api_routes_are_get_only_and_do_not_add_job_controls(db_session, qualification_factory) -> None:
    fx = _fixture(db_session, qualification_factory)
    db_session.commit()
    client = TestClient(create_app())

    for path in [
        "/ops/command-center",
        f"/channels/{fx['scope'].channel.id}/runtime-trace",
        f"/video-projects/{fx['project'].id}/runtime-trace",
        f"/video-packages/{fx['package'].id}/ops-summary",
        f"/uploaded-videos/{fx['uploaded'].id}/ops-summary",
        "/diagnostics/queue",
        "/recovery/queue",
        "/learning/queue",
        "/memory/review-queue/ops",
        f"/retrieval-manifests/{fx['retrieval'].id}",
        f"/memory-influence/{fx['influence'].id}",
        f"/quality-delta/{fx['quality'].id}",
        f"/provider-cost/{fx['package'].id}",
    ]:
        response = client.get(path)
        assert response.status_code == 200, response.text

    r3d9_paths = {route.path: route.methods for route in create_app().routes if route.path in {
        "/ops/command-center",
        "/ops/next-actions",
        "/diagnostics/queue",
        "/recovery/queue",
        "/learning/queue",
        "/memory/review-queue/ops",
    }}
    assert all(methods == {"GET"} for methods in r3d9_paths.values())
    source = Path("app/services/r3d9.py").read_text(encoding="utf-8").lower()
    forbidden = ["execute_real_provider_flow", "googledriveuploadservice", "youtubeupload", "scrape", "studio", "run_pending_cleanup"]
    assert [token for token in forbidden if token in source] == []
