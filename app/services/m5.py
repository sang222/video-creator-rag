# Compatibility note: semantic facades `daily_operations`, `context_resolver`, and `project_admission` re-export this implementation; phase-coded import kept for reports/tests/backward compatibility.
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
    PromptOutputValidationRequest,
    PromptRenderRequest,
)
from app.contracts.m5 import (
    AudienceTargetPackCreate,
    ChannelDailyRunCreate,
    ChannelStatePackSnapshotCreate,
    ContextPackSnapshotCreate,
    CreativeBriefDraft,
    DailyIdeaDecisionCreate,
    DailyRunExecuteRequest,
    EditorialCalendarSlotCreate,
    IdeaMarketPreflightCreate,
    MockAuthorityProposal,
    ProjectAdmissionDecisionCreate,
    ResearchPackDraft,
    RetrievalPlanSnapshotCreate,
    SourcePackDraft,
    SearchDemandEvidenceCreate,
    SearchIntentMapCreate,
)
from app.contracts.nich1 import (
    ChannelFitEvaluation,
    NicheAlignmentDossier,
    NicheContractDigest,
    NicheCriterion,
    NicheCriterionEvidence,
    NicheDossierScope,
    NicheEvidenceRef,
    NicheGateKey,
    NicheGateResult,
    NicheGateVerdict,
    NicheReasonCode,
    TopicNicheAlignmentInput,
)
from app.contracts.ops import BudgetGateCheckRequest
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate, VideoProjectCreate
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AudienceTargetPack,
    ChannelDailyRun,
    ChannelProfileVersion,
    ChannelStatePackSnapshot,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    ContextPackSnapshot,
    DailyIdeaDecision,
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
    User,
    VideoProject,
)
from app.services.audit import AuditService
from app.services.config_registry import content_hash
from app.services.domain_events import DomainEventBus
from app.services.ops import BudgetGateService
from app.services.m10_1 import LLMRouterService
from app.services.m12_1 import PromptRegistryService
from app.services.nich1 import (
    EditorialSlotValidator,
    NicheAlignmentDossierBuilder,
    NicheContractCompilationError,
    NicheContractDigestCompiler,
    TopicNicheAlignmentGate,
    channel_fit_threshold_from_compiled_policy,
    evaluate_channel_fit,
)
from app.services.r3d1 import ProjectScopeAdmissionGuard
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.r3d3 import AgentContextContractRegistry, PromptBudgetGate
from app.services.workflow import ArtifactService, VideoProjectService


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
DEFAULT_DAILY_CONTEXT_SOURCES = [
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
    "OFFICIAL_MANUAL",
    "PAID_TOOL_CSV",
    "GOOGLE_TRENDS_CSV",
    "YOUTUBE_ANALYTICS",
    "TIKTOK_CREATOR_SEARCH_INSIGHTS_MANUAL",
    "INTERNAL_ANALYTICS",
    "MANUAL_RESEARCH",
}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_")
SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
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
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        payload = data.model_dump()
        slot = EditorialCalendarSlot(**payload)
        self.session.add(slot)
        self.session.flush()
        snapshot = self.session.get(CompiledChannelPolicySnapshot, slot.policy_snapshot_id)
        if snapshot is not None and _is_nich1_strict_snapshot(snapshot):
            channel = self.session.get(ChannelWorkspace, slot.channel_workspace_id)
            profile = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
            category = self.session.get(ContentCategory, slot.category_id) if slot.category_id else None
            if channel is None or profile is None:
                raise ValidationFailureError("NICH1_SLOT_AUTHORITY_MISSING")
            validation = EditorialSlotValidator().validate(
                channel=channel,
                profile_version=profile,
                policy_snapshot=snapshot,
                channel_contract=(snapshot.compiled_payload or {}).get("channel_contract_json") or {},
                category=category,
                editorial_slot=slot,
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
                raise ValidationFailureError("NICH1_CONTEXT_POLICY_REFS_CALLER_FORBIDDEN")
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
                raise ValidationFailureError("NICH1_CONTEXT_AUTHORITY_REF_CALLER_FORBIDDEN")
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
            raise ValidationFailureError("memory refs are not allowed in M5 context packs")
        pack_content = {**generated["pack_content"], **data.pack_content}
        if metric_refs:
            pack_content["metric_truth"] = {"state": "PROVIDED_BY_SYSTEM", "metric_refs": metric_refs}
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
            _validate_nich1_daily_context_authority(
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

    def get_context_pack(self, context_pack_id: uuid.UUID) -> ContextPackSnapshot | None:
        return self.session.get(ContextPackSnapshot, context_pack_id)

    def require_context_pack(self, context_pack_id: uuid.UUID) -> ContextPackSnapshot:
        pack = self.get_context_pack(context_pack_id)
        if pack is None:
            raise NotFoundError(f"context pack not found: {context_pack_id}")
        return pack

    def _validate_plan_scope(self, data: RetrievalPlanSnapshotCreate) -> None:
        if not data.allowed_sources:
            raise ValidationFailureError("allowed_sources must be explicit")
        if data.purpose in {"DAILY_IDEA", "AUTHORITY_REVIEW", "SEARCH_DEMAND"}:
            if data.channel_workspace_id is None or data.policy_snapshot_id is None:
                raise ValidationFailureError("daily authority context requires explicit channel and policy snapshot scope")
        channel: ChannelWorkspace | None = None
        if data.channel_workspace_id is not None:
            channel = self.session.get(ChannelWorkspace, data.channel_workspace_id)
            if channel is None:
                raise NotFoundError(f"channel not found: {data.channel_workspace_id}")
            if channel.company_id != data.company_id:
                raise ValidationFailureError("channel does not belong to context company")
        if data.policy_snapshot_id is not None:
            snapshot = self.session.get(CompiledChannelPolicySnapshot, data.policy_snapshot_id)
            if snapshot is None:
                raise NotFoundError(f"policy snapshot not found: {data.policy_snapshot_id}")
            if data.channel_workspace_id is not None and snapshot.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("policy snapshot does not belong to context channel")
            if data.channel_profile_version_id is None:
                data.channel_profile_version_id = snapshot.channel_profile_version_id
        if data.channel_profile_version_id is not None:
            profile = self.session.get(ChannelProfileVersion, data.channel_profile_version_id)
            if profile is None:
                raise NotFoundError(f"channel profile version not found: {data.channel_profile_version_id}")
            if data.channel_workspace_id is not None and profile.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("profile version does not belong to context channel")
        if data.video_project_id is not None:
            project = self.session.get(VideoProject, data.video_project_id)
            if project is None:
                raise NotFoundError(f"project not found: {data.video_project_id}")
            if project.company_id != data.company_id:
                raise ValidationFailureError("project does not belong to context company")
            if data.channel_workspace_id is not None and project.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("project does not belong to context channel")
            if data.policy_snapshot_id is not None and project.policy_snapshot_id != data.policy_snapshot_id:
                raise ValidationFailureError("project policy snapshot does not match context policy snapshot")
        if data.editorial_calendar_slot_id is not None:
            slot = self.session.get(EditorialCalendarSlot, data.editorial_calendar_slot_id)
            if slot is None:
                raise NotFoundError(f"editorial slot not found: {data.editorial_calendar_slot_id}")
            if slot.company_id != data.company_id:
                raise ValidationFailureError("slot does not belong to context company")
            if data.channel_workspace_id is not None and slot.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("slot does not belong to context channel")
            if data.policy_snapshot_id is not None and slot.policy_snapshot_id != data.policy_snapshot_id:
                raise ValidationFailureError("slot policy snapshot does not match context policy snapshot")
        if channel is None:
            _require_company(self.session, data.company_id)

    def _build_scoped_pack_content(self, plan: RetrievalPlanSnapshot) -> dict[str, Any]:
        allowed = set(plan.allowed_sources)
        input_refs: list[dict[str, Any]] = []
        policy_refs: list[dict[str, Any]] = []
        evidence_refs: list[dict[str, Any]] = []
        pack_content: dict[str, Any] = {"scope": _plan_scope(plan), "numeric_truth_contract": "SQL_OR_UNKNOWN"}
        if "channel_profile" in allowed and plan.channel_workspace_id is not None:
            channel = self.session.get(ChannelWorkspace, plan.channel_workspace_id)
            profile = self.session.get(ChannelProfileVersion, plan.channel_profile_version_id) if plan.channel_profile_version_id else None
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
                input_refs.append({"type": "channel_profile_version", "id": str(profile.id)})
        if "policy_snapshot" in allowed and plan.policy_snapshot_id is not None:
            snapshot = self.session.get(CompiledChannelPolicySnapshot, plan.policy_snapshot_id)
            if snapshot is not None:
                pack_content["policy_snapshot"] = {
                    "id": str(snapshot.id),
                    "status": snapshot.status,
                    "content_hash": snapshot.content_hash,
                    "compiler_version": snapshot.compiler_version,
                    "channel_profile_version_id": str(snapshot.channel_profile_version_id),
                }
                policy_refs.append({"type": "compiled_channel_policy_snapshot", "id": str(snapshot.id), "content_hash": snapshot.content_hash})
        if "editorial_slot" in allowed and plan.editorial_calendar_slot_id is not None:
            slot = self.session.get(EditorialCalendarSlot, plan.editorial_calendar_slot_id)
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
                input_refs.append({"type": "editorial_calendar_slot", "id": str(slot.id)})
        if (
            "niche_contract_digest" in allowed
            and plan.channel_workspace_id is not None
            and plan.channel_profile_version_id is not None
            and plan.policy_snapshot_id is not None
            and plan.editorial_calendar_slot_id is not None
        ):
            channel = self.session.get(ChannelWorkspace, plan.channel_workspace_id)
            profile = self.session.get(ChannelProfileVersion, plan.channel_profile_version_id)
            snapshot = self.session.get(CompiledChannelPolicySnapshot, plan.policy_snapshot_id)
            slot = self.session.get(EditorialCalendarSlot, plan.editorial_calendar_slot_id)
            category = self.session.get(ContentCategory, slot.category_id) if slot and slot.category_id else None
            if snapshot is not None and _is_nich1_strict_snapshot(snapshot):
                if any(item is None for item in (channel, profile, slot, category)):
                    raise ValidationFailureError("NICH1_DAILY_CONTEXT_AUTHORITY_MISSING")
                try:
                    digest = NicheContractDigestCompiler().compile(
                        channel=channel,
                        profile_version=profile,
                        policy_snapshot=snapshot,
                        category=category,
                        editorial_slot=slot,
                    )
                except NicheContractCompilationError as exc:
                    raise ValidationFailureError(f"NICH1_DAILY_CONTEXT_BLOCKED:{exc}") from exc
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
                    _search_evidence_refs(self.session, plan.company_id, plan.channel_workspace_id)
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
                    "DailyIdeaAgent", task_type="daily_idea", lane="cheap_structured"
                )
                budget = PromptBudgetGate().apply(
                    contract=contract,
                    sections=sections,
                    initial_omitted=[],
                )
                if budget.status != "OK":
                    raise ValidationFailureError(
                        "NICH1_DAILY_CONTEXT_BUDGET_BLOCKED:" + ",".join(budget.reason_codes)
                    )
                pack_content.update(budget.sections)
                pack_content["niche_contract_digest_ref"] = digest_ref
                pack_content["prompt_budget_report"] = budget.budget_report
                pack_content["agent_context_pack"] = {
                    "schema_version": "r3d3.agent-context-pack.v1",
                    "agent_key": "DailyIdeaAgent",
                    "agent_context_contract": contract.to_dict(),
                    "digests": budget.sections,
                    "prompt_budget_report": budget.budget_report,
                    "omitted_items": budget.omitted_items,
                }
                input_refs.extend(
                    [
                        {"type": "content_category", "id": str(category.id), "content_hash": category.content_hash},
                        {"type": "editorial_calendar_slot", "id": str(slot.id), "content_hash": digest.editorial_slot_hash},
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
            pending_reviews = _pending_reviews(self.session, plan.company_id, plan.channel_workspace_id)
            pack_content["pending_reviews"] = pending_reviews
            input_refs.extend({"type": "review_task", "id": item["id"]} for item in pending_reviews[:20])
        if "gate_runs" in allowed:
            gate_summary = _gate_summary(self.session, plan.company_id, plan.channel_workspace_id)
            pack_content["gate_summary"] = gate_summary
        if "provider_health" in allowed:
            pack_content["provider_health"] = _provider_health_summary(self.session)
        if "quota_ledger" in allowed:
            pack_content["quota_summary"] = _quota_summary(self.session)
        if "search_demand_evidence" in allowed and plan.channel_workspace_id is not None:
            evidence = _search_evidence_refs(self.session, plan.company_id, plan.channel_workspace_id)
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
            pack = ResourceResolverService(self.session).require_context_pack(data.context_pack_snapshot_id)
            if pack.company_id != data.company_id or pack.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("context pack scope does not match channel state scope")
        active_project_refs = _active_project_refs(self.session, data.company_id, data.channel_workspace_id)
        pending_review_refs = _pending_reviews(self.session, data.company_id, data.channel_workspace_id)
        readiness_summary = _readiness_summary(self.session, data.company_id, data.channel_workspace_id)
        provider_health_summary = _provider_health_summary(self.session)
        quota_summary = _quota_summary(self.session)
        evidence_summary = {
            "search_demand_evidence_count": self.session.scalar(
                select(func.count())
                .select_from(SearchDemandEvidence)
                .where(SearchDemandEvidence.company_id == data.company_id)
                .where(SearchDemandEvidence.channel_workspace_id == data.channel_workspace_id)
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
            channel_daily_run_id=data.channel_daily_run_id,
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
            freshness_state="UNKNOWN" if provider_health_summary["llm_router"]["state"] == "UNKNOWN" else "FRESH",
            confidence_level="UNKNOWN" if evidence_summary["search_demand_evidence_count"] == 0 else "MEDIUM",
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
        metadata = payload.pop("metadata")
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

    def create_map(self, *, data: SearchIntentMapCreate, correlation_id: str = "m5-search-intent-map") -> SearchIntentMap:
        _require_channel_for_company(self.session, data.company_id, data.channel_workspace_id)
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
            payload={"demand_confidence": item.demand_confidence, "source_evidence_refs": item.source_evidence_refs},
        )
        return item


class AudienceTargetService:
    def __init__(self, session: Session):
        self.session = session

    def create_pack(self, *, data: AudienceTargetPackCreate, correlation_id: str = "m5-audience-target-pack") -> AudienceTargetPack:
        _require_channel_for_company(self.session, data.company_id, data.channel_workspace_id)
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
            payload={"confidence_level": item.confidence_level, "evidence_refs": item.evidence_refs},
        )
        return item


class IdeaMarketPreflightService:
    def __init__(self, session: Session):
        self.session = session

    def create_preflight(
        self,
        *,
        data: IdeaMarketPreflightCreate,
        correlation_id: str = "m5-idea-market-preflight",
    ) -> IdeaMarketPreflight:
        _require_channel_for_company(self.session, data.company_id, data.channel_workspace_id)
        evidence_refs = _resolve_preflight_evidence_refs(self.session, data)
        strict_result = _evaluate_nich1_preflight(self.session, data=data, evidence_refs=evidence_refs)
        if strict_result is None:
            decision, reasons, confidence, demand_score = _evaluate_preflight(data, evidence_refs)
            channel_fit_score = data.channel_fit_score
            policy_fit_state = data.policy_fit_state
            evidence_blob = {**data.evidence_blob, "evidence_refs": evidence_refs}
        else:
            decision = strict_result["decision"]
            reasons = strict_result["reason_codes"]
            confidence = strict_result["confidence"]
            demand_score = strict_result["demand_score"]
            channel_fit_score = Decimal(str(strict_result["channel_fit_score"]))
            policy_fit_state = strict_result["policy_fit_state"]
            evidence_blob = {
                **data.evidence_blob,
                "evidence_refs": evidence_refs,
                "niche_contract_digest_ref": strict_result["niche_contract_digest_ref"],
                "topic_niche_alignment_gate": strict_result["topic_gate"],
                "channel_fit_gate": strict_result["channel_fit_gate"],
                "niche_alignment_dossier": strict_result["niche_alignment_dossier"],
                "channel_fit_score": strict_result["channel_fit_score"],
                "channel_fit_threshold": strict_result["channel_fit_threshold"],
                "channel_fit_result": strict_result["channel_fit_result"],
                "caller_policy_fit_state_ignored": data.policy_fit_state,
            }
        if data.target_market is not None:
            evidence_blob.update(
                {
                    "niche_contract_digest_ref": data.niche_contract_digest_ref,
                    "niche_contract_digest_hash": data.niche_contract_digest_hash,
                    "target_market_digest_ref": data.target_market_digest_ref,
                    "target_market_digest_hash": data.target_market_digest_hash,
                    "editorial_slot_ref": data.editorial_slot_ref,
                    "content_category_ref": data.content_category_ref,
                    "target_market": data.target_market,
                    "market_scope": sorted({item.upper() for item in data.market_scope}),
                    "market_fit_score": (
                        float(data.market_fit_score)
                        if data.market_fit_score is not None
                        else None
                    ),
                    "market_fit_threshold": (
                        float(data.market_fit_threshold)
                        if data.market_fit_threshold is not None
                        else None
                    ),
                }
            )
            missing_market_authority = [
                key
                for key, value in {
                    "TARGET_MARKET_DIGEST": data.target_market_digest_ref
                    and data.target_market_digest_hash,
                    "NICHE_CONTRACT_DIGEST": data.niche_contract_digest_ref
                    and data.niche_contract_digest_hash,
                    "EDITORIAL_SLOT": data.editorial_slot_ref,
                    "CONTENT_CATEGORY": data.content_category_ref,
                }.items()
                if not value
            ]
            target_scoped = data.target_market in {
                item.upper() for item in data.market_scope
            }
            global_only = "GLOBAL" in {
                item.upper() for item in data.market_scope
            } and not target_scoped
            if missing_market_authority:
                decision = "BLOCK"
                reasons = sorted(
                    set([*reasons, "TARGET_MARKET_PROFILE_MISSING"])
                )
                confidence = "LOW"
            elif not target_scoped or global_only:
                if decision != "BLOCK":
                    decision = "REVIEW_REQUIRED"
                reasons = sorted(
                    set([*reasons, "MARKET_DEMAND_SCOPE_MISSING"])
                )
                confidence = "LOW"
            elif (
                data.market_fit_score is None
                or data.market_fit_threshold is None
                or data.market_fit_score < data.market_fit_threshold
            ):
                if decision == "PASS":
                    decision = "REVIEW_REQUIRED"
                reasons = sorted(
                    set([*reasons, "TOPIC_MARKET_DEMAND_WEAK"])
                )
        item = IdeaMarketPreflight(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            channel_daily_run_id=data.channel_daily_run_id,
            daily_idea_decision_id=data.daily_idea_decision_id,
            search_intent_map_id=data.search_intent_map_id,
            audience_target_pack_id=data.audience_target_pack_id,
            demand_score=demand_score,
            channel_fit_score=channel_fit_score,
            policy_fit_state=policy_fit_state,
            confidence_state=confidence,
            evidence_blob=evidence_blob,
            reason_codes=reasons,
            decision=decision,
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
            reason_code=item.reason_codes[0] if item.reason_codes else "CONTEXT_PACK_CREATED",
            payload={
                "decision": item.decision,
                "reason_codes": item.reason_codes,
                "evidence_refs": evidence_refs,
            },
        )
        return item


class LLMWorkflowService:
    def __init__(
        self,
        session: Session,
        *,
        llm_router: Any | None = None,
        prompt_registry: PromptRegistryService | None = None,
    ):
        self.session = session
        self.llm_router = llm_router or LLMRouterService(session)
        self.prompt_registry = prompt_registry or PromptRegistryService(session)

    def run_authority(
        self,
        *,
        daily_run: ChannelDailyRun,
        context_pack: ContextPackSnapshot,
        provider_key: str,
        quota_account_id: uuid.UUID | None,
        budget_policy_key: str | None,
        estimated_cost: Decimal,
        correlation_id: str,
    ) -> LLMWorkflowResult:
        if provider_key.startswith("mock_"):
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key="llm_router",
                reason_codes=["TEST_DOUBLE_RUNTIME_REMOVED", "HUMAN_ACTION_REQUIRED"],
                message="Runtime mock providers were removed from production. Configure a real LLM provider.",
                correlation_id=correlation_id,
            )
        if not _authority_source_refs(context_pack):
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key=provider_key,
                reason_codes=["AUTHORITY_CONTEXT_INSUFFICIENT", "AUTHORITY_IDEA_SOURCE_MISSING"],
                message="M5 cannot create a daily idea without real authority/context inputs.",
                correlation_id=correlation_id,
            )
        snapshot = self.session.get(CompiledChannelPolicySnapshot, daily_run.policy_snapshot_id)
        if snapshot is None:
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key=provider_key,
                reason_codes=["POLICY_SNAPSHOT_MISSING"],
                message="The frozen policy snapshot is missing.",
                correlation_id=correlation_id,
            )
        if _is_nich1_strict_snapshot(snapshot):
            try:
                _validate_nich1_daily_context_authority(
                    self.session,
                    context_pack=context_pack,
                    snapshot=snapshot,
                )
            except ValidationFailureError:
                return self._blocked_run(
                    daily_run=daily_run,
                    context_pack=context_pack,
                    provider_key=provider_key,
                    reason_codes=["NICH1_DAILY_CONTEXT_AUTHORITY_INVALID"],
                    message="The strict daily niche context failed authoritative revalidation.",
                    correlation_id=correlation_id,
                )
        if getattr(daily_run, "run_mode", "REAL") == "MOCK":
            fixture_mode = str((daily_run.metadata_ or {}).get("fixture_mode") or "success")
            if fixture_mode == "unavailable":
                return self._blocked_run(
                    daily_run=daily_run,
                    context_pack=context_pack,
                    provider_key="offline_daily_fixture",
                    reason_codes=["OFFLINE_DAILY_FIXTURE_UNAVAILABLE"],
                    message="The explicitly selected offline Daily fixture is unavailable.",
                    correlation_id=correlation_id,
                )
            try:
                proposal = _proposal_from_context(context_pack, None)
                proposal.update(
                    {
                        "channel_fit_score": Decimal("0.80"),
                        "channel_fit_evidence": {"source": "OFFLINE_DETERMINISTIC_FIXTURE"},
                    }
                )
                if fixture_mode == "malformed":
                    proposal.pop("proposed_title", None)
                proposal = _validated_authority_proposal(proposal)
            except (ValidationError, ValidationFailureError, ValueError):
                llm_run = self._create_llm_run(
                    daily_run=daily_run,
                    context_pack=context_pack,
                    provider_key="offline_daily_fixture",
                    status="FAILED",
                    output_payload={"error": "offline fixture schema validation failed"},
                    quota_event_id=None,
                    cost_event_id=None,
                    correlation_id=correlation_id,
                    run_mode="MOCK",
                )
                return LLMWorkflowResult(
                    terminal_status="FAILED",
                    reason_codes=["LLM_OUTPUT_MALFORMED", "LLM_SCHEMA_VALIDATION_FAILED"],
                    llm_run=llm_run,
                    proposal=None,
                    provider_attempt=None,
                    quota_event_id=None,
                    cost_event_id=None,
                    budget_gate_result={"decision": "PASS", "source": "OFFLINE_FIXTURE"},
                )
            llm_run = self._create_llm_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key="offline_daily_fixture",
                status="COMPLETED",
                output_payload=proposal,
                quota_event_id=None,
                cost_event_id=None,
                correlation_id=correlation_id,
                run_mode="MOCK",
            )
            return LLMWorkflowResult(
                terminal_status="COMPLETED",
                reason_codes=["OFFLINE_DAILY_FIXTURE_COMPLETED"],
                llm_run=llm_run,
                proposal=proposal,
                provider_attempt=None,
                quota_event_id=None,
                cost_event_id=None,
                budget_gate_result={"decision": "PASS", "source": "OFFLINE_FIXTURE"},
            )
        scoped = (snapshot.compiled_payload or {}).get("channel_scoped_policy") or {}
        compiled_budget = scoped.get("budget_policy") or {}
        raw_cap = compiled_budget.get("max_estimated_cost_per_video")
        try:
            compiled_cap = Decimal(str(raw_cap))
        except (ArithmeticError, TypeError, ValueError):
            compiled_cap = Decimal("-1")
        if not compiled_cap.is_finite() or compiled_cap < 0:
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key=provider_key,
                reason_codes=["COST_BUDGET_POLICY_REQUIRED", "HUMAN_ACTION_REQUIRED"],
                message="The frozen channel policy has no usable LLM cost firewall envelope.",
                correlation_id=correlation_id,
            )
        compiled_envelope = {
            "decision": "PASS",
            "reason_codes": ["COMPILED_COST_ENVELOPE_PASS"],
            "source": "compiled_channel_policy_snapshot",
            "estimated_cost": str(estimated_cost),
            "max_estimated_cost_per_video": str(compiled_cap),
            "compiled_policy_snapshot_id": str(snapshot.id),
            "compiled_policy_snapshot_hash": snapshot.content_hash,
        }
        if estimated_cost < 0 or estimated_cost > compiled_cap:
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key=provider_key,
                reason_codes=["COST_BUDGET_BLOCKED", "ESTIMATED_COST_EXCEEDS_FROZEN_CAP"],
                message="The frozen channel cost envelope blocked M5 daily authority.",
                correlation_id=correlation_id,
                budget_gate_result={**compiled_envelope, "decision": "BLOCK"},
            )
        budget_result: dict[str, Any] = compiled_envelope
        if budget_policy_key is not None:
            check = BudgetGateService(self.session).check(
                data=BudgetGateCheckRequest(
                    policy_key=budget_policy_key,
                    provider_key=provider_key,
                    scope_type="CHANNEL",
                    scope_id=daily_run.channel_workspace_id,
                    estimated_cost=estimated_cost,
                    quota_account_id=quota_account_id,
                    quota_amount=Decimal("1") if quota_account_id else None,
                    unit="REQUESTS" if quota_account_id else None,
                ),
                correlation_id="m5-llm-budget-gate",
            )
            budget_result = {
                **check.model_dump(mode="json"),
                "compiled_cost_envelope": compiled_envelope,
            }
            if check.decision == "BLOCK":
                return self._blocked_run(
                    daily_run=daily_run,
                    context_pack=context_pack,
                    provider_key=provider_key,
                    reason_codes=["COST_BUDGET_BLOCKED", *check.reason_codes],
                    message="Budget gate blocked M5 real-provider authority.",
                    correlation_id=correlation_id,
                    budget_gate_result=budget_result,
                )
            if check.decision == "REVIEW_REQUIRED":
                return self._blocked_run(
                    daily_run=daily_run,
                    context_pack=context_pack,
                    provider_key=provider_key,
                    reason_codes=["BUDGET_REVIEW_REQUIRED", *check.reason_codes],
                    message="Budget gate requires review before M5 real-provider authority.",
                    correlation_id=correlation_id,
                    budget_gate_result=budget_result,
                )
        if provider_key != "llm_router":
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key="llm_router",
                reason_codes=["LLM_ROUTE_NOT_APPROVED", "HUMAN_ACTION_REQUIRED"],
                message="M5 daily execution must use the registered LLMRouter route.",
                correlation_id=correlation_id,
                budget_gate_result=budget_result,
            )

        channel_contract = (snapshot.compiled_payload or {}).get("channel_contract_json") or {}
        task_payload = {
            "daily_context_pack": context_pack.pack_content,
            "daily_context_pack_ref": {
                "id": str(context_pack.id),
                "content_hash": context_pack.pack_hash,
            },
            "agent_context_pack": {
                **((context_pack.pack_content or {}).get("agent_context_pack") or {}),
                "context_pack_version": "nich1.daily-agent-context.v1",
                "agent_key": "DailyIdeaAgent",
                "context_pack_hash": context_pack.pack_hash,
            },
        }
        render = self.prompt_registry.render_prompt(
            PromptRenderRequest(
                agent_key="DailyIdeaAgent",
                router_lane="cheap_structured",
                task_payload=task_payload,
                channel_profile_version_id=snapshot.channel_profile_version_id,
                compiled_policy_snapshot_id=snapshot.id,
                channel_contract_json=channel_contract,
                compiled_policy_snapshot_json=snapshot.compiled_payload,
                market_locale_context_json=channel_contract.get("market_locale"),
                evidence_refs=context_pack.evidence_refs,
                artifact_refs=context_pack.input_refs,
                input_payload_ref=f"daily-context-pack:{context_pack.id}",
            )
        )
        if render.status != "OK":
            return self._blocked_run(
                daily_run=daily_run,
                context_pack=context_pack,
                provider_key=provider_key,
                reason_codes=[*render.reason_codes, "DAILY_PROMPT_NOT_READY"],
                message="DailyIdeaAgent prompt/context readiness failed.",
                correlation_id=correlation_id,
                budget_gate_result=budget_result,
            )
        route = self.llm_router.route(
            lane_name=render.router_lane,
            messages=[message.model_dump() for message in render.rendered_messages],
            requested_task_type="daily_idea",
            response_format="json",
            correlation_id=correlation_id,
        )
        llm_run = (
            self.session.get(LLMRunSnapshot, route.llm_run_snapshot_id)
            if route.llm_run_snapshot_id is not None
            else None
        )
        if route.status != "SUCCESS":
            return LLMWorkflowResult(
                terminal_status="BLOCKED" if route.status in {"SKIPPED", "BLOCKED"} else "FAILED",
                reason_codes=[
                    *(route.reason_codes or []),
                    "LLM_PROVIDER_NOT_CONFIGURED" if route.status == "SKIPPED" else "DAILY_LLM_ROUTE_FAILED",
                ],
                llm_run=llm_run,
                proposal=None,
                provider_attempt=(
                    self.session.get(ProviderAttempt, route.provider_attempt_id)
                    if route.provider_attempt_id is not None
                    else None
                ),
                quota_event_id=None,
                cost_event_id=None,
                budget_gate_result=budget_result,
            )
        if llm_run is None:
            return LLMWorkflowResult(
                terminal_status="FAILED",
                reason_codes=[
                    *(route.reason_codes or []),
                    "LLM_RUN_SNAPSHOT_REQUIRED",
                ],
                llm_run=None,
                proposal=None,
                provider_attempt=(
                    self.session.get(ProviderAttempt, route.provider_attempt_id)
                    if route.provider_attempt_id is not None
                    else None
                ),
                quota_event_id=None,
                cost_event_id=None,
                budget_gate_result=budget_result,
            )
        validation = self.prompt_registry.validate_output(
            PromptOutputValidationRequest(
                agent_key="DailyIdeaAgent",
                raw_output=route.structured_output or route.content or "",
                prompt_render_run_id=render.prompt_render_run_id,
            )
        )
        if validation.status != "OK":
            review_status = validation.status in {"REVIEW_REQUIRED", "BLOCK"}
            return LLMWorkflowResult(
                terminal_status="BLOCKED" if review_status else "FAILED",
                reason_codes=[
                    *validation.reason_codes,
                    (
                        "DAILY_IDEA_REVIEW_REQUIRED"
                        if validation.status == "REVIEW_REQUIRED"
                        else "DAILY_IDEA_BLOCKED"
                        if validation.status == "BLOCK"
                        else "LLM_SCHEMA_VALIDATION_FAILED"
                    ),
                ],
                llm_run=llm_run,
                proposal=None,
                provider_attempt=(
                    self.session.get(ProviderAttempt, route.provider_attempt_id)
                    if route.provider_attempt_id is not None
                    else None
                ),
                quota_event_id=None,
                cost_event_id=None,
                budget_gate_result=budget_result,
            )
        parsed = validation.parsed_output if isinstance(validation.parsed_output, dict) else {}
        parsed_status = str(parsed.get("status") or "").upper()
        if parsed_status != "OK":
            return LLMWorkflowResult(
                terminal_status="BLOCKED",
                reason_codes=[
                    *validation.reason_codes,
                    (
                        "DAILY_IDEA_REVIEW_REQUIRED"
                        if parsed_status == "REVIEW_REQUIRED"
                        else "DAILY_IDEA_BLOCKED"
                        if parsed_status in {"BLOCK", "REFUSAL"}
                        else "DAILY_IDEA_OUTPUT_STATUS_NOT_OK"
                    ),
                ],
                llm_run=llm_run,
                proposal=None,
                provider_attempt=(
                    self.session.get(ProviderAttempt, route.provider_attempt_id)
                    if route.provider_attempt_id is not None
                    else None
                ),
                quota_event_id=None,
                cost_event_id=None,
                budget_gate_result=budget_result,
            )
        artifact = parsed.get("artifact") if isinstance(parsed.get("artifact"), dict) else {}
        try:
            proposal = MockAuthorityProposal.model_validate(
                {
                    "proposed_title": artifact.get("proposed_title"),
                    "proposed_angle": artifact.get("proposed_angle"),
                    "proposed_format": artifact.get("proposed_format"),
                    "proposed_pillar": artifact.get("proposed_pillar"),
                    "proposed_series_key": artifact.get("proposed_series_key"),
                    "audience_problem": artifact.get("audience_problem"),
                    "search_intent_hypothesis": artifact.get("search_intent_hypothesis") or {},
                    "rationale": {
                        **(artifact.get("rationale") or {}),
                        "channel_fit_score": artifact.get("channel_fit_score"),
                        "channel_fit_evidence": artifact.get("channel_fit_evidence") or {},
                        "prompt_render_run_id": str(render.prompt_render_run_id),
                        "prompt_audit_snapshot_id": str(render.prompt_audit_snapshot_id),
                    },
                    "evidence_refs": parsed.get("evidence_refs") or context_pack.evidence_refs,
                    "confidence": parsed.get("confidence_label") or "UNKNOWN",
                    "idea_source_refs": [
                        *_authority_source_refs(context_pack),
                        {
                            "type": "context_pack_snapshot",
                            "id": str(context_pack.id),
                            "pack_hash": context_pack.pack_hash,
                        },
                    ],
                    # Router JSON numbers arrive as ``float`` values while the
                    # authoritative proposal contract deliberately stores a
                    # bounded Decimal.  Normalize at the JSON boundary so a
                    # valid DailyIdeaAgent response is not rejected by the
                    # contract's strict model configuration.
                    "channel_fit_score": Decimal(str(artifact.get("channel_fit_score"))),
                    "channel_fit_evidence": artifact.get("channel_fit_evidence") or {},
                }
            ).model_dump(mode="json")
        except (ValidationError, ArithmeticError, TypeError, ValueError):
            return LLMWorkflowResult(
                terminal_status="FAILED",
                reason_codes=["LLM_OUTPUT_MALFORMED", "LLM_SCHEMA_VALIDATION_FAILED"],
                llm_run=llm_run,
                proposal=None,
                provider_attempt=(
                    self.session.get(ProviderAttempt, route.provider_attempt_id)
                    if route.provider_attempt_id is not None
                    else None
                ),
                quota_event_id=None,
                cost_event_id=None,
                budget_gate_result=budget_result,
            )
        return LLMWorkflowResult(
            terminal_status="COMPLETED",
            reason_codes=["DAILY_IDEA_ROUTED", *(route.reason_codes or [])],
            llm_run=llm_run,
            proposal=proposal,
            provider_attempt=(
                self.session.get(ProviderAttempt, route.provider_attempt_id)
                if route.provider_attempt_id is not None
                else None
            ),
            quota_event_id=None,
            cost_event_id=None,
            budget_gate_result=budget_result,
        )

    def _blocked_run(
        self,
        *,
        daily_run: ChannelDailyRun,
        context_pack: ContextPackSnapshot,
        provider_key: str,
        reason_codes: list[str],
        message: str,
        correlation_id: str,
        budget_gate_result: dict[str, Any] | None = None,
    ) -> LLMWorkflowResult:
        llm_run = self._create_llm_run(
            daily_run=daily_run,
            context_pack=context_pack,
            provider_key=provider_key,
            status="BLOCKED",
            output_payload={"error": message, "reason_codes": reason_codes},
            quota_event_id=None,
            cost_event_id=None,
            correlation_id=correlation_id,
            run_mode=getattr(daily_run, "run_mode", "REAL"),
        )
        return LLMWorkflowResult(
            terminal_status="BLOCKED",
            reason_codes=reason_codes,
            llm_run=llm_run,
            proposal=None,
            provider_attempt=None,
            quota_event_id=None,
            cost_event_id=None,
            budget_gate_result=budget_gate_result or {},
        )

    def _create_llm_run(
        self,
        *,
        daily_run: ChannelDailyRun,
        context_pack: ContextPackSnapshot,
        provider_key: str,
        status: str,
        output_payload: dict[str, Any] | None,
        quota_event_id: uuid.UUID | None,
        cost_event_id: uuid.UUID | None,
        correlation_id: str,
        run_mode: str = "REAL_DISABLED",
        provider_configured: bool = False,
    ) -> LLMRunSnapshot:
        input_payload = {
            "purpose": "DAILY_IDEA",
            "channel_daily_run_id": str(daily_run.id),
            "context_pack_snapshot_id": str(context_pack.id),
            "context_pack_hash": context_pack.pack_hash,
            "policy_snapshot_id": str(daily_run.policy_snapshot_id),
            "provider_key": provider_key,
        }
        llm_run = LLMRunSnapshot(
            run_type="M5_CHANNEL_AUTHORITY_PROPOSAL",
            provider="llm_router",
            model_name=None,
            provider_key=provider_key,
            model_key=None,
            run_mode=run_mode,
            prompt_template_key="daily_idea_agent.production",
            prompt_template_version="1.0.0",
            input_payload=input_payload,
            input_hash=_hash_payload(input_payload),
            output_payload=output_payload,
            output_hash=_hash_payload(output_payload) if output_payload is not None else None,
            status=status,
            estimated_cost=Decimal("0"),
            token_estimate=Decimal("0"),
            quota_event_id=quota_event_id,
            cost_event_id=cost_event_id,
            cost_payload={"estimated_cost": "0", "currency": "USD", "provider_configured": provider_configured},
            correlation_id=correlation_id,
            completed_at=utc_now(),
        )
        self.session.add(llm_run)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="llm_run_snapshot.completed" if status == "COMPLETED" else "llm_run_snapshot.blocked",
            aggregate_type="llm_run_snapshot",
            aggregate_id=llm_run.id,
            actor_id=None,
            target_type="llm_run_snapshot",
            target_id=llm_run.id,
            company_id=daily_run.company_id,
            correlation_id=correlation_id,
            reason_code="LLM_RUN_SNAPSHOT_CREATED",
            payload={
                "run_type": llm_run.run_type,
                "provider_key": llm_run.provider_key,
                "run_mode": llm_run.run_mode,
                "status": llm_run.status,
                "input_hash": llm_run.input_hash,
                "output_hash": llm_run.output_hash,
            },
        )
        return llm_run


