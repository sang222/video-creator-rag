from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.providers.base import ProviderResponse


FAKE_ERROR_MODES = {
    "timeout": ("PROVIDER_TIMEOUT", "fake provider timeout", True),
    "quota_exceeded": ("PROVIDER_QUOTA_EXCEEDED", "fake provider quota exceeded", False),
    "malformed": ("MALFORMED_OUTPUT", "fake provider returned malformed output", False),
    "unavailable": ("PROVIDER_UNAVAILABLE", "fake provider unavailable", True),
    "retryable_error": ("RETRYABLE_PROVIDER_ERROR", "fake retryable provider error", True),
    "non_retryable_error": ("NON_RETRYABLE_PROVIDER_ERROR", "fake non-retryable provider error", False),
    "circuit_open": ("CIRCUIT_BREAKER_OPEN", "fake circuit breaker open", True),
}


class _FakeProviderBase:
    provider_key: str

    def _response(self, *, mode: str, output: dict[str, Any]) -> ProviderResponse:
        if mode == "success":
            return ProviderResponse(ok=True, output=output, latency_ms=1)
        if mode in FAKE_ERROR_MODES:
            code, message, retryable = FAKE_ERROR_MODES[mode]
            malformed = {"raw": "not-json::fake"} if mode == "malformed" else {}
            return ProviderResponse(
                ok=False,
                output=malformed,
                error_code=code,
                error_message=message,
                retryable=retryable,
                latency_ms=50 if mode == "timeout" else 1,
            )
        return ProviderResponse(ok=False, error_code="UNKNOWN_FAKE_MODE", error_message=f"unknown fake mode: {mode}")


class FakeLLMProvider(_FakeProviderBase):
    provider_key = "fake_llm"

    def generate(self, *, prompt: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "text": "fake llm contract response",
                "json": {"fixture": "fake_llm", "prompt_hash_hint": len(prompt)},
            },
        )


class FakeTTSProvider(_FakeProviderBase):
    provider_key = "fake_tts"

    def synthesize(self, *, text: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={"provider_key": self.provider_key, "audio_ref": "fake://tts/audio.wav", "duration_seconds": max(1, len(text) // 20)},
        )


class FakeMediaProvider(_FakeProviderBase):
    provider_key = "fake_media"

    def resolve_media(self, *, query: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={"provider_key": self.provider_key, "media_ref": "fake://media/asset-001", "query": query},
        )


class FakeStorageProvider(_FakeProviderBase):
    provider_key = "fake_storage"

    def store(self, *, object_key: str, payload_ref: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={"provider_key": self.provider_key, "storage_ref": f"fake://storage/{object_key}", "payload_ref": payload_ref},
        )


class FakePlatformProvider(_FakeProviderBase):
    provider_key = "fake_platform"

    def check_platform(self, *, target_ref: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={"provider_key": self.provider_key, "platform_ref": "fake://platform/channel", "target_ref": target_ref},
        )


class FakeAnalyticsProvider(_FakeProviderBase):
    provider_key = "fake_analytics"

    def fetch_metrics(self, *, metric_key: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={"provider_key": self.provider_key, "metric_key": metric_key, "metrics": [{"name": "views", "value": 0}]},
        )

    def fetch_video_metrics(self, *, platform: str, platform_video_id: str, published_at: datetime, mode: str = "success") -> ProviderResponse:
        captured_at = datetime.now(UTC)
        return self._response(
            mode=mode,
            output={
                "platform": platform,
                "platform_video_id": platform_video_id,
                "captured_at": captured_at.isoformat(),
                "observed_from": published_at.isoformat(),
                "observed_to": min(captured_at, published_at + timedelta(hours=24)).isoformat(),
                "observation_window": "T_PLUS_24H",
                "metrics": {"views": 120, "likes": 12, "comments": 3},
                "metric_availability": {},
                "traffic_sources": [],
                "retention_curve": [],
                "engagement": {},
                "provider_metadata": {"provider_key": self.provider_key, "network_call": False},
                "freshness_state": "FRESH",
                "confidence_level": "HIGH",
            },
        )


def fake_provider_for_key(provider_key: str):
    mapping = {
        "fake_llm": FakeLLMProvider,
        "fake_tts": FakeTTSProvider,
        "fake_media": FakeMediaProvider,
        "fake_storage": FakeStorageProvider,
        "fake_platform": FakePlatformProvider,
        "fake_analytics": FakeAnalyticsProvider,
    }
    provider = mapping.get(provider_key)
    if provider is None:
        raise KeyError(f"fake provider not found: {provider_key}")
    return provider()
