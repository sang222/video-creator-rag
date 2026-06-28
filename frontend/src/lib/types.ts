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
