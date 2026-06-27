from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    AgentOutputEnvelope,
    PromptMessage,
    PromptOutputValidationRequest,
    PromptOutputValidationResult,
    PromptRegistrySyncSummary,
    PromptRenderRequest,
    PromptRenderResult,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    AgentPromptProfile,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    PromptAuditSnapshot,
    PromptContractVersion,
    PromptEvaluationCase,
    PromptEvaluationRun,
    PromptRenderRun,
    PromptTemplateRecord,
    StructuredOutputSchema,
)


PROMPT_CONTRACT_VERSION = "m12.1.0"
DEFAULT_TEMPLATE_VERSION = "1.0.0"
BASE_SCHEMA_REF = "base_agent_envelope"
MISSING_CHANNEL_NEXT_ACTION = "Bổ sung hoặc compile lại ChannelProfileVersion trước khi render prompt."
FORBIDDEN_BEHAVIOR_CODES = [
    "fake" + "_traffic",
    "bot" + "_engagement",
    "spam_reupload",
    "algorithm_manipulation",
    "platform" + "_evasion",
    "ip_vps_tricks",
    "youtube_studio_scraping",
    "dashboard_scraping",
    "invented_metrics",
    "invented_sources",
    "invented_rights",
    "unsupported_local_claims",
]

REQUIRED_AGENT_KEYS = [
    "ChannelAuthorityAgent",
    "TopicIdeaScoringAgent",
    "ResearchPackSummarizer",
    "ScriptPlanningAgent",
    "ScriptWriterAgent",
    "ScriptRewriteAgent",
    "PublishingMetadataAgent",
    "VisualPlanningAgent",
    "ThumbnailBriefAgent",
    "GatekeeperSoftReviewAgent",
    "LearningCandidateService",
    "EvidenceBundleSummarizer",
    "PostPublishSummaryAgent",
    "EngineeringArchitectAgent",
    "ShortCandidateExtractor",
    "ShortCandidateRanker",
    "DerivativeOriginalityReviewer",
    "RecoveryProposalReviewer",
    "LocalizationSubtitleAgent",
    "LocalizedMetadataAgent",
    "PublishTimingSummaryAgent",
    "ProviderReadinessSummaryAgent",
    "MediaQCExplanationAgent",
    "RightsDisclosureReviewer",
    "UploadCardCopyAgent",
]

ENVELOPE_REQUIRED_FIELDS = {
    "contract_version",
    "agent_key",
    "status",
    "confidence_label",
    "risk_level",
    "evidence_refs",
    "limitations",
    "next_action",
    "operator_summary_vi",
    "technical_appendix",
    "artifact",
}
ENVELOPE_ALLOWED_STATUS = {"OK", "REVIEW_REQUIRED", "BLOCK", "REFUSAL", "ERROR"}
ENVELOPE_ALLOWED_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
ENVELOPE_ALLOWED_RISK = {"LOW", "MEDIUM", "HIGH", "CRITICAL", None}


@dataclass(frozen=True)
class PromptTemplateBundle:
    manifest: dict[str, Any]
    system_prompt: str
    user_template: str
    output_schema: dict[str, Any]
    prompt_hash: str
    manifest_path: Path


