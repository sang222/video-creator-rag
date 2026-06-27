from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.contracts import (
    AssetReuseSearchRequest,
    BuildUploadCardsRequest,
    ContentDerivativeGraphEdgeCreate,
    CrossPlatformFunnelPackageCreate,
    DerivativeOriginalityCheckCreate,
    EventEnvelope,
    LLMRouteResponse,
    PromoteShortToLongCandidateCreate,
    ReusableArtifactCreate,
    ShortCandidateExtractRequest,
    ShortCandidateRankRequest,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import (
    AssetReuseIndexEntry,
    CaptionTrackSnapshot,
    ContentDerivativeGraphEdge,
    CrossPlatformFunnelPackage,
    DerivativeOriginalityCheck,
    DerivativeReleasePlan,
    HumanUploadTask,
    LLMModelProfile,
    LLMRouteAttempt,
    LLMRouterLane,
    LLMRouterProfile,
    LLMRunSnapshot,
    ProviderAttempt,
    ReusableArtifact,
    ShortCandidate,
    ShortCandidateScore,
    ShortRenderPlan,
    UploadCard,
    UploadedVideo,
    VideoProject,
    VisualPlanSnapshot,
    VoiceTimelineSnapshot,
    PromoteShortToLongCandidate,
)
from app.providers.base import ProviderResponse
from app.providers.ollama import OllamaChatRequest, OllamaLLMProvider
from app.services.domain_events import DomainEventBus


def _model_from_env(env_var: str, default: str) -> str:
    return os.getenv(env_var, default)


FINAL_LANES: list[dict[str, Any]] = [
    {
        "lane_name": "cheap_structured",
        "lane_description": "Low-cost structured JSON, metadata, small classification, repair and validation text.",
        "allowed_task_types": ["json_schema_output", "metadata_generation", "small_classification", "repair_validation"],
        "primary_model": _model_from_env("VCOS_LLM_MODEL_CHEAP_STRUCTURED_PRIMARY", "gpt-oss:20b-cloud"),
        "fallback_models": [_model_from_env("VCOS_LLM_MODEL_CHEAP_STRUCTURED_FALLBACK", "qwen3.5:cloud")],
        "cost_tier": "LOW",
        "latency_tier": "FAST",
        "route_priority": 10,
    },
    {
        "lane_name": "default_multimodal",
        "lane_description": "General multimodal reasoning and non-critical creative checks.",
        "allowed_task_types": ["multimodal_reasoning", "creative_check"],
        "primary_model": _model_from_env("VCOS_LLM_MODEL_DEFAULT_MULTIMODAL_PRIMARY", "qwen3.5:cloud"),
        "fallback_models": [_model_from_env("VCOS_LLM_MODEL_DEFAULT_MULTIMODAL_FALLBACK", "gemma4:31b-cloud")],
        "cost_tier": "MEDIUM",
        "latency_tier": "NORMAL",
        "route_priority": 20,
    },
    {
        "lane_name": "visual_creative_review",
        "lane_description": "Visual plan, scene concept, creative consistency, and thumbnail direction review.",
        "allowed_task_types": ["visual_plan_review", "scene_concept_review", "thumbnail_direction_review"],
        "primary_model": _model_from_env("VCOS_LLM_MODEL_VISUAL_CREATIVE_REVIEW_PRIMARY", "minimax-m3:cloud"),
        "fallback_models": [_model_from_env("VCOS_LLM_MODEL_VISUAL_CREATIVE_REVIEW_FALLBACK", "qwen3.5:cloud")],
        "emergency_model": _model_from_env("VCOS_LLM_MODEL_VISUAL_CREATIVE_REVIEW_EMERGENCY", "gemma4:31b-cloud"),
        "cost_tier": "MEDIUM",
        "latency_tier": "NORMAL",
        "route_priority": 30,
    },
    {
        "lane_name": "long_context_text",
        "lane_description": "Long-form script outline, generation, synthesis, research-to-script, and deep rewrite/review.",
        "allowed_task_types": ["long_form_script", "long_context_synthesis", "research_pack_to_script", "deep_rewrite"],
        "primary_model": _model_from_env("VCOS_LLM_MODEL_LONG_CONTEXT_TEXT_PRIMARY", "deepseek-v4-flash:cloud"),
        "fallback_models": [_model_from_env("VCOS_LLM_MODEL_LONG_CONTEXT_TEXT_FALLBACK", "nemotron-3-super:cloud")],
        "premium_model": _model_from_env("VCOS_LLM_MODEL_LONG_CONTEXT_TEXT_PREMIUM", "deepseek-v4-flash:cloud"),
        "cost_tier": "MEDIUM",
        "latency_tier": "NORMAL",
        "route_priority": 40,
    },
    {
        "lane_name": "engineering_architect",
        "lane_description": "Internal engineering design review, code architecture reasoning, test planning, and implementation prompts.",
        "allowed_task_types": ["engineering_design_review", "code_architecture", "test_planning", "implementation_prompt"],
        "primary_model": _model_from_env("VCOS_LLM_MODEL_ENGINEERING_ARCHITECT_PRIMARY", "qwen3-coder:480b-cloud"),
        "fallback_models": [_model_from_env("VCOS_LLM_MODEL_ENGINEERING_ARCHITECT_FALLBACK", "kimi-k2.7-code:cloud")],
        "backup_model": _model_from_env("VCOS_LLM_MODEL_ENGINEERING_ARCHITECT_BACKUP", "deepseek-v4-flash:cloud"),
        "cost_tier": "HIGH",
        "latency_tier": "SLOW",
        "critical_path_allowed": False,
        "route_priority": 50,
    },
    {
        "lane_name": "gatekeeper_soft_review",
        "lane_description": "Policy/compliance, monetization risk, factuality/risk, and final content soft review.",
        "allowed_task_types": ["policy_soft_review", "monetization_risk_review", "script_risk_review", "factuality_review"],
        "primary_model": _model_from_env("VCOS_LLM_MODEL_GATEKEEPER_SOFT_REVIEW_PRIMARY", "nemotron-3-super:cloud"),
        "fallback_models": [_model_from_env("VCOS_LLM_MODEL_GATEKEEPER_SOFT_REVIEW_FALLBACK", "deepseek-v4-flash:cloud")],
        "premium_model": _model_from_env("VCOS_LLM_MODEL_GATEKEEPER_SOFT_REVIEW_PREMIUM", "deepseek-v4-flash:cloud"),
        "cost_tier": "HIGH",
        "latency_tier": "NORMAL",
        "route_priority": 60,
    },
]

AGENT_ROUTER_MAPPING: dict[str, list[str]] = {
    "ChannelAuthorityAgent": ["cheap_structured", "long_context_text"],
    "TopicIdeaScoringAgent": ["cheap_structured"],
    "ResearchPackSummarizer": ["long_context_text"],
    "ScriptPlanningAgent": ["long_context_text"],
    "ScriptWriterAgent": ["long_context_text"],
    "ScriptRewriteAgent": ["long_context_text"],
    "PublishingMetadataAgent": ["cheap_structured"],
    "VisualPlanningAgent": ["visual_creative_review", "long_context_text"],
    "ThumbnailBriefAgent": ["visual_creative_review"],
    "GatekeeperSoftReviewAgent": ["gatekeeper_soft_review"],
    "LearningCandidateService": ["cheap_structured"],
    "EvidenceBundleSummarizer": ["cheap_structured", "long_context_text"],
    "PostPublishSummaryAgent": ["cheap_structured"],
    "EngineeringArchitectAgent": ["engineering_architect"],
    "ShortCandidateExtractor": ["cheap_structured", "long_context_text"],
    "ShortCandidateRanker": ["cheap_structured"],
    "DerivativeOriginalityReviewer": ["gatekeeper_soft_review", "cheap_structured"],
    "RecoveryProposalReviewer": ["gatekeeper_soft_review"],
    "LocalizationSubtitleAgent": ["long_context_text"],
    "LocalizedMetadataAgent": ["cheap_structured"],
    "PublishTimingSummaryAgent": ["cheap_structured"],
    "ProviderReadinessSummaryAgent": ["cheap_structured"],
    "MediaQCExplanationAgent": ["cheap_structured"],
    "RightsDisclosureReviewer": ["gatekeeper_soft_review"],
    "UploadCardCopyAgent": ["cheap_structured"],
}


def configured_router_models() -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for lane in FINAL_LANES:
        lane_models = [
            lane["primary_model"],
            *lane.get("fallback_models", []),
            lane.get("premium_model"),
            lane.get("emergency_model"),
            lane.get("backup_model"),
        ]
        for model_id in lane_models:
            if model_id and model_id not in seen:
                seen.add(model_id)
                models.append(model_id)
    _assert_no_forbidden_model(models)
    return models


@dataclass(frozen=True)
class _ModelChoice:
    model_id: str
    fallback_level: str


class LLMRouterConfigLoader:
    def __init__(self, session: Session):
        self.session = session

    def ensure_default_profile(self, *, profile_key: str | None = None) -> LLMRouterProfile:
        profile_key = profile_key or os.getenv("VCOS_LLM_ROUTER_PROFILE", "default")
        base_url = get_settings().ollama_base_url
        real_enabled = _env_bool("VCOS_LLM_REAL_EXECUTION_ENABLED", False)
        provider = os.getenv("VCOS_LLM_PROVIDER", "ollama").lower()
        if provider != "ollama":
            raise ValidationFailureError("M10.1 only allows the Ollama LLM provider.")
        profile = self.session.scalars(select(LLMRouterProfile).where(LLMRouterProfile.profile_key == profile_key)).one_or_none()
        if profile is None:
            profile = LLMRouterProfile(
                profile_key=profile_key,
                provider_key="OLLAMA",
                base_url=base_url,
                real_execution_enabled=real_enabled,
                default_timeout_seconds=30,
            )
            self.session.add(profile)
            self.session.flush()
            _record_m10_1_event(
                self.session,
                event_type="llm_router_profile.created",
                aggregate_type="llm_router_profile",
                aggregate_id=profile.id,
                company_id=None,
                correlation_id="m10-1-llm-router-seed",
                reason_code="LLM_ROUTER_PROFILE_CREATED",
                payload={"profile_key": profile.profile_key, "provider_key": profile.provider_key},
            )
        else:
            profile.base_url = base_url
            profile.real_execution_enabled = real_enabled
        self._ensure_lanes(profile, real_enabled=real_enabled)
        self._ensure_model_profiles()
        self.session.flush()
        return profile

    def list_profiles(self) -> list[LLMRouterProfile]:
        self.ensure_default_profile()
        return list(self.session.scalars(select(LLMRouterProfile).order_by(LLMRouterProfile.profile_key)).all())

    def get_profile(self, profile_key: str) -> LLMRouterProfile:
        self.ensure_default_profile(profile_key=profile_key)
        profile = self.session.scalars(select(LLMRouterProfile).where(LLMRouterProfile.profile_key == profile_key)).one_or_none()
        if profile is None:
            raise NotFoundError(f"LLM router profile not found: {profile_key}")
        return profile

    def list_lanes(self, *, profile_key: str = "default") -> list[LLMRouterLane]:
        profile = self.ensure_default_profile(profile_key=profile_key)
        return list(
            self.session.scalars(
                select(LLMRouterLane).where(LLMRouterLane.router_profile_id == profile.id).order_by(LLMRouterLane.route_priority)
            ).all()
        )

    def require_lane(self, *, profile_key: str, lane_name: str) -> tuple[LLMRouterProfile, LLMRouterLane]:
        profile = self.ensure_default_profile(profile_key=profile_key)
        lane = self.session.scalars(
            select(LLMRouterLane)
            .where(LLMRouterLane.router_profile_id == profile.id)
            .where(LLMRouterLane.lane_name == lane_name)
        ).one_or_none()
        if lane is None:
            raise NotFoundError(f"LLM router lane not found: {lane_name}")
        _assert_no_forbidden_model([lane.primary_model, *lane.fallback_models, lane.premium_model, lane.emergency_model, lane.backup_model])
        return profile, lane

    def _ensure_lanes(self, profile: LLMRouterProfile, *, real_enabled: bool) -> None:
        existing = {
            lane.lane_name: lane
            for lane in self.session.scalars(select(LLMRouterLane).where(LLMRouterLane.router_profile_id == profile.id)).all()
        }
        expected_names = {lane["lane_name"] for lane in FINAL_LANES}
        for stale_name, stale_lane in list(existing.items()):
            if stale_name not in expected_names:
                self.session.delete(stale_lane)
        for lane_def in FINAL_LANES:
            _assert_no_forbidden_model(
                [
                    lane_def["primary_model"],
                    *lane_def.get("fallback_models", []),
                    lane_def.get("premium_model"),
                    lane_def.get("emergency_model"),
                    lane_def.get("backup_model"),
                ]
            )
            lane = existing.get(lane_def["lane_name"])
            values = {
                "lane_description": lane_def["lane_description"],
                "allowed_task_types": lane_def["allowed_task_types"],
                "primary_model": lane_def["primary_model"],
                "fallback_models": lane_def.get("fallback_models", []),
                "premium_model": lane_def.get("premium_model"),
                "emergency_model": lane_def.get("emergency_model"),
                "backup_model": lane_def.get("backup_model"),
                "max_input_tokens": lane_def.get("max_input_tokens"),
                "max_output_tokens": lane_def.get("max_output_tokens"),
                "cost_tier": lane_def["cost_tier"],
                "latency_tier": lane_def["latency_tier"],
                "critical_path_allowed": lane_def.get("critical_path_allowed", False),
                "requires_human_approval_for_premium": lane_def.get("requires_human_approval_for_premium", True),
                "route_priority": lane_def["route_priority"],
                "real_execution_enabled": real_enabled,
            }
            if lane is None:
                lane = LLMRouterLane(router_profile_id=profile.id, lane_name=lane_def["lane_name"], **values)
                self.session.add(lane)
                self.session.flush()
                _record_m10_1_event(
                    self.session,
                    event_type="llm_router_lane.created",
                    aggregate_type="llm_router_lane",
                    aggregate_id=lane.id,
                    company_id=None,
                    correlation_id="m10-1-llm-router-seed",
                    reason_code="LLM_ROUTER_LANE_CREATED",
                    payload={"lane_name": lane.lane_name, "primary_model": lane.primary_model},
                )
            else:
                for key, value in values.items():
                    setattr(lane, key, value)

    def _ensure_model_profiles(self) -> None:
        lane_by_model: dict[str, set[str]] = {}
        roles: dict[str, str] = {}
        for lane in FINAL_LANES:
            roles.setdefault(lane["primary_model"], "PRIMARY")
            lane_by_model.setdefault(lane["primary_model"], set()).add(lane["lane_name"])
            for model in lane.get("fallback_models", []):
                roles.setdefault(model, "FALLBACK")
                lane_by_model.setdefault(model, set()).add(lane["lane_name"])
            for role_key in ["premium_model", "emergency_model", "backup_model"]:
                model = lane.get(role_key)
                if model:
                    roles.setdefault(model, role_key.replace("_model", "").upper())
                    lane_by_model.setdefault(model, set()).add(lane["lane_name"])
        for model_id, lane_names in sorted(lane_by_model.items()):
            _assert_no_forbidden_model([model_id])
            profile = self.session.scalars(
                select(LLMModelProfile).where(LLMModelProfile.provider_key == "OLLAMA").where(LLMModelProfile.model_id == model_id)
            ).one_or_none()
            values = {
                "model_role": roles[model_id],
                "lane_names": sorted(lane_names),
                "is_enabled": True,
                "critical_path_allowed": False,
                "notes": "M10.1 router catalog model profile.",
            }
            if profile is None:
                self.session.add(LLMModelProfile(provider_key="OLLAMA", model_id=model_id, **values))
            else:
                for key, value in values.items():
                    setattr(profile, key, value)


class LLMRouterService:
    def __init__(self, session: Session, provider: OllamaLLMProvider | None = None):
        self.session = session
        self.provider = provider

    def route(
        self,
        *,
        lane_name: str,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        requested_task_type: str | None = None,
        response_format: str = "text",
        profile_key: str = "default",
        correlation_id: str = "m10-1-llm-router",
    ) -> LLMRouteResponse:
        if prompt is None and messages is None:
            raise ValidationFailureError("LLM route requires either prompt or chat messages.")
        profile, lane = LLMRouterConfigLoader(self.session).require_lane(profile_key=profile_key, lane_name=lane_name)
        request_payload = {
            "lane_name": lane_name,
            "requested_task_type": requested_task_type,
            "prompt": prompt,
            "messages": messages,
            "response_format": response_format,
            "profile_key": profile.profile_key,
        }
        request_hash = _hash_payload(request_payload)
        if not self._real_execution_allowed(profile, lane):
            llm_run = _create_llm_run_snapshot(
                self.session,
                profile=profile,
                lane=lane,
                selected_model=lane.primary_model,
                request_payload=request_payload,
                output_payload={"skipped": True, "reason_code": "OLLAMA_REAL_EXECUTION_DISABLED"},
                status="SKIPPED",
                run_mode="REAL_DISABLED",
                correlation_id=correlation_id,
            )
            route_attempt = _create_route_attempt(
                self.session,
                profile=profile,
                lane=lane,
                selected_model=lane.primary_model,
                fallback_level="PRIMARY",
                request_hash=request_hash,
                response_payload={"skipped": True},
                status="SKIPPED",
                requested_task_type=requested_task_type,
                provider_attempt=None,
                llm_run=llm_run,
                error_code="OLLAMA_REAL_EXECUTION_DISABLED",
                error_message="Real Ollama execution is disabled by environment/profile/lane guard.",
            )
            return LLMRouteResponse(
                status="SKIPPED",
                lane_name=lane.lane_name,
                selected_model=lane.primary_model,
                fallback_level="PRIMARY",
                content=None,
                structured_output=None,
                route_attempt_id=route_attempt.id,
                provider_attempt_id=None,
                llm_run_snapshot_id=llm_run.id,
                reason_codes=["OLLAMA_REAL_EXECUTION_DISABLED"],
            )

        provider = self.provider or OllamaLLMProvider(base_url=profile.base_url, timeout_seconds=profile.default_timeout_seconds)
        last_response: ProviderResponse | None = None
        for choice in _model_choices(lane):
            response = provider.chat(
                request=OllamaChatRequest(model=choice.model_id, prompt=prompt, messages=messages, response_format=response_format)
            )
            status = "SUCCESS" if response.ok else "FAILED"
            provider_attempt = _create_provider_attempt(
                self.session,
                provider_key="OLLAMA",
                operation_key="llm_router.chat",
                target_type="llm_router_lane",
                target_id=lane.id,
                response=response,
                model_id=choice.model_id,
                correlation_id=correlation_id,
                router_lane=lane.lane_name,
                request_hash=request_hash,
            )
            llm_run = _create_llm_run_snapshot(
                self.session,
                profile=profile,
                lane=lane,
                selected_model=choice.model_id,
                request_payload=request_payload,
                output_payload=response.output if response.ok else {"error_code": response.error_code},
                status=status,
                run_mode="REAL",
                correlation_id=correlation_id,
                provider_attempt=provider_attempt,
            )
            route_attempt = _create_route_attempt(
                self.session,
                profile=profile,
                lane=lane,
                selected_model=choice.model_id,
                fallback_level=choice.fallback_level,
                request_hash=request_hash,
                response_payload=response.output if response.ok else None,
                status=status,
                requested_task_type=requested_task_type,
                provider_attempt=provider_attempt,
                llm_run=llm_run,
                error_code=response.error_code,
                error_message=response.error_message,
            )
            last_response = response
            if response.ok:
                reason_codes = ["LLM_ROUTE_ATTEMPT_CREATED"]
                if choice.fallback_level != "PRIMARY":
                    reason_codes.append("LLM_ROUTE_FALLBACK_USED")
                return LLMRouteResponse(
                    status="SUCCESS",
                    lane_name=lane.lane_name,
                    selected_model=choice.model_id,
                    fallback_level=choice.fallback_level,
                    content=response.output.get("content"),
                    structured_output=response.output.get("json"),
                    route_attempt_id=route_attempt.id,
                    provider_attempt_id=provider_attempt.id,
                    llm_run_snapshot_id=llm_run.id,
                    reason_codes=reason_codes,
                )
        assert last_response is not None
        return LLMRouteResponse(
            status="FAILED",
            lane_name=lane.lane_name,
            selected_model=choice.model_id,
            fallback_level=choice.fallback_level,
            content=None,
            structured_output=None,
            route_attempt_id=route_attempt.id,
            provider_attempt_id=provider_attempt.id,
            llm_run_snapshot_id=llm_run.id,
            reason_codes=["LLM_ROUTE_ATTEMPT_CREATED"],
        )

    def run_smoke_test(self, *, profile_key: str = "default") -> dict[str, Any]:
        profile = LLMRouterConfigLoader(self.session).ensure_default_profile(profile_key=profile_key)
        if not _env_bool("VCOS_LLM_ROUTER_REAL_SMOKE", False):
            return {
                "status": "SKIPPED",
                "real_smoke_enabled": False,
                "reason_codes": ["OLLAMA_REAL_EXECUTION_DISABLED"],
                "next_action": "Set VCOS_LLM_ROUTER_REAL_SMOKE=true and VCOS_LLM_REAL_EXECUTION_ENABLED=true to run local Ollama smoke.",
            }
        provider = self.provider or OllamaLLMProvider(base_url=profile.base_url, timeout_seconds=profile.default_timeout_seconds)
        health = provider.list_models()
        if not health.ok:
            return {
                "status": "BLOCKED",
                "real_smoke_enabled": True,
                "health_check": {"ok": False, "error_code": health.error_code, "error_message": health.error_message},
                "reason_codes": ["OLLAMA_REAL_SMOKE_BLOCKED"],
                "next_action": "Start local Ollama and ensure required models are available. VCOS does not auto-pull models.",
            }
        cheap = self.route(
            lane_name="cheap_structured",
            requested_task_type="smoke_json",
            prompt='Return JSON exactly like {"ok": true, "lane": "cheap_structured"}.',
            response_format="json",
            profile_key=profile_key,
            correlation_id="m10-1-ollama-smoke-cheap",
        )
        long_context = self.route(
            lane_name="long_context_text",
            requested_task_type="smoke_text",
            prompt="Reply with one short sentence confirming the long context lane is reachable.",
            response_format="text",
            profile_key=profile_key,
            correlation_id="m10-1-ollama-smoke-long-context",
        )
        route_attempt_ids = [cheap.route_attempt_id, long_context.route_attempt_id]
        status = "SUCCESS" if cheap.status == "SUCCESS" and long_context.status == "SUCCESS" else "FAILED"
        return {
            "status": status,
            "real_smoke_enabled": True,
            "health_check": {"ok": True, "model_count": len(health.output.get("models", []))},
            "cheap_structured": cheap.model_dump(mode="json"),
            "long_context_text": long_context.model_dump(mode="json"),
            "fallback_probe": {"covered_by_unit_test": True},
            "route_attempt_ids": route_attempt_ids,
            "reason_codes": ["OLLAMA_REAL_SMOKE_PASSED"] if status == "SUCCESS" else ["OLLAMA_REAL_SMOKE_BLOCKED"],
        }

    def _real_execution_allowed(self, profile: LLMRouterProfile, lane: LLMRouterLane) -> bool:
        return (
            _env_bool("VCOS_LLM_REAL_EXECUTION_ENABLED", False)
            and profile.real_execution_enabled
            and lane.real_execution_enabled
            and os.getenv("VCOS_LLM_PROVIDER", "ollama").lower() == "ollama"
        )


class ShortCandidateExtractionService:
    def __init__(self, session: Session):
        self.session = session

    def extract_for_project(
        self,
        *,
        video_project_id: uuid.UUID,
        data: ShortCandidateExtractRequest,
        correlation_id: str = "m10-1-short-extract",
    ) -> list[ShortCandidate]:
        project = _require_project(self.session, video_project_id)
        voice = _latest_voice_timeline(self.session, video_project_id)
        captions = _latest_caption_track(self.session, video_project_id)
        visual = _latest_visual_plan(self.session, video_project_id)
        if voice is None:
            raise ValidationFailureError("Short candidate extraction requires a stored VoiceTimelineSnapshot.")
        segments = _timeline_segments(voice)
        if not segments:
            raise ValidationFailureError("Voice timeline has no narration segments.")
        caption_ids_by_segment = _caption_ids_by_segment(captions)
        candidates: list[ShortCandidate] = []
        used_ranges: set[tuple[int, int]] = set()
        for window in _candidate_windows(segments, max_candidates=data.max_candidates):
            start_ms = int(window[0]["estimated_start_time"] * 1000)
            end_ms = int(window[-1]["estimated_end_time"] * 1000)
            if (start_ms, end_ms) in used_ranges:
                continue
            duration_ms = end_ms - start_ms
            if duration_ms <= 0 or duration_ms >= 59000:
                continue
            text = " ".join(str(segment.get("text") or "") for segment in window).strip()
            caption_ids = [
                caption_id
                for segment in window
                for caption_id in caption_ids_by_segment.get(str(segment.get("narration_segment_id")), [])
            ]
            enhancement = None
            if data.use_llm_enhancement:
                enhancement = LLMRouterService(self.session).route(
                    lane_name="cheap_structured",
                    requested_task_type="short_candidate_summary",
                    prompt=f"Summarize this candidate as JSON with hook_line/core_idea/standalone_summary: {text}",
                    response_format="json",
                    correlation_id="m10-1-short-extract-optional-llm",
                )
            candidate = ShortCandidate(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                parent_video_project_id=project.id,
                parent_voice_timeline_id=voice.id,
                parent_caption_track_id=captions.id if captions else None,
                parent_visual_plan_id=visual.id if visual else None,
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                duration_ms=duration_ms,
                caption_ids=caption_ids,
                core_idea=_enhanced_value(enhancement, "core_idea") or _core_idea(text),
                hook_line=_enhanced_value(enhancement, "hook_line") or _hook_line(text),
                standalone_summary=_enhanced_value(enhancement, "standalone_summary") or _standalone_summary(text),
                suggested_title=_title_from_text(text),
                overlay_text=_overlay_text(text),
                crop_strategy="VERTICAL_9_16",
                visual_source=_visual_source_for_window(visual, window),
                candidate_state="GENERATED" if 20_000 <= duration_ms <= 45_000 else "NEEDS_REWRITE",
                policy_risk_level="LOW",
                rights_risk_level="LOW",
                production_cost_estimate={
                    "m10_1_estimate_only": True,
                    "media_provider_routing_deferred_to_m10_2": True,
                    "no_real_media_call": True,
                },
            )
            self.session.add(candidate)
            candidates.append(candidate)
            used_ranges.add((start_ms, end_ms))
            if len(candidates) >= data.max_candidates:
                break
        self.session.flush()
        for candidate in candidates:
            _record_m10_1_event(
                self.session,
                event_type="short_candidate.extracted",
                aggregate_type="short_candidate",
                aggregate_id=candidate.id,
                company_id=candidate.company_id,
                correlation_id=correlation_id,
                reason_code="SHORT_CANDIDATE_EXTRACTED",
                payload={"parent_video_project_id": str(project.id), "duration_ms": candidate.duration_ms},
            )
        return candidates

    def list_for_project(self, video_project_id: uuid.UUID) -> list[ShortCandidate]:
        return list(
            self.session.scalars(
                select(ShortCandidate).where(ShortCandidate.parent_video_project_id == video_project_id).order_by(ShortCandidate.created_at)
            ).all()
        )


class ShortCandidateRankingService:
    def __init__(self, session: Session):
        self.session = session

    def rank(self, *, short_candidate_id: uuid.UUID, data: ShortCandidateRankRequest) -> ShortCandidateScore:
        candidate = _require_short_candidate(self.session, short_candidate_id)
        text = f"{candidate.hook_line} {candidate.standalone_summary} {candidate.core_idea}".lower()
        duration_seconds = Decimal(candidate.duration_ms) / Decimal(1000)
        hook_strength = Decimal("14") if "?" in candidate.hook_line or len(candidate.hook_line.split()) <= 14 else Decimal("10")
        standalone_clarity = Decimal("16") if candidate.standalone_summary else Decimal("4")
        insight_density = Decimal("12") if any(word in text for word in ["how", "why", "framework", "workflow", "mistake"]) else Decimal("8")
        visual_punch = Decimal("10") if candidate.visual_source in {"PARENT_SCENE_REUSE", "PARENT_HERO_REUSE"} else Decimal("7")
        audience_relevance = Decimal("12")
        bridge_value = Decimal("8") if 20 <= duration_seconds <= 45 else Decimal("4")
        production_reuse_saving = Decimal("9") if candidate.visual_source.startswith("PARENT") else Decimal("5")
        context_dependency_penalty = Decimal("8") if any(word in text for word in ["this", "that", "as mentioned", "previous"]) else Decimal("0")
        policy_risk_penalty = Decimal("12") if candidate.policy_risk_level in {"HIGH", "BLOCKED"} else Decimal("0")
        generic_template_penalty = Decimal("5") if candidate.visual_source == "TEMPLATE_CARD" else Decimal("0")
        total = (
            hook_strength
            + standalone_clarity
            + insight_density
            + visual_punch
            + audience_relevance
            + bridge_value
            + production_reuse_saving
            - context_dependency_penalty
            - policy_risk_penalty
            - generic_template_penalty
        )
        score = ShortCandidateScore(
            short_candidate_id=candidate.id,
            hook_strength=hook_strength,
            standalone_clarity=standalone_clarity,
            insight_density=insight_density,
            visual_punch=visual_punch,
            audience_relevance=audience_relevance,
            bridge_value=bridge_value,
            production_reuse_saving=production_reuse_saving,
            context_dependency_penalty=context_dependency_penalty,
            policy_risk_penalty=policy_risk_penalty,
            generic_template_penalty=generic_template_penalty,
            total_score=total,
            score_version="m10.1.v1",
            explanation="Deterministic ShortValueScore with context, risk, and generic template penalties.",
        )
        self.session.add(score)
        candidate.candidate_state = "SELECTED_FOR_RENDER" if total >= data.select_threshold and candidate.policy_risk_level != "BLOCKED" else "REJECTED"
        if candidate.candidate_state == "REJECTED" and total >= Decimal("45"):
            candidate.candidate_state = "NEEDS_REWRITE"
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="short_candidate.scored",
            aggregate_type="short_candidate",
            aggregate_id=candidate.id,
            company_id=candidate.company_id,
            correlation_id="m10-1-short-rank",
            reason_code="SHORT_CANDIDATE_SCORED",
            payload={"score_id": str(score.id), "total_score": str(score.total_score), "candidate_state": candidate.candidate_state},
        )
        return score


