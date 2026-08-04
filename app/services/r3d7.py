from __future__ import annotations

# Compatibility note: semantic facade `learning_loop` re-exports this implementation; phase-coded import kept for reports/tests/backward compatibility.
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.r3d5 import ChannelMemoryDraftCreate, MemoryFacetInput, MemoryFromApprovedPlaybookCreate
from app.contracts.r3d6 import RetrievalPolicy, RetrievalRequest
from app.contracts.r3d7 import LearningToMemoryPromotionRequest, QualityDeltaAttributionRunRequest
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    AgentMemoryApplicationRecord,
    ApprovedPlaybookEntry,
    ChannelMemoryItem,
    EffectiveChannelRuntimeContextSnapshot,
    EmbeddingFacet,
    EmbeddingJob,
    FailureTraceReport,
    LearningCandidate,
    LearningCandidateGenerationRun,
    LearningEvidenceBundle,
    LearningReviewDecision,
    LearningToMemoryPromotionRun,
    MemoryConfidenceUpdateLedger,
    MemoryFacet,
    MemoryInfluenceManifest,
    OpsIncident,
    QualityDeltaAttribution,
    RecoveryProposal,
    UploadedVideo,
    UploadedVideoMetricsSummary,
    VectorRetrievalManifest,
)
from app.services.r3d5 import ControlledMemoryService, MemoryScopeGate
from app.services.r3d6 import VectorSafeRetrievalService


CONFIDENCE_ORDER = ["UNPROVEN", "LOW", "MEDIUM", "HIGH"]
QUALITY_RESULTS = {"IMPROVED", "DEGRADED", "INCONCLUSIVE", "TOO_EARLY", "BLOCKED_BY_DATA_QUALITY"}
PROMOTION_STATUSES = {"CREATED", "REVIEW_REQUIRED", "BLOCKED", "COMPLETED"}
APPLICATION_MODES = {"GUIDANCE", "AVOID_PATTERN", "STYLE_ANCHOR", "PACKAGING_HINT", "VISUAL_HINT", "METADATA_HINT"}


@dataclass(frozen=True)
class GateEvaluation:
    passed: bool
    reason_codes: list[str]
    status: str
    details: dict[str, Any]


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


