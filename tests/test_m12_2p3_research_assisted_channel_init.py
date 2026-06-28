from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.contracts import ChannelContractReviewRequest, ChannelInitDraftCreate, ReviewFieldDecision
from app.core.errors import ValidationFailureError
from app.db.models import ChannelInitDraft, ChannelProfileVersion, CompiledChannelPolicySnapshot, HumanUploadTask, User, VideoProject
from app.services import ChannelProfileService, CompanyService
from app.services.m12_2p3 import (
    ChannelContractCompiler,
    ChannelContractReviewService,
    ChannelInitDraftService,
    ChannelSetupResearchAgentService,
    leaf_values,
)


def _company(db_session):
    return CompanyService(db_session).create_company(name=f"M12.2P3 {uuid.uuid4().hex[:8]}")


def _minimal_input(company_id, **overrides) -> ChannelInitDraftCreate:
    payload = {
        "company_id": company_id,
        "channel_name": "Small Team AI",
        "public_presence_mode": "EXISTING_PUBLIC_CHANNEL",
        "youtube_url_or_handle": "https://www.youtube.com/@SmallTeamAI",
        "website_url": "https://smallteamai.com/",
        "social_profile_links": [],
        "operator_note_purpose": "Kênh chia sẻ AI workflows thực tế, automation systems, dashboards cho đội ngũ nhỏ.",
        "intended_content_language": None,
        "intended_primary_market": None,
        "owner_operator_language": "vi-VN",
        "initial_topic_pillar_hints": ["AI workflows", "automation systems", "operating dashboards"],
        "source_usage_attestation": True,
    }
    payload.update(overrides)
    return ChannelInitDraftCreate.model_validate(payload)


def _research(db_session):
    company = _company(db_session)
    draft = ChannelInitDraftService(db_session).create(_minimal_input(company.id))
    contract_draft = ChannelSetupResearchAgentService(db_session).run(draft.id)
    return company, draft, contract_draft


def _confirm_required(db_session, draft_id):
    decisions = [
        ReviewFieldDecision(field_path="market_locale.primary_market", action="edit", value="US", note="Primary market confirmed by operator."),
        ReviewFieldDecision(field_path="market_locale.audience_locale", action="confirm"),
        ReviewFieldDecision(field_path="market_locale.content_language", action="confirm"),
        ReviewFieldDecision(field_path="target_audience.primary_persona", action="confirm"),
        ReviewFieldDecision(field_path="channel_identity.niche", action="confirm"),
        ReviewFieldDecision(field_path="channel_identity.positioning", action="confirm"),
        ReviewFieldDecision(field_path="editorial_strategy.content_pillars", action="confirm"),
        ReviewFieldDecision(field_path="editorial_strategy.claim_style", action="confirm"),
        ReviewFieldDecision(field_path="format_policy.long_form.enabled", action="confirm"),
        ReviewFieldDecision(field_path="format_policy.shorts.enabled", action="confirm"),
        ReviewFieldDecision(field_path="learning_policy.min_evidence_required", action="confirm"),
    ]
    return ChannelContractReviewService(db_session).apply_review(
        draft_id,
        ChannelContractReviewRequest(decisions=decisions, human_notes="Confirmed strategic fields."),
    )


def test_m12_2p3_minimal_input_creates_init_draft_with_research_pending(db_session) -> None:
    company = _company(db_session)

    draft = ChannelInitDraftService(db_session).create(_minimal_input(company.id))

    assert draft.company_id == company.id
    assert draft.workflow_status == "RESEARCH_PENDING"
    assert draft.channel_id is None
    assert draft.channel_profile_version_id is None
    assert draft.compiled_policy_snapshot_id is None


def test_m12_2p3_existing_public_channel_requires_anchor_and_attestation(db_session) -> None:
    company = _company(db_session)
    with pytest.raises(ValidationError):
        _minimal_input(company.id, youtube_url_or_handle=None, website_url=None, social_profile_links=[])

    draft = ChannelInitDraftService(db_session).create(_minimal_input(company.id, source_usage_attestation=False))
    assert draft.workflow_status == "BLOCKED"
    with pytest.raises(ValidationFailureError):
        ChannelSetupResearchAgentService(db_session).run(draft.id)


def test_m12_2p3_research_draft_has_evidence_and_field_source_coverage(db_session) -> None:
    _, draft, contract_draft = _research(db_session)

    contract = contract_draft.suggested_channel_contract
    field_map = contract_draft.field_source_map_json

    assert draft.channel_id is None
    assert contract_draft.evidence_refs
    assert "ev_admin_note_purpose" in {item["ref_id"] for item in contract_draft.evidence_refs}
    assert set(leaf_values(contract)) <= set(field_map)
    assert contract["channel_identity"]["niche"].lower().startswith("practical ai workflows")
    assert contract["market_locale"]["content_language"] == "en"
    assert contract["market_locale"]["primary_market"] == "UNKNOWN"
    assert field_map["market_locale.primary_market"]["source_type"] == "UNKNOWN"
    assert field_map["market_locale.content_language"]["review_required"] is True


def test_m12_2p3_no_strong_evidence_keeps_audience_unknown(db_session) -> None:
    company = _company(db_session)
    draft = ChannelInitDraftService(db_session).create(
            _minimal_input(
                company.id,
                channel_name="Untitled Channel",
                public_presence_mode="NEW_CHANNEL_NO_PUBLIC_FOOTPRINT",
                youtube_url_or_handle=None,
            website_url=None,
            operator_note_purpose="TBD",
            initial_topic_pillar_hints=[],
        )
    )
    contract_draft = ChannelSetupResearchAgentService(db_session).run(draft.id)

    assert contract_draft.suggested_channel_contract["target_audience"]["primary_persona"] == "UNKNOWN"
    assert "target_audience.primary_persona" in " ".join(contract_draft.missing_fields)


