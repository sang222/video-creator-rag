from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import get_args

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.production_publish import UploadedVideoReadV2
from app.contracts.youtube_delivery import ResumableUploadStatus
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.db.models.foundation import DomainEvent
from app.db.models.youtube_delivery import (
    YouTubeComponentAttempt,
    YouTubeComponentReceipt,
    YouTubePrivateStage,
    YouTubeUploadAttempt,
)
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.outbox_dispatcher import DurableOutboxDispatcher, HumanWaitDisposition
from app.services.youtube_delivery import (
    LocalSessionSecretStore,
    ResolvedMediaBytes,
    VerifiedMediaByteSourceResolver,
    YouTubeDataApiTransport,
    YouTubePrivateStageExecutor,
    _normalize_youtube_readback,
    _youtube_insert_body,
    _validate_public_release_observation,
    YOUTUBE_PUBLICATION_OBSERVATION_EVENT_TYPE,
)


@pytest.fixture
def db_session():
    engine = create_engine(get_settings().database_url, future=True, pool_pre_ping=True)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _insert_stage(session: Session, *, label: str) -> YouTubePrivateStage:
    """Insert only the aggregate under test; parent FKs are not the subject here."""

    stage = YouTubePrivateStage(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"youtube-private-stage:{label}"),
        company_id=uuid.uuid4(),
        channel_workspace_id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        final_review_candidate_id=uuid.uuid4(),
        final_video_decision_id=uuid.uuid4(),
        final_media_ref_id=uuid.uuid4(),
        final_media_ref="vcos-local-archive://fixture/" + "a" * 64 + "/final.mp4",
        final_media_checksum="a" * 64,
        publishing_credential_id=uuid.uuid4(),
        production_thumbnail_binding_id=uuid.uuid4(),
        caption_ref="fixture/captions.srt",
        caption_hash="b" * 64,
        staging_metadata={
            "privacy_status": "PRIVATE",
            "public_release_by_api": False,
            "snippet": {"title": "Fixture"},
            "status": {"privacyStatus": "private"},
        },
        staging_metadata_hash=content_hash({"label": f"staging:{label}"}),
        public_release_expectation={"manual_release_only": True},
        public_release_expectation_hash=content_hash({"label": f"public:{label}"}),
        state="PREPARED",
        identity_hash=content_hash({"label": f"identity:{label}"}),
    )
    # PostgreSQL FK enforcement is deliberately suppressed for this narrow
    # aggregate test.  All delivery table checks/uniqueness constraints remain
    # active; full repository migrations are applied by CI before this test.
    session.execute(text("SET session_replication_role = replica"))
    session.add(stage)
    session.flush()
    session.execute(text("SET session_replication_role = origin"))
    session.commit()
    return stage


