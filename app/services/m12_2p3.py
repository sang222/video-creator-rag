from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import (
    ChannelContractReviewRequest,
    ChannelInitDraftCreate,
)
from app.contracts.profile import ChannelProfileInput
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelContractDraft,
    ChannelInitDraft,
    ChannelProfileVersion,
    ChannelWorkspace,
    Company,
    CompiledChannelPolicySnapshot,
)
from app.services.channel_contract import (
    CONTRACT_COMPLETE,
    CONTRACT_CONTRADICTORY,
    CONTRACT_PARTIAL,
    FORBIDDEN_BEHAVIOR_CODES,
    reject_legacy_provider_budget_fields,
)
from app.services.channel_workspace import ChannelWorkspaceService
from app.services.config_registry import content_hash


WORKFLOW_RESEARCH_PENDING = "RESEARCH_PENDING"
WORKFLOW_RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
WORKFLOW_NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
WORKFLOW_READY_TO_COMPILE = "READY_TO_COMPILE"
WORKFLOW_COMPILED_PARTIAL = "COMPILED_PARTIAL"
WORKFLOW_COMPILED_COMPLETE = "COMPILED_COMPLETE"
WORKFLOW_ACTIVATED = "ACTIVATED"
WORKFLOW_BLOCKED = "BLOCKED"

STRATEGIC_FIELD_PATHS = {
    "market_locale.primary_market",
    "market_locale.audience_locale",
    "market_locale.content_language",
    "target_audience.primary_persona",
    "channel_identity.niche",
    "channel_identity.positioning",
    "editorial_strategy.content_pillars",
    "editorial_strategy.claim_style",
    "format_policy.long_form.enabled",
    "format_policy.shorts.enabled",
    "learning_policy.min_evidence_required",
}
HUMAN_TRUTH_SOURCE_TYPES = {"ADMIN_INPUT", "HUMAN_CONFIRMED", "GLOBAL_LOCKED_POLICY", "PROVIDER_POLICY"}


class ChannelInitDraftService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, data: ChannelInitDraftCreate) -> ChannelInitDraft:
        if self.session.get(Company, data.company_id) is None:
            raise NotFoundError(f"company not found: {data.company_id}")
        reject_legacy_provider_budget_fields(data.model_dump(mode="json"))
        workflow_status = WORKFLOW_RESEARCH_PENDING if data.source_usage_attestation else WORKFLOW_BLOCKED
        draft = ChannelInitDraft(
            company_id=data.company_id,
            channel_name=data.channel_name,
            public_presence_mode=data.public_presence_mode,
            youtube_url_or_handle=_clean(data.youtube_url_or_handle),
            website_url=_clean(data.website_url),
            social_profile_links=_string_list(data.social_profile_links),
            operator_note_purpose=data.operator_note_purpose,
            intended_content_language=_clean(data.intended_content_language),
            intended_primary_market=_clean(data.intended_primary_market),
            owner_operator_language=data.owner_operator_language or "vi-VN",
            initial_topic_pillar_hints=_string_list(data.initial_topic_pillar_hints),
            source_usage_attestation=data.source_usage_attestation,
            workflow_status=workflow_status,
        )
        self.session.add(draft)
        self.session.flush()
        return draft

    def get(self, draft_id: uuid.UUID) -> ChannelInitDraft:
        draft = self.session.get(ChannelInitDraft, draft_id)
        if draft is None:
            raise NotFoundError(f"channel init draft not found: {draft_id}")
        return draft

    def latest_contract_draft(self, draft_id: uuid.UUID) -> ChannelContractDraft | None:
        return self.session.scalars(
            select(ChannelContractDraft)
            .where(ChannelContractDraft.init_draft_id == draft_id)
            .order_by(ChannelContractDraft.created_at.desc())
        ).first()


class ChannelResearchEvidenceCollector:
    def collect(self, draft: ChannelInitDraft, *, enable_optional_web_snippets: bool = False) -> list[dict[str, Any]]:
        now = utc_now().isoformat()
        evidence: list[dict[str, Any]] = []
        if draft.operator_note_purpose:
            evidence.append(
                {
                    "ref_id": "ev_admin_note_purpose",
                    "source_type": "ADMIN_NOTE",
                    "url": None,
                    "title": "Operator purpose note",
                    "snippet": draft.operator_note_purpose,
                    "captured_at": now,
                    "reliability": "HIGH",
                }
            )
        if draft.initial_topic_pillar_hints:
            evidence.append(
                {
                    "ref_id": "ev_admin_topic_hints",
                    "source_type": "ADMIN_NOTE",
                    "url": None,
                    "title": "Operator topic/pillar hints",
                    "snippet": ", ".join(draft.initial_topic_pillar_hints),
                    "captured_at": now,
                    "reliability": "HIGH",
                }
            )
        if draft.youtube_url_or_handle:
            evidence.append(
                {
                    "ref_id": "ev_youtube_public_anchor",
                    "source_type": "YOUTUBE_API",
                    "url": draft.youtube_url_or_handle,
                    "title": "Admin-provided public YouTube anchor",
                    "snippet": "Public anchor only; no YouTube Studio, private analytics, upload, or scraping call was made.",
                    "captured_at": now,
                    "reliability": "MEDIUM",
                }
            )
        if draft.website_url:
            evidence.append(
                {
                    "ref_id": "ev_website_public_anchor",
                    "source_type": "PUBLIC_WEB",
                    "url": draft.website_url,
                    "title": "Admin-provided public website anchor",
                    "snippet": "Public website anchor confirmed by operator attestation; optional web snippet connector disabled by default.",
                    "captured_at": now,
                    "reliability": "MEDIUM",
                }
            )
        for index, link in enumerate(draft.social_profile_links, start=1):
            evidence.append(
                {
                    "ref_id": f"ev_social_profile_{index}",
                    "source_type": "SOCIAL_PROFILE",
                    "url": link,
                    "title": "Admin-provided public social/profile anchor",
                    "snippet": "Public social/profile link confirmed by operator attestation.",
                    "captured_at": now,
                    "reliability": "MEDIUM",
                }
            )
        if enable_optional_web_snippets:
            evidence.append(
                {
                    "ref_id": "ev_optional_web_snippets_disabled_safe_default",
                    "source_type": "CONNECTOR_SNIPPET",
                    "url": None,
                    "title": "Optional web snippet adapter",
                    "snippet": "Connector flag was enabled, but no external snippet provider is configured in this local workflow.",
                    "captured_at": now,
                    "reliability": "LOW",
                }
            )
        return evidence


