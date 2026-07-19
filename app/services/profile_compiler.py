import uuid
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import (
    CapabilityMatrix,
    ChannelProfileCompileResult,
    ChannelProfileInput,
    CompiledChannelPolicyPayload,
    NicheProfileTemplate,
    ProfileCompilerPolicy,
)
from app.contracts.channel_policy import (
    CapabilityEvaluation,
    ChannelVisualSourcePolicyBinding,
    ChannelScopedPolicy,
    CompilerInputManifest,
    GeminiImageUsagePolicy,
    LaunchRestrictions,
    NativeRenderPolicySnapshot,
    PolicyRef,
    PolicySnapshotRefs,
)
from app.contracts.visual_routing import NicheVisualSourceProfile, VisualSourceRoute
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelProfileCompileRun,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    FormatIdentityContract,
)
from app.services.config_registry import LoadedCatalog, canonical_json, content_hash
from app.services.config_registry import ConfigRegistryService
from app.services.channel_contract import build_channel_contract, reject_legacy_provider_budget_fields
from app.services.creative_quality_policy import CreativeQualityPolicyCatalog


CH1_FLEX_V2_MASTER_APPROVAL_REF = (
    "operator-approval://ch1-flex-v2/"
    "small-team-ai/master-prompt-2026-07-19"
)


@dataclass(frozen=True)
class LoadedM1Catalogs:
    template_catalog: LoadedCatalog
    template: NicheProfileTemplate
    capability_catalog: LoadedCatalog
    capability_matrix: CapabilityMatrix
    compiler_policy_catalog: LoadedCatalog
    compiler_policy: ProfileCompilerPolicy


