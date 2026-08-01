from __future__ import annotations

import json
import runpy
import secrets
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.time import utc_now
from app.db.models import (
    FinalMediaRef,
    OperatorAuthSession,
    OperatorUser,
    ProviderAttempt,
)
from app.main import create_app
from app.services.m11_1 import AUTH_COOKIE_NAME, hash_session_token
from app.services.production_package import ProductionReadinessService


ROOT = Path(__file__).resolve().parents[1]
_PHASE3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
_phase3_scope = _PHASE3["_scope"]
_phase3_create_package = _PHASE3["_create_package"]


def _promote_package(db_session):
    scope = _phase3_scope(
        db_session,
        minimum_ms=1_500,
        target_ms=2_000,
        maximum_ms=3_500,
    )
    package = _phase3_create_package(db_session, scope)
    readiness = ProductionReadinessService(db_session).evaluate(
        package_artifact_version_id=package.artifact_version_id,
        created_by_user_id=scope.operator.id,
    )
    assert readiness.status == "READY_FOR_PRODUCTION"
    return scope, SimpleNamespace(
        project={"id": scope.project.id},
        package={"id": package.artifact_version_id},
    )


def test_application_trigger_runs_real_local_fixture_to_reviewable_mp4_and_resumes(
    db_session,
) -> None:
    scope, promoted = _promote_package(db_session)
    project_id = promoted.project["id"]
    package_id = promoted.package["id"]
    token = secrets.token_urlsafe(48)
    operator_user = OperatorUser(
        canonical_user_id=scope.operator.id,
        email=scope.operator.email,
        password_hash="not-used-by-session-auth",
        display_name=scope.operator.display_name,
        role="OWNER_ADMIN",
        status="ACTIVE",
    )
    db_session.add(operator_user)
    db_session.flush()
    db_session.add(
        OperatorAuthSession(
            user_id=operator_user.id,
            session_token_hash=hash_session_token(token),
            expires_at=utc_now() + timedelta(hours=1),
        )
    )
    db_session.commit()

    with TestClient(create_app()) as client:
        client.cookies.set(AUTH_COOKIE_NAME, token)
        gated = client.post(
            f"/video-projects/{project_id}/long-production/run",
            json={
                "package_id": package_id,
                "execution_mode": "REAL_APPROVED_PRODUCTION",
            },
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

        candidate = json.loads(
            Path(body["review_media_candidate_ref"]).read_text(encoding="utf-8")
        )
        output = Path(candidate["output_file_ref"])
        assert output.is_file() and output.stat().st_size > 10_000
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
    assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == (
        "aac",
        48000,
        2,
    )
    technical = json.loads(
        Path(body["technical_media_qc_ref"]).read_text(encoding="utf-8")
    )
    creative = json.loads(
        Path(body["creative_media_qc_ref"]).read_text(encoding="utf-8")
    )
    strict = json.loads(
        (
            Path(body["review_media_candidate_ref"]).parent
            / "strict-long-form-render-package.json"
        ).read_text(encoding="utf-8")
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
