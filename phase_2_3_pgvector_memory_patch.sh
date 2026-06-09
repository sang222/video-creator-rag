#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

ROOT = Path.cwd()


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise SystemExit(f"expected block not found in {path}")
    target.write_text(text.replace(old, new), encoding="utf-8")


write(
    "app/config/settings.py",
    '''from functools import lru_cache
from pathlib import Path
import os


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.app_name = "Company-level Editorial Content Operating System"
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./phase1.db")
        self.policy_dir = Path(os.getenv("POLICY_DIR", "app/policies"))
        self.storage_root = Path(os.getenv("STORAGE_ROOT", "local_storage"))
        self.skill_dir = Path(os.getenv("SKILL_DIR", "app/skills"))
        self.agent_runtime_mode = os.getenv("AGENT_RUNTIME_MODE", "mock").lower()
        self.use_mock_providers = _env_bool("USE_MOCK_PROVIDERS", "true")
        self.allow_provider_fallback_to_mock = _env_bool("ALLOW_PROVIDER_FALLBACK_TO_MOCK", "true")
        self.validate_skill_packs_on_startup = _env_bool("VALIDATE_SKILL_PACKS_ON_STARTUP", "false")
        self.llm_provider = os.getenv("LLM_PROVIDER", "mock")
        self.llm_base_url = os.getenv("LLM_BASE_URL")
        self.llm_api_key = os.getenv("LLM_API_KEY")
        self.llm_model = os.getenv("LLM_MODEL", "mock-llm")
        self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "mock")
        self.embedding_base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("LLM_BASE_URL")
        self.embedding_api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY")
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mock-hash-embedding")
        self.embedding_dimension = int(os.getenv("EMBEDDING_DIMENSION", "16"))
        self.memory_retrieval_backend = os.getenv("MEMORY_RETRIEVAL_BACKEND", "auto").lower()
        self.enable_pgvector = _env_bool("ENABLE_PGVECTOR", "true")
        self.memory_vector_top_k = int(os.getenv("MEMORY_VECTOR_TOP_K", "5"))
        self.memory_keyword_fallback_enabled = _env_bool("MEMORY_KEYWORD_FALLBACK_ENABLED", "true")
        self.max_agent_context_tokens = int(os.getenv("MAX_AGENT_CONTEXT_TOKENS", "6000"))
        self.max_skill_context_tokens = int(os.getenv("MAX_SKILL_CONTEXT_TOKENS", "1200"))
        self.max_memory_context_tokens = int(os.getenv("MAX_MEMORY_CONTEXT_TOKENS", "2500"))
        self.max_constitution_tokens = int(os.getenv("MAX_CONSTITUTION_TOKENS", "1000"))
        self.max_task_input_tokens = int(os.getenv("MAX_TASK_INPUT_TOKENS", "1500"))
        self.max_repair_attempts = int(os.getenv("MAX_REPAIR_ATTEMPTS", "1"))
        self.agent_call_timeout_seconds = int(os.getenv("AGENT_CALL_TIMEOUT_SECONDS", "120"))

        if self.embedding_dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if self.memory_retrieval_backend not in {"auto", "pgvector", "fallback"}:
            raise ValueError("MEMORY_RETRIEVAL_BACKEND must be auto, pgvector, or fallback")
        if self.memory_vector_top_k <= 0:
            raise ValueError("MEMORY_VECTOR_TOP_K must be positive")


@lru_cache
def get_settings() -> Settings:
    return Settings()
''',
)

write(
    "app/providers/embedding.py",
    '''from abc import ABC, abstractmethod
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
''',
)

write(
    "scripts/generate_openapi.py",
    '''import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(sys.prefix) / "lib" / f"python{version}" / "site-packages",
        Path(sys.base_prefix) / "lib" / f"python{version}" / "site-packages",
    ]
    framework_root = Path("/Library/Frameworks/Python.framework/Versions")
    if framework_root.exists():
        candidates.extend(framework_root.glob(f"*/lib/python{version}/site-packages"))
    for candidate in candidates:
        if (candidate / "fastapi").exists() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))
            break

from app.main import app  # noqa: E402


def main() -> None:
    schema = app.openapi()
    output_path = Path("openapi.json")
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True, default=str) + "\\n", encoding="utf-8")
    print(f"generated {output_path} with {len(schema.get('paths', {}))} paths")


if __name__ == "__main__":
    main()
''',
)

replace(
    "app/providers/factory.py",
    "    if runtime_mode == \"mock\":\n        return MockEmbeddingProvider()\n",
    "    if runtime_mode == \"mock\":\n        return MockEmbeddingProvider(dimension=settings.embedding_dimension)\n",
)
replace(
    "app/providers/factory.py",
    "            return _with_fallback_reason(MockEmbeddingProvider(), reason)\n",
    "            return _with_fallback_reason(MockEmbeddingProvider(dimension=settings.embedding_dimension), reason)\n",
)
replace(
    "app/providers/factory.py",
    "        return _with_fallback_reason(MockEmbeddingProvider(), reason) if runtime_mode in {\"hybrid\", \"real_text\"} else MockEmbeddingProvider()\n",
    "        return _with_fallback_reason(MockEmbeddingProvider(dimension=settings.embedding_dimension), reason) if runtime_mode in {\"hybrid\", \"real_text\"} else MockEmbeddingProvider(dimension=settings.embedding_dimension)\n",
)

