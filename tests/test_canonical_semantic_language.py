from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.ai_visual_production import AIVisualNarrationUnit
from app.contracts.editorial_authorship import (
    EditorialAuthorityBinding,
    EditorialAuthorityType,
    EditorialAuthorshipContract,
)
from app.contracts.channel_policy import FormatIdentityBinding, PolicyRef
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
    VisualReuseConstraintFact,
    VisualReuseSemanticFact,
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
        channel_authority=PolicyRef(
            ref="channel-profile://operator-notes/v3",
            version="3",
            content_hash="a" * 64,
        ),
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
        format_authority=FormatIdentityBinding(
            ref="format-profile://editorial-explainer/v2",
            version="2",
            content_hash="b" * 64,
            status="APPROVED",
        ),
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
        format_authority=FormatIdentityBinding(
            ref="format-profile://solo-commentary/v1",
            version="1",
            content_hash="c" * 64,
            status="APPROVED",
        ),
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
        channel_authority=PolicyRef(
            ref="channel-profile://future-conversations/v1",
            version="1",
            content_hash="d" * 64,
        ),
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
        format_authority=FormatIdentityBinding(
            ref="format-profile://interview-podcast/v1",
            version="1",
            content_hash="e" * 64,
            status="APPROVED",
        ),
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


def _small_team_channel_variant(
    *,
    authority: PolicyRef | None = None,
    comparison_feature_definitions: list[ComparisonFeatureDefinition] | None = None,
) -> ChannelSemanticProfile:
    base = _small_team_channel()
    return ChannelSemanticProfile.build(
        channel_authority=authority or base.channel_authority,
        semantic_definition_version=base.semantic_definition_version,
        extension_definitions=base.extension_definitions,
        comparison_feature_definitions=(
            comparison_feature_definitions
            if comparison_feature_definitions is not None
            else base.comparison_feature_definitions
        ),
    )


def _explainer_format_variant(
    *,
    authority: FormatIdentityBinding | None = None,
    required_projection_families: list[SemanticProjectionFamily] | None = None,
) -> FormatSemanticProfile:
    base = _explainer_format()
    return FormatSemanticProfile.build(
        format_authority=authority or base.format_authority,
        semantic_definition_version=base.semantic_definition_version,
        required_projection_families=(
            required_projection_families
            if required_projection_families is not None
            else base.required_projection_families
        ),
        extension_definitions=base.extension_definitions,
        comparison_feature_definitions=base.comparison_feature_definitions,
    )


