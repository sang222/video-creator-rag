from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.ai_visual_production import AIVisualNarrationUnit
from app.contracts.editorial_authorship import EditorialAuthorshipContract
from app.contracts.semantic import (
    Applicability,
    ChannelSemanticProfile,
    ComparisonFeatureDefinition,
    ComparisonFeatureScope,
    ComparisonFeatureValue,
    ComparisonFeatureView,
    EvidenceRequirement,
    Factuality,
    FormatSemanticProfile,
    OverlaySemanticIntent,
    OverlayState,
    PresentationOutcome,
    PresentationSemanticIntent,
    ProjectRichSemanticSnapshot,
    ProjectionNotApplicable,
    SemanticAtom,
    SemanticAtomKind,
    SemanticAuthorityRef,
    SemanticDefinitionScope,
    SemanticExtensionDefinition,
    SemanticExtensionPayload,
    SemanticKernelDefinition,
    SemanticMeaningUnit,
    SemanticPresentationRole,
    SemanticProjectionCompiler,
    SemanticProjectionFamily,
    TemporalSemanticBinding,
    VisualReuseCompatibility,
    VisualSemanticProjection,
    WriterSemanticProjection,
    visual_reuse_compatible,
)
from app.services.semantic import SemanticProfileCompiler


def _feature(key: str, scope: ComparisonFeatureScope, values: list[str]):
    return ComparisonFeatureDefinition.build(
        feature_key=key,
        scope=scope,
        allowed_values=values,
    )


def _kernel() -> SemanticKernelDefinition:
    return SemanticKernelDefinition.build(
        global_feature_definitions=[
            _feature(
                "angle_family",
                ComparisonFeatureScope.GLOBAL,
                ["FAILURE_MECHANISM", "DECISION_GUIDE"],
            ),
            _feature(
                "mechanism_family",
                ComparisonFeatureScope.GLOBAL,
                ["ASYNC_APPROVAL", "WORKFLOW_HANDOFF"],
            ),
        ]
    )


def _small_team_channel() -> ChannelSemanticProfile:
    return ChannelSemanticProfile.build(
        channel_profile_ref="channel-profile://operator-notes/v3",
        semantic_definition_version="operator-notes-semantic/v1",
        extension_definitions=[
            SemanticExtensionDefinition.build(
                extension_definition_id="channel-extension://operator-notes/decision",
                scope=SemanticDefinitionScope.CHANNEL,
                definition_version="v1",
                field_keys=["central_question", "decision_value"],
                projection_families=[SemanticProjectionFamily.WRITER],
            )
        ],
        comparison_feature_definitions=[
            _feature(
                "narrative_structure",
                ComparisonFeatureScope.PROFILE,
                ["PROBLEM_TO_DECISION", "FAILURE_TO_RECOVERY"],
            )
        ],
    )


def _explainer_format() -> FormatSemanticProfile:
    return FormatSemanticProfile.build(
        format_profile_ref="format-profile://editorial-explainer/v2",
        semantic_definition_version="editorial-explainer-semantic/v2",
        required_projection_families=[
            SemanticProjectionFamily.WRITER,
            SemanticProjectionFamily.VISUAL,
            SemanticProjectionFamily.PACKAGING,
            SemanticProjectionFamily.QC,
            SemanticProjectionFamily.LEARNING,
        ],
        comparison_feature_definitions=[
            _feature(
                "visual_grammar_family",
                ComparisonFeatureScope.FORMAT,
                ["EXPLANATORY_DIAGRAM", "CONCEPTUAL_SEQUENCE"],
            )
        ],
    )


def _audio_commentary_format() -> FormatSemanticProfile:
    return FormatSemanticProfile.build(
        format_profile_ref="format-profile://solo-commentary/v1",
        semantic_definition_version="solo-commentary-semantic/v1",
        required_projection_families=[
            SemanticProjectionFamily.WRITER,
            SemanticProjectionFamily.PACKAGING,
            SemanticProjectionFamily.QC,
            SemanticProjectionFamily.LEARNING,
        ],
        comparison_feature_definitions=[
            _feature(
                "delivery_grammar_family",
                ComparisonFeatureScope.FORMAT,
                ["MONOLOGUE", "REFLECTIVE_COMMENTARY"],
            )
        ],
    )


