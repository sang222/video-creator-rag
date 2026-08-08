from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.contracts.script_qualification import (
    AssignmentObservation,
    ForbiddenScopeObservation,
    MaterialClaimObservation,
    QualifiedScriptOutput,
    QualifiedScriptOutputV2,
    SectionPurposeObservation,
    SemanticVerificationOutput,
    VerifierScriptSpan,
)
from app.db.models.script_qualification import (
    ScriptQualificationReceipt,
    ScriptQualificationRun,
)
from app.db.models.foundation import DomainEvent
from app.db.models.ops import DeadLetterJob
from app.services.outbox_dispatcher import DurableOutboxDispatcher
from app.services.script_qualification import (
    ScriptQualificationService,
    TopicDefinitionService,
    canonical_hash,
    script_hash,
    span_hash,
)
from app.workers.production_workflow import ProductionWorkflowWorker
from app.services.script_qualification_authority import (
    canonical_memory_digest_hash,
    validate_memory_digest,
)
from app.services.runtime_migration_guard import (
    REQUIRED_RUNTIME_DB_REVISION,
    RuntimeMigrationGuard,
)
from app.contracts.production_package import ProductionDurationContractV2
from app.services.v2_support_authority import (
    V2SupportAuthorityService,
    V2SupportProductionContext,
)
from tests.qualification.conftest import QualificationFactory


def _span(script: str, section_id: str, text: str) -> VerifierScriptSpan:
    assert text in script
    return VerifierScriptSpan(
        text=text,
        section_id=section_id,
    )


def test_historical_greenlit_without_current_topic_receipt_is_ineligible(db_session):
    flow = QualificationFactory(db_session).m5_admitted_project(mock_mode="blocked")
    candidate = flow.candidate
    # Simulate a pre-topic-gate historical GREENLIT row. New greenlights must
    # have a receipt, but legacy lineage remains read-only and ineligible.
    candidate.stage = "GREENLIT"
    original = (candidate.stage, candidate.proposed_title, candidate.proposed_angle)

    eligibility = TopicDefinitionService(db_session).current_eligibility(candidate)

    assert not eligibility.eligible
    assert eligibility.primary_reason_code == "EDITORIAL_SUBJECT_NOT_IDENTIFIED"
    assert (candidate.stage, candidate.proposed_title, candidate.proposed_angle) == original


def test_topic_gate_blocks_boilerplate_and_passes_bound_specific_definition(db_session):
    flow = QualificationFactory(db_session).m5_admitted_project(mock_mode="blocked")
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


def _runtime_contract(*, forbidden_claims=None, forbidden_style_terms=None):
    body = {
        "schema_version": "script-runtime-contract.v1",
        "expected_language": "en",
        "duration_contract": {"minimum_duration_ms": 1, "target_duration_ms": 5_000, "maximum_duration_ms": 60_000},
        "duration_estimation_method": "WORD_COUNT_WPM",
        "duration_estimation_wpm": 150,
        "minimum_major_sections": 3,
        "minimum_material_claims": 3,
        "forbidden_claims": forbidden_claims or [],
        "forbidden_style_terms": forbidden_style_terms or [],
        "channel_profile_version_id": str(uuid.uuid4()),
        "channel_profile_hash": "b" * 64,
        "compiled_policy_snapshot_id": str(uuid.uuid4()),
        "compiled_policy_snapshot_hash": "c" * 64,
    }
    body["contract_hash"] = canonical_hash(body)
    return body


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


def test_memory_digest_hash_is_detached_canonical_and_rejects_full_digest_hash():
    digest = {"status": "EMPTY_SAFE_DIGEST", "lessons": [], "non_factual_guidance_only": True}
    digest["digest_hash"] = canonical_hash(digest)
    assert digest["digest_hash"] == canonical_memory_digest_hash(digest)
    assert validate_memory_digest(digest) == digest["digest_hash"]

    digest["digest_hash"] = canonical_hash(digest)
    with pytest.raises(ValueError, match="SCRIPT_MEMORY_DIGEST_HASH_MISMATCH"):
        validate_memory_digest(digest)


