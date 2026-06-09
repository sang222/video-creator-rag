from typing import Any

from app.agents.base import Agent
from app.core.enums import ProjectState
from app.schemas.api import (
    AnalyticsDiagnosisResult,
    AuthorityDecision,
    ComplianceResult,
    MediaQAResult,
    MemoryCuratorResult,
    MonetizationStrategyResult,
    SalvageDecision,
    ScriptCriticResult,
)


class WorkspaceManagerAgent(Agent):
    name = "WorkspaceManagerAgent"
    node_name = "workspace_manager"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "workspace_loaded", "workspace_context_required": True}


class TrendDiscoveryAgent(Agent):
    name = "TrendDiscoveryAgent"
    node_name = "trend_discovery"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        niche = payload["workspace_context"].get("niche") or "creator education"
        return {"topic": f"Practical {niche} workflow that can validate buyer intent", "trend_active": True}


class MonetizationStrategyAgent(Agent):
    name = "MonetizationStrategyAgent"
    node_name = "monetization_strategy"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return MonetizationStrategyResult(
            monetization_hypothesis="Audience wants practical tooling recommendations with clear affiliate or YPP path.",
            target_metric="qualified CTA clicks and 24h retention",
            cta_strategy="soft CTA to a vetted tool/template after delivering standalone value",
            expected_revenue_path="affiliate first, YouTube ads after YPP, sponsorship later",
            buyer_intent_score=7.5,
        ).model_dump()


class TopicScoringAgent(Agent):
    name = "TopicScoringAgent"
    node_name = "topic_scoring"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"score": 8.0, "buyer_intent_score": 7.5, "brand_fit_score": 8.2, "policy_risk": "LOW"}


class AuthorityAgent(Agent):
    name = "AuthorityAgent"
    node_name = "authority_gate"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "workspace_context" not in payload or "compiled_workspace_operational_constitution" not in payload:
            return AuthorityDecision(
                decision="PASS_TO_HUMAN",
                monetization_passability_impact="NEGATIVE",
                revenue_impact="LOW",
                policy_risk="HIGH",
                brand_fit_score=0.0,
                audience_fit_score=0.0,
                buyer_intent_score=0.0,
                reasoning_summary=["Missing required workspace context or constitution."],
                instructions={"required": ["workspace_context", "compiled_workspace_operational_constitution"]},
            ).model_dump()
        gate = payload.get("gate", "TOPIC_ANGLE_GATE")
        decision = {
            "TOPIC_ANGLE_GATE": "APPROVE_TOPIC",
            "SCRIPT_GATE": "APPROVE_SCRIPT",
            "FINAL_EDITORIAL_GATE": "PASS_TO_HUMAN",
            "POST_PUBLISH_DIAGNOSIS_GATE": "CONTINUE_CURRENT_PLAYBOOK",
        }.get(gate, "REQUEST_MORE_RESEARCH")
        return AuthorityDecision(
            decision=decision,
            monetization_passability_impact="POSITIVE",
            revenue_impact="MEDIUM",
            policy_risk="LOW",
            brand_fit_score=8.5,
            audience_fit_score=8.0,
            buyer_intent_score=7.5,
            reasoning_summary=[
                "Supports monetization validation without premium spend.",
                "Preserves platform safety and channel brand voice.",
            ],
            instructions={"mode": payload.get("workflow_mode")},
            playbook_update_allowed=False,
        ).model_dump()


class ScriptAgent(Agent):
    name = "ScriptAgent"
    node_name = "script"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "script": "Hook: solve a real creator workflow problem. Body: compare reusable process steps. CTA: vetted tool/template.",
            "monetization_hypothesis": payload.get("monetization_strategy", {}).get("monetization_hypothesis"),
        }


class ScriptCriticAgent(Agent):
    name = "ScriptCriticAgent"
    node_name = "script_critic"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return ScriptCriticResult(
            decision="APPROVE",
            issues=[],
            monetization_alignment_score=8.0,
            policy_risk="LOW",
        ).model_dump()


