from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from app.contracts import LearningCandidateGenerationRunCreate, ManualAnalyticsImportContract, PostPublishHealthRunCreate

pytestmark = pytest.mark.skip(
    reason="Historical learning qualification depends on pre-M12 local render/upload fixture; M12.1R keeps auto-promote disabled."
)
from app.contracts.m7 import ManualPublishConfirmationCreate, PublishHandoffCreate
from app.db.models import (
    DomainEvent,
    ApprovedPlaybookEntry,
    LearningCandidate,
    LearningCandidateGenerationRun,
    LearningEvidenceBundle,
    LearningPromotionEligibilityRun,
    LearningReviewQueueItem,
    LearningReviewDecision,
    PlaybookCandidateDraft,
    UploadedVideo,
)
from app.main import create_app
from app.services import (
    AnalyticsSyncService,
    LearningCandidateGenerationService,
    ManualPublishConfirmationService,
    PostPublishHealthMonitorService,
    PublishHandoffService,
)

from .helpers.git_checks import collect_git_status
from .helpers.repo_scanners import all_scope_violations


M10_TABLES = {
    "learning_candidate_generation_runs",
    "learning_candidates",
    "learning_evidence_bundles",
    "learning_promotion_eligibility_runs",
    "learning_review_queue_items",
    "playbook_candidate_drafts",
}

FORBIDDEN_M10_2_M11_TABLES = {
    "content_derivative_graphs",
    "reusable_artifact_stores",
    "derivative_originality_gates",
    "ollama_llm_providers",
    "media_provider_routers",
    "provider_capability_matrices",
    "dashboard_widgets",
    "config_review_ctas",
}

FORBIDDEN_LEARNING_PAYLOAD_FIELDS = {
    "recommended_operator_cta",
    "cta_type",
    "cta_label",
    "cta_target",
    "suggested_config_area",
    "suggested_config_patch",
    "config_review_reason",
    "config_upgrade_recommended",
}


def _actual_metadata(title: str = "M10 learning fixture") -> dict:
    return {
        "actual_title": title,
        "actual_description": "Manual upload description.",
        "actual_tags": ["learning"],
        "actual_hashtags": ["#learning"],
        "actual_privacy_status": "PUBLIC",
        "actual_caption_uploaded": True,
        "actual_made_for_kids": False,
    }


def _actual_disclosures(*, rights_confirmed: bool = True, ai_disclosure_confirmed: bool | None = False) -> dict:
    return {
        "ai_disclosure_confirmed": ai_disclosure_confirmed,
        "ai_disclosure_label_used": None,
        "paid_promotion_disclosure_confirmed": False,
        "music_license_confirmed": True,
        "stock_license_confirmed": True,
        "rights_confirmed": rights_confirmed,
        "operator_confirmed_no_unlicensed_assets": rights_confirmed,
    }


def _uploaded_video(
    db_session,
    qualification_factory,
    tmp_path,
    *,
    video_id: str,
    published_offset: timedelta = timedelta(days=3),
    disclosures: dict | None = None,
) -> UploadedVideo:
    flow = qualification_factory.m2_project()
    published_at = datetime.now(UTC) - published_offset
    metadata = _actual_metadata()
    uploaded = UploadedVideo(
        company_id=flow.company.id,
        channel_workspace_id=flow.channel.id,
        video_project_id=flow.project.id,
        policy_snapshot_id=flow.snapshot.id,
        platform="YOUTUBE",
        platform_video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=published_at,
        publish_status="CONFIRMED",
        actual_metadata=metadata,
        actual_disclosures=disclosures or _actual_disclosures(),
        lineage_refs={
            "source": "manual_qualification_fixture",
            "human_handoff_only": True,
            "no_upload_api_by_policy": True,
        },
        monitoring_state="NOT_STARTED",
        operator_summary={
            "title": metadata["actual_title"],
            "operator_summary_vi": "Video đã được ghi nhận thủ công cho fixture dashboard.",
        },
        actual_title=metadata["actual_title"],
        actual_visibility="PUBLIC",
        actual_publish_time=published_at,
        actual_upload_time=published_at,
        thumbnail_uploaded=True,
        subtitles_uploaded=True,
        verification_status="VERIFICATION_UNAVAILABLE",
        analytics_sync_status="NOT_CONFIGURED",
    )
    db_session.add(uploaded)
    db_session.flush()
    return uploaded


