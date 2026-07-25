from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import func, select, update

from app.contracts.mr1 import (
    MR1FinalMediaCloseoutCommand,
    MR1ProviderAttemptContinuationCommand,
    MR1ProviderAttemptContinuationReviewCommand,
    MR1StartCommand,
)
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    FinalMediaRef,
    ReviewTask,
)
from app.services.mr1_real_production import (
    MR1ProviderGateways,
    MR1RealProductionService,
    mr1_drive_finalization_idempotency_key,
)
from app.services.mr1_reapproval import MR1ReapprovalService
from tests.test_mr1_reapproval_v3 import _approved_revision
from tests.test_mr1_sc04_composite_authority import (
    _approve_mr1,
    _approved_sc04_revision,
)


RUN_ARTIFACT_TYPE = "mr1_execution_run"
ATTEMPT_LEDGER_ARTIFACT_TYPE = "mr1_provider_attempt_ledger"
REVIEW_CANDIDATE_ARTIFACT_TYPE = "mr1_review_media_candidate"
DRIVE_RECEIPT_ARTIFACT_TYPE = "mr1_drive_archive_receipt"
PEXELS_SCENES = ("SC-04", "SC-07", "SC-09")
ALL_SCENES = tuple(f"SC-{index:02d}" for index in range(1, 10))
DRIVE_IDEMPOTENCY_PHASES = [
    {
        "phase": "CANONICAL_REVIEW_ARCHIVE",
        "operation_key": "google_drive:archive",
        "boundary": "PRE_HUMAN_PASS",
        "max_mutations": 1,
        "cost_usd": 0.0,
    },
    {
        "phase": "FINALIZATION_SUPPLEMENT",
        "operation_key": "google_drive:finalization-supplement",
        "boundary": "POST_HUMAN_PASS_PRE_FINAL_MEDIA_REF",
        "max_mutations": 1,
        "cost_usd": 0.0,
    },
]


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return deepcopy(value)
    raise TypeError(f"unsupported fake gateway request: {type(value)!r}")


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class FakeNarrationGateway:
    """Gateway-level fake: the hook is the durable network-submit boundary."""

    def __init__(
        self, *, fail_before_submit: bool = False, fail_after_submit: bool = False
    ):
        self.fail_before_submit = fail_before_submit
        self.fail_after_submit = fail_after_submit
        self.preflight_calls = 0
        self.calls: list[dict[str, Any]] = []

    def preflight(self, **_: Any) -> dict[str, Any]:
        self.preflight_calls += 1
        return {"result": "PASS", "billable_generation_probe": False}

    def execute_once(
        self,
        request: Any,
        *,
        destination: Path,
        before_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = _dump(request)
        if self.fail_before_submit:
            raise RuntimeError("FAKE_NARRATION_SERIALIZATION_FAILED_BEFORE_SUBMIT")
        before_submit()
        self.calls.append(payload)
        if self.fail_after_submit:
            raise RuntimeError("FAKE_NARRATION_CONNECTION_LOST_AFTER_SUBMIT")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mr1-fake-narration-authority")
        audio_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "provider": "elevenlabs",
            "provider_request_id": "fake-elevenlabs-request-1",
            "request_hash": payload["request_hash"],
            "audio_path": str(destination),
            "audio_sha256": audio_hash,
            "audio_duration_ms": 540_000,
            "sample_rate": 48_000,
            "channels": 2,
            "provider_call_made": True,
            "actual_cost_usd": 0.0,
        }


