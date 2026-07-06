export type Severity = "LOW" | "NORMAL" | "HIGH" | "CRITICAL" | "HARD_RULE" | string;

export type DashboardWarning = {
  key: string;
  label: string;
  severity: Severity;
  text: string;
};

export type DashboardActionCard = {
  key: string;
  title: string;
  count: number;
  severity: Severity;
  next_action: string;
  route?: string | null;
};

export type DashboardMetricCard = {
  key: string;
  label: string;
  value: number | string | null;
  state: string;
  next_action?: string | null;
};

export type CommandCenter = {
  generated_at: string;
  company_id?: string | null;
  cards: DashboardActionCard[];
  metrics: DashboardMetricCard[];
  required_actions: Array<Record<string, unknown>>;
  safety_warnings: DashboardWarning[];
  technical_appendix: Record<string, unknown>;
};

export type Company = {
  id: string;
  name: string;
  slug: string;
  description: string;
  status: string;
  default_currency: string;
};

export type FieldMeta = {
  value: unknown;
  source_type: string;
  confidence_label: "LOW" | "MEDIUM" | "HIGH" | string;
  evidence_refs: string[];
  review_required: boolean;
  editable_by_human: boolean;
  locked_reason?: string | null;
};

export type EvidenceRef = {
  ref_id: string;
  source_type: string;
  url?: string | null;
  title?: string | null;
  snippet?: string | null;
  captured_at: string;
  reliability: "LOW" | "MEDIUM" | "HIGH" | string;
};

export type ChannelContractDraft = {
  id: string;
  init_draft_id: string;
  company_id: string;
  channel_name: string;
  source_urls: Array<Record<string, unknown>>;
  admin_minimal_input: Record<string, unknown>;
  suggested_channel_contract: Record<string, unknown>;
  field_source_map_json: Record<string, FieldMeta>;
  confidence_summary: Record<string, string>;
  missing_fields: string[];
  human_questions: Array<Record<string, unknown>>;
  risks: Array<Record<string, unknown>>;
  evidence_refs: EvidenceRef[];
  workflow_status: string;
  contract_status?: string | null;
  review_decision_log_json: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
};

export type ChannelInitDraft = {
  id: string;
  company_id: string;
  channel_name: string;
  public_presence_mode: "EXISTING_PUBLIC_CHANNEL" | "NEW_CHANNEL_NO_PUBLIC_FOOTPRINT";
  youtube_url_or_handle?: string | null;
  website_url?: string | null;
  social_profile_links: string[];
  operator_note_purpose: string;
  intended_content_language?: string | null;
  intended_primary_market?: string | null;
  owner_operator_language: string;
  initial_topic_pillar_hints: string[];
  source_usage_attestation: boolean;
  workflow_status: string;
  contract_status?: string | null;
  channel_id?: string | null;
  channel_profile_version_id?: string | null;
  compiled_policy_snapshot_id?: string | null;
  latest_contract_draft?: ChannelContractDraft | null;
  created_at: string;
  updated_at: string;
};

export type ChannelInitCompileResult = {
  init_draft_id: string;
  channel_id: string;
  channel_profile_version_id: string;
  compiled_policy_snapshot_id: string;
  workflow_status: string;
  contract_status: string;
  missing_fields: string[];
  contradiction_reasons: string[];
  activation_eligibility: boolean;
  channel_contract_json: Record<string, unknown>;
  field_source_map_json: Record<string, FieldMeta>;
};

export type ApprovalQueueItem = {
  queue_item_id?: string | null;
  queue_type: string;
  entity_type: string;
  entity_id?: string | null;
  channel?: { id: string; key: string; name: string; status: string } | null;
  project?: { id: string; title: string; status: string } | null;
  operator_summary: string;
  friendly_status: string;
  priority: string;
  risk_level: string;
  confidence_label: string;
  freshness_label: string;
  evidence_summary: string;
  next_action: string;
  due_at?: string | null;
  allowed_actions: string[];
  action_ref?: Record<string, unknown> | null;
  source_refs: Array<Record<string, unknown>>;
  audit_refs: Array<Record<string, unknown>>;
  technical_appendix: Record<string, unknown>;
};

export type DashboardQueues = {
  generated_at: string;
  summaries: Array<{
    queue_type: string;
    label: string;
    count: number;
    priority: string;
    next_action: string;
    allowed_actions: string[];
  }>;
  items: ApprovalQueueItem[];
};

