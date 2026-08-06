from fastapi import APIRouter

from app.api.routes.imports import (
    AUTH_COOKIE_NAME,
    AuthLoginRequest,
    AuthService,
    AuthSessionRead,
    ForbiddenError,
    HTTPException,
    Request,
    Response,
    check_database,
    session_scope,
    status,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)
from app.services.runtime_migration_guard import RuntimeMigrationGuard



def create_router(settings) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        try:
            check_database(settings.database_url)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            ) from exc
        with session_scope() as session:
            migration = RuntimeMigrationGuard(session).inspect()
        return {
            "status": "ok" if migration.ready else "blocked",
            "app": settings.app_name,
            "database": "ok",
            "migration_current": migration.current_revision or "missing",
            "migration_required": migration.required_revision,
            "migration_reason_code": migration.reason_code or "NONE",
        }

    @router.post("/auth/login", response_model=AuthSessionRead)
    def auth_login(data: AuthLoginRequest, response: Response) -> AuthSessionRead:
        try:
            with session_scope() as session:
                auth_payload, token, expires_at = AuthService(session, settings).login(email=data.email, password=data.password)
                response.set_cookie(
                    AUTH_COOKIE_NAME,
                    token,
                    httponly=True,
                    secure=False,
                    samesite="lax",
                    expires=expires_at,
                    max_age=settings.auth_session_ttl_hours * 60 * 60,
                    path="/",
                )
                return auth_payload
        except ForbiddenError as exc:
            # The login transaction is rolled back with the rejected
            # credential attempt. Persist its audit in a separate,
            # successful transaction so failures cannot disappear.
            with session_scope() as audit_session:
                AuthService(audit_session, settings).record_failed_login(
                    email=data.email
                )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email hoặc mật khẩu không đúng.") from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/auth/logout")
    def auth_logout(request: Request, response: Response) -> dict[str, str]:
        try:
            with session_scope() as session:
                AuthService(session, settings).logout(request.cookies.get(AUTH_COOKIE_NAME))
                response.delete_cookie(AUTH_COOKIE_NAME, path="/")
                return {"status": "ok", "message": "Đăng xuất thành công."}
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/auth/me", response_model=AuthSessionRead)
    def auth_me(request: Request) -> AuthSessionRead:
        try:
            with session_scope() as session:
                return AuthService(session, settings).current_user(request.cookies.get(AUTH_COOKIE_NAME))
        except ForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập đã hết hạn.") from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
