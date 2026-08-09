from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.editorial_fresh_evidence import (
    FreshEvidenceAuthority,
    FreshEvidenceSource,
    _source_validation_reason,
    _tool_discovery_candidates,
    scope_authority_to_research_territory,
)
from app.services.editorial_research_territory import EditorialResearchTerritoryPlanner
from app.services.editorial_specificity import EditorialIdeaSynthesisService
from app.services.script_qualification import classify_source_specificity


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _PlannerSession:
    def __init__(self, rows=()):
        self.rows = tuple(rows)

    def scalars(self, _statement):
        return _Rows(self.rows)


def _snapshot():
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        compiled_payload={
            "channel_contract_json": {
                "channel_identity": {
                    "niche": "AI workflow automation for practical small-team operations",
                    "brand_promise": "Evidence-backed systems without workflow hype.",
                },
                "target_audience": {
                    "pain_points": ["manual repetitive work"],
                },
                "editorial_strategy": {
                    "content_pillars": ["AI workflows", "workflow automation"],
                    "allowed_topics": ["automation systems", "approval workflows"],
                    "allowed_angles": ["step-by-step mechanism", "risk-aware automation advice"],
                },
            }
        },
    )


def _slot():
    return SimpleNamespace(
        content_pillar="AI workflows",
        production_goal="AI workflows, automation systems, and operating dashboards for small teams",
    )


def test_planner_selects_stable_channel_scoped_territory_without_vendor_first_prompt():
    planner = EditorialResearchTerritoryPlanner(_PlannerSession())
    territory = planner.plan(
        channel_workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        policy_snapshot=_snapshot(),
        research_slot=_slot(),
        content_mode="STANDALONE",
        series_binding=None,
        exclusion_authority={},
    )

    assert territory.research_territory_type == "WORKFLOW_MECHANISM"
    assert territory.audience_problem == "manual repetitive work"
    assert "OpenAI documentation" not in territory.research_question
    assert "manual repetitive work" in territory.research_question
    assert "WORKFLOW_MECHANISM" in territory.research_question
    assert len(territory.allowed_source_families) >= 2
    assert territory.territory_hash == planner.plan(
        channel_workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        policy_snapshot=_snapshot(),
        research_slot=_slot(),
        content_mode="STANDALONE",
        series_binding=None,
        exclusion_authority={},
    ).territory_hash


def test_planner_avoids_recent_terminal_territory_when_another_is_permitted():
    prior = SimpleNamespace(
        status="BLOCKED",
        reason_codes=["EDITORIAL_SPECIFIC_NOVEL_IDEA_EXHAUSTED"],
        metadata_={
            "runway_replenishment": {
                "research_territory": {"research_territory_type": "WORKFLOW_MECHANISM"}
            }
        }
    )
    planner = EditorialResearchTerritoryPlanner(_PlannerSession([prior]))
    territory = planner.plan(
        channel_workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        policy_snapshot=_snapshot(),
        research_slot=_slot(),
        content_mode="STANDALONE",
        series_binding=None,
        exclusion_authority={},
    )

    assert territory.research_territory_type == "TOOL_SELECTION"
    assert "EDITORIAL_RESEARCH_TERRITORY_RECENT_FAILURE_AVOIDED" in territory.territory_selection_reason_codes


def test_multi_family_first_party_validation_rejects_unknown_third_party_domain():
    policy = {
        "allowed_source_classes": ["OFFICIAL_DOCUMENT"],
        "allowed_domains": ["developers.openai.com", "docs.anthropic.com", "ai.google.dev"],
        "freshness_days": 30,
        "source_families": [
            {"family_id": "openai", "organization": "OpenAI", "first_party": True, "approved_domains": ["developers.openai.com"]},
            {"family_id": "anthropic", "organization": "Anthropic", "first_party": True, "approved_domains": ["docs.anthropic.com"]},
            {"family_id": "google_ai", "organization": "Google", "first_party": True, "approved_domains": ["ai.google.dev"]},
        ],
    }
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    approved = FreshEvidenceSource(
        source_ref="https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
        title="Tool use",
        publisher="docs.anthropic.com",
        source_class="OFFICIAL_DOCUMENT",
        retrieved_content="A sufficiently long first-party technical source describing tool use constraints.",
        retrieved_at=now,
        query="tool selection",
        source_family="anthropic",
        organization="Anthropic",
    )
    third_party = FreshEvidenceSource(
        source_ref="https://random-blog.example/tool-use",
        title="Tool use blog",
        publisher="random-blog.example",
        source_class="OFFICIAL_DOCUMENT",
        retrieved_content="A sufficiently long third-party article that must not be production evidence.",
        retrieved_at=now,
        query="tool selection",
        source_family="random_blog",
        organization="Random Blog",
    )

    assert _source_validation_reason(source=approved, policy=policy, now=now) is None
    assert _source_validation_reason(source=third_party, policy=policy, now=now) == "SOURCE_QUALITY_INSUFFICIENT"


