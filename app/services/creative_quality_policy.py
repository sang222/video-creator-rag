from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.caption_voice_quality import (
    CaptionStylePolicy,
    CaptionSyncPolicy,
    NarrationPacingPolicy,
)
from app.contracts.visual_direction import (
    VeoDurationFitThresholds,
    VisualRankingWeights,
    VisualRiskPenalties,
)


POLICY_FAMILIES = (
    "narration_pacing_policy",
    "caption_style_policy",
    "caption_sync_policy",
    "visual_language_policy",
    "visual_continuity_policy",
    "creative_media_qc_policy",
    "human_watchability_policy",
)


class VisualLanguagePolicyConfig(BaseModel):
    realism_level: str = Field(min_length=1)
    treatment_mode: str = Field(min_length=1)
    human_presence_policy: str = Field(min_length=1)
    environment_type: str = Field(min_length=1)
    industry_context: str = Field(min_length=1)
    time_of_day: str = Field(min_length=1)
    lighting_direction: str = Field(min_length=1)
    lighting_temperature: str = Field(min_length=1)
    palette: list[str] = Field(min_length=1)
    contrast: str = Field(min_length=1)
    saturation: str = Field(min_length=1)
    camera_distance: str = Field(min_length=1)
    lens_feel: str = Field(min_length=1)
    camera_movement: str = Field(min_length=1)
    motion_intensity: str = Field(min_length=1)
    framing_rule: str = Field(min_length=1)
    depth_of_field_style: str = Field(min_length=1)
    texture_grain: str = Field(min_length=1)
    tone_mode: str = Field(min_length=1)
    prohibited_cliches: list[str] = Field(default_factory=list)
    channel_identity_markers: list[str] = Field(default_factory=list)
    adjacent_scene_constraints: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class MinimumScorePolicy(BaseModel):
    pass_min: float = Field(ge=0, le=1)
    review_min: float = Field(ge=0, le=1)
    block_below: float = Field(ge=0, le=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "MinimumScorePolicy":
        if self.block_below != self.review_min or self.review_min > self.pass_min:
            raise ValueError("CREATIVE_POLICY_SCORE_THRESHOLDS_INVALID")
        return self


class VisualContinuityPolicyConfig(BaseModel):
    semantic_match_score: MinimumScorePolicy
    adjacency_continuity_score: MinimumScorePolicy
    ranking_weights: VisualRankingWeights
    explicit_risk_penalties: VisualRiskPenalties
    veo_duration_fit: VeoDurationFitThresholds
    hard_conflicts_block: bool
    cross_provider_cut_requires_both_scores_pass: bool
    provider_source_rules: dict[str, str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class MaximumDeltaPolicy(BaseModel):
    pass_max: float = Field(ge=0)
    review_max: float = Field(ge=0)
    block_above: float = Field(ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "MaximumDeltaPolicy":
        if not self.pass_max <= self.review_max <= self.block_above:
            raise ValueError("CREATIVE_POLICY_MAXIMUM_THRESHOLDS_INVALID")
        return self


class CreativeMediaQCPolicyConfig(BaseModel):
    aggregate_order: list[Literal["BLOCK", "REVIEW_REQUIRED", "PASS"]] = Field(min_length=3, max_length=3)
    technical_pass_does_not_imply_creative_pass: Literal[True]
    final_duration_consistency_ms: MaximumDeltaPolicy

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def block_first(self) -> "CreativeMediaQCPolicyConfig":
        if self.aggregate_order != ["BLOCK", "REVIEW_REQUIRED", "PASS"]:
            raise ValueError("CREATIVE_MEDIA_QC_AGGREGATE_ORDER_INVALID")
        return self


class HumanWatchabilityPolicyConfig(BaseModel):
    uninterrupted_full_watch_required: Literal[True]
    playback_speed: Literal[1.0]
    optional_flagged_spot_check_speed: float = Field(gt=0, le=1)
    dimensions: Literal[8]
    pass_total_min: int = Field(gt=0)
    pass_dimension_min: int = Field(ge=1, le=5)
    repair_total_range: tuple[int, int]
    critical_issue_overrides_score: Literal[True]
    codex_may_mark_pass: Literal[False]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "HumanWatchabilityPolicyConfig":
        if self.repair_total_range[0] > self.repair_total_range[1]:
            raise ValueError("HUMAN_WATCHABILITY_REPAIR_RANGE_INVALID")
        if self.repair_total_range[1] >= self.pass_total_min:
            raise ValueError("HUMAN_WATCHABILITY_PASS_RANGE_OVERLAP")
        return self


class TypedCreativeQualityPolicySnapshot(BaseModel):
    channel_id: str = Field(min_length=1)
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    narration_pacing_policy: NarrationPacingPolicy
    caption_style_policy: CaptionStylePolicy
    caption_sync_policy: CaptionSyncPolicy
    visual_language_policy: VisualLanguagePolicyConfig
    visual_continuity_policy: VisualContinuityPolicyConfig
    creative_media_qc_policy: CreativeMediaQCPolicyConfig
    human_watchability_policy: HumanWatchabilityPolicyConfig

    model_config = ConfigDict(extra="forbid")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


class CreativeQualityPolicyCatalog:
    """Read a versioned channel policy snapshot without mutating profile state."""

    def __init__(self, path: str | Path = "config/creative_quality_policy_catalog.yaml"):
        self.path = Path(path)

    def approved_snapshot(self, channel_id: str) -> dict[str, Any]:
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("catalog_key") != "creative_quality_policy_catalog"
            or raw.get("status") != "active"
        ):
            raise ValueError("CREATIVE_POLICY_CATALOG_NOT_ACTIVE")
        items = raw.get("items") or []
        matches = [item for item in items if item.get("channel_key") == channel_id]
        if len(matches) != 1:
            raise ValueError("CREATIVE_POLICY_CHANNEL_SNAPSHOT_NOT_UNIQUE")
        selected = matches[0]
        missing = [key for key in POLICY_FAMILIES if not isinstance(selected.get(key), dict)]
        if missing:
            raise ValueError("CREATIVE_POLICY_FAMILY_MISSING:" + ",".join(missing))
        body = {
            "channel_id": channel_id,
            "policy_version": selected["policy_version"],
            "catalog_version": str(raw["catalog_version"]),
            **{key: selected[key] for key in POLICY_FAMILIES},
        }
        snapshot = {
            **body,
            "policy_ref": f"creative-policy://{channel_id}/{selected['policy_version']}",
            "policy_hash": _hash(body),
            "catalog_hash": _hash(raw),
        }
        typed_policy_snapshot(snapshot)
        return snapshot

    def approved_typed_snapshot(self, channel_id: str) -> TypedCreativeQualityPolicySnapshot:
        return typed_policy_snapshot(self.approved_snapshot(channel_id))


def policy_family(snapshot: dict[str, Any], family: str) -> dict[str, Any]:
    if family not in POLICY_FAMILIES:
        raise ValueError("CREATIVE_POLICY_FAMILY_UNKNOWN")
    value = snapshot.get(family)
    if not isinstance(value, dict):
        raise ValueError("CREATIVE_POLICY_FAMILY_MISSING")
    return value


def typed_policy_snapshot(snapshot: Mapping[str, Any]) -> TypedCreativeQualityPolicySnapshot:
    metadata = {
        "policy_ref": snapshot.get("policy_ref"),
        "policy_version": snapshot.get("policy_version"),
        "policy_hash": snapshot.get("policy_hash"),
        "channel_id": snapshot.get("channel_id"),
    }

    def typed_family(name: str, model: type[BaseModel]) -> BaseModel:
        raw = snapshot.get(name)
        if not isinstance(raw, Mapping):
            raise ValueError(f"CREATIVE_POLICY_FAMILY_MISSING:{name}")
        return model.model_validate({**dict(raw), **metadata})

    payload = {
        "channel_id": snapshot.get("channel_id"),
        "policy_ref": snapshot.get("policy_ref"),
        "policy_version": snapshot.get("policy_version"),
        "policy_hash": snapshot.get("policy_hash"),
        "catalog_version": snapshot.get("catalog_version"),
        "narration_pacing_policy": typed_family("narration_pacing_policy", NarrationPacingPolicy),
        "caption_style_policy": typed_family("caption_style_policy", CaptionStylePolicy),
        "caption_sync_policy": typed_family("caption_sync_policy", CaptionSyncPolicy),
        "visual_language_policy": VisualLanguagePolicyConfig.model_validate(
            snapshot.get("visual_language_policy")
        ),
        "visual_continuity_policy": VisualContinuityPolicyConfig.model_validate(
            snapshot.get("visual_continuity_policy")
        ),
        "creative_media_qc_policy": CreativeMediaQCPolicyConfig.model_validate(
            snapshot.get("creative_media_qc_policy")
        ),
        "human_watchability_policy": HumanWatchabilityPolicyConfig.model_validate(
            snapshot.get("human_watchability_policy")
        ),
    }
    return TypedCreativeQualityPolicySnapshot.model_validate(payload)


def validate_creative_quality_policy_item(item: Mapping[str, Any]) -> TypedCreativeQualityPolicySnapshot:
    channel_id = str(item.get("channel_key") or "").strip()
    policy_version = str(item.get("policy_version") or "").strip()
    if not channel_id or not policy_version:
        raise ValueError("CREATIVE_POLICY_IDENTITY_REQUIRED")
    body = {
        "channel_id": channel_id,
        "policy_version": policy_version,
        "catalog_version": str(item.get("catalog_version") or "catalog-item-validation"),
        **{key: item.get(key) for key in POLICY_FAMILIES},
    }
    snapshot = {
        **body,
        "policy_ref": f"creative-policy://{channel_id}/{policy_version}",
        "policy_hash": _hash(body),
    }
    return typed_policy_snapshot(snapshot)
