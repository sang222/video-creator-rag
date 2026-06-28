from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.contracts.m12_2 import (
    FirstScriptedVideoPackageRead,
    FirstScriptedVideoPackageRequest,
    FirstScriptedVideoPackageReviewRead,
)
from app.contracts.m12_1 import PromptOutputValidationRequest, PromptRenderRequest
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    FirstScriptedVideoPackage,
    PromptAuditSnapshot,
    VideoProject,
)
from app.services.m10_1 import LLMRouterConfigLoader, LLMRouterService
from app.services.m12 import ProviderReadinessService
from app.services.m12_1 import PromptRegistryService


ROOT = Path(__file__).resolve().parents[2]
M12_2_REQUIRED_TAGS = ("m12-1-prompt-registry-contracts", "m12-1r-mock-dryrun-purge")
CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION = "Bổ sung hoặc compile lại ChannelProfileVersion trước khi chạy video package production."
NEEDS_CHANNEL_NEXT_ACTION = "Tạo channel và compile ChannelProfileVersion trước khi chạy M12.2."
NEEDS_RESEARCH_PACK_NEXT_ACTION = "Bổ sung research pack/source notes trước khi chạy video package production."
HUMAN_APPROVAL_REQUIRED = "Human final approval required before any media generation, upload, publish, or reupload."

VISUAL_SOURCE_ALLOWLIST = {
    "DIAGRAM",
    "CARD",
    "SCREENSHOT",
    "EXISTING_ASSET",
    "VEO_HERO_CANDIDATE_ONLY",
    "CREATOMATE_CARD_CANDIDATE_ONLY",
}


@dataclass(frozen=True)
class PackageAgentStep:
    agent_key: str
    router_lane: str
    artifact_key: str
    requested_task_type: str


PACKAGE_AGENT_CHAIN: tuple[PackageAgentStep, ...] = (
    PackageAgentStep("ChannelAuthorityAgent", "cheap_structured", "admission_decision", "json_schema_output"),
    PackageAgentStep("TopicIdeaScoringAgent", "cheap_structured", "topic_scores", "json_schema_output"),
    PackageAgentStep("ResearchPackSummarizer", "long_context_text", "research_notes", "long_context_synthesis"),
    PackageAgentStep("ScriptPlanningAgent", "long_context_text", "script_outline", "long_form_script"),
    PackageAgentStep("ScriptWriterAgent", "long_context_text", "narration_script", "long_form_script"),
    PackageAgentStep("PublishingMetadataAgent", "cheap_structured", "metadata_package", "metadata_generation"),
    PackageAgentStep("VisualPlanningAgent", "visual_creative_review", "visual_plan", "visual_plan_review"),
    PackageAgentStep("UploadCardCopyAgent", "cheap_structured", "upload_card_copy", "metadata_generation"),
    PackageAgentStep("GatekeeperSoftReviewAgent", "gatekeeper_soft_review", "gatekeeper_review", "policy_soft_review"),
)


def verify_m12_2_required_tags(repo_root: Path = ROOT) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "tag", "--list"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return {
            "status": "BLOCKED",
            "required_tags": list(M12_2_REQUIRED_TAGS),
            "missing_tags": list(M12_2_REQUIRED_TAGS),
            "error": str(exc),
        }
    tags = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    missing = [tag for tag in M12_2_REQUIRED_TAGS if tag not in tags]
    return {
        "status": "PASS" if not missing else "BLOCKED",
        "required_tags": list(M12_2_REQUIRED_TAGS),
        "missing_tags": missing,
    }


