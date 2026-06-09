CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
  id varchar(64) PRIMARY KEY,
  name varchar(255) NOT NULL,
  status varchar(32) NOT NULL DEFAULT 'ACTIVE',
  default_language varchar(16) NOT NULL DEFAULT 'en',
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS channel_workspaces (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_name varchar(255) NOT NULL,
  platform varchar(32) NOT NULL,
  platform_channel_id varchar(255),
  channel_name varchar(255) NOT NULL,
  channel_url varchar(1024),
  niche varchar(255),
  language varchar(16) NOT NULL DEFAULT 'en',
  target_market jsonb NOT NULL DEFAULT '[]'::jsonb,
  status varchar(32) NOT NULL DEFAULT 'ACTIVE',
  follower_count integer NOT NULL DEFAULT 0,
  published_video_count integer NOT NULL DEFAULT 0,
  monetization_status varchar(32) NOT NULL DEFAULT 'NOT_STARTED',
  baseline_confidence double precision NOT NULL DEFAULT 0,
  maturity_stage varchar(64) NOT NULL DEFAULT 'NEW_CHANNEL',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_profiles (
  workspace_id varchar(64) PRIMARY KEY REFERENCES channel_workspaces(id),
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  brand_voice text,
  target_audience text,
  forbidden_topics jsonb NOT NULL DEFAULT '[]'::jsonb,
  preferred_formats jsonb NOT NULL DEFAULT '[]'::jsonb,
  target_market jsonb NOT NULL DEFAULT '[]'::jsonb,
  monetization_thesis_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  platform_rules_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  human_review_required boolean NOT NULL DEFAULT true,
  default_workflow_mode varchar(64) NOT NULL DEFAULT 'MONETIZATION_VALIDATION_MODE',
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_maturity_snapshots (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  maturity_stage varchar(64) NOT NULL,
  follower_count integer NOT NULL,
  published_video_count integer NOT NULL,
  baseline_confidence double precision NOT NULL,
  reason_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_baselines (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  metric_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence double precision NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_budget_policies (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  cost_per_video_target double precision NOT NULL DEFAULT 1,
  hard_max_per_video double precision NOT NULL DEFAULT 2.5,
  daily_budget_limit double precision NOT NULL DEFAULT 5,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_daily_plans (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  plan_date varchar(32) NOT NULL,
  plan_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status varchar(32) NOT NULL DEFAULT 'CREATED',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_operational_constitutions (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  version varchar(64) NOT NULL,
  content text NOT NULL,
  source_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
  token_estimate integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS video_projects (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  title varchar(512) NOT NULL,
  topic text,
  workflow_mode varchar(64),
  current_state varchar(64) NOT NULL DEFAULT 'IDEA_FOUND',
  status varchar(32) NOT NULL DEFAULT 'ACTIVE',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS video_states (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) NOT NULL REFERENCES video_projects(id),
  state varchar(64) NOT NULL,
  event_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  agent_name varchar(128) NOT NULL,
  node_name varchar(128) NOT NULL,
  input_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  output_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status varchar(32) NOT NULL DEFAULT 'SUCCESS',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS authority_reviews (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  gate varchar(64) NOT NULL,
  decision_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS human_reviews (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  reviewer varchar(255),
  action varchar(64) NOT NULL,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_tasks (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  task_type varchar(64) NOT NULL,
  status varchar(64) NOT NULL DEFAULT 'OPEN',
  title varchar(512) NOT NULL,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  due_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_actions (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  review_task_id varchar(64) NOT NULL REFERENCES review_tasks(id),
  action varchar(64) NOT NULL,
  actor varchar(255),
  notes text,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS uploaded_videos (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  platform varchar(32) NOT NULL,
  platform_video_id varchar(255),
  video_url varchar(1024),
  title varchar(512) NOT NULL,
  description text,
  hashtags jsonb NOT NULL DEFAULT '[]'::jsonb,
  thumbnail_uri varchar(1024),
  publish_time timestamptz,
  duration_seconds integer,
  visibility varchar(32) NOT NULL DEFAULT 'PUBLIC',
  monetization_status varchar(64) NOT NULL DEFAULT 'UNKNOWN',
  upload_status varchar(64) NOT NULL DEFAULT 'IMPORTED',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS video_artifacts (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) NOT NULL REFERENCES video_projects(id),
  artifact_type varchar(64) NOT NULL,
  uri varchar(1024),
  content_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS render_timelines (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) NOT NULL REFERENCES video_projects(id),
  version varchar(64) NOT NULL DEFAULT 'v1',
  timeline_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  manifest_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_library (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  asset_type varchar(64) NOT NULL,
  scope varchar(64) NOT NULL DEFAULT 'workspace_only',
  source_project_id varchar(64) REFERENCES video_projects(id),
  scene_id varchar(64),
  topic_cluster varchar(255),
  semantic_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
  visual_style varchar(255),
  character_pack_id varchar(255),
  duration double precision,
  aspect_ratio varchar(32),
  qa_score double precision NOT NULL DEFAULT 0,
  compliance_status varchar(64) NOT NULL DEFAULT 'PENDING',
  reuse_allowed boolean NOT NULL DEFAULT false,
  storage_uri varchar(1024) NOT NULL,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_items (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) REFERENCES channel_workspaces(id),
  scope varchar(64) NOT NULL,
  family varchar(128) NOT NULL,
  type varchar(128) NOT NULL,
  title varchar(512) NOT NULL,
  content text NOT NULL,
  summary text,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence double precision NOT NULL DEFAULT 0.5,
  sample_size integer NOT NULL DEFAULT 0,
  source_video_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  status varchar(64) NOT NULL DEFAULT 'ACTIVE',
  expires_at timestamptz,
  embedding vector(16),
  embedding_model varchar(128),
  embedding_version varchar(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS editorial_playbooks (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  version varchar(64) NOT NULL DEFAULT 'seed_playbook_v1',
  content_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS compliance_reports (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  status varchar(64) NOT NULL,
  report_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qa_reports (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  qa_type varchar(64) NOT NULL,
  score double precision NOT NULL DEFAULT 0,
  report_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cost_events (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  project_id varchar(64) REFERENCES video_projects(id),
  agent_name varchar(128) NOT NULL,
  node_name varchar(128) NOT NULL,
  provider varchar(128) NOT NULL DEFAULT 'mock',
  model varchar(128),
  input_tokens integer NOT NULL DEFAULT 0,
  output_tokens integer NOT NULL DEFAULT 0,
  media_units double precision NOT NULL DEFAULT 0,
  estimated_cost double precision NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  uploaded_video_id varchar(64) REFERENCES uploaded_videos(id),
  project_id varchar(64) REFERENCES video_projects(id),
  snapshot_time timestamptz NOT NULL DEFAULT now(),
  hours_since_publish integer,
  views integer NOT NULL DEFAULT 0,
  impressions integer NOT NULL DEFAULT 0,
  ctr double precision,
  avg_view_duration double precision,
  avg_percentage_viewed double precision,
  subscribers_gained integer NOT NULL DEFAULT 0,
  likes integer NOT NULL DEFAULT 0,
  comments integer NOT NULL DEFAULT 0,
  shares integer NOT NULL DEFAULT 0,
  estimated_revenue double precision NOT NULL DEFAULT 0,
  rpm double precision,
  traffic_source_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  geography_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retention_segments (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  uploaded_video_id varchar(64) REFERENCES uploaded_videos(id),
  project_id varchar(64) REFERENCES video_projects(id),
  segment_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS diagnosis_events (
  id varchar(64) PRIMARY KEY,
  company_id varchar(64) NOT NULL REFERENCES companies(id),
  workspace_id varchar(64) NOT NULL REFERENCES channel_workspaces(id),
  uploaded_video_id varchar(64) REFERENCES uploaded_videos(id),
  project_id varchar(64) REFERENCES video_projects(id),
  diagnosis_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_channel_workspaces_company ON channel_workspaces(company_id);
CREATE INDEX IF NOT EXISTS idx_memory_scope_workspace ON memory_items(company_id, scope, workspace_id, family);
CREATE INDEX IF NOT EXISTS idx_uploaded_videos_workspace ON uploaded_videos(company_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_analytics_workspace_video ON analytics_snapshots(company_id, workspace_id, uploaded_video_id);
CREATE INDEX IF NOT EXISTS idx_cost_events_project ON cost_events(project_id);
