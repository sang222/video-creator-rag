from __future__ import annotations

from types import SimpleNamespace

from app.contracts.script_qualification import (
    AssignmentObservation,
    MaterialClaimObservation,
    QualifiedScriptOutput,
    ScriptSpan,
    SectionPurposeObservation,
    SemanticVerificationOutput,
)
from app.db.models.m5 import EditorialIdeaCandidate
from app.db.models.script_qualification import ScriptQualificationRun
from app.services.script_qualification import (
    ScriptQualificationService,
    TopicDefinitionService,
    canonical_hash,
    script_hash,
    span_hash,
)
from tests.qualification.conftest import QualificationFactory


def _span(script: str, section_id: str, text: str) -> ScriptSpan:
    start = script.encode("utf-8").find(text.encode("utf-8"))
    assert start >= 0
    return ScriptSpan(
        text=text,
        start_byte=start,
        end_byte=start + len(text.encode("utf-8")),
        span_hash=span_hash(text),
        section_id=section_id,
    )


def test_historical_greenlit_without_current_topic_receipt_is_ineligible(db_session):
    flow = QualificationFactory(db_session).m5_admitted_project()
    candidate = flow.candidate
    original = (candidate.stage, candidate.proposed_title, candidate.proposed_angle)

    eligibility = TopicDefinitionService(db_session).current_eligibility(candidate)

    assert not eligibility.eligible
    assert eligibility.primary_reason_code == "EDITORIAL_SUBJECT_NOT_IDENTIFIED"
    assert (candidate.stage, candidate.proposed_title, candidate.proposed_angle) == original


def test_topic_gate_blocks_boilerplate_and_passes_bound_specific_definition(db_session):
    flow = QualificationFactory(db_session).m5_admitted_project()
    candidate = flow.candidate
    candidate.proposed_title = "GPT-5.4 Pro Model | OpenAI API"
    candidate.proposed_angle = "A source-grounded standalone explanation constrained to the fetched official documentation."
    service = TopicDefinitionService(db_session)
    fields = {
        "subject_type": "OFFICIAL_DOCUMENTED_PRODUCT_OR_FEATURE",
        "subject_name": "GPT-5.4 Pro Model",
        "subject_canonical_id": "official-document:gpt-5-4-pro",
        "subject_evidence_refs": [{"id": "evidence-1", "content_hash": "a" * 64}],
        "subject_evidence_spans": [{"text": "GPT-5.4 Pro Model", "span_hash": span_hash("GPT-5.4 Pro Model")}],
        "target_audience": "small professional teams",
        "audience_problem": "need to judge documented scope before making a tool decision",
        "content_pillar": "AI automation workflows",
        "production_goal": candidate.proposed_title,
        "scope_inclusions": ["the exact official model page"],
        "exclusions": ["undocumented capabilities and ROI claims"],
        "central_question_or_thesis": "What does the official model page establish and what remains out of scope?",
        "learning_outcome": "Viewers can separate a documented capability from an unsupported inference.",
        "viewer_value": "A source-bounded way to decide what to verify next.",
        "content_mode": "STANDALONE",
        "channel_contract_ref": {"policy_snapshot_id": str(candidate.policy_snapshot_id)},
        "source_classification_refs": [{"source_classification": "TOPIC_CAPABLE"}],
        "standalone_self_containment_required": True,
    }
    blocked = service.evaluate(service.create(candidate=candidate, fields=fields))
    assert blocked.state == "BLOCK"
    assert "EDITORIAL_ANGLE_BOILERPLATE" in blocked.reason_codes

    candidate.proposed_angle = "A bounded walkthrough of the documented GPT-5.4 Pro model page and the decision limits it leaves intact."
    fields["viewer_value"] = "A source-bounded decision frame for the specific documented model page."
    passed = service.evaluate(service.create(candidate=candidate, fields=fields))
    assert passed.state == "PASS"
    assert passed.current_production_eligibility is True


