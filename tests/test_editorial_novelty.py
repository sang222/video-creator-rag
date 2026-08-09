from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.contracts.m5 import EditorialIdeaCandidateTransition, SearchDemandEvidenceCreate
from app.core.actor import _system_worker_actor
from app.db.models.m5 import EditorialIdeaCandidate, IdeaMarketPreflight
from app.services.config_registry import content_hash
from app.services.editorial_novelty import (
    EDITORIAL_NOVELTY_GATE_VERSION,
    EditorialDuplicateCleanupService,
    EditorialNoveltyService,
    EditorialTerritoryCompiler,
    NoveltyEvaluation,
)
from app.services.editorial_research import EditorialResearchService
from app.services.editorial_specificity import (
    EDITORIAL_IDEA_SYNTHESIS_VERSION,
    EditorialIdeaProposal,
    EditorialSpecificityMaintenanceService,
    EditorialSpecificityService,
)
from app.services.editorial_runway_replenishment import (
    EditorialModeDecision,
    EditorialRunwayReplenishmentService,
    _canonical_hash,
    _scheduled_scope_key,
)
from app.services.m5 import IDEA_MARKET_PREFLIGHT_VERSION, SearchDemandEvidenceService
from app.services.script_qualification import (
    TOPIC_GATE_VERSION,
    TopicDefinitionService,
    classify_source_specificity,
    span_hash,
)
from tests.qualification.conftest import QualificationFactory


def _compiler_candidate() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        channel_workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        policy_snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000022"),
        editorial_research_run_id=uuid.uuid4(),
        proposed_title="GPT-5.4 Pro reasoning workflow",
        proposed_angle="A bounded decision workflow.",
    )


def _compiler_topic(
    *,
    mode: str = "STANDALONE",
    question: str = "What documented reasoning workflow should a small team verify?",
    episode_delta: str = "Explain the first bounded workflow step.",
) -> SimpleNamespace:
    return SimpleNamespace(
        content_mode=mode,
        subject_type="OFFICIAL_DOCUMENTED_PRODUCT_OR_FEATURE",
        subject_name="GPT-5.4 Pro",
        subject_canonical_id="official-document:gpt-5-4-pro",
        central_question_or_thesis=question,
        learning_outcome="Viewers can separate documented workflows from unsupported claims.",
        production_goal="Help teams select a safe reasoning workflow.",
        content_pillar="AI automation workflows",
        target_audience="small professional teams",
        audience_problem="need an evidence-first model decision",
        series_binding=(
            {
                "series_plan_id": "00000000-0000-0000-0000-000000000033",
                "series_run_id": "00000000-0000-0000-0000-000000000044",
                "episode_role": "WORKFLOW_DEEP_DIVE",
                "episode_delta": episode_delta,
            }
            if mode == "SERIES_EPISODE"
            else None
        ),
    )


def test_editorial_territory_compiler_is_stable_and_distinguishes_semantics():
    compiler = EditorialTerritoryCompiler()
    candidate = _compiler_candidate()
    topic = _compiler_topic()

    first = compiler.compile(candidate=candidate, topic=topic)
    candidate.editorial_research_run_id = uuid.uuid4()
    same = compiler.compile(candidate=candidate, topic=copy.deepcopy(topic))
    assert first.key == same.key
    assert first.key == compiler.compile(candidate=candidate, topic=topic).key

    assert first.key != compiler.compile(
        candidate=candidate,
        topic=_compiler_topic(question="How should a team assess GPT-5.4 Pro cost tradeoffs?"),
    ).key
    assert first.key != compiler.compile(
        candidate=candidate, topic=_compiler_topic(mode="SERIES_EPISODE")
    ).key
    assert compiler.compile(
        candidate=candidate,
        topic=_compiler_topic(mode="SERIES_EPISODE"),
    ).key != compiler.compile(
        candidate=candidate,
        topic=_compiler_topic(
            mode="SERIES_EPISODE",
            episode_delta="Compare the next documented workflow boundary.",
        ),
    ).key