write(
    "app/services/memory_retrieval.py",
    '''from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.core.enums import MemoryFamily, MemoryScope
from app.models.entities import ChannelWorkspace, MemoryItem
from app.providers.embedding import EmbeddingProvider, cosine
from app.providers.factory import get_embedding_provider


ALL_MEMORY_FAMILIES = {family.value for family in MemoryFamily}

ROLE_MEMORY_FAMILY_POLICY: dict[str, set[str]] = {
    "authority": {
        MemoryFamily.MONETIZATION.value,
        MemoryFamily.COMPLIANCE.value,
        MemoryFamily.BRAND_IDENTITY.value,
        MemoryFamily.AUDIENCE.value,
        MemoryFamily.EDITORIAL_PLAYBOOK.value,
        MemoryFamily.PERFORMANCE.value,
    },
    "script": {
        MemoryFamily.BRAND_IDENTITY.value,
        MemoryFamily.AUDIENCE.value,
        MemoryFamily.UPLOADED_VIDEO.value,
        MemoryFamily.EDITORIAL_PLAYBOOK.value,
        MemoryFamily.REUSABLE_ASSET.value,
    },
    "script_critic": {
        MemoryFamily.COMPLIANCE.value,
        MemoryFamily.AUDIENCE.value,
        MemoryFamily.PERFORMANCE.value,
        MemoryFamily.DIAGNOSIS.value,
        MemoryFamily.PRODUCTION_FAILURE.value,
    },
    "monetization_strategy": {
        MemoryFamily.MONETIZATION.value,
        MemoryFamily.PERFORMANCE.value,
        MemoryFamily.AUDIENCE.value,
        MemoryFamily.BASELINE.value,
    },
    "seo_metadata": {
        MemoryFamily.AUDIENCE.value,
        MemoryFamily.PERFORMANCE.value,
        MemoryFamily.EDITORIAL_PLAYBOOK.value,
        MemoryFamily.UPLOADED_VIDEO.value,
    },
    "publishing_content": {
        MemoryFamily.BRAND_IDENTITY.value,
        MemoryFamily.AUDIENCE.value,
        MemoryFamily.EDITORIAL_PLAYBOOK.value,
        MemoryFamily.COMPLIANCE.value,
    },
    "compliance": {
        MemoryFamily.COMPLIANCE.value,
        MemoryFamily.MONETIZATION.value,
        MemoryFamily.UPLOADED_VIDEO.value,
        MemoryFamily.PRODUCTION_FAILURE.value,
    },
    "memory_curator": set(ALL_MEMORY_FAMILIES),
    "generic": set(ALL_MEMORY_FAMILIES),
}


@dataclass
class RankedMemoryItem:
    item: MemoryItem
    score: float
    reason: str


@dataclass
class MemoryRetrievalResult:
    items: list[MemoryItem]
    backend_used: str
    retrieval_trace: dict[str, Any]
    ranked_items: list[RankedMemoryItem] = field(default_factory=list)


def normalize_agent_role(agent_role: str | None) -> str:
    value = (agent_role or "generic").strip().lower()
    aliases = {
        "authorityagent": "authority",
        "authority_agent": "authority",
        "scriptagent": "script",
        "script_agent": "script",
        "scriptcriticagent": "script_critic",
        "script_critic_agent": "script_critic",
        "monetizationstrategyagent": "monetization_strategy",
        "monetization_strategy_agent": "monetization_strategy",
        "seometadataagent": "seo_metadata",
        "seo_metadata_agent": "seo_metadata",
        "publishingcontentagent": "publishing_content",
        "publishing_content_agent": "publishing_content",
        "compliancecopyrightagent": "compliance",
        "compliance_agent": "compliance",
        "memorycuratoragent": "memory_curator",
        "memory_curator_agent": "memory_curator",
    }
    return aliases.get(value, value)


def allowed_families_for_role(agent_role: str | None) -> set[str]:
    return ROLE_MEMORY_FAMILY_POLICY.get(normalize_agent_role(agent_role), ROLE_MEMORY_FAMILY_POLICY["generic"])


def default_scopes_for_context(workspace_context: dict) -> list[str]:
    scopes = [MemoryScope.WORKSPACE_ONLY.value, MemoryScope.COMPANY_GLOBAL.value]
    if workspace_context.get("platform"):
        scopes.append(MemoryScope.PLATFORM_GLOBAL.value)
    return scopes


def platform_from_metadata(metadata: dict | None) -> str | None:
    metadata = metadata or {}
    platform = metadata.get("platform")
    if isinstance(platform, str):
        return platform.lower()
    platforms = metadata.get("platforms")
    if isinstance(platforms, list) and platforms:
        return str(platforms[0]).lower()
    return None


def is_platform_global_visible(item: MemoryItem, *, company_id: str, platform: str | None) -> bool:
    if item.scope != MemoryScope.PLATFORM_GLOBAL.value:
        return True
    if item.company_id != company_id or not platform:
        return False
    item_platform = platform_from_metadata(item.metadata_json or {})
    return bool(item_platform and item_platform == platform.lower())


def validate_memory_scope_payload(*, company_id: str | None, workspace_id: str | None, scope: str, metadata_json: dict) -> None:
    if scope == MemoryScope.WORKSPACE_ONLY.value and not workspace_id:
        raise ValueError("workspace_id is required for workspace_only memory")
    if scope == MemoryScope.COMPANY_GLOBAL.value and not company_id:
        raise ValueError("company_id is required for company_global memory")
    if scope == MemoryScope.PLATFORM_GLOBAL.value and not platform_from_metadata(metadata_json or {}):
        raise ValueError("platform_global memory requires metadata_json.platform")


def validate_embedding_vector(
    embedding: list[float] | tuple[float, ...] | None,
    *,
    expected_dimension: int | None = None,
    label: str = "embedding",
) -> list[float]:
    if embedding is None:
        raise ValueError(f"{label} is required")
    if not isinstance(embedding, (list, tuple)):
        raise ValueError(f"{label} must be a list of floats")
    values = [float(value) for value in embedding]
    if expected_dimension is not None and len(values) != expected_dimension:
        raise ValueError(
            f"{label} dimension mismatch: expected {expected_dimension}, got {len(values)}"
        )
    return values


def _format_pgvector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in embedding) + "]"


class MemoryRetrievalService:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedding_provider = embedding_provider or get_embedding_provider(self.settings)

    def embed_item(self, db: Session, item: MemoryItem) -> MemoryItem:
        embedding = self.embedding_provider.embed(f"{item.title}\\n{item.content}")
        item.embedding = validate_embedding_vector(
            embedding,
            expected_dimension=self.settings.embedding_dimension,
            label="memory embedding",
        )
        item.embedding_model = self.embedding_provider.model
        item.embedding_version = self.embedding_provider.version
        item.embedded_at = datetime.now(timezone.utc)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def is_pgvector_available(self, db: Session) -> tuple[bool, str | None]:
        if not self.settings.enable_pgvector:
            return False, "ENABLE_PGVECTOR=false"
        if self.settings.memory_retrieval_backend == "fallback":
            return False, "MEMORY_RETRIEVAL_BACKEND=fallback"
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return False, f"dialect={bind.dialect.name}"
        try:
            extension_exists = bool(
                db.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")).scalar()
            )
            if not extension_exists:
                return False, "pgvector extension is not installed"
            column_type = db.execute(
                text(
                    """
                    SELECT udt_name
                    FROM information_schema.columns
                    WHERE table_name = 'memory_items'
                      AND column_name = 'embedding'
                    """
                )
            ).scalar()
            if column_type != "vector":
                return False, "memory_items.embedding is not a vector column"
        except SQLAlchemyError as exc:
            return False, f"pgvector availability check failed: {exc}"
        return True, None

    def retrieve(
        self,
        db: Session,
        *,
        company_id: str,
        workspace_id: str,
        query: str,
        query_embedding: list[float] | None = None,
        scopes: list[str] | None = None,
        families: list[str] | None = None,
        agent_role: str | None = "generic",
        workspace_context: dict | None = None,
        top_k: int | None = None,
        limit: int | None = None,
    ) -> MemoryRetrievalResult:
        workspace_context = dict(workspace_context or {})
        workspace_context.setdefault("company_id", company_id)
        workspace_context.setdefault("workspace_id", workspace_id)
        platform = workspace_context.get("platform")
        requested_scopes = scopes or default_scopes_for_context(workspace_context)
        requested_limit = top_k or limit or self.settings.memory_vector_top_k

        allowed_role_families = allowed_families_for_role(agent_role)
        selected_families = set(families or allowed_role_families)
        disallowed = selected_families - allowed_role_families
        if families and disallowed:
            raise ValueError(
                f"requested memory families are not allowed for agent_role={agent_role}: {sorted(disallowed)}"
            )

        trace: dict[str, Any] = {
            "scope_filter_applied": True,
            "family_filter_applied": bool(selected_families),
            "platform_filter_applied": bool(platform),
            "query_embedding_present": query_embedding is not None,
            "pgvector_available": False,
            "fallback_reason": None,
            "candidate_count": 0,
            "returned_count": 0,
        }

        candidates = self._load_scope_filtered_candidates(
            db,
            company_id=company_id,
            workspace_id=workspace_id,
            platform=platform,
            scopes=requested_scopes,
            families=selected_families,
        )
        trace["candidate_count"] = len(candidates)
        if not candidates:
            trace["returned_count"] = 0
            return MemoryRetrievalResult(items=[], backend_used="fallback_keyword", retrieval_trace=trace)

        if query_embedding is None:
            try:
                query_embedding = validate_embedding_vector(
                    self.embedding_provider.embed(query),
                    expected_dimension=self.settings.embedding_dimension,
                    label="query embedding",
                )
                trace["query_embedding_present"] = True
            except (RuntimeError, ValueError) as exc:
                trace["fallback_reason"] = f"query embedding unavailable: {exc}"
                query_embedding = None
        else:
            query_embedding = validate_embedding_vector(
                query_embedding,
                expected_dimension=self.settings.embedding_dimension,
                label="query embedding",
            )
            trace["query_embedding_present"] = True

        pgvector_available, unavailable_reason = self.is_pgvector_available(db)
        trace["pgvector_available"] = pgvector_available

        if self.settings.memory_retrieval_backend == "pgvector" and not pgvector_available:
            raise RuntimeError(f"pgvector memory retrieval requested but unavailable: {unavailable_reason}")

        if pgvector_available and query_embedding is not None:
            try:
                ranked = self._retrieve_pgvector(
                    db,
                    candidates=candidates,
                    query_embedding=query_embedding,
                    limit=requested_limit,
                )
                trace["returned_count"] = len(ranked)
                return MemoryRetrievalResult(
                    items=[ranked_item.item for ranked_item in ranked],
                    backend_used="pgvector",
                    retrieval_trace=trace,
                    ranked_items=ranked,
                )
            except SQLAlchemyError as exc:
                if self.settings.memory_retrieval_backend == "pgvector":
                    raise RuntimeError(f"pgvector memory retrieval failed: {exc}") from exc
                trace["fallback_reason"] = f"pgvector query failed: {exc}"
        elif unavailable_reason:
            trace["fallback_reason"] = unavailable_reason

        if not self.settings.memory_keyword_fallback_enabled:
            trace["fallback_reason"] = trace["fallback_reason"] or "keyword fallback disabled"
            trace["returned_count"] = 0
            return MemoryRetrievalResult(items=[], backend_used="fallback_keyword", retrieval_trace=trace)

        ranked, backend = self._retrieve_fallback(
            candidates,
            query=query,
            query_embedding=query_embedding,
            limit=requested_limit,
        )
        trace["returned_count"] = len(ranked)
        return MemoryRetrievalResult(
            items=[ranked_item.item for ranked_item in ranked],
            backend_used=backend,
            retrieval_trace=trace,
            ranked_items=ranked,
        )

    def _load_scope_filtered_candidates(
        self,
        db: Session,
        *,
        company_id: str,
        workspace_id: str,
        platform: str | None,
        scopes: list[str],
        families: set[str],
    ) -> list[MemoryItem]:
        scope_filters = []
        now = datetime.now(timezone.utc)
        if MemoryScope.WORKSPACE_ONLY.value in scopes:
            scope_filters.append(
                (MemoryItem.scope == MemoryScope.WORKSPACE_ONLY.value)
                & (MemoryItem.company_id == company_id)
                & (MemoryItem.workspace_id == workspace_id)
            )
        if MemoryScope.COMPANY_GLOBAL.value in scopes:
            scope_filters.append(
                (MemoryItem.scope == MemoryScope.COMPANY_GLOBAL.value)
                & (MemoryItem.company_id == company_id)
            )
        if MemoryScope.PLATFORM_GLOBAL.value in scopes and platform:
            scope_filters.append(
                (MemoryItem.scope == MemoryScope.PLATFORM_GLOBAL.value)
                & (MemoryItem.company_id == company_id)
            )
        if not scope_filters:
            return []

        stmt = select(MemoryItem).where(
            or_(*scope_filters),
            MemoryItem.status == "ACTIVE",
            or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > now),
        )
        if families:
            stmt = stmt.where(MemoryItem.family.in_(families))
        items = list(db.scalars(stmt))
        return [
            item
            for item in items
            if is_platform_global_visible(item, company_id=company_id, platform=platform)
        ]

    def _retrieve_pgvector(
        self,
        db: Session,
        *,
        candidates: list[MemoryItem],
        query_embedding: list[float],
        limit: int,
    ) -> list[RankedMemoryItem]:
        candidate_ids = [item.id for item in candidates]
        item_by_id = {item.id: item for item in candidates}
        stmt = text(
            """
            SELECT id, embedding <-> CAST(:query_embedding AS vector) AS distance
            FROM memory_items
            WHERE id IN :candidate_ids
              AND embedding IS NOT NULL
            ORDER BY embedding <-> CAST(:query_embedding AS vector)
            LIMIT :limit
            """
        ).bindparams(bindparam("candidate_ids", expanding=True))
        rows = db.execute(
            stmt,
            {
                "candidate_ids": candidate_ids,
                "query_embedding": _format_pgvector_literal(query_embedding),
                "limit": limit,
            },
        ).mappings()
        ranked: list[RankedMemoryItem] = []
        for row in rows:
            item = item_by_id.get(row["id"])
            if item is None:
                continue
            distance = float(row["distance"])
            ranked.append(
                RankedMemoryItem(
                    item=item,
                    score=1.0 / (1.0 + distance),
                    reason=f"pgvector_l2_distance={distance:.6f}",
                )
            )
        return ranked

    def _retrieve_fallback(
        self,
        candidates: list[MemoryItem],
        *,
        query: str,
        query_embedding: list[float] | None,
        limit: int,
    ) -> tuple[list[RankedMemoryItem], str]:
        query_words = {word for word in query.lower().split() if word}
        has_candidate_embeddings = any(
            isinstance(item.embedding, list)
            and len(item.embedding) == self.settings.embedding_dimension
            for item in candidates
        )
        backend = "fallback_embedding_scan" if query_embedding is not None and has_candidate_embeddings else "fallback_keyword"

        def scope_rank(item: MemoryItem) -> int:
            return {
                MemoryScope.WORKSPACE_ONLY.value: 0,
                MemoryScope.COMPANY_GLOBAL.value: 1,
                MemoryScope.PLATFORM_GLOBAL.value: 2,
            }.get(item.scope, 9)

        ranked: list[RankedMemoryItem] = []
        for item in candidates:
            text_value = f"{item.title} {item.summary or ''} {item.content}".lower()
            keyword_score = float(sum(1 for word in query_words if word in text_value))
            vector_score = 0.0
            if backend == "fallback_embedding_scan":
                vector_score = cosine(item.embedding, query_embedding)
            score = keyword_score + vector_score + float(item.confidence or 0.0) * 0.01
            reason = f"keyword={keyword_score:.3f}"
            if backend == "fallback_embedding_scan":
                reason += f", cosine={vector_score:.6f}"
            ranked.append(RankedMemoryItem(item=item, score=score, reason=reason))

        ranked.sort(key=lambda ranked_item: (scope_rank(ranked_item.item), -ranked_item.score, -ranked_item.item.confidence))
        return ranked[:limit], backend


def build_workspace_context_from_id(db: Session, workspace_id: str) -> dict:
    workspace = db.get(ChannelWorkspace, workspace_id)
    if not workspace:
        raise KeyError(f"workspace not found: {workspace_id}")
    return {
        "company_id": workspace.company_id,
        "workspace_id": workspace.id,
        "platform": workspace.platform,
    }
''',
)

