from app.api.routes.imports import (
    Any,
    ConflictError,
    ForbiddenError,
    HTTPException,
    LearningReviewDecisionCreate,
    LearningReviewDecisionRead,
    M11LearningReviewService,
    NotFoundError,
    ValidationFailureError,
    learning_review_decision_read,
    session_scope,
    status,
    uuid,
)
from app.core.actor import ActorContext


def _publish_handoff(handoff: Any) -> dict[str, Any]:
    return {
        "id": handoff.id,
        "company_id": handoff.company_id,
        "channel_workspace_id": handoff.channel_workspace_id,
        "video_project_id": handoff.video_project_id,
        "policy_snapshot_id": handoff.policy_snapshot_id,
        "production_artifact_run_id": handoff.production_artifact_run_id,
        "render_package_snapshot_id": handoff.render_package_snapshot_id,
        "render_spec_snapshot_id": handoff.render_spec_snapshot_id,
        "media_qc_report_id": handoff.media_qc_report_id,
        "accessibility_qc_report_id": handoff.accessibility_qc_report_id,
        "source_manifest_snapshot_id": handoff.source_manifest_snapshot_id,
        "asset_manifest_snapshot_id": handoff.asset_manifest_snapshot_id,
        "target_platform": handoff.target_platform,
        "target_surface": handoff.target_surface,
        "destination_binding_id": handoff.destination_binding_id,
        "destination_binding_fingerprint": handoff.destination_binding_fingerprint,
        "market_policy_hash": handoff.market_policy_hash,
        "approved_package_hash": handoff.approved_package_hash,
        "approval_decision_id": handoff.approval_decision_id,
        "target_market_profile_ref": handoff.target_market_profile_ref,
        "target_market_profile_hash": handoff.target_market_profile_hash,
        "market_alignment_dossier_ref": handoff.market_alignment_dossier_ref,
        "market_alignment_dossier_hash": handoff.market_alignment_dossier_hash,
        "approved_publish_timezone": handoff.approved_publish_timezone,
        "approved_publish_window": handoff.approved_publish_window,
        "render_variant_id": handoff.render_variant_id,
        "package_state": handoff.package_state,
        "planned_metadata": handoff.planned_metadata,
        "planned_disclosures": handoff.planned_disclosures,
        "planned_files": handoff.planned_files,
        "cloud_media_refs": handoff.cloud_media_refs,
        "checklist_snapshot": handoff.checklist_snapshot,
        "operator_instructions": handoff.operator_instructions,
        "risk_summary": handoff.risk_summary,
        "reason_codes": handoff.reason_codes,
        "next_action": handoff.next_action,
        "created_by_user_id": handoff.created_by_user_id,
        "created_at": handoff.created_at,
        "updated_at": handoff.updated_at,
    }


def _manual_publish_confirmation(confirmation: Any) -> dict[str, Any]:
    return {
        "id": confirmation.id,
        "publish_handoff_package_id": confirmation.publish_handoff_package_id,
        "company_id": confirmation.company_id,
        "channel_workspace_id": confirmation.channel_workspace_id,
        "video_project_id": confirmation.video_project_id,
        "policy_snapshot_id": confirmation.policy_snapshot_id,
        "target_platform": confirmation.target_platform,
        "target_surface": confirmation.target_surface,
        "confirmed_by_user_id": confirmation.confirmed_by_user_id,
        "confirmation_state": confirmation.confirmation_state,
        "actual_video_id": confirmation.actual_video_id,
        "actual_video_url": confirmation.actual_video_url,
        "actual_published_at": confirmation.actual_published_at,
        "destination_binding_id": confirmation.destination_binding_id,
        "destination_binding_fingerprint": confirmation.destination_binding_fingerprint,
        "market_policy_hash": confirmation.market_policy_hash,
        "approved_package_hash": confirmation.approved_package_hash,
        "actual_metadata": confirmation.actual_metadata,
        "actual_disclosures": confirmation.actual_disclosures,
        "actual_files": confirmation.actual_files,
        "operator_notes": confirmation.operator_notes,
        "validation_summary": confirmation.validation_summary,
        "metadata_diff": confirmation.metadata_diff,
        "reason_codes": confirmation.reason_codes,
        "next_action": confirmation.next_action,
        "created_at": confirmation.created_at,
        "updated_at": confirmation.updated_at,
    }


