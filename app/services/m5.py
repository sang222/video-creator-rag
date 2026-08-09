# Semantic context and project-admission facades re-export this implementation.
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import (
    AuditEnvelope,
    EventEnvelope,
)
from app.contracts.m5 import (
    AudienceTargetPackCreate,
    ChannelStatePackSnapshotCreate,
    ContextPackSnapshotCreate,
    EditorialCalendarSlotCreate,
    IdeaMarketPreflightCreate,
    RetrievalPlanSnapshotCreate,
    SearchDemandEvidenceCreate,
    SearchIntentMapCreate,
)
from app.contracts.geo_market import TargetMarketDigest, TargetMarketProfile
from app.contracts.nich1 import (
    EditorialSlotValidationResult,
    NicheContractDigest,
    NicheGateVerdict,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    AudienceTargetPack,
    ChannelProfileVersion,
    ChannelStatePackSnapshot,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    ContextPackSnapshot,
    EditorialCalendarSlot,
    GateRun,
    IdeaMarketPreflight,
    LLMRunSnapshot,
    ProjectAdmissionDecision,
    ProviderAttempt,
    ProviderHealthSnapshot,
    QuotaAccount,
    RetrievalPlanSnapshot,
    ReviewTask,
    SearchDemandEvidence,
    SearchIntentMap,
    SeriesPlan,
    SeriesRun,
    User,
    VideoProject,
)
from app.db.models.m7 import UploadedVideo
from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion, LaunchRun
from app.services.audit import AuditService
from app.services.config_registry import content_hash
from app.services.first_launch_authority import launch_run_authority_hash
from app.services.domain_events import DomainEventBus
from app.services.geo_market import (
    TargetMarketDigestCompiler,
    target_market_digest_ref_from_digest,
)
from app.services.nich1 import (
    EditorialSlotValidator,
    NicheContractCompilationError,
    NicheContractDigestCompiler,
)
from app.services.r3d3 import AgentContextContractRegistry, PromptBudgetGate


ALLOWED_CONTEXT_SOURCES = {
    "channel_profile",
    "policy_snapshot",
    "editorial_slot",
    "video_project",
    "artifact_versions",
    "review_tasks",
    "gate_runs",
    "provider_health",
    "quota_ledger",
    "cost_ledger",
    "search_demand_evidence",
    "manual_input",
    "channel_state",
    "niche_contract_digest",
}
DEFAULT_EDITORIAL_CONTEXT_SOURCES = [
    "channel_profile",
    "policy_snapshot",
    "editorial_slot",
    "review_tasks",
    "gate_runs",
    "provider_health",
    "quota_ledger",
    "search_demand_evidence",
    "manual_input",
    "niche_contract_digest",
]
FORBIDDEN_CONTEXT_SOURCES = {
    "all_company_memory",
    "company_memory",
    "vector",
    "vector_index",
    "embedding",
    "rag",
    "source_scraping",
    "autosuggest",
    "credential_secret",
    "raw_secret",
}
SAFE_SEARCH_SOURCES = {
    "OFFICIAL_DOCUMENT",
    "OFFICIAL_MANUAL",
    "PAID_TOOL_CSV",
    "GOOGLE_TRENDS_CSV",
    "YOUTUBE_ANALYTICS",
    "TIKTOK_CREATOR_SEARCH_INSIGHTS_MANUAL",
    "INTERNAL_ANALYTICS",
    "MANUAL_RESEARCH",
}
# Only these sources can carry active, quantitative market-demand authority.
# Official documents stay valuable claim evidence, but a search result or an
# official-doc citation is not itself a demand metric.
QUANTITATIVE_DEMAND_SOURCES = {
    "PAID_TOOL_CSV",
    "GOOGLE_TRENDS_CSV",
    "YOUTUBE_ANALYTICS",
    "INTERNAL_ANALYTICS",
}
# A first-launch preflight is a semantic authority decision.  Scheduled runway
# identity binds this version so a corrected decision is not a retry.
IDEA_MARKET_PREFLIGHT_VERSION = "vcos.idea-market-preflight.v4"
CLAIM_SOURCE_TYPES = {"OFFICIAL_DOCUMENT", "OFFICIAL_MANUAL"}
RAW_SECRET_MARKERS = (
    "sk-",
    "pk_live_",
    "BEGIN PRIVATE KEY",
    "anthropic-",
    "xoxb-",
    "ghp_",
)
SECRET_KEY_FRAGMENTS = {
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential_value",
}
# These are metering counters emitted by the canonical OpenAI provider, not
# credential material.  Keep the exception exact and numeric so a real token
# field or a string payload cannot bypass the secret guard.
SAFE_USAGE_COUNTER_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
}
INITIAL_M5_ARTIFACT_TYPES = ("creative_brief", "research_pack", "source_pack")


class M5AuthorityError(ValidationFailureError):
    def __init__(
        self,
        message: str,
        *,
        terminal_status: str,
        reason_codes: list[str],
        llm_run_snapshot_id: uuid.UUID | None = None,
    ):
        super().__init__(message)
        self.terminal_status = terminal_status
        self.reason_codes = reason_codes
        self.llm_run_snapshot_id = llm_run_snapshot_id


@dataclass(frozen=True)
class LLMWorkflowResult:
    terminal_status: str
    reason_codes: list[str]
    llm_run: LLMRunSnapshot | None
    proposal: dict[str, Any] | None
    provider_attempt: ProviderAttempt | None
    quota_event_id: uuid.UUID | None
    cost_event_id: uuid.UUID | None
    budget_gate_result: dict[str, Any]


@dataclass(frozen=True)
class _TypedSlotNicheAuthority:
    """Read-only NICH1 view backed by a persisted typed slot and SeriesPlan."""

    persisted_slot: EditorialCalendarSlot
    series_key: str | None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.persisted_slot, name)


def _typed_slot_niche_authority(
    slot: EditorialCalendarSlot,
    *,
    preferred_plan: SeriesPlan | None,
) -> EditorialCalendarSlot | _TypedSlotNicheAuthority:
    if (
        slot.schema_version == "v2"
        and slot.series_key is None
        and preferred_plan is not None
    ):
        return _TypedSlotNicheAuthority(
            persisted_slot=slot,
            series_key=preferred_plan.stable_series_key,
        )
    return slot


class EditorialCalendarService:
    def __init__(self, session: Session):
        self.session = session

    def create_slot(
        self,
        *,
        data: EditorialCalendarSlotCreate,
        correlation_id: str = "m5-editorial-slot",
    ) -> EditorialCalendarSlot:
        _validate_channel_policy_scope(
            self.session,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            policy_snapshot_id=data.policy_snapshot_id,
        )
        preferred_plan = _validate_typed_slot_series_preferences(
            self.session,
            data=data,
        )
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        payload = data.model_dump()
        slot = EditorialCalendarSlot(**payload)
        self.session.add(slot)
        self.session.flush()
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, slot.policy_snapshot_id
        )
        if snapshot is not None and _is_nich1_strict_snapshot(snapshot):
            channel = self.session.get(ChannelWorkspace, slot.channel_workspace_id)
            profile = self.session.get(
                ChannelProfileVersion, snapshot.channel_profile_version_id
            )
            category = (
                self.session.get(ContentCategory, slot.category_id)
                if slot.category_id
                else None
            )
            if channel is None or profile is None:
                raise ValidationFailureError("NICH1_SLOT_AUTHORITY_MISSING")
            validation = EditorialSlotValidator().validate(
                channel=channel,
                profile_version=profile,
                policy_snapshot=snapshot,
                channel_contract=(snapshot.compiled_payload or {}).get(
                    "channel_contract_json"
                )
                or {},
                category=category,
                editorial_slot=_typed_slot_niche_authority(
                    slot,
                    preferred_plan=preferred_plan,
                ),
                strict_production=True,
            )
            if validation.verdict != NicheGateVerdict.PASS:
                raise ValidationFailureError(
                    "NICH1_EDITORIAL_SLOT_BLOCKED:"
                    + ",".join(code.value for code in validation.reason_codes)
                )
            slot.operational_envelope = {
                **(slot.operational_envelope or {}),
                "nich1_slot_validation": validation.model_dump(mode="json"),
            }
            self.session.flush()
        _record_m5_event(
            self.session,
            event_type="editorial_calendar_slot.created",
            aggregate_type="editorial_calendar_slot",
            aggregate_id=slot.id,
            actor_id=slot.created_by_user_id,
            target_type="editorial_calendar_slot",
            target_id=slot.id,
            company_id=slot.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "channel_workspace_id": str(slot.channel_workspace_id),
                "policy_snapshot_id": str(slot.policy_snapshot_id),
                "slot_date": slot.slot_date.isoformat(),
                "slot_type": slot.slot_type,
            },
        )
        return slot

    def get_slot(self, slot_id: uuid.UUID) -> EditorialCalendarSlot | None:
        return self.session.get(EditorialCalendarSlot, slot_id)