write(
    "app/memory/router.py",
    '''from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import MemoryItem
from app.providers.embedding import EmbeddingProvider
from app.services.memory_retrieval import (
    ALL_MEMORY_FAMILIES,
    ROLE_MEMORY_FAMILY_POLICY,
    MemoryRetrievalResult,
    MemoryRetrievalService,
    allowed_families_for_role,
    build_workspace_context_from_id,
    default_scopes_for_context,
    is_platform_global_visible,
    normalize_agent_role,
    platform_from_metadata,
    validate_memory_scope_payload,
)


class MemoryRouter:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        retrieval_service: MemoryRetrievalService | None = None,
    ) -> None:
        self.retrieval_service = retrieval_service or MemoryRetrievalService(embedding_provider=embedding_provider)
        self.embedding_provider = self.retrieval_service.embedding_provider

    def embed_item(self, db: Session, item: MemoryItem) -> MemoryItem:
        return self.retrieval_service.embed_item(db, item)

    def retrieve_memory_result(
        self,
        db: Session,
        *,
        agent_role: str,
        workspace_context: dict,
        query: str,
        families: list[str] | None = None,
        scopes: list[str] | None = None,
        limit: int = 5,
        query_embedding: list[float] | None = None,
    ) -> MemoryRetrievalResult:
        return self.retrieval_service.retrieve(
            db,
            company_id=workspace_context["company_id"],
            workspace_id=workspace_context["workspace_id"],
            query=query,
            query_embedding=query_embedding,
            scopes=scopes,
            families=families,
            agent_role=agent_role,
            workspace_context=workspace_context,
            limit=limit,
        )

    def retrieve_memory(
        self,
        db: Session,
        *,
        agent_role: str,
        workspace_context: dict,
        query: str,
        families: list[str] | None = None,
        scopes: list[str] | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        return self.retrieve_memory_result(
            db,
            agent_role=agent_role,
            workspace_context=workspace_context,
            query=query,
            families=families,
            scopes=scopes,
            limit=limit,
        ).items

    def build_context_pack(
        self,
        db: Session | None = None,
        *,
        agent_role: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        query: str | None = None,
        families: list[str] | None = None,
        scopes: list[str] | None = None,
        workspace_context: dict | None = None,
        limit: int = 5,
        memories: list[MemoryItem] | None = None,
    ) -> dict:
        retrieval_result: MemoryRetrievalResult | None = None
        if memories is None and isinstance(db, list):
            memories = db
            db = None
        if memories is None:
            if db is None or query is None:
                raise ValueError("db and query are required when memories are not provided")
            if workspace_context is None:
                if not workspace_id:
                    raise ValueError("workspace_id or workspace_context is required when memories are not provided")
                workspace_context = build_workspace_context_from_id(db, workspace_id)
            retrieval_result = self.retrieve_memory_result(
                db,
                agent_role=agent_role or "generic",
                workspace_context=workspace_context,
                query=query,
                families=families or [],
                scopes=scopes,
                limit=limit,
            )
            memories = retrieval_result.items
            workspace_id = workspace_context.get("workspace_id", workspace_id)
        pack = {
            "agent_role": agent_role,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "query": query,
            "allowed_families": sorted(allowed_families_for_role(agent_role)),
            "scopes": scopes,
            "memory_count": len(memories),
            "items": [
                {
                    "id": item.id,
                    "scope": item.scope,
                    "family": item.family,
                    "title": item.title,
                    "summary": item.summary or item.content[:240],
                    "confidence": item.confidence,
                }
                for item in memories
            ],
        }
        if retrieval_result is not None:
            pack["retrieval_backend"] = retrieval_result.backend_used
            pack["retrieval_trace"] = retrieval_result.retrieval_trace
            pack["retrieval_scores"] = [
                {"id": ranked.item.id, "score": ranked.score, "reason": ranked.reason}
                for ranked in retrieval_result.ranked_items
            ]
        return pack
''',
)