def test_runtime_contract_blocks_language_duration_counts_and_forbidden_terms(db_session):
    run, draft, _verifier = _qualified_inputs()
    run.runtime_contract = _runtime_contract(
        forbidden_claims=["invented roi"], forbidden_style_terms=["hype"]
    )
    run.runtime_contract_hash = run.runtime_contract["contract_hash"]
    service = ScriptQualificationService(db_session)

    assert service._structural_receipt(run, draft)["status"] == "PASS"
    wrong_language = draft.model_copy(update={"language": "vi"})
    assert "SCRIPT_LANGUAGE_CONTRACT_MISMATCH" in service._structural_receipt(run, wrong_language)["reason_codes"]

    run.runtime_contract = _runtime_contract()
    run.runtime_contract["duration_contract"]["minimum_duration_ms"] = 100_000
    run.runtime_contract["contract_hash"] = canonical_hash({key: value for key, value in run.runtime_contract.items() if key != "contract_hash"})
    assert "SCRIPT_DURATION_CONTRACT_MISMATCH" in service._structural_receipt(run, draft)["reason_codes"]

    run.runtime_contract = _runtime_contract(forbidden_claims=["official page"])
    run.runtime_contract_hash = run.runtime_contract["contract_hash"]
    assert "SCRIPT_FORBIDDEN_CLAIM_VIOLATION" in service._structural_receipt(run, draft)["reason_codes"]


def test_section_role_reuse_compares_every_prior_and_forbidden_scope_is_complete(db_session):
    run, draft, verifier = _qualified_inputs()
    service = ScriptQualificationService(db_session)
    verifier.section_purpose_observations[1] = SectionPurposeObservation(
        section_id="body", observed_primary_role="HOOK", fulfilled_requirement_ids=["question"],
        editorial_delta="Introduces a distinct decision tension.", genericity_state="SPECIFIC",
        role_reuse_justification="A different editorial function.",
    )
    verifier.section_purpose_observations[2] = SectionPurposeObservation(
        section_id="close", observed_primary_role="HOOK", fulfilled_requirement_ids=["subject"],
        editorial_delta="Establishes the bounded subject.", genericity_state="SPECIFIC",
        role_reuse_justification="A claimed closing distinction.",
    )
    receipts = service._semantic_receipts(run, draft, verifier, service._structural_receipt(run, draft))
    assert "SCRIPT_SECTION_ROLE_REUSE_INVALID" in receipts["fulfillment"]["reason_codes"]

    run.script_assignment["forbidden_scope_units"] = [{"forbidden_scope_id": "forbidden-scope:1", "scope": "unsupported ROI"}]
    missing = service._semantic_receipts(run, draft, verifier, service._structural_receipt(run, draft))
    assert "SCRIPT_FORBIDDEN_SCOPE_OBSERVATION_MISSING:forbidden-scope:1" in missing["fulfillment"]["reason_codes"]
    verifier.forbidden_scope_observations = [
        ForbiddenScopeObservation(forbidden_scope_id="forbidden-scope:1", state="ABSENT")
    ]
    present = service._semantic_receipts(run, draft, verifier, service._structural_receipt(run, draft))
    assert "SCRIPT_FORBIDDEN_SCOPE_OBSERVATION_MISSING:forbidden-scope:1" not in present["fulfillment"]["reason_codes"]


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


def test_assignment_contains_angle_scope_action_and_mode_specific_obligation(db_session):
    service = ScriptQualificationService(db_session)
    candidate = SimpleNamespace(proposed_title="Bounded walkthrough", proposed_angle="Compare the documented boundary before adopting it.")
    standalone = SimpleNamespace(
        id=uuid.uuid4(), topic_definition_hash="a" * 64,
        subject_canonical_id="official-document:one", subject_name="One",
        central_question_or_thesis="What is actually documented?",
        target_audience="Small teams", audience_problem="Need a safe decision.",
        scope_inclusions=["The documented setup boundary"], exclusions=["Undocumented ROI"],
        learning_outcome="Separate evidence from inference.", viewer_value="A bounded decision frame.",
        content_mode="STANDALONE", series_binding=None,
    )
    assignment = service._assignment(candidate, standalone)
    required = {item["requirement_id"] for item in assignment["required_requirement_units"]}
    assert {"subject", "accepted-angle", "question", "audience", "scope-inclusion:1", "outcome", "viewer-value", "viewer-action", "self-containment"} <= required

    episode = SimpleNamespace(**{**standalone.__dict__, "content_mode": "SERIES_EPISODE", "series_binding": {"episode_delta": "Explains the operational limit not covered by episode one."}})
    episode_assignment = service._assignment(candidate, episode)
    episode_required = {item["requirement_id"] for item in episode_assignment["required_requirement_units"]}
    assert "episode-delta" in episode_required
    assert "self-containment" not in episode_required


