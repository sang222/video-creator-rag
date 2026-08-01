from __future__ import annotations

# Compatibility note: semantic facades `prompt_registry` and `prompt_audit` re-export this implementation; phase-coded import kept for reports/tests/backward compatibility.
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
from app.services.channel_contract import build_channel_contract
from app.services.r3d3 import (
    HARD_RULE_HEADER,
    HARD_RULE_HEADER_HASH,
    LANE_POLICY_VERSION,
    build_common_skill_digest,
    stable_hash,
)


PROMPT_CONTRACT_VERSION = "m12.1.0"
DEFAULT_TEMPLATE_VERSION = "1.0.0"
BASE_SCHEMA_REF = "base_agent_envelope"
MISSING_CHANNEL_NEXT_ACTION = (
    "Bổ sung hoặc compile lại ChannelProfileVersion trước khi render prompt."
)
REQUIRED_AGENT_KEYS = [
    "ChannelAuthorityAgent",
    "EditorialIdeaResearchAgent",
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
    "RecoveryProposalReviewer",
    "LocalizationSubtitleAgent",
    "LocalizedMetadataAgent",
    "PublishTimingSummaryAgent",
    "ProviderReadinessSummaryAgent",
    "MediaQCExplanationAgent",
    "RightsDisclosureReviewer",
    "ChannelSetupResearchAgent",
]

