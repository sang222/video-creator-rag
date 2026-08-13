"""Deterministic cross-modal compilers and typed QC policy.

No function here calls a provider.  The generative visual planner may choose
semantic scene data, but timing, ownership, hashes, asset-fit disposition and
high-confidence QC blocks remain deterministic application authority.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.contracts.cross_modal import (
    AssetFitObservation,
    CrossModalObservation,
    CrossModalQCReport,
    InformationUnit,
    NarrationUnit,
    NarrationUnitCompilation,
    SectionCoverage,
    SectionCoveragePlan,
    TimedNarrationBindingSet,
    TimedNarrationUnitBinding,
    VisualRealizationPlan,
    cross_modal_hash,
)
from app.contracts.script_qualification import QualifiedScriptOutputV2


COVERAGE_COMPILER_VERSION = "section-coverage-compiler.v1"
NARRATION_UNIT_COMPILER_VERSION = "narration-unit-compiler.v1"


class CrossModalContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _hash_body(payload: Mapping[str, Any], field: str = "content_hash") -> str:
    return cross_modal_hash(
        {key: value for key, value in payload.items() if key != field}
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value, flags=re.UNICODE))


def _require_hash(value: Any, code: str) -> str:
    value = str(value or "")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise CrossModalContractError(code)
    return value


class SectionCoverageCompiler:
    """Compile typed coverage from the frozen assignment, never prose parsing."""

    @classmethod
    def compile(
        cls,
        *,
        assignment: Mapping[str, Any],
        evidence_pack: Mapping[str, Any],
        runtime_contract: Mapping[str, Any],
    ) -> SectionCoveragePlan:
        requirements = [
            dict(item)
            for item in (assignment.get("required_requirement_units") or [])
            if isinstance(item, Mapping) and item.get("required") is True
        ]
        if not requirements or any(
            not _clean(item.get("requirement_id")) for item in requirements
        ):
            raise CrossModalContractError(
                "SECTION_COVERAGE_REQUIRED_ASSIGNMENT_UNITS_MISSING"
            )
        ids = [str(item["requirement_id"]) for item in requirements]
        if len(ids) != len(set(ids)):
            raise CrossModalContractError("SECTION_COVERAGE_REQUIREMENT_ID_DUPLICATE")
        content_mode = str(assignment.get("content_mode") or "")
        if content_mode not in {"STANDALONE", "SERIES_EPISODE"}:
            raise CrossModalContractError("SECTION_COVERAGE_CONTENT_MODE_INVALID")
        evidence_refs = [
            str(item.get("evidence_span_id"))
            for item in (evidence_pack.get("spans") or [])
            if isinstance(item, Mapping) and _clean(item.get("evidence_span_id"))
        ]
        if not evidence_refs:
            raise CrossModalContractError("SECTION_COVERAGE_EVIDENCE_MISSING")
        requirements_hash = cross_modal_hash(requirements)
        # The plan pins exact evidence span identities, not the enclosing
        # evidence-pack hash.  This avoids a circular hash dependency when the
        # immutable assignment stores this plan and the evidence pack records
        # the final assignment hash.
        evidence_hash = cross_modal_hash(sorted(evidence_refs))
        duration = runtime_contract.get("duration_contract") or {}
        target_ms = int(duration.get("target_duration_ms") or 0)
        wpm = int(runtime_contract.get("duration_estimation_wpm") or 0)
        if target_ms <= 0 or wpm <= 0:
            raise CrossModalContractError("SECTION_COVERAGE_RUNTIME_CONTRACT_INVALID")
        target_words = max(1, round(target_ms * wpm / 60_000))
        unit_models: list[InformationUnit] = []
        for index, requirement in enumerate(requirements, start=1):
            requirement_id = str(requirement["requirement_id"])
            proposition = _clean(requirement.get("obligation"))
            if not proposition:
                raise CrossModalContractError(
                    "SECTION_COVERAGE_REQUIREMENT_OBLIGATION_MISSING"
                )
            fields = {
                "information_unit_id": f"iu-{index:03d}",
                "assignment_requirement_refs": [requirement_id],
                "evidence_refs": evidence_refs,
                "editorial_function": str(
                    requirement.get("requirement_type") or "ASSIGNMENT_FULFILLMENT"
                ),
                "proposition": proposition,
                "new_information_delta": f"Fulfill {requirement_id} without restating an earlier unit.",
                "importance": "CORE",
                "factual_risk": "HIGH"
                if "scope" in requirement_id
                or requirement_id in {"subject", "question"}
                else "MEDIUM",
                "estimated_word_allocation": max(1, target_words // len(requirements)),
                "visualizability_hint": cls._visualizability_hint(requirement_id),
            }
            unit_models.append(
                InformationUnit(**fields, content_hash=cross_modal_hash(fields))
            )
        section_count = min(5, max(3, math.ceil(len(unit_models) / 3)))
        grouped: list[list[InformationUnit]] = [[] for _ in range(section_count)]
        for index, unit in enumerate(unit_models):
            grouped[
                min(section_count - 1, index * section_count // len(unit_models))
            ].append(unit)
        sections: list[SectionCoverage] = []
        for ordinal, units in enumerate(grouped, start=1):
            if not units:
                raise CrossModalContractError("SECTION_COVERAGE_EMPTY_SECTION")
            allocation = max(
                len(units), round(target_words * len(units) / len(unit_models))
            )
            body = {
                "section_id": f"section-{ordinal:03d}",
                "ordinal": ordinal,
                "primary_requirement_ids": [
                    ref for unit in units for ref in unit.assignment_requirement_refs
                ],
                "owned_information_unit_ids": [
                    unit.information_unit_id for unit in units
                ],
                "permitted_callbacks": [],
                "word_min": max(1, math.floor(allocation * 0.75)),
                "word_max": max(1, math.ceil(allocation * 1.30)),
                "section_delta": " → ".join(
                    unit.new_information_delta for unit in units
                ),
            }
            sections.append(
                SectionCoverage(**body, content_hash=cross_modal_hash(body))
            )
        payload = {
            "schema_version": "vcos.section-coverage-plan.v1",
            "content_mode": content_mode,
            "assignment_requirements_hash": requirements_hash,
            "evidence_pack_hash": evidence_hash,
            "information_units": [item.model_dump(mode="json") for item in unit_models],
            "sections": [item.model_dump(mode="json") for item in sections],
            "target_word_count": target_words,
        }
        return SectionCoveragePlan(**payload, content_hash=cross_modal_hash(payload))

    @staticmethod
    def _visualizability_hint(requirement_id: str) -> str:
        if requirement_id in {"question", "viewer-action", "episode-delta"}:
            return "PROCESS_OR_DECISION_MODEL"
        if "scope" in requirement_id:
            return "BOUNDARY_COMPARISON"
        if requirement_id in {"subject", "accepted-angle"}:
            return "AUTHENTIC_EVIDENCE_CONTEXT"
        return "EXPLANATORY_CONTEXT"

    @staticmethod
    def validate_writer_output(
        output: QualifiedScriptOutputV2,
        plan: SectionCoveragePlan,
    ) -> None:
        by_id = {item.section_id: item for item in output.sections}
        if len(by_id) != len(output.sections) or set(by_id) != {
            item.section_id for item in plan.sections
        }:
            raise CrossModalContractError(
                "SCRIPT_SECTION_COVERAGE_SECTION_SET_MISMATCH"
            )
        for coverage in plan.sections:
            section = by_id[coverage.section_id]
            if section.ordinal != coverage.ordinal:
                raise CrossModalContractError(
                    "SCRIPT_SECTION_COVERAGE_ORDINAL_MISMATCH"
                )
            if (
                section.required_assignment_unit_refs
                != coverage.primary_requirement_ids
            ):
                raise CrossModalContractError(
                    "SCRIPT_SECTION_COVERAGE_REQUIREMENT_OWNERSHIP_VIOLATION"
                )


class NarrationUnitCompiler:
    """Split qualified text into deterministic meaning units, not caption cues."""

    @classmethod
    def compile(
        cls,
        *,
        output: QualifiedScriptOutputV2,
        canonical_script: str,
        canonical_script_hash: str,
        coverage_plan: SectionCoveragePlan,
        estimated_duration_ms: int,
    ) -> NarrationUnitCompilation:
        if estimated_duration_ms <= 0:
            raise CrossModalContractError("NARRATION_UNIT_DURATION_INVALID")
        _require_hash(canonical_script_hash, "NARRATION_UNIT_SCRIPT_HASH_INVALID")
        SectionCoverageCompiler.validate_writer_output(output, coverage_plan)
        sections = sorted(output.sections, key=lambda item: item.ordinal)
        coverage_by_id = {item.section_id: item for item in coverage_plan.sections}
        info_by_id = {
            item.information_unit_id: item for item in coverage_plan.information_units
        }
        units: list[NarrationUnit] = []
        cursor = 0
        ordinal = 1
        total_words = max(1, _word_count(canonical_script))
        for section in sections:
            narration = section.narration.strip()
            start = canonical_script.find(narration, cursor)
            if start < 0:
                raise CrossModalContractError(
                    "NARRATION_UNIT_CANONICAL_SECTION_SPAN_MISSING"
                )
            end = start + len(narration)
            if canonical_script[start:end] != narration:
                raise CrossModalContractError(
                    "NARRATION_UNIT_CANONICAL_SECTION_SPAN_INVALID"
                )
            coverage = coverage_by_id[section.section_id]
            chunks = cls._meaning_chunks(narration)
            chunk_cursor = start
            owned_information = [
                info_by_id[item] for item in coverage.owned_information_unit_ids
            ]
            for chunk_index, chunk in enumerate(chunks):
                found = canonical_script.find(chunk, chunk_cursor, end)
                if found < 0:
                    raise CrossModalContractError("NARRATION_UNIT_SOURCE_COVERAGE_GAP")
                chunk_end = found + len(chunk)
                word_count = max(1, _word_count(chunk))
                info = owned_information[min(chunk_index, len(owned_information) - 1)]
                body = {
                    "narration_unit_id": f"nu-{ordinal:03d}",
                    "section_id": section.section_id,
                    "ordinal": ordinal,
                    "source_text_span": {"start": found, "end": chunk_end},
                    "source_text_hash": hashlib_sha256(chunk),
                    "text": chunk,
                    "information_unit_ids": [info.information_unit_id],
                    "assignment_requirement_ids": list(
                        info.assignment_requirement_refs
                    ),
                    "evidence_span_ids": list(info.evidence_refs),
                    "semantic_intent": info.proposition,
                    "visual_function": info.visualizability_hint,
                    "importance": info.importance,
                    "factual_risk": info.factual_risk,
                    "estimated_spoken_duration_ms": max(
                        1, round(estimated_duration_ms * word_count / total_words)
                    ),
                }
                units.append(NarrationUnit(**body, content_hash=cross_modal_hash(body)))
                ordinal += 1
                chunk_cursor = chunk_end
            cursor = end
        cls._validate_non_whitespace_coverage(canonical_script, units)
        payload = {
            "schema_version": "vcos.narration-unit-compilation.v1",
            "canonical_script_hash": canonical_script_hash,
            "coverage_plan_hash": coverage_plan.content_hash,
            "narration_units": [item.model_dump(mode="json") for item in units],
        }
        return NarrationUnitCompilation(
            **payload, content_hash=cross_modal_hash(payload)
        )

    @staticmethod
    def _meaning_chunks(narration: str) -> list[str]:
        sentence_spans: list[tuple[int, int]] = []
        for match in re.finditer(
            r"""[^.!?]+(?:[.!?]+["'\u201d\u2019\u00bb)\]]*|$)""",
            narration,
            flags=re.S,
        ):
            raw = match.group(0)
            stripped = raw.strip()
            if not stripped:
                continue
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            sentence_spans.append((match.start() + leading, match.end() - trailing))
        if not sentence_spans:
            raise CrossModalContractError("NARRATION_UNIT_SENTENCE_EXTRACTION_EMPTY")
        chunk_spans: list[tuple[int, int]] = []
        pending: list[tuple[int, int]] = []
        words = 0
        for sentence_span in sentence_spans:
            sentence_words = _word_count(narration[sentence_span[0] : sentence_span[1]])
            pending.append(sentence_span)
            words += sentence_words
            # A unit normally carries at least two sentences or a material
            # amount of speech.  A single-sentence section remains one unit
            # because splitting it would make a caption-sized pseudo-unit.
            if (len(pending) >= 2 and words >= 18) or words >= 56:
                chunk_spans.append((pending[0][0], pending[-1][1]))
                pending, words = [], 0
        if pending:
            pending_text = narration[pending[0][0] : pending[-1][1]]
            if chunk_spans and len(pending) == 1 and _word_count(pending_text) < 18:
                chunk_spans[-1] = (chunk_spans[-1][0], pending[-1][1])
            else:
                chunk_spans.append((pending[0][0], pending[-1][1]))
        # Slice the exact original section so paragraph/newline separators are
        # preserved.  Rejoining normalized sentences with a literal space
        # changes the canonical bytes and makes the subsequent source-span
        # lookup fail for live multi-paragraph scripts.
        return [narration[start:end] for start, end in chunk_spans]

    @staticmethod
    def _validate_non_whitespace_coverage(
        script: str, units: list[NarrationUnit]
    ) -> None:
        covered: set[int] = set()
        for unit in units:
            covered.update(
                range(unit.source_text_span.start, unit.source_text_span.end)
            )
        expected = {
            index for index, character in enumerate(script) if not character.isspace()
        }
        if covered & {
            index for index, character in enumerate(script) if character.isspace()
        }:
            # Whitespace inside a text span is expected; this branch exists
            # only to keep the intention explicit for future span refactors.
            pass
        if not expected.issubset(covered):
            raise CrossModalContractError("NARRATION_UNIT_SOURCE_COVERAGE_GAP")


def hashlib_sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bind_timed_narration_units(
    *,
    compilation: NarrationUnitCompilation,
    spoken_text_hash: str,
    timed_words: Iterable[Mapping[str, Any]],
    alignment_evidence_ref: str,
    alignment_confidence: float = 1.0,
) -> TimedNarrationBindingSet:
    """Bind provider verified words to existing units without re-timing scenes."""

    _require_hash(spoken_text_hash, "NARRATION_UNIT_SPOKEN_TEXT_HASH_INVALID")
    words = [dict(item) for item in timed_words]
    if not words:
        raise CrossModalContractError("NARRATION_UNIT_TIMED_WORDS_MISSING")
    previous_start = -1
    previous_end = -1
    previous_index = -1
    for ordinal, word in enumerate(words, start=1):
        token = _clean(word.get("text"))
        try:
            start = int(word["start_ms"])
            end = int(word["end_ms"])
            word_index = int(word.get("index", ordinal))
        except (KeyError, TypeError, ValueError) as exc:
            raise CrossModalContractError(
                "NARRATION_UNIT_TIMED_WORD_FORMAT_INVALID"
            ) from exc
        if (
            not token
            or start < 0
            or end <= start
            or start < previous_start
            or end < previous_end
            or word_index <= previous_index
        ):
            raise CrossModalContractError("NARRATION_UNIT_TIMED_WORD_ORDER_INVALID")
        previous_start, previous_end, previous_index = start, end, word_index
    expected_tokens = [
        token
        for unit in compilation.narration_units
        for token in re.findall(r"\S+", unit.text, flags=re.UNICODE)
    ]
    actual_tokens = [str(item.get("text") or "") for item in words]
    if len(expected_tokens) != len(actual_tokens) or [
        _normalize_token(item) for item in expected_tokens
    ] != [_normalize_token(item) for item in actual_tokens]:
        raise CrossModalContractError("NARRATION_UNIT_ALIGNMENT_TOKEN_MISMATCH")
    bindings: list[TimedNarrationUnitBinding] = []
    cursor = 0
    for unit in compilation.narration_units:
        count = len(re.findall(r"\S+", unit.text, flags=re.UNICODE))
        matched = words[cursor : cursor + count]
        start, end = int(matched[0]["start_ms"]), int(matched[-1]["end_ms"])
        if end <= start or any(
            int(item["end_ms"]) <= int(item["start_ms"]) for item in matched
        ):
            raise CrossModalContractError("NARRATION_UNIT_ALIGNMENT_TIMING_INVALID")
        refs = [
            f"timed-word:{int(item.get('index', cursor + position + 1))}"
            for position, item in enumerate(matched)
        ]
        body = {
            "narration_unit_id": unit.narration_unit_id,
            "spoken_text_hash": spoken_text_hash,
            "spoken_token_refs": [
                f"spoken-token:{cursor + position + 1}" for position in range(count)
            ],
            "verified_word_refs": refs,
            "actual_start_ms": start,
            "actual_end_ms": end,
            "alignment_confidence": alignment_confidence,
            "alignment_evidence_ref": alignment_evidence_ref,
        }
        alignment_hash = cross_modal_hash(body)
        bindings.append(
            TimedNarrationUnitBinding(
                **body,
                alignment_hash=alignment_hash,
                content_hash=cross_modal_hash(
                    {**body, "alignment_hash": alignment_hash}
                ),
            )
        )
        cursor += count
    payload = {
        "schema_version": "vcos.timed-narration-unit-binding.v1",
        "narration_unit_compilation_hash": compilation.content_hash,
        "spoken_text_hash": spoken_text_hash,
        "bindings": [item.model_dump(mode="json") for item in bindings],
    }
    return TimedNarrationBindingSet(**payload, content_hash=cross_modal_hash(payload))


def validate_visual_realization_plan(
    *,
    plan: VisualRealizationPlan,
    bindings: TimedNarrationBindingSet,
) -> None:
    binding_by_unit = {item.narration_unit_id: item for item in bindings.bindings}
    seen: set[str] = set()
    for scene in sorted(plan.scenes, key=lambda item: item.ordinal):
        if seen.intersection(scene.narration_unit_ids):
            raise CrossModalContractError("VISUAL_REALIZATION_NARRATION_UNIT_DUPLICATE")
        seen.update(scene.narration_unit_ids)
        selected = [binding_by_unit.get(item) for item in scene.narration_unit_ids]
        if any(item is None for item in selected):
            raise CrossModalContractError("VISUAL_REALIZATION_NARRATION_UNIT_UNKNOWN")
        assert selected
        if (
            scene.actual_start_ms != selected[0].actual_start_ms
            or scene.actual_end_ms != selected[-1].actual_end_ms
        ):
            raise CrossModalContractError(
                "VISUAL_REALIZATION_TIMING_NOT_NARRATION_BOUND"
            )
    if seen != set(binding_by_unit):
        raise CrossModalContractError("VISUAL_REALIZATION_NARRATION_UNIT_COVERAGE_GAP")


def asset_fit_observation(
    *,
    scene_id: str,
    asset_ref: str,
    components: Mapping[str, str],
    evidence_refs: Iterable[str] = (),
    representative_still_refs: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> AssetFitObservation:
    required = {
        "subject_match",
        "action_match",
        "context_match",
        "factual_match",
        "composition_match",
        "timing_fit",
        "style_compatibility",
        "visual_redundancy",
    }
    if set(components) != required or any(
        value not in {"PASS", "WARN", "BLOCK"} for value in components.values()
    ):
        raise CrossModalContractError("ASSET_FIT_COMPONENTS_INVALID")
    disposition = (
        "BLOCK"
        if "BLOCK" in components.values()
        else ("WARN" if "WARN" in components.values() else "PASS")
    )
    body = {
        "scene_id": scene_id,
        "asset_ref": asset_ref,
        **dict(components),
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
        "representative_still_refs": list(dict.fromkeys(representative_still_refs)),
        "disposition": disposition,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }
    return AssetFitObservation(**body, content_hash=cross_modal_hash(body))


_HARD_BLOCK_CODES = {
    "NARRATION_VISUAL_DIRECT_MISMATCH",
    "VISUAL_EVIDENCE_AUTHENTICITY_FAILURE",
    "MISSING_NARRATION_UNIT_COVERAGE",
    "MATERIAL_TIMING_MISMATCH",
    "ESSENTIAL_VISUAL_UNREADABLE",
    "TECHNICAL_MEDIA_FAILURE",
    "UNEXPECTED_SUBTITLE_STREAM",
    "NARRATION_BURN_IN_REGRESSION",
}


def cross_modal_qc_report(
    *,
    canonical_timeline_hash: str,
    visual_realization_plan_hash: str,
    observations: Iterable[CrossModalObservation],
) -> CrossModalQCReport:
    _require_hash(canonical_timeline_hash, "CROSS_MODAL_TIMELINE_HASH_INVALID")
    _require_hash(visual_realization_plan_hash, "CROSS_MODAL_VISUAL_PLAN_HASH_INVALID")
    typed = list(observations)
    if any(
        item.reason_code in _HARD_BLOCK_CODES and item.severity != "BLOCK"
        for item in typed
    ):
        raise CrossModalContractError("CROSS_MODAL_HARD_FAILURE_NOT_BLOCKED")
    disposition = (
        "BLOCK"
        if any(item.severity == "BLOCK" for item in typed)
        else ("WARN" if any(item.severity == "WARN" for item in typed) else "PASS")
    )
    payload = {
        "schema_version": "vcos.cross-modal-qc.v1",
        "canonical_timeline_hash": canonical_timeline_hash,
        "visual_realization_plan_hash": visual_realization_plan_hash,
        "observations": [item.model_dump(mode="json") for item in typed],
        "deterministic_disposition": disposition,
    }
    return CrossModalQCReport(**payload, content_hash=cross_modal_hash(payload))


def _normalize_token(value: str) -> str:
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()