def test_out_of_scope_assignment_observation_blocks(db_session):
    run, draft, verifier = _qualified_inputs()
    verifier.assignment_fulfillment_observations[0] = AssignmentObservation(
        requirement_id="subject", status="OUT_OF_SCOPE", reason_codes=["forbidden scope used"]
    )
    receipts = ScriptQualificationService(db_session)._semantic_receipts(
        run, draft, verifier, ScriptQualificationService(db_session)._structural_receipt(run, draft)
    )
    assert receipts["fulfillment"]["status"] == "BLOCK"
    assert "SCRIPT_ASSIGNMENT_OUT_OF_SCOPE:subject" in receipts["fulfillment"]["reason_codes"]


def test_duplicate_section_purpose_blocks_but_justified_role_reuse_passes(db_session):
    run, draft, verifier = _qualified_inputs()
    service = ScriptQualificationService(db_session)
    verifier.section_purpose_observations[2] = SectionPurposeObservation(
        section_id="close", observed_primary_role="HOOK", fulfilled_requirement_ids=["outcome"],
        editorial_delta="Establishes the bounded subject.", genericity_state="SPECIFIC",
    )
    blocked = service._semantic_receipts(run, draft, verifier, service._structural_receipt(run, draft))
    assert "SCRIPT_SECTION_ROLE_REUSE_INVALID" in blocked["fulfillment"]["reason_codes"]

    verifier.section_purpose_observations[2] = SectionPurposeObservation(
        section_id="close", observed_primary_role="HOOK", fulfilled_requirement_ids=["outcome", "viewer-value", "self-containment"],
        editorial_delta="Returns to the opening question with a distinct viewer decision.",
        genericity_state="SPECIFIC", role_reuse_justification="The closing hook reframes the decision after the evidence walk-through.",
    )
    passed = service._semantic_receipts(run, draft, verifier, service._structural_receipt(run, draft))
    assert passed["fulfillment"]["status"] == "PASS"


def test_verifier_must_reference_the_writer_exact_evidence_span_ids(db_session):
    run, draft, verifier = _qualified_inputs()
    run.factual_evidence_pack["spans"].append({
        "evidence_span_id": "evidence-2", "evidence_id": "00000000-0000-0000-0000-000000000001",
        "text": "Another exact evidence span with a distinct identity.", "source_snapshot_hash": "b" * 64,
    })
    verifier.material_claim_inventory[0] = verifier.material_claim_inventory[0].model_copy(
        update={"factual_evidence_span_ids": ["evidence-2"]}
    )
    service = ScriptQualificationService(db_session)
    receipts = service._semantic_receipts(run, draft, verifier, service._structural_receipt(run, draft))
    assert receipts["grounding"]["status"] == "BLOCK"
    assert "SCRIPT_MATERIAL_CLAIM_EVIDENCE_SPAN_MISMATCH" in receipts["grounding"]["reason_codes"]


