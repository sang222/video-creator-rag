from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from app.contracts.ai_image import AIImageRequest, CompiledImagePrompt, ai_image_stable_hash
from app.contracts.google_gemini_image import (
    GeminiImageCostEstimateSnapshot,
    GeminiImageExecutionGates,
    GeminiImageGenerationRequest,
    GeminiImageOperationReceipt,
)
from app.contracts.asset_acquisition import (
    DriveArchiveReceipt,
    ProductionArchiveManifest,
)
from app.contracts.img_canary import (
    IMGCanaryAttemptLedger,
    IMGCanaryDriveReadinessEvidence,
    IMGCanaryHumanReviewPacket,
    IMGCanaryPreflightEvidence,
    IMGCanaryPreviousRunImmutabilityEvidence,
    IMGCanaryPreviousRunsImmutabilityEvidence,
    IMGCanaryTerminalRunImmutabilitySnapshot,
    IMGCanaryProviderResponseSummary,
    IMGCanaryRunIdentity,
    IMGCanaryNativeHeadlineArtifact,
    IMGCanaryScopedApproval,
    IMGCanarySerializedRequestEvidence,
    IMGCanaryV2ApprovalBinding,
    IMGCanaryV3ApprovalBinding,
    IMGCanaryV3SerializedRequestEvidence,
)
from app.contracts.img_canary_security import (
    IMGCanaryBudgetReservationEvidence,
    IMGCanaryCompromisedCredentialRecord,
    IMGCanaryMonthlyBudgetAuthorityLedger,
    IMGCanaryTaskAuthorizationLedger,
    img_canary_task_authority_identity,
)
from app.contracts.image_visual_quality_control import (
    ImageVisualQualityControlInput,
    ImageVisualQualityControlReport,
)
from app.contracts.native_renderer import (
    CompiledNativeRenderManifest,
    FFmpegCommandManifest,
    NativeRenderExecutionReceipt,
    NativeRenderPlan,
    NativeOverlayPlan,
)
from app.contracts.visual_direction import VisualDirectionContract
from app.contracts.visual_routing import (
    SceneVisualRealizationRequirements,
    VisualSourceDecision,
)
from app.core.config import Settings
from app.providers.google_gemini_image import GoogleGeminiImageAdapter
from app.services.img_canary import (
    IMGCanaryArtifactWriter,
    IMGCanaryAttemptLedgerStore,
    IMGCanaryPlanBuilder,
    IMGCanaryPlanBundle,
    IMGCanaryPreflightService,
    IMGCanaryImageNormalizer,
    IMG_CANARY_CREDENTIAL_INCIDENT_REF,
    IMG_CANARY_MASTER_AUTHORIZATION_REF,
    IMG_CANARY_MASTER_TASK_KEY,
    IMGCanaryNativeReviewPlanBuilder,
)
from app.services.img_canary_security import (
    IMGCanaryCredentialRotationAuthority,
    IMGCanaryMonthlyBudgetAuthority,
    IMGCanarySecurityAuthorityError,
    IMGCanaryTaskAuthorizationStore,
)
from app.services.img_canary_vqc import (
    IMGCanaryVQCEvidenceBuilder,
    img_canary_representative_crop_manifest_path,
    img_canary_representative_crop_paths,
)
from app.services.img_canary_drive import IMGCanaryDriveArchive
from app.services.native_ffmpeg_renderer import (
    FFmpegCommandBuilder,
    NativeFFmpegRenderer,
)
from app.services.production_archive import (
    IMG_CANARY_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES,
    IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES,
    ArchiveSource,
    ProductionArchiveBuilder,
)


IMG_CANARY_EXPLICIT_EXECUTION_TOKEN = (
    "EXECUTE_EXACTLY_ONE_PAID_GEMINI_IMAGE_CANARY"
)
IMG_CANARY_V1_PREVIOUS_RUN_ID = "img-canary-20260718T075252Z-319bacb0"
IMG_CANARY_V2_PREVIOUS_RUN_ID = "img-canary-v2-20260718T091203Z-cce118a4"


@dataclass(frozen=True)
class IMGCanaryPlannedRun:
    bundle: IMGCanaryPlanBundle
    workspace_root: Path
    artifact_paths: dict[str, Path]
    attempt_ledger_path: Path
    planned_attempt: IMGCanaryAttemptLedger
    task_authorization_path: Path
    credential_authority_path: Path
    budget_authority_path: Path
    previous_run_evidence: IMGCanaryPreviousRunImmutabilityEvidence | None = None
    previous_run_evidence_path: Path | None = None
    previous_runs_evidence: IMGCanaryPreviousRunsImmutabilityEvidence | None = None
    previous_runs_evidence_path: Path | None = None


@dataclass(frozen=True)
class IMGCanaryPreparedRun:
    planned: IMGCanaryPlannedRun
    preflight: IMGCanaryPreflightEvidence
    execution_gates: GeminiImageExecutionGates
    preflight_path: Path
    execution_gates_path: Path
    drive_readiness_evidence: IMGCanaryDriveReadinessEvidence | None = None
    drive_readiness_evidence_path: Path | None = None


@dataclass(frozen=True)
class IMGCanaryPaidExecutionResult:
    prepared: IMGCanaryPreparedRun
    operation_receipt: GeminiImageOperationReceipt
    attempt_ledger: IMGCanaryAttemptLedger
    provider_response_summary: IMGCanaryProviderResponseSummary | None
    materialization_receipt: dict[str, object] | None
    original_image_path: Path | None


@dataclass(frozen=True)
class IMGCanaryLocalReviewResult:
    paid_execution: IMGCanaryPaidExecutionResult
    normalized_image_path: Path
    normalization_receipt: dict[str, object]
    vqc_evidence: ImageVisualQualityControlInput
    vqc_report: ImageVisualQualityControlReport
    render_plan: NativeRenderPlan
    compiled_render_manifest: CompiledNativeRenderManifest
    ffmpeg_command_manifest: FFmpegCommandManifest
    render_execution_receipt: NativeRenderExecutionReceipt
    render_qc: object
    review_mp4_path: Path


@dataclass(frozen=True)
class IMGCanaryArchiveBuildResult:
    local_review: IMGCanaryLocalReviewResult
    manifest: ProductionArchiveManifest
    manifest_path: Path
    source_paths_by_role: dict[str, Path]


@dataclass(frozen=True)
class IMGCanaryRunLocalReportSnapshots:
    """Point-in-time reports frozen before the first Drive archive mutation."""

    vqc_report_markdown_path: Path
    vqc_summary_path: Path
    canary_report_markdown_path: Path
    canary_summary_path: Path
    repair_cycles_path: Path


@dataclass(frozen=True)
class IMGCanaryPostPaidCompletion:
    """Verified archive and deliberately still-pending human-review boundary."""

    local_review: IMGCanaryLocalReviewResult
    reports: IMGCanaryRunLocalReportSnapshots
    archive: IMGCanaryArchiveBuildResult
    drive_archive_receipt: DriveArchiveReceipt
    drive_archive_receipt_path: Path
    human_review_packet: IMGCanaryHumanReviewPacket
    human_review_packet_path: Path


