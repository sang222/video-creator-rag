from __future__ import annotations

import runpy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m8 import ManualAnalyticsImportContract
from app.contracts.m9 import PostPublishHealthRunCreate
from app.core.time import utc_now
from app.db.models import DomainEvent, OpsIncident, UploadedVideo
from app.services.m8 import AnalyticsSyncService
from app.services.m9 import PostPublishHealthMonitorService
from app.services.long_form_analytics import (
    ANALYTICS_WINDOW_EVENT_TYPE,
    PRIMARY_METRIC_AUTHORITY,
    WINDOW_DELTAS,
    LongFormAnalyticsScheduler,
)


ROOT = Path(__file__).resolve().parents[1]
_PHASE5 = runpy.run_path(str(ROOT / "tests/test_phase5_final_publish.py"))


def _uploaded(session: Session):
    ready = _PHASE5["_ready_final"](session)
    _result, task = _PHASE5["_decide_upload"](session, ready)
    confirmation = _PHASE5["_submit"](session, ready, task)
    result = _PHASE5["ProductionPublishService"](session).verify_confirmation(
        confirmation_id=confirmation.id,
        data=_PHASE5["_verification_data"](confirmation),
        actor=_PHASE5["_actor"](ready.scope),
    )
    assert result.uploaded_video is not None
    return result.uploaded_video


def test_analytics_ready_schedules_exactly_four_idempotent_windows(
    db_session: Session,
) -> None:
    uploaded = _uploaded(db_session)
    scheduler = LongFormAnalyticsScheduler(db_session)

    windows = scheduler.list_windows(uploaded.id)
    assert [item.window_type for item in windows] == ["H24", "H72", "D7", "D30"]
    assert {item.metric_authority for item in windows} == {PRIMARY_METRIC_AUTHORITY}
    assert all(item.production_lane == "LONG_FORM" for item in windows)
    assert all(item.content_mode in {"SERIES_EPISODE", "STANDALONE"} for item in windows)

    repeated = scheduler.schedule_uploaded_video(uploaded.id)
    assert [item.id for item in repeated] == [item.id for item in windows]
    for window in windows:
        assert window.scheduled_for == uploaded.published_at + WINDOW_DELTAS[window.window_type]
        assert window.minimum_maturity_at == window.scheduled_for


def test_early_window_waits_without_provider_call(db_session: Session) -> None:
    uploaded = _uploaded(db_session)
    early_now = uploaded.published_at + timedelta(hours=1)
    scheduler = LongFormAnalyticsScheduler(
        db_session,
        now=lambda: early_now,
        owner_sync=lambda _uploaded_video_id: (_ for _ in ()).throw(
            AssertionError("provider must not run before maturity")
        ),
    )
    window = scheduler.list_windows(uploaded.id)[0]

    result = scheduler.execute_window(window.id)

    assert result.state == "WAITING_FOR_MATURITY"
    assert result.next_attempt_at == result.minimum_maturity_at
    assert "OBSERVATION_WINDOW_NOT_MATURE" in result.reason_codes


def test_due_event_is_exactly_once_and_missing_owner_auth_is_honest(
    db_session: Session,
) -> None:
    uploaded = _uploaded(db_session)
    mature_now = uploaded.published_at + timedelta(hours=24, seconds=1)
    scheduler = LongFormAnalyticsScheduler(
        db_session,
        now=lambda: mature_now,
        owner_sync=lambda *_args: SimpleNamespace(
            run_state="NEEDS_AUTH",
            error_code="YOUTUBE_OWNER_ANALYTICS_NEEDS_AUTH",
        ),
    )

    assert scheduler.enqueue_due_windows() == 1
    assert scheduler.enqueue_due_windows() == 0
    events = db_session.scalars(
        select(DomainEvent).where(DomainEvent.event_type == ANALYTICS_WINDOW_EVENT_TYPE)
    ).all()
    assert len(events) == 1

    result = scheduler.execute_window(scheduler.list_windows(uploaded.id)[0].id)

    assert result.state == "BLOCKED_AUTH"
    assert result.analytics_snapshot_id is None
    assert "YOUTUBE_OWNER_ANALYTICS_NEEDS_AUTH" in result.reason_codes
    incident = db_session.scalars(
        select(OpsIncident).where(OpsIncident.uploaded_video_id == uploaded.id)
    ).one()
    assert incident.learning_excluded is True
    assert "ANALYTICS_AUTH_FAILURE" in incident.reason_codes


def test_health_run_keeps_the_exact_phase_e_snapshot_not_the_latest(
    db_session: Session,
) -> None:
    uploaded = _uploaded(db_session)
    uploaded_row = db_session.get(UploadedVideo, uploaded.id)
    assert uploaded_row is not None
    uploaded_row.monitoring_state = "READY_FOR_ANALYTICS"
    db_session.flush()
    captured_at = utc_now()
    first = AnalyticsSyncService(db_session).import_manual(
        data=ManualAnalyticsImportContract(
            uploaded_video_id=uploaded.id,
            platform="YOUTUBE",
            platform_video_id=uploaded.platform_video_id,
            captured_at=captured_at,
            observed_from=uploaded.published_at,
            observed_to=uploaded.published_at + timedelta(hours=24),
            observation_window="H24",
            metrics={"views": 1},
            source_note="phase-e exact H24 fixture",
        )
    )
    latest = AnalyticsSyncService(db_session).import_manual(
        data=ManualAnalyticsImportContract(
            uploaded_video_id=uploaded.id,
            platform="YOUTUBE",
            platform_video_id=uploaded.platform_video_id,
            captured_at=captured_at + timedelta(seconds=1),
            observed_from=uploaded.published_at,
            observed_to=uploaded.published_at + timedelta(days=7),
            observation_window="D7",
            metrics={"views": 99},
            source_note="later snapshot must not replace H24 evidence",
        )
    )

    run = PostPublishHealthMonitorService(db_session).create_health_run(
        data=PostPublishHealthRunCreate(
            uploaded_video_id=uploaded.id,
            observation_window="H24",
            analytics_snapshot_id=first.id,
        )
    )

    assert latest.id != first.id
    assert run.analytics_snapshot_id == first.id