def test_qualification_frozen_sources_preserve_multi_source_long_exact_spans():
    long_text = "Official evidence " + ("x" * 1500)
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    spans = [
        {
            "evidence_span_id": f"search_demand_evidence:{first_id}:0", "evidence_type": "search_demand_evidence", "evidence_id": str(first_id),
            "canonical_url": "https://docs.example.test/one", "authority_purpose": "CLAIM_SOURCE", "evidence_source_type": "OFFICIAL_DOCUMENT",
            "source_class": "OFFICIAL_DOCUMENTATION", "source_classification": "TOPIC_CAPABLE", "source_snapshot_hash": "a" * 64,
            "text": long_text, "start_byte": 0, "end_byte": len(long_text.encode("utf-8")), "span_hash": span_hash(long_text),
            "freshness_state": "FRESH", "source_quality_state": "PASS",
        },
        {
            "evidence_span_id": f"search_demand_evidence:{second_id}:0", "evidence_type": "search_demand_evidence", "evidence_id": str(second_id),
            "canonical_url": "https://docs.example.test/two", "authority_purpose": "CLAIM_SOURCE", "evidence_source_type": "OFFICIAL_MANUAL",
            "source_class": "OFFICIAL_DOCUMENTATION", "source_classification": "TOPIC_CAPABLE", "source_snapshot_hash": "b" * 64,
            "text": "A distinct source span is preserved independently.", "start_byte": 0, "end_byte": 50, "span_hash": span_hash("A distinct source span is preserved independently."),
            "freshness_state": "FRESH", "source_quality_state": "PASS",
        },
    ]
    # Keep offsets faithful to the exact source text in the fixture.
    spans[1]["end_byte"] = len(spans[1]["text"].encode("utf-8"))
    evidence = {"spans": spans}
    memory = {"status": "EMPTY_SAFE_DIGEST"}
    memory["digest_hash"] = canonical_hash(memory)
    content = {"qualified_script": {"canonical_script": "placeholder", "language": "en", "sections": [{"section_id": "one", "heading": "One", "narration": "placeholder"}], "claims": []}, "factual_evidence_pack": evidence, "memory_digest": memory, "producer_provenance": {}}
    receipt = SimpleNamespace(content=content, factual_evidence_pack_hash=canonical_hash(evidence))
    sources = V2SupportAuthorityService._qualification_frozen_sources(receipt)

    assert {(source.type, source.id) for source in sources} == {("search_demand_evidence", first_id), ("search_demand_evidence", second_id)}
    first = next(source for source in sources if source.id == first_id)
    assert first.evidence_spans[0].text == long_text
    assert first.fact_statements == [long_text]


def test_support_projects_qualification_provenance_and_memory_without_new_retrieval(db_session):
    evidence_id = uuid.uuid4()
    evidence_text = "The official workflow document defines a bounded verification sequence for small teams."
    script_lines = [
        "The documented workflow establishes a bounded verification sequence before a team acts.",
        "That evidence boundary answers the central decision question without inventing performance claims.",
        "A small team can use the documented sequence as its next verification action.",
    ]
    script_payload = QualifiedScriptOutput(
        canonical_script=" ".join(script_lines), language="en",
        sections=[
            {"section_id": "hook", "heading": "Hook", "narration": script_lines[0]},
            {"section_id": "body", "heading": "Body", "narration": script_lines[1]},
            {"section_id": "close", "heading": "Close", "narration": script_lines[2]},
        ],
        claims=[
            {"claim_id": f"claim-{index}", "claim_text": line, "evidence_span_ids": [f"search_demand_evidence:{evidence_id}:0"]}
            for index, line in enumerate(script_lines, start=1)
        ],
    ).model_dump(mode="json")
    evidence_pack = {"spans": [{
        "evidence_span_id": f"search_demand_evidence:{evidence_id}:0", "evidence_type": "search_demand_evidence", "evidence_id": str(evidence_id),
        "canonical_url": "https://docs.example.test/bounded-workflow", "authority_purpose": "CLAIM_SOURCE", "evidence_source_type": "OFFICIAL_DOCUMENT",
        "source_class": "OFFICIAL_DOCUMENTATION", "source_classification": "TOPIC_CAPABLE", "source_snapshot_hash": "d" * 64,
        "text": evidence_text, "start_byte": 0, "end_byte": len(evidence_text.encode("utf-8")), "span_hash": span_hash(evidence_text),
        "freshness_state": "FRESH", "source_quality_state": "PASS",
    }]}
    memory_without_retrieval = {"status": "EMPTY_SAFE_DIGEST", "digest_type": "EMPTY_SAFE_DIGEST"}
    memory_without_retrieval["digest_hash"] = canonical_hash(memory_without_retrieval)
    writer_input_hash = "1" * 64
    provenance = {"writer": {
        "producer_input_hash": writer_input_hash,
        "producer_output_hash": canonical_hash(script_payload),
        "prompt_version": "script-writer-assignment.v1", "lane_name": "long_context_text",
        "selected_model": "gpt-5.6-luna", "fallback_level": "PRIMARY",
        "route_attempt_id": str(uuid.uuid4()), "provider_attempt_id": str(uuid.uuid4()),
        "llm_run_snapshot_id": str(uuid.uuid4()),
    }}
    receipt_content = {
        "qualified_script": script_payload, "factual_evidence_pack": evidence_pack,
        "memory_digest": memory_without_retrieval, "producer_provenance": provenance,
    }
    receipt = SimpleNamespace(
        content=receipt_content, content_hash=canonical_hash(receipt_content),
        factual_evidence_pack_hash=canonical_hash(evidence_pack),
    )
    profile_id, policy_id = uuid.uuid4(), uuid.uuid4()
    duration = ProductionDurationContractV2(
        minimum_duration_ms=1_000, target_duration_ms=10_000, maximum_duration_ms=20_000,
        duration_contract_version="test", source_profile_version_id=profile_id, source_policy_snapshot_id=policy_id,
        duration_contract_hash=ProductionDurationContractV2.calculate_hash(
            minimum_duration_ms=1_000, target_duration_ms=10_000, maximum_duration_ms=20_000,
            duration_contract_version="test", source_profile_version_id=profile_id, source_policy_snapshot_id=policy_id,
        ),
    )
    sources = V2SupportAuthorityService._qualification_frozen_sources(receipt)
    context = V2SupportProductionContext(
        video_project_id=uuid.uuid4(), production_lane="LONG_FORM", title="Bounded Workflow",
        expected_language="en", duration_contract=duration, frozen_sources=sources,
        memory_guidance_digest=memory_without_retrieval,
    )
    validated = V2SupportAuthorityService(db_session)._qualified_validated(
        qualification_receipt=receipt, context=context
    )

    producer = validated["script"].producer_receipt
    assert producer.producer_input_hash == writer_input_hash
    assert producer.producer_output_hash == canonical_hash(script_payload)
    assert producer.qualification_receipt_hash == receipt.content_hash
    assert context.memory_guidance_digest == memory_without_retrieval
    assert validated["claim_bindings"][0].evidence_span_refs[0].text == evidence_text


