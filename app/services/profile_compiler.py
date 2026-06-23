import uuid
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
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelProfileCompileRun,
    ChannelProfileVersion,
    CompiledChannelPolicySnapshot,
)
from app.services.config_registry import LoadedCatalog, canonical_json, content_hash
from app.services.config_registry import ConfigRegistryService


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
            payload, output_hash = self.compile_from_input(
                profile_input=profile_input,
                template=catalogs.template,
                capability_matrix=catalogs.capability_matrix,
                compiler_policy=catalogs.compiler_policy,
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
    ) -> tuple[dict[str, Any], str]:
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
        payload = {
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
        missing = sorted(set(compiler_policy.required_output_sections) - set(payload))
        if missing:
            raise ValidationFailureError(f"compiled payload missing sections: {missing}")
        parsed = CompiledChannelPolicyPayload.model_validate(payload)
        parsed_payload = parsed.model_dump(mode="json")
        return parsed_payload, content_hash(parsed_payload)

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