class FakeAlignmentGateway:
    def __init__(self):
        self.preflight_calls = 0
        self.calls: list[dict[str, Any]] = []

    def preflight(self, **_: Any) -> dict[str, Any]:
        self.preflight_calls += 1
        return {"result": "PASS", "billable_generation_probe": False}

    def execute_once(
        self,
        request: Any,
        *,
        audio_path: Path,
        before_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = _dump(request)
        assert audio_path.is_file()
        assert (
            hashlib.sha256(audio_path.read_bytes()).hexdigest()
            == payload["audio_sha256"]
        )
        before_submit()
        self.calls.append(payload)
        spoken_tokens = payload.get("spoken_tokens") or [
            {"token_id": "token-0001", "text": "fixture"}
        ]
        step_ms = max(1, 540_000 // len(spoken_tokens))
        words = [
            {
                "text": token["text"],
                "source_spoken_token_ids": [token["token_id"]],
                "start_ms": index * step_ms,
                "end_ms": min(540_000, (index + 1) * step_ms),
                "confidence": 1.0,
            }
            for index, token in enumerate(spoken_tokens)
        ]
        return {
            "provider": "forced_alignment",
            "provider_request_id": "fake-alignment-request-1",
            "request_hash": payload["request_hash"],
            "audio_sha256": payload["audio_sha256"],
            "spoken_text_hash": payload["spoken_text_hash"],
            "verified_words": words,
            "token_coverage": 1.0,
            "missing_tokens": [],
            "extra_tokens": [],
            "verification_status": "PASS",
            "estimated_timing_fallback_used": False,
            "provider_call_made": True,
            "actual_cost_usd": 0.0,
        }


class FakePexelsGateway:
    """One approved scene is one logical flow: one search plus one chosen download."""

    def __init__(
        self,
        *,
        fail_before_search_for: str | None = None,
        fail_after_search_for: str | None = None,
    ) -> None:
        self.fail_before_search_for = fail_before_search_for
        self.fail_after_search_for = fail_after_search_for
        self.preflight_calls = 0
        self.search_calls: list[dict[str, Any]] = []
        self.download_calls: list[dict[str, Any]] = []

    def preflight(self, **_: Any) -> dict[str, Any]:
        self.preflight_calls += 1
        return {"result": "PASS", "billable_generation_probe": False}

    def acquire_scene_once(
        self,
        request: Any,
        *,
        destination: Path,
        before_search_submit: Callable[[], None],
        before_download_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = _dump(request)
        scene_id = payload["scene_id"]
        if scene_id == self.fail_before_search_for:
            raise RuntimeError("FAKE_PEXELS_REQUEST_INVALID_BEFORE_SUBMIT")
        before_search_submit()
        self.search_calls.append(payload)
        if scene_id == self.fail_after_search_for:
            raise RuntimeError("FAKE_PEXELS_CONNECTION_LOST_AFTER_SEARCH_SUBMIT")
        before_download_submit()
        self.download_calls.append(payload)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"mr1-fake-pexels:{scene_id}".encode())
        checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "provider": "pexels_api",
            "scene_id": scene_id,
            "route": "PEXELS_VIDEO",
            "request_hash": payload["request_hash"],
            "provider_asset_id": f"pexels-{scene_id.lower()}",
            "provider_file_id": f"pexels-file-{scene_id.lower()}",
            "creator_ref": f"pexels-creator://{scene_id.lower()}",
            "source_page_url": (
                f"https://www.pexels.com/video/pexels-{scene_id.lower()}/"
            ),
            "license_ref": "https://www.pexels.com/license/",
            "rights_policy_ref": "pexels-license://v1",
            "local_path": str(destination),
            "sha256": checksum,
            "width": 1920,
            "height": 1080,
            "duration_ms": 8_000,
            "search_submit_count": 1,
            "download_submit_count": 1,
            "provider_call_made": True,
            "actual_cost_usd": 0.0,
        }


class SemanticFailPexelsGateway(FakePexelsGateway):
    def acquire_scene_once(
        self,
        request: Any,
        *,
        destination: Path,
        before_search_submit: Callable[[], None],
        before_download_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = _dump(request)
        if payload["scene_id"] != "SC-07":
            return super().acquire_scene_once(
                request,
                destination=destination,
                before_search_submit=before_search_submit,
                before_download_submit=before_download_submit,
            )
        before_search_submit()
        self.search_calls.append(payload)
        raise RuntimeError("PEXELS_SEMANTIC_FIT_INADEQUATE")


class SafeEvidenceSemanticFailPexelsGateway(FakePexelsGateway):
    def acquire_scene_once(
        self,
        request: Any,
        *,
        destination: Path,
        before_search_submit: Callable[[], None],
        before_download_submit: Callable[[], None],
    ) -> dict[str, Any]:
        payload = _dump(request)
        if payload["scene_id"] != "SC-07":
            return super().acquire_scene_once(
                request,
                destination=destination,
                before_search_submit=before_search_submit,
                before_download_submit=before_download_submit,
            )
        before_search_submit()
        self.search_calls.append(payload)
        evidence = {
            "schema_version": "vcos.pexels-search-ranking-failure.v2",
            "reason_code": "PEXELS_SEMANTIC_FIT_INADEQUATE",
            "recorded_at": "2026-07-24T00:00:00+00:00",
            "request_id": payload["idempotency_key"],
            "query_plan": {
                "plan_hash": "f" * 64,
                "queries": [
                    "private raw query must not enter the durable ledger"
                ],
            },
            "retrieval_evidence": {
                "query_used": (
                    "private raw query must not enter the durable ledger"
                ),
                "provider_result_count": 3,
                "provider_result_order": [
                    "private-candidate-1",
                    "private-candidate-2",
                    "private-candidate-3",
                ],
            },
            "cross_scene_exclusion": {
                "excluded_provider_asset_count": 0,
                "excluded_provider_asset_ids": [],
                "filter_applied_before_ranking": True,
            },
            "technical_viability_filter": {
                "input_candidate_count": 3,
                "viable_candidate_count": 3,
            },
            "ranking": {
                "selected_candidate_id": None,
                "ranking_verdict": "FAIL",
            },
            "semantic_scoring_evidence": [],
            "semantic_fit_gate": {
                "threshold": 0.78,
                "selected_semantic_relevance": 0.5,
                "highest_ranked_semantic_relevance": 0.5,
                "result": "FAIL",
            },
            "rate_limit": {"remaining": 199},
            "sanitization": {
                "authorization_header_persisted": False,
                "api_key_persisted": False,
                "raw_provider_payload_persisted": False,
                "raw_media_urls_persisted": False,
                "candidate_text_normalized_to_tokens": True,
                "secret_values_exposed": False,
            },
        }
        evidence["content_hash"] = _stable_hash(evidence)
        evidence_path = destination.parent / (
            "pexels-search-ranking-failure-test.json"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(evidence, sort_keys=True),
            encoding="utf-8",
        )
        error = RuntimeError("PEXELS_SEMANTIC_FIT_INADEQUATE")
        error.safe_evidence_kind = "PEXELS_SEARCH_RANKING_FAILURE"
        error.safe_evidence = {
            **evidence,
            "evidence_path": str(evidence_path),
            "evidence_persisted": True,
        }
        raise error


class FakeDriveGateway:
    def __init__(
        self,
        *,
        fail_before_first_mutation_once: bool = False,
        fail_after_first_mutation_once: bool = False,
        fail_finalization_after_mutation_once: bool = False,
    ):
        self.preflight_calls = 0
        self.upload_invocation_count = 0
        self.calls: list[dict[str, Any]] = []
        self.finalization_calls: list[dict[str, Any]] = []
        self.fail_before_first_mutation_once = fail_before_first_mutation_once
        self.fail_after_first_mutation_once = fail_after_first_mutation_once
        self.fail_finalization_after_mutation_once = (
            fail_finalization_after_mutation_once
        )

    def preflight(self, **_: Any) -> dict[str, Any]:
        self.preflight_calls += 1
        return {"result": "PASS", "billable_generation_probe": False}

    def upload_or_resume_and_verify(
        self,
        manifest: Any,
        *,
        archive_identity: str,
        journal_path: Path,
        before_first_mutation: Callable[[], None],
    ) -> dict[str, Any]:
        self.upload_invocation_count += 1
        payload = _dump(manifest)
        if (
            self.fail_before_first_mutation_once
            and self.upload_invocation_count == 1
        ):
            raise RuntimeError("FAKE_DRIVE_INTERRUPTED_BEFORE_FIRST_MUTATION")
        before_first_mutation()
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(
            json.dumps({"archive_identity": archive_identity}, sort_keys=True),
            encoding="utf-8",
        )
        call = {
            "manifest": payload,
            "archive_identity": archive_identity,
            "journal_path": str(journal_path),
        }
        self.calls.append(call)
        if self.fail_after_first_mutation_once and len(self.calls) == 1:
            raise RuntimeError("FAKE_DRIVE_INTERRUPTED_AFTER_FIRST_MUTATION")
        files = payload["files"]
        drive_folder_id = "fake-drive-folder-mr1"
        items = []
        proofs = []
        for index, source in enumerate(files, start=1):
            role = source["logical_role"]
            role_component = role.strip().casefold()
            local_path = Path(source["source_path"])
            archive_path = source.get("archive_path") or (
                f"items/{role_component}/{index:03d}-{role_component}-{local_path.name}"
            )
            digest = hashlib.md5(
                local_path.read_bytes(), usedforsecurity=False
            ).hexdigest()
            item = {
                "logical_role": role,
                "name": Path(archive_path).name,
                "source_path": str(local_path.resolve()),
                "archive_path": archive_path,
                "size_bytes": local_path.stat().st_size,
                "sha256": hashlib.sha256(local_path.read_bytes()).hexdigest(),
                "md5": digest,
            }
            items.append(item)
            proofs.append(
                {
                    "logical_role": role,
                    "name": item["name"],
                    "archive_path": archive_path,
                    "drive_file_id": f"fake-drive-file-{index}",
                    "drive_folder_id": drive_folder_id,
                    "local_size_bytes": item["size_bytes"],
                    "remote_size_bytes": item["size_bytes"],
                    "local_sha256": item["sha256"],
                    "remote_sha256": item["sha256"],
                    "local_md5": digest,
                    "remote_md5": digest,
                    "verification_method": "SHA256_PLUS_SIZE",
                    "verified": True,
                }
            )
        ordered = sorted(zip(items, proofs), key=lambda pair: pair[0]["archive_path"])
        items = [pair[0] for pair in ordered]
        proofs = [pair[1] for pair in ordered]
        receipt = {
            "schema_version": "MR1_DRIVE_ARCHIVE_RECEIPT_V1",
            "run_id": payload["run_id"],
            "archive_identity": archive_identity,
            "archive_state": "VERIFIED",
            "archive_manifest_hash": _stable_hash(
                {
                    "run_id": payload["run_id"],
                    "archive_identity": archive_identity,
                    "items": items,
                }
            ),
            "root_relative_path": f"small-team-ai/mr1/{payload['run_id']}",
            "drive_folder_id": drive_folder_id,
            "expected_item_count": len(files),
            "verified_item_count": len(files),
            "remote_item_count": len(files),
            "total_local_size_bytes": sum(item["size_bytes"] for item in items),
            "total_remote_size_bytes": sum(item["size_bytes"] for item in items),
            "items": items,
            "files": proofs,
            "remote_exact_set_verified": True,
            "mismatch_reason_codes": [],
            "transport": "FAKE_GOOGLE_DRIVE_API",
            "verified_at": "2026-07-19T00:00:00+00:00",
            "provider_call_made": True,
        }
        receipt["receipt_hash"] = _stable_hash(receipt)
        receipt.update(
            {
                "ARCHIVE_VERIFIED": True,
                "exact_item_count": len(files),
                "actual_item_count": len(files),
                "verified_item_count": len(files),
                "duplicate_count": 0,
                "parent_verified": True,
                "correct_parent": True,
                "names_verified": True,
                "correct_names": True,
                "sizes_verified": True,
                "size_verification": True,
                "checksums_verified": True,
                "checksum_verification": True,
                "remote_id_journal_ref": str(journal_path),
            }
        )
        return receipt

    def upload_finalization_supplement_and_verify(
        self,
        manifest: Any,
        *,
        archive_identity: str,
        journal_path: Path,
        before_first_mutation: Callable[[], None],
    ) -> dict[str, Any]:
        payload = _dump(manifest)
        before_first_mutation()
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(
            json.dumps(
                {
                    "archive_identity": archive_identity,
                    "archive_phase": "FINALIZATION_SUPPLEMENT",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        call = {
            "manifest": payload,
            "archive_identity": archive_identity,
            "journal_path": str(journal_path),
        }
        self.finalization_calls.append(call)
        if (
            self.fail_finalization_after_mutation_once
            and len(self.finalization_calls) == 1
        ):
            raise RuntimeError("FAKE_DRIVE_FINALIZATION_INTERRUPTED_AFTER_MUTATION")

        drive_folder_id = "fake-drive-folder-mr1-finalization"
        items = []
        proofs = []
        for index, source in enumerate(payload["files"], start=1):
            local_path = Path(source["source_path"])
            digest = hashlib.md5(
                local_path.read_bytes(), usedforsecurity=False
            ).hexdigest()
            item = {
                "logical_role": source["logical_role"],
                "name": source["name"],
                "source_path": str(local_path.resolve()),
                "archive_path": source["archive_path"],
                "size_bytes": local_path.stat().st_size,
                "sha256": hashlib.sha256(local_path.read_bytes()).hexdigest(),
                "md5": digest,
            }
            items.append(item)
            proofs.append(
                {
                    "logical_role": source["logical_role"],
                    "name": source["name"],
                    "archive_path": source["archive_path"],
                    "drive_file_id": f"fake-finalization-drive-file-{index}",
                    "drive_folder_id": drive_folder_id,
                    "local_size_bytes": item["size_bytes"],
                    "remote_size_bytes": item["size_bytes"],
                    "local_sha256": item["sha256"],
                    "remote_sha256": item["sha256"],
                    "local_md5": digest,
                    "remote_md5": digest,
                    "verification_method": "SHA256_PLUS_SIZE",
                    "verified": True,
                }
            )
        receipt = {
            "schema_version": "MR1_DRIVE_ARCHIVE_RECEIPT_V1",
            "run_id": payload["run_id"],
            "archive_identity": archive_identity,
            "archive_manifest_hash": _stable_hash(
                {
                    "run_id": payload["run_id"],
                    "archive_identity": archive_identity,
                    "items": items,
                }
            ),
            "root_relative_path": (
                f"small-team-ai/mr1/{payload['run_id']}/finalization"
            ),
            "drive_folder_id": drive_folder_id,
            "expected_item_count": len(items),
            "verified_item_count": len(items),
            "remote_item_count": len(items),
            "total_local_size_bytes": sum(item["size_bytes"] for item in items),
            "total_remote_size_bytes": sum(item["size_bytes"] for item in items),
            "items": items,
            "files": proofs,
            "remote_exact_set_verified": True,
            "archive_state": "VERIFIED",
            "mismatch_reason_codes": [],
            "provider_call_made": True,
            "transport": "FAKE_GOOGLE_DRIVE_API",
            "verified_at": "2026-07-22T00:00:00+00:00",
        }
        receipt["receipt_hash"] = _stable_hash(receipt)
        receipt.update(
            {
                "archive_phase": "FINALIZATION_SUPPLEMENT",
                "ARCHIVE_VERIFIED": True,
                "exact_item_count": len(items),
                "actual_item_count": len(items),
                "duplicate_count": 0,
                "supplement_manifest_hash": _stable_hash(payload),
                "supplement_item_set_hash": payload["item_set_hash"],
                "canonical_drive_archive_receipt": deepcopy(
                    payload["canonical_drive_archive_receipt"]
                ),
                "verification": {
                    "exact_item_set": True,
                    "exact_item_count": True,
                    "correct_parent": True,
                    "correct_names": True,
                    "size_verified": True,
                    "checksum_readback_verified": True,
                    "duplicate_absence": True,
                },
            }
        )
        return receipt


class FakeLocalContinuation:
    """Deterministic local work is independently resumable from provider outputs."""

    def __init__(self, *, fail_once_at_render: bool = False):
        self.fail_once_at_render = fail_once_at_render
        self.calls: list[dict[str, Any]] = []
        self.temporal_calls: list[dict[str, Any]] = []

    @staticmethod
    def _approved_scene_routes(authority: dict[str, Any]) -> dict[str, str]:
        plan = authority["resolved"]["provider_execution_plan"]["content"]
        routes = {
            item["scene_id"]: item["route"] for item in plan["scene_routes"]
        }
        assert set(routes) == set(ALL_SCENES)
        return routes

    def prepare_temporal_authority_once(
        self,
        *,
        run_id: uuid.UUID,
        workspace: Path,
        authority: dict[str, Any],
        provider_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        self.temporal_calls.append(
            {
                "run_id": str(run_id),
                "authority": deepcopy(authority),
                "provider_outputs": deepcopy(provider_outputs),
            }
        )
        duration_ms = int(provider_outputs["narration"]["audio_duration_ms"])
        step = duration_ms // 9
        windows = []
        for index in range(9):
            start = index * step
            end = duration_ms if index == 8 else (index + 1) * step
            windows.append(
                {
                    "scene_id": f"SC-{index + 1:02d}",
                    "start_ms": start,
                    "end_ms": end,
                    "duration_ms": end - start,
                }
            )
        timeline_hash = _stable_hash(provider_outputs["alignment"])
        policy_ref = (
            "mr1-temporal-policy://supporting-stock-subwindow/"
            "min-8000ms-or-floor-20pct/v1"
        )
        mechanisms = {
            "SC-04": "BRIEF_CONTEXT_THEN_BASELINE_CHECKLIST",
            "SC-07": "BRIEF_CONTEXT_THEN_EXCEPTION_QUEUE",
            "SC-09": "BRIEF_CONTEXT_THEN_FIVE_ITEM_AUDIT",
        }
        approved_routes = self._approved_scene_routes(authority)
        pexels_scenes = tuple(
            scene_id
            for scene_id in ALL_SCENES
            if approved_routes[scene_id] == "PEXELS_VIDEO"
        )
        supporting = []
        for scene_id in pexels_scenes:
            scene = next(item for item in windows if item["scene_id"] == scene_id)
            stock_duration = min(8_000, (scene["duration_ms"] * 20) // 100)
            stock_end = scene["start_ms"] + stock_duration
            supporting.append(
                {
                    "scene_id": scene_id,
                    "stock_context": {
                        "start_ms": scene["start_ms"],
                        "end_ms": stock_end,
                        "duration_ms": stock_duration,
                    },
                    "native_explanation": {
                        "start_ms": stock_end,
                        "end_ms": scene["end_ms"],
                        "duration_ms": scene["duration_ms"] - stock_duration,
                    },
                    "native_mechanism": mechanisms[scene_id],
                    "policy_ref": policy_ref,
                }
            )
        supporting_hash = _stable_hash(
            {
                "schema_version": "mr1.supporting-visual-subwindows.v1",
                "timeline_hash": timeline_hash,
                "policy_ref": policy_ref,
                "supporting_visual_subwindows": supporting,
            }
        )
        temporal = workspace / "temporal"
        temporal.mkdir(parents=True, exist_ok=True)
        (temporal / "canonical-media-timeline.json").write_text(
            json.dumps({"timeline_hash": timeline_hash, "segments": windows}),
            encoding="utf-8",
        )
        return {
            "schema_version": "mr1.temporal-authority-preparation.v1",
            "state": "CANONICAL_TIMELINE_READY",
            "result": "PASS",
            "run_id": str(run_id),
            "timing_authority": "CANONICAL_MEDIA_TIMELINE",
            "timeline_hash": timeline_hash,
            "verified_alignment_hash": timeline_hash,
            "token_coverage": 1.0,
            "audio_duration_ms": duration_ms,
            "scene_windows": windows,
            "supporting_visual_subwindows": supporting,
            "supporting_visual_subwindows_hash": supporting_hash,
            "estimated_timing_fallback_used": False,
            "automatic_visual_fallback_used": False,
            "provider_calls_made_by_continuation": 0,
        }

    def continue_once(
        self,
        *,
        run_id: uuid.UUID,
        workspace: Path,
        authority: dict[str, Any],
        provider_outputs: dict[str, Any],
        resume_from: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "run_id": str(run_id),
                "authority": deepcopy(authority),
                "provider_outputs": deepcopy(provider_outputs),
                "resume_from": resume_from,
            }
        )
        if self.fail_once_at_render and len(self.calls) == 1:
            return {
                "state": "REPAIRABLE_LOCAL_FAILURE",
                "failed_stage": "NATIVE_FFMPEG_RENDER",
                "resume_from": "NATIVE_RENDER_PLAN_READY",
                "provider_outputs_durable": True,
                "reason_codes": ["FAKE_DETERMINISTIC_RENDER_FAILURE"],
            }

        repair_directive_path = workspace / "human_repair_directive.json"
        review_round = 1
        if repair_directive_path.is_file():
            review_round = int(
                json.loads(repair_directive_path.read_text(encoding="utf-8"))[
                    "review_round"
                ]
            )
        output_dir = workspace / "review" / f"round-{review_round:02d}"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "mr1-review.mp4"
        thumbnail = output_dir / "thumbnail.png"
        captions = output_dir / "captions.srt"
        output.write_bytes(f"mr1-fake-rendered-mp4-round-{review_round}".encode())
        thumbnail.write_bytes(f"mr1-fake-thumbnail-round-{review_round}".encode())
        captions.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nSmall Team AI\n",
            encoding="utf-8",
        )
        output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        qc_dir = workspace / "qc" / f"round-{review_round:02d}"
        qc_dir.mkdir(parents=True, exist_ok=True)
        technical_path = qc_dir / "technical-media-qc.json"
        creative_path = qc_dir / "creative-media-qc.json"
        technical_qc = {
            "schema_version": "mr1.technical-media-qc.v1",
            "run_id": str(run_id),
            "result": "PASS",
            "actual_mp4_bytes_probed": True,
            "full_decode_performed": True,
            "checks": {"checksum_sha256": output_hash},
            "output_sha256": output_hash,
            "reason_codes": [],
            "production_eligible": True,
            "not_publishable": True,
            "human_full_watch_still_required": True,
        }
        technical_qc["content_hash"] = _stable_hash(technical_qc)
        creative_qc = {
            "schema_version": "mr1.creative-media-qc.v1",
            "run_id": str(run_id),
            "result": "REVIEW_REQUIRED",
            "output_sha256": output_hash,
            "human_full_watch_required": True,
        }
        creative_qc["content_hash"] = _stable_hash(creative_qc)
        technical_path.write_text(
            json.dumps(technical_qc, sort_keys=True), encoding="utf-8"
        )
        creative_path.write_text(
            json.dumps(creative_qc, sort_keys=True), encoding="utf-8"
        )
        timeline = {
            "result": "PASS",
            "timing_authority": "CANONICAL_MEDIA_TIMELINE",
            "estimated_timing_fallback_used": False,
            "content_hash": _stable_hash(provider_outputs["alignment"]),
        }
        assets_dir = workspace / "assets"
        source_dir = assets_dir / "source"
        normalized_dir = assets_dir / "normalized"
        source_dir.mkdir(parents=True, exist_ok=True)
        normalized_dir.mkdir(parents=True, exist_ok=True)
        provenance_items = []
        approved_routes = self._approved_scene_routes(authority)
        pexels_scenes = tuple(
            scene_id
            for scene_id in ALL_SCENES
            if approved_routes[scene_id] == "PEXELS_VIDEO"
        )
        for scene_id in ALL_SCENES:
            route = approved_routes[scene_id]
            if route == "PEXELS_VIDEO":
                provider_output = provider_outputs[f"pexels:{scene_id}"]
                source_path = Path(provider_output["local_path"])
                rights = {
                    "rights_status": "CONFIRMED",
                    "provider_asset_id": provider_output["provider_asset_id"],
                    "provider_file_id": provider_output["provider_file_id"],
                    "creator_ref": provider_output["creator_ref"],
                    "source_page_url": provider_output["source_page_url"],
                    "license_ref": provider_output["license_ref"],
                    "rights_policy_ref": provider_output["rights_policy_ref"],
                }
            else:
                source_path = source_dir / f"{scene_id}.svg"
                source_path.write_text(
                    f"<svg><title>MR1 {scene_id}</title></svg>",
                    encoding="utf-8",
                )
                rights = {
                    "rights_status": "NOT_REQUIRED",
                    "source_type": "NATIVE_OWNED",
                }
            normalized_path = normalized_dir / f"{scene_id}.mp4"
            normalized_path.write_bytes(
                source_path.read_bytes() + f":normalized:{scene_id}".encode()
            )
            provenance_items.append(
                {
                    "scene_id": scene_id,
                    "route": route,
                    "source_path": str(source_path.resolve()),
                    "source_sha256": hashlib.sha256(
                        source_path.read_bytes()
                    ).hexdigest(),
                    "normalized_path": str(normalized_path.resolve()),
                    "normalized_sha256": hashlib.sha256(
                        normalized_path.read_bytes()
                    ).hexdigest(),
                    "rights": rights,
                    "fallback_used": False,
                }
            )
        provenance_core = {
            "schema_version": "mr1.asset-provenance-manifest.v1",
            "timeline_hash": timeline["content_hash"],
            "items": provenance_items,
            "scene_count": len(ALL_SCENES),
            "native_scene_count": len(ALL_SCENES) - len(pexels_scenes),
            "pexels_scene_count": len(pexels_scenes),
            "provider_substitution_used": False,
            "automatic_fallback_used": False,
            "rights_complete": True,
        }
        provenance = {
            **provenance_core,
            "content_hash": _stable_hash(provenance_core),
        }
        provenance_path = assets_dir / "asset-provenance-manifest.json"
        provenance_path.write_text(
            json.dumps(provenance, sort_keys=True), encoding="utf-8"
        )
        candidate_authority = deepcopy(authority["candidate_authority_bindings"])
        lineage_derivation_checks = {
            "package_version_exact": True,
            "approval_exact": True,
            "profile_snapshot_exact": True,
            "rights_planning_authority_exact": True,
            "synthetic_disclosure_authority_exact": True,
            "provenance_plan_authority_exact": True,
            "actual_provenance_manifest_exact": True,
        }
        project_id = str(
            authority.get("project_id")
            or (authority.get("exact_target") or {}).get("project_id")
        )
        package_id = str(authority["package_artifact_version_id"])
        candidate = {
            "candidate_id": f"mr1-review-candidate:{run_id}:r{review_round}",
            "schema_version": "mr1.review-media-candidate.v1",
            "run_id": str(run_id),
            "project_id": project_id,
            "project_ref": f"video-project://{project_id}",
            "package_artifact_version_id": package_id,
            "package_content_hash": authority["package_content_hash"],
            "package_ref": f"artifact-version://{package_id}",
            "approval_id": authority["approval_id"],
            "approval_content_hash": authority["approval_content_hash"],
            "approval_ref": authority["approval_ref"],
            "canonical_timeline_hash": timeline["content_hash"],
            "review_round": review_round,
            "output_file_ref": str(output),
            "output_sha256": output_hash,
            "thumbnail_path": str(thumbnail),
            "captions_path": str(captions),
            "technical_media_qc_ref": str(technical_path),
            "technical_media_qc_hash": technical_qc["content_hash"],
            "creative_media_qc_ref": str(creative_path),
            "creative_media_qc_hash": creative_qc["content_hash"],
            "technical_media_qc": "PASS",
            "technical_qc_result": "PASS",
            "creative_media_qc": "REVIEW_REQUIRED",
            "creative_review_result": "REVIEW_REQUIRED",
            "candidate_authority_bindings": candidate_authority,
            "candidate_authority_bindings_hash": candidate_authority["content_hash"],
            "asset_provenance_manifest_ref": str(provenance_path.resolve()),
            "asset_provenance_manifest_hash": provenance["content_hash"],
            "asset_provenance_manifest_file_sha256": hashlib.sha256(
                provenance_path.read_bytes()
            ).hexdigest(),
            "lineage_derivation_checks": lineage_derivation_checks,
            "production_eligible": True,
            "not_publishable": True,
            "human_review_status": "PENDING",
            "package_lineage_valid": True,
            "legacy_incomplete_package": False,
            "provenance_complete": True,
            "rights_disclosure_resolved": True,
            "youtube_upload_authorized": False,
            "upload_ready": False,
            "publish_execution_ready": False,
            "youtube_calls": 0,
        }
        candidate["content_hash"] = _stable_hash(candidate)
        return {
            "state": "READY_FOR_ARCHIVE",
            "resume_from": "REVIEW_MEDIA_CANDIDATE_CREATED",
            "canonical_timeline": timeline,
            "media_normalization": {
                "result": "PASS",
                "actual_bytes_probed": True,
            },
            "native_render_plan": {"result": "PASS", "deterministic": True},
            "native_motion_compiler": {"result": "PASS"},
            "native_ffmpeg_render": {
                "result": "PASS",
                "render_attempts": len(self.calls),
                "review_round": review_round,
                "output_file_ref": str(output),
                "output_sha256": output_hash,
            },
            "technical_media_qc": {
                **technical_qc,
            },
            "creative_media_qc": {**creative_qc},
            "review_media_candidate": candidate,
            "archive_sources": [
                {
                    "logical_role": "MR1_FINAL_REVIEW_MP4",
                    "source_path": str(output),
                    "sha256": output_hash,
                },
                {
                    "logical_role": "MR1_TECHNICAL_MEDIA_QC",
                    "source_path": str(technical_path),
                    "sha256": hashlib.sha256(technical_path.read_bytes()).hexdigest(),
                },
                {
                    "logical_role": "MR1_CREATIVE_MEDIA_QC",
                    "source_path": str(creative_path),
                    "sha256": hashlib.sha256(creative_path.read_bytes()).hexdigest(),
                },
                {
                    "logical_role": "MR1_ASSET_PROVENANCE_MANIFEST",
                    "source_path": str(provenance_path),
                    "sha256": hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
                },
                {
                    "logical_role": "CAPTIONS",
                    "source_path": str(captions),
                    "sha256": hashlib.sha256(captions.read_bytes()).hexdigest(),
                },
                {
                    "logical_role": "THUMBNAIL",
                    "source_path": str(thumbnail),
                    "sha256": hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
                },
            ],
        }


def _approved_mr1(db_session, tmp_path: Path) -> dict[str, Any]:
    _, pending, _, reapproval_command, _ = _approved_revision(db_session, tmp_path)
    approval = MR1ReapprovalService(db_session).approve(reapproval_command)
    command = MR1StartCommand(
        approval_id=uuid.UUID(approval["approval_id"]),
        approval_content_hash=approval["approval_content_hash"],
        project_id=uuid.UUID(approval["exact_target"]["project_id"]),
        package_artifact_version_id=uuid.UUID(
            approval["exact_target"]["package_artifact_version_id"]
        ),
    )
    return {"pending": pending, "approval": approval, "command": command}


def _gateways(
    *,
    narration: FakeNarrationGateway | None = None,
    pexels: FakePexelsGateway | None = None,
    drive: FakeDriveGateway | None = None,
) -> tuple[MR1ProviderGateways, dict[str, Any]]:
    values = {
        "narration": narration or FakeNarrationGateway(),
        "alignment": FakeAlignmentGateway(),
        "pexels": pexels or FakePexelsGateway(),
        "drive": drive or FakeDriveGateway(),
    }
    return MR1ProviderGateways(**values), values


def _artifact_count(db_session, project_id: uuid.UUID, artifact_type: str) -> int:
    return int(
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(
                Artifact.video_project_id == project_id,
                Artifact.artifact_type == artifact_type,
            )
        )
        or 0
    )


def _persisted_attempt_ledger(
    db_session,
    *,
    run_artifact_version_id: str,
    operation_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_version = db_session.get(
        ArtifactVersion,
        uuid.UUID(run_artifact_version_id),
    )
    assert run_version is not None
    run_state = deepcopy(run_version.content)
    attempt_artifact = db_session.get(
        Artifact,
        uuid.UUID(run_state["attempt_artifact_ids"][operation_key]),
    )
    assert attempt_artifact is not None
    assert attempt_artifact.current_version_id is not None
    attempt_version = db_session.get(
        ArtifactVersion,
        attempt_artifact.current_version_id,
    )
    assert attempt_version is not None
    return run_state, deepcopy(attempt_version.content)


def _final_human_review_tasks(db_session, project_id: uuid.UUID) -> list[ReviewTask]:
    return list(
        db_session.scalars(
            select(ReviewTask).where(
                ReviewTask.video_project_id == project_id,
                ReviewTask.review_type == "final_human",
                ReviewTask.context_pack_ref.like("mr1-final-human://%"),
            )
        ).all()
    )


def _closeout_command(
    db_session,
    scope: dict[str, Any],
    result: dict[str, Any],
    *,
    decision: str,
    operator_decision_text: str,
) -> MR1FinalMediaCloseoutCommand:
    candidate = result["review_media_candidate"]
    drive = result["drive_archive"]
    approval = db_session.get(ApprovalDecision, scope["command"].approval_id)
    assert approval is not None
    return MR1FinalMediaCloseoutCommand(
        run_id=uuid.UUID(result["run_id"]),
        project_id=scope["command"].project_id,
        review_media_candidate_artifact_version_id=uuid.UUID(
            candidate["artifact_version_id"]
        ),
        review_media_candidate_content_hash=candidate["content_hash"],
        reviewed_output_sha256=candidate["output_sha256"],
        drive_archive_receipt_artifact_version_id=uuid.UUID(
            drive["artifact_version_id"]
        ),
        drive_archive_receipt_content_hash=drive["content_hash"],
        archive_identity=result["archive_identity"],
        decided_by_user_id=approval.decided_by_user_id,
        decision=decision,
        operator_decision_text=operator_decision_text,
    )


def _attempt(
    result: dict[str, Any], provider: str, scene_id: str | None = None
) -> dict[str, Any]:
    matches = [
        item
        for item in result["attempts"]
        if item["provider"] == provider and item.get("scene_id") == scene_id
    ]
    assert len(matches) == 1
    return matches[0]


def _mutation_counts(fakes: dict[str, Any]) -> dict[str, Any]:
    return {
        "narration": len(fakes["narration"].calls),
        "alignment": len(fakes["alignment"].calls),
        "pexels_search": [item["scene_id"] for item in fakes["pexels"].search_calls],
        "pexels_download": [
            item["scene_id"] for item in fakes["pexels"].download_calls
        ],
        "drive": len(fakes["drive"].calls),
    }


@pytest.mark.parametrize("wrong_binding", ["approval_hash", "package_id"])
def test_exact_mr1_approval_and_package_hash_preflight_fail_before_any_submit(
    db_session, tmp_path: Path, wrong_binding: str
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    command = scope["command"]
    if wrong_binding == "approval_hash":
        command = command.model_copy(update={"approval_content_hash": "0" * 64})
    else:
        command = command.model_copy(
            update={"package_artifact_version_id": uuid.uuid4()}
        )
    gateways, fakes = _gateways()
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )

    with pytest.raises(
        ValidationFailureError,
        match="MR1_.*(HASH|PACKAGE).*MISMATCH",
    ):
        service.start(command, gateways=gateways)

    assert _mutation_counts(fakes) == {
        "narration": 0,
        "alignment": 0,
        "pexels_search": [],
        "pexels_download": [],
        "drive": 0,
    }
    assert local.calls == []
    assert (
        _artifact_count(db_session, scope["command"].project_id, RUN_ARTIFACT_TYPE) == 0
    )
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, ATTEMPT_LEDGER_ARTIFACT_TYPE
        )
        == 0
    )


def test_fresh_exact_run_executes_only_approved_routes_and_waits_after_drive(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    gateways, fakes = _gateways()
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )
    final_before = db_session.scalar(select(func.count()).select_from(FinalMediaRef))

    result = service.start(scope["command"], gateways=gateways)

    assert result["run_id"] not in {
        scope["approval"]["approval_id"],
        scope["approval"]["exact_target"]["project_id"],
        scope["approval"]["exact_target"]["package_artifact_version_id"],
    }
    assert result["approval_id"] == scope["approval"]["approval_id"]
    assert result["approval_content_hash"] == scope["approval"]["approval_content_hash"]
    assert result["exact_target"] == scope["approval"]["exact_target"]
    assert result["MR1_ENTRY"] == "PASS"
    assert result["MR1_APPROVAL_BINDING"] == "PASS"
    assert result["MR1_PREFLIGHT"] == "PASS"
    assert result["MR1_REQUIRED_PROVIDER_EXECUTION"] == "PASS"
    assert result["MR1_ELEVENLABS"] == "PASS"
    assert result["MR1_FORCED_ALIGNMENT"] == "PASS"
    assert result["MR1_CANONICAL_TIMELINE"] == "PASS"
    assert result["MR1_PEXELS"] == "PASS"
    assert result["MR1_GEMINI_IMAGE"] == "NOT_REQUIRED"
    assert result["MR1_GOOGLE_VEO"] == "NOT_REQUIRED"
    assert result["MR1_NATIVE_ASSETS"] == "PASS"
    assert result["MR1_ASSET_RESOLUTION"] == "PASS"
    assert result["MR1_REVIEW_MEDIA_CANDIDATE"] == "PASS"
    assert result["MR1_DRIVE_ARCHIVE"] == "PASS"
    assert result["ARCHIVE_VERIFIED"] is True
    assert result["MR1_HUMAN_REVIEW"] == "PENDING"
    assert result["MR1_FINAL_MEDIA_REF"] == "NOT_CREATED"
    assert result["MR1_FINAL"] == "WAITING_HUMAN_REVIEW"
    assert result["DESTINATION_STATUS"] == "PENDING_PLATFORM_ID"
    assert result["UPLOAD_READY"] is False
    assert result["PUBLISH_EXECUTION_READY"] is False
    assert result["PROCEED_TO_PUB1"] is False
    assert result["final_media_ref_id"] is None

    assert _mutation_counts(fakes) == {
        "narration": 1,
        "alignment": 1,
        "pexels_search": list(PEXELS_SCENES),
        "pexels_download": list(PEXELS_SCENES),
        "drive": 1,
    }
    assert {name: gateway.preflight_calls for name, gateway in fakes.items()} == {
        "narration": 1,
        "alignment": 1,
        "pexels": 1,
        "drive": 1,
    }
    assert result["MR1_PROVIDER_CALL_COUNT"] == 6
    assert result["provider_call_counts"] == {
        "elevenlabs_narration": 1,
        "forced_alignment": 1,
        "pexels_scene_flows": 3,
        "google_gemini_image": 0,
        "google_veo": 0,
        "google_drive_archive_flows": 1,
        "youtube": 0,
    }
    assert fakes["drive"].finalization_calls == []
    current_run = db_session.get(
        ArtifactVersion, uuid.UUID(result["run_artifact_version_id"])
    )
    assert current_run is not None
    task_authorization = current_run.content["task_authorization"]
    assert task_authorization["drive_idempotency_phases"] == (DRIVE_IDEMPOTENCY_PHASES)
    assert task_authorization["drive_phase_count"] == 2
    assert task_authorization["drive_phases_are_distinct_authorized_mutations"] is True
    assert not hasattr(gateways, "gemini_image")
    assert not hasattr(gateways, "google_veo")
    assert not hasattr(gateways, "youtube")
    assert len(local.temporal_calls) == 1
    for payload in fakes["pexels"].search_calls:
        assert payload["timing_authority"] == "CANONICAL_MEDIA_TIMELINE"
        assert payload["estimated_timing_fallback_used"] is False
        assert len(payload["canonical_timeline_hash"]) == 64
        assert payload["semantic_fit_threshold"] == 0.78
        assert payload["semantic_fit_threshold_authority"].endswith(
            "pexels.semantic_fit_threshold"
        )
        assert payload["scene_duration_ms"] == (
            payload["scene_end_ms"] - payload["scene_start_ms"]
        )
        assert payload["minimum_duration_seconds"] >= (
            payload["stock_context_duration_ms"] / 1000
        )
        assert payload["minimum_duration_seconds"] < (
            payload["scene_duration_ms"] / 1000
        )

    expected_succeeded_attempts = [
        ("elevenlabs", None),
        ("forced_alignment", None),
        *(("pexels_api", scene_id) for scene_id in PEXELS_SCENES),
    ]
    assert len(result["attempts"]) == len(expected_succeeded_attempts) + 1
    for provider, scene_id in expected_succeeded_attempts:
        item = _attempt(result, provider, scene_id)
        assert item["attempt_cap"] == 1
        assert item["attempt_count"] == 1
        assert item["submit_state"] == "SUCCEEDED"
        assert item["automatic_retry_allowed"] is False
        assert item["provider_substitution_allowed"] is False
        assert len(item["request_hash"]) == 64
        assert item["idempotency_key"]
        if provider == "pexels_api":
            assert item["search_submit_count"] == 1
            assert item["download_submit_count"] == 1
    finalization_attempt = _attempt(result, "google_drive", None)
    assert finalization_attempt["operation"] == "finalization_supplement"
    assert finalization_attempt["state"] == "WAITING_HUMAN_PASS"
    assert finalization_attempt["submit_state"] == "NOT_SUBMITTED"
    assert finalization_attempt["attempt_cap"] == 1
    assert finalization_attempt["attempt_count"] == 0
    assert finalization_attempt["network_submit_started"] is False
    assert finalization_attempt["review_round"] == 1
    assert finalization_attempt["idempotency_key"] == (
        mr1_drive_finalization_idempotency_key(
            run_id=result["run_id"],
            review_round=1,
        )
    )
    assert (
        finalization_attempt["drive_phase_authority"] == (DRIVE_IDEMPOTENCY_PHASES[1])
    )
    assert finalization_attempt["distinct_from_canonical_archive"] is True
    assert finalization_attempt["automatic_retry_allowed"] is False

    scene_executions = {item["scene_id"]: item for item in result["scene_executions"]}
    assert set(scene_executions) == {f"SC-{index:02d}" for index in range(1, 10)}
    assert {scene_id: item["route"] for scene_id, item in scene_executions.items()} == {
        "SC-01": "NATIVE_DIAGRAM",
        "SC-02": "NATIVE_DIAGRAM",
        "SC-03": "NATIVE_DIAGRAM",
        "SC-04": "PEXELS_VIDEO",
        "SC-05": "NATIVE_DIAGRAM",
        "SC-06": "NATIVE_DIAGRAM",
        "SC-07": "PEXELS_VIDEO",
        "SC-08": "NATIVE_DIAGRAM",
        "SC-09": "PEXELS_VIDEO",
    }
    assert all(item["fallback_used"] is False for item in scene_executions.values())

    narration_request = json.dumps(fakes["narration"].calls[0], sort_keys=True)
    voice_binding = scope["pending"]["package"]["revised_artifacts"]["voice_policy"]
    assert voice_binding["content_hash"] in narration_request
    assert scope["approval"]["approval_ref"] in narration_request
    narration_audio_hash = hashlib.sha256(
        Path(result["narration"]["audio_path"]).read_bytes()
    ).hexdigest()
    assert fakes["alignment"].calls[0]["audio_sha256"] == narration_audio_hash
    assert result["alignment"]["verification_status"] == "PASS"
    assert result["alignment"]["token_coverage"] == 1.0
    assert result["canonical_timeline"]["timing_authority"] == (
        "CANONICAL_MEDIA_TIMELINE"
    )
    assert result["canonical_timeline"]["estimated_timing_fallback_used"] is False

    candidate = result["review_media_candidate"]
    assert candidate["production_eligible"] is True
    assert candidate["not_publishable"] is True
    assert candidate["human_review_status"] == "PENDING"
    assert candidate["review_round"] == 1
    assert Path(candidate["output_file_ref"]).is_file()
    assert (
        hashlib.sha256(Path(candidate["output_file_ref"]).read_bytes()).hexdigest()
        == (candidate["output_sha256"])
    )
    candidate_version = db_session.get(
        ArtifactVersion, uuid.UUID(candidate["artifact_version_id"])
    )
    assert candidate_version is not None
    candidate_payload = deepcopy(candidate_version.content or {})
    embedded_candidate_hash = candidate_payload.pop("content_hash")
    assert embedded_candidate_hash == _stable_hash(candidate_payload)
    assert candidate_payload["project_id"] == str(scope["command"].project_id)
    assert candidate_payload["package_artifact_version_id"] == str(
        scope["command"].package_artifact_version_id
    )
    assert candidate_payload["approval_id"] == str(scope["command"].approval_id)
    assert candidate_payload["canonical_timeline_hash"] == (
        result["canonical_timeline"].get("timeline_hash")
        or result["canonical_timeline"]["content_hash"]
    )
    frozen_authority = deepcopy(candidate_payload["candidate_authority_bindings"])
    frozen_authority_hash = frozen_authority.pop("content_hash")
    assert frozen_authority_hash == _stable_hash(frozen_authority)
    assert candidate_payload["candidate_authority_bindings_hash"] == (
        frozen_authority_hash
    )
    assert candidate_payload["lineage_derivation_checks"] == {
        "package_version_exact": True,
        "approval_exact": True,
        "profile_snapshot_exact": True,
        "rights_planning_authority_exact": True,
        "synthetic_disclosure_authority_exact": True,
        "provenance_plan_authority_exact": True,
        "actual_provenance_manifest_exact": True,
    }
    provenance_path = Path(candidate_payload["asset_provenance_manifest_ref"])
    assert (
        provenance_path
        == (
            Path(result["workspace"]) / "assets" / "asset-provenance-manifest.json"
        ).resolve()
    )
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance_core = deepcopy(provenance)
    provenance_hash = provenance_core.pop("content_hash")
    assert provenance_hash == _stable_hash(provenance_core)
    assert provenance_hash == candidate_payload["asset_provenance_manifest_hash"]
    assert (
        hashlib.sha256(provenance_path.read_bytes()).hexdigest()
        == (candidate_payload["asset_provenance_manifest_file_sha256"])
    )
    assert [item["scene_id"] for item in provenance["items"]] == list(ALL_SCENES)
    assert provenance["provider_substitution_used"] is False
    assert provenance["automatic_fallback_used"] is False
    assert provenance["rights_complete"] is True
    for item in provenance["items"]:
        source = Path(item["source_path"])
        normalized = Path(item["normalized_path"])
        assert source.is_file() and not source.is_symlink()
        assert normalized.is_file() and not normalized.is_symlink()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["source_sha256"]
        assert (
            hashlib.sha256(normalized.read_bytes()).hexdigest()
            == item["normalized_sha256"]
        )
        assert item["fallback_used"] is False
        if item["route"] == "PEXELS_VIDEO":
            assert item["rights"]["rights_status"] == "CONFIRMED"
            assert item["rights"]["provider_asset_id"]
            assert item["rights"]["creator_ref"]
            assert item["rights"]["source_page_url"]
            assert item["rights"]["license_ref"] == ("https://www.pexels.com/license/")
        else:
            assert item["rights"]["rights_status"] == "NOT_REQUIRED"
    for path_key, hash_key in (
        ("technical_media_qc_ref", "technical_media_qc_hash"),
        ("creative_media_qc_ref", "creative_media_qc_hash"),
    ):
        qc_payload = json.loads(
            Path(candidate_payload[path_key]).read_text(encoding="utf-8")
        )
        supplied_hash = qc_payload.pop("content_hash")
        assert supplied_hash == candidate_payload[hash_key]
        assert supplied_hash == _stable_hash(qc_payload)

    technical_artifacts = list(
        db_session.scalars(
            select(Artifact).where(
                Artifact.video_project_id == scope["command"].project_id,
                Artifact.artifact_type == "mr1_technical_media_qc_receipt",
            )
        ).all()
    )
    assert len(technical_artifacts) == 1
    technical_version = db_session.get(
        ArtifactVersion, technical_artifacts[0].current_version_id
    )
    assert technical_version is not None
    assert technical_version.content["review_round"] == 1
    assert technical_version.content["review_media_candidate"] == {
        "artifact_version_id": candidate["artifact_version_id"],
        "content_hash": candidate["content_hash"],
    }
    assert technical_version.content["output_sha256"] == candidate["output_sha256"]

    drive = result["drive_archive"]
    final_items = [
        item
        for item in drive["items"]
        if item["logical_role"] == "MR1_FINAL_REVIEW_MP4"
    ]
    final_proofs = [
        item
        for item in drive["files"]
        if item["logical_role"] == "MR1_FINAL_REVIEW_MP4"
    ]
    assert len(final_items) == len(final_proofs) == 1
    assert final_items[0]["sha256"] == candidate["output_sha256"]
    assert final_proofs[0]["local_sha256"] == candidate["output_sha256"]
    assert final_proofs[0]["remote_sha256"] == candidate["output_sha256"]
    assert final_proofs[0]["verified"] is True

    review_tasks = _final_human_review_tasks(db_session, scope["command"].project_id)
    assert len(review_tasks) == 1
    review_task = review_tasks[0]
    assert review_task.status == "open"
    assert (
        str(review_task.target_artifact_version_id) == candidate["artifact_version_id"]
    )
    assert review_task.review_reason_codes == [
        "MR1_EXACT_FINAL_MEDIA_FULL_WATCH_REQUIRED",
        "MR1_REVIEW_ROUND_1",
    ]
    assert {
        item.get("review_round")
        for item in review_task.evidence_refs
        if item.get("type")
        in {
            "mr1_review_media_candidate",
            "mr1_drive_archive_receipt",
            "mr1_technical_media_qc_receipt",
        }
    } == {1}
    drive_manifest = json.dumps(fakes["drive"].calls[0]["manifest"], sort_keys=True)
    assert candidate["content_hash"] in drive_manifest
    event_order = result["event_order"]
    assert event_order.index("CANONICAL_TIMELINE_PASS") < event_order.index(
        "PEXELS_SC-04_SEARCH_SUBMITTING"
    )
    assert (
        event_order.index("REVIEW_MEDIA_CANDIDATE_CREATED")
        < event_order.index("DRIVE_ARCHIVE_VERIFIED")
        < event_order.index("HUMAN_FULL_WATCH_PENDING")
    )
    assert "FINAL_MEDIA_REF_CREATED" not in event_order

    assert (
        _artifact_count(db_session, scope["command"].project_id, RUN_ARTIFACT_TYPE) == 1
    )
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, ATTEMPT_LEDGER_ARTIFACT_TYPE
        )
        >= 1
    )
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, REVIEW_CANDIDATE_ARTIFACT_TYPE
        )
        == 1
    )
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, DRIVE_RECEIPT_ARTIFACT_TYPE
        )
        == 1
    )
    assert (
        db_session.scalar(select(func.count()).select_from(FinalMediaRef))
        == final_before
    )