class StoryboardAgent(Agent):
    name = "StoryboardAgent"
    node_name = "storyboard"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"scenes": [{"scene_id": "s01", "visual": "screen workflow mock", "duration": 4.0}]}


class PromptEngineerAgent(Agent):
    name = "PromptEngineerAgent"
    node_name = "prompt_engineer"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"prompts": [{"scene_id": "s01", "prompt": "clean educational creator workflow visual"}]}


class AssetContextBuilder(Agent):
    name = "AssetContextBuilder"
    node_name = "asset_context"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"asset_requests": [{"scene_id": "s01", "topic_cluster": "ai_workflow", "reuse_threshold": 0.78}]}


class ReusableAssetRetrievalAgent(Agent):
    name = "ReusableAssetRetrievalAgent"
    node_name = "reusable_asset_search"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"searched": True, "reuse_score": 0.2, "selected_asset_id": None, "action": "GENERATE_NEW_ASSET"}


class ImageGenerationAgent(Agent):
    name = "ImageGenerationAgent"
    node_name = "image_generation"
    estimated_cost = 0.02

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"asset_uri": "local://mock/assets/s01.png", "provider": "mock_image"}


class VoiceoverAgent(Agent):
    name = "VoiceoverAgent"
    node_name = "voiceover"
    estimated_cost = 0.01

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"audio_uri": "local://mock/audio/voiceover.wav", "provider": "mock_voice"}


class SubtitleAgent(Agent):
    name = "SubtitleAgent"
    node_name = "subtitle"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"subtitle_uri": "local://mock/subtitles/subtitles.srt"}


class RenderAgent(Agent):
    name = "RenderAgent"
    node_name = "render"
    estimated_cost = 0.01

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "render_timeline": {
                "version": "v1",
                "aspect_ratio": "9:16",
                "tracks": [
                    {"type": "video", "items": [{"scene_id": "s01", "uri": "local://mock/assets/s01.png"}]},
                    {"type": "audio", "items": [{"uri": "local://mock/audio/voiceover.wav"}]},
                    {"type": "subtitles", "items": [{"uri": "local://mock/subtitles/subtitles.srt"}]},
                ],
                "outputs": {
                    "render_timeline": "local://mock/render/render_timeline.json",
                    "project_manifest": "local://mock/render/project_manifest.json",
                    "final_render": "local://mock/render/final_render.mp4",
                },
            }
        }


class MediaQAAgent(Agent):
    name = "MediaQAAgent"
    node_name = "media_qa"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return MediaQAResult(decision="PASS", score=8.0, issues=[]).model_dump()


class ComplianceCopyrightAgent(Agent):
    name = "ComplianceCopyrightAgent"
    node_name = "compliance_check"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision": "PASS",
            "policy_risk": "LOW",
            "checklist": [
                {"item": "copyright risk", "status": "PASS", "notes": "Mock copyright risk is low."},
                {"item": "reused content risk", "status": "PASS", "notes": "Mock reused content risk is low."},
                {"item": "AI disclosure risk", "status": "PASS", "notes": "No material AI disclosure required in mock."},
                {"item": "misleading claims", "status": "PASS", "notes": "No unsupported claims in mock."},
                {"item": "platform safety", "status": "PASS", "notes": "No platform safety issue in mock."},
                {"item": "monetization eligibility risk", "status": "PASS", "notes": "Eligibility risk is low."},
            ],
            "issues": [],
            "required_fixes": [],
            "disclosure_required": False,
            "ai_disclosure_required": False,
            "affiliate_disclosure_required": False,
            "sponsorship_disclosure_required": False,
            "reused_content_risk": "LOW",
            "copyright_basis": "mock generated or reusable placeholder media",
            "copyright_risk_level": "LOW",
            "monetization_eligibility_risk": "LOW",
            "misleading_claims_risk": "LOW",
            "platform_safety_risk": "LOW",
            "policy_references": ["copyright originality", "platform monetization safety"],
            "required_disclosures": [],
        }


