from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, get_args

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Match

from app.contracts.events import AuditEnvelope
from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.db.session import session_scope
from app.services.audit import AuditService
from app.services.m11_1 import AUTH_COOKIE_NAME, AuthService


logger = logging.getLogger(__name__)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PUBLIC_MUTATIONS = frozenset({("POST", "/auth/login")})
AUTHENTICATED_ONLY_PERMISSION = "session.end"
SIDE_EFFECT_GET_PERMISSIONS = {
    "/auth/youtube/start": "publish.prepare",
    "/auth/youtube/callback": "publish.prepare",
    "/auth/google-drive/start": "publish.prepare",
    "/auth/google-drive/callback": "publish.prepare",
    "/media/local-retention-policy": "ops.manage",
}
PROTECTED_READ_PERMISSIONS = {
    "/credential-references/{credential_reference_id}": "provider.execute",
}
LOCALIZATION_PACKAGE_CREATE_ROUTES = frozenset(
    {
        "/video-projects/{video_project_id}/localized-subtitles",
        "/video-projects/{video_project_id}/localized-metadata",
    }
)


@dataclass(frozen=True, slots=True)
class PermissionRule:
    permission: str
    path_pattern: re.Pattern[str]
    methods: frozenset[str] | None = None

    def matches(self, method: str, route_path: str) -> bool:
        return (
            self.methods is None or method in self.methods
        ) and self.path_pattern.search(route_path) is not None


def _rule(
    permission: str,
    pattern: str,
    *,
    methods: Iterable[str] | None = None,
) -> PermissionRule:
    return PermissionRule(
        permission=permission,
        path_pattern=re.compile(pattern),
        methods=frozenset(methods) if methods is not None else None,
    )


PROTECTED_READ_RULES = (
    _rule(
        "production.read",
        r"^/(?:launch-(?:runs|policies)(?:/|$)|"
        r"channels/[^/]+/launch-policy$)",
        methods={"GET"},
    ),
    _rule(
        "production.read",
        r"^/editorial-(?:research-runs|idea-candidates)(?:/|$)",
        methods={"GET"},
    ),
    _rule(
        "production.read",
        r"^/(?:project-admission-decisions/[^/]+|"
        r"production-runs/[^/]+|"
        r"video-projects/[^/]+/long-production)$",
        methods={"GET"},
    ),
    _rule("production.read", r"^/production-workflows(?:/|$)", methods={"GET"}),
    _rule(
        "production.read",
        r"^/operator-planning/catalog$",
        methods={"GET"},
    ),
    _rule(
        "production.read",
        r"^/(?:operator-cockpit|video-projects/[^/]+/operator-cockpit|"
        r"channels/[^/]+/operator-cockpit)$",
        methods={"GET"},
    ),
    _rule(
        "production.read",
        r"^/(?:final-review-candidates/[^/]+(?:/(?:media|thumbnail))?|"
        r"final-video-decisions/[^/]+|"
        r"human-upload-tasks/[^/]+/v2|"
        r"manual-publish-confirmations/[^/]+/v2|"
        r"uploaded-videos/[^/]+/v2)$",
        methods={"GET"},
    ),
)