def test_pre_submit_failure_consumes_zero_and_local_resume_never_recalls_providers(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    narration = FakeNarrationGateway(fail_before_submit=True)
    gateways, fakes = _gateways(narration=narration)
    local = FakeLocalContinuation(fail_once_at_render=True)
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )

    pre_submit = service.start(scope["command"], gateways=gateways)

    assert pre_submit["current_state"] == "BLOCKED_PRE_SUBMIT_REPAIRABLE"
    assert pre_submit["MR1_FINAL"] == "REPAIRABLE_PRE_SUBMIT_FAILURE"
    assert pre_submit["MR1_REQUIRED_PROVIDER_EXECUTION"] == "FAIL"
    assert pre_submit["MR1_ASSET_RESOLUTION"] == "FAIL"
    narration_attempt = _attempt(pre_submit, "elevenlabs")
    assert narration_attempt["attempt_count"] == 0
    assert narration_attempt["submit_state"] == "NOT_SUBMITTED"
    assert _mutation_counts(fakes)["narration"] == 0
    assert local.calls == []
    run_id = uuid.UUID(pre_submit["run_id"])

    narration.fail_before_submit = False
    local_failure = service.resume(run_id=run_id, gateways=gateways)

    assert local_failure["run_id"] == str(run_id)
    assert local_failure["current_state"] == "REPAIRABLE_LOCAL_FAILURE"
    assert local_failure["failed_stage"] == "NATIVE_FFMPEG_RENDER"
    assert local_failure["MR1_REQUIRED_PROVIDER_EXECUTION"] == "PASS"
    assert local_failure["MR1_ASSET_RESOLUTION"] == "FAIL"
    provider_counts_after_local_failure = _mutation_counts(fakes)
    assert provider_counts_after_local_failure == {
        "narration": 1,
        "alignment": 1,
        "pexels_search": list(PEXELS_SCENES),
        "pexels_download": list(PEXELS_SCENES),
        "drive": 0,
    }

    completed = service.resume(run_id=run_id, gateways=gateways)

    assert completed["current_state"] == "AWAITING_HUMAN_FULL_WATCH"
    assert completed["MR1_FINAL"] == "WAITING_HUMAN_REVIEW"
    assert completed["MR1_REQUIRED_PROVIDER_EXECUTION"] == "PASS"
    assert completed["MR1_ASSET_RESOLUTION"] == "PASS"
    assert _mutation_counts(fakes) == {
        **provider_counts_after_local_failure,
        "drive": 1,
    }
    assert len(local.calls) == 2
    assert local.calls[1]["resume_from"] == "NATIVE_RENDER_PLAN_READY"

    duplicate_start = service.start(scope["command"], gateways=gateways)
    duplicate_resume = service.resume(run_id=run_id, gateways=gateways)
    assert duplicate_start["run_id"] == duplicate_resume["run_id"] == str(run_id)
    assert (
        duplicate_start["run_artifact_version_id"]
        == completed["run_artifact_version_id"]
    )
    assert (
        duplicate_resume["review_media_candidate"]["content_hash"]
        == completed["review_media_candidate"]["content_hash"]
    )
    assert _mutation_counts(fakes) == {
        **provider_counts_after_local_failure,
        "drive": 1,
    }
    assert len(local.calls) == 2
    assert (
        _artifact_count(db_session, scope["command"].project_id, RUN_ARTIFACT_TYPE) == 1
    )


