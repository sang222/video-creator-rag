from __future__ import annotations

# Compatibility note: semantic facade `controlled_memory` re-exports this implementation; phase-coded import kept for reports/tests/backward compatibility.
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.r3d5 import (
    ChannelMemoryDraftCreate,
    MemoryApprovalRequest,
    MemoryFacetInput,
    MemoryFromApprovedPlaybookCreate,
    MemoryUsageManifestCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ApprovedPlaybookEntry,
    ChannelMemoryItem,
    EffectiveChannelRuntimeContextSnapshot,
    LearningCandidate,
    LearningReviewDecision,
    MemoryApprovalDecision,
    MemoryFacet,
    MemoryReviewQueueItem,
    MemorySourceLink,
    MemoryUsageManifest,
)


MEMORY_TYPES = {
    "CHANNEL_IDENTITY",
    "CATEGORY_STYLE",
    "WINNING_HOOK",
    "FAILED_HOOK",
    "PACKAGING_PATTERN",
    "RETENTION_LESSON",
    "VISUAL_PATTERN",
    "THUMBNAIL_PATTERN",
    "METADATA_PATTERN",
    "AVOID_REPEAT",
    "APPROVED_PLAYBOOK",
    "SOURCE_QUALITY_LESSON",
    "COST_EFFICIENCY_LESSON",
    "PROVIDER_BOUNDARY_LESSON",
    "CHARACTER_CONTINUITY_LESSON",
    "MARKET_LOCALE_LESSON",
}
SOURCE_TYPES = {
    "HUMAN_REFERENCE",
    "PUBLISHED_VIDEO",
    "FAILURE_TRACE_REPORT",
    "RECOVERY_PROPOSAL",
    "LEARNING_CANDIDATE",
    "LEARNING_EVIDENCE_BUNDLE",
    "APPROVED_PLAYBOOK_ENTRY",
    "APPROVED_ARTIFACT",
    "MANUAL_REJECTED_EXAMPLE",
    "SYSTEM_POLICY_LEARNING",
}
APPROVAL_STATUSES = {"DRAFT", "REVIEW_REQUIRED", "APPROVED", "REJECTED", "ARCHIVED"}
RIGHTS_STATUSES = {"UNKNOWN", "SAFE", "RESTRICTED", "EXPIRED", "BLOCKED"}
PROMPT_SAFETY_STATES = {"UNKNOWN", "PROMPT_SAFE", "NOT_PROMPT_SAFE"}
REUSE_SCOPES = {"CHANNEL", "CATEGORY", "SERIES", "CHARACTER", "COMPANY_APPROVED"}
FRESHNESS_STATES = {"FRESH", "STALE", "EXPIRED", "NEEDS_REVIEW"}
QUEUE_STATUSES = {"PENDING", "IN_REVIEW", "APPROVED", "REJECTED", "NEEDS_CHANGES"}
MEMORY_USAGE_STATUSES = {"PLANNED", "USED_IN_DIGEST", "BLOCKED", "IGNORED"}
SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "xoxb-", "ghp_", "refresh_token", "access_token")
RAW_BLOB_MARKERS = ("full_script", "raw_provider_payload", "analytics_snapshot", "provider_response", "sentences")


@dataclass(frozen=True)
class MemoryGateResult:
    passed: bool
    reason_codes: list[str]
    summary: str


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
    return stable_hash({"text": _normalize_text(text)})


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in markers)


def _status(ok: bool, code: str, summary: str) -> MemoryGateResult:
    return MemoryGateResult(passed=ok, reason_codes=[] if ok else [code], summary=summary)


class MemoryApprovalGate:
    def check(self, item: ChannelMemoryItem) -> MemoryGateResult:
        return _status(item.approval_status == "APPROVED", "MEMORY_NOT_APPROVED", "Memory item must be APPROVED.")


class MemoryRightsGate:
    def check(self, item: ChannelMemoryItem) -> MemoryGateResult:
        return _status(item.rights_status == "SAFE", "MEMORY_RIGHTS_NOT_SAFE", "Memory rights_status must be SAFE.")


class MemoryPromptSafetyGate:
    def check(self, item: ChannelMemoryItem, facet: MemoryFacet | None = None) -> MemoryGateResult:
        if item.prompt_safety_state != "PROMPT_SAFE":
            return _status(False, "MEMORY_ITEM_NOT_PROMPT_SAFE", "Memory item is not prompt safe.")
        if facet is not None and facet.prompt_safety_state != "PROMPT_SAFE":
            return _status(False, "MEMORY_FACET_NOT_PROMPT_SAFE", "Memory facet is not prompt safe.")
        return _status(True, "", "Memory prompt safety passed.")


