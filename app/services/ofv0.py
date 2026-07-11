"""OFV0 deterministic originality and first-channel format validation.

This module is deliberately local/data-only: it never submits a provider job, renders media,
or mutates Channel Contract/Profile/EffectiveContext state.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.contracts.ofv0 import (
    ClaimEvidenceInput,
    FormatIdentityContractDraftRequest,
    FormatIdentityContractRead,
    OriginalityGateRead,
    OriginalityReviewRead,
    SyntheticDisclosureInput,
)
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ClaimEvidenceLedger,
    EpisodeOriginalityManifest,
    FirstScriptedVideoPackage,
    FormatIdentityContract,
    OriginalityGateRun,
    PlatformNativePackagePlan,
    SyntheticMediaDisclosureReceipt,
)
from app.services.r3d3 import stable_hash

PASS = "PASS"
REVIEW = "REVIEW_REQUIRED"
BLOCK = "BLOCK"

FORMAT_REQUIRED_KEYS = (
    "identity_statement",
    "audience_recognition_cues",
    "fixed_elements",
    "must_vary_elements",
    "allowed_hook_families",
    "allowed_narrative_units",
    "preferred_visual_treatments",
    "limited_visual_treatments",
    "forbidden_visual_patterns",
    "narration_style_rules",
    "thumbnail_identity_rules",
    "metadata_identity_rules",
    "intro_outro_policy",
    "claim_policy_summary",
    "synthetic_media_policy_summary",
    "stock_usage_policy_summary",
    "ai_hero_usage_policy_summary",
    "comparison_window_size",
    "originality_risk_thresholds",
)

FIRST_CHANNEL_FORMAT_IDENTITY: dict[str, Any] = {
    "identity_statement": "Professional documentary/explainer about practical small-team operational problems, visualized mechanisms, and evidence-aware practical takeaways without a recurring synthetic human.",
    "audience_recognition_cues": ["operational problem stated quickly", "concrete hook and promise", "mechanism visualized", "practical takeaway", "restrained professional narration"],
    "fixed_elements": ["professional documentary/explainer tone", "native diagram/UI/slide explanatory backbone", "evidence-aware claims", "manual-publish packaging"],
    "must_vary_elements": ["hook family", "primary angle", "section order", "native visual grammar", "thumbnail composition", "title/metadata pattern", "hero concept", "stock sequence"],
    "allowed_hook_families": ["time-cost diagnosis", "workflow bottleneck", "before-after mechanism", "decision tradeoff", "operational misconception"],
    "allowed_narrative_units": ["problem", "mechanism", "constraint", "illustrative scenario", "practical takeaway"],
    "preferred_visual_treatments": ["native diagram", "UI flow", "slide/card", "data mechanism", "screen-style walkthrough"],
    "limited_visual_treatments": ["supporting stock", "limited AI hero/metaphor"],
    "forbidden_visual_patterns": ["generic office stock as backbone", "synthetic human host", "fake product demo", "fake customer result", "AI hero for every sentence"],
    "narration_style_rules": ["restrained", "professional", "no guaranteed outcome", "distinguish scenario from evidence"],
    "thumbnail_identity_rules": ["one operational tension", "specific readable text", "vary composition", "no fake official/partner visual"],
    "metadata_identity_rules": ["plain promise", "claim support cannot be exceeded", "no implied affiliation", "manual-publish review required"],
    "intro_outro_policy": {"intro_reuse_allowed": True, "outro_reuse_allowed": True, "main_body_material_difference_required": True},
    "character_policy_mode": "NO_CHARACTER",
    "claim_policy_summary": "Quantified claims require a ledger entry; scenario/illustrative wording cannot imply a guarantee.",
    "synthetic_media_policy_summary": "No synthetic host or real-person likeness. Final asset disclosure review remains required before manual publish.",
    "stock_usage_policy_summary": "Stock is supporting/fallback only and cannot be the episode backbone or repeated exact sequence.",
    "ai_hero_usage_policy_summary": "AI hero moments are limited, meaningful, and never substitute for explanatory visual substance.",
    "comparison_window_size": 10,
    "originality_risk_thresholds": {"hook_family_review_frequency": 3, "comparison_window_max": 20, "exact_duplicate": "BLOCK"},
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "script", "narration", "title", "summary", "content"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        sentences = _list(value.get("sentences"))
        return " ".join(str(item.get("text", "")).strip() for item in sentences if isinstance(item, dict)).strip()
    return ""


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.lower())).strip()


def _pattern(value: str) -> str:
    return re.sub(r"\b\d+(?:\.\d+)?\b", "#", _normalise(value))


def _gate(
    key: str,
    status: str,
    codes: list[str],
    explanation: str,
    next_action: str,
    *,
    compared: list[dict[str, Any]] | None = None,
    dimensions: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "gate_key": key,
        "status": status,
        "reason_codes": sorted(set(codes)),
        "compared_episode_refs": compared or [],
        "comparison_dimensions": dimensions or [],
        "explanation": explanation,
        "recommended_next_action": next_action,
        "details": details or {},
    }


class FormatIdentityContractService:
    def __init__(self, session: Session):
        self.session = session

    def draft(self, data: FormatIdentityContractDraftRequest) -> FormatIdentityContractRead:
        content = {**FIRST_CHANNEL_FORMAT_IDENTITY, **data.content}
        content["character_policy_mode"] = str(content.get("character_policy_mode") or "NO_CHARACTER")
        current_version = self.session.scalar(
            select(func.max(FormatIdentityContract.contract_version)).where(FormatIdentityContract.channel_id == data.channel_id)
        ) or 0
        contract = FormatIdentityContract(
            channel_id=data.channel_id,
            channel_profile_version_id=data.channel_profile_version_id,
            effective_context_snapshot_id=data.effective_context_snapshot_id,
            contract_version=int(current_version) + 1,
            status="PENDING_HUMAN_APPROVAL",
            character_policy_mode=content["character_policy_mode"],
            content=content,
            content_hash=stable_hash(content),
            created_by=data.created_by,
        )
        self.session.add(contract)
        self.session.flush()
        return FormatIdentityContractRead.model_validate(contract)

    def approve(self, contract_id: uuid.UUID, *, decided_by: str, actor_kind: str = "HUMAN", rationale: str | None = None) -> FormatIdentityContractRead:
        if actor_kind != "HUMAN":
            raise ForbiddenError("FORMAT_IDENTITY_AGENT_SELF_APPROVAL_FORBIDDEN")
        contract = self._require(contract_id)
        if contract.status not in {"DRAFT", "PENDING_HUMAN_APPROVAL"}:
            raise ValidationFailureError("FORMAT_IDENTITY_NOT_APPROVABLE")
        for prior in self.session.scalars(
            select(FormatIdentityContract).where(
                FormatIdentityContract.channel_id == contract.channel_id,
                FormatIdentityContract.status == "APPROVED",
            )
        ).all():
            prior.status = "SUPERSEDED"
        contract.status = "APPROVED"
        contract.approved_by = decided_by
        contract.approved_at = utc_now()
        self.session.flush()
        return FormatIdentityContractRead.model_validate(contract)

    def reject(self, contract_id: uuid.UUID, *, decided_by: str, actor_kind: str = "HUMAN", rationale: str | None = None) -> FormatIdentityContractRead:
        if actor_kind != "HUMAN":
            raise ForbiddenError("FORMAT_IDENTITY_AGENT_SELF_APPROVAL_FORBIDDEN")
        contract = self._require(contract_id)
        if contract.status not in {"DRAFT", "PENDING_HUMAN_APPROVAL"}:
            raise ValidationFailureError("FORMAT_IDENTITY_NOT_REJECTABLE")
        contract.status = "REJECTED"
        contract.approved_by = decided_by
        contract.approved_at = utc_now()
        self.session.flush()
        return FormatIdentityContractRead.model_validate(contract)

    def latest_approved(self, channel_id: uuid.UUID) -> FormatIdentityContract | None:
        return self.session.scalars(
            select(FormatIdentityContract)
            .where(FormatIdentityContract.channel_id == channel_id, FormatIdentityContract.status == "APPROVED")
            .order_by(desc(FormatIdentityContract.contract_version))
            .limit(1)
        ).one_or_none()

    def _require(self, contract_id: uuid.UUID) -> FormatIdentityContract:
        contract = self.session.get(FormatIdentityContract, contract_id)
        if contract is None:
            raise NotFoundError(f"format identity contract not found: {contract_id}")
        return contract


class EpisodeSimilarityComparator:
    DIMENSIONS = ["exact_title", "title_pattern", "hook_digest", "hook_family", "section_order_hash", "visual_treatment_distribution", "stock_asset_sequence", "hero_concepts", "thumbnail_composition", "thumbnail_text_pattern", "intro_outro", "repeated_exact_phrases", "metadata_pattern"]

    def __init__(self, session: Session):
        self.session = session

    def compare(self, manifest: EpisodeOriginalityManifest) -> dict[str, Any]:
        current = manifest.content
        window = int(_dict(current.get("comparison_policy")).get("window_size") or 10)
        previous = self.session.scalars(
            select(EpisodeOriginalityManifest)
            .where(
                EpisodeOriginalityManifest.channel_id == manifest.channel_id,
                EpisodeOriginalityManifest.id != manifest.id,
                EpisodeOriginalityManifest.approval_status.in_(["APPROVED", "PUBLISHED"]),
            )
            .order_by(desc(EpisodeOriginalityManifest.created_at))
            .limit(min(window, 20))
        ).all()
        comparisons: list[dict[str, Any]] = []
        hard_codes: list[str] = []
        review_codes: list[str] = []
        for prior in previous:
            old = prior.content
            dimensions = {
                "exact_title": _normalise(str(current.get("title", ""))) == _normalise(str(old.get("title", ""))),
                "title_pattern": _pattern(str(current.get("title", ""))) == _pattern(str(old.get("title", ""))),
                "hook_digest": current.get("hook_text_digest") == old.get("hook_text_digest"),
                "hook_family": current.get("hook_family") == old.get("hook_family"),
                "section_order_hash": current.get("section_order_hash") == old.get("section_order_hash"),
                "visual_treatment_distribution": current.get("visual_treatment_distribution") == old.get("visual_treatment_distribution"),
                "stock_asset_sequence": current.get("stock_asset_ids") and current.get("stock_asset_ids") == old.get("stock_asset_ids"),
                "hero_concepts": bool(set(_strings(current.get("planned_ai_hero_concepts"))) & set(_strings(old.get("planned_ai_hero_concepts")))),
                "thumbnail_composition": current.get("thumbnail_composition") == old.get("thumbnail_composition"),
                "thumbnail_text_pattern": current.get("thumbnail_text_pattern") == old.get("thumbnail_text_pattern"),
                "intro_outro": current.get("intro_pattern") == old.get("intro_pattern") and current.get("outro_pattern") == old.get("outro_pattern"),
                "repeated_exact_phrases": bool(set(_strings(current.get("repeated_exact_phrases"))) & set(_strings(old.get("repeated_exact_phrases")))),
                "metadata_pattern": current.get("metadata_pattern") == old.get("metadata_pattern"),
            }
            same_script = current.get("narration_digest") and current.get("narration_digest") == old.get("narration_digest")
            if same_script:
                hard_codes.append("EXACT_DUPLICATE_SCRIPT_DIGEST")
            if dimensions["exact_title"] and dimensions["thumbnail_composition"]:
                hard_codes.append("EXACT_TITLE_THUMBNAIL_PAIR")
            if dimensions["section_order_hash"] and dimensions["stock_asset_sequence"]:
                hard_codes.append("REPEATED_ASSET_SEQUENCE_BACKBONE")
            if dimensions["hook_family"]:
                review_codes.append("HOOK_FAMILY_REPEATED")
            if dimensions["section_order_hash"]:
                review_codes.append("NARRATIVE_STRUCTURE_REPEATED")
            if dimensions["thumbnail_composition"]:
                review_codes.append("THUMBNAIL_COMPOSITION_REPEATED")
            comparisons.append({
                "episode_manifest_id": str(prior.id),
                "package_id": str(prior.package_id),
                "manifest_hash": prior.manifest_hash,
                "dimensions": dimensions,
                "same_narration_digest": bool(same_script),
            })
        return {"comparison_window_size": min(window, 20), "compared_episode_refs": comparisons, "hard_codes": sorted(set(hard_codes)), "review_codes": sorted(set(review_codes)), "dimensions": self.DIMENSIONS}


class EpisodeOriginalityManifestBuilder:
    def __init__(self, session: Session):
        self.session = session
        self.comparator = EpisodeSimilarityComparator(session)

    def build(self, package_id: uuid.UUID, *, contract_id: uuid.UUID, episode_topic: str | None = None, primary_angle: str | None = None) -> EpisodeOriginalityManifest:
        package = self._package(package_id)
        contract = self.session.get(FormatIdentityContract, contract_id)
        if contract is None or contract.channel_id != package.channel_id:
            raise ValidationFailureError("FORMAT_IDENTITY_CONTRACT_CHANNEL_MISMATCH")
        existing = self.session.scalar(select(EpisodeOriginalityManifest).where(EpisodeOriginalityManifest.package_id == package.id))
        if existing is not None:
            return existing
        artifacts = _dict(package.artifacts)
        script = _dict(artifacts.get("narration_script"))
        outline = _dict(artifacts.get("script_outline"))
        visual = _dict(artifacts.get("visual_plan"))
        metadata = _dict(artifacts.get("metadata_package"))
        thumbnail = _dict(artifacts.get("thumbnail_brief"))
        narration = _text(script)
        title = str(metadata.get("title") or metadata.get("youtube_title") or episode_topic or "Untitled episode")
        sections = _section_order(outline)
        scenes = [item for item in _list(visual.get("scenes")) if isinstance(item, dict)]
        treatments = Counter(str(item.get("visual_treatment") or item.get("visual_source") or item.get("source_type") or "UNSPECIFIED") for item in scenes)
        stock_ids = [str(item.get("asset_id")) for item in scenes if item.get("asset_id") and str(item.get("visual_source") or item.get("source_type", "")).upper() in {"PEXELS", "STOCK", "ASSET"}]
        hero_concepts = [str(item.get("hero_concept")) for item in scenes if item.get("hero_concept")]
        hook = str(outline.get("hook") or script.get("hook") or _first_sentence(narration))
        hook_family = str(outline.get("hook_family") or "time-cost diagnosis")
        content = {
            "package_id": str(package.id), "video_project_id": str(package.video_project_id) if package.video_project_id else None,
            "channel_id": str(package.channel_id), "format_identity_contract_ref": str(contract.id), "format_identity_contract_hash": contract.content_hash,
            "episode_topic": episode_topic or str(artifacts.get("topic") or title),
            "primary_angle": primary_angle or str(outline.get("primary_angle") or "Operational mechanism before promised outcome"),
            "original_insight_summary": str(outline.get("original_insight_summary") or "Explains the mechanism, constraints, and an evidence-aware scenario rather than only the outcome."),
            "viewer_value_summary": str(outline.get("viewer_value_summary") or "Viewer can identify a repeatable coordination bottleneck and evaluate an automation workflow."),
            "title": title, "hook_family": hook_family, "hook_text_digest": stable_hash(_normalise(hook)),
            "narration_digest": stable_hash(_normalise(narration)), "narrative_structure": str(outline.get("narrative_structure") or "problem-mechanism-constraint-takeaway"),
            "section_order": sections, "section_order_hash": stable_hash(sections),
            "narrative_unit_distribution": _distribution(sections), "visual_treatment_distribution": dict(treatments),
            "native_diagram_or_ui_moments": [item.get("scene_id") or item.get("sentence_id") for item in scenes if str(item.get("visual_source") or item.get("source_type", "")).upper() in {"DIAGRAM", "CARD", "SCREENSHOT", "UI", "SLIDE"}],
            "hero_moments": [item.get("scene_id") or item.get("sentence_id") for item in scenes if item.get("hero_concept")],
            "stock_asset_ids": stock_ids, "planned_ai_hero_concepts": hero_concepts,
            "intro_pattern": str(outline.get("intro_pattern") or "operational-problem-open"), "outro_pattern": str(outline.get("outro_pattern") or "practical-takeaway"),
            "thumbnail_composition": str(thumbnail.get("composition") or metadata.get("thumbnail_composition") or "operational-tension"),
            "thumbnail_text_pattern": str(thumbnail.get("text_pattern") or metadata.get("thumbnail_text") or "specific-operational-outcome"),
            "metadata_pattern": _pattern(str(metadata.get("description") or title)), "repeated_assets": [], "repeated_exact_phrases": _candidate_phrases(narration),
            "originality_strengths": ["distinct mechanism framing", "native explanatory visual priority"], "originality_weaknesses": [],
            "human_review_required": True, "comparison_policy": {"window_size": int(contract.content.get("comparison_window_size") or 10), "no_vector_similarity": True},
        }
        manifest = EpisodeOriginalityManifest(package_id=package.id, video_project_id=package.video_project_id, channel_id=package.channel_id, format_identity_contract_id=contract.id, format_identity_contract_hash=contract.content_hash, content=content, manifest_hash=stable_hash(content))
        self.session.add(manifest)
        self.session.flush()
        comparison = self.comparator.compare(manifest)
        content["recent_episode_comparisons"] = comparison["compared_episode_refs"]
        content["similarity_risk_summary"] = {"hard_codes": comparison["hard_codes"], "review_codes": comparison["review_codes"], "comparison_dimensions": comparison["dimensions"]}
        manifest.content = content
        manifest.manifest_hash = stable_hash(content)
        self.session.flush()
        return manifest

    def _package(self, package_id: uuid.UUID) -> FirstScriptedVideoPackage:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"package not found: {package_id}")
        return package


class ClaimEvidenceLedgerCompiler:
    def __init__(self, session: Session): self.session = session

    def compile(self, package_id: uuid.UUID, claims: list[ClaimEvidenceInput]) -> list[ClaimEvidenceLedger]:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None: raise NotFoundError(f"package not found: {package_id}")
        records = []
        for claim in claims:
            content = claim.model_dump(mode="json")
            record = self.session.scalar(select(ClaimEvidenceLedger).where(ClaimEvidenceLedger.package_id == package.id, ClaimEvidenceLedger.claim_id == claim.claim_id))
            if record is None:
                record = ClaimEvidenceLedger(package_id=package.id, claim_id=claim.claim_id, content=content, content_hash=stable_hash(content))
                self.session.add(record)
            else:
                record.content, record.content_hash = content, stable_hash(content)
            records.append(record)
        self.session.flush()
        return records


class SyntheticMediaDisclosureReceiptBuilder:
    def __init__(self, session: Session): self.session = session

    def build(self, package_id: uuid.UUID, disclosure: SyntheticDisclosureInput) -> SyntheticMediaDisclosureReceipt:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None: raise NotFoundError(f"package not found: {package_id}")
        content = disclosure.model_dump(mode="json")
        content["character_policy_mode"] = "NO_CHARACTER"
        content["provenance_manifest_refs"] = content.get("provenance_manifest_refs", [])
        status = "BLOCKED" if content["real_person_likeness_used"] else "PRE_RENDER_PLANNED"
        record = self.session.scalar(select(SyntheticMediaDisclosureReceipt).where(SyntheticMediaDisclosureReceipt.package_id == package.id))
        if record is None:
            record = SyntheticMediaDisclosureReceipt(package_id=package.id, receipt_status=status, content=content, content_hash=stable_hash(content)); self.session.add(record)
        else:
            record.receipt_status, record.content, record.content_hash = status, content, stable_hash(content)
        self.session.flush(); return record


class PlatformNativePackagePlanService:
    def __init__(self, session: Session): self.session = session

    def ensure_youtube_plans(self, package_id: uuid.UUID, *, include_short: bool = True) -> list[PlatformNativePackagePlan]:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None: raise NotFoundError(f"package not found: {package_id}")
        plans = [("YOUTUBE_LONG", {"hook_adaptation": "canonical long-form hook", "duration_target": "long_form", "aspect_ratio": "16:9", "caption_behavior": "burn-in/local caption plan", "title_metadata_behavior": "manual-publish review", "disclosure_state": "PRE_RENDER_PLANNED", "originality_relationship_to_source": "canonical"})]
        if include_short:
            plans.append(("YOUTUBE_SHORT", {"hook_adaptation": "standalone hook required", "duration_target": "derived_short", "aspect_ratio": "9:16", "caption_behavior": "burn-in/local caption plan", "title_metadata_behavior": "distinct from source", "disclosure_state": "PRE_RENDER_PLANNED", "originality_relationship_to_source": "compressed standalone derivative; not raw crop-only"}))
        records = []
        for surface, content in plans:
            record = self.session.scalar(select(PlatformNativePackagePlan).where(PlatformNativePackagePlan.source_package_id == package.id, PlatformNativePackagePlan.target_surface == surface))
            if record is None:
                record = PlatformNativePackagePlan(source_package_id=package.id, target_surface=surface, content=content, derivative_manifest_ref=f"ofv0://package/{package.id}/{surface}"); self.session.add(record)
            records.append(record)
        self.session.flush(); return records


class OriginalityGateService:
    def __init__(self, session: Session): self.session = session; self.comparator = EpisodeSimilarityComparator(session)

    def evaluate(self, package_id: uuid.UUID, *, final_publish: bool = False) -> list[OriginalityGateRead]:
        package = self._package(package_id)
        manifest = self.session.scalar(select(EpisodeOriginalityManifest).where(EpisodeOriginalityManifest.package_id == package.id))
        if manifest is None: raise ValidationFailureError("EPISODE_ORIGINALITY_MANIFEST_MISSING")
        contract = self.session.get(FormatIdentityContract, manifest.format_identity_contract_id)
        claims = self.session.scalars(select(ClaimEvidenceLedger).where(ClaimEvidenceLedger.package_id == package.id)).all()
        receipt = self.session.scalar(select(SyntheticMediaDisclosureReceipt).where(SyntheticMediaDisclosureReceipt.package_id == package.id))
        comparison = self.comparator.compare(manifest)
        gates = [self._format(contract), self._episode(manifest, comparison), self._variation(manifest, comparison), self._claim(manifest, claims), self._packaging(manifest), self._disclosure(receipt, final_publish=final_publish)]
        status = BLOCK if any(item["status"] == BLOCK for item in gates) else REVIEW if any(item["status"] == REVIEW for item in gates) else PASS
        final = _gate("FinalOriginalityGate", status, [code for item in gates for code in item["reason_codes"]], "Deterministic BLOCK overrides every soft review result.", "Resolve BLOCK items first; then complete required human review." if status != PASS else "Originality safety gates pass.", compared=comparison["compared_episode_refs"], dimensions=EpisodeSimilarityComparator.DIMENSIONS, details={"component_statuses": {item["gate_key"]: item["status"] for item in gates}})
        gates.append(final)
        for item in gates:
            self.session.add(OriginalityGateRun(package_id=package.id, gate_key=item["gate_key"], status=item["status"], result=item))
        self.session.flush()
        return [OriginalityGateRead.model_validate(item) for item in gates]

    def assert_native_render_preflight(self, package_id: uuid.UUID) -> None:
        gates = self.evaluate(package_id, final_publish=False)
        final = next(item for item in gates if item.gate_key == "FinalOriginalityGate")
        if final.status != PASS:
            raise ValidationFailureError(f"NATIVE_RENDER_PLAN_BLOCKED_BY_ORIGINALITY: {final.status}")

    def _format(self, contract: FormatIdentityContract | None) -> dict[str, Any]:
        if contract is None: return _gate("FormatIdentityCompletenessGate", BLOCK, ["FORMAT_IDENTITY_MISSING"], "No format identity is frozen on the episode manifest.", "Create a human-approved FormatIdentityContract.")
        missing = [key for key in FORMAT_REQUIRED_KEYS if contract.content.get(key) in (None, "", [])]
        if contract.character_policy_mode != "NO_CHARACTER" or contract.content.get("character_policy_mode") != "NO_CHARACTER":
            return _gate("FormatIdentityCompletenessGate", BLOCK, ["CHARACTER_POLICY_MODE_CONFLICT"], "First-channel identity conflicts with NO_CHARACTER.", "Use NO_CHARACTER; do not add a recurring synthetic human.")
        if missing: return _gate("FormatIdentityCompletenessGate", BLOCK, ["FORMAT_IDENTITY_INCOMPLETE"], "Required identity sections are missing.", "Complete required format identity sections.", details={"missing": missing})
        if contract.status != "APPROVED": return _gate("FormatIdentityCompletenessGate", BLOCK, ["FORMAT_IDENTITY_NOT_APPROVED"], "A draft identity cannot be used for production readiness.", "Human must approve or reject the format identity.", details={"contract_status": contract.status, "contract_ref": str(contract.id)})
        return _gate("FormatIdentityCompletenessGate", PASS, [], "Approved format identity is complete and NO_CHARACTER-compliant.", "Continue originality review.", details={"contract_ref": str(contract.id), "content_hash": contract.content_hash})

    def _episode(self, manifest: EpisodeOriginalityManifest, comparison: dict[str, Any]) -> dict[str, Any]:
        content = manifest.content
        if comparison["hard_codes"]: return _gate("EpisodeOriginalityGate", BLOCK, comparison["hard_codes"], "Exact duplication/repackaging signal found in recent approved episode manifests.", "Change substance, structure, assets, title, and packaging; do not randomize transitions only.", compared=comparison["compared_episode_refs"], dimensions=comparison["dimensions"])
        if not content.get("primary_angle") or not content.get("original_insight_summary"):
            return _gate("EpisodeOriginalityGate", REVIEW, ["ORIGINAL_INSIGHT_INSUFFICIENT"], "Episode lacks a clear unique angle or original insight summary.", "Add materially new mechanism/constraint insight.", compared=comparison["compared_episode_refs"], dimensions=comparison["dimensions"])
        if comparison["review_codes"]: return _gate("EpisodeOriginalityGate", REVIEW, comparison["review_codes"], "Rolling comparison found repeat-risk signals requiring human review.", "Explain material difference or vary substantive episode elements.", compared=comparison["compared_episode_refs"], dimensions=comparison["dimensions"])
        return _gate("EpisodeOriginalityGate", PASS, [], "Unique angle and substantive explanatory identity are recorded.", "Continue variation and claim checks.", compared=comparison["compared_episode_refs"], dimensions=comparison["dimensions"])

    def _variation(self, manifest: EpisodeOriginalityManifest, comparison: dict[str, Any]) -> dict[str, Any]:
        content = manifest.content
        if not _dict(content.get("visual_treatment_distribution")):
            return _gate("VariationGate", REVIEW, ["VISUAL_TREATMENT_DISTRIBUTION_MISSING"], "Visual variation cannot be evaluated without planned treatment distribution.", "Add native visual treatment plan.")
        if comparison["review_codes"]:
            return _gate("VariationGate", REVIEW, comparison["review_codes"], "Shared identity is allowed, but repeated substantive structure needs review.", "Vary hook, narrative unit order, visual grammar, metadata, or thumbnail composition; transitions alone do not count.", compared=comparison["compared_episode_refs"], dimensions=comparison["dimensions"])
        return _gate("VariationGate", PASS, [], "Must-vary elements are represented in the manifest; renderer preset variation is not used as evidence.", "Continue claim checks.")

    def _claim(self, manifest: EpisodeOriginalityManifest, claims: list[ClaimEvidenceLedger]) -> dict[str, Any]:
        content = manifest.content
        all_text = " ".join(str(content.get(key, "")) for key in ("title", "episode_topic", "primary_angle", "viewer_value_summary"))
        numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", all_text))
        ledger_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", " ".join(str(item.content.get("claim_text", "")) for item in claims)))
        unsupported = [item.content.get("claim_id", item.claim_id) for item in claims if item.content.get("claim_type") == "UNSUPPORTED"]
        unsafe = [item.claim_id for item in claims if item.content.get("claim_type") in {"SCENARIO_BASED", "ESTIMATE", "ILLUSTRATIVE"} and not _strings(item.content.get("allowed_wording"))]
        if numbers - ledger_numbers: return _gate("ClaimEvidenceGate", BLOCK, ["QUANTIFIED_CLAIM_LEDGER_ENTRY_MISSING"], "A quantified package claim has no corresponding ledger entry.", "Add claim ledger evidence/safe wording before packaging.", details={"unledgered_numbers": sorted(numbers - ledger_numbers)})
        if unsupported: return _gate("ClaimEvidenceGate", BLOCK, ["UNSUPPORTED_CLAIM"], "Unsupported claim appears in the ledger/package.", "Remove claim or add verifiable evidence.", details={"claim_ids": unsupported})
        if unsafe: return _gate("ClaimEvidenceGate", REVIEW, ["SCENARIO_WORDING_REVIEW_REQUIRED"], "Scenario/estimate claim lacks explicit allowed wording.", "Add non-guarantee wording and disclaimer guidance.", details={"claim_ids": unsafe})
        return _gate("ClaimEvidenceGate", PASS, [], "Declared claims are ledgered and wording boundaries are present.", "Continue packaging truthfulness check.")

    def _packaging(self, manifest: EpisodeOriginalityManifest) -> dict[str, Any]:
        text = " ".join(str(manifest.content.get(key, "")) for key in ("title", "thumbnail_text_pattern", "metadata_pattern", "viewer_value_summary")).lower()
        text = re.sub(r"\bno guarantee official affiliation or downloadable resource is claimed\b", "", text)
        text = re.sub(r"\b(no|without) (an )?official affiliation\b", "", text)
        text = re.sub(r"\bnot a guaranteed? result\b", "", text)
        text = re.sub(r"\bno downloadable resource is claimed\b", "", text)
        patterns = {"IMPLIED_OFFICIAL_AFFILIATION": r"\bofficial\b|\bpartnered with\b", "FAKE_DEMO_OR_RESULT": r"\bactual customer\b|\btestimonial\b|\bguaranteed\b|\bproven result\b", "FAKE_RESOURCE_CLAIM": r"\bfree download\b|\bchecklist included\b"}
        codes = [code for code, pattern in patterns.items() if re.search(pattern, text)]
        if codes: return _gate("DeceptivePackagingGate", BLOCK, codes, "Packaging contains a deceptive or unsupported presentation pattern.", "Rewrite title/thumbnail/metadata to describe only what the package supports.")
        return _gate("DeceptivePackagingGate", PASS, [], "No deterministic deceptive official/demo/result/resource pattern found.", "Retain human packaging review.")

    def _disclosure(self, receipt: SyntheticMediaDisclosureReceipt | None, *, final_publish: bool) -> dict[str, Any]:
        if receipt is None: return _gate("SyntheticMediaDisclosureGate", BLOCK, ["SYNTHETIC_DISCLOSURE_RECEIPT_MISSING"], "No planned disclosure receipt exists.", "Build disclosure receipt before render/provider planning.")
        content = receipt.content
        if content.get("real_person_likeness_used"): return _gate("SyntheticMediaDisclosureGate", BLOCK, ["REAL_PERSON_LIKENESS_MANUAL_APPROVAL_REQUIRED"], "Real-person likeness is forbidden without explicit manual approval.", "Block asset and obtain separate approved review.")
        if content.get("realistic_ai_person_present") or content.get("fictional_character_used"):
            return _gate("SyntheticMediaDisclosureGate", BLOCK, ["NO_CHARACTER_POLICY_CONFLICT"], "First channel does not permit a realistic synthetic/fictional recurring character.", "Use NO_CHARACTER visual plan.")
        if final_publish and (receipt.receipt_status != "FINAL_CONFIRMED" or content.get("final_asset_confirmation_pending")):
            return _gate("SyntheticMediaDisclosureGate", BLOCK, ["FINAL_DISCLOSURE_REVIEW_INCOMPLETE"], "Final asset disclosure confirmation is incomplete.", "Complete operator disclosure review before manual publish.")
        return _gate("SyntheticMediaDisclosureGate", PASS, ["FINAL_ASSET_REVIEW_PENDING"] if receipt.receipt_status != "FINAL_CONFIRMED" else [], "Pre-render disclosure plan is acceptable; final confirmation remains a manual-publish requirement.", "Confirm final asset disclosure before manual publish.")

    def _package(self, package_id: uuid.UUID) -> FirstScriptedVideoPackage:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None: raise NotFoundError(f"package not found: {package_id}")
        return package


class OriginalityReviewReadModelBuilder:
    def __init__(self, session: Session): self.session = session

    def build(self, package_id: uuid.UUID) -> OriginalityReviewRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None: raise NotFoundError(f"package not found: {package_id}")
        manifest = self.session.scalar(select(EpisodeOriginalityManifest).where(EpisodeOriginalityManifest.package_id == package.id))
        if manifest is None: raise ValidationFailureError("EPISODE_ORIGINALITY_MANIFEST_MISSING")
        contract = self.session.get(FormatIdentityContract, manifest.format_identity_contract_id)
        latest: dict[str, OriginalityGateRun] = {}
        for run in self.session.scalars(select(OriginalityGateRun).where(OriginalityGateRun.package_id == package.id).order_by(desc(OriginalityGateRun.created_at), desc(OriginalityGateRun.id))).all(): latest.setdefault(run.gate_key, run)
        final = latest.get("FinalOriginalityGate")
        final_status = final.status if final else BLOCK
        receipt = self.session.scalar(select(SyntheticMediaDisclosureReceipt).where(SyntheticMediaDisclosureReceipt.package_id == package.id))
        claims = self.session.scalars(select(ClaimEvidenceLedger).where(ClaimEvidenceLedger.package_id == package.id)).all()
        plans = self.session.scalars(select(PlatformNativePackagePlan).where(PlatformNativePackagePlan.source_package_id == package.id)).all()
        comparison = _dict(manifest.content.get("similarity_risk_summary"))
        next_action = "Human approve/reject FormatIdentityContract." if contract and contract.status != "APPROVED" else ("Resolve deterministic originality blockers." if final_status == BLOCK else "Human review originality evidence." if final_status == REVIEW else "Ready for subsequent manual decision gate.")
        return OriginalityReviewRead(
            package_id=package.id,
            format_identity={"status": contract.status if contract else "MISSING", "contract_ref": str(contract.id) if contract else None, "content_hash": contract.content_hash if contract else None, "plain_language": "Format identity requires human approval." if not contract or contract.status != "APPROVED" else "Approved format identity is frozen."},
            episode_originality={"status": latest.get("EpisodeOriginalityGate").status if latest.get("EpisodeOriginalityGate") else "BLOCK", "manifest_ref": str(manifest.id), "manifest_hash": manifest.manifest_hash, "plain_language": "Episode originality is compared against recent approved manifests."},
            claim_evidence={"status": latest.get("ClaimEvidenceGate").status if latest.get("ClaimEvidenceGate") else "BLOCK", "ledger_count": len(claims), "plain_language": "Quantified claims need an explicit ledger."},
            packaging_truthfulness={"status": latest.get("DeceptivePackagingGate").status if latest.get("DeceptivePackagingGate") else "BLOCK", "plain_language": "Packaging cannot imply official affiliation, fake demos, results, or resources."},
            synthetic_disclosure={"status": latest.get("SyntheticMediaDisclosureGate").status if latest.get("SyntheticMediaDisclosureGate") else "BLOCK", "receipt_status": receipt.receipt_status if receipt else "MISSING", "plain_language": "Final disclosure confirmation remains required before manual publish."},
            platform_plans=[{"target_surface": item.target_surface, **item.content, "derivative_manifest_ref": item.derivative_manifest_ref} for item in plans],
            final_originality_verdict=final_status,
            compared_recent_episodes=_list(manifest.content.get("recent_episode_comparisons")),
            exact_next_action=next_action,
            plain_language_summary="Originality safety is blocked until format identity receives human approval." if final_status == BLOCK else "Originality safety requires human review." if final_status == REVIEW else "Originality safety gates pass.",
            technical_details={"gate_results": [run.result for run in latest.values()], "comparison_dimensions": comparison.get("comparison_dimensions", []), "no_raw_previous_scripts_injected": True},
        )


def _section_order(outline: dict[str, Any]) -> list[str]:
    sections = _list(outline.get("sections"))
    values = [str(item.get("name") or item.get("section") or item.get("title") or item.get("id")) for item in sections if isinstance(item, dict)]
    return [item for item in values if item] or ["problem", "mechanism", "constraint", "takeaway"]


def _distribution(values: list[str]) -> dict[str, int]: return dict(Counter(values))
def _first_sentence(value: str) -> str: return re.split(r"(?<=[.!?])\s+", value.strip())[0] if value.strip() else "Operational problem stated quickly"
def _candidate_phrases(value: str) -> list[str]: return [item for item in re.findall(r"\b[a-zA-Z][a-zA-Z ]{12,80}\b", value)[:3] if item.strip()]