# First match wins. Domain-specific approval and execution paths intentionally
# precede broad resource families.
PERMISSION_RULES = (
    _rule(
        "production.start",
        r"^/(?:channels/[^/]+/launch-runs$|"
        r"launch-runs(?:/|$))",
    ),
    _rule(
        "channel.manage",
        r"^/(?:channels/[^/]+/launch-policies$|"
        r"launch-policies(?:/|$))",
    ),
    _rule(
        "production.cancel",
        r"^/(?:production-runs|production-workflows)/"
        r"[^/]+/(?:cancel|abort|stop)$",
    ),
    _rule(
        "ops.manage",
        r"^/production-workflows/dead-letters/[^/]+/retry$",
    ),
    _rule(
        "memory.promote",
        r"^/(?:memory(?:/|$)|learning-loop/promotions(?:/|$)|quality-delta-attributions/run$)",
    ),
    _rule(
        "learning.review",
        r"^/(?:learning(?:-|/|$)|post-publish-health-runs(?:/|$)|recovery-proposals(?:/|$))",
    ),
    _rule(
        "publish.confirm",
        r"^/(?:manual-publish-confirmations(?:/|$)|upload-tasks(?:/|$)|"
        r"human-upload-tasks/[^/]+/(?:manual-publish-confirmations|cancel)$|"
        r"uploaded-videos/[^/]+/verify$)",
    ),
    _rule(
        "publish.prepare",
        r"^/(?:publish-handoffs(?:/|$)|video-packages/[^/]+/upload-task$|human-upload-tasks(?:/|$))",
    ),
    _rule(
        "analytics.sync",
        r"^/(?:analytics(?:-|/|$)|uploaded-videos/[^/]+/youtube/(?:public-sync|owner-analytics-sync)$)",
    ),
    _rule(
        "review.final_decide",
        r"(?:^/(?:approval-decisions|review-findings|revision-requests)$|"
        r"^/final-review-candidates/[^/]+/decisions$|"
        r"^/channels/[^/]+/target-market-draft/approve$|"
        r"^/originality/format-identities/[^/]+/(?:approve|reject)$|"
        r"^/packaging-proposed-patches/[^/]+/(?:approve|reject|request-changes|apply)$|"
        r"^/profile-versions/[^/]+/(?:approve|reject)$)",
    ),
    _rule(
        "provider.execute",
        r"^/(?:providers(?:/|$)|credential-references(?:/|$)|quota-(?:accounts|events)(?:/|$)|cost-events(?:/|$)|"
        r"budget-(?:policies|gates)(?:/|$)|provider-(?:attempts|jobs|boundary|idempotency-keys)(?:/|$)|"
        r"paid-render-approvals(?:/|$)|paid-provider-call-ledger(?:/|$)|paid-attempt-limit-records(?:/|$)|"
        r"cost-estimates(?:/|$)|llm-router(?:/|$)|media-render-routing(?:/|$)|media-provider-gates(?:/|$)|"
        r"ai-hero-assets/[^/]+/generate$|integrations/providers/[^/]+/smoke$)",
    ),
    _rule(
        "ops.manage",
        r"^/(?:integrations/readiness/run$|component-health(?:/|$)|system-health(?:/|$)|retry-policies(?:/|$)|"
        r"dead-letter-jobs(?:/|$)|ops-incidents(?:/|$)|manual-actions(?:/|$)|prompt-registry(?:/|$)|"
        r"media/(?:offload-jobs|local-cleanup)(?:/|$))",
    ),
    _rule(
        "channel.manage",
        r"^/(?:channels(?:/|$)|companies(?:/|$)|channel-init-drafts(?:/|$)|profile-versions(?:/|$)|"
        r"policy-snapshots(?:/|$))",
    ),
    _rule(
        "production.start",
        r"^/(?:operator-planning/(?:prepare|launch|long-form/launch)$|"
        r"production-runs(?:/|$)|render-revisions(?:/|$)|"
        r"production-workflows/[^/]+/resume$|"
        r"video-packages(?:/|$)|production-packages(?:/|$)|"
        r"video-projects/[^/]+/(?:production-workflow/start|long-production(?:/run)?|long-form-render-package|"
        r"ai-hero-assets/plan|thumbnail-variants/plan)$|"
        r"revision-requests/[^/]+/resolve$|"
        r"media-qc/run$|accessibility-qc/run$|"
        r"media-render-routing/decide$)",
    ),
    _rule(
        "editorial.manage",
        r"^/(?:video-projects(?:/|$)|content-categories(?:/|$)|category-creative-digests(?:/|$)|"
        r"character-(?:profiles|versions|image-branches|reference-assets|reference-asset-packs|bindings)(?:/|$)|"
        r"voice-profiles(?:/|$)|artifacts(?:/|$)|artifact-versions(?:/|$)|review-tasks(?:/|$)|"
        r"review-findings(?:/|$)|revision-requests(?:/|$)|gates(?:/|$)|policy-(?:catalogs|versions|source-refs|"
        r"change-records|revalidation-batches)(?:/|$)|editorial-calendar-slots(?:/|$)|"
        r"search-demand-evidence(?:/|$)|context(?:/|$)|channel-state-packs(?:/|$)|"
        r"editorial-research-runs(?:/|$)|editorial-idea-candidates(?:/|$)|"
        r"idea-market-preflights(?:/|$)|project-admission-decisions(?:/|$)|"
        r"series-(?:plans|runs)(?:/|$)|"
        r"reusable-artifacts(?:/|$)|asset-reuse-index(?:/|$)|"
        r"proxy-preview-artifact-flags(?:/|$)|"
        r"originality/format-identities$|localized-(?:subtitle|metadata)-packages(?:/|$)|"
        r"media-provider-(?:roles|capabilities|budgets)(?:/|$)|thumbnail-variants(?:/|$))",
    ),
)


