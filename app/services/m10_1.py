from __future__ import annotations

import hashlib
import json
import os
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import EventEnvelope, LLMRouteResponse
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import (
    LLMModelProfile,
    LLMRouteAttempt,
    LLMRouterLane,
    LLMRouterProfile,
    LLMRunSnapshot,
    ProviderAttempt,
)
from app.providers.base import ProviderResponse
from app.providers.openai import OpenAIResponsesProvider, OpenAIResponsesRequest
from app.services.domain_events import DomainEventBus


FINAL_LANES: list[dict[str, Any]] = [
    {
        "lane_name": "cheap_structured",
        "lane_description": "Low-cost structured JSON, metadata, small classification, repair and validation text.",
        "allowed_task_types": [
            "json_schema_output",
            "metadata_generation",
            "small_classification",
            "repair_validation",
            "editorial_idea_research",
        ],
        "primary_model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "cost_tier": "LOW",
        "latency_tier": "FAST",
        "route_priority": 10,
    },
    {
        "lane_name": "default_multimodal",
        "lane_description": "General multimodal reasoning and non-critical creative checks.",
        "allowed_task_types": ["multimodal_reasoning", "creative_check"],
        "primary_model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "cost_tier": "MEDIUM",
        "latency_tier": "NORMAL",
        "route_priority": 20,
    },
    {
        "lane_name": "visual_creative_review",
        "lane_description": "Visual plan, scene concept, creative consistency, and thumbnail direction review.",
        "allowed_task_types": [
            "visual_plan_review",
            "scene_concept_review",
            "thumbnail_direction_review",
        ],
        "primary_model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "cost_tier": "MEDIUM",
        "latency_tier": "NORMAL",
        "route_priority": 30,
    },
    {
        "lane_name": "long_context_text",
        "lane_description": "Long-form script outline, generation, synthesis, research-to-script, and deep rewrite/review.",
        "allowed_task_types": [
            "long_form_script",
            "long_context_synthesis",
            "research_pack_to_script",
            "deep_rewrite",
        ],
        "primary_model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "cost_tier": "MEDIUM",
        "latency_tier": "NORMAL",
        "route_priority": 40,
    },
    {
        "lane_name": "engineering_architect",
        "lane_description": "Internal engineering design review, code architecture reasoning, test planning, and implementation prompts.",
        "allowed_task_types": [
            "engineering_design_review",
            "code_architecture",
            "test_planning",
            "implementation_prompt",
        ],
        "primary_model": "gpt-5.6-luna",
        "reasoning_effort": "high",
        "cost_tier": "HIGH",
        "latency_tier": "SLOW",
        "critical_path_allowed": False,
        "route_priority": 50,
    },
    {
        "lane_name": "gatekeeper_soft_review",
        "lane_description": "Policy/compliance, monetization risk, factuality/risk, and final content soft review.",
        "allowed_task_types": [
            "policy_soft_review",
            "monetization_risk_review",
            "script_risk_review",
            "factuality_review",
        ],
        "primary_model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "cost_tier": "HIGH",
        "latency_tier": "NORMAL",
        "route_priority": 60,
    },
]

# Versioned against the OpenAI model-pricing page on the cutover date.  This
# is intentionally a pinned receipt, not a live scrape during a production run.
OPENAI_PRICING_VERSION = "openai-api-pricing-2026-08-01"
OPENAI_STANDARD_PRICING_PER_MILLION: dict[str, dict[str, Decimal]] = {
    "gpt-5.6-luna": {
        "input": Decimal("1.00"),
        "cached_input": Decimal("0.10"),
        "output": Decimal("6.00"),
    },
    "gpt-5.6-terra": {
        "input": Decimal("2.50"),
        "cached_input": Decimal("0.25"),
        "output": Decimal("15.00"),
    },
}

