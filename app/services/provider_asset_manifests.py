from __future__ import annotations

import hashlib
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
            candidates.append(
                ParsedStockCandidate(
                    candidate_id=f"pexels-{item['id']}",
                    provider_asset_id=str(item["id"]),
                    source_page_url=url,
                    creator_name=str(user["name"]),
                    creator_url=str(user.get("url") or ""),
                    width=int(item.get("width") or 1),
                    height=int(item.get("height") or 1),
                    duration_seconds=float(item.get("duration") or 0),
                    tags=[str(tag).lower() for tag in item.get("tags", [])],
                    description=str(item.get("description") or ""),
                    composition=str(item.get("composition") or "UNKNOWN"),
                    logo_or_text_present=item.get("logo_or_text_present"),
                    identifiable_person_present=item.get("identifiable_person_present"),
                    brand_or_trademark_present=item.get("brand_or_trademark_present"),
                    motion_suitability=float(item.get("motion_suitability", 0.5)),
                    channel_identity_fit=float(item.get("channel_identity_fit", 0.5)),
                    prior_use_count=int(item.get("prior_use_count", 0)),
                    video_files=list(item.get("video_files") or []),
                    source_complete=bool(user.get("url") and item.get("video_files")),
                )
            )
        return candidates


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
            if mime != "video/mp4" or width < min_width or height < min_height:
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
        safe_ref = f"volatile://pexels-download/{hashlib.sha256(raw_link.encode()).hexdigest()[:24]}"
        payload = {
            "provider_asset_id": candidate.provider_asset_id,
            "provider_file_id": str(rendition.get("id")),
            "source_page_url": candidate.source_page_url,
            "creator_name": candidate.creator_name,
            "creator_url": candidate.creator_url,
            "selected_download_url_reference": safe_ref,
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


def build_ai_hero_request(asset_request: AssetRequest, *, package_id: str, prompt_text: str) -> AIHeroAssetRequest:
    if asset_request.requested_role != "AI_HERO":
        raise ValueError("AI_HERO_REQUEST_ROLE_INVALID")
    reason = asset_request.purpose.upper()
    allowed = {"HOOK", "METAPHOR", "EMOTIONAL_PAYOFF", "VISUAL_SIGNATURE", "NATIVE_MOTION_INSUFFICIENT"}
    if reason not in allowed:
        raise ValueError("AI_HERO_REASON_NOT_ALLOWED")
    desired = max(4, min(8, int(asset_request.maximum_duration_seconds)))
    duration = min((4, 6, 8), key=lambda value: (abs(value - desired), value))
    aspect = {"landscape": "16:9", "portrait": "9:16", "square": "1:1"}[asset_request.required_orientation]
    payload = {
        "request_id": asset_request.request_id,
        "package_id": package_id,
        "scene_id": asset_request.scene_id,
        "source_segment_ids": asset_request.source_segment_ids,
        "visual_intent": asset_request.semantic_visual_intent,
        "hero_reason": reason,
        "prompt_text": prompt_text,
        "prompt_safety_status": "PASS",
        "duration_seconds": duration,
        "aspect_ratio": aspect,
        "reference_image_ref": None,
        "character_policy_mode": "NO_CHARACTER",
        "projected_cost_class": asset_request.projected_cost_class,
        "human_approval_required": True,
    }
    return AIHeroAssetRequest(**payload, request_hash=stable_hash(payload))


def build_planned_ai_generation_manifest(request: AIHeroAssetRequest) -> AIGenerationManifest:
    payload = {
        "provider": "LUMA",
        "request_ref": request.request_id,
        "request_hash": request.request_hash,
        "generation_id": None,
        "provider_status": "PLANNED_NOT_SUBMITTED",
        "prompt_hash": hashlib.sha256(request.prompt_text.encode()).hexdigest(),
        "submitted_at": None,
        "completed_at": None,
        "asset_url_reference": None,
        "downloaded_path": None,
        "downloaded_sha256": None,
        "cost_snapshot_ref": None,
        "attempt_record_ref": None,
        "media_qc_ref": None,
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
