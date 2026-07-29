from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.contracts.r3d5 import MemoryApprovalRequest
from app.contracts.r3d7 import LearningToMemoryPromotionRequest, QualityDeltaAttributionRunRequest
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AgentMemoryApplicationRecord,
    ApprovedPlaybookEntry,
    FailureTraceReport,
    LearningCandidate,
    LearningEvidenceBundle,
    MemoryConfidenceUpdateLedger,
    MemoryInfluenceManifest,
    OpsIncident,
    PostPublishHealthRun,
    RecoveryProposal,
    UploadedVideo,
    UploadedVideoMetricsSummary,
    VectorRetrievalManifest,
)
from app.services.r3d3 import AgentContextPackBuilder
from app.services.r3d5 import ControlledMemoryService
from app.services.r3d6 import EmbeddingJobService, VectorSafeRetrievalService, stable_hash
from app.services.r3d7 import (
    ClosedLearningLoopService,
    LearningLoopEligibilityGate,
    LearningToMemoryPromotionService,
    MemoryInfluenceManifestService,
    QualityDeltaAttributionService,
)
from tests.qualification.conftest import QualificationFactory
from tests.test_r3d5_controlled_memory_foundation import _category, _effective_context
from tests.test_r3d6_vector_safe_retrieval_foundation import _eligible_memory, _retrieval_request


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _uploaded_video(db_session, scope, project_id, *, platform_id: str | None = None, published_offset: str = "source"):
    uploaded = UploadedVideo(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project_id,
        policy_snapshot_id=scope.snapshot.id,
        platform="YOUTUBE",
        platform_video_id=platform_id or f"yt-{published_offset}-{uuid.uuid4().hex[:8]}",
        video_url=f"https://youtu.be/{uuid.uuid4().hex[:11]}",
        published_at=utc_now(),
        publish_status="CONFIRMED",
        actual_visibility="public",
    )
    db_session.add(uploaded)
    db_session.flush()
    return uploaded


def _metrics_summary(
    db_session,
    scope,
    uploaded,
    project_id,
    *,
    metrics: dict,
    freshness: str = "FRESH",
    confidence: str = "HIGH",
    monitoring_state: str = "SYNCED",
):
    summary = UploadedVideoMetricsSummary(
        uploaded_video_id=uploaded.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=project_id,
        platform="YOUTUBE",
        platform_video_id=uploaded.platform_video_id,
        metrics_summary=metrics,
        availability_summary={},
        freshness_state=freshness,
        confidence_level=confidence,
        monitoring_state=monitoring_state,
        latest_captured_at=utc_now(),
    )
    db_session.add(summary)
    db_session.flush()
    return summary