def _podcast_channel() -> ChannelSemanticProfile:
    return ChannelSemanticProfile.build(
        channel_profile_ref="channel-profile://future-conversations/v1",
        semantic_definition_version="future-conversations-semantic/v1",
        extension_definitions=[
            SemanticExtensionDefinition.build(
                extension_definition_id="channel-extension://future-conversations/turn",
                scope=SemanticDefinitionScope.CHANNEL,
                definition_version="v1",
                field_keys=["speaker_role", "turn_intent", "conversation_act"],
                projection_families=[SemanticProjectionFamily.WRITER],
            )
        ],
        comparison_feature_definitions=[
            _feature(
                "conversation_pattern",
                ComparisonFeatureScope.PROFILE,
                ["CHALLENGE_AND_CLARIFY", "QUESTION_AND_ANSWER"],
            )
        ],
    )


def _podcast_format() -> FormatSemanticProfile:
    return FormatSemanticProfile.build(
        format_profile_ref="format-profile://interview-podcast/v1",
        semantic_definition_version="interview-podcast-semantic/v1",
        required_projection_families=[
            SemanticProjectionFamily.WRITER,
            SemanticProjectionFamily.PACKAGING,
            SemanticProjectionFamily.QC,
            SemanticProjectionFamily.LEARNING,
        ],
        comparison_feature_definitions=[
            _feature(
                "conversation_grammar",
                ComparisonFeatureScope.FORMAT,
                ["INTERVIEW", "ROUND_TABLE"],
            )
        ],
    )


def _authorship() -> EditorialAuthorshipContract:
    return EditorialAuthorshipContract.build(
        source_evidence_refs=["evidence://approved/recovery"],
        authored_authority_refs=[
            "brief://episode",
            "outline://episode",
            "review://episode",
        ],
        content_mode="STANDALONE",
        format_key="editorial-explainer",
        channel_promise="Make operational tradeoffs legible to a cold viewer.",
        episode_reasoning="Show why bounded recovery requires an authored decision.",
        central_question="Where does an async approval recovery fail?",
        early_stakes_or_payoff="A hidden exception handoff delays the decision.",
        original_thesis_or_position="The recovery boundary must be explicit.",
        editorial_delta="Separate authority from implementation detail.",
        reasoning_or_narrative_spine="Failure, boundary, then recoverable decision.",
        progression="Reveal the handoff before the recovery payoff.",
        tension_applicability="APPLICABLE",
        tension_failure_contradiction_or_tradeoff="Speed conflicts with accountable approval.",
        visible_editorial_judgment="The exception handoff is the meaningful boundary.",
        memorable_payoff_framework_or_conclusion="Bound the fallback before it becomes invisible debt.",
    )


