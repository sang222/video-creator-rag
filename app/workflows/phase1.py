from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.mocks import (
    AnalyticsAgent,
    AssetContextBuilder,
    AuthorityAgent,
    ComplianceCopyrightAgent,
    HumanReviewPackAgent,
    ImageGenerationAgent,
    MemoryCuratorAgent,
    MonetizationStrategyAgent,
    PublishingContentAgent,
    PromptEngineerAgent,
    ReusableAssetRetrievalAgent,
    RenderAgent,
    SEOMetadataAgent,
    ScriptAgent,
    ScriptCriticAgent,
    StoryboardAgent,
    SubtitleAgent,
    TitleAgent,
    TrendDiscoveryAgent,
    VoiceoverAgent,
)
from app.agents import real_text as real_text_agents
from app.config.settings import get_settings
from app.core.enums import MemoryFamily, ProjectState, ReviewTaskType
from app.memory.router import MemoryRouter
from app.models.entities import (
    AnalyticsSnapshot,
    AuthorityReview,
    ComplianceReport,
    MemoryItem,
    QAReport,
    RenderTimeline,
    ReviewTask,
    UploadedVideo,
    VideoArtifact,
    VideoProject,
)
from app.schemas.api import MemoryItemCreate, WorkflowModeResult
from app.services.context import ContextCompilerService, WorkspaceContextService
from app.services.reviews import ReviewTaskService
from app.services.state_machine import StateMachineService
from app.services.workflow_mode import WorkflowModeRouter


try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - fallback documents skeleton when dependency is absent
    END = "__end__"
    StateGraph = None