class ChannelAuthorityService:
    def __init__(self, session: Session, *, llm_workflow: LLMWorkflowService | None = None):
        self.session = session
        self.llm_workflow = llm_workflow or LLMWorkflowService(session)

    def create_decision(
        self,
        *,
        data: DailyIdeaDecisionCreate,
        correlation_id: str = "m5-channel-authority",
    ) -> DailyIdeaDecision:
        daily_run = _require_daily_run(self.session, data.channel_daily_run_id)
        context_pack = ResourceResolverService(self.session).require_context_pack(data.context_pack_snapshot_id)
        state_pack = (
            self.session.get(ChannelStatePackSnapshot, data.channel_state_pack_snapshot_id)
            if data.channel_state_pack_snapshot_id is not None
            else None
        )
        _validate_pack_scope(daily_run, context_pack, state_pack)
        result = self.llm_workflow.run_authority(
            daily_run=daily_run,
            context_pack=context_pack,
            provider_key=data.provider_key,
            quota_account_id=data.quota_account_id,
            budget_policy_key=data.budget_policy_key,
            estimated_cost=data.estimated_cost,
            correlation_id=correlation_id,
        )
        if result.terminal_status != "COMPLETED" or result.proposal is None or result.llm_run is None:
            raise M5AuthorityError(
                "M5 real-provider authority is not configured",
                terminal_status=result.terminal_status,
                reason_codes=result.reason_codes,
                llm_run_snapshot_id=result.llm_run.id if result.llm_run else None,
            )
        proposal = result.proposal
        decision = DailyIdeaDecision(
            channel_daily_run_id=daily_run.id,
            company_id=daily_run.company_id,
            channel_workspace_id=daily_run.channel_workspace_id,
            policy_snapshot_id=daily_run.policy_snapshot_id,
            context_pack_snapshot_id=context_pack.id,
            channel_state_pack_snapshot_id=state_pack.id if state_pack else None,
            llm_run_snapshot_id=result.llm_run.id,
            decision_status="PROPOSED",
            proposed_title=proposal["proposed_title"],
            proposed_angle=proposal.get("proposed_angle"),
            proposed_format=proposal.get("proposed_format"),
            proposed_pillar=proposal.get("proposed_pillar"),
            proposed_series_key=proposal.get("proposed_series_key"),
            rationale={
                **proposal.get("rationale", {}),
                "audience_problem": proposal.get("audience_problem"),
                "search_intent_hypothesis": proposal.get(
                    "search_intent_hypothesis", {}
                ),
                "channel_fit_score": proposal.get("channel_fit_score"),
                "channel_fit_evidence": proposal.get("channel_fit_evidence", {}),
            },
            evidence_refs=proposal.get("evidence_refs", []),
            reason_codes=result.reason_codes,
            confidence_level=proposal.get("confidence", "UNKNOWN"),
        )
        self.session.add(decision)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="daily_idea_decision.created",
            aggregate_type="daily_idea_decision",
            aggregate_id=decision.id,
            actor_id=None,
            target_type="daily_idea_decision",
            target_id=decision.id,
            company_id=decision.company_id,
            correlation_id=correlation_id,
            reason_code="LLM_RUN_SNAPSHOT_CREATED",
            payload={
                "channel_daily_run_id": str(daily_run.id),
                "context_pack_snapshot_id": str(context_pack.id),
                "llm_run_snapshot_id": str(result.llm_run.id),
                "decision_status": decision.decision_status,
                "reason_codes": decision.reason_codes,
            },
        )
        return decision


