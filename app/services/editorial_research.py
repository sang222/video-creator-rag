"""Long-form editorial research and controlled-evidence candidate runway."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.m5 import (
    EditorialIdeaCandidateCreate,
    EditorialIdeaCandidateTransition,
    EditorialResearchRunCreate,
)
from app.core.actor import ActorContext
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    EditorialResearchRun,
    IdeaMarketPreflight,
)
from app.db.models.m7 import UploadedVideo
from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion
from app.services.company_access import require_company_permission


_CANDIDATE_TRANSITIONS = {
    "RESEARCHED": {"PREFLIGHT_PASS", "PREFLIGHT_BLOCK", "REJECTED", "EXPIRED"},
    "PREFLIGHT_PASS": {"GREENLIT", "REJECTED", "EXPIRED"},
    "PREFLIGHT_BLOCK": {"REJECTED", "EXPIRED"},
    "GREENLIT": {"SELECTED_FOR_SLOT", "REJECTED", "EXPIRED"},
    "SELECTED_FOR_SLOT": {"IN_PRODUCTION", "GREENLIT", "REJECTED", "EXPIRED"},
    "IN_PRODUCTION": {"FINAL_REVIEW_READY", "REJECTED"},
    "FINAL_REVIEW_READY": {"PUBLISHED", "REJECTED"},
    "PUBLISHED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class EditorialResearchService:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        data: EditorialResearchRunCreate,
        actor: ActorContext,
    ) -> EditorialResearchRun:
        require_company_permission(
            self.session,
            actor=actor,
            permission="editorial.manage",
            company_id=data.company_id,
        )
        workspace = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        profile = self.session.get(
            ChannelProfileVersion, data.channel_profile_version_id
        )
        policy = self.session.get(
            CompiledChannelPolicySnapshot, data.policy_snapshot_id
        )
        if (
            workspace is None
            or workspace.company_id != data.company_id
            or profile is None
            or profile.channel_workspace_id != workspace.id
            or policy is None
            or policy.channel_workspace_id != workspace.id
            or policy.channel_profile_version_id != profile.id
            or profile.status not in {"approved", "active"}
            or policy.status not in {"approved", "active"}
        ):
            raise ValidationFailureError("EDITORIAL_RESEARCH_AUTHORITY_MISMATCH")
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        record = EditorialResearchRun(
            **payload,
            metadata_=metadata,
            candidate_count=0,
            created_by_user_id=actor.actor_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def start_run(
        self, *, run_id: uuid.UUID, actor: ActorContext
    ) -> EditorialResearchRun:
        run = self._locked_run(run_id)
        self._authorize(run, actor)
        if run.status == "RUNNING":
            return run
        if run.status != "PENDING":
            raise ConflictError(f"research run cannot start from {run.status}")
        run.status = "RUNNING"
        run.started_at = utc_now()
        run.reason_codes = ["EDITORIAL_RESEARCH_STARTED"]
        self.session.flush()
        return run

    def add_candidate(
        self,
        *,
        data: EditorialIdeaCandidateCreate,
        actor: ActorContext,
    ) -> EditorialIdeaCandidate:
        run = self._locked_run(data.editorial_research_run_id)
        self._authorize(run, actor)
        if run.status not in {"PENDING", "RUNNING"}:
            raise ConflictError(
                f"research run does not accept candidates: {run.status}"
            )
        if data.stage != "RESEARCHED":
            raise ValidationFailureError("EDITORIAL_CANDIDATE_MUST_START_RESEARCHED")
        launch_policy = self.session.scalar(
            select(FirstChannelLaunchPolicyVersion).where(
                FirstChannelLaunchPolicyVersion.channel_workspace_id
                == run.channel_workspace_id,
                FirstChannelLaunchPolicyVersion.policy_snapshot_id
                == run.policy_snapshot_id,
                FirstChannelLaunchPolicyVersion.state == "APPROVED",
            )
        )
        if data.suggested_series_plan_id is not None:
            from app.db.models.vcos_v2 import SeriesPlan

            plan = self.session.get(SeriesPlan, data.suggested_series_plan_id)
            if (
                plan is None
                or plan.company_id != run.company_id
                or plan.channel_workspace_id != run.channel_workspace_id
                or plan.policy_snapshot_id != run.policy_snapshot_id
                or plan.state != "APPROVED"
                or plan.allowed_production_lanes != ["LONG_FORM"]
            ):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_SERIES_AUTHORITY_MISMATCH"
                )
            if launch_policy is not None and str(
                data.suggested_series_plan_id
            ) not in set(launch_policy.approved_initial_series_plan_ids or []):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_SERIES_OUTSIDE_LAUNCH_POLICY"
                )
        published_count = int(
            self.session.scalar(
                select(func.count(UploadedVideo.id)).where(
                    UploadedVideo.channel_workspace_id == run.channel_workspace_id,
                    UploadedVideo.verification_status == "VERIFIED",
                )
            )
            or 0
        )
        experiment_video_count = (
            launch_policy.first_n_public_videos if launch_policy is not None else 10
        )
        audience_phase_end = max(
            1,
            (experiment_video_count * 3 + 9) // 10,
        )
        series_phase_end = max(
            audience_phase_end,
            (experiment_video_count * 7 + 9) // 10,
        )
        expected_phase = (
            "AUDIENCE_PROMISE"
            if published_count < audience_phase_end
            else "SERIES_PACKAGING"
            if published_count < series_phase_end
            else "ALLOCATION_PREPARATION"
            if published_count < experiment_video_count
            else "STEADY_STATE"
        )
        if (
            data.experiment_phase is not None
            and data.experiment_phase != expected_phase
        ):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_EXPERIMENT_PHASE_MISMATCH"
            )
        if data.primary_variable_under_test and (
            not data.baseline_refs or not data.comparison_group
        ):
            raise ValidationFailureError(
                "EXPERIMENT_VARIABLE_REQUIRES_BASELINE_AND_COMPARISON"
            )
        normalized_candidate = data.model_copy(
            update={
                "budget_readiness": "UNKNOWN",
                "rights_policy_state": "UNKNOWN",
                "quality_state": "UNKNOWN",
            }
        )
        semantic = {
            **normalized_candidate.model_dump(mode="json"),
            "company_id": str(run.company_id),
            "channel_workspace_id": str(run.channel_workspace_id),
            "policy_snapshot_id": str(run.policy_snapshot_id),
        }
        digest = _canonical_hash(semantic)
        existing = self.session.scalar(
            select(EditorialIdeaCandidate).where(
                EditorialIdeaCandidate.canonical_hash == digest
            )
        )
        if existing is not None:
            return existing
        payload = normalized_candidate.model_dump(exclude={"experiment_phase"})
        record = EditorialIdeaCandidate(
            **payload,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            policy_snapshot_id=run.policy_snapshot_id,
            experiment_phase=data.experiment_phase or expected_phase,
            canonical_hash=digest,
            created_by_user_id=actor.actor_id,
        )
        self.session.add(record)
        run.candidate_count += 1
        self.session.flush()
        return record

    def complete_run(
        self, *, run_id: uuid.UUID, actor: ActorContext
    ) -> EditorialResearchRun:
        run = self._locked_run(run_id)
        self._authorize(run, actor)
        if run.status == "COMPLETED":
            return run
        if run.status not in {"PENDING", "RUNNING"}:
            raise ConflictError(f"research run cannot complete from {run.status}")
        run.status = "COMPLETED"
        run.started_at = run.started_at or utc_now()
        run.completed_at = utc_now()
        run.reason_codes = ["EDITORIAL_RESEARCH_COMPLETED"]
        self.session.flush()
        return run

    def transition_candidate(
        self,
        *,
        candidate_id: uuid.UUID,
        data: EditorialIdeaCandidateTransition,
        actor: ActorContext,
    ) -> EditorialIdeaCandidate:
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            raise NotFoundError(f"editorial candidate not found: {candidate_id}")
        run = self.session.get(
            EditorialResearchRun, candidate.editorial_research_run_id
        )
        if run is None:
            raise ValidationFailureError("EDITORIAL_RESEARCH_RUN_MISSING")
        self._authorize(run, actor)
        target = data.target_stage
        if target == candidate.stage:
            return candidate
        if target not in _CANDIDATE_TRANSITIONS[candidate.stage]:
            raise ConflictError(
                f"invalid candidate transition: {candidate.stage}->{target}"
            )
        preflight = (
            self.session.get(IdeaMarketPreflight, data.idea_market_preflight_id)
            if data.idea_market_preflight_id
            else None
        )
        if target in {"PREFLIGHT_PASS", "PREFLIGHT_BLOCK", "GREENLIT"}:
            if (
                preflight is None
                or preflight.editorial_idea_candidate_id != candidate.id
                or preflight.company_id != candidate.company_id
                or preflight.channel_workspace_id != candidate.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_PREFLIGHT_AUTHORITY_MISMATCH"
                )
        if target == "PREFLIGHT_PASS" and (
            preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
            or not preflight.niche_contract_digest_hash
            or not preflight.target_market_digest_hash
            or (preflight.evidence_blob or {}).get("canonical_authority_verified")
            is not True
        ):
            raise ValidationFailureError("STRICT_EDITORIAL_PREFLIGHT_NOT_PASS")
        if target == "PREFLIGHT_BLOCK" and preflight.decision != "BLOCK":
            raise ValidationFailureError("EDITORIAL_PREFLIGHT_NOT_BLOCKED")
        canonical_evidence_refs = (
            (preflight.evidence_blob or {}).get("evidence_refs")
            if preflight is not None
            else None
        )
        if target == "PREFLIGHT_PASS" and (
            not isinstance(canonical_evidence_refs, list)
            or not canonical_evidence_refs
            or any(
                not isinstance(item, dict)
                or item.get("type") != "search_demand_evidence"
                or not item.get("id")
                for item in canonical_evidence_refs
            )
        ):
            raise ValidationFailureError("EDITORIAL_CANONICAL_EVIDENCE_REQUIRED")
        if target == "GREENLIT":
            if candidate.stage != "PREFLIGHT_PASS":
                raise ValidationFailureError("GREENLIGHT_REQUIRES_PREFLIGHT_PASS")
            if (
                candidate.rights_policy_state != "PASS"
                or candidate.quality_state != "PASS"
                or not candidate.evidence_refs
            ):
                raise ValidationFailureError(
                    "GREENLIGHT_DETERMINISTIC_ELIGIBILITY_NOT_MET"
                )
        if target == "PREFLIGHT_PASS":
            candidate.evidence_refs = list(canonical_evidence_refs)
            candidate.rights_policy_state = "PASS"
            candidate.quality_state = "PASS"
            candidate.budget_readiness = "UNKNOWN"
        elif target == "PREFLIGHT_BLOCK":
            candidate.quality_state = "BLOCK"
            candidate.budget_readiness = "UNKNOWN"
        candidate.stage = target
        candidate.reason_codes = data.reason_codes
        self.session.flush()
        return candidate

    def _locked_run(self, run_id: uuid.UUID) -> EditorialResearchRun:
        record = self.session.scalar(
            select(EditorialResearchRun)
            .where(EditorialResearchRun.id == run_id)
            .with_for_update()
        )
        if record is None:
            raise NotFoundError(f"editorial research run not found: {run_id}")
        return record

    def _authorize(self, run: EditorialResearchRun, actor: ActorContext) -> None:
        require_company_permission(
            self.session,
            actor=actor,
            permission="editorial.manage",
            company_id=run.company_id,
        )
