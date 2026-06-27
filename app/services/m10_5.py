from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import urllib.error
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib import request as urlrequest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m10_5 import (
    CloudMediaReadPayload,
    LocalCleanupRunResult,
    MediaOffloadExecuteRequest,
    MediaOffloadJobCreate,
    GoogleDriveConnectionStatusRead,
    GoogleDriveOAuthStartResult,
)
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    CloudMediaRef,
    CredentialReference,
    FinalMediaRef,
    GoogleDriveMediaCredential,
    GoogleDriveOAuthSession,
    LocalMediaRetentionPolicy,
    MediaOffloadJob,
)

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DRIVE_CREDENTIAL_DIR = ROOT / "var" / "credentials" / "google-drive"
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
GOOGLE_DRIVE_PROVIDER_KEY = "google_drive"
GOOGLE_DRIVE_CREDENTIAL_KEY = "media_offload_default"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
RESUMABLE_THRESHOLD_BYTES = 5 * 1024 * 1024
DEFAULT_ALLOWED_CLEANUP_ROOTS = (
    ROOT / "var" / "tmp" / "rendering",
    ROOT / "var" / "tmp" / "upload-staging",
    ROOT / "var" / "generated",
)

SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
PUBLIC_FORBIDDEN_KEY_FRAGMENTS = {"local_source_path", "absolute_path", "download", "preview", "web_content_link", *SECRET_KEY_FRAGMENTS}


@dataclass(frozen=True)
class GoogleDriveUploadResult:
    drive_file_id: str
    drive_folder_id: str | None
    web_view_link: str
    file_name: str | None
    mime_type: str | None
    size_bytes: int | None
    checksum_sha256: str | None = None
    upload_mode: str | None = None
    technical_appendix: dict[str, Any] | None = None


@dataclass(frozen=True)
class GoogleDriveVerificationResult:
    ok: bool
    verification_status: str
    reason_code: str
    size_verified: bool
    checksum_verified: bool
    checksum_unavailable: bool
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


class GoogleDriveConfigService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def scopes(self) -> list[str]:
        raw = self.settings.google_drive_oauth_scopes.replace(",", " ")
        scopes = [item.strip() for item in raw.split() if item.strip()]
        if not scopes:
            return [GOOGLE_DRIVE_SCOPE]
        if GOOGLE_DRIVE_SCOPE not in scopes or any(scope == "https://www.googleapis.com/auth/drive" for scope in scopes):
            raise ValidationFailureError("Google Drive upload must use the drive.file OAuth scope")
        return scopes

    def offload_enabled(self) -> bool:
        return bool(self.settings.google_drive_offload_enabled)

    def root_folder_id(self) -> str | None:
        return self.settings.google_drive_root_folder_id

    def upload_mode(self) -> str:
        mode = (self.settings.google_drive_upload_mode or "resumable").lower()
        return mode if mode in {"resumable", "multipart"} else "resumable"

    def oauth_client_config(self) -> dict[str, str]:
        config = self._oauth_client_config_or_none()
        if config is None:
            raise ValidationFailureError("Google Drive OAuth client is not configured")
        return config

    def oauth_configured(self) -> bool:
        try:
            return self._oauth_client_config_or_none() is not None and bool(self.scopes)
        except ValidationFailureError:
            return False

    def safe_status(self) -> dict[str, Any]:
        oauth_configured = self.oauth_configured()
        return {
            "offload_enabled": self.offload_enabled(),
            "config_state": "CONFIGURED" if self.offload_enabled() and oauth_configured and self.root_folder_id() else "NOT_CONFIGURED",
            "scopes": self.scopes if oauth_configured else [],
            "root_folder_id_configured": bool(self.root_folder_id()),
            "upload_mode": self.upload_mode(),
            "secret_values_exposed": False,
        }

    def _oauth_client_config_or_none(self) -> dict[str, str] | None:
        client_id = self.settings.google_drive_oauth_client_id
        client_secret = self.settings.google_drive_oauth_client_secret.get_secret_value() if self.settings.google_drive_oauth_client_secret else None
        redirect_uri = self.settings.google_drive_oauth_redirect_uri
        if self.settings.google_drive_oauth_client_secrets_file:
            file_config = _read_oauth_client_file(Path(self.settings.google_drive_oauth_client_secrets_file))
            client_id = client_id or file_config.get("client_id")
            client_secret = client_secret or file_config.get("client_secret")
            redirect_uri = redirect_uri or file_config.get("redirect_uri")
        if not client_id or not client_secret or not redirect_uri:
            return None
        return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}