class DerivativeOriginalityService:
    def __init__(self, session: Session):
        self.session = session

    def create_check(self, *, data: DerivativeOriginalityCheckCreate) -> DerivativeOriginalityCheck:
        company_id, channel_id = _resolve_originality_scope(self.session, data)
        result = _originality_result(data)
        summary = {
            "PASS": "Derivative has standalone value and sufficient new value evidence.",
            "REVIEW_REQUIRED": "Derivative needs human review before publish_allowed can be true.",
            "BLOCK": "Derivative is blocked by originality, policy, rights, or compilation rules.",
        }[result]
        check = DerivativeOriginalityCheck(
            company_id=company_id,
            channel_workspace_id=channel_id,
            content_derivative_edge_id=data.content_derivative_edge_id,
            short_candidate_id=data.short_candidate_id,
            derivative_type=data.derivative_type,
            standalone_value_ok=data.standalone_value_ok,
            new_value_added_ok=data.new_value_added_ok,
            reused_runtime_pct=data.reused_runtime_pct,
            template_repetition_risk=data.template_repetition_risk,
            generic_stock_risk=data.generic_stock_risk,
            commentary_or_context_added=data.commentary_or_context_added,
            policy_flags=data.policy_flags,
            rights_flags=data.rights_flags,
            result=result,
            operator_summary=summary,
            technical_appendix={
                **data.technical_appendix,
                "no_raw_compilation_publish": True,
                "follow_up_long_requires_new_arc": data.derivative_type == "FOLLOW_UP_LONG",
            },
        )
        self.session.add(check)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="derivative_originality_check.created",
            aggregate_type="derivative_originality_check",
            aggregate_id=check.id,
            company_id=check.company_id,
            correlation_id="m10-1-originality-check",
            reason_code={"PASS": "ORIGINALITY_CHECK_PASSED", "REVIEW_REQUIRED": "ORIGINALITY_CHECK_REVIEW_REQUIRED", "BLOCK": "ORIGINALITY_CHECK_BLOCKED"}[result],
            payload={"result": result, "derivative_type": check.derivative_type},
        )
        return check

    def require_check(self, check_id: uuid.UUID) -> DerivativeOriginalityCheck:
        check = self.session.get(DerivativeOriginalityCheck, check_id)
        if check is None:
            raise NotFoundError(f"derivative originality check not found: {check_id}")
        return check


