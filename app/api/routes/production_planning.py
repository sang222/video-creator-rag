from fastapi import APIRouter, Request
from pathlib import Path

from app.contracts.d2p1 import DailyToPackageRunRequest, DailyToPackageStatusRead
from app.contracts.long_production import (
    LongProductionOrchestrationReceipt,
    LongProductionRunRequest,
    LongProductionStatusRead,
)
from app.services.d2p1 import DailyToPackageOrchestrator
from app.services.long_production import LongProductionOrchestrator
from app.services.security_boundary import actor_from_request
from app.db.models import VideoProject

from app.api.routes.imports import (
    AccessibilityQCService,
    Any,
    ChannelAuthorityService,
    ChannelDailyRunCreate,
    ChannelDailyRunRead,
    ChannelDailyRunService,
    ChannelStatePackService,
    ChannelStatePackSnapshotCreate,
    ChannelStatePackSnapshotRead,
    ContextPackSnapshotCreate,
    ContextPackSnapshotRead,
    DailyIdeaDecisionCreate,
    DailyIdeaDecisionRead,
    DailyRunExecuteRequest,
    EditorialCalendarService,
    EditorialCalendarSlotCreate,
    EditorialCalendarSlotRead,
    IdeaMarketPreflightCreate,
    IdeaMarketPreflightRead,
    IdeaMarketPreflightService,
    LocalFixtureRendererService,
    MediaQCService,
    NotFoundError,
    ProductionArtifactRunCreate,
    ProductionArtifactRunRead,
    ProductionArtifactRunService,
    ProjectAdmissionDecisionCreate,
    ProjectAdmissionDecisionRead,
    ProjectAdmissionService,
    QCRunRequest,
    ResourceResolverService,
    RetrievalPlanSnapshotCreate,
    RetrievalPlanSnapshotRead,
    SearchDemandEvidenceCreate,
    SearchDemandEvidenceRead,
    SearchDemandEvidenceService,
    ValidationFailureError,
    session_scope,
    uuid,
)