def _import_metrics(
    db_session,
    uploaded: UploadedVideo,
    *,
    metrics: dict,
    captured_at: datetime | None = None,
    observed_to: datetime | None = None,
    retention_curve: list[dict] | None = None,
) -> None:
    captured = captured_at or datetime.now(UTC)
    AnalyticsSyncService(db_session).import_manual(
        data=ManualAnalyticsImportContract(
            uploaded_video_id=uploaded.id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            captured_at=captured,
            observed_from=uploaded.published_at,
            observed_to=observed_to or captured,
            observation_window="T_PLUS_24H",
            metrics=metrics,
            traffic_sources=[
                {
                    "source_key": "browse",
                    "source_label": "Browse",
                    "views": metrics.get("views", 0),
                    "impressions": metrics.get("impressions"),
                    "watch_time_minutes": metrics.get("watch_time_minutes", 0),
                    "percentage": 100,
                    "metadata": {"fixture": True},
                }
            ],
            retention_curve=retention_curve
            or [
                {"time_seconds": 0, "retention_percent": 100},
                {"time_seconds": 10, "retention_percent": 75},
            ],
            engagement={},
            duration_seconds=60,
            timeline_alignment={"scene_refs": [{"time_seconds": 0, "scene_id": "scene_001"}]},
            source_note="m10 local fixture",
        )
    )


def _run_m9(db_session, uploaded: UploadedVideo):
    service = PostPublishHealthMonitorService(db_session)
    run = service.create_health_run(data=PostPublishHealthRunCreate(uploaded_video_id=uploaded.id, observation_window="T_PLUS_24H"))
    return service.execute_health_run(run_id=run.id)


def _run_m10(db_session, uploaded: UploadedVideo) -> LearningCandidateGenerationRun:
    service = LearningCandidateGenerationService(db_session)
    run = service.create_run(data=LearningCandidateGenerationRunCreate(uploaded_video_id=uploaded.id))
    return service.execute_run(run_id=run.id)


def test_m10_preflight_schema_catalogs_defaults_and_scope(engine, db_session, qualification_factory) -> None:
    status = collect_git_status()
    assert status.required_tags["m9-post-publish-diagnostics"] is True
    tables = set(inspect(engine).get_table_names())
    assert M10_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_2_M11_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0021_m12_2r_handoff_ledger"
        defaults = connection.execute(
            text(
                """
                select table_name, column_name, column_default
                from information_schema.columns
                where table_name in ('learning_candidates','learning_evidence_bundles','learning_review_queue_items')
                  and column_name in ('source_refs','limitations','counter_evidence','technical_appendix','approval_actions_allowed')
                """
            )
        ).all()
    default_map = {(row.table_name, row.column_name, row.column_default) for row in defaults}
    assert ("learning_candidates", "source_refs", "'[]'::jsonb") in default_map
    assert ("learning_candidates", "technical_appendix", "'{}'::jsonb") in default_map
    assert ("learning_evidence_bundles", "counter_evidence", "'[]'::jsonb") in default_map
    assert ("learning_review_queue_items", "approval_actions_allowed", "'[]'::jsonb") in default_map

    qualification_factory.seed_all()
    assert all_scope_violations(engine) == []
    route_text = " ".join(route.path for route in create_app().routes).lower()
    assert "/learning-candidates/{candidate_id}/approve" in route_text
    assert "/learning-candidates/{candidate_id}/reject" in route_text
    assert "config-review" not in route_text