def _meaning(statement: str = "A bounded recovery crosses an approval boundary."):
    return SemanticMeaningUnit(
        meaning_id="meaning-approval-recovery",
        statement=statement,
        factuality=Factuality.FACTUAL,
        evidence_requirement=EvidenceRequirement.REQUIRED,
        atoms=[
            SemanticAtom(
                atom_id="subject",
                kind=SemanticAtomKind.SUBJECT,
                value="approval recovery",
            ),
            SemanticAtom(
                atom_id="state",
                kind=SemanticAtomKind.STATE,
                value="bounded fallback pending human approval",
            ),
            SemanticAtom(
                atom_id="action",
                kind=SemanticAtomKind.ACTION,
                value="routes an exception to an accountable decision",
            ),
            SemanticAtom(
                atom_id="relation",
                kind=SemanticAtomKind.CAUSAL_RELATION,
                value="an unowned exception delays the recovery",
            ),
            SemanticAtom(
                atom_id="context",
                kind=SemanticAtomKind.CONTEXT,
                value="a bounded operational recovery workflow",
            ),
            SemanticAtom(
                atom_id="claim",
                kind=SemanticAtomKind.CLAIM,
                value="A recovery needs an explicit approval boundary.",
            ),
            SemanticAtom(
                atom_id="evidence",
                kind=SemanticAtomKind.EVIDENCE_REQUIREMENT,
                value="Trace the recovery to approved evidence.",
            ),
            SemanticAtom(
                atom_id="premise",
                kind=SemanticAtomKind.PREMISE,
                value="Async recovery can hide an unowned handoff.",
            ),
            SemanticAtom(
                atom_id="stakes",
                kind=SemanticAtomKind.VIEWER_OR_LISTENER_STAKES,
                value="The operator needs to see where a decision is owed.",
            ),
            SemanticAtom(
                atom_id="bridge",
                kind=SemanticAtomKind.COLD_VIEWER_BRIDGE,
                value="Define the approval boundary before using system terms.",
            ),
            SemanticAtom(
                atom_id="preserve",
                kind=SemanticAtomKind.MUST_PRESERVE,
                value="The fallback is bounded and still requires approval.",
            ),
            SemanticAtom(
                atom_id="abstract",
                kind=SemanticAtomKind.MAY_ABSTRACT,
                value="The system may be represented without a product UI.",
            ),
            SemanticAtom(
                atom_id="invent",
                kind=SemanticAtomKind.MUST_NOT_INVENT,
                value="Do not imply an autonomous approval decision.",
            ),
        ],
    )


def _compilation(
    channel: ChannelSemanticProfile | None = None,
    format: FormatSemanticProfile | None = None,
):
    return SemanticProfileCompiler.compile(
        kernel=_kernel(),
        channel_profile=channel or _small_team_channel(),
        format_profile=format or _explainer_format(),
    )


def _snapshot(
    *,
    compilation=None,
    statement: str = "A bounded recovery crosses an approval boundary.",
    podcast: bool = False,
):
    compilation = compilation or _compilation()
    authorship = _authorship()
    authorship_ref = f"editorial-authorship://{authorship.content_hash}"
    extension = (
        SemanticExtensionPayload(
            extension_definition_id="channel-extension://future-conversations/turn",
            semantic_owner_ref="meaning-approval-recovery",
            values={
                "speaker_role": "HOST",
                "turn_intent": "CLARIFY",
                "conversation_act": "QUESTION",
            },
        )
        if podcast
        else SemanticExtensionPayload(
            extension_definition_id="channel-extension://operator-notes/decision",
            semantic_owner_ref="meaning-approval-recovery",
            values={
                "central_question": "Where is the accountable boundary?",
                "decision_value": "Expose the handoff before it becomes delay.",
            },
        )
    )
    return ProjectRichSemanticSnapshot.build_from_authorship_contract(
        editorial_authorship_contract=authorship,
        snapshot_id="semantic-snapshot-001",
        project_ref="video-project://001",
        revision_ref="revision://001",
        semantic_profile=compilation,
        source_authorities=[
            SemanticAuthorityRef(
                authority_type="QUALIFIED_SCRIPT",
                authority_ref="script://qualified/001",
                content_hash="1" * 64,
            )
        ],
        meaning_units=[_meaning(statement)],
        extensions=[extension],
        temporal_bindings=[
            TemporalSemanticBinding(
                semantic_boundary_id="boundary-approval-001",
                semantic_owner_ref="meaning-approval-recovery",
                presentation_intent=PresentationSemanticIntent(
                    outcome=PresentationOutcome.HOLD,
                    semantic_role=SemanticPresentationRole.HOLD,
                    editorial_reason="Hold while the viewer resolves the approval boundary.",
                    editorial_authority_ref=authorship_ref,
                ),
                viewer_beat_ref="viewer-beat://approval-recognition",
                technical_segment_ref="provider-segment://unrelated-boundary",
            )
        ],
        overlay_intents=[
            OverlaySemanticIntent(
                overlay_state=OverlayState.NO_OVERLAY,
                semantic_owner_ref="meaning-approval-recovery",
                presentation_intent=PresentationSemanticIntent(
                    outcome=PresentationOutcome.NO_VISUAL_CHANGE,
                    editorial_reason="The authored hold is clearer without display copy.",
                    editorial_authority_ref=authorship_ref,
                ),
                continuity_or_change_reason="No overlay preserves the authored hold.",
            )
        ],
    )


