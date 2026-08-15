"""Market-aware voice casting and semantic narration-performance authority."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.voice_authority import (
    ApprovedVoicePoolCreate,
    NarrationPerformanceBeat,
    NarrationPerformancePlanCreate,
    ProviderVoiceCandidate,
    TTSPerformanceSegment,
    VoiceCastingRequest,
    VoiceMarketResearchCreate,
    VoiceProviderCatalogCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.voice_authority import (
    ApprovedVoicePool,
    NarrationPerformancePlan,
    NarrationVoiceSnapshot,
    SeriesNarratorBinding,
    TTSPerformanceProjection,
    VoiceCastingDecision,
    VoiceMarketResearchArtifact,
    VoiceProviderCatalogSnapshot,
)
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.voice_execution import (
    ELEVENLABS_CAPABILITY_PROFILE_VERSION,
    elevenlabs_capability,
    select_execution_strategy,
)

VOICE_CASTING_POLICY_VERSION = "vcos.voice-casting-policy.v1"
VOICE_PERFORMANCE_POLICY_VERSION = "vcos.narration-performance-policy.v1"
VOICE_CAPABILITY_PROFILE_VERSION = ELEVENLABS_CAPABILITY_PROFILE_VERSION

_NARRATION_FUNCTIONS = {
    "HOOK",
    "SETUP",
    "PROBLEM",
    "EXPLANATION",
    "PROCESS",
    "CONTRAST",
    "KEY_INSIGHT",
    "EXAMPLE",
    "WARNING",
    "LIMITATION",
    "PAYOFF",
    "CONCLUSION",
}
_DELIVERY_INTENTS = {
    "CURIOUS_ENGAGED",
    "CLEAR_PRECISE",
    "CONVERSATIONAL",
    "SERIOUS_MEASURED",
    "CAUTIONARY",
    "EMPHATIC",
    "CONFIDENT",
    "WARM",
    "DECISIVE",
}
_FUNCTION_INTENT_COMPATIBILITY = {
    "HOOK": {"CURIOUS_ENGAGED", "CONVERSATIONAL", "EMPHATIC"},
    "SETUP": {"CURIOUS_ENGAGED", "CLEAR_PRECISE", "CONVERSATIONAL"},
    "PROBLEM": {"SERIOUS_MEASURED", "CAUTIONARY", "EMPHATIC"},
    "EXPLANATION": {"CLEAR_PRECISE", "CONVERSATIONAL"},
    "PROCESS": {"CLEAR_PRECISE", "CONVERSATIONAL"},
    "CONTRAST": {"CLEAR_PRECISE", "EMPHATIC", "SERIOUS_MEASURED"},
    "KEY_INSIGHT": {"EMPHATIC", "CONFIDENT", "CLEAR_PRECISE"},
    "EXAMPLE": {"CONVERSATIONAL", "CURIOUS_ENGAGED", "CLEAR_PRECISE"},
    "WARNING": {"CAUTIONARY", "SERIOUS_MEASURED"},
    "LIMITATION": {"CAUTIONARY", "SERIOUS_MEASURED", "CLEAR_PRECISE"},
    "PAYOFF": {"CONFIDENT", "WARM", "EMPHATIC"},
    "CONCLUSION": {"CONFIDENT", "WARM", "DECISIVE"},
}
_DELIVERY_PROFILE = {
    "CURIOUS_ENGAGED": {
        "energy": "MEDIUM_HIGH",
        "pace": "MEDIUM_FAST",
        "emphasis": "MEDIUM",
    },
    "CLEAR_PRECISE": {"energy": "CONTROLLED", "pace": "MEDIUM", "emphasis": "MEDIUM"},
    "CONVERSATIONAL": {"energy": "MEDIUM", "pace": "MEDIUM", "emphasis": "LOW"},
    "SERIOUS_MEASURED": {
        "energy": "CONTROLLED",
        "pace": "MEASURED",
        "emphasis": "MEDIUM",
    },
    "CAUTIONARY": {"energy": "CONTROLLED", "pace": "MEASURED", "emphasis": "HIGH"},
    "EMPHATIC": {"energy": "MEDIUM_HIGH", "pace": "MEDIUM", "emphasis": "HIGH"},
    "CONFIDENT": {"energy": "MEDIUM", "pace": "MEDIUM", "emphasis": "MEDIUM"},
    "WARM": {"energy": "MEDIUM", "pace": "MEASURED", "emphasis": "LOW"},
    "DECISIVE": {"energy": "MEDIUM", "pace": "MEDIUM", "emphasis": "HIGH"},
}
_SETTING_DELTAS = {
    "CURIOUS_ENGAGED": {"stability": -0.08, "style": 0.08, "speed": 0.03},
    "CLEAR_PRECISE": {"stability": 0.03, "style": 0.0, "speed": 0.0},
    "CONVERSATIONAL": {"stability": -0.04, "style": 0.04, "speed": 0.01},
    "SERIOUS_MEASURED": {"stability": 0.08, "style": -0.02, "speed": -0.04},
    "CAUTIONARY": {"stability": 0.06, "style": 0.02, "speed": -0.05},
    "EMPHATIC": {"stability": -0.07, "style": 0.1, "speed": -0.01},
    "CONFIDENT": {"stability": 0.0, "style": 0.04, "speed": 0.0},
    "WARM": {"stability": -0.03, "style": 0.04, "speed": -0.03},
    "DECISIVE": {"stability": 0.03, "style": 0.05, "speed": -0.01},
}
_MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "eleven_multilingual_v2": {
        "max_characters": 10_000,
        "supports_context_stitching": True,
        "supports_voice_settings": True,
        "supports_audio_tags": False,
    },
    "eleven_flash_v2_5": {
        "max_characters": 40_000,
        "supports_context_stitching": True,
        "supports_voice_settings": True,
        "supports_audio_tags": False,
    },
    "eleven_turbo_v2_5": {
        "max_characters": 40_000,
        "supports_context_stitching": True,
        "supports_voice_settings": True,
        "supports_audio_tags": False,
    },
    "eleven_v3": {
        "max_characters": 5_000,
        "supports_context_stitching": False,
        "supports_voice_settings": False,
        "supports_audio_tags": True,
    },
}


@dataclass(frozen=True, slots=True)
class VoiceAuthorityBundle:
    pool: ApprovedVoicePool
    casting: VoiceCastingDecision
    snapshot: NarrationVoiceSnapshot
    performance: NarrationPerformancePlan
    projection: TTSPerformanceProjection


class VoiceAuthorityService:
    """Create and resolve immutable channel- and project-scoped voice truth."""

    def __init__(self, session: Session):
        self.session = session

    def create_market_research(
        self, data: VoiceMarketResearchCreate
    ) -> VoiceMarketResearchArtifact:
        self._require_channel_scope(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            channel_profile_version_id=data.channel_profile_version_id,
            policy_snapshot_id=data.policy_snapshot_id,
        )
        payload = data.model_dump(mode="json")
        digest = content_hash(payload)
        existing = self.session.scalar(
            select(VoiceMarketResearchArtifact).where(
                VoiceMarketResearchArtifact.channel_workspace_id
                == data.channel_workspace_id,
                VoiceMarketResearchArtifact.channel_profile_version_id
                == data.channel_profile_version_id,
                VoiceMarketResearchArtifact.policy_snapshot_id
                == data.policy_snapshot_id,
                VoiceMarketResearchArtifact.content_hash == digest,
            )
        )
        if existing is not None:
            return existing
        record = VoiceMarketResearchArtifact(
            **data.model_dump(exclude={"market_identity", "requirements", "evidence"}),
            market_identity=data.market_identity.model_dump(mode="json"),
            requirements=data.requirements.model_dump(mode="json"),
            evidence=[item.model_dump(mode="json") for item in data.evidence],
            state="APPROVED",
            content_hash=digest,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def create_provider_catalog(
        self, data: VoiceProviderCatalogCreate
    ) -> VoiceProviderCatalogSnapshot:
        channel = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        if channel is None or channel.company_id != data.company_id:
            raise ValidationFailureError("VOICE_PROVIDER_CATALOG_CHANNEL_SCOPE_INVALID")
        payload = data.model_dump(mode="json")
        digest = content_hash(payload)
        existing = self.session.scalar(
            select(VoiceProviderCatalogSnapshot).where(
                VoiceProviderCatalogSnapshot.channel_workspace_id
                == data.channel_workspace_id,
                VoiceProviderCatalogSnapshot.provider == data.provider,
                VoiceProviderCatalogSnapshot.catalog_version == data.catalog_version,
                VoiceProviderCatalogSnapshot.content_hash == digest,
            )
        )
        if existing is not None:
            return existing
        record = VoiceProviderCatalogSnapshot(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            provider=data.provider,
            catalog_version=data.catalog_version,
            voices=[voice.model_dump(mode="json") for voice in data.voices],
            source_refs=list(data.source_refs),
            content_hash=digest,
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def create_approved_pool(self, data: ApprovedVoicePoolCreate) -> ApprovedVoicePool:
        research = self.session.get(
            VoiceMarketResearchArtifact, data.voice_market_research_id
        )
        catalog = self.session.get(
            VoiceProviderCatalogSnapshot, data.provider_catalog_snapshot_id
        )
        if (
            research is None
            or catalog is None
            or research.state != "APPROVED"
            or research.company_id != data.company_id
            or research.channel_workspace_id != data.channel_workspace_id
            or research.channel_profile_version_id != data.channel_profile_version_id
            or research.policy_snapshot_id != data.policy_snapshot_id
            or catalog.company_id != data.company_id
            or catalog.channel_workspace_id != data.channel_workspace_id
        ):
            raise ValidationFailureError("APPROVED_VOICE_POOL_AUTHORITY_MISMATCH")
        catalog_voices = {
            str(item.get("voice_id")): ProviderVoiceCandidate.model_validate(item)
            for item in catalog.voices
        }
        for voice in data.voices:
            if voice.voice_id not in catalog_voices:
                raise ValidationFailureError("APPROVED_VOICE_NOT_IN_PROVIDER_CATALOG")
            if catalog_voices[voice.voice_id] != voice:
                raise ValidationFailureError("APPROVED_VOICE_CATALOG_DRIFT")
        active = self.session.scalars(
            select(ApprovedVoicePool)
            .where(
                ApprovedVoicePool.channel_workspace_id == data.channel_workspace_id,
                ApprovedVoicePool.status == "APPROVED",
            )
            .with_for_update()
        ).all()
        for prior in active:
            prior.status = "SUPERSEDED"
        payload = data.model_dump(mode="json")
        digest = content_hash(payload)
        record = ApprovedVoicePool(
            **data.model_dump(exclude={"voices"}),
            voices=[voice.model_dump(mode="json") for voice in data.voices],
            content_hash=digest,
            approved_at=utc_now(),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def active_pool_for_project(self, project: VideoProject) -> ApprovedVoicePool:
        pool = self.session.scalar(
            select(ApprovedVoicePool)
            .where(
                ApprovedVoicePool.channel_workspace_id == project.channel_workspace_id,
                ApprovedVoicePool.channel_profile_version_id
                == project.channel_profile_version_id,
                ApprovedVoicePool.policy_snapshot_id == project.policy_snapshot_id,
                ApprovedVoicePool.status == "APPROVED",
            )
            .order_by(ApprovedVoicePool.version.desc())
            .limit(1)
        )
        if pool is None:
            raise ValidationFailureError("APPROVED_VOICE_POOL_REQUIRED")
        return pool

    def cast_voice(self, request: VoiceCastingRequest) -> VoiceCastingDecision:
        project = self.session.get(VideoProject, request.video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {request.video_project_id}")
        if project.schema_version != "v2" or project.production_lane != "LONG_FORM":
            raise ValidationFailureError("VOICE_CASTING_LONG_FORM_V2_REQUIRED")
        pool = self.active_pool_for_project(project)
        existing = self.session.scalar(
            select(VoiceCastingDecision)
            .where(
                VoiceCastingDecision.video_project_id == project.id,
                VoiceCastingDecision.qualified_script_hash
                == request.qualified_script_hash,
                VoiceCastingDecision.narration_mode == request.narration_mode,
                VoiceCastingDecision.state == "FROZEN",
            )
            .order_by(VoiceCastingDecision.decision_version.desc())
            .limit(1)
        )
        if existing is not None:
            return existing
        pool_voices = [
            ProviderVoiceCandidate.model_validate(item) for item in pool.voices
        ]
        series_binding = None
        if project.content_mode == "SERIES_EPISODE":
            if project.series_plan_id is None:
                raise ValidationFailureError("SERIES_VOICE_CASTING_PLAN_REQUIRED")
            series_binding = self.session.scalar(
                select(SeriesNarratorBinding)
                .where(
                    SeriesNarratorBinding.series_plan_id == project.series_plan_id,
                    SeriesNarratorBinding.state == "ACTIVE",
                )
                .order_by(SeriesNarratorBinding.binding_version.desc())
                .limit(1)
            )
        if series_binding is not None:
            selected = next(
                (
                    voice
                    for voice in pool_voices
                    if voice.voice_id == series_binding.voice_id
                    and series_binding.model_id in voice.approved_model_ids
                    and voice.availability_state == "AVAILABLE"
                ),
                None,
            )
            if selected is None:
                raise ValidationFailureError("SERIES_NARRATOR_NO_LONGER_APPROVED")
            selected_model_id = series_binding.model_id
            reason_codes = ["SERIES_NARRATOR_BINDING_REUSED"]
        else:
            selected = self._select_voice(
                voices=pool_voices,
                narration_mode=request.narration_mode,
                required_locale=request.required_locale,
                required_market=request.required_market,
            )
            selected_model_id = selected.default_model_id
            reason_codes = [
                "VOICE_SELECTED_FROM_APPROVED_POOL",
                "MARKET_AND_NARRATION_MODE_FIT",
                "DETERMINISTIC_CASTING",
            ]
        prior_decisions = self.session.scalars(
            select(VoiceCastingDecision)
            .where(
                VoiceCastingDecision.video_project_id == project.id,
                VoiceCastingDecision.state == "FROZEN",
            )
            .with_for_update()
        ).all()
        for prior in prior_decisions:
            prior.state = "SUPERSEDED"
        version = (
            int(
                self.session.scalar(
                    select(func.max(VoiceCastingDecision.decision_version)).where(
                        VoiceCastingDecision.video_project_id == project.id
                    )
                )
                or 0
            )
            + 1
        )
        research = self.session.get(
            VoiceMarketResearchArtifact, pool.voice_market_research_id
        )
        if research is None:
            raise ValidationFailureError("VOICE_CASTING_RESEARCH_REQUIRED")
        evidence_refs = sorted(
            {
                *selected.evidence_refs,
                *[
                    str(item.get("evidence_id"))
                    for item in research.evidence
                    if item.get("evidence_id")
                ],
            }
        )
        body = {
            "schema_version": "vcos.voice-casting-decision.v1",
            "video_project_id": str(project.id),
            "approved_voice_pool_id": str(pool.id),
            "approved_voice_pool_hash": pool.content_hash,
            "qualified_script_ref": request.qualified_script_ref,
            "qualified_script_hash": request.qualified_script_hash,
            "narration_mode": request.narration_mode,
            "selected_voice_id": selected.voice_id,
            "selected_model_id": selected_model_id,
            "baseline_delivery_profile": request.baseline_delivery_profile,
            "selection_reason_codes": reason_codes,
            "market_fit_evidence_refs": evidence_refs,
            "series_narrator_binding_id": (
                str(series_binding.id) if series_binding is not None else None
            ),
            "casting_policy_version": request.casting_policy_version,
            "decision_version": version,
        }
        record = VoiceCastingDecision(
            video_project_id=project.id,
            approved_voice_pool_id=pool.id,
            approved_voice_pool_hash=pool.content_hash,
            qualified_script_ref=request.qualified_script_ref,
            qualified_script_hash=request.qualified_script_hash,
            narration_mode=request.narration_mode,
            selected_voice_id=selected.voice_id,
            selected_model_id=selected_model_id,
            baseline_delivery_profile=deepcopy(request.baseline_delivery_profile),
            selection_reason_codes=reason_codes,
            market_fit_evidence_refs=evidence_refs,
            series_narrator_binding_id=(
                series_binding.id if series_binding is not None else None
            ),
            casting_policy_version=request.casting_policy_version,
            decision_version=version,
            state="FROZEN",
            content_hash=content_hash(body),
            created_by_user_id=request.created_by_user_id,
        )
        self.session.add(record)
        self.session.flush()
        if project.content_mode == "SERIES_EPISODE" and series_binding is None:
            series_binding = self._create_series_binding(
                project=project,
                pool=pool,
                decision=record,
                voice=selected,
                model_id=selected_model_id,
                actor_id=request.created_by_user_id,
            )
            record.series_narrator_binding_id = series_binding.id
            body["series_narrator_binding_id"] = str(series_binding.id)
            record.content_hash = content_hash(body)
            self.session.flush()
        return record

    def freeze_voice_snapshot(
        self, *, decision_id: uuid.UUID
    ) -> NarrationVoiceSnapshot:
        decision = self.session.get(VoiceCastingDecision, decision_id)
        if decision is None or decision.state != "FROZEN":
            raise ValidationFailureError("VOICE_CASTING_DECISION_FROZEN_REQUIRED")
        existing = self.session.scalar(
            select(NarrationVoiceSnapshot).where(
                NarrationVoiceSnapshot.voice_casting_decision_id == decision.id,
                NarrationVoiceSnapshot.state == "ACTIVE",
            )
        )
        if existing is not None:
            return existing
        pool = self.session.get(ApprovedVoicePool, decision.approved_voice_pool_id)
        if pool is None or pool.content_hash != decision.approved_voice_pool_hash:
            raise ValidationFailureError("VOICE_SNAPSHOT_POOL_MISMATCH")
        catalog = self.session.get(
            VoiceProviderCatalogSnapshot, pool.provider_catalog_snapshot_id
        )
        research = self.session.get(
            VoiceMarketResearchArtifact, pool.voice_market_research_id
        )
        if catalog is None or research is None:
            raise ValidationFailureError("VOICE_SNAPSHOT_SOURCE_AUTHORITY_REQUIRED")
        voice = next(
            (
                ProviderVoiceCandidate.model_validate(item)
                for item in pool.voices
                if item.get("voice_id") == decision.selected_voice_id
            ),
            None,
        )
        if (
            voice is None
            or decision.selected_model_id not in voice.approved_model_ids
            or voice.availability_state != "AVAILABLE"
        ):
            raise ValidationFailureError("VOICE_SNAPSHOT_SELECTED_VOICE_INVALID")
        active = self.session.scalars(
            select(NarrationVoiceSnapshot)
            .where(
                NarrationVoiceSnapshot.video_project_id == decision.video_project_id,
                NarrationVoiceSnapshot.state == "ACTIVE",
            )
            .with_for_update()
        ).all()
        for prior in active:
            prior.state = "SUPERSEDED"
        market_identity_hash = content_hash(research.market_identity)
        body = {
            "schema_version": "vcos.narration-voice-snapshot.v1",
            "video_project_id": str(decision.video_project_id),
            "voice_casting_decision_id": str(decision.id),
            "approved_voice_pool_id": str(pool.id),
            "provider": voice.provider,
            "voice_id": voice.voice_id,
            "model_id": decision.selected_model_id,
            "baseline_voice_settings": voice.default_settings,
            "voice_catalog_version": catalog.catalog_version,
            "approved_voice_pool_version": pool.version,
            "market_identity_hash": market_identity_hash,
            "qualified_script_hash": decision.qualified_script_hash,
        }
        record = NarrationVoiceSnapshot(
            video_project_id=decision.video_project_id,
            voice_casting_decision_id=decision.id,
            approved_voice_pool_id=pool.id,
            provider=voice.provider,
            voice_id=voice.voice_id,
            model_id=decision.selected_model_id,
            baseline_voice_settings=deepcopy(voice.default_settings),
            voice_catalog_version=catalog.catalog_version,
            approved_voice_pool_version=pool.version,
            market_identity_hash=market_identity_hash,
            qualified_script_hash=decision.qualified_script_hash,
            state="ACTIVE",
            content_hash=content_hash(body),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def create_performance_plan(
        self, data: NarrationPerformancePlanCreate
    ) -> NarrationPerformancePlan:
        snapshot = self.session.get(
            NarrationVoiceSnapshot, data.narration_voice_snapshot_id
        )
        if (
            snapshot is None
            or snapshot.video_project_id != data.video_project_id
            or snapshot.qualified_script_hash != data.qualified_script_hash
            or snapshot.state != "ACTIVE"
        ):
            raise ValidationFailureError("PERFORMANCE_PLAN_VOICE_SNAPSHOT_MISMATCH")
        gate = NarrationPerformanceGate.evaluate(
            narration=data.canonical_narration,
            beats=data.beats,
        )
        if not gate.passed:
            raise ValidationFailureError(
                "NARRATION_PERFORMANCE_GATE_FAILED:" + ",".join(gate.reason_codes)
            )
        existing = self.session.scalar(
            select(NarrationPerformancePlan).where(
                NarrationPerformancePlan.video_project_id == data.video_project_id,
                NarrationPerformancePlan.qualified_script_hash
                == data.qualified_script_hash,
                NarrationPerformancePlan.voice_snapshot_hash == snapshot.content_hash,
                NarrationPerformancePlan.state == "FROZEN",
            )
        )
        if existing is not None:
            return existing
        active = self.session.scalars(
            select(NarrationPerformancePlan)
            .where(
                NarrationPerformancePlan.video_project_id == data.video_project_id,
                NarrationPerformancePlan.state == "FROZEN",
            )
            .with_for_update()
        ).all()
        for prior in active:
            prior.state = "SUPERSEDED"
        canonical_hash = _text_hash(data.canonical_narration)
        body = {
            "schema_version": "vcos.narration-performance-plan.v1",
            "video_project_id": str(data.video_project_id),
            "qualified_script_ref": data.qualified_script_ref,
            "qualified_script_hash": data.qualified_script_hash,
            "canonical_narration_hash": canonical_hash,
            "narration_voice_snapshot_id": str(snapshot.id),
            "voice_snapshot_hash": snapshot.content_hash,
            "baseline_delivery": data.baseline_delivery,
            "beats": [beat.model_dump(mode="json") for beat in data.beats],
            "performance_policy_version": data.performance_policy_version,
            "coverage_gate_state": "PASS",
            "semantic_alignment_gate_state": "PASS",
            "continuity_gate_state": "PASS",
            "monotony_risk_gate_state": "PASS",
        }
        record = NarrationPerformancePlan(
            video_project_id=data.video_project_id,
            qualified_script_ref=data.qualified_script_ref,
            qualified_script_hash=data.qualified_script_hash,
            canonical_narration_hash=canonical_hash,
            narration_voice_snapshot_id=snapshot.id,
            voice_snapshot_hash=snapshot.content_hash,
            baseline_delivery=deepcopy(data.baseline_delivery),
            beats=[beat.model_dump(mode="json") for beat in data.beats],
            performance_policy_version=data.performance_policy_version,
            coverage_gate_state="PASS",
            semantic_alignment_gate_state="PASS",
            continuity_gate_state="PASS",
            monotony_risk_gate_state="PASS",
            state="FROZEN",
            content_hash=content_hash(body),
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def compile_tts_projection(
        self, *, performance_plan_id: uuid.UUID
    ) -> TTSPerformanceProjection:
        plan = self.session.get(NarrationPerformancePlan, performance_plan_id)
        if plan is None or plan.state != "FROZEN":
            raise ValidationFailureError("FROZEN_PERFORMANCE_PLAN_REQUIRED")
        existing = self.session.scalar(
            select(TTSPerformanceProjection).where(
                TTSPerformanceProjection.narration_performance_plan_id == plan.id,
                TTSPerformanceProjection.state == "FROZEN",
            )
        )
        if existing is not None:
            return existing
        snapshot = self.session.get(
            NarrationVoiceSnapshot, plan.narration_voice_snapshot_id
        )
        if snapshot is None or snapshot.content_hash != plan.voice_snapshot_hash:
            raise ValidationFailureError("TTS_PROJECTION_VOICE_SNAPSHOT_MISMATCH")
        pool = self.session.get(ApprovedVoicePool, snapshot.approved_voice_pool_id)
        if pool is None:
            raise ValidationFailureError("TTS_PROJECTION_VOICE_POOL_REQUIRED")
        voice = next(
            (
                ProviderVoiceCandidate.model_validate(item)
                for item in pool.voices
                if item.get("voice_id") == snapshot.voice_id
            ),
            None,
        )
        if voice is None:
            raise ValidationFailureError("TTS_PROJECTION_VOICE_NOT_IN_POOL")
        capability = elevenlabs_capability(snapshot.model_id)
        capabilities = {
            "max_characters": capability.max_characters,
            "supports_context_stitching": capability.supports_request_id_stitching,
            "supports_voice_settings": capability.supports_voice_settings,
            "supports_audio_tags": capability.supports_audio_tags,
        }
        beats = [NarrationPerformanceBeat.model_validate(item) for item in plan.beats]
        segments = self._compile_segments(
            beats=beats,
            voice=voice,
            capabilities=capabilities,
        )
        strategy = select_execution_strategy(
            model_id=snapshot.model_id, segment_count=len(segments)
        )
        body = {
            "schema_version": "vcos.tts-performance-projection.v1",
            "video_project_id": str(plan.video_project_id),
            "narration_performance_plan_id": str(plan.id),
            "narration_voice_snapshot_id": str(snapshot.id),
            "provider": "elevenlabs",
            "model_id": snapshot.model_id,
            "execution_strategy": strategy,
            "capability_profile_version": VOICE_CAPABILITY_PROFILE_VERSION,
            "segments": [segment.model_dump(mode="json") for segment in segments],
        }
        record = TTSPerformanceProjection(
            video_project_id=plan.video_project_id,
            narration_performance_plan_id=plan.id,
            narration_voice_snapshot_id=snapshot.id,
            provider="elevenlabs",
            model_id=snapshot.model_id,
            execution_strategy=strategy,
            capability_profile_version=VOICE_CAPABILITY_PROFILE_VERSION,
            segments=[segment.model_dump(mode="json") for segment in segments],
            state="FROZEN",
            content_hash=content_hash(body),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def ensure_project_bundle(
        self,
        *,
        video_project_id: uuid.UUID,
        qualified_script_ref: str,
        qualified_script_hash: str,
        canonical_narration: str,
        script_sections: Sequence[dict[str, Any]],
        narration_mode: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> VoiceAuthorityBundle:
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        pool = self.active_pool_for_project(project)
        research = self.session.get(
            VoiceMarketResearchArtifact, pool.voice_market_research_id
        )
        if research is None:
            raise ValidationFailureError("VOICE_MARKET_RESEARCH_REQUIRED")
        market = research.market_identity
        resolved_mode = narration_mode or infer_narration_mode(
            title=project.title,
            canonical_narration=canonical_narration,
        )
        casting = self.cast_voice(
            VoiceCastingRequest(
                video_project_id=project.id,
                qualified_script_ref=qualified_script_ref,
                qualified_script_hash=qualified_script_hash,
                narration_mode=resolved_mode,
                required_locale=str(market["locale"]),
                required_market=str(market["primary_market"]),
                baseline_delivery_profile={
                    "pace": research.requirements.get("pacing_profile", "MEDIUM"),
                    "energy": research.requirements.get("energy_profile", "MEDIUM"),
                    "expressiveness": "CONTROLLED",
                    "authority": research.requirements.get("authority_profile", "HIGH"),
                    "conversationality": research.requirements.get(
                        "conversationality_profile", "MEDIUM"
                    ),
                },
                casting_policy_version=VOICE_CASTING_POLICY_VERSION,
                created_by_user_id=created_by_user_id,
            )
        )
        snapshot = self.freeze_voice_snapshot(decision_id=casting.id)
        beats = compile_performance_beats(
            canonical_narration=canonical_narration,
            script_sections=script_sections,
        )
        performance = self.create_performance_plan(
            NarrationPerformancePlanCreate(
                video_project_id=project.id,
                qualified_script_ref=qualified_script_ref,
                qualified_script_hash=qualified_script_hash,
                canonical_narration=canonical_narration,
                narration_voice_snapshot_id=snapshot.id,
                baseline_delivery=deepcopy(casting.baseline_delivery_profile),
                beats=beats,
                performance_policy_version=VOICE_PERFORMANCE_POLICY_VERSION,
                created_by_user_id=created_by_user_id,
            )
        )
        projection = self.compile_tts_projection(performance_plan_id=performance.id)
        return VoiceAuthorityBundle(
            pool=pool,
            casting=casting,
            snapshot=snapshot,
            performance=performance,
            projection=projection,
        )

    def _require_channel_scope(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        channel_profile_version_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID,
    ) -> None:
        channel = self.session.get(ChannelWorkspace, channel_workspace_id)
        profile = self.session.get(ChannelProfileVersion, channel_profile_version_id)
        policy = self.session.get(CompiledChannelPolicySnapshot, policy_snapshot_id)
        if (
            channel is None
            or profile is None
            or policy is None
            or channel.company_id != company_id
            or profile.channel_workspace_id != channel.id
            or policy.channel_workspace_id != channel.id
            or policy.channel_profile_version_id != profile.id
        ):
            raise ValidationFailureError("VOICE_MARKET_RESEARCH_SCOPE_INVALID")

    @staticmethod
    def _select_voice(
        *,
        voices: Sequence[ProviderVoiceCandidate],
        narration_mode: str,
        required_locale: str,
        required_market: str,
    ) -> ProviderVoiceCandidate:
        eligible = [
            voice
            for voice in voices
            if voice.availability_state == "AVAILABLE"
            and narration_mode in voice.narration_mode_fit
            and (
                required_locale in voice.locale_tags
                or required_locale.split("-", 1)[0] in voice.language_tags
            )
        ]
        if not eligible:
            raise ValidationFailureError("NO_APPROVED_MARKET_VOICE_FOR_VIDEO")

        def score(voice: ProviderVoiceCandidate) -> tuple[int, int, str]:
            market = 30 if required_market in voice.market_fit_tags else 0
            locale = 20 if required_locale in voice.locale_tags else 10
            mode = 50 if narration_mode in voice.narration_mode_fit else 0
            qualities = (
                voice.clarity_score
                + voice.authority_score
                + voice.conversationality_score
                + voice.warmth_score
                + voice.energy_score
            ) // 10
            return (mode + market + locale + qualities, -voice.priority, voice.voice_id)

        return max(eligible, key=score)

    def _create_series_binding(
        self,
        *,
        project: VideoProject,
        pool: ApprovedVoicePool,
        decision: VoiceCastingDecision,
        voice: ProviderVoiceCandidate,
        model_id: str,
        actor_id: uuid.UUID | None,
    ) -> SeriesNarratorBinding:
        if project.series_plan_id is None:
            raise ValidationFailureError("SERIES_NARRATOR_PLAN_REQUIRED")
        version = (
            int(
                self.session.scalar(
                    select(func.max(SeriesNarratorBinding.binding_version)).where(
                        SeriesNarratorBinding.series_plan_id == project.series_plan_id
                    )
                )
                or 0
            )
            + 1
        )
        body = {
            "schema_version": "vcos.series-narrator-binding.v1",
            "series_plan_id": str(project.series_plan_id),
            "approved_voice_pool_id": str(pool.id),
            "source_voice_casting_decision_id": str(decision.id),
            "binding_version": version,
            "voice_id": voice.voice_id,
            "model_id": model_id,
            "policy_version": VOICE_CASTING_POLICY_VERSION,
        }
        binding = SeriesNarratorBinding(
            series_plan_id=project.series_plan_id,
            approved_voice_pool_id=pool.id,
            source_voice_casting_decision_id=decision.id,
            binding_version=version,
            voice_id=voice.voice_id,
            model_id=model_id,
            policy_version=VOICE_CASTING_POLICY_VERSION,
            state="ACTIVE",
            content_hash=content_hash(body),
            created_by_user_id=actor_id,
        )
        self.session.add(binding)
        self.session.flush()
        return binding

    @staticmethod
    def _compile_segments(
        *,
        beats: Sequence[NarrationPerformanceBeat],
        voice: ProviderVoiceCandidate,
        capabilities: dict[str, Any],
    ) -> list[TTSPerformanceSegment]:
        if not beats:
            raise ValidationFailureError("TTS_PROJECTION_BEATS_REQUIRED")
        grouped: list[list[NarrationPerformanceBeat]] = []
        current: list[NarrationPerformanceBeat] = []
        for beat in beats:
            if current and current[-1].delivery_intent != beat.delivery_intent:
                grouped.append(current)
                current = []
            current.append(beat)
        if current:
            grouped.append(current)
        if not capabilities["supports_voice_settings"]:
            grouped = [list(beats)]
        segments: list[TTSPerformanceSegment] = []
        for index, group in enumerate(grouped, start=1):
            start = group[0].source_text_start
            end = group[-1].source_text_end
            intent = group[0].delivery_intent
            settings = _compile_voice_settings(
                voice=voice,
                delivery_intent=intent,
                supports_voice_settings=bool(capabilities["supports_voice_settings"]),
            )
            segments.append(
                TTSPerformanceSegment(
                    segment_id=f"segment-{index:03d}",
                    ordinal=index,
                    beat_ids=[beat.beat_id for beat in group],
                    source_text_start=start,
                    source_text_end=end,
                    text_hash=content_hash(
                        {
                            "source_text_start": start,
                            "source_text_end": end,
                            "beat_hashes": [beat.source_text_hash for beat in group],
                        }
                    ),
                    voice_settings=settings,
                )
            )
        for index, segment in enumerate(segments):
            previous_text = None
            next_text = None
            if capabilities["supports_context_stitching"]:
                if index:
                    previous_text = " ".join(segments[index - 1].beat_ids)
                if index + 1 < len(segments):
                    next_text = " ".join(segments[index + 1].beat_ids)
            segments[index] = segment.model_copy(
                update={"previous_text": previous_text, "next_text": next_text}
            )
        return segments


@dataclass(frozen=True, slots=True)
class PerformanceGateResult:
    passed: bool
    reason_codes: tuple[str, ...]


class NarrationPerformanceGate:
    @classmethod
    def evaluate(
        cls,
        *,
        narration: str,
        beats: Sequence[NarrationPerformanceBeat],
    ) -> PerformanceGateResult:
        reasons: list[str] = []
        if not narration or not beats:
            return PerformanceGateResult(False, ("PERFORMANCE_COVERAGE_MISSING",))
        expected_start = 0
        functions: set[str] = set()
        intents: set[str] = set()
        prior_intent: str | None = None
        rapid_changes = 0
        for ordinal, beat in enumerate(beats, start=1):
            if beat.ordinal != ordinal:
                reasons.append("PERFORMANCE_BEAT_ORDINAL_INVALID")
            if beat.source_text_start != expected_start:
                reasons.append("PERFORMANCE_BEAT_COVERAGE_GAP_OR_OVERLAP")
            if beat.source_text_end > len(narration):
                reasons.append("PERFORMANCE_BEAT_SPAN_OUTSIDE_NARRATION")
                continue
            text = narration[beat.source_text_start : beat.source_text_end]
            if _text_hash(text) != beat.source_text_hash:
                reasons.append("PERFORMANCE_BEAT_TEXT_HASH_MISMATCH")
            if beat.narration_function not in _NARRATION_FUNCTIONS:
                reasons.append("PERFORMANCE_FUNCTION_INVALID")
            if beat.delivery_intent not in _DELIVERY_INTENTS:
                reasons.append("PERFORMANCE_DELIVERY_INTENT_INVALID")
            if beat.delivery_intent not in _FUNCTION_INTENT_COMPATIBILITY.get(
                beat.narration_function, set()
            ):
                reasons.append("PERFORMANCE_SEMANTIC_ALIGNMENT_FAILURE")
            functions.add(beat.narration_function)
            intents.add(beat.delivery_intent)
            if prior_intent is not None and prior_intent != beat.delivery_intent:
                rapid_changes += 1
            prior_intent = beat.delivery_intent
            expected_start = beat.source_text_end
        if expected_start != len(narration):
            reasons.append("PERFORMANCE_BEAT_COVERAGE_INCOMPLETE")
        if len(functions) >= 3 and len(intents) == 1:
            reasons.append("NARRATION_MONOTONY_RISK")
        if len(beats) >= 5 and rapid_changes == len(beats) - 1:
            reasons.append("PERFORMANCE_CONTINUITY_FAILURE")
        return PerformanceGateResult(not reasons, tuple(sorted(set(reasons))))


def compile_performance_beats(
    *,
    canonical_narration: str,
    script_sections: Sequence[dict[str, Any]],
) -> list[NarrationPerformanceBeat]:
    if not canonical_narration.strip() or not script_sections:
        raise ValidationFailureError("PERFORMANCE_SOURCE_SECTIONS_REQUIRED")
    starts: list[int] = []
    cursor = 0
    extracted: list[tuple[str, str]] = []
    for index, section in enumerate(script_sections):
        heading = str(section.get("heading") or section.get("section_id") or "").strip()
        text = _section_text(section)
        if not text:
            raise ValidationFailureError("PERFORMANCE_SECTION_TEXT_REQUIRED")
        match = _find_flexible(canonical_narration, text, cursor)
        if match is None:
            raise ValidationFailureError("PERFORMANCE_SECTION_NOT_IN_CANONICAL_SCRIPT")
        starts.append(match.start())
        extracted.append((heading, text))
        cursor = match.end()
    starts[0] = 0
    boundaries = [*starts, len(canonical_narration)]
    beats: list[NarrationPerformanceBeat] = []
    for index, ((heading, _), start, end) in enumerate(
        zip(extracted, boundaries[:-1], boundaries[1:], strict=True), start=1
    ):
        function, intent = _performance_semantics(
            heading=heading,
            ordinal=index,
            total=len(extracted),
        )
        profile = _DELIVERY_PROFILE[intent]
        text = canonical_narration[start:end]
        beats.append(
            NarrationPerformanceBeat(
                beat_id=f"beat-{index:03d}",
                ordinal=index,
                source_text_start=start,
                source_text_end=end,
                source_text_hash=_text_hash(text),
                narration_function=function,
                delivery_intent=intent,
                energy=profile["energy"],
                pace=profile["pace"],
                emphasis=profile["emphasis"],
                pause_before_ms=0 if index == 1 else 120,
                pause_after_ms=180
                if function in {"HOOK", "KEY_INSIGHT", "WARNING"}
                else 80,
                continuity_intent="PRESERVE_PRIMARY_NARRATOR_AND_SEMANTIC_FLOW",
                provider_control_intent={"delivery_intent": intent},
            )
        )
    result = NarrationPerformanceGate.evaluate(
        narration=canonical_narration,
        beats=beats,
    )
    if not result.passed:
        raise ValidationFailureError(
            "COMPILED_PERFORMANCE_PLAN_INVALID:" + ",".join(result.reason_codes)
        )
    return beats


def infer_narration_mode(*, title: str, canonical_narration: str) -> str:
    text = f"{title} {canonical_narration[:1200]}".lower()
    if any(term in text for term in ("warning", "risk", "failure", "danger")):
        return "CAUTIONARY"
    if any(term in text for term in ("case study", "story", "what happened")):
        return "STORY_CASE_STUDY"
    if any(term in text for term in ("compare", "tradeoff", "decision")):
        return "ANALYTICAL"
    if any(term in text for term in ("step", "workflow", "how to", "build")):
        return "TACTICAL"
    return "TECHNICAL_EXPLAINER"


def voice_authority_required(policy_snapshot: CompiledChannelPolicySnapshot) -> bool:
    policy = policy_snapshot.compiled_payload.get("voice_authority_policy")
    return (
        isinstance(policy, dict) and policy.get("required_for_real_production") is True
    )


def _section_text(section: dict[str, Any]) -> str:
    narration = section.get("narration")
    if isinstance(narration, str) and narration.strip():
        return narration.strip()
    sentences = section.get("sentences")
    if isinstance(sentences, list):
        values = []
        for item in sentences:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                values.append(item["text"].strip())
            elif isinstance(item, str):
                values.append(item.strip())
        return " ".join(value for value in values if value)
    return ""


def _find_flexible(text: str, fragment: str, start: int) -> re.Match[str] | None:
    pieces = [re.escape(piece) for piece in re.split(r"\s+", fragment.strip())]
    return re.compile(r"\s+".join(pieces)).search(text, start)


def _performance_semantics(
    *, heading: str, ordinal: int, total: int
) -> tuple[str, str]:
    value = heading.casefold()
    if ordinal == 1 or any(term in value for term in ("hook", "open")):
        return "HOOK", "CURIOUS_ENGAGED"
    if ordinal == total or any(
        term in value for term in ("conclusion", "close", "ending")
    ):
        return "CONCLUSION", "DECISIVE"
    if any(term in value for term in ("warning", "risk", "limit", "failure")):
        return "LIMITATION", "CAUTIONARY"
    if any(term in value for term in ("example", "case", "scenario")):
        return "EXAMPLE", "CONVERSATIONAL"
    if any(term in value for term in ("compare", "contrast", "versus", "vs")):
        return "CONTRAST", "EMPHATIC"
    if any(term in value for term in ("insight", "key", "decision", "distinction")):
        return "KEY_INSIGHT", "EMPHATIC"
    if any(term in value for term in ("process", "workflow", "mechanism", "how")):
        return "PROCESS", "CLEAR_PRECISE"
    if ordinal == total - 1:
        return "PAYOFF", "CONFIDENT"
    return "EXPLANATION", "CLEAR_PRECISE"


def _compile_voice_settings(
    *,
    voice: ProviderVoiceCandidate,
    delivery_intent: str,
    supports_voice_settings: bool,
) -> dict[str, Any]:
    settings = deepcopy(voice.default_settings)
    if not supports_voice_settings:
        return settings
    for key, delta in _SETTING_DELTAS[delivery_intent].items():
        bounds = voice.safe_setting_bounds[key]
        settings[key] = round(
            max(
                float(bounds["min"]),
                min(float(bounds["max"]), float(settings[key]) + delta),
            ),
            4,
        )
    return settings


def _text_hash(value: str) -> str:
    return content_hash({"text": value})


def available_voice_ids(pool: ApprovedVoicePool) -> set[str]:
    return {
        voice.voice_id
        for voice in (
            ProviderVoiceCandidate.model_validate(item) for item in pool.voices
        )
        if voice.availability_state == "AVAILABLE"
    }


def validate_single_primary_narrator(
    projections: Iterable[TTSPerformanceProjection],
) -> None:
    snapshot_ids = {item.narration_voice_snapshot_id for item in projections}
    if len(snapshot_ids) > 1:
        raise ValidationFailureError("VOICE_SWITCH_WITHIN_VIDEO_FORBIDDEN")
