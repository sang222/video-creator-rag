"""Typed editorial-idea synthesis and deterministic specificity authority.

An official source is evidence.  It becomes production runway work only after
an evidence-bound editorial proposal passes this gate.  This module deliberately
contains no retry loop and no score-based authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailureError
from app.db.models.m5 import EditorialIdeaCandidate, EditorialResearchRun, SearchDemandEvidence
from app.db.models.script_qualification import EditorialTopicDefinition
from app.services.config_registry import content_hash


EDITORIAL_IDEA_PROPOSAL_SCHEMA = "vcos.editorial-idea-proposal.v1"
EDITORIAL_IDEA_SYNTHESIS_VERSION = "editorial-idea-synthesis.v1"
EDITORIAL_SPECIFICITY_GATE_VERSION = "editorial-specificity-gate.v1"
MAX_EDITORIAL_IDEA_PROPOSALS_PER_RESEARCH = 3
MAX_EDITORIAL_IDEA_SYNTHESIS_CALLS_PER_REPLENISHMENT = 1

_SOURCE_CLASSES = {
    "DISCOVERY_ONLY": 0,
    "BROAD_TOPIC_CAPABLE": 1,
    "NARROW_TOPIC_CAPABLE": 2,
}
_GENERIC_TITLES = {
    "api", "apis", "model", "models", "documentation", "developers",
    "openai api", "openai developers", "responses", "guides", "reference",
}
_GENERIC_ANGLE_MARKERS = (
    "bounded standalone walkthrough",
    "what remains outside the source scope",
    "what to verify next",
    "what the documentation establishes",
    "what the official documentation",
)
_GENERIC_QUESTION_MARKERS = (
    "what does the official documentation",
    "what documentation establishes",
    "what docs establish",
    "what should users verify",
    "what should a small team verify",
)
_GENERIC_OUTCOME_MARKERS = (
    "distinguish documented scope from unsupported assumptions",
    "distinguish the documented scope",
    "understand what the documentation says",
)
_GENERIC_VIEWER_MARKERS = (
    "bounded evidence first decision frame",
    "instead of a broad product overview",
    "what to verify next",
)
_GENERIC_FUNCTION_WORDS = {
    "documentation", "document", "docs", "source", "official", "overview",
    "understand", "review", "verify", "scope", "assumptions", "information",
    "video", "viewer", "users", "teams", "small", "use", "using", "learn",
}


def _normalized(value: Any) -> str:
    from app.services.editorial_novelty import normalize_editorial_text

    return normalize_editorial_text(value)


def _proposal_hash_payload(value: dict[str, Any]) -> dict[str, Any]:
    # This intentionally has no candidate, run, response, or clock identity.
    return {key: item for key, item in value.items() if key != "proposal_hash"}


class EditorialIdeaProposal(BaseModel):
    """The editorial proposition selected from a bounded evidence pack."""

    proposal_schema_version: str = EDITORIAL_IDEA_PROPOSAL_SCHEMA
    proposed_title: str = Field(min_length=1, max_length=500)
    proposed_angle: str = Field(min_length=1, max_length=2_000)
    specific_audience_problem: str = Field(min_length=1, max_length=2_000)
    central_question_or_thesis: str = Field(min_length=1, max_length=2_000)
    learning_outcome: str = Field(min_length=1, max_length=2_000)
    viewer_value: str = Field(min_length=1, max_length=2_000)
    editorial_delta: str = Field(min_length=1, max_length=2_000)
    specific_mechanism_or_use_case: str = Field(min_length=1, max_length=2_000)
    decision_value: str = Field(min_length=1, max_length=2_000)
    scope_inclusions: list[str] = Field(min_length=1, max_length=12)
    scope_exclusions: list[str] = Field(min_length=1, max_length=12)
    primary_evidence_refs: list[dict[str, Any]] = Field(min_length=1, max_length=4)
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=6)
    # Every material viewer-facing statement is anchored to a short exact span
    # retained in the source snapshot.  The gate verifies these spans locally.
    evidence_bindings: list[dict[str, Any]] = Field(min_length=1, max_length=16)
    source_specificity_class: Literal[
        "DISCOVERY_ONLY", "BROAD_TOPIC_CAPABLE", "NARROW_TOPIC_CAPABLE"
    ]
    content_mode: Literal["STANDALONE", "SERIES_EPISODE"]
    series_binding: dict[str, Any] | None = None
    proposal_hash: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _hash_is_semantic_and_stable(self) -> "EditorialIdeaProposal":
        if self.proposal_schema_version != EDITORIAL_IDEA_PROPOSAL_SCHEMA:
            raise ValueError("EDITORIAL_IDEA_PROPOSAL_SCHEMA_INVALID")
        body = self.model_dump(mode="json", exclude={"proposal_hash"})
        digest = content_hash(_proposal_hash_payload(body))
        if self.proposal_hash is not None and self.proposal_hash != digest:
            raise ValueError("EDITORIAL_IDEA_PROPOSAL_HASH_MISMATCH")
        self.proposal_hash = digest
        return self


@dataclass(frozen=True, slots=True)
class SpecificityEvaluation:
    candidate_id: uuid.UUID
    state: Literal["PASS", "BLOCK"]
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[dict[str, Any], ...]
    proposal_hash: str
    gate_version: str
    evaluation_hash: str

    def receipt(self) -> dict[str, Any]:
        body = {
            "candidate_id": str(self.candidate_id),
            "state": self.state,
            "reason_codes": list(self.reason_codes),
            "evidence_refs": list(self.evidence_refs),
            "proposal_hash": self.proposal_hash,
            "gate_version": self.gate_version,
        }
        return {**body, "evaluation_hash": self.evaluation_hash}


@dataclass(frozen=True, slots=True)
class EditorialIdeaSynthesisResult:
    proposals: tuple[EditorialIdeaProposal, ...]
    receipt: dict[str, Any]


def _snapshot(evidence: SearchDemandEvidence) -> dict[str, Any]:
    fresh = (evidence.metadata_ or {}).get("editorial_fresh_evidence") or {}
    snapshot = fresh.get("source_snapshot") if isinstance(fresh, dict) else {}
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _ref_id(value: dict[str, Any]) -> str:
    return str(value.get("id") or "").strip()


def _concrete_editorial_function(value: Any) -> bool:
    words = [word for word in _normalized(value).split() if word not in _GENERIC_FUNCTION_WORDS]
    return len(set(words)) >= 2


class EditorialIdeaSynthesisService:
    """One bounded, structured synthesis call after bounded evidence fetches.

    The existing evidence provider only yields discovery URLs and fetched source
    snapshots; it cannot cleanly return structured proposals from that same
    response.  This is therefore the explicitly bounded one-call fallback.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def synthesize(
        self,
        *,
        research_run: EditorialResearchRun,
        evidence_refs: list[dict[str, Any]],
        content_mode: str,
        series_binding: dict[str, Any] | None,
        research_question: str,
    ) -> EditorialIdeaSynthesisResult:
        if content_mode not in {"STANDALONE", "SERIES_EPISODE"}:
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_MODE_INVALID")
        evidence = self._evidence(evidence_refs)
        if not evidence:
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_EVIDENCE_REQUIRED")
        source_pack = []
        source_class_by_id: dict[str, str] = {}
        for item in evidence:
            from app.services.script_qualification import classify_source_specificity

            snapshot = _snapshot(item)
            source_class = classify_source_specificity(item)
            source_class_by_id[str(item.id)] = source_class
            source_pack.append(
                {
                    "evidence_id": str(item.id),
                    "canonical_url": snapshot.get("canonical_url") or item.source_ref,
                    "title": snapshot.get("title"),
                    "source_specificity_class": source_class,
                    "content_excerpt": str(snapshot.get("content_excerpt") or "")[:4_000],
                }
            )
        from app.services.m10_1 import LLMRouterService

        prompt = self._prompt(
            content_mode=content_mode,
            series_binding=series_binding,
            research_question=research_question,
            source_pack=source_pack,
        )
        response = LLMRouterService(self.session).route(
            lane_name="cheap_structured",
            requested_task_type="editorial_idea_research",
            response_format="json",
            prompt=prompt,
            correlation_id=f"editorial-idea-synthesis:{research_run.id}",
            idempotency_key=(
                f"editorial-idea-synthesis:{EDITORIAL_IDEA_SYNTHESIS_VERSION}:{research_run.id}"
            ),
        )
        if response.status != "SUCCESS" or not isinstance(response.structured_output, dict):
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_PROVIDER_BLOCKED")
        raw_proposals = response.structured_output.get("proposals")
        if not isinstance(raw_proposals, list) or len(raw_proposals) > MAX_EDITORIAL_IDEA_PROPOSALS_PER_RESEARCH:
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_OUTPUT_INVALID")
        proposals: list[EditorialIdeaProposal] = []
        invalid_count = 0
        for raw in raw_proposals:
            if not isinstance(raw, dict):
                invalid_count += 1
                continue
            try:
                ref_ids = {
                    _ref_id(ref)
                    for ref in [
                        *(raw.get("primary_evidence_refs") or []),
                        *(raw.get("supporting_evidence_refs") or []),
                    ]
                    if isinstance(ref, dict)
                }
                if not ref_ids or not ref_ids.issubset(source_class_by_id):
                    raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_EVIDENCE_REF_INVALID")
                strongest_class = max(
                    (source_class_by_id[item] for item in ref_ids),
                    key=lambda item: _SOURCE_CLASSES[item],
                )
                # Source classification, semantic hash, and mode binding are
                # server authority.  The provider supplies only the editorial
                # proposition grounded in the frozen source pack.
                normalized = {
                    **raw,
                    "proposal_schema_version": EDITORIAL_IDEA_PROPOSAL_SCHEMA,
                    "source_specificity_class": strongest_class,
                    "content_mode": content_mode,
                    "series_binding": series_binding if content_mode == "SERIES_EPISODE" else None,
                }
                normalized.pop("proposal_hash", None)
                proposal = EditorialIdeaProposal.model_validate(normalized)
                self._validate_provider_proposal(
                    proposal=proposal,
                    source_class_by_id=source_class_by_id,
                    expected_mode=content_mode,
                    expected_series_binding=series_binding,
                )
            except (ValueError, ValidationFailureError):
                invalid_count += 1
                continue
            proposals.append(proposal)
        receipt = {
            "schema_version": "vcos.editorial-idea-synthesis-receipt.v1",
            "synthesis_version": EDITORIAL_IDEA_SYNTHESIS_VERSION,
            "execution_mode": "ONE_BOUNDED_ADDITIONAL_LLM_CALL",
            "max_calls": MAX_EDITORIAL_IDEA_SYNTHESIS_CALLS_PER_REPLENISHMENT,
            "provider_route_attempt_id": str(response.route_attempt_id),
            "provider_attempt_id": str(response.provider_attempt_id) if response.provider_attempt_id else None,
            "llm_run_snapshot_id": str(response.llm_run_snapshot_id),
            "source_pack_hash": content_hash(source_pack),
            "proposal_count": len(proposals),
            "invalid_proposal_count": invalid_count,
            "proposals": [item.model_dump(mode="json") for item in proposals],
        }
        receipt["receipt_hash"] = content_hash(receipt)
        return EditorialIdeaSynthesisResult(tuple(proposals), receipt)

    def _evidence(self, refs: list[dict[str, Any]]) -> list[SearchDemandEvidence]:
        rows: list[SearchDemandEvidence] = []
        seen: set[uuid.UUID] = set()
        for ref in refs:
            try:
                evidence_id = uuid.UUID(_ref_id(ref))
            except ValueError:
                continue
            if evidence_id in seen:
                continue
            row = self.session.get(SearchDemandEvidence, evidence_id)
            if row is not None:
                rows.append(row)
                seen.add(evidence_id)
        return rows

    @staticmethod
    def _prompt(
        *,
        content_mode: str,
        series_binding: dict[str, Any] | None,
        research_question: str,
        source_pack: list[dict[str, Any]],
    ) -> str:
        return (
            "Return JSON only with {\"proposals\": [...]}, containing zero to three "
            "EditorialIdeaProposal objects. A source is evidence, never an automatic "
            "video title or idea. Propose only a concrete mechanism, workflow, constraint, "
            "tradeoff, capability boundary, risk, or viewer decision explicitly supported "
            "by the supplied excerpts. Do not make performance, ROI, market, or unsupported "
            "product-family claims. Do not make a generic documentation walkthrough. "
            "Every proposal must include exactly these proposal fields: "
            "proposal_schema_version='vcos.editorial-idea-proposal.v1', proposed_title, "
            "proposed_angle, specific_audience_problem, central_question_or_thesis, "
            "learning_outcome, viewer_value, editorial_delta, specific_mechanism_or_use_case, "
            "decision_value, scope_inclusions, scope_exclusions, primary_evidence_refs, "
            "supporting_evidence_refs, evidence_bindings, source_specificity_class, content_mode, "
            "series_binding. Do not include proposal_hash; the server computes it. Evidence refs use {id, ref}; evidence_bindings use "
            "{field, evidence_id, quoted_text}, where quoted_text is an exact short quote from "
            "the supplied content_excerpt. Bind every viewer-facing material field. "
            f"The authoritative content_mode is {content_mode}; the authoritative series binding "
            f"is {series_binding or None}. The frozen discovery question is: {research_question}. "
            f"The only usable sources are: {source_pack}."
        )

    @staticmethod
    def _validate_provider_proposal(
        *,
        proposal: EditorialIdeaProposal,
        source_class_by_id: dict[str, str],
        expected_mode: str,
        expected_series_binding: dict[str, Any] | None,
    ) -> None:
        if proposal.content_mode != expected_mode:
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_MODE_MISMATCH")
        if expected_mode == "SERIES_EPISODE" and proposal.series_binding != expected_series_binding:
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_SERIES_BINDING_MISMATCH")
        ref_ids = {
            _ref_id(ref)
            for ref in [*proposal.primary_evidence_refs, *proposal.supporting_evidence_refs]
        }
        if not ref_ids or not ref_ids.issubset(source_class_by_id):
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_EVIDENCE_REF_INVALID")
        strongest_class = max(
            (source_class_by_id[item] for item in ref_ids),
            key=lambda item: _SOURCE_CLASSES[item],
        )
        if proposal.source_specificity_class != strongest_class:
            raise ValidationFailureError("EDITORIAL_IDEA_SYNTHESIS_SOURCE_CLASS_MISMATCH")