replace(
    "app/models/entities.py",
    "from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint\nfrom sqlalchemy.orm import Mapped, mapped_column, relationship\n\nfrom app.db.base import Base\n",
    "from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint\nfrom sqlalchemy.orm import Mapped, mapped_column, relationship\nfrom sqlalchemy.types import TypeDecorator\n\nfrom app.config.settings import get_settings\nfrom app.db.base import Base\n\n\nclass EmbeddingVectorType(TypeDecorator):\n    impl = JSON\n    cache_ok = True\n\n    def __init__(self) -> None:\n        super().__init__()\n        self.dimension = get_settings().embedding_dimension\n\n    def load_dialect_impl(self, dialect):\n        if dialect.name == \"postgresql\":\n            try:\n                from pgvector.sqlalchemy import Vector\n\n                return dialect.type_descriptor(Vector(self.dimension))\n            except ImportError:\n                return dialect.type_descriptor(JSON())\n        return dialect.type_descriptor(JSON())\n",
)
replace(
    "app/models/entities.py",
    "    embedding: Mapped[list | None] = mapped_column(JSON)\n    embedding_model: Mapped[str | None] = mapped_column(String(128))\n    embedding_version: Mapped[str | None] = mapped_column(String(64))\n",
    "    embedding: Mapped[list | None] = mapped_column(EmbeddingVectorType())\n    embedding_model: Mapped[str | None] = mapped_column(String(128))\n    embedding_version: Mapped[str | None] = mapped_column(String(64))\n    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))\n",
)