def _uploaded_video(uploaded: Any) -> dict[str, Any]:
    return {
        "id": uploaded.id,
        "company_id": uploaded.company_id,
        "channel_workspace_id": uploaded.channel_workspace_id,
        "video_project_id": uploaded.video_project_id,
        "policy_snapshot_id": uploaded.policy_snapshot_id,
        "publish_handoff_package_id": uploaded.publish_handoff_package_id,
        "manual_publish_confirmation_id": uploaded.manual_publish_confirmation_id,
        "render_package_snapshot_id": uploaded.render_package_snapshot_id,
        "first_scripted_video_package_id": uploaded.first_scripted_video_package_id,
        "human_upload_task_id": uploaded.human_upload_task_id,
        "destination": uploaded.destination,
        "destination_binding_id": uploaded.destination_binding_id,
        "destination_binding_fingerprint": uploaded.destination_binding_fingerprint,
        "market_policy_hash": uploaded.market_policy_hash,
        "approved_package_hash": uploaded.approved_package_hash,
        "source_manifest_snapshot_id": uploaded.source_manifest_snapshot_id,
        "rights_envelope_ref": uploaded.rights_envelope_ref,
        "platform": uploaded.platform,
        "platform_video_id": uploaded.platform_video_id,
        "video_url": uploaded.video_url,
        "external_video_id": uploaded.platform_video_id,
        "external_url": uploaded.video_url,
        "published_at": uploaded.published_at,
        "publish_status": uploaded.publish_status,
        "actual_metadata": uploaded.actual_metadata,
        "actual_disclosures": uploaded.actual_disclosures,
        "lineage_refs": uploaded.lineage_refs,
        "monitoring_state": uploaded.monitoring_state,
        "operator_summary": uploaded.operator_summary,
        "actual_title": uploaded.actual_title,
        "actual_visibility": uploaded.actual_visibility,
        "actual_publish_time": uploaded.actual_publish_time,
        "actual_upload_time": uploaded.actual_upload_time,
        "playlist_id": uploaded.playlist_id,
        "thumbnail_uploaded": uploaded.thumbnail_uploaded,
        "subtitles_uploaded": uploaded.subtitles_uploaded,
        "description_modified_from_package": uploaded.description_modified_from_package,
        "package_metadata_diff": uploaded.package_metadata_diff,
        "verification_status": uploaded.verification_status,
        "analytics_sync_status": uploaded.analytics_sync_status,
        "last_verified_at": uploaded.last_verified_at,
        "last_analytics_sync_at": uploaded.last_analytics_sync_at,
        "operator_note": uploaded.operator_note,
        "created_at": uploaded.created_at,
        "updated_at": uploaded.updated_at,
    }


def _uploaded_video_summary(summary: Any) -> dict[str, Any]:
    return {
        "id": summary.id,
        "uploaded_video_id": summary.uploaded_video_id,
        "company_id": summary.company_id,
        "channel_workspace_id": summary.channel_workspace_id,
        "video_project_id": summary.video_project_id,
        "platform": summary.platform,
        "platform_video_id": summary.platform_video_id,
        "video_url": summary.video_url,
        "published_at": summary.published_at,
        "title": summary.title,
        "publish_status": summary.publish_status,
        "monitoring_state": summary.monitoring_state,
        "operator_status": summary.operator_status,
        "operator_summary": summary.operator_summary,
        "next_action": summary.next_action,
        "freshness_state": summary.freshness_state,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
    }


