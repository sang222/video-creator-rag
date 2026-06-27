from __future__ import annotations

import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib import request as urlrequest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m8 import KNOWN_ANALYTICS_METRICS
from app.contracts.m10_3 import (
    OWNER_ANALYTICS_METRICS,
    PUBLIC_MONITOR_METRICS,
    UploadedVideoYouTubeFollowSummaryRead,
    YouTubeConnectionStatusRead,
    YouTubeOAuthStartResult,
    YouTubeOwnerAnalyticsProviderOutput,
    YouTubeOwnerAnalyticsSyncRequest,
    YouTubePublicProviderOutput,
)
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AnalyticsSnapshot,
    AnalyticsSyncRun,
    CredentialHealthSnapshot,
    CredentialReference,
    MetricAvailabilitySnapshot,
    RenderPackageSnapshot,
    UploadedVideo,
    UploadedVideoMetricsSummary,
    UploadedVideoYouTubeOwnerAnalyticsSnapshot,
    UploadedVideoYouTubePublicMonitorSnapshot,
    YouTubeMonitoringCredential,
    YouTubeOAuthSession,
    YouTubeOwnerAnalyticsSyncRun,
    YouTubePublicSyncRun,
)
from app.services.m8 import METRIC_UNITS


ROOT = Path(__file__).resolve().parents[2]
LOCAL_YOUTUBE_CREDENTIAL_DIR = ROOT / "var" / "credentials" / "youtube"
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_DATA_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_ANALYTICS_REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
YOUTUBE_PUBLIC_PROVIDER_KEY = "YOUTUBE_DATA_API"
YOUTUBE_OWNER_PROVIDER_KEY = "YOUTUBE_ANALYTICS_API"

SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_", "ya29.")
YOUTUBE_PLATFORM_VALUES = {"YOUTUBE", "YOUTUBE_SHORTS"}
YOUTUBE_NOT_AVAILABLE_METRICS = {"saves", "bookmarks"}
OWNER_TO_M8_METRIC_KEYS = {
    "impression_click_through_rate": "click_through_rate",
    "estimated_minutes_watched": "watch_time_minutes",
}


@dataclass(frozen=True)
class ProviderFetchResult:
    ok: bool
    output: dict[str, Any] | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class TokenExchanger(Protocol):
    def exchange_code(self, *, code: str, client_config: dict[str, str], scopes: list[str]) -> dict[str, Any]:
        ...

    def refresh_access_token(self, *, refresh_token: str, client_config: dict[str, str]) -> dict[str, Any]:
        ...


class GoogleOAuthTokenExchanger:
    def exchange_code(self, *, code: str, client_config: dict[str, str], scopes: list[str]) -> dict[str, Any]:
        payload = {
            "code": code,
            "client_id": client_config["client_id"],
            "client_secret": client_config["client_secret"],
            "redirect_uri": client_config["redirect_uri"],
            "grant_type": "authorization_code",
        }
        return _post_google_token(payload)

    def refresh_access_token(self, *, refresh_token: str, client_config: dict[str, str]) -> dict[str, Any]:
        payload = {
            "refresh_token": refresh_token,
            "client_id": client_config["client_id"],
            "client_secret": client_config["client_secret"],
            "grant_type": "refresh_token",
        }
        return _post_google_token(payload)


class YouTubeMonitoringConfigService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def scopes(self) -> list[str]:
        return [item.strip() for item in self.settings.youtube_oauth_scopes.split(",") if item.strip()]

    def public_monitor_enabled(self) -> bool:
        return bool(self.settings.youtube_public_monitor_enabled and self.settings.youtube_data_api_key)

    def owner_analytics_enabled(self) -> bool:
        return bool(self.settings.youtube_owner_analytics_enabled and self._oauth_client_config_or_none() and self.scopes)

    def public_api_key(self) -> str | None:
        return self.settings.youtube_data_api_key.get_secret_value() if self.settings.youtube_data_api_key else None

    def oauth_client_config(self) -> dict[str, str]:
        config = self._oauth_client_config_or_none()
        if config is None:
            raise ValidationFailureError("YouTube OAuth client is not configured")
        return config

    def safe_status(self) -> dict[str, Any]:
        public_enabled = self.public_monitor_enabled()
        owner_enabled = self.owner_analytics_enabled()
        return {
            "public_monitor_enabled": public_enabled,
            "public_config_state": "CONFIGURED" if public_enabled else "NOT_CONFIGURED",
            "owner_analytics_enabled": owner_enabled,
            "owner_config_state": "CONFIGURED" if owner_enabled else "NOT_CONFIGURED",
            "scopes": self.scopes,
            "secret_values_exposed": False,
        }

    def _oauth_client_config_or_none(self) -> dict[str, str] | None:
        client_id = self.settings.youtube_oauth_client_id
        client_secret = self.settings.youtube_oauth_client_secret.get_secret_value() if self.settings.youtube_oauth_client_secret else None
        redirect_uri = self.settings.youtube_oauth_redirect_uri
        if self.settings.youtube_oauth_client_secrets_file:
            file_config = _read_oauth_client_file(Path(self.settings.youtube_oauth_client_secrets_file))
            client_id = client_id or file_config.get("client_id")
            client_secret = client_secret or file_config.get("client_secret")
            redirect_uri = redirect_uri or file_config.get("redirect_uri")
        if not client_id or not client_secret or not redirect_uri:
            return None
        return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}


class YouTubeMetricMappingService:
    def map_public_video_item(self, item: dict[str, Any]) -> YouTubePublicProviderOutput:
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        content_details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        platform_video_id = str(item.get("id") or "")
        public_stats_viewable = status.get("publicStatsViewable")
        values = {
            "views": _optional_int(statistics.get("viewCount")),
            "likes": _optional_int(statistics.get("likeCount")),
            "comments": _optional_int(statistics.get("commentCount")),
        }
        availability = {
            key: "AVAILABLE" if value is not None else "NOT_AVAILABLE" if public_stats_viewable is False else "UNKNOWN"
            for key, value in values.items()
        }
        thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        thumbnail_url = _best_thumbnail_url(thumbnails)
        return YouTubePublicProviderOutput(
            platform_video_id=platform_video_id,
            video_url=f"https://www.youtube.com/watch?v={platform_video_id}" if platform_video_id else None,
            views=values["views"],
            likes=values["likes"],
            comments=values["comments"],
            youtube_title=snippet.get("title"),
            youtube_published_at=_parse_datetime(snippet.get("publishedAt")),
            youtube_channel_id=snippet.get("channelId"),
            youtube_channel_title=snippet.get("channelTitle"),
            thumbnail_url=thumbnail_url,
            duration_seconds=_parse_iso8601_duration_seconds(content_details.get("duration")),
            definition=content_details.get("definition"),
            caption_status=content_details.get("caption"),
            privacy_status=status.get("privacyStatus"),
            public_stats_viewable=public_stats_viewable if isinstance(public_stats_viewable, bool) else None,
            metric_availability=availability,
            freshness_state="FRESH",
            technical_appendix={
                "payload_hash": _payload_hash(_minimal_public_debug_payload(item)),
                "source": YOUTUBE_PUBLIC_PROVIDER_KEY,
                "no_comment_text_fetched": True,
                "no_description_stored": True,
            },
        )

    def map_owner_analytics_report(
        self,
        *,
        platform_video_id: str,
        start_date: date,
        end_date: date,
        report: dict[str, Any],
    ) -> YouTubeOwnerAnalyticsProviderOutput | None:
        headers = report.get("columnHeaders") if isinstance(report.get("columnHeaders"), list) else []
        rows = report.get("rows") if isinstance(report.get("rows"), list) else []
        if not rows:
            return None
        names = [str(header.get("name")) for header in headers if isinstance(header, dict)]
        first = rows[0]
        if not isinstance(first, list):
            return None
        by_source_name = {names[index]: first[index] for index in range(min(len(names), len(first)))}
        source_to_field = {
            "views": "views",
            "likes": "likes",
            "comments": "comments",
            "impressions": "impressions",
            "impressionClickThroughRate": "impression_click_through_rate",
            "averageViewDuration": "average_view_duration_seconds",
            "averageViewPercentage": "average_view_percentage",
            "estimatedMinutesWatched": "estimated_minutes_watched",
            "subscribersGained": "subscribers_gained",
            "subscribersLost": "subscribers_lost",
        }
        values: dict[str, Any] = {}
        availability: dict[str, str] = {}
        for source_name, field_name in source_to_field.items():
            if source_name in by_source_name:
                numeric = _optional_float(by_source_name[source_name])
                values[field_name] = numeric
                availability[field_name] = "AVAILABLE" if numeric is not None else "UNKNOWN"
            else:
                values[field_name] = None
                availability[field_name] = "UNKNOWN"
        return YouTubeOwnerAnalyticsProviderOutput(
            platform_video_id=platform_video_id,
            analytics_start_date=start_date,
            analytics_end_date=end_date,
            metric_availability=availability,
            freshness_state="FRESH",
            technical_appendix={
                "payload_hash": _payload_hash({"columnHeaders": headers, "row_count": len(rows)}),
                "source": YOUTUBE_OWNER_PROVIDER_KEY,
                "filters": ["video"],
                "monetization_metrics_deferred": True,
            },
            **values,
        )