def _feature_values(snapshot, *, mechanism: str = "ASYNC_APPROVAL"):
    values = {
        "angle_family": "FAILURE_MECHANISM",
        "mechanism_family": mechanism,
        "narrative_structure": "FAILURE_TO_RECOVERY",
        "visual_grammar_family": "CONCEPTUAL_SEQUENCE",
        "conversation_pattern": "CHALLENGE_AND_CLARIFY",
        "conversation_grammar": "INTERVIEW",
        "delivery_grammar_family": "MONOLOGUE",
    }
    return [
        ComparisonFeatureValue(
            feature_key=definition.feature_key,
            scope=definition.scope,
            applicability=Applicability.APPLICABLE,
            value=values[definition.feature_key],
        )
        for definition in snapshot.semantic_profile.feature_definitions
    ]


def test_small_team_like_profile_compiles_without_global_hard_code():
    compiled = _compilation()

    assert compiled.channel_profile.channel_profile_ref == "channel-profile://operator-notes/v3"
    assert "SMALL_TEAM_AI" not in compiled.model_dump_json()
    assert compiled.format_profile.format_profile_ref.endswith("editorial-explainer/v2")


def test_podcast_profile_compiles_without_visual_only_global_fields():
    compiled = _compilation(_podcast_channel(), _podcast_format())
    snapshot = _snapshot(compilation=compiled, podcast=True)

    visual = SemanticProjectionCompiler.visual(snapshot)
    writer = SemanticProjectionCompiler.writer(snapshot)

    assert isinstance(visual, ProjectionNotApplicable)
    assert visual.applicability == Applicability.NOT_APPLICABLE
    assert isinstance(writer, WriterSemanticProjection)
    assert writer.extensions[0].values["speaker_role"] == "HOST"


def test_channel_is_not_format_same_channel_can_compile_distinct_format_projections():
    channel = _small_team_channel()
    explainer = _compilation(channel, _explainer_format())
    commentary = _compilation(channel, _audio_commentary_format())

    assert explainer.channel_profile.content_hash == commentary.channel_profile.content_hash
    assert explainer.format_profile.content_hash != commentary.format_profile.content_hash
    assert SemanticProjectionFamily.VISUAL in explainer.format_profile.required_projection_families
    assert SemanticProjectionFamily.VISUAL not in commentary.format_profile.required_projection_families


def test_stage_projections_share_language_but_not_a_shared_payload():
    snapshot = _snapshot()

    writer = SemanticProjectionCompiler.writer(snapshot)
    visual = SemanticProjectionCompiler.visual(snapshot)

    assert isinstance(writer, WriterSemanticProjection)
    assert isinstance(visual, VisualSemanticProjection)
    writer_kinds = {atom.kind for unit in writer.writer_units for atom in unit.atoms}
    visual_kinds = {atom.kind for unit in visual.visual_units for atom in unit.atoms}
    assert SemanticAtomKind.CLAIM in writer_kinds
    assert SemanticAtomKind.MUST_NOT_INVENT not in writer_kinds
    assert SemanticAtomKind.MUST_NOT_INVENT in visual_kinds
    assert "comparison_fingerprint" not in writer.model_dump()
    assert "comparison_fingerprint" not in visual.model_dump()


def test_rich_snapshots_with_different_mechanisms_can_share_a_coarse_feature_family():
    first = _snapshot(statement="A three-stage async approval recovery preserves a bounded fallback.")
    second = _snapshot(statement="A delegated exception queue preserves an accountable recovery boundary.")

    first_view = ComparisonFeatureView.build(
        snapshot=first, features=_feature_values(first)
    )
    second_view = ComparisonFeatureView.build(
        snapshot=second, features=_feature_values(second)
    )

    assert first.content_hash != second.content_hash
    assert first_view.shared_feature_fingerprint == second_view.shared_feature_fingerprint
    assert first_view.comparison_fingerprint == second_view.comparison_fingerprint