class GoogleDriveOAuthCredentialService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: GoogleDriveConfigService | None = None,
        token_exchanger: TokenExchanger | None = None,
        credential_dir: Path | None = None,
    ):
        self.session = session
        self.config_service = config_service or GoogleDriveConfigService()
        self.token_exchanger = token_exchanger or GoogleOAuthTokenExchanger()
        self.credential_dir = credential_dir or LOCAL_DRIVE_CREDENTIAL_DIR

    def store_token_response(
        self,
        *,
        token_response: dict[str, Any],
        scopes: list[str],
        company_id: Any | None,
        channel_workspace_id: Any | None,
    ) -> CredentialReference:
        refresh_value = token_response.get("refresh_token")
        access_value = token_response.get("access_token")
        if not refresh_value or not access_value:
            raise ValidationFailureError("Google Drive OAuth requires refresh_token and access_token")
        expires_at = _expires_at_from_response(token_response)
        credential_id = _new_uuid()
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
        self._upsert_drive_credential(
            credential_reference_id=reference.id,
            scopes=scopes,
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            state="CONNECTED",
            error_code=None,
            error_message=None,
        )
        return reference

    def exchange_authorization_code(
        self,
        *,
        code: str,
        scopes: list[str],
        company_id: Any | None,
        channel_workspace_id: Any | None,
    ) -> CredentialReference:
        token_response = self.token_exchanger.exchange_code(
            code=code,
            client_config=self.config_service.oauth_client_config(),
            scopes=scopes,
        )
        return self.store_token_response(
            token_response=token_response,
            scopes=scopes,
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
        )

    def get_connected_reference(self) -> CredentialReference | None:
        credential = self.session.scalars(
            select(GoogleDriveMediaCredential)
            .where(GoogleDriveMediaCredential.connection_state == "CONNECTED")
            .order_by(GoogleDriveMediaCredential.updated_at.desc())
            .limit(1)
        ).one_or_none()
        if credential is None:
            return None
        return self.session.get(CredentialReference, credential.credential_reference_id)

    def get_valid_access_token(self, reference: CredentialReference) -> str | None:
        if reference.status in {"MISSING", "REVOKED", "DISABLED"}:
            self._mark_drive_state(reference.id, "NEEDS_REAUTH", "GOOGLE_DRIVE_NEEDS_REAUTH", "credential is not usable")
            return None
        payload = self._read_token_payload(reference)
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_at = _parse_datetime(payload.get("expires_at"))
        if expires_at is not None and expires_at <= utc_now() + timedelta(seconds=60):
            if not refresh_token:
                self._mark_drive_state(reference.id, "NEEDS_REAUTH", "GOOGLE_DRIVE_NEEDS_REAUTH", "refresh token missing")
                return None
            try:
                refreshed = self.token_exchanger.refresh_access_token(
                    refresh_token=str(refresh_token),
                    client_config=self.config_service.oauth_client_config(),
                )
            except Exception:
                self._mark_drive_state(reference.id, "NEEDS_REAUTH", "GOOGLE_DRIVE_NEEDS_REAUTH", "token refresh failed")
                return None
            access_token = refreshed.get("access_token")
            if not access_token:
                self._mark_drive_state(reference.id, "NEEDS_REAUTH", "GOOGLE_DRIVE_NEEDS_REAUTH", "token refresh failed")
                return None
            expires_at = _expires_at_from_response(refreshed)
            payload["access_token"] = access_token
            payload["expires_at"] = expires_at.isoformat() if expires_at else None
            _write_json_secret_file(self._path_from_secret_ref(reference.secret_ref), payload)
            reference.expires_at = expires_at
            reference.status = "CONFIGURED"
            reference.metadata_ = {"storage": "LOCAL_DEV_FILE", "raw_values_in_db": False, "last_refresh_at": utc_now().isoformat()}
            self._mark_drive_state(reference.id, "CONNECTED", None, None)
            self.session.flush()
        if not access_token:
            self._mark_drive_state(reference.id, "NEEDS_REAUTH", "GOOGLE_DRIVE_NEEDS_REAUTH", "access token missing")
            return None
        return str(access_token)

    def _upsert_credential_reference(
        self,
        *,
        credential_id: Any,
        secret_ref: str,
        scopes: list[str],
        expires_at: datetime | None,
    ) -> CredentialReference:
        existing = self.session.scalars(
            select(CredentialReference).where(
                CredentialReference.provider_key == GOOGLE_DRIVE_PROVIDER_KEY,
                CredentialReference.credential_key == GOOGLE_DRIVE_CREDENTIAL_KEY,
            )
        ).one_or_none()
        if existing is None:
            existing = CredentialReference(
                id=credential_id,
                provider_key=GOOGLE_DRIVE_PROVIDER_KEY,
                credential_key=GOOGLE_DRIVE_CREDENTIAL_KEY,
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

    def _upsert_drive_credential(
        self,
        *,
        credential_reference_id: Any,
        scopes: list[str],
        company_id: Any | None,
        channel_workspace_id: Any | None,
        state: str,
        error_code: str | None,
        error_message: str | None,
    ) -> GoogleDriveMediaCredential:
        existing = self.session.scalars(
            select(GoogleDriveMediaCredential).where(GoogleDriveMediaCredential.credential_reference_id == credential_reference_id)
        ).one_or_none()
        if existing is None:
            existing = GoogleDriveMediaCredential(
                company_id=company_id,
                channel_workspace_id=channel_workspace_id,
                credential_reference_id=credential_reference_id,
                connection_state=state,
                scopes=scopes,
                root_folder_id=self.config_service.root_folder_id(),
                last_health_check_at=utc_now(),
                error_code=error_code,
                error_message=error_message,
            )
            self.session.add(existing)
        else:
            existing.company_id = company_id
            existing.channel_workspace_id = channel_workspace_id
            existing.connection_state = state
            existing.scopes = scopes
            existing.root_folder_id = self.config_service.root_folder_id()
            existing.last_health_check_at = utc_now()
            existing.error_code = error_code
            existing.error_message = error_message
        self.session.flush()
        return existing

    def _mark_drive_state(self, credential_reference_id: Any, state: str, error_code: str | None, error_message: str | None) -> None:
        credential = self.session.scalars(
            select(GoogleDriveMediaCredential).where(GoogleDriveMediaCredential.credential_reference_id == credential_reference_id)
        ).one_or_none()
        if credential is None:
            return
        credential.connection_state = state
        credential.last_health_check_at = utc_now()
        credential.error_code = error_code
        credential.error_message = error_message
        self.session.flush()

    def _read_token_payload(self, reference: CredentialReference) -> dict[str, Any]:
        path = self._path_from_secret_ref(reference.secret_ref)
        if not path.exists():
            self._mark_drive_state(reference.id, "NEEDS_REAUTH", "GOOGLE_DRIVE_NEEDS_REAUTH", "local token file missing")
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _path_from_secret_ref(self, secret_ref: str | None) -> Path:
        if not secret_ref or not secret_ref.startswith("local_file://"):
            raise ValidationFailureError("credential reference does not point to local dev token storage")
        value = secret_ref.removeprefix("local_file://")
        path = Path(value)
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()

    def _token_storage_path(self, credential_reference_id: Any) -> Path:
        return self.credential_dir / "oauth" / f"{credential_reference_id}.json"


class GoogleDriveOAuthSessionService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: GoogleDriveConfigService | None = None,
        credential_service: GoogleDriveOAuthCredentialService | None = None,
    ):
        self.session = session
        self.config_service = config_service or GoogleDriveConfigService()
        self.credential_service = credential_service or GoogleDriveOAuthCredentialService(session, config_service=self.config_service)

    def start(self, *, company_id: Any | None = None, channel_workspace_id: Any | None = None) -> GoogleDriveOAuthStartResult:
        if not self.config_service.oauth_configured():
            raise ValidationFailureError("Google Drive OAuth is not configured")
        client_config = self.config_service.oauth_client_config()
        state_token = secrets.token_urlsafe(32)
        scopes = self.config_service.scopes
        oauth_session = GoogleDriveOAuthSession(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            state_token_hash=_hash_state(state_token),
            redirect_uri=client_config["redirect_uri"],
            scopes=scopes,
            status="STARTED",
        )
        self.session.add(oauth_session)
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
        return GoogleDriveOAuthStartResult(
            oauth_session_id=oauth_session.id,
            authorization_url=f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}",
        )

    def handle_callback(self, *, state: str, code: str | None = None, error: str | None = None) -> GoogleDriveOAuthSession:
        oauth_session = self._require_session_for_state(state)
        oauth_session.status = "CALLBACK_RECEIVED"
        self.session.flush()
        if error:
            oauth_session.status = "FAILED"
            oauth_session.error_code = "GOOGLE_DRIVE_OAUTH_CALLBACK_RECEIVED"
            oauth_session.error_message = "OAuth callback returned an error"
            self.session.flush()
            return oauth_session
        if not code:
            oauth_session.status = "FAILED"
            oauth_session.error_code = "GOOGLE_DRIVE_OAUTH_CALLBACK_RECEIVED"
            oauth_session.error_message = "OAuth callback missing authorization code"
            self.session.flush()
            return oauth_session
        try:
            reference = self.credential_service.exchange_authorization_code(
                code=code,
                scopes=oauth_session.scopes,
                company_id=oauth_session.company_id,
                channel_workspace_id=oauth_session.channel_workspace_id,
            )
        except Exception:
            oauth_session.status = "FAILED"
            oauth_session.error_code = "GOOGLE_DRIVE_NEEDS_REAUTH"
            oauth_session.error_message = "OAuth token exchange failed"
            self.session.flush()
            return oauth_session
        oauth_session.status = "TOKEN_EXCHANGED"
        oauth_session.credential_reference_id = reference.id
        oauth_session.error_code = None
        oauth_session.error_message = None
        self.session.flush()
        return oauth_session

    def _require_session_for_state(self, state: str) -> GoogleDriveOAuthSession:
        oauth_session = self.session.scalars(
            select(GoogleDriveOAuthSession)
            .where(GoogleDriveOAuthSession.state_token_hash == _hash_state(state))
            .order_by(GoogleDriveOAuthSession.created_at.desc())
            .limit(1)
        ).one_or_none()
        if oauth_session is None:
            raise ValidationFailureError("invalid Google Drive OAuth state")
        return oauth_session


