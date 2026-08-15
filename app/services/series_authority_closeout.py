"""D15 series arc, completion, and public-ordinal authority.

SeriesRun.capacity remains an operational reservation ceiling. Editorial series
length belongs to SeriesArcVersion. Public ordinals are allocated only after a
verified PUBLIC receipt and continue across runs of the same SeriesPlan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.architecture_closeout import (
    SeriesArcDecisionAuthority,
    SeriesArcVersion,
    SeriesEpisodeAttemptAuthority,
    SeriesEpisodeBlueprint,
    SeriesPublicOrdinalAuthority,
)
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.youtube_delivery import (
    PublicPublicationReceipt,
    YouTubeSeriesEpisodeBinding,
)
from app.services.config_registry import content_hash


@dataclass(frozen=True, slots=True)
class SeriesProgress:
    series_plan_id: uuid.UUID
    arc_version_id: uuid.UUID
    planning_mode: str
    planned_episode_count: int | None
    public_episode_count: int
    remaining_episode_count: int | None
    state: str
    next_public_ordinal: int
    completion_pending: bool


class SeriesAuthorityCloseoutService:
    def __init__(self, session: Session):
        self.session = session

    def create_arc(
        self,
        *,
        series_plan_id: uuid.UUID,
        planning_mode: str,
        planned_episode_count: int | None,
        editorial_coverage: dict[str, Any],
        supersedes_series_arc_version_id: uuid.UUID | None = None,
    ) -> SeriesArcVersion:
        plan = self._plan(series_plan_id, lock=True)
        mode = planning_mode.upper()
        if mode not in {"FIXED_COUNT", "ROLLING"}:
            raise ValidationFailureError("SERIES_ARC_PLANNING_MODE_INVALID")
        if (mode == "FIXED_COUNT" and (planned_episode_count or 0) <= 0) or (
            mode == "ROLLING" and planned_episode_count is not None
        ):
            raise ValidationFailureError("SERIES_ARC_PLANNED_COUNT_INVALID")
        latest_version = int(
            self.session.scalar(
                select(func.coalesce(func.max(SeriesArcVersion.version), 0)).where(
                    SeriesArcVersion.series_plan_id == plan.id
                )
            )
            or 0
        )
        if supersedes_series_arc_version_id is not None:
            previous = self.session.get(SeriesArcVersion, supersedes_series_arc_version_id)
            if previous is None or previous.series_plan_id != plan.id:
                raise ValidationFailureError("SERIES_ARC_SUPERSEDE_SCOPE_MISMATCH")
            if previous.state != "APPROVED":
                raise ConflictError("SERIES_ARC_SUPERSEDE_REQUIRES_APPROVED_SOURCE")
        payload = {
            "schema_version": "vcos.series-arc-version.v1",
            "series_plan_id": str(plan.id),
            "version": latest_version + 1,
            "planning_mode": mode,
            "planned_episode_count": planned_episode_count,
            "editorial_coverage": editorial_coverage,
            "supersedes_series_arc_version_id": str(supersedes_series_arc_version_id)
            if supersedes_series_arc_version_id
            else None,
        }
        row = SeriesArcVersion(
            company_id=plan.company_id,
            channel_workspace_id=plan.channel_workspace_id,
            series_plan_id=plan.id,
            version=latest_version + 1,
            planning_mode=mode,
            planned_episode_count=planned_episode_count,
            editorial_coverage=dict(editorial_coverage),
            state="DRAFT",
            supersedes_series_arc_version_id=supersedes_series_arc_version_id,
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_blueprint(
        self,
        *,
        arc_version_id: uuid.UUID,
        blueprint_key: str,
        editorial_position: int,
        editorial_purpose: str,
        coverage_contract: dict[str, Any],
        title_hint: str | None = None,
        state: str = "PLANNED",
    ) -> SeriesEpisodeBlueprint:
        arc = self._arc(arc_version_id, lock=True)
        if arc.state != "DRAFT":
            raise ConflictError("SERIES_BLUEPRINT_REQUIRES_DRAFT_ARC")
        if editorial_position <= 0 or not blueprint_key.strip() or not editorial_purpose.strip():
            raise ValidationFailureError("SERIES_BLUEPRINT_INVALID")
        payload = {
            "schema_version": "vcos.series-episode-blueprint.v1",
            "series_arc_version_id": str(arc.id),
            "blueprint_key": blueprint_key.strip(),
            "editorial_position": editorial_position,
            "title_hint": title_hint,
            "editorial_purpose": editorial_purpose.strip(),
            "coverage_contract": coverage_contract,
            "state": state,
        }
        row = SeriesEpisodeBlueprint(
            series_arc_version_id=arc.id,
            series_plan_id=arc.series_plan_id,
            blueprint_key=blueprint_key.strip(),
            editorial_position=editorial_position,
            title_hint=title_hint,
            editorial_purpose=editorial_purpose.strip(),
            coverage_contract=dict(coverage_contract),
            state=state,
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def approve_arc(
        self,
        *,
        arc_version_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        evidence_refs: list[dict[str, Any]],
    ) -> SeriesArcVersion:
        arc = self._arc(arc_version_id, lock=True)
        if arc.state == "APPROVED":
            return arc
        if arc.state != "DRAFT" or not evidence_refs:
            raise ValidationFailureError("SERIES_ARC_APPROVAL_EVIDENCE_REQUIRED")
        existing = self.session.scalar(
            select(SeriesArcVersion)
            .where(
                SeriesArcVersion.series_plan_id == arc.series_plan_id,
                SeriesArcVersion.state == "APPROVED",
                SeriesArcVersion.id != arc.id,
            )
            .with_for_update()
        )
        if existing is not None and existing.id != arc.supersedes_series_arc_version_id:
            raise ConflictError("SERIES_ARC_APPROVED_VERSION_ALREADY_EXISTS")
        blueprints = list(
            self.session.scalars(
                select(SeriesEpisodeBlueprint).where(
                    SeriesEpisodeBlueprint.series_arc_version_id == arc.id,
                    SeriesEpisodeBlueprint.state == "PLANNED",
                )
            ).all()
        )
        if arc.planning_mode == "FIXED_COUNT":
            positions = sorted(item.editorial_position for item in blueprints)
            expected = list(range(1, int(arc.planned_episode_count or 0) + 1))
            if positions != expected:
                raise ValidationFailureError("SERIES_ARC_EDITORIAL_COVERAGE_INCOMPLETE")
        if existing is not None:
            existing.state = "SUPERSEDED"
        arc.state = "APPROVED"
        arc.approved_by_user_id = actor_user_id
        arc.approved_at = utc_now()
        arc.approval_evidence_refs = list(evidence_refs)
        self.session.flush()
        return arc

    def register_attempt(
        self,
        *,
        series_run_id: uuid.UUID,
        technical_attempt_number: int,
        reservation_ref: str,
        video_project_id: uuid.UUID | None = None,
        episode_blueprint_id: uuid.UUID | None = None,
        state: str = "RESERVED",
    ) -> SeriesEpisodeAttemptAuthority:
        run = self._run(series_run_id, lock=True)
        arc = self._approved_arc(run.series_plan_id, lock=False)
        if technical_attempt_number <= 0:
            raise ValidationFailureError("SERIES_TECHNICAL_ATTEMPT_NUMBER_INVALID")
        payload = {
            "schema_version": "vcos.series-episode-attempt.v1",
            "series_plan_id": str(run.series_plan_id),
            "series_run_id": str(run.id),
            "series_arc_version_id": str(arc.id),
            "technical_attempt_number": technical_attempt_number,
            "reservation_ref": reservation_ref,
            "video_project_id": str(video_project_id) if video_project_id else None,
            "episode_blueprint_id": str(episode_blueprint_id) if episode_blueprint_id else None,
        }
        digest = content_hash(payload)
        existing = self.session.scalar(
            select(SeriesEpisodeAttemptAuthority).where(
                SeriesEpisodeAttemptAuthority.identity_hash == digest
            )
        )
        if existing is not None:
            return existing
        row = SeriesEpisodeAttemptAuthority(
            series_plan_id=run.series_plan_id,
            series_run_id=run.id,
            series_arc_version_id=arc.id,
            episode_blueprint_id=episode_blueprint_id,
            technical_attempt_number=technical_attempt_number,
            reservation_ref=reservation_ref,
            video_project_id=video_project_id,
            state=state,
            identity_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def allocate_public_ordinal(
        self, *, public_publication_receipt_id: uuid.UUID
    ) -> SeriesPublicOrdinalAuthority | None:
        receipt = self.session.scalar(
            select(PublicPublicationReceipt)
            .where(PublicPublicationReceipt.id == public_publication_receipt_id)
            .with_for_update()
        )
        if receipt is None:
            raise NotFoundError("public publication receipt not found")
        candidate = self.session.get(FinalReviewCandidate, receipt.final_review_candidate_id)
        if candidate is None:
            raise ValidationFailureError("SERIES_PUBLIC_ORDINAL_CANDIDATE_MISSING")
        if candidate.content_mode != "SERIES_EPISODE":
            return None
        if candidate.series_plan_id is None or candidate.series_run_id is None or candidate.episode_number is None:
            raise ValidationFailureError("SERIES_PUBLIC_ORDINAL_LINEAGE_INCOMPLETE")
        self._plan(candidate.series_plan_id, lock=True)
        arc = self._approved_arc(candidate.series_plan_id, lock=False)
        existing = self.session.scalar(
            select(SeriesPublicOrdinalAuthority).where(
                SeriesPublicOrdinalAuthority.public_publication_receipt_id == receipt.id
            )
        )
        if existing is not None:
            return existing
        attempt = self.session.scalar(
            select(SeriesEpisodeAttemptAuthority).where(
                SeriesEpisodeAttemptAuthority.video_project_id == candidate.video_project_id
            )
        )
        if attempt is None:
            attempt = self.register_attempt(
                series_run_id=candidate.series_run_id,
                technical_attempt_number=candidate.episode_number,
                reservation_ref=f"publication-derived-attempt://{candidate.video_project_id}",
                video_project_id=candidate.video_project_id,
                state="PUBLISHED",
            )
        else:
            attempt.state = "PUBLISHED"
        next_ordinal = int(
            self.session.scalar(
                select(func.coalesce(func.max(SeriesPublicOrdinalAuthority.public_episode_ordinal), 0)).where(
                    SeriesPublicOrdinalAuthority.series_plan_id == candidate.series_plan_id
                )
            )
            or 0
        ) + 1
        payload = {
            "schema_version": "vcos.series-public-ordinal.v1",
            "series_plan_id": str(candidate.series_plan_id),
            "series_run_id": str(candidate.series_run_id),
            "series_arc_version_id": str(arc.id),
            "episode_attempt_authority_id": str(attempt.id),
            "video_project_id": str(candidate.video_project_id),
            "public_publication_receipt_id": str(receipt.id),
            "public_episode_ordinal": next_ordinal,
        }
        row = SeriesPublicOrdinalAuthority(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            series_plan_id=candidate.series_plan_id,
            series_run_id=candidate.series_run_id,
            series_arc_version_id=arc.id,
            episode_attempt_authority_id=attempt.id,
            video_project_id=candidate.video_project_id,
            public_publication_receipt_id=receipt.id,
            public_episode_ordinal=next_ordinal,
            authority_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        self._project_to_youtube_binding(candidate=candidate, receipt=receipt, authority=row)
        self._auto_completion_pending(run_id=candidate.series_run_id, arc=arc)
        self.session.flush()
        return row

    def authorize_early_completion(
        self,
        *,
        series_plan_id: uuid.UUID,
        effective_public_episode_count: int,
        actor_user_id: uuid.UUID,
        reason: str,
        evidence_refs: list[dict[str, Any]],
    ) -> SeriesArcDecisionAuthority:
        self._plan(series_plan_id, lock=True)
        arc = self._approved_arc(series_plan_id, lock=False)
        published = self._public_count(series_plan_id)
        if arc.planning_mode != "FIXED_COUNT" or not (published <= effective_public_episode_count < int(arc.planned_episode_count or 0)):
            raise ValidationFailureError("SERIES_EARLY_COMPLETION_TARGET_INVALID")
        return self._decision(
            arc=arc,
            action="EARLY_COMPLETION",
            actor_user_id=actor_user_id,
            reason=reason,
            evidence_refs=evidence_refs,
            effective_public_episode_count=effective_public_episode_count,
        )

    def extend_fixed_arc(
        self,
        *,
        series_plan_id: uuid.UUID,
        new_planned_episode_count: int,
        actor_user_id: uuid.UUID,
        reason: str,
        evidence_refs: list[dict[str, Any]],
    ) -> SeriesArcVersion:
        self._plan(series_plan_id, lock=True)
        source = self._approved_arc(series_plan_id, lock=False)
        if source.planning_mode != "FIXED_COUNT" or new_planned_episode_count <= int(source.planned_episode_count or 0):
            raise ValidationFailureError("SERIES_EXTENSION_COUNT_INVALID")
        target = self.create_arc(
            series_plan_id=series_plan_id,
            planning_mode="FIXED_COUNT",
            planned_episode_count=new_planned_episode_count,
            editorial_coverage=dict(source.editorial_coverage),
            supersedes_series_arc_version_id=source.id,
        )
        self._decision(
            arc=source,
            target_arc=target,
            action="EXTENSION",
            actor_user_id=actor_user_id,
            reason=reason,
            evidence_refs=evidence_refs,
            effective_public_episode_count=new_planned_episode_count,
        )
        return target

    def progress(self, series_plan_id: uuid.UUID) -> SeriesProgress:
        self._plan(series_plan_id, lock=False)
        arc = self._approved_arc(series_plan_id, lock=False)
        public_count = self._public_count(series_plan_id)
        planned = arc.planned_episode_count
        remaining = max(0, int(planned) - public_count) if planned is not None else None
        completion_pending = bool(
            self.session.scalar(
                select(func.count(SeriesRun.id)).where(
                    SeriesRun.series_plan_id == series_plan_id,
                    SeriesRun.state == "COMPLETION_PENDING",
                )
            )
        )
        return SeriesProgress(
            series_plan_id=series_plan_id,
            arc_version_id=arc.id,
            planning_mode=arc.planning_mode,
            planned_episode_count=planned,
            public_episode_count=public_count,
            remaining_episode_count=remaining,
            state=arc.state,
            next_public_ordinal=public_count + 1,
            completion_pending=completion_pending,
        )

    def _auto_completion_pending(self, *, run_id: uuid.UUID, arc: SeriesArcVersion) -> None:
        if arc.planning_mode != "FIXED_COUNT":
            return
        target = int(arc.planned_episode_count or 0)
        early = self.session.scalar(
            select(SeriesArcDecisionAuthority)
            .where(
                SeriesArcDecisionAuthority.series_plan_id == arc.series_plan_id,
                SeriesArcDecisionAuthority.action == "EARLY_COMPLETION",
            )
            .order_by(SeriesArcDecisionAuthority.created_at.desc())
            .limit(1)
        )
        if early is not None and early.effective_public_episode_count is not None:
            target = min(target, early.effective_public_episode_count)
        if self._public_count(arc.series_plan_id) < target:
            return
        run = self._run(run_id, lock=True)
        if run.state == "ACTIVE":
            run.state = "COMPLETION_PENDING"
            run.completion_pending_at = utc_now()
            run.state_reason_codes = ["EDITORIAL_ARC_PUBLIC_TARGET_REACHED"]

    def _project_to_youtube_binding(
        self,
        *,
        candidate: FinalReviewCandidate,
        receipt: PublicPublicationReceipt,
        authority: SeriesPublicOrdinalAuthority,
    ) -> None:
        binding = self.session.scalar(
            select(YouTubeSeriesEpisodeBinding).where(
                YouTubeSeriesEpisodeBinding.video_project_id == candidate.video_project_id
            )
        )
        if binding is None:
            return
        if binding.series_plan_id != authority.series_plan_id or binding.series_run_id != authority.series_run_id:
            raise ValidationFailureError("SERIES_PLAYLIST_BINDING_SCOPE_MISMATCH")
        binding.public_publication_receipt_id = receipt.id
        binding.public_episode_ordinal = authority.public_episode_ordinal
        binding.public_ordinal_authority_ref = f"series-public-ordinal://{authority.id}"
        binding.public_ordinal_authority_hash = authority.authority_hash
        binding.expected_position = authority.public_episode_ordinal - 1
        binding.state = "PUBLICATION_VERIFIED"

    def _decision(
        self,
        *,
        arc: SeriesArcVersion,
        action: str,
        actor_user_id: uuid.UUID,
        reason: str,
        evidence_refs: list[dict[str, Any]],
        effective_public_episode_count: int | None,
        target_arc: SeriesArcVersion | None = None,
    ) -> SeriesArcDecisionAuthority:
        if not evidence_refs or not reason.strip():
            raise ValidationFailureError("SERIES_ARC_DECISION_EVIDENCE_REQUIRED")
        payload = {
            "schema_version": "vcos.series-arc-decision.v1",
            "series_plan_id": str(arc.series_plan_id),
            "source_arc_version_id": str(arc.id),
            "target_arc_version_id": str(target_arc.id) if target_arc else None,
            "action": action,
            "effective_public_episode_count": effective_public_episode_count,
            "reason": reason.strip(),
            "evidence_refs": evidence_refs,
            "decided_by_user_id": str(actor_user_id),
        }
        row = SeriesArcDecisionAuthority(
            series_plan_id=arc.series_plan_id,
            source_arc_version_id=arc.id,
            target_arc_version_id=target_arc.id if target_arc else None,
            action=action,
            effective_public_episode_count=effective_public_episode_count,
            reason=reason.strip(),
            evidence_refs=list(evidence_refs),
            decided_by_user_id=actor_user_id,
            decision_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _public_count(self, series_plan_id: uuid.UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count(SeriesPublicOrdinalAuthority.id)).where(
                    SeriesPublicOrdinalAuthority.series_plan_id == series_plan_id
                )
            )
            or 0
        )

    def _approved_arc(self, series_plan_id: uuid.UUID, *, lock: bool) -> SeriesArcVersion:
        stmt = select(SeriesArcVersion).where(
            SeriesArcVersion.series_plan_id == series_plan_id,
            SeriesArcVersion.state == "APPROVED",
        )
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt)
        if row is None:
            raise ValidationFailureError("SERIES_APPROVED_ARC_REQUIRED")
        return row

    def _arc(self, arc_id: uuid.UUID, *, lock: bool) -> SeriesArcVersion:
        stmt = select(SeriesArcVersion).where(SeriesArcVersion.id == arc_id)
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFoundError("series arc version not found")
        return row

    def _plan(self, plan_id: uuid.UUID, *, lock: bool) -> SeriesPlan:
        stmt = select(SeriesPlan).where(SeriesPlan.id == plan_id)
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFoundError("series plan not found")
        return row

    def _run(self, run_id: uuid.UUID, *, lock: bool) -> SeriesRun:
        stmt = select(SeriesRun).where(SeriesRun.id == run_id)
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFoundError("series run not found")
        return row
