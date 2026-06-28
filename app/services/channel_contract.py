from __future__ import annotations

from typing import Any

from app.core.errors import ValidationFailureError


CONTRACT_COMPLETE = "COMPLETE"
CONTRACT_PARTIAL = "PARTIAL"
CONTRACT_MISSING = "MISSING"
CONTRACT_STALE = "STALE"
CONTRACT_CONTRADICTORY = "CONTRADICTORY"

LEGACY_PROVIDER_BUDGET_KEYS = {
    "tts_character_budget",
    "voice_budget",
    "channel_tts_budget",
    "ai_hero_budget_usd",
    "ai_hero_monthly_cap",
    "channel_ai_hero_budget",
}

FORBIDDEN_BEHAVIOR_CODES = [
    "fake_traffic",
    "bot_engagement",
    "spam_reupload",
    "algorithm_manipulation",
    "platform_evasion",
    "ip_vps_tricks",
    "youtube_studio_scraping",
    "dashboard_scraping",
    "invented_metrics",
    "invented_sources",
    "invented_rights",
    "unsupported_local_claims",
]

VALID_PRIMARY_MARKETS = {"US", "UK", "EU", "JP", "KR", "VN", "AU", "CA", "OTHER"}
VALID_AUDIENCE_LOCALES = {"en-US", "en-GB", "ja-JP", "ko-KR", "vi-VN", "other"}


def reject_legacy_provider_budget_fields(payload: Any) -> None:
    matches = sorted(_find_keys(payload, LEGACY_PROVIDER_BUDGET_KEYS))
    if matches:
        raise ValidationFailureError(f"provider budget fields are not channel init inputs: {matches}")


