from __future__ import annotations

from pathlib import Path
import re


def patch_generic_strict_long_form_predicate() -> None:
    path = Path("app/services/m5.py")
    text = path.read_text(encoding="utf-8")
    text = text.replace("_is_nich1_strict_snapshot", "_is_strict_long_form_snapshot")
    pattern = re.compile(
        r"def _is_strict_long_form_snapshot\(snapshot: CompiledChannelPolicySnapshot\) -> bool:\n"
        r".*?\n\n\ndef _[a-zA-Z0-9_]",
        re.S,
    )
    replacement = '''def _is_strict_long_form_snapshot(snapshot: CompiledChannelPolicySnapshot) -> bool:
    """Recognize current long-form production authority without channel identity forks."""

    payload = snapshot.compiled_payload if isinstance(snapshot.compiled_payload, dict) else {}
    scoped = payload.get("channel_scoped_policy")
    capability = payload.get("capability_evaluation")
    if not isinstance(scoped, dict) or not isinstance(capability, dict):
        return False
    identity = scoped.get("channel_identity_policy")
    media = scoped.get("media_production_profile")
    providers = scoped.get("provider_usage_policy")
    publish = scoped.get("publish_policy")
    gates = scoped.get("gate_policy")
    if not all(isinstance(item, dict) for item in (identity, media, providers, publish, gates)):
        return False
    return bool(
        scoped.get("policy_version")
        and scoped.get("policy_status") == "APPROVED"
        and identity.get("primary_platform") == "YouTube"
        and identity.get("primary_format") == "long-form documentary/explainer"
        and media.get("final_render_authority") == "native_ffmpeg_renderer"
        and media.get("final_narration_authority") == "elevenlabs"
        and media.get("canonical_media_timeline_required") is True
        and media.get("youtube_private_stage_authority") is True
        and media.get("youtube_public_release_manual_only") is True
        and providers.get("native_ffmpeg_final_render_authority") is True
        and providers.get("youtube_private_stage_required_before_cleanup") is True
        and providers.get("youtube_public_release_api_allowed") is False
        and publish.get("youtube_private_stage_required") is True
        and publish.get("manual_public_release_only") is True
        and publish.get("human_final_approval_required") is True
        and gates.get("hard_policy_cannot_be_weakened") is True
        and gates.get("no_gate_weakening") is True
        and capability.get("status") == "PASS"
    )


def _'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"strict long-form predicate replacement expected 1 match, found {count}")
    path.write_text(text, encoding="utf-8")


def rewrite_generic_qualification_factory() -> None:
    Path("tests/qualification/conftest.py").write_text(
        r'''from __future__ import annotations

import shutil
import tempfile
import uuid
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.contracts import (
    ChannelProfileDraftUpdate,
    ChannelProfileInput,
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
)
from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.m5 import (
    EditorialCalendarSlotCreate,
    EditorialIdeaCandidateCreate,
    EditorialIdeaCandidateTransition,
    EditorialResearchRunCreate,
    IdeaMarketPreflightCreate,
    SearchDemandEvidenceCreate,
)
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.ops import ProviderRegistryEntryCreate
from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.vcos_v2 import AssignmentMode, LongFormPlanningRequest
from app.core.actor import authenticated_actor_context
from app.db.models import User, VideoProject
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    EditorialCalendarService,
    GateDefinitionService,
    IdeaMarketPreflightService,
    ProviderRegistryService,
    R3D1AdminService,
    RBACService,
    SearchDemandEvidenceService,
)
from app.services.creative_quality_policy import CreativeQualityPolicyCatalog
from app.services.editorial_research import EditorialResearchService
from app.services.ofv0 import FormatIdentityContractService
from app.services.production_package import ChannelDurationContractResolver
from app.services.vcos_v2 import LongFormPlanningService


ROOT = Path(__file__).resolve().parents[2]


