from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.contracts.r3d1 import CharacterPolicyMode, RuntimeScopeErrorCode
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CharacterBinding,
    CharacterImageBranch,
    CharacterProfile,
    CharacterReferenceAssetPack,
    CharacterVersion,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    EditorialCalendarSlot,
    EffectiveChannelRuntimeContextSnapshot,
    VideoProject,
    VoiceProfile,
)
from app.services.channel_contract import CONTRACT_COMPLETE, contract_status_from_snapshot_payload
from app.services.config_registry import content_hash
from app.services.r3d1 import CharacterBindingResolver


PASS = "PASS"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCK = "BLOCK"
NO_CHARACTER = CharacterPolicyMode.NO_CHARACTER.value
REQUIRED_CHARACTER = CharacterPolicyMode.REQUIRED_CHARACTER.value
OPTIONAL_CHARACTER = CharacterPolicyMode.OPTIONAL_CHARACTER.value
_MISSING = object()


def _code(code: RuntimeScopeErrorCode) -> str:
    return code.value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if item not in (None, "")]


def _uuid_string(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _slot_character_binding_id(slot: EditorialCalendarSlot | None) -> uuid.UUID | None:
    payload = slot.character_binding_policy_json if slot is not None else None
    if not isinstance(payload, dict):
        return None
    value = payload.get("character_binding_id")
    if value in (None, ""):
        return None
    return uuid.UUID(str(value))


def _source_paths(*paths: str) -> list[str]:
    return list(paths)


@dataclass(frozen=True)
class _Authority:
    channel: ChannelWorkspace | None
    profile_version: ChannelProfileVersion | None
    policy_snapshot: CompiledChannelPolicySnapshot | None
    channel_contract_json: dict[str, Any]
    compiled_policy_json: dict[str, Any]
    field_source_map_json: dict[str, Any]
    channel_contract_hash: str | None
    field_source_map_hash: str | None
    reason_codes: list[str]


@dataclass(frozen=True)
class _CharacterRefs:
    binding: CharacterBinding | None
    profile: CharacterProfile | None
    version: CharacterVersion | None
    branch: CharacterImageBranch | None
    pack: CharacterReferenceAssetPack | None
    voice: VoiceProfile | None
    reason_codes: list[str]


class EffectiveChannelRuntimeContextCompiler:
    def __init__(self, session: Session):
        self.session = session

    def ensure_for_project(
        self,
        video_project_id: uuid.UUID,
        *,
        editorial_calendar_slot_id: uuid.UUID | None = None,
    ) -> EffectiveChannelRuntimeContextSnapshot:
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.effective_context_snapshot_id is not None:
            snapshot = self.session.get(EffectiveChannelRuntimeContextSnapshot, project.effective_context_snapshot_id)
            if snapshot is not None:
                return snapshot
        slot = self.session.get(EditorialCalendarSlot, editorial_calendar_slot_id) if editorial_calendar_slot_id else None
        return self.compile_for_project(project=project, editorial_calendar_slot=slot)

    def compile_for_project(
        self,
        *,
        project: VideoProject,
        editorial_calendar_slot: EditorialCalendarSlot | None = None,
        policy_snapshot_override: CompiledChannelPolicySnapshot | None | object = _MISSING,
    ) -> EffectiveChannelRuntimeContextSnapshot:
        authority = self._resolve_authority(project, policy_snapshot_override=policy_snapshot_override)
        category = self._resolve_category(project)
        character_refs = self._resolve_character_refs(project=project, category=category, slot=editorial_calendar_slot)
        block_reasons = [
            *authority.reason_codes,
            *self._category_reason_codes(project, category),
            *character_refs.reason_codes,
            *self._conflict_reason_codes(
                channel_contract=authority.channel_contract_json,
                compiled_policy=authority.compiled_policy_json,
                category=category,
            ),
        ]
        review_reasons = self._review_reason_codes(category)
        compile_status = BLOCK if block_reasons else REVIEW_REQUIRED if review_reasons else PASS
        reason_codes = _ordered_unique([*block_reasons, *review_reasons])
        subcontexts = self._build_subcontexts(
            project=project,
            channel=authority.channel,
            channel_contract=authority.channel_contract_json,
            compiled_policy=authority.compiled_policy_json,
            category=category,
            character_refs=character_refs,
        )
        source_refs = self._source_refs(
            project=project,
            authority=authority,
            category=category,
            character_refs=character_refs,
        )
        context_hash = content_hash(
            {
                "schema_version": "r3d2.effective_channel_runtime_context.v1",
                "compile_status": compile_status,
                "reason_codes": reason_codes,
                "source_refs": source_refs,
                "subcontexts": subcontexts,
            }
        )
        snapshot = EffectiveChannelRuntimeContextSnapshot(
            video_project_id=project.id,
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            channel_profile_version_id=authority.profile_version.id if authority.profile_version else None,
            compiled_policy_snapshot_id=authority.policy_snapshot.id if authority.policy_snapshot else None,
            channel_contract_hash=authority.channel_contract_hash,
            field_source_map_hash=authority.field_source_map_hash,
            content_category_id=category.id if category else None,
            character_binding_id=character_refs.binding.id if character_refs.binding else None,
            character_policy_mode=category.character_policy_mode if category else None,
            character_profile_id=character_refs.profile.id if character_refs.profile else None,
            character_version_id=character_refs.version.id if character_refs.version else None,
            character_image_branch_id=character_refs.branch.id if character_refs.branch else None,
            reference_asset_pack_id=character_refs.pack.id if character_refs.pack else None,
            voice_profile_id=character_refs.voice.id if character_refs.voice else None,
            compile_status=compile_status,
            reason_codes_json=reason_codes,
            source_refs_json=source_refs,
            context_hash=context_hash,
            **subcontexts,
        )
        self.session.add(snapshot)
        self.session.flush()
        project.effective_context_snapshot_id = snapshot.id
        if project.channel_contract_content_hash is None and authority.channel_contract_hash is not None:
            project.channel_contract_content_hash = authority.channel_contract_hash
        project.audience_delivery_summary = {
            **(project.audience_delivery_summary or {}),
            "effective_context_snapshot_id": str(snapshot.id),
            "effective_context_hash": snapshot.context_hash,
            "effective_context_compile_status": snapshot.compile_status,
        }
        self.session.flush()
        return snapshot

    def _resolve_authority(
        self,
        project: VideoProject,
        *,
        policy_snapshot_override: CompiledChannelPolicySnapshot | None | object,
    ) -> _Authority:
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        snapshot = (
            self.session.get(CompiledChannelPolicySnapshot, project.policy_snapshot_id)
            if policy_snapshot_override is _MISSING
            else policy_snapshot_override
        )
        if snapshot is None or channel is None or snapshot.channel_workspace_id != project.channel_workspace_id:
            return _Authority(
                channel=channel,
                profile_version=None,
                policy_snapshot=None,
                channel_contract_json={},
                compiled_policy_json={},
                field_source_map_json={},
                channel_contract_hash=None,
                field_source_map_hash=None,
                reason_codes=[_code(RuntimeScopeErrorCode.POLICY_SNAPSHOT_MISSING)],
            )
        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        payload = _dict(snapshot.compiled_payload)
        contract = _dict(payload.get("channel_contract_json"))
        compiled_policy = _dict(payload.get("compiled_policy_snapshot_json"))
        field_source_map = _dict(payload.get("field_source_map_json") or compiled_policy.get("field_source_map_json"))
        status, _, _ = contract_status_from_snapshot_payload(payload)
        reasons: list[str] = []
        if profile_version is None or profile_version.channel_workspace_id != project.channel_workspace_id:
            reasons.append(_code(RuntimeScopeErrorCode.POLICY_SNAPSHOT_MISSING))
        if snapshot.status != "active" or status != CONTRACT_COMPLETE or not contract:
            reasons.append(_code(RuntimeScopeErrorCode.CHANNEL_CONTRACT_NOT_COMPLETE))
        return _Authority(
            channel=channel,
            profile_version=profile_version,
            policy_snapshot=snapshot,
            channel_contract_json=contract,
            compiled_policy_json=compiled_policy,
            field_source_map_json=field_source_map,
            channel_contract_hash=content_hash(contract) if contract else None,
            field_source_map_hash=content_hash(field_source_map) if field_source_map else None,
            reason_codes=_ordered_unique(reasons),
        )

    def _resolve_category(self, project: VideoProject) -> ContentCategory | None:
        if project.category_id is None:
            return None
        category = self.session.get(ContentCategory, project.category_id)
        if (
            category is None
            or category.company_id != project.company_id
            or category.channel_workspace_id != project.channel_workspace_id
        ):
            return None
        return category

    def _category_reason_codes(self, project: VideoProject, category: ContentCategory | None) -> list[str]:
        if category is None:
            return [_code(RuntimeScopeErrorCode.CATEGORY_SCOPE_MISSING)]
        if category.status != "ACTIVE":
            return [_code(RuntimeScopeErrorCode.CATEGORY_NOT_ACTIVE)]
        return []

    def _resolve_character_refs(
        self,
        *,
        project: VideoProject,
        category: ContentCategory | None,
        slot: EditorialCalendarSlot | None,
    ) -> _CharacterRefs:
        if category is None:
            return _CharacterRefs(None, None, None, None, None, None, [])
        explicit_binding_id = project.character_binding_id or _slot_character_binding_id(slot)
        if category.character_policy_mode == REQUIRED_CHARACTER and explicit_binding_id is None:
            return _CharacterRefs(
                None,
                None,
                None,
                None,
                None,
                None,
                [_code(RuntimeScopeErrorCode.CHARACTER_REQUIRED_BUT_MISSING)],
            )
        resolution = CharacterBindingResolver(self.session).resolve(
            category=category,
            explicit_character_binding_id=explicit_binding_id,
        )
        binding = resolution.character_binding
        if binding is None and explicit_binding_id is not None:
            binding = self.session.get(CharacterBinding, explicit_binding_id)
        profile = self.session.get(CharacterProfile, binding.character_profile_id) if binding else None
        version = self.session.get(CharacterVersion, binding.character_version_id) if binding else None
        branch = self.session.get(CharacterImageBranch, binding.character_image_branch_id) if binding and binding.character_image_branch_id else None
        pack = (
            self.session.get(CharacterReferenceAssetPack, binding.reference_asset_pack_id)
            if binding and binding.reference_asset_pack_id
            else None
        )
        voice = self.session.get(VoiceProfile, binding.voice_profile_id) if binding and binding.voice_profile_id else None
        return _CharacterRefs(binding, profile, version, branch, pack, voice, resolution.reason_codes)

    def _conflict_reason_codes(
        self,
        *,
        channel_contract: dict[str, Any],
        compiled_policy: dict[str, Any],
        category: ContentCategory | None,
    ) -> list[str]:
        reasons: list[str] = []
        market = _dict(channel_contract.get("market_locale"))
        compiled_market = _dict(compiled_policy.get("market_locale"))
        for key in ("primary_market", "content_language"):
            if market.get(key) and compiled_market.get(key) and str(market[key]).lower() != str(compiled_market[key]).lower():
                reasons.append("MARKET_LANGUAGE_CONFLICT")
        timezone = market.get("timezone")
        publish_mode = _dict(channel_contract.get("platform_strategy")).get("publish_mode", "human_handoff_only")
        if publish_mode == "human_handoff_only" and not timezone:
            reasons.append("PUBLISH_TIMING_POLICY_MISSING")
        budget = _dict(channel_contract.get("budget_policy"))
        paid_allowed = budget.get("paid_provider_allowed")
        if paid_allowed is False and category is not None:
            requires_paid = any(
                _dict(value).get("requires_paid_provider") is True
                for value in [
                    category.default_format_policy_json,
                    category.default_visual_style_json,
                    category.default_voice_style_json,
                    category.default_thumbnail_style_json,
                ]
            )
            if requires_paid:
                reasons.append("PAID_PROVIDER_POLICY_CONFLICT")
        return _ordered_unique(reasons)

    def _review_reason_codes(self, category: ContentCategory | None) -> list[str]:
        if category is None:
            return []
        visual_style = _dict(category.default_visual_style_json)
        if visual_style.get("requires_style_note") is True and not (
            visual_style.get("style_note") or visual_style.get("style_notes")
        ):
            return ["OPTIONAL_STYLE_NOTE_MISSING"]
        return []

    def _build_subcontexts(
        self,
        *,
        project: VideoProject,
        channel: ChannelWorkspace | None,
        channel_contract: dict[str, Any],
        compiled_policy: dict[str, Any],
        category: ContentCategory | None,
        character_refs: _CharacterRefs,
    ) -> dict[str, dict[str, Any]]:
        market = _dict(channel_contract.get("market_locale"))
        audience = _dict(channel_contract.get("target_audience"))
        voice_style = _dict(channel_contract.get("voice_style"))
        editorial = _dict(channel_contract.get("editorial_strategy"))
        platform = _dict(channel_contract.get("platform_strategy"))
        media = _dict(channel_contract.get("media_policy"))
        rights = _dict(channel_contract.get("rights_policy"))
        budget = _dict(channel_contract.get("budget_policy"))
        learning = _dict(channel_contract.get("learning_policy"))
        monetization = _dict(compiled_policy.get("monetization_policy") or _dict(channel_contract.get("monetization_policy")))
        voice_profile = character_refs.voice
        version = character_refs.version
        branch = character_refs.branch
        pack = character_refs.pack
        channel_timezone = market.get("timezone") or (channel.primary_timezone if channel else None) or (channel.default_timezone if channel else None)
        content_language = market.get("content_language") or (channel.primary_language if channel else None)
        return {
            "market_locale_context_json": {
                "primary_market": market.get("primary_market") or (channel.target_market if channel else None),
                "locale": market.get("audience_locale"),
                "content_language": content_language,
                "target_regions": _strings(market.get("secondary_markets")) or _strings(channel.target_regions if channel else []),
                "channel_timezone": channel_timezone,
                "audience_timezone": market.get("audience_timezone") or channel_timezone,
                "currency_context": market.get("currency"),
                "spelling_style": market.get("spelling_style") or market.get("market_examples_preference"),
                "cultural_style_notes": market.get("cultural_style"),
                "source_contract_paths": _source_paths("market_locale"),
            },
            "audience_context_json": {
                "audience_segment": (category.audience_segment if category else None) or audience.get("primary_persona"),
                "audience_level": audience.get("audience_level"),
                "audience_pain_points": _strings(audience.get("pain_points")),
                "forbidden_assumptions": _strings(editorial.get("forbidden_assumptions")) or _strings(editorial.get("forbidden_angles")),
                "source_contract_paths": _source_paths("target_audience", "editorial_strategy.forbidden_angles"),
            },
            "brand_voice_persona_context_json": {
                "tone": voice_style.get("narration_tone"),
                "persona": _dict(character_refs.profile.persona_json) if character_refs.profile else {},
                "style_rules": _strings(voice_style.get("allowed_style")),
                "forbidden_style": _strings(voice_style.get("forbidden_style")),
                "source_contract_paths": _source_paths("voice_style"),
            },
            "category_runtime_context_json": {
                "category_id": _uuid_string(category.id) if category else None,
                "category_key": category.category_key if category else None,
                "sub_niche": category.sub_niche if category else None,
                "content_pillar": category.content_pillar if category else None,
                "default_format_policy": _dict(category.default_format_policy_json) if category else {},
                "source_contract_paths": _source_paths("format_policy", "editorial_strategy.content_pillars"),
            },
            "character_identity_context_json": {
                "character_policy_mode": category.character_policy_mode if category else None,
                "character_profile_id": _uuid_string(character_refs.profile.id) if character_refs.profile else None,
                "character_version_id": _uuid_string(character_refs.version.id) if character_refs.version else None,
                "character_image_branch_id": _uuid_string(branch.id) if branch else None,
                "reference_asset_pack_id": _uuid_string(pack.id) if pack else None,
                "allowed_character_refs": _dict(pack.pack_manifest_json) if pack else {},
                "forbidden_character_refs": [] if category and category.character_policy_mode != NO_CHARACTER else ["project_character_binding"],
                "no_character_constraints": {"no_prompt_based_character_continuity": True} if category and category.character_policy_mode == NO_CHARACTER else {},
                "identity": _dict(version.identity_json) if version else {},
                "continuity_rules": _dict(version.continuity_rules_json) if version else {},
                "source_contract_paths": _source_paths("category.character_policy_mode"),
            },
            "visual_style_context_json": {
                "visual_style": _dict(category.default_visual_style_json) if category else {},
                "visual_mode": category.visual_mode if category else None,
                "allowed_visual_sources": _strings(media.get("allowed_visual_sources")) or ["DIAGRAM", "CARD", "SCREENSHOT", "EXISTING_ASSET"],
                "forbidden_visual_bait": _strings(media.get("forbidden_visual_bait")) or _strings(editorial.get("forbidden_angles")),
                "character_visual_rules": {
                    "visual_identity": _dict(version.visual_identity_json) if version else {},
                    "branch": _dict(branch.visual_branch_json) if branch else {},
                    "provider_constraints": _dict(branch.provider_constraints_json) if branch else {},
                }
                if version or branch
                else None,
                "source_contract_paths": _source_paths("media_policy", "category.default_visual_style_json"),
            },
            "voice_audio_context_json": {
                "voice_profile_id": _uuid_string(voice_profile.id) if voice_profile else None,
                "language": voice_profile.language if voice_profile else content_language,
                "accent": voice_profile.accent if voice_profile else None,
                "tone": _dict(voice_profile.tone_json) if voice_profile else {"contract_tone": voice_style.get("narration_tone")},
                "pace": _dict(voice_profile.pace_json) if voice_profile else {"contract_pacing": voice_style.get("pacing")},
                "pronunciation_dictionary_ref": voice_profile.pronunciation_dictionary_ref if voice_profile else None,
                "consent_status": voice_profile.consent_status if voice_profile else None,
                "commercial_use_status": voice_profile.commercial_use_status if voice_profile else None,
                "provider_policy": _dict(voice_profile.provider_policy_json) if voice_profile else {"voice_provider": media.get("voice_provider")},
                "source_contract_paths": _source_paths("voice_style", "media_policy.voice_provider"),
            },
            "thumbnail_style_context_json": {
                "thumbnail_style": _dict(category.default_thumbnail_style_json) if category else {},
                "text_overlay_language": content_language,
                "mobile_readability_rules": _dict(media.get("thumbnail_mobile_readability_rules")),
                "character_thumbnail_rules": _dict(version.visual_identity_json).get("thumbnail_rules") if version else None,
                "forbidden_thumbnail_patterns": _strings(media.get("forbidden_thumbnail_patterns")) or _strings(editorial.get("forbidden_angles")),
                "source_contract_paths": _source_paths("category.default_thumbnail_style_json", "media_policy"),
            },
            "metadata_seo_policy_context_json": {
                "title_style": _dict(platform.get("title_style")),
                "description_style": _dict(platform.get("description_style")),
                "metadata_languages": _strings(channel.target_metadata_languages if channel else []),
                "subtitle_languages": _strings(channel.target_subtitle_languages if channel else []),
                "hashtag_policy": _dict(platform.get("hashtag_policy")),
                "disclosure_placement_policy": rights.get("disclosure_placement_policy"),
                "source_contract_paths": _source_paths("platform_strategy", "rights_policy"),
            },
            "publish_timing_context_json": {
                "channel_timezone": channel_timezone,
                "audience_timezone": market.get("audience_timezone") or channel_timezone,
                "configured_publish_window": platform.get("configured_publish_window"),
                "suggested_publish_window_policy": platform.get("suggested_publish_window_policy"),
                "manual_publish_only": True,
                "source_contract_paths": _source_paths("platform_strategy.publish_mode", "market_locale.timezone"),
            },
            "source_rights_disclosure_context_json": {
                "rights_policy": rights,
                "source_policy": _dict(channel_contract.get("source_policy")),
                "ai_disclosure_policy": rights.get("ai_disclosure_required_when_ai_media_used"),
                "affiliate_disclosure_policy": rights.get("affiliate_disclosure_policy"),
                "required_disclosure_blocks": _strings(rights.get("required_disclosure_blocks")),
                "source_contract_paths": _source_paths("rights_policy"),
            },
            "monetization_cta_context_json": {
                "monetization_mode": monetization.get("primary") or monetization.get("mode"),
                "allowed_cta_types": _strings(monetization.get("allowed_cta_types")),
                "forbidden_cta_types": _strings(monetization.get("forbidden_cta_types")),
                "affiliate_allowed": monetization.get("affiliate_allowed"),
                "unsupported_asset_offer_forbidden": True,
                "source_contract_paths": _source_paths("compiled_policy_snapshot_json.monetization_policy"),
            },
            "cost_provider_policy_context_json": {
                "provider_boundary": {
                    "no_provider_calls_in_context_compile": True,
                    "llm_agents_must_use_llm_router": True,
                },
                "provider_allowlist": _strings(budget.get("provider_allowlist")),
                "paid_provider_allowed": budget.get("paid_provider_allowed"),
                "premium_provider_allowed": budget.get("premium_provider_allowed"),
                "budget_tier": budget.get("budget_tier") or budget.get("cost_sensitivity"),
                "source_contract_paths": _source_paths("budget_policy", "media_policy"),
            },
            "safety_forbidden_claims_context_json": {
                "forbidden_topics": _strings(editorial.get("forbidden_topics")),
                "forbidden_claims": _strings(editorial.get("forbidden_claims")),
                "evidence_required_claim_types": _strings(editorial.get("evidence_required_claim_types")) or _strings(editorial.get("claim_style")),
                "high_risk_claim_policy": learning.get("min_evidence_required"),
                "source_contract_paths": _source_paths("editorial_strategy", "learning_policy.min_evidence_required"),
            },
        }

    def _source_refs(
        self,
        *,
        project: VideoProject,
        authority: _Authority,
        category: ContentCategory | None,
        character_refs: _CharacterRefs,
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = [
            {"type": "video_project", "id": str(project.id), "policy_snapshot_id": str(project.policy_snapshot_id)},
        ]
        if authority.profile_version is not None:
            refs.append({"type": "channel_profile_version", "id": str(authority.profile_version.id)})
        if authority.policy_snapshot is not None:
            refs.append(
                {
                    "type": "compiled_channel_policy_snapshot",
                    "id": str(authority.policy_snapshot.id),
                    "content_hash": authority.policy_snapshot.content_hash,
                }
            )
        if authority.channel_contract_hash is not None:
            refs.append({"type": "channel_contract", "content_hash": authority.channel_contract_hash})
        if authority.field_source_map_hash is not None:
            refs.append({"type": "field_source_map", "content_hash": authority.field_source_map_hash})
        if category is not None:
            refs.append({"type": "content_category", "id": str(category.id), "content_hash": category.content_hash})
        if character_refs.binding is not None:
            refs.append({"type": "character_binding", "id": str(character_refs.binding.id), "content_hash": character_refs.binding.content_hash})
        for label, record in [
            ("character_profile", character_refs.profile),
            ("character_version", character_refs.version),
            ("character_image_branch", character_refs.branch),
            ("character_reference_asset_pack", character_refs.pack),
            ("voice_profile", character_refs.voice),
        ]:
            if record is not None:
                refs.append({"type": label, "id": str(record.id), "content_hash": getattr(record, "content_hash", None)})
        return refs


def build_effective_channel_runtime_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    return {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "compile_status": snapshot.compile_status,
        "reason_codes": snapshot.reason_codes_json,
        "channel_contract_hash": snapshot.channel_contract_hash,
        "compiled_policy_snapshot_id": _uuid_string(snapshot.compiled_policy_snapshot_id),
    }


def build_script_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    return {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "market_locale": snapshot.market_locale_context_json,
        "audience": snapshot.audience_context_json,
        "brand_voice_persona": snapshot.brand_voice_persona_context_json,
        "category": snapshot.category_runtime_context_json,
        "safety_forbidden_claims": snapshot.safety_forbidden_claims_context_json,
    }


def build_visual_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    return {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "visual_style": snapshot.visual_style_context_json,
        "character_identity": snapshot.character_identity_context_json,
        "source_rights_disclosure": snapshot.source_rights_disclosure_context_json,
    }


def build_thumbnail_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    return {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "thumbnail_style": snapshot.thumbnail_style_context_json,
        "character_identity": snapshot.character_identity_context_json,
    }


def build_metadata_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    return {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "metadata_seo_policy": snapshot.metadata_seo_policy_context_json,
        "source_rights_disclosure": snapshot.source_rights_disclosure_context_json,
        "monetization_cta": snapshot.monetization_cta_context_json,
    }


def build_publish_handoff_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    return {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "publish_timing": snapshot.publish_timing_context_json,
        "manual_publish_only": True,
        "source_rights_disclosure": snapshot.source_rights_disclosure_context_json,
    }
