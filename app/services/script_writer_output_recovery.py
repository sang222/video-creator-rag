"""Durable, no-writer recovery from one completed malformed writer response."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.script_qualification import (
    QualifiedScriptOutput,
    QualifiedScriptOutputV2,
)
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import (
    FirstChannelLaunchPolicyVersion,
    LaunchRun,
    LongFormPublishSlot,
)
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.script_qualification import (
    ControlledProductionContinuationAuthority,
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    SeriesEpisodeReservation,
    ScriptContentRepairAuthorizationReceipt,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationRun,
    ScriptContractReplacementAuthority,
    ScriptWriterOutputNormalizationReceipt,
)
from app.providers.openai import OpenAIResponsesProvider
from app.services.config_registry import content_hash
from app.services.editorial_novelty import (
    EDITORIAL_NOVELTY_GATE_VERSION,
    EditorialNoveltyService,
)
from app.services.editorial_specificity import EditorialSpecificityService
from app.services.launch_cadence import _preflight_demand_authority_valid
from app.services.script_qualification_background import (
    BACKGROUND_EVENT_TYPE,
    build_script_qualification_deadline_policy,
    derive_script_qualification_deadline,
    minimum_script_qualification_window_close_at,
    script_qualification_slot_is_viable,
)
from app.services.script_contract_replacement import (
    CONTROLLED_CONTINUATION_REASON,
    CONTROLLED_CONTINUATION_SCHEMA,
    OPERATOR_RECOVERY_REASON,
    OPERATOR_RECOVERY_SCHEMA,
    REPAIR_POLICY_REF,
    ScriptContractReplacementAuthorityService,
    controlled_continuation_authority_body,
    controlled_continuation_slot_projection,
    operator_recovery_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.production_start_readiness import (
    resolve_budget_authority,
    resolve_provider_authority,
)
from app.services.script_qualification_authority import validate_memory_digest
from app.services.script_writer_output_normalization import (
    CONTRACT_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    V2_CONTRACT_SCHEMA_VERSION,
    V2_OWNERSHIP_NORMALIZATION_VERSION,
    WriterOutputNormalizationError,
    normalize_legacy_writer_output,
    normalize_v2_section_ownership,
    validation_errors,
)


NORMALIZED_RECOVERY_PREFIX = "script-qualification-normalized-response-recovery"
VERIFIER_SCHEMA_RECOVERY_PREFIX = "script-qualification-verifier-schema-recovery"
VERIFIER_SCHEMA_RECOVERY_POLICY_VERSION = "strict-schema-no-default-or-open-object.v1"
V2_OWNERSHIP_RECOVERY_PREFIX = "script-qualification-v2-ownership-normalization"
V2_OWNERSHIP_FAILURE = "SCRIPT_SECTION_COVERAGE_REQUIREMENT_OWNERSHIP_VIOLATION"
V2_OWNERSHIP_RECEIPT_SCHEMA = "script-writer-v2-ownership-normalization-receipt.v1"
V2_OWNERSHIP_COMPENSATION_SCHEMA = "script-writer-v2-ownership-compensation.v1"
V2_OWNERSHIP_ACTOR = "system:script-writer-v2-ownership-normalization"
VERIFIER_AUTH_REJECTION_PREFIX = "script-qualification-verifier-auth-rejection"
CONTENT_REPAIR_SCOPE_RECOVERY_PREFIX = (
    "script-qualification-content-repair-scope-reclassification"
)


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
                raise ValidationFailureError(
                    "SCRIPT_WRITER_NORMALIZATION_RESPONSE_INVALID"
                )
        else:
            raw_response = snapshot.raw_provider_response
            raw_content = snapshot.raw_output_content
            usage = snapshot.usage
        if attempt.output_hash != content_hash(raw_response):
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_OUTPUT_HASH_MISMATCH"
            )
        try:
            raw_output = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_JSON_INVALID"
            ) from exc

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
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_SNAPSHOT_VALIDATION_DRIFT"
            )
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
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_SOURCE_NOT_BLOCKED"
            )
        payload = QualifiedScriptOutput.model_validate(receipt.normalized_payload)
        structural = self._structural_gate(source, payload)
        if structural.get("status") != "PASS":
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_GATE_A_RECHECK_FAILED"
            )
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

    def create_v2_ownership_normalized_recovery(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Reuse one completed V2 writer response after an exact ref projection.

        The provider-authored narration, claims, evidence IDs, purposes,
        ordinals, and section IDs remain byte-equivalent. Only a field whose
        observed values equal the frozen section's owned information-unit IDs
        is projected to that section's frozen primary requirement IDs.
        """

        source, attempt, snapshot = self._v2_ownership_source(
            source_qualification_run_id
        )
        existing_receipt = self.session.scalar(
            select(ScriptWriterOutputNormalizationReceipt)
            .where(
                ScriptWriterOutputNormalizationReceipt.source_qualification_run_id
                == source.id
            )
            .with_for_update()
        )
        if existing_receipt is not None:
            receipt = self._validate_v2_ownership_receipt(
                existing_receipt,
                source=source,
                attempt=attempt,
                snapshot=snapshot,
            )
        else:
            try:
                raw_output = json.loads(snapshot.raw_output_content)
                normalized = normalize_v2_section_ownership(
                    raw_output,
                    (source.script_assignment or {}).get("section_coverage_plan"),
                )
            except (json.JSONDecodeError, WriterOutputNormalizationError) as exc:
                raise ValidationFailureError(str(exc)) from exc
            typed_payload = QualifiedScriptOutputV2.model_validate(
                normalized.payload
            ).model_dump(mode="json")
            validation_result = {
                "source_contract_valid": True,
                "normalized_contract_valid": True,
                "coverage_contract_valid": True,
                "narration_changed": False,
                "claims_changed": False,
            }
            body = {
                "schema_version": V2_OWNERSHIP_RECEIPT_SCHEMA,
                "source_qualification_run_id": str(source.id),
                "source_background_attempt_id": str(attempt.id),
                "source_provider_response_snapshot_id": str(snapshot.id),
                "source_provider_response_id": attempt.provider_response_id,
                "source_provider_request_id": attempt.provider_request_id,
                "source_raw_output_hash": snapshot.raw_output_hash,
                "source_schema_classification": normalized.classification,
                "normalization_version": V2_OWNERSHIP_NORMALIZATION_VERSION,
                "field_mapping": normalized.field_mapping,
                "removed_wrapper_fields": normalized.removed_wrapper_fields,
                "normalized_payload": typed_payload,
                "normalized_payload_hash": content_hash(typed_payload),
                "contract_schema_version": V2_CONTRACT_SCHEMA_VERSION,
                "validation_result": validation_result,
                "actor": V2_OWNERSHIP_ACTOR,
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
                normalization_version=V2_OWNERSHIP_NORMALIZATION_VERSION,
                field_mapping=normalized.field_mapping,
                removed_wrapper_fields=normalized.removed_wrapper_fields,
                normalized_payload=typed_payload,
                normalized_payload_hash=body["normalized_payload_hash"],
                contract_schema_version=V2_CONTRACT_SCHEMA_VERSION,
                validation_result=validation_result,
                actor=V2_OWNERSHIP_ACTOR,
                reason_codes=normalized.reason_codes,
                receipt_hash=content_hash(body),
            )
            self.session.add(receipt)
            self.session.flush()

        recovery_key = f"{V2_OWNERSHIP_RECOVERY_PREFIX}:{source.id}:{receipt.id}"
        existing = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.recovery_key == recovery_key)
            .with_for_update()
        )
        if existing is not None:
            self._validate_v2_compensation(
                source=source, child=existing, receipt=receipt
            )
            return existing

        slot = self.session.scalar(
            select(LongFormPublishSlot)
            .where(LongFormPublishSlot.id == source.publish_slot_id)
            .with_for_update()
        )
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == source.editorial_idea_candidate_id)
            .with_for_update()
        )
        current = self.now()
        terminal = dict(source.terminal_settlement_receipt or {})
        terminal_body = {
            key: value for key, value in terminal.items() if key != "content_hash"
        }
        deadline_policy = build_script_qualification_deadline_policy(self.settings)
        deadline_policy_receipt = deadline_policy.receipt()
        authority = self.session.get(
            ScriptContractReplacementAuthority,
            source.replacement_authority_id,
        )
        authority_deadline_policy = (
            (authority.authority_versions or {}).get("deadline_policy")
            if authority is not None
            else None
        )
        from app.services.script_content_repair import (
            ScriptContentRepairService,
        )

        ScriptContentRepairService(
            self.session, now=self.now
        )._validate_current_authority(source)
        from app.services.script_qualification import (
            ScriptQualificationService,
            ScriptRuntimeContractResolver,
        )

        assignment_body = {
            key: value
            for key, value in source.script_assignment.items()
            if key != "assignment_hash"
        }
        evidence_body = {
            key: value
            for key, value in source.factual_evidence_pack.items()
            if key != "evidence_pack_hash"
        }
        validate_memory_digest(
            source.memory_digest, expected_hash=source.memory_digest_hash
        )
        ScriptRuntimeContractResolver.validate(
            source.runtime_contract, expected_hash=source.runtime_contract_hash
        )
        ScriptQualificationService._validate_assignment_resolution(source)
        provider_authority = resolve_provider_authority(
            self.session,
            policy_snapshot_id=candidate.policy_snapshot_id,
            channel_workspace_id=candidate.channel_workspace_id,
        )
        provider_authority_hash = content_hash(provider_authority)
        series_reservation = self.session.scalar(
            select(SeriesEpisodeReservation.id).where(
                SeriesEpisodeReservation.script_qualification_run_id == source.id
            )
        )
        if (
            slot is None
            or candidate is None
            or slot.state != "CANCELED"
            or slot.reserved_candidate_id != candidate.id
            or slot.admitted_video_project_id is not None
            or candidate.stage != "REJECTED"
            or terminal.get("kind") != "DETERMINISTIC_BLOCK"
            or terminal.get("reason_code") != "SCRIPT_WRITER_OUTPUT_INVALID"
            or terminal.get("qualification_run_id") != str(source.id)
            or terminal.get("content_hash") != content_hash(terminal_body)
            or terminal.get("publish_slot_id") != str(slot.id)
            or terminal.get("publish_slot_state") != "CANCELED"
            or terminal.get("reserved_candidate_id") != str(candidate.id)
            or terminal.get("candidate_id") != str(candidate.id)
            or terminal.get("candidate_stage") != "REJECTED"
            or terminal.get("capacity_released") is not True
            or terminal.get("reservation_id") is not None
            or terminal.get("reservation_state") is not None
            or (source.failure_receipt or {}).get("terminal_settlement_receipt_hash")
            != terminal.get("content_hash")
            or source.episode_reservation_active
            or series_reservation is not None
            or authority is None
            or authority.replacement_qualification_run_id != source.id
            or authority.replacement_candidate_id != candidate.id
            or authority.replacement_slot_id != slot.id
            or authority.qualification_deadline != source.logical_deadline_at
            or authority.max_initial_writer_submissions != 1
            or authority.max_verifier_submissions != 1
            or authority.replacement_reason != OPERATOR_RECOVERY_REASON
            or authority.operator_recovery_schema_version != OPERATOR_RECOVERY_SCHEMA
            or authority.operator_recovery_id != authority.id
            or authority_deadline_policy != deadline_policy_receipt
            or provider_authority.get("state") != "READY"
            or source.script_assignment.get("assignment_hash")
            != source.script_assignment_hash
            or source.script_assignment_hash != content_hash(assignment_body)
            or source.factual_evidence_pack_hash
            != source.factual_evidence_pack.get("evidence_pack_hash")
            or source.factual_evidence_pack_hash != content_hash(evidence_body)
            or current
            + timedelta(
                seconds=(
                    deadline_policy.provider_stage_budget_seconds
                    + deadline_policy.safety_buffer_seconds
                )
            )
            >= source.logical_deadline_at
        ):
            raise ValidationFailureError("SCRIPT_WRITER_V2_OWNERSHIP_SETTLEMENT_DRIFT")

        normalized_output = QualifiedScriptOutputV2.model_validate(
            receipt.normalized_payload
        )
        child_id = uuid.uuid4()
        child = ScriptQualificationRun(
            id=child_id,
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
            episode_reservation_active=False,
            writer_prompt_version=source.writer_prompt_version,
            verifier_prompt_version=source.verifier_prompt_version,
            gate_policy_version=source.gate_policy_version,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=content_hash(
                {
                    "source_logical_identity_hash": source.logical_identity_hash,
                    "normalization_receipt_hash": receipt.receipt_hash,
                    "recovery_key": recovery_key,
                }
            ),
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=self.now(),
            logical_deadline_at=source.logical_deadline_at,
            state="RESERVED",
            writer_attempt_key=f"{recovery_key}:reused-completed-response",
            verifier_attempt_key=f"{recovery_key}:verifier",
            script_contract_version=source.script_contract_version,
            replacement_authority_id=source.replacement_authority_id,
        )
        self.session.add(child)
        self.session.flush()
        qualification_service = ScriptQualificationService(self.session)
        draft = qualification_service.accept_writer_output(
            child, normalized_output.model_dump(mode="json")
        )
        structural = qualification_service._structural_receipt(child, draft)
        if structural.get("status") != "PASS":
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_GATE_A_BLOCKED:"
                + ",".join(structural.get("reason_codes") or [])
            )
        child.state = "SCRIPT_GENERATED"
        child.writer_receipt = {
            "producer": "NORMALIZED_COMPLETED_V2_BACKGROUND_RESPONSE",
            "producer_type": "OPENAI_BACKGROUND_NORMALIZED",
            "producer_version": V2_OWNERSHIP_NORMALIZATION_VERSION,
            "background": True,
            "provider": "OPENAI",
            "model": source.model,
            "selected_model": attempt.model,
            "lane_name": attempt.lane,
            "source_background_attempt_id": str(attempt.id),
            "source_qualification_run_id": str(source.id),
            "source_provider_response_id": receipt.source_provider_response_id,
            "source_provider_request_id": receipt.source_provider_request_id,
            "source_raw_output_hash": receipt.source_raw_output_hash,
            "source_typed_provider_output_hash": snapshot.accepted_typed_output_hash,
            "normalization_receipt_id": str(receipt.id),
            "normalization_receipt_hash": receipt.receipt_hash,
            "producer_input_hash": attempt.input_fingerprint,
            "producer_output_hash": receipt.normalized_payload_hash,
            "prompt_version": source.writer_prompt_version,
            "writer_submission_count_for_new_recovery": 0,
            "reused_completed_writer_response_count": 1,
            "structural_receipt": structural,
        }
        compensation_body = {
            "schema_version": V2_OWNERSHIP_COMPENSATION_SCHEMA,
            "source_qualification_run_id": str(source.id),
            "child_qualification_run_id": str(child.id),
            "normalization_receipt_id": str(receipt.id),
            "normalization_receipt_hash": receipt.receipt_hash,
            "normalized_payload_hash": receipt.normalized_payload_hash,
            "prior_terminal_settlement_hash": terminal["content_hash"],
            "prior_slot_state": slot.state,
            "prior_candidate_stage": candidate.stage,
            "restored_slot_state": "QUALIFICATION_RESERVED",
            "restored_candidate_stage": "GREENLIT",
            "deadline_policy": deadline_policy_receipt,
            "provider_authority_hash": provider_authority_hash,
            "provider_authority_state": provider_authority.get("state"),
            "compensated_at": current.isoformat(),
        }
        compensation = {
            **compensation_body,
            "content_hash": content_hash(compensation_body),
        }
        source.provider_outcome_reconciliation_receipts = [
            *(source.provider_outcome_reconciliation_receipts or []),
            compensation,
        ]
        slot.state = "QUALIFICATION_RESERVED"
        candidate.stage = "GREENLIT"
        candidate.reason_codes = [
            code
            for code in (candidate.reason_codes or [])
            if code != "SCRIPT_QUALIFICATION_TERMINAL_BLOCKED"
        ]
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
            raise ValidationFailureError(
                "VERIFIER_SCHEMA_RECOVERY_NO_CONFIRMED_HTTP_REJECTION"
            )
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

    def continue_after_confirmed_verifier_auth_rejection(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Continue after an HTTP 401 that created no provider response."""

        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        failure = source.failure_receipt if source is not None else {}
        if (
            source is None
            or source.state != "BLOCKED_NON_REPAIRABLE"
            or source.script_contract_version != "V2_SINGLE_SOURCE"
            or source.script_payload is None
            or source.logical_deadline_at is None
            or "SCRIPT_PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"
            not in ((failure or {}).get("reason_codes") or [])
            or "HTTP 401" not in str((failure or {}).get("detail") or "")
        ):
            raise ValidationFailureError("VERIFIER_AUTH_REJECTION_SOURCE_INVALID")
        attempts = list(
            self.session.scalars(
                select(ScriptQualificationBackgroundAttempt)
                .where(
                    ScriptQualificationBackgroundAttempt.script_qualification_run_id
                    == source.id
                )
                .with_for_update()
            ).all()
        )
        if len(attempts) != 1:
            raise ValidationFailureError("VERIFIER_AUTH_REJECTION_ATTEMPT_DRIFT")
        failed = attempts[0]
        if (
            failed.phase != "VERIFIER"
            or failed.background_status != "SUBMISSION_OUTCOME_UNKNOWN"
            or failed.submission_attempt_count != 1
            or failed.provider_response_id is not None
            or failed.provider_request_id is not None
            or failed.last_network_error != "OPENAI_AUTHENTICATION_FAILED"
        ):
            raise ValidationFailureError("VERIFIER_AUTH_REJECTION_NOT_PROVEN")
        recovery_key = f"{VERIFIER_AUTH_REJECTION_PREFIX}:{source.id}:{failed.id}"
        existing = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.recovery_key == recovery_key)
            .with_for_update()
        )
        if existing is not None:
            return existing

        authority = self.session.get(
            ScriptContractReplacementAuthority, source.replacement_authority_id
        )
        root = self.session.get(
            ScriptQualificationRun,
            authority.replacement_qualification_run_id if authority else None,
        )
        slot = self.session.scalar(
            select(LongFormPublishSlot)
            .where(LongFormPublishSlot.id == source.publish_slot_id)
            .with_for_update()
        )
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == source.editorial_idea_candidate_id)
            .with_for_update()
        )
        terminal = dict(source.terminal_settlement_receipt or {})
        terminal_body = {
            key: value for key, value in terminal.items() if key != "content_hash"
        }
        original = [
            item
            for item in (root.provider_outcome_reconciliation_receipts if root else [])
            if isinstance(item, dict)
            and item.get("schema_version") == V2_OWNERSHIP_COMPENSATION_SCHEMA
            and item.get("child_qualification_run_id") == str(source.id)
        ]
        try:
            authorized_at = datetime.fromisoformat(str(original[0]["compensated_at"]))
        except (IndexError, KeyError, ValueError) as exc:
            raise ValidationFailureError(
                "VERIFIER_AUTH_REJECTION_ORIGINAL_AUTHORITY_MISSING"
            ) from exc
        policy = build_script_qualification_deadline_policy(self.settings)
        current = self.now()
        accepted_response = self.session.scalar(
            select(ScriptQualificationBackgroundAttempt.id)
            .join(
                ScriptQualificationRun,
                ScriptQualificationRun.id
                == ScriptQualificationBackgroundAttempt.script_qualification_run_id,
            )
            .where(
                ScriptQualificationRun.replacement_authority_id
                == source.replacement_authority_id,
                ScriptQualificationBackgroundAttempt.phase == "VERIFIER",
                ScriptQualificationBackgroundAttempt.provider_response_id.is_not(None),
            )
            .limit(1)
        )
        if (
            authority is None
            or root is None
            or slot is None
            or candidate is None
            or authority.replacement_reason != OPERATOR_RECOVERY_REASON
            or authority.operator_recovery_schema_version != OPERATOR_RECOVERY_SCHEMA
            or authority.qualification_deadline != source.logical_deadline_at
            or source.supersedes_qualification_run_id != root.id
            or slot.state != "QUALIFICATION_RECONCILIATION_REQUIRED"
            or candidate.stage != "REJECTED"
            or terminal.get("kind") != "UNKNOWN_PROVIDER_OUTCOME"
            or terminal.get("qualification_run_id") != str(source.id)
            or terminal.get("content_hash") != content_hash(terminal_body)
            or accepted_response is not None
            or authorized_at
            + timedelta(
                seconds=(
                    policy.provider_stage_budget_seconds + policy.safety_buffer_seconds
                )
            )
            >= source.logical_deadline_at
            or current >= source.logical_deadline_at
        ):
            raise ValidationFailureError("VERIFIER_AUTH_REJECTION_AUTHORITY_DRIFT")

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
            episode_reservation_active=False,
            writer_prompt_version=source.writer_prompt_version,
            verifier_prompt_version=source.verifier_prompt_version,
            gate_policy_version=source.gate_policy_version,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=content_hash(
                {
                    "source": source.logical_identity_hash,
                    "failed_attempt_id": str(failed.id),
                    "recovery_key": recovery_key,
                }
            ),
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=current,
            logical_deadline_at=source.logical_deadline_at,
            state="RESERVED",
            writer_attempt_key=f"{recovery_key}:reused-normalized-output",
            verifier_attempt_key=f"{recovery_key}:verifier",
            script_contract_version=source.script_contract_version,
            replacement_authority_id=source.replacement_authority_id,
        )
        self.session.add(child)
        self.session.flush()
        from app.services.script_qualification import ScriptQualificationService

        service = ScriptQualificationService(self.session)
        draft = service.accept_writer_output(child, source.script_payload)
        structural = service._structural_receipt(child, draft)
        if structural.get("status") != "PASS":
            raise ValidationFailureError("VERIFIER_AUTH_REJECTION_SCRIPT_DRIFT")
        body = {
            "schema_version": "script-verifier-auth-rejection-compensation.v1",
            "source_qualification_run_id": str(source.id),
            "failed_background_attempt_id": str(failed.id),
            "child_qualification_run_id": str(child.id),
            "provider_effect": "NONE_CONFIRMED_BY_HTTP_401",
            "provider_response_id": None,
            "provider_request_id": None,
            "prior_terminal_settlement_hash": terminal["content_hash"],
            "original_verifier_authorized_at": authorized_at.isoformat(),
            "deadline_policy": policy.receipt(),
            "restored_slot_state": "QUALIFICATION_RESERVED",
            "restored_candidate_stage": "GREENLIT",
            "compensated_at": current.isoformat(),
        }
        compensation = {**body, "content_hash": content_hash(body)}
        source.provider_outcome_reconciliation_receipts = [
            *(source.provider_outcome_reconciliation_receipts or []),
            compensation,
        ]
        child.state = "SCRIPT_GENERATED"
        child.writer_receipt = {
            **(source.writer_receipt or {}),
            "source_auth_rejected_qualification_run_id": str(source.id),
            "auth_rejection_compensation_hash": compensation["content_hash"],
            "writer_submission_count_for_new_recovery": 0,
        }
        slot.state = "QUALIFICATION_RESERVED"
        candidate.stage = "GREENLIT"
        candidate.reason_codes = [
            item
            for item in (candidate.reason_codes or [])
            if item != "SCRIPT_QUALIFICATION_RECONCILIATION_REQUIRED"
        ]
        self.session.flush()
        self._enqueue_verifier(child)
        return child

    def continue_after_content_repair_scope_reclassification(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Reuse one completed repair response under fresh additive authority."""

        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if (
            source is None
            or source.state != "BLOCKED_NON_REPAIRABLE"
            or source.repair_attempts != 1
            or source.script_contract_version != "V2_SINGLE_SOURCE"
            or (source.failure_receipt or {}).get("detail")
            != "SCRIPT_CONTENT_REPAIR_SECTION_IDENTITY_CHANGED"
            or source.script_payload is not None
            or source.logical_deadline_at is None
        ):
            raise ValidationFailureError("CONTENT_REPAIR_SCOPE_SOURCE_INVALID")
        attempts = list(
            self.session.scalars(
                select(ScriptQualificationBackgroundAttempt)
                .where(
                    ScriptQualificationBackgroundAttempt.script_qualification_run_id
                    == source.id
                )
                .with_for_update()
            ).all()
        )
        if len(attempts) != 1:
            raise ValidationFailureError("CONTENT_REPAIR_SCOPE_ATTEMPT_DRIFT")
        attempt = attempts[0]
        snapshot = self.session.scalar(
            select(ScriptQualificationProviderResponseSnapshot)
            .where(
                ScriptQualificationProviderResponseSnapshot.background_attempt_id
                == attempt.id
            )
            .with_for_update()
        )
        if (
            snapshot is None
            or attempt.phase != "WRITER"
            or attempt.background_status != "COMPLETED"
            or attempt.provider_outcome != "SCRIPT_WRITER_OUTPUT_INVALID"
            or attempt.submission_attempt_count != 1
            or not attempt.provider_response_id
            or not attempt.provider_request_id
            or snapshot.validation_errors
            or snapshot.accepted_typed_output_hash is None
        ):
            raise ValidationFailureError("CONTENT_REPAIR_SCOPE_RESPONSE_INVALID")
        try:
            payload = QualifiedScriptOutputV2.model_validate(
                json.loads(snapshot.raw_output_content)
            ).model_dump(mode="json")
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationFailureError(
                "CONTENT_REPAIR_SCOPE_RESPONSE_INVALID"
            ) from exc
        if (
            snapshot.script_qualification_run_id != source.id
            or snapshot.background_attempt_id != attempt.id
            or snapshot.phase != "WRITER"
            or snapshot.provider_response_id != attempt.provider_response_id
            or snapshot.provider_request_id != attempt.provider_request_id
            or snapshot.prompt_version != attempt.prompt_version
            or attempt.prompt_version != source.writer_prompt_version
            or attempt.model != source.model
            or snapshot.response_schema_identifier != attempt.response_schema_identifier
            or snapshot.response_schema_hash != attempt.response_schema_hash
            or snapshot.producer_input_hash != attempt.input_fingerprint
            or snapshot.raw_provider_response_hash
            != content_hash(snapshot.raw_provider_response)
            or snapshot.raw_provider_response_hash != attempt.output_hash
            or snapshot.raw_output_hash
            != content_hash({"content": snapshot.raw_output_content})
            or snapshot.accepted_typed_output_hash != content_hash(payload)
        ):
            raise ValidationFailureError("CONTENT_REPAIR_SCOPE_RESPONSE_DRIFT")
        from app.services.script_content_repair import (
            ScriptContentRepairService,
            content_repair_authorization_body,
        )

        repair_service = ScriptContentRepairService(self.session, now=self.now)
        repair_service.validate_output_scope(
            source, QualifiedScriptOutputV2.model_validate(payload)
        )
        editable_claim_ids, removable_claim_ids = repair_service.claim_repair_scope(
            source
        )
        recovery_key = (
            f"{CONTENT_REPAIR_SCOPE_RECOVERY_PREFIX}:{source.id}:{snapshot.id}"
        )
        existing = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.recovery_key == recovery_key)
            .with_for_update()
        )
        if existing is not None:
            self._validate_content_repair_continuation(
                source=source, child=existing, snapshot=snapshot
            )
            return existing
        source_slot = self.session.scalar(
            select(LongFormPublishSlot)
            .where(LongFormPublishSlot.id == source.publish_slot_id)
            .with_for_update()
        )
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == source.editorial_idea_candidate_id)
            .with_for_update()
        )
        terminal = dict(source.terminal_settlement_receipt or {})
        terminal_body = {
            key: value for key, value in terminal.items() if key != "content_hash"
        }
        authority = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(
                ScriptContractReplacementAuthority.id == source.replacement_authority_id
            )
            .with_for_update()
        )
        repair_authorization = self.session.scalar(
            select(ScriptContentRepairAuthorizationReceipt)
            .where(
                ScriptContentRepairAuthorizationReceipt.id
                == (
                    (source.writer_receipt or {}).get("content_repair_authorization_id")
                    if isinstance(source.writer_receipt, dict)
                    else None
                )
            )
            .with_for_update()
        )
        if repair_authorization is None:
            repair_authorization = self.session.scalar(
                select(ScriptContentRepairAuthorizationReceipt)
                .where(
                    ScriptContentRepairAuthorizationReceipt.source_qualification_run_id
                    == source.supersedes_qualification_run_id
                )
                .with_for_update()
            )
        current = self.now()
        launch = self.session.get(LaunchRun, source.launch_run_id)
        current_leaf = (
            resolve_replacement_qualification_leaf(
                self.session, authority=authority, lock=True
            )
            if authority is not None
            else None
        )
        existing_admission = self.session.scalar(
            select(ProjectAdmissionDecision.id).where(
                ProjectAdmissionDecision.editorial_idea_candidate_id
                == source.editorial_idea_candidate_id,
                ProjectAdmissionDecision.decision == "ADMIT",
            )
        )
        active_workflow = (
            self.session.scalar(
                select(ProductionWorkflowRun.id).where(
                    ProductionWorkflowRun.channel_workspace_id
                    == candidate.channel_workspace_id,
                    ProductionWorkflowRun.state.not_in(
                        {
                            "CANCELED",
                            "FAILED_TERMINAL",
                            "DEAD_LETTERED",
                            "SUPERSEDED",
                        }
                    ),
                )
            )
            if candidate is not None
            else None
        )
        if (
            source_slot is None
            or candidate is None
            or authority is None
            or repair_authorization is None
            or source_slot.state != "CANCELED"
            or source_slot.reserved_candidate_id != candidate.id
            or source_slot.admitted_video_project_id is not None
            or candidate.stage != "REJECTED"
            or terminal.get("kind") != "DETERMINISTIC_BLOCK"
            or terminal.get("reason_code") != "SCRIPT_WRITER_OUTPUT_INVALID"
            or terminal.get("qualification_run_id") != str(source.id)
            or terminal.get("publish_slot_id") != str(source_slot.id)
            or terminal.get("candidate_id") != str(candidate.id)
            or terminal.get("reserved_candidate_id") != str(candidate.id)
            or terminal.get("publish_slot_state") != "CANCELED"
            or terminal.get("candidate_stage") != "REJECTED"
            or terminal.get("capacity_released") is not True
            or terminal.get("reservation_id") is not None
            or terminal.get("reservation_state") is not None
            or terminal.get("content_hash") != content_hash(terminal_body)
            or (source.failure_receipt or {}).get("terminal_settlement_receipt_hash")
            != terminal.get("content_hash")
            or source.admitted_video_project_id is not None
            or source.production_workflow_run_id is not None
            or source.episode_reservation_active
            or authority.replacement_reason != OPERATOR_RECOVERY_REASON
            or authority.operator_recovery_schema_version != OPERATOR_RECOVERY_SCHEMA
            or authority.bounded_content_repair_policy_ref != REPAIR_POLICY_REF
            or authority.authority_hash
            != content_hash(operator_recovery_authority_body(authority))
            or authority.operator_recovery_id != authority.id
            or authority.replacement_candidate_id != candidate.id
            or current_leaf is None
            or current_leaf.id != source.id
            or authority.qualification_deadline != source.logical_deadline_at
            or current < source.logical_deadline_at
            or launch is None
            or launch.state != "ACTIVE"
            or launch.id != source_slot.launch_run_id
            or launch.launch_policy_version_id != source_slot.launch_policy_version_id
            or launch.company_id != candidate.company_id
            or launch.channel_workspace_id != candidate.channel_workspace_id
            or source_slot.company_id != candidate.company_id
            or source_slot.channel_workspace_id != candidate.channel_workspace_id
            or existing_admission is not None
            or active_workflow is not None
            or repair_authorization.source_qualification_run_id
            != source.supersedes_qualification_run_id
            or repair_authorization.repair_policy_version != "script-content-repair.v1"
            or repair_authorization.repair_type != "SCRIPT_CONTENT_REPAIR"
            or repair_authorization.authorization_hash
            != content_hash(content_repair_authorization_body(repair_authorization))
        ):
            raise ValidationFailureError("CONTENT_REPAIR_SCOPE_SETTLEMENT_DRIFT")
        policy = build_script_qualification_deadline_policy(self.settings)
        slot_close = minimum_script_qualification_window_close_at(
            requested_at=current, policy=policy
        ) + timedelta(seconds=1)
        deadline = derive_script_qualification_deadline(slot_close, policy)
        if not script_qualification_slot_is_viable(
            now=current, slot_window_close_at=slot_close, policy=policy
        ):
            raise ValidationFailureError("CONTENT_REPAIR_CONTINUATION_WINDOW_INVALID")
        launch_policy = self.session.get(
            FirstChannelLaunchPolicyVersion, source_slot.launch_policy_version_id
        )
        if launch_policy is None or launch_policy.state != "APPROVED":
            raise ValidationFailureError("CONTENT_REPAIR_LAUNCH_POLICY_STALE")
        from app.services.script_qualification import TOPIC_GATE_VERSION

        topic_receipt = self.session.scalar(
            select(EditorialTopicDefinitionGateReceipt)
            .where(
                EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
                == source.topic_definition_id,
                EditorialTopicDefinitionGateReceipt.gate_version == TOPIC_GATE_VERSION,
            )
            .order_by(EditorialTopicDefinitionGateReceipt.created_at.desc())
        )
        topic_definition = self.session.get(
            EditorialTopicDefinition, source.topic_definition_id
        )
        preflight = self.session.scalar(
            select(IdeaMarketPreflight)
            .where(IdeaMarketPreflight.editorial_idea_candidate_id == candidate.id)
            .order_by(IdeaMarketPreflight.created_at.desc())
        )
        novelty = (
            EditorialNoveltyService(self.session).evaluate(
                candidate=candidate,
                topic=topic_definition,
            )
            if topic_definition is not None
            else None
        )
        freshness = ScriptContractReplacementAuthorityService(
            self.session, now=self.now
        )._current_freshness_snapshot(candidate=candidate, evaluated_at=current)
        provider_authority = resolve_provider_authority(
            self.session,
            policy_snapshot_id=candidate.policy_snapshot_id,
            channel_workspace_id=candidate.channel_workspace_id,
        )
        budget_authority = resolve_budget_authority(
            self.session,
            policy_snapshot_id=candidate.policy_snapshot_id,
            channel_workspace_id=candidate.channel_workspace_id,
        )
        if (
            topic_receipt is None
            or topic_receipt.state != "PASS"
            or not topic_receipt.current_production_eligibility
            or preflight is None
            or preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
            or not _preflight_demand_authority_valid(preflight)
            or not EditorialSpecificityService(self.session).current_pass(candidate)
            or novelty is None
            or novelty.state != "PASS"
            or (candidate.editorial_novelty_receipt or {}).get("gate_version")
            != EDITORIAL_NOVELTY_GATE_VERSION
            or (candidate.editorial_novelty_receipt or {}).get("evaluation_hash")
            != novelty.evaluation_hash
            or freshness.get("state") != "FRESH"
            or provider_authority.get("state") != "READY"
            or budget_authority.get("state") != "READY"
        ):
            raise ValidationFailureError("CONTENT_REPAIR_CURRENT_AUTHORITY_BLOCKED")
        continuation_id = uuid.uuid4()
        child_id = uuid.uuid4()
        continuation_slot_id = uuid.uuid4()
        logical_identity_hash = content_hash(
            {
                "source": source.logical_identity_hash,
                "snapshot_id": str(snapshot.id),
                "continuation_authority_id": str(continuation_id),
                "recovery_key": recovery_key,
            }
        )
        intended_publish_at = slot_close + timedelta(
            hours=launch_policy.render_lead_time_min_hours
        )
        continuation_slot = LongFormPublishSlot(
            id=continuation_slot_id,
            launch_run_id=source_slot.launch_run_id,
            launch_policy_version_id=source_slot.launch_policy_version_id,
            company_id=source_slot.company_id,
            channel_workspace_id=source_slot.channel_workspace_id,
            local_publish_date=intended_publish_at.astimezone(
                ZoneInfo(launch_policy.timezone)
            ).date(),
            intended_publish_at=intended_publish_at,
            target_start_window_open_at=current,
            target_start_window_close_at=slot_close,
            state="QUALIFICATION_RESERVED",
            reserved_candidate_id=candidate.id,
            replaces_slot_id=source_slot.id,
            replacement_authority_id=authority.id,
            replacement_reason=CONTROLLED_CONTINUATION_REASON,
            replacement_lineage_key=content_hash(
                {
                    "continuation_authority_id": str(continuation_id),
                    "source_slot_id": str(source_slot.id),
                    "candidate_id": str(candidate.id),
                }
            ),
        )
        self.session.add(continuation_slot)
        slot_projection = controlled_continuation_slot_projection(continuation_slot)
        child = ScriptQualificationRun(
            id=child_id,
            editorial_idea_candidate_id=source.editorial_idea_candidate_id,
            publish_slot_id=continuation_slot.id,
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
            episode_reservation_active=False,
            writer_prompt_version=source.writer_prompt_version,
            verifier_prompt_version=source.verifier_prompt_version,
            gate_policy_version=source.gate_policy_version,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=logical_identity_hash,
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=self.now(),
            logical_deadline_at=deadline,
            state="RESERVED",
            writer_attempt_key=f"{recovery_key}:reused-repair-response",
            verifier_attempt_key=f"{recovery_key}:verifier",
            repair_attempts=1,
            script_contract_version=source.script_contract_version,
            replacement_authority_id=source.replacement_authority_id,
        )
        self.session.add(child)
        self.session.flush()
        from app.services.script_qualification import ScriptQualificationService

        service = ScriptQualificationService(self.session)
        draft = service.accept_writer_output(child, payload)
        structural = service._structural_receipt(child, draft)
        if structural.get("status") != "PASS":
            raise ValidationFailureError("CONTENT_REPAIR_SCOPE_STRUCTURAL_BLOCK")
        repair_body = {
            "schema_version": "script-content-repair-scope-reclassification.v1",
            "source_qualification_run_id": str(source.id),
            "child_qualification_run_id": str(child.id),
            "background_attempt_id": str(attempt.id),
            "provider_response_id": attempt.provider_response_id,
            "provider_request_id": attempt.provider_request_id,
            "provider_output_hash": attempt.output_hash,
            "snapshot_id": str(snapshot.id),
            "accepted_typed_output_hash": snapshot.accepted_typed_output_hash,
            "scope_validator_version": "script-content-repair-scope.v2",
            "writer_submission_count_for_child": 0,
            "prior_terminal_settlement_hash": terminal["content_hash"],
            "compensated_at": current.isoformat(),
        }
        compensation = {**repair_body, "content_hash": content_hash(repair_body)}
        deadline_policy_receipt = policy.receipt()
        current_authority_snapshot = {
            "topic_gate_receipt_id": str(topic_receipt.id),
            "topic_gate_state": topic_receipt.state,
            "preflight_id": str(preflight.id),
            "preflight_decision": preflight.decision,
            "specificity_pass": True,
            "novelty_receipt_hash": novelty.evaluation_hash,
            "freshness_snapshot": freshness,
            "evaluated_at": current.isoformat(),
        }
        continuation = ControlledProductionContinuationAuthority(
            id=continuation_id,
            root_replacement_authority_id=authority.id,
            source_qualification_run_id=source.id,
            source_slot_id=source_slot.id,
            continuation_candidate_id=candidate.id,
            continuation_slot_id=continuation_slot.id,
            continuation_qualification_run_id=child.id,
            source_provider_response_snapshot_id=snapshot.id,
            repair_authorization_id=repair_authorization.id,
            schema_version=CONTROLLED_CONTINUATION_SCHEMA,
            continuation_reason=CONTROLLED_CONTINUATION_REASON,
            root_authority_hash=authority.authority_hash,
            operator_recovery_schema_version=authority.operator_recovery_schema_version,
            operator_actor_context=authority.operator_actor_context,
            bounded_content_repair_policy_ref=authority.bounded_content_repair_policy_ref,
            source_logical_identity_hash=source.logical_identity_hash,
            continuation_logical_identity_hash=logical_identity_hash,
            source_terminal_settlement_hash=terminal["content_hash"],
            source_slot_state="CANCELED",
            source_candidate_stage="REJECTED",
            repair_authorization_hash=repair_authorization.authorization_hash,
            affected_section_ids=list(repair_authorization.affected_section_ids),
            editable_claim_ids=editable_claim_ids,
            removable_claim_ids=removable_claim_ids,
            source_background_attempt_id=attempt.id,
            source_provider_response_id=attempt.provider_response_id,
            source_provider_request_id=attempt.provider_request_id,
            source_raw_provider_response_hash=snapshot.raw_provider_response_hash,
            source_raw_output_hash=snapshot.raw_output_hash,
            source_typed_output_hash=snapshot.accepted_typed_output_hash,
            reclassification_receipt_hash=compensation["content_hash"],
            deadline_policy=deadline_policy_receipt,
            slot_projection=slot_projection,
            current_authority_snapshot=current_authority_snapshot,
            provider_authority_hash=content_hash(provider_authority),
            budget_authority_hash=content_hash(budget_authority),
            max_writer_submissions=0,
            max_verifier_submissions=1,
            production_window_end=slot_close,
            qualification_deadline=deadline,
            authority_hash="0" * 64,
            created_at=current,
        )
        continuation.authority_hash = content_hash(
            controlled_continuation_authority_body(continuation)
        )
        self.session.add(continuation)
        source.provider_outcome_reconciliation_receipts = [
            *(source.provider_outcome_reconciliation_receipts or []),
            compensation,
        ]
        child.state = "SCRIPT_GENERATED"
        child.writer_receipt = {
            "producer": "REUSED_COMPLETED_CONTENT_REPAIR_RESPONSE",
            "producer_type": "OPENAI_BACKGROUND_CONTENT_REPAIR",
            "provider": attempt.provider,
            "producer_version": attempt.prompt_version,
            "selected_model": attempt.model,
            "lane_name": attempt.lane,
            "source_qualification_run_id": str(source.id),
            "source_background_attempt_id": str(attempt.id),
            "source_provider_response_snapshot_id": str(snapshot.id),
            "source_provider_response_id": attempt.provider_response_id,
            "source_provider_request_id": attempt.provider_request_id,
            "producer_input_hash": attempt.input_fingerprint,
            "producer_output_hash": snapshot.accepted_typed_output_hash,
            "source_typed_provider_output_hash": snapshot.accepted_typed_output_hash,
            "reclassification_receipt_hash": compensation["content_hash"],
            "continuation_authority_id": str(continuation.id),
            "continuation_authority_hash": continuation.authority_hash,
            "writer_submission_count_for_new_recovery": 0,
        }
        candidate.stage = "GREENLIT"
        candidate.reason_codes = [
            item
            for item in (candidate.reason_codes or [])
            if item != "SCRIPT_QUALIFICATION_TERMINAL_BLOCKED"
        ]
        self.session.flush()
        self._enqueue_verifier(
            child, recovery_mode="BOUNDED_CONTENT_REPAIR_VERIFIER_CONTINUATION"
        )
        return child

    def _validate_content_repair_continuation(
        self,
        *,
        source: ScriptQualificationRun,
        child: ScriptQualificationRun,
        snapshot: ScriptQualificationProviderResponseSnapshot,
    ) -> None:
        continuation = self.session.scalar(
            select(ControlledProductionContinuationAuthority)
            .where(
                ControlledProductionContinuationAuthority.continuation_qualification_run_id
                == child.id
            )
            .with_for_update()
        )
        slot = self.session.get(LongFormPublishSlot, child.publish_slot_id)
        receipt = child.writer_receipt if isinstance(child.writer_receipt, dict) else {}
        matches = [
            item
            for item in (source.provider_outcome_reconciliation_receipts or [])
            if isinstance(item, dict)
            and item.get("content_hash")
            == (continuation.reclassification_receipt_hash if continuation else None)
        ]
        if (
            continuation is None
            or slot is None
            or continuation.schema_version != CONTROLLED_CONTINUATION_SCHEMA
            or continuation.continuation_reason != CONTROLLED_CONTINUATION_REASON
            or continuation.root_replacement_authority_id
            != source.replacement_authority_id
            or continuation.source_qualification_run_id != source.id
            or continuation.source_slot_id != source.publish_slot_id
            or continuation.continuation_candidate_id
            != source.editorial_idea_candidate_id
            or continuation.continuation_slot_id != child.publish_slot_id
            or continuation.source_provider_response_snapshot_id != snapshot.id
            or continuation.source_logical_identity_hash != source.logical_identity_hash
            or continuation.continuation_logical_identity_hash
            != child.logical_identity_hash
            or continuation.qualification_deadline != child.logical_deadline_at
            or continuation.max_writer_submissions != 0
            or continuation.max_verifier_submissions != 1
            or continuation.authority_hash
            != content_hash(controlled_continuation_authority_body(continuation))
            or continuation.slot_projection
            != controlled_continuation_slot_projection(slot)
            or child.supersedes_qualification_run_id != source.id
            or child.repair_attempts != 1
            or child.script_contract_version != "V2_SINGLE_SOURCE"
            or child.replacement_authority_id != source.replacement_authority_id
            or slot.replaces_slot_id != source.publish_slot_id
            or slot.replacement_lineage_key is None
            or receipt.get("continuation_authority_id") != str(continuation.id)
            or receipt.get("continuation_authority_hash") != continuation.authority_hash
            or receipt.get("reclassification_receipt_hash")
            != continuation.reclassification_receipt_hash
            or len(matches) != 1
            or matches[0].get("content_hash")
            != content_hash(
                {
                    key: value
                    for key, value in matches[0].items()
                    if key != "content_hash"
                }
            )
        ):
            raise ValidationFailureError("CONTENT_REPAIR_CONTINUATION_DRIFT")

    def _v2_ownership_source(
        self, source_qualification_run_id: uuid.UUID
    ) -> tuple[
        ScriptQualificationRun,
        ScriptQualificationBackgroundAttempt,
        ScriptQualificationProviderResponseSnapshot,
    ]:
        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        failure = (
            source.failure_receipt
            if source is not None and isinstance(source.failure_receipt, dict)
            else {}
        )
        if (
            source is None
            or source.state != "BLOCKED_NON_REPAIRABLE"
            or source.script_contract_version != "V2_SINGLE_SOURCE"
            or failure.get("detail") != V2_OWNERSHIP_FAILURE
            or (failure.get("reason_codes") or []) != ["SCRIPT_WRITER_OUTPUT_INVALID"]
            or source.script_payload is not None
            or source.result_receipts is not None
            or source.canonical_script_artifact_id is not None
            or source.derived_canonical_script_hash is not None
            or source.admitted_video_project_id is not None
            or source.production_workflow_run_id is not None
            or source.replacement_authority_id is None
            or source.logical_deadline_at is None
        ):
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_SOURCE_NOT_ELIGIBLE"
            )
        attempts = list(
            self.session.scalars(
                select(ScriptQualificationBackgroundAttempt)
                .where(
                    ScriptQualificationBackgroundAttempt.script_qualification_run_id
                    == source.id
                )
                .with_for_update()
            ).all()
        )
        if len(attempts) != 1:
            raise ValidationFailureError("SCRIPT_WRITER_V2_OWNERSHIP_ATTEMPT_DRIFT")
        attempt = attempts[0]
        if (
            attempt.phase != "WRITER"
            or attempt.background_status != "COMPLETED"
            or attempt.provider_outcome != "SCRIPT_WRITER_OUTPUT_INVALID"
            or attempt.submission_attempt_count != 1
            or not attempt.provider_response_id
            or not attempt.provider_request_id
            or not attempt.output_hash
            or attempt.prompt_version != source.writer_prompt_version
        ):
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_RESPONSE_NOT_REUSABLE"
            )
        snapshots = list(
            self.session.scalars(
                select(ScriptQualificationProviderResponseSnapshot)
                .where(
                    ScriptQualificationProviderResponseSnapshot.script_qualification_run_id
                    == source.id
                )
                .with_for_update()
            ).all()
        )
        if len(snapshots) != 1:
            raise ValidationFailureError("SCRIPT_WRITER_V2_OWNERSHIP_SNAPSHOT_MISSING")
        snapshot = snapshots[0]
        try:
            raw_output = json.loads(snapshot.raw_output_content)
            typed_source = QualifiedScriptOutputV2.model_validate(
                raw_output
            ).model_dump(mode="json")
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_SOURCE_CONTRACT_INVALID"
            ) from exc
        if (
            snapshot.background_attempt_id != attempt.id
            or snapshot.phase != "WRITER"
            or snapshot.provider_response_id != attempt.provider_response_id
            or snapshot.provider_request_id != attempt.provider_request_id
            or snapshot.prompt_version != source.writer_prompt_version
            or snapshot.response_schema_identifier != attempt.response_schema_identifier
            or snapshot.response_schema_hash != attempt.response_schema_hash
            or snapshot.producer_input_hash != attempt.input_fingerprint
            or bool(snapshot.validation_errors)
            or snapshot.accepted_typed_output_hash != content_hash(raw_output)
            or typed_source != raw_output
            or snapshot.raw_provider_response_hash
            != content_hash(snapshot.raw_provider_response)
            or attempt.output_hash != content_hash(snapshot.raw_provider_response)
            or snapshot.raw_output_hash
            != content_hash({"content": snapshot.raw_output_content})
        ):
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_SNAPSHOT_HASH_MISMATCH"
            )
        return source, attempt, snapshot

    def _validate_v2_ownership_receipt(
        self,
        receipt: ScriptWriterOutputNormalizationReceipt,
        *,
        source: ScriptQualificationRun,
        attempt: ScriptQualificationBackgroundAttempt,
        snapshot: ScriptQualificationProviderResponseSnapshot,
    ) -> ScriptWriterOutputNormalizationReceipt:
        body = {
            "schema_version": V2_OWNERSHIP_RECEIPT_SCHEMA,
            "source_qualification_run_id": str(receipt.source_qualification_run_id),
            "source_background_attempt_id": str(receipt.source_background_attempt_id),
            "source_provider_response_snapshot_id": str(
                receipt.source_provider_response_snapshot_id
            ),
            "source_provider_response_id": receipt.source_provider_response_id,
            "source_provider_request_id": receipt.source_provider_request_id,
            "source_raw_output_hash": receipt.source_raw_output_hash,
            "source_schema_classification": receipt.source_schema_classification,
            "normalization_version": receipt.normalization_version,
            "field_mapping": receipt.field_mapping,
            "removed_wrapper_fields": receipt.removed_wrapper_fields,
            "normalized_payload": receipt.normalized_payload,
            "normalized_payload_hash": receipt.normalized_payload_hash,
            "contract_schema_version": receipt.contract_schema_version,
            "validation_result": receipt.validation_result,
            "actor": receipt.actor,
            "reason_codes": receipt.reason_codes,
        }
        try:
            normalized = QualifiedScriptOutputV2.model_validate(
                receipt.normalized_payload
            ).model_dump(mode="json")
            source_payload = json.loads(snapshot.raw_output_content)
            recomputed = normalize_v2_section_ownership(
                source_payload,
                (source.script_assignment or {}).get("section_coverage_plan"),
            )
        except ValueError as exc:
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_RECEIPT_INVALID"
            ) from exc
        if (
            receipt.source_qualification_run_id != source.id
            or receipt.source_background_attempt_id != attempt.id
            or receipt.source_provider_response_snapshot_id != snapshot.id
            or receipt.source_provider_response_id != attempt.provider_response_id
            or receipt.source_provider_request_id != attempt.provider_request_id
            or receipt.source_raw_output_hash != snapshot.raw_output_hash
            or receipt.source_schema_classification
            != "V2_INFORMATION_UNIT_IDS_IN_REQUIREMENT_REFS"
            or receipt.normalization_version != V2_OWNERSHIP_NORMALIZATION_VERSION
            or receipt.contract_schema_version != V2_CONTRACT_SCHEMA_VERSION
            or receipt.actor != V2_OWNERSHIP_ACTOR
            or receipt.removed_wrapper_fields != {}
            or normalized != receipt.normalized_payload
            or recomputed.classification != receipt.source_schema_classification
            or recomputed.payload != receipt.normalized_payload
            or recomputed.field_mapping != receipt.field_mapping
            or recomputed.removed_wrapper_fields != receipt.removed_wrapper_fields
            or recomputed.reason_codes != receipt.reason_codes
            or receipt.normalized_payload_hash != content_hash(normalized)
            or receipt.receipt_hash != content_hash(body)
        ):
            raise ValidationFailureError("SCRIPT_WRITER_V2_OWNERSHIP_RECEIPT_INVALID")
        return receipt

    @staticmethod
    def _validate_v2_compensation(
        *,
        source: ScriptQualificationRun,
        child: ScriptQualificationRun,
        receipt: ScriptWriterOutputNormalizationReceipt,
    ) -> None:
        matches = [
            item
            for item in (source.provider_outcome_reconciliation_receipts or [])
            if isinstance(item, dict)
            and item.get("schema_version") == V2_OWNERSHIP_COMPENSATION_SCHEMA
            and item.get("child_qualification_run_id") == str(child.id)
        ]
        if len(matches) != 1:
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_COMPENSATION_MISSING"
            )
        compensation = matches[0]
        body = {
            key: value for key, value in compensation.items() if key != "content_hash"
        }
        if (
            compensation.get("content_hash") != content_hash(body)
            or compensation.get("source_qualification_run_id") != str(source.id)
            or compensation.get("normalization_receipt_id") != str(receipt.id)
            or compensation.get("normalization_receipt_hash") != receipt.receipt_hash
            or child.supersedes_qualification_run_id != source.id
            or child.replacement_authority_id != source.replacement_authority_id
            or child.script_contract_version != source.script_contract_version
        ):
            raise ValidationFailureError(
                "SCRIPT_WRITER_V2_OWNERSHIP_COMPENSATION_INVALID"
            )

    def _source(
        self, source_qualification_run_id: uuid.UUID
    ) -> tuple[ScriptQualificationRun, ScriptQualificationBackgroundAttempt]:
        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if source is None or source.state != "BLOCKED_NON_REPAIRABLE":
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_SOURCE_NOT_TERMINAL"
            )
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
            raise ValidationFailureError(
                "SCRIPT_WRITER_NORMALIZATION_RESPONSE_NOT_REUSABLE"
            )
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
                raise ValidationFailureError(
                    "SCRIPT_WRITER_NORMALIZATION_SNAPSHOT_DRIFT"
                )
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

        return ScriptQualificationService(self.session)._structural_receipt(
            source, payload
        )

    def _enqueue_verifier(
        self,
        run: ScriptQualificationRun,
        *,
        recovery_mode: str = "NORMALIZED_COMPLETED_WRITER_RESPONSE",
    ) -> None:
        command_id = f"{run.recovery_key}:verifier-post-normalization"
        existing = self.session.scalar(
            select(DomainEvent).where(DomainEvent.command_id == command_id)
        )
        if existing is not None:
            return
        payload = {
            "script_qualification_run_id": str(run.id),
            "background_attempt_id": None,
            "recovery_mode": recovery_mode,
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
                    "retry_policy": {
                        "automatic_retry_allowed": recovery_mode
                        != "BOUNDED_CONTENT_REPAIR_VERIFIER_CONTINUATION"
                    },
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
