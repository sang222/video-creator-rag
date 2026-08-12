"""Trusted, ID-only preparation boundary for frozen v2 production support.

Public callers may select an already-frozen planning source, but they may not
submit scripts, research, evidence, provider plans, rights claims, or
destination bindings.  This service resolves those authorities from server
records, asks the guarded LLM router for a typed script draft, validates it,
and seals one immutable domain-only envelope.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.geo_market import DestinationBinding
from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.production_package import ProductionDurationContractV2
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.m10_2 import (
    MediaProviderRoleProfile,
    ProviderCapabilityMatrixEntry,
)
from app.db.models.m5 import (
    AudienceTargetPack,
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    SearchIntentMap,
)
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.m10_1 import LLMRouterService
from app.services.m10_2 import (
    MediaProviderRoleService,
    ProviderCapabilityMatrixService,
)
from app.services.mr1_monthly_budget import MR1MonthlyBudgetAuthority
from app.services.production_package import semantic_hash
from app.services.r3d7 import AgentMemoryDigestInjectionService
from app.services.workflow import ArtifactService
from app.contracts.cross_modal import SectionCoveragePlan, cross_modal_hash


V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE = "v2_frozen_support_envelope"
V2_FROZEN_SUPPORT_ENVELOPE_SCHEMA = "vcos.frozen-support-envelope.v2"
V2_SUPPORT_AUTHORITY_VERSION = "vcos.v2-support-authority.v1"
V2_SUPPORT_PRODUCER_SCHEMA = "vcos.v2-trusted-support-draft.v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MEDIA_ROUTING_POLICY_CATALOG = Path(
    "config/media_provider_routing_policy_catalog.yaml"
)
_SOURCE_TYPES = ("LONG_FORM_PLAN",)
_LOCAL_PROVIDER_BY_STAGE = {
    "MEDIA": "vcos_caption_timeline",
    "RENDER": "native_ffmpeg_renderer",
    "QC": "vcos_media_qc",
    "ARCHIVE": "vcos_storage",
}
_V2_ADAPTER_BY_STAGE = {
    "MEDIA": "v2-local-native",
    "RENDER": "v2-local-native",
    "QC": "v2-local-native",
    # Archive is deliberately remote-artifact resolution, not a local-copy
    # completion.  The adapter itself remains network-free and fail-closed.
    "ARCHIVE": "v2-google-drive-archive",
}
_JOB_TYPES_BY_LANE = {
    "LONG_FORM": {
        "MEDIA": "LONG_CAPTION_TIMELINE",
        "RENDER": "LONG_FORM_FINAL_RENDER",
        "QC": "LONG_MEDIA_QC",
    },
}
_EFFECTIVE_SUBCONTEXT_FIELDS = (
    "market_locale_context_json",
    "audience_context_json",
    "brand_voice_persona_context_json",
    "category_runtime_context_json",
    "character_identity_context_json",
    "visual_style_context_json",
    "voice_audio_context_json",
    "thumbnail_style_context_json",
    "metadata_seo_policy_context_json",
    "publish_timing_context_json",
    "source_rights_disclosure_context_json",
    "monetization_cta_context_json",
    "cost_provider_policy_context_json",
    "safety_forbidden_claims_context_json",
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class V2SupportAuthorityPrepareCommand(_StrictFrozenModel):
    """Only values an authenticated launcher may pass into this boundary."""

    video_project_id: uuid.UUID
    source_type: Literal["LONG_FORM_PLAN"]
    source_id: uuid.UUID
    actor_user_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=160)
    max_budget_usd: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        le=Decimal("250"),
        decimal_places=2,
    )
    execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"] = (
        "QUALIFICATION_LOCAL"
    )
    budget_reservation_run_id: uuid.UUID | None = None
    # Real production is permitted only after the pre-admission authority has
    # sealed a current ScriptQualificationReceipt.
    script_qualification_run_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_execution_mode(self) -> Self:
        if self.execution_mode == "REAL_LONG_FORM_PRODUCTION" and (
            self.budget_reservation_run_id is None
            or self.script_qualification_run_id is None
        ):
            raise ValueError("V2_REAL_PROVIDER_SCRIPT_QUALIFICATION_REQUIRED")
        if self.execution_mode == "QUALIFICATION_LOCAL" and (
            self.budget_reservation_run_id is not None
            or self.script_qualification_run_id is not None
        ):
            raise ValueError("V2_QUALIFICATION_BUDGET_RESERVATION_FORBIDDEN")
        return self


class V2ExactAuthorityRef(_StrictFrozenModel):
    type: str = Field(min_length=1)
    id: uuid.UUID
    ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)


class V2FrozenEvidenceSpan(_StrictFrozenModel):
    """An exact factual span frozen by script qualification.

    Planning sources intentionally have no entries here.  A factual citation
    is valid only when every one of these fields is carried unchanged from the
    immutable qualification evidence pack.
    """

    evidence_span_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    span_hash: str = Field(pattern=_SHA256_PATTERN)
    source_snapshot_hash: str = Field(pattern=_SHA256_PATTERN)
    freshness_state: Literal["FRESH"]
    source_quality_state: Literal["PASS"]
    authority_purpose: Literal["CLAIM_SOURCE"]
    source_class: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_exact_span(self) -> Self:
        if self.end_byte <= self.start_byte or self.end_byte - self.start_byte != len(
            self.text.encode("utf-8")
        ):
            raise ValueError("V2_FROZEN_EVIDENCE_SPAN_RANGE_INVALID")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.span_hash:
            raise ValueError("V2_FROZEN_EVIDENCE_SPAN_HASH_INVALID")
        return self


class V2FrozenSourceRef(V2ExactAuthorityRef):
    source_kind: str = Field(min_length=1)
    fact_statements: list[str] = Field(min_length=1)
    evidence_spans: list[V2FrozenEvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_factual_span_binding(self) -> Self:
        if self.evidence_spans:
            if any(
                span.source_snapshot_hash != self.content_hash
                for span in self.evidence_spans
            ):
                raise ValueError("V2_FROZEN_EVIDENCE_SNAPSHOT_HASH_MISMATCH")
            if self.fact_statements != [span.text for span in self.evidence_spans]:
                raise ValueError("V2_FROZEN_EVIDENCE_STATEMENT_SPAN_MISMATCH")
        return self


class V2GeneratedCitation(_StrictFrozenModel):
    source_ref_id: uuid.UUID
    source_excerpt: str = Field(min_length=8)
    source_ref_type: str | None = Field(default=None, min_length=1)
    evidence_span_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_span_identity(self) -> Self:
        if (self.source_ref_type is None) != (self.evidence_span_id is None):
            raise ValueError("V2_GENERATED_CITATION_SPAN_IDENTITY_INCOMPLETE")
        return self


class V2GeneratedClaim(_StrictFrozenModel):
    claim_id: str = Field(min_length=1, max_length=120)
    claim_text: str = Field(min_length=3, max_length=2_000)
    citations: list[V2GeneratedCitation] = Field(min_length=1)


class V2GeneratedSection(_StrictFrozenModel):
    section_id: str = Field(min_length=1, max_length=120)
    heading: str = Field(min_length=1, max_length=240)
    narration: str = Field(min_length=1)


class V2ProducerReceipt(_StrictFrozenModel):
    producer_type: Literal["LLM_ROUTER", "OPENAI_BACKGROUND_NORMALIZED"]
    producer_version: str = Field(min_length=1)
    lane_name: str = Field(min_length=1)
    selected_model: str = Field(min_length=1)
    fallback_level: str | None = Field(default=None, min_length=1)
    route_attempt_id: uuid.UUID | None = None
    provider_attempt_id: uuid.UUID | None = None
    llm_run_snapshot_id: uuid.UUID | None = None
    background_attempt_id: uuid.UUID | None = None
    provider_response_id: str | None = Field(default=None, min_length=1)
    provider_request_id: str | None = Field(default=None, min_length=1)
    normalization_receipt_id: uuid.UUID | None = None
    normalization_receipt_hash: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    source_typed_provider_output_hash: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    producer_input_hash: str = Field(pattern=_SHA256_PATTERN)
    producer_output_hash: str = Field(pattern=_SHA256_PATTERN)
    # Present only when this envelope projects a script that was produced by
    # script qualification.  ``producer_*`` then remain the original writer
    # boundary hashes while this records the deterministic projection shape.
    projected_output_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    qualification_receipt_hash: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )

    @model_validator(mode="after")
    def validate_producer_boundary(self) -> Self:
        router_fields = (
            self.fallback_level,
            self.route_attempt_id,
        )
        background_fields = (
            self.background_attempt_id,
            self.provider_response_id,
            self.provider_request_id,
            self.normalization_receipt_id,
            self.normalization_receipt_hash,
            self.source_typed_provider_output_hash,
        )
        if self.producer_type == "LLM_ROUTER":
            if any(item is None for item in router_fields) or any(
                item is not None for item in background_fields
            ):
                raise ValueError("V2_PRODUCER_ROUTER_PROVENANCE_INVALID")
        elif (
            any(item is None for item in background_fields)
            or any(item is not None for item in router_fields)
            or self.provider_attempt_id is not None
            or self.llm_run_snapshot_id is not None
        ):
            raise ValueError("V2_PRODUCER_BACKGROUND_PROVENANCE_INVALID")
        return self


class V2TrustedSupportDraft(_StrictFrozenModel):
    """Typed output accepted only from a trusted in-process producer."""

    schema_version: Literal["vcos.v2-trusted-support-draft.v1"] = (
        V2_SUPPORT_PRODUCER_SCHEMA
    )
    approved_script_text: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=40)
    sections: list[V2GeneratedSection] = Field(min_length=3)
    claims: list[V2GeneratedClaim] = Field(min_length=3)
    producer_receipt: V2ProducerReceipt


class V2SupportProductionContext(_StrictFrozenModel):
    """Server-resolved facts exposed to the trusted producer."""

    schema_version: Literal["vcos.v2-support-production-context.v1"] = (
        "vcos.v2-support-production-context.v1"
    )
    video_project_id: uuid.UUID
    production_lane: Literal["LONG_FORM"]
    title: str = Field(min_length=1)
    expected_language: str = Field(min_length=2)
    duration_contract: ProductionDurationContractV2
    frozen_sources: list[V2FrozenSourceRef] = Field(min_length=1)
    forbidden_claims: list[str] = Field(default_factory=list)
    forbidden_style_terms: list[str] = Field(default_factory=list)
    # This is a prompt-safe digest only. It is never evidence for a script
    # claim and never contains raw analytics or raw controlled-memory rows.
    memory_guidance_digest: dict[str, Any] | None = None


@runtime_checkable
class TrustedV2SupportProducer(Protocol):
    """Internal producer; it never receives caller-authored script fields."""

    def produce(self, context: V2SupportProductionContext) -> V2TrustedSupportDraft: ...


@runtime_checkable
class LLMRouterPort(Protocol):
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
    ) -> LLMRouteResponse: ...


class _LLMGeneratedSupportOutput(_StrictFrozenModel):
    approved_script_text: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=40)
    sections: list[V2GeneratedSection] = Field(min_length=3)
    claims: list[V2GeneratedClaim] = Field(min_length=3)


class LLMRouterV2SupportProducer:
    """Guarded LLMRouter adapter with explicit disabled/invalid failures."""

    def __init__(
        self,
        session: Session,
        *,
        router: LLMRouterPort | None = None,
        enabled: bool = True,
        profile_key: str = "default",
    ):
        self.session = session
        self.router = router or LLMRouterService(session)
        self.enabled = enabled
        self.profile_key = profile_key

    def produce(self, context: V2SupportProductionContext) -> V2TrustedSupportDraft:
        if not self.enabled:
            raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_DISABLED")
        producer_input_hash = semantic_hash(context.model_dump(mode="json"))
        try:
            response = self.router.route(
                lane_name="long_context_text",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Return strict JSON only. Write an original production "
                            "script using only the frozen source statements supplied. "
                            "Every material claim must quote an exact source statement "
                            "as source_excerpt and cite its source_ref_id. Do not add "
                            "external assets, URLs, provider calls, or facts. Any "
                            "memory guidance is non-factual creative guidance only; "
                            "never cite it as evidence or turn it into a claim."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "output_schema": {
                                    "approved_script_text": "string",
                                    "language": "string",
                                    "sections": [
                                        {
                                            "section_id": "string",
                                            "heading": "string",
                                            "narration": "string",
                                        }
                                    ],
                                    "claims": [
                                        {
                                            "claim_id": "string",
                                            "claim_text": "exact script excerpt",
                                            "citations": [
                                                {
                                                    "source_ref_id": "uuid",
                                                    "source_excerpt": (
                                                        "exact frozen source statement"
                                                    ),
                                                }
                                            ],
                                        }
                                    ],
                                },
                                "requirements": {
                                    "section_narration_concatenation_must_equal_script": (
                                        True
                                    ),
                                    "minimum_duration_ms": (
                                        context.duration_contract.minimum_duration_ms
                                    ),
                                    "target_duration_ms": (
                                        context.duration_contract.target_duration_ms
                                    ),
                                    "maximum_duration_ms": (
                                        context.duration_contract.maximum_duration_ms
                                    ),
                                    "timing_model_words_per_minute": 150,
                                    "minimum_distinct_sections": 3,
                                    "minimum_source_bound_claims": 3,
                                    "expected_language": context.expected_language,
                                    "forbidden_claims": context.forbidden_claims,
                                    "forbidden_style_terms": (
                                        context.forbidden_style_terms
                                    ),
                                },
                                "title": context.title,
                                "production_lane": context.production_lane,
                                "frozen_sources": [
                                    {
                                        "source_ref_id": str(source.id),
                                        "source_kind": source.source_kind,
                                        "content_hash": source.content_hash,
                                        "fact_statements": source.fact_statements,
                                    }
                                    for source in context.frozen_sources
                                ],
                                "memory_guidance_digest": context.memory_guidance_digest,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    },
                ],
                requested_task_type="research_pack_to_script",
                response_format="json",
                profile_key=self.profile_key,
                correlation_id=(f"v2-support-authority-{context.video_project_id}"),
            )
        except ValidationFailureError as exc:
            raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_FAILED") from exc
        except Exception as exc:
            raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_FAILED") from exc
        if response.status == "SKIPPED":
            raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_DISABLED")
        if response.status != "SUCCESS":
            raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_FAILED")
        payload: Any = response.structured_output
        if payload is None and response.content:
            try:
                payload = json.loads(response.content)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_INVALID") from exc
        try:
            generated = _LLMGeneratedSupportOutput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationFailureError("V2_SUPPORT_LLM_PRODUCER_INVALID") from exc
        output_hash = semantic_hash(generated.model_dump(mode="json"))
        return V2TrustedSupportDraft(
            approved_script_text=generated.approved_script_text,
            language=generated.language,
            sections=generated.sections,
            claims=generated.claims,
            producer_receipt=V2ProducerReceipt(
                producer_type="LLM_ROUTER",
                producer_version=V2_SUPPORT_AUTHORITY_VERSION,
                lane_name=response.lane_name,
                selected_model=response.selected_model,
                fallback_level=response.fallback_level,
                route_attempt_id=response.route_attempt_id,
                provider_attempt_id=response.provider_attempt_id,
                llm_run_snapshot_id=response.llm_run_snapshot_id,
                producer_input_hash=producer_input_hash,
                producer_output_hash=output_hash,
            ),
        )


class V2ApprovedScriptProvenance(_StrictFrozenModel):
    approval_state: Literal["APPROVED"] = "APPROVED"
    approved_script_text: str = Field(min_length=1)
    script_hash: str = Field(pattern=_SHA256_PATTERN)
    word_count: int = Field(gt=0)
    estimated_duration_ms: int = Field(gt=0)
    repeated_sentence_ratio: float = Field(ge=0, le=1)
    language: str = Field(min_length=2)
    sections: list[V2GeneratedSection] = Field(min_length=3)
    producer_receipt: V2ProducerReceipt
    gate_receipt_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        section_ids = [section.section_id for section in self.sections]
        narrations = [_normalized_text(section.narration) for section in self.sections]
        script = self.approved_script_text.strip()
        words = re.findall(r"\b[\w'-]+\b", script, flags=re.UNICODE)
        sentences = _sentences(script)
        normalized_sentences = [_normalized_text(sentence) for sentence in sentences]
        repeated_ratio = (
            round(
                (len(normalized_sentences) - len(set(normalized_sentences)))
                / len(normalized_sentences),
                6,
            )
            if normalized_sentences
            else 1.0
        )
        if (
            len(section_ids) != len(set(section_ids))
            or len(narrations) != len(set(narrations))
            or _normalized_text(script)
            != _normalized_text(
                " ".join(section.narration for section in self.sections)
            )
            or self.script_hash != semantic_hash({"approved_script_text": script})
            or self.word_count != len(words)
            or self.estimated_duration_ms != round(len(words) / 150 * 60_000)
            or self.repeated_sentence_ratio != repeated_ratio
        ):
            raise ValueError("APPROVED_SCRIPT_SECTION_BINDING_INVALID")
        return self


class V2CrossModalScriptLineage(_StrictFrozenModel):
    """Compact bridge from immutable qualification to post-voice planning."""

    qualified_script_hash: str = Field(pattern=_SHA256_PATTERN)
    section_coverage_plan: SectionCoveragePlan
    writer_sections: list[dict[str, Any]] = Field(min_length=3)
    capability_projection_receipts: dict[str, dict[str, Any]]
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = cross_modal_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("V2_CROSS_MODAL_SCRIPT_LINEAGE_HASH_MISMATCH")
        return self


class V2ClaimSourceBinding(_StrictFrozenModel):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=3)
    source_refs: list[V2ExactAuthorityRef] = Field(min_length=1)
    source_excerpts: list[str] = Field(min_length=1)
    evidence_span_refs: list[V2FrozenEvidenceSpan] = Field(default_factory=list)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = semantic_hash(self.model_dump(mode="json", exclude={"binding_hash"}))
        if self.binding_hash != expected:
            raise ValueError("CLAIM_SOURCE_BINDING_HASH_MISMATCH")
        return self


class V2LocalGeneratedCardRights(_StrictFrozenModel):
    """Frozen visual-use authority (historical name retained for reads).

    V1 envelopes were local-card only.  V2 keeps that record readable while
    allowing a later support envelope to freeze a policy-selected asset
    request route.  This is an authorization of *requests*, not a fabricated
    claim that an external asset was already acquired.
    """

    schema_version: Literal[
        "vcos.local-generated-card-rights.v1",
        "vcos.visual-asset-request-authority.v2",
    ] = "vcos.local-generated-card-rights.v1"
    rights_state: Literal["PASS"] = "PASS"
    visual_source_mode: Literal[
        "LOCAL_GENERATED_CARDS_ONLY",
        "NATIVE_BACKBONE_POLICY_ONLY",
        "POLICY_SELECTED_ASSET_REQUESTS",
    ] = "LOCAL_GENERATED_CARDS_ONLY"
    external_asset_refs: list[Any] = Field(default_factory=list, max_length=0)
    stock_asset_refs: list[Any] = Field(default_factory=list, max_length=0)
    license_evidence_required: Literal[False] = False
    synthetic_media_disclosure_required: Literal[False] = False
    policy_refs: list[str] = Field(default_factory=list)
    allowed_provider_keys: list[str] = Field(default_factory=list)
    one_source_decision_per_scene: bool = True
    provider_fallback_allowed: Literal[False] = False
    asset_request_compiler_required: bool = False
    post_readiness_acquisition_required: bool = False
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        body = self.model_dump(mode="json", exclude={"content_hash"})
        # V1 artifacts did not have the V2 policy/request fields.  Excluding
        # their harmless defaults keeps historical frozen envelopes readable
        # without rewriting their durable hash.
        if self.schema_version == "vcos.local-generated-card-rights.v1":
            for key in (
                "policy_refs",
                "allowed_provider_keys",
                "one_source_decision_per_scene",
                "provider_fallback_allowed",
                "asset_request_compiler_required",
                "post_readiness_acquisition_required",
            ):
                body.pop(key, None)
        expected = semantic_hash(body)
        if self.content_hash != expected:
            raise ValueError("LOCAL_CARD_RIGHTS_HASH_MISMATCH")
        if self.visual_source_mode == "LOCAL_GENERATED_CARDS_ONLY" and (
            self.schema_version != "vcos.local-generated-card-rights.v1"
            or self.policy_refs
            or self.allowed_provider_keys
            or self.asset_request_compiler_required
            or self.post_readiness_acquisition_required
        ):
            raise ValueError("V2_HISTORICAL_LOCAL_CARD_RIGHTS_INVALID")
        if self.visual_source_mode == "POLICY_SELECTED_ASSET_REQUESTS" and (
            self.schema_version != "vcos.visual-asset-request-authority.v2"
            or not self.policy_refs
            or not self.allowed_provider_keys
            or not self.asset_request_compiler_required
            or not self.post_readiness_acquisition_required
        ):
            raise ValueError("V2_VISUAL_ASSET_REQUEST_AUTHORITY_INVALID")
        if self.visual_source_mode == "NATIVE_BACKBONE_POLICY_ONLY" and (
            self.schema_version != "vcos.visual-asset-request-authority.v2"
            or not self.policy_refs
            or self.allowed_provider_keys != ["native_ffmpeg_renderer"]
            or self.asset_request_compiler_required
            or self.post_readiness_acquisition_required
        ):
            raise ValueError("V2_NATIVE_BACKBONE_VISUAL_AUTHORITY_INVALID")
        return self


class V2MemoryGuidanceAuthority(_StrictFrozenModel):
    """Reference-only proof that a prompt-safe memory digest influenced planning."""

    memory_influence_manifest_id: uuid.UUID
    retrieval_manifest_id: uuid.UUID
    agent_memory_application_record_id: uuid.UUID
    digest_hash: str = Field(pattern=_SHA256_PATTERN)
    scope_status: Literal["PASS", "EMPTY_SAFE_DIGEST"]
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = semantic_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("V2_MEMORY_GUIDANCE_AUTHORITY_HASH_MISMATCH")
        return self


class V2NativeRouteReceipt(_StrictFrozenModel):
    stage: Literal["MEDIA", "RENDER", "QC", "ARCHIVE"]
    operation_id: str = Field(min_length=1, max_length=200)
    adapter_key: Literal[
        "v2-local-native",
        "v2-google-drive-archive",
        "v2-elevenlabs-narration",
        "v2-google-drive-remote",
    ]
    provider_role_id: uuid.UUID
    provider_key: str = Field(min_length=1)
    provider_type: str = Field(min_length=1)
    routing_decision_id: uuid.UUID | None = None
    routing_policy_ref: str = Field(min_length=1)
    routing_policy_hash: str = Field(pattern=_SHA256_PATTERN)
    capability_entry_id: uuid.UUID | None = None
    job_type: str | None = None
    paid_provider_call: bool = False
    max_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=250)
    route_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = semantic_hash(self.model_dump(mode="json", exclude={"route_hash"}))
        if self.route_hash != expected:
            raise ValueError("V2_PROVIDER_ROUTE_HASH_MISMATCH")
        return self


class V2ZeroCostBudgetAuthority(_StrictFrozenModel):
    """Frozen route budget, retaining the historical class name for reads."""

    schema_version: Literal[
        "vcos.zero-cost-route-budget.v1", "vcos.real-provider-route-budget.v1"
    ] = "vcos.zero-cost-route-budget.v1"
    policy_mode: Literal[
        "LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER",
        "REAL_PROVIDER_PER_VIDEO_RESERVATION",
    ] = "LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER"
    execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"] = (
        "QUALIFICATION_LOCAL"
    )
    requested_ceiling_usd: Decimal = Field(ge=0, le=250)
    authorized_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=250)
    paid_provider_calls_allowed: bool = False
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=250)
    monthly_used_usd: Decimal | None = Field(default=None, ge=0, le=250)
    monthly_reserved_usd: Decimal | None = Field(default=None, ge=0, le=250)
    reservation_ref: str | None = None
    reservation_evidence: dict[str, Any] | None = None
    operation_ids: list[str] = Field(min_length=4, max_length=4)
    route_hashes: list[str] = Field(min_length=4, max_length=4)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        if self.operation_ids != sorted(set(self.operation_ids)):
            raise ValueError("BUDGET_OPERATION_BINDING_INVALID")
        if self.route_hashes != sorted(set(self.route_hashes)):
            raise ValueError("BUDGET_ROUTE_BINDING_INVALID")
        expected = semantic_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("V2_ROUTE_BUDGET_HASH_MISMATCH")
        if self.execution_mode == "QUALIFICATION_LOCAL" and (
            self.schema_version != "vcos.zero-cost-route-budget.v1"
            or self.policy_mode != "LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER"
            or self.authorized_cost_usd != 0
            or self.paid_provider_calls_allowed
        ):
            raise ValueError("V2_QUALIFICATION_BUDGET_MODE_INVALID")
        if self.execution_mode == "REAL_LONG_FORM_PRODUCTION" and (
            self.schema_version != "vcos.real-provider-route-budget.v1"
            or self.policy_mode != "REAL_PROVIDER_PER_VIDEO_RESERVATION"
            or self.authorized_cost_usd <= 0
            or not self.paid_provider_calls_allowed
            or not self.reservation_ref
            or not isinstance(self.reservation_evidence, dict)
            or self.reservation_evidence.get("reservation_ref") != self.reservation_ref
            or self.reservation_evidence.get("status") != "RESERVED"
        ):
            raise ValueError("V2_REAL_PROVIDER_BUDGET_MODE_INVALID")
        return self


class V2VerifiedDestinationAuthority(_StrictFrozenModel):
    active_binding_ref: str = Field(min_length=1)
    binding: DestinationBinding
    destination_hash: str = Field(pattern=_SHA256_PATTERN)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        if self.binding.content_hash != self.destination_hash:
            raise ValueError("DESTINATION_HASH_MISMATCH")
        expected = semantic_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("DESTINATION_AUTHORITY_HASH_MISMATCH")
        return self


class V2FrozenSupportEnvelope(_StrictFrozenModel):
    schema_version: Literal["vcos.frozen-support-envelope.v2"] = (
        V2_FROZEN_SUPPORT_ENVELOPE_SCHEMA
    )
    authority_classification: Literal["DOMAIN_ONLY_CANONICAL_V2"] = (
        "DOMAIN_ONLY_CANONICAL_V2"
    )
    authority_version: Literal["vcos.v2-support-authority.v1"] = (
        V2_SUPPORT_AUTHORITY_VERSION
    )
    approval_state: Literal["APPROVED"] = "APPROVED"
    idempotency_hash: str = Field(pattern=_SHA256_PATTERN)
    input_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    project_ref: V2ExactAuthorityRef
    admission_ref: V2ExactAuthorityRef
    profile_ref: V2ExactAuthorityRef
    compiled_policy_ref: V2ExactAuthorityRef
    effective_context_ref: V2ExactAuthorityRef
    production_lane: Literal["LONG_FORM"]
    execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"] = (
        "QUALIFICATION_LOCAL"
    )
    duration_contract: ProductionDurationContractV2
    frozen_sources: list[V2FrozenSourceRef] = Field(min_length=2)
    approved_script: V2ApprovedScriptProvenance
    cross_modal_script_lineage: V2CrossModalScriptLineage | None = None
    claim_source_bindings: list[V2ClaimSourceBinding] = Field(min_length=3)
    local_generated_card_rights: V2LocalGeneratedCardRights
    memory_guidance_authority: V2MemoryGuidanceAuthority | None = None
    native_routes: list[V2NativeRouteReceipt] = Field(min_length=4, max_length=4)
    zero_cost_budget: V2ZeroCostBudgetAuthority
    verified_destination: V2VerifiedDestinationAuthority
    gate_receipts: list[dict[str, Any]] = Field(min_length=6)

    @model_validator(mode="after")
    def validate_cross_bindings(self) -> Self:
        stages = [route.stage for route in self.native_routes]
        if sorted(stages) != ["ARCHIVE", "MEDIA", "QC", "RENDER"]:
            raise ValueError("NATIVE_ROUTE_STAGE_SET_INVALID")
        route_hashes = sorted(route.route_hash for route in self.native_routes)
        operation_ids = sorted(route.operation_id for route in self.native_routes)
        if (
            self.zero_cost_budget.route_hashes != route_hashes
            or self.zero_cost_budget.operation_ids != operation_ids
            or self.zero_cost_budget.execution_mode != self.execution_mode
        ):
            raise ValueError("BUDGET_ROUTE_BINDING_INVALID")
        expected_adapters = (
            {
                "MEDIA": "v2-local-native",
                "RENDER": "v2-local-native",
                "QC": "v2-local-native",
                "ARCHIVE": "v2-google-drive-archive",
            }
            if self.execution_mode == "QUALIFICATION_LOCAL"
            else {
                "MEDIA": "v2-elevenlabs-narration",
                "RENDER": "v2-local-native",
                "QC": "v2-local-native",
                "ARCHIVE": "v2-google-drive-remote",
            }
        )
        if {
            route.stage: route.adapter_key for route in self.native_routes
        } != expected_adapters:
            raise ValueError("V2_EXECUTION_ROUTE_MODE_MISMATCH")
        if (
            self.approved_script.estimated_duration_ms
            < self.duration_contract.minimum_duration_ms
            or self.approved_script.estimated_duration_ms
            > self.duration_contract.maximum_duration_ms
        ):
            raise ValueError("APPROVED_SCRIPT_DURATION_INVALID")
        source_keys = {(source.type, source.id) for source in self.frozen_sources}
        source_by_key = {
            (source.type, source.id): source for source in self.frozen_sources
        }
        if len(source_keys) != len(self.frozen_sources):
            raise ValueError("FROZEN_SOURCE_IDENTITY_COLLISION")
        claim_ids = [binding.claim_id for binding in self.claim_source_bindings]
        claim_texts = [
            _normalized_text(binding.claim_text)
            for binding in self.claim_source_bindings
        ]
        if len(claim_ids) != len(set(claim_ids)) or len(claim_texts) != len(
            set(claim_texts)
        ):
            raise ValueError("CLAIM_SOURCE_BINDING_INVALID")
        for binding in self.claim_source_bindings:
            if (
                not {(source.type, source.id) for source in binding.source_refs}
                <= source_keys
            ):
                raise ValueError("CLAIM_SOURCE_BINDING_INVALID")
            if binding.evidence_span_refs and binding.source_excerpts != [
                span.text for span in binding.evidence_span_refs
            ]:
                raise ValueError("CLAIM_SOURCE_EVIDENCE_SPAN_EXCERPT_MISMATCH")
            for span in binding.evidence_span_refs:
                matches = [
                    source
                    for source in binding.source_refs
                    if any(
                        item.evidence_span_id == span.evidence_span_id
                        for item in source_by_key[
                            (source.type, source.id)
                        ].evidence_spans
                    )
                ]
                if len(matches) != 1:
                    raise ValueError("CLAIM_SOURCE_EVIDENCE_SPAN_INVALID")
                source = source_by_key[(matches[0].type, matches[0].id)]
                expected = next(
                    item
                    for item in source.evidence_spans
                    if item.evidence_span_id == span.evidence_span_id
                )
                if span != expected or span.text not in binding.source_excerpts:
                    raise ValueError("CLAIM_SOURCE_EVIDENCE_SPAN_INVALID")
        script_gate = {
            "schema_version": "vcos.script-authority-gate.v1",
            "status": "PASS",
            "script_hash": self.approved_script.script_hash,
            "word_count": self.approved_script.word_count,
            "estimated_duration_ms": (self.approved_script.estimated_duration_ms),
            "repeated_sentence_ratio": (self.approved_script.repeated_sentence_ratio),
            "sections": [
                section.model_dump(mode="json")
                for section in self.approved_script.sections
            ],
            "claim_binding_hashes": [
                binding.binding_hash for binding in self.claim_source_bindings
            ],
            "duration_contract_hash": (self.duration_contract.duration_contract_hash),
        }
        if self.approved_script.gate_receipt_hash != semantic_hash(script_gate):
            raise ValueError("APPROVED_SCRIPT_GATE_RECEIPT_MISMATCH")
        return self


class V2SupportAuthorityResult(_StrictFrozenModel):
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    envelope_hash: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["APPROVED"] = "APPROVED"
    replayed: bool
    approved_script_hash: str = Field(pattern=_SHA256_PATTERN)
    approved_script_word_count: int = Field(gt=0)
    exact_source_refs: list[V2FrozenSourceRef] = Field(min_length=2)
    reason_codes: list[str] = Field(min_length=1)


class _ResolvedSupportAuthority(_StrictFrozenModel):
    project_ref: V2ExactAuthorityRef
    admission_ref: V2ExactAuthorityRef
    profile_ref: V2ExactAuthorityRef
    compiled_policy_ref: V2ExactAuthorityRef
    effective_context_ref: V2ExactAuthorityRef
    production_lane: Literal["LONG_FORM"]
    title: str
    expected_language: str
    duration_contract: ProductionDurationContractV2
    frozen_sources: list[V2FrozenSourceRef]
    forbidden_claims: list[str]
    forbidden_style_terms: list[str]
    verified_destination: V2VerifiedDestinationAuthority
    execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"]
    idempotency_hash: str
    input_fingerprint: str


class V2SupportAuthorityService:
    """Resolve, generate, validate, and seal one immutable support envelope."""

    def __init__(
        self,
        session: Session,
        *,
        producer: TrustedV2SupportProducer | None = None,
    ):
        self.session = session
        self.producer = producer or LLMRouterV2SupportProducer(session)

    def prepare(
        self,
        command: V2SupportAuthorityPrepareCommand,
        *,
        producer: TrustedV2SupportProducer | None = None,
    ) -> V2SupportAuthorityResult:
        # The project row is the serialization lock for the one-envelope rule.
        project = self.session.scalar(
            select(VideoProject)
            .where(VideoProject.id == command.video_project_id)
            .with_for_update()
        )
        if project is None:
            raise NotFoundError(f"video project not found: {command.video_project_id}")
        resolved = self._resolve(command=command, project=project)
        qualified_run = None
        qualification_receipt = None
        qualification_memory: dict[str, Any] | None = None
        qualification_runtime_contract: dict[str, Any] | None = None
        if command.execution_mode == "REAL_LONG_FORM_PRODUCTION":
            from app.db.models.script_qualification import ScriptQualificationRun
            from app.services.script_qualification import (
                ScriptQualificationService,
                ScriptRuntimeContractResolver,
            )
            from app.services.script_qualification_authority import (
                validate_memory_digest,
            )

            admission = self.session.get(
                ProjectAdmissionDecision, project.project_admission_decision_id
            )
            if admission is None or admission.editorial_idea_candidate_id is None:
                raise ValidationFailureError("V2_SUPPORT_QUALIFICATION_LINEAGE_MISSING")
            qualification_receipt = ScriptQualificationService(
                self.session
            ).require_pass(
                command.script_qualification_run_id,
                candidate_id=admission.editorial_idea_candidate_id,
            )
            qualified_run = self.session.get(
                ScriptQualificationRun, command.script_qualification_run_id
            )
            if qualified_run is None or qualified_run.script_payload is None:
                raise ValidationFailureError("V2_SUPPORT_QUALIFIED_SCRIPT_MISSING")
            _script, _evidence, qualification_memory, _provenance = (
                ScriptQualificationService.qualification_output(qualification_receipt)
            )
            try:
                validate_memory_digest(
                    qualification_memory, expected_hash=qualified_run.memory_digest_hash
                )
            except ValueError as exc:
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFICATION_MEMORY_AUTHORITY_INVALID"
                ) from exc
            if qualification_memory.get("status") != "EMPTY_SAFE_DIGEST":
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFICATION_MEMORY_AUTHORITY_INVALID"
                )
            qualification_runtime_contract = ScriptRuntimeContractResolver.validate(
                qualified_run.runtime_contract,
                expected_hash=qualified_run.runtime_contract_hash,
            )
            if (
                qualification_runtime_contract["expected_language"].casefold()
                != resolved.expected_language.casefold()
                or qualification_runtime_contract["duration_contract"]
                != resolved.duration_contract.model_dump(mode="json")
                or qualification_runtime_contract["forbidden_claims"]
                != resolved.forbidden_claims
                or qualification_runtime_contract["forbidden_style_terms"]
                != resolved.forbidden_style_terms
            ):
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFICATION_RUNTIME_CONTRACT_MISMATCH"
                )
            resolved = resolved.model_copy(
                update={
                    "frozen_sources": [
                        *resolved.frozen_sources,
                        *self._qualification_frozen_sources(qualification_receipt),
                    ]
                }
            )
        # Resolve only idempotent catalog/role/capability authorities before
        # replay.  No MediaRenderRoutingDecision or provider effect is created.
        routes = self._create_native_routes(
            project=project,
            input_fingerprint=resolved.input_fingerprint,
            duration=resolved.duration_contract,
            execution_mode=resolved.execution_mode,
        )
        existing = self._existing_artifact(project.id)
        if existing is not None:
            return self._replay(
                artifact=existing,
                expected_idempotency_hash=resolved.idempotency_hash,
                expected_input_fingerprint=resolved.input_fingerprint,
                expected_routes=routes,
                expected_execution_mode=resolved.execution_mode,
                requested_ceiling_usd=command.max_budget_usd,
                reservation_run_id=command.budget_reservation_run_id,
            )
        budget = _build_zero_cost_budget(
            session=self.session,
            routes=routes,
            requested_ceiling_usd=command.max_budget_usd,
            execution_mode=resolved.execution_mode,
            policy_snapshot=self.session.get(
                CompiledChannelPolicySnapshot, project.policy_snapshot_id
            ),
            project_id=project.id,
            reservation_run_id=command.budget_reservation_run_id,
        )
        if qualification_receipt is not None:
            # Qualification owns the script's memory boundary.  Do not fetch
            # a later digest and misrepresent it as writer influence.
            memory_digest = qualification_memory
            memory_guidance = None
        else:
            memory_digest, memory_guidance = self._memory_guidance(
                project=project,
                effective_context_id=resolved.effective_context_ref.id,
                title=resolved.title,
            )
        production_context = V2SupportProductionContext(
            video_project_id=project.id,
            production_lane=resolved.production_lane,
            title=resolved.title,
            expected_language=(qualification_runtime_contract or {}).get(
                "expected_language", resolved.expected_language
            ),
            duration_contract=(
                resolved.duration_contract.__class__.model_validate(
                    qualification_runtime_contract["duration_contract"]
                )
                if qualification_runtime_contract is not None
                else resolved.duration_contract
            ),
            frozen_sources=resolved.frozen_sources,
            forbidden_claims=(qualification_runtime_contract or {}).get(
                "forbidden_claims", resolved.forbidden_claims
            ),
            forbidden_style_terms=(qualification_runtime_contract or {}).get(
                "forbidden_style_terms", resolved.forbidden_style_terms
            ),
            memory_guidance_digest=memory_digest,
        )
        if qualified_run is not None:
            validated = self._qualified_validated(
                qualification_receipt=qualification_receipt,
                context=production_context,
            )
        else:
            draft = (producer or self.producer).produce(production_context)
            validated = self._validate_draft(draft=draft, context=production_context)
        cross_modal_lineage = (
            self._cross_modal_script_lineage(qualified_run)
            if (
                qualified_run is not None
                and qualified_run.script_contract_version == "V2_SINGLE_SOURCE"
            )
            else None
        )
        rights = _build_visual_rights(
            policy_snapshot=self.session.get(
                CompiledChannelPolicySnapshot, project.policy_snapshot_id
            ),
            execution_mode=resolved.execution_mode,
        )
        gate_receipts = self._gate_receipts(
            resolved=resolved,
            validated=validated,
            rights=rights,
            routes=routes,
            budget=budget,
            memory_guidance=memory_guidance,
            script_qualification_run_id=command.script_qualification_run_id,
            qualification_memory=qualification_memory,
        )
        envelope = V2FrozenSupportEnvelope(
            idempotency_hash=resolved.idempotency_hash,
            input_fingerprint=resolved.input_fingerprint,
            project_ref=resolved.project_ref,
            admission_ref=resolved.admission_ref,
            profile_ref=resolved.profile_ref,
            compiled_policy_ref=resolved.compiled_policy_ref,
            effective_context_ref=resolved.effective_context_ref,
            production_lane=resolved.production_lane,
            execution_mode=resolved.execution_mode,
            duration_contract=resolved.duration_contract,
            frozen_sources=resolved.frozen_sources,
            approved_script=validated["script"],
            cross_modal_script_lineage=cross_modal_lineage,
            claim_source_bindings=validated["claim_bindings"],
            local_generated_card_rights=rights,
            memory_guidance_authority=memory_guidance,
            native_routes=routes,
            zero_cost_budget=budget,
            verified_destination=resolved.verified_destination,
            gate_receipts=gate_receipts,
        )
        artifact, version = self._seal(
            command=command,
            envelope=envelope,
        )
        return _result(
            artifact=artifact,
            version=version,
            envelope=envelope,
            replayed=False,
            reason_code="V2_SUPPORT_ENVELOPE_PREPARED",
        )

    @staticmethod
    def _qualification_frozen_sources(
        qualification_receipt: Any,
    ) -> list[V2FrozenSourceRef]:
        from app.services.script_qualification import ScriptQualificationService

        sources: list[V2FrozenSourceRef] = []
        by_source: dict[tuple[str, uuid.UUID], list[V2FrozenEvidenceSpan]] = {}
        _script, evidence_pack, _memory, _provenance = (
            ScriptQualificationService.qualification_output(qualification_receipt)
        )
        for raw_span in evidence_pack.get("spans", []):
            if not isinstance(raw_span, dict):
                raise ValidationFailureError("V2_SUPPORT_QUALIFIED_EVIDENCE_INVALID")
            try:
                evidence_id = uuid.UUID(str(raw_span["evidence_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFIED_EVIDENCE_INVALID"
                ) from exc
            if (
                raw_span.get("evidence_type") != "search_demand_evidence"
                or raw_span.get("authority_purpose") != "CLAIM_SOURCE"
                or raw_span.get("freshness_state") != "FRESH"
                or raw_span.get("source_quality_state") != "PASS"
                or raw_span.get("source_classification") != "TOPIC_CAPABLE"
                or not raw_span.get("source_class")
            ):
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFIED_EVIDENCE_AUTHORITY_INVALID"
                )
            try:
                frozen_span = V2FrozenEvidenceSpan(
                    evidence_span_id=str(raw_span["evidence_span_id"]),
                    text=str(raw_span["text"]),
                    start_byte=int(raw_span["start_byte"]),
                    end_byte=int(raw_span["end_byte"]),
                    span_hash=str(raw_span["span_hash"]),
                    source_snapshot_hash=str(raw_span["source_snapshot_hash"]),
                    freshness_state=str(raw_span["freshness_state"]),
                    source_quality_state=str(raw_span["source_quality_state"]),
                    authority_purpose=str(raw_span["authority_purpose"]),
                    source_class=str(raw_span["source_class"]),
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFIED_EVIDENCE_INVALID"
                ) from exc
            key = ("search_demand_evidence", evidence_id)
            by_source.setdefault(key, []).append(frozen_span)
        for (source_type, evidence_id), spans in sorted(
            by_source.items(), key=lambda item: (item[0][0], str(item[0][1]))
        ):
            if len({span.evidence_span_id for span in spans}) != len(spans):
                raise ValidationFailureError(
                    "V2_SUPPORT_QUALIFIED_EVIDENCE_SPAN_DUPLICATE"
                )
            sources.append(
                V2FrozenSourceRef(
                    type=source_type,
                    id=evidence_id,
                    ref=str(
                        next(
                            raw
                            for raw in evidence_pack["spans"]
                            if str(raw.get("evidence_id")) == str(evidence_id)
                        ).get("canonical_url")
                        or f"evidence://{evidence_id}"
                    ),
                    content_hash=spans[0].source_snapshot_hash,
                    source_kind="FACTUAL_SOURCE_SNAPSHOT",
                    fact_statements=[span.text for span in spans],
                    evidence_spans=spans,
                )
            )
        if not sources:
            raise ValidationFailureError("V2_SUPPORT_QUALIFIED_EVIDENCE_MISSING")
        return sources

    def _qualified_validated(
        self,
        *,
        qualification_receipt: Any,
        context: V2SupportProductionContext,
    ) -> dict[str, Any]:
        """Project the already-qualified writer result without a third LLM call."""

        from app.contracts.script_qualification import (
            QualifiedScriptOutput,
            QualifiedScriptOutputV2,
        )
        from app.services.canonical_script_compiler import CanonicalScriptCompiler
        from app.services.script_qualification import ScriptQualificationService

        try:
            script_payload, evidence_pack, _memory, provenance = (
                ScriptQualificationService.qualification_output(qualification_receipt)
            )
            # New production qualifications are V2 single-source outputs: only
            # section narration is provider-authored and canonical narration is
            # compiled locally.  Preserve the legacy reader solely for sealed
            # historical receipts; never coerce a V2 payload into that old
            # representation or ask the provider to supply a parallel script.
            if (
                isinstance(script_payload, dict)
                and "canonical_script" not in script_payload
            ):
                qualified = QualifiedScriptOutputV2.model_validate(script_payload)
                canonical = CanonicalScriptCompiler.compile(qualified)
                sections = [
                    V2GeneratedSection(
                        section_id=item.section_id,
                        heading=item.purpose,
                        narration=item.narration,
                    )
                    for item in sorted(
                        qualified.sections, key=lambda item: item.ordinal
                    )
                ]
                approved_script_text = canonical.canonical_script
            else:
                qualified = QualifiedScriptOutput.model_validate(script_payload)
                sections = [
                    V2GeneratedSection.model_validate(item.model_dump(mode="json"))
                    for item in qualified.sections
                ]
                approved_script_text = qualified.canonical_script
        except (ValidationError, ValueError) as exc:
            raise ValidationFailureError("V2_SUPPORT_QUALIFIED_SCRIPT_INVALID") from exc
        evidence_by_span = {
            str(item.get("evidence_span_id")): item
            for item in evidence_pack.get("spans", [])
            if isinstance(item, dict)
        }
        claims: list[V2GeneratedClaim] = []
        for claim in qualified.claims:
            citations: list[V2GeneratedCitation] = []
            for evidence_span_id in claim.evidence_span_ids:
                evidence = evidence_by_span.get(evidence_span_id)
                if evidence is None:
                    raise ValidationFailureError("V2_SUPPORT_QUALIFIED_CLAIM_UNBOUND")
                try:
                    evidence_id = uuid.UUID(str(evidence["evidence_id"]))
                except (KeyError, ValueError) as exc:
                    raise ValidationFailureError(
                        "V2_SUPPORT_QUALIFIED_CLAIM_UNBOUND"
                    ) from exc
                excerpt = str(evidence.get("text") or "")
                if len(excerpt) < 8:
                    raise ValidationFailureError("V2_SUPPORT_QUALIFIED_CLAIM_UNBOUND")
                citations.append(
                    V2GeneratedCitation(
                        source_ref_id=evidence_id,
                        source_ref_type=str(evidence.get("evidence_type") or ""),
                        evidence_span_id=evidence_span_id,
                        source_excerpt=excerpt,
                    )
                )
            claims.append(
                V2GeneratedClaim(
                    claim_id=claim.claim_id,
                    claim_text=claim.claim_text,
                    citations=citations,
                )
            )
        output_payload = {
            "approved_script_text": approved_script_text,
            "language": qualified.language,
            "sections": [item.model_dump(mode="json") for item in sections],
            "claims": [item.model_dump(mode="json") for item in claims],
        }
        writer = provenance.get("writer") if isinstance(provenance, dict) else {}
        expected_writer_output_hash = semantic_hash(script_payload)
        try:
            if writer.get("producer_type") == "OPENAI_BACKGROUND_NORMALIZED":
                from app.db.models.script_qualification import (
                    ScriptQualificationBackgroundAttempt,
                    ScriptWriterOutputNormalizationReceipt,
                )

                background_attempt_id = uuid.UUID(
                    str(writer["source_background_attempt_id"])
                )
                normalization_receipt_id = uuid.UUID(
                    str(writer["normalization_receipt_id"])
                )
                attempt = self.session.get(
                    ScriptQualificationBackgroundAttempt,
                    background_attempt_id,
                )
                normalization = self.session.get(
                    ScriptWriterOutputNormalizationReceipt,
                    normalization_receipt_id,
                )
                if (
                    attempt is None
                    or normalization is None
                    or attempt.provider_response_id
                    != writer.get("source_provider_response_id")
                    or attempt.provider_request_id
                    != writer.get("source_provider_request_id")
                    or attempt.input_fingerprint != writer.get("producer_input_hash")
                    or normalization.receipt_hash
                    != writer.get("normalization_receipt_hash")
                    or normalization.normalized_payload_hash
                    != writer.get("producer_output_hash")
                ):
                    raise ValueError("background normalization provenance drift")
                sealed_script = (
                    qualification_receipt.content.get("qualified_script")
                    if isinstance(qualification_receipt.content, dict)
                    else None
                )
                raw_v2_payload = (
                    {
                        "language": sealed_script.get("language"),
                        "sections": sealed_script.get("sections"),
                        "claims": sealed_script.get("claims"),
                    }
                    if isinstance(sealed_script, dict)
                    and sealed_script.get("script_contract_version")
                    == "V2_SINGLE_SOURCE"
                    else None
                )
                if (
                    raw_v2_payload is None
                    or QualifiedScriptOutputV2.model_validate(
                        raw_v2_payload
                    ).model_dump(mode="json")
                    != normalization.normalized_payload
                ):
                    raise ValueError("normalized V2 qualification payload drift")
                expected_writer_output_hash = semantic_hash(raw_v2_payload)
                producer_receipt = V2ProducerReceipt(
                    producer_type="OPENAI_BACKGROUND_NORMALIZED",
                    producer_version=str(writer["producer_version"]),
                    lane_name=str(writer["lane_name"]),
                    selected_model=str(writer["selected_model"]),
                    background_attempt_id=background_attempt_id,
                    provider_response_id=str(writer["source_provider_response_id"]),
                    provider_request_id=str(writer["source_provider_request_id"]),
                    normalization_receipt_id=normalization_receipt_id,
                    normalization_receipt_hash=str(
                        writer["normalization_receipt_hash"]
                    ),
                    source_typed_provider_output_hash=str(
                        writer["source_typed_provider_output_hash"]
                    ),
                    producer_input_hash=str(writer["producer_input_hash"]),
                    producer_output_hash=str(writer["producer_output_hash"]),
                    projected_output_hash=semantic_hash(output_payload),
                    qualification_receipt_hash=str(qualification_receipt.content_hash),
                )
            else:
                producer_receipt = V2ProducerReceipt(
                    producer_type="LLM_ROUTER",
                    producer_version=str(writer["prompt_version"]),
                    lane_name=str(writer["lane_name"]),
                    selected_model=str(writer["selected_model"]),
                    fallback_level=str(writer["fallback_level"]),
                    route_attempt_id=uuid.UUID(str(writer["route_attempt_id"])),
                    provider_attempt_id=(
                        uuid.UUID(str(writer["provider_attempt_id"]))
                        if writer.get("provider_attempt_id")
                        else None
                    ),
                    llm_run_snapshot_id=(
                        uuid.UUID(str(writer["llm_run_snapshot_id"]))
                        if writer.get("llm_run_snapshot_id")
                        else None
                    ),
                    producer_input_hash=str(writer["producer_input_hash"]),
                    producer_output_hash=str(writer["producer_output_hash"]),
                    projected_output_hash=semantic_hash(output_payload),
                    qualification_receipt_hash=str(qualification_receipt.content_hash),
                )
            draft = V2TrustedSupportDraft(
                **output_payload, producer_receipt=producer_receipt
            )
        except (KeyError, ValidationError, ValueError) as exc:
            raise ValidationFailureError(
                "V2_SUPPORT_QUALIFIED_WRITER_RECEIPT_INVALID"
            ) from exc
        return self._validate_draft(
            draft=draft,
            context=context,
            expected_producer_input_hash=str(writer.get("producer_input_hash") or ""),
            expected_producer_output_hash=expected_writer_output_hash,
            qualification_receipt_hash=str(qualification_receipt.content_hash),
        )

    def _memory_guidance(
        self,
        *,
        project: VideoProject,
        effective_context_id: uuid.UUID,
        title: str,
    ) -> tuple[dict[str, Any], V2MemoryGuidanceAuthority]:
        """Retrieve a prompt-safe digest and freeze only its provenance refs."""

        effective = self.session.get(
            EffectiveChannelRuntimeContextSnapshot, effective_context_id
        )
        if effective is None or effective.video_project_id != project.id:
            raise ValidationFailureError("V2_MEMORY_GUIDANCE_CONTEXT_MISMATCH")
        try:
            digest = AgentMemoryDigestInjectionService(
                self.session
            ).retrieve_and_record_digest(
                package_id=None,
                effective=effective,
                agent_key="ScriptWriterAgent",
                use_case="script",
                query_text=title,
                max_selected_facets=3,
                max_digest_chars=1200,
                requested_facet_types=[],
                vector_enabled=get_settings().vector_retrieval_enabled,
            )
            manifest_id = uuid.UUID(str(digest["memory_influence_manifest_id"]))
            retrieval_id = uuid.UUID(str(digest["retrieval_manifest_id"]))
            record_id = uuid.UUID(str(digest["agent_memory_application_record_id"]))
            scope_status = str(
                (digest.get("r3d7_influence_manifest_ref") or {}).get("scope_status")
            )
            digest_hash = str(digest["digest_hash"])
        except (KeyError, TypeError, ValueError, ValidationFailureError) as exc:
            raise ValidationFailureError("V2_MEMORY_GUIDANCE_RETRIEVAL_FAILED") from exc
        if (
            digest.get("no_raw_memory") is not True
            or len(digest_hash) != 64
            or scope_status not in {"PASS", "EMPTY_SAFE_DIGEST"}
        ):
            raise ValidationFailureError("V2_MEMORY_GUIDANCE_DIGEST_UNSAFE")
        authority_payload = {
            "memory_influence_manifest_id": str(manifest_id),
            "retrieval_manifest_id": str(retrieval_id),
            "agent_memory_application_record_id": str(record_id),
            "digest_hash": digest_hash,
            "scope_status": scope_status,
        }
        authority = V2MemoryGuidanceAuthority(
            **authority_payload,
            content_hash=semantic_hash(authority_payload),
        )
        prompt_digest = {
            "digest_type": digest.get("digest_type"),
            "digest_version": digest.get("digest_version"),
            "status": digest.get("status"),
            "reason_codes": list(digest.get("reason_codes") or []),
            "lessons": list(digest.get("lessons") or []),
            "selected_memory_facet_refs": list(
                digest.get("selected_memory_facet_refs") or []
            ),
            "memory_influence_manifest_id": str(manifest_id),
            "digest_hash": digest_hash,
            "non_factual_guidance_only": True,
            "no_raw_analytics": True,
            "no_raw_memory": True,
        }
        return prompt_digest, authority

    def _resolve(
        self,
        *,
        command: V2SupportAuthorityPrepareCommand,
        project: VideoProject,
    ) -> _ResolvedSupportAuthority:
        if (
            project.schema_version != "v2"
            or project.planning_source_type not in _SOURCE_TYPES
            or project.production_lane not in _JOB_TYPES_BY_LANE
            or str(project.planning_source_type) != command.source_type
            or project.planning_source_type != "LONG_FORM_PLAN"
            or project.production_lane != "LONG_FORM"
        ):
            raise ValidationFailureError("V2_SUPPORT_SOURCE_PROJECT_MISMATCH")
        admission = (
            self.session.get(
                ProjectAdmissionDecision,
                project.project_admission_decision_id,
            )
            if project.project_admission_decision_id is not None
            else None
        )
        if (
            admission is None
            or admission.schema_version != "v2"
            or admission.decision != "ADMIT"
            or admission.admitted_video_project_id != project.id
            or admission.id != project.project_admission_decision_id
            or admission.company_id != project.company_id
            or admission.channel_workspace_id != project.channel_workspace_id
            or admission.channel_profile_version_id
            != project.channel_profile_version_id
            or admission.policy_snapshot_id != project.policy_snapshot_id
            or admission.planning_source_type != command.source_type
            or admission.production_lane != project.production_lane
            or not _valid_sha256(admission.decision_hash)
        ):
            raise ValidationFailureError("V2_SUPPORT_ADMISSION_NOT_ADMITTED")
        source_matches = admission.editorial_calendar_slot_id == command.source_id
        if not source_matches:
            raise ValidationFailureError("V2_SUPPORT_SOURCE_PROJECT_MISMATCH")

        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        profile = (
            self.session.get(
                ChannelProfileVersion,
                project.channel_profile_version_id,
            )
            if project.channel_profile_version_id is not None
            else None
        )
        policy = self.session.get(
            CompiledChannelPolicySnapshot,
            project.policy_snapshot_id,
        )
        if (
            channel is None
            or profile is None
            or policy is None
            or channel.company_id != project.company_id
            or profile.channel_workspace_id != channel.id
            or policy.channel_workspace_id != channel.id
            or policy.channel_profile_version_id != profile.id
            or profile.status not in {"approved", "active"}
            or policy.status not in {"approved", "active"}
            or content_hash(profile.profile_input or {}) != profile.profile_input_hash
            or content_hash(policy.compiled_payload or {}) != policy.content_hash
            or policy.profile_input_hash != profile.profile_input_hash
        ):
            raise ValidationFailureError("V2_SUPPORT_AUTHORITY_HASH_MISMATCH")

        effective = (
            self.session.get(
                EffectiveChannelRuntimeContextSnapshot,
                project.effective_context_snapshot_id,
            )
            if project.effective_context_snapshot_id is not None
            else None
        )
        if (
            effective is None
            or effective.video_project_id != project.id
            or effective.company_id != project.company_id
            or effective.channel_workspace_id != channel.id
            or effective.channel_profile_version_id != profile.id
            or effective.compiled_policy_snapshot_id != policy.id
            or effective.compile_status != "PASS"
            or _effective_context_hash(effective) != effective.context_hash
        ):
            raise ValidationFailureError("V2_SUPPORT_EFFECTIVE_CONTEXT_NOT_PASS")
        try:
            duration = ProductionDurationContractV2.model_validate(
                project.duration_contract
            )
        except ValidationError as exc:
            raise ValidationFailureError(
                "V2_SUPPORT_DURATION_CONTRACT_INVALID"
            ) from exc
        if (
            duration.source_profile_version_id != profile.id
            or duration.source_policy_snapshot_id != policy.id
            or admission.duration_contract != duration.model_dump(mode="json")
        ):
            raise ValidationFailureError("V2_SUPPORT_DURATION_CONTRACT_INVALID")

        frozen_sources = self._resolve_sources(
            command=command,
            admission=admission,
            project=project,
        )
        destination = _verified_destination(channel)
        project_ref = V2ExactAuthorityRef(
            type="video_project",
            id=project.id,
            ref=f"video-project://{project.id}",
            content_hash=_project_authority_hash(project),
        )
        admission_ref = V2ExactAuthorityRef(
            type="project_admission_decision",
            id=admission.id,
            ref=f"project-admission://{admission.id}",
            content_hash=str(admission.decision_hash),
        )
        profile_ref = V2ExactAuthorityRef(
            type="channel_profile_version",
            id=profile.id,
            ref=f"channel-profile-version://{profile.id}",
            content_hash=profile.profile_input_hash,
        )
        policy_ref = V2ExactAuthorityRef(
            type="compiled_channel_policy_snapshot",
            id=policy.id,
            ref=f"compiled-channel-policy://{policy.id}",
            content_hash=policy.content_hash,
        )
        effective_ref = V2ExactAuthorityRef(
            type="effective_channel_runtime_context",
            id=effective.id,
            ref=f"effective-context://{effective.id}",
            content_hash=effective.context_hash,
        )
        expected_language = str(
            (effective.market_locale_context_json or {}).get("content_language")
            or channel.primary_language
        ).strip()
        if not expected_language:
            raise ValidationFailureError("V2_SUPPORT_EFFECTIVE_CONTEXT_NOT_PASS")
        forbidden_claims = _string_list(
            (effective.safety_forbidden_claims_context_json or {}).get(
                "forbidden_claims"
            )
        )
        forbidden_style_terms = _string_list(
            (effective.brand_voice_persona_context_json or {}).get("forbidden_style")
        )
        input_payload = {
            "schema_version": "vcos.v2-support-input-fingerprint.v1",
            "project_ref": project_ref.model_dump(mode="json"),
            "admission_ref": admission_ref.model_dump(mode="json"),
            "profile_ref": profile_ref.model_dump(mode="json"),
            "compiled_policy_ref": policy_ref.model_dump(mode="json"),
            "effective_context_ref": effective_ref.model_dump(mode="json"),
            "production_lane": project.production_lane,
            "duration_contract": duration.model_dump(mode="json"),
            "frozen_sources": [
                source.model_dump(mode="json") for source in frozen_sources
            ],
            "verified_destination_hash": destination.content_hash,
            "requested_budget_ceiling_usd": _decimal_string(command.max_budget_usd),
            "execution_mode": command.execution_mode,
            "budget_reservation_run_id": (
                str(command.budget_reservation_run_id)
                if command.budget_reservation_run_id is not None
                else None
            ),
            "idempotency_hash": semantic_hash(
                {"idempotency_key": command.idempotency_key}
            ),
        }
        idempotency_hash = input_payload["idempotency_hash"]
        return _ResolvedSupportAuthority(
            project_ref=project_ref,
            admission_ref=admission_ref,
            profile_ref=profile_ref,
            compiled_policy_ref=policy_ref,
            effective_context_ref=effective_ref,
            production_lane=project.production_lane,
            title=project.title,
            expected_language=expected_language,
            duration_contract=duration,
            frozen_sources=frozen_sources,
            forbidden_claims=forbidden_claims,
            forbidden_style_terms=forbidden_style_terms,
            verified_destination=destination,
            execution_mode=command.execution_mode,
            idempotency_hash=idempotency_hash,
            input_fingerprint=semantic_hash(input_payload),
        )

    def _resolve_sources(
        self,
        *,
        command: V2SupportAuthorityPrepareCommand,
        admission: ProjectAdmissionDecision,
        project: VideoProject,
    ) -> list[V2FrozenSourceRef]:
        slot = self.session.get(EditorialCalendarSlot, command.source_id)
        if (
            slot is None
            or slot.schema_version != "v2"
            or slot.company_id != project.company_id
            or slot.channel_workspace_id != project.channel_workspace_id
            or slot.policy_snapshot_id != project.policy_snapshot_id
            or slot.production_lane != project.production_lane
            or slot.status != "ADMITTED"
        ):
            raise ValidationFailureError("V2_SUPPORT_SOURCE_PROJECT_MISMATCH")
        preflight = (
            self.session.get(
                IdeaMarketPreflight,
                admission.idea_market_preflight_id,
            )
            if admission.idea_market_preflight_id is not None
            else None
        )
        if (
            preflight is None
            or preflight.company_id != project.company_id
            or preflight.channel_workspace_id != project.channel_workspace_id
            or preflight.editorial_calendar_slot_id != slot.id
            or preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
        ):
            raise ValidationFailureError("V2_SUPPORT_PREFLIGHT_NOT_PASS")

        sources: list[V2FrozenSourceRef] = [
            _editorial_slot_source(slot),
            _preflight_source(preflight),
        ]
        if preflight.search_intent_map_id is not None:
            search_intent = self.session.get(
                SearchIntentMap,
                preflight.search_intent_map_id,
            )
            if (
                search_intent is None
                or search_intent.company_id != project.company_id
                or search_intent.channel_workspace_id != project.channel_workspace_id
            ):
                raise ValidationFailureError("V2_SUPPORT_TYPED_SOURCE_MISMATCH")
            sources.append(_search_intent_source(search_intent))
        if preflight.audience_target_pack_id is not None:
            audience = self.session.get(
                AudienceTargetPack,
                preflight.audience_target_pack_id,
            )
            if (
                audience is None
                or audience.company_id != project.company_id
                or audience.channel_workspace_id != project.channel_workspace_id
            ):
                raise ValidationFailureError("V2_SUPPORT_TYPED_SOURCE_MISMATCH")
            sources.append(_audience_source(audience))
        return sources

    def _validate_draft(
        self,
        *,
        draft: V2TrustedSupportDraft,
        context: V2SupportProductionContext,
        expected_producer_input_hash: str | None = None,
        expected_producer_output_hash: str | None = None,
        qualification_receipt_hash: str | None = None,
    ) -> dict[str, Any]:
        expected_input_hash = expected_producer_input_hash or semantic_hash(
            context.model_dump(mode="json")
        )
        if draft.producer_receipt.producer_input_hash != expected_input_hash:
            raise ValidationFailureError("V2_SUPPORT_PRODUCER_INPUT_HASH_MISMATCH")
        output_payload = {
            "approved_script_text": draft.approved_script_text,
            "language": draft.language,
            "sections": [item.model_dump(mode="json") for item in draft.sections],
            "claims": [item.model_dump(mode="json") for item in draft.claims],
        }
        if expected_producer_output_hash is None and (
            draft.producer_receipt.producer_output_hash != semantic_hash(output_payload)
            or draft.producer_receipt.projected_output_hash is not None
            or draft.producer_receipt.qualification_receipt_hash is not None
        ):
            raise ValidationFailureError("V2_SUPPORT_PRODUCER_OUTPUT_HASH_MISMATCH")
        if expected_producer_output_hash is not None and (
            draft.producer_receipt.producer_output_hash != expected_producer_output_hash
            or draft.producer_receipt.projected_output_hash
            != semantic_hash(output_payload)
            or draft.producer_receipt.qualification_receipt_hash
            != qualification_receipt_hash
        ):
            raise ValidationFailureError("V2_SUPPORT_QUALIFICATION_PROVENANCE_MISMATCH")
        expected_language = context.expected_language.lower()
        observed_language = draft.language.lower()
        if (
            observed_language != expected_language
            and not observed_language.startswith(f"{expected_language}-")
            and not expected_language.startswith(f"{observed_language}-")
        ):
            raise ValidationFailureError("V2_SUPPORT_SCRIPT_LANGUAGE_MISMATCH")
        section_ids = [section.section_id for section in draft.sections]
        section_narrations = {
            _normalized_text(section.narration) for section in draft.sections
        }
        minimum_sections = 3
        if (
            len(draft.sections) < minimum_sections
            or len(section_ids) != len(set(section_ids))
            or len(section_narrations) != len(draft.sections)
            or _normalized_text(draft.approved_script_text)
            != _normalized_text(
                " ".join(section.narration for section in draft.sections)
            )
        ):
            raise ValidationFailureError("V2_SUPPORT_SCRIPT_SECTIONS_INVALID")
        script = draft.approved_script_text.strip()
        words = re.findall(r"\b[\w'-]+\b", script, flags=re.UNICODE)
        estimated_duration_ms = round(len(words) / 150 * 60_000)
        if (
            len(words) < 24
            or estimated_duration_ms < context.duration_contract.minimum_duration_ms
            or estimated_duration_ms > context.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError("V2_SUPPORT_SCRIPT_DURATION_INVALID")
        sentences = _sentences(script)
        normalized_sentences = [_normalized_text(item) for item in sentences]
        repeated = len(normalized_sentences) - len(set(normalized_sentences))
        repeated_ratio = (
            round(repeated / len(normalized_sentences), 6)
            if normalized_sentences
            else 1.0
        )
        if len(sentences) < 3 or repeated_ratio > 0.2:
            raise ValidationFailureError("V2_SUPPORT_SCRIPT_PADDING_DETECTED")
        lowered_script = script.casefold()
        forbidden_hits = [
            term
            for term in [
                *context.forbidden_claims,
                *context.forbidden_style_terms,
            ]
            if term.strip() and term.casefold() in lowered_script
        ]
        if forbidden_hits:
            raise ValidationFailureError("V2_SUPPORT_SCRIPT_POLICY_VIOLATION")

        source_by_identity = {
            (source.type, source.id): source for source in context.frozen_sources
        }
        claim_ids: set[str] = set()
        normalized_claim_texts: set[str] = set()
        bindings: list[V2ClaimSourceBinding] = []
        for claim in draft.claims:
            normalized_claim = _normalized_text(claim.claim_text)
            if (
                claim.claim_id in claim_ids
                or normalized_claim in normalized_claim_texts
                or normalized_claim not in _normalized_text(script)
            ):
                raise ValidationFailureError("V2_SUPPORT_CLAIM_SOURCE_BINDING_INVALID")
            claim_ids.add(claim.claim_id)
            normalized_claim_texts.add(normalized_claim)
            exact_refs: list[V2ExactAuthorityRef] = []
            excerpts: list[str] = []
            seen_sources: set[tuple[str, uuid.UUID]] = set()
            evidence_span_refs: list[V2FrozenEvidenceSpan] = []
            for citation in claim.citations:
                if citation.source_ref_type is None:
                    candidates = [
                        source
                        for source in context.frozen_sources
                        if source.id == citation.source_ref_id
                    ]
                    source = candidates[0] if len(candidates) == 1 else None
                else:
                    source = source_by_identity.get(
                        (citation.source_ref_type, citation.source_ref_id)
                    )
                if source is None or (
                    citation.evidence_span_id is None
                    and (
                        source.evidence_spans
                        or citation.source_excerpt not in source.fact_statements
                    )
                ):
                    raise ValidationFailureError(
                        "V2_SUPPORT_CLAIM_SOURCE_BINDING_INVALID"
                    )
                if citation.evidence_span_id is not None:
                    span = next(
                        (
                            item
                            for item in source.evidence_spans
                            if item.evidence_span_id == citation.evidence_span_id
                        ),
                        None,
                    )
                    if span is None or citation.source_excerpt != span.text:
                        raise ValidationFailureError(
                            "V2_SUPPORT_CLAIM_EVIDENCE_SPAN_MISMATCH"
                        )
                    evidence_span_refs.append(span)
                source_key = (source.type, source.id)
                if source_key not in seen_sources:
                    exact_refs.append(
                        V2ExactAuthorityRef(
                            type=source.type,
                            id=source.id,
                            ref=source.ref,
                            content_hash=source.content_hash,
                        )
                    )
                    seen_sources.add(source_key)
                excerpts.append(citation.source_excerpt)
            binding_payload = {
                "claim_id": claim.claim_id,
                "claim_text": claim.claim_text,
                "source_refs": [item.model_dump(mode="json") for item in exact_refs],
                "source_excerpts": excerpts,
                "evidence_span_refs": [
                    item.model_dump(mode="json") for item in evidence_span_refs
                ],
            }
            bindings.append(
                V2ClaimSourceBinding(
                    **binding_payload,
                    binding_hash=semantic_hash(binding_payload),
                )
            )
        gate_payload = {
            "schema_version": "vcos.script-authority-gate.v1",
            "status": "PASS",
            "script_hash": semantic_hash({"approved_script_text": script}),
            "word_count": len(words),
            "estimated_duration_ms": estimated_duration_ms,
            "repeated_sentence_ratio": repeated_ratio,
            "sections": [section.model_dump(mode="json") for section in draft.sections],
            "claim_binding_hashes": [binding.binding_hash for binding in bindings],
            "duration_contract_hash": (
                context.duration_contract.duration_contract_hash
            ),
        }
        script_authority = V2ApprovedScriptProvenance(
            approved_script_text=script,
            script_hash=gate_payload["script_hash"],
            word_count=len(words),
            estimated_duration_ms=estimated_duration_ms,
            repeated_sentence_ratio=repeated_ratio,
            language=draft.language,
            sections=draft.sections,
            producer_receipt=draft.producer_receipt,
            gate_receipt_hash=semantic_hash(gate_payload),
        )
        return {"script": script_authority, "claim_bindings": bindings}

    def _create_native_routes(
        self,
        *,
        project: VideoProject,
        input_fingerprint: str,
        duration: ProductionDurationContractV2,
        execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"],
    ) -> list[V2NativeRouteReceipt]:
        if execution_mode == "REAL_LONG_FORM_PRODUCTION":
            return self._create_real_provider_routes(
                project=project,
                input_fingerprint=input_fingerprint,
                duration=duration,
            )
        lane_routes = _JOB_TYPES_BY_LANE.get(str(project.production_lane))
        if lane_routes is None:
            raise ValidationFailureError("V2_SUPPORT_NATIVE_ROUTE_INVALID")
        roles = MediaProviderRoleService(self.session)
        roles.ensure_matrix()
        matrix = ProviderCapabilityMatrixService(self.session)
        routing_catalog = ConfigRegistryService(self.session).validate_catalog(
            _MEDIA_ROUTING_POLICY_CATALOG
        )
        routing_items = {
            str(item.get("job_type")): item
            for item in routing_catalog.content.get("items", [])
            if isinstance(item, dict) and item.get("job_type")
        }
        receipts: list[V2NativeRouteReceipt] = []
        for stage in ("MEDIA", "RENDER", "QC"):
            expected_provider = _LOCAL_PROVIDER_BY_STAGE[stage]
            job_type = lane_routes[stage]
            role = roles.require_role(expected_provider)
            capability = matrix.find_entry(
                provider_key=expected_provider,
                job_type=job_type,
            )
            routing_item = routing_items.get(job_type)
            if (
                role.provider_key != expected_provider
                or role.is_enabled is not True
                or role.is_real_provider is not False
                or role.supports_real_execution is not True
                or capability is None
                or capability.capability != "SUPPORTED"
                or (
                    capability.max_duration_seconds is not None
                    and Decimal(duration.target_duration_ms) / Decimal("1000")
                    > capability.max_duration_seconds
                )
                or not isinstance(routing_item, dict)
                or routing_item.get("provider_key") != expected_provider
            ):
                raise ValidationFailureError("V2_SUPPORT_NATIVE_ROUTE_INVALID")
            receipts.append(
                _route_receipt(
                    stage=stage,
                    project_id=project.id,
                    input_fingerprint=input_fingerprint,
                    role=role,
                    capability=capability,
                    job_type=job_type,
                    routing_policy_ref=(
                        "config://media_provider_routing_policy_catalog/"
                        f"{routing_catalog.catalog_version}/{job_type}"
                    ),
                    routing_policy_hash=semantic_hash(routing_item),
                )
            )
        archive_role = roles.require_role(_LOCAL_PROVIDER_BY_STAGE["ARCHIVE"])
        if (
            archive_role.is_enabled is not True
            or archive_role.is_real_provider is not False
            or archive_role.supports_real_execution is not True
        ):
            raise ValidationFailureError("V2_SUPPORT_NATIVE_ROUTE_INVALID")
        receipts.append(
            _route_receipt(
                stage="ARCHIVE",
                project_id=project.id,
                input_fingerprint=input_fingerprint,
                role=archive_role,
                capability=None,
                job_type=None,
                routing_policy_ref=(
                    "domain://v2-support-authority/local-archive-route"
                ),
                routing_policy_hash=semantic_hash(
                    {
                        "stage": "ARCHIVE",
                        "provider_key": archive_role.provider_key,
                        "provider_role_id": str(archive_role.id),
                        "paid_provider_call": False,
                    }
                ),
            )
        )
        return receipts

    def _create_real_provider_routes(
        self,
        *,
        project: VideoProject,
        input_fingerprint: str,
        duration: ProductionDurationContractV2,
    ) -> list[V2NativeRouteReceipt]:
        """Bind the real lane to its contract-selected providers only.

        The local adapter remains a valid *visual composition* boundary, but
        it is never the narration or archive authority for real long-form
        production.  Provider credentials are deliberately represented by a
        stable reference, not by secret material in an immutable package.
        """

        policy_snapshot = self.session.get(
            CompiledChannelPolicySnapshot, project.policy_snapshot_id
        )
        try:
            scoped = ChannelScopedPolicy.model_validate(
                (policy_snapshot.compiled_payload or {}).get("channel_scoped_policy")
                if policy_snapshot is not None
                else None
            )
        except ValidationError as exc:
            raise ValidationFailureError("V2_REAL_PROVIDER_POLICY_INVALID") from exc
        if (
            scoped.voice_policy.provider != "elevenlabs"
            or not scoped.provider_usage_policy.elevenlabs.enabled
            or not scoped.provider_usage_policy.elevenlabs.final_narration_authority
            or not scoped.provider_usage_policy.native_ffmpeg_final_render_authority
            or not scoped.provider_usage_policy.drive_archive_required_before_cleanup
            or not scoped.publish_policy.drive_archive_required
        ):
            raise ValidationFailureError("V2_REAL_PROVIDER_POLICY_NOT_AUTHORIZED")
        lane_routes = _JOB_TYPES_BY_LANE.get(str(project.production_lane))
        if lane_routes is None:
            raise ValidationFailureError("V2_SUPPORT_NATIVE_ROUTE_INVALID")
        roles = MediaProviderRoleService(self.session)
        roles.ensure_matrix()
        matrix = ProviderCapabilityMatrixService(self.session)
        routing_catalog = ConfigRegistryService(self.session).validate_catalog(
            _MEDIA_ROUTING_POLICY_CATALOG
        )
        routing_items = {
            str(item.get("job_type")): item
            for item in routing_catalog.content.get("items", [])
            if isinstance(item, dict) and item.get("job_type")
        }
        route_specs = (
            (
                "MEDIA",
                "elevenlabs",
                "v2-elevenlabs-narration",
                "LONG_VOICE_GENERATION",
                True,
            ),
            (
                "RENDER",
                "native_ffmpeg_renderer",
                "v2-local-native",
                lane_routes["RENDER"],
                False,
            ),
            ("QC", "vcos_media_qc", "v2-local-native", lane_routes["QC"], False),
        )
        receipts: list[V2NativeRouteReceipt] = []
        for stage, provider_key, adapter_key, job_type, paid in route_specs:
            role = roles.require_role(provider_key)
            capability = matrix.find_entry(
                provider_key=provider_key,
                job_type=job_type,
            )
            routing_item = routing_items.get(job_type)
            if (
                role.is_enabled is not True
                or role.supports_real_execution is not True
                or capability is None
                or capability.capability != "SUPPORTED"
                or not isinstance(routing_item, dict)
                or routing_item.get("provider_key") != provider_key
                or (
                    capability.max_duration_seconds is not None
                    and Decimal(duration.target_duration_ms) / Decimal("1000")
                    > capability.max_duration_seconds
                )
            ):
                raise ValidationFailureError("V2_REAL_PROVIDER_ROUTE_INVALID")
            receipts.append(
                _route_receipt(
                    stage=stage,
                    project_id=project.id,
                    input_fingerprint=input_fingerprint,
                    role=role,
                    capability=capability,
                    job_type=job_type,
                    routing_policy_ref=(
                        "config://media_provider_routing_policy_catalog/"
                        f"{routing_catalog.catalog_version}/{job_type}"
                    ),
                    routing_policy_hash=semantic_hash(routing_item),
                    adapter_key=adapter_key,
                    paid_provider_call=paid,
                    max_cost_usd=(
                        Decimal(str(scoped.budget_policy.max_estimated_cost_per_video))
                        if paid
                        else Decimal("0")
                    ),
                )
            )
        archive_role = roles.require_role("google_drive_archive")
        if (
            archive_role.is_enabled is not True
            or archive_role.is_real_provider is not True
            or archive_role.supports_real_execution is not True
        ):
            raise ValidationFailureError("V2_REAL_PROVIDER_ROUTE_INVALID")
        receipts.append(
            _route_receipt(
                stage="ARCHIVE",
                project_id=project.id,
                input_fingerprint=input_fingerprint,
                role=archive_role,
                capability=None,
                job_type=None,
                routing_policy_ref="domain://v2-support-authority/google-drive-remote-archive",
                routing_policy_hash=semantic_hash(
                    {
                        "stage": "ARCHIVE",
                        "provider_key": archive_role.provider_key,
                        "provider_role_id": str(archive_role.id),
                        "remote_archive_required": True,
                    }
                ),
                adapter_key="v2-google-drive-remote",
                paid_provider_call=False,
                max_cost_usd=Decimal("0"),
            )
        )
        return receipts

    def _gate_receipts(
        self,
        *,
        resolved: _ResolvedSupportAuthority,
        validated: dict[str, Any],
        rights: V2LocalGeneratedCardRights,
        routes: list[V2NativeRouteReceipt],
        budget: V2ZeroCostBudgetAuthority,
        memory_guidance: V2MemoryGuidanceAuthority | None,
        script_qualification_run_id: uuid.UUID | None = None,
        qualification_memory: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        receipts = [
            {
                "gate_key": "exact_authority_lineage",
                "status": "PASS",
                "receipt_hash": semantic_hash(
                    {
                        "project": resolved.project_ref.model_dump(mode="json"),
                        "admission": resolved.admission_ref.model_dump(mode="json"),
                        "profile": resolved.profile_ref.model_dump(mode="json"),
                        "policy": resolved.compiled_policy_ref.model_dump(mode="json"),
                        "effective": resolved.effective_context_ref.model_dump(
                            mode="json"
                        ),
                    }
                ),
            },
            {
                "gate_key": "frozen_source_preflight",
                "status": "PASS",
                "receipt_hash": semantic_hash(
                    [
                        source.model_dump(mode="json")
                        for source in resolved.frozen_sources
                    ]
                ),
            },
            {
                "gate_key": "approved_script_integrity",
                "status": "PASS",
                "receipt_hash": validated["script"].gate_receipt_hash,
            },
            {
                "gate_key": "claim_source_bindings",
                "status": "PASS",
                "receipt_hash": semantic_hash(
                    [binding.binding_hash for binding in validated["claim_bindings"]]
                ),
            },
            {
                "gate_key": "local_generated_card_rights",
                "status": "PASS",
                "receipt_hash": rights.content_hash,
            },
            (
                {
                    "gate_key": "memory_guidance_digest",
                    "status": "PASS",
                    "receipt_hash": memory_guidance.content_hash,
                }
                if memory_guidance is not None
                else {
                    "gate_key": "qualification_memory_digest",
                    "status": "PASS_EMPTY",
                    "receipt_hash": str(
                        (qualification_memory or {}).get("digest_hash") or ""
                    ),
                }
            ),
            {
                "gate_key": "native_provider_capability",
                "status": "PASS",
                "receipt_hash": semantic_hash([route.route_hash for route in routes]),
            },
            {
                "gate_key": "zero_cost_budget",
                "status": "PASS",
                "receipt_hash": budget.content_hash,
            },
            {
                "gate_key": "verified_destination",
                "status": "PASS",
                "receipt_hash": resolved.verified_destination.content_hash,
            },
        ]
        if script_qualification_run_id is not None:
            from app.services.script_qualification import ScriptQualificationService

            receipt = ScriptQualificationService(self.session).require_pass(
                script_qualification_run_id
            )
            receipts.append(
                {
                    "gate_key": "script_qualification",
                    "status": "PASS",
                    "receipt_hash": receipt.content_hash,
                    "script_qualification_run_id": str(script_qualification_run_id),
                    "script_hash": receipt.script_hash,
                    "assignment_hash": receipt.script_assignment_hash,
                    "evidence_pack_hash": receipt.factual_evidence_pack_hash,
                    "memory_digest_hash": str(
                        (qualification_memory or {}).get("digest_hash") or ""
                    ),
                    "runtime_contract_hash": str(
                        ((receipt.content or {}).get("runtime_contract_hash") or "")
                    ),
                    "assignment_resolution_hash": str(
                        (
                            (receipt.content or {}).get("assignment_resolution_hash")
                            or ""
                        )
                    ),
                    "research_coverage_ratio": float(
                        ((receipt.content or {}).get("receipts") or {})
                        .get("fulfillment", {})
                        .get("research_coverage_ratio", 0.0)
                    ),
                }
            )
        return receipts

    def _seal(
        self,
        *,
        command: V2SupportAuthorityPrepareCommand,
        envelope: V2FrozenSupportEnvelope,
    ) -> tuple[Artifact, ArtifactVersion]:
        service = ArtifactService(self.session)
        correlation_id = f"v2-support-authority-{envelope.input_fingerprint[:20]}"
        artifact = service.create_artifact(
            data=ArtifactCreate(
                video_project_id=command.video_project_id,
                artifact_type=V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
                status="approved",
                created_by_user_id=command.actor_user_id,
            ),
            correlation_id=correlation_id,
            trusted_authority_write=True,
        )
        source_refs = [
            {
                "type": source.type,
                "id": str(source.id),
                "ref": source.ref,
                "content_hash": source.content_hash,
            }
            for source in envelope.frozen_sources
        ]
        version = service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=envelope.model_dump(mode="json"),
                status="approved",
                created_by_user_id=command.actor_user_id,
                external_entity_refs=source_refs,
                packaging_metadata={
                    "authority_version": V2_SUPPORT_AUTHORITY_VERSION,
                    "input_fingerprint": envelope.input_fingerprint,
                    "idempotency_hash": envelope.idempotency_hash,
                },
                source_manifest={
                    "schema_version": "vcos.v2-support-source-manifest.v1",
                    "sources": source_refs,
                },
                evidence_refs=[
                    {
                        "claim_id": binding.claim_id,
                        "binding_hash": binding.binding_hash,
                    }
                    for binding in envelope.claim_source_bindings
                ],
                context_refs=[
                    envelope.profile_ref.model_dump(mode="json"),
                    envelope.compiled_policy_ref.model_dump(mode="json"),
                    envelope.effective_context_ref.model_dump(mode="json"),
                ],
                claim_refs=[
                    {
                        "claim_id": binding.claim_id,
                        "claim_text_hash": semantic_hash(
                            {"claim_text": binding.claim_text}
                        ),
                        "binding_hash": binding.binding_hash,
                    }
                    for binding in envelope.claim_source_bindings
                ],
            ),
            correlation_id=correlation_id,
            trusted_authority_write=True,
        )
        return artifact, version

    @staticmethod
    def _cross_modal_script_lineage(
        qualification_run: Any,
    ) -> V2CrossModalScriptLineage:
        """Carry identities and section source, never full skill procedures."""

        assignment = (
            qualification_run.script_assignment
            if isinstance(qualification_run.script_assignment, dict)
            else {}
        )
        payload = (
            qualification_run.script_payload
            if isinstance(qualification_run.script_payload, dict)
            else {}
        )
        raw_plan = assignment.get("section_coverage_plan")
        receipts = assignment.get("capability_projection_receipts")
        sections = payload.get("sections")
        if (
            qualification_run.script_contract_version != "V2_SINGLE_SOURCE"
            or not isinstance(raw_plan, dict)
            or not isinstance(receipts, dict)
            or not isinstance(sections, list)
        ):
            raise ValidationFailureError("V2_CROSS_MODAL_SCRIPT_LINEAGE_REQUIRED")
        try:
            plan = SectionCoveragePlan.model_validate(raw_plan)
        except ValueError as exc:
            raise ValidationFailureError(
                "V2_CROSS_MODAL_SECTION_COVERAGE_INVALID"
            ) from exc
        if plan.content_hash != assignment.get("section_coverage_plan_hash"):
            raise ValidationFailureError(
                "V2_CROSS_MODAL_SECTION_COVERAGE_HASH_MISMATCH"
            )
        body = {
            "qualified_script_hash": str(
                qualification_run.derived_canonical_script_hash or ""
            ),
            "section_coverage_plan": plan.model_dump(mode="json"),
            "writer_sections": sections,
            "capability_projection_receipts": receipts,
        }
        if not re.fullmatch(_SHA256_PATTERN, body["qualified_script_hash"]):
            raise ValidationFailureError("V2_CROSS_MODAL_QUALIFIED_SCRIPT_HASH_INVALID")
        return V2CrossModalScriptLineage(**body, content_hash=cross_modal_hash(body))

    def _existing_artifact(self, video_project_id: uuid.UUID) -> Artifact | None:
        artifacts = list(
            self.session.scalars(
                select(Artifact)
                .where(
                    Artifact.video_project_id == video_project_id,
                    Artifact.artifact_type == V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
                )
                .order_by(Artifact.created_at, Artifact.id)
            ).all()
        )
        if len(artifacts) > 1:
            raise ValidationFailureError("V2_SUPPORT_ENVELOPE_INTEGRITY_MISMATCH")
        return artifacts[0] if artifacts else None

    def _replay(
        self,
        *,
        artifact: Artifact,
        expected_idempotency_hash: str,
        expected_input_fingerprint: str,
        expected_routes: list[V2NativeRouteReceipt],
        expected_execution_mode: Literal[
            "QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"
        ],
        requested_ceiling_usd: Decimal,
        reservation_run_id: uuid.UUID | None,
    ) -> V2SupportAuthorityResult:
        version = (
            self.session.get(ArtifactVersion, artifact.current_version_id)
            if artifact.current_version_id is not None
            else None
        )
        domain = (
            (version.packaging_metadata or {}).get("_vcos_domain_authority")
            if version is not None
            else None
        )
        try:
            envelope = (
                V2FrozenSupportEnvelope.model_validate(version.content)
                if version is not None
                else None
            )
        except ValidationError as exc:
            raise ValidationFailureError(
                "V2_SUPPORT_ENVELOPE_INTEGRITY_MISMATCH"
            ) from exc
        if (
            version is None
            or envelope is None
            or artifact.status != "approved"
            or version.status != "approved"
            or version.artifact_id != artifact.id
            or content_hash(version.content) != version.content_hash
            or not isinstance(domain, dict)
            or domain.get("writer") != "server_domain_service"
            or domain.get("artifact_type") != V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE
            or domain.get("content_hash") != version.content_hash
            or version.packaging_metadata.get("idempotency_hash")
            != envelope.idempotency_hash
        ):
            raise ValidationFailureError("V2_SUPPORT_ENVELOPE_INTEGRITY_MISMATCH")
        if (
            envelope.idempotency_hash != expected_idempotency_hash
            or envelope.input_fingerprint != expected_input_fingerprint
            or envelope.execution_mode != expected_execution_mode
            or [route.route_hash for route in envelope.native_routes]
            != [route.route_hash for route in expected_routes]
            or envelope.zero_cost_budget.requested_ceiling_usd != requested_ceiling_usd
        ):
            raise ValidationFailureError("V2_SUPPORT_ENVELOPE_IMMUTABLE_DRIFT")
        budget = envelope.zero_cost_budget
        if expected_execution_mode == "REAL_LONG_FORM_PRODUCTION":
            evidence = budget.reservation_evidence or {}
            if (
                reservation_run_id is None
                or budget.reservation_ref != f"mr1-budget://{reservation_run_id}"
                or evidence.get("run_id") != str(reservation_run_id)
                or evidence.get("reservation_ref") != budget.reservation_ref
                or evidence.get("status") != "RESERVED"
            ):
                raise ValidationFailureError("V2_SUPPORT_ENVELOPE_IMMUTABLE_DRIFT")
        elif (
            budget.schema_version != "vcos.zero-cost-route-budget.v1"
            or budget.authorized_cost_usd != 0
            or budget.reservation_ref is not None
        ):
            raise ValidationFailureError("V2_SUPPORT_ENVELOPE_IMMUTABLE_DRIFT")
        return _result(
            artifact=artifact,
            version=version,
            envelope=envelope,
            replayed=True,
            reason_code="V2_SUPPORT_ENVELOPE_REPLAYED",
        )


def _source_ref(
    *,
    source_type: str,
    source_kind: str,
    source_id: uuid.UUID,
    payload: dict[str, Any],
    fact_statements: Iterable[str | None],
) -> V2FrozenSourceRef:
    statements = [
        str(statement).strip()
        for statement in fact_statements
        if isinstance(statement, str) and statement.strip()
    ]
    if not statements:
        raise ValidationFailureError("V2_SUPPORT_TYPED_SOURCE_MISMATCH")
    return V2FrozenSourceRef(
        type=source_type,
        source_kind=source_kind,
        id=source_id,
        ref=f"{source_type.replace('_', '-')}://{source_id}",
        content_hash=semantic_hash(payload),
        fact_statements=list(dict.fromkeys(statements)),
    )


def _editorial_slot_source(
    slot: EditorialCalendarSlot,
) -> V2FrozenSourceRef:
    payload = {
        "schema_version": slot.schema_version,
        "id": str(slot.id),
        "company_id": str(slot.company_id),
        "channel_workspace_id": str(slot.channel_workspace_id),
        "policy_snapshot_id": str(slot.policy_snapshot_id),
        "category_id": str(slot.category_id) if slot.category_id else None,
        "slot_date": slot.slot_date.isoformat(),
        "slot_type": slot.slot_type,
        "status": slot.status,
        "production_lane": slot.production_lane,
        "assignment_mode": slot.assignment_mode,
        "preferred_series_plan_id": (
            str(slot.preferred_series_plan_id)
            if slot.preferred_series_plan_id
            else None
        ),
        "preferred_series_run_id": (
            str(slot.preferred_series_run_id) if slot.preferred_series_run_id else None
        ),
        "production_goal": slot.production_goal,
        "target_platforms": slot.target_platforms,
        "content_pillar": slot.content_pillar,
        "format_hint": slot.format_hint,
        "risk_level": slot.risk_level,
        "operational_envelope": slot.operational_envelope,
    }
    return _source_ref(
        source_type="editorial_calendar_slot",
        source_kind="FROZEN_EDITORIAL_SLOT",
        source_id=slot.id,
        payload=payload,
        fact_statements=[
            (
                f"Production goal: {slot.production_goal}"
                if slot.production_goal
                else None
            ),
            (f"Content pillar: {slot.content_pillar}" if slot.content_pillar else None),
            (f"Format hint: {slot.format_hint}" if slot.format_hint else None),
            f"Production lane: {slot.production_lane}",
            f"Target platforms: {', '.join(slot.target_platforms or [])}",
        ],
    )


def _preflight_source(
    preflight: IdeaMarketPreflight,
) -> V2FrozenSourceRef:
    payload = {
        "id": str(preflight.id),
        "company_id": str(preflight.company_id),
        "channel_workspace_id": str(preflight.channel_workspace_id),
        "editorial_calendar_slot_id": (
            str(preflight.editorial_calendar_slot_id)
            if preflight.editorial_calendar_slot_id
            else None
        ),
        "search_intent_map_id": (
            str(preflight.search_intent_map_id)
            if preflight.search_intent_map_id
            else None
        ),
        "audience_target_pack_id": (
            str(preflight.audience_target_pack_id)
            if preflight.audience_target_pack_id
            else None
        ),
        "demand_score": (
            _decimal_string(preflight.demand_score)
            if preflight.demand_score is not None
            else None
        ),
        "channel_fit_score": (
            _decimal_string(preflight.channel_fit_score)
            if preflight.channel_fit_score is not None
            else None
        ),
        "policy_fit_state": preflight.policy_fit_state,
        "confidence_state": preflight.confidence_state,
        # The blob is hash-bound for drift detection but is never exposed as
        # producer facts, so embedded caller prose cannot become a script.
        "evidence_blob": preflight.evidence_blob,
        "reason_codes": preflight.reason_codes,
        "decision": preflight.decision,
    }
    return _source_ref(
        source_type="idea_market_preflight",
        source_kind="TYPED_MARKET_PREFLIGHT",
        source_id=preflight.id,
        payload=payload,
        fact_statements=[
            f"Market preflight decision: {preflight.decision}",
            f"Policy fit state: {preflight.policy_fit_state}",
            f"Preflight confidence: {preflight.confidence_state}",
            (
                f"Demand score: {_decimal_string(preflight.demand_score)}"
                if preflight.demand_score is not None
                else None
            ),
            (
                f"Channel fit score: {_decimal_string(preflight.channel_fit_score)}"
                if preflight.channel_fit_score is not None
                else None
            ),
        ],
    )


def _search_intent_source(search: SearchIntentMap) -> V2FrozenSourceRef:
    payload = {
        "id": str(search.id),
        "company_id": str(search.company_id),
        "channel_workspace_id": str(search.channel_workspace_id),
        "primary_search_intent": search.primary_search_intent,
        "secondary_search_intents": search.secondary_search_intents,
        "keyword_cluster": search.keyword_cluster,
        "audience_problem": search.audience_problem,
        "audience_language": search.audience_language,
        "target_geo": search.target_geo,
        "source_evidence_refs": search.source_evidence_refs,
        "demand_confidence": search.demand_confidence,
        "competition_notes": search.competition_notes,
        "content_gap_notes": search.content_gap_notes,
    }
    return _source_ref(
        source_type="search_intent_map",
        source_kind="TYPED_SEARCH_INTENT",
        source_id=search.id,
        payload=payload,
        fact_statements=[
            f"Primary search intent: {search.primary_search_intent}",
            (
                f"Secondary search intents: {', '.join(search.secondary_search_intents or [])}"
                if search.secondary_search_intents
                else None
            ),
            (
                f"Keyword cluster: {', '.join(search.keyword_cluster or [])}"
                if search.keyword_cluster
                else None
            ),
            (
                f"Audience problem: {search.audience_problem}"
                if search.audience_problem
                else None
            ),
            f"Search demand confidence: {search.demand_confidence}",
        ],
    )


def _audience_source(audience: AudienceTargetPack) -> V2FrozenSourceRef:
    payload = {
        "id": str(audience.id),
        "company_id": str(audience.company_id),
        "channel_workspace_id": str(audience.channel_workspace_id),
        "target_audience": audience.target_audience,
        "audience_problem": audience.audience_problem,
        "audience_language": audience.audience_language,
        "target_geo": audience.target_geo,
        "platform_surface_hypothesis": audience.platform_surface_hypothesis,
        "audience_rationale": audience.audience_rationale,
        "evidence_refs": audience.evidence_refs,
        "confidence_level": audience.confidence_level,
    }
    return _source_ref(
        source_type="audience_target_pack",
        source_kind="TYPED_AUDIENCE_TARGET",
        source_id=audience.id,
        payload=payload,
        fact_statements=[
            f"Target audience: {audience.target_audience}",
            f"Audience problem: {audience.audience_problem}",
            (
                f"Audience language: {audience.audience_language}"
                if audience.audience_language
                else None
            ),
            (
                f"Target geography: {audience.target_geo}"
                if audience.target_geo
                else None
            ),
            f"Audience confidence: {audience.confidence_level}",
        ],
    )


def _verified_destination(
    channel: ChannelWorkspace,
) -> V2VerifiedDestinationAuthority:
    metadata = channel.metadata_ if isinstance(channel.metadata_, dict) else {}
    governance = metadata.get("destination_governance")
    if not isinstance(governance, dict):
        raise ValidationFailureError("V2_SUPPORT_DESTINATION_NOT_VERIFIED")
    active_ref = str(governance.get("active_binding_ref") or "")
    bindings = governance.get("bindings")
    active = (
        next(
            (
                item
                for item in bindings
                if isinstance(item, dict)
                and active_ref
                == (
                    f"destination-binding://{channel.key}/"
                    f"v{item.get('binding_version')}"
                )
            ),
            None,
        )
        if isinstance(bindings, list)
        else None
    )
    try:
        binding = (
            DestinationBinding.model_validate(active)
            if isinstance(active, dict)
            else None
        )
    except ValidationError as exc:
        raise ValidationFailureError("V2_SUPPORT_DESTINATION_NOT_VERIFIED") from exc
    expected_ref = (
        f"destination-binding://{channel.key}/v{binding.binding_version}"
        if binding is not None
        else ""
    )
    if (
        binding is None
        or active_ref != expected_ref
        or binding.channel_id != channel.id
        or binding.channel_key != channel.key
        or binding.destination_status != "VERIFIED"
        or binding.verification_state != "VERIFIED"
        or not binding.platform_account_ref
        or not binding.platform_channel_id
        or not binding.credential_ref
        or not binding.content_hash
        or active.get("content_hash") != binding.content_hash
    ):
        raise ValidationFailureError("V2_SUPPORT_DESTINATION_NOT_VERIFIED")
    payload = {
        "active_binding_ref": active_ref,
        "binding": binding.model_dump(mode="json"),
        "destination_hash": binding.content_hash,
    }
    return V2VerifiedDestinationAuthority(
        **payload,
        content_hash=semantic_hash(payload),
    )


def _project_authority_hash(project: VideoProject) -> str:
    return semantic_hash(
        {
            "id": str(project.id),
            "company_id": str(project.company_id),
            "channel_workspace_id": str(project.channel_workspace_id),
            "policy_snapshot_id": str(project.policy_snapshot_id),
            "channel_profile_version_id": (
                str(project.channel_profile_version_id)
                if project.channel_profile_version_id
                else None
            ),
            "effective_context_snapshot_id": (
                str(project.effective_context_snapshot_id)
                if project.effective_context_snapshot_id
                else None
            ),
            "schema_version": project.schema_version,
            "planning_source_type": project.planning_source_type,
            "production_lane": project.production_lane,
            "content_mode": project.content_mode,
            "assignment_mode": project.assignment_mode,
            "series_plan_id": (
                str(project.series_plan_id) if project.series_plan_id else None
            ),
            "series_run_id": (
                str(project.series_run_id) if project.series_run_id else None
            ),
            "episode_number": project.episode_number,
            "standalone_reason_code": project.standalone_reason_code,
            "project_admission_decision_id": (
                str(project.project_admission_decision_id)
                if project.project_admission_decision_id
                else None
            ),
            "duration_contract": project.duration_contract,
            "render_eligible": project.render_eligible,
        }
    )


def _effective_context_hash(
    snapshot: EffectiveChannelRuntimeContextSnapshot,
) -> str:
    subcontexts = {
        field: getattr(snapshot, field) or {} for field in _EFFECTIVE_SUBCONTEXT_FIELDS
    }
    return content_hash(
        {
            "schema_version": "r3d2.effective_channel_runtime_context.v1",
            "compile_status": snapshot.compile_status,
            "reason_codes": snapshot.reason_codes_json,
            "source_refs": snapshot.source_refs_json,
            "subcontexts": subcontexts,
        }
    )


def _build_visual_rights(
    *,
    policy_snapshot: CompiledChannelPolicySnapshot | None,
    execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"],
) -> V2LocalGeneratedCardRights:
    """Freeze only policy-selected visual request authority.

    A real long-form envelope must carry the CH1 visual binding.  We do not
    turn an absent binding into an invented local-card permission.  Existing
    qualification envelopes retain their historical local-only shape for
    backward readability.
    """
    try:
        scoped = ChannelScopedPolicy.model_validate(
            (policy_snapshot.compiled_payload or {}).get("channel_scoped_policy")
            if policy_snapshot is not None
            else None
        )
    except ValidationError as exc:
        if execution_mode == "REAL_LONG_FORM_PRODUCTION":
            raise ValidationFailureError("V2_REAL_VISUAL_POLICY_INVALID") from exc
        return _build_local_card_rights()

    visual = scoped.visual_source_policy_binding
    if visual is None:
        if execution_mode == "REAL_LONG_FORM_PRODUCTION":
            payload = {
                "schema_version": "vcos.visual-asset-request-authority.v2",
                "rights_state": "PASS",
                "visual_source_mode": "NATIVE_BACKBONE_POLICY_ONLY",
                "external_asset_refs": [],
                "stock_asset_refs": [],
                "license_evidence_required": False,
                "synthetic_media_disclosure_required": False,
                "policy_refs": [
                    scoped.approval_ref,
                    scoped.format_identity_contract.ref,
                ],
                "allowed_provider_keys": ["native_ffmpeg_renderer"],
                "one_source_decision_per_scene": True,
                "provider_fallback_allowed": False,
                "asset_request_compiler_required": False,
                "post_readiness_acquisition_required": False,
            }
            return V2LocalGeneratedCardRights(
                **payload,
                content_hash=semantic_hash(payload),
            )
        return _build_local_card_rights()

    providers = ["native_ffmpeg_renderer"]
    if scoped.provider_usage_policy.pexels.enabled:
        providers.append("pexels_api")
    if scoped.provider_usage_policy.google_veo.enabled:
        providers.append("google_veo")
    if scoped.provider_usage_policy.google_gemini_image is not None:
        providers.append("google_gemini_image")
    policy_refs = [
        visual.visual_source_routing_policy.ref,
        visual.visual_source_routing_catalog.ref,
        visual.gemini_image_provider_registry.ref,
        visual.gemini_image_model_catalog.ref,
        visual.image_visual_quality_control.ref,
        visual.image_canary_v3_qualification.ref,
        visual.drive_verified_canary_receipt.ref,
    ]
    payload = {
        "schema_version": "vcos.visual-asset-request-authority.v2",
        "rights_state": "PASS",
        "visual_source_mode": "POLICY_SELECTED_ASSET_REQUESTS",
        "external_asset_refs": [],
        "stock_asset_refs": [],
        "license_evidence_required": False,
        "synthetic_media_disclosure_required": False,
        "policy_refs": policy_refs,
        "allowed_provider_keys": providers,
        "one_source_decision_per_scene": True,
        "provider_fallback_allowed": False,
        "asset_request_compiler_required": True,
        "post_readiness_acquisition_required": True,
    }
    return V2LocalGeneratedCardRights(
        **payload,
        content_hash=semantic_hash(payload),
    )


def _build_local_card_rights() -> V2LocalGeneratedCardRights:
    payload = {
        "schema_version": "vcos.local-generated-card-rights.v1",
        "rights_state": "PASS",
        "visual_source_mode": "LOCAL_GENERATED_CARDS_ONLY",
        "external_asset_refs": [],
        "stock_asset_refs": [],
        "license_evidence_required": False,
        "synthetic_media_disclosure_required": False,
    }
    return V2LocalGeneratedCardRights(
        **payload,
        content_hash=semantic_hash(payload),
    )


def _route_receipt(
    *,
    stage: str,
    project_id: uuid.UUID,
    input_fingerprint: str,
    role: MediaProviderRoleProfile,
    capability: ProviderCapabilityMatrixEntry | None,
    job_type: str | None,
    routing_policy_ref: str,
    routing_policy_hash: str,
    adapter_key: str | None = None,
    paid_provider_call: bool = False,
    max_cost_usd: Decimal = Decimal("0"),
) -> V2NativeRouteReceipt:
    adapter_key = adapter_key or _V2_ADAPTER_BY_STAGE[stage]
    payload = {
        "stage": stage,
        "operation_id": (
            f"{adapter_key}:{project_id}:{stage.lower()}:{input_fingerprint[:20]}"
        ),
        "adapter_key": adapter_key,
        "provider_role_id": str(role.id),
        "provider_key": role.provider_key,
        "provider_type": role.provider_type,
        "routing_decision_id": None,
        "routing_policy_ref": routing_policy_ref,
        "routing_policy_hash": routing_policy_hash,
        "capability_entry_id": str(capability.id) if capability else None,
        "job_type": job_type,
        "paid_provider_call": paid_provider_call,
        "max_cost_usd": _decimal_string(max_cost_usd),
    }
    return V2NativeRouteReceipt(
        **payload,
        route_hash=semantic_hash(payload),
    )


def _build_zero_cost_budget(
    *,
    session: Session,
    routes: list[V2NativeRouteReceipt],
    requested_ceiling_usd: Decimal,
    execution_mode: Literal["QUALIFICATION_LOCAL", "REAL_LONG_FORM_PRODUCTION"],
    policy_snapshot: CompiledChannelPolicySnapshot | None,
    project_id: uuid.UUID,
    reservation_run_id: uuid.UUID | None,
) -> V2ZeroCostBudgetAuthority:
    if execution_mode == "REAL_LONG_FORM_PRODUCTION":
        try:
            scoped = ChannelScopedPolicy.model_validate(
                (policy_snapshot.compiled_payload or {}).get("channel_scoped_policy")
                if policy_snapshot is not None
                else None
            )
        except ValidationError as exc:
            raise ValidationFailureError(
                "V2_REAL_PROVIDER_BUDGET_POLICY_INVALID"
            ) from exc
        per_video = Decimal(str(scoped.budget_policy.max_estimated_cost_per_video))
        monthly = Decimal(str(scoped.budget_policy.monthly_channel_budget))
        if (
            requested_ceiling_usd < per_video
            or per_video <= 0
            or reservation_run_id is None
        ):
            raise ValidationFailureError("V2_REAL_PROVIDER_BUDGET_RESERVATION_BLOCKED")
        settings = get_settings()
        environment_cap = Decimal(str(settings.monthly_ai_budget_usd or 0))
        elevenlabs_cap = Decimal(str(settings.elevenlabs_monthly_cap_usd or 0))
        if environment_cap <= 0 or elevenlabs_cap <= 0:
            raise ValidationFailureError("V2_REAL_PROVIDER_BUDGET_CAP_REQUIRED")
        evidence = MR1MonthlyBudgetAuthority(session).reserve_run(
            run_id=reservation_run_id,
            project_id=project_id,
            reservation_amount_usd=per_video,
            environment_cap_usd=environment_cap,
            company_cap_usd=environment_cap,
            channel_cap_usd=monthly,
            provider_allocations_usd={
                "elevenlabs": per_video,
                "google_drive": Decimal("0"),
            },
            provider_caps_usd={
                "elevenlabs": elevenlabs_cap,
                "google_drive": environment_cap,
            },
            provider_aliases={
                "elevenlabs": ["elevenlabs", "forced_alignment"],
                "google_drive": ["google_drive"],
            },
        )
        if (
            evidence.get("run_id") != str(reservation_run_id)
            or evidence.get("project_id") != str(project_id)
            or evidence.get("status") != "RESERVED"
            or Decimal(str(evidence.get("reserved_amount_usd"))) != per_video
        ):
            raise ValidationFailureError("V2_REAL_PROVIDER_BUDGET_RESERVATION_INVALID")
        payload = {
            "schema_version": "vcos.real-provider-route-budget.v1",
            "policy_mode": "REAL_PROVIDER_PER_VIDEO_RESERVATION",
            "execution_mode": execution_mode,
            "requested_ceiling_usd": _decimal_string(requested_ceiling_usd),
            "authorized_cost_usd": _decimal_string(per_video),
            "paid_provider_calls_allowed": True,
            "monthly_budget_usd": _decimal_string(monthly),
            "monthly_used_usd": str(
                (evidence.get("capacity_evidence") or {})
                .get("before_reservation", {})
                .get("channel_occupied_usd", "0")
            ),
            "monthly_reserved_usd": evidence["reserved_amount_usd"],
            "reservation_ref": evidence["reservation_ref"],
            "reservation_evidence": evidence,
            "operation_ids": sorted(route.operation_id for route in routes),
            "route_hashes": sorted(route.route_hash for route in routes),
        }
        return V2ZeroCostBudgetAuthority(
            **payload,
            content_hash=semantic_hash(payload),
        )
    payload = {
        "schema_version": "vcos.zero-cost-route-budget.v1",
        "policy_mode": "LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER",
        "execution_mode": execution_mode,
        "requested_ceiling_usd": _decimal_string(requested_ceiling_usd),
        "authorized_cost_usd": "0",
        "paid_provider_calls_allowed": False,
        "monthly_budget_usd": None,
        "monthly_used_usd": None,
        "monthly_reserved_usd": None,
        "reservation_ref": None,
        "reservation_evidence": None,
        "operation_ids": sorted(route.operation_id for route in routes),
        "route_hashes": sorted(route.route_hash for route in routes),
    }
    return V2ZeroCostBudgetAuthority(
        **payload,
        content_hash=semantic_hash(payload),
    )


def _result(
    *,
    artifact: Artifact,
    version: ArtifactVersion,
    envelope: V2FrozenSupportEnvelope,
    replayed: bool,
    reason_code: str,
) -> V2SupportAuthorityResult:
    return V2SupportAuthorityResult(
        artifact_id=artifact.id,
        artifact_version_id=version.id,
        envelope_hash=version.content_hash,
        replayed=replayed,
        approved_script_hash=envelope.approved_script.script_hash,
        approved_script_word_count=envelope.approved_script.word_count,
        exact_source_refs=envelope.frozen_sources,
        reason_codes=[reason_code],
    )


def _valid_sha256(value: str | None) -> bool:
    return bool(value and re.fullmatch(_SHA256_PATTERN, value))


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _sentences(value: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", value.strip())
        if sentence.strip()
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip() for item in value if isinstance(item, str) and item.strip()
    ]


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")