class DerivativeGraphService:
    def __init__(self, session: Session):
        self.session = session

    def create_edge(self, *, data: ContentDerivativeGraphEdgeCreate) -> ContentDerivativeGraphEdge:
        company_id, channel_id = _resolve_edge_scope(self.session, data)
        publish_allowed = bool(
            data.new_value_added
            and data.derivative_type != "COMPILATION"
            and (data.originality_score is None or data.originality_score >= Decimal("60"))
            and data.policy_risk_level not in {"HIGH", "BLOCKED"}
            and data.rights_risk_level not in {"HIGH", "BLOCKED"}
        )
        edge = ContentDerivativeGraphEdge(
            company_id=company_id,
            channel_workspace_id=channel_id,
            parent_video_project_id=data.parent_video_project_id,
            parent_uploaded_video_id=data.parent_uploaded_video_id,
            derivative_video_project_id=data.derivative_video_project_id,
            derivative_uploaded_video_id=data.derivative_uploaded_video_id,
            derivative_type=data.derivative_type,
            transformation_summary=data.transformation_summary,
            new_value_added=data.new_value_added,
            originality_score=data.originality_score,
            reused_runtime_pct=data.reused_runtime_pct,
            publish_allowed=publish_allowed,
            policy_risk_level=data.policy_risk_level,
            rights_risk_level=data.rights_risk_level,
            source_refs=data.source_refs,
            technical_appendix={
                **data.technical_appendix,
                "publish_allowed_requires_originality_and_risk_pass": True,
                "uploaded_video_remains_canonical": True,
            },
        )
        self.session.add(edge)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="content_derivative_graph_edge.created",
            aggregate_type="content_derivative_graph_edge",
            aggregate_id=edge.id,
            company_id=edge.company_id,
            correlation_id="m10-1-derivative-edge",
            reason_code="DERIVATIVE_GRAPH_EDGE_CREATED",
            payload={"derivative_type": edge.derivative_type, "publish_allowed": edge.publish_allowed},
        )
        return edge

    def graph_for_project(self, video_project_id: uuid.UUID) -> list[ContentDerivativeGraphEdge]:
        return list(
            self.session.scalars(
                select(ContentDerivativeGraphEdge)
                .where(
                    or_(
                        ContentDerivativeGraphEdge.parent_video_project_id == video_project_id,
                        ContentDerivativeGraphEdge.derivative_video_project_id == video_project_id,
                    )
                )
                .order_by(ContentDerivativeGraphEdge.created_at)
            ).all()
        )

    def require_edge(self, edge_id: uuid.UUID) -> ContentDerivativeGraphEdge:
        edge = self.session.get(ContentDerivativeGraphEdge, edge_id)
        if edge is None:
            raise NotFoundError(f"derivative graph edge not found: {edge_id}")
        return edge


