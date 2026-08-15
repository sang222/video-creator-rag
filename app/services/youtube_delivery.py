"""Private YouTube staging, resumable upload, and publication boundaries.

The service intentionally refuses public-release API operations.  It may place
an exact approved long-form package on YouTube only with ``privacyStatus``
PRIVATE.  Public publication remains a human action observed later by the
canonical manual-publish verification service.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.youtube_delivery import (
    ProductionThumbnailBindingCreate,
    ResumableUploadStatus,
    TelegramSendResult,
    YouTubePrivateReadback,
    YouTubePrivateStagePrepare,
    YouTubePublishingCapability,
    YouTubePublishingCredentialCreate,
    YouTubeSeriesOrdinalBind,
)
from app.contracts.events import EventEnvelope
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.m10_2 import FinalMediaRef, ThumbnailVariant
from app.db.models.ai_visual import AIVisualAssetEffect
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m10_1 import HumanUploadTask
from app.db.models.m10_3 import YouTubeMonitoringCredential
from app.db.models.m7 import ManualPublishConfirmation
from app.db.models.foundation import DomainEvent
from app.db.models.ops import CredentialReference
from app.db.models.production_publish import FinalReviewCandidate, FinalVideoDecision
from app.db.models.vcos_v2 import SeriesPlan
from app.db.models.youtube_delivery import (
    LocalMediaPurgeAttempt,
    LocalMediaPurgeReceipt,
    ProductionThumbnailBinding,
    PublicPublicationReceipt,
    TelegramDeliveryNotification,
    YouTubeComponentAttempt,
    YouTubeComponentReceipt,
    YouTubePrivateStage,
    YouTubePublishingCredential,
    YouTubeSeriesEpisodeBinding,
    YouTubeSeriesPlaylistBinding,
    YouTubeUploadAttempt,
)
from app.db.models.voice_authority import CombinedReplacementBudgetAuthority
from app.services.config_registry import content_hash
from app.services.domain_events import DomainEventBus
from app.services.m10_3 import YouTubeOAuthCredentialService


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_FULL_SCOPE = "https://www.googleapis.com/auth/youtube"
_REQUIRED_PRIVATE_CAPABILITIES = frozenset(
    {
        YouTubePublishingCapability.VIDEO_UPLOAD_PRIVATE.value,
        YouTubePublishingCapability.THUMBNAIL_WRITE.value,
        YouTubePublishingCapability.CAPTION_WRITE.value,
        YouTubePublishingCapability.METADATA_READBACK.value,
        YouTubePublishingCapability.PROCESSING_READBACK.value,
    }
)
_COMPONENTS_REQUIRED_FOR_PRIVATE_VERIFICATION = frozenset(
    {"VIDEO_UPLOAD", "THUMBNAIL", "CAPTION", "METADATA_READBACK", "PROCESSING_READBACK"}
)
_UPLOAD_NAMESPACE = uuid.UUID("a35d9ad1-61d3-56a2-a2d5-8e69760e8eab")
_STAGE_NAMESPACE = uuid.UUID("cf0ef8d4-cd34-5cf0-bf95-edf9ce0dc7ac")
_PUBLICATION_NAMESPACE = uuid.UUID("f5efbcb0-26c9-5211-b0f3-cba38bc81547")
_DELIVERY_EVENT_NAMESPACE = uuid.UUID("a33d093f-9f24-5938-a75a-9379b391fc81")

YOUTUBE_PRIVATE_STAGE_EVENT_TYPE = "YOUTUBE_PRIVATE_STAGE_EXECUTION_REQUESTED"
TELEGRAM_DELIVERY_EVENT_TYPE = "TELEGRAM_DELIVERY_NOTIFICATION_REQUESTED"
LOCAL_MEDIA_PURGE_EVENT_TYPE = "LOCAL_MEDIA_PURGE_REQUESTED"
YOUTUBE_PRIVATE_STAGE_AGGREGATE_TYPE = "youtube_private_stage"
TELEGRAM_DELIVERY_AGGREGATE_TYPE = "telegram_delivery_notification"
LOCAL_MEDIA_PURGE_AGGREGATE_TYPE = "local_media_purge_attempt"
DELIVERY_EVENT_TYPES = frozenset(
    {
        YOUTUBE_PRIVATE_STAGE_EVENT_TYPE,
        TELEGRAM_DELIVERY_EVENT_TYPE,
        LOCAL_MEDIA_PURGE_EVENT_TYPE,
    }
)


def _hash(payload: Mapping[str, Any]) -> str:
    return content_hash(dict(payload))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_root() -> Path:
    configured = os.getenv("VCOS_V2_PRODUCTION_ROOT")
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[2] / "var" / "v2-production").resolve()


def _delivery_secret_root() -> Path:
    configured = os.getenv("VCOS_DELIVERY_SECRET_ROOT")
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[2] / "var" / "delivery-secrets").resolve()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _append_delivery_event_once(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    payload: Mapping[str, Any],
    max_attempts: int,
    causation_id: uuid.UUID | None = None,
) -> DomainEvent:
    if event_type not in DELIVERY_EVENT_TYPES or max_attempts < 1:
        raise ValidationFailureError("DELIVERY_EVENT_AUTHORITY_INVALID")
    canonical_payload = dict(payload)
    payload_hash = _hash(canonical_payload)
    event_id = uuid.uuid5(
        _DELIVERY_EVENT_NAMESPACE,
        f"{event_type}:{aggregate_id}:{payload_hash}",
    )
    command_id = f"delivery:{event_type.lower()}:{aggregate_id}:{payload_hash}"
    existing = session.get(DomainEvent, event_id)
    if existing is not None:
        if (
            existing.event_type != event_type
            or existing.aggregate_type != aggregate_type
            or existing.aggregate_id != aggregate_id
            or existing.payload != canonical_payload
            or existing.command_id != command_id
        ):
            raise ConflictError("DELIVERY_EVENT_IDENTITY_COLLISION")
        return existing
    event = DomainEventBus(session).append(
        EventEnvelope(
            event_id=event_id,
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=f"delivery:{aggregate_type}:{aggregate_id}",
            causation_id=causation_id,
            payload=canonical_payload,
            metadata={
                "queue": "production-workflow",
                "delivery_authority": True,
                "at_most_once": True,
            },
        ),
        company_id=company_id,
    )
    event.channel_workspace_id = channel_workspace_id
    event.command_id = command_id
    event.payload_hash = payload_hash
    event.max_attempts = max_attempts
    session.flush()
    return event


@dataclass(frozen=True, slots=True)
class ResolvedMediaBytes:
    path: Path
    checksum_sha256: str
    size_bytes: int
    mime_type: str
    temporary: bool = False


class LegacyDriveDownloader(Protocol):
    def download(self, *, cloud_ref: CloudMediaRef, destination: Path) -> None: ...


class SessionSecretStore(Protocol):
    def put(self, *, key: str, value: str) -> tuple[str, str]: ...

    def get(self, *, secret_ref: str, expected_hash: str) -> str: ...


class YouTubePrivateTransport(Protocol):
    def create_resumable_session(
        self,
        *,
        access_token: str,
        metadata: Mapping[str, Any],
        total_bytes: int,
        mime_type: str,
    ) -> str: ...

    def query_resumable_session(
        self, *, session_uri: str, total_bytes: int
    ) -> ResumableUploadStatus: ...

    def upload_media(
        self,
        *,
        session_uri: str,
        media_path: Path,
        start_offset: int,
        total_bytes: int,
        mime_type: str,
    ) -> ResumableUploadStatus: ...

    def set_thumbnail(
        self,
        *,
        access_token: str,
        platform_video_id: str,
        thumbnail_path: Path,
        mime_type: str,
    ) -> Mapping[str, Any]: ...

    def insert_caption(
        self,
        *,
        access_token: str,
        platform_video_id: str,
        caption_path: Path,
        language: str,
        name: str,
    ) -> Mapping[str, Any]: ...

    def readback_video(
        self, *, access_token: str, platform_video_id: str
    ) -> Mapping[str, Any]: ...


class YouTubePlaylistTransport(Protocol):
    def create_playlist(
        self,
        *,
        access_token: str,
        title: str,
        description: str,
        privacy_status: str,
    ) -> Mapping[str, Any]: ...

    def insert_playlist_item(
        self,
        *,
        access_token: str,
        playlist_id: str,
        video_id: str,
        position: int,
    ) -> Mapping[str, Any]: ...

    def read_playlist_items(
        self, *, access_token: str, playlist_id: str
    ) -> Sequence[Mapping[str, Any]]: ...


class LocalSessionSecretStore:
    """Stores resumable session URIs outside the database with mode 0600."""

    def __init__(self, root: Path | None = None):
        self.root = (root or _delivery_secret_root()).resolve()

    def put(self, *, key: str, value: str) -> tuple[str, str]:
        if not value.startswith("https://"):
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_URI_INVALID")
        self.root.mkdir(parents=True, exist_ok=True)
        path = (self.root / f"{key}.json").resolve()
        if not path.is_relative_to(self.root):
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        payload = json.dumps({"session_uri": value}, sort_keys=True)
        part = path.with_suffix(".part")
        part.write_text(payload, encoding="utf-8")
        os.chmod(part, 0o600)
        os.replace(part, path)
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"local_file://{path}", digest

    def get(self, *, secret_ref: str, expected_hash: str) -> str:
        if not secret_ref.startswith("local_file://") or not _is_sha256(expected_hash):
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        path = Path(secret_ref.removeprefix("local_file://")).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.is_symlink():
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        value = json.loads(path.read_text(encoding="utf-8")).get("session_uri")
        if (
            not isinstance(value, str)
            or hashlib.sha256(value.encode("utf-8")).hexdigest() != expected_hash
        ):
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_HASH_MISMATCH")
        return value


class VerifiedMediaByteSourceResolver:
    """Resolve exact local bytes or checksum-verified legacy Drive bytes."""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        legacy_drive_downloader: LegacyDriveDownloader | None = None,
    ):
        self.root = (workspace_root or _production_root()).resolve()
        self.legacy_drive_downloader = legacy_drive_downloader

    @contextmanager
    def resolve_final_media(
        self,
        *,
        final_media: FinalMediaRef,
        cloud_ref: CloudMediaRef | None,
        expected_checksum: str,
    ) -> Iterator[ResolvedMediaBytes]:
        if not _is_sha256(expected_checksum):
            raise ValidationFailureError("YOUTUBE_MEDIA_CHECKSUM_INVALID")
        file_ref = str(final_media.file_ref)
        if file_ref.startswith("vcos-local-archive://"):
            parts = file_ref.removeprefix("vcos-local-archive://").split("/")
            if len(parts) != 3 or parts[1] != expected_checksum or parts[2] != "final.mp4":
                raise ValidationFailureError("YOUTUBE_LOCAL_MEDIA_REF_INVALID")
            target = self.root / "archive" / parts[0] / f"{expected_checksum}.mp4"
            resolved = self._verified_local(
                target=target, expected_checksum=expected_checksum, mime_type="video/mp4"
            )
            yield resolved
            return
        if file_ref.startswith("file://") or Path(file_ref).is_absolute():
            raw = file_ref.removeprefix("file://")
            resolved = self._verified_local(
                target=Path(raw), expected_checksum=expected_checksum, mime_type="video/mp4"
            )
            yield resolved
            return
        if cloud_ref is None or self.legacy_drive_downloader is None:
            raise ValidationFailureError("YOUTUBE_MEDIA_BYTE_SOURCE_UNAVAILABLE")
        staging_root = self.root / "youtube-upload-staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        fd, value = tempfile.mkstemp(prefix="legacy-drive-", suffix=".mp4", dir=staging_root)
        os.close(fd)
        temp = Path(value)
        try:
            self.legacy_drive_downloader.download(cloud_ref=cloud_ref, destination=temp)
            if _sha256_file(temp) != expected_checksum:
                raise ValidationFailureError("YOUTUBE_DRIVE_DOWNLOAD_CHECKSUM_MISMATCH")
            yield ResolvedMediaBytes(
                path=temp,
                checksum_sha256=expected_checksum,
                size_bytes=temp.stat().st_size,
                mime_type="video/mp4",
                temporary=True,
            )
        finally:
            temp.unlink(missing_ok=True)

    @contextmanager
    def resolve_sidecar(
        self,
        *,
        file_ref: str,
        expected_checksum: str,
        mime_type: str,
    ) -> Iterator[ResolvedMediaBytes]:
        if not _is_sha256(expected_checksum):
            raise ValidationFailureError("YOUTUBE_SIDECAR_CHECKSUM_INVALID")
        raw = file_ref.removeprefix("file://")
        target = Path(raw)
        if not target.is_absolute():
            target = self.root / raw
        yield self._verified_local(
            target=target, expected_checksum=expected_checksum, mime_type=mime_type
        )

    def _verified_local(
        self, *, target: Path, expected_checksum: str, mime_type: str
    ) -> ResolvedMediaBytes:
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError as exc:
            raise NotFoundError("verified media bytes not found") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ValidationFailureError("YOUTUBE_MEDIA_PATH_REJECTED")
        if _sha256_file(resolved) != expected_checksum:
            raise ValidationFailureError("YOUTUBE_MEDIA_READBACK_CHECKSUM_MISMATCH")
        return ResolvedMediaBytes(
            path=resolved,
            checksum_sha256=expected_checksum,
            size_bytes=resolved.stat().st_size,
            mime_type=mime_type,
        )


class YouTubeDataApiTransport:
    """Minimal official YouTube Data API v3 private-staging transport."""

    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: int = 120):
        self.client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        if not access_token:
            raise ValidationFailureError("YOUTUBE_PUBLISHING_ACCESS_TOKEN_REQUIRED")
        return {"Authorization": f"Bearer {access_token}"}

    def create_resumable_session(
        self,
        *,
        access_token: str,
        metadata: Mapping[str, Any],
        total_bytes: int,
        mime_type: str,
    ) -> str:
        response = self.client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                **self._headers(access_token),
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(total_bytes),
                "X-Upload-Content-Type": mime_type,
            },
            json=dict(metadata),
        )
        response.raise_for_status()
        session_uri = response.headers.get("Location")
        if not session_uri:
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_MISSING")
        return session_uri

    def query_resumable_session(
        self, *, session_uri: str, total_bytes: int
    ) -> ResumableUploadStatus:
        response = self.client.put(
            session_uri,
            headers={"Content-Length": "0", "Content-Range": f"bytes */{total_bytes}"},
        )
        return self._upload_status(response=response, total_bytes=total_bytes)

    def upload_media(
        self,
        *,
        session_uri: str,
        media_path: Path,
        start_offset: int,
        total_bytes: int,
        mime_type: str,
    ) -> ResumableUploadStatus:
        remaining = total_bytes - start_offset
        if remaining <= 0:
            return self.query_resumable_session(
                session_uri=session_uri, total_bytes=total_bytes
            )
        with media_path.open("rb") as stream:
            stream.seek(start_offset)
            response = self.client.put(
                session_uri,
                headers={
                    "Content-Type": mime_type,
                    "Content-Length": str(remaining),
                    "Content-Range": f"bytes {start_offset}-{total_bytes - 1}/{total_bytes}",
                },
                content=stream,
            )
        return self._upload_status(response=response, total_bytes=total_bytes)

    @staticmethod
    def _upload_status(
        *, response: httpx.Response, total_bytes: int
    ) -> ResumableUploadStatus:
        if response.status_code == 308:
            range_header = response.headers.get("Range", "")
            match = re.fullmatch(r"bytes=0-(\d+)", range_header)
            committed = int(match.group(1)) + 1 if match else 0
            return ResumableUploadStatus(state="INCOMPLETE", committed_bytes=committed)
        if 200 <= response.status_code < 300:
            payload = response.json()
            return ResumableUploadStatus(
                state="COMPLETE",
                committed_bytes=total_bytes,
                platform_video_id=str(payload.get("id") or ""),
                response_payload=payload,
            )
        return ResumableUploadStatus(
            state="FAILED",
            committed_bytes=0,
            response_payload={"http_status": response.status_code},
        )

    def set_thumbnail(
        self,
        *,
        access_token: str,
        platform_video_id: str,
        thumbnail_path: Path,
        mime_type: str,
    ) -> Mapping[str, Any]:
        with thumbnail_path.open("rb") as stream:
            response = self.client.post(
                "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                params={"videoId": platform_video_id, "uploadType": "media"},
                headers={**self._headers(access_token), "Content-Type": mime_type},
                content=stream,
            )
        response.raise_for_status()
        return response.json()

    def insert_caption(
        self,
        *,
        access_token: str,
        platform_video_id: str,
        caption_path: Path,
        language: str,
        name: str,
    ) -> Mapping[str, Any]:
        metadata = {
            "snippet": {
                "videoId": platform_video_id,
                "language": language,
                "name": name,
                "isDraft": False,
            }
        }
        with caption_path.open("rb") as stream:
            response = self.client.post(
                "https://www.googleapis.com/upload/youtube/v3/captions",
                params={"part": "snippet", "uploadType": "multipart"},
                headers=self._headers(access_token),
                files={
                    "metadata": (None, json.dumps(metadata), "application/json"),
                    "media": (caption_path.name, stream, "application/octet-stream"),
                },
            )
        response.raise_for_status()
        return response.json()

    def readback_video(
        self, *, access_token: str, platform_video_id: str
    ) -> Mapping[str, Any]:
        response = self.client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,status,processingDetails",
                "id": platform_video_id,
            },
            headers=self._headers(access_token),
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if len(items) != 1:
            raise ValidationFailureError("YOUTUBE_PRIVATE_VIDEO_READBACK_MISSING")
        return dict(items[0])


class YouTubeCredentialResolver:
    """Uses the existing OAuth token store/refresh logic without merging roles."""

    def __init__(self, session: Session):
        self.session = session

    def access_token(self, credential: YouTubePublishingCredential) -> str:
        reference = self.session.get(CredentialReference, credential.credential_reference_id)
        reference_scopes = set(
            str(item) for item in (reference.scope_blob or {}).get("scopes", [])
        ) if reference is not None else set()
        if (
            reference is None
            or reference.status not in {"CONFIGURED", "ACTIVE"}
            or not set(credential.oauth_scopes).issubset(reference_scopes)
        ):
            raise ValidationFailureError("YOUTUBE_PUBLISHING_CREDENTIAL_REFERENCE_MISSING")
        token = YouTubeOAuthCredentialService(self.session).get_valid_access_token(reference)
        if not token:
            raise ValidationFailureError("YOUTUBE_PUBLISHING_CREDENTIAL_UNAVAILABLE")
        return token


class YouTubeDeliveryService:
    def __init__(self, session: Session):
        self.session = session

    def register_publishing_credential(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        data: YouTubePublishingCredentialCreate,
    ) -> YouTubePublishingCredential:
        reference = self.session.get(CredentialReference, data.credential_reference_id)
        monitoring = self.session.scalar(
            select(YouTubeMonitoringCredential).where(
                YouTubeMonitoringCredential.credential_reference_id
                == data.credential_reference_id
            )
        )
        frozen_reference_scopes = set(
            str(item) for item in ((reference.scope_blob or {}).get("scopes") or [])
        ) if reference is not None else set()
        if (
            reference is None
            or reference.status not in {"CONFIGURED", "ACTIVE"}
            or not reference.secret_ref
            or reference.provider_key not in {
                "youtube_analytics_api",
                "youtube_data_api",
                "youtube_publishing_api",
            }
            or monitoring is None
            or monitoring.company_id != company_id
            or monitoring.channel_workspace_id != channel_workspace_id
            or monitoring.connection_state != "CONNECTED"
        ):
            raise ValidationFailureError("YOUTUBE_PUBLISHING_CREDENTIAL_REFERENCE_INVALID")
        scopes = sorted(set(data.oauth_scopes))
        capabilities = sorted({item.value for item in data.capabilities})
        if not _REQUIRED_PRIVATE_CAPABILITIES.issubset(capabilities):
            raise ValidationFailureError("YOUTUBE_PRIVATE_STAGING_CAPABILITIES_INCOMPLETE")
        if YOUTUBE_UPLOAD_SCOPE not in scopes:
            raise ValidationFailureError("YOUTUBE_UPLOAD_SCOPE_REQUIRED")
        if not set(scopes).issubset(frozen_reference_scopes):
            raise ValidationFailureError("YOUTUBE_PUBLISHING_SCOPE_REFERENCE_DRIFT")
        if (
            YouTubePublishingCapability.CAPTION_WRITE.value in capabilities
            and not ({YOUTUBE_FORCE_SSL_SCOPE, YOUTUBE_FULL_SCOPE} & set(scopes))
        ):
            raise ValidationFailureError("YOUTUBE_FORCE_SSL_SCOPE_REQUIRED_FOR_CAPTIONS")
        payload = {
            "schema_version": "vcos.youtube-publishing-credential.v1",
            "company_id": str(company_id),
            "channel_workspace_id": str(channel_workspace_id),
            "credential_reference_id": str(reference.id),
            "platform_channel_id": data.platform_channel_id,
            "account_identity": data.account_identity,
            "oauth_scopes": scopes,
            "capabilities": capabilities,
            "public_release_allowed": False,
            "delete_allowed": False,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(YouTubePublishingCredential).where(
                YouTubePublishingCredential.channel_workspace_id == channel_workspace_id,
                YouTubePublishingCredential.platform_channel_id == data.platform_channel_id,
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise ConflictError("YOUTUBE_PUBLISHING_CREDENTIAL_IMMUTABLE_CONFLICT")
            return existing
        record = YouTubePublishingCredential(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            credential_reference_id=reference.id,
            platform_channel_id=data.platform_channel_id,
            account_identity=data.account_identity,
            oauth_scopes=scopes,
            capabilities=capabilities,
            public_release_allowed=False,
            delete_allowed=False,
            state="ACTIVE",
            content_hash=digest,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def bind_generated_thumbnail(
        self,
        *,
        candidate_id: uuid.UUID,
        data: ProductionThumbnailBindingCreate,
        allowed_root: Path | None = None,
    ) -> ProductionThumbnailBinding:
        candidate = self.session.get(FinalReviewCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"final review candidate not found: {candidate_id}")
        existing = self.session.scalar(
            select(ProductionThumbnailBinding).where(
                ProductionThumbnailBinding.final_review_candidate_id == candidate.id
            )
        )
        if existing is not None:
            if (
                existing.thumbnail_variant_id != data.thumbnail_variant_id
                or existing.provider_key != data.provider_key
                or existing.provider_effect_ref != data.provider_effect_ref
                or existing.provider_effect_hash != data.provider_effect_hash
                or existing.file_ref != data.file_ref
                or existing.checksum_sha256 != data.checksum_sha256
                or existing.mime_type != data.mime_type
                or existing.size_bytes != data.size_bytes
                or existing.width != data.width
                or existing.height != data.height
            ):
                raise ConflictError("PRODUCTION_THUMBNAIL_BINDING_IMMUTABLE_CONFLICT")
            return existing
        match = re.fullmatch(r"ai-visual-asset-effects/([0-9a-f-]{36})", data.provider_effect_ref)
        try:
            effect_id = uuid.UUID(match.group(1)) if match else None
        except ValueError as exc:
            raise ValidationFailureError(
                "PRODUCTION_THUMBNAIL_PROVIDER_EFFECT_INVALID"
            ) from exc
        effect = self.session.get(AIVisualAssetEffect, effect_id) if effect_id else None
        if (
            effect is None
            or effect.video_project_id != candidate.video_project_id
            or effect.state != "VERIFIED"
            or effect.route != "AI_IMAGE"
            or effect.provider_key != data.provider_key
            or effect.effect_identity_hash != data.provider_effect_hash
            or effect.output_ref != data.file_ref
            or effect.output_checksum != data.checksum_sha256
            or effect.output_size_bytes != data.size_bytes
            or effect.output_content_type != data.mime_type
            or effect.output_width != data.width
            or effect.output_height != data.height
        ):
            raise ValidationFailureError(
                "PRODUCTION_THUMBNAIL_PROVIDER_EFFECT_MISMATCH"
            )
        if data.thumbnail_variant_id is not None:
            variant = self.session.get(ThumbnailVariant, data.thumbnail_variant_id)
            if (
                variant is None
                or variant.video_project_id != candidate.video_project_id
                or variant.provider_key != data.provider_key
                or variant.output_ref != data.file_ref
                or variant.state not in {"VERIFIED", "COMPLETED"}
            ):
                raise ValidationFailureError("PRODUCTION_THUMBNAIL_VARIANT_MISMATCH")
        path = _resolve_local_ref(data.file_ref, root=allowed_root or _production_root())
        if _sha256_file(path) != data.checksum_sha256 or path.stat().st_size != data.size_bytes:
            raise ValidationFailureError("PRODUCTION_THUMBNAIL_BYTES_MISMATCH")
        observed_width, observed_height = _image_dimensions(path, data.mime_type)
        if (observed_width, observed_height) != (data.width, data.height):
            raise ValidationFailureError("PRODUCTION_THUMBNAIL_DIMENSION_MISMATCH")
        if data.width < 640 or data.height < 360 or data.width * 9 != data.height * 16:
            raise ValidationFailureError("PRODUCTION_THUMBNAIL_ASPECT_RATIO_INVALID")
        identity = {
            "schema_version": "vcos.production-thumbnail-binding.v1",
            "candidate_id": str(candidate.id),
            "candidate_hash": candidate.candidate_hash,
            "video_project_id": str(candidate.video_project_id),
            "thumbnail_variant_id": str(data.thumbnail_variant_id) if data.thumbnail_variant_id else None,
            "source_type": "AI_GENERATED",
            "provider_key": data.provider_key,
            "provider_effect_ref": data.provider_effect_ref,
            "provider_effect_hash": data.provider_effect_hash,
            "file_ref": data.file_ref,
            "checksum_sha256": data.checksum_sha256,
            "mime_type": data.mime_type,
            "size_bytes": data.size_bytes,
            "width": data.width,
            "height": data.height,
            "state": "VERIFIED",
        }
        record = ProductionThumbnailBinding(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            final_review_candidate_id=candidate.id,
            thumbnail_variant_id=data.thumbnail_variant_id,
            source_type="AI_GENERATED",
            provider_key=data.provider_key,
            provider_effect_ref=data.provider_effect_ref,
            provider_effect_hash=data.provider_effect_hash,
            file_ref=data.file_ref,
            checksum_sha256=data.checksum_sha256,
            mime_type=data.mime_type,
            size_bytes=data.size_bytes,
            width=data.width,
            height=data.height,
            state="VERIFIED",
            content_hash=_hash(identity),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def prepare_private_stage(
        self,
        *,
        decision_id: uuid.UUID,
        data: YouTubePrivateStagePrepare,
    ) -> YouTubePrivateStage:
        decision = self.session.get(FinalVideoDecision, decision_id)
        if decision is None:
            raise NotFoundError(f"final video decision not found: {decision_id}")
        if decision.decision != "UPLOAD":
            raise ValidationFailureError("YOUTUBE_PRIVATE_STAGE_REQUIRES_UPLOAD_DECISION")
        existing = self.session.scalar(
            select(YouTubePrivateStage).where(
                YouTubePrivateStage.final_video_decision_id == decision.id
            )
        )
        candidate = self.session.get(FinalReviewCandidate, decision.final_review_candidate_id)
        final_media = self.session.get(FinalMediaRef, decision.final_media_ref_id)
        credential = self.session.get(
            YouTubePublishingCredential, data.publishing_credential_id
        )
        thumbnail = self.session.get(
            ProductionThumbnailBinding, data.production_thumbnail_binding_id
        )
        if (
            candidate is None
            or final_media is None
            or credential is None
            or thumbnail is None
            or credential.state != "ACTIVE"
            or credential.company_id != candidate.company_id
            or credential.channel_workspace_id != candidate.channel_workspace_id
            or credential.platform_channel_id != candidate.destination_platform_channel_id
            or credential.account_identity != candidate.destination_account_identity
            or thumbnail.final_review_candidate_id != candidate.id
            or thumbnail.state != "VERIFIED"
            or final_media.id != candidate.final_media_ref_id
            or final_media.checksum_sha256 != candidate.final_media_hash
        ):
            raise ValidationFailureError("YOUTUBE_PRIVATE_STAGE_LINEAGE_MISMATCH")
        title = str(candidate.publish_metadata_snapshot.get("title") or "").strip()
        description = str(candidate.publish_metadata_snapshot.get("description") or "")
        if not title:
            raise ValidationFailureError("YOUTUBE_STAGING_TITLE_REQUIRED")
        frozen_metadata = dict(candidate.publish_metadata_snapshot or {})
        if frozen_metadata.get("delivery_mode") == "YOUTUBE_PRIVATE_STAGE":
            frozen_caption = frozen_metadata.get("caption_sidecar")
            frozen_tags = [str(item) for item in list(frozen_metadata.get("tags") or [])]
            frozen_category = str(frozen_metadata.get("category_id") or "")
            frozen_language = str(frozen_metadata.get("default_language") or "")
            if (
                not isinstance(frozen_caption, Mapping)
                or data.caption_ref != frozen_caption.get("caption_local_file_ref")
                or data.caption_hash != frozen_caption.get("caption_checksum_sha256")
                or list(data.tags) != frozen_tags
                or data.category_id != frozen_category
                or (data.default_language or "") != frozen_language
                or data.made_for_kids
                is not bool(frozen_metadata.get("made_for_kids"))
                or data.contains_synthetic_media
                is not bool(frozen_metadata.get("contains_synthetic_media"))
            ):
                raise ValidationFailureError(
                    "YOUTUBE_PRIVATE_STAGE_FROZEN_METADATA_DRIFT"
                )
        language = data.default_language or str(
            candidate.target_market_lineage.get("content_language") or "en"
        )
        staging_metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": list(data.tags),
                "categoryId": data.category_id,
                "defaultLanguage": language,
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": data.made_for_kids,
                "containsSyntheticMedia": data.contains_synthetic_media,
            },
            "privacy_status": "PRIVATE",
            "public_release_by_api": False,
            "publish_at": None,
        }
        public_expectation = {
            "expected_privacy_status": "PUBLIC",
            "manual_release_only": True,
            "title": title,
            "description": description,
            "tags": list(data.tags),
            "made_for_kids": data.made_for_kids,
            "contains_synthetic_media": data.contains_synthetic_media,
            "platform_channel_id": credential.platform_channel_id,
        }
        identity = {
            "schema_version": "vcos.youtube-private-stage.v1",
            "decision_id": str(decision.id),
            "decision_hash": decision.decision_hash,
            "candidate_id": str(candidate.id),
            "candidate_hash": candidate.candidate_hash,
            "final_media_ref_id": str(final_media.id),
            "final_media_checksum": candidate.final_media_hash,
            "publishing_credential_id": str(credential.id),
            "publishing_credential_hash": credential.content_hash,
            "thumbnail_binding_id": str(thumbnail.id),
            "thumbnail_binding_hash": thumbnail.content_hash,
            "caption_ref": data.caption_ref,
            "caption_hash": data.caption_hash,
            "staging_metadata_hash": _hash(staging_metadata),
            "public_release_expectation_hash": _hash(public_expectation),
        }
        digest = _hash(identity)
        if existing is not None:
            if existing.identity_hash != digest:
                raise ConflictError("YOUTUBE_PRIVATE_STAGE_IMMUTABLE_CONFLICT")
            _append_delivery_event_once(
                self.session,
                event_type=YOUTUBE_PRIVATE_STAGE_EVENT_TYPE,
                aggregate_type=YOUTUBE_PRIVATE_STAGE_AGGREGATE_TYPE,
                aggregate_id=existing.id,
                company_id=existing.company_id,
                channel_workspace_id=existing.channel_workspace_id,
                payload={
                    "youtube_private_stage_id": str(existing.id),
                    "stage_identity_hash": existing.identity_hash,
                },
                max_attempts=12,
                causation_id=decision.id,
            )
            self._bind_release_task_to_stage(
                candidate=candidate,
                decision=decision,
                stage=existing,
            )
            return existing
        stage_id = uuid.uuid5(_STAGE_NAMESPACE, digest)
        record = YouTubePrivateStage(
            id=stage_id,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            final_review_candidate_id=candidate.id,
            final_video_decision_id=decision.id,
            final_media_ref_id=final_media.id,
            final_media_ref=final_media.file_ref,
            final_media_checksum=candidate.final_media_hash,
            publishing_credential_id=credential.id,
            production_thumbnail_binding_id=thumbnail.id,
            caption_ref=data.caption_ref,
            caption_hash=data.caption_hash,
            staging_metadata=staging_metadata,
            staging_metadata_hash=_hash(staging_metadata),
            public_release_expectation=public_expectation,
            public_release_expectation_hash=_hash(public_expectation),
            state="PREPARED",
            identity_hash=digest,
        )
        self.session.add(record)
        self.session.flush()
        _append_delivery_event_once(
            self.session,
            event_type=YOUTUBE_PRIVATE_STAGE_EVENT_TYPE,
            aggregate_type=YOUTUBE_PRIVATE_STAGE_AGGREGATE_TYPE,
            aggregate_id=record.id,
            company_id=record.company_id,
            channel_workspace_id=record.channel_workspace_id,
            payload={
                "youtube_private_stage_id": str(record.id),
                "stage_identity_hash": record.identity_hash,
            },
            max_attempts=12,
            causation_id=decision.id,
        )
        self._bind_release_task_to_stage(
            candidate=candidate,
            decision=decision,
            stage=record,
        )
        return record

    def prepare_private_stage_from_current_authority(
        self,
        *,
        decision_id: uuid.UUID,
    ) -> YouTubePrivateStage:
        """Resolve exact frozen candidate delivery authorities without guessing.

        This convenience path is intentionally strict.  Historical candidates
        that do not carry the complete frozen metadata contract must use the
        explicit ``prepare_private_stage`` command instead of server defaults.
        """

        decision = self.session.get(FinalVideoDecision, decision_id)
        if decision is None:
            raise NotFoundError(f"final video decision not found: {decision_id}")
        candidate = self.session.get(
            FinalReviewCandidate, decision.final_review_candidate_id
        )
        if candidate is None:
            raise ValidationFailureError("YOUTUBE_PRIVATE_STAGE_CANDIDATE_REQUIRED")
        credentials = list(
            self.session.scalars(
                select(YouTubePublishingCredential).where(
                    YouTubePublishingCredential.company_id == candidate.company_id,
                    YouTubePublishingCredential.channel_workspace_id
                    == candidate.channel_workspace_id,
                    YouTubePublishingCredential.platform_channel_id
                    == candidate.destination_platform_channel_id,
                    YouTubePublishingCredential.account_identity
                    == candidate.destination_account_identity,
                    YouTubePublishingCredential.state == "ACTIVE",
                )
            ).all()
        )
        thumbnails = list(
            self.session.scalars(
                select(ProductionThumbnailBinding).where(
                    ProductionThumbnailBinding.final_review_candidate_id
                    == candidate.id,
                    ProductionThumbnailBinding.state == "VERIFIED",
                )
            ).all()
        )
        if len(credentials) != 1 or len(thumbnails) != 1:
            raise ValidationFailureError(
                "YOUTUBE_PRIVATE_STAGE_CURRENT_AUTHORITY_INCOMPLETE"
            )
        metadata = dict(candidate.publish_metadata_snapshot or {})
        caption = metadata.get("caption_sidecar")
        required = {
            "tags": metadata.get("tags"),
            "category_id": metadata.get("category_id"),
            "default_language": metadata.get("default_language"),
            "made_for_kids": metadata.get("made_for_kids"),
            "contains_synthetic_media": metadata.get(
                "contains_synthetic_media"
            ),
        }
        if (
            metadata.get("delivery_mode") != "YOUTUBE_PRIVATE_STAGE"
            or not isinstance(caption, Mapping)
            or not isinstance(caption.get("caption_local_file_ref"), str)
            or not _is_sha256(caption.get("caption_checksum_sha256"))
            or not isinstance(required["tags"], list)
            or not isinstance(required["category_id"], str)
            or not required["category_id"]
            or not isinstance(required["default_language"], str)
            or not required["default_language"]
            or not isinstance(required["made_for_kids"], bool)
            or not isinstance(required["contains_synthetic_media"], bool)
        ):
            raise ValidationFailureError(
                "YOUTUBE_PRIVATE_STAGE_FROZEN_METADATA_INCOMPLETE"
            )
        return self.prepare_private_stage(
            decision_id=decision.id,
            data=YouTubePrivateStagePrepare(
                publishing_credential_id=credentials[0].id,
                production_thumbnail_binding_id=thumbnails[0].id,
                caption_ref=caption["caption_local_file_ref"],
                caption_hash=caption["caption_checksum_sha256"],
                tags=[str(item) for item in required["tags"]],
                category_id=required["category_id"],
                default_language=required["default_language"],
                made_for_kids=required["made_for_kids"],
                contains_synthetic_media=required[
                    "contains_synthetic_media"
                ],
            ),
        )

    def _bind_release_task_to_stage(
        self,
        *,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        stage: YouTubePrivateStage,
    ) -> None:
        task = self.session.scalar(
            select(HumanUploadTask).where(
                HumanUploadTask.final_video_decision_id == decision.id
            )
        )
        if task is None:
            return
        if task.final_review_candidate_id != candidate.id:
            raise ValidationFailureError("YOUTUBE_RELEASE_TASK_LINEAGE_MISMATCH")
        task.operator_note = (
            "VCOS private staging authority: "
            f"youtube-private-stage://{stage.id}. Public release remains human-only."
        )
        task.checklist = [
            {
                "key": "YOUTUBE_PRIVATE_STAGE",
                "required": True,
                "state": stage.state,
                "youtube_private_stage_id": str(stage.id),
            },
            {
                "key": "HUMAN_PUBLIC_RELEASE",
                "required": True,
                "auto_publish": False,
            },
            {
                "key": "PUBLICATION_CONFIRMATION",
                "required": True,
            },
        ]
        if task.task_state == "READY_FOR_OPERATOR":
            task.task_state = "IN_PROGRESS"
        self.session.flush()

    def require_stage(self, stage_id: uuid.UUID) -> YouTubePrivateStage:
        stage = self.session.get(YouTubePrivateStage, stage_id)
        if stage is None:
            raise NotFoundError(f"youtube private stage not found: {stage_id}")
        return stage

    def create_publication_receipt(
        self,
        *,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        confirmation: ManualPublishConfirmation,
        observed_metadata: Mapping[str, Any],
        observed_platform_channel_id: str,
        observed_platform_video_id: str,
        observed_video_url: str,
        observed_published_at: datetime,
        verification_evidence_ref: str,
        verification_evidence_hash: str,
    ) -> PublicPublicationReceipt:
        privacy = str(observed_metadata.get("privacy_status") or "").upper()
        if privacy != "PUBLIC":
            raise ValidationFailureError("PUBLICATION_RECEIPT_REQUIRES_PUBLIC_VISIBILITY")
        stage = self.session.scalar(
            select(YouTubePrivateStage).where(
                YouTubePrivateStage.final_video_decision_id == decision.id
            )
        )
        if stage is not None and (
            stage.state != "PRIVATE_VERIFIED"
            or stage.platform_video_id != observed_platform_video_id
            or stage.public_release_expectation.get("platform_channel_id")
            != observed_platform_channel_id
        ):
            raise ValidationFailureError("PUBLICATION_RECEIPT_STAGE_LINEAGE_MISMATCH")
        payload = {
            "schema_version": "vcos.public-publication-receipt.v1",
            "candidate_id": str(candidate.id),
            "candidate_hash": candidate.candidate_hash,
            "decision_id": str(decision.id),
            "decision_hash": decision.decision_hash,
            "confirmation_id": str(confirmation.id),
            "youtube_private_stage_id": str(stage.id) if stage else None,
            "platform_channel_id": observed_platform_channel_id,
            "platform_video_id": observed_platform_video_id,
            "public_url": observed_video_url,
            "observed_privacy_status": "PUBLIC",
            "observed_published_at": observed_published_at.isoformat(),
            "observed_metadata_hash": _hash(dict(observed_metadata)),
            "verification_evidence_ref": verification_evidence_ref,
            "verification_evidence_hash": verification_evidence_hash,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(PublicPublicationReceipt).where(
                PublicPublicationReceipt.final_video_decision_id == decision.id
            )
        )
        if existing is not None:
            if existing.receipt_hash != digest:
                raise ConflictError("PUBLICATION_RECEIPT_IMMUTABLE_CONFLICT")
            return existing
        receipt = PublicPublicationReceipt(
            id=uuid.uuid5(_PUBLICATION_NAMESPACE, digest),
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            final_review_candidate_id=candidate.id,
            final_video_decision_id=decision.id,
            manual_publish_confirmation_id=confirmation.id,
            youtube_private_stage_id=stage.id if stage else None,
            platform_channel_id=observed_platform_channel_id,
            platform_video_id=observed_platform_video_id,
            public_url=observed_video_url,
            observed_privacy_status="PUBLIC",
            observed_published_at=observed_published_at,
            observed_metadata=dict(observed_metadata),
            observed_metadata_hash=_hash(dict(observed_metadata)),
            verification_evidence_ref=verification_evidence_ref,
            verification_evidence_hash=verification_evidence_hash,
            receipt_hash=digest,
        )
        self.session.add(receipt)
        self.session.flush()
        if stage is not None:
            binding = self.session.scalar(
                select(YouTubeSeriesEpisodeBinding).where(
                    YouTubeSeriesEpisodeBinding.youtube_private_stage_id == stage.id
                )
            )
            if binding is not None:
                binding.public_publication_receipt_id = receipt.id
                binding.state = "PUBLICATION_VERIFIED"
                binding.binding_hash = _hash(
                    {
                        "prior_binding_hash": binding.binding_hash,
                        "public_publication_receipt_id": str(receipt.id),
                        "state": binding.state,
                    }
                )
        return receipt

    def create_series_playlist_binding(
        self,
        *,
        series_plan_id: uuid.UUID,
        publishing_credential_id: uuid.UUID,
        expected_title: str,
        expected_description: str,
    ) -> YouTubeSeriesPlaylistBinding:
        plan = self.session.get(SeriesPlan, series_plan_id)
        credential = self.session.get(
            YouTubePublishingCredential, publishing_credential_id
        )
        if plan is None or credential is None or credential.state != "ACTIVE":
            raise ValidationFailureError("YOUTUBE_SERIES_PLAYLIST_AUTHORITY_INVALID")
        if YouTubePublishingCapability.PLAYLIST_WRITE.value not in credential.capabilities:
            raise ValidationFailureError("YOUTUBE_PLAYLIST_WRITE_CAPABILITY_REQUIRED")
        existing = self.session.scalar(
            select(YouTubeSeriesPlaylistBinding).where(
                YouTubeSeriesPlaylistBinding.series_plan_id == plan.id
            )
        )
        payload = {
            "schema_version": "vcos.youtube-series-playlist-binding.v1",
            "series_plan_id": str(plan.id),
            "publishing_credential_id": str(credential.id),
            "platform_channel_id": credential.platform_channel_id,
            "expected_metadata": {
                "title": expected_title,
                "description": expected_description,
                "privacy_status": "PRIVATE",
            },
            "state": "NOT_CREATED",
        }
        digest = _hash(payload)
        if existing is not None:
            if existing.binding_hash != digest:
                raise ConflictError("YOUTUBE_SERIES_PLAYLIST_BINDING_CONFLICT")
            return existing
        binding = YouTubeSeriesPlaylistBinding(
            company_id=credential.company_id,
            channel_workspace_id=credential.channel_workspace_id,
            series_plan_id=plan.id,
            publishing_credential_id=credential.id,
            platform_channel_id=credential.platform_channel_id,
            state="NOT_CREATED",
            expected_metadata=payload["expected_metadata"],
            binding_hash=digest,
        )
        self.session.add(binding)
        self.session.flush()
        return binding

    def bind_public_episode_ordinal(
        self,
        *,
        episode_binding_id: uuid.UUID,
        data: YouTubeSeriesOrdinalBind,
    ) -> YouTubeSeriesEpisodeBinding:
        binding = self.session.get(YouTubeSeriesEpisodeBinding, episode_binding_id)
        if binding is None:
            raise NotFoundError(f"series episode binding not found: {episode_binding_id}")
        if binding.public_episode_ordinal is not None:
            if (
                binding.public_episode_ordinal != data.public_episode_ordinal
                or binding.public_ordinal_authority_hash
                != data.public_ordinal_authority_hash
            ):
                raise ConflictError("PUBLIC_EPISODE_ORDINAL_IMMUTABLE")
            return binding
        binding.public_episode_ordinal = data.public_episode_ordinal
        binding.public_ordinal_authority_ref = data.public_ordinal_authority_ref
        binding.public_ordinal_authority_hash = data.public_ordinal_authority_hash
        binding.expected_position = data.public_episode_ordinal - 1
        binding.state = "PLAYLIST_BIND_PENDING"
        binding.binding_hash = _hash(
            {
                "video_project_id": str(binding.video_project_id),
                "youtube_video_id": binding.youtube_video_id,
                "public_episode_ordinal": data.public_episode_ordinal,
                "public_ordinal_authority_ref": data.public_ordinal_authority_ref,
                "public_ordinal_authority_hash": data.public_ordinal_authority_hash,
            }
        )
        self.session.flush()
        return binding

    def _ensure_series_private_binding(self, stage: YouTubePrivateStage) -> None:
        candidate = self.session.get(FinalReviewCandidate, stage.final_review_candidate_id)
        if candidate is None or candidate.content_mode != "SERIES_EPISODE":
            return
        playlist = self.session.scalar(
            select(YouTubeSeriesPlaylistBinding).where(
                YouTubeSeriesPlaylistBinding.series_plan_id == candidate.series_plan_id
            )
        )
        if playlist is None:
            return
        existing = self.session.scalar(
            select(YouTubeSeriesEpisodeBinding).where(
                YouTubeSeriesEpisodeBinding.youtube_private_stage_id == stage.id
            )
        )
        if existing is not None:
            return
        payload = {
            "schema_version": "vcos.youtube-series-episode-binding.v1",
            "playlist_binding_id": str(playlist.id),
            "series_plan_id": str(candidate.series_plan_id),
            "series_run_id": str(candidate.series_run_id),
            "technical_episode_number": candidate.episode_number,
            "video_project_id": str(candidate.video_project_id),
            "youtube_private_stage_id": str(stage.id),
            "state": "PRIVATE_UPLOADED",
            "public_episode_ordinal": None,
        }
        binding = YouTubeSeriesEpisodeBinding(
            youtube_series_playlist_binding_id=playlist.id,
            series_plan_id=candidate.series_plan_id,
            series_run_id=candidate.series_run_id,
            technical_episode_number=candidate.episode_number,
            video_project_id=candidate.video_project_id,
            youtube_private_stage_id=stage.id,
            youtube_video_id=stage.platform_video_id or "PENDING_PRIVATE_UPLOAD",
            state="PRIVATE_UPLOADED",
            binding_hash=_hash(payload),
        )
        self.session.add(binding)


class YouTubePrivateStageExecutor:
    """Crash-safe private upload executor with one resumable insert effect."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        transport: YouTubePrivateTransport | None = None,
        secret_store: SessionSecretStore | None = None,
        media_resolver: VerifiedMediaByteSourceResolver | None = None,
    ):
        self.session_factory = session_factory
        self.transport = transport or YouTubeDataApiTransport()
        self.secret_store = secret_store or LocalSessionSecretStore()
        self.media_resolver = media_resolver or VerifiedMediaByteSourceResolver()

    def execute(self, *, stage_id: uuid.UUID) -> YouTubePrivateStage:
        with self.session_factory() as session:
            stage, candidate, final_media, cloud, credential, thumbnail, access_token = (
                self._load_scope(session, stage_id)
            )
            if stage.state == "PRIVATE_VERIFIED":
                return stage
            caption_ref = stage.caption_ref
            caption_hash = stage.caption_hash
            stage_snapshot = {
                "id": stage.id,
                "identity_hash": stage.identity_hash,
                "staging_metadata": dict(stage.staging_metadata),
                "final_media_checksum": stage.final_media_checksum,
                "publishing_credential_hash": credential.content_hash,
            }
        with self.media_resolver.resolve_final_media(
            final_media=final_media,
            cloud_ref=cloud,
            expected_checksum=stage_snapshot["final_media_checksum"],
        ) as media, self.media_resolver.resolve_sidecar(
            file_ref=thumbnail.file_ref,
            expected_checksum=thumbnail.checksum_sha256,
            mime_type=thumbnail.mime_type,
        ) as thumbnail_bytes, self.media_resolver.resolve_sidecar(
            file_ref=caption_ref,
            expected_checksum=caption_hash,
            mime_type="application/x-subrip",
        ) as caption_bytes:
            platform_video_id = self._upload_video(
                stage_id=stage_id,
                stage_snapshot=stage_snapshot,
                media=media,
                access_token=access_token,
            )
            self._write_component_once(
                stage_id=stage_id,
                component_type="THUMBNAIL",
                request_payload={
                    "platform_video_id": platform_video_id,
                    "checksum": thumbnail_bytes.checksum_sha256,
                },
                call=lambda: self.transport.set_thumbnail(
                    access_token=access_token,
                    platform_video_id=platform_video_id,
                    thumbnail_path=thumbnail_bytes.path,
                    mime_type=thumbnail_bytes.mime_type,
                ),
            )
            language = str(
                stage_snapshot["staging_metadata"].get("snippet", {}).get(
                    "defaultLanguage", "en"
                )
            )
            self._write_component_once(
                stage_id=stage_id,
                component_type="CAPTION",
                request_payload={
                    "platform_video_id": platform_video_id,
                    "checksum": caption_bytes.checksum_sha256,
                    "language": language,
                },
                call=lambda: self.transport.insert_caption(
                    access_token=access_token,
                    platform_video_id=platform_video_id,
                    caption_path=caption_bytes.path,
                    language=language,
                    name="VCOS canonical captions",
                ),
            )
            observed = dict(
                self.transport.readback_video(
                    access_token=access_token, platform_video_id=platform_video_id
                )
            )
            processing_status = str(
                (observed.get("processingDetails") or {}).get("processingStatus")
                or ""
            ).upper()
            if processing_status != "SUCCEEDED":
                with self.session_factory() as session:
                    stage = session.get(YouTubePrivateStage, stage_id)
                    if stage is None:
                        raise NotFoundError(
                            f"youtube private stage not found: {stage_id}"
                        )
                    stage.state = "PROCESSING"
                    stage.processing_status = processing_status or "PENDING"
                    stage.last_error_code = "YOUTUBE_PROCESSING_PENDING"
                    session.commit()
                raise ValidationFailureError("YOUTUBE_PROCESSING_PENDING")
            observed["vcosThumbnailVerified"] = True
            observed["vcosCaptionVerified"] = True
            observed["vcosEvidenceRef"] = "youtube-readback://videos.list+component-receipts"
            readback = _normalize_youtube_readback(observed)
            self._finalize_private_readback(
                stage_id=stage_id,
                readback=readback,
                raw_observed=observed,
            )
        with self.session_factory() as session:
            return YouTubeDeliveryService(session).require_stage(stage_id)

    def _load_scope(self, session: Session, stage_id: uuid.UUID):
        stage = session.get(YouTubePrivateStage, stage_id)
        if stage is None:
            raise NotFoundError(f"youtube private stage not found: {stage_id}")
        candidate = session.get(FinalReviewCandidate, stage.final_review_candidate_id)
        final_media = session.get(FinalMediaRef, stage.final_media_ref_id)
        credential = session.get(
            YouTubePublishingCredential, stage.publishing_credential_id
        )
        thumbnail = session.get(
            ProductionThumbnailBinding, stage.production_thumbnail_binding_id
        )
        cloud = (
            session.get(CloudMediaRef, final_media.cloud_media_ref_id)
            if final_media is not None and final_media.cloud_media_ref_id
            else None
        )
        if (
            candidate is None
            or final_media is None
            or credential is None
            or thumbnail is None
            or credential.state != "ACTIVE"
            or not _REQUIRED_PRIVATE_CAPABILITIES.issubset(set(credential.capabilities))
            or final_media.checksum_sha256 != stage.final_media_checksum
            or candidate.final_media_hash != stage.final_media_checksum
            or thumbnail.final_review_candidate_id != candidate.id
        ):
            raise ValidationFailureError("YOUTUBE_PRIVATE_STAGE_EXECUTION_SCOPE_INVALID")
        token = YouTubeCredentialResolver(session).access_token(credential)
        return stage, candidate, final_media, cloud, credential, thumbnail, token

    def _upload_video(
        self,
        *,
        stage_id: uuid.UUID,
        stage_snapshot: Mapping[str, Any],
        media: ResolvedMediaBytes,
        access_token: str,
    ) -> str:
        with self.session_factory() as session:
            attempt = session.scalar(
                select(YouTubeUploadAttempt)
                .where(YouTubeUploadAttempt.youtube_private_stage_id == stage_id)
                .with_for_update()
            )
            request_payload = {
                "stage_id": str(stage_id),
                "stage_identity_hash": stage_snapshot["identity_hash"],
                "media_checksum": media.checksum_sha256,
                "media_size": media.size_bytes,
                "metadata_hash": _hash(stage_snapshot["staging_metadata"]),
            }
            request_hash = _hash(request_payload)
            effect_key = f"youtube-private-upload:{uuid.uuid5(_UPLOAD_NAMESPACE, request_hash)}"
            if attempt is None:
                attempt = YouTubeUploadAttempt(
                    youtube_private_stage_id=stage_id,
                    attempt_number=1,
                    provider_effect_key=effect_key,
                    request_hash=request_hash,
                    total_bytes=media.size_bytes,
                    committed_bytes=0,
                    state="INTENDED",
                    outcome_certainty="NOT_SUBMITTED",
                )
                session.add(attempt)
                session.commit()
            elif (
                attempt.request_hash != request_hash
                or attempt.total_bytes != media.size_bytes
                or attempt.provider_effect_key != effect_key
            ):
                raise ConflictError("YOUTUBE_UPLOAD_ATTEMPT_IDENTITY_MISMATCH")
            attempt_id = attempt.id
        with self.session_factory() as session:
            attempt = session.get(YouTubeUploadAttempt, attempt_id)
            stage = session.get(YouTubePrivateStage, stage_id)
            if attempt.state == "VERIFIED":
                return str(attempt.provider_video_id)
            if attempt.state == "INTENDED":
                # Seal the one allowed ``videos.insert`` intent before the
                # provider receives it. A crash after this commit is
                # deliberately ambiguous and may not create another session.
                attempt.state = "SESSION_SUBMITTED"
                attempt.outcome_certainty = "UNCERTAIN"
                stage.state = "UPLOADING"
                session.commit()
                try:
                    session_uri = self.transport.create_resumable_session(
                        access_token=access_token,
                        metadata=stage_snapshot["staging_metadata"],
                        total_bytes=media.size_bytes,
                        mime_type=media.mime_type,
                    )
                    secret_ref, uri_hash = self.secret_store.put(
                        key=str(attempt.id), value=session_uri
                    )
                except Exception as exc:
                    attempt.state = "OUTCOME_UNKNOWN"
                    attempt.outcome_certainty = "UNCERTAIN"
                    attempt.error_code = "YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN"
                    stage.state = "OUTCOME_UNKNOWN"
                    stage.last_error_code = attempt.error_code
                    session.commit()
                    raise ValidationFailureError(attempt.error_code) from exc
                attempt.session_secret_ref = secret_ref
                attempt.session_uri_hash = uri_hash
                attempt.state = "SESSION_CREATED"
                attempt.outcome_certainty = "CERTAIN_PENDING"
                stage.state = "SESSION_CREATED"
                session.commit()
            elif attempt.state == "SESSION_SUBMITTED" or (
                attempt.state == "OUTCOME_UNKNOWN"
                and not attempt.session_secret_ref
            ):
                raise ValidationFailureError(
                    "YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN"
                )
            attempt.state = "UPLOADING"
            attempt.outcome_certainty = "CERTAIN_PENDING"
            stage.state = "UPLOADING"
            session.commit()
            session_uri = self.secret_store.get(
                secret_ref=str(attempt.session_secret_ref),
                expected_hash=str(attempt.session_uri_hash),
            )
        try:
            status = self.transport.query_resumable_session(
                session_uri=session_uri, total_bytes=media.size_bytes
            )
            if status.state == "INCOMPLETE":
                status = self.transport.upload_media(
                    session_uri=session_uri,
                    media_path=media.path,
                    start_offset=status.committed_bytes,
                    total_bytes=media.size_bytes,
                    mime_type=media.mime_type,
                )
        except Exception as exc:
            with self.session_factory() as session:
                attempt = session.get(YouTubeUploadAttempt, attempt_id)
                stage = session.get(YouTubePrivateStage, stage_id)
                # The exact resumable session is known, therefore a transport
                # interruption is reconcilable by querying that same URI. It
                # must not lead to a second videos.insert request.
                attempt.state = "UPLOADING"
                attempt.outcome_certainty = "CERTAIN_PENDING"
                attempt.error_code = "YOUTUBE_UPLOAD_RECONCILIATION_REQUIRED"
                stage.state = "UPLOADING"
                stage.last_error_code = attempt.error_code
                session.commit()
            raise ValidationFailureError(
                "YOUTUBE_UPLOAD_RECONCILIATION_REQUIRED"
            ) from exc
        if status.state != "COMPLETE" or not status.platform_video_id:
            with self.session_factory() as session:
                attempt = session.get(YouTubeUploadAttempt, attempt_id)
                stage = session.get(YouTubePrivateStage, stage_id)
                attempt.committed_bytes = status.committed_bytes
                attempt.state = "UPLOADING" if status.state == "INCOMPLETE" else "FAILED"
                attempt.outcome_certainty = (
                    "CERTAIN_PENDING"
                    if status.state == "INCOMPLETE"
                    else "CERTAIN_FAILURE"
                )
                attempt.error_code = (
                    "YOUTUBE_UPLOAD_INCOMPLETE"
                    if status.state == "INCOMPLETE"
                    else "YOUTUBE_UPLOAD_FAILED"
                )
                stage.state = "UPLOADING" if status.state == "INCOMPLETE" else "FAILED"
                stage.last_error_code = attempt.error_code
                session.commit()
            raise ValidationFailureError(
                "YOUTUBE_UPLOAD_INCOMPLETE"
                if status.state == "INCOMPLETE"
                else "YOUTUBE_UPLOAD_FAILED"
            )
        response_hash = _hash(status.response_payload)
        with self.session_factory() as session:
            attempt = session.get(YouTubeUploadAttempt, attempt_id)
            stage = session.get(YouTubePrivateStage, stage_id)
            attempt.committed_bytes = media.size_bytes
            attempt.state = "VERIFIED"
            attempt.outcome_certainty = "CERTAIN_SUCCESS"
            attempt.provider_video_id = status.platform_video_id
            attempt.provider_response_hash = response_hash
            stage.platform_video_id = status.platform_video_id
            stage.studio_url = (
                "https://studio.youtube.com/video/"
                + quote(status.platform_video_id, safe="")
                + "/edit"
            )
            stage.state = "BYTES_ACCEPTED"
            self._component_receipt(
                session=session,
                stage_id=stage_id,
                component_type="VIDEO_UPLOAD",
                provider_resource_id=status.platform_video_id,
                request_hash=attempt.request_hash,
                response_payload=status.response_payload,
            )
            episode_binding = session.scalar(
                select(YouTubeSeriesEpisodeBinding).where(
                    YouTubeSeriesEpisodeBinding.youtube_private_stage_id == stage_id
                )
            )
            candidate = session.get(FinalReviewCandidate, stage.final_review_candidate_id)
            if episode_binding is not None:
                episode_binding.youtube_video_id = status.platform_video_id
            elif candidate is not None and candidate.content_mode == "SERIES_EPISODE":
                playlist = session.scalar(
                    select(YouTubeSeriesPlaylistBinding).where(
                        YouTubeSeriesPlaylistBinding.series_plan_id == candidate.series_plan_id
                    )
                )
                if playlist is not None:
                    episode_payload = {
                        "schema_version": "vcos.youtube-series-episode-binding.v1",
                        "playlist_binding_id": str(playlist.id),
                        "series_plan_id": str(candidate.series_plan_id),
                        "series_run_id": str(candidate.series_run_id),
                        "technical_episode_number": candidate.episode_number,
                        "video_project_id": str(candidate.video_project_id),
                        "youtube_private_stage_id": str(stage.id),
                        "youtube_video_id": status.platform_video_id,
                        "state": "PRIVATE_UPLOADED",
                        "public_episode_ordinal": None,
                    }
                    session.add(
                        YouTubeSeriesEpisodeBinding(
                            youtube_series_playlist_binding_id=playlist.id,
                            series_plan_id=candidate.series_plan_id,
                            series_run_id=candidate.series_run_id,
                            technical_episode_number=candidate.episode_number,
                            video_project_id=candidate.video_project_id,
                            youtube_private_stage_id=stage.id,
                            youtube_video_id=status.platform_video_id,
                            state="PRIVATE_UPLOADED",
                            binding_hash=_hash(episode_payload),
                        )
                    )
            session.commit()
        return status.platform_video_id

    def _write_component_once(
        self,
        *,
        stage_id: uuid.UUID,
        component_type: str,
        request_payload: Mapping[str, Any],
        call,
    ) -> None:
        request_hash = _hash(request_payload)
        effect_key = f"youtube:{stage_id}:{component_type.lower()}:{request_hash}"
        with self.session_factory() as session:
            receipt = session.scalar(
                select(YouTubeComponentReceipt).where(
                    YouTubeComponentReceipt.youtube_private_stage_id == stage_id,
                    YouTubeComponentReceipt.component_type == component_type,
                )
            )
            if receipt is not None:
                if receipt.request_hash != request_hash:
                    raise ConflictError("YOUTUBE_COMPONENT_RECEIPT_IDENTITY_MISMATCH")
                return
            attempt = session.scalar(
                select(YouTubeComponentAttempt)
                .where(
                    YouTubeComponentAttempt.youtube_private_stage_id == stage_id,
                    YouTubeComponentAttempt.component_type == component_type,
                )
                .with_for_update()
            )
            if attempt is None:
                attempt = YouTubeComponentAttempt(
                    youtube_private_stage_id=stage_id,
                    component_type=component_type,
                    provider_effect_key=effect_key,
                    request_hash=request_hash,
                    state="INTENDED",
                    attempt_count=0,
                )
                session.add(attempt)
                session.commit()
            elif attempt.request_hash != request_hash:
                raise ConflictError("YOUTUBE_COMPONENT_ATTEMPT_IDENTITY_MISMATCH")
            if attempt.state == "VERIFIED":
                return
            if attempt.state in {"SUBMITTED", "OUTCOME_UNKNOWN"}:
                raise ValidationFailureError("YOUTUBE_COMPONENT_OUTCOME_UNKNOWN")
            attempt.state = "SUBMITTED"
            attempt.attempt_count = 1
            session.commit()
            attempt_id = attempt.id
        try:
            response = dict(call())
        except Exception as exc:
            with self.session_factory() as session:
                attempt = session.get(YouTubeComponentAttempt, attempt_id)
                attempt.state = "OUTCOME_UNKNOWN"
                attempt.error_code = f"YOUTUBE_{component_type}_OUTCOME_UNKNOWN"
                session.commit()
            raise ValidationFailureError(f"YOUTUBE_{component_type}_OUTCOME_UNKNOWN") from exc
        resource_id = str(response.get("id") or response.get("etag") or component_type)
        response_hash = _hash(response)
        with self.session_factory() as session:
            attempt = session.get(YouTubeComponentAttempt, attempt_id)
            attempt.state = "VERIFIED"
            attempt.provider_resource_id = resource_id
            attempt.response_hash = response_hash
            self._component_receipt(
                session=session,
                stage_id=stage_id,
                component_type=component_type,
                provider_resource_id=resource_id,
                request_hash=request_hash,
                response_payload=response,
            )
            session.commit()

    @staticmethod
    def _component_receipt(
        *,
        session: Session,
        stage_id: uuid.UUID,
        component_type: str,
        provider_resource_id: str | None,
        request_hash: str,
        response_payload: Mapping[str, Any],
    ) -> YouTubeComponentReceipt:
        existing = session.scalar(
            select(YouTubeComponentReceipt).where(
                YouTubeComponentReceipt.youtube_private_stage_id == stage_id,
                YouTubeComponentReceipt.component_type == component_type,
            )
        )
        response_hash = _hash(response_payload)
        body = {
            "schema_version": "vcos.youtube-component-receipt.v1",
            "stage_id": str(stage_id),
            "component_type": component_type,
            "state": "VERIFIED",
            "provider_resource_id": provider_resource_id,
            "request_hash": request_hash,
            "response_hash": response_hash,
            "evidence": dict(response_payload),
        }
        digest = _hash(body)
        if existing is not None:
            if existing.receipt_hash != digest:
                raise ConflictError("YOUTUBE_COMPONENT_RECEIPT_CONFLICT")
            return existing
        receipt = YouTubeComponentReceipt(
            youtube_private_stage_id=stage_id,
            component_type=component_type,
            state="VERIFIED",
            provider_resource_id=provider_resource_id,
            request_hash=request_hash,
            response_hash=response_hash,
            evidence=dict(response_payload),
            receipt_hash=digest,
        )
        session.add(receipt)
        session.flush()
        return receipt

    def _finalize_private_readback(
        self,
        *,
        stage_id: uuid.UUID,
        readback: YouTubePrivateReadback,
        raw_observed: Mapping[str, Any],
    ) -> None:
        with self.session_factory() as session:
            stage = session.get(YouTubePrivateStage, stage_id)
            credential = session.get(
                YouTubePublishingCredential, stage.publishing_credential_id
            )
            expected = stage.staging_metadata
            snippet = expected["snippet"]
            status = expected["status"]
            if (
                readback.platform_channel_id != credential.platform_channel_id
                or readback.platform_video_id != stage.platform_video_id
                or readback.title != snippet["title"]
                or readback.description != snippet["description"]
                or readback.tags != list(snippet.get("tags") or [])
                or readback.category_id != str(snippet.get("categoryId") or "")
                or readback.default_language
                != (snippet.get("defaultLanguage") or None)
                or readback.privacy_status != "PRIVATE"
                or readback.processing_status != "SUCCEEDED"
                or readback.made_for_kids != status["selfDeclaredMadeForKids"]
                or readback.contains_synthetic_media != status["containsSyntheticMedia"]
                or not readback.thumbnail_verified
                or not readback.caption_verified
            ):
                stage.state = "BLOCKED"
                stage.last_error_code = "YOUTUBE_PRIVATE_READBACK_MISMATCH"
                session.commit()
                raise ValidationFailureError(stage.last_error_code)
            metadata_payload = {
                "platform_channel_id": readback.platform_channel_id,
                "platform_video_id": readback.platform_video_id,
                "title": readback.title,
                "description": readback.description,
                "tags": list(readback.tags),
                "category_id": readback.category_id,
                "default_language": readback.default_language,
                "privacy_status": readback.privacy_status,
                "made_for_kids": readback.made_for_kids,
                "contains_synthetic_media": readback.contains_synthetic_media,
            }
            self._component_receipt(
                session=session,
                stage_id=stage_id,
                component_type="METADATA_READBACK",
                provider_resource_id=readback.platform_video_id,
                request_hash=_hash({"video_id": readback.platform_video_id, "part": "snippet,status"}),
                response_payload=metadata_payload,
            )
            self._component_receipt(
                session=session,
                stage_id=stage_id,
                component_type="PROCESSING_READBACK",
                provider_resource_id=readback.platform_video_id,
                request_hash=_hash({"video_id": readback.platform_video_id, "part": "processingDetails"}),
                response_payload={"processing_status": "SUCCEEDED", "raw_hash": _hash(raw_observed)},
            )
            component_types = set(
                session.scalars(
                    select(YouTubeComponentReceipt.component_type).where(
                        YouTubeComponentReceipt.youtube_private_stage_id == stage_id
                    )
                ).all()
            )
            if not _COMPONENTS_REQUIRED_FOR_PRIVATE_VERIFICATION.issubset(component_types):
                raise ValidationFailureError("YOUTUBE_PRIVATE_COMPONENT_RECEIPTS_INCOMPLETE")
            stage.observed_metadata = metadata_payload
            stage.observed_metadata_hash = _hash(metadata_payload)
            stage.processing_status = "SUCCEEDED"
            stage.state = "PRIVATE_VERIFIED"
            stage.private_verified_at = utc_now()
            stage.last_error_code = None
            release_task = session.scalar(
                select(HumanUploadTask).where(
                    HumanUploadTask.final_video_decision_id
                    == stage.final_video_decision_id
                )
            )
            if release_task is not None and release_task.task_state not in {
                "VERIFIED",
                "CANCELED",
            }:
                release_task.task_state = "AWAITING_CONFIRMATION"
                release_task.blocked_reason = None
                release_task.checklist = [
                    {
                        "key": "YOUTUBE_PRIVATE_STAGE",
                        "required": True,
                        "state": "PRIVATE_VERIFIED",
                        "youtube_private_stage_id": str(stage.id),
                        "platform_video_id": stage.platform_video_id,
                        "studio_url": stage.studio_url,
                    },
                    {
                        "key": "HUMAN_PUBLIC_RELEASE",
                        "required": True,
                        "state": "PENDING",
                        "auto_publish": False,
                    },
                    {
                        "key": "PUBLICATION_CONFIRMATION",
                        "required": True,
                        "state": "PENDING",
                    },
                ]
                release_task.operator_note = (
                    "YouTube PRIVATE verified. Open Studio and perform the "
                    "human-only public release, then submit publication confirmation."
                )
            TelegramDeliveryService(session).prepare(
                candidate_id=stage.final_review_candidate_id,
                notification_kind="YOUTUBE_PRIVATE_VERIFIED",
                youtube_stage=stage,
            )
            LocalMediaPurgeService(session).prepare_after_private_verified(
                stage_id=stage.id,
            )
            session.commit()


