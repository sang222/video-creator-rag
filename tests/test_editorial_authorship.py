from __future__ import annotations

import pytest

from app.contracts.cross_modal import (
    InformationUnit,
    SectionCoverage,
    SectionCoveragePlan,
    cross_modal_hash,
)
from app.contracts.editorial_authorship import (
    EditorialAuthorityBinding,
    EditorialAuthorityType,
    EditorialAuthorshipContract,
    ViewerFacingAuthorshipLaw,
    validate_viewer_facing_presentation,
)
from app.core.errors import ValidationFailureError
from app.services.editorial_specificity import EditorialIdeaProposal
from app.services.gates import _production_editorial_authorship_gate
from app.services.v2_package_readiness import _exact_reasoning_progression


def _contract() -> EditorialAuthorshipContract:
    return EditorialAuthorshipContract.build(
        source_evidence_authorities=[
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.SOURCE_EVIDENCE,
                authority_ref="evidence://source-1",
                content_hash="a" * 64,
            )
        ],
        authored_authorities=[
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.EDITORIAL_PROPOSAL,
                authority_ref=f"editorial-proposal://{'b' * 64}",
                content_hash="b" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=(
                    EditorialAuthorityType.EDITORIAL_SPECIFICITY_RECEIPT
                ),
                authority_ref=f"editorial-specificity://{'c' * 64}",
                content_hash="c" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.TOPIC_DEFINITION,
                authority_ref=f"topic-definition://{'d' * 64}",
                content_hash="d" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.VIDEO_PROJECT,
                authority_ref="video-project://project-1",
                content_hash="e" * 64,
            ),
        ],
        content_mode="STANDALONE",
        format_key="future-format",
        channel_promise="Give viewers evidence-led decisions.",
        episode_reasoning="This episode tests one decision boundary.",
        central_question="Where should the decision happen?",
        early_stakes_or_payoff="A missed boundary creates an unreviewed change.",
        original_thesis_or_position="Keep human review at the external-action boundary.",
        editorial_delta="Turns source evidence into a concrete decision frame.",
        reasoning_or_narrative_spine="Question -> evidence -> decision",
        progression="The reasoning moves from question to evidence to decision.",
        tension_applicability="APPLICABLE",
        tension_failure_contradiction_or_tradeoff=(
            "Speed trades off against accountability."
        ),
        visible_editorial_judgment="Review is required before the external action.",
        memorable_payoff_framework_or_conclusion="Use the boundary test before shipping.",
    )


def _contract_values() -> dict:
    return _contract().model_dump(mode="python", exclude={"content_hash"})


def _proposal(**overrides: object) -> EditorialIdeaProposal:
    values: dict[str, object] = {
        "proposed_title": "Where the approval boundary belongs",
        "proposed_angle": "Frame the boundary as an operational decision.",
        "specific_audience_problem": "Operators can miss a hidden handoff.",
        "central_question_or_thesis": "Where should the boundary be placed?",
        "learning_outcome": "Recognize the accountable handoff.",
        "viewer_value": "A reusable boundary test.",
        "editorial_delta": "Make one hidden exception path explicit.",
        "specific_mechanism_or_use_case": "An asynchronous approval recovery.",
        "decision_value": "Choose review before an external action.",
        "scope_inclusions": ["approval recovery"],
        "scope_exclusions": ["provider choreography"],
        "primary_evidence_refs": [{"id": "source-1", "ref": "evidence://source-1"}],
        "supporting_evidence_refs": [],
        "evidence_bindings": [
            {
                "field": "proposed_title",
                "evidence_id": "source-1",
                "quoted_text": "approval recovery",
            }
        ],
        "source_specificity_class": "NARROW_TOPIC_CAPABLE",
        "content_mode": "STANDALONE",
        "series_binding": None,
        "tension_applicability": "NOT_APPLICABLE",
        "tension_failure_contradiction_or_tradeoff": None,
    }
    values.update(overrides)
    return EditorialIdeaProposal.model_validate(values)


