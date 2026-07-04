from fastapi import APIRouter

from app.api.routes.imports import (
    IntegrationReadinessRead,
    ProviderReadinessM2Service,
    ProviderReadinessService,
    ProviderReadinessSnapshotM2Read,
    ProviderReadinessSnapshotRead,
    ProviderSmokeRequest,
    ReadinessRunRequest,
    RealSmokeOrchestratorService,
    RealSmokeRunRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/integrations/readiness", response_model=IntegrationReadinessRead)
    def get_integrations_readiness() -> IntegrationReadinessRead:
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).readiness()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/integrations/provider-wiring", response_model=ProviderReadinessSnapshotM2Read)
    def get_provider_wiring_readiness() -> ProviderReadinessSnapshotM2Read:
        try:
            return ProviderReadinessM2Service().snapshot()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/integrations/readiness/run", response_model=ProviderReadinessSnapshotRead)
    def run_integrations_readiness(data: ReadinessRunRequest | None = None) -> ProviderReadinessSnapshotRead:
        _ = data or ReadinessRunRequest()
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).run()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/integrations/readiness/snapshots/{snapshot_id}", response_model=ProviderReadinessSnapshotRead)
    def get_integrations_readiness_snapshot(snapshot_id: uuid.UUID) -> ProviderReadinessSnapshotRead:
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).get_snapshot(snapshot_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/integrations/providers/{provider_key}/readiness", response_model=IntegrationReadinessRead)
    def get_provider_readiness(provider_key: str) -> IntegrationReadinessRead:
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).provider_readiness(provider_key)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/integrations/providers/{provider_key}/smoke", response_model=RealSmokeRunRead)
    def run_provider_smoke(provider_key: str, data: ProviderSmokeRequest | None = None) -> RealSmokeRunRead:
        try:
            with session_scope() as session:
                return RealSmokeOrchestratorService(session).run_provider(provider_key, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/integrations/smoke-runs/{run_id}", response_model=RealSmokeRunRead)
    def get_integration_smoke_run(run_id: uuid.UUID) -> RealSmokeRunRead:
        try:
            with session_scope() as session:
                return RealSmokeOrchestratorService(session).get_run(run_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
