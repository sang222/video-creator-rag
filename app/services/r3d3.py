from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AgentContextPackSnapshot, EffectiveChannelRuntimeContextSnapshot, PromptRenderRun


R3D3_CONTEXT_PACK_VERSION = "r3d3.agent_context_pack.v1"
R3D3_BUILDER_VERSION = "r3d3.builder.v1"
R3D3_CONTRACT_VERSION = "r3d3.agent_context_contract.v1"
LANE_POLICY_VERSION = "m10.1.final-lanes"

HARD_RULE_HEADER = "\n".join(
    [
        "VCOS HARD RULES:",
        "- Human final approval required.",
        "- No publish/upload/reupload automation.",
        "- No fake traffic, bot engagement, platform evasion, scraping, or dashboard/browser automation.",
        "- Only supplied digests and frozen snapshots are operational truth.",
        "- Frozen Channel Contract only; do not mutate ChannelProfileVersion.",
        "- No mock fallback and no dry-run success as production success.",
        "- Return REVIEW_REQUIRED or BLOCK if required truth is missing or conflicting.",
        "- Do not claim nonexistent assets, demos, freebies, or evidence.",
        "- Do not call media providers when provider/media boundary is blocked.",
    ]
)
HARD_RULE_HEADER_HASH = hashlib.sha256(HARD_RULE_HEADER.encode("utf-8")).hexdigest()

GLOBAL_FORBIDDEN_SECTIONS = [
    "full_previous_artifacts",
    "full_channel_contract_json",
    "full_compiled_policy_snapshot_json",
    "provider_readiness_snapshot_raw",
    "full_provider_logs",
    "raw_research_pack",
    "full_research_pack",
    "raw_upstream_prompt_text",
    "raw_memory_lake",
    "latest_channel_settings",
]

LANE_CONTEXT_BUDGETS = {
    "cheap_structured": 12000,
    "long_context_text": 16000,
    "visual_creative_review": 12000,
    "gatekeeper_soft_review": 18000,
    "engineering_architect": 12000,
}


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _source_hash(value: Any) -> str:
    return stable_hash({"source": value})


def _short_text(value: Any, limit: int = 280) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit]


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if item not in (None, "")]


def _compact_digest(
    *,
    digest_type: str,
    payload: dict[str, Any],
    source_snapshot_id: uuid.UUID | str | None = None,
    source_ref: str | None = None,
    source_hash: str | None = None,
    relevant_contract_paths: list[str] | None = None,
    must_follow: list[str] | None = None,
    must_not_do: list[str] | None = None,
) -> dict[str, Any]:
    digest = {
        "digest_type": digest_type,
        "source_snapshot_id": str(source_snapshot_id) if source_snapshot_id is not None else None,
        "source_ref": source_ref,
        "source_hash": source_hash or stable_hash(payload),
        "relevant_contract_paths": relevant_contract_paths or [],
        "must_follow": must_follow or [],
        "must_not_do": must_not_do or [],
        "allowed_overrides": "none unless human-approved",
        "payload": payload,
    }
    digest["digest_hash"] = stable_hash(digest)
    return digest


@dataclass(frozen=True)
class AgentContextContract:
    agent_key: str
    task_type: str | None
    required_context_sections: list[str]
    optional_context_sections: list[str]
    forbidden_context_sections: list[str]
    max_context_chars: int
    max_memory_facets: int
    max_artifact_refs: int
    raw_artifact_allowed: bool
    full_debug_allowed: bool
    lane: str
    contract_version: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_key": self.agent_key,
            "task_type": self.task_type,
            "required_context_sections": self.required_context_sections,
            "optional_context_sections": self.optional_context_sections,
            "forbidden_context_sections": self.forbidden_context_sections,
            "max_context_chars": self.max_context_chars,
            "max_memory_facets": self.max_memory_facets,
            "max_artifact_refs": self.max_artifact_refs,
            "raw_artifact_allowed": self.raw_artifact_allowed,
            "full_debug_allowed": self.full_debug_allowed,
            "lane": self.lane,
            "contract_version": self.contract_version,
            "content_hash": self.content_hash,
        }


def _contract(
    agent_key: str,
    *,
    lane: str,
    required: list[str],
    optional: list[str] | None = None,
    forbidden: list[str] | None = None,
    max_context_chars: int | None = None,
    max_artifact_refs: int = 8,
    task_type: str | None = None,
) -> AgentContextContract:
    payload = {
        "agent_key": agent_key,
        "task_type": task_type,
        "required_context_sections": required,
        "optional_context_sections": optional or [],
        "forbidden_context_sections": sorted(set([*(forbidden or []), *GLOBAL_FORBIDDEN_SECTIONS])),
        "max_context_chars": max_context_chars or LANE_CONTEXT_BUDGETS.get(lane, 8000),
        "max_memory_facets": 0,
        "max_artifact_refs": max_artifact_refs,
        "raw_artifact_allowed": False,
        "full_debug_allowed": False,
        "lane": lane,
        "contract_version": R3D3_CONTRACT_VERSION,
    }
    return AgentContextContract(content_hash=stable_hash(payload), **payload)