def build_channel_contract(*, profile_input: dict[str, Any], channel: Any | None = None) -> dict[str, Any]:
    policies = profile_input.get("policies") if isinstance(profile_input.get("policies"), dict) else {}
    metadata = (getattr(channel, "metadata_", None) or {}) if channel is not None else {}
    explicit = _first_dict(
        policies.get("channel_contract"),
        policies.get("channel_contract_json"),
        metadata.get("channel_contract"),
        metadata.get("m12_2p_channel_contract"),
    )
    explicit_mode = explicit is not None
    source = explicit or {}

    channel_identity_source = _dict(source.get("channel_identity"))
    target_audience_source = _dict(source.get("target_audience"))
    market_source = _dict(source.get("market_locale"))
    editorial_source = _dict(source.get("editorial_strategy"))
    format_source = _dict(source.get("format_policy"))
    voice_source = _dict(source.get("voice_style"))
    platform_source = _dict(source.get("platform_strategy"))
    media_source = _dict(source.get("media_policy"))
    rights_source = _dict(source.get("rights_policy"))
    budget_source = _dict(source.get("budget_policy"))
    learning_source = _dict(source.get("learning_policy"))

    legacy_market = _legacy_market(profile_input.get("target_market")) if not explicit_mode else None
    legacy_locale = _legacy_locale(profile_input=profile_input, channel=channel) if not explicit_mode else None
    explicit_primary_market = _clean(market_source.get("primary_market"))
    secondary_markets = _string_list(market_source.get("secondary_markets"))
    if not secondary_markets and not explicit_mode and channel is not None:
        secondary_markets = _string_list(getattr(channel, "target_regions", []))

    contract = {
        "channel_identity": {
            "company_id": channel_identity_source.get("company_id"),
            "channel_key": channel_identity_source.get("channel_key") or (getattr(channel, "key", None) if channel is not None else None),
            "channel_name": channel_identity_source.get("channel_name") or profile_input.get("display_name") or (getattr(channel, "name", None) if channel is not None else None),
            "template_key": channel_identity_source.get("template_key") or profile_input.get("template_key"),
            "channel_type": channel_identity_source.get("channel_type") or "YOUTUBE_CHANNEL",
            "niche": channel_identity_source.get("niche") or profile_input.get("template_key"),
            "positioning": channel_identity_source.get("positioning") or profile_input.get("audience_segment"),
            "brand_promise": channel_identity_source.get("brand_promise"),
            "primary_platform": channel_identity_source.get("primary_platform") or "YouTube",
            "secondary_platforms": _string_list(channel_identity_source.get("secondary_platforms")),
            "series_plan": channel_identity_source.get("series_plan") or profile_input.get("series_plan", []),
        },
        "target_audience": {
            "primary_persona": target_audience_source.get("primary_persona") or (profile_input.get("audience_segment") if not explicit_mode else None),
            "audience_level": target_audience_source.get("audience_level"),
            "pain_points": _string_list(target_audience_source.get("pain_points")),
            "desired_outcome": target_audience_source.get("desired_outcome"),
            "audience_notes": target_audience_source.get("audience_notes"),
        },
        "market_locale": {
            "primary_market": explicit_primary_market or legacy_market,
            "secondary_markets": secondary_markets,
            "audience_locale": market_source.get("audience_locale") or legacy_locale,
            "content_language": market_source.get("content_language") or (_clean(profile_input.get("target_language")) if not explicit_mode else None) or (getattr(channel, "primary_language", None) if channel is not None and not explicit_mode else None),
            "operator_language": market_source.get("operator_language") or ("vi" if not explicit_mode else None),
            "timezone": market_source.get("timezone") or ((getattr(channel, "primary_timezone", None) or getattr(channel, "default_timezone", None)) if channel is not None and not explicit_mode else None),
            "currency": market_source.get("currency"),
            "measurement_units": market_source.get("measurement_units"),
            "date_format": market_source.get("date_format"),
            "cultural_style": _dict(market_source.get("cultural_style")),
            "market_examples_preference": market_source.get("market_examples_preference"),
            "regulatory_sensitivity": _dict(market_source.get("regulatory_sensitivity")),
        },
        "editorial_strategy": {
            "content_pillars": _string_list(editorial_source.get("content_pillars") or profile_input.get("content_pillars", [])),
            "allowed_angles": _string_list(editorial_source.get("allowed_angles")),
            "forbidden_angles": _string_list(editorial_source.get("forbidden_angles")),
            "claim_style": _string_list(editorial_source.get("claim_style") or ["measured", "evidence_backed"]),
            "allowed_topics": _string_list(editorial_source.get("allowed_topics")),
            "forbidden_topics": _string_list(editorial_source.get("forbidden_topics")),
        },
        "format_policy": _format_policy(format_source or profile_input.get("format_strategy") or {}),
        "voice_style": {
            "narration_tone": voice_source.get("narration_tone") or _legacy_voice_tone(profile_input.get("voice_style")) if not explicit_mode else voice_source.get("narration_tone"),
            "pacing": voice_source.get("pacing") or _dict(profile_input.get("voice_style")).get("pacing"),
            "allowed_style": _string_list(voice_source.get("allowed_style")),
            "forbidden_style": _string_list(voice_source.get("forbidden_style") or ["hype", "fearmongering", "aggressive_sales", "fake_urgency"]),
        },
        "platform_strategy": {
            "primary_platform": platform_source.get("primary_platform") or "YouTube",
            "youtube_is_learning_authority": platform_source.get("youtube_is_learning_authority", True),
            "secondary_platforms": _string_list(platform_source.get("secondary_platforms")),
            "disabled_authorities": _string_list(platform_source.get("disabled_authorities") or ["tiktok_analytics_learning", "facebook_analytics_learning"]),
            "publish_mode": platform_source.get("publish_mode", "human_handoff_only"),
            "auto_publish_allowed": platform_source.get("auto_publish_allowed", False),
            "studio_scraping_allowed": platform_source.get("studio_scraping_allowed", False),
        },
        "media_policy": {
            "voice_provider": media_source.get("voice_provider") or "ElevenLabs",
            "ai_hero_provider": media_source.get("ai_hero_provider") or "Google Vertex Veo",
            "ai_hero_model_id": media_source.get("ai_hero_model_id") or "veo-3.1-fast-generate-001",
            "ai_hero_allowed_durations_seconds": _int_list(media_source.get("ai_hero_allowed_durations_seconds") or [4, 6, 8]),
            "ai_hero_default_duration_seconds": media_source.get("ai_hero_default_duration_seconds", 8),
            "ai_hero_audio": media_source.get("ai_hero_audio", False),
            "ai_hero_allowed_use": _string_list(media_source.get("ai_hero_allowed_use") or ["hero_shot", "hard_to_find_visual"]),
            "ai_hero_forbidden_use": _string_list(media_source.get("ai_hero_forbidden_use") or ["data_diagram", "workflow_chart", "factual_evidence_visualization"]),
            "renderer": media_source.get("renderer") or "Creatomate Growth 10K",
            "storage_archive": media_source.get("storage_archive") or "Google Drive",
            "drive_offload_enabled": media_source.get("drive_offload_enabled", True),
        },
        "rights_policy": {
            "source_manifest_required": rights_source.get("source_manifest_required", True),
            "rights_evidence_required": rights_source.get("rights_evidence_required", True),
            "ai_disclosure_required_when_ai_media_used": rights_source.get("ai_disclosure_required_when_ai_media_used", True),
            "synthetic_media_warning_when_applicable": rights_source.get("synthetic_media_warning_when_applicable", True),
            "music_policy": rights_source.get("music_policy") or "approved_licensed_audio_library_safe_only",
            "reused_content_sensitivity": rights_source.get("reused_content_sensitivity") or "medium",
        },
        "budget_policy": {
            "cost_sensitivity": budget_source.get("cost_sensitivity") or "medium",
            "avoid_unnecessary_ai_hero": budget_source.get("avoid_unnecessary_ai_hero", True),
            "prefer_reuse_safe_assets": budget_source.get("prefer_reuse_safe_assets", True),
            "exact_cost_claim_requires_provider_snapshot": budget_source.get("exact_cost_claim_requires_provider_snapshot", True),
        },
        "learning_policy": {
            "authority": learning_source.get("authority") or "youtube_analytics_only",
            "min_evidence_required": learning_source.get("min_evidence_required") or _dict(profile_input.get("evidence_requirement")),
            "auto_promote_learning": learning_source.get("auto_promote_learning", False),
            "config_mutation_by_agent_allowed": learning_source.get("config_mutation_by_agent_allowed", False),
            "weak_evidence_action": learning_source.get("weak_evidence_action") or "summarize_limitations_only",
        },
        "forbidden_behavior": sorted(set([*_string_list(source.get("forbidden_behavior")), *FORBIDDEN_BEHAVIOR_CODES])),
    }
    missing_fields = _missing_fields(contract)
    contradiction_reasons = _contradiction_reasons(contract)
    market_status = "KNOWN" if not any(field.startswith("market_locale.") for field in missing_fields) else "PARTIAL"
    contract["market_locale"]["market_locale_context_status"] = market_status
    if contradiction_reasons:
        status = CONTRACT_CONTRADICTORY
    elif missing_fields:
        status = CONTRACT_PARTIAL
    else:
        status = CONTRACT_COMPLETE
    contract["contract_status"] = status
    contract["missing_fields"] = missing_fields
    contract["contradiction_reasons"] = contradiction_reasons
    contract["next_action"] = _next_action(status)
    return contract


