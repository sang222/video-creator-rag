"""Durable, fail-closed replenishment of the editorial runway.

This service deliberately stops at editorial research.  It never admits a
project, reserves a production slot, or dispatches the production workflow.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.m5 import (
    ChannelStatePackSnapshotCreate,
    ContextPackSnapshotCreate,
    EditorialCalendarSlotCreate,
    EditorialIdeaCandidateCreate,
    EditorialIdeaCandidateTransition,
    EditorialResearchRunCreate,
    IdeaMarketPreflightCreate,
    RetrievalPlanSnapshotCreate,
)
from app.contracts.vcos_v2 import (
    AssignmentCandidate,
    AssignmentMode,
    AssignmentResolverInput,
    ContentMode,
    ProductionLane,
    SeriesPlanState,
    SeriesRunState,
    DecisionReversibility,
    StrategicIntent,
    StrategicLineageV2,
)
from app.core.actor import ActorContext
from app.core.config import get_settings
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.launch_cadence import (
    FirstChannelLaunchPolicyVersion,
    LaunchRun,
)
from app.db.models.m5 import (
    EditorialCalendarSlot,
    EditorialIdeaCandidate,
    EditorialResearchRun,
    SearchDemandEvidence,
)
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.services.config_registry import content_hash
from app.services.editorial_novelty import EditorialNoveltyService, normalize_editorial_text
from app.services.editorial_specificity import (
    EDITORIAL_IDEA_SYNTHESIS_VERSION,
    EDITORIAL_SPECIFICITY_GATE_VERSION,
    EditorialIdeaSynthesisService,
)
from app.db.models.r3d1 import ContentCategory
from app.services.editorial_fresh_evidence import (
    EditorialEvidenceProviderActivationService,
    FreshEvidenceCollector,
    OpenAIWebEvidenceProvider,
)
from app.services.editorial_research import EditorialResearchService
from app.services.m5 import (
    ChannelStatePackService,
    EditorialCalendarService,
    IdeaMarketPreflightService,
    ResourceResolverService,
)
from app.services.production_start_readiness import (
    resolve_budget_authority,
    resolve_provider_authority,
)
from app.services.vcos_v2 import AssignmentResolutionError, DeterministicAssignmentResolver


# v8 makes the current Topic Definition gate part of the durable scheduled
# attempt identity. A same-day historical run that only produced generic,
# discovery-derived candidates cannot suppress one bounded repair attempt.
RUNWAY_REPLENISHMENT_SCHEMA = "vcos.editorial-runway-replenishment.v10"
_METADATA_KEY = "runway_replenishment"
_ACTIVE_STATUSES = {"PENDING", "RUNNING"}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _scheduled_scope_key(
    *,
    launch_run_id: str,
    launch_policy_version_id: str,
    policy_snapshot_id: str,
    policy_hash: str,
    topic_gate_version: str,
    editorial_idea_synthesis_version: str,
    editorial_specificity_gate_version: str,
    editorial_territory_version: str,
    editorial_evidence_provider_key: str,
    editorial_evidence_provider_config_hash: str,
    editorial_evidence_provider_state: str,
) -> str:
    """Build the durable semantic identity of a scheduled replenishment.

    ``topic_gate_version`` is deliberately part of this identity: a changed
    proposal-to-topic authority is a new semantic decision, not a retry of a
    historical same-day attempt.
    """

    return _canonical_hash(
        {
            "schema": RUNWAY_REPLENISHMENT_SCHEMA,
            "launch_run_id": launch_run_id,
            "launch_policy_version_id": launch_policy_version_id,
            "policy_snapshot_id": policy_snapshot_id,
            "policy_hash": policy_hash,
            "topic_gate_version": topic_gate_version,
            "editorial_idea_synthesis_version": editorial_idea_synthesis_version,
            "editorial_specificity_gate_version": editorial_specificity_gate_version,
            "editorial_territory_version": editorial_territory_version,
            "editorial_evidence_provider_key": editorial_evidence_provider_key,
            "editorial_evidence_provider_config_hash": editorial_evidence_provider_config_hash,
            "editorial_evidence_provider_state": editorial_evidence_provider_state,
        }
    )


@dataclass(frozen=True, slots=True)
class RunwayReplenishmentResult:
    launch_run_id: uuid.UUID
    status: str
    editorial_research_run_id: uuid.UUID | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EditorialModeDecision:
    """Frozen assignment outcome before any source/evidence work starts."""

    content_mode: str | None
    assignment_mode: str | None
    reason_codes: tuple[str, ...]
    resolver_version: str | None = None
    resolver_input_hash: str | None = None
    standalone_authority: dict[str, Any] | None = None
    series_binding: dict[str, Any] | None = None


class EditorialRunwayReplenishmentService:
    """Reconcile active launches with one idempotent editorial run per day.

    The locked ``LaunchRun`` row is the concurrency boundary.  All paths that
    create automatic runs enter through this service, so two worker processes
    cannot manufacture equivalent scheduled research work.
    """

    def __init__(
        self, session: Session, *, now: Callable[[], datetime] = utc_now
    ) -> None:
        self.session = session
        self.now = now

    def reconcile_active_launches(
        self, *, actor: ActorContext
    ) -> list[RunwayReplenishmentResult]:
        launches = list(
            self.session.scalars(
                select(LaunchRun)
                .where(LaunchRun.state == "ACTIVE")
                .order_by(LaunchRun.id)
                .with_for_update(skip_locked=True)
            ).all()
        )
        return [self._reconcile_locked(run=run, actor=actor) for run in launches]

    def _reconcile_locked(
        self, *, run: LaunchRun, actor: ActorContext
    ) -> RunwayReplenishmentResult:
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, run.launch_policy_version_id
        )
        if policy is None or policy.state != "APPROVED":
            return RunwayReplenishmentResult(
                launch_run_id=run.id,
                status="SKIPPED",
                reason_codes=("RUNWAY_REPLENISHMENT_LAUNCH_POLICY_NOT_APPROVED",),
            )
        if (
            policy.company_id != run.company_id
            or policy.channel_workspace_id != run.channel_workspace_id
        ):
            return RunwayReplenishmentResult(
                launch_run_id=run.id,
                status="SKIPPED",
                reason_codes=("RUNWAY_REPLENISHMENT_LAUNCH_AUTHORITY_MISMATCH",),
            )
        # Row count is not runway diversity.  Only a current topic/preflight/
        # novelty authority may occupy one distinct editorial territory.
        from app.services.script_qualification import TOPIC_GATE_VERSION
        from app.services.editorial_novelty import EDITORIAL_TERRITORY_SCHEMA

        novelty = EditorialNoveltyService(self.session)
        runway_counts = novelty.runway_counts(
            channel_workspace_id=run.channel_workspace_id,
            policy_snapshot_id=policy.policy_snapshot_id,
        )
        if runway_counts.distinct_eligible_territory_count >= policy.greenlight_target:
            return RunwayReplenishmentResult(
                launch_run_id=run.id,
                status="SATISFIED",
                reason_codes=("RUNWAY_REPLENISHMENT_DISTINCT_TERRITORY_TARGET_MET",),
            )

        policy_snapshot = self.session.get(
            CompiledChannelPolicySnapshot, policy.policy_snapshot_id
        )
        if policy_snapshot is None:
            return RunwayReplenishmentResult(
                launch_run_id=run.id,
                status="SKIPPED",
                reason_codes=("RUNWAY_REPLENISHMENT_POLICY_SNAPSHOT_MISSING",),
            )
        activation = EditorialEvidenceProviderActivationService(self.session).activate(
            policy_snapshot_id=str(policy_snapshot.id),
            policy_snapshot_hash=policy_snapshot.content_hash,
            company_id=str(run.company_id),
        )
        run_date = self._run_date(policy=policy)
        scope_key = _scheduled_scope_key(
            launch_run_id=str(run.id),
            launch_policy_version_id=str(policy.id),
            policy_snapshot_id=str(policy.policy_snapshot_id),
            policy_hash=policy.canonical_hash,
            topic_gate_version=TOPIC_GATE_VERSION,
            editorial_idea_synthesis_version=EDITORIAL_IDEA_SYNTHESIS_VERSION,
            editorial_specificity_gate_version=EDITORIAL_SPECIFICITY_GATE_VERSION,
            editorial_territory_version=EDITORIAL_TERRITORY_SCHEMA,
            editorial_evidence_provider_key=activation.authority.provider_key,
            editorial_evidence_provider_config_hash=activation.authority.config_hash,
            editorial_evidence_provider_state=activation.authority.state,
        )
        attempt_key = _canonical_hash({"scope_key": scope_key, "run_date": run_date})
        existing = self._existing_equivalent(
            run=run,
            policy=policy,
            scope_key=scope_key,
            attempt_key=attempt_key,
        )
        if existing is not None:
            return RunwayReplenishmentResult(
                launch_run_id=run.id,
                status=("ACTIVE" if existing.status in _ACTIVE_STATUSES else "COOLDOWN"),
                editorial_research_run_id=existing.id,
                reason_codes=tuple(existing.reason_codes or []),
            )

        exclusion_authority = novelty.occupied_exclusion_authority(
            channel_workspace_id=run.channel_workspace_id,
            policy_snapshot_id=policy.policy_snapshot_id,
        )
        research_slot, slot_blocker = self._create_research_slot(
            run=run,
            policy=policy,
            run_date=run_date,
        )
        mode_decision = self._resolve_mode(
            run=run,
            policy=policy,
            editorial_calendar_slot=research_slot,
        )
        blockers, diagnostics = self._capability_blockers(
            run=run,
            policy=policy,
            mode_decision=mode_decision,
        )
        if slot_blocker is not None:
            blockers.append(slot_blocker)
        if research_slot is not None:
            diagnostics["editorial_calendar_slot_id"] = str(research_slot.id)
        diagnostics["mode_decision"] = {
            "content_mode": mode_decision.content_mode,
            "assignment_mode": mode_decision.assignment_mode,
            "reason_codes": list(mode_decision.reason_codes),
            "resolver_version": mode_decision.resolver_version,
            "resolver_input_hash": mode_decision.resolver_input_hash,
            "standalone_authority": mode_decision.standalone_authority,
        }

        editorial = EditorialResearchService(self.session)
        research_run = editorial.create_run(
            data=EditorialResearchRunCreate(
                company_id=run.company_id,
                channel_workspace_id=run.channel_workspace_id,
                channel_profile_version_id=policy.channel_profile_version_id,
                policy_snapshot_id=policy.policy_snapshot_id,
                editorial_calendar_slot_id=(
                    research_slot.id if research_slot is not None else None
                ),
                run_date=run_date,
                trigger_type="SCHEDULED",
                reason_codes=["RUNWAY_REPLENISHMENT_REQUESTED"],
                metadata={
                    _METADATA_KEY: {
                        "schema_version": RUNWAY_REPLENISHMENT_SCHEMA,
                        "launch_run_id": str(run.id),
                        "launch_policy_version_id": str(policy.id),
                        "policy_snapshot_id": str(policy.policy_snapshot_id),
                        "policy_hash": policy.canonical_hash,
                        "scope_key": scope_key,
                        "attempt_key": attempt_key,
                        "raw_greenlit_row_count_before": runway_counts.raw_greenlit_rows,
                        "current_eligible_greenlit_row_count_before": (
                            runway_counts.current_eligible_greenlit_rows
                        ),
                        "distinct_eligible_territory_count_before": (
                            runway_counts.distinct_eligible_territory_count
                        ),
                        "greenlight_target": policy.greenlight_target,
                        "provider_calls_allowed": True,
                        "max_editorial_research_provider_calls": 1,
                        "max_editorial_idea_synthesis_calls": 1,
                        "mode_decision": diagnostics["mode_decision"],
                        "exclusion_authority": exclusion_authority,
                    }
                },
            ),
            actor=actor,
        )
        editorial.start_run(run_id=research_run.id, actor=actor)

        context_id: uuid.UUID | None = None
        state_id: uuid.UUID | None = None
        if research_slot is not None:
            try:
                context_id, state_id = self._freeze_context(
                    run=run,
                    policy=policy,
                    research_run=research_run,
                    editorial_calendar_slot=research_slot,
                )
                editorial.attach_context_snapshots(
                    run_id=research_run.id,
                    context_pack_snapshot_id=context_id,
                    channel_state_pack_snapshot_id=state_id,
                    actor=actor,
                )
            except ValidationFailureError as exc:
                blockers.append("RUNWAY_REPLENISHMENT_CONTEXT_FREEZE_BLOCKED")
                diagnostics["context_freeze_error"] = str(exc)
        fresh_evidence = FreshEvidenceCollector(self.session).inspect_authority(
            policy_snapshot_id=str(policy.policy_snapshot_id),
            policy_snapshot_hash=policy_snapshot.content_hash,
        )
        diagnostics["fresh_evidence"] = {
            "provider_state": fresh_evidence.state,
            "provider_key": fresh_evidence.provider_key,
            "provider_config_hash": fresh_evidence.config_hash,
            "reason_codes": list(fresh_evidence.reason_codes),
            "network_call_made": False,
            "collector_receipt": {
                "schema_version": "vcos.editorial-fresh-evidence.v1",
                "editorial_research_run_id": str(research_run.id),
                "context_pack_snapshot_id": str(context_id) if context_id else None,
                "provider_state": fresh_evidence.state,
                "provider_key": fresh_evidence.provider_key,
                "network_call_made": False,
                "reason_codes": list(fresh_evidence.reason_codes),
            },
        }
        if not fresh_evidence.ready:
            blockers.extend(fresh_evidence.reason_codes)
        elif context_id is None or research_slot is None:
            blockers.append("RUNWAY_REPLENISHMENT_CONTEXT_FREEZE_BLOCKED")
        elif not blockers:
            research_question = self._research_question(
                research_slot=research_slot,
                mode_decision=mode_decision,
                exclusion_authority=exclusion_authority,
            )
            settings = get_settings()
            provider = OpenAIWebEvidenceProvider(
                api_key=(
                    settings.openai_api_key.get_secret_value()
                    if settings.openai_api_key is not None
                    else None
                ),
                policy=fresh_evidence.policy or {},
            )
            collection = FreshEvidenceCollector(self.session).collect(
                authority=fresh_evidence,
                provider=provider,
                company_id=str(run.company_id),
                channel_workspace_id=str(run.channel_workspace_id),
                editorial_research_run_id=str(research_run.id),
                context_pack_snapshot_id=str(context_id),
                research_question=research_question,
            )
            diagnostics["fresh_evidence"] = {
                "provider_state": collection.authority.state,
                "provider_key": collection.authority.provider_key,
                "provider_config_hash": collection.authority.config_hash,
                "reason_codes": list(collection.authority.reason_codes),
                "network_call_made": bool(
                    (collection.receipt or {}).get("network_call_made")
                ),
                "collector_receipt": collection.receipt,
                "research_question_hash": _canonical_hash(
                    {"research_question": research_question}
                ),
            }
            if collection.ok:
                try:
                    candidate, preflight = self._create_candidate_and_preflight(
                        editorial=editorial,
                        research_run=research_run,
                        research_slot=research_slot,
                        context_pack_snapshot_id=context_id,
                        channel_state_pack_snapshot_id=state_id,
                        mode_decision=mode_decision,
                        collection_receipt=collection.receipt or {},
                        evidence_refs=list(collection.evidence_refs),
                        exclusion_authority=exclusion_authority,
                        research_question=research_question,
                        actor=actor,
                    )
                except ValidationFailureError as exc:
                    error_code = str(exc)
                    blockers.append(
                        error_code
                        if error_code.startswith("EDITORIAL_")
                        else "RUNWAY_REPLENISHMENT_STRICT_PREFLIGHT_BLOCKED"
                    )
                    diagnostics["editorial_processing_error"] = error_code
                else:
                    diagnostics["editorial_outcome"] = {
                        "candidate_id": str(candidate.id),
                        "candidate_stage": candidate.stage,
                        "strict_preflight_id": str(preflight.id),
                        "strict_preflight_decision": preflight.decision,
                        "strict_preflight_reason_codes": list(preflight.reason_codes),
                    }
            else:
                blockers.extend(collection.authority.reason_codes)
        research_run.metadata_ = {
            **(research_run.metadata_ or {}),
            _METADATA_KEY: {
                **((research_run.metadata_ or {}).get(_METADATA_KEY) or {}),
                "context_pack_snapshot_id": str(context_id) if context_id else None,
                "channel_state_pack_snapshot_id": str(state_id) if state_id else None,
                "capability_diagnostics": diagnostics,
            },
        }
        blockers = list(dict.fromkeys(blockers))
        if blockers:
            editorial.block_run(
                run_id=research_run.id,
                reason_codes=blockers,
                actor=actor,
            )
            status = "BLOCKED"
            reason_codes = tuple(blockers)
        else:
            editorial.complete_run(run_id=research_run.id, actor=actor)
            outcome = diagnostics.get("editorial_outcome") or {}
            status = "COMPLETED"
            reason_codes = tuple(
                outcome.get("strict_preflight_reason_codes")
                or ["EDITORIAL_RESEARCH_COMPLETED"]
            )
        return RunwayReplenishmentResult(
            launch_run_id=run.id,
            status=status,
            editorial_research_run_id=research_run.id,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _research_question(
        *,
        research_slot: EditorialCalendarSlot,
        mode_decision: EditorialModeDecision,
        exclusion_authority: dict[str, list[str]],
    ) -> str:
        """Derive one bounded discovery question from frozen editorial facts."""

        goal = (research_slot.production_goal or "").strip()
        pillar = (research_slot.content_pillar or "").strip()
        if not goal or not pillar or mode_decision.content_mode is None:
            raise ValidationFailureError("EDITORIAL_RESEARCH_QUESTION_AUTHORITY_MISSING")
        if mode_decision.content_mode == ContentMode.SERIES_EPISODE.value:
            binding = mode_decision.series_binding or {}
            series_title = str(binding.get("series_display_name") or "the active series")
            episode_delta = str(binding.get("episode_delta") or "the next bounded episode")
            prompt = (
                "Find current first-party OpenAI documentation that can ground "
                f"a US English long-form episode for {series_title}. The episode "
                f"must {episode_delta}. Return only official documentation URLs; "
                "do not make market, ROI, or performance claims."
            )
            return f"{prompt} {EditorialRunwayReplenishmentService._exclusion_prompt(exclusion_authority)}"
        if mode_decision.content_mode != ContentMode.STANDALONE.value:
            raise ValidationFailureError("EDITORIAL_RESEARCH_QUESTION_AUTHORITY_MISSING")
        prompt = (
            "Find current first-party OpenAI documentation that can ground a "
            "US English long-form standalone editorial idea for the approved "
            f"pillar '{pillar}' and frozen production goal '{goal}'. Return "
            "only official documentation URLs; do not make market, ROI, "
            "time-saving, or earnings claims."
        )
        return f"{prompt} {EditorialRunwayReplenishmentService._exclusion_prompt(exclusion_authority)}"

    @staticmethod
    def _exclusion_prompt(exclusion_authority: dict[str, list[str]]) -> str:
        items = [
            *exclusion_authority.get("excluded_canonical_source_urls", []),
            *exclusion_authority.get("excluded_editorial_questions", []),
        ]
        if not items:
            return "Return a materially different documented question and learning outcome."
        compact = "; ".join(str(item) for item in items[:12])
        return (
            "Do not return documentation already represented by these current "
            f"territories: {compact}. The source must support a materially different "
            "editorial question and learning outcome."
        )

    def _create_candidate_and_preflight(
        self,
        *,
        editorial: EditorialResearchService,
        research_run: EditorialResearchRun,
        research_slot: EditorialCalendarSlot,
        context_pack_snapshot_id: uuid.UUID,
        channel_state_pack_snapshot_id: uuid.UUID | None,
        mode_decision: EditorialModeDecision,
        collection_receipt: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
        exclusion_authority: dict[str, list[str]],
        research_question: str,
        actor: ActorContext,
    ):
        if mode_decision.content_mode not in {
            ContentMode.STANDALONE.value,
            ContentMode.SERIES_EPISODE.value,
        }:
            raise ValidationFailureError("EDITORIAL_CANDIDATE_MODE_AUTHORITY_MISSING")
        if not evidence_refs:
            raise ValidationFailureError("EDITORIAL_CANONICAL_EVIDENCE_REQUIRED")
        from app.services.script_qualification import TopicDefinitionService, classify_source

        topic_evidence = None
        excluded_urls = {
            normalize_editorial_text(item)
            for item in exclusion_authority.get("excluded_canonical_source_urls", [])
        }
        topic_capable_found = False
        for ref in evidence_refs:
            try:
                evidence_id = uuid.UUID(str(ref["id"]))
            except (KeyError, TypeError, ValueError):
                continue
            candidate_evidence = self.session.get(SearchDemandEvidence, evidence_id)
            if candidate_evidence is None or classify_source(candidate_evidence) != "TOPIC_CAPABLE":
                continue
            topic_capable_found = True
            snapshot = (
                ((candidate_evidence.metadata_ or {}).get("editorial_fresh_evidence") or {}).get(
                    "source_snapshot"
                )
                or {}
            )
            canonical_url = normalize_editorial_text(
                snapshot.get("canonical_url") or candidate_evidence.source_ref
            )
            if canonical_url and canonical_url in excluded_urls:
                continue
            if candidate_evidence is not None:
                topic_evidence = candidate_evidence
                break
        if topic_evidence is None:
            raise ValidationFailureError(
                "RUNWAY_REPLENISHMENT_NOVEL_SOURCE_EXHAUSTED"
                if topic_capable_found
                else "EDITORIAL_TOPIC_CAPABLE_SOURCE_REQUIRED"
            )
        # The current evidence provider produces URLs and bounded fetched
        # snapshots, not a structured editorial proposition in the same
        # response.  Use exactly one bounded synthesis call; it is the only
        # permitted additional LLM effect and never retries until a proposal
        # passes.
        synthesis = EditorialIdeaSynthesisService(self.session).synthesize(
            research_run=research_run,
            evidence_refs=evidence_refs,
            content_mode=str(mode_decision.content_mode),
            series_binding=mode_decision.series_binding,
            research_question=research_question,
        )
        research_pack = dict(collection_receipt.get("research_pack") or {})
        research_pack["editorial_idea_synthesis"] = synthesis.receipt
        research_pack["content_hash"] = _canonical_hash(
            {key: value for key, value in research_pack.items() if key != "content_hash"}
        )
        collection_receipt["research_pack"] = research_pack
        parent = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(
                EditorialIdeaCandidate.channel_workspace_id == research_run.channel_workspace_id,
                EditorialIdeaCandidate.policy_snapshot_id == research_run.policy_snapshot_id,
                EditorialIdeaCandidate.stage == "GREENLIT",
                EditorialIdeaCandidate.parent_candidate_id.is_(None),
            )
            .order_by(EditorialIdeaCandidate.created_at)
        )
        parent_eligibility = (
            TopicDefinitionService(self.session).current_eligibility(parent)
            if parent is not None
            else None
        )
        repair_parent = (
            parent
            if parent_eligibility is not None
            and not parent_eligibility.eligible
            and parent.topic_repair_depth < 2
            else None
        )
        parent_topic_definition_id = (
            parent_eligibility.definition.id
            if parent_eligibility and parent_eligibility.definition
            else None
        )
        last_candidate = None
        last_preflight = None
        for proposal in synthesis.proposals:
            proposal_body = proposal.model_dump(mode="json")
            candidate = editorial.add_candidate(
                data=EditorialIdeaCandidateCreate(
                    editorial_research_run_id=research_run.id,
                    context_pack_snapshot_id=context_pack_snapshot_id,
                    channel_state_pack_snapshot_id=channel_state_pack_snapshot_id,
                    stage="RESEARCHED",
                    proposed_title=proposal.proposed_title,
                    proposed_angle=proposal.proposed_angle,
                    parent_candidate_id=repair_parent.id if repair_parent is not None else None,
                    topic_repair_depth=(repair_parent.topic_repair_depth + 1 if repair_parent is not None else 0),
                    proposed_format="LONG_FORM",
                    proposed_pillar=research_slot.content_pillar,
                    suggested_series_plan_id=(
                        uuid.UUID(str((mode_decision.series_binding or {})["series_plan_id"]))
                        if mode_decision.content_mode == ContentMode.SERIES_EPISODE.value
                        else None
                    ),
                    editorial_idea_proposal=proposal_body,
                    rationale={
                        "schema_version": "vcos.editorial-evidence-candidate.v2",
                        "source_pack": collection_receipt.get("source_pack"),
                        "research_pack": collection_receipt.get("research_pack"),
                        "editorial_idea_proposal": proposal_body,
                        "claim_evidence_map": [
                            {
                                "claim_scope": "approved editorial proposition",
                                "evidence_refs": [
                                    *proposal.primary_evidence_refs,
                                    *proposal.supporting_evidence_refs,
                                ],
                                "coverage_state": "PRESENT",
                            }
                        ],
                    },
                    evidence_refs=evidence_refs,
                    reason_codes=[
                        "FRESH_EVIDENCE_COLLECTED",
                        "EDITORIAL_IDEA_SYNTHESIZED",
                        "STRICT_PREFLIGHT_PENDING",
                    ],
                    confidence_level="HIGH",
                    experiment_phase="AUDIENCE_PROMISE",
                ),
                actor=actor,
            )
            topic_definition = TopicDefinitionService(self.session).create_from_editorial_idea_proposal(
                candidate=candidate,
                proposal=proposal_body,
                parent_topic_definition_id=parent_topic_definition_id,
            )
            topic_receipt = TopicDefinitionService(self.session).evaluate(topic_definition)
            if topic_receipt.state != "PASS":
                editorial.transition_candidate(
                    candidate_id=candidate.id,
                    data=EditorialIdeaCandidateTransition(
                        target_stage="REJECTED",
                        reason_codes=list(topic_receipt.reason_codes),
                    ),
                    actor=actor,
                )
                last_candidate = candidate
                continue
            preflight = IdeaMarketPreflightService(self.session).create_preflight(
                data=IdeaMarketPreflightCreate(
                    company_id=research_run.company_id,
                    channel_workspace_id=research_run.channel_workspace_id,
                    editorial_calendar_slot_id=research_slot.id,
                    editorial_research_run_id=research_run.id,
                    editorial_idea_candidate_id=candidate.id,
                    evidence_blob={
                        "claim_evidence_refs": [str(item["id"]) for item in evidence_refs],
                        "market_demand_evidence_refs": [],
                        "source_pack_hash": (collection_receipt.get("source_pack") or {}).get("content_hash"),
                        "research_pack_hash": (collection_receipt.get("research_pack") or {}).get("content_hash"),
                    },
                ),
                correlation_id=f"runway-replenishment:{research_run.id}",
            )
            last_candidate, last_preflight = candidate, preflight
            if preflight.decision != "PASS":
                if preflight.decision == "BLOCK":
                    editorial.transition_candidate(
                        candidate_id=candidate.id,
                        data=EditorialIdeaCandidateTransition(
                            target_stage="PREFLIGHT_BLOCK",
                            idea_market_preflight_id=preflight.id,
                            reason_codes=list(preflight.reason_codes),
                        ),
                        actor=actor,
                    )
                continue
            candidate = editorial.transition_candidate(
                candidate_id=candidate.id,
                data=EditorialIdeaCandidateTransition(
                    target_stage="PREFLIGHT_PASS",
                    idea_market_preflight_id=preflight.id,
                    reason_codes=list(preflight.reason_codes),
                ),
                actor=actor,
            )
            candidate = editorial.transition_candidate(
                candidate_id=candidate.id,
                data=EditorialIdeaCandidateTransition(
                    target_stage="GREENLIT",
                    idea_market_preflight_id=preflight.id,
                    reason_codes=[*preflight.reason_codes, "DETERMINISTIC_GREENLIGHT"],
                ),
                actor=actor,
            )
            if candidate.stage == "GREENLIT":
                return candidate, preflight
            last_candidate = candidate
        if not synthesis.proposals:
            raise ValidationFailureError("EDITORIAL_SPECIFIC_NOVEL_IDEA_EXHAUSTED")
        if last_candidate is not None and last_preflight is not None:
            raise ValidationFailureError("EDITORIAL_SPECIFIC_NOVEL_IDEA_EXHAUSTED")
        raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_NO_USABLE_PROPOSAL")

    def _first_launch_candidate_lineage(
        self,
        *,
        research_run: EditorialResearchRun,
    ) -> dict[str, Any]:
        """Freeze the approved first-launch experiment authority on its candidate.

        This is editorial authority, not an invented market score.  The strict
        preflight later validates its current policy/run and first-N position.
        """
        run = self.session.scalar(
            select(LaunchRun).where(
                LaunchRun.company_id == research_run.company_id,
                LaunchRun.channel_workspace_id == research_run.channel_workspace_id,
                LaunchRun.state == "ACTIVE",
            )
        )
        if run is None:
            raise ValidationFailureError("FIRST_LAUNCH_EXPERIMENT_ACTIVE_RUN_REQUIRED")
        policy = self.session.get(FirstChannelLaunchPolicyVersion, run.launch_policy_version_id)
        snapshot = self.session.get(CompiledChannelPolicySnapshot, research_run.policy_snapshot_id)
        payload = snapshot.compiled_payload if snapshot is not None else {}
        contract = payload.get("channel_contract_json") if isinstance(payload, dict) else None
        identity = contract.get("channel_identity") if isinstance(contract, dict) else None
        target = contract.get("target_audience") if isinstance(contract, dict) else None
        market = contract.get("market_locale") if isinstance(contract, dict) else None
        promise = identity.get("brand_promise") if isinstance(identity, dict) else None
        persona = target.get("primary_persona") if isinstance(target, dict) else None
        if policy is None or policy.state != "APPROVED" or not isinstance(promise, str) or not promise.strip() or not isinstance(persona, str) or not persona.strip():
            raise ValidationFailureError("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
        target_definition = {
            "audience_level": target.get("audience_level"),
            "audience_notes": target.get("audience_notes"),
            "desired_outcome": target.get("desired_outcome"),
            "market_locale": dict(market) if isinstance(market, dict) else {},
            "pain_points": list(target.get("pain_points") or []),
            "primary_persona": persona,
        }
        promise_version = f"channel-contract-snapshot-{snapshot.snapshot_version}"
        drift_version = f"channel-contract-drift-guard-{snapshot.snapshot_version}"
        criteria = {
            "criterion": "AUDIENCE_PROMISE_VALIDATION",
            "launch_policy_hash": policy.canonical_hash,
            "measurement_scope": "FIRST_N_PUBLIC_VIDEOS",
            "required_candidate_phase": "AUDIENCE_PROMISE",
        }
        criteria_version = "launch-audience-promise-v1"
        hypothesis = (
            "The frozen channel promise is relevant to the approved target audience "
            "when demonstrated through one bounded long-form video."
        )
        decision = DecisionReversibility.TWO_WAY_DOOR
        return StrategicLineageV2(
            audience_promise=promise.strip(),
            audience_promise_version=promise_version,
            audience_promise_hash=StrategicLineageV2.calculate_audience_promise_hash(
                audience_promise=promise.strip(), audience_promise_version=promise_version,
                target_audience_definition=target_definition, audience_drift_guard_version=drift_version,
            ),
            target_audience_definition=target_definition,
            audience_drift_guard_version=drift_version,
            strategic_intent=StrategicIntent.ACQUISITION,
            intent_success_criteria=criteria,
            intent_success_criteria_version=criteria_version,
            intent_success_criteria_hash=StrategicLineageV2.calculate_intent_success_criteria_hash(
                strategic_intent=StrategicIntent.ACQUISITION, intent_success_criteria=criteria,
                intent_success_criteria_version=criteria_version, experiment_hypothesis=hypothesis,
                primary_variable_under_test="audience_promise_validation", decision_reversibility=decision,
            ),
            experiment_hypothesis=hypothesis,
            primary_variable_under_test="audience_promise_validation",
            decision_reversibility=decision,
            active_launch_policy_version_id=policy.id,
            active_launch_policy_hash=policy.canonical_hash,
            active_launch_run_id=run.id,
            active_launch_run_hash=content_hash({
                "launch_key": run.launch_key, "launch_policy_hash": policy.canonical_hash,
                "launch_policy_version_id": str(policy.id), "launch_run_id": str(run.id),
                "launch_started_at": run.launch_started_at.isoformat() if run.launch_started_at else None,
                "preparation_started_on": run.preparation_started_on.isoformat(),
                "reason_codes": list(run.reason_codes or []), "state": run.state,
            }),
        ).model_dump(mode="python")

    def _freeze_context(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        research_run: EditorialResearchRun,
        editorial_calendar_slot: EditorialCalendarSlot,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        resolver = ResourceResolverService(self.session)
        plan = resolver.create_retrieval_plan(
            data=RetrievalPlanSnapshotCreate(
                purpose="EDITORIAL_RESEARCH",
                company_id=run.company_id,
                channel_workspace_id=run.channel_workspace_id,
                channel_profile_version_id=policy.channel_profile_version_id,
                policy_snapshot_id=policy.policy_snapshot_id,
                editorial_calendar_slot_id=editorial_calendar_slot.id,
                # Deliberately omit search evidence until an automatic source
                # authority supplies fresh, run-bound evidence.
                allowed_sources=[
                    "channel_profile",
                    "policy_snapshot",
                    "editorial_slot",
                    "review_tasks",
                    "gate_runs",
                    "provider_health",
                    "quota_ledger",
                    "niche_contract_digest",
                ],
                source_order=[
                    "channel_profile",
                    "policy_snapshot",
                    "editorial_slot",
                    "quota_ledger",
                ],
            ),
            correlation_id=f"runway-replenishment:{research_run.id}",
        )
        context = resolver.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan.id,
                freshness_state="UNKNOWN",
                confidence_level="UNKNOWN",
            ),
            correlation_id=f"runway-replenishment:{research_run.id}",
        )
        state = ChannelStatePackService(self.session).build_snapshot(
            data=ChannelStatePackSnapshotCreate(
                editorial_research_run_id=research_run.id,
                company_id=run.company_id,
                channel_workspace_id=run.channel_workspace_id,
                policy_snapshot_id=policy.policy_snapshot_id,
                context_pack_snapshot_id=context.id,
            ),
            correlation_id=f"runway-replenishment:{research_run.id}",
        )
        return context.id, state.id

    def _create_research_slot(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        run_date: date,
    ) -> tuple[EditorialCalendarSlot | None, str | None]:
        """Create a v2 research envelope from already-approved channel facts."""

        category = self.session.scalar(
            select(ContentCategory)
            .where(
                ContentCategory.company_id == run.company_id,
                ContentCategory.channel_workspace_id == run.channel_workspace_id,
                ContentCategory.status == "ACTIVE",
            )
            .order_by(ContentCategory.created_at, ContentCategory.id)
            .limit(1)
        )
        snapshot = self.session.get(CompiledChannelPolicySnapshot, policy.policy_snapshot_id)
        payload = snapshot.compiled_payload if snapshot is not None else {}
        payload = payload if isinstance(payload, dict) else {}
        channel_contract = payload.get("channel_contract_json") or {}
        channel_contract = (
            channel_contract if isinstance(channel_contract, dict) else {}
        )
        editorial = channel_contract.get("editorial_strategy") or {}
        editorial = editorial if isinstance(editorial, dict) else {}
        allowed_pillars = editorial.get("content_pillars") or []
        category_pillar = category.content_pillar if category is not None else None
        if (
            category is None
            or not category_pillar
            or category_pillar not in allowed_pillars
        ):
            return None, "RUNWAY_REPLENISHMENT_EDITORIAL_SLOT_AUTHORITY_UNAVAILABLE"
        initial_runway = payload.get("initial_content_runway") or []
        initial_item = initial_runway[0] if initial_runway else {}
        production_goal = (
            initial_item.get("title") if isinstance(initial_item, dict) else None
        )
        if not isinstance(production_goal, str) or not production_goal.strip():
            channel_identity = channel_contract.get("channel_identity") or {}
            channel_identity = (
                channel_identity if isinstance(channel_identity, dict) else {}
            )
            production_goal = channel_identity.get("niche")
        platform_strategy = channel_contract.get("platform_strategy") or {}
        platform_strategy = (
            platform_strategy if isinstance(platform_strategy, dict) else {}
        )
        primary_platform = platform_strategy.get("primary_platform")
        if not isinstance(production_goal, str) or not production_goal.strip():
            return None, "RUNWAY_REPLENISHMENT_EDITORIAL_SLOT_AUTHORITY_UNAVAILABLE"
        target_platforms = (
            [str(primary_platform).upper().replace(" ", "_")]
            if isinstance(primary_platform, str) and primary_platform.strip()
            else []
        )
        try:
            slot = EditorialCalendarService(self.session).create_slot(
                data=EditorialCalendarSlotCreate(
                    company_id=run.company_id,
                    channel_workspace_id=run.channel_workspace_id,
                    policy_snapshot_id=policy.policy_snapshot_id,
                    category_id=category.id,
                    slot_date=run_date,
                    slot_type="RESEARCH",
                    schema_version="v2",
                    production_lane="LONG_FORM",
                    assignment_mode="OPEN_MIX",
                    production_goal=production_goal,
                    target_platforms=target_platforms,
                    content_pillar=category_pillar,
                    risk_level="UNKNOWN",
                ),
                correlation_id=f"runway-replenishment:{run.id}",
            )
        except ValidationFailureError:
            return None, "RUNWAY_REPLENISHMENT_EDITORIAL_SLOT_AUTHORITY_UNAVAILABLE"
        return slot, None

    def _resolve_mode(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        editorial_calendar_slot: EditorialCalendarSlot | None,
    ) -> EditorialModeDecision:
        """Resolve the v2 content mode before applying mode-specific checks.

        The synthetic replenishment slot is a real, persisted ``OPEN_MIX``
        authority.  ``OPEN_MIX`` with no eligible active series resolves to
        ``STANDALONE`` through the same versioned resolver used by admission;
        it is never silently treated as a series episode.
        """

        if editorial_calendar_slot is None:
            return EditorialModeDecision(
                content_mode=None,
                assignment_mode=None,
                reason_codes=("EDITORIAL_CONTENT_MODE_UNRESOLVED",),
            )
        try:
            assignment_mode = AssignmentMode(editorial_calendar_slot.assignment_mode)
        except (TypeError, ValueError):
            return EditorialModeDecision(
                content_mode=None,
                assignment_mode=editorial_calendar_slot.assignment_mode,
                reason_codes=("EDITORIAL_CONTENT_MODE_UNRESOLVED",),
            )
        try:
            resolution = DeterministicAssignmentResolver().resolve(
                AssignmentResolverInput(
                    production_lane=ProductionLane.LONG_FORM,
                    assignment_mode=assignment_mode,
                    preferred_series_plan_id=(
                        editorial_calendar_slot.preferred_series_plan_id
                    ),
                    preferred_series_run_id=(
                        editorial_calendar_slot.preferred_series_run_id
                    ),
                    # Replenishment resolves *routing* before market evidence.
                    # Strict preflight is still the only market PASS authority.
                    candidates=self._series_assignment_candidates(run=run, policy=policy),
                    niche_gate_passed=True,
                    market_gate_passed=True,
                )
            )
        except AssignmentResolutionError as exc:
            return EditorialModeDecision(
                content_mode=None,
                assignment_mode=assignment_mode.value,
                reason_codes=tuple(exc.reason_codes),
            )
        if resolution.content_mode == ContentMode.STANDALONE:
            authority = self._standalone_authority(
                run=run,
                policy=policy,
                editorial_calendar_slot=editorial_calendar_slot,
            )
            if authority is None:
                return EditorialModeDecision(
                    content_mode=None,
                    assignment_mode=assignment_mode.value,
                    reason_codes=("STANDALONE_AUTHORITY_UNAVAILABLE",),
                    resolver_version=resolution.resolver_version,
                    resolver_input_hash=resolution.resolver_input_hash,
                )
            return EditorialModeDecision(
                content_mode=ContentMode.STANDALONE.value,
                assignment_mode=assignment_mode.value,
                reason_codes=tuple(str(item) for item in resolution.reason_codes),
                resolver_version=resolution.resolver_version,
                resolver_input_hash=resolution.resolver_input_hash,
                standalone_authority=authority,
            )
        if (
            resolution.series_plan_id is None
            or resolution.series_run_id is None
        ):
            return EditorialModeDecision(
                content_mode=None,
                assignment_mode=assignment_mode.value,
                reason_codes=("SERIES_EPISODE_BINDING_INVALID",),
                resolver_version=resolution.resolver_version,
                resolver_input_hash=resolution.resolver_input_hash,
            )
        plan = self.session.get(SeriesPlan, resolution.series_plan_id)
        series_run = self.session.get(SeriesRun, resolution.series_run_id)
        if plan is None or series_run is None:
            return EditorialModeDecision(
                content_mode=None,
                assignment_mode=assignment_mode.value,
                reason_codes=("SERIES_EPISODE_BINDING_INVALID",),
                resolver_version=resolution.resolver_version,
                resolver_input_hash=resolution.resolver_input_hash,
            )
        role_policy = plan.episode_role_policy if isinstance(plan.episode_role_policy, dict) else {}
        episode_role = str(
            role_policy.get("default_role")
            or role_policy.get("episode_role")
            or "SERIES_EPISODE"
        ).strip()
        episode_delta = str(
            role_policy.get("episode_delta")
            or f"advance {plan.display_name} with one bounded documented workflow"
        ).strip()
        learning_outcome = str(
            role_policy.get("learning_outcome") or plan.editorial_promise
        ).strip()
        if not episode_role or not episode_delta or not learning_outcome:
            return EditorialModeDecision(
                content_mode=None,
                assignment_mode=assignment_mode.value,
                reason_codes=("SERIES_EPISODE_BINDING_INVALID",),
                resolver_version=resolution.resolver_version,
                resolver_input_hash=resolution.resolver_input_hash,
            )
        return EditorialModeDecision(
            content_mode=ContentMode.SERIES_EPISODE.value,
            assignment_mode=assignment_mode.value,
            reason_codes=tuple(str(item) for item in resolution.reason_codes),
            resolver_version=resolution.resolver_version,
            resolver_input_hash=resolution.resolver_input_hash,
            series_binding={
                "series_plan_id": str(plan.id),
                "series_run_id": str(series_run.id),
                "series_display_name": plan.display_name,
                "episode_role": episode_role,
                "episode_delta": episode_delta,
                "learning_outcome": learning_outcome,
            },
        )

    def _series_assignment_candidates(
        self, *, run: LaunchRun, policy: FirstChannelLaunchPolicyVersion
    ) -> list[AssignmentCandidate]:
        """Build typed current-series candidates for the shared resolver."""

        approved_plan_ids = {
            str(item) for item in policy.approved_initial_series_plan_ids or []
        }
        rows = self.session.execute(
            select(SeriesRun, SeriesPlan)
            .join(SeriesPlan, SeriesPlan.id == SeriesRun.series_plan_id)
            .where(
                SeriesPlan.company_id == run.company_id,
                SeriesPlan.channel_workspace_id == run.channel_workspace_id,
                SeriesPlan.channel_profile_version_id == policy.channel_profile_version_id,
                SeriesPlan.policy_snapshot_id == policy.policy_snapshot_id,
            )
            .order_by(SeriesRun.id)
        ).all()
        now = self.now()
        candidates: list[AssignmentCandidate] = []
        for series_run, plan in rows:
            if str(plan.id) not in approved_plan_ids:
                continue
            role_policy = (
                plan.episode_role_policy
                if isinstance(plan.episode_role_policy, dict)
                else {}
            )
            schedule_eligible = (
                (series_run.schedule_window_start is None or now >= series_run.schedule_window_start)
                and (series_run.schedule_window_end is None or now <= series_run.schedule_window_end)
            )
            try:
                candidates.append(
                    AssignmentCandidate(
                        series_plan_id=plan.id,
                        series_run_id=series_run.id,
                        production_lane=ProductionLane.LONG_FORM,
                        plan_state=SeriesPlanState(plan.state),
                        run_state=SeriesRunState(series_run.state),
                        next_episode_number=series_run.next_episode_number,
                        capacity=series_run.capacity,
                        reserved_episode_count=series_run.reserved_episode_count,
                        priority=series_run.priority,
                        coherence_score=100,
                        schedule_eligible=schedule_eligible,
                        episode_role=str(
                            role_policy.get("default_role")
                            or role_policy.get("episode_role")
                            or "SERIES_EPISODE"
                        ),
                    )
                )
            except ValueError:
                continue
        return candidates

    def _standalone_authority(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        editorial_calendar_slot: EditorialCalendarSlot,
    ) -> dict[str, Any] | None:
        """Prove standalone authority from current immutable channel facts."""

        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, policy.policy_snapshot_id
        )
        category = self.session.get(ContentCategory, editorial_calendar_slot.category_id)
        if snapshot is None or category is None:
            return None
        payload = snapshot.compiled_payload or {}
        contract = payload.get("channel_contract_json") or {}
        contract = contract if isinstance(contract, dict) else {}
        editorial = contract.get("editorial_strategy") or {}
        editorial = editorial if isinstance(editorial, dict) else {}
        platform = contract.get("platform_strategy") or {}
        platform = platform if isinstance(platform, dict) else {}
        identity = contract.get("channel_identity") or {}
        identity = identity if isinstance(identity, dict) else {}
        allowed_pillars = editorial.get("content_pillars") or payload.get(
            "content_pillars"
        ) or []
        initial_runway = payload.get("initial_content_runway") or []
        long_form_runway = any(
            isinstance(item, dict)
            and str(item.get("format") or "").lower().replace("-", "_")
            == "long_form"
            for item in initial_runway
        )
        initial_series_ids = list(policy.approved_initial_series_plan_ids or [])
        if (
            policy.initial_series_count != 0
            or initial_series_ids
            or snapshot.channel_workspace_id != run.channel_workspace_id
            or snapshot.channel_profile_version_id != policy.channel_profile_version_id
            or snapshot.status not in {"approved", "active"}
            or category.company_id != run.company_id
            or category.channel_workspace_id != run.channel_workspace_id
            or category.status != "ACTIVE"
            or not category.content_pillar
            or category.content_pillar != editorial_calendar_slot.content_pillar
            or category.content_pillar not in allowed_pillars
            or not long_form_runway
            or str(platform.get("primary_platform") or "").lower() != "youtube"
            or not identity
            or "YOUTUBE" not in set(editorial_calendar_slot.target_platforms or [])
        ):
            return None
        return {
            "channel_profile_version_id": str(policy.channel_profile_version_id),
            "policy_snapshot_id": str(snapshot.id),
            "policy_snapshot_hash": snapshot.content_hash,
            "category_id": str(category.id),
            "content_pillar": category.content_pillar,
            "platform": "YOUTUBE",
            "launch_initial_series_count": policy.initial_series_count,
            "launch_approved_initial_series_plan_ids": initial_series_ids,
            "channel_constitution_present": bool(
                payload.get("channel_constitution") or identity
            ),
            "operating_blueprint_present": bool(
                payload.get("operating_blueprint") or platform
            ),
        }

    def _capability_blockers(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        mode_decision: EditorialModeDecision,
    ) -> tuple[list[str], dict[str, Any]]:
        workspace = self.session.get(ChannelWorkspace, run.channel_workspace_id)
        profile = self.session.get(ChannelProfileVersion, policy.channel_profile_version_id)
        snapshot = self.session.get(CompiledChannelPolicySnapshot, policy.policy_snapshot_id)
        category = self.session.scalar(
            select(ContentCategory)
            .where(
                ContentCategory.company_id == run.company_id,
                ContentCategory.channel_workspace_id == run.channel_workspace_id,
                ContentCategory.status == "ACTIVE",
            )
            .order_by(ContentCategory.created_at, ContentCategory.id)
            .limit(1)
        )
        budget = resolve_budget_authority(
            self.session,
            policy_snapshot_id=policy.policy_snapshot_id,
            channel_workspace_id=run.channel_workspace_id,
        )
        providers = resolve_provider_authority(
            self.session,
            policy_snapshot_id=policy.policy_snapshot_id,
            channel_workspace_id=run.channel_workspace_id,
        )
        blockers: list[str] = []
        if (
            workspace is None
            or workspace.company_id != run.company_id
            or profile is None
            or profile.channel_workspace_id != run.channel_workspace_id
            or profile.status not in {"approved", "active"}
            or snapshot is None
            or snapshot.channel_workspace_id != run.channel_workspace_id
            or snapshot.channel_profile_version_id != profile.id
            or snapshot.status not in {"approved", "active"}
        ):
            blockers.append("RUNWAY_REPLENISHMENT_CHANNEL_AUTHORITY_MISMATCH")
        if category is None:
            blockers.append("RUNWAY_REPLENISHMENT_CATEGORY_AUTHORITY_UNAVAILABLE")
        if mode_decision.content_mode is None:
            blockers.extend(mode_decision.reason_codes)
        elif mode_decision.content_mode == ContentMode.SERIES_EPISODE.value:
            # The resolver has selected typed plan/run intent.  Exact episode
            # allocation belongs to pre-writer reservation.
            # Retain an explicit invariant for this scheduler boundary.
            if "SERIES_EPISODE_BINDING_INVALID" in mode_decision.reason_codes:
                blockers.append("SERIES_EPISODE_BINDING_INVALID")
        elif mode_decision.content_mode != ContentMode.STANDALONE.value:
            blockers.append("EDITORIAL_CONTENT_MODE_UNRESOLVED")
        if budget.get("state") != "READY":
            blockers.append("RUNWAY_REPLENISHMENT_BUDGET_AUTHORITY_BLOCKED")
        if providers.get("state") != "READY":
            blockers.append("RUNWAY_REPLENISHMENT_PROVIDER_AUTHORITY_BLOCKED")
        return blockers, {
            "category_id": str(category.id) if category else None,
            "budget": budget,
            "providers": providers,
        }

    def _existing_equivalent(
        self,
        *,
        run: LaunchRun,
        policy: FirstChannelLaunchPolicyVersion,
        scope_key: str,
        attempt_key: str,
    ) -> EditorialResearchRun | None:
        candidates = self.session.scalars(
            select(EditorialResearchRun)
            .where(
                EditorialResearchRun.company_id == run.company_id,
                EditorialResearchRun.channel_workspace_id == run.channel_workspace_id,
                EditorialResearchRun.channel_profile_version_id == policy.channel_profile_version_id,
                EditorialResearchRun.policy_snapshot_id == policy.policy_snapshot_id,
                EditorialResearchRun.trigger_type == "SCHEDULED",
            )
            .order_by(EditorialResearchRun.created_at.desc())
        ).all()
        for candidate in candidates:
            metadata = (candidate.metadata_ or {}).get(_METADATA_KEY) or {}
            if metadata.get("schema_version") != RUNWAY_REPLENISHMENT_SCHEMA:
                continue
            if metadata.get("scope_key") != scope_key:
                continue
            if (
                candidate.status in _ACTIVE_STATUSES
                or metadata.get("attempt_key") == attempt_key
            ):
                return candidate
        return None

    def _run_date(self, *, policy: FirstChannelLaunchPolicyVersion) -> date:
        try:
            return self.now().astimezone(ZoneInfo(policy.timezone)).date()
        except ZoneInfoNotFoundError as exc:
            raise ValidationFailureError("RUNWAY_REPLENISHMENT_TIMEZONE_INVALID") from exc