class ChannelContractDraftBuilder:
    def build(self, draft: ChannelInitDraft, evidence_refs: list[dict[str, Any]]) -> dict[str, Any]:
        text_blob = " ".join(
            [
                draft.channel_name,
                draft.operator_note_purpose,
                " ".join(draft.initial_topic_pillar_hints),
                draft.youtube_url_or_handle or "",
                draft.website_url or "",
            ]
        )
        hints = draft.initial_topic_pillar_hints
        has_ai_workflow_signal = _contains_any(text_blob, ["ai workflow", "automation", "dashboard", "small team"])
        topic_summary = " / ".join(hints) if hints else _bounded_summary(draft.operator_note_purpose)
        niche = (
            f"Practical {topic_summary} for small teams"
            if has_ai_workflow_signal and topic_summary
            else topic_summary or "UNKNOWN"
        )
        content_language = _content_language_suggestion(draft, text_blob)
        audience_locale = _audience_locale_for_language(content_language)
        primary_market = draft.intended_primary_market or "UNKNOWN"
        contract: dict[str, Any] = {
            "channel_identity": {
                "company_id": str(draft.company_id),
                "channel_name": draft.channel_name,
                "channel_type": "YOUTUBE_CHANNEL",
                "niche": niche,
                "positioning": _positioning_suggestion(draft, niche),
                "brand_promise": _brand_promise_suggestion(draft),
                "primary_platform": "YouTube",
                "secondary_platforms": ["Shorts"],
                "series_plan": [{"key": "operator_series", "name": niche if niche != "UNKNOWN" else draft.channel_name}],
            },
            "target_audience": {
                "primary_persona": _audience_suggestion(text_blob),
                "audience_level": "semi_technical" if has_ai_workflow_signal else "UNKNOWN",
                "pain_points": _pain_points_suggestion(text_blob),
                "desired_outcome": _desired_outcome_suggestion(draft),
                "audience_notes": "Research suggestion only; requires operator confirmation.",
            },
            "market_locale": {
                "primary_market": primary_market,
                "secondary_markets": [],
                "audience_locale": audience_locale,
                "content_language": content_language,
                "operator_language": draft.owner_operator_language,
                "timezone": "UNKNOWN",
                "currency": "UNKNOWN",
                "measurement_units": "metric",
                "date_format": "DD/MM/YYYY",
                "cultural_style": {"tone": "clear", "formality": "professional", "humor": "light", "cta_style": "practical"},
                "market_examples_preference": "prefer",
                "regulatory_sensitivity": {
                    "finance_claim_sensitivity": "high",
                    "health_claim_sensitivity": "high",
                    "disclosure_standard": "clear_ai_and_source_disclosure",
                },
                "market_locale_context_status": "UNKNOWN" if primary_market == "UNKNOWN" else "PARTIAL",
            },
            "editorial_strategy": {
                "content_pillars": hints if hints else ["UNKNOWN"],
                "allowed_angles": ["practical explainer", "operator workflow walkthrough"],
                "forbidden_angles": ["guaranteed ROI", "fake urgency", "provider bypass"],
                "claim_style": ["practical", "evidence_bounded", "no_exaggerated_roi"],
                "allowed_topics": hints if hints else [],
                "forbidden_topics": ["fake engagement", "platform evasion"],
            },
            "format_policy": {
                "long_form": {
                    "enabled": True,
                    "target_duration_minutes": {"min": 8, "max": 15},
                    "structure": ["hook", "problem", "mechanism", "workflow", "takeaway"],
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
                "allowed_style": ["calm", "specific", "implementation-first"],
                "forbidden_style": ["hype", "fearmongering", "aggressive_sales", "fake_urgency"],
            },
            "platform_strategy": {
                "primary_platform": "YouTube",
                "youtube_is_learning_authority": True,
                "secondary_platforms": ["Shorts"],
                "disabled_authorities": ["tiktok_analytics_learning", "facebook_analytics_learning"],
                "publish_mode": "human_handoff_only",
                "auto_publish_allowed": False,
                "studio_scraping_allowed": False,
                "dashboard_scraping_allowed": False,
            },
            "media_policy": _locked_media_policy(),
            "rights_policy": _locked_rights_policy(),
            "budget_policy": {
                "cost_sensitivity": "medium",
                "avoid_unnecessary_ai_hero": True,
                "prefer_reuse_safe_assets": True,
                "exact_cost_claim_requires_provider_snapshot": True,
            },
            "learning_policy": {
                "authority": "youtube_analytics_only",
                "min_evidence_required": "2 source refs for non-obvious claims",
                "auto_promote_learning": False,
                "config_mutation_by_agent_allowed": False,
                "weak_evidence_action": "summarize_limitations_only",
            },
            "forbidden_behavior": sorted(FORBIDDEN_BEHAVIOR_CODES),
        }
        field_map = self._build_field_map(contract=contract, draft=draft, evidence_refs=evidence_refs)
        status, missing_fields, contradiction_reasons = evaluate_contract(contract, field_map)
        _apply_status(contract, field_map, status, missing_fields, contradiction_reasons)
        confidence_summary = {path: meta["confidence_label"] for path, meta in field_map.items()}
        return {
            "contract": contract,
            "field_map": field_map,
            "confidence_summary": confidence_summary,
            "missing_fields": missing_fields,
            "human_questions": _human_questions(missing_fields),
            "risks": _research_risks(draft),
        }

    def _build_field_map(
        self,
        *,
        contract: dict[str, Any],
        draft: ChannelInitDraft,
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        evidence_ids = [item["ref_id"] for item in evidence_refs]
        note_refs = [item["ref_id"] for item in evidence_refs if item["source_type"] == "ADMIN_NOTE"]
        public_refs = [item["ref_id"] for item in evidence_refs if item["source_type"] in {"YOUTUBE_API", "PUBLIC_WEB", "SOCIAL_PROFILE"}]
        field_map: dict[str, dict[str, Any]] = {}
        for path, value in leaf_values(contract).items():
            if path in {
                "channel_identity.company_id",
                "channel_identity.channel_name",
                "market_locale.operator_language",
            }:
                field_map[path] = _meta(value, "ADMIN_INPUT", "HIGH", [], review_required=False)
            elif path == "market_locale.primary_market" and draft.intended_primary_market:
                field_map[path] = _meta(value, "ADMIN_HINT", "MEDIUM", note_refs, review_required=True)
            elif path == "market_locale.content_language" and draft.intended_content_language:
                field_map[path] = _meta(value, "ADMIN_HINT", "MEDIUM", note_refs, review_required=True)
            elif path.startswith("media_policy."):
                field_map[path] = _meta(value, "PROVIDER_POLICY", "HIGH", [], review_required=False, editable=False, locked_reason="Global provider policy")
            elif path.startswith("rights_policy.") or path in {
                "platform_strategy.publish_mode",
                "platform_strategy.auto_publish_allowed",
                "platform_strategy.studio_scraping_allowed",
                "platform_strategy.dashboard_scraping_allowed",
                "learning_policy.authority",
                "learning_policy.auto_promote_learning",
                "learning_policy.config_mutation_by_agent_allowed",
                "learning_policy.weak_evidence_action",
                "forbidden_behavior",
            }:
                field_map[path] = _meta(value, "GLOBAL_LOCKED_POLICY", "HIGH", [], review_required=False, editable=False, locked_reason="Global safety policy")
            elif path in {"channel_identity.niche", "editorial_strategy.content_pillars", "editorial_strategy.allowed_topics"} and draft.initial_topic_pillar_hints:
                field_map[path] = _meta(value, "ADMIN_HINT", "MEDIUM", note_refs, review_required=True)
            elif path in {"channel_identity.positioning", "channel_identity.brand_promise"} and draft.operator_note_purpose:
                field_map[path] = _meta(value, "ADMIN_HINT", "MEDIUM", note_refs, review_required=True)
            elif _is_unknown(value):
                field_map[path] = _meta(value, "UNKNOWN", "LOW", [], review_required=True)
            else:
                refs = public_refs or note_refs or evidence_ids
                confidence = "MEDIUM" if refs else "LOW"
                field_map[path] = _meta(value, "RESEARCH_INFERENCE", confidence, refs, review_required=path in STRATEGIC_FIELD_PATHS)
        return field_map


class ChannelSetupResearchAgentService:
    def __init__(self, session: Session):
        self.session = session
        self.collector = ChannelResearchEvidenceCollector()
        self.builder = ChannelContractDraftBuilder()

    def run(self, draft_id: uuid.UUID, *, enable_optional_web_snippets: bool = False) -> ChannelContractDraft:
        draft = ChannelInitDraftService(self.session).get(draft_id)
        if not draft.source_usage_attestation:
            draft.workflow_status = WORKFLOW_BLOCKED
            self.session.flush()
            raise ValidationFailureError("source usage attestation is required before research")
        if draft.public_presence_mode == "EXISTING_PUBLIC_CHANNEL" and not _has_public_anchor(draft):
            draft.workflow_status = WORKFLOW_BLOCKED
            self.session.flush()
            raise ValidationFailureError("existing public channel requires at least one public source anchor")
        evidence_refs = self.collector.collect(draft, enable_optional_web_snippets=enable_optional_web_snippets)
        result = self.builder.build(draft, evidence_refs)
        contract_draft = ChannelContractDraft(
            init_draft_id=draft.id,
            company_id=draft.company_id,
            channel_name=draft.channel_name,
            source_urls=_source_urls(draft),
            admin_minimal_input=_minimal_input_json(draft),
            suggested_channel_contract=result["contract"],
            field_source_map_json=result["field_map"],
            confidence_summary=result["confidence_summary"],
            missing_fields=result["missing_fields"],
            human_questions=result["human_questions"],
            risks=result["risks"],
            evidence_refs=evidence_refs,
            workflow_status=WORKFLOW_NEEDS_HUMAN_REVIEW,
            contract_status=CONTRACT_PARTIAL,
            review_decision_log_json=[],
        )
        draft.workflow_status = WORKFLOW_NEEDS_HUMAN_REVIEW
        draft.contract_status = CONTRACT_PARTIAL
        self.session.add(contract_draft)
        self.session.flush()
        return contract_draft


class ChannelContractReviewService:
    def __init__(self, session: Session):
        self.session = session

    def apply_review(self, draft_id: uuid.UUID, data: ChannelContractReviewRequest) -> ChannelContractDraft:
        init_draft = ChannelInitDraftService(self.session).get(draft_id)
        contract_draft = ChannelInitDraftService(self.session).latest_contract_draft(draft_id)
        if contract_draft is None:
            raise NotFoundError(f"channel contract draft not found for init draft: {draft_id}")
        contract = deepcopy(contract_draft.suggested_channel_contract)
        field_map = deepcopy(contract_draft.field_source_map_json)
        log = list(contract_draft.review_decision_log_json or [])
        now = utc_now().isoformat()
        for decision in data.decisions:
            path = decision.field_path
            action = decision.action
            previous_value = _get_path(contract, path)
            previous_meta = field_map.get(path, _meta(previous_value, "UNKNOWN", "LOW", [], review_required=True))
            if not previous_meta.get("editable_by_human", True) and action != "add_note":
                raise ValidationFailureError(f"field is locked and cannot be reviewed: {path}")
            if action == "confirm":
                new_value = previous_value
                field_map[path] = {
                    **previous_meta,
                    "value": new_value,
                    "source_type": "HUMAN_CONFIRMED",
                    "confidence_label": "HIGH",
                    "review_required": False,
                }
            elif action == "edit":
                new_value = decision.value
                _set_path(contract, path, new_value)
                field_map[path] = _meta(
                    new_value,
                    "HUMAN_CONFIRMED",
                    "HIGH",
                    previous_meta.get("evidence_refs", []),
                    review_required=False,
                    editable=True,
                )
            elif action in {"reject", "mark_unknown"}:
                new_value = "UNKNOWN"
                _set_path(contract, path, new_value)
                field_map[path] = _meta(new_value, "UNKNOWN", "LOW", [], review_required=True, editable=True)
            elif action == "add_note":
                new_value = previous_value
            else:
                raise ValidationFailureError(f"unsupported review action: {action}")
            log.append(
                {
                    "field_path": path,
                    "action": action,
                    "previous_value": previous_value,
                    "new_value": new_value,
                    "previous_source_type": previous_meta.get("source_type"),
                    "new_source_type": field_map.get(path, previous_meta).get("source_type"),
                    "reviewer_user_id": str(decision.reviewer_user_id) if decision.reviewer_user_id else None,
                    "timestamp": now,
                    "reason_note": decision.note,
                    "human_notes": data.human_notes,
                }
            )
        ensure_field_source_coverage(contract, field_map)
        status, missing_fields, contradiction_reasons = evaluate_contract(contract, field_map)
        _apply_status(contract, field_map, status, missing_fields, contradiction_reasons)
        contract_draft.suggested_channel_contract = contract
        contract_draft.field_source_map_json = field_map
        contract_draft.confidence_summary = {path: meta["confidence_label"] for path, meta in field_map.items()}
        contract_draft.missing_fields = missing_fields
        contract_draft.human_questions = _human_questions(missing_fields)
        contract_draft.contract_status = status
        contract_draft.workflow_status = WORKFLOW_READY_TO_COMPILE if status == CONTRACT_COMPLETE else WORKFLOW_NEEDS_HUMAN_REVIEW
        contract_draft.review_decision_log_json = log
        init_draft.workflow_status = contract_draft.workflow_status
        init_draft.contract_status = status
        self.session.flush()
        return contract_draft


class ChannelContractCompiler:
    def __init__(self, session: Session):
        self.session = session

    def compile(self, draft_id: uuid.UUID, *, correlation_id: str | None = None) -> dict[str, Any]:
        init_draft = ChannelInitDraftService(self.session).get(draft_id)
        contract_draft = ChannelInitDraftService(self.session).latest_contract_draft(draft_id)
        if contract_draft is None:
            raise NotFoundError(f"channel contract draft not found for init draft: {draft_id}")
        contract = deepcopy(contract_draft.suggested_channel_contract)
        field_map = deepcopy(contract_draft.field_source_map_json)
        self._force_locked_policies(contract, field_map)
        ensure_field_source_coverage(contract, field_map)
        reject_legacy_provider_budget_fields(contract)
        status, missing_fields, contradiction_reasons = evaluate_contract(contract, field_map)
        _apply_status(contract, field_map, status, missing_fields, contradiction_reasons)
        channel = self._get_or_create_channel(init_draft, contract, field_map)
        profile = self._create_profile_version(channel, contract, field_map)
        payload = self._compiled_payload(contract=contract, field_map=field_map)
        output_hash = content_hash(payload)
        snapshot = self._create_snapshot(channel=channel, profile=profile, payload=payload, output_hash=output_hash)
        contract_draft.suggested_channel_contract = contract
        contract_draft.field_source_map_json = field_map
        contract_draft.missing_fields = missing_fields
        contract_draft.contract_status = status
        contract_draft.workflow_status = WORKFLOW_COMPILED_COMPLETE if status == CONTRACT_COMPLETE else WORKFLOW_COMPILED_PARTIAL
        init_draft.channel_id = channel.id
        init_draft.channel_profile_version_id = profile.id
        init_draft.compiled_policy_snapshot_id = snapshot.id
        init_draft.contract_status = status
        init_draft.workflow_status = contract_draft.workflow_status
        self.session.flush()
        return {
            "init_draft_id": init_draft.id,
            "channel_id": channel.id,
            "channel_profile_version_id": profile.id,
            "compiled_policy_snapshot_id": snapshot.id,
            "workflow_status": init_draft.workflow_status,
            "contract_status": status,
            "missing_fields": missing_fields,
            "contradiction_reasons": contradiction_reasons,
            "activation_eligibility": status == CONTRACT_COMPLETE,
            "channel_contract_json": contract,
            "field_source_map_json": field_map,
        }

    def _force_locked_policies(self, contract: dict[str, Any], field_map: dict[str, dict[str, Any]]) -> None:
        locked_values = {
            "platform_strategy.publish_mode": "human_handoff_only",
            "platform_strategy.auto_publish_allowed": False,
            "platform_strategy.studio_scraping_allowed": False,
            "platform_strategy.dashboard_scraping_allowed": False,
            "learning_policy.authority": "youtube_analytics_only",
            "learning_policy.auto_promote_learning": False,
            "learning_policy.config_mutation_by_agent_allowed": False,
            "learning_policy.weak_evidence_action": "summarize_limitations_only",
            "forbidden_behavior": sorted(FORBIDDEN_BEHAVIOR_CODES),
        }
        locked_values.update({f"media_policy.{key}": value for key, value in _locked_media_policy().items()})
        locked_values.update({f"rights_policy.{key}": value for key, value in _locked_rights_policy().items()})
        for path, value in locked_values.items():
            _set_path(contract, path, value)
            source_type = "PROVIDER_POLICY" if path.startswith("media_policy.") else "GLOBAL_LOCKED_POLICY"
            field_map[path] = _meta(
                value,
                source_type,
                "HIGH",
                [],
                review_required=False,
                editable=False,
                locked_reason="Global provider policy" if source_type == "PROVIDER_POLICY" else "Global safety policy",
            )

    def _get_or_create_channel(
        self,
        init_draft: ChannelInitDraft,
        contract: dict[str, Any],
        field_map: dict[str, dict[str, Any]],
    ) -> ChannelWorkspace:
        if init_draft.channel_id:
            channel = self.session.get(ChannelWorkspace, init_draft.channel_id)
            if channel is None:
                raise NotFoundError(f"channel not found: {init_draft.channel_id}")
            return channel
        key = _unique_channel_key(self.session, init_draft.company_id, init_draft.channel_name, init_draft.id)
        market = contract.get("market_locale", {})
        channel = ChannelWorkspaceService(self.session).create_channel(
            company_id=init_draft.company_id,
            data=_channel_workspace_create(
                key=key,
                name=init_draft.channel_name,
                contract=contract,
                field_map=field_map,
                init_draft_id=init_draft.id,
                market=market,
            ),
            correlation_id="m12-2p3-channel-init-compile",
        )
        return channel

    def _create_profile_version(
        self,
        channel: ChannelWorkspace,
        contract: dict[str, Any],
        field_map: dict[str, dict[str, Any]],
    ) -> ChannelProfileVersion:
        next_version = (
            self.session.scalar(
                select(func.max(ChannelProfileVersion.version)).where(
                    ChannelProfileVersion.channel_workspace_id == channel.id
                )
            )
            or 0
        ) + 1
        profile_input = _profile_input_from_contract(contract, field_map)
        profile = ChannelProfileVersion(
            channel_workspace_id=channel.id,
            version=next_version,
            status="compiled",
            profile_input=profile_input,
            profile_input_hash=content_hash(profile_input),
            source_template_key=None,
            source_template_version=None,
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def _create_snapshot(
        self,
        *,
        channel: ChannelWorkspace,
        profile: ChannelProfileVersion,
        payload: dict[str, Any],
        output_hash: str,
    ) -> CompiledChannelPolicySnapshot:
        next_version = (
            self.session.scalar(
                select(func.max(CompiledChannelPolicySnapshot.snapshot_version)).where(
                    CompiledChannelPolicySnapshot.channel_workspace_id == channel.id
                )
            )
            or 0
        ) + 1
        snapshot = CompiledChannelPolicySnapshot(
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            compile_run_id=None,
            snapshot_version=next_version,
            status="compiled",
            compiler_version="m12.2p3.research_assisted_compiler.v1",
            capability_matrix_version="m12.2p3.no_provider_calls",
            compiled_payload=payload,
            content_hash=output_hash,
            profile_input_hash=profile.profile_input_hash,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def _compiled_payload(self, *, contract: dict[str, Any], field_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        pillars = _string_list(contract.get("editorial_strategy", {}).get("content_pillars")) or ["UNKNOWN"]
        series_plan = _list_of_dicts(contract.get("channel_identity", {}).get("series_plan")) or [
            {"key": "operator_series", "name": contract.get("channel_identity", {}).get("channel_name", "Channel")}
        ]
        legacy_payload = {
            "channel_constitution": {
                "promise": contract.get("channel_identity", {}).get("brand_promise") or "Research-assisted channel contract.",
                "audience": contract.get("target_audience", {}).get("primary_persona") or "UNKNOWN",
                "boundaries": ["human review required", "no auto publish/upload/reupload"],
            },
            "operating_blueprint": {
                "target_market": contract.get("market_locale", {}).get("primary_market"),
                "platform_strategy": contract.get("platform_strategy", {}),
                "human_review_strictness": "strict",
                "risk_tolerance": "low_to_medium",
            },
            "content_pillars": pillars,
            "series_plan": series_plan,
            "editorial_calendar_defaults": {"planning_unit": "weekly"},
            "initial_content_runway": [{"title": pillars[0], "format": "long_form"}],
            "default_playbook": {"format_strategy": contract.get("format_policy", {}), "media_style": {"source": "research_assisted"}},
            "render_policy": {
                "capcut_prototype_viewer_only": True,
                "production_renderer_planned": "Creatomate Growth 10K",
                "transcription_pilot": "faster_whisper_local",
                "ai_video_mode": "manual_external",
                "visual_plan_required": True,
            },
            "gate_policy": {"claim_review": "human_review_for_non_obvious_claims", "safety": "avoid unsupported claims"},
            "voice_policy": contract.get("voice_style", {}),
            "evidence_policy": {"claims": contract.get("learning_policy", {}).get("min_evidence_required")},
            "monetization_policy": {"primary": "UNKNOWN", "channels": []},
            "kpi_profile": {"primary": "qualified attention", "secondary": ["watch_time", "returning_viewers"]},
            "editorial_promise": contract.get("channel_identity", {}).get("brand_promise") or "Evidence-bounded practical guidance.",
            "distinctiveness_profile": {"angle": contract.get("channel_identity", {}).get("positioning") or "UNKNOWN", "visual_bias": []},
            "format_bible": {"long_form": contract.get("format_policy", {}).get("long_form", {}), "voice": contract.get("voice_style", {})},
            "capability_status": {
                "profile_compiler": "research_assisted_minimal",
                "policy_snapshot": "available",
                "artifact_workflow": "available",
                "media_pipeline": "restricted_until_milestone",
                "publish_pipeline": "human_handoff_only",
                "analytics": "available",
                "no_view_diagnostic": "available",
                "envato_manual_asset_pilot_documented": False,
                "ffmpeg_renderer_planned": False,
            },
        }
        compiled_policy_snapshot_json = {
            "schema_version": "m12.2p3.channel_policy_snapshot.v1",
            "snapshot_source": "ChannelContractCompiler",
            "channel_contract_status": contract["contract_status"],
            "missing_fields": contract["missing_fields"],
            "contradiction_reasons": contract["contradiction_reasons"],
            "market_locale": contract.get("market_locale", {}),
            "field_source_map_json": field_map,
            "no_runtime_mutation_guarantee": True,
            "no_provider_calls_confirmed": True,
            "legacy_policy_sections": legacy_payload,
        }
        return {
            **legacy_payload,
            "channel_contract_json": contract,
            "field_source_map_json": field_map,
            "compiled_policy_snapshot_json": compiled_policy_snapshot_json,
            "contract_status": contract["contract_status"],
            "missing_fields": contract["missing_fields"],
            "contradiction_reasons": contract["contradiction_reasons"],
            "activation_required": contract["contract_status"] != CONTRACT_COMPLETE,
        }


def evaluate_contract(contract: dict[str, Any], field_map: dict[str, dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    ensure_field_source_coverage(contract, field_map)
    missing: set[str] = set()
    contradictions: list[str] = []
    for path in STRATEGIC_FIELD_PATHS:
        value = _get_path(contract, path)
        meta = field_map.get(path, {})
        if _is_unknown(value):
            missing.add(path)
            continue
        if meta.get("source_type") not in HUMAN_TRUTH_SOURCE_TYPES:
            missing.add(f"{path}:requires_human_confirmation")
    for path in [
        "rights_policy.source_manifest_required",
        "rights_policy.rights_evidence_required",
        "rights_policy.ai_disclosure_required_when_ai_media_used",
        "platform_strategy.publish_mode",
        "media_policy.voice_provider",
        "media_policy.renderer",
        "forbidden_behavior",
    ]:
        if _is_unknown(_get_path(contract, path)):
            missing.add(path)
    platform = contract.get("platform_strategy", {})
    learning = contract.get("learning_policy", {})
    forbidden = set(_string_list(contract.get("forbidden_behavior")))
    if platform.get("auto_publish_allowed") is not False:
        contradictions.append("platform_strategy.auto_publish_allowed must be false")
    if platform.get("studio_scraping_allowed") is not False:
        contradictions.append("platform_strategy.studio_scraping_allowed must be false")
    if platform.get("dashboard_scraping_allowed") not in {False, None}:
        contradictions.append("platform_strategy.dashboard_scraping_allowed must be false")
    if platform.get("publish_mode") != "human_handoff_only":
        contradictions.append("platform_strategy.publish_mode must be human_handoff_only")
    if learning.get("auto_promote_learning") is not False:
        contradictions.append("learning_policy.auto_promote_learning must be false")
    if learning.get("config_mutation_by_agent_allowed") is not False:
        contradictions.append("learning_policy.config_mutation_by_agent_allowed must be false")
    missing_forbidden = sorted(set(FORBIDDEN_BEHAVIOR_CODES) - forbidden)
    if missing_forbidden:
        contradictions.append(f"forbidden_behavior missing locked rules: {missing_forbidden}")
    if contradictions:
        return CONTRACT_CONTRADICTORY, sorted(missing), contradictions
    if missing:
        return CONTRACT_PARTIAL, sorted(missing), []
    return CONTRACT_COMPLETE, [], []


def ensure_field_source_coverage(contract: dict[str, Any], field_map: dict[str, dict[str, Any]]) -> None:
    for path, value in leaf_values(contract).items():
        if path not in field_map:
            field_map[path] = _meta(value, "UNKNOWN", "LOW", [], review_required=True)
        else:
            field_map[path]["value"] = value


def leaf_values(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        result: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(leaf_values(child, path))
        return result
    return {prefix: value} if prefix else {}


def _apply_status(
    contract: dict[str, Any],
    field_map: dict[str, dict[str, Any]],
    status: str,
    missing_fields: list[str],
    contradiction_reasons: list[str],
) -> None:
    contract["contract_status"] = status
    contract["missing_fields"] = missing_fields
    contract["contradiction_reasons"] = contradiction_reasons
    contract["next_action"] = "Kích hoạt kênh." if status == CONTRACT_COMPLETE else "Người vận hành cần xác nhận hoặc sửa các field còn thiếu."
    for path in ["contract_status", "missing_fields", "contradiction_reasons", "next_action"]:
        field_map[path] = _meta(
            contract[path],
            "COMPILER_DERIVED",
            "HIGH",
            [],
            review_required=False,
            editable=False,
            locked_reason="Compiler status output",
        )
    ensure_field_source_coverage(contract, field_map)


def _profile_input_from_contract(contract: dict[str, Any], field_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    identity = contract.get("channel_identity", {})
    market = contract.get("market_locale", {})
    audience = contract.get("target_audience", {})
    editorial = contract.get("editorial_strategy", {})
    pillars = _string_list(editorial.get("content_pillars")) or ["UNKNOWN"]
    payload = ChannelProfileInput(
        template_key="research_assisted_minimal_contract",
        template_version="m12.2p3",
        display_name=identity.get("channel_name") or "Channel",
        target_market=market.get("primary_market") if not _is_unknown(market.get("primary_market")) else "",
        audience_segment=audience.get("primary_persona") if not _is_unknown(audience.get("primary_persona")) else "UNKNOWN",
        monetization_model={"primary": "UNKNOWN", "channels": []},
        format_strategy=contract.get("format_policy", {}),
        risk_tolerance="low_to_medium",
        media_style={"visual_bias": [], "external_assets": "approved/licensed/audio-library-safe only"},
        voice_style=contract.get("voice_style", {}),
        evidence_requirement={"claims": contract.get("learning_policy", {}).get("min_evidence_required")},
        platform_strategy=contract.get("platform_strategy", {}),
        human_review_strictness="strict",
        content_pillars=pillars,
        series_plan=_list_of_dicts(identity.get("series_plan")) or [{"key": "operator_series", "name": pillars[0]}],
        initial_content_runway=[{"title": pillars[0], "format": "long_form"}],
        policies={
            "channel_contract": contract,
            "field_source_map_json": field_map,
            "m12_2p3_no_catalog_template_used": True,
            "review_boundary": "human_review_required_for_complete",
        },
    )
    return payload.model_dump(mode="json")


def _channel_workspace_create(
    *,
    key: str,
    name: str,
    contract: dict[str, Any],
    field_map: dict[str, dict[str, Any]],
    init_draft_id: uuid.UUID,
    market: dict[str, Any],
) -> Any:
    from app.contracts import ChannelWorkspaceCreate

    content_language = market.get("content_language")
    primary_market = market.get("primary_market")
    timezone = market.get("timezone")
    return ChannelWorkspaceCreate(
        key=key,
        name=name,
        status="draft",
        primary_language=content_language if not _is_unknown(content_language) else "und",
        primary_region=primary_market if not _is_unknown(primary_market) else None,
        primary_timezone=timezone if not _is_unknown(timezone) else "UTC",
        target_market=primary_market if not _is_unknown(primary_market) else None,
        default_timezone=timezone if not _is_unknown(timezone) else "UTC",
        target_regions=_string_list(market.get("secondary_markets")),
        metadata={
            "m12_2p3_init_draft_id": str(init_draft_id),
            "m12_2p3_channel_contract": contract,
            "m12_2p3_field_source_map_json": field_map,
            "m12_2p3_no_catalog_template_used": True,
            "no_ai_config_suggestion": True,
        },
    )


def _meta(
    value: Any,
    source_type: str,
    confidence_label: str,
    evidence_refs: list[str],
    *,
    review_required: bool,
    editable: bool = True,
    locked_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "source_type": source_type,
        "confidence_label": confidence_label,
        "evidence_refs": evidence_refs,
        "review_required": review_required,
        "editable_by_human": editable,
        "locked_reason": locked_reason,
    }


def _has_public_anchor(draft: ChannelInitDraft) -> bool:
    return bool(draft.youtube_url_or_handle or draft.website_url or draft.social_profile_links)


def _minimal_input_json(draft: ChannelInitDraft) -> dict[str, Any]:
    return {
        "company_id": str(draft.company_id),
        "channel_name": draft.channel_name,
        "public_presence_mode": draft.public_presence_mode,
        "youtube_url_or_handle": draft.youtube_url_or_handle,
        "website_url": draft.website_url,
        "social_profile_links": draft.social_profile_links,
        "operator_note_purpose": draft.operator_note_purpose,
        "intended_content_language": draft.intended_content_language,
        "intended_primary_market": draft.intended_primary_market,
        "owner_operator_language": draft.owner_operator_language,
        "initial_topic_pillar_hints": draft.initial_topic_pillar_hints,
        "source_usage_attestation": draft.source_usage_attestation,
    }


def _source_urls(draft: ChannelInitDraft) -> list[dict[str, Any]]:
    urls = []
    if draft.youtube_url_or_handle:
        urls.append({"type": "youtube", "url": draft.youtube_url_or_handle})
    if draft.website_url:
        urls.append({"type": "website", "url": draft.website_url})
    for link in draft.social_profile_links:
        urls.append({"type": "social_profile", "url": link})
    return urls


def _content_language_suggestion(draft: ChannelInitDraft, text_blob: str) -> str:
    if draft.intended_content_language:
        return draft.intended_content_language
    if _contains_any(text_blob, [" small ", " team ", " workflow", "automation", "dashboard", "https://smallteamai.com"]):
        return "en"
    return "UNKNOWN"


def _audience_locale_for_language(content_language: str) -> str:
    lowered = str(content_language or "").lower()
    if lowered.startswith("vi"):
        return "vi-VN"
    if lowered.startswith("en"):
        return "en-US"
    return "UNKNOWN"


def _positioning_suggestion(draft: ChannelInitDraft, niche: str) -> str:
    if niche and niche != "UNKNOWN":
        return f"Clear, implementation-first guidance around {niche}"
    return _bounded_summary(draft.operator_note_purpose) or "UNKNOWN"


def _brand_promise_suggestion(draft: ChannelInitDraft) -> str:
    if draft.operator_note_purpose:
        return draft.operator_note_purpose
    return "UNKNOWN"


def _audience_suggestion(text_blob: str) -> str:
    if _contains_any(text_blob, ["small team", "đội ngũ nhỏ", "founder", "operator", "dashboard"]):
        return "Small business owners, team leads, operators, and builders"
    return "UNKNOWN"


def _pain_points_suggestion(text_blob: str) -> list[str]:
    points = []
    if _contains_any(text_blob, ["workflow", "automation", "dashboard"]):
        points.append("Need practical AI workflows and operating systems without hype")
    return points or ["UNKNOWN"]


def _desired_outcome_suggestion(draft: ChannelInitDraft) -> str:
    if draft.operator_note_purpose:
        return _bounded_summary(draft.operator_note_purpose)
    return "UNKNOWN"


def _human_questions(missing_fields: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "field_path": field,
            "question_vi": f"Người vận hành cần xác nhận hoặc sửa field: {field}",
            "reason": "Strategic field cannot be completed from research alone.",
        }
        for field in missing_fields
    ]


def _research_risks(draft: ChannelInitDraft) -> list[dict[str, Any]]:
    risks = [
        {
            "risk_code": "RESEARCH_DRAFT_NOT_RUNTIME_TRUTH",
            "severity": "HIGH",
            "message_vi": "Kết quả research chỉ là đề xuất, chưa phải cấu hình runtime.",
        },
        {
            "risk_code": "NO_YOUTUBE_STUDIO_SCRAPING",
            "severity": "HIGH",
            "message_vi": "Không dùng YouTube Studio scraping.",
        },
    ]
    if draft.public_presence_mode == "NEW_CHANNEL_NO_PUBLIC_FOOTPRINT":
        risks.append(
            {
                "risk_code": "NO_PUBLIC_FOOTPRINT",
                "severity": "MEDIUM",
                "message_vi": "Kênh mới chưa có footprint công khai nên research phải giữ nhiều field UNKNOWN.",
            }
        )
    return risks


def _locked_media_policy() -> dict[str, Any]:
    return {
        "voice_provider": "ElevenLabs",
        "ai_hero_provider": "Google Vertex Veo",
        "ai_hero_model_id": "veo-3.1-fast-generate-001",
        "ai_hero_allowed_durations_seconds": [4, 6, 8],
        "ai_hero_default_duration_seconds": 8,
        "ai_hero_audio": False,
        "ai_hero_allowed_use": ["hero_shot", "hard_to_find_visual"],
        "ai_hero_forbidden_use": ["data_diagram", "workflow_chart", "factual_evidence_visualization"],
        "renderer": "Creatomate Growth 10K",
        "storage_archive": "Google Drive",
        "drive_offload_enabled": True,
    }


def _locked_rights_policy() -> dict[str, Any]:
    return {
        "source_manifest_required": True,
        "rights_evidence_required": True,
        "ai_disclosure_required_when_ai_media_used": True,
        "synthetic_media_warning_when_applicable": True,
        "music_policy": "approved_licensed_audio_library_safe_only",
        "reused_content_sensitivity": "medium",
    }


def _unique_channel_key(session: Session, company_id: uuid.UUID, channel_name: str, draft_id: uuid.UUID) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", channel_name.lower()).strip("-") or "channel"
    candidate = base[:64]
    existing = session.scalars(
        select(ChannelWorkspace.key).where(ChannelWorkspace.company_id == company_id)
    ).all()
    if candidate not in set(existing):
        return candidate
    return f"{candidate[:52]}-{str(draft_id)[:8]}"


def _get_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    current: dict[str, Any] = payload
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _is_unknown(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or str(value).upper() in {"UNKNOWN", "NEEDS_HUMAN_CONFIRMATION"}


def _contains_any(value: str, needles: list[str]) -> bool:
    lowered = f" {value.lower()} "
    return any(needle.lower() in lowered for needle in needles)


def _bounded_summary(value: str | None, *, max_length: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length].rstrip()


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"\n|,", str(value)) if part.strip()]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