export type ChannelLifecycle = {
  channel_id: string;
  lifecycle_state: string;
  health_status: string;
  daily_generation_allowed: boolean;
  next_action: string;
  main_blocker?: string | null;
  allowed_actions: string[];
  last_decision?: Record<string, unknown> | null;
};

export type ChannelSummary = {
  id: string;
  company_id: string;
  key: string;
  name: string;
  status: string;
  lifecycle_state: string;
  health_status: string;
  next_action: string;
  daily_generation_allowed: boolean;
  contract_status?: string;
  contract_review_label?: string;
  contract_review?: {
    contract_status: string;
    label: string;
    latest_snapshot_id?: string | null;
    active_snapshot_id?: string | null;
    snapshot_version?: number;
    missing_fields?: string[];
    contradiction_reasons?: string[];
    market_locale?: Record<string, unknown>;
    next_action?: string;
  };
  upload_counts?: PublishLedgerCounts;
};

export type ChannelWorkspace = {
  channel: ChannelSummary;
  health_summary: Record<string, unknown>;
  lifecycle: ChannelLifecycle;
  projects: Array<Record<string, unknown>>;
  daily_runs: Array<Record<string, unknown>>;
  approvals: ApprovalQueueItem[];
  uploaded_videos: Array<Record<string, unknown>>;
  publish_ledger?: PublishLedgerCounts & { operator_summary_vi?: string };
  media_storage: Record<string, unknown>;
  provider_health: Record<string, unknown>;
  technical_appendix: Record<string, unknown>;
};

export type UploadedVideoListItem = {
  id: string;
  title: string;
  channel_id: string;
  platform: string;
  platform_video_id: string;
  video_url: string;
  external_video_id?: string | null;
  external_url?: string | null;
  actual_visibility?: string | null;
  verification_status: string;
  analytics_sync_status: string;
  published_at: string;
  metrics: Record<string, number | string | null>;
  freshness: string;
  owner_analytics_status: string;
  latest_diagnostic?: string | null;
  next_action?: string | null;
};

export type UploadedVideoDashboard = {
  uploaded_video: Record<string, unknown>;
  public_stats: Record<string, unknown>;
  owner_analytics: Record<string, unknown>;
  publish_check: Record<string, unknown>;
  diagnostics: Array<Record<string, unknown>>;
  recovery_proposals: Array<Record<string, unknown>>;
  learning_candidates: Array<Record<string, unknown>>;
  media: GoogleDriveMedia[];
  safety_warnings: DashboardWarning[];
  technical_appendix: Record<string, unknown>;
};

export type GoogleDriveMedia = {
  id: string;
  storage: "Google Drive";
  media_type: string;
  status: string;
  cta_label: string;
  web_view_link: string;
  file_size?: number | null;
  uploaded_at?: string | null;
  cleanup_status: string;
  verification_status: string;
  friendly_error?: string | null;
  technical_appendix: Record<string, unknown>;
};

export type ProviderOps = {
  generated_at: string;
  providers: Array<Record<string, unknown>>;
  credentials: Array<Record<string, unknown>>;
  quotas: Array<Record<string, unknown>>;
  costs: Array<Record<string, unknown>>;
  incidents: Array<Record<string, unknown>>;
  manual_actions: Array<Record<string, unknown>>;
  integrations: Record<string, Record<string, unknown>>;
  safety_warnings: DashboardWarning[];
};

export type ProviderReadinessCheck = {
  id?: string | null;
  provider_key: string;
  provider_type: string;
  check_type: string;
  check_state: string;
  operator_summary: string;
  next_action?: string | null;
  reason_codes: string[];
  technical_appendix: Record<string, unknown>;
  created_at?: string | null;
};

export type ProviderSummary = {
  provider_key: string;
  provider_name: string;
  provider_type: string;
  readiness_state: string;
  status_label: string;
  operator_summary: string;
  next_action: string;
  smoke_state?: string | null;
  learning_authority?: string | null;
  safe_config: Record<string, unknown>;
  missing_env_keys: string[];
  reason_codes: string[];
  technical_appendix: Record<string, unknown>;
};

export type ProviderBudgetCard = {
  key: string;
  provider_name: string;
  role: string;
  configured_plan?: string | null;
  configured_monthly_cap?: string | null;
  budget_basis: string;
  readiness_state: string;
  missing_env_keys: string[];
  note: string;
  technical_appendix: Record<string, unknown>;
};

