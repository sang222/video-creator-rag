"""Tests for M12.2P-R Channel Activation CTA Repair.

Backend:
- activation succeeds when contract COMPLETE
- activation blocked when contract PARTIAL
- activation blocked when no snapshot
- activation does not mutate existing VideoProject snapshots
- activation records audit/event (CHANNEL_ACTIVATED / CHANNEL_ACTIVATION_BLOCKED)

Frontend:
- button visible when eligible
- button hidden/disabled when not eligible
- click calls activation endpoint
- success refreshes status to active
- error displays Vietnamese message
- no publish/upload button appears
"""

import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ChannelProfileVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    AuditEvent,
    ChannelProfileVersion,
    ChannelWorkspace,
    Company,
    CompiledChannelPolicySnapshot,
    DomainEvent,
    User,
    VideoProject,
)
from app.main import create_app
from app.services.channel_profile import ChannelProfileService
from app.services.company import CompanyService
from app.services.profile_compiler import ChannelProfileCompiler


def _make_company(session: Session) -> Company:
    svc = CompanyService(session)
    company = svc.create_company(name=f"Activation Test Co {uuid.uuid4().hex[:6]}", slug=f"activation-test-{uuid.uuid4().hex[:6]}")
    session.flush()
    return company


def _make_channel(session: Session, company_id: uuid.UUID, *, status: str = "draft") -> ChannelWorkspace:
    channel = ChannelWorkspace(
        company_id=company_id,
        key=f"test-channel-{uuid.uuid4().hex[:8]}",
        name=f"Test Channel {uuid.uuid4().hex[:6]}",
        status=status,
        primary_language="en",
        primary_timezone="UTC",
        default_timezone="UTC",
    )
    session.add(channel)
    session.flush()
    return channel


