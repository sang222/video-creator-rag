"""Typed AI-visual cross-modal lineage and inspection evidence.

The automated disposition in this module is deliberately narrower than a
semantic judgment about generated pixels.  Gemini image generation can return
a checksum-bound description in the same interaction; Veo currently returns
video bytes and provider provenance, while local QC inspects technical media
and sampled-frame motion only.  Both routes therefore retain a mandatory human
semantic-review boundary, and the Veo route never claims semantic inspection.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_visual_production import ai_visual_stable_hash


_SHA256 = r"^[0-9a-f]{64}$"

AIVisualAssetInspectionScope = Literal[
    "IMAGE_SAME_INTERACTION_SEMANTIC_AND_TECHNICAL",
    "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY",
]
AIVisualAssetDescriptionSource = Literal[
    "SAME_INTERACTION_MODEL_OUTPUT",
    "NO_AUTOMATED_ASSET_DESCRIPTION",
]
AIVisualAssetSemanticDisposition = Literal[
    "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW",
    "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW",
]


def _hash_matches(model: BaseModel) -> bool:
    return model.content_hash == ai_visual_stable_hash(
        model.model_dump(mode="json", exclude={"content_hash"})
    )


class VeoTechnicalMotionInspectionEvidence(BaseModel):
    """Durable Veo receipt with no automated semantic-conformity claim."""

    schema_version: Literal["vcos.ai-visual-veo-technical-motion-evidence.v1"] = (
        "vcos.ai-visual-veo-technical-motion-evidence.v1"
    )
    evidence_scope: Literal["LINEAGE_SCENE_TECHNICAL_AND_MOTION_ONLY"] = (
        "LINEAGE_SCENE_TECHNICAL_AND_MOTION_ONLY"
    )
    asset_effect_id: str = Field(min_length=1)
    asset_effect_identity_hash: str = Field(pattern=_SHA256)
    provider_request_hash: str = Field(pattern=_SHA256)
    asset_slot_id: str = Field(min_length=1)
    primary_asset_owner_scene_id: str = Field(min_length=1)
    bound_scene_ids: list[str] = Field(min_length=1)
    bound_scene_plan_hashes: list[str] = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=_SHA256)
    compiled_prompt_hash: str = Field(pattern=_SHA256)
    prompt_hash: str = Field(pattern=_SHA256)
    required_semantic_anchors: list[str] = Field(min_length=4, max_length=4)
    provider_key: Literal["google_veo"] = "google_veo"
    model_id: str = Field(min_length=1)
    provider_operation_id: str = Field(min_length=1)
    output_ref: str = Field(min_length=1)
    output_checksum: str = Field(pattern=_SHA256)
    qc_ref: str = Field(min_length=1)
    qc_hash: str = Field(pattern=_SHA256)
    sampled_frame_sha256: list[str] = Field(min_length=3)
    provider_provenance_verified: Literal[True] = True
    scene_binding_verified: Literal[True] = True
    technical_qc_passed: Literal[True] = True
    motion_inspection_performed: Literal[True] = True
    provider_audio_discarded: Literal[True] = True
    actual_asset_semantic_inspection_performed: Literal[False] = False
    same_interaction_model_output_semantic_inspection_performed: Literal[False] = False
    independent_multimodal_inspection_performed: Literal[False] = False
    semantic_conformity_asserted: Literal[False] = False
    human_semantic_review_required: Literal[True] = True
    semantic_disposition: Literal[
        "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
    ] = "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
    content_hash: str = Field(pattern=_SHA256)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_evidence(self) -> "VeoTechnicalMotionInspectionEvidence":
        if (
            len(self.bound_scene_ids) != len(set(self.bound_scene_ids))
            or len(self.bound_scene_ids) != len(self.bound_scene_plan_hashes)
            or self.primary_asset_owner_scene_id not in self.bound_scene_ids
            or self.scene_plan_hash
            != self.bound_scene_plan_hashes[
                self.bound_scene_ids.index(self.primary_asset_owner_scene_id)
            ]
            or len(set(self.sampled_frame_sha256)) < 2
            or len(set(self.required_semantic_anchors)) != 4
            or any(
                not isinstance(item, str)
                or len(item) != 64
                or any(character not in "0123456789abcdef" for character in item)
                for item in self.sampled_frame_sha256
            )
            or not _hash_matches(self)
        ):
            raise ValueError("AI_VISUAL_VEO_TECHNICAL_MOTION_EVIDENCE_INVALID")
        return self

    @classmethod
    def build(cls, **values: Any) -> "VeoTechnicalMotionInspectionEvidence":
        body = {
            "schema_version": "vcos.ai-visual-veo-technical-motion-evidence.v1",
            "evidence_scope": "LINEAGE_SCENE_TECHNICAL_AND_MOTION_ONLY",
            "provider_key": "google_veo",
            "provider_provenance_verified": True,
            "scene_binding_verified": True,
            "technical_qc_passed": True,
            "motion_inspection_performed": True,
            "provider_audio_discarded": True,
            "actual_asset_semantic_inspection_performed": False,
            "same_interaction_model_output_semantic_inspection_performed": False,
            "independent_multimodal_inspection_performed": False,
            "semantic_conformity_asserted": False,
            "human_semantic_review_required": True,
            "semantic_disposition": (
                "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
            ),
            **values,
        }
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class VerifiedAIVisualEffectEvidence(BaseModel):
    """Normalized, provider-receipt-bound evidence for one generated asset."""

    schema_version: Literal["vcos.verified-ai-visual-effect-evidence.v1"] = (
        "vcos.verified-ai-visual-effect-evidence.v1"
    )
    asset_slot_id: str = Field(min_length=1)
    primary_asset_owner_scene_id: str = Field(min_length=1)
    bound_scene_ids: list[str] = Field(min_length=1)
    bound_scene_plan_hashes: list[str] = Field(min_length=1)
    route: Literal["AI_IMAGE", "AI_VIDEO"]
    provider_key: Literal["google_gemini_image", "google_veo"]
    model_id: str = Field(min_length=1)
    asset_effect_identity_hash: str = Field(pattern=_SHA256)
    provider_request_hash: str | None = Field(default=None, pattern=_SHA256)
    provider_operation_id: str | None = None
    compiled_prompt_hash: str = Field(pattern=_SHA256)
    prompt: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=_SHA256)
    output_ref: str = Field(min_length=1)
    output_checksum: str = Field(pattern=_SHA256)
    qc_ref: str = Field(min_length=1)
    qc_hash: str = Field(pattern=_SHA256)
    provider_receipt_hash: str = Field(pattern=_SHA256)
    asset_inspection_evidence_hash: str = Field(pattern=_SHA256)
    asset_semantic_attestation_hash: str | None = Field(default=None, pattern=_SHA256)
    asset_inspection_scope: AIVisualAssetInspectionScope
    actual_asset_description_source: AIVisualAssetDescriptionSource
    observed_output_summary: str | None = None
    observed_primary_subjects: list[str] = Field(default_factory=list)
    observed_action_or_relation: str | None = None
    observed_environment: str | None = None
    required_semantic_anchors: list[str] = Field(min_length=4, max_length=4)
    observed_semantic_anchors: list[str] = Field(default_factory=list, max_length=4)
    provider_text_hash: str | None = Field(default=None, pattern=_SHA256)
    provider_semantic_match_asserted: bool
    provider_semantic_mismatch_reasons: list[str] = Field(default_factory=list)
    provider_forbidden_content_detected: list[str] = Field(default_factory=list)
    provider_forbidden_content_inspection_performed: bool
    actual_asset_semantic_inspection_performed: bool
    same_interaction_model_output_semantic_inspection_performed: bool
    technical_asset_inspection_performed: Literal[True] = True
    sampled_frame_sha256: list[str] = Field(default_factory=list)
    motion_inspection_performed: bool
    independent_multimodal_inspection_performed: Literal[False] = False
    automated_semantic_conformity_asserted: Literal[False] = False
    actual_asset_semantic_disposition: AIVisualAssetSemanticDisposition
    human_semantic_review_required: Literal[True] = True
    durable_effect_evidence_hash: str = Field(pattern=_SHA256)
    content_hash: str = Field(pattern=_SHA256)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_evidence(self) -> "VerifiedAIVisualEffectEvidence":
        common_invalid = (
            len(self.bound_scene_ids) != len(set(self.bound_scene_ids))
            or len(self.bound_scene_ids) != len(self.bound_scene_plan_hashes)
            or self.primary_asset_owner_scene_id not in self.bound_scene_ids
            or len(set(self.required_semantic_anchors)) != 4
            or self.provider_semantic_mismatch_reasons
            or self.provider_forbidden_content_detected
            or not _hash_matches(self)
        )
        if self.route == "AI_IMAGE":
            route_invalid = (
                self.provider_key != "google_gemini_image"
                or self.provider_request_hash is not None
                or self.provider_operation_id is not None
                or self.asset_inspection_scope
                != "IMAGE_SAME_INTERACTION_SEMANTIC_AND_TECHNICAL"
                or self.actual_asset_description_source
                != "SAME_INTERACTION_MODEL_OUTPUT"
                or not self.asset_semantic_attestation_hash
                or self.asset_inspection_evidence_hash
                != self.asset_semantic_attestation_hash
                or not self.observed_output_summary
                or not self.observed_primary_subjects
                or not self.observed_action_or_relation
                or not self.observed_environment
                or self.observed_semantic_anchors != self.required_semantic_anchors
                or not self.provider_text_hash
                or self.provider_semantic_match_asserted is not True
                or self.provider_forbidden_content_inspection_performed is not True
                or self.actual_asset_semantic_inspection_performed is not True
                or self.same_interaction_model_output_semantic_inspection_performed
                is not True
                or self.sampled_frame_sha256
                or self.motion_inspection_performed
                or self.actual_asset_semantic_disposition
                != "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
            )
        else:
            route_invalid = (
                self.provider_key != "google_veo"
                or not self.provider_request_hash
                or not self.provider_operation_id
                or self.asset_inspection_scope
                != "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY"
                or self.actual_asset_description_source
                != "NO_AUTOMATED_ASSET_DESCRIPTION"
                or self.asset_semantic_attestation_hash is not None
                or self.asset_inspection_evidence_hash != self.provider_receipt_hash
                or self.observed_output_summary is not None
                or self.observed_primary_subjects
                or self.observed_action_or_relation is not None
                or self.observed_environment is not None
                or self.observed_semantic_anchors
                or self.provider_text_hash is not None
                or self.provider_semantic_match_asserted
                or self.provider_forbidden_content_inspection_performed
                or self.actual_asset_semantic_inspection_performed
                or self.same_interaction_model_output_semantic_inspection_performed
                or len(self.sampled_frame_sha256) < 3
                or len(set(self.sampled_frame_sha256)) < 2
                or not self.motion_inspection_performed
                or self.actual_asset_semantic_disposition
                != "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
            )
        if common_invalid or route_invalid:
            raise ValueError("AI_VISUAL_EFFECT_EVIDENCE_INTEGRITY_INVALID")
        return self

    @classmethod
    def build(cls, **values: Any) -> "VerifiedAIVisualEffectEvidence":
        route = values.get("route")
        if route == "AI_IMAGE":
            route_defaults: dict[str, Any] = {
                "provider_request_hash": None,
                "provider_operation_id": None,
                "asset_inspection_evidence_hash": values.get(
                    "asset_semantic_attestation_hash"
                ),
                "asset_inspection_scope": (
                    "IMAGE_SAME_INTERACTION_SEMANTIC_AND_TECHNICAL"
                ),
                "actual_asset_description_source": "SAME_INTERACTION_MODEL_OUTPUT",
                "provider_forbidden_content_inspection_performed": True,
                "actual_asset_semantic_inspection_performed": True,
                "same_interaction_model_output_semantic_inspection_performed": True,
                "sampled_frame_sha256": [],
                "motion_inspection_performed": False,
                "actual_asset_semantic_disposition": (
                    "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
                ),
            }
        else:
            route_defaults = {
                "asset_semantic_attestation_hash": None,
                "asset_inspection_scope": "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY",
                "actual_asset_description_source": "NO_AUTOMATED_ASSET_DESCRIPTION",
                "observed_output_summary": None,
                "observed_primary_subjects": [],
                "observed_action_or_relation": None,
                "observed_environment": None,
                "observed_semantic_anchors": [],
                "provider_text_hash": None,
                "provider_semantic_match_asserted": False,
                "provider_semantic_mismatch_reasons": [],
                "provider_forbidden_content_detected": [],
                "provider_forbidden_content_inspection_performed": False,
                "actual_asset_semantic_inspection_performed": False,
                "same_interaction_model_output_semantic_inspection_performed": False,
                "motion_inspection_performed": True,
                "actual_asset_semantic_disposition": (
                    "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
                ),
            }
        body = {
            "schema_version": "vcos.verified-ai-visual-effect-evidence.v1",
            "technical_asset_inspection_performed": True,
            "independent_multimodal_inspection_performed": False,
            "automated_semantic_conformity_asserted": False,
            "human_semantic_review_required": True,
            **route_defaults,
            **values,
        }
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class AIVisualAssetSemanticAttestation(BaseModel):
    """Checksum-bound, route-aware inspection and generation-lineage record.

    The historical class name is retained for serialized compatibility.  For
    ``AI_VIDEO`` it is explicitly a technical/motion attestation and contains
    no semantic attestation.
    """

    schema_version: Literal["vcos.ai-visual-asset-semantic-attestation.v1"] = (
        "vcos.ai-visual-asset-semantic-attestation.v1"
    )
    evidence_scope: Literal["LINEAGE_AND_SCENE_BINDING"] = "LINEAGE_AND_SCENE_BINDING"
    asset_slot_id: str = Field(min_length=1)
    primary_asset_owner_scene_id: str = Field(min_length=1)
    bound_scene_ids: list[str] = Field(min_length=1)
    bound_scene_plan_hashes: list[str] = Field(min_length=1)
    route: Literal["AI_IMAGE", "AI_VIDEO"]
    provider_key: Literal["google_gemini_image", "google_veo"]
    model_id: str = Field(min_length=1)
    owner_scene_meaning: str = Field(min_length=1)
    generation_prompt: str = Field(min_length=1)
    generation_prompt_hash: str = Field(pattern=_SHA256)
    provider_request_hash: str | None = Field(default=None, pattern=_SHA256)
    provider_operation_id: str | None = None
    compiled_prompt_hash: str = Field(pattern=_SHA256)
    asset_ref: str = Field(min_length=1)
    asset_checksum: str = Field(pattern=_SHA256)
    asset_effect_identity_hash: str = Field(pattern=_SHA256)
    provider_receipt_hash: str = Field(pattern=_SHA256)
    provider_qc_hash: str = Field(pattern=_SHA256)
    asset_inspection_evidence_hash: str = Field(pattern=_SHA256)
    asset_semantic_attestation_hash: str | None = Field(default=None, pattern=_SHA256)
    asset_inspection_scope: AIVisualAssetInspectionScope
    actual_asset_description_source: AIVisualAssetDescriptionSource
    observed_output_summary: str | None = None
    observed_primary_subjects: list[str] = Field(default_factory=list)
    observed_action_or_relation: str | None = None
    observed_environment: str | None = None
    required_semantic_anchors: list[str] = Field(min_length=4, max_length=4)
    observed_semantic_anchors: list[str] = Field(default_factory=list, max_length=4)
    provider_text_hash: str | None = Field(default=None, pattern=_SHA256)
    provider_semantic_match_asserted: bool
    provider_semantic_mismatch_reasons: list[str] = Field(default_factory=list)
    provider_forbidden_content_detected: list[str] = Field(default_factory=list)
    provider_forbidden_content_inspection_performed: bool
    actual_bytes_checksum_verified: Literal[True] = True
    provider_receipt_binding_verified: Literal[True] = True
    generation_plan_binding_verified: Literal[True] = True
    scene_plan_binding_verified: Literal[True] = True
    technical_asset_inspection_performed: Literal[True] = True
    sampled_frame_sha256: list[str] = Field(default_factory=list)
    motion_inspection_performed: bool
    actual_asset_semantic_inspection_performed: bool
    same_interaction_model_output_semantic_inspection_performed: bool
    independent_multimodal_inspection_performed: Literal[False] = False
    automated_semantic_conformity_asserted: Literal[False] = False
    actual_asset_semantic_disposition: AIVisualAssetSemanticDisposition
    content_hash: str = Field(pattern=_SHA256)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_attestation(self) -> "AIVisualAssetSemanticAttestation":
        evidence_values = self.model_dump(
            mode="json",
            include={
                "asset_slot_id",
                "primary_asset_owner_scene_id",
                "bound_scene_ids",
                "bound_scene_plan_hashes",
                "route",
                "provider_key",
                "provider_request_hash",
                "provider_operation_id",
                "asset_inspection_evidence_hash",
                "asset_semantic_attestation_hash",
                "asset_inspection_scope",
                "actual_asset_description_source",
                "observed_output_summary",
                "observed_primary_subjects",
                "observed_action_or_relation",
                "observed_environment",
                "required_semantic_anchors",
                "observed_semantic_anchors",
                "provider_text_hash",
                "provider_semantic_match_asserted",
                "provider_semantic_mismatch_reasons",
                "provider_forbidden_content_detected",
                "provider_forbidden_content_inspection_performed",
                "actual_asset_semantic_inspection_performed",
                "same_interaction_model_output_semantic_inspection_performed",
                "sampled_frame_sha256",
                "motion_inspection_performed",
                "actual_asset_semantic_disposition",
            },
        )
        if (
            len(self.bound_scene_ids) != len(set(self.bound_scene_ids))
            or len(self.bound_scene_ids) != len(self.bound_scene_plan_hashes)
            or self.primary_asset_owner_scene_id not in self.bound_scene_ids
            or not _route_inspection_values_valid(evidence_values)
            or not _hash_matches(self)
        ):
            raise ValueError("AI_VISUAL_ASSET_SEMANTIC_ATTESTATION_INVALID")
        return self

    @classmethod
    def build(cls, **values: Any) -> "AIVisualAssetSemanticAttestation":
        route = values.get("route")
        if route == "AI_IMAGE":
            route_defaults: dict[str, Any] = {
                "provider_request_hash": None,
                "provider_operation_id": None,
                "asset_inspection_evidence_hash": values.get(
                    "asset_semantic_attestation_hash"
                ),
                "asset_inspection_scope": (
                    "IMAGE_SAME_INTERACTION_SEMANTIC_AND_TECHNICAL"
                ),
                "actual_asset_description_source": "SAME_INTERACTION_MODEL_OUTPUT",
                "provider_forbidden_content_inspection_performed": True,
                "sampled_frame_sha256": [],
                "motion_inspection_performed": False,
                "actual_asset_semantic_inspection_performed": True,
                "same_interaction_model_output_semantic_inspection_performed": True,
                "actual_asset_semantic_disposition": (
                    "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
                ),
            }
        else:
            route_defaults = {
                "asset_semantic_attestation_hash": None,
                "asset_inspection_scope": "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY",
                "actual_asset_description_source": "NO_AUTOMATED_ASSET_DESCRIPTION",
                "observed_output_summary": None,
                "observed_primary_subjects": [],
                "observed_action_or_relation": None,
                "observed_environment": None,
                "observed_semantic_anchors": [],
                "provider_text_hash": None,
                "provider_semantic_match_asserted": False,
                "provider_semantic_mismatch_reasons": [],
                "provider_forbidden_content_detected": [],
                "provider_forbidden_content_inspection_performed": False,
                "sampled_frame_sha256": values.get("sampled_frame_sha256", []),
                "motion_inspection_performed": True,
                "actual_asset_semantic_inspection_performed": False,
                "same_interaction_model_output_semantic_inspection_performed": False,
                "actual_asset_semantic_disposition": (
                    "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
                ),
            }
        body = {
            "schema_version": "vcos.ai-visual-asset-semantic-attestation.v1",
            "evidence_scope": "LINEAGE_AND_SCENE_BINDING",
            "actual_bytes_checksum_verified": True,
            "provider_receipt_binding_verified": True,
            "generation_plan_binding_verified": True,
            "scene_plan_binding_verified": True,
            "technical_asset_inspection_performed": True,
            "independent_multimodal_inspection_performed": False,
            "automated_semantic_conformity_asserted": False,
            **route_defaults,
            **values,
        }
        return cls(**body, content_hash=ai_visual_stable_hash(body))


def _route_inspection_values_valid(values: dict[str, Any]) -> bool:
    """Share strict route semantics without constructing a second model."""

    required = values.get("required_semantic_anchors") or []
    if len(required) != 4 or len(set(required)) != 4:
        return False
    if values.get("provider_semantic_mismatch_reasons") or values.get(
        "provider_forbidden_content_detected"
    ):
        return False
    if values.get("route") == "AI_IMAGE":
        return bool(
            values.get("provider_key") == "google_gemini_image"
            and values.get("provider_request_hash") is None
            and values.get("provider_operation_id") is None
            and values.get("asset_inspection_scope")
            == "IMAGE_SAME_INTERACTION_SEMANTIC_AND_TECHNICAL"
            and values.get("actual_asset_description_source")
            == "SAME_INTERACTION_MODEL_OUTPUT"
            and values.get("asset_semantic_attestation_hash")
            and values.get("asset_inspection_evidence_hash")
            == values.get("asset_semantic_attestation_hash")
            and values.get("observed_output_summary")
            and values.get("observed_primary_subjects")
            and values.get("observed_action_or_relation")
            and values.get("observed_environment")
            and values.get("observed_semantic_anchors") == required
            and values.get("provider_text_hash")
            and values.get("provider_semantic_match_asserted") is True
            and values.get("provider_forbidden_content_inspection_performed") is True
            and values.get("actual_asset_semantic_inspection_performed") is True
            and values.get(
                "same_interaction_model_output_semantic_inspection_performed"
            )
            is True
            and not values.get("sampled_frame_sha256")
            and values.get("motion_inspection_performed") is False
            and values.get("actual_asset_semantic_disposition")
            == "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
        )
    sampled = values.get("sampled_frame_sha256") or []
    return bool(
        values.get("provider_key") == "google_veo"
        and values.get("provider_request_hash")
        and values.get("provider_operation_id")
        and values.get("asset_inspection_scope")
        == "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY"
        and values.get("actual_asset_description_source")
        == "NO_AUTOMATED_ASSET_DESCRIPTION"
        and values.get("asset_semantic_attestation_hash") is None
        and values.get("asset_inspection_evidence_hash")
        and values.get("observed_output_summary") is None
        and not values.get("observed_primary_subjects")
        and values.get("observed_action_or_relation") is None
        and values.get("observed_environment") is None
        and not values.get("observed_semantic_anchors")
        and values.get("provider_text_hash") is None
        and values.get("provider_semantic_match_asserted") is False
        and values.get("provider_forbidden_content_inspection_performed") is False
        and values.get("actual_asset_semantic_inspection_performed") is False
        and values.get("same_interaction_model_output_semantic_inspection_performed")
        is False
        and len(sampled) >= 3
        and len(set(sampled)) >= 2
        and values.get("motion_inspection_performed") is True
        and values.get("actual_asset_semantic_disposition")
        == "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
    )


class AIVisualSceneCrossModalBinding(BaseModel):
    """One presentation scene's narration-to-actual-asset binding proof."""

    schema_version: Literal["vcos.ai-visual-scene-cross-modal-binding.v1"] = (
        "vcos.ai-visual-scene-cross-modal-binding.v1"
    )
    scene_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    narration_unit_ids: list[str] = Field(min_length=1)
    information_unit_ids: list[str] = Field(min_length=1)
    narration_semantic_intents: list[str] = Field(min_length=1)
    scene_meaning: str = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=_SHA256)
    presentation_start_ms: int = Field(ge=0)
    presentation_end_ms: int = Field(gt=0)
    asset_slot_id: str = Field(min_length=1)
    generation_owner_scene_id: str = Field(min_length=1)
    asset_route: Literal["AI_IMAGE", "AI_VIDEO"]
    asset_inspection_scope: AIVisualAssetInspectionScope
    asset_checksum: str = Field(pattern=_SHA256)
    asset_attestation_hash: str = Field(pattern=_SHA256)
    narration_information_binding_verified: Literal[True] = True
    narration_timing_binding_verified: Literal[True] = True
    semantic_intent_projection_verified: Literal[True] = True
    asset_scene_binding_verified: Literal[True] = True
    technical_asset_inspection_performed: Literal[True] = True
    motion_inspection_performed: bool
    actual_asset_semantic_inspection_performed: bool
    actual_asset_semantic_conformity_verified: Literal[False] = False
    automated_disposition_scope: Literal[
        "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
    ] = "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
    deterministic_disposition: Literal["PASS"] = "PASS"
    content_hash: str = Field(pattern=_SHA256)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_binding(self) -> "AIVisualSceneCrossModalBinding":
        if (
            self.presentation_end_ms <= self.presentation_start_ms
            or len(self.narration_unit_ids) != len(set(self.narration_unit_ids))
            or len(self.information_unit_ids) != len(set(self.information_unit_ids))
            or (
                self.asset_route == "AI_IMAGE"
                and (
                    self.asset_inspection_scope
                    != "IMAGE_SAME_INTERACTION_SEMANTIC_AND_TECHNICAL"
                    or not self.actual_asset_semantic_inspection_performed
                    or self.motion_inspection_performed
                )
            )
            or (
                self.asset_route == "AI_VIDEO"
                and (
                    self.asset_inspection_scope
                    != "VIDEO_LINEAGE_TECHNICAL_AND_MOTION_ONLY"
                    or self.actual_asset_semantic_inspection_performed
                    or not self.motion_inspection_performed
                )
            )
            or not _hash_matches(self)
        ):
            raise ValueError("AI_VISUAL_SCENE_CROSS_MODAL_BINDING_INVALID")
        return self

    @classmethod
    def build(cls, **values: Any) -> "AIVisualSceneCrossModalBinding":
        body = {
            "schema_version": "vcos.ai-visual-scene-cross-modal-binding.v1",
            "narration_information_binding_verified": True,
            "narration_timing_binding_verified": True,
            "semantic_intent_projection_verified": True,
            "asset_scene_binding_verified": True,
            "technical_asset_inspection_performed": True,
            "actual_asset_semantic_conformity_verified": False,
            "automated_disposition_scope": (
                "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
            ),
            "deterministic_disposition": "PASS",
            **values,
        }
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class AIVisualCrossModalQCReport(BaseModel):
    """Fail-closed automated lineage report with an explicit human boundary."""

    schema_version: Literal["vcos.ai-visual-cross-modal-qc.v1"] = (
        "vcos.ai-visual-cross-modal-qc.v1"
    )
    evidence_scope: Literal["LINEAGE_AND_SCENE_BINDING"] = "LINEAGE_AND_SCENE_BINDING"
    automated_disposition_scope: Literal[
        "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
    ] = "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
    canonical_timeline_hash: str = Field(pattern=_SHA256)
    ai_visual_scene_plan_artifact_hash: str = Field(pattern=_SHA256)
    ai_visual_scene_plan_compilation_hash: str = Field(pattern=_SHA256)
    asset_manifest_hash: str = Field(pattern=_SHA256)
    verified_effect_evidence_set_hash: str = Field(pattern=_SHA256)
    asset_attestations: list[AIVisualAssetSemanticAttestation] = Field(min_length=1)
    scene_bindings: list[AIVisualSceneCrossModalBinding] = Field(min_length=1)
    image_asset_count: int = Field(ge=0)
    video_asset_count: int = Field(ge=0)
    image_same_interaction_semantic_attestation_count: int = Field(ge=0)
    video_technical_motion_inspection_count: int = Field(ge=0)
    semantic_inspected_asset_count: int = Field(ge=0)
    semantic_uninspected_asset_count: int = Field(ge=0)
    deterministic_disposition: Literal["PASS"] = "PASS"
    actual_asset_description_source: Literal[
        "SAME_INTERACTION_MODEL_OUTPUT",
        "NO_AUTOMATED_ASSET_DESCRIPTION",
        "MIXED_IMAGE_ATTESTATION_AND_VIDEO_TECHNICAL_EVIDENCE",
    ]
    image_same_interaction_semantic_attestations_verified: bool
    same_interaction_asset_semantic_attestations_verified: bool
    any_asset_semantic_inspection_performed: bool
    actual_asset_semantic_inspection_performed: bool
    same_interaction_model_output_semantic_inspection_performed: bool
    video_actual_asset_semantic_inspection_performed: Literal[False] = False
    video_provider_semantic_match_asserted: Literal[False] = False
    video_technical_motion_evidence_verified: bool
    independent_multimodal_inspection_performed: Literal[False] = False
    actual_asset_semantic_disposition: Literal[
        "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW",
        "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW",
        "MIXED_IMAGE_ATTESTED_VIDEO_PENDING_HUMAN_SEMANTIC_REVIEW",
    ]
    automated_semantic_conformity_asserted: Literal[False] = False
    automated_pass_is_not_independent_semantic_conformity: Literal[True] = True
    human_semantic_review_required: Literal[True] = True
    human_final_review_required: Literal[True] = True
    content_hash: str = Field(pattern=_SHA256)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_report(self) -> "AIVisualCrossModalQCReport":
        asset_slots = [item.asset_slot_id for item in self.asset_attestations]
        scene_ids = [item.scene_id for item in self.scene_bindings]
        attestations_by_hash = {
            item.content_hash: item for item in self.asset_attestations
        }
        images = [item for item in self.asset_attestations if item.route == "AI_IMAGE"]
        videos = [item for item in self.asset_attestations if item.route == "AI_VIDEO"]
        image_semantic_count = sum(
            item.same_interaction_model_output_semantic_inspection_performed
            and item.provider_semantic_match_asserted
            for item in images
        )
        video_technical_count = sum(
            item.technical_asset_inspection_performed
            and item.motion_inspection_performed
            and not item.actual_asset_semantic_inspection_performed
            for item in videos
        )
        semantic_count = sum(
            item.actual_asset_semantic_inspection_performed
            for item in self.asset_attestations
        )
        if images and videos:
            expected_source = "MIXED_IMAGE_ATTESTATION_AND_VIDEO_TECHNICAL_EVIDENCE"
            expected_semantic_disposition = (
                "MIXED_IMAGE_ATTESTED_VIDEO_PENDING_HUMAN_SEMANTIC_REVIEW"
            )
        elif videos:
            expected_source = "NO_AUTOMATED_ASSET_DESCRIPTION"
            expected_semantic_disposition = (
                "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
            )
        else:
            expected_source = "SAME_INTERACTION_MODEL_OUTPUT"
            expected_semantic_disposition = (
                "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
            )
        if (
            len(asset_slots) != len(set(asset_slots))
            or len(scene_ids) != len(set(scene_ids))
            or set(scene_ids)
            != {
                scene_id
                for item in self.asset_attestations
                for scene_id in item.bound_scene_ids
            }
            or any(
                item.asset_attestation_hash not in attestations_by_hash
                for item in self.scene_bindings
            )
            or any(
                item.scene_id
                not in attestations_by_hash[item.asset_attestation_hash].bound_scene_ids
                or item.asset_slot_id
                != attestations_by_hash[item.asset_attestation_hash].asset_slot_id
                or item.generation_owner_scene_id
                != attestations_by_hash[
                    item.asset_attestation_hash
                ].primary_asset_owner_scene_id
                or item.asset_route
                != attestations_by_hash[item.asset_attestation_hash].route
                or item.asset_inspection_scope
                != attestations_by_hash[
                    item.asset_attestation_hash
                ].asset_inspection_scope
                or item.asset_checksum
                != attestations_by_hash[item.asset_attestation_hash].asset_checksum
                or item.actual_asset_semantic_inspection_performed
                != attestations_by_hash[
                    item.asset_attestation_hash
                ].actual_asset_semantic_inspection_performed
                or item.motion_inspection_performed
                != attestations_by_hash[
                    item.asset_attestation_hash
                ].motion_inspection_performed
                for item in self.scene_bindings
            )
            or self.image_asset_count != len(images)
            or self.video_asset_count != len(videos)
            or self.image_same_interaction_semantic_attestation_count
            != image_semantic_count
            or self.video_technical_motion_inspection_count != video_technical_count
            or self.semantic_inspected_asset_count != semantic_count
            or self.semantic_uninspected_asset_count
            != len(self.asset_attestations) - semantic_count
            or self.actual_asset_description_source != expected_source
            or self.actual_asset_semantic_disposition != expected_semantic_disposition
            or self.image_same_interaction_semantic_attestations_verified
            != (image_semantic_count == len(images))
            or self.same_interaction_asset_semantic_attestations_verified
            != (image_semantic_count == len(self.asset_attestations))
            or self.any_asset_semantic_inspection_performed != bool(semantic_count)
            or self.actual_asset_semantic_inspection_performed
            != (semantic_count == len(self.asset_attestations))
            or self.same_interaction_model_output_semantic_inspection_performed
            != (image_semantic_count == len(self.asset_attestations))
            or self.video_technical_motion_evidence_verified
            != (video_technical_count == len(videos))
            or not _hash_matches(self)
        ):
            raise ValueError("AI_VISUAL_CROSS_MODAL_QC_REPORT_INVALID")
        return self

    @classmethod
    def build(cls, **values: Any) -> "AIVisualCrossModalQCReport":
        attestations = list(values.get("asset_attestations") or [])
        images = [item for item in attestations if item.route == "AI_IMAGE"]
        videos = [item for item in attestations if item.route == "AI_VIDEO"]
        image_semantic_count = sum(
            item.same_interaction_model_output_semantic_inspection_performed
            and item.provider_semantic_match_asserted
            for item in images
        )
        video_technical_count = sum(
            item.technical_asset_inspection_performed
            and item.motion_inspection_performed
            and not item.actual_asset_semantic_inspection_performed
            for item in videos
        )
        semantic_count = sum(
            item.actual_asset_semantic_inspection_performed for item in attestations
        )
        if images and videos:
            source = "MIXED_IMAGE_ATTESTATION_AND_VIDEO_TECHNICAL_EVIDENCE"
            semantic_disposition = (
                "MIXED_IMAGE_ATTESTED_VIDEO_PENDING_HUMAN_SEMANTIC_REVIEW"
            )
        elif videos:
            source = "NO_AUTOMATED_ASSET_DESCRIPTION"
            semantic_disposition = (
                "NOT_AUTOMATICALLY_INSPECTED_PENDING_HUMAN_SEMANTIC_REVIEW"
            )
        else:
            source = "SAME_INTERACTION_MODEL_OUTPUT"
            semantic_disposition = "PASS_SAME_INTERACTION_ATTESTED_PENDING_HUMAN_REVIEW"
        body = {
            "schema_version": "vcos.ai-visual-cross-modal-qc.v1",
            "evidence_scope": "LINEAGE_AND_SCENE_BINDING",
            "automated_disposition_scope": (
                "LINEAGE_SCENE_TIMING_TECHNICAL_AND_MOTION_ONLY"
            ),
            "image_asset_count": len(images),
            "video_asset_count": len(videos),
            "image_same_interaction_semantic_attestation_count": (image_semantic_count),
            "video_technical_motion_inspection_count": video_technical_count,
            "semantic_inspected_asset_count": semantic_count,
            "semantic_uninspected_asset_count": len(attestations) - semantic_count,
            "deterministic_disposition": "PASS",
            "actual_asset_description_source": source,
            "image_same_interaction_semantic_attestations_verified": (
                image_semantic_count == len(images)
            ),
            "same_interaction_asset_semantic_attestations_verified": (
                image_semantic_count == len(attestations)
            ),
            "any_asset_semantic_inspection_performed": bool(semantic_count),
            "actual_asset_semantic_inspection_performed": (
                semantic_count == len(attestations)
            ),
            "same_interaction_model_output_semantic_inspection_performed": (
                image_semantic_count == len(attestations)
            ),
            "video_actual_asset_semantic_inspection_performed": False,
            "video_provider_semantic_match_asserted": False,
            "video_technical_motion_evidence_verified": (
                video_technical_count == len(videos)
            ),
            "independent_multimodal_inspection_performed": False,
            "actual_asset_semantic_disposition": semantic_disposition,
            "automated_semantic_conformity_asserted": False,
            "automated_pass_is_not_independent_semantic_conformity": True,
            "human_semantic_review_required": True,
            "human_final_review_required": True,
            **values,
        }
        return cls(**body, content_hash=ai_visual_stable_hash(body))


__all__ = [
    "AIVisualAssetSemanticAttestation",
    "AIVisualCrossModalQCReport",
    "AIVisualSceneCrossModalBinding",
    "VeoTechnicalMotionInspectionEvidence",
    "VerifiedAIVisualEffectEvidence",
]
