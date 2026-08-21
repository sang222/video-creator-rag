"""Global editorial authorship law for publishable projects.

This module defines the editorial reasons a project must carry.  It does not
define a visual effect engine or any renderer/provider primitive.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
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
    source_evidence_refs: list[str] = Field(min_length=1)
    authored_authority_refs: list[str] = Field(min_length=3)
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
        if any(not ref.strip() for ref in self.source_evidence_refs):
            raise ValueError("EDITORIAL_AUTHORSHIP_SOURCE_EVIDENCE_REF_INVALID")
        if any(not ref.strip() for ref in self.authored_authority_refs):
            raise ValueError("EDITORIAL_AUTHORSHIP_AUTHORED_AUTHORITY_REF_INVALID")
        if len(set(self.authored_authority_refs)) != len(self.authored_authority_refs):
            raise ValueError("EDITORIAL_AUTHORSHIP_AUTHORITY_REF_DUPLICATE")
        if set(self.source_evidence_refs).intersection(self.authored_authority_refs):
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
        body = self.model_dump(mode="json", exclude={"content_hash"})
        if self.content_hash != _semantic_hash(body):
            raise ValueError("EDITORIAL_AUTHORSHIP_CONTRACT_HASH_MISMATCH")
        return self

    @classmethod
    def build(cls, **values: Any) -> "EditorialAuthorshipContract":
        values.setdefault("schema_version", EDITORIAL_AUTHORSHIP_LAW_VERSION)
        values.setdefault("source_role", "EVIDENCE")
        values.setdefault("viewer_facing_presentation", ViewerFacingAuthorshipLaw.build())
        body = cls.model_construct(**values).model_dump(mode="json")
        body.pop("content_hash", None)
        return cls(**body, content_hash=_semantic_hash(body))


# Explicit aliases make the law discoverable under the language used by later
# cards without creating a second authority.
GlobalEditorialAuthorshipLaw = EditorialAuthorshipContract
AuthoredPresentationLaw = ViewerFacingAuthorshipLaw


def validate_viewer_facing_presentation(
    decisions: Iterable[Mapping[str, Any]] | Mapping[str, Any],
) -> None:
    """Validate generic presentation decisions without defining their schema.

    ``HOLD`` and ``NO_VISUAL_CHANGE`` are valid stable outcomes.  Any other
    viewer-facing decision needs a human-readable editorial reason and an
    editorial authority reference; mechanical triggers can never be that
    authority on their own.
    """

    if isinstance(decisions, Mapping):
        decisions = (decisions,)
    for decision in decisions:
        outcome = str(decision.get("outcome") or "").strip().upper()
        reason = decision.get("editorial_reason") or decision.get("authored_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("NO_EFFECT_WITHOUT_EDITORIAL_REASON")
        triggers: set[str] = set()
        raw_trigger = decision.get("trigger")
        if isinstance(raw_trigger, str):
            triggers.add(raw_trigger.strip().upper())
        elif isinstance(raw_trigger, Iterable):
            triggers.update(str(item).strip().upper() for item in raw_trigger)
        if triggers.intersection(MECHANICAL_PRESENTATION_TRIGGERS) and not str(
            decision.get("editorial_authority_ref") or ""
        ).strip():
            raise ValueError("MECHANICAL_PRESENTATION_TRIGGER_HAS_NO_AUTHORITY")
        if not str(decision.get("editorial_authority_ref") or "").strip():
            raise ValueError("EDITORIAL_PRESENTATION_AUTHORITY_REQUIRED")


NO_EFFECT_WITHOUT_EDITORIAL_REASON = "NO_EFFECT_WITHOUT_EDITORIAL_REASON"
NO_VISUAL_CHANGE = "NO_VISUAL_CHANGE"
HOLD = "HOLD"