class ReusableArtifactService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, *, data: ReusableArtifactCreate) -> ReusableArtifact:
        source_provider = data.source_provider or ""
        source_provider_lower = source_provider.lower()
        blocked_manual_marketplace = "envato" in source_provider_lower
        blocked_automation = "api" in source_provider_lower
        if blocked_manual_marketplace and blocked_automation:
            raise ValidationFailureError("Manual stock sources are not automated providers in M10.1.")
        artifact = ReusableArtifact(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            artifact_type=data.artifact_type,
            content_hash=data.content_hash,
            source_provider=data.source_provider,
            license_status=data.license_status,
            rights_envelope_id=data.rights_envelope_id,
            reuse_scope=data.reuse_scope,
            reuse_count=0,
            max_reuse_policy=data.max_reuse_policy,
            cooldown_days=data.cooldown_days,
            last_used_video_ids=[],
            quality_score=data.quality_score,
            state=data.state,
        )
        self.session.add(artifact)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="reusable_artifact.created",
            aggregate_type="reusable_artifact",
            aggregate_id=artifact.id,
            company_id=artifact.company_id,
            correlation_id="m10-1-reusable-artifact",
            reason_code="REUSABLE_ARTIFACT_CREATED",
            payload={"artifact_type": artifact.artifact_type, "reuse_scope": artifact.reuse_scope, "license_status": artifact.license_status},
        )
        return artifact

    def list(self, *, company_id: uuid.UUID | None = None) -> list[ReusableArtifact]:
        statement = select(ReusableArtifact).order_by(ReusableArtifact.created_at)
        if company_id:
            statement = statement.where(ReusableArtifact.company_id == company_id)
        return list(self.session.scalars(statement).all())

    def require(self, artifact_id: uuid.UUID) -> ReusableArtifact:
        artifact = self.session.get(ReusableArtifact, artifact_id)
        if artifact is None:
            raise NotFoundError(f"reusable artifact not found: {artifact_id}")
        return artifact