def _candidate_copy(source: EditorialIdeaCandidate, *, suffix: str) -> EditorialIdeaCandidate:
    return EditorialIdeaCandidate(
        editorial_research_run_id=source.editorial_research_run_id,
        company_id=source.company_id,
        channel_workspace_id=source.channel_workspace_id,
        policy_snapshot_id=source.policy_snapshot_id,
        stage="PREFLIGHT_PASS",
        script_contract_version=source.script_contract_version,
        topic_repair_depth=0,
        proposed_title=f"{source.proposed_title} {suffix}",
        proposed_angle=(
            "Show where a human approval checkpoint belongs before an "
            f"automation commits a change ({suffix})."
        ),
        proposed_format=source.proposed_format,
        proposed_pillar=source.proposed_pillar,
        rationale=copy.deepcopy(source.rationale),
        evidence_refs=copy.deepcopy(source.evidence_refs),
        reason_codes=["NOVELTY_TEST"],
        confidence_level=source.confidence_level,
        budget_readiness="UNKNOWN",
        rights_policy_state="PASS",
        quality_state="PASS",
        canonical_hash=content_hash({"novelty-test-candidate": suffix, "id": str(uuid.uuid4())}),
    )


def _flow_with_current_topic(session):
    """Build a pre-topic-gate legacy fixture, then attach current authority."""

    flow = QualificationFactory(session).m5_admitted_project(mock_mode="blocked")
    candidate = flow.candidate
    candidate.stage = "GREENLIT"
    subject = candidate.proposed_title
    evidence_id = str(candidate.evidence_refs[0]["id"])
    topic = TopicDefinitionService(session).create(
        candidate=candidate,
        fields={
            "subject_type": "OFFICIAL_DOCUMENTED_PRODUCT_OR_FEATURE",
            "subject_name": subject,
            "subject_canonical_id": "official-document:fixture-vcos-workflow",
            "subject_evidence_refs": [{"id": evidence_id}],
            "subject_evidence_spans": [
                {
                    "evidence_id": evidence_id,
                    "text": subject,
                    "span_hash": span_hash(subject),
                }
            ],
            "target_audience": "small professional teams",
            "audience_problem": "need an evidence-first automation audit",
            "content_pillar": "AI automation workflows",
            "production_goal": subject,
            "scope_inclusions": ["one documented audit workflow"],
            "exclusions": ["unsupported performance claims"],
            "central_question_or_thesis": (
                "What does a small team need to verify before relying on one "
                "automation workflow?"
            ),
            "learning_outcome": (
                "Viewers can separate documented workflow boundaries from "
                "unsupported assumptions."
            ),
            "viewer_value": "A bounded decision frame for an automation audit.",
            "content_mode": "STANDALONE",
            "channel_contract_ref": {"policy_snapshot_id": str(flow.snapshot.id)},
            "source_classification_refs": [{"source_classification": "TOPIC_CAPABLE"}],
            "standalone_self_containment_required": True,
        },
    )
    assert TopicDefinitionService(session).evaluate(topic).state == "PASS"
    _attach_specific_proposal(session, candidate)
    return flow


