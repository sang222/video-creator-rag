from abc import ABC, abstractmethod
import hashlib
import json
import math
import urllib.error
import urllib.request


class EmbeddingProvider(ABC):
    model = "abstract"
    version = "v0"

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class MockEmbeddingProvider(EmbeddingProvider):
    model = "mock-hash-embedding"
    version = "v1"

    def __init__(self, dimension: int | None = None) -> None:
        if dimension is None:
            from app.config.settings import get_settings

            dimension = get_settings().embedding_dimension
        if dimension <= 0:
            raise ValueError("mock embedding dimension must be positive")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        buckets = [0.0] * self.dimension
        for word in text.lower().split():
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            buckets[digest[0] % len(buckets)] += 1.0
        norm = math.sqrt(sum(v * v for v in buckets)) or 1.0
        return [round(v / norm, 6) for v in buckets]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    version = "v1"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def embed(self, text: str) -> list[float]:
        from app.config.settings import get_settings

        body = {"model": self.model, "input": text}
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=get_settings().agent_call_timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as exc:
            raise RuntimeError(f"embedding HTTP request failed: {exc}") from exc
        return [float(value) for value in raw["data"][0]["embedding"]]


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(y * y for y in b)) or 1.0))