class AssetReuseIndexService:
    def __init__(self, session: Session):
        self.session = session

    def search(self, *, data: AssetReuseSearchRequest) -> list[AssetReuseIndexEntry]:
        entries = self.session.scalars(
            select(AssetReuseIndexEntry)
            .where(AssetReuseIndexEntry.scene_requirement_hash == data.scene_requirement_hash)
            .where(AssetReuseIndexEntry.match_score >= data.min_match_score)
            .order_by(AssetReuseIndexEntry.match_score.desc())
        ).all()
        filtered: list[AssetReuseIndexEntry] = []
        for entry in entries:
            artifact = self.session.get(ReusableArtifact, entry.reusable_artifact_id)
            if artifact and artifact.company_id == data.company_id and (data.channel_workspace_id is None or artifact.channel_workspace_id == data.channel_workspace_id):
                filtered.append(entry)
        return filtered


class DerivativeReleasePlanService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_parent_project(self, *, video_project_id: uuid.UUID, selected_short_ids: list[uuid.UUID]) -> DerivativeReleasePlan:
        project = _require_project(self.session, video_project_id)
        plan = DerivativeReleasePlan(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            parent_video_project_id=project.id,
            max_shorts_per_long=min(3, len(selected_short_ids)),
            min_spacing_hours=24,
            preferred_publish_order=[{"short_candidate_id": str(candidate_id)} for candidate_id in selected_short_ids[:3]],
            platform_surface=["YOUTUBE_SHORTS"],
            bridge_strategy={"youtube_first": True, "no_auto_schedule": True},
            avoid_same_day_spam=True,
            release_state="READY_FOR_HUMAN_REVIEW" if selected_short_ids else "DRAFT",
        )
        self.session.add(plan)
        self.session.flush()
        return plan