class ResourceResolverService:
    def __init__(self, session: Session):
        self.session = session

    def create_retrieval_plan(
        self,
        *,
        data: RetrievalPlanSnapshotCreate,
        correlation_id: str = "m5-retrieval-plan",
    ) -> RetrievalPlanSnapshot:
        self._validate_plan_scope(data)
        _validate_allowed_sources(data.allowed_sources, data.excluded_sources)
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        payload = data.model_dump()
        plan_hash = _hash_payload(
            {
                "purpose": data.purpose,
                "company_id": data.company_id,
                "channel_workspace_id": data.channel_workspace_id,
                "channel_profile_version_id": data.channel_profile_version_id,
                "policy_snapshot_id": data.policy_snapshot_id,
                "video_project_id": data.video_project_id,
                "editorial_calendar_slot_id": data.editorial_calendar_slot_id,
                "allowed_sources": data.allowed_sources,
                "excluded_sources": data.excluded_sources,
                "redaction_rules": data.redaction_rules,
                "token_budget": data.token_budget,
                "source_order": data.source_order,
            }
        )
        plan = RetrievalPlanSnapshot(**payload, plan_hash=plan_hash)
        self.session.add(plan)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="retrieval_plan_snapshot.created",
            aggregate_type="retrieval_plan_snapshot",
            aggregate_id=plan.id,
            actor_id=plan.created_by_user_id,
            target_type="retrieval_plan_snapshot",
            target_id=plan.id,
            company_id=plan.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "purpose": plan.purpose,
                "plan_hash": plan.plan_hash,
                "allowed_sources": plan.allowed_sources,
            },
        )
        return plan

    def build_context_pack(
        self,
        *,
        data: ContextPackSnapshotCreate,
        correlation_id: str = "m5-context-pack",
    ) -> ContextPackSnapshot:
        plan = self.require_plan(data.retrieval_plan_snapshot_id)
        _validate_allowed_sources(plan.allowed_sources, plan.excluded_sources)
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        generated = self._build_scoped_pack_content(plan)
        policy_snapshot = (
            self.session.get(CompiledChannelPolicySnapshot, plan.policy_snapshot_id)
            if plan.policy_snapshot_id is not None
            else None
        )
        strict_nich1 = bool(
            policy_snapshot is not None and _is_nich1_strict_snapshot(policy_snapshot)
        )
        if strict_nich1:
            reserved_keys = set(generated["pack_content"])
            attempted_overrides = reserved_keys & set(data.pack_content)
            if attempted_overrides:
                raise ValidationFailureError(
                    "NICH1_CONTEXT_AUTHORITY_OVERRIDE_FORBIDDEN:"
                    + ",".join(sorted(attempted_overrides))
                )
            if data.policy_refs:
                raise ValidationFailureError(
                    "NICH1_CONTEXT_POLICY_REFS_CALLER_FORBIDDEN"
                )
            authority_ref_types = {
                "channel_workspace",
                "channel_profile_version",
                "compiled_channel_policy_snapshot",
                "content_category",
                "editorial_calendar_slot",
                "niche_contract_digest",
                "niche_contract_digest_authority",
            }
            caller_refs = [*data.input_refs, *data.evidence_refs]
            if any(
                str(item.get("type") or "") in authority_ref_types
                for item in caller_refs
                if isinstance(item, dict)
            ):
                raise ValidationFailureError(
                    "NICH1_CONTEXT_AUTHORITY_REF_CALLER_FORBIDDEN"
                )
            input_refs = [*generated["input_refs"], *data.input_refs]
            policy_refs = list(generated["policy_refs"])
            evidence_refs = [*generated["evidence_refs"], *data.evidence_refs]
        else:
            input_refs = data.input_refs or generated["input_refs"]
            policy_refs = data.policy_refs or generated["policy_refs"]
            evidence_refs = data.evidence_refs or generated["evidence_refs"]
        metric_refs = data.metric_refs or []
        memory_refs = data.memory_refs or []
        if memory_refs:
            raise ValidationFailureError(
                "memory refs are not allowed in M5 context packs"
            )
        pack_content = {**generated["pack_content"], **data.pack_content}
        if metric_refs:
            pack_content["metric_truth"] = {
                "state": "PROVIDED_BY_SYSTEM",
                "metric_refs": metric_refs,
            }
        else:
            pack_content["metric_truth"] = {"state": "UNKNOWN", "metric_refs": []}
        _ensure_no_secret_payload(pack_content)
        pack_hash = _hash_payload(
            {
                "input_refs": input_refs,
                "policy_refs": policy_refs,
                "evidence_refs": evidence_refs,
                "metric_refs": metric_refs,
                "memory_refs": memory_refs,
                "pack_content": pack_content,
            }
        )
        pack = ContextPackSnapshot(
            retrieval_plan_snapshot_id=plan.id,
            purpose=plan.purpose,
            company_id=plan.company_id,
            channel_workspace_id=plan.channel_workspace_id,
            channel_profile_version_id=plan.channel_profile_version_id,
            policy_snapshot_id=plan.policy_snapshot_id,
            video_project_id=plan.video_project_id,
            editorial_calendar_slot_id=plan.editorial_calendar_slot_id,
            input_refs=input_refs,
            policy_refs=policy_refs,
            evidence_refs=evidence_refs,
            metric_refs=metric_refs,
            memory_refs=memory_refs,
            pack_content=pack_content,
            freshness_state=data.freshness_state,
            confidence_level=data.confidence_level,
            pack_hash=pack_hash,
            created_by_user_id=data.created_by_user_id,
        )
        if strict_nich1:
            assert policy_snapshot is not None
            _validate_nich1_editorial_context_authority(
                self.session,
                context_pack=pack,
                snapshot=policy_snapshot,
            )
        self.session.add(pack)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="context_pack_snapshot.created",
            aggregate_type="context_pack_snapshot",
            aggregate_id=pack.id,
            actor_id=pack.created_by_user_id,
            target_type="context_pack_snapshot",
            target_id=pack.id,
            company_id=pack.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "retrieval_plan_snapshot_id": str(plan.id),
                "purpose": pack.purpose,
                "pack_hash": pack.pack_hash,
                "freshness_state": pack.freshness_state,
                "confidence_level": pack.confidence_level,
            },
        )
        return pack

    def require_plan(self, plan_id: uuid.UUID) -> RetrievalPlanSnapshot:
        plan = self.session.get(RetrievalPlanSnapshot, plan_id)
        if plan is None:
            raise NotFoundError(f"retrieval plan not found: {plan_id}")
        return plan

    def get_context_pack(
        self, context_pack_id: uuid.UUID
    ) -> ContextPackSnapshot | None:
        return self.session.get(ContextPackSnapshot, context_pack_id)

    def require_context_pack(self, context_pack_id: uuid.UUID) -> ContextPackSnapshot:
        pack = self.get_context_pack(context_pack_id)
        if pack is None:
            raise NotFoundError(f"context pack not found: {context_pack_id}")
        return pack

    def _validate_plan_scope(self, data: RetrievalPlanSnapshotCreate) -> None:
        if not data.allowed_sources:
            raise ValidationFailureError("allowed_sources must be explicit")
        if data.purpose in {"EDITORIAL_RESEARCH", "AUTHORITY_REVIEW", "SEARCH_DEMAND"}:
            if data.channel_workspace_id is None or data.policy_snapshot_id is None:
                raise ValidationFailureError(
                    "editorial authority context requires explicit channel and policy snapshot scope"
                )
        channel: ChannelWorkspace | None = None
        if data.channel_workspace_id is not None:
            channel = self.session.get(ChannelWorkspace, data.channel_workspace_id)
            if channel is None:
                raise NotFoundError(f"channel not found: {data.channel_workspace_id}")
            if channel.company_id != data.company_id:
                raise ValidationFailureError(
                    "channel does not belong to context company"
                )
        if data.policy_snapshot_id is not None:
            snapshot = self.session.get(
                CompiledChannelPolicySnapshot, data.policy_snapshot_id
            )
            if snapshot is None:
                raise NotFoundError(
                    f"policy snapshot not found: {data.policy_snapshot_id}"
                )
            if (
                data.channel_workspace_id is not None
                and snapshot.channel_workspace_id != data.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "policy snapshot does not belong to context channel"
                )
            if data.channel_profile_version_id is None:
                data.channel_profile_version_id = snapshot.channel_profile_version_id
        if data.channel_profile_version_id is not None:
            profile = self.session.get(
                ChannelProfileVersion, data.channel_profile_version_id
            )
            if profile is None:
                raise NotFoundError(
                    f"channel profile version not found: {data.channel_profile_version_id}"
                )
            if (
                data.channel_workspace_id is not None
                and profile.channel_workspace_id != data.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "profile version does not belong to context channel"
                )
        if data.video_project_id is not None:
            project = self.session.get(VideoProject, data.video_project_id)
            if project is None:
                raise NotFoundError(f"project not found: {data.video_project_id}")
            if project.company_id != data.company_id:
                raise ValidationFailureError(
                    "project does not belong to context company"
                )
            if (
                data.channel_workspace_id is not None
                and project.channel_workspace_id != data.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "project does not belong to context channel"
                )
            if (
                data.policy_snapshot_id is not None
                and project.policy_snapshot_id != data.policy_snapshot_id
            ):
                raise ValidationFailureError(
                    "project policy snapshot does not match context policy snapshot"
                )
        if data.editorial_calendar_slot_id is not None:
            slot = self.session.get(
                EditorialCalendarSlot, data.editorial_calendar_slot_id
            )
            if slot is None:
                raise NotFoundError(
                    f"editorial slot not found: {data.editorial_calendar_slot_id}"
                )
            if slot.company_id != data.company_id:
                raise ValidationFailureError("slot does not belong to context company")
            if (
                data.channel_workspace_id is not None
                and slot.channel_workspace_id != data.channel_workspace_id
            ):
                raise ValidationFailureError("slot does not belong to context channel")
            if (
                data.policy_snapshot_id is not None
                and slot.policy_snapshot_id != data.policy_snapshot_id
            ):
                raise ValidationFailureError(
                    "slot policy snapshot does not match context policy snapshot"
                )
        if channel is None:
            _require_company(self.session, data.company_id)

    def _build_scoped_pack_content(self, plan: RetrievalPlanSnapshot) -> dict[str, Any]:
        allowed = set(plan.allowed_sources)
        input_refs: list[dict[str, Any]] = []
        policy_refs: list[dict[str, Any]] = []
        evidence_refs: list[dict[str, Any]] = []
        pack_content: dict[str, Any] = {
            "scope": _plan_scope(plan),
            "numeric_truth_contract": "SQL_OR_UNKNOWN",
        }
        if "channel_profile" in allowed and plan.channel_workspace_id is not None:
            channel = self.session.get(ChannelWorkspace, plan.channel_workspace_id)
            profile = (
                self.session.get(ChannelProfileVersion, plan.channel_profile_version_id)
                if plan.channel_profile_version_id
                else None
            )
            if channel is not None:
                pack_content["channel"] = {
                    "id": str(channel.id),
                    "key": channel.key,
                    "name": channel.name,
                    "primary_language": channel.primary_language,
                    "target_market": channel.target_market,
                }
                input_refs.append({"type": "channel_workspace", "id": str(channel.id)})
            if profile is not None:
                pack_content["profile"] = {
                    "id": str(profile.id),
                    "version": profile.version,
                    "status": profile.status,
                    "profile_input_hash": profile.profile_input_hash,
                }
                input_refs.append(
                    {"type": "channel_profile_version", "id": str(profile.id)}
                )
        if "policy_snapshot" in allowed and plan.policy_snapshot_id is not None:
            snapshot = self.session.get(
                CompiledChannelPolicySnapshot, plan.policy_snapshot_id
            )
            if snapshot is not None:
                pack_content["policy_snapshot"] = {
                    "id": str(snapshot.id),
                    "status": snapshot.status,
                    "content_hash": snapshot.content_hash,
                    "compiler_version": snapshot.compiler_version,
                    "channel_profile_version_id": str(
                        snapshot.channel_profile_version_id
                    ),
                }
                policy_refs.append(
                    {
                        "type": "compiled_channel_policy_snapshot",
                        "id": str(snapshot.id),
                        "content_hash": snapshot.content_hash,
                    }
                )
        if "editorial_slot" in allowed and plan.editorial_calendar_slot_id is not None:
            slot = self.session.get(
                EditorialCalendarSlot, plan.editorial_calendar_slot_id
            )
            if slot is not None:
                pack_content["editorial_slot"] = {
                    "id": str(slot.id),
                    "slot_date": slot.slot_date.isoformat(),
                    "slot_type": slot.slot_type,
                    "production_goal": slot.production_goal,
                    "target_platforms": slot.target_platforms,
                    "content_pillar": slot.content_pillar,
                    "series_key": slot.series_key,
                    "format_hint": slot.format_hint,
                    "risk_level": slot.risk_level,
                    "operational_envelope": slot.operational_envelope,
                }
                input_refs.append(
                    {"type": "editorial_calendar_slot", "id": str(slot.id)}
                )
        if (
            "niche_contract_digest" in allowed
            and plan.channel_workspace_id is not None
            and plan.channel_profile_version_id is not None
            and plan.policy_snapshot_id is not None
            and plan.editorial_calendar_slot_id is not None
        ):
            channel = self.session.get(ChannelWorkspace, plan.channel_workspace_id)
            profile = self.session.get(
                ChannelProfileVersion, plan.channel_profile_version_id
            )
            snapshot = self.session.get(
                CompiledChannelPolicySnapshot, plan.policy_snapshot_id
            )
            slot = self.session.get(
                EditorialCalendarSlot, plan.editorial_calendar_slot_id
            )
            category = (
                self.session.get(ContentCategory, slot.category_id)
                if slot and slot.category_id
                else None
            )
            if snapshot is not None and _is_nich1_strict_snapshot(snapshot):
                if any(item is None for item in (channel, profile, slot, category)):
                    raise ValidationFailureError(
                        "NICH1_EDITORIAL_CONTEXT_AUTHORITY_MISSING"
                    )
                try:
                    digest = NicheContractDigestCompiler().compile(
                        channel=channel,
                        profile_version=profile,
                        policy_snapshot=snapshot,
                        category=category,
                        editorial_slot=slot,
                    )
                except NicheContractCompilationError as exc:
                    raise ValidationFailureError(
                        f"NICH1_EDITORIAL_CONTEXT_BLOCKED:{exc}"
                    ) from exc
                digest_payload = digest.model_dump(mode="json")
                digest_ref = {
                    "type": "niche_contract_digest",
                    "ref": digest.editorial_slot_ref + "#niche_contract_digest",
                    "content_hash": digest.content_hash,
                }
                editorial_slot_digest = {
                    "slot_id": str(digest.editorial_slot_id),
                    "slot_ref": digest.editorial_slot_ref,
                    "slot_hash": digest.editorial_slot_hash,
                    "category_id": str(digest.category_id),
                    "content_pillar_id": digest.content_pillar_id,
                    "content_pillar_key": digest.content_pillar_key,
                    "series_key": digest.series_key,
                    "production_goal": digest.production_goal,
                }
                bounded_evidence = (
                    _search_evidence_refs(
                        self.session, plan.company_id, plan.channel_workspace_id
                    )
                    if "search_demand_evidence" in allowed
                    else []
                )
                sections = {
                    "niche_contract_digest": digest_payload,
                    "editorial_slot_digest": editorial_slot_digest,
                    "runtime_guard_digest": {
                        "compiled_policy_snapshot_id": str(snapshot.id),
                        "compiled_policy_snapshot_hash": snapshot.content_hash,
                        "provider_calls_allowed": False,
                        "direct_provider_sdk_allowed": False,
                    },
                    "evidence_digest": {
                        "evidence_refs": bounded_evidence,
                        "numeric_truth_contract": "SQL_OR_UNKNOWN",
                    },
                    "common_skill_digest": {
                        "policy_truth": "AUTHORITATIVE_REFS_ONLY",
                        "unsupported_claims_forbidden": True,
                    },
                }
                contract = AgentContextContractRegistry().resolve(
                    "EditorialIdeaResearchAgent",
                    task_type="editorial_idea_research",
                    lane="cheap_structured",
                )
                budget = PromptBudgetGate().apply(
                    contract=contract,
                    sections=sections,
                    initial_omitted=[],
                )
                if budget.status != "OK":
                    raise ValidationFailureError(
                        "NICH1_EDITORIAL_CONTEXT_BUDGET_BLOCKED:"
                        + ",".join(budget.reason_codes)
                    )
                pack_content.update(budget.sections)
                pack_content["niche_contract_digest_ref"] = digest_ref
                pack_content["prompt_budget_report"] = budget.budget_report
                pack_content["agent_context_pack"] = {
                    "schema_version": "r3d3.agent-context-pack.v1",
                    "agent_key": "EditorialIdeaResearchAgent",
                    "agent_context_contract": contract.to_dict(),
                    "digests": budget.sections,
                    "prompt_budget_report": budget.budget_report,
                    "omitted_items": budget.omitted_items,
                }
                input_refs.extend(
                    [
                        {
                            "type": "content_category",
                            "id": str(category.id),
                            "content_hash": category.content_hash,
                        },
                        {
                            "type": "editorial_calendar_slot",
                            "id": str(slot.id),
                            "content_hash": digest.editorial_slot_hash,
                        },
                        digest_ref,
                    ]
                )
                policy_refs.append(
                    {
                        "type": "niche_contract_digest_authority",
                        "compiled_policy_snapshot_id": str(snapshot.id),
                        "content_hash": digest.content_hash,
                    }
                )
        if "video_project" in allowed and plan.video_project_id is not None:
            project = self.session.get(VideoProject, plan.video_project_id)
            if project is not None:
                pack_content["project"] = {
                    "id": str(project.id),
                    "title": project.title,
                    "status": project.status,
                    "policy_snapshot_id": str(project.policy_snapshot_id),
                }
                input_refs.append({"type": "video_project", "id": str(project.id)})
        if "review_tasks" in allowed:
            pending_reviews = _pending_reviews(
                self.session, plan.company_id, plan.channel_workspace_id
            )
            pack_content["pending_reviews"] = pending_reviews
            input_refs.extend(
                {"type": "review_task", "id": item["id"]}
                for item in pending_reviews[:20]
            )
        if "gate_runs" in allowed:
            gate_summary = _gate_summary(
                self.session, plan.company_id, plan.channel_workspace_id
            )
            pack_content["gate_summary"] = gate_summary
        if "provider_health" in allowed:
            pack_content["provider_health"] = _provider_health_summary(self.session)
        if "quota_ledger" in allowed:
            pack_content["quota_summary"] = _quota_summary(self.session)
        if (
            "search_demand_evidence" in allowed
            and plan.channel_workspace_id is not None
        ):
            evidence = _search_evidence_refs(
                self.session, plan.company_id, plan.channel_workspace_id
            )
            evidence_refs.extend(evidence)
            pack_content["search_demand_evidence_refs"] = evidence
        return {
            "input_refs": input_refs,
            "policy_refs": policy_refs,
            "evidence_refs": evidence_refs,
            "pack_content": pack_content,
        }