class LocalMediaPurgeService:
    """Prepare a deterministic purge command after private remote verification."""

    def __init__(self, session: Session, *, allowed_root: Path | None = None):
        self.session = session
        self.allowed_root = (allowed_root or _production_root()).resolve()

    def prepare_after_private_verified(
        self,
        *,
        stage_id: uuid.UUID,
        command_id: str | None = None,
    ) -> LocalMediaPurgeAttempt | None:
        stage = self.session.get(YouTubePrivateStage, stage_id)
        if stage is None:
            raise NotFoundError(f"youtube private stage not found: {stage_id}")
        if stage.state != "PRIVATE_VERIFIED":
            raise ValidationFailureError("LOCAL_MEDIA_PURGE_REQUIRES_PRIVATE_VERIFIED")
        final_media = self.session.get(FinalMediaRef, stage.final_media_ref_id)
        if final_media is None:
            raise ValidationFailureError("LOCAL_MEDIA_PURGE_FINAL_MEDIA_REQUIRED")
        if not str(final_media.file_ref).startswith("vcos-local-archive://"):
            # Legacy Drive-only media has no active local-archive object to purge.
            return None
        original = _local_archive_path(
            final_media_ref=final_media,
            checksum=stage.final_media_checksum,
            root=self.allowed_root,
        )
        stable_command = command_id or f"local-media-purge:{stage.id}"
        attempt_id = uuid.uuid5(
            _DELIVERY_EVENT_NAMESPACE, f"local-media-purge:{stage.id}"
        )
        quarantine = (
            self.allowed_root
            / "purge-quarantine"
            / f"{attempt_id}-{stage.final_media_checksum}.mp4"
        ).resolve()
        identity = {
            "schema_version": "vcos.local-media-purge-attempt.v1",
            "attempt_id": str(attempt_id),
            "youtube_private_stage_id": str(stage.id),
            "final_media_ref_id": str(final_media.id),
            "command_id": stable_command,
            "original_file_ref": f"file://{original}",
            "quarantine_file_ref": f"file://{quarantine}",
            "checksum_sha256": stage.final_media_checksum,
        }
        digest = _hash(identity)
        existing = self.session.scalar(
            select(LocalMediaPurgeAttempt).where(
                LocalMediaPurgeAttempt.youtube_private_stage_id == stage.id
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise ConflictError("LOCAL_MEDIA_PURGE_ATTEMPT_IDENTITY_MISMATCH")
            attempt = existing
        else:
            attempt = LocalMediaPurgeAttempt(
                id=attempt_id,
                youtube_private_stage_id=stage.id,
                final_media_ref_id=final_media.id,
                command_id=stable_command,
                original_file_ref=identity["original_file_ref"],
                quarantine_file_ref=identity["quarantine_file_ref"],
                checksum_sha256=stage.final_media_checksum,
                state="INTENDED",
                attempt_count=0,
                content_hash=digest,
            )
            self.session.add(attempt)
            self.session.flush()
        _append_delivery_event_once(
            self.session,
            event_type=LOCAL_MEDIA_PURGE_EVENT_TYPE,
            aggregate_type=LOCAL_MEDIA_PURGE_AGGREGATE_TYPE,
            aggregate_id=attempt.id,
            company_id=stage.company_id,
            channel_workspace_id=stage.channel_workspace_id,
            payload={
                "local_media_purge_attempt_id": str(attempt.id),
                "attempt_hash": attempt.content_hash,
            },
            max_attempts=3,
            causation_id=stage.id,
        )
        return attempt


class LocalMediaPurgeExecutor:
    """Remove active MP4 bytes with deterministic quarantine reconciliation."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        allowed_root: Path | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.allowed_root = (allowed_root or _production_root()).resolve()

    def execute(self, *, attempt_id: uuid.UUID) -> LocalMediaPurgeReceipt:
        with self.session_factory() as session:
            attempt = session.scalar(
                select(LocalMediaPurgeAttempt)
                .where(LocalMediaPurgeAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise NotFoundError(f"local purge attempt not found: {attempt_id}")
            existing = session.scalar(
                select(LocalMediaPurgeReceipt).where(
                    LocalMediaPurgeReceipt.youtube_private_stage_id
                    == attempt.youtube_private_stage_id
                )
            )
            if existing is not None:
                return existing
            stage = session.get(YouTubePrivateStage, attempt.youtube_private_stage_id)
            if stage is None or stage.state != "PRIVATE_VERIFIED":
                raise ValidationFailureError(
                    "LOCAL_MEDIA_PURGE_REQUIRES_PRIVATE_VERIFIED"
                )
            original = _purge_path(
                attempt.original_file_ref, root=self.allowed_root, kind="original"
            )
            quarantine = _purge_path(
                attempt.quarantine_file_ref, root=self.allowed_root, kind="quarantine"
            )
            if attempt.state == "INTENDED":
                attempt.state = "SUBMITTED"
                attempt.attempt_count = 1
                attempt.error_code = None
                session.commit()

        original_exists = original.is_file() and not original.is_symlink()
        quarantine_exists = quarantine.is_file() and not quarantine.is_symlink()
        if original_exists and quarantine_exists:
            raise ValidationFailureError("LOCAL_MEDIA_PURGE_DUPLICATE_BYTES")
        if original_exists:
            if _sha256_file(original) != attempt.checksum_sha256:
                raise ValidationFailureError("LOCAL_MEDIA_PURGE_BYTES_MISMATCH")
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original, quarantine)
            original_exists = False
            quarantine_exists = True
        if quarantine_exists and _sha256_file(quarantine) != attempt.checksum_sha256:
            raise ValidationFailureError("LOCAL_MEDIA_PURGE_QUARANTINE_MISMATCH")

        with self.session_factory() as session:
            attempt = session.get(LocalMediaPurgeAttempt, attempt_id)
            if attempt is None:
                raise NotFoundError(f"local purge attempt not found: {attempt_id}")
            if quarantine_exists:
                attempt.state = "QUARANTINED"
                attempt.error_code = None
                session.commit()

        if quarantine_exists:
            quarantine.unlink()
        if original.exists() or quarantine.exists():
            raise ValidationFailureError("LOCAL_MEDIA_PURGE_FILESYSTEM_NOT_EMPTY")

        with self.session_factory() as session:
            attempt = session.get(LocalMediaPurgeAttempt, attempt_id)
            if attempt is None:
                raise NotFoundError(f"local purge attempt not found: {attempt_id}")
            existing = session.scalar(
                select(LocalMediaPurgeReceipt).where(
                    LocalMediaPurgeReceipt.youtube_private_stage_id
                    == attempt.youtube_private_stage_id
                )
            )
            if existing is not None:
                return existing
            body = {
                "schema_version": "vcos.local-media-purge-receipt.v1",
                "youtube_private_stage_id": str(attempt.youtube_private_stage_id),
                "final_media_ref_id": str(attempt.final_media_ref_id),
                "local_file_ref": attempt.original_file_ref,
                "checksum_sha256": attempt.checksum_sha256,
                "state": "PURGED",
                "attempt_hash": attempt.content_hash,
            }
            receipt = LocalMediaPurgeReceipt(
                youtube_private_stage_id=attempt.youtube_private_stage_id,
                final_media_ref_id=attempt.final_media_ref_id,
                local_file_ref=attempt.original_file_ref,
                checksum_sha256=attempt.checksum_sha256,
                state="PURGED",
                deleted_at=utc_now(),
                receipt_hash=_hash(body),
            )
            attempt.state = "PURGED"
            attempt.error_code = None
            final_media = session.get(FinalMediaRef, attempt.final_media_ref_id)
            cloud_ref = (
                session.get(CloudMediaRef, final_media.cloud_media_ref_id)
                if final_media is not None and final_media.cloud_media_ref_id is not None
                else None
            )
            if cloud_ref is not None:
                if (
                    cloud_ref.storage_provider != "VCOS_LOCAL_ARCHIVE"
                    or cloud_ref.checksum_sha256 != attempt.checksum_sha256
                ):
                    raise ValidationFailureError(
                        "LOCAL_MEDIA_PURGE_CLOUD_AUTHORITY_DRIFT"
                    )
                cloud_ref.local_cleanup_status = "CLEANED"
                cloud_ref.cleaned_at = receipt.deleted_at
                cloud_ref.retention_policy = {
                    **dict(cloud_ref.retention_policy or {}),
                    "keep_local": False,
                    "cleanup_authority": "YOUTUBE_PRIVATE_VERIFIED",
                    "youtube_private_stage_id": str(
                        attempt.youtube_private_stage_id
                    ),
                }
            session.add(receipt)
            session.commit()
            return receipt


class TelegramTransport(Protocol):
    def send_message(self, *, bot_token: str, chat_id: str, text: str) -> TelegramSendResult: ...


class TelegramHttpTransport:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30)

    def send_message(self, *, bot_token: str, chat_id: str, text: str) -> TelegramSendResult:
        response = self.client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            raise ValidationFailureError("TELEGRAM_SEND_FAILED")
        result = payload.get("result") or {}
        return TelegramSendResult(
            message_id=str(result.get("message_id") or ""), response_payload=payload
        )


class TelegramDeliveryService:
    def __init__(self, session: Session):
        self.session = session

    def prepare(
        self,
        *,
        candidate_id: uuid.UUID,
        notification_kind: str = "FINAL_REVIEW_READY",
        youtube_stage: YouTubePrivateStage | None = None,
    ) -> TelegramDeliveryNotification:
        candidate = self.session.get(FinalReviewCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"final review candidate not found: {candidate_id}")
        existing = self.session.scalar(
            select(TelegramDeliveryNotification).where(
                TelegramDeliveryNotification.final_review_candidate_id == candidate.id,
                TelegramDeliveryNotification.notification_kind == notification_kind,
            )
        )
        reference = self.session.scalar(
            select(CredentialReference).where(
                CredentialReference.provider_key == "telegram_bot",
                CredentialReference.status.in_(["CONFIGURED", "ACTIVE"]),
            )
        )
        chat_ref = os.getenv("VCOS_TELEGRAM_CHAT_ID_REF")
        if existing is not None:
            if existing.state == "BLOCKED_CONFIG" and reference is not None and chat_ref:
                existing.credential_reference_id = reference.id
                existing.chat_binding_ref = chat_ref
                existing.state = "PENDING"
                existing.error_code = None
            if existing.state == "PENDING":
                self._enqueue(existing)
            return existing
        payload = {
            "schema_version": "vcos.telegram-delivery-notification.v1",
            "notification_kind": notification_kind,
            "channel_workspace_id": str(candidate.channel_workspace_id),
            "video_project_id": str(candidate.video_project_id),
            "title": str(candidate.publish_metadata_snapshot.get("title") or ""),
            "content_mode": candidate.content_mode,
            "series_plan_id": (
                str(candidate.series_plan_id) if candidate.series_plan_id else None
            ),
            "episode_number": candidate.episode_number,
            "final_review_candidate_id": str(candidate.id),
            "final_media_checksum": candidate.final_media_hash,
            "production_package_hash": candidate.production_package_hash,
            "total_budget": _candidate_budget_summary(self.session, candidate),
            "youtube_private_stage_id": (str(youtube_stage.id) if youtube_stage else None),
            "youtube_studio_url": youtube_stage.studio_url if youtube_stage else None,
        }
        state = "PENDING" if reference is not None and chat_ref else "BLOCKED_CONFIG"
        notice = TelegramDeliveryNotification(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            final_review_candidate_id=candidate.id,
            credential_reference_id=reference.id if reference else None,
            chat_binding_ref=chat_ref,
            notification_kind=notification_kind,
            payload=payload,
            payload_hash=_hash(payload),
            state=state,
            attempt_count=0,
            error_code=None if state == "PENDING" else "TELEGRAM_CONFIG_REQUIRED",
        )
        self.session.add(notice)
        self.session.flush()
        if notice.state == "PENDING":
            self._enqueue(notice)
        return notice

    def _enqueue(self, notice: TelegramDeliveryNotification) -> DomainEvent:
        return _append_delivery_event_once(
            self.session,
            event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
            aggregate_type=TELEGRAM_DELIVERY_AGGREGATE_TYPE,
            aggregate_id=notice.id,
            company_id=notice.company_id,
            channel_workspace_id=notice.channel_workspace_id,
            payload={
                "telegram_delivery_notification_id": str(notice.id),
                "payload_hash": notice.payload_hash,
            },
            max_attempts=1,
            causation_id=notice.final_review_candidate_id,
        )


class TelegramDeliveryExecutor:
    """Persist SUBMITTED before the one permitted Telegram provider call."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        transport: TelegramTransport | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.transport = transport or TelegramHttpTransport()

    def execute(self, *, notification_id: uuid.UUID) -> TelegramDeliveryNotification:
        with self.session_factory() as session:
            notice = session.scalar(
                select(TelegramDeliveryNotification)
                .where(TelegramDeliveryNotification.id == notification_id)
                .with_for_update()
            )
            if notice is None:
                raise NotFoundError(
                    f"telegram notification not found: {notification_id}"
                )
            if notice.state == "SENT":
                return notice
            if notice.state in {"SUBMITTED", "OUTCOME_UNKNOWN"}:
                raise ValidationFailureError("TELEGRAM_NOTIFICATION_OUTCOME_UNKNOWN")
            if notice.state != "PENDING" or notice.attempt_count != 0:
                raise ValidationFailureError("TELEGRAM_NOTIFICATION_NOT_SENDABLE")
            reference = session.get(CredentialReference, notice.credential_reference_id)
            if reference is None or not reference.secret_ref or not notice.chat_binding_ref:
                notice.state = "BLOCKED_CONFIG"
                notice.error_code = "TELEGRAM_CONFIG_REQUIRED"
                session.commit()
                return notice
            bot_token = _resolve_simple_secret(reference.secret_ref)
            chat_id = _resolve_chat_binding(notice.chat_binding_ref)
            text = _telegram_text(notice.payload)
            notice.state = "SUBMITTED"
            notice.attempt_count = 1
            notice.error_code = None
            session.commit()

        try:
            result = self.transport.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
            )
        except Exception as exc:
            with self.session_factory() as session:
                notice = session.get(TelegramDeliveryNotification, notification_id)
                if notice is None:
                    raise NotFoundError(
                        f"telegram notification not found: {notification_id}"
                    ) from exc
                notice.state = "OUTCOME_UNKNOWN"
                notice.error_code = "TELEGRAM_SEND_OUTCOME_UNKNOWN"
                session.commit()
            raise ValidationFailureError("TELEGRAM_SEND_OUTCOME_UNKNOWN") from exc

        with self.session_factory() as session:
            notice = session.get(TelegramDeliveryNotification, notification_id)
            if notice is None or notice.state != "SUBMITTED":
                raise ValidationFailureError("TELEGRAM_NOTIFICATION_STATE_DRIFT")
            notice.state = "SENT"
            notice.provider_message_id = result.message_id
            notice.provider_response_hash = _hash(result.response_payload)
            notice.sent_at = utc_now()
            notice.error_code = None
            session.commit()
            return notice



def _local_archive_path(
    *,
    final_media_ref: FinalMediaRef,
    checksum: str,
    root: Path,
) -> Path:
    """Resolve exact local-archive bytes without trusting an arbitrary path."""

    raw = str(final_media_ref.file_ref or "")
    if not raw.startswith("vcos-local-archive://"):
        raise ValidationFailureError("LOCAL_MEDIA_PURGE_LOCAL_ARCHIVE_REQUIRED")
    parts = raw.removeprefix("vcos-local-archive://").split("/")
    if (
        len(parts) != 3
        or parts[1] != checksum
        or parts[2] != "final.mp4"
        or not parts[0]
    ):
        raise ValidationFailureError("LOCAL_MEDIA_PURGE_LOCAL_ARCHIVE_REQUIRED")
    candidate = (root / "archive" / parts[0] / f"{checksum}.mp4").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValidationFailureError("LOCAL_MEDIA_PURGE_PATH_ESCAPE") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise ValidationFailureError("LOCAL_MEDIA_PURGE_BYTES_UNAVAILABLE")
    if _sha256_file(candidate) != checksum:
        raise ValidationFailureError("LOCAL_MEDIA_PURGE_BYTES_MISMATCH")
    return candidate


def _purge_path(value: str, *, root: Path, kind: str) -> Path:
    """Resolve a deterministic purge path and keep it inside the media root."""

    raw = value.removeprefix("file://")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValidationFailureError(f"LOCAL_MEDIA_PURGE_{kind.upper()}_PATH_ESCAPE") from exc
    if candidate.is_symlink():
        raise ValidationFailureError(f"LOCAL_MEDIA_PURGE_{kind.upper()}_SYMLINK_REJECTED")
    return candidate

def _resolve_local_ref(value: str, *, root: Path) -> Path:
    raw = value.removeprefix("file://")
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("thumbnail file not found") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ValidationFailureError("PRODUCTION_THUMBNAIL_PATH_REJECTED")
    return resolved


def _image_dimensions(path: Path, mime_type: str) -> tuple[int, int]:
    data = path.read_bytes()[:65536]
    if mime_type == "image/png":
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValidationFailureError("PRODUCTION_THUMBNAIL_PNG_INVALID")
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if mime_type == "image/jpeg":
        if not data.startswith(b"\xff\xd8"):
            raise ValidationFailureError("PRODUCTION_THUMBNAIL_JPEG_INVALID")
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            length = int.from_bytes(data[index : index + 2], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                return (
                    int.from_bytes(data[index + 5 : index + 7], "big"),
                    int.from_bytes(data[index + 3 : index + 5], "big"),
                )
            index += length
    raise ValidationFailureError("PRODUCTION_THUMBNAIL_DIMENSIONS_UNREADABLE")


def _normalize_youtube_readback(item: Mapping[str, Any]) -> YouTubePrivateReadback:
    snippet = dict(item.get("snippet") or {})
    status = dict(item.get("status") or {})
    processing = dict(item.get("processingDetails") or {})
    return YouTubePrivateReadback(
        platform_channel_id=str(snippet.get("channelId") or ""),
        platform_video_id=str(item.get("id") or ""),
        title=str(snippet.get("title") or ""),
        description=str(snippet.get("description") or ""),
        tags=[str(value) for value in list(snippet.get("tags") or [])],
        category_id=str(snippet.get("categoryId") or ""),
        default_language=(
            str(snippet.get("defaultLanguage"))
            if snippet.get("defaultLanguage")
            else None
        ),
        privacy_status=str(status.get("privacyStatus") or "").upper(),
        processing_status=str(processing.get("processingStatus") or "").upper(),
        made_for_kids=bool(status.get("madeForKids", status.get("selfDeclaredMadeForKids", False))),
        contains_synthetic_media=bool(status.get("containsSyntheticMedia", False)),
        thumbnail_verified=bool(item.get("vcosThumbnailVerified", False)),
        caption_verified=bool(item.get("vcosCaptionVerified", False)),
        evidence_ref=str(item.get("vcosEvidenceRef") or "youtube-readback://videos.list"),
    )


def _candidate_budget_summary(
    session: Session,
    candidate: FinalReviewCandidate,
) -> dict[str, Any]:
    authorities = list(
        session.scalars(
            select(CombinedReplacementBudgetAuthority)
            .where(
                CombinedReplacementBudgetAuthority.video_project_id
                == candidate.video_project_id,
                CombinedReplacementBudgetAuthority.state == "FROZEN",
            )
            .order_by(CombinedReplacementBudgetAuthority.created_at.desc())
        ).all()
    )
    if not authorities:
        return {
            "authority_state": "UNAVAILABLE",
            "production_package_hash": candidate.production_package_hash,
            "final_media_hash": candidate.final_media_hash,
        }
    authority = authorities[0]
    return {
        "authority_state": authority.state,
        "authority_ref": authority.authority_ref,
        "authority_hash": authority.content_hash,
        "projected_total_usd": str(
            authority.combined_replacement_projected_cost_usd
        ),
        "approved_ceiling_usd": str(authority.approved_ceiling_usd),
        "components_usd": {
            "tts": str(authority.new_tts_projected_cost_usd),
            "forced_alignment": str(
                authority.forced_alignment_projected_cost_usd
            ),
            "ai_image": str(authority.ai_image_projected_cost_usd),
            "ai_video": str(authority.ai_video_projected_cost_usd),
            "other": str(authority.other_metered_effects_projected_cost_usd),
        },
    }


def _resolve_simple_secret(secret_ref: str) -> str:
    if secret_ref.startswith("env://"):
        value = os.getenv(secret_ref.removeprefix("env://"))
    elif secret_ref.startswith("env:"):
        value = os.getenv(secret_ref.removeprefix("env:"))
    elif secret_ref.startswith("local_file://"):
        payload = json.loads(
            Path(secret_ref.removeprefix("local_file://")).read_text(encoding="utf-8")
        )
        value = payload.get("bot_token") or payload.get("access_token")
    else:
        value = None
    if not value:
        raise ValidationFailureError("DELIVERY_SECRET_UNAVAILABLE")
    return str(value)


def _resolve_chat_binding(value: str) -> str:
    if value.startswith("env://"):
        resolved = os.getenv(value.removeprefix("env://"))
    else:
        resolved = value
    if not resolved:
        raise ValidationFailureError("TELEGRAM_CHAT_BINDING_UNAVAILABLE")
    return str(resolved)


def _telegram_text(payload: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "VCOS — VIDEO READY",
            f"Channel: {payload.get('channel_workspace_id')}",
            f"Title: {payload.get('title')}",
            f"Mode: {payload.get('content_mode')}",
            f"Series: {payload.get('series_plan_id') or '—'}",
            f"Episode: {payload.get('episode_number') or '—'}",
            f"Final Review: {payload.get('final_review_candidate_id')}",
            f"Media SHA256: {payload.get('final_media_checksum')}",
            f"YouTube Studio: {payload.get('youtube_studio_url') or 'not staged yet'}",
        ]
    )