def test_m12_2p3_human_confirm_and_edit_update_source_and_decision_log(db_session) -> None:
    _, draft, _ = _research(db_session)

    reviewed = ChannelContractReviewService(db_session).apply_review(
        draft.id,
        ChannelContractReviewRequest(
            decisions=[
                ReviewFieldDecision(field_path="target_audience.primary_persona", action="confirm", note="Persona accepted."),
                ReviewFieldDecision(field_path="market_locale.primary_market", action="edit", value="US", note="Operator selected market."),
            ]
        ),
    )

    assert reviewed.field_source_map_json["target_audience.primary_persona"]["source_type"] == "HUMAN_CONFIRMED"
    assert reviewed.field_source_map_json["market_locale.primary_market"]["source_type"] == "HUMAN_CONFIRMED"
    assert any(item["action"] == "edit" and item["field_path"] == "market_locale.primary_market" for item in reviewed.review_decision_log_json)


def test_m12_2p3_complete_blocked_until_strategic_fields_confirmed_and_partial_cannot_activate(db_session) -> None:
    _, draft, _ = _research(db_session)

    partial = ChannelContractCompiler(db_session).compile(draft.id)
    snapshot = db_session.get(CompiledChannelPolicySnapshot, partial["compiled_policy_snapshot_id"])

    assert partial["contract_status"] == "PARTIAL"
    assert partial["activation_eligibility"] is False
    with pytest.raises(ValidationFailureError):
        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)


def test_m12_2p3_locked_safety_and_provider_budget_are_not_channel_inputs(db_session) -> None:
    _, draft, _ = _research(db_session)

    with pytest.raises(ValidationFailureError):
        ChannelContractReviewService(db_session).apply_review(
            draft.id,
            ChannelContractReviewRequest(
                decisions=[
                    ReviewFieldDecision(field_path="platform_strategy.auto_publish_allowed", action="edit", value=True)
                ]
            ),
        )
    with pytest.raises(ValidationError):
        ChannelInitDraftCreate.model_validate({**_minimal_input(_company(db_session).id).model_dump(mode="json"), "ai_hero_budget_usd": 25})


def test_m12_2p3_complete_compile_creates_profile_snapshot_and_can_activate_explicitly(db_session) -> None:
    _, draft, _ = _research(db_session)
    reviewed = _confirm_required(db_session, draft.id)

    compiled = ChannelContractCompiler(db_session).compile(draft.id)
    profile = db_session.get(ChannelProfileVersion, compiled["channel_profile_version_id"])
    snapshot = db_session.get(CompiledChannelPolicySnapshot, compiled["compiled_policy_snapshot_id"])

    assert reviewed.contract_status == "COMPLETE"
    assert compiled["contract_status"] == "COMPLETE"
    assert profile is not None
    assert profile.source_template_key is None
    assert profile.profile_input["policies"]["m12_2p3_no_catalog_template_used"] is True
    assert snapshot.compiled_payload["channel_contract_json"]["contract_status"] == "COMPLETE"
    assert snapshot.compiled_payload["field_source_map_json"]
    assert set(leaf_values(snapshot.compiled_payload["channel_contract_json"])) <= set(snapshot.compiled_payload["field_source_map_json"])

    activated = ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
    db_session.refresh(draft)
    assert activated.status == "active"


def test_m12_2p3_existing_video_project_snapshot_binding_remains_intact(db_session) -> None:
    company, draft, _ = _research(db_session)
    _confirm_required(db_session, draft.id)
    first = ChannelContractCompiler(db_session).compile(draft.id)
    first_snapshot = db_session.get(CompiledChannelPolicySnapshot, first["compiled_policy_snapshot_id"])
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=first_snapshot.id)
    user = User(email=f"m12-2p3-{uuid.uuid4().hex[:8]}@example.com", display_name="M12.2P3", status="active")
    db_session.add(user)
    db_session.flush()
    project = VideoProject(
        company_id=company.id,
        channel_workspace_id=first["channel_id"],
        policy_snapshot_id=first_snapshot.id,
        title="Frozen project",
        description="Project keeps original snapshot.",
        created_by_user_id=user.id,
    )
    db_session.add(project)
    db_session.flush()

    second_draft = ChannelInitDraftService(db_session).create(
        _minimal_input(company.id, channel_name="Small Team AI 2", intended_primary_market="VN")
    )
    ChannelSetupResearchAgentService(db_session).run(second_draft.id)
    _confirm_required(db_session, second_draft.id)
    second = ChannelContractCompiler(db_session).compile(second_draft.id)
    second_snapshot = db_session.get(CompiledChannelPolicySnapshot, second["compiled_policy_snapshot_id"])
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=second_snapshot.id)

    db_session.refresh(project)
    assert project.policy_snapshot_id == first_snapshot.id


def test_m12_2p3_research_has_no_provider_upload_or_studio_scraping_side_effects(db_session) -> None:
    _, draft, contract_draft = _research(db_session)
    _confirm_required(db_session, draft.id)
    compiled = ChannelContractCompiler(db_session).compile(draft.id)

    assert db_session.query(HumanUploadTask).count() == 0
    assert all("studio" not in str(item).lower() or "no_youtube_studio_scraping" in str(item).lower() for item in contract_draft.risks)
    assert compiled["channel_contract_json"]["platform_strategy"]["studio_scraping_allowed"] is False
    assert compiled["channel_contract_json"]["platform_strategy"]["auto_publish_allowed"] is False