def _learning_chain(db_session, scope, source_uploaded, source_project_id, *, playbook_text: str | None = None):
    health = PostPublishHealthRun(
        uploaded_video_id=source_uploaded.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=source_project_id,
        policy_snapshot_id=scope.snapshot.id,
        platform="YOUTUBE",
        platform_video_id=source_uploaded.platform_video_id,
        observation_window="T_PLUS_24H",
        run_state="COMPLETED",
        health_state="NO_VIEW_RISK",
        severity="HIGH",
        confidence_level="MEDIUM",
        operator_summary="Video co dau hieu no-view.",
    )
    db_session.add(health)
    db_session.flush()
    failure = FailureTraceReport(
        post_publish_health_run_id=health.id,
        uploaded_video_id=source_uploaded.id,
        video_project_id=source_project_id,
        platform="YOUTUBE",
        platform_video_id=source_uploaded.platform_video_id,
        observation_window="T_PLUS_24H",
        primary_status="NO_VIEW_RISK",
        primary_suspected_cause="PACKAGING_PATTERN",
        confidence_level="MEDIUM",
        severity="HIGH",
        evidence_plain_text=["CTR thap hon baseline."],
        operator_summary="Packaging can than hon.",
    )
    db_session.add(failure)
    db_session.flush()
    recovery = RecoveryProposal(
        failure_trace_report_id=failure.id,
        uploaded_video_id=source_uploaded.id,
        video_project_id=source_project_id,
        proposal_type="REVIEW_TITLE_THUMBNAIL",
        proposal_state="PROPOSED",
        operator_summary="Dung title cu the hon.",
        recommended_actions=["Prefer concrete outcome framing."],
        risk_level="LOW",
    )
    db_session.add(recovery)
    candidate = LearningCandidate(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=source_project_id,
        uploaded_video_id=source_uploaded.id,
        candidate_type="PACKAGING_PATTERN",
        candidate_state="READY_FOR_HUMAN_REVIEW",
        operator_summary="Learning candidate.",
        friendly_status="Can review.",
        candidate_summary="Concrete framing beat abstract framing.",
        suggested_learning="Prefer concrete outcome framing over abstract titles.",
        suggested_playbook_text=playbook_text or "Prefer concrete outcome framing over abstract titles.",
        recommended_scope="CHANNEL",
        confidence_label="MEDIUM",
        risk_level="LOW",
    )
    db_session.add(candidate)
    db_session.flush()
    evidence = LearningEvidenceBundle(
        learning_candidate_id=candidate.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        evidence_summary="No-view diagnostic and CTR baseline.",
        source_video_refs=[{"uploaded_video_id": str(source_uploaded.id)}],
        diagnostic_refs=[{"failure_trace_report_id": str(failure.id)}],
        recovery_refs=[{"recovery_proposal_id": str(recovery.id)}],
        metric_support=[{"metric": "click_through_rate", "direction": "LOW"}],
        freshness_summary={"state": "FRESH"},
        confidence_summary={"label": "MEDIUM"},
        policy_rights_summary={"risk": "LOW"},
    )
    db_session.add(evidence)
    candidate.evidence_bundle_id = evidence.id
    entry = ApprovedPlaybookEntry(
        learning_candidate_id=candidate.id,
        evidence_bundle_id=evidence.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        scope="CHANNEL",
        category="PACKAGING",
        playbook_text=candidate.suggested_playbook_text,
        evidence_refs=[{"evidence_bundle_id": str(evidence.id)}],
        state="APPROVED",
        approved_by_user_id=scope.operator.id,
    )
    db_session.add(entry)
    db_session.flush()
    return candidate, evidence, entry, failure, recovery


