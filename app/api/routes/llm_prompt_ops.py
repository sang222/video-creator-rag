from fastapi import APIRouter

from app.api.routes.imports import (
    LLMRouteRequest,
    LLMRouteResponse,
    LLMRouterConfigLoader,
    LLMRouterLaneRead,
    LLMRouterProfileRead,
    LLMRouterService,
    LLMRouterSmokeTestRead,
    LLMRouterSmokeTestRequest,
    PromptEvaluationRunRead,
    PromptOutputValidationRequest,
    PromptOutputValidationResult,
    PromptRegistryService,
    PromptRegistrySyncSummary,
    PromptRenderRequest,
    PromptRenderResult,
    session_scope,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/llm-router/profiles", response_model=list[LLMRouterProfileRead])
    def list_llm_router_profiles() -> list[LLMRouterProfileRead]:
        try:
            with session_scope() as session:
                profiles = LLMRouterConfigLoader(session).list_profiles()
                return [LLMRouterProfileRead.model_validate(profile) for profile in profiles]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/llm-router/profiles/{profile_key}", response_model=LLMRouterProfileRead)
    def get_llm_router_profile(profile_key: str) -> LLMRouterProfileRead:
        try:
            with session_scope() as session:
                return LLMRouterProfileRead.model_validate(LLMRouterConfigLoader(session).get_profile(profile_key))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/llm-router/lanes", response_model=list[LLMRouterLaneRead])
    def list_llm_router_lanes(profile_key: str = "default") -> list[LLMRouterLaneRead]:
        try:
            with session_scope() as session:
                lanes = LLMRouterConfigLoader(session).list_lanes(profile_key=profile_key)
                return [LLMRouterLaneRead.model_validate(lane) for lane in lanes]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/llm-router/route", response_model=LLMRouteResponse)
    def route_llm_request(data: LLMRouteRequest) -> LLMRouteResponse:
        try:
            with session_scope() as session:
                return LLMRouterService(session).route(
                    lane_name=data.lane_name,
                    prompt=data.prompt,
                    messages=data.messages,
                    requested_task_type=data.requested_task_type,
                    response_format=data.response_format,
                    profile_key=data.profile_key,
                    correlation_id="api-m10-1-llm-route",
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/llm-router/smoke-test", response_model=LLMRouterSmokeTestRead)
    def run_llm_router_smoke_test(data: LLMRouterSmokeTestRequest | None = None) -> LLMRouterSmokeTestRead:
        try:
            with session_scope() as session:
                request = data or LLMRouterSmokeTestRequest()
                return LLMRouterSmokeTestRead.model_validate(LLMRouterService(session).run_smoke_test(profile_key=request.profile_key))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/prompt-registry/sync", response_model=PromptRegistrySyncSummary)
    def sync_prompt_registry() -> PromptRegistrySyncSummary:
        try:
            with session_scope() as session:
                return PromptRegistryService(session).sync_repo_registry()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/prompt-registry/render", response_model=PromptRenderResult)
    def render_prompt(data: PromptRenderRequest) -> PromptRenderResult:
        try:
            with session_scope() as session:
                return PromptRegistryService(session).render_prompt(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/prompt-registry/validate-output", response_model=PromptOutputValidationResult)
    def validate_prompt_output(data: PromptOutputValidationRequest) -> PromptOutputValidationResult:
        try:
            with session_scope() as session:
                return PromptRegistryService(session).validate_output(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/prompt-registry/evaluations/run", response_model=list[PromptEvaluationRunRead])
    def run_prompt_evaluations() -> list[PromptEvaluationRunRead]:
        try:
            with session_scope() as session:
                runs = PromptRegistryService(session).run_evaluation_cases()
                return [PromptEvaluationRunRead.model_validate(run) for run in runs]
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
