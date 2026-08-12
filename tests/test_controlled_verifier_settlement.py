from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import ProgrammingError

from app.contracts.production_package import ProductionDurationContractV2
from app.contracts.script_qualification import (
    QualifiedScriptOutputV2,
    SemanticVerificationOutput,
)
from app.core.errors import ValidationFailureError
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.script_qualification import (
    ControlledVerifierSettlementAuthority,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationReceipt,
)
from app.services.config_registry import content_hash
from app.services.script_contract_replacement import (
    CONTROLLED_VERIFIER_SETTLEMENT_POLICY,
    CONTROLLED_VERIFIER_SETTLEMENT_REASON,
    CONTROLLED_VERIFIER_SETTLEMENT_SCHEMA,
    controlled_verifier_settlement_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.script_qualification import ScriptQualificationService
from app.services.script_qualification_background import (
    ScriptQualificationBackgroundService,
)
from app.services.script_qualification_recovery import (
    ScriptQualificationRecoveryService,
)
from app.services.script_verifier_settlement import (
    ScriptVerifierSettlementRecoveryService,
    derive_v3_semantic_receipts,
)
from app.services.v2_support_authority import (
    V2SupportAuthorityService,
    V2SupportProductionContext,
)
from tests.qualification.conftest import QualificationFactory
from tests.test_controlled_production_continuation import (
    _build_continuation_lineage,
    _completed_attempt_with_snapshot,
    _fresh_attempt,
)
import tests.test_controlled_production_continuation as continuation_test


_PARAPHRASE_CLAIM_2 = (
    "Function calling connects a model to external tools and APIs."
)
_EXACT_CLAIM_2 = (
    "Function calling lets you connect models to external tools and APIs."
)
_PARAPHRASE_CLAIM_3 = (
    "The documented examples include actions such as scheduling an appointment, "
    "creating an invoice, or sending an email."
)
_EXACT_CLAIM_3 = (
    "The documented action examples also include scheduling appointments, "
    "creating invoices, and sending emails."
)
_QUESTION_ALTERNATE = (
    "It is whether the workflow can turn unstructured text into predictable data, "
    "and then connect that data to an action without treating generated prose as "
    "an executable instruction."
)
_SHARED_FULFILLMENT_SPAN = (
    "Taken together, the workflow answers the central question without requiring "
    "a previous episode."
)


@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)


def _live_shaped_v2_payload(run, *, repaired: bool = False) -> dict:
    """Create the audited 67-sentence shape under the fixture's frozen plan."""

    plan = (run.script_assignment or {})["section_coverage_plan"]
    assert len(plan["sections"]) == 3
    evidence_span_id = run.factual_evidence_pack["spans"][0]["evidence_span_id"]
    special = {
        4: _QUESTION_ALTERNATE,
        10: _PARAPHRASE_CLAIM_2,
        12: _PARAPHRASE_CLAIM_3,
        55: _EXACT_CLAIM_2,
        60: _EXACT_CLAIM_3,
        63: _SHARED_FULFILLMENT_SPAN,
    }
    # Preserve the audited global sentence ordinals while fitting the real
    # three-section continuation assignment.  Question is owned by section 1;
    # self-containment (and the shared sentence 63) is owned by section 3.
    counts = [23, 22, 22]
    output_sections: list[dict] = []
    global_ordinal = 0
    claim_1_text = ""
    for section_index, (coverage, sentence_count) in enumerate(
        zip(plan["sections"], counts, strict=True), start=1
    ):
        sentences: list[str] = []
        for local_ordinal in range(1, sentence_count + 1):
            global_ordinal += 1
            if global_ordinal in special:
                sentence = special[global_ordinal]
            else:
                variant = (
                    "repaired"
                    if repaired and section_index == 1
                    else "original"
                )
                sentence = " ".join(
                    [
                        f"{variant}section{section_index}",
                        f"sentence{global_ordinal}",
                        *[
                            f"s{section_index}n{global_ordinal}token{token}"
                            for token in range(1, 19)
                        ],
                    ]
                ) + "."
            if global_ordinal == 1:
                claim_1_text = sentence
            sentences.append(sentence)
        expected_claim_refs: list[str] = []
        if section_index == 1:
            # Mirror the audited provider output: the paraphrase observations
            # and their later exact anchors are both explicitly declared.
            expected_claim_refs = ["claim-001", "claim-002", "claim-003"]
        elif section_index == 3:
            expected_claim_refs = ["claim-002", "claim-003"]
        output_sections.append(
            {
                "section_id": coverage["section_id"],
                "ordinal": coverage["ordinal"],
                "purpose": coverage["section_delta"],
                "narration": " ".join(sentences),
                "required_assignment_unit_refs": coverage[
                    "primary_requirement_ids"
                ],
                "expected_claim_refs": expected_claim_refs,
            }
        )
    assert global_ordinal == 67
    return QualifiedScriptOutputV2(
        language=(run.runtime_contract or {}).get("expected_language") or "en",
        sections=output_sections,
        claims=[
            {
                "claim_id": "claim-001",
                "claim_text": claim_1_text,
                "evidence_span_ids": [evidence_span_id],
            },
            {
                "claim_id": "claim-002",
                "claim_text": _EXACT_CLAIM_2,
                "evidence_span_ids": [evidence_span_id],
            },
            {
                "claim_id": "claim-003",
                "claim_text": _EXACT_CLAIM_3,
                "evidence_span_ids": [evidence_span_id],
            },
        ],
    ).model_dump(mode="json")