def _build_pack_with_memory(db_session, monkeypatch, scope, effective, *, agent_key: str = "ScriptWriterAgent", topic: str = "R3D7 memory"):
    monkeypatch.setenv("CONTROLLED_MEMORY_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("VECTOR_RETRIEVAL_ENABLED", "false")
    get_settings.cache_clear()
    try:
        return AgentContextPackBuilder(db_session).build(
            package_id=uuid.uuid4(),
            video_project_id=effective.video_project_id,
            agent_key=agent_key,
            task_type="long_form_script",
            lane="long_context_text" if agent_key != "GatekeeperSoftReviewAgent" else "gatekeeper_soft_review",
            effective_context_snapshot_id=effective.id,
            effective_context_hash=effective.context_hash,
            compiled_policy_snapshot_id=scope.snapshot.id,
            compiled_policy_snapshot_hash=scope.snapshot.content_hash,
            channel_contract_hash=effective.channel_contract_hash,
            artifacts={"script_outline": {"outline": ["hook"]}},
            evidence_refs=[{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d7"}],
            current_package_state={"topic": topic, "research_pack_ref": "r3d7"},
            runtime_guard_state={"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
        )
    finally:
        get_settings.cache_clear()


def _approved_memory_from_promotion(db_session, scope, entry, *, category=None):
    run = LearningToMemoryPromotionService(db_session).promote_approved_playbook(
        LearningToMemoryPromotionRequest(
            approved_playbook_entry_id=entry.id,
            evidence_bundle_id=entry.evidence_bundle_id,
            content_category_id=category.id if category is not None else None,
            allowed_use_cases_json=["script", "script_planning", "gatekeeper"],
            embedding_eligible=True,
            facet_type="FAILED_HOOK",
        )
    )
    assert run.run_status == "COMPLETED"
    item_id = uuid.UUID(run.created_memory_item_ids_json[0])
    item = ControlledMemoryService(db_session).require_item(item_id)
    ControlledMemoryService(db_session).approve(
        memory_item_id=item.id,
        data=MemoryApprovalRequest(decided_by=scope.operator.id, rationale="Approve R3D7 fixture memory."),
    )
    facet = ControlledMemoryService(db_session).list_facets(memory_item_id=item.id)[0]
    EmbeddingJobService(db_session).store_embedding(memory_facet_id=facet.id, embedding_vector=[1.0, 0.0])
    return run, item, facet


def test_unapproved_learning_candidate_cannot_promote_to_memory(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Unapproved")
    candidate = LearningCandidate(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        candidate_type="PACKAGING_PATTERN",
        candidate_state="READY_FOR_HUMAN_REVIEW",
        operator_summary="Candidate",
        friendly_status="Review",
        candidate_summary="Summary",
        suggested_learning="Do something better.",
        recommended_scope="CHANNEL",
        confidence_label="LOW",
        risk_level="LOW",
    )
    db_session.add(candidate)
    db_session.flush()

    run = LearningToMemoryPromotionService(db_session).promote_learning_candidate(learning_candidate_id=candidate.id)

    assert run.run_status == "BLOCKED"
    assert "NO_AUTO_PROMOTION_FROM_RAW_LEARNING" in run.reason_codes_json
    assert run.created_memory_item_ids_json == []


def test_approved_playbook_creates_memory_draft_facets_review_queue_without_prompt_injection(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Promotion")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    source_video = _uploaded_video(db_session, scope, effective.video_project_id)
    _, _, entry, _, _ = _learning_chain(db_session, scope, source_video, effective.video_project_id)

    run = LearningToMemoryPromotionService(db_session).promote_approved_playbook(
        LearningToMemoryPromotionRequest(approved_playbook_entry_id=entry.id, evidence_bundle_id=entry.evidence_bundle_id, content_category_id=category.id)
    )

    item = ControlledMemoryService(db_session).require_item(uuid.UUID(run.created_memory_item_ids_json[0]))
    assert run.run_status == "COMPLETED"
    assert item.approval_status == "REVIEW_REQUIRED"
    assert run.created_memory_facet_ids_json
    assert db_session.query(MemoryInfluenceManifest).count() == 0


def test_memory_not_approved_safe_prompt_safe_cannot_be_retrieved_or_injected(db_session, qualification_factory, monkeypatch) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Unapproved Retrieval")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    source_video = _uploaded_video(db_session, scope, effective.video_project_id)
    _, _, entry, _, _ = _learning_chain(db_session, scope, source_video, effective.video_project_id)
    LearningToMemoryPromotionService(db_session).promote_approved_playbook(
        LearningToMemoryPromotionRequest(approved_playbook_entry_id=entry.id, content_category_id=category.id, embedding_eligible=True)
    )

    result = _build_pack_with_memory(db_session, monkeypatch, scope, effective)
    digest = result.context_pack["digests"]["memory_digest"]

    assert result.status == "OK"
    assert digest["status"] == "EMPTY_SAFE_DIGEST"
    assert digest["selected_memory_facet_refs"] == []


def test_retrieval_digest_injection_creates_manifest_and_application_record_digest_only(db_session, qualification_factory, monkeypatch) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Influence")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    source_video = _uploaded_video(db_session, scope, effective.video_project_id)
    _, _, entry, _, _ = _learning_chain(db_session, scope, source_video, effective.video_project_id)
    _, item, facet = _approved_memory_from_promotion(db_session, scope, entry, category=category)

    result = _build_pack_with_memory(db_session, monkeypatch, scope, effective)
    digest = result.context_pack["digests"]["memory_digest"]
    manifest = db_session.get(MemoryInfluenceManifest, uuid.UUID(digest["memory_influence_manifest_id"]))

    assert result.status == "OK"
    assert manifest.scope_status == "PASS"
    assert manifest.memory_facet_ids_used_json == [str(facet.id)]
    assert manifest.memory_item_ids_used_json == [str(item.id)]
    assert db_session.query(AgentMemoryApplicationRecord).count() == 1
    digest_text = str(digest)
    assert "facet_text" not in digest_text
    assert "embedding_vector_json" not in digest_text
    assert "full old script" not in digest_text.lower()


def test_memory_influence_manifest_blocks_scope_mismatch(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Scope")
    other = qualification_factory.channel_scope(name="R3D7 Scope Other")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    item, facet, _ = _eligible_memory(db_session, other, text="Cross-channel should block.", vector=[1.0, 0.0])
    retrieval = VectorRetrievalManifest(
        video_project_id=effective.video_project_id,
        package_id=uuid.uuid4(),
        effective_context_snapshot_id=effective.id,
        company_id=effective.company_id,
        channel_workspace_id=effective.channel_workspace_id,
        content_category_id=effective.content_category_id,
        agent_key="ScriptWriterAgent",
        use_case="script",
        query_text_hash=stable_hash({"q": "scope"}),
        selected_memory_facet_refs_json=[{"memory_item_id": str(item.id), "memory_facet_id": str(facet.id)}],
        retrieval_hash=stable_hash({"retrieval": "scope"}),
        digest_hash=stable_hash({"digest": "scope"}),
    )
    db_session.add(retrieval)
    db_session.flush()

    with pytest.raises(ValidationFailureError):
        MemoryInfluenceManifestService(db_session).record_from_digest(
            video_project_id=effective.video_project_id,
            package_id=retrieval.package_id,
            effective_context_snapshot_id=effective.id,
            agent_key="ScriptWriterAgent",
            digest={
                "retrieval_manifest_id": str(retrieval.id),
                "digest_hash": retrieval.digest_hash,
                "selected_memory_facet_refs": retrieval.selected_memory_facet_refs_json,
            },
            prompt_context_hash=stable_hash({"prompt": "scope"}),
        )


def test_provider_readiness_agent_gets_no_creative_memory_and_gatekeeper_gets_manifest_summary(db_session, qualification_factory, monkeypatch) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Agent Modes")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    _eligible_memory(db_session, scope, effective=effective, category=category, text="Use concrete hooks.", vector=[1.0, 0.0])

    provider_result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=False).retrieve(
        _retrieval_request(effective, agent_key="ProviderReadinessSummaryAgent")
    )
    assert provider_result.digest["status"] == "EMPTY_SAFE_DIGEST"
    assert provider_result.digest["reason_codes"] == ["AGENT_DOES_NOT_ACCEPT_CREATIVE_MEMORY"]

    gatekeeper = _build_pack_with_memory(db_session, monkeypatch, scope, effective, agent_key="GatekeeperSoftReviewAgent")
    memory_digest = gatekeeper.context_pack["digests"]["memory_digest"]
    assert memory_digest["digest_type"] == "GatekeeperMemoryManifestSummary"
    assert memory_digest["lessons"] == []
    assert memory_digest["memory_influence_manifest_id"]


def test_quality_delta_returns_too_early_and_blocked_by_data_quality(db_session, qualification_factory, monkeypatch) -> None:
    scope, effective, manifest = _attribution_manifest_fixture(db_session, qualification_factory, monkeypatch)
    target = _uploaded_video(db_session, scope, effective.video_project_id, published_offset="immature")
    _metrics_summary(db_session, scope, target, effective.video_project_id, metrics={}, monitoring_state="NO_DATA_YET", confidence="UNKNOWN")

    too_early = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_uploaded_video_id=target.id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
        )
    )
    blocked = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.03}, "freshness_state": "STALE", "confidence_level": "HIGH"},
        )
    )

    assert too_early.confidence_result == "TOO_EARLY"
    assert blocked.confidence_result == "BLOCKED_BY_DATA_QUALITY"
    assert "OBSERVED_ANALYTICS_STALE" in blocked.reason_codes_json