def _qualified_inputs():
    sections = [
        {"section_id": "hook", "heading": "Hook", "narration": "The official page names the model. It defines the evidence boundary."},
        {"section_id": "body", "heading": "Body", "narration": "Small teams need scope before a decision. The documented detail answers the central question."},
        {"section_id": "close", "heading": "Close", "narration": "The learning outcome is evidence-first evaluation. The viewer can verify the next decision."},
    ]
    script = " ".join(section["narration"] for section in sections)
    sentences = [
        "The official page names the model.", "It defines the evidence boundary.",
        "Small teams need scope before a decision.", "The documented detail answers the central question.",
        "The learning outcome is evidence-first evaluation.", "The viewer can verify the next decision.",
    ]
    section_by_sentence = ["hook", "hook", "body", "body", "close", "close"]
    spans = [_span(script, section_id, sentence) for sentence, section_id in zip(sentences, section_by_sentence, strict=True)]
    claims = [
        {"claim_id": f"writer-{index}", "claim_text": sentence, "evidence_span_ids": ["evidence-1"]}
        for index, sentence in enumerate(sentences, start=1)
    ]
    draft = QualifiedScriptOutput(canonical_script=script, language="en", sections=sections, claims=claims)
    requirements = ["subject", "question", "audience", "outcome", "viewer-value", "self-containment"]
    observations = [
        MaterialClaimObservation(
            observed_claim_id=f"observed-{index}", span=span,
            claim_type="FACTUAL_ASSERTION", materiality_state="MATERIAL",
            writer_declared_claim_id=f"writer-{index}", factual_evidence_span_ids=["evidence-1"],
            semantic_relation="ENTAILED", assignment_requirement_ids=[requirements[index - 1]],
        )
        for index, span in enumerate(spans, start=1)
    ]
    fulfillment = [
        AssignmentObservation(requirement_id=requirement, status="SUFFICIENT", spans=[span])
        for requirement, span in zip(requirements, spans, strict=True)
    ]
    verifier = SemanticVerificationOutput(
        material_claim_inventory=observations,
        assignment_fulfillment_observations=fulfillment,
        section_purpose_observations=[
            SectionPurposeObservation(section_id="hook", observed_primary_role="HOOK", fulfilled_requirement_ids=["subject"], editorial_delta="Establishes the bounded subject.", genericity_state="SPECIFIC"),
            SectionPurposeObservation(section_id="body", observed_primary_role="MECHANISM", fulfilled_requirement_ids=["question", "audience"], editorial_delta="Explains why the decision needs scope.", genericity_state="SPECIFIC"),
            SectionPurposeObservation(section_id="close", observed_primary_role="CLOSING_INSIGHT", fulfilled_requirement_ids=["outcome", "viewer-value", "self-containment"], editorial_delta="Converts evidence into the promised action.", genericity_state="SPECIFIC"),
        ],
    )
    assignment = {"required_requirement_units": [{"requirement_id": item, "required": True} for item in requirements]}
    assignment["assignment_hash"] = canonical_hash(assignment)
    evidence = {"spans": [{"evidence_span_id": "evidence-1", "evidence_id": "00000000-0000-0000-0000-000000000001", "text": "Official source evidence supports the bounded model page.", "source_snapshot_hash": "a" * 64}]}
    evidence["evidence_pack_hash"] = canonical_hash(evidence)
    memory = {"status": "EMPTY_SAFE_DIGEST"}
    memory["digest_hash"] = canonical_hash(memory)
    run = ScriptQualificationRun(
        script_assignment=assignment, script_assignment_hash=assignment["assignment_hash"],
        factual_evidence_pack=evidence, factual_evidence_pack_hash=evidence["evidence_pack_hash"],
        memory_digest=memory, memory_digest_hash=memory["digest_hash"],
    )
    return run, draft, verifier


def test_independent_inventory_and_entailment_control_all_script_gates(db_session):
    run, draft, verifier = _qualified_inputs()
    service = ScriptQualificationService(db_session)
    structural = service._structural_receipt(run, draft)
    receipts = service._semantic_receipts(run, draft, verifier, structural)

    assert receipts["structural"]["status"] == "PASS"
    assert receipts["inventory"]["status"] == "PASS"
    assert receipts["grounding"]["status"] == "PASS"
    assert receipts["fulfillment"]["status"] == "PASS"
    assert receipts["memory"]["status"] == "PASS_EMPTY"
    assert receipts["fulfillment"]["research_coverage_ratio"] == 1.0
    assert receipts["grounding"]["script_hash"] == script_hash(draft.canonical_script)


def test_undeclared_or_partially_supported_material_claim_blocks(db_session):
    run, draft, verifier = _qualified_inputs()
    service = ScriptQualificationService(db_session)
    structural = service._structural_receipt(run, draft)

    verifier.material_claim_inventory[0] = verifier.material_claim_inventory[0].model_copy(
        update={"writer_declared_claim_id": None, "semantic_relation": "PARTIALLY_SUPPORTED"}
    )
    receipts = service._semantic_receipts(run, draft, verifier, structural)

    assert receipts["inventory"]["status"] == "BLOCK"
    assert "SCRIPT_MATERIAL_CLAIM_UNDECLARED" in receipts["inventory"]["reason_codes"]
    assert receipts["grounding"]["status"] == "BLOCK"
    assert "SCRIPT_CLAIM_PARTIALLY_SUPPORTED" in receipts["grounding"]["reason_codes"]