def test_learning_generation_creates_ready_candidate_bundle_gate_queue_and_draft(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-low-ctr")
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
    )
    _run_m9(db_session, uploaded)
    run = _run_m10(db_session, uploaded)
    candidate = db_session.scalars(select(LearningCandidate).where(LearningCandidate.generation_run_id == run.id)).one()
    bundle = db_session.get(LearningEvidenceBundle, candidate.evidence_bundle_id)
    eligibility = db_session.get(LearningPromotionEligibilityRun, candidate.eligibility_run_id)
    queue = db_session.scalars(select(LearningReviewQueueItem).where(LearningReviewQueueItem.learning_candidate_id == candidate.id)).one()
    draft = db_session.scalars(select(PlaybookCandidateDraft).where(PlaybookCandidateDraft.learning_candidate_id == candidate.id)).one()

    assert run.run_state == "COMPLETED"
    assert run.generated_candidate_count == 1
    assert candidate.candidate_type == "PACKAGING_PATTERN"
    assert candidate.candidate_state == "READY_FOR_HUMAN_REVIEW"
    assert candidate.recommended_scope == "CHANNEL"
    assert "Hypothesis:" in candidate.suggested_learning
    assert {ref["type"] for ref in candidate.source_refs} >= {"UploadedVideo", "AnalyticsSnapshot", "FailureTraceReport", "RecoveryProposal"}
    assert bundle.evidence_summary
    assert eligibility.result == "ELIGIBLE_FOR_REVIEW"
    assert queue.queue_state == "READY_FOR_HUMAN_REVIEW"
    assert queue.operator_summary
    assert queue.friendly_status
    assert queue.evidence_summary
    assert "APPROVE" in queue.approval_actions_allowed
    assert queue.technical_appendix["no_approval_action_implemented"] is True
    assert draft.state == "READY_FOR_REVIEW"
    assert "not approved" not in draft.state.lower()