def _analytics_sync_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "uploaded_video_id": run.uploaded_video_id,
        "video_project_id": run.video_project_id,
        "policy_snapshot_id": run.policy_snapshot_id,
        "platform": run.platform,
        "platform_video_id": run.platform_video_id,
        "sync_mode": run.sync_mode,
        "sync_state": run.sync_state,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "observed_from": run.observed_from,
        "observed_to": run.observed_to,
        "provider_key": run.provider_key,
        "provider_attempt_id": run.provider_attempt_id,
        "analytics_snapshot_id": run.analytics_snapshot_id,
        "reason_codes": run.reason_codes,
        "next_action": run.next_action,
        "metadata": run.metadata_,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _analytics_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "analytics_sync_run_id": snapshot.analytics_sync_run_id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "video_project_id": snapshot.video_project_id,
        "policy_snapshot_id": snapshot.policy_snapshot_id,
        "platform": snapshot.platform,
        "platform_video_id": snapshot.platform_video_id,
        "captured_at": snapshot.captured_at,
        "observed_from": snapshot.observed_from,
        "observed_to": snapshot.observed_to,
        "observation_window": snapshot.observation_window,
        "metrics_blob": snapshot.metrics_blob,
        "normalized_metrics_blob": snapshot.normalized_metrics_blob,
        "metric_availability": snapshot.metric_availability,
        "source_metadata": snapshot.source_metadata,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "reason_codes": snapshot.reason_codes,
        "created_at": snapshot.created_at,
    }


def _traffic_source_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "analytics_snapshot_id": snapshot.analytics_snapshot_id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "platform": snapshot.platform,
        "platform_video_id": snapshot.platform_video_id,
        "captured_at": snapshot.captured_at,
        "traffic_sources": snapshot.traffic_sources,
        "source_summary": snapshot.source_summary,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "created_at": snapshot.created_at,
    }


def _retention_curve_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "analytics_snapshot_id": snapshot.analytics_snapshot_id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "video_project_id": snapshot.video_project_id,
        "render_package_snapshot_id": snapshot.render_package_snapshot_id,
        "platform": snapshot.platform,
        "platform_video_id": snapshot.platform_video_id,
        "captured_at": snapshot.captured_at,
        "curve_points": snapshot.curve_points,
        "curve_summary": snapshot.curve_summary,
        "duration_seconds": snapshot.duration_seconds,
        "timeline_alignment": snapshot.timeline_alignment,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "created_at": snapshot.created_at,
    }


def _uploaded_video_metrics_summary(summary: Any) -> dict[str, Any]:
    return {
        "id": summary.id,
        "uploaded_video_id": summary.uploaded_video_id,
        "company_id": summary.company_id,
        "channel_workspace_id": summary.channel_workspace_id,
        "video_project_id": summary.video_project_id,
        "platform": summary.platform,
        "platform_video_id": summary.platform_video_id,
        "latest_analytics_snapshot_id": summary.latest_analytics_snapshot_id,
        "latest_retention_curve_snapshot_id": summary.latest_retention_curve_snapshot_id,
        "latest_traffic_source_snapshot_id": summary.latest_traffic_source_snapshot_id,
        "latest_engagement_snapshot_id": summary.latest_engagement_snapshot_id,
        "latest_captured_at": summary.latest_captured_at,
        "metrics_summary": summary.metrics_summary,
        "availability_summary": summary.availability_summary,
        "freshness_state": summary.freshness_state,
        "confidence_level": summary.confidence_level,
        "monitoring_state": summary.monitoring_state,
        "operator_summary": summary.operator_summary,
        "next_action": summary.next_action,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
    }