class GoogleDriveCredentialHealthService:
    def __init__(self, session: Session, *, config_service: GoogleDriveConfigService | None = None):
        self.session = session
        self.config_service = config_service or GoogleDriveConfigService()

    def connection_status(self) -> GoogleDriveConnectionStatusRead:
        config = self.config_service.safe_status()
        credential = self.session.scalars(
            select(GoogleDriveMediaCredential).order_by(GoogleDriveMediaCredential.updated_at.desc()).limit(1)
        ).one_or_none()
        connection_state = credential.connection_state if credential else config["config_state"]
        reasons: list[str] = []
        if config["offload_enabled"]:
            reasons.append("GOOGLE_DRIVE_OFFLOAD_CONFIGURED")
        if credential and credential.connection_state == "CONNECTED":
            reasons.append("GOOGLE_DRIVE_TOKEN_EXCHANGED")
        if not config["root_folder_id_configured"]:
            reasons.append("GOOGLE_DRIVE_ROOT_FOLDER_MISSING")
        next_action = None
        if not config["offload_enabled"]:
            next_action = "Set GOOGLE_DRIVE_OFFLOAD_ENABLED=true to enable Drive offload."
        elif not config["root_folder_id_configured"]:
            next_action = "Configure GOOGLE_DRIVE_ROOT_FOLDER_ID before real upload."
        elif connection_state != "CONNECTED":
            next_action = "Connect Google Drive OAuth before offload."
        return GoogleDriveConnectionStatusRead(
            offload_enabled=config["offload_enabled"],
            config_state=config["config_state"],
            connection_state=connection_state,
            connected=connection_state == "CONNECTED",
            credential_reference_id=credential.credential_reference_id if credential and credential.connection_state == "CONNECTED" else None,
            root_folder_id_configured=config["root_folder_id_configured"],
            scopes=config["scopes"],
            upload_mode=config["upload_mode"],
            reason_codes=reasons,
            next_action=next_action,
        )