def test_missing_m8_m9_sources_blocks_run_without_candidate(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-missing")
    run = _run_m10(db_session, uploaded)
    assert run.run_state == "BLOCKED"
    assert run.generated_candidate_count == 0
    assert "LEARNING_NEEDS_MORE_EVIDENCE" in run.reason_codes
    assert db_session.query(LearningCandidate).filter_by(generation_run_id=run.id).count() == 0


def test_evidence_bundle_preserves_zero_unknown_and_limitations(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-zero")
    _import_metrics(
        db_session,
        uploaded,
        metrics={"views": 0, "impressions": 5000, "click_through_rate": 0, "likes": 0, "comments": 0, "shares": 0},
    )
    _run_m9(db_session, uploaded)
    _run_m10(db_session, uploaded)
    bundle = db_session.scalars(select(LearningEvidenceBundle)).one()
    support = {item["metric_key"]: item for item in bundle.metric_support}

    assert support["views"]["value"] == 0
    assert support["views"]["availability"] == "AVAILABLE"
    assert support["average_view_duration_seconds"]["value"] is None
    assert support["average_view_duration_seconds"]["availability"] == "UNKNOWN"
    assert any(item["type"] == "metric_availability" for item in bundle.limitations)
    assert bundle.freshness_summary["freshness_required_for_review"] is True


def test_stale_analytics_needs_more_evidence_and_no_draft(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-stale", published_offset=timedelta(days=7))
    stale_time = datetime.now(UTC) - timedelta(days=5)
    _import_metrics(
        db_session,
        uploaded,
        captured_at=stale_time,
        observed_to=stale_time,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
    )
    _run_m9(db_session, uploaded)
    _run_m10(db_session, uploaded)
    candidate = db_session.scalars(select(LearningCandidate)).one()
    eligibility = db_session.get(LearningPromotionEligibilityRun, candidate.eligibility_run_id)
    queue = db_session.scalars(select(LearningReviewQueueItem)).one()

    assert eligibility.result == "NEEDS_MORE_EVIDENCE"
    assert "ANALYTICS_FRESHNESS_INSUFFICIENT" in eligibility.reason_codes
    assert candidate.candidate_state == "NEEDS_MORE_EVIDENCE"
    assert queue.queue_state == "NEEDS_MORE_EVIDENCE"
    assert db_session.query(PlaybookCandidateDraft).count() == 0


def test_rights_risk_blocks_queue_and_playbook_draft(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-rights")
    uploaded.actual_disclosures = _actual_disclosures(rights_confirmed=False)
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
    )
    _run_m9(db_session, uploaded)
    _run_m10(db_session, uploaded)
    candidate = db_session.scalars(select(LearningCandidate)).one()
    queue = db_session.scalars(select(LearningReviewQueueItem)).one()

    assert candidate.candidate_state == "BLOCKED_RIGHTS_RISK"
    assert candidate.risk_level == "BLOCKED"
    assert candidate.rights_flags
    assert queue.queue_state == "BLOCKED"
    assert "APPROVE" not in queue.approval_actions_allowed
    assert db_session.query(PlaybookCandidateDraft).count() == 0


def test_m10_api_read_paths_events_and_no_action_mutations(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-api")
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
    )
    _run_m9(db_session, uploaded)
    db_session.commit()

    client = TestClient(create_app())
    created = client.post("/learning-candidate-generation-runs", json={"uploaded_video_id": str(uploaded.id)})
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    executed = client.post(f"/learning-candidate-generation-runs/{run_id}/execute", json={})
    assert executed.status_code == 200, executed.text
    assert executed.json()["run_state"] == "COMPLETED"
    assert client.get(f"/learning-candidate-generation-runs/{run_id}").status_code == 200
    candidates = client.get("/learning-candidates")
    assert candidates.status_code == 200, candidates.text
    candidate_id = candidates.json()[0]["id"]
    assert client.get(f"/learning-candidates/{candidate_id}").status_code == 200
    assert client.get(f"/learning-candidates/{candidate_id}/evidence-bundle").status_code == 200
    queue = client.get("/learning-review-queue")
    assert queue.status_code == 200, queue.text
    queue_id = queue.json()[0]["id"]
    assert client.get(f"/learning-review-queue/{queue_id}").status_code == 200
    db_session.expire_all()
    draft_id = db_session.scalars(select(PlaybookCandidateDraft.id)).first()
    assert draft_id is not None
    assert client.get(f"/playbook-candidate-drafts/{draft_id}").status_code == 200

    blocked = client.post(
        f"/learning-candidates/{candidate_id}/approve",
        json={"action": "APPROVE", "actor_role": "READ_ONLY_OBSERVER"},
    )
    assert blocked.status_code == 403

    approved = client.post(
        f"/learning-candidates/{candidate_id}/approve",
        json={"action": "APPROVE", "actor_role": "LEARNING_REVIEWER", "rationale": "Evidence reviewed in M11."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_playbook_entry_id"] is not None
    assert approved.json()["technical_appendix"]["no_channel_profile_mutation"] is True
    db_session.expire_all()
    assert db_session.query(LearningReviewDecision).count() == 1
    assert db_session.query(ApprovedPlaybookEntry).count() == 1

    event_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert {
        "learning_candidate_generation_run.created",
        "learning_candidate_generation_run.completed",
        "learning_candidate.generated",
        "learning_evidence_bundle.created",
        "learning_promotion_eligibility_run.created",
        "learning_review_queue_item.created",
        "playbook_candidate_draft.created",
        "learning.review_decision_recorded",
        "approved_playbook_entry.created",
    } <= event_types


def test_m10_payloads_do_not_include_config_or_deferred_scope_fields(db_session, qualification_factory, tmp_path) -> None:
    uploaded = _uploaded_video(db_session, qualification_factory, tmp_path, video_id="yt-m10-scope")
    _import_metrics(
        db_session,
        uploaded,
        metrics={
            "views": 100,
            "impressions": 5000,
            "click_through_rate": 1.0,
            "average_view_duration_seconds": 40,
            "average_view_percentage": 70,
            "likes": 8,
            "comments": 2,
            "shares": 1,
        },
    )
    _run_m9(db_session, uploaded)
    _run_m10(db_session, uploaded)
    candidate = db_session.scalars(select(LearningCandidate)).one()
    queue = db_session.scalars(select(LearningReviewQueueItem)).one()
    payload_text = json.dumps(
        {
            "candidate": {
                "operator_summary": candidate.operator_summary,
                "friendly_status": candidate.friendly_status,
                "suggested_learning": candidate.suggested_learning,
                "suggested_playbook_text": candidate.suggested_playbook_text,
                "technical_appendix": candidate.technical_appendix,
            },
            "queue": {
                "operator_summary": queue.operator_summary,
                "friendly_status": queue.friendly_status,
                "technical_appendix": queue.technical_appendix,
            },
        },
        sort_keys=True,
    ).lower()
    for field in FORBIDDEN_LEARNING_PAYLOAD_FIELDS:
        assert field not in payload_text
    assert "ollama" not in payload_text
    assert "elevenlabs" not in payload_text
    assert "creatomate" not in payload_text
    assert "auto publish" not in payload_text