def _youtube_oauth_session(session: Any) -> dict[str, Any]:
    return {
        "id": session.id,
        "company_id": session.company_id,
        "channel_workspace_id": session.channel_workspace_id,
        "redirect_uri": session.redirect_uri,
        "scopes": session.scopes,
        "status": session.status,
        "credential_reference_id": session.credential_reference_id,
        "error_code": session.error_code,
        "error_message": session.error_message,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _google_drive_oauth_session(session: Any) -> dict[str, Any]:
    return {
        "id": session.id,
        "company_id": session.company_id,
        "channel_workspace_id": session.channel_workspace_id,
        "redirect_uri": session.redirect_uri,
        "scopes": session.scopes,
        "status": session.status,
        "credential_reference_id": session.credential_reference_id,
        "error_code": session.error_code,
        "error_message": session.error_message,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _media_offload_job(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "company_id": job.company_id,
        "channel_workspace_id": job.channel_workspace_id,
        "video_project_id": job.video_project_id,
        "uploaded_video_id": job.uploaded_video_id,
        "source_media_ref_id": job.source_media_ref_id,
        "render_package_id": job.render_package_id,
        "local_source_path_hash": job.local_source_path_hash,
        "target_provider": job.target_provider,
        "target_folder_policy": job.target_folder_policy,
        "target_media_type": job.target_media_type,
        "job_state": job.job_state,
        "cloud_media_ref_id": job.cloud_media_ref_id,
        "retry_count": job.retry_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _local_media_retention_policy(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "company_id": policy.company_id,
        "channel_workspace_id": policy.channel_workspace_id,
        "keep_local_after_upload": policy.keep_local_after_upload,
        "cleanup_after_verified": policy.cleanup_after_verified,
        "max_local_age_hours": policy.max_local_age_hours,
        "max_local_storage_gb": policy.max_local_storage_gb,
        "protected_paths": policy.protected_paths,
        "allowed_cleanup_roots": policy.allowed_cleanup_roots,
        "state": policy.state,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }


def _youtube_public_sync_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_video_id": run.uploaded_video_id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "platform_video_id": run.platform_video_id,
        "run_state": run.run_state,
        "source": run.source,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "http_status": run.http_status,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "metrics_found": run.metrics_found,
        "created_snapshot_id": run.created_snapshot_id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _youtube_owner_sync_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_video_id": run.uploaded_video_id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "platform_video_id": run.platform_video_id,
        "credential_reference_id": run.credential_reference_id,
        "run_state": run.run_state,
        "source": run.source,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "http_status": run.http_status,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "metrics_found": run.metrics_found,
        "created_snapshot_id": run.created_snapshot_id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _youtube_public_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "platform_video_id": snapshot.platform_video_id,
        "video_url": snapshot.video_url,
        "views": snapshot.views,
        "likes": snapshot.likes,
        "comments": snapshot.comments,
        "youtube_title": snapshot.youtube_title,
        "youtube_published_at": snapshot.youtube_published_at,
        "youtube_channel_id": snapshot.youtube_channel_id,
        "youtube_channel_title": snapshot.youtube_channel_title,
        "thumbnail_url": snapshot.thumbnail_url,
        "duration_seconds": snapshot.duration_seconds,
        "definition": snapshot.definition,
        "caption_status": snapshot.caption_status,
        "privacy_status": snapshot.privacy_status,
        "public_stats_viewable": snapshot.public_stats_viewable,
        "title_matches_confirmed_metadata": snapshot.title_matches_confirmed_metadata,
        "duration_matches_render_package": snapshot.duration_matches_render_package,
        "views_availability": snapshot.views_availability,
        "likes_availability": snapshot.likes_availability,
        "comments_availability": snapshot.comments_availability,
        "freshness_state": snapshot.freshness_state,
        "sync_status": snapshot.sync_status,
        "sync_error_code": snapshot.sync_error_code,
        "learning_authority": snapshot.learning_authority,
        "last_synced_at": snapshot.last_synced_at,
        "unknown_metrics": snapshot.unknown_metrics,
        "unavailable_metrics": snapshot.unavailable_metrics,
        "technical_appendix": snapshot.technical_appendix,
        "created_at": snapshot.created_at,
    }


def _youtube_owner_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "platform_video_id": snapshot.platform_video_id,
        "analytics_start_date": snapshot.analytics_start_date,
        "analytics_end_date": snapshot.analytics_end_date,
        "learning_authority": snapshot.learning_authority,
        "views": snapshot.views,
        "likes": snapshot.likes,
        "comments": snapshot.comments,
        "impressions": snapshot.impressions,
        "impression_click_through_rate": snapshot.impression_click_through_rate,
        "average_view_duration_seconds": snapshot.average_view_duration_seconds,
        "average_view_percentage": snapshot.average_view_percentage,
        "estimated_minutes_watched": snapshot.estimated_minutes_watched,
        "subscribers_gained": snapshot.subscribers_gained,
        "subscribers_lost": snapshot.subscribers_lost,
        "metric_availability": snapshot.metric_availability,
        "freshness_state": snapshot.freshness_state,
        "sync_status": snapshot.sync_status,
        "sync_error_code": snapshot.sync_error_code,
        "last_synced_at": snapshot.last_synced_at,
        "technical_appendix": snapshot.technical_appendix,
        "created_at": snapshot.created_at,
    }


def _post_publish_health_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_video_id": run.uploaded_video_id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "video_project_id": run.video_project_id,
        "policy_snapshot_id": run.policy_snapshot_id,
        "platform": run.platform,
        "platform_video_id": run.platform_video_id,
        "observation_window": run.observation_window,
        "analytics_snapshot_id": run.analytics_snapshot_id,
        "uploaded_video_metrics_summary_id": run.uploaded_video_metrics_summary_id,
        "retention_curve_snapshot_id": run.retention_curve_snapshot_id,
        "traffic_source_snapshot_id": run.traffic_source_snapshot_id,
        "engagement_snapshot_id": run.engagement_snapshot_id,
        "run_state": run.run_state,
        "health_state": run.health_state,
        "severity": run.severity,
        "confidence_level": run.confidence_level,
        "evidence_refs": run.evidence_refs,
        "reason_codes": run.reason_codes,
        "operator_summary": run.operator_summary,
        "next_action": run.next_action,
        "do_not_do": run.do_not_do,
        "technical_appendix": run.technical_appendix,
        "strategic_lineage": getattr(run, "strategic_lineage", None),
        "created_at": run.created_at,
    }


