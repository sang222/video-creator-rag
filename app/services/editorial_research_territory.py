"""Deterministic channel-scoped research territory and source-family authority.

This module chooses *what* evidence should be sought from frozen channel
authority. It does not make an LLM call, invent a video idea, or relax any
editorial gate. A web-search executor may later discover URLs only inside the
returned first-party source-family envelope.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailureError
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.db.models.m5 import EditorialCalendarSlot, EditorialResearchRun
from app.services.config_registry import content_hash


EDITORIAL_RESEARCH_TERRITORY_SCHEMA = "editorial-research-territory.v1"
EDITORIAL_EVIDENCE_DISCOVERY_VERSION = "editorial-evidence-discovery.v2"
SOURCE_FAMILY_REGISTRY_PATH = Path("config/editorial_first_party_source_families.yaml")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _terms(value: Any) -> set[str]:
    terms: set[str] = set()
    values = value if isinstance(value, list) else [value]
    for item in values:
        for term in re.findall(r"[a-z0-9]+", _clean(item).casefold()):
            terms.add(term[:-1] if term.endswith("s") and len(term) > 4 else term)
    return terms


def _bounded_strings(value: Any, *, limit: int = 12) -> tuple[str, ...]:
    values = value if isinstance(value, list) else []
    return tuple(sorted({_clean(item) for item in values if _clean(item)})[:limit])


@dataclass(frozen=True, slots=True)
class SourceFamilyDefinition:
    family_id: str
    organization: str
    approved_domains: tuple[str, ...]
    first_party: bool
    capability_tags: tuple[str, ...]
    source_surface_types: tuple[str, ...]
    status: str
    version: str

    def receipt(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "organization": self.organization,
            "approved_domains": list(self.approved_domains),
            "first_party": self.first_party,
            "capability_tags": list(self.capability_tags),
            "source_surface_types": list(self.source_surface_types),
            "status": self.status,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ResearchTerritoryType:
    territory_type: str
    priority: int
    profile_tags: tuple[str, ...]
    capability_tags: tuple[str, ...]
    desired_editorial_function: str
    allowed_claim_scope: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EditorialResearchTerritory:
    schema_version: str
    channel_workspace_id: uuid.UUID
    policy_snapshot_id: uuid.UUID
    content_mode: str
    content_pillar: str
    audience_problem: str
    production_goal: str
    research_territory_type: str
    research_question: str
    desired_editorial_function: str
    allowed_claim_scope: tuple[str, ...]
    excluded_editorial_territory_keys: tuple[str, ...]
    excluded_questions: tuple[str, ...]
    excluded_source_urls: tuple[str, ...]
    territory_selection_reason_codes: tuple[str, ...]
    allowed_source_families: tuple[SourceFamilyDefinition, ...]
    territory_hash: str
    series_plan_id: str | None = None
    series_run_id: str | None = None
    episode_role: str | None = None
    episode_delta: str | None = None

    def receipt(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "channel_workspace_id": str(self.channel_workspace_id),
            "policy_snapshot_id": str(self.policy_snapshot_id),
            "content_mode": self.content_mode,
            "content_pillar": self.content_pillar,
            "audience_problem": self.audience_problem,
            "production_goal": self.production_goal,
            "research_territory_type": self.research_territory_type,
            "research_question": self.research_question,
            "desired_editorial_function": self.desired_editorial_function,
            "allowed_claim_scope": list(self.allowed_claim_scope),
            "excluded_editorial_territory_keys": list(self.excluded_editorial_territory_keys),
            "excluded_questions": list(self.excluded_questions),
            "excluded_source_urls": list(self.excluded_source_urls),
            "territory_selection_reason_codes": list(self.territory_selection_reason_codes),
            "allowed_source_families": [item.receipt() for item in self.allowed_source_families],
            "territory_hash": self.territory_hash,
            "series_plan_id": self.series_plan_id,
            "series_run_id": self.series_run_id,
            "episode_role": self.episode_role,
            "episode_delta": self.episode_delta,
        }


class FirstPartySourceFamilyRegistry:
    """Load approved first-party source families from versioned configuration."""

    def __init__(self, path: Path = SOURCE_FAMILY_REGISTRY_PATH) -> None:
        self.path = path
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = document.get("items") if isinstance(document, dict) else None
        raw = items[0] if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) else {}
        if raw.get("registry_schema_version") != "editorial-first-party-source-families.v1":
            raise ValidationFailureError("EDITORIAL_SOURCE_FAMILY_REGISTRY_INVALID")
        self.schema_version = str(raw["registry_schema_version"])
        self._families = tuple(self._family(item) for item in raw.get("source_families") or [])
        self._territories = tuple(
            self._territory(item) for item in raw.get("territory_types") or []
        )
        if (
            not self._families
            or not self._territories
            or len({item.family_id for item in self._families}) != len(self._families)
            or len({item.territory_type for item in self._territories})
            != len(self._territories)
        ):
            raise ValidationFailureError("EDITORIAL_SOURCE_FAMILY_REGISTRY_INVALID")

    @staticmethod
    def _family(raw: Any) -> SourceFamilyDefinition:
        if not isinstance(raw, dict):
            raise ValidationFailureError("EDITORIAL_SOURCE_FAMILY_REGISTRY_INVALID")
        family = SourceFamilyDefinition(
            family_id=_clean(raw.get("family_id")),
            organization=_clean(raw.get("organization")),
            approved_domains=_bounded_strings(raw.get("approved_domains")),
            first_party=raw.get("first_party") is True,
            capability_tags=_bounded_strings(raw.get("capability_tags")),
            source_surface_types=_bounded_strings(raw.get("source_surface_types")),
            status=_clean(raw.get("status")),
            version=_clean(raw.get("version")),
        )
        if (
            not family.family_id
            or not family.organization
            or not family.approved_domains
            or not family.first_party
            or not family.capability_tags
            or not family.source_surface_types
            or family.status != "active"
            or not family.version
        ):
            raise ValidationFailureError("EDITORIAL_SOURCE_FAMILY_REGISTRY_INVALID")
        return family

    @staticmethod
    def _territory(raw: Any) -> ResearchTerritoryType:
        if not isinstance(raw, dict):
            raise ValidationFailureError("EDITORIAL_RESEARCH_TERRITORY_REGISTRY_INVALID")
        territory = ResearchTerritoryType(
            territory_type=_clean(raw.get("territory_type")),
            priority=int(raw.get("priority") or 0),
            profile_tags=_bounded_strings(raw.get("profile_tags")),
            capability_tags=_bounded_strings(raw.get("capability_tags")),
            desired_editorial_function=_clean(raw.get("desired_editorial_function")),
            allowed_claim_scope=_bounded_strings(raw.get("allowed_claim_scope")),
        )
        if (
            not territory.territory_type
            or territory.priority <= 0
            or not territory.profile_tags
            or not territory.capability_tags
            or not territory.desired_editorial_function
            or not territory.allowed_claim_scope
        ):
            raise ValidationFailureError("EDITORIAL_RESEARCH_TERRITORY_REGISTRY_INVALID")
        return territory

    def permitted_territories(self, *, channel_terms: set[str]) -> tuple[ResearchTerritoryType, ...]:
        return tuple(
            item
            for item in sorted(self._territories, key=lambda item: (item.priority, item.territory_type))
            if channel_terms.intersection(_terms(item.profile_tags))
        )

    def families_for(self, *, capability_tags: tuple[str, ...]) -> tuple[SourceFamilyDefinition, ...]:
        wanted = _terms(capability_tags)
        return tuple(
            item
            for item in self._families
            if item.status == "active"
            and item.first_party
            and wanted.intersection(_terms(item.capability_tags))
        )


class EditorialResearchTerritoryPlanner:
    """Choose one underrepresented problem-led research territory deterministically."""

    def __init__(self, session: Session, *, registry: FirstPartySourceFamilyRegistry | None = None) -> None:
        self.session = session
        self.registry = registry or FirstPartySourceFamilyRegistry()

    def plan(
        self,
        *,
        channel_workspace_id: uuid.UUID,
        policy_snapshot: CompiledChannelPolicySnapshot,
        research_slot: EditorialCalendarSlot,
        content_mode: str,
        series_binding: dict[str, Any] | None,
        exclusion_authority: dict[str, list[str]],
    ) -> EditorialResearchTerritory:
        payload = policy_snapshot.compiled_payload if isinstance(policy_snapshot.compiled_payload, dict) else {}
        contract = payload.get("channel_contract_json") if isinstance(payload.get("channel_contract_json"), dict) else {}
        editorial = contract.get("editorial_strategy") if isinstance(contract.get("editorial_strategy"), dict) else {}
        audience = contract.get("target_audience") if isinstance(contract.get("target_audience"), dict) else {}
        identity = contract.get("channel_identity") if isinstance(contract.get("channel_identity"), dict) else {}
        channel_terms = _terms(
            [
                *(editorial.get("content_pillars") or []),
                *(editorial.get("allowed_topics") or []),
                *(editorial.get("allowed_angles") or []),
                identity.get("niche"),
                identity.get("brand_promise"),
            ]
        )
        territory_types = self.registry.permitted_territories(channel_terms=channel_terms)
        if not territory_types:
            raise ValidationFailureError("EDITORIAL_RESEARCH_TERRITORY_UNAVAILABLE")
        recent_failed = self._recent_failed_territory_types(
            channel_workspace_id=channel_workspace_id,
            policy_snapshot_id=policy_snapshot.id,
        )
        candidates = [
            item for item in territory_types if item.territory_type not in recent_failed
        ] or list(territory_types)
        selected = candidates[0]
        families = self.registry.families_for(capability_tags=selected.capability_tags)
        if not families:
            raise ValidationFailureError("EDITORIAL_RESEARCH_SOURCE_FAMILY_UNAVAILABLE")
        pain_points = audience.get("pain_points") if isinstance(audience.get("pain_points"), list) else []
        audience_problem = _clean(pain_points[0]) or _clean(audience.get("desired_outcome"))
        if not audience_problem:
            raise ValidationFailureError("EDITORIAL_RESEARCH_AUDIENCE_PROBLEM_MISSING")
        exclusions = {
            "territories": _bounded_strings(exclusion_authority.get("excluded_territory_keys")),
            "questions": _bounded_strings(exclusion_authority.get("excluded_editorial_questions")),
            "urls": _bounded_strings(exclusion_authority.get("excluded_canonical_source_urls")),
        }
        family_names = ", ".join(item.organization for item in families)
        query = (
            "Find current first-party technical evidence for a US-English YouTube long-form "
            f"editorial idea for professional small teams facing '{audience_problem}', within "
            f"the content pillar '{research_slot.content_pillar}'. Research the "
            f"{selected.territory_type} territory: {selected.desired_editorial_function} "
            f"Prioritize a concrete mechanism, workflow, constraint, tradeoff, capability "
            f"boundary, failure mode, or viewer decision. Search only these approved first-party "
            f"source families when relevant: {family_names}. Return source URL, title, organization, "
            "source family, and why each specific technical page supports this territory. Prefer "
            "specific technical pages; do not return generic homepages or documentation indexes. "
            "Do not create the final video idea, performance claim, ROI claim, market claim, or "
            "unsupported product claim."
        )
        if content_mode == "SERIES_EPISODE" and isinstance(series_binding, dict):
            query = (
                f"{query} The bound series episode role is '{_clean(series_binding.get('episode_role'))}' "
                f"and its required delta is '{_clean(series_binding.get('episode_delta'))}'."
            )
        body = {
            "schema_version": EDITORIAL_RESEARCH_TERRITORY_SCHEMA,
            "channel_workspace_id": str(channel_workspace_id),
            "policy_snapshot_id": str(policy_snapshot.id),
            "content_mode": content_mode,
            "content_pillar": research_slot.content_pillar,
            "audience_problem": audience_problem,
            "production_goal": research_slot.production_goal,
            "research_territory_type": selected.territory_type,
            "desired_editorial_function": selected.desired_editorial_function,
            "allowed_claim_scope": list(selected.allowed_claim_scope),
            "exclusions": exclusions,
            "allowed_source_families": [item.receipt() for item in families],
            "series_binding": series_binding if content_mode == "SERIES_EPISODE" else None,
        }
        territory_hash = content_hash(body)
        return EditorialResearchTerritory(
            schema_version=EDITORIAL_RESEARCH_TERRITORY_SCHEMA,
            channel_workspace_id=channel_workspace_id,
            policy_snapshot_id=policy_snapshot.id,
            content_mode=content_mode,
            content_pillar=research_slot.content_pillar,
            audience_problem=audience_problem,
            production_goal=research_slot.production_goal,
            research_territory_type=selected.territory_type,
            research_question=query,
            desired_editorial_function=selected.desired_editorial_function,
            allowed_claim_scope=selected.allowed_claim_scope,
            excluded_editorial_territory_keys=exclusions["territories"],
            excluded_questions=exclusions["questions"],
            excluded_source_urls=exclusions["urls"],
            territory_selection_reason_codes=(
                "EDITORIAL_RESEARCH_TERRITORY_PROFILE_PERMITTED",
                "EDITORIAL_RESEARCH_TERRITORY_RECENT_FAILURE_AVOIDED"
                if recent_failed
                else "EDITORIAL_RESEARCH_TERRITORY_DETERMINISTIC_SELECTION",
            ),
            allowed_source_families=families,
            territory_hash=territory_hash,
            series_plan_id=_clean((series_binding or {}).get("series_plan_id")) or None,
            series_run_id=_clean((series_binding or {}).get("series_run_id")) or None,
            episode_role=_clean((series_binding or {}).get("episode_role")) or None,
            episode_delta=_clean((series_binding or {}).get("episode_delta")) or None,
        )

    def _recent_failed_territory_types(
        self, *, channel_workspace_id: uuid.UUID, policy_snapshot_id: uuid.UUID
    ) -> set[str]:
        rows = self.session.scalars(
            select(EditorialResearchRun)
            .where(EditorialResearchRun.channel_workspace_id == channel_workspace_id)
            .where(EditorialResearchRun.policy_snapshot_id == policy_snapshot_id)
            .where(EditorialResearchRun.trigger_type == "SCHEDULED")
            .where(EditorialResearchRun.status.in_(("COMPLETED", "BLOCKED")))
            .order_by(EditorialResearchRun.created_at.desc())
            .limit(12)
        ).all()
        result: set[str] = set()
        for row in rows:
            if _clean(getattr(row, "status", "")) != "BLOCKED":
                continue
            reason_codes = {
                _clean(code)
                for code in (getattr(row, "reason_codes", None) or [])
            }
            if not reason_codes.intersection(
                {
                    "SOURCE_DISCOVERY_NO_URLS",
                    "SOURCE_FETCH_INSUFFICIENT",
                    "EDITORIAL_SPECIFIC_NOVEL_IDEA_EXHAUSTED",
                    "EDITORIAL_IDEA_SYNTHESIS_NO_USABLE_PROPOSAL",
                    "EDITORIAL_TOPIC_CAPABLE_SOURCE_REQUIRED",
                }
            ):
                continue
            metadata = (row.metadata_ or {}).get("runway_replenishment") or {}
            territory = metadata.get("research_territory") if isinstance(metadata, dict) else {}
            territory_type = _clean(territory.get("research_territory_type")) if isinstance(territory, dict) else ""
            if territory_type:
                result.add(territory_type)
        return result
