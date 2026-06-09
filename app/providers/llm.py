from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar
import json
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError

from app.schemas import api as schemas


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMProviderError(RuntimeError):
    pass


class LLMResponse(BaseModel):
    output: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    raw_usage_json: dict[str, Any] = {}


def estimate_tokens(value: Any) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=True, default=str).split()))


def estimate_llm_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    if provider == "mock":
        return round((input_tokens + output_tokens) * 0.000001, 6)
    # Conservative generic estimate for OpenAI-compatible APIs when exact pricing is unknown.
    return round((input_tokens * 0.0000005) + (output_tokens * 0.0000015), 6)


class LLMProvider(ABC):
    provider_name = "abstract"
    model = "abstract"

    @abstractmethod
    def complete_structured(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        schema: type[StructuredModel],
    ) -> LLMResponse:
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    provider_name = "mock"
    model = "mock-structured-llm"

    def complete_structured(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        schema: type[StructuredModel],
    ) -> LLMResponse:
        output = self._mock_output(schema, payload)
        validated = schema.model_validate(output)
        input_tokens = estimate_tokens({"system": system_prompt, "payload": payload})
        output_tokens = estimate_tokens(validated.model_dump())
        return LLMResponse(
            output=validated.model_dump(),
            provider=self.provider_name,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimate_llm_cost(self.provider_name, self.model, input_tokens, output_tokens),
            raw_usage_json={"mock": True},
        )

    def _mock_output(self, schema: type[StructuredModel], payload: dict[str, Any]) -> dict[str, Any]:
        name = schema.__name__
        if name == "AuthorityDecision":
            gate = payload.get("gate", "TOPIC_ANGLE_GATE")
            decision = {
                "TOPIC_ANGLE_GATE": "APPROVE_TOPIC",
                "SCRIPT_GATE": "APPROVE_SCRIPT",
                "FINAL_EDITORIAL_GATE": "PASS_TO_HUMAN",
                "POST_PUBLISH_DIAGNOSIS_GATE": "CONTINUE_CURRENT_PLAYBOOK",
            }.get(gate, "REQUEST_MORE_RESEARCH")
            return {
                "decision": decision,
                "monetization_passability_impact": "POSITIVE",
                "revenue_impact": "MEDIUM",
                "policy_risk": "LOW",
                "brand_fit_score": 8.4,
                "audience_fit_score": 8.1,
                "buyer_intent_score": 7.6,
                "reasoning_summary": ["Provider-backed mock decision kept within authority enum."],
                "instructions": {"gate": gate, "mode": payload.get("workflow_mode")},
                "playbook_update_allowed": False,
            }
        if name == "MonetizationStrategyResult":
            return {
                "monetization_hypothesis": "Audience needs practical creator workflow help with a clear affiliate or ads path.",
                "target_metric": "qualified CTA clicks and 24h retention",
                "cta_strategy": "soft CTA after standalone value",
                "expected_revenue_path": "affiliate first, YPP ads after eligibility, sponsorship later",
                "buyer_intent_score": 7.5,
            }
        if name == "ScriptResult":
            topic = payload.get("topic") or payload.get("trend", {}).get("topic") or "creator workflow"
            return {
                "script": (
                    f"Hook: fix one expensive mistake in {topic}. "
                    "Body: show the reusable workflow, the tradeoffs, and one concrete example. "
                    "CTA: use the vetted template or tool only after the viewer has received complete value."
                ),
                "outline": ["hook", "workflow", "example", "monetization-safe CTA"],
                "risk_notes": [],
                "monetization_hypothesis": payload.get("monetization_strategy", {}).get("monetization_hypothesis"),
            }
        if name == "SEOMetadataResult":
            return {
                "recommended_title": "A Practical AI Workflow That Validates Buyer Intent",
                "title": "A Practical AI Workflow That Validates Buyer Intent",
                "title_variants": [
                    "A Practical AI Workflow That Validates Buyer Intent",
                    "Validate Buyer Intent With This AI Creator Workflow",
                ],
                "description": "A monetization-safe creator workflow with practical steps, reusable assets, and a clear CTA.",
                "hashtags": ["AItools", "CreatorWorkflow", "Productivity"],
                "keywords": ["AI creator tools", "workflow", "buyer intent"],
                "tags": ["AI creator tools", "workflow", "buyer intent"],
                "clickbait_risk": "LOW",
                "misleading_risk": "LOW",
            }
        if name == "PublishingContentResult":
            return {
                "final_title": payload.get("seo", {}).get("recommended_title")
                or payload.get("seo", {}).get("title")
                or "A Practical AI Workflow That Validates Buyer Intent",
                "final_description": "Practical creator workflow with a monetization-safe CTA.",
                "pinned_comment": "What part of this workflow would you reuse first?",
                "community_post": "New workflow breakdown is ready for review.",
                "short_description": "Practical creator workflow with a monetization-safe CTA.",
                "disclosure_note": "Includes transparent disclosure if AI assistance or affiliate links are used.",
                "affiliate_disclosure_note": "Some links may be affiliate links when applicable.",
                "sponsorship_disclosure_note": None,
                "upload_checklist": ["title", "description", "pinned_comment", "disclosures"],
            }
        if name == "ComplianceChecklistResult":
            return {
                "decision": "PASS",
                "policy_risk": "LOW",
                "checklist": [
                    {"item": "copyright risk", "status": "PASS", "notes": "Mock provider found no issue."},
                    {"item": "reused content risk", "status": "PASS", "notes": "Reuse risk is low."},
                    {"item": "AI disclosure risk", "status": "PASS", "notes": "Disclosure can be added if AI assistance is material."},
                    {"item": "misleading claims", "status": "PASS", "notes": "No unsupported claims found."},
                    {"item": "platform safety", "status": "PASS", "notes": "No platform safety concern found."},
                    {"item": "monetization eligibility risk", "status": "PASS", "notes": "Mock provider found low risk."},
                ],
                "issues": [],
                "required_fixes": [],
                "disclosure_required": False,
                "ai_disclosure_required": False,
                "affiliate_disclosure_required": False,
                "sponsorship_disclosure_required": False,
                "reused_content_risk": "LOW",
                "copyright_basis": "mock workflow uses generated or licensed placeholders",
                "copyright_risk_level": "LOW",
                "monetization_eligibility_risk": "LOW",
                "misleading_claims_risk": "LOW",
                "platform_safety_risk": "LOW",
                "policy_references": ["platform monetization safety", "copyright originality"],
                "required_disclosures": [],
            }
        raise LLMProviderError(f"mock provider has no output fixture for schema {name}")