write(
    "app/db/session.py",
    '''from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def _engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


settings = get_settings()
engine = create_engine(settings.database_url, future=True, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _table_columns(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column_name: str, ddl: str) -> None:
    if column_name not in _table_columns(table_name):
        with engine.begin() as connection:
            connection.execute(text(ddl))


def _ensure_sqlite_compatibility() -> None:
    _add_column_if_missing(
        "cost_events",
        "raw_usage_json",
        "ALTER TABLE cost_events ADD COLUMN raw_usage_json JSON NOT NULL DEFAULT '{}'",
    )
    _add_column_if_missing(
        "memory_items",
        "embedded_at",
        "ALTER TABLE memory_items ADD COLUMN embedded_at DATETIME",
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE workspace_operational_constitutions
                SET active = 0
                WHERE active = 1
                  AND id NOT IN (
                    SELECT id FROM (
                      SELECT id,
                             ROW_NUMBER() OVER (
                               PARTITION BY workspace_id
                               ORDER BY created_at DESC, id DESC
                             ) AS rn
                      FROM workspace_operational_constitutions
                      WHERE active = 1
                    )
                    WHERE rn = 1
                  )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_workspace_active_constitution
                ON workspace_operational_constitutions(workspace_id)
                WHERE active = 1
                """
            )
        )


def _ensure_postgres_compatibility() -> None:
    if settings.enable_pgvector:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    _add_column_if_missing(
        "memory_items",
        "embedded_at",
        "ALTER TABLE memory_items ADD COLUMN embedded_at TIMESTAMPTZ",
    )
    if settings.enable_pgvector and "embedding" not in _table_columns("memory_items"):
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE memory_items ADD COLUMN embedding vector({settings.embedding_dimension})"))

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE workspace_operational_constitutions
                SET active = false
                WHERE active = true
                  AND id NOT IN (
                    SELECT id FROM (
                      SELECT id,
                             ROW_NUMBER() OVER (
                               PARTITION BY workspace_id
                               ORDER BY created_at DESC, id DESC
                             ) AS rn
                      FROM workspace_operational_constitutions
                      WHERE active = true
                    ) ranked
                    WHERE rn = 1
                  )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_workspace_active_constitution
                ON workspace_operational_constitutions(workspace_id)
                WHERE active = true
                """
            )
        )
    if settings.enable_pgvector:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_hnsw
                        ON memory_items
                        USING hnsw (embedding vector_l2_ops)
                        WHERE embedding IS NOT NULL
                        """
                    )
                )
        except SQLAlchemyError:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_ivfflat
                        ON memory_items
                        USING ivfflat (embedding vector_l2_ops)
                        WITH (lists = 100)
                        WHERE embedding IS NOT NULL
                        """
                    )
                )


def init_db() -> None:
    from app.models import entities  # noqa: F401
    from app.db.base import Base

    backend = engine.url.get_backend_name()
    if backend == "postgresql" and settings.enable_pgvector:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    Base.metadata.create_all(bind=engine)

    if backend == "sqlite":
        _ensure_sqlite_compatibility()
    elif backend == "postgresql":
        _ensure_postgres_compatibility()
''',
)