class LearningToMemoryPromotionService:
    def __init__(self, session: Session):
        self.session = session

    def promote_approved_playbook(self, data: LearningToMemoryPromotionRequest) -> LearningToMemoryPromotionRun:
        if data.approved_playbook_entry_id is None:
            return self._blocked_run(data=data, reason_codes=["APPROVED_PLAYBOOK_ENTRY_REQUIRED"])
        entry = self.session.get(ApprovedPlaybookEntry, data.approved_playbook_entry_id)
        if entry is None:
            raise NotFoundError(f"approved playbook entry not found: {data.approved_playbook_entry_id}")
        evidence_bundle_id = data.evidence_bundle_id or entry.evidence_bundle_id
        source_uploaded_video_id = data.source_uploaded_video_id or self._source_uploaded_video_from_candidate(entry.learning_candidate_id)
        run = LearningToMemoryPromotionRun(
            learning_candidate_id=entry.learning_candidate_id,
            approved_playbook_entry_id=entry.id,
            evidence_bundle_id=evidence_bundle_id,
            source_uploaded_video_id=source_uploaded_video_id,
            created_memory_item_ids_json=[],
            created_memory_facet_ids_json=[],
            run_status="CREATED",
            reason_codes_json=[],
            human_approval_ref=data.human_approval_ref or f"approved_playbook_entry:{entry.id}",
        )
        self.session.add(run)
        self.session.flush()
        blockers = self._validate_approved_playbook_source(entry=entry, evidence_bundle_id=evidence_bundle_id, data=data)
        if blockers:
            run.run_status = "BLOCKED"
            run.reason_codes_json = blockers
            self.session.flush()
            return run
        item = ControlledMemoryService(self.session).create_from_approved_playbook_entry(
            playbook_entry_id=entry.id,
            data=MemoryFromApprovedPlaybookCreate(
                content_category_id=data.content_category_id,
                reuse_scope=data.reuse_scope,
                memory_type=data.memory_type,
                prompt_safe=data.prompt_safe,
                rights_safe=data.rights_safe,
                facet_type=data.facet_type,
                allowed_use_cases_json=data.allowed_use_cases_json,
                embedding_eligible=data.embedding_eligible,
            ),
        )
        if data.failure_trace_report_id is not None:
            item.created_from_failure_trace_report_id = data.failure_trace_report_id
        if data.recovery_proposal_id is not None:
            item.created_from_recovery_proposal_id = data.recovery_proposal_id
        facets = ControlledMemoryService(self.session).list_facets(memory_item_id=item.id)
        run.created_memory_item_ids_json = [str(item.id)]
        run.created_memory_facet_ids_json = [str(facet.id) for facet in facets]
        run.run_status = "COMPLETED"
        run.reason_codes_json = ["MEMORY_DRAFT_CREATED", "MEMORY_REVIEW_REQUIRED", "NO_AUTO_MEMORY_APPROVAL"]
        self.session.flush()
        return run

    def promote_learning_candidate(self, *, learning_candidate_id: uuid.UUID) -> LearningToMemoryPromotionRun:
        candidate = self.session.get(LearningCandidate, learning_candidate_id)
        if candidate is None:
            raise NotFoundError(f"learning candidate not found: {learning_candidate_id}")
        run = LearningToMemoryPromotionRun(
            learning_candidate_id=candidate.id,
            evidence_bundle_id=candidate.evidence_bundle_id,
            source_uploaded_video_id=candidate.uploaded_video_id,
            created_memory_item_ids_json=[],
            created_memory_facet_ids_json=[],
            run_status="CREATED",
            reason_codes_json=[],
        )
        self.session.add(run)
        self.session.flush()
        approved = self.session.scalars(
            select(ApprovedPlaybookEntry).where(
                ApprovedPlaybookEntry.learning_candidate_id == candidate.id,
                ApprovedPlaybookEntry.state == "APPROVED",
            )
        ).first()
        decision = self.session.scalars(
            select(LearningReviewDecision).where(
                LearningReviewDecision.learning_candidate_id == candidate.id,
                LearningReviewDecision.action == "APPROVE",
            )
        ).first()
        if approved is None and decision is None:
            run.run_status = "BLOCKED"
            run.reason_codes_json = ["LEARNING_CANDIDATE_NOT_HUMAN_APPROVED", "NO_AUTO_PROMOTION_FROM_RAW_LEARNING"]
            self.session.flush()
            return run
        try:
            item = ControlledMemoryService(self.session).create_draft_from_learning_candidate(candidate_id=candidate.id)
        except ValidationFailureError as exc:
            run.run_status = "BLOCKED"
            run.reason_codes_json = [str(exc)]
            self.session.flush()
            return run
        facets = ControlledMemoryService(self.session).list_facets(memory_item_id=item.id)
        run.created_memory_item_ids_json = [str(item.id)]
        run.created_memory_facet_ids_json = [str(facet.id) for facet in facets]
        run.run_status = "COMPLETED"
        run.reason_codes_json = ["HUMAN_APPROVED_LEARNING_MEMORY_DRAFT_CREATED", "MEMORY_REVIEW_REQUIRED"]
        self.session.flush()
        return run

    def promote_system_governed_candidate(
        self,
        *,
        learning_candidate_id: uuid.UUID,
        policy_version: str,
        policy_hash: str,
    ) -> LearningToMemoryPromotionRun:
        """Apply only mature, recurrent low-risk guidance without M11 gating."""

        candidate = self.session.get(LearningCandidate, learning_candidate_id)
        if candidate is None:
            raise NotFoundError(f"learning candidate not found: {learning_candidate_id}")
        run = LearningToMemoryPromotionRun(
            learning_candidate_id=candidate.id,
            evidence_bundle_id=candidate.evidence_bundle_id,
            source_uploaded_video_id=candidate.uploaded_video_id,
            created_memory_item_ids_json=[],
            created_memory_facet_ids_json=[],
            run_status="CREATED",
            reason_codes_json=[],
            human_approval_ref=None,
        )
        self.session.add(run)
        self.session.flush()
        allowed_types = {
            "PACKAGING_PATTERN", "HOOK_PATTERN", "RETENTION_PATTERN",
            "VISUAL_SOURCE_PATTERN", "COST_EFFICIENCY_PATTERN",
        }
        blockers: list[str] = []
        if candidate.candidate_type not in allowed_types:
            blockers.append("HIGH_RISK_OR_UNSUPPORTED_LEARNING_SCOPE")
        if candidate.risk_level != "LOW" or candidate.policy_flags or candidate.rights_flags:
            blockers.append("HIGH_RISK_OR_POLICY_RIGHTS_CHANGE_NON_APPLIED")
        if not policy_version or len(policy_hash) != 64:
            blockers.append("SYSTEM_POLICY_PROVENANCE_INVALID")
        comparable = self._mature_recurrent_candidates(candidate)
        source_video_ids = sorted({str(item.uploaded_video_id) for item in comparable if item.uploaded_video_id})
        if len(source_video_ids) < 2:
            blockers.append("LEARNING_RECURRENCE_INSUFFICIENT")
        if blockers:
            run.run_status = "REVIEW_REQUIRED" if any("HIGH_RISK" in item for item in blockers) else "BLOCKED"
            run.reason_codes_json = list(dict.fromkeys(blockers + ["PROPOSAL_ONLY_NON_APPLIED"]))
            self.session.flush()
            return run
        existing = self.session.scalars(
            select(ChannelMemoryItem).where(
                ChannelMemoryItem.created_from_learning_candidate_id == candidate.id,
                ChannelMemoryItem.approval_authority_type == "SYSTEM_POLICY",
            )
        ).first()
        if existing is not None:
            run.created_memory_item_ids_json = [str(existing.id)]
            run.run_status = "COMPLETED"
            run.reason_codes_json = ["SYSTEM_POLICY_MEMORY_ALREADY_PROMOTED"]
            self.session.flush()
            return run
        text = candidate.suggested_playbook_text or candidate.suggested_learning
        item = ControlledMemoryService(self.session).create_draft(
            data=ChannelMemoryDraftCreate(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                memory_type=_system_memory_type(candidate.candidate_type),
                source_type="SYSTEM_POLICY_LEARNING",
                source_ref=f"learning-candidate:{candidate.id}",
                source_content={"candidate_id": str(candidate.id), "learning": text},
                summary=candidate.candidate_summary,
                rights_status="SAFE",
                prompt_safety_state="PROMPT_SAFE",
                reuse_scope="CHANNEL",
                created_from_learning_candidate_id=candidate.id,
                facets=[
                    MemoryFacetInput(
                        facet_type=_system_facet_type(candidate.candidate_type),
                        facet_text=text[:420],
                        allowed_use_cases_json=["script", "visual", "packaging", "metadata"],
                        polarity="NEGATIVE" if "FAILED" in candidate.candidate_type else "POSITIVE",
                        confidence_label="MEDIUM",
                        prompt_safety_state="PROMPT_SAFE",
                    )
                ],
            )
        )
        ControlledMemoryService(self.session).approve_system_policy(
            memory_item_id=item.id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            evidence={
                "eligibility_run_id": str(candidate.eligibility_run_id),
                "evidence_bundle_id": str(candidate.evidence_bundle_id),
                "source_uploaded_video_ids": source_video_ids,
                "mature_sample_count": len(source_video_ids),
                "reason_codes": ["LOW_RISK_MATURE_RECURRENT_SYSTEM_POLICY_PROMOTION"],
            },
        )
        run.created_memory_item_ids_json = [str(item.id)]
        run.created_memory_facet_ids_json = [str(facet.id) for facet in ControlledMemoryService(self.session).list_facets(memory_item_id=item.id)]
        run.run_status = "COMPLETED"
        run.reason_codes_json = ["SYSTEM_POLICY_MEMORY_PROMOTED", "NO_HUMAN_APPROVAL_FIELDS"]
        self.session.flush()
        return run

    def _mature_recurrent_candidates(self, candidate: LearningCandidate) -> list[LearningCandidate]:
        rows = self.session.execute(
            select(LearningCandidate, LearningCandidateGenerationRun)
            .join(LearningCandidateGenerationRun, LearningCandidate.generation_run_id == LearningCandidateGenerationRun.id)
            .where(
                LearningCandidate.company_id == candidate.company_id,
                LearningCandidate.channel_workspace_id == candidate.channel_workspace_id,
                LearningCandidate.candidate_type == candidate.candidate_type,
                LearningCandidate.risk_level == "LOW",
            )
        ).all()
        return [
            item
            for item, generation in rows
            if (generation.metadata_ or {}).get("maturity") == "MATURE"
            and not item.policy_flags
            and not item.rights_flags
        ]

    def _blocked_run(self, *, data: LearningToMemoryPromotionRequest, reason_codes: list[str]) -> LearningToMemoryPromotionRun:
        run = LearningToMemoryPromotionRun(
            learning_candidate_id=data.learning_candidate_id,
            approved_playbook_entry_id=data.approved_playbook_entry_id,
            evidence_bundle_id=data.evidence_bundle_id,
            source_uploaded_video_id=data.source_uploaded_video_id,
            created_memory_item_ids_json=[],
            created_memory_facet_ids_json=[],
            run_status="BLOCKED",
            reason_codes_json=reason_codes,
            human_approval_ref=data.human_approval_ref,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def _validate_approved_playbook_source(
        self,
        *,
        entry: ApprovedPlaybookEntry,
        evidence_bundle_id: uuid.UUID | None,
        data: LearningToMemoryPromotionRequest,
    ) -> list[str]:
        reasons: list[str] = []
        if entry.state != "APPROVED":
            reasons.append("APPROVED_PLAYBOOK_ENTRY_NOT_APPROVED")
        if entry.channel_workspace_id is None:
            reasons.append("APPROVED_PLAYBOOK_MISSING_CHANNEL_SCOPE")
        if evidence_bundle_id is not None and self.session.get(LearningEvidenceBundle, evidence_bundle_id) is None:
            reasons.append("EVIDENCE_BUNDLE_MISSING")
        if data.failure_trace_report_id is not None and self.session.get(FailureTraceReport, data.failure_trace_report_id) is None:
            reasons.append("FAILURE_TRACE_REPORT_MISSING")
        if data.recovery_proposal_id is not None and self.session.get(RecoveryProposal, data.recovery_proposal_id) is None:
            reasons.append("RECOVERY_PROPOSAL_MISSING")
        if data.uploaded_video_metrics_summary_id is not None and self.session.get(UploadedVideoMetricsSummary, data.uploaded_video_metrics_summary_id) is None:
            reasons.append("UPLOADED_VIDEO_METRICS_SUMMARY_MISSING")
        return reasons

    def _source_uploaded_video_from_candidate(self, candidate_id: uuid.UUID | None) -> uuid.UUID | None:
        if candidate_id is None:
            return None
        candidate = self.session.get(LearningCandidate, candidate_id)
        return candidate.uploaded_video_id if candidate is not None else None


class MemoryInfluenceManifestGate:
    def __init__(self, session: Session):
        self.session = session

    def check_digest_manifest_presence(self, *, digest: dict[str, Any]) -> GateEvaluation:
        manifest_id = digest.get("memory_influence_manifest_id")
        if not manifest_id:
            return GateEvaluation(False, ["MEMORY_DIGEST_WITHOUT_INFLUENCE_MANIFEST"], "BLOCK", {})
        return GateEvaluation(True, [], "PASS", {"memory_influence_manifest_id": manifest_id})

    def check_manifest_scope(
        self,
        *,
        manifest: MemoryInfluenceManifest,
        retrieval: VectorRetrievalManifest,
        effective: EffectiveChannelRuntimeContextSnapshot,
    ) -> GateEvaluation:
        reasons: list[str] = []
        if manifest.effective_context_snapshot_id != effective.id or retrieval.effective_context_snapshot_id != effective.id:
            reasons.append("MEMORY_INFLUENCE_EFFECTIVE_CONTEXT_MISMATCH")
        if manifest.video_project_id != effective.video_project_id or retrieval.video_project_id != effective.video_project_id:
            reasons.append("MEMORY_INFLUENCE_VIDEO_PROJECT_MISMATCH")
        if retrieval.company_id != effective.company_id:
            reasons.append("MEMORY_INFLUENCE_COMPANY_MISMATCH")
        if retrieval.channel_workspace_id != effective.channel_workspace_id:
            reasons.append("MEMORY_INFLUENCE_CHANNEL_MISMATCH")
        if retrieval.content_category_id is not None and retrieval.content_category_id != effective.content_category_id:
            reasons.append("MEMORY_INFLUENCE_CATEGORY_MISMATCH")
        return GateEvaluation(not reasons, reasons, "PASS" if not reasons else "BLOCK", {})

    def check_references_scope(
        self,
        *,
        memory_item_ids: list[uuid.UUID],
        effective: EffectiveChannelRuntimeContextSnapshot,
    ) -> GateEvaluation:
        reasons: list[str] = []
        for memory_item_id in memory_item_ids:
            item = self.session.get(ChannelMemoryItem, memory_item_id)
            if item is None:
                reasons.append("MEMORY_INFLUENCE_ITEM_MISSING")
                continue
            scope = MemoryScopeGate().check(item=item, effective_context=effective)
            reasons.extend(scope.reason_codes)
        return GateEvaluation(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCK", {})


class MemoryInfluenceManifestService:
    def __init__(self, session: Session):
        self.session = session

    def record_from_digest(
        self,
        *,
        video_project_id: uuid.UUID,
        package_id: uuid.UUID | None,
        effective_context_snapshot_id: uuid.UUID,
        agent_key: str,
        digest: dict[str, Any],
        prompt_context_hash: str,
    ) -> MemoryInfluenceManifest:
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, effective_context_snapshot_id)
        if effective is None:
            raise NotFoundError(f"effective context snapshot not found: {effective_context_snapshot_id}")
        retrieval_manifest_id = _uuid_from_optional(digest.get("retrieval_manifest_id"))
        if retrieval_manifest_id is None:
            raise ValidationFailureError("RETRIEVAL_MANIFEST_REQUIRED_FOR_MEMORY_INFLUENCE")
        retrieval = self.session.get(VectorRetrievalManifest, retrieval_manifest_id)
        if retrieval is None:
            raise NotFoundError(f"vector retrieval manifest not found: {retrieval_manifest_id}")
        item_ids, facet_ids = _memory_refs_from_digest_or_manifest(digest, retrieval)
        gate = MemoryInfluenceManifestGate(self.session)
        scope_checks = [
            gate.check_manifest_scope(
                manifest=_manifest_stub(video_project_id, effective_context_snapshot_id),
                retrieval=retrieval,
                effective=effective,
            ),
            gate.check_references_scope(memory_item_ids=item_ids, effective=effective),
        ]
        reasons = sorted({reason for check in scope_checks for reason in check.reason_codes})
        scope_status = "BLOCK" if reasons else ("EMPTY_SAFE_DIGEST" if not facet_ids else "PASS")
        if scope_status == "BLOCK":
            raise ValidationFailureError(",".join(reasons))
        manifest = MemoryInfluenceManifest(
            video_project_id=video_project_id,
            package_id=package_id,
            effective_context_snapshot_id=effective.id,
            agent_key=agent_key,
            retrieval_manifest_id=retrieval.id,
            memory_facet_ids_used_json=[str(item) for item in facet_ids],
            memory_item_ids_used_json=[str(item) for item in item_ids],
            digest_hash=str(digest.get("digest_hash") or stable_hash(digest)),
            prompt_render_run_id=None,
            prompt_context_hash=prompt_context_hash,
            applied_as_json={
                "context_pack_section": "memory_digest",
                "payload_policy": "digest_only",
                "retrieval_status": digest.get("status"),
                "retrieval_hash": digest.get("retrieval_hash"),
            },
            ignored_memory_refs_json=list(retrieval.rejected_refs_json or []),
            blocked_memory_refs_json=list(retrieval.blocked_refs_json or []),
            scope_status=scope_status,
        )
        self.session.add(manifest)
        self.session.flush()
        return manifest

    def update_digest_hash(self, *, manifest_id: uuid.UUID, digest_hash: str) -> None:
        manifest = self.require_manifest(manifest_id)
        manifest.digest_hash = digest_hash
        self.session.flush()

    def link_prompt_render_run(self, *, manifest_id: uuid.UUID, prompt_render_run_id: uuid.UUID, prompt_context_hash: str) -> None:
        manifest = self.require_manifest(manifest_id)
        manifest.prompt_render_run_id = prompt_render_run_id
        manifest.prompt_context_hash = prompt_context_hash
        self.session.flush()

    def require_manifest(self, manifest_id: uuid.UUID) -> MemoryInfluenceManifest:
        manifest = self.session.get(MemoryInfluenceManifest, manifest_id)
        if manifest is None:
            raise NotFoundError(f"memory influence manifest not found: {manifest_id}")
        return manifest

    def list_manifests(
        self,
        *,
        video_project_id: uuid.UUID | None = None,
        package_id: uuid.UUID | None = None,
        agent_key: str | None = None,
        limit: int = 100,
    ) -> list[MemoryInfluenceManifest]:
        statement = select(MemoryInfluenceManifest).order_by(
            MemoryInfluenceManifest.created_at.desc(),
            MemoryInfluenceManifest.id.desc(),
        )
        if video_project_id is not None:
            statement = statement.where(MemoryInfluenceManifest.video_project_id == video_project_id)
        if package_id is not None:
            statement = statement.where(MemoryInfluenceManifest.package_id == package_id)
        if agent_key is not None:
            statement = statement.where(MemoryInfluenceManifest.agent_key == agent_key)
        return list(self.session.scalars(statement.limit(limit)).all())


class AgentMemoryDigestInjectionService:
    def __init__(self, session: Session):
        self.session = session

    def retrieve_and_record_digest(
        self,
        *,
        package_id: uuid.UUID | None,
        effective: EffectiveChannelRuntimeContextSnapshot,
        agent_key: str,
        use_case: str,
        query_text: str,
        max_selected_facets: int,
        max_digest_chars: int,
        requested_facet_types: list[str],
        vector_enabled: bool,
    ) -> dict[str, Any]:
        request = RetrievalRequest(
            effective_context_snapshot_id=effective.id,
            agent_key=agent_key,
            use_case=use_case,
            package_id=package_id,
            video_project_id=effective.video_project_id,
            query_text=query_text,
            policy=RetrievalPolicy(
                max_selected_facets=max_selected_facets,
                max_digest_chars=max_digest_chars,
                requested_facet_types=requested_facet_types,
                vector_enabled=vector_enabled,
            ),
        )
        result = VectorSafeRetrievalService(self.session).retrieve(request)
        digest = dict(result.digest)
        digest["retrieval_manifest_id"] = str(result.manifest_id)
        digest["retrieval_hash"] = result.retrieval_hash
        digest["context_pack_payload"] = "digest_only"
        pending_prompt_context_hash = stable_hash(
            {
                "pending_memory_prompt_context": True,
                "package_id": str(package_id) if package_id is not None else None,
                "agent_key": agent_key,
                "digest_hash": digest.get("digest_hash"),
                "retrieval_manifest_id": result.manifest_id,
            }
        )
        manifest = MemoryInfluenceManifestService(self.session).record_from_digest(
            video_project_id=effective.video_project_id,
            package_id=package_id,
            effective_context_snapshot_id=effective.id,
            agent_key=agent_key,
            digest=digest,
            prompt_context_hash=pending_prompt_context_hash,
        )
        digest["memory_influence_manifest_id"] = str(manifest.id)
        digest["r3d7_influence_manifest_ref"] = {
            "type": "memory_influence_manifest",
            "id": str(manifest.id),
            "scope_status": manifest.scope_status,
        }
        digest["r3d6_digest_hash"] = digest.get("digest_hash")
        digest["digest_hash"] = stable_hash(digest)
        MemoryInfluenceManifestService(self.session).update_digest_hash(manifest_id=manifest.id, digest_hash=digest["digest_hash"])
        record = self.record_application(
            video_project_id=effective.video_project_id,
            package_id=package_id,
            agent_key=agent_key,
            manifest_id=manifest.id,
            digest=digest,
        )
        digest["agent_memory_application_record_id"] = str(record.id)
        digest["digest_hash"] = stable_hash(digest)
        manifest.digest_hash = digest["digest_hash"]
        record.memory_digest_hash = digest["digest_hash"]
        self.session.flush()
        return digest

    def record_application(
        self,
        *,
        video_project_id: uuid.UUID,
        package_id: uuid.UUID | None,
        agent_key: str,
        manifest_id: uuid.UUID,
        digest: dict[str, Any],
    ) -> AgentMemoryApplicationRecord:
        record = AgentMemoryApplicationRecord(
            video_project_id=video_project_id,
            package_id=package_id,
            agent_key=agent_key,
            memory_influence_manifest_id=manifest_id,
            memory_digest_hash=str(digest.get("digest_hash") or stable_hash(digest)),
            application_mode=_application_mode_for_digest(agent_key, digest),
            applied_context_refs_json=[
                {"type": "memory_influence_manifest", "id": str(manifest_id)},
                {"type": "vector_retrieval_manifest", "id": str(digest.get("retrieval_manifest_id"))},
            ],
        )
        self.session.add(record)
        self.session.flush()
        return record


class QualityAttributionDataQualityGate:
    def __init__(self, session: Session):
        self.session = session

    def check(
        self,
        *,
        target_uploaded_video_id: uuid.UUID | None,
        observed_snapshot_ref: dict[str, Any] | None,
    ) -> GateEvaluation:
        if self._has_unresolved_severe_enforcement():
            return GateEvaluation(False, ["UNRESOLVED_SEVERE_ENFORCEMENT_FREEZE"], "BLOCKED_BY_DATA_QUALITY", {})
        if observed_snapshot_ref is not None:
            return self._check_snapshot_ref(observed_snapshot_ref)
        if target_uploaded_video_id is None:
            return GateEvaluation(False, ["TARGET_UPLOADED_VIDEO_REQUIRED"], "BLOCKED_BY_DATA_QUALITY", {})
        summary = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == target_uploaded_video_id)
        ).one_or_none()
        if summary is None:
            return GateEvaluation(False, ["OBSERVED_ANALYTICS_SNAPSHOT_MISSING"], "BLOCKED_BY_DATA_QUALITY", {})
        if summary.monitoring_state in {"NO_DATA_YET", "READY_FOR_ANALYTICS"}:
            return GateEvaluation(False, ["ANALYTICS_NOT_MATURE"], "TOO_EARLY", {"monitoring_state": summary.monitoring_state})
        if summary.freshness_state == "STALE":
            return GateEvaluation(False, ["OBSERVED_ANALYTICS_STALE"], "BLOCKED_BY_DATA_QUALITY", {})
        if summary.confidence_level in {"UNKNOWN", "LOW"}:
            return GateEvaluation(False, ["OBSERVED_ANALYTICS_LOW_CONFIDENCE"], "BLOCKED_BY_DATA_QUALITY", {})
        if _dict(summary.availability_summary).get("conflicted") is True:
            return GateEvaluation(False, ["OBSERVED_ANALYTICS_CONFLICTED"], "BLOCKED_BY_DATA_QUALITY", {})
        return GateEvaluation(True, [], "PASS", {"summary_id": str(summary.id)})

    def _check_snapshot_ref(self, snapshot_ref: dict[str, Any]) -> GateEvaluation:
        state = str(snapshot_ref.get("freshness_state") or snapshot_ref.get("data_quality") or "").upper()
        confidence = str(snapshot_ref.get("confidence_level") or snapshot_ref.get("confidence") or "").upper()
        if snapshot_ref.get("maturity_state") in {"IMMATURE", "NOT_READY"}:
            return GateEvaluation(False, ["ANALYTICS_NOT_MATURE"], "TOO_EARLY", {})
        if snapshot_ref.get("conflicted") is True:
            return GateEvaluation(False, ["OBSERVED_ANALYTICS_CONFLICTED"], "BLOCKED_BY_DATA_QUALITY", {})
        if state in {"STALE", "MISSING", "NOT_AVAILABLE"}:
            return GateEvaluation(False, [f"OBSERVED_ANALYTICS_{state}"], "BLOCKED_BY_DATA_QUALITY", {})
        if confidence in {"LOW", "UNKNOWN"}:
            return GateEvaluation(False, ["OBSERVED_ANALYTICS_LOW_CONFIDENCE"], "BLOCKED_BY_DATA_QUALITY", {})
        return GateEvaluation(True, [], "PASS", {})

    def _has_unresolved_severe_enforcement(self) -> bool:
        incidents = self.session.scalars(
            select(OpsIncident)
            .where(
                OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
                OpsIncident.severity.in_(["ERROR", "HIGH", "CRITICAL"]),
            )
            .limit(25)
        ).all()
        direct_types = {"PLATFORM_ENFORCEMENT", "YOUTUBE_ENFORCEMENT", "POLICY_ENFORCEMENT"}
        enforcement_codes = {"PLATFORM_ENFORCEMENT", "YOUTUBE_ENFORCEMENT", "POLICY_ENFORCEMENT", "ENFORCEMENT_FREEZE"}
        for incident in incidents:
            if incident.incident_type in direct_types:
                return True
            codes = {str(code).upper() for code in (incident.reason_codes or [])}
            if codes & enforcement_codes:
                return True
        return False


