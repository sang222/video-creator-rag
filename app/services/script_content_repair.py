"""One bounded, evidence-preserving repair after a verified content block."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.script_qualification import (
    QualifiedScriptOutput,
    QualifiedScriptOutputV2,
)
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m5 import EditorialIdeaCandidate, IdeaMarketPreflight
from app.db.models.script_qualification import (
    EditorialTopicDefinitionGateReceipt,
    ScriptContentRepairAuthorizationReceipt,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationRun,
)
from app.services.config_registry import content_hash
from app.services.canonical_script_compiler import CanonicalScriptCompiler
from app.services.production_start_readiness import resolve_budget_authority


CONTENT_REPAIR_PREFIX = "script-qualification-content-repair"
CONTENT_REPAIR_POLICY_VERSION = "script-content-repair.v1"
CONTENT_REPAIR_PROMPT_VERSION = "script-writer-content-repair.v2"
CONTENT_REPAIR_TYPE = "SCRIPT_CONTENT_REPAIR"
BACKGROUND_EVENT_TYPE = "script_qualification.background.execute.v1"
_V2_SINGLE_SOURCE = "V2_SINGLE_SOURCE"
_ScriptOutput = QualifiedScriptOutput | QualifiedScriptOutputV2


def content_repair_authorization_body(
    authorization: ScriptContentRepairAuthorizationReceipt,
) -> dict[str, Any]:
    """Return the immutable authorization fields covered by its hash."""

    return {
        "schema_version": "script-content-repair-authorization.v1",
        "source_qualification_run_id": str(authorization.source_qualification_run_id),
        "source_script_hash": authorization.source_script_hash,
        "source_result_receipts_hash": authorization.source_result_receipts_hash,
        "source_terminal_settlement_hash": (
            authorization.source_terminal_settlement_hash
        ),
        "script_assignment_hash": authorization.script_assignment_hash,
        "factual_evidence_pack_hash": authorization.factual_evidence_pack_hash,
        "memory_digest_hash": authorization.memory_digest_hash,
        "runtime_contract_hash": authorization.runtime_contract_hash,
        "affected_section_ids": list(authorization.affected_section_ids),
        "reason_codes": list(authorization.reason_codes),
        "repair_policy_version": authorization.repair_policy_version,
        "repair_type": authorization.repair_type,
        "compensation": authorization.compensation,
    }


class ScriptContentRepairService:
    """Authorize exactly one localized Luna repair against frozen authority."""

    def __init__(self, session: Session, *, now=utc_now) -> None:
        self.session = session
        self.now = now

    def authorize(
        self, *, source_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Compensate the deterministic block and queue one exact repair child.

        This is deliberately available only for the source that owns the
        content block.  It never creates a new candidate, slot, research run,
        or evidence authority.
        """

        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if source is None:
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_SOURCE_MISSING")
        authorization = self.session.scalar(
            select(ScriptContentRepairAuthorizationReceipt)
            .where(
                ScriptContentRepairAuthorizationReceipt.source_qualification_run_id
                == source.id
            )
            .with_for_update()
        )
        if authorization is not None:
            child = self.session.scalar(
                select(ScriptQualificationRun)
                .where(
                    ScriptQualificationRun.supersedes_qualification_run_id == source.id
                )
                .where(
                    ScriptQualificationRun.recovery_key
                    == f"{CONTENT_REPAIR_PREFIX}:{source.id}:{authorization.id}"
                )
                .with_for_update()
            )
            if child is None:
                raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_CHILD_MISSING")
            return child

        draft, terminal = self._validate_source(source)
        affected_sections, reason_codes = self._affected_sections(source, draft)
        affected_information_units = self._affected_information_units(
            source, affected_sections
        )
        self._validate_current_authority(source)

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
        if slot is None or candidate is None:
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_LINEAGE_MISSING")
        if (
            slot.state != "CANCELED"
            or slot.reserved_candidate_id != candidate.id
            or slot.admitted_video_project_id is not None
            or candidate.stage != "REJECTED"
        ):
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_SETTLEMENT_DRIFT")

        compensation = {
            "schema_version": "script-content-repair-compensation.v1",
            "source_qualification_run_id": str(source.id),
            "prior_terminal_settlement_hash": terminal["content_hash"],
            "prior_slot_state": slot.state,
            "prior_candidate_stage": candidate.stage,
            "restored_slot_state": "QUALIFICATION_RESERVED",
            "restored_candidate_stage": "GREENLIT",
            "restored_at": self.now().isoformat(),
            "affected_information_unit_ids": affected_information_units,
        }
        body = {
            "schema_version": "script-content-repair-authorization.v1",
            "source_qualification_run_id": str(source.id),
            "source_script_hash": _canonical_script_hash(draft),
            "source_result_receipts_hash": content_hash(source.result_receipts),
            "source_terminal_settlement_hash": terminal["content_hash"],
            "script_assignment_hash": source.script_assignment_hash,
            "factual_evidence_pack_hash": source.factual_evidence_pack_hash,
            "memory_digest_hash": source.memory_digest_hash,
            "runtime_contract_hash": source.runtime_contract_hash,
            "affected_section_ids": affected_sections,
            "reason_codes": reason_codes,
            "repair_policy_version": CONTENT_REPAIR_POLICY_VERSION,
            "repair_type": CONTENT_REPAIR_TYPE,
            "compensation": compensation,
        }
        authorization = ScriptContentRepairAuthorizationReceipt(
            source_qualification_run_id=source.id,
            source_script_hash=body["source_script_hash"],
            source_result_receipts_hash=body["source_result_receipts_hash"],
            source_terminal_settlement_hash=body["source_terminal_settlement_hash"],
            script_assignment_hash=body["script_assignment_hash"],
            factual_evidence_pack_hash=body["factual_evidence_pack_hash"],
            memory_digest_hash=body["memory_digest_hash"],
            runtime_contract_hash=body["runtime_contract_hash"],
            affected_section_ids=affected_sections,
            reason_codes=reason_codes,
            repair_policy_version=CONTENT_REPAIR_POLICY_VERSION,
            repair_type=CONTENT_REPAIR_TYPE,
            compensation=compensation,
            authorization_hash=content_hash(body),
        )
        self.session.add(authorization)
        self.session.flush()

        recovery_key = f"{CONTENT_REPAIR_PREFIX}:{source.id}:{authorization.id}"
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
            writer_prompt_version=CONTENT_REPAIR_PROMPT_VERSION,
            verifier_prompt_version="script-semantic-verifier.v5",
            gate_policy_version=source.gate_policy_version,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=content_hash(
                {
                    "source_logical_identity_hash": source.logical_identity_hash,
                    "authorization_hash": authorization.authorization_hash,
                    "recovery_key": recovery_key,
                }
            ),
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=self.now(),
            logical_deadline_at=source.logical_deadline_at,
            state="RESERVED",
            writer_attempt_key=f"{recovery_key}:writer-content-repair",
            verifier_attempt_key=f"{recovery_key}:verifier",
            repair_attempts=1,
            script_contract_version=source.script_contract_version,
            replacement_authority_id=source.replacement_authority_id,
        )
        self.session.add(child)
        source.repair_attempts = 1
        slot.state = "QUALIFICATION_RESERVED"
        candidate.stage = "GREENLIT"
        candidate.reason_codes = [
            code
            for code in (candidate.reason_codes or [])
            if code != "SCRIPT_QUALIFICATION_TERMINAL_BLOCKED"
        ]
        self.session.flush()
        self._enqueue(child)
        return child

    def writer_context(self, run: ScriptQualificationRun) -> dict[str, Any] | None:
        """Return the frozen repair directive, or ``None`` for regular writes."""

        if run.repair_attempts != 1 or run.supersedes_qualification_run_id is None:
            return None
        source = self.session.get(
            ScriptQualificationRun, run.supersedes_qualification_run_id
        )
        authorization = self.session.scalar(
            select(ScriptContentRepairAuthorizationReceipt).where(
                ScriptContentRepairAuthorizationReceipt.source_qualification_run_id
                == run.supersedes_qualification_run_id
            )
        )
        if source is None or authorization is None or source.script_payload is None:
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_AUTHORITY_MISSING")
        original = _script_output(source)
        if (
            authorization.source_script_hash != _canonical_script_hash(original)
            or authorization.script_assignment_hash != run.script_assignment_hash
            or authorization.factual_evidence_pack_hash
            != run.factual_evidence_pack_hash
            or authorization.memory_digest_hash != run.memory_digest_hash
            or authorization.runtime_contract_hash != run.runtime_contract_hash
            or authorization.repair_type != CONTENT_REPAIR_TYPE
        ):
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_AUTHORITY_DRIFT")
        return {
            "repair_type": CONTENT_REPAIR_TYPE,
            "repair_authorization_id": str(authorization.id),
            "repair_authorization_hash": authorization.authorization_hash,
            "prior_qualified_script": original.model_dump(mode="json"),
            "prior_script_hash": authorization.source_script_hash,
            "affected_section_ids": list(authorization.affected_section_ids),
            "affected_information_unit_ids": list(
                (authorization.compensation or {}).get(
                    "affected_information_unit_ids", []
                )
            ),
            "gate_reason_codes": list(authorization.reason_codes),
            "requirements": [
                (
                    "Return the complete strict QualifiedScriptOutputV2."
                    if isinstance(original, QualifiedScriptOutputV2)
                    else "Return the complete strict QualifiedScriptOutput."
                ),
                "Preserve every section outside affected_section_ids byte-for-byte.",
                "Change only affected section narration and the claim inventory needed to bind it.",
                "Use only the frozen factual evidence pack; do not introduce external facts.",
                "Every claim_text must be one exact contiguous span copied from one section narration.",
                "Every claim evidence_span_id must exactly match an ID in the frozen factual evidence pack.",
                "Remove or rewrite any partially supported synthesis; do not relabel it as supported.",
                "Keep language, duration contract, editorial assignment, scope, and evidence identities unchanged.",
            ],
        }

    def validate_output_scope(
        self, run: ScriptQualificationRun, repaired: _ScriptOutput
    ) -> None:
        """Reject a paid repair that changes an untouched source section."""

        directive = self.writer_context(run)
        if directive is None:
            return
        source = self.session.get(
            ScriptQualificationRun, run.supersedes_qualification_run_id
        )
        assert source is not None and source.script_payload is not None
        original = _script_output(source)
        editable_claim_ids, removable_claim_ids = self._claim_repair_scope(
            source, original
        )
        if type(repaired) is not type(original):
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_CONTRACT_CHANGED")
        if repaired.language != original.language:
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_LANGUAGE_CHANGED")
        old_sections = original.sections
        new_sections = repaired.sections
        old_claims = {claim.claim_id: claim for claim in original.claims}
        new_claims = {claim.claim_id: claim for claim in repaired.claims}
        if len(new_claims) != len(repaired.claims) or not set(new_claims).issubset(
            old_claims
        ):
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_CLAIM_INVENTORY_SCOPE_CHANGED"
            )
        removed_claim_ids = set(old_claims) - set(new_claims)
        if not removed_claim_ids.issubset(removable_claim_ids):
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_CLAIM_INVENTORY_SCOPE_CHANGED"
            )
        for claim_id, new_claim in new_claims.items():
            old_claim = old_claims[claim_id]
            if claim_id not in editable_claim_ids:
                if new_claim.model_dump(mode="json") != old_claim.model_dump(
                    mode="json"
                ):
                    raise ValidationFailureError(
                        "SCRIPT_CONTENT_REPAIR_CLAIM_INVENTORY_SCOPE_CHANGED"
                    )
            elif claim_id not in removable_claim_ids and (
                new_claim.evidence_span_ids != old_claim.evidence_span_ids
            ):
                raise ValidationFailureError(
                    "SCRIPT_CONTENT_REPAIR_CLAIM_EVIDENCE_SCOPE_CHANGED"
                )
        repaired_claim_ids = {claim.claim_id for claim in repaired.claims}
        if len(old_sections) != len(new_sections):
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_SECTION_COUNT_CHANGED")
        allowed = set(directive["affected_section_ids"])
        for old, new in zip(old_sections, new_sections, strict=True):
            if old.section_id != new.section_id:
                raise ValidationFailureError(
                    "SCRIPT_CONTENT_REPAIR_SECTION_IDENTITY_CHANGED"
                )
            if isinstance(original, QualifiedScriptOutput):
                if old.heading != new.heading:
                    raise ValidationFailureError(
                        "SCRIPT_CONTENT_REPAIR_SECTION_IDENTITY_CHANGED"
                    )
            elif (
                old.ordinal != new.ordinal
                or old.purpose != new.purpose
                or old.required_assignment_unit_refs
                != new.required_assignment_unit_refs
            ):
                raise ValidationFailureError(
                    "SCRIPT_CONTENT_REPAIR_SECTION_IDENTITY_CHANGED"
                )
            if old.section_id not in allowed and old.narration != new.narration:
                raise ValidationFailureError(
                    "SCRIPT_CONTENT_REPAIR_OUT_OF_SCOPE_SECTION_CHANGED"
                )
            retained_expected = [
                claim_id
                for claim_id in old.expected_claim_refs
                if claim_id in repaired_claim_ids
            ]
            if new.expected_claim_refs != retained_expected:
                raise ValidationFailureError(
                    "SCRIPT_CONTENT_REPAIR_CLAIM_INVENTORY_SCOPE_CHANGED"
                )

    def claim_repair_scope(
        self, run: ScriptQualificationRun
    ) -> tuple[list[str], list[str]]:
        """Expose the receipt-derived claim boundary for reuse receipts/tests."""

        source = self.session.get(
            ScriptQualificationRun, run.supersedes_qualification_run_id
        )
        if source is None or source.script_payload is None:
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_AUTHORITY_MISSING")
        editable, removable = self._claim_repair_scope(source, _script_output(source))
        return sorted(editable), sorted(removable)

    def _claim_repair_scope(
        self, source: ScriptQualificationRun, original: _ScriptOutput
    ) -> tuple[set[str], set[str]]:
        """Derive mutable/removable claims only from sealed verifier evidence."""

        attempt = self.session.scalar(
            select(ScriptQualificationBackgroundAttempt).where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == source.id,
                ScriptQualificationBackgroundAttempt.phase == "VERIFIER",
            )
        )
        snapshot = (
            self.session.scalar(
                select(ScriptQualificationProviderResponseSnapshot).where(
                    ScriptQualificationProviderResponseSnapshot.background_attempt_id
                    == attempt.id
                )
            )
            if attempt is not None
            else None
        )
        if snapshot is None or snapshot.validation_errors:
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_VERIFIER_EVIDENCE_MISSING"
            )
        try:
            verifier = json.loads(snapshot.raw_output_content)
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_VERIFIER_EVIDENCE_INVALID"
            ) from exc
        claim_ids = {claim.claim_id for claim in original.claims}
        narration = "\n\n".join(section.narration for section in original.sections)
        editable = {
            claim.claim_id
            for claim in original.claims
            if narration.count(claim.claim_text) != 1
        }
        removable: set[str] = set()
        for observation in verifier.get("material_claim_inventory", []):
            if not isinstance(observation, dict):
                continue
            claim_id = observation.get("writer_declared_claim_id")
            if claim_id not in claim_ids:
                continue
            if observation.get(
                "semantic_relation"
            ) != "ENTAILED" or not observation.get("factual_evidence_span_ids"):
                editable.add(claim_id)
                removable.add(claim_id)
        return editable, removable

    def seal_exhausted(
        self, *, repair_qualification_run_id: uuid.UUID
    ) -> ScriptQualificationRun:
        """Persist the terminal no-second-call decision for a bad repair body."""

        run = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == repair_qualification_run_id)
            .with_for_update()
        )
        if (
            run is None
            or run.state != "BLOCKED_NON_REPAIRABLE"
            or run.repair_attempts != 1
            or run.script_payload is None
            or not isinstance(run.result_receipts, dict)
        ):
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_EXHAUSTION_RUN_INVALID")
        structural = run.result_receipts.get("structural")
        if not isinstance(structural, dict) or structural.get("status") != "BLOCK":
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_EXHAUSTION_NOT_STRUCTURAL"
            )
        attempt = self.session.scalar(
            select(ScriptQualificationBackgroundAttempt)
            .where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == run.id,
                ScriptQualificationBackgroundAttempt.phase == "WRITER",
            )
            .with_for_update()
        )
        snapshot = (
            self.session.scalar(
                select(ScriptQualificationProviderResponseSnapshot).where(
                    ScriptQualificationProviderResponseSnapshot.background_attempt_id
                    == (attempt.id if attempt is not None else None)
                )
            )
            if attempt is not None
            else None
        )
        if (
            attempt is None
            or attempt.submission_attempt_count != 1
            or attempt.background_status != "COMPLETED"
            or not attempt.provider_response_id
            or snapshot is None
            or snapshot.validation_errors
            or not snapshot.accepted_typed_output_hash
        ):
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_EXHAUSTION_EVIDENCE_INVALID"
            )
        failure = dict(run.failure_receipt or {})
        if "SCRIPT_OUTPUT_CONTRACT_REPAIR_EXHAUSTED" in (
            failure.get("reason_codes") or []
        ):
            return run
        body = {
            "schema_version": "script-content-repair-exhaustion.v1",
            "repair_qualification_run_id": str(run.id),
            "repair_type": CONTENT_REPAIR_TYPE,
            "writer_submission_count": attempt.submission_attempt_count,
            "provider_response_id": attempt.provider_response_id,
            "provider_request_id": attempt.provider_request_id,
            "provider_output_hash": attempt.output_hash,
            "snapshot_id": str(snapshot.id),
            "snapshot_raw_output_hash": snapshot.raw_output_hash,
            "accepted_typed_output_hash": snapshot.accepted_typed_output_hash,
            "structural_receipt": structural,
            "normalization_permitted": False,
            "normalization_reason": "CANONICAL_SCRIPT_AND_SECTION_NARRATION_DIVERGE",
            "further_writer_submission_permitted": False,
            "reason_code": "SCRIPT_OUTPUT_CONTRACT_REPAIR_EXHAUSTED",
            "sealed_at": self.now().isoformat(),
        }
        exhaustion = {**body, "content_hash": content_hash(body)}
        failure["reason_codes"] = list(
            dict.fromkeys(
                [
                    *(failure.get("reason_codes") or []),
                    "SCRIPT_OUTPUT_CONTRACT_REPAIR_EXHAUSTED",
                ]
            )
        )
        failure["content_repair_exhaustion_receipt"] = exhaustion
        run.failure_receipt = failure
        self.session.flush()
        return run

    def _validate_source(
        self, source: ScriptQualificationRun
    ) -> tuple[_ScriptOutput, dict[str, Any]]:
        if (
            source.state != "BLOCKED_NON_REPAIRABLE"
            or source.repair_attempts != 0
            or source.script_payload is None
            or not isinstance(source.result_receipts, dict)
            or not isinstance(source.terminal_settlement_receipt, dict)
        ):
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_SOURCE_NOT_ELIGIBLE")
        draft = _script_output(source)
        terminal = dict(source.terminal_settlement_receipt)
        terminal_body = {
            key: value for key, value in terminal.items() if key != "content_hash"
        }
        if (
            terminal.get("kind") != "DETERMINISTIC_BLOCK"
            or terminal.get("reason_code") != "SCRIPT_QUALIFICATION_BLOCKED"
            or terminal.get("qualification_run_id") != str(source.id)
            or terminal.get("content_hash") != content_hash(terminal_body)
            or source.admitted_video_project_id is not None
            or source.production_workflow_run_id is not None
        ):
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_TERMINAL_AUTHORITY_INVALID"
            )
        attempts = self.session.scalars(
            select(ScriptQualificationBackgroundAttempt).where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == source.id,
                ScriptQualificationBackgroundAttempt.phase == "VERIFIER",
            )
        ).all()
        if len(attempts) != 1 or attempts[0].background_status != "COMPLETED":
            raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_VERIFIER_NOT_COMPLETED")
        return draft, terminal

    def _affected_sections(
        self, source: ScriptQualificationRun, draft: _ScriptOutput
    ) -> tuple[list[str], list[str]]:
        snapshot = self.session.scalar(
            select(ScriptQualificationProviderResponseSnapshot)
            .join(
                ScriptQualificationBackgroundAttempt,
                ScriptQualificationProviderResponseSnapshot.background_attempt_id
                == ScriptQualificationBackgroundAttempt.id,
            )
            .where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == source.id,
                ScriptQualificationBackgroundAttempt.phase == "VERIFIER",
            )
        )
        if snapshot is None or snapshot.validation_errors:
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_VERIFIER_EVIDENCE_MISSING"
            )
        try:
            output = json.loads(snapshot.raw_output_content)
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_VERIFIER_EVIDENCE_INVALID"
            ) from exc
        allowed = {section.section_id for section in draft.sections}
        affected: set[str] = set()
        for observation in output.get("material_claim_inventory", []):
            if (
                not isinstance(observation, dict)
                or observation.get("materiality_state") != "MATERIAL"
            ):
                continue
            if (
                not observation.get("writer_declared_claim_id")
                or observation.get("semantic_relation") != "ENTAILED"
                or not observation.get("factual_evidence_span_ids")
            ):
                span = observation.get("span")
                section_id = span.get("section_id") if isinstance(span, dict) else None
                if isinstance(section_id, str) and section_id in allowed:
                    affected.add(section_id)
        if not affected:
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_AFFECTED_SECTIONS_EMPTY"
            )
        reason_codes = sorted(
            {
                str(code)
                for receipt in (source.result_receipts or {}).values()
                if isinstance(receipt, dict)
                for code in (receipt.get("reason_codes") or [])
                if str(code).startswith("SCRIPT_")
            }
        )
        return sorted(affected), reason_codes

    @staticmethod
    def _affected_information_units(
        source: ScriptQualificationRun, affected_sections: list[str]
    ) -> list[str]:
        """Project repair scope to frozen information-unit ownership when present."""

        assignment = source.script_assignment or {}
        raw_plan = assignment.get("section_coverage_plan")
        if not isinstance(raw_plan, dict):
            return []
        units: list[str] = []
        for section in raw_plan.get("sections") or []:
            if (
                not isinstance(section, dict)
                or section.get("section_id") not in affected_sections
            ):
                continue
            units.extend(
                str(item)
                for item in section.get("owned_information_unit_ids") or []
                if str(item).strip()
            )
        return sorted(set(units))

    def _validate_current_authority(self, source: ScriptQualificationRun) -> None:
        now = self.now()
        from app.services.script_qualification_background import (
            build_script_qualification_deadline_policy,
        )

        deadline_policy = build_script_qualification_deadline_policy()
        candidate = self.session.get(
            EditorialIdeaCandidate, source.editorial_idea_candidate_id
        )
        slot = self.session.get(LongFormPublishSlot, source.publish_slot_id)
        topic = self.session.scalar(
            select(EditorialTopicDefinitionGateReceipt)
            .where(
                EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
                == source.topic_definition_id
            )
            .order_by(EditorialTopicDefinitionGateReceipt.created_at.desc())
        )
        preflight = self.session.scalar(
            select(IdeaMarketPreflight)
            .where(
                IdeaMarketPreflight.editorial_idea_candidate_id
                == source.editorial_idea_candidate_id
            )
            .order_by(IdeaMarketPreflight.created_at.desc())
        )
        budget = (
            resolve_budget_authority(
                self.session,
                policy_snapshot_id=candidate.policy_snapshot_id,
                channel_workspace_id=candidate.channel_workspace_id,
            )
            if candidate is not None
            else {"state": "BLOCKED"}
        )
        reasons: list[str] = []
        if source.logical_deadline_at is None or now >= source.logical_deadline_at:
            reasons.append("SCRIPT_CONTENT_REPAIR_DEADLINE_EXCEEDED")
        elif (
            now + timedelta(seconds=deadline_policy.total_qualification_budget_seconds)
            >= source.logical_deadline_at
        ):
            reasons.append("SCRIPT_CONTENT_REPAIR_DEADLINE_NOT_VIABLE")
        if slot is None or not (
            slot.target_start_window_open_at <= now <= slot.target_start_window_close_at
        ):
            reasons.append("SCRIPT_CONTENT_REPAIR_WINDOW_CLOSED")
        if (
            topic is None
            or topic.state != "PASS"
            or not topic.current_production_eligibility
        ):
            reasons.append("SCRIPT_CONTENT_REPAIR_TOPIC_NOT_CURRENT")
        if (
            preflight is None
            or preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
        ):
            reasons.append("SCRIPT_CONTENT_REPAIR_PREFLIGHT_NOT_CURRENT")
        if budget.get("state") != "READY":
            reasons.append("SCRIPT_CONTENT_REPAIR_BUDGET_NOT_READY")
        if reasons:
            raise ValidationFailureError(
                "SCRIPT_CONTENT_REPAIR_AUTHORITY_BLOCKED:" + ",".join(reasons)
            )

    def _enqueue(self, run: ScriptQualificationRun) -> None:
        command_id = f"{run.recovery_key}:writer-content-repair"
        payload = {
            "script_qualification_run_id": str(run.id),
            "background_attempt_id": None,
            "repair_type": CONTENT_REPAIR_TYPE,
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
                    "repair_type": CONTENT_REPAIR_TYPE,
                    "writer_submission_limit": 1,
                    "retry_policy": {
                        "automatic_retry_allowed": False,
                        "provider_substitution_allowed": False,
                    },
                },
                attempt_count=0,
                max_attempts=1,
                next_attempt_at=self.now(),
                occurred_at=self.now(),
            )
        )


def _script_output(source: ScriptQualificationRun) -> _ScriptOutput:
    """Read the source contract without coercing V2 into legacy prose."""

    if not isinstance(source.script_payload, dict):
        raise ValidationFailureError("SCRIPT_CONTENT_REPAIR_SOURCE_PAYLOAD_INVALID")
    if source.script_contract_version == _V2_SINGLE_SOURCE:
        return QualifiedScriptOutputV2.model_validate(source.script_payload)
    return QualifiedScriptOutput.model_validate(source.script_payload)


def _canonical_script_hash(output: _ScriptOutput) -> str:
    if isinstance(output, QualifiedScriptOutputV2):
        return CanonicalScriptCompiler.compile(output).canonical_script_hash
    import hashlib
    import unicodedata

    canonical = (
        unicodedata.normalize("NFC", output.canonical_script)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