def _market_locale(profile_input: ChannelProfileInput) -> dict:
    policies = profile_input.policies if isinstance(profile_input.policies, dict) else {}
    contract = policies.get("channel_contract") if isinstance(policies.get("channel_contract"), dict) else {}
    value = contract.get("market_locale") if isinstance(contract.get("market_locale"), dict) else {}
    return value


def _target_audience(profile_input: ChannelProfileInput) -> str:
    policies = profile_input.policies if isinstance(profile_input.policies, dict) else {}
    contract = policies.get("channel_contract") if isinstance(policies.get("channel_contract"), dict) else {}
    target = contract.get("target_audience") if isinstance(contract.get("target_audience"), dict) else {}
    return str(target.get("primary_persona") or profile_input.audience_segment)


def _content_pillar(profile_input: ChannelProfileInput) -> str:
    return str(profile_input.content_pillars[0])


def _duration_minutes(profile_input: ChannelProfileInput) -> tuple[float, float]:
    contract = profile_input.format_strategy.get("duration_contract")
    if isinstance(contract, dict):
        minimum_ms = contract.get("minimum_duration_ms")
        maximum_ms = contract.get("maximum_duration_ms")
        if isinstance(minimum_ms, (int, float)) and isinstance(maximum_ms, (int, float)):
            return max(float(minimum_ms) / 60000.0, 0.1), max(float(maximum_ms) / 60000.0, 0.1)
    return 6.0, 12.0


def _generic_format_identity(profile_input: ChannelProfileInput) -> dict:
    audience = _target_audience(profile_input)
    pillar = _content_pillar(profile_input)
    return {
        "identity_statement": f"Evidence-aware long-form documentary/explainer for {audience}.",
        "audience_recognition_cues": ["specific problem", "bounded mechanism", "evidence boundary", "practical takeaway"],
        "fixed_elements": ["documentary/explainer tone", "explanatory visual backbone", "evidence-aware claims", "human public-release review"],
        "must_vary_elements": ["hook family", "primary angle", "section order", "visual grammar", "thumbnail composition", "metadata pattern"],
        "allowed_hook_families": ["problem diagnosis", "mechanism", "tradeoff", "misconception"],
        "allowed_narrative_units": ["problem", "mechanism", "constraint", "evidence", "practical takeaway"],
        "preferred_visual_treatments": ["AI-authored explanatory still", "AI video", "native diagram", "UI flow", "data mechanism"],
        "limited_visual_treatments": ["supporting contextual media"],
        "forbidden_visual_patterns": ["generic stock backbone", "synthetic human host", "fake product demo", "fake customer result"],
        "narration_style_rules": ["restrained", "clear", "no guaranteed outcome", "distinguish scenario from evidence"],
        "thumbnail_identity_rules": ["one specific tension", "truthful readable text", "vary composition"],
        "metadata_identity_rules": ["plain promise", "do not exceed evidence", "no implied affiliation"],
        "intro_outro_policy": {"intro_reuse_allowed": True, "outro_reuse_allowed": True, "main_body_material_difference_required": True},
        "character_policy_mode": "NO_CHARACTER",
        "claim_policy_summary": "Material claims require evidence authority and bounded wording.",
        "synthetic_media_policy_summary": "AI media remains subject to disclosure and human final review.",
        "stock_usage_policy_summary": "Stock is not a primary visual authority in the generic fixture.",
        "ai_hero_usage_policy_summary": "AI image/video may carry authored explanatory or hero visuals when policy permits.",
        "comparison_window_size": 10,
        "originality_risk_thresholds": {"hook_family_review_frequency": 3, "comparison_window_max": 20, "exact_duplicate": "BLOCK"},
        "fixture_content_pillar": pillar,
    }