def _attach_specific_proposal(session, candidate: EditorialIdeaCandidate) -> None:
    """Give legacy novelty fixtures the new, current proposal authority."""

    topic = TopicDefinitionService(session).current_eligibility(candidate).definition
    assert topic is not None
    quote = "Use an approval checkpoint before automation commits a change."
    evidence = SearchDemandEvidenceService(session).create_evidence(
        data=SearchDemandEvidenceCreate(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            evidence_source_type="OFFICIAL_DOCUMENT",
            authority_purpose="CLAIM_SOURCE",
            source_ref="https://docs.example.test/automation/approval-workflows",
            query="approval checkpoint automation workflow",
            platform="GOOGLE",
            geo="US",
            language="en-US",
            evidence_confidence="HIGH",
            metadata={
                "editorial_fresh_evidence": {
                    "source_snapshot": {
                        "canonical_url": "https://docs.example.test/automation/approval-workflows",
                        "title": "Approval workflow implementation reference",
                        "content_excerpt": (
                            f"{quote} The documented workflow keeps accountable human review "
                            "before an external side effect."
                        ),
                        "content_hash": content_hash({"fixture": "approval-workflow"}),
                    }
                }
            },
        ),
        correlation_id="test-editorial-specificity",
    )
    evidence_id = str(evidence.id)
    candidate.evidence_refs = [
        {
            "type": "search_demand_evidence",
            "id": evidence_id,
            "ref": evidence.source_ref,
        }
    ]
    candidate.proposed_angle = (
        "Show where a human approval checkpoint belongs before an automation "
        "commits a documented external change."
    )
    proposal = EditorialIdeaProposal.model_validate(
        {
            "proposed_title": candidate.proposed_title,
            "proposed_angle": candidate.proposed_angle,
            "specific_audience_problem": topic.audience_problem,
            "central_question_or_thesis": topic.central_question_or_thesis,
            "learning_outcome": topic.learning_outcome,
            "viewer_value": topic.viewer_value,
            "editorial_delta": (
                "Explains the documented approval checkpoint that separates a "
                "reviewable automation from an unreviewed external change."
            ),
            "specific_mechanism_or_use_case": (
                "Place a human approval checkpoint before an automation commits "
                "an external change."
            ),
            "decision_value": (
                "Teams can decide which workflow action must retain human approval."
            ),
            "scope_inclusions": list(topic.scope_inclusions),
            "scope_exclusions": list(topic.exclusions),
            "primary_evidence_refs": [{"id": evidence_id, "ref": evidence.source_ref}],
            "supporting_evidence_refs": [],
            "evidence_bindings": [
                {"field": field, "evidence_id": evidence_id, "quoted_text": quote}
                for field in (
                    "proposed_title", "proposed_angle", "central_question_or_thesis",
                    "learning_outcome", "viewer_value", "editorial_delta",
                    "specific_mechanism_or_use_case", "decision_value",
                )
            ],
            "source_specificity_class": classify_source_specificity(evidence),
            "content_mode": topic.content_mode,
            "series_binding": topic.series_binding,
        }
    )
    candidate.editorial_idea_proposal = proposal.model_dump(mode="json")
    evaluation = EditorialSpecificityService(session).evaluate(
        candidate=candidate, topic=topic
    )
    assert evaluation.state == "PASS", evaluation.reason_codes
    EditorialSpecificityService(session).persist(
        candidate=candidate, evaluation=evaluation
    )


def _copy_topic(
    session,
    *,
    source: EditorialIdeaCandidate,
    candidate: EditorialIdeaCandidate,
    question: str | None = None,
) -> None:
    source_topic = TopicDefinitionService(session).current_eligibility(source).definition
    assert source_topic is not None
    candidate.proposed_title = (
        f"{source_topic.subject_name} Approval Checkpoint {candidate.canonical_hash[:8]}"
    )
    fields = {
        name: copy.deepcopy(getattr(source_topic, name))
        for name in (
            "subject_type",
            "subject_name",
            "subject_canonical_id",
            "subject_evidence_refs",
            "subject_evidence_spans",
            "target_audience",
            "audience_problem",
            "content_pillar",
            "production_goal",
            "scope_inclusions",
            "exclusions",
            "central_question_or_thesis",
            "learning_outcome",
            "viewer_value",
            "content_mode",
            "channel_contract_ref",
            "source_classification_refs",
            "series_binding",
            "standalone_self_containment_required",
        )
    }
    if question is not None:
        fields["central_question_or_thesis"] = question
        fields["learning_outcome"] = "Viewers can make a different documented decision."
    topic = TopicDefinitionService(session).create(candidate=candidate, fields=fields)
    assert TopicDefinitionService(session).evaluate(topic).state == "PASS"
    _attach_specific_proposal(session, candidate)


def _copy_preflight(session, source: IdeaMarketPreflight, candidate: EditorialIdeaCandidate):
    values = {
        column.name: copy.deepcopy(getattr(source, column.name))
        for column in source.__table__.columns
        if column.name not in {"id", "created_at", "editorial_idea_candidate_id"}
    }
    evidence_blob = dict(values.get("evidence_blob") or {})
    evidence_blob["claim_evidence_refs"] = copy.deepcopy(candidate.evidence_refs)
    values["evidence_blob"] = evidence_blob
    preflight = IdeaMarketPreflight(
        **values, editorial_idea_candidate_id=candidate.id
    )
    session.add(preflight)
    session.flush()
    return preflight


