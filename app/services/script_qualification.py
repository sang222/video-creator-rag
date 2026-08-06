"""Current topic eligibility and durable, pre-admission script qualification.

The service deliberately makes two authority boundaries explicit:

* editorial planning may frame the video, but it cannot support a factual
  narration claim; and
* a writer's manifest is an input to verification, never the final claim
  inventory or PASS decision.

The paid-call path is outbox driven.  A stable logical identity is committed
before dispatch.  If a worker dies after a provider dispatch but before a
durable result, the run fails closed rather than spending again.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.script_qualification import (
    QualifiedScriptOutput,
    ScriptAssignmentResolution,
    ScriptRuntimeContract,
    SemanticVerificationOutput,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.m5 import EditorialIdeaCandidate, SearchDemandEvidence
from app.db.models.channel import ChannelProfileVersion, CompiledChannelPolicySnapshot
from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion
from app.db.models.script_qualification import (
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    ScriptQualificationReceipt,
    ScriptQualificationRun,
)
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.services.config_registry import content_hash
from app.services.production_package import ChannelDurationContractResolver
from app.services.script_qualification_authority import (
    hashed_payload,
    validate_memory_digest,
)


TOPIC_GATE_VERSION = "editorial-topic-definition-gate.v1"
QUALIFICATION_POLICY_VERSION = "script-qualification-policy.v2"
WRITER_PROMPT_VERSION = "script-writer-assignment.v2"
VERIFIER_PROMPT_VERSION = "script-semantic-verifier.v2"
SCRIPT_QUALIFICATION_EVENT_TYPE = "script_qualification.execute.v1"
SCRIPT_QUALIFICATION_AGGREGATE_TYPE = "script_qualification_run"
LUNA_MODEL = "gpt-5.6-luna"

_DISCOVERY_PATHS = {"", "/", "/docs", "/docs/", "/index", "/index/"}
_BOILERPLATE_ANGLES = (
    "source-grounded standalone explanation constrained to the fetched official documentation",
    "source grounded standalone explanation",
)


def canonical_hash(value: Any) -> str:
    return content_hash(value)


def canonical_script_bytes(script: str) -> bytes:
    return unicodedata.normalize("NFC", script).replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def script_hash(script: str) -> str:
    return hashlib.sha256(canonical_script_bytes(script)).hexdigest()


def span_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    return sorted({_clean(item) for item in value if _clean(item)}) if isinstance(value, list) else []


class ScriptRuntimeContractResolver:
    """Resolve the exact channel authority consumed later by support preparation."""

    VERSION = "script-runtime-contract.v1"
    DURATION_ESTIMATION_WPM = 150

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(self, *, policy_snapshot_id: uuid.UUID) -> dict[str, Any]:
        policy = self.session.get(CompiledChannelPolicySnapshot, policy_snapshot_id)
        if policy is None:
            raise ValidationFailureError("SCRIPT_RUNTIME_CONTRACT_POLICY_MISSING")
        profile = self.session.get(ChannelProfileVersion, policy.channel_profile_version_id)
        if profile is None or profile.channel_workspace_id != policy.channel_workspace_id:
            raise ValidationFailureError("SCRIPT_RUNTIME_CONTRACT_PROFILE_MISSING")
        duration = ChannelDurationContractResolver(self.session).resolve(
            profile_version_id=profile.id,
            policy_snapshot_id=policy.id,
        )
        compiled = policy.compiled_payload if isinstance(policy.compiled_payload, dict) else {}
        channel_contract = compiled.get("channel_contract_json") if isinstance(compiled.get("channel_contract_json"), dict) else {}
        market = channel_contract.get("market_locale") if isinstance(channel_contract.get("market_locale"), dict) else {}
        editorial = channel_contract.get("editorial_strategy") if isinstance(channel_contract.get("editorial_strategy"), dict) else {}
        voice = channel_contract.get("voice_style") if isinstance(channel_contract.get("voice_style"), dict) else {}
        expected_language = _clean(market.get("content_language"))
        if not expected_language:
            raise ValidationFailureError("SCRIPT_RUNTIME_CONTRACT_EXPECTED_LANGUAGE_MISSING")
        body = {
            "schema_version": self.VERSION,
            "expected_language": expected_language,
            "duration_contract": duration.model_dump(mode="json"),
            "duration_estimation_method": "WORD_COUNT_WPM",
            "duration_estimation_wpm": self.DURATION_ESTIMATION_WPM,
            "minimum_major_sections": 3,
            "minimum_material_claims": 3,
            "forbidden_claims": _string_list(editorial.get("forbidden_claims")),
            "forbidden_style_terms": _string_list(voice.get("forbidden_style")),
            "channel_profile_version_id": str(profile.id),
            "channel_profile_hash": str(profile.profile_input_hash),
            "compiled_policy_snapshot_id": str(policy.id),
            "compiled_policy_snapshot_hash": str(policy.content_hash),
        }
        return hashed_payload(body, "contract_hash")

    @staticmethod
    def validate(contract: Any, *, expected_hash: str | None = None) -> dict[str, Any]:
        if not isinstance(contract, dict):
            raise ValidationFailureError("SCRIPT_RUNTIME_CONTRACT_MISSING")
        try:
            typed = ScriptRuntimeContract.model_validate(contract)
        except ValueError as exc:
            raise ValidationFailureError("SCRIPT_RUNTIME_CONTRACT_INVALID") from exc
        expected = canonical_hash({key: value for key, value in typed.model_dump(mode="json").items() if key != "contract_hash"})
        if typed.contract_hash != expected or (expected_hash is not None and typed.contract_hash != expected_hash):
            raise ValidationFailureError("SCRIPT_RUNTIME_CONTRACT_HASH_MISMATCH")
        return typed.model_dump(mode="json")


def _source_snapshot(evidence: SearchDemandEvidence) -> dict[str, Any]:
    fresh = (evidence.metadata_ or {}).get("editorial_fresh_evidence") or {}
    snapshot = fresh.get("source_snapshot") if isinstance(fresh, dict) else {}
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def classify_source(evidence: SearchDemandEvidence) -> str:
    """Classify discovery-only sources without trusting their page title."""

    snapshot = _source_snapshot(evidence)
    source_ref = _clean(snapshot.get("canonical_url") or evidence.source_ref)
    path = urlparse(source_ref).path.rstrip("/")
    title = _clean(snapshot.get("title")).casefold()
    if path in _DISCOVERY_PATHS or "documentation index" in title or title.endswith(" developers"):
        return "DISCOVERY_ONLY"
    return "TOPIC_CAPABLE"


def _evidence_ref(evidence: SearchDemandEvidence) -> dict[str, Any]:
    snapshot = _source_snapshot(evidence)
    return {
        "type": "search_demand_evidence",
        "id": str(evidence.id),
        "ref": evidence.source_ref,
        "content_hash": _clean(snapshot.get("content_hash")) or canonical_hash(snapshot),
        "source_class": snapshot.get("source_class"),
        "source_classification": classify_source(evidence),
    }


@dataclass(frozen=True, slots=True)
class TopicEligibility:
    eligible: bool
    state: str
    primary_reason_code: str
    reason_codes: tuple[str, ...]
    definition: EditorialTopicDefinition | None = None
    receipt: EditorialTopicDefinitionGateReceipt | None = None


class TopicDefinitionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def current_eligibility(self, candidate: EditorialIdeaCandidate) -> TopicEligibility:
        definition = self.session.scalar(
            select(EditorialTopicDefinition)
            .where(EditorialTopicDefinition.editorial_idea_candidate_id == candidate.id)
            .order_by(EditorialTopicDefinition.topic_definition_version.desc())
        )
        if definition is None:
            return TopicEligibility(
                False, "BLOCK", "EDITORIAL_SUBJECT_NOT_IDENTIFIED",
                ("EDITORIAL_SUBJECT_NOT_IDENTIFIED", "EDITORIAL_TOPIC_DEFINITION_MISSING"),
            )
        receipt = self.session.scalar(
            select(EditorialTopicDefinitionGateReceipt)
            .where(EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id == definition.id)
            .where(EditorialTopicDefinitionGateReceipt.gate_version == TOPIC_GATE_VERSION)
        )
        if receipt is None:
            return TopicEligibility(
                False, "BLOCK", "EDITORIAL_TOPIC_GATE_RECEIPT_MISSING",
                ("EDITORIAL_TOPIC_GATE_RECEIPT_MISSING",), definition,
            )
        if receipt.current_production_eligibility:
            qualification = self.session.scalar(
                select(ScriptQualificationRun)
                .where(
                    ScriptQualificationRun.editorial_idea_candidate_id == candidate.id
                )
                .order_by(
                    ScriptQualificationRun.created_at.desc(),
                    ScriptQualificationRun.id.desc(),
                )
            )
            if qualification is not None:
                if qualification.state in {
                    "RESERVED",
                    "WRITER_DISPATCHED",
                    "SCRIPT_GENERATED",
                    "STRUCTURAL_CHECKED",
                    "CLAIM_INVENTORY_CHECKED",
                    "GROUNDING_CHECKED",
                    "VERIFIER_DISPATCHED",
                    "EDITORIAL_CHECKED",
                    "MEMORY_CHECKED",
                    "REPAIRABLE_BLOCK",
                    "REPAIR_DISPATCHED",
                    "REVERIFYING",
                }:
                    return TopicEligibility(
                        False,
                        "PENDING",
                        "SCRIPT_QUALIFICATION_PENDING",
                        ("SCRIPT_QUALIFICATION_PENDING",),
                        definition,
                        receipt,
                    )
                if qualification.state == "QUALIFIED":
                    try:
                        ScriptQualificationService(self.session).require_pass(
                            qualification.id,
                            candidate_id=candidate.id,
                        )
                    except ValidationFailureError:
                        return TopicEligibility(
                            False,
                            "BLOCK",
                            "SCRIPT_QUALIFICATION_CURRENT_AUTHORITY_REQUIRED",
                            ("SCRIPT_QUALIFICATION_CURRENT_AUTHORITY_REQUIRED",),
                            definition,
                            receipt,
                        )
                if qualification.state in {
                    "BLOCKED_NON_REPAIRABLE",
                    "BLOCKED_REPAIR_BUDGET_EXHAUSTED",
                    "COOLDOWN",
                    "SUPERSEDED",
                }:
                    return TopicEligibility(
                        False,
                        "BLOCK",
                        "SCRIPT_QUALIFICATION_BLOCKED",
                        ("SCRIPT_QUALIFICATION_BLOCKED", qualification.state),
                        definition,
                        receipt,
                    )
        return TopicEligibility(
            bool(receipt.current_production_eligibility), receipt.state,
            receipt.primary_reason_code or ("EDITORIAL_TOPIC_GATE_PASS" if receipt.state == "PASS" else "EDITORIAL_TOPIC_GATE_BLOCK"),
            tuple(receipt.reason_codes or []), definition, receipt,
        )

    def create(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        fields: dict[str, Any],
        parent_topic_definition_id: uuid.UUID | None = None,
    ) -> EditorialTopicDefinition:
        prior = self.session.scalar(
            select(EditorialTopicDefinition)
            .where(EditorialTopicDefinition.editorial_idea_candidate_id == candidate.id)
            .order_by(EditorialTopicDefinition.topic_definition_version.desc())
        )
        version = 1 if prior is None else prior.topic_definition_version + 1
        body = {
            "subject_type": _clean(fields.get("subject_type")),
            "subject_name": _clean(fields.get("subject_name")),
            "subject_canonical_id": _clean(fields.get("subject_canonical_id")),
            "subject_evidence_refs": list(fields.get("subject_evidence_refs") or []),
            "subject_evidence_spans": list(fields.get("subject_evidence_spans") or []),
            "target_audience": _clean(fields.get("target_audience")),
            "audience_problem": _clean(fields.get("audience_problem")),
            "content_pillar": _clean(fields.get("content_pillar")),
            "production_goal": _clean(fields.get("production_goal")),
            "scope_inclusions": list(fields.get("scope_inclusions") or []),
            "exclusions": list(fields.get("exclusions") or []),
            "central_question_or_thesis": _clean(fields.get("central_question_or_thesis")),
            "learning_outcome": _clean(fields.get("learning_outcome")),
            "viewer_value": _clean(fields.get("viewer_value")),
            "content_mode": _clean(fields.get("content_mode")),
            "channel_contract_ref": dict(fields.get("channel_contract_ref") or {}),
            "source_classification_refs": list(fields.get("source_classification_refs") or []),
            "series_binding": fields.get("series_binding"),
            "standalone_self_containment_required": bool(fields.get("standalone_self_containment_required")),
        }
        digest = canonical_hash({"candidate_id": str(candidate.id), "version": version, **body})
        existing = self.session.scalar(select(EditorialTopicDefinition).where(EditorialTopicDefinition.topic_definition_hash == digest))
        if existing is not None:
            return existing
        definition = EditorialTopicDefinition(
            editorial_idea_candidate_id=candidate.id, company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id, policy_snapshot_id=candidate.policy_snapshot_id,
            topic_definition_version=version, topic_definition_hash=digest,
            parent_topic_definition_id=parent_topic_definition_id, **body,
        )
        self.session.add(definition)
        self.session.flush()
        return definition

    def evaluate(self, definition: EditorialTopicDefinition) -> EditorialTopicDefinitionGateReceipt:
        existing = self.session.scalar(
            select(EditorialTopicDefinitionGateReceipt)
            .where(EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id == definition.id)
            .where(EditorialTopicDefinitionGateReceipt.gate_version == TOPIC_GATE_VERSION)
        )
        if existing is not None:
            return existing
        candidate = self.session.get(EditorialIdeaCandidate, definition.editorial_idea_candidate_id)
        if candidate is None:
            raise NotFoundError(f"candidate not found: {definition.editorial_idea_candidate_id}")
        reasons: list[str] = []
        required = {
            "subject_name": definition.subject_name, "subject_canonical_id": definition.subject_canonical_id,
            "target_audience": definition.target_audience, "audience_problem": definition.audience_problem,
            "content_pillar": definition.content_pillar, "production_goal": definition.production_goal,
            "central_question_or_thesis": definition.central_question_or_thesis,
            "learning_outcome": definition.learning_outcome, "viewer_value": definition.viewer_value,
        }
        for name, value in required.items():
            if not _clean(value):
                reasons.append(f"EDITORIAL_{name.upper()}_MISSING")
        if not definition.subject_evidence_refs or not definition.subject_evidence_spans:
            reasons.append("EDITORIAL_SUBJECT_EVIDENCE_MISSING")
        if not definition.scope_inclusions:
            reasons.append("EDITORIAL_SCOPE_NOT_DEFINED")
        if not definition.exclusions:
            reasons.append("EDITORIAL_EXCLUSIONS_NOT_DEFINED")
        if not definition.channel_contract_ref:
            reasons.append("EDITORIAL_CHANNEL_BINDING_MISSING")
        classifications = [item.get("source_classification") for item in definition.source_classification_refs if isinstance(item, dict)]
        if not classifications or "TOPIC_CAPABLE" not in classifications:
            reasons.append("EDITORIAL_SOURCE_DISCOVERY_ONLY")
        title = _clean(candidate.proposed_title)
        subject = _clean(definition.subject_name)
        if not title or not subject or subject.casefold() not in title.casefold():
            reasons.append("EDITORIAL_TITLE_SUBJECT_MISMATCH")
        if title.casefold() in {"openai developers", "developers", "documentation"}:
            reasons.append("EDITORIAL_TITLE_BRAND_OR_SECTION_ONLY")
        angle = _clean(candidate.proposed_angle).rstrip(".").casefold()
        if not angle or any(marker in angle for marker in _BOILERPLATE_ANGLES):
            reasons.append("EDITORIAL_ANGLE_BOILERPLATE")
        if definition.content_mode == "STANDALONE" and not definition.standalone_self_containment_required:
            reasons.append("EDITORIAL_STANDALONE_SELF_CONTAINMENT_MISSING")
        if definition.content_mode == "SERIES_EPISODE":
            reasons.extend(self._series_binding_reasons(definition=definition, candidate=candidate))
        elif definition.content_mode != "STANDALONE":
            reasons.append("EDITORIAL_CONTENT_MODE_INVALID")
        reasons = sorted(set(reasons))
        primary = reasons[0] if reasons else None
        payload = {
            "definition_hash": definition.topic_definition_hash, "candidate_hash": candidate.canonical_hash,
            "gate_version": TOPIC_GATE_VERSION, "reason_codes": reasons,
        }
        receipt = EditorialTopicDefinitionGateReceipt(
            editorial_topic_definition_id=definition.id, editorial_idea_candidate_id=candidate.id,
            gate_version=TOPIC_GATE_VERSION, state="PASS" if not reasons else "BLOCK",
            current_production_eligibility=not reasons, primary_reason_code=primary,
            reason_codes=reasons or ["EDITORIAL_TOPIC_GATE_PASS"], input_hash=canonical_hash(payload),
            receipt_hash=canonical_hash({**payload, "state": "PASS" if not reasons else "BLOCK"}),
        )
        self.session.add(receipt)
        self.session.flush()
        return receipt

    def _series_binding_reasons(
        self,
        *,
        definition: EditorialTopicDefinition,
        candidate: EditorialIdeaCandidate,
    ) -> list[str]:
        """Validate typed series intent before it can become GREENLIT work."""

        binding = definition.series_binding if isinstance(definition.series_binding, dict) else {}
        plan_ref = _clean(binding.get("series_plan_id") or binding.get("series_ref"))
        run_ref = _clean(binding.get("series_run_id") or binding.get("run_ref"))
        reasons: list[str] = []
        try:
            plan_id = uuid.UUID(plan_ref)
        except ValueError:
            plan_id = None
            reasons.append("EDITORIAL_SERIES_PLAN_ID_INVALID")
        try:
            run_id = uuid.UUID(run_ref)
        except ValueError:
            run_id = None
            reasons.append("EDITORIAL_SERIES_RUN_ID_INVALID")
        if not _clean(binding.get("episode_role")):
            reasons.append("EDITORIAL_SERIES_EPISODE_ROLE_MISSING")
        if not _clean(binding.get("episode_delta")):
            reasons.append("EDITORIAL_SERIES_EPISODE_DELTA_MISSING")
        if not _clean(binding.get("learning_outcome")):
            reasons.append("EDITORIAL_SERIES_LEARNING_OUTCOME_MISSING")
        if plan_id is None or run_id is None:
            return reasons
        plan = self.session.get(SeriesPlan, plan_id)
        run = self.session.get(SeriesRun, run_id)
        if plan is None:
            reasons.append("EDITORIAL_SERIES_PLAN_MISSING")
        if run is None:
            reasons.append("EDITORIAL_SERIES_RUN_MISSING")
        if plan is None or run is None:
            return reasons
        if plan.state != "APPROVED":
            reasons.append("EDITORIAL_SERIES_PLAN_NOT_APPROVED")
        if run.state != "ACTIVE":
            reasons.append("EDITORIAL_SERIES_RUN_NOT_ACTIVE")
        if run.series_plan_id != plan.id:
            reasons.append("EDITORIAL_SERIES_RUN_PLAN_MISMATCH")
        if (
            plan.company_id != candidate.company_id
            or plan.channel_workspace_id != candidate.channel_workspace_id
            or plan.policy_snapshot_id != candidate.policy_snapshot_id
            or run.company_id != candidate.company_id
            or run.channel_workspace_id != candidate.channel_workspace_id
            or run.policy_snapshot_id != candidate.policy_snapshot_id
            or definition.company_id != candidate.company_id
            or definition.channel_workspace_id != candidate.channel_workspace_id
            or definition.policy_snapshot_id != candidate.policy_snapshot_id
        ):
            reasons.append("EDITORIAL_SERIES_CHANNEL_POLICY_BINDING_MISMATCH")
        launch_policy = self.session.scalar(
            select(FirstChannelLaunchPolicyVersion).where(
                FirstChannelLaunchPolicyVersion.company_id == candidate.company_id,
                FirstChannelLaunchPolicyVersion.channel_workspace_id
                == candidate.channel_workspace_id,
                FirstChannelLaunchPolicyVersion.state == "APPROVED",
            )
        )
        if (
            launch_policy is None
            or launch_policy.policy_snapshot_id != candidate.policy_snapshot_id
            or launch_policy.channel_profile_version_id
            not in {plan.channel_profile_version_id, run.channel_profile_version_id}
            or plan.channel_profile_version_id != run.channel_profile_version_id
            or str(plan.id)
            not in set(
                str(item) for item in launch_policy.approved_initial_series_plan_ids
                or []
            )
        ):
            reasons.append("EDITORIAL_SERIES_LAUNCH_POLICY_NOT_PERMITTED")
        return reasons

    def topic_capable_evidence(self, candidate: EditorialIdeaCandidate) -> SearchDemandEvidence | None:
        ids = [str(item.get("id")) for item in (candidate.evidence_refs or []) if isinstance(item, dict) and item.get("id")]
        for evidence_id in ids:
            try:
                evidence = self.session.get(SearchDemandEvidence, uuid.UUID(evidence_id))
            except ValueError:
                continue
            if evidence is not None and classify_source(evidence) == "TOPIC_CAPABLE":
                return evidence
        return None

    def create_from_topic_capable_evidence(
        self,
        *, candidate: EditorialIdeaCandidate, parent_topic_definition_id: uuid.UUID | None = None,
    ) -> EditorialTopicDefinition:
        evidence = self.topic_capable_evidence(candidate)
        if evidence is None:
            raise ValidationFailureError("EDITORIAL_TOPIC_CAPABLE_SOURCE_REQUIRED")
        # A candidate already carrying approved-series intent may not be
        # silently converted into a standalone topic merely because this
        # convenience constructor lacks a durable episode reservation.
        if getattr(candidate, "suggested_series_plan_id", None) is not None:
            raise ValidationFailureError("EDITORIAL_SERIES_BINDING_REQUIRED")
        snapshot = _source_snapshot(evidence)
        title = _clean(snapshot.get("title"))
        subject = re.sub(r"\s*\|\s*OpenAI API\s*$", "", title, flags=re.I).strip()
        subject = subject or title
        audience = (candidate.target_audience_definition or {}).get("primary_persona") or "small professional teams"
        pains = (candidate.target_audience_definition or {}).get("pain_points") or []
        problem = _clean(pains[0] if pains else "need a bounded way to evaluate an official technical document before acting")
        excerpt = _clean(snapshot.get("content_excerpt"))
        subject_span = subject if subject and subject in excerpt else excerpt[: min(len(excerpt), 400)]
        evidence_ref = _evidence_ref(evidence)
        source_ref = {**evidence_ref, "source_classification": "TOPIC_CAPABLE"}
        return self.create(
            candidate=candidate,
            parent_topic_definition_id=parent_topic_definition_id,
            fields={
                "subject_type": "OFFICIAL_DOCUMENTED_PRODUCT_OR_FEATURE",
                "subject_name": subject,
                "subject_canonical_id": f"official-document:{evidence.id}",
                "subject_evidence_refs": [evidence_ref],
                "subject_evidence_spans": [{"evidence_id": str(evidence.id), "text": subject_span, "span_hash": span_hash(subject_span)}],
                "target_audience": audience, "audience_problem": problem,
                "content_pillar": candidate.proposed_pillar or "AI workflows",
                "production_goal": candidate.proposed_title,
                "scope_inclusions": [f"Only the documented scope in {snapshot.get('canonical_url') or evidence.source_ref}"],
                "exclusions": ["Undocumented performance, ROI, market, and product-family claims"],
                "central_question_or_thesis": f"What does the official documentation for {subject} establish, and what should a small team verify before relying on it?",
                "learning_outcome": f"Viewers can distinguish the documented scope of {subject} from unsupported assumptions.",
                "viewer_value": "A bounded evidence-first decision frame instead of a broad product overview.",
                "content_mode": "STANDALONE", "channel_contract_ref": {"policy_snapshot_id": str(candidate.policy_snapshot_id)},
                "source_classification_refs": [source_ref], "standalone_self_containment_required": True,
            },
        )

    def create_series_from_topic_capable_evidence(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        series_plan: SeriesPlan,
        series_run: SeriesRun,
        episode_role: str,
        episode_delta: str,
        series_learning_outcome: str,
        parent_topic_definition_id: uuid.UUID | None = None,
    ) -> EditorialTopicDefinition:
        """Production constructor for a typed, allocation-pending series topic."""

        if (
            series_plan.state != "APPROVED"
            or series_run.state != "ACTIVE"
            or series_run.series_plan_id != series_plan.id
            or series_plan.company_id != candidate.company_id
            or series_plan.channel_workspace_id != candidate.channel_workspace_id
            or series_plan.policy_snapshot_id != candidate.policy_snapshot_id
            or series_run.company_id != candidate.company_id
            or series_run.channel_workspace_id != candidate.channel_workspace_id
            or series_run.policy_snapshot_id != candidate.policy_snapshot_id
        ):
            raise ValidationFailureError("EDITORIAL_SERIES_TOPIC_AUTHORITY_INVALID")
        evidence = self.topic_capable_evidence(candidate)
        if evidence is None:
            raise ValidationFailureError("EDITORIAL_TOPIC_CAPABLE_SOURCE_REQUIRED")
        snapshot = _source_snapshot(evidence)
        title = _clean(snapshot.get("title"))
        subject = re.sub(r"\s*\|\s*OpenAI API\s*$", "", title, flags=re.I).strip() or title
        excerpt = _clean(snapshot.get("content_excerpt"))
        subject_span = subject if subject and subject in excerpt else excerpt[:400]
        evidence_ref = _evidence_ref(evidence)
        audience = (candidate.target_audience_definition or {}).get("primary_persona") or "small professional teams"
        pains = (candidate.target_audience_definition or {}).get("pain_points") or []
        problem = _clean(pains[0] if pains else "need an evidence-first workflow")
        return self.create(
            candidate=candidate,
            parent_topic_definition_id=parent_topic_definition_id,
            fields={
                "subject_type": "OFFICIAL_DOCUMENTED_PRODUCT_OR_FEATURE",
                "subject_name": subject,
                "subject_canonical_id": f"official-document:{evidence.id}",
                "subject_evidence_refs": [evidence_ref],
                "subject_evidence_spans": [{"evidence_id": str(evidence.id), "text": subject_span, "span_hash": span_hash(subject_span)}],
                "target_audience": audience,
                "audience_problem": problem,
                "content_pillar": candidate.proposed_pillar or "AI workflows",
                "production_goal": candidate.proposed_title,
                "scope_inclusions": [f"Only the documented scope in {snapshot.get('canonical_url') or evidence.source_ref}"],
                "exclusions": ["Undocumented performance, ROI, market, and product-family claims"],
                "central_question_or_thesis": f"What does the official documentation for {subject} establish for this episode of {series_plan.display_name}?",
                "learning_outcome": series_learning_outcome,
                "viewer_value": "A bounded evidence-first decision frame that advances the active series.",
                "content_mode": "SERIES_EPISODE",
                "channel_contract_ref": {"policy_snapshot_id": str(candidate.policy_snapshot_id)},
                "source_classification_refs": [{**evidence_ref, "source_classification": "TOPIC_CAPABLE"}],
                "series_binding": {
                    "series_plan_id": str(series_plan.id),
                    "series_run_id": str(series_run.id),
                    "episode_role": episode_role.strip(),
                    "episode_delta": episode_delta.strip(),
                    "learning_outcome": series_learning_outcome.strip(),
                },
                "standalone_self_containment_required": False,
            },
        )


class ScriptQualificationProducer(Protocol):
    def write(self, context: dict[str, Any], *, idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]: ...
    def verify(self, context: dict[str, Any], *, idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]: ...


class LunaScriptQualificationProducer:
    """The only production producer: OpenAI Router's Luna lanes, no fallback."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _route(self, *, lane: str, task: str, context: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        from app.services.m10_1 import LLMRouterService
        prompt = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        response = LLMRouterService(self.session).route(
            lane_name=lane, requested_task_type=task, response_format="json", prompt=prompt,
            correlation_id=idempotency_key, idempotency_key=idempotency_key,
        )
        receipt = response.model_dump(mode="json")
        if response.status != "SUCCESS" or response.selected_model != LUNA_MODEL or response.fallback_level != "PRIMARY" or not response.structured_output:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_LUNA_ROUTE_BLOCKED")
        return dict(response.structured_output), receipt

    def write(self, context: dict[str, Any], *, idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._route(lane="long_context_text", task="long_form_script", context={"task": "Return only the typed Script Writer JSON output.", **context}, idempotency_key=idempotency_key)

    def verify(self, context: dict[str, Any], *, idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._route(lane="gatekeeper_soft_review", task="factuality_review", context={"task": "Independently return only the semantic-verifier JSON output. Do not issue a gate PASS.", **context}, idempotency_key=idempotency_key)


class ScriptQualificationService:
    def __init__(self, session: Session, *, producer: ScriptQualificationProducer | None = None, now: Callable[[], Any] = utc_now) -> None:
        self.session = session
        self.producer = producer or LunaScriptQualificationProducer(session)
        self.now = now

    def reserve(
        self, *, candidate: EditorialIdeaCandidate, publish_slot_id: uuid.UUID, launch_run_id: uuid.UUID,
    ) -> ScriptQualificationRun:
        # A cadence retry may arrive after the topic service correctly reports
        # the candidate as pending.  The slot-owned run is still the durable
        # idempotency authority and must be returned before reevaluating topic
        # eligibility.
        existing_slot = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.publish_slot_id == publish_slot_id)
            .with_for_update()
        )
        if existing_slot is not None:
            if (
                existing_slot.editorial_idea_candidate_id != candidate.id
                or existing_slot.launch_run_id != launch_run_id
            ):
                raise ValidationFailureError("SCRIPT_QUALIFICATION_SLOT_RESERVATION_CONFLICT")
            from app.services.series_episode_reservation import (
                EpisodeReservationAuthorityService,
            )

            EpisodeReservationAuthorityService(self.session).reserve_for_qualification(
                existing_slot
            )
            return existing_slot
        topic = TopicDefinitionService(self.session).current_eligibility(candidate)
        if not topic.eligible or topic.definition is None:
            raise ValidationFailureError(topic.primary_reason_code)
        assignment = self._assignment(candidate, topic.definition)
        evidence_pack = self._factual_evidence_pack(candidate, assignment)
        memory = self._assignment_memory_digest(topic.definition, assignment)
        runtime_contract = ScriptRuntimeContractResolver(self.session).resolve(
            policy_snapshot_id=topic.definition.policy_snapshot_id
        )
        assignment_resolution = self._assignment_resolution(topic.definition)
        identity_body = {
            "candidate_id": str(candidate.id),
            "topic_definition_hash": topic.definition.topic_definition_hash,
            "assignment_hash": assignment["assignment_hash"], "evidence_pack_hash": evidence_pack["evidence_pack_hash"],
            "memory_digest_hash": memory["digest_hash"], "writer_prompt_version": WRITER_PROMPT_VERSION,
            "verifier_prompt_version": VERIFIER_PROMPT_VERSION, "gate_policy_version": QUALIFICATION_POLICY_VERSION,
            "runtime_contract_hash": runtime_contract["contract_hash"],
            "assignment_resolution_hash": assignment_resolution["resolution_hash"],
            "model": LUNA_MODEL, "launch_run_id": str(launch_run_id), "publish_slot_id": str(publish_slot_id),
        }
        identity = canonical_hash(identity_body)
        existing = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.logical_identity_hash == identity))
        if existing is not None:
            from app.services.series_episode_reservation import (
                EpisodeReservationAuthorityService,
            )

            EpisodeReservationAuthorityService(self.session).reserve_for_qualification(
                existing
            )
            return existing
        run = ScriptQualificationRun(
            editorial_idea_candidate_id=candidate.id, publish_slot_id=publish_slot_id, launch_run_id=launch_run_id,
            topic_definition_id=topic.definition.id, topic_definition_hash=topic.definition.topic_definition_hash,
            script_assignment=assignment, script_assignment_hash=assignment["assignment_hash"],
            factual_evidence_pack=evidence_pack, factual_evidence_pack_hash=evidence_pack["evidence_pack_hash"],
            memory_digest=memory, memory_digest_hash=memory["digest_hash"], writer_prompt_version=WRITER_PROMPT_VERSION,
            runtime_contract=runtime_contract, runtime_contract_hash=runtime_contract["contract_hash"],
            assignment_resolution=assignment_resolution, assignment_resolution_hash=assignment_resolution["resolution_hash"],
            verifier_prompt_version=VERIFIER_PROMPT_VERSION, gate_policy_version=QUALIFICATION_POLICY_VERSION,
            model=LUNA_MODEL, logical_attempt_number=1, logical_identity_hash=identity, state="RESERVED",
            writer_attempt_key=f"{identity}:writer", verifier_attempt_key=f"{identity}:verifier",
        )
        self.session.add(run)
        self.session.flush()
        # SERIES_EPISODE owns its exact SeriesRun identity before either LLM
        # boundary.  STANDALONE explicitly owns no such reservation.
        from app.services.series_episode_reservation import (
            EpisodeReservationAuthorityService,
        )

        EpisodeReservationAuthorityService(self.session).reserve_for_qualification(run)
        # A series TopicDefinition owns intent, not a speculative episode
        # number.  The reservation authority has now allocated the exact
        # number under the SeriesRun lock, before any provider call.  Bind the
        # durable attempt identity to that final authority.
        self._refresh_logical_identity_after_allocation(run)
        identity = run.logical_identity_hash
        payload = {"script_qualification_run_id": str(run.id), "logical_identity_hash": identity}
        command_id = f"script-qualification:{identity}"
        self.session.add(DomainEvent(
            id=uuid.uuid5(uuid.NAMESPACE_URL, command_id), event_type=SCRIPT_QUALIFICATION_EVENT_TYPE,
            event_version=1, aggregate_type=SCRIPT_QUALIFICATION_AGGREGATE_TYPE, aggregate_id=run.id,
            company_id=candidate.company_id, channel_workspace_id=candidate.channel_workspace_id, workflow_run_id=None,
            correlation_id=f"script-qualification:{run.id}", command_id=command_id, payload_hash=canonical_hash(payload), payload=payload,
            metadata_={"queue_name": "production-workflow", "retry_policy": {"policy_key": "script-qualification-v1", "automatic_retry_allowed": False, "provider_substitution_allowed": False, "finalization_retry_allowed": True}},
            # Provider execution is never retried.  The extra bounded attempts
            # are reserved solely for a zero-provider-spend final-admission
            # replay after the qualification receipt has already been sealed.
            attempt_count=0, max_attempts=3, next_attempt_at=self.now(), occurred_at=self.now(),
        ))
        self.session.flush()
        return run

    def execute(self, run_id: uuid.UUID) -> ScriptQualificationRun:
        run = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.id == run_id).with_for_update())
        if run is None:
            raise NotFoundError(f"script qualification run not found: {run_id}")
        if run.state in {"QUALIFIED", "BLOCKED_NON_REPAIRABLE", "BLOCKED_REPAIR_BUDGET_EXHAUSTED", "COOLDOWN", "SUPERSEDED"}:
            return run
        # A previous worker may have reached the provider boundary but not
        # durably recorded its response.  Retrying would risk duplicate spend.
        if run.state in {"WRITER_DISPATCHED", "VERIFIER_DISPATCHED", "REPAIR_DISPATCHED", "REVERIFYING"}:
            return self._block_unknown_provider_outcome(run)
        if run.state != "RESERVED":
            return self._block(run, "SCRIPT_QUALIFICATION_STATE_INVALID")
        try:
            ScriptRuntimeContractResolver.validate(
                run.runtime_contract, expected_hash=run.runtime_contract_hash
            )
            self._validate_assignment_resolution(run)
            validate_memory_digest(run.memory_digest, expected_hash=run.memory_digest_hash)
            from app.services.series_episode_reservation import (
                EpisodeReservationAuthorityService,
            )

            EpisodeReservationAuthorityService(self.session).require_current(run)
        except (ValidationFailureError, ValueError) as exc:
            return self._block(run, str(exc) or "SCRIPT_QUALIFICATION_CURRENT_AUTHORITY_INVALID")
        run.state = "WRITER_DISPATCHED"
        self.session.flush()
        self.session.commit()
        writer_context = self._writer_context(run)
        try:
            writer_output, writer_receipt = self.producer.write(writer_context, idempotency_key=run.writer_attempt_key)
            draft = QualifiedScriptOutput.model_validate(writer_output)
        except Exception as exc:
            return self._block_after_dispatch(run.id, "SCRIPT_WRITER_FAILED", exc)
        run = self._locked(run.id)
        run.script_payload = draft.model_dump(mode="json")
        run.writer_receipt = self._freeze_producer_provenance(
            receipt=writer_receipt,
            input_context=writer_context,
            accepted_output=draft.model_dump(mode="json"),
            prompt_version=run.writer_prompt_version,
            attempt_key=run.writer_attempt_key,
        )
        run.state = "SCRIPT_GENERATED"
        self.session.flush()
        self.session.commit()
        run = self._locked(run.id)
        structural = self._structural_receipt(run, draft)
        if structural["status"] != "PASS":
            return self._seal_block(run, draft, {"structural": structural})
        run.state = "STRUCTURAL_CHECKED"
        self.session.flush()
        self.session.commit()
        run = self._locked(run.id)
        run.state = "VERIFIER_DISPATCHED"
        self.session.flush()
        self.session.commit()
        verifier_context = self._verifier_context(run, draft)
        try:
            verifier_output, verifier_receipt = self.producer.verify(verifier_context, idempotency_key=run.verifier_attempt_key)
            verifier = SemanticVerificationOutput.model_validate(verifier_output)
        except Exception as exc:
            return self._block_after_dispatch(run.id, "SCRIPT_VERIFIER_FAILED", exc)
        run = self._locked(run.id)
        run.verifier_receipt = self._freeze_producer_provenance(
            receipt=verifier_receipt,
            input_context=verifier_context,
            accepted_output=verifier.model_dump(mode="json"),
            prompt_version=run.verifier_prompt_version,
            attempt_key=run.verifier_attempt_key,
        )
        receipts = self._semantic_receipts(run, draft, verifier, structural)
        result = "PASS" if all(item["status"] in {"PASS", "PASS_EMPTY"} for item in receipts.values()) else "BLOCK"
        if result == "PASS":
            run.state = "QUALIFIED"
            run.result_receipts = receipts
            self._create_receipt(run, draft, "PASS", receipts)
        else:
            return self._seal_block(run, draft, receipts)
        self.session.flush()
        return run

    def require_pass(self, run_id: uuid.UUID, *, candidate_id: uuid.UUID | None = None) -> ScriptQualificationReceipt:
        run = self.session.get(ScriptQualificationRun, run_id)
        receipt = self.session.scalar(select(ScriptQualificationReceipt).where(ScriptQualificationReceipt.script_qualification_run_id == run_id))
        if run is None or receipt is None or run.state != "QUALIFIED" or receipt.result != "PASS":
            raise ValidationFailureError("SCRIPT_QUALIFICATION_NOT_PASS")
        if candidate_id is not None and run.editorial_idea_candidate_id != candidate_id:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_CANDIDATE_MISMATCH")
        if receipt.script_assignment_hash != run.script_assignment_hash or receipt.factual_evidence_pack_hash != run.factual_evidence_pack_hash:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECEIPT_STALE")
        try:
            runtime_contract = ScriptRuntimeContractResolver.validate(
                run.runtime_contract, expected_hash=run.runtime_contract_hash
            )
            resolution = self._validate_assignment_resolution(run)
            validate_memory_digest(run.memory_digest, expected_hash=run.memory_digest_hash)
            from app.services.series_episode_reservation import (
                EpisodeReservationAuthorityService,
            )

            EpisodeReservationAuthorityService(self.session).require_current(run)
        except (ValidationFailureError, ValueError) as exc:
            raise ValidationFailureError(str(exc) or "SCRIPT_QUALIFICATION_RECEIPT_STALE") from exc
        content = receipt.content if isinstance(receipt.content, dict) else {}
        if content.get("schema_version") != "script-qualification-receipt.v3":
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECEIPT_VERSION_STALE")
        if "qualified_script" in content and (
            canonical_hash(content) != receipt.content_hash
            or content.get("script_hash") != receipt.script_hash
            or content.get("assignment_hash") != receipt.script_assignment_hash
            or content.get("evidence_pack_hash") != receipt.factual_evidence_pack_hash
            or content.get("runtime_contract_hash") != runtime_contract["contract_hash"]
            or content.get("assignment_resolution_hash") != resolution["resolution_hash"]
        ):
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECEIPT_CONTENT_HASH_MISMATCH")
        try:
            validate_memory_digest(content.get("memory_digest"), expected_hash=run.memory_digest_hash)
        except ValueError as exc:
            raise ValidationFailureError(str(exc)) from exc
        return receipt

    @staticmethod
    def qualification_output(receipt: ScriptQualificationReceipt) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Read the immutable qualification projection carried by its receipt."""

        content = receipt.content if isinstance(receipt.content, dict) else {}
        script = content.get("qualified_script")
        evidence = content.get("factual_evidence_pack")
        memory = content.get("memory_digest")
        provenance = content.get("producer_provenance")
        if not all(isinstance(item, dict) for item in (script, evidence, memory, provenance)):
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECEIPT_OUTPUT_MISSING")
        # Current packs carry a self-describing hash of their body.  Retain
        # read compatibility for the initial immutable receipt shape, whose
        # stored receipt hash covered the whole evidence mapping and therefore
        # did not repeat an ``evidence_pack_hash`` field.  Both variants bind
        # every evidence value to the receipt hash; an embedded hash, when
        # present, is never optional or advisory.
        embedded_evidence_hash = evidence.get("evidence_pack_hash")
        evidence_body = {
            key: value
            for key, value in evidence.items()
            if key != "evidence_pack_hash"
        }
        evidence_hash_matches = (
            (
                isinstance(embedded_evidence_hash, str)
                and embedded_evidence_hash == receipt.factual_evidence_pack_hash
                and canonical_hash(evidence_body)
                == receipt.factual_evidence_pack_hash
            )
            or (
                embedded_evidence_hash is None
                and canonical_hash(evidence) == receipt.factual_evidence_pack_hash
            )
        )
        if not evidence_hash_matches:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_RECEIPT_EVIDENCE_PACK_MISMATCH")
        try:
            validate_memory_digest(memory)
        except ValueError as exc:
            raise ValidationFailureError(str(exc)) from exc
        return dict(script), dict(evidence), dict(memory), dict(provenance)

    def _locked(self, run_id: uuid.UUID) -> ScriptQualificationRun:
        run = self.session.scalar(select(ScriptQualificationRun).where(ScriptQualificationRun.id == run_id).with_for_update())
        if run is None:
            raise NotFoundError(f"script qualification run not found: {run_id}")
        return run

    def _assignment(self, candidate: EditorialIdeaCandidate, topic: EditorialTopicDefinition) -> dict[str, Any]:
        units = [
            {"requirement_id": "subject", "requirement_type": "SUBJECT_PRESERVATION", "required": True, "obligation": topic.subject_name},
            {"requirement_id": "accepted-angle", "requirement_type": "ACCEPTED_ANGLE", "required": True, "obligation": candidate.proposed_angle},
            {"requirement_id": "question", "requirement_type": "CENTRAL_QUESTION_ANSWER", "required": True, "obligation": topic.central_question_or_thesis},
            {"requirement_id": "audience", "requirement_type": "AUDIENCE_PROBLEM", "required": True, "obligation": topic.audience_problem},
            {"requirement_id": "outcome", "requirement_type": "LEARNING_OUTCOME", "required": True, "obligation": topic.learning_outcome},
            {"requirement_id": "viewer-value", "requirement_type": "VIEWER_VALUE", "required": True, "obligation": topic.viewer_value},
            {"requirement_id": "viewer-action", "requirement_type": "VIEWER_ACTION_OR_DECISION", "required": True, "obligation": "Give the viewer a bounded next verification or adoption decision."},
        ]
        units.extend(
            {
                "requirement_id": f"scope-inclusion:{index}",
                "requirement_type": "REQUIRED_SCOPE_INCLUSION",
                "required": True,
                "obligation": inclusion,
            }
            for index, inclusion in enumerate(topic.scope_inclusions, start=1)
        )
        if topic.content_mode == "STANDALONE":
            units.append({"requirement_id": "self-containment", "requirement_type": "STANDALONE_SELF_CONTAINMENT", "required": True, "obligation": "The narration must stand alone without a previous episode."})
        else:
            units.append({"requirement_id": "episode-delta", "requirement_type": "EPISODE_DELTA", "required": True, "obligation": _clean((topic.series_binding or {}).get("episode_delta"))})
        body = {
            "schema_version": "script-assignment.v1", "topic_definition_id": str(topic.id), "topic_definition_hash": topic.topic_definition_hash,
            "subject_canonical_id": topic.subject_canonical_id, "subject_name": topic.subject_name, "candidate_title": candidate.proposed_title,
            "accepted_angle": candidate.proposed_angle, "central_question_or_thesis": topic.central_question_or_thesis,
            "target_audience": topic.target_audience, "audience_problem": topic.audience_problem,
            "scope_inclusions": topic.scope_inclusions, "exclusions": topic.exclusions, "learning_outcome": topic.learning_outcome,
            "viewer_value": topic.viewer_value, "content_mode": topic.content_mode,
            "section_role_constraints": ["HOOK", "PROBLEM", "MECHANISM", "EVIDENCE", "APPLICATION", "CLOSING_INSIGHT"],
            "required_requirement_units": units,
            "forbidden_scope_units": [
                {"forbidden_scope_id": f"forbidden-scope:{index}", "scope": exclusion}
                for index, exclusion in enumerate(topic.exclusions, start=1)
            ],
            "completion_policy_version": QUALIFICATION_POLICY_VERSION,
        }
        body["assignment_hash"] = canonical_hash(body)
        return body

    def _factual_evidence_pack(self, candidate: EditorialIdeaCandidate, assignment: dict[str, Any]) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        seen_source_ids: set[uuid.UUID] = set()
        for ref in candidate.evidence_refs or []:
            if not isinstance(ref, dict) or not ref.get("id"):
                continue
            try:
                evidence = self.session.get(SearchDemandEvidence, uuid.UUID(str(ref["id"])))
            except ValueError:
                evidence = None
            if evidence is None or evidence.authority_purpose != "CLAIM_SOURCE" or evidence.evidence_source_type not in {"OFFICIAL_DOCUMENT", "OFFICIAL_MANUAL"}:
                continue
            if evidence.id in seen_source_ids:
                continue
            snapshot = _source_snapshot(evidence)
            text = _clean(snapshot.get("content_excerpt"))
            snapshot_hash = _clean(snapshot.get("content_hash"))
            if (
                not text
                or not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash)
                or snapshot.get("freshness_state") != "FRESH"
                or snapshot.get("quality_decision") != "PASS"
                or not _clean(snapshot.get("source_class"))
            ):
                continue
            if classify_source(evidence) == "DISCOVERY_ONLY":
                continue
            seen_source_ids.add(evidence.id)
            excerpt = text[:8000]
            entries.append({
                "evidence_span_id": f"search_demand_evidence:{evidence.id}:0", "evidence_type": "search_demand_evidence", "evidence_id": str(evidence.id), "canonical_url": snapshot.get("canonical_url") or evidence.source_ref,
                "authority_purpose": evidence.authority_purpose, "evidence_source_type": evidence.evidence_source_type,
                "source_class": snapshot.get("source_class"), "source_classification": classify_source(evidence), "retrieval_receipt": ((evidence.metadata_ or {}).get("editorial_fresh_evidence") or {}).get("fetch_receipt"),
                "source_snapshot_hash": snapshot_hash, "text": excerpt, "start_byte": 0,
                "end_byte": len(excerpt.encode("utf-8")), "span_hash": span_hash(excerpt), "freshness_state": "FRESH", "source_quality_state": "PASS",
                "allowed_claim_boundaries": ["Only claims entailed by this exact snapshot span"],
                "assignment_requirement_ids": [item["requirement_id"] for item in assignment["required_requirement_units"]],
            })
        if not entries:
            raise ValidationFailureError("SCRIPT_FACTUAL_EVIDENCE_AUTHORITY_INCOMPLETE")
        body = {"schema_version": "script-factual-evidence-pack.v1", "candidate_id": str(candidate.id), "assignment_hash": assignment["assignment_hash"], "spans": entries, "excluded_unsupported_scope": assignment["forbidden_scope_units"]}
        body["evidence_pack_hash"] = canonical_hash(body)
        return body

    @staticmethod
    def _assignment_memory_digest(topic: EditorialTopicDefinition, assignment: dict[str, Any]) -> dict[str, Any]:
        # Qualification can run before a VideoProject/effective context exists.
        # The explicit safe empty digest is a controlled non-factual authority,
        # never a surrogate factual source or assignment completion signal.
        body = {"digest_type": "EMPTY_SAFE_DIGEST", "status": "EMPTY_SAFE_DIGEST", "lessons": [], "assignment_query": {"subject": topic.subject_canonical_id, "audience_problem": topic.audience_problem, "requirement_types": [item["requirement_type"] for item in assignment["required_requirement_units"]], "content_mode": topic.content_mode}, "non_factual_guidance_only": True, "no_raw_analytics": True, "no_raw_memory": True}
        return hashed_payload(body, "digest_hash")

    @staticmethod
    def _assignment_resolution(topic: EditorialTopicDefinition) -> dict[str, Any]:
        """Freeze mode before writer execution; never let admission reinterpret it."""

        if topic.content_mode == "STANDALONE":
            body = {
                "schema_version": "script-assignment-resolution.v1",
                "assignment_mode": "STANDALONE_REQUIRED",
                "content_mode": "STANDALONE",
                "standalone_reason_code": "EXPLICIT_STANDALONE_REQUIRED",
                "standalone_self_containment_required": bool(topic.standalone_self_containment_required),
                "series_plan_id": None, "series_run_id": None, "episode_number": None,
                "episode_role": None, "episode_delta": None, "series_learning_outcome": None,
                "authority_refs": {"topic_definition_id": str(topic.id), "topic_definition_hash": topic.topic_definition_hash},
            }
        else:
            binding = topic.series_binding if isinstance(topic.series_binding, dict) else {}
            plan_id = _clean(binding.get("series_plan_id") or binding.get("series_ref"))
            run_id = _clean(binding.get("series_run_id") or binding.get("run_ref"))
            body = {
                "schema_version": "script-assignment-resolution.v1",
                "assignment_mode": "SERIES_REQUIRED",
                "content_mode": "SERIES_EPISODE",
                "standalone_reason_code": None,
                "standalone_self_containment_required": False,
                "series_plan_id": plan_id or None, "series_run_id": run_id or None,
                # Only reserve() may allocate the exact episode under a
                # locked SeriesRun.  Ignore any historical/topic-proposed
                # number so two stale candidates cannot both freeze episode 1.
                "episode_number": None, "episode_role": _clean(binding.get("episode_role")) or None,
                "episode_delta": _clean(binding.get("episode_delta")) or None,
                "series_learning_outcome": _clean(binding.get("learning_outcome")) or None,
                "authority_refs": {"topic_definition_id": str(topic.id), "topic_definition_hash": topic.topic_definition_hash},
            }
        return hashed_payload(body, "resolution_hash")

    @staticmethod
    def _logical_identity(run: ScriptQualificationRun) -> str:
        return canonical_hash(
            {
                "candidate_id": str(run.editorial_idea_candidate_id),
                "topic_definition_hash": run.topic_definition_hash,
                "assignment_hash": run.script_assignment_hash,
                "evidence_pack_hash": run.factual_evidence_pack_hash,
                "memory_digest_hash": run.memory_digest_hash,
                "writer_prompt_version": run.writer_prompt_version,
                "verifier_prompt_version": run.verifier_prompt_version,
                "gate_policy_version": run.gate_policy_version,
                "runtime_contract_hash": run.runtime_contract_hash,
                "assignment_resolution_hash": run.assignment_resolution_hash,
                "model": run.model,
                "launch_run_id": str(run.launch_run_id),
                "publish_slot_id": str(run.publish_slot_id),
            }
        )

    def _refresh_logical_identity_after_allocation(
        self, run: ScriptQualificationRun
    ) -> None:
        identity = self._logical_identity(run)
        if run.logical_identity_hash == identity:
            return
        conflict = self.session.scalar(
            select(ScriptQualificationRun.id).where(
                ScriptQualificationRun.logical_identity_hash == identity,
                ScriptQualificationRun.id != run.id,
            )
        )
        if conflict is not None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_LOGICAL_IDENTITY_CONFLICT")
        run.logical_identity_hash = identity
        run.writer_attempt_key = f"{identity}:writer"
        run.verifier_attempt_key = f"{identity}:verifier"
        self.session.flush()

    @staticmethod
    def _validate_assignment_resolution(run: ScriptQualificationRun) -> dict[str, Any]:
        if not isinstance(run.assignment_resolution, dict):
            raise ValidationFailureError("SCRIPT_ASSIGNMENT_RESOLUTION_MISSING")
        try:
            typed = ScriptAssignmentResolution.model_validate(run.assignment_resolution)
        except ValueError as exc:
            raise ValidationFailureError("SCRIPT_ASSIGNMENT_RESOLUTION_INVALID") from exc
        payload = typed.model_dump(mode="json")
        expected = canonical_hash({key: value for key, value in payload.items() if key != "resolution_hash"})
        if typed.resolution_hash != expected or typed.resolution_hash != run.assignment_resolution_hash:
            raise ValidationFailureError("SCRIPT_ASSIGNMENT_RESOLUTION_HASH_MISMATCH")
        return payload

    def _writer_context(self, run: ScriptQualificationRun) -> dict[str, Any]:
        return {"script_assignment": run.script_assignment, "assignment_resolution": run.assignment_resolution, "script_runtime_contract": run.runtime_contract, "factual_evidence_pack": run.factual_evidence_pack, "memory_digest": run.memory_digest, "writer_output_contract": "canonical_script, language, sections, claims with factual evidence span ids; writer claims are hints only", "forbidden": ["Do not use planning authority or memory as factual evidence.", "Do not assert facts outside exact factual evidence spans."]}

    def _verifier_context(self, run: ScriptQualificationRun, draft: QualifiedScriptOutput) -> dict[str, Any]:
        return {"script_assignment": run.script_assignment, "assignment_resolution": run.assignment_resolution, "script_runtime_contract": run.runtime_contract, "factual_evidence_pack": run.factual_evidence_pack, "canonical_script": draft.canonical_script, "sections": [item.model_dump(mode="json") for item in draft.sections], "writer_claims": [item.model_dump(mode="json") for item in draft.claims], "verifier_output_contract": "complete material claim inventory, entailment observations, assignment fulfillment observations, exactly one forbidden-scope observation per frozen exclusion, section purposes, and memory application observations. Emit no final PASS."}

    def _structural_receipt(self, run: ScriptQualificationRun, draft: QualifiedScriptOutput) -> dict[str, Any]:
        script = unicodedata.normalize("NFC", draft.canonical_script).replace("\r\n", "\n").replace("\r", "\n")
        reasons: list[str] = []
        sections = draft.sections
        narrations = [_clean(item.narration) for item in sections]
        section_ids = [_clean(item.section_id) for item in sections]
        if draft.canonical_script != script or not script or not narrations or " ".join(narrations).strip() != script.strip():
            reasons.append("SCRIPT_STRUCTURE_CANONICALIZATION_MISMATCH")
        if len(sections) < 3:
            reasons.append("SCRIPT_STRUCTURE_INSUFFICIENT_MAJOR_SECTIONS")
        if len(section_ids) != len(set(section_ids)) or any(not item for item in section_ids):
            reasons.append("SCRIPT_STRUCTURE_SECTION_IDS_INVALID")
        runtime = run.runtime_contract if isinstance(run.runtime_contract, dict) else {}
        expected_language = _clean(runtime.get("expected_language"))
        if expected_language and draft.language.casefold() != expected_language.casefold():
            reasons.append("SCRIPT_LANGUAGE_CONTRACT_MISMATCH")
        minimum_sections = runtime.get("minimum_major_sections")
        if isinstance(minimum_sections, int) and len(sections) < minimum_sections:
            reasons.append("SCRIPT_MINIMUM_SECTION_COUNT_UNMET")
        minimum_claims = runtime.get("minimum_material_claims")
        if isinstance(minimum_claims, int) and len(draft.claims) < minimum_claims:
            reasons.append("SCRIPT_MINIMUM_MATERIAL_CLAIM_COUNT_UNMET")
        duration = runtime.get("duration_contract") if isinstance(runtime.get("duration_contract"), dict) else {}
        wpm = runtime.get("duration_estimation_wpm")
        if isinstance(wpm, int) and wpm > 0 and duration:
            words = re.findall(r"\b[\w'-]+\b", script, flags=re.UNICODE)
            estimated_duration_ms = round(len(words) / wpm * 60_000)
            if not (
                isinstance(duration.get("minimum_duration_ms"), int)
                and isinstance(duration.get("maximum_duration_ms"), int)
            ) or not duration["minimum_duration_ms"] <= estimated_duration_ms <= duration["maximum_duration_ms"]:
                reasons.append("SCRIPT_DURATION_CONTRACT_MISMATCH")
        text_folded = script.casefold()
        if any(term.casefold() in text_folded for term in _string_list(runtime.get("forbidden_claims"))):
            reasons.append("SCRIPT_FORBIDDEN_CLAIM_VIOLATION")
        if any(term.casefold() in text_folded for term in _string_list(runtime.get("forbidden_style_terms"))):
            reasons.append("SCRIPT_FORBIDDEN_STYLE_VIOLATION")
        normalized = [re.sub(r"\s+", " ", item).casefold() for item in re.split(r"(?<=[.!?])\s+", script) if item.strip()]
        if len(normalized) != len(set(normalized)):
            reasons.append("SCRIPT_STRUCTURE_REPETITION_BLOCK")
        return {"gate": "A", "status": "PASS" if not reasons else "BLOCK", "script_hash": script_hash(script), "runtime_contract_hash": runtime.get("contract_hash"), "reason_codes": sorted(set(reasons)) or ["SCRIPT_STRUCTURAL_INTEGRITY_PASS"]}

    def _semantic_receipts(self, run: ScriptQualificationRun, draft: QualifiedScriptOutput, verifier: SemanticVerificationOutput, structural: dict[str, Any]) -> dict[str, dict[str, Any]]:
        script = draft.canonical_script
        script_bytes = canonical_script_bytes(script)
        section_bounds = self._section_bounds(draft.sections)
        writer_claims = {item.claim_id: item for item in draft.claims}
        evidence = {item["evidence_span_id"]: item for item in (run.factual_evidence_pack or {}).get("spans", [])}
        inventory_reasons: list[str] = []
        grounding_reasons: list[str] = []
        seen_claims: set[str] = set()
        seen_sections: set[str] = set()
        for observation in verifier.material_claim_inventory:
            if observation.observed_claim_id in seen_claims:
                inventory_reasons.append("SCRIPT_CLAIM_INVENTORY_DUPLICATE_ID")
            seen_claims.add(observation.observed_claim_id)
            seen_sections.add(observation.span.section_id)
            span = observation.span
            actual = script_bytes[span.start_byte:span.end_byte]
            if span.end_byte > len(script_bytes) or actual != span.text.encode("utf-8") or span_hash(span.text) != span.span_hash or not self._span_in_section(span, section_bounds):
                inventory_reasons.append("SCRIPT_CLAIM_INVENTORY_SPAN_INVALID")
            if observation.materiality_state == "MATERIAL":
                if not observation.writer_declared_claim_id or observation.writer_declared_claim_id not in writer_claims:
                    inventory_reasons.append("SCRIPT_MATERIAL_CLAIM_UNDECLARED")
                    writer_claim = None
                else:
                    writer_claim = writer_claims[observation.writer_declared_claim_id]
                if writer_claim is not None:
                    observed_text = re.sub(r"\s+", " ", observation.span.text).casefold()
                    declared_text = re.sub(r"\s+", " ", writer_claim.claim_text).casefold()
                    if declared_text not in observed_text and observed_text not in declared_text:
                        inventory_reasons.append("SCRIPT_WRITER_CLAIM_SPAN_MISMATCH")
                    if len(writer_claim.evidence_span_ids) != len(set(writer_claim.evidence_span_ids)) or any(evidence_id not in evidence for evidence_id in writer_claim.evidence_span_ids):
                        inventory_reasons.append("SCRIPT_WRITER_CLAIM_EVIDENCE_UNBOUND")
                if not observation.factual_evidence_span_ids:
                    inventory_reasons.append("SCRIPT_MATERIAL_CLAIM_UNBOUND")
                if observation.claim_type in {"NON_FACTUAL_OPINION_OR_FRAMING", "STRUCTURAL_TRANSITION"}:
                    inventory_reasons.append("SCRIPT_CLAIM_CLASSIFICATION_INVALID")
                if observation.semantic_relation != "ENTAILED":
                    grounding_reasons.append(f"SCRIPT_CLAIM_{observation.semantic_relation}")
                if not observation.assignment_requirement_ids:
                    grounding_reasons.append("SCRIPT_CLAIM_ASSIGNMENT_IRRELEVANT")
                if len(observation.factual_evidence_span_ids) != len(set(observation.factual_evidence_span_ids)):
                    grounding_reasons.append("SCRIPT_MATERIAL_CLAIM_EVIDENCE_DUPLICATE")
                if writer_claim is not None and set(observation.factual_evidence_span_ids) != set(writer_claim.evidence_span_ids):
                    grounding_reasons.append("SCRIPT_MATERIAL_CLAIM_EVIDENCE_SPAN_MISMATCH")
                for evidence_id in observation.factual_evidence_span_ids:
                    if evidence_id not in evidence:
                        grounding_reasons.append("SCRIPT_MATERIAL_CLAIM_UNBOUND")
        required_sections = {item.section_id for item in draft.sections if item.section_id}
        if not required_sections <= seen_sections:
            inventory_reasons.append("SCRIPT_CLAIM_INVENTORY_INCOMPLETE")
        for start, end in self._sentence_bounds(script):
            if not any(
                item.span.start_byte <= start and item.span.end_byte >= end
                for item in verifier.material_claim_inventory
            ):
                inventory_reasons.append("SCRIPT_CLAIM_INVENTORY_UNCOVERED_SENTENCE")
        inventory = {"gate": "MATERIAL_CLAIM_INVENTORY", "status": "PASS" if not inventory_reasons else "BLOCK", "script_hash": script_hash(script), "reason_codes": sorted(set(inventory_reasons)) or ["SCRIPT_MATERIAL_CLAIM_INVENTORY_PASS"], "observed_count": len(verifier.material_claim_inventory)}
        grounding = {"gate": "B", "status": "PASS" if inventory["status"] == "PASS" and not grounding_reasons else "BLOCK", "script_hash": script_hash(script), "assignment_hash": run.script_assignment_hash, "evidence_pack_hash": run.factual_evidence_pack_hash, "reason_codes": sorted(set(grounding_reasons)) or ["SCRIPT_CLAIM_GROUNDING_PASS"]}
        fulfillment_reasons, coverage = self._fulfillment_reasons(run, verifier, script_bytes, section_bounds)
        fulfillment = {"gate": "C", "status": "PASS" if not fulfillment_reasons else "BLOCK", "script_hash": script_hash(script), "assignment_hash": run.script_assignment_hash, "reason_codes": sorted(set(fulfillment_reasons)) or ["SCRIPT_EDITORIAL_ASSIGNMENT_FULFILLMENT_PASS"], **coverage}
        memory_reasons = self._memory_reasons(run, verifier)
        try:
            memory_hash = validate_memory_digest(run.memory_digest, expected_hash=run.memory_digest_hash)
        except ValueError as exc:
            memory_reasons.append(str(exc))
            memory_hash = None
        memory = {"gate": "D", "status": "PASS_EMPTY" if (run.memory_digest or {}).get("status") == "EMPTY_SAFE_DIGEST" and not memory_reasons else ("PASS" if not memory_reasons else "BLOCK"), "script_hash": script_hash(script), "memory_digest_hash": memory_hash, "reason_codes": sorted(set(memory_reasons)) or (["SCRIPT_MEMORY_GUIDANCE_PASS_EMPTY"] if (run.memory_digest or {}).get("status") == "EMPTY_SAFE_DIGEST" else ["SCRIPT_MEMORY_GUIDANCE_PASS"])}
        return {"structural": structural, "inventory": inventory, "grounding": grounding, "fulfillment": fulfillment, "memory": memory}

    @staticmethod
    def _section_bounds(sections: list[Any]) -> dict[str, tuple[int, int]]:
        cursor = 0
        bounds: dict[str, tuple[int, int]] = {}
        for section in sections:
            section_id = _clean(getattr(section, "section_id", None))
            narration = _clean(getattr(section, "narration", None))
            start = cursor
            cursor += len(narration.encode("utf-8"))
            bounds[section_id] = (start, cursor)
            cursor += 1
        return bounds

    @staticmethod
    def _span_in_section(span: Any, bounds: dict[str, tuple[int, int]]) -> bool:
        bound = bounds.get(span.section_id)
        return bound is not None and bound[0] <= span.start_byte < span.end_byte <= bound[1]

    @staticmethod
    def _sentence_bounds(script: str) -> list[tuple[int, int]]:
        """Return byte ranges every verifier must classify, including framing."""

        bounds: list[tuple[int, int]] = []
        for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", script, flags=re.S):
            raw = match.group(0)
            text = raw.strip()
            if not text:
                continue
            leading = raw[: len(raw) - len(raw.lstrip())]
            start = len(script[: match.start()].encode("utf-8")) + len(leading.encode("utf-8"))
            end = start + len(text.encode("utf-8"))
            bounds.append((start, end))
        return bounds

    def _fulfillment_reasons(self, run: ScriptQualificationRun, verifier: SemanticVerificationOutput, script_bytes: bytes, bounds: dict[str, tuple[int, int]]) -> tuple[list[str], dict[str, int | float]]:
        reasons: list[str] = []
        requirements = {item["requirement_id"] for item in (run.script_assignment or {}).get("required_requirement_units", []) if item.get("required")}
        observed = {item.requirement_id: item for item in verifier.assignment_fulfillment_observations}
        fulfilled = 0
        coverage_owners: dict[tuple[int, int], str] = {}
        if set(observed) - requirements:
            reasons.append("SCRIPT_ASSIGNMENT_UNKNOWN_REQUIREMENT_OBSERVATION")
        for item in observed.values():
            if item.status == "OUT_OF_SCOPE":
                reasons.append(f"SCRIPT_ASSIGNMENT_OUT_OF_SCOPE:{item.requirement_id}")
        for requirement_id in requirements:
            item = observed.get(requirement_id)
            if item is None or item.status != "SUFFICIENT" or not item.spans or item.missing_reasoning_step:
                reasons.append(f"SCRIPT_ASSIGNMENT_REQUIREMENT_UNFULFILLED:{requirement_id}")
                continue
            fulfilled += 1
            for span in item.spans:
                if span.end_byte > len(script_bytes) or script_bytes[span.start_byte:span.end_byte] != span.text.encode("utf-8") or span_hash(span.text) != span.span_hash or not self._span_in_section(span, bounds):
                    reasons.append("SCRIPT_COVERAGE_MANIFEST_SPAN_INVALID")
                    continue
                if any(evidence_span_id not in {item["evidence_span_id"] for item in (run.factual_evidence_pack or {}).get("spans", [])} for evidence_span_id in item.evidence_span_ids):
                    reasons.append("SCRIPT_ASSIGNMENT_EVIDENCE_SPAN_UNBOUND")
                key = (span.start_byte, span.end_byte)
                owner = coverage_owners.setdefault(key, requirement_id)
                if owner != requirement_id:
                    reasons.append("SCRIPT_ASSIGNMENT_COVERAGE_SPAN_REUSED")
        observed_roles: dict[str, list[tuple[frozenset[str], str]]] = {}
        observed_sections: set[str] = set()
        for section in verifier.section_purpose_observations:
            if section.section_id not in bounds or section.section_id in observed_sections:
                reasons.append("SCRIPT_SECTION_PURPOSE_DUPLICATE_OBSERVATION")
            observed_sections.add(section.section_id)
            if section.genericity_state != "SPECIFIC" or not section.editorial_delta:
                reasons.append("SCRIPT_SECTION_EDITORIAL_DELTA_MISSING")
            purpose = frozenset(section.fulfilled_requirement_ids)
            if set(section.fulfilled_requirement_ids) - requirements:
                reasons.append("SCRIPT_SECTION_PURPOSE_UNKNOWN_REQUIREMENT_ID")
            delta = re.sub(r"\s+", " ", section.editorial_delta).strip().casefold()
            prior = observed_roles.setdefault(section.observed_primary_role, [])
            if prior:
                if (
                    not section.role_reuse_justification
                    or any(purpose == previous_purpose for previous_purpose, _ in prior)
                    or any(delta == previous_delta for _, previous_delta in prior)
                ):
                    reasons.append("SCRIPT_SECTION_ROLE_REUSE_INVALID")
            prior.append((purpose, delta))
        if observed_sections != set(bounds):
            reasons.append("SCRIPT_SECTION_PURPOSE_COVERAGE_INCOMPLETE")
        forbidden = {
            str(item.get("forbidden_scope_id")): item
            for item in (run.script_assignment or {}).get("forbidden_scope_units", [])
            if isinstance(item, dict) and item.get("forbidden_scope_id")
        }
        observations: dict[str, Any] = {}
        for observation in verifier.forbidden_scope_observations:
            scope_id = observation.forbidden_scope_id
            if scope_id in observations:
                reasons.append("SCRIPT_FORBIDDEN_SCOPE_OBSERVATION_DUPLICATE")
                continue
            observations[scope_id] = observation
            if scope_id not in forbidden:
                reasons.append("SCRIPT_FORBIDDEN_SCOPE_UNKNOWN_ID")
                continue
            if observation.state in {"VIOLATED", "AMBIGUOUS"}:
                reasons.append(f"SCRIPT_FORBIDDEN_SCOPE_{observation.state}:{scope_id}")
            for span in observation.script_spans:
                if (
                    span.end_byte > len(script_bytes)
                    or script_bytes[span.start_byte:span.end_byte] != span.text.encode("utf-8")
                    or span_hash(span.text) != span.span_hash
                    or not self._span_in_section(span, bounds)
                ):
                    reasons.append("SCRIPT_FORBIDDEN_SCOPE_SPAN_INVALID")
        for scope_id in forbidden:
            if scope_id not in observations:
                reasons.append(f"SCRIPT_FORBIDDEN_SCOPE_OBSERVATION_MISSING:{scope_id}")
        total = len(requirements)
        return reasons, {
            "required_requirement_count": total,
            "fulfilled_required_requirement_count": fulfilled,
            "research_coverage_ratio": round(fulfilled / total, 6) if total else 0.0,
        }

    @staticmethod
    def _memory_reasons(run: ScriptQualificationRun, verifier: SemanticVerificationOutput) -> list[str]:
        reasons: list[str] = []
        if (run.memory_digest or {}).get("status") == "EMPTY_SAFE_DIGEST":
            return reasons
        for item in verifier.memory_application_observations:
            if item.get("mandatory") and item.get("application_state") == "VIOLATED":
                reasons.append("SCRIPT_MEMORY_MANDATORY_AVOID_PATTERN_VIOLATED")
        return reasons

    @staticmethod
    def _freeze_producer_provenance(
        *,
        receipt: dict[str, Any],
        input_context: dict[str, Any],
        accepted_output: dict[str, Any],
        prompt_version: str,
        attempt_key: str,
    ) -> dict[str, Any]:
        """Seal facts observed at the two producer boundaries.

        The hashes are computed from the exact typed input handed to the
        producer and the accepted typed response.  They are not derived later
        from project/support state, which would fabricate a new provenance
        story for an already-produced script.
        """

        body = dict(receipt or {})
        body.update(
            {
                "producer_input_hash": canonical_hash(input_context),
                "producer_output_hash": canonical_hash(accepted_output),
                "prompt_version": prompt_version,
                "attempt_key": attempt_key,
                "model": str(body.get("selected_model") or LUNA_MODEL),
            }
        )
        return body

    def _create_receipt(self, run: ScriptQualificationRun, draft: QualifiedScriptOutput, result: str, receipts: dict[str, Any]) -> ScriptQualificationReceipt:
        existing = self.session.scalar(select(ScriptQualificationReceipt).where(ScriptQualificationReceipt.script_qualification_run_id == run.id))
        if existing is not None:
            return existing
        body = {"schema_version": "script-qualification-receipt.v3", "run_id": str(run.id), "result": result, "script_hash": script_hash(draft.canonical_script), "assignment_hash": run.script_assignment_hash, "evidence_pack_hash": run.factual_evidence_pack_hash, "topic_definition_hash": run.topic_definition_hash, "runtime_contract": run.runtime_contract, "runtime_contract_hash": run.runtime_contract_hash, "assignment_resolution": run.assignment_resolution, "assignment_resolution_hash": run.assignment_resolution_hash, "memory_digest_hash": run.memory_digest_hash, "receipts": receipts, "qualified_script": draft.model_dump(mode="json"), "factual_evidence_pack": run.factual_evidence_pack, "memory_digest": run.memory_digest, "producer_provenance": {"writer": run.writer_receipt, "verifier": run.verifier_receipt}, "repair_attempts": run.repair_attempts}
        receipt = ScriptQualificationReceipt(script_qualification_run_id=run.id, result=result, script_hash=body["script_hash"], script_assignment_hash=run.script_assignment_hash, factual_evidence_pack_hash=run.factual_evidence_pack_hash, content=body, content_hash=canonical_hash(body))
        self.session.add(receipt)
        return receipt

    def _seal_block(self, run: ScriptQualificationRun, draft: QualifiedScriptOutput, receipts: dict[str, Any]) -> ScriptQualificationRun:
        run.state = "BLOCKED_NON_REPAIRABLE"
        run.result_receipts = receipts
        run.failure_receipt = {"reason_codes": [code for receipt in receipts.values() for code in receipt.get("reason_codes", [])]}
        self._create_receipt(run, draft, "BLOCK", receipts)
        from app.services.script_qualification_recovery import (
            ScriptQualificationRecoveryService,
        )

        ScriptQualificationRecoveryService(self.session, now=self.now).settle_deterministic_block(
            run, reason_code="SCRIPT_QUALIFICATION_BLOCKED"
        )
        self.session.flush()
        return run

    def _block_unknown_provider_outcome(self, run: ScriptQualificationRun) -> ScriptQualificationRun:
        # A provider may have accepted the call before the worker crashed.
        # Preserve the episode identity until an explicit supersession/manual
        # remediation decision; never silently hand it to another script.
        run.state = "BLOCKED_NON_REPAIRABLE"
        run.failure_receipt = {
            "reason_codes": ["SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY"],
            "logical_identity_hash": run.logical_identity_hash,
        }
        from app.services.script_qualification_recovery import (
            ScriptQualificationRecoveryService,
        )

        ScriptQualificationRecoveryService(
            self.session, now=self.now
        ).settle_unknown_provider_outcome(run)
        self.session.flush()
        return run

    def _block_after_dispatch(self, run_id: uuid.UUID, code: str, exc: Exception) -> ScriptQualificationRun:
        run = self._locked(run_id)
        return self._block(run, code, detail=str(exc))

    def _block(self, run: ScriptQualificationRun, code: str, detail: str | None = None) -> ScriptQualificationRun:
        run.state = "BLOCKED_NON_REPAIRABLE"
        run.failure_receipt = {"reason_codes": [code], "detail": detail[:512] if detail else None, "logical_identity_hash": run.logical_identity_hash}
        from app.services.script_qualification_recovery import (
            ScriptQualificationRecoveryService,
        )

        ScriptQualificationRecoveryService(self.session, now=self.now).settle_deterministic_block(
            run, reason_code=code
        )
        self.session.flush()
        return run

    def supersede(self, run_id: uuid.UUID) -> ScriptQualificationRun:
        """Cancel an unadmitted qualification and release its episode once."""

        run = self._locked(run_id)
        if run.admitted_video_project_id is not None:
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SUPERSEDE_AFTER_ADMISSION")
        if run.state == "SUPERSEDED":
            return run
        if run.state == "QUALIFIED":
            raise ValidationFailureError("SCRIPT_QUALIFICATION_SUPERSEDE_QUALIFIED")
        run.state = "SUPERSEDED"
        from app.services.series_episode_reservation import (
            EpisodeReservationAuthorityService,
        )

        EpisodeReservationAuthorityService(self.session).release_for_terminal_qualification(
            run,
            reason_code="SCRIPT_QUALIFICATION_SUPERSEDED",
        )
        self.session.flush()
        return run