class QualityDeltaAttributionService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, data: QualityDeltaAttributionRunRequest) -> QualityDeltaAttribution:
        manifest = self.session.get(MemoryInfluenceManifest, data.source_memory_influence_manifest_id)
        if manifest is None:
            raise NotFoundError(f"memory influence manifest not found: {data.source_memory_influence_manifest_id}")
        target_uploaded = self.session.get(UploadedVideo, data.target_uploaded_video_id) if data.target_uploaded_video_id else None
        target_video_project_id = data.target_video_project_id or (target_uploaded.video_project_id if target_uploaded is not None else None)
        if target_video_project_id is None:
            raise ValidationFailureError("TARGET_VIDEO_PROJECT_REQUIRED")
        effective_id = data.effective_context_snapshot_id or manifest.effective_context_snapshot_id
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, effective_id)
        if effective is None:
            raise NotFoundError(f"effective context snapshot not found: {effective_id}")
        quality = QualityAttributionDataQualityGate(self.session).check(
            target_uploaded_video_id=data.target_uploaded_video_id,
            observed_snapshot_ref=data.observed_snapshot_ref,
        )
        baseline_ref = data.baseline_snapshot_ref or self._baseline_ref_for_project(manifest.video_project_id)
        observed_ref = data.observed_snapshot_ref or self._observed_ref_for_video(data.target_uploaded_video_id)
        reasons = list(quality.reason_codes)
        if quality.status == "TOO_EARLY":
            result = "TOO_EARLY"
            delta = 0
        elif quality.status == "BLOCKED_BY_DATA_QUALITY":
            result = "BLOCKED_BY_DATA_QUALITY"
            delta = 0
        elif baseline_ref is None:
            result = "BLOCKED_BY_DATA_QUALITY"
            delta = 0
            reasons.append("BASELINE_ANALYTICS_SNAPSHOT_MISSING")
        else:
            result, delta, compare_reasons = self._compare(
                expected_metric_family=data.expected_metric_family,
                direction=data.expected_improvement_direction,
                baseline_ref=baseline_ref,
                observed_ref=observed_ref,
            )
            reasons.extend(compare_reasons)
        attribution = QualityDeltaAttribution(
            source_memory_influence_manifest_id=manifest.id,
            source_video_project_id=manifest.video_project_id,
            target_uploaded_video_id=data.target_uploaded_video_id,
            target_video_project_id=target_video_project_id,
            effective_context_snapshot_id=effective.id,
            market_context_hash=stable_hash(effective.market_locale_context_json) if effective.market_locale_context_json else None,
            category_id=effective.content_category_id,
            character_binding_id=effective.character_binding_id,
            expected_metric_family=data.expected_metric_family,
            expected_improvement_direction=data.expected_improvement_direction,
            baseline_snapshot_ref=baseline_ref,
            observed_snapshot_ref=observed_ref,
            attribution_window=data.attribution_window,
            confidence_result=result,
            confidence_delta=delta,
            reason_codes_json=sorted(set(reasons)),
            notes=data.notes,
        )
        self.session.add(attribution)
        self.session.flush()
        self._record_confidence_updates(manifest=manifest, attribution=attribution)
        return attribution

    def _compare(
        self,
        *,
        expected_metric_family: str,
        direction: str,
        baseline_ref: dict[str, Any] | None,
        observed_ref: dict[str, Any] | None,
    ) -> tuple[str, int, list[str]]:
        metric_keys = _metric_keys_for_family(expected_metric_family)
        baseline_value = _extract_first_metric(baseline_ref, metric_keys)
        observed_value = _extract_first_metric(observed_ref, metric_keys)
        if baseline_value is None or observed_value is None:
            return "BLOCKED_BY_DATA_QUALITY", 0, ["ATTRIBUTION_METRIC_MISSING"]
        if abs(observed_value - baseline_value) < 0.000001:
            return "INCONCLUSIVE", 0, ["NO_MEASURABLE_DELTA"]
        improved = observed_value > baseline_value if direction == "HIGHER" else observed_value < baseline_value
        if improved:
            return "IMPROVED", 1, [f"METRIC_{metric_keys[0]}_IMPROVED"]
        return "DEGRADED", -1, [f"METRIC_{metric_keys[0]}_DEGRADED"]

    def _record_confidence_updates(self, *, manifest: MemoryInfluenceManifest, attribution: QualityDeltaAttribution) -> None:
        facet_ids = [_uuid_from_optional(value) for value in manifest.memory_facet_ids_used_json]
        for facet_id in [value for value in facet_ids if value is not None]:
            facet = self.session.get(MemoryFacet, facet_id)
            if facet is None:
                continue
            old_label = facet.confidence_label
            new_label, ledger_delta, _requires_review, reasons = _confidence_after_attribution(
                old_label=old_label,
                result=attribution.confidence_result,
            )
            if attribution.confidence_result in {"IMPROVED", "DEGRADED", "BLOCKED_BY_DATA_QUALITY"}:
                ledger = MemoryConfidenceUpdateLedger(
                    memory_facet_id=facet.id,
                    quality_delta_attribution_id=attribution.id,
                    old_confidence_label=old_label,
                    new_confidence_label=new_label,
                    confidence_delta=ledger_delta,
                    reason_codes_json=sorted(
                        set(
                            [
                                *attribution.reason_codes_json,
                                *reasons,
                                "ACTIVE_MEMORY_CONFIDENCE_UNCHANGED",
                                "CONFIDENCE_CHANGE_PROPOSAL_ONLY",
                            ]
                        )
                    ),
                    requires_human_review=True,
                )
                self.session.add(ledger)
        self.session.flush()

    def _baseline_ref_for_project(self, video_project_id: uuid.UUID) -> dict[str, Any] | None:
        summary = self.session.scalars(
            select(UploadedVideoMetricsSummary)
            .where(UploadedVideoMetricsSummary.video_project_id == video_project_id)
            .order_by(UploadedVideoMetricsSummary.latest_captured_at.desc().nullslast(), UploadedVideoMetricsSummary.created_at.desc())
            .limit(1)
        ).one_or_none()
        if summary is None:
            return None
        return _summary_snapshot_ref(summary)

    def _observed_ref_for_video(self, uploaded_video_id: uuid.UUID | None) -> dict[str, Any] | None:
        if uploaded_video_id is None:
            return None
        summary = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded_video_id)
        ).one_or_none()
        return _summary_snapshot_ref(summary) if summary is not None else None