def _insert_public_observation_event(
    session: Session, *, stage: YouTubePrivateStage, label: str, max_attempts: int
) -> DomainEvent:
    event_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"youtube-public-observation-event:{label}"
    )
    event = DomainEvent(
        id=event_id,
        event_type=YOUTUBE_PUBLICATION_OBSERVATION_EVENT_TYPE,
        event_version=1,
        aggregate_type="youtube_private_stage",
        aggregate_id=stage.id,
        company_id=stage.company_id,
        channel_workspace_id=None,
        correlation_id=f"youtube-public-observation:{stage.id}",
        causation_id=stage.id,
        command_id=f"youtube-public-observation:{label}",
        payload={
            "youtube_private_stage_id": str(stage.id),
            "stage_identity_hash": stage.identity_hash,
        },
        metadata_={
            "queue": "production-workflow",
            "delivery_authority": True,
            "at_most_once": True,
        },
        max_attempts=max_attempts,
        next_attempt_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.execute(text("SET session_replication_role = replica"))
    session.add(event)
    session.flush()
    session.execute(text("SET session_replication_role = origin"))
    session.commit()
    return event


class _SuccessfulUploadTransport:
    def __init__(self) -> None:
        self.create_calls = 0
        self.query_calls = 0
        self.upload_calls = 0

    def create_resumable_session(self, **_kwargs) -> str:
        self.create_calls += 1
        return "https://upload.youtube.test/session/exact"

    def query_resumable_session(self, **_kwargs) -> ResumableUploadStatus:
        self.query_calls += 1
        return ResumableUploadStatus(state="INCOMPLETE", committed_bytes=0)

    def upload_media(self, **kwargs) -> ResumableUploadStatus:
        self.upload_calls += 1
        return ResumableUploadStatus(
            state="COMPLETE",
            committed_bytes=kwargs["total_bytes"],
            platform_video_id="video-private-1",
            response_payload={"id": "video-private-1"},
        )


class _UnknownSessionTransport(_SuccessfulUploadTransport):
    def create_resumable_session(self, **_kwargs) -> str:
        self.create_calls += 1
        raise RuntimeError("connection lost after videos.insert")


def _executor(db_session: Session, *, transport, tmp_path: Path):
    factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    return YouTubePrivateStageExecutor(
        factory,
        transport=transport,
        secret_store=LocalSessionSecretStore(tmp_path / "secrets"),
    )


def test_resumable_upload_reuses_exact_session_and_never_inserts_twice(
    db_session: Session, tmp_path: Path
) -> None:
    stage = _insert_stage(db_session, label="success")
    media_path = tmp_path / "final.mp4"
    media_path.write_bytes(b"exact-private-video-bytes")
    media = ResolvedMediaBytes(
        path=media_path,
        checksum_sha256=hashlib.sha256(media_path.read_bytes()).hexdigest(),
        size_bytes=media_path.stat().st_size,
        mime_type="video/mp4",
    )
    transport = _SuccessfulUploadTransport()
    executor = _executor(db_session, transport=transport, tmp_path=tmp_path)
    snapshot = {
        "identity_hash": stage.identity_hash,
        "staging_metadata": stage.staging_metadata,
    }

    first = executor._upload_video(
        stage_id=stage.id,
        stage_snapshot=snapshot,
        media=media,
        access_token="test-token",
    )
    second = executor._upload_video(
        stage_id=stage.id,
        stage_snapshot=snapshot,
        media=media,
        access_token="test-token",
    )

    assert first == second == "video-private-1"
    assert transport.create_calls == 1
    assert transport.upload_calls == 1
    attempt = db_session.scalar(
        select(YouTubeUploadAttempt).where(
            YouTubeUploadAttempt.youtube_private_stage_id == stage.id
        )
    )
    db_session.refresh(attempt)
    assert attempt.state == "VERIFIED"
    assert attempt.attempt_number == 1
    assert attempt.outcome_certainty == "CERTAIN_SUCCESS"


def test_ambiguous_videos_insert_is_never_repeated(
    db_session: Session, tmp_path: Path
) -> None:
    stage = _insert_stage(db_session, label="unknown-session")
    media_path = tmp_path / "uncertain.mp4"
    media_path.write_bytes(b"uncertain-private-video-bytes")
    media = ResolvedMediaBytes(
        path=media_path,
        checksum_sha256=hashlib.sha256(media_path.read_bytes()).hexdigest(),
        size_bytes=media_path.stat().st_size,
        mime_type="video/mp4",
    )
    transport = _UnknownSessionTransport()
    executor = _executor(db_session, transport=transport, tmp_path=tmp_path)
    snapshot = {
        "identity_hash": stage.identity_hash,
        "staging_metadata": stage.staging_metadata,
    }

    with pytest.raises(
        ValidationFailureError,
        match="YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN",
    ):
        executor._upload_video(
            stage_id=stage.id,
            stage_snapshot=snapshot,
            media=media,
            access_token="test-token",
        )
    with pytest.raises(
        ValidationFailureError,
        match="YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN",
    ):
        executor._upload_video(
            stage_id=stage.id,
            stage_snapshot=snapshot,
            media=media,
            access_token="test-token",
        )

    assert transport.create_calls == 1
    attempt = db_session.scalar(
        select(YouTubeUploadAttempt).where(
            YouTubeUploadAttempt.youtube_private_stage_id == stage.id
        )
    )
    db_session.refresh(attempt)
    assert attempt.state == "OUTCOME_UNKNOWN"
    assert attempt.outcome_certainty == "UNCERTAIN"


def test_thumbnail_or_caption_component_is_at_most_once(
    db_session: Session, tmp_path: Path
) -> None:
    stage = _insert_stage(db_session, label="component")
    executor = _executor(
        db_session, transport=_SuccessfulUploadTransport(), tmp_path=tmp_path
    )
    calls: list[str] = []

    executor._write_component_once(
        stage_id=stage.id,
        component_type="THUMBNAIL",
        request_payload={"video_id": "video-1", "checksum": "c" * 64},
        call=lambda: calls.append("called") or {"etag": "thumbnail-etag"},
    )
    executor._write_component_once(
        stage_id=stage.id,
        component_type="THUMBNAIL",
        request_payload={"video_id": "video-1", "checksum": "c" * 64},
        call=lambda: calls.append("called-again") or {"etag": "thumbnail-etag"},
    )

    assert calls == ["called"]
    attempt = db_session.scalar(
        select(YouTubeComponentAttempt).where(
            YouTubeComponentAttempt.youtube_private_stage_id == stage.id
        )
    )
    receipt = db_session.scalar(
        select(YouTubeComponentReceipt).where(
            YouTubeComponentReceipt.youtube_private_stage_id == stage.id
        )
    )
    db_session.refresh(attempt)
    assert attempt.state == "VERIFIED"
    assert attempt.attempt_count == 1
    assert receipt.state == "VERIFIED"


def test_private_readback_contract_rejects_public_or_processing_pending() -> None:
    valid = {
        "id": "video-1",
        "snippet": {
            "channelId": "channel-1",
            "title": "Frozen title",
            "description": "Frozen description",
            "tags": ["one"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
        "processingDetails": {"processingStatus": "succeeded"},
        "vcosThumbnailVerified": True,
        "vcosCaptionVerified": True,
        "vcosEvidenceRef": "youtube-readback://fixture",
    }
    parsed = _normalize_youtube_readback(valid)
    assert parsed.privacy_status == "PRIVATE"
    assert parsed.processing_status == "SUCCEEDED"

    public = {**valid, "status": {**valid["status"], "privacyStatus": "public"}}
    with pytest.raises(ValueError):
        _normalize_youtube_readback(public)
    pending = {
        **valid,
        "processingDetails": {"processingStatus": "processing"},
    }
    with pytest.raises(ValueError):
        _normalize_youtube_readback(pending)


def test_upload_status_parser_honors_308_range_and_complete_video_id() -> None:
    incomplete = YouTubeDataApiTransport._upload_status(
        response=httpx.Response(308, headers={"Range": "bytes=0-9"}),
        total_bytes=20,
    )
    assert incomplete.state == "INCOMPLETE"
    assert incomplete.committed_bytes == 10
    complete = YouTubeDataApiTransport._upload_status(
        response=httpx.Response(200, json={"id": "video-2"}),
        total_bytes=20,
    )
    assert complete.state == "COMPLETE"
    assert complete.platform_video_id == "video-2"


def test_current_channel_policy_freezes_private_stage_and_human_public_release() -> None:
    loaded = ConfigRegistryService(None).validate_catalog(
        Path("config/channel_scoped_policy_catalog.yaml")
    )
    policy = ChannelScopedPolicy.model_validate(loaded.content["items"][0])
    assert policy.publish_policy.youtube_private_stage_required is True
    assert policy.publish_policy.manual_public_release_only is True
    assert policy.publish_policy.manual_upload_only is False
    assert policy.publish_policy.drive_archive_required is False
    assert policy.publish_policy.local_purge_after_archive_state == (
        "YOUTUBE_PRIVATE_VERIFIED"
    )
    assert policy.provider_usage_policy.youtube_public_release_api_allowed is False

class _BlockingUploadTransport(_SuccessfulUploadTransport):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def create_resumable_session(self, **_kwargs) -> str:
        self.create_calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("blocking upload transport timed out")
        return "https://upload.youtube.test/session/concurrent"


def test_complete_upload_status_requires_platform_video_id() -> None:
    with pytest.raises(ValueError, match="platform_video_id"):
        ResumableUploadStatus(state="COMPLETE", committed_bytes=1)


def test_uploaded_video_read_contract_accepts_v3_rows() -> None:
    annotation = UploadedVideoReadV2.model_fields["schema_version"].annotation
    assert set(get_args(annotation)) == {"v2", "v3", "v4"}


def test_private_insert_body_drops_local_only_controls() -> None:
    body = _youtube_insert_body(
        {
            "snippet": {
                "title": "Frozen title",
                "description": "Frozen description",
                "tags": ["one"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
            "privacy_status": "PRIVATE",
            "public_release_by_api": False,
            "exact_remote_bytes_unavailable": True,
            "synthetic_media_assessment": {"assessment_version": "v1"},
        }
    )
    assert body == {
        "snippet": {
            "title": "Frozen title",
            "description": "Frozen description",
            "tags": ["one"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }


def test_resumable_query_and_upload_send_authorization(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer exact-token"
        if request.headers.get("Content-Range", "").startswith("bytes */"):
            return httpx.Response(308, headers={"Range": "bytes=0-0"})
        return httpx.Response(200, json={"id": "video-authenticated"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = YouTubeDataApiTransport(client=client)
    queried = transport.query_resumable_session(
        access_token="exact-token",
        session_uri="https://upload.youtube.test/session/auth",
        total_bytes=2,
    )
    assert queried.committed_bytes == 1
    media_path = tmp_path / "auth.mp4"
    media_path.write_bytes(b"ab")
    uploaded = transport.upload_media(
        access_token="exact-token",
        session_uri="https://upload.youtube.test/session/auth",
        media_path=media_path,
        start_offset=1,
        total_bytes=2,
        mime_type="video/mp4",
    )
    assert uploaded.platform_video_id == "video-authenticated"
    assert len(requests) == 2


def test_resumable_insert_sends_only_private_provider_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer exact-token"
        assert request.content
        body = json.loads(request.content)
        assert body["status"]["privacyStatus"] == "private"
        assert set(body) == {"snippet", "status"}
        assert set(body["snippet"]) == {
            "title",
            "description",
            "tags",
            "categoryId",
            "defaultLanguage",
        }
        assert set(body["status"]) == {
            "privacyStatus",
            "selfDeclaredMadeForKids",
            "containsSyntheticMedia",
        }
        return httpx.Response(
            200,
            headers={"Location": "https://upload.youtube.test/session/private"},
        )

    transport = YouTubeDataApiTransport(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    session_uri = transport.create_resumable_session(
        access_token="exact-token",
        metadata={
            "snippet": {
                "title": "Frozen",
                "description": "Description",
                "tags": ["tag"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
            "privacy_status": "PRIVATE",
            "public_release_by_api": False,
            "publish_at": None,
        },
        total_bytes=10,
        mime_type="video/mp4",
    )
    assert session_uri.endswith("/private")
    assert len(requests) == 1


def test_verified_media_resolver_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.srt"
    outside.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
    checksum = hashlib.sha256(outside.read_bytes()).hexdigest()
    resolver = VerifiedMediaByteSourceResolver(workspace_root=root)
    with pytest.raises(ValidationFailureError, match="YOUTUBE_MEDIA_PATH_ESCAPE"):
        with resolver.resolve_sidecar(
            file_ref=f"file://{outside}",
            expected_checksum=checksum,
            mime_type="application/x-subrip",
        ):
            pass


def test_public_release_observation_must_match_frozen_expectation() -> None:
    expectation = {
        "expected_privacy_status": "PUBLIC",
        "manual_release_only": True,
        "title": "Frozen title",
        "description": "Frozen description",
        "tags": ["one", "two"],
        "category_id": "28",
        "default_language": "en",
        "made_for_kids": False,
        "contains_synthetic_media": True,
        "thumbnail_confirmed": True,
        "caption_confirmed": True,
    }
    observed = {
        "privacy_status": "PUBLIC",
        "title": "Frozen title",
        "description": "Frozen description",
        "tags": ["one", "two"],
        "category_id": "28",
        "default_language": "en",
        "made_for_kids": False,
        "contains_synthetic_media": True,
        "thumbnail_confirmed": True,
        "caption_confirmed": True,
    }
    _validate_public_release_observation(
        expectation=expectation,
        observed=observed,
        require_component_flags=True,
    )
    with pytest.raises(
        ValidationFailureError,
        match="PUBLICATION_RECEIPT_FROZEN_READBACK_MISMATCH",
    ):
        _validate_public_release_observation(
            expectation=expectation,
            observed={**observed, "tags": ["drifted"]},
            require_component_flags=True,
        )
    with pytest.raises(
        ValidationFailureError,
        match="PUBLICATION_RECEIPT_OBSERVATION_INCOMPLETE",
    ):
        _validate_public_release_observation(
            expectation=expectation,
            observed={key: value for key, value in observed.items() if key != "category_id"},
            require_component_flags=True,
        )


def test_public_observer_contract_does_not_require_exact_remote_thumbnail_bytes() -> None:
    expectation = {
        "expected_privacy_status": "PUBLIC",
        "manual_release_only": True,
        "title": "Frozen title",
        "description": "Frozen description",
        "tags": ["one"],
        "category_id": "28",
        "default_language": "en",
        "self_declared_made_for_kids": False,
        "contains_synthetic_media": True,
    }
    observed = {
        **expectation,
        "privacy_status": "PUBLIC",
        "thumbnail_assurance": "LOCAL_EFFECT_HASH_VERIFIED",
        "caption_assurance": "LOCAL_EFFECT_HASH_VERIFIED",
        "exact_remote_bytes_unavailable": True,
    }
    _validate_public_release_observation(
        expectation=expectation,
        observed=observed,
    )


def test_concurrent_upload_cannot_create_second_resumable_session(
    db_session: Session,
    tmp_path: Path,
) -> None:
    stage = _insert_stage(db_session, label="concurrent-upload")
    media_path = tmp_path / "concurrent.mp4"
    media_path.write_bytes(b"concurrent-private-video-bytes")
    media = ResolvedMediaBytes(
        path=media_path,
        checksum_sha256=hashlib.sha256(media_path.read_bytes()).hexdigest(),
        size_bytes=media_path.stat().st_size,
        mime_type="video/mp4",
    )
    transport = _BlockingUploadTransport()
    executor = _executor(db_session, transport=transport, tmp_path=tmp_path)
    snapshot = {
        "identity_hash": stage.identity_hash,
        "staging_metadata": stage.staging_metadata,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            executor._upload_video,
            stage_id=stage.id,
            stage_snapshot=snapshot,
            media=media,
            access_token="test-token",
        )
        assert transport.entered.wait(timeout=5)
        with pytest.raises(
            ValidationFailureError,
            match="YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN",
        ):
            executor._upload_video(
                stage_id=stage.id,
                stage_snapshot=snapshot,
                media=media,
                access_token="test-token",
            )
        transport.release.set()
        assert first.result(timeout=15) == "video-private-1"
    assert transport.create_calls == 1


def test_concurrent_component_write_is_fail_closed(
    db_session: Session,
    tmp_path: Path,
) -> None:
    stage = _insert_stage(db_session, label="concurrent-component")
    executor = _executor(
        db_session,
        transport=_SuccessfulUploadTransport(),
        tmp_path=tmp_path,
    )
    entered = Event()
    release = Event()
    calls: list[str] = []

    def call() -> dict[str, str]:
        calls.append("called")
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("blocking component transport timed out")
        return {"etag": "component-etag"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            executor._write_component_once,
            stage_id=stage.id,
            component_type="CAPTION",
            request_payload={"video_id": "video-1", "checksum": "d" * 64},
            call=call,
        )
        assert entered.wait(timeout=5)
        with pytest.raises(
            ValidationFailureError,
            match="YOUTUBE_COMPONENT_OUTCOME_UNKNOWN",
        ):
            executor._write_component_once(
                stage_id=stage.id,
                component_type="CAPTION",
                request_payload={"video_id": "video-1", "checksum": "d" * 64},
                call=lambda: {"etag": "must-not-run"},
            )
        release.set()
        first.result(timeout=15)
    assert calls == ["called"]


def test_still_private_is_durable_human_wait_without_failure_budget_exhaustion(
    db_session: Session,
) -> None:
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    stage = _insert_stage(db_session, label="durable-human-wait")
    event = _insert_public_observation_event(
        db_session, stage=stage, label="durable-human-wait", max_attempts=1
    )
    dispatcher = DurableOutboxDispatcher(
        db_session,
        backoff_base_seconds=5,
        backoff_cap_seconds=900,
        now=lambda: now[0],
    )

    for expected_wait_count in range(1, 5):
        claim = dispatcher.claim_next(worker_id="human-wait-worker")
        assert claim is not None
        disposition = dispatcher.defer_human_public_wait(
            event_id=event.id,
            worker_id="human-wait-worker",
        )
        db_session.commit()
        db_session.refresh(event)

        assert disposition.wait_count == expected_wait_count
        assert event.attempt_count == expected_wait_count
        assert event.delivered_at is None
        assert event.dead_lettered_at is None
        assert event.metadata_["waiting_semantic"] == "WAITING_FOR_HUMAN_PUBLIC"
        assert event.metadata_["human_wait_count"] == expected_wait_count
        assert event.next_attempt_at == now[0] + timedelta(seconds=900)
        now[0] = event.next_attempt_at + timedelta(seconds=1)


def test_not_yet_public_code_defers_instead_of_entering_delivery_failure_budget(
    db_session: Session,
) -> None:
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    stage = _insert_stage(db_session, label="not-yet-public-deferral")
    event = _insert_public_observation_event(
        db_session, stage=stage, label="not-yet-public-deferral", max_attempts=1
    )
    dispatcher = DurableOutboxDispatcher(db_session, now=lambda: now[0])
    claim = dispatcher.claim_next(worker_id="not-yet-public-worker")
    assert claim is not None

    disposition = dispatcher.record_failure(
        event_id=event.id,
        worker_id="not-yet-public-worker",
        error=ValidationFailureError("YOUTUBE_PUBLICATION_NOT_YET_PUBLIC"),
    )
    db_session.commit()
    db_session.refresh(event)

    assert isinstance(disposition, HumanWaitDisposition)
    assert disposition.wait_count == 1
    assert event.attempt_count == 1
    assert event.metadata_["human_wait_count"] == 1
    assert event.metadata_.get("technical_failure_count", 0) == 0
    assert event.last_error_code is None
    assert event.dead_lettered_at is None
    assert event.next_attempt_at > now[0]


def test_readback_failures_remain_bounded_after_human_wait(
    db_session: Session,
) -> None:
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    stage = _insert_stage(db_session, label="bounded-after-human-wait")
    event = _insert_public_observation_event(
        db_session, stage=stage, label="bounded-after-human-wait", max_attempts=2
    )
    dispatcher = DurableOutboxDispatcher(
        db_session,
        backoff_base_seconds=5,
        backoff_cap_seconds=900,
        now=lambda: now[0],
    )

    first_claim = dispatcher.claim_next(worker_id="bounded-worker")
    assert first_claim is not None
    dispatcher.defer_human_public_wait(
        event_id=event.id,
        worker_id="bounded-worker",
    )
    db_session.commit()
    db_session.refresh(event)
    now[0] = event.next_attempt_at + timedelta(seconds=1)

    for expected_failure_count in (1, 2):
        claim = dispatcher.claim_next(worker_id="bounded-worker")
        assert claim is not None
        disposition = dispatcher.record_failure(
            event_id=event.id,
            worker_id="bounded-worker",
            error=ValidationFailureError(
                "YOUTUBE_PUBLICATION_OBSERVATION_RECONCILIATION_REQUIRED"
            ),
        )
        db_session.commit()
        db_session.refresh(event)
        assert event.metadata_["technical_failure_count"] == expected_failure_count
        if expected_failure_count == 1:
            assert disposition.retry_scheduled is True
            assert disposition.dead_letter_job_id is None
            now[0] = event.next_attempt_at + timedelta(seconds=1)
        else:
            assert disposition.retry_scheduled is False
            assert disposition.dead_letter_job_id is not None
            assert event.dead_lettered_at is not None