def test_support_projects_v2_single_source_qualification_without_legacy_script_field(
    db_session,
):
    """The support envelope must consume local canonical V2 narration.

    This exercises the real post-qualification projection boundary: a V2
    writer never provides ``canonical_script``, yet the immutable support
    envelope still receives exact, ordered narration and evidence bindings.
    """

    evidence_id = uuid.uuid4()
    evidence_span_id = f"search_demand_evidence:{evidence_id}:0"
    evidence_text = "The official document establishes a bounded verification sequence."
    narrations = [
        "The documented workflow starts by defining the evidence boundary before a decision.",
        "The next step compares the proposed change with the exact proof the document provides.",
        "The viewer can record the smallest test that would disprove the present assumption.",
    ]
    script_payload = QualifiedScriptOutputV2(
        language="en",
        sections=[
            {
                "section_id": f"section-{index:03d}",
                "ordinal": index,
                "purpose": f"Advance requirement {index}.",
                "narration": narration,
                "required_assignment_unit_refs": [f"requirement-{index}"],
            }
            for index, narration in enumerate(narrations, start=1)
        ],
        claims=[
            {
                "claim_id": f"claim-{index}",
                "claim_text": narration,
                "evidence_span_ids": [evidence_span_id],
            }
            for index, narration in enumerate(narrations, start=1)
        ],
    ).model_dump(mode="json")
    evidence_pack = {"spans": [{
        "evidence_span_id": evidence_span_id,
        "evidence_type": "search_demand_evidence",
        "evidence_id": str(evidence_id),
        "canonical_url": "https://docs.example.test/v2-workflow",
        "authority_purpose": "CLAIM_SOURCE",
        "evidence_source_type": "OFFICIAL_DOCUMENT",
        "source_class": "OFFICIAL_DOCUMENTATION",
        "source_classification": "TOPIC_CAPABLE",
        "source_snapshot_hash": "c" * 64,
        "text": evidence_text,
        "start_byte": 0,
        "end_byte": len(evidence_text.encode("utf-8")),
        "span_hash": span_hash(evidence_text),
        "freshness_state": "FRESH",
        "source_quality_state": "PASS",
    }]}
    memory = {"status": "EMPTY_SAFE_DIGEST", "digest_type": "EMPTY_SAFE_DIGEST"}
    memory["digest_hash"] = canonical_hash(memory)
    provenance = {"writer": {
        "producer_input_hash": "2" * 64,
        "producer_output_hash": canonical_hash(script_payload),
        "prompt_version": "script-writer-assignment.v2",
        "lane_name": "long_context_text",
        "selected_model": "gpt-5.6-luna",
        "fallback_level": "PRIMARY",
        "route_attempt_id": str(uuid.uuid4()),
        "provider_attempt_id": str(uuid.uuid4()),
        "llm_run_snapshot_id": str(uuid.uuid4()),
    }}
    receipt_content = {
        "qualified_script": script_payload,
        "factual_evidence_pack": evidence_pack,
        "memory_digest": memory,
        "producer_provenance": provenance,
    }
    receipt = SimpleNamespace(
        content=receipt_content,
        content_hash=canonical_hash(receipt_content),
        factual_evidence_pack_hash=canonical_hash(evidence_pack),
    )
    profile_id, policy_id = uuid.uuid4(), uuid.uuid4()
    duration = ProductionDurationContractV2(
        minimum_duration_ms=1_000,
        target_duration_ms=10_000,
        maximum_duration_ms=20_000,
        duration_contract_version="test",
        source_profile_version_id=profile_id,
        source_policy_snapshot_id=policy_id,
        duration_contract_hash=ProductionDurationContractV2.calculate_hash(
            minimum_duration_ms=1_000,
            target_duration_ms=10_000,
            maximum_duration_ms=20_000,
            duration_contract_version="test",
            source_profile_version_id=profile_id,
            source_policy_snapshot_id=policy_id,
        ),
    )
    context = V2SupportProductionContext(
        video_project_id=uuid.uuid4(),
        production_lane="LONG_FORM",
        title="V2 support projection",
        expected_language="en",
        duration_contract=duration,
        frozen_sources=V2SupportAuthorityService._qualification_frozen_sources(receipt),
        memory_guidance_digest=memory,
    )

    validated = V2SupportAuthorityService(db_session)._qualified_validated(
        qualification_receipt=receipt, context=context
    )

    assert "canonical_script" not in script_payload
    assert validated["script"].approved_script_text == "\n\n".join(narrations)
    assert [section.heading for section in validated["script"].sections] == [
        "Advance requirement 1.",
        "Advance requirement 2.",
        "Advance requirement 3.",
    ]


