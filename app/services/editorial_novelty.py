"""Deterministic editorial territory, novelty, and duplicate maintenance.

This module deliberately uses only frozen editorial authorities.  It makes no
provider calls and never uses similarity scores or embeddings as authority.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.events import AuditEnvelope
from app.core.actor import ActorContext
from app.core.errors import ValidationFailureError
from app.db.models.channel import ChannelWorkspace
from app.db.models.foundation import AuditEvent, DomainEvent
from app.db.models.launch_cadence import (
    CadenceEvaluationReceipt,
    LaunchRun,
    LongFormPublishSlot,
)
from app.db.models.m5 import (
    AudienceTargetPack,
    EditorialIdeaCandidate,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    SearchIntentMap,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.script_qualification import (
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    ScriptContractReplacementAuthority,
    ScriptQualificationRun,
)
from app.services.audit import AuditService
from app.services.config_registry import content_hash


EDITORIAL_TERRITORY_SCHEMA = "vcos.editorial-territory.v2"
EDITORIAL_NOVELTY_GATE_VERSION = "editorial-novelty-gate.v1"
ACTIVE_TERRITORY_STAGES = {
    "GREENLIT",
    "SELECTED_FOR_SLOT",
    "IN_PRODUCTION",
    "FINAL_REVIEW_READY",
}
ACTIVE_QUALIFICATION_STATES = {
    "RESERVED",
    "WRITER_DISPATCHED",
    "SCRIPT_GENERATED",
    "STRUCTURAL_CHECKED",
    "CLAIM_INVENTORY_CHECKED",
    "GROUNDING_CHECKED",
    "VERIFIER_DISPATCHED",
    "EDITORIAL_CHECKED",
    "MEMORY_CHECKED",
    "REPAIRABLE_BLOCK",
    "REPAIR_DISPATCHED",
    "REVERIFYING",
    "WRITER_IN_PROGRESS",
    "VERIFIER_IN_PROGRESS",
    "WRITER_SUBMIT_PENDING",
    "VERIFIER_SUBMIT_PENDING",
    "RECOVERY_AUTHORIZED",
}
ACTIVE_WORKFLOW_STATES = {
    "PLANNING_PENDING",
    "PLANNING_RUNNING",
    "ASSIGNMENT_READY",
    "RESEARCH_PENDING",
    "RESEARCH_RUNNING",
    "PACKAGE_PENDING",
    "PACKAGE_RUNNING",
    "READY_FOR_PRODUCTION",
    "MEDIA_PENDING",
    "MEDIA_RUNNING",
    "RENDER_PENDING",
    "RENDER_RUNNING",
    "QC_PENDING",
    "QC_RUNNING",
    "ARCHIVE_PENDING",
    "ARCHIVE_RUNNING",
    "PAUSED_AFTER_NATIVE_RENDER",
}
TERMINAL_QUALIFICATION_STATES = {
    "QUALIFIED",
    "BLOCKED_NON_REPAIRABLE",
    "BLOCKED_REPAIR_BUDGET_EXHAUSTED",
    "COOLDOWN",
    "SUPERSEDED",
}


def normalize_editorial_text(value: Any) -> str:
    """Normalize semantic text without allowing volatile lineage into a key."""

    text = str(value or "").strip().casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _stable_subject_id(topic: EditorialTopicDefinition) -> str:
    """Use a topic's canonical ID unless legacy code stored an evidence UUID."""

    value = str(topic.subject_canonical_id or "").strip()
    if re.fullmatch(r"official-document:[0-9a-f-]{36}", value, flags=re.I):
        # Legacy topic definitions tied this field to SearchDemandEvidence.id.
        # A normalized subject remains deterministic and is paired with the
        # question/outcome below, so it does not collapse distinct videos.
        return f"legacy-subject:{normalize_editorial_text(topic.subject_name)}"
    return normalize_editorial_text(value) or normalize_editorial_text(topic.subject_name)


@dataclass(frozen=True, slots=True)
class EditorialTerritory:
    key: str
    payload: dict[str, Any]


