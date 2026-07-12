from __future__ import annotations

# Compatibility note: semantic facade `output_validation_gates` re-exports this implementation; phase-coded import kept for reports/tests/backward compatibility.
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    AgentOutputValidationRun,
    EffectiveChannelRuntimeContextSnapshot,
    R3D4GateBatchRun,
    R3D4GateRun,
    SchemaViolationLedger,
)
from app.services.r3d3 import canonical_json, stable_hash


GATE_PASS = "PASS"
GATE_REVIEW = "REVIEW_REQUIRED"
GATE_BLOCK = "BLOCK"
GATE_SKIPPED = "SKIPPED_NOT_APPLICABLE"

SEVERITY_INFO = "INFO"
SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

R3D4_SCHEMA_VERSION = "r3d4.agent_output.v1"
R3D4_GATE_VERSION = "r3d4.gates.v1"


@dataclass(frozen=True)
class AgentOutputContract:
    agent_key: str
    artifact_type: str
    output_type: str
    schema_version: str
    criticality: str
    required_artifact_fields: tuple[str, ...] = ()
    required_context_refs: tuple[str, ...] = (
        "effective_context_snapshot_id",
        "compiled_policy_snapshot_id",
        "channel_contract_hash",
        "prompt_context_hash",
        "relevant_contract_paths_used",
    )


