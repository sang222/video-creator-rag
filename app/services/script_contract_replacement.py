"""One explicit, immutable V2 replacement lineage for a terminal script run."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m5 import (
    EditorialIdeaCandidateTransition,
    IdeaMarketPreflightCreate,
)
from app.core.actor import ActorContext, ActorType, _system_worker_actor
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.db.models.launch_cadence import (
    FirstChannelLaunchPolicyVersion,
    LaunchRun,
    LongFormPublishSlot,
)
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    SearchDemandEvidence,
)
from app.db.models.ops import ProviderAttempt
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.script_qualification import (
    ControlledProductionContinuationAuthority,
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    ScriptContractReplacementAuthority,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationRun,
)
from app.services.canonical_script_compiler import SCRIPT_CONTRACT_V2
from app.services.config_registry import content_hash
from app.services.editorial_research import EditorialResearchService
from app.services.editorial_fresh_evidence import FreshEvidenceCollector
from app.services.editorial_novelty import (
    EDITORIAL_NOVELTY_GATE_VERSION,
    EditorialNoveltyService,
)
from app.services.editorial_specificity import (
    EDITORIAL_SPECIFICITY_GATE_VERSION,
    EditorialSpecificityService,
)
from app.services.launch_cadence import _preflight_demand_authority_valid
from app.services.m5 import IdeaMarketPreflightService
from app.services.production_start_readiness import (
    resolve_budget_authority,
    resolve_provider_authority,
)
from app.services.script_qualification import (
    QUALIFICATION_POLICY_VERSION,
    SCRIPT_CONTRACT_V1,
    TOPIC_GATE_VERSION,
    ScriptQualificationService,
    TopicDefinitionService,
)


REPLACEMENT_REASON = "SCRIPT_CONTRACT_SINGLE_SOURCE_OF_TRUTH_MIGRATION"
OPERATOR_RECOVERY_REASON = "OPERATOR_REQUESTED_FIRST_VIDEO_RECOVERY"
OPERATOR_RECOVERY_SCHEMA = "vcos.controlled-production-recovery.v1"
OPERATOR_RECOVERY_STRATEGY = "CANDIDATE_REPLACEMENT"
REPAIR_POLICY_REF = "script-content-repair.v1:max-1"
CONTROLLED_CONTINUATION_SCHEMA = "vcos.controlled-production-continuation.v1"
CONTROLLED_CONTINUATION_REASON = "BOUNDED_CONTENT_REPAIR_VERIFIER_CONTINUATION"


def controlled_continuation_slot_projection(
    slot: LongFormPublishSlot,
) -> dict[str, Any]:
    """Seal every identity/timing field of the fresh continuation slot."""

    return {
        "slot_id": str(slot.id),
        "source_slot_id": str(slot.replaces_slot_id),
        "launch_run_id": str(slot.launch_run_id),
        "launch_policy_version_id": str(slot.launch_policy_version_id),
        "company_id": str(slot.company_id),
        "channel_workspace_id": str(slot.channel_workspace_id),
        "local_publish_date": slot.local_publish_date.isoformat(),
        "intended_publish_at": slot.intended_publish_at.isoformat(),
        "target_start_window_open_at": slot.target_start_window_open_at.isoformat(),
        "target_start_window_close_at": slot.target_start_window_close_at.isoformat(),
        "reserved_candidate_id": str(slot.reserved_candidate_id),
        "replacement_authority_id": str(slot.replacement_authority_id),
        "replacement_reason": slot.replacement_reason,
        "replacement_lineage_key": slot.replacement_lineage_key,
    }


def controlled_continuation_authority_body(
    authority: ControlledProductionContinuationAuthority,
) -> dict[str, Any]:
    """Return the complete immutable body covered by ``authority_hash``."""

    return {
        "schema_version": authority.schema_version,
        "continuation_authority_id": str(authority.id),
        "root_replacement_authority_id": str(authority.root_replacement_authority_id),
        "source_qualification_run_id": str(authority.source_qualification_run_id),
        "source_slot_id": str(authority.source_slot_id),
        "continuation_candidate_id": str(authority.continuation_candidate_id),
        "continuation_slot_id": str(authority.continuation_slot_id),
        "continuation_qualification_run_id": str(
            authority.continuation_qualification_run_id
        ),
        "source_provider_response_snapshot_id": str(
            authority.source_provider_response_snapshot_id
        ),
        "repair_authorization_id": str(authority.repair_authorization_id),
        "continuation_reason": authority.continuation_reason,
        "root_authority_hash": authority.root_authority_hash,
        "operator_recovery_schema_version": (
            authority.operator_recovery_schema_version
        ),
        "operator_actor_context": authority.operator_actor_context,
        "bounded_content_repair_policy_ref": (
            authority.bounded_content_repair_policy_ref
        ),
        "source_logical_identity_hash": authority.source_logical_identity_hash,
        "continuation_logical_identity_hash": (
            authority.continuation_logical_identity_hash
        ),
        "source_terminal_settlement_hash": (authority.source_terminal_settlement_hash),
        "source_slot_state": authority.source_slot_state,
        "source_candidate_stage": authority.source_candidate_stage,
        "repair_authorization_hash": authority.repair_authorization_hash,
        "affected_section_ids": authority.affected_section_ids,
        "editable_claim_ids": authority.editable_claim_ids,
        "removable_claim_ids": authority.removable_claim_ids,
        "source_background_attempt_id": str(authority.source_background_attempt_id),
        "source_provider_response_id": authority.source_provider_response_id,
        "source_provider_request_id": authority.source_provider_request_id,
        "source_raw_provider_response_hash": (
            authority.source_raw_provider_response_hash
        ),
        "source_raw_output_hash": authority.source_raw_output_hash,
        "source_typed_output_hash": authority.source_typed_output_hash,
        "reclassification_receipt_hash": (authority.reclassification_receipt_hash),
        "deadline_policy": authority.deadline_policy,
        "slot_projection": authority.slot_projection,
        "current_authority_snapshot": authority.current_authority_snapshot,
        "provider_authority_hash": authority.provider_authority_hash,
        "budget_authority_hash": authority.budget_authority_hash,
        "max_writer_submissions": authority.max_writer_submissions,
        "max_verifier_submissions": authority.max_verifier_submissions,
        "production_window_end": authority.production_window_end.isoformat(),
        "qualification_deadline": authority.qualification_deadline.isoformat(),
        "created_at": authority.created_at.isoformat(),
    }


def operator_recovery_authority_body(
    authority: ScriptContractReplacementAuthority,
) -> dict[str, Any]:
    """Rebuild the sealed operator recovery body without mutable link fields."""

    freshness = authority.freshness_snapshot or {}
    receipt_body = {
        "schema_version": authority.operator_recovery_schema_version,
        "operator_recovery_id": str(authority.operator_recovery_id),
        "replacement_candidate_id": str(authority.replacement_candidate_id),
        "historical_candidate_id": str(authority.replaces_candidate_id),
        "historical_qualification_id": str(authority.historical_qualification_id),
        "historical_slot_id": str(authority.replaces_slot_id),
        "reason": authority.replacement_reason,
        "recovery_strategy": authority.recovery_strategy,
        "authority_versions": authority.authority_versions,
        "freshness_snapshot": freshness,
        "actor_context": authority.operator_actor_context,
        "created_at": authority.operator_authorized_at.isoformat(),
    }
    return {
        **receipt_body,
        "replacement_slot_id": str(authority.replacement_slot_id),
        "source_topic_definition_id": str(authority.source_topic_definition_id),
        "source_preflight_id": str(authority.source_preflight_id),
        "source_evidence_pack_hash": authority.source_evidence_pack_id.removeprefix(
            "evidence-pack-hash:"
        ),
        "source_memory_digest_hash": authority.source_memory_digest_id.removeprefix(
            "memory-digest-hash:"
        ),
        "production_window_end": authority.production_window_end.isoformat(),
        "qualification_deadline": authority.qualification_deadline.isoformat(),
        "recovery_receipt_hash": authority.recovery_receipt_hash,
    }


@dataclass(frozen=True, slots=True)
class ScriptContractReplacementLineage:
    authority: ScriptContractReplacementAuthority
    candidate: EditorialIdeaCandidate
    slot: LongFormPublishSlot
    qualification: ScriptQualificationRun


def resolve_replacement_qualification_leaf(
    session: Session,
    *,
    authority: ScriptContractReplacementAuthority,
    lock: bool = False,
) -> ScriptQualificationRun:
    """Resolve the sole reachable qualification leaf under a sealed authority.

    ``replacement_qualification_run_id`` is the immutable root pointer.  A
    bounded repair may append a child, but may never rewrite that pointer or
    introduce a fork/unreachable sibling.
    """

    if authority.replacement_reason == OPERATOR_RECOVERY_REASON and (
        authority.operator_recovery_schema_version != OPERATOR_RECOVERY_SCHEMA
        or authority.operator_recovery_id != authority.id
        or authority.recovery_receipt_hash is None
        or authority.authority_hash
        != content_hash(operator_recovery_authority_body(authority))
    ):
        raise ValidationFailureError("SCOPED_REPLACEMENT_AUTHORITY_DRIFT")

    statement = (
        select(ScriptQualificationRun)
        .where(ScriptQualificationRun.replacement_authority_id == authority.id)
        .order_by(
            ScriptQualificationRun.logical_attempt_number.asc(),
            ScriptQualificationRun.created_at.asc(),
            ScriptQualificationRun.id.asc(),
        )
    )
    if lock:
        statement = statement.with_for_update()
    runs = list(session.scalars(statement).all())
    continuation_statement = select(ControlledProductionContinuationAuthority).where(
        ControlledProductionContinuationAuthority.root_replacement_authority_id
        == authority.id
    )
    if lock:
        continuation_statement = continuation_statement.with_for_update()
    continuations = list(session.scalars(continuation_statement).all())
    continuation_by_qualification_id = {
        item.continuation_qualification_run_id: item for item in continuations
    }
    if len(continuations) > 1:
        raise ValidationFailureError("SCOPED_REPLACEMENT_CONTINUATION_FORK")
    by_id = {run.id: run for run in runs}
    root = by_id.get(authority.replacement_qualification_run_id)
    if (
        root is None
        or root.supersedes_qualification_run_id is not None
        or root.logical_attempt_number != 1
    ):
        raise ValidationFailureError("SCOPED_REPLACEMENT_QUALIFICATION_ROOT_DRIFT")
    for run in runs:
        continuation = continuation_by_qualification_id.get(run.id)
        expected_slot_id = (
            continuation.continuation_slot_id
            if continuation is not None
            else authority.replacement_slot_id
        )
        continuation_slot = (
            session.get(LongFormPublishSlot, continuation.continuation_slot_id)
            if continuation is not None
            else None
        )
        if (
            run.editorial_idea_candidate_id != authority.replacement_candidate_id
            or run.publish_slot_id != expected_slot_id
            or (
                continuation is not None
                and (
                    continuation.schema_version != CONTROLLED_CONTINUATION_SCHEMA
                    or continuation.continuation_reason
                    != CONTROLLED_CONTINUATION_REASON
                    or continuation.continuation_candidate_id
                    != authority.replacement_candidate_id
                    or continuation.source_qualification_run_id
                    != run.supersedes_qualification_run_id
                    or continuation.max_writer_submissions != 0
                    or continuation.max_verifier_submissions != 1
                    or continuation.authority_hash
                    != content_hash(
                        controlled_continuation_authority_body(continuation)
                    )
                    or continuation.slot_projection
                    != (
                        controlled_continuation_slot_projection(continuation_slot)
                        if continuation_slot is not None
                        else None
                    )
                )
            )
        ):
            raise ValidationFailureError(
                "SCOPED_REPLACEMENT_QUALIFICATION_LINEAGE_DRIFT"
            )
    seen: set[uuid.UUID] = set()
    leaf = root
    expected_attempt = root.logical_attempt_number
    while True:
        if leaf.id in seen:
            raise ValidationFailureError(
                "SCOPED_REPLACEMENT_QUALIFICATION_LINEAGE_CYCLE"
            )
        seen.add(leaf.id)
        child_statement = select(ScriptQualificationRun).where(
            ScriptQualificationRun.supersedes_qualification_run_id == leaf.id
        )
        if lock:
            child_statement = child_statement.with_for_update()
        next_runs = list(session.scalars(child_statement).all())
        if len(next_runs) > 1:
            raise ValidationFailureError(
                "SCOPED_REPLACEMENT_QUALIFICATION_LINEAGE_FORK"
            )
        if not next_runs:
            break
        child = next_runs[0]
        continuation = continuation_by_qualification_id.get(child.id)
        expected_attempt += 1
        if (
            child.logical_attempt_number != expected_attempt
            or child.replacement_authority_id != authority.id
            or child.editorial_idea_candidate_id != root.editorial_idea_candidate_id
            or child.publish_slot_id
            != (
                continuation.continuation_slot_id
                if continuation is not None
                else leaf.publish_slot_id
            )
            or child.launch_run_id != root.launch_run_id
            or child.topic_definition_hash != root.topic_definition_hash
            or child.script_assignment_hash != root.script_assignment_hash
            or child.factual_evidence_pack_hash != root.factual_evidence_pack_hash
            or child.memory_digest_hash != root.memory_digest_hash
            or child.runtime_contract_hash != root.runtime_contract_hash
            or child.assignment_resolution_hash != root.assignment_resolution_hash
            or child.script_contract_version != root.script_contract_version
            or not child.recovery_key
        ):
            raise ValidationFailureError(
                "SCOPED_REPLACEMENT_QUALIFICATION_ATTEMPT_DRIFT"
            )
        leaf = child
    if seen != set(by_id):
        raise ValidationFailureError(
            "SCOPED_REPLACEMENT_QUALIFICATION_UNREACHABLE_NODE"
        )
    if set(continuation_by_qualification_id) - seen:
        raise ValidationFailureError("SCOPED_REPLACEMENT_CONTINUATION_UNREACHABLE")
    return leaf


class ScriptContractReplacementAuthorityService:
    """Creates only the authority explicitly granted for this contract migration.

    This service performs no discovery and no network research.  It clones
    frozen editorial inputs into a new candidate-bound projection because the
    current data model makes those rows candidate-scoped, while keeping the
    exact source IDs and hashes in the immutable replacement authority.
    """

    def __init__(self, session: Session, *, now=utc_now) -> None:
        self.session = session
        self.now = now

    def create(
        self,
        *,
        replaces_candidate_id: uuid.UUID,
        replaces_slot_id: uuid.UUID,
    ) -> ScriptContractReplacementLineage:
        parent = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == replaces_candidate_id)
            .with_for_update()
        )
        slot = self.session.scalar(
            select(LongFormPublishSlot)
            .where(LongFormPublishSlot.id == replaces_slot_id)
            .with_for_update()
        )
        if parent is None or slot is None:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_SOURCE_MISSING")
        if parent.stage != "REJECTED" or slot.state != "CANCELED":
            raise ValidationFailureError(
                "SCRIPT_CONTRACT_REPLACEMENT_PARENT_NOT_TERMINAL"
            )
        if slot.reserved_candidate_id != parent.id:
            raise ValidationFailureError(
                "SCRIPT_CONTRACT_REPLACEMENT_SLOT_PARENT_MISMATCH"
            )
        current = self.now()
        deadline = slot.target_start_window_close_at - timedelta(hours=3)
        if current >= deadline or current > slot.target_start_window_close_at:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_WINDOW_CLOSED")
        budget = resolve_budget_authority(
            self.session,
            policy_snapshot_id=parent.policy_snapshot_id,
            channel_workspace_id=parent.channel_workspace_id,
        )
        if budget.get("state") != "READY":
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_BUDGET_BLOCKED")

        existing = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(
                ScriptContractReplacementAuthority.replaces_candidate_id == parent.id,
                ScriptContractReplacementAuthority.new_script_contract_version
                == SCRIPT_CONTRACT_V2,
            )
            .with_for_update()
        )
        if existing is not None:
            return self._existing_lineage(existing)

        source_run = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.editorial_idea_candidate_id == parent.id)
            .order_by(
                ScriptQualificationRun.created_at.desc(),
                ScriptQualificationRun.id.desc(),
            )
        )
        if source_run is None or source_run.state not in {
            "BLOCKED_NON_REPAIRABLE",
            "BLOCKED_REPAIR_BUDGET_EXHAUSTED",
        }:
            raise ValidationFailureError(
                "SCRIPT_CONTRACT_REPLACEMENT_TERMINAL_QUALIFICATION_REQUIRED"
            )
        source_topic = self.session.scalar(
            select(EditorialTopicDefinition)
            .where(EditorialTopicDefinition.editorial_idea_candidate_id == parent.id)
            .order_by(EditorialTopicDefinition.topic_definition_version.desc())
        )
        source_gate = (
            self.session.scalar(
                select(EditorialTopicDefinitionGateReceipt)
                .where(
                    EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
                    == source_topic.id
                )
                .order_by(EditorialTopicDefinitionGateReceipt.created_at.desc())
            )
            if source_topic is not None
            else None
        )
        source_preflight = self.session.scalar(
            select(IdeaMarketPreflight)
            .where(IdeaMarketPreflight.editorial_idea_candidate_id == parent.id)
            .order_by(IdeaMarketPreflight.created_at.desc())
        )
        if (
            source_topic is None
            or source_gate is None
            or source_gate.state != "PASS"
            or not source_gate.current_production_eligibility
            or source_preflight is None
            or source_preflight.decision != "PASS"
            or source_preflight.policy_fit_state != "PASS"
            or not _preflight_demand_authority_valid(source_preflight)
        ):
            raise ValidationFailureError(
                "SCRIPT_CONTRACT_REPLACEMENT_SOURCE_AUTHORITY_STALE"
            )

        authority_id = uuid.uuid4()
        authority_body = {
            "authority_id": str(authority_id),
            "operator_authorized_at": current.isoformat(),
            "replaces_candidate_id": str(parent.id),
            "replaces_slot_id": str(slot.id),
            "replacement_reason": REPLACEMENT_REASON,
            "source_topic_definition_id": str(source_topic.id),
            "source_preflight_id": str(source_preflight.id),
            "source_evidence_pack_id": f"evidence-pack-hash:{source_run.factual_evidence_pack_hash}",
            "source_memory_digest_id": f"memory-digest-hash:{source_run.memory_digest_hash}",
            "old_script_contract_version": source_run.script_contract_version,
            "new_script_contract_version": SCRIPT_CONTRACT_V2,
            "max_replacement_lineages": 1,
            "max_initial_writer_submissions": 1,
            "max_verifier_submissions": 1,
            "bounded_content_repair_policy_ref": REPAIR_POLICY_REF,
            "production_window_end": slot.target_start_window_close_at.isoformat(),
            "qualification_deadline": deadline.isoformat(),
        }
        if authority_body["old_script_contract_version"] != SCRIPT_CONTRACT_V1:
            raise ValidationFailureError(
                "SCRIPT_CONTRACT_REPLACEMENT_OLD_CONTRACT_INVALID"
            )
        authority = ScriptContractReplacementAuthority(
            id=authority_id,
            operator_authorized_at=current,
            replaces_candidate_id=parent.id,
            replaces_slot_id=slot.id,
            replacement_reason=REPLACEMENT_REASON,
            source_topic_definition_id=source_topic.id,
            source_preflight_id=source_preflight.id,
            source_evidence_pack_id=authority_body["source_evidence_pack_id"],
            source_memory_digest_id=authority_body["source_memory_digest_id"],
            old_script_contract_version=SCRIPT_CONTRACT_V1,
            new_script_contract_version=SCRIPT_CONTRACT_V2,
            max_replacement_lineages=1,
            max_initial_writer_submissions=1,
            max_verifier_submissions=1,
            bounded_content_repair_policy_ref=REPAIR_POLICY_REF,
            production_window_end=slot.target_start_window_close_at,
            qualification_deadline=deadline,
            authority_hash=content_hash(authority_body),
        )
        self.session.add(authority)
        self.session.flush()

        candidate = self._clone_candidate(parent=parent, authority=authority)
        self.session.add(candidate)
        self.session.flush()
        topic = self._clone_topic(
            source=source_topic,
            candidate=candidate,
            parent_topic_definition_id=source_topic.id,
        )
        gate = TopicDefinitionService(self.session).evaluate(topic)
        if gate.state != "PASS" or not gate.current_production_eligibility:
            raise ValidationFailureError(
                "SCRIPT_CONTRACT_REPLACEMENT_TOPIC_REUSE_BLOCKED"
            )
        preflight = self._clone_preflight(
            source=source_preflight, candidate=candidate, authority=authority
        )
        self.session.add(preflight)
        self.session.flush()
        candidate = EditorialResearchService(self.session).transition_candidate(
            candidate_id=candidate.id,
            data=EditorialIdeaCandidateTransition(
                target_stage="GREENLIT",
                idea_market_preflight_id=preflight.id,
                reason_codes=[REPLACEMENT_REASON],
            ),
            actor=_system_worker_actor(
                "vcos-durable-worker", permissions={"editorial.manage"}
            ),
        )
        replacement_slot = self._clone_slot(
            slot=slot, candidate=candidate, authority=authority
        )
        self.session.add(replacement_slot)
        self.session.flush()
        qualification = ScriptQualificationService(self.session, now=self.now).reserve(
            candidate=candidate,
            publish_slot_id=replacement_slot.id,
            launch_run_id=slot.launch_run_id,
            script_contract_version=SCRIPT_CONTRACT_V2,
            replacement_authority_id=authority.id,
        )
        authority.replacement_candidate_id = candidate.id
        authority.replacement_slot_id = replacement_slot.id
        authority.replacement_qualification_run_id = qualification.id
        self.session.flush()
        return ScriptContractReplacementLineage(
            authority=authority,
            candidate=candidate,
            slot=replacement_slot,
            qualification=qualification,
        )

    def create_operator_first_video_recovery(
        self,
        *,
        historical_candidate_id: uuid.UUID,
        actor: ActorContext,
    ) -> ScriptContractReplacementLineage:
        """Create one fresh V2 lineage without rewriting the expired source.

        This is intentionally candidate-specific and first-video-specific.  It
        does not discover content, change a gate result, reuse the old slot, or
        expose a generic state transition.  Every current authority is
        re-evaluated before the immutable replacement row is sealed.
        """

        self._authorize_controlled_recovery(actor)
        current = self.now()
        if current.tzinfo is None:
            raise ValidationFailureError("CONTROLLED_RECOVERY_AWARE_TIME_REQUIRED")
        current = current.astimezone(timezone.utc)

        parent = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == historical_candidate_id)
            .with_for_update()
        )
        if parent is None:
            raise ValidationFailureError("CONTROLLED_RECOVERY_CANDIDATE_MISSING")
        source_run = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.editorial_idea_candidate_id == parent.id)
            .order_by(
                ScriptQualificationRun.created_at.desc(),
                ScriptQualificationRun.id.desc(),
            )
            .with_for_update()
        )
        source_slot = (
            self.session.scalar(
                select(LongFormPublishSlot)
                .where(LongFormPublishSlot.id == source_run.publish_slot_id)
                .with_for_update()
            )
            if source_run is not None
            else None
        )
        if source_run is None or source_slot is None:
            raise ValidationFailureError("CONTROLLED_RECOVERY_SOURCE_LINEAGE_MISSING")

        launch = self.session.scalar(
            select(LaunchRun)
            .where(LaunchRun.id == source_run.launch_run_id)
            .with_for_update()
        )
        policy = (
            self.session.get(
                FirstChannelLaunchPolicyVersion,
                launch.launch_policy_version_id,
            )
            if launch is not None
            else None
        )
        if (
            launch is None
            or policy is None
            or launch.state != "ACTIVE"
            or policy.state != "APPROVED"
            or launch.channel_workspace_id != parent.channel_workspace_id
            or launch.company_id != parent.company_id
            or policy.policy_snapshot_id != parent.policy_snapshot_id
        ):
            raise ValidationFailureError("CONTROLLED_RECOVERY_LAUNCH_AUTHORITY_STALE")

        scope_key = f"first-video:{parent.channel_workspace_id}"
        existing_scope = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(
                ScriptContractReplacementAuthority.operator_recovery_scope_key
                == scope_key
            )
            .with_for_update()
        )
        if existing_scope is not None:
            if (
                existing_scope.replaces_candidate_id != parent.id
                or existing_scope.replacement_reason != OPERATOR_RECOVERY_REASON
            ):
                raise ValidationFailureError(
                    "CONTROLLED_RECOVERY_FIRST_VIDEO_SCOPE_ALREADY_USED"
                )
            return self._existing_lineage(existing_scope)

        existing_parent = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(
                ScriptContractReplacementAuthority.replaces_candidate_id == parent.id,
                ScriptContractReplacementAuthority.new_script_contract_version
                == SCRIPT_CONTRACT_V2,
            )
            .with_for_update()
        )
        if existing_parent is not None:
            raise ValidationFailureError(
                "CONTROLLED_RECOVERY_PARENT_REPLACEMENT_ALREADY_EXISTS"
            )

        source_reasons = set(
            (source_run.failure_receipt or {}).get("reason_codes") or []
        )
        source_attempts = list(
            self.session.scalars(
                select(ScriptQualificationBackgroundAttempt).where(
                    ScriptQualificationBackgroundAttempt.script_qualification_run_id
                    == source_run.id
                )
            ).all()
        )
        source_response_snapshot_id = self.session.scalar(
            select(ScriptQualificationProviderResponseSnapshot.id).where(
                ScriptQualificationProviderResponseSnapshot.script_qualification_run_id
                == source_run.id
            )
        )
        source_provider_attempt_id = self.session.scalar(
            select(ProviderAttempt.id).where(ProviderAttempt.target_id == source_run.id)
        )
        if (
            parent.stage != "GREENLIT"
            or source_run.state != "BLOCKED_NON_REPAIRABLE"
            or "SCRIPT_PROVIDER_LOGICAL_DEADLINE_EXCEEDED" not in source_reasons
            or source_run.script_contract_version != SCRIPT_CONTRACT_V1
            or source_run.logical_deadline_at is None
            or source_run.logical_deadline_at >= current
            or source_slot.reserved_candidate_id != parent.id
            or source_slot.state != "QUALIFICATION_RESERVED"
            or source_slot.admitted_video_project_id is not None
            or bool(source_attempts)
            or source_response_snapshot_id is not None
            or source_provider_attempt_id is not None
        ):
            raise ValidationFailureError(
                "CONTROLLED_RECOVERY_EXPIRED_ZERO_EFFECT_LINEAGE_REQUIRED"
            )

        conflicting_workflow = self.session.scalar(
            select(ProductionWorkflowRun.id).where(
                ProductionWorkflowRun.channel_workspace_id
                == parent.channel_workspace_id,
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
        target_admission = self.session.scalar(
            select(ProjectAdmissionDecision.id).where(
                ProjectAdmissionDecision.editorial_idea_candidate_id == parent.id,
                ProjectAdmissionDecision.decision == "ADMIT",
            )
        )
        if conflicting_workflow is not None or target_admission is not None:
            raise ValidationFailureError(
                "CONTROLLED_RECOVERY_ACTIVE_PRODUCTION_CONFLICT"
            )

        source_topic = self.session.scalar(
            select(EditorialTopicDefinition)
            .where(EditorialTopicDefinition.editorial_idea_candidate_id == parent.id)
            .order_by(EditorialTopicDefinition.topic_definition_version.desc())
        )
        source_gate = (
            self.session.scalar(
                select(EditorialTopicDefinitionGateReceipt)
                .where(
                    EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
                    == source_topic.id,
                    EditorialTopicDefinitionGateReceipt.gate_version
                    == TOPIC_GATE_VERSION,
                )
                .order_by(EditorialTopicDefinitionGateReceipt.created_at.desc())
            )
            if source_topic is not None
            else None
        )
        source_preflight = self.session.scalar(
            select(IdeaMarketPreflight)
            .where(IdeaMarketPreflight.editorial_idea_candidate_id == parent.id)
            .order_by(IdeaMarketPreflight.created_at.desc())
        )
        novelty = EditorialNoveltyService(self.session)
        current_novelty = (
            novelty.evaluate(candidate=parent, topic=source_topic)
            if source_topic is not None
            else None
        )
        persisted_novelty = parent.editorial_novelty_receipt or {}
        if (
            source_topic is None
            or source_gate is None
            or source_gate.state != "PASS"
            or not source_gate.current_production_eligibility
            or source_preflight is None
            or source_preflight.decision != "PASS"
            or source_preflight.policy_fit_state != "PASS"
            or not _preflight_demand_authority_valid(source_preflight)
            or not EditorialSpecificityService(self.session).current_pass(parent)
            or current_novelty is None
            or current_novelty.state != "PASS"
            or persisted_novelty.get("gate_version") != EDITORIAL_NOVELTY_GATE_VERSION
            or persisted_novelty.get("state") != "PASS"
            or persisted_novelty.get("evaluation_hash")
            != current_novelty.evaluation_hash
        ):
            raise ValidationFailureError(
                "CONTROLLED_RECOVERY_CURRENT_EDITORIAL_AUTHORITY_REQUIRED"
            )

        freshness_snapshot = self._current_freshness_snapshot(
            candidate=parent,
            evaluated_at=current,
        )
        budget = resolve_budget_authority(
            self.session,
            policy_snapshot_id=parent.policy_snapshot_id,
            channel_workspace_id=parent.channel_workspace_id,
        )
        providers = resolve_provider_authority(
            self.session,
            policy_snapshot_id=parent.policy_snapshot_id,
            channel_workspace_id=parent.channel_workspace_id,
        )
        if budget.get("state") != "READY":
            raise ValidationFailureError("CONTROLLED_RECOVERY_BUDGET_BLOCKED")
        if providers.get("state") != "READY":
            raise ValidationFailureError("CONTROLLED_RECOVERY_PROVIDER_BLOCKED")

        from app.services.script_qualification_background import (
            build_script_qualification_deadline_policy,
            derive_script_qualification_deadline,
            minimum_script_qualification_window_close_at,
            script_qualification_slot_is_viable,
        )

        deadline_policy = build_script_qualification_deadline_policy()
        slot_close = minimum_script_qualification_window_close_at(
            requested_at=current,
            policy=deadline_policy,
        ) + timedelta(seconds=1)
        deadline = derive_script_qualification_deadline(
            slot_window_close_at=slot_close,
            policy=deadline_policy,
        )
        if not script_qualification_slot_is_viable(
            now=current,
            slot_window_close_at=slot_close,
            policy=deadline_policy,
        ):
            raise ValidationFailureError("CONTROLLED_RECOVERY_FRESH_SLOT_NOT_VIABLE")
        intended_publish_at = slot_close + timedelta(
            hours=policy.render_lead_time_min_hours
        )
        local_publish_date = intended_publish_at.astimezone(
            ZoneInfo(policy.timezone)
        ).date()

        authority_id = uuid.uuid4()
        replacement_candidate_id = uuid.uuid4()
        replacement_slot_id = uuid.uuid4()
        actor_receipt = {
            "actor_type": actor.actor_type.value,
            "actor_id": str(actor.actor_id),
            "actor_role": actor.actor_role,
            "operator_user_id": (
                str(actor.operator_user_id) if actor.operator_user_id else None
            ),
        }
        deadline_policy_receipt = deadline_policy.receipt()
        authority_versions = {
            "topic_gate": TOPIC_GATE_VERSION,
            "preflight": (source_preflight.evidence_blob or {}).get("schema_version"),
            "specificity": EDITORIAL_SPECIFICITY_GATE_VERSION,
            "novelty": EDITORIAL_NOVELTY_GATE_VERSION,
            "script_qualification": QUALIFICATION_POLICY_VERSION,
            "script_contract": SCRIPT_CONTRACT_V2,
            "launch_policy_id": str(policy.id),
            "launch_policy_version": policy.policy_version,
            "launch_policy_hash": policy.canonical_hash,
            "deadline_policy": deadline_policy_receipt,
        }
        receipt_body = {
            "schema_version": OPERATOR_RECOVERY_SCHEMA,
            "operator_recovery_id": str(authority_id),
            "replacement_candidate_id": str(replacement_candidate_id),
            "historical_candidate_id": str(parent.id),
            "historical_qualification_id": str(source_run.id),
            "historical_slot_id": str(source_slot.id),
            "reason": OPERATOR_RECOVERY_REASON,
            "recovery_strategy": OPERATOR_RECOVERY_STRATEGY,
            "authority_versions": authority_versions,
            "freshness_snapshot": freshness_snapshot,
            "actor_context": actor_receipt,
            "created_at": current.isoformat(),
        }
        recovery_receipt_hash = content_hash(receipt_body)
        authority_body = {
            **receipt_body,
            "replacement_slot_id": str(replacement_slot_id),
            "source_topic_definition_id": str(source_topic.id),
            "source_preflight_id": str(source_preflight.id),
            "source_evidence_pack_hash": source_run.factual_evidence_pack_hash,
            "source_memory_digest_hash": source_run.memory_digest_hash,
            "production_window_end": slot_close.isoformat(),
            "qualification_deadline": deadline.isoformat(),
            "recovery_receipt_hash": recovery_receipt_hash,
        }
        authority = ScriptContractReplacementAuthority(
            id=authority_id,
            operator_authorized_at=current,
            replaces_candidate_id=parent.id,
            replaces_slot_id=source_slot.id,
            replacement_reason=OPERATOR_RECOVERY_REASON,
            operator_recovery_schema_version=OPERATOR_RECOVERY_SCHEMA,
            operator_recovery_id=authority_id,
            operator_recovery_scope_key=scope_key,
            historical_qualification_id=source_run.id,
            recovery_strategy=OPERATOR_RECOVERY_STRATEGY,
            authority_versions=authority_versions,
            freshness_snapshot=freshness_snapshot,
            operator_actor_context=actor_receipt,
            recovery_receipt_hash=recovery_receipt_hash,
            source_topic_definition_id=source_topic.id,
            source_preflight_id=source_preflight.id,
            source_evidence_pack_id=(
                f"evidence-pack-hash:{source_run.factual_evidence_pack_hash}"
            ),
            source_memory_digest_id=(
                f"memory-digest-hash:{source_run.memory_digest_hash}"
            ),
            old_script_contract_version=SCRIPT_CONTRACT_V1,
            new_script_contract_version=SCRIPT_CONTRACT_V2,
            max_replacement_lineages=1,
            max_initial_writer_submissions=1,
            max_verifier_submissions=1,
            bounded_content_repair_policy_ref=REPAIR_POLICY_REF,
            production_window_end=slot_close,
            qualification_deadline=deadline,
            authority_hash=content_hash(authority_body),
        )
        self.session.add(authority)
        self.session.flush()

        candidate = self._clone_candidate(
            parent=parent,
            authority=authority,
            candidate_id=replacement_candidate_id,
            replacement_reason=OPERATOR_RECOVERY_REASON,
            initial_stage="RESEARCHED",
            reset_current_editorial_authorities=True,
        )
        self.session.add(candidate)
        self.session.flush()
        topic = self._clone_topic(
            source=source_topic,
            candidate=candidate,
            parent_topic_definition_id=source_topic.id,
        )
        gate = TopicDefinitionService(self.session).evaluate(topic)
        if gate.state != "PASS" or not gate.current_production_eligibility:
            raise ValidationFailureError("CONTROLLED_RECOVERY_TOPIC_GATE_BLOCKED")

        source_blob = source_preflight.evidence_blob or {}
        claim_refs = self._preflight_id_refs(source_blob.get("claim_evidence_refs"))
        demand_refs = self._preflight_id_refs(
            source_blob.get("market_demand_evidence_refs")
        )
        preflight = IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                editorial_calendar_slot_id=source_preflight.editorial_calendar_slot_id,
                editorial_research_run_id=candidate.editorial_research_run_id,
                editorial_idea_candidate_id=candidate.id,
                search_intent_map_id=source_preflight.search_intent_map_id,
                audience_target_pack_id=source_preflight.audience_target_pack_id,
                claim_evidence_refs=claim_refs,
                market_demand_evidence_refs=demand_refs,
                evidence_blob={
                    "operator_recovery_id": str(authority.id),
                    "source_preflight_id": str(source_preflight.id),
                },
            ),
            correlation_id=f"controlled-recovery:{authority.id}:preflight",
        )
        if preflight.decision != "PASS" or preflight.policy_fit_state != "PASS":
            raise ValidationFailureError("CONTROLLED_RECOVERY_PREFLIGHT_BLOCKED")
        editorial = EditorialResearchService(self.session)
        editorial.transition_candidate(
            candidate_id=candidate.id,
            data=EditorialIdeaCandidateTransition(
                target_stage="PREFLIGHT_PASS",
                idea_market_preflight_id=preflight.id,
                reason_codes=[
                    OPERATOR_RECOVERY_REASON,
                    "STRICT_LONG_FORM_PREFLIGHT_PASS",
                ],
            ),
            actor=actor,
        )
        candidate = editorial.transition_candidate(
            candidate_id=candidate.id,
            data=EditorialIdeaCandidateTransition(
                target_stage="GREENLIT",
                idea_market_preflight_id=preflight.id,
                reason_codes=[OPERATOR_RECOVERY_REASON],
            ),
            actor=actor,
        )
        if candidate.stage != "GREENLIT":
            raise ValidationFailureError("CONTROLLED_RECOVERY_EDITORIAL_GATES_BLOCKED")

        replacement_slot = self._clone_slot(
            slot=source_slot,
            candidate=candidate,
            authority=authority,
            slot_id=replacement_slot_id,
            replacement_reason=OPERATOR_RECOVERY_REASON,
            target_start_window_open_at=current,
            target_start_window_close_at=slot_close,
            intended_publish_at=intended_publish_at,
            local_publish_date=local_publish_date,
        )
        self.session.add(replacement_slot)
        self.session.flush()
        qualification = ScriptQualificationService(self.session, now=self.now).reserve(
            candidate=candidate,
            publish_slot_id=replacement_slot.id,
            launch_run_id=launch.id,
            script_contract_version=SCRIPT_CONTRACT_V2,
            replacement_authority_id=authority.id,
        )
        qualification.logical_deadline_at = deadline
        authority.replacement_candidate_id = candidate.id
        authority.replacement_slot_id = replacement_slot.id
        authority.replacement_qualification_run_id = qualification.id
        self.session.flush()
        return ScriptContractReplacementLineage(
            authority=authority,
            candidate=candidate,
            slot=replacement_slot,
            qualification=qualification,
        )

    @staticmethod
    def _authorize_controlled_recovery(actor: ActorContext) -> None:
        trusted = _system_worker_actor(
            "vcos-controlled-recovery",
            permissions={"editorial.manage", "production.start"},
        )
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or actor.actor_id != trusted.actor_id
            or actor.operator_user_id is not None
            or not actor.has_permission("editorial.manage")
            or not actor.has_permission("production.start")
        ):
            raise ValidationFailureError("CONTROLLED_RECOVERY_ACTOR_UNTRUSTED")

    def _current_freshness_snapshot(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        evaluated_at: datetime,
    ) -> dict[str, Any]:
        policy_snapshot = self.session.get(
            CompiledChannelPolicySnapshot,
            candidate.policy_snapshot_id,
        )
        if policy_snapshot is None:
            raise ValidationFailureError("CONTROLLED_RECOVERY_EVIDENCE_POLICY_MISSING")
        authority = FreshEvidenceCollector(
            self.session, now=lambda: evaluated_at
        ).inspect_authority(
            policy_snapshot_id=str(policy_snapshot.id),
            policy_snapshot_hash=policy_snapshot.content_hash,
        )
        policy = authority.policy if authority.ready else None
        if not isinstance(policy, dict):
            raise ValidationFailureError(
                "CONTROLLED_RECOVERY_EVIDENCE_AUTHORITY_NOT_READY"
            )
        try:
            freshness_days = int(policy["freshness_days"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "CONTROLLED_RECOVERY_EVIDENCE_TTL_INVALID"
            ) from exc
        allowed_classes = set(
            str(item) for item in policy.get("allowed_source_classes") or []
        )
        allowed_domains = [
            str(item).lower() for item in policy.get("allowed_domains") or []
        ]

        evidence_ids: list[uuid.UUID] = []
        for ref in candidate.evidence_refs or []:
            if not isinstance(ref, dict) or not ref.get("id"):
                continue
            try:
                evidence_id = uuid.UUID(str(ref["id"]))
            except ValueError:
                continue
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
        if not evidence_ids:
            raise ValidationFailureError("CONTROLLED_RECOVERY_EVIDENCE_REFS_MISSING")

        sources: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            evidence = self.session.get(SearchDemandEvidence, evidence_id)
            editorial = (
                (evidence.metadata_ or {}).get("editorial_fresh_evidence")
                if evidence is not None
                else None
            )
            snapshot = (
                editorial.get("source_snapshot")
                if isinstance(editorial, dict)
                else None
            )
            if evidence is None or not isinstance(snapshot, dict):
                raise ValidationFailureError(
                    "CONTROLLED_RECOVERY_EVIDENCE_SNAPSHOT_MISSING"
                )
            raw_retrieved = snapshot.get("retrieved_at")
            timestamp_source = "source_snapshot.retrieved_at"
            try:
                retrieved_at = (
                    datetime.fromisoformat(str(raw_retrieved).replace("Z", "+00:00"))
                    if raw_retrieved
                    else evidence.captured_at
                )
            except ValueError as exc:
                raise ValidationFailureError(
                    "CONTROLLED_RECOVERY_EVIDENCE_TIMESTAMP_INVALID"
                ) from exc
            if raw_retrieved is None:
                timestamp_source = "search_demand_evidence.captured_at"
            if retrieved_at.tzinfo is None:
                raise ValidationFailureError(
                    "CONTROLLED_RECOVERY_EVIDENCE_TIMESTAMP_INVALID"
                )
            retrieved_at = retrieved_at.astimezone(timezone.utc)
            if retrieved_at > evaluated_at:
                raise ValidationFailureError(
                    "CONTROLLED_RECOVERY_EVIDENCE_TIMESTAMP_IN_FUTURE"
                )
            expires_at = retrieved_at + timedelta(days=freshness_days)
            canonical_url = str(
                snapshot.get("canonical_url") or evidence.source_ref or ""
            )
            parsed = urlparse(canonical_url)
            source_class = str(snapshot.get("source_class") or "")
            if (
                snapshot.get("freshness_state") != "FRESH"
                or snapshot.get("quality_decision") != "PASS"
                or source_class not in allowed_classes
                or parsed.scheme != "https"
                or not parsed.hostname
                or not self._hostname_allowed(parsed.hostname.lower(), allowed_domains)
                or evaluated_at >= expires_at
            ):
                raise ValidationFailureError(
                    "CONTROLLED_RECOVERY_EVIDENCE_NOT_CURRENT_FRESH"
                )
            sources.append(
                {
                    "evidence_id": str(evidence.id),
                    "canonical_url": canonical_url,
                    "source_class": source_class,
                    "snapshot_hash": snapshot.get("content_hash"),
                    "retrieved_at": retrieved_at.isoformat(),
                    "timestamp_source": timestamp_source,
                    "expires_at": expires_at.isoformat(),
                    "age_seconds": int((evaluated_at - retrieved_at).total_seconds()),
                    "state": "FRESH",
                }
            )
        body = {
            "schema_version": "vcos.controlled-recovery-freshness.v1",
            "state": "FRESH",
            "evaluated_at": evaluated_at.isoformat(),
            "freshness_days": freshness_days,
            "provider_key": authority.provider_key,
            "provider_config_hash": authority.config_hash,
            "policy_snapshot_id": str(policy_snapshot.id),
            "policy_snapshot_hash": policy_snapshot.content_hash,
            "sources": sources,
        }
        return {**body, "snapshot_hash": content_hash(body)}

    @staticmethod
    def _hostname_allowed(hostname: str, allowed_domains: list[str]) -> bool:
        return any(
            hostname == item.lstrip("*.") or hostname.endswith(f".{item.lstrip('*.')}")
            for item in allowed_domains
            if item
        )

    @staticmethod
    def _preflight_id_refs(value: Any) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        for item in value if isinstance(value, list) else []:
            if isinstance(item, dict) and item.get("id"):
                refs.append({"id": str(item["id"])})
        return refs

    def _existing_lineage(
        self, authority: ScriptContractReplacementAuthority
    ) -> ScriptContractReplacementLineage:
        candidate = self.session.get(
            EditorialIdeaCandidate, authority.replacement_candidate_id
        )
        if candidate is None:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_AUTHORITY_DRIFT")
        qualification = resolve_replacement_qualification_leaf(
            self.session, authority=authority, lock=True
        )
        slot = self.session.get(LongFormPublishSlot, qualification.publish_slot_id)
        if slot is None:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_AUTHORITY_DRIFT")
        return ScriptContractReplacementLineage(
            authority, candidate, slot, qualification
        )

    def _clone_candidate(
        self,
        *,
        parent: EditorialIdeaCandidate,
        authority: ScriptContractReplacementAuthority,
        candidate_id: uuid.UUID | None = None,
        replacement_reason: str = REPLACEMENT_REASON,
        initial_stage: str = "PREFLIGHT_PASS",
        reset_current_editorial_authorities: bool = False,
    ) -> EditorialIdeaCandidate:
        ignored = {
            "id",
            "stage",
            "parent_candidate_id",
            "replaces_candidate_id",
            "replacement_authority_id",
            "replacement_reason",
            "replacement_lineage_key",
            "script_contract_version",
            "canonical_hash",
            "reason_codes",
            "created_by_user_id",
            "created_at",
        }
        values = {
            column.name: deepcopy(getattr(parent, column.name))
            for column in parent.__table__.columns
            if column.name not in ignored
        }
        if reset_current_editorial_authorities:
            values.update(
                {
                    "editorial_territory_key": None,
                    "editorial_novelty_receipt": None,
                    "editorial_specificity_receipt": None,
                }
            )
        lineage_key = content_hash(
            {
                "authority_id": str(authority.id),
                "source_candidate_hash": parent.canonical_hash,
                "source_topic_definition_id": str(authority.source_topic_definition_id),
            }
        )
        return EditorialIdeaCandidate(
            **values,
            id=candidate_id,
            stage=initial_stage,
            replaces_candidate_id=parent.id,
            replacement_authority_id=authority.id,
            replacement_reason=replacement_reason,
            replacement_lineage_key=lineage_key,
            script_contract_version=SCRIPT_CONTRACT_V2,
            canonical_hash=content_hash(
                {
                    "source_candidate_hash": parent.canonical_hash,
                    "replacement_lineage_key": lineage_key,
                    "script_contract_version": SCRIPT_CONTRACT_V2,
                }
            ),
            reason_codes=[
                *(parent.reason_codes or []),
                replacement_reason,
            ],
        )

    def _clone_topic(
        self,
        *,
        source: EditorialTopicDefinition,
        candidate: EditorialIdeaCandidate,
        parent_topic_definition_id: uuid.UUID,
    ) -> EditorialTopicDefinition:
        fields = {
            "subject_type": source.subject_type,
            "subject_name": source.subject_name,
            "subject_canonical_id": source.subject_canonical_id,
            "subject_evidence_refs": deepcopy(source.subject_evidence_refs),
            "subject_evidence_spans": deepcopy(source.subject_evidence_spans),
            "target_audience": source.target_audience,
            "audience_problem": source.audience_problem,
            "content_pillar": source.content_pillar,
            "production_goal": source.production_goal,
            "scope_inclusions": deepcopy(source.scope_inclusions),
            "exclusions": deepcopy(source.exclusions),
            "central_question_or_thesis": source.central_question_or_thesis,
            "learning_outcome": source.learning_outcome,
            "viewer_value": source.viewer_value,
            "content_mode": source.content_mode,
            "channel_contract_ref": deepcopy(source.channel_contract_ref),
            "source_classification_refs": deepcopy(source.source_classification_refs),
            "series_binding": deepcopy(source.series_binding),
            "standalone_self_containment_required": source.standalone_self_containment_required,
        }
        return TopicDefinitionService(self.session).create(
            candidate=candidate,
            fields=fields,
            parent_topic_definition_id=parent_topic_definition_id,
        )

    @staticmethod
    def _clone_preflight(
        *,
        source: IdeaMarketPreflight,
        candidate: EditorialIdeaCandidate,
        authority: ScriptContractReplacementAuthority,
    ) -> IdeaMarketPreflight:
        ignored = {"id", "editorial_idea_candidate_id", "created_at"}
        values = {
            column.name: deepcopy(getattr(source, column.name))
            for column in source.__table__.columns
            if column.name not in ignored
        }
        values["evidence_blob"] = {
            **dict(values.get("evidence_blob") or {}),
            "script_contract_replacement": {
                "authority_id": str(authority.id),
                "source_preflight_id": str(source.id),
                "source_evidence_reused": True,
            },
        }
        return IdeaMarketPreflight(**values, editorial_idea_candidate_id=candidate.id)

    @staticmethod
    def _clone_slot(
        *,
        slot: LongFormPublishSlot,
        candidate: EditorialIdeaCandidate,
        authority: ScriptContractReplacementAuthority,
        slot_id: uuid.UUID | None = None,
        replacement_reason: str = REPLACEMENT_REASON,
        target_start_window_open_at: datetime | None = None,
        target_start_window_close_at: datetime | None = None,
        intended_publish_at: datetime | None = None,
        local_publish_date=None,
    ) -> LongFormPublishSlot:
        lineage_key = content_hash(
            {
                "authority_id": str(authority.id),
                "replaces_slot_id": str(slot.id),
                "candidate_id": str(candidate.id),
            }
        )
        return LongFormPublishSlot(
            id=slot_id,
            launch_run_id=slot.launch_run_id,
            launch_policy_version_id=slot.launch_policy_version_id,
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            local_publish_date=local_publish_date or slot.local_publish_date,
            intended_publish_at=intended_publish_at or slot.intended_publish_at,
            target_start_window_open_at=(
                target_start_window_open_at or slot.target_start_window_open_at
            ),
            target_start_window_close_at=(
                target_start_window_close_at or slot.target_start_window_close_at
            ),
            state="QUALIFICATION_RESERVED",
            reserved_candidate_id=candidate.id,
            replaces_slot_id=slot.id,
            replacement_authority_id=authority.id,
            replacement_reason=replacement_reason,
            replacement_lineage_key=lineage_key,
        )
