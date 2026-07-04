from fastapi import APIRouter

from app.api.routes.imports import (
    AssetReuseIndexEntryRead,
    AssetReuseIndexService,
    AssetReuseSearchRequest,
    BuildUploadCardsRequest,
    ContentDerivativeGraphEdgeCreate,
    ContentDerivativeGraphEdgeRead,
    CrossPlatformFunnelPackageCreate,
    CrossPlatformFunnelPackageRead,
    CrossPlatformFunnelPackageService,
    DerivativeGraphService,
    DerivativeOriginalityCheckCreate,
    DerivativeOriginalityCheckRead,
    DerivativeOriginalityService,
    HumanUploadTaskRead,
    HumanUploadTaskService,
    NotFoundError,
    PromoteShortToLongCandidateCreate,
    PromoteShortToLongCandidateRead,
    PromoteShortToLongCandidateService,
    ReusableArtifactCreate,
    ReusableArtifactRead,
    ReusableArtifactService,
    ShortCandidateExtractRequest,
    ShortCandidateExtractionService,
    ShortCandidateRankRequest,
    ShortCandidateRankingService,
    ShortCandidateRead,
    ShortCandidateScoreRead,
    UploadCardRead,
    UploadCardService,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/video-projects/{video_project_id}/short-candidates/extract", response_model=list[ShortCandidateRead])
    def extract_short_candidates(
        video_project_id: uuid.UUID,
        data: ShortCandidateExtractRequest | None = None,
    ) -> list[ShortCandidateRead]:
        try:
            with session_scope() as session:
                request = data or ShortCandidateExtractRequest()
                candidates = ShortCandidateExtractionService(session).extract_for_project(video_project_id=video_project_id, data=request)
                return [ShortCandidateRead.model_validate(candidate) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{video_project_id}/short-candidates", response_model=list[ShortCandidateRead])
    def list_short_candidates(video_project_id: uuid.UUID) -> list[ShortCandidateRead]:
        try:
            with session_scope() as session:
                candidates = ShortCandidateExtractionService(session).list_for_project(video_project_id)
                return [ShortCandidateRead.model_validate(candidate) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/short-candidates/{short_candidate_id}", response_model=ShortCandidateRead)
    def get_short_candidate(short_candidate_id: uuid.UUID) -> ShortCandidateRead:
        try:
            with session_scope() as session:
                from app.db.models import ShortCandidate

                candidate = session.get(ShortCandidate, short_candidate_id)
                if candidate is None:
                    raise NotFoundError("short candidate not found")
                return ShortCandidateRead.model_validate(candidate)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/short-candidates/{short_candidate_id}/rank", response_model=ShortCandidateScoreRead)
    def rank_short_candidate(
        short_candidate_id: uuid.UUID,
        data: ShortCandidateRankRequest | None = None,
    ) -> ShortCandidateScoreRead:
        try:
            with session_scope() as session:
                score = ShortCandidateRankingService(session).rank(short_candidate_id=short_candidate_id, data=data or ShortCandidateRankRequest())
                return ShortCandidateScoreRead.model_validate(score)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{video_project_id}/derivative-graph", response_model=list[ContentDerivativeGraphEdgeRead])
    def get_derivative_graph(video_project_id: uuid.UUID) -> list[ContentDerivativeGraphEdgeRead]:
        try:
            with session_scope() as session:
                edges = DerivativeGraphService(session).graph_for_project(video_project_id)
                return [ContentDerivativeGraphEdgeRead.model_validate(edge) for edge in edges]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/derivative-graph/edges/{edge_id}", response_model=ContentDerivativeGraphEdgeRead)
    def get_derivative_graph_edge(edge_id: uuid.UUID) -> ContentDerivativeGraphEdgeRead:
        try:
            with session_scope() as session:
                return ContentDerivativeGraphEdgeRead.model_validate(DerivativeGraphService(session).require_edge(edge_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/derivative-graph/edges", response_model=ContentDerivativeGraphEdgeRead)
    def create_derivative_graph_edge(data: ContentDerivativeGraphEdgeCreate) -> ContentDerivativeGraphEdgeRead:
        try:
            with session_scope() as session:
                return ContentDerivativeGraphEdgeRead.model_validate(DerivativeGraphService(session).create_edge(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/derivative-originality-checks", response_model=DerivativeOriginalityCheckRead)
    def create_derivative_originality_check(data: DerivativeOriginalityCheckCreate) -> DerivativeOriginalityCheckRead:
        try:
            with session_scope() as session:
                return DerivativeOriginalityCheckRead.model_validate(DerivativeOriginalityService(session).create_check(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/derivative-originality-checks/{check_id}", response_model=DerivativeOriginalityCheckRead)
    def get_derivative_originality_check(check_id: uuid.UUID) -> DerivativeOriginalityCheckRead:
        try:
            with session_scope() as session:
                return DerivativeOriginalityCheckRead.model_validate(DerivativeOriginalityService(session).require_check(check_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/reusable-artifacts", response_model=list[ReusableArtifactRead])
    def list_reusable_artifacts(company_id: uuid.UUID | None = None) -> list[ReusableArtifactRead]:
        try:
            with session_scope() as session:
                artifacts = ReusableArtifactService(session).list(company_id=company_id)
                return [ReusableArtifactRead.model_validate(artifact) for artifact in artifacts]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/reusable-artifacts", response_model=ReusableArtifactRead)
    def create_reusable_artifact(data: ReusableArtifactCreate) -> ReusableArtifactRead:
        try:
            with session_scope() as session:
                return ReusableArtifactRead.model_validate(ReusableArtifactService(session).create(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/reusable-artifacts/{artifact_id}", response_model=ReusableArtifactRead)
    def get_reusable_artifact(artifact_id: uuid.UUID) -> ReusableArtifactRead:
        try:
            with session_scope() as session:
                return ReusableArtifactRead.model_validate(ReusableArtifactService(session).require(artifact_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/asset-reuse-index/search", response_model=list[AssetReuseIndexEntryRead])
    def search_asset_reuse_index(data: AssetReuseSearchRequest) -> list[AssetReuseIndexEntryRead]:
        try:
            with session_scope() as session:
                entries = AssetReuseIndexService(session).search(data=data)
                return [AssetReuseIndexEntryRead.model_validate(entry) for entry in entries]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/cross-platform-funnel-packages", response_model=CrossPlatformFunnelPackageRead)
    def create_cross_platform_funnel_package(data: CrossPlatformFunnelPackageCreate) -> CrossPlatformFunnelPackageRead:
        try:
            with session_scope() as session:
                package = CrossPlatformFunnelPackageService(session).create(data=data)
                return CrossPlatformFunnelPackageRead.model_validate(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/cross-platform-funnel-packages/{package_id}", response_model=CrossPlatformFunnelPackageRead)
    def get_cross_platform_funnel_package(package_id: uuid.UUID) -> CrossPlatformFunnelPackageRead:
        try:
            with session_scope() as session:
                return CrossPlatformFunnelPackageRead.model_validate(CrossPlatformFunnelPackageService(session).require(package_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/cross-platform-funnel-packages/{package_id}/build-upload-cards", response_model=list[UploadCardRead])
    def build_cross_platform_upload_cards(
        package_id: uuid.UUID,
        data: BuildUploadCardsRequest | None = None,
    ) -> list[UploadCardRead]:
        try:
            with session_scope() as session:
                cards = CrossPlatformFunnelPackageService(session).build_upload_cards(
                    package_id=package_id,
                    data=data or BuildUploadCardsRequest(),
                )
                return [UploadCardRead.model_validate(card) for card in cards]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/upload-cards/{upload_card_id}", response_model=UploadCardRead)
    def get_upload_card(upload_card_id: uuid.UUID) -> UploadCardRead:
        try:
            with session_scope() as session:
                return UploadCardRead.model_validate(UploadCardService(session).require(upload_card_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/human-upload-tasks", response_model=list[HumanUploadTaskRead])
    def list_human_upload_tasks(task_state: str | None = None) -> list[HumanUploadTaskRead]:
        try:
            with session_scope() as session:
                tasks = HumanUploadTaskService(session).list(task_state=task_state)
                return [HumanUploadTaskRead.model_validate(task) for task in tasks]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/human-upload-tasks/{task_id}", response_model=HumanUploadTaskRead)
    def get_human_upload_task(task_id: uuid.UUID) -> HumanUploadTaskRead:
        try:
            with session_scope() as session:
                return HumanUploadTaskRead.model_validate(HumanUploadTaskService(session).require(task_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/promote-short-to-long-candidates", response_model=PromoteShortToLongCandidateRead)
    def create_promote_short_to_long_candidate(data: PromoteShortToLongCandidateCreate) -> PromoteShortToLongCandidateRead:
        try:
            with session_scope() as session:
                return PromoteShortToLongCandidateRead.model_validate(PromoteShortToLongCandidateService(session).create(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/promote-short-to-long-candidates", response_model=list[PromoteShortToLongCandidateRead])
    def list_promote_short_to_long_candidates() -> list[PromoteShortToLongCandidateRead]:
        try:
            with session_scope() as session:
                candidates = PromoteShortToLongCandidateService(session).list()
                return [PromoteShortToLongCandidateRead.model_validate(candidate) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/promote-short-to-long-candidates/{candidate_id}", response_model=PromoteShortToLongCandidateRead)
    def get_promote_short_to_long_candidate(candidate_id: uuid.UUID) -> PromoteShortToLongCandidateRead:
        try:
            with session_scope() as session:
                return PromoteShortToLongCandidateRead.model_validate(PromoteShortToLongCandidateService(session).require(candidate_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
