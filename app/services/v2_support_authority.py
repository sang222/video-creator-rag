"""Trusted, ID-only preparation boundary for frozen v2 production support.

Public callers may select an already-frozen planning source, but they may not
submit scripts, research, evidence, provider plans, rights claims, or
destination bindings.  This service resolves those authorities from server
records, asks the guarded LLM router for a typed script draft, validates it,
and seals one immutable domain-only envelope.
"""

from __future__ import annotations

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

from app.contracts.geo_market import DestinationBinding
from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.production_package import ProductionDurationContractV2
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
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
from app.services.production_package import semantic_hash
from app.services.workflow import ArtifactService


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


class V2ExactAuthorityRef(_StrictFrozenModel):
    type: str = Field(min_length=1)
    id: uuid.UUID
    ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)


class V2FrozenSourceRef(V2ExactAuthorityRef):
    source_kind: str = Field(min_length=1)
    fact_statements: list[str] = Field(min_length=1)


class V2GeneratedCitation(_StrictFrozenModel):
    source_ref_id: uuid.UUID
    source_excerpt: str = Field(min_length=8, max_length=1_000)


class V2GeneratedClaim(_StrictFrozenModel):
    claim_id: str = Field(min_length=1, max_length=120)
    claim_text: str = Field(min_length=3, max_length=2_000)
    citations: list[V2GeneratedCitation] = Field(min_length=1)


class V2GeneratedSection(_StrictFrozenModel):
    section_id: str = Field(min_length=1, max_length=120)
    heading: str = Field(min_length=1, max_length=240)
    narration: str = Field(min_length=1)


class V2ProducerReceipt(_StrictFrozenModel):
    producer_type: Literal["LLM_ROUTER"]
    producer_version: str = Field(min_length=1)
    lane_name: str = Field(min_length=1)
    selected_model: str = Field(min_length=1)
    fallback_level: str = Field(min_length=1)
    route_attempt_id: uuid.UUID
    provider_attempt_id: uuid.UUID | None = None
    llm_run_snapshot_id: uuid.UUID | None = None
    producer_input_hash: str = Field(pattern=_SHA256_PATTERN)
    producer_output_hash: str = Field(pattern=_SHA256_PATTERN)


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
                            "external assets, URLs, provider calls, or facts."
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


class V2ClaimSourceBinding(_StrictFrozenModel):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=3)
    source_refs: list[V2ExactAuthorityRef] = Field(min_length=1)
    source_excerpts: list[str] = Field(min_length=1)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = semantic_hash(self.model_dump(mode="json", exclude={"binding_hash"}))
        if self.binding_hash != expected:
            raise ValueError("CLAIM_SOURCE_BINDING_HASH_MISMATCH")
        return self


class V2LocalGeneratedCardRights(_StrictFrozenModel):
    schema_version: Literal["vcos.local-generated-card-rights.v1"] = (
        "vcos.local-generated-card-rights.v1"
    )
    rights_state: Literal["PASS"] = "PASS"
    visual_source_mode: Literal["LOCAL_GENERATED_CARDS_ONLY"] = (
        "LOCAL_GENERATED_CARDS_ONLY"
    )
    external_asset_refs: list[Any] = Field(default_factory=list, max_length=0)
    stock_asset_refs: list[Any] = Field(default_factory=list, max_length=0)
    license_evidence_required: Literal[False] = False
    synthetic_media_disclosure_required: Literal[False] = False
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        expected = semantic_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("LOCAL_CARD_RIGHTS_HASH_MISMATCH")
        return self


class V2NativeRouteReceipt(_StrictFrozenModel):
    stage: Literal["MEDIA", "RENDER", "QC", "ARCHIVE"]
    operation_id: str = Field(min_length=1, max_length=200)
    adapter_key: Literal["v2-local-native", "v2-google-drive-archive"]
    provider_role_id: uuid.UUID
    provider_key: str = Field(min_length=1)
    provider_type: str = Field(min_length=1)
    routing_decision_id: uuid.UUID | None = None
    routing_policy_ref: str = Field(min_length=1)
    routing_policy_hash: str = Field(pattern=_SHA256_PATTERN)
    capability_entry_id: uuid.UUID | None = None
    job_type: str | None = None
    paid_provider_call: Literal[False] = False
    max_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=0)
    route_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        if self.adapter_key != _V2_ADAPTER_BY_STAGE[self.stage]:
            raise ValueError("V2_NATIVE_ROUTE_ADAPTER_STAGE_MISMATCH")
        expected = semantic_hash(self.model_dump(mode="json", exclude={"route_hash"}))
        if self.route_hash != expected:
            raise ValueError("NATIVE_ROUTE_HASH_MISMATCH")
        return self


