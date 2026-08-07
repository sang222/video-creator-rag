"""Focused contract canaries for typed V2 capability and media lineage."""

from __future__ import annotations

import hashlib
import re
import uuid
from types import SimpleNamespace

import pytest

from app.capabilities import default_capability_compiler
from app.capabilities.compiler import (
    CapabilityCompilationError,
    CapabilityCompiler,
    ExecutionClassification,
    SkillDefinition,
    stable_hash,
)
from app.contracts.cross_modal import (
    CrossModalObservation,
    VisualFunction,
    VisualRealizationPlan,
    VisualRealizationScene,
    cross_modal_hash,
)
from app.contracts.script_qualification import QualifiedScriptOutputV2
from app.services.cross_modal import (
    CrossModalContractError,
    NarrationUnitCompiler,
    SectionCoverageCompiler,
    asset_fit_observation,
    bind_timed_narration_units,
    cross_modal_qc_report,
    validate_visual_realization_plan,
)
from app.services.v2_native_effects import _cross_modal_timeline_projection


def _qualified_fixture() -> tuple[object, QualifiedScriptOutputV2, str]:
    assignment = {
        "content_mode": "STANDALONE",
        "required_requirement_units": [
            {
                "requirement_id": "subject",
                "required": True,
                "obligation": "Define the operational problem precisely.",
                "requirement_type": "SUBJECT",
            },
            {
                "requirement_id": "question",
                "required": True,
                "obligation": "Explain the decision the viewer can make.",
                "requirement_type": "QUESTION",
            },
            {
                "requirement_id": "viewer-action",
                "required": True,
                "obligation": "Give the viewer one evidence-bound next action.",
                "requirement_type": "VIEWER_ACTION",
            },
        ],
    }
    evidence = {"spans": [{"evidence_span_id": "evidence-001"}]}
    runtime = {
        "duration_contract": {"target_duration_ms": 90_000},
        "duration_estimation_wpm": 150,
    }
    coverage = SectionCoverageCompiler.compile(
        assignment=assignment,
        evidence_pack=evidence,
        runtime_contract=runtime,
    )
    narrations = [
        "The operational problem is a mismatch between a stated goal and the evidence available to support it. This section defines that boundary before making a recommendation.",
        "The practical decision is whether the evidence supports the next change rather than a broader claim. The viewer compares the stated constraint with the observed proof before acting.",
        "The next action is to record the smallest test that can disprove the current assumption. That action preserves an evidence trail for the following decision.",
    ]
    output = QualifiedScriptOutputV2(
        language="en",
        sections=[
            {
                "section_id": section.section_id,
                "ordinal": section.ordinal,
                "purpose": section.section_delta,
                "narration": narrations[index],
                "required_assignment_unit_refs": section.primary_requirement_ids,
            }
            for index, section in enumerate(coverage.sections)
        ],
    )
    return coverage, output, "\n\n".join(narrations)


def _timed_words(script: str) -> list[dict[str, int | str]]:
    return [
        {
            "index": index,
            "text": token,
            "start_ms": (index - 1) * 200,
            "end_ms": index * 200,
        }
        for index, token in enumerate(re.findall(r"\S+", script), start=1)
    ]


def test_compiler_projection_is_deterministic_and_budgeted() -> None:
    compiler = default_capability_compiler()
    first = compiler.compile(
        role="script_writer",
        task="long_form_script",
        content_mode="STANDALONE",
        stage_context={"assignment_hash": "a" * 64},
    )
    second = compiler.compile(
        role="script_writer",
        task="long_form_script",
        content_mode="STANDALONE",
        stage_context={"assignment_hash": "a" * 64},
    )
    assert first.compiled_projection_hash == second.compiled_projection_hash
    assert first.estimated_tokens <= first.max_compiled_tokens == 280
    assert "procedure_blocks" not in first.provider_payload()
    assert first.receipt_identity()["skills"]