def _authorship() -> EditorialAuthorshipContract:
    return EditorialAuthorshipContract.build(
        source_evidence_authorities=[
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.SOURCE_EVIDENCE,
                authority_ref="evidence://approved/recovery",
                content_hash="a" * 64,
            )
        ],
        authored_authorities=[
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.VIDEO_PROJECT,
                authority_ref="video-project://001",
                content_hash="3" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.PROJECT_ADMISSION,
                authority_ref=f"project-admission://001/{'4' * 64}",
                content_hash="4" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.CHANNEL_PROFILE,
                authority_ref=f"channel-profile://001/{'5' * 64}",
                content_hash="5" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.EDITORIAL_PROPOSAL,
                authority_ref=f"editorial-proposal://{'b' * 64}",
                content_hash="b" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.EDITORIAL_SPECIFICITY_RECEIPT,
                authority_ref=f"editorial-specificity://{'c' * 64}",
                content_hash="c" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.TOPIC_DEFINITION,
                authority_ref=f"topic-definition://{'d' * 64}",
                content_hash="d" * 64,
            ),
            EditorialAuthorityBinding(
                authority_type=EditorialAuthorityType.SECTION_COVERAGE_PLAN,
                authority_ref=f"section-coverage-plan://{'6' * 64}",
                content_hash="6" * 64,
            ),
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
                source_refs=["evidence://approved/recovery"],
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
                source_refs=["evidence://approved/recovery"],
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
                authority_type="RESEARCH_EVIDENCE",
                authority_ref="evidence://approved/recovery",
                content_hash="2" * 64,
            ),
            SemanticAuthorityRef(
                authority_type="QUALIFIED_SCRIPT",
                authority_ref="script://qualified/001",
                content_hash="1" * 64,
            ),
        ],
        meaning_units=[_meaning(statement)],
        extensions=[extension],
        temporal_bindings=[
            TemporalSemanticBinding(
                semantic_owner_ref="meaning-approval-recovery",
                authored_semantic_trigger_ref="meaning-approval-recovery#relation",
                presentation_intent=PresentationSemanticIntent(
                    outcome=PresentationOutcome.HOLD,
                    semantic_role=SemanticPresentationRole.HOLD,
                    editorial_reason="Hold while the viewer resolves the approval boundary.",
                    editorial_authority_ref=authorship_ref,
                ),
                semantic_boundary_ref="semantic-boundary://approval-001",
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


def _snapshot_variant(
    snapshot: ProjectRichSemanticSnapshot,
    **updates,
) -> ProjectRichSemanticSnapshot:
    values = {
        "snapshot_id": snapshot.snapshot_id,
        "project_ref": snapshot.project_ref,
        "revision_ref": snapshot.revision_ref,
        "semantic_profile": snapshot.semantic_profile,
        "source_authorities": snapshot.source_authorities,
        "meaning_units": snapshot.meaning_units,
        "extensions": snapshot.extensions,
        "temporal_bindings": snapshot.temporal_bindings,
        "overlay_intents": snapshot.overlay_intents,
    }
    values.update(updates)
    return ProjectRichSemanticSnapshot.build(**values)


def _reuse_compatibility(
    *facts: tuple[SemanticAtomKind, str],
    subject_refs: list[str] | None = None,
    context_refs: list[str] | None = None,
    evidence_requirement: EvidenceRequirement = EvidenceRequirement.REQUIRED,
    representation_constraints: list[tuple[SemanticAtomKind, str]] | None = None,
    semantic_owner_ref: str = "meaning-approval-recovery",
) -> VisualReuseCompatibility:
    return VisualReuseCompatibility.build(
        semantic_owner_ref=semantic_owner_ref,
        subject_refs=(
            subject_refs if subject_refs is not None else ["approval recovery"]
        ),
        proposition="The same proposition is being considered.",
        semantic_signature_facts=[
            VisualReuseSemanticFact(kind=kind, value=value) for kind, value in facts
        ],
        context_refs=(
            context_refs if context_refs is not None else ["operational workflow"]
        ),
        factuality=Factuality.FACTUAL,
        evidence_requirement=evidence_requirement,
        representation_constraints=[
            VisualReuseConstraintFact(kind=kind, value=value)
            for kind, value in (representation_constraints or [])
        ],
        reuse_eligible=bool(facts),
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

    assert (
        compiled.channel_profile.channel_profile_ref
        == "channel-profile://operator-notes/v3"
    )
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

    assert (
        explainer.channel_profile.content_hash
        == commentary.channel_profile.content_hash
    )
    assert (
        explainer.format_profile.content_hash != commentary.format_profile.content_hash
    )
    assert (
        SemanticProjectionFamily.VISUAL
        in explainer.format_profile.required_projection_families
    )
    assert (
        SemanticProjectionFamily.VISUAL
        not in commentary.format_profile.required_projection_families
    )


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
    first = _snapshot(
        statement="A three-stage async approval recovery preserves a bounded fallback."
    )
    second = _snapshot(
        statement="A delegated exception queue preserves an accountable recovery boundary."
    )

    first_view = ComparisonFeatureView.build(
        snapshot=first, features=_feature_values(first)
    )
    second_view = ComparisonFeatureView.build(
        snapshot=second, features=_feature_values(second)
    )

    assert first.content_hash != second.content_hash
    assert (
        first_view.shared_feature_fingerprint == second_view.shared_feature_fingerprint
    )
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
    with pytest.raises(
        ValidationError, match="COMPARISON_FEATURE_APPLICABILITY_VALUE_INVALID"
    ):
        ComparisonFeatureValue(
            feature_key="visual_grammar_family",
            scope=ComparisonFeatureScope.FORMAT,
            applicability=Applicability.NOT_APPLICABLE,
            value="UNKNOWN",
        )


def test_semantic_owner_requires_explicit_identity_never_a_position():
    compiled = _compilation()
    authorship = _authorship()
    with pytest.raises(
        ValidationError, match="SEMANTIC_OWNER_REF_UNKNOWN_OR_POSITIONAL"
    ):
        ProjectRichSemanticSnapshot.build_from_authorship_contract(
            editorial_authorship_contract=authorship,
            snapshot_id="semantic-snapshot-invalid",
            project_ref="video-project://001",
            revision_ref="revision://001",
            semantic_profile=compiled,
            source_authorities=[
                SemanticAuthorityRef(
                    authority_type="RESEARCH_EVIDENCE",
                    authority_ref="evidence://approved/recovery",
                    content_hash="2" * 64,
                )
            ],
            meaning_units=[_meaning()],
            temporal_bindings=[
                TemporalSemanticBinding(
                    semantic_owner_ref="position-1",
                    authored_semantic_trigger_ref="meaning-approval-recovery#relation",
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


def test_card_e_bridge_requires_verified_current_card_d_authority():
    authorship = _authorship()
    snapshot = _snapshot()
    assert any(
        authority.authority_ref == authorship.presentation_authority.authority_ref
        for authority in snapshot.source_authorities
    )

    authorities = list(authorship.authored_authorities)
    authorities[0] = authorities[0].model_copy(update={"content_hash": "0" * 64})
    tampered = authorship.model_copy(update={"authored_authorities": authorities})
    with pytest.raises(ValueError, match="EDITORIAL_AUTHORSHIP_CONTRACT_HASH_MISMATCH"):
        ProjectRichSemanticSnapshot.build_from_authorship_contract(
            editorial_authorship_contract=tampered
        )

    from app.contracts.editorial_authorship import _semantic_hash

    legacy_body = authorship.model_dump(mode="json", exclude={"content_hash"})
    legacy_body.pop("source_evidence_authorities")
    legacy_body.pop("authored_authorities")
    legacy_body["source_evidence_refs"] = ["evidence://historical"]
    legacy_body["authored_authority_refs"] = [
        "editorial-proposal://historical",
        "editorial-specificity://historical",
        "topic-definition://historical",
    ]
    legacy = EditorialAuthorshipContract.model_validate(
        {**legacy_body, "content_hash": _semantic_hash(legacy_body)}
    )
    with pytest.raises(
        ValueError, match="EDITORIAL_AUTHORSHIP_CURRENT_AUTHORITY_REQUIRED"
    ):
        ProjectRichSemanticSnapshot.build_from_authorship_contract(
            editorial_authorship_contract=legacy
        )


def test_effect_role_is_semantic_why_not_a_renderer_primitive():
    authority_ref = f"editorial-authorship://{'a' * 64}"
    intent = PresentationSemanticIntent(
        outcome=PresentationOutcome.PRESENTATION_CHANGE,
        semantic_role=SemanticPresentationRole.REVEAL,
        editorial_reason="Reveal the causal boundary when it becomes relevant.",
        editorial_authority_ref=authority_ref,
    )

    assert intent.semantic_role == SemanticPresentationRole.REVEAL
    assert "TEXT_SWIPE" not in intent.model_dump_json()
    with pytest.raises(ValidationError):
        PresentationSemanticIntent(
            outcome=PresentationOutcome.PRESENTATION_CHANGE,
            semantic_role="TEXT_SWIPE_IN",
            editorial_reason="Renderer primitive is not a semantic role.",
            editorial_authority_ref=authority_ref,
        )


def test_semantic_boundary_viewer_beat_and_technical_segment_are_distinct():
    binding = _snapshot().temporal_bindings[0]

    assert binding.semantic_boundary_ref == "semantic-boundary://approval-001"
    assert binding.viewer_beat_ref == "viewer-beat://approval-recognition"
    assert binding.technical_segment_ref == "provider-segment://unrelated-boundary"
    assert "ms" not in binding.model_dump_json().lower()


def test_hold_and_no_overlay_remain_valid_under_card_d_law():
    snapshot = _snapshot()

    assert (
        snapshot.temporal_bindings[0].presentation_intent.outcome
        == PresentationOutcome.HOLD
    )
    assert snapshot.overlay_intents[0].overlay_state == OverlayState.NO_OVERLAY
    assert (
        snapshot.overlay_intents[0].presentation_intent.outcome
        == PresentationOutcome.NO_VISUAL_CHANGE
    )


def test_visual_reuse_requires_subject_proposition_action_context_and_factuality():
    projection = SemanticProjectionCompiler.visual(_snapshot())

    assert isinstance(projection, VisualSemanticProjection)
    compatibility = projection.reuse_compatibility[0]
    assert compatibility.reuse_eligible is True
    incompatible = VisualReuseCompatibility.build(
        semantic_owner_ref="meaning-different",
        subject_refs=compatibility.subject_refs,
        proposition="A different proposition is not equivalent merely because it is adjacent.",
        semantic_signature_facts=compatibility.semantic_signature_facts,
        context_refs=compatibility.context_refs,
        factuality=compatibility.factuality,
        evidence_requirement=compatibility.evidence_requirement,
        representation_constraints=compatibility.representation_constraints,
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
    first = _snapshot(
        statement="A three-stage async approval recovery preserves a bounded fallback."
    )
    second = _snapshot(
        statement="A delegated exception queue preserves an accountable recovery boundary."
    )
    first_view = ComparisonFeatureView.build(
        snapshot=first, features=_feature_values(first)
    )
    second_view = ComparisonFeatureView.build(
        snapshot=second, features=_feature_values(second)
    )

    assert (
        first_view.source_semantic_snapshot_hash
        != second_view.source_semantic_snapshot_hash
    )
    assert (
        first_view.learning_equivalence_payload()
        == second_view.learning_equivalence_payload()
    )
    assert (
        "source_semantic_snapshot_hash" not in first_view.learning_equivalence_payload()
    )


def test_cross_profile_learning_can_compare_only_global_applicable_features():
    small_snapshot = _snapshot()
    podcast_snapshot = _snapshot(
        compilation=_compilation(_podcast_channel(), _podcast_format()),
        podcast=True,
    )
    small_view = ComparisonFeatureView.build(
        snapshot=small_snapshot,
        features=_feature_values(small_snapshot),
    )
    podcast_view = ComparisonFeatureView.build(
        snapshot=podcast_snapshot,
        features=_feature_values(podcast_snapshot),
    )

    assert (
        small_view.shared_feature_fingerprint == podcast_view.shared_feature_fingerprint
    )
    assert small_view.comparison_fingerprint != podcast_view.comparison_fingerprint
    assert small_view.learning_equivalence_payload(
        comparison_scope="GLOBAL"
    ) == podcast_view.learning_equivalence_payload(comparison_scope="GLOBAL")
    assert set(
        small_view.learning_equivalence_payload(comparison_scope="GLOBAL")[
            "normalized_features"
        ]
    ) == {"angle_family", "mechanism_family"}


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


def test_pr_workflow_explicitly_gates_card_e_source_tests_and_lint():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/canonical-semantic-language.yml"
    ).read_text()

    assert "pull_request:" in workflow
    assert "pytest -q tests/test_canonical_semantic_language.py" in workflow
    assert "app/contracts/semantic.py" in workflow
    assert "app/services/semantic.py" in workflow
    assert "tests/test_canonical_semantic_language.py" in workflow


def test_exact_channel_and_format_authority_bindings_change_compilation_identity():
    baseline = _compilation()
    same = _compilation()
    changed_channel = _small_team_channel().model_copy(
        update={
            "channel_authority": PolicyRef(
                ref="channel-profile://operator-notes/v3",
                version="3",
                content_hash="f" * 64,
            )
        }
    )
    changed_format = _explainer_format().model_copy(
        update={
            "format_authority": FormatIdentityBinding(
                ref="format-profile://editorial-explainer/v2",
                version="3",
                content_hash="b" * 64,
                status="APPROVED",
            )
        }
    )

    assert baseline.content_hash == same.content_hash
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        SemanticProfileCompiler.compile(
            kernel=_kernel(),
            channel_profile=changed_channel,
            format_profile=_explainer_format(),
        )
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        SemanticProfileCompiler.compile(
            kernel=_kernel(),
            channel_profile=_small_team_channel(),
            format_profile=changed_format,
        )

    rebounded_channel = _small_team_channel_variant(
        authority=PolicyRef(
            ref="channel-profile://operator-notes/v3",
            version="3",
            content_hash="f" * 64,
        )
    )
    rebound_format = _explainer_format_variant(
        authority=FormatIdentityBinding(
            ref="format-profile://editorial-explainer/v2",
            version="3",
            content_hash="b" * 64,
            status="APPROVED",
        )
    )
    assert (
        SemanticProfileCompiler.compile(
            kernel=_kernel(),
            channel_profile=rebounded_channel,
            format_profile=_explainer_format(),
        ).content_hash
        != baseline.content_hash
    )
    assert (
        SemanticProfileCompiler.compile(
            kernel=_kernel(),
            channel_profile=_small_team_channel(),
            format_profile=rebound_format,
        ).content_hash
        != baseline.content_hash
    )


def test_nested_authority_and_definition_hash_drift_fail_closed():
    channel = _small_team_channel()
    channel.channel_authority.content_hash = "f" * 64
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        channel.verify_integrity()

    format_profile = _explainer_format()
    format_profile.format_authority.content_hash = "0" * 64
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        format_profile.verify_integrity()

    kernel = _kernel()
    definition = kernel.global_feature_definitions[0].model_copy(
        update={"allowed_values": ["FAILURE_MECHANISM"]}
    )
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        definition.verify_integrity()
    kernel.global_feature_definitions[0] = definition
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        kernel.verify_integrity()

    projection = SemanticProjectionCompiler.writer(_snapshot())
    assert isinstance(projection, WriterSemanticProjection)
    tampered_projection = projection.model_copy(
        update={"semantic_snapshot_ref": "semantic-snapshot://tampered"}
    )
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        tampered_projection.verify_integrity()


def test_only_card_d_authorship_contract_can_author_presentation_intent():
    snapshot = _snapshot()
    evidence_ref = "evidence://approved/recovery"
    evidence_bound_intent = snapshot.temporal_bindings[
        0
    ].presentation_intent.model_copy(update={"editorial_authority_ref": evidence_ref})
    evidence_bound_binding = snapshot.temporal_bindings[0].model_copy(
        update={"presentation_intent": evidence_bound_intent}
    )

    with pytest.raises(
        ValidationError, match="SEMANTIC_PRESENTATION_AUTHORITY_NOT_AUTHORED"
    ):
        ProjectRichSemanticSnapshot.build(
            snapshot_id="semantic-snapshot-evidence-bound",
            project_ref=snapshot.project_ref,
            revision_ref=snapshot.revision_ref,
            semantic_profile=snapshot.semantic_profile,
            source_authorities=snapshot.source_authorities,
            meaning_units=snapshot.meaning_units,
            extensions=snapshot.extensions,
            temporal_bindings=[evidence_bound_binding],
            overlay_intents=snapshot.overlay_intents,
        )

    unknown_bound_intent = snapshot.temporal_bindings[0].presentation_intent.model_copy(
        update={"editorial_authority_ref": "authorship://unknown"}
    )
    unknown_bound_binding = snapshot.temporal_bindings[0].model_copy(
        update={"presentation_intent": unknown_bound_intent}
    )
    with pytest.raises(
        ValidationError, match="SEMANTIC_PRESENTATION_AUTHORITY_NOT_AUTHORED"
    ):
        ProjectRichSemanticSnapshot.build(
            snapshot_id="semantic-snapshot-unknown-bound",
            project_ref=snapshot.project_ref,
            revision_ref=snapshot.revision_ref,
            semantic_profile=snapshot.semantic_profile,
            source_authorities=snapshot.source_authorities,
            meaning_units=snapshot.meaning_units,
            extensions=snapshot.extensions,
            temporal_bindings=[unknown_bound_binding],
            overlay_intents=snapshot.overlay_intents,
        )


def test_learning_projection_is_canonical_and_respects_format_applicability():
    snapshot = _snapshot()
    learning = SemanticProjectionCompiler.learning(
        snapshot, features=_feature_values(snapshot)
    )

    assert isinstance(learning, ComparisonFeatureView)
    assert learning.projection_family == SemanticProjectionFamily.LEARNING
    assert learning.applicability == Applicability.APPLICABLE

    format_without_learning = _explainer_format_variant(
        required_projection_families=[SemanticProjectionFamily.WRITER]
    )
    not_applicable_snapshot = _snapshot(
        compilation=_compilation(format=format_without_learning)
    )
    result = SemanticProjectionCompiler.learning(
        not_applicable_snapshot,
        features=_feature_values(not_applicable_snapshot),
    )

    assert isinstance(result, ProjectionNotApplicable)
    assert result.projection_family == SemanticProjectionFamily.LEARNING
    with pytest.raises(ValueError, match="LEARNING_PROJECTION_FORMAT_NOT_APPLICABLE"):
        ComparisonFeatureView.build(
            snapshot=not_applicable_snapshot,
            features=_feature_values(not_applicable_snapshot),
        )


def test_definition_aware_fingerprints_distinguish_definition_drift_only_when_scoped():
    baseline_snapshot = _snapshot()
    baseline_view = ComparisonFeatureView.build(
        snapshot=baseline_snapshot,
        features=_feature_values(baseline_snapshot),
    )
    changed_kernel = SemanticKernelDefinition.build(
        global_feature_definitions=[
            _feature(
                "angle_family",
                ComparisonFeatureScope.GLOBAL,
                ["FAILURE_MECHANISM", "DECISION_GUIDE"],
            ),
            _feature(
                "mechanism_family",
                ComparisonFeatureScope.GLOBAL,
                ["ASYNC_APPROVAL", "WORKFLOW_HANDOFF", "ESCALATION_PATH"],
            ),
        ]
    )
    kernel_changed_snapshot = _snapshot(
        compilation=SemanticProfileCompiler.compile(
            kernel=changed_kernel,
            channel_profile=_small_team_channel(),
            format_profile=_explainer_format(),
        )
    )
    kernel_changed_view = ComparisonFeatureView.build(
        snapshot=kernel_changed_snapshot,
        features=_feature_values(kernel_changed_snapshot),
    )

    assert (
        baseline_view.shared_feature_fingerprint
        != kernel_changed_view.shared_feature_fingerprint
    )

    profile_definition = _feature(
        "narrative_structure",
        ComparisonFeatureScope.PROFILE,
        [
            "PROBLEM_TO_DECISION",
            "FAILURE_TO_RECOVERY",
            "DIRECT_TO_DECISION",
        ],
    )
    profile_changed_snapshot = _snapshot(
        compilation=_compilation(
            channel=_small_team_channel_variant(
                comparison_feature_definitions=[profile_definition]
            )
        )
    )
    profile_changed_view = ComparisonFeatureView.build(
        snapshot=profile_changed_snapshot,
        features=_feature_values(profile_changed_snapshot),
    )

    assert (
        baseline_view.shared_feature_fingerprint
        == profile_changed_view.shared_feature_fingerprint
    )
    assert (
        baseline_view.comparison_fingerprint
        != profile_changed_view.comparison_fingerprint
    )


def test_empty_scope_state_is_not_equivalent_to_all_not_applicable():
    baseline = _snapshot()
    all_not_applicable_values = [
        ComparisonFeatureValue(
            feature_key=definition.feature_key,
            scope=definition.scope,
            applicability=(
                Applicability.NOT_APPLICABLE
                if definition.scope == ComparisonFeatureScope.PROFILE
                else Applicability.APPLICABLE
            ),
            value=(
                None
                if definition.scope == ComparisonFeatureScope.PROFILE
                else _feature_values(baseline)[index].value
            ),
        )
        for index, definition in enumerate(
            baseline.semantic_profile.feature_definitions
        )
    ]
    all_not_applicable = ComparisonFeatureView.build(
        snapshot=baseline,
        features=all_not_applicable_values,
    )
    no_profile_snapshot = _snapshot(
        compilation=_compilation(
            channel=_small_team_channel_variant(comparison_feature_definitions=[])
        )
    )
    no_profile = ComparisonFeatureView.build(
        snapshot=no_profile_snapshot,
        features=_feature_values(no_profile_snapshot),
    )

    assert (
        all_not_applicable.profile_feature_fingerprint
        != no_profile.profile_feature_fingerprint
    )


def test_viewer_beat_or_technical_segment_can_exist_without_a_semantic_boundary():
    snapshot = _snapshot()
    binding = TemporalSemanticBinding(
        semantic_owner_ref="meaning-approval-recovery",
        authored_semantic_trigger_ref="meaning-approval-recovery#state",
        presentation_intent=snapshot.temporal_bindings[0].presentation_intent,
        viewer_beat_ref="viewer-beat://focus-b",
        technical_segment_ref="provider-segment://part-2",
    )
    no_boundary_snapshot = ProjectRichSemanticSnapshot.build(
        snapshot_id="semantic-snapshot-no-boundary",
        project_ref=snapshot.project_ref,
        revision_ref=snapshot.revision_ref,
        semantic_profile=snapshot.semantic_profile,
        source_authorities=snapshot.source_authorities,
        meaning_units=snapshot.meaning_units,
        extensions=snapshot.extensions,
        temporal_bindings=[binding],
        overlay_intents=snapshot.overlay_intents,
    )

    assert no_boundary_snapshot.temporal_bindings[0].semantic_boundary_ref is None
    assert (
        no_boundary_snapshot.temporal_bindings[0].viewer_beat_ref
        == "viewer-beat://focus-b"
    )
    assert (
        no_boundary_snapshot.temporal_bindings[0].technical_segment_ref
        == "provider-segment://part-2"
    )
    assert (
        "ms" not in no_boundary_snapshot.temporal_bindings[0].model_dump_json().lower()
    )
    with pytest.raises(
        ValidationError, match="AUTHORED_SEMANTIC_TRIGGER_POSITIONAL_FORBIDDEN"
    ):
        TemporalSemanticBinding(
            semantic_owner_ref="meaning-approval-recovery",
            authored_semantic_trigger_ref="position-3",
            presentation_intent=snapshot.temporal_bindings[0].presentation_intent,
        )


def test_factuality_and_evidence_requirement_have_one_typed_owner_and_survive_projections():
    snapshot = _snapshot()
    writer = SemanticProjectionCompiler.writer(snapshot)
    visual = SemanticProjectionCompiler.visual(snapshot)
    qc = SemanticProjectionCompiler.qc(snapshot)

    for projection, units in (
        (writer, writer.writer_units),
        (visual, visual.visual_units),
        (qc, qc.qc_units),
    ):
        assert projection.applicability == Applicability.APPLICABLE
        assert units[0].factuality == Factuality.FACTUAL
        assert units[0].evidence_requirement == EvidenceRequirement.REQUIRED
        assert all(
            atom.kind not in {"FACTUALITY", "EVIDENCE_REQUIREMENT"}
            for atom in units[0].atoms
        )

    assert "FACTUALITY" not in SemanticAtomKind
    assert "EVIDENCE_REQUIREMENT" not in SemanticAtomKind
    for forbidden_kind in ("FACTUALITY", "EVIDENCE_REQUIREMENT"):
        with pytest.raises(ValidationError):
            SemanticAtom(
                atom_id=f"forbidden-{forbidden_kind.lower()}",
                kind=forbidden_kind,
                value="A free-text duplicate typed fact is forbidden.",
            )
    with pytest.raises(
        ValidationError, match="SEMANTIC_EXTENSION_CANONICAL_FIELD_RESERVED"
    ):
        SemanticExtensionDefinition.build(
            extension_definition_id="channel-extension://invalid/factuality",
            scope=SemanticDefinitionScope.CHANNEL,
            definition_version="v1",
            field_keys=["factuality"],
        )
    prose_snapshot = _snapshot(
        statement="This prose says hypothetical, but prose cannot own factuality."
    )
    prose_writer = SemanticProjectionCompiler.writer(prose_snapshot)
    assert isinstance(prose_writer, WriterSemanticProjection)
    assert prose_writer.writer_units[0].factuality == Factuality.FACTUAL


def test_atom_provenance_resolves_exact_snapshot_authorities_without_authoring_effects():
    snapshot = _snapshot()
    declared_refs = {item.authority_ref for item in snapshot.source_authorities}
    atom_refs = {
        ref
        for unit in snapshot.meaning_units
        for atom in unit.atoms
        for ref in atom.source_refs
    }

    assert atom_refs == {"evidence://approved/recovery"}
    assert atom_refs.issubset(declared_refs)
    assert "evidence://approved/recovery" in {
        item.authority_ref
        for item in snapshot.source_authorities
        if item.authority_type == "RESEARCH_EVIDENCE"
    }
    assert (
        snapshot.temporal_bindings[0].presentation_intent.editorial_authority_ref
        != "evidence://approved/recovery"
    )

    unknown_atom = (
        snapshot.meaning_units[0]
        .atoms[0]
        .model_copy(update={"source_refs": ["evidence://invented"]})
    )
    dangling_meaning = snapshot.meaning_units[0].model_copy(
        update={"atoms": [unknown_atom, *snapshot.meaning_units[0].atoms[1:]]}
    )
    with pytest.raises(ValidationError, match="SEMANTIC_ATOM_SOURCE_REF_UNDECLARED"):
        _snapshot_variant(
            snapshot,
            snapshot_id="semantic-snapshot-dangling-atom-provenance",
            meaning_units=[dangling_meaning],
        )
    with pytest.raises(ValidationError, match="SEMANTIC_ATOM_SOURCE_REF_INVALID"):
        SemanticAtom(
            atom_id="duplicate-source",
            kind=SemanticAtomKind.CLAIM,
            value="Duplicate provenance is not an independent source.",
            source_refs=["evidence://approved/recovery"] * 2,
        )
    with pytest.raises(ValidationError, match="SEMANTIC_ATOM_SOURCE_REF_INVALID"):
        SemanticAtom(
            atom_id="empty-source",
            kind=SemanticAtomKind.CLAIM,
            value="An empty provenance ref is not a declared authority.",
            source_refs=[""],
        )

    source_free_meaning = SemanticMeaningUnit(
        meaning_id="meaning-approval-recovery",
        statement=snapshot.meaning_units[0].statement,
        factuality=Factuality.FACTUAL,
        evidence_requirement=EvidenceRequirement.NOT_REQUIRED,
        atoms=[
            atom.model_copy(update={"source_refs": []})
            for atom in snapshot.meaning_units[0].atoms
        ],
    )
    source_free_snapshot = _snapshot_variant(
        snapshot,
        snapshot_id="semantic-snapshot-source-free",
        meaning_units=[source_free_meaning],
    )
    assert all(
        not atom.source_refs for atom in source_free_snapshot.meaning_units[0].atoms
    )


def test_visual_reuse_signature_covers_material_relations_and_normalizes_set_order():
    baseline = _reuse_compatibility(
        (SemanticAtomKind.STATE, "fallback is bounded"),
        (SemanticAtomKind.CAUSAL_RELATION, "unowned exception causes delay"),
        (SemanticAtomKind.COMPARISON, "bounded fallback is safer than silent retry"),
        (SemanticAtomKind.TEMPORAL_RELATION, "after accountable approval"),
    )
    same_different_order = _reuse_compatibility(
        (SemanticAtomKind.TEMPORAL_RELATION, "after accountable approval"),
        (SemanticAtomKind.COMPARISON, "bounded fallback is safer than silent retry"),
        (SemanticAtomKind.CAUSAL_RELATION, "unowned exception causes delay"),
        (SemanticAtomKind.STATE, "fallback is bounded"),
        subject_refs=["approval recovery"],
        context_refs=["operational workflow"],
    )

    assert visual_reuse_compatible(baseline, same_different_order) is True
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is bounded"),
                (SemanticAtomKind.CAUSAL_RELATION, "delay causes unowned exception"),
                (
                    SemanticAtomKind.COMPARISON,
                    "bounded fallback is safer than silent retry",
                ),
                (SemanticAtomKind.TEMPORAL_RELATION, "after accountable approval"),
            ),
        )
        is False
    )
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is unbounded"),
                (SemanticAtomKind.CAUSAL_RELATION, "unowned exception causes delay"),
                (
                    SemanticAtomKind.COMPARISON,
                    "bounded fallback is safer than silent retry",
                ),
                (SemanticAtomKind.TEMPORAL_RELATION, "after accountable approval"),
            ),
        )
        is False
    )
    change_baseline = _reuse_compatibility(
        (SemanticAtomKind.CHANGE, "fallback becomes bounded"),
    )
    assert (
        visual_reuse_compatible(
            change_baseline,
            _reuse_compatibility(
                (SemanticAtomKind.CHANGE, "fallback becomes unbounded"),
            ),
        )
        is False
    )
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is bounded"),
                (SemanticAtomKind.CAUSAL_RELATION, "unowned exception causes delay"),
                (
                    SemanticAtomKind.COMPARISON,
                    "silent retry is safer than bounded fallback",
                ),
                (SemanticAtomKind.TEMPORAL_RELATION, "after accountable approval"),
            ),
        )
        is False
    )
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is bounded"),
                (SemanticAtomKind.CAUSAL_RELATION, "unowned exception causes delay"),
                (
                    SemanticAtomKind.COMPARISON,
                    "bounded fallback is safer than silent retry",
                ),
                (SemanticAtomKind.TEMPORAL_RELATION, "before accountable approval"),
            ),
        )
        is False
    )

    state_and_comparison_only = _reuse_compatibility(
        (SemanticAtomKind.STATE, "fallback is bounded"),
        (SemanticAtomKind.COMPARISON, "fallback is safer than retry"),
    )
    assert state_and_comparison_only.reuse_eligible is True
    assert "visual_function" not in VisualReuseCompatibility.model_fields
    assert "adjacent" not in VisualReuseCompatibility.model_fields