class YouTubePublicStatsProvider:
    def __init__(self, mapping_service: YouTubeMetricMappingService | None = None):
        self.mapping_service = mapping_service or YouTubeMetricMappingService()

    def fetch(self, *, platform_video_id: str, api_key: str) -> ProviderFetchResult:
        query = urllib.parse.urlencode(
            {
                "part": "snippet,statistics,contentDetails,status",
                "id": platform_video_id,
                "key": api_key,
            }
        )
        request = urlrequest.Request(f"{YOUTUBE_DATA_VIDEOS_URL}?{query}", method="GET")
        try:
            with urlrequest.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                items = payload.get("items") if isinstance(payload.get("items"), list) else []
                if not items:
                    return ProviderFetchResult(False, http_status=response.status, error_code="YOUTUBE_VIDEO_NOT_FOUND", error_message="video not found")
                output = self.mapping_service.map_public_video_item(items[0]).model_dump(mode="json")
                return ProviderFetchResult(True, output=output, http_status=response.status)
        except urllib.error.HTTPError as exc:
            return ProviderFetchResult(False, http_status=exc.code, error_code=_youtube_http_error_code(exc.code), error_message="YouTube Data API error")
        except Exception:
            return ProviderFetchResult(False, error_code="YOUTUBE_PUBLIC_SYNC_FAILED", error_message="YouTube Data API request failed")