def test_comparison_features_reject_rich_prose_and_uncontrolled_values():
    snapshot = _snapshot()
    with pytest.raises(ValidationError):
        ComparisonFeatureValue(
            feature_key="mechanism_family",
            scope=ComparisonFeatureScope.GLOBAL,
            applicability=Applicability.APPLICABLE,
            value="three stage async approval recovery with bounded fallback",
        )

    invalid = _feature_values(snapshot)
    invalid[1] = ComparisonFeatureValue(
        feature_key="mechanism_family",
        scope=ComparisonFeatureScope.GLOBAL,
        applicability=Applicability.APPLICABLE,
        value="DECISION_GUIDE",
    )
    with pytest.raises(ValidationError, match="COMPARISON_FEATURE_VALUE_UNCONTROLLED"):
        ComparisonFeatureView.build(snapshot=snapshot, features=invalid)


def test_not_applicable_is_explicit_and_cannot_be_unknown_or_zero():
    value = ComparisonFeatureValue(
        feature_key="visual_grammar_family",
        scope=ComparisonFeatureScope.FORMAT,
        applicability=Applicability.NOT_APPLICABLE,
    )
    assert value.value is None
    with pytest.raises(ValidationError, match="COMPARISON_FEATURE_APPLICABILITY_VALUE_INVALID"):
        ComparisonFeatureValue(
            feature_key="visual_grammar_family",
            scope=ComparisonFeatureScope.FORMAT,
            applicability=Applicability.NOT_APPLICABLE,
            value="UNKNOWN",
        )


def test_semantic_owner_requires_explicit_identity_never_a_position():
    compiled = _compilation()
    authorship = _authorship()
    with pytest.raises(ValidationError, match="SEMANTIC_OWNER_REF_UNKNOWN_OR_POSITIONAL"):
        ProjectRichSemanticSnapshot.build_from_authorship_contract(
            editorial_authorship_contract=authorship,
            snapshot_id="semantic-snapshot-invalid",
            project_ref="video-project://001",
            revision_ref="revision://001",
            semantic_profile=compiled,
            meaning_units=[_meaning()],
            temporal_bindings=[
                TemporalSemanticBinding(
                    semantic_boundary_id="boundary-1",
                    semantic_owner_ref="position-1",
                    presentation_intent=PresentationSemanticIntent(
                        outcome=PresentationOutcome.HOLD,
                        semantic_role=SemanticPresentationRole.HOLD,
                        editorial_reason="Authored semantic hold.",
                        editorial_authority_ref=(
                            f"editorial-authorship://{authorship.content_hash}"
                        ),
                    ),
                )
            ],
        )


def test_effect_role_is_semantic_why_not_a_renderer_primitive():
    intent = PresentationSemanticIntent(
        outcome=PresentationOutcome.PRESENTATION_CHANGE,
        semantic_role=SemanticPresentationRole.REVEAL,
        editorial_reason="Reveal the causal boundary when it becomes relevant.",
        editorial_authority_ref="editorial-authorship://authority",
    )

    assert intent.semantic_role == SemanticPresentationRole.REVEAL
    assert "TEXT_SWIPE" not in intent.model_dump_json()
    with pytest.raises(ValidationError):
        PresentationSemanticIntent(
            outcome=PresentationOutcome.PRESENTATION_CHANGE,
            semantic_role="TEXT_SWIPE_IN",
            editorial_reason="Renderer primitive is not a semantic role.",
            editorial_authority_ref="editorial-authorship://authority",
        )


def test_semantic_boundary_viewer_beat_and_technical_segment_are_distinct():
    binding = _snapshot().temporal_bindings[0]

    assert binding.semantic_boundary_id == "boundary-approval-001"
    assert binding.viewer_beat_ref == "viewer-beat://approval-recognition"
    assert binding.technical_segment_ref == "provider-segment://unrelated-boundary"
    assert "ms" not in binding.model_dump_json().lower()


