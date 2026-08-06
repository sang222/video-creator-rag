"""Shared deterministic authority helpers for current script qualification.

This module deliberately has no database dependency so every projection can
validate exactly the bytes sealed by the qualification service.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.config_registry import content_hash


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_memory_digest_body(digest: dict[str, Any]) -> dict[str, Any]:
    """Return the only hashable memory-digest body.

    ``digest_hash`` is a detached checksum.  It is intentionally never part of
    the material that it checks, which prevents self-referential hash drift.
    """

    return {key: value for key, value in dict(digest or {}).items() if key != "digest_hash"}


def canonical_memory_digest_hash(digest: dict[str, Any]) -> str:
    return content_hash(canonical_memory_digest_body(digest))


def validate_memory_digest(digest: Any, *, expected_hash: str | None = None) -> str:
    """Fail closed unless the stored detached digest hash is canonical."""

    if not isinstance(digest, dict):
        raise ValueError("SCRIPT_MEMORY_DIGEST_MISSING")
    stored = digest.get("digest_hash")
    if not isinstance(stored, str) or not SHA256_RE.fullmatch(stored):
        raise ValueError("SCRIPT_MEMORY_DIGEST_HASH_MALFORMED")
    calculated = canonical_memory_digest_hash(digest)
    if stored != calculated:
        raise ValueError("SCRIPT_MEMORY_DIGEST_HASH_MISMATCH")
    if expected_hash is not None:
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise ValueError("SCRIPT_MEMORY_DIGEST_EXPECTED_HASH_MALFORMED")
        if expected_hash != stored:
            raise ValueError("SCRIPT_MEMORY_DIGEST_DOWNSTREAM_HASH_MISMATCH")
    return stored


def hashed_payload(payload: dict[str, Any], hash_key: str) -> dict[str, Any]:
    """Attach a hash over the payload excluding its own hash field."""

    body = {key: value for key, value in payload.items() if key != hash_key}
    return {**body, hash_key: content_hash(body)}