def _make_user(session: Session) -> User:
    user = User(
        email=f"activation-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Activation Test",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _profile_input(session: Session, contract: dict):
    compiler = ChannelProfileCompiler(session)
    profile_input, _ = compiler.profile_input_from_template("saas_digital_leverage")
    return profile_input.model_copy(
        update={
            "display_name": contract["channel_identity"].get("channel_name") or "Test Channel",
            "target_market": contract.get("market_locale", {}).get("primary_market") or "",
            "format_strategy": contract["format_policy"],
            "voice_style": contract["voice_style"],
            "platform_strategy": contract["platform_strategy"],
            "content_pillars": contract["editorial_strategy"]["content_pillars"],
            "policies": {
                "review": "human_review",
                "safety": "avoid unsupported claims",
                "channel_contract": contract,
            },
        }
    )


def _compile_snapshot(
    session: Session,
    channel: ChannelWorkspace,
    *,
    contract: dict | None = None,
    correlation_id: str = "m12-2p-r-activation-test",
) -> tuple[ChannelProfileVersion, CompiledChannelPolicySnapshot]:
    compiler = ChannelProfileCompiler(session)
    profile = ChannelProfileService(session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(profile_input=_profile_input(session, contract or _complete_contract_payload())),
    )
    compiled = compiler.compile(profile_version_id=profile.id, correlation_id=correlation_id)
    snapshot = session.get(CompiledChannelPolicySnapshot, compiled.snapshot_id)
    assert snapshot is not None
    return profile, snapshot


def _complete_contract_payload() -> dict:
    return {
        "channel_identity": {
            "channel_name": "Test Channel",
            "channel_key": "test-channel",
            "template_key": "saas_digital_leverage",
            "channel_type": "YOUTUBE_CHANNEL",
            "niche": "SaaS",
            "positioning": "Expert positioning",
            "brand_promise": "Deliver value",
            "primary_platform": "YouTube",
            "secondary_platforms": ["Shorts"],
        },
        "target_audience": {
            "primary_persona": "SaaS founders",
            "audience_level": "semi_technical",
            "pain_points": ["No growth"],
            "desired_outcome": "10x growth",
        },
        "market_locale": {
            "primary_market": "US",
            "secondary_markets": ["UK"],
            "audience_locale": "en-US",
            "content_language": "en",
            "operator_language": "vi",
            "timezone": "America/New_York",
            "currency": "USD",
            "measurement_units": "metric",
            "date_format": "MM/DD/YYYY",
        },
        "editorial_strategy": {
            "content_pillars": ["growth", "product"],
            "allowed_angles": ["case study"],
            "forbidden_angles": ["hype"],
            "claim_style": ["measured", "evidence_backed"],
            "allowed_topics": ["growth"],
            "forbidden_topics": ["spam"],
        },
        "format_policy": {
            "long_form": {
                "enabled": True,
                "target_duration_minutes": {"min": 8, "max": 14},
                "structure": ["hook", "problem", "mechanism", "result", "takeaway"],
                "chapters_required": True,
            },
            "shorts": {
                "enabled": True,
                "target_duration_seconds": {"min": 30, "max": 45},
                "hard_max_seconds": 59,
                "captions_required": True,
                "shorts_per_long_form": 2,
            },
        },
        "voice_style": {
            "narration_tone": "practical_explainer",
            "pacing": "clear_short_sentences",
            "allowed_style": ["direct"],
            "forbidden_style": ["hype", "fearmongering"],
        },
        "platform_strategy": {
            "primary_platform": "YouTube",
            "youtube_is_learning_authority": True,
            "secondary_platforms": ["Shorts"],
            "disabled_authorities": ["tiktok_analytics_learning"],
            "publish_mode": "human_handoff_only",
            "auto_publish_allowed": False,
            "studio_scraping_allowed": False,
        },
        "media_policy": {
            "voice_provider": "ElevenLabs",
            "ai_hero_provider": "Google Vertex Veo",
            "ai_hero_model_id": "veo-3.1-fast-generate-001",
            "ai_hero_allowed_durations_seconds": [4, 6, 8],
            "ai_hero_default_duration_seconds": 8,
            "ai_hero_audio": False,
            "ai_hero_allowed_use": ["hero_shot"],
            "ai_hero_forbidden_use": ["data_diagram"],
            "renderer": "Creatomate Growth 10K",
            "storage_archive": "Google Drive",
            "drive_offload_enabled": True,
        },
        "rights_policy": {
            "source_manifest_required": True,
            "rights_evidence_required": True,
            "ai_disclosure_required_when_ai_media_used": True,
            "synthetic_media_warning_when_applicable": True,
            "music_policy": "approved_licensed_audio_library_safe_only",
            "reused_content_sensitivity": "medium",
        },
        "budget_policy": {
            "cost_sensitivity": "medium",
            "avoid_unnecessary_ai_hero": True,
            "prefer_reuse_safe_assets": True,
            "exact_cost_claim_requires_provider_snapshot": True,
        },
        "learning_policy": {
            "authority": "youtube_analytics_only",
            "min_evidence_required": "2 independent sources",
            "auto_promote_learning": False,
            "config_mutation_by_agent_allowed": False,
            "weak_evidence_action": "summarize_limitations_only",
        },
        "forbidden_behavior": [
            "fake_traffic", "bot_engagement", "spam_reupload", "algorithm_manipulation",
            "platform_evasion", "ip_vps_tricks", "youtube_studio_scraping",
            "dashboard_scraping", "invented_metrics", "invented_sources",
            "invented_rights", "unsupported_local_claims",
        ],
    }


def _partial_contract_payload() -> dict:
    """Remove required fields to make contract PARTIAL."""
    payload = _complete_contract_payload()
    del payload["channel_identity"]["channel_name"]
    del payload["target_audience"]["primary_persona"]
    del payload["market_locale"]["primary_market"]
    return payload


def _second_complete_contract_payload() -> dict:
    payload = _complete_contract_payload()
    payload["channel_identity"]["channel_name"] = "Test Channel UK"
    payload["market_locale"]["primary_market"] = "UK"
    payload["market_locale"]["audience_locale"] = "en-GB"
    payload["market_locale"]["timezone"] = "Europe/London"
    payload["market_locale"]["currency"] = "GBP"
    return payload


class TestActivationSucceedsWhenContractComplete:
    """Activation succeeds when contract is COMPLETE and snapshot exists."""

    def test_activation_sets_channel_active(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        assert channel.status == "draft"
        profile, snapshot = _compile_snapshot(db_session, channel)
        before_profile_input = copy.deepcopy(profile.profile_input)
        before_profile_hash = profile.profile_input_hash

        result = ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
        db_session.commit()

        db_session.refresh(profile)
        db_session.refresh(channel)
        assert channel.status == "active"
        assert channel.active_policy_snapshot_id == snapshot.id
        assert result.status == "active"
        assert profile.profile_input == before_profile_input
        assert profile.profile_input_hash == before_profile_hash

    def test_activation_sets_metadata_active(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        _, snapshot = _compile_snapshot(db_session, channel)

        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
        db_session.commit()

        db_session.refresh(channel)
        assert channel.metadata_["m11_lifecycle_state"] == "ACTIVE"

    def test_activation_records_audit(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        _, snapshot = _compile_snapshot(db_session, channel)

        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
        db_session.commit()

        audits = db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.target_id == channel.id,
                AuditEvent.reason_code == "CHANNEL_ACTIVATED",
            )
        ).all()
        assert [audit.event_type for audit in audits] == ["channel.activated"]

    def test_activation_records_domain_event(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        _, snapshot = _compile_snapshot(db_session, channel)

        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
        db_session.commit()

        events = db_session.scalars(
            select(DomainEvent).where(
                DomainEvent.aggregate_id == channel.id,
                DomainEvent.event_type == "channel.activated",
            )
        ).all()
        assert events
        assert events[-1].payload["reason_code"] == "CHANNEL_ACTIVATED"


class TestActivationBlockedWhenContractPartial:
    """Activation blocked when contract is PARTIAL."""

    def test_activation_raises_on_partial_contract(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        _, snapshot = _compile_snapshot(db_session, channel, contract=_partial_contract_payload())

        with pytest.raises(ValidationFailureError, match=r"not COMPLETE"):
            ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)

        db_session.refresh(channel)
        assert channel.status == "draft"


class TestActivationBlockedWhenNoSnapshot:
    """Activation blocked when no snapshot exists."""

    def test_activation_raises_on_missing_snapshot(self, db_session: Session) -> None:
        fake_id = uuid.uuid4()
        svc = ChannelProfileService(db_session)
        with pytest.raises(Exception, match=r"snapshot not found"):
            svc.activate_snapshot(snapshot_id=fake_id)

    def test_activation_endpoint_blocks_channel_without_snapshot(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        db_session.commit()

        response = TestClient(create_app()).post(f"/channels/{channel.id}/activate", json={})

        assert response.status_code == 404
        assert "policy snapshot not found" in response.text


class TestActivationDoesNotMutateVideoProjectSnapshots:
    """Activation does not mutate existing VideoProject snapshots."""

    def test_activation_preserves_project_snapshots(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        user = _make_user(db_session)
        _, first_snapshot = _compile_snapshot(
            db_session,
            channel,
            correlation_id="m12-2p-r-first-snapshot",
        )
        project = VideoProject(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=first_snapshot.id,
            title="Frozen project",
            description="Project keeps its original policy snapshot.",
            created_by_user_id=user.id,
        )
        db_session.add(project)
        db_session.flush()
        _, second_snapshot = _compile_snapshot(
            db_session,
            channel,
            contract=_second_complete_contract_payload(),
            correlation_id="m12-2p-r-second-snapshot",
        )

        ChannelProfileService(db_session).activate_snapshot(snapshot_id=second_snapshot.id)
        db_session.commit()

        db_session.refresh(channel)
        db_session.refresh(project)
        assert channel.status == "active"
        assert channel.active_policy_snapshot_id == second_snapshot.id
        assert project.policy_snapshot_id == first_snapshot.id


class TestActivationBlockedRecordsEvent:
    """Blocked activation records activation_blocked event."""

    def test_blocked_records_event_and_audit(self, db_session: Session) -> None:
        company = _make_company(db_session)
        channel = _make_channel(db_session, company.id, status="draft")
        _, snapshot = _compile_snapshot(db_session, channel, contract=_partial_contract_payload())

        with pytest.raises(ValidationFailureError):
            ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)

        db_session.commit()

        events = db_session.scalars(
            select(DomainEvent).where(
                DomainEvent.aggregate_id == channel.id,
                DomainEvent.event_type == "channel.activation_blocked",
            )
        ).all()
        audits = db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.target_id == channel.id,
                AuditEvent.reason_code == "CHANNEL_ACTIVATION_BLOCKED",
            )
        ).all()
        assert events
        assert events[-1].payload["reason_code"] == "CHANNEL_ACTIVATION_BLOCKED"
        assert audits
        assert audits[-1].event_type == "channel.activation_blocked"