export type IntegrationReadiness = {
  generated_at: string;
  snapshot_state: string;
  latest_snapshot_id?: string | null;
  provider_summaries: ProviderSummary[];
  checks: ProviderReadinessCheck[];
  blocking_items: Array<Record<string, unknown>>;
  warning_items: Array<Record<string, unknown>>;
  next_actions: Array<Record<string, unknown>>;
  budget_cards: ProviderBudgetCard[];
  security_summary: Record<string, unknown>;
  technical_appendix: Record<string, unknown>;
};

export type RealSmokeRun = {
  id: string;
  provider_key: string;
  smoke_type: string;
  run_state: string;
  env_flags: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  result_summary?: string | null;
  technical_appendix: Record<string, unknown>;
  created_at: string;
};

export type LearningDecisionPayload = {
  action: "APPROVE" | "REJECT" | "REQUEST_MORE_EVIDENCE" | "SUPPRESS" | "EXPIRE";
  actor_role: string;
  rationale?: string;
};

export type CurrentOperatorUser = {
  id: string;
  email: string;
  display_name?: string | null;
  role: string;
  status: string;
};

export type AuthSession = {
  authenticated: boolean;
  auth_enabled: boolean;
  auth_mode: string;
  local_dev_note: string;
  user?: CurrentOperatorUser | null;
};

export type PublishLedgerCounts = {
  need_upload_count: number;
  waiting_backfill_count: number;
  uploaded_count: number;
  waiting_verification_count: number;
  verified_count: number;
  analytics_not_configured_count?: number;
  blocked_count?: number;
};

export type HumanUploadTask = {
  id: string;
  channel_id: string;
  video_project_id?: string | null;
  first_scripted_video_package_id?: string | null;
  publish_package_id?: string | null;
  destination: "YOUTUBE";
  status: string;
  upload_card_ref?: string | null;
  title_snapshot: string;
  description_snapshot?: string | null;
  thumbnail_ref?: unknown;
  subtitle_refs: Array<Record<string, unknown>>;
  required_assets: Array<Record<string, unknown>>;
  checklist: Array<Record<string, unknown>>;
  actual_uploaded_video_id?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  blocked_reason?: string | null;
  operator_note?: string | null;
  next_action: string;
};

export type HumanUploadTaskList = PublishLedgerCounts & {
  channel_id: string;
  tasks: HumanUploadTask[];
  unverified_count: number;
};

export type UploadedVideoLedger = {
  id: string;
  channel_id: string;
  video_project_id?: string | null;
  first_scripted_video_package_id?: string | null;
  publish_package_id?: string | null;
  human_upload_task_id?: string | null;
  destination: "YOUTUBE";
  external_video_id: string;
  external_url: string;
  actual_title?: string | null;
  actual_visibility: string;
  actual_publish_time?: string | null;
  actual_upload_time?: string | null;
  playlist_id?: string | null;
  thumbnail_uploaded?: boolean | null;
  subtitles_uploaded?: boolean | null;
  description_modified_from_package?: boolean | null;
  package_metadata_diff?: Record<string, unknown> | null;
  verification_status: string;
  analytics_sync_status: string;
  last_verified_at?: string | null;
  last_analytics_sync_at?: string | null;
  operator_note?: string | null;
  next_action: string;
  created_at: string;
  updated_at: string;
};

export type UploadedVideoLedgerList = {
  channel_id: string;
  uploaded_videos: UploadedVideoLedger[];
};

export type PublishLedger = PublishLedgerCounts & {
  channel_id: string;
  latest_tasks: HumanUploadTask[];
  latest_uploaded_videos: UploadedVideoLedger[];
  operator_summary_vi: string;
};

export type PackagingGateStatus = "PASS" | "REVIEW_REQUIRED" | "BLOCK" | "SKIPPED_NOT_APPLICABLE" | string;

export type PackagingGateResult = {
  gate_key: string;
  status: PackagingGateStatus;
  reason_codes: string[];
  checked_artifact_refs: Array<Record<string, unknown>>;
  checked_contract_paths: string[];
  summary_vi: string;
  next_action_vi?: string | null;
};

