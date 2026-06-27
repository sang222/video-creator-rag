"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getChannelWorkspace, queryKeys } from "@/lib/api";

export function ChannelWorkspaceView({ channelId }: { channelId: string }) {
  const query = useQuery({ queryKey: queryKeys.channelWorkspace(channelId), queryFn: () => getChannelWorkspace(channelId) });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading channel workspace" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading channel workspace" /></div>;

  const workspace = query.data;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">{workspace.channel.name}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{String(workspace.health_summary.next_action ?? workspace.lifecycle.next_action)}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {["channel_status", "health", "analytics_freshness", "storage_state"].map((key) => (
          <Panel key={key}>
            <div className="text-sm text-muted-foreground">{key.replaceAll("_", " ")}</div>
            <div className="mt-3"><StatusBadge value={String(workspace.health_summary[key] ?? "UNKNOWN")} /></div>
          </Panel>
        ))}
      </div>
      <Tabs.Root defaultValue="overview">
        <Tabs.List className="flex flex-wrap gap-2">
          {["overview", "projects", "approvals", "publishing", "analytics", "learning", "profile-policy", "media", "provider-health"].map((tab) => (
            <Tabs.Trigger key={tab} value={tab} className="rounded-md border border-border px-3 py-2 text-sm data-[state=active]:border-primary data-[state=active]:text-primary">
              {tab}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="overview" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Lifecycle</h2>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <StatusBadge value={workspace.lifecycle.lifecycle_state} />
              <StatusBadge value={workspace.lifecycle.health_status} />
              <StatusBadge value={workspace.lifecycle.daily_generation_allowed ? "DAILY_ALLOWED" : "DAILY_BLOCKED"} />
            </div>
            <p className="mt-4 text-sm text-muted-foreground">{workspace.lifecycle.next_action}</p>
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="projects" className="mt-5">
          {workspace.projects.length ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {workspace.projects.map((project) => (
                <Panel key={String(project.id)}>
                  <h2 className="text-base font-semibold">{String(project.title)}</h2>
                  <p className="mt-2 text-sm text-muted-foreground">{String(project.next_action)}</p>
                  <div className="mt-3"><StatusBadge value={String(project.current_stage)} /></div>
                </Panel>
              ))}
            </div>
          ) : (
            <EmptyState title="No projects" body="Daily generation only creates new work while lifecycle is ACTIVE." />
          )}
        </Tabs.Content>
        <Tabs.Content value="approvals" className="mt-5">
          {workspace.approvals.length ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {workspace.approvals.map((item) => <ApprovalCard key={item.queue_item_id ?? item.entity_id ?? item.operator_summary} item={item} />)}
            </div>
          ) : (
            <EmptyState title="No approval blockers" body="No learning, publishing, or ops queue items are currently attached to this channel." />
          )}
        </Tabs.Content>
        <Tabs.Content value="media" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Media Storage</h2>
            <p className="mt-2 text-sm text-muted-foreground">Google Drive is blob storage only. Dashboard uses web_view_link CTA and never local paths.</p>
            <div className="mt-4"><StatusBadge value={String(workspace.media_storage.storage_state ?? workspace.health_summary.storage_state)} /></div>
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="publishing" className="mt-5">
          <EmptyState title="Publishing handoff" body="Open the publishing queue for Drive media CTA, metadata copy, and manual paste-back confirmation." />
        </Tabs.Content>
        <Tabs.Content value="analytics" className="mt-5">
          <EmptyState title="Analytics" body="Uploaded video detail shows public YouTube stats and owner analytics availability without treating unknown metrics as zero." />
        </Tabs.Content>
        <Tabs.Content value="learning" className="mt-5">
          <EmptyState title="Learning" body="Learning candidates must be approved by a human reviewer before becoming playbook entries." />
        </Tabs.Content>
        <Tabs.Content value="profile-policy" className="mt-5">
          <EmptyState title="Profile & Policy" body="Human edits create a new profile version and compiled snapshot; existing projects keep their old snapshot." />
        </Tabs.Content>
        <Tabs.Content value="provider-health" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Provider Health</h2>
            <p className="mt-2 text-sm text-muted-foreground">Provider status is read-only from guarded backend state. Dashboard load does not call providers.</p>
          </Panel>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
