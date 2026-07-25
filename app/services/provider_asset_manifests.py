from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.contracts.asset_acquisition import (
    AIHeroAssetRequest,
    AIGenerationManifest,
    AssetDownloadReceipt,
    AssetRequest,
    ParsedStockCandidate,
    PexelsDownloadPlan,
    PexelsQueryPlan,
    StockSourceManifest,
)
from app.services.native_render_plan import stable_hash


class PexelsRequestBuilder:
    """Build a structured request description. This class deliberately has no HTTP client."""

    def build(self, plan: PexelsQueryPlan, query: str) -> dict[str, Any]:
        if query not in plan.queries:
            raise ValueError("PEXELS_QUERY_NOT_IN_PLAN")
        return {
            "method": "GET",
            "endpoint": "/v1/videos/search",
            "query_params": {
                "query": query,
                "orientation": plan.orientation,
                "size": plan.size_preference,
                "per_page": plan.per_page,
            },
            "headers": {"Authorization": "SECRET_REFERENCE:PEXELS_API_KEY"},
            "network_execution_allowed": False,
        }


class PexelsResponseParser:
    def parse(self, payload: dict[str, Any]) -> list[ParsedStockCandidate]:
        candidates: list[ParsedStockCandidate] = []
        for item in payload.get("videos", []):
            user = item.get("user") or {}
            url = str(item.get("url") or "")
            if not item.get("id") or not url or not user.get("name"):
                continue
            description = _observable_pexels_description(
                source_page_url=url,
                provider_asset_id=str(item["id"]),
            )
            if description is None:
                continue
            creator_url = str(user.get("url") or "")
            video_files = list(item.get("video_files") or [])
            candidates.append(
                ParsedStockCandidate(
                    candidate_id=f"pexels-{item['id']}",
                    provider_asset_id=str(item["id"]),
                    source_page_url=url,
                    creator_name=str(user["name"]),
                    creator_url=creator_url,
                    width=int(item.get("width") or 1),
                    height=int(item.get("height") or 1),
                    duration_seconds=float(item.get("duration") or 0),
                    # Pexels /v1/videos/search does not expose any of these
                    # enriched ranking fields.  Only its public page slug is
                    # admissible candidate text; unexpected response fields
                    # must never fabricate semantic or risk evidence.
                    tags=[],
                    description=description,
                    composition="UNKNOWN",
                    logo_or_text_present=None,
                    identifiable_person_present=None,
                    brand_or_trademark_present=None,
                    motion_suitability=0.5,
                    channel_identity_fit=0.5,
                    prior_use_count=0,
                    video_files=video_files,
                    source_complete=bool(
                        _is_official_pexels_url(creator_url) and video_files
                    ),
                )
            )
        return candidates


def _observable_pexels_description(
    *,
    source_page_url: str,
    provider_asset_id: str,
) -> str | None:
    """Return only the public slug from an official Pexels video page URL."""

    if not _is_official_pexels_url(source_page_url):
        return None
    path_tokens = re.findall(
        r"[a-z0-9]+", urlsplit(source_page_url).path.casefold()
    )
    if (
        len(path_tokens) < 3
        or path_tokens[0] != "video"
        or not path_tokens[-1].isdigit()
        or path_tokens[-1] != provider_asset_id
    ):
        return None
    path_tokens = path_tokens[1:]
    path_tokens.pop()
    if not path_tokens:
        return None
    return " ".join(path_tokens)


def _is_official_pexels_url(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold()
        in {"pexels.com", "www.pexels.com"}
    )