class GoogleDriveMediaStorageProvider:
    def choose_upload_mode(self, *, size_bytes: int, configured_mode: str) -> str:
        if size_bytes > RESUMABLE_THRESHOLD_BYTES:
            return "resumable"
        return "multipart" if configured_mode == "multipart" else "resumable"

    def ensure_folder_path(self, *, access_token: str, root_folder_id: str, folder_path: list[str]) -> str:
        parent_id = root_folder_id
        for folder_name in folder_path:
            parent_id = self._ensure_child_folder(access_token=access_token, parent_id=parent_id, folder_name=folder_name)
        return parent_id

    def upload_file(
        self,
        *,
        access_token: str,
        local_path: Path,
        folder_id: str,
        upload_mode: str,
        mime_type: str | None,
    ) -> GoogleDriveUploadResult:
        size_bytes = local_path.stat().st_size
        mode = self.choose_upload_mode(size_bytes=size_bytes, configured_mode=upload_mode)
        if mode == "multipart":
            return self._multipart_upload(
                access_token=access_token,
                local_path=local_path,
                folder_id=folder_id,
                mime_type=mime_type,
            )
        return self._resumable_upload(
            access_token=access_token,
            local_path=local_path,
            folder_id=folder_id,
            mime_type=mime_type,
        )

    def get_file_metadata(self, *, access_token: str, drive_file_id: str) -> GoogleDriveUploadResult:
        query = urllib.parse.urlencode({"fields": "id,name,size,mimeType,webViewLink,parents"})
        request = urlrequest.Request(
            f"{GOOGLE_DRIVE_FILES_URL}/{urllib.parse.quote(drive_file_id)}?{query}",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urlrequest.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return _drive_result_from_payload(payload, upload_mode=None)

    def _ensure_child_folder(self, *, access_token: str, parent_id: str, folder_name: str) -> str:
        escaped_name = folder_name.replace("'", "\\'")
        query = urllib.parse.urlencode(
            {
                "q": f"name='{escaped_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false",
                "fields": "files(id,name)",
                "spaces": "drive",
            }
        )
        request = urlrequest.Request(
            f"{GOOGLE_DRIVE_FILES_URL}?{query}",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urlrequest.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        if files:
            return str(files[0]["id"])
        body = json.dumps(
            {"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
        ).encode("utf-8")
        create = urlrequest.Request(
            f"{GOOGLE_DRIVE_FILES_URL}?fields=id,name",
            method="POST",
            data=body,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"},
        )
        with urlrequest.urlopen(create, timeout=20) as response:
            created = json.loads(response.read().decode("utf-8"))
        return str(created["id"])

    def _multipart_upload(self, *, access_token: str, local_path: Path, folder_id: str, mime_type: str | None) -> GoogleDriveUploadResult:
        media_type = mime_type or "application/octet-stream"
        boundary = f"vcos-{secrets.token_hex(12)}"
        metadata = {"name": local_path.name, "parents": [folder_id]}
        body = b"\r\n".join(
            [
                f"--{boundary}".encode(),
                b"Content-Type: application/json; charset=UTF-8",
                b"",
                json.dumps(metadata).encode("utf-8"),
                f"--{boundary}".encode(),
                f"Content-Type: {media_type}".encode(),
                b"",
                local_path.read_bytes(),
                f"--{boundary}--".encode(),
                b"",
            ]
        )
        query = urllib.parse.urlencode({"uploadType": "multipart", "fields": "id,name,size,mimeType,webViewLink,parents"})
        request = urlrequest.Request(
            f"{GOOGLE_DRIVE_UPLOAD_URL}?{query}",
            method="POST",
            data=body,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}"},
        )
        with urlrequest.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return _drive_result_from_payload(payload, upload_mode="multipart")

    def _resumable_upload(self, *, access_token: str, local_path: Path, folder_id: str, mime_type: str | None) -> GoogleDriveUploadResult:
        media_type = mime_type or "application/octet-stream"
        metadata = json.dumps({"name": local_path.name, "parents": [folder_id]}).encode("utf-8")
        query = urllib.parse.urlencode({"uploadType": "resumable", "fields": "id,name,size,mimeType,webViewLink,parents"})
        init = urlrequest.Request(
            f"{GOOGLE_DRIVE_UPLOAD_URL}?{query}",
            method="POST",
            data=metadata,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": media_type,
                "X-Upload-Content-Length": str(local_path.stat().st_size),
            },
        )
        with urlrequest.urlopen(init, timeout=20) as response:
            location = response.headers.get("Location")
        if not location:
            raise ValidationFailureError("Google Drive resumable upload session did not return a location")
        data = local_path.read_bytes()
        upload = urlrequest.Request(
            location,
            method="PUT",
            data=data,
            headers={"Content-Type": media_type, "Content-Length": str(len(data))},
        )
        with urlrequest.urlopen(upload, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return _drive_result_from_payload(payload, upload_mode="resumable")


class GoogleDriveUploadVerifier:
    def verify(
        self,
        *,
        upload_result: GoogleDriveUploadResult,
        local_size_bytes: int,
        local_sha256: str,
    ) -> GoogleDriveVerificationResult:
        if not upload_result.drive_file_id or not upload_result.web_view_link:
            return GoogleDriveVerificationResult(False, "FAILED", "MEDIA_OFFLOAD_UPLOAD_FAILED", False, False, False, "missing Drive file id or web_view_link")
        size_verified = upload_result.size_bytes == local_size_bytes if upload_result.size_bytes is not None else False
        if not size_verified:
            return GoogleDriveVerificationResult(False, "FAILED", "MEDIA_OFFLOAD_UPLOAD_FAILED", False, False, False, "Drive size verification failed")
        if upload_result.checksum_sha256:
            checksum_verified = upload_result.checksum_sha256 == local_sha256
            return GoogleDriveVerificationResult(
                checksum_verified,
                "CHECKSUM_VERIFIED" if checksum_verified else "FAILED",
                "MEDIA_OFFLOAD_UPLOAD_VERIFIED" if checksum_verified else "MEDIA_OFFLOAD_UPLOAD_FAILED",
                True,
                checksum_verified,
                False,
                None if checksum_verified else "Drive checksum verification failed",
            )
        return GoogleDriveVerificationResult(True, "CHECKSUM_UNAVAILABLE", "MEDIA_OFFLOAD_UPLOAD_VERIFIED", True, False, True)


class CloudMediaRefService:
    def __init__(self, session: Session):
        self.session = session

    def create_verified_ref(
        self,
        *,
        company_id: Any | None,
        channel_workspace_id: Any | None,
        video_project_id: Any | None,
        uploaded_video_id: Any | None,
        render_package_id: Any | None,
        media_type: str,
        upload_result: GoogleDriveUploadResult,
        verification: GoogleDriveVerificationResult,
        local_source_path_hash: str | None,
        checksum_sha256: str | None,
        source_refs: list[dict[str, Any]] | None = None,
        retention_policy: dict[str, Any] | None = None,
    ) -> CloudMediaRef:
        if not verification.ok:
            raise ValidationFailureError("CloudMediaRef can only be created after verified Drive upload")
        ref = CloudMediaRef(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            uploaded_video_id=uploaded_video_id,
            render_package_id=render_package_id,
            media_type=media_type,
            storage_provider="GOOGLE_DRIVE",
            drive_file_id=upload_result.drive_file_id,
            drive_folder_id=upload_result.drive_folder_id,
            web_view_link=upload_result.web_view_link,
            mime_type=upload_result.mime_type,
            file_name=upload_result.file_name,
            size_bytes=upload_result.size_bytes,
            checksum_sha256=checksum_sha256,
            local_source_path_hash=local_source_path_hash,
            upload_status="VERIFIED",
            verification_status=verification.verification_status,
            local_cleanup_status="PENDING" if retention_policy and retention_policy.get("cleanup_after_verified") else "NOT_ELIGIBLE",
            uploaded_at=utc_now(),
            retention_policy=retention_policy or {},
            source_refs=source_refs or [],
            technical_appendix=_sanitize_public_payload(
                {
                    **(upload_result.technical_appendix or {}),
                    "drive_file_id_verified": True,
                    "size_verified": verification.size_verified,
                    "checksum_verified": verification.checksum_verified,
                    "checksum_unavailable": verification.checksum_unavailable,
                    "dashboard_drive_cta_only": True,
                    "backend_download_proxy": False,
                    "backend_preview_proxy": False,
                }
            ),
        )
        self.session.add(ref)
        self.session.flush()
        return ref

    def require(self, cloud_media_ref_id: Any) -> CloudMediaRef:
        ref = self.session.get(CloudMediaRef, cloud_media_ref_id)
        if ref is None:
            raise NotFoundError(f"cloud media ref not found: {cloud_media_ref_id}")
        return ref

    def dashboard_payload(self, ref: CloudMediaRef) -> CloudMediaReadPayload:
        return CloudMediaReadPayload(
            cloud_media_ref_id=ref.id,
            media_type=ref.media_type,
            file_name=ref.file_name,
            storage_provider="GOOGLE_DRIVE",
            web_view_link=ref.web_view_link,
            upload_status=ref.upload_status,
            verification_status=ref.verification_status,
            local_cleanup_status=ref.local_cleanup_status,
            size_bytes=ref.size_bytes,
            mime_type=ref.mime_type,
            uploaded_at=ref.uploaded_at,
            cleaned_at=ref.cleaned_at,
            source_refs=ref.source_refs,
            technical_appendix=_sanitize_public_payload(ref.technical_appendix),
        )


class LocalMediaRetentionPolicyService:
    def __init__(self, session: Session, *, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def get_or_create_default(
        self,
        *,
        company_id: Any | None = None,
        channel_workspace_id: Any | None = None,
    ) -> LocalMediaRetentionPolicy:
        policy = self.session.scalars(
            select(LocalMediaRetentionPolicy)
            .where(LocalMediaRetentionPolicy.company_id.is_(None) if company_id is None else LocalMediaRetentionPolicy.company_id == company_id)
            .where(
                LocalMediaRetentionPolicy.channel_workspace_id.is_(None)
                if channel_workspace_id is None
                else LocalMediaRetentionPolicy.channel_workspace_id == channel_workspace_id
            )
            .order_by(LocalMediaRetentionPolicy.updated_at.desc())
            .limit(1)
        ).one_or_none()
        if policy is not None:
            return policy
        policy = LocalMediaRetentionPolicy(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            keep_local_after_upload=False,
            cleanup_after_verified=bool(self.settings.delete_local_after_drive_upload),
            max_local_age_hours=self.settings.local_media_max_age_hours,
            max_local_storage_gb=self.settings.local_media_max_storage_gb,
            protected_paths=[],
            allowed_cleanup_roots=[str(path.resolve()) for path in DEFAULT_ALLOWED_CLEANUP_ROOTS],
            state="ACTIVE",
        )
        self.session.add(policy)
        self.session.flush()
        return policy

    def retention_blob(self, policy: LocalMediaRetentionPolicy, *, keep_local: bool) -> dict[str, Any]:
        return {
            "keep_local_after_upload": bool(policy.keep_local_after_upload or keep_local),
            "cleanup_after_verified": bool(policy.cleanup_after_verified),
            "max_local_age_hours": policy.max_local_age_hours,
            "max_local_storage_gb": policy.max_local_storage_gb,
            "allowed_cleanup_roots_count": len(policy.allowed_cleanup_roots or []),
            "protected_paths_count": len(policy.protected_paths or []),
        }


class LocalMediaCleanupService:
    def __init__(self, session: Session):
        self.session = session

    def cleanup_verified_ref(
        self,
        *,
        cloud_ref: CloudMediaRef,
        local_path: Path,
        policy: LocalMediaRetentionPolicy,
        keep_local: bool,
        current_job_id: Any | None = None,
        dry_run: bool = False,
    ) -> str:
        if cloud_ref.upload_status != "VERIFIED" or cloud_ref.verification_status not in {"SIZE_VERIFIED", "CHECKSUM_VERIFIED", "CHECKSUM_UNAVAILABLE"}:
            cloud_ref.local_cleanup_status = "SKIPPED"
            cloud_ref.technical_appendix = {**(cloud_ref.technical_appendix or {}), "cleanup_reason": "LOCAL_MEDIA_NOT_DELETED_UPLOAD_UNVERIFIED"}
            self.session.flush()
            return "LOCAL_MEDIA_NOT_DELETED_UPLOAD_UNVERIFIED"
        if keep_local or policy.keep_local_after_upload or not policy.cleanup_after_verified:
            cloud_ref.local_cleanup_status = "SKIPPED"
            self.session.flush()
            return "LOCAL_MEDIA_CLEANUP_SKIPPED"
        resolved = local_path.resolve()
        if not _path_is_under_allowed_root(resolved, [Path(item) for item in policy.allowed_cleanup_roots or []]):
            cloud_ref.local_cleanup_status = "SKIPPED"
            cloud_ref.technical_appendix = {**(cloud_ref.technical_appendix or {}), "cleanup_reason": "LOCAL_MEDIA_NOT_DELETED_PROTECTED_PATH"}
            self.session.flush()
            return "LOCAL_MEDIA_NOT_DELETED_PROTECTED_PATH"
        if _path_is_under_allowed_root(resolved, [Path(item) for item in policy.protected_paths or []]):
            cloud_ref.local_cleanup_status = "SKIPPED"
            cloud_ref.technical_appendix = {**(cloud_ref.technical_appendix or {}), "cleanup_reason": "LOCAL_MEDIA_NOT_DELETED_PROTECTED_PATH"}
            self.session.flush()
            return "LOCAL_MEDIA_NOT_DELETED_PROTECTED_PATH"
        if self._has_active_job(cloud_ref.local_source_path_hash, current_job_id=current_job_id):
            cloud_ref.local_cleanup_status = "SKIPPED"
            self.session.flush()
            return "LOCAL_MEDIA_CLEANUP_SKIPPED"
        if dry_run:
            cloud_ref.local_cleanup_status = "PENDING"
            self.session.flush()
            return "LOCAL_MEDIA_CLEANUP_SKIPPED"
        try:
            if resolved.exists() and resolved.is_file():
                resolved.unlink()
            cloud_ref.local_cleanup_status = "CLEANED"
            cloud_ref.cleaned_at = utc_now()
            self.session.flush()
            return "LOCAL_MEDIA_CLEANUP_COMPLETED"
        except Exception:
            cloud_ref.local_cleanup_status = "FAILED"
            self.session.flush()
            return "LOCAL_MEDIA_CLEANUP_FAILED"

    def run_pending_cleanup(self, *, dry_run: bool = False) -> LocalCleanupRunResult:
        refs = list(
            self.session.scalars(
                select(CloudMediaRef).where(CloudMediaRef.local_cleanup_status == "PENDING").order_by(CloudMediaRef.created_at.asc())
            )
        )
        skipped = len(refs)
        reason_codes = ["LOCAL_MEDIA_CLEANUP_SKIPPED"] if refs else []
        return LocalCleanupRunResult(scanned=len(refs), cleaned=0, skipped=skipped, failed=0, reason_codes=reason_codes)

    def _has_active_job(self, local_source_path_hash: str | None, *, current_job_id: Any | None) -> bool:
        if not local_source_path_hash:
            return False
        jobs = list(
            self.session.scalars(
                select(MediaOffloadJob)
                .where(MediaOffloadJob.local_source_path_hash == local_source_path_hash)
                .where(MediaOffloadJob.job_state.in_(["PENDING", "UPLOADING"]))
            )
        )
        return any(job.id != current_job_id for job in jobs)


class GoogleDriveUploadService:
    def __init__(
        self,
        session: Session,
        *,
        config_service: GoogleDriveConfigService | None = None,
        credential_service: GoogleDriveOAuthCredentialService | None = None,
        provider: GoogleDriveMediaStorageProvider | None = None,
        verifier: GoogleDriveUploadVerifier | None = None,
    ):
        self.session = session
        self.config_service = config_service or GoogleDriveConfigService()
        self.credential_service = credential_service or GoogleDriveOAuthCredentialService(session, config_service=self.config_service)
        self.provider = provider or GoogleDriveMediaStorageProvider()
        self.verifier = verifier or GoogleDriveUploadVerifier()

    def upload_verified(
        self,
        *,
        local_path: Path,
        media_type: str,
        company_id: Any | None,
        channel_workspace_id: Any | None,
        video_project_id: Any | None,
        uploaded_video_id: Any | None,
        render_package_id: Any | None,
        source_refs: list[dict[str, Any]],
        retention_policy: dict[str, Any],
    ) -> tuple[CloudMediaRef, GoogleDriveVerificationResult]:
        if not self.config_service.offload_enabled():
            raise ValidationFailureError("Google Drive offload is disabled")
        root_folder_id = self.config_service.root_folder_id()
        if not root_folder_id:
            raise ValidationFailureError("GOOGLE_DRIVE_ROOT_FOLDER_ID is required for real upload")
        reference = self.credential_service.get_connected_reference()
        if reference is None:
            raise ValidationFailureError("Google Drive OAuth credential is not connected")
        access_token = self.credential_service.get_valid_access_token(reference)
        if not access_token:
            raise ValidationFailureError("Google Drive OAuth credential needs reauthorization")
        local_size = local_path.stat().st_size
        local_sha256 = _sha256_file(local_path)
        folder_path = _default_drive_folder_path(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            media_type=media_type,
        )
        folder_id = self.provider.ensure_folder_path(access_token=access_token, root_folder_id=root_folder_id, folder_path=folder_path)
        mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        upload_result = self.provider.upload_file(
            access_token=access_token,
            local_path=local_path,
            folder_id=folder_id,
            upload_mode=self.config_service.upload_mode(),
            mime_type=mime_type,
        )
        metadata = self.provider.get_file_metadata(access_token=access_token, drive_file_id=upload_result.drive_file_id)
        upload_result = GoogleDriveUploadResult(
            drive_file_id=metadata.drive_file_id or upload_result.drive_file_id,
            drive_folder_id=metadata.drive_folder_id or upload_result.drive_folder_id or folder_id,
            web_view_link=metadata.web_view_link or upload_result.web_view_link,
            file_name=metadata.file_name or upload_result.file_name or local_path.name,
            mime_type=metadata.mime_type or upload_result.mime_type or mime_type,
            size_bytes=metadata.size_bytes if metadata.size_bytes is not None else upload_result.size_bytes,
            checksum_sha256=metadata.checksum_sha256 or upload_result.checksum_sha256,
            upload_mode=upload_result.upload_mode,
            technical_appendix={"folder_path": folder_path, "upload_mode": upload_result.upload_mode, "root_folder_configured": True},
        )
        verification = self.verifier.verify(upload_result=upload_result, local_size_bytes=local_size, local_sha256=local_sha256)
        if not verification.ok:
            raise ValidationFailureError(verification.error_message or "Google Drive upload verification failed")
        cloud_ref = CloudMediaRefService(self.session).create_verified_ref(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            uploaded_video_id=uploaded_video_id,
            render_package_id=render_package_id,
            media_type=media_type,
            upload_result=upload_result,
            verification=verification,
            local_source_path_hash=_hash_path(local_path),
            checksum_sha256=local_sha256,
            source_refs=source_refs,
            retention_policy=retention_policy,
        )
        return cloud_ref, verification


class MediaOffloadJobService:
    def __init__(
        self,
        session: Session,
        *,
        upload_service: GoogleDriveUploadService | None = None,
        retention_service: LocalMediaRetentionPolicyService | None = None,
        cleanup_service: LocalMediaCleanupService | None = None,
    ):
        self.session = session
        self.upload_service = upload_service or GoogleDriveUploadService(session)
        self.retention_service = retention_service or LocalMediaRetentionPolicyService(session)
        self.cleanup_service = cleanup_service or LocalMediaCleanupService(session)

    def create_job(self, *, data: MediaOffloadJobCreate) -> MediaOffloadJob:
        path_hash = _hash_path(Path(data.local_source_path)) if data.local_source_path else None
        job = MediaOffloadJob(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            video_project_id=data.video_project_id,
            uploaded_video_id=data.uploaded_video_id,
            source_media_ref_id=data.source_media_ref_id,
            render_package_id=data.render_package_id,
            local_source_path_hash=path_hash,
            target_provider="GOOGLE_DRIVE",
            target_folder_policy=_sanitize_internal_policy({**data.target_folder_policy, "keep_local": data.keep_local}),
            target_media_type=data.target_media_type,
            job_state="PENDING",
        )
        self.session.add(job)
        self.session.flush()
        return job

    def require(self, job_id: Any) -> MediaOffloadJob:
        job = self.session.get(MediaOffloadJob, job_id)
        if job is None:
            raise NotFoundError(f"media offload job not found: {job_id}")
        return job

    def execute_job(self, *, job_id: Any, data: MediaOffloadExecuteRequest | None = None) -> MediaOffloadJob:
        job = self.require(job_id)
        request = data or MediaOffloadExecuteRequest()
        local_path = self._resolve_local_path(job, request.local_source_path)
        if local_path is None:
            job.job_state = "FAILED"
            job.error_code = "LOCAL_SOURCE_PATH_REQUIRED"
            job.error_message = "local source path is required at execution time"
            job.completed_at = utc_now()
            self.session.flush()
            return job
        try:
            resolved = local_path.resolve()
            if not resolved.exists() or not resolved.is_file():
                raise ValidationFailureError("local source file does not exist")
            job.local_source_path_hash = _hash_path(resolved)
            job.job_state = "UPLOADING"
            job.started_at = utc_now()
            job.error_code = None
            job.error_message = None
            self.session.flush()
            policy = self.retention_service.get_or_create_default(company_id=job.company_id, channel_workspace_id=job.channel_workspace_id)
            keep_local = bool(request.keep_local or (job.target_folder_policy or {}).get("keep_local"))
            retention_blob = self.retention_service.retention_blob(policy, keep_local=keep_local)
            cloud_ref, _verification = self.upload_service.upload_verified(
                local_path=resolved,
                media_type=job.target_media_type,
                company_id=job.company_id,
                channel_workspace_id=job.channel_workspace_id,
                video_project_id=job.video_project_id,
                uploaded_video_id=job.uploaded_video_id,
                render_package_id=job.render_package_id,
                source_refs=_source_refs_for_job(job),
                retention_policy=retention_blob,
            )
            job.cloud_media_ref_id = cloud_ref.id
            job.job_state = "VERIFIED"
            if job.source_media_ref_id:
                source_ref = self.session.get(FinalMediaRef, job.source_media_ref_id)
                if source_ref is not None:
                    source_ref.cloud_media_ref_id = cloud_ref.id
            cleanup_reason = self.cleanup_service.cleanup_verified_ref(
                cloud_ref=cloud_ref,
                local_path=resolved,
                policy=policy,
                keep_local=keep_local,
                current_job_id=job.id,
            )
            if cleanup_reason == "LOCAL_MEDIA_CLEANUP_COMPLETED":
                job.job_state = "CLEANED_LOCAL"
            job.completed_at = utc_now()
            self.session.flush()
            return job
        except Exception as exc:
            job.job_state = "FAILED"
            job.error_code = _error_code_for_exception(exc)
            job.error_message = _safe_error_message(exc)
            job.completed_at = utc_now()
            self.session.flush()
            return job

    def _resolve_local_path(self, job: MediaOffloadJob, local_source_path: str | None) -> Path | None:
        if local_source_path:
            return Path(local_source_path)
        if job.source_media_ref_id:
            source_ref = self.session.get(FinalMediaRef, job.source_media_ref_id)
            if source_ref is not None and source_ref.file_ref:
                return Path(source_ref.file_ref)
        return None


class MediaCloudReadService:
    def __init__(self, session: Session):
        self.session = session

    def require_ref(self, cloud_media_ref_id: Any) -> CloudMediaRef:
        return CloudMediaRefService(self.session).require(cloud_media_ref_id)

    def dashboard_payload(self, cloud_media_ref_id: Any) -> CloudMediaReadPayload:
        return CloudMediaRefService(self.session).dashboard_payload(self.require_ref(cloud_media_ref_id))

    def list_by_video_project(self, video_project_id: Any) -> list[CloudMediaReadPayload]:
        refs = self.session.scalars(
            select(CloudMediaRef).where(CloudMediaRef.video_project_id == video_project_id).order_by(CloudMediaRef.created_at.desc())
        ).all()
        return [CloudMediaRefService(self.session).dashboard_payload(ref) for ref in refs]

    def list_by_render_package(self, render_package_id: Any) -> list[CloudMediaReadPayload]:
        refs = self.session.scalars(
            select(CloudMediaRef).where(CloudMediaRef.render_package_id == render_package_id).order_by(CloudMediaRef.created_at.desc())
        ).all()
        return [CloudMediaRefService(self.session).dashboard_payload(ref) for ref in refs]

    def list_by_uploaded_video(self, uploaded_video_id: Any) -> list[CloudMediaReadPayload]:
        refs = self.session.scalars(
            select(CloudMediaRef).where(CloudMediaRef.uploaded_video_id == uploaded_video_id).order_by(CloudMediaRef.created_at.desc())
        ).all()
        return [CloudMediaRefService(self.session).dashboard_payload(ref) for ref in refs]


class MediaOffloadReadService:
    def __init__(self, session: Session):
        self.session = session

    def require_job(self, job_id: Any) -> MediaOffloadJob:
        return MediaOffloadJobService(self.session).require(job_id)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _post_google_token(payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urlrequest.Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlrequest.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ValidationFailureError(f"Google OAuth token exchange failed: HTTP {exc.code}") from exc


def _read_oauth_client_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    config = payload.get("installed") or payload.get("web") or payload
    redirect_uris = config.get("redirect_uris") if isinstance(config.get("redirect_uris"), list) else []
    return {
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
        "redirect_uri": redirect_uris[0] if redirect_uris else config.get("redirect_uri"),
    }


def _write_json_secret_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _local_file_secret_ref(path: Path) -> str:
    try:
        return f"local_file://{path.resolve().relative_to(ROOT)}"
    except ValueError:
        return f"local_file://{path.resolve()}"


def _expires_at_from_response(token_response: dict[str, Any]) -> datetime | None:
    expires_in = token_response.get("expires_in")
    if expires_in is None:
        return None
    try:
        return utc_now() + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=utc_now().tzinfo)


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _hash_path(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _drive_result_from_payload(payload: dict[str, Any], *, upload_mode: str | None) -> GoogleDriveUploadResult:
    parents = payload.get("parents") if isinstance(payload.get("parents"), list) else []
    size = payload.get("size")
    try:
        size_bytes = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    return GoogleDriveUploadResult(
        drive_file_id=str(payload.get("id") or ""),
        drive_folder_id=str(parents[0]) if parents else None,
        web_view_link=str(payload.get("webViewLink") or ""),
        file_name=payload.get("name"),
        mime_type=payload.get("mimeType"),
        size_bytes=size_bytes,
        checksum_sha256=payload.get("sha256Checksum"),
        upload_mode=upload_mode,
        technical_appendix={"upload_mode": upload_mode} if upload_mode else {},
    )


def _default_drive_folder_path(
    *,
    company_id: Any | None,
    channel_workspace_id: Any | None,
    video_project_id: Any | None,
    media_type: str,
) -> list[str]:
    subfolder = {
        "LONG_FORM_FINAL": "long_form",
        "SHORT_FINAL": "shorts",
        "THUMBNAIL": "thumbnails",
        "CAPTION": "captions",
        "AI_HERO": "ai_hero",
        "CREATOMATE_ASSET": "creatomate_assets",
        "PUBLISH_PACKAGE": "publish_package",
        "QC_EXPORT": "qc",
    }.get(media_type, "misc")
    return [
        "VCOS",
        f"company_{company_id or 'unknown'}",
        f"channel_{channel_workspace_id or 'unknown'}",
        f"project_{video_project_id or 'unknown'}",
        subfolder,
    ]


def _source_refs_for_job(job: MediaOffloadJob) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [{"type": "MediaOffloadJob", "id": str(job.id)}]
    if job.source_media_ref_id:
        refs.append({"type": "FinalMediaRef", "id": str(job.source_media_ref_id)})
    if job.render_package_id:
        refs.append({"type": "RenderPackageSnapshot", "id": str(job.render_package_id)})
    if job.video_project_id:
        refs.append({"type": "VideoProject", "id": str(job.video_project_id)})
    if job.uploaded_video_id:
        refs.append({"type": "UploadedVideo", "id": str(job.uploaded_video_id)})
    return refs


def _sanitize_internal_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in policy.items() if "local_source_path" not in key.lower() and "absolute_path" not in key.lower()}


def _sanitize_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in PUBLIC_FORBIDDEN_KEY_FRAGMENTS):
                continue
            sanitized[key] = _sanitize_public_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_public_payload(item) for item in value]
    return value


def _path_is_under_allowed_root(path: Path, roots: list[Path]) -> bool:
    if not roots:
        return False
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _error_code_for_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "quota" in text:
        return "GOOGLE_DRIVE_QUOTA_ERROR"
    if "auth" in text or "oauth" in text or "reauthorization" in text:
        return "GOOGLE_DRIVE_NEEDS_REAUTH"
    if "disabled" in text or "root_folder" in text or "not configured" in text:
        return "GOOGLE_DRIVE_OFFLOAD_NOT_CONFIGURED"
    if "verification" in text or "size" in text:
        return "MEDIA_OFFLOAD_UPLOAD_FAILED"
    return "MEDIA_OFFLOAD_UPLOAD_FAILED"


def _safe_error_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    for marker in ("access_token", "refresh_token", "client_secret", "authorization", "Bearer "):
        message = message.replace(marker, "[redacted]")
    return message[:500]
