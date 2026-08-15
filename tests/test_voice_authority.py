from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.core.errors import ValidationFailureError
from app.db.models.channel import ChannelProfileVersion, ChannelWorkspace
from app.db.models.foundation import Company
from app.db.models.voice_authority import (
    ApprovedVoicePool,
    NarrationPerformancePlan,
    NarrationVoiceSnapshot,
    TTSPerformanceProjection,
    VoiceCastingDecision,
    VoiceMarketResearchArtifact,
    VoiceProviderCatalogSnapshot,
)
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.voice_authority import (
    VoiceAuthorityService,
    infer_narration_mode,
    voice_authority_required,
)


def _hash(label: str) -> str:
    return content_hash({"label": label})


def _channel_policy() -> ChannelScopedPolicy:
    return ChannelScopedPolicy.model_validate(
        {
            "market_identity": {
                "primary_market": "US",
                "content_language": "en",
                "locale": "en-US",
                "audience_positioning": "Small US teams adopting practical AI workflows",
            },
            "narration_policy": {
                "provider": "elevenlabs",
                "voice_selection_mode": "CHANNEL_MARKET_RESEARCH",
                "performance_mode": "SEMANTIC_BEAT_PROJECTION",
                "single_primary_narrator": True,
                "series_narrator_stickiness": True,
                "approved_pool_required": True,
                "global_voice_authority_allowed": False,
                "market_research_required": True,
            },
        }
    )


def _company_channel_project(session: Session):
    company = Company(id=uuid.uuid4(), name="VCOS", slug=f"vcos-{uuid.uuid4().hex}")
    channel = ChannelWorkspace(
        id=uuid.uuid4(),
        company_id=company.id,
        key=f"small-team-ai-{uuid.uuid4().hex}",
        name="Small Team AI",
    )
    profile = ChannelProfileVersion(
        id=uuid.uuid4(),
        channel_workspace_id=channel.id,
        version=1,
        profile_input={},
        profile_input_hash=_hash("profile"),
    )
    project = VideoProject(
        id=uuid.uuid4(),
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile.id,
        title="A durable AI workflow",
    )
    session.add_all([company])
    session.flush()
    session.add_all([channel])
    session.flush()
    session.add_all([profile])
    session.flush()
    session.add_all([project])
    session.flush()
    return company, channel, profile, project


def test_voice_authority_builds_deterministic_project_bundle() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        Company.__table__,
        ChannelWorkspace.__table__,
        ChannelProfileVersion.__table__,
        VideoProject.__table__,
        VoiceMarketResearchArtifact.__table__,
        VoiceProviderCatalogSnapshot.__table__,
        ApprovedVoicePool.__table__,
        VoiceCastingDecision.__table__,
        NarrationVoiceSnapshot.__table__,
        NarrationPerformancePlan.__table__,
        TTSPerformanceProjection.__table__,
    ):
        table.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        company, channel, profile, project = _company_channel_project(session)
        service = VoiceAuthorityService(session)
        research = service.create_market_research(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=None,
            policy=_channel_policy(),
            evidence=[
                {
                    "source_ref": "research://us-voice-fit",
                    "claim": "US professional explainers benefit from clear conversational delivery.",
                    "source_hash": _hash("research evidence"),
                }
            ],
            confidence_label="HIGH",
        )
        catalog = service.create_provider_catalog_snapshot(
            company_id=company.id,
            channel_workspace_id=channel.id,
            provider="elevenlabs",
            catalog_version="2026-08-15",
            voices=[
                {
                    "voice_id": "voice-us-1",
                    "model_ids": ["eleven_multilingual_v2"],
                    "market_fit": {
                        "locale": "en-US",
                        "delivery": "clear conversational",
                    },
                    "baseline_voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                        "style": 0.2,
                        "speed": 1.0,
                    },
                    "approved_bounds": {
                        "stability": [0.35, 0.75],
                        "similarity_boost": [0.6, 0.9],
                        "style": [0.0, 0.5],
                        "speed": [0.9, 1.1],
                    },
                }
            ],
            source_refs=["elevenlabs://voices/catalog"],
        )
        pool = service.approve_pool(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=None,
            market_research=research,
            provider_catalog=catalog,
            voices=catalog.voices,
        )
        bundle = service.ensure_project_bundle(
            video_project_id=project.id,
            qualified_script_ref="script://project/1",
            qualified_script_hash=_hash("script"),
            canonical_narration=(
                "This is the hook. The team has a recurring problem. "
                "Here is the process that solves it. The result is predictable."
            ),
            script_sections=[
                {
                    "section_id": "hook",
                    "heading": "Hook",
                    "sentences": [{"text": "This is the hook."}],
                },
                {
                    "section_id": "problem",
                    "heading": "Problem",
                    "sentences": [{"text": "The team has a recurring problem."}],
                },
                {
                    "section_id": "process",
                    "heading": "Process",
                    "sentences": [{"text": "Here is the process that solves it."}],
                },
                {
                    "section_id": "payoff",
                    "heading": "Payoff",
                    "sentences": [{"text": "The result is predictable."}],
                },
            ],
            approved_voice_pool_id=pool.id,
        )
        replay = service.ensure_project_bundle(
            video_project_id=project.id,
            qualified_script_ref="script://project/1",
            qualified_script_hash=_hash("script"),
            canonical_narration=(
                "This is the hook. The team has a recurring problem. "
                "Here is the process that solves it. The result is predictable."
            ),
            script_sections=[
                {
                    "section_id": "hook",
                    "heading": "Hook",
                    "sentences": [{"text": "This is the hook."}],
                },
                {
                    "section_id": "problem",
                    "heading": "Problem",
                    "sentences": [{"text": "The team has a recurring problem."}],
                },
                {
                    "section_id": "process",
                    "heading": "Process",
                    "sentences": [{"text": "Here is the process that solves it."}],
                },
                {
                    "section_id": "payoff",
                    "heading": "Payoff",
                    "sentences": [{"text": "The result is predictable."}],
                },
            ],
            approved_voice_pool_id=pool.id,
        )
        assert bundle.snapshot.voice_id == "voice-us-1"
        assert bundle.snapshot.model_id == "eleven_multilingual_v2"
        assert bundle.plan.coverage_gate_state == "PASS"
        assert bundle.plan.semantic_alignment_gate_state == "PASS"
        assert bundle.plan.continuity_gate_state == "PASS"
        assert bundle.plan.monotony_risk_gate_state == "PASS"
        assert len(bundle.projection.segments) == 4
        assert replay.snapshot.id == bundle.snapshot.id
        assert replay.projection.id == bundle.projection.id


