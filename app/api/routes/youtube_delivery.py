"""Authenticated YouTube private-staging and operator-delivery API.

The API prepares exact authorities only.  Provider effects are dispatched by
VCOS' durable outbox/worker path; no endpoint can make a video public, schedule
publication, or delete a YouTube asset.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.youtube_delivery import (
    ProductionThumbnailBindingCreate,
    ProductionThumbnailBindingRead,
    PublicPublicationReceiptRead,
    TelegramNotificationRead,
    YouTubePrivateStageReview,
    YouTubePrivateStageRead,
    YouTubePublishingCredentialCreate,
    YouTubePublishingCredentialRead,
    YouTubeSeriesEpisodeBindingRead,
    YouTubeSeriesOrdinalBind,
    YouTubeSeriesPlaylistBindingCreate,
    YouTubeSeriesPlaylistBindingRead,
)
from app.core.db import get_session_factory
from app.db.models.channel import ChannelWorkspace
from app.db.models.production_publish import FinalReviewCandidate, FinalVideoDecision
from app.db.models.vcos_v2 import SeriesPlan
from app.db.models.youtube_delivery import (
    PublicPublicationReceipt,
    TelegramDeliveryNotification,
    YouTubeSeriesEpisodeBinding,
    YouTubePrivateStage,
)
from app.db.session import session_scope
from app.services.company_access import require_company_permission
from app.services.security_boundary import actor_from_request
from app.services.youtube_delivery import YouTubeDeliveryService


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/channels/{channel_workspace_id}/youtube-publishing-credentials",
        response_model=YouTubePublishingCredentialRead,
    )
    def register_youtube_publishing_credential(
        channel_workspace_id: uuid.UUID,
        data: YouTubePublishingCredentialCreate,
        request: Request,
    ) -> YouTubePublishingCredentialRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                channel = session.get(ChannelWorkspace, channel_workspace_id)
                if channel is None:
                    raise ValueError("CHANNEL_WORKSPACE_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="provider.execute",
                    company_id=channel.company_id,
                )
                credential = YouTubeDeliveryService(
                    session
                ).register_publishing_credential(
                    company_id=channel.company_id,
                    channel_workspace_id=channel.id,
                    data=data,
                )
                return YouTubePublishingCredentialRead.model_validate(credential)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/final-review-candidates/{candidate_id}/production-thumbnail-binding",
        response_model=ProductionThumbnailBindingRead,
    )
    def bind_production_thumbnail(
        candidate_id: uuid.UUID,
        data: ProductionThumbnailBindingCreate,
        request: Request,
    ) -> ProductionThumbnailBindingRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                candidate = session.get(FinalReviewCandidate, candidate_id)
                if candidate is None:
                    raise ValueError("FINAL_REVIEW_CANDIDATE_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="publish.prepare",
                    company_id=candidate.company_id,
                )
                binding = YouTubeDeliveryService(session).bind_generated_thumbnail(
                    candidate_id=candidate.id,
                    data=data,
                )
                return ProductionThumbnailBindingRead.model_validate(binding)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/final-video-decisions/{decision_id}/youtube-private-stage",
        response_model=YouTubePrivateStageRead,
    )
    def prepare_youtube_private_stage(
        decision_id: uuid.UUID,
        request: Request,
    ) -> YouTubePrivateStageRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                decision = session.get(FinalVideoDecision, decision_id)
                if decision is None:
                    raise ValueError("FINAL_VIDEO_DECISION_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="publish.prepare",
                    company_id=decision.company_id,
                )
                stage = YouTubeDeliveryService(
                    session
                ).prepare_private_stage_from_current_authority(
                    decision_id=decision.id,
                )
                return YouTubePrivateStageRead.model_validate(stage)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/final-review-candidates/{candidate_id}/youtube-private-stage",
        response_model=YouTubePrivateStageRead,
    )
    def prepare_youtube_private_stage_from_candidate(
        candidate_id: uuid.UUID,
        request: Request,
    ) -> YouTubePrivateStageRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                candidate = session.get(FinalReviewCandidate, candidate_id)
                if candidate is None:
                    raise ValueError("FINAL_REVIEW_CANDIDATE_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="publish.prepare",
                    company_id=candidate.company_id,
                )
                stage = YouTubeDeliveryService(session).prepare_private_stage_from_candidate(
                    candidate_id=candidate.id
                )
                return YouTubePrivateStageRead.model_validate(stage)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/youtube-private-stages/{stage_id}",
        response_model=YouTubePrivateStageRead,
    )
    def get_youtube_private_stage(
        stage_id: uuid.UUID,
        request: Request,
    ) -> YouTubePrivateStageRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                stage = YouTubeDeliveryService(session).require_stage(stage_id)
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=stage.company_id,
                )
                return YouTubePrivateStageRead.model_validate(stage)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/youtube-private-stages/{stage_id}/review",
        response_model=YouTubePrivateStageRead,
    )
    def review_youtube_private_stage(
        stage_id: uuid.UUID,
        data: YouTubePrivateStageReview,
        request: Request,
    ) -> YouTubePrivateStageRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                stage = YouTubeDeliveryService(session).require_stage(stage_id)
                require_company_permission(
                    session,
                    actor=actor,
                    permission="publish.confirm",
                    company_id=stage.company_id,
                )
                stage = YouTubeDeliveryService(session).review_private_stage(
                    stage_id=stage.id,
                    disposition=data.disposition,
                    reason=data.reason,
                    actor_id=actor.actor_id,
                )
                return YouTubePrivateStageRead.model_validate(stage)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/youtube-private-stages/{stage_id}/observe-publication",
        response_model=YouTubePrivateStageRead,
    )
    def observe_youtube_publication(
        stage_id: uuid.UUID,
        request: Request,
    ) -> YouTubePrivateStageRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                stage = YouTubeDeliveryService(session).require_stage(stage_id)
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=stage.company_id,
                )
                from app.services.youtube_delivery import YouTubePublicPublicationObserver

                observed = YouTubePublicPublicationObserver(
                    get_session_factory()
                ).reconcile(stage_id=stage.id)
                return YouTubePrivateStageRead.model_validate(observed)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/final-video-decisions/{decision_id}/public-publication-receipt",
        response_model=PublicPublicationReceiptRead,
    )
    def get_public_publication_receipt(
        decision_id: uuid.UUID,
        request: Request,
    ) -> PublicPublicationReceiptRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                decision = session.get(FinalVideoDecision, decision_id)
                if decision is None:
                    raise ValueError("FINAL_VIDEO_DECISION_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=decision.company_id,
                )
                receipt = session.scalar(
                    select(PublicPublicationReceipt).where(
                        PublicPublicationReceipt.final_video_decision_id
                        == decision.id
                    )
                )
                if receipt is None:
                    raise ValueError("PUBLIC_PUBLICATION_RECEIPT_NOT_FOUND")
                return PublicPublicationReceiptRead.model_validate(receipt)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/youtube-private-stages/{stage_id}/public-publication-receipt",
        response_model=PublicPublicationReceiptRead,
    )
    def get_stage_public_publication_receipt(
        stage_id: uuid.UUID,
        request: Request,
    ) -> PublicPublicationReceiptRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                stage = session.get(YouTubePrivateStage, stage_id)
                if stage is None:
                    raise ValueError("YOUTUBE_PRIVATE_STAGE_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=stage.company_id,
                )
                receipt = session.scalar(
                    select(PublicPublicationReceipt).where(
                        PublicPublicationReceipt.youtube_private_stage_id == stage.id
                    )
                )
                if receipt is None:
                    raise ValueError("PUBLIC_PUBLICATION_RECEIPT_NOT_FOUND")
                return PublicPublicationReceiptRead.model_validate(receipt)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/final-review-candidates/{candidate_id}/telegram-notifications",
        response_model=list[TelegramNotificationRead],
    )
    def list_candidate_telegram_notifications(
        candidate_id: uuid.UUID,
        request: Request,
    ) -> list[TelegramNotificationRead]:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                candidate = session.get(FinalReviewCandidate, candidate_id)
                if candidate is None:
                    raise ValueError("FINAL_REVIEW_CANDIDATE_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=candidate.company_id,
                )
                rows = list(
                    session.scalars(
                        select(TelegramDeliveryNotification)
                        .where(
                            TelegramDeliveryNotification.final_review_candidate_id
                            == candidate.id
                        )
                        .order_by(TelegramDeliveryNotification.created_at)
                    ).all()
                )
                return [
                    TelegramNotificationRead.model_validate(row) for row in rows
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/series-plans/{series_plan_id}/youtube-playlist-binding",
        response_model=YouTubeSeriesPlaylistBindingRead,
    )
    def create_youtube_series_playlist_binding(
        series_plan_id: uuid.UUID,
        data: YouTubeSeriesPlaylistBindingCreate,
        request: Request,
    ) -> YouTubeSeriesPlaylistBindingRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                plan = session.get(SeriesPlan, series_plan_id)
                if plan is None:
                    raise ValueError("SERIES_PLAN_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="publish.prepare",
                    company_id=plan.company_id,
                )
                binding = YouTubeDeliveryService(
                    session
                ).create_series_playlist_binding(
                    series_plan_id=plan.id,
                    publishing_credential_id=data.publishing_credential_id,
                    expected_title=data.expected_title,
                    expected_description=data.expected_description,
                )
                return YouTubeSeriesPlaylistBindingRead.model_validate(binding)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/youtube-series-episode-bindings/{episode_binding_id}/public-ordinal",
        response_model=YouTubeSeriesEpisodeBindingRead,
    )
    def bind_youtube_series_public_ordinal(
        episode_binding_id: uuid.UUID,
        data: YouTubeSeriesOrdinalBind,
        request: Request,
    ) -> YouTubeSeriesEpisodeBindingRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                row = session.get(YouTubeSeriesEpisodeBinding, episode_binding_id)
                if row is None:
                    raise ValueError("YOUTUBE_SERIES_EPISODE_BINDING_NOT_FOUND")
                plan = session.get(SeriesPlan, row.series_plan_id)
                if plan is None:
                    raise ValueError("SERIES_PLAN_NOT_FOUND")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="publish.prepare",
                    company_id=plan.company_id,
                )
                binding = YouTubeDeliveryService(session).bind_public_episode_ordinal(
                    episode_binding_id=row.id,
                    data=data,
                )
                return YouTubeSeriesEpisodeBindingRead.model_validate(binding)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
