-- Phase 1 integrity hardening.
--
-- Composite foreign keys are intentionally not added here because Phase 1 uses
-- SQLite for local tests/dev and Postgres for production-ready DDL. Ownership is
-- enforced in the service layer. These unique indexes make those checks fast and
-- provide stable targets for future composite foreign keys in Postgres-only
-- migrations.

CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_workspaces_id_company
  ON channel_workspaces (id, company_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_video_projects_id_company_workspace
  ON video_projects (id, company_id, workspace_id);

CREATE INDEX IF NOT EXISTS ix_channel_workspaces_company_workspace
  ON channel_workspaces (company_id, id);

CREATE INDEX IF NOT EXISTS ix_video_projects_company_workspace
  ON video_projects (company_id, workspace_id);

CREATE INDEX IF NOT EXISTS ix_review_tasks_company_workspace_project
  ON review_tasks (company_id, workspace_id, project_id);

CREATE INDEX IF NOT EXISTS ix_review_tasks_scope_status
  ON review_tasks (company_id, workspace_id, status);

CREATE INDEX IF NOT EXISTS ix_uploaded_videos_company_workspace_project
  ON uploaded_videos (company_id, workspace_id, project_id);

CREATE INDEX IF NOT EXISTS ix_analytics_snapshots_company_workspace_project
  ON analytics_snapshots (company_id, workspace_id, project_id);

CREATE INDEX IF NOT EXISTS ix_analytics_snapshots_company_workspace_video
  ON analytics_snapshots (company_id, workspace_id, uploaded_video_id);

CREATE INDEX IF NOT EXISTS ix_cost_events_company_workspace_project
  ON cost_events (company_id, workspace_id, project_id);