def test_quality_delta_improved_degraded_inconclusive_and_confidence_ledger(db_session, qualification_factory, monkeypatch) -> None:
    scope, effective, manifest = _attribution_manifest_fixture(db_session, qualification_factory, monkeypatch)
    facet_id = uuid.UUID(manifest.memory_facet_ids_used_json[0])
    facet = ControlledMemoryService(db_session).require_facet(facet_id)
    facet.confidence_label = "LOW"
    db_session.flush()

    improved = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.04}, "freshness_state": "FRESH", "confidence_level": "HIGH"},
        )
    )
    degraded = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.04}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.01}, "freshness_state": "FRESH", "confidence_level": "HIGH"},
        )
    )
    inconclusive = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.03}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.03}, "freshness_state": "FRESH", "confidence_level": "HIGH"},
        )
    )

    assert improved.confidence_result == "IMPROVED"
    assert degraded.confidence_result == "DEGRADED"
    assert inconclusive.confidence_result == "INCONCLUSIVE"
    assert facet.confidence_label == "LOW"
    ledgers = (
        db_session.query(MemoryConfidenceUpdateLedger)
        .filter(MemoryConfidenceUpdateLedger.memory_facet_id == facet.id)
        .all()
    )
    assert len(ledgers) >= 2
    assert {ledger.new_confidence_label for ledger in ledgers} >= {
        "MEDIUM",
        "UNPROVEN",
    }
    assert all(ledger.requires_human_review for ledger in ledgers)
    assert all(
        "CONFIDENCE_CHANGE_PROPOSAL_ONLY" in ledger.reason_codes_json
        for ledger in ledgers
    )