def is_protected_route(method: str, route_path: str) -> bool:
    method = method.upper()
    return (
        method in UNSAFE_METHODS and (method, route_path) not in PUBLIC_MUTATIONS
    ) or (
        method == "GET"
        and (
            route_path in SIDE_EFFECT_GET_PERMISSIONS
            or route_path in PROTECTED_READ_PERMISSIONS
            or any(rule.matches(method, route_path) for rule in PROTECTED_READ_RULES)
        )
    )


def permission_for_route(method: str, route_path: str) -> str | None:
    method = method.upper()
    if not is_protected_route(method, route_path):
        return None
    if method == "POST" and route_path == "/auth/logout":
        return AUTHENTICATED_ONLY_PERMISSION
    if method == "GET":
        exact = SIDE_EFFECT_GET_PERMISSIONS.get(
            route_path
        ) or PROTECTED_READ_PERMISSIONS.get(route_path)
        if exact is not None:
            return exact
        for rule in PROTECTED_READ_RULES:
            if rule.matches(method, route_path):
                return rule.permission
        return None
    for rule in PERMISSION_RULES:
        if rule.matches(method, route_path):
            return rule.permission
    return None


def uncovered_protected_routes(application: FastAPI) -> list[tuple[str, str]]:
    uncovered: list[tuple[str, str]] = []
    for route in application.routes:
        route_path = getattr(route, "path", None)
        if route_path is None:
            continue
        for method in getattr(route, "methods", set()) or set():
            if (
                is_protected_route(method, route_path)
                and permission_for_route(method, route_path) is None
            ):
                uncovered.append((method, route_path))
    return sorted(uncovered)