class LearningLoopEligibilityGate:
    def __init__(self, session: Session):
        self.session = session

    def check_for_attribution(
        self,
        *,
        uploaded_video_id: uuid.UUID,
        learning_candidate_id: uuid.UUID,
        memory_facet_ids: list[uuid.UUID],
        retrieval_manifest_id: uuid.UUID,
        influence_manifest_id: uuid.UUID,
    ) -> GateEvaluation:
        reasons: list[str] = []
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            reasons.append("UPLOADED_VIDEO_MISSING")
        if not self._learning_candidate_approved(learning_candidate_id):
            reasons.append("LEARNING_CANDIDATE_NOT_HUMAN_APPROVED")
        for facet_id in memory_facet_ids:
            facet = self.session.get(MemoryFacet, facet_id)
            item = self.session.get(ChannelMemoryItem, facet.memory_item_id) if facet is not None else None
            if facet is None or item is None:
                reasons.append("MEMORY_FACET_MISSING")
                continue
            if item.approval_status != "APPROVED" or item.rights_status != "SAFE" or item.prompt_safety_state != "PROMPT_SAFE":
                reasons.append("MEMORY_NOT_APPROVED_SAFE_PROMPT_SAFE")
        if self.session.get(VectorRetrievalManifest, retrieval_manifest_id) is None:
            reasons.append("RETRIEVAL_MANIFEST_MISSING")
        if self.session.get(MemoryInfluenceManifest, influence_manifest_id) is None:
            reasons.append("INFLUENCE_MANIFEST_MISSING")
        data_quality = QualityAttributionDataQualityGate(self.session).check(
            target_uploaded_video_id=uploaded_video_id,
            observed_snapshot_ref=None,
        )
        if data_quality.status in {"BLOCKED_BY_DATA_QUALITY", "TOO_EARLY"}:
            reasons.extend(data_quality.reason_codes)
        return GateEvaluation(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCK", {})

    def _learning_candidate_approved(self, learning_candidate_id: uuid.UUID) -> bool:
        approved = self.session.scalars(
            select(ApprovedPlaybookEntry).where(
                ApprovedPlaybookEntry.learning_candidate_id == learning_candidate_id,
                ApprovedPlaybookEntry.state == "APPROVED",
            )
        ).first()
        decision = self.session.scalars(
            select(LearningReviewDecision).where(
                LearningReviewDecision.learning_candidate_id == learning_candidate_id,
                LearningReviewDecision.action == "APPROVE",
            )
        ).first()
        return approved is not None or decision is not None


class ClosedLearningLoopService:
    def __init__(self, session: Session):
        self.session = session

    def status(
        self,
        *,
        uploaded_video_id: uuid.UUID | None = None,
        target_video_project_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        steps = [
            self._step("uploaded_video", uploaded_video_id is not None and self.session.get(UploadedVideo, uploaded_video_id) is not None),
            self._step("failure_trace_report", self._exists(FailureTraceReport, FailureTraceReport.uploaded_video_id == uploaded_video_id) if uploaded_video_id else False),
            self._step("recovery_proposal", self._exists(RecoveryProposal, RecoveryProposal.uploaded_video_id == uploaded_video_id) if uploaded_video_id else False),
            self._step("learning_candidate", self._exists(LearningCandidate, LearningCandidate.uploaded_video_id == uploaded_video_id) if uploaded_video_id else False),
            self._step("approved_playbook_entry", self._approved_playbook_exists(uploaded_video_id) if uploaded_video_id else False),
            self._step("promotion_run", self._promotion_run_exists(uploaded_video_id) if uploaded_video_id else False),
            self._step("controlled_memory", self._controlled_memory_exists(uploaded_video_id) if uploaded_video_id else False),
            self._step("embedding_or_embedding_job", self._embedding_exists(uploaded_video_id) if uploaded_video_id else False),
            self._step("retrieval_manifest", self._exists(VectorRetrievalManifest, VectorRetrievalManifest.video_project_id == target_video_project_id) if target_video_project_id else False),
            self._step("memory_influence_manifest", self._exists(MemoryInfluenceManifest, MemoryInfluenceManifest.video_project_id == target_video_project_id) if target_video_project_id else False),
            self._step("quality_delta_attribution", self._exists(QualityDeltaAttribution, QualityDeltaAttribution.target_video_project_id == target_video_project_id) if target_video_project_id else False),
        ]
        missing = [step["step"] for step in steps if step["status"] != "PASS"]
        return {
            "status": "COMPLETED" if not missing else "INCOMPLETE",
            "uploaded_video_id": str(uploaded_video_id) if uploaded_video_id else None,
            "target_video_project_id": str(target_video_project_id) if target_video_project_id else None,
            "steps": steps,
            "reason_codes": [f"{name.upper()}_MISSING" for name in missing],
            "next_action": None if not missing else f"Hoan tat buoc: {missing[0]}",
        }

    def _step(self, name: str, passed: bool) -> dict[str, Any]:
        return {"step": name, "status": "PASS" if passed else "MISSING"}

    def _exists(self, model: type[Any], *conditions: Any) -> bool:
        statement = select(model).limit(1)
        for condition in conditions:
            statement = statement.where(condition)
        return self.session.scalars(statement).first() is not None

    def _approved_playbook_exists(self, uploaded_video_id: uuid.UUID | None) -> bool:
        statement = (
            select(ApprovedPlaybookEntry)
            .join(LearningCandidate, LearningCandidate.id == ApprovedPlaybookEntry.learning_candidate_id)
            .where(LearningCandidate.uploaded_video_id == uploaded_video_id, ApprovedPlaybookEntry.state == "APPROVED")
        )
        return self.session.scalars(statement.limit(1)).first() is not None

    def _promotion_run_exists(self, uploaded_video_id: uuid.UUID | None) -> bool:
        return self._exists(LearningToMemoryPromotionRun, LearningToMemoryPromotionRun.source_uploaded_video_id == uploaded_video_id)

    def _controlled_memory_exists(self, uploaded_video_id: uuid.UUID | None) -> bool:
        candidate = self.session.scalars(select(LearningCandidate).where(LearningCandidate.uploaded_video_id == uploaded_video_id)).first()
        if candidate is None:
            return False
        return self._exists(ChannelMemoryItem, ChannelMemoryItem.created_from_learning_candidate_id == candidate.id)

    def _embedding_exists(self, uploaded_video_id: uuid.UUID | None) -> bool:
        candidate = self.session.scalars(select(LearningCandidate).where(LearningCandidate.uploaded_video_id == uploaded_video_id)).first()
        if candidate is None:
            return False
        item = self.session.scalars(select(ChannelMemoryItem).where(ChannelMemoryItem.created_from_learning_candidate_id == candidate.id)).first()
        if item is None:
            return False
        facet_ids = [facet.id for facet in self.session.scalars(select(MemoryFacet).where(MemoryFacet.memory_item_id == item.id)).all()]
        if not facet_ids:
            return False
        return (
            self.session.scalars(select(EmbeddingFacet).where(EmbeddingFacet.memory_facet_id.in_(facet_ids)).limit(1)).first() is not None
            or self.session.scalars(select(EmbeddingJob).where(EmbeddingJob.memory_facet_id.in_(facet_ids)).limit(1)).first() is not None
        )


def _system_memory_type(candidate_type: str) -> str:
    return {
        "PACKAGING_PATTERN": "PACKAGING_PATTERN",
        "HOOK_PATTERN": "WINNING_HOOK",
        "RETENTION_PATTERN": "RETENTION_LESSON",
        "VISUAL_SOURCE_PATTERN": "VISUAL_PATTERN",
        "COST_EFFICIENCY_PATTERN": "COST_EFFICIENCY_LESSON",
    }[candidate_type]


def _system_facet_type(candidate_type: str) -> str:
    return {
        "PACKAGING_PATTERN": "PACKAGING_PATTERN",
        "HOOK_PATTERN": "WINNING_HOOK",
        "RETENTION_PATTERN": "RETENTION_LESSON",
        "VISUAL_SOURCE_PATTERN": "VISUAL_PATTERN",
        "COST_EFFICIENCY_PATTERN": "COST_EFFICIENCY_LESSON",
    }[candidate_type]


def _manifest_stub(video_project_id: uuid.UUID, effective_context_snapshot_id: uuid.UUID) -> MemoryInfluenceManifest:
    return MemoryInfluenceManifest(
        video_project_id=video_project_id,
        package_id=None,
        effective_context_snapshot_id=effective_context_snapshot_id,
        agent_key="_gate_stub",
        retrieval_manifest_id=uuid.uuid4(),
        memory_facet_ids_used_json=[],
        memory_item_ids_used_json=[],
        digest_hash=stable_hash({"stub": True}),
        prompt_context_hash=stable_hash({"stub": True}),
        applied_as_json={},
        ignored_memory_refs_json=[],
        blocked_memory_refs_json=[],
        scope_status="PASS",
    )


def _uuid_from_optional(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _memory_refs_from_digest_or_manifest(
    digest: dict[str, Any],
    retrieval: VectorRetrievalManifest,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    refs = digest.get("selected_memory_facet_refs") or digest.get("applied_lesson_refs") or retrieval.selected_memory_facet_refs_json or []
    item_ids: list[uuid.UUID] = []
    facet_ids: list[uuid.UUID] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        item_id = _uuid_from_optional(ref.get("memory_item_id"))
        facet_id = _uuid_from_optional(ref.get("memory_facet_id"))
        if item_id is not None:
            item_ids.append(item_id)
        if facet_id is not None:
            facet_ids.append(facet_id)
    return sorted(set(item_ids), key=str), sorted(set(facet_ids), key=str)


def _application_mode_for_digest(agent_key: str, digest: dict[str, Any]) -> str:
    if digest.get("status") == "EMPTY_SAFE_DIGEST":
        return "GUIDANCE"
    if any(str(item).upper() == "NEGATIVE" for item in _flatten_polarities(digest)):
        return "AVOID_PATTERN"
    return {
        "ScriptWriterAgent": "GUIDANCE",
        "ScriptPlanningAgent": "GUIDANCE",
        "VisualPlanningAgent": "VISUAL_HINT",
        "ThumbnailBriefAgent": "PACKAGING_HINT",
        "PublishingMetadataAgent": "METADATA_HINT",
        "GatekeeperSoftReviewAgent": "GUIDANCE",
    }.get(agent_key, "GUIDANCE")


def _flatten_polarities(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(value.get("polarity"))] + [item for child in value.values() for item in _flatten_polarities(child)]
    if isinstance(value, list):
        return [item for child in value for item in _flatten_polarities(child)]
    return []


def _metric_keys_for_family(family: str) -> list[str]:
    mapping = {
        "FAILED_HOOK": ["first_30s_hold", "average_view_percentage", "average_view_duration_seconds"],
        "PACKAGING_PATTERN": ["click_through_rate", "impressions_click_through_rate"],
        "THUMBNAIL_PATTERN": ["click_through_rate", "impressions_click_through_rate"],
        "VISUAL_PATTERN": ["average_view_percentage", "average_view_duration_seconds"],
        "METADATA_PATTERN": ["views", "impressions", "watch_time_minutes"],
    }
    return mapping.get(family, [family])


def _extract_first_metric(snapshot_ref: dict[str, Any] | None, keys: list[str]) -> float | None:
    if snapshot_ref is None:
        return None
    candidates = [
        snapshot_ref,
        _dict(snapshot_ref.get("metrics")),
        _dict(snapshot_ref.get("metrics_summary")),
        _dict(snapshot_ref.get("normalized_metrics_blob")),
    ]
    for blob in candidates:
        for key in keys:
            value = blob.get(key)
            if isinstance(value, bool) or value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _summary_snapshot_ref(summary: UploadedVideoMetricsSummary) -> dict[str, Any]:
    return {
        "uploaded_video_metrics_summary_id": str(summary.id),
        "uploaded_video_id": str(summary.uploaded_video_id),
        "metrics_summary": summary.metrics_summary,
        "availability_summary": summary.availability_summary,
        "freshness_state": summary.freshness_state,
        "confidence_level": summary.confidence_level,
        "monitoring_state": summary.monitoring_state,
        "captured_at": summary.latest_captured_at.isoformat() if summary.latest_captured_at else None,
    }


def _confidence_after_attribution(*, old_label: str, result: str) -> tuple[str, int, bool, list[str]]:
    normalized = old_label if old_label in CONFIDENCE_ORDER else "UNPROVEN"
    index = CONFIDENCE_ORDER.index(normalized)
    if result == "IMPROVED":
        proposed = CONFIDENCE_ORDER[min(index + 1, len(CONFIDENCE_ORDER) - 1)]
        reasons = ["QUALITY_DELTA_IMPROVED"]
        if proposed == "HIGH" and normalized != "HIGH":
            proposed = "MEDIUM"
            reasons.append("ONE_SAMPLE_CONFIDENCE_CAP")
        return proposed, CONFIDENCE_ORDER.index(proposed) - index, proposed == "HIGH", reasons
    if result == "DEGRADED":
        proposed = CONFIDENCE_ORDER[max(index - 1, 0)]
        return proposed, CONFIDENCE_ORDER.index(proposed) - index, True, ["QUALITY_DELTA_DEGRADED", "HUMAN_REVIEW_RECOMMENDED"]
    if result == "BLOCKED_BY_DATA_QUALITY":
        return normalized, 0, True, ["DATA_QUALITY_BLOCKED_CONFIDENCE_UNCHANGED"]
    return normalized, 0, False, ["CONFIDENCE_UNCHANGED"]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