def test_snapshot_reference_and_nested_signature_drift_fail_closed():
    snapshot = _snapshot()
    invalid_trigger = snapshot.temporal_bindings[0].model_copy(
        update={"authored_semantic_trigger_ref": "meaning-approval-recovery#missing"}
    )
    with pytest.raises(ValidationError, match="AUTHORED_SEMANTIC_TRIGGER_REF_UNKNOWN"):
        _snapshot_variant(
            snapshot,
            snapshot_id="semantic-snapshot-invalid-trigger",
            temporal_bindings=[invalid_trigger],
        )

    authored_ref = snapshot.temporal_bindings[
        0
    ].presentation_intent.editorial_authority_ref
    invalid_overlay = OverlaySemanticIntent(
        overlay_state=OverlayState.OVERLAY,
        semantic_owner_ref="meaning-approval-recovery",
        presentation_intent=PresentationSemanticIntent(
            outcome=PresentationOutcome.PRESENTATION_CHANGE,
            semantic_role=SemanticPresentationRole.LABEL,
            editorial_reason="Label the authored approval boundary.",
            editorial_authority_ref=authored_ref,
        ),
        overlay_role="LABEL",
        information_purpose="Identify the boundary that requires approval.",
        target_refs=["meaning-approval-recovery#missing"],
        continuity_or_change_reason="The label clarifies the authored boundary.",
    )
    with pytest.raises(ValidationError, match="SEMANTIC_OVERLAY_TARGET_REF_UNKNOWN"):
        _snapshot_variant(
            snapshot,
            snapshot_id="semantic-snapshot-invalid-overlay-target",
            overlay_intents=[invalid_overlay],
        )

    atom_drift = _snapshot()
    atom_drift.meaning_units[0].atoms[0].source_refs.append("evidence://invented")
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        atom_drift.verify_integrity()

    factuality_drift = _snapshot()
    factuality_drift.meaning_units[0] = factuality_drift.meaning_units[0].model_copy(
        update={"factuality": Factuality.HYPOTHETICAL}
    )
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        factuality_drift.verify_integrity()

    evidence_drift = _snapshot()
    evidence_drift.meaning_units[0] = evidence_drift.meaning_units[0].model_copy(
        update={"evidence_requirement": EvidenceRequirement.NOT_REQUIRED}
    )
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        evidence_drift.verify_integrity()

    authority_drift = _snapshot()
    authority_drift.source_authorities[0] = authority_drift.source_authorities[
        0
    ].model_copy(update={"content_hash": "f" * 64})
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        authority_drift.verify_integrity()

    reuse = SemanticProjectionCompiler.visual(_snapshot()).reuse_compatibility[0]
    signature_drift = reuse.model_copy(
        update={
            "semantic_signature_facts": [
                VisualReuseSemanticFact(
                    kind=SemanticAtomKind.STATE,
                    value="a changed state",
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        signature_drift.verify_integrity()

    writer = SemanticProjectionCompiler.writer(_snapshot())
    assert isinstance(writer, WriterSemanticProjection)
    with pytest.raises(
        ValidationError, match="SEMANTIC_PROJECTION_EXTENSION_OWNER_UNKNOWN"
    ):
        WriterSemanticProjection.build(
            semantic_snapshot_ref=writer.semantic_snapshot_ref,
            semantic_snapshot_hash=writer.semantic_snapshot_hash,
            writer_units=writer.writer_units,
            extensions=[
                SemanticExtensionPayload(
                    extension_definition_id="channel-extension://operator-notes/decision",
                    semantic_owner_ref="meaning-unknown",
                    values={"central_question": "Unknown semantic owner."},
                )
            ],
        )


@pytest.mark.parametrize(
    ("outcome", "semantic_role"),
    [
        (PresentationOutcome.PRESENTATION_CHANGE, SemanticPresentationRole.HOLD),
        (PresentationOutcome.PRESENTATION_CHANGE, None),
        (PresentationOutcome.HOLD, SemanticPresentationRole.REVEAL),
        (PresentationOutcome.NO_VISUAL_CHANGE, SemanticPresentationRole.COMPARE),
    ],
)
def test_presentation_outcome_role_matrix_blocks_contradictions(
    outcome: PresentationOutcome,
    semantic_role: SemanticPresentationRole | None,
):
    with pytest.raises(
        ValidationError, match="SEMANTIC_PRESENTATION_ROLE_OUTCOME_INVALID"
    ):
        PresentationSemanticIntent(
            outcome=outcome,
            semantic_role=semantic_role,
            editorial_reason="Exercise the canonical outcome-role matrix.",
            editorial_authority_ref=f"editorial-authorship://{'a' * 64}",
        )


@pytest.mark.parametrize(
    ("outcome", "semantic_role"),
    [
        (PresentationOutcome.PRESENTATION_CHANGE, SemanticPresentationRole.REVEAL),
        (PresentationOutcome.HOLD, SemanticPresentationRole.HOLD),
        (PresentationOutcome.HOLD, None),
        (PresentationOutcome.NO_VISUAL_CHANGE, SemanticPresentationRole.HOLD),
        (PresentationOutcome.NO_VISUAL_CHANGE, None),
    ],
)
def test_presentation_outcome_role_matrix_accepts_canonical_combinations(
    outcome: PresentationOutcome,
    semantic_role: SemanticPresentationRole | None,
):
    intent = PresentationSemanticIntent(
        outcome=outcome,
        semantic_role=semantic_role,
        editorial_reason="Exercise the canonical outcome-role matrix.",
        editorial_authority_ref=f"editorial-authorship://{'a' * 64}",
    )

    assert intent.semantic_role == semantic_role


@pytest.mark.parametrize(
    ("semantic_boundary_ref", "viewer_beat_ref", "technical_segment_ref"),
    [
        ("identity://same", "identity://same", None),
        ("identity://same", None, "identity://same"),
        (None, "identity://same", "identity://same"),
    ],
)
def test_temporal_semantic_identities_are_pairwise_distinct(
    semantic_boundary_ref: str | None,
    viewer_beat_ref: str | None,
    technical_segment_ref: str | None,
):
    with pytest.raises(
        ValidationError, match="TEMPORAL_SEMANTIC_IDENTITIES_NOT_PAIRWISE_DISTINCT"
    ):
        TemporalSemanticBinding(
            semantic_owner_ref="meaning-approval-recovery",
            authored_semantic_trigger_ref="meaning-approval-recovery#state",
            presentation_intent=_snapshot().temporal_bindings[0].presentation_intent,
            semantic_boundary_ref=semantic_boundary_ref,
            viewer_beat_ref=viewer_beat_ref,
            technical_segment_ref=technical_segment_ref,
        )


@pytest.mark.parametrize(
    ("semantic_boundary_ref", "viewer_beat_ref", "technical_segment_ref"),
    [
        ("semantic://boundary", None, None),
        (None, "viewer://beat", None),
        (None, None, "technical://segment"),
        (None, "viewer://beat", "technical://segment"),
        ("semantic://boundary", "viewer://beat", "technical://segment"),
    ],
)
def test_distinct_temporal_semantic_identity_combinations_remain_valid(
    semantic_boundary_ref: str | None,
    viewer_beat_ref: str | None,
    technical_segment_ref: str | None,
):
    binding = TemporalSemanticBinding(
        semantic_owner_ref="meaning-approval-recovery",
        authored_semantic_trigger_ref="meaning-approval-recovery#state",
        presentation_intent=_snapshot().temporal_bindings[0].presentation_intent,
        semantic_boundary_ref=semantic_boundary_ref,
        viewer_beat_ref=viewer_beat_ref,
        technical_segment_ref=technical_segment_ref,
    )

    assert len(
        {
            ref
            for ref in (
                binding.semantic_boundary_ref,
                binding.viewer_beat_ref,
                binding.technical_segment_ref,
            )
            if ref is not None
        }
    ) == sum(
        ref is not None
        for ref in (
            binding.semantic_boundary_ref,
            binding.viewer_beat_ref,
            binding.technical_segment_ref,
        )
    )


def test_temporal_trigger_must_resolve_to_its_declared_owner():
    snapshot = _snapshot()
    valid_binding = snapshot.temporal_bindings[0].model_copy(
        update={"authored_semantic_trigger_ref": "meaning-approval-recovery#state"}
    )
    valid = _snapshot_variant(
        snapshot,
        snapshot_id="semantic-snapshot-owner-bound-trigger",
        temporal_bindings=[valid_binding],
    )
    other_meaning = SemanticMeaningUnit(
        meaning_id="meaning-other",
        statement="A different meaning owns this state.",
        factuality=Factuality.FACTUAL,
        evidence_requirement=EvidenceRequirement.NOT_REQUIRED,
        atoms=[
            SemanticAtom(
                atom_id="state",
                kind=SemanticAtomKind.STATE,
                value="a distinct state",
            )
        ],
    )
    cross_owner_binding = valid_binding.model_copy(
        update={"authored_semantic_trigger_ref": "meaning-other#state"}
    )

    assert valid.temporal_bindings[0].semantic_owner_ref == "meaning-approval-recovery"
    with pytest.raises(
        ValidationError, match="AUTHORED_SEMANTIC_TRIGGER_OWNER_MISMATCH"
    ):
        _snapshot_variant(
            snapshot,
            snapshot_id="semantic-snapshot-cross-owner-trigger",
            meaning_units=[*snapshot.meaning_units, other_meaning],
            temporal_bindings=[cross_owner_binding],
        )
    with pytest.raises(ValidationError, match="AUTHORED_SEMANTIC_TRIGGER_REF_UNKNOWN"):
        _snapshot_variant(
            snapshot,
            snapshot_id="semantic-snapshot-unknown-owner-trigger",
            temporal_bindings=[
                valid_binding.model_copy(
                    update={
                        "authored_semantic_trigger_ref": (
                            "meaning-approval-recovery#unknown"
                        )
                    }
                )
            ],
        )
    with pytest.raises(
        ValidationError, match="AUTHORED_SEMANTIC_TRIGGER_POSITIONAL_FORBIDDEN"
    ):
        TemporalSemanticBinding(
            semantic_owner_ref="meaning-approval-recovery",
            authored_semantic_trigger_ref="position-3",
            presentation_intent=valid_binding.presentation_intent,
        )


@pytest.mark.parametrize(
    "field_key",
    [kind.value.lower() for kind in SemanticAtomKind]
    + ["factuality", "evidence_requirement"],
)
def test_extensions_cannot_shadow_any_canonical_kernel_owner(field_key: str):
    with pytest.raises(
        ValidationError, match="SEMANTIC_EXTENSION_CANONICAL_FIELD_RESERVED"
    ):
        SemanticExtensionDefinition.build(
            extension_definition_id=f"channel-extension://invalid/{field_key}",
            scope=SemanticDefinitionScope.CHANNEL,
            definition_version="v1",
            field_keys=[field_key],
        )


def test_profile_specific_extension_fields_remain_allowed():
    definition = SemanticExtensionDefinition.build(
        extension_definition_id="channel-extension://podcast/speaker",
        scope=SemanticDefinitionScope.CHANNEL,
        definition_version="v1",
        field_keys=["speaker_role"],
    )

    assert definition.field_keys == ["speaker_role"]


def test_visual_reuse_compares_evidence_and_representation_constraints():
    constraints = [
        (SemanticAtomKind.MUST_PRESERVE, "the accountable approval boundary"),
        (SemanticAtomKind.MAY_ABSTRACT, "the concrete product interface"),
        (SemanticAtomKind.MUST_NOT_INVENT, "an autonomous approval decision"),
    ]
    baseline = _reuse_compatibility(
        (SemanticAtomKind.STATE, "fallback is bounded"),
        representation_constraints=constraints,
    )
    reordered = _reuse_compatibility(
        (SemanticAtomKind.STATE, "fallback is bounded"),
        representation_constraints=list(reversed(constraints)),
    )

    assert visual_reuse_compatible(baseline, reordered) is True
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is bounded"),
                evidence_requirement=EvidenceRequirement.NOT_REQUIRED,
                representation_constraints=constraints,
            ),
        )
        is False
    )
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is bounded"),
                representation_constraints=[
                    (
                        SemanticAtomKind.MUST_PRESERVE,
                        "only a generic recovery state",
                    ),
                    *constraints[1:],
                ],
            ),
        )
        is False
    )
    assert (
        visual_reuse_compatible(
            baseline,
            _reuse_compatibility(
                (SemanticAtomKind.STATE, "fallback is bounded"),
                representation_constraints=constraints[:-1],
            ),
        )
        is False
    )

    compiled = SemanticProjectionCompiler.visual(_snapshot()).reuse_compatibility[0]
    assert compiled.evidence_requirement == EvidenceRequirement.REQUIRED
    assert {fact.kind for fact in compiled.representation_constraints} == {
        SemanticAtomKind.MUST_PRESERVE,
        SemanticAtomKind.MAY_ABSTRACT,
        SemanticAtomKind.MUST_NOT_INVENT,
    }


