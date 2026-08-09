from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import ValidationFailureError
from app.services.editorial_specificity import (
    REQUIRED_EVIDENCE_BINDING_FIELDS,
    EditorialIdeaProposal,
    EditorialIdeaSynthesisService,
    EditorialSpecificityService,
)


_EVIDENCE_ID = "11111111-1111-1111-1111-111111111111"
_SUPPORTING_EVIDENCE_ID = "22222222-2222-2222-2222-222222222222"
_THIRD_EVIDENCE_ID = "33333333-3333-3333-3333-333333333333"
_UNKNOWN_EVIDENCE_ID = "44444444-4444-4444-4444-444444444444"
_QUOTE = "The model can call a function when the workflow requires an external action."
_SOURCE_CLASS_BY_ID = {
    _EVIDENCE_ID: "NARROW_TOPIC_CAPABLE",
    _SUPPORTING_EVIDENCE_ID: "NARROW_TOPIC_CAPABLE",
    _THIRD_EVIDENCE_ID: "NARROW_TOPIC_CAPABLE",
}


def _proposal(*, bound_fields: set[str]) -> EditorialIdeaProposal:
    return EditorialIdeaProposal.model_validate(
        {
            "proposed_title": "When Should an AI Workflow Call a Tool?",
            "proposed_angle": "Map one external action to a documented function call.",
            "specific_audience_problem": "A small team needs a reliable external action.",
            "central_question_or_thesis": "When does a workflow need a function call?",
            "learning_outcome": "Viewers can identify the documented action boundary.",
            "viewer_value": "A concrete decision rule for one workflow step.",
            "editorial_delta": "Turns the documented capability into a bounded decision.",
            "specific_mechanism_or_use_case": "Use a function call for the documented external action.",
            "decision_value": "Teams can decide whether one step needs a tool.",
            "scope_inclusions": ["Documented function calling"],
            "scope_exclusions": ["Unsupported performance claims"],
            "primary_evidence_refs": [
                {"id": _EVIDENCE_ID, "ref": "https://docs.example.test/function-calling"}
            ],
            "supporting_evidence_refs": [
                {"id": _SUPPORTING_EVIDENCE_ID, "ref": "https://docs.example.test/evaluators"},
                {"id": _THIRD_EVIDENCE_ID, "ref": "https://docs.example.test/workflow"},
            ],
            "evidence_bindings": [
                {"field": field, "evidence_id": _EVIDENCE_ID, "quoted_text": _QUOTE}
                for field in sorted(bound_fields)
            ],
            "source_specificity_class": "NARROW_TOPIC_CAPABLE",
            "content_mode": "STANDALONE",
        }
    )


def test_synthesis_rejects_incomplete_required_binding_coverage() -> None:
    proposal = _proposal(
        bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS) - {"proposed_title"}
    )

    with pytest.raises(
        ValidationFailureError,
        match="EDITORIAL_IDEA_SYNTHESIS_BINDING_COVERAGE_INCOMPLETE",
    ):
        EditorialIdeaSynthesisService._validate_provider_proposal(
            proposal=proposal,
            source_class_by_id=_SOURCE_CLASS_BY_ID,
            expected_mode="STANDALONE",
            expected_series_binding=None,
        )


def test_generated_title_is_traceable_without_being_a_verbatim_source_title() -> None:
    proposal = _proposal(bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS))

    EditorialIdeaSynthesisService._validate_provider_proposal(
        proposal=proposal,
        source_class_by_id=_SOURCE_CLASS_BY_ID,
        expected_mode="STANDALONE",
        expected_series_binding=None,
    )


def test_synthesis_rejects_a_binding_outside_the_frozen_source_pack() -> None:
    proposal = _proposal(bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS))
    proposal.evidence_bindings[0]["evidence_id"] = _UNKNOWN_EVIDENCE_ID

    with pytest.raises(
        ValidationFailureError,
        match="EDITORIAL_IDEA_SYNTHESIS_BINDING_INVALID",
    ):
        EditorialIdeaSynthesisService._validate_provider_proposal(
            proposal=proposal,
            source_class_by_id=_SOURCE_CLASS_BY_ID,
            expected_mode="STANDALONE",
            expected_series_binding=None,
        )


def test_specificity_keeps_rejecting_a_quote_absent_from_frozen_evidence() -> None:
    proposal = _proposal(bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS))
    evidence = [
        SimpleNamespace(
            id=_EVIDENCE_ID,
            metadata_={
                "editorial_fresh_evidence": {
                    "source_snapshot": {"content_excerpt": "A different retained span."}
                }
            },
        ),
        SimpleNamespace(
            id=_SUPPORTING_EVIDENCE_ID,
            metadata_={
                "editorial_fresh_evidence": {
                    "source_snapshot": {"content_excerpt": _QUOTE}
                }
            },
        ),
        SimpleNamespace(
            id=_THIRD_EVIDENCE_ID,
            metadata_={
                "editorial_fresh_evidence": {
                    "source_snapshot": {"content_excerpt": _QUOTE}
                }
            },
        ),
    ]
    reasons: list[str] = []

    EditorialSpecificityService._evidence_reasons(
        proposal=proposal,
        evidence=evidence,
        reasons=reasons,
    )

    assert reasons == ["EDITORIAL_PROPOSAL_EVIDENCE_INSUFFICIENT"]