class IMGCanaryControlledRunner:
    """The sole orchestration boundary for a real IMG canary submission.

    Planning and preflight are safe/offline. The paid path additionally requires
    an exact explicit token, a persisted PASS preflight, a bound durable attempt
    ledger, and a prebound output path. The adapter itself consumes the ledger
    before transport and atomically materializes the response before success.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        scoped_settings: Settings,
        adapter_factory: Callable[[], GoogleGeminiImageAdapter] | None = None,
        approval_version: str = "v1",
    ) -> None:
        self.repo_root = repo_root.resolve(strict=True)
        if not (self.repo_root / ".git").exists():
            raise ValueError("IMG_CANARY_REPOSITORY_ROOT_INVALID")
        self.settings = scoped_settings
        if approval_version not in {"v1", "v2", "v3"}:
            raise ValueError("IMG_CANARY_APPROVAL_VERSION_INVALID")
        self.approval_version = approval_version
        self.security_root = (
            self.repo_root / "var" / "credentials" / "img-canary"
        ).resolve(strict=False)
        self.adapter_factory = adapter_factory or (
            lambda: GoogleGeminiImageAdapter(self.settings)
        )

    def record_current_credential_compromised(
        self,
        *,
        now: datetime,
    ) -> IMGCanaryCompromisedCredentialRecord:
        credential = (
            self.settings.gemini_api_key.get_secret_value().strip()
            if self.settings.gemini_api_key
            else ""
        )
        if not credential:
            raise ValueError("GEMINI_API_KEY_NOT_CONFIGURED")
        return IMGCanaryCredentialRotationAuthority(
            self.security_root / "compromised-credential.json"
        ).record_compromised(
            credential=credential,
            incident_ref=IMG_CANARY_CREDENTIAL_INCIDENT_REF,
            now=now,
        )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _capture_previous_run_immutability(
        self,
        *,
        now: datetime,
    ) -> IMGCanaryPreviousRunImmutabilityEvidence:
        """Recompute the complete historical v1 snapshot; never mutate it."""

        if now.tzinfo is None:
            raise ValueError("IMG_CANARY_PREVIOUS_RUN_EVIDENCE_TIMEZONE_REQUIRED")
        old_root = (
            self.repo_root
            / "artifacts"
            / "img_canary"
            / IMG_CANARY_V1_PREVIOUS_RUN_ID
        ).resolve(strict=True)
        files = sorted(
            (path for path in old_root.rglob("*") if path.is_file()),
            key=lambda path: str(path.relative_to(self.repo_root)),
        )
        file_hashes: dict[str, str] = {}
        aggregate = hashlib.sha256()
        for path in files:
            relative = str(path.relative_to(self.repo_root))
            digest = self._sha256_file(path)
            file_hashes[relative] = digest
            aggregate.update(f"{digest}  {relative}\n".encode("utf-8"))

        try:
            old_attempt = IMGCanaryAttemptLedger.model_validate_json(
                (old_root / "manifests" / "attempt-ledger.json").read_text(
                    encoding="utf-8"
                )
            )
            old_task = IMGCanaryTaskAuthorizationLedger.model_validate_json(
                (
                    old_root / "manifests" / "task-authorization-consumed.json"
                ).read_text(encoding="utf-8")
            )
            old_receipt = GeminiImageOperationReceipt.model_validate_json(
                (old_root / "manifests" / "provider-operation-receipt.json").read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            raise RuntimeError("IMG_CANARY_PREVIOUS_RUN_EVIDENCE_INVALID") from exc
        canonical_old_task_path = self.security_root / "master-authorization.json"
        if (
            len(files) != 24
            or aggregate.hexdigest()
            != "6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423"
            or self._sha256_file(canonical_old_task_path)
            != "6c115ed2ead3a6a730a26edc775dd68aae91e82dc54ef67661482d9d85c9c440"
            or old_attempt.run_id != IMG_CANARY_V1_PREVIOUS_RUN_ID
            or old_attempt.attempts_consumed != 1
            or old_attempt.status != "BLOCKED_REQUIRES_NEW_APPROVAL"
            or old_task.status != "CONSUMED"
            or old_task.completion_status != "PROVIDER_ATTEMPT_FAILED"
            or old_receipt.provider_status != "NATIVE_SUBMIT_FAILED"
            or old_receipt.provider_error_code != "GEMINI_IMAGE_PROVIDER_HTTP_400"
            or old_receipt.generation_attempts_consumed != 1
            or old_receipt.provider_call_made is not True
            or old_receipt.output_reference is not None
        ):
            raise RuntimeError("IMG_CANARY_PREVIOUS_RUN_IMMUTABILITY_MISMATCH")
        stable_payload: dict[str, object] = {
            "schema_version": "img-canary-v2-previous-run-evidence/v1",
            "previous_run_id": IMG_CANARY_V1_PREVIOUS_RUN_ID,
            "file_count": len(files),
            "file_sha256_by_relative_path": file_hashes,
            "aggregate_sha256": aggregate.hexdigest(),
            "task_authority_file_sha256": self._sha256_file(
                canonical_old_task_path
            ),
            "attempts_consumed": old_attempt.attempts_consumed,
            "task_authorization_status": old_task.status,
            "task_completion_status": old_task.completion_status,
            "provider_status": old_receipt.provider_status,
            "provider_error_code": old_receipt.provider_error_code,
            "provider_output_count": (
                1 if old_receipt.output_reference is not None else 0
            ),
            "external_fallback_used": False,
        }
        evidence_hash = ai_image_stable_hash(stable_payload)
        payload = {
            **stable_payload,
            "evidence_hash": evidence_hash,
            "captured_at": now,
        }
        return IMGCanaryPreviousRunImmutabilityEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    def _capture_previous_runs_immutability(
        self,
        *,
        now: datetime,
    ) -> IMGCanaryPreviousRunsImmutabilityEvidence:
        """Capture stable terminal snapshots of both paid predecessor runs."""

        if now.tzinfo is None:
            raise ValueError("IMG_CANARY_PREVIOUS_RUNS_TIMEZONE_REQUIRED")
        v1_evidence = self._capture_previous_run_immutability(now=now)
        v1_payload: dict[str, object] = {
            "run_id": v1_evidence.previous_run_id,
            "file_count": v1_evidence.file_count,
            "file_sha256_by_relative_path": (
                v1_evidence.file_sha256_by_relative_path
            ),
            "aggregate_sha256": v1_evidence.aggregate_sha256,
            "task_authority_file_sha256": (
                v1_evidence.task_authority_file_sha256
            ),
            "attempts_consumed": v1_evidence.attempts_consumed,
            "task_authorization_status": (
                v1_evidence.task_authorization_status
            ),
            "task_completion_status": v1_evidence.task_completion_status,
            "provider_status": v1_evidence.provider_status,
            "provider_error_code": v1_evidence.provider_error_code,
            "provider_output_count": v1_evidence.provider_output_count,
            "external_fallback_used": v1_evidence.external_fallback_used,
        }
        v1_snapshot = IMGCanaryTerminalRunImmutabilitySnapshot(
            **v1_payload,
            snapshot_hash=ai_image_stable_hash(v1_payload),
        )

        v2_root = (
            self.repo_root
            / "artifacts"
            / "img_canary"
            / IMG_CANARY_V2_PREVIOUS_RUN_ID
        ).resolve(strict=True)
        v2_files = sorted(
            (path for path in v2_root.rglob("*") if path.is_file()),
            key=lambda path: str(path.relative_to(self.repo_root)),
        )
        v2_file_hashes: dict[str, str] = {}
        v2_aggregate = hashlib.sha256()
        for path in v2_files:
            relative = str(path.relative_to(self.repo_root))
            digest = self._sha256_file(path)
            v2_file_hashes[relative] = digest
            v2_aggregate.update(f"{digest}  {relative}\n".encode("utf-8"))
        try:
            v2_attempt = IMGCanaryAttemptLedger.model_validate_json(
                (v2_root / "manifests" / "attempt-ledger.json").read_text(
                    encoding="utf-8"
                )
            )
            v2_task = IMGCanaryTaskAuthorizationLedger.model_validate_json(
                (
                    v2_root / "manifests" / "task-authorization-consumed.json"
                ).read_text(encoding="utf-8")
            )
            v2_receipt = GeminiImageOperationReceipt.model_validate_json(
                (
                    v2_root / "manifests" / "provider-operation-receipt.json"
                ).read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise RuntimeError("IMG_CANARY_V2_TERMINAL_EVIDENCE_INVALID") from exc
        _, _, v2_task_relative_path = img_canary_task_authority_identity(
            IMG_CANARY_V2_PREVIOUS_RUN_ID
        )
        canonical_v2_task_path = self.security_root / v2_task_relative_path
        v2_task_sha256 = self._sha256_file(canonical_v2_task_path)
        if (
            len(v2_files) != 28
            or v2_aggregate.hexdigest()
            != "7528b4c0fcbcb523174d158e6e2e760ba14409d8d05a3df0a330daa990b22603"
            or v2_task_sha256
            != "88bdf88d881b6cbe8d1ee0428344d871053255686b0ff999008ae937bb884b36"
            or v2_attempt.run_id != IMG_CANARY_V2_PREVIOUS_RUN_ID
            or v2_attempt.attempts_consumed != 1
            or v2_attempt.status != "BLOCKED_REQUIRES_NEW_APPROVAL"
            or v2_task.status != "CONSUMED"
            or v2_task.completion_status != "PROVIDER_ATTEMPT_SUBMITTED"
            or v2_receipt.provider_status != "NATIVE_SUBMIT_FAILED"
            or v2_receipt.provider_error_code != "GEMINI_IMAGE_PROVIDER_HTTP_400"
            or v2_receipt.generation_attempts_consumed != 1
            or v2_receipt.provider_call_made is not True
            or v2_receipt.output_reference is not None
        ):
            raise RuntimeError("IMG_CANARY_V2_TERMINAL_IMMUTABILITY_MISMATCH")
        v2_payload: dict[str, object] = {
            "run_id": IMG_CANARY_V2_PREVIOUS_RUN_ID,
            "file_count": len(v2_files),
            "file_sha256_by_relative_path": v2_file_hashes,
            "aggregate_sha256": v2_aggregate.hexdigest(),
            "task_authority_file_sha256": v2_task_sha256,
            "attempts_consumed": v2_attempt.attempts_consumed,
            "task_authorization_status": v2_task.status,
            "task_completion_status": v2_task.completion_status,
            "provider_status": v2_receipt.provider_status,
            "provider_error_code": v2_receipt.provider_error_code,
            "provider_output_count": 0,
            "external_fallback_used": False,
        }
        v2_snapshot = IMGCanaryTerminalRunImmutabilitySnapshot(
            **v2_payload,
            snapshot_hash=ai_image_stable_hash(v2_payload),
        )
        stable_payload: dict[str, object] = {
            "schema_version": "img-canary-v3-previous-runs-evidence/v1",
            "v1_terminal_run": v1_snapshot,
            "v2_terminal_run": v2_snapshot,
        }
        evidence_hash = ai_image_stable_hash(stable_payload)
        payload = {
            **stable_payload,
            "evidence_hash": evidence_hash,
            "captured_at": now,
        }
        return IMGCanaryPreviousRunsImmutabilityEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    def _repository_entry_state(self) -> tuple[bool, bool]:
        """Derive versioned entry/VQC truth from reports, never CLI claims."""

        try:
            vsr = json.loads(
                (self.repo_root / "reports" / "vsr1_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            img1 = json.loads(
                (self.repo_root / "reports" / "img1_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            vqc1 = json.loads(
                (self.repo_root / "reports" / "vqc1_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            old = json.loads(
                (self.repo_root / "reports" / "img_canary_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            v2 = (
                json.loads(
                    (
                        self.repo_root
                        / "reports"
                        / "img_canary_v2_summary.json"
                    ).read_text(encoding="utf-8")
                )
                if self.approval_version == "v3"
                else None
            )
        except Exception:
            return False, False
        vqc_passed = vqc1.get("verdicts", {}).get("VQC1_FINAL") == "PASS"
        entry_passed = bool(
            vsr.get("verdicts", {}).get("VSR1_FINAL") == "PASS"
            and img1.get("verdicts", {}).get("IMG1_FINAL") == "PASS"
            and vqc_passed
            and old.get("verdicts", {}).get("IMG_CANARY_PROVIDER_ATTEMPTS") == 1
            and old.get("verdicts", {}).get("IMG_CANARY_FINAL") == "BLOCKED"
            and old.get("verdicts", {}).get("PROCEED_TO_CH1_FLEX_V2") is False
            and old.get("verdicts", {}).get("MR1_EXECUTION") == "ON_HOLD"
            and old.get("verdicts", {}).get("PROCEED_TO_MR1") is False
        )
        if self.approval_version == "v3":
            v2_verdicts = (v2 or {}).get("verdicts", {})
            entry_passed = bool(
                entry_passed
                and v2_verdicts.get("IMG_CANARY_V2_PROVIDER_ATTEMPTS") == 1
                and v2_verdicts.get("IMG_CANARY_V2_FINAL") == "BLOCKED"
                and v2_verdicts.get("IMG_CANARY_V2_EXTERNAL_FALLBACK_USED")
                is False
                and v2_verdicts.get("PROCEED_TO_CH1_FLEX_V2") is False
                and v2_verdicts.get("MR1_EXECUTION") == "ON_HOLD"
                and v2_verdicts.get("PROCEED_TO_MR1") is False
            )
        return entry_passed, vqc_passed

    def plan(
        self,
        *,
        now: datetime | None = None,
        run_suffix: str | None = None,
    ) -> IMGCanaryPlannedRun:
        timestamp = now or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("IMG_CANARY_RUNNER_TIMEZONE_REQUIRED")
        previous_run_evidence = (
            self._capture_previous_run_immutability(now=timestamp)
            if self.approval_version == "v2"
            else None
        )
        previous_runs_evidence = (
            self._capture_previous_runs_immutability(now=timestamp)
            if self.approval_version == "v3"
            else None
        )
        bundle = IMGCanaryPlanBuilder(
            self.settings,
            approval_version=self.approval_version,
        ).build(
            now=timestamp,
            run_suffix=run_suffix,
            previous_run_evidence_hash=(
                previous_run_evidence.evidence_hash
                if previous_run_evidence is not None
                else None
            ),
            previous_runs_evidence_hash=(
                previous_runs_evidence.evidence_hash
                if previous_runs_evidence is not None
                else None
            ),
        )
        workspace = (
            self.repo_root / "artifacts" / "img_canary" / bundle.run_identity.run_id
        ).resolve(strict=False)
        try:
            workspace.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError("IMG_CANARY_RUNNER_WORKSPACE_ESCAPE") from exc
        if workspace.exists():
            raise FileExistsError("IMG_CANARY_RUN_ID_ALREADY_EXISTS")
        task_key, authorization_ref, task_relative_path = (
            img_canary_task_authority_identity(bundle.run_identity.run_id)
        )
        task_authorization_path = self.security_root / task_relative_path
        fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
            bundle.provider_request
        )
        IMGCanaryTaskAuthorizationStore(task_authorization_path).initialize(
            task_key=task_key,
            authorization_ref=authorization_ref,
            approval_version=(
                self.approval_version.upper()
                if self.approval_version in {"v2", "v3"}
                else None
            ),
            approved_run_id=(
                bundle.run_identity.run_id
                if self.approval_version in {"v2", "v3"}
                else None
            ),
            approved_request_fingerprint=(
                fingerprint
                if self.approval_version in {"v2", "v3"}
                else None
            ),
            approved_prompt_hash=(
                bundle.provider_request.prompt_hash
                if self.approval_version in {"v2", "v3"}
                else None
            ),
            approved_serialized_body_hash=(
                bundle.serialized_request_evidence.serialized_body_hash
                if bundle.serialized_request_evidence is not None
                else None
            ),
            approved_scoped_approval_hash=(
                bundle.v2_approval_binding.content_hash
                if bundle.v2_approval_binding is not None
                else bundle.v3_approval_binding.content_hash
                if bundle.v3_approval_binding is not None
                else None
            ),
            now=timestamp,
        )
        writer = IMGCanaryArtifactWriter(workspace)
        artifact_paths = writer.write_plan_bundle(bundle)
        previous_run_evidence_path: Path | None = None
        if previous_run_evidence is not None:
            previous_run_evidence_path = (
                workspace / "manifests" / "previous-run-immutability.json"
            )
            writer._write_json(
                previous_run_evidence_path,
                previous_run_evidence.model_dump(mode="json"),
            )
            artifact_paths["previous-run-immutability.json"] = (
                previous_run_evidence_path
            )
        previous_runs_evidence_path: Path | None = None
        if previous_runs_evidence is not None:
            previous_runs_evidence_path = (
                workspace / "manifests" / "previous-runs-immutability.json"
            )
            writer._write_json(
                previous_runs_evidence_path,
                previous_runs_evidence.model_dump(mode="json"),
            )
            artifact_paths["previous-runs-immutability.json"] = (
                previous_runs_evidence_path
            )
        ledger_path = workspace / "manifests" / "attempt-ledger.json"
        ledger = IMGCanaryAttemptLedgerStore(ledger_path).create(
            run_id=bundle.run_identity.run_id,
            request_fingerprint=fingerprint,
            idempotency_key=bundle.provider_request.idempotency_key,
            now=timestamp,
        )
        artifact_paths["attempt-ledger.json"] = ledger_path
        credential_authority_path = self.security_root / "compromised-credential.json"
        budget_authority_path = (
            self.security_root / f"budget-{timestamp.strftime('%Y-%m')}.json"
        )
        return IMGCanaryPlannedRun(
            bundle=bundle,
            workspace_root=workspace,
            artifact_paths=artifact_paths,
            attempt_ledger_path=ledger_path,
            planned_attempt=ledger,
            task_authorization_path=task_authorization_path,
            credential_authority_path=credential_authority_path,
            budget_authority_path=budget_authority_path,
            previous_run_evidence=previous_run_evidence,
            previous_run_evidence_path=previous_run_evidence_path,
            previous_runs_evidence=previous_runs_evidence,
            previous_runs_evidence_path=previous_runs_evidence_path,
        )

    def load_planned_run(self, *, run_id: str) -> IMGCanaryPlannedRun:
        """Reload an existing immutable plan for deterministic resume only."""

        workspace = (
            self.repo_root / "artifacts" / "img_canary" / run_id
        ).resolve(strict=True)
        if workspace.name != run_id or workspace.is_symlink():
            raise ValueError("IMG_CANARY_RESUME_WORKSPACE_INVALID")
        manifests = workspace / "manifests"

        def load(name: str, model_type: type):
            path = manifests / name
            if path.is_symlink():
                raise ValueError("IMG_CANARY_RESUME_MANIFEST_SYMLINK_BLOCKED")
            try:
                return model_type.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"IMG_CANARY_RESUME_MANIFEST_INVALID:{name}") from exc

        identity = load("run-identity.json", IMGCanaryRunIdentity)
        if identity.run_id != run_id:
            raise ValueError("IMG_CANARY_RESUME_RUN_ID_MISMATCH")
        is_v2 = run_id.startswith("img-canary-v2-")
        is_v3 = run_id.startswith("img-canary-v3-")
        detected_approval_version = "v3" if is_v3 else "v2" if is_v2 else "v1"
        if detected_approval_version != self.approval_version:
            raise ValueError("IMG_CANARY_RESUME_APPROVAL_VERSION_MISMATCH")
        serialized = (
            load(
                "serialized-request-evidence.json",
                IMGCanaryV3SerializedRequestEvidence
                if is_v3
                else IMGCanarySerializedRequestEvidence,
            )
            if is_v2 or is_v3
            else None
        )
        v2_binding = (
            load("operator-approval-v2-binding.json", IMGCanaryV2ApprovalBinding)
            if is_v2
            else None
        )
        v3_binding = (
            load("operator-approval-v3-binding.json", IMGCanaryV3ApprovalBinding)
            if is_v3
            else None
        )
        bundle = IMGCanaryPlanBundle(
            run_identity=identity,
            requirements=load(
                "scene-requirements.json", SceneVisualRealizationRequirements
            ),
            decision=load("visual-source-decision.json", VisualSourceDecision),
            visual_direction=load(
                "visual-direction-contract.json", VisualDirectionContract
            ),
            headline=load("native-headline.json", IMGCanaryNativeHeadlineArtifact),
            overlay_plan=load("native-overlay-plan.json", NativeOverlayPlan),
            cost=load("cost-estimate.json", GeminiImageCostEstimateSnapshot),
            generic_request=load("ai-image-request.json", AIImageRequest),
            compiled_prompt=load("compiled-image-prompt.json", CompiledImagePrompt),
            provider_request=load(
                "gemini-image-request.json", GeminiImageGenerationRequest
            ),
            approval=load("operator-approval.json", IMGCanaryScopedApproval),
            execution_gates=load("execution-gates.json", GeminiImageExecutionGates),
            serialized_request_evidence=serialized,
            v2_approval_binding=v2_binding,
            v3_approval_binding=v3_binding,
        )
        plan_names = {
            "run-identity.json",
            "scene-requirements.json",
            "visual-source-decision.json",
            "visual-direction-contract.json",
            "native-headline.json",
            "native-overlay-plan.json",
            "cost-estimate.json",
            "ai-image-request.json",
            "compiled-image-prompt.json",
            "gemini-image-request.json",
            "operator-approval.json",
            "execution-gates.json",
            "attempt-ledger.json",
        }
        if is_v2:
            plan_names.update(
                {
                    "serialized-request-evidence.json",
                    "operator-approval-v2-binding.json",
                    "previous-run-immutability.json",
                }
            )
        if is_v3:
            plan_names.update(
                {
                    "serialized-request-evidence.json",
                    "operator-approval-v3-binding.json",
                    "previous-runs-immutability.json",
                }
            )
        artifact_paths = {name: manifests / name for name in plan_names}
        attempt_path = manifests / "attempt-ledger.json"
        attempt = IMGCanaryAttemptLedgerStore(attempt_path).load()
        _, _, task_relative_path = img_canary_task_authority_identity(run_id)
        previous_path = manifests / "previous-run-immutability.json" if is_v2 else None
        previous = (
            load(
                "previous-run-immutability.json",
                IMGCanaryPreviousRunImmutabilityEvidence,
            )
            if is_v2
            else None
        )
        previous_runs_path = (
            manifests / "previous-runs-immutability.json" if is_v3 else None
        )
        previous_runs = (
            load(
                "previous-runs-immutability.json",
                IMGCanaryPreviousRunsImmutabilityEvidence,
            )
            if is_v3
            else None
        )
        planned = IMGCanaryPlannedRun(
            bundle=bundle,
            workspace_root=workspace,
            artifact_paths=artifact_paths,
            attempt_ledger_path=attempt_path,
            planned_attempt=attempt,
            task_authorization_path=self.security_root / task_relative_path,
            credential_authority_path=(
                self.security_root / "compromised-credential.json"
            ),
            budget_authority_path=(
                self.security_root
                / f"budget-{identity.created_at.strftime('%Y-%m')}.json"
            ),
            previous_run_evidence=previous,
            previous_run_evidence_path=previous_path,
            previous_runs_evidence=previous_runs,
            previous_runs_evidence_path=previous_runs_path,
        )
        self._assert_canonical_planned_run_paths(planned)
        return planned

    def load_prepared_run(self, *, run_id: str) -> IMGCanaryPreparedRun:
        planned = self.load_planned_run(run_id=run_id)
        manifests = planned.workspace_root / "manifests"
        try:
            preflight = IMGCanaryPreflightEvidence.model_validate_json(
                (manifests / "preflight.json").read_text(encoding="utf-8")
            )
            gates = GeminiImageExecutionGates.model_validate_json(
                (manifests / "execution-gates-runtime.json").read_text(
                    encoding="utf-8"
                )
            )
            drive_path = manifests / "drive-readiness.json"
            drive = (
                IMGCanaryDriveReadinessEvidence.model_validate_json(
                    drive_path.read_text(encoding="utf-8")
                )
                if drive_path.exists()
                else None
            )
        except Exception as exc:
            raise ValueError("IMG_CANARY_RESUME_PREFLIGHT_INVALID") from exc
        prepared = IMGCanaryPreparedRun(
            planned=planned,
            preflight=preflight,
            execution_gates=gates,
            preflight_path=manifests / "preflight.json",
            execution_gates_path=manifests / "execution-gates-runtime.json",
            drive_readiness_evidence=drive,
            drive_readiness_evidence_path=drive_path if drive is not None else None,
        )
        self._assert_canonical_prepared_paths(prepared)
        return prepared

    def preflight(
        self,
        *,
        planned: IMGCanaryPlannedRun,
        vqc1_final_passed: bool,
        credential_rotation_ref: str,
        drive_readiness_evidence: IMGCanaryDriveReadinessEvidence | None = None,
        worktree_reviewed: bool = True,
        repository_identity_passed: bool = True,
        now: datetime | None = None,
    ) -> IMGCanaryPreparedRun:
        self._assert_canonical_planned_run_paths(planned)
        checked_at = now or datetime.now(UTC)
        is_versioned = planned.bundle.run_identity.run_id.startswith(
            ("img-canary-v2-", "img-canary-v3-")
        )
        if is_versioned:
            repository_entry, repository_vqc = self._repository_entry_state()
            repository_identity_passed = bool(
                repository_identity_passed and repository_entry
            )
            vqc1_final_passed = repository_vqc
        current_ledger = IMGCanaryAttemptLedgerStore(
            planned.attempt_ledger_path
        ).load()
        if current_ledger.content_hash != planned.planned_attempt.content_hash:
            raise ValueError("IMG_CANARY_RUNNER_PLANNED_LEDGER_CHANGED")
        if is_versioned:
            if (
                drive_readiness_evidence is None
                or drive_readiness_evidence.run_id
                != planned.bundle.run_identity.run_id
            ):
                version = (
                    "V3"
                    if planned.bundle.run_identity.run_id.startswith(
                        "img-canary-v3-"
                    )
                    else "V2"
                )
                raise ValueError(
                    f"IMG_CANARY_{version}_DRIVE_READINESS_EVIDENCE_REQUIRED"
                )
        credential_value = (
            self.settings.gemini_api_key.get_secret_value().strip()
            if self.settings.gemini_api_key
            else None
        )
        credential_rotation = IMGCanaryCredentialRotationAuthority(
            planned.credential_authority_path
        ).verify_rotation(
            current_credential=credential_value,
            rotation_ref=credential_rotation_ref,
            now=checked_at,
        )
        task_authorization = IMGCanaryTaskAuthorizationStore(
            planned.task_authorization_path
        ).load()
        configured_cap = Decimal(
            self.settings.extra_ai_image_monthly_budget_usd
            if self.settings.extra_ai_image_monthly_budget_usd is not None
            else self.settings.monthly_ai_budget_usd or 0
        )
        budget_authority = IMGCanaryMonthlyBudgetAuthority(
            planned.budget_authority_path
        )
        budget_authority.initialize(
            authority_ref=(
                f"budget://small-team-ai/{checked_at.strftime('%Y-%m')}/img-canary"
            ),
            billing_period=checked_at.strftime("%Y-%m"),
            dedicated_cap_usd=configured_cap,
            opening_spend_usd=Decimal("0"),
            per_request_hard_cap_usd=Decimal("0.15"),
            now=checked_at,
        )
        fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
            planned.bundle.provider_request
        )
        budget_evidence = budget_authority.inspect_capacity(
            run_id=planned.bundle.run_identity.run_id,
            request_fingerprint=fingerprint,
            request_estimate_usd=planned.bundle.cost.estimated_amount,
            now=checked_at,
        )
        service = IMGCanaryPreflightService()
        evidence = service.evaluate(
            bundle=planned.bundle,
            scoped_settings=self.settings,
            vqc1_final_passed=vqc1_final_passed,
            credential_rotation_evidence=credential_rotation,
            monthly_budget_evidence=budget_evidence,
            task_authorization_evidence=task_authorization,
            attempt_ledger=current_ledger,
            drive_readiness_evidence=drive_readiness_evidence,
            worktree_reviewed=worktree_reviewed,
            repository_identity_passed=repository_identity_passed,
            now=checked_at,
        )
        gates = service.execution_gates(
            bundle=planned.bundle,
            preflight=evidence,
        )
        writer = IMGCanaryArtifactWriter(planned.workspace_root)
        drive_readiness_path: Path | None = None
        if drive_readiness_evidence is not None:
            drive_readiness_path = (
                planned.workspace_root / "manifests" / "drive-readiness.json"
            )
            writer._write_json(
                drive_readiness_path,
                drive_readiness_evidence.model_dump(mode="json"),
            )
        preflight_path = writer.write_preflight(evidence)
        gates_path = planned.workspace_root / "manifests" / "execution-gates-runtime.json"
        writer._write_json(gates_path, gates.model_dump(mode="json"))
        return IMGCanaryPreparedRun(
            planned=planned,
            preflight=evidence,
            execution_gates=gates,
            preflight_path=preflight_path,
            execution_gates_path=gates_path,
            drive_readiness_evidence=drive_readiness_evidence,
            drive_readiness_evidence_path=drive_readiness_path,
        )

    def execute_paid_once(
        self,
        *,
        prepared: IMGCanaryPreparedRun,
        explicit_execution_token: str,
        now: datetime | None = None,
    ) -> IMGCanaryPaidExecutionResult:
        if explicit_execution_token != IMG_CANARY_EXPLICIT_EXECUTION_TOKEN:
            raise PermissionError("IMG_CANARY_EXPLICIT_EXECUTION_TOKEN_REQUIRED")
        self._assert_canonical_planned_run_paths(prepared.planned)
        self._assert_canonical_prepared_paths(prepared)
        if prepared.preflight.status != "PASS" or not prepared.execution_gates.all_passed:
            raise PermissionError("IMG_CANARY_PERSISTED_PASS_PREFLIGHT_REQUIRED")
        try:
            persisted_preflight = IMGCanaryPreflightEvidence.model_validate_json(
                prepared.preflight_path.read_text(encoding="utf-8")
            )
            persisted_gates = GeminiImageExecutionGates.model_validate_json(
                prepared.execution_gates_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise ValueError("IMG_CANARY_PERSISTED_PREFLIGHT_INVALID") from exc
        if (
            persisted_preflight.content_hash != prepared.preflight.content_hash
            or persisted_gates.evidence_hash != prepared.execution_gates.evidence_hash
        ):
            raise ValueError("IMG_CANARY_PERSISTED_PREFLIGHT_CHANGED")

        planned = prepared.planned
        IMGCanaryPreflightService.validate_bundle_cost_integrity(planned.bundle)
        if (
            prepared.preflight.run_id != planned.bundle.run_identity.run_id
            or prepared.preflight.evidence_refs.get("provider_request")
            != planned.bundle.provider_request.content_hash
            or prepared.preflight.evidence_refs.get("approval")
            != planned.bundle.approval.content_hash
            or prepared.preflight.evidence_refs.get("cost")
            != planned.bundle.cost.snapshot_hash
        ):
            raise ValueError("IMG_CANARY_EXECUTION_BUNDLE_PREFLIGHT_BINDING_MISMATCH")
        is_v2 = planned.bundle.run_identity.run_id.startswith("img-canary-v2-")
        is_v3 = planned.bundle.run_identity.run_id.startswith("img-canary-v3-")
        is_versioned = is_v2 or is_v3
        destination = self._original_image_destination(planned)
        store = IMGCanaryAttemptLedgerStore(planned.attempt_ledger_path)
        writer = IMGCanaryArtifactWriter(planned.workspace_root)
        fingerprint = GoogleGeminiImageAdapter.idempotency_fingerprint(
            planned.bundle.provider_request
        )
        task_store = IMGCanaryTaskAuthorizationStore(
            planned.task_authorization_path
        )
        task_authorization = task_store.load()
        current_attempt = store.load()
        if (
            current_attempt.status == "SUCCEEDED"
            and current_attempt.attempts_consumed == 1
            and task_authorization.status == "CONSUMED"
            and task_authorization.claimed_run_id
            == planned.bundle.run_identity.run_id
            and task_authorization.claimed_request_fingerprint == fingerprint
        ):
            return self._load_persisted_paid_success(prepared=prepared)
        if is_versioned:
            version = "V3" if is_v3 else "V2"
            if (
                planned.bundle.serialized_request_evidence is None
                or prepared.drive_readiness_evidence is None
                or prepared.drive_readiness_evidence_path is None
                or (
                    is_v2
                    and (
                        planned.previous_run_evidence is None
                        or planned.previous_run_evidence_path is None
                        or planned.bundle.v2_approval_binding is None
                    )
                )
                or (
                    is_v3
                    and (
                        planned.previous_runs_evidence is None
                        or planned.previous_runs_evidence_path is None
                        or planned.bundle.v3_approval_binding is None
                    )
                )
            ):
                raise PermissionError(
                    f"IMG_CANARY_{version}_RUNTIME_BINDING_EVIDENCE_REQUIRED"
                )
            try:
                persisted_previous = (
                    IMGCanaryPreviousRunImmutabilityEvidence.model_validate_json(
                        planned.previous_run_evidence_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    if is_v2 and planned.previous_run_evidence_path is not None
                    else None
                )
                persisted_previous_runs = (
                    IMGCanaryPreviousRunsImmutabilityEvidence.model_validate_json(
                        planned.previous_runs_evidence_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    if is_v3 and planned.previous_runs_evidence_path is not None
                    else None
                )
                persisted_drive = IMGCanaryDriveReadinessEvidence.model_validate_json(
                    prepared.drive_readiness_evidence_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise ValueError(
                    f"IMG_CANARY_{version}_RUNTIME_EVIDENCE_INVALID"
                ) from exc
            current_previous = (
                self._capture_previous_run_immutability(
                    now=now or datetime.now(UTC)
                )
                if is_v2
                else None
            )
            current_previous_runs = (
                self._capture_previous_runs_immutability(
                    now=now or datetime.now(UTC)
                )
                if is_v3
                else None
            )
            if (
                (
                    is_v2
                    and (
                        persisted_previous is None
                        or planned.previous_run_evidence is None
                        or current_previous is None
                        or persisted_previous.model_dump(mode="json")
                        != planned.previous_run_evidence.model_dump(mode="json")
                        or current_previous.evidence_hash
                        != persisted_previous.evidence_hash
                        or current_previous.file_sha256_by_relative_path
                        != persisted_previous.file_sha256_by_relative_path
                    )
                )
                or (
                    is_v3
                    and (
                        persisted_previous_runs is None
                        or planned.previous_runs_evidence is None
                        or current_previous_runs is None
                        or persisted_previous_runs.model_dump(mode="json")
                        != planned.previous_runs_evidence.model_dump(mode="json")
                        or current_previous_runs.evidence_hash
                        != persisted_previous_runs.evidence_hash
                        or current_previous_runs.v1_terminal_run
                        != persisted_previous_runs.v1_terminal_run
                        or current_previous_runs.v2_terminal_run
                        != persisted_previous_runs.v2_terminal_run
                    )
                )
                or persisted_drive.model_dump(mode="json")
                != prepared.drive_readiness_evidence.model_dump(mode="json")
                or prepared.preflight.evidence_refs.get("drive_readiness")
                != persisted_drive.content_hash
            ):
                raise PermissionError(
                    f"IMG_CANARY_{version}_RUNTIME_EVIDENCE_CHANGED"
                )
            serialization = GoogleGeminiImageAdapter(
                self.settings
            ).capture_official_sdk_serialization(planned.bundle.provider_request)
            if (
                serialization.get("body")
                != GoogleGeminiImageAdapter.expected_serialized_request_body(
                    planned.bundle.provider_request
                )
                or serialization.get("body_sha256")
                != planned.bundle.serialized_request_evidence.serialized_body_hash
                or prepared.preflight.evidence_refs.get("serialized_request_body")
                != serialization.get("body_sha256")
            ):
                raise PermissionError(
                    f"IMG_CANARY_{version}_SERIALIZED_BODY_DRIFT"
                )
        if (
            task_authorization.content_hash
            != prepared.preflight.task_authorization_evidence.content_hash
            or task_authorization.status != "AVAILABLE"
        ):
            raise PermissionError("IMG_CANARY_TASK_AUTHORIZATION_UNAVAILABLE")
        credential_value = (
            self.settings.gemini_api_key.get_secret_value().strip()
            if self.settings.gemini_api_key
            else None
        )
        current_rotation = IMGCanaryCredentialRotationAuthority(
            planned.credential_authority_path
        ).verify_rotation(
            current_credential=credential_value,
            rotation_ref=prepared.preflight.credential_rotation_evidence.rotation_ref,
            now=prepared.preflight.checked_at,
        )
        if (
            current_rotation.content_hash
            != prepared.preflight.credential_rotation_evidence.content_hash
            or current_rotation.status != "PASS"
        ):
            raise PermissionError("IMG_CANARY_CREDENTIAL_ROTATION_AUTHORITY_CHANGED")
        budget_store = IMGCanaryMonthlyBudgetAuthority(
            planned.budget_authority_path
        )
        current_budget = budget_store.load()
        if (
            current_budget.content_hash
            != prepared.preflight.monthly_budget_evidence.authority_ledger_hash
        ):
            raise PermissionError("IMG_CANARY_MONTHLY_BUDGET_AUTHORITY_CHANGED")

        transition_time = now or datetime.now(UTC)
        claimed = task_store.claim(
            run_id=planned.bundle.run_identity.run_id,
            request_fingerprint=fingerprint,
            now=transition_time,
        )
        writer._write_json(
            planned.workspace_root / "manifests" / "task-authorization-claimed.json",
            claimed.model_dump(mode="json"),
        )
        reservation_ref: str | None = None
        receipt: GeminiImageOperationReceipt | None = None
        adapter = self.adapter_factory()
        try:
            reservation = budget_store.reserve(
                run_id=planned.bundle.run_identity.run_id,
                request_fingerprint=fingerprint,
                request_estimate_usd=planned.bundle.cost.estimated_amount,
                now=transition_time,
            )
            if reservation.status not in {"RESERVED", "ALREADY_RESERVED"}:
                raise IMGCanarySecurityAuthorityError(
                    "IMG_CANARY_MONTHLY_BUDGET_RESERVATION_FAILED"
                )
            reservation_ref = reservation.reservation_ref
            writer._write_json(
                planned.workspace_root / "manifests" / "budget-reservation-runtime.json",
                reservation.model_dump(mode="json"),
            )
            runtime_preflight = IMGCanaryPreflightService().evaluate(
                bundle=planned.bundle,
                scoped_settings=self.settings,
                vqc1_final_passed=prepared.preflight.vqc1_final_passed,
                credential_rotation_evidence=current_rotation,
                monthly_budget_evidence=reservation,
                task_authorization_evidence=claimed,
                attempt_ledger=current_attempt,
                drive_readiness_evidence=prepared.drive_readiness_evidence,
                worktree_reviewed=prepared.preflight.worktree_reviewed,
                repository_identity_passed=(
                    prepared.preflight.repository_identity_passed
                ),
                runtime_submission=True,
                planning_preflight_hash=prepared.preflight.content_hash,
                now=transition_time,
            )
            runtime_gates = IMGCanaryPreflightService.execution_gates(
                bundle=planned.bundle,
                preflight=runtime_preflight,
            )
            if runtime_preflight.status != "PASS" or not runtime_gates.all_passed:
                raise PermissionError("IMG_CANARY_RUNTIME_SUBMIT_PREFLIGHT_REQUIRED")
            runtime_preflight_path = (
                planned.workspace_root
                / "manifests"
                / "preflight-runtime-submit.json"
            )
            runtime_gates_path = (
                planned.workspace_root
                / "manifests"
                / "execution-gates-runtime-submit.json"
            )
            writer._write_json(
                runtime_preflight_path,
                runtime_preflight.model_dump(mode="json"),
            )
            writer._write_json(
                runtime_gates_path,
                runtime_gates.model_dump(mode="json"),
            )
            receipt = adapter.submit_generation(
                planned.bundle.provider_request,
                gates=runtime_gates,
                preflight=runtime_preflight,
                preflight_path=runtime_preflight_path,
                execution_gates_path=runtime_gates_path,
                attempt_store=store,
                workspace_root=planned.workspace_root,
                destination_path=destination,
            )
        finally:
            finalized_attempt = store.load()
            provider_attempted = bool(
                finalized_attempt.attempts_consumed == 1
                and finalized_attempt.provider_call_made
            )
            if provider_attempted and reservation_ref:
                spent = budget_store.mark_spent(
                    reservation_ref=reservation_ref,
                    run_id=planned.bundle.run_identity.run_id,
                    request_fingerprint=fingerprint,
                    now=datetime.now(UTC),
                )
                writer._write_json(
                    planned.workspace_root / "manifests" / "budget-authority-spent.json",
                    spent.model_dump(mode="json"),
                )
            live_task = task_store.load()
            if is_versioned and live_task.status == "CONSUMED":
                if live_task.completion_status != "PROVIDER_ATTEMPT_SUBMITTED":
                    raise RuntimeError(
                        "IMG_CANARY_VERSIONED_TASK_SUBMIT_BOUNDARY_INVALID"
                    )
                consumed = live_task
            else:
                completion_status = (
                    "PROVIDER_ATTEMPT_SUBMITTED"
                    if is_versioned and provider_attempted
                    else "PROVIDER_ATTEMPT_COMPLETED"
                    if finalized_attempt.status == "SUCCEEDED"
                    else "PROVIDER_ATTEMPT_FAILED"
                    if provider_attempted
                    else "FAIL_CLOSED_AFTER_CLAIM"
                )
                consumed = task_store.consume(
                    run_id=planned.bundle.run_identity.run_id,
                    request_fingerprint=fingerprint,
                    completion_status=completion_status,
                    now=datetime.now(UTC),
                )
            writer._write_json(
                planned.workspace_root / "manifests" / "task-authorization-consumed.json",
                consumed.model_dump(mode="json"),
            )
        if receipt is None:
            raise RuntimeError("IMG_CANARY_PROVIDER_RECEIPT_MISSING_AFTER_SUBMIT")
        writer._write_json(
            planned.workspace_root / "manifests" / "provider-operation-receipt.json",
            receipt.model_dump(mode="json"),
        )
        final_ledger = store.load()
        summary_contract: IMGCanaryProviderResponseSummary | None = None
        materialization: dict[str, object] | None = None
        original_path: Path | None = None
        try:
            safe_summary = adapter.provider_response_summary_for(receipt)
        except ValueError:
            safe_summary = None
        if safe_summary is not None:
            writer._write_json(
                planned.workspace_root / "manifests" / "provider-response-summary-raw-safe.json",
                safe_summary,
            )
        if receipt.normalized_status == "SUCCEEDED":
            if final_ledger.status != "SUCCEEDED" or not destination.is_file():
                raise RuntimeError("IMG_CANARY_SUCCESS_WITHOUT_DURABLE_OUTPUT_OR_LEDGER")
            materialization = adapter.materialization_receipt_for(receipt)
            writer._write_json(
                planned.workspace_root / "manifests" / "materialization-receipt.json",
                materialization,
            )
            if safe_summary is None:
                raise RuntimeError("IMG_CANARY_PROVIDER_SUMMARY_MISSING")
            summary_contract = self._provider_summary_contract(
                prepared=prepared,
                receipt=receipt,
                ledger=final_ledger,
                summary=safe_summary,
            )
            writer._write_json(
                planned.workspace_root / "manifests" / "provider-response-summary.json",
                summary_contract.model_dump(mode="json"),
            )
            original_path = destination
        return IMGCanaryPaidExecutionResult(
            prepared=prepared,
            operation_receipt=receipt,
            attempt_ledger=final_ledger,
            provider_response_summary=summary_contract,
            materialization_receipt=materialization,
            original_image_path=original_path,
        )

    def _assert_canonical_planned_run_paths(
        self,
        planned: IMGCanaryPlannedRun,
    ) -> None:
        run_id = planned.bundle.run_identity.run_id
        expected_workspace = (
            self.repo_root / "artifacts" / "img_canary" / run_id
        ).resolve(strict=False)
        expected_security_root = (
            self.repo_root / "var" / "credentials" / "img-canary"
        ).resolve(strict=False)
        _, _, task_relative_path = img_canary_task_authority_identity(run_id)
        expected_paths = {
            "workspace": expected_workspace,
            "attempt": expected_workspace / "manifests" / "attempt-ledger.json",
            "task": expected_security_root / task_relative_path,
            "credential": expected_security_root / "compromised-credential.json",
            "budget": expected_security_root
            / f"budget-{planned.bundle.run_identity.created_at.strftime('%Y-%m')}.json",
        }
        actual_paths = {
            "workspace": planned.workspace_root,
            "attempt": planned.attempt_ledger_path,
            "task": planned.task_authorization_path,
            "credential": planned.credential_authority_path,
            "budget": planned.budget_authority_path,
        }
        if any(
            Path(actual_paths[name]).resolve(strict=False) != expected
            for name, expected in expected_paths.items()
        ):
            raise ValueError("IMG_CANARY_NONCANONICAL_RUN_AUTHORITY_PATH")

    @staticmethod
    def _original_image_destination(planned: IMGCanaryPlannedRun) -> Path:
        filename = (
            "original-generated.jpg"
            if planned.bundle.run_identity.run_id.startswith(
                ("img-canary-v2-", "img-canary-v3-")
            )
            else "original-generated.raster"
        )
        return planned.workspace_root / "source" / filename

    @staticmethod
    def _assert_canonical_prepared_paths(prepared: IMGCanaryPreparedRun) -> None:
        manifests = prepared.planned.workspace_root / "manifests"
        if (
            prepared.preflight_path.resolve(strict=False)
            != (manifests / "preflight.json").resolve(strict=False)
            or prepared.execution_gates_path.resolve(strict=False)
            != (manifests / "execution-gates-runtime.json").resolve(strict=False)
        ):
            raise ValueError("IMG_CANARY_NONCANONICAL_PLANNING_PREFLIGHT_PATH")

    @staticmethod
    def _load_persisted_paid_success(
        *,
        prepared: IMGCanaryPreparedRun,
    ) -> IMGCanaryPaidExecutionResult:
        planned = prepared.planned
        root = planned.workspace_root
        destination = IMGCanaryControlledRunner._original_image_destination(planned)
        try:
            receipt = GeminiImageOperationReceipt.model_validate_json(
                (root / "manifests" / "provider-operation-receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            summary = IMGCanaryProviderResponseSummary.model_validate_json(
                (root / "manifests" / "provider-response-summary.json").read_text(
                    encoding="utf-8"
                )
            )
            materialization = json.loads(
                (root / "manifests" / "materialization-receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            ledger = IMGCanaryAttemptLedgerStore(planned.attempt_ledger_path).load()
            resolved = destination.resolve(strict=True)
        except Exception as exc:
            raise RuntimeError("IMG_CANARY_PERSISTED_PAID_SUCCESS_INVALID") from exc
        if (
            receipt.normalized_status != "SUCCEEDED"
            or ledger.status != "SUCCEEDED"
            or summary.run_id != planned.bundle.run_identity.run_id
            or materialization.get("local_path") != str(resolved)
            or materialization.get("size_bytes") != resolved.stat().st_size
            or materialization.get("sha256")
            != GoogleGeminiImageAdapter._file_sha256(resolved)
        ):
            raise RuntimeError("IMG_CANARY_PERSISTED_PAID_SUCCESS_MISMATCH")
        return IMGCanaryPaidExecutionResult(
            prepared=prepared,
            operation_receipt=receipt,
            attempt_ledger=ledger,
            provider_response_summary=summary,
            materialization_receipt=materialization,
            original_image_path=resolved,
        )

    def build_local_review(
        self,
        *,
        paid_execution: IMGCanaryPaidExecutionResult,
        comparison_asset_refs: list[str] | None = None,
        comparison_asset_sha256: list[str] | None = None,
        now: datetime | None = None,
    ) -> IMGCanaryLocalReviewResult:
        """Normalize, run real-byte VQC, and render without another provider call."""

        if (
            paid_execution.operation_receipt.normalized_status != "SUCCEEDED"
            or paid_execution.attempt_ledger.status != "SUCCEEDED"
            or paid_execution.original_image_path is None
            or paid_execution.provider_response_summary is None
            or paid_execution.materialization_receipt is None
        ):
            raise PermissionError("IMG_CANARY_SUCCESSFUL_PAID_OUTPUT_REQUIRED")
        planned = paid_execution.prepared.planned
        normalized = planned.workspace_root / "source" / "normalized-1920x1080.png"
        normalization = IMGCanaryImageNormalizer().normalize(
            source_path=paid_execution.original_image_path,
            destination_path=normalized,
            workspace_root=planned.workspace_root,
        )
        evidence, vqc_report = IMGCanaryVQCEvidenceBuilder().build_and_evaluate(
            bundle=planned.bundle,
            normalized_image_path=normalized,
            provider_response=paid_execution.provider_response_summary,
            attempt_ledger=paid_execution.attempt_ledger,
            materialization_receipt=paid_execution.materialization_receipt,
            normalization_receipt=normalization,
            comparison_asset_refs=comparison_asset_refs,
            comparison_asset_sha256=comparison_asset_sha256,
            now=now,
        )
        if not vqc_report.archive_eligible_for_review:
            raise PermissionError("IMG_CANARY_REAL_IMAGE_VQC_NOT_ARCHIVE_ELIGIBLE")

        render_plan, compiled = IMGCanaryNativeReviewPlanBuilder().build(
            bundle=planned.bundle,
            vqc_report=vqc_report,
            normalized_image_path=normalized,
            workspace_root=planned.workspace_root,
            image_checksum=vqc_report.image_sha256 or "",
            created_at=now,
        )
        command = FFmpegCommandBuilder(planned.workspace_root).build_image_review(
            compiled,
            run_key=planned.bundle.run_identity.run_id,
            image_path=normalized,
            headline_artifact=planned.bundle.headline,
            duration_seconds=6.0,
        )
        render_receipt, render_qc = NativeFFmpegRenderer(
            planned.workspace_root,
            smoke_enabled=True,
            production_enabled=False,
        ).execute(
            compiled,
            command,
            purpose="IMG_CANARY_NON_PRODUCTION_REVIEW",
        )
        review_mp4 = Path(render_receipt.output_path).resolve(strict=True)
        writer = IMGCanaryArtifactWriter(planned.workspace_root)
        artifacts = {
            "normalization-receipt.json": normalization,
            "vqc1-evidence.json": evidence.model_dump(mode="json"),
            "vqc1-report.json": vqc_report.model_dump(mode="json"),
            "native-render-plan.json": render_plan.model_dump(mode="json"),
            "compiled-render-manifest.json": compiled.model_dump(mode="json"),
            "ffmpeg-command-manifest.json": command.model_dump(mode="json"),
            "render-execution-receipt.json": render_receipt.model_dump(mode="json"),
            "render-qc.json": render_qc.model_dump(mode="json"),
        }
        for name, payload in artifacts.items():
            writer._write_json(
                planned.workspace_root / "manifests" / name,
                payload,
            )
        return IMGCanaryLocalReviewResult(
            paid_execution=paid_execution,
            normalized_image_path=normalized,
            normalization_receipt=normalization,
            vqc_evidence=evidence,
            vqc_report=vqc_report,
            render_plan=render_plan,
            compiled_render_manifest=compiled,
            ffmpeg_command_manifest=command,
            render_execution_receipt=render_receipt,
            render_qc=render_qc,
            review_mp4_path=review_mp4,
        )

    @staticmethod
    def _versioned_archive_role_paths(
        *,
        run_id: str,
        normalized_image_path: Path,
        manifest_paths: Path,
    ) -> dict[str, Path]:
        """Return only the version-specific review and authority evidence."""

        is_v2 = run_id.startswith("img-canary-v2-")
        is_v3 = run_id.startswith("img-canary-v3-")
        if not (is_v2 or is_v3):
            return {}
        role_paths = {
            **img_canary_representative_crop_paths(normalized_image_path),
            "IMG_CANARY_VQC1_REPORT_JSON": manifest_paths / "vqc1-report.json",
            "IMG_CANARY_RENDER_EXECUTION_RECEIPT": manifest_paths
            / "render-execution-receipt.json",
        }
        if is_v2:
            role_paths.update(
                {
                    "IMG_CANARY_V2_PREVIOUS_RUN_IMMUTABILITY": manifest_paths
                    / "previous-run-immutability.json",
                    "IMG_CANARY_V2_SERIALIZED_REQUEST_EVIDENCE": manifest_paths
                    / "serialized-request-evidence.json",
                    "IMG_CANARY_V2_OPERATOR_APPROVAL_BINDING": manifest_paths
                    / "operator-approval-v2-binding.json",
                    "IMG_CANARY_V2_DRIVE_READINESS_EVIDENCE": manifest_paths
                    / "drive-readiness.json",
                    "IMG_CANARY_V2_RUNTIME_PREFLIGHT": manifest_paths
                    / "preflight-runtime-submit.json",
                    "IMG_CANARY_V2_RUNTIME_EXECUTION_GATES": manifest_paths
                    / "execution-gates-runtime-submit.json",
                }
            )
        else:
            role_paths.update(
                {
                    "IMG_CANARY_V3_PREVIOUS_RUNS_IMMUTABILITY": manifest_paths
                    / "previous-runs-immutability.json",
                    "IMG_CANARY_V3_SERIALIZED_REQUEST_EVIDENCE": manifest_paths
                    / "serialized-request-evidence.json",
                    "IMG_CANARY_V3_OPERATOR_APPROVAL_BINDING": manifest_paths
                    / "operator-approval-v3-binding.json",
                    "IMG_CANARY_V3_DRIVE_READINESS_EVIDENCE": manifest_paths
                    / "drive-readiness.json",
                    "IMG_CANARY_V3_RUNTIME_PREFLIGHT": manifest_paths
                    / "preflight-runtime-submit.json",
                    "IMG_CANARY_V3_RUNTIME_EXECUTION_GATES": manifest_paths
                    / "execution-gates-runtime-submit.json",
                }
            )
        return role_paths

    def build_archive_manifest(
        self,
        *,
        local_review: IMGCanaryLocalReviewResult,
        vqc_report_markdown_path: Path,
        vqc_summary_path: Path,
        canary_report_markdown_path: Path,
        canary_summary_path: Path,
        repair_cycles_path: Path,
    ) -> IMGCanaryArchiveBuildResult:
        """Freeze the complete local review package before any Drive mutation."""

        paid = local_review.paid_execution
        planned = paid.prepared.planned
        is_v2 = planned.bundle.run_identity.run_id.startswith("img-canary-v2-")
        is_v3 = planned.bundle.run_identity.run_id.startswith("img-canary-v3-")
        required_archive_roles = (
            IMG_CANARY_V3_REQUIRED_ARCHIVE_ROLES
            if is_v3
            else IMG_CANARY_V2_REQUIRED_ARCHIVE_ROLES
            if is_v2
            else IMG_CANARY_REQUIRED_ARCHIVE_ROLES
        )
        if paid.original_image_path is None:
            raise ValueError("IMG_CANARY_ARCHIVE_ORIGINAL_IMAGE_MISSING")
        root = planned.workspace_root
        writer = IMGCanaryArtifactWriter(root)
        for report_path in (
            vqc_report_markdown_path,
            vqc_summary_path,
            canary_report_markdown_path,
            canary_summary_path,
            repair_cycles_path,
        ):
            if not report_path.resolve(strict=True).is_file():
                raise ValueError("IMG_CANARY_ARCHIVE_REPORT_SOURCE_INVALID")

        generated: dict[str, Path] = {}

        def write(name: str, payload: dict[str, object]) -> Path:
            path = root / "archive-inputs" / name
            writer._write_json(path, payload)
            generated[name] = path
            return path

        try:
            runtime_budget_reservation = IMGCanaryBudgetReservationEvidence.model_validate_json(
                (root / "manifests" / "budget-reservation-runtime.json").read_text(
                    encoding="utf-8"
                )
            )
            spent_budget_authority = IMGCanaryMonthlyBudgetAuthorityLedger.model_validate_json(
                (root / "manifests" / "budget-authority-spent.json").read_text(
                    encoding="utf-8"
                )
            )
            consumed_task_authorization = IMGCanaryTaskAuthorizationLedger.model_validate_json(
                (root / "manifests" / "task-authorization-consumed.json").read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            raise ValueError("IMG_CANARY_ARCHIVE_RUNTIME_AUTHORITY_EVIDENCE_INVALID") from exc
        runtime_reservation = next(
            (
                item
                for item in spent_budget_authority.reservations
                if item.run_id == planned.bundle.run_identity.run_id
                and item.request_fingerprint
                == paid.attempt_ledger.request_fingerprint
            ),
            None,
        )
        if (
            runtime_budget_reservation.status
            not in {"RESERVED", "ALREADY_RESERVED"}
            or runtime_reservation is None
            or runtime_reservation.status != "SPENT"
            or consumed_task_authorization.status != "CONSUMED"
            or consumed_task_authorization.claimed_run_id
            != planned.bundle.run_identity.run_id
            or consumed_task_authorization.claimed_request_fingerprint
            != paid.attempt_ledger.request_fingerprint
        ):
            raise ValueError("IMG_CANARY_ARCHIVE_RUNTIME_AUTHORITY_EVIDENCE_MISMATCH")

        cost_attempt = write(
            "cost-attempt-evidence.json",
            {
                "run_id": planned.bundle.run_identity.run_id,
                "cost_estimate": planned.bundle.cost.model_dump(mode="json"),
                "scoped_approval": planned.bundle.approval.model_dump(mode="json"),
                "monthly_budget_evidence": paid.prepared.preflight.monthly_budget_evidence.model_dump(
                    mode="json"
                ),
                "runtime_budget_reservation": runtime_budget_reservation.model_dump(
                    mode="json"
                ),
                "spent_budget_authority": spent_budget_authority.model_dump(
                    mode="json"
                ),
                "credential_rotation_evidence": paid.prepared.preflight.credential_rotation_evidence.model_dump(
                    mode="json"
                ),
                "task_authorization_consumed": consumed_task_authorization.model_dump(
                    mode="json"
                ),
                "attempt_ledger": paid.attempt_ledger.model_dump(mode="json"),
                "provider_response_hash": paid.provider_response_summary.content_hash
                if paid.provider_response_summary
                else None,
                "provider_attempts_consumed": paid.attempt_ledger.attempts_consumed,
                "external_fallback_used": False,
            },
        )
        overlay_binding = write(
            "native-overlay-binding.json",
            local_review.vqc_evidence.native_overlay.model_dump(mode="json"),
        )
        provenance = write(
            "provenance.json",
            local_review.vqc_evidence.rights_disclosure.model_dump(mode="json"),
        )
        disclosure = write(
            "synthetic-media-disclosure.json",
            {
                "run_id": planned.bundle.run_identity.run_id,
                "provider": "google_gemini_image",
                "model": planned.bundle.provider_request.model_id,
                "synthetic_foundation": True,
                "native_exact_text_authority": True,
                "generated_evidence_authority": False,
                "production_eligible": False,
                "not_publishable": True,
                "rights_evidence_hash": local_review.vqc_evidence.rights_disclosure.content_hash,
            },
        )
        qc_crops = write(
            "qc-crops.json",
            {
                "run_id": planned.bundle.run_identity.run_id,
                "image_sha256": local_review.vqc_report.image_sha256,
                "inspection_state": "PENDING_HUMAN_OBSERVATION",
                "representative_crop_refs": local_review.vqc_evidence.artifact_inspection.representative_crop_refs,
                "regions": [
                    item.model_dump(mode="json")
                    for item in local_review.vqc_evidence.artifact_inspection.detected_or_suspected_regions
                ],
                "no_absence_claim_from_metadata": True,
            },
        )
        not_publishable = write(
            "not-publishable.json",
            {
                "run_id": planned.bundle.run_identity.run_id,
                "production_eligible": False,
                "not_publishable": True,
                "human_review_state": "PENDING",
                "final_media_ref_created": False,
                "youtube_upload_allowed": False,
                "proceed_to_ch1_flex_v2": False,
            },
        )

        paths = planned.artifact_paths
        manifest_paths = root / "manifests"
        role_paths: dict[str, Path] = {
            "IMG_CANARY_RUN_IDENTITY": paths["run-identity.json"],
            "IMG_CANARY_OPERATOR_APPROVAL": paths["operator-approval.json"],
            "IMG_CANARY_VISUAL_SOURCE_DECISION": paths["visual-source-decision.json"],
            "IMG_CANARY_AI_IMAGE_REQUEST": paths["ai-image-request.json"],
            "IMG_CANARY_COMPILED_PROMPT": paths["compiled-image-prompt.json"],
            "IMG_CANARY_GEMINI_REQUEST": paths["gemini-image-request.json"],
            "IMG_CANARY_PREFLIGHT": paid.prepared.preflight_path,
            "IMG_CANARY_ATTEMPT_LEDGER": planned.attempt_ledger_path,
            "IMG_CANARY_PROVIDER_OPERATION_RECEIPT": manifest_paths
            / "provider-operation-receipt.json",
            "IMG_CANARY_PROVIDER_RESPONSE_SUMMARY": manifest_paths
            / "provider-response-summary.json",
            "IMG_CANARY_COST_ATTEMPT_EVIDENCE": cost_attempt,
            "IMG_CANARY_MATERIALIZATION_RECEIPT": manifest_paths
            / "materialization-receipt.json",
            "IMG_CANARY_NORMALIZATION_RECEIPT": manifest_paths
            / "normalization-receipt.json",
            "IMG_CANARY_NATIVE_OVERLAY_PLAN": paths["native-overlay-plan.json"],
            "IMG_CANARY_NATIVE_OVERLAY_BINDING": overlay_binding,
            "IMG_CANARY_NATIVE_RENDER_PLAN": manifest_paths / "native-render-plan.json",
            "IMG_CANARY_COMPILED_RENDER_MANIFEST": manifest_paths
            / "compiled-render-manifest.json",
            "IMG_CANARY_FFMPEG_COMMAND_MANIFEST": manifest_paths
            / "ffmpeg-command-manifest.json",
            "IMG_CANARY_PROVENANCE": provenance,
            "IMG_CANARY_SYNTHETIC_DISCLOSURE": disclosure,
            "IMG_CANARY_ORIGINAL_IMAGE": paid.original_image_path,
            "IMG_CANARY_NORMALIZED_IMAGE": local_review.normalized_image_path,
            "IMG_CANARY_REVIEW_MP4": local_review.review_mp4_path,
            "IMG_CANARY_QC_CROPS": (
                img_canary_representative_crop_manifest_path(
                    local_review.normalized_image_path
                )
                if is_v2 or is_v3
                else qc_crops
            ),
            "IMG_CANARY_VQC1_EVIDENCE": manifest_paths / "vqc1-evidence.json",
            "IMG_CANARY_RENDER_QC": manifest_paths / "render-qc.json",
            "IMG_CANARY_VQC1_REPORT": vqc_report_markdown_path,
            "IMG_CANARY_VQC1_SUMMARY": vqc_summary_path,
            "IMG_CANARY_REPORT": canary_report_markdown_path,
            "IMG_CANARY_SUMMARY": canary_summary_path,
            "IMG_CANARY_REPAIR_CYCLES": repair_cycles_path,
            "IMG_CANARY_NOT_PUBLISHABLE": not_publishable,
        }
        role_paths.update(
            self._versioned_archive_role_paths(
                run_id=planned.bundle.run_identity.run_id,
                normalized_image_path=local_review.normalized_image_path,
                manifest_paths=manifest_paths,
            )
        )
        if any(path is None for path in role_paths.values()):
            raise ValueError("IMG_CANARY_ARCHIVE_SOURCE_MISSING")
        package_index_path = root / "archive-inputs" / "package-index.json"
        role_paths["IMG_CANARY_PACKAGE_INDEX"] = package_index_path
        index_payload = {
            "run_id": planned.bundle.run_identity.run_id,
            "required_roles": sorted(required_archive_roles),
            "files": {
                role: {
                    "source_path": str(path.resolve(strict=True)),
                    "size_bytes": path.stat().st_size,
                    "sha256": GoogleGeminiImageAdapter._file_sha256(path),
                }
                for role, path in sorted(role_paths.items())
                if role != "IMG_CANARY_PACKAGE_INDEX"
            },
            "package_complete_before_drive": True,
            "local_purge_allowed": False,
        }
        writer._write_json(package_index_path, index_payload)
        if set(role_paths) != set(required_archive_roles):
            missing = sorted(set(required_archive_roles) - set(role_paths))
            extra = sorted(set(role_paths) - set(required_archive_roles))
            raise ValueError(
                "IMG_CANARY_ARCHIVE_ROLE_SET_MISMATCH:"
                + ",".join([*missing, *extra])
            )
        sources = [
            ArchiveSource(
                logical_role=role,
                source_path=path.resolve(strict=True),
                required_for_archive=True,
                required_for_local_purge=False,
            )
            for role, path in sorted(role_paths.items())
        ]
        manifest = ProductionArchiveBuilder().build(
            manifest_id=f"{planned.bundle.run_identity.run_id}-archive-v1",
            project_id=planned.bundle.run_identity.project_id,
            package_id=planned.bundle.run_identity.package_id,
            sources=sources,
            required_roles=required_archive_roles,
        )
        manifest_path = root / "archive" / "production-archive-manifest.json"
        writer._write_json(manifest_path, manifest.model_dump(mode="json"))
        return IMGCanaryArchiveBuildResult(
            local_review=local_review,
            manifest=manifest,
            manifest_path=manifest_path,
            source_paths_by_role=role_paths,
        )

    @staticmethod
    def verify_drive_readiness(
        *,
        drive_archive: IMGCanaryDriveArchive,
        access_token: str,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> IMGCanaryDriveReadinessEvidence | None:
        """Prove the configured OAuth token can read the configured Drive root.

        This probe is intentionally safe and transient: it persists neither the
        token nor the returned Drive metadata. The controlled CLI performs it
        before the paid Gemini submission so an unusable archive target cannot
        strand a newly paid output.
        """

        if not isinstance(access_token, str) or not access_token.strip():
            raise RuntimeError("IMG_CANARY_DRIVE_OAUTH_NOT_READY")
        root_folder_id = drive_archive.root_folder_id
        if not root_folder_id:
            raise RuntimeError("IMG_CANARY_DRIVE_ROOT_FOLDER_NOT_READY")
        try:
            metadata = drive_archive.provider.get_file_metadata(
                access_token=access_token,
                drive_file_id=root_folder_id,
            )
        except Exception:
            # Provider exception messages can contain request material. The
            # readiness boundary exposes only a fixed reason code.
            raise RuntimeError("IMG_CANARY_DRIVE_ROOT_OR_OAUTH_NOT_READY") from None
        if (
            metadata.drive_file_id != root_folder_id
            or metadata.mime_type != "application/vnd.google-apps.folder"
        ):
            raise RuntimeError("IMG_CANARY_DRIVE_ROOT_FOLDER_NOT_READY")
        if run_id is None:
            return None
        checked_at = now or datetime.now(UTC)
        readiness_version = "v3" if run_id.startswith("img-canary-v3-") else "v2"
        payload: dict[str, object] = {
            "schema_version": (
                f"img-canary-{readiness_version}-drive-readiness/v1"
            ),
            "run_id": run_id,
            "status": "PASS",
            "root_folder_id": root_folder_id,
            "root_folder_mime_type": "application/vnd.google-apps.folder",
            "oauth_access_token_persisted": False,
            "raw_drive_response_persisted": False,
            "checked_at": checked_at,
        }
        return IMGCanaryDriveReadinessEvidence(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )

    def write_run_local_report_snapshots(
        self,
        *,
        local_review: IMGCanaryLocalReviewResult,
        repair_cycles: list[dict[str, object]] | None = None,
        now: datetime | None = None,
    ) -> IMGCanaryRunLocalReportSnapshots:
        """Freeze truthful pre-archive reports from the real run contracts.

        These are explicitly point-in-time snapshots. They never claim Drive or
        human approval before either event has occurred.
        """

        captured_at = now or datetime.now(UTC)
        if captured_at.tzinfo is None:
            raise ValueError("IMG_CANARY_REPORT_SNAPSHOT_TIMEZONE_REQUIRED")
        paid = local_review.paid_execution
        planned = paid.prepared.planned
        run_id = planned.bundle.run_identity.run_id
        if (
            local_review.vqc_report.run_id != run_id
            or local_review.render_execution_receipt.run_key != run_id
            or paid.attempt_ledger.run_id != run_id
            or paid.attempt_ledger.attempts_consumed != 1
            or paid.attempt_ledger.status != "SUCCEEDED"
        ):
            raise ValueError("IMG_CANARY_REPORT_SNAPSHOT_RUN_BINDING_INVALID")

        cycles = [dict(item) for item in (repair_cycles or [])]
        for item in cycles:
            attempts_before = item.get("provider_attempts_before")
            attempts_after = item.get("provider_attempts_after")
            if (
                attempts_before not in (None, 0, 1)
                or attempts_after not in (None, 0, 1)
                or (
                    attempts_before is not None
                    and attempts_after is not None
                    and attempts_before != attempts_after
                )
            ):
                raise ValueError("IMG_CANARY_REPAIR_CYCLE_PROVIDER_ATTEMPT_CHANGED")

        root = planned.workspace_root
        reports_root = root / "report-snapshots"
        writer = IMGCanaryArtifactWriter(root)
        gate_rows = [
            {
                "gate_name": gate.gate_name,
                "result": gate.result,
                "reason_codes": list(gate.reason_codes),
            }
            for gate in local_review.vqc_report.gate_results
        ]
        vqc_payload: dict[str, object] = {
            "schema_version": "img-canary.run-local-vqc-snapshot.v1",
            "captured_at": captured_at.isoformat(),
            "captured_stage": "LOCAL_REVIEW_COMPLETE_ARCHIVE_PENDING",
            "run_id": run_id,
            "image_sha256": local_review.vqc_report.image_sha256,
            "technical_status": local_review.vqc_report.technical_status,
            "creative_review_state": local_review.vqc_report.creative_review_state,
            "human_review_state": local_review.vqc_report.human_review_state,
            "archive_eligible_for_review": local_review.vqc_report.archive_eligible_for_review,
            "verdict": local_review.vqc_report.verdict,
            "gate_results": gate_rows,
            "human_final_approval_auto_passed": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        vqc_payload["snapshot_hash"] = ai_image_stable_hash(vqc_payload)

        canary_payload: dict[str, object] = {
            "schema_version": "img-canary.run-local-execution-snapshot.v1",
            "captured_at": captured_at.isoformat(),
            "captured_stage": "LOCAL_REVIEW_COMPLETE_ARCHIVE_PENDING",
            "run_id": run_id,
            "workspace_root": str(root),
            "provider": "google_gemini_image",
            "model": planned.bundle.provider_request.model_id,
            "image_size": planned.bundle.provider_request.image_size,
            "aspect_ratio": planned.bundle.provider_request.aspect_ratio,
            "output_count": planned.bundle.provider_request.output_count,
            "provider_execution": paid.operation_receipt.normalized_status,
            "provider_attempts_consumed": paid.attempt_ledger.attempts_consumed,
            "external_fallback_used": False,
            "estimated_cost_usd": str(planned.bundle.cost.estimated_amount),
            "actual_cost_usd": (
                str(paid.operation_receipt.actual_cost)
                if paid.operation_receipt.actual_cost is not None
                else None
            ),
            "original_image_path": str(paid.original_image_path),
            "normalized_image_path": str(local_review.normalized_image_path),
            "review_mp4_path": str(local_review.review_mp4_path),
            "technical_image_qc": local_review.vqc_report.technical_status,
            "creative_review": local_review.vqc_report.creative_review_state,
            "native_render_exit_code": local_review.render_execution_receipt.exit_code,
            "native_render_output_sha256": local_review.render_execution_receipt.output_checksum,
            "drive_archive": "PENDING_NOT_STARTED_AT_SNAPSHOT",
            "archive_verified": False,
            "human_review": "PENDING_NOT_OPENED",
            "production_eligible": False,
            "not_publishable": True,
            "proceed_to_ch1_flex_v2": False,
        }
        canary_payload["snapshot_hash"] = ai_image_stable_hash(canary_payload)

        cycles_payload: dict[str, object] = {
            "schema_version": "img-canary.run-local-repair-cycles.v1",
            "captured_at": captured_at.isoformat(),
            "run_id": run_id,
            "repair_cycle_count": len(cycles),
            "repair_cycles": cycles,
            "provider_attempts_at_snapshot": 1,
            "additional_generation_submissions_during_repairs": 0,
        }
        cycles_payload["snapshot_hash"] = ai_image_stable_hash(cycles_payload)

        vqc_summary = reports_root / "vqc1-summary.json"
        canary_summary = reports_root / "img-canary-summary.json"
        repair_cycles_path = reports_root / "img-canary-repair-cycles.json"
        writer._write_json(vqc_summary, vqc_payload)
        writer._write_json(canary_summary, canary_payload)
        writer._write_json(repair_cycles_path, cycles_payload)

        vqc_markdown = reports_root / "vqc1-image-visual-quality-control-report.md"
        canary_markdown = reports_root / "img-canary-google-gemini-image-report.md"
        _write_text_atomic(
            vqc_markdown,
            "\n".join(
                [
                    "# VQC1 real-image run snapshot",
                    "",
                    f"- Run: `{run_id}`",
                    f"- Captured stage: `{vqc_payload['captured_stage']}`",
                    f"- Technical status: `{local_review.vqc_report.technical_status}`",
                    f"- Creative review: `{local_review.vqc_report.creative_review_state}`",
                    f"- Human review: `{local_review.vqc_report.human_review_state}`",
                    f"- Archive eligible for review: `{str(local_review.vqc_report.archive_eligible_for_review).lower()}`",
                    "- Human final approval was not auto-passed.",
                    "",
                ]
            ),
        )
        _write_text_atomic(
            canary_markdown,
            "\n".join(
                [
                    "# IMG canary real-run snapshot",
                    "",
                    f"- Run: `{run_id}`",
                    "- Provider execution: `SUCCEEDED`",
                    "- Provider attempts consumed: `1`",
                    "- External fallback used: `false`",
                    "- Local VQC/render: `COMPLETE`",
                    "- Drive archive at snapshot: `PENDING_NOT_STARTED_AT_SNAPSHOT`",
                    "- Human review: `PENDING_NOT_OPENED`",
                    "- Production eligible: `false`",
                    "",
                ]
            ),
        )
        return IMGCanaryRunLocalReportSnapshots(
            vqc_report_markdown_path=vqc_markdown,
            vqc_summary_path=vqc_summary,
            canary_report_markdown_path=canary_markdown,
            canary_summary_path=canary_summary,
            repair_cycles_path=repair_cycles_path,
        )

    def complete_post_paid_pipeline(
        self,
        *,
        paid_execution: IMGCanaryPaidExecutionResult,
        drive_archive: IMGCanaryDriveArchive,
        access_token: str,
        repair_cycles: list[dict[str, object]] | None = None,
        now: datetime | None = None,
    ) -> IMGCanaryPostPaidCompletion:
        """Normalize, VQC, render, archive, verify, and open human review.

        The method contains no generation call. Any deterministic retry of this
        lower half therefore continues to use the same paid response and ledger.
        """

        if not isinstance(access_token, str) or not access_token.strip():
            raise RuntimeError("IMG_CANARY_DRIVE_OAUTH_NOT_READY")
        if paid_execution.original_image_path is None:
            raise RuntimeError("IMG_CANARY_PAID_OUTPUT_MISSING")
        local_review = self.build_local_review(
            paid_execution=paid_execution,
            now=now,
        )
        reports = self.write_run_local_report_snapshots(
            local_review=local_review,
            repair_cycles=repair_cycles,
            now=now,
        )
        archive = self.build_archive_manifest(
            local_review=local_review,
            vqc_report_markdown_path=reports.vqc_report_markdown_path,
            vqc_summary_path=reports.vqc_summary_path,
            canary_report_markdown_path=reports.canary_report_markdown_path,
            canary_summary_path=reports.canary_summary_path,
            repair_cycles_path=reports.repair_cycles_path,
        )
        planned = paid_execution.prepared.planned
        run_id = planned.bundle.run_identity.run_id
        archive_date = planned.bundle.run_identity.created_at.date().isoformat()
        receipt = drive_archive.upload_and_verify(
            manifest=archive.manifest,
            run_id=run_id,
            archive_date=archive_date,
            access_token=access_token,
        )
        writer = IMGCanaryArtifactWriter(planned.workspace_root)
        receipt_path = planned.workspace_root / "manifests" / "drive-archive-receipt.json"
        writer._write_json(receipt_path, receipt.model_dump(mode="json"))
        if (
            receipt.archive_state != "VERIFIED"
            or receipt.mismatch_reason_codes
            or not receipt.files
            or not all(item.verified for item in receipt.files)
        ):
            raise RuntimeError("IMG_CANARY_DRIVE_ARCHIVE_NOT_VERIFIED")

        ambiguities = sorted(
            {
                reason
                for gate in local_review.vqc_report.gate_results
                if gate.result == "REVIEW_REQUIRED"
                for reason in gate.reason_codes
            }
        )
        packet = writer.build_pending_human_packet(
            run_id=run_id,
            original_image_path=paid_execution.original_image_path,
            normalized_image_path=local_review.normalized_image_path,
            review_mp4_path=local_review.review_mp4_path,
            drive_archive_receipt=receipt,
            archive_manifest=archive.manifest,
            archive_manifest_path=archive.manifest_path,
            attempt_ledger=paid_execution.attempt_ledger,
            vqc_report=local_review.vqc_report,
            render_execution_receipt=local_review.render_execution_receipt,
            estimated_cost_usd=planned.bundle.cost.estimated_amount,
            actual_cost_usd=paid_execution.operation_receipt.actual_cost,
            ambiguities=ambiguities,
        )
        packet_path = planned.workspace_root / "review" / "human-review-packet.json"
        return IMGCanaryPostPaidCompletion(
            local_review=local_review,
            reports=reports,
            archive=archive,
            drive_archive_receipt=receipt,
            drive_archive_receipt_path=receipt_path,
            human_review_packet=packet,
            human_review_packet_path=packet_path,
        )

    @staticmethod
    def _provider_summary_contract(
        *,
        prepared: IMGCanaryPreparedRun,
        receipt: GeminiImageOperationReceipt,
        ledger: IMGCanaryAttemptLedger,
        summary: dict[str, object],
    ) -> IMGCanaryProviderResponseSummary:
        payload = {
            "run_id": prepared.planned.bundle.run_identity.run_id,
            "provider": "google_gemini_image",
            "model": prepared.planned.bundle.provider_request.model_id,
            "provider_status": receipt.provider_status,
            "provider_request_id_ref": ledger.provider_request_id_ref,
            "provider_operation_id_ref": ledger.provider_operation_id_ref,
            "submitted_at": receipt.submitted_at,
            "completed_at": receipt.completed_at,
            "output_count": summary.get("output_count"),
            "output_checksum": summary.get("output_sha256"),
            "image_width": summary.get("image_width"),
            "image_height": summary.get("image_height"),
            "image_format": summary.get("image_format"),
            "size_bytes": summary.get("output_size_bytes"),
            "usage_metadata": summary.get("usage") or {},
            "estimated_cost_usd": prepared.planned.bundle.cost.estimated_amount,
            "actual_cost_usd": receipt.actual_cost,
            "provider_attempts_consumed": 1,
            "raw_response_persisted": False,
            "raw_image_bytes_persisted_in_manifest": False,
            "raw_url_persisted": False,
            "api_key_persisted": False,
            "external_fallback_used": False,
        }
        return IMGCanaryProviderResponseSummary(
            **payload,
            content_hash=ai_image_stable_hash(payload),
        )


def _write_text_atomic(path: Path, content: str) -> None:
    """Durably replace a generated Markdown snapshot."""

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with part.open("x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
        descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except Exception:
        part.unlink(missing_ok=True)
        raise


__all__ = [
    "IMG_CANARY_EXPLICIT_EXECUTION_TOKEN",
    "IMGCanaryArchiveBuildResult",
    "IMGCanaryControlledRunner",
    "IMGCanaryPostPaidCompletion",
    "IMGCanaryPaidExecutionResult",
    "IMGCanaryLocalReviewResult",
    "IMGCanaryPlannedRun",
    "IMGCanaryPreparedRun",
    "IMGCanaryRunLocalReportSnapshots",
]
