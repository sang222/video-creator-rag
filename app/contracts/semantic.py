"""Card E canonical semantic language and purpose-specific projections.

The kernel in this module is deliberately small and channel-neutral.  It
stores rich episode meaning as typed atoms, while channel/format-specific
meaning remains in versioned extension definitions.  Consumers receive a
projection for their purpose instead of a shared all-purpose payload.

This contract does not author an episode, implement a renderer, or make
learning decisions.  It provides the sealed semantic authority those stages
can consume.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Iterable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.editorial_authorship import (
    EditorialAuthorshipContract,
    validate_viewer_facing_presentation,
)
from app.contracts.channel_policy import FormatIdentityBinding, PolicyRef


SEMANTIC_KERNEL_VERSION = "vcos.semantic-kernel.v1"
SEMANTIC_PROFILE_COMPILATION_VERSION = "vcos.semantic-profile-compilation.v1"
PROJECT_SEMANTIC_SNAPSHOT_VERSION = "vcos.project-semantic-snapshot.v1"
SEMANTIC_PROJECTION_VERSION = "vcos.semantic-projection.v1"
COMPARISON_FEATURE_VIEW_VERSION = "vcos.comparison-feature-view.v1"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
_KEY_PATTERN = r"^[a-z][a-z0-9_]{1,79}$"
_CONTROLLED_VALUE_PATTERN = r"^[A-Z][A-Z0-9_]{1,79}$"


def semantic_hash(value: Any) -> str:
    """Stable SHA-256 for every sealed Card E authority."""

    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sealed_body(model_type: type[BaseModel], values: dict[str, Any]) -> dict[str, Any]:
    """Materialize pydantic defaults before computing a sealed content hash."""

    instance = model_type.model_construct(**values)
    return instance.model_dump(mode="json", exclude={"content_hash"})


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def _seal(cls, **values: Any):
        body = _sealed_body(cls, values)
        return cls(**body, content_hash=semantic_hash(body))

    def verify_integrity(self) -> None:
        body = self.model_dump(mode="json", exclude={"content_hash"})
        if getattr(self, "content_hash", None) != semantic_hash(body):
            raise ValueError("SEMANTIC_CONTENT_HASH_MISMATCH")


class SemanticProjectionFamily(StrEnum):
    WRITER = "WRITER"
    VISUAL = "VISUAL"
    PACKAGING = "PACKAGING"
    QC = "QC"
    LEARNING = "LEARNING"


class SemanticDefinitionScope(StrEnum):
    CHANNEL = "CHANNEL"
    FORMAT = "FORMAT"


class ComparisonFeatureScope(StrEnum):
    GLOBAL = "GLOBAL"
    PROFILE = "PROFILE"
    FORMAT = "FORMAT"


class Applicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Factuality(StrEnum):
    FACTUAL = "FACTUAL"
    INFERRED = "INFERRED"
    HYPOTHETICAL = "HYPOTHETICAL"
    CREATIVE_ABSTRACTION = "CREATIVE_ABSTRACTION"


class EvidenceRequirement(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_REQUIRED = "NOT_REQUIRED"


class SemanticAtomKind(StrEnum):
    """Small, stable, cross-channel semantic primitives.

    Values are semantic meaning, not channel-specific concepts, format
    styling, renderer effects, timestamps, or provider primitives.
    """

    SUBJECT = "SUBJECT"
    ENTITY = "ENTITY"
    STATE = "STATE"
    ACTION = "ACTION"
    RELATIONSHIP = "RELATIONSHIP"
    CHANGE = "CHANGE"
    CAUSAL_RELATION = "CAUSAL_RELATION"
    COMPARISON = "COMPARISON"
    TEMPORAL_RELATION = "TEMPORAL_RELATION"
    CONTEXT = "CONTEXT"
    CLAIM = "CLAIM"
    EVIDENCE_REQUIREMENT = "EVIDENCE_REQUIREMENT"
    FACTUALITY = "FACTUALITY"
    EDITORIAL_FUNCTION = "EDITORIAL_FUNCTION"
    PREMISE = "PREMISE"
    AUDIENCE_PROBLEM = "AUDIENCE_PROBLEM"
    VIEWER_OR_LISTENER_STAKES = "VIEWER_OR_LISTENER_STAKES"
    PROMISE = "PROMISE"
    TENSION = "TENSION"
    PAYOFF = "PAYOFF"
    PRIOR_KNOWLEDGE_DEPENDENCY = "PRIOR_KNOWLEDGE_DEPENDENCY"
    COLD_VIEWER_BRIDGE = "COLD_VIEWER_BRIDGE"
    PACKAGING_BRIDGE = "PACKAGING_BRIDGE"
    VIEWER_INFERENCE = "VIEWER_INFERENCE"
    MUST_PRESERVE = "MUST_PRESERVE"
    MAY_ABSTRACT = "MAY_ABSTRACT"
    MUST_NOT_INVENT = "MUST_NOT_INVENT"


class SemanticPresentationRole(StrEnum):
    """Why a presentation changes, deliberately excluding renderer how-to."""

    ESTABLISH = "ESTABLISH"
    REVEAL = "REVEAL"
    FOCUS = "FOCUS"
    COMPARE = "COMPARE"
    PROGRESS = "PROGRESS"
    HOLD = "HOLD"
    SETTLE = "SETTLE"
    RESET = "RESET"
    STATE_CHANGE = "STATE_CHANGE"
    KEY_VALUE = "KEY_VALUE"
    LABEL = "LABEL"


class PresentationOutcome(StrEnum):
    HOLD = "HOLD"
    NO_VISUAL_CHANGE = "NO_VISUAL_CHANGE"
    PRESENTATION_CHANGE = "PRESENTATION_CHANGE"


class OverlayState(StrEnum):
    NO_OVERLAY = "NO_OVERLAY"
    OVERLAY = "OVERLAY"


class OverlayRole(StrEnum):
    LABEL = "LABEL"
    KEY_VALUE = "KEY_VALUE"
    CLARIFY = "CLARIFY"
    COMPARE = "COMPARE"
    ORIENT = "ORIENT"


_WRITER_ATOMS = frozenset(
    {
        SemanticAtomKind.CLAIM,
        SemanticAtomKind.EVIDENCE_REQUIREMENT,
        SemanticAtomKind.FACTUALITY,
        SemanticAtomKind.EDITORIAL_FUNCTION,
        SemanticAtomKind.PREMISE,
        SemanticAtomKind.AUDIENCE_PROBLEM,
        SemanticAtomKind.VIEWER_OR_LISTENER_STAKES,
        SemanticAtomKind.PROMISE,
        SemanticAtomKind.TENSION,
        SemanticAtomKind.PAYOFF,
        SemanticAtomKind.PRIOR_KNOWLEDGE_DEPENDENCY,
        SemanticAtomKind.COLD_VIEWER_BRIDGE,
    }
)
_VISUAL_ATOMS = frozenset(
    {
        SemanticAtomKind.SUBJECT,
        SemanticAtomKind.ENTITY,
        SemanticAtomKind.STATE,
        SemanticAtomKind.ACTION,
        SemanticAtomKind.RELATIONSHIP,
        SemanticAtomKind.CHANGE,
        SemanticAtomKind.CAUSAL_RELATION,
        SemanticAtomKind.COMPARISON,
        SemanticAtomKind.TEMPORAL_RELATION,
        SemanticAtomKind.CONTEXT,
        SemanticAtomKind.FACTUALITY,
        SemanticAtomKind.EVIDENCE_REQUIREMENT,
        SemanticAtomKind.VIEWER_INFERENCE,
        SemanticAtomKind.MUST_PRESERVE,
        SemanticAtomKind.MAY_ABSTRACT,
        SemanticAtomKind.MUST_NOT_INVENT,
    }
)
_PACKAGING_ATOMS = frozenset(
    {
        SemanticAtomKind.PREMISE,
        SemanticAtomKind.VIEWER_OR_LISTENER_STAKES,
        SemanticAtomKind.PROMISE,
        SemanticAtomKind.COLD_VIEWER_BRIDGE,
        SemanticAtomKind.PACKAGING_BRIDGE,
        SemanticAtomKind.PAYOFF,
    }
)
_QC_ATOMS = frozenset(
    {
        SemanticAtomKind.CLAIM,
        SemanticAtomKind.EVIDENCE_REQUIREMENT,
        SemanticAtomKind.FACTUALITY,
        SemanticAtomKind.MUST_PRESERVE,
        SemanticAtomKind.MUST_NOT_INVENT,
    }
)


class SemanticAtom(_StrictFrozen):
    atom_id: str = Field(min_length=1, max_length=160)
    kind: SemanticAtomKind
    value: str = Field(min_length=1, max_length=8_000)
    source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_atom(self) -> Self:
        if len(self.source_refs) != len(set(self.source_refs)) or any(
            not ref.strip() for ref in self.source_refs
        ):
            raise ValueError("SEMANTIC_ATOM_SOURCE_REF_INVALID")
        return self


class SemanticMeaningUnit(_StrictFrozen):
    """Episode-specific rich truth with an explicit, non-positional identity."""

    meaning_id: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=8_000)
    atoms: list[SemanticAtom] = Field(min_length=1)
    factuality: Factuality
    evidence_requirement: EvidenceRequirement

    @model_validator(mode="after")
    def valid_meaning(self) -> Self:
        atom_ids = [atom.atom_id for atom in self.atoms]
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("SEMANTIC_ATOM_ID_DUPLICATE")
        return self


class SemanticExtensionDefinition(_StrictFrozen):
    """A versioned profile/format extension without widening the kernel."""

    extension_definition_id: str = Field(min_length=1, max_length=200)
    scope: SemanticDefinitionScope
    definition_version: str = Field(min_length=1, max_length=120)
    field_keys: list[str] = Field(min_length=1, max_length=32)
    projection_families: list[SemanticProjectionFamily] = Field(default_factory=list)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_definition(self) -> Self:
        if (
            len(self.field_keys) != len(set(self.field_keys))
            or any(re.fullmatch(_KEY_PATTERN, key) is None for key in self.field_keys)
            or len(self.projection_families) != len(set(self.projection_families))
        ):
            raise ValueError("SEMANTIC_EXTENSION_DEFINITION_INVALID")
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "SemanticExtensionDefinition":
        return cls._seal(**values)


class ComparisonFeatureDefinition(_StrictFrozen):
    """A controlled-cardinality learning/comparison feature family."""

    feature_key: str = Field(pattern=_KEY_PATTERN)
    scope: ComparisonFeatureScope
    allowed_values: list[str] = Field(min_length=1, max_length=64)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_feature_definition(self) -> Self:
        if len(self.allowed_values) != len(set(self.allowed_values)) or any(
            re.fullmatch(_CONTROLLED_VALUE_PATTERN, item) is None
            for item in self.allowed_values
        ):
            raise ValueError("COMPARISON_FEATURE_CARDINALITY_INVALID")
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "ComparisonFeatureDefinition":
        return cls._seal(**values)


class SemanticKernelDefinition(_StrictFrozen):
    """Versioned kernel metadata plus globally shared comparison controls."""

    schema_version: Literal[SEMANTIC_KERNEL_VERSION] = SEMANTIC_KERNEL_VERSION
    kernel_version: str = Field(default=SEMANTIC_KERNEL_VERSION, min_length=1)
    global_feature_definitions: list[ComparisonFeatureDefinition] = Field(
        default_factory=list
    )
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_kernel(self) -> Self:
        if any(
            feature.scope != ComparisonFeatureScope.GLOBAL
            for feature in self.global_feature_definitions
        ):
            raise ValueError("SEMANTIC_KERNEL_GLOBAL_FEATURE_SCOPE_INVALID")
        keys = [item.feature_key for item in self.global_feature_definitions]
        if len(keys) != len(set(keys)):
            raise ValueError("SEMANTIC_KERNEL_FEATURE_KEY_DUPLICATE")
        for definition in self.global_feature_definitions:
            definition.verify_integrity()
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "SemanticKernelDefinition":
        return cls._seal(**values)


class ChannelSemanticProfile(_StrictFrozen):
    """Channel identity boundary.  It deliberately contains no format grammar."""

    schema_version: Literal["vcos.channel-semantic-profile.v1"] = (
        "vcos.channel-semantic-profile.v1"
    )
    # Reuse the canonical immutable authority-reference shape.  A mutable
    # channel-profile locator alone is insufficient semantic authority.
    channel_authority: PolicyRef
    semantic_definition_version: str = Field(min_length=1, max_length=120)
    extension_definitions: list[SemanticExtensionDefinition] = Field(
        default_factory=list
    )
    comparison_feature_definitions: list[ComparisonFeatureDefinition] = Field(
        default_factory=list
    )
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_channel_profile(self) -> Self:
        if any(
            definition.scope != SemanticDefinitionScope.CHANNEL
            for definition in self.extension_definitions
        ) or any(
            definition.scope != ComparisonFeatureScope.PROFILE
            for definition in self.comparison_feature_definitions
        ):
            raise ValueError("CHANNEL_SEMANTIC_PROFILE_SCOPE_INVALID")
        ids = [item.extension_definition_id for item in self.extension_definitions]
        keys = [item.feature_key for item in self.comparison_feature_definitions]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise ValueError("CHANNEL_SEMANTIC_PROFILE_IDENTITY_DUPLICATE")
        for definition in (
            self.extension_definitions + self.comparison_feature_definitions
        ):
            definition.verify_integrity()
        self.verify_integrity()
        return self

    @property
    def channel_profile_ref(self) -> str:
        """Compatibility accessor; identity is the complete authority binding."""

        return self.channel_authority.ref

    @classmethod
    def build(cls, **values: Any) -> "ChannelSemanticProfile":
        return cls._seal(**values)


class FormatSemanticProfile(_StrictFrozen):
    """Format grammar boundary, independent from channel identity and audience."""

    schema_version: Literal["vcos.format-semantic-profile.v1"] = (
        "vcos.format-semantic-profile.v1"
    )
    # This is the repository's approved FormatIdentityBinding, not a bare
    # format locator.  It binds ref, version, content hash, and approval.
    format_authority: FormatIdentityBinding
    semantic_definition_version: str = Field(min_length=1, max_length=120)
    required_projection_families: list[SemanticProjectionFamily] = Field(min_length=1)
    extension_definitions: list[SemanticExtensionDefinition] = Field(
        default_factory=list
    )
    comparison_feature_definitions: list[ComparisonFeatureDefinition] = Field(
        default_factory=list
    )
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_format_profile(self) -> Self:
        if any(
            definition.scope != SemanticDefinitionScope.FORMAT
            for definition in self.extension_definitions
        ) or any(
            definition.scope != ComparisonFeatureScope.FORMAT
            for definition in self.comparison_feature_definitions
        ):
            raise ValueError("FORMAT_SEMANTIC_PROFILE_SCOPE_INVALID")
        projections = self.required_projection_families
        ids = [item.extension_definition_id for item in self.extension_definitions]
        keys = [item.feature_key for item in self.comparison_feature_definitions]
        if (
            len(projections) != len(set(projections))
            or len(ids) != len(set(ids))
            or len(keys) != len(set(keys))
        ):
            raise ValueError("FORMAT_SEMANTIC_PROFILE_IDENTITY_DUPLICATE")
        for definition in (
            self.extension_definitions + self.comparison_feature_definitions
        ):
            definition.verify_integrity()
        self.verify_integrity()
        return self

    @property
    def format_profile_ref(self) -> str:
        """Compatibility accessor; identity is the complete authority binding."""

        return self.format_authority.ref

    @classmethod
    def build(cls, **values: Any) -> "FormatSemanticProfile":
        return cls._seal(**values)


class SemanticProfileCompilation(_StrictFrozen):
    """Immutable combination of one kernel, one channel, and one format."""

    schema_version: Literal[SEMANTIC_PROFILE_COMPILATION_VERSION] = (
        SEMANTIC_PROFILE_COMPILATION_VERSION
    )
    kernel: SemanticKernelDefinition
    channel_profile: ChannelSemanticProfile
    format_profile: FormatSemanticProfile
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @property
    def feature_definitions(self) -> tuple[ComparisonFeatureDefinition, ...]:
        return tuple(
            self.kernel.global_feature_definitions
            + self.channel_profile.comparison_feature_definitions
            + self.format_profile.comparison_feature_definitions
        )

    @property
    def extension_definitions(self) -> tuple[SemanticExtensionDefinition, ...]:
        return tuple(
            self.channel_profile.extension_definitions
            + self.format_profile.extension_definitions
        )

    @model_validator(mode="after")
    def valid_compilation(self) -> Self:
        self.kernel.verify_integrity()
        self.channel_profile.verify_integrity()
        self.format_profile.verify_integrity()
        feature_keys = [item.feature_key for item in self.feature_definitions]
        extension_ids = [
            item.extension_definition_id for item in self.extension_definitions
        ]
        if len(feature_keys) != len(set(feature_keys)):
            raise ValueError("SEMANTIC_COMPILATION_FEATURE_KEY_COLLISION")
        if len(extension_ids) != len(set(extension_ids)):
            raise ValueError("SEMANTIC_COMPILATION_EXTENSION_ID_COLLISION")
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "SemanticProfileCompilation":
        return cls._seal(**values)


class SemanticAuthorityRef(_StrictFrozen):
    authority_type: str = Field(min_length=1, max_length=120)
    authority_ref: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_authority_ref(self) -> Self:
        # Card D contracts have a canonical immutable ref.  This check does
        # not turn evidence into authorship; snapshot validation below decides
        # which source types can authorize presentation intent.
        if self.authority_type == "EDITORIAL_AUTHORSHIP_CONTRACT" and (
            self.authority_ref != f"editorial-authorship://{self.content_hash}"
        ):
            raise ValueError("EDITORIAL_AUTHORSHIP_AUTHORITY_BINDING_INVALID")
        return self


class SemanticExtensionPayload(_StrictFrozen):
    extension_definition_id: str = Field(min_length=1, max_length=200)
    semantic_owner_ref: str = Field(min_length=1, max_length=200)
    values: dict[str, Any] = Field(min_length=1)


class PresentationSemanticIntent(_StrictFrozen):
    """An authored semantic why; it has no renderer primitive or timing field."""

    outcome: PresentationOutcome
    semantic_role: SemanticPresentationRole | None = None
    editorial_reason: str = Field(min_length=1, max_length=4_000)
    editorial_authority_ref: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid_authorship_law(self) -> Self:
        if (
            self.outcome == PresentationOutcome.PRESENTATION_CHANGE
            and self.semantic_role is None
        ) or (
            self.outcome != PresentationOutcome.PRESENTATION_CHANGE
            and self.semantic_role not in {None, SemanticPresentationRole.HOLD}
        ):
            raise ValueError("SEMANTIC_PRESENTATION_ROLE_OUTCOME_INVALID")
        # Reuse Card D's law rather than re-declaring a competing rule set.
        validate_viewer_facing_presentation(
            {
                "outcome": self.outcome.value,
                "editorial_reason": self.editorial_reason,
                "editorial_authority_ref": self.editorial_authority_ref,
            }
        )
        return self


class TemporalSemanticBinding(_StrictFrozen):
    """Keep semantic boundaries, viewer beats, and technical segments distinct."""

    semantic_owner_ref: str = Field(min_length=1, max_length=200)
    authored_semantic_trigger_ref: str = Field(min_length=1, max_length=300)
    presentation_intent: PresentationSemanticIntent
    semantic_boundary_ref: str | None = Field(
        default=None, min_length=1, max_length=300
    )
    viewer_beat_ref: str | None = Field(default=None, min_length=1, max_length=300)
    technical_segment_ref: str | None = Field(
        default=None, min_length=1, max_length=300
    )

    @model_validator(mode="after")
    def valid_temporal_binding(self) -> Self:
        if re.fullmatch(
            r"(?:(?:position|index|ordinal|slot)[-_:/]?)?\d+",
            self.authored_semantic_trigger_ref.strip(),
            flags=re.IGNORECASE,
        ):
            raise ValueError("AUTHORED_SEMANTIC_TRIGGER_POSITIONAL_FORBIDDEN")
        if self.semantic_boundary_ref and self.semantic_boundary_ref in {
            self.viewer_beat_ref,
            self.technical_segment_ref,
        }:
            raise ValueError("SEMANTIC_BOUNDARY_PRESENTATION_OR_TECHNICAL_COLLISION")
        return self

    @property
    def semantic_boundary_id(self) -> str | None:
        """Compatibility accessor; a boundary is intentionally optional."""

        return self.semantic_boundary_ref


class OverlaySemanticIntent(_StrictFrozen):
    """Semantic overlay purpose only; display copy, geometry, and time are out of scope."""

    overlay_state: OverlayState
    semantic_owner_ref: str = Field(min_length=1, max_length=200)
    presentation_intent: PresentationSemanticIntent
    overlay_role: OverlayRole | None = None
    information_purpose: str | None = Field(
        default=None, min_length=1, max_length=4_000
    )
    target_refs: list[str] = Field(default_factory=list)
    continuity_or_change_reason: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def valid_overlay_intent(self) -> Self:
        if self.overlay_state == OverlayState.NO_OVERLAY:
            if (
                self.overlay_role is not None
                or self.information_purpose is not None
                or self.target_refs
            ):
                raise ValueError("NO_OVERLAY_PAYLOAD_FORBIDDEN")
            if self.presentation_intent.outcome not in {
                PresentationOutcome.HOLD,
                PresentationOutcome.NO_VISUAL_CHANGE,
            }:
                raise ValueError("NO_OVERLAY_PRESENTATION_OUTCOME_INVALID")
        elif (
            self.overlay_role is None
            or self.information_purpose is None
            or not self.target_refs
        ):
            raise ValueError("OVERLAY_SEMANTIC_PURPOSE_REQUIRED")
        if len(self.target_refs) != len(set(self.target_refs)):
            raise ValueError("OVERLAY_TARGET_REF_DUPLICATE")
        return self


class ProjectRichSemanticSnapshot(_StrictFrozen):
    """Sealed project truth.  Its full hash is never a learning identity."""

    schema_version: Literal[PROJECT_SEMANTIC_SNAPSHOT_VERSION] = (
        PROJECT_SEMANTIC_SNAPSHOT_VERSION
    )
    snapshot_id: str = Field(min_length=1, max_length=200)
    project_ref: str = Field(min_length=1, max_length=300)
    revision_ref: str = Field(min_length=1, max_length=300)
    semantic_profile: SemanticProfileCompilation
    source_authorities: list[SemanticAuthorityRef] = Field(min_length=1)
    meaning_units: list[SemanticMeaningUnit] = Field(min_length=1)
    extensions: list[SemanticExtensionPayload] = Field(default_factory=list)
    temporal_bindings: list[TemporalSemanticBinding] = Field(default_factory=list)
    overlay_intents: list[OverlaySemanticIntent] = Field(default_factory=list)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_snapshot(self) -> Self:
        self.semantic_profile.verify_integrity()
        meaning_ids = [item.meaning_id for item in self.meaning_units]
        authority_refs = [item.authority_ref for item in self.source_authorities]
        if len(meaning_ids) != len(set(meaning_ids)):
            raise ValueError("SEMANTIC_MEANING_ID_DUPLICATE")
        if len(authority_refs) != len(set(authority_refs)):
            raise ValueError("SEMANTIC_SOURCE_AUTHORITY_REF_DUPLICATE")
        authored_presentation_refs = {
            item.authority_ref
            for item in self.source_authorities
            if item.authority_type == "EDITORIAL_AUTHORSHIP_CONTRACT"
        }
        if not authored_presentation_refs:
            raise ValueError("SEMANTIC_AUTHORSHIP_AUTHORITY_REQUIRED")

        allowed_extension_defs = {
            item.extension_definition_id: item
            for item in self.semantic_profile.extension_definitions
        }
        extension_identity: set[tuple[str, str]] = set()
        for extension in self.extensions:
            definition = allowed_extension_defs.get(extension.extension_definition_id)
            if definition is None:
                raise ValueError("SEMANTIC_EXTENSION_DEFINITION_UNDECLARED")
            if not set(extension.values).issubset(definition.field_keys):
                raise ValueError("SEMANTIC_EXTENSION_FIELD_UNDECLARED")
            identity = (
                extension.extension_definition_id,
                extension.semantic_owner_ref,
            )
            if identity in extension_identity:
                raise ValueError("SEMANTIC_EXTENSION_OWNER_DUPLICATE")
            extension_identity.add(identity)

        owner_refs = {
            *(binding.semantic_owner_ref for binding in self.temporal_bindings),
            *(intent.semantic_owner_ref for intent in self.overlay_intents),
            *(extension.semantic_owner_ref for extension in self.extensions),
        }
        if not owner_refs.issubset(set(meaning_ids)):
            raise ValueError("SEMANTIC_OWNER_REF_UNKNOWN_OR_POSITIONAL")
        authored_refs = {
            binding.presentation_intent.editorial_authority_ref
            for binding in self.temporal_bindings
        } | {
            intent.presentation_intent.editorial_authority_ref
            for intent in self.overlay_intents
        }
        if not authored_refs.issubset(authored_presentation_refs):
            raise ValueError("SEMANTIC_PRESENTATION_AUTHORITY_NOT_AUTHORED")
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "ProjectRichSemanticSnapshot":
        return cls._seal(**values)

    @classmethod
    def build_from_authorship_contract(
        cls,
        *,
        editorial_authorship_contract: EditorialAuthorshipContract,
        source_authorities: Iterable[SemanticAuthorityRef] = (),
        **values: Any,
    ) -> "ProjectRichSemanticSnapshot":
        authorship_ref = (
            f"editorial-authorship://{editorial_authorship_contract.content_hash}"
        )
        authorities = list(source_authorities)
        authorities.append(
            SemanticAuthorityRef(
                authority_type="EDITORIAL_AUTHORSHIP_CONTRACT",
                authority_ref=authorship_ref,
                content_hash=editorial_authorship_contract.content_hash,
            )
        )
        return cls.build(source_authorities=authorities, **values)


class ProjectedSemanticUnit(_StrictFrozen):
    semantic_owner_ref: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    atoms: list[SemanticAtom] = Field(default_factory=list)


class VisualReuseCompatibility(_StrictFrozen):
    """Minimum semantic evidence required before a later stage may reuse a visual.

    This is intentionally a compatibility record, not a scene-grouping
    algorithm or a renderer instruction.  A caller cannot infer equivalence
    from adjacency, a scene ordinal, or a visual-function label alone.
    """

    semantic_owner_ref: str = Field(min_length=1)
    subject_refs: list[str] = Field(default_factory=list)
    proposition: str = Field(min_length=1)
    action_or_relationships: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    factuality: Factuality
    reuse_eligible: bool
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_compatibility(self) -> Self:
        expected = bool(
            self.subject_refs and self.action_or_relationships and self.context_refs
        )
        if self.reuse_eligible != expected:
            raise ValueError("VISUAL_REUSE_COMPATIBILITY_INCOMPLETE")
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "VisualReuseCompatibility":
        return cls._seal(**values)


def visual_reuse_compatible(
    left: VisualReuseCompatibility,
    right: VisualReuseCompatibility,
) -> bool:
    """Strict semantic gate for a future grouping/reuse implementation."""

    return bool(
        left.reuse_eligible
        and right.reuse_eligible
        and left.subject_refs == right.subject_refs
        and left.proposition == right.proposition
        and left.action_or_relationships == right.action_or_relationships
        and left.context_refs == right.context_refs
        and left.factuality == right.factuality
    )


class ProjectionNotApplicable(_StrictFrozen):
    schema_version: Literal[SEMANTIC_PROJECTION_VERSION] = SEMANTIC_PROJECTION_VERSION
    projection_family: SemanticProjectionFamily
    applicability: Literal[Applicability.NOT_APPLICABLE] = Applicability.NOT_APPLICABLE
    semantic_snapshot_ref: str = Field(min_length=1)
    semantic_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    reason: Literal["FORMAT_NOT_APPLICABLE"] = "FORMAT_NOT_APPLICABLE"
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_hash(self) -> Self:
        self.verify_integrity()
        return self

    @classmethod
    def build(cls, **values: Any) -> "ProjectionNotApplicable":
        return cls._seal(**values)


class _Projection(_StrictFrozen):
    schema_version: Literal[SEMANTIC_PROJECTION_VERSION] = SEMANTIC_PROJECTION_VERSION
    projection_family: SemanticProjectionFamily
    applicability: Literal[Applicability.APPLICABLE] = Applicability.APPLICABLE
    semantic_snapshot_ref: str = Field(min_length=1)
    semantic_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    extensions: list[SemanticExtensionPayload] = Field(default_factory=list)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_hash(self) -> Self:
        self.verify_integrity()
        return self


class WriterSemanticProjection(_Projection):
    projection_family: Literal[SemanticProjectionFamily.WRITER] = (
        SemanticProjectionFamily.WRITER
    )
    writer_units: list[ProjectedSemanticUnit] = Field(default_factory=list)

    @classmethod
    def build(cls, **values: Any) -> "WriterSemanticProjection":
        return cls._seal(**values)


class VisualSemanticProjection(_Projection):
    projection_family: Literal[SemanticProjectionFamily.VISUAL] = (
        SemanticProjectionFamily.VISUAL
    )
    visual_units: list[ProjectedSemanticUnit] = Field(default_factory=list)
    reuse_compatibility: list[VisualReuseCompatibility] = Field(default_factory=list)
    temporal_bindings: list[TemporalSemanticBinding] = Field(default_factory=list)
    overlay_intents: list[OverlaySemanticIntent] = Field(default_factory=list)

    @classmethod
    def build(cls, **values: Any) -> "VisualSemanticProjection":
        return cls._seal(**values)


class PackagingSemanticProjection(_Projection):
    projection_family: Literal[SemanticProjectionFamily.PACKAGING] = (
        SemanticProjectionFamily.PACKAGING
    )
    packaging_units: list[ProjectedSemanticUnit] = Field(default_factory=list)

    @classmethod
    def build(cls, **values: Any) -> "PackagingSemanticProjection":
        return cls._seal(**values)


class QCSemanticProjection(_Projection):
    projection_family: Literal[SemanticProjectionFamily.QC] = (
        SemanticProjectionFamily.QC
    )
    qc_units: list[ProjectedSemanticUnit] = Field(default_factory=list)

    @classmethod
    def build(cls, **values: Any) -> "QCSemanticProjection":
        return cls._seal(**values)


class ComparisonFeatureValue(_StrictFrozen):
    feature_key: str = Field(pattern=_KEY_PATTERN)
    scope: ComparisonFeatureScope
    applicability: Applicability
    value: str | None = Field(default=None, pattern=_CONTROLLED_VALUE_PATTERN)

    @model_validator(mode="after")
    def valid_applicability(self) -> Self:
        if (self.applicability == Applicability.APPLICABLE) != (self.value is not None):
            raise ValueError("COMPARISON_FEATURE_APPLICABILITY_VALUE_INVALID")
        return self


def _fingerprint(
    *,
    features: Iterable[ComparisonFeatureValue],
    definitions: Iterable[ComparisonFeatureDefinition],
    scope: ComparisonFeatureScope,
    definition_identity: str,
) -> str:
    """Fingerprint values with their controlling semantic definition identity.

    The explicit empty-scope state prevents an all-NOT_APPLICABLE definition
    set from being silently equivalent to a scope with no definitions at all.
    """

    scoped_definitions = sorted(
        (
            {
                "feature_key": definition.feature_key,
                "content_hash": definition.content_hash,
            }
            for definition in definitions
            if definition.scope == scope
        ),
        key=lambda item: item["feature_key"],
    )
    scoped_features = sorted(
        (item for item in features if item.scope == scope),
        key=lambda item: item.feature_key,
    )
    applicable_features = {
        item.feature_key: item.value
        for item in scoped_features
        if item.applicability == Applicability.APPLICABLE
    }
    scope_state = (
        "NO_FEATURE_DEFINITIONS"
        if not scoped_definitions
        else "APPLICABLE"
        if applicable_features
        else "ALL_NOT_APPLICABLE"
    )
    return semantic_hash(
        {
            "scope": scope.value,
            "definition_identity": definition_identity,
            "feature_definition_hashes": scoped_definitions,
            "scope_state": scope_state,
            "applicable_feature_values": applicable_features,
        }
    )


class ComparisonFeatureView(_StrictFrozen):
    """The only Card E learning/comparison view: controlled values, never rich prose."""

    schema_version: Literal[COMPARISON_FEATURE_VIEW_VERSION] = (
        COMPARISON_FEATURE_VIEW_VERSION
    )
    projection_family: Literal[SemanticProjectionFamily.LEARNING] = (
        SemanticProjectionFamily.LEARNING
    )
    applicability: Literal[Applicability.APPLICABLE] = Applicability.APPLICABLE
    semantic_profile_hash: str = Field(pattern=SHA256_PATTERN)
    kernel_definition_hash: str = Field(pattern=SHA256_PATTERN)
    channel_definition_hash: str = Field(pattern=SHA256_PATTERN)
    format_definition_hash: str = Field(pattern=SHA256_PATTERN)
    source_semantic_snapshot_ref: str = Field(min_length=1)
    source_semantic_snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    feature_definitions: list[ComparisonFeatureDefinition] = Field(min_length=1)
    features: list[ComparisonFeatureValue] = Field(min_length=1)
    shared_feature_fingerprint: str = Field(pattern=SHA256_PATTERN)
    profile_feature_fingerprint: str = Field(pattern=SHA256_PATTERN)
    format_feature_fingerprint: str = Field(pattern=SHA256_PATTERN)
    comparison_fingerprint: str = Field(pattern=SHA256_PATTERN)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_comparison_view(self) -> Self:
        for definition in self.feature_definitions:
            definition.verify_integrity()
        definitions = {item.feature_key: item for item in self.feature_definitions}
        values = {item.feature_key: item for item in self.features}
        if len(definitions) != len(self.feature_definitions) or len(values) != len(
            self.features
        ):
            raise ValueError("COMPARISON_FEATURE_ID_DUPLICATE")
        if set(definitions) != set(values):
            raise ValueError("COMPARISON_FEATURE_DEFINITION_SET_MISMATCH")
        for key, value in values.items():
            definition = definitions[key]
            if value.scope != definition.scope or (
                value.value is not None and value.value not in definition.allowed_values
            ):
                raise ValueError("COMPARISON_FEATURE_VALUE_UNCONTROLLED")
        expected_global = _fingerprint(
            features=self.features,
            definitions=self.feature_definitions,
            scope=ComparisonFeatureScope.GLOBAL,
            definition_identity=self.kernel_definition_hash,
        )
        expected_profile = _fingerprint(
            features=self.features,
            definitions=self.feature_definitions,
            scope=ComparisonFeatureScope.PROFILE,
            definition_identity=self.channel_definition_hash,
        )
        expected_format = _fingerprint(
            features=self.features,
            definitions=self.feature_definitions,
            scope=ComparisonFeatureScope.FORMAT,
            definition_identity=self.format_definition_hash,
        )
        expected_comparison = semantic_hash(
            {
                "global": expected_global,
                "profile": expected_profile,
                "format": expected_format,
            }
        )
        if (
            self.shared_feature_fingerprint != expected_global
            or self.profile_feature_fingerprint != expected_profile
            or self.format_feature_fingerprint != expected_format
            or self.comparison_fingerprint != expected_comparison
        ):
            raise ValueError("COMPARISON_FINGERPRINT_MISMATCH")
        self.verify_integrity()
        return self

    @classmethod
    def build(
        cls,
        *,
        snapshot: ProjectRichSemanticSnapshot,
        features: Iterable[ComparisonFeatureValue],
    ) -> "ComparisonFeatureView":
        snapshot.verify_integrity()
        if (
            SemanticProjectionFamily.LEARNING
            not in snapshot.semantic_profile.format_profile.required_projection_families
        ):
            raise ValueError("LEARNING_PROJECTION_FORMAT_NOT_APPLICABLE")
        values = list(features)
        definitions = list(snapshot.semantic_profile.feature_definitions)
        global_fingerprint = _fingerprint(
            features=values,
            definitions=definitions,
            scope=ComparisonFeatureScope.GLOBAL,
            definition_identity=snapshot.semantic_profile.kernel.content_hash,
        )
        profile_fingerprint = _fingerprint(
            features=values,
            definitions=definitions,
            scope=ComparisonFeatureScope.PROFILE,
            definition_identity=snapshot.semantic_profile.channel_profile.content_hash,
        )
        format_fingerprint = _fingerprint(
            features=values,
            definitions=definitions,
            scope=ComparisonFeatureScope.FORMAT,
            definition_identity=snapshot.semantic_profile.format_profile.content_hash,
        )
        comparison_fingerprint = semantic_hash(
            {
                "global": global_fingerprint,
                "profile": profile_fingerprint,
                "format": format_fingerprint,
            }
        )
        return cls._seal(
            semantic_profile_hash=snapshot.semantic_profile.content_hash,
            kernel_definition_hash=snapshot.semantic_profile.kernel.content_hash,
            channel_definition_hash=snapshot.semantic_profile.channel_profile.content_hash,
            format_definition_hash=snapshot.semantic_profile.format_profile.content_hash,
            source_semantic_snapshot_ref=(
                f"semantic-snapshot://{snapshot.snapshot_id}"
            ),
            source_semantic_snapshot_hash=snapshot.content_hash,
            feature_definitions=definitions,
            features=values,
            shared_feature_fingerprint=global_fingerprint,
            profile_feature_fingerprint=profile_fingerprint,
            format_feature_fingerprint=format_fingerprint,
            comparison_fingerprint=comparison_fingerprint,
        )

    def learning_equivalence_payload(
        self,
        *,
        comparison_scope: Literal["SCOPED", "GLOBAL"] = "SCOPED",
    ) -> dict[str, Any]:
        """Safe adapter for the existing learning fingerprint authority.

        The snapshot ref/hash are intentionally absent: they are provenance,
        never equivalence identity.  ``GLOBAL`` is the only cross-profile
        payload: it excludes profile/format features that are not shared by
        definition.  R owns persistence and learning decisions.
        """

        selected_scope = (
            ComparisonFeatureScope.GLOBAL if comparison_scope == "GLOBAL" else None
        )
        return {
            "normalized_features": {
                item.feature_key: item.value
                for item in self.features
                if item.applicability == Applicability.APPLICABLE
                and (selected_scope is None or item.scope == selected_scope)
            },
            "fingerprint": (
                self.shared_feature_fingerprint
                if comparison_scope == "GLOBAL"
                else self.comparison_fingerprint
            ),
        }


class SemanticProjectionCompiler:
    """Compile stage payloads from a sealed snapshot with no positional fallback."""

    @staticmethod
    def _snapshot_ref(snapshot: ProjectRichSemanticSnapshot) -> str:
        return f"semantic-snapshot://{snapshot.snapshot_id}"

    @staticmethod
    def _applicable(
        snapshot: ProjectRichSemanticSnapshot,
        family: SemanticProjectionFamily,
    ) -> bool:
        return (
            family
            in snapshot.semantic_profile.format_profile.required_projection_families
        )

    @classmethod
    def _not_applicable(
        cls,
        snapshot: ProjectRichSemanticSnapshot,
        family: SemanticProjectionFamily,
    ) -> ProjectionNotApplicable:
        return ProjectionNotApplicable.build(
            projection_family=family,
            semantic_snapshot_ref=cls._snapshot_ref(snapshot),
            semantic_snapshot_hash=snapshot.content_hash,
        )

    @staticmethod
    def _units(
        snapshot: ProjectRichSemanticSnapshot,
        kinds: frozenset[SemanticAtomKind],
    ) -> list[ProjectedSemanticUnit]:
        return [
            ProjectedSemanticUnit(
                semantic_owner_ref=unit.meaning_id,
                statement=unit.statement,
                atoms=[atom for atom in unit.atoms if atom.kind in kinds],
            )
            for unit in snapshot.meaning_units
        ]

    @staticmethod
    def _extensions(
        snapshot: ProjectRichSemanticSnapshot,
        family: SemanticProjectionFamily,
    ) -> list[SemanticExtensionPayload]:
        definition_by_id = {
            definition.extension_definition_id: definition
            for definition in snapshot.semantic_profile.extension_definitions
        }
        return [
            extension
            for extension in snapshot.extensions
            if family
            in definition_by_id[extension.extension_definition_id].projection_families
        ]

    @staticmethod
    def _reuse_compatibility(
        snapshot: ProjectRichSemanticSnapshot,
    ) -> list[VisualReuseCompatibility]:
        def values_for(
            unit: SemanticMeaningUnit,
            kinds: frozenset[SemanticAtomKind],
        ) -> list[str]:
            return [atom.value for atom in unit.atoms if atom.kind in kinds]

        return [
            VisualReuseCompatibility.build(
                semantic_owner_ref=unit.meaning_id,
                subject_refs=values_for(
                    unit,
                    frozenset({SemanticAtomKind.SUBJECT, SemanticAtomKind.ENTITY}),
                ),
                proposition=unit.statement,
                action_or_relationships=values_for(
                    unit,
                    frozenset({SemanticAtomKind.ACTION, SemanticAtomKind.RELATIONSHIP}),
                ),
                context_refs=values_for(unit, frozenset({SemanticAtomKind.CONTEXT})),
                factuality=unit.factuality,
                reuse_eligible=bool(
                    values_for(
                        unit,
                        frozenset({SemanticAtomKind.SUBJECT, SemanticAtomKind.ENTITY}),
                    )
                    and values_for(
                        unit,
                        frozenset(
                            {SemanticAtomKind.ACTION, SemanticAtomKind.RELATIONSHIP}
                        ),
                    )
                    and values_for(unit, frozenset({SemanticAtomKind.CONTEXT}))
                ),
            )
            for unit in snapshot.meaning_units
        ]

    @classmethod
    def writer(
        cls, snapshot: ProjectRichSemanticSnapshot
    ) -> WriterSemanticProjection | ProjectionNotApplicable:
        snapshot.verify_integrity()
        family = SemanticProjectionFamily.WRITER
        if not cls._applicable(snapshot, family):
            return cls._not_applicable(snapshot, family)
        return WriterSemanticProjection.build(
            semantic_snapshot_ref=cls._snapshot_ref(snapshot),
            semantic_snapshot_hash=snapshot.content_hash,
            writer_units=cls._units(snapshot, _WRITER_ATOMS),
            extensions=cls._extensions(snapshot, family),
        )

    @classmethod
    def visual(
        cls, snapshot: ProjectRichSemanticSnapshot
    ) -> VisualSemanticProjection | ProjectionNotApplicable:
        snapshot.verify_integrity()
        family = SemanticProjectionFamily.VISUAL
        if not cls._applicable(snapshot, family):
            return cls._not_applicable(snapshot, family)
        return VisualSemanticProjection.build(
            semantic_snapshot_ref=cls._snapshot_ref(snapshot),
            semantic_snapshot_hash=snapshot.content_hash,
            visual_units=cls._units(snapshot, _VISUAL_ATOMS),
            reuse_compatibility=cls._reuse_compatibility(snapshot),
            temporal_bindings=snapshot.temporal_bindings,
            overlay_intents=snapshot.overlay_intents,
            extensions=cls._extensions(snapshot, family),
        )

    @classmethod
    def packaging(
        cls, snapshot: ProjectRichSemanticSnapshot
    ) -> PackagingSemanticProjection | ProjectionNotApplicable:
        snapshot.verify_integrity()
        family = SemanticProjectionFamily.PACKAGING
        if not cls._applicable(snapshot, family):
            return cls._not_applicable(snapshot, family)
        return PackagingSemanticProjection.build(
            semantic_snapshot_ref=cls._snapshot_ref(snapshot),
            semantic_snapshot_hash=snapshot.content_hash,
            packaging_units=cls._units(snapshot, _PACKAGING_ATOMS),
            extensions=cls._extensions(snapshot, family),
        )

    @classmethod
    def qc(
        cls, snapshot: ProjectRichSemanticSnapshot
    ) -> QCSemanticProjection | ProjectionNotApplicable:
        snapshot.verify_integrity()
        family = SemanticProjectionFamily.QC
        if not cls._applicable(snapshot, family):
            return cls._not_applicable(snapshot, family)
        return QCSemanticProjection.build(
            semantic_snapshot_ref=cls._snapshot_ref(snapshot),
            semantic_snapshot_hash=snapshot.content_hash,
            qc_units=cls._units(snapshot, _QC_ATOMS),
            extensions=cls._extensions(snapshot, family),
        )

    @classmethod
    def learning(
        cls,
        snapshot: ProjectRichSemanticSnapshot,
        *,
        features: Iterable[ComparisonFeatureValue],
    ) -> ComparisonFeatureView | ProjectionNotApplicable:
        """Return Card E's canonical controlled learning view when applicable.

        Values are an explicit controlled input.  This compiler never derives
        learning categories from a rich meaning statement, nor does it make a
        learning decision or persist a learning record.
        """

        snapshot.verify_integrity()
        family = SemanticProjectionFamily.LEARNING
        if not cls._applicable(snapshot, family):
            return cls._not_applicable(snapshot, family)
        return ComparisonFeatureView.build(snapshot=snapshot, features=features)
