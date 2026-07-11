import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.ofv0 import (
    FormatIdentityContractDraftRequest,
    FormatIdentityContractRead,
    FormatIdentityDecisionRequest,
    OriginalityReviewRead,
)
from app.core.config import get_settings
from app.core.errors import ForbiddenError
from app.db.session import session_scope
from app.services.m11_1 import AUTH_COOKIE_NAME, AuthService
from app.services.ofv0 import FormatIdentityContractService, OriginalityReviewReadModelBuilder


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/originality/format-identities", response_model=FormatIdentityContractRead)
    def draft_format_identity(data: FormatIdentityContractDraftRequest) -> FormatIdentityContractRead:
        try:
            with session_scope() as session:
                return FormatIdentityContractService(session).draft(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/originality/format-identities/{contract_id}/approve", response_model=FormatIdentityContractRead)
    def approve_format_identity(contract_id: uuid.UUID, data: FormatIdentityDecisionRequest, request: Request) -> FormatIdentityContractRead:
        try:
            with session_scope() as session:
                _require_operator(session, request)
                return FormatIdentityContractService(session).approve(contract_id, decided_by=data.decided_by, rationale=data.rationale)
        except ForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bạn chưa có quyền duyệt Format Identity.") from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/originality/format-identities/{contract_id}/reject", response_model=FormatIdentityContractRead)
    def reject_format_identity(contract_id: uuid.UUID, data: FormatIdentityDecisionRequest, request: Request) -> FormatIdentityContractRead:
        try:
            with session_scope() as session:
                _require_operator(session, request)
                return FormatIdentityContractService(session).reject(contract_id, decided_by=data.decided_by, rationale=data.rationale)
        except ForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bạn chưa có quyền duyệt Format Identity.") from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-packages/{package_id}/originality-review", response_model=OriginalityReviewRead)
    def originality_review(package_id: uuid.UUID) -> OriginalityReviewRead:
        try:
            with session_scope() as session:
                return OriginalityReviewReadModelBuilder(session).build(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router


def _require_operator(session, request: Request) -> None:
    auth = AuthService(session, get_settings()).current_user(request.cookies.get(AUTH_COOKIE_NAME))
    if not auth.auth_enabled:
        return
    if auth.user is None or auth.user.role == "READ_ONLY":
        raise ForbiddenError("operator approval required")
