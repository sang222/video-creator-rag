"""P1 proactive audience-delivery projection before public release.

The service does not promise distribution or manipulate platform signals. It
only proves that the package is deliberately aligned to its frozen destination,
market, language, caption, and packaging authority before a human release.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.youtube_delivery import YouTubePrivateStage


@dataclass(frozen=True, slots=True)
class AudienceDeliveryReadiness:
    result: str
    reason_codes: tuple[str, ...]
    target_market: str | None
    content_language: str | None
    destination_mode: str | None
    private_stage_state: str | None


class ProactiveAudienceDeliveryService:
    def __init__(self, session: Session):
        self.session = session

    def evaluate_candidate(self, candidate_id: uuid.UUID) -> AudienceDeliveryReadiness:
        candidate = self.session.get(FinalReviewCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError("final review candidate not found")
        lineage = dict(candidate.target_market_lineage or {})
        metadata = dict(candidate.publish_metadata_snapshot or {})
        reasons: list[str] = []
        target_market = lineage.get("primary_market") or lineage.get("target_market")
        content_language = lineage.get("content_language") or metadata.get("default_language")
        destination_mode = lineage.get("destination_mode")
        if not target_market:
            reasons.append("TARGET_MARKET_AUTHORITY_MISSING")
        if not content_language:
            reasons.append("CONTENT_LANGUAGE_AUTHORITY_MISSING")
        if not str(metadata.get("title") or "").strip():
            reasons.append("PACKAGING_TITLE_MISSING")
        if not str(metadata.get("description") or "").strip():
            reasons.append("PACKAGING_DESCRIPTION_MISSING")
        caption = metadata.get("caption_sidecar")
        if not isinstance(caption, dict) or not caption.get("caption_checksum_sha256"):
            reasons.append("CAPTION_SIDECAR_AUTHORITY_MISSING")
        if destination_mode not in {"VERIFIED_PUBLISH_DESTINATION", "FINAL_REVIEW_ONLY"}:
            reasons.append("DESTINATION_AUTHORITY_INVALID")
        stage = self.session.scalar(
            select(YouTubePrivateStage).where(
                YouTubePrivateStage.final_review_candidate_id == candidate.id
            )
        )
        if stage is not None and stage.state not in {
            "PREPARED",
            "SESSION_CREATED",
            "UPLOADING",
            "BYTES_ACCEPTED",
            "PROCESSING",
            "PRIVATE_VERIFIED",
        }:
            reasons.append("PRIVATE_STAGE_NOT_REVIEWABLE")
        return AudienceDeliveryReadiness(
            result="READY" if not reasons else "BLOCKED",
            reason_codes=tuple(reasons),
            target_market=str(target_market) if target_market else None,
            content_language=str(content_language) if content_language else None,
            destination_mode=str(destination_mode) if destination_mode else None,
            private_stage_state=stage.state if stage is not None else None,
        )
