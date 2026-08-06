"""Deterministic settlement and operator recovery for script qualification.

The qualification result is immutable evidence about the two provider calls.
This module owns the separate local lifecycle of the cadence slot, candidate,
and series capacity after that result becomes terminal.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actor import ActorContext, ActorType
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m5 import EditorialIdeaCandidate
from app.db.models.script_qualification import ScriptQualificationRun
from app.services.config_registry import content_hash
from app.services.series_episode_reservation import EpisodeReservationAuthorityService


SETTLEMENT_SCHEMA_VERSION = "script-qualification-terminal-settlement.v1"
RECONCILIATION_SCHEMA_VERSION = "script-qualification-provider-reconciliation.v1"
UNKNOWN_PROVIDER_OUTCOME_CODE = "SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY"

ProviderOutcomeDecision = Literal[
    "PROVIDER_EFFECT_CONFIRMED", "NO_PROVIDER_EFFECT_CONFIRMED", "UNRESOLVED"
]


def _append_reason(existing: list[str] | None, reason: str) -> list[str]:
    return list(dict.fromkeys([*(existing or []), reason]))


class ScriptQualificationRecoveryService:
    """Settle terminal qualification outcomes without reopening a slot."""

    def __init__(self, session: Session, *, now: Any = utc_now) -> None:
        self.session = session
        self.now = now

    def settle_deterministic_block(
        self, run: ScriptQualificationRun, *, reason_code: str
    ) -> dict[str, Any]:
        return self._settle(
            run,
            kind="DETERMINISTIC_BLOCK",
            reason_code=reason_code,
            release_series_capacity=True,
        )

    def settle_unknown_provider_outcome(
        self, run: ScriptQualificationRun
    ) -> dict[str, Any]:
        return self._settle(
            run,
            kind="UNKNOWN_PROVIDER_OUTCOME",
            reason_code=UNKNOWN_PROVIDER_OUTCOME_CODE,
            release_series_capacity=False,
        )

    def _settle(
        self,
        run: ScriptQualificationRun,
        *,
        kind: Literal["DETERMINISTIC_BLOCK", "UNKNOWN_PROVIDER_OUTCOME"],
        reason_code: str,
        release_series_capacity: bool,
    ) -> dict[str, Any]:
        existing = run.terminal_settlement_receipt
        if isinstance(existing, dict):
            self._validate_receipt(existing)
            return existing

        slot = self.session.scalar(
            select(LongFormPublishSlot)
            .where(LongFormPublishSlot.id == run.publish_slot_id)
            .with_for_update()
        )
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == run.editorial_idea_candidate_id)
            .with_for_update()
        )
        if slot is None or candidate is None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SETTLEMENT_LINEAGE_MISSING")
        if (
            slot.reserved_candidate_id != candidate.id
            or slot.admitted_video_project_id is not None
        ):
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SETTLEMENT_LINEAGE_DRIFT")
        if candidate.stage in {"IN_PRODUCTION", "FINAL_REVIEW_READY", "PUBLISHED"}:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SETTLEMENT_AFTER_ADMISSION")

        reservation = None
        if release_series_capacity:
            authority = EpisodeReservationAuthorityService(self.session)
            try:
                reservation = authority.release_for_terminal_qualification(
                    run, reason_code=reason_code
                )
            except ValidationFailureError:
                # A malformed assignment can fail before any series authority
                # was reserved.  That must still settle the cadence slot.  If
                # a reservation exists, preserve the fail-closed capacity hold
                # rather than guessing which SeriesRun to release.
                reservation = authority._reservation(run.id, lock=True)
                if reservation is not None:
                    raise
            if slot.state not in {"QUALIFICATION_RESERVED", "CANCELED"}:
                raise ValidationFailureError("SCRIPT_QUALIFICATION_SLOT_SETTLEMENT_STATE_INVALID")
            slot.state = "CANCELED"
            candidate_reason = "SCRIPT_QUALIFICATION_TERMINAL_BLOCKED"
        else:
            reservation = EpisodeReservationAuthorityService(
                self.session
            )._reservation(run.id, lock=True)
            if slot.state not in {
                "QUALIFICATION_RESERVED",
                "QUALIFICATION_RECONCILIATION_REQUIRED",
            }:
                raise ValidationFailureError("SCRIPT_QUALIFICATION_SLOT_SETTLEMENT_STATE_INVALID")
            slot.state = "QUALIFICATION_RECONCILIATION_REQUIRED"
            candidate_reason = "SCRIPT_QUALIFICATION_RECONCILIATION_REQUIRED"

        if candidate.stage != "REJECTED":
            candidate.stage = "REJECTED"
        candidate.reason_codes = _append_reason(candidate.reason_codes, candidate_reason)
        body = {
            "schema_version": SETTLEMENT_SCHEMA_VERSION,
            "qualification_run_id": str(run.id),
            "kind": kind,
            "reason_code": reason_code,
            "publish_slot_id": str(slot.id),
            "publish_slot_state": slot.state,
            "reserved_candidate_id": str(slot.reserved_candidate_id),
            "candidate_id": str(candidate.id),
            "candidate_stage": candidate.stage,
            "reservation_id": str(reservation.id) if reservation is not None else None,
            "reservation_state": reservation.state if reservation is not None else None,
            "capacity_released": release_series_capacity,
            "settled_at": self.now().isoformat(),
        }
        receipt = {**body, "content_hash": content_hash(body)}
        run.terminal_settlement_receipt = receipt
        failure = dict(run.failure_receipt or {})
        failure["terminal_settlement_receipt_hash"] = receipt["content_hash"]
        run.failure_receipt = failure
        self.session.flush()
        return receipt

    def reconcile_provider_outcome(
        self,
        *,
        run_id: uuid.UUID,
        decision: ProviderOutcomeDecision,
        evidence_refs: list[dict[str, Any]],
        reason_code: str,
        actor: ActorContext,
    ) -> dict[str, Any]:
        """Record the only safe resolution of an unknown provider outcome."""

        if (
            actor.actor_type != ActorType.HUMAN_USER
            or not actor.has_permission("production.start")
        ):
            raise ValidationFailureError("SCRIPT_PROVIDER_RECONCILIATION_FORBIDDEN")
        if not reason_code.strip() or not evidence_refs or not all(
            isinstance(item, dict) and (item.get("ref") or item.get("id"))
            for item in evidence_refs
        ):
            raise ValidationFailureError("SCRIPT_PROVIDER_RECONCILIATION_EVIDENCE_REQUIRED")
        run = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise ValidationFailureError("SCRIPT_PROVIDER_RECONCILIATION_RUN_MISSING")
        failure = run.failure_receipt if isinstance(run.failure_receipt, dict) else {}
        if (
            run.state not in {"BLOCKED_NON_REPAIRABLE", "SUPERSEDED"}
            or UNKNOWN_PROVIDER_OUTCOME_CODE not in (failure.get("reason_codes") or [])
        ):
            raise ValidationFailureError("SCRIPT_PROVIDER_RECONCILIATION_NOT_REQUIRED")

        command = {
            "qualification_run_id": str(run.id),
            "decision": decision,
            "reason_code": reason_code.strip(),
            "evidence_refs": evidence_refs,
        }
        command_hash = content_hash(command)
        receipts = list(run.provider_outcome_reconciliation_receipts or [])
        for item in receipts:
            if isinstance(item, dict) and item.get("command_hash") == command_hash:
                self._validate_receipt(item)
                return item

        if decision == "NO_PROVIDER_EFFECT_CONFIRMED":
            reservation = EpisodeReservationAuthorityService(
                self.session
            ).release_for_terminal_qualification(
                run, reason_code="SCRIPT_PROVIDER_OUTCOME_NO_EFFECT_CONFIRMED"
            )
            slot = self.session.scalar(
                select(LongFormPublishSlot)
                .where(LongFormPublishSlot.id == run.publish_slot_id)
                .with_for_update()
            )
            if slot is None or slot.state not in {
                "QUALIFICATION_RECONCILIATION_REQUIRED",
                "CANCELED",
            }:
                raise ValidationFailureError("SCRIPT_PROVIDER_RECONCILIATION_SLOT_DRIFT")
            slot.state = "CANCELED"
            run.state = "SUPERSEDED"
            outcome = "SUPERSEDED_AND_CAPACITY_RELEASED"
        elif decision == "PROVIDER_EFFECT_CONFIRMED":
            reservation = EpisodeReservationAuthorityService(
                self.session
            )._reservation(run.id, lock=True)
            outcome = "HELD_TERMINAL"
        else:
            reservation = EpisodeReservationAuthorityService(
                self.session
            )._reservation(run.id, lock=True)
            outcome = "HELD_UNRESOLVED"

        body = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "command_hash": command_hash,
            "qualification_run_id": str(run.id),
            "decision": decision,
            "reason_code": reason_code.strip(),
            "evidence_refs": evidence_refs,
            "actor_id": str(actor.actor_id),
            "outcome": outcome,
            "reservation_id": str(reservation.id) if reservation is not None else None,
            "reservation_state": reservation.state if reservation is not None else None,
            "recorded_at": self.now().isoformat(),
        }
        receipt = {**body, "content_hash": content_hash(body)}
        run.provider_outcome_reconciliation_receipts = [*receipts, receipt]
        self.session.flush()
        return receipt

    @staticmethod
    def _validate_receipt(receipt: dict[str, Any]) -> None:
        body = {key: value for key, value in receipt.items() if key != "content_hash"}
        if receipt.get("content_hash") != content_hash(body):
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECOVERY_RECEIPT_HASH_INVALID")