AGENT_ROUTER_MAPPING: dict[str, list[str]] = {
    "ChannelAuthorityAgent": ["cheap_structured", "long_context_text"],
    "EditorialIdeaResearchAgent": ["cheap_structured"],
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
    "RecoveryProposalReviewer": ["gatekeeper_soft_review"],
    "LocalizationSubtitleAgent": ["long_context_text"],
    "LocalizedMetadataAgent": ["cheap_structured"],
    "PublishTimingSummaryAgent": ["cheap_structured"],
    "ProviderReadinessSummaryAgent": ["cheap_structured"],
    "MediaQCExplanationAgent": ["cheap_structured"],
    "RightsDisclosureReviewer": ["gatekeeper_soft_review"],
}


def configured_router_models() -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for lane in FINAL_LANES:
        for model_id in [lane["primary_model"]]:
            if model_id and model_id not in seen:
                seen.add(model_id)
                models.append(model_id)
    _assert_allowed_models(models)
    return models


class LLMRouterConfigLoader:
    def __init__(self, session: Session):
        self.session = session

    def ensure_default_profile(
        self, *, profile_key: str | None = None
    ) -> LLMRouterProfile:
        profile_key = profile_key or os.getenv("VCOS_LLM_ROUTER_PROFILE", "default")
        settings = get_settings()
        base_url = settings.openai_base_url
        timeout_seconds = max(1, int(settings.openai_timeout_seconds))
        real_enabled = settings.llm_real_execution_enabled
        if settings.llm_provider != "openai":
            raise ValidationFailureError("M10.1 only allows the OpenAI LLM provider.")
        profile = self.session.scalars(
            select(LLMRouterProfile).where(LLMRouterProfile.profile_key == profile_key)
        ).one_or_none()
        if profile is None:
            profile = LLMRouterProfile(
                profile_key=profile_key,
                provider_key="OPENAI",
                base_url=base_url,
                real_execution_enabled=real_enabled,
                default_timeout_seconds=timeout_seconds,
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
                payload={
                    "profile_key": profile.profile_key,
                    "provider_key": profile.provider_key,
                },
            )
        else:
            profile.provider_key = "OPENAI"
            profile.base_url = base_url
            profile.real_execution_enabled = real_enabled
            profile.default_timeout_seconds = timeout_seconds
        self._ensure_lanes(profile, real_enabled=real_enabled)
        self._ensure_model_profiles()
        self.session.flush()
        return profile

    def list_profiles(self) -> list[LLMRouterProfile]:
        self.ensure_default_profile()
        return list(
            self.session.scalars(
                select(LLMRouterProfile).order_by(LLMRouterProfile.profile_key)
            ).all()
        )

    def get_profile(self, profile_key: str) -> LLMRouterProfile:
        self.ensure_default_profile(profile_key=profile_key)
        profile = self.session.scalars(
            select(LLMRouterProfile).where(LLMRouterProfile.profile_key == profile_key)
        ).one_or_none()
        if profile is None:
            raise NotFoundError(f"LLM router profile not found: {profile_key}")
        return profile

    def list_lanes(self, *, profile_key: str = "default") -> list[LLMRouterLane]:
        profile = self.ensure_default_profile(profile_key=profile_key)
        return list(
            self.session.scalars(
                select(LLMRouterLane)
                .where(LLMRouterLane.router_profile_id == profile.id)
                .order_by(LLMRouterLane.route_priority)
            ).all()
        )

    def require_lane(
        self, *, profile_key: str, lane_name: str
    ) -> tuple[LLMRouterProfile, LLMRouterLane]:
        profile = self.ensure_default_profile(profile_key=profile_key)
        lane = self.session.scalars(
            select(LLMRouterLane)
            .where(LLMRouterLane.router_profile_id == profile.id)
            .where(LLMRouterLane.lane_name == lane_name)
        ).one_or_none()
        if lane is None:
            raise NotFoundError(f"LLM router lane not found: {lane_name}")
        _assert_allowed_models([lane.primary_model])
        return profile, lane

    def _ensure_lanes(self, profile: LLMRouterProfile, *, real_enabled: bool) -> None:
        existing = {
            lane.lane_name: lane
            for lane in self.session.scalars(
                select(LLMRouterLane).where(
                    LLMRouterLane.router_profile_id == profile.id
                )
            ).all()
        }
        expected_names = {lane["lane_name"] for lane in FINAL_LANES}
        for stale_name, stale_lane in list(existing.items()):
            if stale_name not in expected_names:
                self.session.delete(stale_lane)
        for lane_def in FINAL_LANES:
            _assert_allowed_models([lane_def["primary_model"]])
            lane = existing.get(lane_def["lane_name"])
            values = {
                "lane_description": lane_def["lane_description"],
                "allowed_task_types": lane_def["allowed_task_types"],
                "primary_model": lane_def["primary_model"],
                "reasoning_effort": lane_def["reasoning_effort"],
                "fallback_models": [],
                "premium_model": None,
                "emergency_model": None,
                "backup_model": None,
                "max_input_tokens": lane_def.get("max_input_tokens"),
                "max_output_tokens": lane_def.get("max_output_tokens"),
                "cost_tier": lane_def["cost_tier"],
                "latency_tier": lane_def["latency_tier"],
                "critical_path_allowed": lane_def.get("critical_path_allowed", False),
                "requires_human_approval_for_premium": False,
                "route_priority": lane_def["route_priority"],
                "real_execution_enabled": real_enabled,
            }
            if lane is None:
                lane = LLMRouterLane(
                    router_profile_id=profile.id,
                    lane_name=lane_def["lane_name"],
                    **values,
                )
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
                    payload={
                        "lane_name": lane.lane_name,
                        "primary_model": lane.primary_model,
                    },
                )
            else:
                for key, value in values.items():
                    setattr(lane, key, value)

    def _ensure_model_profiles(self) -> None:
        lane_by_model: dict[str, set[str]] = {}
        roles: dict[str, str] = {}
        for lane in FINAL_LANES:
            roles.setdefault(lane["primary_model"], "PRIMARY")
            lane_by_model.setdefault(lane["primary_model"], set()).add(
                lane["lane_name"]
            )
        active_model_ids = set(lane_by_model)
        for profile in self.session.scalars(
            select(LLMModelProfile).where(LLMModelProfile.provider_key == "OPENAI")
        ).all():
            if profile.model_id not in active_model_ids:
                # Retain the row for historical receipts, but make a retired
                # model unavailable to every new router decision.
                profile.is_enabled = False
                profile.lane_names = []
                profile.notes = "Retired from the active Luna-only VCOS router."
        for model_id, lane_names in sorted(lane_by_model.items()):
            _assert_allowed_models([model_id])
            profile = self.session.scalars(
                select(LLMModelProfile)
                .where(LLMModelProfile.provider_key == "OPENAI")
                .where(LLMModelProfile.model_id == model_id)
            ).one_or_none()
            values = {
                "model_role": roles[model_id],
                "lane_names": sorted(lane_names),
                "is_enabled": True,
                "critical_path_allowed": False,
                "capability_blob": _model_capability_blob(model_id),
                "pricing_snapshot_version": OPENAI_PRICING_VERSION,
                "notes": "OpenAI-only VCOS router catalog model profile.",
            }
            if profile is None:
                self.session.add(
                    LLMModelProfile(provider_key="OPENAI", model_id=model_id, **values)
                )
            else:
                for key, value in values.items():
                    setattr(profile, key, value)