class Phase1WorkflowService:
    def __init__(self) -> None:
        self.context_service = WorkspaceContextService()
        self.compiler = ContextCompilerService()
        self.mode_router = WorkflowModeRouter()
        self.state_machine = StateMachineService()
        self.review_service = ReviewTaskService()
        self.memory_router = MemoryRouter()
        self.agent_runtime_mode = get_settings().agent_runtime_mode

    def build_langgraph(self) -> Any:
        if StateGraph is None:
            return {"type": "fallback", "nodes": self.node_names()}
        graph = StateGraph(dict)
        for node in self.node_names():
            graph.add_node(node, lambda state, node=node: {**state, "last_node": node})
        names = self.node_names()
        graph.set_entry_point(names[0])
        for current, nxt in zip(names, names[1:]):
            graph.add_edge(current, nxt)
        graph.add_edge(names[-1], END)
        return graph.compile()

    def node_names(self) -> list[str]:
        return [
            "workspace_context_load",
            "workflow_mode_router",
            "trend_discovery",
            "monetization_strategy",
            "topic_scoring",
            "authority_topic_gate",
            "script",
            "script_critic",
            "authority_script_gate",
            "storyboard",
            "asset_context",
            "reusable_asset_search",
            "media_mock_generation",
            "lowfi_render_mock",
            "qa",
            "compliance",
            "authority_final_gate",
            "seo_metadata",
            "review_task_created",
            "human_action_pause",
            "pre_publish_reverify",
            "salvage_if_needed",
            "mark_published",
            "analytics_snapshot",
            "authority_post_review",
            "memory_curator",
        ]

    def run_to_review_task(self, db: Session, project: VideoProject) -> ReviewTask:
        workspace_context = self.context_service.get_context(db, project.workspace_id)
        constitution = self.compiler.get_active_or_compile(db, project.workspace_id)
        compiled = constitution.content
        if project.current_state == ProjectState.PRE_APPROVED.value:
            mode_result = self._current_mode_result(project, workspace_context)
            monetization = project.metadata_json.get("monetization_strategy") or {
                "monetization_hypothesis": "Resume from pre-spend approval with monetization-safe validation.",
                "target_metric": "qualified CTA clicks and retention",
                "cta_strategy": "soft CTA after value",
                "expected_revenue_path": "affiliate first, ads after YPP",
                "buyer_intent_score": 7.0,
            }
        else:
            mode_result = self.mode_router.route(workspace_context)
            project.workflow_mode = mode_result.selected_mode
            project.metadata_json = {**project.metadata_json, "workflow_mode_result": mode_result.model_dump()}

            self._transition(db, project, ProjectState.RESEARCHED, {"node": "workspace_context_load"})

            trend = self._agent(TrendDiscoveryAgent(), db, project, {"workspace_context": workspace_context})
            monetization = self._agent(
                self._monetization_agent(),
                db,
                project,
                {"workspace_context": workspace_context, "trend": trend},
            )
            project.metadata_json = {**project.metadata_json, "trend": trend, "monetization_strategy": monetization}
            self._transition(db, project, ProjectState.TOPIC_REVIEW, {"trend": trend})

            topic_review = self._authority(
                db,
                project,
                "TOPIC_ANGLE_GATE",
                workspace_context,
                compiled,
                {"trend": trend, "monetization": monetization},
                mode_result.selected_mode,
            )
            self._transition(db, project, ProjectState.TOPIC_APPROVED, topic_review)
            self._transition(db, project, ProjectState.COST_AND_URGENCY_CHECK, mode_result.model_dump())
            if mode_result.requires_human_preapproval:
                task = self.review_service.create_task(
                    db,
                    company_id=project.company_id,
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    task_type=ReviewTaskType.PRE_SPEND.value,
                    title="Pre-spend approval",
                    payload_json={
                        "workflow_mode_result": mode_result.model_dump(),
                        "reason": "Mode requires human approval before spend-heavy script/media/render steps.",
                    },
                )
                db.flush()
                self._transition(db, project, ProjectState.WAITING_HUMAN_PRE_APPROVAL, {"review_task_id": task.id})
                db.commit()
                db.refresh(task)
                return task
            self._transition(db, project, ProjectState.PRE_PRODUCTION_PACK_CREATED, {})

        script = self._agent(
            self._script_agent(),
            db,
            project,
            {"workspace_context": workspace_context, "monetization_strategy": monetization},
        )
        self._transition(db, project, ProjectState.SCRIPT_DRAFTED, script)
        critic = self._agent(ScriptCriticAgent(), db, project, {"workspace_context": workspace_context, "script": script})
        self._transition(db, project, ProjectState.SCRIPT_CRITIQUED, critic)
        self._transition(db, project, ProjectState.SCRIPT_REVIEW, {})
        script_review = self._authority(
            db,
            project,
            "SCRIPT_GATE",
            workspace_context,
            compiled,
            {"script": script, "critic": critic},
            mode_result.selected_mode,
        )
        self._transition(db, project, ProjectState.SCRIPT_APPROVED, script_review)

        storyboard = self._agent(StoryboardAgent(), db, project, {"script": script})
        self._transition(db, project, ProjectState.STORYBOARD_CREATED, storyboard)
        prompts = self._agent(PromptEngineerAgent(), db, project, {"storyboard": storyboard})
        self._transition(db, project, ProjectState.PROMPTS_CREATED, prompts)
        asset_context = self._agent(AssetContextBuilder(), db, project, {"prompts": prompts})
        self._transition(db, project, ProjectState.ASSET_CONTEXT_CREATED, asset_context)
        reuse = self._agent(ReusableAssetRetrievalAgent(), db, project, asset_context)
        self._transition(db, project, ProjectState.REUSABLE_ASSET_SEARCHED, reuse)
        if reuse.get("selected_asset_id"):
            self._transition(db, project, ProjectState.ASSET_REUSED, reuse)
        else:
            image = self._agent(ImageGenerationAgent(), db, project, {"asset_context": asset_context})
            self._transition(db, project, ProjectState.ASSET_GENERATED, image)
        self._transition(db, project, ProjectState.MEDIA_GENERATED, {})

        voice = self._agent(VoiceoverAgent(), db, project, {"script": script})
        self._transition(db, project, ProjectState.VOICE_GENERATED, voice)
        subtitles = self._agent(SubtitleAgent(), db, project, {"script": script, "voice": voice})
        self._transition(db, project, ProjectState.SUBTITLE_CREATED, subtitles)

        render = self._agent(RenderAgent(), db, project, {"storyboard": storyboard, "voice": voice, "subtitles": subtitles})
        self._transition(db, project, ProjectState.LOWFI_RENDERED, render)
        self._transition(db, project, ProjectState.LOWFI_QA_PASSED, {})
        timeline = RenderTimeline(
            company_id=project.company_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            version="v1",
            timeline_json=render["render_timeline"],
            manifest_json={"project_manifest": "local://mock/render/project_manifest.json"},
        )
        db.add(timeline)
        db.flush()
        self._transition(db, project, ProjectState.RENDER_TIMELINE_SAVED, {"render_timeline_id": timeline.id})

        qa = self._agent(TitleAgent(), db, project, {"script": script})
        media_qa = self._agent_for_report(db, project, "qa", qa)
        self._transition(db, project, ProjectState.MEDIA_QA, media_qa)
        compliance = self._agent(self._compliance_agent(), db, project, {"render": render, "metadata": qa})
        db.add(
            ComplianceReport(
                company_id=project.company_id,
                workspace_id=project.workspace_id,
                project_id=project.id,
                status=compliance["decision"],
                report_json=compliance,
            )
        )
        self._transition(db, project, ProjectState.COMPLIANCE_CHECKED, compliance)
        final_review = self._authority(
            db,
            project,
            "FINAL_EDITORIAL_GATE",
            workspace_context,
            compiled,
            {"qa": media_qa, "compliance": compliance},
            mode_result.selected_mode,
        )
        self._transition(db, project, ProjectState.AUTHORITY_FINAL_REVIEW, final_review)

        seo = self._agent(self._seo_agent(), db, project, {"workspace_context": workspace_context, "script": script})
        publishing = self._agent(
            self._publishing_agent(),
            db,
            project,
            {"workspace_context": workspace_context, "script": script, "seo": seo},
        )
        db.add(
            VideoArtifact(
                company_id=project.company_id,
                workspace_id=project.workspace_id,
                project_id=project.id,
                artifact_type="seo_metadata",
                content_json={**seo, "publishing_content": publishing},
            )
        )
        self._transition(db, project, ProjectState.SEO_METADATA_CREATED, {**seo, "publishing_content": publishing})

        review_pack = self._agent(
            HumanReviewPackAgent(),
            db,
            project,
            {
                "seo": seo,
                "publishing_content": publishing,
                "timeline": render["render_timeline"],
                "authority_final_review": final_review,
            },
        )
        task = self.review_service.create_task(
            db,
            company_id=project.company_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_type=ReviewTaskType.FINAL_VIDEO.value,
            title="Final video review",
            payload_json=review_pack,
        )
        db.flush()
        self._transition(db, project, ProjectState.REVIEW_TASK_CREATED, {"review_task_id": task.id})
        self._transition(db, project, ProjectState.WAITING_HUMAN_REVIEW, {"review_task_id": task.id})
        db.commit()
        db.refresh(task)
        return task

    def _current_mode_result(self, project: VideoProject, workspace_context: dict) -> WorkflowModeResult:
        raw = project.metadata_json.get("workflow_mode_result")
        if raw:
            result = WorkflowModeResult.model_validate(raw)
            if project.workflow_mode:
                result.selected_mode = project.workflow_mode
            return result
        return self.mode_router.route(workspace_context, requested_mode=project.workflow_mode)

    def run_post_publish_diagnosis(self, db: Session, project: VideoProject) -> dict:
        workspace_context = self.context_service.get_context(db, project.workspace_id)
        constitution = self.compiler.get_active_or_compile(db, project.workspace_id)
        diagnosis = self._agent(AnalyticsAgent(), db, project, {"workspace_context": workspace_context})
        db.add(
            AuthorityReview(
                company_id=project.company_id,
                workspace_id=project.workspace_id,
                project_id=project.id,
                gate="POST_PUBLISH_DIAGNOSIS_GATE",
                decision_json=diagnosis,
            )
        )
        self._transition(db, project, ProjectState.AUTHORITY_POST_REVIEW, diagnosis)
        next_state = (
            ProjectState.PLAYBOOK_NO_CHANGE
            if diagnosis["decision"] == "CONTINUE_CURRENT_PLAYBOOK"
            else ProjectState.PLAYBOOK_MINOR_TUNING
        )
        self._transition(db, project, next_state, diagnosis)

        memories = self._agent(
            MemoryCuratorAgent(),
            db,
            project,
            {
                "workspace_context": workspace_context,
                "compiled_workspace_operational_constitution": constitution.content,
                "diagnosis": diagnosis,
            },
        )
        for memory in memories.get("memories", []):
            item_data = MemoryItemCreate.model_validate(memory)
            item = MemoryItem(**item_data.model_dump())
            db.add(item)
            db.flush()
            self.memory_router.embed_item(db, item)
        self._transition(db, project, ProjectState.MEMORY_UPDATED, memories)
        db.commit()
        return {"diagnosis": diagnosis, "memory_curator": memories}

    def mark_published(self, db: Session, project: VideoProject, uploaded_video: UploadedVideo) -> UploadedVideo:
        if project.current_state == ProjectState.HUMAN_APPROVED.value:
            self._transition(db, project, ProjectState.PRE_PUBLISH_REVERIFY, {})
        if project.current_state == ProjectState.PRE_PUBLISH_REVERIFY.value:
            self._transition(db, project, ProjectState.PUBLISHED, {"uploaded_video_id": uploaded_video.id})
        if project.current_state == ProjectState.PUBLISHED.value:
            self._transition(db, project, ProjectState.PUBLISH_DETECTED, {"uploaded_video_id": uploaded_video.id})
        db.commit()
        db.refresh(uploaded_video)
        return uploaded_video

    def record_analytics_state(self, db: Session, project: VideoProject, snapshot: AnalyticsSnapshot) -> None:
        next_state = {
            2: ProjectState.ANALYTICS_2H_COLLECTED,
            4: ProjectState.ANALYTICS_4H_COLLECTED,
            24: ProjectState.ANALYTICS_24H_COLLECTED,
            48: ProjectState.ANALYTICS_48H_COLLECTED,
            168: ProjectState.ANALYTICS_7D_COLLECTED,
        }.get(snapshot.hours_since_publish)
        if next_state:
            self._transition(db, project, next_state, {"analytics_snapshot_id": snapshot.id})

    def _agent(self, agent: Callable, db: Session, project: VideoProject, payload: dict[str, Any]) -> dict[str, Any]:
        return agent(
            db,
            company_id=project.company_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            payload=payload,
        )

    def _agent_for_report(self, db: Session, project: VideoProject, qa_type: str, evidence: dict[str, Any]) -> dict[str, Any]:
        from app.agents.mocks import MediaQAAgent

        result = self._agent(MediaQAAgent(), db, project, {"evidence": evidence})
        db.add(
            QAReport(
                company_id=project.company_id,
                workspace_id=project.workspace_id,
                project_id=project.id,
                qa_type=qa_type,
                score=result["score"],
                report_json=result,
            )
        )
        return result

    def _authority(
        self,
        db: Session,
        project: VideoProject,
        gate: str,
        workspace_context: dict,
        compiled_constitution: str,
        evidence: dict[str, Any],
        workflow_mode: str,
    ) -> dict[str, Any]:
        decision = self._agent(
            self._authority_agent(),
            db,
            project,
            {
                "gate": gate,
                "workspace_context": workspace_context,
                "compiled_workspace_operational_constitution": compiled_constitution,
                "task_specific_evidence": evidence,
                "workflow_mode": workflow_mode,
            },
        )
        db.add(
            AuthorityReview(
                company_id=project.company_id,
                workspace_id=project.workspace_id,
                project_id=project.id,
                gate=gate,
                decision_json=decision,
            )
        )
        return decision

    def _real_text_enabled(self) -> bool:
        return self.agent_runtime_mode in {"hybrid", "real_text"}

    def _authority_agent(self) -> Callable:
        return real_text_agents.AuthorityAgent() if self._real_text_enabled() else AuthorityAgent()

    def _script_agent(self) -> Callable:
        return real_text_agents.ScriptAgent() if self._real_text_enabled() else ScriptAgent()

    def _monetization_agent(self) -> Callable:
        return real_text_agents.MonetizationStrategyAgent() if self._real_text_enabled() else MonetizationStrategyAgent()

    def _seo_agent(self) -> Callable:
        return real_text_agents.SEOMetadataAgent() if self._real_text_enabled() else SEOMetadataAgent()

    def _publishing_agent(self) -> Callable:
        return real_text_agents.PublishingContentAgent() if self._real_text_enabled() else PublishingContentAgent()

    def _compliance_agent(self) -> Callable:
        return real_text_agents.ComplianceCopyrightAgent() if self._real_text_enabled() else ComplianceCopyrightAgent()

    def _transition(self, db: Session, project: VideoProject, next_state: ProjectState, event: dict[str, Any]) -> None:
        self.state_machine.transition(db, project, next_state.value, event)