export type PackagingHandoff = {
  package_id: string;
  package_status: string;
  channel_id: string;
  video_project_id?: string | null;
  effective_context_snapshot_id?: string | null;
  effective_context_hash?: string | null;
  hook_spec: {
    id: string;
    package_id: string;
    video_project_id?: string | null;
    effective_context_snapshot_id?: string | null;
    hook_type: string;
    first_3_seconds_script?: string | null;
    first_3_seconds_visual?: string | null;
    promise_made?: string | null;
    payoff_location?: string | null;
    clickbait_risk: string;
    evidence_refs_json: Array<Record<string, unknown>>;
    contract_paths_used_json: string[];
    content_hash: string;
    created_at: string;
  };
  upload_handoff_copy: {
    title?: string | null;
    description?: string | null;
    hashtags_json?: string[] | null;
    subtitle_refs_json: Array<Record<string, unknown>>;
    disclosure_notes_json: Array<Record<string, unknown>>;
    checklist_items_json: Array<Record<string, unknown>>;
    language?: string | null;
    locale?: string | null;
    channel_contract_hash?: string | null;
    effective_context_snapshot_id?: string | null;
    packaging_gate_status: PackagingGateStatus;
    source_artifact_refs_json: Array<Record<string, unknown>>;
  };
  thumbnail_handoff: {
    concept?: string | null;
    text_overlay?: string | null;
    main_subject?: string | null;
    composition?: string | null;
    mobile_readability_notes?: string | null;
    thumbnail_ref?: unknown;
    drive_ref?: unknown;
    character_image_branch_id?: string | null;
    reference_asset_pack_id?: string | null;
    thumbnail_variant_plan_json?: unknown;
    contract_paths_used_json: string[];
    source_artifact_refs_json: Array<Record<string, unknown>>;
  };
  publish_timing_recommendation: {
    channel_timezone?: string | null;
    audience_timezone?: string | null;
    operator_local_timezone?: string | null;
    configured_publish_window_json?: unknown;
    suggested_publish_time_channel_tz?: string | null;
    suggested_publish_time_operator_local?: string | null;
    publish_timing_policy_ref?: string | null;
    manual_publish_only: boolean;
    source_contract_paths: string[];
    reason_codes_json: string[];
  };
  packaging_gate_summary: {
    overall_status: PackagingGateStatus;
    gate_results: PackagingGateResult[];
    r3d4_gate_batch_refs: string[];
    next_action_vi: string;
  };
  manual_upload: Record<string, unknown>;
  provider_readiness_summary: Record<string, unknown>;
  manual_publish_only: boolean;
  no_upload_or_publish_calls_made: boolean;
  created_at: string;
};

export type PackagingProposedPatch = {
  id: string;
  queue_item_id: string;
  package_id: string;
  proposal_source: string;
  routed_agent_key?: string | null;
  patch_type: string;
  before_snapshot_ref: string;
  proposed_patch_json: Record<string, unknown>;
  after_preview_json: Record<string, unknown>;
  affected_artifact_refs_json: Array<Record<string, unknown>>;
  risk_level: string;
  requires_human_approval: boolean;
  patch_hash: string;
  status: string;
  created_at: string;
};

export type PackagingReviewQueueItem = {
  id: string;
  package_id: string;
  video_project_id?: string | null;
  effective_context_snapshot_id?: string | null;
  gate_key: string;
  issue_code: string;
  severity: "BLOCK" | "REVIEW_REQUIRED" | "WARNING" | string;
  target_artifact_type: string;
  target_artifact_ref?: string | null;
  source_gate_run_id?: string | null;
  source_gate_batch_id?: string | null;
  status: string;
  next_action_code: string;
  human_readable_title: string;
  human_readable_why: string;
  human_readable_fix: string;
  section: string;
  proposed_patch?: PackagingProposedPatch | null;
  created_at: string;
  updated_at: string;
};

export type PackagingReviewQueue = {
  package_id: string;
  review_verdict:
    | "READY_FOR_MANUAL_UPLOAD"
    | "WAITING_FINAL_MEDIA_ASSET"
    | "REVIEW_REQUIRED"
    | "BLOCKED"
    | "WAITING_PROVIDER_CONFIG"
    | string;
  plain_language_status: string;
  must_fix_count: number;
  next_safe_action: string;
  upload_task_creation_allowed: boolean;
  approved_patch_count: number;
  ready_for_review_patch_count: number;
  rejected_patch_count: number;
  request_changes_patch_count: number;
  applied_patch_count: number;
  can_apply_approved_changes: boolean;
  apply_approved_changes_label: string;
  apply_approved_changes_disabled_reason?: string | null;
  last_apply_recheck_result?: Record<string, unknown> | null;
  items: PackagingReviewQueueItem[];
  technical_appendix: Record<string, unknown>;
};

export type PackagingPatchApprovalDecision = {
  id: string;
  proposed_patch_id: string;
  decision: "APPROVE" | "REJECT" | "REQUEST_CHANGES" | string;
  decided_by: string;
  rationale?: string | null;
  created_at: string;
};

