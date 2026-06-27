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
};

export type ChannelWorkspace = {
  channel: ChannelSummary;
  health_summary: Record<string, unknown>;
  lifecycle: ChannelLifecycle;
  projects: Array<Record<string, unknown>>;
  daily_runs: Array<Record<string, unknown>>;
  approvals: ApprovalQueueItem[];
  uploaded_videos: Array<Record<string, unknown>>;
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