def test_resumable_drive_failure_reuses_exact_render_and_candidate(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    drive = FakeDriveGateway(fail_after_first_mutation_once=True)
    gateways, fakes = _gateways(drive=drive)
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )

    interrupted = service.start(scope["command"], gateways=gateways)

    assert interrupted["current_state"] == "REPAIRABLE_DRIVE_FAILURE"
    assert len(local.calls) == 1
    assert len(drive.calls) == 1
    interrupted_state, interrupted_ledger = _persisted_attempt_ledger(
        db_session,
        run_artifact_version_id=interrupted["run_artifact_version_id"],
        operation_key="google_drive:archive",
    )
    internal_counts_after_first_boundary = deepcopy(
        interrupted_state["provider_call_counts"]
    )
    assert interrupted_state["attempts"]["google_drive:archive"][
        "state"
    ] == "RESUMABLE_FAILURE"
    assert interrupted_state["attempts"]["google_drive:archive"][
        "submit_state"
    ] == "RESUMABLE_FAILURE"
    assert interrupted_state["attempts"]["google_drive:archive"][
        "attempt_count"
    ] == 1
    assert interrupted_state["attempts"]["google_drive:archive"][
        "network_submit_started"
    ] is True
    assert interrupted_state["attempts"]["google_drive:archive"][
        "pre_submit_failures"
    ] == 0
    assert interrupted_ledger["state"] == "RESUMABLE_FAILURE"
    assert interrupted_ledger["submit_state"] == "RESUMABLE_FAILURE"
    assert interrupted_ledger["attempt_count"] == 1
    assert interrupted_ledger["network_submit_started"] is True
    assert internal_counts_after_first_boundary["drive"] == 1
    assert internal_counts_after_first_boundary["logical_total"] == 6
    assert interrupted["MR1_PROVIDER_CALL_COUNT"] == 6
    assert interrupted["provider_call_counts"]["google_drive_archive_flows"] == 1
    candidate = deepcopy(interrupted["review_media_candidate"])
    output_sha256 = candidate["output_sha256"]
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, REVIEW_CANDIDATE_ARTIFACT_TYPE
        )
        == 1
    )

    completed = service.resume(
        run_id=uuid.UUID(interrupted["run_id"]), gateways=gateways
    )

    assert completed["current_state"] == "AWAITING_HUMAN_FULL_WATCH"
    assert len(local.calls) == 1
    assert len(drive.calls) == 2
    completed_state, completed_ledger = _persisted_attempt_ledger(
        db_session,
        run_artifact_version_id=completed["run_artifact_version_id"],
        operation_key="google_drive:archive",
    )
    assert completed_state["attempts"]["google_drive:archive"][
        "state"
    ] == "SUCCEEDED"
    assert completed_state["attempts"]["google_drive:archive"][
        "submit_state"
    ] == "SUCCEEDED"
    assert completed_state["attempts"]["google_drive:archive"][
        "attempt_count"
    ] == 1
    assert completed_state["attempts"]["google_drive:archive"][
        "network_submit_started"
    ] is True
    assert completed_ledger["state"] == "SUCCEEDED"
    assert completed_ledger["submit_state"] == "SUCCEEDED"
    assert completed_ledger["attempt_count"] == 1
    assert completed_ledger["network_submit_started"] is True
    assert (
        completed_state["provider_call_counts"]
        == internal_counts_after_first_boundary
    )
    assert completed["MR1_PROVIDER_CALL_COUNT"] == 6
    assert completed["provider_call_counts"]["google_drive_archive_flows"] == 1
    assert (
        completed["review_media_candidate"]["artifact_version_id"]
        == candidate["artifact_version_id"]
    )
    assert (
        completed["review_media_candidate"]["content_hash"] == candidate["content_hash"]
    )
    assert completed["review_media_candidate"]["output_sha256"] == output_sha256
    assert drive.calls[0]["manifest"] == drive.calls[1]["manifest"]
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, REVIEW_CANDIDATE_ARTIFACT_TYPE
        )
        == 1
    )
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, DRIVE_RECEIPT_ARTIFACT_TYPE
        )
        == 1
    )