def _generic_creative_catalog(channel_key: str, config_dir: Path) -> dict:
    source = yaml.safe_load((ROOT / "config/creative_quality_policy_catalog.yaml").read_text(encoding="utf-8"))
    item = deepcopy(source["items"][0])
    item["channel_key"] = channel_key
    item["policy_version"] = f"{channel_key}.creative-quality.v1"
    visual = item["visual_language_policy"]
    visual["treatment_mode"] = "generic-evidence-aware-documentary"
    visual["environment_type"] = "context-dependent-real-or-authored-environment"
    visual["industry_context"] = "general"
    visual["tone_mode"] = "calm-evidence-aware-documentary"
    visual["channel_identity_markers"] = ["authored explanatory visuals", "restrained camera language", "truthful native overlays"]
    continuity = item["visual_continuity_policy"]
    continuity["provider_source_rules"] = {
        "NATIVE_VISUAL": "native_composition_and_exact_content",
        "AI_IMAGE": "authored_editorial_still",
        "AI_VIDEO": "hero_metaphor_signature_or_transition",
    }
    source["items"] = [item]
    path = config_dir / "creative_quality_policy_catalog.yaml"
    path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    return CreativeQualityPolicyCatalog(path).approved_snapshot(channel_key)


def _generic_channel_policy(
    *,
    channel_key: str,
    profile_input: ChannelProfileInput,
    format_contract,
    creative_snapshot: dict,
) -> ChannelScopedPolicy:
    market = _market_locale(profile_input)
    minimum_minutes, maximum_minutes = _duration_minutes(profile_input)
    primary_market = str(market.get("primary_market") or profile_input.target_market)
    locale = str(market.get("audience_locale") or market.get("primary_locale") or "en")
    content_language = str(market.get("content_language") or "en")
    operator_language = str(market.get("operator_language") or content_language)
    tone = str(profile_input.voice_style.get("tone") or "calm documentary explainer")
    format_ref = f"format-identity://{channel_key}/v{format_contract.contract_version}"
    policy = {
        "channel_key": channel_key,
        "policy_version": f"{channel_key}.long-form-policy.v1",
        "policy_status": "APPROVED",
        "approval_ref": f"operator-approval://qualification/{channel_key}/long-form-policy-v1",
        "channel_identity_policy": {
            "channel_key": channel_key,
            "primary_market": primary_market,
            "locale": locale,
            "content_language": content_language,
            "operator_language": operator_language,
            "primary_platform": "YouTube",
            "primary_format": "long-form documentary/explainer",
        },
        "audience_pacing_profile": {
            "audience": _target_audience(profile_input),
            "target_runtime_minutes": {"minimum": minimum_minutes, "maximum": maximum_minutes},
            "tone": tone,
            "sentence_style": "short natural evidence-aware sentences",
            "pre_tts_estimate_advisory_only": True,
        },
        "format_identity_contract": {"ref": format_ref, "version": str(format_contract.contract_version), "content_hash": format_contract.content_hash, "status": "APPROVED"},
        "channel_visual_strategy_profile": {
            "strategy_label": "ai-authored-long-form-with-native-composition",
            "native_explanatory_target_range": {"minimum": 0.35, "maximum": 0.70},
            "supporting_visual_target_range": {"minimum": 0.0, "maximum": 0.20},
            "ai_hero_target_range": {"minimum": 0.20, "maximum": 0.60},
            "ranges_are_planning_guidance_only": True,
            "minimum_pexels_quota": 0,
            "minimum_veo_quota": 0,
            "asset_selected_only_to_satisfy_ratio": False,
            "native_preferred_scene_kinds": ["mechanism", "data", "text", "workflow", "UI"],
            "pexels_allowed_scene_kinds": ["supporting context only"],
            "veo_allowed_scene_kinds": ["hero", "metaphor", "signature beat"],
            "forced_provider_alternation": False,
        },
        "media_production_profile": {
            "native_visual_backbone": True,
            "final_render_authority": "native_ffmpeg_renderer",
            "final_narration_authority": "elevenlabs",
            "canonical_media_timeline_required": True,
            "drive_verified_archive_only": False,
            "youtube_manual_upload_only": False,
            "youtube_private_stage_authority": True,
            "youtube_public_release_manual_only": True,
        },
        "voice_policy": {
            "provider": "elevenlabs",
            "voice_id": f"qualification-{channel_key}",
            "voice_name": "Qualification Narrator",
            "model_id": "eleven_multilingual_v2",
            "commercial_use_state": "APPROVED_PLAN_REQUIRED",
            "pronunciation_dictionary_refs": [],
            "settings": {"speed": 0.95, "stability": 0.55, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True},
            "one_complete_narration_preferred": True,
            "forced_alignment_required": True,
            "canonical_media_timeline_required": True,
            "unavailable_behavior": "BLOCK_FOR_REVIEW",
            "paid_retry_cap": 1,
            "retry_requires_new_approval": True,
        },
        "creative_quality_binding": {
            "policy_ref": creative_snapshot["policy_ref"],
            "policy_version": creative_snapshot["policy_version"],
            "source_run_id": "qualification-generic-creative-policy",
            "required_families": ["narration_pacing_policy", "subtitle_sidecar_policy", "caption_sync_policy", "visual_language_policy", "visual_continuity_policy", "creative_media_qc_policy", "human_watchability_policy"],
        },
        "character_policy": {"mode": "NO_CHARACTER", "recurring_host_allowed": False, "real_person_likeness_allowed": False},
        "provider_usage_policy": {
            "pexels": {"enabled": False, "optional": True, "role": "SUPPORTING_ONLY", "semantic_fit_threshold": 0.78, "max_searches_per_video": 0, "max_downloads_per_video": 0, "factual_evidence_allowed": False, "recurring_host_allowed": False, "rights_provenance_required": True, "minimum_quota": 0},
            "google_veo": {"enabled": True, "optional": True, "role": "AI_HERO_ONLY", "allowed_hero_reasons": ["HOOK", "METAPHOR", "EMOTIONAL_PAYOFF", "VISUAL_SIGNATURE", "NATIVE_MOTION_INSUFFICIENT"], "approved_model_catalog_ref": "config://google_veo_model_price_catalog/current", "max_hero_clips_per_video": 1, "max_hero_seconds_per_video": 8.0, "max_hero_cost_usd_per_video": 1.0, "provider_audio_policy": "DISCARD", "unavailable_behavior": "NATIVE_VISUAL_OR_REVIEW", "external_provider_fallback_allowed": False, "minimum_quota": 0},
            "elevenlabs": {"enabled": True, "final_narration_authority": True, "forced_alignment_required": True, "initial_tts_attempts": 1, "controlled_retry_requires_new_approval": True},
            "native_ffmpeg_final_render_authority": True,
            "drive_archive_required_before_cleanup": False,
            "youtube_manual_publish_only": False,
            "youtube_private_stage_required_before_cleanup": True,
            "youtube_public_release_api_allowed": False,
        },
        "budget_policy": {
            "channel_stage": "NEW_UNPROVEN", "tier": "TIER_1_LOW_COST_PRODUCTION", "currency": "USD",
            "max_estimated_cost_per_video": 1.0, "max_actual_cost_per_video": 1.0,
            "max_paid_attempts_per_provider_per_video": 1, "max_veo_clips_per_video": 1,
            "max_veo_seconds_per_video": 8.0, "max_veo_cost_per_video": 1.0,
            "monthly_channel_budget": 20.0, "cost_overrun_review_required": True,
            "premium_experiment_permission": False, "resolution_state": "APPROVED_DETERMINISTIC",
            "derivation_refs": [f"qualification://{channel_key}/budget-policy"],
        },
        "evidence_policy": {"material_claim_ledger_required": True, "scenario_assumptions_required": True, "stock_is_not_factual_evidence": True, "source_rights_required": True, "human_full_watch_required": True},
        "originality_policy": {
            "format_identity_contract_ref": format_ref,
            "fixed_identity_elements": ["documentary/explainer tone", "evidence-aware claims", "human public-release review"],
            "must_vary_elements": ["hook family", "primary angle", "section order", "visual grammar", "thumbnail composition", "metadata pattern"],
            "hook_repetition_budget": 3, "thumbnail_grammar_repetition_budget": 3,
            "asset_reuse_checks_required": True, "hero_concept_reuse_checks_required": True,
            "rolling_same_channel_comparison_scope": 10, "cross_channel_duplication_awareness": True,
        },
        "publish_policy": {
            "primary_destination": "YouTube", "manual_upload_only": False,
            "manual_public_release_only": True, "youtube_private_stage_required": True,
            "youtube_category_id": "28", "youtube_made_for_kids": False, "youtube_default_tags": [],
            "synthetic_media_disclosure_required": True, "rights_license_complete_required": True,
            "metadata_thumbnail_truthfulness_required": True, "human_final_approval_required": True,
            "drive_archive_required": False, "local_purge_after_archive_state": "YOUTUBE_PRIVATE_VERIFIED",
        },
        "analytics_maturity_policy": {"maturity": "NEW_UNPROVEN", "learning_promotion_allowed": False, "minimum_published_episode_count_before_promotion": 10},
        "gate_policy": {"hard_policy_cannot_be_weakened": True, "technical_media_qc_required": True, "creative_perceptual_media_qc_required": True, "human_full_watch_required": True, "no_gate_weakening": True},
        "capability_requirements": {"required": ["profile_compiler", "policy_snapshot", "format_identity_contract", "creative_quality_policy", "native_ffmpeg_renderer", "canonical_media_timeline", "elevenlabs", "forced_alignment", "google_veo_optional", "youtube_private_stage", "manual_youtube_public_release"], "launch_requires_all": True},
    }
    return ChannelScopedPolicy.model_validate(policy)