def _coverage_plan(deltas: list[str]) -> SectionCoveragePlan:
    units: list[InformationUnit] = []
    sections: list[SectionCoverage] = []
    for ordinal, delta in enumerate(deltas, start=1):
        unit_body = {
            "information_unit_id": f"unit-{ordinal}",
            "assignment_requirement_refs": [f"requirement-{ordinal}"],
            "evidence_refs": [f"evidence-{ordinal}"],
            "editorial_function": "DECISION_REASONING",
            "proposition": delta,
            "new_information_delta": delta,
            "importance": "CORE",
            "factual_risk": "LOW",
            "estimated_word_allocation": 15,
            "visualizability_hint": "Show the exact decision boundary.",
        }
        unit = InformationUnit(
            **unit_body,
            content_hash=cross_modal_hash(unit_body),
        )
        units.append(unit)
        section_body = {
            "section_id": f"section-{ordinal}",
            "ordinal": ordinal,
            "primary_requirement_ids": [f"requirement-{ordinal}"],
            "owned_information_unit_ids": [unit.information_unit_id],
            "permitted_callbacks": [],
            "word_min": 10,
            "word_max": 20,
            "section_delta": delta,
        }
        sections.append(
            SectionCoverage(
                **section_body,
                content_hash=cross_modal_hash(section_body),
            )
        )
    plan_body = {
        "schema_version": "vcos.section-coverage-plan.v1",
        "content_mode": "STANDALONE",
        "assignment_requirements_hash": "1" * 64,
        "evidence_pack_hash": "2" * 64,
        "information_units": [item.model_dump(mode="json") for item in units],
        "sections": [item.model_dump(mode="json") for item in sections],
        "target_word_count": 45,
    }
    return SectionCoveragePlan(
        **plan_body,
        content_hash=cross_modal_hash(plan_body),
    )


def test_authorship_contract_is_format_neutral_and_hash_bound() -> None:
    contract = _contract()

    assert contract.source_role == "EVIDENCE"
    assert contract.format_key == "future-format"
    assert contract.viewer_facing_presentation == ViewerFacingAuthorshipLaw.build()
    assert contract.model_validate(contract.model_dump(mode="json")) == contract

    tampered = contract.model_dump(mode="json")
    tampered["content_hash"] = "0" * 64
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_CONTRACT_HASH_MISMATCH"):
        EditorialAuthorshipContract.model_validate(tampered)


def test_hold_and_no_visual_change_are_valid_stable_outcomes() -> None:
    validate_viewer_facing_presentation(
        [
            {
                "outcome": "HOLD",
                "editorial_reason": "Stability keeps the comparison legible.",
                "editorial_authority": _contract().presentation_authority,
                "actual_presentation_change": False,
            },
            {
                "outcome": "NO_VISUAL_CHANGE",
                "editorial_reason": "The unchanged frame preserves comprehension.",
                "editorial_authority": _contract().presentation_authority,
                "actual_presentation_change": False,
            },
        ]
    )


def test_viewer_facing_change_requires_editorial_reason() -> None:
    with pytest.raises(ValueError, match="NO_EFFECT_WITHOUT_EDITORIAL_REASON"):
        validate_viewer_facing_presentation([{"outcome": "CHANGE"}])


def test_mechanical_trigger_cannot_be_presentation_authority() -> None:
    with pytest.raises(
        ValueError, match="MECHANICAL_PRESENTATION_TRIGGER_HAS_NO_AUTHORITY"
    ):
        validate_viewer_facing_presentation(
            {
                "outcome": "CHANGE",
                "trigger": "TIMER",
                "editorial_reason": "The scene was still.",
            }
        )

    validate_viewer_facing_presentation(
        {
            "outcome": "CHANGE",
            "trigger": "TIMER",
            "editorial_reason": "Reveal the new evidence boundary.",
            "editorial_authority": _contract().presentation_authority,
        }
    )


def test_hold_without_reason_is_not_authored_presentation() -> None:
    with pytest.raises(ValueError, match="NO_EFFECT_WITHOUT_EDITORIAL_REASON"):
        validate_viewer_facing_presentation({"outcome": "HOLD"})


def test_generic_interchangeable_authored_roles_are_blocked() -> None:
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_REASONING_NOT_DISTINCT"):
        EditorialAuthorshipContract.build(
            source_evidence_authorities=_contract().source_evidence_authorities,
            authored_authorities=_contract().authored_authorities,
            content_mode="STANDALONE",
            format_key="future-format",
            channel_promise="A durable promise",
            episode_reasoning="A distinct episode argument",
            central_question="Same",
            early_stakes_or_payoff="Same stakes",
            original_thesis_or_position="Same thesis",
            editorial_delta="Same",
            reasoning_or_narrative_spine="Same spine",
            progression="Same progression",
            tension_applicability="NOT_APPLICABLE",
            visible_editorial_judgment="Same",
            memorable_payoff_framework_or_conclusion="Same",
        )


def test_source_and_authored_authority_refs_cannot_overlap() -> None:
    values = _contract_values()
    values["source_evidence_authorities"] = [
        EditorialAuthorityBinding(
            authority_type=EditorialAuthorityType.SOURCE_EVIDENCE,
            authority_ref=values["authored_authorities"][0]["authority_ref"],
            content_hash="a" * 64,
        )
    ]
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_SOURCE_AUTHORITY_OVERLAP"):
        EditorialAuthorshipContract.build(**values)