def _failure_trace_report(report: Any) -> dict[str, Any]:
    return {
        "id": report.id,
        "post_publish_health_run_id": report.post_publish_health_run_id,
        "uploaded_video_id": report.uploaded_video_id,
        "video_project_id": report.video_project_id,
        "platform": report.platform,
        "platform_video_id": report.platform_video_id,
        "observation_window": report.observation_window,
        "primary_status": report.primary_status,
        "primary_suspected_cause": report.primary_suspected_cause,
        "secondary_suspected_causes": report.secondary_suspected_causes,
        "confidence_level": report.confidence_level,
        "severity": report.severity,
        "evidence_plain_text": report.evidence_plain_text,
        "operator_summary": report.operator_summary,
        "operator_report": report.operator_report,
        "next_action": report.next_action,
        "do_not_do": report.do_not_do,
        "technical_appendix": report.technical_appendix,
        "strategic_lineage": getattr(report, "strategic_lineage", None),
        "created_at": report.created_at,
    }


def _recovery_proposal(proposal: Any) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "failure_trace_report_id": proposal.failure_trace_report_id,
        "uploaded_video_id": proposal.uploaded_video_id,
        "video_project_id": proposal.video_project_id,
        "proposal_type": proposal.proposal_type,
        "proposal_state": proposal.proposal_state,
        "operator_summary": proposal.operator_summary,
        "recommended_actions": proposal.recommended_actions,
        "do_not_do": proposal.do_not_do,
        "evidence_refs": proposal.evidence_refs,
        "risk_level": proposal.risk_level,
        "requires_human_approval": proposal.requires_human_approval,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
    }


def _learning_generation_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "video_project_id": run.video_project_id,
        "uploaded_video_id": run.uploaded_video_id,
        "source_failure_trace_report_id": run.source_failure_trace_report_id,
        "source_recovery_proposal_id": run.source_recovery_proposal_id,
        "source_analytics_snapshot_id": run.source_analytics_snapshot_id,
        "source_uploaded_video_metrics_summary_id": run.source_uploaded_video_metrics_summary_id,
        "run_mode": run.run_mode,
        "run_state": run.run_state,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "generated_candidate_count": run.generated_candidate_count,
        "reason_codes": run.reason_codes,
        "next_action": run.next_action,
        "metadata": run.metadata_,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _learning_candidate(candidate: Any) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "generation_run_id": candidate.generation_run_id,
        "company_id": candidate.company_id,
        "channel_workspace_id": candidate.channel_workspace_id,
        "video_project_id": candidate.video_project_id,
        "uploaded_video_id": candidate.uploaded_video_id,
        "candidate_type": candidate.candidate_type,
        "candidate_state": candidate.candidate_state,
        "operator_summary": candidate.operator_summary,
        "friendly_status": candidate.friendly_status,
        "candidate_summary": candidate.candidate_summary,
        "suggested_learning": candidate.suggested_learning,
        "suggested_playbook_text": candidate.suggested_playbook_text,
        "recommended_scope": candidate.recommended_scope,
        "confidence_label": candidate.confidence_label,
        "risk_level": candidate.risk_level,
        "evidence_bundle_id": candidate.evidence_bundle_id,
        "eligibility_run_id": candidate.eligibility_run_id,
        "source_refs": candidate.source_refs,
        "diagnostic_refs": candidate.diagnostic_refs,
        "recovery_refs": candidate.recovery_refs,
        "metric_refs": candidate.metric_refs,
        "policy_flags": candidate.policy_flags,
        "rights_flags": candidate.rights_flags,
        "limitations": candidate.limitations,
        "counter_evidence": candidate.counter_evidence,
        "technical_appendix": candidate.technical_appendix,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
    }