class MemoryFreshnessGate:
    def check(self, item: ChannelMemoryItem) -> MemoryGateResult:
        return _status(item.freshness_state == "FRESH", "MEMORY_NOT_FRESH", "Memory must be FRESH.")


class MemoryScopeGate:
    def check(
        self,
        *,
        item: ChannelMemoryItem,
        effective_context: EffectiveChannelRuntimeContextSnapshot,
        allow_company_approved: bool = False,
    ) -> MemoryGateResult:
        reasons: list[str] = []
        if item.company_id != effective_context.company_id:
            reasons.append("MEMORY_SCOPE_COMPANY_MISMATCH")
        if item.reuse_scope == "COMPANY_APPROVED" and not allow_company_approved:
            reasons.append("COMPANY_APPROVED_MEMORY_REQUIRES_EXPLICIT_ALLOW")
        if item.channel_workspace_id != effective_context.channel_workspace_id:
            if not (allow_company_approved and item.reuse_scope == "COMPANY_APPROVED"):
                reasons.append("CROSS_CHANNEL_MEMORY_BLOCKED")
        if item.content_category_id is not None and item.content_category_id != effective_context.content_category_id:
            reasons.append("MEMORY_SCOPE_CATEGORY_MISMATCH")
        if item.reuse_scope == "CATEGORY" and item.content_category_id is None:
            reasons.append("CATEGORY_MEMORY_REQUIRES_CATEGORY_REF")
        character_specific = any([item.character_profile_id, item.character_version_id, item.character_binding_id]) or item.reuse_scope == "CHARACTER"
        if character_specific:
            if effective_context.character_policy_mode == "NO_CHARACTER":
                reasons.append("NO_CHARACTER_CONTEXT_BLOCKS_CHARACTER_MEMORY")
            if item.character_profile_id is None or item.character_version_id is None:
                reasons.append("CHARACTER_MEMORY_REQUIRES_CHARACTER_REFS")
            if item.character_profile_id is not None and item.character_profile_id != effective_context.character_profile_id:
                reasons.append("CHARACTER_PROFILE_SCOPE_MISMATCH")
            if item.character_version_id is not None and item.character_version_id != effective_context.character_version_id:
                reasons.append("CHARACTER_VERSION_SCOPE_MISMATCH")
            if item.character_binding_id is not None and item.character_binding_id != effective_context.character_binding_id:
                reasons.append("CHARACTER_BINDING_SCOPE_MISMATCH")
        return MemoryGateResult(not reasons, reasons, "Memory scope gate evaluated.")


class MemoryPromptBudgetGate:
    def __init__(self, *, max_facet_chars: int = 700):
        self.max_facet_chars = max_facet_chars

    def check(self, facet: MemoryFacetInput | MemoryFacet) -> MemoryGateResult:
        text = facet.facet_text
        reasons: list[str] = []
        if len(text) > self.max_facet_chars:
            reasons.append("MEMORY_FACET_TOO_LARGE")
        if _contains_any(text, RAW_BLOB_MARKERS):
            reasons.append("RAW_ARTIFACT_BLOB_BLOCKED")
        if _contains_any(text, SECRET_MARKERS):
            reasons.append("SECRET_LIKE_MEMORY_BLOCKED")
        return MemoryGateResult(not reasons, reasons, "Memory prompt budget gate evaluated.")


class MemoryDuplicationGate:
    def __init__(self, session: Session):
        self.session = session

    def check(
        self,
        *,
        source_content_hash: str,
        facet_text_hash: str | None = None,
        cooldown_key: str | None = None,
        exclude_memory_item_id: uuid.UUID | None = None,
    ) -> MemoryGateResult:
        reasons: list[str] = []
        item_statement = select(ChannelMemoryItem).where(ChannelMemoryItem.source_content_hash == source_content_hash)
        if exclude_memory_item_id is not None:
            item_statement = item_statement.where(ChannelMemoryItem.id != exclude_memory_item_id)
        if self.session.scalars(item_statement.limit(1)).one_or_none() is not None:
            reasons.append("DUPLICATE_SOURCE_CONTENT_HASH")
        if facet_text_hash is not None:
            facet = self.session.scalars(select(MemoryFacet).where(MemoryFacet.facet_text_hash == facet_text_hash).limit(1)).one_or_none()
            if facet is not None:
                reasons.append("DUPLICATE_FACET_TEXT_HASH")
        if cooldown_key:
            facet = self.session.scalars(
                select(MemoryFacet).where(MemoryFacet.scope_json["cooldown_key"].astext == cooldown_key).limit(1)
            ).one_or_none()
            if facet is not None:
                reasons.append("DUPLICATE_COOLDOWN_KEY")
        return MemoryGateResult(not reasons, reasons, "Memory duplication gate evaluated.")


