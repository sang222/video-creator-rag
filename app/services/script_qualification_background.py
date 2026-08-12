"""Durable OpenAI Responses Background execution for script qualification.

The service intentionally commits every state transition before crossing the
provider boundary.  A worker crash can therefore resume from a durable
``provider_response_id`` and never turns a poll/network failure into a second
generation request.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.script_qualification import (
    QualifiedScriptOutput,
    QualifiedScriptOutputV2,
    SemanticVerificationOutput,
)
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.foundation import LLMRunSnapshot
from app.db.models.m10_1 import LLMRouteAttempt
from app.db.models.m5 import EditorialIdeaCandidate, IdeaMarketPreflight
from app.db.models.script_qualification import (
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationProviderReclassificationReceipt,
    ScriptQualificationRun,
)
from app.providers.openai import OpenAIResponsesProvider, OpenAIResponsesRequest
from app.services.config_registry import content_hash


BACKGROUND_EVENT_TYPE = "script_qualification.background.execute.v1"
BACKGROUND_POLL_EVENT_TYPE = "script_qualification.background.poll.v1"
RECOVERY_PREFIX = "script-qualification-recovery"

QUALIFICATION_PROVIDER_STAGE_COUNT = 2
QUALIFICATION_POLL_CYCLES_PER_STAGE = 60
QUALIFICATION_QUEUE_LATENCY_SECONDS_PER_STAGE = 60
QUALIFICATION_SAFETY_BUFFER_SECONDS = 5 * 60
QUALIFICATION_DOWNSTREAM_LEAD_SECONDS = 3 * 60 * 60


@dataclass(frozen=True, slots=True)
class ScriptQualificationDeadlinePolicy:
    """Bounded time authority for both Background provider stages."""

    submit_timeout_seconds: int
    poll_request_timeout_seconds: int
    poll_interval_seconds: int
    provider_stage_count: int = QUALIFICATION_PROVIDER_STAGE_COUNT
    poll_cycles_per_stage: int = QUALIFICATION_POLL_CYCLES_PER_STAGE
    queue_latency_seconds_per_stage: int = QUALIFICATION_QUEUE_LATENCY_SECONDS_PER_STAGE
    safety_buffer_seconds: int = QUALIFICATION_SAFETY_BUFFER_SECONDS
    downstream_lead_seconds: int = QUALIFICATION_DOWNSTREAM_LEAD_SECONDS

    def __post_init__(self) -> None:
        if any(value <= 0 for value in (
            self.submit_timeout_seconds,
            self.poll_request_timeout_seconds,
            self.poll_interval_seconds,
            self.poll_cycles_per_stage,
        )):
            raise ValueError("script qualification deadline policy values must be positive")
        if self.provider_stage_count != QUALIFICATION_PROVIDER_STAGE_COUNT:
            raise ValueError("script qualification requires exactly two provider stages")
        if any(value < 0 for value in (
            self.queue_latency_seconds_per_stage,
            self.safety_buffer_seconds,
            self.downstream_lead_seconds,
        )):
            raise ValueError("script qualification deadline policy buffers must be non-negative")

    @property
    def provider_stage_budget_seconds(self) -> int:
        return (
            self.submit_timeout_seconds
            + self.poll_cycles_per_stage
            * (self.poll_request_timeout_seconds + self.poll_interval_seconds)
            + self.queue_latency_seconds_per_stage
        )

    @property
    def total_qualification_budget_seconds(self) -> int:
        return self.provider_stage_count * self.provider_stage_budget_seconds + self.safety_buffer_seconds

    def receipt(self) -> dict[str, Any]:
        body = {
            "schema_version": "script-qualification-deadline-policy.v1",
            "submit_timeout_seconds": self.submit_timeout_seconds,
            "poll_request_timeout_seconds": self.poll_request_timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "provider_stage_count": self.provider_stage_count,
            "poll_cycles_per_stage": self.poll_cycles_per_stage,
            "queue_latency_seconds_per_stage": self.queue_latency_seconds_per_stage,
            "provider_stage_budget_seconds": self.provider_stage_budget_seconds,
            "safety_buffer_seconds": self.safety_buffer_seconds,
            "total_qualification_budget_seconds": self.total_qualification_budget_seconds,
            "downstream_lead_seconds": self.downstream_lead_seconds,
        }
        return {**body, "content_hash": content_hash(body)}


def build_script_qualification_deadline_policy(
    settings: Any | None = None,
    *,
    poll_cycles_per_stage: int | None = None,
    queue_latency_seconds_per_stage: int | None = None,
    safety_buffer_seconds: int | None = None,
    downstream_lead_seconds: int | None = None,
) -> ScriptQualificationDeadlinePolicy:
    """Build the policy from runtime timeouts and explicit bounded buffers."""

    configured = settings or get_settings()
    return ScriptQualificationDeadlinePolicy(
        submit_timeout_seconds=configured.openai_background_submit_timeout_seconds,
        poll_request_timeout_seconds=configured.openai_background_poll_request_timeout_seconds,
        poll_interval_seconds=configured.script_qualification_background_poll_seconds,
        poll_cycles_per_stage=(
            poll_cycles_per_stage
            if poll_cycles_per_stage is not None
            else getattr(
                configured,
                "script_qualification_background_poll_cycles_per_stage",
                QUALIFICATION_POLL_CYCLES_PER_STAGE,
            )
        ),
        queue_latency_seconds_per_stage=(
            queue_latency_seconds_per_stage
            if queue_latency_seconds_per_stage is not None
            else getattr(
                configured,
                "script_qualification_background_queue_latency_seconds_per_stage",
                QUALIFICATION_QUEUE_LATENCY_SECONDS_PER_STAGE,
            )
        ),
        safety_buffer_seconds=(
            safety_buffer_seconds
            if safety_buffer_seconds is not None
            else getattr(
                configured,
                "script_qualification_background_safety_buffer_seconds",
                QUALIFICATION_SAFETY_BUFFER_SECONDS,
            )
        ),
        downstream_lead_seconds=(
            downstream_lead_seconds
            if downstream_lead_seconds is not None
            else getattr(
                configured,
                "script_qualification_downstream_lead_seconds",
                QUALIFICATION_DOWNSTREAM_LEAD_SECONDS,
            )
        ),
    )


def derive_script_qualification_deadline(
    slot_window_close_at: datetime,
    policy: ScriptQualificationDeadlinePolicy | None = None,
) -> datetime:
    resolved = policy or build_script_qualification_deadline_policy()
    return slot_window_close_at - timedelta(seconds=resolved.downstream_lead_seconds)


def minimum_script_qualification_window_close_at(
    requested_at: datetime,
    policy: ScriptQualificationDeadlinePolicy | None = None,
) -> datetime:
    """Return the exclusive lower bound for a viable slot close."""

    resolved = policy or build_script_qualification_deadline_policy()
    return requested_at + timedelta(
        seconds=resolved.total_qualification_budget_seconds + resolved.downstream_lead_seconds
    )


def script_qualification_slot_is_viable(
    now: datetime,
    slot_window_close_at: datetime,
    policy: ScriptQualificationDeadlinePolicy | None = None,
) -> bool:
    resolved = policy or build_script_qualification_deadline_policy()
    deadline = derive_script_qualification_deadline(slot_window_close_at, resolved)
    return deadline > now and now + timedelta(seconds=resolved.total_qualification_budget_seconds) < deadline


class ScriptQualificationBackgroundService:
    def __init__(self, session: Session, *, now=utc_now, provider: OpenAIResponsesProvider | None = None) -> None:
        self.session = session
        self.now = now
        self.settings = get_settings()
        self.deadline_policy = build_script_qualification_deadline_policy(self.settings)
        self.provider = provider or OpenAIResponsesProvider(
            api_key=(self.settings.openai_api_key.get_secret_value() if self.settings.openai_api_key else None),
            base_url=self.settings.openai_base_url,
            timeout_seconds=self.settings.openai_timeout_seconds,
            runtime_origin="script-qualification-background",
        )

    def authorize_recovery(self, *, original_run_id: uuid.UUID, requested_at: datetime | None = None) -> ScriptQualificationRun:
        """Create the one allowed T+1 child run without modifying its parent."""
        requested_at = requested_at or self.now()
        original = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.id == original_run_id).with_for_update())
        if original is None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_ORIGINAL_RUN_NOT_FOUND")
        if original.state != "BLOCKED_NON_REPAIRABLE":
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECOVERY_ORIGINAL_NOT_TERMINAL")
        route = self.session.scalar(select(LLMRouteAttempt).join(
            LLMRunSnapshot, LLMRunSnapshot.id == LLMRouteAttempt.llm_run_snapshot_id
        ).where(LLMRunSnapshot.correlation_id == original.writer_attempt_key).order_by(LLMRouteAttempt.created_at.desc()))
        receipt = self.session.scalar(select(ScriptQualificationProviderReclassificationReceipt).where(ScriptQualificationProviderReclassificationReceipt.original_qualification_run_id == original.id))
        if receipt is None:
            body = {
                "schema_version": "script-qualification-provider-reclassification.v1",
                "original_qualification_run_id": str(original.id),
                "original_route_attempt_id": str(route.id) if route else None,
                "original_classification": (original.failure_receipt or {}).get("reason_codes", []),
                "observed_error": route.error_message if route else (original.failure_receipt or {}).get("detail"),
                "correct_failure_domain": "PROVIDER_INFRASTRUCTURE",
                "content_failure": False,
                "candidate_editorial_eligibility_unchanged": True,
                "provider_outcome": "UNKNOWN_SUBMISSION_TIMEOUT",
                "retry_authorized": True,
                "authorized_recovery_count": 1,
                "requested_recovery_at": requested_at.isoformat(),
            }
            self.session.add(ScriptQualificationProviderReclassificationReceipt(
                original_qualification_run_id=original.id,
                original_route_attempt_id=route.id if route else None,
                receipt=body, receipt_hash=content_hash(body),
            ))
        existing = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.supersedes_qualification_run_id == original.id)
            .order_by(
                ScriptQualificationRun.logical_attempt_number.desc(),
                ScriptQualificationRun.created_at.desc(),
                ScriptQualificationRun.id.desc(),
            )
            .with_for_update()
        )
        if existing is not None:
            return existing
        slot = self.session.get(LongFormPublishSlot, original.publish_slot_id)
        if slot is None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SLOT_NOT_FOUND")
        if not script_qualification_slot_is_viable(
            requested_at, slot.target_start_window_close_at, self.deadline_policy
        ):
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECOVERY_DEADLINE_NOT_VIABLE")
        recovery_key = f"{RECOVERY_PREFIX}:{original.id}:attempt-{original.logical_attempt_number + 1}"
        deadline = self._deadline_for(original, requested_at=requested_at)
        child = ScriptQualificationRun(
            editorial_idea_candidate_id=original.editorial_idea_candidate_id,
            publish_slot_id=original.publish_slot_id, launch_run_id=original.launch_run_id,
            topic_definition_id=original.topic_definition_id, topic_definition_hash=original.topic_definition_hash,
            script_assignment=original.script_assignment, script_assignment_hash=original.script_assignment_hash,
            factual_evidence_pack=original.factual_evidence_pack, factual_evidence_pack_hash=original.factual_evidence_pack_hash,
            memory_digest=original.memory_digest, memory_digest_hash=original.memory_digest_hash,
            runtime_contract=original.runtime_contract, runtime_contract_hash=original.runtime_contract_hash,
            assignment_resolution=original.assignment_resolution, assignment_resolution_hash=original.assignment_resolution_hash,
            episode_reservation_active=original.episode_reservation_active,
            writer_prompt_version=original.writer_prompt_version, verifier_prompt_version=original.verifier_prompt_version,
            gate_policy_version=original.gate_policy_version, model=original.model,
            logical_attempt_number=original.logical_attempt_number + 1,
            logical_identity_hash=content_hash({"original": original.logical_identity_hash, "recovery_key": recovery_key}),
            supersedes_qualification_run_id=original.id, recovery_key=recovery_key,
            recovery_requested_at=requested_at, logical_deadline_at=deadline,
            state="RECOVERY_AUTHORIZED",
            writer_attempt_key=f"{recovery_key}:writer", verifier_attempt_key=f"{recovery_key}:verifier",
        )
        self.session.add(child)
        self.session.flush()
        self._enqueue(child, due_at=requested_at, event_type=BACKGROUND_EVENT_TYPE, command_id=recovery_key)
        return child

    def resume_local_pre_submission_failure(
        self,
        *,
        run_id: uuid.UUID,
        scheduled_at: datetime,
        fired_at: datetime,
    ) -> ScriptQualificationRun:
        """Repair a proven local failure before any provider boundary.

        This is deliberately narrower than provider-outcome reconciliation: it
        is allowed only for the one recovery child and only when the persisted
        evidence proves that no Background attempt or provider response was
        created.  It compensates the local terminal settlement caused by that
        defect, then queues the *same* governed recovery identity once.
        """

        run = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.supersedes_qualification_run_id is None:
            raise ValidationFailureError("RECOVERY_LOCAL_RESUME_RUN_INVALID")
        failure = dict(run.failure_receipt or {})
        detail = str(failure.get("detail") or "")
        attempts = list(
            self.session.scalars(
                select(ScriptQualificationBackgroundAttempt).where(
                    ScriptQualificationBackgroundAttempt.script_qualification_run_id
                    == run.id
                )
            ).all()
        )
        if (
            run.state != "BLOCKED_NON_REPAIRABLE"
            or ">=" not in detail
            or "NoneType" not in detail
            or attempts
        ):
            raise ValidationFailureError("RECOVERY_LOCAL_RESUME_NOT_PROVEN_PRE_SUBMISSION")

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
        if (
            slot is None
            or candidate is None
            or slot.reserved_candidate_id != candidate.id
            or slot.admitted_video_project_id is not None
            or slot.state != "QUALIFICATION_RECONCILIATION_REQUIRED"
            or candidate.stage != "REJECTED"
        ):
            raise ValidationFailureError("RECOVERY_LOCAL_RESUME_SETTLEMENT_DRIFT")

        previous_settlement = dict(run.terminal_settlement_receipt or {})
        entry = {
            "schema_version": "script-qualification-local-pre-submission-compensation.v1",
            "recovery_qualification_run_id": str(run.id),
            "original_qualification_run_id": str(run.supersedes_qualification_run_id),
            "failed_event_execution_detail": detail[:512],
            "provider_submission_count": 0,
            "provider_response_id": None,
            "previous_terminal_settlement_receipt_hash": previous_settlement.get("content_hash"),
            "compensated_at": self.now().isoformat(),
            "scheduled_at": scheduled_at.isoformat(),
            "fired_at": fired_at.isoformat(),
            "delay_seconds": max(0, int((fired_at - scheduled_at).total_seconds())),
        }
        entry["content_hash"] = content_hash(entry)
        run.provider_outcome_reconciliation_receipts = [
            *(run.provider_outcome_reconciliation_receipts or []),
            entry,
        ]
        # These were created only by the local pre-submission defect above;
        # retain its immutable compensation record while restoring the original
        # reservation that existed before this task's failed worker execution.
        slot.state = "QUALIFICATION_RESERVED"
        candidate.stage = "GREENLIT"
        candidate.reason_codes = [
            item
            for item in (candidate.reason_codes or [])
            if item != "SCRIPT_QUALIFICATION_RECONCILIATION_REQUIRED"
        ]
        run.state = "RECOVERY_AUTHORIZED"
        run.failure_receipt = None
        run.terminal_settlement_receipt = None
        command_id = f"{run.recovery_key}:resume-pre-submission"
        self._enqueue(
            run,
            due_at=fired_at,
            event_type=BACKGROUND_EVENT_TYPE,
            command_id=command_id,
            metadata={
                "recovery_schedule_missed": {
                    "scheduled_at": scheduled_at.isoformat(),
                    "fired_at": fired_at.isoformat(),
                    "delay_seconds": max(
                        0, int((fired_at - scheduled_at).total_seconds())
                    ),
                    "reason_code": "RECOVERY_SCHEDULE_MISSED",
                    "cause": "BACKGROUND_EVENT_DISPATCH_ALLOWLIST_MISSING",
                },
                "recovery_local_pre_submission_compensation_hash": entry[
                    "content_hash"
                ],
            },
        )
        self.session.flush()
        return run

    def execute(self, run_id: uuid.UUID) -> ScriptQualificationRun:
        run = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.id == run_id).with_for_update())
        if run is None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RUN_NOT_FOUND")
        if run.state in {"QUALIFIED", "BLOCKED_NON_REPAIRABLE", "BLOCKED_REPAIR_BUDGET_EXHAUSTED", "COOLDOWN", "SUPERSEDED"}:
            return run
        if run.logical_deadline_at is None:
            run.logical_deadline_at = self._deadline_for(run, requested_at=self.now())
        if self.now() >= run.logical_deadline_at:
            return self._deadline(run)
        attempt = self.session.scalar(select(ScriptQualificationBackgroundAttempt).where(ScriptQualificationBackgroundAttempt.script_qualification_run_id == run.id, ScriptQualificationBackgroundAttempt.background_status.not_in(("COMPLETED", "FAILED", "CANCELLED", "INCOMPLETE", "DEADLINE_EXCEEDED", "SUBMISSION_OUTCOME_UNKNOWN"))).order_by(ScriptQualificationBackgroundAttempt.created_at.desc()))
        if attempt is not None:
            if attempt.provider_response_id and attempt.next_poll_at and attempt.next_poll_at <= self.now():
                return self._poll(run, attempt)
            return run
        if run.script_payload is None:
            if not self._recovery_guards_pass(run, phase="WRITER"):
                return run
            return self._submit(run, phase="WRITER")
        if not self._recovery_guards_pass(run, phase="VERIFIER"):
            return run
        return self._submit(run, phase="VERIFIER")

    def enqueue_due_polls(self) -> int:
        now = self.now()
        attempts = self.session.scalars(select(ScriptQualificationBackgroundAttempt).where(
            ScriptQualificationBackgroundAttempt.background_status.in_(("SUBMITTED", "QUEUED", "IN_PROGRESS")),
            ScriptQualificationBackgroundAttempt.next_poll_at <= now,
        ).with_for_update(skip_locked=True)).all()
        for attempt in attempts:
            self._enqueue(self.session.get(ScriptQualificationRun, attempt.script_qualification_run_id), due_at=now,
                event_type=BACKGROUND_POLL_EVENT_TYPE, command_id=f"script-qualification-background-poll:{attempt.id}:{attempt.poll_count + 1}", background_attempt_id=attempt.id)
        return len(attempts)

    def _submit(self, run: ScriptQualificationRun, *, phase: str) -> ScriptQualificationRun:
        context = self._writer_context(run) if phase == "WRITER" else self._verifier_context(run)
        lane, task = (("long_context_text", "long_form_script") if phase == "WRITER" else ("gatekeeper_soft_review", "factuality_review"))
        schema_identifier, schema = self._response_schema(run, phase=phase)
        prompt_version = run.writer_prompt_version if phase == "WRITER" else run.verifier_prompt_version
        prompt = self._schema_bound_prompt(
            context=context,
            phase=phase,
            schema_identifier=schema_identifier,
            prompt_version=prompt_version,
        )
        schema_hash = content_hash(schema)
        fingerprint = content_hash({"phase": phase, "prompt": prompt, "model": run.model, "lane": lane, "schema_identifier": schema_identifier, "schema_hash": schema_hash, "prompt_version": prompt_version})
        attempt = self.session.scalar(select(ScriptQualificationBackgroundAttempt).where(ScriptQualificationBackgroundAttempt.script_qualification_run_id == run.id, ScriptQualificationBackgroundAttempt.phase == phase).with_for_update())
        if attempt is None:
            attempt = ScriptQualificationBackgroundAttempt(
                script_qualification_run_id=run.id, phase=phase, provider="OPENAI", model=run.model, lane=lane, task=task,
                input_fingerprint=fingerprint, immutable_input_hashes=self._hashes(run),
                client_correlation_id=(run.writer_attempt_key if phase == "WRITER" else run.verifier_attempt_key),
                logical_deadline_at=run.logical_deadline_at or self._deadline_for(run, requested_at=self.now()),
                background_status="SUBMIT_PENDING",
                poll_count=0,
                submission_attempt_count=0,
                response_schema_identifier=schema_identifier,
                response_schema_hash=schema_hash,
                prompt_version=prompt_version,
            )
            self.session.add(attempt)
        if attempt.provider_response_id:
            return run
        if attempt.submission_attempt_count >= 1:
            attempt.background_status = "SUBMISSION_OUTCOME_UNKNOWN"
            return self._provider_block(run, attempt, "SCRIPT_PROVIDER_SUBMISSION_OUTCOME_UNKNOWN")
        run.state = f"{phase}_SUBMIT_PENDING"
        self.session.flush()
        # Explicitly release the database transaction before network I/O.
        self.session.commit()
        result = self.provider.submit_background(request=OpenAIResponsesRequest(model=run.model, reasoning_effort="medium", prompt=prompt, response_format="json", json_schema=schema, json_schema_name=schema_identifier, json_schema_strict=True, idempotency_key=attempt.client_correlation_id, background=True), timeout_seconds=self.settings.openai_background_submit_timeout_seconds)
        run = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.id == run.id).with_for_update())
        attempt = self.session.scalar(select(ScriptQualificationBackgroundAttempt).where(ScriptQualificationBackgroundAttempt.id == attempt.id).with_for_update())
        assert run is not None and attempt is not None
        attempt.submission_attempt_count = 1
        if not result.ok:
            attempt.last_network_error = result.error_code
            if result.error_code == "OPENAI_INVALID_REQUEST":
                attempt.provider_outcome = "PROVIDER_REQUEST_REJECTED_NO_RESPONSE"
            attempt.background_status = "SUBMISSION_OUTCOME_UNKNOWN"
            return self._provider_block(
                run,
                attempt,
                "SCRIPT_PROVIDER_REQUEST_REJECTED_NO_RESPONSE"
                if result.error_code == "OPENAI_INVALID_REQUEST"
                else "SCRIPT_PROVIDER_SUBMISSION_OUTCOME_UNKNOWN",
                detail=result.error_message or result.error_code,
            )
        attempt.provider_response_id = result.output["provider_response_id"]
        attempt.provider_request_id = result.output.get("provider_request_id")
        attempt.submitted_at = self.now()
        attempt.next_poll_at = self.now()
        attempt.background_status = self._status(result.output.get("background_status"))
        run.state = f"{phase}_BACKGROUND_SUBMITTED"
        self.session.flush()
        return run

    def _poll(self, run: ScriptQualificationRun, attempt: ScriptQualificationBackgroundAttempt) -> ScriptQualificationRun:
        assert attempt.provider_response_id
        attempt.last_polled_at = self.now()
        attempt.poll_count += 1
        self.session.flush()
        self.session.commit()
        result = self.provider.retrieve_background(response_id=attempt.provider_response_id, timeout_seconds=self.settings.openai_background_poll_request_timeout_seconds)
        run = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.id == run.id).with_for_update())
        attempt = self.session.scalar(select(ScriptQualificationBackgroundAttempt).where(ScriptQualificationBackgroundAttempt.id == attempt.id).with_for_update())
        assert run is not None and attempt is not None
        if not result.ok:
            attempt.last_network_error = result.error_code
            attempt.next_poll_at = self.now() + timedelta(seconds=self.settings.script_qualification_background_poll_seconds)
            return run
        attempt.provider_request_id = result.output.get("provider_request_id") or attempt.provider_request_id
        attempt.usage = result.output.get("usage")
        status = self._status(result.output.get("background_status"))
        attempt.background_status = status
        if status in {"SUBMITTED", "QUEUED", "IN_PROGRESS"}:
            attempt.next_poll_at = self.now() + timedelta(seconds=self.settings.script_qualification_background_poll_seconds)
            run.state = f"{attempt.phase}_{status}" if status != "SUBMITTED" else f"{attempt.phase}_BACKGROUND_SUBMITTED"
            return run
        if status in {"FAILED", "CANCELLED", "INCOMPLETE"}:
            return self._provider_block(run, attempt, f"SCRIPT_PROVIDER_BACKGROUND_{status}")
        if status != "COMPLETED":
            return self._provider_block(run, attempt, "SCRIPT_PROVIDER_BACKGROUND_INCOMPLETE")
        attempt.completed_at = self.now()
        attempt.provider_outcome = "COMPLETED"
        attempt.output_hash = content_hash(result.output.get("raw") or {})
        try:
            parsed = json.loads(result.output.get("content") or "")
            if attempt.phase == "WRITER":
                from app.services.script_qualification import ScriptQualificationService

                service = ScriptQualificationService(self.session)
                service.writer_output_model(run).model_validate(parsed)
                self._persist_response_snapshot(
                    run=run, attempt=attempt, output=result.output,
                    accepted_typed_output_hash=content_hash(parsed),
                )
                from app.services.script_content_repair import (
                    ScriptContentRepairService,
                )

                ScriptContentRepairService(
                    self.session, now=self.now
                ).validate_output_scope(
                    run, service.writer_output_model(run).model_validate(parsed)
                )
                draft = service.accept_writer_output(run, parsed)
                run.writer_receipt = self._receipt(attempt, result.output)
                structural = service._structural_receipt(run, draft)
                if structural["status"] != "PASS":
                    return service._seal_block(run, draft, {"structural": structural})
                run.state = "SCRIPT_GENERATED"
                return run
            from app.services.script_qualification import ScriptQualificationService
            service = ScriptQualificationService(self.session)
            draft = service.draft_from_run(run)
            verifier = SemanticVerificationOutput.model_validate(parsed)
            self._persist_response_snapshot(
                run=run, attempt=attempt, output=result.output,
                accepted_typed_output_hash=content_hash(verifier.model_dump(mode="json")),
            )
            run.verifier_receipt = self._receipt(attempt, result.output)
            structural = service._structural_receipt(run, draft)
            receipts = service._semantic_receipts(run, draft, verifier, structural)
            if all(item["status"] in {"PASS", "PASS_EMPTY"} for item in receipts.values()):
                run.state = "QUALIFIED"
                run.result_receipts = receipts
                service._create_receipt(run, draft, "PASS", receipts)
                return run
            return service._seal_block(run, draft, receipts)
        except Exception as exc:
            self._persist_response_snapshot(
                run=run,
                attempt=attempt,
                output=result.output,
                validation_errors=_validation_errors(exc),
            )
            return self._provider_block(run, attempt, "SCRIPT_WRITER_OUTPUT_INVALID" if attempt.phase == "WRITER" else "SCRIPT_VERIFIER_OUTPUT_INVALID", detail=str(exc))

    def _provider_block(self, run: ScriptQualificationRun, attempt: ScriptQualificationBackgroundAttempt, code: str, detail: str | None = None) -> ScriptQualificationRun:
        unknown_provider_outcome = (
            code != "SCRIPT_PROVIDER_REQUEST_REJECTED_NO_RESPONSE"
            and (
                code == "SCRIPT_PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"
                or self._attempt_provider_outcome_is_unknown(attempt)
            )
        )
        reason_codes = [code]
        if unknown_provider_outcome:
            reason_codes.append("SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY")
        run.state = "BLOCKED_NON_REPAIRABLE"
        attempt.provider_outcome = code
        attempt.next_poll_at = None
        run.failure_receipt = {"reason_codes": reason_codes, "detail": (detail or attempt.last_network_error or "")[:512], "logical_identity_hash": run.logical_identity_hash}
        self._settle_terminal_if_current(run, reason_code=code, unknown_provider_outcome=unknown_provider_outcome)
        return run

    def _deadline(self, run: ScriptQualificationRun) -> ScriptQualificationRun:
        attempts = list(self.session.scalars(select(ScriptQualificationBackgroundAttempt).where(ScriptQualificationBackgroundAttempt.script_qualification_run_id == run.id)).all())
        unknown_provider_outcome = any(self._attempt_provider_outcome_is_unknown(attempt) for attempt in attempts)
        for attempt in attempts:
            if attempt.background_status not in {"COMPLETED", "FAILED", "CANCELLED", "INCOMPLETE", "SUBMISSION_OUTCOME_UNKNOWN"}:
                attempt.background_status = "DEADLINE_EXCEEDED"
                attempt.provider_outcome = (
                    "SCRIPT_PROVIDER_LOGICAL_DEADLINE_EXCEEDED"
                )
                attempt.next_poll_at = None
        reason_codes = ["SCRIPT_PROVIDER_LOGICAL_DEADLINE_EXCEEDED"]
        if unknown_provider_outcome:
            reason_codes.append("SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY")
        run.state = "BLOCKED_NON_REPAIRABLE"
        run.failure_receipt = {"reason_codes": reason_codes, "deadline_policy": self.deadline_policy.receipt()}
        self._settle_terminal_if_current(
            run,
            reason_code="SCRIPT_PROVIDER_LOGICAL_DEADLINE_EXCEEDED",
            unknown_provider_outcome=unknown_provider_outcome,
        )
        return run

    def _enqueue(self, run: ScriptQualificationRun | None, *, due_at: datetime, event_type: str, command_id: str, background_attempt_id: uuid.UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        if run is None:
            return
        if self.session.scalar(select(DomainEvent).where(DomainEvent.command_id == command_id)) is not None:
            return
        payload = {"script_qualification_run_id": str(run.id), "background_attempt_id": str(background_attempt_id) if background_attempt_id else None}
        self.session.add(DomainEvent(id=uuid.uuid5(uuid.NAMESPACE_URL, command_id), event_type=event_type, event_version=1, aggregate_type="script_qualification_run", aggregate_id=run.id, company_id=None, channel_workspace_id=None, workflow_run_id=None, correlation_id=command_id[:160], command_id=command_id, payload_hash=content_hash(payload), payload=payload, metadata_={"queue_name": "production-workflow", "retry_policy": {"automatic_retry_allowed": True}, **(metadata or {})}, attempt_count=0, max_attempts=3, next_attempt_at=due_at, occurred_at=self.now()))

    def _recovery_guards_pass(self, run: ScriptQualificationRun, *, phase: str) -> bool:
        """Apply the current readiness gates before the one permitted submit."""

        if run.supersedes_qualification_run_id is None:
            return True
        candidate = self.session.get(EditorialIdeaCandidate, run.editorial_idea_candidate_id)
        slot = self.session.get(LongFormPublishSlot, run.publish_slot_id)
        # The explicit current topic receipt is intentionally queried by the
        # frozen Topic Definition id; TopicDefinitionService would treat this
        # in-flight child as a recursive pending qualification.
        from app.db.models.script_qualification import EditorialTopicDefinitionGateReceipt
        topic = self.session.scalar(select(EditorialTopicDefinitionGateReceipt).where(
            EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id == run.topic_definition_id
        ).order_by(EditorialTopicDefinitionGateReceipt.created_at.desc()))
        preflight = self.session.scalar(select(IdeaMarketPreflight).where(
            IdeaMarketPreflight.editorial_idea_candidate_id == run.editorial_idea_candidate_id
        ).order_by(IdeaMarketPreflight.created_at.desc()))
        from app.services.production_start_readiness import resolve_budget_authority

        budget = (
            resolve_budget_authority(
                self.session,
                policy_snapshot_id=candidate.policy_snapshot_id,
                channel_workspace_id=candidate.channel_workspace_id,
            )
            if candidate is not None
            else {"state": "BLOCKED"}
        )
        now = self.now()
        reasons: list[str] = []
        if candidate is None or candidate.stage != "GREENLIT":
            reasons.append("RECOVERY_CANDIDATE_NOT_CURRENT_ELIGIBLE")
        if topic is None or not topic.current_production_eligibility or topic.state != "PASS":
            reasons.append("RECOVERY_TOPIC_DEFINITION_NOT_PASS")
        if preflight is None or preflight.decision != "PASS" or preflight.policy_fit_state != "PASS":
            reasons.append("RECOVERY_PREFLIGHT_NOT_PASS")
        if (
            slot is None
            or slot.reserved_candidate_id != run.editorial_idea_candidate_id
            or slot.state != "QUALIFICATION_RESERVED"
            or not (slot.target_start_window_open_at <= now <= slot.target_start_window_close_at)
        ):
            reasons.append("RECOVERY_PRODUCTION_WINDOW_NOT_OPEN")
        if budget.get("state") != "READY":
            reasons.append("RECOVERY_BUDGET_AUTHORITY_BLOCKED")
        attempts = self.session.scalars(select(ScriptQualificationBackgroundAttempt).where(
            ScriptQualificationBackgroundAttempt.script_qualification_run_id == run.id,
            ScriptQualificationBackgroundAttempt.phase == phase,
            ScriptQualificationBackgroundAttempt.provider_response_id.is_not(None),
        )).all()
        if attempts:
            reasons.append("RECOVERY_BACKGROUND_RESPONSE_ALREADY_EXISTS")
        if not reasons:
            return True
        unknown_provider_outcome = any(
            self._attempt_provider_outcome_is_unknown(attempt)
            for attempt in attempts
        )
        if unknown_provider_outcome:
            reasons.append("SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY")
        run.state = "BLOCKED_NON_REPAIRABLE"
        run.failure_receipt = {
            "reason_codes": reasons,
            "logical_identity_hash": run.logical_identity_hash,
            "recovery_guarded_at": now.isoformat(),
        }
        self._settle_terminal_if_current(
            run,
            reason_code=reasons[0],
            unknown_provider_outcome=unknown_provider_outcome,
        )
        return False

    @staticmethod
    def _status(value: Any) -> str:
        raw = str(value or "queued").upper()
        return {"QUEUED": "QUEUED", "IN_PROGRESS": "IN_PROGRESS", "COMPLETED": "COMPLETED", "FAILED": "FAILED", "CANCELLED": "CANCELLED", "INCOMPLETE": "INCOMPLETE"}.get(raw, "SUBMITTED")

    @staticmethod
    def _hashes(run: ScriptQualificationRun) -> dict[str, Any]:
        return {"topic_definition_hash": run.topic_definition_hash, "script_assignment_hash": run.script_assignment_hash, "factual_evidence_pack_hash": run.factual_evidence_pack_hash, "memory_digest_hash": run.memory_digest_hash, "runtime_contract_hash": run.runtime_contract_hash, "assignment_resolution_hash": run.assignment_resolution_hash, "script_contract_version": run.script_contract_version, "replacement_authority_id": str(run.replacement_authority_id) if run.replacement_authority_id else None}

    def _deadline_for(self, run: ScriptQualificationRun, *, requested_at: datetime) -> datetime:
        slot = self.session.get(LongFormPublishSlot, run.publish_slot_id)
        if slot is None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SLOT_NOT_FOUND")
        # The requested time remains explicit for recovery identity. Deadline
        # authority comes from the slot close after preserving downstream lead.
        _ = requested_at
        return derive_script_qualification_deadline(slot.target_start_window_close_at, self.deadline_policy)

    @staticmethod
    def _attempt_provider_outcome_is_unknown(attempt: ScriptQualificationBackgroundAttempt) -> bool:
        if attempt.provider_outcome in {
            "PROVIDER_REQUEST_REJECTED_NO_RESPONSE",
            "SCRIPT_PROVIDER_REQUEST_REJECTED_NO_RESPONSE",
        }:
            return False
        if attempt.background_status in {"COMPLETED", "FAILED", "CANCELLED", "INCOMPLETE"}:
            return False
        return bool(
            attempt.provider_response_id
            or attempt.submission_attempt_count > 0
            or attempt.background_status == "SUBMISSION_OUTCOME_UNKNOWN"
        )

    def _settle_terminal_if_current(
        self,
        run: ScriptQualificationRun,
        *,
        reason_code: str,
        unknown_provider_outcome: bool,
    ) -> None:
        """Settle only the run that still owns the current reserved lineage."""

        if run.terminal_settlement_receipt is None:
            slot = self.session.scalar(select(LongFormPublishSlot).where(LongFormPublishSlot.id == run.publish_slot_id).with_for_update())
            candidate = self.session.scalar(select(EditorialIdeaCandidate).where(EditorialIdeaCandidate.id == run.editorial_idea_candidate_id).with_for_update())
            latest = self.session.scalar(
                select(ScriptQualificationRun)
                .where(
                    ScriptQualificationRun.publish_slot_id == run.publish_slot_id,
                    ScriptQualificationRun.editorial_idea_candidate_id == run.editorial_idea_candidate_id,
                )
                .order_by(
                    ScriptQualificationRun.logical_attempt_number.desc(),
                    ScriptQualificationRun.created_at.desc(),
                    ScriptQualificationRun.id.desc(),
                )
                .with_for_update()
            )
            if (
                slot is None
                or candidate is None
                or latest is None
                or latest.id != run.id
                or slot.reserved_candidate_id != candidate.id
                or slot.admitted_video_project_id is not None
                or run.admitted_video_project_id is not None
                or candidate.stage in {"IN_PRODUCTION", "FINAL_REVIEW_READY", "PUBLISHED"}
            ):
                return

        from app.services.script_qualification_recovery import ScriptQualificationRecoveryService

        recovery = ScriptQualificationRecoveryService(self.session, now=self.now)
        if unknown_provider_outcome:
            recovery.settle_unknown_provider_outcome(run)
        else:
            recovery.settle_deterministic_block(run, reason_code=reason_code)

    def _writer_context(self, run: ScriptQualificationRun) -> dict[str, Any]:
        from app.services.script_qualification import ScriptQualificationService
        context = ScriptQualificationService(self.session)._writer_context(run)
        from app.services.script_content_repair import ScriptContentRepairService

        repair = ScriptContentRepairService(self.session, now=self.now).writer_context(run)
        if repair is not None:
            context["content_repair"] = repair
        return context

    def _verifier_context(self, run: ScriptQualificationRun) -> dict[str, Any]:
        from app.services.script_qualification import ScriptQualificationService
        service = ScriptQualificationService(self.session)
        return service._verifier_context(run, service.draft_from_run(run))

    def _response_schema(
        self, run: ScriptQualificationRun, *, phase: str
    ) -> tuple[str, dict[str, Any]]:
        model = (
            (QualifiedScriptOutputV2 if run.script_contract_version == "V2_SINGLE_SOURCE" else QualifiedScriptOutput)
            if phase == "WRITER"
            else SemanticVerificationOutput
        )
        schema = _strict_json_schema(model.model_json_schema())
        if phase == "WRITER":
            runtime = run.runtime_contract if isinstance(run.runtime_contract, dict) else {}
            schema["properties"]["sections"]["minItems"] = max(
                1, int(runtime.get("minimum_major_sections") or 1)
            )
            schema["properties"]["claims"]["minItems"] = max(
                1, int(runtime.get("minimum_material_claims") or 1)
            )
            return (
                "vcos_qualified_script_output_v2_single_source"
                if run.script_contract_version == "V2_SINGLE_SOURCE"
                else "vcos_qualified_script_output_v1",
                schema,
            )
        return "vcos_semantic_verification_output_v2", schema

    @staticmethod
    def _schema_bound_prompt(
        *, context: dict[str, Any], phase: str, schema_identifier: str, prompt_version: str
    ) -> str:
        if phase == "WRITER":
            if schema_identifier == "vcos_qualified_script_output_v2_single_source":
                instruction = (
                    "Return only the strict schema-bound QualifiedScriptOutputV2. "
                    "Author narration only in sections[].narration. Do not return canonical_script and do not mirror the full script in any field. "
                    "Every narration sentence belongs to exactly one section; ordinals are contiguous and section IDs are unique. "
                    "Use claim_text and evidence_span_ids exactly. Do not add undocumented fields."
                )
            else:
                instruction = (
                    "Return only the strict schema-bound QualifiedScriptOutput. "
                    "canonical_script is a string, never an object; sections and claims are top-level arrays. "
                    "Do not put a title or sections inside canonical_script. Use claim_text and evidence_span_ids exactly. "
                    "Do not add undocumented fields."
                )
            if context.get("content_repair"):
                instruction += (
                    " This is the one authorized SCRIPT_CONTENT_REPAIR. Follow content_repair exactly: "
                    "preserve all untouched sections verbatim and repair only the listed sections and their claim bindings."
                )
        else:
            instruction = (
                "Return only the strict schema-bound SemanticVerificationOutput. "
                "For each span, emit its exact text and section_id only; VCOS derives UTF-8 offsets and hashes locally. "
                "The selected text must occur exactly once in that section. Include every top-level field and emit no final PASS; "
                "deterministic aggregation owns the final decision."
            )
        return json.dumps(
            {
                "prompt_version": prompt_version,
                "response_schema_identifier": schema_identifier,
                "instruction": instruction,
                "frozen_context": context,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _persist_response_snapshot(
        self,
        *,
        run: ScriptQualificationRun,
        attempt: ScriptQualificationBackgroundAttempt,
        output: dict[str, Any],
        accepted_typed_output_hash: str | None = None,
        validation_errors: list[dict[str, Any]] | None = None,
    ) -> ScriptQualificationProviderResponseSnapshot:
        existing = self.session.scalar(
            select(ScriptQualificationProviderResponseSnapshot).where(
                ScriptQualificationProviderResponseSnapshot.background_attempt_id == attempt.id
            )
        )
        if existing is not None:
            return existing
        raw = output.get("raw")
        if not isinstance(raw, dict) or not attempt.provider_response_id:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RESPONSE_SNAPSHOT_INPUT_INVALID")
        content = str(output.get("content") or "")
        snapshot = ScriptQualificationProviderResponseSnapshot(
            script_qualification_run_id=run.id,
            background_attempt_id=attempt.id,
            phase=attempt.phase,
            provider_response_id=attempt.provider_response_id,
            provider_request_id=attempt.provider_request_id,
            raw_provider_response=raw,
            raw_provider_response_hash=content_hash(raw),
            raw_output_content=content,
            raw_output_hash=content_hash({"content": content}),
            usage=output.get("usage") if isinstance(output.get("usage"), dict) else None,
            response_schema_identifier=attempt.response_schema_identifier or "legacy-unbound-json-schema",
            response_schema_hash=attempt.response_schema_hash,
            prompt_version=(
                attempt.prompt_version
                or (
                    run.writer_prompt_version
                    if attempt.phase == "WRITER"
                    else run.verifier_prompt_version
                )
            ),
            producer_input_hash=attempt.input_fingerprint,
            accepted_typed_output_hash=accepted_typed_output_hash,
            validation_errors=validation_errors or [],
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    @staticmethod
    def _receipt(attempt: ScriptQualificationBackgroundAttempt, output: dict[str, Any]) -> dict[str, Any]:
        return {"provider": attempt.provider, "model": attempt.model, "lane_name": attempt.lane, "task": attempt.task, "provider_response_id": attempt.provider_response_id, "provider_request_id": attempt.provider_request_id, "input_fingerprint": attempt.input_fingerprint, "output_hash": attempt.output_hash, "usage": output.get("usage"), "background": True, "response_schema_identifier": attempt.response_schema_identifier, "response_schema_hash": attempt.response_schema_hash, "prompt_version": attempt.prompt_version}


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON Schema compatible with Responses strict output."""

    copied = json.loads(json.dumps(schema))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            # Defaults are rejected by OpenAI strict Structured Outputs. The
            # field is still required and nullable fields retain their explicit
            # null branch, so removing a default does not weaken validation.
            node.pop("default", None)
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(copied)
    return copied


def _validation_errors(exc: Exception) -> list[dict[str, Any]]:
    if hasattr(exc, "errors"):
        try:
            return [dict(item) for item in exc.errors(include_url=False)]
        except TypeError:  # pragma: no cover - old Pydantic compatibility
            return [dict(item) for item in exc.errors()]
    return [{"type": type(exc).__name__, "msg": str(exc), "loc": []}]