def _qualified_outbox_authority(db_session):
    """Reserve a real cadence-owned qualification event and seal a PASS fixture."""

    from app.contracts.launch_cadence import CadenceEvaluationCommand
    from app.core.actor import _system_worker_actor
    from app.services.launch_cadence import LongFormCadenceService
    from tests.test_long_form_launch_cadence import (
        _active_launch_run,
        _actor,
        _approved_launch_policy,
        _bind_current_topic_authority,
        _fixture_qualification_pass,
        _greenlit_candidate,
        _ready_provider_snapshot,
        _test_support_authority_preparer,
    )

    scope = QualificationFactory(db_session).channel_scope(
        name="Qualification Outbox", strict_long_form=True
    )
    policy, admin_actor, _ = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=datetime(2026, 7, 20).date()
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_current_topic_authority(db_session, candidate)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    cadence = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    evaluation = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="qualification-outbox"),
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    assert evaluation.script_qualification_run_id is not None
    qualification = _fixture_qualification_pass(
        db_session, evaluation.script_qualification_run_id
    )
    event = db_session.scalar(
        select(DomainEvent).where(
            DomainEvent.aggregate_id == qualification.id,
            DomainEvent.event_type == "script_qualification.execute.v1",
        )
    )
    assert event is not None
    # Reservation uses the qualification service's own clock; make this
    # outbox fixture explicitly due for the deterministic dispatcher clock.
    event.next_attempt_at = now
    db_session.flush()
    return scope, now, qualification, event


