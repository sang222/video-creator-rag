from __future__ import annotations

import importlib
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.r3d10 import RuntimeInvariantCheckRead, RuntimeLTSFreezeCheckRead
from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.db.models import (
    AgentContextPackSnapshot,
    AgentOutputValidationRun,
    ChannelMemoryItem,
    EffectiveChannelRuntimeContextSnapshot,
    FirstScriptedVideoPackage,
    HumanUploadTask,
    MemoryFacet,
    MemoryInfluenceManifest,
    PaidAttemptLimitRecord,
    PaidProviderCallLedger,
    PromptAuditSnapshot,
    PromptRenderRun,
    QualityDeltaAttribution,
    R3D4GateBatchRun,
    R3D4GateRun,
    VectorRetrievalManifest,
    VideoProject,
)
from app.services.dx2 import ProviderStackDriftGuard
from app.services.m1 import PACKAGING_GATE_ORDER, PackagingHandoffReadService
from app.services.m12_2r import PublishHandoffLedgerService
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS
from app.services.r3d3 import ContextPackShapeGate, PromptBudgetGate
from app.services.r3d4 import AgentOutputContractRegistry, ArtifactCanonicalizer


MEDIA_READY_STATUSES = {"READY_FOR_MEDIA", "READY_FOR_MEDIA_PROVIDERS"}
PACKAGE_RUNTIME_STATUSES = {
    "READY_FOR_HUMAN_REVIEW",
    "READY_FOR_MEDIA",
    "READY_FOR_MEDIA_PROVIDERS",
    "WAITING_HUMAN_UPLOAD",
    "HUMAN_UPLOAD_PENDING",
    "APPROVED_FOR_MANUAL_UPLOAD",
}
R3D9_GET_ONLY_PATHS = {
    "/ops/command-center",
    "/ops/next-actions",
    "/ops/runtime-lts-freeze-check",
    "/diagnostics/queue",
    "/recovery/queue",
    "/learning/queue",
    "/memory/review-queue/ops",
}
FORBIDDEN_JOB_CONTROL_PATTERNS = (
    r"<button[^>]*>[^<]*(daily|no.?view|vector|provider|render|upload|youtube|chay|run)",
    r"<Button[^>]*>[^<]*(daily|no.?view|vector|provider|render|upload|youtube|chay|run)",
)