def test_drive_pre_submit_failure_stays_unsubmitted_then_counts_once(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    drive = FakeDriveGateway(fail_before_first_mutation_once=True)
    gateways, _fakes = _gateways(drive=drive)
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )

    interrupted = service.start(scope["command"], gateways=gateways)

    assert interrupted["current_state"] == "BLOCKED_PRE_SUBMIT_REPAIRABLE"
    assert len(local.calls) == 1
    assert drive.upload_invocation_count == 1
    assert drive.calls == []
    interrupted_state, interrupted_ledger = _persisted_attempt_ledger(
        db_session,
        run_artifact_version_id=interrupted["run_artifact_version_id"],
        operation_key="google_drive:archive",
    )
    assert interrupted_state["attempts"]["google_drive:archive"][
        "state"
    ] == "PLANNED"
    assert interrupted_state["attempts"]["google_drive:archive"][
        "submit_state"
    ] == "NOT_SUBMITTED"
    assert interrupted_state["attempts"]["google_drive:archive"][
        "attempt_count"
    ] == 0
    assert interrupted_state["attempts"]["google_drive:archive"][
        "network_submit_started"
    ] is False
    assert interrupted_state["attempts"]["google_drive:archive"][
        "pre_submit_failures"
    ] == 1
    assert interrupted_ledger["state"] == "PLANNED"
    assert interrupted_ledger["submit_state"] == "NOT_SUBMITTED"
    assert interrupted_ledger["attempt_count"] == 0
    assert interrupted_ledger["network_submit_started"] is False
    assert interrupted_state["provider_call_counts"]["drive"] == 0
    assert interrupted_state["provider_call_counts"]["logical_total"] == 5
    assert interrupted["MR1_PROVIDER_CALL_COUNT"] == 5
    assert interrupted["provider_call_counts"]["google_drive_archive_flows"] == 0

    completed = service.resume(
        run_id=uuid.UUID(interrupted["run_id"]),
        gateways=gateways,
    )

    assert completed["current_state"] == "AWAITING_HUMAN_FULL_WATCH"
    assert len(local.calls) == 1
    assert drive.upload_invocation_count == 2
    assert len(drive.calls) == 1
    completed_state, completed_ledger = _persisted_attempt_ledger(
        db_session,
        run_artifact_version_id=completed["run_artifact_version_id"],
        operation_key="google_drive:archive",
    )
    assert completed_state["attempts"]["google_drive:archive"][
        "state"
    ] == "SUCCEEDED"
    assert completed_state["attempts"]["google_drive:archive"][
        "submit_state"
    ] == "SUCCEEDED"
    assert completed_state["attempts"]["google_drive:archive"][
        "attempt_count"
    ] == 1
    assert completed_state["attempts"]["google_drive:archive"][
        "network_submit_started"
    ] is True
    assert completed_state["attempts"]["google_drive:archive"][
        "pre_submit_failures"
    ] == 1
    assert completed_ledger["state"] == "SUCCEEDED"
    assert completed_ledger["submit_state"] == "SUCCEEDED"
    assert completed_ledger["attempt_count"] == 1
    assert completed_ledger["network_submit_started"] is True
    assert completed_state["provider_call_counts"]["drive"] == 1
    assert completed_state["provider_call_counts"]["logical_total"] == 6
    assert completed["MR1_PROVIDER_CALL_COUNT"] == 6
    assert completed["provider_call_counts"]["google_drive_archive_flows"] == 1


def test_human_reject_local_repair_reuses_provider_outputs_and_returns_round_two(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    gateways, fakes = _gateways()
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )
    round_one = service.start(scope["command"], gateways=gateways)
    round_one_candidate = deepcopy(round_one["review_media_candidate"])
    provider_counts = deepcopy(round_one["provider_call_counts"])
    round_one_tasks = _final_human_review_tasks(db_session, scope["command"].project_id)
    assert len(round_one_tasks) == 1
    round_one_task = round_one_tasks[0]
    assert round_one_task.status == "open"
    assert (
        str(round_one_task.target_artifact_version_id)
        == (round_one_candidate["artifact_version_id"])
    )
    assert round_one_task.review_reason_codes == [
        "MR1_EXACT_FINAL_MEDIA_FULL_WATCH_REQUIRED",
        "MR1_REVIEW_ROUND_1",
    ]

    rejected = service.closeout(
        _closeout_command(
            db_session,
            scope,
            round_one,
            decision="REJECT",
            operator_decision_text=(
                "REJECT: caption readability requires deterministic repair"
            ),
        )
    )

    assert rejected["current_state"] == "REPAIR_REQUIRED_AFTER_HUMAN_REJECTION"
    assert rejected["MR1_HUMAN_REVIEW"] == "REJECT"
    assert rejected["final_media_ref_id"] is None
    assert rejected["provider_call_counts"] == provider_counts
    assert Path(rejected["workspace"], "human_repair_directive.json").is_file()
    db_session.refresh(round_one_task)
    assert round_one_task.status == "completed"

    round_two = service.resume(run_id=uuid.UUID(round_one["run_id"]), gateways=gateways)

    assert round_two["current_state"] == "AWAITING_HUMAN_FULL_WATCH"
    assert round_two["MR1_FINAL"] == "WAITING_HUMAN_REVIEW"
    assert round_two["provider_call_counts"] == provider_counts
    assert (
        round_two["review_media_candidate"]["artifact_version_id"]
        != (round_one_candidate["artifact_version_id"])
    )
    assert (
        round_two["review_media_candidate"]["output_sha256"]
        != (round_one_candidate["output_sha256"])
    )
    round_two_tasks = _final_human_review_tasks(db_session, scope["command"].project_id)
    assert len(round_two_tasks) == 2
    round_two_task = next(
        task
        for task in round_two_tasks
        if str(task.target_artifact_version_id)
        == round_two["review_media_candidate"]["artifact_version_id"]
    )
    assert round_two_task.id != round_one_task.id
    assert round_two_task.status == "open"
    assert round_two_task.review_reason_codes == [
        "MR1_EXACT_FINAL_MEDIA_FULL_WATCH_REQUIRED",
        "MR1_REVIEW_ROUND_2",
    ]
    assert round_two_task.context_pack_ref.endswith("/review-round-2")
    assert {
        item.get("review_round")
        for item in round_two_task.evidence_refs
        if item.get("type")
        in {
            "mr1_review_media_candidate",
            "mr1_drive_archive_receipt",
            "mr1_technical_media_qc_receipt",
        }
    } == {2}
    assert len(local.calls) == 2
    assert _mutation_counts(fakes) == {
        "narration": 1,
        "alignment": 1,
        "pexels_search": list(PEXELS_SCENES),
        "pexels_download": list(PEXELS_SCENES),
        "drive": 2,
    }
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, REVIEW_CANDIDATE_ARTIFACT_TYPE
        )
        == 1
    )
    candidate_artifact = db_session.get(
        Artifact,
        db_session.get(
            ArtifactVersion,
            uuid.UUID(round_two["review_media_candidate"]["artifact_version_id"]),
        ).artifact_id,
    )
    assert candidate_artifact is not None
    assert (
        int(
            db_session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.artifact_id == candidate_artifact.id)
            )
            or 0
        )
        == 2
    )

    with pytest.raises(
        ValidationFailureError,
        match="EXACT_ARTIFACT_VERSION_REQUIRED|CURRENT_REVIEW_ROUND",
    ):
        service.closeout(
            _closeout_command(
                db_session,
                scope,
                round_one,
                decision="PASS",
                operator_decision_text="PASS",
            ),
            drive_gateway=fakes["drive"],
        )

    passed = service.closeout(
        _closeout_command(
            db_session,
            scope,
            round_two,
            decision="PASS",
            operator_decision_text="PASS",
        ),
        drive_gateway=fakes["drive"],
    )
    assert passed["MR1_FINAL"] == "PASS"
    assert passed["MR1_HUMAN_REVIEW"] == "PASS"
    assert passed["final_media_ref_id"] is not None
    assert passed["MR1_PROVIDER_CALL_COUNT"] == 7
    assert passed["provider_call_counts"]["google_drive_archive_flows"] == 2
    assert len(fakes["drive"].finalization_calls) == 1
    finalization_attempt = _attempt(passed, "google_drive", None)
    assert finalization_attempt["state"] == "SUCCEEDED"
    assert finalization_attempt["submit_state"] == "SUCCEEDED"
    assert finalization_attempt["attempt_count"] == 1
    assert finalization_attempt["network_submit_started"] is True
    assert (
        finalization_attempt["drive_phase_authority"] == (DRIVE_IDEMPOTENCY_PHASES[1])
    )
    db_session.refresh(round_two_task)
    assert round_two_task.status == "completed"
    assert round_one_task.status == "completed"
    assert (
        int(
            db_session.scalar(
                select(func.count())
                .select_from(FinalMediaRef)
                .where(FinalMediaRef.video_project_id == scope["command"].project_id)
            )
            or 0
        )
        == 1
    )


@pytest.mark.parametrize(
    "reason",
    [
        "REJECT: new script required",
        "REJECT: new narration required",
        "REJECT: new provider generation required",
        "REJECT: visual route must change",
        "REJECT: metadata must change",
    ],
)
def test_human_reject_source_change_blocks_for_new_package_without_rerender(
    db_session, tmp_path: Path, reason: str
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    gateways, fakes = _gateways()
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )
    pending = service.start(scope["command"], gateways=gateways)
    mutations_before = _mutation_counts(fakes)

    blocked = service.closeout(
        _closeout_command(
            db_session,
            scope,
            pending,
            decision="REJECT",
            operator_decision_text=reason,
        )
    )

    assert blocked["current_state"] == (
        "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL"
    )
    assert blocked["MR1_FINAL"] == (
        "BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL"
    )
    assert blocked["final_media_ref_id"] is None
    assert _mutation_counts(fakes) == mutations_before
    assert len(local.calls) == 1


def test_after_submit_failure_is_consumed_and_blocks_retry_or_provider_fallback(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    pexels = FakePexelsGateway(fail_after_search_for="SC-07")
    gateways, fakes = _gateways(pexels=pexels)
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=local,
    )

    blocked = service.start(scope["command"], gateways=gateways)

    assert blocked["current_state"] == "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
    assert blocked["MR1_FINAL"] == "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
    assert blocked["MR1_REQUIRED_PROVIDER_EXECUTION"] == "FAIL"
    assert blocked["MR1_ASSET_RESOLUTION"] == "FAIL"
    consumed = _attempt(blocked, "pexels_api", "SC-07")
    assert consumed["attempt_cap"] == consumed["attempt_count"] == 1
    assert consumed["submit_state"] in {"FAILED_CONSUMED", "UNKNOWN"}
    assert consumed["automatic_retry_allowed"] is False
    assert consumed["provider_substitution_allowed"] is False
    assert _mutation_counts(fakes) == {
        "narration": 1,
        "alignment": 1,
        "pexels_search": ["SC-04", "SC-07"],
        "pexels_download": ["SC-04"],
        "drive": 0,
    }
    assert local.calls == []
    assert blocked["provider_call_counts"]["google_gemini_image"] == 0
    assert blocked["provider_call_counts"]["google_veo"] == 0
    assert blocked["provider_call_counts"]["youtube"] == 0
    assert all(
        item.get("fallback_used") is not True
        for item in blocked.get("scene_executions", [])
    )

    again = service.resume(
        run_id=uuid.UUID(blocked["run_id"]),
        gateways=gateways,
    )

    assert again["current_state"] == "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
    assert again["run_id"] == blocked["run_id"]
    assert _mutation_counts(fakes) == {
        "narration": 1,
        "alignment": 1,
        "pexels_search": ["SC-04", "SC-07"],
        "pexels_download": ["SC-04"],
        "drive": 0,
    }
    assert _attempt(again, "pexels_api", "SC-07")["attempt_count"] == 1
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, REVIEW_CANDIDATE_ARTIFACT_TYPE
        )
        == 0
    )
    assert (
        _artifact_count(
            db_session, scope["command"].project_id, DRIVE_RECEIPT_ARTIFACT_TYPE
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(FinalMediaRef)
            .where(FinalMediaRef.video_project_id == scope["command"].project_id)
        )
        == 0
    )