def test_voice_authority_rejects_cross_channel_pool() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        Company.__table__,
        ChannelWorkspace.__table__,
        ChannelProfileVersion.__table__,
        VideoProject.__table__,
        VoiceMarketResearchArtifact.__table__,
        VoiceProviderCatalogSnapshot.__table__,
        ApprovedVoicePool.__table__,
        VoiceCastingDecision.__table__,
        NarrationVoiceSnapshot.__table__,
        NarrationPerformancePlan.__table__,
        TTSPerformanceProjection.__table__,
    ):
        table.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        company, channel, profile, project = _company_channel_project(session)
        other_channel = ChannelWorkspace(
            id=uuid.uuid4(),
            company_id=company.id,
            key=f"other-{uuid.uuid4().hex}",
            name="Other Channel",
        )
        session.add(other_channel)
        session.flush()
        research = VoiceMarketResearchArtifact(
            id=uuid.uuid4(),
            company_id=company.id,
            channel_workspace_id=other_channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=None,
            market_identity={},
            requirements={},
            evidence=[],
            confidence_label="HIGH",
            content_hash=_hash("other research"),
        )
        catalog = VoiceProviderCatalogSnapshot(
            id=uuid.uuid4(),
            company_id=company.id,
            channel_workspace_id=other_channel.id,
            provider="elevenlabs",
            catalog_version="test",
            voices=[],
            source_refs=[],
            content_hash=_hash("other catalog"),
        )
        pool = ApprovedVoicePool(
            id=uuid.uuid4(),
            company_id=company.id,
            channel_workspace_id=other_channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=None,
            voice_market_research_id=research.id,
            provider_catalog_snapshot_id=catalog.id,
            version=1,
            voices=[
                {
                    "voice_id": "wrong-channel",
                    "model_ids": ["eleven_multilingual_v2"],
                }
            ],
            content_hash=_hash("other pool"),
        )
        session.add_all([research, catalog, pool])
        session.flush()
        with pytest.raises(
            ValidationFailureError, match="VOICE_AUTHORITY_APPROVED_POOL_SCOPE_MISMATCH"
        ):
            VoiceAuthorityService(session).ensure_project_bundle(
                video_project_id=project.id,
                qualified_script_ref="script://project/1",
                qualified_script_hash=_hash("script"),
                canonical_narration="One exact sentence.",
                script_sections=[
                    {
                        "section_id": "hook",
                        "heading": "Hook",
                        "sentences": [{"text": "One exact sentence."}],
                    }
                ],
                approved_voice_pool_id=pool.id,
            )


def test_voice_authority_required_reads_compiled_policy() -> None:
    assert voice_authority_required(
        SimpleNamespace(
            compiled_payload={
                "channel_scoped_policy": {
                    "narration_policy": {"approved_pool_required": True}
                }
            }
        )
    )
    assert not voice_authority_required(SimpleNamespace(compiled_payload={}))


def test_narration_mode_is_semantic_and_deterministic() -> None:
    assert (
        infer_narration_mode(
            title="Build a durable workflow",
            canonical_narration="Here is how to build the workflow step by step.",
        )
        == "TACTICAL"
    )
    assert (
        infer_narration_mode(
            title="A critical risk",
            canonical_narration="This warning explains the failure mode.",
        )
        == "CAUTIONARY"
    )


def test_voice_migration_is_single_head() -> None:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["0084_youtube_private_delivery"]