def test_visual_reuse_authority_is_exactly_one_record_per_visual_owner():
    projection = SemanticProjectionCompiler.visual(_snapshot())
    assert isinstance(projection, VisualSemanticProjection)

    def rebuild(records: list[VisualReuseCompatibility]):
        return VisualSemanticProjection.build(
            semantic_snapshot_ref=projection.semantic_snapshot_ref,
            semantic_snapshot_hash=projection.semantic_snapshot_hash,
            visual_units=projection.visual_units,
            reuse_compatibility=records,
            temporal_bindings=projection.temporal_bindings,
            overlay_intents=projection.overlay_intents,
            extensions=projection.extensions,
        )

    compatibility = projection.reuse_compatibility[0]
    assert {unit.semantic_owner_ref for unit in projection.visual_units} == {
        item.semantic_owner_ref for item in projection.reuse_compatibility
    }
    with pytest.raises(ValidationError, match="VISUAL_REUSE_OWNER_1_TO_1_INVALID"):
        rebuild([compatibility, compatibility])
    with pytest.raises(ValidationError, match="VISUAL_REUSE_OWNER_1_TO_1_INVALID"):
        rebuild([])
    with pytest.raises(ValidationError, match="VISUAL_REUSE_OWNER_1_TO_1_INVALID"):
        rebuild(
            [
                compatibility,
                _reuse_compatibility(
                    (SemanticAtomKind.STATE, "an unrelated state"),
                    semantic_owner_ref="meaning-unknown",
                ),
            ]
        )