DEFAULT_CONTRACTS: dict[str, AgentContextContract] = {
    "ChannelAuthorityAgent": _contract(
        "ChannelAuthorityAgent",
        lane="cheap_structured",
        required=["effective_channel_runtime_digest", "runtime_guard_digest", "evidence_digest", "common_skill_digest"],
        optional=["package_status_digest"],
    ),
    "TopicIdeaScoringAgent": _contract(
        "TopicIdeaScoringAgent",
        lane="cheap_structured",
        required=["effective_channel_runtime_digest", "runtime_guard_digest", "evidence_digest", "common_skill_digest"],
        optional=["script_contract_digest"],
    ),
    "ResearchPackSummarizer": _contract(
        "ResearchPackSummarizer",
        lane="long_context_text",
        required=["effective_channel_runtime_digest", "runtime_guard_digest", "evidence_digest", "common_skill_digest"],
        optional=["script_contract_digest"],
    ),
    "ScriptPlanningAgent": _contract(
        "ScriptPlanningAgent",
        lane="long_context_text",
        required=[
            "effective_channel_runtime_digest",
            "script_contract_digest",
            "duration_policy",
            "evidence_digest",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["package_status_digest"],
    ),
    "ScriptWriterAgent": _contract(
        "ScriptWriterAgent",
        lane="long_context_text",
        required=[
            "script_contract_digest",
            "effective_channel_runtime_digest",
            "script_plan_digest",
            "evidence_digest",
            "duration_policy",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["package_status_digest"],
        forbidden=["full_visual_plan", "upload_card_copy", "media_qc_report"],
    ),
    "PublishingMetadataAgent": _contract(
        "PublishingMetadataAgent",
        lane="cheap_structured",
        required=[
            "metadata_contract_digest",
            "script_digest",
            "evidence_digest",
            "disclosure_digest",
            "title_style_locale_digest",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["package_status_digest"],
        forbidden=["full_visual_plan", "provider_internals", "full_research_pack"],
    ),
    "VisualPlanningAgent": _contract(
        "VisualPlanningAgent",
        lane="visual_creative_review",
        required=[
            "visual_contract_digest",
            "script_sentence_digest",
            "allowed_visual_source_policy",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["asset_inventory_digest", "package_status_digest"],
        forbidden=["full_research_pack", "full_provider_logs", "full_previous_artifacts"],
    ),
    "ThumbnailBriefAgent": _contract(
        "ThumbnailBriefAgent",
        lane="visual_creative_review",
        required=[
            "thumbnail_contract_digest",
            "title_hook_digest",
            "visual_style_digest",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["character_thumbnail_digest", "package_status_digest"],
        forbidden=["full_narration_script", "raw_research_pack", "provider_readiness_snapshot_raw"],
    ),
    "RightsDisclosureReviewer": _contract(
        "RightsDisclosureReviewer",
        lane="gatekeeper_soft_review",
        required=[
            "source_rights_disclosure_context_digest",
            "metadata_digest",
            "visual_plan_digest",
            "provider_media_state_digest",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["evidence_digest"],
        forbidden=["full_topic_scores", "raw_upstream_prompt_text"],
    ),
    "GatekeeperSoftReviewAgent": _contract(
        "GatekeeperSoftReviewAgent",
        lane="gatekeeper_soft_review",
        required=[
            "effective_channel_runtime_digest",
            "runtime_guard_digest",
            "artifact_digests",
            "evidence_digest",
            "common_skill_digest",
        ],
        optional=["provider_media_state_digest", "package_status_digest"],
        max_artifact_refs=10,
    ),
    "UploadCardCopyAgent": _contract(
        "UploadCardCopyAgent",
        lane="cheap_structured",
        required=[
            "publish_handoff_digest",
            "metadata_digest",
            "disclosure_digest",
            "cta_eligibility_flags",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["package_status_digest"],
        forbidden=["full_research_pack", "full_narration_script", "provider_readiness_snapshot_raw"],
    ),
    "ProviderReadinessSummaryAgent": _contract(
        "ProviderReadinessSummaryAgent",
        lane="cheap_structured",
        required=[
            "runtime_guard_digest",
            "provider_readiness_digest",
            "package_status_digest",
            "common_skill_digest",
        ],
        optional=["provider_media_state_digest"],
        forbidden=["full_script", "full_visual_plan", "full_metadata_body"],
    ),
    "MediaQCExplanationAgent": _contract(
        "MediaQCExplanationAgent",
        lane="cheap_structured",
        required=[
            "package_summary_digest",
            "provider_readiness_digest",
            "media_inventory_digest",
            "gate_summary_digest",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=[],
        forbidden=["full_script", "full_outline", "topic_scores", "full_previous_history"],
        max_context_chars=14000,
    ),
    "ScriptRewriteAgent": _contract(
        "ScriptRewriteAgent",
        lane="long_context_text",
        required=[
            "script_contract_digest",
            "script_digest",
            "gate_summary_digest",
            "evidence_digest",
            "runtime_guard_digest",
            "common_skill_digest",
        ],
        optional=["script_plan_digest"],
        forbidden=["full_research_pack", "raw_upstream_prompt_text"],
    ),
}


class AgentContextContractRegistry:
    def __init__(self, contracts: dict[str, AgentContextContract] | None = None):
        self.contracts = contracts or DEFAULT_CONTRACTS

    def resolve(self, agent_key: str, task_type: str | None = None, lane: str | None = None) -> AgentContextContract:
        contract = self.contracts.get(agent_key)
        if contract is None:
            contract = _contract(
                agent_key,
                lane=lane or "cheap_structured",
                required=["effective_channel_runtime_digest", "runtime_guard_digest", "evidence_digest", "common_skill_digest"],
                optional=["artifact_digests", "package_status_digest"],
            )
        if task_type is not None or lane is not None:
            payload = contract.to_dict()
            payload["task_type"] = task_type
            payload["lane"] = lane or contract.lane
            payload.pop("content_hash", None)
            contract = replace(contract, task_type=task_type, lane=lane or contract.lane, content_hash=stable_hash(payload))
        return contract


@dataclass(frozen=True)
class PromptBudgetResult:
    status: str
    sections: dict[str, Any]
    budget_report: dict[str, Any]
    omitted_items: list[dict[str, Any]]
    largest_context_contributors: list[dict[str, Any]]
    reason_codes: list[str]


class PromptBudgetGate:
    def apply(self, *, contract: AgentContextContract, sections: dict[str, Any], initial_omitted: list[dict[str, Any]]) -> PromptBudgetResult:
        required = set(contract.required_context_sections) | {"effective_channel_runtime_digest"}
        selected = dict(sections)
        omitted = list(initial_omitted)
        contributors = self._contributors(selected)
        total_chars = sum(item["chars"] for item in contributors)
        optional_names = [name for name in selected if name not in required]

        for name in sorted(optional_names, key=lambda item: len(canonical_json(selected[item])), reverse=True):
            if total_chars <= contract.max_context_chars:
                break
            removed = selected.pop(name)
            omitted.append({"section": name, "reason": "OPTIONAL_CONTEXT_REMOVED_FOR_BUDGET", "chars": len(canonical_json(removed))})
            contributors = self._contributors(selected)
            total_chars = sum(item["chars"] for item in contributors)

        contributors = self._contributors(selected)
        total_chars = sum(item["chars"] for item in contributors)
        required_chars = sum(item["chars"] for item in contributors if item["section"] in required)
        status = "OK" if total_chars <= contract.max_context_chars else "BLOCK"
        reason_codes = [] if status == "OK" else ["CONTEXT_BUDGET_EXCEEDED"]
        report = {
            "max_context_chars": contract.max_context_chars,
            "context_pack_chars": total_chars,
            "required_context_chars": required_chars,
            "optional_context_chars": max(0, total_chars - required_chars),
            "budget_status": status,
            "reason_codes": reason_codes,
        }
        return PromptBudgetResult(
            status=status,
            sections=selected,
            budget_report=report,
            omitted_items=omitted,
            largest_context_contributors=contributors[:8],
            reason_codes=reason_codes,
        )

    def _contributors(self, sections: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [{"section": name, "chars": len(canonical_json(value))} for name, value in sections.items()]
        return sorted(rows, key=lambda item: item["chars"], reverse=True)


@dataclass(frozen=True)
class ShapeGateResult:
    status: str
    reason_codes: list[str]
    errors: list[str]


class ContextPackShapeGate:
    def check(self, *, contract: AgentContextContract, context_pack: dict[str, Any]) -> ShapeGateResult:
        errors: list[str] = []
        digests = _dict(context_pack.get("digests"))
        for section in contract.required_context_sections:
            if section not in digests:
                errors.append(f"required section missing: {section}")
        for section in contract.forbidden_context_sections:
            if section in digests or _contains_key(context_pack, section):
                errors.append(f"forbidden section present: {section}")
        if "effective_channel_runtime_digest" not in digests:
            errors.append("EffectiveChannelRuntimeDigest missing")
        if context_pack.get("agent_context_contract", {}).get("content_hash") != contract.content_hash:
            errors.append("AgentContextContract hash mismatch")
        if context_pack.get("latest_channel_settings_read") is not False:
            errors.append("latest channel settings bypass marker missing")
        if "prompt_budget_metrics" not in context_pack:
            errors.append("prompt budget metrics missing")
        if not context_pack.get("audit_refs"):
            errors.append("audit refs missing")
        if _contains_key(context_pack, "previous_artifacts"):
            errors.append("raw previous_artifacts key present")
        if _contains_key(context_pack, "channel_contract_json"):
            errors.append("raw channel_contract_json key present")
        if _contains_key(context_pack, "compiled_policy_snapshot_json"):
            errors.append("raw compiled_policy_snapshot_json key present")
        status = "OK" if not errors else "BLOCK"
        return ShapeGateResult(
            status=status,
            reason_codes=[] if status == "OK" else ["CONTEXT_PACK_SHAPE_INVALID"],
            errors=errors,
        )


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return any(str(item_key) == key or _contains_key(item_value, key) for item_key, item_value in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


@dataclass(frozen=True)
class AgentContextPackBuildResult:
    status: str
    context_pack: dict[str, Any] | None
    snapshot: AgentContextPackSnapshot | None
    blocking_report: dict[str, Any] | None
    reason_codes: list[str]


class ArtifactDigestBuilder:
    ARTIFACT_TYPES = {
        "script_outline": "script_outline",
        "narration_script": "narration_script",
        "visual_plan": "visual_plan",
        "metadata_package": "metadata_package",
        "thumbnail_brief": "thumbnail_brief",
        "rights_disclosure_review": "rights_disclosure_review",
        "provider_readiness_summary": "provider_readiness_summary",
        "upload_card_copy": "upload_card_copy",
    }

    def build_many(self, *, package_id: uuid.UUID, artifacts: dict[str, Any], max_refs: int = 8) -> list[dict[str, Any]]:
        digests: list[dict[str, Any]] = []
        for key in sorted(artifacts):
            if key in {"agent_context_pack_refs", "human_review_checklist"}:
                continue
            value = artifacts[key]
            if not isinstance(value, dict):
                continue
            digests.append(self.build(package_id=package_id, artifact_key=key, artifact=value))
        return digests[:max_refs]

    def build(self, *, package_id: uuid.UUID, artifact_key: str, artifact: dict[str, Any]) -> dict[str, Any]:
        artifact_type = self.ARTIFACT_TYPES.get(artifact_key, artifact_key)
        artifact_hash = stable_hash({"artifact_key": artifact_key, "artifact": artifact})
        payload = {
            "artifact_id": artifact_key,
            "artifact_type": artifact_type,
            "status": str(artifact.get("status") or "AVAILABLE"),
            "artifact_hash": artifact_hash,
            "validation_state": "AVAILABLE",
            "risk_flags": self._risk_flags(artifact_key, artifact),
            "source_refs": artifact.get("source_refs") or artifact.get("evidence_refs") or [],
            "full_artifact_ref": f"first_scripted_video_package:{package_id}:artifacts.{artifact_key}",
            "key_fields": self._key_fields(artifact_type, artifact),
        }
        payload["digest_hash"] = stable_hash(payload)
        return payload

    def _risk_flags(self, artifact_key: str, artifact: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        if artifact_key == "rights_disclosure_review" and str(artifact.get("result", "")).upper() != "PASS":
            flags.append("RIGHTS_REVIEW_REQUIRED")
        if artifact_key == "media_qc_explanation" and str(artifact.get("status", "")).upper() in {"PASS", "QC_PASS"}:
            flags.append("MEDIA_QC_PASS_WITHOUT_MEDIA_RISK")
        if artifact.get("ai_disclosure_needed") is True:
            flags.append("AI_DISCLOSURE_NEEDED")
        return flags

    def _key_fields(self, artifact_type: str, artifact: dict[str, Any]) -> dict[str, Any]:
        if artifact_type == "script_outline":
            chapters = artifact.get("chapters") or artifact.get("outline") or []
            return {
                "chapter_ids": [str(item.get("chapter_id") or item.get("id") or index + 1) if isinstance(item, dict) else str(index + 1) for index, item in enumerate(_list(chapters))],
                "target_duration": artifact.get("target_duration") or artifact.get("target_duration_seconds"),
                "chapter_budgets": artifact.get("chapter_budgets") or artifact.get("duration_budgets") or [],
            }
        if artifact_type == "narration_script":
            sentences = [item for item in _list(artifact.get("sentences")) if isinstance(item, dict)]
            return {
                "sentence_count": len(sentences),
                "total_approx_seconds": sum(float(item.get("approx_seconds") or 0) for item in sentences),
                "chapter_totals": artifact.get("chapter_totals") or [],
                "claim_count": len(_list(artifact.get("claims") or artifact.get("claim_refs"))),
                "open_issues": artifact.get("open_issues") or [],
            }
        if artifact_type == "visual_plan":
            scenes = [item for item in _list(artifact.get("scenes")) if isinstance(item, dict)]
            sentence_ids = {str(item.get("sentence_id")) for item in scenes if item.get("sentence_id")}
            source_types = sorted({str(item.get("intended_visual_source") or item.get("source_type")) for item in scenes if item.get("intended_visual_source") or item.get("source_type")})
            return {
                "scene_count": len(scenes),
                "covered_sentence_count": len(sentence_ids),
                "uncovered_sentence_ids_count": artifact.get("uncovered_sentence_ids_count", 0),
                "source_types_used": source_types,
            }
        if artifact_type == "metadata_package":
            return {
                "title": artifact.get("title"),
                "description_summary": _short_text(artifact.get("description"), limit=220),
                "disclosure_flag": bool(artifact.get("disclosure_notes") or artifact.get("ai_disclosure_needed")),
                "cta_class": artifact.get("cta_class") or artifact.get("cta_type"),
                "language": artifact.get("language") or artifact.get("content_language"),
            }
        if artifact_type == "thumbnail_brief":
            variant = _list(artifact.get("variants"))[0] if _list(artifact.get("variants")) else artifact
            variant_dict = _dict(variant)
            return {
                "concept": artifact.get("concept") or variant_dict.get("concept"),
                "text_overlay": artifact.get("text_overlay") or variant_dict.get("text"),
                "character_refs": artifact.get("character_refs") or variant_dict.get("character_refs") or [],
            }
        if artifact_type == "rights_disclosure_review":
            return {
                "result": artifact.get("result"),
                "ai_disclosure_needed": artifact.get("ai_disclosure_needed"),
                "rights_risk": artifact.get("rights_risk"),
            }
        if artifact_type == "provider_readiness_summary":
            providers = artifact.get("providers") if isinstance(artifact.get("providers"), dict) else {}
            return {
                "overall_readiness": artifact.get("overall_readiness") or artifact.get("summary_status"),
                "blocked_providers": [key for key, value in providers.items() if "NEEDS" in str(value) or "BLOCK" in str(value)],
                "next_action": artifact.get("next_action"),
            }
        if artifact_type == "upload_card_copy":
            return {
                "title_ref": stable_hash({"title": artifact.get("title")}) if artifact.get("title") else None,
                "description_ref": stable_hash({"description": artifact.get("description")}) if artifact.get("description") else None,
                "cta_class": artifact.get("cta_class"),
                "disclosure_refs": artifact.get("disclosure_refs") or [],
            }
        return {"summary": _short_text(artifact, limit=260)}


class AgentContextPackBuilder:
    def __init__(
        self,
        session: Session,
        *,
        contract_registry: AgentContextContractRegistry | None = None,
        budget_gate: PromptBudgetGate | None = None,
        shape_gate: ContextPackShapeGate | None = None,
    ):
        self.session = session
        self.contract_registry = contract_registry or AgentContextContractRegistry()
        self.budget_gate = budget_gate or PromptBudgetGate()
        self.shape_gate = shape_gate or ContextPackShapeGate()
        self.artifacts = ArtifactDigestBuilder()

    def build(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        agent_key: str,
        task_type: str | None,
        lane: str,
        effective_context_snapshot_id: uuid.UUID | None,
        effective_context_hash: str | None,
        compiled_policy_snapshot_id: uuid.UUID | None,
        compiled_policy_snapshot_hash: str | None,
        channel_contract_hash: str | None,
        artifacts: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
        current_package_state: dict[str, Any],
        runtime_guard_state: dict[str, Any],
        provider_readiness_state: dict[str, Any] | None = None,
        schema_requirements: dict[str, Any] | None = None,
    ) -> AgentContextPackBuildResult:
        contract = self.contract_registry.resolve(agent_key, task_type=task_type, lane=lane)
        if video_project_id is None or effective_context_snapshot_id is None or not effective_context_hash:
            return self._blocked(
                reason_codes=["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"],
                report={
                    "status": "BLOCK",
                    "reason_codes": ["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"],
                    "next_action": "Chọn VideoProject đã có EffectiveChannelRuntimeContextSnapshot PASS trước khi render prompt.",
                },
            )

        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, effective_context_snapshot_id)
        if effective is None or effective.video_project_id != video_project_id:
            return self._blocked(
                reason_codes=["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"],
                report={
                    "status": "BLOCK",
                    "reason_codes": ["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"],
                    "effective_context_snapshot_id": str(effective_context_snapshot_id),
                },
            )
        if effective.context_hash != effective_context_hash or effective.compile_status != "PASS":
            return self._blocked(
                reason_codes=["EFFECTIVE_CONTEXT_SNAPSHOT_NOT_PASS"],
                report={
                    "status": "BLOCK" if effective.compile_status == "BLOCK" else "REVIEW_REQUIRED",
                    "reason_codes": ["EFFECTIVE_CONTEXT_SNAPSHOT_NOT_PASS"],
                    "effective_context_snapshot_id": str(effective.id),
                    "compile_status": effective.compile_status,
                    "context_hash": effective.context_hash,
                    "expected_context_hash": effective_context_hash,
                },
            )

        candidate_sections = self._candidate_sections(
            package_id=package_id,
            effective=effective,
            agent_key=agent_key,
            artifacts=artifacts,
            evidence_refs=evidence_refs,
            current_package_state=current_package_state,
            runtime_guard_state=runtime_guard_state,
            provider_readiness_state=provider_readiness_state or {},
        )
        allowed = set(contract.required_context_sections) | set(contract.optional_context_sections) | {
            "effective_channel_runtime_digest"
        }
        selected: dict[str, Any] = {}
        omitted: list[dict[str, Any]] = []
        for name, value in candidate_sections.items():
            if name in contract.forbidden_context_sections:
                omitted.append({"section": name, "reason": "FORBIDDEN_BY_AGENT_CONTEXT_CONTRACT"})
                continue
            if name in allowed:
                selected[name] = value
            else:
                omitted.append({"section": name, "reason": "NOT_IN_AGENT_CONTEXT_ALLOWLIST"})

        missing = [name for name in contract.required_context_sections if name not in selected]
        if missing:
            return self._persist_blocking_snapshot(
                package_id=package_id,
                video_project_id=video_project_id,
                contract=contract,
                effective=effective,
                compiled_policy_snapshot_id=compiled_policy_snapshot_id,
                compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
                channel_contract_hash=channel_contract_hash,
                sections=selected,
                omitted=omitted,
                shape_gate={"status": "BLOCK", "reason_codes": ["REQUIRED_CONTEXT_MISSING"], "missing_required_sections": missing},
                reason_codes=["REQUIRED_CONTEXT_MISSING"],
                blocking_status="BLOCK",
            )

        budget = self.budget_gate.apply(contract=contract, sections=selected, initial_omitted=omitted)
        if budget.status != "OK":
            return self._persist_blocking_snapshot(
                package_id=package_id,
                video_project_id=video_project_id,
                contract=contract,
                effective=effective,
                compiled_policy_snapshot_id=compiled_policy_snapshot_id,
                compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
                channel_contract_hash=channel_contract_hash,
                sections=budget.sections,
                omitted=budget.omitted_items,
                budget_report=budget.budget_report,
                largest_context_contributors=budget.largest_context_contributors,
                shape_gate={"status": "BLOCK", "reason_codes": budget.reason_codes},
                reason_codes=budget.reason_codes,
                blocking_status="BLOCK",
            )

        context_pack = self._assemble_pack(
            package_id=package_id,
            video_project_id=video_project_id,
            contract=contract,
            effective=effective,
            sections=budget.sections,
            budget_report=budget.budget_report,
            omitted_items=budget.omitted_items,
            largest_context_contributors=budget.largest_context_contributors,
            compiled_policy_snapshot_id=compiled_policy_snapshot_id,
            compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
            channel_contract_hash=channel_contract_hash,
            schema_requirements=schema_requirements or {},
        )
        shape = self.shape_gate.check(contract=contract, context_pack=context_pack)
        snapshot = self._persist_snapshot(
            package_id=package_id,
            video_project_id=video_project_id,
            contract=contract,
            effective=effective,
            compiled_policy_snapshot_id=compiled_policy_snapshot_id,
            compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
            channel_contract_hash=channel_contract_hash,
            context_pack=context_pack,
            budget_report=context_pack["prompt_budget_metrics"],
            omitted_items=budget.omitted_items,
            largest_context_contributors=budget.largest_context_contributors,
            shape_gate_result={"status": shape.status, "reason_codes": shape.reason_codes, "errors": shape.errors},
        )
        if shape.status != "OK":
            return AgentContextPackBuildResult(
                status="BLOCK",
                context_pack=context_pack,
                snapshot=snapshot,
                blocking_report={"status": "BLOCK", "reason_codes": shape.reason_codes, "errors": shape.errors},
                reason_codes=shape.reason_codes,
            )
        return AgentContextPackBuildResult(status="OK", context_pack=context_pack, snapshot=snapshot, blocking_report=None, reason_codes=[])

    def link_prompt_render_run(self, *, snapshot_id: uuid.UUID, prompt_render_run_id: uuid.UUID, prompt_context_hash: str) -> None:
        snapshot = self.session.get(AgentContextPackSnapshot, snapshot_id)
        if snapshot is None:
            return
        snapshot.prompt_render_run_id = prompt_render_run_id
        snapshot.prompt_context_hash = prompt_context_hash
        run = self.session.get(PromptRenderRun, prompt_render_run_id)
        if run is not None:
            render_vars = dict(run.render_vars_json or {})
            render_vars["agent_context_pack_snapshot_id"] = str(snapshot_id)
            render_vars["agent_context_pack_hash"] = snapshot.context_pack_hash
            run.render_vars_json = render_vars
            refs = list(run.artifact_refs or [])
            refs.append(
                {
                    "type": "agent_context_pack_snapshot",
                    "id": str(snapshot_id),
                    "context_pack_hash": snapshot.context_pack_hash,
                }
            )
            run.artifact_refs = refs
        self.session.flush()

    def _candidate_sections(
        self,
        *,
        package_id: uuid.UUID,
        effective: EffectiveChannelRuntimeContextSnapshot,
        agent_key: str,
        artifacts: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
        current_package_state: dict[str, Any],
        runtime_guard_state: dict[str, Any],
        provider_readiness_state: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_digests = self.artifacts.build_many(package_id=package_id, artifacts=artifacts, max_refs=12)
        artifact_by_key = {item["artifact_id"]: item for item in artifact_digests}
        runtime_guard = build_runtime_guard_digest(
            effective=effective,
            runtime_guard_state=runtime_guard_state,
            provider_readiness_state=provider_readiness_state,
        )
        evidence = build_evidence_digest(evidence_refs=evidence_refs, artifacts=artifacts, current_package_state=current_package_state)
        common = build_common_skill_digest()
        script = build_script_contract_digest(effective)
        visual = build_visual_contract_digest(effective)
        thumbnail = build_thumbnail_contract_digest(effective)
        metadata = build_metadata_contract_digest(effective)
        publish = build_publish_handoff_digest(effective)
        provider_digest = build_provider_readiness_digest(provider_readiness_state=provider_readiness_state, effective=effective)
        package_status = build_package_status_digest(current_package_state=current_package_state, artifacts=artifacts, package_id=package_id)
        sections = {
            "effective_channel_runtime_digest": build_effective_channel_runtime_digest(effective),
            "script_contract_digest": script,
            "voice_contract_digest": build_voice_contract_digest(effective),
            "visual_contract_digest": visual,
            "thumbnail_contract_digest": thumbnail,
            "metadata_contract_digest": metadata,
            "publish_handoff_digest": publish,
            "runtime_guard_digest": runtime_guard,
            "artifact_digests": artifact_digests,
            "evidence_digest": evidence,
            "common_skill_digest": common,
            "duration_policy": build_duration_policy_digest(effective),
            "script_plan_digest": artifact_by_key.get("script_outline") or unavailable_digest("script_plan_digest", "script_outline"),
            "script_sentence_digest": build_script_sentence_digest(package_id=package_id, artifacts=artifacts),
            "script_digest": artifact_by_key.get("narration_script") or unavailable_digest("script_digest", "narration_script"),
            "allowed_visual_source_policy": build_allowed_visual_source_policy_digest(effective),
            "asset_inventory_digest": build_asset_inventory_digest(artifacts=artifacts, package_id=package_id),
            "title_hook_digest": build_title_hook_digest(artifacts=artifacts),
            "visual_style_digest": build_visual_style_digest(effective),
            "character_thumbnail_digest": build_character_thumbnail_digest(effective),
            "metadata_digest": artifact_by_key.get("metadata_package") or unavailable_digest("metadata_digest", "metadata_package"),
            "disclosure_digest": build_disclosure_digest(artifacts=artifacts, effective=effective),
            "title_style_locale_digest": build_title_style_locale_digest(effective),
            "source_rights_disclosure_context_digest": build_source_rights_disclosure_context_digest(effective),
            "visual_plan_digest": artifact_by_key.get("visual_plan") or unavailable_digest("visual_plan_digest", "visual_plan"),
            "provider_media_state_digest": build_provider_media_state_digest(provider_readiness_state=provider_readiness_state, runtime_guard=runtime_guard),
            "cta_eligibility_flags": build_cta_eligibility_flags(effective),
            "provider_readiness_digest": provider_digest,
            "package_status_digest": package_status,
            "package_summary_digest": build_package_summary_digest(current_package_state=current_package_state, artifacts=artifacts, package_id=package_id),
            "media_inventory_digest": build_media_inventory_digest(artifacts=artifacts, package_id=package_id),
            "gate_summary_digest": build_gate_summary_digest(artifacts=artifacts),
        }
        return sections

    def _assemble_pack(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID,
        contract: AgentContextContract,
        effective: EffectiveChannelRuntimeContextSnapshot,
        sections: dict[str, Any],
        budget_report: dict[str, Any],
        omitted_items: list[dict[str, Any]],
        largest_context_contributors: list[dict[str, Any]],
        compiled_policy_snapshot_id: uuid.UUID | None,
        compiled_policy_snapshot_hash: str | None,
        channel_contract_hash: str | None,
        schema_requirements: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_digest_refs = _artifact_digest_refs(sections.get("artifact_digests"))
        budget_metrics = build_prompt_budget_metrics(
            sections=sections,
            budget_report=budget_report,
            omitted_items=omitted_items,
            largest_context_contributors=largest_context_contributors,
        )
        pack = {
            "context_pack_version": R3D3_CONTEXT_PACK_VERSION,
            "builder_version": R3D3_BUILDER_VERSION,
            "package_id": str(package_id),
            "video_project_id": str(video_project_id),
            "agent_key": contract.agent_key,
            "task_type": contract.task_type,
            "lane": contract.lane,
            "hard_rule_header": HARD_RULE_HEADER,
            "hard_rule_header_hash": HARD_RULE_HEADER_HASH,
            "agent_context_contract": contract.to_dict(),
            "digests": sections,
            "prompt_budget_metrics": budget_metrics,
            "omitted_context_report": {
                "omitted_context_count": len(omitted_items),
                "items": omitted_items,
            },
            "audit_refs": {
                "effective_context_snapshot_id": str(effective.id),
                "effective_context_hash": effective.context_hash,
                "channel_contract_hash": channel_contract_hash,
                "compiled_policy_snapshot_id": str(compiled_policy_snapshot_id) if compiled_policy_snapshot_id else None,
                "compiled_policy_snapshot_hash": compiled_policy_snapshot_hash,
                "artifact_digest_refs": artifact_digest_refs,
                "schema_requirements": schema_requirements,
                "full_debug_replay": {
                    "production_path": False,
                    "full_artifacts_available_in_db": True,
                    "full_channel_contract_available_via_compiled_policy_snapshot_ref": True,
                },
            },
            "latest_channel_settings_read": False,
        }
        pack["context_pack_hash"] = stable_hash({key: value for key, value in pack.items() if key != "context_pack_hash"})
        return pack

    def _persist_blocking_snapshot(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID,
        contract: AgentContextContract,
        effective: EffectiveChannelRuntimeContextSnapshot,
        compiled_policy_snapshot_id: uuid.UUID | None,
        compiled_policy_snapshot_hash: str | None,
        channel_contract_hash: str | None,
        sections: dict[str, Any],
        omitted: list[dict[str, Any]],
        shape_gate: dict[str, Any],
        reason_codes: list[str],
        blocking_status: str,
        budget_report: dict[str, Any] | None = None,
        largest_context_contributors: list[dict[str, Any]] | None = None,
    ) -> AgentContextPackBuildResult:
        budget = budget_report or build_prompt_budget_metrics(
            sections=sections,
            budget_report={"budget_status": blocking_status, "reason_codes": reason_codes},
            omitted_items=omitted,
            largest_context_contributors=largest_context_contributors or [],
        )
        context_pack = {
            "context_pack_version": R3D3_CONTEXT_PACK_VERSION,
            "builder_version": R3D3_BUILDER_VERSION,
            "package_id": str(package_id),
            "video_project_id": str(video_project_id),
            "agent_key": contract.agent_key,
            "lane": contract.lane,
            "hard_rule_header": HARD_RULE_HEADER,
            "hard_rule_header_hash": HARD_RULE_HEADER_HASH,
            "agent_context_contract": contract.to_dict(),
            "digests": sections,
            "prompt_budget_metrics": budget,
            "omitted_context_report": {"omitted_context_count": len(omitted), "items": omitted},
            "audit_refs": {"effective_context_snapshot_id": str(effective.id), "effective_context_hash": effective.context_hash},
            "latest_channel_settings_read": False,
        }
        context_pack["context_pack_hash"] = stable_hash({key: value for key, value in context_pack.items() if key != "context_pack_hash"})
        snapshot = self._persist_snapshot(
            package_id=package_id,
            video_project_id=video_project_id,
            contract=contract,
            effective=effective,
            compiled_policy_snapshot_id=compiled_policy_snapshot_id,
            compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
            channel_contract_hash=channel_contract_hash,
            context_pack=context_pack,
            budget_report=budget,
            omitted_items=omitted,
            largest_context_contributors=largest_context_contributors or [],
            shape_gate_result=shape_gate,
        )
        report = {"status": blocking_status, "reason_codes": reason_codes, **shape_gate}
        return AgentContextPackBuildResult(
            status=blocking_status,
            context_pack=context_pack,
            snapshot=snapshot,
            blocking_report=report,
            reason_codes=reason_codes,
        )

    def _persist_snapshot(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID,
        contract: AgentContextContract,
        effective: EffectiveChannelRuntimeContextSnapshot,
        compiled_policy_snapshot_id: uuid.UUID | None,
        compiled_policy_snapshot_hash: str | None,
        channel_contract_hash: str | None,
        context_pack: dict[str, Any],
        budget_report: dict[str, Any],
        omitted_items: list[dict[str, Any]],
        largest_context_contributors: list[dict[str, Any]],
        shape_gate_result: dict[str, Any],
    ) -> AgentContextPackSnapshot:
        digests = _dict(context_pack.get("digests"))
        snapshot = AgentContextPackSnapshot(
            package_id=package_id,
            video_project_id=video_project_id,
            agent_key=contract.agent_key,
            task_type=contract.task_type,
            lane=contract.lane,
            context_pack_version=R3D3_CONTEXT_PACK_VERSION,
            builder_version=R3D3_BUILDER_VERSION,
            agent_context_contract_hash=contract.content_hash,
            effective_context_snapshot_id=effective.id,
            effective_context_hash=effective.context_hash,
            channel_contract_hash=channel_contract_hash,
            compiled_policy_snapshot_id=compiled_policy_snapshot_id,
            compiled_policy_snapshot_hash=compiled_policy_snapshot_hash,
            context_pack_hash=str(context_pack["context_pack_hash"]),
            artifact_digest_refs_json=_artifact_digest_refs(digests.get("artifact_digests")),
            evidence_digest_hash=_dict(digests.get("evidence_digest")).get("digest_hash"),
            common_skill_digest_hash=_dict(digests.get("common_skill_digest")).get("digest_hash"),
            runtime_guard_digest_hash=str(_dict(digests.get("runtime_guard_digest")).get("digest_hash") or stable_hash({})),
            budget_report_json=budget_report,
            omitted_items_json=omitted_items,
            largest_context_contributors_json=largest_context_contributors,
            agent_context_contract_json=contract.to_dict(),
            context_pack_json=context_pack,
            shape_gate_result_json=shape_gate_result,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def _blocked(self, *, reason_codes: list[str], report: dict[str, Any]) -> AgentContextPackBuildResult:
        return AgentContextPackBuildResult(
            status="BLOCK",
            context_pack=None,
            snapshot=None,
            blocking_report=report,
            reason_codes=reason_codes,
        )


def _artifact_digest_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        refs.append(
            {
                "artifact_id": item.get("artifact_id"),
                "artifact_type": item.get("artifact_type"),
                "artifact_hash": item.get("artifact_hash"),
                "digest_hash": item.get("digest_hash"),
                "full_artifact_ref": item.get("full_artifact_ref"),
            }
        )
    return refs


def unavailable_digest(digest_type: str, source_ref: str) -> dict[str, Any]:
    return _compact_digest(
        digest_type=digest_type,
        source_ref=source_ref,
        source_hash=stable_hash({"source_ref": source_ref, "status": "UNAVAILABLE"}),
        relevant_contract_paths=[],
        must_follow=["Return REVIEW_REQUIRED if this required digest is needed for the requested task."],
        must_not_do=["Do not invent missing artifact content."],
        payload={"status": "UNAVAILABLE", "source_ref": source_ref},
    )


def build_effective_channel_runtime_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    payload = {
        "effective_context_snapshot_id": str(snapshot.id),
        "context_hash": snapshot.context_hash,
        "company_ref": str(snapshot.company_id),
        "channel_ref": str(snapshot.channel_workspace_id),
        "category_ref": str(snapshot.content_category_id) if snapshot.content_category_id else None,
        "character_refs": {
            "character_binding_id": str(snapshot.character_binding_id) if snapshot.character_binding_id else None,
            "character_profile_id": str(snapshot.character_profile_id) if snapshot.character_profile_id else None,
            "character_version_id": str(snapshot.character_version_id) if snapshot.character_version_id else None,
            "character_image_branch_id": str(snapshot.character_image_branch_id) if snapshot.character_image_branch_id else None,
            "reference_asset_pack_id": str(snapshot.reference_asset_pack_id) if snapshot.reference_asset_pack_id else None,
            "voice_profile_id": str(snapshot.voice_profile_id) if snapshot.voice_profile_id else None,
        },
        "compile_status": snapshot.compile_status,
        "reason_codes": snapshot.reason_codes_json,
    }
    return _compact_digest(
        digest_type="EffectiveChannelRuntimeDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["effective_channel_runtime_context_snapshots"],
        must_follow=["Use this frozen snapshot as runtime source of truth."],
        must_not_do=["Do not read latest channel settings or infer missing channel fields."],
        payload=payload,
    )


def build_script_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    market = _dict(snapshot.market_locale_context_json)
    audience = _dict(snapshot.audience_context_json)
    persona = _dict(snapshot.brand_voice_persona_context_json)
    category = _dict(snapshot.category_runtime_context_json)
    safety = _dict(snapshot.safety_forbidden_claims_context_json)
    payload = {
        "content_language": market.get("content_language"),
        "market": market.get("primary_market"),
        "locale": market.get("locale"),
        "audience_level": audience.get("audience_level"),
        "tone_persona": {"tone": persona.get("tone"), "persona": persona.get("persona"), "style_rules": persona.get("style_rules")},
        "duration_policy": _dict(category.get("default_format_policy")),
        "structure_policy": _dict(category.get("default_format_policy")).get("structure"),
        "claim_evidence_policy": safety.get("evidence_required_claim_types") or safety.get("high_risk_claim_policy"),
        "forbidden_style_topics_claims": {
            "forbidden_style": persona.get("forbidden_style"),
            "forbidden_topics": safety.get("forbidden_topics"),
            "forbidden_claims": safety.get("forbidden_claims"),
        },
        "character_persona": _dict(snapshot.character_identity_context_json).get("identity"),
    }
    return _compact_digest(
        digest_type="ScriptContractDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["market_locale", "target_audience", "voice_style", "format_policy", "editorial_strategy"],
        must_follow=["Follow content language, duration, evidence, persona, and forbidden-claim policy."],
        must_not_do=["Do not add unsupported claims or override channel persona."],
        payload=payload,
    )


def build_voice_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    voice = _dict(snapshot.voice_audio_context_json)
    payload = {
        "voice_profile_id": voice.get("voice_profile_id"),
        "language": voice.get("language"),
        "accent": voice.get("accent"),
        "tone": voice.get("tone"),
        "pace": voice.get("pace"),
        "pronunciation_dictionary_ref": voice.get("pronunciation_dictionary_ref"),
        "consent_status": voice.get("consent_status"),
        "commercial_use_status": voice.get("commercial_use_status"),
        "provider_policy": voice.get("provider_policy"),
        "character_voice_binding": _dict(snapshot.character_identity_context_json).get("character_profile_id"),
    }
    return _compact_digest(
        digest_type="VoiceContractDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["voice_style", "media_policy.voice_provider", "character.voice_profile"],
        must_follow=["Voice generation remains future/human-approved provider boundary."],
        must_not_do=["Do not call TTS or claim a generated voice file exists."],
        payload=payload,
    )


def build_visual_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    visual = _dict(snapshot.visual_style_context_json)
    payload = {
        "visual_mode": visual.get("visual_mode"),
        "allowed_visual_sources": visual.get("allowed_visual_sources"),
        "forbidden_visual_bait": visual.get("forbidden_visual_bait"),
        "character_presence_policy": _dict(snapshot.character_identity_context_json).get("character_policy_mode"),
        "character_visual_branch": _dict(visual.get("character_visual_rules")),
        "rights_source_policy": _dict(snapshot.source_rights_disclosure_context_json),
    }
    return _compact_digest(
        digest_type="VisualContractDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["media_policy", "category.default_visual_style_json", "rights_policy"],
        must_follow=["Use only allowed visual sources and candidate-only provider-backed assets."],
        must_not_do=["Do not request media provider generation or use visual bait."],
        payload=payload,
    )


def build_thumbnail_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    thumb = _dict(snapshot.thumbnail_style_context_json)
    payload = {
        "thumbnail_style": thumb.get("thumbnail_style"),
        "text_overlay_language": thumb.get("text_overlay_language"),
        "mobile_readability_rules": thumb.get("mobile_readability_rules"),
        "character_thumbnail_rules": thumb.get("character_thumbnail_rules"),
        "forbidden_thumbnail_patterns": thumb.get("forbidden_thumbnail_patterns"),
    }
    return _compact_digest(
        digest_type="ThumbnailContractDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["category.default_thumbnail_style_json", "media_policy", "editorial_strategy.forbidden_angles"],
        must_follow=["Create brief only; preserve mobile readability and language rules."],
        must_not_do=["Do not render or claim a thumbnail asset exists."],
        payload=payload,
    )


def build_metadata_contract_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    meta = _dict(snapshot.metadata_seo_policy_context_json)
    market = _dict(snapshot.market_locale_context_json)
    payload = {
        "title_style": meta.get("title_style"),
        "description_style": meta.get("description_style"),
        "language": market.get("content_language"),
        "locale": market.get("locale"),
        "subtitle_languages": meta.get("subtitle_languages"),
        "metadata_languages": meta.get("metadata_languages"),
        "seo_policy": meta.get("hashtag_policy"),
        "disclosure_placement_policy": meta.get("disclosure_placement_policy"),
    }
    return _compact_digest(
        digest_type="MetadataContractDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["platform_strategy", "rights_policy", "market_locale"],
        must_follow=["Keep metadata language, title style, SEO, and disclosure placement policy."],
        must_not_do=["Do not invent evidence, assets, or provider internals."],
        payload=payload,
    )


def build_publish_handoff_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    publish = _dict(snapshot.publish_timing_context_json)
    payload = {
        "manual_publish_only": True,
        "target_platform": "YouTube",
        "channel_timezone": publish.get("channel_timezone"),
        "audience_timezone": publish.get("audience_timezone"),
        "configured_publish_window": publish.get("configured_publish_window"),
        "visibility_schedule_policy": publish.get("suggested_publish_window_policy"),
    }
    return _compact_digest(
        digest_type="PublishHandoffDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["platform_strategy.publish_mode", "market_locale.timezone"],
        must_follow=["Manual publish handoff only."],
        must_not_do=["Do not upload, publish, reupload, or schedule."],
        payload=payload,
    )


def build_runtime_guard_digest(
    *,
    effective: EffectiveChannelRuntimeContextSnapshot,
    runtime_guard_state: dict[str, Any],
    provider_readiness_state: dict[str, Any],
) -> dict[str, Any]:
    provider_summary = _provider_readiness_summary(provider_readiness_state)
    payload = {
        "no_upload_publish_reupload": True,
        "no_provider_media_calls_unless_configured_and_human_approved_later": True,
        "no_mock_fallback": True,
        "provider_readiness_summary": provider_summary,
        "media_boundary_state": runtime_guard_state.get("media_boundary_state") or "BLOCKED_UNTIL_HUMAN_APPROVED_PROVIDER_STAGE",
        "google_drive_archive_only": True,
        "llm_router_only": True,
        "runtime_flags": {
            key: runtime_guard_state.get(key)
            for key in sorted(runtime_guard_state)
            if key.startswith("no_") or key.endswith("_only") or key.endswith("_disabled")
        },
    }
    return _compact_digest(
        digest_type="RuntimeGuardDigest",
        source_snapshot_id=effective.id,
        source_hash=stable_hash({"effective": effective.context_hash, "runtime_guard_state": runtime_guard_state, "provider": provider_summary}),
        relevant_contract_paths=["platform_strategy", "media_policy", "budget_policy"],
        must_follow=["Route LLM agents through LLMRouter and stop before provider media/upload/publish."],
        must_not_do=["Do not call media providers, Google Drive upload, or YouTube upload/publish."],
        payload=payload,
    )


def build_evidence_digest(
    *,
    evidence_refs: list[dict[str, Any]],
    artifacts: dict[str, Any],
    current_package_state: dict[str, Any],
) -> dict[str, Any]:
    research_text = str(current_package_state.get("research_pack_text") or "")
    fact_candidates = [line.strip(" -") for line in re.split(r"[\n.;]+", research_text) if line.strip()]
    research_notes = _dict(artifacts.get("research_notes"))
    facts = _strings(research_notes.get("facts")) or [_short_text(item, 160) for item in fact_candidates[:6] if _short_text(item, 160)]
    assumptions = _strings(research_notes.get("assumptions"))
    open_questions = _strings(research_notes.get("open_questions"))
    conflicts = _strings(research_notes.get("conflicts"))
    payload = {
        "summary": _short_text(research_notes.get("summary") or research_text, 360),
        "facts": facts,
        "assumptions": assumptions,
        "open_questions": open_questions,
        "conflicts": conflicts,
        "unavailable_fields": [] if facts else ["facts"],
        "support_refs": evidence_refs,
        "freshness_quality": {
            "source": "operator_supplied",
            "freshness": "UNKNOWN" if not evidence_refs else "SUPPLIED",
            "quality": "REVIEW_REQUIRED" if not facts else "MEDIUM",
        },
    }
    return _compact_digest(
        digest_type="EvidenceDigest",
        source_ref="operator_research_pack",
        source_hash=stable_hash({"evidence_refs": evidence_refs, "research_text_hash": stable_hash(research_text)}),
        relevant_contract_paths=["editorial_strategy.claim_style", "learning_policy.min_evidence_required"],
        must_follow=["Use only supplied evidence refs and mark unsupported claims REVIEW_REQUIRED."],
        must_not_do=["Do not invent citations, metrics, assets, or rights evidence."],
        payload=payload,
    )


def build_common_skill_digest() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    common_dir = root / "app" / "prompts" / "common"
    refs: list[dict[str, Any]] = []
    for path in sorted(common_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        refs.append({"ref": str(path.relative_to(root)), "name": path.stem, "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    payload = {
        "hard_rule_header_inline": True,
        "hard_rule_header_hash": HARD_RULE_HEADER_HASH,
        "common_skill_refs": refs,
        "full_skill_text_in_prompt": False,
        "full_skill_refs_preserved_for_audit": True,
    }
    return _compact_digest(
        digest_type="CommonSkillDigest",
        source_ref="app/prompts/common",
        source_hash=stable_hash(refs),
        relevant_contract_paths=["prompt_registry.common_skill_refs"],
        must_follow=["Hard-rule header remains inline in every prompt."],
        must_not_do=["Do not expand full common skill text into production prompts by default."],
        payload=payload,
    )


def build_duration_policy_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    policy = _dict(_dict(snapshot.category_runtime_context_json).get("default_format_policy"))
    return _compact_digest(
        digest_type="DurationPolicyDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["format_policy", "category.default_format_policy_json"],
        must_follow=["Respect target duration and chapter budgets."],
        must_not_do=["Do not invent duration policy if unavailable."],
        payload={"duration_policy": policy, "status": "AVAILABLE" if policy else "UNAVAILABLE"},
    )


def build_allowed_visual_source_policy_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    visual = _dict(snapshot.visual_style_context_json)
    return _compact_digest(
        digest_type="AllowedVisualSourcePolicyDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["media_policy.allowed_visual_sources"],
        must_follow=["Use only listed visual source values."],
        must_not_do=["Do not imply provider-generated media exists."],
        payload={"allowed_visual_sources": visual.get("allowed_visual_sources") or []},
    )


def build_script_sentence_digest(*, package_id: uuid.UUID, artifacts: dict[str, Any]) -> dict[str, Any]:
    script = _dict(artifacts.get("narration_script"))
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    payload = {
        "sentence_count": len(sentences),
        "timeline_slice": [
            {
                "sentence_id": item.get("sentence_id"),
                "approx_seconds": item.get("approx_seconds"),
                "text_preview": _short_text(item.get("text"), 96),
                "text_hash": stable_hash({"text": item.get("text")}) if item.get("text") else None,
            }
            for item in sentences[:80]
        ],
        "full_script_ref": f"first_scripted_video_package:{package_id}:artifacts.narration_script",
    }
    return _compact_digest(
        digest_type="ScriptSentenceDigest",
        source_ref=payload["full_script_ref"],
        source_hash=stable_hash(script),
        relevant_contract_paths=["artifact.narration_script.sentences"],
        must_follow=["Cover sentence ids in order."],
        must_not_do=["Do not rewrite narration unless explicitly assigned."],
        payload=payload,
    )


def build_asset_inventory_digest(*, artifacts: dict[str, Any], package_id: uuid.UUID) -> dict[str, Any]:
    media_keys = [key for key in sorted(artifacts) if "asset" in key or "media" in key or "thumbnail" in key]
    payload = {
        "available_asset_refs": [{"artifact_key": key, "ref": f"first_scripted_video_package:{package_id}:artifacts.{key}"} for key in media_keys],
        "media_generation_done": False,
    }
    return _compact_digest(
        digest_type="AssetInventoryDigest",
        source_ref=f"first_scripted_video_package:{package_id}:artifacts",
        source_hash=stable_hash(media_keys),
        relevant_contract_paths=["media_policy"],
        must_follow=["Use only listed existing assets."],
        must_not_do=["Do not claim nonexistent assets."],
        payload=payload,
    )


def build_title_hook_digest(*, artifacts: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(artifacts.get("metadata_package"))
    script = _dict(artifacts.get("narration_script"))
    first_sentence = None
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    if sentences:
        first_sentence = _short_text(sentences[0].get("text"), 160)
    payload = {
        "title": metadata.get("title"),
        "hook_preview": metadata.get("hook") or first_sentence,
        "title_hash": stable_hash({"title": metadata.get("title")}) if metadata.get("title") else None,
    }
    return _compact_digest(
        digest_type="TitleHookDigest",
        source_ref="metadata_package+narration_script",
        source_hash=stable_hash(payload),
        relevant_contract_paths=["platform_strategy.title_style"],
        must_follow=["Thumbnail/message must match title and hook truth."],
        must_not_do=["Do not add unsupported visual promises."],
        payload=payload,
    )


def build_visual_style_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    visual = _dict(snapshot.visual_style_context_json)
    payload = {
        "visual_mode": visual.get("visual_mode"),
        "visual_style": visual.get("visual_style"),
        "forbidden_visual_bait": visual.get("forbidden_visual_bait"),
    }
    return _compact_digest(
        digest_type="VisualStyleDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["category.default_visual_style_json", "media_policy"],
        must_follow=["Keep visual style compact and policy-aligned."],
        must_not_do=["Do not use bait or unapproved provider media."],
        payload=payload,
    )


def build_character_thumbnail_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    character = _dict(snapshot.character_identity_context_json)
    thumb = _dict(snapshot.thumbnail_style_context_json)
    payload = {
        "character_bound": bool(character.get("character_profile_id")),
        "character_profile_id": character.get("character_profile_id"),
        "character_version_id": character.get("character_version_id"),
        "character_thumbnail_rules": thumb.get("character_thumbnail_rules"),
        "reference_asset_pack_id": character.get("reference_asset_pack_id"),
    }
    return _compact_digest(
        digest_type="CharacterThumbnailDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["character_identity", "thumbnail_style"],
        must_follow=["Use character thumbnail rules only when character binding exists."],
        must_not_do=["Do not invent character likeness or assets."],
        payload=payload,
    )


def build_disclosure_digest(*, artifacts: dict[str, Any], effective: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    rights = _dict(artifacts.get("rights_disclosure_review"))
    source = _dict(effective.source_rights_disclosure_context_json)
    payload = {
        "rights_review_result": rights.get("result"),
        "ai_disclosure_needed": rights.get("ai_disclosure_needed", source.get("ai_disclosure_policy")),
        "rights_risk": rights.get("rights_risk"),
        "disclosure_notes": _strings(rights.get("disclosure_notes") or source.get("required_disclosure_blocks")),
    }
    return _compact_digest(
        digest_type="DisclosureDigest",
        source_snapshot_id=effective.id,
        source_hash=stable_hash({"rights": rights, "source": source}),
        relevant_contract_paths=["rights_policy"],
        must_follow=["Carry disclosure meaning into metadata/upload handoff."],
        must_not_do=["Do not invent license evidence."],
        payload=payload,
    )


def build_title_style_locale_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    metadata = _dict(snapshot.metadata_seo_policy_context_json)
    market = _dict(snapshot.market_locale_context_json)
    payload = {
        "title_style": metadata.get("title_style"),
        "market": market.get("primary_market"),
        "locale": market.get("locale"),
        "content_language": market.get("content_language"),
    }
    return _compact_digest(
        digest_type="TitleStyleLocaleDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["platform_strategy.title_style", "market_locale"],
        must_follow=["Use configured title style and locale only."],
        must_not_do=["Do not fake local relevance."],
        payload=payload,
    )


def build_source_rights_disclosure_context_digest(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    source = _dict(snapshot.source_rights_disclosure_context_json)
    return _compact_digest(
        digest_type="SourceRightsDisclosureContextDigest",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["rights_policy", "source_policy"],
        must_follow=["Review rights/source/disclosure state from frozen context."],
        must_not_do=["Do not use raw upstream prompt text as rights evidence."],
        payload=source,
    )


def build_provider_media_state_digest(*, provider_readiness_state: dict[str, Any], runtime_guard: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "provider_readiness_summary": _provider_readiness_summary(provider_readiness_state),
        "media_boundary_state": _dict(runtime_guard.get("payload")).get("media_boundary_state"),
        "no_provider_calls_confirmed": True,
    }
    return _compact_digest(
        digest_type="ProviderMediaStateDigest",
        source_ref="provider_readiness_snapshot_digest",
        source_hash=stable_hash(payload),
        relevant_contract_paths=["media_policy", "runtime_guard"],
        must_follow=["Provider gaps are boundary state, not a reason to fake media output."],
        must_not_do=["Do not call providers or claim media exists."],
        payload=payload,
    )


def build_cta_eligibility_flags(snapshot: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    cta = _dict(snapshot.monetization_cta_context_json)
    payload = {
        "allowed_cta_types": cta.get("allowed_cta_types"),
        "forbidden_cta_types": cta.get("forbidden_cta_types"),
        "affiliate_allowed": cta.get("affiliate_allowed"),
        "unsupported_asset_offer_forbidden": True,
    }
    return _compact_digest(
        digest_type="CTAEligibilityFlags",
        source_snapshot_id=snapshot.id,
        source_hash=snapshot.context_hash,
        relevant_contract_paths=["compiled_policy_snapshot_json.monetization_policy"],
        must_follow=["Only use eligible CTA classes."],
        must_not_do=["Do not claim nonexistent demos, assets, freebies, or offers."],
        payload=payload,
    )


def build_provider_readiness_digest(*, provider_readiness_state: dict[str, Any], effective: EffectiveChannelRuntimeContextSnapshot) -> dict[str, Any]:
    payload = _provider_readiness_summary(provider_readiness_state)
    return _compact_digest(
        digest_type="ProviderReadinessDigest",
        source_snapshot_id=provider_readiness_state.get("id"),
        source_ref="provider_readiness_snapshot",
        source_hash=stable_hash(payload),
        relevant_contract_paths=["cost_provider_policy", "media_policy"],
        must_follow=["Summarize readiness only; do not execute providers."],
        must_not_do=["Do not expose raw provider logs or secrets."],
        payload=payload,
    )


def _provider_readiness_summary(provider_readiness_state: dict[str, Any]) -> dict[str, Any]:
    summaries = _list(provider_readiness_state.get("provider_summaries"))
    providers: dict[str, Any] = {}
    for item in summaries:
        if not isinstance(item, dict) or not item.get("provider_key"):
            continue
        key = str(item["provider_key"])
        if key not in {"ollama", "elevenlabs", "creatomate", "google-vertex-veo", "cloud-final-renderer", "google-drive", "youtube-owner", "youtube-public"}:
            continue
        providers[key] = {
            "readiness_state": item.get("readiness_state"),
            "reason_codes": item.get("reason_codes") or [],
            "missing_env_keys_count": len(item.get("missing_env_keys") or []),
            "next_action": item.get("next_action"),
        }
    return {
        "provider_readiness_snapshot_id": str(provider_readiness_state.get("id")) if provider_readiness_state.get("id") else None,
        "providers": providers,
    }


def build_package_status_digest(*, current_package_state: dict[str, Any], artifacts: dict[str, Any], package_id: uuid.UUID) -> dict[str, Any]:
    payload = {
        "package_id": str(package_id),
        "milestone": current_package_state.get("milestone"),
        "agent_task": current_package_state.get("agent_task"),
        "artifact_keys_present": sorted(key for key, value in artifacts.items() if isinstance(value, dict)),
        "required_stop_at": current_package_state.get("required_stop_at"),
    }
    return _compact_digest(
        digest_type="PackageStatusDigest",
        source_ref=f"first_scripted_video_package:{package_id}",
        source_hash=stable_hash(payload),
        relevant_contract_paths=["package_state"],
        must_follow=["Use package status only as compact state."],
        must_not_do=["Do not inspect full previous artifacts."],
        payload=payload,
    )


def build_package_summary_digest(*, current_package_state: dict[str, Any], artifacts: dict[str, Any], package_id: uuid.UUID) -> dict[str, Any]:
    artifact_presence = {key: isinstance(value, dict) and bool(value) for key, value in sorted(artifacts.items())}
    payload = {
        "package_id": str(package_id),
        "artifact_presence_summary": {
            "present_count": sum(1 for present in artifact_presence.values() if present),
            "presence_hash": stable_hash(artifact_presence),
        },
        "text_agents_completed": [key for key in ("narration_script", "metadata_package", "visual_plan", "thumbnail_brief", "rights_disclosure_review") if artifacts.get(key)],
        "media_generation_done": False,
    }
    return _compact_digest(
        digest_type="PackageSummaryDigest",
        source_ref=f"first_scripted_video_package:{package_id}",
        source_hash=stable_hash(payload),
        relevant_contract_paths=["package_state"],
        must_follow=["Explain QC boundary from package summary only."],
        must_not_do=["Do not read full script, outline, topic scores, or previous history."],
        payload=payload,
    )


def build_media_inventory_digest(*, artifacts: dict[str, Any], package_id: uuid.UUID) -> dict[str, Any]:
    payload = {
        "media_files_present": False,
        "media_file_refs": [],
        "visual_plan_ref": f"first_scripted_video_package:{package_id}:artifacts.visual_plan" if artifacts.get("visual_plan") else None,
        "thumbnail_brief_ref": f"first_scripted_video_package:{package_id}:artifacts.thumbnail_brief" if artifacts.get("thumbnail_brief") else None,
        "media_qc_allowed_status": ["NOT_AVAILABLE", "WAITING_MEDIA_GENERATION"],
    }
    return _compact_digest(
        digest_type="MediaInventoryDigest",
        source_ref=f"first_scripted_video_package:{package_id}:media_inventory",
        source_hash=stable_hash(payload),
        relevant_contract_paths=["media_boundary"],
        must_follow=["Media QC cannot pass before media exists."],
        must_not_do=["Do not claim generated media or provider output."],
        payload=payload,
    )


def build_gate_summary_digest(*, artifacts: dict[str, Any]) -> dict[str, Any]:
    gate = _dict(artifacts.get("gatekeeper_review"))
    payload = {
        "gatekeeper_result": gate.get("result") or gate.get("decision"),
        "finding_count": len(_list(gate.get("findings"))),
        "reason_codes": gate.get("reason_codes") or [],
    }
    return _compact_digest(
        digest_type="GateSummaryDigest",
        source_ref="gatekeeper_review",
        source_hash=stable_hash(gate),
        relevant_contract_paths=["gatekeeper_review"],
        must_follow=["Use gate summary without expanding full prior history."],
        must_not_do=["Do not turn BLOCK into PASS."],
        payload=payload,
    )


def build_prompt_budget_metrics(
    *,
    sections: dict[str, Any],
    budget_report: dict[str, Any],
    omitted_items: list[dict[str, Any]],
    largest_context_contributors: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact_chars = len(canonical_json(sections.get("artifact_digests", [])))
    evidence_chars = len(canonical_json(sections.get("evidence_digest", {})))
    context_chars = sum(len(canonical_json(value)) for value in sections.values())
    return {
        "prompt_chars_system": 0,
        "prompt_chars_user": 0,
        "prompt_tokens_estimated": max(1, context_chars // 4),
        "context_pack_chars": context_chars,
        "artifact_digest_chars": artifact_chars,
        "evidence_digest_chars": evidence_chars,
        "omitted_context_count": len(omitted_items),
        "largest_context_contributors": largest_context_contributors,
        "agent_latency_ms": None,
        "package_total_runtime_ms": None,
        "cache_hit_rate": None,
        **budget_report,
    }


def update_prompt_budget_after_render(context_pack: dict[str, Any], *, system_chars: int, user_chars: int) -> dict[str, Any]:
    metrics = dict(context_pack.get("prompt_budget_metrics") or {})
    metrics["prompt_chars_system"] = system_chars
    metrics["prompt_chars_user"] = user_chars
    metrics["prompt_tokens_estimated"] = max(1, (system_chars + user_chars) // 4)
    context_pack["prompt_budget_metrics"] = metrics
    context_pack["context_pack_hash"] = stable_hash({key: value for key, value in context_pack.items() if key != "context_pack_hash"})
    return context_pack