class ChannelStatePackService:
    def __init__(self, session: Session):
        self.session = session

    def build_snapshot(
        self,
        *,
        data: ChannelStatePackSnapshotCreate,
        correlation_id: str = "m5-channel-state-pack",
    ) -> ChannelStatePackSnapshot:
        _validate_channel_policy_scope(
            self.session,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            policy_snapshot_id=data.policy_snapshot_id,
        )
        if data.context_pack_snapshot_id is not None:
            pack = ResourceResolverService(self.session).require_context_pack(
                data.context_pack_snapshot_id
            )
            if (
                pack.company_id != data.company_id
                or pack.channel_workspace_id != data.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "context pack scope does not match channel state scope"
                )
        active_project_refs = _active_project_refs(
            self.session, data.company_id, data.channel_workspace_id
        )
        pending_review_refs = _pending_reviews(
            self.session, data.company_id, data.channel_workspace_id
        )
        readiness_summary = _readiness_summary(
            self.session, data.company_id, data.channel_workspace_id
        )
        provider_health_summary = _provider_health_summary(self.session)
        quota_summary = _quota_summary(self.session)
        evidence_summary = {
            "search_demand_evidence_count": self.session.scalar(
                select(func.count())
                .select_from(SearchDemandEvidence)
                .where(SearchDemandEvidence.company_id == data.company_id)
                .where(
                    SearchDemandEvidence.channel_workspace_id
                    == data.channel_workspace_id
                )
            )
            or 0,
            "analytics_state": "UNKNOWN",
        }
        state_blob = {
            "company_id": str(data.company_id),
            "channel_workspace_id": str(data.channel_workspace_id),
            "policy_snapshot_id": str(data.policy_snapshot_id),
            "analytics": {"state": "UNKNOWN", "reason_code": "METRIC_REF_UNKNOWN"},
        }
        state_hash = _hash_payload(
            {
                "state_blob": state_blob,
                "active_project_refs": active_project_refs,
                "pending_review_refs": pending_review_refs,
                "readiness_summary": readiness_summary,
                "provider_health_summary": provider_health_summary,
                "quota_summary": quota_summary,
                "evidence_summary": evidence_summary,
            }
        )
        snapshot = ChannelStatePackSnapshot(
            editorial_research_run_id=data.editorial_research_run_id,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            policy_snapshot_id=data.policy_snapshot_id,
            context_pack_snapshot_id=data.context_pack_snapshot_id,
            state_blob=state_blob,
            active_project_refs=active_project_refs,
            pending_review_refs=pending_review_refs,
            readiness_summary=readiness_summary,
            provider_health_summary=provider_health_summary,
            quota_summary=quota_summary,
            evidence_summary=evidence_summary,
            freshness_state="UNKNOWN"
            if provider_health_summary["llm_router"]["state"] == "UNKNOWN"
            else "FRESH",
            confidence_level="UNKNOWN"
            if evidence_summary["search_demand_evidence_count"] == 0
            else "MEDIUM",
            state_hash=state_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="channel_state_pack_snapshot.created",
            aggregate_type="channel_state_pack_snapshot",
            aggregate_id=snapshot.id,
            actor_id=None,
            target_type="channel_state_pack_snapshot",
            target_id=snapshot.id,
            company_id=snapshot.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "channel_workspace_id": str(snapshot.channel_workspace_id),
                "policy_snapshot_id": str(snapshot.policy_snapshot_id),
                "state_hash": snapshot.state_hash,
            },
        )
        return snapshot


