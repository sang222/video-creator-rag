from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.img_canary_security import (
    IMG_CANARY_V1_AUTHORIZATION_FILENAME,
    IMG_CANARY_V1_AUTHORIZATION_REF,
    IMG_CANARY_V1_TASK_KEY,
    IMG_CANARY_V2_AUTHORIZATION_REF,
    IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH,
    IMG_CANARY_V2_TASK_KEY,
    IMG_CANARY_V3_APPROVAL_ID,
    IMG_CANARY_V3_AUTHORIZATION_REF,
    IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH,
    IMG_CANARY_V3_TASK_KEY,
    img_canary_task_authority_identity,
)
from app.services.img_canary_security import (
    IMGCanarySecurityAuthorityError,
    IMGCanaryTaskAuthorizationStore,
)


NOW = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
RUN_ID = "img-canary-v3-20260718T103000Z-a1b2c3d4"
FINGERPRINT = "a" * 64
PROMPT_HASH = "b" * 64
BODY_HASH = "c" * 64
APPROVAL_HASH = "d" * 64


def _initialize(store: IMGCanaryTaskAuthorizationStore, *, run_id: str = RUN_ID):
    return store.initialize(
        task_key=IMG_CANARY_V3_TASK_KEY,
        authorization_ref=IMG_CANARY_V3_AUTHORIZATION_REF,
        approval_version="V3",
        approved_run_id=run_id,
        approved_request_fingerprint=FINGERPRINT,
        approved_prompt_hash=PROMPT_HASH,
        approved_serialized_body_hash=BODY_HASH,
        approved_scoped_approval_hash=APPROVAL_HASH,
        now=NOW,
    )


def test_v3_identity_is_fresh_and_v1_v2_resolution_is_unchanged() -> None:
    assert IMG_CANARY_V3_APPROVAL_ID == "operator-3c895af877e10f7f"
    assert IMG_CANARY_V3_TASK_KEY == (
        "img-canary-v3-approval-operator-3c895af877e10f7f"
    )
    assert IMG_CANARY_V3_AUTHORIZATION_REF == (
        "authorization://img-canary/v3/operator-3c895af877e10f7f/one-paid-request"
    )
    assert IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH == Path(
        "authorizations/operator-3c895af877e10f7f.json"
    )
    assert img_canary_task_authority_identity(RUN_ID) == (
        IMG_CANARY_V3_TASK_KEY,
        IMG_CANARY_V3_AUTHORIZATION_REF,
        IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH,
    )
    assert img_canary_task_authority_identity(
        "img-canary-v2-20260718T091203Z-cce118a4"
    ) == (
        IMG_CANARY_V2_TASK_KEY,
        IMG_CANARY_V2_AUTHORIZATION_REF,
        IMG_CANARY_V2_AUTHORIZATION_RELATIVE_PATH,
    )
    assert img_canary_task_authority_identity("img-canary-v1-legacy") == (
        IMG_CANARY_V1_TASK_KEY,
        IMG_CANARY_V1_AUTHORIZATION_REF,
        Path(IMG_CANARY_V1_AUTHORIZATION_FILENAME),
    )


