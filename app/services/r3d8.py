from __future__ import annotations

# Compatibility note: semantic facade `cost_firewall` re-exports this implementation; phase-coded import kept for reports/tests/backward compatibility.
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.contracts.r3d8 import (
    CostEstimateCreateRequest,
    HumanPaidRenderApprovalCreateRequest,
    HumanPaidRenderApprovalDecisionRequest,
    ProviderBoundaryDecisionRead,
    ProviderBoundaryPreflightRequest,
    ProviderIdempotencyKeyCreateRequest,
    ProviderJobCreateRequest,
    ProxyPreviewArtifactFlagCreateRequest,
    RenderRevisionCreateRequest,
)
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    CostEstimateSnapshot,
    EffectiveChannelRuntimeContextSnapshot,
    FirstScriptedVideoPackage,
    HumanPaidRenderApproval,
    PaidAttemptLimitRecord,
    PaidProviderCallLedger,
    ProviderIdempotencyKey,
    ProviderJobSnapshot,
    ProxyPreviewArtifactFlag,
    R3D4GateBatchRun,
    RenderRevision,
    VoiceProfile,
)
from app.services.m2 import PAID_CAPABILITIES, ProviderReadinessM2Service, validate_pexels_policy
from app.services.provider_stack import normalize_provider_key, provider_key_rejection_reasons


PAID_PROVIDER_KEYS = {"elevenlabs", "luma_api", "creatomate_growth_10k"}
CHARACTER_DEPENDENT_STAGES = {"AI_HERO_VIDEO", "AI_HERO_GENERATION", "AI_METAPHOR_GENERATION", "LUMA_HERO_VIDEO"}
VOICE_STAGES = {"VOICE_GENERATION", "LONG_VOICE_GENERATION", "SHORT_VOICE_GENERATION"}
PEXELS_STAGES = {"FREE_VISUAL_FALLBACK", "PEXELS_SEARCH", "PEXELS_FALLBACK"}
EXECUTION_FLAG_BY_PROVIDER = {
    "elevenlabs": "elevenlabs_real_generation_enabled",
    "luma_api": "luma_real_generation_enabled",
    "creatomate_growth_10k": "creatomate_real_render_enabled",
    "pexels_api": "pexels_real_search_enabled",
    "google_drive_archive": "google_drive_real_archive_enabled",
}


@dataclass(frozen=True)
class GateCheck:
    passed: bool
    reason_codes: list[str]
    status: str = "PASS"
    details: dict[str, Any] | None = None


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stage_key(provider_key: str, provider_stage: str) -> str:
    return f"{provider_key}:{provider_stage}".lower()


def _is_paid_provider(provider_key: str, provider_stage: str) -> bool:
    return provider_key in PAID_PROVIDER_KEYS or provider_stage in PAID_CAPABILITIES