class YouTubeOwnerAnalyticsProvider:
    def __init__(self, mapping_service: YouTubeMetricMappingService | None = None):
        self.mapping_service = mapping_service or YouTubeMetricMappingService()

    def fetch(
        self,
        *,
        platform_video_id: str,
        access_token: str,
        start_date: date,
        end_date: date,
    ) -> ProviderFetchResult:
        metrics = (
            "views,likes,comments,impressions,impressionClickThroughRate,averageViewDuration,"
            "averageViewPercentage,estimatedMinutesWatched,subscribersGained,subscribersLost"
        )
        query = urllib.parse.urlencode(
            {
                "ids": "channel==MINE",
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "metrics": metrics,
                "filters": f"video=={platform_video_id}",
            }
        )
        request = urlrequest.Request(
            f"{YOUTUBE_ANALYTICS_REPORTS_URL}?{query}",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with urlrequest.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                output = self.mapping_service.map_owner_analytics_report(
                    platform_video_id=platform_video_id,
                    start_date=start_date,
                    end_date=end_date,
                    report=payload,
                )
                if output is None:
                    return ProviderFetchResult(False, http_status=response.status, error_code="YOUTUBE_METRIC_UNKNOWN", error_message="no analytics row returned")
                return ProviderFetchResult(True, output=output.model_dump(mode="json"), http_status=response.status)
        except urllib.error.HTTPError as exc:
            return ProviderFetchResult(False, http_status=exc.code, error_code=_youtube_http_error_code(exc.code), error_message="YouTube Analytics API error")
        except Exception:
            return ProviderFetchResult(False, error_code="YOUTUBE_OWNER_ANALYTICS_SYNC_FAILED", error_message="YouTube Analytics API request failed")


class YouTubeOAuthCredentialService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: YouTubeMonitoringConfigService | None = None,
        token_exchanger: TokenExchanger | None = None,
        credential_dir: Path | None = None,
    ):
        self.session = session
        self.config_service = config_service or YouTubeMonitoringConfigService()
        self.token_exchanger = token_exchanger or GoogleOAuthTokenExchanger()
        self.credential_dir = credential_dir or LOCAL_YOUTUBE_CREDENTIAL_DIR

    def store_token_response(
        self,
        *,
        token_response: dict[str, Any],
        scopes: list[str],
        company_id: uuid.UUID | None,
        channel_workspace_id: uuid.UUID | None,
    ) -> CredentialReference:
        refresh_value = token_response.get("refresh_token")
        access_value = token_response.get("access_token")
        if not refresh_value or not access_value:
            raise ValidationFailureError("YouTube OAuth owner analytics requires refresh_token and access_token")
        expires_at = _expires_at_from_response(token_response)
        credential_id = uuid.uuid4()
        storage_path = self._token_storage_path(credential_id)
        secret_ref = _local_file_secret_ref(storage_path)
        _write_json_secret_file(
            storage_path,
            {
                "access_token": access_value,
                "refresh_token": refresh_value,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "scope": token_response.get("scope"),
                "token_type": token_response.get("token_type"),
            },
        )
        reference = self._upsert_credential_reference(
            credential_id=credential_id,
            secret_ref=secret_ref,
            scopes=scopes,
            expires_at=expires_at,
        )
        self._upsert_monitoring_credential(
            credential_reference_id=reference.id,
            scopes=scopes,
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            state="CONNECTED",
            token_metadata={
                "storage": "LOCAL_DEV_FILE",
                "expires_at": expires_at.isoformat() if expires_at else None,
                "refresh_required": True,
                "raw_values_in_db": False,
            },
        )
        return reference

    def exchange_authorization_code(
        self,
        *,
        code: str,
        scopes: list[str],
        company_id: uuid.UUID | None,
        channel_workspace_id: uuid.UUID | None,
    ) -> CredentialReference:
        client_config = self.config_service.oauth_client_config()
        token_response = self.token_exchanger.exchange_code(code=code, client_config=client_config, scopes=scopes)
        return self.store_token_response(
            token_response=token_response,
            scopes=scopes,
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
        )

    def get_connected_owner_reference(self) -> CredentialReference | None:
        monitoring = self.session.scalars(
            select(YouTubeMonitoringCredential)
            .where(YouTubeMonitoringCredential.provider_key == YOUTUBE_OWNER_PROVIDER_KEY)
            .where(YouTubeMonitoringCredential.connection_state == "CONNECTED")
            .order_by(YouTubeMonitoringCredential.updated_at.desc())
            .limit(1)
        ).one_or_none()
        if monitoring is None:
            return None
        return self.session.get(CredentialReference, monitoring.credential_reference_id)

    def get_valid_access_token(self, reference: CredentialReference) -> str | None:
        if reference.status in {"MISSING", "REVOKED", "DISABLED"}:
            self._mark_monitoring_state(reference.id, "NEEDS_REAUTH", "YOUTUBE_OAUTH_NEEDS_REAUTH", "credential is not usable")
            return None
        payload = self._read_token_payload(reference)
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_at = _parse_datetime(payload.get("expires_at"))
        if expires_at is not None and expires_at <= utc_now() + timedelta(seconds=60):
            if not refresh_token:
                self._mark_monitoring_state(reference.id, "NEEDS_REAUTH", "YOUTUBE_OAUTH_NEEDS_REAUTH", "refresh token missing")
                return None
            try:
                refreshed = self.token_exchanger.refresh_access_token(
                    refresh_token=refresh_token,
                    client_config=self.config_service.oauth_client_config(),
                )
            except Exception:
                self._mark_monitoring_state(reference.id, "NEEDS_REAUTH", "YOUTUBE_OAUTH_NEEDS_REAUTH", "token refresh failed")
                return None
            access_token = refreshed.get("access_token")
            if not access_token:
                self._mark_monitoring_state(reference.id, "NEEDS_REAUTH", "YOUTUBE_OAUTH_NEEDS_REAUTH", "token refresh failed")
                return None
            expires_at = _expires_at_from_response(refreshed)
            payload["access_token"] = access_token
            payload["expires_at"] = expires_at.isoformat() if expires_at else None
            _write_json_secret_file(self._path_from_secret_ref(reference.secret_ref), payload)
            reference.expires_at = expires_at
            reference.status = "CONFIGURED"
            reference.metadata_ = {**(reference.metadata_ or {}), "last_refresh_at": utc_now().isoformat(), "raw_values_in_db": False}
            self._mark_monitoring_state(reference.id, "CONNECTED", None, None)
            self.session.flush()
        if not access_token:
            self._mark_monitoring_state(reference.id, "NEEDS_REAUTH", "YOUTUBE_OAUTH_NEEDS_REAUTH", "access token missing")
            return None
        return str(access_token)

    def _upsert_credential_reference(
        self,
        *,
        credential_id: uuid.UUID,
        secret_ref: str,
        scopes: list[str],
        expires_at: datetime | None,
    ) -> CredentialReference:
        existing = self.session.scalars(
            select(CredentialReference).where(
                CredentialReference.provider_key == "youtube_analytics_api",
                CredentialReference.credential_key == "owner_analytics_default",
            )
        ).one_or_none()
        if existing is None:
            existing = CredentialReference(
                id=credential_id,
                provider_key="youtube_analytics_api",
                credential_key="owner_analytics_default",
                credential_type="OAUTH_TOKEN",
                secret_ref=secret_ref,
                scope_blob={"scopes": scopes},
                status="CONFIGURED",
                expires_at=expires_at,
                metadata_={"storage": "LOCAL_DEV_FILE", "raw_values_in_db": False},
            )
            self.session.add(existing)
        else:
            existing.secret_ref = secret_ref
            existing.scope_blob = {"scopes": scopes}
            existing.status = "CONFIGURED"
            existing.expires_at = expires_at
            existing.metadata_ = {"storage": "LOCAL_DEV_FILE", "raw_values_in_db": False}
        self.session.flush()
        return existing

    def _upsert_monitoring_credential(
        self,
        *,
        credential_reference_id: uuid.UUID,
        scopes: list[str],
        company_id: uuid.UUID | None,
        channel_workspace_id: uuid.UUID | None,
        state: str,
        token_metadata: dict[str, Any],
    ) -> YouTubeMonitoringCredential:
        existing = self.session.scalars(
            select(YouTubeMonitoringCredential).where(
                YouTubeMonitoringCredential.provider_key == YOUTUBE_OWNER_PROVIDER_KEY,
                YouTubeMonitoringCredential.auth_mode == "OAUTH2",
            )
        ).one_or_none()
        if existing is None:
            existing = YouTubeMonitoringCredential(
                company_id=company_id,
                channel_workspace_id=channel_workspace_id,
                credential_reference_id=credential_reference_id,
                auth_mode="OAUTH2",
                provider_key=YOUTUBE_OWNER_PROVIDER_KEY,
                connection_state=state,
                scopes=scopes,
                token_metadata=token_metadata,
                last_health_check_at=utc_now(),
            )
            self.session.add(existing)
        else:
            existing.company_id = company_id
            existing.channel_workspace_id = channel_workspace_id
            existing.credential_reference_id = credential_reference_id
            existing.connection_state = state
            existing.scopes = scopes
            existing.token_metadata = token_metadata
            existing.last_health_check_at = utc_now()
            existing.error_code = None
            existing.error_message = None
        self.session.flush()
        return existing

    def _mark_monitoring_state(
        self,
        credential_reference_id: uuid.UUID,
        state: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        monitoring = self.session.scalars(
            select(YouTubeMonitoringCredential).where(
                YouTubeMonitoringCredential.credential_reference_id == credential_reference_id,
                YouTubeMonitoringCredential.provider_key == YOUTUBE_OWNER_PROVIDER_KEY,
            )
        ).one_or_none()
        if monitoring is None:
            return
        monitoring.connection_state = state
        monitoring.last_health_check_at = utc_now()
        monitoring.error_code = error_code
        monitoring.error_message = error_message
        self.session.flush()

    def _read_token_payload(self, reference: CredentialReference) -> dict[str, Any]:
        path = self._path_from_secret_ref(reference.secret_ref)
        if not path.exists():
            self._mark_monitoring_state(reference.id, "NEEDS_REAUTH", "YOUTUBE_OAUTH_NEEDS_REAUTH", "local token file missing")
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _path_from_secret_ref(self, secret_ref: str | None) -> Path:
        if not secret_ref or not secret_ref.startswith("local_file://"):
            raise ValidationFailureError("credential reference does not point to local dev token storage")
        value = secret_ref.removeprefix("local_file://")
        path = Path(value)
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()

    def _token_storage_path(self, credential_reference_id: uuid.UUID) -> Path:
        return self.credential_dir / "oauth" / f"{credential_reference_id}.json"


class YouTubeOAuthSessionService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: YouTubeMonitoringConfigService | None = None,
        credential_service: YouTubeOAuthCredentialService | None = None,
    ):
        self.session = session
        self.config_service = config_service or YouTubeMonitoringConfigService()
        self.credential_service = credential_service or YouTubeOAuthCredentialService(session, config_service=self.config_service)

    def start(
        self,
        *,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> YouTubeOAuthStartResult:
        if not self.config_service.owner_analytics_enabled():
            raise ValidationFailureError("YouTube owner analytics OAuth is not configured")
        client_config = self.config_service.oauth_client_config()
        state_token = secrets.token_urlsafe(32)
        scopes = self.config_service.scopes
        session = YouTubeOAuthSession(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            state_token_hash=_hash_state(state_token),
            redirect_uri=client_config["redirect_uri"],
            scopes=scopes,
            status="STARTED",
        )
        self.session.add(session)
        self.session.flush()
        params = {
            "response_type": "code",
            "client_id": client_config["client_id"],
            "redirect_uri": client_config["redirect_uri"],
            "scope": " ".join(scopes),
            "state": state_token,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return YouTubeOAuthStartResult(
            oauth_session_id=session.id,
            authorization_url=f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}",
        )

    def handle_callback(self, *, state: str, code: str | None = None, error: str | None = None) -> YouTubeOAuthSession:
        session = self._require_session_for_state(state)
        session.status = "CALLBACK_RECEIVED"
        self.session.flush()
        if error:
            session.status = "FAILED"
            session.error_code = "YOUTUBE_OAUTH_CALLBACK_RECEIVED"
            session.error_message = "OAuth callback returned an error"
            self.session.flush()
            return session
        if not code:
            session.status = "FAILED"
            session.error_code = "YOUTUBE_OAUTH_CALLBACK_RECEIVED"
            session.error_message = "OAuth callback missing authorization code"
            self.session.flush()
            return session
        try:
            reference = self.credential_service.exchange_authorization_code(
                code=code,
                scopes=session.scopes,
                company_id=session.company_id,
                channel_workspace_id=session.channel_workspace_id,
            )
        except Exception:
            session.status = "FAILED"
            session.error_code = "YOUTUBE_OAUTH_NEEDS_REAUTH"
            session.error_message = "OAuth token exchange failed"
            self.session.flush()
            return session
        session.status = "TOKEN_EXCHANGED"
        session.credential_reference_id = reference.id
        session.error_code = None
        session.error_message = None
        self.session.flush()
        return session

    def _require_session_for_state(self, state: str) -> YouTubeOAuthSession:
        hashed = _hash_state(state)
        session = self.session.scalars(
            select(YouTubeOAuthSession).where(YouTubeOAuthSession.state_token_hash == hashed).order_by(YouTubeOAuthSession.created_at.desc()).limit(1)
        ).one_or_none()
        if session is None:
            raise ValidationFailureError("invalid YouTube OAuth state")
        return session


class YouTubeCredentialHealthService:
    def __init__(self, session: Session, *, config_service: YouTubeMonitoringConfigService | None = None):
        self.session = session
        self.config_service = config_service or YouTubeMonitoringConfigService()

    def connection_status(self) -> YouTubeConnectionStatusRead:
        config = self.config_service.safe_status()
        public_ref = self._ensure_public_api_key_reference() if config["public_monitor_enabled"] else None
        public_state = "CONFIGURED" if public_ref is not None else "NOT_CONFIGURED"
        owner_monitoring = self.session.scalars(
            select(YouTubeMonitoringCredential)
            .where(YouTubeMonitoringCredential.provider_key == YOUTUBE_OWNER_PROVIDER_KEY)
            .order_by(YouTubeMonitoringCredential.updated_at.desc())
            .limit(1)
        ).one_or_none()
        owner_state = owner_monitoring.connection_state if owner_monitoring else config["owner_config_state"]
        owner_ref_id = owner_monitoring.credential_reference_id if owner_monitoring and owner_monitoring.connection_state == "CONNECTED" else None
        reasons: list[str] = []
        if public_ref is not None:
            reasons.append("YOUTUBE_PUBLIC_MONITOR_CONFIGURED")
        if config["owner_analytics_enabled"]:
            reasons.append("YOUTUBE_OWNER_ANALYTICS_CONFIGURED")
        if owner_state == "CONNECTED":
            reasons.append("YOUTUBE_OAUTH_TOKEN_EXCHANGED")
        next_action = None
        if not config["public_monitor_enabled"]:
            next_action = "Configure YOUTUBE_PUBLIC_MONITOR_ENABLED and YOUTUBE_DATA_API_KEY for public sync."
        elif owner_state != "CONNECTED":
            next_action = "Connect YouTube OAuth before owner analytics sync."
        return YouTubeConnectionStatusRead(
            public_monitor_enabled=config["public_monitor_enabled"],
            public_config_state=public_state,
            owner_analytics_enabled=config["owner_analytics_enabled"],
            owner_connection_state=owner_state,
            owner_analytics_connected=owner_state == "CONNECTED",
            public_credential_reference_id=public_ref.id if public_ref else None,
            owner_credential_reference_id=owner_ref_id,
            scopes=config["scopes"],
            reason_codes=reasons,
            next_action=next_action,
        )

    def record_owner_health(self, *, reference: CredentialReference | None, state: str, reason_code: str) -> None:
        if reference is None:
            return
        snapshot = CredentialHealthSnapshot(
            credential_reference_id=reference.id,
            provider_key=reference.provider_key,
            health_state=state,
            reason_codes=[reason_code],
            next_action="Reconnect YouTube OAuth." if state in {"MISSING", "EXPIRED", "REVOKED"} else None,
            metadata_={"youtube_owner_analytics": True, "raw_values_in_db": False},
        )
        reference.last_checked_at = snapshot.checked_at
        self.session.add(snapshot)
        self.session.flush()

    def _ensure_public_api_key_reference(self) -> CredentialReference | None:
        if not self.config_service.public_monitor_enabled():
            return None
        existing = self.session.scalars(
            select(CredentialReference).where(
                CredentialReference.provider_key == "youtube_data_api",
                CredentialReference.credential_key == "public_monitor_default",
            )
        ).one_or_none()
        if existing is None:
            existing = CredentialReference(
                provider_key="youtube_data_api",
                credential_key="public_monitor_default",
                credential_type="API_KEY",
                secret_ref="env://YOUTUBE_DATA_API_KEY",
                scope_blob={"mode": "PUBLIC_MONITOR"},
                status="CONFIGURED",
                metadata_={"env_ref": "YOUTUBE_DATA_API_KEY", "raw_values_in_db": False},
            )
            self.session.add(existing)
            self.session.flush()
        monitoring = self.session.scalars(
            select(YouTubeMonitoringCredential).where(
                YouTubeMonitoringCredential.provider_key == YOUTUBE_PUBLIC_PROVIDER_KEY,
                YouTubeMonitoringCredential.auth_mode == "API_KEY",
            )
        ).one_or_none()
        if monitoring is None:
            monitoring = YouTubeMonitoringCredential(
                credential_reference_id=existing.id,
                auth_mode="API_KEY",
                provider_key=YOUTUBE_PUBLIC_PROVIDER_KEY,
                connection_state="CONFIGURED",
                scopes=[],
                token_metadata={"env_ref": "YOUTUBE_DATA_API_KEY", "raw_values_in_db": False},
                last_health_check_at=utc_now(),
            )
            self.session.add(monitoring)
        else:
            monitoring.credential_reference_id = existing.id
            monitoring.connection_state = "CONFIGURED"
            monitoring.last_health_check_at = utc_now()
        self.session.flush()
        return existing


class YouTubePublicStatsSyncService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: YouTubeMonitoringConfigService | None = None,
        provider: YouTubePublicStatsProvider | None = None,
    ):
        self.session = session
        self.config_service = config_service or YouTubeMonitoringConfigService()
        self.provider = provider or YouTubePublicStatsProvider()

    def sync_uploaded_video(self, *, uploaded_video_id: uuid.UUID) -> YouTubePublicSyncRun:
        uploaded = _require_uploaded_video(self.session, uploaded_video_id)
        _validate_youtube_uploaded(uploaded)
        run = YouTubePublicSyncRun(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            platform_video_id=uploaded.platform_video_id,
            run_state="PENDING",
            source=YOUTUBE_PUBLIC_PROVIDER_KEY,
        )
        self.session.add(run)
        self.session.flush()
        if not self.config_service.public_monitor_enabled():
            run.run_state = "SKIPPED"
            run.completed_at = utc_now()
            run.error_code = "YOUTUBE_PUBLIC_MONITOR_NOT_CONFIGURED"
            run.error_message = "YouTube public monitor is not configured"
            self.session.flush()
            return run
        api_key = self.config_service.public_api_key()
        run.run_state = "RUNNING"
        run.started_at = utc_now()
        self.session.flush()
        result = self.provider.fetch(platform_video_id=uploaded.platform_video_id, api_key=api_key or "")
        run.http_status = result.http_status
        if not result.ok or result.output is None:
            run.run_state = "FAILED"
            run.completed_at = utc_now()
            run.error_code = result.error_code or "YOUTUBE_PUBLIC_SYNC_FAILED"
            run.error_message = result.error_message
            self.session.flush()
            return run
        output = YouTubePublicProviderOutput.model_validate(result.output)
        snapshot = self._create_public_snapshot(uploaded=uploaded, output=output)
        analytics = _create_m8_snapshot(
            self.session,
            uploaded=uploaded,
            metrics={key: getattr(output, key) for key in PUBLIC_MONITOR_METRICS if getattr(output, key) is not None},
            explicit_availability=output.metric_availability,
            sync_mode="YOUTUBE_PUBLIC_MONITOR",
            provider_key="youtube_data_api",
            source=YOUTUBE_PUBLIC_PROVIDER_KEY,
            authority="WEAK",
            source_snapshot_id=snapshot.id,
            freshness_state=output.freshness_state,
            confidence_level="MEDIUM",
            reason_codes=["YOUTUBE_PUBLIC_SYNC_COMPLETED", "PUBLIC_MONITOR_WEAK_AUTHORITY"],
        )
        run.run_state = "COMPLETED"
        run.completed_at = utc_now()
        run.metrics_found = bool(analytics.metrics_blob)
        run.created_snapshot_id = snapshot.id
        self.session.flush()
        return run

    def latest_snapshot(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubePublicMonitorSnapshot | None:
        return self.session.scalars(
            select(UploadedVideoYouTubePublicMonitorSnapshot)
            .where(UploadedVideoYouTubePublicMonitorSnapshot.uploaded_video_id == uploaded_video_id)
            .order_by(UploadedVideoYouTubePublicMonitorSnapshot.last_synced_at.desc(), UploadedVideoYouTubePublicMonitorSnapshot.created_at.desc())
            .limit(1)
        ).one_or_none()

    def _create_public_snapshot(
        self,
        *,
        uploaded: UploadedVideo,
        output: YouTubePublicProviderOutput,
    ) -> UploadedVideoYouTubePublicMonitorSnapshot:
        unknown = sorted(key for key, state in output.metric_availability.items() if state == "UNKNOWN")
        unavailable = sorted(key for key, state in output.metric_availability.items() if state == "NOT_AVAILABLE")
        status = "OK" if not unknown and not unavailable else "PARTIAL"
        title_match = _title_matches(uploaded, output.youtube_title)
        duration_match = _duration_matches(self.session, uploaded, output.duration_seconds)
        snapshot = UploadedVideoYouTubePublicMonitorSnapshot(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            platform_video_id=uploaded.platform_video_id,
            video_url=output.video_url or uploaded.video_url,
            views=output.views,
            likes=output.likes,
            comments=output.comments,
            youtube_title=output.youtube_title,
            youtube_published_at=output.youtube_published_at,
            youtube_channel_id=output.youtube_channel_id,
            youtube_channel_title=output.youtube_channel_title,
            thumbnail_url=str(output.thumbnail_url) if output.thumbnail_url else None,
            duration_seconds=output.duration_seconds,
            definition=output.definition,
            caption_status=output.caption_status,
            privacy_status=output.privacy_status,
            public_stats_viewable=output.public_stats_viewable,
            title_matches_confirmed_metadata=title_match,
            duration_matches_render_package=duration_match,
            views_availability=output.metric_availability.get("views", "UNKNOWN"),
            likes_availability=output.metric_availability.get("likes", "UNKNOWN"),
            comments_availability=output.metric_availability.get("comments", "UNKNOWN"),
            freshness_state=output.freshness_state,
            sync_status=status,
            sync_error_code=None,
            learning_authority="WEAK",
            last_synced_at=utc_now(),
            unknown_metrics=unknown,
            unavailable_metrics=unavailable,
            technical_appendix={
                **output.technical_appendix,
                "source": YOUTUBE_PUBLIC_PROVIDER_KEY,
                "authority": "PUBLIC_MONITOR_WEAK",
                "raw_secrets_exposed": False,
            },
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot


class YouTubeOwnerAnalyticsSyncService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: YouTubeMonitoringConfigService | None = None,
        credential_service: YouTubeOAuthCredentialService | None = None,
        provider: YouTubeOwnerAnalyticsProvider | None = None,
    ):
        self.session = session
        self.config_service = config_service or YouTubeMonitoringConfigService()
        self.credential_service = credential_service or YouTubeOAuthCredentialService(session, config_service=self.config_service)
        self.provider = provider or YouTubeOwnerAnalyticsProvider()

    def sync_uploaded_video(
        self,
        *,
        uploaded_video_id: uuid.UUID,
        request: YouTubeOwnerAnalyticsSyncRequest | None = None,
    ) -> YouTubeOwnerAnalyticsSyncRun:
        uploaded = _require_uploaded_video(self.session, uploaded_video_id)
        _validate_youtube_uploaded(uploaded)
        sync_request = request or YouTubeOwnerAnalyticsSyncRequest()
        start_date = sync_request.start_date or uploaded.published_at.date()
        end_date = sync_request.end_date or utc_now().date()
        run = YouTubeOwnerAnalyticsSyncRun(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            platform_video_id=uploaded.platform_video_id,
            run_state="PENDING",
            source=YOUTUBE_OWNER_PROVIDER_KEY,
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(run)
        self.session.flush()
        if not self.config_service.owner_analytics_enabled():
            run.run_state = "SKIPPED"
            run.completed_at = utc_now()
            run.error_code = "YOUTUBE_OWNER_ANALYTICS_NOT_CONFIGURED"
            run.error_message = "YouTube owner analytics is not configured"
            self.session.flush()
            return run
        reference = self.credential_service.get_connected_owner_reference()
        run.credential_reference_id = reference.id if reference else None
        if reference is None:
            run.run_state = "NEEDS_AUTH"
            run.completed_at = utc_now()
            run.error_code = "YOUTUBE_OWNER_ANALYTICS_NEEDS_AUTH"
            run.error_message = "YouTube OAuth credential is not connected"
            self.session.flush()
            return run
        access_token = self.credential_service.get_valid_access_token(reference)
        if access_token is None:
            run.run_state = "NEEDS_AUTH"
            run.completed_at = utc_now()
            run.error_code = "YOUTUBE_OAUTH_NEEDS_REAUTH"
            run.error_message = "YouTube OAuth credential needs reauthorization"
            self.session.flush()
            return run
        run.run_state = "RUNNING"
        run.started_at = utc_now()
        self.session.flush()
        result = self.provider.fetch(
            platform_video_id=uploaded.platform_video_id,
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
        )
        run.http_status = result.http_status
        if not result.ok or result.output is None:
            run.run_state = "FAILED"
            run.completed_at = utc_now()
            run.error_code = result.error_code or "YOUTUBE_OWNER_ANALYTICS_SYNC_FAILED"
            run.error_message = result.error_message
            self.session.flush()
            return run
        output = YouTubeOwnerAnalyticsProviderOutput.model_validate(result.output)
        snapshot = self._create_owner_snapshot(uploaded=uploaded, output=output)
        m8_metrics = _owner_output_to_m8_metrics(output)
        m8_availability = _owner_availability_to_m8(output.metric_availability)
        analytics = _create_m8_snapshot(
            self.session,
            uploaded=uploaded,
            metrics=m8_metrics,
            explicit_availability=m8_availability,
            sync_mode="YOUTUBE_OWNER_ANALYTICS",
            provider_key="youtube_analytics_api",
            source=YOUTUBE_OWNER_PROVIDER_KEY,
            authority="STRONG",
            source_snapshot_id=snapshot.id,
            freshness_state=output.freshness_state,
            confidence_level="HIGH",
            reason_codes=["YOUTUBE_OWNER_ANALYTICS_SYNC_COMPLETED", "OWNER_ANALYTICS_STRONG_AUTHORITY"],
        )
        run.run_state = "COMPLETED"
        run.completed_at = utc_now()
        run.metrics_found = bool(analytics.metrics_blob)
        run.created_snapshot_id = snapshot.id
        self.session.flush()
        return run

    def latest_snapshot(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubeOwnerAnalyticsSnapshot | None:
        return self.session.scalars(
            select(UploadedVideoYouTubeOwnerAnalyticsSnapshot)
            .where(UploadedVideoYouTubeOwnerAnalyticsSnapshot.uploaded_video_id == uploaded_video_id)
            .order_by(UploadedVideoYouTubeOwnerAnalyticsSnapshot.last_synced_at.desc(), UploadedVideoYouTubeOwnerAnalyticsSnapshot.created_at.desc())
            .limit(1)
        ).one_or_none()

    def _create_owner_snapshot(
        self,
        *,
        uploaded: UploadedVideo,
        output: YouTubeOwnerAnalyticsProviderOutput,
    ) -> UploadedVideoYouTubeOwnerAnalyticsSnapshot:
        unknown = [key for key in OWNER_ANALYTICS_METRICS if output.metric_availability.get(key) == "UNKNOWN"]
        unavailable = [key for key in OWNER_ANALYTICS_METRICS if output.metric_availability.get(key) == "NOT_AVAILABLE"]
        status = "OK" if not unknown and not unavailable else "PARTIAL"
        snapshot = UploadedVideoYouTubeOwnerAnalyticsSnapshot(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            platform_video_id=uploaded.platform_video_id,
            analytics_start_date=output.analytics_start_date,
            analytics_end_date=output.analytics_end_date,
            learning_authority="STRONG",
            views=output.views,
            likes=output.likes,
            comments=output.comments,
            impressions=output.impressions,
            impression_click_through_rate=output.impression_click_through_rate,
            average_view_duration_seconds=output.average_view_duration_seconds,
            average_view_percentage=output.average_view_percentage,
            estimated_minutes_watched=output.estimated_minutes_watched,
            subscribers_gained=output.subscribers_gained,
            subscribers_lost=output.subscribers_lost,
            metric_availability=output.metric_availability,
            freshness_state=output.freshness_state,
            sync_status=status,
            sync_error_code=None,
            last_synced_at=utc_now(),
            technical_appendix={
                **output.technical_appendix,
                "source": YOUTUBE_OWNER_PROVIDER_KEY,
                "authority": "OWNER_ANALYTICS_STRONG",
                "unknown_metrics": unknown,
                "unavailable_metrics": unavailable,
                "raw_secrets_exposed": False,
            },
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot


class UploadedVideoYouTubeFollowReadService:
    def __init__(self, session: Session):
        self.session = session

    def get_summary(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubeFollowSummaryRead:
        uploaded = _require_uploaded_video(self.session, uploaded_video_id)
        public = self._latest_public(uploaded.id)
        owner = self._latest_owner(uploaded.id)
        connection = YouTubeCredentialHealthService(self.session).connection_status()
        unknown = set(public.unknown_metrics if public else PUBLIC_MONITOR_METRICS)
        unavailable = set(public.unavailable_metrics if public else [])
        if owner is None:
            unknown.update(
                [
                    "impressions",
                    "impression_click_through_rate",
                    "average_view_duration_seconds",
                    "average_view_percentage",
                    "estimated_minutes_watched",
                    "subscribers_gained",
                    "subscribers_lost",
                ]
            )
        else:
            for key, item in owner.metric_availability.items():
                if item == "UNKNOWN":
                    unknown.add(key)
                elif item == "NOT_AVAILABLE":
                    unavailable.add(key)
        title = public.youtube_title if public and public.youtube_title else uploaded.actual_metadata.get("actual_title")
        learning_authority = _summary_learning_authority(public, owner)
        return UploadedVideoYouTubeFollowSummaryRead(
            uploaded_video_id=uploaded.id,
            platform_video_id=uploaded.platform_video_id,
            video_url=uploaded.video_url,
            title=title,
            thumbnail_url=public.thumbnail_url if public else _thumbnail_from_uploaded(uploaded),
            published_at=public.youtube_published_at if public and public.youtube_published_at else uploaded.published_at,
            views=owner.views if owner and owner.views is not None else public.views if public else None,
            likes=owner.likes if owner and owner.likes is not None else public.likes if public else None,
            comments=owner.comments if owner and owner.comments is not None else public.comments if public else None,
            public_last_synced_at=public.last_synced_at if public else None,
            public_freshness_state=public.freshness_state if public else "UNKNOWN",
            owner_analytics_connected=connection.owner_analytics_connected,
            impressions=owner.impressions if owner else None,
            impression_click_through_rate=owner.impression_click_through_rate if owner else None,
            average_view_duration_seconds=owner.average_view_duration_seconds if owner else None,
            average_view_percentage=owner.average_view_percentage if owner else None,
            estimated_minutes_watched=owner.estimated_minutes_watched if owner else None,
            subscribers_gained=owner.subscribers_gained if owner else None,
            subscribers_lost=owner.subscribers_lost if owner else None,
            owner_last_synced_at=owner.last_synced_at if owner else None,
            owner_freshness_state=owner.freshness_state if owner else "UNKNOWN",
            title_match_status=_title_match_status(public),
            duration_match_status=_duration_match_status(public),
            caption_status=_dashboard_caption_status(public.caption_status if public else None),
            visibility_status=_visibility_status(public.privacy_status if public else uploaded.actual_metadata.get("actual_privacy_status")),
            learning_authority=learning_authority,
            unavailable_metrics=sorted(unavailable),
            unknown_metrics=sorted(unknown - unavailable),
            next_action=_summary_next_action(public, owner, connection.owner_analytics_connected),
            technical_appendix={
                "public_snapshot_id": str(public.id) if public else None,
                "owner_snapshot_id": str(owner.id) if owner else None,
                "uploaded_video_id": str(uploaded.id),
                "dashboard_ui_deferred_to_m11": True,
            },
        )

    def list_summaries(self) -> list[UploadedVideoYouTubeFollowSummaryRead]:
        uploaded_videos = self.session.scalars(
            select(UploadedVideo).where(UploadedVideo.platform.in_(list(YOUTUBE_PLATFORM_VALUES))).order_by(UploadedVideo.published_at.desc())
        ).all()
        return [self.get_summary(uploaded.id) for uploaded in uploaded_videos]

    def _latest_public(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubePublicMonitorSnapshot | None:
        return YouTubePublicStatsSyncService(self.session).latest_snapshot(uploaded_video_id)

    def _latest_owner(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubeOwnerAnalyticsSnapshot | None:
        return YouTubeOwnerAnalyticsSyncService(self.session).latest_snapshot(uploaded_video_id)


def _create_m8_snapshot(
    session: Session,
    *,
    uploaded: UploadedVideo,
    metrics: dict[str, Any],
    explicit_availability: dict[str, str],
    sync_mode: str,
    provider_key: str,
    source: str,
    authority: str,
    source_snapshot_id: uuid.UUID,
    freshness_state: str,
    confidence_level: str,
    reason_codes: list[str],
) -> AnalyticsSnapshot:
    now = utc_now()
    clean_metrics = {key: value for key, value in metrics.items() if value is not None}
    availability, unknown, unavailable = _build_youtube_metric_availability(
        metrics=clean_metrics,
        explicit_availability=explicit_availability,
        provider_key=provider_key,
        source=source,
    )
    sync_run = AnalyticsSyncRun(
        company_id=uploaded.company_id,
        channel_workspace_id=uploaded.channel_workspace_id,
        uploaded_video_id=uploaded.id,
        video_project_id=uploaded.video_project_id,
        policy_snapshot_id=uploaded.policy_snapshot_id,
        platform=uploaded.platform,
        platform_video_id=uploaded.platform_video_id,
        sync_mode=sync_mode,
        sync_state="COMPLETED",
        started_at=now,
        completed_at=now,
        provider_key=provider_key,
        reason_codes=reason_codes,
        next_action="Use YouTube follow summary for dashboard-ready monitoring.",
        metadata_={"source": source, "authority": authority, "no_scraping": True, "raw_secrets_exposed": False},
    )
    session.add(sync_run)
    session.flush()
    normalized = {
        key: {
            "value": value,
            "unit": METRIC_UNITS.get(key, "UNKNOWN"),
            "source_metric_key": key,
            "source_provider_key": provider_key,
            "source": source,
            "platform": uploaded.platform,
            "captured_at": now.isoformat(),
            "learning_authority": authority,
            "raw_metric_ref": f"metrics_blob.{key}",
        }
        for key, value in sorted(clean_metrics.items())
    }
    snapshot = AnalyticsSnapshot(
        analytics_sync_run_id=sync_run.id,
        uploaded_video_id=uploaded.id,
        company_id=uploaded.company_id,
        channel_workspace_id=uploaded.channel_workspace_id,
        video_project_id=uploaded.video_project_id,
        policy_snapshot_id=uploaded.policy_snapshot_id,
        platform=uploaded.platform,
        platform_video_id=uploaded.platform_video_id,
        captured_at=now,
        observed_from=None,
        observed_to=now,
        observation_window="UNKNOWN",
        metrics_blob=clean_metrics,
        normalized_metrics_blob=normalized,
        metric_availability=availability,
        source_metadata={
            "source": source,
            "provider_key": provider_key,
            "learning_authority": authority,
            "youtube_source_snapshot_id": str(source_snapshot_id),
            "no_scraping": True,
            "no_dashboard_ui": True,
            "raw_payload_not_stored": True,
        },
        freshness_state=freshness_state,
        confidence_level=confidence_level,
        reason_codes=reason_codes,
    )
    session.add(snapshot)
    session.flush()
    availability_snapshot = MetricAvailabilitySnapshot(
        uploaded_video_id=uploaded.id,
        analytics_sync_run_id=sync_run.id,
        platform=uploaded.platform,
        platform_video_id=uploaded.platform_video_id,
        availability_blob=availability,
        unavailable_metrics=unavailable,
        unknown_metrics=unknown,
        source_metric_keys=sorted(clean_metrics),
        freshness_state=freshness_state,
        confidence_level=confidence_level,
        captured_at=now,
    )
    session.add(availability_snapshot)
    sync_run.analytics_snapshot_id = snapshot.id
    _update_m8_summary(
        session,
        uploaded=uploaded,
        snapshot=snapshot,
        availability_snapshot=availability_snapshot,
        authority=authority,
    )
    session.flush()
    return snapshot


def _update_m8_summary(
    session: Session,
    *,
    uploaded: UploadedVideo,
    snapshot: AnalyticsSnapshot,
    availability_snapshot: MetricAvailabilitySnapshot,
    authority: str,
) -> UploadedVideoMetricsSummary:
    summary = session.scalars(
        select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)
    ).one_or_none()
    if summary is None:
        summary = UploadedVideoMetricsSummary(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            video_project_id=uploaded.video_project_id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            freshness_state="UNKNOWN",
            confidence_level="UNKNOWN",
            monitoring_state="NO_DATA_YET",
        )
        session.add(summary)
        session.flush()
    metric_summary = dict(summary.metrics_summary or {})
    for key, item in snapshot.normalized_metrics_blob.items():
        metric_summary[key] = {
            "value": item.get("value"),
            "unit": item.get("unit"),
            "source_metric_key": item.get("source_metric_key"),
            "source": item.get("source"),
            "learning_authority": authority,
            "analytics_snapshot_id": str(snapshot.id),
            "captured_at": snapshot.captured_at.isoformat(),
        }
    availability_summary = dict(summary.availability_summary or {})
    availability_summary.update(
        {
            "availability": availability_snapshot.availability_blob,
            "unknown_metrics": availability_snapshot.unknown_metrics,
            "unavailable_metrics": availability_snapshot.unavailable_metrics,
            "source_metric_keys": availability_snapshot.source_metric_keys,
            "zero_is_available": True,
            "missing_is_not_zero": True,
            "learning_authority": authority,
        }
    )
    summary.latest_analytics_snapshot_id = snapshot.id
    summary.latest_captured_at = snapshot.captured_at
    summary.metrics_summary = metric_summary
    summary.availability_summary = availability_summary
    summary.freshness_state = snapshot.freshness_state
    summary.confidence_level = snapshot.confidence_level
    summary.monitoring_state = "PARTIAL_DATA" if availability_snapshot.unknown_metrics else "SYNCED"
    summary.operator_summary = "YouTube analytics synced successfully" if authority == "STRONG" else "YouTube public stats synced successfully"
    summary.next_action = "Review YouTube follow summary"
    session.flush()
    return summary


def _build_youtube_metric_availability(
    *,
    metrics: dict[str, Any],
    explicit_availability: dict[str, str],
    provider_key: str,
    source: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    availability: dict[str, Any] = {}
    unknown: list[str] = []
    unavailable: list[str] = []
    for metric_key in sorted(KNOWN_ANALYTICS_METRICS):
        explicit = explicit_availability.get(metric_key)
        if metric_key in metrics:
            state = "AVAILABLE"
            reason_code = None
        elif explicit in {"AVAILABLE", "UNKNOWN", "NOT_AVAILABLE"}:
            state = explicit
            reason_code = "YOUTUBE_METRIC_UNKNOWN" if state == "UNKNOWN" else "YOUTUBE_METRIC_UNAVAILABLE" if state == "NOT_AVAILABLE" else None
        elif metric_key in YOUTUBE_NOT_AVAILABLE_METRICS:
            state = "NOT_AVAILABLE"
            reason_code = "YOUTUBE_METRIC_UNAVAILABLE"
        else:
            state = "UNKNOWN"
            reason_code = "YOUTUBE_METRIC_UNKNOWN"
        availability[metric_key] = {
            "state": state,
            "source_metric_key": metric_key if metric_key in metrics else None,
            "reason_code": reason_code,
            "unit": METRIC_UNITS.get(metric_key, "UNKNOWN"),
            "provider_key": provider_key,
            "source": source,
        }
        if state == "UNKNOWN":
            unknown.append(metric_key)
        elif state == "NOT_AVAILABLE":
            unavailable.append(metric_key)
    return availability, unknown, unavailable


def _owner_output_to_m8_metrics(output: YouTubeOwnerAnalyticsProviderOutput) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key in OWNER_ANALYTICS_METRICS:
        value = getattr(output, key)
        if value is None:
            continue
        metrics[OWNER_TO_M8_METRIC_KEYS.get(key, key)] = value
    return metrics


def _owner_availability_to_m8(metric_availability: dict[str, str]) -> dict[str, str]:
    return {OWNER_TO_M8_METRIC_KEYS.get(key, key): value for key, value in metric_availability.items()}


def _require_uploaded_video(session: Session, uploaded_video_id: uuid.UUID) -> UploadedVideo:
    uploaded = session.get(UploadedVideo, uploaded_video_id)
    if uploaded is None:
        raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
    return uploaded


def _validate_youtube_uploaded(uploaded: UploadedVideo) -> None:
    if uploaded.platform not in YOUTUBE_PLATFORM_VALUES:
        raise ValidationFailureError("YouTube follow sync only supports YouTube uploaded videos")
    if uploaded.monitoring_state != "READY_FOR_ANALYTICS":
        raise ValidationFailureError("uploaded video is not ready for analytics")


def _title_matches(uploaded: UploadedVideo, youtube_title: str | None) -> bool | None:
    actual = uploaded.actual_metadata.get("actual_title")
    if not actual or not youtube_title:
        return None
    return str(actual).strip() == youtube_title.strip()


def _duration_matches(session: Session, uploaded: UploadedVideo, youtube_duration_seconds: int | None) -> bool | None:
    if youtube_duration_seconds is None:
        return None
    package = session.get(RenderPackageSnapshot, uploaded.render_package_snapshot_id)
    if package is None or package.duration_seconds is None:
        return None
    return abs(float(package.duration_seconds) - float(youtube_duration_seconds)) <= 1.0


def _title_match_status(public: UploadedVideoYouTubePublicMonitorSnapshot | None) -> str:
    if public is None or public.title_matches_confirmed_metadata is None:
        return "UNKNOWN"
    return "OK" if public.title_matches_confirmed_metadata else "CHANGED"


def _duration_match_status(public: UploadedVideoYouTubePublicMonitorSnapshot | None) -> str:
    if public is None or public.duration_matches_render_package is None:
        return "UNKNOWN"
    return "OK" if public.duration_matches_render_package else "REVIEW"


def _dashboard_caption_status(caption_status: str | None) -> str:
    if caption_status is None:
        return "UNKNOWN"
    normalized = caption_status.upper()
    if normalized in {"TRUE", "AVAILABLE", "CAPTIONED"}:
        return "AVAILABLE"
    if normalized in {"FALSE", "NONE", "NOT_AVAILABLE"}:
        return "NOT_AVAILABLE"
    return "UNKNOWN"


def _visibility_status(value: str | None) -> str:
    normalized = str(value or "").upper()
    if normalized in {"PUBLIC", "UNLISTED", "PRIVATE"}:
        return normalized
    return "UNKNOWN"


def _summary_learning_authority(
    public: UploadedVideoYouTubePublicMonitorSnapshot | None,
    owner: UploadedVideoYouTubeOwnerAnalyticsSnapshot | None,
) -> str:
    if public is not None and owner is not None:
        return "MIXED"
    if owner is not None:
        return "STRONG"
    if public is not None:
        return "WEAK"
    return "NONE"


def _summary_next_action(
    public: UploadedVideoYouTubePublicMonitorSnapshot | None,
    owner: UploadedVideoYouTubeOwnerAnalyticsSnapshot | None,
    owner_connected: bool,
) -> str:
    if public is None:
        return "Run YouTube public sync."
    if not owner_connected:
        return "Connect YouTube OAuth for owner analytics."
    if owner is None:
        return "Run YouTube owner analytics sync."
    if public.freshness_state == "STALE" or owner.freshness_state == "STALE":
        return "Refresh YouTube follow sync."
    return "Monitor in the future M11 dashboard."


def _thumbnail_from_uploaded(uploaded: UploadedVideo) -> str | None:
    ref = uploaded.actual_metadata.get("actual_thumbnail_ref")
    if isinstance(ref, dict):
        return ref.get("url") or ref.get("file_path")
    if isinstance(ref, str):
        return ref
    return None


def _read_oauth_client_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    container = payload.get("web") or payload.get("installed") or payload
    redirect_uris = container.get("redirect_uris") if isinstance(container.get("redirect_uris"), list) else []
    return {
        "client_id": container.get("client_id"),
        "client_secret": container.get("client_secret"),
        "redirect_uri": redirect_uris[0] if redirect_uris else None,
    }


def _post_google_token(payload: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urlrequest.Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlrequest.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_json_secret_file(path: Path, payload: dict[str, Any]) -> None:
    _ensure_no_secret_payload_for_db({"storage": "LOCAL_DEV_FILE", "raw_values_in_db": False})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def _local_file_secret_ref(path: Path) -> str:
    resolved = path.resolve()
    try:
        value = resolved.relative_to(ROOT).as_posix()
    except ValueError:
        value = resolved.as_posix()
    return f"local_file://{value}"


def _ensure_no_secret_payload_for_db(value: Any) -> None:
    for key, item in _walk_items(value):
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS) and normalized not in {"secret_ref"}:
            raise ValidationFailureError(f"secret-like payload key is not allowed: {key}")
        if isinstance(item, str) and any(marker.lower() in item.lower() for marker in RAW_SECRET_MARKERS):
            raise ValidationFailureError("raw secret-like value is not allowed")


def _walk_items(value: Any):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield str(child_key), child_value
            yield from _walk_items(child_value)
    elif isinstance(value, list):
        for child_value in value:
            yield "", child_value
            yield from _walk_items(child_value)


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _minimal_public_debug_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "snippet_keys": sorted((item.get("snippet") or {}).keys()) if isinstance(item.get("snippet"), dict) else [],
        "statistics_keys": sorted((item.get("statistics") or {}).keys()) if isinstance(item.get("statistics"), dict) else [],
        "content_details_keys": sorted((item.get("contentDetails") or {}).keys()) if isinstance(item.get("contentDetails"), dict) else [],
        "status_keys": sorted((item.get("status") or {}).keys()) if isinstance(item.get("status"), dict) else [],
    }


def _best_thumbnail_url(thumbnails: dict[str, Any]) -> str | None:
    for key in ("maxres", "standard", "high", "medium", "default"):
        item = thumbnails.get(key)
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric >= 0 else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric >= 0 else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _expires_at_from_response(token_response: dict[str, Any]) -> datetime | None:
    expires_in = token_response.get("expires_in")
    if expires_in is None:
        return None
    try:
        return utc_now() + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def _parse_iso8601_duration_seconds(value: Any) -> int | None:
    if not value or not isinstance(value, str) or not value.startswith("P"):
        return None
    # YouTube returns ISO 8601 durations such as PT12M34S. Years/months are not valid for video durations here.
    text = value.removeprefix("P")
    if not text.startswith("T"):
        return None
    text = text.removeprefix("T")
    total = 0
    current = ""
    multipliers = {"H": 3600, "M": 60, "S": 1}
    for char in text:
        if char.isdigit() or char == ".":
            current += char
            continue
        if char not in multipliers or not current:
            return None
        total += int(float(current) * multipliers[char])
        current = ""
    return total


def _youtube_http_error_code(http_status: int) -> str:
    if http_status in {401, 403, 429}:
        return "YOUTUBE_API_QUOTA_OR_AUTH_ERROR"
    if http_status == 404:
        return "YOUTUBE_VIDEO_NOT_FOUND"
    return "YOUTUBE_API_ERROR"
