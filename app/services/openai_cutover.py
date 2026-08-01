"""Authenticated OpenAI cutover and bounded canary authority.

This module deliberately owns configuration/readiness evidence only.  It does
not fabricate provider health and it never substitutes a model when a lane
fails.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    BudgetGateCheckRequest,
    BudgetPolicyCreate,
    CostEventCreate,
    CredentialReferenceCreate,
    EventEnvelope,
    ProviderRegistryEntryCreate,
    QuotaAccountCreate,
    QuotaEventRequest,
)
from app.core.actor import ActorContext
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    BudgetPolicy,
    CredentialReference,
    LLMRouteAttempt,
    LLMRunSnapshot,
    OpenAICanaryArtifact,
    OpenAICutoverReceipt,
    OpenAIPricingSnapshot,
    ProviderRegistryEntry,
    QuotaAccount,
)
from app.services.company_access import require_company_permission
from app.services.domain_events import DomainEventBus
from app.services.m10_1 import (
    FINAL_LANES,
    OPENAI_PRICING_VERSION,
    OPENAI_STANDARD_PRICING_PER_MILLION,
    LLMRouterConfigLoader,
    LLMRouterService,
)
from app.services.ops import (
    BudgetGateService,
    CostService,
    CredentialReferenceService,
    ProviderRegistryService,
    QuotaService,
)


OPENAI_PROVIDER_KEY = "openai"
OPENAI_CREDENTIAL_KEY = "openai_api_key"
OPENAI_SERVICE_TIER = "standard"
OPENAI_MONTHLY_HARD_CAP_USD = Decimal("12.00")
OPENAI_INITIAL_PREPAID_TARGET_USD = Decimal("15.00")
OPENAI_CANARY_RESERVATION_USD = Decimal("0.05")
OPENAI_CANARY_HARD_CAP_USD = Decimal("2.00")
OPENAI_PRICING_EVIDENCE_REF = "https://developers.openai.com/api/docs/models/compare"


@dataclass(frozen=True, slots=True)
class OpenAICutoverAuthority:
    provider: ProviderRegistryEntry
    credential: CredentialReference
    pricing_snapshot: OpenAIPricingSnapshot
    budget_policy: BudgetPolicy
    quota_account: QuotaAccount
    receipt: OpenAICutoverReceipt


@dataclass(frozen=True, slots=True)
class CanaryResult:
    receipt_id: uuid.UUID
    total: int
    succeeded: int
    failed: int
    skipped_idempotent: int
    actual_cost_usd: Decimal
    status: str


CANARY_INVENTORY: tuple[dict[str, str], ...] = (
    # Luna: eight low-cost, known-schema tasks.
    {"artifact_key": "topic-scoring", "lane_name": "cheap_structured"},
    {"artifact_key": "publishing-metadata", "lane_name": "cheap_structured"},
    {"artifact_key": "publish-timing-summary", "lane_name": "cheap_structured"},
    {"artifact_key": "provider-readiness-summary", "lane_name": "cheap_structured"},
    {"artifact_key": "media-qc-explanation", "lane_name": "cheap_structured"},
    {"artifact_key": "upload-card-copy", "lane_name": "cheap_structured"},
    {"artifact_key": "structured-extraction", "lane_name": "cheap_structured"},
    {"artifact_key": "structured-classification", "lane_name": "cheap_structured"},
    # Terra: fourteen representative planning/review tasks.
    {"artifact_key": "research-summary", "lane_name": "long_context_text"},
    {"artifact_key": "script-plan", "lane_name": "long_context_text"},
    {"artifact_key": "script-generation", "lane_name": "long_context_text"},
    {"artifact_key": "script-rewrite", "lane_name": "long_context_text"},
    {"artifact_key": "long-context-reasoning", "lane_name": "long_context_text"},
    {"artifact_key": "visual-plan", "lane_name": "visual_creative_review"},
    {"artifact_key": "thumbnail-brief", "lane_name": "visual_creative_review"},
    {
        "artifact_key": "contact-sheet-review",
        "lane_name": "visual_creative_review",
        "visual_input": "contact_sheet",
    },
    {"artifact_key": "gatekeeper-review", "lane_name": "gatekeeper_soft_review"},
    {"artifact_key": "rights-disclosure-review", "lane_name": "gatekeeper_soft_review"},
    {"artifact_key": "recovery-proposal", "lane_name": "gatekeeper_soft_review"},
    {"artifact_key": "factuality-review", "lane_name": "gatekeeper_soft_review"},
    {"artifact_key": "architecture-reasoning", "lane_name": "engineering_architect"},
    {"artifact_key": "creative-check", "lane_name": "default_multimodal"},
)


class OpenAICutoverService:
    """Creates immutable cutover prerequisites through existing ops services."""

    def __init__(self, session: Session, *, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def establish_authority(
        self,
        *,
        actor: ActorContext,
        company_id: uuid.UUID,
    ) -> OpenAICutoverAuthority:
        require_company_permission(
            self.session,
            actor=actor,
            permission="ops.manage",
            company_id=company_id,
        )
        require_company_permission(
            self.session,
            actor=actor,
            permission="provider.execute",
            company_id=company_id,
        )
        if self.settings.llm_provider != "openai":
            raise ValidationFailureError("OPENAI_CUTOVER_PROVIDER_CONFIG_REQUIRED")

        # This is a configuration write, not an external API call.  Seeding the
        # router also freezes the exact six lanes and the model-capability rows.
        LLMRouterConfigLoader(self.session).ensure_default_profile()
        provider = self._ensure_provider()
        credential = self._ensure_credential()
        pricing_snapshot = self._ensure_pricing_snapshot(actor=actor)
        budget_policy = self._ensure_budget_policy(company_id=company_id)
        quota_account = self._ensure_quota_account(company_id=company_id)
        receipt = self._ensure_receipt(
            actor=actor,
            provider=provider,
            credential=credential,
            pricing_snapshot=pricing_snapshot,
            budget_policy=budget_policy,
        )
        return OpenAICutoverAuthority(
            provider=provider,
            credential=credential,
            pricing_snapshot=pricing_snapshot,
            budget_policy=budget_policy,
            quota_account=quota_account,
            receipt=receipt,
        )

    def run_canary(
        self,
        *,
        actor: ActorContext,
        company_id: uuid.UUID,
        receipt_id: uuid.UUID,
        router: LLMRouterService | None = None,
    ) -> CanaryResult:
        authority = self.establish_authority(actor=actor, company_id=company_id)
        if authority.receipt.id != receipt_id:
            receipt = self.session.get(OpenAICutoverReceipt, receipt_id)
            if receipt is None:
                raise NotFoundError("OPENAI_CUTOVER_RECEIPT_NOT_FOUND")
            if receipt.status not in {"READY", "BLOCKED"}:
                raise ValidationFailureError("OPENAI_CUTOVER_RECEIPT_NOT_RUNNABLE")
        else:
            receipt = authority.receipt
        if receipt.status == "BLOCKED":
            raise ValidationFailureError("OPENAI_CUTOVER_CREDENTIAL_NOT_CONFIGURED")
        if not self._api_key_present():
            receipt.status = "BLOCKED"
            self.session.flush()
            raise ValidationFailureError("OPENAI_CREDENTIAL_MISSING")

        route_service = router or LLMRouterService(self.session)
        total = succeeded = failed = skipped_idempotent = 0
        total_cost = Decimal("0")
        for item in CANARY_INVENTORY:
            total += 1
            artifact, created = self._get_or_create_canary_artifact(
                receipt=receipt, item=item
            )
            if not created and artifact.status == "SUCCESS":
                skipped_idempotent += 1
                total_cost += artifact.actual_cost_usd or Decimal("0")
                continue
            if not self._reserve_canary_budget(
                authority=authority, artifact=artifact, lane_name=item["lane_name"]
            ):
                artifact.status = "FAILED"
                self.session.flush()
                failed += 1
                continue
            result = route_service.route(
                lane_name=item["lane_name"],
                requested_task_type=f"canary:{item['artifact_key']}",
                prompt=_canary_prompt(item["artifact_key"]),
                image_inputs=_canary_image_inputs(item),
                response_format="json",
                correlation_id=f"openai-canary:{receipt.id}:{item['artifact_key']}",
            )
            route_attempt = self.session.get(LLMRouteAttempt, result.route_attempt_id)
            accepted = _accepted_canary_output(
                result.structured_output, artifact_key=item["artifact_key"]
            )
            actual_cost = route_attempt.actual_cost_usd if route_attempt else None
            artifact.llm_route_attempt_id = result.route_attempt_id
            artifact.response_hash = (
                route_attempt.response_hash if route_attempt else None
            )
            if result.status != "SUCCESS" or not accepted or actual_cost is None:
                artifact.status = "FAILED"
                artifact.repair_count += 1
                self._release_reservation(authority.quota_account, artifact)
                failed += 1
                if (
                    route_attempt
                    and route_attempt.error_code == "OPENAI_CREDENTIAL_REJECTED"
                ):
                    self._record_credential_rejection(
                        receipt=receipt,
                        credential=authority.credential,
                        actor=actor,
                    )
                    self.session.flush()
                    break
                self.session.flush()
                continue
            self._settle_actual_cost(
                authority=authority,
                artifact=artifact,
                route_attempt=route_attempt,
                actual_cost=actual_cost,
            )
            artifact.actual_cost_usd = actual_cost
            artifact.status = "SUCCESS"
            total_cost += actual_cost
            succeeded += 1
            self.session.flush()

        statuses = list(
            self.session.scalars(
                select(OpenAICanaryArtifact.status).where(
                    OpenAICanaryArtifact.cutover_receipt_id == receipt.id
                )
            ).all()
        )
        receipt.status = (
            "CANARY_PASSED"
            if len(statuses) == len(CANARY_INVENTORY)
            and all(status == "SUCCESS" for status in statuses)
            else "BLOCKED"
        )
        self.session.flush()
        return CanaryResult(
            receipt_id=receipt.id,
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped_idempotent=skipped_idempotent,
            actual_cost_usd=total_cost,
            status=receipt.status,
        )

    def reconcile_credential_rejection(
        self,
        *,
        actor: ActorContext,
        company_id: uuid.UUID,
        receipt_id: uuid.UUID,
    ) -> OpenAICutoverReceipt:
        """Persist an already-recorded 401 without issuing another request."""

        require_company_permission(
            self.session,
            actor=actor,
            permission="ops.manage",
            company_id=company_id,
        )
        receipt = self.session.get(OpenAICutoverReceipt, receipt_id)
        if receipt is None:
            raise NotFoundError("OPENAI_CUTOVER_RECEIPT_NOT_FOUND")
        rejected = self.session.scalar(
            select(LLMRouteAttempt.id)
            .join(
                OpenAICanaryArtifact,
                OpenAICanaryArtifact.llm_route_attempt_id == LLMRouteAttempt.id,
            )
            .where(OpenAICanaryArtifact.cutover_receipt_id == receipt.id)
            .where(LLMRouteAttempt.error_code == "OPENAI_CREDENTIAL_REJECTED")
            .limit(1)
        )
        if rejected is not None:
            credential = self.session.get(
                CredentialReference, receipt.credential_reference_id
            )
            if credential is None:
                raise ValidationFailureError("OPENAI_CREDENTIAL_REFERENCE_MISSING")
            self._record_credential_rejection(
                receipt=receipt,
                credential=credential,
                actor=actor,
            )
        return receipt

    def authorize_rotated_credential(
        self,
        *,
        actor: ActorContext,
        company_id: uuid.UUID,
        receipt_id: uuid.UUID,
    ) -> CredentialReference:
        """Permit one new canary only after an operator rotates the secret.

        A rejected credential remains fail-closed across normal authority
        reconciliation.  This explicit, authenticated transition is the only
        supported way to make it runnable again; it never inspects or stores
        the credential value itself.
        """

        require_company_permission(
            self.session,
            actor=actor,
            permission="ops.manage",
            company_id=company_id,
        )
        require_company_permission(
            self.session,
            actor=actor,
            permission="provider.execute",
            company_id=company_id,
        )
        if not self._api_key_present():
            raise ValidationFailureError("OPENAI_CREDENTIAL_MISSING")

        receipt = self.session.get(OpenAICutoverReceipt, receipt_id)
        if receipt is None:
            raise NotFoundError("OPENAI_CUTOVER_RECEIPT_NOT_FOUND")
        credential = self.session.get(
            CredentialReference, receipt.credential_reference_id
        )
        if credential is None:
            raise ValidationFailureError("OPENAI_CREDENTIAL_REFERENCE_MISSING")
        if (
            credential.provider_key != OPENAI_PROVIDER_KEY
            or credential.credential_key != OPENAI_CREDENTIAL_KEY
            or credential.secret_ref != "env:OPENAI_API_KEY"
        ):
            raise ValidationFailureError("OPENAI_CREDENTIAL_REFERENCE_CONTRADICTORY")
        if credential.status != "REVOKED":
            raise ValidationFailureError("OPENAI_CREDENTIAL_ROTATION_NOT_REQUIRED")

        credential.status = "CONFIGURED"
        metadata = dict(credential.metadata_ or {})
        metadata["rotation_authorized_at"] = utc_now().isoformat()
        metadata["rotation_authorized_by_user_id"] = str(actor.actor_id)
        credential.metadata_ = metadata
        _record_cutover_event(
            self.session,
            event_type="openai_cutover.credential_rotation_authorized",
            aggregate_type="openai_cutover_receipt",
            aggregate_id=receipt.id,
            actor_id=actor.actor_id,
            payload={"credential_reference_id": str(credential.id)},
        )
        self.session.flush()
        return credential

    def _ensure_provider(self) -> ProviderRegistryEntry:
        service = ProviderRegistryService(self.session)
        existing = service.get_entry(OPENAI_PROVIDER_KEY)
        if existing is not None:
            if existing.status != "ACTIVE" or existing.provider_type != "LLM":
                raise ValidationFailureError("OPENAI_PROVIDER_REGISTRY_CONTRADICTORY")
            return existing
        return service.create_entry(
            data=ProviderRegistryEntryCreate(
                provider_key=OPENAI_PROVIDER_KEY,
                provider_name="OpenAI Responses API",
                provider_type="LLM",
                status="ACTIVE",
                capability_blob={
                    "models": _model_capabilities(),
                    "responses_api": True,
                    "automatic_model_fallback": False,
                },
                policy_fit_blob={
                    "service_tier": OPENAI_SERVICE_TIER,
                    "fast_mode": False,
                    "premium_override": None,
                    "automatic_premium_fallback": False,
                },
                cost_model_blob={
                    "pricing_version": OPENAI_PRICING_VERSION,
                    "evidence_ref": OPENAI_PRICING_EVIDENCE_REF,
                },
                quota_model_blob={
                    "monthly_hard_cap_usd": str(OPENAI_MONTHLY_HARD_CAP_USD)
                },
                retry_policy_blob={"model_fallback": False},
                metadata={"credential_env": "OPENAI_API_KEY"},
            ),
            correlation_id="openai-cutover-provider-registry",
        )

    def _ensure_credential(self) -> CredentialReference:
        existing = self.session.scalars(
            select(CredentialReference)
            .where(CredentialReference.provider_key == OPENAI_PROVIDER_KEY)
            .where(CredentialReference.credential_key == OPENAI_CREDENTIAL_KEY)
        ).one_or_none()
        expected_status = "CONFIGURED" if self._api_key_present() else "MISSING"
        if existing is not None:
            if existing.secret_ref != "env:OPENAI_API_KEY":
                raise ValidationFailureError(
                    "OPENAI_CREDENTIAL_REFERENCE_CONTRADICTORY"
                )
            if existing.status != "REVOKED":
                existing.status = expected_status
            return existing
        return CredentialReferenceService(self.session).create_reference(
            data=CredentialReferenceCreate(
                provider_key=OPENAI_PROVIDER_KEY,
                credential_key=OPENAI_CREDENTIAL_KEY,
                credential_type="API_KEY",
                secret_ref="env:OPENAI_API_KEY",
                status=expected_status,
                metadata={"environment_handle_only": True},
            ),
            correlation_id="openai-cutover-credential-reference",
        )

    def _ensure_pricing_snapshot(self, *, actor: ActorContext) -> OpenAIPricingSnapshot:
        existing = self.session.scalars(
            select(OpenAIPricingSnapshot).where(
                OpenAIPricingSnapshot.snapshot_version == OPENAI_PRICING_VERSION
            )
        ).one_or_none()
        expected_hash = _hash(_pricing_payload())
        if existing is not None:
            if (
                existing.status != "APPROVED"
                or existing.canonical_hash != expected_hash
                or existing.approved_by_user_id is None
            ):
                raise ValidationFailureError("OPENAI_PRICING_SNAPSHOT_NOT_APPROVED")
            return existing
        snapshot = OpenAIPricingSnapshot(
            snapshot_version=OPENAI_PRICING_VERSION,
            provider_key="OPENAI",
            service_tier=OPENAI_SERVICE_TIER,
            pricing_blob=_pricing_payload(),
            evidence_ref=OPENAI_PRICING_EVIDENCE_REF,
            canonical_hash=expected_hash,
            status="APPROVED",
            approved_by_user_id=actor.actor_id,
            approved_at=utc_now(),
        )
        self.session.add(snapshot)
        self.session.flush()
        _record_cutover_event(
            self.session,
            event_type="openai_pricing_snapshot.approved",
            aggregate_type="openai_pricing_snapshot",
            aggregate_id=snapshot.id,
            actor_id=actor.actor_id,
            payload={"snapshot_version": snapshot.snapshot_version},
        )
        return snapshot

    def _ensure_budget_policy(self, *, company_id: uuid.UUID) -> BudgetPolicy:
        policy_key = _budget_policy_key(company_id)
        service = BudgetGateService(self.session)
        existing = service.get_policy(policy_key)
        if existing is not None:
            if existing.status != "ACTIVE":
                raise ValidationFailureError("OPENAI_BUDGET_POLICY_NOT_ACTIVE")
            return existing
        return service.create_policy(
            data=BudgetPolicyCreate(
                policy_key=policy_key,
                scope_type="COMPANY",
                scope_id=company_id,
                status="ACTIVE",
                policy_blob={
                    "monthly_hard_cap_usd": str(OPENAI_MONTHLY_HARD_CAP_USD),
                    "initial_prepaid_target_usd": str(
                        OPENAI_INITIAL_PREPAID_TARGET_USD
                    ),
                    "service_tier": OPENAI_SERVICE_TIER,
                    "fast_mode": False,
                    "premium_override": None,
                    "automatic_premium_fallback": False,
                    "canary_hard_cap_usd": str(OPENAI_CANARY_HARD_CAP_USD),
                    "per_lane_cap_usd": "1.00",
                },
            ),
            correlation_id="openai-cutover-budget-policy",
        )

    def _ensure_quota_account(self, *, company_id: uuid.UUID) -> QuotaAccount:
        existing = self.session.scalars(
            select(QuotaAccount)
            .where(QuotaAccount.provider_key == OPENAI_PROVIDER_KEY)
            .where(QuotaAccount.quota_scope_type == "COMPANY")
            .where(QuotaAccount.quota_scope_id == company_id)
            .where(QuotaAccount.quota_window == "MONTHLY")
            .where(QuotaAccount.unit == "USD")
        ).one_or_none()
        if existing is not None:
            return existing
        return QuotaService(self.session).create_account(
            data=QuotaAccountCreate(
                provider_key=OPENAI_PROVIDER_KEY,
                quota_scope_type="COMPANY",
                quota_scope_id=company_id,
                quota_window="MONTHLY",
                quota_limit=OPENAI_MONTHLY_HARD_CAP_USD,
                unit="USD",
                status="ACTIVE",
                metadata={
                    "prepaid_target_usd": str(OPENAI_INITIAL_PREPAID_TARGET_USD),
                    "account_balance_verified": False,
                },
            ),
            correlation_id="openai-cutover-monthly-quota",
        )

    def _ensure_receipt(
        self,
        *,
        actor: ActorContext,
        provider: ProviderRegistryEntry,
        credential: CredentialReference,
        pricing_snapshot: OpenAIPricingSnapshot,
        budget_policy: BudgetPolicy,
    ) -> OpenAICutoverReceipt:
        lane_mapping_hash = _hash(_lane_mapping_payload())
        existing = self.session.scalars(
            select(OpenAICutoverReceipt).where(
                OpenAICutoverReceipt.lane_mapping_hash == lane_mapping_hash
            )
        ).one_or_none()
        if existing is not None:
            desired_status = "READY" if credential.status == "CONFIGURED" else "BLOCKED"
            if (
                existing.status in {"READY", "BLOCKED"}
                and existing.status != desired_status
            ):
                existing.status = desired_status
            return existing
        status = "READY" if credential.status == "CONFIGURED" else "BLOCKED"
        receipt = OpenAICutoverReceipt(
            provider_registry_entry_id=provider.id,
            pricing_snapshot_id=pricing_snapshot.id,
            budget_policy_id=budget_policy.id,
            credential_reference_id=credential.id,
            lane_mapping_hash=lane_mapping_hash,
            canonical_hash=_hash(
                {
                    "provider_registry_entry_id": provider.id,
                    "pricing_snapshot_id": pricing_snapshot.id,
                    "budget_policy_id": budget_policy.id,
                    "credential_reference_id": credential.id,
                    "lane_mapping_hash": lane_mapping_hash,
                }
            ),
            status=status,
            created_by_user_id=actor.actor_id,
        )
        self.session.add(receipt)
        self.session.flush()
        _record_cutover_event(
            self.session,
            event_type="openai_cutover_receipt.created",
            aggregate_type="openai_cutover_receipt",
            aggregate_id=receipt.id,
            actor_id=actor.actor_id,
            payload={"status": receipt.status, "lane_mapping_hash": lane_mapping_hash},
        )
        return receipt

    def _get_or_create_canary_artifact(
        self, *, receipt: OpenAICutoverReceipt, item: dict[str, str]
    ) -> tuple[OpenAICanaryArtifact, bool]:
        existing = self.session.scalars(
            select(OpenAICanaryArtifact)
            .where(OpenAICanaryArtifact.cutover_receipt_id == receipt.id)
            .where(OpenAICanaryArtifact.artifact_key == item["artifact_key"])
        ).one_or_none()
        if existing is not None:
            return existing, False
        model_id, reasoning_effort = _lane_model_and_reasoning(item["lane_name"])
        artifact = OpenAICanaryArtifact(
            cutover_receipt_id=receipt.id,
            artifact_key=item["artifact_key"],
            lane_name=item["lane_name"],
            model_id=model_id,
            reasoning_effort=reasoning_effort,
            request_hash=_hash(_canary_prompt(item["artifact_key"])),
            is_critical=True,
            status="PENDING",
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact, True

    def _reserve_canary_budget(
        self,
        *,
        authority: OpenAICutoverAuthority,
        artifact: OpenAICanaryArtifact,
        lane_name: str,
    ) -> bool:
        decision = BudgetGateService(self.session).check(
            data=BudgetGateCheckRequest(
                policy_key=authority.budget_policy.policy_key,
                provider_key=OPENAI_PROVIDER_KEY,
                scope_type="COMPANY",
                scope_id=authority.quota_account.quota_scope_id,
                estimated_cost=OPENAI_CANARY_RESERVATION_USD,
                quota_account_id=authority.quota_account.id,
                quota_amount=OPENAI_CANARY_RESERVATION_USD,
                unit="USD",
                metadata={
                    "lane_name": lane_name,
                    "canary_artifact_id": str(artifact.id),
                },
            ),
            correlation_id=f"openai-canary-budget:{artifact.id}",
        )
        if decision.decision != "PASS":
            return False
        quota_event = QuotaService(self.session).reserve_quota(
            data=QuotaEventRequest(
                quota_account_id=authority.quota_account.id,
                amount=OPENAI_CANARY_RESERVATION_USD,
                target_type="openai_canary_artifact",
                target_id=artifact.id,
                reason_code="OPENAI_CANARY_RESERVE",
                metadata={"lane_name": lane_name},
            )
        )
        return quota_event.event_type == "RESERVE"

    def _settle_actual_cost(
        self,
        *,
        authority: OpenAICutoverAuthority,
        artifact: OpenAICanaryArtifact,
        route_attempt: LLMRouteAttempt,
        actual_cost: Decimal,
    ) -> None:
        quota = QuotaService(self.session)
        quota.consume_quota(
            data=QuotaEventRequest(
                quota_account_id=authority.quota_account.id,
                amount=actual_cost,
                target_type="openai_canary_artifact",
                target_id=artifact.id,
                reason_code="OPENAI_CANARY_ACTUAL_SETTLEMENT",
                metadata={"lane_name": artifact.lane_name},
            )
        )
        remainder = max(Decimal("0"), OPENAI_CANARY_RESERVATION_USD - actual_cost)
        if remainder:
            quota.release_quota(
                data=QuotaEventRequest(
                    quota_account_id=authority.quota_account.id,
                    amount=remainder,
                    target_type="openai_canary_artifact",
                    target_id=artifact.id,
                    reason_code="OPENAI_CANARY_RESERVATION_RELEASED",
                    metadata={"lane_name": artifact.lane_name},
                )
            )
        cost_event = CostService(self.session).record_event(
            data=CostEventCreate(
                provider_key=OPENAI_PROVIDER_KEY,
                cost_scope_type="COMPANY",
                cost_scope_id=authority.quota_account.quota_scope_id,
                amount=actual_cost,
                currency="USD",
                cost_type="ACTUAL",
                unit_count=Decimal(
                    str(
                        (route_attempt.prompt_eval_count or 0)
                        + (route_attempt.eval_count or 0)
                    )
                ),
                unit_type="TOKENS",
                provider_run_ref=route_attempt.provider_request_id,
                metadata={
                    "lane_name": artifact.lane_name,
                    "model_id": artifact.model_id,
                    "reasoning_effort": artifact.reasoning_effort,
                    "pricing_version": OPENAI_PRICING_VERSION,
                    "canary_artifact_id": str(artifact.id),
                },
            ),
            correlation_id=f"openai-canary-cost:{artifact.id}",
        )
        snapshot = self.session.get(LLMRunSnapshot, route_attempt.llm_run_snapshot_id)
        if snapshot is not None:
            snapshot.cost_event_id = cost_event.id
            snapshot.estimated_cost = actual_cost
            payload = dict(snapshot.cost_payload or {})
            payload["actual_cost_usd"] = str(actual_cost)
            snapshot.cost_payload = payload

    def _release_reservation(
        self, account: QuotaAccount, artifact: OpenAICanaryArtifact
    ) -> None:
        QuotaService(self.session).release_quota(
            data=QuotaEventRequest(
                quota_account_id=account.id,
                amount=OPENAI_CANARY_RESERVATION_USD,
                target_type="openai_canary_artifact",
                target_id=artifact.id,
                reason_code="OPENAI_CANARY_FAILED_RESERVATION_RELEASED",
            )
        )

    def _record_credential_rejection(
        self,
        *,
        receipt: OpenAICutoverReceipt,
        credential: CredentialReference,
        actor: ActorContext,
    ) -> None:
        credential.status = "REVOKED"
        receipt.status = "BLOCKED"
        _record_cutover_event(
            self.session,
            event_type="openai_cutover.credential_rejected",
            aggregate_type="openai_cutover_receipt",
            aggregate_id=receipt.id,
            actor_id=actor.actor_id,
            payload={"reason_code": "OPENAI_CREDENTIAL_REJECTED"},
        )

    def _api_key_present(self) -> bool:
        return self.settings.openai_api_key is not None and bool(
            self.settings.openai_api_key.get_secret_value()
        )


def _model_capabilities() -> dict[str, Any]:
    return {
        model_id: {
            "reasoning_effort": ["none", "low", "medium", "high"],
            "structured_outputs": True,
            "function_calling": True,
            "image_input": True,
            "context_window": 1_050_000,
            "max_output": 128_000,
            "service_tier": OPENAI_SERVICE_TIER,
            "pricing": {key: str(value) for key, value in prices.items()},
        }
        for model_id, prices in OPENAI_STANDARD_PRICING_PER_MILLION.items()
    }


def _pricing_payload() -> dict[str, Any]:
    return {
        "provider_key": "OPENAI",
        "service_tier": OPENAI_SERVICE_TIER,
        "models": _model_capabilities(),
        "pricing_version": OPENAI_PRICING_VERSION,
    }


def _lane_mapping_payload() -> list[dict[str, str]]:
    return [
        {
            "lane_name": str(lane["lane_name"]),
            "model_id": str(lane["primary_model"]),
            "reasoning_effort": str(lane["reasoning_effort"]),
            "fallback_models": "[]",
            "premium_model": "null",
        }
        for lane in FINAL_LANES
    ]


def _lane_model_and_reasoning(lane_name: str) -> tuple[str, str]:
    for lane in FINAL_LANES:
        if lane["lane_name"] == lane_name:
            return str(lane["primary_model"]), str(lane["reasoning_effort"])
    raise ValidationFailureError("OPENAI_CANARY_UNKNOWN_LANE")


def _budget_policy_key(company_id: uuid.UUID) -> str:
    return f"openai-standard-monthly-{company_id}"


def _canary_prompt(artifact_key: str) -> str:
    return (
        "VCOS OpenAI cutover canary. Return one JSON object only with exactly "
        f'"artifact_key": "{artifact_key}", "acceptance": "PASS", and '
        '"schema_version": "v1". Do not call tools or add prose.'
    )


def _canary_image_inputs(item: dict[str, str]) -> list[dict[str, str]] | None:
    """Supply a still contact-sheet probe only to the visual-review canary.

    The tiny valid PNG is a deterministic known-result fixture.  It proves the
    Responses adapter's image-input shape without ever sending raw MP4/audio
    bytes or relying on an external asset URL.
    """

    if item.get("visual_input") != "contact_sheet":
        return None
    return [
        {
            "media_type": "image/png",
            "image_url": (
                "data:image/png;base64,"
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
                "/x8AAusB9Y9JZqQAAAAASUVORK5CYII="
            ),
        }
    ]


def _accepted_canary_output(
    output: dict[str, Any] | None, *, artifact_key: str
) -> bool:
    return bool(
        isinstance(output, dict)
        and output.get("artifact_key") == artifact_key
        and output.get("acceptance") == "PASS"
        and output.get("schema_version") == "v1"
    )


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _record_cutover_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=f"openai-cutover:{aggregate_id}",
            payload={**payload, "actor_id": str(actor_id)},
        )
    )
