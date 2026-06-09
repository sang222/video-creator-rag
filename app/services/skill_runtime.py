from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Literal

from app.config.settings import get_settings
from app.services.skill_pack import SkillPackLoader


Priority = Literal["low", "medium", "high", "critical"]
TokenCostClass = Literal["tiny", "small", "medium", "large"]

PRIORITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TOKEN_CLASS_RANK: dict[str, int] = {"tiny": 0, "small": 1, "medium": 2, "large": 3}

RULE_1_COMPACT = (
    "Rule #1: prioritize monetization approval probability and sustainable revenue while preserving "
    "platform safety, audience trust, and brand identity."
)


def estimate_tokens(text: Any) -> int:
    if text is None:
        return 0
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=True, default=str, sort_keys=True)
    return max(1, (len(text) + 3) // 4)


def normalize_identifier(value: str | None) -> str:
    value = (value or "").strip()
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    aliases = {
        "authorityagent": "authority_agent",
        "scriptagent": "script_agent",
        "monetizationstrategyagent": "monetization_strategy_agent",
        "seometadataagent": "seo_metadata_agent",
        "publishingcontentagent": "publishing_content_agent",
        "compliancecopyrightagent": "compliance_agent",
        "compliance_agent": "compliance_agent",
        "memorycuratoragent": "memory_curator_agent",
    }
    compact = value.replace("_", "")
    return aliases.get(compact, aliases.get(value, value))


@dataclass(frozen=True)
class SkillDescriptor:
    skill_id: str
    source_path: str
    category: str
    version: str = "v1"
    applies_to_agents: list[str] = field(default_factory=list)
    applies_to_stages: list[str] = field(default_factory=list)
    priority: Priority = "medium"
    token_cost_class: TokenCostClass = "small"
    runtime_summary: list[str] = field(default_factory=list)
    hard_rules: list[str] = field(default_factory=list)
    decision_hooks: list[str] = field(default_factory=list)
    output_checks: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    full_policy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_agents(self) -> set[str]:
        return {normalize_identifier(agent) for agent in self.applies_to_agents}


@dataclass(frozen=True)
class RoleCapability:
    role: str
    allowed_skill_categories: list[str]
    default_task_stages: list[str]
    forbidden_actions: list[str]
    default_output_checks: list[str]


ROLE_CAPABILITIES: dict[str, RoleCapability] = {
    "authority_agent": RoleCapability(
        role="authority_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["authority_gate", "topic_review", "script_review", "final_review"],
        forbidden_actions=["PUBLISH"],
        default_output_checks=["decision_enum_valid", "monetization_risk_checked", "policy_risk_checked"],
    ),
    "script_agent": RoleCapability(
        role="script_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_rag_reading", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["script", "draft"],
        forbidden_actions=["FINAL_APPROVAL", "PUBLISH"],
        default_output_checks=["script_present", "risk_notes_present", "monetization_hypothesis_present"],
    ),
    "monetization_strategy_agent": RoleCapability(
        role="monetization_strategy_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["monetization_strategy", "planning"],
        forbidden_actions=["PUBLISH"],
        default_output_checks=["buyer_intent_score_present", "revenue_path_present"],
    ),
    "seo_metadata_agent": RoleCapability(
        role="seo_metadata_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["seo_metadata", "publishing"],
        forbidden_actions=["PUBLISH", "FINAL_APPROVAL"],
        default_output_checks=["title_present", "description_present", "clickbait_risk_present"],
    ),
    "publishing_content_agent": RoleCapability(
        role="publishing_content_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["publishing_content", "publishing"],
        forbidden_actions=["PUBLISH", "FINAL_APPROVAL"],
        default_output_checks=["publishing_copy_present", "disclosures_checked", "no_publish_decision"],
    ),
    "compliance_agent": RoleCapability(
        role="compliance_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["compliance", "final_review"],
        forbidden_actions=["PUBLISH"],
        default_output_checks=["checklist_present", "copyright_risk_present", "monetization_risk_present"],
    ),
    "memory_curator_agent": RoleCapability(
        role="memory_curator_agent",
        allowed_skill_categories=["creator_operating_philosophy", "creator_rag_reading", "creator_content_knowledge", "agents", "common"],
        default_task_stages=["memory", "post_publish"],
        forbidden_actions=["PUBLISH", "FINAL_APPROVAL"],
        default_output_checks=["memory_scope_valid", "learning_is_actionable"],
    ),
}


def capability_for_role(agent_role: str | None) -> RoleCapability:
    role = normalize_identifier(agent_role)
    return ROLE_CAPABILITIES.get(
        role,
        RoleCapability(
            role=role or "generic",
            allowed_skill_categories=["creator_operating_philosophy", "creator_rag_reading", "creator_content_knowledge", "agents", "common"],
            default_task_stages=[],
            forbidden_actions=["PUBLISH"],
            default_output_checks=["structured_json_valid"],
        ),
    )


def _strip_markdown(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#+\s*", "", line)
    line = re.sub(r"^[-*]\s*", "", line)
    return line.strip()


def _first_meaningful_lines(text: str, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = _strip_markdown(raw)
        if not line or line.lower() in {"principles:", "guardrails:", "cost policy:"}:
            continue
        lines.append(line[:240])
        if len(lines) >= limit:
            break
    return lines


def _priority_for_path(relative: Path, text: str) -> Priority:
    name = relative.as_posix()
    if "company_monetization_constitution" in name:
        return "critical"
    if name.startswith("agents/"):
        return "high"
    if "playbook" in name:
        return "medium"
    return "low"


def _category_for_path(relative: Path) -> str:
    parts = relative.parts
    if not parts:
        return "common"
    if parts[0] == "agents":
        return "agents"
    if parts[0] in {"creator_operating_philosophy", "creator_rag_reading", "creator_content_knowledge"}:
        return parts[0]
    if relative.name == "company_monetization_constitution.md":
        return "creator_operating_philosophy"
    if "playbook" in relative.name:
        return "creator_content_knowledge"
    return "common"


def _agent_scope_for_markdown(relative: Path) -> list[str]:
    if relative.parts and relative.parts[0] == "agents":
        role = normalize_identifier(relative.stem)
        return [role]
    return []


def _descriptor_from_markdown(path: Path, root: Path) -> SkillDescriptor:
    relative = path.relative_to(root)
    text = path.read_text(encoding="utf-8").strip()
    skill_id = normalize_identifier("_".join(relative.with_suffix("").parts))
    summary = _first_meaningful_lines(text)
    token_cost = "large" if estimate_tokens(text) > 1200 else "medium" if estimate_tokens(text) > 500 else "small"
    forbidden_actions: list[str] = []
    if relative.parts and relative.parts[0] == "agents":
        forbidden_actions = capability_for_role(relative.stem).forbidden_actions
    if "publish" in text.lower() and "never" in text.lower():
        forbidden_actions = sorted(set(forbidden_actions + ["PUBLISH"]))
    return SkillDescriptor(
        skill_id=skill_id,
        source_path=str(path),
        category=_category_for_path(relative),
        version="markdown_v1",
        applies_to_agents=_agent_scope_for_markdown(relative),
        applies_to_stages=[],
        priority=_priority_for_path(relative, text),
        token_cost_class=token_cost,  # type: ignore[arg-type]
        runtime_summary=summary or [relative.stem.replace("_", " ")],
        hard_rules=[line for line in summary if "must" in line.lower() or "never" in line.lower() or "rule #1" in line.lower()],
        decision_hooks=[RULE_1_COMPACT] if "monetization" in text.lower() else [],
        output_checks=[],
        forbidden_actions=forbidden_actions,
        full_policy=text,
        metadata={"format": "markdown", "relative_path": relative.as_posix()},
    )


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _descriptor_from_yaml(path: Path, root: Path) -> SkillDescriptor | None:
    data = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    if not data:
        return None
    relative = path.relative_to(root)
    skill_id = normalize_identifier(str(data.get("skill_id") or "_".join(relative.with_suffix("").parts)))
    runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    return SkillDescriptor(
        skill_id=skill_id,
        source_path=str(path),
        category=str(data.get("category") or _category_for_path(relative)),
        version=str(data.get("version") or "yaml_v1"),
        applies_to_agents=[normalize_identifier(agent) for agent in data.get("applies_to_agents", []) or []],
        applies_to_stages=[str(stage) for stage in data.get("applies_to_stages", []) or []],
        priority=str(data.get("priority") or "medium"),  # type: ignore[arg-type]
        token_cost_class=str(data.get("token_cost_class") or "small"),  # type: ignore[arg-type]
        runtime_summary=list(runtime.get("summary") or data.get("runtime_summary") or []),
        hard_rules=list(runtime.get("hard_rules") or data.get("hard_rules") or []),
        decision_hooks=list(runtime.get("decision_hooks") or data.get("decision_hooks") or []),
        output_checks=list(runtime.get("output_checks") or data.get("output_checks") or []),
        forbidden_actions=list(data.get("forbidden_actions") or []),
        full_policy=data.get("full_policy"),
        metadata={"format": "yaml", "relative_path": relative.as_posix(), **dict(data.get("metadata") or {})},
    )


@dataclass
class SkillResolutionResult:
    always_on_skills: list[SkillDescriptor]
    selected_skills: list[SkillDescriptor]
    deferred_skills: list[SkillDescriptor]
    forbidden_actions: list[str]
    required_output_checks: list[str]
    reason: list[str]
    trace: dict[str, Any]

    def selected_ids(self) -> list[str]:
        return [skill.skill_id for skill in self.selected_skills]

    def deferred_ids(self) -> list[str]:
        return [skill.skill_id for skill in self.deferred_skills]


@dataclass
class SkillCompressionResult:
    compact_context: str
    estimated_tokens: int
    included_skill_ids: list[str]
    deferred_skill_ids: list[str]
    trimmed: bool
    trim_reason: str | None


class SkillRegistry:
    def __init__(self, root: Path | None = None, loader: SkillPackLoader | None = None) -> None:
        self.root = root or get_settings().skill_dir
        self.loader = loader or SkillPackLoader(root=self.root)
        self._skills: dict[str, SkillDescriptor] = {}

    def discover(self) -> list[SkillDescriptor]:
        self.loader.assert_required()
        self._skills = {}
        if not self.root.exists():
            return []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml"}:
                continue
            descriptor: SkillDescriptor | None
            if path.suffix.lower() == ".md":
                descriptor = _descriptor_from_markdown(path, self.root)
            else:
                descriptor = _descriptor_from_yaml(path, self.root)
            if descriptor:
                self.register(descriptor)
        return list(self._skills.values())

    def register(self, descriptor: SkillDescriptor) -> None:
        self._skills[descriptor.skill_id] = descriptor

    def all(self) -> list[SkillDescriptor]:
        if not self._skills:
            self.discover()
        return list(self._skills.values())

    def get(self, skill_id: str) -> SkillDescriptor | None:
        if not self._skills:
            self.discover()
        return self._skills.get(normalize_identifier(skill_id))

    def by_category(self, category: str) -> list[SkillDescriptor]:
        return [skill for skill in self.all() if skill.category == category]

    def by_agent_role(self, agent_role: str) -> list[SkillDescriptor]:
        role = normalize_identifier(agent_role)
        return [skill for skill in self.all() if role in skill.normalized_agents]


class SkillResolver:
    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def resolve(
        self,
        *,
        agent_role: str,
        task_type: str | None = None,
        workflow_stage: str | None = None,
        workspace_context: dict[str, Any] | None = None,
        risk_flags: list[str] | None = None,
        requested_skill_ids: list[str] | None = None,
    ) -> SkillResolutionResult:
        role = normalize_identifier(agent_role)
        capability = capability_for_role(role)
        all_skills = self.registry.all()
        reasons: list[str] = []
        trace: dict[str, Any] = {
            "agent_role": role,
            "task_type": task_type,
            "workflow_stage": workflow_stage,
            "requested_skill_ids": requested_skill_ids or [],
            "risk_flags": risk_flags or [],
        }

        baseline = [
            skill
            for skill in all_skills
            if skill.category == "creator_operating_philosophy"
            or "monetization_constitution" in skill.skill_id
        ]
        role_contracts = [skill for skill in all_skills if role in skill.normalized_agents]
        always_on = self._dedupe([*baseline[:1], *role_contracts[:1]])
        reasons.append("included compact monetization/platform baseline and role contract when available")

        allowed_categories = set(capability.allowed_skill_categories)
        selected = list(always_on)
        deferred: list[SkillDescriptor] = []

        if requested_skill_ids:
            for requested_id in requested_skill_ids:
                skill = self.registry.get(requested_id)
                if not skill:
                    trace.setdefault("missing_requested_skills", []).append(requested_id)
                    continue
                if not self._is_allowed(skill, role, allowed_categories):
                    deferred.append(skill)
                    trace.setdefault("rejected_or_deferred_requested_skills", []).append(
                        {"skill_id": skill.skill_id, "reason": "not allowed for agent role"}
                    )
                    continue
                selected.append(skill)

        stage = normalize_identifier(workflow_stage or task_type)
        candidates = [
            skill
            for skill in all_skills
            if skill not in selected
            and self._is_allowed(skill, role, allowed_categories)
            and (
                role in skill.normalized_agents
                or (stage and stage in {normalize_identifier(item) for item in skill.applies_to_stages})
                or skill.category == "creator_content_knowledge"
            )
        ]
        candidates.sort(key=lambda skill: (PRIORITY_RANK.get(skill.priority, 2), TOKEN_CLASS_RANK.get(skill.token_cost_class, 1), skill.skill_id))
        for skill in candidates:
            if len(selected) >= 3:
                deferred.append(skill)
                continue
            if skill.token_cost_class == "large" and skill.priority not in {"critical", "high"}:
                deferred.append(skill)
                continue
            selected.append(skill)

        selected = self._dedupe(selected)
        selected_ids = {skill.skill_id for skill in selected}
        deferred.extend([skill for skill in all_skills if skill.skill_id not in selected_ids and skill not in deferred])
        deferred = self._dedupe(deferred)

        forbidden = sorted(set(capability.forbidden_actions + [action for skill in selected for action in skill.forbidden_actions]))
        checks = sorted(set(capability.default_output_checks + [check for skill in selected for check in skill.output_checks]))
        trace.update(
            {
                "available_skill_count": len(all_skills),
                "selected_skill_ids": [skill.skill_id for skill in selected],
                "deferred_skill_ids": [skill.skill_id for skill in deferred],
                "allowed_categories": sorted(allowed_categories),
            }
        )
        return SkillResolutionResult(
            always_on_skills=always_on,
            selected_skills=selected,
            deferred_skills=deferred,
            forbidden_actions=forbidden,
            required_output_checks=checks,
            reason=reasons,
            trace=trace,
        )

    def _is_allowed(self, skill: SkillDescriptor, role: str, allowed_categories: set[str]) -> bool:
        if skill.normalized_agents and role not in skill.normalized_agents:
            return False
        return skill.category in allowed_categories

    def _dedupe(self, skills: list[SkillDescriptor]) -> list[SkillDescriptor]:
        seen: set[str] = set()
        unique: list[SkillDescriptor] = []
        for skill in skills:
            if skill.skill_id in seen:
                continue
            seen.add(skill.skill_id)
            unique.append(skill)
        return unique


class SkillCompressor:
    def compress(
        self,
        selected_skills: list[SkillDescriptor],
        *,
        max_skill_tokens: int,
        allow_full_policy: bool = False,
        deferred_skill_ids: list[str] | None = None,
    ) -> SkillCompressionResult:
        ordered = sorted(
            selected_skills,
            key=lambda skill: (PRIORITY_RANK.get(skill.priority, 2), TOKEN_CLASS_RANK.get(skill.token_cost_class, 1), skill.skill_id),
        )
        included: list[str] = []
        deferred = list(deferred_skill_ids or [])
        blocks: list[str] = []
        used = 0
        trimmed = False
        trim_reason: str | None = None
        for skill in ordered:
            block = self._skill_block(skill, allow_full_policy=allow_full_policy)
            cost = estimate_tokens(block)
            if used + cost > max_skill_tokens:
                trimmed = True
                trim_reason = f"skill context exceeded max_skill_tokens={max_skill_tokens}"
                remaining = max_skill_tokens - used
                if remaining > 40 and skill.priority in {"critical", "high"}:
                    trimmed_block = block[: max(0, remaining * 4)]
                    blocks.append(trimmed_block)
                    included.append(skill.skill_id)
                    used += estimate_tokens(trimmed_block)
                else:
                    deferred.append(skill.skill_id)
                continue
            blocks.append(block)
            included.append(skill.skill_id)
            used += cost
        return SkillCompressionResult(
            compact_context="\n\n".join(blocks).strip(),
            estimated_tokens=estimate_tokens("\n\n".join(blocks).strip()),
            included_skill_ids=included,
            deferred_skill_ids=sorted(set(deferred)),
            trimmed=trimmed,
            trim_reason=trim_reason,
        )

    def _skill_block(self, skill: SkillDescriptor, *, allow_full_policy: bool) -> str:
        lines = [f"[skill:{skill.skill_id}]", f"category: {skill.category}", f"priority: {skill.priority}"]
        if skill.runtime_summary:
            lines.append("runtime_summary:")
            lines.extend(f"- {item}" for item in skill.runtime_summary[:6])
        if skill.hard_rules:
            lines.append("hard_rules:")
            lines.extend(f"- {item}" for item in skill.hard_rules[:6])
        if skill.decision_hooks:
            lines.append("decision_hooks:")
            lines.extend(f"- {item}" for item in skill.decision_hooks[:6])
        if skill.output_checks:
            lines.append("output_checks:")
            lines.extend(f"- {item}" for item in skill.output_checks[:6])
        if skill.forbidden_actions:
            lines.append("forbidden_actions:")
            lines.extend(f"- {item}" for item in skill.forbidden_actions[:10])
        if allow_full_policy and skill.full_policy:
            lines.append("full_policy:")
            lines.append(skill.full_policy)
        return "\n".join(lines)


class PromptBudgeter:
    def __init__(
        self,
        *,
        max_total_tokens: int | None = None,
        max_skill_tokens: int | None = None,
        max_memory_tokens: int | None = None,
        max_constitution_tokens: int | None = None,
        max_task_input_tokens: int | None = None,
    ) -> None:
        settings = get_settings()
        self.max_total_tokens = max_total_tokens or settings.max_agent_context_tokens
        self.max_skill_tokens = max_skill_tokens or settings.max_skill_context_tokens
        self.max_memory_tokens = max_memory_tokens or settings.max_memory_context_tokens
        self.max_constitution_tokens = max_constitution_tokens or settings.max_constitution_tokens
        self.max_task_input_tokens = max_task_input_tokens or settings.max_task_input_tokens

    def trim_text(self, text: str | None, max_tokens: int) -> tuple[str, bool]:
        text = text or ""
        if estimate_tokens(text) <= max_tokens:
            return text, False
        if max_tokens <= 0:
            return "", bool(text)
        return text[: max_tokens * 4], True

    def trim_value(self, value: Any, max_tokens: int) -> tuple[Any, int, bool]:
        if value is None:
            return None, 0, False
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, default=str, sort_keys=True)
        trimmed_text, trimmed = self.trim_text(text, max_tokens)
        if not trimmed:
            return value, estimate_tokens(text), False
        if isinstance(value, str):
            return trimmed_text, estimate_tokens(trimmed_text), True
        return {"_trimmed": True, "content": trimmed_text}, estimate_tokens(trimmed_text), True

    def fit_sections(
        self,
        *,
        system_prompt: str,
        skill_context: str,
        constitution: Any,
        memory_context: Any,
        task_input: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        skill_context, skills_trimmed = self.trim_text(skill_context, self.max_skill_tokens)
        constitution_value, constitution_tokens, constitution_trimmed = self.trim_value(constitution, self.max_constitution_tokens)
        memory_value, memory_tokens, memory_trimmed = self.trim_value(memory_context, self.max_memory_tokens)
        task_value, task_tokens, task_trimmed = self.trim_value(task_input, self.max_task_input_tokens)
        system_tokens = estimate_tokens(system_prompt)
        skills_tokens = estimate_tokens(skill_context)

        total = system_tokens + skills_tokens + constitution_tokens + memory_tokens + task_tokens
        trimmed_reasons: list[str] = []
        if skills_trimmed:
            trimmed_reasons.append("skills")
        if constitution_trimmed:
            trimmed_reasons.append("constitution")
        if memory_trimmed:
            trimmed_reasons.append("memory")
        if task_trimmed:
            trimmed_reasons.append("task_input")

        if total > self.max_total_tokens:
            overflow = total - self.max_total_tokens
            task_budget = max(100, task_tokens - overflow)
            task_value, task_tokens, task_trimmed_again = self.trim_value(task_value, task_budget)
            if task_trimmed_again:
                trimmed_reasons.append("total_budget_task_input")
            total = system_tokens + skills_tokens + constitution_tokens + memory_tokens + task_tokens
        if total > self.max_total_tokens:
            overflow = total - self.max_total_tokens
            memory_budget = max(100, memory_tokens - overflow)
            memory_value, memory_tokens, memory_trimmed_again = self.trim_value(memory_value, memory_budget)
            if memory_trimmed_again:
                trimmed_reasons.append("total_budget_memory")
            total = system_tokens + skills_tokens + constitution_tokens + memory_tokens + task_tokens

        trace = {
            "max_total": self.max_total_tokens,
            "system_tokens": system_tokens,
            "skills_tokens": skills_tokens,
            "constitution_tokens": constitution_tokens,
            "memory_tokens": memory_tokens,
            "task_input_tokens": task_tokens,
            "estimated_total_tokens": total,
            "trimmed": bool(trimmed_reasons),
            "trim_reason": ", ".join(dict.fromkeys(trimmed_reasons)) or None,
            "section_limits": {
                "skills": self.max_skill_tokens,
                "constitution": self.max_constitution_tokens,
                "memory": self.max_memory_tokens,
                "task_input": self.max_task_input_tokens,
            },
        }
        return (
            {
                "skill_context": skill_context,
                "constitution": constitution_value,
                "memory_context": memory_value,
                "task_input": task_value,
            },
            trace,
        )
