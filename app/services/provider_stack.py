from __future__ import annotations

from typing import Any


CANONICAL_PROVIDER_KEYS = ("elevenlabs", "luma_api", "pexels_api")
OPTIONAL_STORAGE_PROVIDER_KEYS = ("youtube_readonly", "google_drive_archive")
LOCAL_CAPABILITY_KEYS = ("native_ffmpeg_renderer",)

STALE_PROVIDER_KEYS = {
    "elevenlabs_flash_turbo",
    "google_vertex_veo",
    "google_vertex",
    "veo",
    "pexels_pixabay_free_fallback",
    "pixabay_free_fallback",
}

_CANONICAL_ALIASES = {
    "elevenlabs": "elevenlabs",
    "eleven_labs": "elevenlabs",
    "luma": "luma_api",
    "luma_api": "luma_api",
    "pexels": "pexels_api",
    "pexels_api": "pexels_api",
    "youtube_readonly": "youtube_readonly",
    "youtube_read_only": "youtube_readonly",
    "google_drive_archive": "google_drive_archive",
    "drive_archive": "google_drive_archive",
}


def provider_key_slug(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    return text or None


def normalize_provider_key(value: Any) -> str | None:
    slug = provider_key_slug(value)
    if slug is None:
        return None
    return _CANONICAL_ALIASES.get(slug, slug)


def is_canonical_provider_key(value: Any) -> bool:
    return normalize_provider_key(value) in set(CANONICAL_PROVIDER_KEYS)


def is_local_capability_key(value: Any) -> bool:
    return normalize_provider_key(value) in set(LOCAL_CAPABILITY_KEYS)


def is_stale_provider_key(value: Any) -> bool:
    slug = provider_key_slug(value)
    return bool(slug and slug in STALE_PROVIDER_KEYS)


def canonical_or_original(value: Any) -> str:
    return normalize_provider_key(value) or str(value or "")


def provider_key_rejection_reasons(value: Any, *, allow_optional_storage: bool = True) -> list[str]:
    normalized = normalize_provider_key(value)
    allowed = set(CANONICAL_PROVIDER_KEYS)
    if allow_optional_storage:
        allowed.update(OPTIONAL_STORAGE_PROVIDER_KEYS)
    if is_stale_provider_key(value):
        return ["STALE_PROVIDER_KEY_NOT_ACTIVE", "PROVIDER_STACK_DRIFT"]
    if normalized not in allowed:
        return ["UNKNOWN_PROVIDER_KEY"]
    return []