replace(
    "app/schemas/api.py",
    "    embedding_model: str | None\n    embedding_version: str | None\n    created_at: datetime\n",
    "    embedding_model: str | None\n    embedding_version: str | None\n    embedded_at: datetime | None\n    created_at: datetime\n",
)
replace(
    "app/schemas/api.py",
    "    scopes: list[str] = Field(default_factory=lambda: [\"workspace_only\", \"company_global\", \"platform_global\"])\n    limit: int = 5\n",
    "    scopes: list[str] = Field(default_factory=lambda: [\"workspace_only\", \"company_global\", \"platform_global\"])\n    limit: int = 5\n    top_k: int | None = None\n",
)
replace(
    "app/schemas/api.py",
    "    limit: int = 5\n    top_k: int | None = None\n    top_k: int | None = None\n",
    "    limit: int = 5\n    top_k: int | None = None\n",
)

replace(
    "app/main.py",
    "        if payload.embed:\n            MemoryRouter().embed_item(db, item)\n    return items\n",
    "        if payload.embed:\n            try:\n                MemoryRouter().embed_item(db, item)\n            except ValueError as exc:\n                raise HTTPException(status_code=400, detail=str(exc)) from exc\n    return items\n",
)
replace(
    "app/main.py",
    "@app.post(\"/memory/items/{id}/embed\", response_model=MemoryItemOut)\ndef embed_memory_item(id: str, db: Session = Depends(get_db)):\n    item = get_or_404(db, MemoryItem, id)\n    return MemoryRouter().embed_item(db, item)\n",
    "@app.post(\"/memory/items/{id}/embed\", response_model=MemoryItemOut)\ndef embed_memory_item(id: str, db: Session = Depends(get_db)):\n    item = get_or_404(db, MemoryItem, id)\n    try:\n        return MemoryRouter().embed_item(db, item)\n    except ValueError as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n",
)
replace(
    "app/main.py",
    "            limit=payload.limit,\n",
    "            limit=payload.top_k or payload.limit,\n",
)