class SearchDemandEvidenceService:
    def __init__(self, session: Session):
        self.session = session

    def create_evidence(
        self,
        *,
        data: SearchDemandEvidenceCreate,
        correlation_id: str = "m5-search-demand-evidence",
    ) -> SearchDemandEvidence:
        channel = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {data.channel_workspace_id}")
        if channel.company_id != data.company_id:
            raise ValidationFailureError("channel does not belong to evidence company")
        if data.evidence_source_type not in SAFE_SEARCH_SOURCES:
            raise ValidationFailureError("search demand source type is not M5-safe")
        _ensure_no_secret_payload(data.model_dump(mode="json"))
        payload = data.model_dump()
        purpose = payload.get("authority_purpose") or _infer_evidence_authority_purpose(
            data.evidence_source_type
        )
        if purpose == "CLAIM_SOURCE" and data.evidence_source_type not in CLAIM_SOURCE_TYPES:
            raise ValidationFailureError("CLAIM_EVIDENCE_SOURCE_TYPE_INVALID")
        if purpose == "MARKET_DEMAND":
            if data.evidence_source_type not in QUANTITATIVE_DEMAND_SOURCES:
                raise ValidationFailureError("MARKET_DEMAND_SOURCE_NOT_QUANTITATIVE")
            if data.search_volume_30d is None and data.relative_interest_index is None:
                raise ValidationFailureError("MARKET_DEMAND_QUANTITATIVE_METRIC_REQUIRED")
        if purpose == "CLAIM_SOURCE" and any(
            value is not None
            for value in (
                data.search_volume_30d,
                data.relative_interest_index,
                data.competition_index,
                data.trending_velocity,
            )
        ):
            raise ValidationFailureError("OFFICIAL_DOCUMENT_NOT_DEMAND_METRIC")
        payload["authority_purpose"] = purpose
        metadata = payload.pop("metadata")
        metadata = {
            **metadata,
            "authority": {
                "purpose": purpose,
                "source_category": data.evidence_source_type,
                "schema_version": "vcos.evidence-authority.v1",
            },
        }
        if payload.get("captured_at") is None:
            payload.pop("captured_at")
        evidence = SearchDemandEvidence(**payload, metadata_=metadata)
        self.session.add(evidence)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="search_demand_evidence.created",
            aggregate_type="search_demand_evidence",
            aggregate_id=evidence.id,
            actor_id=None,
            target_type="search_demand_evidence",
            target_id=evidence.id,
            company_id=evidence.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "channel_workspace_id": str(evidence.channel_workspace_id),
                "evidence_source_type": evidence.evidence_source_type,
                "platform": evidence.platform,
                "evidence_confidence": evidence.evidence_confidence,
            },
        )
        return evidence


class SearchIntentService:
    def __init__(self, session: Session):
        self.session = session

    def create_map(
        self,
        *,
        data: SearchIntentMapCreate,
        correlation_id: str = "m5-search-intent-map",
    ) -> SearchIntentMap:
        _require_channel_for_company(
            self.session, data.company_id, data.channel_workspace_id
        )
        item = SearchIntentMap(**data.model_dump())
        self.session.add(item)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="search_intent_map.created",
            aggregate_type="search_intent_map",
            aggregate_id=item.id,
            actor_id=None,
            target_type="search_intent_map",
            target_id=item.id,
            company_id=item.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "demand_confidence": item.demand_confidence,
                "source_evidence_refs": item.source_evidence_refs,
            },
        )
        return item


class AudienceTargetService:
    def __init__(self, session: Session):
        self.session = session

    def create_pack(
        self,
        *,
        data: AudienceTargetPackCreate,
        correlation_id: str = "m5-audience-target-pack",
    ) -> AudienceTargetPack:
        _require_channel_for_company(
            self.session, data.company_id, data.channel_workspace_id
        )
        item = AudienceTargetPack(**data.model_dump())
        self.session.add(item)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="audience_target_pack.created",
            aggregate_type="audience_target_pack",
            aggregate_id=item.id,
            actor_id=None,
            target_type="audience_target_pack",
            target_id=item.id,
            company_id=item.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "confidence_level": item.confidence_level,
                "evidence_refs": item.evidence_refs,
            },
        )
        return item


class IdeaMarketPreflightService:
    """Persist strict channel/niche/market evidence for long-form candidates."""

    def __init__(self, session: Session):
        self.session = session

    def create_preflight(
        self,
        *,
        data: IdeaMarketPreflightCreate,
        correlation_id: str = "m5-idea-market-preflight",
    ) -> IdeaMarketPreflight:
        _require_channel_for_company(
            self.session,
            data.company_id,
            data.channel_workspace_id,
        )
        research_run = None
        candidate = None
        slot = None
        if data.editorial_research_run_id is not None:
            from app.db.models import EditorialResearchRun

            research_run = self.session.get(
                EditorialResearchRun,
                data.editorial_research_run_id,
            )
            if (
                research_run is None
                or research_run.company_id != data.company_id
                or research_run.channel_workspace_id != data.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "EDITORIAL_PREFLIGHT_RESEARCH_RUN_SCOPE_MISMATCH"
                )
        if data.editorial_idea_candidate_id is not None:
            from app.db.models import EditorialIdeaCandidate

            candidate = self.session.get(
                EditorialIdeaCandidate,
                data.editorial_idea_candidate_id,
            )
            if (
                candidate is None
                or candidate.company_id != data.company_id
                or candidate.channel_workspace_id != data.channel_workspace_id
                or (
                    research_run is not None
                    and candidate.editorial_research_run_id != research_run.id
                )
            ):
                raise ValidationFailureError(
                    "EDITORIAL_PREFLIGHT_CANDIDATE_SCOPE_MISMATCH"
                )
        if data.editorial_calendar_slot_id is not None:
            slot = self.session.get(
                EditorialCalendarSlot,
                data.editorial_calendar_slot_id,
            )
            if (
                slot is None
                or slot.company_id != data.company_id
                or slot.channel_workspace_id != data.channel_workspace_id
                or slot.schema_version != "v2"
                or slot.production_lane != "LONG_FORM"
            ):
                raise ValidationFailureError(
                    "LONG_FORM_PREFLIGHT_SLOT_AUTHORITY_MISMATCH"
                )

        result = _evaluate_v2_long_form_preflight(
            self.session,
            data=data,
            candidate=candidate,
            research_run=research_run,
            slot=slot,
        )
        item = IdeaMarketPreflight(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            editorial_calendar_slot_id=data.editorial_calendar_slot_id,
            editorial_research_run_id=data.editorial_research_run_id,
            editorial_idea_candidate_id=data.editorial_idea_candidate_id,
            search_intent_map_id=data.search_intent_map_id,
            audience_target_pack_id=data.audience_target_pack_id,
            demand_score=result["demand_score"],
            channel_fit_score=result["channel_fit_score"],
            policy_fit_state=result["policy_fit_state"],
            niche_contract_digest_ref=result["niche_contract_digest_ref"],
            niche_contract_digest_hash=result["niche_contract_digest_hash"],
            target_market_digest_ref=result["target_market_digest_ref"],
            target_market_digest_hash=result["target_market_digest_hash"],
            editorial_slot_ref=result["editorial_slot_ref"],
            content_category_ref=result["content_category_ref"],
            target_market=result["target_market"],
            market_scope=result["market_scope"],
            market_fit_score=result["market_fit_score"],
            market_fit_threshold=result["market_fit_threshold"],
            confidence_state=result["confidence"],
            evidence_blob=result["evidence_blob"],
            reason_codes=result["reason_codes"],
            decision=result["decision"],
        )
        self.session.add(item)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="idea_market_preflight.created",
            aggregate_type="idea_market_preflight",
            aggregate_id=item.id,
            actor_id=None,
            target_type="idea_market_preflight",
            target_id=item.id,
            company_id=item.company_id,
            correlation_id=correlation_id,
            reason_code=(
                item.reason_codes[0]
                if item.reason_codes
                else "EDITORIAL_PREFLIGHT_CREATED"
            ),
            payload={
                "decision": item.decision,
                "reason_codes": item.reason_codes,
                "editorial_idea_candidate_id": (
                    str(item.editorial_idea_candidate_id)
                    if item.editorial_idea_candidate_id
                    else None
                ),
            },
        )
        return item


class ProjectAdmissionService:
    """Canonical long-form assignment facade retained for stable imports."""

    def __init__(self, session: Session):
        self.session = session

    def create_decision(
        self,
        *,
        data: Any,
        correlation_id: str = "vcos-long-form-project-admission",
    ) -> ProjectAdmissionDecision:
        from app.services.vcos_v2 import ProjectAdmissionV2Service

        return ProjectAdmissionV2Service(self.session).create_decision(
            data=data,
            correlation_id=correlation_id,
        )

    def create_v2_decision(
        self,
        *,
        data: Any,
        correlation_id: str = "vcos-long-form-project-admission",
    ) -> ProjectAdmissionDecision:
        return self.create_decision(data=data, correlation_id=correlation_id)

    def get_decision(
        self,
        decision_id: uuid.UUID,
    ) -> ProjectAdmissionDecision | None:
        return self.session.get(ProjectAdmissionDecision, decision_id)