def test_qualified_finalization_retry_never_reinvokes_producers(db_session):
    _scope, now, qualification, event = _qualified_outbox_authority(db_session)
    dispatcher = DurableOutboxDispatcher(db_session, now=lambda: now)
    claim = dispatcher.claim_next(worker_id="qualification-retry-worker")
    assert claim is not None and claim.event_id == event.id
    disposition = dispatcher.record_failure(
        event_id=event.id,
        worker_id="qualification-retry-worker",
        error=RuntimeError("admission transaction failed"),
    )
    assert disposition.retry_scheduled is True
    assert event.last_error_code == "SCRIPT_QUALIFICATION_FINALIZATION_FAILED"

    calls = {"writer": 0, "verifier": 0}

    class _NeverInvokeProducer:
        def write(self, *_args, **_kwargs):
            calls["writer"] += 1
            raise AssertionError("writer must not be replayed after qualification PASS")

        def verify(self, *_args, **_kwargs):
            calls["verifier"] += 1
            raise AssertionError("verifier must not be replayed after qualification PASS")

    assert ScriptQualificationService(db_session, producer=_NeverInvokeProducer()).execute(qualification.id).state == "QUALIFIED"
    assert calls == {"writer": 0, "verifier": 0}


def test_worker_seals_qualification_pass_before_finalization_failure(
    db_session, engine, monkeypatch
):
    """Admission rollback cannot erase the committed two-call PASS authority."""

    from app.contracts.launch_cadence import CadenceEvaluationCommand
    from app.core.actor import _system_worker_actor
    from app.services.launch_cadence import LongFormCadenceService
    from tests.test_long_form_launch_cadence import (
        _DeterministicPassingQualificationProducer,
        _active_launch_run,
        _actor,
        _approved_launch_policy,
        _bind_current_topic_authority,
        _greenlit_candidate,
        _ready_provider_snapshot,
        _test_support_authority_preparer,
    )

    scope = QualificationFactory(db_session).channel_scope(
        name="Qualification pass boundary", strict_long_form=True
    )
    policy, admin_actor, _ = _approved_launch_policy(
        db_session, scope, timezone_name="UTC", weekdays=["TUESDAY"]
    )
    launch = _active_launch_run(
        db_session, policy, admin_actor, started_on=datetime(2026, 7, 20).date()
    )
    _, candidate, _ = _greenlit_candidate(db_session, scope, _actor(db_session, scope))
    _bind_current_topic_authority(db_session, candidate)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    evaluation = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    ).evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="durable-pass-boundary"),
        actor=_system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        ),
    )
    assert evaluation.script_qualification_run_id is not None
    db_session.commit()
    qualification_event_id = db_session.scalar(
        select(DomainEvent.id).where(
            DomainEvent.aggregate_id == evaluation.script_qualification_run_id,
            DomainEvent.event_type == "script_qualification.execute.v1",
        )
    )
    assert qualification_event_id is not None
    qualification_event = db_session.get(DomainEvent, qualification_event_id)
    assert qualification_event is not None
    qualification_event.next_attempt_at = now
    db_session.commit()

    producer = _DeterministicPassingQualificationProducer()
    monkeypatch.setattr(
        "app.services.script_qualification.LunaScriptQualificationProducer",
        lambda _session: producer,
    )

    original_finalize = LongFormCadenceService.finalize_qualified_script_run

    def _fail_finalization(*_args, **_kwargs):
        raise RuntimeError("inject final admission failure")

    monkeypatch.setattr(
        LongFormCadenceService, "finalize_qualified_script_run", _fail_finalization
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    first_worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id="durable-pass-boundary-first",
        now=lambda: now,
    )
    first = next(
        result
        for _ in range(4)
        if (result := first_worker.run_once()).event_id == qualification_event_id
    )
    assert first.event_id == qualification_event_id
    assert first.status == "RETRY_SCHEDULED"
    assert first.retry_scheduled is True
    assert producer.writer_calls == 1
    assert producer.verifier_calls == 1

    db_session.expire_all()
    qualification = db_session.get(
        ScriptQualificationRun, evaluation.script_qualification_run_id
    )
    receipt = db_session.scalar(
        select(ScriptQualificationReceipt).where(
            ScriptQualificationReceipt.script_qualification_run_id == qualification.id
        )
    )
    assert qualification is not None and qualification.state == "QUALIFIED"
    assert receipt is not None and receipt.result == "PASS"
    assert canonical_hash(receipt.content) == receipt.content_hash

    monkeypatch.setattr(
        LongFormCadenceService,
        "finalize_qualified_script_run",
        lambda *_args, **_kwargs: (None, None),
    )
    second_worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id="durable-pass-boundary-retry",
        now=lambda: now + timedelta(days=1),
    )
    second = next(
        result
        for _ in range(4)
        if (result := second_worker.run_once()).event_id == qualification_event_id
    )
    assert second.status == "DELIVERED"
    assert producer.writer_calls == 1
    assert producer.verifier_calls == 1
    assert original_finalize is not None