def test_learning_required_fails_at_profile_compilation_without_feature_authority():
    base_format = _explainer_format()
    empty_kernel = SemanticKernelDefinition.build(global_feature_definitions=[])
    empty_channel = _small_team_channel_variant(comparison_feature_definitions=[])

    def format_with(
        required: list[SemanticProjectionFamily],
        definitions: list[ComparisonFeatureDefinition],
    ) -> FormatSemanticProfile:
        return FormatSemanticProfile.build(
            format_authority=base_format.format_authority,
            semantic_definition_version=base_format.semantic_definition_version,
            required_projection_families=required,
            extension_definitions=base_format.extension_definitions,
            comparison_feature_definitions=definitions,
        )

    with pytest.raises(
        ValidationError, match="LEARNING_REQUIRED_COMPARISON_AUTHORITY_MISSING"
    ):
        SemanticProfileCompiler.compile(
            kernel=empty_kernel,
            channel_profile=empty_channel,
            format_profile=format_with([SemanticProjectionFamily.LEARNING], []),
        )

    controlled = SemanticProfileCompiler.compile(
        kernel=empty_kernel,
        channel_profile=empty_channel,
        format_profile=format_with(
            [SemanticProjectionFamily.LEARNING],
            [
                _feature(
                    "learning_signal",
                    ComparisonFeatureScope.FORMAT,
                    ["PRESENT"],
                )
            ],
        ),
    )
    not_required = SemanticProfileCompiler.compile(
        kernel=empty_kernel,
        channel_profile=empty_channel,
        format_profile=format_with([SemanticProjectionFamily.WRITER], []),
    )

    assert len(controlled.feature_definitions) == 1
    assert not_required.feature_definitions == ()