class MutationSecurityMiddleware(BaseHTTPMiddleware):
    """Authenticate, authorize, bind actor identity, and audit protected mutations."""

    def __init__(self, app: Any, *, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        route_path, path_params, matched_route = _matched_route(request)
        if route_path is None or not is_protected_route(request.method, route_path):
            return await call_next(request)

        permission = permission_for_route(request.method, route_path)
        if permission is None:
            return JSONResponse(
                status_code=403,
                content={"detail": "protected route has no permission mapping"},
            )

        try:
            with session_scope() as session:
                actor = AuthService(session, self.settings).actor_context(
                    request.cookies.get(AUTH_COOKIE_NAME)
                )
        except ForbiddenError:
            return JSONResponse(
                status_code=401,
                content={"detail": "authentication required"},
            )

        permission = await _effective_mutation_permission(
            request,
            route_path=route_path,
            default_permission=permission,
        )
        if permission != AUTHENTICATED_ONLY_PERMISSION and not actor.has_permission(
            permission
        ):
            return JSONResponse(status_code=403, content={"detail": "forbidden"})

        request.state.actor = actor
        request.state.permission = permission
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        try:
            replacement = await _authoritative_actor_body(
                request,
                actor,
                declared_fields=_declared_body_fields(matched_route),
            )
        except PublicSystemWorkerForgeryError:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "system worker identity cannot be supplied publicly"
                },
            )
        if replacement is not None:
            request._body = replacement

            async def receive() -> dict[str, Any]:
                return {
                    "type": "http.request",
                    "body": replacement,
                    "more_body": False,
                }

            request._receive = receive

        try:
            _append_authenticated_audit(
                actor=actor,
                permission=permission,
                request_id=request_id,
                method=request.method,
                route_path=route_path,
                path_params=path_params,
                status_code=None,
            )
        except Exception:
            logger.exception("failed to persist authenticated mutation authorization")
            return JSONResponse(
                status_code=500,
                content={"detail": "mutation authorization audit persistence failed"},
                headers={"X-Request-ID": request_id},
            )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if response.status_code < 400:
            try:
                _append_authenticated_audit(
                    actor=actor,
                    permission=permission,
                    request_id=request_id,
                    method=request.method,
                    route_path=route_path,
                    path_params=path_params,
                    status_code=response.status_code,
                )
            except Exception:
                logger.exception("failed to persist authenticated mutation audit")
                return JSONResponse(
                    status_code=500,
                    content={"detail": "mutation audit persistence failed"},
                    headers={"X-Request-ID": request_id},
                )
        return response


def actor_from_request(request: Request) -> ActorContext:
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, ActorContext):
        raise RuntimeError("trusted actor context is unavailable")
    return actor


async def _effective_mutation_permission(
    request: Request,
    *,
    route_path: str,
    default_permission: str,
) -> str:
    """Select review authority when a localization create carries a final state."""

    if route_path not in LOCALIZATION_PACKAGE_CREATE_ROUTES:
        return default_permission
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json":
        return default_permission
    raw_body = await request.body()
    if not raw_body:
        return default_permission
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return default_permission
    if not isinstance(payload, dict):
        return default_permission
    if route_path.endswith("/localized-subtitles"):
        final_review = payload.get("translation_status") in {
            "APPROVED",
            "PASS",
        } or payload.get("human_review_status") in {"APPROVED", "NOT_REQUIRED", "PASS"}
    else:
        final_review = payload.get("human_review_status") in {"APPROVED", "PASS"}
    return "review.final_decide" if final_review else default_permission


def _matched_route(
    request: Request,
) -> tuple[str | None, dict[str, Any], Any | None]:
    for route in request.app.router.routes:
        match, child_scope = route.matches(request.scope)
        if match is Match.FULL:
            return (
                getattr(route, "path", None),
                dict(child_scope.get("path_params") or {}),
                route,
            )
    return None, {}, None


async def _authoritative_actor_body(
    request: Request,
    actor: ActorContext,
    *,
    declared_fields: set[str],
) -> bytes | None:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json":
        return None
    raw_body = await request.body()
    if not raw_body:
        return None
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    changed = _add_missing_actor_claims(payload, actor, declared_fields)
    if _replace_actor_claims(payload, actor, declared_fields):
        changed = True
    if not changed:
        return None
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


_CANONICAL_ACTOR_FIELDS = frozenset(
    {
        "actor_user_id",
        "approved_by",
        "approved_by_user_id",
        "confirmed_by_user_id",
        "created_by",
        "created_by_user_id",
        "decided_by",
        "decided_by_user_id",
        "edited_by_user_id",
        "imported_by_user_id",
        "requested_by_user_id",
        "rejected_by",
        "reviewer_user_id",
    }
)
_OPERATOR_ACTOR_FIELDS = frozenset({"operator_user_id"})


class PublicSystemWorkerForgeryError(ValueError):
    pass


def _declared_body_fields(route: Any | None) -> set[str]:
    declared: set[str] = set()
    dependant = getattr(route, "dependant", None)
    for body_param in getattr(dependant, "body_params", ()) or ():
        annotation = getattr(
            getattr(body_param, "field_info", None), "annotation", None
        )
        for model_type in _pydantic_model_types(annotation):
            declared.update(model_type.model_fields)
    return declared