export type PackagingPatchApplyRun = {
  id: string;
  proposed_patch_id: string;
  package_id: string;
  apply_status: string;
  created_artifact_ref?: string | null;
  created_handoff_override_ref?: string | null;
  created_version_hash?: string | null;
  reason_codes_json: string[];
  created_at: string;
};

export type PackagingGateRerunRecord = {
  id: string;
  package_id: string;
  proposed_patch_id?: string | null;
  gate_keys_json: string[];
  rerun_status: string;
  gate_batch_run_id?: string | null;
  reason_codes_json: string[];
  created_at: string;
};

export type PackagingApplyApprovedChangesResult = {
  status: string;
  package_id: string;
  applied_patch_ids: string[];
  skipped_patch_ids: string[];
  gate_rerun_record_ids: string[];
  package_status: string;
  final_package_status: string;
  review_verdict: string;
  must_fix_count: number;
  upload_task_creation_allowed: boolean;
  remaining_blockers: Array<Record<string, unknown>>;
  next_safe_action: string;
  no_provider_media_upload_execution: boolean;
  no_execution_proof: Record<string, unknown>;
};

export type VideoPackageReview = {
  package_id: string;
  package_status: string;
  channel_binding: Record<string, unknown>;
  effective_context: Record<string, unknown>;
  packaging_handoff?: PackagingHandoff | null;
  packaging_review_queue?: PackagingReviewQueue | null;
  human_review_checklist: Record<string, unknown>;
  agent_outputs: Record<string, unknown>;
  prompt_snapshots: Record<string, unknown>;
  provider_readiness_snapshot_ref?: string | null;
  limitations: string[];
  next_action?: string | null;
};

export type BackfillUploadedVideoInput = {
  youtube_url_or_video_id: string;
  actual_title?: string | null;
  actual_visibility?: string | null;
  actual_publish_time?: string | null;
  actual_upload_time?: string | null;
  playlist_id?: string | null;
  thumbnail_uploaded?: boolean | null;
  subtitles_uploaded?: boolean | null;
  description_modified_from_package?: boolean | null;
  operator_note?: string | null;
};

export type BackfillUploadedVideoResult = {
  task: HumanUploadTask;
  uploaded_video: UploadedVideoLedger;
  parsed_video_id: string;
  next_action: string;
};

export type OperatorNextAction = {
  next_action_code: string;
  next_action_label_vi: string;
  allowed_actor_role: string;
  blocking_reason_codes: string[];
  target_url?: string | null;
  action_ref?: Record<string, unknown> | null;
  is_manual_only: boolean;
};

export type OpsCard = {
  key: string;
  entity_type: string;
  entity_id?: string | null;
  title: string;
  status: string;
  severity: string;
  blocker_reason_codes: string[];
  next_action: OperatorNextAction;
  owner_role?: string | null;
  link_target?: string | null;
  updated_at?: string | null;
  technical_appendix: Record<string, unknown>;
};

export type RuntimeOpsCommandCenter = {
  generated_at: string;
  active_channels: OpsCard[];
  packages_waiting_review: OpsCard[];
  upload_tasks_waiting_human: OpsCard[];
  uploaded_videos_waiting_verification_or_analytics: OpsCard[];
  diagnostics_needing_review: OpsCard[];
  recovery_proposals_needing_action: OpsCard[];
  learning_candidates_needing_review: OpsCard[];
  memory_approvals_needing_review: OpsCard[];
  provider_cost_blockers: OpsCard[];
  gate_failures: OpsCard[];
  next_actions: OperatorNextAction[];
  forbidden_actions: string[];
  technical_appendix: Record<string, unknown>;
};

export type ChannelRuntimeTrace = {
  channel_id: string;
  video_project_id?: string | null;
  package_id?: string | null;
  channel_profile_version_id?: string | null;
  compiled_policy_snapshot_id?: string | null;
  channel_contract_hash?: string | null;
  effective_context_snapshot_id: string;
  context_hash: string;
  category_id?: string | null;
  character_binding_id?: string | null;
  market_locale_language: Record<string, unknown>;
  voice_profile: Record<string, unknown>;
  thumbnail_style: Record<string, unknown>;
  publish_timing_policy: Record<string, unknown>;
  provider_boundary: Record<string, unknown>;
  budget_cost_policy: Record<string, unknown>;
  source_refs: Array<Record<string, unknown>>;
  snapshot_refs: Record<string, unknown>;
  latest_mutable_settings_used: boolean;
  technical_appendix: Record<string, unknown>;
};

