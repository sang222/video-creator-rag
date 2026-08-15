from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.youtube_delivery import ResumableUploadStatus
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.db.models.youtube_delivery import (
    YouTubeComponentAttempt,
    YouTubeComponentReceipt,
    YouTubePrivateStage,
    YouTubeUploadAttempt,
)
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.youtube_delivery import (
    LocalSessionSecretStore,
    ResolvedMediaBytes,
    YouTubeDataApiTransport,
    YouTubePrivateStageExecutor,
    _normalize_youtube_readback,
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
