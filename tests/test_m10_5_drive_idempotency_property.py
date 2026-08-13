from __future__ import annotations

import hashlib
import json
import urllib.parse

import pytest

from app.services.m10_5 import (
    GOOGLE_DRIVE_APP_PROPERTY_MAX_BYTES,
    GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY,
    GoogleDriveMediaStorageProvider,
    _drive_idempotency_property_value,
)


LIVE_VIDEO_IDEMPOTENCY_KEY = (
    "v2-google-drive-remote:11111111-2222-3333-4444-555555555555:"
    "archive:44c8ab0133dfe8ee08ad:google-drive-archive"
)
LIVE_CAPTION_IDEMPOTENCY_KEY = f"{LIVE_VIDEO_IDEMPOTENCY_KEY}.caption"


@pytest.mark.parametrize(
    ("idempotency_key", "original_property_bytes"),
    [
        (LIVE_VIDEO_IDEMPOTENCY_KEY, 129),
        (LIVE_CAPTION_IDEMPOTENCY_KEY, 137),
    ],
)
def test_live_shaped_idempotency_values_compact_below_drive_property_limit(
    idempotency_key: str,
    original_property_bytes: int,
) -> None:
    property_key_bytes = GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY.encode("utf-8")
    original_value_bytes = idempotency_key.encode("utf-8")
    assert (
        len(property_key_bytes) + len(original_value_bytes) == original_property_bytes
    )

    compacted = _drive_idempotency_property_value(idempotency_key)

    assert compacted == f"sha256:{hashlib.sha256(original_value_bytes).hexdigest()}"
    assert (
        len(property_key_bytes) + len(compacted.encode("utf-8"))
        < GOOGLE_DRIVE_APP_PROPERTY_MAX_BYTES
    )
    assert _drive_idempotency_property_value(idempotency_key) == compacted


def test_legacy_idempotency_property_value_within_limit_is_unchanged() -> None:
    legacy_value = "v2:legacy-archive:google-drive"
    boundary_value = "x" * (
        GOOGLE_DRIVE_APP_PROPERTY_MAX_BYTES
        - len(GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY.encode("utf-8"))
    )

    assert _drive_idempotency_property_value(legacy_value) == legacy_value
    assert _drive_idempotency_property_value(boundary_value) == boundary_value


@pytest.mark.parametrize(
    "idempotency_key",
    [LIVE_VIDEO_IDEMPOTENCY_KEY, LIVE_CAPTION_IDEMPOTENCY_KEY],
)
@pytest.mark.parametrize("upload_mode", ["multipart", "resumable"])
def test_drive_lookup_and_upload_use_the_same_compacted_property_value(
    monkeypatch,
    tmp_path,
    idempotency_key: str,
    upload_mode: str,
) -> None:
    requests = []

    class FakeResponse:
        def __init__(self, payload: dict, *, headers: dict | None = None) -> None:
            self.payload = payload
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout, context=None):
        requests.append(request)
        if request.get_method() == "GET":
            assert timeout == 20
            return FakeResponse({"files": []})
        if "uploadType=resumable" in request.full_url:
            assert timeout == 20
            return FakeResponse(
                {}, headers={"Location": "https://upload.invalid/session"}
            )
        assert timeout == (60 if upload_mode == "multipart" else 120)
        return FakeResponse(
            {
                "id": "remote-file",
                "name": "final.mp4",
                "size": "5",
                "mimeType": "video/mp4",
                "webViewLink": "https://drive.invalid/remote-file",
                "parents": ["folder-id"],
            }
        )

    monkeypatch.setattr("app.services.m10_5.urlrequest.urlopen", fake_urlopen)
    provider = GoogleDriveMediaStorageProvider()
    local_file = tmp_path / "final.mp4"
    local_file.write_bytes(b"video")

    assert (
        provider.find_file_by_idempotency_key(
            access_token="ephemeral-token",
            folder_id="folder-id",
            idempotency_key=idempotency_key,
        )
        is None
    )
    provider.upload_file(
        access_token="ephemeral-token",
        local_path=local_file,
        folder_id="folder-id",
        upload_mode=upload_mode,
        mime_type="video/mp4",
        idempotency_key=idempotency_key,
    )

    assert len(requests) == (2 if upload_mode == "multipart" else 3)
    lookup_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(requests[0].full_url).query
    )["q"][0]
    compacted = _drive_idempotency_property_value(idempotency_key)
    assert f"value='{compacted}'" in lookup_query
    assert idempotency_key not in lookup_query

    upload_metadata_body = requests[1].data
    assert isinstance(upload_metadata_body, bytes)
    expected_metadata_property = json.dumps(
        {GOOGLE_DRIVE_IDEMPOTENCY_PROPERTY_KEY: compacted}
    ).encode("utf-8")
    assert expected_metadata_property in upload_metadata_body
    assert idempotency_key.encode("utf-8") not in upload_metadata_body
