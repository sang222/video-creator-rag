"""Typed lineage contracts from qualified narration to final visual truth.

These contracts extend the existing canonical media timeline.  They do not
create an editable second timeline or grant any publishing authority.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def cross_modal_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InformationUnit(_Strict):
    information_unit_id: str = Field(min_length=1)
    assignment_requirement_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    editorial_function: str = Field(min_length=1)
    proposition: str = Field(min_length=1)
    new_information_delta: str = Field(min_length=1)
    importance: Literal["CORE", "SUPPORTING"]
    factual_risk: Literal["LOW", "MEDIUM", "HIGH"]
    estimated_word_allocation: int = Field(gt=0)
    visualizability_hint: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "InformationUnit":
        if (
            len(self.assignment_requirement_refs) != len(set(self.assignment_requirement_refs))
            or len(self.evidence_refs) != len(set(self.evidence_refs))
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("INFORMATION_UNIT_INTEGRITY_INVALID")
        return self


class SectionCoverage(_Strict):
    section_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    primary_requirement_ids: list[str] = Field(min_length=1)
    owned_information_unit_ids: list[str] = Field(min_length=1)
    permitted_callbacks: list[str] = Field(default_factory=list)
    word_min: int = Field(gt=0)
    word_max: int = Field(gt=0)
    section_delta: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_word_range(self) -> "SectionCoverage":
        if (
            self.word_max < self.word_min
            or len(self.primary_requirement_ids) != len(set(self.primary_requirement_ids))
            or len(self.owned_information_unit_ids)
            != len(set(self.owned_information_unit_ids))
            or set(self.primary_requirement_ids).intersection(self.permitted_callbacks)
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("SECTION_COVERAGE_WORD_RANGE_INVALID")
        return self


class SectionCoveragePlan(_Strict):
    schema_version: Literal["vcos.section-coverage-plan.v1"] = "vcos.section-coverage-plan.v1"
    content_mode: Literal["STANDALONE", "SERIES_EPISODE"]
    assignment_requirements_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_pack_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    information_units: list[InformationUnit] = Field(min_length=1)
    sections: list[SectionCoverage] = Field(min_length=3)
    target_word_count: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete_primary_ownership(self) -> "SectionCoveragePlan":
        section_ids = [item.section_id for item in self.sections]
        ordinals = [item.ordinal for item in self.sections]
        if len(section_ids) != len(set(section_ids)) or sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError("SECTION_COVERAGE_SECTION_IDENTITY_INVALID")
        requirement_ids = [
            ref for section in self.sections for ref in section.primary_requirement_ids
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("SECTION_COVERAGE_REQUIREMENT_PRIMARY_OWNERSHIP_DUPLICATE")
        unit_ids = [item.information_unit_id for item in self.information_units]
        owned = [ref for section in self.sections for ref in section.owned_information_unit_ids]
        if len(unit_ids) != len(set(unit_ids)) or sorted(owned) != sorted(unit_ids):
            raise ValueError("SECTION_COVERAGE_INFORMATION_UNIT_OWNERSHIP_INVALID")
        units_by_id = {item.information_unit_id: item for item in self.information_units}
        for section in self.sections:
            owned_requirements = {
                requirement
                for unit_id in section.owned_information_unit_ids
                for requirement in units_by_id[unit_id].assignment_requirement_refs
            }
            if (
                not set(section.primary_requirement_ids).issubset(owned_requirements)
                or not set(section.permitted_callbacks).issubset(set(requirement_ids))
            ):
                raise ValueError("SECTION_COVERAGE_REQUIREMENT_OWNERSHIP_INVALID")
        if not (
            sum(section.word_min for section in self.sections)
            <= self.target_word_count
            <= sum(section.word_max for section in self.sections)
        ):
            raise ValueError("SECTION_COVERAGE_TARGET_WORD_COUNT_INVALID")
        if self.content_hash != cross_modal_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("SECTION_COVERAGE_PLAN_HASH_INVALID")
        return self


class SourceTextSpan(_Strict):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "SourceTextSpan":
        if self.end <= self.start:
            raise ValueError("NARRATION_UNIT_SOURCE_SPAN_INVALID")
        return self


class NarrationUnit(_Strict):
    narration_unit_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    source_text_span: SourceTextSpan
    source_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1)
    information_unit_ids: list[str] = Field(min_length=1)
    assignment_requirement_ids: list[str] = Field(min_length=1)
    evidence_span_ids: list[str] = Field(min_length=1)
    semantic_intent: str = Field(min_length=1)
    visual_function: str = Field(min_length=1)
    importance: Literal["CORE", "SUPPORTING"]
    factual_risk: Literal["LOW", "MEDIUM", "HIGH"]
    estimated_spoken_duration_ms: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "NarrationUnit":
        if (
            len(self.information_unit_ids) != len(set(self.information_unit_ids))
            or len(self.assignment_requirement_ids)
            != len(set(self.assignment_requirement_ids))
            or len(self.evidence_span_ids) != len(set(self.evidence_span_ids))
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("NARRATION_UNIT_INTEGRITY_INVALID")
        return self


class NarrationUnitCompilation(_Strict):
    schema_version: Literal["vcos.narration-unit-compilation.v1"] = "vcos.narration-unit-compilation.v1"
    canonical_script_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    narration_units: list[NarrationUnit] = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "NarrationUnitCompilation":
        unit_ids = [item.narration_unit_id for item in self.narration_units]
        ordinals = [item.ordinal for item in self.narration_units]
        if (
            len(unit_ids) != len(set(unit_ids))
            or ordinals != list(range(1, len(ordinals) + 1))
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("NARRATION_UNIT_COMPILATION_INTEGRITY_INVALID")
        return self


class TimedNarrationUnitBinding(_Strict):
    narration_unit_id: str = Field(min_length=1)
    spoken_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    spoken_token_refs: list[str] = Field(min_length=1)
    verified_word_refs: list[str] = Field(min_length=1)
    actual_start_ms: int = Field(ge=0)
    actual_end_ms: int = Field(gt=0)
    alignment_confidence: float = Field(ge=0, le=1)
    alignment_evidence_ref: str = Field(min_length=1)
    alignment_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_time(self) -> "TimedNarrationUnitBinding":
        body = self.model_dump(
            mode="json", exclude={"alignment_hash", "content_hash"}
        )
        if (
            self.actual_end_ms <= self.actual_start_ms
            or len(self.spoken_token_refs) != len(set(self.spoken_token_refs))
            or len(self.verified_word_refs) != len(set(self.verified_word_refs))
            or self.alignment_hash != cross_modal_hash(body)
            or self.content_hash
            != cross_modal_hash({**body, "alignment_hash": self.alignment_hash})
        ):
            raise ValueError("NARRATION_UNIT_TIMING_INVALID")
        return self


class TimedNarrationBindingSet(_Strict):
    schema_version: Literal["vcos.timed-narration-unit-binding.v1"] = "vcos.timed-narration-unit-binding.v1"
    narration_unit_compilation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    spoken_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bindings: list[TimedNarrationUnitBinding] = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "TimedNarrationBindingSet":
        binding_ids = [item.narration_unit_id for item in self.bindings]
        if (
            len(binding_ids) != len(set(binding_ids))
            or any(item.spoken_text_hash != self.spoken_text_hash for item in self.bindings)
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("TIMED_NARRATION_BINDING_INTEGRITY_INVALID")
        return self


class VisualFunction(StrEnum):
    ACTION = "ACTION"
    PROCESS = "PROCESS"
    COMPARISON = "COMPARISON"
    DATA = "DATA"
    INTERFACE = "INTERFACE"
    CONCEPT_MODEL = "CONCEPT_MODEL"
    EXAMPLE_CONTEXT = "EXAMPLE_CONTEXT"
    TRANSITION_HERO = "TRANSITION_HERO"
    NO_VISUAL_CHANGE = "NO_VISUAL_CHANGE"


class VisualRealizationScene(_Strict):
    scene_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    narration_unit_ids: list[str] = Field(min_length=1)
    information_unit_ids: list[str] = Field(min_length=1)
    actual_start_ms: int = Field(ge=0)
    actual_end_ms: int = Field(gt=0)
    visual_function: VisualFunction
    scene_meaning: str = Field(min_length=1)
    information_ownership_statement: str = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    stable_visual_concept_key: str = Field(min_length=1)
    factual_risk: Literal["LOW", "MEDIUM", "HIGH"]
    importance: Literal["CORE", "SUPPORTING"]
    evidence_truth_requirement: Literal["NOT_REQUIRED", "AUTHORIZED_SOURCE_REQUIRED"]
    subject_requirements: list[str] = Field(min_length=1)
    action_requirements: list[str] = Field(default_factory=list)
    context_requirements: list[str] = Field(default_factory=list)
    prohibited_misreadings: list[str] = Field(default_factory=list)
    preferred_source_class: str = Field(min_length=1)
    route_constraints: list[str] = Field(default_factory=list)
    source_semantic_specs: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def timing_valid(self) -> "VisualRealizationScene":
        if (
            self.actual_end_ms <= self.actual_start_ms
            or len(self.narration_unit_ids) != len(set(self.narration_unit_ids))
            or len(self.information_unit_ids) != len(set(self.information_unit_ids))
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("VISUAL_REALIZATION_SCENE_TIMING_INVALID")
        return self


class VisualRealizationPlan(_Strict):
    schema_version: Literal["vcos.visual-realization-plan.v1"] = "vcos.visual-realization-plan.v1"
    narration_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenes: list[VisualRealizationScene] = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "VisualRealizationPlan":
        scene_ids = [item.scene_id for item in self.scenes]
        ordinals = [item.ordinal for item in self.scenes]
        if (
            len(scene_ids) != len(set(scene_ids))
            or ordinals != list(range(1, len(ordinals) + 1))
            or self.content_hash
            != cross_modal_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        ):
            raise ValueError("VISUAL_REALIZATION_PLAN_INTEGRITY_INVALID")
        return self


class AssetFitObservation(_Strict):
    scene_id: str = Field(min_length=1)
    asset_ref: str = Field(min_length=1)
    subject_match: Literal["PASS", "WARN", "BLOCK"]
    action_match: Literal["PASS", "WARN", "BLOCK"]
    context_match: Literal["PASS", "WARN", "BLOCK"]
    factual_match: Literal["PASS", "WARN", "BLOCK"]
    composition_match: Literal["PASS", "WARN", "BLOCK"]
    timing_fit: Literal["PASS", "WARN", "BLOCK"]
    style_compatibility: Literal["PASS", "WARN", "BLOCK"]
    visual_redundancy: Literal["PASS", "WARN", "BLOCK"]
    evidence_refs: list[str] = Field(default_factory=list)
    representative_still_refs: list[str] = Field(default_factory=list)
    disposition: Literal["PASS", "WARN", "BLOCK"]
    reason_codes: list[str] = Field(default_factory=list)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "AssetFitObservation":
        if self.content_hash != cross_modal_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("ASSET_FIT_OBSERVATION_HASH_INVALID")
        return self


class CrossModalObservation(_Strict):
    reason_code: str = Field(min_length=1)
    severity: Literal["OBSERVE", "WARN", "BLOCK"]
    scene_id: str | None = None
    narration_unit_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    owner_stage: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "CrossModalObservation":
        if self.content_hash != cross_modal_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("CROSS_MODAL_OBSERVATION_HASH_INVALID")
        return self


class CrossModalQCReport(_Strict):
    schema_version: Literal["vcos.cross-modal-qc.v1"] = "vcos.cross-modal-qc.v1"
    canonical_timeline_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_realization_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: list[CrossModalObservation] = Field(default_factory=list)
    deterministic_disposition: Literal["PASS", "WARN", "BLOCK"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_identity(self) -> "CrossModalQCReport":
        if self.content_hash != cross_modal_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("CROSS_MODAL_QC_REPORT_HASH_INVALID")
        return self