class RetrievalAuditGate:
    def check_manifest_required(self, *, manifest_id: uuid.UUID | None) -> MemoryGateResult:
        return _status(manifest_id is not None, "RETRIEVAL_MANIFEST_REQUIRED", "Future retrieval must persist a manifest.")


class MemoryFacetExtractor:
    def __init__(self, *, max_facet_chars: int = 420):
        self.max_facet_chars = max_facet_chars

    def from_approved_playbook_entry(
        self,
        entry: ApprovedPlaybookEntry,
        *,
        facet_type: str | None = None,
        allowed_use_cases_json: list[str] | None = None,
        embedding_eligible: bool = False,
    ) -> list[MemoryFacetInput]:
        text = _normalize_text(entry.playbook_text)
        self._validate_source_text(text)
        pieces = [piece for piece in re.split(r"(?<=[.!?])\s+", text) if piece]
        if not pieces:
            pieces = [text]
        facets: list[MemoryFacetInput] = []
        for piece in pieces[:4]:
            facet_text = piece[: self.max_facet_chars].strip()
            if not facet_text:
                continue
            facets.append(
                MemoryFacetInput(
                    facet_type=facet_type or _facet_type_from_playbook_category(entry.category),
                    facet_text=facet_text,
                    scope_json={"approved_playbook_entry_id": str(entry.id), "playbook_scope": entry.scope},
                    allowed_use_cases_json=allowed_use_cases_json or [],
                    polarity="NEGATIVE" if entry.category in {"RECOVERY", "POLICY"} and "avoid" in facet_text.lower() else "POSITIVE",
                    confidence_label="HIGH",
                    prompt_safety_state="PROMPT_SAFE",
                    embedding_eligible=embedding_eligible,
                )
            )
        return facets

    def from_manual_reference(
        self,
        *,
        text: str,
        facet_type: str,
        polarity: str = "NEUTRAL",
        allowed_use_cases_json: list[str] | None = None,
        prompt_safety_state: str = "PROMPT_SAFE",
    ) -> list[MemoryFacetInput]:
        normalized = _normalize_text(text)
        self._validate_source_text(normalized)
        return [
            MemoryFacetInput(
                facet_type=facet_type,
                facet_text=normalized[: self.max_facet_chars],
                allowed_use_cases_json=allowed_use_cases_json or [],
                polarity=polarity,
                confidence_label="MEDIUM",
                prompt_safety_state=prompt_safety_state,
                embedding_eligible=False,
            )
        ]

    def _validate_source_text(self, text: str) -> None:
        if _contains_any(text, SECRET_MARKERS):
            raise ValidationFailureError("memory facet source contains secret-like text")
        if _contains_any(text, RAW_BLOB_MARKERS) or len(text) > 6000:
            raise ValidationFailureError("memory facet extraction refuses raw artifact/provider/analytics blobs")