class OpenAICompatibleLLMProvider(LLMProvider):
    provider_name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete_structured(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        schema: type[StructuredModel],
    ) -> LLMResponse:
        from app.config.settings import get_settings

        last_error: Exception | None = None
        for attempt in range(get_settings().max_repair_attempts + 1):
            try:
                raw = self._call(system_prompt=system_prompt, payload=payload, schema=schema, attempt=attempt)
                parsed = self._extract_json(raw)
                validated = schema.model_validate(parsed)
                usage = raw.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or estimate_tokens(payload))
                output_tokens = int(
                    usage.get("completion_tokens") or usage.get("output_tokens") or estimate_tokens(validated.model_dump())
                )
                return LLMResponse(
                    output=validated.model_dump(),
                    provider=self.provider_name,
                    model=self.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost=estimate_llm_cost(self.provider_name, self.model, input_tokens, output_tokens),
                    raw_usage_json=usage,
                )
            except (ValidationError, LLMProviderError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
        raise LLMProviderError(f"structured LLM response failed schema validation: {last_error}") from last_error

    def _call(self, *, system_prompt: str, payload: dict[str, Any], schema: type[StructuredModel], attempt: int) -> dict:
        from app.config.settings import get_settings

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_payload": payload,
                            "schema_name": schema.__name__,
                            "retry_instruction": "Return only valid JSON matching the schema." if attempt else None,
                        },
                        ensure_ascii=True,
                        default=str,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=get_settings().agent_call_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as exc:
            raise LLMProviderError(f"LLM HTTP request failed: {exc}") from exc

    def _extract_json(self, raw: dict) -> dict:
        content = raw["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            return content
        return json.loads(content)


def default_mock_for_schema(schema_name: str) -> type[BaseModel] | None:
    return getattr(schemas, schema_name, None)