class EditorialSpecificityService:
    """Deterministically block generic or insufficiently grounded proposals."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def evaluate(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        topic: EditorialTopicDefinition,
    ) -> SpecificityEvaluation:
        reasons: list[str] = []
        proposal = self._proposal(candidate=candidate, reasons=reasons)
        proposal_hash = (
            proposal.proposal_hash
            if proposal is not None and proposal.proposal_hash
            else content_hash(
                {
                    "legacy_candidate_id": str(candidate.id),
                    "candidate_hash": candidate.canonical_hash,
                    "topic_hash": topic.topic_definition_hash,
                }
            )
        )
        evidence, source_titles, source_classes = self._evidence(candidate)
        evidence_refs = tuple(self._reference(item) for item in evidence)
        if proposal is None:
            reasons.append("EDITORIAL_IDEA_PROPOSAL_MISSING")
            self._legacy_genericity_reasons(
                candidate=candidate,
                topic=topic,
                source_titles=source_titles,
                source_classes=source_classes,
                reasons=reasons,
            )
        else:
            self._mapping_reasons(candidate=candidate, topic=topic, proposal=proposal, reasons=reasons)
            self._evidence_reasons(
                proposal=proposal,
                evidence=evidence,
                reasons=reasons,
            )
            self._genericity_reasons(
                proposal=proposal,
                source_titles=source_titles,
                source_classes=source_classes,
                reasons=reasons,
            )
        if not evidence:
            reasons.append("EDITORIAL_PROPOSAL_EVIDENCE_INSUFFICIENT")
        state: Literal["PASS", "BLOCK"] = "PASS" if not reasons else "BLOCK"
        codes = tuple(sorted(set(reasons)) or ["EDITORIAL_SPECIFICITY_PASS"])
        body = {
            "candidate_id": str(candidate.id),
            "topic_definition_hash": topic.topic_definition_hash,
            "proposal_hash": proposal_hash,
            "gate_version": EDITORIAL_SPECIFICITY_GATE_VERSION,
            "state": state,
            "reason_codes": list(codes),
            "evidence_refs": list(evidence_refs),
        }
        return SpecificityEvaluation(
            candidate_id=candidate.id,
            state=state,
            reason_codes=codes,
            evidence_refs=evidence_refs,
            proposal_hash=proposal_hash,
            gate_version=EDITORIAL_SPECIFICITY_GATE_VERSION,
            evaluation_hash=content_hash(body),
        )

    def persist(
        self,
        *,
        candidate: EditorialIdeaCandidate,
        evaluation: SpecificityEvaluation,
    ) -> None:
        candidate.editorial_specificity_receipt = evaluation.receipt()
        self.session.flush()

    def current_pass(self, candidate: EditorialIdeaCandidate) -> bool:
        receipt = candidate.editorial_specificity_receipt
        if not isinstance(receipt, dict):
            return False
        if (
            receipt.get("gate_version") != EDITORIAL_SPECIFICITY_GATE_VERSION
            or receipt.get("state") != "PASS"
        ):
            return False
        topic = self.session.scalar(
            select(EditorialTopicDefinition)
            .where(EditorialTopicDefinition.editorial_idea_candidate_id == candidate.id)
            .order_by(EditorialTopicDefinition.topic_definition_version.desc())
        )
        if topic is None:
            return False
        current = self.evaluate(candidate=candidate, topic=topic)
        return (
            current.state == "PASS"
            and receipt.get("proposal_hash") == current.proposal_hash
            and receipt.get("evaluation_hash") == current.evaluation_hash
        )

    @staticmethod
    def _reference(evidence: SearchDemandEvidence) -> dict[str, Any]:
        snapshot = _snapshot(evidence)
        return {
            "type": "search_demand_evidence",
            "id": str(evidence.id),
            "ref": snapshot.get("canonical_url") or evidence.source_ref,
            "content_hash": snapshot.get("content_hash"),
        }

    def _proposal(
        self, *, candidate: EditorialIdeaCandidate, reasons: list[str]
    ) -> EditorialIdeaProposal | None:
        raw = candidate.editorial_idea_proposal
        if not isinstance(raw, dict):
            return None
        try:
            return EditorialIdeaProposal.model_validate(raw)
        except ValueError:
            reasons.append("EDITORIAL_IDEA_PROPOSAL_INVALID")
            return None

    def _evidence(
        self, candidate: EditorialIdeaCandidate
    ) -> tuple[list[SearchDemandEvidence], list[str], list[str]]:
        rows: list[SearchDemandEvidence] = []
        titles: list[str] = []
        classes: list[str] = []
        for ref in candidate.evidence_refs or []:
            if not isinstance(ref, dict):
                continue
            try:
                evidence_id = uuid.UUID(_ref_id(ref))
            except ValueError:
                continue
            evidence = self.session.get(SearchDemandEvidence, evidence_id)
            if evidence is None:
                continue
            from app.services.script_qualification import classify_source_specificity

            rows.append(evidence)
            snapshot = _snapshot(evidence)
            if snapshot.get("title"):
                titles.append(str(snapshot["title"]))
            classes.append(classify_source_specificity(evidence))
        return rows, titles, classes

    @staticmethod
    def _mapping_reasons(
        *,
        candidate: EditorialIdeaCandidate,
        topic: EditorialTopicDefinition,
        proposal: EditorialIdeaProposal,
        reasons: list[str],
    ) -> None:
        expected = {
            "proposed_title": candidate.proposed_title,
            "proposed_angle": candidate.proposed_angle,
            "specific_audience_problem": topic.audience_problem,
            "central_question_or_thesis": topic.central_question_or_thesis,
            "learning_outcome": topic.learning_outcome,
            "viewer_value": topic.viewer_value,
        }
        for key, actual in expected.items():
            if _normalized(getattr(proposal, key)) != _normalized(actual):
                reasons.append("EDITORIAL_PROPOSAL_TOPIC_MAPPING_MISMATCH")
                break
        if sorted(_normalized(item) for item in proposal.scope_inclusions) != sorted(
            _normalized(item) for item in topic.scope_inclusions
        ):
            reasons.append("EDITORIAL_PROPOSAL_SCOPE_MAPPING_MISMATCH")
        if sorted(_normalized(item) for item in proposal.scope_exclusions) != sorted(
            _normalized(item) for item in topic.exclusions
        ):
            reasons.append("EDITORIAL_PROPOSAL_SCOPE_MAPPING_MISMATCH")
        if proposal.content_mode != topic.content_mode:
            reasons.append("EDITORIAL_PROPOSAL_CONTENT_MODE_MISMATCH")

    @staticmethod
    def _evidence_reasons(
        *,
        proposal: EditorialIdeaProposal,
        evidence: list[SearchDemandEvidence],
        reasons: list[str],
    ) -> None:
        by_id = {str(item.id): _snapshot(item) for item in evidence}
        proposal_ids = {
            _ref_id(item)
            for item in [*proposal.primary_evidence_refs, *proposal.supporting_evidence_refs]
        }
        if not proposal_ids or not proposal_ids.issubset(by_id):
            reasons.append("EDITORIAL_PROPOSAL_EVIDENCE_INSUFFICIENT")
            return
        required_fields = {
            "proposed_title",
            "proposed_angle",
            "central_question_or_thesis",
            "learning_outcome",
            "viewer_value",
            "editorial_delta",
            "specific_mechanism_or_use_case",
            "decision_value",
        }
        bound_fields: set[str] = set()
        for binding in proposal.evidence_bindings:
            if not isinstance(binding, dict):
                continue
            field = str(binding.get("field") or "")
            evidence_id = str(binding.get("evidence_id") or "")
            quote = str(binding.get("quoted_text") or "").strip()
            excerpt = str((by_id.get(evidence_id) or {}).get("content_excerpt") or "")
            if field in required_fields and quote and quote in excerpt:
                bound_fields.add(field)
        if not required_fields.issubset(bound_fields):
            reasons.append("EDITORIAL_PROPOSAL_EVIDENCE_INSUFFICIENT")

    @staticmethod
    def _genericity_reasons(
        *,
        proposal: EditorialIdeaProposal,
        source_titles: list[str],
        source_classes: list[str],
        reasons: list[str],
    ) -> None:
        title = _normalized(proposal.proposed_title)
        if title in _GENERIC_TITLES:
            reasons.append("EDITORIAL_TITLE_BROAD_SECTION_ONLY")
        if any(title == _normalized(item) for item in source_titles):
            reasons.append("EDITORIAL_TITLE_SOURCE_LABEL_ONLY")
        angle = _normalized(proposal.proposed_angle)
        if any(marker in angle for marker in _GENERIC_ANGLE_MARKERS):
            reasons.extend(
                ["EDITORIAL_ANGLE_GENERIC_WALKTHROUGH", "EDITORIAL_ANGLE_REUSABLE_TEMPLATE"]
            )
        if "source summary" in angle or "documentation says" in angle:
            reasons.append("EDITORIAL_ANGLE_SOURCE_SUMMARY")
        question = _normalized(proposal.central_question_or_thesis)
        if any(marker in question for marker in _GENERIC_QUESTION_MARKERS):
            reasons.extend(
                ["EDITORIAL_QUESTION_GENERIC_DOCUMENTATION_REVIEW", "EDITORIAL_QUESTION_NOT_DECISION_RELEVANT"]
            )
        outcome = _normalized(proposal.learning_outcome)
        if any(marker in outcome for marker in _GENERIC_OUTCOME_MARKERS):
            reasons.append("EDITORIAL_LEARNING_OUTCOME_GENERIC")
        viewer = _normalized(proposal.viewer_value)
        if any(marker in viewer for marker in _GENERIC_VIEWER_MARKERS):
            reasons.append("EDITORIAL_VIEWER_VALUE_GENERIC")
        if not _concrete_editorial_function(proposal.editorial_delta):
            reasons.append("EDITORIAL_DELTA_MISSING")
        if not _concrete_editorial_function(proposal.specific_mechanism_or_use_case):
            reasons.append("EDITORIAL_MECHANISM_OR_USE_CASE_MISSING")
        if not _concrete_editorial_function(proposal.decision_value):
            reasons.append("EDITORIAL_DECISION_VALUE_MISSING")
        if "DISCOVERY_ONLY" in source_classes:
            reasons.append("EDITORIAL_SOURCE_TOO_BROAD_FOR_PROPOSAL")
        if "BROAD_TOPIC_CAPABLE" in source_classes and (
            not _concrete_editorial_function(proposal.specific_mechanism_or_use_case)
        ):
            reasons.append("EDITORIAL_SOURCE_TOO_BROAD_FOR_PROPOSAL")

    @staticmethod
    def _legacy_genericity_reasons(
        *,
        candidate: EditorialIdeaCandidate,
        topic: EditorialTopicDefinition,
        source_titles: list[str],
        source_classes: list[str],
        reasons: list[str],
    ) -> None:
        """Explain a historical block without manufacturing a proposal PASS."""

        title = _normalized(candidate.proposed_title)
        if title in _GENERIC_TITLES:
            reasons.append("EDITORIAL_TITLE_BROAD_SECTION_ONLY")
        if any(title == _normalized(item) for item in source_titles):
            reasons.append("EDITORIAL_TITLE_SOURCE_LABEL_ONLY")
        angle = _normalized(candidate.proposed_angle)
        if any(marker in angle for marker in _GENERIC_ANGLE_MARKERS):
            reasons.extend(
                ["EDITORIAL_ANGLE_GENERIC_WALKTHROUGH", "EDITORIAL_ANGLE_REUSABLE_TEMPLATE"]
            )
        question = _normalized(topic.central_question_or_thesis)
        if any(marker in question for marker in _GENERIC_QUESTION_MARKERS):
            reasons.extend(
                ["EDITORIAL_QUESTION_GENERIC_DOCUMENTATION_REVIEW", "EDITORIAL_QUESTION_NOT_DECISION_RELEVANT"]
            )
        outcome = _normalized(topic.learning_outcome)
        if any(marker in outcome for marker in _GENERIC_OUTCOME_MARKERS):
            reasons.append("EDITORIAL_LEARNING_OUTCOME_GENERIC")
        viewer = _normalized(topic.viewer_value)
        if any(marker in viewer for marker in _GENERIC_VIEWER_MARKERS):
            reasons.append("EDITORIAL_VIEWER_VALUE_GENERIC")
        reasons.extend(
            [
                "EDITORIAL_DELTA_MISSING",
                "EDITORIAL_MECHANISM_OR_USE_CASE_MISSING",
                "EDITORIAL_DECISION_VALUE_MISSING",
                "EDITORIAL_PROPOSAL_EVIDENCE_INSUFFICIENT",
            ]
        )
        if "DISCOVERY_ONLY" in source_classes or "BROAD_TOPIC_CAPABLE" in source_classes:
            reasons.append("EDITORIAL_SOURCE_TOO_BROAD_FOR_PROPOSAL")


@dataclass(frozen=True, slots=True)
class SpecificityCleanupAction:
    candidate_id: uuid.UUID
    title: str
    angle: str | None
    central_question_or_thesis: str | None
    learning_outcome: str | None
    source_urls: tuple[str, ...]
    territory_key: str | None
    specificity: SpecificityEvaluation | None
    action: Literal["KEEP", "REJECT", "PRESERVE_CONFLICT", "PRESERVE_NON_GREENLIT"]
    conflict_reason_codes: tuple[str, ...] = ()


class EditorialSpecificityMaintenanceService:
    """Plan and apply retroactive cleanup without deleting durable lineage."""

    _ACTIVE_STAGES = {"GREENLIT", "SELECTED_FOR_SLOT", "IN_PRODUCTION", "FINAL_REVIEW_READY"}
    _ACTIVE_QUALIFICATION_STATES = {
        "RESERVED", "WRITER_DISPATCHED", "SCRIPT_GENERATED", "STRUCTURAL_CHECKED",
        "CLAIM_INVENTORY_CHECKED", "GROUNDING_CHECKED", "VERIFIER_DISPATCHED",
        "EDITORIAL_CHECKED", "MEMORY_CHECKED", "REPAIRABLE_BLOCK", "REPAIR_DISPATCHED",
        "REVERIFYING", "WRITER_IN_PROGRESS", "VERIFIER_IN_PROGRESS",
        "WRITER_SUBMIT_PENDING", "VERIFIER_SUBMIT_PENDING", "RECOVERY_AUTHORIZED",
    }
    _ACTIVE_WORKFLOW_STATES = {
        "PLANNING_PENDING", "PLANNING_RUNNING", "ASSIGNMENT_READY", "RESEARCH_PENDING",
        "RESEARCH_RUNNING", "PACKAGE_PENDING", "PACKAGE_RUNNING", "READY_FOR_PRODUCTION",
        "MEDIA_PENDING", "MEDIA_RUNNING", "RENDER_PENDING", "RENDER_RUNNING", "QC_PENDING",
        "QC_RUNNING", "ARCHIVE_PENDING", "ARCHIVE_RUNNING", "PAUSED_AFTER_NATIVE_RENDER",
    }

    def __init__(self, session: Session) -> None:
        self.session = session
        self.specificity = EditorialSpecificityService(session)

    def plan(
        self,
        *,
        channel_workspace_id: uuid.UUID | None = None,
        policy_snapshot_id: uuid.UUID | None = None,
    ) -> list[SpecificityCleanupAction]:
        query = select(EditorialIdeaCandidate).where(
            EditorialIdeaCandidate.stage.in_(self._ACTIVE_STAGES)
        )
        if channel_workspace_id is not None:
            query = query.where(
                EditorialIdeaCandidate.channel_workspace_id == channel_workspace_id
            )
        if policy_snapshot_id is not None:
            query = query.where(
                EditorialIdeaCandidate.policy_snapshot_id == policy_snapshot_id
            )
        actions: list[SpecificityCleanupAction] = []
        for candidate in self.session.scalars(query.order_by(EditorialIdeaCandidate.created_at, EditorialIdeaCandidate.id)):
            topic = self.session.scalar(
                select(EditorialTopicDefinition)
                .where(EditorialTopicDefinition.editorial_idea_candidate_id == candidate.id)
                .order_by(EditorialTopicDefinition.topic_definition_version.desc())
            )
            evaluation = (
                self.specificity.evaluate(candidate=candidate, topic=topic)
                if topic is not None
                else SpecificityEvaluation(
                    candidate_id=candidate.id,
                    state="BLOCK",
                    reason_codes=("EDITORIAL_TOPIC_DEFINITION_MISSING",),
                    evidence_refs=(),
                    proposal_hash=content_hash(
                        {"candidate_hash": candidate.canonical_hash, "missing_topic": True}
                    ),
                    gate_version=EDITORIAL_SPECIFICITY_GATE_VERSION,
                    evaluation_hash=content_hash(
                        {"candidate_id": str(candidate.id), "missing_topic": True,
                         "gate_version": EDITORIAL_SPECIFICITY_GATE_VERSION}
                    ),
                )
            )
            conflicts = self._active_authority_conflicts(candidate)
            source_urls = tuple(
                str(ref.get("ref"))
                for ref in (candidate.evidence_refs or [])
                if isinstance(ref, dict) and ref.get("ref")
            ) or tuple(
                str(ref.get("ref"))
                for ref in ((topic.subject_evidence_refs if topic else None) or [])
                if isinstance(ref, dict) and ref.get("ref")
            )
            if evaluation is not None and evaluation.state == "PASS":
                action: Literal["KEEP", "REJECT", "PRESERVE_CONFLICT", "PRESERVE_NON_GREENLIT"] = "KEEP"
            elif conflicts:
                action = "PRESERVE_CONFLICT"
            elif candidate.stage == "GREENLIT":
                action = "REJECT"
            else:
                action = "PRESERVE_NON_GREENLIT"
            actions.append(
                SpecificityCleanupAction(
                    candidate_id=candidate.id,
                    title=candidate.proposed_title,
                    angle=candidate.proposed_angle,
                    central_question_or_thesis=(topic.central_question_or_thesis if topic else None),
                    learning_outcome=(topic.learning_outcome if topic else None),
                    source_urls=source_urls,
                    territory_key=candidate.editorial_territory_key,
                    specificity=evaluation,
                    action=action,
                    conflict_reason_codes=tuple(conflicts),
                )
            )
        return actions

    def apply(self, *, actions: list[SpecificityCleanupAction], actor: Any) -> list[SpecificityCleanupAction]:
        """Apply only reviewed GREENLIT→REJECTED legal transitions."""

        from app.contracts.m5 import EditorialIdeaCandidateTransition
        from app.services.editorial_research import EditorialResearchService

        editorial = EditorialResearchService(self.session)
        applied: list[SpecificityCleanupAction] = []
        for action in actions:
            if action.action != "REJECT" or action.specificity is None:
                applied.append(action)
                continue
            candidate = self.session.get(EditorialIdeaCandidate, action.candidate_id)
            if candidate is None or candidate.stage != "GREENLIT":
                applied.append(action)
                continue
            if self._active_authority_conflicts(candidate):
                applied.append(
                    replace(
                        action,
                        action="PRESERVE_CONFLICT",
                        conflict_reason_codes=tuple(self._active_authority_conflicts(candidate)),
                    )
                )
                continue
            self.specificity.persist(candidate=candidate, evaluation=action.specificity)
            editorial.transition_candidate(
                candidate_id=candidate.id,
                data=EditorialIdeaCandidateTransition(
                    target_stage="REJECTED",
                    reason_codes=list(
                        dict.fromkeys(
                            [
                                "EDITORIAL_SPECIFICITY_RETROACTIVE_BLOCK",
                                *action.specificity.reason_codes,
                            ]
                        )
                    ),
                ),
                actor=actor,
            )
            applied.append(action)
        self.session.flush()
        return applied

    @staticmethod
    def report(actions: list[SpecificityCleanupAction]) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": str(action.candidate_id),
                "title": action.title,
                "angle": action.angle,
                "topic_question": action.central_question_or_thesis,
                "learning_outcome": action.learning_outcome,
                "source_urls": list(action.source_urls),
                "novelty_territory_key": action.territory_key,
                "specificity_result": action.specificity.state if action.specificity else "BLOCK",
                "specificity_reason_codes": list(
                    action.specificity.reason_codes if action.specificity else ("EDITORIAL_TOPIC_DEFINITION_MISSING",)
                ),
                "proposed_maintenance_action": action.action,
                "active_authority_conflicts": list(action.conflict_reason_codes),
            }
            for action in actions
        ]

    def _active_authority_conflicts(self, candidate: EditorialIdeaCandidate) -> list[str]:
        from app.db.models.launch_cadence import LongFormPublishSlot
        from app.db.models.m5 import ProjectAdmissionDecision
        from app.db.models.production_workflow import ProductionWorkflowRun
        from app.db.models.script_qualification import ScriptQualificationRun

        reasons: list[str] = []
        if self.session.scalar(
            select(LongFormPublishSlot.id).where(
                LongFormPublishSlot.reserved_candidate_id == candidate.id,
                LongFormPublishSlot.state.in_({"QUALIFICATION_RESERVED", "RESERVED"}),
            )
        ):
            reasons.append("EDITORIAL_SPECIFICITY_ACTIVE_SLOT_CONFLICT")
        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.editorial_idea_candidate_id == candidate.id)
            .order_by(ScriptQualificationRun.created_at.desc())
        )
        if qualification is not None and qualification.state in self._ACTIVE_QUALIFICATION_STATES:
            reasons.append("EDITORIAL_SPECIFICITY_ACTIVE_QUALIFICATION_CONFLICT")
        if self.session.scalar(
            select(ProductionWorkflowRun.id).where(
                ProductionWorkflowRun.project_admission_decision_id.in_(
                    select(ProjectAdmissionDecision.id).where(
                        ProjectAdmissionDecision.editorial_idea_candidate_id == candidate.id,
                        ProjectAdmissionDecision.decision == "ADMIT",
                    )
                ),
                ProductionWorkflowRun.state.in_(self._ACTIVE_WORKFLOW_STATES),
            )
        ):
            reasons.append("EDITORIAL_SPECIFICITY_ACTIVE_WORKFLOW_CONFLICT")
        return reasons