def test_runtime_migration_guard_blocks_worker_before_any_production_claim(
    db_session, engine
):
    db_session.execute(
        text("update alembic_version set version_num = '0060_series_episode_reservations'")
    )
    db_session.commit()
    try:
        assert RuntimeMigrationGuard(db_session).inspect().ready is False
        result = ProductionWorkflowWorker(
            session_factory=sessionmaker(bind=engine, expire_on_commit=False),
            worker_id="schema-guard-worker",
        ).run_once()
        assert result.status == "SCHEMA_BLOCKED"
    finally:
        db_session.execute(
            text("update alembic_version set version_num = :revision"),
            {"revision": REQUIRED_RUNTIME_DB_REVISION},
        )
        db_session.commit()
    assert RuntimeMigrationGuard(db_session).inspect().ready is True


def test_qualification_shutdown_and_expired_lease_reconcile_without_orphaning(db_session):
    _scope, now, qualification, event = _qualified_outbox_authority(db_session)
    dispatcher = DurableOutboxDispatcher(db_session, now=lambda: now)
    event.lease_owner = "qualification-shutdown-worker"
    event.lease_expires_at = now + timedelta(seconds=30)
    db_session.flush()
    assert dispatcher.release_worker_leases(worker_id="qualification-shutdown-worker") == 1
    assert event.lease_owner is None
    assert event.next_attempt_at == now

    event.lease_owner = "qualification-expired-worker"
    event.lease_expires_at = now - timedelta(seconds=1)
    db_session.flush()
    assert dispatcher.reclaim_expired() == 1
    assert event.dead_lettered_at is None
    assert qualification.state == "QUALIFIED"
    assert event.next_attempt_at == now


def test_unknown_provider_outcome_fails_closed_after_worker_restart(db_session):
    _scope, now, qualification, event = _qualified_outbox_authority(db_session)
    qualification.state = "WRITER_DISPATCHED"
    event.lease_owner = "crashed-qualification-worker"
    event.lease_expires_at = now - timedelta(seconds=1)
    db_session.flush()
    dispatcher = DurableOutboxDispatcher(db_session, now=lambda: now)
    assert dispatcher.reclaim_expired() == 1

    class _NeverRetryProvider:
        def write(self, *_args, **_kwargs):
            raise AssertionError("unknown writer outcome must fail closed")

        def verify(self, *_args, **_kwargs):
            raise AssertionError("unknown verifier outcome must fail closed")

    result = ScriptQualificationService(
        db_session, producer=_NeverRetryProvider()
    ).execute(qualification.id)
    assert result.state == "BLOCKED_NON_REPAIRABLE"
    assert result.failure_receipt["reason_codes"] == [
        "SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY"
    ]


def test_only_qualified_finalization_dead_letters_are_replayable(db_session):
    scope, now, qualification, event = _qualified_outbox_authority(db_session)
    dispatcher = DurableOutboxDispatcher(db_session, now=lambda: now)
    event.lease_owner = "qualification-dead-letter-worker"
    event.lease_expires_at = now + timedelta(seconds=30)
    event.attempt_count = event.max_attempts
    db_session.flush()
    disposition = dispatcher.record_failure(
        event_id=event.id,
        worker_id="qualification-dead-letter-worker",
        error=RuntimeError("final admission still unavailable"),
    )
    assert disposition.retry_scheduled is False
    assert disposition.dead_letter_job_id is not None
    job = db_session.get(DeadLetterJob, disposition.dead_letter_job_id)
    assert job is not None and job.workflow_run_id is None and job.retry_eligible is True

    from app.contracts.production_workflow import DeadLetterRetryRequest
    from tests.test_long_form_launch_cadence import _actor

    replay = dispatcher.retry_dead_letter(
        dead_letter_job_id=job.id,
        company_id=scope.company.id,
        data=DeadLetterRetryRequest(reason_code="RETRY_FINAL_ADMISSION", additional_attempts=1),
        actor=_actor(db_session, scope, admin=True),
    )
    assert replay.workflow_run_id is None
    assert event.dead_lettered_at is None
    assert qualification.state == "QUALIFIED"
