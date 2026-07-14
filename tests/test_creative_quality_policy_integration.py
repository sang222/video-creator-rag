from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import uuid

import pytest
import yaml

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.caption_voice_quality import CaptionStylePolicy, CaptionSyncPolicy, NarrationPacingPolicy
from app.contracts.visual_direction import (
    VeoDurationFitThresholds,
    VisualRankingWeights,
    VisualRiskPenalties,
    VisualScoreThresholds,
)
from app.core.errors import ValidationFailureError
from app.db.models import CompiledChannelPolicySnapshot
from app.services import ChannelProfileCompiler, ChannelProfileService, ChannelWorkspaceService, CompanyService
from app.services.config_registry import ConfigRegistryService
from app.services.creative_quality_policy import (
    POLICY_FAMILIES,
    CreativeMediaQCPolicyConfig,
    CreativeQualityPolicyCatalog,
    HumanWatchabilityPolicyConfig,
    TypedCreativeQualityPolicySnapshot,
    VisualContinuityPolicyConfig,
    VisualLanguagePolicyConfig,
    typed_policy_snapshot,
)
from app.services.native_render_plan import stable_hash
from app.services.visual_direction import VisualDirectionCompiler


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config/creative_quality_policy_catalog.yaml"


def test_small_team_catalog_has_exactly_seven_typed_b_c_d_policy_families():
    service = CreativeQualityPolicyCatalog(CATALOG)
    snapshot = service.approved_snapshot("small-team-ai")
    typed = service.approved_typed_snapshot("small-team-ai")

    assert set(POLICY_FAMILIES) == {key for key in snapshot if key.endswith("_policy")}
    assert isinstance(typed, TypedCreativeQualityPolicySnapshot)
    assert isinstance(typed.narration_pacing_policy, NarrationPacingPolicy)
    assert isinstance(typed.caption_style_policy, CaptionStylePolicy)
    assert isinstance(typed.caption_sync_policy, CaptionSyncPolicy)
    assert isinstance(typed.visual_language_policy, VisualLanguagePolicyConfig)
    assert isinstance(typed.visual_continuity_policy, VisualContinuityPolicyConfig)
    assert isinstance(typed.creative_media_qc_policy, CreativeMediaQCPolicyConfig)
    assert isinstance(typed.human_watchability_policy, HumanWatchabilityPolicyConfig)
    assert typed.policy_ref == "creative-policy://small-team-ai/small-team-ai.creative-quality.v1"
    assert typed.policy_hash == snapshot["policy_hash"]

    direction = VisualDirectionCompiler().compile(
        channel_id="typed-fixture-channel",
        project_id="typed-fixture-project",
        format_identity_ref="artifact://format/typed-fixture",
        format_identity_hash=stable_hash("format-typed-fixture"),
        visual_strategy_profile_ref="artifact://visual-strategy/typed-fixture",
        visual_strategy_profile_hash=stable_hash("strategy-typed-fixture"),
        policy=snapshot,
    )
    thresholds = VisualScoreThresholds.from_policy(snapshot)
    ranking_weights = VisualRankingWeights.from_policy(snapshot)
    risk_penalties = VisualRiskPenalties.from_policy(snapshot)
    duration_fit = VeoDurationFitThresholds.from_policy(snapshot)

    assert direction.content_hash
    assert direction.treatment_mode == typed.visual_language_policy.treatment_mode
    assert thresholds.semantic_pass_min == typed.visual_continuity_policy.semantic_match_score.pass_min
    assert thresholds.adjacency_review_min == typed.visual_continuity_policy.adjacency_continuity_score.review_min
    assert ranking_weights == typed.visual_continuity_policy.ranking_weights
    assert risk_penalties == typed.visual_continuity_policy.explicit_risk_penalties
    assert duration_fit == typed.visual_continuity_policy.veo_duration_fit
    assert typed.human_watchability_policy.optional_flagged_spot_check_speed == 0.75


def test_registry_rejects_malformed_inner_policy_not_just_missing_family(db_session, tmp_path):
    raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    raw["items"][0]["visual_continuity_policy"]["semantic_match_score"]["review_min"] = 0.90
    invalid = tmp_path / "invalid-creative-quality-policy.yaml"
    invalid.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationFailureError, match="CREATIVE_POLICY_SCORE_THRESHOLDS_INVALID"):
        ConfigRegistryService(db_session).validate_catalog(invalid)


def test_policy_services_are_channel_agnostic_and_registry_supports_multiple_channels(db_session, tmp_path):
    service_paths = (
        ROOT / "app/services/creative_quality_policy.py",
        ROOT / "app/services/config_registry.py",
        ROOT / "app/services/profile_compiler.py",
    )
    assert all("small-team-ai" not in path.read_text(encoding="utf-8") for path in service_paths)

    raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    second = deepcopy(raw["items"][0])
    second["channel_key"] = "fixture-channel"
    second["policy_version"] = "fixture-channel.creative-quality.v1"
    raw["items"].append(second)
    multi = tmp_path / "multi-channel-creative-quality-policy.yaml"
    multi.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    loaded = ConfigRegistryService(db_session).validate_catalog(multi)
    typed = CreativeQualityPolicyCatalog(multi).approved_typed_snapshot("fixture-channel")

    assert len(loaded.content["items"]) == 2
    assert typed.channel_id == "fixture-channel"
    assert typed.policy_version == "fixture-channel.creative-quality.v1"


def test_profile_compiler_projects_policy_into_new_snapshot_without_mutating_profile_content(db_session):
    suffix = uuid.uuid4().hex[:8]
    company = CompanyService(db_session).create_company(name=f"Creative policy {suffix}", slug=f"creative-{suffix}")
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="small-team-ai", name="Small Team AI"),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    profile_input_before = deepcopy(profile.profile_input)
    profile_hash_before = profile.profile_input_hash
    channel_metadata_before = deepcopy(channel.metadata_)
    compiler = ChannelProfileCompiler(db_session)

    first = compiler.compile(profile_version_id=profile.id, correlation_id="creative-policy-first")
    second = compiler.compile(profile_version_id=profile.id, correlation_id="creative-policy-second")
    snapshot = db_session.get(CompiledChannelPolicySnapshot, first.snapshot_id)
    policies = snapshot.compiled_payload["creative_quality_policies"]
    typed = typed_policy_snapshot(policies)

    db_session.refresh(profile)
    db_session.refresh(channel)
    assert second.snapshot_id == first.snapshot_id and second.content_hash == first.content_hash
    assert set(POLICY_FAMILIES) <= set(policies)
    assert typed.channel_id == channel.key
    assert policies["policy_ref"].startswith("creative-policy://")
    assert policies["policy_hash"] and policies["catalog_hash"]
    assert snapshot.compiled_payload["compiled_policy_snapshot_json"]["creative_quality_policies"] == policies
    assert profile.profile_input == profile_input_before
    assert profile.profile_input_hash == profile_hash_before
    assert channel.metadata_ == channel_metadata_before
