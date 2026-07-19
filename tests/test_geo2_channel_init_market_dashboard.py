from __future__ import annotations

import uuid
from copy import deepcopy

import pytest

from app.contracts import (
    MinimalMarketChannelInit,
    TargetMarketDraftApproval,
    TargetMarketProfileDraft,
)
from app.core.errors import ValidationFailureError
from app.services import CompanyService, MarketChannelGovernanceService, OfflineMarketResearchRouter


class FakeMarketRouter(OfflineMarketResearchRouter):
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, payload):
        self.calls += 1
        return super().propose(payload)


def _create_market_channel(db_session, slug="geo2-channel"):
    company = CompanyService(db_session).create_company(name="GEO2", slug=slug)
    router = FakeMarketRouter()
    service = MarketChannelGovernanceService(db_session, router=router)
    channel = service.create_minimal_channel(
        MinimalMarketChannelInit(
            company_id=company.id,
            channel_name="Small Team AI",
            channel_key=f"small-team-ai-{slug}",
            channel_purpose="Practical AI workflows for small US teams.",
            primary_market="US",
            primary_language="en",
            primary_locale="en-US",
            target_audience_summary="US small business operators",
            channel_market_type="MARKET_NATIVE",
            known_destination_channel="@SmallTeamAI",
            account_country="VN",
        )
    )
    return company, channel, router, service


def test_minimal_init_research_draft_human_edit_and_exact_approval(db_session) -> None:
    _company, channel, router, service = _create_market_channel(db_session)
    minimal = channel.metadata_["target_market_governance"]["minimal_input"]
    assert set(minimal) >= {
        "channel_name",
        "channel_key",
        "channel_purpose",
        "primary_market",
        "primary_language",
        "primary_locale",
        "target_audience_summary",
        "channel_market_type",
    }
    assert "currency" not in minimal
    assert "units_policy" not in minimal

    draft = service.run_market_research_draft(channel.id)
    assert router.calls == 1
    assert draft.proposal_authority == "AGENT_PROPOSAL_ONLY"
    assert draft.human_confirmation_required is True
    assert draft.status == "NEEDS_HUMAN_REVIEW"
    assert draft.currency == "USD"
    assert draft.primary_timezone == "America/New_York"
    assert draft.suggestions
    assert all(item.evidence_refs and item.human_confirmation_required for item in draft.suggestions)
    assert service.run_market_research_draft(channel.id).draft_id == draft.draft_id
    assert router.calls == 1

    raw = draft.model_dump(mode="python", exclude={"content_hash"})
    raw["acceptable_secondary_geos"] = ["CA", "GB"]
    edited = TargetMarketProfileDraft.model_validate(raw)
    edited = service.update_market_draft(
        channel.id,
        expected_hash=str(draft.content_hash),
        draft=edited,
    )
    assert edited.acceptable_secondary_geos == ["CA", "GB"]
    assert edited.content_hash != draft.content_hash

    with pytest.raises(ValidationFailureError, match="APPROVAL_TARGET_MISMATCH"):
        service.approve_market_draft(
            channel.id,
            TargetMarketDraftApproval(
                expected_draft_id=edited.draft_id,
                expected_draft_version=edited.draft_version,
                expected_draft_hash="0" * 64,
                reviewer="operator",
                approval_ref="operator-approval://geo2/wrong",
            ),
        )

    profile = service.approve_market_draft(
        channel.id,
        TargetMarketDraftApproval(
            expected_draft_id=edited.draft_id,
            expected_draft_version=edited.draft_version,
            expected_draft_hash=str(edited.content_hash),
            reviewer="operator",
            approval_ref="operator-approval://geo2/small-team-ai/us-v1",
        ),
    )
    assert profile is not None
    assert profile.approved_draft_ref.endswith(f"/v{edited.draft_version}")
    preview = service.target_market_preview(channel.id)
    assert preview["state"] == "APPROVED_NOT_ACTIVE"
    assert preview["profile"]["content_hash"] == profile.content_hash
    assert preview["digest"]["profile_hash"] == profile.content_hash
    assert channel.metadata_["target_market_governance"]["active_profile_ref"] is None
    assert channel.active_policy_snapshot_id is None


def test_account_country_is_separate_and_draft_cannot_self_activate(db_session) -> None:
    _company, channel, _router, service = _create_market_channel(
        db_session, slug="geo2-account-country"
    )
    draft = service.run_market_research_draft(channel.id)
    assert draft.account_country == "VN"
    assert draft.target_market == "US"
    raw = deepcopy(channel.metadata_["organic_geo_truth"])
    assert raw == {
        "per_video_target_country_supported": False,
        "guaranteed_country_delivery": False,
        "account_country_is_target_market": False,
        "actual_viewer_geography_state": "UNMEASURED",
    }
    assert not hasattr(service, "activate_market_profile")