def test_memory_confidence_does_not_auto_promote_to_high_from_one_weak_sample(db_session, qualification_factory, monkeypatch) -> None:
    _, effective, manifest = _attribution_manifest_fixture(db_session, qualification_factory, monkeypatch)
    facet = ControlledMemoryService(db_session).require_facet(uuid.UUID(manifest.memory_facet_ids_used_json[0]))
    facet.confidence_label = "MEDIUM"
    db_session.flush()

    QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.05}, "freshness_state": "FRESH", "confidence_level": "HIGH"},
        )
    )

    assert facet.confidence_label == "MEDIUM"
    ledger = db_session.query(MemoryConfidenceUpdateLedger).order_by(MemoryConfidenceUpdateLedger.created_at.desc()).first()
    assert "ONE_SAMPLE_CONFIDENCE_CAP" in ledger.reason_codes_json
    assert ledger.requires_human_review is True
    assert "ACTIVE_MEMORY_CONFIDENCE_UNCHANGED" in ledger.reason_codes_json


def test_unresolved_severe_enforcement_incident_freezes_learning_attribution(db_session, qualification_factory, monkeypatch) -> None:
    _, effective, manifest = _attribution_manifest_fixture(db_session, qualification_factory, monkeypatch)
    db_session.add(
        OpsIncident(
            incident_type="HEALTH_DEGRADED",
            severity="CRITICAL",
            state="OPEN",
            reason_codes=["YOUTUBE_ENFORCEMENT"],
            next_action="Human review.",
        )
    )
    db_session.flush()

    attribution = QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_video_project_id=effective.video_project_id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.02}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.05}, "freshness_state": "FRESH", "confidence_level": "HIGH"},
        )
    )

    assert attribution.confidence_result == "BLOCKED_BY_DATA_QUALITY"
    assert "UNRESOLVED_SEVERE_ENFORCEMENT_FREEZE" in attribution.reason_codes_json