class ChannelProfileCompiler:
    def __init__(self, session: Session, config_dir: str | Path = "config"):
        self.session = session
        self.config_dir = Path(config_dir)

    def profile_input_from_template(self, template_key: str) -> tuple[ChannelProfileInput, LoadedM1Catalogs]:
        catalogs = self.load_catalogs(template_key)
        template = catalogs.template
        profile_input = ChannelProfileInput(
            template_key=template.template_key,
            template_version=template.template_version,
            display_name=template.display_name,
            target_market=template.target_market,
            audience_segment=template.audience_segment,
            monetization_model=template.monetization_model,
            format_strategy=template.format_strategy,
            risk_tolerance=template.risk_tolerance,
            media_style=template.media_style,
            voice_style=template.voice_style,
            evidence_requirement=template.evidence_requirement,
            platform_strategy=template.platform_strategy,
            human_review_strictness=template.human_review_strictness,
            content_pillars=template.default_content_pillars,
            series_plan=template.default_series_plan,
            initial_content_runway=template.default_runway,
            policies=template.default_policies,
        )
        return profile_input, catalogs

    def build_ch1_flex_v2_profile_input(
        self,
        *,
        active_profile_input: ChannelProfileInput | dict[str, Any],
        approval_ref: str,
    ) -> ChannelProfileInput:
        """Copy the effective v1 input and add only the approved CH1-FLEX v2 overlay."""

        parsed = ChannelProfileInput.model_validate(active_profile_input)
        if parsed.channel_policy is None:
            raise ValidationFailureError(
                "effective active channel policy is required for CH1-FLEX v2"
            )
        payload = deepcopy(parsed.model_dump(mode="json"))
        payload["channel_policy"] = self.build_ch1_flex_v2_policy(
            active_policy=parsed.channel_policy,
            approval_ref=approval_ref,
        ).model_dump(mode="json")
        return ChannelProfileInput.model_validate(payload)

    def build_ch1_flex_v2_policy(
        self,
        *,
        active_policy: ChannelScopedPolicy | dict[str, Any],
        approval_ref: str,
    ) -> ChannelScopedPolicy:
        """Build a deterministic v2 policy overlay from immutable evidence."""

        policy = ChannelScopedPolicy.model_validate(active_policy)
        if (
            policy.channel_key != "small-team-ai"
            or approval_ref != CH1_FLEX_V2_MASTER_APPROVAL_REF
        ):
            raise ValidationFailureError("CH1-FLEX v2 requires its exact scoped operator approval")
        binding = self._qualified_visual_source_binding()
        raw = deepcopy(policy.model_dump(mode="json"))
        raw["policy_version"] = f"{policy.channel_key}.channel-policy.v2"
        raw["policy_status"] = "APPROVED"
        raw["approval_ref"] = approval_ref
        raw["visual_source_policy_binding"] = binding.model_dump(mode="json")
        raw["provider_usage_policy"]["google_gemini_image"] = (
            GeminiImageUsagePolicy().model_dump(mode="json")
        )
        required = list(raw["capability_requirements"]["required"])
        for capability in (
            "visual_source_routing",
            "google_gemini_image_planning",
            "image_visual_quality_control",
            "drive_verified_image_canary",
        ):
            if capability not in required:
                required.append(capability)
        raw["capability_requirements"]["required"] = required
        derivation_refs = list(raw["budget_policy"]["derivation_refs"])
        for ref in (
            binding.gemini_image_model_catalog.ref,
            binding.image_canary_v3_qualification.ref,
            approval_ref,
        ):
            if ref not in derivation_refs:
                derivation_refs.append(ref)
        raw["budget_policy"]["derivation_refs"] = derivation_refs
        return ChannelScopedPolicy.model_validate(raw)

    def compile(
        self,
        *,
        profile_version_id: uuid.UUID,
        correlation_id: str,
    ) -> ChannelProfileCompileResult:
        profile_version = self.session.get(ChannelProfileVersion, profile_version_id)
        if profile_version is None:
            raise KeyError(f"profile version not found: {profile_version_id}")
        profile_input = ChannelProfileInput.model_validate(profile_version.profile_input)
        reject_legacy_provider_budget_fields(profile_input.model_dump(mode="json"))
        catalogs = self.load_catalogs(profile_input.template_key)
        run = ChannelProfileCompileRun(
            channel_profile_version_id=profile_version.id,
            compiler_version=catalogs.compiler_policy.compiler_version,
            capability_matrix_version=catalogs.capability_catalog.catalog_version,
            input_hash=profile_version.profile_input_hash,
            status="started",
            diagnostics={},
            correlation_id=correlation_id,
        )
        self.session.add(run)
        self.session.flush()
        try:
            channel = self.session.get(ChannelWorkspace, profile_version.channel_workspace_id)
            payload, output_hash = self.compile_from_input(
                profile_input=profile_input,
                template=catalogs.template,
                capability_matrix=catalogs.capability_matrix,
                compiler_policy=catalogs.compiler_policy,
                channel=channel,
                profile_input_hash_override=profile_version.profile_input_hash,
            )
            if profile_version.status == "active" and channel is not None and channel.active_policy_snapshot_id:
                active_snapshot = self.session.get(
                    CompiledChannelPolicySnapshot,
                    channel.active_policy_snapshot_id,
                )
                if (
                    active_snapshot is not None
                    and (active_snapshot.compiled_payload or {}).get("channel_scoped_policy") is not None
                    and active_snapshot.content_hash != output_hash
                ):
                    raise ValidationFailureError(
                        "active channel profile is immutable; create a new draft version"
                    )
            snapshot = self._get_or_create_snapshot(
                profile_version=profile_version,
                run=run,
                catalogs=catalogs,
                payload=payload,
                output_hash=output_hash,
            )
            run.output_hash = output_hash
            run.status = "succeeded"
            run.completed_at = utc_now()
            run.diagnostics = self._catalog_diagnostics(catalogs)
            if profile_version.status == "draft":
                profile_version.status = "compiled"
            self.session.flush()
            return ChannelProfileCompileResult(
                compile_run_id=run.id,
                snapshot_id=snapshot.id,
                content_hash=snapshot.content_hash,
                profile_input_hash=snapshot.profile_input_hash,
                compiler_version=snapshot.compiler_version,
                capability_matrix_version=snapshot.capability_matrix_version,
                source_template_version=catalogs.template_catalog.catalog_version,
                source_template_catalog_hash=catalogs.template_catalog.content_hash,
                capability_matrix_hash=catalogs.capability_catalog.content_hash,
                profile_compiler_policy_hash=catalogs.compiler_policy_catalog.content_hash,
            )
        except Exception as exc:
            run.status = "failed"
            run.completed_at = utc_now()
            run.diagnostics = {"error": str(exc), **self._catalog_diagnostics(catalogs)}
            self.session.flush()
            raise

    def compile_from_input(
        self,
        *,
        profile_input: ChannelProfileInput,
        template: NicheProfileTemplate,
        capability_matrix: CapabilityMatrix,
        compiler_policy: ProfileCompilerPolicy,
        channel: ChannelWorkspace | None = None,
        profile_input_hash_override: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        reject_legacy_provider_budget_fields(profile_input.model_dump(mode="json"))
        if not capability_matrix.profile_compiler_available:
            raise ValidationFailureError("profile compiler capability is unavailable")
        if not capability_matrix.policy_snapshot_available:
            raise ValidationFailureError("policy snapshot capability is unavailable")
        if profile_input.template_key not in compiler_policy.allowed_template_keys:
            raise ValidationFailureError(f"unsupported template: {profile_input.template_key}")
        if profile_input.template_key != template.template_key:
            raise ValidationFailureError("profile input template does not match loaded template")
        if profile_input.audience_segment not in compiler_policy.allowed_audience_segments:
            raise ValidationFailureError(f"unsupported audience segment: {profile_input.audience_segment}")
        if profile_input.risk_tolerance not in compiler_policy.allowed_risk_tolerance:
            raise ValidationFailureError(f"unsupported risk tolerance: {profile_input.risk_tolerance}")
        legacy_payload = {
            "channel_constitution": {
                "promise": f"Practical, evidence-aware {profile_input.display_name} content.",
                "audience": profile_input.audience_segment,
                "boundaries": [profile_input.policies.get("safety", "avoid unsupported claims")],
            },
            "operating_blueprint": {
                "target_market": profile_input.target_market,
                "platform_strategy": profile_input.platform_strategy,
                "human_review_strictness": profile_input.human_review_strictness,
                "risk_tolerance": profile_input.risk_tolerance,
            },
            "content_pillars": profile_input.content_pillars,
            "series_plan": profile_input.series_plan,
            "editorial_calendar_defaults": {
                "planning_unit": "weekly",
                "long_form_minutes": profile_input.format_strategy.get("long_form_minutes"),
                "shorts_role": profile_input.format_strategy.get("shorts_role"),
            },
            "initial_content_runway": profile_input.initial_content_runway,
            "default_playbook": {
                "format_strategy": profile_input.format_strategy,
                "media_style": profile_input.media_style,
            },
            "render_policy": {
                "capcut_prototype_viewer_only": True,
                "production_renderer_planned": "ffmpeg",
                "transcription_pilot": "faster_whisper_local",
                "ai_video_mode": "manual_external",
                "visual_plan_required": True,
            },
            "gate_policy": {
                "claim_review": profile_input.policies.get("review"),
                "safety": profile_input.policies.get("safety"),
            },
            "voice_policy": profile_input.voice_style,
            "evidence_policy": profile_input.evidence_requirement,
            "monetization_policy": profile_input.monetization_model,
            "kpi_profile": {
                "primary": "qualified attention",
                "secondary": ["watch_time", "affiliate_intent", "returning_viewers"],
            },
            "editorial_promise": "Calm, practical explanations with clear evidence boundaries.",
            "distinctiveness_profile": {
                "angle": "operator-grade workflows over hype",
                "visual_bias": profile_input.media_style.get("visual_bias", []),
            },
            "format_bible": {
                "long_form": profile_input.format_strategy,
                "voice": profile_input.voice_style,
            },
            "capability_status": self._capability_status(capability_matrix),
        }
        channel_contract = build_channel_contract(profile_input=profile_input.model_dump(mode="json"), channel=channel)
        creative_quality_policies = self._creative_quality_policies(channel_key=channel.key if channel else None)
        channel_policy_payload = self._channel_scoped_policy_payload(
            channel=channel,
            policy_override=profile_input.channel_policy,
            profile_input_hash=profile_input_hash_override or content_hash(profile_input.model_dump(mode="json")),
            creative_quality_policies=creative_quality_policies,
        )
        compiled_policy_snapshot_json = {
            "schema_version": "m12.2p.channel_policy_snapshot.v1",
            "snapshot_source": "ChannelProfileCompiler",
            "channel_contract_status": channel_contract["contract_status"],
            "missing_fields": channel_contract["missing_fields"],
            "contradiction_reasons": channel_contract["contradiction_reasons"],
            "market_locale": channel_contract["market_locale"],
            "legacy_policy_sections": legacy_payload,
            "creative_quality_policies": creative_quality_policies,
        }
        payload = {
            **legacy_payload,
            "channel_contract_json": channel_contract,
            "compiled_policy_snapshot_json": compiled_policy_snapshot_json,
            "creative_quality_policies": creative_quality_policies,
            "contract_status": channel_contract["contract_status"],
            "missing_fields": channel_contract["missing_fields"],
            "contradiction_reasons": channel_contract["contradiction_reasons"],
            "activation_required": channel_contract["contract_status"] != "COMPLETE",
        }
        if channel_policy_payload is not None:
            payload.update(channel_policy_payload)
            scoped_policy = channel_policy_payload["channel_scoped_policy"]
            visual_binding = scoped_policy.get("visual_source_policy_binding") or {}
            if (
                visual_binding.get("schema_version")
                == "ch1-flex.visual-source-policy-binding.v2"
            ):
                provider_policy = scoped_policy.get("provider_usage_policy") or {}
                pexels_policy = provider_policy.get("pexels") or {}
                threshold = pexels_policy.get("semantic_fit_threshold")
                if (
                    isinstance(threshold, bool)
                    or not isinstance(threshold, (int, float))
                    or not 0 < float(threshold) <= 1
                ):
                    raise ValidationFailureError(
                        "NICH1_APPROVED_SEMANTIC_FIT_THRESHOLD_MISSING"
                    )
                provider_ref = channel_policy_payload["snapshot_refs"][
                    "provider_usage_policy"
                ]
                payload["gate_policy"] = {
                    **payload["gate_policy"],
                    "channel_fit_threshold": float(threshold),
                    "channel_fit_threshold_authority": {
                        "ref": provider_ref["ref"]
                        + "#pexels.semantic_fit_threshold",
                        "version": provider_ref["version"],
                        "content_hash": provider_ref["content_hash"],
                        "derivation": "REUSE_APPROVED_SEMANTIC_FIT_THRESHOLD",
                    },
                }
                payload["compiler_decision_log"].append(
                    {
                        "decision": "NICH1_CHANNEL_FIT_THRESHOLD_AUTHORITY",
                        "result": "PASS",
                        "threshold": float(threshold),
                        "authority_ref": payload["gate_policy"][
                            "channel_fit_threshold_authority"
                        ]["ref"],
                    }
                )
            payload["activation_required"] = bool(
                payload["activation_required"]
                or channel_policy_payload["capability_evaluation"]["status"] != "PASS"
                or channel_policy_payload["channel_scoped_policy"]["policy_status"] != "APPROVED"
            )
        missing = sorted(set(compiler_policy.required_output_sections) - set(payload))
        if missing:
            raise ValidationFailureError(f"compiled payload missing sections: {missing}")
        parsed = CompiledChannelPolicyPayload.model_validate(payload)
        parsed_payload = parsed.model_dump(mode="json")
        return parsed_payload, content_hash(parsed_payload)

    def _channel_scoped_policy_payload(
        self,
        *,
        channel: ChannelWorkspace | None,
        policy_override: ChannelScopedPolicy | None,
        profile_input_hash: str,
        creative_quality_policies: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if channel is None:
            return None
        if policy_override is not None:
            policy = policy_override
            if policy.channel_key != channel.key:
                raise ValidationFailureError("channel policy override scope mismatch")
            catalog_ref = f"profile-input://{channel.key}/{policy.policy_version}"
            catalog_hash = content_hash(policy.model_dump(mode="json"))
        else:
            loaded = ConfigRegistryService(self.session).validate_catalog(
                self.config_dir / "channel_scoped_policy_catalog.yaml"
            )
            selected = next(
                (item for item in loaded.content["items"] if item.get("channel_key") == channel.key),
                None,
            )
            if selected is None:
                return None
            policy = ChannelScopedPolicy.model_validate(selected)
            catalog_ref = f"config://channel_scoped_policy_catalog/{loaded.catalog_version}#{policy.channel_key}"
            catalog_hash = loaded.content_hash
        return self.compile_channel_policy_blocks(
            policy=policy,
            creative_quality_policies=creative_quality_policies,
            profile_input_hash=profile_input_hash,
            channel_policy_catalog_ref=catalog_ref,
            channel_policy_catalog_hash=catalog_hash,
            format_contract_evidence=self._format_contract_evidence(channel=channel, policy=policy),
        )

    def compile_channel_policy_blocks(
        self,
        *,
        policy: ChannelScopedPolicy,
        creative_quality_policies: dict[str, Any] | None,
        profile_input_hash: str,
        channel_policy_catalog_ref: str,
        channel_policy_catalog_hash: str,
        format_contract_evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Compile any typed channel policy through one branch-free deterministic path."""
        visual_binding = self._validated_visual_source_binding(policy)
        blockers: list[str] = []
        if creative_quality_policies is None:
            blockers.append("CREATIVE_QUALITY_POLICY_MISSING")
            creative_quality_policies = {
                "policy_ref": policy.creative_quality_binding.policy_ref,
                "policy_version": policy.creative_quality_binding.policy_version,
                "catalog_hash": "0" * 64,
            }
        if creative_quality_policies.get("policy_ref") != policy.creative_quality_binding.policy_ref:
            blockers.append("CREATIVE_QUALITY_POLICY_REF_MISMATCH")
        if creative_quality_policies.get("policy_version") != policy.creative_quality_binding.policy_version:
            blockers.append("CREATIVE_QUALITY_POLICY_VERSION_MISMATCH")
        creative_hash = str(creative_quality_policies.get("catalog_hash") or "")
        if len(creative_hash) != 64:
            blockers.append("CREATIVE_QUALITY_POLICY_HASH_MISSING")
            creative_hash = "0" * 64

        if format_contract_evidence is None:
            blockers.append("FORMAT_IDENTITY_CONTRACT_MISSING")
        elif format_contract_evidence.get("content_hash") != policy.format_identity_contract.content_hash:
            blockers.append("FORMAT_IDENTITY_CONTRACT_HASH_MISMATCH")
        elif format_contract_evidence.get("status") != "APPROVED":
            blockers.append("FORMAT_IDENTITY_CONTRACT_NOT_APPROVED")

        provider_dump = policy.provider_usage_policy.model_dump(mode="json")
        budget_dump = policy.budget_policy.model_dump(mode="json")
        publish_dump = policy.publish_policy.model_dump(mode="json")
        caption_hash = content_hash(
            {
                "caption_style_policy": creative_quality_policies.get("caption_style_policy"),
                "caption_sync_policy": creative_quality_policies.get("caption_sync_policy"),
            }
        )
        native_core = {
            "final_render_authority": "native_ffmpeg_renderer",
            "temporal_authority": "CanonicalMediaTimeline",
            "strict_plan_requires_final_audio": True,
            "caption_policy_ref": f"{policy.creative_quality_binding.policy_ref}#caption",
            "caption_policy_hash": caption_hash,
        }
        native_hash = content_hash(native_core)
        native_snapshot = NativeRenderPolicySnapshot(
            policy_ref=f"channel-policy://{policy.channel_key}/{policy.policy_version}/native-render",
            policy_hash=native_hash,
            **native_core,
        )
        provider_hash = content_hash(provider_dump)
        budget_hash = content_hash(budget_dump)
        publish_hash = content_hash(publish_dump)
        capability = CapabilityEvaluation(
            status="BLOCKED" if blockers else "PASS",
            required=policy.capability_requirements.required,
            available=[
                item for item in policy.capability_requirements.required
                if item not in ({"format_identity_contract", "creative_quality_policy"} if blockers else set())
            ],
            blockers=sorted(set(blockers)),
        )
        input_manifest = CompilerInputManifest(
            precedence=[
                "hard_global_company_policy",
                "approved_channel_contract",
                "approved_channel_profile_version",
                "category_policy",
                "series_policy",
                "episode_project_brief",
                "approved_operator_overrides",
            ],
            profile_input_hash=profile_input_hash,
            channel_policy_catalog_ref=channel_policy_catalog_ref,
            channel_policy_catalog_hash=channel_policy_catalog_hash,
            creative_quality_policy_ref=policy.creative_quality_binding.policy_ref,
            creative_quality_policy_hash=creative_hash,
            format_identity_contract_ref=policy.format_identity_contract.ref,
            format_identity_contract_hash=policy.format_identity_contract.content_hash,
        )
        refs = PolicySnapshotRefs(
            native_render_policy=PolicyRef(
                ref=native_snapshot.policy_ref,
                version=policy.policy_version,
                content_hash=native_hash,
            ),
            creative_quality_policy=PolicyRef(
                ref=policy.creative_quality_binding.policy_ref,
                version=policy.creative_quality_binding.policy_version,
                content_hash=creative_hash,
            ),
            provider_usage_policy=PolicyRef(
                ref=f"channel-policy://{policy.channel_key}/{policy.policy_version}/provider-usage",
                version=policy.policy_version,
                content_hash=provider_hash,
            ),
            budget_policy=PolicyRef(
                ref=f"channel-policy://{policy.channel_key}/{policy.policy_version}/budget",
                version=policy.policy_version,
                content_hash=budget_hash,
            ),
            publish_policy=PolicyRef(
                ref=f"channel-policy://{policy.channel_key}/{policy.policy_version}/publish",
                version=policy.policy_version,
                content_hash=publish_hash,
            ),
            format_identity_contract=PolicyRef(
                ref=policy.format_identity_contract.ref,
                version=policy.format_identity_contract.version,
                content_hash=policy.format_identity_contract.content_hash,
            ),
            visual_source_routing_policy=(
                visual_binding.visual_source_routing_policy if visual_binding else None
            ),
            visual_source_routing_catalog=(
                visual_binding.visual_source_routing_catalog if visual_binding else None
            ),
            gemini_image_provider_registry=(
                visual_binding.gemini_image_provider_registry if visual_binding else None
            ),
            gemini_image_model_catalog=(
                visual_binding.gemini_image_model_catalog if visual_binding else None
            ),
            image_visual_quality_control=(
                visual_binding.image_visual_quality_control if visual_binding else None
            ),
            image_canary_v3_qualification=(
                visual_binding.image_canary_v3_qualification if visual_binding else None
            ),
            drive_verified_canary_receipt=(
                visual_binding.drive_verified_canary_receipt if visual_binding else None
            ),
        )
        decision_log = [
            {"decision": "HARD_POLICY_PRECEDENCE_LOCKED", "result": "PASS"},
            {"decision": "CHANNEL_POLICY_SCOPE", "result": policy.channel_key},
            {"decision": "STRATEGY_RANGES_PLANNING_ONLY", "result": "PASS"},
            {"decision": "NO_MINIMUM_PROVIDER_QUOTAS", "result": "PASS"},
            {"decision": "CAPABILITY_EVALUATION", "result": capability.status},
        ]
        if visual_binding is not None:
            decision_log.extend(
                [
                    {
                        "decision": "NICHE_VISUAL_SOURCE_PROFILE",
                        "result": visual_binding.niche_visual_source_profile.value,
                    },
                    {
                        "decision": "VISUAL_QUALIFICATION_BINDINGS",
                        "result": "PASS",
                    },
                    {
                        "decision": "PEXELS_FAILURE_OPENS_AI",
                        "result": "FORBIDDEN",
                    },
                    {
                        "decision": "GEMINI_IMAGE_PROVIDER_EXECUTION_DEFAULT",
                        "result": "DISABLED",
                    },
                ]
            )
        return {
            "channel_scoped_policy": policy.model_dump(mode="json"),
            "native_render_policy_snapshot": native_snapshot.model_dump(mode="json"),
            "provider_usage_policy_snapshot": {
                "policy": provider_dump,
                "content_hash": provider_hash,
                "scope": policy.channel_key,
            },
            "creative_quality_policy_snapshot": {
                **creative_quality_policies,
                "content_hash": creative_hash,
                "source_run_id": policy.creative_quality_binding.source_run_id,
            },
            "publish_policy_snapshot": {
                "policy": publish_dump,
                "content_hash": publish_hash,
                "scope": policy.channel_key,
            },
            "capability_evaluation": capability.model_dump(mode="json"),
            "launch_restrictions": LaunchRestrictions().model_dump(mode="json"),
            "compiler_input_manifest": input_manifest.model_dump(mode="json"),
            "compiler_decision_log": decision_log,
            "snapshot_refs": refs.model_dump(mode="json"),
        }

    def _format_contract_evidence(
        self,
        *,
        channel: ChannelWorkspace,
        policy: ChannelScopedPolicy,
    ) -> dict[str, Any] | None:
        contract = self.session.scalars(
            select(FormatIdentityContract)
            .where(
                FormatIdentityContract.channel_id == channel.id,
                FormatIdentityContract.status == "APPROVED",
            )
            .order_by(FormatIdentityContract.contract_version.desc())
        ).first()
        if contract is None:
            return None
        return {
            "id": str(contract.id),
            "status": contract.status,
            "content_hash": contract.content_hash,
            "expected_ref": policy.format_identity_contract.ref,
        }

    def _creative_quality_policies(self, *, channel_key: str | None) -> dict[str, Any] | None:
        """Compile immutable channel-scoped creative policy without service constants."""
        if not channel_key:
            return None
        loaded = ConfigRegistryService(self.session).validate_catalog(
            self.config_dir / "creative_quality_policy_catalog.yaml"
        )
        selected = next(
            (item for item in loaded.content["items"] if item.get("channel_key") == channel_key),
            None,
        )
        if selected is None:
            return None
        policy = CreativeQualityPolicyCatalog(
            self.config_dir / "creative_quality_policy_catalog.yaml"
        ).approved_snapshot(channel_key)
        if policy["catalog_hash"] != loaded.content_hash:
            raise ValidationFailureError("creative quality catalog hash mismatch")
        return {
            **policy,
            "channel_key": channel_key,
        }

    def _validated_visual_source_binding(
        self,
        policy: ChannelScopedPolicy,
    ) -> ChannelVisualSourcePolicyBinding | None:
        binding = policy.visual_source_policy_binding
        if binding is None:
            return None
        expected = self._qualified_visual_source_binding()
        if binding.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise ValidationFailureError(
                "CH1_FLEX_V2_VISUAL_QUALIFICATION_BINDING_MISMATCH"
            )
        image_policy = policy.provider_usage_policy.google_gemini_image
        if image_policy is None or (
            image_policy.model_dump(mode="json")
            != GeminiImageUsagePolicy().model_dump(mode="json")
        ):
            raise ValidationFailureError("CH1_FLEX_V2_GEMINI_IMAGE_POLICY_MISMATCH")
        return binding

    def _qualified_visual_source_binding(self) -> ChannelVisualSourcePolicyBinding:
        """Resolve and verify immutable qualification evidence without provider I/O."""

        # Imported lazily to keep the generic compiler module free of service-load cycles.
        from app.services.visual_source_routing import VisualSourceRoutingPolicyCatalog

        registry = ConfigRegistryService(self.session)
        visual_catalog_loaded = registry.validate_catalog(
            self.config_dir / "visual_source_routing_policy_catalog.yaml"
        )
        provider_registry_loaded = registry.validate_catalog(
            self.config_dir / "provider_registry_catalog.yaml"
        )
        image_catalog_loaded = registry.validate_catalog(
            self.config_dir / "google_gemini_image_model_price_catalog.yaml"
        )
        visual_catalog = VisualSourceRoutingPolicyCatalog(
            self.config_dir / "visual_source_routing_policy_catalog.yaml"
        )
        lifecycle = visual_catalog.typed_item.lifecycle
        if (
            lifecycle.activation_milestone != "CH1-FLEX_v2"
            or lifecycle.provider_execution_allowed is not False
        ):
            raise ValidationFailureError("CH1_FLEX_V2_VSR1_ACTIVATION_BOUNDARY_INVALID")

        provider_rows = [
            item
            for item in provider_registry_loaded.content["items"]
            if item.get("provider_key") == "google_gemini_image"
        ]
        if len(provider_rows) != 1:
            raise ValidationFailureError("CH1_FLEX_V2_GEMINI_IMAGE_PROVIDER_NOT_UNIQUE")
        provider_row = provider_rows[0]
        if (
            provider_row.get("status") != "ACTIVE"
            or provider_row.get("capability_blob", {}).get("capability")
            != "AI_IMAGE_GENERATION"
            or provider_row.get("policy_fit_blob", {}).get(
                "provider_fallback_allowed"
            )
            is not False
            or provider_row.get("policy_fit_blob", {}).get(
                "production_enabled_when_configured"
            )
            is not False
        ):
            raise ValidationFailureError("CH1_FLEX_V2_GEMINI_IMAGE_PROVIDER_POLICY_INVALID")

        default_image_rows = [
            item
            for item in image_catalog_loaded.content["items"]
            if item.get("is_default_route") is True
        ]
        if len(default_image_rows) != 1:
            raise ValidationFailureError("CH1_FLEX_V2_GEMINI_IMAGE_DEFAULT_NOT_UNIQUE")
        default_image = default_image_rows[0]
        if (
            default_image.get("model_id") != "gemini-3.1-flash-image"
            or default_image.get("size") != "2K"
            or default_image.get("aspect_ratio") != "16:9"
            or default_image.get("policy_state") != "ALLOWED"
        ):
            raise ValidationFailureError("CH1_FLEX_V2_GEMINI_IMAGE_DEFAULT_INVALID")

        vsr1, vsr1_hash = self._load_immutable_json_report("vsr1_summary.json")
        img1, img1_hash = self._load_immutable_json_report("img1_summary.json")
        vqc1, vqc1_hash = self._load_immutable_json_report("vqc1_summary.json")
        canary, canary_hash = self._load_immutable_json_report(
            "img_canary_v3_summary.json"
        )
        drive, _drive_summary_hash = self._load_immutable_json_report(
            "img_canary_v3_drive_closeout_summary.json"
        )
        del vsr1_hash, img1_hash, _drive_summary_hash
        if (
            vsr1.get("verdicts", {}).get("VSR1_FINAL") != "PASS"
            or img1.get("verdicts", {}).get("IMG1_FINAL") != "PASS"
            or vqc1.get("verdicts", {}).get("VQC1_FINAL") != "PASS"
        ):
            raise ValidationFailureError("CH1_FLEX_V2_FOUNDATION_QUALIFICATION_MISSING")
        canary_verdicts = canary.get("verdicts", {})
        canary_boundaries = canary.get("boundaries", {})
        drive_verdicts = drive.get("verdicts", {})
        if (
            canary_verdicts.get("IMG_CANARY_V3_FINAL") != "PASS"
            or canary_verdicts.get("IMG_CANARY_V3_HUMAN_REVIEW") != "PASS"
            or canary_verdicts.get("IMG_CANARY_V3_DRIVE_ARCHIVE") != "PASS"
            or canary_verdicts.get("ARCHIVE_VERIFIED_ON_DRIVE") is not True
            or canary_boundaries.get("PROCEED_TO_CH1_FLEX_V2") is not True
            or canary_boundaries.get("MR1_EXECUTION") != "ON_HOLD"
            or canary_boundaries.get("PROCEED_TO_MR1") is not False
            or drive.get("human_review", {}).get("decision") != "PASS"
            or drive.get("drive", {}).get("archive_verified") is not True
            or drive_verdicts.get("IMG_CANARY_V3_FINAL") != "PASS"
            or drive_verdicts.get("PROCEED_TO_CH1_FLEX_V2") is not True
            or drive_verdicts.get("MR1_EXECUTION") != "ON_HOLD"
            or drive_verdicts.get("PROCEED_TO_MR1") is not False
        ):
            raise ValidationFailureError("CH1_FLEX_V2_CANARY_QUALIFICATION_MISSING")
        canary_receipt_hash = str(drive.get("drive", {}).get("receipt_hash") or "")
        if (
            len(canary_receipt_hash) != 64
            or canary.get("drive", {}).get("drive_receipt_hash")
            != canary_receipt_hash
        ):
            raise ValidationFailureError("CH1_FLEX_V2_DRIVE_RECEIPT_BINDING_INVALID")

        run_id = str(canary.get("run", {}).get("run_id") or "")
        vqc_schema = str(vqc1.get("implementation", {}).get("schema_version") or "")
        canary_schema = str(canary.get("schema_version") or "")
        drive_schema = str(drive.get("schema_version") or "")
        if not all((run_id, vqc_schema, canary_schema, drive_schema)):
            raise ValidationFailureError("CH1_FLEX_V2_QUALIFICATION_VERSION_MISSING")

        return ChannelVisualSourcePolicyBinding(
            niche_visual_source_profile=NicheVisualSourceProfile.STOCK_ASSISTED,
            visual_source_routing_policy=PolicyRef(
                ref=visual_catalog.policy_ref,
                version=visual_catalog.policy_version,
                content_hash=visual_catalog.policy_hash,
            ),
            visual_source_routing_catalog=PolicyRef(
                ref=(
                    "config://visual_source_routing_policy_catalog/"
                    f"{visual_catalog_loaded.catalog_version}"
                ),
                version=visual_catalog_loaded.catalog_version,
                content_hash=visual_catalog_loaded.content_hash,
            ),
            gemini_image_provider_registry=PolicyRef(
                ref=(
                    "config://provider_registry_catalog/"
                    f"{provider_registry_loaded.catalog_version}#google_gemini_image"
                ),
                version=provider_registry_loaded.catalog_version,
                content_hash=provider_registry_loaded.content_hash,
            ),
            gemini_image_model_catalog=PolicyRef(
                ref=(
                    "config://google_gemini_image_model_price_catalog/"
                    f"{image_catalog_loaded.catalog_version}"
                ),
                version=image_catalog_loaded.catalog_version,
                content_hash=image_catalog_loaded.content_hash,
            ),
            image_visual_quality_control=PolicyRef(
                ref=f"report://vqc1_summary/{vqc_schema}",
                version=vqc_schema,
                content_hash=vqc1_hash,
            ),
            image_canary_v3_qualification=PolicyRef(
                ref=f"report://img_canary_v3_summary/{run_id}",
                version=canary_schema,
                content_hash=canary_hash,
            ),
            drive_verified_canary_receipt=PolicyRef(
                ref=f"drive-receipt://img-canary-v3/{run_id}",
                version=drive_schema,
                content_hash=canary_receipt_hash,
            ),
            allowed_source_routes=list(VisualSourceRoute),
        )

    def _load_immutable_json_report(self, filename: str) -> tuple[dict[str, Any], str]:
        path = self.config_dir.resolve().parent / "reports" / filename
        try:
            raw = path.read_bytes()
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailureError(
                f"CH1_FLEX_V2_QUALIFICATION_REPORT_INVALID:{filename}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValidationFailureError(
                f"CH1_FLEX_V2_QUALIFICATION_REPORT_INVALID:{filename}"
            )
        return payload, hashlib.sha256(raw).hexdigest()

    def load_catalogs(self, template_key: str) -> LoadedM1Catalogs:
        registry = ConfigRegistryService(self.session)
        template_catalog = registry.validate_catalog(self.config_dir / "niche_profile_templates.yaml")
        capability_catalog = registry.validate_catalog(self.config_dir / "capability_matrix.yaml")
        compiler_policy_catalog = registry.validate_catalog(self.config_dir / "profile_compiler_policy.yaml")
        template_item = self._find_item(template_catalog, "template_key", template_key)
        if template_item is None:
            raise ValidationFailureError(f"unsupported template: {template_key}")
        return LoadedM1Catalogs(
            template_catalog=template_catalog,
            template=NicheProfileTemplate.model_validate(template_item),
            capability_catalog=capability_catalog,
            capability_matrix=CapabilityMatrix.model_validate(capability_catalog.content["items"][0]),
            compiler_policy_catalog=compiler_policy_catalog,
            compiler_policy=ProfileCompilerPolicy.model_validate(compiler_policy_catalog.content["items"][0]),
        )

    def _get_or_create_snapshot(
        self,
        *,
        profile_version: ChannelProfileVersion,
        run: ChannelProfileCompileRun,
        catalogs: LoadedM1Catalogs,
        payload: dict[str, Any],
        output_hash: str,
    ) -> CompiledChannelPolicySnapshot:
        existing = self.session.scalars(
            select(CompiledChannelPolicySnapshot).where(
                CompiledChannelPolicySnapshot.channel_profile_version_id == profile_version.id,
                CompiledChannelPolicySnapshot.compiler_version
                == catalogs.compiler_policy.compiler_version,
                CompiledChannelPolicySnapshot.capability_matrix_version
                == catalogs.capability_catalog.catalog_version,
                CompiledChannelPolicySnapshot.profile_input_hash
                == profile_version.profile_input_hash,
                CompiledChannelPolicySnapshot.content_hash == output_hash,
            )
        ).one_or_none()
        if existing is not None:
            return existing
        next_version = (
            self.session.scalar(
                select(func.max(CompiledChannelPolicySnapshot.snapshot_version)).where(
                    CompiledChannelPolicySnapshot.channel_workspace_id == profile_version.channel_workspace_id
                )
            )
            or 0
        ) + 1
        snapshot = CompiledChannelPolicySnapshot(
            channel_workspace_id=profile_version.channel_workspace_id,
            channel_profile_version_id=profile_version.id,
            compile_run_id=run.id,
            snapshot_version=next_version,
            status="compiled",
            compiler_version=catalogs.compiler_policy.compiler_version,
            capability_matrix_version=catalogs.capability_catalog.catalog_version,
            compiled_payload=payload,
            content_hash=output_hash,
            profile_input_hash=profile_version.profile_input_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def _capability_status(self, capability_matrix: CapabilityMatrix) -> dict[str, Any]:
        return {
            "profile_compiler": "available" if capability_matrix.profile_compiler_available else "not_available_yet",
            "policy_snapshot": "available" if capability_matrix.policy_snapshot_available else "not_available_yet",
            "artifact_workflow": "available" if capability_matrix.artifact_workflow_available else "restricted_until_milestone",
            "media_pipeline": "available" if capability_matrix.media_pipeline_available else "restricted_until_milestone",
            "publish_pipeline": "available" if capability_matrix.publish_pipeline_available else "restricted_until_milestone",
            "analytics": "available" if capability_matrix.analytics_available else "restricted_until_milestone",
            "no_view_diagnostic": "available" if capability_matrix.no_view_diagnostic_available else "restricted_until_milestone",
            "envato_manual_asset_pilot_documented": capability_matrix.envato_manual_asset_pilot_documented,
            "ffmpeg_renderer_planned": capability_matrix.ffmpeg_renderer_planned,
        }

    def _catalog_diagnostics(self, catalogs: LoadedM1Catalogs) -> dict[str, str]:
        return {
            "template_catalog_version": catalogs.template_catalog.catalog_version,
            "template_catalog_hash": catalogs.template_catalog.content_hash,
            "capability_matrix_version": catalogs.capability_catalog.catalog_version,
            "capability_matrix_hash": catalogs.capability_catalog.content_hash,
            "compiler_policy_version": catalogs.compiler_policy_catalog.catalog_version,
            "compiler_policy_hash": catalogs.compiler_policy_catalog.content_hash,
            "canonical_json": canonical_json({"policy": catalogs.compiler_policy.model_dump(mode="json")}),
        }

    def _find_item(self, catalog: LoadedCatalog, key: str, value: str) -> dict[str, Any] | None:
        for item in catalog.content["items"]:
            if item.get(key) == value:
                return item
        return None