def test_reopen_package_bound_artifact_version_deduplicates_exact_refs_across_sections(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    authority = service._resolve_exact_authority(scope["command"])
    ref_fields = (
        "artifact_id",
        "artifact_version_id",
        "artifact_version_ref",
        "version_number",
        "content_hash",
    )

    for artifact_type in (
        "visual_source_decision_set",
        "script",
        "scene_visual_intent",
    ):
        candidate_authority = deepcopy(authority)
        bound_refs = [
            (authority["package"].get(section_name) or {}).get(artifact_type)
            for section_name in (
                "effective_artifacts",
                "revised_artifacts",
                "reused_artifacts",
            )
        ]
        source_ref = next(ref for ref in bound_refs if isinstance(ref, dict))
        exact_ref = {key: deepcopy(source_ref[key]) for key in ref_fields}
        for section_name in (
            "effective_artifacts",
            "revised_artifacts",
            "reused_artifacts",
        ):
            candidate_authority["package"].setdefault(section_name, {})[
                artifact_type
            ] = deepcopy(exact_ref)

        reopened = service._reopen_package_bound_artifact_version(
            authority=candidate_authority,
            artifact_type=artifact_type,
        )

        assert str(reopened.id) == exact_ref["artifact_version_id"]
        assert reopened.content_hash == exact_ref["content_hash"]


def test_continuation_review_rejects_conflicting_package_refs_before_provider_call(
    db_session, tmp_path: Path, monkeypatch
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    first_gateways, first_fakes = _gateways(
        pexels=SemanticFailPexelsGateway()
    )
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    blocked = service.start(scope["command"], gateways=first_gateways)
    authority = service._resolve_exact_authority(scope["command"])
    ref_fields = (
        "artifact_id",
        "artifact_version_id",
        "artifact_version_ref",
        "version_number",
        "content_hash",
    )
    visual_ref = {
        key: deepcopy(authority["resolved"]["visual_source_decision_set"][key])
        for key in ref_fields
    }
    conflicting_ref = deepcopy(visual_ref)
    conflicting_ref["content_hash"] = "0" * 64
    authority["package"].setdefault("effective_artifacts", {})[
        "visual_source_decision_set"
    ] = deepcopy(visual_ref)
    authority["package"].setdefault("revised_artifacts", {})[
        "visual_source_decision_set"
    ] = deepcopy(visual_ref)
    authority["package"].setdefault("reused_artifacts", {})[
        "visual_source_decision_set"
    ] = conflicting_ref
    monkeypatch.setattr(
        service,
        "_resolve_exact_authority",
        lambda _command: deepcopy(authority),
    )
    mutations_before = _mutation_counts(first_fakes)
    review_tasks_before = db_session.scalar(
        select(func.count()).select_from(ReviewTask)
    )

    with pytest.raises(
        ValidationFailureError,
        match="^MR1_STOCK_SEARCH_VISUAL_SOURCE_DECISION_SET_REF_INVALID$",
    ):
        service.prepare_provider_attempt_continuation_review(
            MR1ProviderAttemptContinuationReviewCommand(
                run_id=uuid.UUID(blocked["run_id"]),
                operation_key="pexels:SC-07",
                approved_stock_search_intent=(
                    "People discussing office paperwork together."
                ),
                approved_pending_scene_stock_search_intents={},
            )
        )

    assert _mutation_counts(first_fakes) == mutations_before
    assert (
        db_session.scalar(select(func.count()).select_from(ReviewTask))
        == review_tasks_before
    )


def test_exact_operator_continuation_binds_new_sc07_query_and_preserves_consumed_attempt(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    first_pexels = SemanticFailPexelsGateway()
    first_gateways, first_fakes = _gateways(pexels=first_pexels)
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    blocked = service.start(scope["command"], gateways=first_gateways)
    assert blocked["current_state"] == "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
    consumed = _attempt(blocked, "pexels_api", "SC-07")
    assert consumed["state"] == "CONSUMED_FAILED"
    assert consumed["attempt_count"] == consumed["attempt_cap"] == 1
    assert consumed["download_submit_count"] == 0

    with pytest.raises(
        ValidationFailureError,
        match="^MR1_PEXELS_QUERY_INTENT_COVERAGE_INADEQUATE$",
    ):
        service.prepare_provider_attempt_continuation_review(
            MR1ProviderAttemptContinuationReviewCommand(
                run_id=uuid.UUID(blocked["run_id"]),
                operation_key="pexels:SC-07",
                approved_stock_search_intent=(
                    "Office coworkers review paperwork at a small-business "
                    "conference table while one person points to a missing field."
                ),
                approved_pending_scene_stock_search_intents={},
            )
        )
    _artifact, after_rejected_version = service._require_run(
        uuid.UUID(blocked["run_id"]),
        lock=False,
    )
    after_rejected = after_rejected_version.content or {}
    assert after_rejected["current_state"] == (
        "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
    )
    assert after_rejected.get("provider_attempt_continuation_approvals") in (
        None,
        [],
    )
    assert after_rejected["attempts"]["pexels:SC-07"][
        "artifact_version_id"
    ] == consumed["artifact_version_id"]

    approved_intent = "People discussing office paperwork together."
    approved_sc09_intent = (
        "People working together in an office, planning."
    )
    review_command = MR1ProviderAttemptContinuationReviewCommand(
        run_id=uuid.UUID(blocked["run_id"]),
        operation_key="pexels:SC-07",
        approved_stock_search_intent=approved_intent,
        approved_pending_scene_stock_search_intents={
            "SC-09": approved_sc09_intent
        },
    )
    review = service.prepare_provider_attempt_continuation_review(
        review_command
    )
    assert review["approval_persisted"] is False
    assert review["provider_calls_made"] == 0
    assert review["review_manifest_artifact_version_id"]
    assert review["review_manifest_content_hash"]
    assert review["operator_review_task_id"]
    assert review["required_decided_by_user_id"]
    assert review["required_operator_decision_text"].endswith(
        review["review_manifest_content_hash"]
    )
    manifest = review["review_manifest"]
    package_semantic_intent = manifest["package_semantic_intent"]
    assert package_semantic_intent != approved_intent
    assert manifest["approved_query_authority"][
        "package_semantic_intent"
    ] == package_semantic_intent
    assert manifest["approved_query_authority"][
        "stock_search_intent"
    ] == approved_intent
    assert manifest["query_material_diff"]["materially_different"] is True
    assert manifest["query_material_diff"]["base_primary_query"] != (
        manifest["query_material_diff"]["approved_primary_query"]
    )
    assert manifest["base_query_evidence"][
        "detailed_candidate_ranking_evidence_state"
    ] == "UNAVAILABLE_NOT_DURABLY_CAPTURED"
    assert manifest["base_query_evidence"][
        "detailed_candidate_ranking_evidence_fabricated"
    ] is False
    derivation = manifest["stock_search_intent_derivation"]
    assert derivation["package_semantic_intent_unchanged"] is True
    assert derivation["package_semantic_intent"] == package_semantic_intent
    assert derivation["approved_stock_search_intent"] == approved_intent
    assert derivation["scope"] == (
        "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
    )
    for exact_ref in derivation["refs"].values():
        assert exact_ref
    for exact_ref in (
        derivation["refs"]["visual_source_decision"],
        derivation["refs"]["scene_visual_intent"],
        derivation["refs"]["script"],
    ):
        assert exact_ref["artifact_version_id"]
        assert len(exact_ref["content_hash"]) == 64
    review_task = db_session.get(
        ReviewTask,
        uuid.UUID(review["operator_review_task_id"]),
    )
    assert review_task is not None
    assert review_task.status == "open"
    assert review_task.target_artifact_version_id == uuid.UUID(
        review["review_manifest_artifact_version_id"]
    )
    assert review_task.assigned_to_user_id == uuid.UUID(
        review["required_decided_by_user_id"]
    )
    sc09_unsubmitted = review["review_manifest"][
        "pending_query_amendments"
    ]["SC-09"]["unsubmitted_attempt_snapshot"]
    assert sc09_unsubmitted["state"] == "PLANNED"
    assert sc09_unsubmitted["submit_state"] == "NOT_SUBMITTED"
    assert sc09_unsubmitted["attempt_count"] == 0
    assert sc09_unsubmitted["search_submit_count"] == 0
    assert sc09_unsubmitted["download_submit_count"] == 0
    assert sc09_unsubmitted["network_submit_started"] is False
    assert sc09_unsubmitted["artifact_version_id"]
    assert sc09_unsubmitted["content_hash"]
    receipt = service.approve_provider_attempt_continuation(
        MR1ProviderAttemptContinuationCommand(
            **review_command.model_dump(mode="python"),
            operator_review_manifest_artifact_version_id=uuid.UUID(
                review["review_manifest_artifact_version_id"]
            ),
            operator_review_manifest_content_hash=review[
                "review_manifest_content_hash"
            ],
            operator_review_task_id=uuid.UUID(
                review["operator_review_task_id"]
            ),
            decided_by_user_id=uuid.UUID(
                review["required_decided_by_user_id"]
            ),
            operator_decision_text=review[
                "required_operator_decision_text"
            ],
        )
    )
    assert receipt["scene_id"] == "SC-07"
    assert receipt["package_semantic_intent"] == package_semantic_intent
    assert receipt["approved_stock_search_intent"] == approved_intent
    assert receipt["additional_attempts"] == 1
    assert receipt["maximum_total_attempts"] == 2
    assert receipt["operator_review_manifest_artifact_version_id"] == (
        review["review_manifest_artifact_version_id"]
    )
    assert receipt["operator_review_manifest_content_hash"] == (
        review["review_manifest_content_hash"]
    )
    assert receipt["operator_review_task_id"] == (
        review["operator_review_task_id"]
    )
    assert receipt["operator_decision_text"] == (
        review["required_operator_decision_text"]
    )
    assert receipt["decided_by_user_id"] == (
        review["required_decided_by_user_id"]
    )
    assert receipt["approved_query_authority"]["primary_query"] == (
        "people discussing office paperwork workplace b roll"
    )
    assert receipt["query_intent_coverage_evidence"][
        "query_intent_coverage"
    ] == 0.8
    assert receipt["query_intent_coverage_evidence"][
        "required_matched_intent_token_count"
    ] == 4
    assert receipt["semantic_fit_threshold"] == 0.78
    assert receipt["pending_query_amendments"]["SC-09"][
        "approved_stock_search_intent"
    ] == approved_sc09_intent
    sc09_package_semantic_intent = receipt[
        "pending_query_amendments"
    ]["SC-09"]["package_semantic_intent"]
    assert sc09_package_semantic_intent != approved_sc09_intent
    assert receipt["pending_query_amendments"]["SC-09"][
        "approved_query_authority"
    ]["primary_query"] == (
        "people working together office workplace b roll"
    )
    assert receipt["pending_query_amendments"]["SC-09"][
        "query_intent_coverage_evidence"
    ]["query_intent_coverage"] == 0.8
    assert receipt["pending_query_amendments"]["SC-09"][
        "query_intent_coverage_evidence"
    ]["required_matched_intent_token_count"] == 4
    assert review_task.status == "completed"
    continuation_decision = db_session.get(
        ApprovalDecision,
        uuid.UUID(receipt["approval_decision_id"]),
    )
    assert continuation_decision is not None
    assert continuation_decision.decided_by_user_id == uuid.UUID(
        review["required_decided_by_user_id"]
    )
    assert continuation_decision.target_artifact_version_id == uuid.UUID(
        review["review_manifest_artifact_version_id"]
    )
    assert continuation_decision.decision_basis["operator_review_task_id"] == (
        review["operator_review_task_id"]
    )
    assert any(
        evidence.get("approval_decision_ids")
        == [receipt["approval_decision_id"]]
        for evidence in review_task.evidence_refs
    )

    continuation_pexels = FakePexelsGateway()
    continuation_gateways, continuation_fakes = _gateways(
        pexels=continuation_pexels
    )
    resumed = service.resume(
        run_id=uuid.UUID(blocked["run_id"]),
        gateways=continuation_gateways,
    )

    assert resumed["current_state"] == "AWAITING_HUMAN_FULL_WATCH", {
        "current_state": resumed["current_state"],
        "blocker": resumed.get("blocker"),
        "local_result": resumed.get("local_result"),
        "event_order": resumed.get("event_order", [])[-8:],
        "attempts": [
            {
                "operation_key": item.get("operation_key"),
                "state": item.get("state"),
                "failure": item.get("failure"),
            }
            for item in resumed["attempts"]
            if item["provider"] == "pexels_api"
        ],
    }
    assert [item["scene_id"] for item in first_fakes["pexels"].search_calls] == [
        "SC-04",
        "SC-07",
    ]
    assert [item["scene_id"] for item in continuation_fakes["pexels"].search_calls] == [
        "SC-07",
        "SC-09",
    ]
    continuation_request = continuation_fakes["pexels"].search_calls[0]
    assert continuation_request["semantic_intent"] == (
        package_semantic_intent
    )
    assert continuation_request["stock_search_intent"] == approved_intent
    assert continuation_request["stock_search_intent_scope"] == (
        "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
    )
    assert continuation_request["approved_query_authority"] == receipt[
        "approved_query_authority"
    ]
    sc09_request = continuation_fakes["pexels"].search_calls[1]
    assert sc09_request["semantic_intent"] == (
        sc09_package_semantic_intent
    )
    assert sc09_request["stock_search_intent"] == approved_sc09_intent
    assert sc09_request["stock_search_intent_scope"] == (
        "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
    )
    assert sc09_request["approved_query_authority"] == receipt[
        "pending_query_amendments"
    ]["SC-09"]["approved_query_authority"]
    sc07_attempts = [
        item
        for item in resumed["attempts"]
        if item["provider"] == "pexels_api" and item.get("scene_id") == "SC-07"
    ]
    assert len(sc07_attempts) == 2
    assert sorted(item["attempt_count"] for item in sc07_attempts) == [1, 1]
    assert sorted(item["state"] for item in sc07_attempts) == [
        "CONSUMED_FAILED",
        "SUCCEEDED",
    ]
    assert resumed["provider_call_counts"]["pexels_scene_flows"] == 4
    assert resumed["provider_call_counts"]["youtube"] == 0


def test_consumed_pexels_safe_failure_evidence_is_allowlisted_and_review_bound(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    blocked = service.start(
        scope["command"],
        gateways=_gateways(
            pexels=SafeEvidenceSemanticFailPexelsGateway()
        )[0],
    )
    consumed = _attempt(blocked, "pexels_api", "SC-07")
    safe = consumed["safe_failure_evidence"]
    assert safe["schema_version"] == (
        "mr1.pexels-safe-failure-evidence-ref.v1"
    )
    assert safe["provider_evidence_schema_version"] == (
        "vcos.pexels-search-ranking-failure.v2"
    )
    assert safe["safe_evidence_kind"] == (
        "PEXELS_SEARCH_RANKING_FAILURE"
    )
    assert safe["guarded_key"] == "pexels_search_ranking_failure"
    assert safe["reason_code"] == "PEXELS_SEMANTIC_FIT_INADEQUATE"
    assert safe["provider_result_count"] == 3
    assert safe["semantic_fit_gate"]["threshold"] == 0.78
    assert safe["raw_provider_payload_persisted"] is False
    assert safe["raw_media_urls_persisted"] is False
    assert safe["secret_values_exposed"] is False
    serialized_safe = json.dumps(safe, sort_keys=True)
    assert "private raw query" not in serialized_safe
    assert "private-candidate" not in serialized_safe
    assert "request_id" not in serialized_safe
    assert "http://" not in serialized_safe
    assert "https://" not in serialized_safe
    assert consumed["attempt_outcomes"][-1][
        "safe_failure_evidence_ref"
    ]["content_hash"] == safe["content_hash"]

    preview = service.prepare_provider_attempt_continuation_review(
        MR1ProviderAttemptContinuationReviewCommand(
            run_id=uuid.UUID(blocked["run_id"]),
            operation_key="pexels:SC-07",
            approved_stock_search_intent=(
                "People discussing office paperwork together."
            ),
            approved_pending_scene_stock_search_intents={},
        )
    )
    base_evidence = preview["review_manifest"]["base_query_evidence"]
    assert base_evidence[
        "detailed_candidate_ranking_evidence_state"
    ] == "AVAILABLE_DURABLY_CAPTURED"
    assert base_evidence["safe_failure_evidence"] == safe
    assert preview["review_manifest"]["prior_consumed_attempt"][
        "safe_failure_evidence"
    ] == safe
    assert preview["provider_calls_made"] == 0
    assert preview["approval_persisted"] is False

    workspace = Path(blocked["workspace"])
    evidence_path = workspace / safe["evidence_ref"].removeprefix(
        "workspace-relative://"
    )
    original_payload = json.loads(evidence_path.read_text(encoding="utf-8"))

    def rejected_mutation(
        name: str,
        mutate: Callable[[dict[str, Any]], None],
    ) -> None:
        payload = deepcopy(original_payload)
        mutate(payload)
        payload_without_hash = deepcopy(payload)
        payload_without_hash.pop("content_hash", None)
        payload["content_hash"] = _stable_hash(payload_without_hash)
        mutated_path = workspace / f"{name}.json"
        mutated_path.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )
        error = RuntimeError("PEXELS_SEMANTIC_FIT_INADEQUATE")
        error.safe_evidence_kind = "PEXELS_SEARCH_RANKING_FAILURE"
        error.safe_evidence = {
            **payload,
            "evidence_path": str(mutated_path),
            "evidence_persisted": True,
        }
        assert (
            service._sanitized_pexels_failure_evidence(
                exc=error,
                workspace=workspace,
            )
            is None
        )

    rejected_mutation(
        "unexpected-sanitization-key",
        lambda payload: payload["sanitization"].update(
            {"leaked_token": "sk_live_nested-secret"}
        ),
    )
    rejected_mutation(
        "nested-secret-string",
        lambda payload: payload["retrieval_evidence"].update(
            {"note": "sk_live_nested-secret"}
        ),
    )
    rejected_mutation(
        "sensitive-collection",
        lambda payload: payload["retrieval_evidence"].update(
            {"api_key_bundle": {"nested": "must-fail-without-type-error"}}
        ),
    )


def test_runtime_rejects_replaced_safe_failure_file_without_provider_calls(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    blocked = service.start(
        scope["command"],
        gateways=_gateways(
            pexels=SafeEvidenceSemanticFailPexelsGateway()
        )[0],
    )
    review_command = MR1ProviderAttemptContinuationReviewCommand(
        run_id=uuid.UUID(blocked["run_id"]),
        operation_key="pexels:SC-07",
        approved_stock_search_intent=(
            "People discussing office paperwork together."
        ),
        approved_pending_scene_stock_search_intents={},
    )
    review = service.prepare_provider_attempt_continuation_review(
        review_command
    )
    service.approve_provider_attempt_continuation(
        MR1ProviderAttemptContinuationCommand(
            **review_command.model_dump(mode="python"),
            operator_review_manifest_artifact_version_id=uuid.UUID(
                review["review_manifest_artifact_version_id"]
            ),
            operator_review_manifest_content_hash=review[
                "review_manifest_content_hash"
            ],
            operator_review_task_id=uuid.UUID(
                review["operator_review_task_id"]
            ),
            decided_by_user_id=uuid.UUID(
                review["required_decided_by_user_id"]
            ),
            operator_decision_text=review[
                "required_operator_decision_text"
            ],
        )
    )

    safe = _attempt(blocked, "pexels_api", "SC-07")[
        "safe_failure_evidence"
    ]
    workspace = Path(blocked["workspace"])
    evidence_path = workspace / safe["evidence_ref"].removeprefix(
        "workspace-relative://"
    )
    replacement = json.loads(evidence_path.read_text(encoding="utf-8"))
    replacement["retrieval_evidence"]["note"] = (
        "sk_live_replaced-after-exact-operator-approval"
    )
    replacement_without_hash = deepcopy(replacement)
    replacement_without_hash.pop("content_hash", None)
    replacement["content_hash"] = _stable_hash(
        replacement_without_hash
    )
    evidence_path.write_text(
        json.dumps(replacement, sort_keys=True),
        encoding="utf-8",
    )

    continuation_gateways, continuation_fakes = _gateways()
    result = service.resume(
        run_id=uuid.UUID(blocked["run_id"]),
        gateways=continuation_gateways,
    )

    assert result["current_state"] == "BLOCKED_PRE_SUBMIT_REPAIRABLE"
    assert _mutation_counts(continuation_fakes) == {
        "narration": 0,
        "alignment": 0,
        "pexels_search": [],
        "pexels_download": [],
        "drive": 0,
    }
    supplemental = [
        item
        for item in result["attempts"]
        if item["operation_key"] == "pexels:SC-07:supplement:02"
    ]
    assert len(supplemental) == 1
    assert supplemental[0]["attempt_count"] == 0
    assert supplemental[0]["network_submit_started"] is False


def test_real_consumed_sc07_legacy_request_hash_reconstructs_exactly() -> None:
    frozen_workspace = (
        Path(__file__).resolve().parents[1]
        / "var"
        / "mr1"
        / "runs"
        / "b932773c-4049-482a-8827-6933d924c34f"
    )
    state_path = frozen_workspace / "run_state.json"
    authority_path = frozen_workspace / "authority.json"
    state_bytes = state_path.read_bytes()
    authority_bytes = authority_path.read_bytes()
    state = json.loads(state_bytes)
    authority = json.loads(authority_bytes)
    service = object.__new__(MR1RealProductionService)

    request_v2 = service._pexels_request(
        state,
        authority,
        "SC-07",
        Path(state["workspace"]),
    )
    legacy_v1 = service._legacy_pexels_request_v1(request_v2)
    consumed_hash = (
        state["attempts"]["pexels:SC-07"]["request_hash"]
    )

    assert request_v2["request_hash"] == (
        "160fcb4fb5a8a989395acc39f237f381031a3cbd63e15bfdb2ce1fac4f86de84"
    )
    assert legacy_v1["request_hash"] == consumed_hash == (
        "10af25b3174a6f0cc55f9c694a5228f713ed67576ec3fb8008994a4409400fec"
    )
    assert "stock_search_intent" not in legacy_v1
    assert "stock_search_intent_scope" not in legacy_v1
    assert Path(legacy_v1["destination"]).is_absolute()
    assert state_path.read_bytes() == state_bytes
    assert authority_path.read_bytes() == authority_bytes


def test_continuation_rejects_unreviewed_task_id_without_provider_calls(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    first_gateways, first_fakes = _gateways(
        pexels=SemanticFailPexelsGateway()
    )
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    blocked = service.start(scope["command"], gateways=first_gateways)
    review_command = MR1ProviderAttemptContinuationReviewCommand(
        run_id=uuid.UUID(blocked["run_id"]),
        operation_key="pexels:SC-07",
        approved_stock_search_intent=(
            "People discussing office paperwork together."
        ),
        approved_pending_scene_stock_search_intents={
            "SC-09": "People working together in an office, planning."
        },
    )
    review = service.prepare_provider_attempt_continuation_review(
        review_command
    )
    mutations_before = _mutation_counts(first_fakes)

    with pytest.raises(
        ValidationFailureError,
        match=(
            "^MR1_PROVIDER_CONTINUATION_"
            "REVIEW_MANIFEST_AUTHORITY_MISMATCH$"
        ),
    ):
        service.approve_provider_attempt_continuation(
            MR1ProviderAttemptContinuationCommand(
                **review_command.model_dump(mode="python"),
                operator_review_manifest_artifact_version_id=uuid.UUID(
                    review["review_manifest_artifact_version_id"]
                ),
                operator_review_manifest_content_hash=review[
                    "review_manifest_content_hash"
                ],
                operator_review_task_id=uuid.uuid4(),
                decided_by_user_id=uuid.UUID(
                    review["required_decided_by_user_id"]
                ),
                operator_decision_text=review[
                    "required_operator_decision_text"
                ],
            )
        )

    assert _mutation_counts(first_fakes) == mutations_before
    task = db_session.get(
        ReviewTask,
        uuid.UUID(review["operator_review_task_id"]),
    )
    assert task is not None
    assert task.status == "open"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ApprovalDecision)
            .where(
                ApprovalDecision.target_artifact_version_id
                == uuid.UUID(
                    review["review_manifest_artifact_version_id"]
                )
            )
        )
        == 0
    )


def test_changed_continuation_manifest_cancels_stale_open_review_task(
    db_session, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    blocked = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    ).start(
        scope["command"],
        gateways=_gateways(pexels=SemanticFailPexelsGateway())[0],
    )
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    first_command = MR1ProviderAttemptContinuationReviewCommand(
        run_id=uuid.UUID(blocked["run_id"]),
        operation_key="pexels:SC-07",
        approved_stock_search_intent=(
            "People discussing office paperwork together."
        ),
        approved_pending_scene_stock_search_intents={},
    )
    first = service.prepare_provider_attempt_continuation_review(
        first_command
    )
    first_task = db_session.get(
        ReviewTask,
        uuid.UUID(first["operator_review_task_id"]),
    )
    assert first_task is not None
    assert first_task.status == "open"

    second_command = MR1ProviderAttemptContinuationReviewCommand(
        run_id=uuid.UUID(blocked["run_id"]),
        operation_key="pexels:SC-07",
        approved_stock_search_intent=(
            "Coworkers organize office documents together."
        ),
        approved_pending_scene_stock_search_intents={},
    )
    second = service.prepare_provider_attempt_continuation_review(
        second_command
    )
    second_task = db_session.get(
        ReviewTask,
        uuid.UUID(second["operator_review_task_id"]),
    )
    db_session.refresh(first_task)
    assert first_task.status == "cancelled"
    assert second_task is not None
    assert second_task.status == "open"
    assert second_task.id != first_task.id
    supersession = next(
        item
        for item in first_task.evidence_refs
        if item.get("type")
        == "mr1_provider_continuation_review_superseded"
    )
    assert supersession[
        "superseded_manifest_artifact_version_id"
    ] == first["review_manifest_artifact_version_id"]
    assert supersession[
        "superseded_manifest_content_hash"
    ] == first["review_manifest_content_hash"]
    assert supersession[
        "superseded_by_manifest_artifact_version_id"
    ] == second["review_manifest_artifact_version_id"]
    assert supersession[
        "superseded_by_manifest_content_hash"
    ] == second["review_manifest_content_hash"]
    assert second["superseded_review_tasks"] == [
        {
            "review_task_id": str(first_task.id),
            "status": "cancelled",
            "target_artifact_version_id": (
                first["review_manifest_artifact_version_id"]
            ),
            "target_content_hash": first[
                "review_manifest_content_hash"
            ],
            "supersession_evidence": supersession,
        }
    ]

    with pytest.raises(
        ValidationFailureError,
        match=(
            "^MR1_PROVIDER_CONTINUATION_REVIEW_MANIFEST_"
            "AUTHORITY_MISMATCH$"
        ),
    ):
        service.approve_provider_attempt_continuation(
            MR1ProviderAttemptContinuationCommand(
                **first_command.model_dump(mode="python"),
                operator_review_manifest_artifact_version_id=uuid.UUID(
                    first["review_manifest_artifact_version_id"]
                ),
                operator_review_manifest_content_hash=first[
                    "review_manifest_content_hash"
                ],
                operator_review_task_id=first_task.id,
                decided_by_user_id=uuid.UUID(
                    first["required_decided_by_user_id"]
                ),
                operator_decision_text=first[
                    "required_operator_decision_text"
                ],
            )
        )
    db_session.refresh(second_task)
    assert second_task.status == "open"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ApprovalDecision)
            .where(
                ApprovalDecision.target_artifact_version_id
                == uuid.UUID(
                    first["review_manifest_artifact_version_id"]
                )
            )
        )
        == 0
    )


