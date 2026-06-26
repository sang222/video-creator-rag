from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.providers.base import ProviderResponse


MOCK_ERROR_MODES = {
    "timeout": ("PROVIDER_TIMEOUT", "mock provider timeout", True),
    "quota_exceeded": ("PROVIDER_QUOTA_EXCEEDED", "mock provider quota exceeded", False),
    "malformed": ("MALFORMED_OUTPUT", "mock provider returned malformed output", False),
    "unavailable": ("PROVIDER_UNAVAILABLE", "mock provider unavailable", True),
    "retryable_error": ("RETRYABLE_PROVIDER_ERROR", "mock retryable provider error", True),
    "non_retryable_error": ("NON_RETRYABLE_PROVIDER_ERROR", "mock non-retryable provider error", False),
    "circuit_open": ("CIRCUIT_BREAKER_OPEN", "mock circuit breaker open", True),
}


class _MockProviderBase:
    provider_key: str

    def _response(self, *, mode: str, output: dict[str, Any]) -> ProviderResponse:
        if mode == "success":
            return ProviderResponse(ok=True, output=output, latency_ms=1)
        if mode in MOCK_ERROR_MODES:
            code, message, retryable = MOCK_ERROR_MODES[mode]
            malformed = {"raw": "not-json::mock"} if mode == "malformed" else {}
            return ProviderResponse(
                ok=False,
                output=malformed,
                error_code=code,
                error_message=message,
                retryable=retryable,
                latency_ms=50 if mode == "timeout" else 1,
            )
        return ProviderResponse(
            ok=False,
            error_code="UNKNOWN_MOCK_MODE",
            error_message=f"unknown mock mode: {mode}",
            retryable=False,
        )


class MockLLMProvider(_MockProviderBase):
    provider_key = "mock_llm"

    def generate(self, *, prompt: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "text": "mock llm contract response",
                "json": {"fixture": "mock_llm", "prompt_hash_hint": len(prompt)},
            },
        )


class MockTTSProvider(_MockProviderBase):
    provider_key = "mock_tts"

    def synthesize(self, *, text: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "audio_ref": "mock://tts/audio.wav",
                "duration_seconds": max(1, len(text) // 20),
            },
        )


class MockMediaProvider(_MockProviderBase):
    provider_key = "mock_media"

    def resolve_media(self, *, query: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "media_ref": "mock://media/asset-001",
                "query": query,
                "license_ref": "mock://license/media-001",
            },
        )


class MockStorageProvider(_MockProviderBase):
    provider_key = "mock_storage"

    def store(self, *, object_key: str, payload_ref: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "storage_ref": f"mock://storage/{object_key}",
                "payload_ref": payload_ref,
            },
        )


class MockPlatformProvider(_MockProviderBase):
    provider_key = "mock_platform"

    def check_platform(self, *, target_ref: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "platform_ref": "mock://platform/channel",
                "target_ref": target_ref,
                "status": "reachable",
            },
        )


class MockAnalyticsProvider(_MockProviderBase):
    provider_key = "mock_analytics"

    def fetch_metrics(self, *, metric_key: str, mode: str = "success") -> ProviderResponse:
        return self._response(
            mode=mode,
            output={
                "provider_key": self.provider_key,
                "metric_key": metric_key,
                "metrics": [{"name": "views", "value": 0, "freshness": "MOCK"}],
            },
        )

    def fetch_video_metrics(
        self,
        *,
        platform: str,
        platform_video_id: str,
        published_at: datetime,
        mode: str = "success",
    ) -> ProviderResponse:
        captured_at = datetime.now(UTC)
        observed_from = published_at
        observed_to = min(captured_at, published_at + timedelta(hours=24))
        metrics = {
            "views": 120,
            "impressions": 2400,
            "click_through_rate": 5.0,
            "average_view_duration_seconds": 42.0,
            "average_view_percentage": 64.0,
            "watch_time_minutes": 84.0,
            "likes": 12,
            "comments": 3,
            "shares": 2,
            "subscribers_gained": 1,
            "subscribers_lost": 0,
            "reach": 2100,
            "completion_rate": 52.0,
        }
        if platform in {"TIKTOK", "INSTAGRAM", "FACEBOOK", "GENERIC"}:
            metrics["saves"] = 4
        return self._response(
            mode=mode,
            output={
                "platform": platform,
                "platform_video_id": platform_video_id,
                "captured_at": captured_at.isoformat(),
                "observed_from": observed_from.isoformat(),
                "observed_to": observed_to.isoformat(),
                "observation_window": "T_PLUS_24H",
                "metrics": metrics,
                "metric_availability": {},
                "traffic_sources": [
                    {
                        "source_key": "browse",
                        "source_label": "Browse features",
                        "views": 60,
                        "impressions": 1200,
                        "watch_time_minutes": 42,
                        "percentage": 50,
                        "metadata": {"mock": True},
                    },
                    {
                        "source_key": "search",
                        "source_label": "Search",
                        "views": 60,
                        "impressions": 1200,
                        "watch_time_minutes": 42,
                        "percentage": 50,
                        "metadata": {"mock": True},
                    },
                ],
                "retention_curve": [
                    {"time_seconds": 0, "retention_percent": 100, "viewers_remaining_estimate": 120, "source_metric": "mock_retention"},
                    {"time_seconds": 5, "retention_percent": 72, "viewers_remaining_estimate": 86, "source_metric": "mock_retention"},
                    {"time_seconds": 10, "retention_percent": 58, "viewers_remaining_estimate": 69, "source_metric": "mock_retention"},
                ],
                "engagement": {},
                "provider_metadata": {
                    "provider_key": self.provider_key,
                    "mock": True,
                    "network_call": False,
                    "credentials_used": False,
                },
                "freshness_state": "FRESH",
                "confidence_level": "HIGH",
            },
        )


def mock_provider_for_key(provider_key: str):
    mapping = {
        "mock_llm": MockLLMProvider,
        "mock_tts": MockTTSProvider,
        "mock_media": MockMediaProvider,
        "mock_storage": MockStorageProvider,
        "mock_platform": MockPlatformProvider,
        "mock_analytics": MockAnalyticsProvider,
    }
    provider = mapping.get(provider_key)
    if provider is None:
        raise KeyError(f"mock provider not found: {provider_key}")
    return provider()


def run_mock_contract(provider_key: str, *, operation_key: str, mode: str = "success") -> ProviderResponse:
    provider = mock_provider_for_key(provider_key)
    if provider_key == "mock_llm":
        return provider.generate(prompt=f"contract:{operation_key}", mode=mode)
    if provider_key == "mock_tts":
        return provider.synthesize(text=f"contract:{operation_key}", mode=mode)
    if provider_key == "mock_media":
        return provider.resolve_media(query=f"contract:{operation_key}", mode=mode)
    if provider_key == "mock_storage":
        return provider.store(object_key=operation_key, payload_ref="mock://payload/contract", mode=mode)
    if provider_key == "mock_platform":
        return provider.check_platform(target_ref=f"contract:{operation_key}", mode=mode)
    if provider_key == "mock_analytics":
        return provider.fetch_metrics(metric_key=operation_key, mode=mode)
    raise KeyError(f"mock provider not found: {provider_key}")