class ChannelDailyRunService:
    def __init__(self, session: Session, *, llm_workflow: LLMWorkflowService | None = None):
        self.session = session
        self.llm_workflow = llm_workflow or LLMWorkflowService(session)

    def create_run(
        self,
        *,
        data: ChannelDailyRunCreate,
        correlation_id: str = "m5-daily-run-create",
    ) -> ChannelDailyRun:
        if data.run_mode in {"REAL", "MOCK"}:
            reason_codes: list[str] = []
            status = data.status
        else:
            reason_codes = ["REAL_LLM_EXECUTION_DISABLED", "HUMAN_ACTION_REQUIRED"]
            status = "BLOCKED"
        _validate_channel_policy_scope(
            self.session,
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            policy_snapshot_id=data.policy_snapshot_id,
        )
        if data.editorial_calendar_slot_id is not None:
            slot = self.session.get(EditorialCalendarSlot, data.editorial_calendar_slot_id)
            if slot is None:
                raise NotFoundError(f"editorial slot not found: {data.editorial_calendar_slot_id}")
            if slot.company_id != data.company_id or slot.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("slot scope does not match daily run")
            if slot.policy_snapshot_id != data.policy_snapshot_id:
                raise ValidationFailureError("slot policy snapshot does not match daily run")
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        payload["status"] = status
        payload["reason_codes"] = reason_codes or payload.get("reason_codes", [])
        daily_run = ChannelDailyRun(**payload, metadata_=metadata)
        self.session.add(daily_run)
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="channel_daily_run.created",
            aggregate_type="channel_daily_run",
            aggregate_id=daily_run.id,
            actor_id=None,
            target_type="channel_daily_run",
            target_id=daily_run.id,
            company_id=daily_run.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={
                "channel_workspace_id": str(daily_run.channel_workspace_id),
                "policy_snapshot_id": str(daily_run.policy_snapshot_id),
                "run_date": daily_run.run_date.isoformat(),
                "run_mode": daily_run.run_mode,
            },
        )
        return daily_run

    def execute_run(
        self,
        *,
        daily_run_id: uuid.UUID,
        data: DailyRunExecuteRequest,
        correlation_id: str = "m5-daily-run-execute",
    ) -> ChannelDailyRun:
        daily_run = _require_daily_run(self.session, daily_run_id)
        if daily_run.status != "PENDING":
            raise ConflictError(f"daily run is not executable from status: {daily_run.status}")
        daily_run.status = "RUNNING"
        if daily_run.run_mode == "MOCK":
            daily_run.metadata_ = {
                **(daily_run.metadata_ or {}),
                "fixture_mode": data.fixture_mode,
            }
        daily_run.started_at = utc_now()
        self.session.flush()
        _record_m5_event(
            self.session,
            event_type="channel_daily_run.started",
            aggregate_type="channel_daily_run",
            aggregate_id=daily_run.id,
            actor_id=data.created_by_user_id,
            target_type="channel_daily_run",
            target_id=daily_run.id,
            company_id=daily_run.company_id,
            correlation_id=correlation_id,
            reason_code="CONTEXT_PACK_CREATED",
            payload={"run_mode": daily_run.run_mode},
        )
        try:
            plan = ResourceResolverService(self.session).create_retrieval_plan(
                data=RetrievalPlanSnapshotCreate(
                    purpose="DAILY_IDEA",
                    company_id=daily_run.company_id,
                    channel_workspace_id=daily_run.channel_workspace_id,
                    policy_snapshot_id=daily_run.policy_snapshot_id,
                    editorial_calendar_slot_id=daily_run.editorial_calendar_slot_id,
                    allowed_sources=DEFAULT_DAILY_CONTEXT_SOURCES,
                    source_order=DEFAULT_DAILY_CONTEXT_SOURCES,
                    created_by_user_id=data.created_by_user_id,
                ),
                correlation_id="m5-daily-run-retrieval-plan",
            )
            context_pack = ResourceResolverService(self.session).build_context_pack(
                data=ContextPackSnapshotCreate(
                    retrieval_plan_snapshot_id=plan.id,
                    freshness_state="UNKNOWN",
                    confidence_level="UNKNOWN",
                    created_by_user_id=data.created_by_user_id,
                ),
                correlation_id="m5-daily-run-context-pack",
            )
            state_pack = ChannelStatePackService(self.session).build_snapshot(
                data=ChannelStatePackSnapshotCreate(
                    channel_daily_run_id=daily_run.id,
                    company_id=daily_run.company_id,
                    channel_workspace_id=daily_run.channel_workspace_id,
                    policy_snapshot_id=daily_run.policy_snapshot_id,
                    context_pack_snapshot_id=context_pack.id,
                ),
                correlation_id="m5-daily-run-state-pack",
            )
            daily_run.context_pack_snapshot_id = context_pack.id
            daily_run.channel_state_pack_snapshot_id = state_pack.id
            self.session.flush()
            decision = ChannelAuthorityService(self.session, llm_workflow=self.llm_workflow).create_decision(
                data=DailyIdeaDecisionCreate(
                    channel_daily_run_id=daily_run.id,
                    context_pack_snapshot_id=context_pack.id,
                    channel_state_pack_snapshot_id=state_pack.id,
                    provider_key=data.provider_key,
                    quota_account_id=data.quota_account_id,
                    budget_policy_key=data.budget_policy_key,
                    estimated_cost=data.estimated_cost,
                ),
                correlation_id="m5-daily-run-authority",
            )
            daily_run.daily_idea_decision_id = decision.id
            daily_run.status = "COMPLETED"
            daily_run.completed_at = utc_now()
            daily_run.reason_codes = ["DAILY_RUN_COMPLETED", *decision.reason_codes]
            self.session.flush()
            _record_m5_event(
                self.session,
                event_type="channel_daily_run.completed",
                aggregate_type="channel_daily_run",
                aggregate_id=daily_run.id,
                actor_id=data.created_by_user_id,
                target_type="channel_daily_run",
                target_id=daily_run.id,
                company_id=daily_run.company_id,
                correlation_id=correlation_id,
                reason_code="DAILY_RUN_COMPLETED",
                payload={"daily_idea_decision_id": str(decision.id), "reason_codes": daily_run.reason_codes},
            )
        except M5AuthorityError as exc:
            daily_run.status = "BLOCKED" if exc.terminal_status == "BLOCKED" else "FAILED"
            daily_run.completed_at = utc_now()
            daily_run.reason_codes = exc.reason_codes
            self.session.flush()
            _record_m5_event(
                self.session,
                event_type="channel_daily_run.blocked" if daily_run.status == "BLOCKED" else "channel_daily_run.failed",
                aggregate_type="channel_daily_run",
                aggregate_id=daily_run.id,
                actor_id=data.created_by_user_id,
                target_type="channel_daily_run",
                target_id=daily_run.id,
                company_id=daily_run.company_id,
                correlation_id=correlation_id,
                reason_code=daily_run.reason_codes[0] if daily_run.reason_codes else "DAILY_RUN_FAILED",
                payload={"reason_codes": daily_run.reason_codes, "llm_run_snapshot_id": str(exc.llm_run_snapshot_id) if exc.llm_run_snapshot_id else None},
            )
        return daily_run

    def get_run(self, daily_run_id: uuid.UUID) -> ChannelDailyRun | None:
        return self.session.get(ChannelDailyRun, daily_run_id)