class CrossPlatformFunnelPackageService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, *, data: CrossPlatformFunnelPackageCreate) -> CrossPlatformFunnelPackage:
        company_id, channel_id = _resolve_funnel_scope(self.session, data)
        selected_ids = [str(candidate_id) for candidate_id in data.selected_short_candidate_ids]
        for candidate_id in data.selected_short_candidate_ids:
            _require_short_candidate(self.session, candidate_id)
        package = CrossPlatformFunnelPackage(
            company_id=company_id,
            channel_workspace_id=channel_id,
            parent_video_project_id=data.parent_video_project_id,
            parent_uploaded_video_id=data.parent_uploaded_video_id,
            youtube_long_package_id=data.youtube_long_package_id,
            selected_short_candidate_ids=selected_ids,
            youtube_shorts_package_status="READY_FOR_UPLOAD_CARD" if selected_ids else "NO_SELECTED_SHORTS",
            tiktok_package_status="EXPORT_ONLY",
            facebook_reels_package_status="EXPORT_ONLY",
            bridge_strategy={
                **data.bridge_strategy,
                "youtube_first": True,
                "tiktok_facebook_export_only": True,
                "no_tiktok_facebook_analytics_learning": True,
            },
            package_state="READY_FOR_HUMAN_REVIEW" if selected_ids else "DRAFT",
        )
        self.session.add(package)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="cross_platform_funnel_package.created",
            aggregate_type="cross_platform_funnel_package",
            aggregate_id=package.id,
            company_id=package.company_id,
            correlation_id="m10-1-funnel-package",
            reason_code="CROSS_PLATFORM_FUNNEL_PACKAGE_CREATED",
            payload={"selected_short_count": len(selected_ids), "youtube_first": True},
        )
        return package

    def require(self, package_id: uuid.UUID) -> CrossPlatformFunnelPackage:
        package = self.session.get(CrossPlatformFunnelPackage, package_id)
        if package is None:
            raise NotFoundError(f"cross-platform funnel package not found: {package_id}")
        return package

    def build_upload_cards(self, *, package_id: uuid.UUID, data: BuildUploadCardsRequest) -> list[UploadCard]:
        package = self.require(package_id)
        cards: list[UploadCard] = []
        for short_id_text in package.selected_short_candidate_ids:
            candidate = _require_short_candidate(self.session, uuid.UUID(str(short_id_text)))
            for platform in data.platforms:
                card = UploadCardService(self.session).create_for_short_candidate(candidate=candidate, platform=platform)
                HumanUploadTaskService(self.session).create_for_card(card=card)
                cards.append(card)
        package.package_state = "READY_FOR_UPLOAD_TASKS" if cards else package.package_state
        self.session.flush()
        return cards


class UploadCardService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_short_candidate(self, *, candidate: ShortCandidate, platform: str) -> UploadCard:
        render_plan = _ensure_short_render_plan(self.session, candidate, platform=platform)
        card = UploadCard(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            platform=platform,
            video_project_id=candidate.parent_video_project_id,
            short_candidate_id=candidate.id,
            render_plan_id=render_plan.id,
            file_ref=None,
            title_internal=candidate.suggested_title or candidate.hook_line[:80],
            hook_line=candidate.hook_line,
            caption=candidate.standalone_summary,
            description=f"{candidate.standalone_summary}\n\nRecord actual platform metadata back into VCOS after manual upload.",
            hashtags=["#Shorts"] if platform == "YOUTUBE_SHORTS" else [],
            cta_type="SEARCH_YOUTUBE" if platform in {"TIKTOK", "FACEBOOK_REELS"} else "NONE",
            cta_text="Search the full video on YouTube." if platform in {"TIKTOK", "FACEBOOK_REELS"} else None,
            pinned_comment=None,
            ai_disclosure_required=False,
            ai_disclosure_reason=[],
            music_policy="SAFE_MODE",
            cover_frame_suggestion=candidate.overlay_text,
            human_notes=[
                {"note": "Manual upload only. If native platform copy changes, record actual value back into VCOS."},
                {"note": "TikTok/Facebook exports are support surfaces only in M10.1."},
            ],
            paste_back_required_fields=["actual_video_id", "actual_video_url", "actual_published_at", "actual_metadata"],
            card_state="READY",
        )
        self.session.add(card)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="upload_card.created",
            aggregate_type="upload_card",
            aggregate_id=card.id,
            company_id=card.company_id,
            correlation_id="m10-1-upload-card",
            reason_code="UPLOAD_CARD_CREATED",
            payload={"platform": card.platform, "manual_only": True},
        )
        return card

    def require(self, upload_card_id: uuid.UUID) -> UploadCard:
        card = self.session.get(UploadCard, upload_card_id)
        if card is None:
            raise NotFoundError(f"upload card not found: {upload_card_id}")
        return card


class HumanUploadTaskService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_card(self, *, card: UploadCard) -> HumanUploadTask:
        task = HumanUploadTask(
            company_id=card.company_id,
            channel_workspace_id=card.channel_workspace_id,
            upload_card_id=card.id,
            target_platform=card.platform,
            task_state="READY",
            required_checklist=[
                {"item": "Upload manually outside VCOS."},
                {"item": "Confirm rights/disclosure before publishing."},
                {"item": "Paste actual URL/video id back through existing UploadedVideo flow where supported."},
            ],
            actual_uploaded_video_id=None,
        )
        self.session.add(task)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="human_upload_task.created",
            aggregate_type="human_upload_task",
            aggregate_id=task.id,
            company_id=task.company_id,
            correlation_id="m10-1-human-upload-task",
            reason_code="HUMAN_UPLOAD_TASK_CREATED",
            payload={"target_platform": task.target_platform, "manual_only": True},
        )
        return task

    def list(self, *, task_state: str | None = None) -> list[HumanUploadTask]:
        statement = select(HumanUploadTask).order_by(HumanUploadTask.created_at)
        if task_state:
            statement = statement.where(HumanUploadTask.task_state == task_state)
        return list(self.session.scalars(statement).all())

    def require(self, task_id: uuid.UUID) -> HumanUploadTask:
        task = self.session.get(HumanUploadTask, task_id)
        if task is None:
            raise NotFoundError(f"human upload task not found: {task_id}")
        return task


class PromoteShortToLongCandidateService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, *, data: PromoteShortToLongCandidateCreate) -> PromoteShortToLongCandidate:
        company_id, channel_id, non_youtube = _resolve_promotion_scope(self.session, data)
        has_youtube_signal = bool(data.audience_signal.get("youtube") or data.audience_signal.get("youtube_metrics"))
        state = "READY_FOR_HUMAN_REVIEW" if has_youtube_signal and not non_youtube else "NEEDS_MORE_EVIDENCE"
        if non_youtube:
            state = "NEEDS_MORE_EVIDENCE"
        candidate = PromoteShortToLongCandidate(
            company_id=company_id,
            channel_workspace_id=channel_id,
            source_short_uploaded_video_id=data.source_short_uploaded_video_id,
            source_short_candidate_id=data.source_short_candidate_id,
            winning_hook=data.winning_hook,
            audience_signal={
                **data.audience_signal,
                "youtube_analytics_only_authority": True,
                "tiktok_facebook_analytics_loop_deferred": True,
            },
            suggested_long_topic=data.suggested_long_topic,
            suggested_outline=data.suggested_outline,
            expected_watch_hour_potential=data.expected_watch_hour_potential,
            confidence_label=data.confidence_label,
            risk_level=data.risk_level,
            state=state,
            evidence_refs=data.evidence_refs,
        )
        self.session.add(candidate)
        self.session.flush()
        _record_m10_1_event(
            self.session,
            event_type="promote_short_to_long_candidate.created",
            aggregate_type="promote_short_to_long_candidate",
            aggregate_id=candidate.id,
            company_id=candidate.company_id,
            correlation_id="m10-1-promote-short-to-long",
            reason_code="YOUTUBE_ANALYTICS_ONLY_AUTHORITY",
            payload={"state": candidate.state, "no_video_project_created": True},
        )
        return candidate

    def list(self) -> list[PromoteShortToLongCandidate]:
        return list(self.session.scalars(select(PromoteShortToLongCandidate).order_by(PromoteShortToLongCandidate.created_at)).all())

    def require(self, candidate_id: uuid.UUID) -> PromoteShortToLongCandidate:
        candidate = self.session.get(PromoteShortToLongCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"promote short to long candidate not found: {candidate_id}")
        return candidate