def test_capability_compiler_rejects_conflicts_and_hard_budget_overflow() -> None:
    def skill(
        skill_id: str,
        procedure: str,
        *,
        conflicts: list[str] | None = None,
    ) -> SkillDefinition:
        body = {
            "skill_id": skill_id,
            "version": "v1",
            "execution_classification": ExecutionClassification.SKILL_REQUIRED.value,
            "applicable_roles": ["script_writer"],
            "applicable_tasks": ["long_form_script"],
            "required_features": [],
            "primitive_refs": [],
            "procedure_blocks": [procedure],
            "prohibited_behaviors": [],
            "validation_hooks": [],
            "max_compiled_tokens": 380,
            "compatibility": {"conflicts": conflicts or []},
        }
        return SkillDefinition(**body, content_hash=stable_hash(body))

    conflict = CapabilityCompiler(
        (
            skill("assignment", "Keep scope frozen.", conflicts=["style"]),
            skill("style", "Use concise speech."),
        )
    )
    with pytest.raises(CapabilityCompilationError, match="CAPABILITY_SKILL_CONFLICT"):
        conflict.compile(
            role="script_writer",
            task="long_form_script",
            content_mode="STANDALONE",
        )

    overflowing = CapabilityCompiler((skill("overflow", "word " * 300),))
    with pytest.raises(
        CapabilityCompilationError,
        match="CAPABILITY_PROJECTION_TOKEN_BUDGET_EXCEEDED",
    ):
        overflowing.compile(
            role="script_writer",
            task="long_form_script",
            content_mode="STANDALONE",
        )


def test_narration_units_bind_exact_words_and_drive_one_realization_scene_each() -> None:
    coverage, output, canonical_script = _qualified_fixture()
    script_hash = hashlib.sha256(canonical_script.encode("utf-8")).hexdigest()
    compilation = NarrationUnitCompiler.compile(
        output=output,
        canonical_script=canonical_script,
        canonical_script_hash=script_hash,
        coverage_plan=coverage,
        estimated_duration_ms=90_000,
    )
    bindings = bind_timed_narration_units(
        compilation=compilation,
        spoken_text_hash="b" * 64,
        timed_words=_timed_words(canonical_script),
        alignment_evidence_ref="artifact-version://timed-words",
    )
    assert len(compilation.narration_units) == len(bindings.bindings) == 3
    assert len(compilation.narration_units) < 6  # not one pseudo-scene per sentence

    scenes = []
    for ordinal, (unit, binding) in enumerate(
        zip(compilation.narration_units, bindings.bindings, strict=True), start=1
    ):
        body = {
            "scene_id": f"scene-{ordinal:03d}",
            "ordinal": ordinal,
            "narration_unit_ids": [unit.narration_unit_id],
            "information_unit_ids": unit.information_unit_ids,
            "actual_start_ms": binding.actual_start_ms,
            "actual_end_ms": binding.actual_end_ms,
            "visual_function": VisualFunction.CONCEPT_MODEL,
            "scene_meaning": unit.semantic_intent,
            "information_ownership_statement": "Owns its compiled information unit.",
            "visual_intent": "Explain the qualified relationship natively.",
            "stable_visual_concept_key": f"concept-{ordinal}",
            "factual_risk": unit.factual_risk,
            "importance": unit.importance,
            "evidence_truth_requirement": "NOT_REQUIRED",
            "subject_requirements": [unit.semantic_intent],
            "action_requirements": [],
            "context_requirements": [],
            "prohibited_misreadings": [],
            "preferred_source_class": "NATIVE_GRAPHIC",
            "route_constraints": [],
            "source_semantic_specs": {},
        }
        scenes.append(VisualRealizationScene(**body, content_hash=cross_modal_hash(body)))
    plan_body = {
        "schema_version": "vcos.visual-realization-plan.v1",
        "narration_binding_hash": bindings.content_hash,
        "coverage_plan_hash": coverage.content_hash,
        "scenes": [item.model_dump(mode="json") for item in scenes],
    }
    plan = VisualRealizationPlan(**plan_body, content_hash=cross_modal_hash(plan_body))
    validate_visual_realization_plan(plan=plan, bindings=bindings)