def test_authored_authority_refs_cannot_be_padded_with_duplicates() -> None:
    values = _contract_values()
    values["authored_authorities"] = [
        values["authored_authorities"][0],
        values["authored_authorities"][1],
        values["authored_authorities"][1],
    ]
    with pytest.raises(
        ValueError, match="EDITORIAL_AUTHORSHIP_AUTHORITY_REF_DUPLICATE"
    ):
        EditorialAuthorshipContract.build(**values)


def test_same_channel_promise_and_episode_reasoning_are_blocked() -> None:
    values = _contract_values()
    values["episode_reasoning"] = values["channel_promise"]
    with pytest.raises(
        ValueError,
        match="EDITORIAL_AUTHORSHIP_CHANNEL_EPISODE_REASONING_NOT_DISTINCT",
    ):
        EditorialAuthorshipContract.build(**values)


def test_applicable_tension_requires_tradeoff() -> None:
    values = _contract_values()
    values["tension_applicability"] = "APPLICABLE"
    values["tension_failure_contradiction_or_tradeoff"] = None
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_TENSION_REQUIRED"):
        EditorialAuthorshipContract.build(**values)


def test_production_gate_requires_readiness_hash_binding() -> None:
    contract = _contract()
    snapshot = {
        "artifact_version": {
            "content": {
                "schema_version": "production.package.v2",
                "authority_classification": "CANONICAL_V2_AUTHORITY",
                "readiness_evidence": {
                    "authorship_contract_hash": contract.content_hash,
                },
                "editorial_authorship": contract.model_dump(mode="json"),
            }
        }
    }
    assert _production_editorial_authorship_gate(snapshot).result == "PASS"
    snapshot["artifact_version"]["content"]["readiness_evidence"][
        "authorship_contract_hash"
    ] = "0" * 64
    assert _production_editorial_authorship_gate(snapshot).result == "BLOCK"


def test_presentation_authority_must_be_typed_hash_bound_authorship() -> None:
    decision = {
        "outcome": "CHANGE",
        "editorial_reason": "Reveal an already-authored decision boundary.",
    }
    with pytest.raises(
        ValueError, match="EDITORIAL_PRESENTATION_AUTHORITY_BINDING_REQUIRED"
    ):
        validate_viewer_facing_presentation(
            {**decision, "editorial_authority_ref": "anything://non-empty"}
        )
    with pytest.raises(
        ValueError, match="EDITORIAL_PRESENTATION_AUTHORITY_BINDING_REQUIRED"
    ):
        validate_viewer_facing_presentation(
            {
                **decision,
                "editorial_authority": {
                    "authority_type": "SCENE_PLAN",
                    "authority_ref": "scene-plan://self-authored",
                    "content_hash": "f" * 64,
                },
            }
        )
    with pytest.raises(ValueError, match="SOURCE_CANNOT_AUTHOR_PRESENTATION"):
        validate_viewer_facing_presentation(
            {
                **decision,
                "editorial_authority": EditorialAuthorityBinding(
                    authority_type=EditorialAuthorityType.SOURCE_EVIDENCE,
                    authority_ref="evidence://source-1",
                    content_hash="a" * 64,
                ),
            }
        )

    validate_viewer_facing_presentation(
        {**decision, "editorial_authority": _contract().presentation_authority}
    )
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORITY_REF_HASH_MISMATCH"):
        EditorialAuthorityBinding(
            authority_type=EditorialAuthorityType.EDITORIAL_AUTHORSHIP_CONTRACT,
            authority_ref=f"editorial-authorship://{'a' * 64}",
            content_hash="b" * 64,
        )


def test_transitive_child_hash_drift_invalidates_old_contract_identity() -> None:
    contract = _contract()
    values = _contract_values()
    project_authority = values["authored_authorities"][-1]
    project_authority["content_hash"] = "f" * 64
    rebound = EditorialAuthorshipContract.build(**values)

    assert rebound.authored_authorities[-1].authority_ref == (
        contract.authored_authorities[-1].authority_ref
    )
    assert rebound.content_hash != contract.content_hash

    tampered = contract.model_dump(mode="json")
    tampered["authored_authorities"][-1]["content_hash"] = "f" * 64
    with pytest.raises(
        ValueError, match="EDITORIAL_AUTHORSHIP_CONTRACT_HASH_MISMATCH"
    ):
        EditorialAuthorshipContract.model_validate(tampered)


def test_legacy_contract_remains_readable_but_cannot_be_new_authority() -> None:
    from app.contracts.editorial_authorship import _semantic_hash

    body = _contract().model_dump(mode="json", exclude={"content_hash"})
    body.pop("source_evidence_authorities")
    body.pop("authored_authorities")
    body["source_evidence_refs"] = ["evidence://historical"]
    body["authored_authority_refs"] = [
        "editorial-proposal://historical",
        "editorial-specificity://historical",
        "topic-definition://historical",
    ]
    legacy = EditorialAuthorshipContract.model_validate(
        {**body, "content_hash": _semantic_hash(body)}
    )

    assert legacy.has_transitive_authority_binding is False
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_LEGACY_BUILD_FORBIDDEN"):
        EditorialAuthorshipContract.build(**body)


