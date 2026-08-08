"""One explicit, immutable V2 replacement lineage for a terminal script run."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m5 import EditorialIdeaCandidateTransition
from app.core.actor import _system_worker_actor
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m5 import EditorialIdeaCandidate, IdeaMarketPreflight
from app.db.models.script_qualification import (
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    ScriptContractReplacementAuthority,
    ScriptQualificationRun,
)
from app.services.canonical_script_compiler import SCRIPT_CONTRACT_V2
from app.services.config_registry import content_hash
from app.services.editorial_research import EditorialResearchService
from app.services.launch_cadence import _preflight_demand_authority_valid
from app.services.production_start_readiness import resolve_budget_authority
from app.services.script_qualification import (
    SCRIPT_CONTRACT_V1,
    ScriptQualificationService,
    TopicDefinitionService,
)


REPLACEMENT_REASON = "SCRIPT_CONTRACT_SINGLE_SOURCE_OF_TRUTH_MIGRATION"
REPAIR_POLICY_REF = "script-content-repair.v1:max-1"


@dataclass(frozen=True, slots=True)
class ScriptContractReplacementLineage:
    authority: ScriptContractReplacementAuthority
    candidate: EditorialIdeaCandidate
    slot: LongFormPublishSlot
    qualification: ScriptQualificationRun


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
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_PARENT_NOT_TERMINAL")
        if slot.reserved_candidate_id != parent.id:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_SLOT_PARENT_MISMATCH")
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
            .order_by(ScriptQualificationRun.created_at.desc(), ScriptQualificationRun.id.desc())
        )
        if source_run is None or source_run.state not in {
            "BLOCKED_NON_REPAIRABLE",
            "BLOCKED_REPAIR_BUDGET_EXHAUSTED",
        }:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_TERMINAL_QUALIFICATION_REQUIRED")
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
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_SOURCE_AUTHORITY_STALE")

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
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_OLD_CONTRACT_INVALID")
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
            source=source_topic, candidate=candidate, parent_topic_definition_id=source_topic.id
        )
        gate = TopicDefinitionService(self.session).evaluate(topic)
        if gate.state != "PASS" or not gate.current_production_eligibility:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_TOPIC_REUSE_BLOCKED")
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
        replacement_slot = self._clone_slot(slot=slot, candidate=candidate, authority=authority)
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

    def _existing_lineage(
        self, authority: ScriptContractReplacementAuthority
    ) -> ScriptContractReplacementLineage:
        candidate = self.session.get(EditorialIdeaCandidate, authority.replacement_candidate_id)
        slot = self.session.get(LongFormPublishSlot, authority.replacement_slot_id)
        qualification = self.session.get(
            ScriptQualificationRun, authority.replacement_qualification_run_id
        )
        if candidate is None or slot is None or qualification is None:
            raise ValidationFailureError("SCRIPT_CONTRACT_REPLACEMENT_AUTHORITY_DRIFT")
        return ScriptContractReplacementLineage(authority, candidate, slot, qualification)

    def _clone_candidate(
        self,
        *,
        parent: EditorialIdeaCandidate,
        authority: ScriptContractReplacementAuthority,
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
            "created_at",
        }
        values = {
            column.name: deepcopy(getattr(parent, column.name))
            for column in parent.__table__.columns
            if column.name not in ignored
        }
        lineage_key = content_hash(
            {
                "authority_id": str(authority.id),
                "source_candidate_hash": parent.canonical_hash,
                "source_topic_definition_id": str(authority.source_topic_definition_id),
            }
        )
        return EditorialIdeaCandidate(
            **values,
            stage="PREFLIGHT_PASS",
            replaces_candidate_id=parent.id,
            replacement_authority_id=authority.id,
            replacement_reason=REPLACEMENT_REASON,
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
                REPLACEMENT_REASON,
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
        return IdeaMarketPreflight(
            **values, editorial_idea_candidate_id=candidate.id
        )

    @staticmethod
    def _clone_slot(
        *,
        slot: LongFormPublishSlot,
        candidate: EditorialIdeaCandidate,
        authority: ScriptContractReplacementAuthority,
    ) -> LongFormPublishSlot:
        lineage_key = content_hash(
            {
                "authority_id": str(authority.id),
                "replaces_slot_id": str(slot.id),
                "candidate_id": str(candidate.id),
            }
        )
        return LongFormPublishSlot(
            launch_run_id=slot.launch_run_id,
            launch_policy_version_id=slot.launch_policy_version_id,
            company_id=slot.company_id,
            channel_workspace_id=slot.channel_workspace_id,
            local_publish_date=slot.local_publish_date,
            intended_publish_at=slot.intended_publish_at,
            target_start_window_open_at=slot.target_start_window_open_at,
            target_start_window_close_at=slot.target_start_window_close_at,
            state="QUALIFICATION_RESERVED",
            reserved_candidate_id=candidate.id,
            replaces_slot_id=slot.id,
            replacement_authority_id=authority.id,
            replacement_reason=REPLACEMENT_REASON,
            replacement_lineage_key=lineage_key,
        )