def _learning_evidence_bundle(bundle: Any) -> dict[str, Any]:
    return {
        "id": bundle.id,
        "learning_candidate_id": bundle.learning_candidate_id,
        "company_id": bundle.company_id,
        "channel_workspace_id": bundle.channel_workspace_id,
        "evidence_summary": bundle.evidence_summary,
        "source_video_refs": bundle.source_video_refs,
        "source_project_refs": bundle.source_project_refs,
        "analytics_snapshot_refs": bundle.analytics_snapshot_refs,
        "diagnostic_refs": bundle.diagnostic_refs,
        "recovery_refs": bundle.recovery_refs,
        "metric_support": bundle.metric_support,
        "counter_evidence": bundle.counter_evidence,
        "limitations": bundle.limitations,
        "freshness_summary": bundle.freshness_summary,
        "confidence_summary": bundle.confidence_summary,
        "policy_rights_summary": bundle.policy_rights_summary,
        "created_at": bundle.created_at,
    }


def _learning_review_queue_item(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "learning_candidate_id": item.learning_candidate_id,
        "evidence_bundle_id": item.evidence_bundle_id,
        "eligibility_run_id": item.eligibility_run_id,
        "company_id": item.company_id,
        "channel_workspace_id": item.channel_workspace_id,
        "video_project_id": item.video_project_id,
        "uploaded_video_id": item.uploaded_video_id,
        "queue_state": item.queue_state,
        "priority": item.priority,
        "operator_summary": item.operator_summary,
        "friendly_status": item.friendly_status,
        "evidence_summary": item.evidence_summary,
        "recommended_scope": item.recommended_scope,
        "confidence_label": item.confidence_label,
        "risk_level": item.risk_level,
        "next_action": item.next_action,
        "approval_actions_allowed": item.approval_actions_allowed,
        "source_refs": item.source_refs,
        "audit_refs": item.audit_refs,
        "technical_appendix": item.technical_appendix,
        "due_at": item.due_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _playbook_candidate_draft(draft: Any) -> dict[str, Any]:
    return {
        "id": draft.id,
        "learning_candidate_id": draft.learning_candidate_id,
        "company_id": draft.company_id,
        "channel_workspace_id": draft.channel_workspace_id,
        "candidate_scope": draft.candidate_scope,
        "playbook_category": draft.playbook_category,
        "draft_text": draft.draft_text,
        "rationale": draft.rationale,
        "evidence_refs": draft.evidence_refs,
        "risk_notes": draft.risk_notes,
        "state": draft.state,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _learning_review_action(
    candidate_id: uuid.UUID,
    action: str,
    data: LearningReviewDecisionCreate | None,
    actor: ActorContext,
) -> LearningReviewDecisionRead:
    try:
        request = (
            data.model_copy(
                update={
                    "action": action,
                    "actor_role": actor.actor_role,
                    "decided_by_user_id": actor.actor_id,
                }
            )
            if data is not None
            else LearningReviewDecisionCreate(
                action=action,
                actor_role=actor.actor_role,
                decided_by_user_id=actor.actor_id,
            )
        )
        with session_scope() as session:
            decision = M11LearningReviewService(session).decide(
                candidate_id=candidate_id, data=request
            )
            return learning_review_decision_read(session, decision)
    except Exception as exc:
        raise _as_http_error(exc) from exc


def _as_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (NotFoundError, KeyError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (ValidationFailureError, ValueError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
    )


__all__ = [name for name in globals() if not name.startswith("__")]