def test_v3_authority_has_exact_binding_and_terminal_submit_semantics(
    tmp_path: Path,
) -> None:
    authority_path = tmp_path / "security" / IMG_CANARY_V3_AUTHORIZATION_RELATIVE_PATH
    store = IMGCanaryTaskAuthorizationStore(authority_path)
    available = _initialize(store)
    initial_bytes = authority_path.read_bytes()

    assert available.status == "AVAILABLE"
    assert available.approval_version == "V3"
    assert available.approved_run_id == RUN_ID
    assert available.approved_request_fingerprint == FINGERPRINT
    assert available.approved_prompt_hash == PROMPT_HASH
    assert available.approved_serialized_body_hash == BODY_HASH
    assert available.approved_scoped_approval_hash == APPROVAL_HASH
    assert _initialize(store).content_hash == available.content_hash
    assert authority_path.read_bytes() == initial_bytes

    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_V3_TASK_AUTHORIZATION_BINDING_MISMATCH",
    ):
        store.claim(
            run_id="img-canary-v3-20260718T103001Z-deadbeef",
            request_fingerprint=FINGERPRINT,
            now=NOW + timedelta(seconds=1),
        )
    assert store.load().status == "AVAILABLE"

    claimed = store.claim(
        run_id=RUN_ID,
        request_fingerprint=FINGERPRINT,
        now=NOW + timedelta(seconds=1),
    )
    assert claimed.status == "CLAIMED"
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORIZATION_ALREADY_CLAIMED",
    ):
        store.claim(
            run_id=RUN_ID,
            request_fingerprint=FINGERPRINT,
            now=NOW + timedelta(seconds=2),
        )

    consumed = store.consume(
        run_id=RUN_ID,
        request_fingerprint=FINGERPRINT,
        completion_status="PROVIDER_ATTEMPT_SUBMITTED",
        now=NOW + timedelta(seconds=2),
        expected_claimed_content_hash=claimed.content_hash,
        expected_serialized_body_hash=BODY_HASH,
        expected_scoped_approval_hash=APPROVAL_HASH,
    )
    consumed_bytes = authority_path.read_bytes()
    assert consumed.status == "CONSUMED"
    assert consumed.completion_status == "PROVIDER_ATTEMPT_SUBMITTED"
    assert store.consume(
        run_id=RUN_ID,
        request_fingerprint=FINGERPRINT,
        completion_status="PROVIDER_ATTEMPT_SUBMITTED",
        now=NOW + timedelta(seconds=3),
        expected_serialized_body_hash=BODY_HASH,
        expected_scoped_approval_hash=APPROVAL_HASH,
    ).content_hash == consumed.content_hash
    assert authority_path.read_bytes() == consumed_bytes
    assert _initialize(store).content_hash == consumed.content_hash

    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORIZATION_COMPLETION_CONFLICT",
    ):
        store.consume(
            run_id=RUN_ID,
            request_fingerprint=FINGERPRINT,
            completion_status="PROVIDER_ATTEMPT_FAILED",
            now=NOW + timedelta(seconds=4),
        )
    with pytest.raises(
        IMGCanarySecurityAuthorityError,
        match="IMG_CANARY_TASK_AUTHORITY_IDENTITY_CONFLICT",
    ):
        _initialize(
            store,
            run_id="img-canary-v3-20260718T103002Z-feedface",
        )


def test_v3_rejects_wrong_identity_run_prefix_and_incomplete_binding(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="IMG_CANARY_V3_TASK_AUTH_IDENTITY_MISMATCH"):
        IMGCanaryTaskAuthorizationStore(tmp_path / "wrong-identity.json").initialize(
            task_key=IMG_CANARY_V2_TASK_KEY,
            authorization_ref=IMG_CANARY_V2_AUTHORIZATION_REF,
            approval_version="V3",
            approved_run_id=RUN_ID,
            approved_request_fingerprint=FINGERPRINT,
            approved_prompt_hash=PROMPT_HASH,
            approved_serialized_body_hash=BODY_HASH,
            approved_scoped_approval_hash=APPROVAL_HASH,
            now=NOW,
        )
    with pytest.raises(ValidationError, match="IMG_CANARY_V3_TASK_AUTH_RUN_ID_MISMATCH"):
        _initialize(
            IMGCanaryTaskAuthorizationStore(tmp_path / "wrong-run.json"),
            run_id="img-canary-v2-20260718T103000Z-a1b2c3d4",
        )
    with pytest.raises(ValidationError, match="IMG_CANARY_V3_TASK_AUTH_BINDING_INCOMPLETE"):
        IMGCanaryTaskAuthorizationStore(tmp_path / "incomplete.json").initialize(
            task_key=IMG_CANARY_V3_TASK_KEY,
            authorization_ref=IMG_CANARY_V3_AUTHORIZATION_REF,
            approval_version="V3",
            approved_run_id=RUN_ID,
            now=NOW,
        )
