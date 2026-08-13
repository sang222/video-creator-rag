"""One-shot recovery of missing timing for already-sealed V2 narration audio.

This service is intentionally narrower than dead-letter replay.  It authorizes
one exact controlled-verifier-settlement lineage, never retries final TTS, and
permits at most one ElevenLabs Forced Alignment submission.  The failed MEDIA
event and its dead letter remain immutable historical evidence.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.contracts.production_package import ProductionPackageContentV2
from app.contracts.production_workflow import ProductionWorkflowStage
from app.contracts.temporal_authority import NarrationTimingSeed
from app.core.actor import ActorContext, ActorType
from app.core.config import Settings, get_settings
from app.core.db import get_session_factory
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.ops import DeadLetterJob
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.script_qualification import (
    ControlledVerifierSettlementAuthority,
    ScriptContractReplacementAuthority,
    ScriptQualificationRun,
)
from app.db.models.v2_effect import (
    V2NarrationTimingRecoveryAuthority,
    V2NarrationTimingRecoveryReceipt,
    V2ProductionEffectLedger,
)
from app.db.models.workflow import ArtifactVersion
from app.services.config_registry import content_hash
from app.services.cqr1_real_provider import ElevenLabsForcedAlignmentClient
from app.services.mr1_monthly_budget import MR1MonthlyBudgetAuthority
from app.services.mr1_provider_gateways import (
    MR1AlignmentGatewayAdapter,
    _temporal_normalized,
)
from app.services.production_package import ProductionPackageService
from app.services.production_workflow import (
    ProductionWorkflowCoordinator,
    WORKFLOW_AGGREGATE_TYPE,
    WORKFLOW_EVENT_TYPE,
    WORKFLOW_EVENT_VERSION,
    command_id_for,
    handler_key_for,
    semantic_hash,
)
from app.services.script_contract_replacement import (
    controlled_verifier_settlement_authority_body,
    operator_recovery_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.temporal_authority import (
    ElevenLabsForcedAlignmentRequestBuilder,
    ElevenLabsForcedAlignmentResponseParser,
)
from app.services.v2_elevenlabs_narration import V2ElevenLabsNarrationAdapter
from app.services.v2_native_effects import (
    _load_json,
    _persist_exact_json,
    _sha256_file,
    _write_json_atomic,
)
from app.services.v2_support_authority import V2FrozenSupportEnvelope
from app.contracts.vcos_v2 import ProductionLane


AUTHORITY_SCHEMA = "vcos.v2-narration-timing-recovery-authority.v1"
RECEIPT_SCHEMA = "vcos.v2-narration-timing-recovery-receipt.v1"
RECOVERY_REASON = "DURABLE_TTS_AUDIO_MISSING_TIMING_PROVENANCE"
ORIGINAL_FAILURE = "V2_ELEVENLABS_PROVIDER_FAILURE"
ALIGNMENT_METHOD = "ELEVENLABS_FORCED_ALIGNMENT_RECOVERY"
RECOVERY_HANDLER_VERSION = "production-workflow.v1+v2-narration-timing-recovery@1"
_AUTHORITY_NAMESPACE = uuid.UUID("8673c077-fd4f-54d5-852e-09546bfb9314")
_CONTROLLED_RECOVERY_ACTOR_ID = uuid.UUID("6d196d74-7938-5c85-bc10-f25466616258")


@dataclass(frozen=True, slots=True)
class V2NarrationTimingRecoveryResult:
    workflow_run_id: uuid.UUID
    authority_id: uuid.UUID
    receipt_id: uuid.UUID | None
    media_effect_ledger_id: uuid.UUID
    workflow_command_receipt_id: uuid.UUID | None
    workflow_state: str
    next_domain_event_id: uuid.UUID | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class _RecoveryScope:
    run: ProductionWorkflowRun
    ledger: V2ProductionEffectLedger
    event: DomainEvent
    dead_letter: DeadLetterJob
    root: ScriptContractReplacementAuthority
    settlement: ControlledVerifierSettlementAuthority
    qualification: ScriptQualificationRun
    package_version: ArtifactVersion
    package: ProductionPackageContentV2
    script_version: ArtifactVersion
    budget: MR1MonthlyBudgetReservation
    request_identity: dict[str, Any]
    audio_path: Path
    audio_relative_path: str
    audio_checksum: str
    audio_size_bytes: int
    audio_duration_ms: int
    approved_script_hash: str
    budget_authority_hash: str
    caption_locale: str


class V2NarrationTimingRecoveryService:
    """Authorize and execute one exact no-TTS-retry timing recovery."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        session_factory: Callable[[], Session] | None = None,
        audio_probe: Callable[[Path], int] | None = None,
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        workspace_root: Path | None = None,
        now: Callable[[], Any] = utc_now,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.session_factory = session_factory or get_session_factory()
        self.audio_probe = audio_probe
        self.client = client
        self.client_factory = client_factory
        self.now = now
        self.adapter = V2ElevenLabsNarrationAdapter(
            settings=self.settings,
            workspace_root=workspace_root,
            session_factory=self.session_factory,
        )

    def authorize(
        self, workflow_run_id: uuid.UUID, actor: ActorContext
    ) -> V2NarrationTimingRecoveryAuthority:
        """Seal authority before any Forced Alignment provider boundary."""

        self._require_actor(actor)
        existing = self.session.scalar(
            select(V2NarrationTimingRecoveryAuthority).where(
                V2NarrationTimingRecoveryAuthority.workflow_run_id == workflow_run_id
            )
        )
        if existing is not None:
            self._validate_existing_authority(existing)
            return existing

        self._require_permission(actor)
        self._require_provider_preflight()
        scope = self._resolve_scope(workflow_run_id, require_blocked=True)
        self._require_pristine_initial_scope(scope)
        authority_id = uuid.uuid5(_AUTHORITY_NAMESPACE, str(workflow_run_id))
        values = {
            "id": authority_id,
            "workflow_run_id": scope.run.id,
            "video_project_id": scope.run.video_project_id,
            "media_effect_ledger_id": scope.ledger.id,
            "media_domain_event_id": scope.event.id,
            "media_dead_letter_job_id": scope.dead_letter.id,
            "root_replacement_authority_id": scope.root.id,
            "verifier_settlement_authority_id": scope.settlement.id,
            "settlement_qualification_run_id": scope.qualification.id,
            "production_package_artifact_version_id": scope.package_version.id,
            "production_package_hash": scope.package_version.content_hash,
            "script_artifact_version_id": scope.script_version.id,
            "script_content_hash": scope.script_version.content_hash,
            "approved_script_hash": scope.approved_script_hash,
            "budget_reservation_id": scope.budget.id,
            "budget_reservation_ref": scope.budget.reservation_ref,
            "budget_authority_hash": scope.budget_authority_hash,
            "provider_policy_hash": scope.package.compiled_policy_snapshot_hash,
            "tts_request_journal_ref": self.adapter._relative(
                self.adapter._effect_dir(scope.ledger.command_id)
                / "elevenlabs-request-journal.json"
            ),
            "tts_request_identity_hash": content_hash(scope.request_identity),
            "tts_idempotency_key": str(scope.request_identity["idempotency_key"]),
            "audio_relative_path": scope.audio_relative_path,
            "audio_checksum_sha256": scope.audio_checksum,
            "audio_size_bytes": scope.audio_size_bytes,
            "audio_duration_ms": scope.audio_duration_ms,
            "original_failure_reason_code": ORIGINAL_FAILURE,
            "forced_alignment_permission_confirmed": True,
            "max_tts_retries": 0,
            "max_forced_alignment_submissions": 1,
            "schema_version": AUTHORITY_SCHEMA,
            "recovery_reason": RECOVERY_REASON,
            "authorized_by_actor_type": actor.actor_type.value,
            "authorized_by_actor_id": actor.actor_id,
            "authorized_by_actor_role": actor.actor_role,
            "created_at": self.now(),
        }
        authority = V2NarrationTimingRecoveryAuthority(
            **values,
            authority_hash=content_hash(_authority_body(values)),
        )
        self.session.add(authority)
        self.session.flush()
        # This is the required crash boundary.  The provider is unreachable
        # until the immutable authority and every referenced old fact commit.
        self.session.commit()
        self.session.refresh(authority)
        return authority

    def recover(
        self, workflow_run_id: uuid.UUID, actor: ActorContext
    ) -> V2NarrationTimingRecoveryResult:
        """Recover timing once, verify MEDIA, and schedule exact RENDER."""

        self._require_actor(actor)
        # There is no durable authority yet on the normal first invocation, so
        # an unconfirmed external permission must fail before any DB/lock touch.
        # Once authority exists, its sealed confirmation governs offline
        # journal replay even if current configuration later changes.
        existing_authority = self.session.scalar(
            select(V2NarrationTimingRecoveryAuthority).where(
                V2NarrationTimingRecoveryAuthority.workflow_run_id == workflow_run_id
            )
        )
        if existing_authority is None:
            self._require_permission(actor)
        with self._recovery_lock(workflow_run_id):
            # Serialize the lookup/insert as well as the provider boundary.
            # The authority ID is deterministic, but concurrency must replay
            # the committed row rather than surface a unique-key failure.
            replay = self._replay_result(workflow_run_id)
            if replay is not None:
                return replay
            authority = self.authorize(workflow_run_id, actor)
            return self._recover_locked(workflow_run_id, actor, authority)

    def _recover_locked(
        self,
        workflow_run_id: uuid.UUID,
        actor: ActorContext,
        authority: V2NarrationTimingRecoveryAuthority,
    ) -> V2NarrationTimingRecoveryResult:
        del actor
        replay = self._replay_result(workflow_run_id)
        if replay is not None:
            return replay
        scope = self._resolve_scope(
            workflow_run_id,
            require_blocked=True,
            authority=authority,
        )
        self._assert_authority_matches_scope(authority, scope)

        normalized = _temporal_normalized(
            {
                "normalized_text": self._script_text(scope.script_version),
                "script_artifact_version_id": str(scope.script_version.id),
            }
        )
        effect_dir = self.adapter._effect_dir(scope.ledger.command_id)
        request_path = effect_dir / "elevenlabs-forced-alignment-request-journal.json"
        response_path = effect_dir / "elevenlabs-forced-alignment-response-journal.json"
        evidence_path = effect_dir / "elevenlabs-forced-alignment-evidence.json"
        timing_path = effect_dir / "elevenlabs-recovered-timing-seed.json"
        narration_path = effect_dir / "elevenlabs-narration-receipt.json"

        request = ElevenLabsForcedAlignmentRequestBuilder().build(
            audio_asset_ref=self._audio_asset_ref(scope), normalized=normalized
        )
        request_journal = {
            "schema_version": "vcos.v2-forced-alignment-recovery-request.v1",
            "authority_id": str(authority.id),
            "authority_hash": authority.authority_hash,
            "workflow_run_id": str(scope.run.id),
            "media_effect_ledger_id": str(scope.ledger.id),
            "forced_alignment_request": request,
            "forced_alignment_request_hash": request["request_hash"],
            "audio_relative_path": scope.audio_relative_path,
            "audio_checksum_sha256": scope.audio_checksum,
            "audio_duration_ms": scope.audio_duration_ms,
            "spoken_text_hash": normalized.spoken_text_hash,
            "max_provider_submissions": 1,
            "tts_retry_count": 0,
            "state": "SUBMITTED",
        }
        request_existed = request_path.exists()
        if request_existed:
            if _load_json(request_path) != request_journal:
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_REQUEST_MISMATCH"
                )

        response_exists = response_path.exists()
        if response_exists and not request_existed:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_RESPONSE_WITHOUT_REQUEST"
            )
        if not request_existed and not response_exists:
            # Only a fresh provider submission requires the live execution
            # gate.  Captured responses are parsed offline, and a submitted
            # request without a capture stays outcome-unknown even if current
            # credentials or provider settings later change.
            self._require_provider_preflight()
            if scope.budget.status == "RESERVED":
                MR1MonthlyBudgetAuthority(self.session).mark_submitted(
                    scope.budget.reservation_ref
                )

        # `_resolve_scope` deliberately locks the workflow and effect ledger.
        # Release those row locks before the adapter opens its independent
        # reconciliation session.  The session-level recovery advisory lock
        # remains held across this commit and serializes the one provider
        # boundary, while the immutable authority and O_EXCL journal preserve
        # the crash/replay contract.
        self.session.commit()

        if response_exists:
            captured = self._load_response_capture(
                response_path, authority=authority, request_hash=request["request_hash"]
            )
            evidence = ElevenLabsForcedAlignmentResponseParser().parse(
                response=dict(captured["response"]),
                response_headers=dict(captured.get("response_headers") or {}),
                normalized=normalized,
                audio_asset_ref=self._audio_asset_ref(scope),
                audio_duration_ms=scope.audio_duration_ms,
            )
        else:
            if request_existed:
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_OUTCOME_UNKNOWN"
                )
            self._write_exclusive_json(request_path, request_journal)
            captured_holder: dict[str, Any] = {}

            def capture(value: dict[str, Any]) -> None:
                captured = dict(value)
                payload = {
                    "schema_version": ("vcos.v2-forced-alignment-recovery-response.v1"),
                    "authority_id": str(authority.id),
                    "authority_hash": authority.authority_hash,
                    "forced_alignment_request_hash": request["request_hash"],
                    "capture": captured,
                }
                payload["content_hash"] = content_hash(payload)
                _write_json_atomic(response_path, payload)
                captured_holder.update(captured)

            client = self._forced_alignment_client(capture)
            kwargs = {
                "api_key": self._api_key(),
                "normalized": normalized,
                "audio_path": scope.audio_path,
                "audio_asset_ref": self._audio_asset_ref(scope),
                "audio_duration_ms": scope.audio_duration_ms,
            }
            if "response_capture" in inspect.signature(client.execute_once).parameters:
                kwargs["response_capture"] = capture
            execution = client.execute_once(**kwargs)
            captured = self._load_response_capture(
                response_path,
                authority=authority,
                request_hash=request["request_hash"],
            )
            captured_evidence = ElevenLabsForcedAlignmentResponseParser().parse(
                response=dict(captured["response"]),
                response_headers=dict(captured.get("response_headers") or {}),
                normalized=normalized,
                audio_asset_ref=self._audio_asset_ref(scope),
                audio_duration_ms=scope.audio_duration_ms,
            )
            if (
                getattr(execution, "request_hash", None) != request["request_hash"]
                or getattr(execution, "provider_response_hash", None)
                != captured_holder.get("content_hash")
                or execution.evidence.content_hash != captured_evidence.content_hash
            ):
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_PROVIDER_PROOF_MISMATCH"
                )
            evidence = captured_evidence
            if not response_path.exists() or not captured_holder:
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_RESPONSE_NOT_CAPTURED"
                )

        alignment_audit = self._validate_exact_alignment(
            evidence,
            normalized,
            raw_response=dict(captured["response"]),
        )
        evidence_payload = evidence.model_dump(mode="json")
        _persist_exact_json(evidence_path, evidence_payload)
        timing_seed = self._timing_seed(
            scope=scope,
            normalized=normalized,
            evidence=evidence,
            alignment_audit=alignment_audit,
        )
        timing_payload = timing_seed.model_dump(mode="json")
        _persist_exact_json(timing_path, timing_payload)
        audio = {
            **scope.request_identity,
            "audio_strategy": "ELEVENLABS_FINAL_NARRATION",
            "audio_asset_ref": self._audio_asset_ref(scope),
            "audio_checksum": scope.audio_checksum,
            "audio_relative_path": scope.audio_relative_path,
            "duration_ms": scope.audio_duration_ms,
            "narration_present": True,
            "alignment_method": ALIGNMENT_METHOD,
            "caption_locale": scope.caption_locale,
            "provider_request_hash": str(request["request_hash"]),
            "provider_request_id": evidence.provider_request_id,
            "timing_seed": timing_payload,
            "timing_seed_hash": timing_seed.content_hash,
            "alignment_audit": alignment_audit,
            "usage_metadata": {
                "provider": "elevenlabs_forced_alignment",
                "provider_call_count": 1,
                "tts_retry_count": 0,
            },
            "actual_cost_usd": None,
            "secret_values_exposed": False,
        }
        _persist_exact_json(narration_path, audio)
        result = self.adapter.reconcile_recovered_media(
            workflow_run_id=scope.run.id,
            ledger_id=scope.ledger.id,
            audio=audio,
            recovery_authority_id=authority.id,
            recovery_authority_hash=authority.authority_hash,
        )

        self.session.expire_all()
        run = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.id == workflow_run_id)
            .with_for_update()
        )
        ledger = self.session.get(V2ProductionEffectLedger, scope.ledger.id)
        if run is None or ledger is None or ledger.state != "VERIFIED":
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_MEDIA_NOT_VERIFIED"
            )
        response = self._load_response_capture(
            response_path, authority=authority, request_hash=request["request_hash"]
        )
        receipt_values = {
            "id": uuid.uuid5(_AUTHORITY_NAMESPACE, f"receipt:{workflow_run_id}"),
            "authority_id": authority.id,
            "workflow_run_id": workflow_run_id,
            "media_effect_ledger_id": ledger.id,
            "forced_alignment_request_hash": str(request["request_hash"]),
            "forced_alignment_provider_response_hash": str(
                response["provider_response_hash"]
            ),
            "forced_alignment_provider_request_id": evidence.provider_request_id,
            "forced_alignment_provider_request_id_availability": (
                evidence.provider_request_id_availability
            ),
            "forced_alignment_evidence_hash": evidence.content_hash,
            "recovered_timing_seed_hash": timing_seed.content_hash,
            "narration_receipt_hash": content_hash(audio),
            "canonical_media_timeline_hash": str(result.result_hash),
            "provider_call_count": 1,
            "tts_retry_count": 0,
            "schema_version": RECEIPT_SCHEMA,
            "recovery_state": "VERIFIED",
            "created_at": self.now(),
        }
        recovery_receipt = V2NarrationTimingRecoveryReceipt(
            **receipt_values,
            receipt_hash=content_hash(_receipt_body(receipt_values)),
        )
        self.session.add(recovery_receipt)
        self.session.flush()

        from app.services.v2_ai_visual_authorization import (
            authorize_normal_ai_visual_after_verified_media,
        )

        authorize_normal_ai_visual_after_verified_media(
            session=self.session,
            workflow_run_id=workflow_run_id,
            media_result=result,
            workspace_root=self.adapter.root,
            settings=self.settings,
            clock=self.now,
        )
        command_receipt = WorkflowCommandReceipt(
            workflow_run_id=workflow_run_id,
            domain_event_id=scope.event.id,
            command_id=scope.ledger.command_id,
            stage="MEDIA",
            handler_key=str(scope.event.payload["handler_key"]),
            handler_version=RECOVERY_HANDLER_VERSION,
            input_hash=str(scope.event.payload["input_hash"]),
            effect_state=result.effect_state.value,
            result_type=result.result_type,
            result_id=result.result_id,
            result_ref=result.result_ref,
            result_hash=result.result_hash,
            result_payload={
                **result.result_payload,
                "timing_recovery_receipt_id": str(recovery_receipt.id),
                "timing_recovery_receipt_hash": recovery_receipt.receipt_hash,
                "recovered_media_domain_event_id": str(scope.event.id),
                "recovered_media_dead_letter_job_id": str(scope.dead_letter.id),
            },
            authority_refs=result.authority_refs.model_dump(
                mode="json", exclude_none=True
            ),
            started_at=ledger.started_at or self.now(),
            completed_at=ledger.completed_at or self.now(),
        )
        self.session.add(command_receipt)
        coordinator = ProductionWorkflowCoordinator(self.session, now=self.now)
        coordinator._apply_authority_refs(run, result.authority_refs)
        self.session.flush()
        coordinator._advance_after_receipt(
            run, command_receipt, reason_codes=result.reason_codes
        )
        self.session.flush()
        self.session.commit()
        self.session.refresh(run)
        expected_next_stage = (
            ProductionWorkflowStage.VISUAL
            if run.ai_visual_production_run_id is not None
            else ProductionWorkflowStage.RENDER
        )
        next_event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_run_id,
                DomainEvent.command_id
                == command_id_for(workflow_run_id, expected_next_stage),
            )
        )
        if (
            next_event is None
            or next_event.event_type != WORKFLOW_EVENT_TYPE
            or next_event.event_version != WORKFLOW_EVENT_VERSION
            or next_event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or next_event.aggregate_id != workflow_run_id
            or next_event.payload_hash != semantic_hash(next_event.payload or {})
            or (next_event.payload or {}).get("stage") != expected_next_stage.value
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_NEXT_EVENT_DRIFT"
            )
        return V2NarrationTimingRecoveryResult(
            workflow_run_id=workflow_run_id,
            authority_id=authority.id,
            receipt_id=recovery_receipt.id,
            media_effect_ledger_id=ledger.id,
            workflow_command_receipt_id=command_receipt.id,
            workflow_state=run.state,
            next_domain_event_id=next_event.id,
            replayed=False,
        )

    @staticmethod
    def _require_actor(actor: ActorContext) -> None:
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or actor.actor_id != _CONTROLLED_RECOVERY_ACTOR_ID
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_SYSTEM_WORKER_REQUIRED"
            )

    def _require_permission(self, actor: ActorContext) -> None:
        self._require_actor(actor)
        if self.settings.elevenlabs_forced_alignment_permission_confirmed is not True:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_PERMISSION_NOT_CONFIRMED"
            )

    def _resolve_scope(
        self,
        workflow_run_id: uuid.UUID,
        *,
        require_blocked: bool,
        authority: V2NarrationTimingRecoveryAuthority | None = None,
    ) -> _RecoveryScope:
        run = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.id == workflow_run_id)
            .with_for_update()
        )
        if (
            run is None
            or run.video_project_id is None
            or run.current_stage != "MEDIA"
            or (require_blocked and run.state != "BLOCKED")
            or run.production_package_artifact_version_id is None
            or run.production_package_hash is None
            or (run.canonical_media_timeline_ref is not None and authority is None)
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_SCOPE_INVALID")
        ledgers = list(
            self.session.scalars(
                select(V2ProductionEffectLedger)
                .where(
                    V2ProductionEffectLedger.workflow_run_id == workflow_run_id,
                    V2ProductionEffectLedger.stage == "MEDIA",
                )
                .with_for_update()
            ).all()
        )
        if len(ledgers) != 1:
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_LEDGER_INVALID")
        ledger = ledgers[0]
        event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_run_id,
                DomainEvent.command_id == ledger.command_id,
            )
        )
        dead_letter = self.session.scalar(
            select(DeadLetterJob).where(
                DeadLetterJob.domain_event_id == (event.id if event else None)
            )
        )
        settlements = list(
            self.session.scalars(
                select(ControlledVerifierSettlementAuthority)
                .join(
                    ScriptQualificationRun,
                    ScriptQualificationRun.id
                    == ControlledVerifierSettlementAuthority.settlement_qualification_run_id,
                )
                .where(
                    ScriptQualificationRun.production_workflow_run_id == workflow_run_id
                )
            ).all()
        )
        if len(settlements) != 1:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_SETTLEMENT_INVALID"
            )
        settlement = settlements[0]
        root = self.session.get(
            ScriptContractReplacementAuthority,
            settlement.root_replacement_authority_id,
        )
        qualification = self.session.get(
            ScriptQualificationRun, settlement.settlement_qualification_run_id
        )
        expected_media_input_hash = ProductionWorkflowCoordinator(
            self.session, now=self.now
        )._stage_input_hash(run, ProductionWorkflowStage.MEDIA)
        if (
            event is None
            or dead_letter is None
            or root is None
            or qualification is None
            or ledger.state not in {"FAILED_UNCERTAIN", "VERIFIED"}
            or ledger.effect_invocation_count != 1
            or ledger.command_id
            != command_id_for(workflow_run_id, ProductionWorkflowStage.MEDIA)
            or ledger.video_project_id != run.video_project_id
            or ledger.production_package_artifact_version_id
            != run.production_package_artifact_version_id
            or ledger.production_package_hash != run.production_package_hash
            or ledger.input_hash != expected_media_input_hash
            or (ledger.state == "FAILED_UNCERTAIN" and ledger.result_hash is not None)
            or (
                ledger.state == "VERIFIED"
                and not self._verified_ledger_matches_authority(ledger, authority)
            )
            or event.dead_lettered_at is None
            or event.event_type != WORKFLOW_EVENT_TYPE
            or event.event_version != WORKFLOW_EVENT_VERSION
            or event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or event.aggregate_id != workflow_run_id
            or event.command_id
            != command_id_for(workflow_run_id, ProductionWorkflowStage.MEDIA)
            or event.payload_hash != semantic_hash(event.payload or {})
            or (event.payload or {}).get("workflow_run_id") != str(workflow_run_id)
            or (event.payload or {}).get("production_lane") != run.production_lane
            or (event.payload or {}).get("stage") != "MEDIA"
            or (event.payload or {}).get("handler_key")
            != handler_key_for(
                ProductionLane(run.production_lane), ProductionWorkflowStage.MEDIA
            )
            or (event.payload or {}).get("input_hash") != expected_media_input_hash
            or event.last_error_code != ORIGINAL_FAILURE
            or dead_letter.reason_code != ORIGINAL_FAILURE
            or dead_letter.replay_state != "NOT_REPLAYABLE"
            or dead_letter.retry_eligible is not False
            or root.authority_hash
            != content_hash(operator_recovery_authority_body(root))
            or settlement.authority_hash
            != content_hash(controlled_verifier_settlement_authority_body(settlement))
            or resolve_replacement_qualification_leaf(self.session, authority=root).id
            != qualification.id
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_LINEAGE_INVALID")
        package_version = self.session.get(
            ArtifactVersion, run.production_package_artifact_version_id
        )
        if (
            package_version is None
            or package_version.content_hash != run.production_package_hash
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_PACKAGE_INVALID")
        package = ProductionPackageService(self.session).validate_for_readiness(
            package_version.id
        )
        effective = self.session.get(
            EffectiveChannelRuntimeContextSnapshot,
            package.effective_context_ref.id,
        )
        caption_locale = str(
            (effective.market_locale_context_json or {}).get("content_language")
            if effective is not None
            else ""
        ).strip()
        if (
            effective is None
            or effective.video_project_id != run.video_project_id
            or effective.context_hash != package.effective_context_ref.content_hash
            or effective.compile_status != "PASS"
            or not caption_locale
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_LOCALE_AUTHORITY_INVALID"
            )
        script_id = package.script_ref.artifact_version_id
        script_version = self.session.get(ArtifactVersion, script_id)
        budget = self.session.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.run_id == workflow_run_id
            )
        )
        if script_version is None or budget is None:
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_INPUT_MISSING")
        envelope_version = self.session.get(
            ArtifactVersion,
            package.support_envelope_ref.artifact_version_id
            if package.support_envelope_ref is not None
            else None,
        )
        try:
            envelope = V2FrozenSupportEnvelope.model_validate(
                envelope_version.content if envelope_version is not None else None
            )
        except ValueError as exc:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_SUPPORT_INVALID"
            ) from exc
        frozen_budget = envelope.zero_cost_budget.reservation_evidence or {}
        if (
            envelope.execution_mode != "REAL_LONG_FORM_PRODUCTION"
            or envelope.zero_cost_budget.reservation_ref != budget.reservation_ref
            or frozen_budget.get("reservation_id") != str(budget.id)
            or frozen_budget.get("run_id") != str(workflow_run_id)
            or frozen_budget.get("request_hash") != budget.request_hash
            or (budget.capacity_evidence_json or {}).get("content_hash")
            != (frozen_budget.get("capacity_evidence") or {}).get("content_hash")
            or budget.status not in {"RESERVED", "SUBMITTED"}
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_BUDGET_INVALID")
        effect_dir = self.adapter._effect_dir(ledger.command_id)
        tts_path = effect_dir / "elevenlabs-request-journal.json"
        request_raw = _load_json(tts_path)
        request_identity = {
            key: value for key, value in request_raw.items() if key != "state"
        }
        audio_relative = str(request_identity.get("output_relative_path") or "")
        audio_path = self.adapter._from_relative(audio_relative)
        duration = self._audio_duration(audio_path)
        script_text = self._script_text(script_version)
        approved_script_hash = hashlib.sha256(script_text.encode()).hexdigest()
        exact_request = {
            "schema_version": "vcos.v2-elevenlabs-request.v1",
            "command_id": ledger.command_id,
            "idempotency_key": request_identity.get("idempotency_key"),
            "script_content_hash": script_version.content_hash,
            "approved_script_hash": approved_script_hash,
            "voice_id": request_identity.get("voice_id"),
            "model_id": request_identity.get("model_id"),
            "voice_settings": request_identity.get("voice_settings"),
            "estimated_cost_usd": request_identity.get("estimated_cost_usd"),
            "output_relative_path": audio_relative,
            "attempt_limit": 1,
        }
        if request_raw.get("state") != "SUBMITTED" or request_identity != exact_request:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_TTS_IDENTITY_INVALID"
            )
        if not (
            package.duration_contract.minimum_duration_ms
            <= duration
            <= package.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_DURATION_INVALID"
            )
        return _RecoveryScope(
            run=run,
            ledger=ledger,
            event=event,
            dead_letter=dead_letter,
            root=root,
            settlement=settlement,
            qualification=qualification,
            package_version=package_version,
            package=package,
            script_version=script_version,
            budget=budget,
            request_identity=exact_request,
            audio_path=audio_path,
            audio_relative_path=audio_relative,
            audio_checksum=_sha256_file(audio_path),
            audio_size_bytes=audio_path.stat().st_size,
            audio_duration_ms=duration,
            approved_script_hash=approved_script_hash,
            budget_authority_hash=str(
                (budget.capacity_evidence_json or {}).get("content_hash") or ""
            ),
            caption_locale=caption_locale,
        )

    def _validate_existing_authority(
        self, authority: V2NarrationTimingRecoveryAuthority
    ) -> None:
        if authority.authority_hash != content_hash(_authority_body(authority)):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_AUTHORITY_DRIFT")
        audio = self.adapter._from_relative(authority.audio_relative_path)
        if (
            _sha256_file(audio) != authority.audio_checksum_sha256
            or audio.stat().st_size != authority.audio_size_bytes
            or self._audio_duration(audio) != authority.audio_duration_ms
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_AUDIO_DRIFT")

    def _assert_authority_matches_scope(
        self,
        authority: V2NarrationTimingRecoveryAuthority,
        scope: _RecoveryScope,
    ) -> None:
        self._validate_existing_authority(authority)
        request_journal_ref = self.adapter._relative(
            self.adapter._effect_dir(scope.ledger.command_id)
            / "elevenlabs-request-journal.json"
        )
        if (
            authority.workflow_run_id != scope.run.id
            or authority.video_project_id != scope.run.video_project_id
            or authority.media_effect_ledger_id != scope.ledger.id
            or authority.media_domain_event_id != scope.event.id
            or authority.media_dead_letter_job_id != scope.dead_letter.id
            or authority.root_replacement_authority_id != scope.root.id
            or authority.verifier_settlement_authority_id != scope.settlement.id
            or authority.settlement_qualification_run_id != scope.qualification.id
            or authority.production_package_artifact_version_id
            != scope.package_version.id
            or authority.production_package_hash != scope.package_version.content_hash
            or authority.script_artifact_version_id != scope.script_version.id
            or authority.script_content_hash != scope.script_version.content_hash
            or authority.approved_script_hash != scope.approved_script_hash
            or authority.budget_reservation_id != scope.budget.id
            or authority.budget_reservation_ref != scope.budget.reservation_ref
            or authority.tts_request_identity_hash
            != content_hash(scope.request_identity)
            or authority.tts_request_journal_ref != request_journal_ref
            or authority.tts_idempotency_key
            != str(scope.request_identity["idempotency_key"])
            or authority.budget_authority_hash != scope.budget_authority_hash
            or authority.provider_policy_hash
            != scope.package.compiled_policy_snapshot_hash
            or authority.audio_relative_path != scope.audio_relative_path
            or authority.audio_checksum_sha256 != scope.audio_checksum
            or authority.audio_size_bytes != scope.audio_size_bytes
            or authority.audio_duration_ms != scope.audio_duration_ms
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_AUTHORITY_DRIFT")

    def _require_pristine_initial_scope(self, scope: _RecoveryScope) -> None:
        """Require the exact pre-0076 fork-free state before issuing authority."""

        effect_dir = self.adapter._effect_dir(scope.ledger.command_id)
        if any(
            (effect_dir / name).exists()
            for name in (
                "elevenlabs-provider-response-journal.json",
                "elevenlabs-narration-receipt.json",
                "elevenlabs-forced-alignment-request-journal.json",
                "elevenlabs-forced-alignment-response-journal.json",
                "elevenlabs-forced-alignment-evidence.json",
                "elevenlabs-recovered-timing-seed.json",
                "canonical-media-timeline.json",
                "canonical-captions.srt",
            )
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_ORIGINAL_RECEIPT_PRESENT"
            )
        downstream_ledgers = list(
            self.session.scalars(
                select(V2ProductionEffectLedger.id).where(
                    V2ProductionEffectLedger.workflow_run_id == scope.run.id,
                    V2ProductionEffectLedger.stage.in_({"RENDER", "QC", "ARCHIVE"}),
                )
            ).all()
        )
        downstream_receipts = list(
            self.session.scalars(
                select(WorkflowCommandReceipt.id).where(
                    WorkflowCommandReceipt.workflow_run_id == scope.run.id,
                    WorkflowCommandReceipt.stage.in_(
                        {"MEDIA", "RENDER", "QC", "ARCHIVE", "FINALIZE"}
                    ),
                )
            ).all()
        )
        downstream_events = [
            event
            for event in self.session.scalars(
                select(DomainEvent).where(DomainEvent.workflow_run_id == scope.run.id)
            ).all()
            if (
                str((event.payload or {}).get("stage") or "")
                in {"RENDER", "QC", "ARCHIVE", "FINALIZE"}
                or event.command_id
                in {
                    command_id_for(scope.run.id, stage)
                    for stage in (
                        ProductionWorkflowStage.RENDER,
                        ProductionWorkflowStage.QC,
                        ProductionWorkflowStage.ARCHIVE,
                        ProductionWorkflowStage.FINALIZE,
                    )
                }
            )
        ]
        if downstream_ledgers or downstream_receipts or downstream_events:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_DOWNSTREAM_EFFECT_PRESENT"
            )

    def _replay_result(
        self, workflow_run_id: uuid.UUID
    ) -> V2NarrationTimingRecoveryResult | None:
        receipt = self.session.scalar(
            select(V2NarrationTimingRecoveryReceipt).where(
                V2NarrationTimingRecoveryReceipt.workflow_run_id == workflow_run_id
            )
        )
        if receipt is None:
            return None
        authority = self.session.get(
            V2NarrationTimingRecoveryAuthority, receipt.authority_id
        )
        command_receipt = self.session.scalar(
            select(WorkflowCommandReceipt).where(
                WorkflowCommandReceipt.workflow_run_id == workflow_run_id,
                WorkflowCommandReceipt.stage == "MEDIA",
            )
        )
        run = self.session.get(ProductionWorkflowRun, workflow_run_id)
        if authority is None or command_receipt is None or run is None:
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_REPLAY_INVALID")
        self._validate_existing_authority(authority)
        result_payload = dict(command_receipt.result_payload or {})
        authority_refs = dict(command_receipt.authority_refs or {})
        if (
            receipt.receipt_hash != content_hash(_receipt_body(receipt))
            or receipt.authority_id != authority.id
            or receipt.workflow_run_id != workflow_run_id
            or receipt.media_effect_ledger_id != authority.media_effect_ledger_id
            or command_receipt.domain_event_id != authority.media_domain_event_id
            or command_receipt.command_id
            != command_id_for(workflow_run_id, ProductionWorkflowStage.MEDIA)
            or command_receipt.handler_version != RECOVERY_HANDLER_VERSION
            or command_receipt.result_hash != receipt.canonical_media_timeline_hash
            or result_payload.get("timing_recovery_receipt_id") != str(receipt.id)
            or result_payload.get("timing_recovery_receipt_hash")
            != receipt.receipt_hash
            or result_payload.get("recovered_media_domain_event_id")
            != str(authority.media_domain_event_id)
            or result_payload.get("recovered_media_dead_letter_job_id")
            != str(authority.media_dead_letter_job_id)
            or authority_refs.get("video_project_id") != str(authority.video_project_id)
            or authority_refs.get("canonical_media_timeline_hash")
            != receipt.canonical_media_timeline_hash
            or str(run.canonical_media_timeline_hash or "")
            != receipt.canonical_media_timeline_hash
        ):
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_RECEIPT_DRIFT")
        expected_next_stage = (
            ProductionWorkflowStage.VISUAL
            if run.ai_visual_production_run_id is not None
            else ProductionWorkflowStage.RENDER
        )
        next_event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_run_id,
                DomainEvent.command_id
                == command_id_for(workflow_run_id, expected_next_stage),
            )
        )
        if (
            next_event is None
            or next_event.event_type != WORKFLOW_EVENT_TYPE
            or next_event.event_version != WORKFLOW_EVENT_VERSION
            or next_event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or next_event.aggregate_id != workflow_run_id
            or next_event.payload_hash != semantic_hash(next_event.payload or {})
            or (next_event.payload or {}).get("stage") != expected_next_stage.value
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_NEXT_EVENT_DRIFT"
            )
        return V2NarrationTimingRecoveryResult(
            workflow_run_id=workflow_run_id,
            authority_id=authority.id,
            receipt_id=receipt.id,
            media_effect_ledger_id=receipt.media_effect_ledger_id,
            workflow_command_receipt_id=command_receipt.id,
            workflow_state=run.state,
            next_domain_event_id=next_event.id,
            replayed=True,
        )

    @contextmanager
    def _recovery_lock(self, workflow_run_id: uuid.UUID):
        """Serialize the recovery across commits and crash-released sessions."""

        lock_key = int.from_bytes(
            hashlib.sha256(f"v2-timing-recovery:{workflow_run_id}".encode()).digest()[
                :8
            ],
            byteorder="big",
            signed=False,
        ) & ((1 << 63) - 1)
        lock_session = self.session_factory()
        acquired = False
        try:
            acquired = bool(
                lock_session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": lock_key},
                ).scalar_one()
            )
            if not acquired:
                raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_IN_PROGRESS")
            yield
        finally:
            if acquired:
                lock_session.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                )
            lock_session.close()

    @staticmethod
    def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
        """Claim the only provider submission with an O_EXCL durable journal."""

        import json
        import os

        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        )
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError as exc:
            if _load_json(path) != payload:
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_REQUEST_MISMATCH"
                ) from exc
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_OUTCOME_UNKNOWN"
            ) from exc

    @staticmethod
    def _verified_ledger_matches_authority(
        ledger: V2ProductionEffectLedger,
        authority: V2NarrationTimingRecoveryAuthority | None,
    ) -> bool:
        if authority is None:
            return False
        journal = dict(ledger.effect_journal or {})
        return bool(
            ledger.result_type == "V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE"
            and ledger.result_hash
            and ledger.completed_at is not None
            and journal.get("timeline_hash") == ledger.result_hash
            and journal.get("timing_recovery_authority_id") == str(authority.id)
            and journal.get("timing_recovery_authority_hash")
            == authority.authority_hash
            and journal.get("provider_call_count") == 2
            and journal.get("tts_provider_call_count") == 1
            and journal.get("tts_retry_count") == 0
            and journal.get("forced_alignment_provider_call_count") == 1
        )

    def _forced_alignment_client(
        self, capture: Callable[[dict[str, Any]], None]
    ) -> Any:
        if self.client is not None:
            if hasattr(self.client, "response_capture"):
                self.client.response_capture = capture
            return self.client
        if self.client_factory is not None:
            try:
                return self.client_factory(response_capture=capture)
            except TypeError:
                return self.client_factory()
        return ElevenLabsForcedAlignmentClient(response_capture=capture)

    def _load_response_capture(
        self,
        path: Path,
        *,
        authority: V2NarrationTimingRecoveryAuthority,
        request_hash: str,
    ) -> dict[str, Any]:
        payload = _load_json(path)
        body = {key: value for key, value in payload.items() if key != "content_hash"}
        capture = payload.get("capture")
        if (
            payload.get("content_hash") != content_hash(body)
            or payload.get("authority_id") != str(authority.id)
            or payload.get("authority_hash") != authority.authority_hash
            or payload.get("forced_alignment_request_hash") != request_hash
            or not isinstance(capture, dict)
            or capture.get("content_hash")
            != content_hash(
                {key: value for key, value in capture.items() if key != "content_hash"}
            )
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_RESPONSE_MISMATCH"
            )
        return {
            **capture,
            "provider_response_hash": capture["content_hash"],
            "wrapper_hash": payload["content_hash"],
        }

    @staticmethod
    def _validate_exact_alignment(
        evidence: Any,
        normalized: Any,
        *,
        raw_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Prove exact text coverage without inventing zero-duration timing.

        ElevenLabs can truthfully return characters whose rounded start and end
        are equal.  ``CharacterAlignment`` deliberately cannot represent those
        as timed characters.  The immutable response capture remains the
        complete character-sequence authority, while downstream caption timing
        uses the exact provider word boundaries.
        """

        raw_characters = raw_response.get("characters")
        if not isinstance(raw_characters, list):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
            )
        raw_timed: list[tuple[int, str, int, int]] = []
        zero_duration_count = 0
        previous_start = -1
        for index, item in enumerate(raw_characters):
            if not isinstance(item, dict):
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
                )
            character = item.get("text")
            try:
                start_ms = ElevenLabsForcedAlignmentResponseParser._time_ms(
                    item, "start"
                )
                end_ms = ElevenLabsForcedAlignmentResponseParser._time_ms(item, "end")
            except (TypeError, ValueError):
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
                ) from None
            if (
                not isinstance(character, str)
                or len(character) != 1
                or index >= len(normalized.spoken_text)
                or character != normalized.spoken_text[index]
                or start_ms < previous_start
                or start_ms < 0
                or end_ms < start_ms
                or end_ms > evidence.audio_duration_ms
            ):
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
                )
            previous_start = start_ms
            if end_ms == start_ms:
                zero_duration_count += 1
            else:
                raw_timed.append((index, character, start_ms, end_ms))

        characters = sorted(evidence.characters, key=lambda item: item.character_index)
        parsed_timed = [
            (item.character_index, item.character, item.start_ms, item.end_ms)
            for item in characters
        ]
        token_ids = [
            token_id
            for word in evidence.words
            for token_id in word.source_spoken_token_ids
        ]
        expected_token_ids = [item.token_id for item in normalized.spoken_tokens]
        previous_word_end = -1
        word_timing_valid = True
        for word in evidence.words:
            if word.start_ms < previous_word_end or word.end_ms <= word.start_ms:
                word_timing_valid = False
                break
            previous_word_end = word.end_ms
        if (
            evidence.verification_status != "PASS"
            or evidence.missing_tokens
            or evidence.extra_words
            or token_ids != expected_token_ids
            or len(token_ids) != len(set(token_ids))
            or len(evidence.words) != len(normalized.spoken_tokens)
            or any(
                word.source_spoken_token_ids != [token.token_id]
                for word, token in zip(
                    evidence.words, normalized.spoken_tokens, strict=True
                )
            )
            or not word_timing_valid
            or len(raw_characters) != len(normalized.spoken_text)
            or parsed_timed != raw_timed
        ):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
            )
        return {
            "schema_version": "vcos.v2-forced-alignment-recovery-audit.v1",
            "exact_raw_character_sequence": True,
            "raw_character_count": len(raw_characters),
            "provider_timed_character_count": len(raw_timed),
            "provider_zero_duration_character_count": zero_duration_count,
            "zero_duration_character_timing_synthesized": False,
            "exact_word_token_coverage": True,
            "provider_word_count": len(evidence.words),
            "canonical_spoken_token_count": len(normalized.spoken_tokens),
            "caption_timing_source": "ELEVENLABS_FORCED_ALIGNMENT_WORD_BOUNDARIES",
        }

    @staticmethod
    def _timing_seed(
        *,
        scope: _RecoveryScope,
        normalized: Any,
        evidence: Any,
        alignment_audit: dict[str, Any],
    ) -> NarrationTimingSeed:
        caption_words = list(re.finditer(r"\S+", normalized.spoken_text))
        if len(caption_words) != len(evidence.words):
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
            )
        caption_timed_words: list[dict[str, Any]] = []
        for index, (match, token, word) in enumerate(
            zip(
                caption_words,
                normalized.spoken_tokens,
                evidence.words,
                strict=True,
            ),
            start=1,
        ):
            if not (
                match.start() <= token.spoken_span.start
                and token.spoken_span.end <= match.end()
                and word.source_spoken_token_ids == [token.token_id]
            ):
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_EXACT_COVERAGE_REQUIRED"
                )
            caption_timed_words.append(
                {
                    "index": index,
                    "text": match.group(),
                    "start_ms": word.start_ms,
                    "end_ms": word.end_ms,
                    "provider_word_id": word.word_id,
                    "source_spoken_token_ids": list(word.source_spoken_token_ids),
                }
            )
        payload = {
            "provider_key": "elevenlabs_forced_alignment_recovery",
            "provider_request_id": evidence.provider_request_id,
            "audio_asset_ref": evidence.audio_asset_ref,
            "audio_duration_ms": scope.audio_duration_ms,
            "source_text_hash": normalized.source_text_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "original_character_alignment": [
                item.model_dump(mode="json") for item in evidence.characters
            ],
            "normalized_character_alignment": [
                item.model_dump(mode="json") for item in evidence.characters
            ],
            "provider_model_id": str(scope.request_identity["model_id"]),
            "provider_voice_id": str(scope.request_identity["voice_id"]),
            "seed": None,
            "voice_settings": dict(scope.request_identity["voice_settings"]),
            "pronunciation_dictionary_refs": [],
            "response_metadata": {
                "forced_alignment_evidence_hash": evidence.content_hash,
                "provider_request_id_availability": (
                    evidence.provider_request_id_availability
                ),
                "exact_character_coverage": True,
                "exact_token_coverage": True,
                "alignment_audit": dict(alignment_audit),
                "caption_timed_words": caption_timed_words,
                "interpolation_used": False,
                "estimation_used": False,
            },
            "timing_available": True,
            "timing_parse_warnings": list(evidence.warnings),
        }
        return NarrationTimingSeed(**payload, content_hash=content_hash(payload))

    def _audio_duration(self, path: Path) -> int:
        if self.audio_probe is not None:
            value = self.audio_probe(path)
        else:
            from app.services.v2_native_effects import _probe_duration_ms

            value = _probe_duration_ms(self.adapter._builder.ffprobe, path)
        if not isinstance(value, int) or value <= 0:
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_AUDIO_PROBE_INVALID"
            )
        return value

    def _api_key(self) -> str:
        secret = self.settings.elevenlabs_api_key
        value = secret.get_secret_value() if secret is not None else ""
        if not value.strip():
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_CREDENTIAL_MISSING"
            )
        return value

    def _require_provider_preflight(self) -> None:
        """Require the complete offline Forced Alignment execution gate."""

        readiness = MR1AlignmentGatewayAdapter(
            api_key=self._api_key(),
            settings=self.settings,
            workspace_root=self.adapter.root,
        ).preflight()
        if readiness.get("result") != "PASS":
            failed = readiness.get("failed_checks")
            suffix = ",".join(str(item) for item in failed or [])
            raise ValidationFailureError(
                "V2_NARRATION_TIMING_RECOVERY_PROVIDER_PREFLIGHT_FAILED"
                + (f":{suffix}" if suffix else "")
            )

    @staticmethod
    def _script_text(script: ArtifactVersion) -> str:
        text = str((script.content or {}).get("narration_text") or "").strip()
        if not text:
            raise ValidationFailureError("V2_NARRATION_TIMING_RECOVERY_SCRIPT_MISSING")
        return text

    @staticmethod
    def _audio_asset_ref(scope: _RecoveryScope) -> str:
        return f"v2-elevenlabs://{scope.request_identity['idempotency_key']}"


def _authority_body(value: Any) -> dict[str, Any]:
    names = (
        "id",
        "workflow_run_id",
        "video_project_id",
        "media_effect_ledger_id",
        "media_domain_event_id",
        "media_dead_letter_job_id",
        "root_replacement_authority_id",
        "verifier_settlement_authority_id",
        "settlement_qualification_run_id",
        "production_package_artifact_version_id",
        "production_package_hash",
        "script_artifact_version_id",
        "script_content_hash",
        "approved_script_hash",
        "budget_reservation_id",
        "budget_reservation_ref",
        "budget_authority_hash",
        "provider_policy_hash",
        "tts_request_journal_ref",
        "tts_request_identity_hash",
        "tts_idempotency_key",
        "audio_relative_path",
        "audio_checksum_sha256",
        "audio_size_bytes",
        "audio_duration_ms",
        "original_failure_reason_code",
        "forced_alignment_permission_confirmed",
        "max_tts_retries",
        "max_forced_alignment_submissions",
        "schema_version",
        "recovery_reason",
        "authorized_by_actor_type",
        "authorized_by_actor_id",
        "authorized_by_actor_role",
        "created_at",
    )
    raw = (
        value
        if isinstance(value, dict)
        else {name: getattr(value, name) for name in names}
    )
    body = {name: _hash_value(raw[name]) for name in names if name != "id"}
    return {"authority_id": _hash_value(raw["id"]), **body}


def _receipt_body(value: Any) -> dict[str, Any]:
    names = (
        "id",
        "authority_id",
        "workflow_run_id",
        "media_effect_ledger_id",
        "forced_alignment_request_hash",
        "forced_alignment_provider_response_hash",
        "forced_alignment_provider_request_id",
        "forced_alignment_provider_request_id_availability",
        "forced_alignment_evidence_hash",
        "recovered_timing_seed_hash",
        "narration_receipt_hash",
        "canonical_media_timeline_hash",
        "provider_call_count",
        "tts_retry_count",
        "schema_version",
        "recovery_state",
        "created_at",
    )
    raw = (
        value
        if isinstance(value, dict)
        else {name: getattr(value, name) for name in names}
    )
    body = {name: _hash_value(raw[name]) for name in names if name != "id"}
    return {"receipt_id": _hash_value(raw["id"]), **body}


def _hash_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = [
    "V2NarrationTimingRecoveryResult",
    "V2NarrationTimingRecoveryService",
]
