from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel

from app.contracts.nich1 import (
    NICHE_CONTRACT_DIGEST_VERSION,
    NICHE_GATE_STRICT_ORDER,
    ChannelFitEvaluation,
    ContentCategoryBinding,
    EditorialSlotBinding,
    EditorialSlotValidationResult,
    MetadataNicheAlignmentInput,
    NicheAlignmentDossier,
    NicheContractDigest,
    NicheCriterion,
    NicheDossierScope,
    NicheEvidenceRef,
    NicheGateCheck,
    NicheGateKey,
    NicheGateResult,
    NicheGateVerdict,
    NicheReasonCode,
    ScriptNicheAlignmentInput,
    ThumbnailNicheAlignmentInput,
    TopicNicheAlignmentInput,
    VisualNicheAlignmentInput,
    nich1_stable_hash,
)


_HashModel = TypeVar("_HashModel", bound=BaseModel)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "with",
    "your",
}
_NATIVE_MECHANISM_ROUTES = {
    "NATIVE_DIAGRAM",
    "NATIVE_MOTION_GRAPHIC",
    "EDITORIAL_TEXT_GRAPHIC",
    "AUTHORIZED_UI_OR_PRODUCT_ASSET",
    "HUMAN_SUPPLIED_ASSET",
}
_STOCK_ROUTES = {"PEXELS_VIDEO", "PEXELS_PHOTO"}
_AI_IMAGE_ROUTES = {
    "AI_GENERATED_IMAGE",
    "AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY",
}
_AUTHORIZED_EVIDENCE_ROUTES = {
    "AUTHORIZED_UI_OR_PRODUCT_ASSET",
    "HUMAN_SUPPLIED_ASSET",
    "ARCHIVED_ASSET_REUSE",
}


class NicheContractCompilationError(ValueError):
    def __init__(
        self, reason_codes: Sequence[NicheReasonCode], details: str | None = None
    ):
        self.reason_codes = _ordered_unique(reason_codes)
        message = ",".join(code.value for code in self.reason_codes)
        if details:
            message = f"{message}:{details}"
        super().__init__(message)


class NichePolicyThresholdError(ValueError):
    reason_code = NicheReasonCode.CHANNEL_FIT_POLICY_THRESHOLD_MISSING