def _live_shaped_verifier(run) -> SemanticVerificationOutput:
    # The canonical inventory is deliberately derived from the persisted child,
    # not duplicated from the expected sentence numbering in this test.
    qualification = ScriptQualificationService(run._sa_instance_state.session)
    materialized = qualification.draft_from_run(run)
    inventory = qualification._canonical_sentence_inventory(materialized)[
        "sentences"
    ]
    assert len(inventory) == 67
    by_id = {item["sentence_id"]: item for item in inventory}
    evidence_span_id = run.factual_evidence_pack["spans"][0]["evidence_span_id"]
    material_claims = {
        "sentence-0001": ("claim-001", ["subject"]),
        "sentence-0010": ("claim-002", ["subject", "scope-inclusion:3"]),
        "sentence-0012": ("claim-003", ["subject", "scope-inclusion:4"]),
        "sentence-0055": ("claim-002", ["scope-inclusion:3"]),
        "sentence-0060": ("claim-003", ["subject", "scope-inclusion:4"]),
    }
    claim_inventory = []
    for sentence in inventory:
        claim = material_claims.get(sentence["sentence_id"])
        claim_inventory.append(
            {
                "observed_claim_id": sentence["sentence_id"],
                "span": {
                    "text": sentence["text"],
                    "section_id": sentence["section_id"],
                },
                "claim_type": (
                    "FACTUAL_ASSERTION" if claim else "STRUCTURAL_TRANSITION"
                ),
                "materiality_state": "MATERIAL" if claim else "NON_MATERIAL",
                "writer_declared_claim_id": claim[0] if claim else None,
                "factual_evidence_span_ids": [evidence_span_id] if claim else [],
                "semantic_relation": "ENTAILED" if claim else "NOT_APPLICABLE",
                "assignment_requirement_ids": claim[1] if claim else [],
                "reason_codes": ["DIRECT_OFFICIAL_EVIDENCE"] if claim else [],
            }
        )

    coverage_sections = (run.script_assignment or {})["section_coverage_plan"][
        "sections"
    ]
    requirement_spans: dict[str, list[str]] = {}
    starts = [1, 24, 46]
    for section, start in zip(coverage_sections, starts, strict=True):
        for offset, requirement_id in enumerate(
            section["primary_requirement_ids"]
        ):
            requirement_spans[requirement_id] = [
                f"sentence-{start + offset:04d}"
            ]
    requirement_spans["question"] = ["sentence-0004", "sentence-0063"]
    requirement_spans["self-containment"] = ["sentence-0063"]
    fulfillment = []
    for requirement in (run.script_assignment or {})[
        "required_requirement_units"
    ]:
        requirement_id = requirement["requirement_id"]
        fulfillment.append(
            {
                "requirement_id": requirement_id,
                "status": "SUFFICIENT",
                "spans": [
                    {
                        "text": by_id[sentence_id]["text"],
                        "section_id": by_id[sentence_id]["section_id"],
                    }
                    for sentence_id in requirement_spans[requirement_id]
                ],
                "evidence_span_ids": [],
                "missing_reasoning_step": None,
                "reason_codes": [f"{requirement_id.upper()}_PRESENT"],
            }
        )
    section_purpose = [
        {
            "section_id": section["section_id"],
            "observed_primary_role": f"ROLE_{section['ordinal']}",
            "fulfilled_requirement_ids": section["primary_requirement_ids"],
            "editorial_delta": f"Distinct frozen delta {section['ordinal']}",
            "genericity_state": "SPECIFIC",
            "role_reuse_justification": None,
        }
        for section in coverage_sections
    ]
    forbidden = [
        {
            "forbidden_scope_id": item["forbidden_scope_id"],
            "state": "ABSENT",
            "script_spans": [],
            "observed_relation": None,
            "reason_codes": [],
        }
        for item in (run.script_assignment or {}).get("forbidden_scope_units", [])
    ]
    return SemanticVerificationOutput.model_validate(
        {
            "material_claim_inventory": claim_inventory,
            "assignment_fulfillment_observations": fulfillment,
            "section_purpose_observations": section_purpose,
            "forbidden_scope_observations": forbidden,
            "memory_application_observations": [],
        }
    )


