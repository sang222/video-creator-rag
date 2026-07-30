"""Authenticated Phase 5 final-review and canonical manual-publish API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.production_publish import (
    FinalReviewCandidateRead,
    FinalVideoDecisionCreate,
    FinalVideoDecisionRead,
    FinalVideoDecisionResult,
    HumanUploadTaskCancelV2,
    HumanUploadTaskReadV2,
    HumanUploadTaskStartV2,
    ManualPublishConfirmationCreateV2,
    ManualPublishConfirmationReadV2,
    ManualPublishCorrectionV2,
    ManualPublishVerificationResultV2,
    ManualPublishVerificationV2,
    UploadedVideoReadV2,
)
from app.db.session import session_scope
from app.services.production_publish import ProductionPublishService
from app.services.security_boundary import actor_from_request


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/final-review-candidates/{candidate_id}",
        response_model=FinalReviewCandidateRead,
    )
    def get_final_review_candidate(
        candidate_id: uuid.UUID,
        request: Request,
    ) -> FinalReviewCandidateRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                candidate = ProductionPublishService(session).require_candidate(
                    candidate_id,
                    actor=actor,
                )
                return FinalReviewCandidateRead.model_validate(candidate)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/final-review-candidates/{candidate_id}/media")
    def get_final_review_candidate_media(
        candidate_id: uuid.UUID,
        request: Request,
        download: bool = Query(default=False),
    ) -> FileResponse:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                media = ProductionPublishService(
                    session
                ).resolve_verified_candidate_media(
                    candidate_id=candidate_id,
                    actor=actor,
                    media_kind="video",
                )
            return FileResponse(
                media.path,
                media_type=media.media_type,
                filename=media.file_name,
                content_disposition_type="attachment" if download else "inline",
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                    "X-VCOS-Content-SHA256": media.checksum_sha256,
                },
            )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/final-review-candidates/{candidate_id}/thumbnail")
    def get_final_review_candidate_thumbnail(
        candidate_id: uuid.UUID,
        request: Request,
    ) -> FileResponse:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                media = ProductionPublishService(
                    session
                ).resolve_verified_candidate_media(
                    candidate_id=candidate_id,
                    actor=actor,
                    media_kind="thumbnail",
                )
            return FileResponse(
                media.path,
                media_type=media.media_type,
                filename=media.file_name,
                content_disposition_type="inline",
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                    "X-VCOS-Content-SHA256": media.checksum_sha256,
                },
            )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/final-review-candidates/{candidate_id}/decisions",
        response_model=FinalVideoDecisionResult,
    )
    def decide_final_video(
        candidate_id: uuid.UUID,
        data: FinalVideoDecisionCreate,
        request: Request,
    ) -> FinalVideoDecisionResult:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionPublishService(session).decide(
                    candidate_id=candidate_id,
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/final-video-decisions/{decision_id}",
        response_model=FinalVideoDecisionRead,
    )
    def get_final_video_decision(
        decision_id: uuid.UUID,
        request: Request,
    ) -> FinalVideoDecisionRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                decision = ProductionPublishService(session).require_decision(
                    decision_id,
                    actor=actor,
                )
                return FinalVideoDecisionRead.model_validate(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/human-upload-tasks/{task_id}/v2",
        response_model=HumanUploadTaskReadV2,
    )
    def get_v2_upload_task(
        task_id: uuid.UUID,
        request: Request,
    ) -> HumanUploadTaskReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                task = ProductionPublishService(session).require_upload_task(
                    task_id,
                    actor=actor,
                )
                return HumanUploadTaskReadV2.model_validate(task)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/human-upload-tasks/{task_id}/start",
        response_model=HumanUploadTaskReadV2,
    )
    def start_v2_upload_task(
        task_id: uuid.UUID,
        data: HumanUploadTaskStartV2,
        request: Request,
    ) -> HumanUploadTaskReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                task = ProductionPublishService(session).start_upload_task(
                    task_id=task_id,
                    data=data,
                    actor=actor,
                )
                return HumanUploadTaskReadV2.model_validate(task)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/human-upload-tasks/{task_id}/cancel",
        response_model=HumanUploadTaskReadV2,
    )
    def cancel_v2_upload_task(
        task_id: uuid.UUID,
        data: HumanUploadTaskCancelV2,
        request: Request,
    ) -> HumanUploadTaskReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                task = ProductionPublishService(session).cancel_upload_task(
                    task_id=task_id,
                    data=data,
                    actor=actor,
                )
                return HumanUploadTaskReadV2.model_validate(task)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/human-upload-tasks/{task_id}/manual-publish-confirmations",
        response_model=ManualPublishConfirmationReadV2,
    )
    def submit_v2_manual_publish_confirmation(
        task_id: uuid.UUID,
        data: ManualPublishConfirmationCreateV2,
        request: Request,
    ) -> ManualPublishConfirmationReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                confirmation = ProductionPublishService(session).submit_confirmation(
                    task_id=task_id,
                    data=data,
                    actor=actor,
                )
                return ManualPublishConfirmationReadV2.model_validate(confirmation)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/manual-publish-confirmations/{confirmation_id}/v2",
        response_model=ManualPublishConfirmationReadV2,
    )
    def get_v2_manual_publish_confirmation(
        confirmation_id: uuid.UUID,
        request: Request,
    ) -> ManualPublishConfirmationReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                confirmation = ProductionPublishService(session).require_confirmation(
                    confirmation_id,
                    actor=actor,
                )
                return ManualPublishConfirmationReadV2.model_validate(confirmation)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/manual-publish-confirmations/{confirmation_id}/corrections",
        response_model=ManualPublishConfirmationReadV2,
    )
    def correct_v2_manual_publish_confirmation(
        confirmation_id: uuid.UUID,
        data: ManualPublishCorrectionV2,
        request: Request,
    ) -> ManualPublishConfirmationReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                confirmation = ProductionPublishService(session).apply_correction(
                    confirmation_id=confirmation_id,
                    data=data,
                    actor=actor,
                )
                return ManualPublishConfirmationReadV2.model_validate(confirmation)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/manual-publish-confirmations/{confirmation_id}/verification",
        response_model=ManualPublishVerificationResultV2,
    )
    def verify_v2_manual_publish_confirmation(
        confirmation_id: uuid.UUID,
        data: ManualPublishVerificationV2,
        request: Request,
    ) -> ManualPublishVerificationResultV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionPublishService(session).verify_confirmation(
                    confirmation_id=confirmation_id,
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/uploaded-videos/{uploaded_video_id}/v2",
        response_model=UploadedVideoReadV2,
    )
    def get_v2_uploaded_video(
        uploaded_video_id: uuid.UUID,
        request: Request,
    ) -> UploadedVideoReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                uploaded = ProductionPublishService(session).require_uploaded_video(
                    uploaded_video_id,
                    actor=actor,
                )
                return UploadedVideoReadV2.model_validate(uploaded)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