def _validate_allowed_sources(
    allowed_sources: list[str], excluded_sources: list[str]
) -> None:
    if not allowed_sources:
        raise ValidationFailureError("allowed_sources must be explicit")
    for source in [*allowed_sources, *excluded_sources]:
        if source in FORBIDDEN_CONTEXT_SOURCES or source not in ALLOWED_CONTEXT_SOURCES:
            raise ValidationFailureError(f"retrieval source not allowed: {source}")
    if set(allowed_sources) & set(excluded_sources):
        raise ValidationFailureError("source cannot be both allowed and excluded")


def _validate_channel_policy_scope(
    session: Session,
    *,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    policy_snapshot_id: uuid.UUID,
) -> None:
    channel = _require_channel_for_company(session, company_id, channel_workspace_id)
    snapshot = session.get(CompiledChannelPolicySnapshot, policy_snapshot_id)
    if snapshot is None:
        raise NotFoundError(f"policy snapshot not found: {policy_snapshot_id}")
    if snapshot.channel_workspace_id != channel.id:
        raise ValidationFailureError("policy snapshot does not belong to channel")


def _validate_typed_slot_series_preferences(
    session: Session,
    *,
    data: EditorialCalendarSlotCreate,
) -> SeriesPlan | None:
    if data.schema_version != "v2" or data.preferred_series_plan_id is None:
        return None

    snapshot = session.get(
        CompiledChannelPolicySnapshot,
        data.policy_snapshot_id,
    )
    plan = session.get(SeriesPlan, data.preferred_series_plan_id)
    if plan is None:
        raise NotFoundError(
            f"preferred series plan not found: {data.preferred_series_plan_id}"
        )
    if (
        snapshot is None
        or plan.company_id != data.company_id
        or plan.channel_workspace_id != data.channel_workspace_id
        or plan.policy_snapshot_id != data.policy_snapshot_id
        or plan.channel_profile_version_id != snapshot.channel_profile_version_id
    ):
        raise ValidationFailureError("V2_SLOT_SERIES_PLAN_SCOPE_MISMATCH")
    if data.production_lane is None or data.production_lane.value not in set(
        plan.allowed_production_lanes or []
    ):
        raise ValidationFailureError("V2_SLOT_SERIES_PLAN_LANE_MISMATCH")

    if data.preferred_series_run_id is None:
        return plan
    run = session.get(SeriesRun, data.preferred_series_run_id)
    if run is None:
        raise NotFoundError(
            f"preferred series run not found: {data.preferred_series_run_id}"
        )
    if run.series_plan_id != plan.id:
        raise ValidationFailureError("V2_SLOT_SERIES_RUN_PLAN_MISMATCH")
    if (
        run.company_id != data.company_id
        or run.channel_workspace_id != data.channel_workspace_id
        or run.policy_snapshot_id != data.policy_snapshot_id
        or run.channel_profile_version_id != snapshot.channel_profile_version_id
    ):
        raise ValidationFailureError("V2_SLOT_SERIES_RUN_SCOPE_MISMATCH")
    return plan


def _require_company(session: Session, company_id: uuid.UUID) -> None:
    from app.db.models import Company

    if session.get(Company, company_id) is None:
        raise NotFoundError(f"company not found: {company_id}")


def _require_channel_for_company(
    session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID
) -> ChannelWorkspace:
    channel = session.get(ChannelWorkspace, channel_workspace_id)
    if channel is None:
        raise NotFoundError(f"channel not found: {channel_workspace_id}")
    if channel.company_id != company_id:
        raise ValidationFailureError("channel does not belong to company")
    return channel


def _require_user(session: Session, user_id: uuid.UUID, field_name: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"{field_name} not found: {user_id}")
    return user


def _plan_scope(plan: RetrievalPlanSnapshot) -> dict[str, Any]:
    return {
        "purpose": plan.purpose,
        "company_id": str(plan.company_id),
        "channel_workspace_id": str(plan.channel_workspace_id)
        if plan.channel_workspace_id
        else None,
        "channel_profile_version_id": str(plan.channel_profile_version_id)
        if plan.channel_profile_version_id
        else None,
        "policy_snapshot_id": str(plan.policy_snapshot_id)
        if plan.policy_snapshot_id
        else None,
        "video_project_id": str(plan.video_project_id)
        if plan.video_project_id
        else None,
        "editorial_calendar_slot_id": str(plan.editorial_calendar_slot_id)
        if plan.editorial_calendar_slot_id
        else None,
    }


def _active_project_refs(
    session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID
) -> list[dict[str, Any]]:
    projects = session.scalars(
        select(VideoProject)
        .where(VideoProject.company_id == company_id)
        .where(VideoProject.channel_workspace_id == channel_workspace_id)
        .where(VideoProject.status.in_(["draft", "in_review"]))
        .order_by(VideoProject.created_at.asc())
    ).all()
    return [
        {
            "type": "video_project",
            "id": str(project.id),
            "status": project.status,
            "title": project.title,
        }
        for project in projects
    ]