def test_overlay_targets_must_resolve_to_their_declared_owner():
    snapshot = _snapshot()
    other_meaning = SemanticMeaningUnit(
        meaning_id="meaning-other",
        statement="A different meaning owns this overlay target.",
        factuality=Factuality.FACTUAL,
        evidence_requirement=EvidenceRequirement.NOT_REQUIRED,
        atoms=[
            SemanticAtom(
                atom_id="state",
                kind=SemanticAtomKind.STATE,
                value="a distinct state",
            )
        ],
    )
    cross_owner_overlay = OverlaySemanticIntent(
        overlay_state=OverlayState.OVERLAY,
        semantic_owner_ref="meaning-approval-recovery",
        presentation_intent=PresentationSemanticIntent(
            outcome=PresentationOutcome.PRESENTATION_CHANGE,
            semantic_role=SemanticPresentationRole.LABEL,
            editorial_reason="Label an authored semantic target.",
            editorial_authority_ref=(
                snapshot.temporal_bindings[
                    0
                ].presentation_intent.editorial_authority_ref
            ),
        ),
        overlay_role="LABEL",
        information_purpose="Identify the semantic state.",
        target_refs=["meaning-other#state"],
        continuity_or_change_reason="The label changes the authored presentation.",
    )

    with pytest.raises(ValidationError, match="SEMANTIC_OVERLAY_TARGET_OWNER_MISMATCH"):
        _snapshot_variant(
            snapshot,
            snapshot_id="semantic-snapshot-cross-owner-overlay",
            meaning_units=[*snapshot.meaning_units, other_meaning],
            overlay_intents=[cross_owner_overlay],
        )


