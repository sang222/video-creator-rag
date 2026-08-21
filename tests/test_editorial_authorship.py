from __future__ import annotations

import pytest

from app.contracts.editorial_authorship import (
    EditorialAuthorshipContract,
    ViewerFacingAuthorshipLaw,
    validate_viewer_facing_presentation,
)


def _contract() -> EditorialAuthorshipContract:
    return EditorialAuthorshipContract.build(
        source_evidence_refs=["evidence://source-1"],
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


def test_hold_and_no_visual_change_are_valid_stable_outcomes() -> None:
    validate_viewer_facing_presentation(
        [{"outcome": "HOLD"}, {"outcome": "NO_VISUAL_CHANGE"}]
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