export type PackageOpsSummary = {
  package_id: string;
  package_status: string;
  video_project_id?: string | null;
  channel_id: string;
  effective_context_snapshot_id?: string | null;
  effective_context_hash?: string | null;
  agent_context_pack_refs: Array<Record<string, unknown>>;
  prompt_budget_summary: Array<Record<string, unknown>>;
  hook_first_3_seconds: Record<string, unknown>;
  title_description_subtitles_disclosure: Record<string, unknown>;
  thumbnail_handoff: Record<string, unknown>;
  publish_timing_recommendation: Record<string, unknown>;
  r3d4_deterministic_gate_results: Array<Record<string, unknown>>;
  gatekeeper_soft_review_result?: Record<string, unknown> | null;
  packaging_gate_results: Array<Record<string, unknown>>;
  provider_boundary_summary: Record<string, unknown>;
  manual_publish_handoff: Record<string, unknown>;
  next_action: OperatorNextAction;
  no_provider_media_upload_execution: boolean;
  technical_appendix: Record<string, unknown>;
};

export type UploadedVideoOpsSummary = {
  uploaded_video_id: string;
  platform: string;
  platform_video_id: string;
  platform_url: string;
  backfill_history: Array<Record<string, unknown>>;
  verification_status: string;
  actual_upload_time?: string | null;
  actual_publish_time?: string | null;
  channel_timezone?: string | null;
  operator_timezone?: string | null;
  analytics_sync_status: string;
  analytics_maturity: string;
  analytics_confidence: string;
  enforcement_restriction_flags: string[];
  linked_package_project: Record<string, unknown>;
  diagnostics: Array<Record<string, unknown>>;
  recovery_proposal_refs: Array<Record<string, unknown>>;
  learning_candidate_refs: Array<Record<string, unknown>>;
  next_action: OperatorNextAction;
  no_youtube_studio_scraping: boolean;
  technical_appendix: Record<string, unknown>;
};

export type OpsQueue<T = Record<string, unknown>> = {
  generated_at: string;
  items: T[];
  prompt_eligibility_rule_vi?: string;
};

export type RetrievalManifestOps = {
  manifest_id: string;
  effective_context_snapshot_id: string;
  agent_key: string;
  use_case: string;
  sql_filter: Record<string, unknown>;
  candidate_count_before_vector: number;
  candidate_count_after_policy: number;
  selected_facets: Array<Record<string, unknown>>;
  blocked_refs: Array<Record<string, unknown>>;
  rejected_refs: Array<Record<string, unknown>>;
  retrieval_hash: string;
  digest_hash?: string | null;
  raw_memory_hidden: boolean;
  advanced_refs_collapsed_by_default: boolean;
  technical_appendix: Record<string, unknown>;
};

export type MemoryInfluenceOps = {
  manifest_id: string;
  video_project_id: string;
  package_id?: string | null;
  agent_key: string;
  retrieval_manifest_id: string;
  memory_facets_used: Array<Record<string, unknown>>;
  digest_hash: string;
  prompt_context_hash: string;
  applied_as: Record<string, unknown>;
  ignored_memory_refs: Array<Record<string, unknown>>;
  blocked_memory_refs: Array<Record<string, unknown>>;
  scope_status: string;
  next_action: OperatorNextAction;
  technical_appendix: Record<string, unknown>;
};

export type QualityDeltaOps = {
  quality_delta_id: string;
  memory_facets_used: Array<Record<string, unknown>>;
  expected_metric_family: string;
  expected_direction: string;
  baseline_snapshot?: Record<string, unknown> | null;
  observed_snapshot?: Record<string, unknown> | null;
  result: string;
  confidence_delta: number;
  reason_codes: string[];
  next_action: OperatorNextAction;
  technical_appendix: Record<string, unknown>;
};

export type ProviderCostOps = {
  package_id: string;
  provider_readiness: Record<string, unknown>;
  missing_config: string[];
  render_revisions: Array<Record<string, unknown>>;
  cost_estimates: Array<Record<string, unknown>>;
  human_paid_render_approvals: Array<Record<string, unknown>>;
  paid_attempt_limits: Array<Record<string, unknown>>;
  provider_boundary_decisions: Array<Record<string, unknown>>;
  paid_provider_call_ledger: Array<Record<string, unknown>>;
  proxy_preview_flags: Array<Record<string, unknown>>;
  will_execute: boolean;
  next_action: OperatorNextAction;
  technical_appendix: Record<string, unknown>;
};