write(
    "sql/003_phase2_3_pgvector_memory.sql",
    '''-- Phase 2.3 pgvector memory retrieval readiness.
--
-- The application keeps SQLite/dev compatibility by mapping embeddings through
-- a portable SQLAlchemy type. In PostgreSQL, install pgvector and keep
-- memory_items.embedding as vector(16) unless EMBEDDING_DIMENSION is changed.
-- If EMBEDDING_DIMENSION is changed, edit vector(16) below to the deployed
-- dimension before applying this SQL.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE memory_items
  ADD COLUMN IF NOT EXISTS embedded_at timestamptz;

ALTER TABLE memory_items
  ADD COLUMN IF NOT EXISTS embedding vector(16);

CREATE INDEX IF NOT EXISTS ix_memory_items_scope_workspace_family
  ON memory_items(company_id, scope, workspace_id, family);

-- Preferred on pgvector versions that support HNSW.
CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_hnsw
  ON memory_items
  USING hnsw (embedding vector_l2_ops)
  WHERE embedding IS NOT NULL;

-- If HNSW is not available on the deployed pgvector version, use this instead:
-- CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_ivfflat
--   ON memory_items
--   USING ivfflat (embedding vector_l2_ops)
--   WITH (lists = 100)
--   WHERE embedding IS NOT NULL;
''',
)