class ProjectAdmissionService:
    def __init__(self, session: Session):
        self.session = session

    def create_decision(
        self,
        *,
        data: ProjectAdmissionDecisionCreate,
        correlation_id: str = "m5-project-admission",
    ) -> ProjectAdmissionDecision:
        daily_run = _require_daily_run(self.session, data.channel_daily_run_id)
        idea = self.session.scalar(
            select(DailyIdeaDecision)
            .where(DailyIdeaDecision.id == data.daily_idea_decision_id)
            .with_for_update()
        )
        if idea is None:
            raise NotFoundError(f"daily idea decision not found: {data.daily_idea_decision_id}")
        if idea.channel_daily_run_id != daily_run.id:
            raise ValidationFailureError("idea decision does not belong to daily run")
        # A DailyIdeaDecision is immutable admission input.  Reusing the same
        # decision after either an ADMIT or a terminal policy BLOCK must return
        # the original receipt; otherwise a blocked Effective Context could
        # create a second project and a second set of initial artifacts.
        existing_admission = self.session.scalars(
            select(ProjectAdmissionDecision)
            .where(ProjectAdmissionDecision.daily_idea_decision_id == idea.id)
            .order_by(ProjectAdmissionDecision.created_at.asc())
        ).first()
        if existing_admission is not None:
            return existing_admission
        preflight = self.session.get(IdeaMarketPreflight, data.idea_market_preflight_id) if data.idea_market_preflight_id else None
        if data.idea_market_preflight_id is not None and preflight is None:
            raise NotFoundError(f"idea market preflight not found: {data.idea_market_preflight_id}")
        budget_gate_result = self._budget_gate(data, daily_run)
        decision, reasons = _admission_result(idea, preflight, budget_gate_result)
        nich1_lineage, nich1_blockers = _resolve_nich1_admission_lineage(
            self.session,
            daily_run=daily_run,
            idea=idea,
            preflight=preflight,
        )
        if nich1_blockers:
            decision = "BLOCK"
            reasons = [*nich1_blockers, "IDEA_BLOCKED"]
        admitted_project: VideoProject | None = None
        artifact_refs: list[dict[str, Any]] = []
        evidence_refs = list(idea.evidence_refs)
        if preflight is not None:
            evidence_refs.extend(preflight.evidence_blob.get("evidence_refs", []))
        readiness_gate_refs: list[dict[str, Any]] = []
        if decision == "ADMIT":
            scope_result = ProjectScopeAdmissionGuard(self.session).evaluate_for_daily_run(
                daily_run,
                explicit_category_id=data.category_id,
                explicit_character_binding_id=data.character_binding_id,
            )
            readiness_gate_refs = scope_result.readiness_refs()
            if nich1_lineage is not None:
                readiness_gate_refs.extend(nich1_lineage["readiness_gate_refs"])
            if not scope_result.ok:
                decision = "BLOCK"
                reasons = [*scope_result.reason_codes, "IDEA_BLOCKED"]
            else:
                category_id = scope_result.category_id
                character_binding_id = scope_result.character_binding_id
                channel_contract_content_hash = scope_result.channel_contract_content_hash
                if category_id is None or channel_contract_content_hash is None:
                    decision = "BLOCK"
                    reasons = ["CATEGORY_SCOPE_MISSING", "IDEA_BLOCKED"]
        if decision == "ADMIT":
            if data.created_by_user_id is None:
                raise ValidationFailureError("created_by_user_id is required to admit a project")
            effective_status: str | None = None
            with self.session.begin_nested() as candidate_savepoint:
                candidate_project = VideoProjectService(self.session).create_project(
                    data=VideoProjectCreate(
                        company_id=daily_run.company_id,
                        channel_workspace_id=daily_run.channel_workspace_id,
                        policy_snapshot_id=daily_run.policy_snapshot_id,
                        category_id=category_id,
                        character_binding_id=character_binding_id,
                        channel_contract_content_hash=channel_contract_content_hash,
                        title=idea.proposed_title,
                        description=idea.proposed_angle,
                        project_type="m5_daily_run",
                        created_by_user_id=data.created_by_user_id,
                        audience_delivery_summary={
                            "daily_idea_decision_id": str(idea.id),
                            "context_pack_snapshot_id": str(idea.context_pack_snapshot_id),
                            **(
                                {"niche_governance": nich1_lineage["frozen_lineage"]}
                                if nich1_lineage is not None
                                else {}
                            ),
                        },
                    ),
                    correlation_id="m5-video-project-created-from-daily-run",
                )
                effective_snapshot = EffectiveChannelRuntimeContextCompiler(
                    self.session
                ).compile_for_project(
                    project=candidate_project,
                    editorial_calendar_slot=(
                        self.session.get(
                            EditorialCalendarSlot,
                            daily_run.editorial_calendar_slot_id,
                        )
                        if daily_run.editorial_calendar_slot_id
                        else None
                    ),
                )
                effective_status = effective_snapshot.compile_status
                if effective_status != "PASS":
                    candidate_savepoint.rollback()
                else:
                    artifact_refs = self._create_initial_artifacts(
                        candidate_project,
                        idea,
                        data.created_by_user_id,
                    )
                    admitted_project = candidate_project
            if effective_status != "PASS":
                decision = "BLOCK"
                reasons.extend(
                    [
                        f"EFFECTIVE_CONTEXT_{effective_status or 'UNKNOWN'}",
                        "EFFECTIVE_CONTEXT_NOT_PASS",
                        "CANDIDATE_PROJECT_ROLLED_BACK",
                        "IDEA_BLOCKED",
                    ]
                )
            else:
                reasons.extend(
                    [
                        "PROJECT_CREATED_FROM_DAILY_RUN",
                        "EFFECTIVE_CONTEXT_SNAPSHOT_CREATED",
                        "EFFECTIVE_CONTEXT_PASS",
                        "INITIAL_ARTIFACTS_CREATED",
                    ]
                )
        record = ProjectAdmissionDecision(
            channel_daily_run_id=daily_run.id,
            daily_idea_decision_id=idea.id,
            idea_market_preflight_id=preflight.id if preflight else None,
            budget_gate_result=budget_gate_result,
            readiness_gate_refs=readiness_gate_refs,
            decision=decision,
            reason_codes=reasons,
            evidence_refs=evidence_refs,
            admitted_video_project_id=admitted_project.id if admitted_project else None,
            created_artifact_refs=artifact_refs,
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(record)
        self.session.flush()
        daily_run.project_admission_decision_id = record.id
        if decision == "ADMIT":
            if daily_run.editorial_calendar_slot_id is not None:
                slot = self.session.get(EditorialCalendarSlot, daily_run.editorial_calendar_slot_id)
                if slot is not None:
                    slot.status = "ADMITTED"
        self.session.flush()
        event_type = "project_admission_decision.admitted" if decision == "ADMIT" else "project_admission_decision.blocked" if decision == "BLOCK" else "project_admission_decision.created"
        _record_m5_event(
            self.session,
            event_type="project_admission_decision.created",
            aggregate_type="project_admission_decision",
            aggregate_id=record.id,
            actor_id=data.created_by_user_id,
            target_type="project_admission_decision",
            target_id=record.id,
            company_id=daily_run.company_id,
            correlation_id=correlation_id,
            reason_code=record.reason_codes[0] if record.reason_codes else "IDEA_REVIEW_REQUIRED",
            payload={"decision": record.decision, "reason_codes": record.reason_codes},
        )
        if event_type != "project_admission_decision.created":
            _record_m5_event(
                self.session,
                event_type=event_type,
                aggregate_type="project_admission_decision",
                aggregate_id=record.id,
                actor_id=data.created_by_user_id,
                target_type="project_admission_decision",
                target_id=record.id,
                company_id=daily_run.company_id,
                correlation_id=correlation_id,
                reason_code=record.reason_codes[0],
                payload={
                    "decision": record.decision,
                    "admitted_video_project_id": str(record.admitted_video_project_id) if record.admitted_video_project_id else None,
                },
            )
        return record

    def get_decision(self, decision_id: uuid.UUID) -> ProjectAdmissionDecision | None:
        return self.session.get(ProjectAdmissionDecision, decision_id)

    def _budget_gate(self, data: ProjectAdmissionDecisionCreate, daily_run: ChannelDailyRun) -> dict[str, Any]:
        if data.budget_policy_key is None:
            return {"decision": "PASS", "reason_codes": ["SYSTEM_OK"], "deterministic": True, "policy_key": None}
        result = BudgetGateService(self.session).check(
            data=BudgetGateCheckRequest(
                policy_key=data.budget_policy_key,
                provider_key="llm_router",
                scope_type="CHANNEL",
                scope_id=daily_run.channel_workspace_id,
                estimated_cost=data.estimated_cost,
                quota_account_id=data.quota_account_id,
                quota_amount=Decimal("1") if data.quota_account_id else None,
                unit="REQUESTS" if data.quota_account_id else None,
            ),
            correlation_id="m5-admission-budget-gate",
        )
        return result.model_dump(mode="json")

    def _create_initial_artifacts(
        self,
        project: VideoProject,
        idea: DailyIdeaDecision,
        created_by_user_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        artifact_service = ArtifactService(self.session)
        for artifact_type in INITIAL_M5_ARTIFACT_TYPES:
            content = _initial_artifact_content(artifact_type, idea)
            artifact = artifact_service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project.id,
                    artifact_type=artifact_type,
                    created_by_user_id=created_by_user_id,
                ),
                correlation_id="m5-initial-artifact",
            )
            version = artifact_service.create_artifact_version(
                data=ArtifactVersionCreate(
                    artifact_id=artifact.id,
                    content=content,
                    created_by_user_id=created_by_user_id,
                    evidence_refs=idea.evidence_refs,
                    context_refs=[{"type": "context_pack_snapshot", "id": str(idea.context_pack_snapshot_id)}],
                    retrieval_plan_ref=str(idea.context_pack_snapshot_id),
                ),
                correlation_id="m5-initial-artifact-version",
            )
            refs.append(
                {
                    "artifact_id": str(artifact.id),
                    "artifact_version_id": str(version.id),
                    "artifact_type": artifact.artifact_type,
                    "version_number": version.version_number,
                }
            )
            _record_m5_event(
                self.session,
                event_type="initial_artifact.created_from_daily_run",
                aggregate_type="artifact",
                aggregate_id=artifact.id,
                actor_id=created_by_user_id,
                target_type="artifact",
                target_id=artifact.id,
                company_id=project.company_id,
                correlation_id="m5-initial-artifact",
                reason_code="INITIAL_ARTIFACTS_CREATED",
                payload=refs[-1],
            )
        _record_m5_event(
            self.session,
            event_type="video_project.created_from_daily_run",
            aggregate_type="video_project",
            aggregate_id=project.id,
            actor_id=created_by_user_id,
            target_type="video_project",
            target_id=project.id,
            company_id=project.company_id,
            correlation_id="m5-video-project-created-from-daily-run",
            reason_code="PROJECT_CREATED_FROM_DAILY_RUN",
            payload={"policy_snapshot_id": str(project.policy_snapshot_id), "daily_idea_decision_id": str(idea.id)},
        )
        return refs


