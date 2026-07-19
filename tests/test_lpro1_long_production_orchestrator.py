from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.contracts.d2p1 import DailyToPackageRequest
from app.contracts.workflow import ApprovalDecisionCreate
from app.db.models import FinalMediaRef, ProviderAttempt, ReviewTask
from app.main import create_app
from app.services.d2p1 import DailyToPackageOrchestrator
from app.services.workflow import ApprovalService
from tests.test_d2p1_daily_to_package_bridge import (
    _OfflinePackageService,
    _approve_research,
    _d2p_scope,
)


def _promote_package(db_session):
    scope = _d2p_scope(db_session)
    orchestrator = DailyToPackageOrchestrator(
        db_session,
        package_service=_OfflinePackageService(db_session),
    )
    request = DailyToPackageRequest(
        daily_idea_decision_id=scope.decision.id,
        created_by_user_id=scope.operator.id,
    )
    assert orchestrator.run(request).current_state == "AWAITING_RESEARCH"
    _approve_research(db_session, scope)
    pending = orchestrator.run(request)
    assert pending.current_state == "PACKAGE_READY_FOR_HUMAN_REVIEW"
    reviewed_version_id = uuid.UUID(pending.receipt["artifact_version_id"])
    review = db_session.scalars(
        select(ReviewTask).where(
            ReviewTask.target_artifact_version_id == reviewed_version_id,
            ReviewTask.review_type == "final_human",
        )
    ).one()
    review.status = "completed"
    ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=reviewed_version_id,
            target_artifact_version_id=reviewed_version_id,
            decision="approved",
            decided_by_user_id=scope.admin.id,
            rationale="LPRO1 exact package handoff fixture review PASS.",
            evidence_basis={"review_task_id": str(review.id)},
        )
    )
    promoted = orchestrator.run(request)
    assert promoted.current_state == "READY_FOR_LONG_PRODUCTION"
    return scope, promoted


def test_application_trigger_runs_real_local_fixture_to_reviewable_mp4_and_resumes(db_session) -> None:
    scope, promoted = _promote_package(db_session)
    project_id = promoted.project["id"]
    package_id = promoted.package["id"]
    db_session.commit()

    with TestClient(create_app()) as client:
        gated = client.post(
            f"/video-projects/{project_id}/long-production/run",
            json={"package_id": package_id, "execution_mode": "REAL_APPROVED_PRODUCTION"},
        )
        assert gated.status_code != 200
        assert "LPRO1_MR1_EXECUTION_ENVELOPE_REQUIRED" in gated.text

        first = client.post(
            f"/video-projects/{project_id}/long-production/run",
            json={"package_id": package_id, "execution_mode": "OFFLINE_FIXTURE"},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["current_state"] == "READY_FOR_HUMAN_REVIEW"
        assert body["provider_calls"] == 0
        assert body["render_attempts"] == 1
        assert body["final_media_ref"] is None
        assert body["state_transitions"][-4:] == [
            "RENDERED_AWAITING_TECHNICAL_QC",
            "TECHNICAL_QC_PASSED",
            "CREATIVE_REVIEW_REQUIRED",
            "READY_FOR_HUMAN_REVIEW",
        ]

        candidate = json.loads(Path(body["review_media_candidate_ref"]).read_text(encoding="utf-8"))
        output = Path(candidate["output_file_ref"])
        assert output.is_file() and output.stat().st_size > 1_000_000
        assert candidate["production_eligible"] is False
        assert candidate["not_publishable"] is True
        assert candidate["human_review_status"] == "PENDING"
        mtime = output.stat().st_mtime_ns

        resumed = client.post(
            f"/video-projects/{project_id}/long-production/run",
            json={"package_id": package_id, "execution_mode": "OFFLINE_FIXTURE"},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["run_id"] == body["run_id"]
        assert resumed.json()["content_hash"] == body["content_hash"]
        assert output.stat().st_mtime_ns == mtime

        status = client.get(f"/video-projects/{project_id}/long-production")
        assert status.status_code == 200, status.text
        assert status.json()["current_state"] == "READY_FOR_HUMAN_REVIEW"
        assert status.json()["technical_qc_status"] == "PASS"
        assert status.json()["creative_qc_status"] == "REVIEW_REQUIRED"
        assert status.json()["final_media_ref_status"] == "NOT_CREATED"

    probe = subprocess.run(
        [
            "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    media = json.loads(probe.stdout)
    video = next(item for item in media["streams"] if item["codec_type"] == "video")
    audio = next(item for item in media["streams"] if item["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"], video["pix_fmt"]) == (
        1920,
        1080,
        "h264",
        "yuv420p",
    )
    assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == ("aac", 48000, 2)
    technical = json.loads(Path(body["technical_media_qc_ref"]).read_text(encoding="utf-8"))
    creative = json.loads(Path(body["creative_media_qc_ref"]).read_text(encoding="utf-8"))
    strict = json.loads(
        (Path(body["review_media_candidate_ref"]).parent / "strict-long-form-render-package.json").read_text(
            encoding="utf-8"
        )
    )
    assert technical["result"] == "PASS"
    assert creative["result"] == "REVIEW_REQUIRED"
    assert {item["preferred_route"] for item in strict["visual_source_decisions"]} == {
        "NATIVE_DIAGRAM",
        "PEXELS_VIDEO",
        "AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY",
    }
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0
    assert db_session.scalar(select(func.count()).select_from(FinalMediaRef)) == 0