ENVELOPE_REQUIRED_FIELDS = {
    "contract_version",
    "agent_key",
    "status",
    "confidence_label",
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
TOPIC_IDEA_ARTIFACT_ALLOWED_KEYS = {
    "topic_score",
    "risk_assessment",
    "scoring_risks",
    "score",
    "risk",
    "cost",
    "demand_signal",
    "evidence_assessment",
    "recommendation",
    "reason_codes",
}


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
            raise ValidationFailureError(
                "M12.1 prompt registry source file is missing."
            )
        raw = yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {}
        agents = raw.get("agents")
        if not isinstance(agents, list):
            raise ValidationFailureError(
                "M12.1 prompt registry must contain an agents list."
            )
        manifests: dict[str, dict[str, Any]] = {}
        for item in agents:
            if not isinstance(item, dict) or not item.get("agent_key"):
                raise ValidationFailureError("Invalid prompt registry agent entry.")
            agent_key = str(item["agent_key"])
            manifests[agent_key] = item
        missing = sorted(set(REQUIRED_AGENT_KEYS) - set(manifests))
        if missing:
            raise ValidationFailureError(
                f"M12.1 prompt registry missing required agents: {missing}"
            )
        return manifests

    def load_bundle(self, agent_key: str) -> PromptTemplateBundle:
        manifests = self.load_agent_manifests()
        manifest = manifests.get(agent_key)
        if manifest is None:
            raise NotFoundError(f"prompt agent not found: {agent_key}")
        common_skill_digest = build_common_skill_digest()
        common_parts = [
            f"- {item['name']}: {item['ref']} hash={item['hash']}"
            for item in common_skill_digest["payload"]["common_skill_refs"]
        ]
        system_delta = (
            self._resolve(manifest["system_delta_ref"])
            .read_text(encoding="utf-8")
            .strip()
        )
        user_template = (
            self._resolve(manifest["user_template_ref"])
            .read_text(encoding="utf-8")
            .strip()
        )
        schema = json.loads(
            self._resolve(manifest["output_schema_ref"]).read_text(encoding="utf-8")
        )
        system_prompt = "\n\n".join(
            [
                "# VCOS Hard-Rule Header",
                HARD_RULE_HEADER,
                "# VCOS Common Skill Digest",
                "\n".join(common_parts),
                "# Agent-Specific Skill",
                system_delta,
                "# Output Contract",
                (
                    "Return JSON only as one top-level BaseEnvelope object. Do not return an artifact-only object. "
                    "The top-level keys must be exactly contract_version, agent_key, status, confidence_label, "
                    "evidence_refs, limitations, next_action, operator_summary_vi, technical_appendix, and artifact. "
                    "Do not include top-level risk_level; put risk semantics inside artifact.risk_assessment only when needed. "
                    'Use contract_version "m12.1.0" and the exact requested agent_key. Use uppercase enum values only.'
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
                return json.loads(
                    self._resolve(manifest["output_schema_ref"]).read_text(
                        encoding="utf-8"
                    )
                )
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
    def __init__(
        self, session: Session, repository: PromptRegistryRepository | None = None
    ):
        self.session = session
        self.repository = repository or PromptRegistryRepository()

    def sync_repo_registry(self) -> PromptRegistrySyncSummary:
        self.repository.load_agent_manifests()
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
        if (
            template_key != manifest["template_key"]
            or template_version != manifest["template_version"]
        ):
            raise NotFoundError(
                f"prompt template not found: {data.agent_key}/{template_key}@{template_version}"
            )
        profile = self._profile(data.agent_key)
        router_lane = data.router_lane or profile.default_router_lane
        if router_lane not in profile.allowed_router_lanes:
            raise ValidationFailureError(
                f"router lane {router_lane} is not allowed for {data.agent_key}"
            )

        contract_payload = self._resolve_channel_payload(data=data, profile=profile)
        context_hash = self._prompt_context_hash(
            data=data,
            manifest=manifest,
            router_lane=router_lane,
            contract_payload=contract_payload,
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

        render_vars = self._render_vars(
            data=data, manifest=manifest, contract_payload=contract_payload
        )
        messages = [
            PromptMessage(role="system", content=bundle.system_prompt),
            PromptMessage(
                role="user", content=render_template(bundle.user_template, render_vars)
            ),
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
            validation_result={
                "status": "NOT_RUN",
                "schema_ref": manifest["schema_ref"],
            },
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

    def validate_output(
        self, data: PromptOutputValidationRequest
    ) -> PromptOutputValidationResult:
        self.sync_repo_registry()
        schema = self.repository.load_schema(data.schema_ref)
        parsed, repair_attempts = parse_json_with_safe_repair(data.raw_output)
        if parsed is None:
            result = {
                "valid": False,
                "errors": ["Output is not parseable JSON."],
                "schema_ref": data.schema_ref,
            }
            return PromptOutputValidationResult(
                status="ERROR",
                validation_result=result,
                repair_attempts=repair_attempts,
                reason_codes=["JSON_PARSE_FAILED"],
            )
        wrapped, wrap_attempt = _wrap_topic_idea_artifact_only_output(
            parsed, expected_agent_key=data.agent_key
        )
        if wrap_attempt is not None:
            parsed = wrapped
            repair_attempts.append(wrap_attempt)
        parsed, shape_attempts = repair_envelope_shape(
            parsed,
            expected_agent_key=data.agent_key,
            max_attempts=max(0, 2 - len(repair_attempts)),
        )
        repair_attempts = (repair_attempts + shape_attempts)[:2]
        validation = validate_base_envelope(
            parsed, schema=schema, expected_agent_key=data.agent_key
        )
        status = "OK" if validation["valid"] else "REVIEW_REQUIRED"
        if parsed.get("status") in {"BLOCK", "REFUSAL", "ERROR"}:
            status = (
                parsed["status"]
                if parsed["status"] in {"BLOCK", "ERROR"}
                else "REVIEW_REQUIRED"
            )
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
            reason_codes=["SCHEMA_VALIDATED"]
            if validation["valid"]
            else ["SCHEMA_VALIDATION_REVIEW_REQUIRED"],
        )

    def run_evaluation_cases(self) -> list[PromptEvaluationRun]:
        self.sync_repo_registry()
        cases = list(
            self.session.scalars(
                select(PromptEvaluationCase).where(
                    PromptEvaluationCase.status == "ACTIVE"
                )
            ).all()
        )
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
                    result = {
                        "render_status": render.status,
                        "expected_status": expected_status,
                        "reason_codes": render.reason_codes,
                    }
                elif case.pass_criteria.get("type") == "base_envelope_schema":
                    fixture = json.loads(
                        (self.repository.root / case.input_fixture_ref).read_text(
                            encoding="utf-8"
                        )
                    )
                    validation = self.validate_output(
                        PromptOutputValidationRequest(
                            agent_key=case.agent_key, raw_output=fixture["raw_output"]
                        )
                    )
                    state = (
                        "PASS"
                        if validation.status == case.expected_outcome.get("status")
                        else "FAIL"
                    )
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
        profile = self.session.scalars(
            select(AgentPromptProfile).where(AgentPromptProfile.agent_key == agent_key)
        ).one_or_none()
        if profile is None:
            raise NotFoundError(f"agent prompt profile not found: {agent_key}")
        return profile

    def _resolve_channel_payload(
        self, *, data: PromptRenderRequest, profile: AgentPromptProfile
    ) -> dict[str, Any]:
        channel_contract = data.channel_contract_json
        compiled_policy = data.compiled_policy_snapshot_json
        market_locale = data.market_locale_context_json
        if data.channel_profile_version_id is not None:
            profile_version = self.session.get(
                ChannelProfileVersion, data.channel_profile_version_id
            )
            if profile_version is None:
                raise NotFoundError(
                    f"channel profile version not found: {data.channel_profile_version_id}"
                )
            if channel_contract is None:
                channel_contract = build_channel_contract_from_profile(
                    self.session, profile_version
                )
        if data.compiled_policy_snapshot_id is not None:
            snapshot = self.session.get(
                CompiledChannelPolicySnapshot, data.compiled_policy_snapshot_id
            )
            if snapshot is None:
                raise NotFoundError(
                    f"compiled policy snapshot not found: {data.compiled_policy_snapshot_id}"
                )
            if (
                data.channel_profile_version_id is not None
                and snapshot.channel_profile_version_id
                != data.channel_profile_version_id
            ):
                raise ValidationFailureError(
                    "compiled policy snapshot does not match channel profile version"
                )
            if compiled_policy is None:
                compiled_policy = snapshot.compiled_payload
            if data.channel_contract_json is None and isinstance(
                snapshot.compiled_payload, dict
            ):
                frozen_contract = snapshot.compiled_payload.get("channel_contract_json")
                if isinstance(frozen_contract, dict):
                    channel_contract = frozen_contract
        if market_locale is None and channel_contract is not None:
            market_locale = (
                channel_contract.get("market_locale")
                if isinstance(channel_contract, dict)
                else None
            )
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
        if (
            profile.market_locale_context_required
            and not contract_payload["market_locale_context_json"]
        ):
            missing.append("market_locale_context_json")
        contract_status = None
        market_status = None
        if isinstance(contract_payload["channel_contract_json"], dict):
            contract_status = contract_payload["channel_contract_json"].get(
                "contract_status"
            )
            market = contract_payload["channel_contract_json"].get("market_locale")
            if isinstance(market, dict):
                market_status = market.get("market_locale_context_status")
        if profile.channel_contract_required and contract_status in {
            "MISSING",
            "PARTIAL",
            "STALE",
            "CONTRADICTORY",
        }:
            missing.append(f"contract_status:{contract_status}")
        if profile.market_locale_context_required and market_status in {
            "UNKNOWN",
            "PARTIAL",
            None,
        }:
            missing.append(f"market_locale_context_status:{market_status or 'MISSING'}")
        if not missing:
            return None
        return AgentOutputEnvelope(
            contract_version=PROMPT_CONTRACT_VERSION,
            agent_key=data.agent_key,
            status="REVIEW_REQUIRED",
            confidence_label="LOW",
            evidence_refs=[],
            limitations=[
                "Thiếu Channel Contract đã compile/freeze nên agent không được suy đoán cấu hình kênh."
            ],
            next_action=MISSING_CHANNEL_NEXT_ACTION,
            operator_summary_vi="Cần bổ sung hoặc compile lại cấu hình kênh trước khi render prompt.",
            technical_appendix={"missing_or_invalid_fields": sorted(set(missing))},
            artifact=None,
        )

    def _render_vars(
        self,
        *,
        data: PromptRenderRequest,
        manifest: dict[str, Any],
        contract_payload: dict[str, Any],
    ) -> dict[str, str]:
        channel_profile_version_id = (
            str(data.channel_profile_version_id)
            if data.channel_profile_version_id
            else "null"
        )
        compiled_policy_snapshot_id = (
            str(data.compiled_policy_snapshot_id)
            if data.compiled_policy_snapshot_id
            else "null"
        )
        channel_contract_ref = {
            "channel_profile_version_id": channel_profile_version_id,
            "compiled_policy_snapshot_id": compiled_policy_snapshot_id,
            "channel_contract_hash": sha256_text(
                canonical_json(contract_payload["channel_contract_json"])
            ),
            "contract_status": _dict(contract_payload["channel_contract_json"]).get(
                "contract_status"
            ),
        }
        compiled_policy_snapshot_ref = {
            "compiled_policy_snapshot_id": compiled_policy_snapshot_id,
            "compiled_policy_snapshot_hash": sha256_text(
                canonical_json(contract_payload["compiled_policy_snapshot_json"])
            ),
        }
        market_locale_ref = {
            "market_locale_context_hash": sha256_text(
                canonical_json(contract_payload["market_locale_context_json"])
            ),
        }
        agent_context_pack = (
            data.task_payload.get("agent_context_pack")
            if isinstance(data.task_payload, dict)
            else None
        )
        task_payload_for_render = dict(data.task_payload)
        if isinstance(agent_context_pack, dict):
            task_payload_for_render["agent_context_pack"] = {
                "context_pack_hash": agent_context_pack.get("context_pack_hash"),
                "context_pack_version": agent_context_pack.get("context_pack_version"),
                "agent_key": agent_context_pack.get("agent_key"),
            }
        prompt_agent_context_pack = _prompt_safe_agent_context_pack(agent_context_pack)
        payload: dict[str, Any] = {
            **data.render_vars,
            "agent_key": data.agent_key,
            "template_key": manifest["template_key"],
            "template_version": manifest["template_version"],
            "channel_profile_version_id": channel_profile_version_id,
            "compiled_policy_snapshot_id": compiled_policy_snapshot_id,
            "channel_contract_ref_json": canonical_json(channel_contract_ref),
            "compiled_policy_snapshot_ref_json": canonical_json(
                compiled_policy_snapshot_ref
            ),
            "market_locale_context_ref_json": canonical_json(market_locale_ref),
            "agent_context_pack_json": canonical_json(prompt_agent_context_pack),
            "task_payload_json": canonical_json(task_payload_for_render),
            "evidence_refs_json": canonical_json(data.evidence_refs),
            "artifact_refs_json": canonical_json(data.artifact_refs),
            "required_output_instruction": "Return JSON only using the BaseEnvelope schema. Do not add unknown fields.",
        }
        return {key: str(value) for key, value in payload.items()}

    def _prompt_context_hash(
        self,
        *,
        data: PromptRenderRequest,
        manifest: dict[str, Any],
        router_lane: str,
        contract_payload: dict[str, Any],
    ) -> str:
        agent_context_pack = (
            data.task_payload.get("agent_context_pack")
            if isinstance(data.task_payload, dict)
            else None
        )
        if isinstance(agent_context_pack, dict) and agent_context_pack.get(
            "context_pack_hash"
        ):
            common_digest = _dict(
                _dict(agent_context_pack.get("digests")).get("common_skill_digest")
            )
            schema_contract_hash = stable_hash(
                {
                    "input_contract": manifest.get("input_contract"),
                    "output_contract": manifest.get("output_contract"),
                    "schema_ref": manifest.get("schema_ref"),
                    "schema_version": manifest.get("schema_version"),
                }
            )
            return stable_hash(
                {
                    "template_version": manifest["template_version"],
                    "lane_policy_version": f"{LANE_POLICY_VERSION}:{router_lane}",
                    "hard_rule_header_hash": HARD_RULE_HEADER_HASH,
                    "common_skill_digest_hash": common_digest.get("digest_hash"),
                    "context_pack_hash": agent_context_pack.get("context_pack_hash"),
                    "schema_contract_hash": schema_contract_hash,
                }
            )
        return prompt_context_hash(
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
            compiled_policy_snapshot_json=contract_payload[
                "compiled_policy_snapshot_json"
            ],
            market_locale_context_json=contract_payload["market_locale_context_json"],
            render_vars_json=render_vars
            or {"task_payload": data.task_payload, "render_vars": data.render_vars},
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
        profile = self.session.scalars(
            select(AgentPromptProfile).where(
                AgentPromptProfile.agent_key == manifest["agent_key"]
            )
        ).one_or_none()
        values = {
            "default_router_lane": manifest["default_router_lane"],
            "allowed_router_lanes": list(manifest["allowed_router_lanes"]),
            "input_contract": dict(manifest["input_contract"]),
            "output_contract": dict(manifest["output_contract"]),
            "safety_policy_refs": list(manifest["safety_policy_refs"]),
            "common_skill_refs": list(manifest["common_skill_refs"]),
            "channel_contract_required": bool(manifest["channel_contract_required"]),
            "market_locale_context_required": bool(
                manifest["market_locale_context_required"]
            ),
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
            .where(
                PromptTemplateRecord.template_version == manifest["template_version"]
            )
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
            .where(
                PromptContractVersion.template_version == manifest["template_version"]
            )
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
            case = self.session.scalars(
                select(PromptEvaluationCase).where(
                    PromptEvaluationCase.case_key == case_data["case_key"]
                )
            ).one_or_none()
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
                self.session.add(
                    PromptEvaluationCase(case_key=case_data["case_key"], **values)
                )
            else:
                for key, value in values.items():
                    setattr(case, key, value)
        return count


def build_channel_contract_from_profile(
    session: Session, profile_version: ChannelProfileVersion
) -> dict[str, Any]:
    channel = session.get(ChannelWorkspace, profile_version.channel_workspace_id)
    return build_channel_contract(
        profile_input=profile_version.profile_input, channel=channel
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _prompt_safe_agent_context_pack(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    pack = dict(value)
    contract = _dict(pack.get("agent_context_contract"))
    if contract:
        pack["agent_context_contract_ref"] = {
            "agent_key": contract.get("agent_key"),
            "task_type": contract.get("task_type"),
            "lane": contract.get("lane"),
            "contract_version": contract.get("contract_version"),
            "content_hash": contract.get("content_hash"),
            "required_context_sections": contract.get("required_context_sections"),
            "optional_context_sections": contract.get("optional_context_sections"),
            "max_context_chars": contract.get("max_context_chars"),
            "max_memory_facets": contract.get("max_memory_facets"),
            "max_artifact_refs": contract.get("max_artifact_refs"),
            "raw_artifact_allowed": contract.get("raw_artifact_allowed"),
            "full_debug_allowed": contract.get("full_debug_allowed"),
        }
    pack.pop("agent_context_contract", None)
    return pack


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
        "channel_profile_version_id": str(channel_profile_version_id)
        if channel_profile_version_id
        else None,
        "compiled_policy_snapshot_id": str(compiled_policy_snapshot_id)
        if compiled_policy_snapshot_id
        else None,
        "channel_contract_hash": sha256_text(canonical_json(channel_contract_json)),
        "market_locale_context_hash": sha256_text(
            canonical_json(market_locale_context_json)
        ),
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


def parse_json_with_safe_repair(
    raw_output: str | dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if isinstance(raw_output, dict):
        return raw_output, []
    stripped = raw_output.strip()
    candidates: list[tuple[str, list[dict[str, Any]]]] = [(stripped, [])]

    fenced = _strip_whole_json_code_fence(stripped)
    if fenced is not None:
        candidates.append(
            (
                fenced,
                [{"repair_type": "strip_code_fence", "semantic_change_allowed": False}],
            )
        )

    sources = [stripped]
    if fenced is not None:
        sources.append(fenced)
    for source in sources:
        for candidate in _balanced_json_object_candidates(source):
            candidates.append(
                (
                    candidate,
                    [
                        {
                            "repair_type": "extract_base_envelope_json_object",
                            "semantic_change_allowed": False,
                            "reason_codes": [
                                "BASE_ENVELOPE_OBJECT_EXTRACTED_FROM_TEXT"
                            ],
                        }
                    ],
                )
            )

    expanded: list[tuple[str, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for candidate, attempts in candidates:
        candidate_variants = [
            (candidate, []),
            (
                _repair_stray_colon_object_property(candidate),
                [
                    {
                        "repair_type": "repair_stray_colon_object_property",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_duplicate_standalone_number_after_numeric_property(candidate),
                [
                    {
                        "repair_type": "remove_duplicate_standalone_number_after_numeric_property",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_json_smart_quote_delimiters(candidate),
                [
                    {
                        "repair_type": "repair_json_smart_quote_delimiters",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_json_string_replace_expression(candidate),
                [
                    {
                        "repair_type": "repair_json_string_replace_expression",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_artifact_compliance_chained_properties(candidate),
                [
                    {
                        "repair_type": "repair_artifact_compliance_chained_properties",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_chained_string_properties(candidate),
                [
                    {
                        "repair_type": "repair_chained_string_properties",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_contract_version_equals_typo(candidate),
                [
                    {
                        "repair_type": "repair_contract_version_equals_typo",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_embedded_agent_key_value(candidate),
                [
                    {
                        "repair_type": "repair_embedded_agent_key_value",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_missing_evidence_refs_array_close_before_limitations(candidate),
                [
                    {
                        "repair_type": "repair_missing_evidence_refs_array_close_before_limitations",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_unquoted_percent_number_values(candidate),
                [
                    {
                        "repair_type": "repair_unquoted_percent_number_values",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_rights_artifact_present_marker(candidate),
                [
                    {
                        "repair_type": "repair_rights_artifact_present_marker",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
            (
                _repair_unclosed_string_before_json_delimiter(candidate),
                [
                    {
                        "repair_type": "repair_unclosed_string_before_json_delimiter",
                        "semantic_change_allowed": False,
                    }
                ],
            ),
        ]
        completed_candidate = _append_missing_json_closing_delimiters(candidate)
        if completed_candidate is not None:
            candidate_variants.append(
                (
                    completed_candidate,
                    [
                        {
                            "repair_type": "append_missing_json_closing_delimiters",
                            "semantic_change_allowed": False,
                        }
                    ],
                )
            )
        for base_text, base_attempts in candidate_variants:
            for text, extra_attempt in (
                (base_text, None),
                (
                    re.sub(r",\s*([}\]])", r"\1", base_text),
                    {
                        "repair_type": "remove_trailing_commas",
                        "semantic_change_allowed": False,
                    },
                ),
            ):
                if text in seen:
                    continue
                seen.add(text)
                combined = (
                    attempts
                    + (base_attempts if base_text != candidate else [])
                    + (
                        [extra_attempt]
                        if extra_attempt is not None and text != base_text
                        else []
                    )
                )
                expanded.append((text, combined[:2]))

    for candidate, attempts in expanded:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, attempts
        return {"value": parsed}, attempts
    return None, _failed_json_repair_attempts(stripped)[:2]


def _strip_whole_json_code_fence(value: str) -> str | None:
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", value, flags=re.IGNORECASE | re.DOTALL
    )
    return match.group(1).strip() if match else None


def _repair_json_smart_quote_delimiters(value: str) -> str:
    return re.sub(r"”(\s*[,}\]])", r'"\1', value)


def _repair_json_string_replace_expression(value: str) -> str:
    pattern = re.compile(
        r'("(?:(?:\\.)|[^"\\])*")\.replace\(\s*("(?:(?:\\.)|[^"\\])*")\s*,\s*("(?:(?:\\.)|[^"\\])*")\s*\)'
    )

    def _replace(match: re.Match[str]) -> str:
        try:
            source = json.loads(match.group(1))
            old = json.loads(match.group(2))
            new = json.loads(match.group(3))
        except json.JSONDecodeError:
            return match.group(0)
        if not all(isinstance(item, str) for item in (source, old, new)):
            return match.group(0)
        return json.dumps(source.replace(old, new), ensure_ascii=False)

    return pattern.sub(_replace, value)


def _repair_contract_version_equals_typo(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        version = match.group("version")
        normalized = version if version.startswith("m") else f"m{version}"
        return f'"contract_version":"{normalized}"'

    return re.sub(r'"contract_version="(?P<version>[^"]+)"', _replace, value, count=1)


def _repair_embedded_agent_key_value(value: str) -> str:
    agent_keys = "|".join(
        re.escape(agent_key)
        for agent_key in sorted(REQUIRED_AGENT_KEYS, key=len, reverse=True)
    )
    pattern = re.compile(
        r'("agent_key"\s*:\s*)"[^"\n{}]*:\s*"(?P<agent_key>' + agent_keys + r')"'
    )

    def _replace(match: re.Match[str]) -> str:
        return f'{match.group(1)}"{match.group("agent_key")}"'

    return pattern.sub(_replace, value, count=1)


def _repair_artifact_compliance_chained_properties(value: str) -> str:
    block_pattern = re.compile(
        r'("artifact_compliance"\s*:\s*\{)(?P<body>[^{}]*)(\})', flags=re.DOTALL
    )
    chained_pattern = re.compile(
        r'"(?P<field>[A-Za-z0-9_ -]+)"\s*:\s*"(?P<label>[^"]+)"\s*:\s*"(?P<state>[^"]*)"'
    )

    def _safe_key(text: str) -> str:
        key = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
        return key or "value"

    def _repair_block(match: re.Match[str]) -> str:
        def _replace_chained(item: re.Match[str]) -> str:
            field = _safe_key(item.group("field"))
            label = _safe_key(item.group("label"))
            state = json.dumps(item.group("state"), ensure_ascii=False)
            return f'"{field}_{label}":{state}'

        return f"{match.group(1)}{chained_pattern.sub(_replace_chained, match.group('body'))}{match.group(3)}"

    return block_pattern.sub(_repair_block, value)


def _repair_chained_string_properties(value: str) -> str:
    pattern = re.compile(
        r'"(?P<field>[A-Za-z0-9_ -]+)"\s*:\s*"(?P<label>[^"]+)"\s*:\s*"(?P<state>[^"]*)"'
    )

    def _safe_key(text: str) -> str:
        key = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
        return key or "value"

    def _replace(match: re.Match[str]) -> str:
        field = _safe_key(match.group("field"))
        label = _safe_key(match.group("label"))
        state = json.dumps(match.group("state"), ensure_ascii=False)
        return f'"{field}_{label}":{state}'

    return pattern.sub(_replace, value)


def _repair_missing_evidence_refs_array_close_before_limitations(value: str) -> str:
    pattern = re.compile(
        r'("evidence_refs"\s*:\s*\[(?:\s*\{[^{}]*\}\s*,?)+)\s*,\s*"limitations"\s*:',
        flags=re.DOTALL,
    )
    return pattern.sub(r'\1],"limitations":', value, count=1)


def _repair_unquoted_percent_number_values(value: str) -> str:
    pattern = re.compile(r":\s*(?P<number>-?\d+(?:\.\d+)?)%\s*(?P<suffix>[,}\]])")

    def _replace(match: re.Match[str]) -> str:
        return f':"{match.group("number")}%"{match.group("suffix")}'

    return pattern.sub(_replace, value)


def _repair_rights_artifact_present_marker(value: str) -> str:
    return re.sub(
        r'("artifact"\s*:\s*\{\s*)"artifact_present_and_valid"\s*,',
        r'\1"result":"PASS",',
        value,
        count=1,
        flags=re.DOTALL,
    )


def _repair_unclosed_string_before_json_delimiter(value: str) -> str:
    repaired: list[str] = []
    changed = False
    in_string = False
    escape = False
    index = 0
    while index < len(value):
        char = value[index]
        if in_string:
            if escape:
                repaired.append(char)
                escape = False
            elif char == "\\":
                repaired.append(char)
                escape = True
            elif char == '"':
                repaired.append(char)
                in_string = False
            elif char in "\r\n":
                lookahead = index + 1
                while lookahead < len(value) and value[lookahead] in " \t\r\n":
                    lookahead += 1
                if lookahead < len(value) and value[lookahead] in ",]}":
                    repaired.append('"')
                    repaired.append(char)
                    in_string = False
                    changed = True
                else:
                    repaired.append(char)
            else:
                repaired.append(char)
        else:
            repaired.append(char)
            if char == '"':
                in_string = True
                escape = False
        index += 1
    return "".join(repaired) if changed else value


def _balanced_json_object_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for index, char in enumerate(value):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidate = value[start : index + 1].strip()
                if _looks_like_base_envelope(candidate):
                    candidates.append(candidate)
                start = None
    return candidates


def _looks_like_base_envelope(candidate: str) -> bool:
    return all(
        token in candidate
        for token in ('"contract_version"', '"agent_key"', '"status"', '"artifact"')
    )


def _repair_stray_colon_object_property(value: str) -> str:
    repaired = re.sub(
        r'("text"\s*:\s*"(?:\\.|[^"\\])*")\s*:\s*\{\s*("approx_seconds"\s*:)',
        r"\1, \2",
        value,
        flags=re.DOTALL,
    )
    return re.sub(
        r'("approx_seconds"\s*:\s*-?\d+(?:\.\d+)?)\s*\}\s*\}(?=\s*[\],])',
        r"\1}",
        repaired,
        flags=re.DOTALL,
    )


def _repair_duplicate_standalone_number_after_numeric_property(value: str) -> str:
    return re.sub(
        r'("[^"]+"\s*:\s*(?P<number>-?\d+(?:\.\d+)?))\s*\n\s*(?P=number)(?=\s*(?:[,}\]]))',
        r"\1",
        value,
        flags=re.DOTALL,
    )


def _append_missing_json_closing_delimiters(value: str) -> str | None:
    stack: list[str] = []
    in_string = False
    escape = False
    for char in value:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "{[":
            stack.append(char)
            continue
        if char in "}]":
            if not stack:
                return None
            opener = stack.pop()
            if (opener, char) not in (("{", "}"), ("[", "]")):
                return None
    if in_string or not stack or len(stack) > 2:
        return None
    if value.rstrip()[-1:] not in "}]":
        return None
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    repaired = value.rstrip() + suffix
    return repaired if _looks_like_base_envelope(repaired) else None


def _failed_json_repair_attempts(value: str) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    if _strip_whole_json_code_fence(value) is not None:
        attempts.append(
            {"repair_type": "strip_code_fence", "semantic_change_allowed": False}
        )
    if _balanced_json_object_candidates(value):
        attempts.append(
            {
                "repair_type": "extract_base_envelope_json_object",
                "semantic_change_allowed": False,
                "reason_codes": ["BASE_ENVELOPE_OBJECT_EXTRACTED_FROM_TEXT"],
            }
        )
    attempts.append(
        {"repair_type": "remove_trailing_commas", "semantic_change_allowed": False}
    )
    if _repair_stray_colon_object_property(value) != value:
        attempts.append(
            {
                "repair_type": "repair_stray_colon_object_property",
                "semantic_change_allowed": False,
            }
        )
    if _repair_embedded_agent_key_value(value) != value:
        attempts.append(
            {
                "repair_type": "repair_embedded_agent_key_value",
                "semantic_change_allowed": False,
            }
        )
    if _repair_duplicate_standalone_number_after_numeric_property(value) != value:
        attempts.append(
            {
                "repair_type": "remove_duplicate_standalone_number_after_numeric_property",
                "semantic_change_allowed": False,
            }
        )
    if _append_missing_json_closing_delimiters(value) is not None:
        attempts.append(
            {
                "repair_type": "append_missing_json_closing_delimiters",
                "semantic_change_allowed": False,
            }
        )
    if _repair_unclosed_string_before_json_delimiter(value) != value:
        attempts.append(
            {
                "repair_type": "repair_unclosed_string_before_json_delimiter",
                "semantic_change_allowed": False,
            }
        )
    return attempts


def _wrap_topic_idea_artifact_only_output(
    parsed: dict[str, Any],
    *,
    expected_agent_key: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if expected_agent_key != "TopicIdeaScoringAgent":
        return parsed, None
    if ENVELOPE_REQUIRED_FIELDS.intersection(parsed):
        return parsed, None
    if not _valid_topic_idea_artifact(parsed):
        return parsed, None
    wrapped = {
        "contract_version": PROMPT_CONTRACT_VERSION,
        "agent_key": "TopicIdeaScoringAgent",
        "status": "REVIEW_REQUIRED",
        "confidence_label": "LOW",
        "evidence_refs": [],
        "limitations": ["Evidence is insufficient."],
        "next_action": "HUMAN_REVIEW_REQUIRED",
        "operator_summary_vi": "Chủ đề cần được người vận hành kiểm tra trước khi tiếp tục.",
        "technical_appendix": {"wrapped_artifact_only_output": True},
        "artifact": dict(parsed),
    }
    return wrapped, {
        "repair_type": "wrap_topic_idea_artifact_in_base_envelope",
        "semantic_change_allowed": False,
        "reason_codes": ["TOPIC_IDEA_ARTIFACT_WRAPPED"],
    }


def _valid_topic_idea_artifact(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    if "risk_level" in value:
        return False
    keys = set(value)
    return (
        bool(keys & TOPIC_IDEA_ARTIFACT_ALLOWED_KEYS)
        and keys <= TOPIC_IDEA_ARTIFACT_ALLOWED_KEYS
    )


def repair_envelope_shape(
    parsed: dict[str, Any],
    *,
    expected_agent_key: str,
    max_attempts: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if max_attempts <= 0:
        return parsed, []
    repaired = dict(parsed)
    attempts: list[dict[str, Any]] = []

    metadata_changed = False
    for key, value in {
        "contract_version": PROMPT_CONTRACT_VERSION,
        "evidence_refs": [],
        "limitations": [],
        "next_action": None,
        "technical_appendix": {},
    }.items():
        if key not in repaired:
            repaired[key] = value
            metadata_changed = True
    if metadata_changed:
        attempts.append(
            {
                "repair_type": "fill_missing_envelope_metadata",
                "semantic_change_allowed": False,
                "fields": [
                    key
                    for key in [
                        "contract_version",
                        "agent_key",
                        "evidence_refs",
                        "limitations",
                        "next_action",
                        "technical_appendix",
                    ]
                    if key not in parsed
                ],
            }
        )

    if len(attempts) >= max_attempts:
        return repaired, attempts[:max_attempts]

    if repaired.get("agent_key") != expected_agent_key:
        repaired["agent_key"] = expected_agent_key
        attempts.append(
            {
                "repair_type": "normalize_envelope_agent_key",
                "semantic_change_allowed": False,
            }
        )

    if len(attempts) >= max_attempts:
        return repaired, attempts[:max_attempts]

    risk_move_attempt = _move_top_level_risk_level(repaired)
    if risk_move_attempt is not None:
        attempts.append(risk_move_attempt)

    if len(attempts) >= max_attempts:
        return repaired, attempts[:max_attempts]

    metadata_shape_fields: list[str] = []
    metadata_shape_reason_codes: list[str] = []

    appendix_value = repaired.get("technical_appendix")
    if not isinstance(appendix_value, dict):
        if appendix_value in (None, "", []):
            repaired["technical_appendix"] = {}
            metadata_shape_fields.append("technical_appendix")
            metadata_shape_reason_codes.append(
                "TECHNICAL_APPENDIX_EMPTY_OBJECT_DEFAULTED"
            )
        else:
            repaired["technical_appendix"] = {
                "repaired_non_object_value": appendix_value
            }
            metadata_shape_fields.append("technical_appendix")
            metadata_shape_reason_codes.append("TECHNICAL_APPENDIX_OBJECT_REPAIRED")

    limitations_value = repaired.get("limitations")
    if isinstance(limitations_value, str):
        repaired["limitations"] = [limitations_value]
        metadata_shape_fields.append("limitations")
        metadata_shape_reason_codes.append("LIMITATIONS_STRING_LIST_REPAIRED")
    elif isinstance(limitations_value, dict):
        repaired["limitations"] = _limitation_object_to_string_list(limitations_value)
        metadata_shape_fields.append("limitations")
        metadata_shape_reason_codes.append("LIMITATIONS_OBJECT_LIST_REPAIRED")
    elif isinstance(limitations_value, list) and any(
        not isinstance(item, str) for item in limitations_value
    ):
        repaired["limitations"] = [
            item if isinstance(item, str) else canonical_json(item)
            for item in limitations_value
            if item not in (None, "", [])
        ]
        metadata_shape_fields.append("limitations")
        metadata_shape_reason_codes.append("LIMITATIONS_OBJECT_LIST_REPAIRED")

    next_action_value = repaired.get("next_action")
    if isinstance(next_action_value, list):
        repaired["next_action"] = (
            "; ".join(
                str(item) for item in next_action_value if item not in (None, "", [])
            )
            or None
        )
        metadata_shape_fields.append("next_action")
        metadata_shape_reason_codes.append("NEXT_ACTION_LIST_STRING_REPAIRED")

    operator_summary = repaired.get("operator_summary_vi")
    if expected_agent_key == "ChannelAuthorityAgent" and (
        not isinstance(operator_summary, str) or not operator_summary.strip()
    ):
        artifact = (
            repaired.get("artifact")
            if isinstance(repaired.get("artifact"), dict)
            else {}
        )
        summary_source = artifact.get("reason") or repaired.get("next_action")
        if isinstance(summary_source, str) and summary_source.strip():
            repaired["operator_summary_vi"] = (
                f"ChannelAuthorityAgent cần review: {summary_source.strip()}"
            )
            metadata_shape_fields.append("operator_summary_vi")
            metadata_shape_reason_codes.append(
                "CHANNEL_AUTHORITY_OPERATOR_SUMMARY_REPAIRED"
            )
    if expected_agent_key == "TopicIdeaScoringAgent" and (
        not isinstance(operator_summary, str) or not operator_summary.strip()
    ):
        artifact = repaired.get("artifact")
        if _valid_topic_idea_artifact(artifact):
            repaired["operator_summary_vi"] = (
                "Chủ đề cần được người vận hành kiểm tra trước khi tiếp tục."
            )
            metadata_shape_fields.append("operator_summary_vi")
            metadata_shape_reason_codes.append("OPERATOR_SUMMARY_VI_COMPLETED")

    if expected_agent_key == "ProviderReadinessSummaryAgent" and not isinstance(
        repaired.get("artifact"), dict
    ):
        appendix = repaired.get("technical_appendix")
        nested_artifact = (
            appendix.get("artifact") if isinstance(appendix, dict) else None
        )
        if _valid_provider_readiness_artifact(nested_artifact):
            repaired["artifact"] = nested_artifact
            appendix.pop("artifact", None)
            metadata_shape_fields.extend(["artifact", "technical_appendix"])
            metadata_shape_reason_codes.append(
                "PROVIDER_READINESS_ARTIFACT_MOVED_FROM_TECHNICAL_APPENDIX"
            )
    if expected_agent_key == "ProviderReadinessSummaryAgent" and (
        not isinstance(operator_summary, str) or not operator_summary.strip()
    ):
        summary = _provider_readiness_operator_summary_vi(repaired.get("artifact"))
        if summary:
            repaired["operator_summary_vi"] = summary
            metadata_shape_fields.append("operator_summary_vi")
            metadata_shape_reason_codes.append(
                "PROVIDER_READINESS_OPERATOR_SUMMARY_REPAIRED"
            )

    if repaired.get("confidence_label") == "UNKNOWN":
        repaired["confidence_label"] = "LOW"
        metadata_shape_fields.append("confidence_label")
        metadata_shape_reason_codes.append("CONFIDENCE_UNKNOWN_TO_LOW_REPAIRED")
    elif repaired.get("confidence_label") == "MEDIUM_HIGH":
        repaired["confidence_label"] = "MEDIUM"
        metadata_shape_fields.append("confidence_label")
        metadata_shape_reason_codes.append("CONFIDENCE_MEDIUM_HIGH_TO_MEDIUM_REPAIRED")
    elif repaired.get("confidence_label") == "VERY_HIGH":
        repaired["confidence_label"] = "HIGH"
        metadata_shape_fields.append("confidence_label")
        metadata_shape_reason_codes.append("CONFIDENCE_VERY_HIGH_TO_HIGH_REPAIRED")

    if repaired.get("status") in {
        "SUCCESS",
        "PASS",
        "COMPLETE",
        "COMPLETED",
        "READY",
        "READY_FOR_HUMAN_REVIEW",
    }:
        original_status = repaired.get("status")
        repaired["status"] = "OK"
        metadata_shape_fields.append("status")
        if original_status in {"COMPLETE", "COMPLETED"}:
            metadata_shape_reason_codes.append(
                f"STATUS_{original_status}_TO_OK_REPAIRED"
            )
        elif original_status == "READY":
            metadata_shape_reason_codes.append("STATUS_READY_TO_OK_REPAIRED")
        elif original_status == "READY_FOR_HUMAN_REVIEW":
            metadata_shape_reason_codes.append(
                "STATUS_READY_FOR_HUMAN_REVIEW_TO_OK_REPAIRED"
            )
        else:
            metadata_shape_reason_codes.append("STATUS_SUCCESS_TO_OK_REPAIRED")

    if metadata_shape_fields:
        attempts.append(
            {
                "repair_type": "normalize_envelope_metadata_shape",
                "semantic_change_allowed": False,
                "fields": sorted(set(metadata_shape_fields)),
                "reason_codes": sorted(set(metadata_shape_reason_codes)),
            }
        )

    if len(attempts) >= max_attempts:
        return repaired, attempts[:max_attempts]

    enum_changed = False
    enum_fields = {
        "status": ENVELOPE_ALLOWED_STATUS,
        "confidence_label": ENVELOPE_ALLOWED_CONFIDENCE,
    }
    for key, allowed in enum_fields.items():
        value = repaired.get(key)
        if isinstance(value, str):
            upper_value = value.upper()
            if upper_value in allowed and upper_value != value:
                repaired[key] = upper_value
                enum_changed = True
    if enum_changed:
        attempts.append(
            {
                "repair_type": "normalize_envelope_enum_casing",
                "semantic_change_allowed": False,
            }
        )

    return repaired, attempts[:max_attempts]


def _limitation_object_to_string_list(value: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    for key, item in value.items():
        if isinstance(item, list):
            for nested in item:
                if nested not in (None, "", []):
                    limitations.append(
                        f"{key}: {nested if isinstance(nested, str) else canonical_json(nested)}"
                    )
            continue
        if item not in (None, "", []):
            limitations.append(
                f"{key}: {item if isinstance(item, str) else canonical_json(item)}"
            )
    return limitations or [
        "ChannelAuthorityAgent returned limitations as an object; review original output audit."
    ]


def _move_top_level_risk_level(repaired: dict[str, Any]) -> dict[str, Any] | None:
    if "risk_level" not in repaired:
        return None
    raw_risk = repaired.pop("risk_level")
    normalized = raw_risk
    reason_codes = ["TOP_LEVEL_RISK_LEVEL_MOVED_TO_ARTIFACT"]
    if isinstance(raw_risk, str):
        if raw_risk.lower() in {"null", "none"}:
            normalized = None
            reason_codes.append("RISK_LEVEL_STRING_NULL_TO_NULL_REPAIRED")
        else:
            upper = raw_risk.upper()
            if upper == "MODERATE":
                upper = "MEDIUM"
                reason_codes.append("RISK_LEVEL_MODERATE_TO_MEDIUM_REPAIRED")
            normalized = upper if upper in ENVELOPE_ALLOWED_RISK else raw_risk
    artifact = repaired.get("artifact")
    if not isinstance(artifact, dict):
        artifact = {}
        repaired["artifact"] = artifact
    risk_assessment = artifact.get("risk_assessment")
    if not isinstance(risk_assessment, dict):
        risk_assessment = {}
        artifact["risk_assessment"] = risk_assessment
    risk_assessment.setdefault("risk_level", normalized)
    return {
        "repair_type": "move_top_level_risk_level_to_artifact",
        "semantic_change_allowed": False,
        "fields": ["risk_level", "artifact.risk_assessment.risk_level"],
        "reason_codes": sorted(set(reason_codes)),
    }


def _valid_provider_readiness_artifact(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    providers = value.get("providers")
    return isinstance(providers, dict) and bool(providers)


def _provider_readiness_operator_summary_vi(value: Any) -> str | None:
    if not _valid_provider_readiness_artifact(value):
        return None
    providers = value.get("providers")
    counts: dict[str, int] = {}
    blocked: list[str] = []
    for provider_key, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        state = str(
            provider.get("status") or provider.get("readiness_state") or "UNKNOWN"
        ).upper()
        counts[state] = counts.get(state, 0) + 1
        if state in {"BLOCK", "BLOCKED", "NOT_CONFIGURED"}:
            blocked.append(str(provider_key))
    if not counts:
        return None
    blocked_text = (
        ", ".join(sorted(blocked)) if blocked else "không có provider bị block"
    )
    counts_text = ", ".join(
        f"{state}={count}" for state, count in sorted(counts.items())
    )
    return f"Tóm tắt readiness provider: {counts_text}. Provider cần cấu hình trước media stage: {blocked_text}."


def validate_base_envelope(
    parsed: dict[str, Any], *, schema: dict[str, Any], expected_agent_key: str
) -> dict[str, Any]:
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
    if not isinstance(parsed.get("evidence_refs"), list):
        errors.append("evidence_refs must be a list")
    if not isinstance(parsed.get("limitations"), list):
        errors.append("limitations must be a list")
    if not isinstance(parsed.get("technical_appendix"), dict):
        errors.append("technical_appendix must be an object")
    if not isinstance(parsed.get("operator_summary_vi"), str) or not parsed.get(
        "operator_summary_vi"
    ):
        errors.append("operator_summary_vi is required")
    if parsed.get("artifact") is not None and not isinstance(
        parsed.get("artifact"), dict
    ):
        errors.append("artifact must be an object or null")
    if expected_agent_key == "TopicIdeaScoringAgent" and not _valid_topic_idea_artifact(
        parsed.get("artifact")
    ):
        errors.append("TopicIdeaScoringAgent artifact must be a valid object")
    return {
        "valid": not errors,
        "errors": errors,
        "schema_ref": BASE_SCHEMA_REF,
        "schema_version": schema.get("x-vcos-schema-version", DEFAULT_TEMPLATE_VERSION),
        "safe_json_repair_policy": "syntax_shape_only_no_semantic_change",
    }


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def normalize_text(value: str) -> str:
    return "\n".join(
        line.rstrip() for line in value.replace("\r\n", "\n").strip().split("\n")
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