class LLMRouterService:
    def __init__(
        self, session: Session, provider: OpenAIResponsesProvider | None = None
    ):
        self.session = session
        self.provider = provider

    def route(
        self,
        *,
        lane_name: str,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        image_inputs: list[dict[str, str]] | None = None,
        requested_task_type: str | None = None,
        response_format: str = "text",
        profile_key: str = "default",
        correlation_id: str = "m10-1-llm-router",
        idempotency_key: str | None = None,
    ) -> LLMRouteResponse:
        if prompt is None and messages is None:
            raise ValidationFailureError(
                "LLM route requires either prompt or chat messages."
            )
        profile, lane = LLMRouterConfigLoader(self.session).require_lane(
            profile_key=profile_key, lane_name=lane_name
        )
        request_payload = {
            "lane_name": lane_name,
            "requested_task_type": requested_task_type,
            "prompt": prompt,
            "messages": messages,
            "image_inputs": image_inputs,
            "response_format": response_format,
            "profile_key": profile.profile_key,
            "idempotency_key": idempotency_key,
        }
        request_hash = _hash_payload(request_payload)
        if not self._real_execution_allowed(profile, lane):
            llm_run = _create_llm_run_snapshot(
                self.session,
                profile=profile,
                lane=lane,
                selected_model=lane.primary_model,
                request_payload=request_payload,
                output_payload={
                    "skipped": True,
                    "reason_code": "OPENAI_REAL_EXECUTION_DISABLED",
                },
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
                error_code="OPENAI_REAL_EXECUTION_DISABLED",
                error_message="Real OpenAI execution is disabled by environment/profile/lane guard.",
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
                reason_codes=["OPENAI_REAL_EXECUTION_DISABLED"],
            )

        provider = self.provider or OpenAIResponsesProvider(
            api_key=_openai_api_key(),
            base_url=profile.base_url,
            timeout_seconds=profile.default_timeout_seconds,
        )
        response = provider.respond(
            request=OpenAIResponsesRequest(
                model=lane.primary_model,
                reasoning_effort=_reasoning_effort_for_lane(lane.lane_name),
                prompt=prompt,
                messages=messages,
                image_inputs=image_inputs,
                response_format=response_format,
                idempotency_key=idempotency_key,
            )
        )
        status = "SUCCESS" if response.ok else "FAILED"
        provider_attempt = _create_provider_attempt(
            self.session,
            provider_key="OPENAI",
            operation_key="llm_router.responses",
            target_type="llm_router_lane",
            target_id=lane.id,
            response=response,
            model_id=lane.primary_model,
            reasoning_effort=_reasoning_effort_for_lane(lane.lane_name),
            correlation_id=correlation_id,
            router_lane=lane.lane_name,
            request_hash=request_hash,
        )
        llm_run = _create_llm_run_snapshot(
            self.session,
            profile=profile,
            lane=lane,
            selected_model=lane.primary_model,
            request_payload=request_payload,
            output_payload=response.output
            if response.ok
            else {"error_code": response.error_code},
            status=status,
            run_mode="REAL",
            correlation_id=correlation_id,
            provider_attempt=provider_attempt,
        )
        route_attempt = _create_route_attempt(
            self.session,
            profile=profile,
            lane=lane,
            selected_model=lane.primary_model,
            fallback_level="PRIMARY",
            request_hash=request_hash,
            response_payload=response.output if response.ok else None,
            status=status,
            requested_task_type=requested_task_type,
            provider_attempt=provider_attempt,
            llm_run=llm_run,
            error_code=response.error_code,
            error_message=response.error_message,
        )
        return LLMRouteResponse(
            status=status,
            lane_name=lane.lane_name,
            selected_model=lane.primary_model,
            fallback_level="PRIMARY",
            content=response.output.get("content") if response.ok else None,
            structured_output=response.output.get("json") if response.ok else None,
            route_attempt_id=route_attempt.id,
            provider_attempt_id=provider_attempt.id,
            llm_run_snapshot_id=llm_run.id,
            reason_codes=["LLM_ROUTE_ATTEMPT_CREATED", "NO_MODEL_FALLBACK"],
        )

    def run_smoke_test(self, *, profile_key: str = "default") -> dict[str, Any]:
        LLMRouterConfigLoader(self.session).ensure_default_profile(
            profile_key=profile_key
        )
        if not get_settings().llm_router_real_smoke:
            return {
                "status": "SKIPPED",
                "real_smoke_enabled": False,
                "reason_codes": ["OPENAI_REAL_EXECUTION_DISABLED"],
                "next_action": "Set VCOS_LLM_ROUTER_REAL_SMOKE=true and VCOS_LLM_REAL_EXECUTION_ENABLED=true to run the bounded OpenAI smoke.",
            }
        cheap = self.route(
            lane_name="cheap_structured",
            requested_task_type="smoke_json",
            prompt='Return JSON exactly like {"ok": true, "lane": "cheap_structured"}.',
            response_format="json",
            profile_key=profile_key,
            correlation_id="m10-1-openai-smoke-luna",
        )
        long_context = self.route(
            lane_name="long_context_text",
            requested_task_type="smoke_text",
            prompt="Reply with one short sentence confirming the long context lane is reachable.",
            response_format="text",
            profile_key=profile_key,
            correlation_id="m10-1-openai-smoke-luna",
        )
        route_attempt_ids = [cheap.route_attempt_id, long_context.route_attempt_id]
        status = (
            "SUCCESS"
            if cheap.status == "SUCCESS" and long_context.status == "SUCCESS"
            else "FAILED"
        )
        return {
            "status": status,
            "real_smoke_enabled": True,
            "health_check": {"ok": status == "SUCCESS"},
            "cheap_structured": cheap.model_dump(mode="json"),
            "long_context_text": long_context.model_dump(mode="json"),
            "fallback_probe": {"automatic_model_fallback": False},
            "route_attempt_ids": route_attempt_ids,
            "reason_codes": ["OPENAI_REAL_SMOKE_PASSED"]
            if status == "SUCCESS"
            else ["OPENAI_REAL_SMOKE_BLOCKED"],
        }

    def _real_execution_allowed(
        self, profile: LLMRouterProfile, lane: LLMRouterLane
    ) -> bool:
        return (
            get_settings().llm_real_execution_enabled
            and profile.real_execution_enabled
            and lane.real_execution_enabled
            and get_settings().llm_provider == "openai"
        )


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
        reasoning_effort=_reasoning_effort_for_lane(lane.lane_name),
        provider_request_id=(
            response_payload.get("request_id") if response_payload is not None else None
        ),
        actual_cost_usd=_actual_cost_usd(selected_model, response_payload),
        request_hash=request_hash,
        response_hash=_hash_payload(response_payload)
        if response_payload is not None
        else None,
        status=status,
        error_code=error_code,
        error_message=error_message,
        prompt_eval_count=usage.get("input_tokens"),
        eval_count=usage.get("output_tokens"),
        total_duration_ms=None,
        load_duration_ms=None,
        prompt_eval_duration_ms=None,
        eval_duration_ms=None,
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
        payload={
            "lane_name": lane.lane_name,
            "selected_model": selected_model,
            "status": status,
            "fallback_level": fallback_level,
        },
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
    reasoning_effort: str,
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
        error_message_redacted="redacted provider error"
        if response.error_code
        else None,
        started_at=utc_now(),
        finished_at=utc_now(),
        latency_ms=response.latency_ms,
        metadata_={
            "model_id": model_id,
            "router_lane": router_lane,
            "reasoning_effort": reasoning_effort,
            "request_hash": request_hash,
            "response_hash": response_hash,
            "openai_request_id": response.output.get("request_id")
            if response.ok
            else None,
            "pricing_version": OPENAI_PRICING_VERSION,
            "actual_cost_usd": _serialized_cost(model_id, response.output)
            if response.ok
            else None,
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
        payload={
            "provider_key": provider_key,
            "operation_key": operation_key,
            "status": attempt.status,
            "model_id": model_id,
        },
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
    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        token_total = Decimal(
            str((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0))
        )
    snapshot = LLMRunSnapshot(
        run_type=f"M10_1_LLM_ROUTER_{lane.lane_name.upper()}",
        provider="openai",
        model_name=selected_model,
        provider_key=profile.provider_key,
        model_key=selected_model,
        run_mode=run_mode,
        prompt_template_key="m10_1_llm_router",
        prompt_template_version="1.0.0",
        input_payload=request_payload,
        input_hash=_hash_payload(request_payload),
        output_payload=output_payload,
        output_hash=_hash_payload(output_payload)
        if output_payload is not None
        else None,
        status=status,
        estimated_cost=_actual_cost_usd(selected_model, output_payload),
        token_estimate=token_total,
        quota_event_id=None,
        cost_event_id=None,
        cost_payload={
            "pricing_version": OPENAI_PRICING_VERSION,
            "actual_cost_usd": _serialized_cost(selected_model, output_payload),
            "openai_request_id": output_payload.get("request_id")
            if output_payload
            else None,
            "reasoning_effort": _reasoning_effort_for_lane(lane.lane_name),
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
        "input_tokens": _maybe_int(usage.get("input_tokens")),
        "cached_input_tokens": _maybe_int(usage.get("cached_input_tokens")),
        "output_tokens": _maybe_int(usage.get("output_tokens")),
        "reasoning_tokens": _maybe_int(usage.get("reasoning_tokens")),
    }


def _assert_allowed_models(models: list[str | None]) -> None:
    invalid = [
        model
        for model in models
        if model and model != "gpt-5.6-luna"
    ]
    if invalid:
        raise ValidationFailureError("OPENAI_LUNA_MODEL_REQUIRED")


def _reasoning_effort_for_lane(lane_name: str) -> str:
    for lane in FINAL_LANES:
        if lane["lane_name"] == lane_name:
            return str(lane["reasoning_effort"])
    raise NotFoundError(f"LLM router lane not found: {lane_name}")


def _model_capability_blob(model_id: str) -> dict[str, Any]:
    _assert_allowed_models([model_id])
    return {
        "model_id": model_id,
        "provider_id": "openai",
        "reasoning_effort": sorted(
            {
                str(lane["reasoning_effort"])
                for lane in FINAL_LANES
                if lane["primary_model"] == model_id
            }
        ),
        "structured_outputs": True,
        "function_calling": True,
        "image_input": True,
        "context_limit_tokens": 1_050_000,
        "output_limit_tokens": 128_000,
        "service_tier": "standard",
        "pricing_version": OPENAI_PRICING_VERSION,
        "pricing_evidence_ref": "https://developers.openai.com/api/docs/models/compare",
    }


def _openai_api_key() -> str | None:
    secret = get_settings().openai_api_key
    return secret.get_secret_value() if secret is not None else None


def _actual_cost_usd(model_id: str, payload: dict[str, Any] | None) -> Decimal | None:
    if not payload:
        return None
    usage = _usage_from_payload(payload)
    if usage.get("input_tokens") is None and usage.get("output_tokens") is None:
        return None
    pricing = OPENAI_STANDARD_PRICING_PER_MILLION.get(model_id)
    if pricing is None:
        return None
    cached = Decimal(str(usage.get("cached_input_tokens") or 0))
    input_tokens = Decimal(str(usage.get("input_tokens") or 0))
    non_cached = max(Decimal("0"), input_tokens - cached)
    output_tokens = Decimal(str(usage.get("output_tokens") or 0))
    return (
        (non_cached * pricing["input"])
        + (cached * pricing["cached_input"])
        + (output_tokens * pricing["output"])
    ) / Decimal("1000000")


def _serialized_cost(model_id: str, payload: dict[str, Any] | None) -> str | None:
    value = _actual_cost_usd(model_id, payload)
    return str(value) if value is not None else None


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