def test_closed_learning_loop_fixture_and_eligibility_gate(db_session, qualification_factory, monkeypatch) -> None:
    scope = qualification_factory.channel_scope(name="R3D7 Closed Loop")
    category = _category(db_session, scope)
    source_effective = _effective_context(db_session, scope, category=category)
    target_effective = _effective_context(db_session, scope, category=category)
    source_video = _uploaded_video(db_session, scope, source_effective.video_project_id, published_offset="low")
    target_video = _uploaded_video(db_session, scope, target_effective.video_project_id, published_offset="target")
    _metrics_summary(db_session, scope, source_video, source_effective.video_project_id, metrics={"click_through_rate": 0.01})
    _metrics_summary(db_session, scope, target_video, target_effective.video_project_id, metrics={"click_through_rate": 0.04})
    candidate, _, entry, _, _ = _learning_chain(db_session, scope, source_video, source_effective.video_project_id)
    _, _, facet = _approved_memory_from_promotion(db_session, scope, entry, category=category)
    result = _build_pack_with_memory(db_session, monkeypatch, scope, target_effective)
    manifest = db_session.get(MemoryInfluenceManifest, uuid.UUID(result.context_pack["digests"]["memory_digest"]["memory_influence_manifest_id"]))
    QualityDeltaAttributionService(db_session).run(
        QualityDeltaAttributionRunRequest(
            source_memory_influence_manifest_id=manifest.id,
            target_uploaded_video_id=target_video.id,
            expected_metric_family="PACKAGING_PATTERN",
            baseline_snapshot_ref={"metrics": {"click_through_rate": 0.01}},
            observed_snapshot_ref={"metrics": {"click_through_rate": 0.04}, "freshness_state": "FRESH", "confidence_level": "HIGH"},
        )
    )

    gate = LearningLoopEligibilityGate(db_session).check_for_attribution(
        uploaded_video_id=target_video.id,
        learning_candidate_id=candidate.id,
        memory_facet_ids=[facet.id],
        retrieval_manifest_id=manifest.retrieval_manifest_id,
        influence_manifest_id=manifest.id,
    )
    status = ClosedLearningLoopService(db_session).status(uploaded_video_id=source_video.id, target_video_project_id=target_effective.video_project_id)

    assert gate.passed is True
    assert status["status"] == "COMPLETED"


def test_r3d7_source_guards_no_provider_upload_external_vector_or_prompt_mutation() -> None:
    service_source = Path("app/services/r3d7.py").read_text(encoding="utf-8")
    r3d3_source = Path("app/services/r3d3.py").read_text(encoding="utf-8")
    config = get_settings()
    forbidden = [
        "requests.",
        "httpx",
        "YouTubeUpload",
        "GoogleDriveUploadService(",
        "GoogleVertexVeoProvider",
        "Pinecone",
        "qdrant",
        "weaviate",
    ]
    assert [token for token in forbidden if token in service_source] == []
    assert "ChannelProfileVersion(" not in service_source
    assert "channel_profile_versions" not in service_source
    assert "prompt self" not in service_source.lower()
    assert "memory_influence_manifest_id" in r3d3_source
    assert config.controlled_memory_retrieval_enabled is False
    assert config.vector_retrieval_enabled is False
    assert config.embedding_execution_enabled is False


def _attribution_manifest_fixture(db_session, qualification_factory, monkeypatch):
    scope = qualification_factory.channel_scope(name=f"R3D7 Attribution {uuid.uuid4().hex[:4]}")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    source_video = _uploaded_video(db_session, scope, effective.video_project_id)
    _, _, entry, _, _ = _learning_chain(db_session, scope, source_video, effective.video_project_id)
    _approved_memory_from_promotion(db_session, scope, entry, category=category)
    result = _build_pack_with_memory(db_session, monkeypatch, scope, effective)
    manifest_id = uuid.UUID(result.context_pack["digests"]["memory_digest"]["memory_influence_manifest_id"])
    manifest = db_session.get(MemoryInfluenceManifest, manifest_id)
    assert manifest is not None
    assert manifest.memory_facet_ids_used_json
    return scope, effective, manifest