def _create_route_attempt(
    session: Session,
    *,
    profile: LLMRouterProfile,
    lane: LLMRouterLane,
    selected_model: str,
    fallback_level: str,
    request_hash: str,
    response_payload: dict[str, Any] | None,
    status: str,
    requested_task_type: str | None,
    provider_attempt: ProviderAttempt | None,
    llm_run: LLMRunSnapshot | None,
    error_code: str | None,
    error_message: str | None,
) -> LLMRouteAttempt:
    usage = _usage_from_payload(response_payload)
    route_attempt = LLMRouteAttempt(
        router_profile_id=profile.id,
        lane_name=lane.lane_name,
        requested_task_type=requested_task_type,
        selected_model=selected_model,
        fallback_level=fallback_level,
        request_hash=request_hash,
        response_hash=_hash_payload(response_payload) if response_payload is not None else None,
        status=status,
        error_code=error_code,
        error_message=error_message,
        prompt_eval_count=usage.get("prompt_eval_count"),
        eval_count=usage.get("eval_count"),
        total_duration_ms=usage.get("total_duration_ms"),
        load_duration_ms=usage.get("load_duration_ms"),
        prompt_eval_duration_ms=usage.get("prompt_eval_duration_ms"),
        eval_duration_ms=usage.get("eval_duration_ms"),
        provider_attempt_id=provider_attempt.id if provider_attempt else None,
        llm_run_snapshot_id=llm_run.id if llm_run else None,
    )
    session.add(route_attempt)
    session.flush()
    _record_m10_1_event(
        session,
        event_type="llm_route_attempt.created",
        aggregate_type="llm_route_attempt",
        aggregate_id=route_attempt.id,
        company_id=None,
        correlation_id="m10-1-llm-route-attempt",
        reason_code="LLM_ROUTE_ATTEMPT_CREATED",
        payload={"lane_name": lane.lane_name, "selected_model": selected_model, "status": status, "fallback_level": fallback_level},
    )
    return route_attempt


def _create_provider_attempt(
    session: Session,
    *,
    provider_key: str,
    operation_key: str,
    target_type: str,
    target_id: uuid.UUID,
    response: ProviderResponse,
    model_id: str,
    correlation_id: str,
    router_lane: str,
    request_hash: str,
) -> ProviderAttempt:
    response_hash = _hash_payload(response.output) if response.ok else None
    attempt = ProviderAttempt(
        provider_key=provider_key,
        operation_key=operation_key,
        target_type=target_type,
        target_id=target_id,
        attempt_number=1,
        status=_provider_attempt_status(response),
        error_code=response.error_code,
        error_message_redacted="redacted provider error" if response.error_code else None,
        started_at=utc_now(),
        finished_at=utc_now(),
        latency_ms=response.latency_ms,
        metadata_={
            "model_id": model_id,
            "router_lane": router_lane,
            "request_hash": request_hash,
            "response_hash": response_hash,
            "ollama_local_endpoint": True,
            "no_dollar_cost_reported": True,
            "response_usage": response.output.get("usage") if response.ok else {},
            "validation_outcome": "VCOS_VALIDATION_PENDING",
            "repair_outcome": "NOT_ATTEMPTED",
        },
    )
    session.add(attempt)
    session.flush()
    _record_m10_1_event(
        session,
        event_type="provider_attempt.created",
        aggregate_type="provider_attempt",
        aggregate_id=attempt.id,
        company_id=None,
        correlation_id=correlation_id,
        reason_code="LLM_ROUTE_ATTEMPT_CREATED",
        payload={"provider_key": provider_key, "operation_key": operation_key, "status": attempt.status, "model_id": model_id},
    )
    return attempt


def _create_llm_run_snapshot(
    session: Session,
    *,
    profile: LLMRouterProfile,
    lane: LLMRouterLane,
    selected_model: str,
    request_payload: dict[str, Any],
    output_payload: dict[str, Any] | None,
    status: str,
    run_mode: str,
    correlation_id: str,
    provider_attempt: ProviderAttempt | None = None,
) -> LLMRunSnapshot:
    usage = _usage_from_payload(output_payload)
    token_total = None
    if usage.get("prompt_eval_count") is not None or usage.get("eval_count") is not None:
        token_total = Decimal(str((usage.get("prompt_eval_count") or 0) + (usage.get("eval_count") or 0)))
    snapshot = LLMRunSnapshot(
        run_type=f"M10_1_LLM_ROUTER_{lane.lane_name.upper()}",
        provider="ollama",
        model_name=selected_model,
        provider_key=profile.provider_key,
        model_key=selected_model,
        run_mode=run_mode,
        prompt_template_key="m10_1_llm_router",
        prompt_template_version="1.0.0",
        input_payload=request_payload,
        input_hash=_hash_payload(request_payload),
        output_payload=output_payload,
        output_hash=_hash_payload(output_payload) if output_payload is not None else None,
        status=status,
        estimated_cost=None,
        token_estimate=token_total,
        quota_event_id=None,
        cost_event_id=None,
        cost_payload={
            "provider_price_unavailable": True,
            "no_dollar_cost_invented": True,
            "validation_outcome": "VCOS_VALIDATION_PENDING",
            "repair_outcome": "NOT_ATTEMPTED",
            "router_lane": lane.lane_name,
            "selected_model": selected_model,
        },
        correlation_id=correlation_id,
        completed_at=utc_now(),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _model_choices(lane: LLMRouterLane) -> list[_ModelChoice]:
    choices: list[_ModelChoice] = []
    seen: set[str] = set()

    def add_choice(model_id: str | None, fallback_level: str) -> None:
        if model_id and model_id not in seen:
            seen.add(model_id)
            choices.append(_ModelChoice(model_id, fallback_level))

    add_choice(lane.primary_model, "PRIMARY")
    for model in lane.fallback_models:
        add_choice(model, "FALLBACK")
    add_choice(lane.premium_model, "PREMIUM")
    add_choice(lane.emergency_model, "EMERGENCY")
    add_choice(lane.backup_model, "BACKUP")
    _assert_no_forbidden_model([choice.model_id for choice in choices])
    return choices


def _provider_attempt_status(response: ProviderResponse) -> str:
    if response.ok:
        return "SUCCESS"
    if response.error_code == "PROVIDER_QUOTA_EXCEEDED":
        return "QUOTA_REJECTED"
    if response.error_code == "CIRCUIT_BREAKER_OPEN":
        return "CIRCUIT_OPEN"
    if response.retryable:
        return "RETRYABLE_FAILURE"
    return "NON_RETRYABLE_FAILURE"


def _usage_from_payload(payload: dict[str, Any] | None) -> dict[str, int | None]:
    if not payload:
        return {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "prompt_eval_count": _maybe_int(usage.get("prompt_eval_count")),
        "eval_count": _maybe_int(usage.get("eval_count")),
        "total_duration_ms": _maybe_int(usage.get("total_duration_ms")),
        "load_duration_ms": _maybe_int(usage.get("load_duration_ms")),
        "prompt_eval_duration_ms": _maybe_int(usage.get("prompt_eval_duration_ms")),
        "eval_duration_ms": _maybe_int(usage.get("eval_duration_ms")),
    }


def _assert_no_forbidden_model(models: list[str | None]) -> None:
    forbidden = [model for model in models if model and "glm" in model.lower()]
    if forbidden:
        raise ValidationFailureError("GLM_MODEL_FORBIDDEN")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _require_project(session: Session, project_id: uuid.UUID) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"video project not found: {project_id}")
    return project


def _require_short_candidate(session: Session, candidate_id: uuid.UUID) -> ShortCandidate:
    candidate = session.get(ShortCandidate, candidate_id)
    if candidate is None:
        raise NotFoundError(f"short candidate not found: {candidate_id}")
    return candidate


def _require_uploaded(session: Session, uploaded_video_id: uuid.UUID) -> UploadedVideo:
    uploaded = session.get(UploadedVideo, uploaded_video_id)
    if uploaded is None:
        raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
    return uploaded


def _latest_voice_timeline(session: Session, video_project_id: uuid.UUID) -> VoiceTimelineSnapshot | None:
    return session.scalars(
        select(VoiceTimelineSnapshot).where(VoiceTimelineSnapshot.video_project_id == video_project_id).order_by(VoiceTimelineSnapshot.created_at.desc()).limit(1)
    ).one_or_none()