class TitleAgent(Agent):
    name = "TitleAgent"
    node_name = "title"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"title": "A Practical AI Workflow That Validates Buyer Intent"}


class SEOMetadataAgent(Agent):
    name = "SEOMetadataAgent"
    node_name = "seo_metadata"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "recommended_title": "A Practical AI Workflow That Validates Buyer Intent",
            "title": "A Practical AI Workflow That Validates Buyer Intent",
            "title_variants": [
                "A Practical AI Workflow That Validates Buyer Intent",
                "Validate Buyer Intent With This AI Creator Workflow",
            ],
            "description": "A monetization-safe creator workflow with practical value.",
            "hashtags": ["AItools", "CreatorWorkflow", "Productivity"],
            "keywords": ["AI creator tools", "workflow", "buyer intent"],
            "tags": ["AI creator tools", "workflow", "buyer intent"],
            "clickbait_risk": "LOW",
            "misleading_risk": "LOW",
        }


class PublishingContentAgent(Agent):
    name = "PublishingContentAgent"
    node_name = "publishing_content"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        seo = payload.get("seo", {})
        return {
            "final_title": seo.get("recommended_title") or seo.get("title") or "A Practical AI Workflow That Validates Buyer Intent",
            "final_description": seo.get("description") or "Practical creator workflow with a monetization-safe CTA.",
            "pinned_comment": "What part of this workflow would you reuse first?",
            "community_post": "New workflow breakdown is ready for review.",
            "short_description": "Practical creator workflow with a monetization-safe CTA.",
            "disclosure_note": "Add transparent disclosure if AI assistance or affiliate links are used.",
            "affiliate_disclosure_note": "Some links may be affiliate links when applicable.",
            "sponsorship_disclosure_note": None,
            "upload_checklist": ["title", "description", "pinned_comment", "disclosures"],
            "publish_ready": True,
            "authority_publish_decision": "NOT_ALLOWED",
        }


class HumanReviewPackAgent(Agent):
    name = "HumanReviewPackAgent"
    node_name = "human_review_pack"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"review_pack": payload, "recommended_action": "APPROVE"}


class SalvageStrategyAgent(Agent):
    name = "SalvageStrategyAgent"
    node_name = "salvage_strategy"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return SalvageDecision(
            action="CONVERT_TO_EVERGREEN",
            reason=["Mock Phase 1 salvage keeps reusable assets instead of discarding work."],
            next_state=ProjectState.CONVERTING_TO_EVERGREEN,
        ).model_dump()


class PublishDetectionAgent(Agent):
    name = "PublishDetectionAgent"
    node_name = "publish_detection"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"detected": True, "platform_video_id": payload.get("platform_video_id")}


class AnalyticsAgent(Agent):
    name = "AnalyticsAgent"
    node_name = "analytics"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return AnalyticsDiagnosisResult(
            decision="CONTINUE_CURRENT_PLAYBOOK",
            baseline_comparison={"above_baseline": True, "trend_active": True},
            recommendations=["NO_CHANGE"],
        ).model_dump()


class MemoryCuratorAgent(Agent):
    name = "MemoryCuratorAgent"
    node_name = "memory_curator"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_context = payload["workspace_context"]
        return MemoryCuratorResult(
            decision="STORE",
            memories=[
                {
                    "company_id": workspace_context["company_id"],
                    "workspace_id": workspace_context["workspace_id"],
                    "scope": "workspace_only",
                    "family": "monetization_memory",
                    "type": "workflow_learning",
                    "title": "Mock workflow completed",
                    "content": "Phase 1 mock workflow reached review/publication/diagnosis path.",
                    "summary": "Mock workflow learning recorded.",
                    "confidence": 0.5,
                    "sample_size": 1,
                    "source_video_ids": [],
                    "metadata_json": {},
                }
            ],
        ).model_dump()