def _verifier_copy(verifier: SemanticVerificationOutput, mutate) -> SemanticVerificationOutput:
    payload = verifier.model_dump(mode="json")
    mutate(payload)
    return SemanticVerificationOutput.model_validate(payload)


def _blocked_live_shaped_source(
    session,
    qualification_factory,
    monkeypatch,
) -> SimpleNamespace:
    monkeypatch.setattr(
        continuation_test,
        "_v2_payload",
        _live_shaped_v2_payload,
    )
    lineage = _build_continuation_lineage(
        session, qualification_factory, monkeypatch
    )
    source = lineage.child
    verifier = _live_shaped_verifier(source)
    attempt, snapshot = _completed_attempt_with_snapshot(
        session,
        run=source,
        phase="VERIFIER",
        payload=verifier.model_dump(mode="json"),
        provider_outcome="COMPLETED",
        prompt_version=source.verifier_prompt_version,
        identity="live-shaped-settlement-verifier",
    )
    source.verifier_receipt = ScriptQualificationBackgroundService._receipt(
        attempt, {"usage": None}
    )
    qualification = ScriptQualificationService(session)
    draft = qualification.draft_from_run(source)
    structural = qualification._structural_receipt(source, draft)
    receipts = qualification._semantic_receipts(
        source, draft, verifier, structural
    )
    assert receipts["structural"]["status"] == "PASS"
    assert receipts["inventory"]["reason_codes"] == [
        "SCRIPT_WRITER_CLAIM_SPAN_MISMATCH"
    ]
    assert receipts["fulfillment"]["reason_codes"] == [
        "SCRIPT_ASSIGNMENT_COVERAGE_SPAN_REUSED"
    ]
    assert receipts["memory"]["status"] == "PASS_EMPTY"
    source.state = "BLOCKED_NON_REPAIRABLE"
    source.result_receipts = receipts
    source.failure_receipt = {
        "reason_codes": [
            reason
            for receipt in receipts.values()
            for reason in receipt["reason_codes"]
        ]
    }
    qualification._create_receipt(source, draft, "BLOCK", receipts)
    settlement_now = lineage.now + timedelta(seconds=1)
    ScriptQualificationRecoveryService(
        session, now=lambda: settlement_now
    ).settle_deterministic_block(
        source, reason_code="SCRIPT_QUALIFICATION_BLOCKED"
    )
    source_receipt = session.scalar(
        select(ScriptQualificationReceipt).where(
            ScriptQualificationReceipt.script_qualification_run_id == source.id
        )
    )
    assert source_receipt is not None and source_receipt.result == "BLOCK"
    session.flush()
    return SimpleNamespace(
        **vars(lineage),
        verifier=verifier,
        verifier_attempt=attempt,
        verifier_snapshot=snapshot,
        draft=draft,
        source_receipt=source_receipt,
        settlement_now=settlement_now,
    )