class RenderRevisionService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, data: RenderRevisionCreateRequest) -> RenderRevision:
        package = self.session.get(FirstScriptedVideoPackage, data.package_id)
        if package is None:
            raise NotFoundError(f"package not found: {data.package_id}")
        if package.video_project_id is None:
            raise ValidationFailureError("RENDER_REVISION_REQUIRES_VIDEO_PROJECT")
        if package.effective_context_snapshot_id is None:
            raise ValidationFailureError("RENDER_REVISION_REQUIRES_EFFECTIVE_CONTEXT")
        source_refs = data.source_artifact_refs_json or _artifact_refs_from_package(package)
        gate_refs = data.gate_batch_refs_json or self._latest_gate_batch_refs(package)
        if data.supersede_previous:
            for previous in self.session.scalars(
                select(RenderRevision).where(
                    RenderRevision.package_id == package.id,
                    RenderRevision.revision_status != "SUPERSEDED",
                )
            ):
                previous.revision_status = "SUPERSEDED"
        revision_no = int(
            self.session.scalar(select(func.coalesce(func.max(RenderRevision.revision_no), 0)).where(RenderRevision.package_id == package.id))
            or 0
        ) + 1
        render_plan_hash = stable_hash(
            {
                "package_id": package.id,
                "video_project_id": package.video_project_id,
                "effective_context_snapshot_id": package.effective_context_snapshot_id,
                "source_artifact_refs_json": source_refs,
                "gate_batch_refs_json": gate_refs,
                "provider_plan_json": data.provider_plan_json,
            }
        )
        revision = RenderRevision(
            video_project_id=package.video_project_id,
            package_id=package.id,
            effective_context_snapshot_id=package.effective_context_snapshot_id,
            revision_no=revision_no,
            revision_status="READY_FOR_COST_ESTIMATE" if source_refs and gate_refs else "DRAFT",
            source_artifact_refs_json=source_refs,
            gate_batch_refs_json=gate_refs,
            render_plan_hash=render_plan_hash,
            provider_plan_json=data.provider_plan_json,
            created_by=data.created_by,
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def get(self, revision_id: uuid.UUID) -> RenderRevision:
        revision = self.session.get(RenderRevision, revision_id)
        if revision is None:
            raise NotFoundError(f"render revision not found: {revision_id}")
        return revision

    def list_for_package(self, package_id: uuid.UUID) -> list[RenderRevision]:
        return list(
            self.session.scalars(
                select(RenderRevision)
                .where(RenderRevision.package_id == package_id)
                .order_by(desc(RenderRevision.revision_no), desc(RenderRevision.created_at))
            )
        )

    def _latest_gate_batch_refs(self, package: FirstScriptedVideoPackage) -> list[dict[str, Any]]:
        batches = self.session.scalars(
            select(R3D4GateBatchRun)
            .where(R3D4GateBatchRun.package_id == package.id)
            .order_by(desc(R3D4GateBatchRun.created_at), desc(R3D4GateBatchRun.id))
            .limit(3)
        ).all()
        return [{"gate_batch_run_id": str(batch.id), "status": batch.status} for batch in batches]


class CostEstimateService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def create(self, data: CostEstimateCreateRequest) -> CostEstimateSnapshot:
        revision = RenderRevisionService(self.session).get(data.render_revision_id)
        provider_items = _planned_provider_items(revision.provider_plan_json)
        provider_map = {item.provider_key: item for item in ProviderReadinessM2Service(self.settings).snapshot().providers}
        blockers: list[str] = []
        provider_estimates: dict[str, Any] = {}
        costs = {"voice": None, "ai_hero": None, "final_render": None, "pexels": Decimal("0")}

        if not provider_items:
            blockers.extend(["PROVIDER_PLAN_EMPTY", "ESTIMATE_PENDING_PROVIDER_CONFIG"])

        for item in provider_items:
            provider_key = normalize_provider_key(item.get("provider_key")) or str(item.get("provider_key") or "").strip().lower()
            provider_stage = str(item.get("provider_stage") or item.get("stage") or "").strip().upper()
            provider = provider_map.get(provider_key)
            configured = bool(provider and provider.readiness_state in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION"})
            stale_reasons = provider_key_rejection_reasons(provider_key)
            key = _stage_key(provider_key, provider_stage)
            provider_estimates[key] = {
                "provider_key": provider_key,
                "provider_stage": provider_stage,
                "configured": configured,
                "readiness_state": "STALE_PROVIDER_KEY" if stale_reasons else provider.readiness_state if provider else "UNKNOWN",
                "estimated_cost": _decimal_string(item.get("estimated_cost")),
                "currency": data.currency,
            }
            if stale_reasons:
                blockers.extend(stale_reasons)
                continue
            if provider_key == "pexels_api":
                costs["pexels"] = Decimal("0")
                if not item.get("attribution_manifest_ref") or not item.get("usage_policy_manifest_ref"):
                    blockers.append("PEXELS_ATTRIBUTION_USAGE_MANIFEST_REQUIRED")
                continue
            if provider_key in PAID_PROVIDER_KEYS and not configured:
                blockers.append(f"{provider_key.upper()}_PROVIDER_NOT_CONFIGURED")
                continue
            explicit_cost = _decimal_or_none(item.get("estimated_cost"))
            if provider_key in PAID_PROVIDER_KEYS and explicit_cost is None:
                blockers.append(f"{provider_key.upper()}_ESTIMATE_REQUIRES_REAL_PROVIDER")
            if provider_key == "elevenlabs":
                costs["voice"] = explicit_cost
            elif provider_key == "luma_api":
                costs["ai_hero"] = explicit_cost
            elif provider_key == "creatomate_growth_10k":
                costs["final_render"] = explicit_cost

        if not provider_items:
            estimate_status = "ESTIMATE_PENDING_PROVIDER_CONFIG"
        elif any(code.endswith("_PROVIDER_NOT_CONFIGURED") for code in blockers):
            estimate_status = "ESTIMATE_PENDING_PROVIDER_CONFIG"
        elif any(code.endswith("_ESTIMATE_REQUIRES_REAL_PROVIDER") for code in blockers):
            estimate_status = "ESTIMATE_REQUIRES_REAL_PROVIDER"
        elif blockers:
            estimate_status = "BLOCKED"
        else:
            estimate_status = "ESTIMATED"

        estimated_total = None
        if estimate_status == "ESTIMATED":
            estimated_total = sum((value or Decimal("0")) for value in costs.values())
        snapshot_payload = {
            "render_revision_id": revision.id,
            "provider_estimates": provider_estimates,
            "estimate_status": estimate_status,
            "blockers": sorted(set(blockers)),
            "costs": costs,
        }
        snapshot = CostEstimateSnapshot(
            render_revision_id=revision.id,
            video_project_id=revision.video_project_id,
            package_id=revision.package_id,
            estimate_status=estimate_status,
            currency=data.currency,
            estimated_total_cost=estimated_total,
            estimated_voice_cost=costs["voice"],
            estimated_ai_hero_cost=costs["ai_hero"],
            estimated_final_render_cost=costs["final_render"],
            estimated_pexels_cost=costs["pexels"] or Decimal("0"),
            provider_estimates_json=provider_estimates,
            blocker_reason_codes_json=sorted(set(blockers)),
            content_hash=stable_hash(snapshot_payload),
        )
        self.session.add(snapshot)
        if estimate_status == "ESTIMATED":
            revision.revision_status = "COST_ESTIMATED"
        elif estimate_status == "BLOCKED":
            revision.revision_status = "BLOCKED"
        else:
            revision.revision_status = "READY_FOR_COST_ESTIMATE"
        self.session.flush()
        return snapshot

    def latest_for_revision(self, revision_id: uuid.UUID) -> CostEstimateSnapshot | None:
        return self.session.scalars(
            select(CostEstimateSnapshot)
            .where(CostEstimateSnapshot.render_revision_id == revision_id)
            .order_by(desc(CostEstimateSnapshot.created_at), desc(CostEstimateSnapshot.id))
            .limit(1)
        ).one_or_none()


class HumanPaidRenderApprovalService:
    def __init__(self, session: Session):
        self.session = session

    def create_pending(self, data: HumanPaidRenderApprovalCreateRequest) -> HumanPaidRenderApproval:
        RenderRevisionService(self.session).get(data.render_revision_id)
        approval = HumanPaidRenderApproval(
            render_revision_id=data.render_revision_id,
            approval_status="PENDING",
            approved_by=None,
            approved_at=None,
            max_approved_cost=data.max_approved_cost,
            approved_provider_stages_json=data.approved_provider_stages_json,
            rationale=data.rationale,
            expires_at=data.expires_at,
        )
        self.session.add(approval)
        self.session.flush()
        return approval

    def approve(self, approval_id: uuid.UUID, data: HumanPaidRenderApprovalDecisionRequest) -> HumanPaidRenderApproval:
        approval = self.require(approval_id)
        approval.approval_status = "APPROVED"
        approval.approved_by = data.approved_by or "operator"
        approval.approved_at = utc_now()
        if data.rationale is not None:
            approval.rationale = data.rationale
        if data.max_approved_cost is not None:
            approval.max_approved_cost = data.max_approved_cost
        if data.approved_provider_stages_json is not None:
            approval.approved_provider_stages_json = data.approved_provider_stages_json
        if data.expires_at is not None:
            approval.expires_at = data.expires_at
        revision = self.session.get(RenderRevision, approval.render_revision_id)
        if revision is not None:
            revision.revision_status = "APPROVED_FOR_PROVIDER_BOUNDARY"
        self.session.flush()
        return approval

    def reject(self, approval_id: uuid.UUID, data: HumanPaidRenderApprovalDecisionRequest | None = None) -> HumanPaidRenderApproval:
        approval = self.require(approval_id)
        approval.approval_status = "REJECTED"
        if data and data.rationale:
            approval.rationale = data.rationale
        self.session.flush()
        return approval

    def revoke(self, approval_id: uuid.UUID, data: HumanPaidRenderApprovalDecisionRequest | None = None) -> HumanPaidRenderApproval:
        approval = self.require(approval_id)
        approval.approval_status = "REVOKED"
        if data and data.rationale:
            approval.rationale = data.rationale
        self.session.flush()
        return approval

    def require(self, approval_id: uuid.UUID) -> HumanPaidRenderApproval:
        approval = self.session.get(HumanPaidRenderApproval, approval_id)
        if approval is None:
            raise NotFoundError(f"paid render approval not found: {approval_id}")
        return approval


class ProviderIdempotencyService:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, data: ProviderIdempotencyKeyCreateRequest) -> ProviderIdempotencyKey:
        RenderRevisionService(self.session).get(data.render_revision_id)
        fingerprint = data.request_fingerprint or stable_hash(data.request_payload_json)
        existing = self.session.scalars(
            select(ProviderIdempotencyKey)
            .where(
                ProviderIdempotencyKey.render_revision_id == data.render_revision_id,
                ProviderIdempotencyKey.provider_key == data.provider_key,
                ProviderIdempotencyKey.provider_stage == data.provider_stage,
                ProviderIdempotencyKey.request_fingerprint == fingerprint,
            )
            .limit(1)
        ).one_or_none()
        if existing is not None:
            return existing
        idempotency_key = "provider-idem:" + stable_hash(
            {
                "render_revision_id": data.render_revision_id,
                "provider_key": data.provider_key,
                "provider_stage": data.provider_stage,
                "request_fingerprint": fingerprint,
            }
        )
        record = ProviderIdempotencyKey(
            render_revision_id=data.render_revision_id,
            provider_key=data.provider_key,
            provider_stage=data.provider_stage,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        self.session.add(record)
        self.session.flush()
        return record


class PaidAttemptLimitGate:
    def __init__(self, session: Session):
        self.session = session

    def check(self, *, render_revision_id: uuid.UUID, provider_key: str, provider_stage: str, max_attempts: int = 1) -> PaidAttemptLimitRecord:
        record = self._record(render_revision_id=render_revision_id, provider_key=provider_key, provider_stage=provider_stage, max_attempts=max_attempts)
        if record.attempt_count >= record.max_attempts:
            record.status = "BLOCKED"
            record.reason_codes_json = ["PAID_ATTEMPT_LIMIT_EXCEEDED"]
        else:
            record.status = "PASS"
            record.reason_codes_json = ["PAID_ATTEMPT_AVAILABLE"]
        self.session.flush()
        return record

    def record_attempt(self, *, render_revision_id: uuid.UUID, provider_key: str, provider_stage: str, max_attempts: int = 1) -> PaidAttemptLimitRecord:
        record = self._record(render_revision_id=render_revision_id, provider_key=provider_key, provider_stage=provider_stage, max_attempts=max_attempts)
        if record.attempt_count >= record.max_attempts:
            record.status = "BLOCKED"
            record.reason_codes_json = ["PAID_ATTEMPT_LIMIT_EXCEEDED"]
        else:
            record.attempt_count += 1
            record.last_attempt_at = utc_now()
            record.status = "PASS" if record.attempt_count <= record.max_attempts else "BLOCKED"
            record.reason_codes_json = ["PAID_ATTEMPT_RECORDED"]
        self.session.flush()
        return record

    def _record(self, *, render_revision_id: uuid.UUID, provider_key: str, provider_stage: str, max_attempts: int) -> PaidAttemptLimitRecord:
        RenderRevisionService(self.session).get(render_revision_id)
        record = self.session.scalars(
            select(PaidAttemptLimitRecord)
            .where(
                PaidAttemptLimitRecord.render_revision_id == render_revision_id,
                PaidAttemptLimitRecord.provider_key == provider_key,
                PaidAttemptLimitRecord.provider_stage == provider_stage,
            )
            .limit(1)
        ).one_or_none()
        if record is None:
            record = PaidAttemptLimitRecord(
                render_revision_id=render_revision_id,
                provider_key=provider_key,
                provider_stage=provider_stage,
                attempt_count=0,
                max_attempts=max_attempts,
                status="PASS",
                reason_codes_json=["PAID_ATTEMPT_AVAILABLE"],
            )
            self.session.add(record)
            self.session.flush()
        return record


class PaidRenderApprovalGate:
    def __init__(self, session: Session):
        self.session = session

    def check(
        self,
        *,
        approval: HumanPaidRenderApproval | None,
        revision: RenderRevision,
        provider_stage: str,
        estimate: CostEstimateSnapshot | None,
    ) -> GateCheck:
        if approval is None:
            return GateCheck(False, ["HUMAN_PAID_APPROVAL_MISSING"], "WAITING_HUMAN_PAID_APPROVAL")
        if approval.render_revision_id != revision.id:
            return GateCheck(False, ["HUMAN_PAID_APPROVAL_REVISION_MISMATCH"], "WAITING_HUMAN_PAID_APPROVAL")
        if approval.approval_status != "APPROVED":
            return GateCheck(False, [f"HUMAN_PAID_APPROVAL_{approval.approval_status}"], "WAITING_HUMAN_PAID_APPROVAL")
        if approval.expires_at is not None and approval.expires_at <= utc_now():
            approval.approval_status = "EXPIRED"
            self.session.flush()
            return GateCheck(False, ["HUMAN_PAID_APPROVAL_EXPIRED"], "WAITING_HUMAN_PAID_APPROVAL")
        if provider_stage not in set(approval.approved_provider_stages_json or []):
            return GateCheck(False, ["HUMAN_PAID_APPROVAL_STAGE_NOT_APPROVED"], "WAITING_HUMAN_PAID_APPROVAL")
        if estimate and approval.max_approved_cost is not None and estimate.estimated_total_cost is not None:
            if estimate.estimated_total_cost > approval.max_approved_cost:
                return GateCheck(False, ["COST_ESTIMATE_EXCEEDS_APPROVAL"], "WAITING_HUMAN_PAID_APPROVAL")
        return GateCheck(True, ["HUMAN_PAID_APPROVAL_PRESENT"], "PASS")


class ProviderCharacterInputGate:
    def check(self, *, effective: EffectiveChannelRuntimeContextSnapshot, provider_key: str, provider_stage: str, request_payload: dict[str, Any]) -> GateCheck:
        stage = provider_stage.upper()
        if provider_key != "luma_api" and stage not in CHARACTER_DEPENDENT_STAGES:
            return GateCheck(True, [], "PASS")
        reasons: list[str] = []
        requires_character = bool(request_payload.get("requires_character") or request_payload.get("character_ref_required"))
        payload_refs = {
            "character_profile_id": request_payload.get("character_profile_id"),
            "character_image_branch_id": request_payload.get("character_image_branch_id"),
            "reference_asset_pack_id": request_payload.get("reference_asset_pack_id"),
        }
        if effective.character_policy_mode == "NO_CHARACTER" and (requires_character or any(payload_refs.values())):
            reasons.append("NO_CHARACTER_BLOCKS_CHARACTER_PROVIDER_INPUT")
        if effective.character_profile_id is not None:
            if effective.character_image_branch_id is None:
                reasons.append("CHARACTER_IMAGE_BRANCH_REQUIRED")
            if effective.reference_asset_pack_id is None:
                reasons.append("REFERENCE_ASSET_PACK_REQUIRED")
        expected = {
            "character_profile_id": str(effective.character_profile_id) if effective.character_profile_id else None,
            "character_image_branch_id": str(effective.character_image_branch_id) if effective.character_image_branch_id else None,
            "reference_asset_pack_id": str(effective.reference_asset_pack_id) if effective.reference_asset_pack_id else None,
        }
        for key, value in payload_refs.items():
            if value and expected.get(key) and str(value) != expected[key]:
                reasons.append(f"{key.upper()}_MISMATCH_EFFECTIVE_CONTEXT")
        return GateCheck(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCKED_CHARACTER_INPUT")


class ProviderVoiceInputGate:
    def __init__(self, session: Session):
        self.session = session

    def check(self, *, effective: EffectiveChannelRuntimeContextSnapshot, provider_key: str, provider_stage: str, request_payload: dict[str, Any]) -> GateCheck:
        stage = provider_stage.upper()
        if provider_key != "elevenlabs" and stage not in VOICE_STAGES:
            return GateCheck(True, [], "PASS")
        reasons: list[str] = []
        voice_profile_id = request_payload.get("voice_profile_id") or effective.voice_profile_id
        voice_context = _dict(effective.voice_audio_context_json)
        if not voice_profile_id:
            reasons.append("VOICE_PROFILE_REQUIRED")
            return GateCheck(False, reasons, "BLOCKED_VOICE_INPUT")
        if effective.voice_profile_id and str(voice_profile_id) != str(effective.voice_profile_id):
            reasons.append("VOICE_PROFILE_MISMATCH_EFFECTIVE_CONTEXT")
        voice = self.session.get(VoiceProfile, uuid.UUID(str(voice_profile_id)))
        if voice is None:
            reasons.append("VOICE_PROFILE_NOT_FOUND")
        else:
            if voice.status != "ACTIVE":
                reasons.append("VOICE_PROFILE_NOT_ACTIVE")
            if voice.consent_status not in {"VERIFIED", "APPROVED", "NOT_REQUIRED"}:
                reasons.append("VOICE_CONSENT_NOT_VALID")
            if voice.commercial_use_status not in {"ALLOWED", "APPROVED", "NOT_REQUIRED"}:
                reasons.append("VOICE_COMMERCIAL_USE_NOT_ALLOWED")
            if not voice.language:
                reasons.append("VOICE_LANGUAGE_REQUIRED")
            if not voice.accent:
                reasons.append("VOICE_ACCENT_REQUIRED")
        if not voice_context.get("language"):
            reasons.append("VOICE_CONTEXT_LANGUAGE_REQUIRED")
        return GateCheck(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCKED_VOICE_INPUT")


class PexelsUsagePolicyGate:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def check(self, *, request_payload: dict[str, Any]) -> GateCheck:
        role = request_payload.get("usage_role")
        metrics = _dict(request_payload.get("usage_metrics"))
        reasons = validate_pexels_policy(role, self.settings, metrics)
        if role in {"factual_evidence", "recurring_host_identity"}:
            reasons.append("PEXELS_CANNOT_BE_EVIDENCE_OR_RECURRING_CHARACTER_SOURCE")
        if not request_payload.get("attribution_manifest_ref"):
            reasons.append("PEXELS_ATTRIBUTION_MANIFEST_REQUIRED")
        return GateCheck(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCK")


class VisualSourceMixGate:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def check(
        self,
        *,
        revision: RenderRevision,
        request_payload: dict[str, Any],
        provider_key: str | None = None,
        provider_stage: str | None = None,
    ) -> GateCheck:
        plan = {**_dict(revision.provider_plan_json), **request_payload}
        reasons: list[str] = []
        if str(plan.get("visual_backbone") or "").upper() == "PEXELS" or plan.get("pexels_core_backbone") is True:
            reasons.append("PEXELS_CANNOT_BE_CORE_VISUAL_BACKBONE")
        stage = (provider_stage or "").upper()
        if provider_key == "luma_api" or stage in CHARACTER_DEPENDENT_STAGES:
            luma_duration = _decimal_or_none(plan.get("luma_duration_seconds") or plan.get("duration_seconds"))
            if luma_duration is not None and luma_duration > Decimal("8"):
                reasons.append("LUMA_DURATION_EXCEEDS_8_SECONDS")
        return GateCheck(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCK")


class ProxyPreviewGate:
    def __init__(self, session: Session):
        self.session = session

    def flag(self, data: ProxyPreviewArtifactFlagCreateRequest) -> ProxyPreviewArtifactFlag:
        if not data.preview_only or not data.not_final_media or not data.not_publishable:
            raise ValidationFailureError("PROXY_PREVIEW_FLAGS_MUST_BE_NON_PUBLISHABLE")
        record = ProxyPreviewArtifactFlag(
            artifact_ref=data.artifact_ref,
            video_project_id=data.video_project_id,
            package_id=data.package_id,
            preview_only=data.preview_only,
            not_final_media=data.not_final_media,
            not_publishable=data.not_publishable,
            source_type=data.source_type,
        )
        self.session.merge(record)
        self.session.flush()
        return self.session.get(ProxyPreviewArtifactFlag, data.artifact_ref) or record

    def check_not_final_media(self, *, artifact_ref: str | None) -> GateCheck:
        if not artifact_ref:
            return GateCheck(True, [], "PASS")
        record = self.session.get(ProxyPreviewArtifactFlag, artifact_ref)
        if record is None:
            return GateCheck(True, [], "PASS")
        if record.preview_only or record.not_final_media or record.not_publishable:
            return GateCheck(False, ["PROXY_PREVIEW_ARTIFACT_NOT_PUBLISHABLE"], "BLOCKED_PROXY_PREVIEW")
        return GateCheck(True, [], "PASS")


class ProviderBoundaryAuditService:
    def __init__(self, session: Session):
        self.session = session

    def log(
        self,
        *,
        revision: RenderRevision,
        provider_key: str,
        provider_stage: str,
        call_type: str,
        call_status: str,
        request_fingerprint: str,
        reason_codes: list[str],
        human_approval_id: uuid.UUID | None = None,
        idempotency_key_id: uuid.UUID | None = None,
        cost_estimate_snapshot_id: uuid.UUID | None = None,
        response_ref: str | None = None,
    ) -> PaidProviderCallLedger:
        ledger = PaidProviderCallLedger(
            render_revision_id=revision.id,
            provider_key=provider_key,
            provider_stage=provider_stage,
            call_type=call_type,
            call_status=call_status,
            human_approval_id=human_approval_id,
            idempotency_key_id=idempotency_key_id,
            cost_estimate_snapshot_id=cost_estimate_snapshot_id,
            request_fingerprint=request_fingerprint,
            response_ref=response_ref,
            reason_codes_json=sorted(set(reason_codes)),
        )
        self.session.add(ledger)
        self.session.flush()
        return ledger


class PaidProviderBoundaryService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def preflight(self, data: ProviderBoundaryPreflightRequest) -> ProviderBoundaryDecisionRead:
        revision = RenderRevisionService(self.session).get(data.render_revision_id)
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, revision.effective_context_snapshot_id)
        if effective is None:
            raise NotFoundError(f"effective context not found: {revision.effective_context_snapshot_id}")
        provider_key = normalize_provider_key(data.provider_key) or data.provider_key
        provider_stage = data.provider_stage.strip().upper()
        request_fingerprint = stable_hash(data.request_payload_json)
        idempotency = (
            self.session.get(ProviderIdempotencyKey, data.idempotency_key_id)
            if data.idempotency_key_id
            else ProviderIdempotencyService(self.session).get_or_create(
                ProviderIdempotencyKeyCreateRequest(
                    render_revision_id=revision.id,
                    provider_key=provider_key,
                    provider_stage=provider_stage,
                    request_payload_json=data.request_payload_json,
                    request_fingerprint=request_fingerprint,
                )
            )
        )
        estimate = self._estimate(revision=revision, estimate_id=data.cost_estimate_snapshot_id)
        approval = self.session.get(HumanPaidRenderApproval, data.human_approval_id) if data.human_approval_id else self._latest_approved_revision_approval(revision)
        reasons: list[str] = provider_key_rejection_reasons(provider_key)
        status = "BLOCKED_PROVIDER_NOT_CONFIGURED" if reasons else "READY_FOR_PROVIDER_BOUNDARY"

        provider_check = self._provider_ready(provider_key)
        if not reasons and not provider_check.passed:
            reasons.extend(provider_check.reason_codes)
            status = "BLOCKED_PROVIDER_NOT_CONFIGURED"
        gate_check = self._deterministic_gate_check(revision)
        if not gate_check.passed:
            reasons.extend(gate_check.reason_codes)
            status = "BLOCKED_DETERMINISTIC_GATE"
        if estimate is None:
            reasons.append("COST_ESTIMATE_MISSING")
            status = "BLOCKED_COST_ESTIMATE"
        elif estimate.estimate_status != "ESTIMATED":
            reasons.extend(estimate.blocker_reason_codes_json or [f"COST_ESTIMATE_{estimate.estimate_status}"])
            status = "BLOCKED_COST_ESTIMATE"
        if _is_paid_provider(provider_key, provider_stage):
            approval_check = PaidRenderApprovalGate(self.session).check(
                approval=approval,
                revision=revision,
                provider_stage=provider_stage,
                estimate=estimate,
            )
            if not approval_check.passed:
                reasons.extend(approval_check.reason_codes)
                status = approval_check.status
        attempt_record = PaidAttemptLimitGate(self.session).check(
            render_revision_id=revision.id,
            provider_key=provider_key,
            provider_stage=provider_stage,
        )
        if attempt_record.status == "BLOCKED":
            reasons.extend(attempt_record.reason_codes_json)
            status = "BLOCKED_ATTEMPT_LIMIT"
        character_check = ProviderCharacterInputGate().check(
            effective=effective,
            provider_key=provider_key,
            provider_stage=provider_stage,
            request_payload=data.request_payload_json,
        )
        if not character_check.passed:
            reasons.extend(character_check.reason_codes)
            status = "BLOCKED_CHARACTER_INPUT"
        voice_check = ProviderVoiceInputGate(self.session).check(
            effective=effective,
            provider_key=provider_key,
            provider_stage=provider_stage,
            request_payload=data.request_payload_json,
        )
        if not voice_check.passed:
            reasons.extend(voice_check.reason_codes)
            status = "BLOCKED_VOICE_INPUT"
        if provider_key == "pexels_api" or provider_stage in PEXELS_STAGES:
            pexels_check = PexelsUsagePolicyGate(self.settings).check(request_payload=data.request_payload_json)
            if not pexels_check.passed:
                reasons.extend(pexels_check.reason_codes)
                status = "BLOCKED_PROVIDER_NOT_CONFIGURED" if "PEXELS_API_KEY_MISSING" in pexels_check.reason_codes else "BLOCKED_COST_ESTIMATE"
        visual_check = VisualSourceMixGate(self.settings).check(
            revision=revision,
            request_payload=data.request_payload_json,
            provider_key=provider_key,
            provider_stage=provider_stage,
        )
        if not visual_check.passed:
            reasons.extend(visual_check.reason_codes)
            status = "BLOCKED_CHARACTER_INPUT" if "LUMA" in " ".join(visual_check.reason_codes) else "BLOCKED_COST_ESTIMATE"
        proxy_check = ProxyPreviewGate(self.session).check_not_final_media(artifact_ref=data.request_payload_json.get("final_artifact_ref"))
        if not proxy_check.passed:
            reasons.extend(proxy_check.reason_codes)
            status = "BLOCKED_PROXY_PREVIEW"

        reasons = sorted(set(reasons))
        if reasons:
            call_status = "BLOCKED"
            allowed = False
        else:
            real_execution_enabled = self._real_execution_enabled(provider_key)
            if not real_execution_enabled:
                reasons.append("PROVIDER_REAL_EXECUTION_DISABLED")
                status = "ALLOWED_NOT_EXECUTED"
                call_status = "ALLOWED_NOT_EXECUTED"
                allowed = True
            else:
                reasons.append("FUTURE_PROVIDER_EXECUTION_NOT_IMPLEMENTED")
                status = "ALLOWED_NOT_EXECUTED"
                call_status = "ALLOWED_NOT_EXECUTED"
                allowed = True

        ledger = ProviderBoundaryAuditService(self.session).log(
            revision=revision,
            provider_key=provider_key,
            provider_stage=provider_stage,
            call_type=data.call_type,
            call_status=call_status,
            request_fingerprint=request_fingerprint,
            reason_codes=reasons,
            human_approval_id=approval.id if approval else None,
            idempotency_key_id=idempotency.id if idempotency else None,
            cost_estimate_snapshot_id=estimate.id if estimate else None,
        )
        return ProviderBoundaryDecisionRead(
            render_revision_id=revision.id,
            provider_key=provider_key,
            provider_stage=provider_stage,
            status=status,
            call_status=call_status,
            allowed=allowed,
            will_execute=False,
            reason_codes=reasons,
            ledger_id=ledger.id,
            cost_estimate_snapshot_id=estimate.id if estimate else None,
            human_approval_id=approval.id if approval else None,
            idempotency_key_id=idempotency.id if idempotency else None,
            attempt_limit_record_id=attempt_record.id,
            no_network_call_made=True,
        )

    def _provider_ready(self, provider_key: str) -> GateCheck:
        provider = {item.provider_key: item for item in ProviderReadinessM2Service(self.settings).snapshot().providers}.get(provider_key)
        if provider is None:
            return GateCheck(False, ["PROVIDER_NOT_IN_M2_READINESS"], "BLOCKED_PROVIDER_NOT_CONFIGURED")
        if provider.readiness_state not in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION"}:
            return GateCheck(False, ["BLOCKED_PROVIDER_NOT_CONFIGURED", *provider.blocker_reason_codes], "BLOCKED_PROVIDER_NOT_CONFIGURED")
        return GateCheck(True, ["PROVIDER_CONFIGURED"], "PASS")

    def _estimate(self, *, revision: RenderRevision, estimate_id: uuid.UUID | None) -> CostEstimateSnapshot | None:
        if estimate_id:
            estimate = self.session.get(CostEstimateSnapshot, estimate_id)
            if estimate is None:
                raise NotFoundError(f"cost estimate not found: {estimate_id}")
            return estimate
        return CostEstimateService(self.session, self.settings).latest_for_revision(revision.id)

    def _latest_approved_revision_approval(self, revision: RenderRevision) -> HumanPaidRenderApproval | None:
        return self.session.scalars(
            select(HumanPaidRenderApproval)
            .where(
                HumanPaidRenderApproval.render_revision_id == revision.id,
                HumanPaidRenderApproval.approval_status == "APPROVED",
            )
            .order_by(desc(HumanPaidRenderApproval.created_at), desc(HumanPaidRenderApproval.id))
            .limit(1)
        ).one_or_none()

    def _deterministic_gate_check(self, revision: RenderRevision) -> GateCheck:
        reasons: list[str] = []
        for ref in _list(revision.gate_batch_refs_json):
            ref_id = _dict(ref).get("gate_batch_run_id") or _dict(ref).get("id")
            status = _dict(ref).get("status")
            batch = self.session.get(R3D4GateBatchRun, uuid.UUID(str(ref_id))) if ref_id else None
            effective_status = batch.status if batch is not None else status
            if effective_status == "BLOCK":
                reasons.append("DETERMINISTIC_GATE_BATCH_BLOCK")
        return GateCheck(not reasons, sorted(set(reasons)), "PASS" if not reasons else "BLOCKED_DETERMINISTIC_GATE")

    def _real_execution_enabled(self, provider_key: str) -> bool:
        if not bool(getattr(self.settings, "provider_real_execution_enabled", False)):
            return False
        flag_name = EXECUTION_FLAG_BY_PROVIDER.get(provider_key)
        return bool(flag_name and getattr(self.settings, flag_name, False))


class ProviderJobService:
    def __init__(self, session: Session):
        self.session = session

    def create_not_submitted(self, data: ProviderJobCreateRequest) -> ProviderJobSnapshot:
        RenderRevisionService(self.session).get(data.render_revision_id)
        snapshot = ProviderJobSnapshot(
            render_revision_id=data.render_revision_id,
            provider_key=data.provider_key,
            provider_stage=data.provider_stage,
            job_status="NOT_SUBMITTED",
            provider_request_hash=stable_hash(data.provider_request_json),
            poll_count=0,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def create_submission_blocked(self, *, render_revision_id: uuid.UUID, provider_key: str, provider_stage: str, reason_code: str) -> ProviderJobSnapshot:
        RenderRevisionService(self.session).get(render_revision_id)
        snapshot = ProviderJobSnapshot(
            render_revision_id=render_revision_id,
            provider_key=provider_key,
            provider_stage=provider_stage,
            job_status="SUBMISSION_BLOCKED",
            last_error_code=reason_code,
            last_error_message="Provider submission blocked by R3D8 boundary.",
            poll_count=0,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def mark_timeout_resume_required(self, job_snapshot_id: uuid.UUID) -> ProviderJobSnapshot:
        snapshot = self.session.get(ProviderJobSnapshot, job_snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"provider job snapshot not found: {job_snapshot_id}")
        snapshot.job_status = "RESUME_REQUIRED"
        snapshot.last_error_code = "PROVIDER_JOB_TIMED_OUT"
        snapshot.last_error_message = "Timeout requires resume/poll continuation; do not submit duplicate job."
        self.session.flush()
        return snapshot


def _artifact_refs_from_package(package: FirstScriptedVideoPackage) -> list[dict[str, Any]]:
    return [
        {"artifact_key": key, "artifact_hash": stable_hash(value)}
        for key, value in sorted(_dict(package.artifacts).items())
        if value is not None
    ]


def _planned_provider_items(provider_plan: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(provider_plan.get("provider_stages"), list):
        return [item for item in provider_plan["provider_stages"] if isinstance(item, dict)]
    items: list[dict[str, Any]] = []
    for key in ("voice", "ai_hero", "final_render", "pexels"):
        value = provider_plan.get(key)
        if isinstance(value, dict):
            items.append(value)
    return items


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _decimal_string(value: Any) -> str | None:
    decimal = _decimal_or_none(value)
    return str(decimal) if decimal is not None else None