class PromptRegistryRepository:
    def __init__(self, *, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[2]
        self.prompts_dir = self.root / "app" / "prompts"
        self.registry_path = self.prompts_dir / "registry" / "agents.yaml"

    def load_agent_manifests(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.exists():
            raise ValidationFailureError("M12.1 prompt registry source file is missing.")
        raw = yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {}
        agents = raw.get("agents")
        if not isinstance(agents, list):
            raise ValidationFailureError("M12.1 prompt registry must contain an agents list.")
        manifests: dict[str, dict[str, Any]] = {}
        for item in agents:
            if not isinstance(item, dict) or not item.get("agent_key"):
                raise ValidationFailureError("Invalid prompt registry agent entry.")
            agent_key = str(item["agent_key"])
            manifests[agent_key] = item
        missing = sorted(set(REQUIRED_AGENT_KEYS) - set(manifests))
        if missing:
            raise ValidationFailureError(f"M12.1 prompt registry missing required agents: {missing}")
        return manifests

    def load_bundle(self, agent_key: str) -> PromptTemplateBundle:
        manifests = self.load_agent_manifests()
        manifest = manifests.get(agent_key)
        if manifest is None:
            raise NotFoundError(f"prompt agent not found: {agent_key}")
        common_parts: list[str] = []
        for ref in manifest.get("common_skill_refs", []):
            common_path = self._resolve(ref)
            common_parts.append(f"## {Path(ref).stem}\n{common_path.read_text(encoding='utf-8').strip()}")
        system_delta = self._resolve(manifest["system_delta_ref"]).read_text(encoding="utf-8").strip()
        user_template = self._resolve(manifest["user_template_ref"]).read_text(encoding="utf-8").strip()
        schema = json.loads(self._resolve(manifest["output_schema_ref"]).read_text(encoding="utf-8"))
        system_prompt = "\n\n".join(
            [
                "# VCOS Common Skills",
                *common_parts,
                "# Agent-Specific Skill",
                system_delta,
                "# Output Contract",
                (
                    "Return JSON only. The JSON must match the BaseEnvelope schema and include "
                    "limitations, confidence_label, risk_level, next_action, operator_summary_vi, "
                    "technical_appendix, and artifact."
                ),
            ]
        )
        prompt_hash = prompt_template_hash(
            system_prompt=system_prompt,
            user_template=user_template,
            output_schema_ref=manifest["schema_ref"],
            template_version=manifest["template_version"],
            common_skill_refs=list(manifest.get("common_skill_refs", [])),
        )
        return PromptTemplateBundle(
            manifest=manifest,
            system_prompt=system_prompt,
            user_template=user_template,
            output_schema=schema,
            prompt_hash=prompt_hash,
            manifest_path=self.registry_path,
        )

    def load_schema(self, schema_ref: str) -> dict[str, Any]:
        manifests = self.load_agent_manifests()
        for manifest in manifests.values():
            if manifest.get("schema_ref") == schema_ref:
                return json.loads(self._resolve(manifest["output_schema_ref"]).read_text(encoding="utf-8"))
        raise NotFoundError(f"prompt schema not found: {schema_ref}")

    def load_eval_cases(self) -> list[dict[str, Any]]:
        cases_dir = self.prompts_dir / "fixtures" / "eval_cases"
        if not cases_dir.exists():
            return []
        cases: list[dict[str, Any]] = []
        for path in sorted(cases_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["input_fixture_ref"] = str(path.relative_to(self.root))
            cases.append(data)
        return cases

    def _resolve(self, ref: str) -> Path:
        path = self.root / ref
        if not path.exists():
            raise ValidationFailureError(f"prompt registry ref missing: {ref}")
        return path


class PromptRegistryService:
    def __init__(self, session: Session, repository: PromptRegistryRepository | None = None):
        self.session = session
        self.repository = repository or PromptRegistryRepository()

    def sync_repo_registry(self) -> PromptRegistrySyncSummary:
        manifests = self.repository.load_agent_manifests()
        prompt_hashes: dict[str, str] = {}
        schema_identities: set[tuple[str, str]] = set()
        for agent_key in REQUIRED_AGENT_KEYS:
            bundle = self.repository.load_bundle(agent_key)
            manifest = bundle.manifest
            prompt_hashes[agent_key] = bundle.prompt_hash
            schema_id = (manifest["schema_ref"], manifest["schema_version"])
            if schema_id not in schema_identities:
                schema_identities.add(schema_id)
                self._upsert_schema(bundle)
            self._upsert_profile(bundle)
            self._upsert_template(bundle)
            self._upsert_contract(bundle)
        eval_count = self._upsert_eval_cases()
        self.session.flush()
        return PromptRegistrySyncSummary(
            template_count=len(REQUIRED_AGENT_KEYS),
            profile_count=len(REQUIRED_AGENT_KEYS),
            contract_count=len(REQUIRED_AGENT_KEYS),
            schema_count=len(schema_identities),
            evaluation_case_count=eval_count,
            agent_keys=list(REQUIRED_AGENT_KEYS),
            prompt_hashes=prompt_hashes,
        )

    def render_prompt(self, data: PromptRenderRequest) -> PromptRenderResult:
        self.sync_repo_registry()
        bundle = self.repository.load_bundle(data.agent_key)
        manifest = bundle.manifest
        template_key = data.template_key or manifest["template_key"]
        template_version = data.template_version or manifest["template_version"]
        if template_key != manifest["template_key"] or template_version != manifest["template_version"]:
            raise NotFoundError(f"prompt template not found: {data.agent_key}/{template_key}@{template_version}")
        profile = self._profile(data.agent_key)
        router_lane = data.router_lane or profile.default_router_lane
        if router_lane not in profile.allowed_router_lanes:
            raise ValidationFailureError(f"router lane {router_lane} is not allowed for {data.agent_key}")

        contract_payload = self._resolve_channel_payload(data=data, profile=profile)
        context_hash = prompt_context_hash(
            render_vars={
                "task_payload": data.task_payload,
                "render_vars": data.render_vars,
                "evidence_refs": data.evidence_refs,
                "artifact_refs": data.artifact_refs,
            },
            channel_profile_version_id=data.channel_profile_version_id,
            compiled_policy_snapshot_id=data.compiled_policy_snapshot_id,
            channel_contract_json=contract_payload["channel_contract_json"],
            market_locale_context_json=contract_payload["market_locale_context_json"],
            artifact_refs=data.artifact_refs,
        )

        missing_result = self._missing_channel_result(
            data=data,
            profile=profile,
            contract_payload=contract_payload,
        )
        if missing_result is not None:
            render_run = self._create_render_run(
                data=data,
                manifest=manifest,
                rendered_messages=[],
                prompt_hash=bundle.prompt_hash,
                prompt_context_hash=context_hash,
                output_schema_ref=manifest["schema_ref"],
                router_lane=router_lane,
                validation_status="REVIEW_REQUIRED",
                contract_payload=contract_payload,
            )
            audit = self._create_audit_snapshot(
                render_run=render_run,
                validation_result=missing_result.model_dump(mode="json"),
                repair_attempts=[],
                provider_attempt_refs=[],
            )
            return PromptRenderResult(
                status="REVIEW_REQUIRED",
                agent_key=data.agent_key,
                template_key=template_key,
                template_version=template_version,
                router_lane=router_lane,
                rendered_messages=[],
                prompt_hash=bundle.prompt_hash,
                prompt_context_hash=context_hash,
                output_schema_ref=manifest["schema_ref"],
                prompt_render_run_id=render_run.id,
                prompt_audit_snapshot_id=audit.id,
                blocking_output=missing_result,
                reason_codes=["CHANNEL_CONTRACT_REQUIRED"],
            )

        render_vars = self._render_vars(data=data, manifest=manifest, contract_payload=contract_payload)
        messages = [
            PromptMessage(role="system", content=bundle.system_prompt),
            PromptMessage(role="user", content=render_template(bundle.user_template, render_vars)),
        ]
        rendered_messages = [message.model_dump() for message in messages]
        render_run = self._create_render_run(
            data=data,
            manifest=manifest,
            rendered_messages=rendered_messages,
            prompt_hash=bundle.prompt_hash,
            prompt_context_hash=context_hash,
            output_schema_ref=manifest["schema_ref"],
            router_lane=router_lane,
            validation_status="OK",
            contract_payload=contract_payload,
            render_vars=render_vars,
        )
        audit = self._create_audit_snapshot(
            render_run=render_run,
            validation_result={"status": "NOT_RUN", "schema_ref": manifest["schema_ref"]},
            repair_attempts=[],
            provider_attempt_refs=[],
        )
        return PromptRenderResult(
            status="OK",
            agent_key=data.agent_key,
            template_key=template_key,
            template_version=template_version,
            router_lane=router_lane,
            rendered_messages=messages,
            prompt_hash=bundle.prompt_hash,
            prompt_context_hash=context_hash,
            output_schema_ref=manifest["schema_ref"],
            prompt_render_run_id=render_run.id,
            prompt_audit_snapshot_id=audit.id,
            blocking_output=None,
            reason_codes=["PROMPT_RENDERED"],
        )

    def validate_output(self, data: PromptOutputValidationRequest) -> PromptOutputValidationResult:
        self.sync_repo_registry()
        schema = self.repository.load_schema(data.schema_ref)
        parsed, repair_attempts = parse_json_with_safe_repair(data.raw_output)
        if parsed is None:
            result = {"valid": False, "errors": ["Output is not parseable JSON."], "schema_ref": data.schema_ref}
            return PromptOutputValidationResult(status="ERROR", validation_result=result, repair_attempts=repair_attempts, reason_codes=["JSON_PARSE_FAILED"])
        validation = validate_base_envelope(parsed, schema=schema, expected_agent_key=data.agent_key)
        status = "OK" if validation["valid"] else "REVIEW_REQUIRED"
        if parsed.get("status") in {"BLOCK", "REFUSAL", "ERROR"}:
            status = parsed["status"] if parsed["status"] in {"BLOCK", "ERROR"} else "REVIEW_REQUIRED"
        if data.prompt_render_run_id is not None:
            render_run = self.session.get(PromptRenderRun, data.prompt_render_run_id)
            if render_run is not None:
                render_run.validation_status = status
                self._create_audit_snapshot(
                    render_run=render_run,
                    validation_result=validation,
                    repair_attempts=repair_attempts,
                    provider_attempt_refs=[],
                    final_output_ref=f"prompt-output:{data.prompt_render_run_id}",
                )
        return PromptOutputValidationResult(
            status=status,
            parsed_output=parsed,
            validation_result=validation,
            repair_attempts=repair_attempts,
            reason_codes=["SCHEMA_VALIDATED"] if validation["valid"] else ["SCHEMA_VALIDATION_REVIEW_REQUIRED"],
        )

    def run_evaluation_cases(self) -> list[PromptEvaluationRun]:
        self.sync_repo_registry()
        cases = list(self.session.scalars(select(PromptEvaluationCase).where(PromptEvaluationCase.status == "ACTIVE")).all())
        runs: list[PromptEvaluationRun] = []
        for case in cases:
            state = "SKIPPED"
            result: dict[str, Any] = {"case_key": case.case_key}
            try:
                if case.pass_criteria.get("type") == "missing_channel_contract":
                    render = self.render_prompt(
                        PromptRenderRequest(
                            agent_key=case.agent_key,
                            template_key=case.template_key,
                            template_version=case.template_version,
                            task_payload={"eval_case": case.case_key},
                        )
                    )
                    expected_status = case.expected_outcome.get("status")
                    state = "PASS" if render.status == expected_status else "FAIL"
                    result = {"render_status": render.status, "expected_status": expected_status, "reason_codes": render.reason_codes}
                elif case.pass_criteria.get("type") == "base_envelope_schema":
                    fixture = json.loads((self.repository.root / case.input_fixture_ref).read_text(encoding="utf-8"))
                    validation = self.validate_output(
                        PromptOutputValidationRequest(agent_key=case.agent_key, raw_output=fixture["raw_output"])
                    )
                    state = "PASS" if validation.status == case.expected_outcome.get("status") else "FAIL"
                    result = validation.validation_result
            except Exception as exc:
                state = "ERROR"
                result = {"error": str(exc)}
            run = PromptEvaluationRun(
                case_key=case.case_key,
                agent_key=case.agent_key,
                template_version=case.template_version,
                run_state=state,
                output_ref=None,
                validation_result=result,
            )
            self.session.add(run)
            runs.append(run)
        self.session.flush()
        return runs

    def _profile(self, agent_key: str) -> AgentPromptProfile:
        profile = self.session.scalars(select(AgentPromptProfile).where(AgentPromptProfile.agent_key == agent_key)).one_or_none()
        if profile is None:
            raise NotFoundError(f"agent prompt profile not found: {agent_key}")
        return profile

    def _resolve_channel_payload(self, *, data: PromptRenderRequest, profile: AgentPromptProfile) -> dict[str, Any]:
        channel_contract = data.channel_contract_json
        compiled_policy = data.compiled_policy_snapshot_json
        market_locale = data.market_locale_context_json
        if data.channel_profile_version_id is not None:
            profile_version = self.session.get(ChannelProfileVersion, data.channel_profile_version_id)
            if profile_version is None:
                raise NotFoundError(f"channel profile version not found: {data.channel_profile_version_id}")
            if channel_contract is None:
                channel_contract = build_channel_contract_from_profile(self.session, profile_version)
        if data.compiled_policy_snapshot_id is not None:
            snapshot = self.session.get(CompiledChannelPolicySnapshot, data.compiled_policy_snapshot_id)
            if snapshot is None:
                raise NotFoundError(f"compiled policy snapshot not found: {data.compiled_policy_snapshot_id}")
            if data.channel_profile_version_id is not None and snapshot.channel_profile_version_id != data.channel_profile_version_id:
                raise ValidationFailureError("compiled policy snapshot does not match channel profile version")
            if compiled_policy is None:
                compiled_policy = snapshot.compiled_payload
        if market_locale is None and channel_contract is not None:
            market_locale = channel_contract.get("market_locale") if isinstance(channel_contract, dict) else None
        return {
            "channel_contract_json": channel_contract,
            "compiled_policy_snapshot_json": compiled_policy,
            "market_locale_context_json": market_locale,
            "channel_contract_required": profile.channel_contract_required,
            "market_locale_context_required": profile.market_locale_context_required,
        }

    def _missing_channel_result(
        self,
        *,
        data: PromptRenderRequest,
        profile: AgentPromptProfile,
        contract_payload: dict[str, Any],
    ) -> AgentOutputEnvelope | None:
        missing: list[str] = []
        if profile.channel_contract_required:
            if data.channel_profile_version_id is None:
                missing.append("channel_profile_version_id")
            if data.compiled_policy_snapshot_id is None:
                missing.append("compiled_policy_snapshot_id")
            if not contract_payload["channel_contract_json"]:
                missing.append("channel_contract_json")
            if not contract_payload["compiled_policy_snapshot_json"]:
                missing.append("compiled_policy_snapshot_json")
        if profile.market_locale_context_required and not contract_payload["market_locale_context_json"]:
            missing.append("market_locale_context_json")
        contract_status = None
        market_status = None
        if isinstance(contract_payload["channel_contract_json"], dict):
            contract_status = contract_payload["channel_contract_json"].get("contract_status")
            market = contract_payload["channel_contract_json"].get("market_locale")
            if isinstance(market, dict):
                market_status = market.get("market_locale_context_status")
        if profile.channel_contract_required and contract_status in {"MISSING", "PARTIAL", "STALE", "CONTRADICTORY"}:
            missing.append(f"contract_status:{contract_status}")
        if profile.market_locale_context_required and market_status in {"UNKNOWN", "PARTIAL", None}:
            missing.append(f"market_locale_context_status:{market_status or 'MISSING'}")
        if not missing:
            return None
        return AgentOutputEnvelope(
            contract_version=PROMPT_CONTRACT_VERSION,
            agent_key=data.agent_key,
            status="REVIEW_REQUIRED",
            confidence_label="LOW",
            risk_level="HIGH",
            evidence_refs=[],
            limitations=["Thiếu Channel Contract đã compile/freeze nên agent không được suy đoán cấu hình kênh."],
            next_action=MISSING_CHANNEL_NEXT_ACTION,
            operator_summary_vi="Cần bổ sung hoặc compile lại cấu hình kênh trước khi render prompt.",
            technical_appendix={"missing_or_invalid_fields": sorted(set(missing))},
            artifact=None,
        )

    def _render_vars(self, *, data: PromptRenderRequest, manifest: dict[str, Any], contract_payload: dict[str, Any]) -> dict[str, str]:
        channel_profile_version_id = str(data.channel_profile_version_id) if data.channel_profile_version_id else "null"
        compiled_policy_snapshot_id = str(data.compiled_policy_snapshot_id) if data.compiled_policy_snapshot_id else "null"
        payload: dict[str, Any] = {
            **data.render_vars,
            "agent_key": data.agent_key,
            "template_key": manifest["template_key"],
            "template_version": manifest["template_version"],
            "channel_profile_version_id": channel_profile_version_id,
            "compiled_policy_snapshot_id": compiled_policy_snapshot_id,
            "task_payload_json": canonical_json(data.task_payload),
            "channel_contract_json": canonical_json(contract_payload["channel_contract_json"]),
            "compiled_policy_snapshot_json": canonical_json(contract_payload["compiled_policy_snapshot_json"]),
            "market_locale_context_json": canonical_json(contract_payload["market_locale_context_json"]),
            "evidence_refs_json": canonical_json(data.evidence_refs),
            "artifact_refs_json": canonical_json(data.artifact_refs),
            "required_output_instruction": "Return JSON only using the BaseEnvelope schema. Do not add unknown fields.",
        }
        return {key: str(value) for key, value in payload.items()}

    def _create_render_run(
        self,
        *,
        data: PromptRenderRequest,
        manifest: dict[str, Any],
        rendered_messages: list[dict[str, Any]],
        prompt_hash: str,
        prompt_context_hash: str,
        output_schema_ref: str,
        router_lane: str,
        validation_status: str,
        contract_payload: dict[str, Any],
        render_vars: dict[str, Any] | None = None,
    ) -> PromptRenderRun:
        run = PromptRenderRun(
            agent_key=data.agent_key,
            template_key=manifest["template_key"],
            template_version=manifest["template_version"],
            rendered_messages=rendered_messages,
            prompt_hash=prompt_hash,
            prompt_context_hash=prompt_context_hash,
            input_payload_ref=data.input_payload_ref,
            output_schema_ref=output_schema_ref,
            router_lane=router_lane,
            channel_profile_version_id=data.channel_profile_version_id,
            compiled_policy_snapshot_id=data.compiled_policy_snapshot_id,
            channel_contract_json=contract_payload["channel_contract_json"],
            compiled_policy_snapshot_json=contract_payload["compiled_policy_snapshot_json"],
            market_locale_context_json=contract_payload["market_locale_context_json"],
            render_vars_json=render_vars or {"task_payload": data.task_payload, "render_vars": data.render_vars},
            artifact_refs=data.artifact_refs,
            validation_status=validation_status,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def _create_audit_snapshot(
        self,
        *,
        render_run: PromptRenderRun,
        validation_result: dict[str, Any],
        repair_attempts: list[dict[str, Any]],
        provider_attempt_refs: list[dict[str, Any]],
        final_output_ref: str | None = None,
    ) -> PromptAuditSnapshot:
        audit = PromptAuditSnapshot(
            agent_key=render_run.agent_key,
            template_key=render_run.template_key,
            template_version=render_run.template_version,
            channel_profile_version_id=render_run.channel_profile_version_id,
            compiled_policy_snapshot_id=render_run.compiled_policy_snapshot_id,
            prompt_hash=render_run.prompt_hash,
            prompt_context_hash=render_run.prompt_context_hash,
            router_lane=render_run.router_lane,
            provider_attempt_refs=provider_attempt_refs,
            prompt_render_run_id=render_run.id,
            final_output_ref=final_output_ref,
            validation_result=validation_result,
            repair_attempts=repair_attempts,
        )
        self.session.add(audit)
        self.session.flush()
        return audit

    def _upsert_schema(self, bundle: PromptTemplateBundle) -> StructuredOutputSchema:
        manifest = bundle.manifest
        schema = self.session.scalars(
            select(StructuredOutputSchema)
            .where(StructuredOutputSchema.schema_ref == manifest["schema_ref"])
            .where(StructuredOutputSchema.schema_version == manifest["schema_version"])
        ).one_or_none()
        values = {
            "dialect": bundle.output_schema.get("$schema", "JSON_SCHEMA_2020_12"),
            "json_schema": bundle.output_schema,
            "status": manifest["status"],
        }
        if schema is None:
            schema = StructuredOutputSchema(
                schema_ref=manifest["schema_ref"],
                schema_version=manifest["schema_version"],
                **values,
            )
            self.session.add(schema)
        else:
            for key, value in values.items():
                setattr(schema, key, value)
        return schema

    def _upsert_profile(self, bundle: PromptTemplateBundle) -> AgentPromptProfile:
        manifest = bundle.manifest
        profile = self.session.scalars(select(AgentPromptProfile).where(AgentPromptProfile.agent_key == manifest["agent_key"])).one_or_none()
        values = {
            "default_router_lane": manifest["default_router_lane"],
            "allowed_router_lanes": list(manifest["allowed_router_lanes"]),
            "input_contract": dict(manifest["input_contract"]),
            "output_contract": dict(manifest["output_contract"]),
            "safety_policy_refs": list(manifest["safety_policy_refs"]),
            "common_skill_refs": list(manifest["common_skill_refs"]),
            "channel_contract_required": bool(manifest["channel_contract_required"]),
            "market_locale_context_required": bool(manifest["market_locale_context_required"]),
            "status": manifest["status"],
        }
        if profile is None:
            profile = AgentPromptProfile(agent_key=manifest["agent_key"], **values)
            self.session.add(profile)
        else:
            for key, value in values.items():
                setattr(profile, key, value)
        return profile

    def _upsert_template(self, bundle: PromptTemplateBundle) -> PromptTemplateRecord:
        manifest = bundle.manifest
        record = self.session.scalars(
            select(PromptTemplateRecord)
            .where(PromptTemplateRecord.agent_key == manifest["agent_key"])
            .where(PromptTemplateRecord.template_key == manifest["template_key"])
            .where(PromptTemplateRecord.template_version == manifest["template_version"])
        ).one_or_none()
        values = {
            "status": manifest["status"],
            "file_path": str(bundle.manifest_path.relative_to(self.repository.root)),
            "prompt_hash": bundle.prompt_hash,
        }
        if record is None:
            record = PromptTemplateRecord(
                agent_key=manifest["agent_key"],
                template_key=manifest["template_key"],
                template_version=manifest["template_version"],
                **values,
            )
            self.session.add(record)
        else:
            for key, value in values.items():
                setattr(record, key, value)
        return record

    def _upsert_contract(self, bundle: PromptTemplateBundle) -> PromptContractVersion:
        manifest = bundle.manifest
        contract = self.session.scalars(
            select(PromptContractVersion)
            .where(PromptContractVersion.agent_key == manifest["agent_key"])
            .where(PromptContractVersion.template_key == manifest["template_key"])
            .where(PromptContractVersion.template_version == manifest["template_version"])
        ).one_or_none()
        values = {
            "input_contract": dict(manifest["input_contract"]),
            "output_contract": dict(manifest["output_contract"]),
            "schema_ref": manifest["schema_ref"],
            "schema_version": manifest["schema_version"],
            "status": manifest["status"],
        }
        if contract is None:
            contract = PromptContractVersion(
                agent_key=manifest["agent_key"],
                template_key=manifest["template_key"],
                template_version=manifest["template_version"],
                **values,
            )
            self.session.add(contract)
        else:
            for key, value in values.items():
                setattr(contract, key, value)
        return contract

    def _upsert_eval_cases(self) -> int:
        count = 0
        for case_data in self.repository.load_eval_cases():
            count += 1
            case = self.session.scalars(select(PromptEvaluationCase).where(PromptEvaluationCase.case_key == case_data["case_key"])).one_or_none()
            values = {
                "agent_key": case_data["agent_key"],
                "template_key": case_data["template_key"],
                "template_version": case_data["template_version"],
                "input_fixture_ref": case_data["input_fixture_ref"],
                "expected_outcome": case_data["expected_outcome"],
                "pass_criteria": case_data["pass_criteria"],
                "status": case_data.get("status", "ACTIVE"),
            }
            if case is None:
                self.session.add(PromptEvaluationCase(case_key=case_data["case_key"], **values))
            else:
                for key, value in values.items():
                    setattr(case, key, value)
        return count


def build_channel_contract_from_profile(session: Session, profile_version: ChannelProfileVersion) -> dict[str, Any]:
    channel = session.get(ChannelWorkspace, profile_version.channel_workspace_id)
    profile_input = profile_version.profile_input
    market_locale = {
        "primary_market": profile_input.get("target_market") or (channel.target_market if channel else None),
        "secondary_markets": channel.target_regions if channel else [],
        "audience_locale": channel.primary_region if channel else None,
        "content_language": channel.primary_language if channel else None,
        "operator_language": (channel.metadata_ or {}).get("operator_language") if channel else None,
        "timezone": channel.primary_timezone if channel else None,
        "currency": None,
        "measurement_units": None,
        "date_format": None,
        "cultural_style": None,
        "market_examples_preference": None,
        "regulatory_sensitivity": None,
        "market_locale_context_status": "KNOWN" if (profile_input.get("target_market") and channel and channel.primary_language) else "PARTIAL",
    }
    contract_status = "COMPLETE" if market_locale["market_locale_context_status"] == "KNOWN" and market_locale["operator_language"] else "PARTIAL"
    return {
        "channel_identity": {
            "channel_name": profile_input.get("display_name") or (channel.name if channel else None),
            "channel_type": "YOUTUBE_CHANNEL",
            "niche": profile_input.get("template_key"),
            "positioning": profile_input.get("audience_segment"),
            "brand_promise": None,
            "platform_targets": profile_input.get("platform_strategy"),
            "series_plan": profile_input.get("series_plan", []),
        },
        "target_audience": {
            "primary_persona": profile_input.get("audience_segment"),
            "audience_level": None,
            "pain_points": [],
            "desired_outcome": None,
        },
        "market_locale": market_locale,
        "editorial_strategy": {
            "content_pillars": profile_input.get("content_pillars", []),
            "allowed_angles": [],
            "forbidden_angles": [],
            "claim_style": profile_input.get("evidence_requirement"),
            "allowed_topics": [],
            "forbidden_topics": [],
        },
        "format_policy": profile_input.get("format_strategy", {}),
        "voice_style": profile_input.get("voice_style", {}),
        "platform_strategy": {
            **(profile_input.get("platform_strategy") or {}),
            "youtube_is_learning_authority": True,
            "auto_publish_allowed": False,
            "studio_scraping_allowed": False,
        },
        "media_policy": {
            "voice_provider": "ELEVENLABS",
            "ai_hero_provider": "GOOGLE_VERTEX_VEO",
            "ai_hero_model_id": "veo-3.1-fast-generate-001",
            "ai_hero_allowed_durations_seconds": [4, 6, 8],
            "ai_hero_default_duration_seconds": 8,
            "ai_hero_audio": False,
            "renderer": "CREATOMATE_GROWTH_10K_LIGHT_ONLY",
            "storage_archive": "GOOGLE_DRIVE",
        },
        "rights_policy": profile_input.get("policies", {}),
        "budget_policy": {
            "monthly_budget_usd": None,
            "cost_sensitivity": None,
            "avoid_unnecessary_ai_hero": True,
            "prefer_reuse_safe_assets": True,
            "exact_cost_claim_requires_provider_snapshot": True,
        },
        "learning_policy": {
            "authority": "YOUTUBE",
            "min_evidence_required": profile_input.get("evidence_requirement"),
            "auto_promote_learning": False,
            "config_mutation_by_agent_allowed": False,
            "weak_evidence_action": "REVIEW_REQUIRED",
        },
        "forbidden_behavior": list(FORBIDDEN_BEHAVIOR_CODES),
        "contract_status": contract_status,
    }


def prompt_template_hash(
    *,
    system_prompt: str,
    user_template: str,
    output_schema_ref: str,
    template_version: str,
    common_skill_refs: list[str],
) -> str:
    return sha256_text(
        "\n".join(
            [
                normalize_text(system_prompt),
                normalize_text(user_template),
                output_schema_ref,
                template_version,
                canonical_json(sorted(common_skill_refs)),
            ]
        )
    )


def prompt_context_hash(
    *,
    render_vars: dict[str, Any],
    channel_profile_version_id: uuid.UUID | None,
    compiled_policy_snapshot_id: uuid.UUID | None,
    channel_contract_json: dict[str, Any] | None,
    market_locale_context_json: dict[str, Any] | None,
    artifact_refs: list[dict[str, Any]],
) -> str:
    payload = {
        "render_vars": render_vars,
        "channel_profile_version_id": str(channel_profile_version_id) if channel_profile_version_id else None,
        "compiled_policy_snapshot_id": str(compiled_policy_snapshot_id) if compiled_policy_snapshot_id else None,
        "channel_contract_hash": sha256_text(canonical_json(channel_contract_json)),
        "market_locale_context_hash": sha256_text(canonical_json(market_locale_context_json)),
        "artifact_refs": sorted(artifact_refs, key=lambda item: canonical_json(item)),
    }
    return sha256_text(canonical_json(payload))


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    missing = sorted(set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", rendered)))
    if missing:
        raise ValidationFailureError(f"prompt template missing render vars: {missing}")
    return rendered


def parse_json_with_safe_repair(raw_output: str | dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if isinstance(raw_output, dict):
        return raw_output, []
    attempts: list[dict[str, Any]] = []
    candidates = [raw_output]
    stripped = raw_output.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
        candidates.append(stripped)
        attempts.append({"repair_type": "strip_code_fence", "semantic_change_allowed": False})
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
        attempts.append({"repair_type": "trim_to_json_object", "semantic_change_allowed": False})
    candidates.append(re.sub(r",\s*([}\]])", r"\1", candidates[-1]))
    attempts.append({"repair_type": "remove_trailing_commas", "semantic_change_allowed": False})
    for index, candidate in enumerate(candidates[:3]):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, attempts[: max(0, index)]
        return {"value": parsed}, attempts[: max(0, index)]
    return None, attempts[:2]


def validate_base_envelope(parsed: dict[str, Any], *, schema: dict[str, Any], expected_agent_key: str) -> dict[str, Any]:
    errors: list[str] = []
    missing = sorted(ENVELOPE_REQUIRED_FIELDS - set(parsed))
    unknown = sorted(set(parsed) - ENVELOPE_REQUIRED_FIELDS)
    if missing:
        errors.append(f"Missing required fields: {missing}")
    if unknown or schema.get("additionalProperties") is False and unknown:
        errors.append(f"Unknown fields are not allowed: {unknown}")
    if parsed.get("agent_key") != expected_agent_key:
        errors.append("agent_key does not match validation request")
    if parsed.get("status") not in ENVELOPE_ALLOWED_STATUS:
        errors.append("status is not allowed")
    if parsed.get("confidence_label") not in ENVELOPE_ALLOWED_CONFIDENCE:
        errors.append("confidence_label is not allowed")
    if parsed.get("risk_level") not in ENVELOPE_ALLOWED_RISK:
        errors.append("risk_level is not allowed")
    if not isinstance(parsed.get("evidence_refs"), list):
        errors.append("evidence_refs must be a list")
    if not isinstance(parsed.get("limitations"), list):
        errors.append("limitations must be a list")
    if not isinstance(parsed.get("technical_appendix"), dict):
        errors.append("technical_appendix must be an object")
    if not isinstance(parsed.get("operator_summary_vi"), str) or not parsed.get("operator_summary_vi"):
        errors.append("operator_summary_vi is required")
    return {
        "valid": not errors,
        "errors": errors,
        "schema_ref": BASE_SCHEMA_REF,
        "schema_version": schema.get("x-vcos-schema-version", DEFAULT_TEMPLATE_VERSION),
        "safe_json_repair_policy": "syntax_shape_only_no_semantic_change",
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def normalize_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").strip().split("\n"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