def _seal(model: type[_HashModel], payload: dict[str, Any]) -> _HashModel:
    draft = model.model_construct(**payload, content_hash="0" * 64)
    normalized = draft.model_dump(mode="json", exclude={"content_hash"})
    return model.model_validate(
        {**normalized, "content_hash": nich1_stable_hash(normalized)}
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _strings(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    return _ordered_unique_strings(item for item in raw if _clean(item))


def _ordered_unique(values: Sequence[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        marker = value.value if isinstance(value, Enum) else value
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _ordered_unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(value)
        if not text:
            continue
        marker = text.casefold()
        if marker not in seen:
            seen.add(marker)
            result.append(text)
    return result


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalized(value).split()
        if len(token) > 1 and token not in _STOPWORDS
    }


def _same(left: Any, right: Any) -> bool:
    return bool(_normalized(left)) and _normalized(left) == _normalized(right)


def _labels_overlap(left: Any, right: Any) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    return bool(left_tokens and right_tokens and left_tokens & right_tokens)


def _any_label_match(
    candidate_values: Sequence[str], authority_values: Sequence[str]
) -> bool:
    return any(
        _same(candidate, authority) or _labels_overlap(candidate, authority)
        for candidate in candidate_values
        for authority in authority_values
    )


def _matched_forbidden_topics(text: str, forbidden_topics: Sequence[str]) -> list[str]:
    normalized_text = f" {_normalized(text)} "
    matches: list[str] = []
    for topic in forbidden_topics:
        normalized_topic = _normalized(topic)
        if normalized_topic and f" {normalized_topic} " in normalized_text:
            matches.append(topic)
    return _ordered_unique_strings(matches)


def _summary(value: Any, *, limit: int = 2000) -> str:
    if isinstance(value, str):
        rendered = _clean(value) or "UNKNOWN"
    else:
        rendered = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    return rendered[:limit]


def _ref(prefix: str, value: Any, fragment: str | None = None) -> str:
    suffix = f"#{fragment}" if fragment else ""
    return f"{prefix}://{value}{suffix}"


def _profile_input(profile_version: Any) -> dict[str, Any]:
    return _as_dict(_get(profile_version, "profile_input", {}))


def _compiled_payload(policy_snapshot: Any) -> dict[str, Any]:
    source = _get(policy_snapshot, "compiled_payload", policy_snapshot)
    return _as_dict(source)


def _category_topics(category: Any, kind: str) -> list[str]:
    candidates = (
        _get(category, f"{kind}_topics_json"),
        _get(category, f"{kind}_topics"),
        _as_dict(_get(category, "editorial_policy_json", {})).get(f"{kind}_topics"),
    )
    for candidate in candidates:
        if candidate not in (None, "", []):
            return _strings(candidate)
    return []


def _series_plan(
    channel_contract: Mapping[str, Any], profile_version: Any
) -> list[dict[str, Any]]:
    identity = _as_dict(channel_contract.get("channel_identity"))
    raw = (
        identity.get("series_plan")
        or _profile_input(profile_version).get("series_plan")
        or []
    )
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _series_key(item: Mapping[str, Any]) -> str | None:
    return _clean(item.get("key") or item.get("series_key") or item.get("id"))


def _category_hash(category: Any) -> str:
    stored = _clean(_get(category, "content_hash"))
    if stored and re.fullmatch(r"[0-9a-f]{64}", stored):
        return stored
    payload = {
        "id": str(_get(category, "id")),
        "company_id": str(_get(category, "company_id")),
        "channel_workspace_id": str(_get(category, "channel_workspace_id")),
        "category_key": _get(category, "category_key"),
        "name": _get(category, "name"),
        "sub_niche": _get(category, "sub_niche"),
        "audience_segment": _get(category, "audience_segment"),
        "content_pillar": _get(category, "content_pillar"),
        "allowed_topics": _category_topics(category, "allowed"),
        "forbidden_topics": _category_topics(category, "forbidden"),
        "status": _get(category, "status"),
    }
    return nich1_stable_hash(payload)


class EditorialSlotValidator:
    """Validate strict production slot bindings without mutating legacy rows."""

    def validate(
        self,
        *,
        channel: Any,
        profile_version: Any,
        policy_snapshot: Any,
        channel_contract: Mapping[str, Any],
        category: Any | None,
        editorial_slot: Any,
        strict_production: bool = True,
        allow_historical_approved: bool = False,
    ) -> EditorialSlotValidationResult:
        reasons: list[NicheReasonCode] = []
        checks: dict[str, bool] = {}

        channel_id = _uuid(_get(channel, "id"))
        company_id = _uuid(_get(channel, "company_id"))
        profile_id = _uuid(_get(profile_version, "id"))
        snapshot_id = _uuid(_get(policy_snapshot, "id"))
        slot_id = _uuid(_get(editorial_slot, "id"))
        category_id = _uuid(_get(category, "id")) if category is not None else None
        slot_category_id = _uuid(_get(editorial_slot, "category_id"))
        slot_channel_id = _uuid(_get(editorial_slot, "channel_workspace_id"))
        slot_company_id = _uuid(_get(editorial_slot, "company_id"))
        slot_snapshot_id = _uuid(_get(editorial_slot, "policy_snapshot_id"))

        checks["slot_scope"] = bool(
            channel_id
            and company_id
            and slot_channel_id == channel_id
            and slot_company_id == company_id
            and slot_snapshot_id == snapshot_id
        )
        if not checks["slot_scope"]:
            reasons.append(NicheReasonCode.SLOT_SCOPE_MISMATCH)

        snapshot_status = str(_get(policy_snapshot, "status", "")).lower()
        checks["active_policy_binding"] = bool(
            snapshot_id
            and _uuid(_get(policy_snapshot, "channel_workspace_id")) == channel_id
            and _uuid(_get(policy_snapshot, "channel_profile_version_id")) == profile_id
            and (
                snapshot_status in {"active", "approved"}
                if allow_historical_approved
                else snapshot_status == "active"
                and _uuid(_get(channel, "active_policy_snapshot_id")) == snapshot_id
            )
        )
        if not checks["active_policy_binding"]:
            reasons.append(NicheReasonCode.POLICY_SCOPE_MISMATCH)

        checks["category_binding"] = bool(
            category is not None
            and category_id
            and slot_category_id == category_id
            and _uuid(_get(category, "company_id")) == company_id
            and _uuid(_get(category, "channel_workspace_id")) == channel_id
        )
        if not checks["category_binding"]:
            reasons.append(
                NicheReasonCode.CATEGORY_BINDING_MISSING
                if category is None or slot_category_id is None
                else NicheReasonCode.CATEGORY_SCOPE_MISMATCH
            )

        checks["category_active"] = bool(
            category is not None
            and str(_get(category, "status", "")).upper() == "ACTIVE"
        )
        if category is not None and not checks["category_active"]:
            reasons.append(NicheReasonCode.CATEGORY_NOT_ACTIVE)

        sub_niche = (
            _clean(_get(category, "sub_niche")) if category is not None else None
        )
        checks["category_sub_niche"] = bool(sub_niche)
        if category is not None and not sub_niche:
            reasons.append(NicheReasonCode.CATEGORY_SUB_NICHE_MISSING)

        pillar_id = _clean(
            _get(editorial_slot, "content_pillar_id")
            or _as_dict(_get(editorial_slot, "operational_envelope", {})).get(
                "content_pillar_id"
            )
        )
        pillar_key = _clean(
            _get(editorial_slot, "content_pillar_key")
            or _get(editorial_slot, "content_pillar")
        )
        checks["pillar_present"] = bool(pillar_id or pillar_key)
        if not checks["pillar_present"]:
            reasons.append(NicheReasonCode.CONTENT_PILLAR_BINDING_MISSING)

        pillars = _strings(
            _as_dict(channel_contract.get("editorial_strategy")).get("content_pillars")
        )
        checks["pillar_in_contract"] = bool(
            pillar_key and any(_same(pillar_key, item) for item in pillars)
        )
        if pillar_key and not checks["pillar_in_contract"]:
            reasons.append(NicheReasonCode.CONTENT_PILLAR_NOT_IN_CHANNEL_CONTRACT)

        category_pillar = (
            _clean(_get(category, "content_pillar")) if category is not None else None
        )
        checks["category_pillar_combination"] = bool(
            pillar_key and (not category_pillar or _same(category_pillar, pillar_key))
        )
        if pillar_key and category_pillar and not checks["category_pillar_combination"]:
            reasons.append(NicheReasonCode.CATEGORY_PILLAR_MISMATCH)

        series_key = _clean(_get(editorial_slot, "series_key"))
        assignment_mode = str(
            _get(editorial_slot, "assignment_mode") or ""
        ).upper()
        requires_series = assignment_mode == "SERIES_REQUIRED"
        checks["series_present"] = bool(series_key) or not requires_series
        if requires_series and not series_key:
            reasons.append(NicheReasonCode.SERIES_BINDING_MISSING)
        series_plan = _series_plan(channel_contract, profile_version)
        series_item = next(
            (
                item
                for item in series_plan
                if series_key and _same(_series_key(item), series_key)
            ),
            None,
        )
        checks["series_allowed"] = bool(
            (not series_key and not requires_series)
            or (series_key and (not series_plan or series_item is not None))
        )
        if series_key and series_plan and series_item is None:
            reasons.append(NicheReasonCode.SERIES_NOT_ALLOWED)
        if series_item is not None:
            series_pillar = _clean(
                series_item.get("content_pillar_key")
                or series_item.get("pillar_key")
                or series_item.get("content_pillar")
                or series_item.get("pillar")
            )
            if series_pillar and pillar_key and not _same(series_pillar, pillar_key):
                reasons.append(NicheReasonCode.SERIES_PILLAR_MISMATCH)
                checks["series_allowed"] = False
            series_category = _clean(
                series_item.get("category_id")
                or series_item.get("content_category_id")
                or series_item.get("category_key")
            )
            category_markers = {
                str(category_id) if category_id else "",
                _clean(_get(category, "category_key")) or "",
            }
            if series_category and not any(
                _same(series_category, marker) for marker in category_markers
            ):
                reasons.append(NicheReasonCode.SERIES_CATEGORY_MISMATCH)
                checks["series_allowed"] = False

        production_goal = _clean(_get(editorial_slot, "production_goal"))
        checks["production_goal_present"] = bool(production_goal)
        if not production_goal:
            reasons.append(NicheReasonCode.PRODUCTION_GOAL_MISSING)
        forbidden = _strings(
            _as_dict(channel_contract.get("editorial_strategy")).get("forbidden_topics")
        ) + (_category_topics(category, "forbidden") if category is not None else [])
        goal_conflicts = _matched_forbidden_topics(production_goal or "", forbidden)
        checks["production_goal_supported"] = bool(
            production_goal and not goal_conflicts
        )
        if goal_conflicts:
            reasons.append(NicheReasonCode.PRODUCTION_GOAL_UNSUPPORTED)

        reasons = _ordered_unique(reasons)
        missing_binding_codes = {
            NicheReasonCode.CATEGORY_BINDING_MISSING,
            NicheReasonCode.CATEGORY_SUB_NICHE_MISSING,
            NicheReasonCode.CONTENT_PILLAR_BINDING_MISSING,
            NicheReasonCode.SERIES_BINDING_MISSING,
            NicheReasonCode.PRODUCTION_GOAL_MISSING,
        }
        if reasons and not strict_production and set(reasons) <= missing_binding_codes:
            verdict = NicheGateVerdict.REVIEW_REQUIRED
            reasons.append(NicheReasonCode.LEGACY_SLOT_STRICT_BINDING_REQUIRED)
        elif reasons:
            verdict = NicheGateVerdict.BLOCK
        else:
            verdict = NicheGateVerdict.PASS

        slot_binding: EditorialSlotBinding | None = None
        category_binding: ContentCategoryBinding | None = None
        if (
            not reasons
            and channel_id
            and company_id
            and profile_id
            and snapshot_id
            and slot_id
            and category_id
            and pillar_key
            and production_goal
            and sub_niche
        ):
            snapshot_hash = str(_get(policy_snapshot, "content_hash"))
            slot_payload = {
                "slot_id": slot_id,
                "slot_ref": _ref("editorial-slot", slot_id),
                "company_id": company_id,
                "channel_id": channel_id,
                "active_profile_version_ref": _ref(
                    "channel-profile-version", profile_id
                ),
                "active_policy_snapshot_ref": _ref(
                    "compiled-policy-snapshot", snapshot_id
                ),
                "active_policy_snapshot_hash": snapshot_hash,
                "category_id": category_id,
                "content_pillar_id": pillar_id,
                "content_pillar_key": pillar_key,
                "series_key": series_key,
                "production_goal": production_goal,
            }
            slot_binding = _seal(EditorialSlotBinding, slot_payload)
            category_payload = {
                "category_id": category_id,
                "category_ref": _ref("content-category", category_id),
                "company_id": company_id,
                "channel_id": channel_id,
                "status": str(_get(category, "status")),
                "category_name": _clean(_get(category, "name")) or str(category_id),
                "sub_niche": sub_niche,
                "content_pillar_key": category_pillar or pillar_key,
                "allowed_topics": _category_topics(category, "allowed"),
                "forbidden_topics": _category_topics(category, "forbidden"),
            }
            category_binding = _seal(ContentCategoryBinding, category_payload)

        result_payload = {
            "verdict": verdict,
            "production_eligible": verdict == NicheGateVerdict.PASS,
            "legacy_readable": True,
            "strict_production": strict_production,
            "reason_codes": _ordered_unique(reasons),
            "checks": checks,
            "slot_binding": slot_binding,
            "category_binding": category_binding,
        }
        return _seal(EditorialSlotValidationResult, result_payload)


class NicheContractDigestCompiler:
    """Compile the bounded digest from frozen authority objects only."""

    def __init__(self, *, slot_validator: EditorialSlotValidator | None = None):
        self.slot_validator = slot_validator or EditorialSlotValidator()

    def compile(
        self,
        *,
        channel: Any,
        profile_version: Any,
        policy_snapshot: Any,
        category: Any,
        editorial_slot: Any,
        allow_historical_approved: bool = False,
    ) -> NicheContractDigest:
        authority_reasons = self._authority_reasons(
            channel=channel,
            profile_version=profile_version,
            policy_snapshot=policy_snapshot,
            allow_historical_approved=allow_historical_approved,
        )
        payload = _compiled_payload(policy_snapshot)
        channel_contract = _as_dict(payload.get("channel_contract_json"))
        contract_status = str(
            channel_contract.get("contract_status")
            or payload.get("contract_status")
            or ""
        ).upper()
        if contract_status != "COMPLETE":
            authority_reasons.append(NicheReasonCode.CHANNEL_CONTRACT_INCOMPLETE)
        if authority_reasons:
            raise NicheContractCompilationError(authority_reasons)

        slot_result = self.slot_validator.validate(
            channel=channel,
            profile_version=profile_version,
            policy_snapshot=policy_snapshot,
            channel_contract=channel_contract,
            category=category,
            editorial_slot=editorial_slot,
            strict_production=True,
            allow_historical_approved=allow_historical_approved,
        )
        if slot_result.verdict != NicheGateVerdict.PASS:
            raise NicheContractCompilationError(slot_result.reason_codes)
        assert slot_result.slot_binding is not None
        assert slot_result.category_binding is not None

        identity = _as_dict(channel_contract.get("channel_identity"))
        audience = _as_dict(channel_contract.get("target_audience"))
        market = _as_dict(channel_contract.get("market_locale"))
        editorial = _as_dict(channel_contract.get("editorial_strategy"))
        voice = _as_dict(channel_contract.get("voice_style"))
        format_policy = _as_dict(channel_contract.get("format_policy"))
        media = _as_dict(channel_contract.get("media_policy"))
        profile_input = _profile_input(profile_version)
        compiled_channel_policy = _as_dict(payload.get("channel_scoped_policy"))
        compiled_visual = _as_dict(
            compiled_channel_policy.get("channel_visual_strategy_profile")
        )
        compiled_visual_binding = _as_dict(
            compiled_channel_policy.get("visual_source_policy_binding")
        )

        desired_outcomes = _strings(
            audience.get("desired_outcomes") or audience.get("desired_outcome")
        )
        audience_segments = _strings(audience.get("audience_segments"))
        primary_audience = _clean(audience.get("primary_persona"))
        category_audience = _clean(_get(category, "audience_segment"))
        audience_segments = _ordered_unique_strings(
            [*audience_segments, primary_audience, category_audience]
        )
        visual_source_profile = _clean(
            compiled_visual_binding.get("niche_visual_source_profile")
            or compiled_visual.get("niche_visual_source_profile")
            or _as_dict(profile_input.get("media_style")).get(
                "niche_visual_source_profile"
            )
            or _as_dict(_get(category, "default_visual_style_json", {})).get(
                "niche_visual_source_profile"
            )
            or media.get("niche_visual_source_profile")
        )
        required = {
            "primary_niche": _clean(identity.get("niche")),
            "sub_niche": _clean(
                _get(category, "sub_niche") or identity.get("sub_niche")
            ),
            "positioning": _clean(identity.get("positioning")),
            "brand_promise": _clean(identity.get("brand_promise")),
            "primary_market": _clean(market.get("primary_market")),
            "content_language": _clean(market.get("content_language")),
            "locale": _clean(market.get("audience_locale") or market.get("locale")),
            "target_audience": primary_audience or category_audience,
            "visual_source_profile": visual_source_profile,
        }
        missing = [name for name, value in required.items() if not value]
        if not audience_segments:
            missing.append("audience_segments")
        pain_points = _strings(audience.get("pain_points"))
        if not pain_points:
            missing.append("audience_pain_points")
        if not desired_outcomes:
            missing.append("audience_desired_outcomes")
        if missing:
            raise NicheContractCompilationError(
                [NicheReasonCode.NICHE_CONTRACT_REQUIRED_FIELD_MISSING],
                ",".join(sorted(missing)),
            )

        channel_id = _uuid(_get(channel, "id"))
        profile_id = _uuid(_get(profile_version, "id"))
        snapshot_id = _uuid(_get(policy_snapshot, "id"))
        assert channel_id and profile_id and snapshot_id
        category_id = slot_result.category_binding.category_id
        slot_id = slot_result.slot_binding.slot_id
        channel_contract_hash = nich1_stable_hash(channel_contract)
        profile_hash = str(_get(profile_version, "profile_input_hash"))
        snapshot_hash = str(_get(policy_snapshot, "content_hash"))

        voice_summary = _summary(
            {
                "tone": voice.get("narration_tone")
                or _as_dict(profile_input.get("voice_style")).get("tone"),
                "pacing": voice.get("pacing")
                or _as_dict(profile_input.get("voice_style")).get("pacing"),
                "allowed_style": _strings(voice.get("allowed_style")),
                "forbidden_style": _strings(voice.get("forbidden_style")),
            }
        )
        format_summary = _summary(
            {
                "primary_format": _as_dict(
                    compiled_channel_policy.get("channel_identity_policy")
                ).get("primary_format"),
                "slot_format_hint": _get(editorial_slot, "format_hint"),
                "format_policy": format_policy,
                "category_format_policy": _as_dict(
                    _get(category, "default_format_policy_json", {})
                ),
            }
        )
        digest_payload = {
            "digest_version": NICHE_CONTRACT_DIGEST_VERSION,
            "channel_id": channel_id,
            "channel_key": _clean(_get(channel, "key")) or str(channel_id),
            "channel_contract_ref": _ref(
                "compiled-policy-snapshot", snapshot_id, "channel_contract"
            ),
            "channel_contract_hash": channel_contract_hash,
            "channel_profile_version_ref": _ref("channel-profile-version", profile_id),
            "channel_profile_version_hash": profile_hash,
            "compiled_policy_snapshot_ref": _ref(
                "compiled-policy-snapshot", snapshot_id
            ),
            "compiled_policy_snapshot_hash": snapshot_hash,
            "primary_niche": required["primary_niche"],
            "sub_niche": required["sub_niche"],
            "positioning": required["positioning"],
            "brand_promise": required["brand_promise"],
            "primary_market": required["primary_market"],
            "content_language": required["content_language"],
            "locale": required["locale"],
            "target_audience": required["target_audience"],
            "audience_segments": audience_segments,
            "audience_pain_points": pain_points,
            "audience_desired_outcomes": desired_outcomes,
            "content_pillars": _strings(editorial.get("content_pillars")),
            "allowed_topics": _strings(editorial.get("allowed_topics")),
            "forbidden_topics": _strings(editorial.get("forbidden_topics")),
            "category_id": category_id,
            "category_ref": _ref("content-category", category_id),
            "category_hash": _category_hash(category),
            "category_name": slot_result.category_binding.category_name,
            "category_sub_niche": slot_result.category_binding.sub_niche,
            "category_allowed_topics": slot_result.category_binding.allowed_topics,
            "category_forbidden_topics": slot_result.category_binding.forbidden_topics,
            "editorial_slot_id": slot_id,
            "editorial_slot_ref": slot_result.slot_binding.slot_ref,
            "editorial_slot_hash": slot_result.slot_binding.content_hash,
            "content_pillar_id": slot_result.slot_binding.content_pillar_id,
            "content_pillar_key": slot_result.slot_binding.content_pillar_key,
            "series_key": slot_result.slot_binding.series_key,
            "production_goal": slot_result.slot_binding.production_goal,
            "voice_tone_summary": voice_summary,
            "format_summary": format_summary,
            "visual_source_profile": required["visual_source_profile"],
        }
        return _seal(NicheContractDigest, digest_payload)

    def _authority_reasons(
        self,
        *,
        channel: Any,
        profile_version: Any,
        policy_snapshot: Any,
        allow_historical_approved: bool = False,
    ) -> list[NicheReasonCode]:
        reasons: list[NicheReasonCode] = []
        channel_id = _uuid(_get(channel, "id"))
        profile_id = _uuid(_get(profile_version, "id"))
        snapshot_id = _uuid(_get(policy_snapshot, "id"))
        if (
            not channel_id
            or _uuid(_get(profile_version, "channel_workspace_id")) != channel_id
        ):
            reasons.append(NicheReasonCode.PROFILE_SCOPE_MISMATCH)
        if str(_get(profile_version, "status", "")).lower() not in {
            "approved",
            "active",
        }:
            reasons.append(NicheReasonCode.PROFILE_NOT_ACTIVE_OR_APPROVED)
        profile_payload = _profile_input(profile_version)
        profile_hash = _clean(_get(profile_version, "profile_input_hash"))
        if not profile_hash or profile_hash != nich1_stable_hash(profile_payload):
            reasons.append(NicheReasonCode.PROFILE_HASH_MISMATCH)
        if (
            not channel_id
            or not snapshot_id
            or _uuid(_get(policy_snapshot, "channel_workspace_id")) != channel_id
            or _uuid(_get(policy_snapshot, "channel_profile_version_id")) != profile_id
        ):
            reasons.append(NicheReasonCode.POLICY_SCOPE_MISMATCH)
        snapshot_status = str(_get(policy_snapshot, "status", "")).lower()
        valid_policy_status = (
            snapshot_status in {"active", "approved"}
            if allow_historical_approved
            else snapshot_status == "active"
            and _uuid(_get(channel, "active_policy_snapshot_id")) == snapshot_id
        )
        if not valid_policy_status:
            reasons.append(NicheReasonCode.POLICY_SNAPSHOT_NOT_ACTIVE)
        snapshot_payload = _compiled_payload(policy_snapshot)
        snapshot_hash = _clean(_get(policy_snapshot, "content_hash"))
        if not snapshot_hash or snapshot_hash != nich1_stable_hash(snapshot_payload):
            reasons.append(NicheReasonCode.POLICY_SNAPSHOT_HASH_MISMATCH)
        return _ordered_unique(reasons)


def channel_fit_threshold_from_compiled_policy(compiled_policy: Any) -> float:
    payload = _compiled_payload(compiled_policy)
    scoped = _as_dict(payload.get("channel_scoped_policy"))
    scoped_gate_policy = _as_dict(scoped.get("gate_policy"))
    compiled_gate_policy = _as_dict(payload.get("gate_policy"))
    raw = scoped_gate_policy.get("channel_fit_threshold")
    if raw is None:
        raw = compiled_gate_policy.get("channel_fit_threshold")
    if isinstance(raw, bool) or raw is None:
        raise NichePolicyThresholdError(
            NicheReasonCode.CHANNEL_FIT_POLICY_THRESHOLD_MISSING.value
        )
    try:
        threshold = float(raw)
    except (TypeError, ValueError) as exc:
        raise NichePolicyThresholdError(
            NicheReasonCode.CHANNEL_FIT_POLICY_THRESHOLD_MISSING.value
        ) from exc
    if not 0.0 <= threshold <= 1.0:
        raise NichePolicyThresholdError(
            NicheReasonCode.CHANNEL_FIT_POLICY_THRESHOLD_MISSING.value
        )
    return threshold


def evaluate_channel_fit(
    *,
    score: float,
    compiled_policy: Any,
    gate_results: Sequence[NicheGateResult],
    evidence_refs: Sequence[NicheEvidenceRef],
    required_gate_keys: Sequence[NicheGateKey] = (NicheGateKey.TOPIC,),
    caller_policy_fit_state: str | None = None,
) -> ChannelFitEvaluation:
    threshold = channel_fit_threshold_from_compiled_policy(compiled_policy)
    if isinstance(score, bool) or not 0.0 <= float(score) <= 1.0:
        raise ValueError("NICH1_CHANNEL_FIT_SCORE_OUT_OF_RANGE")
    by_key = {result.gate_key: result for result in gate_results}
    reasons: list[NicheReasonCode] = []
    missing = [key for key in required_gate_keys if key not in by_key]
    if missing:
        reasons.append(NicheReasonCode.MANDATORY_NICHE_GATE_EVIDENCE_MISSING)
    required_results = [by_key[key] for key in required_gate_keys if key in by_key]
    if any(result.verdict == NicheGateVerdict.BLOCK for result in required_results):
        reasons.append(NicheReasonCode.CHANNEL_FIT_GATE_BLOCKED)
    elif any(
        result.verdict == NicheGateVerdict.REVIEW_REQUIRED
        for result in required_results
    ):
        reasons.append(NicheReasonCode.CHANNEL_FIT_GATE_REVIEW_REQUIRED)
    if float(score) < threshold:
        reasons.append(NicheReasonCode.CHANNEL_FIT_BELOW_THRESHOLD)
    if not evidence_refs:
        reasons.append(NicheReasonCode.CHANNEL_FIT_EVIDENCE_MISSING)
    if caller_policy_fit_state is not None:
        reasons.append(NicheReasonCode.CALLER_POLICY_FIT_STATE_IGNORED)

    hard_block_codes = {
        NicheReasonCode.MANDATORY_NICHE_GATE_EVIDENCE_MISSING,
        NicheReasonCode.CHANNEL_FIT_GATE_BLOCKED,
        NicheReasonCode.CHANNEL_FIT_BELOW_THRESHOLD,
        NicheReasonCode.CHANNEL_FIT_EVIDENCE_MISSING,
    }
    if set(reasons) & hard_block_codes:
        verdict = NicheGateVerdict.BLOCK
    elif NicheReasonCode.CHANNEL_FIT_GATE_REVIEW_REQUIRED in reasons:
        verdict = NicheGateVerdict.REVIEW_REQUIRED
    else:
        verdict = NicheGateVerdict.PASS
    payload = {
        "channel_fit_score": float(score),
        "channel_fit_threshold": threshold,
        "channel_fit_result": verdict,
        "policy_fit_state": verdict,
        "reason_codes": _ordered_unique(reasons),
        "evidence_refs": list(evidence_refs),
        "required_gate_keys": list(required_gate_keys),
        "gate_result_hashes": {
            key: by_key[key].content_hash for key in required_gate_keys if key in by_key
        },
        "caller_policy_fit_state_ignored": caller_policy_fit_state,
    }
    return _seal(ChannelFitEvaluation, payload)


class _BaseNicheAlignmentGate:
    gate_key: NicheGateKey
    required_criteria: frozenset[NicheCriterion]
    summary_label: str

    def _common_checks(self, data: Any) -> list[NicheGateCheck]:
        checks: list[NicheGateCheck] = []
        digest = data.niche_contract_digest
        if digest is None:
            checks.append(
                _check(
                    "niche_contract_digest_present",
                    NicheGateVerdict.BLOCK,
                    NicheReasonCode.NICHE_CONTRACT_DIGEST_MISSING,
                )
            )
        else:
            digest_ref = data.niche_contract_digest_ref or ""
            ref_match = digest_ref.startswith(
                (
                    "context-pack://",
                    "artifact-version://",
                    "editorial-slot://",
                    "niche-contract-digest://",
                )
            )
            checks.append(
                _check(
                    "niche_contract_digest_ref",
                    NicheGateVerdict.PASS if ref_match else NicheGateVerdict.BLOCK,
                    None
                    if ref_match
                    else NicheReasonCode.NICHE_CONTRACT_DIGEST_REF_MISMATCH,
                )
            )
            hash_match = data.niche_contract_digest_hash == digest.content_hash
            checks.append(
                _check(
                    "niche_contract_digest_hash",
                    NicheGateVerdict.PASS if hash_match else NicheGateVerdict.BLOCK,
                    None
                    if hash_match
                    else NicheReasonCode.NICHE_CONTRACT_DIGEST_HASH_MISMATCH,
                )
            )
            active_match = (
                data.active_policy_snapshot_ref == digest.compiled_policy_snapshot_ref
                and data.active_policy_snapshot_hash
                == digest.compiled_policy_snapshot_hash
            )
            checks.append(
                _check(
                    "active_policy_snapshot",
                    NicheGateVerdict.PASS if active_match else NicheGateVerdict.BLOCK,
                    None
                    if active_match
                    else NicheReasonCode.NICHE_CONTRACT_DIGEST_STALE,
                )
            )
        evidence_by_criterion = {
            item.criterion: item for item in data.semantic_evidence
        }
        for criterion in sorted(self.required_criteria, key=lambda item: item.value):
            item = evidence_by_criterion.get(criterion)
            if item is None:
                checks.append(
                    _check(
                        f"semantic:{criterion.value}",
                        NicheGateVerdict.BLOCK,
                        NicheReasonCode.SEMANTIC_EVIDENCE_MISSING,
                        {"criterion": criterion.value},
                    )
                )
                continue
            reasons = list(item.reason_codes)
            if item.verdict == NicheGateVerdict.BLOCK:
                reasons.append(NicheReasonCode.SEMANTIC_ALIGNMENT_BLOCKED)
            elif item.verdict == NicheGateVerdict.REVIEW_REQUIRED:
                reasons.append(NicheReasonCode.SEMANTIC_ALIGNMENT_REVIEW_REQUIRED)
            checks.append(
                NicheGateCheck(
                    check_key=f"semantic:{criterion.value}",
                    verdict=item.verdict,
                    reason_codes=_ordered_unique(reasons),
                    details={"criterion": criterion.value, "score": item.score},
                )
            )
        return checks

    def _result(self, data: Any, checks: Sequence[NicheGateCheck]) -> NicheGateResult:
        verdict = _worst_verdict(check.verdict for check in checks)
        reasons = _ordered_unique(
            [code for check in checks for code in check.reason_codes]
        )
        evidence_refs: list[NicheEvidenceRef] = list(data.evidence_refs)
        for item in data.semantic_evidence:
            evidence_refs.extend(item.evidence_refs)
        evidence_refs = _unique_evidence_refs(evidence_refs)
        payload = {
            "gate_key": self.gate_key,
            "verdict": verdict,
            "reason_codes": reasons,
            "checks": list(checks),
            "niche_contract_digest_ref": data.niche_contract_digest_ref,
            "niche_contract_digest_hash": data.niche_contract_digest_hash,
            "subject_ref": data.subject_ref,
            "subject_hash": data.subject_hash,
            "checked_policy_snapshot_ref": data.active_policy_snapshot_ref,
            "checked_policy_snapshot_hash": data.active_policy_snapshot_hash,
            "evidence_refs": evidence_refs,
            "human_review_required": verdict == NicheGateVerdict.REVIEW_REQUIRED,
            "summary": f"{self.summary_label}: {verdict.value}",
        }
        return _seal(NicheGateResult, payload)


class TopicNicheAlignmentGate(_BaseNicheAlignmentGate):
    gate_key = NicheGateKey.TOPIC
    summary_label = "Topic niche alignment"
    required_criteria = frozenset(
        {
            NicheCriterion.NICHE_RELEVANCE,
            NicheCriterion.AUDIENCE_FIT,
            NicheCriterion.POSITIONING_FIT,
            NicheCriterion.BRAND_PROMISE_FIT,
            NicheCriterion.ALLOWED_TOPIC_COMPLIANCE,
            NicheCriterion.SERIES_FIT,
            NicheCriterion.PRODUCTION_GOAL_FIT,
        }
    )

    def evaluate(self, data: TopicNicheAlignmentInput) -> NicheGateResult:
        checks = self._common_checks(data)
        digest = data.niche_contract_digest
        slot = data.slot_binding
        category = data.category_binding
        if digest is None:
            return self._result(data, checks)
        if slot is None:
            checks.append(
                _check(
                    "slot_binding",
                    NicheGateVerdict.BLOCK,
                    NicheReasonCode.SLOT_SCOPE_MISMATCH,
                )
            )
        else:
            slot_match = (
                slot.slot_id == digest.editorial_slot_id
                and slot.channel_id == digest.channel_id
                and slot.category_id == digest.category_id
                and _same(slot.content_pillar_key, digest.content_pillar_key)
                and (
                    (slot.series_key is None and digest.series_key is None)
                    or _same(slot.series_key, digest.series_key)
                )
                and _same(slot.production_goal, digest.production_goal)
                and slot.active_policy_snapshot_ref
                == digest.compiled_policy_snapshot_ref
                and slot.active_policy_snapshot_hash
                == digest.compiled_policy_snapshot_hash
            )
            checks.append(
                _check(
                    "slot_binding",
                    NicheGateVerdict.PASS if slot_match else NicheGateVerdict.BLOCK,
                    None if slot_match else NicheReasonCode.SLOT_SCOPE_MISMATCH,
                )
            )
        if category is None:
            checks.append(
                _check(
                    "category_binding",
                    NicheGateVerdict.BLOCK,
                    NicheReasonCode.CATEGORY_BINDING_MISSING,
                )
            )
        else:
            category_match = (
                category.category_id == digest.category_id
                and category.channel_id == digest.channel_id
                and str(category.status).upper() == "ACTIVE"
                and _same(category.sub_niche, digest.category_sub_niche)
                and _same(category.content_pillar_key, digest.content_pillar_key)
            )
            checks.append(
                _check(
                    "category_binding",
                    NicheGateVerdict.PASS if category_match else NicheGateVerdict.BLOCK,
                    None if category_match else NicheReasonCode.CATEGORY_MISMATCH,
                )
            )
        channel_match = data.channel_id == digest.channel_id
        checks.append(
            _check(
                "channel_binding",
                NicheGateVerdict.PASS if channel_match else NicheGateVerdict.BLOCK,
                None if channel_match else NicheReasonCode.CHANNEL_SCOPE_MISMATCH,
            )
        )
        text = " ".join([data.topic, data.angle or "", *data.claim_scope])
        forbidden = [*digest.forbidden_topics, *digest.category_forbidden_topics]
        conflicts = _matched_forbidden_topics(text, forbidden)
        checks.append(
            _check(
                "forbidden_topic_compliance",
                NicheGateVerdict.PASS if not conflicts else NicheGateVerdict.BLOCK,
                None if not conflicts else NicheReasonCode.FORBIDDEN_TOPIC_CONFLICT,
                {"matched_topics": conflicts},
            )
        )
        checks.append(
            _check(
                "adjacent_niche_conflict",
                NicheGateVerdict.BLOCK
                if data.adjacent_niche_conflict
                else NicheGateVerdict.PASS,
                NicheReasonCode.ADJACENT_NICHE_CONFLICT
                if data.adjacent_niche_conflict
                else None,
            )
        )
        return self._result(data, checks)


class ScriptNicheAlignmentGate(_BaseNicheAlignmentGate):
    gate_key = NicheGateKey.SCRIPT
    summary_label = "Script niche alignment"
    required_criteria = frozenset(
        {
            NicheCriterion.TOPIC_FIDELITY,
            NicheCriterion.NICHE_RELEVANCE,
            NicheCriterion.AUDIENCE_FIT,
            NicheCriterion.POSITIONING_FIT,
            NicheCriterion.BRAND_PROMISE_FIT,
            NicheCriterion.CLAIM_SCOPE_FIT,
        }
    )

    def evaluate(self, data: ScriptNicheAlignmentInput) -> NicheGateResult:
        checks = self._common_checks(data)
        digest = data.niche_contract_digest
        if digest is None:
            return self._result(data, checks)
        upstream_pass = (
            data.topic_gate_result.gate_key == NicheGateKey.TOPIC
            and data.topic_gate_result.verdict == NicheGateVerdict.PASS
        )
        checks.append(
            _check(
                "upstream_topic_gate",
                NicheGateVerdict.PASS if upstream_pass else NicheGateVerdict.BLOCK,
                None if upstream_pass else NicheReasonCode.UPSTREAM_TOPIC_GATE_NOT_PASS,
            )
        )
        candidate_binding = (
            data.editorial_idea_candidate_ref == data.topic_gate_result.subject_ref
            and data.editorial_idea_candidate_hash
            == data.topic_gate_result.subject_hash
        )
        checks.append(
            _check(
                "editorial_candidate_topic_gate_binding",
                NicheGateVerdict.PASS if candidate_binding else NicheGateVerdict.BLOCK,
                None if candidate_binding else NicheReasonCode.ARTIFACT_BINDING_MISSING,
            )
        )
        topic_gate_ref_bound = (
            data.topic_gate_result.content_hash in data.topic_gate_ref
        )
        checks.append(
            _check(
                "topic_gate_result_hash_binding",
                NicheGateVerdict.PASS
                if topic_gate_ref_bound
                else NicheGateVerdict.BLOCK,
                None
                if topic_gate_ref_bound
                else NicheReasonCode.ARTIFACT_BINDING_MISSING,
            )
        )
        binding_match = (
            _same(data.declared_primary_niche, digest.primary_niche)
            and _same(data.declared_sub_niche, digest.category_sub_niche)
            and data.declared_category_id == digest.category_id
            and _same(data.declared_content_pillar_key, digest.content_pillar_key)
        )
        checks.append(
            _check(
                "script_declared_niche_binding",
                NicheGateVerdict.PASS if binding_match else NicheGateVerdict.BLOCK,
                None if binding_match else NicheReasonCode.CATEGORY_SUB_NICHE_MISMATCH,
            )
        )
        topic_overlap = _labels_overlap(
            data.approved_topic, data.script_topic
        ) or _same(data.approved_topic, data.script_topic)
        checks.append(
            _check(
                "approved_topic_fidelity",
                NicheGateVerdict.PASS if topic_overlap else NicheGateVerdict.BLOCK,
                None if topic_overlap else NicheReasonCode.APPROVED_TOPIC_DRIFT,
            )
        )
        pain_served = _any_label_match(
            data.addressed_audience_pain_points, digest.audience_pain_points
        )
        outcome_served = _any_label_match(
            data.addressed_audience_desired_outcomes,
            digest.audience_desired_outcomes,
        )
        checks.append(
            _check(
                "audience_pain_served",
                NicheGateVerdict.PASS if pain_served else NicheGateVerdict.BLOCK,
                None if pain_served else NicheReasonCode.AUDIENCE_PAIN_NOT_SERVED,
            )
        )
        checks.append(
            _check(
                "audience_outcome_served",
                NicheGateVerdict.PASS if outcome_served else NicheGateVerdict.BLOCK,
                None if outcome_served else NicheReasonCode.AUDIENCE_OUTCOME_NOT_SERVED,
            )
        )
        text = " ".join([data.script_topic, data.script_text, *data.claim_scope])
        conflicts = _matched_forbidden_topics(
            text, [*digest.forbidden_topics, *digest.category_forbidden_topics]
        )
        checks.append(
            _check(
                "script_forbidden_topic_compliance",
                NicheGateVerdict.PASS if not conflicts else NicheGateVerdict.BLOCK,
                None if not conflicts else NicheReasonCode.FORBIDDEN_TOPIC_CONFLICT,
                {"matched_topics": conflicts},
            )
        )
        checks.append(
            _check(
                "script_adjacent_niche_conflict",
                NicheGateVerdict.BLOCK
                if data.adjacent_niche_conflict
                else NicheGateVerdict.PASS,
                NicheReasonCode.ADJACENT_NICHE_CONFLICT
                if data.adjacent_niche_conflict
                else None,
            )
        )
        return self._result(data, checks)


class VisualNicheAlignmentGate(_BaseNicheAlignmentGate):
    gate_key = NicheGateKey.VISUAL
    summary_label = "Visual niche alignment"
    required_criteria = frozenset(
        {
            NicheCriterion.VISUAL_LANGUAGE_FIT,
            NicheCriterion.VISUAL_MEANING_FIDELITY,
            NicheCriterion.PILLAR_CATEGORY_FIT,
        }
    )

    def evaluate(self, data: VisualNicheAlignmentInput) -> NicheGateResult:
        checks = self._common_checks(data)
        digest = data.niche_contract_digest
        if digest is None:
            return self._result(data, checks)
        binding_match = data.category_id == digest.category_id and _same(
            data.content_pillar_key, digest.content_pillar_key
        )
        checks.append(
            _check(
                "visual_pillar_category_binding",
                NicheGateVerdict.PASS if binding_match else NicheGateVerdict.BLOCK,
                None if binding_match else NicheReasonCode.CATEGORY_PILLAR_MISMATCH,
            )
        )
        direction_channel = _clean(data.visual_direction_contract.get("channel_id"))
        direction_match = bool(
            direction_channel and _same(direction_channel, str(digest.channel_id))
        )
        checks.append(
            _check(
                "visual_direction_channel",
                NicheGateVerdict.PASS if direction_match else NicheGateVerdict.BLOCK,
                None
                if direction_match
                else NicheReasonCode.VISUAL_DIRECTION_CHANNEL_MISMATCH,
            )
        )
        small_team_profile_ok = not _same(digest.channel_key, "small-team-ai") or _same(
            digest.visual_source_profile, "STOCK_ASSISTED"
        )
        checks.append(
            _check(
                "small_team_ai_visual_source_profile",
                NicheGateVerdict.PASS
                if small_team_profile_ok
                else NicheGateVerdict.BLOCK,
                None
                if small_team_profile_ok
                else NicheReasonCode.SMALL_TEAM_AI_STOCK_ASSISTED_REQUIRED,
            )
        )
        decisions = {
            str(item.get("scene_id")): item
            for item in data.visual_source_decisions
            if item.get("scene_id") not in (None, "")
        }
        for index, scene in enumerate(data.scene_visual_intents):
            scene_id = str(scene.get("scene_id") or f"scene-{index}")
            decision = decisions.get(scene_id)
            if decision is None:
                checks.append(
                    _check(
                        f"scene:{scene_id}:decision",
                        NicheGateVerdict.BLOCK,
                        NicheReasonCode.VISUAL_SCENE_DECISION_MISSING,
                    )
                )
                continue
            profiles = [
                _clean(scene.get("niche_visual_source_profile")),
                _clean(decision.get("niche_visual_source_profile")),
            ]
            profile_match = all(
                profile is None or _same(profile, digest.visual_source_profile)
                for profile in profiles
            )
            checks.append(
                _check(
                    f"scene:{scene_id}:source_profile",
                    NicheGateVerdict.PASS if profile_match else NicheGateVerdict.BLOCK,
                    None
                    if profile_match
                    else NicheReasonCode.VISUAL_SOURCE_PROFILE_MISMATCH,
                )
            )
            route = str(
                decision.get("preferred_source_route")
                or decision.get("visual_source_route")
                or ""
            )
            feature = _as_dict(decision.get("input_feature_snapshot"))
            scene_class = _normalized(
                scene.get("scene_class") or feature.get("scene_class")
            )
            narrative = _normalized(
                scene.get("narrative_function") or feature.get("narrative_function")
            )
            meaning = _normalized(
                " ".join(
                    str(value or "")
                    for value in (
                        scene.get("scene_meaning"),
                        scene.get("semantic_intent"),
                        scene.get("editorial_intent"),
                    )
                )
            )
            mechanism = any(
                marker in f"{scene_class} {narrative} {meaning}"
                for marker in ("mechanism", "workflow", "process", "how it works")
            )
            mechanism_ok = not mechanism or route in _NATIVE_MECHANISM_ROUTES
            checks.append(
                _check(
                    f"scene:{scene_id}:mechanism_meaning",
                    NicheGateVerdict.PASS if mechanism_ok else NicheGateVerdict.BLOCK,
                    None
                    if mechanism_ok
                    else NicheReasonCode.MECHANISM_MEANING_REPLACED_BY_GENERIC_STOCK,
                    {"route": route, "scene_class": scene_class},
                )
            )
            if route in _AI_IMAGE_ROUTES:
                justified = bool(
                    data.ai_image_editorial_justification_refs.get(scene_id)
                )
                checks.append(
                    _check(
                        f"scene:{scene_id}:ai_image_justification",
                        NicheGateVerdict.PASS if justified else NicheGateVerdict.BLOCK,
                        None
                        if justified
                        else NicheReasonCode.AI_IMAGE_EDITORIAL_JUSTIFICATION_MISSING,
                    )
                )
            evidence_truth = float(
                scene.get("evidence_truth_requirement")
                or feature.get("evidence_truth_requirement")
                or 0.0
            )
            actual_evidence = evidence_truth >= 0.5 or any(
                marker in scene_class
                for marker in ("actual ui", "product", "document", "evidence")
            )
            if actual_evidence:
                authorized = route in _AUTHORIZED_EVIDENCE_ROUTES and bool(
                    data.authorized_asset_evidence_refs.get(scene_id)
                )
                checks.append(
                    _check(
                        f"scene:{scene_id}:authorized_evidence",
                        NicheGateVerdict.PASS if authorized else NicheGateVerdict.BLOCK,
                        None
                        if authorized
                        else NicheReasonCode.AUTHORIZED_ASSET_REQUIRED_FOR_EVIDENCE,
                    )
                )
        return self._result(data, checks)


class ThumbnailNicheAlignmentGate(_BaseNicheAlignmentGate):
    gate_key = NicheGateKey.THUMBNAIL
    summary_label = "Thumbnail niche alignment"
    required_criteria = frozenset(
        {
            NicheCriterion.THUMBNAIL_PROMISE_FIDELITY,
            NicheCriterion.VISUAL_LANGUAGE_FIT,
            NicheCriterion.CLAIM_SCOPE_FIT,
        }
    )

    def evaluate(self, data: ThumbnailNicheAlignmentInput) -> NicheGateResult:
        checks = self._common_checks(data)
        digest = data.niche_contract_digest
        if digest is None:
            return self._result(data, checks)
        promise_match = _same(
            data.approved_topic, data.thumbnail_promise
        ) or _labels_overlap(data.approved_topic, data.thumbnail_promise)
        checks.append(
            _check(
                "thumbnail_topic_promise",
                NicheGateVerdict.PASS if promise_match else NicheGateVerdict.BLOCK,
                None
                if promise_match
                else NicheReasonCode.THUMBNAIL_TOPIC_PROMISE_MISMATCH,
            )
        )
        implied_niche_match = any(
            _same(data.implied_niche, item)
            for item in (
                digest.primary_niche,
                digest.sub_niche,
                digest.category_sub_niche,
            )
        )
        checks.append(
            _check(
                "thumbnail_implied_niche",
                NicheGateVerdict.PASS
                if implied_niche_match
                else NicheGateVerdict.BLOCK,
                None
                if implied_niche_match
                else NicheReasonCode.ADJACENT_NICHE_CONFLICT,
            )
        )
        text = " ".join(
            [data.thumbnail_promise, *data.text_claims, *data.number_claims]
        )
        conflicts = _matched_forbidden_topics(
            text, [*digest.forbidden_topics, *digest.category_forbidden_topics]
        )
        checks.append(
            _check(
                "thumbnail_forbidden_topic_compliance",
                NicheGateVerdict.PASS if not conflicts else NicheGateVerdict.BLOCK,
                None if not conflicts else NicheReasonCode.FORBIDDEN_TOPIC_CONFLICT,
                {"matched_topics": conflicts},
            )
        )
        claims_bound = not (data.text_claims or data.number_claims) or bool(
            data.claim_evidence_refs
        )
        checks.append(
            _check(
                "thumbnail_claim_evidence",
                NicheGateVerdict.PASS if claims_bound else NicheGateVerdict.BLOCK,
                None
                if claims_bound
                else NicheReasonCode.THUMBNAIL_CLAIM_EVIDENCE_MISSING,
            )
        )
        checks.append(
            _check(
                "thumbnail_product_ui_truthfulness",
                NicheGateVerdict.BLOCK
                if data.misleading_product_or_ui_representation
                else NicheGateVerdict.PASS,
                NicheReasonCode.THUMBNAIL_MISLEADING_PRODUCT_UI
                if data.misleading_product_or_ui_representation
                else None,
            )
        )
        return self._result(data, checks)


class MetadataNicheAlignmentGate(_BaseNicheAlignmentGate):
    gate_key = NicheGateKey.METADATA
    summary_label = "Metadata niche alignment"
    required_criteria = frozenset(
        {
            NicheCriterion.METADATA_TOPIC_FIDELITY,
            NicheCriterion.AUDIENCE_FIT,
            NicheCriterion.POSITIONING_FIT,
            NicheCriterion.CLAIM_SCOPE_FIT,
            NicheCriterion.CTA_FIT,
        }
    )

    def evaluate(self, data: MetadataNicheAlignmentInput) -> NicheGateResult:
        checks = self._common_checks(data)
        digest = data.niche_contract_digest
        if digest is None:
            return self._result(data, checks)
        binding_match = data.declared_category_id == digest.category_id and _same(
            data.declared_content_pillar_key, digest.content_pillar_key
        )
        checks.append(
            _check(
                "metadata_pillar_category_binding",
                NicheGateVerdict.PASS if binding_match else NicheGateVerdict.BLOCK,
                None if binding_match else NicheReasonCode.METADATA_TOPIC_MISMATCH,
            )
        )
        topic_match = _same(data.approved_topic, data.title) or _labels_overlap(
            data.approved_topic, data.title
        )
        checks.append(
            _check(
                "metadata_title_topic",
                NicheGateVerdict.PASS if topic_match else NicheGateVerdict.BLOCK,
                None if topic_match else NicheReasonCode.METADATA_TOPIC_MISMATCH,
            )
        )
        text = " ".join(
            [
                data.title,
                data.description,
                *data.keywords,
                *data.tags,
                *data.chapters,
                data.summary_copy or "",
                data.manual_publishing_copy or "",
                data.cta or "",
                *data.claim_scope,
            ]
        )
        conflicts = _matched_forbidden_topics(
            text, [*digest.forbidden_topics, *digest.category_forbidden_topics]
        )
        checks.append(
            _check(
                "metadata_forbidden_topic_compliance",
                NicheGateVerdict.PASS if not conflicts else NicheGateVerdict.BLOCK,
                None if not conflicts else NicheReasonCode.FORBIDDEN_TOPIC_CONFLICT,
                {"matched_topics": conflicts},
            )
        )
        claims_bound = not data.claim_scope or bool(data.claim_evidence_refs)
        checks.append(
            _check(
                "metadata_claim_evidence",
                NicheGateVerdict.PASS if claims_bound else NicheGateVerdict.BLOCK,
                None
                if claims_bound
                else NicheReasonCode.METADATA_CLAIM_EVIDENCE_MISSING,
            )
        )
        checks.append(
            _check(
                "metadata_adjacent_niche_conflict",
                NicheGateVerdict.BLOCK
                if data.adjacent_niche_conflict
                else NicheGateVerdict.PASS,
                NicheReasonCode.ADJACENT_NICHE_CONFLICT
                if data.adjacent_niche_conflict
                else None,
            )
        )
        return self._result(data, checks)


class NicheAlignmentGateRegistry:
    def __init__(self, gates: Mapping[NicheGateKey | str, Any] | None = None):
        selected = gates or {
            NicheGateKey.TOPIC: TopicNicheAlignmentGate(),
            NicheGateKey.SCRIPT: ScriptNicheAlignmentGate(),
            NicheGateKey.VISUAL: VisualNicheAlignmentGate(),
            NicheGateKey.THUMBNAIL: ThumbnailNicheAlignmentGate(),
            NicheGateKey.METADATA: MetadataNicheAlignmentGate(),
        }
        self._gates = {NicheGateKey(str(key)): gate for key, gate in selected.items()}
        missing = set(NICHE_GATE_STRICT_ORDER) - set(self._gates)
        if missing:
            raise ValueError(
                "NICH1_REQUIRED_GATE_REGISTRATION_MISSING:"
                + ",".join(sorted(key.value for key in missing))
            )
        for key, gate in self._gates.items():
            if getattr(gate, "gate_key", None) != key:
                raise ValueError(f"NICH1_GATE_KEY_IMPLEMENTATION_MISMATCH:{key.value}")

    @property
    def registered_gate_keys(self) -> tuple[NicheGateKey, ...]:
        return tuple(key for key in NICHE_GATE_STRICT_ORDER if key in self._gates)

    def resolve(self, gate_key: NicheGateKey | str) -> Any:
        return self._gates[NicheGateKey(str(gate_key))]


class NicheAlignmentDossierBuilder:
    def build(
        self,
        *,
        digest: NicheContractDigest,
        digest_ref: str,
        gate_results: Sequence[NicheGateResult],
        channel_fit: ChannelFitEvaluation | None,
        dossier_scope: NicheDossierScope,
        human_review_requirements: Sequence[str] = (),
    ) -> NicheAlignmentDossier:
        by_key = {result.gate_key: result for result in gate_results}
        required = (
            (NicheGateKey.TOPIC,)
            if dossier_scope == NicheDossierScope.PRE_ADMISSION
            else NICHE_GATE_STRICT_ORDER
        )
        missing_required = [key for key in required if key not in by_key]
        all_missing = [key for key in NICHE_GATE_STRICT_ORDER if key not in by_key]
        reasons: list[NicheReasonCode] = [
            code for result in gate_results for code in result.reason_codes
        ]
        if missing_required:
            reasons.append(NicheReasonCode.MANDATORY_NICHE_GATE_EVIDENCE_MISSING)
        if channel_fit is None:
            reasons.append(NicheReasonCode.CHANNEL_FIT_EVIDENCE_MISSING)
        else:
            reasons.extend(channel_fit.reason_codes)

        required_results = [by_key[key] for key in required if key in by_key]
        if (
            missing_required
            or channel_fit is None
            or any(
                result.verdict == NicheGateVerdict.BLOCK for result in required_results
            )
            or (
                channel_fit is not None
                and channel_fit.channel_fit_result == NicheGateVerdict.BLOCK
            )
        ):
            overall = NicheGateVerdict.BLOCK
        elif any(
            result.verdict == NicheGateVerdict.REVIEW_REQUIRED
            for result in required_results
        ) or (
            channel_fit is not None
            and channel_fit.channel_fit_result == NicheGateVerdict.REVIEW_REQUIRED
        ):
            overall = NicheGateVerdict.REVIEW_REQUIRED
        else:
            overall = NicheGateVerdict.PASS
        review_requirements = _ordered_unique_strings(human_review_requirements)
        if overall == NicheGateVerdict.REVIEW_REQUIRED:
            reasons.append(NicheReasonCode.HUMAN_REVIEW_REQUIRED)
            if not review_requirements:
                review_requirements = ["Resolve all REVIEW_REQUIRED niche checks."]

        payload = {
            "dossier_scope": dossier_scope,
            "channel_id": digest.channel_id,
            "channel_key": digest.channel_key,
            "channel_contract_ref": digest.channel_contract_ref,
            "channel_contract_hash": digest.channel_contract_hash,
            "channel_profile_version_ref": digest.channel_profile_version_ref,
            "compiled_policy_snapshot_ref": digest.compiled_policy_snapshot_ref,
            "compiled_policy_snapshot_hash": digest.compiled_policy_snapshot_hash,
            "niche_contract_digest_ref": digest_ref,
            "niche_contract_digest_hash": digest.content_hash,
            "editorial_slot_ref": digest.editorial_slot_ref,
            "category_ref": digest.category_ref,
            "content_pillar_id": digest.content_pillar_id,
            "content_pillar_key": digest.content_pillar_key,
            "series_key": digest.series_key,
            "topic_result": by_key.get(NicheGateKey.TOPIC),
            "script_result": by_key.get(NicheGateKey.SCRIPT),
            "visual_result": by_key.get(NicheGateKey.VISUAL),
            "thumbnail_result": by_key.get(NicheGateKey.THUMBNAIL),
            "metadata_result": by_key.get(NicheGateKey.METADATA),
            "channel_fit_score": channel_fit.channel_fit_score if channel_fit else None,
            "channel_fit_threshold": channel_fit.channel_fit_threshold
            if channel_fit
            else None,
            "channel_fit_result": channel_fit.channel_fit_result
            if channel_fit
            else None,
            "completed_gate_keys": [
                key for key in NICHE_GATE_STRICT_ORDER if key in by_key
            ],
            "missing_gate_keys": all_missing,
            "reason_codes": _ordered_unique(reasons),
            "human_review_requirements": review_requirements,
            "overall_verdict": overall,
        }
        return _seal(NicheAlignmentDossier, payload)


def _check(
    check_key: str,
    verdict: NicheGateVerdict,
    reason_code: NicheReasonCode | None = None,
    details: dict[str, Any] | None = None,
) -> NicheGateCheck:
    return NicheGateCheck(
        check_key=check_key,
        verdict=verdict,
        reason_codes=[reason_code] if reason_code is not None else [],
        details=details or {},
    )


def _worst_verdict(values: Any) -> NicheGateVerdict:
    verdicts = set(values)
    if NicheGateVerdict.BLOCK in verdicts:
        return NicheGateVerdict.BLOCK
    if NicheGateVerdict.REVIEW_REQUIRED in verdicts:
        return NicheGateVerdict.REVIEW_REQUIRED
    return NicheGateVerdict.PASS


def _unique_evidence_refs(values: Sequence[NicheEvidenceRef]) -> list[NicheEvidenceRef]:
    result: list[NicheEvidenceRef] = []
    seen: set[tuple[str, str, str | None]] = set()
    for value in values:
        marker = (value.type, value.ref, value.content_hash)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


__all__ = [
    "EditorialSlotValidator",
    "MetadataNicheAlignmentGate",
    "NicheAlignmentDossierBuilder",
    "NicheAlignmentGateRegistry",
    "NicheContractCompilationError",
    "NicheContractDigestCompiler",
    "NichePolicyThresholdError",
    "ScriptNicheAlignmentGate",
    "ThumbnailNicheAlignmentGate",
    "TopicNicheAlignmentGate",
    "VisualNicheAlignmentGate",
    "channel_fit_threshold_from_compiled_policy",
    "evaluate_channel_fit",
]