from app.api.routes.serializers_core import (
    _accessibility_qc_report,
    _channel_daily_run,
    _channel_state_pack,
    _context_pack,
    _daily_idea_decision,
    _editorial_slot,
    _idea_market_preflight,
    _media_qc_report,
    _production_run,
    _project_admission_decision,
    _render_job,
    _render_package,
    _retrieval_plan,
    _search_demand_evidence,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()
    lpro1_workspace = Path(__file__).resolve().parents[3] / "artifacts" / "lpro1"

    @router.get("/video-projects/{project_id}/market-alignment")
    def read_project_market_alignment(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                project = session.get(VideoProject, project_id)
                if project is None:
                    raise NotFoundError(f"video project not found: {project_id}")
                summary = project.audience_delivery_summary or {}
                freeze = summary.get("target_market_freeze")
                alignment = summary.get("market_alignment") or {}
                blockers = []
                if not freeze:
                    blockers.append("TARGET_MARKET_PROFILE_MISSING")
                if not alignment:
                    blockers.append("MARKET_ALIGNMENT_EVIDENCE_MISSING")
                return {
                    "project_id": str(project.id),
                    "target_market_freeze": freeze,
                    "profile_version": (freeze or {}).get(
                        "target_market_profile_version"
                    ),
                    "target_market": (freeze or {}).get("primary_market"),
                    "primary_locale": (freeze or {}).get("primary_locale"),
                    "component_gate_states": alignment.get("component_gate_states", {}),
                    "overall_verdict": alignment.get("overall_verdict", "BLOCK"),
                    "reason_codes": sorted(
                        set([*blockers, *alignment.get("reason_codes", [])])
                    ),
                    "blockers": blockers,
                    "exact_next_action": (
                        "Review the market alignment evidence and create a new artifact version."
                        if blockers
                        else alignment.get(
                            "exact_next_action", "Continue to exact package review."
                        )
                    ),
                    "read_only": True,
                }
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/editorial-calendar-slots", response_model=EditorialCalendarSlotRead)
    def create_editorial_calendar_slot(data: EditorialCalendarSlotCreate) -> EditorialCalendarSlotRead:
        try:
            with session_scope() as session:
                slot = EditorialCalendarService(session).create_slot(data=data)
                return EditorialCalendarSlotRead.model_validate(_editorial_slot(slot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/editorial-calendar-slots/{slot_id}", response_model=EditorialCalendarSlotRead)
    def get_editorial_calendar_slot(slot_id: uuid.UUID) -> EditorialCalendarSlotRead:
        try:
            with session_scope() as session:
                slot = EditorialCalendarService(session).get_slot(slot_id)
                if slot is None:
                    raise NotFoundError(f"editorial slot not found: {slot_id}")
                return EditorialCalendarSlotRead.model_validate(_editorial_slot(slot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/search-demand-evidence", response_model=SearchDemandEvidenceRead)
    def create_search_demand_evidence(data: SearchDemandEvidenceCreate) -> SearchDemandEvidenceRead:
        try:
            with session_scope() as session:
                evidence = SearchDemandEvidenceService(session).create_evidence(data=data)
                return SearchDemandEvidenceRead.model_validate(_search_demand_evidence(evidence))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/context/retrieval-plans", response_model=RetrievalPlanSnapshotRead)
    def create_retrieval_plan(data: RetrievalPlanSnapshotCreate) -> RetrievalPlanSnapshotRead:
        try:
            with session_scope() as session:
                plan = ResourceResolverService(session).create_retrieval_plan(data=data)
                return RetrievalPlanSnapshotRead.model_validate(_retrieval_plan(plan))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/context/context-packs", response_model=ContextPackSnapshotRead)
    def create_context_pack(data: ContextPackSnapshotCreate) -> ContextPackSnapshotRead:
        try:
            with session_scope() as session:
                pack = ResourceResolverService(session).build_context_pack(data=data)
                return ContextPackSnapshotRead.model_validate(_context_pack(pack))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/context/context-packs/{context_pack_id}", response_model=ContextPackSnapshotRead)
    def get_context_pack(context_pack_id: uuid.UUID) -> ContextPackSnapshotRead:
        try:
            with session_scope() as session:
                pack = ResourceResolverService(session).get_context_pack(context_pack_id)
                if pack is None:
                    raise NotFoundError(f"context pack not found: {context_pack_id}")
                return ContextPackSnapshotRead.model_validate(_context_pack(pack))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channel-state-packs", response_model=ChannelStatePackSnapshotRead)
    def create_channel_state_pack(data: ChannelStatePackSnapshotCreate) -> ChannelStatePackSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ChannelStatePackService(session).build_snapshot(data=data)
                return ChannelStatePackSnapshotRead.model_validate(_channel_state_pack(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channel-daily-runs", response_model=ChannelDailyRunRead)
    def create_channel_daily_run(data: ChannelDailyRunCreate) -> ChannelDailyRunRead:
        try:
            with session_scope() as session:
                daily_run = ChannelDailyRunService(session).create_run(data=data)
                return ChannelDailyRunRead.model_validate(_channel_daily_run(daily_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channel-daily-runs/{daily_run_id}/execute", response_model=ChannelDailyRunRead)
    def execute_channel_daily_run(daily_run_id: uuid.UUID, data: DailyRunExecuteRequest | None = None) -> ChannelDailyRunRead:
        try:
            with session_scope() as session:
                daily_run = ChannelDailyRunService(session).execute_run(
                    daily_run_id=daily_run_id,
                    data=data or DailyRunExecuteRequest(),
                )
                return ChannelDailyRunRead.model_validate(_channel_daily_run(daily_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channel-daily-runs/{daily_run_id}", response_model=ChannelDailyRunRead)
    def get_channel_daily_run(daily_run_id: uuid.UUID) -> ChannelDailyRunRead:
        try:
            with session_scope() as session:
                daily_run = ChannelDailyRunService(session).get_run(daily_run_id)
                if daily_run is None:
                    raise NotFoundError(f"daily run not found: {daily_run_id}")
                return ChannelDailyRunRead.model_validate(_channel_daily_run(daily_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/daily-idea-decisions", response_model=DailyIdeaDecisionRead)
    def create_daily_idea_decision(data: DailyIdeaDecisionCreate) -> DailyIdeaDecisionRead:
        try:
            with session_scope() as session:
                decision = ChannelAuthorityService(session).create_decision(data=data)
                return DailyIdeaDecisionRead.model_validate(_daily_idea_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/daily-idea-decisions/{decision_id}", response_model=DailyIdeaDecisionRead)
    def get_daily_idea_decision(decision_id: uuid.UUID) -> DailyIdeaDecisionRead:
        try:
            with session_scope() as session:
                from app.db.models import DailyIdeaDecision

                decision = session.get(DailyIdeaDecision, decision_id)
                if decision is None:
                    raise NotFoundError(f"daily idea decision not found: {decision_id}")
                return DailyIdeaDecisionRead.model_validate(_daily_idea_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/daily-idea-decisions/{decision_id}/production-handoff",
        response_model=DailyToPackageStatusRead,
    )
    def get_daily_idea_production_handoff(decision_id: uuid.UUID) -> DailyToPackageStatusRead:
        """Read durable D2P1 state; this endpoint never runs providers or media."""

        try:
            with session_scope() as session:
                return DailyToPackageOrchestrator(session).status(decision_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/daily-idea-decisions/{decision_id}/production-handoff/run",
        response_model=DailyToPackageStatusRead,
    )
    def run_daily_idea_production_handoff(
        decision_id: uuid.UUID,
        data: DailyToPackageRunRequest | None = None,
    ) -> DailyToPackageStatusRead:
        """Retained route name; new execution must use typed v2 admission."""

        try:
            del decision_id, data
            raise ValidationFailureError(
                "V2_PROJECT_ADMISSION_REQUIRED"
            )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-projects/{project_id}/long-production",
        response_model=LongProductionStatusRead,
    )
    def get_long_production_status(project_id: uuid.UUID) -> LongProductionStatusRead:
        """Read the durable LPRO1 lifecycle without executing media or providers."""

        try:
            with session_scope() as session:
                return LongProductionOrchestrator.status(session, project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/video-projects/{project_id}/long-production/run",
        response_model=LongProductionOrchestrationReceipt,
    )
    def run_long_production(
        project_id: uuid.UUID,
        request: Request,
        data: LongProductionRunRequest | None = None,
    ) -> LongProductionOrchestrationReceipt:
        """Run/resume LPRO1 from frozen package lineage; no creative overrides are accepted."""

        try:
            actor = actor_from_request(request)
            control = data or LongProductionRunRequest()
            with session_scope() as session:
                return LongProductionOrchestrator(
                    session,
                    workspace_root=lpro1_workspace,
                ).run(
                    project_id=project_id,
                    package_id=control.package_id,
                    execution_mode=control.execution_mode,
                    execution_envelope=control.execution_envelope,
                    actor_user_id=actor.actor_id,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/idea-market-preflights", response_model=IdeaMarketPreflightRead)
    def create_idea_market_preflight(data: IdeaMarketPreflightCreate) -> IdeaMarketPreflightRead:
        try:
            with session_scope() as session:
                preflight = IdeaMarketPreflightService(session).create_preflight(data=data)
                return IdeaMarketPreflightRead.model_validate(_idea_market_preflight(preflight))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/project-admission-decisions", response_model=ProjectAdmissionDecisionRead)
    def create_project_admission_decision(data: ProjectAdmissionDecisionCreate) -> ProjectAdmissionDecisionRead:
        try:
            del data
            raise ValidationFailureError(
                "V2_PROJECT_ADMISSION_REQUIRED"
            )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/project-admission-decisions/{decision_id}", response_model=ProjectAdmissionDecisionRead)
    def get_project_admission_decision(decision_id: uuid.UUID) -> ProjectAdmissionDecisionRead:
        try:
            with session_scope() as session:
                decision = ProjectAdmissionService(session).get_decision(decision_id)
                if decision is None:
                    raise NotFoundError(f"project admission decision not found: {decision_id}")
                return ProjectAdmissionDecisionRead.model_validate(_project_admission_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/production-runs", response_model=ProductionArtifactRunRead)
    def create_production_run(data: ProductionArtifactRunCreate) -> ProductionArtifactRunRead:
        try:
            with session_scope() as session:
                run = ProductionArtifactRunService(session).create_run(data=data)
                return ProductionArtifactRunRead.model_validate(_production_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/production-runs/{run_id}/execute", response_model=ProductionArtifactRunRead)
    def execute_production_run(run_id: uuid.UUID) -> ProductionArtifactRunRead:
        try:
            with session_scope() as session:
                run = ProductionArtifactRunService(session).execute_real_provider_flow(run_id=run_id)
                return ProductionArtifactRunRead.model_validate(_production_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/production-runs/{run_id}", response_model=ProductionArtifactRunRead)
    def get_production_run(run_id: uuid.UUID) -> ProductionArtifactRunRead:
        try:
            with session_scope() as session:
                run = ProductionArtifactRunService(session).get_run(run_id)
                if run is None:
                    raise NotFoundError(f"production artifact run not found: {run_id}")
                return ProductionArtifactRunRead.model_validate(_production_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/render-jobs/{render_job_id}")
    def get_render_job(render_job_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                job = LocalFixtureRendererService(session).get_job(render_job_id)
                if job is None:
                    raise NotFoundError(f"render job not found: {render_job_id}")
                return _render_job(job)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/render-packages/{render_package_id}")
    def get_render_package(render_package_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                package = LocalFixtureRendererService(session).get_package(render_package_id)
                if package is None:
                    raise NotFoundError(f"render package not found: {render_package_id}")
                return _render_package(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/media-qc/run")
    def run_media_qc(data: QCRunRequest) -> dict[str, Any]:
        try:
            with session_scope() as session:
                if data.render_package_snapshot_id is None:
                    raise ValidationFailureError("render_package_snapshot_id is required for media QC API")
                package = LocalFixtureRendererService(session).get_package(data.render_package_snapshot_id)
                if package is None:
                    raise NotFoundError(f"render package not found: {data.render_package_snapshot_id}")
                report = MediaQCService(session).run_qc(render_package_snapshot=package)
                return _media_qc_report(report)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/accessibility-qc/run")
    def run_accessibility_qc(data: QCRunRequest) -> dict[str, Any]:
        try:
            with session_scope() as session:
                from app.db.models import CaptionTrackSnapshot, RenderPackageSnapshot

                if data.caption_track_snapshot_id is None:
                    raise ValidationFailureError("caption_track_snapshot_id is required for accessibility QC API")
                caption = session.get(CaptionTrackSnapshot, data.caption_track_snapshot_id)
                if caption is None:
                    raise NotFoundError(f"caption track snapshot not found: {data.caption_track_snapshot_id}")
                package = session.get(RenderPackageSnapshot, data.render_package_snapshot_id) if data.render_package_snapshot_id else None
                report = AccessibilityQCService(session).run_qc(
                    caption_track_snapshot=caption,
                    render_package_snapshot=package,
                )
                return _accessibility_qc_report(report)
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
