from __future__ import annotations

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