def _persist_pass(session, candidate: EditorialIdeaCandidate) -> str:
    novelty = EditorialNoveltyService(session)
    topic = TopicDefinitionService(session).current_eligibility(candidate).definition
    assert topic is not None
    specificity = EditorialSpecificityService(session)
    evaluation = specificity.evaluate(candidate=candidate, topic=topic)
    assert evaluation.state == "PASS", evaluation.reason_codes
    specificity.persist(candidate=candidate, evaluation=evaluation)
    territory = novelty.compiler.compile(candidate=candidate, topic=topic)
    novelty.persist(
        candidate,
        NoveltyEvaluation(
            candidate_id=candidate.id,
            territory_key=territory.key,
            state="PASS",
            matched_candidate_ids=(),
            matched_published_refs=(),
            reason_codes=("EDITORIAL_NOVELTY_PASS",),
            gate_version=EDITORIAL_NOVELTY_GATE_VERSION,
            evaluation_hash=content_hash({"candidate": str(candidate.id), "key": territory.key}),
        ),
    )
    return territory.key


def test_novelty_gate_legally_rejects_an_equivalent_greenlight(db_session):
    flow = _flow_with_current_topic(db_session)
    duplicate = _candidate_copy(flow.candidate, suffix="Duplicate")
    db_session.add(duplicate)
    db_session.flush()
    _copy_topic(db_session, source=flow.candidate, candidate=duplicate)
    preflight = _copy_preflight(db_session, flow.preflight, duplicate)

    result = EditorialResearchService(db_session).transition_candidate(
        candidate_id=duplicate.id,
        data=EditorialIdeaCandidateTransition(
            target_stage="GREENLIT",
            idea_market_preflight_id=preflight.id,
            reason_codes=["TEST_GREENLIGHT"],
        ),
        actor=flow.actor,
    )

    assert result.stage == "REJECTED"
    assert "EDITORIAL_TERRITORY_DUPLICATE" in result.reason_codes
    assert result.editorial_novelty_receipt["state"] == "BLOCK"


@pytest.mark.parametrize(
    ("occupied_stage", "expected_state"),
    [
        ("GREENLIT", "BLOCK"),
        ("SELECTED_FOR_SLOT", "BLOCK"),
        ("IN_PRODUCTION", "BLOCK"),
        ("REJECTED", "PASS"),
    ],
)
def test_novelty_uses_active_stage_not_same_subject_forever(
    db_session, occupied_stage, expected_state
):
    flow = _flow_with_current_topic(db_session)
    flow.candidate.stage = occupied_stage
    different_question = _candidate_copy(flow.candidate, suffix="Different Decision")
    db_session.add(different_question)
    db_session.flush()
    _copy_topic(
        db_session,
        source=flow.candidate,
        candidate=different_question,
        question="Which documented operating boundary should a team test next?",
    )

    topic = TopicDefinitionService(db_session).current_eligibility(different_question).definition
    assert topic is not None
    assert EditorialNoveltyService(db_session).evaluate(
        candidate=different_question, topic=topic
    ).state == "PASS"

    same_question = _candidate_copy(flow.candidate, suffix="Same Territory")
    db_session.add(same_question)
    db_session.flush()
    _copy_topic(db_session, source=flow.candidate, candidate=same_question)
    same_topic = TopicDefinitionService(db_session).current_eligibility(same_question).definition
    assert same_topic is not None
    assert EditorialNoveltyService(db_session).evaluate(
        candidate=same_question, topic=same_topic
    ).state == expected_state