def _pydantic_model_types(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    models: list[type[BaseModel]] = []
    for nested in get_args(annotation):
        models.extend(_pydantic_model_types(nested))
    return models


def _add_missing_actor_claims(
    payload: Any,
    actor: ActorContext,
    declared_fields: set[str],
) -> bool:
    if not isinstance(payload, dict):
        return False
    changed = False
    for field in _CANONICAL_ACTOR_FIELDS.intersection(declared_fields):
        if payload.get(field) is None:
            payload[field] = str(actor.actor_id)
            changed = True
    for field in _OPERATOR_ACTOR_FIELDS.intersection(declared_fields):
        if payload.get(field) is None:
            payload[field] = str(actor.operator_user_id or actor.actor_id)
            changed = True
    if "actor_role" in declared_fields and payload.get("actor_role") is None:
        payload["actor_role"] = actor.actor_role
        changed = True
    return changed


def _replace_actor_claims(
    value: Any,
    actor: ActorContext,
    declared_fields: set[str],
) -> bool:
    if isinstance(value, list):
        changed = False
        for item in value:
            if _replace_actor_claims(item, actor, set()):
                changed = True
        return changed
    if not isinstance(value, dict):
        return False
    changed = False
    actor_type = value.get("actor_type")
    if isinstance(actor_type, str) and actor_type.upper() == "SYSTEM_WORKER":
        raise PublicSystemWorkerForgeryError
    if value.get("actor_role") is not None:
        if value["actor_role"] != actor.actor_role:
            value["actor_role"] = actor.actor_role
            changed = True
    for field in _CANONICAL_ACTOR_FIELDS.intersection(value):
        if value.get(field) is not None and str(value[field]) != str(actor.actor_id):
            value[field] = str(actor.actor_id)
            changed = True
    for field in _OPERATOR_ACTOR_FIELDS.intersection(value):
        authoritative = str(actor.operator_user_id or actor.actor_id)
        if value.get(field) is not None and str(value[field]) != authoritative:
            value[field] = authoritative
            changed = True
    for nested in value.values():
        if _replace_actor_claims(nested, actor, set()):
            changed = True
    return changed


def _append_authenticated_audit(
    *,
    actor: ActorContext,
    permission: str,
    request_id: str,
    method: str,
    route_path: str,
    path_params: dict[str, Any],
    status_code: int | None,
) -> None:
    target_id = next(
        (
            parsed
            for raw in path_params.values()
            if (parsed := _as_uuid(raw)) is not None
        ),
        None,
    )
    target_type = next(
        (
            segment
            for segment in route_path.split("/")
            if segment and not segment.startswith("{")
        ),
        "http_mutation",
    ).replace("-", "_")
    with session_scope() as session:
        AuditService(session).append(
            AuditEnvelope(
                actor_type=actor.actor_type.value,
                actor_id=actor.actor_id,
                action=(
                    "security.authenticated_mutation"
                    if status_code is not None
                    else "security.authenticated_mutation_authorized"
                ),
                target_type=target_type,
                target_id=target_id,
                reason_code=(
                    "AUTHENTICATED_MUTATION_COMPLETED"
                    if status_code is not None
                    else "AUTHENTICATED_MUTATION_AUTHORIZED"
                ),
                correlation_id=request_id[:160],
                payload={
                    "authenticated_actor_id": str(actor.actor_id),
                    "authenticated_actor_role": actor.actor_role,
                    "operator_user_id": (
                        str(actor.operator_user_id)
                        if actor.operator_user_id is not None
                        else None
                    ),
                    "permission": permission,
                    "request_id": request_id,
                    "method": method,
                    "route": route_path,
                    "path_params": {
                        key: str(value) for key, value in path_params.items()
                    },
                    "old_state": None,
                    "new_state": (
                        {"http_status": status_code}
                        if status_code is not None
                        else {"authorization": "GRANTED"}
                    ),
                },
            )
        )


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