def test_policy_v3_applies_only_exact_anchor_and_frozen_ownership_projection(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )

    receipts, projection = derive_v3_semantic_receipts(
        service=ScriptQualificationService(db_session),
        run=lineage.child,
        draft=lineage.draft,
        verifier=lineage.verifier,
        source_verifier_output_hash=content_hash(
            lineage.verifier.model_dump(mode="json")
        ),
    )

    assert all(
        receipt["status"] in {"PASS", "PASS_EMPTY"}
        for receipt in receipts.values()
    )
    assert projection["schema_version"]
    assert projection["policy_version"] == CONTROLLED_VERIFIER_SETTLEMENT_POLICY
    assert projection["source_verifier_output_hash"] == content_hash(
        lineage.verifier.model_dump(mode="json")
    )
    assert projection["claim_anchor_decisions"] == [
        {
            "observed_claim_id": "sentence-0010",
            "writer_declared_claim_id": "claim-002",
            "anchor_observed_claim_id": "sentence-0055",
            "evidence_span_ids": [
                lineage.child.factual_evidence_pack["spans"][0][
                    "evidence_span_id"
                ]
            ],
            "semantic_relation": "ENTAILED",
        },
        {
            "observed_claim_id": "sentence-0012",
            "writer_declared_claim_id": "claim-003",
            "anchor_observed_claim_id": "sentence-0060",
            "evidence_span_ids": [
                lineage.child.factual_evidence_pack["spans"][0][
                    "evidence_span_id"
                ]
            ],
            "semantic_relation": "ENTAILED",
        },
    ]
    assert projection["removed_fulfillment_spans"] == [
        {
            "requirement_id": "question",
            "retained_owner_requirement_id": "self-containment",
            "section_id": "section-003",
            "text": _SHARED_FULFILLMENT_SPAN,
        }
    ]
    assert projection["resulting_receipts_hash"] == content_hash(receipts)
    assert projection["content_hash"] == content_hash(
        {key: value for key, value in projection.items() if key != "content_hash"}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["material_claim_inventory"][9].update(
            semantic_relation="PARTIALLY_SUPPORTED"
        ),
        lambda payload: payload["material_claim_inventory"][9].update(
            factual_evidence_span_ids=["unknown-evidence-span"]
        ),
        lambda payload: payload["assignment_fulfillment_observations"][2].update(
            spans=[payload["assignment_fulfillment_observations"][2]["spans"][1]]
        ),
        lambda payload: payload["assignment_fulfillment_observations"][2].update(
            spans=[
                {
                    **payload["material_claim_inventory"][23]["span"],
                },
                payload["assignment_fulfillment_observations"][2]["spans"][1],
            ]
        ),
        lambda payload: payload["assignment_fulfillment_observations"][0].update(
            status="PARTIAL"
        ),
    ],
    ids=[
        "paraphrase-not-entailed",
        "paraphrase-evidence-mismatch",
        "duplicate-owner-has-no-alternate",
        "alternate-outside-frozen-ownership",
        "unrelated-gate-still-blocked",
    ],
)
def test_policy_v3_rejects_unsafe_semantic_projection(
    db_session,
    qualification_factory,
    monkeypatch,
    mutation,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    unsafe = _verifier_copy(lineage.verifier, mutation)

    with pytest.raises(ValidationFailureError):
        derive_v3_semantic_receipts(
            service=ScriptQualificationService(db_session),
            run=lineage.child,
            draft=lineage.draft,
            verifier=unsafe,
            source_verifier_output_hash=content_hash(
                unsafe.model_dump(mode="json")
            ),
        )


def test_policy_v3_rejects_paraphrase_without_exact_canonical_anchor(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    claims = list(lineage.draft.claims)
    claims[1] = claims[1].model_copy(
        update={"claim_text": "This claim text occurs nowhere in the narration."}
    )
    unsafe_draft = lineage.draft.model_copy(update={"claims": claims})

    with pytest.raises(ValidationFailureError):
        derive_v3_semantic_receipts(
            service=ScriptQualificationService(db_session),
            run=lineage.child,
            draft=unsafe_draft,
            verifier=lineage.verifier,
            source_verifier_output_hash=content_hash(
                lineage.verifier.model_dump(mode="json")
            ),
        )


@pytest.mark.parametrize(
    "section_index",
    [0, 2],
    ids=["paraphrase-section-undeclared", "anchor-section-undeclared"],
)
def test_policy_v3_requires_claim_declared_in_both_observation_sections(
    db_session,
    qualification_factory,
    monkeypatch,
    section_index,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    unsafe_payload = deepcopy(lineage.child.script_payload)
    section = unsafe_payload["sections"][section_index]
    section["expected_claim_refs"] = [
        claim_id
        for claim_id in section["expected_claim_refs"]
        if claim_id != "claim-002"
    ]
    lineage.child.script_payload = unsafe_payload

    with pytest.raises(
        ValidationFailureError,
        match="VERIFIER_SETTLEMENT_EXACT_ANCHOR_INVALID",
    ):
        derive_v3_semantic_receipts(
            service=ScriptQualificationService(db_session),
            run=lineage.child,
            draft=lineage.draft,
            verifier=lineage.verifier,
            source_verifier_output_hash=content_hash(
                lineage.verifier.model_dump(mode="json")
            ),
        )


def _source_snapshot(lineage) -> dict:
    source = lineage.child
    slot = lineage.continuation_slot
    return {
        "state": source.state,
        "failure_receipt": deepcopy(source.failure_receipt),
        "result_receipts": deepcopy(source.result_receipts),
        "terminal_settlement_receipt": deepcopy(
            source.terminal_settlement_receipt
        ),
        "script_payload": deepcopy(source.script_payload),
        "canonical_script_artifact_id": source.canonical_script_artifact_id,
        "derived_canonical_script_hash": source.derived_canonical_script_hash,
        "writer_receipt": deepcopy(source.writer_receipt),
        "verifier_receipt": deepcopy(source.verifier_receipt),
        "slot": {
            "id": slot.id,
            "state": slot.state,
            "reserved_candidate_id": slot.reserved_candidate_id,
            "admitted_video_project_id": slot.admitted_video_project_id,
            "target_start_window_open_at": slot.target_start_window_open_at,
            "target_start_window_close_at": slot.target_start_window_close_at,
            "intended_publish_at": slot.intended_publish_at,
            "replacement_lineage_key": slot.replacement_lineage_key,
        },
    }


def test_zero_provider_settlement_is_append_only_idempotent_and_consumable(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    before = _source_snapshot(lineage)
    attempt_count = db_session.scalar(
        select(func.count(ScriptQualificationBackgroundAttempt.id))
    )
    snapshot_count = db_session.scalar(
        select(func.count(ScriptQualificationProviderResponseSnapshot.id))
    )
    monkeypatch.setattr(
        "app.services.script_verifier_settlement.resolve_provider_authority",
        lambda *_args, **_kwargs: continuation_test._ready_snapshot(),
    )
    monkeypatch.setattr(
        "app.services.script_verifier_settlement.resolve_budget_authority",
        lambda *_args, **_kwargs: continuation_test._ready_snapshot(),
    )
    service = ScriptVerifierSettlementRecoveryService(
        db_session, now=lambda: lineage.settlement_now
    )

    child = service.create(source_qualification_run_id=lineage.child.id)
    repeated = service.create(source_qualification_run_id=lineage.child.id)

    assert repeated.id == child.id
    assert _source_snapshot(lineage) == before
    assert child.id != lineage.child.id
    assert child.supersedes_qualification_run_id == lineage.child.id
    assert child.state == "QUALIFIED"
    assert child.script_payload == lineage.child.script_payload
    assert child.canonical_script_artifact_id != lineage.child.canonical_script_artifact_id
    assert child.derived_canonical_script_hash == lineage.child.derived_canonical_script_hash
    # Settlement explicitly reclassifies the trusted local producer while
    # preserving every original provider-bound field and hash.
    assert {
        key: child.writer_receipt[key]
        for key in lineage.child.writer_receipt
        if key not in {"producer", "producer_type"}
    } == {
        key: value
        for key, value in lineage.child.writer_receipt.items()
        if key not in {"producer", "producer_type"}
    }
    assert child.writer_receipt["producer"] == (
        "DERIVED_FROM_COMPLETED_VERIFIER_SETTLEMENT"
    )
    assert child.writer_receipt["producer_type"] == (
        "OPENAI_BACKGROUND_VERIFIER_SETTLEMENT"
    )
    assert {
        key: child.verifier_receipt[key]
        for key in lineage.child.verifier_receipt
    } == lineage.child.verifier_receipt
    assert child.script_assignment_hash == lineage.child.script_assignment_hash
    assert child.factual_evidence_pack_hash == lineage.child.factual_evidence_pack_hash
    assert child.runtime_contract_hash == lineage.child.runtime_contract_hash
    assert child.assignment_resolution_hash == lineage.child.assignment_resolution_hash
    assert db_session.scalar(
        select(func.count(ScriptQualificationBackgroundAttempt.id))
    ) == attempt_count
    assert db_session.scalar(
        select(func.count(ScriptQualificationProviderResponseSnapshot.id))
    ) == snapshot_count
    assert db_session.scalar(
        select(func.count(ScriptQualificationBackgroundAttempt.id)).where(
            ScriptQualificationBackgroundAttempt.script_qualification_run_id
            == child.id
        )
    ) == 0

    authority = db_session.scalar(
        select(ControlledVerifierSettlementAuthority).where(
            ControlledVerifierSettlementAuthority.settlement_qualification_run_id
            == child.id
        )
    )
    assert authority is not None
    assert child.writer_receipt["settlement_source_qualification_run_id"] == str(
        lineage.child.id
    )
    assert child.writer_receipt["settlement_source_verifier_attempt_id"] == str(
        lineage.verifier_attempt.id
    )
    assert child.writer_receipt["settlement_source_verifier_snapshot_id"] == str(
        lineage.verifier_snapshot.id
    )
    assert child.writer_receipt["settlement_authority_id"] == str(authority.id)
    assert child.writer_receipt["settlement_authority_hash"] == authority.authority_hash
    assert child.writer_receipt["settlement_projection_hash"] == (
        authority.derived_projection_hash
    )
    assert child.writer_receipt["provider_submission_count_for_settlement"] == 0
    assert child.verifier_receipt["settlement_authority_id"] == str(authority.id)
    assert child.verifier_receipt["settlement_authority_hash"] == authority.authority_hash
    assert child.verifier_receipt["settlement_source_qualification_run_id"] == str(
        lineage.child.id
    )
    assert child.verifier_receipt["settlement_source_verifier_snapshot_id"] == str(
        lineage.verifier_snapshot.id
    )
    assert child.verifier_receipt["derived_projection_hash"] == (
        authority.derived_projection_hash
    )
    assert child.verifier_receipt["provider_submission_count_for_settlement"] == 0
    slot = db_session.get(LongFormPublishSlot, child.publish_slot_id)
    assert slot is not None and slot.id != lineage.continuation_slot.id
    assert slot.replaces_slot_id == lineage.continuation_slot.id
    assert slot.state == "QUALIFICATION_RESERVED"
    assert lineage.continuation_slot.state == "CANCELED"
    assert authority.schema_version == CONTROLLED_VERIFIER_SETTLEMENT_SCHEMA
    assert authority.settlement_reason == CONTROLLED_VERIFIER_SETTLEMENT_REASON
    assert authority.settlement_policy_version == CONTROLLED_VERIFIER_SETTLEMENT_POLICY
    assert authority.source_verifier_attempt_id == lineage.verifier_attempt.id
    assert authority.source_verifier_snapshot_id == lineage.verifier_snapshot.id
    assert authority.max_provider_submissions == 0
    assert authority.authority_hash == content_hash(
        controlled_verifier_settlement_authority_body(authority)
    )
    assert authority.derived_projection_hash == authority.derived_projection[
        "content_hash"
    ]
    assert authority.derived_projection_hash == content_hash(
        {
            key: value
            for key, value in authority.derived_projection.items()
            if key != "content_hash"
        }
    )
    assert db_session.scalar(
        select(func.count(ControlledVerifierSettlementAuthority.id))
    ) == 1

    resolved = resolve_replacement_qualification_leaf(
        db_session, authority=lineage.root_lineage.authority
    )
    assert resolved.id == child.id
    pass_receipt = ScriptQualificationService(db_session).require_pass(
        child.id, candidate_id=child.editorial_idea_candidate_id
    )
    materialized, evidence, memory, _provenance = (
        ScriptQualificationService.qualification_output(pass_receipt)
    )
    assert materialized["canonical_script"] == lineage.draft.canonical_script
    assert evidence == child.factual_evidence_pack
    assert memory == child.memory_digest

    duration = ProductionDurationContractV2.model_validate(
        child.runtime_contract["duration_contract"]
    )
    support_context = V2SupportProductionContext(
        video_project_id=uuid.uuid4(),
        production_lane="LONG_FORM",
        title="Controlled verifier settlement",
        expected_language=child.runtime_contract["expected_language"],
        duration_contract=duration,
        frozen_sources=V2SupportAuthorityService._qualification_frozen_sources(
            pass_receipt
        ),
        memory_guidance_digest=child.memory_digest,
    )
    supported = V2SupportAuthorityService(db_session)._qualified_validated(
        qualification_receipt=pass_receipt,
        context=support_context,
    )
    trusted_script = supported["script"]
    assert trusted_script.approved_script_text == lineage.draft.canonical_script
    producer = trusted_script.producer_receipt
    assert producer.producer_type == "OPENAI_BACKGROUND_VERIFIER_SETTLEMENT"
    assert producer.settlement_source_qualification_run_id == lineage.child.id
    assert producer.settlement_source_verifier_attempt_id == lineage.verifier_attempt.id
    assert producer.settlement_source_verifier_snapshot_id == (
        lineage.verifier_snapshot.id
    )
    assert producer.settlement_authority_id == authority.id
    assert producer.settlement_authority_hash == authority.authority_hash
    assert producer.settlement_projection_hash == authority.derived_projection_hash

    for statement in (
        update(ControlledVerifierSettlementAuthority)
        .where(ControlledVerifierSettlementAuthority.id == authority.id)
        .values(max_provider_submissions=1),
        delete(ControlledVerifierSettlementAuthority).where(
            ControlledVerifierSettlementAuthority.id == authority.id
        ),
    ):
        with pytest.raises(
            ProgrammingError,
            match="controlled verifier settlement authorities are immutable",
        ):
            with db_session.begin_nested():
                db_session.execute(statement)
                db_session.flush()
        db_session.expire_all()

    with pytest.raises(
        ProgrammingError,
        match="controlled verifier settlement forbids provider submissions",
    ):
        with db_session.begin_nested():
            db_session.add(
                _fresh_attempt(
                    child,
                    phase="VERIFIER",
                    identity="forbidden-settlement-provider",
                )
            )
            db_session.flush()