def _validate_allowed_sources(allowed_sources: list[str], excluded_sources: list[str]) -> None:
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


def _require_company(session: Session, company_id: uuid.UUID) -> None:
    from app.db.models import Company

    if session.get(Company, company_id) is None:
        raise NotFoundError(f"company not found: {company_id}")


def _require_channel_for_company(session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID) -> ChannelWorkspace:
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


def _require_daily_run(session: Session, daily_run_id: uuid.UUID) -> ChannelDailyRun:
    daily_run = session.get(ChannelDailyRun, daily_run_id)
    if daily_run is None:
        raise NotFoundError(f"daily run not found: {daily_run_id}")
    return daily_run


def _validate_pack_scope(
    daily_run: ChannelDailyRun,
    context_pack: ContextPackSnapshot,
    state_pack: ChannelStatePackSnapshot | None,
) -> None:
    if context_pack.company_id != daily_run.company_id:
        raise ValidationFailureError("context pack company does not match daily run")
    if context_pack.channel_workspace_id != daily_run.channel_workspace_id:
        raise ValidationFailureError("context pack channel does not match daily run")
    if context_pack.policy_snapshot_id != daily_run.policy_snapshot_id:
        raise ValidationFailureError("context pack policy snapshot does not match daily run")
    if state_pack is not None:
        if state_pack.company_id != daily_run.company_id or state_pack.channel_workspace_id != daily_run.channel_workspace_id:
            raise ValidationFailureError("channel state pack scope does not match daily run")
        if state_pack.policy_snapshot_id != daily_run.policy_snapshot_id:
            raise ValidationFailureError("channel state pack policy snapshot does not match daily run")


def _plan_scope(plan: RetrievalPlanSnapshot) -> dict[str, Any]:
    return {
        "purpose": plan.purpose,
        "company_id": str(plan.company_id),
        "channel_workspace_id": str(plan.channel_workspace_id) if plan.channel_workspace_id else None,
        "channel_profile_version_id": str(plan.channel_profile_version_id) if plan.channel_profile_version_id else None,
        "policy_snapshot_id": str(plan.policy_snapshot_id) if plan.policy_snapshot_id else None,
        "video_project_id": str(plan.video_project_id) if plan.video_project_id else None,
        "editorial_calendar_slot_id": str(plan.editorial_calendar_slot_id) if plan.editorial_calendar_slot_id else None,
    }


