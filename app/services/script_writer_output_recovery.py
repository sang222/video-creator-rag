"""Durable, no-writer recovery from one completed malformed writer response."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.script_qualification import QualifiedScriptOutput
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.script_qualification import (
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationRun,
    ScriptWriterOutputNormalizationReceipt,
)
from app.providers.openai import OpenAIResponsesProvider
from app.services.config_registry import content_hash
from app.services.script_qualification_background import BACKGROUND_EVENT_TYPE
from app.services.script_writer_output_normalization import (
    CONTRACT_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    WriterOutputNormalizationError,
    normalize_legacy_writer_output,
    validation_errors,
)


NORMALIZED_RECOVERY_PREFIX = "script-qualification-normalized-response-recovery"
VERIFIER_SCHEMA_RECOVERY_PREFIX = "script-qualification-verifier-schema-recovery"
VERIFIER_SCHEMA_RECOVERY_POLICY_VERSION = "strict-schema-no-default-or-open-object.v1"


class ScriptWriterOutputRecoveryService:
    """Create an auditable post-writer boundary without a second writer call."""

    def __init__(
        self,
        session: Session,
        *,
        now=utc_now,
        provider: OpenAIResponsesProvider | None = None,
    ) -> None:
        self.session = session
        self.now = now
        settings = get_settings()
        self.provider = provider or OpenAIResponsesProvider(
            api_key=(
                settings.openai_api_key.get_secret_value()
                if settings.openai_api_key
                else None
            ),
            base_url=settings.openai_base_url,
            timeout_seconds=settings.openai_timeout_seconds,
            runtime_origin="script-writer-output-normalization",
        )
        self.settings = settings

    def normalize_completed_writer_response(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptWriterOutputNormalizationReceipt:
        """Read the provider's durable response once and seal its exact mapping."""

        existing = self.session.scalar(
            select(ScriptWriterOutputNormalizationReceipt).where(
                ScriptWriterOutputNormalizationReceipt.source_qualification_run_id
                == source_qualification_run_id
            )
        )
        if existing is not None:
            return existing
        source, attempt = self._source(source_qualification_run_id)

        snapshot = self.session.scalar(
            select(ScriptQualificationProviderResponseSnapshot).where(
                ScriptQualificationProviderResponseSnapshot.background_attempt_id
                == attempt.id
            )
        )
        if snapshot is None:
            # A GET is safe to repeat. There is no writer submission here and
            # the persisted response id is the sole remote authority. This is
            # only needed when the historic poll did not persist a snapshot.
            self.session.commit()
            response = self.provider.retrieve_background(
                response_id=attempt.provider_response_id,
                timeout_seconds=self.settings.openai_background_poll_request_timeout_seconds,
            )
            if not response.ok:
                raise ValidationFailureError(
                    "SCRIPT_WRITER_NORMALIZATION_RESPONSE_RETRIEVAL_FAILED:"
                    + str(response.error_code or "UNKNOWN")
                )
            source, attempt = self._source(source_qualification_run_id)
            raw_response = response.output.get("raw")
            raw_content = response.output.get("content")
            usage = response.output.get("usage")
            if not isinstance(raw_response, dict) or not isinstance(raw_content, str):
                raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_RESPONSE_INVALID")
        else:
            raw_response = snapshot.raw_provider_response
            raw_content = snapshot.raw_output_content
            usage = snapshot.usage
        if attempt.output_hash != content_hash(raw_response):
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_OUTPUT_HASH_MISMATCH")
        try:
            raw_output = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_JSON_INVALID") from exc

        source_errors = validation_errors(raw_output)
        if snapshot is None:
            snapshot = self._snapshot(
                source=source,
                attempt=attempt,
                raw_response=raw_response,
                raw_content=raw_content,
                usage=usage,
                source_validation_errors=source_errors,
                accepted_typed_output_hash=None,
            )
        elif snapshot.validation_errors != source_errors:
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_SNAPSHOT_VALIDATION_DRIFT")
        try:
            normalized = normalize_legacy_writer_output(raw_output)
        except WriterOutputNormalizationError as exc:
            raise ValidationFailureError(str(exc)) from exc
        typed = QualifiedScriptOutput.model_validate(normalized.payload)
        structural = self._structural_gate(source, typed)
        if structural.get("status") != "PASS":
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_GATE_A_BLOCKED:"
                + ",".join(structural.get("reason_codes") or [])
            )

        body = {
            "schema_version": "script-writer-output-normalization-receipt.v1",
            "source_qualification_run_id": str(source.id),
            "source_background_attempt_id": str(attempt.id),
            "source_provider_response_snapshot_id": str(snapshot.id),
            "source_provider_response_id": attempt.provider_response_id,
            "source_provider_request_id": attempt.provider_request_id,
            "source_raw_output_hash": snapshot.raw_output_hash,
            "source_schema_classification": normalized.classification,
            "normalization_version": NORMALIZATION_VERSION,
            "field_mapping": normalized.field_mapping,
            "removed_wrapper_fields": normalized.removed_wrapper_fields,
            "normalized_payload": typed.model_dump(mode="json"),
            "normalized_payload_hash": content_hash(typed.model_dump(mode="json")),
            "contract_schema_version": CONTRACT_SCHEMA_VERSION,
            "validation_result": {
                "source_contract_errors": source_errors,
                "normalized_contract_valid": True,
                "gate_a": structural,
            },
            "actor": "system:script-writer-output-normalization",
            "reason_codes": normalized.reason_codes,
        }
        receipt = ScriptWriterOutputNormalizationReceipt(
            source_qualification_run_id=source.id,
            source_background_attempt_id=attempt.id,
            source_provider_response_snapshot_id=snapshot.id,
            source_provider_response_id=attempt.provider_response_id,
            source_provider_request_id=attempt.provider_request_id,
            source_raw_output_hash=snapshot.raw_output_hash,
            source_schema_classification=normalized.classification,
            normalization_version=NORMALIZATION_VERSION,
            field_mapping=normalized.field_mapping,
            removed_wrapper_fields=normalized.removed_wrapper_fields,
            normalized_payload=typed.model_dump(mode="json"),
            normalized_payload_hash=body["normalized_payload_hash"],
            contract_schema_version=CONTRACT_SCHEMA_VERSION,
            validation_result=body["validation_result"],
            actor=body["actor"],
            reason_codes=body["reason_codes"],
            receipt_hash=content_hash(body),
        )
        self.session.add(receipt)
        self.session.flush()
        return receipt

    def create_normalized_recovery(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Create the third lineage node at the deterministic post-writer edge."""

        receipt = self.normalize_completed_writer_response(
            source_qualification_run_id=source_qualification_run_id
        )
        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if source is None:
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_SOURCE_MISSING")
        if source.state != "BLOCKED_NON_REPAIRABLE":
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_SOURCE_NOT_BLOCKED")
        payload = QualifiedScriptOutput.model_validate(receipt.normalized_payload)
        structural = self._structural_gate(source, payload)
        if structural.get("status") != "PASS":
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_GATE_A_RECHECK_FAILED")
        recovery_key = f"{NORMALIZED_RECOVERY_PREFIX}:{source.id}:{receipt.id}"
        existing = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.recovery_key == recovery_key)
            .with_for_update()
        )
        if existing is not None:
            return existing

        identity = content_hash(
            {
                "source_logical_identity_hash": source.logical_identity_hash,
                "normalization_receipt_hash": receipt.receipt_hash,
                "recovery_key": recovery_key,
            }
        )
        child = ScriptQualificationRun(
            editorial_idea_candidate_id=source.editorial_idea_candidate_id,
            publish_slot_id=source.publish_slot_id,
            launch_run_id=source.launch_run_id,
            topic_definition_id=source.topic_definition_id,
            topic_definition_hash=source.topic_definition_hash,
            script_assignment=source.script_assignment,
            script_assignment_hash=source.script_assignment_hash,
            factual_evidence_pack=source.factual_evidence_pack,
            factual_evidence_pack_hash=source.factual_evidence_pack_hash,
            memory_digest=source.memory_digest,
            memory_digest_hash=source.memory_digest_hash,
            runtime_contract=source.runtime_contract,
            runtime_contract_hash=source.runtime_contract_hash,
            assignment_resolution=source.assignment_resolution,
            assignment_resolution_hash=source.assignment_resolution_hash,
            episode_reservation_active=source.episode_reservation_active,
            # This answer was produced under the frozen legacy prompt. The v3
            # prompt/schema applies to future writer submissions only.
            writer_prompt_version=source.writer_prompt_version,
            verifier_prompt_version="script-semantic-verifier.v3",
            gate_policy_version=source.gate_policy_version,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=identity,
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=self.now(),
            logical_deadline_at=source.logical_deadline_at,
            state="SCRIPT_GENERATED",
            writer_attempt_key=f"{recovery_key}:normalized-completed-response",
            verifier_attempt_key=f"{recovery_key}:verifier",
            script_payload=payload.model_dump(mode="json"),
            writer_receipt={
                "producer": "NORMALIZED_COMPLETED_BACKGROUND_RESPONSE",
                "background": True,
                "provider": "OPENAI",
                "model": source.model,
                "source_qualification_run_id": str(source.id),
                "source_provider_response_id": receipt.source_provider_response_id,
                "source_provider_request_id": receipt.source_provider_request_id,
                "source_raw_output_hash": receipt.source_raw_output_hash,
                "normalization_receipt_id": str(receipt.id),
                "normalization_receipt_hash": receipt.receipt_hash,
                "producer_input_hash": source.writer_attempt_key,
                "producer_output_hash": receipt.normalized_payload_hash,
                "prompt_version": source.writer_prompt_version,
                "writer_submission_count_for_new_recovery": 0,
                "reused_completed_writer_response_count": 1,
            },
        )
        self.session.add(child)
        self.session.flush()
        self._enqueue_verifier(child)
        return child

    def continue_after_confirmed_verifier_schema_rejection(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Append a recovery only after an HTTP 400 created no response id.

        The failed run remains terminal. A replacement retains the exact
        canonical script and frozen editorial authorities, changes no content,
        and is allowed one fresh verifier submission using the repaired strict
        response schema.
        """

        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if (
            source is None
            or source.state != "BLOCKED_NON_REPAIRABLE"
            or source.script_payload is None
        ):
            raise ValidationFailureError("VERIFIER_SCHEMA_RECOVERY_SOURCE_NOT_TERMINAL")
        failed_attempt = self.session.scalar(
            select(ScriptQualificationBackgroundAttempt)
            .where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == source.id,
                ScriptQualificationBackgroundAttempt.phase == "VERIFIER",
            )
            .with_for_update()
        )
        if (
            failed_attempt is None
            or failed_attempt.provider_response_id is not None
            or failed_attempt.submission_attempt_count != 1
            or failed_attempt.last_network_error != "OPENAI_INVALID_REQUEST"
        ):
            raise ValidationFailureError("VERIFIER_SCHEMA_RECOVERY_NO_CONFIRMED_HTTP_REJECTION")
        recovery_key = (
            f"{VERIFIER_SCHEMA_RECOVERY_PREFIX}:{source.id}:"
            f"{VERIFIER_SCHEMA_RECOVERY_POLICY_VERSION}"
        )
        existing = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.recovery_key == recovery_key)
            .with_for_update()
        )
        if existing is not None:
            return existing

        reconciliation = {
            "schema_version": "script-qualification-verifier-schema-recovery.v1",
            "source_qualification_run_id": str(source.id),
            "failed_background_attempt_id": str(failed_attempt.id),
            "phase": "VERIFIER",
            "provider_response_id": None,
            "provider_effect": "NONE_CONFIRMED_BY_HTTP_REJECTION",
            "provider_error_code": failed_attempt.last_network_error,
            "source_schema_identifier": failed_attempt.response_schema_identifier,
            "source_schema_hash": failed_attempt.response_schema_hash,
            "local_root_cause": "STRICT_SCHEMA_DEFAULT_OR_OPEN_OBJECT",
            "recovery_policy_version": VERIFIER_SCHEMA_RECOVERY_POLICY_VERSION,
            "recovery_key": recovery_key,
            "content_changed": False,
            "created_at": self.now().isoformat(),
        }
        reconciliation["content_hash"] = content_hash(reconciliation)
        source.provider_outcome_reconciliation_receipts = [
            *(source.provider_outcome_reconciliation_receipts or []),
            reconciliation,
        ]

        child = ScriptQualificationRun(
            editorial_idea_candidate_id=source.editorial_idea_candidate_id,
            publish_slot_id=source.publish_slot_id,
            launch_run_id=source.launch_run_id,
            topic_definition_id=source.topic_definition_id,
            topic_definition_hash=source.topic_definition_hash,
            script_assignment=source.script_assignment,
            script_assignment_hash=source.script_assignment_hash,
            factual_evidence_pack=source.factual_evidence_pack,
            factual_evidence_pack_hash=source.factual_evidence_pack_hash,
            memory_digest=source.memory_digest,
            memory_digest_hash=source.memory_digest_hash,
            runtime_contract=source.runtime_contract,
            runtime_contract_hash=source.runtime_contract_hash,
            assignment_resolution=source.assignment_resolution,
            assignment_resolution_hash=source.assignment_resolution_hash,
            episode_reservation_active=source.episode_reservation_active,
            writer_prompt_version=source.writer_prompt_version,
            verifier_prompt_version=source.verifier_prompt_version,
            gate_policy_version=source.gate_policy_version,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=content_hash(
                {
                    "source_logical_identity_hash": source.logical_identity_hash,
                    "reconciliation_hash": reconciliation["content_hash"],
                    "recovery_key": recovery_key,
                }
            ),
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=self.now(),
            logical_deadline_at=source.logical_deadline_at,
            state="SCRIPT_GENERATED",
            writer_attempt_key=f"{recovery_key}:reused-canonical-script",
            verifier_attempt_key=f"{recovery_key}:verifier",
            script_payload=source.script_payload,
            writer_receipt={
                **(source.writer_receipt or {}),
                "producer": "REUSED_CANONICAL_SCRIPT_AFTER_VERIFIER_SCHEMA_REPAIR",
                "source_qualification_run_id": str(source.id),
                "source_writer_receipt_hash": content_hash(source.writer_receipt or {}),
                "verifier_schema_recovery_receipt_hash": reconciliation["content_hash"],
                "writer_submission_count_for_new_recovery": 0,
                "reused_completed_writer_response_count": 1,
            },
        )
        self.session.add(child)
        self.session.flush()
        self._enqueue_verifier(child)
        return child

    def _source(
        self, source_qualification_run_id: uuid.UUID
    ) -> tuple[ScriptQualificationRun, ScriptQualificationBackgroundAttempt]:
        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if source is None or source.state != "BLOCKED_NON_REPAIRABLE":
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_SOURCE_NOT_TERMINAL")
        attempt = self.session.scalar(
            select(ScriptQualificationBackgroundAttempt)
            .where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == source.id,
                ScriptQualificationBackgroundAttempt.phase == "WRITER",
            )
            .with_for_update()
        )
        if (
            attempt is None
            or attempt.background_status != "COMPLETED"
            or not attempt.provider_response_id
            or attempt.submission_attempt_count != 1
        ):
            raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_RESPONSE_NOT_REUSABLE")
        return source, attempt

    def _snapshot(
        self,
        *,
        source: ScriptQualificationRun,
        attempt: ScriptQualificationBackgroundAttempt,
        raw_response: dict[str, Any],
        raw_content: str,
        usage: Any,
        source_validation_errors: list[dict[str, Any]],
        accepted_typed_output_hash: str,
    ) -> ScriptQualificationProviderResponseSnapshot:
        existing = self.session.scalar(
            select(ScriptQualificationProviderResponseSnapshot).where(
                ScriptQualificationProviderResponseSnapshot.background_attempt_id
                == attempt.id
            )
        )
        if existing is not None:
            if existing.raw_provider_response_hash != content_hash(raw_response):
                raise ValidationFailureError("SCRIPT_WRITER_NORMALIZATION_SNAPSHOT_DRIFT")
            return existing
        snapshot = ScriptQualificationProviderResponseSnapshot(
            script_qualification_run_id=source.id,
            background_attempt_id=attempt.id,
            phase="WRITER",
            provider_response_id=attempt.provider_response_id,
            provider_request_id=attempt.provider_request_id,
            raw_provider_response=raw_response,
            raw_provider_response_hash=content_hash(raw_response),
            raw_output_content=raw_content,
            raw_output_hash=content_hash({"content": raw_content}),
            usage=usage if isinstance(usage, dict) else None,
            response_schema_identifier="legacy-unbound-json-schema",
            response_schema_hash=None,
            prompt_version=source.writer_prompt_version,
            producer_input_hash=attempt.input_fingerprint,
            accepted_typed_output_hash=accepted_typed_output_hash,
            validation_errors=source_validation_errors,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def _structural_gate(
        self, source: ScriptQualificationRun, payload: QualifiedScriptOutput
    ) -> dict[str, Any]:
        from app.services.script_qualification import ScriptQualificationService

        return ScriptQualificationService(self.session)._structural_receipt(source, payload)

    def _enqueue_verifier(self, run: ScriptQualificationRun) -> None:
        command_id = f"{run.recovery_key}:verifier-post-normalization"
        existing = self.session.scalar(
            select(DomainEvent).where(DomainEvent.command_id == command_id)
        )
        if existing is not None:
            return
        payload = {
            "script_qualification_run_id": str(run.id),
            "background_attempt_id": None,
            "recovery_mode": "NORMALIZED_COMPLETED_WRITER_RESPONSE",
        }
        self.session.add(
            DomainEvent(
                id=uuid.uuid5(uuid.NAMESPACE_URL, command_id),
                event_type=BACKGROUND_EVENT_TYPE,
                event_version=1,
                aggregate_type="script_qualification_run",
                aggregate_id=run.id,
                company_id=None,
                channel_workspace_id=None,
                workflow_run_id=None,
                correlation_id=command_id[:160],
                command_id=command_id,
                payload_hash=content_hash(payload),
                payload=payload,
                metadata_={
                    "queue_name": "production-workflow",
                    "retry_policy": {"automatic_retry_allowed": True},
                    "writer_submission_count_for_new_recovery": 0,
                    "reused_completed_writer_response_count": 1,
                    "verifier_submission_limit": 1,
                },
                attempt_count=0,
                max_attempts=3,
                next_attempt_at=self.now(),
                occurred_at=self.now(),
            )
        )
        self.session.flush()