class PexelsRenditionSelector:
    def select(self, candidate: ParsedStockCandidate, request: AssetRequest) -> dict[str, Any]:
        if request.requested_role != "SUPPORTING_STOCK":
            raise ValueError("PEXELS_RENDITION_ROLE_INVALID")
        min_width, min_height = _resolution(request.minimum_resolution)
        target_width, target_height = _resolution(request.preferred_resolution)
        compatible: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
        for item in candidate.video_files:
            mime = str(item.get("file_type") or item.get("mime_type") or "").lower()
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
            raw_link = str(item.get("link") or "")
            file_id = str(item.get("id") or "")
            parsed_link = urlsplit(raw_link)
            if (
                mime != "video/mp4"
                or not file_id
                or not raw_link
                or parsed_link.scheme.lower() != "https"
                or not parsed_link.hostname
                or parsed_link.path.lower().endswith(".m3u8")
                or width < min_width
                or height < min_height
            ):
                continue
            landscape = width >= height
            if request.required_orientation == "landscape" and not landscape:
                continue
            if request.required_orientation == "portrait" and landscape:
                continue
            distance = abs(width - target_width) + abs(height - target_height)
            compatible.append(((distance, -(width * height), int(item.get("id") or 0), str(item.get("id") or "")), item))
        if not compatible:
            raise ValueError("PEXELS_COMPATIBLE_MP4_NOT_FOUND")
        return sorted(compatible, key=lambda pair: pair[0])[0][1]


class PexelsDownloadPlanBuilder:
    def build(self, candidate: ParsedStockCandidate, rendition: dict[str, Any], request: AssetRequest) -> PexelsDownloadPlan:
        raw_link = str(rendition.get("link") or "")
        if not raw_link:
            raise ValueError("PEXELS_DOWNLOAD_LINK_MISSING")
        parsed = urlsplit(raw_link)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.path.lower().endswith(".m3u8"):
            raise ValueError("PEXELS_DOWNLOAD_LINK_INVALID")
        download_url_hash = hashlib.sha256(raw_link.encode()).hexdigest()
        safe_ref = f"volatile://pexels-download/{download_url_hash[:24]}"
        payload = {
            "provider_asset_id": candidate.provider_asset_id,
            "provider_file_id": str(rendition.get("id")),
            "source_page_url": candidate.source_page_url,
            "creator_name": candidate.creator_name,
            "creator_url": candidate.creator_url,
            "volatile_download_reference": safe_ref,
            "download_url_hash": download_url_hash,
            "expected_media_host": parsed.hostname,
            "query_present": bool(parsed.query),
            "width": int(rendition["width"]),
            "height": int(rendition["height"]),
            "duration": candidate.duration_seconds,
            "mime_type": str(rendition.get("file_type") or rendition.get("mime_type")),
            "expected_usage_role": "SUPPORTING_STOCK",
            "production_eligible": False,
        }
        return PexelsDownloadPlan(**payload, plan_hash=stable_hash(payload))