write(
    "tests/test_phase2_3_pgvector_memory.py",
    '''import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import get_settings
from app.core.enums import MemoryScope
from app.db.base import Base
from app.main import app, get_db
from app.models.entities import ChannelWorkspace, Company, EditorialPlaybook, MemoryItem, WorkspaceBudgetPolicy, WorkspaceProfile
from app.providers.embedding import EmbeddingProvider, MockEmbeddingProvider
from app.services.maturity import WorkspaceMaturityService
from app.services.memory_retrieval import MemoryRetrievalService, validate_embedding_vector


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_company_workspace(db, *, company_name="Acme", workspace_name="AI Daily", platform="youtube"):
    company = Company(name=company_name)
    db.add(company)
    db.flush()
    workspace = ChannelWorkspace(
        company_id=company.id,
        workspace_name=workspace_name,
        platform=platform,
        channel_name=workspace_name,
        niche="AI creator tools",
        language="en",
        target_market=["US"],
        follower_count=100,
        published_video_count=3,
        baseline_confidence=0.05,
    )
    stage, _ = WorkspaceMaturityService().classify_workspace(workspace)
    workspace.maturity_stage = stage
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceProfile(
            workspace_id=workspace.id,
            company_id=company.id,
            brand_voice="clear and practical",
            target_audience="solo creators",
            monetization_thesis_json={"primary": "affiliate validation"},
            platform_rules_json={"copyright": "no unlicensed media"},
        )
    )
    db.add(WorkspaceBudgetPolicy(company_id=company.id, workspace_id=workspace.id))
    db.add(
        EditorialPlaybook(
            company_id=company.id,
            workspace_id=workspace.id,
            version="test_playbook_v1",
            content_json={"principles": ["reuse first"]},
            active=True,
        )
    )
    db.commit()
    return company, workspace


def add_memory(db, company, workspace, *, scope, family, title, content, platform=None):
    item = MemoryItem(
        company_id=company.id,
        workspace_id=workspace.id if scope == MemoryScope.WORKSPACE_ONLY.value else None,
        scope=scope,
        family=family,
        type="note",
        title=title,
        content=content,
        metadata_json={"platform": platform} if platform else {},
        confidence=0.8,
    )
    db.add(item)
    db.commit()
    return item


def test_sqlite_dev_mode_falls_back_without_pgvector(db):
    company, workspace = create_company_workspace(db)
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate validation",
        content="Affiliate clicks prove buyer intent.",
    )

    result = MemoryRetrievalService().retrieve(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
        agent_role="MonetizationStrategyAgent",
        query="affiliate buyer intent",
        families=["monetization_memory"],
        limit=5,
    )

    assert result.items
    assert result.backend_used in {"fallback_keyword", "fallback_embedding_scan"}
    assert result.retrieval_trace["pgvector_available"] is False
    assert "dialect=sqlite" in result.retrieval_trace["fallback_reason"]


def test_scope_isolation_is_applied_before_fallback_ranking(db):
    company_a, workspace_a = create_company_workspace(db, company_name="A", workspace_name="A YouTube", platform="youtube")
    _, workspace_b = create_company_workspace(db, company_name="A", workspace_name="A TikTok", platform="tiktok")
    company_c, workspace_c = create_company_workspace(db, company_name="C", workspace_name="C YouTube", platform="youtube")
    workspace_b.company_id = company_a.id
    db.commit()

    visible_workspace = add_memory(
        db,
        company_a,
        workspace_a,
        scope="workspace_only",
        family="monetization_memory",
        title="Workspace A buyer intent",
        content="Workspace A affiliate signal.",
    )
    add_memory(
        db,
        company_a,
        workspace_b,
        scope="workspace_only",
        family="monetization_memory",
        title="Workspace B buyer intent",
        content="Workspace B must not leak.",
    )
    visible_company = add_memory(
        db,
        company_a,
        workspace_a,
        scope="company_global",
        family="monetization_memory",
        title="Company A playbook",
        content="Company A revenue rule.",
    )
    add_memory(
        db,
        company_c,
        workspace_c,
        scope="company_global",
        family="monetization_memory",
        title="Company C playbook",
        content="Company C should not leak.",
    )
    visible_platform = add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="YouTube platform rule",
        content="YouTube monetization rule.",
        platform="youtube",
    )
    add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="TikTok platform rule",
        content="TikTok should not leak into YouTube.",
        platform="tiktok",
    )
    add_memory(
        db,
        company_c,
        workspace_c,
        scope="platform_global",
        family="monetization_memory",
        title="Other company YouTube",
        content="Cross-company platform memory should not leak.",
        platform="youtube",
    )

    result = MemoryRetrievalService().retrieve(
        db,
        company_id=company_a.id,
        workspace_id=workspace_a.id,
        workspace_context={"company_id": company_a.id, "workspace_id": workspace_a.id, "platform": "youtube"},
        agent_role="AuthorityAgent",
        query="buyer intent platform revenue",
        families=["monetization_memory"],
        limit=20,
    )
    ids = {item.id for item in result.items}

    assert {visible_workspace.id, visible_company.id, visible_platform.id}.issubset(ids)
    assert all(item.company_id == company_a.id for item in result.items)
    assert all(item.workspace_id == workspace_a.id for item in result.items if item.scope == "workspace_only")
    assert all((item.metadata_json or {}).get("platform") != "tiktok" for item in result.items)

    no_platform = MemoryRetrievalService().retrieve(
        db,
        company_id=company_a.id,
        workspace_id=workspace_a.id,
        workspace_context={"company_id": company_a.id, "workspace_id": workspace_a.id},
        agent_role="AuthorityAgent",
        query="youtube platform",
        families=["monetization_memory"],
        scopes=["platform_global"],
        limit=20,
    )
    assert no_platform.items == []


def test_embedding_dimension_policy_and_embed_endpoint_validation(client, monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSION", "8")
    get_settings.cache_clear()
    assert len(MockEmbeddingProvider().embed("creator workflow")) == 8
    with pytest.raises(ValueError, match="dimension mismatch"):
        validate_embedding_vector([0.1] * 7, expected_dimension=8, label="test embedding")

    class BadEmbeddingProvider(EmbeddingProvider):
        model = "bad"
        version = "v1"

        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    import app.services.memory_retrieval as memory_retrieval

    monkeypatch.setattr(memory_retrieval, "get_embedding_provider", lambda settings=None: BadEmbeddingProvider())

    company = client.post("/companies", json={"name": "Dim Co"}).json()
    workspace = client.post(
        "/workspaces",
        json={
            "company_id": company["id"],
            "workspace_name": "Dim Workspace",
            "platform": "youtube",
            "channel_name": "Dim Channel",
        },
    ).json()
    item = client.post(
        "/memory/items",
        json={
            "company_id": company["id"],
            "workspace_id": workspace["id"],
            "scope": "workspace_only",
            "family": "monetization_memory",
            "title": "Bad dimension",
            "content": "This embedding provider returns the wrong dimension.",
        },
    ).json()
    response = client.post(f"/memory/items/{item['id']}/embed")

    assert response.status_code == 400
    assert "dimension mismatch" in response.json()["detail"]
    get_settings.cache_clear()


def test_context_pack_uses_retrieval_service_and_preserves_family_policy(db):
    company, workspace = create_company_workspace(db)
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="brand_identity_memory",
        title="Brand voice",
        content="Clear practical brand voice.",
    )
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate revenue",
        content="Affiliate buyer intent.",
    )

    pack = MemoryRetrievalService().retrieve(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
        agent_role="ScriptAgent",
        query="brand affiliate",
        limit=10,
    )
    assert {item.family for item in pack.items} == {"brand_identity_memory"}

    from app.memory.router import MemoryRouter

    context_pack = MemoryRouter().build_context_pack(
        db,
        agent_role="ScriptAgent",
        workspace_id=workspace.id,
        query="brand affiliate",
        limit=10,
    )
    assert context_pack["retrieval_backend"] in {"fallback_keyword", "fallback_embedding_scan"}
    assert context_pack["retrieval_trace"]["scope_filter_applied"] is True
    assert context_pack["memory_count"] == 1


def test_pgvector_backend_is_selected_only_for_postgresql(db, monkeypatch):
    company, workspace = create_company_workspace(db)
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate validation",
        content="Affiliate clicks prove buyer intent.",
    )

    service = MemoryRetrievalService()
    available, reason = service.is_pgvector_available(db)
    assert available is False
    assert "dialect=sqlite" in reason

    monkeypatch.setenv("MEMORY_RETRIEVAL_BACKEND", "pgvector")
    get_settings.cache_clear()
    forced = MemoryRetrievalService()
    with pytest.raises(RuntimeError, match="pgvector memory retrieval requested but unavailable"):
        forced.retrieve(
            db,
            company_id=company.id,
            workspace_id=workspace.id,
            workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
            agent_role="MonetizationStrategyAgent",
            query="affiliate buyer intent",
            families=["monetization_memory"],
            limit=5,
        )
    get_settings.cache_clear()
''',
)

PY

python3 -m py_compile $(find app scripts tests -name "*.py" -print)
pytest -q
python3 scripts/generate_openapi.py

python3 - <<'PY'
from pathlib import Path
import zipfile

files = [
    "phase_2_3_pgvector_memory_patch.sh",
    "app/config/settings.py",
    "app/providers/embedding.py",
    "app/providers/factory.py",
    "app/services/memory_retrieval.py",
    "app/memory/router.py",
    "app/models/entities.py",
    "app/db/session.py",
    "app/schemas/api.py",
    "scripts/generate_openapi.py",
    "app/main.py",
    "sql/003_phase2_3_pgvector_memory.sql",
    "tests/test_phase2_3_pgvector_memory.py",
]
with zipfile.ZipFile("phase_2_3_pgvector_memory_patch.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for file_name in files:
        path = Path(file_name)
        if path.exists():
            archive.write(path, file_name)
PY
