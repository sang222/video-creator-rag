from __future__ import annotations

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
        embedding = self.embedding_provider.embed(f"{item.title}\n{item.content}")
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
