"""Durable pre-admission reservation authority for series episodes.

``SeriesRun`` remains the canonical capacity and sequencing authority.  This
service locks that existing row before allocating an exact episode, then keeps
an immutable reservation record tied to the qualifying script.  No in-memory
state participates in allocation or finalization.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.m5 import EditorialIdeaCandidate
from app.db.models.script_qualification import (
    ScriptQualificationRun,
    SeriesEpisodeReservation,
)
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun


RESERVATION_AUTHORITY_VERSION = "series-episode-reservation.v1"

RESERVATION_AUTHORITY_MISSING = "SCRIPT_EPISODE_RESERVATION_AUTHORITY_MISSING"
RESERVATION_CONFLICT = "SCRIPT_EPISODE_RESERVATION_CONFLICT"
RESERVATION_DRIFT = "SCRIPT_EPISODE_RESERVATION_DRIFT"
RESERVATION_CONSUMED_BY_OTHER = (
    "SCRIPT_EPISODE_RESERVATION_ALREADY_CONSUMED_BY_ANOTHER_ADMISSION"
)
RESERVATION_ASSIGNMENT_MISMATCH = (
    "SCRIPT_EPISODE_RESERVATION_ASSIGNMENT_RESOLUTION_MISMATCH"
)
RESERVATION_RELEASE_MISMATCH = "SCRIPT_EPISODE_RESERVATION_RELEASE_MISMATCH"
RESERVATION_STALE_VERSION = "SCRIPT_EPISODE_RESERVATION_STALE_VERSION"


class EpisodeReservationAuthorityService:
    """Allocate, release, and consume exact SeriesRun episode identities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def reserve_for_qualification(
        self, run: ScriptQualificationRun
    ) -> SeriesEpisodeReservation | None:
        assignment = self._assignment(run)
        if assignment["content_mode"] == "STANDALONE":
            existing = self._reservation(run.id, lock=True)
            if existing is not None or run.episode_reservation_active:
                raise ValidationFailureError(RESERVATION_DRIFT)
            return None

        existing = self._reservation(run.id, lock=True)
        if existing is not None:
            self._require_match(existing, run, assignment)
            # Retries/restarts are idempotent.  A released or consumed record
            # remains the same durable authority and may never be reactivated.
            return existing

        plan_id = assignment["series_plan_id"]
        run_id = assignment["series_run_id"]
        episode_number = assignment["episode_number"]
        assert isinstance(plan_id, uuid.UUID)
        assert isinstance(run_id, uuid.UUID)
        assert isinstance(episode_number, int)
        series_run = self.session.scalar(
            select(SeriesRun)
            .where(SeriesRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        series_plan = self.session.scalar(
            select(SeriesPlan)
            .where(SeriesPlan.id == plan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        candidate = self.session.get(EditorialIdeaCandidate, run.editorial_idea_candidate_id)
        self._require_reservable(
            qualification=run,
            candidate=candidate,
            series_plan=series_plan,
            series_run=series_run,
            assignment=assignment,
        )
        assert series_run is not None
        reservation = SeriesEpisodeReservation(
            script_qualification_run_id=run.id,
            series_plan_id=plan_id,
            series_run_id=run_id,
            episode_number=episode_number,
            episode_role=assignment["episode_role"],
            episode_delta=assignment["episode_delta"],
            assignment_resolution_hash=assignment["resolution_hash"],
            reservation_authority_version=RESERVATION_AUTHORITY_VERSION,
            state="RESERVED",
        )
        try:
            with self.session.begin_nested():
                self.session.add(reservation)
                series_run.next_episode_number += 1
                series_run.reserved_episode_count += 1
                run.episode_reservation_active = True
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(RESERVATION_CONFLICT) from exc
        return reservation

    def require_current(
        self, run: ScriptQualificationRun
    ) -> SeriesEpisodeReservation | None:
        """Reject historical/missing reservation authority before production use."""

        assignment = self._assignment(run)
        reservation = self._reservation(run.id, lock=True)
        if assignment["content_mode"] == "STANDALONE":
            if reservation is not None or run.episode_reservation_active:
                raise ValidationFailureError(RESERVATION_DRIFT)
            return None
        if reservation is None:
            raise ValidationFailureError(RESERVATION_AUTHORITY_MISSING)
        self._require_match(reservation, run, assignment)
        if reservation.state not in {"RESERVED", "CONSUMED"}:
            raise ValidationFailureError(RESERVATION_STALE_VERSION)
        if reservation.state == "RESERVED" and not run.episode_reservation_active:
            raise ValidationFailureError(RESERVATION_DRIFT)
        if reservation.state == "CONSUMED" and run.episode_reservation_active:
            raise ValidationFailureError(RESERVATION_DRIFT)
        return reservation

    def reservation_for_final_admission(
        self, run: ScriptQualificationRun
    ) -> SeriesEpisodeReservation | None:
        """Return the exact reservation that final admission must consume."""

        reservation = self.require_current(run)
        if reservation is not None and reservation.state != "RESERVED":
            raise ValidationFailureError(RESERVATION_STALE_VERSION)
        return reservation

    def consume_for_admission(
        self,
        *,
        qualification: ScriptQualificationRun,
        admission_decision_id: uuid.UUID,
        series_plan_id: uuid.UUID | None,
        series_run_id: uuid.UUID | None,
        episode_number: int | None,
        episode_role: str | None,
    ) -> SeriesEpisodeReservation | None:
        reservation = self.require_current(qualification)
        if reservation is None:
            if any(
                item is not None
                for item in (series_plan_id, series_run_id, episode_number, episode_role)
            ):
                raise ValidationFailureError(RESERVATION_DRIFT)
            return None
        if (
            reservation.series_plan_id != series_plan_id
            or reservation.series_run_id != series_run_id
            or reservation.episode_number != episode_number
            or reservation.episode_role != episode_role
        ):
            raise ValidationFailureError(RESERVATION_DRIFT)
        if reservation.state == "CONSUMED":
            if reservation.consumed_admission_decision_id != admission_decision_id:
                raise ValidationFailureError(RESERVATION_CONSUMED_BY_OTHER)
            return reservation
        if reservation.state != "RESERVED":
            raise ValidationFailureError(RESERVATION_STALE_VERSION)
        reservation.state = "CONSUMED"
        reservation.consumed_admission_decision_id = admission_decision_id
        reservation.consumed_at = utc_now()
        qualification.episode_reservation_active = False
        self.session.flush()
        return reservation

    def release_for_terminal_qualification(
        self,
        run: ScriptQualificationRun,
        *,
        reason_code: str,
    ) -> SeriesEpisodeReservation | None:
        """Cancel a pre-admission reservation once, freeing only capacity."""

        assignment = self._assignment(run)
        reservation = self._reservation(run.id, lock=True)
        if assignment["content_mode"] == "STANDALONE":
            if reservation is not None or run.episode_reservation_active:
                raise ValidationFailureError(RESERVATION_RELEASE_MISMATCH)
            return None
        if reservation is None:
            # Historic runs have no current authority.  They are already
            # permanently ineligible, but blocking/superseding them remains
            # safe and idempotent.
            run.episode_reservation_active = False
            return None
        self._require_match(reservation, run, assignment)
        if reservation.state == "RELEASED":
            if run.episode_reservation_active:
                raise ValidationFailureError(RESERVATION_RELEASE_MISMATCH)
            return reservation
        if reservation.state == "CONSUMED":
            raise ValidationFailureError(RESERVATION_CONSUMED_BY_OTHER)
        series_run = self.session.scalar(
            select(SeriesRun)
            .where(SeriesRun.id == reservation.series_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if series_run is None or series_run.reserved_episode_count <= 0:
            raise ValidationFailureError(RESERVATION_RELEASE_MISMATCH)
        series_run.reserved_episode_count -= 1
        reservation.state = "RELEASED"
        reservation.released_reason_code = reason_code
        reservation.released_at = utc_now()
        run.episode_reservation_active = False
        self.session.flush()
        return reservation

    def _reservation(
        self, qualification_id: uuid.UUID, *, lock: bool
    ) -> SeriesEpisodeReservation | None:
        statement = select(SeriesEpisodeReservation).where(
            SeriesEpisodeReservation.script_qualification_run_id == qualification_id
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    @staticmethod
    def _assignment(run: ScriptQualificationRun) -> dict[str, Any]:
        resolution = run.assignment_resolution
        if not isinstance(resolution, dict):
            raise ValidationFailureError(RESERVATION_ASSIGNMENT_MISMATCH)
        if resolution.get("resolution_hash") != run.assignment_resolution_hash:
            raise ValidationFailureError(RESERVATION_ASSIGNMENT_MISMATCH)
        content_mode = resolution.get("content_mode")
        if content_mode == "STANDALONE":
            return {"content_mode": "STANDALONE"}
        if content_mode != "SERIES_EPISODE":
            raise ValidationFailureError(RESERVATION_ASSIGNMENT_MISMATCH)
        try:
            plan_id = uuid.UUID(str(resolution.get("series_plan_id")))
            run_id = uuid.UUID(str(resolution.get("series_run_id")))
            episode_number = int(resolution.get("episode_number"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationFailureError(RESERVATION_ASSIGNMENT_MISMATCH) from exc
        episode_role = str(resolution.get("episode_role") or "").strip()
        episode_delta = str(resolution.get("episode_delta") or "").strip()
        resolution_hash = str(resolution.get("resolution_hash") or "")
        if episode_number <= 0 or not episode_role or not episode_delta or not resolution_hash:
            raise ValidationFailureError(RESERVATION_ASSIGNMENT_MISMATCH)
        return {
            "content_mode": "SERIES_EPISODE",
            "series_plan_id": plan_id,
            "series_run_id": run_id,
            "episode_number": episode_number,
            "episode_role": episode_role,
            "episode_delta": episode_delta,
            "resolution_hash": resolution_hash,
        }

    @staticmethod
    def _require_match(
        reservation: SeriesEpisodeReservation,
        run: ScriptQualificationRun,
        assignment: dict[str, Any],
    ) -> None:
        if (
            reservation.reservation_authority_version != RESERVATION_AUTHORITY_VERSION
            or assignment["content_mode"] != "SERIES_EPISODE"
            or reservation.series_plan_id != assignment["series_plan_id"]
            or reservation.series_run_id != assignment["series_run_id"]
            or reservation.episode_number != assignment["episode_number"]
            or reservation.episode_role != assignment["episode_role"]
            or reservation.episode_delta != assignment["episode_delta"]
            or reservation.assignment_resolution_hash
            != assignment["resolution_hash"]
            or run.assignment_resolution_hash != assignment["resolution_hash"]
        ):
            raise ValidationFailureError(RESERVATION_DRIFT)

    @staticmethod
    def _require_reservable(
        *,
        qualification: ScriptQualificationRun,
        candidate: EditorialIdeaCandidate | None,
        series_plan: SeriesPlan | None,
        series_run: SeriesRun | None,
        assignment: dict[str, Any],
    ) -> None:
        if (
            series_plan is None
            or series_run is None
            or candidate is None
            or series_run.series_plan_id != series_plan.id
            or series_plan.company_id != candidate.company_id
            or series_plan.channel_workspace_id != candidate.channel_workspace_id
            or series_plan.policy_snapshot_id != candidate.policy_snapshot_id
            or series_run.company_id != candidate.company_id
            or series_run.channel_workspace_id != candidate.channel_workspace_id
            or series_run.policy_snapshot_id != candidate.policy_snapshot_id
        ):
            raise ValidationFailureError(RESERVATION_DRIFT)
        if (
            series_plan.id != assignment["series_plan_id"]
            or series_run.id != assignment["series_run_id"]
            or series_plan.state != "APPROVED"
            or series_run.state != "ACTIVE"
            or series_run.reserved_episode_count >= series_run.capacity
            or series_run.next_episode_number != assignment["episode_number"]
        ):
            raise ValidationFailureError(RESERVATION_DRIFT)