def _active_project_refs(session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    projects = session.scalars(
        select(VideoProject)
        .where(VideoProject.company_id == company_id)
        .where(VideoProject.channel_workspace_id == channel_workspace_id)
        .where(VideoProject.status.in_(["draft", "in_review"]))
        .order_by(VideoProject.created_at.asc())
    ).all()
    return [{"type": "video_project", "id": str(project.id), "status": project.status, "title": project.title} for project in projects]


def _pending_reviews(session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID | None) -> list[dict[str, Any]]:
    statement = (
        select(ReviewTask, VideoProject)
        .join(VideoProject, ReviewTask.video_project_id == VideoProject.id)
        .where(VideoProject.company_id == company_id)
        .where(ReviewTask.status.in_(["open", "in_progress"]))
        .order_by(ReviewTask.created_at.asc())
    )
    if channel_workspace_id is not None:
        statement = statement.where(VideoProject.channel_workspace_id == channel_workspace_id)
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


def _readiness_summary(session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID) -> dict[str, Any]:
    statement = (
        select(GateRun.result, func.count())
        .join(VideoProject, GateRun.video_project_id == VideoProject.id)
        .where(VideoProject.company_id == company_id)
        .where(VideoProject.channel_workspace_id == channel_workspace_id)
        .group_by(GateRun.result)
    )
    counts = {result: count for result, count in session.execute(statement).all()}
    return {"gate_result_counts": counts, "state": "UNKNOWN" if not counts else "FRESH"}


def _gate_summary(session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID | None) -> dict[str, Any]:
    statement = (
        select(GateRun.gate_key, GateRun.result, func.count())
        .join(VideoProject, GateRun.video_project_id == VideoProject.id)
        .where(VideoProject.company_id == company_id)
        .group_by(GateRun.gate_key, GateRun.result)
    )
    if channel_workspace_id is not None:
        statement = statement.where(VideoProject.channel_workspace_id == channel_workspace_id)
    return {
        f"{gate_key}:{result}": count
        for gate_key, result, count in session.execute(statement).all()
    }


def _provider_health_summary(session: Session) -> dict[str, Any]:
    health = _latest_provider_health(session, "llm_router")
    return {
        "llm_router": {
            "state": health.health_state if health else "UNKNOWN",
            "reason_codes": health.reason_codes if health else ["LLM_PROVIDER_NOT_CONFIGURED"],
            "checked_at": health.checked_at.isoformat() if health else None,
        }
    }


def _latest_provider_health(session: Session, provider_key: str) -> ProviderHealthSnapshot | None:
    return session.scalars(
        select(ProviderHealthSnapshot)
        .where(ProviderHealthSnapshot.provider_key == provider_key)
        .order_by(ProviderHealthSnapshot.checked_at.desc())
        .limit(1)
    ).one_or_none()


def _quota_summary(session: Session) -> dict[str, Any]:
    accounts = session.scalars(select(QuotaAccount).where(QuotaAccount.provider_key == "llm_router")).all()
    return {
        "llm_router": [
            {
                "quota_account_id": str(account.id),
                "status": account.status,
                "unit": account.unit,
                "quota_limit": str(account.quota_limit) if account.quota_limit is not None else None,
                "quota_used": str(account.quota_used),
                "quota_reserved": str(account.quota_reserved),
            }
            for account in accounts
        ]
    }


def _search_evidence_refs(session: Session, company_id: uuid.UUID, channel_workspace_id: uuid.UUID) -> list[dict[str, Any]]:
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


def _resolve_preflight_evidence_refs(session: Session, data: IdeaMarketPreflightCreate) -> list[dict[str, Any]]:
    refs = data.evidence_blob.get("evidence_refs") or []
    ids = data.evidence_blob.get("search_demand_evidence_ids") or []
    if ids:
        for raw_id in ids:
            evidence_id = uuid.UUID(str(raw_id))
            evidence = session.get(SearchDemandEvidence, evidence_id)
            if evidence is None:
                raise NotFoundError(f"search demand evidence not found: {evidence_id}")
            if evidence.company_id != data.company_id or evidence.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("search demand evidence scope does not match preflight")
            refs.append(
                {
                    "type": "search_demand_evidence",
                    "id": str(evidence.id),
                    "search_volume_30d": evidence.search_volume_30d,
                    "relative_interest_index": str(evidence.relative_interest_index) if evidence.relative_interest_index is not None else None,
                    "competition_index": str(evidence.competition_index) if evidence.competition_index is not None else None,
                    "confidence": evidence.evidence_confidence,
                }
            )
    if not refs and data.search_intent_map_id is not None:
        intent = session.get(SearchIntentMap, data.search_intent_map_id)
        if intent is None:
            raise NotFoundError(f"search intent map not found: {data.search_intent_map_id}")
        refs = list(intent.source_evidence_refs)
    return refs


def _is_nich1_strict_snapshot(snapshot: CompiledChannelPolicySnapshot) -> bool:
    payload = snapshot.compiled_payload if isinstance(snapshot.compiled_payload, dict) else {}
    scoped = payload.get("channel_scoped_policy") if isinstance(payload.get("channel_scoped_policy"), dict) else {}
    binding = scoped.get("visual_source_policy_binding") if isinstance(scoped.get("visual_source_policy_binding"), dict) else {}
    return (
        binding.get("schema_version") == "ch1-flex.visual-source-policy-binding.v2"
        and scoped.get("policy_version") == "small-team-ai.channel-policy.v2"
    )


def _niche_digest_from_context(pack: ContextPackSnapshot) -> tuple[NicheContractDigest, dict[str, Any]]:
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


def _validate_nich1_daily_context_authority(
    session: Session,
    *,
    context_pack: ContextPackSnapshot,
    snapshot: CompiledChannelPolicySnapshot,
) -> NicheContractDigest:
    """Recompile strict daily niche truth and reject caller/row substitution."""

    if (
        context_pack.policy_snapshot_id != snapshot.id
        or context_pack.channel_workspace_id != snapshot.channel_workspace_id
        or context_pack.channel_profile_version_id
        != snapshot.channel_profile_version_id
        or context_pack.editorial_calendar_slot_id is None
    ):
        raise ValidationFailureError("NICH1_DAILY_CONTEXT_SCOPE_MISMATCH")
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
        raise ValidationFailureError("NICH1_DAILY_CONTEXT_AUTHORITY_MISSING")
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
            f"NICH1_DAILY_CONTEXT_AUTHORITY_BLOCKED:{exc}"
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
        embedded.model_dump(mode="json")
        != authoritative.model_dump(mode="json")
        or embedded_ref.get("ref") != expected_ref
        or embedded_ref.get("content_hash") != authoritative.content_hash
        or nested_digest != authoritative.model_dump(mode="json")
        or agent_context.get("agent_key") != "DailyIdeaAgent"
        or runtime_guard.get("compiled_policy_snapshot_id") != str(snapshot.id)
        or runtime_guard.get("compiled_policy_snapshot_hash") != snapshot.content_hash
        or runtime_guard.get("provider_calls_allowed") is not False
        or runtime_guard.get("direct_provider_sdk_allowed") is not False
    ):
        raise ValidationFailureError("NICH1_DAILY_CONTEXT_DIGEST_BINDING_MISMATCH")
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
        raise ValidationFailureError("NICH1_DAILY_CONTEXT_POLICY_REF_MISMATCH")
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
        raise ValidationFailureError("NICH1_DAILY_CONTEXT_PACK_HASH_MISMATCH")
    return authoritative


def _niche_evidence_ref(*, idea: DailyIdeaDecision, evidence_refs: list[dict[str, Any]]) -> NicheEvidenceRef:
    idea_payload = {
        "id": str(idea.id),
        "policy_snapshot_id": str(idea.policy_snapshot_id),
        "title": idea.proposed_title,
        "angle": idea.proposed_angle,
        "pillar": idea.proposed_pillar,
        "series_key": idea.proposed_series_key,
        "rationale": idea.rationale,
        "evidence_refs": evidence_refs,
    }
    return NicheEvidenceRef(
        type="daily_idea_decision",
        ref=f"daily-idea-decision://{idea.id}",
        content_hash=content_hash(idea_payload),
    )


def _topic_semantic_evidence(
    *,
    idea: DailyIdeaDecision,
    digest: NicheContractDigest,
    evidence_ref: NicheEvidenceRef,
    semantic_threshold: float,
) -> list[NicheCriterionEvidence]:
    rationale = idea.rationale or {}
    channel_fit_evidence = rationale.get("channel_fit_evidence")
    channel_fit_evidence = (
        channel_fit_evidence if isinstance(channel_fit_evidence, dict) else {}
    )
    raw_scores = channel_fit_evidence.get("criterion_scores")
    raw_scores = raw_scores if isinstance(raw_scores, dict) else {}
    raw_rationales = channel_fit_evidence.get("criterion_rationales")
    raw_rationales = raw_rationales if isinstance(raw_rationales, dict) else {}
    adjacent = bool((idea.rationale or {}).get("adjacent_niche_conflict"))
    criteria = (
        NicheCriterion.NICHE_RELEVANCE,
        NicheCriterion.AUDIENCE_FIT,
        NicheCriterion.POSITIONING_FIT,
        NicheCriterion.BRAND_PROMISE_FIT,
        NicheCriterion.ALLOWED_TOPIC_COMPLIANCE,
        NicheCriterion.SERIES_FIT,
        NicheCriterion.PRODUCTION_GOAL_FIT,
    )
    text = " ".join(
        str(value or "")
        for value in (
            idea.proposed_title,
            idea.proposed_angle,
        )
    )
    topic_scope_labels = [
        digest.primary_niche,
        digest.sub_niche,
        digest.category_sub_niche,
        digest.content_pillar_key,
        *digest.allowed_topics,
        *digest.category_allowed_topics,
    ]
    text_tokens = _niche_semantic_tokens(text)
    scope_tokens = _niche_semantic_tokens(" ".join(topic_scope_labels))
    scope_overlap_tokens = text_tokens & scope_tokens
    generic_scope_tokens = {
        "channel",
        "content",
        "lean",
        "operation",
        "operator",
        "practical",
        "professional",
        "small",
        "team",
        "workflow",
    }
    distinctive_scope_overlap = scope_overlap_tokens - generic_scope_tokens
    scope_overlap = bool(
        len(scope_overlap_tokens) >= 2 and distinctive_scope_overlap
    )
    result: list[NicheCriterionEvidence] = []
    for criterion in criteria:
        raw_score = raw_scores.get(criterion.value)
        if raw_score is None:
            raw_score = raw_scores.get(criterion.value.lower())
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = None
        valid_score = score is not None and 0.0 <= score <= 1.0
        reason_codes: list[NicheReasonCode] = []
        if not valid_score:
            verdict = NicheGateVerdict.BLOCK
            reason_codes.append(NicheReasonCode.SEMANTIC_EVIDENCE_MISSING)
            score = None
        elif score >= semantic_threshold:
            verdict = NicheGateVerdict.PASS
        elif score >= max(0.5, semantic_threshold * 0.75):
            verdict = NicheGateVerdict.REVIEW_REQUIRED
            reason_codes.append(NicheReasonCode.SEMANTIC_ALIGNMENT_REVIEW_REQUIRED)
        else:
            verdict = NicheGateVerdict.BLOCK
            reason_codes.append(NicheReasonCode.SEMANTIC_ALIGNMENT_BLOCKED)

        if adjacent:
            verdict = NicheGateVerdict.BLOCK
            reason_codes.append(NicheReasonCode.ADJACENT_NICHE_CONFLICT)
        if criterion in {
            NicheCriterion.NICHE_RELEVANCE,
            NicheCriterion.ALLOWED_TOPIC_COMPLIANCE,
        } and not scope_overlap:
            verdict = NicheGateVerdict.BLOCK
            reason_codes.append(NicheReasonCode.ALLOWED_TOPIC_MISMATCH)
        if criterion == NicheCriterion.AUDIENCE_FIT and not str(
            rationale.get("audience_problem") or ""
        ).strip():
            verdict = NicheGateVerdict.BLOCK
            reason_codes.append(NicheReasonCode.SEMANTIC_EVIDENCE_MISSING)
        if criterion == NicheCriterion.SERIES_FIT and (
            str(idea.proposed_pillar or "").strip().casefold()
            != digest.content_pillar_key.strip().casefold()
            or str(idea.proposed_series_key or "").strip().casefold()
            != digest.series_key.strip().casefold()
        ):
            verdict = NicheGateVerdict.BLOCK
            reason_codes.extend(
                [
                    NicheReasonCode.CATEGORY_PILLAR_MISMATCH,
                    NicheReasonCode.SERIES_PILLAR_MISMATCH,
                ]
            )
        evidence_rationale = raw_rationales.get(criterion.value) or raw_rationales.get(
            criterion.value.lower()
        )
        result.append(
            NicheCriterionEvidence(
                criterion=criterion,
                verdict=verdict,
                score=score,
                rationale=(
                    str(evidence_rationale).strip()
                    if evidence_rationale
                    else (
                        "DailyIdeaAgent criterion evidence was checked against the frozen "
                        f"NicheContractDigest; pillar={digest.content_pillar_key}; "
                        f"series={digest.series_key}; scope_overlap={scope_overlap}; "
                        "scope_overlap_tokens="
                        f"{sorted(scope_overlap_tokens)}; distinctive_overlap_tokens="
                        f"{sorted(distinctive_scope_overlap)}."
                    )
                ),
                reason_codes=list(dict.fromkeys(reason_codes)),
                evidence_refs=[evidence_ref],
            )
        )
    return result


def _niche_semantic_tokens(value: str) -> set[str]:
    """Return bounded lexical anchors; two-letter generic tokens such as AI do not suffice."""

    normalized = "".join(character.casefold() if character.isalnum() else " " for character in value)
    stopwords = {
        "and",
        "for",
        "from",
        "into",
        "one",
        "the",
        "this",
        "that",
        "with",
        "your",
    }
    result: set[str] = set()
    for token in normalized.split():
        if len(token) < 3 or token in stopwords:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        result.add(token)
    return result