def test_runway_counts_distinct_territories_and_cleanup_is_idempotent(db_session):
    flow = _flow_with_current_topic(db_session)
    flow.candidate.stage = "GREENLIT"
    _persist_pass(db_session, flow.candidate)
    candidates = [flow.candidate]
    questions = [
        None,
        None,
        None,
        "Which documented workflow boundary should a team test first?",
        "Which documented workflow boundary should a team test first?",
        "How should a team verify a second documented operating path?",
        "How should a team verify a second documented operating path?",
    ]
    for index, question in enumerate(questions[1:], start=2):
        candidate = _candidate_copy(flow.candidate, suffix=f"Runway {index}")
        candidate.stage = "GREENLIT"
        db_session.add(candidate)
        db_session.flush()
        _copy_topic(
            db_session,
            source=flow.candidate,
            candidate=candidate,
            question=question,
        )
        _copy_preflight(db_session, flow.preflight, candidate)
        _persist_pass(db_session, candidate)
        candidates.append(candidate)

    novelty = EditorialNoveltyService(db_session)
    counts = novelty.runway_counts(
        channel_workspace_id=flow.channel.id,
        policy_snapshot_id=flow.snapshot.id,
    )
    assert counts.raw_greenlit_rows == 7
    assert counts.current_eligible_greenlit_rows == 7
    assert counts.distinct_eligible_territory_count == 3

    cleanup = EditorialDuplicateCleanupService(db_session)
    plan = cleanup.plan(
        channel_workspace_id=flow.channel.id,
        policy_snapshot_id=flow.snapshot.id,
    )
    assert len(plan) == 3
    cleanup.apply(
        clusters=plan,
        actor=_system_worker_actor("vcos-durable-worker", permissions={"editorial.manage"}),
    )
    active = list(
        db_session.scalars(
            select(EditorialIdeaCandidate).where(
                EditorialIdeaCandidate.channel_workspace_id == flow.channel.id,
                EditorialIdeaCandidate.stage == "GREENLIT",
            )
        ).all()
    )
    assert len(active) == 3
    assert not cleanup.plan(
        channel_workspace_id=flow.channel.id,
        policy_snapshot_id=flow.snapshot.id,
    )


def test_cleanup_hard_deletes_only_a_disposable_missing_authority_candidate(db_session):
    flow = _flow_with_current_topic(db_session)
    disposable = _candidate_copy(flow.candidate, suffix="Disposable")
    disposable.stage = "GREENLIT"
    db_session.add(disposable)
    db_session.flush()

    cleanup = EditorialDuplicateCleanupService(db_session)
    plan = cleanup.plan(
        channel_workspace_id=flow.channel.id,
        policy_snapshot_id=flow.snapshot.id,
    )
    action = next(
        item.actions[0] for item in plan if item.candidate_ids == (disposable.id,)
    )
    assert action.action == "HARD_DELETE"
    cleanup.apply(
        clusters=plan,
        actor=_system_worker_actor("vcos-durable-worker", permissions={"editorial.manage"}),
    )
    assert db_session.get(EditorialIdeaCandidate, disposable.id) is None


def test_specificity_gate_blocks_source_label_and_reusable_documentation_review(db_session):
    flow = _flow_with_current_topic(db_session)
    candidate = flow.candidate
    topic = TopicDefinitionService(db_session).current_eligibility(candidate).definition
    assert topic is not None
    proposal = copy.deepcopy(candidate.editorial_idea_proposal)
    proposal.update(
        {
            "proposed_title": "Models | OpenAI API",
            "proposed_angle": (
                "A bounded standalone walkthrough of what Models | OpenAI API "
                "documents, what remains outside the source scope, and what to verify next."
            ),
            "central_question_or_thesis": (
                "What does the official documentation establish and what should users verify?"
            ),
            "learning_outcome": "Viewers can distinguish documented scope from unsupported assumptions.",
            "viewer_value": "A bounded evidence-first decision frame instead of a broad product overview.",
            "editorial_delta": "documentation review",
            "specific_mechanism_or_use_case": "documentation review",
            "decision_value": "what to verify",
        }
    )
    proposal.pop("proposal_hash", None)
    candidate.proposed_title = proposal["proposed_title"]
    candidate.proposed_angle = proposal["proposed_angle"]
    candidate.editorial_idea_proposal = EditorialIdeaProposal.model_validate(
        proposal
    ).model_dump(mode="json")

    evaluation = EditorialSpecificityService(db_session).evaluate(
        candidate=candidate, topic=topic
    )

    assert evaluation.state == "BLOCK"
    assert "EDITORIAL_ANGLE_GENERIC_WALKTHROUGH" in evaluation.reason_codes
    assert "EDITORIAL_QUESTION_GENERIC_DOCUMENTATION_REVIEW" in evaluation.reason_codes
    assert "EDITORIAL_LEARNING_OUTCOME_GENERIC" in evaluation.reason_codes
    assert "EDITORIAL_MECHANISM_OR_USE_CASE_MISSING" in evaluation.reason_codes


