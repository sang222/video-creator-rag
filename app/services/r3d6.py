from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.r3d6 import RetrievalCandidate, RetrievalPolicy, RetrievalRequest, RetrievalResult
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ChannelMemoryItem,
    EffectiveChannelRuntimeContextSnapshot,
    EmbeddingFacet,
    EmbeddingJob,
    MemoryFacet,
    VectorRetrievalManifest,
)
from app.services.r3d5 import (
    MemoryApprovalGate,
    MemoryFreshnessGate,
    MemoryPromptSafetyGate,
    MemoryRightsGate,
    MemoryScopeGate,
)


R3D6_DIGEST_VERSION = "r3d6.memory_digest.v1"
DEFAULT_EMBEDDING_MODEL = "local-seeded-deterministic-vector"
CREATIVE_MEMORY_BLOCKED_AGENTS = {"ProviderReadinessSummaryAgent"}


@dataclass(frozen=True)
class _PolicyCandidate:
    item: ChannelMemoryItem
    facet: MemoryFacet
    deterministic_score: float
    vector_score: float | None
    final_score: float


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _text_hash(text: str) -> str:
    return stable_hash({"text": " ".join(text.split())})


class EmbeddingEligibilityGate:
    def check(
        self,
        *,
        item: ChannelMemoryItem,
        facet: MemoryFacet,
        effective_context: EffectiveChannelRuntimeContextSnapshot | None = None,
        allow_company_approved: bool = False,
    ) -> list[str]:
        reasons: list[str] = []
        for gate_result in [
            MemoryApprovalGate().check(item),
            MemoryRightsGate().check(item),
            MemoryPromptSafetyGate().check(item, facet),
            MemoryFreshnessGate().check(item),
        ]:
            reasons.extend(gate_result.reason_codes)
        if facet.embedding_eligible is not True:
            reasons.append("FACET_NOT_EMBEDDING_ELIGIBLE")
        if effective_context is not None:
            reasons.extend(
                MemoryScopeGate()
                .check(item=item, effective_context=effective_context, allow_company_approved=allow_company_approved)
                .reason_codes
            )
        return reasons