class FirstScriptedVideoPackageService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        prompt_registry: PromptRegistryService | None = None,
        llm_router: LLMRouterService | None = None,
        repo_root: Path = ROOT,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.prompt_registry = prompt_registry or PromptRegistryService(session)
        self.llm_router = llm_router or LLMRouterService(session)
        self.repo_root = repo_root

    def create(self, data: FirstScriptedVideoPackageRequest) -> FirstScriptedVideoPackageRead:
        channel = self.session.get(ChannelWorkspace, data.channel_id)
        if channel is None:
            raise ValidationFailureError(f"BLOCKED: NEEDS_CHANNEL_INIT. {NEEDS_CHANNEL_NEXT_ACTION}")

        preflight = verify_m12_2_required_tags(self.repo_root)
        if preflight["status"] != "PASS":
            return self._read(self._create_package(
                channel_id=channel.id,
                status="BLOCKED",
                artifacts={"preflight": preflight},
                limitations=["M12.2 preflight tags are required before production prompt activation."],
                next_action=f"Khôi phục hoặc tạo tag còn thiếu: {', '.join(preflight['missing_tags'])}.",
            ))

        readiness_snapshot = ProviderReadinessService(self.session, self.settings).run()
        snapshot = self._active_snapshot(channel)
        if snapshot is None:
            return self._read(self._create_package(
                channel_id=channel.id,
                status="BLOCKED",
                provider_readiness_snapshot_id=readiness_snapshot.id,
                artifacts={"preflight": {"reason_code": "CHANNEL_POLICY_SNAPSHOT_MISSING"}},
                limitations=["Thiếu active CompiledChannelPolicySnapshot nên không được render prompt production."],
                next_action=CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
            ))

        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        if profile_version is None:
            return self._read(self._create_package(
                channel_id=channel.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="BLOCKED",
                artifacts={"preflight": {"reason_code": "CHANNEL_PROFILE_VERSION_MISSING"}},
                limitations=["CompiledPolicySnapshot không còn ChannelProfileVersion hợp lệ."],
                next_action=CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
            ))

        video_project_id = self._validate_optional_project(data.video_project_id, channel_id=channel.id, snapshot_id=snapshot.id)
        flag_block = self._flag_block(data)
        if flag_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="BLOCKED",
                artifacts={"runtime_mode": flag_block},
                limitations=["Runtime mode M12.2 chưa cho phép production prompt activation an toàn."],
                next_action=flag_block["next_action"],
            ))

        channel_contract = (
            snapshot.compiled_payload.get("channel_contract_json")
            if isinstance(snapshot.compiled_payload, dict) and isinstance(snapshot.compiled_payload.get("channel_contract_json"), dict)
            else {}
        )
        contract_block = self._channel_contract_block(channel_contract, snapshot)
        if contract_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"channel_contract_review": contract_block},
                limitations=["Channel Contract chưa đủ để agent production suy luận an toàn."],
                next_action=CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
            ))

        if not data.topic:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"topic": {"status": "NEEDS_TOPIC"}},
                limitations=["Thiếu seed topic hoặc project topic; VCOS không tự bịa đề tài."],
                next_action="Bổ sung topic hoặc chọn VideoProject/candidate topic trước khi chạy M12.2.",
            ))

        if not (data.research_pack_text or data.research_pack_ref):
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"research_notes": {"status": "NEEDS_RESEARCH_PACK"}},
                limitations=["Thiếu research pack/source notes; ResearchPackSummarizer không được browse web hoặc bịa nguồn."],
                next_action=NEEDS_RESEARCH_PACK_NEXT_ACTION,
            ))

        llm_block = self._llm_readiness_block()
        if llm_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="NOT_CONFIGURED",
                artifacts={"llm_readiness": llm_block},
                limitations=["Real LLM package run chưa configured; không dùng mock fallback."],
                next_action=llm_block["next_action"],
            ))

        package_state = self._run_agent_chain(
            channel=channel,
            profile_version=profile_version,
            snapshot=snapshot,
            channel_contract=channel_contract,
            provider_readiness_snapshot_id=readiness_snapshot.id,
            data=data,
            video_project_id=video_project_id,
        )
        package = self._create_package(**package_state)
        return self._read(package)

    def get(self, package_id: uuid.UUID) -> FirstScriptedVideoPackageRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        return self._read(package)

    def review(self, package_id: uuid.UUID) -> FirstScriptedVideoPackageReviewRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        return FirstScriptedVideoPackageReviewRead(
            package_id=package.id,
            package_status=package.package_status,  # type: ignore[arg-type]
            channel_binding={
                "channel_id": str(package.channel_id),
                "channel_profile_version_id": str(package.channel_profile_version_id) if package.channel_profile_version_id else None,
                "compiled_policy_snapshot_id": str(package.compiled_policy_snapshot_id) if package.compiled_policy_snapshot_id else None,
            },
            human_review_checklist=package.artifacts.get("human_review_checklist", {}),
            agent_outputs={key: value for key, value in package.artifacts.items() if key not in {"human_review_checklist"}},
            prompt_snapshots={
                "prompt_render_run_refs": package.prompt_render_run_refs,
                "prompt_audit_snapshot_refs": package.prompt_audit_snapshot_refs,
                "agent_run_refs": package.agent_run_refs,
            },
            provider_readiness_snapshot_ref=package.provider_readiness_snapshot_id,
            limitations=package.limitations,
            next_action=package.next_action,
        )

    def _run_agent_chain(
        self,
        *,
        channel: ChannelWorkspace,
        profile_version: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        channel_contract: dict[str, Any],
        provider_readiness_snapshot_id: uuid.UUID,
        data: FirstScriptedVideoPackageRequest,
        video_project_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        artifacts: dict[str, Any] = {
            "channel_contract_snapshot_ref": {
                "channel_id": str(channel.id),
                "channel_profile_version_id": str(profile_version.id),
                "compiled_policy_snapshot_id": str(snapshot.id),
                "channel_contract_status": channel_contract.get("contract_status"),
                "compiled_policy_content_hash": snapshot.content_hash,
            }
        }
        agent_run_refs: list[dict[str, Any]] = []
        prompt_render_run_refs: list[str] = []
        prompt_audit_snapshot_refs: list[str] = []
        status = "READY_FOR_HUMAN_REVIEW"
        next_action = HUMAN_APPROVAL_REQUIRED
        limitations: list[str] = [
            "M12.2 chỉ tạo scripted video package; không render video, không TTS, không upload/publish.",
            "Google Drive chỉ là archive/storage; VCOS DB vẫn là source of truth.",
        ]

        for step in PACKAGE_AGENT_CHAIN:
            task_payload = self._task_payload(
                step=step,
                data=data,
                artifacts=artifacts,
                channel=channel,
                snapshot=snapshot,
            )
            render = self.prompt_registry.render_prompt(
                PromptRenderRequest(
                    agent_key=step.agent_key,
                    router_lane=step.router_lane,
                    task_payload=task_payload,
                    channel_profile_version_id=profile_version.id,
                    compiled_policy_snapshot_id=snapshot.id,
                    channel_contract_json=channel_contract,
                    compiled_policy_snapshot_json=snapshot.compiled_payload,
                    market_locale_context_json=channel_contract.get("market_locale"),
                    evidence_refs=self._evidence_refs(data),
                    artifact_refs=self._artifact_refs(artifacts),
                    input_payload_ref=f"first-scripted-video-package:{channel.id}:{step.agent_key}",
                )
            )
            prompt_render_run_refs.append(str(render.prompt_render_run_id))
            prompt_audit_snapshot_refs.append(str(render.prompt_audit_snapshot_id))
            if render.status != "OK":
                artifacts[step.artifact_key] = render.blocking_output.model_dump(mode="json") if render.blocking_output else None
                status = "REVIEW_REQUIRED" if render.status == "REVIEW_REQUIRED" else "BLOCKED"
                next_action = CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION
                break

            route = self.llm_router.route(
                lane_name=render.router_lane,
                messages=[message.model_dump() for message in render.rendered_messages],
                requested_task_type=step.requested_task_type,
                response_format="json",
                correlation_id=f"m12-2-first-video-package-{step.agent_key}",
            )
            if route.status != "SUCCESS":
                agent_run_refs.append(self._agent_ref(step, render, route=route, validation=None))
                artifacts[step.artifact_key] = {"status": route.status, "reason_codes": route.reason_codes}
                status = "NOT_CONFIGURED" if route.status == "SKIPPED" else "ERROR"
                next_action = "Cấu hình real LLMRouter/Ollama trước khi chạy package production."
                break

            raw_output: str | dict[str, Any] | None = route.structured_output or route.content
            validation = self.prompt_registry.validate_output(
                PromptOutputValidationRequest(
                    agent_key=step.agent_key,
                    raw_output=raw_output or "",
                    prompt_render_run_id=render.prompt_render_run_id,
                )
            )
            audit_id = self._latest_audit_id(
                render.prompt_render_run_id,
                provider_refs=[
                    {
                        "route_attempt_id": str(route.route_attempt_id),
                        "provider_attempt_id": str(route.provider_attempt_id) if route.provider_attempt_id else None,
                        "llm_run_snapshot_id": str(route.llm_run_snapshot_id) if route.llm_run_snapshot_id else None,
                    }
                ],
            )
            if audit_id is not None:
                prompt_audit_snapshot_refs.append(str(audit_id))
            agent_run_refs.append(self._agent_ref(step, render, route=route, validation=validation.model_dump(mode="json")))

            if validation.parsed_output is None or validation.status not in {"OK", "REVIEW_REQUIRED", "BLOCK"}:
                artifacts[step.artifact_key] = validation.validation_result
                status = "ERROR"
                next_action = "Sửa output schema/LLM response trước khi tiếp tục package."
                break
            if validation.status == "REVIEW_REQUIRED":
                artifacts[step.artifact_key] = {
                    "validation_result": validation.validation_result,
                    "parsed_output": validation.parsed_output,
                    "repair_attempts": validation.repair_attempts,
                }
                status = "REVIEW_REQUIRED"
                next_action = "Sửa output schema/LLM response trước khi tiếp tục package."
                break

            output = validation.parsed_output
            artifacts[step.artifact_key] = output.get("artifact") or {}
            envelope_status = output.get("status")
            if step.agent_key == "VisualPlanningAgent":
                visual_block = self._visual_plan_block(artifacts[step.artifact_key])
                if visual_block is not None:
                    artifacts["visual_plan_review"] = visual_block
                    status = "REVIEW_REQUIRED"
                    next_action = "Sửa visual plan để chỉ dùng nguồn DIAGRAM/CARD/SCREENSHOT/EXISTING_ASSET/VEO hoặc Creatomate candidate-only."
                    break
            if step.agent_key == "GatekeeperSoftReviewAgent":
                gatekeeper_result = self._gatekeeper_result(output)
                if gatekeeper_result == "BLOCK":
                    status = "BLOCKED"
                    next_action = output.get("next_action") or "Sửa rủi ro gatekeeper trước khi đưa package vào human review."
                elif gatekeeper_result == "REVIEW_REQUIRED":
                    status = "REVIEW_REQUIRED"
                    next_action = output.get("next_action") or HUMAN_APPROVAL_REQUIRED
                else:
                    status = "READY_FOR_HUMAN_REVIEW"
                    next_action = HUMAN_APPROVAL_REQUIRED
                break
            if envelope_status == "BLOCK":
                status = "BLOCKED"
                next_action = output.get("next_action") or "Agent upstream trả BLOCK; không tiếp tục downstream."
                break
            if envelope_status == "REVIEW_REQUIRED":
                status = "REVIEW_REQUIRED"
                next_action = output.get("next_action") or "Agent upstream cần human review; không tiếp tục downstream."
                break

        artifacts["human_review_checklist"] = self._human_review_checklist(artifacts, provider_readiness_snapshot_id)
        risk_summary = self._risk_summary(artifacts, status)
        return {
            "channel_id": channel.id,
            "video_project_id": video_project_id,
            "channel_profile_version_id": profile_version.id,
            "compiled_policy_snapshot_id": snapshot.id,
            "provider_readiness_snapshot_id": provider_readiness_snapshot_id,
            "status": status,
            "agent_run_refs": agent_run_refs,
            "prompt_render_run_refs": prompt_render_run_refs,
            "prompt_audit_snapshot_refs": sorted(set(prompt_audit_snapshot_refs)),
            "artifacts": artifacts,
            "limitations": limitations + risk_summary.get("limitations", []),
            "risk_limitations_summary": risk_summary,
            "next_action": next_action,
        }

    def _active_snapshot(self, channel: ChannelWorkspace) -> CompiledChannelPolicySnapshot | None:
        if channel.active_policy_snapshot_id is None:
            return None
        snapshot = self.session.get(CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id)
        if snapshot is None or snapshot.channel_workspace_id != channel.id:
            return None
        return snapshot if snapshot.status == "active" else None

    def _validate_optional_project(
        self,
        video_project_id: uuid.UUID | None,
        *,
        channel_id: uuid.UUID,
        snapshot_id: uuid.UUID,
    ) -> uuid.UUID | None:
        if video_project_id is None:
            return None
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.channel_workspace_id != channel_id:
            raise ValidationFailureError("video project does not belong to selected channel")
        if project.policy_snapshot_id != snapshot_id:
            raise ValidationFailureError("video project is not bound to the active compiled policy snapshot")
        return project.id

    def _channel_contract_block(
        self,
        channel_contract: dict[str, Any],
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any] | None:
        missing: list[str] = []
        if not channel_contract:
            missing.append("channel_contract_json")
        if not snapshot.compiled_payload:
            missing.append("compiled_policy_snapshot_json")
        contract_status = channel_contract.get("contract_status")
        market = channel_contract.get("market_locale") if isinstance(channel_contract.get("market_locale"), dict) else {}
        market_status = market.get("market_locale_context_status")
        if contract_status != "COMPLETE":
            missing.append(f"contract_status:{contract_status or 'MISSING'}")
        if market_status != "KNOWN":
            missing.append(f"market_locale_context_status:{market_status or 'MISSING'}")
        if not missing:
            return None
        return {
            "status": "REVIEW_REQUIRED",
            "reason_codes": ["CHANNEL_CONTRACT_INCOMPLETE"],
            "missing_or_invalid_fields": sorted(set(missing)),
            "next_action": CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
        }

    def _flag_block(self, data: FirstScriptedVideoPackageRequest) -> dict[str, Any] | None:
        failures: list[str] = []
        if not self.settings.production_prompt_activation_enabled:
            failures.append("VCOS_ENABLE_PRODUCTION_PROMPT_ACTIVATION")
        if not self.settings.media_provider_calls_disabled or not data.no_media:
            failures.append("VCOS_DISABLE_MEDIA_PROVIDER_CALLS")
        if not self.settings.upload_and_publish_disabled or not data.human_review_only:
            failures.append("VCOS_DISABLE_UPLOAD_AND_PUBLISH")
        if not self.settings.old_provider_smoke_disabled:
            failures.append("VCOS_DISABLE_OLD_PROVIDER_SMOKE")
        if not failures:
            return None
        return {
            "status": "BLOCKED",
            "missing_or_invalid_flags": failures,
            "next_action": "Bật đúng M12.2 activation flags và giữ media/upload/publish disabled.",
        }

    def _llm_readiness_block(self) -> dict[str, Any] | None:
        failures: list[str] = []
        if not self.settings.real_llm_package_run_enabled:
            failures.append("VCOS_ENABLE_REAL_LLM_PACKAGE_RUN")
        if not self.settings.llm_real_execution_enabled:
            failures.append("VCOS_LLM_REAL_EXECUTION_ENABLED")
        if self.settings.llm_provider.lower() != "ollama":
            failures.append("VCOS_LLM_PROVIDER")
        lanes = LLMRouterConfigLoader(self.session).list_lanes(profile_key="default")
        lane_names = {lane.lane_name for lane in lanes}
        required_lanes = {step.router_lane for step in PACKAGE_AGENT_CHAIN}
        missing_lanes = sorted(required_lanes - lane_names)
        if missing_lanes:
            failures.extend(f"LLM_ROUTER_LANE:{lane}" for lane in missing_lanes)
        if not failures:
            return None
        return {
            "status": "NOT_CONFIGURED",
            "reason_codes": ["LLM_PROVIDER_NOT_CONFIGURED"],
            "missing_or_invalid_flags": sorted(set(failures)),
            "next_action": "Cấu hình Ollama/LLMRouter real execution trước khi chạy video package production.",
        }

    def _task_payload(
        self,
        *,
        step: PackageAgentStep,
        data: FirstScriptedVideoPackageRequest,
        artifacts: dict[str, Any],
        channel: ChannelWorkspace,
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any]:
        return {
            "milestone": "M12.2 Production Prompt Activation",
            "agent_task": step.artifact_key,
            "channel_id": str(channel.id),
            "compiled_policy_snapshot_id": str(snapshot.id),
            "seed_topic": data.topic,
            "research_pack_text": data.research_pack_text,
            "research_pack_ref": data.research_pack_ref,
            "previous_artifacts": artifacts,
            "runtime_constraints": {
                "human_review_only": True,
                "no_media_provider_calls": True,
                "no_upload": True,
                "no_publish": True,
                "no_reupload": True,
                "no_mock_fallback": True,
                "no_prompt_self_mutation": True,
                "no_channel_config_mutation": True,
            },
        }

    def _evidence_refs(self, data: FirstScriptedVideoPackageRequest) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        if data.research_pack_ref:
            refs.append({"source_type": "OPERATOR_RESEARCH_PACK", "ref": data.research_pack_ref})
        if data.research_pack_text:
            refs.append({"source_type": "OPERATOR_RESEARCH_PACK_INLINE", "provided": True})
        return refs

    def _artifact_refs(self, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"artifact_key": key} for key in sorted(artifacts)]

    def _latest_audit_id(self, render_run_id: uuid.UUID, *, provider_refs: list[dict[str, Any]]) -> uuid.UUID | None:
        audit = self.session.scalars(
            select(PromptAuditSnapshot)
            .where(PromptAuditSnapshot.prompt_render_run_id == render_run_id)
            .order_by(desc(PromptAuditSnapshot.created_at))
            .limit(1)
        ).one_or_none()
        if audit is None:
            return None
        audit.provider_attempt_refs = provider_refs
        audit.final_output_ref = f"prompt-output:{render_run_id}"
        self.session.flush()
        return audit.id

    def _agent_ref(
        self,
        step: PackageAgentStep,
        render: Any,
        *,
        route: Any,
        validation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_key": step.agent_key,
            "artifact_key": step.artifact_key,
            "template_key": render.template_key,
            "template_version": render.template_version,
            "router_lane": render.router_lane,
            "prompt_hash": render.prompt_hash,
            "prompt_context_hash": render.prompt_context_hash,
            "prompt_render_run_id": str(render.prompt_render_run_id),
            "llm_route_attempt_id": str(route.route_attempt_id),
            "provider_attempt_id": str(route.provider_attempt_id) if route.provider_attempt_id else None,
            "llm_run_snapshot_id": str(route.llm_run_snapshot_id) if route.llm_run_snapshot_id else None,
            "route_status": route.status,
            "validation": validation,
        }

    def _visual_plan_block(self, artifact: Any) -> dict[str, Any] | None:
        values = _find_visual_source_values(artifact)
        invalid = sorted(value for value in values if value not in VISUAL_SOURCE_ALLOWLIST)
        if not invalid:
            return None
        return {
            "status": "REVIEW_REQUIRED",
            "reason_codes": ["VISUAL_SOURCE_NOT_ALLOWED"],
            "invalid_visual_sources": invalid,
            "allowed_visual_sources": sorted(VISUAL_SOURCE_ALLOWLIST),
        }

    def _gatekeeper_result(self, output: dict[str, Any]) -> str:
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        result = str(artifact.get("result") or artifact.get("decision") or output.get("status") or "").upper()
        if result in {"PASS", "OK"}:
            return "PASS"
        if result in {"BLOCK", "BLOCKED"}:
            return "BLOCK"
        return "REVIEW_REQUIRED" if result == "REVIEW_REQUIRED" else "PASS"

    def _human_review_checklist(self, artifacts: dict[str, Any], provider_readiness_snapshot_id: uuid.UUID) -> dict[str, Any]:
        narration = artifacts.get("narration_script") or {}
        research = artifacts.get("research_notes") or {}
        metadata = artifacts.get("metadata_package") or {}
        visual_plan = artifacts.get("visual_plan") or {}
        return {
            "facts_claims_need_review": True,
            "evidence_refs_missing": not bool(research.get("evidence_refs") or research.get("sources") or research.get("source_notes")),
            "title_thumbnail_accuracy": "REVIEW_REQUIRED",
            "rights_source_manifest": "REVIEW_REQUIRED",
            "ai_disclosure_needed": True,
            "market_locale_fit": "REVIEW_REQUIRED",
            "content_language_check": "REVIEW_REQUIRED",
            "reused_content_risk": "REVIEW_REQUIRED",
            "upload_card_copy_ready": bool(artifacts.get("upload_card_copy")),
            "provider_readiness_gaps_ref": str(provider_readiness_snapshot_id),
            "narration_sentence_ids_present": bool(narration.get("sentences") or narration.get("sentence_ids")),
            "metadata_present": bool(metadata),
            "visual_plan_present": bool(visual_plan),
            "final_statement": HUMAN_APPROVAL_REQUIRED,
        }

    def _risk_summary(self, artifacts: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "package_status": status,
            "media_provider_calls_made": False,
            "upload_or_publish_calls_made": False,
            "old_provider_smoke_run": False,
            "mock_fallback_used": False,
            "dry_run_success_used": False,
            "local_fixture_success_used": False,
            "channel_config_mutated": False,
            "learning_auto_promotion": False,
            "limitations": [
                "Gatekeeper soft review không thay thế human approval.",
                "Visual plan là brief/candidate-only, chưa tạo Veo/Creatomate output.",
            ]
            if artifacts.get("visual_plan")
            else ["Package chưa có visual plan hoàn chỉnh."],
        }

    def _create_package(
        self,
        *,
        channel_id: uuid.UUID,
        status: str,
        video_project_id: uuid.UUID | None = None,
        channel_profile_version_id: uuid.UUID | None = None,
        compiled_policy_snapshot_id: uuid.UUID | None = None,
        provider_readiness_snapshot_id: uuid.UUID | None = None,
        agent_run_refs: list[dict[str, Any]] | None = None,
        prompt_render_run_refs: list[str] | None = None,
        prompt_audit_snapshot_refs: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
        limitations: list[str] | None = None,
        risk_limitations_summary: dict[str, Any] | None = None,
        next_action: str | None = None,
    ) -> FirstScriptedVideoPackage:
        package = FirstScriptedVideoPackage(
            video_project_id=video_project_id,
            channel_id=channel_id,
            channel_profile_version_id=channel_profile_version_id,
            compiled_policy_snapshot_id=compiled_policy_snapshot_id,
            provider_readiness_snapshot_id=provider_readiness_snapshot_id,
            package_status=status,
            agent_run_refs=agent_run_refs or [],
            prompt_render_run_refs=prompt_render_run_refs or [],
            prompt_audit_snapshot_refs=prompt_audit_snapshot_refs or [],
            artifacts=artifacts or {},
            limitations=limitations or [],
            risk_limitations_summary=risk_limitations_summary or self._risk_summary(artifacts or {}, status),
            next_action=next_action,
        )
        self.session.add(package)
        self.session.flush()
        return package

    def _read(self, package: FirstScriptedVideoPackage) -> FirstScriptedVideoPackageRead:
        return FirstScriptedVideoPackageRead(
            id=package.id,
            video_project_id=package.video_project_id,
            channel_id=package.channel_id,
            channel_profile_version_id=package.channel_profile_version_id,
            compiled_policy_snapshot_id=package.compiled_policy_snapshot_id,
            provider_readiness_snapshot_id=package.provider_readiness_snapshot_id,
            package_status=package.package_status,  # type: ignore[arg-type]
            agent_run_refs=package.agent_run_refs,
            prompt_render_run_refs=[uuid.UUID(str(item)) for item in package.prompt_render_run_refs],
            prompt_audit_snapshot_refs=[uuid.UUID(str(item)) for item in package.prompt_audit_snapshot_refs],
            artifacts=package.artifacts,
            limitations=package.limitations,
            risk_limitations_summary=package.risk_limitations_summary,
            next_action=package.next_action,
            created_at=package.created_at,
        )


def _find_visual_source_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"visual_source", "source_type", "intended_visual_source"} and isinstance(item, str):
                found.add(item)
            found.update(_find_visual_source_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_visual_source_values(item))
    return found