def test_retroactive_specificity_cleanup_rejects_generic_greenlit_without_deleting_lineage(db_session):
    flow = _flow_with_current_topic(db_session)
    candidate = flow.candidate
    candidate.stage = "GREENLIT"
    proposal = copy.deepcopy(candidate.editorial_idea_proposal)
    proposal["proposed_angle"] = "A bounded standalone walkthrough of what the source documents."
    proposal.pop("proposal_hash", None)
    candidate.proposed_angle = proposal["proposed_angle"]
    candidate.editorial_idea_proposal = EditorialIdeaProposal.model_validate(
        proposal
    ).model_dump(mode="json")
    service = EditorialSpecificityMaintenanceService(db_session)
    plan = service.plan(
        channel_workspace_id=flow.channel.id,
        policy_snapshot_id=flow.snapshot.id,
    )
    action = next(item for item in plan if item.candidate_id == candidate.id)
    assert action.action == "REJECT"
    assert action.specificity is not None and action.specificity.state == "BLOCK"

    service.apply(actions=plan, actor=flow.actor)

    assert candidate.stage == "REJECTED"
    assert "EDITORIAL_SPECIFICITY_RETROACTIVE_BLOCK" in candidate.reason_codes
    assert TopicDefinitionService(db_session).current_eligibility(candidate).definition is not None


def test_discovery_prompt_carries_compact_exclusion_authority():
    question = EditorialRunwayReplenishmentService._research_question(
        research_slot=SimpleNamespace(
            content_pillar="AI automation workflows",
            production_goal="choose an evidence-first workflow",
        ),
        mode_decision=EditorialModeDecision(
            content_mode="STANDALONE",
            assignment_mode="OPEN_MIX",
            reason_codes=(),
        ),
        exclusion_authority={
            "excluded_canonical_source_urls": ["https://platform.openai.com/docs/gpt-5-4"],
            "excluded_editorial_questions": ["What does GPT-5.4 document?"],
        },
    )
    assert "https://platform.openai.com/docs/gpt-5-4" in question
    assert "materially different editorial question" in question


def test_corrected_topic_authority_changes_same_day_scheduled_scope_only_by_version():
    common = {
        "launch_run_id": "launch-1",
        "launch_policy_version_id": "policy-version-1",
        "policy_snapshot_id": "snapshot-1",
        "policy_hash": "policy-hash-1",
        "editorial_idea_synthesis_version": "editorial-idea-synthesis.v1",
        "editorial_specificity_gate_version": "editorial-specificity-gate.v1",
        "editorial_territory_version": "vcos.editorial-territory.v2",
        "editorial_research_territory_version": "editorial-research-territory.v1",
        "editorial_evidence_discovery_version": "editorial-evidence-discovery.v3",
        "editorial_evidence_provider_key": "openai",
        "editorial_evidence_provider_config_hash": "provider-config-1",
        "editorial_evidence_provider_state": "EXISTING_SOURCE_PROVIDER_READY",
        "idea_market_preflight_version": "vcos.idea-market-preflight.v3",
    }
    legacy_scope = _scheduled_scope_key(
        **common,
        topic_gate_version="editorial-topic-definition-gate.v1",
    )
    corrected_scope = _scheduled_scope_key(
        **common,
        topic_gate_version=TOPIC_GATE_VERSION,
    )
    corrected_preflight_scope = _scheduled_scope_key(
        **{**common, "idea_market_preflight_version": IDEA_MARKET_PREFLIGHT_VERSION},
        topic_gate_version="editorial-topic-definition-gate.v1",
    )
    corrected_synthesis_scope = _scheduled_scope_key(
        **{
            **common,
            "editorial_idea_synthesis_version": EDITORIAL_IDEA_SYNTHESIS_VERSION,
        },
        topic_gate_version="editorial-topic-definition-gate.v1",
    )
    run_date = "2026-08-09"

    assert TOPIC_GATE_VERSION == "editorial-topic-definition-gate.v2"
    assert legacy_scope != corrected_scope
    assert IDEA_MARKET_PREFLIGHT_VERSION == "vcos.idea-market-preflight.v4"
    assert legacy_scope != corrected_preflight_scope
    assert EDITORIAL_IDEA_SYNTHESIS_VERSION == "editorial-idea-synthesis.v4"
    assert legacy_scope != corrected_synthesis_scope
    assert _canonical_hash({"scope_key": legacy_scope, "run_date": run_date}) != _canonical_hash(
        {"scope_key": corrected_scope, "run_date": run_date}
    )