def _evaluate_nich1_preflight(
    session: Session,
    *,
    data: IdeaMarketPreflightCreate,
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    daily_run = session.get(ChannelDailyRun, data.channel_daily_run_id) if data.channel_daily_run_id else None
    if daily_run is None:
        return None
    snapshot = session.get(CompiledChannelPolicySnapshot, daily_run.policy_snapshot_id)
    if snapshot is None or not _is_nich1_strict_snapshot(snapshot):
        return None
    if data.daily_idea_decision_id is None:
        raise ValidationFailureError("NICH1_PREFLIGHT_DAILY_IDEA_DECISION_REQUIRED")
    idea = session.get(DailyIdeaDecision, data.daily_idea_decision_id)
    if idea is None or idea.channel_daily_run_id != daily_run.id:
        raise ValidationFailureError("NICH1_PREFLIGHT_DAILY_IDEA_SCOPE_MISMATCH")
    pack = session.get(ContextPackSnapshot, idea.context_pack_snapshot_id)
    if pack is None:
        raise ValidationFailureError("NICH1_PREFLIGHT_CONTEXT_PACK_MISSING")
    _validate_nich1_daily_context_authority(
        session,
        context_pack=pack,
        snapshot=snapshot,
    )
    digest, digest_ref = _niche_digest_from_context(pack)
    slot = session.get(EditorialCalendarSlot, daily_run.editorial_calendar_slot_id) if daily_run.editorial_calendar_slot_id else None
    category = session.get(ContentCategory, slot.category_id) if slot is not None and slot.category_id else None
    channel = session.get(ChannelWorkspace, daily_run.channel_workspace_id)
    profile = session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
    if any(item is None for item in (slot, category, channel, profile)):
        raise ValidationFailureError("NICH1_PREFLIGHT_AUTHORITY_MISSING")
    slot_validation = EditorialSlotValidator().validate(
        channel=channel,
        profile_version=profile,
        policy_snapshot=snapshot,
        channel_contract=(snapshot.compiled_payload or {}).get("channel_contract_json") or {},
        category=category,
        editorial_slot=slot,
        strict_production=True,
    )
    if slot_validation.verdict != NicheGateVerdict.PASS:
        raise ValidationFailureError("NICH1_PREFLIGHT_SLOT_NOT_PASS")
    assert slot_validation.slot_binding is not None
    assert slot_validation.category_binding is not None
    typed_evidence = _niche_evidence_ref(idea=idea, evidence_refs=evidence_refs)
    semantic_threshold = channel_fit_threshold_from_compiled_policy(snapshot)
    topic_gate = TopicNicheAlignmentGate().evaluate(
        TopicNicheAlignmentInput(
            niche_contract_digest=digest,
            niche_contract_digest_ref=str(digest_ref["ref"]),
            niche_contract_digest_hash=digest.content_hash,
            active_policy_snapshot_ref=digest.compiled_policy_snapshot_ref,
            active_policy_snapshot_hash=snapshot.content_hash,
            subject_ref=f"daily-idea-decision://{idea.id}",
            subject_hash=typed_evidence.content_hash,
            semantic_evidence=_topic_semantic_evidence(
                idea=idea,
                digest=digest,
                evidence_ref=typed_evidence,
                semantic_threshold=semantic_threshold,
            ),
            evidence_refs=[typed_evidence],
            channel_id=idea.channel_workspace_id,
            slot_binding=slot_validation.slot_binding,
            category_binding=slot_validation.category_binding,
            topic=idea.proposed_title,
            angle=idea.proposed_angle,
            claim_scope=list((idea.rationale or {}).get("claim_scope") or []),
            adjacent_niche_conflict=bool((idea.rationale or {}).get("adjacent_niche_conflict")),
        )
    )
    score_raw = (idea.rationale or {}).get("channel_fit_score")
    try:
        score = float(score_raw)
    except (TypeError, ValueError):
        score = 0.0
    channel_fit = evaluate_channel_fit(
        score=score,
        compiled_policy=snapshot,
        gate_results=[topic_gate],
        evidence_refs=[typed_evidence],
        required_gate_keys=(NicheGateKey.TOPIC,),
        caller_policy_fit_state=data.policy_fit_state,
    )
    demand_data = data.model_copy(update={"policy_fit_state": "UNKNOWN"})
    demand_decision, demand_reasons, confidence, demand_score = _evaluate_preflight(demand_data, evidence_refs)
    if topic_gate.verdict == NicheGateVerdict.BLOCK or channel_fit.channel_fit_result == NicheGateVerdict.BLOCK:
        decision = "BLOCK"
    elif (
        topic_gate.verdict == NicheGateVerdict.REVIEW_REQUIRED
        or channel_fit.channel_fit_result == NicheGateVerdict.REVIEW_REQUIRED
        or demand_decision == "REVIEW_REQUIRED"
    ):
        decision = "REVIEW_REQUIRED"
    else:
        decision = demand_decision
    dossier = NicheAlignmentDossierBuilder().build(
        digest=digest,
        digest_ref=str(digest_ref["ref"]),
        gate_results=[topic_gate],
        channel_fit=channel_fit,
        dossier_scope=NicheDossierScope.PRE_ADMISSION,
    )
    reason_codes = [
        *demand_reasons,
        *(code.value for code in topic_gate.reason_codes),
        *(code.value for code in channel_fit.reason_codes),
    ]
    return {
        "decision": decision,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "confidence": "HIGH" if decision == "PASS" else confidence,
        "demand_score": demand_score,
        "channel_fit_score": channel_fit.channel_fit_score,
        "channel_fit_threshold": channel_fit.channel_fit_threshold,
        "channel_fit_result": channel_fit.channel_fit_result.value,
        "policy_fit_state": channel_fit.policy_fit_state.value,
        "niche_contract_digest_ref": digest_ref,
        "topic_gate": topic_gate.model_dump(mode="json"),
        "channel_fit_gate": channel_fit.model_dump(mode="json"),
        "niche_alignment_dossier": dossier.model_dump(mode="json"),
    }


def _resolve_nich1_admission_lineage(
    session: Session,
    *,
    daily_run: ChannelDailyRun,
    idea: DailyIdeaDecision,
    preflight: IdeaMarketPreflight | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    snapshot = session.get(CompiledChannelPolicySnapshot, daily_run.policy_snapshot_id)
    if snapshot is None or not _is_nich1_strict_snapshot(snapshot):
        return None, []
    blockers: list[str] = []
    if preflight is None:
        return None, ["NICH1_PREFLIGHT_REQUIRED"]
    if (
        preflight.company_id != daily_run.company_id
        or preflight.channel_workspace_id != daily_run.channel_workspace_id
        or preflight.channel_daily_run_id != daily_run.id
        or preflight.daily_idea_decision_id != idea.id
    ):
        return None, ["NICH1_PREFLIGHT_SCOPE_MISMATCH"]
    pack = session.get(ContextPackSnapshot, idea.context_pack_snapshot_id)
    if pack is None:
        return None, ["NICHE_CONTRACT_DIGEST_MISSING"]
    try:
        _validate_nich1_daily_context_authority(
            session,
            context_pack=pack,
            snapshot=snapshot,
        )
        digest, digest_ref = _niche_digest_from_context(pack)
    except ValidationFailureError:
        return None, ["NICH1_DAILY_CONTEXT_AUTHORITY_INVALID"]
    evidence = preflight.evidence_blob or {}
    topic_gate_raw = evidence.get("topic_niche_alignment_gate")
    channel_fit_raw = evidence.get("channel_fit_gate")
    dossier_raw = evidence.get("niche_alignment_dossier")
    try:
        topic_gate_result = NicheGateResult.model_validate(topic_gate_raw)
    except ValidationError:
        topic_gate_result = None
    try:
        channel_fit_result = ChannelFitEvaluation.model_validate(channel_fit_raw)
    except ValidationError:
        channel_fit_result = None
    try:
        dossier_result = NicheAlignmentDossier.model_validate(dossier_raw)
    except ValidationError:
        dossier_result = None
    if (
        topic_gate_result is None
        or topic_gate_result.gate_key != NicheGateKey.TOPIC
        or topic_gate_result.verdict != NicheGateVerdict.PASS
    ):
        blockers.append("TOPIC_NICHE_ALIGNMENT_GATE_NOT_PASS")
    if (
        preflight.decision != "PASS"
        or preflight.policy_fit_state != "PASS"
        or channel_fit_result is None
        or channel_fit_result.channel_fit_result != NicheGateVerdict.PASS
        or channel_fit_result.policy_fit_state != NicheGateVerdict.PASS
    ):
        blockers.append("CHANNEL_FIT_NOT_PASS")
    evidence_refs = evidence.get("evidence_refs")
    expected_idea_evidence = _niche_evidence_ref(
        idea=idea,
        evidence_refs=(
            [item for item in evidence_refs if isinstance(item, dict)]
            if isinstance(evidence_refs, list)
            else []
        ),
    )
    expected_idea_ref = f"daily-idea-decision://{idea.id}"
    expected_evidence_marker = (
        expected_idea_evidence.type,
        expected_idea_evidence.ref,
        expected_idea_evidence.content_hash,
    )
    if topic_gate_result is not None:
        topic_evidence_markers = {
            (item.type, item.ref, item.content_hash)
            for item in topic_gate_result.evidence_refs
        }
        if (
            topic_gate_result.subject_ref != expected_idea_ref
            or topic_gate_result.subject_hash != expected_idea_evidence.content_hash
            or topic_gate_result.niche_contract_digest_ref != str(digest_ref.get("ref"))
            or topic_gate_result.niche_contract_digest_hash != digest.content_hash
            or topic_gate_result.checked_policy_snapshot_ref
            != digest.compiled_policy_snapshot_ref
            or topic_gate_result.checked_policy_snapshot_hash
            != digest.compiled_policy_snapshot_hash
            or expected_evidence_marker not in topic_evidence_markers
        ):
            blockers.append("TOPIC_NICHE_ALIGNMENT_GATE_BINDING_MISMATCH")
    if channel_fit_result is not None and topic_gate_result is not None:
        fit_evidence_markers = {
            (item.type, item.ref, item.content_hash)
            for item in channel_fit_result.evidence_refs
        }
        if (
            set(channel_fit_result.required_gate_keys) != {NicheGateKey.TOPIC}
            or channel_fit_result.gate_result_hashes.get(NicheGateKey.TOPIC)
            != topic_gate_result.content_hash
            or expected_evidence_marker not in fit_evidence_markers
        ):
            blockers.append("CHANNEL_FIT_GATE_BINDING_MISMATCH")
    if (
        dossier_result is None
        or dossier_result.dossier_scope != NicheDossierScope.PRE_ADMISSION
        or dossier_result.overall_verdict != NicheGateVerdict.PASS
        or dossier_result.topic_result is None
        or topic_gate_result is None
        or dossier_result.topic_result.content_hash != topic_gate_result.content_hash
        or dossier_result.niche_contract_digest_ref != str(digest_ref.get("ref"))
        or dossier_result.niche_contract_digest_hash != digest.content_hash
        or dossier_result.compiled_policy_snapshot_ref
        != digest.compiled_policy_snapshot_ref
        or dossier_result.compiled_policy_snapshot_hash
        != digest.compiled_policy_snapshot_hash
        or channel_fit_result is None
        or dossier_result.channel_fit_result != channel_fit_result.channel_fit_result
        or dossier_result.channel_fit_score != channel_fit_result.channel_fit_score
        or dossier_result.channel_fit_threshold != channel_fit_result.channel_fit_threshold
    ):
        blockers.append("NICHE_ALIGNMENT_DOSSIER_BINDING_MISMATCH")
    slot = session.get(EditorialCalendarSlot, daily_run.editorial_calendar_slot_id) if daily_run.editorial_calendar_slot_id else None
    if slot is None or slot.category_id != digest.category_id:
        blockers.append("NICHE_SLOT_CATEGORY_MISMATCH")
    else:
        if idea.proposed_pillar and idea.proposed_pillar.casefold() != digest.content_pillar_key.casefold():
            blockers.append("NICHE_IDEA_PILLAR_MISMATCH")
        if idea.proposed_series_key and idea.proposed_series_key.casefold() != digest.series_key.casefold():
            blockers.append("NICHE_IDEA_SERIES_MISMATCH")
    if blockers:
        return None, list(dict.fromkeys(blockers))
    assert slot is not None
    assert topic_gate_result is not None
    assert channel_fit_result is not None
    topic_gate = topic_gate_result.model_dump(mode="json")
    channel_fit = channel_fit_result.model_dump(mode="json")
    frozen = {
        "schema_version": "nich1.project-frozen-lineage.v1",
        "channel_id": str(idea.channel_workspace_id),
        "channel_profile_version_ref": digest.channel_profile_version_ref,
        "channel_profile_version_hash": digest.channel_profile_version_hash,
        "compiled_policy_snapshot_ref": digest.compiled_policy_snapshot_ref,
        "compiled_policy_snapshot_hash": digest.compiled_policy_snapshot_hash,
        "channel_contract_ref": digest.channel_contract_ref,
        "channel_contract_hash": digest.channel_contract_hash,
        "niche_contract_digest": digest.model_dump(mode="json"),
        "niche_contract_digest_ref": digest_ref,
        "daily_idea_decision_ref": {
            "type": "daily_idea_decision",
            "id": str(idea.id),
            "ref": topic_gate.get("subject_ref"),
            "content_hash": topic_gate.get("subject_hash"),
        },
        "editorial_slot_ref": {
            "type": "editorial_calendar_slot",
            "id": str(slot.id),
            "content_hash": digest.editorial_slot_hash,
        },
        "category_id": str(digest.category_id),
        "content_pillar": digest.content_pillar_key,
        "series_key": digest.series_key,
        "production_goal": digest.production_goal,
        "topic": idea.proposed_title,
        "angle": idea.proposed_angle,
        "topic_niche_alignment_gate": topic_gate,
        "channel_fit_gate": channel_fit,
        "niche_alignment_dossier": evidence.get("niche_alignment_dossier"),
    }
    return {
        "frozen_lineage": frozen,
        "readiness_gate_refs": [
            {
                "type": "topic_niche_alignment_gate",
                "content_hash": topic_gate.get("content_hash"),
                "verdict": "PASS",
            },
            {
                "type": "channel_fit_gate",
                "content_hash": channel_fit.get("content_hash"),
                "verdict": "PASS",
            },
            digest_ref,
        ],
    }, []


def _evaluate_preflight(data: IdeaMarketPreflightCreate, evidence_refs: list[dict[str, Any]]) -> tuple[str, list[str], str, Decimal | None]:
    if data.policy_fit_state == "BLOCK":
        return "BLOCK", ["IDEA_BLOCKED"], "HIGH", data.demand_score
    search_led = bool(data.evidence_blob.get("search_led", True))
    if not evidence_refs and not search_led:
        return "PASS", ["SEARCH_VOLUME_UNKNOWN"], "UNKNOWN", data.demand_score
    if not evidence_refs:
        return "REVIEW_REQUIRED", ["SEARCH_DEMAND_EVIDENCE_MISSING", "DEMAND_EVIDENCE_WEAK"], "LOW", data.demand_score
    demand_score = data.demand_score if data.demand_score is not None else _score_from_evidence_refs(evidence_refs)
    if demand_score is None:
        return "REVIEW_REQUIRED", ["SEARCH_VOLUME_UNKNOWN", "DEMAND_EVIDENCE_WEAK"], "LOW", demand_score
    if demand_score < Decimal("10"):
        return "BLOCK", ["SEARCH_VOLUME_LOW", "IDEA_BLOCKED"], "MEDIUM", demand_score
    if demand_score < Decimal("30"):
        return "REVIEW_REQUIRED", ["SEARCH_VOLUME_LOW", "DEMAND_EVIDENCE_WEAK"], "MEDIUM", demand_score
    if any(_decimal_or_none(ref.get("competition_index")) is not None and _decimal_or_none(ref.get("competition_index")) >= Decimal("0.85") for ref in evidence_refs):
        return "REVIEW_REQUIRED", ["COMPETITION_HIGH", "IDEA_REVIEW_REQUIRED"], "MEDIUM", demand_score
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


def _admission_result(
    idea: DailyIdeaDecision,
    preflight: IdeaMarketPreflight | None,
    budget_gate_result: dict[str, Any],
) -> tuple[str, list[str]]:
    if idea.decision_status in {"BLOCKED", "REJECTED", "SKIPPED"}:
        return "BLOCK", ["IDEA_BLOCKED"]
    budget_decision = budget_gate_result.get("decision")
    if budget_decision == "BLOCK":
        return "BLOCK", ["COST_BUDGET_BLOCKED", "IDEA_BLOCKED"]
    if budget_decision == "REVIEW_REQUIRED":
        return "REVIEW_REQUIRED", ["BUDGET_REVIEW_REQUIRED", "IDEA_REVIEW_REQUIRED"]
    if preflight is None:
        return "REVIEW_REQUIRED", ["IDEA_REVIEW_REQUIRED", "DEMAND_EVIDENCE_WEAK"]
    if preflight.decision == "BLOCK":
        return "BLOCK", ["IDEA_BLOCKED", *preflight.reason_codes]
    if preflight.decision == "REVIEW_REQUIRED":
        return "REVIEW_REQUIRED", ["IDEA_REVIEW_REQUIRED", *preflight.reason_codes]
    if preflight.decision == "SKIPPED":
        return "SKIP", ["IDEA_REVIEW_REQUIRED"]
    return "ADMIT", ["IDEA_ADMITTED"]


def _provider_attempt_status(response: Any) -> str:
    if response.ok:
        return "SUCCESS"
    if response.error_code == "PROVIDER_QUOTA_EXCEEDED":
        return "QUOTA_REJECTED"
    if response.error_code == "CIRCUIT_BREAKER_OPEN":
        return "CIRCUIT_OPEN"
    if response.retryable:
        return "RETRYABLE_FAILURE"
    return "NON_RETRYABLE_FAILURE"


def _record_provider_attempt(
    session: Session,
    *,
    provider_key: str,
    operation_key: str,
    target_type: str,
    target_id: uuid.UUID,
    status: str,
    error_code: str | None,
    latency_ms: int | None,
    metadata: dict[str, Any],
    correlation_id: str,
    cost_event_id: uuid.UUID | None = None,
    quota_event_id: uuid.UUID | None = None,
) -> ProviderAttempt:
    attempt = ProviderAttempt(
        provider_key=provider_key,
        operation_key=operation_key,
        target_type=target_type,
        target_id=target_id,
        attempt_number=1,
        status=status,
        error_code=error_code,
        error_message_redacted="redacted provider error" if error_code else None,
        started_at=utc_now(),
        finished_at=utc_now(),
        latency_ms=latency_ms,
        cost_event_id=cost_event_id,
        quota_event_id=quota_event_id,
        metadata_=metadata,
    )
    session.add(attempt)
    session.flush()
    reason_code = _provider_attempt_reason_code(status, error_code)
    _record_m5_event(
        session,
        event_type="provider_attempt.created",
        aggregate_type="provider_attempt",
        aggregate_id=attempt.id,
        actor_id=None,
        target_type="provider_attempt",
        target_id=attempt.id,
        company_id=None,
        correlation_id=correlation_id,
        reason_code=reason_code,
        payload={
            "provider_key": attempt.provider_key,
            "operation_key": attempt.operation_key,
            "status": attempt.status,
            "error_code": attempt.error_code,
            "target_type": attempt.target_type,
            "target_id": str(attempt.target_id) if attempt.target_id else None,
        },
    )
    return attempt


def _provider_attempt_reason_code(status: str, error_code: str | None) -> str:
    if status == "SUCCESS":
        return "PROVIDER_ATTEMPT_SUCCEEDED"
    if error_code == "LLM_SCHEMA_VALIDATION_FAILED":
        return "LLM_SCHEMA_VALIDATION_FAILED"
    if error_code == "MALFORMED_OUTPUT":
        return "LLM_OUTPUT_MALFORMED"
    if status == "QUOTA_REJECTED":
        return "PROVIDER_QUOTA_BLOCKED"
    return "PROVIDER_HEALTH_BLOCKED"


def _validated_authority_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    proposal = MockAuthorityProposal.model_validate(raw)
    payload = proposal.model_dump(mode="json")
    _ensure_no_secret_payload(payload)
    return payload


def _authority_source_refs(context_pack: ContextPackSnapshot) -> list[dict[str, Any]]:
    content = context_pack.pack_content
    refs: list[dict[str, Any]] = []
    slot = content.get("editorial_slot") or {}
    if _clean_text(slot.get("production_goal")):
        refs.append({"type": "editorial_calendar_slot", "id": slot.get("id"), "field": "production_goal"})
    for key in ("manual_input", "manual_fixture", "test_fixture_input", "content_runway", "content_runway_input"):
        value = content.get(key)
        if isinstance(value, dict) and _manual_seed_text(value):
            refs.append({"type": key, "id": value.get("id", "explicit_input")})
    for ref in _search_evidence_refs_from_context(context_pack):
        if _clean_text(ref.get("query")):
            refs.append({"type": "search_demand_evidence", "id": ref.get("id"), "query": ref.get("query")})
    return refs


def _authority_idea_seed(context_pack: ContextPackSnapshot) -> dict[str, Any] | None:
    content = context_pack.pack_content
    slot = content.get("editorial_slot") or {}
    goal = _clean_text(slot.get("production_goal"))
    if goal:
        return {
            "title": goal,
            "audience_problem": goal,
            "format": _clean_text(slot.get("format_hint")) or "explainer",
            "pillar": _clean_text(slot.get("content_pillar")),
            "series_key": _clean_text(slot.get("series_key")),
            "source": {"type": "editorial_calendar_slot", "id": slot.get("id"), "field": "production_goal"},
            "query": goal,
        }
    for key in ("manual_input", "manual_fixture", "test_fixture_input", "content_runway", "content_runway_input"):
        value = content.get(key)
        if isinstance(value, dict):
            seed = _manual_seed_text(value)
            if seed:
                return {
                    "title": seed,
                    "audience_problem": _clean_text(value.get("audience_problem")) or seed,
                    "format": _clean_text(value.get("format_hint")) or _clean_text(value.get("format")) or "explainer",
                    "pillar": _clean_text(value.get("content_pillar")) or _clean_text(value.get("pillar")),
                    "series_key": _clean_text(value.get("series_key")),
                    "source": {"type": key, "id": value.get("id", "explicit_input")},
                    "query": _clean_text(value.get("query")) or seed,
                }
    evidence_refs = _search_evidence_refs_from_context(context_pack)
    if evidence_refs:
        ref = evidence_refs[0]
        query = _clean_text(ref.get("query"))
        if query:
            return {
                "title": query,
                "audience_problem": query,
                "format": "explainer",
                "pillar": None,
                "series_key": None,
                "source": {"type": "search_demand_evidence", "id": ref.get("id"), "query": query},
                "query": query,
            }
    return None


def _search_evidence_refs_from_context(context_pack: ContextPackSnapshot) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in context_pack.evidence_refs:
        if ref.get("type") == "search_demand_evidence":
            refs.append(ref)
    for ref in context_pack.pack_content.get("search_demand_evidence_refs") or []:
        if ref.get("type") == "search_demand_evidence" and ref not in refs:
            refs.append(ref)
    return refs


def _manual_seed_text(value: dict[str, Any]) -> str | None:
    for key in ("production_goal", "proposed_title", "title", "idea", "topic", "query"):
        seed = _clean_text(value.get(key))
        if seed:
            return seed
    return None


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _safe_validation_errors(exc: Exception) -> list[dict[str, Any]]:
    if isinstance(exc, ValidationError):
        return [
            {"loc": [str(item) for item in error.get("loc", ())], "msg": error.get("msg", ""), "type": error.get("type", "")}
            for error in exc.errors()
        ]
    return [{"loc": [], "msg": str(exc), "type": exc.__class__.__name__}]


def _proposal_from_context(context_pack: ContextPackSnapshot, state_pack: ChannelStatePackSnapshot | None) -> dict[str, Any]:
    seed = _authority_idea_seed(context_pack)
    if seed is None:
        raise ValidationFailureError("AUTHORITY_IDEA_SOURCE_MISSING")
    title = seed["title"]
    audience_problem = seed["audience_problem"]
    evidence_refs = list(context_pack.evidence_refs)
    idea_source_refs = [
        *_authority_source_refs(context_pack),
        {"type": "context_pack_snapshot", "id": str(context_pack.id), "pack_hash": context_pack.pack_hash},
    ]
    if state_pack is not None:
        idea_source_refs.append({"type": "channel_state_pack_snapshot", "id": str(state_pack.id), "state_hash": state_pack.state_hash})
    return {
        "proposed_title": title,
        "proposed_angle": f"Real-provider draft angle for {title}",
        "proposed_format": seed["format"],
        "proposed_pillar": seed["pillar"],
        "proposed_series_key": seed["series_key"],
        "audience_problem": audience_problem,
        "search_intent_hypothesis": {"query": seed["query"], "source": seed["source"]["type"]},
        "rationale": {
            "mode": "NOT_CONFIGURED",
            "context_pack_snapshot_id": str(context_pack.id),
            "context_pack_hash": context_pack.pack_hash,
            "channel_state_pack_snapshot_id": str(state_pack.id) if state_pack else None,
            "numeric_truth": "SQL_OR_UNKNOWN",
            "authority_source": seed["source"],
        },
        "evidence_refs": evidence_refs,
        "confidence": "MEDIUM" if evidence_refs else "UNKNOWN",
        "idea_source_refs": idea_source_refs,
    }


def _initial_artifact_content(artifact_type: str, idea: DailyIdeaDecision) -> dict[str, Any]:
    if artifact_type == "creative_brief":
        return CreativeBriefDraft.model_validate({
            "title": idea.proposed_title,
            "angle": idea.proposed_angle,
            "format": idea.proposed_format,
            "pillar": idea.proposed_pillar,
            "series_key": idea.proposed_series_key,
            "rationale": idea.rationale,
            "status": "draft",
        }).model_dump(mode="json")
    if artifact_type == "research_pack":
        return ResearchPackDraft.model_validate({
            "evidence_refs": idea.evidence_refs,
            "context_pack_snapshot_id": str(idea.context_pack_snapshot_id),
            "numeric_truth": "SQL_OR_UNKNOWN",
            "status": "draft",
        }).model_dump(mode="json")
    return SourcePackDraft.model_validate({
        "source_refs": idea.evidence_refs,
        "context_pack_snapshot_id": str(idea.context_pack_snapshot_id),
        "status": "draft",
    }).model_dump(mode="json")


def _prompt_stub(context_pack: ContextPackSnapshot) -> str:
    return f"m5_authority:{context_pack.id}:{context_pack.pack_hash}"


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
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS) and normalized != "secret_ref":
            raise ValidationFailureError(f"secret-like payload key is not allowed: {key}")
        if isinstance(item, str) and any(marker in item for marker in RAW_SECRET_MARKERS):
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