class EditorialTerritoryCompiler:
    """Compile source-run-independent editorial semantics into a stable key."""

    def compile(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        topic: EditorialTopicDefinition,
    ) -> EditorialTerritory:
        mode = str(topic.content_mode or "").strip()
        if mode not in {"STANDALONE", "SERIES_EPISODE"}:
            raise ValidationFailureError("EDITORIAL_TERRITORY_CONTENT_MODE_MISSING")
        proposal = (
            getattr(candidate, "editorial_idea_proposal", None)
            if isinstance(getattr(candidate, "editorial_idea_proposal", None), dict)
            else {}
        )
        payload: dict[str, Any] = {
            "schema_version": EDITORIAL_TERRITORY_SCHEMA,
            "channel_workspace_id": str(candidate.channel_workspace_id),
            "policy_snapshot_id": str(candidate.policy_snapshot_id),
            "content_mode": mode,
            "subject_type": normalize_editorial_text(topic.subject_type),
            "subject_canonical_id": _stable_subject_id(topic),
            "central_question_or_thesis": normalize_editorial_text(
                topic.central_question_or_thesis
            ),
            "learning_outcome": normalize_editorial_text(topic.learning_outcome),
            # Legacy territory compilation remains available for historical
            # reporting/duplicate maintenance, but legacy rows cannot count
            # as runway because they lack a current specificity receipt.
            "editorial_delta": normalize_editorial_text(
                proposal.get("editorial_delta")
                or getattr(topic, "viewer_value", "")
                or topic.central_question_or_thesis
            ),
            "production_goal": normalize_editorial_text(topic.production_goal),
            "content_pillar": normalize_editorial_text(topic.content_pillar),
            "target_audience": normalize_editorial_text(topic.target_audience),
            "audience_problem": normalize_editorial_text(topic.audience_problem),
        }
        if not all(
            payload[key]
            for key in (
                "subject_type",
                "subject_canonical_id",
                "central_question_or_thesis",
                "learning_outcome",
                "editorial_delta",
            )
        ):
            raise ValidationFailureError("EDITORIAL_TERRITORY_AUTHORITY_MISSING")
        if mode == "SERIES_EPISODE":
            binding = topic.series_binding if isinstance(topic.series_binding, dict) else {}
            payload["series"] = {
                "series_plan_id": str(binding.get("series_plan_id") or ""),
                "series_run_id": str(binding.get("series_run_id") or ""),
                "episode_role": normalize_editorial_text(binding.get("episode_role")),
                "episode_delta": normalize_editorial_text(binding.get("episode_delta")),
            }
            if not all(payload["series"].values()):
                raise ValidationFailureError("EDITORIAL_TERRITORY_SERIES_AUTHORITY_MISSING")
        return EditorialTerritory(key=content_hash(payload), payload=payload)


@dataclass(frozen=True, slots=True)
class NoveltyEvaluation:
    candidate_id: uuid.UUID
    territory_key: str
    state: str
    matched_candidate_ids: tuple[uuid.UUID, ...]
    matched_published_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    gate_version: str
    evaluation_hash: str

    def receipt(self) -> dict[str, Any]:
        body = {
            "gate_version": self.gate_version,
            "candidate_id": str(self.candidate_id),
            "territory_key": self.territory_key,
            "state": self.state,
            "matched_candidate_ids": [str(item) for item in self.matched_candidate_ids],
            "matched_published_refs": list(self.matched_published_refs),
            "reason_codes": list(self.reason_codes),
        }
        return {**body, "evaluation_hash": self.evaluation_hash}


@dataclass(frozen=True, slots=True)
class RunwayTerritoryCounts:
    raw_greenlit_rows: int
    current_eligible_greenlit_rows: int
    distinct_eligible_territory_count: int


@dataclass(frozen=True, slots=True)
class DuplicateCleanupAction:
    candidate_id: uuid.UUID
    territory_key: str | None
    action: str
    survivor_candidate_id: uuid.UUID | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DuplicateCleanupCluster:
    territory_key: str | None
    candidate_ids: tuple[uuid.UUID, ...]
    survivor_candidate_id: uuid.UUID | None
    actions: tuple[DuplicateCleanupAction, ...]
    conflict: bool = False