def test_runtime_blocks_tampered_continuation_decision_before_provider_submit(
    db_session, engine, tmp_path: Path
) -> None:
    scope = _approved_mr1(db_session, tmp_path)
    blocked = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    ).start(
        scope["command"],
        gateways=_gateways(pexels=SemanticFailPexelsGateway())[0],
    )
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-runs",
        local_continuation=FakeLocalContinuation(),
    )
    review_command = MR1ProviderAttemptContinuationReviewCommand(
        run_id=uuid.UUID(blocked["run_id"]),
        operation_key="pexels:SC-07",
        approved_stock_search_intent=(
            "People discussing office paperwork together."
        ),
        approved_pending_scene_stock_search_intents={
            "SC-09": "People working together in an office, planning."
        },
    )
    review = service.prepare_provider_attempt_continuation_review(
        review_command
    )
    receipt = service.approve_provider_attempt_continuation(
        MR1ProviderAttemptContinuationCommand(
            **review_command.model_dump(mode="python"),
            operator_review_manifest_artifact_version_id=uuid.UUID(
                review["review_manifest_artifact_version_id"]
            ),
            operator_review_manifest_content_hash=review[
                "review_manifest_content_hash"
            ],
            operator_review_task_id=uuid.UUID(
                review["operator_review_task_id"]
            ),
            decided_by_user_id=uuid.UUID(
                review["required_decided_by_user_id"]
            ),
            operator_decision_text=review[
                "required_operator_decision_text"
            ],
        )
    )
    decision = db_session.get(
        ApprovalDecision,
        uuid.UUID(receipt["approval_decision_id"]),
    )
    assert decision is not None
    tampered_basis = deepcopy(decision.decision_basis)
    tampered_basis["additional_attempts"] = 99
    db_session.commit()
    with engine.begin() as independent_connection:
        independent_connection.execute(
            update(ApprovalDecision)
            .where(ApprovalDecision.id == decision.id)
            .values(decision_basis=tampered_basis)
        )
    continuation_gateways, continuation_fakes = _gateways()

    result = service.resume(
        run_id=uuid.UUID(blocked["run_id"]),
        gateways=continuation_gateways,
    )

    assert result["current_state"] == "BLOCKED_PRE_SUBMIT_REPAIRABLE"
    assert _mutation_counts(continuation_fakes) == {
        "narration": 0,
        "alignment": 0,
        "pexels_search": [],
        "pexels_download": [],
        "drive": 0,
    }
    supplemental_attempts = [
        item
        for item in result["attempts"]
        if item["operation_key"] == "pexels:SC-07:supplement:02"
    ]
    assert supplemental_attempts[0]["attempt_count"] == 0
    assert supplemental_attempts[0]["network_submit_started"] is False
    assert supplemental_attempts[0]["automatic_retry_allowed"] is False


