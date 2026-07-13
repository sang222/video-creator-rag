from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from app.contracts.asset_acquisition import AssetRequest
from app.services.native_render_plan import stable_hash
from app.services.pexels_media_downloader import (
    PEXELS_MEDIA_REQUEST_HEADERS,
    PexelsDownloadExecutionContext,
    PexelsMediaDownloadClient,
    PexelsMediaDownloadError,
)
from app.services.provider_asset_manifests import (
    PexelsDownloadPlanBuilder,
    PexelsRenditionSelector,
    PexelsResponseParser,
)


SIGNED_MEDIA_URL = (
    "https://media.pexels.test/video-file.mp4"
    "?expires=1900000000&token=a%2Fb&signature=z%2Bq%3D"
)
REDIRECTED_MEDIA_URL = (
    "https://cdn.pexels.test/final-video.mp4"
    "?expires=1900000000&token=a%2Fb&signature=z%2Bq%3D"
)


def _asset_request() -> AssetRequest:
    payload = {
        "request_id": "pexels-dl1-stock",
        "scene_id": "scene-stock",
        "source_segment_ids": ["segment-stock"],
        "purpose": "SUPPORT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": "guarded media workflow",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 4,
        "maximum_duration_seconds": 12,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["SUPPORTING_STOCK", "NATIVE_VISUAL"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def _api_payload(link: str = SIGNED_MEDIA_URL, *, include_hls: bool = True) -> dict:
    video_files = []
    if include_hls:
        video_files.append(
            {
                "id": 7000,
                "file_type": "video/mp4",
                "width": 1920,
                "height": 1080,
                "link": "https://media.pexels.test/playlist.m3u8?token=forbidden",
            }
        )
    video_files.append(
        {
            "id": 7001,
            "file_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "link": link,
        }
    )
    return {
        "videos": [
            {
                "id": 9001,
                "width": 1920,
                "height": 1080,
                "duration": 8,
                "url": "https://www.pexels.com/video/source-page-not-media-9001/",
                "image": "https://images.pexels.test/not-video.jpeg",
                "video_pictures": [{"picture": "https://images.pexels.test/preview.jpeg"}],
                "user": {
                    "id": 1,
                    "name": "Fixture Creator",
                    "url": "https://www.pexels.com/@fixture-creator",
                },
                "video_files": video_files,
            }
        ]
    }


def _boundary(tmp_path: Path, link: str = SIGNED_MEDIA_URL):
    candidate = PexelsResponseParser().parse(_api_payload(link))[0]
    rendition = PexelsRenditionSelector().select(candidate, _asset_request())
    plan = PexelsDownloadPlanBuilder().build(candidate, rendition, _asset_request())
    context = PexelsDownloadExecutionContext.from_selected_api_rendition(
        provider_asset_id=candidate.provider_asset_id,
        rendition=rendition,
        workspace_directory=tmp_path,
        maximum_allowed_bytes=20 * 1024 * 1024,
    )
    return candidate, rendition, plan, context


def _public_resolver(_host: str):
    return ["8.8.8.8"]


@pytest.fixture(scope="session")
def valid_mp4_bytes(tmp_path_factory) -> bytes:
    output = tmp_path_factory.mktemp("pexels-dl1-media") / "valid-1280x720.mp4"
    subprocess.run(
        [
            "/opt/homebrew/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1280x720:r=1",
            "-frames:v",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output.read_bytes()


def test_video_files_link_selected_and_durable_transient_boundary(tmp_path):
    candidate, rendition, plan, context = _boundary(tmp_path)
    assert rendition["link"] == SIGNED_MEDIA_URL
    assert rendition["link"] != candidate.source_page_url
    assert rendition["id"] == 7001 and rendition["file_type"] == "video/mp4"
    durable = plan.model_dump_json()
    assert plan.volatile_download_reference.startswith("volatile://pexels-download/")
    assert plan.download_url_hash == hashlib.sha256(SIGNED_MEDIA_URL.encode()).hexdigest()
    assert plan.expected_media_host == "media.pexels.test" and plan.query_present is True
    assert SIGNED_MEDIA_URL not in durable and "signature=" not in durable and "token=" not in durable
    assert SIGNED_MEDIA_URL not in repr(context)
    with pytest.raises(TypeError):
        json.dumps(context)
    with pytest.raises(TypeError, match="NOT_SERIALIZABLE"):
        context.__getstate__()


def test_signed_query_redirect_header_isolation_stream_checksum_and_atomic_rename(
    tmp_path,
    valid_mp4_bytes,
    monkeypatch,
):
    _candidate, _rendition, plan, context = _boundary(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "media.pexels.test":
            return httpx.Response(302, headers={"Location": REDIRECTED_MEDIA_URL}, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "video/mp4", "Content-Length": str(len(valid_mp4_bytes))},
            content=valid_mp4_bytes,
            request=request,
        )

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DNS/network forbidden")))
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    downloader = PexelsMediaDownloadClient(client=client, host_resolver=_public_resolver)
    result = downloader.download(plan=plan, context=context)

    assert str(seen[0].url) == SIGNED_MEDIA_URL
    assert str(seen[1].url) == REDIRECTED_MEDIA_URL
    for request in seen:
        names = {name.lower() for name in request.headers}
        assert "authorization" not in names and "x-api-key" not in names and "cookie" not in names
        assert request.headers["Accept"] == PEXELS_MEDIA_REQUEST_HEADERS["Accept"]
        assert request.headers["User-Agent"] == PEXELS_MEDIA_REQUEST_HEADERS["User-Agent"]
    final = Path(result["path"])
    assert final.name == "pexels-9001-7001.mp4"
    assert final.is_file() and not final.with_name(final.name + ".part").exists()
    assert result["sha256"] == hashlib.sha256(valid_mp4_bytes).hexdigest()
    assert result["http_evidence"]["redirect_count"] == 1
    assert result["http_evidence"]["final_media_host"] == "cdn.pexels.test"
    assert result["media_probe"]["width"] == 1280 and result["media_probe"]["height"] == 720
    assert context.expired is True
    client.close()


def test_volatile_reference_cannot_execute_and_no_transport_call(tmp_path):
    _candidate, _rendition, plan, context = _boundary(tmp_path)
    context._raw_media_url = plan.volatile_download_reference
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request)))
    downloader = PexelsMediaDownloadClient(
        client=client,
        host_resolver=_public_resolver,
        media_probe=lambda path: {},
    )
    with pytest.raises(PexelsMediaDownloadError, match="VOLATILE_REFERENCE_USED_AS_EXECUTION_URL"):
        downloader.download(plan=plan, context=context)
    assert calls == [] and context.expired is True
    client.close()


@pytest.mark.parametrize(
    ("content_type", "body", "reason_code"),
    [
        ("text/html", b"<html>denied</html>", "MEDIA_CONTENT_TYPE_INVALID"),
        ("video/mp4", b"", "MEDIA_BODY_EMPTY"),
    ],
)
def test_invalid_or_empty_response_is_rejected_and_part_removed(tmp_path, content_type, body, reason_code):
    _candidate, _rendition, plan, context = _boundary(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Content-Type": content_type}
        if body:
            headers["Content-Length"] = str(len(body))
        return httpx.Response(200, headers=headers, content=body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PexelsMediaDownloadClient(
        client=client,
        host_resolver=_public_resolver,
        media_probe=lambda path: {},
    )
    with pytest.raises(PexelsMediaDownloadError, match=reason_code) as captured:
        downloader.download(plan=plan, context=context)
    target = tmp_path / "pexels-9001-7001.mp4"
    assert not target.exists() and not target.with_name(target.name + ".part").exists()
    assert captured.value.safe_evidence["part_cleanup_result"] in {"ABSENT", "DELETED"}
    assert SIGNED_MEDIA_URL not in json.dumps(captured.value.safe_evidence)
    client.close()


def test_hls_or_missing_media_link_is_never_selected():
    payload = _api_payload(include_hls=False)
    payload["videos"][0]["video_files"] = [
        {
            "id": 7000,
            "file_type": "video/mp4",
            "width": 1920,
            "height": 1080,
            "link": "https://media.pexels.test/playlist.m3u8?token=forbidden",
        },
        {
            "id": 7002,
            "file_type": "video/mp4",
            "width": 1920,
            "height": 1080,
            "link": "",
        },
    ]
    candidate = PexelsResponseParser().parse(payload)[0]
    with pytest.raises(ValueError, match="PEXELS_COMPATIBLE_MP4_NOT_FOUND"):
        PexelsRenditionSelector().select(candidate, _asset_request())


def test_duplicate_download_never_overwrites_existing_file(tmp_path, valid_mp4_bytes):
    _candidate, rendition, plan, first_context = _boundary(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=valid_mp4_bytes, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PexelsMediaDownloadClient(client=client, host_resolver=_public_resolver)
    first = downloader.download(plan=plan, context=first_context)
    before = Path(first["path"]).read_bytes()
    duplicate_context = PexelsDownloadExecutionContext.from_selected_api_rendition(
        provider_asset_id="9001",
        rendition=rendition,
        workspace_directory=tmp_path,
        maximum_allowed_bytes=20 * 1024 * 1024,
    )
    with pytest.raises(PexelsMediaDownloadError, match="DOWNLOAD_TARGET_ALREADY_EXISTS"):
        downloader.download(plan=plan, context=duplicate_context)
    assert Path(first["path"]).read_bytes() == before
    client.close()


@pytest.mark.parametrize(
    ("redirect_target", "reason_code"),
    [
        ("http://cdn.pexels.test/video.mp4?token=unsafe", "MEDIA_URL_HTTPS_REQUIRED"),
        ("https://127.0.0.1/video.mp4?token=unsafe", "MEDIA_SSRF_ADDRESS_FORBIDDEN"),
    ],
)
def test_non_https_or_private_redirect_is_blocked_without_second_request(tmp_path, redirect_target, reason_code):
    _candidate, _rendition, plan, context = _boundary(tmp_path)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"Location": redirect_target}, request=request)

    def resolver(host: str):
        return ["127.0.0.1"] if host == "127.0.0.1" else ["8.8.8.8"]

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PexelsMediaDownloadClient(client=client, host_resolver=resolver, media_probe=lambda path: {})
    with pytest.raises(PexelsMediaDownloadError) as captured:
        downloader.download(plan=plan, context=context)
    assert len(calls) == 1
    assert captured.value.reason_code == reason_code
    assert captured.value.safe_evidence["part_cleanup_result"] == "ABSENT"
    client.close()


def test_redirect_limit_blocks_loop_without_partial_file(tmp_path):
    _candidate, _rendition, plan, context = _boundary(tmp_path)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"Location": SIGNED_MEDIA_URL}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PexelsMediaDownloadClient(
        client=client,
        host_resolver=_public_resolver,
        media_probe=lambda path: {},
        maximum_redirects=2,
    )
    with pytest.raises(PexelsMediaDownloadError, match="MEDIA_REDIRECT_NOT_FOLLOWED") as captured:
        downloader.download(plan=plan, context=context)
    assert len(calls) == 3 and captured.value.safe_evidence["redirect_count"] == 3
    assert not (tmp_path / "pexels-9001-7001.mp4.part").exists()
    client.close()


def test_raw_signed_url_absent_from_durable_and_failure_evidence(tmp_path):
    _candidate, _rendition, plan, context = _boundary(tmp_path)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, headers={"Content-Type": "text/html"}, request=request)
        )
    )
    downloader = PexelsMediaDownloadClient(client=client, host_resolver=_public_resolver, media_probe=lambda path: {})
    with pytest.raises(PexelsMediaDownloadError, match="MEDIA_HTTP_FORBIDDEN") as captured:
        downloader.download(plan=plan, context=context)
    durable_and_error = plan.model_dump_json() + json.dumps(captured.value.safe_evidence) + repr(context)
    for forbidden in (SIGNED_MEDIA_URL, "a%2Fb", "z%2Bq", "signature=", "token="):
        assert forbidden not in durable_and_error
    assert captured.value.safe_evidence["request_header_names"] == ["Accept", "User-Agent"]
    client.close()