def contract_status_from_snapshot_payload(payload: dict[str, Any] | None) -> tuple[str, list[str], list[str]]:
    if not isinstance(payload, dict):
        return CONTRACT_MISSING, ["channel_contract_json"], []
    contract = payload.get("channel_contract_json") if isinstance(payload.get("channel_contract_json"), dict) else {}
    status = str(contract.get("contract_status") or payload.get("contract_status") or CONTRACT_MISSING)
    missing = _string_list(contract.get("missing_fields") or payload.get("missing_fields"))
    contradictions = _string_list(contract.get("contradiction_reasons") or payload.get("contradiction_reasons"))
    return status, missing, contradictions


def ensure_snapshot_contract_activatable(payload: dict[str, Any] | None) -> None:
    status, missing, contradictions = contract_status_from_snapshot_payload(payload)
    if status != CONTRACT_COMPLETE:
        details = {"contract_status": status, "missing_fields": missing, "contradiction_reasons": contradictions}
        raise ValidationFailureError(f"channel contract is not COMPLETE; activation blocked: {details}")


def _format_policy(source: dict[str, Any]) -> dict[str, Any]:
    long_form = source.get("long_form") if isinstance(source.get("long_form"), dict) else {}
    shorts = source.get("shorts") if isinstance(source.get("shorts"), dict) else {}
    long_minutes = source.get("long_form_minutes")
    if long_minutes and not long_form:
        long_form = {"enabled": True, "target_duration_minutes": {"min": 6, "max": 12}, "structure": ["hook", "problem", "mechanism", "result", "takeaway"]}
    return {
        "long_form": {
            "enabled": long_form.get("enabled", True),
            "target_duration_minutes": _dict(long_form.get("target_duration_minutes")) or {"min": long_form.get("min_minutes"), "max": long_form.get("max_minutes")},
            "structure": _string_list(long_form.get("structure") or ["hook", "problem", "mechanism", "result", "takeaway"]),
            "chapters_required": long_form.get("chapters_required", True),
        },
        "shorts": {
            "enabled": shorts.get("enabled", True),
            "target_duration_seconds": _dict(shorts.get("target_duration_seconds")) or {"min": shorts.get("min_seconds"), "max": shorts.get("max_seconds")},
            "hard_max_seconds": shorts.get("hard_max_seconds", 59),
            "captions_required": shorts.get("captions_required", True),
            "shorts_per_long_form": shorts.get("shorts_per_long_form", source.get("shorts_per_long_form", 0)),
        },
    }


