from app.api.routes.imports import (
    Any,
)

def _company(company: Any) -> dict[str, Any]:
    return {
        "id": company.id,
        "name": company.name,
        "slug": company.slug,
        "description": company.description,
        "status": company.status,
        "default_currency": company.default_currency,
    }


def _channel(channel: Any) -> dict[str, Any]:
    return {
        "id": channel.id,
        "company_id": channel.company_id,
        "key": channel.key,
        "name": channel.name,
        "status": channel.status,
        "primary_language": channel.primary_language,
        "primary_region": channel.primary_region,
        "primary_timezone": channel.primary_timezone,
        "target_market": channel.target_market,
        "default_timezone": channel.default_timezone,
        "target_subtitle_languages": channel.target_subtitle_languages,
        "target_metadata_languages": channel.target_metadata_languages,
        "target_regions": channel.target_regions,
        "translation_mode": channel.translation_mode,
        "localization_required_for_publish": channel.localization_required_for_publish,
        "localized_metadata_required": channel.localized_metadata_required,
        "active_policy_snapshot_id": channel.active_policy_snapshot_id,
        "metadata": channel.metadata_,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def _channel_init_draft(draft: Any, latest_contract_draft: Any | None) -> dict[str, Any]:
    return {
        "id": draft.id,
        "company_id": draft.company_id,
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
        "workflow_status": draft.workflow_status,
        "contract_status": draft.contract_status,
        "channel_id": draft.channel_id,
        "channel_profile_version_id": draft.channel_profile_version_id,
        "compiled_policy_snapshot_id": draft.compiled_policy_snapshot_id,
        "latest_contract_draft": _channel_contract_draft(latest_contract_draft) if latest_contract_draft else None,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _channel_contract_draft(draft: Any) -> dict[str, Any]:
    return {
        "id": draft.id,
        "init_draft_id": draft.init_draft_id,
        "company_id": draft.company_id,
        "channel_name": draft.channel_name,
        "source_urls": draft.source_urls,
        "admin_minimal_input": draft.admin_minimal_input,
        "suggested_channel_contract": draft.suggested_channel_contract,
        "field_source_map_json": draft.field_source_map_json,
        "confidence_summary": draft.confidence_summary,
        "missing_fields": draft.missing_fields,
        "human_questions": draft.human_questions,
        "risks": draft.risks,
        "evidence_refs": draft.evidence_refs,
        "workflow_status": draft.workflow_status,
        "contract_status": draft.contract_status,
        "review_decision_log_json": draft.review_decision_log_json,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _membership(membership: Any) -> dict[str, Any]:
    return {
        "id": membership.id,
        "channel_workspace_id": membership.channel_workspace_id,
        "user_id": membership.user_id,
        "role_id": membership.role_id,
        "status": membership.status,
        "created_at": membership.created_at,
    }


def _profile(profile: Any) -> dict[str, Any]:
    return {
        "id": profile.id,
        "channel_workspace_id": profile.channel_workspace_id,
        "version": profile.version,
        "status": profile.status,
        "profile_input": profile.profile_input,
        "profile_input_hash": profile.profile_input_hash,
        "source_template_key": profile.source_template_key,
        "source_template_version": profile.source_template_version,
        "created_by": profile.created_by,
        "approved_by": profile.approved_by,
        "approved_at": profile.approved_at,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "channel_profile_version_id": snapshot.channel_profile_version_id,
        "compile_run_id": snapshot.compile_run_id,
        "snapshot_version": snapshot.snapshot_version,
        "status": snapshot.status,
        "compiler_version": snapshot.compiler_version,
        "capability_matrix_version": snapshot.capability_matrix_version,
        "compiled_payload": snapshot.compiled_payload,
        "content_hash": snapshot.content_hash,
        "profile_input_hash": snapshot.profile_input_hash,
        "activated_at": snapshot.activated_at,
        "created_at": snapshot.created_at,
    }


def _snapshot_with_contract_state(snapshot: Any) -> dict[str, Any]:
    payload = snapshot.compiled_payload if snapshot is not None else {}
    contract = payload.get("channel_contract_json") if isinstance(payload, dict) and isinstance(payload.get("channel_contract_json"), dict) else {}
    status_value = contract.get("contract_status") or payload.get("contract_status") if isinstance(payload, dict) else "MISSING"
    missing_fields = contract.get("missing_fields") or payload.get("missing_fields") if isinstance(payload, dict) else []
    contradiction_reasons = contract.get("contradiction_reasons") or payload.get("contradiction_reasons") if isinstance(payload, dict) else []
    return {
        **_snapshot(snapshot),
        "channel_contract_json": contract or None,
        "compiled_policy_snapshot_json": payload.get("compiled_policy_snapshot_json") if isinstance(payload, dict) else None,
        "contract_status": status_value or "MISSING",
        "missing_fields": missing_fields or [],
        "contradiction_reasons": contradiction_reasons or [],
        "next_action": contract.get("next_action")
        or (
            "Kích hoạt kênh." if status_value == "COMPLETE" else "Bổ sung hồ sơ kênh và compile lại policy snapshot."
        ),
    }


def _video_project(project: Any) -> dict[str, Any]:
    return {
        "id": project.id,
        "company_id": project.company_id,
        "channel_workspace_id": project.channel_workspace_id,
        "policy_snapshot_id": project.policy_snapshot_id,
        "category_id": project.category_id,
        "character_binding_id": project.character_binding_id,
        "channel_contract_content_hash": project.channel_contract_content_hash,
        "effective_context_snapshot_id": project.effective_context_snapshot_id,
        "title": project.title,
        "description": project.description,
        "status": project.status,
        "project_type": project.project_type,
        "priority": project.priority,
        "owner_user_id": project.owner_user_id,
        "created_by_user_id": project.created_by_user_id,
        "financial_summary": project.financial_summary,
        "brand_safety_summary": project.brand_safety_summary,
        "legal_compliance_summary": project.legal_compliance_summary,
        "audience_delivery_summary": project.audience_delivery_summary,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }

def _artifact(artifact: Any) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "video_project_id": artifact.video_project_id,
        "artifact_type": artifact.artifact_type,
        "current_version_id": artifact.current_version_id,
        "status": artifact.status,
        "created_by_user_id": artifact.created_by_user_id,
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
    }

def _artifact_version(version: Any) -> dict[str, Any]:
    return {
        "id": version.id,
        "artifact_id": version.artifact_id,
        "version_number": version.version_number,
        "parent_version_id": version.parent_version_id,
        "content": version.content,
        "content_hash": version.content_hash,
        "status": version.status,
        "created_by_user_id": version.created_by_user_id,
        "external_entity_refs": version.external_entity_refs,
        "packaging_metadata": version.packaging_metadata,
        "media_qc_metadata": version.media_qc_metadata,
        "source_manifest": version.source_manifest,
        "evidence_refs": version.evidence_refs,
        "context_refs": version.context_refs,
        "claim_refs": version.claim_refs,
        "retrieval_plan_ref": version.retrieval_plan_ref,
        "created_at": version.created_at,
    }

def _review_task(review_task: Any) -> dict[str, Any]:
    return {
        "id": review_task.id,
        "video_project_id": review_task.video_project_id,
        "target_type": review_task.target_type,
        "target_id": review_task.target_id,
        "target_artifact_version_id": review_task.target_artifact_version_id,
        "review_type": review_task.review_type,
        "status": review_task.status,
        "assigned_to_user_id": review_task.assigned_to_user_id,
        "requested_by_user_id": review_task.requested_by_user_id,
        "due_at": review_task.due_at,
        "review_reason_codes": review_task.review_reason_codes,
        "evidence_required": review_task.evidence_required,
        "evidence_refs": review_task.evidence_refs,
        "review_scope": review_task.review_scope,
        "context_pack_ref": review_task.context_pack_ref,
        "created_at": review_task.created_at,
        "updated_at": review_task.updated_at,
    }

def _review_finding(finding: Any) -> dict[str, Any]:
    return {
        "id": finding.id,
        "review_task_id": finding.review_task_id,
        "severity": finding.severity,
        "reason_code": finding.reason_code,
        "finding_text": finding.finding_text,
        "evidence_refs": finding.evidence_refs,
        "created_by_user_id": finding.created_by_user_id,
        "created_at": finding.created_at,
    }

def _revision_request(revision: Any) -> dict[str, Any]:
    return {
        "id": revision.id,
        "review_task_id": revision.review_task_id,
        "target_artifact_version_id": revision.target_artifact_version_id,
        "requested_by_user_id": revision.requested_by_user_id,
        "reason": revision.reason,
        "status": revision.status,
        "resolved_by_artifact_version_id": revision.resolved_by_artifact_version_id,
        "created_at": revision.created_at,
        "resolved_at": revision.resolved_at,
    }

def _approval_decision(decision: Any) -> dict[str, Any]:
    return {
        "id": decision.id,
        "target_type": decision.target_type,
        "target_id": decision.target_id,
        "target_artifact_version_id": decision.target_artifact_version_id,
        "decision": decision.decision,
        "decided_by_user_id": decision.decided_by_user_id,
        "decided_at": decision.decided_at,
        "rationale": decision.rationale,
        "metadata": decision.metadata_,
        "decision_basis": decision.decision_basis,
        "evidence_basis": decision.evidence_basis,
        "policy_basis": decision.policy_basis,
        "context_pack_ref": decision.context_pack_ref,
        "human_decision_note": decision.human_decision_note,
        "created_at": decision.created_at,
    }

def _gate_run(gate_run: Any) -> dict[str, Any]:
    return {
        "id": gate_run.id,
        "gate_definition_version_id": gate_run.gate_definition_version_id,
        "gate_key": gate_run.gate_key,
        "target_type": gate_run.target_type,
        "target_id": gate_run.target_id,
        "video_project_id": gate_run.video_project_id,
        "artifact_version_id": gate_run.artifact_version_id,
        "review_task_id": gate_run.review_task_id,
        "policy_snapshot_id": gate_run.policy_snapshot_id,
        "input_snapshot": gate_run.input_snapshot,
        "input_snapshot_hash": gate_run.input_snapshot_hash,
        "result": gate_run.result,
        "reason_codes": gate_run.reason_codes,
        "evidence_refs": gate_run.evidence_refs,
        "metric_refs": gate_run.metric_refs,
        "freshness_state": gate_run.freshness_state,
        "confidence_level": gate_run.confidence_level,
        "confidence_reason_codes": gate_run.confidence_reason_codes,
        "decision_basis": gate_run.decision_basis,
        "created_review_task_id": gate_run.created_review_task_id,
        "created_by_user_id": gate_run.created_by_user_id,
        "created_at": gate_run.created_at,
    }

def _policy_catalog(catalog: Any) -> dict[str, Any]:
    return {
        "id": catalog.id,
        "catalog_key": catalog.catalog_key,
        "platform": catalog.platform,
        "policy_domain": catalog.policy_domain,
        "current_version_id": catalog.current_version_id,
        "status": catalog.status,
        "created_at": catalog.created_at,
        "updated_at": catalog.updated_at,
    }

def _policy_version(version: Any) -> dict[str, Any]:
    return {
        "id": version.id,
        "catalog_id": version.catalog_id,
        "version": version.version,
        "status": version.status,
        "effective_at": version.effective_at,
        "observed_at": version.observed_at,
        "policy_blob": version.policy_blob,
        "interpretation_notes": version.interpretation_notes,
        "created_by_user_id": version.created_by_user_id,
        "created_at": version.created_at,
        "activated_at": version.activated_at,
        "superseded_at": version.superseded_at,
    }

def _policy_source_ref(ref: Any) -> dict[str, Any]:
    return {
        "id": ref.id,
        "policy_version_id": ref.policy_version_id,
        "policy_change_record_id": ref.policy_change_record_id,
        "source_type": ref.source_type,
        "source_title": ref.source_title,
        "source_url": ref.source_url,
        "captured_at": ref.captured_at,
        "reliability": ref.reliability,
        "notes": ref.notes,
        "created_at": ref.created_at,
    }

def _policy_change_record(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "change_key": record.change_key,
        "platform": record.platform,
        "policy_domain": record.policy_domain,
        "state": record.state,
        "summary": record.summary,
        "old_policy_version_id": record.old_policy_version_id,
        "new_policy_version_id": record.new_policy_version_id,
        "impact_classification": record.impact_classification,
        "diff_summary": record.diff_summary,
        "affected_gate_keys": record.affected_gate_keys,
        "affected_domains": record.affected_domains,
        "requires_revalidation": record.requires_revalidation,
        "rollback_available": record.rollback_available,
        "created_by_user_id": record.created_by_user_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }

def _policy_revalidation_batch(batch: Any) -> dict[str, Any]:
    return {
        "id": batch.id,
        "policy_change_record_id": batch.policy_change_record_id,
        "gate_definition_version_id": batch.gate_definition_version_id,
        "scope": batch.scope,
        "status": batch.status,
        "counts": batch.counts,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
        "created_by_user_id": batch.created_by_user_id,
        "created_at": batch.created_at,
    }

def _provider_registry_entry(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "provider_key": entry.provider_key,
        "provider_name": entry.provider_name,
        "provider_type": entry.provider_type,
        "status": entry.status,
        "capability_blob": entry.capability_blob,
        "policy_fit_blob": entry.policy_fit_blob,
        "cost_model_blob": entry.cost_model_blob,
        "quota_model_blob": entry.quota_model_blob,
        "retry_policy_blob": entry.retry_policy_blob,
        "metadata": entry.metadata_,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }

def _credential_reference(reference: Any) -> dict[str, Any]:
    return {
        "id": reference.id,
        "provider_key": reference.provider_key,
        "credential_key": reference.credential_key,
        "credential_type": reference.credential_type,
        "secret_ref": reference.secret_ref,
        "scope_blob": reference.scope_blob,
        "status": reference.status,
        "expires_at": reference.expires_at,
        "last_checked_at": reference.last_checked_at,
        "metadata": reference.metadata_,
        "created_at": reference.created_at,
        "updated_at": reference.updated_at,
    }

def _credential_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "credential_reference_id": snapshot.credential_reference_id,
        "provider_key": snapshot.provider_key,
        "health_state": snapshot.health_state,
        "checked_at": snapshot.checked_at,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _quota_account(account: Any) -> dict[str, Any]:
    return {
        "id": account.id,
        "provider_key": account.provider_key,
        "quota_scope_type": account.quota_scope_type,
        "quota_scope_id": account.quota_scope_id,
        "quota_window": account.quota_window,
        "quota_limit": account.quota_limit,
        "quota_used": account.quota_used,
        "quota_reserved": account.quota_reserved,
        "unit": account.unit,
        "reset_at": account.reset_at,
        "status": account.status,
        "metadata": account.metadata_,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }

def _quota_event(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "quota_account_id": event.quota_account_id,
        "provider_key": event.provider_key,
        "event_type": event.event_type,
        "amount": event.amount,
        "unit": event.unit,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "reason_code": event.reason_code,
        "metadata": event.metadata_,
        "created_at": event.created_at,
    }

def _cost_event(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "provider_key": event.provider_key,
        "cost_scope_type": event.cost_scope_type,
        "cost_scope_id": event.cost_scope_id,
        "amount": event.amount,
        "currency": event.currency,
        "cost_type": event.cost_type,
        "unit_count": event.unit_count,
        "unit_type": event.unit_type,
        "provider_run_ref": event.provider_run_ref,
        "metadata": event.metadata_,
        "created_at": event.created_at,
    }

def _budget_policy(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "policy_key": policy.policy_key,
        "scope_type": policy.scope_type,
        "scope_id": policy.scope_id,
        "policy_blob": policy.policy_blob,
        "status": policy.status,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }

def _provider_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "provider_key": snapshot.provider_key,
        "provider_type": snapshot.provider_type,
        "health_state": snapshot.health_state,
        "checked_at": snapshot.checked_at,
        "latency_ms": snapshot.latency_ms,
        "error_rate": snapshot.error_rate,
        "quota_state": snapshot.quota_state,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _component_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "component_type": snapshot.component_type,
        "component_key": snapshot.component_key,
        "health_state": snapshot.health_state,
        "checked_at": snapshot.checked_at,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _system_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "captured_at": snapshot.captured_at,
        "overall_state": snapshot.overall_state,
        "component_counts": snapshot.component_counts,
        "active_incident_count": snapshot.active_incident_count,
        "action_required": snapshot.action_required,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _retry_policy(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "policy_key": policy.policy_key,
        "provider_key": policy.provider_key,
        "target_type": policy.target_type,
        "policy_blob": policy.policy_blob,
        "status": policy.status,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }

def _provider_attempt(attempt: Any) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "provider_key": attempt.provider_key,
        "operation_key": attempt.operation_key,
        "target_type": attempt.target_type,
        "target_id": attempt.target_id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "error_code": attempt.error_code,
        "error_message_redacted": attempt.error_message_redacted,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "latency_ms": attempt.latency_ms,
        "cost_event_id": attempt.cost_event_id,
        "quota_event_id": attempt.quota_event_id,
        "metadata": attempt.metadata_,
    }

def _dead_letter_job(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "queue_name": job.queue_name,
        "job_type": job.job_type,
        "payload_ref": job.payload_ref,
        "target_type": job.target_type,
        "target_id": job.target_id,
        "fail_count": job.fail_count,
        "first_failed_at": job.first_failed_at,
        "last_failed_at": job.last_failed_at,
        "replay_state": job.replay_state,
        "reason_code": job.reason_code,
        "next_action": job.next_action,
        "metadata": job.metadata_,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

def _ops_incident(incident: Any) -> dict[str, Any]:
    return {
        "id": incident.id,
        "incident_type": incident.incident_type,
        "severity": incident.severity,
        "state": incident.state,
        "impacted_refs": incident.impacted_refs,
        "reason_codes": incident.reason_codes,
        "next_action": incident.next_action,
        "owner_user_id": incident.owner_user_id,
        "opened_at": incident.opened_at,
        "acknowledged_at": incident.acknowledged_at,
        "resolved_at": incident.resolved_at,
        "metadata": incident.metadata_,
        "created_at": incident.created_at,
        "updated_at": incident.updated_at,
    }

def _manual_action(action: Any) -> dict[str, Any]:
    return {
        "id": action.id,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
        "priority": action.priority,
        "state": action.state,
        "reason_code": action.reason_code,
        "next_action": action.next_action,
        "assignee_user_id": action.assignee_user_id,
        "due_at": action.due_at,
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }

def _editorial_slot(slot: Any) -> dict[str, Any]:
    return {
        "id": slot.id,
        "company_id": slot.company_id,
        "channel_workspace_id": slot.channel_workspace_id,
        "policy_snapshot_id": slot.policy_snapshot_id,
        "category_id": slot.category_id,
        "slot_date": slot.slot_date,
        "slot_type": slot.slot_type,
        "status": slot.status,
        "schema_version": slot.schema_version,
        "production_lane": slot.production_lane,
        "assignment_mode": slot.assignment_mode,
        "preferred_series_plan_id": slot.preferred_series_plan_id,
        "preferred_series_run_id": slot.preferred_series_run_id,
        "production_goal": slot.production_goal,
        "target_platforms": slot.target_platforms,
        "content_pillar": slot.content_pillar,
        "series_key": slot.series_key,
        "format_hint": slot.format_hint,
        "character_binding_policy_json": slot.character_binding_policy_json,
        "risk_level": slot.risk_level,
        "operational_envelope": slot.operational_envelope,
        "created_by_user_id": slot.created_by_user_id,
        "created_at": slot.created_at,
        "updated_at": slot.updated_at,
    }

def _channel_daily_run(daily_run: Any) -> dict[str, Any]:
    return {
        "id": daily_run.id,
        "company_id": daily_run.company_id,
        "channel_workspace_id": daily_run.channel_workspace_id,
        "policy_snapshot_id": daily_run.policy_snapshot_id,
        "editorial_calendar_slot_id": daily_run.editorial_calendar_slot_id,
        "run_date": daily_run.run_date,
        "status": daily_run.status,
        "run_mode": daily_run.run_mode,
        "trigger_type": daily_run.trigger_type,
        "started_at": daily_run.started_at,
        "completed_at": daily_run.completed_at,
        "context_pack_snapshot_id": daily_run.context_pack_snapshot_id,
        "channel_state_pack_snapshot_id": daily_run.channel_state_pack_snapshot_id,
        "daily_idea_decision_id": daily_run.daily_idea_decision_id,
        "project_admission_decision_id": daily_run.project_admission_decision_id,
        "reason_codes": daily_run.reason_codes,
        "metadata": daily_run.metadata_,
        "created_at": daily_run.created_at,
        "updated_at": daily_run.updated_at,
    }

def _retrieval_plan(plan: Any) -> dict[str, Any]:
    return {
        "id": plan.id,
        "purpose": plan.purpose,
        "company_id": plan.company_id,
        "channel_workspace_id": plan.channel_workspace_id,
        "channel_profile_version_id": plan.channel_profile_version_id,
        "policy_snapshot_id": plan.policy_snapshot_id,
        "video_project_id": plan.video_project_id,
        "editorial_calendar_slot_id": plan.editorial_calendar_slot_id,
        "allowed_sources": plan.allowed_sources,
        "excluded_sources": plan.excluded_sources,
        "redaction_rules": plan.redaction_rules,
        "token_budget": plan.token_budget,
        "source_order": plan.source_order,
        "plan_hash": plan.plan_hash,
        "created_by_user_id": plan.created_by_user_id,
        "created_at": plan.created_at,
    }

def _context_pack(pack: Any) -> dict[str, Any]:
    return {
        "id": pack.id,
        "retrieval_plan_snapshot_id": pack.retrieval_plan_snapshot_id,
        "purpose": pack.purpose,
        "company_id": pack.company_id,
        "channel_workspace_id": pack.channel_workspace_id,
        "channel_profile_version_id": pack.channel_profile_version_id,
        "policy_snapshot_id": pack.policy_snapshot_id,
        "video_project_id": pack.video_project_id,
        "editorial_calendar_slot_id": pack.editorial_calendar_slot_id,
        "input_refs": pack.input_refs,
        "policy_refs": pack.policy_refs,
        "evidence_refs": pack.evidence_refs,
        "metric_refs": pack.metric_refs,
        "memory_refs": pack.memory_refs,
        "pack_content": pack.pack_content,
        "freshness_state": pack.freshness_state,
        "confidence_level": pack.confidence_level,
        "pack_hash": pack.pack_hash,
        "created_by_user_id": pack.created_by_user_id,
        "created_at": pack.created_at,
    }

def _channel_state_pack(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "channel_daily_run_id": snapshot.channel_daily_run_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "policy_snapshot_id": snapshot.policy_snapshot_id,
        "context_pack_snapshot_id": snapshot.context_pack_snapshot_id,
        "state_blob": snapshot.state_blob,
        "active_project_refs": snapshot.active_project_refs,
        "pending_review_refs": snapshot.pending_review_refs,
        "readiness_summary": snapshot.readiness_summary,
        "provider_health_summary": snapshot.provider_health_summary,
        "quota_summary": snapshot.quota_summary,
        "evidence_summary": snapshot.evidence_summary,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "state_hash": snapshot.state_hash,
        "created_at": snapshot.created_at,
    }

def _search_demand_evidence(evidence: Any) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "company_id": evidence.company_id,
        "channel_workspace_id": evidence.channel_workspace_id,
        "evidence_source_type": evidence.evidence_source_type,
        "source_ref": evidence.source_ref,
        "query": evidence.query,
        "platform": evidence.platform,
        "geo": evidence.geo,
        "language": evidence.language,
        "lookback_window_days": evidence.lookback_window_days,
        "search_volume_30d": evidence.search_volume_30d,
        "relative_interest_index": evidence.relative_interest_index,
        "competition_index": evidence.competition_index,
        "trending_velocity": evidence.trending_velocity,
        "evidence_confidence": evidence.evidence_confidence,
        "captured_at": evidence.captured_at,
        "metadata": evidence.metadata_,
        "created_at": evidence.created_at,
    }

def _daily_idea_decision(decision: Any) -> dict[str, Any]:
    return {
        "id": decision.id,
        "channel_daily_run_id": decision.channel_daily_run_id,
        "company_id": decision.company_id,
        "channel_workspace_id": decision.channel_workspace_id,
        "policy_snapshot_id": decision.policy_snapshot_id,
        "context_pack_snapshot_id": decision.context_pack_snapshot_id,
        "channel_state_pack_snapshot_id": decision.channel_state_pack_snapshot_id,
        "llm_run_snapshot_id": decision.llm_run_snapshot_id,
        "schema_version": decision.schema_version,
        "production_lane": decision.production_lane,
        "proposed_content_mode": decision.proposed_content_mode,
        "assignment_input_ref": decision.assignment_input_ref,
        "decision_status": decision.decision_status,
        "proposed_title": decision.proposed_title,
        "proposed_angle": decision.proposed_angle,
        "proposed_format": decision.proposed_format,
        "proposed_pillar": decision.proposed_pillar,
        "proposed_series_key": decision.proposed_series_key,
        "rationale": decision.rationale,
        "evidence_refs": decision.evidence_refs,
        "reason_codes": decision.reason_codes,
        "confidence_level": decision.confidence_level,
        "created_at": decision.created_at,
    }

def _idea_market_preflight(preflight: Any) -> dict[str, Any]:
    market = preflight.evidence_blob or {}
    return {
        "id": preflight.id,
        "company_id": preflight.company_id,
        "channel_workspace_id": preflight.channel_workspace_id,
        "editorial_calendar_slot_id": preflight.editorial_calendar_slot_id,
        "channel_daily_run_id": preflight.channel_daily_run_id,
        "daily_idea_decision_id": preflight.daily_idea_decision_id,
        "search_intent_map_id": preflight.search_intent_map_id,
        "audience_target_pack_id": preflight.audience_target_pack_id,
        "demand_score": preflight.demand_score,
        "channel_fit_score": preflight.channel_fit_score,
        "policy_fit_state": preflight.policy_fit_state,
        "niche_contract_digest_ref": market.get("niche_contract_digest_ref"),
        "niche_contract_digest_hash": market.get("niche_contract_digest_hash"),
        "target_market_digest_ref": market.get("target_market_digest_ref"),
        "target_market_digest_hash": market.get("target_market_digest_hash"),
        "editorial_slot_ref": market.get("editorial_slot_ref"),
        "content_category_ref": market.get("content_category_ref"),
        "target_market": market.get("target_market"),
        "market_scope": market.get("market_scope", []),
        "market_fit_score": market.get("market_fit_score"),
        "market_fit_threshold": market.get("market_fit_threshold"),
        "confidence_state": preflight.confidence_state,
        "evidence_blob": preflight.evidence_blob,
        "reason_codes": preflight.reason_codes,
        "decision": preflight.decision,
        "created_at": preflight.created_at,
    }

def _project_admission_decision(decision: Any) -> dict[str, Any]:
    return {
        "id": decision.id,
        "schema_version": decision.schema_version,
        "channel_daily_run_id": decision.channel_daily_run_id,
        "daily_idea_decision_id": decision.daily_idea_decision_id,
        "editorial_calendar_slot_id": decision.editorial_calendar_slot_id,
        "company_id": decision.company_id,
        "channel_workspace_id": decision.channel_workspace_id,
        "channel_profile_version_id": decision.channel_profile_version_id,
        "policy_snapshot_id": decision.policy_snapshot_id,
        "idea_market_preflight_id": decision.idea_market_preflight_id,
        "category_id": None,
        "character_binding_id": None,
        "budget_policy_key": decision.budget_gate_result.get("policy_key"),
        "quota_account_id": None,
        "estimated_cost": 0,
        "created_by_user_id": decision.created_by_user_id,
        "planning_source_type": decision.planning_source_type,
        "production_lane": decision.production_lane,
        "content_mode": decision.content_mode,
        "assignment_mode": decision.assignment_mode,
        "series_plan_id": decision.series_plan_id,
        "series_run_id": decision.series_run_id,
        "episode_number": decision.episode_number,
        "episode_role": decision.episode_role,
        "standalone_reason_code": decision.standalone_reason_code,
        "parent_video_project_id": decision.parent_video_project_id,
        "parent_final_media_ref_id": decision.parent_final_media_ref_id,
        "canonical_timeline_ref": decision.canonical_timeline_ref,
        "canonical_timeline_hash": decision.canonical_timeline_hash,
        "resolver_version": decision.resolver_version,
        "resolver_input_hash": decision.resolver_input_hash,
        "decision_hash": decision.decision_hash,
        "assignment_input_ref": decision.assignment_input_ref,
        "duration_contract": decision.duration_contract,
        "budget_gate_result": decision.budget_gate_result,
        "readiness_gate_refs": decision.readiness_gate_refs,
        "decision": decision.decision,
        "reason_codes": decision.reason_codes,
        "evidence_refs": decision.evidence_refs,
        "admitted_video_project_id": decision.admitted_video_project_id,
        "created_artifact_refs": decision.created_artifact_refs,
        "created_at": decision.created_at,
    }

def _production_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "video_project_id": run.video_project_id,
        "policy_snapshot_id": run.policy_snapshot_id,
        "source_project_admission_decision_id": run.source_project_admission_decision_id,
        "run_mode": run.run_mode,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "script_artifact_version_id": run.script_artifact_version_id,
        "voice_timeline_snapshot_id": run.voice_timeline_snapshot_id,
        "caption_track_snapshot_id": run.caption_track_snapshot_id,
        "visual_plan_snapshot_id": run.visual_plan_snapshot_id,
        "scene_manifest_snapshot_id": run.scene_manifest_snapshot_id,
        "render_spec_snapshot_id": run.render_spec_snapshot_id,
        "asset_manifest_snapshot_id": run.asset_manifest_snapshot_id,
        "source_manifest_snapshot_id": run.source_manifest_snapshot_id,
        "render_package_snapshot_id": run.render_package_snapshot_id,
        "media_qc_report_id": run.media_qc_report_id,
        "accessibility_qc_report_id": run.accessibility_qc_report_id,
        "reason_codes": run.reason_codes,
        "metadata": run.metadata_,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }

def _render_job(job: Any) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "production_artifact_run_id": str(job.production_artifact_run_id) if job.production_artifact_run_id else None,
        "video_project_id": str(job.video_project_id),
        "render_spec_snapshot_id": str(job.render_spec_snapshot_id),
        "render_variant_id": job.render_variant_id,
        "renderer_key": job.renderer_key,
        "status": job.status,
        "output_ref": job.output_ref,
        "error_code": job.error_code,
        "reason_codes": job.reason_codes,
    }

def _render_package(package: Any) -> dict[str, Any]:
    return {
        "id": str(package.id),
        "production_artifact_run_id": str(package.production_artifact_run_id) if package.production_artifact_run_id else None,
        "video_project_id": str(package.video_project_id),
        "media_render_job_id": str(package.media_render_job_id),
        "render_spec_snapshot_id": str(package.render_spec_snapshot_id),
        "final_video_ref": package.final_video_ref,
        "caption_ref": package.caption_ref,
        "manifest_ref": package.manifest_ref,
        "file_manifest": package.file_manifest,
        "checksum_manifest": package.checksum_manifest,
        "duration_seconds": str(package.duration_seconds) if package.duration_seconds is not None else None,
        "package_state": package.package_state,
    }

def _media_qc_report(report: Any) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "production_artifact_run_id": str(report.production_artifact_run_id) if report.production_artifact_run_id else None,
        "video_project_id": str(report.video_project_id),
        "render_package_snapshot_id": str(report.render_package_snapshot_id) if report.render_package_snapshot_id else None,
        "render_spec_snapshot_id": str(report.render_spec_snapshot_id),
        "qc_state": report.qc_state,
        "reason_codes": report.reason_codes,
        "duration_check": report.duration_check,
        "file_integrity_check": report.file_integrity_check,
        "manifest_check": report.manifest_check,
    }

def _accessibility_qc_report(report: Any) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "production_artifact_run_id": str(report.production_artifact_run_id) if report.production_artifact_run_id else None,
        "video_project_id": str(report.video_project_id),
        "caption_track_snapshot_id": str(report.caption_track_snapshot_id) if report.caption_track_snapshot_id else None,
        "render_package_snapshot_id": str(report.render_package_snapshot_id) if report.render_package_snapshot_id else None,
        "qc_state": report.qc_state,
        "reason_codes": report.reason_codes,
        "caption_presence_check": report.caption_presence_check,
        "caption_readability_check": report.caption_readability_check,
    }

__all__ = [name for name in globals() if not name.startswith("__")]
