"""Global editorial authorship law for publishable projects.

This module defines the editorial reasons a project must carry.  It does not
define a visual effect engine or any renderer/provider primitive.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


EDITORIAL_AUTHORSHIP_LAW_VERSION = "vcos.global-editorial-authorship.v1"
VIEWER_FACING_AUTHORSHIP_LAW_VERSION = "vcos.viewer-facing-authorship-law.v1"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

# These are law-level names only.  They are deliberately not effect or
# primitive schemas; downstream cards may define their own typed vocabularies.
MECHANICAL_PRESENTATION_TRIGGERS = frozenset(
    {
        "TIMER",
        "SCENE_ORDINAL",
        "MODULO",
        "ANTI_REPEAT_HEURISTIC",
        "SILENCE_MIDPOINT",
        "PROVIDER_DURATION_BOUNDARY",
        "TECHNICAL_SEGMENT_BOUNDARY",
        "PROVIDER_SEGMENT_BOUNDARY",
    }
)


def _semantic_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EditorialAuthorityType(StrEnum):
    """Closed Card D authority classes; source evidence is never authorship."""

    SOURCE_EVIDENCE = "SOURCE_EVIDENCE"
    VIDEO_PROJECT = "VIDEO_PROJECT"
    PROJECT_ADMISSION = "PROJECT_ADMISSION"
    CHANNEL_PROFILE = "CHANNEL_PROFILE"
    EDITORIAL_PROPOSAL = "EDITORIAL_PROPOSAL"
    EDITORIAL_SPECIFICITY_RECEIPT = "EDITORIAL_SPECIFICITY_RECEIPT"
    TOPIC_DEFINITION = "TOPIC_DEFINITION"
    SECTION_COVERAGE_PLAN = "SECTION_COVERAGE_PLAN"
    EDITORIAL_AUTHORSHIP_CONTRACT = "EDITORIAL_AUTHORSHIP_CONTRACT"


_AUTHORED_CHILD_AUTHORITY_TYPES = frozenset(
    {
        EditorialAuthorityType.VIDEO_PROJECT,
        EditorialAuthorityType.PROJECT_ADMISSION,
        EditorialAuthorityType.CHANNEL_PROFILE,
        EditorialAuthorityType.EDITORIAL_PROPOSAL,
        EditorialAuthorityType.EDITORIAL_SPECIFICITY_RECEIPT,
        EditorialAuthorityType.TOPIC_DEFINITION,
        EditorialAuthorityType.SECTION_COVERAGE_PLAN,
    }
)
_AUTHORITY_REF_PREFIXES = {
    EditorialAuthorityType.VIDEO_PROJECT: "video-project://",
    EditorialAuthorityType.PROJECT_ADMISSION: "project-admission://",
    EditorialAuthorityType.CHANNEL_PROFILE: "channel-profile://",
    EditorialAuthorityType.EDITORIAL_PROPOSAL: "editorial-proposal://",
    EditorialAuthorityType.EDITORIAL_SPECIFICITY_RECEIPT: "editorial-specificity://",
    EditorialAuthorityType.TOPIC_DEFINITION: "topic-definition://",
    EditorialAuthorityType.SECTION_COVERAGE_PLAN: "section-coverage-plan://",
    EditorialAuthorityType.EDITORIAL_AUTHORSHIP_CONTRACT: "editorial-authorship://",
}
_DIRECT_HASH_REF_AUTHORITY_TYPES = frozenset(
    {
        EditorialAuthorityType.EDITORIAL_PROPOSAL,
        EditorialAuthorityType.EDITORIAL_SPECIFICITY_RECEIPT,
        EditorialAuthorityType.TOPIC_DEFINITION,
        EditorialAuthorityType.SECTION_COVERAGE_PLAN,
        EditorialAuthorityType.EDITORIAL_AUTHORSHIP_CONTRACT,
    }
)
_SCOPED_HASH_REF_AUTHORITY_TYPES = frozenset(
    {
        EditorialAuthorityType.PROJECT_ADMISSION,
        EditorialAuthorityType.CHANNEL_PROFILE,
    }
)


class EditorialAuthorityBinding(_StrictFrozen):
    """Exact typed/hash-bound authority consumed by Card D."""

    authority_type: EditorialAuthorityType
    authority_ref: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        prefix = _AUTHORITY_REF_PREFIXES.get(self.authority_type)
        if prefix is not None and not self.authority_ref.startswith(prefix):
            raise ValueError("EDITORIAL_AUTHORITY_REF_TYPE_MISMATCH")
        if self.authority_type in _DIRECT_HASH_REF_AUTHORITY_TYPES and (
            self.authority_ref != f"{prefix}{self.content_hash}"
        ):
            raise ValueError("EDITORIAL_AUTHORITY_REF_HASH_MISMATCH")
        if self.authority_type in _SCOPED_HASH_REF_AUTHORITY_TYPES and not re.fullmatch(
            rf"{re.escape(str(prefix))}[^/]+/{self.content_hash}",
            self.authority_ref,
        ):
            raise ValueError("EDITORIAL_AUTHORITY_REF_HASH_MISMATCH")
        return self


def _authorship_contract_body(contract: BaseModel) -> dict[str, Any]:
    body = contract.model_dump(mode="json", exclude={"content_hash"})
    if not body.get("source_evidence_refs"):
        body.pop("source_evidence_refs", None)
    if not body.get("authored_authority_refs"):
        body.pop("authored_authority_refs", None)
    if not body.get("source_evidence_authorities"):
        body.pop("source_evidence_authorities", None)
    if not body.get("authored_authorities"):
        body.pop("authored_authorities", None)
    return body


class ViewerFacingAuthorshipLaw(_StrictFrozen):
    """The global law governing viewer-facing presentation decisions."""

    schema_version: Literal[VIEWER_FACING_AUTHORSHIP_LAW_VERSION] = (
        VIEWER_FACING_AUTHORSHIP_LAW_VERSION
    )
    authorial_intent_visible_to_viewer: Literal[True] = True
    no_effect_without_editorial_reason: Literal[True] = True
    no_visual_change_and_hold_are_valid_outcomes: Literal[True] = True
    mechanical_triggers_have_no_editorial_authority: Literal[True] = True
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def build(cls) -> "ViewerFacingAuthorshipLaw":
        body = {
            "schema_version": VIEWER_FACING_AUTHORSHIP_LAW_VERSION,
            "authorial_intent_visible_to_viewer": True,
            "no_effect_without_editorial_reason": True,
            "no_visual_change_and_hold_are_valid_outcomes": True,
            "mechanical_triggers_have_no_editorial_authority": True,
        }
        return cls(**body, content_hash=_semantic_hash(body))

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        body = self.model_dump(mode="json", exclude={"content_hash"})
        if self.content_hash != _semantic_hash(body):
            raise ValueError("VIEWER_FACING_AUTHORSHIP_LAW_HASH_MISMATCH")
        return self


class EditorialAuthorshipContract(_StrictFrozen):
    """Minimum authored editorial value required before a project can publish."""

    schema_version: Literal[EDITORIAL_AUTHORSHIP_LAW_VERSION] = (
        EDITORIAL_AUTHORSHIP_LAW_VERSION
    )
    source_role: Literal["EVIDENCE"] = "EVIDENCE"
    # Legacy string lists remain readable only for immutable historical
    # packages.  ``build`` forbids producing a new contract through them.
    source_evidence_refs: list[str] = Field(default_factory=list)
    authored_authority_refs: list[str] = Field(default_factory=list)
    source_evidence_authorities: list[EditorialAuthorityBinding] = Field(
        default_factory=list
    )
    authored_authorities: list[EditorialAuthorityBinding] = Field(default_factory=list)
    content_mode: str = Field(min_length=1, max_length=120)
    format_key: str = Field(min_length=1, max_length=160)
    channel_promise: str = Field(min_length=1, max_length=4_000)
    episode_reasoning: str = Field(min_length=1, max_length=4_000)
    central_question: str = Field(min_length=1, max_length=4_000)
    early_stakes_or_payoff: str = Field(min_length=1, max_length=4_000)
    original_thesis_or_position: str = Field(min_length=1, max_length=4_000)
    editorial_delta: str = Field(min_length=1, max_length=4_000)
    reasoning_or_narrative_spine: str = Field(min_length=1, max_length=8_000)
    progression: str = Field(min_length=1, max_length=8_000)
    tension_applicability: Literal["APPLICABLE", "NOT_APPLICABLE"]
    tension_failure_contradiction_or_tradeoff: str | None = Field(
        default=None, max_length=4_000
    )
    visible_editorial_judgment: str = Field(min_length=1, max_length=4_000)
    memorable_payoff_framework_or_conclusion: str = Field(
        min_length=1, max_length=4_000
    )
    viewer_facing_presentation: ViewerFacingAuthorshipLaw
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        text_fields = (
            self.channel_promise,
            self.episode_reasoning,
            self.central_question,
            self.early_stakes_or_payoff,
            self.original_thesis_or_position,
            self.editorial_delta,
            self.reasoning_or_narrative_spine,
            self.progression,
            self.visible_editorial_judgment,
            self.memorable_payoff_framework_or_conclusion,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("EDITORIAL_AUTHORSHIP_VALUE_MISSING")
        legacy_mode = bool(self.source_evidence_refs or self.authored_authority_refs)
        typed_mode = bool(
            self.source_evidence_authorities or self.authored_authorities
        )
        if legacy_mode and typed_mode:
            raise ValueError("EDITORIAL_AUTHORSHIP_DUAL_LINEAGE_AUTHORITY_FORBIDDEN")
        if legacy_mode:
            if (
                not self.source_evidence_refs
                or len(self.authored_authority_refs) < 3
                or any(not ref.strip() for ref in self.source_evidence_refs)
                or any(not ref.strip() for ref in self.authored_authority_refs)
            ):
                raise ValueError("EDITORIAL_AUTHORSHIP_LEGACY_LINEAGE_INVALID")
            source_refs = self.source_evidence_refs
            authored_refs = self.authored_authority_refs
        else:
            if (
                not self.source_evidence_authorities
                or len(self.authored_authorities) < 3
            ):
                raise ValueError("EDITORIAL_AUTHORSHIP_TRANSITIVE_LINEAGE_REQUIRED")
            if any(
                item.authority_type != EditorialAuthorityType.SOURCE_EVIDENCE
                for item in self.source_evidence_authorities
            ):
                raise ValueError("EDITORIAL_AUTHORSHIP_SOURCE_AUTHORITY_TYPE_INVALID")
            if any(
                item.authority_type not in _AUTHORED_CHILD_AUTHORITY_TYPES
                for item in self.authored_authorities
            ):
                raise ValueError("EDITORIAL_AUTHORSHIP_CHILD_AUTHORITY_TYPE_INVALID")
            source_refs = [item.authority_ref for item in self.source_evidence_authorities]
            authored_refs = [item.authority_ref for item in self.authored_authorities]
        if len(source_refs) != len(set(source_refs)) or len(authored_refs) != len(
            set(authored_refs)
        ):
            raise ValueError("EDITORIAL_AUTHORSHIP_AUTHORITY_REF_DUPLICATE")
        if set(source_refs).intersection(authored_refs):
            raise ValueError("EDITORIAL_AUTHORSHIP_SOURCE_AUTHORITY_OVERLAP")
        if _normalized_text(self.channel_promise) == _normalized_text(
            self.episode_reasoning
        ):
            raise ValueError(
                "EDITORIAL_AUTHORSHIP_CHANNEL_EPISODE_REASONING_NOT_DISTINCT"
            )
        authored_roles = {
            _normalized_text(self.central_question),
            _normalized_text(self.editorial_delta),
            _normalized_text(self.visible_editorial_judgment),
            _normalized_text(self.memorable_payoff_framework_or_conclusion),
        }
        if len(authored_roles) != 4:
            raise ValueError("EDITORIAL_AUTHORSHIP_REASONING_NOT_DISTINCT")
        if self.tension_applicability == "APPLICABLE":
            if not self.tension_failure_contradiction_or_tradeoff:
                raise ValueError("EDITORIAL_AUTHORSHIP_TENSION_REQUIRED")
        elif self.tension_failure_contradiction_or_tradeoff is not None:
            raise ValueError("EDITORIAL_AUTHORSHIP_TENSION_NOT_APPLICABLE")
        body = _authorship_contract_body(self)
        if self.content_hash != _semantic_hash(body):
            raise ValueError("EDITORIAL_AUTHORSHIP_CONTRACT_HASH_MISMATCH")
        return self

    @classmethod
    def build(cls, **values: Any) -> "EditorialAuthorshipContract":
        if values.get("source_evidence_refs") or values.get(
            "authored_authority_refs"
        ):
            raise ValueError("EDITORIAL_AUTHORSHIP_LEGACY_BUILD_FORBIDDEN")
        accepted_fields = set(cls.model_fields) - {"content_hash"}
        if unknown_fields := set(values) - accepted_fields:
            raise ValueError(
                f"EDITORIAL_AUTHORSHIP_FIELD_UNKNOWN:{','.join(sorted(unknown_fields))}"
            )
        values.setdefault("schema_version", EDITORIAL_AUTHORSHIP_LAW_VERSION)
        values.setdefault("source_role", "EVIDENCE")
        values.setdefault("viewer_facing_presentation", ViewerFacingAuthorshipLaw.build())
        values["viewer_facing_presentation"] = ViewerFacingAuthorshipLaw.model_validate(
            values["viewer_facing_presentation"]
        )
        for field_name in (
            "source_evidence_authorities",
            "authored_authorities",
        ):
            values[field_name] = [
                EditorialAuthorityBinding.model_validate(item)
                for item in values.get(field_name, [])
            ]
        body = _authorship_contract_body(cls.model_construct(**values))
        return cls(**body, content_hash=_semantic_hash(body))

    @property
    def has_transitive_authority_binding(self) -> bool:
        return bool(
            self.source_evidence_authorities and self.authored_authorities
        ) and not bool(self.source_evidence_refs or self.authored_authority_refs)

    @property
    def presentation_authority(self) -> EditorialAuthorityBinding:
        return EditorialAuthorityBinding(
            authority_type=EditorialAuthorityType.EDITORIAL_AUTHORSHIP_CONTRACT,
            authority_ref=f"editorial-authorship://{self.content_hash}",
            content_hash=self.content_hash,
        )

    def verify_integrity(self) -> None:
        if self.content_hash != _semantic_hash(_authorship_contract_body(self)):
            raise ValueError("EDITORIAL_AUTHORSHIP_CONTRACT_HASH_MISMATCH")


# Explicit aliases make the law discoverable under the language used by later
# cards without creating a second authority.
GlobalEditorialAuthorshipLaw = EditorialAuthorshipContract
AuthoredPresentationLaw = ViewerFacingAuthorshipLaw


def validate_viewer_facing_presentation(
    decisions: Iterable[Mapping[str, Any]] | Mapping[str, Any],
) -> None:
    """Validate generic presentation decisions without defining their schema.

    ``HOLD`` and ``NO_VISUAL_CHANGE`` are valid stable outcomes.  Every
    decision resolves one exact EditorialAuthorshipContract binding;
    arbitrary strings, evidence refs, and generator-owned scene refs cannot
    become presentation authority.
    """

    if isinstance(decisions, Mapping):
        decisions = (decisions,)
    for decision in decisions:
        outcome = str(decision.get("outcome") or "").strip().upper()
        if outcome not in {"HOLD", "NO_VISUAL_CHANGE", "CHANGE", "PRESENTATION_CHANGE"}:
            raise ValueError("VIEWER_FACING_PRESENTATION_OUTCOME_INVALID")
        reason = decision.get("editorial_reason") or decision.get("authored_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("NO_EFFECT_WITHOUT_EDITORIAL_REASON")
        triggers: set[str] = set()
        raw_trigger = decision.get("trigger")
        if isinstance(raw_trigger, str):
            triggers.add(raw_trigger.strip().upper())
        elif isinstance(raw_trigger, Iterable):
            triggers.update(str(item).strip().upper() for item in raw_trigger)
        raw_authority = decision.get("editorial_authority")
        if (
            triggers.intersection(MECHANICAL_PRESENTATION_TRIGGERS)
            and raw_authority is None
        ):
            raise ValueError("MECHANICAL_PRESENTATION_TRIGGER_HAS_NO_AUTHORITY")
        try:
            authority = (
                raw_authority
                if isinstance(raw_authority, EditorialAuthorityBinding)
                else EditorialAuthorityBinding.model_validate(raw_authority)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "EDITORIAL_PRESENTATION_AUTHORITY_BINDING_REQUIRED"
            ) from exc
        if authority.authority_type == EditorialAuthorityType.SOURCE_EVIDENCE:
            raise ValueError("SOURCE_CANNOT_AUTHOR_PRESENTATION")
        if (
            authority.authority_type
            != EditorialAuthorityType.EDITORIAL_AUTHORSHIP_CONTRACT
        ):
            raise ValueError("EDITORIAL_PRESENTATION_AUTHORITY_TYPE_INVALID")
        if outcome in {"HOLD", "NO_VISUAL_CHANGE"} and decision.get(
            "actual_presentation_change"
        ) is True:
            raise ValueError("STABLE_PRESENTATION_OUTCOME_RUNTIME_MISMATCH")


NO_EFFECT_WITHOUT_EDITORIAL_REASON = "NO_EFFECT_WITHOUT_EDITORIAL_REASON"
NO_VISUAL_CHANGE = "NO_VISUAL_CHANGE"
HOLD = "HOLD"
