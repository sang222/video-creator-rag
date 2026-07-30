from __future__ import annotations

import runpy
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models.m10_5 import CloudMediaRef
from app.db.models.ops import OpsIncident
from app.services.operator_cockpit import OperatorCockpitService
from app.services.production_publish import ProductionPublishService


ROOT = Path(__file__).resolve().parents[1]
_PHASE5 = runpy.run_path(str(ROOT / "tests/test_phase5_final_publish.py"))
_ready_final = _PHASE5["_ready_final"]
_decide_upload = _PHASE5["_decide_upload"]
_submit = _PHASE5["_submit"]
_verification_data = _PHASE5["_verification_data"]
_actor = _PHASE5["_actor"]


def test_cockpit_requires_no_gate_action_before_final_media(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False)

    cockpit = OperatorCockpitService(db_session).build(
        actor=_actor(ready.scope), project_id=ready.scope.project.id
    )

    assert cockpit.next_video is not None
    assert cockpit.next_video.lane == "LONG_FORM"
    assert cockpit.next_video.content_mode == "STANDALONE"
    assert cockpit.next_video.operator_action == "NONE"
    assert cockpit.next_video.current_stage == "ARCHIVE"
    assert cockpit.progress is not None
    assert cockpit.progress.archive_status == "VERIFIED"
    assert cockpit.progress.operator_action == "NONE"
    assert cockpit.final_review is None
    assert cockpit.manual_publish is None
    _assert_no_local_paths(cockpit.model_dump(mode="json"))


def test_cockpit_projects_final_review_and_verified_manual_publish(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session)
    assert ready.candidate is not None
    cloud = _attach_drive_media(db_session, ready)

    review_cockpit = OperatorCockpitService(db_session).build(
        actor=_actor(ready.scope), project_id=ready.scope.project.id
    )
    assert review_cockpit.final_review is not None
    assert review_cockpit.final_review.state == "READY_FOR_FINAL_REVIEW"
    assert review_cockpit.final_review.title == "Exact reviewed title"
    assert review_cockpit.final_review.media.file_name == "final.mp4"
    assert review_cockpit.final_review.media.drive_web_view_url == cloud.web_view_link
    assert review_cockpit.final_review.media.player_url is None
    assert review_cockpit.final_review.archive_status == "VERIFIED"
    assert review_cockpit.next_video is not None
    assert review_cockpit.next_video.operator_action == "FINAL_REVIEW"
    _assert_no_local_paths(review_cockpit.model_dump(mode="json"))

    _result, task = _decide_upload(db_session, ready)
    task_cockpit = OperatorCockpitService(db_session).build(
        actor=_actor(ready.scope), project_id=ready.scope.project.id
    )
    assert task_cockpit.final_review is not None
    assert task_cockpit.final_review.decision == "UPLOAD"
    assert task_cockpit.manual_publish is not None
    assert task_cockpit.manual_publish.task_id == task.id
    assert task_cockpit.manual_publish.state == "READY_FOR_OPERATOR"
    assert task_cockpit.manual_publish.exact_file_name == "final.mp4"
    assert (
        task_cockpit.manual_publish.reviewed_checksum_sha256
        == ready.final_media.checksum_sha256
    )
    assert task_cockpit.manual_publish.drive_web_view_url == cloud.web_view_link
    assert (
        task_cockpit.manual_publish.technical_appendix["archive_object_ref"]
        == ready.candidate_data.archive_object_ref
    )

    confirmation = _submit(db_session, ready, task)
    confirmation_cockpit = OperatorCockpitService(db_session).build(
        actor=_actor(ready.scope), project_id=ready.scope.project.id
    )
    assert confirmation_cockpit.manual_publish is not None
    assert (
        confirmation_cockpit.manual_publish.actual_published_at
        == confirmation.actual_published_at
    )
    assert confirmation_cockpit.manual_publish.actual_duration_seconds == float(
        confirmation.actual_duration_seconds
    )
    assert confirmation_cockpit.manual_publish.technical_appendix[
        "confirmation_state"
    ] in {"SUBMITTED", "VARIANCE_ACCEPTED"}
    verified = ProductionPublishService(db_session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_verification_data(confirmation),
        actor=_actor(ready.scope),
    )
    assert verified.status == "VERIFIED"

    verified_cockpit = OperatorCockpitService(db_session).build(
        actor=_actor(ready.scope), project_id=ready.scope.project.id
    )
    assert verified_cockpit.manual_publish is not None
    assert verified_cockpit.manual_publish.state == "VERIFIED"
    assert verified_cockpit.manual_publish.mismatch_state == "MATCHED"
    assert verified_cockpit.manual_publish.uploaded_video_status == "VERIFIED"
    assert verified_cockpit.manual_publish.analytics_ready is True
    assert verified_cockpit.manual_publish.platform_video_id == "phase5-video"
    _assert_no_local_paths(verified_cockpit.model_dump(mode="json"))


def test_cockpit_surfaces_incident_blocker_and_learning_exclusion(
    db_session: Session,
) -> None:
    ready = _ready_final(db_session, create_candidate=False)
    incident = OpsIncident(
        incident_type="PROVIDER_OUTAGE",
        severity="ERROR",
        state="OPEN",
        project_id=ready.scope.project.id,
        workflow_run_id=ready.workflow.id,
        stage="MEDIA",
        retry_eligible=False,
        learning_excluded=True,
        operator_visible_blocker="Nhà cung cấp chưa sẵn sàng.",
        reason_codes=["PROVIDER_UNAVAILABLE"],
        next_action="Kiểm tra cấu hình nhà cung cấp rồi tiếp tục.",
    )
    db_session.add(incident)
    db_session.flush()

    cockpit = OperatorCockpitService(db_session).build(
        actor=_actor(ready.scope), project_id=ready.scope.project.id
    )

    assert cockpit.next_video is not None
    assert cockpit.next_video.operator_action == "RESOLVE_INCIDENT"
    assert cockpit.next_video.blocker == "Nhà cung cấp chưa sẵn sàng."
    assert cockpit.progress is not None
    assert cockpit.progress.blocking_incident == "Nhà cung cấp chưa sẵn sàng."
    assert cockpit.progress.technical_appendix["incident_id"] == incident.id
    assert cockpit.progress.technical_appendix["learning_excluded"] is True
    _assert_no_local_paths(cockpit.model_dump(mode="json"))


def _attach_drive_media(db_session: Session, ready: object) -> CloudMediaRef:
    cloud = db_session.get(
        CloudMediaRef,
        ready.final_media.cloud_media_ref_id,
    )
    assert cloud is not None
    return cloud


def _assert_no_local_paths(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert key not in {
                "local_path",
                "source_path",
                "output_path",
                "working_dir",
                "temp_path",
            }
            _assert_no_local_paths(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_local_paths(item)
    elif isinstance(value, str):
        assert not value.startswith("/")
        assert not value.startswith("file://")
        assert "workspace://" not in value