class PexelsRateLimitMetadataParser:
    ALLOWED = {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"}

    def parse(self, headers: dict[str, Any]) -> dict[str, int | None]:
        lowered = {str(key).lower(): value for key, value in headers.items()}
        return {key.replace("x-ratelimit-", ""): _optional_int(lowered.get(key)) for key in sorted(self.ALLOWED)}


def build_stock_source_manifest(
    *,
    asset_id: str,
    request: AssetRequest,
    query_used: str,
    candidate: ParsedStockCandidate,
    plan: PexelsDownloadPlan,
    download: AssetDownloadReceipt,
    retrieved_at: datetime,
    rights_policy_ref: str,
    attribution_required: bool = True,
) -> StockSourceManifest:
    if download.state != "ASSET_DOWNLOADED" or not download.local_path or not download.sha256 or not download.size_bytes:
        raise ValueError("DOWNLOAD_EVIDENCE_INCOMPLETE")
    payload = {
        "asset_id": asset_id,
        "provider": "PEXELS",
        "provider_asset_id": plan.provider_asset_id,
        "provider_file_id": plan.provider_file_id,
        "source_page_url": candidate.source_page_url,
        "creator_name": candidate.creator_name,
        "creator_url": candidate.creator_url,
        "retrieved_at": retrieved_at,
        "query_used": query_used,
        "width": plan.width,
        "height": plan.height,
        "duration_seconds": plan.duration,
        "mime_type": plan.mime_type,
        "local_path": download.local_path,
        "local_size_bytes": download.size_bytes,
        "local_sha256": download.sha256,
        "used_by_segments": request.source_segment_ids,
        "usage_role": "SUPPORTING_STOCK",
        "rights_policy_ref": rights_policy_ref,
        "attribution_required": attribution_required,
        "attribution_copy": f"Video by {candidate.creator_name} on Pexels" if attribution_required else "",
        "identifiable_person_present": candidate.identifiable_person_present,
        "logo_or_brand_present": candidate.logo_or_text_present or candidate.brand_or_trademark_present,
        "human_review_status": "REQUIRED" if any(value is None for value in (candidate.identifiable_person_present, candidate.logo_or_text_present, candidate.brand_or_trademark_present)) else "FIXTURE_REVIEWED",
    }
    return StockSourceManifest(**payload, manifest_hash=stable_hash(payload))


def build_ai_hero_request(
    asset_request: AssetRequest,
    *,
    package_id: str,
    project_id: str,
    channel_id: str,
    prompt_text: str,
    provider_resolution_policy_ref: str,
) -> AIHeroAssetRequest:
    if asset_request.requested_role != "AI_HERO":
        raise ValueError("AI_HERO_REQUEST_ROLE_INVALID")
    reason = asset_request.purpose.upper()
    allowed = {"HOOK", "METAPHOR", "EMOTIONAL_PAYOFF", "VISUAL_SIGNATURE", "NATIVE_MOTION_INSUFFICIENT"}
    if reason not in allowed:
        raise ValueError("AI_HERO_REASON_NOT_ALLOWED")
    if asset_request.required_orientation == "square":
        raise ValueError("AI_HERO_ASPECT_RATIO_UNSUPPORTED")
    aspect = {"landscape": "16:9", "portrait": "9:16"}[asset_request.required_orientation]
    prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
    payload = {
        "request_id": asset_request.request_id,
        "package_id": package_id,
        "project_id": project_id,
        "channel_id": channel_id,
        "scene_id": asset_request.scene_id,
        "source_segment_ids": asset_request.source_segment_ids,
        "visual_intent": asset_request.semantic_visual_intent,
        "hero_reason": reason,
        "prompt_text": prompt_text,
        "prompt_hash": prompt_hash,
        "prompt_safety_status": "PASS",
        "required_duration_seconds": 8,
        "preferred_resolution": "720p",
        "required_aspect_ratio": aspect,
        "character_policy_mode": "NO_CHARACTER",
        "projected_cost_class": asset_request.projected_cost_class,
        "human_approval_required": True,
        "provider_resolution_policy_ref": provider_resolution_policy_ref,
    }
    return AIHeroAssetRequest(**payload, request_hash=stable_hash(payload))


def build_planned_ai_generation_manifest(
    request: AIHeroAssetRequest,
    *,
    provider_key: str,
    provider_model_id: str,
    synthetic_media_disclosure_ref: str,
) -> AIGenerationManifest:
    payload = {
        "provider_key": provider_key,
        "provider_model_id": provider_model_id,
        "request_ref": request.request_id,
        "request_hash": request.request_hash,
        "external_operation_id": None,
        "provider_status": "PLANNED",
        "prompt_hash": request.prompt_hash,
        "submitted_at": None,
        "completed_at": None,
        "output_url_reference": None,
        "downloaded_path": None,
        "downloaded_sha256": None,
        "cost_snapshot_ref": None,
        "attempt_record_ref": None,
        "media_qc_ref": None,
        "synthetic_media_disclosure_ref": synthetic_media_disclosure_ref,
        "production_eligible": False,
    }
    return AIGenerationManifest(**payload, manifest_hash=stable_hash(payload))


def redact_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _resolution(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError) as exc:
        raise ValueError("RESOLUTION_INVALID") from exc


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
