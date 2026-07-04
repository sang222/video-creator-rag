from fastapi import APIRouter

from app.api.routes.imports import (
    ChannelMemoryItemRead,
    ClosedLearningLoopService,
    ClosedLearningLoopStatusRead,
    ControlledMemoryService,
    FailureTraceReportRead,
    LearningCandidateGenerationRunCreate,
    LearningCandidateGenerationRunExecuteRequest,
    LearningCandidateGenerationRunRead,
    LearningCandidateGenerationService,
    LearningCandidateRead,
    LearningEvidenceBundleRead,
    LearningReadService,
    LearningReviewDecisionCreate,
    LearningReviewDecisionRead,
    LearningReviewQueueItemRead,
    LearningReviewQueueService,
    LearningToMemoryPromotionRequest,
    LearningToMemoryPromotionRunRead,
    LearningToMemoryPromotionService,
    MemoryApprovalDecisionRead,
    MemoryApprovalRequest,
    MemoryFacetRead,
    MemoryFromApprovedPlaybookCreate,
    MemoryInfluenceManifestRead,
    MemoryInfluenceManifestService,
    MemoryReviewQueueItemRead,
    PlaybookCandidateDraftRead,
    PostPublishHealthMonitorService,
    PostPublishHealthRunCreate,
    PostPublishHealthRunRead,
    QualityDeltaAttributionRead,
    QualityDeltaAttributionRunRequest,
    QualityDeltaAttributionService,
    RecoveryProposalRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
    _failure_trace_report,
    _learning_candidate,
    _learning_evidence_bundle,
    _learning_generation_run,
    _learning_review_action,
    _learning_review_queue_item,
    _playbook_candidate_draft,
    _post_publish_health_run,
    _recovery_proposal,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/post-publish-health-runs", response_model=PostPublishHealthRunRead)
    def create_post_publish_health_run(data: PostPublishHealthRunCreate) -> PostPublishHealthRunRead:
        try:
            with session_scope() as session:
                run = PostPublishHealthMonitorService(session).create_health_run(data=data)
                return PostPublishHealthRunRead.model_validate(_post_publish_health_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/post-publish-health-runs/{run_id}/execute", response_model=PostPublishHealthRunRead)
    def execute_post_publish_health_run(run_id: uuid.UUID) -> PostPublishHealthRunRead:
        try:
            with session_scope() as session:
                run = PostPublishHealthMonitorService(session).execute_health_run(run_id=run_id)
                return PostPublishHealthRunRead.model_validate(_post_publish_health_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/post-publish-health-runs/{run_id}", response_model=PostPublishHealthRunRead)
    def get_post_publish_health_run(run_id: uuid.UUID) -> PostPublishHealthRunRead:
        try:
            with session_scope() as session:
                run = PostPublishHealthMonitorService(session).require_health_run(run_id)
                return PostPublishHealthRunRead.model_validate(_post_publish_health_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/post-publish-health", response_model=list[PostPublishHealthRunRead])
    def list_uploaded_video_post_publish_health(uploaded_video_id: uuid.UUID) -> list[PostPublishHealthRunRead]:
        try:
            with session_scope() as session:
                runs = PostPublishHealthMonitorService(session).list_health_runs_by_uploaded_video(uploaded_video_id)
                return [PostPublishHealthRunRead.model_validate(_post_publish_health_run(run)) for run in runs]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/failure-trace-reports", response_model=list[FailureTraceReportRead])
    def list_uploaded_video_failure_trace_reports(uploaded_video_id: uuid.UUID) -> list[FailureTraceReportRead]:
        try:
            with session_scope() as session:
                reports = PostPublishHealthMonitorService(session).list_failure_trace_reports_by_uploaded_video(uploaded_video_id)
                return [FailureTraceReportRead.model_validate(_failure_trace_report(report)) for report in reports]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/failure-trace-reports/{report_id}", response_model=FailureTraceReportRead)
    def get_failure_trace_report(report_id: uuid.UUID) -> FailureTraceReportRead:
        try:
            with session_scope() as session:
                report = PostPublishHealthMonitorService(session).require_failure_trace_report(report_id)
                return FailureTraceReportRead.model_validate(_failure_trace_report(report))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/recovery-proposals", response_model=list[RecoveryProposalRead])
    def list_uploaded_video_recovery_proposals(uploaded_video_id: uuid.UUID) -> list[RecoveryProposalRead]:
        try:
            with session_scope() as session:
                proposals = PostPublishHealthMonitorService(session).list_recovery_proposals_by_uploaded_video(uploaded_video_id)
                return [RecoveryProposalRead.model_validate(_recovery_proposal(proposal)) for proposal in proposals]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/recovery-proposals/{proposal_id}/accept", response_model=RecoveryProposalRead)
    def accept_recovery_proposal(proposal_id: uuid.UUID) -> RecoveryProposalRead:
        try:
            with session_scope() as session:
                proposal = PostPublishHealthMonitorService(session).accept_recovery_proposal(proposal_id=proposal_id)
                return RecoveryProposalRead.model_validate(_recovery_proposal(proposal))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/recovery-proposals/{proposal_id}/reject", response_model=RecoveryProposalRead)
    def reject_recovery_proposal(proposal_id: uuid.UUID) -> RecoveryProposalRead:
        try:
            with session_scope() as session:
                proposal = PostPublishHealthMonitorService(session).reject_recovery_proposal(proposal_id=proposal_id)
                return RecoveryProposalRead.model_validate(_recovery_proposal(proposal))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/learning-candidate-generation-runs", response_model=LearningCandidateGenerationRunRead)
    def create_learning_candidate_generation_run(
        data: LearningCandidateGenerationRunCreate,
    ) -> LearningCandidateGenerationRunRead:
        try:
            with session_scope() as session:
                run = LearningCandidateGenerationService(session).create_run(data=data)
                return LearningCandidateGenerationRunRead.model_validate(_learning_generation_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/learning-candidate-generation-runs/{run_id}/execute", response_model=LearningCandidateGenerationRunRead)
    def execute_learning_candidate_generation_run(
        run_id: uuid.UUID,
        data: LearningCandidateGenerationRunExecuteRequest | None = None,
    ) -> LearningCandidateGenerationRunRead:
        try:
            with session_scope() as session:
                request = data or LearningCandidateGenerationRunExecuteRequest()
                run = LearningCandidateGenerationService(session).execute_run(
                    run_id=run_id,
                    correlation_id=request.correlation_id or "api-m10-learning-generation-execute",
                )
                return LearningCandidateGenerationRunRead.model_validate(_learning_generation_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning-candidate-generation-runs/{run_id}", response_model=LearningCandidateGenerationRunRead)
    def get_learning_candidate_generation_run(run_id: uuid.UUID) -> LearningCandidateGenerationRunRead:
        try:
            with session_scope() as session:
                run = LearningCandidateGenerationService(session).require_run(run_id)
                return LearningCandidateGenerationRunRead.model_validate(_learning_generation_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning-candidates", response_model=list[LearningCandidateRead])
    def list_learning_candidates(
        candidate_state: str | None = None,
        company_id: uuid.UUID | None = None,
        uploaded_video_id: uuid.UUID | None = None,
    ) -> list[LearningCandidateRead]:
        try:
            with session_scope() as session:
                candidates = LearningReadService(session).list_candidates(
                    candidate_state=candidate_state,
                    company_id=company_id,
                    uploaded_video_id=uploaded_video_id,
                )
                return [LearningCandidateRead.model_validate(_learning_candidate(candidate)) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning-candidates/{candidate_id}", response_model=LearningCandidateRead)
    def get_learning_candidate(candidate_id: uuid.UUID) -> LearningCandidateRead:
        try:
            with session_scope() as session:
                candidate = LearningReadService(session).require_candidate(candidate_id)
                return LearningCandidateRead.model_validate(_learning_candidate(candidate))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning-candidates/{candidate_id}/evidence-bundle", response_model=LearningEvidenceBundleRead)
    def get_learning_candidate_evidence_bundle(candidate_id: uuid.UUID) -> LearningEvidenceBundleRead:
        try:
            with session_scope() as session:
                bundle = LearningReadService(session).require_evidence_bundle_for_candidate(candidate_id)
                return LearningEvidenceBundleRead.model_validate(_learning_evidence_bundle(bundle))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/learning-candidates/{candidate_id}/approve", response_model=LearningReviewDecisionRead)
    def approve_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "APPROVE", data)

    @router.post("/learning-candidates/{candidate_id}/reject", response_model=LearningReviewDecisionRead)
    def reject_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "REJECT", data)

    @router.post("/learning-candidates/{candidate_id}/request-more-evidence", response_model=LearningReviewDecisionRead)
    def request_more_learning_evidence(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "REQUEST_MORE_EVIDENCE", data)

    @router.post("/learning-candidates/{candidate_id}/suppress", response_model=LearningReviewDecisionRead)
    def suppress_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "SUPPRESS", data)

    @router.post("/learning-candidates/{candidate_id}/expire", response_model=LearningReviewDecisionRead)
    def expire_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "EXPIRE", data)

    @router.get("/learning-review-queue", response_model=list[LearningReviewQueueItemRead])
    def list_learning_review_queue(
        queue_state: str | None = None,
        company_id: uuid.UUID | None = None,
    ) -> list[LearningReviewQueueItemRead]:
        try:
            with session_scope() as session:
                items = LearningReviewQueueService(session).list_queue(queue_state=queue_state, company_id=company_id)
                return [LearningReviewQueueItemRead.model_validate(_learning_review_queue_item(item)) for item in items]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning-review-queue/{queue_item_id}", response_model=LearningReviewQueueItemRead)
    def get_learning_review_queue_item(queue_item_id: uuid.UUID) -> LearningReviewQueueItemRead:
        try:
            with session_scope() as session:
                item = LearningReadService(session).require_queue_item(queue_item_id)
                return LearningReviewQueueItemRead.model_validate(_learning_review_queue_item(item))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/playbook-candidate-drafts/{draft_id}", response_model=PlaybookCandidateDraftRead)
    def get_playbook_candidate_draft(draft_id: uuid.UUID) -> PlaybookCandidateDraftRead:
        try:
            with session_scope() as session:
                draft = LearningReadService(session).require_playbook_candidate_draft(draft_id)
                return PlaybookCandidateDraftRead.model_validate(_playbook_candidate_draft(draft))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory/review-queue", response_model=list[MemoryReviewQueueItemRead])
    def list_memory_review_queue(queue_status: str | None = None) -> list[MemoryReviewQueueItemRead]:
        try:
            with session_scope() as session:
                items = ControlledMemoryService(session).list_review_queue(queue_status=queue_status)
                return [MemoryReviewQueueItemRead.model_validate(item) for item in items]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory/items/{memory_item_id}", response_model=ChannelMemoryItemRead)
    def get_memory_item(memory_item_id: uuid.UUID) -> ChannelMemoryItemRead:
        try:
            with session_scope() as session:
                item = ControlledMemoryService(session).require_item(memory_item_id)
                return ChannelMemoryItemRead.model_validate(item)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory/items/{memory_item_id}/facets", response_model=list[MemoryFacetRead])
    def get_memory_item_facets(memory_item_id: uuid.UUID) -> list[MemoryFacetRead]:
        try:
            with session_scope() as session:
                facets = ControlledMemoryService(session).list_facets(memory_item_id=memory_item_id)
                return [MemoryFacetRead.model_validate(facet) for facet in facets]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/memory/from-approved-playbook-entry/{playbook_entry_id}", response_model=ChannelMemoryItemRead)
    def create_memory_from_approved_playbook_entry(
        playbook_entry_id: uuid.UUID,
        data: MemoryFromApprovedPlaybookCreate | None = None,
    ) -> ChannelMemoryItemRead:
        try:
            with session_scope() as session:
                item = ControlledMemoryService(session).create_from_approved_playbook_entry(
                    playbook_entry_id=playbook_entry_id,
                    data=data,
                )
                return ChannelMemoryItemRead.model_validate(item)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/memory/items/{memory_item_id}/approve", response_model=MemoryApprovalDecisionRead)
    def approve_memory_item(memory_item_id: uuid.UUID, data: MemoryApprovalRequest | None = None) -> MemoryApprovalDecisionRead:
        try:
            with session_scope() as session:
                decision = ControlledMemoryService(session).approve(
                    memory_item_id=memory_item_id,
                    data=data or MemoryApprovalRequest(),
                )
                return MemoryApprovalDecisionRead.model_validate(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/memory/items/{memory_item_id}/reject", response_model=MemoryApprovalDecisionRead)
    def reject_memory_item(memory_item_id: uuid.UUID, data: MemoryApprovalRequest | None = None) -> MemoryApprovalDecisionRead:
        try:
            with session_scope() as session:
                decision = ControlledMemoryService(session).reject(
                    memory_item_id=memory_item_id,
                    data=data or MemoryApprovalRequest(),
                )
                return MemoryApprovalDecisionRead.model_validate(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/memory/items/{memory_item_id}/archive", response_model=MemoryApprovalDecisionRead)
    def archive_memory_item(memory_item_id: uuid.UUID, data: MemoryApprovalRequest | None = None) -> MemoryApprovalDecisionRead:
        try:
            with session_scope() as session:
                decision = ControlledMemoryService(session).archive(
                    memory_item_id=memory_item_id,
                    data=data or MemoryApprovalRequest(),
                )
                return MemoryApprovalDecisionRead.model_validate(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/learning-loop/promotions/from-approved-playbook/{playbook_entry_id}", response_model=LearningToMemoryPromotionRunRead)
    def promote_approved_playbook_to_memory(
        playbook_entry_id: uuid.UUID,
        data: LearningToMemoryPromotionRequest | None = None,
    ) -> LearningToMemoryPromotionRunRead:
        try:
            payload = data or LearningToMemoryPromotionRequest()
            payload = payload.model_copy(update={"approved_playbook_entry_id": playbook_entry_id})
            with session_scope() as session:
                run = LearningToMemoryPromotionService(session).promote_approved_playbook(payload)
                return LearningToMemoryPromotionRunRead.model_validate(run)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/learning-loop/promotions/from-learning-candidate/{candidate_id}", response_model=LearningToMemoryPromotionRunRead)
    def promote_learning_candidate_to_memory(candidate_id: uuid.UUID) -> LearningToMemoryPromotionRunRead:
        try:
            with session_scope() as session:
                run = LearningToMemoryPromotionService(session).promote_learning_candidate(learning_candidate_id=candidate_id)
                return LearningToMemoryPromotionRunRead.model_validate(run)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory-influence-manifests", response_model=list[MemoryInfluenceManifestRead])
    def list_memory_influence_manifests(
        video_project_id: uuid.UUID | None = None,
        package_id: uuid.UUID | None = None,
        agent_key: str | None = None,
        limit: int = 100,
    ) -> list[MemoryInfluenceManifestRead]:
        try:
            with session_scope() as session:
                manifests = MemoryInfluenceManifestService(session).list_manifests(
                    video_project_id=video_project_id,
                    package_id=package_id,
                    agent_key=agent_key,
                    limit=limit,
                )
                return [MemoryInfluenceManifestRead.model_validate(manifest) for manifest in manifests]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory-influence-manifests/{manifest_id}", response_model=MemoryInfluenceManifestRead)
    def read_memory_influence_manifest(manifest_id: uuid.UUID) -> MemoryInfluenceManifestRead:
        try:
            with session_scope() as session:
                return MemoryInfluenceManifestRead.model_validate(MemoryInfluenceManifestService(session).require_manifest(manifest_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/quality-delta-attributions/run", response_model=QualityDeltaAttributionRead)
    def run_quality_delta_attribution(data: QualityDeltaAttributionRunRequest) -> QualityDeltaAttributionRead:
        try:
            with session_scope() as session:
                attribution = QualityDeltaAttributionService(session).run(data)
                return QualityDeltaAttributionRead.model_validate(attribution)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning-loop/status", response_model=ClosedLearningLoopStatusRead)
    def read_closed_learning_loop_status(
        uploaded_video_id: uuid.UUID | None = None,
        target_video_project_id: uuid.UUID | None = None,
    ) -> ClosedLearningLoopStatusRead:
        try:
            with session_scope() as session:
                return ClosedLearningLoopStatusRead.model_validate(
                    ClosedLearningLoopService(session).status(
                        uploaded_video_id=uploaded_video_id,
                        target_video_project_id=target_video_project_id,
                    )
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