def test_cross_modal_contract_blocks_token_drift_and_direct_mismatch() -> None:
    coverage, output, canonical_script = _qualified_fixture()
    compilation = NarrationUnitCompiler.compile(
        output=output,
        canonical_script=canonical_script,
        canonical_script_hash=hashlib.sha256(canonical_script.encode()).hexdigest(),
        coverage_plan=coverage,
        estimated_duration_ms=90_000,
    )
    words = _timed_words(canonical_script)
    words[0]["text"] = "different"
    with pytest.raises(CrossModalContractError, match="ALIGNMENT_TOKEN_MISMATCH"):
        bind_timed_narration_units(
            compilation=compilation,
            spoken_text_hash="c" * 64,
            timed_words=words,
            alignment_evidence_ref="artifact-version://timed-words",
        )
    observation_body = {
        "reason_code": "NARRATION_VISUAL_DIRECT_MISMATCH",
        "severity": "BLOCK",
        "scene_id": None,
        "narration_unit_ids": [],
        "evidence_refs": [],
        "owner_stage": "QC",
        "detail": "The visible scene asserts a different relationship.",
    }
    report = cross_modal_qc_report(
        canonical_timeline_hash="d" * 64,
        visual_realization_plan_hash="e" * 64,
        observations=[
            CrossModalObservation(
                **observation_body, content_hash=cross_modal_hash(observation_body)
            )
        ],
    )
    assert report.deterministic_disposition == "BLOCK"
    fit = asset_fit_observation(
        scene_id="scene-001",
        asset_ref="native://scene-001",
        components={
            "subject_match": "PASS",
            "action_match": "PASS",
            "context_match": "PASS",
            "factual_match": "PASS",
            "composition_match": "PASS",
            "timing_fit": "PASS",
            "style_compatibility": "PASS",
            "visual_redundancy": "PASS",
        },
    )
    assert fit.disposition == "PASS"


def test_timed_narration_binding_rejects_non_monotonic_provider_word_evidence() -> None:
    coverage, output, canonical_script = _qualified_fixture()
    compilation = NarrationUnitCompiler.compile(
        output=output,
        canonical_script=canonical_script,
        canonical_script_hash=hashlib.sha256(canonical_script.encode()).hexdigest(),
        coverage_plan=coverage,
        estimated_duration_ms=90_000,
    )
    words = _timed_words(canonical_script)
    words[1]["start_ms"] = words[0]["start_ms"] - 1
    with pytest.raises(CrossModalContractError, match="TIMED_WORD_ORDER_INVALID"):
        bind_timed_narration_units(
            compilation=compilation,
            spoken_text_hash="d" * 64,
            timed_words=words,
            alignment_evidence_ref="artifact-version://timed-words",
        )


def test_real_timeline_projection_requires_timed_words_and_preserves_lineage() -> None:
    coverage, output, canonical_script = _qualified_fixture()
    script_hash = hashlib.sha256(canonical_script.encode()).hexdigest()
    writer_sections = [item.model_dump(mode="json") for item in output.sections]
    lineage_body = {
        "qualified_script_hash": script_hash,
        "section_coverage_plan": coverage.model_dump(mode="json"),
        "writer_sections": writer_sections,
        "capability_projection_receipts": {"writer": {"projection": "sealed"}},
    }
    script = SimpleNamespace(
        id=uuid.uuid4(),
        content_hash="f" * 64,
        content={
            "narration_text": canonical_script,
            "cross_modal_script_lineage": {
                **lineage_body,
                "content_hash": cross_modal_hash(lineage_body),
            },
            "section_coverage_plan": coverage.model_dump(mode="json"),
            "single_source_sections": writer_sections,
            "approved_script_provenance": {"language": "en"},
        },
    )
    projection = _cross_modal_timeline_projection(
        project=SimpleNamespace(title="Cross-modal canary"),
        script=script,
        visual=SimpleNamespace(id=uuid.uuid4(), content_hash="1" * 64),
        narration_text=canonical_script,
        duration_ms=90_000,
        audio={
            "alignment_method": "ELEVENLABS_TIMESTAMPS",
            "timing_seed": {"spoken_text_hash": "2" * 64},
            "timed_words_ref": "artifact-version://timed-words",
            "timed_words": _timed_words(canonical_script),
        },
    )
    assert projection is not None
    assert projection["narration_unit_compilation_hash"] == projection[
        "narration_unit_compilation"
    ]["content_hash"]
    assert all(scene["narration_unit_ids"] for scene in projection["scenes"])
