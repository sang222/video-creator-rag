from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResponse:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    latency_ms: int = 1


class LLMProvider(Protocol):
    provider_key: str

    def generate(self, *, prompt: str, mode: str = "success") -> ProviderResponse:
        ...


class TTSProvider(Protocol):
    provider_key: str

    def synthesize(self, *, text: str, mode: str = "success") -> ProviderResponse:
        ...


class MediaProvider(Protocol):
    provider_key: str

    def resolve_media(self, *, query: str, mode: str = "success") -> ProviderResponse:
        ...


class StorageProvider(Protocol):
    provider_key: str

    def store(self, *, object_key: str, payload_ref: str, mode: str = "success") -> ProviderResponse:
        ...


class ExternalPlatformProvider(Protocol):
    provider_key: str

    def check_platform(self, *, target_ref: str, mode: str = "success") -> ProviderResponse:
        ...


class AnalyticsProvider(Protocol):
    provider_key: str

    def fetch_metrics(self, *, metric_key: str, mode: str = "success") -> ProviderResponse:
        ...