class QualificationFactory:
    """Generic PostgreSQL-backed factory compiled by one channel-profile engine."""

    def __init__(self, session):
        self.session = session

    def seed_all(self) -> None:
        ConfigRegistryService(self.session).seed([ROOT / "config"])
        registry = ProviderRegistryService(self.session)
        if registry.get_entry("openai") is None:
            registry.create_entry(
                data=ProviderRegistryEntryCreate(
                    provider_key="openai",
                    provider_name="OpenAI Responses Router",
                    provider_type="LLM",
                    capability_blob={"llm_router_lane_bound": True, "guarded_real_execution": True},
                    policy_fit_blob={"production_enabled_when_configured": True},
                    metadata={"readiness_provider_key": "openai"},
                )
            )
        GateDefinitionService(self.session).seed_definitions()

    def user(self, *, role_key: str = "operator", company_id=None, email_prefix: str = "qual") -> User:
        user = User(email=f"{email_prefix}-{uuid.uuid4().hex[:10]}@example.com", display_name=email_prefix, status="active")
        self.session.add(user)
        self.session.flush()
        if company_id is not None:
            RBACService(self.session).assign_role(user_id=user.id, role_key=role_key, company_id=company_id)
        return user

    def channel_scope(self, *, name: str = "Qualification", strict_long_form: bool = False, template_key: str | None = None) -> SimpleNamespace:
        self.seed_all()
        company = CompanyService(self.session).create_company(name=f"{name} Co")
        operator = self.user(role_key="operator", company_id=company.id, email_prefix="operator")
        admin = self.user(role_key="company_admin", company_id=company.id, email_prefix="admin")
        channel = ChannelWorkspaceService(self.session).create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(key=f"qualification-{uuid.uuid4().hex[:12]}", name=f"{name} Channel"),
        )
        if template_key is None:
            compiler_policy = ConfigRegistryService(self.session).validate_catalog(ROOT / "config/profile_compiler_policy.yaml")
            allowed = [str(item) for item in compiler_policy.content["items"][0]["allowed_template_keys"]]
            if not allowed:
                raise RuntimeError("profile compiler exposes no allowed test template")
            template_key = sorted(allowed)[0]
        profiles = ChannelProfileService(self.session)
        profile = profiles.create_profile_version(
            channel_id=channel.id,
            data=ChannelProfileVersionCreate(template_key=template_key, created_by=admin.id),
        )
        profile_input = ChannelProfileInput.model_validate(profile.profile_input)
        format_contract = FormatIdentityContractService(self.session).draft(
            FormatIdentityContractDraftRequest(
                channel_id=channel.id,
                channel_profile_version_id=profile.id,
                content=_generic_format_identity(profile_input),
                created_by="QualificationFactory",
            )
        )
        format_contract = FormatIdentityContractService(self.session).approve(
            format_contract.id,
            decided_by="qualification-operator",
        )
        with tempfile.TemporaryDirectory(prefix="vcos-qualification-") as temp_root:
            config_dir = Path(temp_root) / "config"
            shutil.copytree(ROOT / "config", config_dir)
            creative_snapshot = _generic_creative_catalog(channel.key, config_dir)
            policy = _generic_channel_policy(
                channel_key=channel.key,
                profile_input=profile_input,
                format_contract=format_contract,
                creative_snapshot=creative_snapshot,
            )
            profile_payload = profile_input.model_dump(mode="json")
            profile_payload["channel_policy"] = policy.model_dump(mode="json")
            profile_input = ChannelProfileInput.model_validate(profile_payload)
            profile = profiles.update_draft(
                profile_version_id=profile.id,
                data=ChannelProfileDraftUpdate(
                    profile_input=profile_input,
                    expected_profile_input_hash=profile.profile_input_hash,
                ),
                correlation_id=f"qualification-policy-{uuid.uuid4().hex[:8]}",
            )
            compiler = ChannelProfileCompiler(self.session, config_dir=config_dir)
            compiled = compiler.compile(
                profile_version_id=profile.id,
                correlation_id=f"qualification-compile-{uuid.uuid4().hex[:8]}",
            )
        profiles.submit_for_approval(profile.id)
        profiles.approve_profile_version(
            profile_version_id=profile.id,
            approved_by=admin.id,
            approval_ref=f"operator-approval://qualification/{channel.key}/profile-v{profile.version}",
        )
        snapshot = profiles.activate_snapshot(snapshot_id=compiled.snapshot_id)
        profile = profiles.get_profile_version(profile.id)
        return SimpleNamespace(company=company, channel=channel, profile=profile, snapshot=snapshot, operator=operator, admin=admin, compiled=compiled)

    def m5_admitted_project(
        self,
        *,
        evidence_volume: int | None = 1200,
        mock_mode: str = "success",
        quota_limit: Decimal | None = None,
        provider_health_mode: str | None = None,
    ) -> SimpleNamespace:
        """Build a generic research/preflight fixture; no provider execution occurs."""

        scope = self.channel_scope(name="M5", strict_long_form=True)
        profile_input = ChannelProfileInput.model_validate(scope.profile.profile_input)
        market = _market_locale(profile_input)
        primary_market = str(market.get("primary_market") or profile_input.target_market)
        locale = str(market.get("audience_locale") or market.get("primary_locale") or "en")
        pillar = _content_pillar(profile_input)
        audience = _target_audience(profile_input)
        permissions = RBACService(self.session).permissions_for_user(user_id=scope.operator.id, company_id=scope.company.id)
        actor = authenticated_actor_context(
            canonical_user_id=scope.operator.id,
            operator_user_id=scope.operator.id,
            actor_role="PRODUCER",
            permissions=permissions,
        )
        category = R3D1AdminService(self.session).create_content_category(
            ContentCategoryCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                category_key=f"default-{uuid.uuid4().hex[:8]}",
                name="Generic Long-form Category",
                sub_niche=profile_input.display_name,
                audience_segment=audience,
                content_pillar=pillar,
                character_policy_mode="NO_CHARACTER",
                status="ACTIVE",
            )
        )
        slot = EditorialCalendarService(self.session).create_slot(
            data=EditorialCalendarSlotCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                category_id=category.id,
                slot_date=date(2026, 6, 24),
                slot_type="RESEARCH",
                schema_version="v2",
                production_lane="LONG_FORM",
                assignment_mode=AssignmentMode.OPEN_MIX,
                production_goal="Explain one bounded workflow with evidence and a human approval boundary",
                target_platforms=["YOUTUBE"],
                content_pillar=pillar,
                format_hint="long-form documentary/explainer",
                created_by_user_id=scope.operator.id,
            )
        )
        evidence = None
        if evidence_volume is not None:
            evidence = SearchDemandEvidenceService(self.session).create_evidence(
                data=SearchDemandEvidenceCreate(
                    company_id=scope.company.id,
                    channel_workspace_id=scope.channel.id,
                    evidence_source_type="MANUAL_RESEARCH",
                    authority_purpose="MARKET_DEMAND",
                    query="bounded workflow approval boundary",
                    platform="YOUTUBE",
                    geo=primary_market,
                    language=locale,
                    search_volume_30d=evidence_volume,
                    relative_interest_index=Decimal("70"),
                    competition_index=Decimal("0.30"),
                    evidence_confidence="MEDIUM",
                )
            )
        research = EditorialResearchService(self.session)
        research_run = research.create_run(
            data=EditorialResearchRunCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                channel_profile_version_id=scope.profile.id,
                policy_snapshot_id=scope.snapshot.id,
                editorial_calendar_slot_id=slot.id,
                run_date=slot.slot_date,
                trigger_type="TEST",
                metadata={"provider_execution": "DISABLED"},
            ),
            actor=actor,
        )
        research.start_run(run_id=research_run.id, actor=actor)
        blocked_readiness = (
            (quota_limit is not None and quota_limit <= 0)
            or provider_health_mode == "unavailable"
            or mock_mode != "success"
        )
        candidate = research.add_candidate(
            data=EditorialIdeaCandidateCreate(
                editorial_research_run_id=research_run.id,
                proposed_title="How to Audit One Workflow Before Automation Commits a Change",
                proposed_angle="Evidence-aware long-form walkthrough with a bounded human approval checkpoint.",
                proposed_format="long-form documentary/explainer",
                proposed_pillar=pillar,
                evidence_refs=[{"type": "search_demand_evidence", "id": str(evidence.id) if evidence else "missing"}],
                confidence_level="MEDIUM",
                budget_readiness="BLOCKED" if blocked_readiness else "READY",
                rights_policy_state="PASS",
                quality_state="BLOCK" if mock_mode != "success" else "PASS",
                experiment_phase="AUDIENCE_PROMISE",
            ),
            actor=actor,
        )
        preflight = IdeaMarketPreflightService(self.session).create_preflight(
            data=IdeaMarketPreflightCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                editorial_calendar_slot_id=slot.id,
                editorial_research_run_id=research_run.id,
                editorial_idea_candidate_id=candidate.id,
                demand_score=Decimal("60") if evidence else None,
                channel_fit_score=Decimal("0.90"),
                policy_fit_state="PASS",
                niche_contract_digest_ref=f"channel-contract://{scope.channel.id}",
                niche_contract_digest_hash="a" * 64,
                target_market_digest_ref=f"target-market://{scope.channel.id}/{primary_market}",
                target_market_digest_hash="b" * 64,
                editorial_slot_ref=f"editorial-slot://{slot.id}",
                content_category_ref=str(category.id),
                target_market=primary_market,
                market_scope=[primary_market],
                market_fit_score=Decimal("0.90"),
                market_fit_threshold=Decimal("0.60"),
                evidence_blob={"search_demand_evidence_ids": [str(evidence.id)] if evidence is not None else []},
            )
        )
        if preflight.decision == "PASS":
            research.transition_candidate(
                candidate_id=candidate.id,
                data=EditorialIdeaCandidateTransition(
                    target_stage="PREFLIGHT_PASS",
                    idea_market_preflight_id=preflight.id,
                    reason_codes=["STRICT_LONG_FORM_PREFLIGHT_PASS"],
                ),
                actor=actor,
            )
        if preflight.decision != "PASS" or blocked_readiness:
            research.complete_run(run_id=research_run.id, actor=actor)
            return SimpleNamespace(
                **scope.__dict__, actor=actor, category=category, slot=slot, evidence=evidence,
                quota_account=None, research_run=research_run, candidate=candidate, idea=candidate,
                preflight=preflight, admission=None, project=None,
            )
        research.transition_candidate(
            candidate_id=candidate.id,
            data=EditorialIdeaCandidateTransition(
                target_stage="GREENLIT",
                idea_market_preflight_id=preflight.id,
                reason_codes=["DETERMINISTIC_GREENLIGHT"],
            ),
            actor=actor,
        )
        duration = ChannelDurationContractResolver(self.session).resolve(
            profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            production_lane="LONG_FORM",
        )
        admission = LongFormPlanningService(self.session).admit(
            LongFormPlanningRequest(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                channel_profile_version_id=scope.profile.id,
                policy_snapshot_id=scope.snapshot.id,
                editorial_calendar_slot_id=slot.id,
                editorial_idea_candidate_id=candidate.id,
                idea_market_preflight_id=preflight.id,
                assignment_mode=AssignmentMode.OPEN_MIX,
                title=candidate.proposed_title,
                description=candidate.proposed_angle,
                category_id=category.id,
                niche_gate_passed=True,
                market_gate_passed=True,
                evidence_refs=list(candidate.evidence_refs),
                duration_contract=duration,
                created_by_user_id=scope.operator.id,
            )
        )
        project = self.session.get(VideoProject, admission.admitted_video_project_id)
        research.complete_run(run_id=research_run.id, actor=actor)
        return SimpleNamespace(
            **scope.__dict__, actor=actor, category=category, slot=slot, evidence=evidence,
            quota_account=None, research_run=research_run, candidate=candidate, idea=candidate,
            preflight=preflight, admission=admission, project=project,
        )


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)
''',
        encoding="utf-8",
    )


def strengthen_one_engine_proof() -> None:
    Path("tests/test_one_engine_many_profiles.py").write_text(
        '''from __future__ import annotations