def test_stable_outcome_matches_runtime_and_has_no_duration_limit() -> None:
    authority = _contract().presentation_authority
    validate_viewer_facing_presentation(
        {
            "outcome": "HOLD",
            "editorial_reason": "Keep a long comparison stable for comprehension.",
            "editorial_authority": authority,
            "actual_presentation_change": False,
            "duration_ms": 120_000,
        }
    )
    with pytest.raises(
        ValueError, match="STABLE_PRESENTATION_OUTCOME_RUNTIME_MISMATCH"
    ):
        validate_viewer_facing_presentation(
            {
                "outcome": "HOLD",
                "editorial_reason": "Keep the comparison stable.",
                "editorial_authority": authority,
                "actual_presentation_change": True,
            }
        )


def test_weaker_proposal_fields_do_not_silently_gain_card_d_authority() -> None:
    historical = _proposal()

    assert historical.specific_audience_problem
    assert historical.proposed_angle
    assert historical.viewer_value
    assert historical.has_exact_card_d_authorship is False
    assert historical.early_stakes_or_payoff is None
    assert historical.original_thesis_or_position is None
    assert historical.memorable_payoff_framework_or_conclusion is None


def test_new_proposal_requires_complete_exact_card_d_values() -> None:
    with pytest.raises(ValueError, match="EDITORIAL_IDEA_CARD_D_AUTHORSHIP_INCOMPLETE"):
        _proposal(central_question="Which exact decision is at issue?")

    proposal = _proposal(
        episode_reasoning="Test the exception handoff as one decision boundary.",
        central_question="Where does the exception handoff lose authority?",
        early_stakes_or_payoff="An invisible handoff can ship an unreviewed action.",
        original_thesis_or_position="Put review at the external-action boundary.",
        visible_editorial_judgment="The external action is the decisive boundary.",
        memorable_payoff_framework_or_conclusion=(
            "Use the external-action test before every fallback."
        ),
    )

    assert proposal.has_exact_card_d_authorship is True
    assert set(proposal.required_evidence_binding_fields) > set(
        _proposal().required_evidence_binding_fields
    )


def test_generic_headings_cannot_become_reasoning_spine() -> None:
    plan = _coverage_plan(["Introduction", "Main section", "Conclusion"])

    with pytest.raises(
        ValidationFailureError,
        match="V2_EDITORIAL_AUTHORSHIP_SPINE_AUTHORITY_INVALID",
    ):
        _exact_reasoning_progression(plan)


def test_exact_section_coverage_semantic_progression_passes() -> None:
    deltas = [
        "Define the hidden approval handoff.",
        "Test the handoff against the external-action boundary.",
        "Apply the boundary test before enabling recovery.",
    ]
    plan = _coverage_plan(deltas)

    assert _exact_reasoning_progression(plan) == deltas


def test_new_production_package_missing_authorship_blocks() -> None:
    snapshot = {
        "artifact_version": {
            "content": {
                "schema_version": "production.package.v2",
                "authority_classification": "CANONICAL_V2_AUTHORITY",
                "readiness_evidence": {},
            }
        }
    }

    result = _production_editorial_authorship_gate(snapshot)

    assert result.result == "BLOCK"
    assert "EDITORIAL_AUTHORSHIP_CONTRACT_REQUIRED" in result.reason_codes


def test_historical_string_lineage_cannot_authorize_new_production() -> None:
    from app.contracts.editorial_authorship import _semantic_hash

    body = _contract().model_dump(mode="json", exclude={"content_hash"})
    body.pop("source_evidence_authorities")
    body.pop("authored_authorities")
    body["source_evidence_refs"] = ["evidence://historical"]
    body["authored_authority_refs"] = [
        "editorial-proposal://historical",
        "editorial-specificity://historical",
        "topic-definition://historical",
    ]
    legacy = EditorialAuthorshipContract.model_validate(
        {**body, "content_hash": _semantic_hash(body)}
    )
    snapshot = {
        "artifact_version": {
            "content": {
                "schema_version": "production.package.v2",
                "authority_classification": "CANONICAL_V2_AUTHORITY",
                "readiness_evidence": {
                    "authorship_contract_hash": legacy.content_hash,
                },
                "editorial_authorship": legacy.model_dump(mode="json"),
            }
        }
    }

    result = _production_editorial_authorship_gate(snapshot)

    assert result.result == "BLOCK"
    assert "EDITORIAL_AUTHORSHIP_TRANSITIVE_AUTHORITY_REQUIRED" in result.reason_codes