def _latest_caption_track(session: Session, video_project_id: uuid.UUID) -> CaptionTrackSnapshot | None:
    return session.scalars(
        select(CaptionTrackSnapshot).where(CaptionTrackSnapshot.video_project_id == video_project_id).order_by(CaptionTrackSnapshot.created_at.desc()).limit(1)
    ).one_or_none()


def _latest_visual_plan(session: Session, video_project_id: uuid.UUID) -> VisualPlanSnapshot | None:
    return session.scalars(
        select(VisualPlanSnapshot).where(VisualPlanSnapshot.video_project_id == video_project_id).order_by(VisualPlanSnapshot.created_at.desc()).limit(1)
    ).one_or_none()


def _timeline_segments(voice: VoiceTimelineSnapshot) -> list[dict[str, Any]]:
    segments = list((voice.timeline_blob or {}).get("segments") or [])
    return sorted(segments, key=lambda item: item.get("sequence_index", 0))


def _caption_ids_by_segment(captions: CaptionTrackSnapshot | None) -> dict[str, list[str]]:
    if captions is None:
        return {}
    result: dict[str, list[str]] = {}
    for cue in (captions.caption_blob or {}).get("cues") or []:
        result.setdefault(str(cue.get("narration_segment_id")), []).append(str(cue.get("caption_id")))
    return result


def _candidate_windows(segments: list[dict[str, Any]], *, max_candidates: int) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    for start_index in range(len(segments)):
        window: list[dict[str, Any]] = []
        for segment in segments[start_index:]:
            window.append(segment)
            duration = float(window[-1]["estimated_end_time"]) - float(window[0]["estimated_start_time"])
            if duration >= 20:
                if duration <= 45:
                    windows.append(window)
                break
        if len(windows) >= max_candidates:
            break
    if not windows:
        total = float(segments[-1]["estimated_end_time"]) - float(segments[0]["estimated_start_time"])
        if 0 < total < 59:
            windows.append(segments)
    return windows[:max_candidates]


def _enhanced_value(enhancement: LLMRouteResponse | None, key: str) -> str | None:
    if enhancement is None or enhancement.structured_output is None:
        return None
    value = enhancement.structured_output.get(key)
    return str(value) if value else None


def _core_idea(text: str) -> str:
    return _clip_text(text, 180)


def _hook_line(text: str) -> str:
    words = text.split()
    hook = " ".join(words[:14])
    return hook.rstrip(".") + ("?" if not hook.endswith("?") else "")


def _standalone_summary(text: str) -> str:
    return _clip_text(f"Standalone short explaining: {text}", 260)


def _title_from_text(text: str) -> str:
    return _clip_text(text, 70).rstrip(".")


def _overlay_text(text: str) -> str:
    return _clip_text(text, 42).rstrip(".")


def _clip_text(text: str, length: int) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= length else normalized[: length - 1].rstrip() + "."


def _visual_source_for_window(visual: VisualPlanSnapshot | None, window: list[dict[str, Any]]) -> str:
    if visual is None:
        return "UNKNOWN"
    scene_ids = {str(segment.get("narration_segment_id")) for segment in window}
    for scene in (visual.visual_plan_blob or {}).get("scenes") or []:
        if str(scene.get("narration_segment_id")) in scene_ids:
            return "PARENT_SCENE_REUSE"
    return "PARENT_HERO_REUSE"


def _resolve_edge_scope(session: Session, data: ContentDerivativeGraphEdgeCreate) -> tuple[uuid.UUID, uuid.UUID]:
    if data.parent_video_project_id:
        project = _require_project(session, data.parent_video_project_id)
        return project.company_id, project.channel_workspace_id
    if data.parent_uploaded_video_id:
        uploaded = _require_uploaded(session, data.parent_uploaded_video_id)
        return uploaded.company_id, uploaded.channel_workspace_id
    if data.derivative_video_project_id:
        project = _require_project(session, data.derivative_video_project_id)
        return project.company_id, project.channel_workspace_id
    if data.derivative_uploaded_video_id:
        uploaded = _require_uploaded(session, data.derivative_uploaded_video_id)
        return uploaded.company_id, uploaded.channel_workspace_id
    raise ValidationFailureError("Derivative graph edge requires at least one project or uploaded video ref.")


def _resolve_originality_scope(session: Session, data: DerivativeOriginalityCheckCreate) -> tuple[uuid.UUID, uuid.UUID]:
    if data.short_candidate_id:
        candidate = _require_short_candidate(session, data.short_candidate_id)
        return candidate.company_id, candidate.channel_workspace_id
    if data.content_derivative_edge_id:
        edge = session.get(ContentDerivativeGraphEdge, data.content_derivative_edge_id)
        if edge is None:
            raise NotFoundError(f"derivative graph edge not found: {data.content_derivative_edge_id}")
        return edge.company_id, edge.channel_workspace_id
    if data.company_id and data.channel_workspace_id:
        return data.company_id, data.channel_workspace_id
    raise ValidationFailureError("Originality check requires short candidate, derivative edge, or explicit company/channel scope.")


def _originality_result(data: DerivativeOriginalityCheckCreate) -> str:
    if data.policy_flags or data.rights_flags:
        return "BLOCK"
    if data.derivative_type == "COMPILATION" and not data.commentary_or_context_added:
        return "BLOCK"
    if data.derivative_type == "FOLLOW_UP_LONG" and not data.new_value_added_ok:
        return "BLOCK"
    if data.standalone_value_ok and data.new_value_added_ok:
        return "PASS"
    return "REVIEW_REQUIRED"


def _resolve_funnel_scope(session: Session, data: CrossPlatformFunnelPackageCreate) -> tuple[uuid.UUID, uuid.UUID]:
    if data.parent_video_project_id:
        project = _require_project(session, data.parent_video_project_id)
        return project.company_id, project.channel_workspace_id
    if data.parent_uploaded_video_id:
        uploaded = _require_uploaded(session, data.parent_uploaded_video_id)
        return uploaded.company_id, uploaded.channel_workspace_id
    if data.selected_short_candidate_ids:
        candidate = _require_short_candidate(session, data.selected_short_candidate_ids[0])
        return candidate.company_id, candidate.channel_workspace_id
    raise ValidationFailureError("Funnel package requires parent video or selected short candidate.")


def _ensure_short_render_plan(session: Session, candidate: ShortCandidate, *, platform: str) -> ShortRenderPlan:
    plan = session.scalars(
        select(ShortRenderPlan)
        .where(ShortRenderPlan.short_candidate_id == candidate.id)
        .where(ShortRenderPlan.target_platform == _short_platform(platform))
    ).one_or_none()
    if plan is not None:
        return plan
    plan = ShortRenderPlan(
        short_candidate_id=candidate.id,
        company_id=candidate.company_id,
        channel_workspace_id=candidate.channel_workspace_id,
        target_platform=_short_platform(platform),
        target_aspect_ratio="9:16",
        target_duration_ms=candidate.duration_ms,
        voice_source="REUSE_PARENT_AUDIO",
        caption_style_ref=None,
        visual_plan={"source": candidate.visual_source, "m10_2_render_deferred": True},
        render_state="READY_FOR_M10_2_RENDER" if candidate.candidate_state == "SELECTED_FOR_RENDER" else "PLANNED",
        blocker_reason=None,
    )
    session.add(plan)
    session.flush()
    return plan


def _short_platform(upload_platform: str) -> str:
    if upload_platform == "YOUTUBE_LONG":
        return "YOUTUBE_SHORTS"
    return upload_platform


def _resolve_promotion_scope(session: Session, data: PromoteShortToLongCandidateCreate) -> tuple[uuid.UUID, uuid.UUID, bool]:
    if data.source_short_candidate_id:
        candidate = _require_short_candidate(session, data.source_short_candidate_id)
        return candidate.company_id, candidate.channel_workspace_id, False
    if data.source_short_uploaded_video_id:
        uploaded = _require_uploaded(session, data.source_short_uploaded_video_id)
        return uploaded.company_id, uploaded.channel_workspace_id, uploaded.platform != "YOUTUBE"
    raise ValidationFailureError("Promote-short-to-long candidate requires a short candidate or uploaded video source.")


def _record_m10_1_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
) -> None:
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload={**payload, "reason_code": reason_code},
        ),
        company_id=company_id,
    )