def _pending_reviews(
    session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    statement = (
        select(ReviewTask, VideoProject)
        .join(VideoProject, ReviewTask.video_project_id == VideoProject.id)
        .where(VideoProject.company_id == company_id)
        .where(ReviewTask.status.in_(["open", "in_progress"]))
        .order_by(ReviewTask.created_at.asc())
    )
    if channel_workspace_id is not None:
        statement = statement.where(
            VideoProject.channel_workspace_id == channel_workspace_id
        )
    rows = session.execute(statement).all()
    return [
        {
            "id": str(review.id),
            "type": "review_task",
            "video_project_id": str(project.id),
            "review_type": review.review_type,
            "status": review.status,
            "reason_codes": review.review_reason_codes,
        }
        for review, project in rows
    ]


def _readiness_summary(
    session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID
) -> dict[str, Any]:
    statement = (
        select(GateRun.result, func.count())
        .join(VideoProject, GateRun.video_project_id == VideoProject.id)
        .where(VideoProject.company_id == company_id)
        .where(VideoProject.channel_workspace_id == channel_workspace_id)
        .group_by(GateRun.result)
    )
    counts = {result: count for result, count in session.execute(statement).all()}
    return {"gate_result_counts": counts, "state": "UNKNOWN" if not counts else "FRESH"}


def _gate_summary(
    session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID | None
) -> dict[str, Any]:
    statement = (
        select(GateRun.gate_key, GateRun.result, func.count())
        .join(VideoProject, GateRun.video_project_id == VideoProject.id)
        .where(VideoProject.company_id == company_id)
        .group_by(GateRun.gate_key, GateRun.result)
    )
    if channel_workspace_id is not None:
        statement = statement.where(
            VideoProject.channel_workspace_id == channel_workspace_id
        )
    return {
        f"{gate_key}:{result}": count
        for gate_key, result, count in session.execute(statement).all()
    }


def _provider_health_summary(session: Session) -> dict[str, Any]:
    health = _latest_provider_health(session, "llm_router")
    return {
        "llm_router": {
            "state": health.health_state if health else "UNKNOWN",
            "reason_codes": health.reason_codes
            if health
            else ["LLM_PROVIDER_NOT_CONFIGURED"],
            "checked_at": health.checked_at.isoformat() if health else None,
        }
    }


def _latest_provider_health(
    session: Session, provider_key: str
) -> ProviderHealthSnapshot | None:
    return session.scalars(
        select(ProviderHealthSnapshot)
        .where(ProviderHealthSnapshot.provider_key == provider_key)
        .order_by(ProviderHealthSnapshot.checked_at.desc())
        .limit(1)
    ).one_or_none()


def _quota_summary(session: Session) -> dict[str, Any]:
    accounts = session.scalars(
        select(QuotaAccount).where(QuotaAccount.provider_key == "llm_router")
    ).all()
    return {
        "llm_router": [
            {
                "quota_account_id": str(account.id),
                "status": account.status,
                "unit": account.unit,
                "quota_limit": str(account.quota_limit)
                if account.quota_limit is not None
                else None,
                "quota_used": str(account.quota_used),
                "quota_reserved": str(account.quota_reserved),
            }
            for account in accounts
        ]
    }


def _search_evidence_refs(
    session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(SearchDemandEvidence)
        .where(SearchDemandEvidence.company_id == company_id)
        .where(SearchDemandEvidence.channel_workspace_id == channel_workspace_id)
        .order_by(SearchDemandEvidence.created_at.desc())
        .limit(20)
    ).all()
    return [
        {
            "type": "search_demand_evidence",
            "id": str(row.id),
            "query": row.query,
            "platform": row.platform,
            "confidence": row.evidence_confidence,
            "captured_at": row.captured_at.isoformat(),
        }
        for row in rows
    ]


def _evaluate_v2_long_form_preflight(
    session: Session,
    *,
    data: IdeaMarketPreflightCreate,
    candidate: Any,
    research_run: Any,
    slot: EditorialCalendarSlot | None,
) -> dict[str, Any]:
    """Recompile every authority used by a strict long-form preflight."""

    if slot is None:
        raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_SLOT_REQUIRED")
    snapshot = session.get(
        CompiledChannelPolicySnapshot,
        slot.policy_snapshot_id,
    )
    channel = session.get(ChannelWorkspace, slot.channel_workspace_id)
    category = (
        session.get(ContentCategory, slot.category_id)
        if slot.category_id is not None
        else None
    )
    profile = (
        session.get(
            ChannelProfileVersion,
            snapshot.channel_profile_version_id,
        )
        if snapshot is not None
        else None
    )
    if any(item is None for item in (snapshot, channel, category, profile)):
        raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_SLOT_AUTHORITY_MISSING")
    assert snapshot is not None
    assert channel is not None
    assert category is not None
    assert profile is not None
    if not _is_nich1_strict_snapshot(snapshot):
        raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_POLICY_NOT_STRICT")
    if (
        candidate is not None
        and (
            candidate.policy_snapshot_id != snapshot.id
            or candidate.channel_workspace_id != channel.id
        )
    ) or (
        research_run is not None
        and (
            research_run.policy_snapshot_id != snapshot.id
            or research_run.channel_profile_version_id != profile.id
        )
    ):
        raise ValidationFailureError(
            "V2_LONG_FORM_PREFLIGHT_EDITORIAL_AUTHORITY_MISMATCH"
        )

    preferred_plan: SeriesPlan | None = None
    if slot.preferred_series_plan_id is not None:
        preferred_plan = session.get(
            SeriesPlan,
            slot.preferred_series_plan_id,
        )
        if (
            preferred_plan is None
            or preferred_plan.company_id != slot.company_id
            or preferred_plan.channel_workspace_id != slot.channel_workspace_id
            or preferred_plan.policy_snapshot_id != snapshot.id
            or preferred_plan.channel_profile_version_id != profile.id
            or "LONG_FORM" not in set(preferred_plan.allowed_production_lanes or [])
        ):
            raise ValidationFailureError(
                "V2_LONG_FORM_PREFLIGHT_SERIES_PLAN_SCOPE_MISMATCH"
            )
    if slot.preferred_series_run_id is not None:
        preferred_run = session.get(
            SeriesRun,
            slot.preferred_series_run_id,
        )
        if (
            preferred_plan is None
            or preferred_run is None
            or preferred_run.series_plan_id != preferred_plan.id
            or preferred_run.company_id != slot.company_id
            or preferred_run.channel_workspace_id != slot.channel_workspace_id
            or preferred_run.policy_snapshot_id != snapshot.id
            or preferred_run.channel_profile_version_id != profile.id
        ):
            raise ValidationFailureError(
                "V2_LONG_FORM_PREFLIGHT_SERIES_RUN_SCOPE_MISMATCH"
            )

    raw_validation = (slot.operational_envelope or {}).get("nich1_slot_validation")
    if not isinstance(raw_validation, dict):
        raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_SLOT_VALIDATION_MISSING")
    try:
        stored_validation = EditorialSlotValidationResult.model_validate(raw_validation)
    except ValidationError as exc:
        raise ValidationFailureError(
            "V2_LONG_FORM_PREFLIGHT_SLOT_VALIDATION_INVALID"
        ) from exc
    current_validation = EditorialSlotValidator().validate(
        channel=channel,
        profile_version=profile,
        policy_snapshot=snapshot,
        channel_contract=(snapshot.compiled_payload or {}).get("channel_contract_json")
        or {},
        category=category,
        editorial_slot=_typed_slot_niche_authority(
            slot,
            preferred_plan=preferred_plan,
        ),
        strict_production=True,
    )
    if (
        stored_validation.verdict != NicheGateVerdict.PASS
        or current_validation.verdict != NicheGateVerdict.PASS
        or stored_validation.content_hash != current_validation.content_hash
    ):
        raise ValidationFailureError(
            "V2_LONG_FORM_PREFLIGHT_SLOT_VALIDATION_NOT_CURRENT_PASS"
        )

    try:
        niche_digest = NicheContractDigestCompiler().compile(
            channel=channel,
            profile_version=profile,
            policy_snapshot=snapshot,
            category=category,
            editorial_slot=_typed_slot_niche_authority(
                slot,
                preferred_plan=preferred_plan,
            ),
        )
    except NicheContractCompilationError as exc:
        raise ValidationFailureError(
            f"V2_LONG_FORM_PREFLIGHT_NICHE_DIGEST_BLOCKED:{exc}"
        ) from exc
    if candidate is not None and candidate.context_pack_snapshot_id is not None:
        context_pack = session.get(
            ContextPackSnapshot,
            candidate.context_pack_snapshot_id,
        )
        if context_pack is None:
            raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_CONTEXT_PACK_MISSING")
        context_digest = _validate_nich1_editorial_context_authority(
            session,
            context_pack=context_pack,
            snapshot=snapshot,
        )
        if context_digest.content_hash != niche_digest.content_hash:
            raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_CONTEXT_DIGEST_STALE")

    scoped_policy = (snapshot.compiled_payload or {}).get("channel_scoped_policy")
    scoped_policy = scoped_policy if isinstance(scoped_policy, dict) else {}
    profile_raw = scoped_policy.get("target_market_profile")
    digest_raw = scoped_policy.get("target_market_digest")
    if not isinstance(profile_raw, dict) or not isinstance(
        digest_raw,
        dict,
    ):
        raise ValidationFailureError(
            "V2_LONG_FORM_PREFLIGHT_TARGET_MARKET_AUTHORITY_MISSING"
        )
    try:
        target_profile = TargetMarketProfile.model_validate(profile_raw)
        embedded_target_digest = TargetMarketDigest.model_validate(digest_raw)
        target_digest = TargetMarketDigestCompiler().compile(target_profile)
    except ValidationError as exc:
        raise ValidationFailureError(
            "V2_LONG_FORM_PREFLIGHT_TARGET_MARKET_AUTHORITY_INVALID"
        ) from exc
    if embedded_target_digest.model_dump(mode="json") != target_digest.model_dump(
        mode="json"
    ):
        raise ValidationFailureError(
            "V2_LONG_FORM_PREFLIGHT_TARGET_MARKET_DIGEST_STALE"
        )

    # The legacy table name is retained for compatibility, but its records are
    # no longer a single mixed authority.  Fresh official documents bind
    # factual claims; only typed quantitative rows can prove steady-state
    # demand.  Historical generic lists are classified conservatively.
    evidence_blob_input = data.evidence_blob or {}
    legacy_refs = evidence_blob_input.get("search_demand_evidence_ids")
    claim_raw_refs = (
        data.claim_evidence_refs
        or evidence_blob_input.get("claim_evidence_refs")
        or legacy_refs
        or []
    )
    demand_raw_refs = (
        data.market_demand_evidence_refs
        or evidence_blob_input.get("market_demand_evidence_refs")
        or legacy_refs
        or []
    )
    claim_evidence_refs, claim_evidence_ids, claim_issues = _resolve_authority_evidence_refs(
        session,
        raw_refs=claim_raw_refs,
        slot=slot,
        expected_purpose="CLAIM_SOURCE",
    )
    market_demand_evidence_refs, market_demand_evidence_ids, demand_issues = (
        _resolve_authority_evidence_refs(
            session,
            raw_refs=demand_raw_refs,
            slot=slot,
            expected_purpose="MARKET_DEMAND",
        )
    )
    target_market = target_digest.primary_market.upper()
    market_scope = sorted(
        {
            str(item["geo"]).upper()
            for item in market_demand_evidence_refs
            if item.get("geo")
        }
    )
    demand_score: Decimal | None = None
    market_fit_score: Decimal | None = None
    market_fit_threshold = Decimal("0.75")
    reasons: list[str] = ["CLAIM_EVIDENCE_AUTHORITY_PASS"] if claim_evidence_refs else ["CLAIM_EVIDENCE_AUTHORITY_MISSING"]
    confidence = "LOW"
    decision = "BLOCK"
    demand_authority_type = "NONE"
    demand_state = "BLOCK"

    if claim_issues:
        reasons.extend(claim_issues)
    elif not claim_evidence_refs:
        reasons.append("CLAIM_EVIDENCE_AUTHORITY_MISSING")
    elif demand_issues:
        reasons.extend(demand_issues)
    elif market_demand_evidence_refs:
        demand_input = data.model_copy(
            update={
                "demand_score": None,
                "channel_fit_score": None,
                "policy_fit_state": "PASS",
                "evidence_blob": {"search_led": True},
            }
        )
        decision, scored_reasons, confidence, demand_score = _evaluate_preflight(
            demand_input,
            market_demand_evidence_refs,
        )
        reasons.extend(scored_reasons)
        demand_authority_type = "QUANTITATIVE_DEMAND"
        market_fit_score = (
            min(Decimal("1"), demand_score / Decimal("100"))
            if demand_score is not None
            else None
        )
        if target_market not in market_scope:
            decision = "BLOCK"
            reasons.append("MARKET_DEMAND_SCOPE_MISSING")
            confidence = "LOW"
        elif market_fit_score is None or market_fit_score < market_fit_threshold:
            decision = "BLOCK"
            reasons.append("TOPIC_MARKET_DEMAND_WEAK")
            confidence = "LOW"
        elif decision == "PASS":
            demand_state = "PASS"
            reasons.append("STRICT_LONG_FORM_PREFLIGHT_PASS")
    else:
        experiment = _first_launch_experiment_authority(
            session,
            candidate=candidate,
            slot=slot,
            snapshot=snapshot,
            target_digest=target_digest,
            claim_evidence_refs=claim_evidence_refs,
        )
        if experiment["authorized"]:
            decision = "PASS"
            confidence = "HIGH"
            demand_authority_type = "FIRST_LAUNCH_EXPERIMENT"
            demand_state = "EXPERIMENT_AUTHORIZED"
            # Deliberately do not fabricate either score.  This is a bounded
            # launch experiment, not proof of steady-state market demand.
            demand_score = None
            market_fit_score = None
            reasons.extend(experiment["reason_codes"])
            reasons.append("STRICT_LONG_FORM_PREFLIGHT_PASS")
        else:
            reasons.extend(experiment["reason_codes"])
            if not any(code == "OFFICIAL_DOCUMENT_NOT_DEMAND_METRIC" for code in reasons):
                reasons.append("MARKET_DEMAND_AUTHORITY_MISSING")

    niche_ref = niche_digest.editorial_slot_ref + "#niche_contract_digest"
    target_ref = target_market_digest_ref_from_digest(target_digest)
    evidence_blob = {
        "schema_version": IDEA_MARKET_PREFLIGHT_VERSION,
        "authority_source": "PERSISTED_LONG_FORM_SLOT",
        "canonical_authority_verified": True,
        "editorial_calendar_slot_id": str(slot.id),
        "policy_snapshot_id": str(snapshot.id),
        "policy_snapshot_hash": snapshot.content_hash,
        "category_id": str(category.id),
        "category_hash": category.content_hash,
        "preferred_series_plan_id": (
            str(preferred_plan.id) if preferred_plan is not None else None
        ),
        "slot_validation": current_validation.model_dump(mode="json"),
        "slot_validation_hash": current_validation.content_hash,
        "niche_contract_digest_ref": niche_ref,
        "niche_contract_digest_hash": niche_digest.content_hash,
        "target_market_digest_ref": target_ref,
        "target_market_digest_hash": target_digest.content_hash,
        # ``search_demand_evidence_ids`` stays as a read-only compatibility
        # projection; active consumers must use the explicit collections.
        "search_demand_evidence_ids": sorted(
            {str(item) for item in [*claim_evidence_ids, *market_demand_evidence_ids]}
        ),
        "claim_evidence_refs": claim_evidence_refs,
        "market_demand_evidence_refs": market_demand_evidence_refs,
        "demand_authority_type": demand_authority_type,
        "demand_state": demand_state,
        "evidence_refs": market_demand_evidence_refs,
        "market_scope": market_scope,
        "market_fit_score": (
            float(market_fit_score) if market_fit_score is not None else None
        ),
        "market_fit_threshold": float(market_fit_threshold),
        "caller_fields_ignored": [
            "demand_score",
            "channel_fit_score",
            "policy_fit_state",
            "niche_contract_digest_ref",
            "niche_contract_digest_hash",
            "target_market_digest_ref",
            "target_market_digest_hash",
            "editorial_slot_ref",
            "content_category_ref",
            "target_market",
            "market_scope",
            "market_fit_score",
            "market_fit_threshold",
            "evidence_blob.evidence_refs",
            "evidence_blob.search_led",
        ],
    }
    return {
        "decision": decision,
        "reason_codes": list(dict.fromkeys(reasons)),
        "confidence": "HIGH" if decision == "PASS" else confidence,
        "demand_score": demand_score,
        "channel_fit_score": Decimal("1"),
        "policy_fit_state": "PASS",
        "niche_contract_digest_ref": niche_ref,
        "niche_contract_digest_hash": niche_digest.content_hash,
        "target_market_digest_ref": target_ref,
        "target_market_digest_hash": target_digest.content_hash,
        "editorial_slot_ref": niche_digest.editorial_slot_ref,
        "content_category_ref": niche_digest.category_ref,
        "target_market": target_market,
        "market_scope": market_scope,
        "market_fit_score": market_fit_score,
        "market_fit_threshold": market_fit_threshold,
        "evidence_blob": evidence_blob,
    }


def _infer_evidence_authority_purpose(source_type: str) -> str:
    """Conservative interpretation for old rows without the new column."""

    if source_type in CLAIM_SOURCE_TYPES:
        return "CLAIM_SOURCE"
    if source_type in QUANTITATIVE_DEMAND_SOURCES:
        return "MARKET_DEMAND"
    return "HISTORICAL_UNCLASSIFIED"


def _authority_ref(evidence: SearchDemandEvidence) -> dict[str, Any]:
    return {
        "type": "search_demand_evidence",
        "id": str(evidence.id),
        "evidence_source_type": evidence.evidence_source_type,
        "authority_purpose": (
            evidence.authority_purpose
            or _infer_evidence_authority_purpose(evidence.evidence_source_type)
        ),
        "query": evidence.query,
        "platform": evidence.platform,
        "geo": evidence.geo,
        "search_volume_30d": evidence.search_volume_30d,
        "relative_interest_index": (
            str(evidence.relative_interest_index)
            if evidence.relative_interest_index is not None
            else None
        ),
        "competition_index": (
            str(evidence.competition_index)
            if evidence.competition_index is not None
            else None
        ),
        "confidence": evidence.evidence_confidence,
        "captured_at": (
            evidence.captured_at.isoformat() if evidence.captured_at is not None else None
        ),
    }


def _raw_evidence_id(value: Any) -> uuid.UUID:
    raw_id = value.get("id") if isinstance(value, dict) else value
    try:
        return uuid.UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_EVIDENCE_ID_INVALID") from exc


def _resolve_authority_evidence_refs(
    session: Session,
    *,
    raw_refs: Any,
    slot: EditorialCalendarSlot,
    expected_purpose: str,
) -> tuple[list[dict[str, Any]], list[uuid.UUID], list[str]]:
    """Reload persisted refs and reject cross-purpose authority laundering."""

    if not isinstance(raw_refs, list):
        raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_EVIDENCE_REFS_INVALID")
    refs: list[dict[str, Any]] = []
    ids: list[uuid.UUID] = []
    issues: list[str] = []
    seen: set[uuid.UUID] = set()
    for raw_ref in raw_refs:
        evidence_id = _raw_evidence_id(raw_ref)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        evidence = session.get(SearchDemandEvidence, evidence_id)
        if evidence is None:
            raise NotFoundError(f"search demand evidence not found: {evidence_id}")
        if (
            evidence.company_id != slot.company_id
            or evidence.channel_workspace_id != slot.channel_workspace_id
        ):
            raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_EVIDENCE_SCOPE_MISMATCH")
        if evidence.evidence_source_type not in SAFE_SEARCH_SOURCES:
            raise ValidationFailureError("V2_LONG_FORM_PREFLIGHT_EVIDENCE_SOURCE_FORBIDDEN")
        purpose = evidence.authority_purpose or _infer_evidence_authority_purpose(
            evidence.evidence_source_type
        )
        if expected_purpose == "CLAIM_SOURCE":
            if purpose != "CLAIM_SOURCE":
                issues.append("CLAIM_EVIDENCE_AUTHORITY_INVALID")
                continue
        elif expected_purpose == "MARKET_DEMAND":
            if evidence.evidence_source_type in CLAIM_SOURCE_TYPES:
                issues.append("OFFICIAL_DOCUMENT_NOT_DEMAND_METRIC")
                continue
            if (
                purpose != "MARKET_DEMAND"
                or evidence.evidence_source_type not in QUANTITATIVE_DEMAND_SOURCES
                or (
                    evidence.search_volume_30d is None
                    and evidence.relative_interest_index is None
                )
            ):
                issues.append("MARKET_DEMAND_AUTHORITY_INVALID")
                continue
        else:  # pragma: no cover - private call sites are fixed literals.
            raise AssertionError(f"unsupported authority purpose: {expected_purpose}")
        refs.append(_authority_ref(evidence))
        ids.append(evidence.id)
    return refs, ids, list(dict.fromkeys(issues))


def _first_launch_experiment_authority(
    session: Session,
    *,
    candidate: Any,
    slot: EditorialCalendarSlot,
    snapshot: CompiledChannelPolicySnapshot,
    target_digest: TargetMarketDigest,
    claim_evidence_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the only scoreless exception to steady-state demand proof."""

    def failure(*codes: str) -> dict[str, Any]:
        return {"authorized": False, "reason_codes": list(codes)}
    if candidate is None or not claim_evidence_refs:
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    if candidate.experiment_phase != "AUDIENCE_PROMISE" or str(candidate.strategic_intent) != "ACQUISITION":
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    if not candidate.active_launch_policy_version_id or not candidate.active_launch_run_id:
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    policy = session.get(
        FirstChannelLaunchPolicyVersion, candidate.active_launch_policy_version_id
    )
    run = session.get(LaunchRun, candidate.active_launch_run_id)
    if (
        policy is None
        or run is None
        or policy.state != "APPROVED"
        or run.state != "ACTIVE"
        or run.launch_policy_version_id != policy.id
        or policy.company_id != slot.company_id
        or policy.channel_workspace_id != slot.channel_workspace_id
        or policy.policy_snapshot_id != snapshot.id
        or candidate.active_launch_policy_hash != policy.canonical_hash
        or candidate.active_launch_run_hash
        != launch_run_authority_hash(launch_policy=policy, launch_run=run)
    ):
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    published_count = int(
        session.scalar(
            select(func.count(UploadedVideo.id)).where(
                UploadedVideo.channel_workspace_id == slot.channel_workspace_id,
                UploadedVideo.verification_status == "VERIFIED",
            )
        )
        or 0
    )
    if published_count >= policy.first_n_public_videos:
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    payload = snapshot.compiled_payload if isinstance(snapshot.compiled_payload, dict) else {}
    contract = payload.get("channel_contract_json") if isinstance(payload, dict) else None
    identity = contract.get("channel_identity") if isinstance(contract, dict) else None
    market = contract.get("market_locale") if isinstance(contract, dict) else None
    target = candidate.target_audience_definition or {}
    candidate_market = (
        (target.get("market_locale") or {}).get("primary_market")
        if isinstance(target, dict)
        else None
    )
    if (
        not isinstance(identity, dict)
        or candidate.audience_promise != identity.get("brand_promise")
        or not isinstance(market, dict)
        or str(candidate_market or "").upper() != target_digest.primary_market.upper()
    ):
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    claim_text = _candidate_declared_claim_text(candidate)
    if any(term in claim_text for term in ("roi", "earnings", "make money", "time-saving", "time saving")):
        return failure("FIRST_LAUNCH_EXPERIMENT_AUTHORITY_INVALID")
    return {
        "authorized": True,
        "reason_codes": ["FIRST_LAUNCH_EXPERIMENT_AUTHORIZED"],
    }


def _candidate_declared_claim_text(candidate: Any) -> str:
    """Return editorial claims, excluding immutable evidence provenance.

    Source-pack queries may explicitly prohibit phrases such as ``time-saving``.
    Those instructions are not claims made by the candidate and must not make a
    first-launch experiment fail closed.  Non-provenance rationale remains in
    scope so manually supplied editorial claims keep their validation.
    """

    rationale = candidate.rationale
    values: list[Any] = [candidate.proposed_title, candidate.proposed_angle]
    if isinstance(rationale, dict):
        values.extend(
            value
            for key, value in rationale.items()
            if key not in {"source_pack", "research_pack", "claim_evidence_map"}
        )
    else:
        values.append(rationale)

    non_claim_keys = {
        # Evidence and its provenance may quote constraints that the candidate
        # itself does not claim.
        "source_pack",
        "research_pack",
        "claim_evidence_map",
        "evidence_bindings",
        "primary_evidence_refs",
        "supporting_evidence_refs",
        # This names claims the proposal explicitly refuses to make.
        "scope_exclusions",
        "proposal_hash",
        "proposal_schema_version",
        "source_specificity_class",
        "content_mode",
        "series_binding",
    }

    def _text(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [
                part
                for key, item in value.items()
                if key not in non_claim_keys
                for part in _text(item)
            ]
        if isinstance(value, (list, tuple, set)):
            return [part for item in value for part in _text(item)]
        return []

    return " ".join(part for value in values for part in _text(value)).lower()


def _evaluate_preflight(
    data: IdeaMarketPreflightCreate, evidence_refs: list[dict[str, Any]]
) -> tuple[str, list[str], str, Decimal | None]:
    if data.policy_fit_state == "BLOCK":
        return "BLOCK", ["IDEA_BLOCKED"], "HIGH", data.demand_score
    search_led = bool(data.evidence_blob.get("search_led", True))
    if not evidence_refs and not search_led:
        return "PASS", ["SEARCH_VOLUME_UNKNOWN"], "UNKNOWN", data.demand_score
    if not evidence_refs:
        return (
            "REVIEW_REQUIRED",
            ["SEARCH_DEMAND_EVIDENCE_MISSING", "DEMAND_EVIDENCE_WEAK"],
            "LOW",
            data.demand_score,
        )
    demand_score = (
        data.demand_score
        if data.demand_score is not None
        else _score_from_evidence_refs(evidence_refs)
    )
    if demand_score is None:
        return (
            "REVIEW_REQUIRED",
            ["SEARCH_VOLUME_UNKNOWN", "DEMAND_EVIDENCE_WEAK"],
            "LOW",
            demand_score,
        )
    if demand_score < Decimal("10"):
        return "BLOCK", ["SEARCH_VOLUME_LOW", "IDEA_BLOCKED"], "MEDIUM", demand_score
    if demand_score < Decimal("30"):
        return (
            "REVIEW_REQUIRED",
            ["SEARCH_VOLUME_LOW", "DEMAND_EVIDENCE_WEAK"],
            "MEDIUM",
            demand_score,
        )
    if any(
        _decimal_or_none(ref.get("competition_index")) is not None
        and _decimal_or_none(ref.get("competition_index")) >= Decimal("0.85")
        for ref in evidence_refs
    ):
        return (
            "REVIEW_REQUIRED",
            ["COMPETITION_HIGH", "IDEA_REVIEW_REQUIRED"],
            "MEDIUM",
            demand_score,
        )
    if data.policy_fit_state == "REVIEW_REQUIRED":
        return "REVIEW_REQUIRED", ["IDEA_REVIEW_REQUIRED"], "MEDIUM", demand_score
    return "PASS", ["IDEA_ADMITTED"], "MEDIUM", demand_score


def _score_from_evidence_refs(evidence_refs: list[dict[str, Any]]) -> Decimal | None:
    scores: list[Decimal] = []
    for ref in evidence_refs:
        volume = ref.get("search_volume_30d")
        relative = _decimal_or_none(ref.get("relative_interest_index"))
        if volume is not None:
            scores.append(min(Decimal("100"), Decimal(str(volume)) / Decimal("10")))
        elif relative is not None:
            scores.append(relative)
    if not scores:
        return None
    return sum(scores, Decimal("0")) / Decimal(len(scores))


def _is_nich1_strict_snapshot(snapshot: CompiledChannelPolicySnapshot) -> bool:
    payload = (
        snapshot.compiled_payload if isinstance(snapshot.compiled_payload, dict) else {}
    )
    scoped = (
        payload.get("channel_scoped_policy")
        if isinstance(payload.get("channel_scoped_policy"), dict)
        else {}
    )
    binding = (
        scoped.get("visual_source_policy_binding")
        if isinstance(scoped.get("visual_source_policy_binding"), dict)
        else {}
    )
    return (
        binding.get("schema_version") == "ch1-flex.visual-source-policy-binding.v2"
        and scoped.get("policy_version")
        in {
            "small-team-ai.channel-policy.v2",
            "small-team-ai.channel-policy.v3",
        }
    )


def _niche_digest_from_context(
    pack: ContextPackSnapshot,
) -> tuple[NicheContractDigest, dict[str, Any]]:
    raw = (pack.pack_content or {}).get("niche_contract_digest")
    if not isinstance(raw, dict):
        raise ValidationFailureError("NICHE_CONTRACT_DIGEST_MISSING")
    try:
        digest = NicheContractDigest.model_validate(raw)
    except ValidationError as exc:
        raise ValidationFailureError("NICHE_CONTRACT_DIGEST_INVALID") from exc
    raw_ref = (pack.pack_content or {}).get("niche_contract_digest_ref")
    if not isinstance(raw_ref, dict):
        raw_ref = digest.as_ref().model_dump(mode="json")
    if raw_ref.get("content_hash") != digest.content_hash:
        raise ValidationFailureError("NICHE_CONTRACT_DIGEST_REF_HASH_MISMATCH")
    return digest, raw_ref


def _validate_nich1_editorial_context_authority(
    session: Session,
    *,
    context_pack: ContextPackSnapshot,
    snapshot: CompiledChannelPolicySnapshot,
) -> NicheContractDigest:
    """Recompile strict editorial niche truth and reject caller/row substitution."""

    if (
        context_pack.policy_snapshot_id != snapshot.id
        or context_pack.channel_workspace_id != snapshot.channel_workspace_id
        or context_pack.channel_profile_version_id
        != snapshot.channel_profile_version_id
        or context_pack.editorial_calendar_slot_id is None
    ):
        raise ValidationFailureError("NICH1_EDITORIAL_CONTEXT_SCOPE_MISMATCH")
    channel = session.get(ChannelWorkspace, snapshot.channel_workspace_id)
    profile = session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
    slot = session.get(
        EditorialCalendarSlot,
        context_pack.editorial_calendar_slot_id,
    )
    category = (
        session.get(ContentCategory, slot.category_id)
        if slot is not None and slot.category_id is not None
        else None
    )
    if any(item is None for item in (channel, profile, slot, category)):
        raise ValidationFailureError("NICH1_EDITORIAL_CONTEXT_AUTHORITY_MISSING")
    try:
        authoritative = NicheContractDigestCompiler().compile(
            channel=channel,
            profile_version=profile,
            policy_snapshot=snapshot,
            category=category,
            editorial_slot=slot,
        )
    except NicheContractCompilationError as exc:
        raise ValidationFailureError(
            f"NICH1_EDITORIAL_CONTEXT_AUTHORITY_BLOCKED:{exc}"
        ) from exc
    embedded, embedded_ref = _niche_digest_from_context(context_pack)
    expected_ref = authoritative.editorial_slot_ref + "#niche_contract_digest"
    agent_context = (context_pack.pack_content or {}).get("agent_context_pack")
    agent_context = agent_context if isinstance(agent_context, dict) else {}
    digests = agent_context.get("digests")
    digests = digests if isinstance(digests, dict) else {}
    nested_digest = digests.get("niche_contract_digest")
    runtime_guard = (context_pack.pack_content or {}).get("runtime_guard_digest")
    runtime_guard = runtime_guard if isinstance(runtime_guard, dict) else {}
    if (
        embedded.model_dump(mode="json") != authoritative.model_dump(mode="json")
        or embedded_ref.get("ref") != expected_ref
        or embedded_ref.get("content_hash") != authoritative.content_hash
        or nested_digest != authoritative.model_dump(mode="json")
        or agent_context.get("agent_key") != "EditorialIdeaResearchAgent"
        or runtime_guard.get("compiled_policy_snapshot_id") != str(snapshot.id)
        or runtime_guard.get("compiled_policy_snapshot_hash") != snapshot.content_hash
        or runtime_guard.get("provider_calls_allowed") is not False
        or runtime_guard.get("direct_provider_sdk_allowed") is not False
    ):
        raise ValidationFailureError("NICH1_EDITORIAL_CONTEXT_DIGEST_BINDING_MISMATCH")
    digest_authorities = [
        item
        for item in context_pack.policy_refs
        if isinstance(item, dict)
        and item.get("type") == "niche_contract_digest_authority"
    ]
    if len(digest_authorities) != 1 or (
        digest_authorities[0].get("compiled_policy_snapshot_id") != str(snapshot.id)
        or digest_authorities[0].get("content_hash") != authoritative.content_hash
    ):
        raise ValidationFailureError("NICH1_EDITORIAL_CONTEXT_POLICY_REF_MISMATCH")
    expected_pack_hash = _hash_payload(
        {
            "input_refs": context_pack.input_refs,
            "policy_refs": context_pack.policy_refs,
            "evidence_refs": context_pack.evidence_refs,
            "metric_refs": context_pack.metric_refs,
            "memory_refs": context_pack.memory_refs,
            "pack_content": context_pack.pack_content,
        }
    )
    if context_pack.pack_hash != expected_pack_hash:
        raise ValidationFailureError("NICH1_EDITORIAL_CONTEXT_PACK_HASH_MISMATCH")
    return authoritative


def _hash_payload(value: Any) -> str:
    jsonable = _jsonable(value)
    if not isinstance(jsonable, dict):
        jsonable = {"value": jsonable}
    return content_hash(jsonable)


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _ensure_no_secret_payload(value: Any) -> None:
    for key, item in _walk_items(value):
        normalized = key.lower().replace("-", "_")
        if normalized in SAFE_USAGE_COUNTER_KEYS and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise ValidationFailureError(
                f"usage counter must be a non-negative integer: {key}"
            )
        if (
            any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)
            and normalized != "secret_ref"
            and normalized not in SAFE_USAGE_COUNTER_KEYS
        ):
            raise ValidationFailureError(
                f"secret-like payload key is not allowed: {key}"
            )
        if isinstance(item, str) and any(
            marker in item for marker in RAW_SECRET_MARKERS
        ):
            raise ValidationFailureError("raw secret-like value is not allowed")


def _walk_items(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield str(child_key), child_value
            yield from _walk_items(child_value, str(child_key))
    elif isinstance(value, list):
        for item in value:
            yield from _walk_items(item, key)


def _record_m5_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
) -> None:
    safe_payload = _jsonable(payload)
    _ensure_no_secret_payload(safe_payload)
    envelope = EventEnvelope(
        event_type=event_type,
        event_version=1,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=correlation_id,
        payload=safe_payload,
    )
    DomainEventBus(session).append(envelope, company_id=company_id)
    audit = AuditEnvelope(
        action=event_type,
        actor_type="system" if actor_id is None else "user",
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id,
        reason_code=reason_code,
        payload=safe_payload,
    )
    AuditService(session).append(audit, company_id=company_id)
