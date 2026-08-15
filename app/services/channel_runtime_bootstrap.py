"""Channel-scoped runtime bootstrap for one-engine/many-profiles operation.

This service replaces the historical first-channel bootstrap assumption.  It
never chooses a niche or channel.  The caller supplies a ChannelWorkspace (or,
for backward-compatible single-channel operation, the database must contain
exactly one active channel).  Runtime truth is always the channel's active
profile + immutable compiled snapshot.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.launch_cadence import (
    FirstChannelLaunchPolicyCreate,
    LaunchPolicyApproval,
    LaunchRunCreate,
    LaunchRunTransition,
)
from app.contracts.profile import ChannelProfileInput, ChannelProfileVersionCreate
from app.core.actor import ActorContext, ActorType
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailureError
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.foundation import User
from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion, LaunchRun
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m11_1 import OperatorUser
from app.db.models.workflow import VideoProject
from app.services.channel_profile import ChannelProfileService
from app.services.company_access import require_company_permission
from app.services.config_registry import content_hash
from app.services.launch_cadence import (
    FirstChannelLaunchPolicyService,
    LaunchRunService,
)
from app.services.profile_compiler import ChannelProfileCompiler


@dataclass(frozen=True, slots=True)
class Phase1RuntimeAuthority:
    channel: ChannelWorkspace
    profile: ChannelProfileVersion
    snapshot: CompiledChannelPolicySnapshot


@dataclass(frozen=True, slots=True)
class Phase1RuntimeBootstrapResult:
    channel_profile_version: ChannelProfileVersion
    policy_snapshot: CompiledChannelPolicySnapshot
    launch_policy: FirstChannelLaunchPolicyVersion
    launch_run: LaunchRun


class Phase1RuntimeBootstrapService:
    """Compatibility name; implementation is fully channel-scoped."""

    def __init__(self, session: Session):
        self.session = session

    def resolve_channel_authority(
        self,
        *,
        actor: ActorContext,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> Phase1RuntimeAuthority:
        self._require_persisted_actor(actor)
        stmt = select(ChannelWorkspace).where(ChannelWorkspace.status == "active")
        if channel_workspace_id is not None:
            stmt = stmt.where(ChannelWorkspace.id == channel_workspace_id)
        channels = list(self.session.scalars(stmt).all())
        if len(channels) != 1:
            reason = (
                "CHANNEL_RUNTIME_AUTHORITY_NOT_FOUND"
                if channel_workspace_id is not None
                else "CHANNEL_RUNTIME_AUTHORITY_AMBIGUOUS"
            )
            raise NotFoundError(reason)
        channel = channels[0]
        require_company_permission(
            self.session,
            actor=actor,
            permission="channel.manage",
            company_id=channel.company_id,
        )
        if channel.active_policy_snapshot_id is None:
            raise ValidationFailureError("CHANNEL_RUNTIME_ACTIVE_SNAPSHOT_REQUIRED")
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id
        )
        if snapshot is None or snapshot.status != "active":
            raise ValidationFailureError("CHANNEL_RUNTIME_ACTIVE_SNAPSHOT_REQUIRED")
        profile = self.session.get(
            ChannelProfileVersion, snapshot.channel_profile_version_id
        )
        if (
            profile is None
            or profile.status != "active"
            or profile.channel_workspace_id != channel.id
            or snapshot.channel_workspace_id != channel.id
        ):
            raise ValidationFailureError("CHANNEL_RUNTIME_ACTIVE_PROFILE_REQUIRED")
        self._require_exact_scoped_identity(channel=channel, snapshot=snapshot)
        return Phase1RuntimeAuthority(channel=channel, profile=profile, snapshot=snapshot)

    def build_sanitized_profile_input(
        self,
        *,
        actor: ActorContext,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> ChannelProfileInput:
        authority = self.resolve_channel_authority(
            actor=actor, channel_workspace_id=channel_workspace_id
        )
        return self.sanitize_profile_input(
            authority.profile.profile_input, channel=authority.channel
        )

    @staticmethod
    def sanitize_profile_input(
        active_profile_input: ChannelProfileInput | dict[str, Any],
        *,
        channel: ChannelWorkspace,
    ) -> ChannelProfileInput:
        source = ChannelProfileInput.model_validate(active_profile_input)
        payload = deepcopy(source.model_dump(mode="json"))
        existing_contract = _source_contract(payload.get("policies"))
        identity = _clean_mapping(existing_contract.get("channel_identity"))
        audience = _clean_mapping(existing_contract.get("target_audience"))
        editorial = _clean_mapping(existing_contract.get("editorial_strategy"))
        voice = _clean_mapping(existing_contract.get("voice_style"))
        rights = _clean_mapping(existing_contract.get("rights_policy"))
        budget = _clean_mapping(existing_contract.get("budget_policy"))
        learning = _clean_mapping(existing_contract.get("learning_policy"))
        media = _clean_mapping(existing_contract.get("media_policy"))
        market = _clean_mapping(existing_contract.get("market_locale"))

        content_pillars = _clean_list_of_strings(payload.get("content_pillars"))
        if not content_pillars:
            content_pillars = _clean_list_of_strings(editorial.get("content_pillars"))
        if not content_pillars:
            fallback = str(identity.get("niche") or source.display_name or channel.name).strip()
            content_pillars = [fallback]
        editorial["content_pillars"] = content_pillars
        audience.setdefault("primary_persona", "channel-defined target audience")
        voice.setdefault("narration_tone", "channel_profile_default")
        voice.setdefault("pacing", "measured")
        media["renderer"] = "NativeFFmpegRenderer"

        primary_market = str(
            channel.target_market
            or channel.primary_region
            or market.get("primary_market")
            or source.target_market
        )
        content_language = str(
            channel.primary_language
            or market.get("content_language")
            or "en"
        )
        locale = str(
            market.get("audience_locale")
            or market.get("locale")
            or content_language
        )
        channel_key = str(channel.key)
        channel_name = str(channel.name)
        clean_contract = {
            "channel_identity": {
                **identity,
                "channel_key": channel_key,
                "channel_name": channel_name,
                "channel_type": identity.get("channel_type", "YOUTUBE_CHANNEL"),
                "primary_platform": identity.get("primary_platform", "YouTube"),
                "secondary_platforms": list(identity.get("secondary_platforms") or []),
            },
            "target_audience": audience,
            "market_locale": {
                **market,
                "primary_market": primary_market,
                "audience_locale": locale,
                "content_language": content_language,
            },
            "editorial_strategy": editorial,
            "format_policy": _long_form_policy(existing_contract.get("format_policy")),
            "voice_style": voice,
            "platform_strategy": {
                **_clean_mapping(existing_contract.get("platform_strategy")),
                "primary_platform": "YouTube",
                "youtube_is_learning_authority": True,
                "publish_mode": "youtube_private_stage_then_human_public",
                "auto_public_allowed": False,
                "studio_scraping_allowed": False,
            },
            "media_policy": media,
            "rights_policy": rights,
            "budget_policy": budget,
            "learning_policy": learning,
        }

        payload["display_name"] = channel_name
        payload["target_market"] = primary_market
        payload["human_review_strictness"] = "final_decision_only"
        payload["format_strategy"] = _long_form_strategy(payload.get("format_strategy"))
        payload["media_style"] = {
            **_clean_mapping(payload.get("media_style")),
            "renderer": "NativeFFmpegRenderer",
            "final_render_authority": "NativeFFmpegRenderer",
        }
        payload["platform_strategy"] = {
            **_clean_mapping(payload.get("platform_strategy")),
            "primary": "youtube_long_form",
            "primary_platform": "YouTube",
            "publish_mode": "youtube_private_stage_then_human_public",
            "auto_public_allowed": False,
            "studio_scraping_allowed": False,
            "secondary_platforms": [],
        }
        payload["series_plan"] = _clean_value(payload.get("series_plan") or [])
        payload["channel_policy"] = _channel_policy_without_legacy_human_gates(
            payload.get("channel_policy"), channel_key=channel_key
        )
        policies = _clean_mapping(payload.get("policies"))
        for key in (
            "channel_contract",
            "channel_contract_json",
            "m12_2p_channel_contract",
            "field_source_map_json",
        ):
            policies.pop(key, None)
        policies["channel_contract"] = clean_contract
        payload["policies"] = policies
        return ChannelProfileInput.model_validate(payload)

    def create_launch_policy(
        self,
        *,
        data: FirstChannelLaunchPolicyCreate,
        actor: ActorContext,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> FirstChannelLaunchPolicyVersion:
        authority = self.resolve_channel_authority(
            actor=actor, channel_workspace_id=channel_workspace_id or data.channel_workspace_id
        )
        if (
            data.company_id != authority.channel.company_id
            or data.channel_workspace_id != authority.channel.id
            or data.channel_profile_version_id != authority.profile.id
            or data.policy_snapshot_id != authority.snapshot.id
        ):
            raise ValidationFailureError("CHANNEL_LAUNCH_POLICY_AUTHORITY_MISMATCH")
        return FirstChannelLaunchPolicyService(self.session).create(data=data, actor=actor)

    def create_launch_run(
        self,
        *,
        data: LaunchRunCreate,
        actor: ActorContext,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> LaunchRun:
        policy = self.session.get(
            FirstChannelLaunchPolicyVersion, data.launch_policy_version_id
        )
        if policy is None:
            raise NotFoundError("launch policy not found")
        authority = self.resolve_channel_authority(
            actor=actor,
            channel_workspace_id=channel_workspace_id or policy.channel_workspace_id,
        )
        if (
            policy.company_id != authority.channel.company_id
            or policy.channel_workspace_id != authority.channel.id
            or policy.channel_profile_version_id != authority.profile.id
            or policy.policy_snapshot_id != authority.snapshot.id
        ):
            raise ValidationFailureError("CHANNEL_LAUNCH_RUN_AUTHORITY_MISMATCH")
        return LaunchRunService(self.session).create(data=data, actor=actor)

    def bootstrap(
        self,
        *,
        actor: ActorContext,
        preparation_started_on: date,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> Phase1RuntimeBootstrapResult:
        authority = self.resolve_channel_authority(
            actor=actor, channel_workspace_id=channel_workspace_id
        )
        clean_input = self.sanitize_profile_input(
            authority.profile.profile_input, channel=authority.channel
        )
        profile, snapshot = self._ensure_clean_profile_active(
            authority=authority,
            clean_input=clean_input,
            actor=actor,
        )
        approval_ref = (
            f"operator-authorization://runtime-bootstrap/{authority.channel.id}/"
            f"{preparation_started_on.isoformat()}"
        )
        evidence_refs = [
            {
                "type": "operator_authorization",
                "ref": approval_ref,
                "actor_id": str(actor.actor_id),
            },
            {
                "type": "channel_profile_snapshot",
                "ref": f"db://compiled_channel_policy_snapshots/{snapshot.id}",
                "content_hash": snapshot.content_hash,
            },
        ]
        policy = self._ensure_launch_policy(
            channel=authority.channel,
            profile=profile,
            snapshot=snapshot,
            actor=actor,
            evidence_refs=evidence_refs,
        )
        run = self._ensure_launch_run_active(
            channel=authority.channel,
            policy=policy,
            actor=actor,
            preparation_started_on=preparation_started_on,
        )
        return Phase1RuntimeBootstrapResult(
            channel_profile_version=profile,
            policy_snapshot=snapshot,
            launch_policy=policy,
            launch_run=run,
        )

    def _ensure_clean_profile_active(
        self,
        *,
        authority: Phase1RuntimeAuthority,
        clean_input: ChannelProfileInput,
        actor: ActorContext,
    ) -> tuple[ChannelProfileVersion, CompiledChannelPolicySnapshot]:
        service = ChannelProfileService(self.session)
        compiler = ChannelProfileCompiler(self.session)
        if clean_input.channel_policy is not None:
            refreshed_payload = clean_input.model_dump(mode="json")
            refreshed_payload["channel_policy"] = (
                compiler.refresh_qualified_visual_source_binding(
                    active_policy=clean_input.channel_policy
                ).model_dump(mode="json")
            )
            clean_input = ChannelProfileInput.model_validate(refreshed_payload)
        clean_hash = content_hash(clean_input.model_dump(mode="json"))
        profiles = service.list_profile_versions(authority.channel.id)
        profile = next(
            (item for item in profiles if item.profile_input_hash == clean_hash), None
        )
        if profile is None:
            profile = service.create_profile_version(
                channel_id=authority.channel.id,
                data=ChannelProfileVersionCreate(
                    profile_input=clean_input,
                    created_by=actor.actor_id,
                ),
                correlation_id=f"channel-runtime-bootstrap-profile-create:{authority.channel.id}",
            )
        snapshot = self.session.scalars(
            select(CompiledChannelPolicySnapshot)
            .where(CompiledChannelPolicySnapshot.channel_profile_version_id == profile.id)
            .order_by(CompiledChannelPolicySnapshot.snapshot_version.desc())
        ).first()
        if snapshot is None:
            compiled = compiler.compile(
                profile_version_id=profile.id,
                correlation_id=f"channel-runtime-bootstrap-profile-compile:{authority.channel.id}",
            )
            snapshot = self.session.get(CompiledChannelPolicySnapshot, compiled.snapshot_id)
        if snapshot is None:
            raise ValidationFailureError("CHANNEL_PROFILE_COMPILE_SNAPSHOT_REQUIRED")
        approval_ref = f"operator-authorization://runtime-bootstrap/{authority.channel.id}/{profile.id}"
        if profile.status in {"draft", "compiled"}:
            service.submit_for_approval(profile.id)
        if profile.status == "pending_approval":
            service.approve_profile_version(
                profile_version_id=profile.id,
                approved_by=actor.actor_id,
                approval_ref=approval_ref,
                correlation_id=f"channel-runtime-bootstrap-profile-approve:{authority.channel.id}",
            )
        if snapshot.status != "active":
            service.activate_snapshot(
                snapshot_id=snapshot.id,
                correlation_id=f"channel-runtime-bootstrap-profile-activate:{authority.channel.id}",
            )
        refreshed_profile = self.session.get(ChannelProfileVersion, profile.id)
        refreshed_snapshot = self.session.get(CompiledChannelPolicySnapshot, snapshot.id)
        if (
            refreshed_profile is None
            or refreshed_snapshot is None
            or refreshed_profile.status != "active"
            or refreshed_snapshot.status != "active"
        ):
            raise ValidationFailureError("CHANNEL_PROFILE_ACTIVATION_REQUIRED")
        return refreshed_profile, refreshed_snapshot

    def _ensure_launch_policy(
        self,
        *,
        channel: ChannelWorkspace,
        profile: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        actor: ActorContext,
        evidence_refs: list[dict[str, Any]],
    ) -> FirstChannelLaunchPolicyVersion:
        service = FirstChannelLaunchPolicyService(self.session)
        existing = service.active_for_channel(channel.id)
        supersedes_policy_version_id = None
        if existing is not None:
            if (
                existing.channel_profile_version_id != profile.id
                or existing.policy_snapshot_id != snapshot.id
            ):
                self._close_empty_superseded_launch_run(policy=existing, actor=actor)
                supersedes_policy_version_id = existing.id
            else:
                return existing
        next_version = (
            self.session.scalar(
                select(FirstChannelLaunchPolicyVersion.policy_version)
                .where(FirstChannelLaunchPolicyVersion.channel_workspace_id == channel.id)
                .order_by(FirstChannelLaunchPolicyVersion.policy_version.desc())
                .limit(1)
            )
            or 0
        ) + 1
        created = self.create_launch_policy(
            data=FirstChannelLaunchPolicyCreate(
                company_id=channel.company_id,
                channel_workspace_id=channel.id,
                channel_profile_version_id=profile.id,
                policy_snapshot_id=snapshot.id,
                approved_initial_series_plan_ids=[],
                policy_version=next_version,
                supersedes_policy_version_id=supersedes_policy_version_id,
                launch_mode="CONTROLLED_EVIDENCE_BUILDING",
                preparation_days_min=14,
                preparation_days_max=21,
                idea_candidates_target=12,
                preflight_pass_target=8,
                greenlight_target=6,
                public_ready_buffer_target=3,
                max_days_produced_ahead=14,
                max_concurrent_productions=1,
                max_active_runs=2,
                initial_series_count=0,
                first_n_public_videos=10,
                max_primary_variables_changed_per_video=1,
                auto_niche_pivot=False,
                auto_series_kill=False,
                auto_playbook_promotion=False,
                pre_render_script_review=False,
                pre_render_package_review=False,
                final_video_decision="UPLOAD_OR_DO_NOT_UPLOAD",
                public_publish="MANUAL_ONLY",
                commercial_model="PLATFORM_AD_REVENUE_ONLY",
                affiliate_cta=False,
                sponsor_content=False,
                primary_cta="NEXT_VIDEO_OR_SUBSCRIBE",
                evidence_refs=evidence_refs,
            ),
            actor=actor,
            channel_workspace_id=channel.id,
        )
        return service.approve(
            policy_version_id=created.id,
            data=LaunchPolicyApproval(evidence_refs=evidence_refs),
            actor=actor,
        )

    def _close_empty_superseded_launch_run(
        self, *, policy: FirstChannelLaunchPolicyVersion, actor: ActorContext
    ) -> None:
        has_project = self.session.scalar(
            select(VideoProject.id)
            .where(VideoProject.channel_workspace_id == policy.channel_workspace_id)
            .where(VideoProject.policy_snapshot_id == policy.policy_snapshot_id)
            .limit(1)
        )
        has_slot = self.session.scalar(
            select(LongFormPublishSlot.id)
            .where(LongFormPublishSlot.launch_policy_version_id == policy.id)
            .limit(1)
        )
        if has_project is not None or has_slot is not None:
            raise ValidationFailureError("CHANNEL_ACTIVE_LAUNCH_POLICY_SUPERSESSION_UNSAFE")
        open_runs = list(
            self.session.scalars(
                select(LaunchRun).where(
                    LaunchRun.launch_policy_version_id == policy.id,
                    LaunchRun.state.in_(["PREPARING", "READY_TO_LAUNCH", "ACTIVE", "PAUSED"]),
                )
            ).all()
        )
        for run in open_runs:
            LaunchRunService(self.session).transition(
                launch_run_id=run.id,
                data=LaunchRunTransition(
                    target_state="CANCELED",
                    reason_codes=["CHANNEL_RUNTIME_POLICY_SUPERSEDED_BEFORE_PRODUCTION"],
                ),
                actor=actor,
            )

    def _ensure_launch_run_active(
        self,
        *,
        channel: ChannelWorkspace,
        policy: FirstChannelLaunchPolicyVersion,
        actor: ActorContext,
        preparation_started_on: date,
    ) -> LaunchRun:
        run = self.create_launch_run(
            data=LaunchRunCreate(
                launch_policy_version_id=policy.id,
                launch_key=f"{channel.key}-controlled-evidence-building-v{policy.policy_version}",
                preparation_started_on=preparation_started_on,
            ),
            actor=actor,
            channel_workspace_id=channel.id,
        )
        if run.state == "PREPARING":
            run = LaunchRunService(self.session).transition(
                launch_run_id=run.id,
                data=LaunchRunTransition(
                    target_state="READY_TO_LAUNCH",
                    reason_codes=["CHANNEL_RUNTIME_RUNWAY_AUTHORITY_READY"],
                ),
                actor=actor,
            )
        if run.state != "ACTIVE":
            run = LaunchRunService(self.session).transition(
                launch_run_id=run.id,
                data=LaunchRunTransition(
                    target_state="ACTIVE",
                    reason_codes=["CHANNEL_RUNTIME_BOOTSTRAP_ACTIVATED"],
                ),
                actor=actor,
            )
        return run

    def _require_persisted_actor(self, actor: ActorContext) -> None:
        if actor.actor_type != ActorType.HUMAN_USER or actor.operator_user_id is None:
            raise ForbiddenError("CHANNEL_RUNTIME_PERSISTED_ACTOR_REQUIRED")
        user = self.session.get(User, actor.actor_id)
        operator = self.session.get(OperatorUser, actor.operator_user_id)
        if (
            user is None
            or str(user.status).lower() != "active"
            or operator is None
            or str(operator.status).upper() != "ACTIVE"
            or operator.canonical_user_id != user.id
        ):
            raise ForbiddenError("CHANNEL_RUNTIME_PERSISTED_ACTOR_REQUIRED")

    @staticmethod
    def _require_exact_scoped_identity(
        *, channel: ChannelWorkspace, snapshot: CompiledChannelPolicySnapshot
    ) -> None:
        scoped_payload = (snapshot.compiled_payload or {}).get("channel_scoped_policy")
        if not isinstance(scoped_payload, dict):
            raise ValidationFailureError("CHANNEL_SCOPED_POLICY_REQUIRED")
        try:
            policy = ChannelScopedPolicy.model_validate(scoped_payload)
        except Exception as exc:
            raise ValidationFailureError("CHANNEL_SCOPED_POLICY_INVALID") from exc
        identity = policy.channel_identity_policy
        if (
            identity.channel_key != channel.key
            or identity.primary_market != (channel.target_market or channel.primary_region)
            or identity.content_language != channel.primary_language
            or identity.primary_platform != "YouTube"
            or "long-form" not in identity.primary_format.lower()
            or policy.media_production_profile.final_render_authority
            != "native_ffmpeg_renderer"
            or not policy.publish_policy.youtube_private_stage_required
        ):
            raise ValidationFailureError("CHANNEL_RUNTIME_SCOPE_MISMATCH")


def _source_contract(policies: Any) -> dict[str, Any]:
    if not isinstance(policies, dict):
        return {}
    for key in ("channel_contract", "channel_contract_json", "m12_2p_channel_contract"):
        value = policies.get(key)
        if isinstance(value, dict):
            return deepcopy(value)
    return {}


def _clean_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _clean_value(child)
        for key, child in value.items()
        if not _is_removed_short_value(key) and not _is_removed_short_value(child)
    }


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _clean_mapping(value)
    if isinstance(value, list):
        return [_clean_value(item) for item in value if not _is_removed_short_value(item)]
    return deepcopy(value)


def _clean_list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip() and not _is_removed_short_value(item)]


def _is_removed_short_value(value: Any) -> bool:
    token = "".join(char for char in str(value).lower() if char.isalnum())
    return token in {
        "shorts",
        "youtubeshorts",
        "shortform",
        "shorts916",
        "longformandshorts",
    }


def _long_form_policy(source: Any) -> dict[str, Any]:
    raw = _clean_mapping(source)
    long_form = _clean_mapping(raw.get("long_form"))
    long_form["enabled"] = True
    long_form.setdefault("target_duration_minutes", {"min": 6, "max": 12})
    long_form.setdefault("chapters_required", True)
    return {"long_form": long_form}


def _long_form_strategy(source: Any) -> dict[str, Any]:
    raw = _clean_mapping(source)
    duration = raw.get("duration_contract")
    if not isinstance(duration, dict):
        duration = {
            "minimum_duration_ms": 360000,
            "target_duration_ms": 540000,
            "maximum_duration_ms": 720000,
            "duration_contract_version": "channel-duration-contract.v2",
        }
    return {
        **raw,
        "long_form": {
            "enabled": True,
            "target_duration_minutes": {"min": 6, "max": 12},
            "chapters_required": True,
        },
        "duration_contract": duration,
        "duration_contracts": {"LONG_FORM": deepcopy(duration)},
    }


def _channel_policy_without_legacy_human_gates(
    value: Any, *, channel_key: str
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw = deepcopy(value)
    if raw.get("channel_key") not in {None, channel_key}:
        raise ValidationFailureError("CHANNEL_POLICY_KEY_MISMATCH")
    raw["channel_key"] = channel_key
    raw["policy_status"] = "APPROVED"
    raw["approval_ref"] = f"channel-runtime-bootstrap://{channel_key}"
    voice = _clean_mapping(raw.get("voice_policy"))
    voice["unavailable_behavior"] = "BLOCK_EXTERNAL_FAILURE"
    voice["retry_requires_new_approval"] = False
    raw["voice_policy"] = voice
    provider_usage = _clean_mapping(raw.get("provider_usage_policy"))
    elevenlabs = _clean_mapping(provider_usage.get("elevenlabs"))
    elevenlabs["controlled_retry_requires_new_approval"] = False
    provider_usage["elevenlabs"] = elevenlabs
    raw["provider_usage_policy"] = provider_usage
    budget = _clean_mapping(raw.get("budget_policy"))
    budget["cost_overrun_review_required"] = False
    raw["budget_policy"] = budget
    freeze = _clean_mapping(raw.get("market_package_freeze_policy"))
    if freeze:
        freeze["exact_package_human_approval_required"] = False
        freeze["post_approval_integrity_required"] = False
        source_preconditions = (
            freeze.get("required_preconditions")
            if isinstance(freeze.get("required_preconditions"), list)
            else []
        )
        preconditions = [
            str(item)
            for item in source_preconditions
            if "human" not in str(item).lower() and "approval" not in str(item).lower()
        ]
        for required in ("AutomatedCreativeQC.PASS", "PackageIntegrity.PASS"):
            if required not in preconditions:
                preconditions.append(required)
        freeze["required_preconditions"] = preconditions
        raw["market_package_freeze_policy"] = freeze
    return ChannelScopedPolicy.model_validate(raw).model_dump(mode="json")