def test_hold_and_no_overlay_remain_valid_under_card_d_law():
    snapshot = _snapshot()

    assert snapshot.temporal_bindings[0].presentation_intent.outcome == PresentationOutcome.HOLD
    assert snapshot.overlay_intents[0].overlay_state == OverlayState.NO_OVERLAY
    assert snapshot.overlay_intents[0].presentation_intent.outcome == PresentationOutcome.NO_VISUAL_CHANGE


def test_visual_reuse_requires_subject_proposition_action_context_and_factuality():
    projection = SemanticProjectionCompiler.visual(_snapshot())

    assert isinstance(projection, VisualSemanticProjection)
    compatibility = projection.reuse_compatibility[0]
    assert compatibility.reuse_eligible is True
    incompatible = VisualReuseCompatibility.build(
        semantic_owner_ref="meaning-different",
        subject_refs=compatibility.subject_refs,
        proposition="A different proposition is not equivalent merely because it is adjacent.",
        action_or_relationships=compatibility.action_or_relationships,
        context_refs=compatibility.context_refs,
        factuality=compatibility.factuality,
        reuse_eligible=True,
    )
    assert visual_reuse_compatible(compatibility, incompatible) is False


def test_snapshot_version_hash_tamper_fails_closed():
    snapshot = _snapshot()
    tampered = snapshot.model_copy(update={"project_ref": "video-project://tampered"})

    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        tampered.verify_integrity()


def test_profile_extension_rejects_undeclared_fields_without_global_schema_change():
    snapshot = _snapshot()
    invalid_extension = SemanticExtensionPayload(
        extension_definition_id="channel-extension://operator-notes/decision",
        semantic_owner_ref="meaning-approval-recovery",
        values={"speaker_role": "HOST"},
    )
    with pytest.raises(ValidationError, match="SEMANTIC_EXTENSION_FIELD_UNDECLARED"):
        ProjectRichSemanticSnapshot.build(
            snapshot_id="semantic-snapshot-invalid-extension",
            project_ref=snapshot.project_ref,
            revision_ref=snapshot.revision_ref,
            semantic_profile=snapshot.semantic_profile,
            source_authorities=snapshot.source_authorities,
            meaning_units=snapshot.meaning_units,
            extensions=[invalid_extension],
        )


def test_rich_snapshot_hash_never_becomes_learning_equivalence_identity():
    first = _snapshot(statement="A three-stage async approval recovery preserves a bounded fallback.")
    second = _snapshot(statement="A delegated exception queue preserves an accountable recovery boundary.")
    first_view = ComparisonFeatureView.build(snapshot=first, features=_feature_values(first))
    second_view = ComparisonFeatureView.build(snapshot=second, features=_feature_values(second))

    assert first_view.source_semantic_snapshot_hash != second_view.source_semantic_snapshot_hash
    assert first_view.learning_equivalence_payload() == second_view.learning_equivalence_payload()
    assert "source_semantic_snapshot_hash" not in first_view.learning_equivalence_payload()


def test_existing_card_d_and_ai_visual_inputs_remain_readable_without_card_e_fields():
    unit = AIVisualNarrationUnit(
        narration_unit_id="existing-unit-1",
        information_unit_ids=["information-1"],
        actual_start_ms=0,
        actual_end_ms=1_000,
        spoken_text="Existing visual authority still accepts its contract.",
        scene_meaning="Explain one existing visual meaning.",
        visual_function="CONCEPT_MODEL",
        core_subject="an existing subject",
        action_or_relation="shows the existing relation",
        environment="an existing environment",
        visual_goal="make the existing authority clear",
        composition_direction="centered",
        camera_direction="eye level",
        motion_need="STATIC_SUFFICIENT",
    )

    assert unit.information_unit_ids == ["information-1"]
    assert _authorship().viewer_facing_presentation.no_effect_without_editorial_reason
