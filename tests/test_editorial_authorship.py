from __future__ import annotations

import pytest

from app.contracts.editorial_authorship import (
    EditorialAuthorshipContract,
    ViewerFacingAuthorshipLaw,
    validate_viewer_facing_presentation,
)
from app.services.gates import _production_editorial_authorship_gate


def _contract() -> EditorialAuthorshipContract:
    return EditorialAuthorshipContract.build(
        source_evidence_refs=["evidence://source-1"],
        authored_authority_refs=[
            "editorial-proposal://proposal-1",
            "editorial-specificity://receipt-1",
            "topic-definition://topic-1",
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
                "editorial_authority_ref": "scene-plan://hold-1",
            },
            {
                "outcome": "NO_VISUAL_CHANGE",
                "editorial_reason": "The unchanged frame preserves comprehension.",
                "editorial_authority_ref": "scene-plan://hold-2",
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
            "editorial_authority_ref": "scene-plan://1",
        }
    )


def test_hold_without_reason_is_not_authored_presentation() -> None:
    with pytest.raises(ValueError, match="NO_EFFECT_WITHOUT_EDITORIAL_REASON"):
        validate_viewer_facing_presentation({"outcome": "HOLD"})


def test_generic_interchangeable_authored_roles_are_blocked() -> None:
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_REASONING_NOT_DISTINCT"):
        EditorialAuthorshipContract.build(
            source_evidence_refs=["evidence://source-1"],
            authored_authority_refs=[
                "editorial-proposal://proposal-1",
                "editorial-specificity://receipt-1",
                "topic-definition://topic-1",
            ],
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
    values = _contract().model_dump(mode="json")
    values["viewer_facing_presentation"] = ViewerFacingAuthorshipLaw.build()
    values["authored_authority_refs"] = [
        "evidence://source-1",
        "editorial-specificity://receipt-1",
        "topic-definition://topic-1",
    ]
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_SOURCE_AUTHORITY_OVERLAP"):
        EditorialAuthorshipContract.build(**values)


def test_authored_authority_refs_cannot_be_padded_with_duplicates() -> None:
    values = _contract().model_dump(mode="json")
    values["viewer_facing_presentation"] = ViewerFacingAuthorshipLaw.build()
    values["authored_authority_refs"] = [
        "editorial-proposal://proposal-1",
        "editorial-specificity://receipt-1",
        "editorial-specificity://receipt-1",
    ]
    with pytest.raises(
        ValueError, match="EDITORIAL_AUTHORSHIP_AUTHORITY_REF_DUPLICATE"
    ):
        EditorialAuthorshipContract.build(**values)


def test_same_channel_promise_and_episode_reasoning_are_blocked() -> None:
    values = _contract().model_dump(mode="json")
    values["viewer_facing_presentation"] = ViewerFacingAuthorshipLaw.build()
    values["episode_reasoning"] = values["channel_promise"]
    with pytest.raises(
        ValueError,
        match="EDITORIAL_AUTHORSHIP_CHANNEL_EPISODE_REASONING_NOT_DISTINCT",
    ):
        EditorialAuthorshipContract.build(**values)


def test_applicable_tension_requires_tradeoff() -> None:
    values = _contract().model_dump(mode="json")
    values["viewer_facing_presentation"] = ViewerFacingAuthorshipLaw.build()
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
