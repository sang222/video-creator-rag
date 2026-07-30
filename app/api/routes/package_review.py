from fastapi import APIRouter

from app.api.routes.imports import (
    AUTH_COOKIE_NAME,
    AuthService,
    BackfillUploadedVideoRequest,
    BackfillUploadedVideoResult,
    FirstScriptedVideoPackageAgentRunsRead,
    FirstScriptedVideoPackageRead,
    FirstScriptedVideoPackageRequest,
    FirstScriptedVideoPackageReviewRead,
    FirstScriptedVideoPackageService,
    HumanUploadTaskLedgerRead,
    M122SPreflightRead,
    PackagingApprovedPatchApplyAndRecheckResultRead,
    PackagingApprovedPatchApplyAndRecheckService,
    PackagingGateRerunRecordRead,
    PackagingGateRerunService,
    PackagingHandoffReadService,
    PackagingHandoffSnapshotRead,
    PackagingPatchApplyRunRead,
    PackagingPatchApplyService,
    PackagingPatchApprovalDecisionRead,
    PackagingPatchApprovalService,
    PackagingPatchDecisionRequest,
    PackagingReviewQueueRead,
    PackagingReviewQueueService,
    PublishHandoffLedgerService,
    Request,
    VideoGenerationBoundaryRead,
    ForbiddenError,
    get_settings,
    session_scope,
    status,
    HTTPException,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)