class EmbeddingJobService:
    def __init__(self, session: Session):
        self.session = session

    def prepare_job(
        self,
        *,
        memory_facet_id: uuid.UUID,
        effective_context_snapshot_id: uuid.UUID | None = None,
        allow_company_approved: bool = False,
    ) -> EmbeddingJob:
        facet = self._require_facet(memory_facet_id)
        item = self._require_item(facet.memory_item_id)
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, effective_context_snapshot_id) if effective_context_snapshot_id else None
        reasons = EmbeddingEligibilityGate().check(
            item=item,
            facet=facet,
            effective_context=effective,
            allow_company_approved=allow_company_approved,
        )
        job = EmbeddingJob(
            memory_facet_id=facet.id,
            job_status="BLOCKED" if reasons else "READY",
            blocker_reason_codes_json=reasons,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def store_embedding(
        self,
        *,
        memory_facet_id: uuid.UUID,
        embedding_vector: list[float],
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        effective_context_snapshot_id: uuid.UUID | None = None,
        allow_company_approved: bool = False,
    ) -> EmbeddingFacet:
        if not embedding_vector:
            raise ValidationFailureError("embedding_vector must be a non-empty deterministic test/local vector")
        job = self.prepare_job(
            memory_facet_id=memory_facet_id,
            effective_context_snapshot_id=effective_context_snapshot_id,
            allow_company_approved=allow_company_approved,
        )
        if job.job_status == "BLOCKED":
            return self._blocked_embedding_placeholder(job)
        facet = self._require_facet(memory_facet_id)
        item = self._require_item(facet.memory_item_id)
        embedding = EmbeddingFacet(
            memory_facet_id=facet.id,
            memory_item_id=item.id,
            company_id=item.company_id,
            channel_workspace_id=item.channel_workspace_id,
            content_category_id=item.content_category_id,
            series_id=item.series_id,
            character_profile_id=item.character_profile_id,
            character_version_id=item.character_version_id,
            facet_type=facet.facet_type,
            facet_text_hash=facet.facet_text_hash,
            embedding_model=embedding_model,
            embedding_dimension=len(embedding_vector),
            embedding_vector_json=[float(value) for value in embedding_vector],
            approval_status_at_embed=item.approval_status,
            rights_status_at_embed=item.rights_status,
            prompt_safety_state_at_embed=item.prompt_safety_state,
            embedding_eligible_at_embed=facet.embedding_eligible,
            stale_state="FRESH",
            stale_reason_codes_json=[],
        )
        self.session.add(embedding)
        job.job_status = "EMBEDDED"
        job.embedding_model = embedding_model
        job.embedding_dimension = len(embedding_vector)
        job.attempt_count += 1
        self.session.flush()
        return embedding

    def detect_stale_embeddings(self) -> list[EmbeddingFacet]:
        embeddings = list(self.session.scalars(select(EmbeddingFacet)).all())
        stale: list[EmbeddingFacet] = []
        for embedding in embeddings:
            facet = self.session.get(MemoryFacet, embedding.memory_facet_id)
            item = self.session.get(ChannelMemoryItem, embedding.memory_item_id)
            reasons: list[str] = []
            if facet is None or item is None:
                reasons.append("MEMORY_SOURCE_MISSING")
            else:
                if facet.facet_text_hash != embedding.facet_text_hash:
                    reasons.append("FACET_TEXT_HASH_CHANGED")
                if item.approval_status != embedding.approval_status_at_embed:
                    reasons.append("APPROVAL_STATUS_CHANGED")
                if item.rights_status != embedding.rights_status_at_embed:
                    reasons.append("RIGHTS_STATUS_CHANGED")
                if item.prompt_safety_state != embedding.prompt_safety_state_at_embed:
                    reasons.append("PROMPT_SAFETY_STATE_CHANGED")
                if facet.embedding_eligible != embedding.embedding_eligible_at_embed:
                    reasons.append("EMBEDDING_ELIGIBILITY_CHANGED")
                current_blockers = EmbeddingEligibilityGate().check(item=item, facet=facet)
                if current_blockers:
                    reasons.extend(current_blockers)
            if reasons:
                embedding.stale_state = "BLOCKED" if any("NOT_SAFE" in reason or "NOT_APPROVED" in reason for reason in reasons) else "REINDEX_REQUIRED"
                embedding.stale_reason_codes_json = sorted(set(reasons))
                stale.append(embedding)
        self.session.flush()
        return stale

    def _blocked_embedding_placeholder(self, job: EmbeddingJob) -> EmbeddingFacet:
        facet = self._require_facet(job.memory_facet_id)
        item = self._require_item(facet.memory_item_id)
        embedding = EmbeddingFacet(
            memory_facet_id=facet.id,
            memory_item_id=item.id,
            company_id=item.company_id,
            channel_workspace_id=item.channel_workspace_id,
            content_category_id=item.content_category_id,
            series_id=item.series_id,
            character_profile_id=item.character_profile_id,
            character_version_id=item.character_version_id,
            facet_type=facet.facet_type,
            facet_text_hash=facet.facet_text_hash,
            embedding_model=DEFAULT_EMBEDDING_MODEL,
            embedding_dimension=0,
            embedding_vector_json=[],
            approval_status_at_embed=item.approval_status,
            rights_status_at_embed=item.rights_status,
            prompt_safety_state_at_embed=item.prompt_safety_state,
            embedding_eligible_at_embed=facet.embedding_eligible,
            stale_state="BLOCKED",
            stale_reason_codes_json=job.blocker_reason_codes_json,
        )
        self.session.add(embedding)
        self.session.flush()
        return embedding

    def _require_facet(self, memory_facet_id: uuid.UUID) -> MemoryFacet:
        facet = self.session.get(MemoryFacet, memory_facet_id)
        if facet is None:
            raise NotFoundError(f"memory facet not found: {memory_facet_id}")
        return facet

    def _require_item(self, memory_item_id: uuid.UUID) -> ChannelMemoryItem:
        item = self.session.get(ChannelMemoryItem, memory_item_id)
        if item is None:
            raise NotFoundError(f"memory item not found: {memory_item_id}")
        return item


class VectorUnavailableSafeFallback:
    def build_result(self, *, request: RetrievalRequest, manifest: VectorRetrievalManifest, reason_code: str) -> RetrievalResult:
        digest = MemoryDigestBuilder().empty_digest(
            agent_key=request.agent_key,
            use_case=request.use_case,
            manifest_id=manifest.id,
            reason_code=reason_code,
        )
        return RetrievalResult(
            status="VECTOR_RUNTIME_EMPTY_SAFE",
            manifest_id=manifest.id,
            retrieval_hash=manifest.retrieval_hash,
            digest=digest,
            reason_codes=[reason_code],
        )


class VectorSafeRetrievalService:
    def __init__(
        self,
        session: Session,
        *,
        retrieval_enabled: bool | None = None,
        vector_enabled: bool | None = None,
    ):
        self.session = session
        settings = get_settings()
        self.retrieval_enabled = settings.controlled_memory_retrieval_enabled if retrieval_enabled is None else retrieval_enabled
        self.vector_enabled = settings.vector_retrieval_enabled if vector_enabled is None else vector_enabled

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, request.effective_context_snapshot_id)
        if effective is None:
            raise NotFoundError(f"effective context snapshot not found: {request.effective_context_snapshot_id}")
        normalized = self._normalize_request(request, effective)
        if not self.retrieval_enabled:
            manifest = self._persist_manifest(
                request=normalized,
                effective=effective,
                sql_filter_json={"retrieval_enabled": False, "sql_filter_first": True},
                candidates_before_vector=[],
                policy_candidates=[],
                selected=[],
                blocked_refs=[],
                rejected_refs=[],
                vector_model=None,
                digest=None,
                ranking_params={"status": "CONTROLLED_MEMORY_RETRIEVAL_DISABLED"},
            )
            return VectorUnavailableSafeFallback().build_result(
                request=normalized,
                manifest=manifest,
                reason_code="CONTROLLED_MEMORY_RETRIEVAL_DISABLED",
            )
        if normalized.policy.vector_enabled and not self.vector_enabled:
            manifest = self._persist_manifest(
                request=normalized,
                effective=effective,
                sql_filter_json={"vector_enabled": False, "sql_filter_first": True},
                candidates_before_vector=[],
                policy_candidates=[],
                selected=[],
                blocked_refs=[],
                rejected_refs=[],
                vector_model=None,
                digest=None,
                ranking_params={"status": "VECTOR_RETRIEVAL_DISABLED"},
            )
            return VectorUnavailableSafeFallback().build_result(
                request=normalized,
                manifest=manifest,
                reason_code="VECTOR_RETRIEVAL_DISABLED",
            )

        sql_candidates = self._sql_policy_candidates(request=normalized, effective=effective)
        policy_candidates, blocked_refs, rejected_refs = self._apply_runtime_policy(
            request=normalized,
            effective=effective,
            candidates=sql_candidates,
        )
        ranked = self._rank_candidates(request=normalized, candidates=policy_candidates)
        selected = ranked[: normalized.policy.max_selected_facets]
        digest = AgentMemoryDigestBuilder().build(
            agent_key=normalized.agent_key,
            use_case=normalized.use_case,
            selected=selected,
            blocked_refs=blocked_refs,
            rejected_refs=rejected_refs,
            max_digest_chars=normalized.policy.max_digest_chars,
        )
        manifest = self._persist_manifest(
            request=normalized,
            effective=effective,
            sql_filter_json=self._sql_filter_json(normalized, effective),
            candidates_before_vector=sql_candidates,
            policy_candidates=policy_candidates,
            selected=selected,
            blocked_refs=blocked_refs,
            rejected_refs=rejected_refs,
            vector_model=DEFAULT_EMBEDDING_MODEL if normalized.policy.vector_enabled else None,
            digest=digest,
            ranking_params={
                "sql_filter_first": True,
                "vector_rank_after_sql": True,
                "vector_enabled": normalized.policy.vector_enabled,
                **normalized.policy.ranking_params_json,
            },
        )
        status = "OK"
        reason_codes: list[str] = []
        if not selected:
            status = "EMPTY_SAFE_DIGEST"
            reason_codes.append("NO_APPROVED_MEMORY")
        elif normalized.policy.vector_enabled and not any(item.vector_score is not None for item in selected):
            status = "OK_DETERMINISTIC_NO_VECTOR"
            reason_codes.append("NO_EMBEDDINGS_AVAILABLE")
        return RetrievalResult(
            status=status,
            manifest_id=manifest.id,
            retrieval_hash=manifest.retrieval_hash,
            digest={**digest, "retrieval_manifest_id": str(manifest.id), "retrieval_hash": manifest.retrieval_hash},
            selected_candidates=[_candidate_read(candidate) for candidate in selected],
            blocked_refs=blocked_refs,
            rejected_refs=rejected_refs,
            sql_filter_applied_before_vector=True,
            reason_codes=reason_codes,
        )

    def _normalize_request(
        self,
        request: RetrievalRequest,
        effective: EffectiveChannelRuntimeContextSnapshot,
    ) -> RetrievalRequest:
        payload = request.model_dump()
        payload["company_id"] = payload["company_id"] or effective.company_id
        payload["channel_workspace_id"] = payload["channel_workspace_id"] or effective.channel_workspace_id
        payload["content_category_id"] = payload["content_category_id"] or effective.content_category_id
        payload["character_profile_id"] = payload["character_profile_id"] or effective.character_profile_id
        payload["character_version_id"] = payload["character_version_id"] or effective.character_version_id
        payload["video_project_id"] = payload["video_project_id"] or effective.video_project_id
        return RetrievalRequest.model_validate(payload)

    def _sql_policy_candidates(
        self,
        *,
        request: RetrievalRequest,
        effective: EffectiveChannelRuntimeContextSnapshot,
    ) -> list[tuple[ChannelMemoryItem, MemoryFacet]]:
        requested_types = set(request.policy.requested_facet_types)
        if request.query_facet_type:
            requested_types.add(request.query_facet_type)
        statement = (
            select(ChannelMemoryItem, MemoryFacet)
            .join(MemoryFacet, MemoryFacet.memory_item_id == ChannelMemoryItem.id)
            .where(
                ChannelMemoryItem.company_id == request.company_id,
                ChannelMemoryItem.approval_status == "APPROVED",
                ChannelMemoryItem.rights_status == "SAFE",
                ChannelMemoryItem.prompt_safety_state == "PROMPT_SAFE",
                ChannelMemoryItem.freshness_state == "FRESH",
                MemoryFacet.prompt_safety_state == "PROMPT_SAFE",
                MemoryFacet.embedding_eligible.is_(True),
            )
        )
        if not request.policy.allow_company_approved:
            statement = statement.where(ChannelMemoryItem.channel_workspace_id == request.channel_workspace_id)
        if request.content_category_id is not None:
            statement = statement.where(
                (ChannelMemoryItem.content_category_id.is_(None))
                | (ChannelMemoryItem.content_category_id == request.content_category_id)
            )
        if requested_types:
            statement = statement.where(MemoryFacet.facet_type.in_(sorted(requested_types)))
        return list(self.session.execute(statement).all())

    def _apply_runtime_policy(
        self,
        *,
        request: RetrievalRequest,
        effective: EffectiveChannelRuntimeContextSnapshot,
        candidates: list[tuple[ChannelMemoryItem, MemoryFacet]],
    ) -> tuple[list[tuple[ChannelMemoryItem, MemoryFacet]], list[dict[str, Any]], list[dict[str, Any]]]:
        policy_candidates: list[tuple[ChannelMemoryItem, MemoryFacet]] = []
        blocked_refs: list[dict[str, Any]] = []
        rejected_refs: list[dict[str, Any]] = []
        for item, facet in candidates:
            scope = MemoryScopeGate().check(
                item=item,
                effective_context=effective,
                allow_company_approved=request.policy.allow_company_approved,
            )
            if not scope.passed:
                blocked_refs.append(_ref(item, facet, scope.reason_codes))
                continue
            if not _use_case_allowed(facet, request):
                rejected_refs.append(_ref(item, facet, ["MEMORY_USE_CASE_NOT_ALLOWED"]))
                continue
            policy_candidates.append((item, facet))
        return policy_candidates, blocked_refs, rejected_refs

    def _rank_candidates(
        self,
        *,
        request: RetrievalRequest,
        candidates: list[tuple[ChannelMemoryItem, MemoryFacet]],
    ) -> list[_PolicyCandidate]:
        embeddings = self._embedding_map([facet.id for _, facet in candidates])
        ranked: list[_PolicyCandidate] = []
        for item, facet in candidates:
            deterministic = _deterministic_score(item=item, facet=facet, request=request)
            vector_score = None
            embedding = embeddings.get(facet.id)
            if request.policy.vector_enabled and request.query_vector is not None and embedding is not None:
                vector_score = _cosine(request.query_vector, embedding.embedding_vector_json)
            final = deterministic + (vector_score or 0.0)
            ranked.append(_PolicyCandidate(item=item, facet=facet, deterministic_score=deterministic, vector_score=vector_score, final_score=final))
        return sorted(ranked, key=lambda item: (item.final_score, str(item.facet.id)), reverse=True)

    def _embedding_map(self, facet_ids: list[uuid.UUID]) -> dict[uuid.UUID, EmbeddingFacet]:
        if not facet_ids:
            return {}
        rows = self.session.scalars(
            select(EmbeddingFacet)
            .where(
                EmbeddingFacet.memory_facet_id.in_(facet_ids),
                EmbeddingFacet.stale_state == "FRESH",
            )
            .order_by(EmbeddingFacet.created_at.desc(), EmbeddingFacet.id.desc())
        ).all()
        result: dict[uuid.UUID, EmbeddingFacet] = {}
        for row in rows:
            result.setdefault(row.memory_facet_id, row)
        return result

    def _persist_manifest(
        self,
        *,
        request: RetrievalRequest,
        effective: EffectiveChannelRuntimeContextSnapshot,
        sql_filter_json: dict[str, Any],
        candidates_before_vector: list[tuple[ChannelMemoryItem, MemoryFacet]],
        policy_candidates: list[tuple[ChannelMemoryItem, MemoryFacet]],
        selected: list[_PolicyCandidate],
        blocked_refs: list[dict[str, Any]],
        rejected_refs: list[dict[str, Any]],
        vector_model: str | None,
        digest: dict[str, Any] | None,
        ranking_params: dict[str, Any],
    ) -> VectorRetrievalManifest:
        selected_refs = [_selected_ref(candidate) for candidate in selected]
        retrieval_hash = stable_hash(
            {
                "request": request.model_dump(),
                "selected_refs": selected_refs,
                "blocked_refs": blocked_refs,
                "rejected_refs": rejected_refs,
                "candidate_count_before_vector": len(candidates_before_vector),
                "candidate_count_after_policy": len(policy_candidates),
                "ranking_params": ranking_params,
            }
        )
        manifest = VectorRetrievalManifest(
            video_project_id=request.video_project_id,
            package_id=request.package_id,
            effective_context_snapshot_id=effective.id,
            company_id=effective.company_id,
            channel_workspace_id=effective.channel_workspace_id,
            content_category_id=effective.content_category_id,
            series_id=request.series_id,
            character_profile_id=effective.character_profile_id,
            character_version_id=effective.character_version_id,
            agent_key=request.agent_key,
            use_case=request.use_case,
            query_facet_type=request.query_facet_type,
            query_text_hash=_text_hash(request.query_text),
            sql_filter_json=sql_filter_json,
            candidate_count_before_vector=len(candidates_before_vector),
            candidate_count_after_policy=len(policy_candidates),
            selected_memory_facet_refs_json=selected_refs,
            blocked_refs_json=blocked_refs,
            rejected_refs_json=rejected_refs,
            vector_model=vector_model,
            ranking_params_json=ranking_params,
            retrieval_hash=retrieval_hash,
            digest_hash=digest.get("digest_hash") if digest else None,
        )
        self.session.add(manifest)
        self.session.flush()
        return manifest

    def _sql_filter_json(self, request: RetrievalRequest, effective: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
        return {
            "sql_filter_first": True,
            "company_id": str(request.company_id),
            "channel_workspace_id": str(request.channel_workspace_id),
            "content_category_id": str(request.content_category_id) if request.content_category_id else None,
            "character_profile_id": str(effective.character_profile_id) if effective.character_profile_id else None,
            "approval_status": "APPROVED",
            "rights_status": "SAFE",
            "prompt_safety_state": "PROMPT_SAFE",
            "freshness_state": "FRESH",
            "facet_prompt_safety_state": "PROMPT_SAFE",
            "facet_embedding_eligible": True,
            "allow_company_approved": request.policy.allow_company_approved,
            "requested_facet_types": request.policy.requested_facet_types,
        }


class MemoryDigestBuilder:
    def empty_digest(
        self,
        *,
        agent_key: str,
        use_case: str,
        manifest_id: uuid.UUID | None,
        reason_code: str = "NO_APPROVED_MEMORY",
    ) -> dict[str, Any]:
        digest = {
            "digest_type": "EMPTY_SAFE_DIGEST",
            "digest_version": R3D6_DIGEST_VERSION,
            "agent_key": agent_key,
            "use_case": use_case,
            "status": "EMPTY_SAFE_DIGEST",
            "reason_codes": [reason_code],
            "selected_memory_facet_refs": [],
            "retrieval_manifest_id": str(manifest_id) if manifest_id else None,
            "lessons": [],
            "no_raw_memory": True,
            "no_full_artifact_history": True,
        }
        digest["digest_hash"] = stable_hash(digest)
        return digest

    def build_digest(
        self,
        *,
        agent_key: str,
        use_case: str,
        selected: list[_PolicyCandidate],
        blocked_refs: list[dict[str, Any]],
        rejected_refs: list[dict[str, Any]],
        max_digest_chars: int,
    ) -> dict[str, Any]:
        if not selected:
            return self.empty_digest(agent_key=agent_key, use_case=use_case, manifest_id=None)
        lessons = _resolve_conflicts(selected)
        digest = {
            "digest_type": _digest_type_for_agent(agent_key),
            "digest_version": R3D6_DIGEST_VERSION,
            "agent_key": agent_key,
            "use_case": use_case,
            "status": "OK",
            "selected_memory_facet_refs": [_selected_ref(candidate) for candidate in selected],
            "blocked_count": len(blocked_refs),
            "rejected_count": len(rejected_refs),
            "lessons": lessons,
            "no_raw_memory": True,
            "no_embedding_rows": True,
            "no_full_artifact_history": True,
        }
        digest = _trim_digest(digest, max_digest_chars=max_digest_chars)
        digest["digest_hash"] = stable_hash(digest)
        return digest


class AgentMemoryDigestBuilder:
    def build(
        self,
        *,
        agent_key: str,
        use_case: str,
        selected: list[_PolicyCandidate],
        blocked_refs: list[dict[str, Any]],
        rejected_refs: list[dict[str, Any]],
        max_digest_chars: int,
    ) -> dict[str, Any]:
        if agent_key in CREATIVE_MEMORY_BLOCKED_AGENTS:
            return MemoryDigestBuilder().empty_digest(
                agent_key=agent_key,
                use_case=use_case,
                manifest_id=None,
                reason_code="AGENT_DOES_NOT_ACCEPT_CREATIVE_MEMORY",
            )
        if agent_key == "GatekeeperSoftReviewAgent":
            digest = {
                "digest_type": "GatekeeperMemoryManifestSummary",
                "digest_version": R3D6_DIGEST_VERSION,
                "agent_key": agent_key,
                "use_case": use_case,
                "status": "OK" if selected else "EMPTY_SAFE_DIGEST",
                "selected_count": len(selected),
                "blocked_count": len(blocked_refs),
                "rejected_count": len(rejected_refs),
                "applied_lesson_refs": [_selected_ref(candidate) for candidate in selected],
                "lessons": [],
                "no_raw_memory": True,
                "no_embedding_rows": True,
            }
            digest["digest_hash"] = stable_hash(digest)
            return digest
        return MemoryDigestBuilder().build_digest(
            agent_key=agent_key,
            use_case=use_case,
            selected=selected,
            blocked_refs=blocked_refs,
            rejected_refs=rejected_refs,
            max_digest_chars=max_digest_chars,
        )


def _deterministic_score(*, item: ChannelMemoryItem, facet: MemoryFacet, request: RetrievalRequest) -> float:
    score = 1.0
    if request.query_facet_type and request.query_facet_type == facet.facet_type:
        score += 0.4
    if request.content_category_id is not None and item.content_category_id == request.content_category_id:
        score += 0.5
    if request.character_profile_id is not None and item.character_profile_id == request.character_profile_id:
        score += 0.4
    if item.reuse_scope == "CATEGORY":
        score += 0.2
    if item.reuse_scope == "CHARACTER":
        score += 0.25
    score += {"HIGH": 0.3, "MEDIUM": 0.2, "LOW": 0.1}.get(facet.confidence_label, 0.0)
    if facet.polarity == "NEGATIVE":
        score += 0.05
    return score


def _use_case_allowed(facet: MemoryFacet, request: RetrievalRequest) -> bool:
    allowed = set(facet.allowed_use_cases_json or [])
    forbidden = set(facet.forbidden_use_cases_json or [])
    keys = {request.use_case, request.agent_key}
    if forbidden & keys:
        return False
    if allowed and not (allowed & keys):
        return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _candidate_read(candidate: _PolicyCandidate) -> RetrievalCandidate:
    return RetrievalCandidate(
        memory_item_id=candidate.item.id,
        memory_facet_id=candidate.facet.id,
        facet_type=candidate.facet.facet_type,
        facet_text_hash=candidate.facet.facet_text_hash,
        deterministic_score=round(candidate.deterministic_score, 6),
        vector_score=round(candidate.vector_score, 6) if candidate.vector_score is not None else None,
        final_score=round(candidate.final_score, 6),
        polarity=candidate.facet.polarity,
        confidence_label=candidate.facet.confidence_label,
        scope=candidate.facet.scope_json,
    )


def _ref(item: ChannelMemoryItem, facet: MemoryFacet, reason_codes: list[str]) -> dict[str, Any]:
    return {
        "memory_item_id": str(item.id),
        "memory_facet_id": str(facet.id),
        "facet_type": facet.facet_type,
        "reason_codes": reason_codes,
    }


def _selected_ref(candidate: _PolicyCandidate) -> dict[str, Any]:
    return {
        "memory_item_id": str(candidate.item.id),
        "memory_facet_id": str(candidate.facet.id),
        "facet_type": candidate.facet.facet_type,
        "facet_text_hash": candidate.facet.facet_text_hash,
        "final_score": round(candidate.final_score, 6),
    }


def _digest_type_for_agent(agent_key: str) -> str:
    mapping = {
        "ScriptWriterAgent": "ScriptMemoryDigest",
        "ScriptPlanningAgent": "ScriptMemoryDigest",
        "VisualPlanningAgent": "VisualMemoryDigest",
        "ThumbnailBriefAgent": "ThumbnailMemoryDigest",
        "PublishingMetadataAgent": "MetadataMemoryDigest",
    }
    return mapping.get(agent_key, "AvoidRepeatDigest")


def _resolve_conflicts(selected: list[_PolicyCandidate]) -> list[dict[str, Any]]:
    grouped: dict[str, list[_PolicyCandidate]] = {}
    for candidate in selected:
        grouped.setdefault(_topic_key(candidate.facet.facet_text), []).append(candidate)
    lessons: list[dict[str, Any]] = []
    for topic, candidates in grouped.items():
        polarities = {candidate.facet.polarity for candidate in candidates}
        refs = [str(candidate.facet.id) for candidate in candidates]
        if "POSITIVE" in polarities and "NEGATIVE" in polarities:
            lessons.append(
                {
                    "lesson": f"Approved memory has mixed signals about {topic}; prefer the avoid-rule until reviewed.",
                    "polarity": "NEUTRAL",
                    "refs": refs,
                }
            )
            continue
        best = candidates[0]
        lessons.append(
            {
                "lesson": _safe_lesson(best.facet.facet_text, best.facet.id),
                "polarity": best.facet.polarity,
                "refs": refs,
            }
        )
    return lessons


def _topic_key(text: str) -> str:
    lower = text.lower()
    if "abstract" in lower and "title" in lower:
        return "abstract titles"
    if "hook" in lower:
        return "hooks"
    if "thumbnail" in lower:
        return "thumbnails"
    words = [word.strip(".,:;!?") for word in lower.split()[:4]]
    return " ".join(words) or "memory lesson"


def _safe_lesson(text: str, facet_id: uuid.UUID) -> str:
    compact = " ".join(text.split())
    if len(compact) > 260:
        compact = f"{compact[:257]}..."
    return f"{compact} Ref: memory_facet_{facet_id}."


def _trim_digest(digest: dict[str, Any], *, max_digest_chars: int) -> dict[str, Any]:
    while len(canonical_json(digest)) > max_digest_chars and len(digest.get("lessons", [])) > 1:
        digest["lessons"] = digest["lessons"][:-1]
    return digest