def test_channel_and_format_authorities_cannot_share_one_identity_ref():
    format_profile = _explainer_format()
    colliding_channel = _small_team_channel_variant(
        authority=PolicyRef(
            ref=format_profile.format_authority.ref,
            version="3",
            content_hash="a" * 64,
        )
    )

    with pytest.raises(
        ValidationError, match="SEMANTIC_CHANNEL_FORMAT_AUTHORITY_COLLISION"
    ):
        SemanticProfileCompiler.compile(
            kernel=_kernel(),
            channel_profile=colliding_channel,
            format_profile=format_profile,
        )


def test_viewer_inference_is_part_of_visual_reuse_safety():
    baseline = _reuse_compatibility(
        (SemanticAtomKind.STATE, "fallback is bounded"),
        (SemanticAtomKind.VIEWER_INFERENCE, "approval remains human-owned"),
    )
    changed_inference = _reuse_compatibility(
        (SemanticAtomKind.STATE, "fallback is bounded"),
        (SemanticAtomKind.VIEWER_INFERENCE, "approval is autonomous"),
    )

    assert visual_reuse_compatible(baseline, changed_inference) is False


def test_sealed_builders_reject_unknown_fields_and_reuse_checks_integrity():
    with pytest.raises(ValueError, match="SEMANTIC_SEALED_FIELD_UNKNOWN:shadow"):
        SemanticKernelDefinition.build(global_feature_definitions=[], shadow="value")

    baseline = _reuse_compatibility((SemanticAtomKind.STATE, "fallback is bounded"))
    tampered = baseline.model_copy(update={"proposition": "tampered proposition"})
    with pytest.raises(ValueError, match="SEMANTIC_CONTENT_HASH_MISMATCH"):
        visual_reuse_compatible(baseline, tampered)