from app.services.security_boundary import actor_from_request


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/video-packages/first-scripted", response_model=FirstScriptedVideoPackageRead
    )
    def create_first_scripted_video_package(
        data: FirstScriptedVideoPackageRequest,
    ) -> FirstScriptedVideoPackageRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).create(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-packages/rehearse-full", response_model=FirstScriptedVideoPackageRead
    )
    def rehearse_full_video_package(
        data: FirstScriptedVideoPackageRequest,
    ) -> FirstScriptedVideoPackageRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).rehearse_full(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/rehearse-full/preflight", response_model=M122SPreflightRead
    )
    def preflight_rehearse_full_video_package(
        channel_id: uuid.UUID | None = None,
    ) -> M122SPreflightRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(
                    session
                ).preflight_full_rehearsal(channel_id=channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/{package_id}", response_model=FirstScriptedVideoPackageRead
    )
    def get_first_scripted_video_package(
        package_id: uuid.UUID,
    ) -> FirstScriptedVideoPackageRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).get(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/{package_id}/review",
        response_model=FirstScriptedVideoPackageReviewRead,
    )
    def get_first_scripted_video_package_review(
        package_id: uuid.UUID,
    ) -> FirstScriptedVideoPackageReviewRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).review(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/{package_id}/packaging-handoff",
        response_model=PackagingHandoffSnapshotRead,
    )
    def get_first_scripted_video_package_packaging_handoff(
        package_id: uuid.UUID,
    ) -> PackagingHandoffSnapshotRead:
        try:
            with session_scope() as session:
                return PackagingHandoffReadService(session).build(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/{package_id}/packaging-review-queue",
        response_model=PackagingReviewQueueRead,
    )
    def get_packaging_review_queue(package_id: uuid.UUID) -> PackagingReviewQueueRead:
        try:
            with session_scope() as session:
                return PackagingReviewQueueService(session).read(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-packages/{package_id}/packaging-review-queue/build-from-gates",
        response_model=PackagingReviewQueueRead,
    )
    def build_packaging_review_queue_from_gates(
        package_id: uuid.UUID,
    ) -> PackagingReviewQueueRead:
        try:
            with session_scope() as session:
                return PackagingReviewQueueService(session).build_from_gates(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-packages/{package_id}/apply-approved-changes-and-recheck",
        response_model=PackagingApprovedPatchApplyAndRecheckResultRead,
    )
    def apply_approved_changes_and_recheck_package(
        package_id: uuid.UUID,
        request: Request,
    ) -> PackagingApprovedPatchApplyAndRecheckResultRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                _require_operator_review_action(session, request)
                return PackagingApprovedPatchApplyAndRecheckService(
                    session
                ).apply_and_recheck(
                    package_id,
                    actor_user_id=actor.actor_id,
                )
        except ForbiddenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bạn chưa có quyền thực hiện thao tác này.",
            ) from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/packaging-proposed-patches/{patch_id}/approve",
        response_model=PackagingPatchApprovalDecisionRead,
    )
    def approve_packaging_proposed_patch(
        patch_id: uuid.UUID,
        request: Request,
        data: PackagingPatchDecisionRequest | None = None,
    ) -> PackagingPatchApprovalDecisionRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                payload = data or PackagingPatchDecisionRequest()
                return PackagingPatchApprovalService(session).approve(
                    patch_id,
                    decided_by=str(actor.actor_id),
                    rationale=payload.rationale,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/packaging-proposed-patches/{patch_id}/reject",
        response_model=PackagingPatchApprovalDecisionRead,
    )
    def reject_packaging_proposed_patch(
        patch_id: uuid.UUID,
        request: Request,
        data: PackagingPatchDecisionRequest | None = None,
    ) -> PackagingPatchApprovalDecisionRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                payload = data or PackagingPatchDecisionRequest()
                return PackagingPatchApprovalService(session).reject(
                    patch_id,
                    decided_by=str(actor.actor_id),
                    rationale=payload.rationale,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/packaging-proposed-patches/{patch_id}/request-changes",
        response_model=PackagingPatchApprovalDecisionRead,
    )
    def request_changes_packaging_proposed_patch(
        patch_id: uuid.UUID,
        request: Request,
        data: PackagingPatchDecisionRequest | None = None,
    ) -> PackagingPatchApprovalDecisionRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                payload = data or PackagingPatchDecisionRequest()
                return PackagingPatchApprovalService(session).request_changes(
                    patch_id,
                    decided_by=str(actor.actor_id),
                    rationale=payload.rationale,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/packaging-proposed-patches/{patch_id}/apply",
        response_model=PackagingPatchApplyRunRead,
    )
    def apply_packaging_proposed_patch(
        patch_id: uuid.UUID,
        request: Request,
    ) -> PackagingPatchApplyRunRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return PackagingPatchApplyService(session).apply(
                    patch_id,
                    actor_user_id=actor.actor_id,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-packages/{package_id}/rerun-packaging-gates",
        response_model=PackagingGateRerunRecordRead,
    )
    def rerun_packaging_gates(package_id: uuid.UUID) -> PackagingGateRerunRecordRead:
        try:
            with session_scope() as session:
                return PackagingGateRerunService(session).rerun_for_package(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/{package_id}/agent-runs",
        response_model=FirstScriptedVideoPackageAgentRunsRead,
    )
    def get_first_scripted_video_package_agent_runs(
        package_id: uuid.UUID,
    ) -> FirstScriptedVideoPackageAgentRunsRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).agent_runs(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-packages/{package_id}/generation-boundary",
        response_model=VideoGenerationBoundaryRead,
    )
    def get_video_generation_boundary(
        package_id: uuid.UUID,
    ) -> VideoGenerationBoundaryRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).generation_boundary(
                    package_id
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-packages/{package_id}/upload-task",
        response_model=HumanUploadTaskLedgerRead,
    )
    def create_upload_task_from_video_package(
        package_id: uuid.UUID,
        request: Request,
    ) -> HumanUploadTaskLedgerRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return PublishHandoffLedgerService(
                    session
                ).create_upload_task_from_package(
                    package_id,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/upload-tasks/{task_id}/start", response_model=HumanUploadTaskLedgerRead
    )
    def start_human_upload_task(
        task_id: uuid.UUID,
        request: Request,
    ) -> HumanUploadTaskLedgerRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return PublishHandoffLedgerService(session).start_upload_task(
                    task_id,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/upload-tasks/{task_id}/backfill-uploaded-video",
        response_model=BackfillUploadedVideoResult,
    )
    def backfill_human_uploaded_video(
        task_id: uuid.UUID,
        data: BackfillUploadedVideoRequest,
        request: Request,
    ) -> BackfillUploadedVideoResult:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return PublishHandoffLedgerService(session).backfill_uploaded_video(
                    task_id=task_id,
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router


def _require_operator_review_action(session, request: Request) -> None:
    auth = AuthService(session, get_settings()).current_user(
        request.cookies.get(AUTH_COOKIE_NAME)
    )
    if not auth.auth_enabled:
        return
    if auth.user is None or auth.user.role == "READ_ONLY":
        raise ForbiddenError("Bạn chưa có quyền thực hiện thao tác này.")