def _missing_fields(contract: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    checks = {
        "channel_identity.channel_name": contract["channel_identity"].get("channel_name"),
        "channel_identity.niche": contract["channel_identity"].get("niche"),
        "target_audience.primary_persona": contract["target_audience"].get("primary_persona"),
        "market_locale.primary_market": contract["market_locale"].get("primary_market"),
        "market_locale.audience_locale": contract["market_locale"].get("audience_locale"),
        "market_locale.content_language": contract["market_locale"].get("content_language"),
        "market_locale.operator_language": contract["market_locale"].get("operator_language"),
        "market_locale.timezone": contract["market_locale"].get("timezone"),
        "editorial_strategy.content_pillars": contract["editorial_strategy"].get("content_pillars"),
        "voice_style.narration_tone": contract["voice_style"].get("narration_tone"),
        "platform_strategy.primary_platform": contract["platform_strategy"].get("primary_platform"),
        "media_policy.voice_provider": contract["media_policy"].get("voice_provider"),
        "media_policy.renderer": contract["media_policy"].get("renderer"),
        "learning_policy.authority": contract["learning_policy"].get("authority"),
        "forbidden_behavior": contract.get("forbidden_behavior"),
    }
    for path, value in checks.items():
        if value is None or value == "" or value == []:
            missing.append(path)
    primary_market = contract["market_locale"].get("primary_market")
    if primary_market and primary_market not in VALID_PRIMARY_MARKETS:
        missing.append("market_locale.primary_market")
    audience_locale = contract["market_locale"].get("audience_locale")
    if audience_locale and audience_locale not in VALID_AUDIENCE_LOCALES:
        missing.append("market_locale.audience_locale")
    if contract["platform_strategy"].get("publish_mode") != "human_handoff_only":
        missing.append("platform_strategy.publish_mode")
    if contract["rights_policy"].get("source_manifest_required") is not True:
        missing.append("rights_policy.source_manifest_required")
    fmt = contract["format_policy"]
    if not (_dict(fmt.get("long_form")).get("enabled") or _dict(fmt.get("shorts")).get("enabled")):
        missing.append("format_policy.long_form_or_shorts")
    return sorted(set(missing))


def _contradiction_reasons(contract: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    platform = contract["platform_strategy"]
    learning = contract["learning_policy"]
    media = contract["media_policy"]
    if platform.get("auto_publish_allowed") is True:
        reasons.append("platform_strategy.auto_publish_allowed must be false")
    if platform.get("studio_scraping_allowed") is True:
        reasons.append("platform_strategy.studio_scraping_allowed must be false")
    if platform.get("publish_mode") != "human_handoff_only":
        reasons.append("platform_strategy.publish_mode must be human_handoff_only")
    if learning.get("auto_promote_learning") is True:
        reasons.append("learning_policy.auto_promote_learning must be false")
    if learning.get("config_mutation_by_agent_allowed") is True:
        reasons.append("learning_policy.config_mutation_by_agent_allowed must be false")
    if media.get("ai_hero_audio") is True:
        reasons.append("media_policy.ai_hero_audio must be false")
    overlap = set(_string_list(media.get("ai_hero_allowed_use"))) & set(_string_list(media.get("ai_hero_forbidden_use")))
    if overlap:
        reasons.append(f"media_policy.ai_hero_allowed_use conflicts with forbidden use: {sorted(overlap)}")
    missing_forbidden = sorted(set(FORBIDDEN_BEHAVIOR_CODES) - set(_string_list(contract.get("forbidden_behavior"))))
    if missing_forbidden:
        reasons.append(f"forbidden_behavior missing locked rules: {missing_forbidden}")
    return reasons


def _legacy_market(value: Any) -> str | None:
    text = str(value or "").upper()
    for market in ["US", "UK", "EU", "JP", "KR", "VN", "AU", "CA"]:
        if market in text:
            return market
    return None


def _legacy_locale(*, profile_input: dict[str, Any], channel: Any | None) -> str | None:
    language = str((getattr(channel, "primary_language", None) if channel is not None else None) or "").lower()
    region = str((getattr(channel, "primary_region", None) if channel is not None else None) or _legacy_market(profile_input.get("target_market")) or "").upper()
    if language.startswith("vi"):
        return "vi-VN"
    if language.startswith("ja"):
        return "ja-JP"
    if language.startswith("ko"):
        return "ko-KR"
    if language.startswith("en") and region == "UK":
        return "en-GB"
    if language.startswith("en"):
        return "en-US"
    return None


def _legacy_voice_tone(value: Any) -> str | None:
    tone = str(_dict(value).get("tone") or "").lower()
    if "documentary" in tone:
        return "documentary_explainer"
    if "professional" in tone:
        return "calm_professional"
    return "practical_explainer" if tone else None


def _next_action(status: str) -> str:
    if status == CONTRACT_COMPLETE:
        return "Hồ sơ đủ để kích hoạt."
    if status == CONTRACT_CONTRADICTORY:
        return "Sửa cấu hình mâu thuẫn trước khi kích hoạt."
    return "Bổ sung Channel Contract và compile lại policy snapshot."


def _find_keys(payload: Any, keys: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys:
                found.add(key)
            found.update(_find_keys(value, keys))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_find_keys(item, keys))
    return found


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.splitlines() if part.strip()]


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
