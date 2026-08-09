from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import ValidationFailureError
from app.services.editorial_specificity import (
    REQUIRED_EVIDENCE_BINDING_FIELDS,
    EditorialIdeaSynthesisService,
    EditorialSpecificityService,
    FrozenEvidenceSource,
    derive_frozen_evidence_spans,
    frozen_quote_is_valid,
    normalize_frozen_evidence_text,
)
from tests.test_editorial_evidence_binding_contract import (
    _EVIDENCE_ID,
    _SOURCE_CLASS_BY_ID,
    _proposal,
)


def _frozen_sources(excerpt: str) -> dict[str, FrozenEvidenceSource]:
    return {
        evidence_id: FrozenEvidenceSource(
            canonical_url=f"https://docs.example.test/{index}",
            content_hash=f"hash-{index}",
            content_excerpt=excerpt,
        )
        for index, evidence_id in enumerate(_SOURCE_CLASS_BY_ID, start=1)
    }


def _evidence(excerpt: str) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=evidence_id,
            metadata_={
                "editorial_fresh_evidence": {
                    "source_snapshot": {"content_excerpt": excerpt}
                }
            },
        )
        for evidence_id in _SOURCE_CLASS_BY_ID
    ]


def test_normalization_only_quote_is_accepted_by_synthesis_and_specificity() -> None:
    quote = "Structured outputs is ideal for: Data extraction"
    excerpt = "Using\u00a0structured outputs is ideal for: Data extraction"
    proposal = _proposal(bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS))
    for binding in proposal.evidence_bindings:
        binding["quoted_text"] = quote

    assert quote not in excerpt
    assert normalize_frozen_evidence_text(quote) in normalize_frozen_evidence_text(excerpt)
    assert frozen_quote_is_valid(quoted_text=quote, frozen_excerpt=excerpt)

    EditorialIdeaSynthesisService._validate_provider_proposal(
        proposal=proposal,
        source_class_by_id=_SOURCE_CLASS_BY_ID,
        frozen_source_by_id=_frozen_sources(excerpt),
        frozen_span_by_id={},
        expected_mode="STANDALONE",
        expected_series_binding=None,
    )

    reasons: list[str] = []
    EditorialSpecificityService._evidence_reasons(
        proposal=proposal,
        evidence=_evidence(excerpt),
        reasons=reasons,
    )
    assert reasons == []


def test_paraphrased_mechanism_quote_is_rejected_at_synthesis_boundary() -> None:
    quote = "function calling lets models connect to external tools"
    excerpt = "function calling lets models connect to external tools"
    proposal = _proposal(bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS))
    for binding in proposal.evidence_bindings:
        binding["quoted_text"] = quote
    next(
        binding
        for binding in proposal.evidence_bindings
        if binding["field"] == "specific_mechanism_or_use_case"
    )["quoted_text"] = "function calling lets models safely connect to external tools"

    with pytest.raises(
        ValidationFailureError,
        match="EDITORIAL_IDEA_SYNTHESIS_BINDING_QUOTE_NOT_IN_FROZEN_EVIDENCE",
    ):
        EditorialIdeaSynthesisService._validate_provider_proposal(
            proposal=proposal,
            source_class_by_id=_SOURCE_CLASS_BY_ID,
            frozen_source_by_id=_frozen_sources(excerpt),
            frozen_span_by_id={},
            expected_mode="STANDALONE",
            expected_series_binding=None,
        )


def test_spacing_around_punctuation_is_not_fuzzy_matched() -> None:
    quote = "extract fields using object, array, string, and integer"
    excerpt = "extract fields using object , array , string , and integer ."

    assert not frozen_quote_is_valid(quoted_text=quote, frozen_excerpt=excerpt)


def test_unknown_binding_field_is_rejected_before_coverage_is_counted() -> None:
    proposal = _proposal(bound_fields=set(REQUIRED_EVIDENCE_BINDING_FIELDS))
    proposal.evidence_bindings.append(
        {
            "field": "unsupported_field",
            "evidence_id": _EVIDENCE_ID,
            "quoted_text": proposal.evidence_bindings[0]["quoted_text"],
        }
    )

    with pytest.raises(
        ValidationFailureError,
        match="EDITORIAL_IDEA_SYNTHESIS_BINDING_FIELD_INVALID",
    ):
        EditorialIdeaSynthesisService._validate_provider_proposal(
            proposal=proposal,
            source_class_by_id=_SOURCE_CLASS_BY_ID,
            frozen_source_by_id=_frozen_sources(
                proposal.evidence_bindings[0]["quoted_text"]
            ),
            frozen_span_by_id={},
            expected_mode="STANDALONE",
            expected_series_binding=None,
        )


def test_provider_selects_server_owned_span_ids_without_authoring_quotes() -> None:
    excerpt = "Function calling lets models connect to external tools."
    span = derive_frozen_evidence_spans(
        evidence_id=_EVIDENCE_ID,
        canonical_url="https://docs.example.test/function-calling",
        source_content_hash="source-hash",
        content_excerpt=excerpt,
    )[0]
    provider_bindings = [
        {"field": field, "supporting_span_ids": [span.span_id]}
        for field in sorted(REQUIRED_EVIDENCE_BINDING_FIELDS)
    ]

    resolved = EditorialIdeaSynthesisService._resolve_provider_span_bindings(
        bindings=provider_bindings,
        frozen_span_by_id={span.span_id: span},
    )

    assert {binding["field"] for binding in resolved} == REQUIRED_EVIDENCE_BINDING_FIELDS
    assert {binding["quoted_text"] for binding in resolved} == {span.exact_text}
    assert {binding["evidence_id"] for binding in resolved} == {_EVIDENCE_ID}


def test_unknown_provider_span_id_is_rejected_before_candidate_creation() -> None:
    with pytest.raises(
        ValidationFailureError,
        match="EDITORIAL_IDEA_SYNTHESIS_BINDING_SPAN_NOT_IN_FROZEN_EVIDENCE",
    ):
        EditorialIdeaSynthesisService._resolve_provider_span_bindings(
            bindings=[
                {
                    "field": "specific_mechanism_or_use_case",
                    "supporting_span_ids": ["unknown-span-id"],
                }
            ],
            frozen_span_by_id={},
        )