class V2ZeroCostBudgetAuthority(_StrictFrozenModel):
    schema_version: Literal["vcos.zero-cost-route-budget.v1"] = (
        "vcos.zero-cost-route-budget.v1"
    )
    policy_mode: Literal["LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER"] = (
        "LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER"
    )
    requested_ceiling_usd: Decimal = Field(ge=0, le=250)
    authorized_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=0)
    paid_provider_calls_allowed: Literal[False] = False
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
            raise ValueError("ZERO_COST_BUDGET_HASH_MISMATCH")
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
    duration_contract: ProductionDurationContractV2
    frozen_sources: list[V2FrozenSourceRef] = Field(min_length=2)
    approved_script: V2ApprovedScriptProvenance
    claim_source_bindings: list[V2ClaimSourceBinding] = Field(min_length=3)
    local_generated_card_rights: V2LocalGeneratedCardRights
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
        ):
            raise ValueError("BUDGET_ROUTE_BINDING_INVALID")
        if (
            self.approved_script.estimated_duration_ms
            < self.duration_contract.minimum_duration_ms
            or self.approved_script.estimated_duration_ms
            > self.duration_contract.maximum_duration_ms
        ):
            raise ValueError("APPROVED_SCRIPT_DURATION_INVALID")
        source_ids = {source.id for source in self.frozen_sources}
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
            if not {source.id for source in binding.source_refs} <= source_ids:
                raise ValueError("CLAIM_SOURCE_BINDING_INVALID")
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
        # Resolve only idempotent catalog/role/capability authorities before
        # replay.  No MediaRenderRoutingDecision or provider effect is created.
        routes = self._create_native_routes(
            project=project,
            input_fingerprint=resolved.input_fingerprint,
            duration=resolved.duration_contract,
        )
        budget = _build_zero_cost_budget(
            routes=routes,
            requested_ceiling_usd=command.max_budget_usd,
        )
        existing = self._existing_artifact(project.id)
        if existing is not None:
            return self._replay(
                artifact=existing,
                expected_idempotency_hash=resolved.idempotency_hash,
                expected_input_fingerprint=resolved.input_fingerprint,
                expected_routes=routes,
                expected_budget=budget,
            )

        production_context = V2SupportProductionContext(
            video_project_id=project.id,
            production_lane=resolved.production_lane,
            title=resolved.title,
            expected_language=resolved.expected_language,
            duration_contract=resolved.duration_contract,
            frozen_sources=resolved.frozen_sources,
            forbidden_claims=resolved.forbidden_claims,
            forbidden_style_terms=resolved.forbidden_style_terms,
        )
        draft = (producer or self.producer).produce(production_context)
        validated = self._validate_draft(
            draft=draft,
            context=production_context,
        )
        rights = _build_local_card_rights()
        gate_receipts = self._gate_receipts(
            resolved=resolved,
            validated=validated,
            rights=rights,
            routes=routes,
            budget=budget,
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
            duration_contract=resolved.duration_contract,
            frozen_sources=resolved.frozen_sources,
            approved_script=validated["script"],
            claim_source_bindings=validated["claim_bindings"],
            local_generated_card_rights=rights,
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
    ) -> dict[str, Any]:
        if draft.producer_receipt.producer_input_hash != semantic_hash(
            context.model_dump(mode="json")
        ):
            raise ValidationFailureError("V2_SUPPORT_PRODUCER_INPUT_HASH_MISMATCH")
        output_payload = {
            "approved_script_text": draft.approved_script_text,
            "language": draft.language,
            "sections": [item.model_dump(mode="json") for item in draft.sections],
            "claims": [item.model_dump(mode="json") for item in draft.claims],
        }
        if draft.producer_receipt.producer_output_hash != semantic_hash(output_payload):
            raise ValidationFailureError("V2_SUPPORT_PRODUCER_OUTPUT_HASH_MISMATCH")
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

        source_by_id = {source.id: source for source in context.frozen_sources}
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
            seen_source_ids: set[uuid.UUID] = set()
            for citation in claim.citations:
                source = source_by_id.get(citation.source_ref_id)
                if (
                    source is None
                    or citation.source_excerpt not in source.fact_statements
                ):
                    raise ValidationFailureError(
                        "V2_SUPPORT_CLAIM_SOURCE_BINDING_INVALID"
                    )
                if source.id not in seen_source_ids:
                    exact_refs.append(
                        V2ExactAuthorityRef(
                            type=source.type,
                            id=source.id,
                            ref=source.ref,
                            content_hash=source.content_hash,
                        )
                    )
                    seen_source_ids.add(source.id)
                excerpts.append(citation.source_excerpt)
            binding_payload = {
                "claim_id": claim.claim_id,
                "claim_text": claim.claim_text,
                "source_refs": [item.model_dump(mode="json") for item in exact_refs],
                "source_excerpts": excerpts,
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
    ) -> list[V2NativeRouteReceipt]:
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

    def _gate_receipts(
        self,
        *,
        resolved: _ResolvedSupportAuthority,
        validated: dict[str, Any],
        rights: V2LocalGeneratedCardRights,
        routes: list[V2NativeRouteReceipt],
        budget: V2ZeroCostBudgetAuthority,
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
        expected_budget: V2ZeroCostBudgetAuthority,
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
            or [route.route_hash for route in envelope.native_routes]
            != [route.route_hash for route in expected_routes]
            or envelope.zero_cost_budget.content_hash != expected_budget.content_hash
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
            str(preflight.demand_score) if preflight.demand_score is not None else None
        ),
        "channel_fit_score": (
            str(preflight.channel_fit_score)
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
                f"Demand score: {preflight.demand_score}"
                if preflight.demand_score is not None
                else None
            ),
            (
                f"Channel fit score: {preflight.channel_fit_score}"
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
) -> V2NativeRouteReceipt:
    adapter_key = _V2_ADAPTER_BY_STAGE[stage]
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
        "paid_provider_call": False,
        "max_cost_usd": "0",
    }
    return V2NativeRouteReceipt(
        **payload,
        route_hash=semantic_hash(payload),
    )


def _build_zero_cost_budget(
    *,
    routes: list[V2NativeRouteReceipt],
    requested_ceiling_usd: Decimal,
) -> V2ZeroCostBudgetAuthority:
    payload = {
        "schema_version": "vcos.zero-cost-route-budget.v1",
        "policy_mode": "LOCAL_CAPABILITY_NO_PAID_PROVIDER_LEDGER",
        "requested_ceiling_usd": _decimal_string(requested_ceiling_usd),
        "authorized_cost_usd": "0",
        "paid_provider_calls_allowed": False,
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
