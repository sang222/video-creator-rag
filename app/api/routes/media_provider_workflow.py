from fastapi import APIRouter

from app.api.routes.imports import (
    AIHeroAssetPlanRequest,
    AIHeroAssetPlanningService,
    AIHeroAssetRead,
    AIHeroGenerationExecuteRequest,
    AIHeroGenerationJobRead,
    AIHeroGenerationService,
    LicenseEvidenceGateCheckRequest,
    LicenseEvidenceGateRead,
    LicenseEvidenceGateService,
    LongFormRenderPackageCreate,
    LongFormRenderPackageRead,
    LongFormRenderPackageService,
    MediaProviderBudgetCheckRequest,
    MediaProviderBudgetGateRead,
    MediaProviderBudgetPolicyRead,
    MediaProviderBudgetService,
    MediaProviderBudgetSnapshotRead,
    MediaProviderRoleProfileRead,
    MediaProviderRoleService,
    MediaQCGateCheckRequest,
    MediaQCGateRead,
    MediaQCGateService,
    MediaRenderJobRouterService,
    MediaRenderRoutingDecisionRead,
    MediaRenderRoutingDecisionRequest,
    ProviderCapabilityGateCheckRequest,
    ProviderCapabilityGateRead,
    ProviderCapabilityGateService,
    ProviderCapabilityMatrixEntryRead,
    ProviderCapabilityMatrixService,
    ReusedContentRiskGateCheckRequest,
    ReusedContentRiskGateRead,
    ReusedContentRiskGateService,
    ThumbnailVariantPlanRequest,
    ThumbnailVariantPlanningService,
    ThumbnailVariantRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/media-provider-roles", response_model=list[MediaProviderRoleProfileRead]
    )
    def list_media_provider_roles() -> list[MediaProviderRoleProfileRead]:
        try:
            with session_scope() as session:
                roles = MediaProviderRoleService(session).list_roles()
                return [
                    MediaProviderRoleProfileRead.model_validate(role) for role in roles
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/media-provider-roles/{provider_key}",
        response_model=MediaProviderRoleProfileRead,
    )
    def get_media_provider_role(provider_key: str) -> MediaProviderRoleProfileRead:
        try:
            with session_scope() as session:
                return MediaProviderRoleProfileRead.model_validate(
                    MediaProviderRoleService(session).require_role(provider_key)
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/media-provider-capabilities",
        response_model=list[ProviderCapabilityMatrixEntryRead],
    )
    def list_media_provider_capabilities(
        provider_key: str | None = None,
    ) -> list[ProviderCapabilityMatrixEntryRead]:
        try:
            with session_scope() as session:
                entries = ProviderCapabilityMatrixService(session).list_entries(
                    provider_key=provider_key
                )
                return [
                    ProviderCapabilityMatrixEntryRead.model_validate(entry)
                    for entry in entries
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/media-provider-capabilities/{provider_key}",
        response_model=list[ProviderCapabilityMatrixEntryRead],
    )
    def get_media_provider_capabilities(
        provider_key: str,
    ) -> list[ProviderCapabilityMatrixEntryRead]:
        try:
            with session_scope() as session:
                entries = ProviderCapabilityMatrixService(session).list_entries(
                    provider_key=provider_key
                )
                return [
                    ProviderCapabilityMatrixEntryRead.model_validate(entry)
                    for entry in entries
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/media-render-routing/decide", response_model=MediaRenderRoutingDecisionRead
    )
    def decide_media_render_route(
        data: MediaRenderRoutingDecisionRequest,
    ) -> MediaRenderRoutingDecisionRead:
        try:
            with session_scope() as session:
                decision = MediaRenderJobRouterService(session).decide(data=data)
                return MediaRenderRoutingDecisionRead.model_validate(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/media-render-routing/decisions/{decision_id}",
        response_model=MediaRenderRoutingDecisionRead,
    )
    def get_media_render_routing_decision(
        decision_id: uuid.UUID,
    ) -> MediaRenderRoutingDecisionRead:
        try:
            with session_scope() as session:
                return MediaRenderRoutingDecisionRead.model_validate(
                    MediaRenderJobRouterService(session).get_decision(decision_id)
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-projects/{video_project_id}/long-form-render-package",
        response_model=LongFormRenderPackageRead,
    )
    def create_long_form_render_package(
        video_project_id: uuid.UUID,
        data: LongFormRenderPackageCreate | None = None,
    ) -> LongFormRenderPackageRead:
        try:
            with session_scope() as session:
                package = LongFormRenderPackageService(session).create(
                    video_project_id=video_project_id,
                    data=data or LongFormRenderPackageCreate(),
                )
                return LongFormRenderPackageRead.model_validate(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/long-form-render-packages/{package_id}",
        response_model=LongFormRenderPackageRead,
    )
    def get_long_form_render_package(
        package_id: uuid.UUID,
    ) -> LongFormRenderPackageRead:
        try:
            with session_scope() as session:
                return LongFormRenderPackageRead.model_validate(
                    LongFormRenderPackageService(session).require(package_id)
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-projects/{video_project_id}/ai-hero-assets/plan",
        response_model=AIHeroAssetRead,
    )
    def plan_ai_hero_asset(
        video_project_id: uuid.UUID, data: AIHeroAssetPlanRequest
    ) -> AIHeroAssetRead:
        try:
            with session_scope() as session:
                return AIHeroAssetRead.model_validate(
                    AIHeroAssetPlanningService(session).plan(
                        video_project_id=video_project_id, data=data
                    )
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/ai-hero-assets/{asset_id}", response_model=AIHeroAssetRead)
    def get_ai_hero_asset(asset_id: uuid.UUID) -> AIHeroAssetRead:
        try:
            with session_scope() as session:
                return AIHeroAssetRead.model_validate(
                    AIHeroAssetPlanningService(session).require(asset_id)
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/ai-hero-assets/{asset_id}/generate", response_model=AIHeroGenerationJobRead
    )
    def generate_ai_hero_asset(
        asset_id: uuid.UUID,
        data: AIHeroGenerationExecuteRequest | None = None,
    ) -> AIHeroGenerationJobRead:
        try:
            with session_scope() as session:
                return AIHeroGenerationService(session).execute(
                    asset_id=asset_id, data=data
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-projects/{video_project_id}/thumbnail-variants/plan",
        response_model=list[ThumbnailVariantRead],
    )
    def plan_thumbnail_variants(
        video_project_id: uuid.UUID, data: ThumbnailVariantPlanRequest
    ) -> list[ThumbnailVariantRead]:
        try:
            with session_scope() as session:
                variants = ThumbnailVariantPlanningService(session).plan(
                    video_project_id=video_project_id, data=data
                )
                return [
                    ThumbnailVariantRead.model_validate(variant) for variant in variants
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/thumbnail-variants/{variant_id}", response_model=ThumbnailVariantRead)
    def get_thumbnail_variant(variant_id: uuid.UUID) -> ThumbnailVariantRead:
        try:
            with session_scope() as session:
                return ThumbnailVariantRead.model_validate(
                    ThumbnailVariantPlanningService(session).require(variant_id)
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/media-provider-budgets", response_model=list[MediaProviderBudgetPolicyRead]
    )
    def list_media_provider_budget_policies() -> list[MediaProviderBudgetPolicyRead]:
        try:
            with session_scope() as session:
                policies = MediaProviderBudgetService(session).list_policies()
                return [
                    MediaProviderBudgetPolicyRead.model_validate(policy)
                    for policy in policies
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/media-provider-budgets/snapshot",
        response_model=list[MediaProviderBudgetSnapshotRead],
    )
    def list_media_provider_budget_snapshots() -> list[MediaProviderBudgetSnapshotRead]:
        try:
            with session_scope() as session:
                snapshots = MediaProviderBudgetService(session).latest_snapshots()
                return [
                    MediaProviderBudgetSnapshotRead.model_validate(snapshot)
                    for snapshot in snapshots
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/media-provider-gates/capability/check",
        response_model=ProviderCapabilityGateRead,
    )
    def check_media_provider_capability_gate(
        data: ProviderCapabilityGateCheckRequest,
    ) -> ProviderCapabilityGateRead:
        try:
            with session_scope() as session:
                return ProviderCapabilityGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/media-provider-gates/license/check", response_model=LicenseEvidenceGateRead
    )
    def check_media_provider_license_gate(
        data: LicenseEvidenceGateCheckRequest,
    ) -> LicenseEvidenceGateRead:
        try:
            with session_scope() as session:
                return LicenseEvidenceGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/media-provider-gates/budget/check", response_model=MediaProviderBudgetGateRead
    )
    def check_media_provider_budget_gate(
        data: MediaProviderBudgetCheckRequest,
    ) -> MediaProviderBudgetGateRead:
        try:
            with session_scope() as session:
                return MediaProviderBudgetService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/media-provider-gates/reused-content/check",
        response_model=ReusedContentRiskGateRead,
    )
    def check_reused_content_risk_gate(
        data: ReusedContentRiskGateCheckRequest,
    ) -> ReusedContentRiskGateRead:
        try:
            with session_scope() as session:
                return ReusedContentRiskGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/media-provider-gates/media-qc/check", response_model=MediaQCGateRead)
    def check_media_qc_gate(data: MediaQCGateCheckRequest) -> MediaQCGateRead:
        try:
            with session_scope() as session:
                return MediaQCGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
