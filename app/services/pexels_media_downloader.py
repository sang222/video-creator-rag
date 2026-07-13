from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.contracts.asset_acquisition import PexelsDownloadPlan


PEXELS_MEDIA_REQUEST_HEADERS = {
    "Accept": "video/mp4,video/*",
    "User-Agent": "VCOS/1.0",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SELECTED_RENDITION_TOKEN = object()
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


class PexelsMediaDownloadError(RuntimeError):
    """Download failure whose message and evidence never contain a media URL."""

    def __init__(self, reason_code: str, safe_evidence: dict[str, Any]):
        self.reason_code = reason_code
        self.safe_evidence = safe_evidence
        super().__init__(reason_code)


class PexelsDownloadExecutionContext:
    """Non-serializable, one-attempt carrier for a selected video_files[].link."""

    __slots__ = (
        "_raw_media_url",
        "_expired",
        "provider_asset_id",
        "provider_file_id",
        "expected_mime_type",
        "expected_width",
        "expected_height",
        "maximum_allowed_bytes",
        "workspace_target_path",
        "initial_media_host",
        "query_present",
        "download_url_hash",
    )

    def __init__(
        self,
        *,
        raw_media_url: str,
        provider_asset_id: str,
        provider_file_id: str,
        expected_mime_type: str,
        expected_width: int,
        expected_height: int,
        maximum_allowed_bytes: int,
        workspace_target_path: Path,
        _source_token: object,
    ):
        if _source_token is not _SELECTED_RENDITION_TOKEN:
            raise ValueError("PEXELS_EXECUTION_CONTEXT_REQUIRES_SELECTED_API_RENDITION")
        parsed = _validated_https_url(raw_media_url, resolve_host=False)
        if parsed.path.lower().endswith(".m3u8"):
            raise ValueError("PEXELS_HLS_RENDITION_FORBIDDEN")
        if expected_mime_type.lower() != "video/mp4":
            raise ValueError("PEXELS_EXECUTION_MIME_TYPE_INVALID")
        if expected_width <= 0 or expected_height <= 0:
            raise ValueError("PEXELS_EXECUTION_DIMENSIONS_INVALID")
        if maximum_allowed_bytes <= 0:
            raise ValueError("PEXELS_EXECUTION_SIZE_CAP_INVALID")
        self._raw_media_url: str | None = raw_media_url
        self._expired = False
        self.provider_asset_id = provider_asset_id
        self.provider_file_id = provider_file_id
        self.expected_mime_type = expected_mime_type.lower()
        self.expected_width = expected_width
        self.expected_height = expected_height
        self.maximum_allowed_bytes = maximum_allowed_bytes
        self.workspace_target_path = workspace_target_path
        self.initial_media_host = parsed.hostname or ""
        self.query_present = bool(parsed.query)
        self.download_url_hash = hashlib.sha256(raw_media_url.encode()).hexdigest()

    @classmethod
    def from_selected_api_rendition(
        cls,
        *,
        provider_asset_id: str,
        rendition: dict[str, Any],
        workspace_directory: Path,
        maximum_allowed_bytes: int,
    ) -> "PexelsDownloadExecutionContext":
        raw_media_url = str(rendition.get("link") or "")
        provider_file_id = str(rendition.get("id") or "")
        if not raw_media_url:
            raise ValueError("PEXELS_DOWNLOAD_LINK_MISSING")
        if not provider_file_id:
            raise ValueError("PEXELS_PROVIDER_FILE_ID_MISSING")
        asset_component = _safe_id_component(provider_asset_id)
        file_component = _safe_id_component(provider_file_id)
        target = workspace_directory / f"pexels-{asset_component}-{file_component}.mp4"
        return cls(
            raw_media_url=raw_media_url,
            provider_asset_id=provider_asset_id,
            provider_file_id=provider_file_id,
            expected_mime_type=str(rendition.get("file_type") or rendition.get("mime_type") or ""),
            expected_width=int(rendition.get("width") or 0),
            expected_height=int(rendition.get("height") or 0),
            maximum_allowed_bytes=maximum_allowed_bytes,
            workspace_target_path=target,
            _source_token=_SELECTED_RENDITION_TOKEN,
        )

    @property
    def expired(self) -> bool:
        return self._expired

    def execution_url(self) -> str:
        if self._expired or self._raw_media_url is None:
            raise RuntimeError("PEXELS_DOWNLOAD_EXECUTION_CONTEXT_EXPIRED")
        return self._raw_media_url

    def validate_against(self, plan: PexelsDownloadPlan) -> None:
        raw_media_url = self.execution_url()
        if raw_media_url.lower().startswith("volatile://"):
            raise PexelsMediaDownloadError(
                "VOLATILE_REFERENCE_USED_AS_EXECUTION_URL",
                _base_evidence(self, reason_code="VOLATILE_REFERENCE_USED_AS_EXECUTION_URL"),
            )
        parsed = urlsplit(raw_media_url)
        if plan.query_present and not parsed.query:
            raise PexelsMediaDownloadError(
                "SIGNED_QUERY_STRIPPED",
                _base_evidence(self, reason_code="SIGNED_QUERY_STRIPPED"),
            )
        matches = (
            self.provider_asset_id == plan.provider_asset_id
            and self.provider_file_id == plan.provider_file_id
            and self.download_url_hash == plan.download_url_hash
            and self.initial_media_host == plan.expected_media_host
            and self.query_present == plan.query_present
            and self.expected_mime_type == plan.mime_type.lower()
            and self.expected_width == plan.width
            and self.expected_height == plan.height
        )
        if not matches:
            reason = "SIGNED_QUERY_STRIPPED" if plan.query_present else "DOWNLOAD_EXECUTION_CONTEXT_PLAN_MISMATCH"
            raise PexelsMediaDownloadError(reason, _base_evidence(self, reason_code=reason))

    def expire(self) -> None:
        self._raw_media_url = None
        self._expired = True

    def __repr__(self) -> str:
        return (
            "PexelsDownloadExecutionContext("
            f"provider_asset_id={self.provider_asset_id!r}, provider_file_id={self.provider_file_id!r}, "
            f"initial_media_host={self.initial_media_host!r}, query_present={self.query_present!r}, "
            f"expired={self.expired!r})"
        )

    def __getstate__(self):
        raise TypeError("PEXELS_DOWNLOAD_EXECUTION_CONTEXT_NOT_SERIALIZABLE")

    def __reduce_ex__(self, protocol):
        raise TypeError("PEXELS_DOWNLOAD_EXECUTION_CONTEXT_NOT_SERIALIZABLE")


class PexelsMediaDownloadClient:
    """Redirect-aware streaming client isolated from Pexels API credentials."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        host_resolver: Callable[[str], Iterable[str]] | None = None,
        media_probe: Callable[[Path], dict[str, Any]] | None = None,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 120.0,
        maximum_redirects: int = 5,
    ):
        if maximum_redirects < 0:
            raise ValueError("PEXELS_MEDIA_REDIRECT_LIMIT_INVALID")
        self._client = client
        self._host_resolver = host_resolver or _resolve_host
        self._media_probe = media_probe or _ffprobe_mp4
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._maximum_redirects = maximum_redirects

    def download(
        self,
        *,
        plan: PexelsDownloadPlan,
        context: PexelsDownloadExecutionContext,
    ) -> dict[str, Any]:
        part = context.workspace_target_path.with_name(context.workspace_target_path.name + ".part")
        state = _base_evidence(context)
        owned_client = self._client is None
        client = self._client or httpx.Client(
            follow_redirects=False,
            timeout=self._timeout,
            trust_env=False,
            headers={},
        )
        try:
            context.validate_against(plan)
            target = context.workspace_target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.parent.is_symlink() or target.is_symlink():
                self._raise("DOWNLOAD_TARGET_SYMLINK_FORBIDDEN", state)
            if target.exists():
                self._raise("DOWNLOAD_TARGET_ALREADY_EXISTS", state)
            part.unlink(missing_ok=True)
            current_url = context.execution_url()
            redirect_count = 0
            digest = hashlib.sha256()
            size = 0

            while True:
                parsed = _validated_https_url(current_url, resolver=self._host_resolver)
                state["final_media_host"] = parsed.hostname
                with client.stream("GET", current_url, headers=PEXELS_MEDIA_REQUEST_HEADERS) as response:
                    state["http_status"] = response.status_code
                    state["content_type"] = _media_type(response.headers.get("Content-Type"))
                    state["content_length"] = _content_length(response.headers.get("Content-Length"))
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            self._raise("MEDIA_REDIRECT_NOT_FOLLOWED", state)
                        redirect_count += 1
                        state["redirect_count"] = redirect_count
                        if redirect_count > self._maximum_redirects:
                            self._raise("MEDIA_REDIRECT_NOT_FOLLOWED", state)
                        current_url = urljoin(current_url, location)
                        _validated_https_url(current_url, resolver=self._host_resolver)
                        continue
                    if response.status_code not in {200, 206}:
                        reason = "MEDIA_HTTP_FORBIDDEN" if response.status_code == 403 else "MEDIA_HTTP_STATUS_INVALID"
                        self._raise(reason, state)
                    if not _allowed_video_content_type(state["content_type"]):
                        self._raise("MEDIA_CONTENT_TYPE_INVALID", state)
                    if state["content_length"] is not None:
                        if state["content_length"] <= 0:
                            self._raise("MEDIA_BODY_EMPTY", state)
                        if state["content_length"] > context.maximum_allowed_bytes:
                            self._raise("MEDIA_SIZE_LIMIT_EXCEEDED", state)
                    with part.open("xb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            if not chunk:
                                continue
                            size += len(chunk)
                            state["bytes_received"] = size
                            if size > context.maximum_allowed_bytes:
                                self._raise("MEDIA_SIZE_LIMIT_EXCEEDED", state)
                            output.write(chunk)
                            digest.update(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                break

            if size == 0:
                self._raise("MEDIA_BODY_EMPTY", state)
            probe = self._media_probe(part)
            if not _probe_matches_context(probe, context):
                self._raise("MEDIA_SIGNATURE_OR_SHAPE_INVALID", state)
            if context.workspace_target_path.exists():
                self._raise("DOWNLOAD_TARGET_ALREADY_EXISTS", state)
            os.rename(part, context.workspace_target_path)
            state.update(
                {
                    "reason_code": None,
                    "exception_class": None,
                    "part_cleanup_result": "RENAMED_ATOMICALLY",
                    "checksum_computed": True,
                }
            )
            return {
                "path": str(context.workspace_target_path),
                "size_bytes": size,
                "sha256": digest.hexdigest(),
                "http_evidence": state,
                "media_probe": probe,
            }
        except PexelsMediaDownloadError as exc:
            exc.safe_evidence["part_cleanup_result"] = _cleanup_part(part)
            raise
        except (httpx.HTTPError, OSError, ValueError, subprocess.SubprocessError) as exc:
            state["exception_class"] = type(exc).__name__
            safe_value_reason = str(exc)
            if isinstance(exc, OSError):
                state["reason_code"] = "CHECKSUM_OR_ATOMIC_RENAME_FAILED"
            elif re.fullmatch(r"(?:MEDIA|PEXELS)_[A-Z0-9_]+", safe_value_reason):
                state["reason_code"] = safe_value_reason
            else:
                state["reason_code"] = "MEDIA_STREAM_INTERRUPTED"
            state["part_cleanup_result"] = _cleanup_part(part)
            raise PexelsMediaDownloadError(state["reason_code"], state) from None
        finally:
            context.expire()
            if owned_client:
                client.close()

    @staticmethod
    def _raise(reason_code: str, state: dict[str, Any]) -> None:
        state["reason_code"] = reason_code
        state["exception_class"] = "PexelsMediaDownloadError"
        raise PexelsMediaDownloadError(reason_code, state)


def _base_evidence(
    context: PexelsDownloadExecutionContext,
    *,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "initial_media_host": context.initial_media_host,
        "final_media_host": None,
        "query_present": context.query_present,
        "redirect_count": 0,
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "bytes_received": 0,
        "checksum_computed": False,
        "exception_class": None,
        "reason_code": reason_code,
        "provider_asset_id": context.provider_asset_id,
        "provider_file_id": context.provider_file_id,
        "part_cleanup_result": "NOT_STARTED",
        "request_header_names": sorted(PEXELS_MEDIA_REQUEST_HEADERS),
    }


def _safe_id_component(value: str) -> str:
    sanitized = _SAFE_ID.sub("-", value).strip("-")
    if not sanitized:
        raise ValueError("PEXELS_PROVIDER_ID_INVALID")
    return sanitized[:80]


def _validated_https_url(
    raw_url: str,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
    resolve_host: bool = True,
):
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() == "volatile":
        raise ValueError("VOLATILE_REFERENCE_USED_AS_EXECUTION_URL")
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("MEDIA_URL_HTTPS_REQUIRED")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("MEDIA_URL_AUTHORITY_OR_FRAGMENT_FORBIDDEN")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("MEDIA_URL_PORT_INVALID") from exc
    if port not in (None, 443):
        raise ValueError("MEDIA_URL_PORT_FORBIDDEN")
    if parsed.hostname.lower() == "localhost" or parsed.hostname.lower().endswith(".local"):
        raise ValueError("MEDIA_SSRF_HOST_FORBIDDEN")
    if resolve_host:
        addresses = list((resolver or _resolve_host)(parsed.hostname))
        if not addresses or any(not _public_ip(address) for address in addresses):
            raise ValueError("MEDIA_SSRF_ADDRESS_FORBIDDEN")
    return parsed


def _resolve_host(host: str) -> list[str]:
    return sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )


def _public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _media_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _allowed_video_content_type(value: str | None) -> bool:
    if value is None:
        return False
    return value == "video/mp4" or (value.startswith("video/") and "mpegurl" not in value)


def _cleanup_part(part: Path) -> str:
    try:
        if part.exists():
            part.unlink()
            return "DELETED"
        return "ABSENT"
    except OSError:
        return "DELETE_FAILED"


def _ffprobe_mp4(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-show_entries",
            "format=format_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("MEDIA_FFPROBE_FAILED")
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    format_name = str((payload.get("format") or {}).get("format_name") or "")
    if not video or "mp4" not in format_name.split(","):
        raise ValueError("MEDIA_SIGNATURE_INVALID")
    return {
        "codec_type": "video",
        "codec_name": str(video.get("codec_name") or ""),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "container": "mp4",
    }


def _probe_matches_context(probe: dict[str, Any], context: PexelsDownloadExecutionContext) -> bool:
    return bool(
        probe.get("codec_type") == "video"
        and probe.get("container") == "mp4"
        and int(probe.get("width") or 0) == context.expected_width
        and int(probe.get("height") or 0) == context.expected_height
    )
