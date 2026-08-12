"""Append-only settlement of one exact completed verifier artifact.

The provider output remains immutable.  This module only derives a narrower,
versioned local projection when every provider-selected span and evidence
binding proves that the prior block was caused by deterministic policy-v2
ownership/lexical rules rather than by a substantive quality failure.
"""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.script_qualification import SemanticVerificationOutput
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
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
    ControlledVerifierSettlementAuthority,
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationRun,
    ScriptContractReplacementAuthority,
)
from app.services.config_registry import content_hash
from app.services.editorial_novelty import (
    EDITORIAL_NOVELTY_GATE_VERSION,
    EditorialNoveltyService,
)
from app.services.editorial_specificity import EditorialSpecificityService
from app.services.launch_cadence import _preflight_demand_authority_valid
from app.services.production_start_readiness import (
    resolve_budget_authority,
    resolve_provider_authority,
)
from app.services.script_contract_replacement import (
    OPERATOR_RECOVERY_REASON,
    OPERATOR_RECOVERY_SCHEMA,
    ScriptContractReplacementAuthorityService,
    controlled_continuation_authority_body,
    controlled_continuation_slot_projection,
    controlled_verifier_settlement_authority_body,
    operator_recovery_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.script_qualification import (
    TOPIC_GATE_VERSION,
    ScriptQualificationService,
)
from app.services.script_qualification_background import (
    build_script_qualification_deadline_policy,
    derive_script_qualification_deadline,
    minimum_script_qualification_window_close_at,
    script_qualification_slot_is_viable,
)


SETTLEMENT_SCHEMA = "vcos.controlled-verifier-settlement.v1"
SETTLEMENT_REASON = "EXACT_VERIFIER_ARTIFACT_POLICY_PROJECTION"
SETTLEMENT_POLICY_VERSION = "script-qualification-policy.v3"
SETTLEMENT_RECOVERY_PREFIX = "script-qualification-verifier-settlement"
SETTLEMENT_SLOT_REASON = "EXACT_VERIFIER_ARTIFACT_POLICY_PROJECTION"


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def derive_v3_semantic_receipts(
    *,
    service: ScriptQualificationService,
    run: ScriptQualificationRun,
    draft: Any,
    verifier: SemanticVerificationOutput,
    source_verifier_output_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive the exact policy-v3 PASS view without changing provider rows."""

    structural = service._structural_receipt(run, draft)
    base = service._semantic_receipts(run, draft, verifier, structural)
    if (
        structural.get("status") != "PASS"
        or run.result_receipts != base
        or base.get("inventory", {}).get("status") != "BLOCK"
        or base.get("inventory", {}).get("reason_codes")
        != ["SCRIPT_WRITER_CLAIM_SPAN_MISMATCH"]
        or base.get("grounding", {}).get("reason_codes")
        != ["SCRIPT_CLAIM_GROUNDING_PASS"]
        or base.get("fulfillment", {}).get("status") != "BLOCK"
        or base.get("fulfillment", {}).get("reason_codes")
        != ["SCRIPT_ASSIGNMENT_COVERAGE_SPAN_REUSED"]
        or base.get("memory", {}).get("status") not in {"PASS", "PASS_EMPTY"}
    ):
        raise ValidationFailureError("VERIFIER_SETTLEMENT_SOURCE_NOT_EXACT")

    writer_claims = {item.claim_id: item for item in draft.claims}
    raw_sections = (run.script_payload or {}).get("sections")
    if not isinstance(raw_sections, list):
        raise ValidationFailureError("VERIFIER_SETTLEMENT_SECTION_CLAIMS_INVALID")
    expected_claims_by_section: dict[str, set[str]] = {}
    for section in raw_sections:
        section_id = section.get("section_id") if isinstance(section, dict) else None
        claim_refs = (
            section.get("expected_claim_refs") if isinstance(section, dict) else None
        )
        if (
            not isinstance(section_id, str)
            or not section_id
            or section_id in expected_claims_by_section
            or not isinstance(claim_refs, list)
            or any(not isinstance(value, str) or not value for value in claim_refs)
            or len(claim_refs) != len(set(claim_refs))
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_SECTION_CLAIMS_INVALID")
        expected_claims_by_section[section_id] = set(claim_refs)
    if set(expected_claims_by_section) != {section.section_id for section in draft.sections}:
        raise ValidationFailureError("VERIFIER_SETTLEMENT_SECTION_CLAIMS_INVALID")
    frozen_evidence_ids = {
        str(item.get("evidence_span_id"))
        for item in (run.factual_evidence_pack or {}).get("spans", [])
        if item.get("evidence_span_id")
    }
    decisions: list[dict[str, Any]] = []
    for observation in verifier.material_claim_inventory:
        if observation.materiality_state != "MATERIAL":
            continue
        claim = writer_claims.get(str(observation.writer_declared_claim_id or ""))
        if claim is None:
            raise ValidationFailureError("VERIFIER_SETTLEMENT_CLAIM_UNDECLARED")
        if _fold(claim.claim_text) in _fold(observation.span.text) or _fold(
            observation.span.text
        ) in _fold(claim.claim_text):
            continue
        evidence_ids = set(observation.factual_evidence_span_ids)
        if (
            observation.claim_type
            in {"NON_FACTUAL_OPINION_OR_FRAMING", "STRUCTURAL_TRANSITION"}
            or observation.semantic_relation != "ENTAILED"
            or not observation.assignment_requirement_ids
            or evidence_ids != set(claim.evidence_span_ids)
            or not evidence_ids
            or not evidence_ids <= frozen_evidence_ids
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_CLAIM_NOT_ENTAILED")
        anchors = [
            item
            for item in verifier.material_claim_inventory
            if item.observed_claim_id != observation.observed_claim_id
            and item.materiality_state == "MATERIAL"
            and item.writer_declared_claim_id == claim.claim_id
            and item.semantic_relation == "ENTAILED"
            and item.claim_type
            not in {"NON_FACTUAL_OPINION_OR_FRAMING", "STRUCTURAL_TRANSITION"}
            and item.span.text == claim.claim_text
            and set(item.factual_evidence_span_ids) == set(claim.evidence_span_ids)
            and bool(item.assignment_requirement_ids)
        ]
        canonical_occurrences = sum(
            section.narration.count(claim.claim_text) for section in draft.sections
        )
        if (
            len(anchors) != 1
            or canonical_occurrences != 1
            or claim.claim_id
            not in expected_claims_by_section.get(observation.span.section_id, set())
            or claim.claim_id
            not in expected_claims_by_section.get(anchors[0].span.section_id, set())
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_EXACT_ANCHOR_INVALID")
        decisions.append(
            {
                "observed_claim_id": observation.observed_claim_id,
                "writer_declared_claim_id": claim.claim_id,
                "anchor_observed_claim_id": anchors[0].observed_claim_id,
                "evidence_span_ids": sorted(evidence_ids),
                "semantic_relation": "ENTAILED",
            }
        )
    if not decisions:
        raise ValidationFailureError("VERIFIER_SETTLEMENT_NO_CLAIM_DECISION")

    projected_payload = verifier.model_dump(mode="json")
    observations = projected_payload["assignment_fulfillment_observations"]
    section_owners = {
        str(item.get("section_id")): set(item.get("primary_requirement_ids") or [])
        for item in (run.script_assignment or {})
        .get("section_coverage_plan", {})
        .get("sections", [])
    }
    selections: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in observations:
        for span in observation.get("spans") or []:
            selections.setdefault(
                (str(span.get("section_id")), str(span.get("text"))), []
            ).append(observation)
    removed: list[dict[str, Any]] = []
    for (section_id, text), users in selections.items():
        requirement_ids = {str(item["requirement_id"]) for item in users}
        if len(requirement_ids) < 2:
            continue
        owners = requirement_ids & section_owners.get(section_id, set())
        if len(owners) != 1:
            raise ValidationFailureError("VERIFIER_SETTLEMENT_SPAN_OWNER_AMBIGUOUS")
        owner = next(iter(owners))
        for item in users:
            requirement_id = str(item["requirement_id"])
            if requirement_id == owner:
                continue
            retained = [
                span
                for span in item.get("spans") or []
                if not (
                    span.get("section_id") == section_id
                    and span.get("text") == text
                )
            ]
            if not retained or item.get("status") != "SUFFICIENT":
                raise ValidationFailureError(
                    "VERIFIER_SETTLEMENT_REQUIREMENT_WOULD_BE_UNFULFILLED"
                )
            item["spans"] = retained
            removed.append(
                {
                    "requirement_id": requirement_id,
                    "retained_owner_requirement_id": owner,
                    "section_id": section_id,
                    "text": text,
                }
            )
    if not removed:
        raise ValidationFailureError("VERIFIER_SETTLEMENT_NO_SPAN_PROJECTION")

    projected_verifier = SemanticVerificationOutput.model_validate(projected_payload)
    receipts = service._semantic_receipts(
        run, draft, projected_verifier, structural
    )
    if (
        receipts["inventory"].get("reason_codes")
        != ["SCRIPT_WRITER_CLAIM_SPAN_MISMATCH"]
        or receipts["fulfillment"].get("status") != "PASS"
        or receipts["grounding"].get("reason_codes")
        != ["SCRIPT_CLAIM_GROUNDING_PASS"]
    ):
        raise ValidationFailureError("VERIFIER_SETTLEMENT_DERIVED_VIEW_INVALID")
    receipts["inventory"] = {
        **receipts["inventory"],
        "status": "PASS",
        "reason_codes": ["SCRIPT_MATERIAL_CLAIM_INVENTORY_PASS_V3"],
        "settlement_policy_version": SETTLEMENT_POLICY_VERSION,
        "exact_anchor_decision_count": len(decisions),
    }
    receipts["grounding"] = {
        **receipts["grounding"],
        "status": "PASS",
        "reason_codes": ["SCRIPT_CLAIM_GROUNDING_PASS"],
    }
    if not all(
        item.get("status") in {"PASS", "PASS_EMPTY"} for item in receipts.values()
    ):
        raise ValidationFailureError("VERIFIER_SETTLEMENT_DERIVED_RESULT_BLOCKED")
    projection_body = {
        "schema_version": "script-verifier-settlement-projection.v1",
        "policy_version": SETTLEMENT_POLICY_VERSION,
        "source_qualification_run_id": str(run.id),
        "source_verifier_output_hash": source_verifier_output_hash,
        "source_result_receipts_hash": content_hash(run.result_receipts or {}),
        "claim_anchor_decisions": decisions,
        "removed_fulfillment_spans": removed,
        "resulting_receipts_hash": content_hash(receipts),
    }
    projection = {**projection_body, "content_hash": content_hash(projection_body)}
    return receipts, projection


class ScriptVerifierSettlementRecoveryService:
    """Create one fresh-slot QUALIFIED child without a provider submission."""

    def __init__(self, session: Session, *, now=utc_now) -> None:
        self.session = session
        self.now = now

    def create(self, *, source_qualification_run_id: uuid.UUID) -> ScriptQualificationRun:
        source = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == source_qualification_run_id)
            .with_for_update()
        )
        if source is None:
            raise ValidationFailureError("VERIFIER_SETTLEMENT_SOURCE_MISSING")
        existing = self.session.scalar(
            select(ControlledVerifierSettlementAuthority).where(
                ControlledVerifierSettlementAuthority.source_qualification_run_id
                == source.id
            )
        )
        if existing is not None:
            child = self.session.get(
                ScriptQualificationRun, existing.settlement_qualification_run_id
            )
            root_authority = self.session.get(
                ScriptContractReplacementAuthority,
                existing.root_replacement_authority_id,
            )
            child_attempt_count = self.session.scalar(
                select(func.count())
                .select_from(ScriptQualificationBackgroundAttempt)
                .where(
                    ScriptQualificationBackgroundAttempt.script_qualification_run_id
                    == existing.settlement_qualification_run_id
                )
            )
            if (
                child is None
                or root_authority is None
                or child.state != "QUALIFIED"
                or child.supersedes_qualification_run_id != source.id
                or existing.derived_projection_hash
                != content_hash(
                    {
                        key: value
                        for key, value in existing.derived_projection.items()
                        if key != "content_hash"
                    }
                )
                or existing.derived_projection.get("content_hash")
                != existing.derived_projection_hash
                or existing.authority_hash
                != content_hash(controlled_verifier_settlement_authority_body(existing))
                or child_attempt_count != 0
                or resolve_replacement_qualification_leaf(
                    self.session, authority=root_authority, lock=True
                ).id
                != child.id
            ):
                raise ValidationFailureError("VERIFIER_SETTLEMENT_REPLAY_DRIFT")
            return child
        current = self.now()
        source_slot = self.session.get(LongFormPublishSlot, source.publish_slot_id)
        candidate = self.session.get(
            EditorialIdeaCandidate, source.editorial_idea_candidate_id
        )
        root = self.session.get(
            ScriptContractReplacementAuthority, source.replacement_authority_id
        )
        continuation = self.session.scalar(
            select(ControlledProductionContinuationAuthority).where(
                ControlledProductionContinuationAuthority.continuation_qualification_run_id
                == source.id
            )
        )
        terminal = (
            source.terminal_settlement_receipt
            if isinstance(source.terminal_settlement_receipt, dict)
            else {}
        )
        terminal_body = {k: v for k, v in terminal.items() if k != "content_hash"}
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
        admission = (
            self.session.scalar(
                select(ProjectAdmissionDecision.id).where(
                    ProjectAdmissionDecision.editorial_idea_candidate_id == candidate.id,
                    ProjectAdmissionDecision.decision == "ADMIT",
                )
            )
            if candidate is not None
            else None
        )
        if (
            source.state != "BLOCKED_NON_REPAIRABLE"
            or source.script_contract_version != "V2_SINGLE_SOURCE"
            or source.gate_policy_version != "script-qualification-policy.v2"
            or source.script_payload is None
            or source.canonical_script_artifact_id is None
            or source.result_receipts is None
            or source_slot is None
            or source_slot.state != "CANCELED"
            or candidate is None
            or candidate.stage != "REJECTED"
            or root is None
            or root.replacement_reason != OPERATOR_RECOVERY_REASON
            or root.operator_recovery_schema_version != OPERATOR_RECOVERY_SCHEMA
            or root.authority_hash != content_hash(operator_recovery_authority_body(root))
            or continuation is None
            or continuation.root_replacement_authority_id != root.id
            or continuation.source_qualification_run_id
            != source.supersedes_qualification_run_id
            or continuation.authority_hash
            != content_hash(controlled_continuation_authority_body(continuation))
            or terminal.get("kind") != "DETERMINISTIC_BLOCK"
            or terminal.get("reason_code") != "SCRIPT_QUALIFICATION_BLOCKED"
            or terminal.get("qualification_run_id") != str(source.id)
            or terminal.get("publish_slot_id") != str(source_slot.id)
            or terminal.get("candidate_id") != str(candidate.id)
            or terminal.get("publish_slot_state") != "CANCELED"
            or terminal.get("candidate_stage") != "REJECTED"
            or terminal.get("capacity_released") is not True
            or terminal.get("content_hash") != content_hash(terminal_body)
            or source.admitted_video_project_id is not None
            or source.production_workflow_run_id is not None
            or source.episode_reservation_active
            or active_workflow is not None
            or admission is not None
            or resolve_replacement_qualification_leaf(
                self.session, authority=root, lock=True
            ).id
            != source.id
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_SOURCE_DRIFT")

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
        if len(attempts) != 1 or len(snapshots) != 1:
            raise ValidationFailureError("VERIFIER_SETTLEMENT_PROVIDER_LINEAGE_DRIFT")
        attempt, snapshot = attempts[0], snapshots[0]
        try:
            raw_verifier = json.loads(snapshot.raw_output_content)
            verifier = SemanticVerificationOutput.model_validate(raw_verifier)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationFailureError("VERIFIER_SETTLEMENT_OUTPUT_INVALID") from exc
        if (
            attempt.phase != "VERIFIER"
            or attempt.background_status != "COMPLETED"
            or attempt.submission_attempt_count != 1
            or not attempt.provider_response_id
            or not attempt.provider_request_id
            or attempt.output_hash != content_hash(snapshot.raw_provider_response)
            or snapshot.background_attempt_id != attempt.id
            or snapshot.phase != "VERIFIER"
            or snapshot.provider_response_id != attempt.provider_response_id
            or snapshot.provider_request_id != attempt.provider_request_id
            or snapshot.prompt_version != source.verifier_prompt_version
            or snapshot.prompt_version != attempt.prompt_version
            or snapshot.response_schema_identifier != attempt.response_schema_identifier
            or snapshot.response_schema_hash != attempt.response_schema_hash
            or snapshot.producer_input_hash != attempt.input_fingerprint
            or snapshot.validation_errors
            or snapshot.raw_provider_response_hash
            != content_hash(snapshot.raw_provider_response)
            or snapshot.raw_output_hash
            != content_hash({"content": snapshot.raw_output_content})
            or snapshot.accepted_typed_output_hash
            != content_hash(verifier.model_dump(mode="json"))
            or not isinstance(source.verifier_receipt, dict)
            or source.verifier_receipt.get("provider_response_id")
            != attempt.provider_response_id
            or source.verifier_receipt.get("provider_request_id")
            != attempt.provider_request_id
            or source.verifier_receipt.get("output_hash") != attempt.output_hash
            or source.verifier_receipt.get("input_fingerprint")
            != attempt.input_fingerprint
            or source.verifier_receipt.get("prompt_version") != attempt.prompt_version
            or source.verifier_receipt.get("response_schema_identifier")
            != attempt.response_schema_identifier
            or source.verifier_receipt.get("response_schema_hash")
            != attempt.response_schema_hash
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_SNAPSHOT_HASH_MISMATCH")

        service = ScriptQualificationService(self.session)
        draft = service.draft_from_run(source)
        receipts, projection = derive_v3_semantic_receipts(
            service=service,
            run=source,
            draft=draft,
            verifier=verifier,
            source_verifier_output_hash=snapshot.accepted_typed_output_hash,
        )
        launch = self.session.get(LaunchRun, source.launch_run_id)
        launch_policy = (
            self.session.get(
                FirstChannelLaunchPolicyVersion, launch.launch_policy_version_id
            )
            if launch is not None
            else None
        )
        topic_receipt = self.session.scalar(
            select(EditorialTopicDefinitionGateReceipt)
            .where(
                EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
                == source.topic_definition_id,
                EditorialTopicDefinitionGateReceipt.gate_version == TOPIC_GATE_VERSION,
            )
            .order_by(EditorialTopicDefinitionGateReceipt.created_at.desc())
        )
        topic = self.session.get(EditorialTopicDefinition, source.topic_definition_id)
        preflight = self.session.scalar(
            select(IdeaMarketPreflight)
            .where(IdeaMarketPreflight.editorial_idea_candidate_id == candidate.id)
            .order_by(IdeaMarketPreflight.created_at.desc())
        )
        novelty = (
            EditorialNoveltyService(self.session).evaluate(candidate=candidate, topic=topic)
            if topic is not None
            else None
        )
        freshness = ScriptContractReplacementAuthorityService(
            self.session, now=self.now
        )._current_freshness_snapshot(candidate=candidate, evaluated_at=current)
        provider = resolve_provider_authority(
            self.session,
            policy_snapshot_id=candidate.policy_snapshot_id,
            channel_workspace_id=candidate.channel_workspace_id,
        )
        budget = resolve_budget_authority(
            self.session,
            policy_snapshot_id=candidate.policy_snapshot_id,
            channel_workspace_id=candidate.channel_workspace_id,
        )
        if (
            launch is None
            or launch_policy is None
            or launch.state != "ACTIVE"
            or launch_policy.state != "APPROVED"
            or topic_receipt is None
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
            or provider.get("state") != "READY"
            or budget.get("state") != "READY"
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_CURRENT_AUTHORITY_BLOCKED")

        policy = build_script_qualification_deadline_policy()
        slot_close = minimum_script_qualification_window_close_at(
            requested_at=current, policy=policy
        ) + timedelta(seconds=1)
        deadline = derive_script_qualification_deadline(slot_close, policy)
        if not script_qualification_slot_is_viable(
            now=current, slot_window_close_at=slot_close, policy=policy
        ):
            raise ValidationFailureError("VERIFIER_SETTLEMENT_SLOT_NOT_VIABLE")
        authority_id, slot_id, child_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        recovery_key = f"{SETTLEMENT_RECOVERY_PREFIX}:{source.id}:{snapshot.id}"
        logical_identity = content_hash(
            {
                "source_logical_identity_hash": source.logical_identity_hash,
                "settlement_authority_id": str(authority_id),
                "settlement_policy_version": SETTLEMENT_POLICY_VERSION,
                "derived_projection_hash": projection["content_hash"],
                "publish_slot_id": str(slot_id),
            }
        )
        intended_publish_at = slot_close + timedelta(
            hours=launch_policy.render_lead_time_min_hours
        )
        slot = LongFormPublishSlot(
            id=slot_id,
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
            replacement_authority_id=root.id,
            replacement_reason=SETTLEMENT_SLOT_REASON,
            replacement_lineage_key=content_hash(
                {
                    "settlement_authority_id": str(authority_id),
                    "source_slot_id": str(source_slot.id),
                    "candidate_id": str(candidate.id),
                }
            ),
        )
        child = ScriptQualificationRun(
            id=child_id,
            editorial_idea_candidate_id=source.editorial_idea_candidate_id,
            publish_slot_id=slot.id,
            launch_run_id=source.launch_run_id,
            topic_definition_id=source.topic_definition_id,
            topic_definition_hash=source.topic_definition_hash,
            script_assignment=deepcopy(source.script_assignment),
            script_assignment_hash=source.script_assignment_hash,
            factual_evidence_pack=deepcopy(source.factual_evidence_pack),
            factual_evidence_pack_hash=source.factual_evidence_pack_hash,
            memory_digest=deepcopy(source.memory_digest),
            memory_digest_hash=source.memory_digest_hash,
            runtime_contract=deepcopy(source.runtime_contract),
            runtime_contract_hash=source.runtime_contract_hash,
            assignment_resolution=deepcopy(source.assignment_resolution),
            assignment_resolution_hash=source.assignment_resolution_hash,
            episode_reservation_active=False,
            writer_prompt_version=source.writer_prompt_version,
            verifier_prompt_version=source.verifier_prompt_version,
            gate_policy_version=SETTLEMENT_POLICY_VERSION,
            model=source.model,
            logical_attempt_number=source.logical_attempt_number + 1,
            logical_identity_hash=logical_identity,
            supersedes_qualification_run_id=source.id,
            recovery_key=recovery_key,
            recovery_requested_at=current,
            logical_deadline_at=deadline,
            state="RESERVED",
            writer_attempt_key=f"{recovery_key}:zero-provider-writer",
            verifier_attempt_key=f"{recovery_key}:zero-provider-verifier",
            repair_attempts=source.repair_attempts,
            script_contract_version=source.script_contract_version,
            replacement_authority_id=root.id,
        )
        self.session.add_all([slot, child])
        self.session.flush()
        child_draft = service.accept_writer_output(child, deepcopy(source.script_payload))
        if service._structural_receipt(child, child_draft).get("status") != "PASS":
            raise ValidationFailureError("VERIFIER_SETTLEMENT_CHILD_STRUCTURAL_BLOCK")

        authority = ControlledVerifierSettlementAuthority(
            id=authority_id,
            root_replacement_authority_id=root.id,
            source_continuation_authority_id=continuation.id,
            source_qualification_run_id=source.id,
            source_slot_id=source_slot.id,
            settlement_candidate_id=candidate.id,
            settlement_slot_id=slot.id,
            settlement_qualification_run_id=child.id,
            source_verifier_attempt_id=attempt.id,
            source_verifier_snapshot_id=snapshot.id,
            canonical_script_artifact_id=child.canonical_script_artifact_id,
            schema_version=SETTLEMENT_SCHEMA,
            settlement_reason=SETTLEMENT_REASON,
            settlement_policy_version=SETTLEMENT_POLICY_VERSION,
            root_authority_hash=root.authority_hash,
            source_continuation_authority_hash=continuation.authority_hash,
            source_logical_identity_hash=source.logical_identity_hash,
            settlement_logical_identity_hash=child.logical_identity_hash,
            source_terminal_settlement_hash=terminal["content_hash"],
            source_script_hash=str(source.derived_canonical_script_hash),
            source_result_receipts_hash=content_hash(source.result_receipts),
            source_verifier_input_hash=attempt.input_fingerprint,
            source_verifier_response_id=str(attempt.provider_response_id),
            source_verifier_request_id=str(attempt.provider_request_id),
            source_verifier_raw_response_hash=snapshot.raw_provider_response_hash,
            source_verifier_raw_output_hash=snapshot.raw_output_hash,
            source_verifier_typed_output_hash=snapshot.accepted_typed_output_hash,
            source_verifier_schema_identifier=snapshot.response_schema_identifier,
            source_verifier_schema_hash=snapshot.response_schema_hash,
            source_verifier_prompt_version=snapshot.prompt_version,
            derived_projection=projection,
            derived_projection_hash=projection["content_hash"],
            deadline_policy=policy.receipt(),
            slot_projection=controlled_continuation_slot_projection(slot),
            current_authority_snapshot={
                "topic_gate_receipt_id": str(topic_receipt.id),
                "preflight_id": str(preflight.id),
                "specificity_pass": True,
                "novelty_receipt_hash": novelty.evaluation_hash,
                "freshness_snapshot": freshness,
                "evaluated_at": current.isoformat(),
            },
            provider_authority_hash=content_hash(provider),
            budget_authority_hash=content_hash(budget),
            max_provider_submissions=0,
            production_window_end=slot_close,
            qualification_deadline=deadline,
            authority_hash="0" * 64,
            created_at=current,
        )
        authority.authority_hash = content_hash(
            controlled_verifier_settlement_authority_body(authority)
        )
        source_writer = deepcopy(source.writer_receipt or {})
        child.writer_receipt = {
            **source_writer,
            "producer": "DERIVED_FROM_COMPLETED_VERIFIER_SETTLEMENT",
            "producer_type": "OPENAI_BACKGROUND_VERIFIER_SETTLEMENT",
            "settlement_source_qualification_run_id": str(source.id),
            "settlement_source_verifier_attempt_id": str(attempt.id),
            "settlement_source_verifier_snapshot_id": str(snapshot.id),
            "settlement_authority_id": str(authority.id),
            "settlement_authority_hash": authority.authority_hash,
            "settlement_projection_hash": projection["content_hash"],
            "provider_submission_count_for_settlement": 0,
        }
        child.verifier_receipt = {
            **deepcopy(source.verifier_receipt or {}),
            "settlement_authority_id": str(authority.id),
            "settlement_authority_hash": authority.authority_hash,
            "settlement_source_qualification_run_id": str(source.id),
            "settlement_source_verifier_snapshot_id": str(snapshot.id),
            "settlement_policy_version": SETTLEMENT_POLICY_VERSION,
            "derived_projection_hash": projection["content_hash"],
            "provider_submission_count_for_settlement": 0,
        }
        child.state = "QUALIFIED"
        child.result_receipts = receipts
        service._create_receipt(child, child_draft, "PASS", receipts)
        self.session.flush()
        self.session.add(authority)
        self.session.flush()
        candidate.stage = "GREENLIT"
        candidate.reason_codes = [
            value
            for value in (candidate.reason_codes or [])
            if value != "SCRIPT_QUALIFICATION_TERMINAL_BLOCKED"
        ]
        self.session.flush()
        return child