def test_continuation_preview_report_preserves_generic_mr1_reports(
    monkeypatch, tmp_path: Path
) -> None:
    from scripts import run_mr1_real_production as runner

    generic_paths = {
        "SUMMARY_PATH": tmp_path / "mr1_summary.json",
        "REPAIR_CYCLES_PATH": tmp_path / "mr1_repair_cycles.json",
        "REPORT_PATH": tmp_path / "mr1_real_production_report.md",
    }
    for attribute, path in generic_paths.items():
        path.write_text(f"sentinel:{attribute}\n", encoding="utf-8")
        monkeypatch.setattr(runner, attribute, path)
    continuation_json = tmp_path / "mr1_pexels_continuation_review.json"
    continuation_markdown = tmp_path / "mr1_pexels_continuation_review.md"
    monkeypatch.setattr(
        runner,
        "CONTINUATION_REVIEW_JSON_PATH",
        continuation_json,
    )
    monkeypatch.setattr(
        runner,
        "CONTINUATION_REVIEW_MARKDOWN_PATH",
        continuation_markdown,
    )
    result = {
        "schema_version": (
            "mr1.provider-attempt-continuation-review.v1"
        ),
        "run_id": str(uuid.uuid4()),
        "run_artifact_version_id": str(uuid.uuid4()),
        "operation_key": "pexels:SC-07",
        "review_manifest_artifact_version_id": str(uuid.uuid4()),
        "review_manifest_content_hash": "a" * 64,
        "operator_review_task_id": str(uuid.uuid4()),
        "required_decided_by_user_id": str(uuid.uuid4()),
        "required_operator_decision_text": (
            "Phê duyệt thêm đúng 1 Pexels SC-07 attempt cho run này; "
            f"manifest sha256 {'a' * 64}"
        ),
        "approval_persisted": False,
        "provider_calls_made": 0,
        "review_manifest": {
            "scene_id": "SC-07",
            "package_artifact_version_id": str(uuid.uuid4()),
            "package_content_hash": "b" * 64,
            "prior_consumed_attempt": {
                "artifact_version_id": str(uuid.uuid4()),
                "content_hash": "c" * 64,
                "state": "CONSUMED_FAILED",
                "submit_state": "FAILED_CONSUMED",
                "search_submit_count": 1,
            },
            "package_semantic_intent": (
                "A founder reviews a detailed filing workflow while supporting "
                "office context illustrates the discussion."
            ),
            "approved_stock_search_intent": (
                "People discussing office paperwork together."
            ),
            "base_query_evidence": {
                "detailed_candidate_ranking_evidence_state": (
                    "UNAVAILABLE_NOT_DURABLY_CAPTURED"
                ),
                "detailed_candidate_ranking_evidence_fabricated": False,
                "query_authority": {
                    "primary_query": "founder filing workflow b roll"
                },
            },
            "approved_query_authority": {
                "package_semantic_intent": (
                    "A founder reviews a detailed filing workflow while "
                    "supporting office context illustrates the discussion."
                ),
                "stock_search_intent": (
                    "People discussing office paperwork together."
                ),
                "primary_query": (
                    "people discussing office paperwork workplace b roll"
                )
            },
            "query_material_diff": {
                "base_primary_query": "founder filing workflow b roll",
                "approved_primary_query": (
                    "people discussing office paperwork workplace b roll"
                ),
                "materially_different": True,
            },
            "stock_search_intent_derivation": {
                "package_semantic_intent": (
                    "A founder reviews a detailed filing workflow while "
                    "supporting office context illustrates the discussion."
                ),
                "approved_stock_search_intent": (
                    "People discussing office paperwork together."
                ),
                "package_semantic_intent_unchanged": True,
                "refs": {
                    "visual_source_decision": {
                        "artifact_version_id": str(uuid.uuid4()),
                        "content_hash": "e" * 64,
                    }
                },
            },
            "query_intent_coverage_evidence": {
                "query_intent_coverage": 0.8
            },
            "pending_query_amendments": {
                "SC-09": {
                    "operation_key": "pexels:SC-09",
                    "package_semantic_intent": (
                        "A planning scene uses supporting office context while "
                        "native graphics explain the actual mechanism."
                    ),
                    "approved_stock_search_intent": (
                        "People working together in an office, planning."
                    ),
                    "base_query_evidence": {
                        "query_authority": {
                            "primary_query": "planning mechanism b roll"
                        }
                    },
                    "approved_query_authority": {
                        "primary_query": (
                            "people working together office workplace b roll"
                        )
                    },
                    "query_material_diff": {
                        "base_primary_query": (
                            "planning mechanism b roll"
                        ),
                        "approved_primary_query": (
                            "people working together office workplace b roll"
                        ),
                        "materially_different": True,
                    },
                    "stock_search_intent_derivation": {
                        "package_semantic_intent": (
                            "A planning scene uses supporting office context "
                            "while native graphics explain the actual mechanism."
                        ),
                        "approved_stock_search_intent": (
                            "People working together in an office, planning."
                        ),
                    },
                    "query_intent_coverage_evidence": {
                        "query_intent_coverage": 0.8
                    },
                    "request_invariants": {
                        "semantic_fit_threshold": 0.78
                    },
                    "unsubmitted_attempt_snapshot": {
                        "artifact_version_id": str(uuid.uuid4()),
                        "content_hash": "d" * 64,
                        "state": "PLANNED",
                        "submit_state": "NOT_SUBMITTED",
                    },
                }
            },
            "semantic_fit_threshold": 0.78,
            "automatic_retry_allowed": False,
            "provider_substitution_allowed": False,
            "youtube_upload_authorized": False,
            "publish_execution_authorized": False,
        },
    }

    runner._write_success_reports(
        result=result,
        state={},
        readiness=None,
        invocation="continuation-preview-test",
        continuation_preview=True,
    )

    for attribute, path in generic_paths.items():
        assert path.read_text(encoding="utf-8") == (
            f"sentinel:{attribute}\n"
        )
    report = json.loads(continuation_json.read_text(encoding="utf-8"))
    assert report["provider_calls_made"] == 0
    assert report["exact_refs"]["operator_review_task_id"] == result[
        "operator_review_task_id"
    ]
    assert report["consumed_attempt_proof"]["state"] == "CONSUMED_FAILED"
    assert report["pending_unsubmitted_attempt_proofs"]["SC-09"][
        "submit_state"
    ] == "NOT_SUBMITTED"
    assert report["semantic_fit_threshold"]["unchanged"] is True
    assert report["package_semantic_intent_unchanged"] is True
    assert report["query_review"]["primary"]["query_material_diff"][
        "materially_different"
    ] is True
    assert report["query_review"]["primary"]["base_query_evidence"][
        "detailed_candidate_ranking_evidence_state"
    ] == "UNAVAILABLE_NOT_DURABLY_CAPTURED"
    assert report["query_review"]["primary"][
        "stock_search_intent_derivation"
    ]["refs"]["visual_source_decision"]["artifact_version_id"]
    assert continuation_markdown.exists()

    runner._write_failure_reports(
        result={
            "terminal_error": (
                "MR1_PROVIDER_CONTINUATION_PREVIEW_TEST_FAILURE"
            )
        },
        state={},
        readiness={"result": "PASS", "provider_calls": 0},
        invocation="continuation-preview-failure-test",
        continuation_preview=True,
    )
    for attribute, path in generic_paths.items():
        assert path.read_text(encoding="utf-8") == (
            f"sentinel:{attribute}\n"
        )
    failure_report = json.loads(
        continuation_json.read_text(encoding="utf-8")
    )
    assert failure_report["schema_version"] == (
        "mr1.pexels-continuation-review-failure-report.v1"
    )
    assert failure_report["provider_calls_made"] == 0
    assert failure_report["approval_persisted"] is False

    with pytest.raises(
        RuntimeError,
        match="^MR1_CONTINUATION_PREVIEW_RESULT_SCHEMA_INVALID$",
    ):
        runner._write_success_reports(
            result={"schema_version": "unexpected.preview.result"},
            state={},
            readiness=None,
            invocation="continuation-preview-schema-tamper",
            continuation_preview=True,
        )
    for attribute, path in generic_paths.items():
        assert path.read_text(encoding="utf-8") == (
            f"sentinel:{attribute}\n"
        )


def test_controlled_runner_reports_both_required_named_verdicts() -> None:
    from scripts import run_mr1_real_production as runner

    result = runner._failure_result("FOCUSED_REPORT_CONTRACT")
    assert result["MR1_REQUIRED_PROVIDER_EXECUTION"] == "FAIL"
    assert result["MR1_ASSET_RESOLUTION"] == "FAIL"

    result["MR1_REQUIRED_PROVIDER_EXECUTION"] = "PASS"
    result["MR1_ASSET_RESOLUTION"] = "PASS"
    summary, _repairs, markdown = runner._report_payloads(
        result=result,
        state={"repair_cycles": []},
        readiness={"result": "PASS"},
        invocation="focused-report-contract",
    )

    assert summary["verdicts"]["MR1_REQUIRED_PROVIDER_EXECUTION"] == "PASS"
    assert summary["verdicts"]["MR1_ASSET_RESOLUTION"] == "PASS"
    assert "MR1_REQUIRED_PROVIDER_EXECUTION" in markdown
    assert "MR1_ASSET_RESOLUTION" in markdown
    verdict_block = runner._verdict_block(result)
    assert "MR1_REQUIRED_PROVIDER_EXECUTION=PASS" in verdict_block
    assert "MR1_ASSET_RESOLUTION=PASS" in verdict_block


def test_runner_accepts_operator_text_for_exact_pexels_continuation_only() -> None:
    from types import SimpleNamespace

    from scripts import run_mr1_real_production as runner

    args = SimpleNamespace(
        closeout=False,
        approve_extra_pexels_attempt=True,
        approve_extra_pexels_sc04_attempt=False,
        human_decision=None,
        operator_decision_text="exact continuation authority",
        review_media_candidate_artifact_version_id=None,
        review_media_candidate_content_hash=None,
        reviewed_output_sha256=None,
        drive_archive_receipt_artifact_version_id=None,
        drive_archive_receipt_content_hash=None,
        archive_identity=None,
        decided_by_user_id=None,
    )
    assert runner._explicit_closeout_command(args) is None

    args.approve_extra_pexels_attempt = False
    with pytest.raises(
        SystemExit,
        match="closeout-only inputs require the explicit --closeout mode",
    ):
        runner._explicit_closeout_command(args)

    args.approve_extra_pexels_attempt = True
    args.reviewed_output_sha256 = "0" * 64
    with pytest.raises(
        SystemExit,
        match="closeout-only inputs require the explicit --closeout mode",
    ):
        runner._explicit_closeout_command(args)


def test_sc04_revision_fake_transport_uses_native_motion_and_waits_after_drive(
    db_session, tmp_path: Path
) -> None:
    _source, pending, closeout = _approved_sc04_revision(db_session, tmp_path)
    approval = _approve_mr1(db_session, pending, closeout)
    command = MR1StartCommand(
        approval_id=uuid.UUID(approval["approval_id"]),
        approval_content_hash=approval["approval_content_hash"],
        project_id=uuid.UUID(approval["exact_target"]["project_id"]),
        package_artifact_version_id=uuid.UUID(
            approval["exact_target"]["package_artifact_version_id"]
        ),
    )
    gateways, fakes = _gateways()
    local = FakeLocalContinuation()
    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-sc04-runs",
        local_continuation=local,
    )
    final_before = db_session.scalar(select(func.count()).select_from(FinalMediaRef))

    result = service.start(command, gateways=gateways)

    assert _mutation_counts(fakes) == {
        "narration": 1,
        "alignment": 1,
        "pexels_search": ["SC-07", "SC-09"],
        "pexels_download": ["SC-07", "SC-09"],
        "drive": 1,
    }
    assert result["MR1_REQUIRED_PROVIDER_EXECUTION"] == "PASS"
    assert result["MR1_ASSET_RESOLUTION"] == "PASS"
    assert result["MR1_PROVIDER_CALL_COUNT"] == 5
    assert result["provider_call_counts"] == {
        "elevenlabs_narration": 1,
        "forced_alignment": 1,
        "pexels_scene_flows": 2,
        "google_gemini_image": 0,
        "google_veo": 0,
        "google_drive_archive_flows": 1,
        "youtube": 0,
    }

    pexels_attempts = [
        item for item in result["attempts"] if item["provider"] == "pexels_api"
    ]
    assert [item["scene_id"] for item in pexels_attempts] == ["SC-07", "SC-09"]
    assert all("SC-04" not in item["operation_key"] for item in pexels_attempts)
    assert all(item["attempt_count"] == 1 for item in pexels_attempts)

    scene_executions = {
        item["scene_id"]: item for item in result["scene_executions"]
    }
    assert scene_executions["SC-04"]["route"] == "NATIVE_MOTION_GRAPHIC"
    assert scene_executions["SC-07"]["route"] == "PEXELS_VIDEO"
    assert scene_executions["SC-09"]["route"] == "PEXELS_VIDEO"
    assert all(item["fallback_used"] is False for item in scene_executions.values())

    candidate = result["review_media_candidate"]
    provenance = json.loads(
        Path(candidate["asset_provenance_manifest_ref"]).read_text(encoding="utf-8")
    )
    provenance_by_scene = {
        item["scene_id"]: item for item in provenance["items"]
    }
    assert provenance_by_scene["SC-04"]["route"] == "NATIVE_MOTION_GRAPHIC"
    assert provenance_by_scene["SC-04"]["rights"] == {
        "rights_status": "NOT_REQUIRED",
        "source_type": "NATIVE_OWNED",
    }
    assert provenance["native_scene_count"] == 7
    assert provenance["pexels_scene_count"] == 2

    event_order = result["event_order"]
    assert not any(event.startswith("PEXELS_SC-04_") for event in event_order)
    assert (
        event_order.index("REVIEW_MEDIA_CANDIDATE_CREATED")
        < event_order.index("DRIVE_ARCHIVE_VERIFIED")
        < event_order.index("HUMAN_FULL_WATCH_PENDING")
    )
    assert result["ARCHIVE_VERIFIED"] is True
    assert result["MR1_DRIVE_ARCHIVE"] == "PASS"
    assert result["MR1_HUMAN_REVIEW"] == "PENDING"
    assert result["MR1_FINAL_MEDIA_REF"] == "NOT_CREATED"
    assert result["MR1_FINAL"] == "WAITING_HUMAN_REVIEW"
    assert result["final_media_ref_id"] is None
    assert "FINAL_MEDIA_REF_CREATED" not in event_order
    assert (
        db_session.scalar(select(func.count()).select_from(FinalMediaRef))
        == final_before
    )


def test_stock_search_reopens_one_unique_exact_ref_across_package_sections(
    db_session, tmp_path: Path
) -> None:
    _source, pending, _closeout = _approved_sc04_revision(db_session, tmp_path)
    package = db_session.get(
        ArtifactVersion,
        uuid.UUID(pending["package_artifact_version_id"]),
    )
    assert package is not None
    package_content = deepcopy(package.content or {})
    authority = {
        "project_id": pending["video_project_id"],
        "authority_project_ids": deepcopy(
            package_content["effective_artifact_authority"][
                "authority_project_ids"
            ]
        ),
        "package": package_content,
        "resolved": {},
    }
    effective_ref = package_content["effective_artifacts"][
        "visual_source_decision_set"
    ]
    assert (
        effective_ref
        == package_content["revised_artifacts"]["visual_source_decision_set"]
    )

    service = MR1RealProductionService(
        db_session,
        workspace_root=tmp_path / "mr1-stock-ref-reopen",
    )
    reopened = service._reopen_package_bound_artifact_version(
        authority=authority,
        artifact_type="visual_source_decision_set",
    )
    assert str(reopened.id) == effective_ref["artifact_version_id"]
    assert reopened.content_hash == effective_ref["content_hash"]

    conflicting_authority = deepcopy(authority)
    conflicting_authority["package"]["revised_artifacts"][
        "visual_source_decision_set"
    ]["content_hash"] = "0" * 64
    with pytest.raises(
        ValidationFailureError,
        match="^MR1_STOCK_SEARCH_VISUAL_SOURCE_DECISION_SET_REF_INVALID$",
    ):
        service._reopen_package_bound_artifact_version(
            authority=conflicting_authority,
            artifact_type="visual_source_decision_set",
        )