def test_generic_homepages_are_discovery_only_across_source_families():
    def evidence(url: str, title: str, excerpt: str):
        return SimpleNamespace(
            source_ref=url,
            metadata_={"editorial_fresh_evidence": {"source_snapshot": {"canonical_url": url, "title": title, "content_excerpt": excerpt}}},
        )

    assert classify_source_specificity(
        evidence("https://docs.anthropic.com/", "Anthropic Docs", "Documentation index")
    ) == "DISCOVERY_ONLY"
    assert classify_source_specificity(
        evidence("https://ai.google.dev/", "Google AI for Developers", "Documentation index")
    ) == "DISCOVERY_ONLY"
    assert classify_source_specificity(
        evidence("https://docs.anthropic.com/en/docs/build-with-claude/tool-use", "Tool use", "Detailed technical content " * 20)
    ) == "NARROW_TOPIC_CAPABLE"
    assert classify_source_specificity(
        evidence("https://ai.google.dev/gemini-api/docs/structured-output", "Structured output", "Detailed technical content " * 20)
    ) == "NARROW_TOPIC_CAPABLE"


def test_tool_discovery_retains_multiple_first_party_families_and_rejects_third_party():
    candidates = _tool_discovery_candidates(
        response_payload={
            "output": [
                {
                    "id": "ws-1",
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {"url": "https://developers.openai.com/api/docs/guides/tools", "title": "Tools"},
                            {"url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use", "title": "Tool use"},
                            {"url": "https://ai.google.dev/gemini-api/docs/structured-output", "title": "Structured output"},
                            {"url": "https://random-blog.example/ai-tools", "title": "Random blog"},
                        ]
                    },
                }
            ]
        },
        allowed_domains=["developers.openai.com", "docs.anthropic.com", "ai.google.dev"],
        maximum_results=8,
    )

    assert [item["canonical_url"] for item in candidates] == [
        "https://developers.openai.com/api/docs/guides/tools",
        "https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
        "https://ai.google.dev/gemini-api/docs/structured-output",
    ]


def test_synthesis_prompt_keeps_multi_family_evidence_as_evidence_not_title_authority():
    prompt = EditorialIdeaSynthesisService._prompt(
        content_mode="STANDALONE",
        series_binding=None,
        research_question="Find tool-selection constraints for teams with manual repetitive work.",
        source_pack=[
            {"source_family": "anthropic", "organization": "Anthropic", "title": "Tool use"},
            {"source_family": "google_ai", "organization": "Google", "title": "Structured output"},
        ],
    )

    assert "Anthropic" in prompt and "Google" in prompt
    assert "A source is evidence, never an automatic video title or idea" in prompt


def test_territory_scope_cannot_expand_the_channel_approved_source_families():
    authority = FreshEvidenceAuthority(
        state="EXISTING_SOURCE_PROVIDER_READY",
        provider_key="openai",
        reason_codes=("SOURCE_PROVIDER_AUTHORITY_PASS",),
        policy={
            "source_families": [
                {"family_id": "openai", "organization": "OpenAI", "first_party": True, "approved_domains": ["developers.openai.com"]},
                {"family_id": "anthropic", "organization": "Anthropic", "first_party": True, "approved_domains": ["docs.anthropic.com"]},
            ]
        },
    )
    scoped = scope_authority_to_research_territory(
        authority=authority,
        research_territory={
            "territory_hash": "territory-1",
            "allowed_source_families": [
                {"family_id": "anthropic", "organization": "Anthropic", "first_party": True}
            ],
        },
    )

    assert scoped.policy is not None
    assert scoped.policy["allowed_domains"] == ["docs.anthropic.com"]
    assert [item["family_id"] for item in scoped.policy["source_families"]] == ["anthropic"]