from tests.qualification.conftest import QualificationFactory


def test_same_engine_compiles_two_isolated_channel_profiles(db_session) -> None:
    factory = QualificationFactory(db_session)
    channel_a = factory.channel_scope(name="Channel A", strict_long_form=True)
    channel_b = factory.channel_scope(name="Channel B", strict_long_form=True)

    policy_a = channel_a.snapshot.compiled_payload["channel_scoped_policy"]
    policy_b = channel_b.snapshot.compiled_payload["channel_scoped_policy"]
    assert channel_a.channel.id != channel_b.channel.id
    assert channel_a.profile.id != channel_b.profile.id
    assert channel_a.snapshot.id != channel_b.snapshot.id
    assert policy_a["channel_key"] == channel_a.channel.key
    assert policy_b["channel_key"] == channel_b.channel.key
    assert policy_a["channel_key"] != policy_b["channel_key"]
    assert channel_a.profile.channel_workspace_id == channel_a.channel.id
    assert channel_b.profile.channel_workspace_id == channel_b.channel.id
    assert channel_a.snapshot.channel_profile_version_id == channel_a.profile.id
    assert channel_b.snapshot.channel_profile_version_id == channel_b.profile.id
    assert channel_a.channel.active_policy_snapshot_id == channel_a.snapshot.id
    assert channel_b.channel.active_policy_snapshot_id == channel_b.snapshot.id
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_generic_strict_long_form_predicate()
    rewrite_generic_qualification_factory()
    strengthen_one_engine_proof()


if __name__ == "__main__":
    main()