class EditorialNoveltyService:
    """Evaluate novelty against occupied, current editorial territories."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.compiler = EditorialTerritoryCompiler()

    def evaluate(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        topic: EditorialTopicDefinition,
    ) -> NoveltyEvaluation:
        self._lock_channel_authority(candidate)
        territory = self.compiler.compile(candidate=candidate, topic=topic)
        exact_title_angle = (
            normalize_editorial_text(candidate.proposed_title),
            normalize_editorial_text(candidate.proposed_angle),
        )
        matched: list[EditorialIdeaCandidate] = []
        published_refs: list[str] = []
        reasons: list[str] = []
        from app.services.editorial_specificity import EditorialSpecificityService
        from app.services.script_qualification import TopicDefinitionService

        specificity = EditorialSpecificityService(self.session)

        for occupied in self._locked_occupied_candidates(candidate):
            occupied_topic = self._current_topic(occupied)
            if occupied_topic is None:
                continue
            if not TopicDefinitionService(self.session).current_eligibility(occupied).eligible:
                continue
            if not specificity.current_pass(occupied):
                continue
            try:
                occupied_territory = self.compiler.compile(
                    candidate=occupied, topic=occupied_topic
                )
            except ValidationFailureError:
                continue
            fast_match = exact_title_angle == (
                normalize_editorial_text(occupied.proposed_title),
                normalize_editorial_text(occupied.proposed_angle),
            ) and all(exact_title_angle)
            question_match = (
                _stable_subject_id(topic) == _stable_subject_id(occupied_topic)
                and normalize_editorial_text(topic.central_question_or_thesis)
                == normalize_editorial_text(occupied_topic.central_question_or_thesis)
            )
            if territory.key == occupied_territory.key or fast_match or question_match:
                matched.append(occupied)
                if occupied.stage == "PUBLISHED":
                    published_refs.append(f"editorial-candidate:{occupied.id}")
                    reasons.append("EDITORIAL_TERRITORY_DUPLICATE_RECENT_PUBLISHED")
                else:
                    reasons.append(self._occupied_reason(occupied))
                if fast_match:
                    reasons.append("EDITORIAL_SOURCE_TOPIC_DUPLICATE")
                if question_match:
                    reasons.append("EDITORIAL_QUESTION_DUPLICATE")
        state = "BLOCK" if matched else "PASS"
        codes = tuple(
            dict.fromkeys(
                reasons
                or ["EDITORIAL_NOVELTY_PASS"]
            )
        )
        body = {
            "gate_version": EDITORIAL_NOVELTY_GATE_VERSION,
            "candidate_id": str(candidate.id),
            "territory_key": territory.key,
            "state": state,
            "matched_candidate_ids": [str(item.id) for item in matched],
            "matched_published_refs": published_refs,
            "reason_codes": list(codes),
        }
        return NoveltyEvaluation(
            candidate_id=candidate.id,
            territory_key=territory.key,
            state=state,
            matched_candidate_ids=tuple(item.id for item in matched),
            matched_published_refs=tuple(published_refs),
            reason_codes=codes,
            gate_version=EDITORIAL_NOVELTY_GATE_VERSION,
            evaluation_hash=content_hash(body),
        )

    def persist(self, candidate: EditorialIdeaCandidate, evaluation: NoveltyEvaluation) -> None:
        candidate.editorial_territory_key = evaluation.territory_key
        candidate.editorial_novelty_receipt = evaluation.receipt()
        self.session.flush()

    def runway_counts(
        self,
        *,
        channel_workspace_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID,
    ) -> RunwayTerritoryCounts:
        candidates = list(
            self.session.scalars(
                select(EditorialIdeaCandidate).where(
                    EditorialIdeaCandidate.channel_workspace_id == channel_workspace_id,
                    EditorialIdeaCandidate.policy_snapshot_id == policy_snapshot_id,
                    EditorialIdeaCandidate.stage == "GREENLIT",
                )
            ).all()
        )
        keys: set[str] = set()
        eligible_rows = 0
        from app.services.editorial_specificity import EditorialSpecificityService
        from app.services.script_qualification import TopicDefinitionService

        topic_service = TopicDefinitionService(self.session)
        specificity = EditorialSpecificityService(self.session)
        for candidate in candidates:
            if not topic_service.current_eligibility(candidate).eligible:
                continue
            if not specificity.current_pass(candidate):
                continue
            if not self._current_strict_preflight_pass(candidate):
                continue
            receipt = candidate.editorial_novelty_receipt
            if (
                not isinstance(receipt, dict)
                or receipt.get("gate_version") != EDITORIAL_NOVELTY_GATE_VERSION
                or receipt.get("state") != "PASS"
                or receipt.get("territory_key") != candidate.editorial_territory_key
            ):
                continue
            eligible_rows += 1
            keys.add(str(candidate.editorial_territory_key))
        return RunwayTerritoryCounts(
            raw_greenlit_rows=len(candidates),
            current_eligible_greenlit_rows=eligible_rows,
            distinct_eligible_territory_count=len(keys),
        )

    def occupied_exclusion_authority(
        self,
        *,
        channel_workspace_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID,
        limit: int = 12,
    ) -> dict[str, list[str]]:
        """Build a compact, deterministic research exclusion authority."""

        subjects: set[str] = set()
        urls: set[str] = set()
        titles: set[str] = set()
        questions: set[str] = set()
        from app.services.editorial_specificity import EditorialSpecificityService

        specificity = EditorialSpecificityService(self.session)
        for candidate in self.session.scalars(
            select(EditorialIdeaCandidate).where(
                EditorialIdeaCandidate.channel_workspace_id == channel_workspace_id,
                EditorialIdeaCandidate.policy_snapshot_id == policy_snapshot_id,
                EditorialIdeaCandidate.stage.in_(ACTIVE_TERRITORY_STAGES),
            )
        ).all():
            topic = self._current_topic(candidate)
            if topic is None:
                continue
            if not specificity.current_pass(candidate):
                continue
            subjects.add(str(topic.subject_name))
            titles.add(str(candidate.proposed_title))
            questions.add(str(topic.central_question_or_thesis))
            for ref in topic.subject_evidence_refs or []:
                if isinstance(ref, dict) and ref.get("ref"):
                    urls.add(str(ref["ref"]))
        return {
            "excluded_subjects": sorted(item for item in subjects if item)[:limit],
            "excluded_canonical_source_urls": sorted(item for item in urls if item)[:limit],
            "excluded_topic_titles": sorted(item for item in titles if item)[:limit],
            "excluded_editorial_questions": sorted(item for item in questions if item)[:limit],
        }

    def _lock_channel_authority(self, candidate: EditorialIdeaCandidate) -> None:
        # Locking the active launch (or workspace when no launch exists) closes
        # the "both saw no duplicate" race before either candidate greenlights.
        launch = self.session.scalar(
            select(LaunchRun)
            .where(
                LaunchRun.channel_workspace_id == candidate.channel_workspace_id,
                LaunchRun.state == "ACTIVE",
            )
            .with_for_update()
        )
        if launch is None:
            workspace = self.session.scalar(
                select(ChannelWorkspace)
                .where(ChannelWorkspace.id == candidate.channel_workspace_id)
                .with_for_update()
            )
            if workspace is None:
                raise ValidationFailureError("EDITORIAL_NOVELTY_CHANNEL_AUTHORITY_MISSING")

    def _locked_occupied_candidates(
        self, candidate: EditorialIdeaCandidate
    ) -> list[EditorialIdeaCandidate]:
        """Lock active and published lineage that can block a new territory."""

        return list(
            self.session.scalars(
                select(EditorialIdeaCandidate)
                .where(
                    EditorialIdeaCandidate.channel_workspace_id
                    == candidate.channel_workspace_id,
                    EditorialIdeaCandidate.policy_snapshot_id
                    == candidate.policy_snapshot_id,
                    EditorialIdeaCandidate.id != candidate.id,
                    EditorialIdeaCandidate.stage.in_(
                        {*ACTIVE_TERRITORY_STAGES, "PUBLISHED"}
                    ),
                )
                .with_for_update()
            ).all()
        )

    def _current_topic(
        self, candidate: EditorialIdeaCandidate
    ) -> EditorialTopicDefinition | None:
        return self.session.scalar(
            select(EditorialTopicDefinition)
            .where(EditorialTopicDefinition.editorial_idea_candidate_id == candidate.id)
            .order_by(EditorialTopicDefinition.topic_definition_version.desc())
        )

    def _current_strict_preflight_pass(self, candidate: EditorialIdeaCandidate) -> bool:
        from app.services.launch_cadence import _preflight_demand_authority_valid

        preflights = self.session.scalars(
            select(IdeaMarketPreflight)
            .where(IdeaMarketPreflight.editorial_idea_candidate_id == candidate.id)
            .order_by(IdeaMarketPreflight.created_at.desc())
        ).all()
        return any(
            preflight.decision == "PASS"
            and preflight.policy_fit_state == "PASS"
            and preflight.niche_contract_digest_hash
            and preflight.target_market_digest_hash
            and isinstance(preflight.evidence_blob, dict)
            and preflight.evidence_blob.get("canonical_authority_verified") is True
            and candidate.rights_policy_state == "PASS"
            and candidate.quality_state == "PASS"
            and _preflight_demand_authority_valid(preflight)
            for preflight in preflights
        )

    @staticmethod
    def _occupied_reason(candidate: EditorialIdeaCandidate) -> str:
        return {
            "GREENLIT": "EDITORIAL_TERRITORY_DUPLICATE_ACTIVE",
            "SELECTED_FOR_SLOT": "EDITORIAL_TERRITORY_DUPLICATE_SCHEDULED",
            "IN_PRODUCTION": "EDITORIAL_TERRITORY_DUPLICATE_IN_PRODUCTION",
            "FINAL_REVIEW_READY": "EDITORIAL_TERRITORY_DUPLICATE_SCHEDULED",
        }.get(candidate.stage, "EDITORIAL_TERRITORY_DUPLICATE_ACTIVE")


class EditorialDuplicateCleanupService:
    """Plan and apply auditable duplicate cleanup without raw SQL mutation."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.novelty = EditorialNoveltyService(session)

    def plan(
        self,
        *,
        channel_workspace_id: uuid.UUID | None = None,
        policy_snapshot_id: uuid.UUID | None = None,
    ) -> list[DuplicateCleanupCluster]:
        statement = select(EditorialIdeaCandidate).where(
            EditorialIdeaCandidate.stage.in_(ACTIVE_TERRITORY_STAGES)
        )
        if channel_workspace_id is not None:
            statement = statement.where(
                EditorialIdeaCandidate.channel_workspace_id == channel_workspace_id
            )
        if policy_snapshot_id is not None:
            statement = statement.where(
                EditorialIdeaCandidate.policy_snapshot_id == policy_snapshot_id
            )
        candidates = list(self.session.scalars(statement.order_by(EditorialIdeaCandidate.created_at)).all())
        clusters: dict[tuple[uuid.UUID, uuid.UUID, str], list[EditorialIdeaCandidate]] = {}
        missing_authority: list[EditorialIdeaCandidate] = []
        for candidate in candidates:
            topic = self.novelty._current_topic(candidate)
            if topic is None:
                if candidate.stage in {"GREENLIT", "SELECTED_FOR_SLOT"}:
                    missing_authority.append(candidate)
                continue
            try:
                territory = self.novelty.compiler.compile(candidate=candidate, topic=topic)
            except ValidationFailureError:
                if candidate.stage in {"GREENLIT", "SELECTED_FOR_SLOT"}:
                    missing_authority.append(candidate)
                continue
            clusters.setdefault(
                (candidate.channel_workspace_id, candidate.policy_snapshot_id, territory.key),
                [],
            ).append(candidate)
        results: list[DuplicateCleanupCluster] = []
        for (_workspace, _policy, key), members in sorted(clusters.items(), key=lambda item: item[0][2]):
            if len(members) < 2:
                continue
            survivor, conflict = self._choose_survivor(members)
            if conflict:
                results.append(
                    DuplicateCleanupCluster(
                        territory_key=key,
                        candidate_ids=tuple(item.id for item in members),
                        survivor_candidate_id=None,
                        actions=(),
                        conflict=True,
                    )
                )
                continue
            assert survivor is not None
            actions = [
                DuplicateCleanupAction(
                    candidate_id=survivor.id,
                    territory_key=key,
                    action="KEEP",
                    survivor_candidate_id=survivor.id,
                    reason_codes=("EDITORIAL_NOVELTY_PASS",),
                )
            ]
            actions.extend(
                DuplicateCleanupAction(
                    candidate_id=item.id,
                    territory_key=key,
                    action="REJECT_SUPERSEDED",
                    survivor_candidate_id=survivor.id,
                    reason_codes=(
                        "EDITORIAL_TERRITORY_DUPLICATE",
                        "EDITORIAL_DUPLICATE_SUPERSEDED",
                        f"SURVIVOR_CANDIDATE_ID:{survivor.id}",
                    ),
                )
                for item in members
                if item.id != survivor.id
            )
            results.append(
                DuplicateCleanupCluster(
                    territory_key=key,
                    candidate_ids=tuple(item.id for item in members),
                    survivor_candidate_id=survivor.id,
                    actions=tuple(actions),
                )
            )
        for candidate in missing_authority:
            hard_delete = self._safe_to_hard_delete(candidate)
            results.append(
                DuplicateCleanupCluster(
                    territory_key=None,
                    candidate_ids=(candidate.id,),
                    survivor_candidate_id=None,
                    actions=(
                        DuplicateCleanupAction(
                            candidate_id=candidate.id,
                            territory_key=None,
                            action="HARD_DELETE" if hard_delete else "REJECT_SUPERSEDED",
                            survivor_candidate_id=None,
                            reason_codes=(
                                ("EDITORIAL_DUPLICATE_HARD_DELETED",)
                                if hard_delete
                                else ("EDITORIAL_NOVELTY_AUTHORITY_MISSING",)
                            ),
                        ),
                    ),
                )
            )
        return results

    def apply(self, *, clusters: Iterable[DuplicateCleanupCluster], actor: Any) -> list[DuplicateCleanupCluster]:
        """Apply an already reviewed plan through legal candidate transitions."""

        from app.contracts.m5 import EditorialIdeaCandidateTransition
        from app.services.editorial_research import EditorialResearchService

        editorial = EditorialResearchService(self.session)
        applied: list[DuplicateCleanupCluster] = []
        for cluster in clusters:
            if cluster.conflict:
                applied.append(cluster)
                continue
            survivor = (
                self.session.get(EditorialIdeaCandidate, cluster.survivor_candidate_id)
                if cluster.survivor_candidate_id is not None
                else None
            )
            if survivor is not None and cluster.territory_key is not None:
                evaluation = NoveltyEvaluation(
                    candidate_id=survivor.id,
                    territory_key=cluster.territory_key,
                    state="PASS",
                    matched_candidate_ids=(),
                    matched_published_refs=(),
                    reason_codes=("EDITORIAL_NOVELTY_PASS",),
                    gate_version=EDITORIAL_NOVELTY_GATE_VERSION,
                    evaluation_hash=content_hash(
                        {
                            "candidate_id": str(survivor.id),
                            "territory_key": cluster.territory_key,
                            "state": "PASS",
                            "gate_version": EDITORIAL_NOVELTY_GATE_VERSION,
                            "cleanup": True,
                        }
                    ),
                )
                self.novelty.persist(survivor, evaluation)
            for action in cluster.actions:
                if action.action == "HARD_DELETE":
                    candidate = self.session.get(EditorialIdeaCandidate, action.candidate_id)
                    if candidate is not None and self._safe_to_hard_delete(candidate):
                        self._hard_delete(candidate=candidate, actor=actor)
                    continue
                if action.action != "REJECT_SUPERSEDED":
                    continue
                candidate = self.session.get(EditorialIdeaCandidate, action.candidate_id)
                if candidate is None or candidate.stage in {"REJECTED", "EXPIRED"}:
                    continue
                editorial.transition_candidate(
                    candidate_id=candidate.id,
                    data=EditorialIdeaCandidateTransition(
                        target_stage="REJECTED",
                        reason_codes=list(action.reason_codes),
                    ),
                    actor=actor,
                )
                if action.territory_key is not None:
                    candidate.editorial_territory_key = action.territory_key
                    candidate.editorial_novelty_receipt = {
                        "gate_version": EDITORIAL_NOVELTY_GATE_VERSION,
                        "candidate_id": str(candidate.id),
                        "territory_key": action.territory_key,
                        "state": "BLOCK",
                        "matched_candidate_ids": [str(action.survivor_candidate_id)]
                        if action.survivor_candidate_id
                        else [],
                        "matched_published_refs": [],
                        "reason_codes": list(action.reason_codes),
                        "evaluation_hash": content_hash(
                            {
                                "candidate_id": str(candidate.id),
                                "territory_key": action.territory_key,
                                "action": "REJECT_SUPERSEDED",
                                "survivor_candidate_id": str(action.survivor_candidate_id)
                                if action.survivor_candidate_id
                                else None,
                            }
                        ),
                    }
            self.session.flush()
            applied.append(cluster)
        return applied

    def report(self, clusters: Iterable[DuplicateCleanupCluster]) -> list[dict[str, Any]]:
        """Render the reviewed plan with the authority used for every action."""

        rendered = cleanup_report(clusters)
        from app.services.script_qualification import TopicDefinitionService

        for cluster in rendered:
            members: list[dict[str, Any]] = []
            for candidate_text in cluster["candidate_ids"]:
                candidate = self.session.get(EditorialIdeaCandidate, uuid.UUID(candidate_text))
                if candidate is None:
                    continue
                topic_eligibility = TopicDefinitionService(self.session).current_eligibility(
                    candidate
                )
                preflight = self.session.scalar(
                    select(IdeaMarketPreflight)
                    .where(IdeaMarketPreflight.editorial_idea_candidate_id == candidate.id)
                    .order_by(IdeaMarketPreflight.created_at.desc())
                )
                admissions = list(
                    self.session.scalars(
                        select(ProjectAdmissionDecision).where(
                            ProjectAdmissionDecision.editorial_idea_candidate_id
                            == candidate.id
                        )
                    ).all()
                )
                admission_ids = [item.id for item in admissions]
                members.append(
                    {
                        "candidate_id": str(candidate.id),
                        "title": candidate.proposed_title,
                        "angle": candidate.proposed_angle,
                        "stage": candidate.stage,
                        "created_at": candidate.created_at.isoformat(),
                        "current_topic_eligibility": {
                            "eligible": topic_eligibility.eligible,
                            "state": topic_eligibility.state,
                            "reason_codes": list(topic_eligibility.reason_codes),
                        },
                        "preflight_state": (
                            {
                                "decision": preflight.decision,
                                "policy_fit_state": preflight.policy_fit_state,
                            }
                            if preflight is not None
                            else None
                        ),
                        "slot_refs": [
                            str(item)
                            for item in self.session.scalars(
                                select(LongFormPublishSlot.id).where(
                                    LongFormPublishSlot.reserved_candidate_id
                                    == candidate.id
                                )
                            ).all()
                        ],
                        "qualification_refs": [
                            str(item)
                            for item in self.session.scalars(
                                select(ScriptQualificationRun.id).where(
                                    ScriptQualificationRun.editorial_idea_candidate_id
                                    == candidate.id
                                )
                            ).all()
                        ],
                        "admission_refs": [str(item) for item in admission_ids],
                        "project_refs": [
                            str(item.admitted_video_project_id)
                            for item in admissions
                            if item.admitted_video_project_id is not None
                        ],
                        "workflow_refs": [
                            str(item)
                            for item in self.session.scalars(
                                select(ProductionWorkflowRun.id).where(
                                    ProductionWorkflowRun.project_admission_decision_id.in_(
                                        admission_ids or [uuid.UUID(int=0)]
                                    )
                                )
                            ).all()
                        ],
                        "published_refs": (
                            [f"editorial-candidate:{candidate.id}"]
                            if candidate.stage == "PUBLISHED"
                            else []
                        ),
                    }
                )
            cluster["members"] = members
        return rendered

    def _choose_survivor(
        self, candidates: list[EditorialIdeaCandidate]
    ) -> tuple[EditorialIdeaCandidate | None, bool]:
        active_workflows = [item for item in candidates if self._has_active_workflow(item)]
        if len(active_workflows) > 1:
            return None, True

        def rank(candidate: EditorialIdeaCandidate) -> tuple[int, Any, str]:
            if candidate in active_workflows:
                priority = 0
            elif self._has_active_qualification_or_slot(candidate):
                priority = 1
            elif self._has_replacement_authority(candidate):
                priority = 2
            elif self._currently_eligible(candidate):
                priority = 3
            elif self._has_terminal_qualification(candidate):
                # A terminal provider receipt is preserved, but cannot win
                # future runway capacity merely because it has durable lineage.
                priority = 5
            else:
                priority = 4
            return priority, candidate.created_at, str(candidate.id)

        return sorted(candidates, key=rank)[0], False

    def _has_active_workflow(self, candidate: EditorialIdeaCandidate) -> bool:
        return bool(
            self.session.scalar(
                select(ProductionWorkflowRun.id).where(
                    ProductionWorkflowRun.project_admission_decision_id.in_(
                        select(ProjectAdmissionDecision.id).where(
                            ProjectAdmissionDecision.editorial_idea_candidate_id
                            == candidate.id,
                            ProjectAdmissionDecision.decision == "ADMIT",
                        )
                    ),
                    ProductionWorkflowRun.state.in_(ACTIVE_WORKFLOW_STATES),
                )
            )
        )

    def _has_active_qualification_or_slot(self, candidate: EditorialIdeaCandidate) -> bool:
        if self._has_terminal_qualification(candidate):
            return False
        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.editorial_idea_candidate_id == candidate.id)
            .order_by(ScriptQualificationRun.created_at.desc())
        )
        if qualification is not None and qualification.state in ACTIVE_QUALIFICATION_STATES:
            return True
        return bool(
            self.session.scalar(
                select(LongFormPublishSlot.id).where(
                    LongFormPublishSlot.reserved_candidate_id == candidate.id,
                    LongFormPublishSlot.state.in_({"QUALIFICATION_RESERVED", "RESERVED"}),
                )
            )
        )

    def _has_replacement_authority(self, candidate: EditorialIdeaCandidate) -> bool:
        return (
            candidate.replacement_authority_id is not None
            and not self._has_terminal_qualification(candidate)
        )

    def _has_terminal_qualification(self, candidate: EditorialIdeaCandidate) -> bool:
        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.editorial_idea_candidate_id == candidate.id)
            .order_by(
                ScriptQualificationRun.created_at.desc(), ScriptQualificationRun.id.desc()
            )
        )
        return bool(
            qualification is not None
            and qualification.state in TERMINAL_QUALIFICATION_STATES
        )

    def _currently_eligible(self, candidate: EditorialIdeaCandidate) -> bool:
        from app.services.editorial_specificity import EditorialSpecificityService
        from app.services.script_qualification import TopicDefinitionService

        return bool(
            TopicDefinitionService(self.session).current_eligibility(candidate).eligible
            and EditorialSpecificityService(self.session).current_pass(candidate)
            and self.novelty._current_strict_preflight_pass(candidate)
        )

    def _safe_to_hard_delete(self, candidate: EditorialIdeaCandidate) -> bool:
        """Prove a candidate has no durable dependent or audit lineage.

        This intentionally errs on the side of a legal terminal transition.
        The review is explicit rather than relying on an opaque cascade or raw
        SQL, and is repeated immediately before deletion.
        """

        candidate_id = candidate.id
        dependent_checks = (
            select(EditorialTopicDefinition.id).where(
                EditorialTopicDefinition.editorial_idea_candidate_id == candidate_id
            ),
            select(EditorialTopicDefinitionGateReceipt.id).where(
                EditorialTopicDefinitionGateReceipt.editorial_idea_candidate_id
                == candidate_id
            ),
            select(IdeaMarketPreflight.id).where(
                IdeaMarketPreflight.editorial_idea_candidate_id == candidate_id
            ),
            select(SearchIntentMap.id).where(
                SearchIntentMap.editorial_idea_candidate_id == candidate_id
            ),
            select(AudienceTargetPack.id).where(
                AudienceTargetPack.editorial_idea_candidate_id == candidate_id
            ),
            select(LongFormPublishSlot.id).where(
                LongFormPublishSlot.reserved_candidate_id == candidate_id
            ),
            select(ScriptQualificationRun.id).where(
                ScriptQualificationRun.editorial_idea_candidate_id == candidate_id
            ),
            select(ProjectAdmissionDecision.id).where(
                ProjectAdmissionDecision.editorial_idea_candidate_id == candidate_id
            ),
            select(CadenceEvaluationReceipt.id).where(
                CadenceEvaluationReceipt.selected_candidate_id == candidate_id
            ),
            select(ScriptContractReplacementAuthority.id).where(
                (ScriptContractReplacementAuthority.replaces_candidate_id == candidate_id)
                | (ScriptContractReplacementAuthority.replacement_candidate_id == candidate_id)
            ),
            select(EditorialIdeaCandidate.id).where(
                (EditorialIdeaCandidate.parent_candidate_id == candidate_id)
                | (EditorialIdeaCandidate.replaces_candidate_id == candidate_id)
            ),
            select(AuditEvent.id).where(AuditEvent.target_id == candidate_id),
            select(DomainEvent.id).where(DomainEvent.aggregate_id == candidate_id),
        )
        return not any(self.session.scalar(statement) is not None for statement in dependent_checks)

    def _hard_delete(self, *, candidate: EditorialIdeaCandidate, actor: ActorContext) -> None:
        candidate_id = candidate.id
        company_id = candidate.company_id
        self.session.delete(candidate)
        self.session.flush()
        AuditService(self.session).append(
            AuditEnvelope(
                actor_type=str(actor.actor_type),
                actor_id=actor.actor_id,
                action="EDITORIAL_DUPLICATE_HARD_DELETED",
                target_type="editorial_idea_candidate",
                target_id=candidate_id,
                reason_code="EDITORIAL_DUPLICATE_HARD_DELETED",
                correlation_id=f"editorial-novelty-dedupe:{candidate_id}",
                payload={"candidate_id": str(candidate_id)},
            ),
            company_id=company_id,
        )


def cleanup_report(clusters: Iterable[DuplicateCleanupCluster]) -> list[dict[str, Any]]:
    """Return a JSON-safe, deterministic maintenance receipt payload."""

    return [
        {
            "territory_key": cluster.territory_key,
            "candidate_ids": [str(item) for item in cluster.candidate_ids],
            "survivor_candidate_id": str(cluster.survivor_candidate_id)
            if cluster.survivor_candidate_id
            else None,
            "conflict": cluster.conflict,
            "actions": [
                {
                    **asdict(action),
                    "candidate_id": str(action.candidate_id),
                    "survivor_candidate_id": str(action.survivor_candidate_id)
                    if action.survivor_candidate_id
                    else None,
                    "reason_codes": list(action.reason_codes),
                }
                for action in cluster.actions
            ],
        }
        for cluster in clusters
    ]