DEFAULT_OUTPUT_CONTRACTS: dict[str, AgentOutputContract] = {
    "ChannelAuthorityAgent": AgentOutputContract(
        "ChannelAuthorityAgent",
        "admission_decision",
        "channel_authority",
        R3D4_SCHEMA_VERSION,
        "PACKAGE_CRITICAL",
        ("decision",),
    ),
    "TopicIdeaScoringAgent": AgentOutputContract("TopicIdeaScoringAgent", "topic_scores", "topic_scoring", R3D4_SCHEMA_VERSION, "REVIEWABLE"),
    "ResearchPackSummarizer": AgentOutputContract("ResearchPackSummarizer", "research_notes", "research_summary", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL"),
    "ScriptPlanningAgent": AgentOutputContract("ScriptPlanningAgent", "script_outline", "script_plan", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL"),
    "ScriptWriterAgent": AgentOutputContract("ScriptWriterAgent", "narration_script", "script", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL", ("sentences",)),
    "ScriptRewriteAgent": AgentOutputContract("ScriptRewriteAgent", "narration_script", "script_rewrite", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL", ("sentences",)),
    "PublishingMetadataAgent": AgentOutputContract("PublishingMetadataAgent", "metadata_package", "metadata", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL"),
    "VisualPlanningAgent": AgentOutputContract("VisualPlanningAgent", "visual_plan", "visual_plan", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL", ("scenes",)),
    "ThumbnailBriefAgent": AgentOutputContract("ThumbnailBriefAgent", "thumbnail_brief", "thumbnail_brief", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL"),
    "RightsDisclosureReviewer": AgentOutputContract(
        "RightsDisclosureReviewer",
        "rights_disclosure_review",
        "rights_disclosure_review",
        R3D4_SCHEMA_VERSION,
        "PACKAGE_CRITICAL",
        ("result", "source_manifest_status", "ai_disclosure_needed", "rights_risk"),
    ),
    "GatekeeperSoftReviewAgent": AgentOutputContract("GatekeeperSoftReviewAgent", "gatekeeper_review", "soft_gatekeeper_review", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL"),
    "UploadCardCopyAgent": AgentOutputContract("UploadCardCopyAgent", "upload_card_copy", "upload_copy", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL"),
    "ProviderReadinessSummaryAgent": AgentOutputContract("ProviderReadinessSummaryAgent", "provider_readiness_summary", "provider_readiness_summary", R3D4_SCHEMA_VERSION, "REVIEWABLE"),
    "MediaQCExplanationAgent": AgentOutputContract("MediaQCExplanationAgent", "media_qc_explanation", "media_qc_explanation", R3D4_SCHEMA_VERSION, "PACKAGE_CRITICAL", ("status",)),
}


class AgentOutputContractRegistry:
    def __init__(self, contracts: dict[str, AgentOutputContract] | None = None):
        self.contracts = contracts or DEFAULT_OUTPUT_CONTRACTS

    def resolve(self, agent_key: str) -> AgentOutputContract:
        return self.contracts.get(agent_key) or AgentOutputContract(
            agent_key=agent_key,
            artifact_type=agent_key,
            output_type="generic_agent_output",
            schema_version=R3D4_SCHEMA_VERSION,
            criticality="REVIEWABLE",
        )


@dataclass(frozen=True)
class StrictRepairResult:
    repaired_output: dict[str, Any] | None
    repair_attempted: bool
    repair_result: dict[str, Any]


class StrictRepairService:
    def repair(self, raw_output: Any, *, agent_key: str) -> StrictRepairResult:
        if not isinstance(raw_output, dict):
            return StrictRepairResult(
                repaired_output=None,
                repair_attempted=True,
                repair_result={"status": "FAILED", "reason_codes": ["STRICT_REPAIR_JSON_OBJECT_REQUIRED"]},
            )
        repaired = dict(raw_output)
        changed = False
        if repaired.get("agent_key") in (None, ""):
            repaired["agent_key"] = agent_key
            changed = True
        if "artifact" not in repaired:
            repaired["artifact"] = {}
            changed = True
        return StrictRepairResult(
            repaired_output=repaired,
            repair_attempted=changed,
            repair_result={
                "status": "REPAIRED" if changed else "NOT_NEEDED",
                "repair_scope": "schema_envelope_only",
                "semantic_change_allowed": False,
                "max_attempts": 1,
            },
        )


@dataclass(frozen=True)
class AgentOutputEnvelopeValidation:
    status: str
    validation_state: str
    missing_fields: list[str]
    invalid_fields: list[str]
    reason_codes: list[str]


@dataclass(frozen=True)
class CanonicalArtifactResult:
    canonical_artifact: dict[str, Any]
    envelope_validation: AgentOutputEnvelopeValidation
    applied_context_refs: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    raw_output_hash: str
    output_hash: str
    artifact_hash: str
    raw_output_ref: str | None


class ArtifactCanonicalizer:
    def canonicalize(
        self,
        *,
        contract: AgentOutputContract,
        raw_output: Any,
        parsed_output: dict[str, Any] | None,
        runtime_context_refs: dict[str, Any],
        raw_output_ref: str | None = None,
    ) -> CanonicalArtifactResult:
        raw_hash = stable_hash({"raw_output": raw_output})
        output = parsed_output if isinstance(parsed_output, dict) else {}
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        artifact = dict(artifact)
        artifact, artifact_repair_reason_codes = _repair_required_artifact_aliases(contract, artifact)
        output_hash = stable_hash({"agent_key": contract.agent_key, "output": output})
        base_artifact_hash = stable_hash({"artifact_type": contract.artifact_type, "artifact": artifact})
        evidence_refs = [item for item in _list(output.get("evidence_refs")) if isinstance(item, dict)]
        applied_context_refs = _dict(output.get("applied_context_refs"))
        if not applied_context_refs:
            applied_context_refs = _dict(_dict(output.get("technical_appendix")).get("applied_context_refs"))
        if not applied_context_refs:
            applied_context_refs = {key: value for key, value in runtime_context_refs.items() if value not in (None, "", [])}

        missing_context = [field for field in contract.required_context_refs if not applied_context_refs.get(field)]
        missing_artifact = [field for field in contract.required_artifact_fields if not _field_present(artifact, field)]
        invalid_fields: list[str] = []
        if output.get("agent_key") not in (None, contract.agent_key):
            invalid_fields.append("agent_key")
        if output.get("status") not in {None, "OK", "REVIEW_REQUIRED", "BLOCK", "REFUSAL", "ERROR"}:
            invalid_fields.append("status")

        reason_codes: list[str] = []
        if missing_context:
            reason_codes.append("APPLIED_CONTEXT_REFS_MISSING")
        if missing_artifact:
            reason_codes.append("REQUIRED_ARTIFACT_FIELDS_MISSING")
        if invalid_fields:
            reason_codes.append("AGENT_OUTPUT_INVALID_FIELDS")
        validation_reason_codes = sorted(
            set((reason_codes or ["AGENT_OUTPUT_CANONICALIZED"]) + artifact_repair_reason_codes)
        )

        if missing_context and contract.criticality == "PACKAGE_CRITICAL":
            status = "BLOCK"
            validation_state = "BLOCKED"
        elif missing_context or missing_artifact or invalid_fields:
            status = "REVIEW_REQUIRED"
            validation_state = "REVIEW_REQUIRED"
        else:
            status = "OK"
            validation_state = "CANONICALIZED" if "applied_context_refs" not in output else "VALID"

        canonical = {
            **artifact,
            "agent_key": contract.agent_key,
            "artifact_type": contract.artifact_type,
            "output_type": contract.output_type,
            "schema_version": contract.schema_version,
            "output_status": output.get("status"),
            "applied_context_refs": applied_context_refs,
            "evidence_refs": evidence_refs,
            "raw_output_ref": raw_output_ref,
            "raw_output_hash": raw_hash,
            "output_hash": output_hash,
            "artifact_hash": base_artifact_hash,
            "validation_state": validation_state,
            "reason_codes": sorted(set(reason_codes + artifact_repair_reason_codes + _strings(artifact.get("reason_codes")))),
        }
        return CanonicalArtifactResult(
            canonical_artifact=canonical,
            envelope_validation=AgentOutputEnvelopeValidation(
                status=status,
                validation_state=validation_state,
                missing_fields=[*(f"applied_context_refs.{field}" for field in missing_context), *missing_artifact],
                invalid_fields=invalid_fields,
                reason_codes=validation_reason_codes,
            ),
            applied_context_refs=applied_context_refs,
            evidence_refs=evidence_refs,
            raw_output_hash=raw_hash,
            output_hash=output_hash,
            artifact_hash=base_artifact_hash,
            raw_output_ref=raw_output_ref,
        )


def _repair_required_artifact_aliases(
    contract: AgentOutputContract,
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    if contract.agent_key == "MediaQCExplanationAgent" and not _field_present(artifact, "status"):
        for alias_key in ("artifact_status", "artifact.status"):
            alias_value = artifact.get(alias_key)
            if isinstance(alias_value, str) and alias_value.strip():
                return {**artifact, "status": alias_value}, ["MEDIA_QC_ARTIFACT_STATUS_ALIAS_REPAIRED"]
    return artifact, []


@dataclass(frozen=True)
class AgentOutputValidationResult:
    status: str
    canonical_artifact: dict[str, Any] | None
    validation_run: AgentOutputValidationRun
    blocking_report: dict[str, Any] | None
    reason_codes: list[str]


class AgentOutputValidationService:
    def __init__(
        self,
        session: Session,
        *,
        contract_registry: AgentOutputContractRegistry | None = None,
        canonicalizer: ArtifactCanonicalizer | None = None,
    ):
        self.session = session
        self.contract_registry = contract_registry or AgentOutputContractRegistry()
        self.canonicalizer = canonicalizer or ArtifactCanonicalizer()

    def validate(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        agent_key: str,
        raw_output: Any,
        parsed_output: dict[str, Any] | None,
        prompt_validation_result: dict[str, Any],
        runtime_context_refs: dict[str, Any],
        prompt_render_run_id: uuid.UUID | None,
        agent_context_pack_snapshot_id: uuid.UUID | None,
        raw_output_ref: str | None = None,
    ) -> AgentOutputValidationResult:
        contract = self.contract_registry.resolve(agent_key)
        canonical = self.canonicalizer.canonicalize(
            contract=contract,
            raw_output=raw_output,
            parsed_output=parsed_output,
            runtime_context_refs=runtime_context_refs,
            raw_output_ref=raw_output_ref,
        )
        validation = canonical.envelope_validation
        validation_result = {
            "prompt_validation_result": prompt_validation_result,
            "agent_output_contract": {
                "agent_key": contract.agent_key,
                "artifact_type": contract.artifact_type,
                "criticality": contract.criticality,
                "schema_version": contract.schema_version,
            },
            "missing_fields": validation.missing_fields,
            "invalid_fields": validation.invalid_fields,
        }
        run = AgentOutputValidationRun(
            package_id=package_id,
            video_project_id=video_project_id,
            prompt_render_run_id=prompt_render_run_id,
            agent_context_pack_snapshot_id=agent_context_pack_snapshot_id,
            agent_key=agent_key,
            artifact_type=contract.artifact_type,
            output_type=contract.output_type,
            schema_version=contract.schema_version,
            status=validation.status,
            validation_state=validation.validation_state,
            reason_codes=validation.reason_codes,
            applied_context_refs_json=canonical.applied_context_refs,
            evidence_refs_json=canonical.evidence_refs,
            raw_output_ref=canonical.raw_output_ref,
            raw_output_hash=canonical.raw_output_hash,
            output_hash=canonical.output_hash,
            artifact_hash=canonical.artifact_hash,
            canonical_artifact_json=canonical.canonical_artifact,
            validation_result_json=validation_result,
        )
        self.session.add(run)
        self.session.flush()
        if validation.missing_fields or validation.invalid_fields:
            self._record_violation(
                package_id=package_id,
                video_project_id=video_project_id,
                prompt_render_run_id=prompt_render_run_id,
                agent_key=agent_key,
                artifact_ref=f"first_scripted_video_package:{package_id}:artifacts.{contract.artifact_type}",
                validation=validation,
            )
        blocking_report = None
        if validation.status != "OK":
            blocking_report = {
                "status": validation.status,
                "reason_codes": validation.reason_codes,
                "missing_fields": validation.missing_fields,
                "invalid_fields": validation.invalid_fields,
                "agent_output_validation_run_id": str(run.id),
            }
        return AgentOutputValidationResult(
            status=validation.status,
            canonical_artifact=canonical.canonical_artifact,
            validation_run=run,
            blocking_report=blocking_report,
            reason_codes=validation.reason_codes,
        )

    def _record_violation(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        prompt_render_run_id: uuid.UUID | None,
        agent_key: str,
        artifact_ref: str,
        validation: AgentOutputEnvelopeValidation,
    ) -> None:
        ledger = SchemaViolationLedger(
            package_id=package_id,
            video_project_id=video_project_id,
            prompt_render_run_id=prompt_render_run_id,
            agent_key=agent_key,
            artifact_ref=artifact_ref,
            violation_type="AGENT_OUTPUT_CONTRACT",
            severity=SEVERITY_CRITICAL if validation.status == "BLOCK" else SEVERITY_HIGH,
            missing_fields=validation.missing_fields,
            invalid_fields=validation.invalid_fields,
            repair_attempted=False,
            repair_result={"status": "NOT_ATTEMPTED", "reason": "R3D4 validation does not rewrite business meaning."},
        )
        self.session.add(ledger)
        self.session.flush()


@dataclass(frozen=True)
class GateResult:
    gate_key: str
    status: str
    severity: str
    measurements_json: dict[str, Any]
    fail_codes: list[str]
    blocking_refs: list[dict[str, Any]]
    checked_artifact_refs: list[dict[str, Any]]
    checked_contract_paths: list[str]
    repair_hint: str | None
    human_readable_summary: str
    evidence_refs: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_key": self.gate_key,
            "status": self.status,
            "severity": self.severity,
            "measurements_json": self.measurements_json,
            "fail_codes": self.fail_codes,
            "blocking_refs": self.blocking_refs,
            "checked_artifact_refs": self.checked_artifact_refs,
            "checked_contract_paths": self.checked_contract_paths,
            "repair_hint": self.repair_hint,
            "human_readable_summary": self.human_readable_summary,
            "evidence_refs": self.evidence_refs or [],
        }


@dataclass(frozen=True)
class GateBatchResult:
    package_id: uuid.UUID
    video_project_id: uuid.UUID | None
    effective_context_snapshot_id: uuid.UUID | None
    status: str
    gate_results: list[GateResult]
    hard_block_count: int
    review_required_count: int
    context_hash: str | None
    gate_batch_run_id: uuid.UUID | None = None

    @property
    def fail_codes(self) -> list[str]:
        codes: list[str] = []
        for result in self.gate_results:
            codes.extend(result.fail_codes)
        return sorted(set(codes))

    def to_report(self) -> dict[str, Any]:
        return {
            "gate_batch_run_id": str(self.gate_batch_run_id) if self.gate_batch_run_id else None,
            "status": self.status,
            "hard_block_count": self.hard_block_count,
            "review_required_count": self.review_required_count,
            "fail_codes": self.fail_codes,
            "gate_results": [result.to_dict() for result in self.gate_results],
        }


class ScriptDurationGate:
    gate_key = "script_duration_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        script = _dict(artifacts.get("narration_script"))
        sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
        missing_timing = [str(item.get("sentence_id") or index + 1) for index, item in enumerate(sentences) if item.get("approx_seconds") is None]
        actual = sum(_float(item.get("approx_seconds")) for item in sentences)
        policy = _duration_policy(effective_context, artifacts=artifacts)
        target = _float(policy.get("target_duration_seconds") or policy.get("target_seconds"))
        allowed_range = _dict(policy.get("allowed_duration_range_seconds"))
        min_seconds = _float(
            allowed_range.get("min")
            or policy.get("min_seconds")
            or policy.get("target_duration_seconds_min")
            or policy.get("long_form_min_seconds")
            or (target * 0.9 if target else 0)
        )
        max_seconds = _float(
            allowed_range.get("max")
            or policy.get("max_seconds")
            or policy.get("target_duration_seconds_max")
            or (target * 1.1 if target else 0)
        )
        declared = _first_number(script.get("total_approx_seconds"), script.get("declared_total_seconds"), _dict(script.get("summary")).get("total_approx_seconds"))
        self_check = _dict(script.get("duration_self_check"))
        self_check_total = _first_number(self_check.get("actual_total_seconds"), self_check.get("total_approx_seconds"))
        word_count = _word_count(" ".join(str(item.get("text") or "") for item in sentences))
        wpm = _float(policy.get("words_per_minute_assumption"))
        words_target = _float(policy.get("narration_words_target"))
        min_words = int(words_target * 0.9) if words_target else 0
        sentence_approx_total = actual
        word_estimated_seconds = round((word_count / wpm) * 60, 3) if wpm and word_count else None
        actual = word_estimated_seconds if word_estimated_seconds is not None else sentence_approx_total
        coverage_ratio = round(actual / target, 4) if target else None
        fail_codes: list[str] = []
        measurements = {
            "sentence_count": len(sentences),
            "actual_total_seconds": actual,
            "sentence_approx_total_seconds": sentence_approx_total,
            "word_estimated_total_seconds": word_estimated_seconds,
            "declared_total_seconds": declared,
            "duration_self_check_total_seconds": self_check_total,
            "target_seconds": target,
            "delta_seconds": round(actual - target, 3) if target else None,
            "coverage_ratio": coverage_ratio,
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "narration_word_count": word_count,
            "words_per_minute_assumption": wpm or policy.get("words_per_minute_assumption"),
            "narration_words_target": policy.get("narration_words_target"),
            "minimum_word_count": min_words,
            "target_format": policy.get("target_format"),
            "missing_timing_sentence_ids": missing_timing,
        }
        if not sentences or missing_timing:
            fail_codes.append("SCRIPT_SENTENCE_TIMING_MISSING")
        if target <= 0:
            fail_codes.append("SCRIPT_DURATION_TARGET_MISSING")
        if min_seconds and actual < min_seconds:
            fail_codes.append("SCRIPT_DURATION_BELOW_MINIMUM")
        if max_seconds and actual > max_seconds:
            fail_codes.append("SCRIPT_DURATION_ABOVE_MAXIMUM")
        if self_check_total is not None and abs(self_check_total - actual) > max(10, actual * 0.1):
            fail_codes.append("SCRIPT_DURATION_SELF_CHECK_MISMATCH")
        if min_words and word_count < min_words:
            fail_codes.append("SCRIPT_WORD_BUDGET_BELOW_MINIMUM")
        if declared is not None and abs(declared - actual) > max(10, actual * 0.1):
            fail_codes.append("SCRIPT_DECLARED_DURATION_MISMATCH")
        metadata_duration = _first_number(_dict(artifacts.get("metadata_package")).get("duration_seconds"))
        if metadata_duration is not None and abs(metadata_duration - actual) > max(10, actual * 0.1):
            fail_codes.append("METADATA_DURATION_MISMATCH")
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, fail_codes, ["narration_script"], ["category.default_format_policy_json"], "Sửa timing script theo duration policy.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["narration_script"], ["category.default_format_policy_json"], None)


class HookSpecGate:
    gate_key = "hook_3s_gate"
    REQUIRED_FIELDS = (
        "hook_type",
        "first_3_seconds_script",
        "first_3_seconds_visual",
        "promise_made",
        "payoff_location",
        "clickbait_risk",
        "visual_hook_relevance",
        "title_hook_alignment",
    )

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        hook = _hook_spec(artifacts)
        missing = [field for field in self.REQUIRED_FIELDS if hook.get(field) in (None, "", [])]
        fail_codes: list[str] = [f"HOOK_{field.upper()}_MISSING" for field in missing]
        text = _text_blob(hook)
        fake_claim_terms = ["fake demo", "product demo", "actual result", "real result", "generated video is ready", "final video"]
        fake_claims = [term for term in fake_claim_terms if term in text.lower()]
        if fake_claims:
            fail_codes.append("HOOK_FAKE_RESULT_OR_ASSET_CLAIM")
        clickbait_risk = str(hook.get("clickbait_risk") or "").upper()
        if clickbait_risk == "HIGH":
            fail_codes.append("HOOK_CLICKBAIT_RISK_HIGH")
        status = GATE_REVIEW if fail_codes == ["HOOK_CLICKBAIT_RISK_HIGH"] else (GATE_BLOCK if fail_codes else GATE_PASS)
        severity = SEVERITY_HIGH if status != GATE_PASS else SEVERITY_INFO
        measurements = {
            "hook_fields_present": sorted(field for field in self.REQUIRED_FIELDS if field not in missing),
            "missing_hook_fields": missing,
            "clickbait_risk": clickbait_risk or None,
            "fake_claim_terms": fake_claims,
        }
        return _gate_result(
            self.gate_key,
            status,
            severity,
            measurements,
            sorted(set(fail_codes)),
            ["hook_spec", "narration_script", "script_outline"],
            ["script_contract.hook_spec", "safety_forbidden_claims_context"],
            "Bổ sung HookSpec 3 giây đầu trước visual/provider plan." if fail_codes else None,
        )


class SRTTimingGate:
    gate_key = "srt_timing_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        if not srt:
            return _gate_result(self.gate_key, GATE_SKIPPED, SEVERITY_INFO, {"srt_present": False}, [], [], ["subtitle_lifecycle"], None)
        content = str(srt.get("srt") or srt.get("content") or "")
        lifecycle = str(srt.get("lifecycle_state") or "DRAFT_SCRIPT_TIMING")
        fail_codes: list[str] = []
        cue_measurements = _parse_srt(content)
        fail_codes.extend(cue_measurements["fail_codes"])
        duration_model = _dict(artifacts.get("duration_model"))
        allowed_range = _dict(duration_model.get("allowed_duration_range_seconds"))
        min_seconds = _first_number(allowed_range.get("min"), duration_model.get("min_seconds"))
        max_seconds = _first_number(allowed_range.get("max"), duration_model.get("max_seconds"))
        narration_total = _script_estimated_total_seconds(_dict(artifacts.get("narration_script")), duration_model)
        if cue_measurements["total_seconds"] and narration_total and abs(cue_measurements["total_seconds"] - narration_total) > max(2, narration_total * 0.01):
            fail_codes.append("PRECHECK_BLOCKED_SRT_DURATION_MISMATCH")
        if min_seconds and cue_measurements["total_seconds"] and cue_measurements["total_seconds"] < min_seconds:
            fail_codes.append("PRECHECK_BLOCKED_SRT_DURATION_BELOW_RANGE")
        if max_seconds and cue_measurements["total_seconds"] and cue_measurements["total_seconds"] > max_seconds:
            fail_codes.append("PRECHECK_BLOCKED_SRT_DURATION_ABOVE_RANGE")
        if cue_measurements["large_gap_count"]:
            fail_codes.append("PRECHECK_BLOCKED_SRT_GAP_TOO_LARGE")
        if lifecycle == "DRAFT_SCRIPT_TIMING" and srt.get("final") is True:
            fail_codes.append("DRAFT_SRT_MARKED_FINAL")
        if lifecycle == "FINAL_APPROVED" and not (srt.get("voice_alignment_evidence_ref") and srt.get("human_approved_at")):
            fail_codes.append("FINAL_SRT_REQUIRES_VOICE_ALIGNMENT_AND_HUMAN_APPROVAL")
        measurements = {
            "lifecycle_state": lifecycle,
            "requires_voice_alignment": lifecycle == "DRAFT_SCRIPT_TIMING",
            "cue_count": cue_measurements["cue_count"],
            "srt_total_seconds": cue_measurements["total_seconds"],
            "narration_total_seconds": narration_total,
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "large_gap_count": cue_measurements["large_gap_count"],
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_HIGH, measurements, fail_codes, ["srt"], ["subtitle_lifecycle"], "Sửa SRT timing/lifecycle trước khi final.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_LOW, measurements, [], ["srt"], ["subtitle_lifecycle"], None)


class SRTFormatGate:
    gate_key = "srt_format_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        if not srt:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, {"srt_present": False}, ["PRECHECK_BLOCKED_SRT_MISSING"], ["srt"], ["subtitle_lifecycle"], "Tạo SRT artifact trước provider boundary.")
        content = str(srt.get("srt") or srt.get("content") or "")
        parsed = _parse_srt(content)
        fail_codes = list(parsed["fail_codes"])
        if parsed["cue_count"] <= 0:
            fail_codes.append("PRECHECK_BLOCKED_SRT_EMPTY")
        if parsed["cues"] and parsed["cues"][0]["start_seconds"] != 0:
            fail_codes.append("PRECHECK_BLOCKED_SRT_START_NOT_ZERO")
        if not all(cue["text"].strip() for cue in parsed["cues"]):
            fail_codes.append("PRECHECK_BLOCKED_SRT_EMPTY_TEXT")
        try:
            content.encode("utf-8").decode("utf-8")
        except UnicodeError:
            fail_codes.append("PRECHECK_BLOCKED_SRT_UTF8_INVALID")
        measurements = {
            "cue_count": parsed["cue_count"],
            "starts_at_zero": bool(parsed["cues"] and parsed["cues"][0]["start_seconds"] == 0),
            "artifact_type": srt.get("artifact_type"),
            "local_path": srt.get("local_path"),
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, sorted(set(fail_codes)), ["srt"], ["subtitle_lifecycle"], "Sửa format SRT deterministic.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["srt"], ["subtitle_lifecycle"], None)


class CaptionCoverageGate:
    gate_key = "caption_coverage_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        script = _dict(artifacts.get("narration_script"))
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
        sentence_ids = [str(item.get("sentence_id") or item.get("id") or index + 1) for index, item in enumerate(sentences)]
        cues = [item for item in _list(srt.get("cues")) if isinstance(item, dict)]
        covered_ids = sorted({str(ref) for cue in cues for ref in _strings(cue.get("sentence_ids") or cue.get("sentence_id"))})
        missing = sorted(set(sentence_ids) - set(covered_ids))
        script_tokens = _normalized_tokens(" ".join(str(item.get("text") or "") for item in sentences))
        srt_text = str(srt.get("srt") or srt.get("content") or "")
        srt_tokens = _normalized_tokens(_srt_text_only(srt_text))
        coverage_percent = round((len(set(sentence_ids) - set(missing)) / len(sentence_ids)) * 100, 3) if sentence_ids else 0.0
        fail_codes: list[str] = []
        if missing:
            fail_codes.append("PRECHECK_BLOCKED_SRT_SENTENCE_COVERAGE_INCOMPLETE")
        if coverage_percent < 100:
            fail_codes.append("PRECHECK_BLOCKED_SRT_COVERAGE_BELOW_THRESHOLD")
        if script_tokens != srt_tokens:
            fail_codes.append("PRECHECK_BLOCKED_SRT_TEXT_COVERAGE_MISMATCH")
        measurements = {
            "script_sentence_count": len(sentence_ids),
            "covered_sentence_count": len(covered_ids),
            "coverage_percent": coverage_percent,
            "missing_sentence_ids": missing[:20],
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, sorted(set(fail_codes)), ["narration_script", "srt"], ["script_contract", "subtitle_lifecycle"], "Đồng bộ coverage script -> SRT.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["narration_script", "srt"], ["script_contract", "subtitle_lifecycle"], None)


class CaptionReadabilityGate:
    gate_key = "caption_readability_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        parsed = _parse_srt(str(srt.get("srt") or srt.get("content") or ""))
        too_long_lines: list[dict[str, Any]] = []
        too_dense: list[int] = []
        bad_duration: list[int] = []
        too_many_lines: list[int] = []
        for cue in parsed["cues"]:
            duration = cue["end_seconds"] - cue["start_seconds"]
            lines = cue["text_lines"]
            if duration < 1.0 or duration > 7.0:
                bad_duration.append(cue["index"])
            if len(lines) > 2:
                too_many_lines.append(cue["index"])
            for line in lines:
                if len(line) > 42:
                    too_long_lines.append({"index": cue["index"], "line_length": len(line)})
            if duration > 0 and _word_count(cue["text"]) / duration > 3.8:
                too_dense.append(cue["index"])
        fail_codes: list[str] = []
        if bad_duration:
            fail_codes.append("PRECHECK_BLOCKED_SRT_CAPTION_DURATION_INVALID")
        if too_many_lines:
            fail_codes.append("PRECHECK_BLOCKED_SRT_TOO_MANY_LINES")
        if too_long_lines:
            fail_codes.append("PRECHECK_BLOCKED_SRT_LINE_TOO_LONG")
        if too_dense:
            fail_codes.append("PRECHECK_BLOCKED_SRT_CAPTION_TOO_DENSE")
        measurements = {
            "bad_duration_caption_indexes": bad_duration[:20],
            "too_many_line_caption_indexes": too_many_lines[:20],
            "too_long_line_count": len(too_long_lines),
            "too_dense_caption_indexes": too_dense[:20],
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_HIGH, measurements, sorted(set(fail_codes)), ["srt"], ["subtitle_readability"], "Sửa readability của caption.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["srt"], ["subtitle_readability"], None)


class ScriptToSRTConsistencyGate:
    gate_key = "script_to_srt_consistency_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        script = _dict(artifacts.get("narration_script"))
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        script_text = " ".join(str(item.get("text") or "") for item in _list(script.get("sentences")) if isinstance(item, dict))
        srt_text = _srt_text_only(str(srt.get("srt") or srt.get("content") or ""))
        script_tokens = _normalized_tokens(script_text)
        srt_tokens = _normalized_tokens(srt_text)
        fail_codes: list[str] = []
        if script_tokens != srt_tokens:
            fail_codes.append("PRECHECK_BLOCKED_SRT_NOT_DERIVED_FROM_SCRIPT")
        measurements = {
            "script_word_count": len(script_tokens),
            "srt_word_count": len(srt_tokens),
            "token_delta": len(srt_tokens) - len(script_tokens),
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, sorted(set(fail_codes)), ["narration_script", "srt"], ["script_contract.hook_spec"], "SRT phải derive từ script, không thêm claims.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["narration_script", "srt"], ["script_contract.hook_spec"], None)


class HookCaptionGate:
    gate_key = "hook_caption_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        hook = _hook_spec(artifacts)
        script = _dict(artifacts.get("narration_script"))
        parsed = _parse_srt(str(srt.get("srt") or srt.get("content") or ""))
        first_window = " ".join(cue["text"] for cue in parsed["cues"] if cue["start_seconds"] < 3.0)
        hook_words = {token for token in _normalized_tokens(str(hook.get("first_3_seconds_script") or hook.get("promise_made") or "")) if len(token) > 3}
        caption_words = set(_normalized_tokens(first_window))
        overlap = sorted(hook_words & caption_words)
        first_sentence = str(_dict(_first([item for item in _list(script.get("sentences")) if isinstance(item, dict)])).get("text") or "")
        derived_from_opening = bool(first_window and _normalized_tokens(first_window) == _normalized_tokens(first_sentence)[: len(_normalized_tokens(first_window))])
        overpromise_terms = ["actual result", "real result", "final video", "uploaded", "published", "guaranteed"]
        overpromise_matches = [term for term in overpromise_terms if term in first_window.lower()]
        fail_codes: list[str] = []
        if not first_window.strip():
            fail_codes.append("PRECHECK_BLOCKED_SRT_HOOK_CAPTION_MISSING")
        if hook_words and not overlap and not derived_from_opening:
            fail_codes.append("PRECHECK_BLOCKED_SRT_HOOK_CAPTION_MISMATCH")
        if overpromise_matches:
            fail_codes.append("PRECHECK_BLOCKED_SRT_HOOK_CAPTION_OVERPROMISE")
        measurements = {
            "first_3_seconds_caption": first_window,
            "hook_overlap_terms": overlap[:10],
            "derived_from_opening_narration": derived_from_opening,
            "overpromise_matches": overpromise_matches,
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_HIGH, measurements, sorted(set(fail_codes)), ["hook_spec", "srt"], ["script_contract.hook_spec"], "Caption 3 giây đầu phải hỗ trợ hook promise.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["hook_spec", "srt"], ["script_contract.hook_spec"], None)


class VisualSRTTimelineGate:
    gate_key = "visual_srt_timeline_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        visual = _dict(artifacts.get("visual_plan"))
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package") or artifacts.get("caption_track"))
        scenes = [item for item in _list(visual.get("scenes")) if isinstance(item, dict)]
        cues = [item for item in _list(srt.get("cues")) if isinstance(item, dict)]
        if not visual:
            return _gate_result(self.gate_key, GATE_SKIPPED, SEVERITY_INFO, {"visual_plan_present": False}, [], [], ["visual_plan"], None)
        srt_end = _first_number(srt.get("srt_total_seconds"), srt.get("estimated_total_seconds")) or 0.0
        cue_sentence_ids = {str(ref) for cue in cues for ref in _strings(cue.get("sentence_ids") or cue.get("sentence_id"))}
        covered_sentence_ids: set[str] = set()
        timed_scene_total = 0.0
        invalid_scene_ids: list[str] = []
        scene_count_with_timing = 0
        for index, scene in enumerate(scenes, start=1):
            refs = _strings(
                scene.get("sentence_ids")
                or scene.get("covered_sentence_ids")
                or scene.get("sentence_ids_covered")
                or scene.get("covers_sentence_ids")
                or scene.get("sentence_refs")
                or scene.get("primary_sentence_ids")
                or scene.get("narration_sentence_ids")
                or scene.get("sentence_range")
                or scene.get("sentence_id")
            )
            covered_sentence_ids.update(refs)
            start = _first_number(scene.get("start_seconds"), scene.get("start_time"), scene.get("start"))
            end = _first_number(scene.get("end_seconds"), scene.get("end_time"), scene.get("end"))
            duration = _first_number(scene.get("duration_seconds"))
            if start is not None or end is not None or duration is not None:
                scene_count_with_timing += 1
                if start is None and end is not None and duration is not None:
                    start = end - duration
                if end is None and start is not None and duration is not None:
                    end = start + duration
                if start is None or end is None or start < 0 or end <= start or (srt_end and end > srt_end + 1.0):
                    invalid_scene_ids.append(str(scene.get("scene_id") or index))
                elif duration is not None:
                    timed_scene_total += duration
                else:
                    timed_scene_total += end - start
        missing_caption_sentence_ids = sorted(cue_sentence_ids - covered_sentence_ids)
        fail_codes: list[str] = []
        if missing_caption_sentence_ids:
            fail_codes.append("VISUAL_COVERAGE_INCOMPLETE")
        if invalid_scene_ids:
            fail_codes.append("VISUAL_SCENE_DURATION_INVALID")
        if scene_count_with_timing and srt_end and abs(timed_scene_total - srt_end) > max(15, srt_end * 0.15):
            fail_codes.append("VISUAL_TIMELINE_MISMATCH")
        if scene_count_with_timing and scenes and cues and len(scenes) >= len(cues) * 0.95:
            fail_codes.append("VISUAL_SCENE_DURATION_INVALID")
        measurements = {
            "scene_count": len(scenes),
            "caption_count": len(cues),
            "srt_total_seconds": srt_end,
            "timed_scene_total_seconds": round(timed_scene_total, 3),
            "scene_count_with_timing": scene_count_with_timing,
            "missing_caption_sentence_ids": missing_caption_sentence_ids[:20],
            "invalid_scene_ids": invalid_scene_ids[:20],
            "timing_mode": "explicit" if scene_count_with_timing else "sentence_id_derived",
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, sorted(set(fail_codes)), ["visual_plan", "srt"], ["visual_plan.scenes", "subtitle_lifecycle"], "Sửa visual/SRT timeline coverage.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["visual_plan", "srt"], ["visual_plan.scenes", "subtitle_lifecycle"], None)


class VisualCoverageGate:
    gate_key = "visual_coverage_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        script = _dict(artifacts.get("narration_script"))
        visual = _dict(artifacts.get("visual_plan"))
        sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
        sentence_ids = {str(item.get("sentence_id")) for item in sentences if item.get("sentence_id")}
        scenes = [item for item in _list(visual.get("scenes")) if isinstance(item, dict)]
        covered: set[str] = set()
        unknown: set[str] = set()
        source_types: set[str] = set()
        scene_duration = 0.0
        for scene in scenes:
            refs = _strings(
                scene.get("sentence_ids")
                or scene.get("covered_sentence_ids")
                or scene.get("sentence_ids_covered")
                or scene.get("covers_sentence_ids")
                or scene.get("sentence_refs")
                or scene.get("primary_sentence_ids")
                or scene.get("narration_sentence_ids")
                or scene.get("sentence_range")
                or scene.get("sentence_id")
            )
            for ref in refs:
                if ref in sentence_ids:
                    covered.add(ref)
                else:
                    unknown.add(ref)
            if scene.get("duration_seconds") is not None:
                scene_duration += _float(scene.get("duration_seconds"))
            source = scene.get("intended_visual_source") or scene.get("source_type")
            if source:
                source_types.add(str(source))
        allowed_sources = set(_strings(_dict(effective_context.visual_style_context_json).get("allowed_visual_sources")))
        if not allowed_sources:
            allowed_sources = {"DIAGRAM", "CARD", "SCREENSHOT", "EXISTING_ASSET"}
        disallowed = sorted(source for source in source_types if source not in allowed_sources)
        missing = sorted(sentence_ids - covered)
        fail_codes: list[str] = []
        if missing:
            fail_codes.append("VISUAL_COVERAGE_MISSING_SENTENCE_IDS")
        if unknown:
            fail_codes.append("VISUAL_PLAN_UNKNOWN_SENTENCE_REFS")
        if disallowed:
            fail_codes.append("VISUAL_SOURCE_DISALLOWED_BY_CONTRACT")
        script_total = _script_total_seconds(script)
        if scene_duration and script_total and abs(scene_duration - script_total) > max(15, script_total * 0.3):
            fail_codes.append("VISUAL_DURATION_LARGE_MISMATCH")
        measurements = {
            "sentence_count": len(sentence_ids),
            "covered_sentence_count": len(covered),
            "missing_sentence_ids": missing,
            "unknown_sentence_refs": sorted(unknown),
            "source_types_used": sorted(source_types),
            "disallowed_source_types": disallowed,
            "script_total_seconds": script_total,
            "scene_duration_seconds": scene_duration,
        }
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, fail_codes, ["visual_plan", "narration_script"], ["visual_style_context.allowed_visual_sources"], "Sửa visual plan coverage/source policy.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["visual_plan", "narration_script"], ["visual_style_context.allowed_visual_sources"], None)


class ArtifactConsistencyGate:
    gate_key = "artifact_consistency_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        script = _dict(artifacts.get("narration_script"))
        metadata = _dict(artifacts.get("metadata_package"))
        duration_model = _dict(artifacts.get("duration_model"))
        fail_codes: list[str] = []
        script_total = _script_estimated_total_seconds(script, duration_model)
        metadata_duration = _first_number(metadata.get("duration_seconds"), metadata.get("total_approx_seconds"))
        if script_total and metadata_duration is not None and abs(script_total - metadata_duration) > max(10, script_total * 0.1):
            fail_codes.append("SCRIPT_METADATA_DURATION_MISMATCH")
        srt = _dict(artifacts.get("srt") or artifacts.get("subtitle_package"))
        if srt:
            srt_total = _parse_srt(str(srt.get("srt") or srt.get("content") or ""))["total_seconds"]
            if script_total and srt_total and abs(script_total - srt_total) > max(2, script_total * 0.05):
                fail_codes.append("SCRIPT_SRT_DURATION_MISMATCH")
        upload = _dict(artifacts.get("upload_card_copy"))
        rights = _dict(artifacts.get("rights_disclosure_review"))
        if rights.get("ai_disclosure_needed") is True and upload and not _text_contains_any(upload, ["disclosure", "ai-assisted", "ai assisted", "AI"]):
            fail_codes.append("UPLOAD_COPY_DISCLOSURE_MISMATCH")
        measurements = {"script_total_seconds": script_total, "metadata_duration_seconds": metadata_duration}
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_HIGH, measurements, fail_codes, ["narration_script", "metadata_package"], ["metadata_seo_policy_context", "source_rights_disclosure_context"], "Đồng bộ duration/disclosure giữa artifacts.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["narration_script", "metadata_package"], ["metadata_seo_policy_context"], None)


class DisclosureConsistencyGate:
    gate_key = "disclosure_consistency_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        metadata = _dict(artifacts.get("metadata_package"))
        upload = _dict(artifacts.get("upload_card_copy"))
        rights = _dict(artifacts.get("rights_disclosure_review"))
        combined = " ".join(str(value) for value in [metadata.get("description"), upload.get("description"), upload.get("title")] if value)
        fail_codes: list[str] = []
        if "ai-generated video is included" in combined.lower() or "media already generated" in combined.lower():
            fail_codes.append("AI_MEDIA_DISCLOSURE_FALSE_PRESENT_TENSE")
        required_blocks = _strings(_dict(effective_context.source_rights_disclosure_context_json).get("required_disclosure_blocks"))
        if required_blocks and not (metadata.get("disclosure_notes") or upload.get("disclosure_refs") or rights.get("disclosure_notes")):
            fail_codes.append("REQUIRED_DISCLOSURE_BLOCK_MISSING")
        if rights.get("ai_disclosure_needed") is True and "future" not in combined.lower() and "planned" not in combined.lower() and not metadata.get("disclosure_notes"):
            fail_codes.append("AI_DISCLOSURE_CONDITIONAL_WORDING_MISSING")
        measurements = {"required_disclosure_blocks": required_blocks, "ai_disclosure_needed": rights.get("ai_disclosure_needed")}
        if fail_codes:
            return _gate_result(self.gate_key, GATE_REVIEW, SEVERITY_HIGH, measurements, fail_codes, ["metadata_package", "upload_card_copy", "rights_disclosure_review"], ["source_rights_disclosure_context"], "Sửa disclosure wording cho đúng trạng thái media.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["metadata_package", "upload_card_copy", "rights_disclosure_review"], ["source_rights_disclosure_context"], None)


class UploadCopyTruthfulnessGate:
    gate_key = "upload_copy_truthfulness_gate"

    def run(self, *, artifacts: dict[str, Any], **_: Any) -> GateResult:
        upload = _dict(artifacts.get("upload_card_copy"))
        metadata = _dict(artifacts.get("metadata_package"))
        text = " ".join(str(value) for value in [upload.get("title"), upload.get("description"), metadata.get("description"), upload.get("cta")] if value).lower()
        fail_codes: list[str] = []
        unsupported = ["free checklist", "download checklist", "product demo", "free demo", "limited time"]
        if any(item in text for item in unsupported) and not (artifacts.get("asset_manifest") or artifacts.get("funnel_manifest")):
            fail_codes.append("UNSUPPORTED_CTA_OR_FREEBIE_CLAIM")
        if "uploaded" in text and not upload.get("not_uploaded"):
            fail_codes.append("UPLOAD_COPY_IMPLIES_AUTOMATED_UPLOAD")
        measurements = {"unsupported_claim_scan": [item for item in unsupported if item in text]}
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, fail_codes, ["upload_card_copy"], ["monetization_cta_context"], "Gỡ CTA/freebie/demo không có manifest thật.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["upload_card_copy"], ["monetization_cta_context"], None)


class ProviderBoundaryGate:
    gate_key = "provider_boundary_gate"

    def run(self, *, artifacts: dict[str, Any], provider_readiness_state: dict[str, Any] | None = None, **_: Any) -> GateResult:
        fail_codes: list[str] = []
        attempts = [item for item in _list(artifacts.get("provider_attempts")) if isinstance(item, dict)]
        forbidden_media = {
            "ELEVENLABS",
            "GOOGLE_VEO",
            "PEXELS_API",
            "GOOGLE_VEO",
            "GOOGLE_DRIVE",
            "YOUTUBE",
        }
        forbidden_attempts = [item for item in attempts if str(item.get("provider_key") or "").upper() in forbidden_media]
        if forbidden_attempts:
            fail_codes.append("FORBIDDEN_PROVIDER_OR_UPLOAD_ATTEMPT")
        if artifacts.get("mock_fallback_used") or _dict(artifacts.get("risk_limitations_summary")).get("mock_fallback_used"):
            fail_codes.append("MOCK_FALLBACK_USED")
        if artifacts.get("dry_run_success_used") or _dict(artifacts.get("risk_limitations_summary")).get("dry_run_success_used"):
            fail_codes.append("DRY_RUN_SUCCESS_USED")
        provider_state = _provider_readiness_map(provider_readiness_state or {})
        missing_required = [
            key
            for key in ("elevenlabs",)
            if provider_state.get(key, {}).get("status") not in {"CONFIGURED", "PASS"}
        ]
        if missing_required:
            fail_codes.extend(f"{key.upper()}_NOT_CONFIGURED" for key in missing_required)
        measurements = {"forbidden_attempt_count": len(forbidden_attempts), "missing_required_providers": missing_required}
        if fail_codes:
            return _gate_result(self.gate_key, GATE_BLOCK, SEVERITY_CRITICAL, measurements, sorted(set(fail_codes)), ["provider_readiness_summary"], ["cost_provider_policy_context", "media_policy"], "Dừng ở provider boundary; không gọi media/upload.")
        return _gate_result(self.gate_key, GATE_PASS, SEVERITY_INFO, measurements, [], ["provider_readiness_summary"], ["cost_provider_policy_context", "media_policy"], None)


class ScriptStyleComplianceGate:
    gate_key = "script_style_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        script = _dict(artifacts.get("narration_script"))
        language = script.get("language") or script.get("content_language")
        expected = _dict(effective_context.market_locale_context_json).get("content_language")
        forbidden = _strings(_dict(effective_context.brand_voice_persona_context_json).get("forbidden_style"))
        text = " ".join(str(item.get("text") or "") for item in _list(script.get("sentences")) if isinstance(item, dict)).lower()
        matched_terms = [term for term in forbidden if term and term.lower() in text]
        fail_codes: list[str] = []
        if language and expected and str(language).lower() != str(expected).lower():
            fail_codes.append("SCRIPT_LANGUAGE_CONTRACT_MISMATCH")
        if matched_terms:
            fail_codes.append("SCRIPT_FORBIDDEN_STYLE_USED")
        status = GATE_BLOCK if fail_codes else GATE_PASS
        return _gate_result(
            self.gate_key,
            status,
            SEVERITY_HIGH if fail_codes else SEVERITY_INFO,
            {"expected_language": expected, "observed_language": language, "forbidden_style_terms_found": matched_terms},
            fail_codes,
            ["narration_script"],
            ["market_locale_context.content_language", "brand_voice_persona_context"],
            "Sửa script language/style theo contract." if fail_codes else None,
        )


class VoiceProfileComplianceGate:
    gate_key = "voice_profile_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        voice = _dict(effective_context.voice_audio_context_json)
        voice_artifact = _dict(artifacts.get("voice_profile") or artifacts.get("voice_timeline"))
        fail_codes: list[str] = []
        if voice_artifact and voice.get("voice_profile_id") and voice_artifact.get("voice_profile_id") != voice.get("voice_profile_id"):
            fail_codes.append("VOICE_PROFILE_MISMATCH")
        character = _dict(effective_context.character_identity_context_json)
        if character.get("character_policy_mode") == "REQUIRED_CHARACTER" and not voice.get("voice_profile_id"):
            fail_codes.append("VOICE_PROFILE_REQUIRED_MISSING")
        status = GATE_BLOCK if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_HIGH if fail_codes else SEVERITY_INFO, {"voice_profile_id": voice.get("voice_profile_id")}, fail_codes, ["voice_profile"], ["voice_audio_context"], "Sửa voice profile binding." if fail_codes else None)


class VisualStyleComplianceGate:
    gate_key = "visual_style_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        base = VisualCoverageGate().run(artifacts=artifacts, effective_context=effective_context)
        return GateResult(
            gate_key=self.gate_key,
            status=base.status,
            severity=base.severity,
            measurements_json=base.measurements_json,
            fail_codes=base.fail_codes,
            blocking_refs=base.blocking_refs,
            checked_artifact_refs=base.checked_artifact_refs,
            checked_contract_paths=base.checked_contract_paths,
            repair_hint=base.repair_hint,
            human_readable_summary=_summary_for(status=base.status, fail_codes=base.fail_codes, gate_key=self.gate_key),
            evidence_refs=base.evidence_refs,
        )


class ThumbnailStyleComplianceGate:
    gate_key = "thumbnail_style_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        thumb = _dict(artifacts.get("thumbnail_brief"))
        expected_language = _dict(effective_context.thumbnail_style_context_json).get("text_overlay_language")
        observed = thumb.get("text_overlay_language") or thumb.get("language")
        text = str(thumb.get("text_overlay") or _dict(_first(_list(thumb.get("variants")))).get("text") or "")
        fail_codes: list[str] = []
        if observed and expected_language and str(observed).lower() != str(expected_language).lower():
            fail_codes.append("THUMBNAIL_LANGUAGE_MISMATCH")
        if len(text) > 80:
            fail_codes.append("THUMBNAIL_TEXT_MOBILE_READABILITY_RISK")
        status = GATE_REVIEW if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_MEDIUM if fail_codes else SEVERITY_INFO, {"expected_language": expected_language, "observed_language": observed, "text_length": len(text)}, fail_codes, ["thumbnail_brief"], ["thumbnail_style_context"], "Sửa thumbnail language/readability." if fail_codes else None)


class MetadataLocaleComplianceGate:
    gate_key = "metadata_locale_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        metadata = _dict(artifacts.get("metadata_package"))
        expected = _dict(effective_context.market_locale_context_json).get("content_language")
        observed = metadata.get("language") or metadata.get("content_language")
        fail_codes = ["METADATA_LANGUAGE_CONTRACT_MISMATCH"] if observed and expected and str(observed).lower() != str(expected).lower() else []
        status = GATE_REVIEW if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_HIGH if fail_codes else SEVERITY_INFO, {"expected_language": expected, "observed_language": observed}, fail_codes, ["metadata_package"], ["market_locale_context.content_language", "metadata_seo_policy_context"], "Sửa metadata locale." if fail_codes else None)


class PublishTimingComplianceGate:
    gate_key = "publish_timing_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        handoff = _dict(artifacts.get("upload_card_copy") or artifacts.get("publish_handoff"))
        timing = _dict(effective_context.publish_timing_context_json)
        fail_codes: list[str] = []
        if handoff.get("auto_publish") is True or handoff.get("scheduled_by_agent") is True:
            fail_codes.append("AUTO_PUBLISH_OR_AGENT_SCHEDULE_FORBIDDEN")
        status = GATE_BLOCK if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_CRITICAL if fail_codes else SEVERITY_INFO, {"manual_publish_only": timing.get("manual_publish_only")}, fail_codes, ["upload_card_copy"], ["publish_timing_context"], "Chỉ manual publish handoff." if fail_codes else None)


class RightsDisclosureComplianceGate:
    gate_key = "rights_disclosure_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **kwargs: Any) -> GateResult:
        base = DisclosureConsistencyGate().run(artifacts=artifacts, effective_context=effective_context, **kwargs)
        return GateResult(
            gate_key=self.gate_key,
            status=base.status,
            severity=base.severity,
            measurements_json=base.measurements_json,
            fail_codes=base.fail_codes,
            blocking_refs=base.blocking_refs,
            checked_artifact_refs=base.checked_artifact_refs,
            checked_contract_paths=base.checked_contract_paths,
            repair_hint=base.repair_hint,
            human_readable_summary=_summary_for(status=base.status, fail_codes=base.fail_codes, gate_key=self.gate_key),
            evidence_refs=base.evidence_refs,
        )


class MonetizationCTAComplianceGate:
    gate_key = "monetization_cta_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        cta = _dict(effective_context.monetization_cta_context_json)
        upload = _dict(artifacts.get("upload_card_copy"))
        text = " ".join(str(value) for value in [upload.get("title"), upload.get("description"), upload.get("cta")] if value).lower()
        forbidden = [item.lower() for item in _strings(cta.get("forbidden_cta_types"))]
        fail_codes = ["CTA_FORBIDDEN_BY_CONTRACT"] if any(item and item in text for item in forbidden) else []
        if cta.get("unsupported_asset_offer_forbidden") and any(item in text for item in ["free checklist", "demo", "download"]) and not artifacts.get("asset_manifest"):
            fail_codes.append("UNSUPPORTED_ASSET_OFFER_FORBIDDEN")
        status = GATE_BLOCK if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_HIGH if fail_codes else SEVERITY_INFO, {"forbidden_cta_types": forbidden}, sorted(set(fail_codes)), ["upload_card_copy"], ["monetization_cta_context"], "Sửa CTA theo monetization contract." if fail_codes else None)


class CharacterRuntimeComplianceGate:
    gate_key = "character_runtime_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        character = _dict(effective_context.character_identity_context_json)
        mode = character.get("character_policy_mode")
        refs = _artifact_character_refs(artifacts)
        fail_codes: list[str] = []
        if mode == "NO_CHARACTER" and refs:
            fail_codes.append("NO_CHARACTER_FORBIDS_CHARACTER_REFS")
        if mode == "REQUIRED_CHARACTER" and not character.get("character_profile_id"):
            fail_codes.append("REQUIRED_CHARACTER_BINDING_MISSING")
        status = GATE_BLOCK if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_CRITICAL if fail_codes else SEVERITY_INFO, {"character_policy_mode": mode, "artifact_character_refs": refs}, fail_codes, ["narration_script", "visual_plan", "thumbnail_brief"], ["character_identity_context"], "Sửa character binding/refs theo frozen context." if fail_codes else None)


class CharacterBindingGate(CharacterRuntimeComplianceGate):
    gate_key = "character_binding_gate"


class CharacterAssetReadinessGate(CharacterRuntimeComplianceGate):
    gate_key = "character_asset_readiness_gate"


class VoiceProfileReadinessGate(VoiceProfileComplianceGate):
    gate_key = "voice_profile_readiness_gate"


class CharacterConsistencyGate:
    gate_key = "character_consistency_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        character = _dict(effective_context.character_identity_context_json)
        allowed = {
            value
            for value in [
                character.get("character_profile_id"),
                character.get("character_version_id"),
                character.get("character_image_branch_id"),
                character.get("reference_asset_pack_id"),
            ]
            if value
        }
        refs = set(_artifact_character_refs(artifacts))
        fail_codes: list[str] = []
        if refs and not refs <= allowed:
            fail_codes.append("CHARACTER_REFS_DO_NOT_MATCH_FROZEN_CONTEXT")
        status = GATE_BLOCK if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_CRITICAL if fail_codes else SEVERITY_INFO, {"allowed_refs": sorted(allowed), "artifact_refs": sorted(refs)}, fail_codes, ["visual_plan", "thumbnail_brief"], ["character_identity_context"], "Dùng character refs từ EffectiveChannelRuntimeContextSnapshot." if fail_codes else None)


class ProviderCharacterInputGate(CharacterConsistencyGate):
    gate_key = "provider_character_input_gate"


class MarketRuntimeComplianceGate:
    gate_key = "market_runtime_compliance_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **_: Any) -> GateResult:
        expected = _dict(effective_context.market_locale_context_json).get("content_language")
        observed_values = [
            _dict(artifacts.get("narration_script")).get("language"),
            _dict(artifacts.get("metadata_package")).get("language"),
            _dict(artifacts.get("thumbnail_brief")).get("language"),
        ]
        mismatches = [value for value in observed_values if value and expected and str(value).lower() != str(expected).lower()]
        fail_codes = ["MARKET_LOCALE_LANGUAGE_MISMATCH"] if mismatches else []
        status = GATE_REVIEW if fail_codes else GATE_PASS
        return _gate_result(self.gate_key, status, SEVERITY_HIGH if fail_codes else SEVERITY_INFO, {"expected_language": expected, "observed_mismatches": mismatches}, fail_codes, ["narration_script", "metadata_package", "thumbnail_brief"], ["market_locale_context"], "Sửa artifact language theo market/locale." if fail_codes else None)


class ChannelRuntimeContractGate:
    gate_key = "channel_runtime_contract_gate"

    def run(self, *, artifacts: dict[str, Any], effective_context: EffectiveChannelRuntimeContextSnapshot, **kwargs: Any) -> GateResult:
        sub_results = [
            ScriptStyleComplianceGate().run(artifacts=artifacts, effective_context=effective_context, **kwargs),
            MetadataLocaleComplianceGate().run(artifacts=artifacts, effective_context=effective_context, **kwargs),
            CharacterRuntimeComplianceGate().run(artifacts=artifacts, effective_context=effective_context, **kwargs),
            MonetizationCTAComplianceGate().run(artifacts=artifacts, effective_context=effective_context, **kwargs),
        ]
        fail_codes: list[str] = []
        for result in sub_results:
            fail_codes.extend(result.fail_codes)
        status = GATE_BLOCK if any(result.status == GATE_BLOCK for result in sub_results) else (GATE_REVIEW if any(result.status == GATE_REVIEW for result in sub_results) else GATE_PASS)
        return _gate_result(self.gate_key, status, SEVERITY_HIGH if fail_codes else SEVERITY_INFO, {"sub_gate_statuses": {item.gate_key: item.status for item in sub_results}}, sorted(set(fail_codes)), ["package_artifacts"], ["effective_channel_runtime_context_snapshot"], "Sửa artifact theo frozen runtime contract." if fail_codes else None)


class GateResultReducer:
    def reduce(self, results: list[GateResult]) -> tuple[str, int, int]:
        hard_blocks = sum(1 for result in results if result.status == GATE_BLOCK)
        reviews = sum(1 for result in results if result.status == GATE_REVIEW)
        if hard_blocks:
            return GATE_BLOCK, hard_blocks, reviews
        if reviews:
            return GATE_REVIEW, 0, reviews
        return GATE_PASS, 0, 0


class PackageStatusReducer:
    def resolve(self, *, current_status: str, deterministic_batch: GateBatchResult | None, gatekeeper_result: str | None = None) -> dict[str, Any]:
        if deterministic_batch and deterministic_batch.status == GATE_BLOCK:
            provider_only = deterministic_batch.fail_codes and all(
                code.endswith("_NOT_CONFIGURED") or code in {"PROVIDER_READINESS_MISSING"}
                for code in deterministic_batch.fail_codes
            )
            return {
                "package_status": "WAITING_PROVIDER_CONFIG" if provider_only else "BLOCKED",
                "reason_codes": deterministic_batch.fail_codes,
                "source": "deterministic_gates",
            }
        if deterministic_batch and deterministic_batch.status == GATE_REVIEW:
            return {"package_status": "REVIEW_REQUIRED", "reason_codes": deterministic_batch.fail_codes, "source": "deterministic_gates"}
        if gatekeeper_result in {None, "", "UNKNOWN", "AMBIGUOUS"}:
            return {"package_status": "REVIEW_REQUIRED", "reason_codes": ["GATEKEEPER_RESULT_UNKNOWN"], "source": "gatekeeper_soft_review"}
        if gatekeeper_result == "BLOCK":
            return {"package_status": "BLOCKED", "reason_codes": ["GATEKEEPER_BLOCK"], "source": "gatekeeper_soft_review"}
        if gatekeeper_result == "REVIEW_REQUIRED":
            return {"package_status": "REVIEW_REQUIRED", "reason_codes": ["GATEKEEPER_REVIEW_REQUIRED"], "source": "gatekeeper_soft_review"}
        return {"package_status": current_status, "reason_codes": [], "source": "package_state"}


class R3D4GateService:
    GATES_BY_KEY = {
        "channel_runtime_contract_gate": ChannelRuntimeContractGate(),
        "script_style_compliance_gate": ScriptStyleComplianceGate(),
        "voice_profile_compliance_gate": VoiceProfileComplianceGate(),
        "visual_style_compliance_gate": VisualStyleComplianceGate(),
        "thumbnail_style_compliance_gate": ThumbnailStyleComplianceGate(),
        "metadata_locale_compliance_gate": MetadataLocaleComplianceGate(),
        "publish_timing_compliance_gate": PublishTimingComplianceGate(),
        "rights_disclosure_compliance_gate": RightsDisclosureComplianceGate(),
        "monetization_cta_compliance_gate": MonetizationCTAComplianceGate(),
        "character_runtime_compliance_gate": CharacterRuntimeComplianceGate(),
        "market_runtime_compliance_gate": MarketRuntimeComplianceGate(),
        "script_duration_gate": ScriptDurationGate(),
        "srt_format_gate": SRTFormatGate(),
        "srt_timing_gate": SRTTimingGate(),
        "caption_coverage_gate": CaptionCoverageGate(),
        "caption_readability_gate": CaptionReadabilityGate(),
        "script_to_srt_consistency_gate": ScriptToSRTConsistencyGate(),
        "hook_caption_gate": HookCaptionGate(),
        "visual_srt_timeline_gate": VisualSRTTimelineGate(),
        "visual_coverage_gate": VisualCoverageGate(),
        "artifact_consistency_gate": ArtifactConsistencyGate(),
        "disclosure_consistency_gate": DisclosureConsistencyGate(),
        "upload_copy_truthfulness_gate": UploadCopyTruthfulnessGate(),
        "provider_boundary_gate": ProviderBoundaryGate(),
        "hook_3s_gate": HookSpecGate(),
        "character_binding_gate": CharacterBindingGate(),
        "character_asset_readiness_gate": CharacterAssetReadinessGate(),
        "voice_profile_readiness_gate": VoiceProfileReadinessGate(),
        "character_consistency_gate": CharacterConsistencyGate(),
        "provider_character_input_gate": ProviderCharacterInputGate(),
    }
    GATES_AFTER_AGENT = {
        "ScriptWriterAgent": ["script_duration_gate", "hook_3s_gate", "script_style_compliance_gate"],
        "ScriptRewriteAgent": ["script_duration_gate", "hook_3s_gate", "script_style_compliance_gate"],
        "VisualPlanningAgent": ["visual_coverage_gate", "visual_style_compliance_gate", "character_runtime_compliance_gate"],
        "ThumbnailBriefAgent": ["thumbnail_style_compliance_gate", "character_consistency_gate"],
        "PublishingMetadataAgent": ["metadata_locale_compliance_gate"],
        "RightsDisclosureReviewer": ["rights_disclosure_compliance_gate", "disclosure_consistency_gate"],
        "UploadCardCopyAgent": ["upload_copy_truthfulness_gate", "monetization_cta_compliance_gate", "publish_timing_compliance_gate"],
    }

    def __init__(self, session: Session, *, reducer: GateResultReducer | None = None):
        self.session = session
        self.reducer = reducer or GateResultReducer()

    def run_after_agent(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        effective_context: EffectiveChannelRuntimeContextSnapshot,
        agent_key: str,
        artifacts: dict[str, Any],
        provider_readiness_state: dict[str, Any] | None = None,
    ) -> GateBatchResult | None:
        gate_keys = self.GATES_AFTER_AGENT.get(agent_key)
        if not gate_keys:
            return None
        return self.run_batch(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context,
            artifacts=artifacts,
            gate_keys=gate_keys,
            trigger_agent_key=agent_key,
            provider_readiness_state=provider_readiness_state,
        )

    def run_final_package_gates(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        effective_context: EffectiveChannelRuntimeContextSnapshot,
        artifacts: dict[str, Any],
        provider_readiness_state: dict[str, Any] | None = None,
        include_provider_boundary: bool = False,
    ) -> GateBatchResult:
        gate_keys = [
            "artifact_consistency_gate",
            "channel_runtime_contract_gate",
            "srt_format_gate",
            "srt_timing_gate",
            "caption_coverage_gate",
            "caption_readability_gate",
            "script_to_srt_consistency_gate",
            "hook_caption_gate",
            "visual_srt_timeline_gate",
        ]
        if include_provider_boundary:
            gate_keys.extend(["provider_boundary_gate", "provider_character_input_gate"])
        return self.run_batch(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context,
            artifacts=artifacts,
            gate_keys=gate_keys,
            trigger_agent_key="package_state_resolver",
            provider_readiness_state=provider_readiness_state,
        )

    def run_batch(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        effective_context: EffectiveChannelRuntimeContextSnapshot,
        artifacts: dict[str, Any],
        gate_keys: list[str],
        trigger_agent_key: str | None = None,
        provider_readiness_state: dict[str, Any] | None = None,
    ) -> GateBatchResult:
        results: list[GateResult] = []
        for gate_key in gate_keys:
            gate = self.GATES_BY_KEY.get(gate_key)
            if gate is None:
                results.append(_gate_result(gate_key, GATE_BLOCK, SEVERITY_CRITICAL, {}, ["REQUIRED_GATE_MISSING"], [], [], "Required gate is not registered."))
                continue
            try:
                results.append(
                    gate.run(
                        artifacts=artifacts,
                        effective_context=effective_context,
                        provider_readiness_state=provider_readiness_state,
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive audit path
                results.append(_gate_result(gate_key, GATE_BLOCK, SEVERITY_CRITICAL, {"exception": str(exc)}, ["GATE_EXCEPTION"], [], [], "Gate exception blocks package."))
        status, hard_blocks, reviews = self.reducer.reduce(results)
        batch = R3D4GateBatchRun(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context_snapshot_id=effective_context.id,
            context_hash=effective_context.context_hash,
            trigger_agent_key=trigger_agent_key,
            status=status,
            hard_block_count=hard_blocks,
            review_required_count=reviews,
            gate_results_json=[result.to_dict() for result in results],
            reducer_decision_json={"status": status, "hard_block_count": hard_blocks, "review_required_count": reviews},
        )
        self.session.add(batch)
        self.session.flush()
        for result in results:
            self.session.add(
                R3D4GateRun(
                    gate_batch_run_id=batch.id,
                    package_id=package_id,
                    video_project_id=video_project_id,
                    effective_context_snapshot_id=effective_context.id,
                    gate_key=result.gate_key,
                    status=result.status,
                    severity=result.severity,
                    measurements_json=result.measurements_json,
                    fail_codes=result.fail_codes,
                    blocking_refs=result.blocking_refs,
                    checked_artifact_refs=result.checked_artifact_refs,
                    checked_contract_paths=result.checked_contract_paths,
                    evidence_refs=result.evidence_refs or [],
                    repair_hint=result.repair_hint,
                    human_readable_summary=result.human_readable_summary,
                )
            )
        self.session.flush()
        return GateBatchResult(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context_snapshot_id=effective_context.id,
            status=status,
            gate_results=results,
            hard_block_count=hard_blocks,
            review_required_count=reviews,
            context_hash=effective_context.context_hash,
            gate_batch_run_id=batch.id,
        )


def compact_gate_report(existing: dict[str, Any] | None, batch: GateBatchResult) -> dict[str, Any]:
    previous_batches = list(_dict(existing).get("gate_batch_run_refs") or [])
    previous_batches.append(str(batch.gate_batch_run_id) if batch.gate_batch_run_id else None)
    fail_codes = sorted(set([*_strings(_dict(existing).get("fail_codes")), *batch.fail_codes]))
    return {
        "status": batch.status if batch.status != GATE_PASS else _dict(existing).get("status", GATE_PASS),
        "gate_batch_run_refs": [item for item in previous_batches if item],
        "hard_block_count": int(_dict(existing).get("hard_block_count") or 0) + batch.hard_block_count,
        "review_required_count": int(_dict(existing).get("review_required_count") or 0) + batch.review_required_count,
        "fail_codes": fail_codes,
        "latest_gate_results": [
            {
                "gate_key": result.gate_key,
                "status": result.status,
                "fail_codes": result.fail_codes,
                "summary": result.human_readable_summary,
            }
            for result in batch.gate_results
            if result.status != GATE_PASS
        ],
    }


def _gate_result(
    gate_key: str,
    status: str,
    severity: str,
    measurements: dict[str, Any],
    fail_codes: list[str],
    artifact_keys: list[str],
    contract_paths: list[str],
    repair_hint: str | None,
) -> GateResult:
    return GateResult(
        gate_key=gate_key,
        status=status,
        severity=severity,
        measurements_json=measurements,
        fail_codes=sorted(set(fail_codes)),
        blocking_refs=[{"artifact_key": key} for key in artifact_keys] if status in {GATE_BLOCK, GATE_REVIEW} else [],
        checked_artifact_refs=[{"artifact_key": key} for key in artifact_keys],
        checked_contract_paths=contract_paths,
        repair_hint=repair_hint,
        human_readable_summary=_summary_for(status=status, fail_codes=fail_codes, gate_key=gate_key),
    )


def _summary_for(*, status: str, fail_codes: list[str], gate_key: str) -> str:
    if status == GATE_PASS:
        return f"{gate_key}: PASS."
    if status == GATE_SKIPPED:
        return f"{gate_key}: skipped/not applicable."
    return f"{gate_key}: {status} ({', '.join(sorted(set(fail_codes))) or 'NO_CODE'})."


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if item not in (None, "")]


def _field_present(value: dict[str, Any], field: str) -> bool:
    current: Any = value
    for part in field.split("."):
        if not isinstance(current, dict) or current.get(part) in (None, "", []):
            return False
        current = current[part]
    return True


def _duration_policy(effective_context: EffectiveChannelRuntimeContextSnapshot, *, artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    frozen = _dict(_dict(artifacts or {}).get("duration_model"))
    if frozen:
        return frozen
    policy = _dict(_dict(effective_context.category_runtime_context_json).get("default_format_policy"))
    long_form = _dict(policy.get("long_form"))
    if long_form:
        minutes = _dict(long_form.get("target_duration_minutes"))
        min_minutes = _first_number(minutes.get("min"))
        max_minutes = _first_number(minutes.get("max"))
        min_seconds = _first_number(long_form.get("min_seconds"), min_minutes * 60 if min_minutes is not None else None)
        max_seconds = _first_number(long_form.get("max_seconds"), max_minutes * 60 if max_minutes is not None else None)
        target = _first_number(long_form.get("target_duration_seconds"), long_form.get("target_seconds"))
        if target is None and min_seconds is not None and max_seconds is not None:
            target = (min_seconds + max_seconds) / 2
        return {
            **policy,
            "target_format": "long_form",
            "target_duration_seconds": target,
            "allowed_duration_range_seconds": {"min": min_seconds, "max": max_seconds},
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
        }
    return policy


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _first(value: list[Any]) -> Any:
    return value[0] if value else None


def _script_total_seconds(script: dict[str, Any]) -> float:
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    return sum(_float(item.get("approx_seconds")) for item in sentences)


def _script_estimated_total_seconds(script: dict[str, Any], duration_model: dict[str, Any] | None = None) -> float:
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    word_count = _word_count(" ".join(str(item.get("text") or "") for item in sentences))
    wpm = _first_number(_dict(duration_model).get("words_per_minute_assumption")) or 140.0
    if word_count and wpm:
        return round((word_count / wpm) * 60, 3)
    return _script_total_seconds(script)


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value))


def _hook_spec(artifacts: dict[str, Any]) -> dict[str, Any]:
    script = _dict(artifacts.get("narration_script"))
    plan = _dict(artifacts.get("script_outline"))
    hook = _dict(artifacts.get("hook_spec") or script.get("hook_spec") or plan.get("hook_spec"))
    return dict(hook)


def _text_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text_blob(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_text_blob(item) for item in value)
    return str(value)


def _parse_srt(content: str) -> dict[str, Any]:
    fail_codes: list[str] = []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content.strip()) if block.strip()]
    previous_end = 0.0
    total_seconds = 0.0
    expected_number = 1
    cues: list[dict[str, Any]] = []
    large_gap_count = 0
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            fail_codes.append("SRT_BLOCK_INCOMPLETE")
            continue
        try:
            number = int(lines[0])
        except ValueError:
            fail_codes.append("SRT_NUMBER_INVALID")
            continue
        if number != expected_number:
            fail_codes.append("SRT_NUMBERING_NOT_SEQUENTIAL")
        expected_number += 1
        match = re.match(r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})$", lines[1])
        if not match:
            fail_codes.append("SRT_TIMESTAMP_FORMAT_INVALID")
            continue
        start = _srt_time(match.group(1))
        end = _srt_time(match.group(2))
        if start < 0 or end < 0:
            fail_codes.append("SRT_NEGATIVE_TIMESTAMP")
        if end <= start:
            fail_codes.append("SRT_END_NOT_AFTER_START")
        if start < previous_end:
            fail_codes.append("SRT_TIMING_OVERLAP")
        if start - previous_end > 1.0:
            large_gap_count += 1
        text_lines = lines[2:]
        if not text_lines:
            fail_codes.append("SRT_TEXT_EMPTY")
        cues.append(
            {
                "index": number,
                "start_seconds": start,
                "end_seconds": end,
                "duration_seconds": round(end - start, 3),
                "text_lines": text_lines,
                "text": " ".join(text_lines),
            }
        )
        previous_end = max(previous_end, end)
        total_seconds = max(total_seconds, end)
    return {
        "cue_count": len(blocks),
        "total_seconds": round(total_seconds, 3),
        "large_gap_count": large_gap_count,
        "cues": cues,
        "fail_codes": sorted(set(fail_codes)),
    }


def _srt_time(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _srt_text_only(content: str) -> str:
    parsed = _parse_srt(content)
    return " ".join(cue["text"] for cue in parsed["cues"])


def _normalized_tokens(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"\b[\w'-]+\b", value)]


def _text_contains_any(value: dict[str, Any], needles: list[str]) -> bool:
    text = canonical_json(value).lower()
    return any(needle.lower() in text for needle in needles)


def _artifact_character_refs(artifacts: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for artifact_key in ("narration_script", "visual_plan", "thumbnail_brief", "voice_profile", "voice_timeline"):
        artifact = _dict(artifacts.get(artifact_key))
        refs.extend(_strings(artifact.get("character_refs")))
        refs.extend(_strings(artifact.get("character_profile_id")))
        refs.extend(_strings(artifact.get("character_version_id")))
        refs.extend(_strings(artifact.get("character_branch_id") or artifact.get("character_image_branch_id")))
    return sorted(set(refs))


def _provider_readiness_map(provider_readiness_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for summary in _list(provider_readiness_state.get("provider_summaries")):
        if not isinstance(summary, dict) or not summary.get("provider_key"):
            continue
        key = str(summary["provider_key"]).lower()
        readiness_state = str(summary.get("readiness_state") or "UNKNOWN")
        missing_env = _list(summary.get("missing_env_keys"))
        reason_codes = _strings(summary.get("reason_codes"))
        status = "PASS" if readiness_state == "PASS" else ("NEEDS_CREDENTIAL" if missing_env or any("CREDENTIAL" in code or "KEY" in code for code in reason_codes) else "NOT_CONFIGURED")
        mapped[key] = {"status": status, "readiness_state": readiness_state, "reason_codes": reason_codes}
    m2_snapshot = _dict(provider_readiness_state.get("m2_provider_wiring")) or _dict(_dict(provider_readiness_state.get("technical_appendix")).get("m2_provider_wiring"))
    for item in _list(m2_snapshot.get("providers")):
        if not isinstance(item, dict) or not item.get("provider_key"):
            continue
        key = str(item["provider_key"]).lower()
        readiness_state = str(item.get("readiness_state") or "UNKNOWN")
        reason_codes = _strings(item.get("blocker_reason_codes"))
        if readiness_state in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION", "CAPABILITY_READY"}:
            status = "CONFIGURED"
        elif readiness_state == "DISABLED":
            status = "DISABLED"
        elif item.get("missing_env_keys") or any("CREDENTIAL" in code or "KEY" in code for code in reason_codes):
            status = "NEEDS_CREDENTIAL"
        else:
            status = "NOT_CONFIGURED"
        mapped[key] = {"status": status, "readiness_state": readiness_state, "reason_codes": reason_codes}
        if key == "google_veo":
            mapped["google_veo"] = mapped[key]
    return mapped