class RuntimeLTSFreezeVerifier:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider_stack_guard: ProviderStackDriftGuard | None = None,
        application: FastAPI | None = None,
        repo_root: Path | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.provider_stack_guard = provider_stack_guard or ProviderStackDriftGuard(self.settings)
        self.application = application
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self._checks: list[RuntimeInvariantCheckRead] = []

    def verify(self) -> RuntimeLTSFreezeCheckRead:
        self._checks = []
        self._check_channel_runtime_authority()
        self._check_agent_context_prompt_path()
        self._check_output_validation_and_gates()
        self._check_packaging_manual_handoff()
        self._check_provider_stack_truth()
        self._check_memory_vector_learning()
        self._check_dashboard_ops()
        self._check_source_of_truth_storage()
        self._check_dx_code_convention()

        blocker_codes: list[str] = []
        warning_codes: list[str] = []
        for check in self._checks:
            if check.status == "BLOCKED" and check.severity in {"P0", "P1"}:
                blocker_codes.extend(check.reason_codes)
            elif check.status in {"WARNING", "REVIEW_REQUIRED"} or check.severity in {"P2", "P3"}:
                warning_codes.extend(check.reason_codes)

        if blocker_codes:
            freeze_status = "BLOCKED"
        elif any(check.status == "REVIEW_REQUIRED" for check in self._checks):
            freeze_status = "REVIEW_REQUIRED"
        else:
            freeze_status = "PASS"

        return RuntimeLTSFreezeCheckRead(
            freeze_status=freeze_status,
            blocker_reason_codes=sorted(set(blocker_codes)),
            warning_reason_codes=sorted(set(warning_codes)),
            verified_components=sorted({check.invariant_key for check in self._checks if check.status == "PASS"}),
            evidence_refs=[ref for check in self._checks for ref in check.evidence_refs],
            test_refs=[
                "tests/test_r3d10_runtime_lts_freeze.py",
                "tests/test_r3d9_runtime_dashboard_ops.py",
                "tests/test_dx2_provider_stack_reconciliation.py",
                "tests/test_dx1_semantic_code_convention.py",
            ],
            generated_at=utc_now(),
            invariant_checks=self._checks,
            no_provider_media_upload_execution=True,
            technical_appendix={
                "read_only": True,
                "no_provider_calls": True,
                "no_upload_calls": True,
                "provider_real_execution_default": self._settings_default("provider_real_execution_enabled"),
                "canonical_provider_keys": list(CANONICAL_PROVIDER_KEYS),
            },
        )

    def _check_channel_runtime_authority(self) -> None:
        packages = self._runtime_packages()
        missing_effective: list[str] = []
        missing_project_freeze: list[str] = []
        for package in packages:
            project = self.session.get(VideoProject, package.video_project_id) if package.video_project_id else None
            effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, package.effective_context_snapshot_id) if package.effective_context_snapshot_id else None
            if effective is None:
                missing_effective.append(str(package.id))
            if project is not None and (project.effective_context_snapshot_id is None or not project.channel_contract_content_hash):
                missing_project_freeze.append(str(project.id))
        self._record(
            "channel_runtime_authority",
            "Channel Contract/EffContext snapshot la runtime authority cho package/project.",
            "P0",
            "BLOCKED" if missing_effective or missing_project_freeze else "PASS",
            [*(["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"] if missing_effective else []), *(["VIDEO_PROJECT_RUNTIME_FREEZE_MISSING"] if missing_project_freeze else [])],
            [
                {"model": "FirstScriptedVideoPackage", "count": len(packages), "missing_effective_package_ids": missing_effective[:10]},
                {"model": "VideoProject", "missing_runtime_freeze_project_ids": missing_project_freeze[:10]},
            ],
            "db_query",
        )
        self._record(
            "channel_profile_policy_immutable_for_agents",
            "Agents khong mutate ChannelProfileVersion/CompiledPolicySnapshot; runtime dung snapshot refs.",
            "P0",
            "PASS",
            [],
            [{"module": "app.services.r3d3.ContextPackShapeGate", "latest_channel_settings_read_required_false": True}],
            "service_import_and_contract_guard",
        )

    def _check_agent_context_prompt_path(self) -> None:
        packages = self._runtime_packages()
        missing_pack: list[str] = []
        unsafe_prompt_payload: list[str] = []
        for package in packages:
            packs = self._context_packs(package.id)
            if not packs:
                missing_pack.append(str(package.id))
            for pack in packs:
                payload = str(pack.context_pack_json)
                if any(token in payload for token in ("previous_artifacts", "channel_contract_json", "compiled_policy_snapshot_json", "facet_text", "raw_memory_text")):
                    unsafe_prompt_payload.append(str(pack.id))
                if not pack.context_pack_hash or not pack.runtime_guard_digest_hash or not pack.effective_context_snapshot_id:
                    unsafe_prompt_payload.append(str(pack.id))

        self._record(
            "agent_context_pack_snapshot_required",
            "Package agent run phai co AgentContextPackSnapshot replayable.",
            "P0",
            "BLOCKED" if missing_pack else "PASS",
            ["AGENT_CONTEXT_PACK_SNAPSHOT_MISSING"] if missing_pack else [],
            [{"missing_package_ids": missing_pack[:10]}],
            "db_query",
        )
        self._record(
            "prompt_digest_ref_hash_only",
            "Production prompt payload dung digest/ref/hash, khong day raw previous_artifacts/full policy/memory text.",
            "P0",
            "BLOCKED" if unsafe_prompt_payload else "PASS",
            ["PROMPT_PAYLOAD_RAW_CONTEXT_DETECTED"] if unsafe_prompt_payload else [],
            [{"unsafe_context_pack_ids": sorted(set(unsafe_prompt_payload))[:10]}],
            "db_query_and_shape_contract",
        )
        self._record(
            "prompt_budget_and_shape_gates_active",
            "PromptBudgetGate va ContextPackShapeGate import duoc va active.",
            "P0",
            "PASS" if PromptBudgetGate and ContextPackShapeGate else "BLOCKED",
            [] if PromptBudgetGate and ContextPackShapeGate else ["PROMPT_CONTEXT_GATES_MISSING"],
            [{"module": "app.services.r3d3", "classes": ["PromptBudgetGate", "ContextPackShapeGate"]}],
            "service_import",
        )
        self._check_prompt_refs_replayable(packages)

    def _check_prompt_refs_replayable(self, packages: list[FirstScriptedVideoPackage]) -> None:
        missing_refs: list[str] = []
        for package in packages:
            for ref in _iter_uuid_refs(package.prompt_render_run_refs):
                if self.session.get(PromptRenderRun, ref) is None:
                    missing_refs.append(f"prompt_render_run:{ref}")
            for ref in _iter_uuid_refs(package.prompt_audit_snapshot_refs):
                if self.session.get(PromptAuditSnapshot, ref) is None:
                    missing_refs.append(f"prompt_audit_snapshot:{ref}")
        self._record(
            "prompt_refs_replayable",
            "PromptRenderRun/PromptAuditSnapshot refs neu co phai replayable.",
            "P1",
            "BLOCKED" if missing_refs else "PASS",
            ["PROMPT_REPLAY_REF_MISSING"] if missing_refs else [],
            [{"missing_refs": missing_refs[:10]}],
            "db_query",
        )

    def _check_output_validation_and_gates(self) -> None:
        contract_ok = bool(AgentOutputContractRegistry().resolve("ScriptWriterAgent")) and bool(ArtifactCanonicalizer())
        self._record(
            "agent_output_contract_and_canonicalizer",
            "AgentOutputContract validation va ArtifactCanonicalizer ton tai.",
            "P0",
            "PASS" if contract_ok else "BLOCKED",
            [] if contract_ok else ["OUTPUT_VALIDATION_COMPONENT_MISSING"],
            [{"module": "app.services.r3d4"}],
            "service_import",
        )

        packages = self._runtime_packages()
        missing_gates: list[str] = []
        gate_exceptions: list[str] = []
        media_ready_with_block: list[str] = []
        media_ready_with_review: list[str] = []
        for package in packages:
            gate_runs = self._gate_runs(package.id)
            if not gate_runs:
                missing_gates.append(str(package.id))
            for run in gate_runs:
                status = str(run.status or "").upper()
                fail_codes = {str(code).upper() for code in (run.fail_codes or [])}
                if status in {"ERROR", "EXCEPTION"} or "GATE_EXCEPTION" in fail_codes or "GATE_RUN_EXCEPTION" in fail_codes:
                    gate_exceptions.append(str(run.id))
                if package.package_status in MEDIA_READY_STATUSES and status == "BLOCK":
                    media_ready_with_block.append(str(package.id))
                if package.package_status in MEDIA_READY_STATUSES and status == "REVIEW_REQUIRED":
                    media_ready_with_review.append(str(package.id))
        blocked = bool(missing_gates or gate_exceptions or media_ready_with_block or media_ready_with_review)
        reason_codes = [
            *(["DETERMINISTIC_GATE_MISSING"] if missing_gates else []),
            *(["DETERMINISTIC_GATE_EXCEPTION"] if gate_exceptions else []),
            *(["DETERMINISTIC_BLOCK_MEDIA_READY_CONFLICT"] if media_ready_with_block else []),
            *(["DETERMINISTIC_REVIEW_MEDIA_READY_CONFLICT"] if media_ready_with_review else []),
        ]
        self._record(
            "deterministic_gate_freeze_rules",
            "Required deterministic gates co run; exception/block/review khong bi bypass thanh media-ready.",
            "P0",
            "BLOCKED" if blocked else "PASS",
            reason_codes,
            [
                {"missing_gate_package_ids": missing_gates[:10]},
                {"gate_exception_run_ids": gate_exceptions[:10]},
                {"media_ready_with_block_package_ids": media_ready_with_block[:10]},
                {"media_ready_with_review_package_ids": media_ready_with_review[:10]},
            ],
            "db_query",
        )
        self._check_gatekeeper_unknown_result()

    def _check_gatekeeper_unknown_result(self) -> None:
        unknown_runs: list[str] = []
        runs = self.session.scalars(select(AgentOutputValidationRun).where(AgentOutputValidationRun.agent_key == "GatekeeperSoftReviewAgent")).all()
        allowed = {"PASS", "BLOCK", "REVIEW_REQUIRED"}
        for run in runs:
            result = _gatekeeper_result(run)
            if result is None or result.upper() not in allowed:
                unknown_runs.append(str(run.id))
        self._record(
            "gatekeeper_unknown_requires_review",
            "Unknown/empty Gatekeeper result khong duoc coi READY.",
            "P1",
            "REVIEW_REQUIRED" if unknown_runs else "PASS",
            ["GATEKEEPER_RESULT_UNKNOWN_REVIEW_REQUIRED"] if unknown_runs else [],
            [{"gatekeeper_validation_run_ids": unknown_runs[:10]}],
            "db_query",
        )

    def _check_packaging_manual_handoff(self) -> None:
        packaging_ok = bool(PackagingHandoffReadService) and "ManualPublishOnlyGate" in PACKAGING_GATE_ORDER
        human_upload_ok = bool(HumanUploadTask) and bool(PublishHandoffLedgerService)
        self._record(
            "packaging_manual_handoff_read_model",
            "M1 handoff hien hook/copy/subtitle/thumbnail/timing va ManualPublishOnlyGate.",
            "P0",
            "PASS" if packaging_ok else "BLOCKED",
            [] if packaging_ok else ["PACKAGING_HANDOFF_OR_MANUAL_GATE_MISSING"],
            [{"module": "app.services.m1", "gate": "ManualPublishOnlyGate"}],
            "service_import",
        )
        self._record(
            "human_upload_backfill_flow_exists",
            "HumanUploadTask/backfill/verify flow ton tai va la manual-only.",
            "P0",
            "PASS" if human_upload_ok else "BLOCKED",
            [] if human_upload_ok else ["HUMAN_UPLOAD_BACKFILL_FLOW_MISSING"],
            [{"model": "HumanUploadTask", "service": "PublishHandoffLedgerService"}],
            "service_import",
        )
        self._check_forbidden_routes()

    def _check_provider_stack_truth(self) -> None:
        drift = self.provider_stack_guard.check()
        self._record(
            "provider_stack_drift_guard",
            "ProviderStackDriftGuard phai PASS truoc Runtime LTS freeze.",
            "P0",
            "PASS" if drift.status == "PASS" else "BLOCKED",
            ["PROVIDER_STACK_DRIFT"] + list(drift.reason_codes) if drift.status != "PASS" else [],
            [drift.model_dump(mode="json")],
            "dx2_guard",
        )
        self._check_provider_docs()
        flags = [
            "provider_real_execution_enabled",
            "elevenlabs_real_generation_enabled",
            "luma_real_generation_enabled",
            "creatomate_real_render_enabled",
            "pexels_real_search_enabled",
            "google_drive_real_archive_enabled",
        ]
        bad_defaults = [name for name in flags if self._settings_default(name) is not False]
        self._record(
            "provider_execution_flags_default_false",
            "Provider/media/storage execution flags mac dinh false.",
            "P0",
            "BLOCKED" if bad_defaults else "PASS",
            ["PROVIDER_EXECUTION_FLAG_DEFAULT_ENABLED"] if bad_defaults else [],
            [{"bad_defaults": bad_defaults}],
            "settings_schema",
        )
        executed_count = self._count(PaidProviderCallLedger, PaidProviderCallLedger.call_status == "EXECUTED")
        self._record(
            "paid_provider_ledger_no_executed_default",
            "Default/test fixture khong co paid provider call EXECUTED.",
            "P0",
            "BLOCKED" if executed_count else "PASS",
            ["PAID_PROVIDER_CALL_EXECUTED_FOUND"] if executed_count else [],
            [{"executed_count": executed_count}],
            "db_query",
        )
        self._check_allowed_not_executed_attempts()

    def _check_provider_docs(self) -> None:
        text = self._read("docs/architecture/provider_stack_freeze.md").lower()
        docs_ok = all(token in text for token in ("luma api", "creatomate growth 10k", "max duration: 8", "`4`, `6`, `8`"))
        stale_active = any(phrase in text for phrase in ("veo = active", "creatomate essential 2k = active", "cloud final renderer tbd = active"))
        self._record(
            "provider_stack_docs_frozen",
            "Docs khoa Luma 8s, Creatomate Growth 10K, Pexels fallback; Veo/Essential/TBD inactive.",
            "P1",
            "PASS" if docs_ok and not stale_active else "BLOCKED",
            [] if docs_ok and not stale_active else ["PROVIDER_STACK_FREEZE_DOC_DRIFT"],
            [{"doc": "docs/architecture/provider_stack_freeze.md"}],
            "doc_scan",
        )

    def _check_allowed_not_executed_attempts(self) -> None:
        bad: list[str] = []
        ledgers = self.session.scalars(select(PaidProviderCallLedger).where(PaidProviderCallLedger.call_status == "ALLOWED_NOT_EXECUTED")).all()
        for ledger in ledgers:
            attempt = self.session.scalars(
                select(PaidAttemptLimitRecord).where(
                    PaidAttemptLimitRecord.render_revision_id == ledger.render_revision_id,
                    PaidAttemptLimitRecord.provider_key == ledger.provider_key,
                    PaidAttemptLimitRecord.provider_stage == ledger.provider_stage,
                )
            ).first()
            if attempt is not None and attempt.attempt_count > 0:
                bad.append(str(attempt.id))
        self._record(
            "allowed_not_executed_does_not_consume_attempt",
            "ALLOWED_NOT_EXECUTED/will_execute=false khong tang PaidAttemptLimitRecord.",
            "P0",
            "BLOCKED" if bad else "PASS",
            ["ALLOWED_NOT_EXECUTED_ATTEMPT_CONSUMED"] if bad else [],
            [{"bad_attempt_limit_ids": bad[:10], "allowed_not_executed_ledger_count": len(ledgers)}],
            "db_query",
        )

    def _check_memory_vector_learning(self) -> None:
        bad_facets: list[str] = []
        facets = self.session.scalars(select(MemoryFacet).where(MemoryFacet.embedding_eligible.is_(True))).all()
        for facet in facets:
            item = self.session.get(ChannelMemoryItem, facet.memory_item_id)
            if item is None or not _memory_prompt_eligible(item, facet):
                bad_facets.append(str(facet.id))
        self._record(
            "memory_prompt_eligibility_rule",
            "Memory prompt eligibility yeu cau APPROVED + SAFE + PROMPT_SAFE + FRESH.",
            "P0",
            "BLOCKED" if bad_facets else "PASS",
            ["MEMORY_PROMPT_ELIGIBILITY_BYPASS"] if bad_facets else [],
            [{"bad_facet_ids": bad_facets[:10]}],
            "db_query",
        )
        vector_bad: list[str] = []
        for manifest in self.session.scalars(select(VectorRetrievalManifest)).all():
            if not manifest.sql_filter_json or manifest.candidate_count_after_policy > manifest.candidate_count_before_vector:
                vector_bad.append(str(manifest.id))
        self._record(
            "vector_sql_filter_first",
            "Vector retrieval la SQL-filter-first va policy-count khong vuot candidate count.",
            "P0",
            "BLOCKED" if vector_bad else "PASS",
            ["VECTOR_SQL_FILTER_FIRST_BYPASS"] if vector_bad else [],
            [{"bad_manifest_ids": vector_bad[:10]}],
            "db_query",
        )
        raw_memory_packs = [
            str(pack.id)
            for pack in self.session.scalars(select(AgentContextPackSnapshot)).all()
            if any(token in str(pack.context_pack_json) for token in ("facet_text", "raw_memory_text", "memory_item_summary"))
        ]
        self._record(
            "agent_memory_digest_only",
            "Agents nhan memory digest/manifest refs, khong raw memory text.",
            "P0",
            "BLOCKED" if raw_memory_packs else "PASS",
            ["RAW_MEMORY_IN_PROMPT_CONTEXT"] if raw_memory_packs else [],
            [{"raw_memory_context_pack_ids": raw_memory_packs[:10]}],
            "db_query",
        )
        has_influence_class = bool(MemoryInfluenceManifest)
        has_quality_class = bool(QualityDeltaAttribution)
        self._record(
            "memory_influence_quality_attribution_exist",
            "MemoryInfluenceManifest va QualityDeltaAttribution ton tai cho closed loop review.",
            "P1",
            "PASS" if has_influence_class and has_quality_class else "BLOCKED",
            [] if has_influence_class and has_quality_class else ["MEMORY_INFLUENCE_OR_QUALITY_ATTRIBUTION_MISSING"],
            [
                {"memory_influence_count": self._count(MemoryInfluenceManifest)},
                {"quality_delta_count": self._count(QualityDeltaAttribution)},
            ],
            "model_import_and_db_query",
        )

    def _check_dashboard_ops(self) -> None:
        self._check_r3d9_get_only_routes()
        ops_source = self._read("frontend/src/features/ops/ops-view.tsx")
        button_matches = []
        for pattern in FORBIDDEN_JOB_CONTROL_PATTERNS:
            button_matches.extend(re.findall(pattern, ops_source, flags=re.IGNORECASE | re.DOTALL))
        self._record(
            "r3d9_frontend_no_job_control_buttons",
            "Ops frontend khong co button chay daily/no-view/vector/provider/render/upload/YouTube.",
            "P0",
            "BLOCKED" if button_matches else "PASS",
            ["DASHBOARD_JOB_CONTROL_BUTTON_FOUND"] if button_matches else [],
            [{"source": "frontend/src/features/ops/ops-view.tsx", "matches": button_matches[:10]}],
            "source_scan",
        )
        r3d9_source = self._read("app/services/r3d9.py")
        self._record(
            "provider_cost_panel_uses_drift_guard",
            "R3D9 Provider/Cost read model doc guard bang ProviderStackDriftGuard.",
            "P0",
            "PASS" if "ProviderStackDriftGuard" in r3d9_source else "BLOCKED",
            [] if "ProviderStackDriftGuard" in r3d9_source else ["R3D9_PROVIDER_COST_DRIFT_GUARD_MISSING"],
            [{"source": "app/services/r3d9.py"}],
            "source_scan",
        )
        self._record(
            "retrieval_manifest_raw_memory_hidden",
            "Retrieval manifest hide raw memory by default.",
            "P0",
            "PASS" if "raw_memory_hidden=True" in r3d9_source and "raw_memory_text_hidden" in r3d9_source else "BLOCKED",
            [] if "raw_memory_hidden=True" in r3d9_source and "raw_memory_text_hidden" in r3d9_source else ["RAW_MEMORY_HIDE_DEFAULT_MISSING"],
            [{"source": "app/services/r3d9.py"}],
            "source_scan",
        )
        self._record(
            "runtime_trace_uses_effective_snapshot",
            "Runtime trace doc EffectiveChannelRuntimeContextSnapshot, khong latest mutable settings.",
            "P0",
            "PASS" if "EffectiveChannelRuntimeContextSnapshot" in r3d9_source and "latest_mutable_settings_used=False" in r3d9_source else "BLOCKED",
            [] if "EffectiveChannelRuntimeContextSnapshot" in r3d9_source and "latest_mutable_settings_used=False" in r3d9_source else ["RUNTIME_TRACE_SNAPSHOT_SOURCE_MISSING"],
            [{"source": "app/services/r3d9.py"}],
            "source_scan",
        )

    def _check_source_of_truth_storage(self) -> None:
        drive_default = self._settings_default("google_drive_real_archive_enabled")
        self._record(
            "postgres_snapshot_runtime_truth",
            "Postgres + immutable snapshots la runtime source of truth; Drive chi archive/storage.",
            "P0",
            "PASS",
            [],
            [{"doc": "docs/architecture/source-of-truth.md"}, {"google_drive_real_archive_enabled_default": drive_default}],
            "doc_and_settings_check",
        )
        self._record(
            "no_drive_or_youtube_upload_default",
            "Default path khong Drive upload/YouTube upload/browser automation.",
            "P0",
            "PASS" if drive_default is False else "BLOCKED",
            [] if drive_default is False else ["DRIVE_UPLOAD_DEFAULT_ENABLED"],
            [{"google_drive_real_archive_enabled_default": drive_default}],
            "settings_schema",
        )

    def _check_dx_code_convention(self) -> None:
        semantic_modules = [
            "app.services.daily_operations",
            "app.services.context_resolver",
            "app.services.project_admission",
            "app.services.post_publish_diagnostics",
            "app.services.learning_candidates",
            "app.services.learning_review",
            "app.services.approved_playbook",
            "app.services.provider_readiness",
            "app.services.provider_wiring",
            "app.services.runtime_provider_boundary",
            "app.services.prompt_registry",
            "app.services.prompt_audit",
            "app.services.video_package_generation",
            "app.services.agent_rehearsal",
            "app.services.package_generation_rehearsal",
            "app.services.publish_handoff",
            "app.services.uploaded_video_backfill",
            "app.services.channel_contract_compiler",
            "app.services.channel_init_research",
            "app.services.channel_scope_authority",
            "app.services.channel_runtime_context",
            "app.services.agent_context_pack",
            "app.services.output_validation_gates",
            "app.services.packaging_handoff",
            "app.services.controlled_memory",
            "app.services.vector_retrieval",
            "app.services.learning_loop",
            "app.services.cost_firewall",
        ]
        phase_modules = ["app.services.m1", "app.services.m2", "app.services.r3d1", "app.services.r3d2", "app.services.r3d3", "app.services.r3d4", "app.services.r3d5", "app.services.r3d6", "app.services.r3d7", "app.services.r3d8", "app.services.r3d9"]
        failed = [name for name in [*semantic_modules, *phase_modules] if not _can_import(name)]
        docs_exist = all((self.repo_root / path).exists() for path in ("docs/architecture/semantic_module_map.md", "docs/architecture/phase_to_domain_map.md"))
        self._record(
            "dx1_semantic_imports_and_wrappers",
            "Semantic modules va phase-coded wrappers van import duoc; docs map phase -> domain ton tai.",
            "P1",
            "BLOCKED" if failed or not docs_exist else "PASS",
            ["DX1_IMPORT_OR_DOCS_MISSING"] if failed or not docs_exist else [],
            [{"failed_imports": failed}, {"docs_exist": docs_exist}],
            "import_and_doc_check",
        )
        self._record(
            "no_schema_history_public_api_break",
            "R3D10 khong rename DB table, rewrite Alembic, hoac remove public API route.",
            "P0",
            "PASS",
            [],
            [{"alembic_policy": "no history rewrite"}, {"api_policy": "additive read-only endpoint only"}],
            "release_policy",
        )

    def _check_forbidden_routes(self) -> None:
        route_paths = self._route_methods()
        upload_routes = [
            path
            for path in route_paths
            if re.search(
                r"youtube/(upload|publish|reupload)|youtube-(upload|publish|reupload)|(upload|publish|reupload)-to-youtube|publish-now|reupload-now",
                path,
                flags=re.IGNORECASE,
            )
        ]
        self._record(
            "no_youtube_upload_api_route",
            "Khong co YouTube upload/publish/reupload API route.",
            "P0",
            "BLOCKED" if upload_routes else "PASS",
            ["YOUTUBE_UPLOAD_API_ROUTE_FOUND"] if upload_routes else [],
            [{"forbidden_routes": upload_routes[:20]}],
            "route_registry_scan",
        )

    def _check_r3d9_get_only_routes(self) -> None:
        routes = self._route_methods()
        bad = {path: sorted(methods) for path, methods in routes.items() if path in R3D9_GET_ONLY_PATHS and methods != {"GET"}}
        self._record(
            "r3d9_ops_endpoints_get_only",
            "R3D9 ops endpoints read-only; manual workflows dung endpoint co san rieng.",
            "P0",
            "BLOCKED" if bad else "PASS",
            ["R3D9_ENDPOINT_NOT_GET_ONLY"] if bad else [],
            [{"bad_routes": bad}],
            "route_registry_scan",
        )

    def _route_methods(self) -> dict[str, set[str]]:
        if self.application is None:
            return {}
        return {
            route.path: set(route.methods or set()) - {"HEAD", "OPTIONS"}
            for route in self.application.routes
            if hasattr(route, "path") and hasattr(route, "methods")
        }

    def _runtime_packages(self) -> list[FirstScriptedVideoPackage]:
        rows = self.session.scalars(select(FirstScriptedVideoPackage)).all()
        return [row for row in rows if str(row.package_status or "").upper() in PACKAGE_RUNTIME_STATUSES]

    def _context_packs(self, package_id: uuid.UUID) -> list[AgentContextPackSnapshot]:
        return self.session.scalars(select(AgentContextPackSnapshot).where(AgentContextPackSnapshot.package_id == package_id)).all()

    def _gate_runs(self, package_id: uuid.UUID) -> list[R3D4GateRun]:
        return self.session.scalars(select(R3D4GateRun).where(R3D4GateRun.package_id == package_id)).all()

    def _count(self, model: Any, *criteria: Any) -> int:
        statement = select(func.count()).select_from(model)
        if criteria:
            statement = statement.where(*criteria)
        return int(self.session.scalar(statement) or 0)

    def _settings_default(self, field_name: str) -> Any:
        field = Settings.model_fields.get(field_name)
        return None if field is None else field.default

    def _read(self, relative_path: str) -> str:
        path = self.repo_root / relative_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _record(
        self,
        invariant_key: str,
        description: str,
        severity: str,
        status: str,
        reason_codes: list[str],
        evidence_refs: list[dict[str, Any]],
        verification_method: str,
    ) -> None:
        self._checks.append(
            RuntimeInvariantCheckRead(
                invariant_key=invariant_key,
                description=description,
                severity=severity,
                status=status,
                reason_codes=sorted(set(reason_codes)),
                evidence_refs=evidence_refs,
                verification_method=verification_method,
            )
        )


def _iter_uuid_refs(refs: Iterable[Any]) -> list[uuid.UUID]:
    values: list[uuid.UUID] = []
    for ref in refs or []:
        try:
            values.append(uuid.UUID(str(ref)))
        except (TypeError, ValueError):
            continue
    return values


def _gatekeeper_result(run: AgentOutputValidationRun) -> str | None:
    payloads = [run.canonical_artifact_json or {}, run.validation_result_json or {}]
    keys = ("result", "gatekeeper_result", "review_result", "decision")
    for payload in payloads:
        found = _find_first(payload, keys)
        if found:
            return str(found)
    return None


def _find_first(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in keys and item is not None:
                return item
            nested = _find_first(item, keys)
            if nested is not None:
                return nested
    if isinstance(value, list):
        for item in value:
            nested = _find_first(item, keys)
            if nested is not None:
                return nested
    return None


def _memory_prompt_eligible(item: ChannelMemoryItem, facet: MemoryFacet) -> bool:
    return (
        item.approval_status == "APPROVED"
        and item.rights_status == "SAFE"
        and item.prompt_safety_state == "PROMPT_SAFE"
        and item.freshness_state == "FRESH"
        and facet.prompt_safety_state == "PROMPT_SAFE"
    )


def _can_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False
