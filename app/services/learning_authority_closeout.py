"""P1 learning authority hardening without adding a new learning agent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.contracts.m11 import LearningReviewDecisionCreate
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.architecture_closeout import (
    LearningReviewCommand,
    LearningSystemPromotionReceipt,
    PlatformEnforcementIncident,
)
from app.db.models.m10 import (
    LearningCandidate,
    LearningCandidateGenerationRun,
    LearningPromotionEligibilityRun,
)
from app.db.models.m11 import LearningReviewDecision
from app.services.config_registry import content_hash
from app.services.m11 import M11LearningReviewService


@dataclass(frozen=True, slots=True)
class PromotionPreflight:
    result: str
    equivalence_fingerprint: str
    distinct_mature_source_count: int
    reason_codes: tuple[str, ...]


class LearningAuthorityCloseoutService:
    """Deterministic wrapper for recurrent system learning and M11 commands."""

    MIN_RECURRENT_MATURE_SOURCES = 3

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def equivalence_fingerprint(candidate: LearningCandidate) -> str:
        normalized = " ".join((candidate.suggested_learning or "").lower().split())
        return content_hash(
            {
                "schema_version": "vcos.learning-equivalence.v1",
                "candidate_type": candidate.candidate_type,
                "recommended_scope": candidate.recommended_scope,
                "normalized_learning": normalized,
            }
        )

    def sync_equivalence_fingerprint(self, candidate_id: uuid.UUID) -> str:
        candidate = self._candidate(candidate_id, lock=True)
        fingerprint = self.equivalence_fingerprint(candidate)
        self.session.execute(
            text(
                "UPDATE learning_candidates SET equivalence_fingerprint=:fingerprint "
                "WHERE id=:candidate_id"
            ),
            {"fingerprint": fingerprint, "candidate_id": candidate.id},
        )
        self.session.flush()
        return fingerprint

    def system_promotion_preflight(
        self,
        *,
        candidate_id: uuid.UUID,
        policy_version: str,
        policy_hash: str,
    ) -> PromotionPreflight:
        candidate = self._candidate(candidate_id, lock=True)
        fingerprint = self.sync_equivalence_fingerprint(candidate.id)
        reasons: list[str] = []
        if not policy_version or len(policy_hash) != 64:
            reasons.append("SYSTEM_POLICY_PROVENANCE_INVALID")
        eligibility = (
            self.session.get(LearningPromotionEligibilityRun, candidate.eligibility_run_id)
            if candidate.eligibility_run_id
            else None
        )
        if eligibility is None or eligibility.result != "ELIGIBLE_FOR_REVIEW":
            reasons.append("SYSTEM_PROMOTION_ELIGIBILITY_RECHECK_FAILED")
        if candidate.risk_level != "LOW" or candidate.policy_flags or candidate.rights_flags:
            reasons.append("SYSTEM_PROMOTION_POLICY_RIGHTS_RISK_BLOCKED")
        if self._enforcement_freeze(candidate):
            reasons.append("SYSTEM_PROMOTION_ENFORCEMENT_FREEZE_ACTIVE")
        recurrent = self._recurrent_mature_source_count(
            channel_workspace_id=candidate.channel_workspace_id,
            fingerprint=fingerprint,
        )
        if recurrent < self.MIN_RECURRENT_MATURE_SOURCES:
            reasons.append("SYSTEM_PROMOTION_EQUIVALENCE_RECURRENCE_INSUFFICIENT")
        result = "PROMOTED" if not reasons else "BLOCKED"
        return PromotionPreflight(
            result=result,
            equivalence_fingerprint=fingerprint,
            distinct_mature_source_count=recurrent,
            reason_codes=tuple(reasons),
        )

    def record_system_promotion_preflight(
        self,
        *,
        candidate_id: uuid.UUID,
        policy_version: str,
        policy_hash: str,
    ) -> LearningSystemPromotionReceipt:
        candidate = self._candidate(candidate_id, lock=True)
        existing = self.session.scalar(
            select(LearningSystemPromotionReceipt).where(
                LearningSystemPromotionReceipt.learning_candidate_id == candidate.id
            )
        )
        if existing is not None:
            return existing
        preflight = self.system_promotion_preflight(
            candidate_id=candidate.id,
            policy_version=policy_version,
            policy_hash=policy_hash,
        )
        if candidate.eligibility_run_id is None:
            raise ValidationFailureError("SYSTEM_PROMOTION_ELIGIBILITY_RUN_REQUIRED")
        payload = {
            "schema_version": "vcos.learning-system-promotion-receipt.v1",
            "learning_candidate_id": str(candidate.id),
            "eligibility_run_id": str(candidate.eligibility_run_id),
            "channel_workspace_id": str(candidate.channel_workspace_id),
            "equivalence_fingerprint": preflight.equivalence_fingerprint,
            "distinct_mature_source_count": preflight.distinct_mature_source_count,
            "result": preflight.result,
            "reason_codes": list(preflight.reason_codes),
            "policy_version": policy_version,
            "policy_hash": policy_hash,
        }
        row = LearningSystemPromotionReceipt(
            learning_candidate_id=candidate.id,
            eligibility_run_id=candidate.eligibility_run_id,
            channel_workspace_id=candidate.channel_workspace_id,
            equivalence_fingerprint=preflight.equivalence_fingerprint,
            distinct_mature_source_count=preflight.distinct_mature_source_count,
            result=preflight.result,
            reason_codes=list(preflight.reason_codes),
            policy_version=policy_version,
            policy_hash=policy_hash,
            receipt_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def decide_m11_exactly_once(
        self,
        *,
        candidate_id: uuid.UUID,
        command_id: uuid.UUID,
        action: str,
        actor_role: str = "LEARNING_REVIEWER",
        decided_by_user_id: uuid.UUID | None = None,
        rationale: str | None = None,
    ) -> LearningReviewDecision:
        candidate = self._candidate(candidate_id, lock=True)
        payload = {
            "schema_version": "vcos.learning-review-command.v1",
            "candidate_id": str(candidate.id),
            "command_id": str(command_id),
            "action": action,
            "actor_role": actor_role,
            "decided_by_user_id": str(decided_by_user_id) if decided_by_user_id else None,
            "rationale": rationale,
        }
        digest = content_hash(payload)
        by_command = self.session.scalar(
            select(LearningReviewCommand).where(LearningReviewCommand.command_id == command_id)
        )
        if by_command is not None:
            if by_command.learning_candidate_id != candidate.id or by_command.decision_hash != digest:
                raise ConflictError("LEARNING_REVIEW_COMMAND_REUSE_CONFLICT")
            if by_command.learning_review_decision_id is None:
                raise ConflictError("LEARNING_REVIEW_COMMAND_INCOMPLETE")
            decision = self.session.get(LearningReviewDecision, by_command.learning_review_decision_id)
            if decision is None:
                raise ConflictError("LEARNING_REVIEW_COMMAND_DECISION_MISSING")
            return decision
        existing = self.session.scalar(
            select(LearningReviewCommand).where(
                LearningReviewCommand.learning_candidate_id == candidate.id
            )
        )
        if existing is not None:
            raise ConflictError("LEARNING_REVIEW_CANDIDATE_ALREADY_TERMINAL")
        command = LearningReviewCommand(
            learning_candidate_id=candidate.id,
            command_id=command_id,
            action=action,
            actor_role=actor_role,
            decided_by_user_id=decided_by_user_id,
            decision_hash=digest,
            state="INTENDED",
        )
        self.session.add(command)
        self.session.flush()
        decision = M11LearningReviewService(self.session).decide(
            candidate_id=candidate.id,
            data=LearningReviewDecisionCreate(
                action=action,
                actor_role=actor_role,
                decided_by_user_id=decided_by_user_id,
                rationale=rationale,
            ),
            correlation_id=f"learning-review-command:{command_id}",
        )
        command.learning_review_decision_id = decision.id
        command.state = "COMPLETED"
        command.completed_at = utc_now()
        self.session.flush()
        return decision

    def canonical_learning_state(self, candidate_id: uuid.UUID) -> str:
        candidate = self._candidate(candidate_id, lock=False)
        decision = self.session.scalar(
            select(LearningReviewDecision)
            .where(LearningReviewDecision.learning_candidate_id == candidate.id)
            .order_by(LearningReviewDecision.created_at.desc())
            .limit(1)
        )
        if decision is None:
            return candidate.candidate_state
        return {
            "APPROVE": "APPROVED",
            "REJECT": "REJECTED",
            "REQUEST_MORE_EVIDENCE": "NEEDS_MORE_EVIDENCE",
            "SUPPRESS": "SUPPRESSED",
            "EXPIRE": "EXPIRED",
        }.get(decision.action, candidate.candidate_state)

    def _recurrent_mature_source_count(
        self, *, channel_workspace_id: uuid.UUID | None, fingerprint: str
    ) -> int:
        if channel_workspace_id is None:
            return 0
        value = self.session.execute(
            text(
                """
                SELECT count(DISTINCT c.uploaded_video_id)
                FROM learning_candidates c
                JOIN learning_candidate_generation_runs g ON g.id = c.generation_run_id
                JOIN learning_promotion_eligibility_runs e ON e.id = c.eligibility_run_id
                WHERE c.channel_workspace_id = :channel_id
                  AND c.equivalence_fingerprint = :fingerprint
                  AND c.risk_level = 'LOW'
                  AND jsonb_array_length(coalesce(c.policy_flags,'[]'::jsonb)) = 0
                  AND jsonb_array_length(coalesce(c.rights_flags,'[]'::jsonb)) = 0
                  AND g.metadata->>'maturity' = 'MATURE'
                  AND e.result = 'ELIGIBLE_FOR_REVIEW'
                """
            ),
            {"channel_id": channel_workspace_id, "fingerprint": fingerprint},
        ).scalar_one()
        return int(value or 0)

    def _enforcement_freeze(self, candidate: LearningCandidate) -> bool:
        if candidate.channel_workspace_id is None:
            return True
        return bool(
            self.session.scalar(
                select(func.count(PlatformEnforcementIncident.id)).where(
                    PlatformEnforcementIncident.channel_workspace_id == candidate.channel_workspace_id,
                    PlatformEnforcementIncident.freeze_learning.is_(True),
                    PlatformEnforcementIncident.state.in_(["OPEN", "UNDER_REVIEW", "APPEAL_READY", "SUBMITTED"]),
                    (
                        PlatformEnforcementIncident.uploaded_video_id.is_(None)
                        if candidate.uploaded_video_id is None
                        else (
                            PlatformEnforcementIncident.uploaded_video_id.is_(None)
                            | (PlatformEnforcementIncident.uploaded_video_id == candidate.uploaded_video_id)
                        )
                    ),
                )
            )
        )

    def _candidate(self, candidate_id: uuid.UUID, *, lock: bool) -> LearningCandidate:
        stmt = select(LearningCandidate).where(LearningCandidate.id == candidate_id)
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFoundError("learning candidate not found")
        return row