class ControlledMemoryService:
    def __init__(self, session: Session):
        self.session = session
        self.extractor = MemoryFacetExtractor()

    def create_draft(self, *, data: ChannelMemoryDraftCreate) -> ChannelMemoryItem:
        _validate_enum(data.memory_type, MEMORY_TYPES, "memory_type")
        _validate_enum(data.source_type, SOURCE_TYPES, "source_type")
        _validate_enum(data.rights_status, RIGHTS_STATUSES, "rights_status")
        _validate_enum(data.prompt_safety_state, PROMPT_SAFETY_STATES, "prompt_safety_state")
        _validate_enum(data.reuse_scope, REUSE_SCOPES, "reuse_scope")
        _validate_enum(data.freshness_state, FRESHNESS_STATES, "freshness_state")
        self._validate_learning_source(data)
        source_payload = data.source_content if data.source_content is not None else {
            "source_type": data.source_type,
            "source_ref": data.source_ref,
            "summary": data.summary,
        }
        source_content_hash = stable_hash({"memory_source": source_payload})
        duplicate = MemoryDuplicationGate(self.session).check(source_content_hash=source_content_hash)
        if not duplicate.passed:
            raise ValidationFailureError(",".join(duplicate.reason_codes))
        content_hash = self.compute_item_hash(data=data, source_content_hash=source_content_hash)
        item = ChannelMemoryItem(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            content_category_id=data.content_category_id,
            series_id=data.series_id,
            character_profile_id=data.character_profile_id,
            character_version_id=data.character_version_id,
            character_binding_id=data.character_binding_id,
            memory_type=data.memory_type,
            source_type=data.source_type,
            source_ref=data.source_ref,
            source_content_hash=source_content_hash,
            summary=_normalize_text(data.summary),
            approval_status="DRAFT",
            rights_status=data.rights_status,
            prompt_safety_state=data.prompt_safety_state,
            reuse_scope=data.reuse_scope,
            freshness_state=data.freshness_state,
            created_from_learning_candidate_id=data.created_from_learning_candidate_id,
            created_from_failure_trace_report_id=data.created_from_failure_trace_report_id,
            created_from_recovery_proposal_id=data.created_from_recovery_proposal_id,
            created_from_approved_playbook_entry_id=data.created_from_approved_playbook_entry_id,
            content_hash=content_hash,
        )
        self.session.add(item)
        self.session.flush()
        self._create_source_link(item)
        if data.facets:
            self.create_facets(memory_item_id=item.id, facets=data.facets)
        return item

    def create_facets(self, *, memory_item_id: uuid.UUID, facets: list[MemoryFacetInput]) -> list[MemoryFacet]:
        item = self.require_item(memory_item_id)
        created: list[MemoryFacet] = []
        for facet_data in facets:
            prompt_budget = MemoryPromptBudgetGate().check(facet_data)
            if not prompt_budget.passed:
                raise ValidationFailureError(",".join(prompt_budget.reason_codes))
            facet_hash = _text_hash(facet_data.facet_text)
            duplicate = MemoryDuplicationGate(self.session).check(
                source_content_hash=f"facet-only:{uuid.uuid4()}",
                facet_text_hash=facet_hash,
            )
            if "DUPLICATE_FACET_TEXT_HASH" in duplicate.reason_codes:
                raise ValidationFailureError("DUPLICATE_FACET_TEXT_HASH")
            facet = MemoryFacet(
                memory_item_id=item.id,
                company_id=item.company_id,
                channel_workspace_id=item.channel_workspace_id,
                content_category_id=item.content_category_id,
                character_profile_id=item.character_profile_id,
                character_version_id=item.character_version_id,
                facet_type=facet_data.facet_type,
                facet_text=_normalize_text(facet_data.facet_text),
                facet_text_hash=facet_hash,
                scope_json=facet_data.scope_json,
                allowed_use_cases_json=facet_data.allowed_use_cases_json,
                forbidden_use_cases_json=facet_data.forbidden_use_cases_json,
                polarity=facet_data.polarity,
                confidence_label=facet_data.confidence_label,
                prompt_safety_state=facet_data.prompt_safety_state,
                embedding_eligible=facet_data.embedding_eligible,
            )
            self.session.add(facet)
            created.append(facet)
        self.session.flush()
        return created

    def submit_to_review_queue(
        self,
        *,
        memory_item_id: uuid.UUID,
        reason_codes: list[str] | None = None,
        reviewer_notes: str | None = None,
    ) -> MemoryReviewQueueItem:
        item = self.require_item(memory_item_id)
        if item.approval_status == "DRAFT":
            item.approval_status = "REVIEW_REQUIRED"
        queue = MemoryReviewQueueItem(
            memory_item_id=item.id,
            queue_status="PENDING",
            reason_codes_json=reason_codes or ["MEMORY_REVIEW_REQUIRED"],
            reviewer_notes=reviewer_notes,
        )
        self.session.add(queue)
        self.session.flush()
        return queue

    def create_from_approved_playbook_entry(
        self,
        *,
        playbook_entry_id: uuid.UUID,
        data: MemoryFromApprovedPlaybookCreate | None = None,
    ) -> ChannelMemoryItem:
        request = data or MemoryFromApprovedPlaybookCreate()
        entry = self.session.get(ApprovedPlaybookEntry, playbook_entry_id)
        if entry is None:
            raise NotFoundError(f"approved playbook entry not found: {playbook_entry_id}")
        if entry.state != "APPROVED":
            raise ValidationFailureError("approved playbook entry must be APPROVED")
        if entry.channel_workspace_id is None:
            raise ValidationFailureError("approved playbook memory requires an origin channel_workspace_id")
        facets = self.extractor.from_approved_playbook_entry(
            entry,
            facet_type=request.facet_type,
            allowed_use_cases_json=request.allowed_use_cases_json,
            embedding_eligible=request.embedding_eligible,
        )
        item = self.create_draft(
            data=ChannelMemoryDraftCreate(
                company_id=entry.company_id,
                channel_workspace_id=entry.channel_workspace_id,
                content_category_id=request.content_category_id,
                series_id=request.series_id,
                character_profile_id=request.character_profile_id,
                character_version_id=request.character_version_id,
                character_binding_id=request.character_binding_id,
                memory_type=request.memory_type,
                source_type="APPROVED_PLAYBOOK_ENTRY",
                source_ref=str(entry.id),
                source_content={"playbook_text": entry.playbook_text, "entry_id": str(entry.id)},
                summary=entry.playbook_text[:500],
                rights_status="SAFE" if request.rights_safe else "UNKNOWN",
                prompt_safety_state="PROMPT_SAFE" if request.prompt_safe else "UNKNOWN",
                reuse_scope=request.reuse_scope,
                created_from_learning_candidate_id=entry.learning_candidate_id,
                created_from_approved_playbook_entry_id=entry.id,
                facets=facets,
            )
        )
        self.submit_to_review_queue(memory_item_id=item.id, reason_codes=["APPROVED_PLAYBOOK_MEMORY_REVIEW_REQUIRED"])
        return item

    def create_draft_from_learning_candidate(self, *, candidate_id: uuid.UUID) -> ChannelMemoryItem:
        candidate = self.session.get(LearningCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"learning candidate not found: {candidate_id}")
        if candidate.candidate_state in {"EXPIRED", "CANCELLED", "INELIGIBLE_LOW_EVIDENCE", "BLOCKED_POLICY_RISK", "BLOCKED_RIGHTS_RISK"}:
            raise ValidationFailureError("rejected/suppressed/expired learning cannot become memory")
        if not self._learning_candidate_has_human_approval(candidate.id):
            raise ValidationFailureError("learning candidate requires human approval or approved playbook link before memory draft")
        text = candidate.suggested_playbook_text or candidate.suggested_learning
        facets = self.extractor.from_manual_reference(
            text=text,
            facet_type=_facet_type_from_candidate(candidate.candidate_type),
            polarity="POSITIVE",
            allowed_use_cases_json=[],
        )
        item = self.create_draft(
            data=ChannelMemoryDraftCreate(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                memory_type=_memory_type_from_candidate(candidate.candidate_type),
                source_type="LEARNING_CANDIDATE",
                source_ref=str(candidate.id),
                source_content={"suggested_learning": text, "candidate_id": str(candidate.id)},
                summary=candidate.candidate_summary,
                rights_status="SAFE",
                prompt_safety_state="PROMPT_SAFE",
                reuse_scope="CHANNEL",
                created_from_learning_candidate_id=candidate.id,
                facets=facets,
            )
        )
        self.submit_to_review_queue(memory_item_id=item.id, reason_codes=["HUMAN_APPROVED_LEARNING_MEMORY_REVIEW_REQUIRED"])
        return item

    def create_failed_output_avoid_memory(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        source_ref: str,
        failed_output_summary: str,
        avoid_rule: str,
        approved_by: uuid.UUID | None,
        content_category_id: uuid.UUID | None = None,
    ) -> ChannelMemoryItem:
        if approved_by is None:
            raise ValidationFailureError("failed output memory requires human approval")
        facets = self.extractor.from_manual_reference(
            text=avoid_rule,
            facet_type="AVOID_REPEAT",
            polarity="NEGATIVE",
            allowed_use_cases_json=["script", "planning", "gatekeeper"],
        )
        item = self.create_draft(
            data=ChannelMemoryDraftCreate(
                company_id=company_id,
                channel_workspace_id=channel_workspace_id,
                content_category_id=content_category_id,
                memory_type="AVOID_REPEAT",
                source_type="MANUAL_REJECTED_EXAMPLE",
                source_ref=source_ref,
                source_content={"failed_output_summary": failed_output_summary, "avoid_rule": avoid_rule},
                summary=failed_output_summary,
                rights_status="SAFE",
                prompt_safety_state="PROMPT_SAFE",
                reuse_scope="CHANNEL" if content_category_id is None else "CATEGORY",
                facets=facets,
            )
        )
        self.approve(memory_item_id=item.id, data=MemoryApprovalRequest(decided_by=approved_by, rationale="Human-approved failed output avoid pattern."))
        return item

    def approve(self, *, memory_item_id: uuid.UUID, data: MemoryApprovalRequest) -> MemoryApprovalDecision:
        item = self.require_item(memory_item_id)
        self._validate_approval_preconditions(item)
        item.approval_status = "APPROVED"
        item.human_approved_at = utc_now()
        item.approved_by = data.decided_by
        item.approval_authority_type = "HUMAN"
        item.approval_policy_version = None
        item.approval_policy_hash = None
        item.approval_evidence_json = {}
        if data.mark_facets_embedding_eligible:
            for facet in self.list_facets(memory_item_id=item.id):
                facet.embedding_eligible = True
        decision = self._record_decision(item=item, decision="APPROVE", data=data)
        self._update_latest_queue(item.id, "APPROVED")
        return decision

    def approve_system_policy(
        self,
        *,
        memory_item_id: uuid.UUID,
        policy_version: str,
        policy_hash: str,
        evidence: dict[str, Any],
    ) -> MemoryApprovalDecision:
        """Approve only a pre-gated low-risk memory without inventing a human."""

        item = self.require_item(memory_item_id)
        if (
            item.source_type != "SYSTEM_POLICY_LEARNING"
            or not policy_version
            or not re.fullmatch(r"[0-9a-f]{64}", policy_hash)
            or not evidence.get("eligibility_run_id")
            or not evidence.get("evidence_bundle_id")
            or not evidence.get("source_uploaded_video_ids")
        ):
            raise ValidationFailureError("SYSTEM_MEMORY_APPROVAL_AUTHORITY_INVALID")
        self._validate_approval_preconditions(item, allow_system_policy=True)
        item.approval_status = "APPROVED"
        item.human_approved_at = None
        item.approved_by = None
        item.approval_authority_type = "SYSTEM_POLICY"
        item.approval_policy_version = policy_version
        item.approval_policy_hash = policy_hash
        item.approval_evidence_json = dict(evidence)
        for facet in self.list_facets(memory_item_id=item.id):
            facet.embedding_eligible = True
        decision = MemoryApprovalDecision(
            memory_item_id=item.id,
            decision="APPROVE",
            decided_by=None,
            approval_authority_type="SYSTEM_POLICY",
            policy_version=policy_version,
            policy_hash=policy_hash,
            evidence_json=dict(evidence),
            rationale="System-policy approval after mature recurrent low-risk evidence.",
            approved_prompt_use_cases_json=[],
            rejected_reason_codes_json=[],
        )
        self.session.add(decision)
        self._update_latest_queue(item.id, "APPROVED")
        self.session.flush()
        return decision

    def reject(self, *, memory_item_id: uuid.UUID, data: MemoryApprovalRequest) -> MemoryApprovalDecision:
        item = self.require_item(memory_item_id)
        item.approval_status = "REJECTED"
        decision = self._record_decision(item=item, decision="REJECT", data=data)
        self._update_latest_queue(item.id, "REJECTED")
        return decision

    def archive(self, *, memory_item_id: uuid.UUID, data: MemoryApprovalRequest) -> MemoryApprovalDecision:
        item = self.require_item(memory_item_id)
        item.approval_status = "ARCHIVED"
        decision = self._record_decision(item=item, decision="ARCHIVE", data=data)
        self._update_latest_queue(item.id, "REJECTED")
        return decision

    def create_usage_manifest(self, *, data: MemoryUsageManifestCreate) -> MemoryUsageManifest:
        _validate_enum(data.usage_status, MEMORY_USAGE_STATUSES, "usage_status")
        manifest = MemoryUsageManifest(**data.model_dump())
        self.session.add(manifest)
        self.session.flush()
        return manifest

    def prompt_eligibility(
        self,
        *,
        memory_item_id: uuid.UUID,
        facet_id: uuid.UUID,
        effective_context_snapshot_id: uuid.UUID,
        allow_company_approved: bool = False,
    ) -> MemoryGateResult:
        item = self.require_item(memory_item_id)
        facet = self.require_facet(facet_id)
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, effective_context_snapshot_id)
        if effective is None:
            raise NotFoundError(f"effective context snapshot not found: {effective_context_snapshot_id}")
        results = [
            MemoryApprovalGate().check(item),
            MemoryRightsGate().check(item),
            MemoryPromptSafetyGate().check(item, facet),
            MemoryFreshnessGate().check(item),
            MemoryScopeGate().check(item=item, effective_context=effective, allow_company_approved=allow_company_approved),
        ]
        reasons = [reason for result in results for reason in result.reason_codes]
        return MemoryGateResult(not reasons, reasons, "R3D5 prompt eligibility evaluated.")

    def require_item(self, memory_item_id: uuid.UUID) -> ChannelMemoryItem:
        item = self.session.get(ChannelMemoryItem, memory_item_id)
        if item is None:
            raise NotFoundError(f"memory item not found: {memory_item_id}")
        return item

    def require_facet(self, facet_id: uuid.UUID) -> MemoryFacet:
        facet = self.session.get(MemoryFacet, facet_id)
        if facet is None:
            raise NotFoundError(f"memory facet not found: {facet_id}")
        return facet

    def list_facets(self, *, memory_item_id: uuid.UUID) -> list[MemoryFacet]:
        return list(
            self.session.scalars(
                select(MemoryFacet).where(MemoryFacet.memory_item_id == memory_item_id).order_by(MemoryFacet.created_at, MemoryFacet.id)
            ).all()
        )

    def list_review_queue(self, *, queue_status: str | None = None) -> list[MemoryReviewQueueItem]:
        statement = select(MemoryReviewQueueItem).order_by(MemoryReviewQueueItem.created_at.desc(), MemoryReviewQueueItem.id.desc())
        if queue_status is not None:
            _validate_enum(queue_status, QUEUE_STATUSES, "queue_status")
            statement = statement.where(MemoryReviewQueueItem.queue_status == queue_status)
        return list(self.session.scalars(statement).all())

    def compute_item_hash(self, *, data: ChannelMemoryDraftCreate, source_content_hash: str) -> str:
        return stable_hash(
            {
                "company_id": data.company_id,
                "channel_workspace_id": data.channel_workspace_id,
                "content_category_id": data.content_category_id,
                "series_id": data.series_id,
                "character_profile_id": data.character_profile_id,
                "character_version_id": data.character_version_id,
                "character_binding_id": data.character_binding_id,
                "memory_type": data.memory_type,
                "source_type": data.source_type,
                "source_ref": data.source_ref,
                "source_content_hash": source_content_hash,
                "summary": _normalize_text(data.summary),
                "reuse_scope": data.reuse_scope,
                "freshness_state": data.freshness_state,
            }
        )

    def _validate_learning_source(self, data: ChannelMemoryDraftCreate) -> None:
        if data.created_from_learning_candidate_id is not None:
            candidate = self.session.get(LearningCandidate, data.created_from_learning_candidate_id)
            if candidate is None:
                raise NotFoundError(f"learning candidate not found: {data.created_from_learning_candidate_id}")
            if candidate.candidate_state in {"EXPIRED", "CANCELLED", "INELIGIBLE_LOW_EVIDENCE", "BLOCKED_POLICY_RISK", "BLOCKED_RIGHTS_RISK"}:
                raise ValidationFailureError("rejected/suppressed/expired learning cannot become memory")
            if (
                data.created_from_approved_playbook_entry_id is None
                and data.source_type != "SYSTEM_POLICY_LEARNING"
                and not self._learning_candidate_has_human_approval(candidate.id)
            ):
                raise ValidationFailureError("LearningCandidate requires human approval or ApprovedPlaybookEntry link")

    def _learning_candidate_has_human_approval(self, candidate_id: uuid.UUID) -> bool:
        approved_entry = self.session.scalars(
            select(ApprovedPlaybookEntry).where(
                ApprovedPlaybookEntry.learning_candidate_id == candidate_id,
                ApprovedPlaybookEntry.state == "APPROVED",
            )
        ).first()
        if approved_entry is not None:
            return True
        decision = self.session.scalars(
            select(LearningReviewDecision).where(
                LearningReviewDecision.learning_candidate_id == candidate_id,
                LearningReviewDecision.action == "APPROVE",
            )
        ).first()
        return decision is not None

    def _validate_approval_preconditions(
        self, item: ChannelMemoryItem, *, allow_system_policy: bool = False
    ) -> None:
        if item.created_from_learning_candidate_id is not None:
            candidate = self.session.get(LearningCandidate, item.created_from_learning_candidate_id)
            if candidate is None:
                raise NotFoundError(f"learning candidate not found: {item.created_from_learning_candidate_id}")
            if candidate.candidate_state in {"EXPIRED", "CANCELLED", "INELIGIBLE_LOW_EVIDENCE", "BLOCKED_POLICY_RISK", "BLOCKED_RIGHTS_RISK"}:
                raise ValidationFailureError("rejected/suppressed/expired learning cannot become approved memory")
            if not (
                allow_system_policy and item.source_type == "SYSTEM_POLICY_LEARNING"
            ) and not self._learning_candidate_has_human_approval(candidate.id):
                raise ValidationFailureError("learning candidate memory cannot be approved without human approval")

    def _record_decision(self, *, item: ChannelMemoryItem, decision: str, data: MemoryApprovalRequest) -> MemoryApprovalDecision:
        record = MemoryApprovalDecision(
            memory_item_id=item.id,
            decision=decision,
            decided_by=data.decided_by,
            approval_authority_type=("HUMAN" if decision == "APPROVE" else None),
            policy_version=None,
            policy_hash=None,
            evidence_json={},
            rationale=data.rationale,
            approved_prompt_use_cases_json=data.approved_prompt_use_cases_json,
            rejected_reason_codes_json=data.rejected_reason_codes_json,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _update_latest_queue(self, memory_item_id: uuid.UUID, status: str) -> None:
        queue = self.session.scalars(
            select(MemoryReviewQueueItem)
            .where(MemoryReviewQueueItem.memory_item_id == memory_item_id)
            .order_by(MemoryReviewQueueItem.created_at.desc())
            .limit(1)
        ).one_or_none()
        if queue is not None:
            queue.queue_status = status
            self.session.flush()

    def _create_source_link(self, item: ChannelMemoryItem) -> MemorySourceLink:
        link = MemorySourceLink(
            memory_item_id=item.id,
            source_type=item.source_type,
            source_ref=item.source_ref,
            source_hash=item.source_content_hash,
        )
        self.session.add(link)
        self.session.flush()
        return link


def _validate_enum(value: str, allowed: set[str], field_name: str) -> None:
    if value not in allowed:
        raise ValidationFailureError(f"unsupported {field_name}: {value}")


def _facet_type_from_playbook_category(category: str) -> str:
    mapping = {
        "PACKAGING": "PACKAGING_PATTERN",
        "HOOK": "HOOK_PATTERN",
        "RETENTION": "RETENTION_LESSON",
        "VISUAL_SOURCE": "VISUAL_PATTERN",
        "COST": "COST_EFFICIENCY_LESSON",
        "POLICY": "SOURCE_QUALITY_LESSON",
        "RECOVERY": "AVOID_REPEAT",
    }
    return mapping.get(category, "APPROVED_PLAYBOOK")


def _facet_type_from_candidate(candidate_type: str) -> str:
    return {
        "PACKAGING_PATTERN": "PACKAGING_PATTERN",
        "HOOK_PATTERN": "HOOK_PATTERN",
        "RETENTION_PATTERN": "RETENTION_LESSON",
        "VISUAL_SOURCE_PATTERN": "VISUAL_PATTERN",
        "COST_EFFICIENCY_PATTERN": "COST_EFFICIENCY_LESSON",
    }.get(candidate_type, "APPROVED_PLAYBOOK")


def _memory_type_from_candidate(candidate_type: str) -> str:
    return {
        "PACKAGING_PATTERN": "PACKAGING_PATTERN",
        "HOOK_PATTERN": "WINNING_HOOK",
        "RETENTION_PATTERN": "RETENTION_LESSON",
        "VISUAL_SOURCE_PATTERN": "VISUAL_PATTERN",
        "COST_EFFICIENCY_PATTERN": "COST_EFFICIENCY_LESSON",
    }.get(candidate_type, "APPROVED_PLAYBOOK")
